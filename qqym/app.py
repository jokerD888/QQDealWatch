import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import websockets

from .config import Config, load_config, load_secrets, required_env
from .core import alert_key, candidate_rules, evaluate_result, parse_onebot
from .ocr import read_image_text
from .services import PushplusError, analyze, pushplus
from .storage import Storage


LOG = logging.getLogger("qqym")
CHINA = timezone(timedelta(hours=8))


async def probe_onebot(config: Config) -> str:
    headers = {}
    if os.getenv("ONEBOT_ACCESS_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["ONEBOT_ACCESS_TOKEN"]
    async with websockets.connect(config.ws_url, additional_headers=headers,
                                  open_timeout=5, close_timeout=2) as ws:
        await ws.send(json.dumps({"action": "get_group_list", "params": {}, "echo": "qqym-check"}))
        while True:
            packet = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
            if packet.get("echo") != "qqym-check":
                continue
            if packet.get("status") != "ok" or packet.get("retcode", 0) != 0:
                return f"OneBot 群列表查询失败：{packet.get('wording', packet.get('retcode'))}"
            groups = packet.get("data", [])
            visible = {int(item["group_id"]) for item in groups if isinstance(item, dict)
                       and str(item.get("group_id", "")).isdigit()}
            missing = config.group_ids - visible
            if missing:
                return "OneBot 已连接，但目标群不可见：" + ", ".join(map(str, sorted(missing)))
            return "OneBot 已连接，目标群在账号群列表中"


class App:
    def __init__(self, config: Config):
        self.config = config
        self.storage = Storage(config.database)
        self.ai_slots = asyncio.Semaphore(3)
        self.delivery_lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.delivery_blocked = False

    async def process(self, event: dict, *, retry: bool = False, attempts: int = 0) -> None:
        message = parse_onebot(event, self.config.group_ids)
        if message is None:
            return
        if not retry and not self.storage.first_seen(message.group_id, message.message_id):
            return
        current_text = message.text
        transient_ocr_error = None
        if message.image_urls and not os.getenv("AI_VISION_MODEL"):
            for image_url in message.image_urls:
                try:
                    ocr_text = await asyncio.to_thread(read_image_text, image_url)
                    if ocr_text:
                        current_text += "\n[图片文字]\n" + ocr_text
                except Exception as exc:
                    LOG.warning("图片 OCR 失败，群 %s 消息 %s：%s",
                                message.group_id, message.message_id, exc)
                    if not isinstance(exc, ValueError):
                        transient_ocr_error = exc
        if not current_text.strip() and transient_ocr_error is not None:
            if retry:
                self.storage.retry_failed(message.group_id, message.message_id,
                                          str(transient_ocr_error), attempts)
            else:
                self.storage.failed_event(message.group_id, message.message_id,
                                          json.dumps(event, ensure_ascii=False),
                                          str(transient_ocr_error))
            return
        self.storage.remember_context(message.group_id, message.message_id,
                                      message.user_id, current_text)
        context = self.storage.recent_context(message.group_id, message.message_id,
                                              message.user_id)
        analysis_message = replace(message, text="\n".join(context + [current_text]))
        rules = candidate_rules(analysis_message, self.config.rules,
                                include_images=bool(os.getenv("AI_VISION_MODEL")))
        if not rules:
            return
        try:
            async with self.ai_slots:
                result = await asyncio.to_thread(analyze, analysis_message, rules)
            matches = evaluate_result(result, rules, self.config)
            for match in matches:
                price = f"¥{match.price}" if match.price is not None else "价格待确认"
                title = f"{match.rule.name} {price}"
                content = (f"群号：{message.group_id}\n商品：{match.rule.name}\n价格：{price}\n"
                           f"条件：{match.conditions or '未注明'}\n依据：{match.evidence}\n"
                           f"链接：{match.url or (message.source_urls[0] if message.source_urls else '无')}\n"
                           f"消息 ID：{message.message_id}")
                queued = self.storage.queue_alert(alert_key(message, match), message.group_id,
                                                  title, content, match.voice,
                                                  self.config.dedupe_hours * 3600)
                if queued:
                    LOG.info("命中：%s，群 %s，消息 %s", title, message.group_id, message.message_id)
            if retry:
                self.storage.clear_failed(message.group_id, message.message_id)
        except Exception as exc:
            LOG.exception("分析失败，群 %s 消息 %s", message.group_id, message.message_id)
            if retry:
                self.storage.retry_failed(message.group_id, message.message_id, str(exc), attempts)
            else:
                self.storage.failed_event(message.group_id, message.message_id,
                                          json.dumps(event, ensure_ascii=False), str(exc))

    async def deliver(self) -> None:
        async with self.delivery_lock:
            if self.delivery_blocked:
                return
            for alert_id, title, content in self.storage.pending_apps():
                try:
                    if self.config.dry_run:
                        LOG.info("[演练推送] %s\n%s", title, content)
                        receipt_code = None
                    else:
                        receipt_code = await asyncio.to_thread(pushplus, title, content,
                                                               self.config.notify_channel)
                    self.storage.app_result(alert_id, receipt_code=receipt_code)
                    if receipt_code:
                        LOG.info("pushplus 已受理提醒 %s，流水号 %s", alert_id, receipt_code)
                except Exception as exc:
                    LOG.warning("App 推送失败，提醒 %s：%s", alert_id, exc)
                    self.storage.app_result(alert_id, str(exc))
                    if isinstance(exc, PushplusError) and exc.code in (900, 903, 905):
                        LOG.error("pushplus 账户状态阻止发送，本进程暂停后续推送；修复后请重启监听器")
                        self.delivery_blocked = True
                        return
            for alert_id, title, content in self.storage.pending_voices():
                now = datetime.now(CHINA)
                start, end = self.config.voice_allowed_hours
                last = self.storage.latest_voice()
                midnight = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                limited = (self.storage.recent_voice_count(midnight) >= self.config.voice_daily_limit or
                           (last is not None and time.time() - last < self.config.voice_cooldown_minutes * 60))
                if not (start <= now.hour < end) or limited or self.config.dry_run:
                    LOG.info("跳过电话：%s（演练、时段或频率限制）", title)
                    self.storage.skip_voice(alert_id)
                    continue
                self.storage.mark_voice_attempted(alert_id)
                try:
                    await asyncio.to_thread(pushplus, title[:60], content, "voice")
                    LOG.info("已请求电话提醒：%s", title)
                except Exception as exc:
                    LOG.error("电话请求失败，提醒 %s；为防止重复计费不自动重试：%s", alert_id, exc)

    async def maintenance(self) -> None:
        while True:
            await asyncio.sleep(20)
            await self.deliver()
            for group_id, message_id, raw, attempts in self.storage.retry_events():
                try:
                    await self.process(json.loads(raw), retry=True, attempts=attempts)
                except Exception as exc:
                    LOG.exception("重试事件失败：%s", exc)
            self.storage.prune()

    async def listen(self) -> None:
        delay = 2
        while True:
            try:
                headers = {}
                if os.getenv("ONEBOT_ACCESS_TOKEN"):
                    headers["Authorization"] = "Bearer " + os.environ["ONEBOT_ACCESS_TOKEN"]
                async with websockets.connect(self.config.ws_url, additional_headers=headers,
                                              ping_interval=20, ping_timeout=20,
                                              max_size=4 * 1024 * 1024) as ws:
                    LOG.info("已连接 OneBot：%s", self.config.ws_url)
                    delay = 2
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                            if not isinstance(event, dict):
                                continue
                        except json.JSONDecodeError:
                            continue
                        task = asyncio.create_task(self.process(event))
                        self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)
                        if len(self.tasks) >= 100:
                            await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("OneBot 连接中断：%s；%s 秒后重连", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def run(self) -> None:
        self.storage.prune()
        LOG.info("监听 %s 个群、%s 条规则；演练模式=%s", len(self.config.group_ids),
                 len(self.config.rules), self.config.dry_run)
        await asyncio.gather(self.listen(), self.maintenance())


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="QQ 羊毛群商品提醒")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--check", action="store_true", help="检查本机连接和配置，不发送消息")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)
    load_secrets(Path(args.config).resolve().parent / "secrets.env")
    if args.check:
        print(f"群号: {', '.join(str(x) for x in sorted(config.group_ids))}")
        print(f"规则: {len(config.rules)} 条；演练模式: {config.dry_run}；通知渠道: {config.notify_channel}")
        for name, uri in (("OneBot", config.ws_url),
                          ("GLM", os.getenv("AI_API_URL", "http://127.0.0.1:8080/v1/chat/completions"))):
            parsed = urlparse(uri)
            port = parsed.port or (443 if parsed.scheme in ("https", "wss") else 80)
            try:
                with socket.create_connection((parsed.hostname, port), timeout=2):
                    print(f"{name}: TCP 可连接 ({parsed.hostname}:{port})")
            except OSError as exc:
                print(f"{name}: 暂不可连接 ({parsed.hostname}:{port}) - {exc}")
        try:
            print(asyncio.run(probe_onebot(config)))
        except Exception as exc:
            print(f"OneBot API 暂不可用：{type(exc).__name__}: {exc}")
        print(f"pushplus token: {'已设置' if os.getenv('PUSHPLUS_TOKEN') else '未设置'}")
        print(f"图片识别: {'视觉模型' if os.getenv('AI_VISION_MODEL') else '本机 RapidOCR'}")
        return
    required_env(config)
    try:
        asyncio.run(App(config).run())
    except KeyboardInterrupt:
        LOG.info("已停止")


if __name__ == "__main__":
    main()

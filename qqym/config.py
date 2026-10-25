import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    aliases: tuple[str, ...]
    requirements: str
    max_price: Decimal | None
    voice_below: Decimal | None
    voice_on_match: bool


@dataclass(frozen=True)
class Config:
    ws_url: str
    group_ids: frozenset[int]
    database: Path
    dry_run: bool
    notify_channel: str
    unknown_price_notify: bool
    dedupe_hours: int
    voice_enabled: bool
    voice_daily_limit: int
    voice_cooldown_minutes: int
    voice_allowed_hours: tuple[int, int]
    rules: tuple[Rule, ...]


def _price(value: object) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("价格必须是正数")
    return result


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    ids = [int(x) for x in raw["group_ids"]]
    if not ids or any(x <= 0 for x in ids):
        raise ValueError("group_ids 至少填写一个有效群号")
    rules = []
    for item in raw["rules"]:
        aliases = tuple(str(x).strip() for x in item["aliases"] if str(x).strip())
        if not aliases:
            raise ValueError(f"规则 {item['id']} 需要至少一个别名")
        rules.append(Rule(str(item["id"]), str(item["name"]), aliases,
                          str(item.get("requirements", "")),
                          _price(item.get("max_price")), _price(item.get("voice_below")),
                          bool(item.get("voice_on_match", False))))
    if not rules or len({r.id for r in rules}) != len(rules):
        raise ValueError("rules 不能为空，且 id 不能重复")
    voice = raw.get("voice", {})
    hours = tuple(voice.get("allowed_hours", [8, 23]))
    if len(hours) != 2 or not (0 <= hours[0] < hours[1] <= 24):
        raise ValueError("voice.allowed_hours 应为 [起始小时, 结束小时]")
    database = Path(raw.get("database", "data/alerts.db"))
    if not database.is_absolute():
        database = path.parent / database
    ws_url = str(raw["onebot_ws_url"])
    if not (ws_url.startswith("ws://127.0.0.1:") or ws_url.startswith("ws://localhost:")):
        raise ValueError("第一版只允许连接本机 OneBot WebSocket")
    notify_channel = str(raw.get("notify_channel", "app"))
    if notify_channel not in ("app", "wechat"):
        raise ValueError("notify_channel 只支持 app 或 wechat")
    return Config(ws_url, frozenset(ids), database, bool(raw.get("dry_run", True)), notify_channel,
                  bool(raw.get("unknown_price_notify", False)), int(raw.get("dedupe_hours", 12)),
                  bool(voice.get("enabled", False)), int(voice.get("daily_limit", 3)),
                  int(voice.get("cooldown_minutes", 60)), (int(hours[0]), int(hours[1])),
                  tuple(rules))


def required_env(config: Config) -> None:
    if not config.dry_run and not os.getenv("PUSHPLUS_TOKEN"):
        raise ValueError("正式推送需要设置 PUSHPLUS_TOKEN")


def load_secrets(path: str | Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path.name} 存在无效行")
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in {"ONEBOT_ACCESS_TOKEN", "PUSHPLUS_TOKEN", "AI_API_URL", "AI_MODEL",
                       "AI_API_KEY", "AI_VISION_MODEL", "AI_VISION_API_URL"}:
            raise ValueError(f"{path.name} 不支持字段 {key}")
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))

"""从终端管理关注商品，不需要手工编辑 JSON。"""

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .config import load_config


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.json"


def parse_price(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        price = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("价格上限应填写数字，例如 699；留空表示不限价") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError("价格上限必须大于 0")
    return format(price.normalize(), "f")


def split_aliases(name: str, value: str) -> list[str]:
    aliases = [name.strip(), *re.split(r"[,，、;；]", value)]
    result = []
    seen = set()
    for alias in aliases:
        alias = alias.strip()
        key = alias.casefold()
        if alias and key not in seen:
            result.append(alias)
            seen.add(key)
    return result


def _read(path: Path) -> tuple[dict, int]:
    path = path.resolve()
    load_config(path)
    return json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime_ns


def _save(path: Path, raw: dict, original_mtime: int) -> None:
    path = path.resolve()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         prefix="config.", suffix=".json",
                                         dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(raw, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        load_config(temp_path)
        if path.stat().st_mtime_ns != original_mtime:
            raise RuntimeError("配置在编辑期间被其他程序修改，请重新操作")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def list_items(path: Path) -> list[dict]:
    raw, _ = _read(path)
    return raw["rules"]


def add_item(path: Path, name: str, aliases: str = "", requirements: str = "",
             max_price: str | None = None) -> str:
    name = name.strip()
    if not name:
        raise ValueError("商品名称不能为空")
    if len(name) > 100:
        raise ValueError("商品名称不能超过 100 个字符")
    raw, mtime = _read(path)
    if any(item["name"].strip().casefold() == name.casefold() for item in raw["rules"]):
        raise ValueError("关注清单中已有同名商品")
    item_id = "item-" + uuid.uuid4().hex[:12]
    raw["rules"].append({
        "id": item_id,
        "name": name,
        "aliases": split_aliases(name, aliases),
        "requirements": requirements.strip() or name,
        "max_price": parse_price(max_price),
        "voice_below": None,
        "voice_on_match": False,
    })
    _save(path, raw, mtime)
    return item_id


def remove_item(path: Path, item_id: str) -> None:
    raw, mtime = _read(path)
    remaining = [item for item in raw["rules"] if item["id"] != item_id]
    if len(remaining) == len(raw["rules"]):
        raise ValueError("找不到该商品")
    raw["rules"] = remaining
    _save(path, raw, mtime)


def set_price(path: Path, item_id: str, max_price: str | None) -> None:
    raw, mtime = _read(path)
    for item in raw["rules"]:
        if item["id"] == item_id:
            item["max_price"] = parse_price(max_price)
            _save(path, raw, mtime)
            return
    raise ValueError("找不到该商品")


def print_items(path: Path) -> list[dict]:
    items = list_items(path)
    print("\n当前关注清单：")
    if not items:
        print("（暂无关注商品；监听器不会发送商品提醒）")
    for index, item in enumerate(items, 1):
        price = item.get("max_price")
        limit = f"低于 ¥{price}" if price is not None else "不限价"
        print(f"{index}. {item['name']}  |  {limit}")
        print(f"   关键词：{'、'.join(item['aliases'])}")
    print()
    return items


def _pick_item(path: Path) -> dict:
    items = print_items(path)
    if not items:
        raise ValueError("清单为空，请先添加商品")
    value = input("输入商品序号：").strip()
    if not value.isdigit() or not 1 <= int(value) <= len(items):
        raise ValueError("商品序号无效")
    return items[int(value) - 1]


def interactive(path: Path) -> None:
    while True:
        print("QQDealWatch 关注清单管理")
        print("1. 查看清单   2. 添加商品   3. 修改价格上限   4. 删除商品   0. 退出")
        choice = input("请选择：").strip()
        try:
            if choice == "0":
                return
            if choice == "1":
                print_items(path)
            elif choice == "2":
                name = input("商品名称（含型号/规格）：").strip()
                aliases = input("其他常用关键词，逗号分隔（可留空）：")
                requirements = input("规格要求或排除条件（可留空）：")
                price = input("价格上限，必须低于该价才提醒（留空不限价）：")
                add_item(path, name, aliases, requirements, price)
                print(f"已添加「{name}」。监听器约 20 秒内自动载入。\n")
            elif choice == "3":
                item = _pick_item(path)
                price = input("新价格上限（留空表示不限价）：")
                set_price(path, item["id"], price)
                print("价格上限已更新。监听器约 20 秒内自动载入。\n")
            elif choice == "4":
                item = _pick_item(path)
                confirm = input(f"确认删除「{item['name']}」？输入 y 确认：").strip().lower()
                if confirm == "y":
                    remove_item(path, item["id"])
                    print("已删除；尚未发送的该商品提醒也会取消。监听器约 20 秒内自动载入。\n")
                else:
                    print("未删除。\n")
            else:
                print("请输入 0 到 4。\n")
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"操作失败：{exc}\n")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="管理 QQDealWatch 关注清单")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    actions = parser.add_subparsers(dest="action")
    actions.add_parser("list", help="查看清单")
    add = actions.add_parser("add", help="添加商品；不传参数时交互填写")
    add.add_argument("--name")
    add.add_argument("--aliases", default="")
    add.add_argument("--requirements", default="")
    add.add_argument("--max-price")
    remove = actions.add_parser("remove", help="按 ID 删除商品")
    remove.add_argument("id")
    price = actions.add_parser("set-price", help="按 ID 修改价格上限")
    price.add_argument("id")
    price.add_argument("max_price", help="数字，或 none 表示不限价")
    args = parser.parse_args()
    try:
        if args.action is None:
            interactive(args.config)
        elif args.action == "list":
            for item in list_items(args.config):
                print(f"{item['id']}\t{item['name']}\t上限：{item.get('max_price') or '不限价'}")
        elif args.action == "add":
            name = args.name or input("商品名称（含型号/规格）：")
            aliases = args.aliases if args.name else input("其他常用关键词，逗号分隔（可留空）：")
            requirements = args.requirements if args.name else input("规格要求或排除条件（可留空）：")
            max_price = args.max_price if args.name else input("价格上限（留空不限价）：")
            print("已添加：" + add_item(args.config, name, aliases, requirements, max_price))
        elif args.action == "remove":
            remove_item(args.config, args.id)
            print("已删除")
        elif args.action == "set-price":
            set_price(args.config, args.id,
                      None if args.max_price.lower() == "none" else args.max_price)
            print("价格上限已更新")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"操作失败：{exc}\n")


if __name__ == "__main__":
    main()

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .config import Config, Rule


@dataclass(frozen=True)
class Message:
    group_id: int
    message_id: str
    user_id: int
    text: str
    image_urls: tuple[str, ...]
    source_urls: tuple[str, ...]


@dataclass(frozen=True)
class Match:
    rule: Rule
    price: Decimal | None
    price_basis: str
    conditions: str
    evidence: str
    url: str | None
    voice: bool


URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+")


def parse_onebot(event: dict, groups: frozenset[int]) -> Message | None:
    if event.get("post_type") != "message" or event.get("message_type") != "group":
        return None
    try:
        group_id = int(event["group_id"])
        user_id = int(event.get("user_id", 0))
        message_id = str(event["message_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if group_id not in groups or not message_id:
        return None
    segments = event.get("message", [])
    texts = []
    images = []
    if isinstance(segments, str):
        texts.append(segments)
    elif isinstance(segments, list):
        for part in segments:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            data = part.get("data") or {}
            if not isinstance(data, dict):
                continue
            if kind == "text":
                texts.append(str(data.get("text", "")))
            elif kind == "image":
                url = str(data.get("url") or data.get("file") or "")
                if urlparse(url).scheme in ("http", "https"):
                    images.append(url)
            elif kind in ("json", "xml"):
                texts.append(str(data.get("data", ""))[:4000])
    text = " ".join(texts).strip()
    if not text and not images:
        text = str(event.get("raw_message", ""))[:4000]
    urls = tuple(dict.fromkeys(URL_RE.findall(text)))[:5]
    if not text and not images:
        return None
    return Message(group_id, message_id, user_id, text[:8000], tuple(images[:3]), urls)


def candidate_rules(message: Message, rules: tuple[Rule, ...], include_images: bool = True) -> tuple[Rule, ...]:
    normalized = lambda value: re.sub(r"[\s\-_]+", "", value).casefold()
    haystack = normalized(message.text)
    # 图片和卡片可能没有可搜索的文字，此时让视觉模型看所有规则。
    if message.image_urls and include_images:
        return rules
    return tuple(rule for rule in rules if any(normalized(alias) in haystack for alias in rule.aliases))


def evaluate_result(payload: dict, candidates: tuple[Rule, ...], config: Config) -> list[Match]:
    by_id = {r.id: r for r in candidates}
    matches = []
    for item in payload.get("matches", []):
        if not isinstance(item, dict) or item.get("match") is not True:
            continue
        rule = by_id.get(item.get("rule_id"))
        if rule is None or not str(item.get("evidence", "")).strip():
            continue
        raw_price = item.get("price")
        try:
            price = Decimal(str(raw_price)) if raw_price is not None else None
            if price is not None and (not price.is_finite() or price <= 0):
                continue
        except (InvalidOperation, ValueError):
            continue
        basis = str(item.get("price_basis", "unknown"))
        if basis not in ("final", "conditional", "unknown"):
            continue
        if basis != "final":
            price = None
        if rule.max_price is not None:
            if price is None and not config.unknown_price_notify:
                continue
            if price is not None and price >= rule.max_price:
                continue
        url = item.get("url")
        if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
            url = None
        evidence = str(item["evidence"])[:400]
        # 电话仅在明确写出该价格时触发；计算价或猜测价仍可进入普通推送。
        price_mentioned = price is not None and bool(re.search(
            rf"(?<!\d){re.escape(format(price.normalize(), 'f'))}(?!\d)", evidence))
        voice = bool(config.voice_enabled and (rule.voice_on_match or
                     (price_mentioned and rule.voice_below is not None and
                      price < rule.voice_below)))
        matches.append(Match(rule, price, basis, str(item.get("conditions", ""))[:300],
                             evidence, url, voice))
    return matches


def alert_key(message: Message, match: Match) -> str:
    # 无价格门槛的规则忽略价格变化，避免同一优惠补价后再次提醒。
    price_part = str(match.price) if match.rule.max_price is not None else "any"
    key = json.dumps([message.group_id, match.rule.id, price_part, match.url or ""],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

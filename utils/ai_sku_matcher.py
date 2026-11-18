"""用于 AI 客服的商品规格匹配工具。

SKU 选择使用确定性匹配，AI 只负责理解问题和组织回复，避免模型自行编造价格或库存。
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional


def normalize_sku_text(value: Any) -> str:
    """统一中英文、全半角和常见分隔符，便于匹配买家输入。"""
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s\-_/,，。:：;；|（）()【】\[\]]+", "", text)


def _property_values(sku_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    properties = []
    for raw_property in sku_config.get("properties") or []:
        if not isinstance(raw_property, dict):
            continue
        name = str(raw_property.get("name") or "").strip()
        values = []
        for raw_value in raw_property.get("values") or []:
            if isinstance(raw_value, dict):
                value = str(raw_value.get("value") or "").strip()
            else:
                value = str(raw_value or "").strip()
            if value:
                values.append(value)
        if name and values:
            properties.append({"name": name, "values": values})
    return properties


def _sku_items(sku_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = []
    for raw_item in sku_config.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        values = [str(value or "").strip() for value in raw_item.get("values") or []]
        if values and all(values):
            items.append({
                "values": values,
                "price": raw_item.get("price"),
                "quantity": raw_item.get("quantity"),
            })
    return items


def _value_in_message(value: str, message: str) -> bool:
    normalized_value = normalize_sku_text(value)
    return bool(normalized_value and normalized_value in message)


def match_sku_config(sku_config: Optional[Dict[str, Any]], message: str) -> Dict[str, Any]:
    """根据买家消息匹配 SKU。

    返回 status：unavailable、none、partial、ambiguous 或 matched。
    partial/ambiguous 都不直接选 SKU，而是交给 AI 请求买家补充信息。
    """
    if not isinstance(sku_config, dict) or not sku_config.get("enabled"):
        return {"status": "unavailable", "candidates": [], "properties": []}

    properties = _property_values(sku_config)
    items = _sku_items(sku_config)
    message_text = normalize_sku_text(message)
    if not properties or not items or not message_text:
        return {"status": "none", "candidates": [], "properties": properties}

    detected_values: Dict[str, List[str]] = {}
    for prop in properties:
        # 长值优先，避免“红”抢先匹配“红色”。
        matches = [
            value for value in sorted(prop["values"], key=lambda value: len(normalize_sku_text(value)), reverse=True)
            if _value_in_message(value, message_text)
        ]
        if matches:
            detected_values[prop["name"]] = matches

    if not detected_values:
        return {
            "status": "none",
            "candidates": [],
            "properties": properties,
            "detected_values": {},
        }

    candidates = []
    for item in items:
        if len(item["values"]) != len(properties):
            continue
        matches = True
        for index, prop in enumerate(properties):
            value = item["values"][index]
            # 买家没有提到的规格暂不限制，用于返回需要进一步确认的候选组合。
            if prop["name"] in detected_values and value not in detected_values[prop["name"]]:
                matches = False
                break
        if matches:
            candidates.append(item)

    detected_property_count = len(detected_values)
    all_properties_detected = detected_property_count == len(properties)
    if len(candidates) == 1 and all_properties_detected:
        item = candidates[0]
        quantity = _safe_number(item.get("quantity"))
        return {
            "status": "matched",
            "item": item,
            "candidates": candidates,
            "properties": properties,
            "detected_values": detected_values,
            "in_stock": quantity is None or quantity > 0,
        }

    if candidates:
        status = "ambiguous" if all_properties_detected else "partial"
    else:
        status = "partial" if detected_property_count < len(properties) else "none"

    return {
        "status": status,
        "candidates": candidates,
        "properties": properties,
        "detected_values": detected_values,
    }


def _safe_number(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_sku_context(sku_config: Optional[Dict[str, Any]], match_result: Optional[Dict[str, Any]] = None) -> str:
    """构造给 AI 的 SKU 事实上下文。"""
    if not isinstance(sku_config, dict) or not sku_config.get("enabled"):
        return ""

    properties = _property_values(sku_config)
    items = _sku_items(sku_config)
    if not properties:
        return ""

    option_text = "；".join(
        f"{prop['name']}：{'、'.join(prop['values'])}" for prop in properties
    )
    lines = [
        "商品已启用多规格。可选规格：" + option_text,
        "只能使用上面的规格值，不要自行创造规格、价格或库存。",
    ]

    result = match_result or {}
    status = result.get("status")
    if status == "matched" and result.get("item"):
        item = result["item"]
        selected = " / ".join(item.get("values") or [])
        quantity = item.get("quantity")
        price = item.get("price")
        stock_text = "有库存" if result.get("in_stock") else "库存为 0"
        lines.append(
            f"系统根据当前消息匹配到：{selected}；价格：{price} 元；库存：{quantity}；{stock_text}。"
        )
    elif status in {"partial", "ambiguous"}:
        candidates = result.get("candidates") or []
        if candidates:
            candidate_text = "、".join(" / ".join(item.get("values") or []) for item in candidates[:20])
            lines.append(f"当前消息无法唯一确定规格，可供进一步确认的组合：{candidate_text}。")
        else:
            lines.append("当前消息只说出了部分或无效规格，请向买家询问缺少的规格值。")
    elif status == "none":
        lines.append("当前消息没有匹配到完整规格；如果买家询问规格，请先让其选择可选规格。")

    return "\n".join(lines)

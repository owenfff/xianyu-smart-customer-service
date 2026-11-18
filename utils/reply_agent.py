"""第一阶段轻量客服 Agent。

这里的 Agent 是一个确定性的编排层，不直接替用户发消息，也不让模型
自行猜价格。它只负责：识别问题类型、决定是否需要商品资料、整理 SKU
事实，并把处理策略交给现有 AI 回复引擎。
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Optional


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _as_number(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_price(value: Any) -> str:
    number = _as_number(value)
    if number is None:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _rich_text_to_plain_text(value: Any) -> str:
    """从商品详情的富文本结构中提取可读文字。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            part for part in (_rich_text_to_plain_text(item) for item in value) if part
        ).strip()
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        children = value.get("children")
        if children is not None:
            return _rich_text_to_plain_text(children)
    return ""


def _raw_sku_price(raw_sku: Dict[str, Any]) -> Optional[float]:
    """闲鱼详情接口通常以分返回价格，优先使用明确的 priceInCent。"""
    cents = _as_number(raw_sku.get("priceInCent"))
    if cents is not None:
        return round(cents / 100, 2)
    price = _as_number(raw_sku.get("price"))
    if price is None:
        return None
    return round(price / 100, 2)


def _build_live_sku_config(raw_skus: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_skus, list):
        return None

    property_names: List[str] = []
    normalized_items: List[Dict[str, Any]] = []
    seen_combinations = set()

    for raw_sku in raw_skus:
        if not isinstance(raw_sku, dict):
            continue
        raw_properties = raw_sku.get("propertyList") or []
        if not isinstance(raw_properties, list) or not raw_properties:
            continue

        property_values: Dict[str, str] = {}
        for raw_property in raw_properties:
            if not isinstance(raw_property, dict):
                continue
            name = str(
                raw_property.get("propertyText")
                or raw_property.get("propertyName")
                or ""
            ).strip()
            value = str(
                raw_property.get("actualValueText")
                or raw_property.get("valueText")
                or raw_property.get("propertyValue")
                or ""
            ).strip()
            if not name or not value:
                continue
            if name not in property_names:
                property_names.append(name)
            property_values[name] = value

        if not property_values or any(name not in property_values for name in property_names):
            continue

        values = [property_values[name] for name in property_names]
        combination = tuple(values)
        if combination in seen_combinations:
            continue
        price = _raw_sku_price(raw_sku)
        if price is None:
            continue
        seen_combinations.add(combination)
        normalized_items.append({
            "values": values,
            "price": price,
            "quantity": raw_sku.get("quantity"),
            "sku_id": raw_sku.get("skuId"),
            "inventory_id": raw_sku.get("inventoryId"),
        })

    if not property_names or not normalized_items:
        return None

    property_values = []
    for name in property_names:
        values = []
        seen_values = set()
        for item in normalized_items:
            value = item["values"][property_names.index(name)]
            if value not in seen_values:
                seen_values.add(value)
                values.append({"value": value})
        property_values.append({
            "name": name,
            "type": "custom",
            "support_image": False,
            "values": values,
        })

    return {
        "enabled": True,
        "properties": property_values,
        "items": normalized_items,
    }


def extract_platform_item_context(payload: Any, item_id: str = "") -> Optional[Dict[str, Any]]:
    """把闲鱼商品详情接口响应转换成客服能使用的商品/SKU事实。

    该函数只读取接口响应，不负责网络请求；失败时返回 None，调用方应
    交给后续人工处理流程，不应让模型猜测商品事实。
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None
    item = data.get("itemDO") or data.get("item") or {}
    if not isinstance(item, dict):
        return None

    title = str(item.get("title") or item.get("itemTitle") or "").strip()
    description = str(item.get("desc") or item.get("description") or "").strip()
    if not description:
        description = _rich_text_to_plain_text(item.get("richTextDesc"))

    raw_skus = item.get("idleItemSkuList") or item.get("skuList") or []
    sku_config = _build_live_sku_config(raw_skus)

    min_price = item.get("minPrice")
    if min_price in (None, "") and sku_config:
        min_price = min(
            item_data.get("price")
            for item_data in sku_config["items"]
            if item_data.get("price") is not None
        )
    max_price = item.get("maxPrice")
    if max_price in (None, "") and sku_config:
        max_price = max(
            item_data.get("price")
            for item_data in sku_config["items"]
            if item_data.get("price") is not None
        )

    price_text = _format_price(min_price)
    price_range = price_text
    max_price_text = _format_price(max_price)
    if price_text and max_price_text and price_text != max_price_text:
        price_range = f"{price_text}-{max_price_text}"

    if not title and not description and not sku_config:
        return None

    return {
        "item_id": str(item_id or item.get("itemId") or "").strip(),
        "title": title or "未知商品",
        "description": description,
        "price": price_text,
        "price_range": price_range,
        "category": str(item.get("categoryId") or "").strip(),
        "sku_config": sku_config,
        "source": "platform_detail_api",
    }


@dataclass(frozen=True)
class ReplyPlan:
    intent: str
    action: str
    needs_product_data: bool
    needs_live_availability: bool
    product_data_available: bool
    sku_status: str
    has_fixed_knowledge: bool
    needs_human_review: bool = False
    human_review_reason: str = ""

    def to_prompt_context(self) -> str:
        action_text = {
            "sync_product": "先使用已同步的商品和 SKU 事实回答；若仍无事实，只能说明需要核实",
            "answer_from_sku": "优先使用匹配到的 SKU 价格和库存回答，不得改写事实",
            "ask_for_spec": "规格未唯一确定；如果酒店价格资料已有多个房型和价格，先列出参考价再询问房型，不要笼统说需要核实价格",
            "verify_live_availability": "房态/余位属于动态信息，不能仅凭 SKU 数量承诺已确认",
            "human_review": "问题需要人工处理；只提醒人工，不生成自动回复",
            "answer_from_rag": "使用全局酒店资料库回答；当前闲鱼商品详情可能只是客户进入聊天的载体，不得覆盖酒店资料",
            "answer_from_product": "使用商品资料回答，不要主动扩展未提供的信息",
            "general_ai": "按普通客服语气简短回答",
        }.get(self.action, "按商品事实谨慎回答")
        return "\n".join([
            f"意图分类：{self.intent}",
            f"回复策略：{action_text}",
            f"商品资料：{'已找到' if self.product_data_available else '缺失'}",
            f"SKU状态：{self.sku_status}",
            f"固定问答参考：{'有' if self.has_fixed_knowledge else '无'}",
            f"人工提醒：{self.human_review_reason or '无需提醒'}",
            "事实约束：价格、规格、库存必须以系统提供的数据为准；缺失时不要编造。",
        ])


class LightweightReplyAgent:
    """第一阶段客服 Agent：分类和编排，不替代现有 AI 引擎。"""

    PRICE_TERMS = ("多少钱", "多少", "价格", "价钱", "费用", "报价", "卖多少")
    AVAILABILITY_TERMS = (
        "有房", "有货", "库存", "余位", "可订", "能住", "房态", "还能订", "可以订",
        "能不能订", "是否有房", "还有没有房", "有票", "余票", "还有票", "票还有吗",
        "还有名额", "名额", "能去吗",
    )
    ORDER_TERMS = (
        "怎么下单", "如何下单", "怎样下单", "下单流程", "怎么订", "如何订", "怎么预订",
        "如何预订", "怎样预订", "怎么预定", "如何预定", "怎样预定", "订房", "预订",
        "预定", "怎么预约", "如何预约", "怎样预约", "预约流程", "购买流程", "怎么购买",
        "怎么买", "付款方式",
    )
    CHECK_IN_TERMS = (
        "怎么入住", "如何入住", "怎样入住", "入住流程", "办理入住", "到店怎么住",
        "到店入住", "前台办理", "入住须知", "退房流程", "身份证", "押金",
    )
    REFUND_TERMS = (
        "退款", "退订", "退房费", "取消订单", "取消预订", "取消预定", "改期",
        "改日期", "不能去", "不去了", "不去可以", "不去能", "能退吗", "可以退吗",
    )
    # 这些资料虽然不全是酒店，但都属于“客服维护的业务 RAG”，
    # 不能因为客户是从一个无关闲鱼商品进入会话，就退回商品详情回答。
    HOTEL_TERMS = (
        "乌镇", "通安", "枕水", "堤上", "昭明", "松鼠部落", "云山境", "半山隐竹",
        "1168", "星空天文", "云端围星季", "山巅星迹", "亲子星野", "酒店", "客栈",
        "牛首山", "五马渡", "游轮", "游船",
    )
    # HOTEL_TERMS 同时承担「走全局业务 RAG」的职责，因此必须包含牛首山门票、
    # 五马渡游轮等非酒店项——它们同样有独立的业务资料，不能退回商品详情。
    # 但「酒店价格话术」只适用于按房型/间晚报价的真酒店；本表从 HOTEL_TERMS
    # 中剔除门票与船票，避免门票咨询被追加「请问您需要什么房型」。
    HOTEL_PRICE_TEMPLATE_TERMS = (
        "乌镇", "通安", "枕水", "堤上", "昭明", "松鼠部落", "云山境", "半山隐竹",
        "1168", "星空天文", "云端围星季", "山巅星迹", "亲子星野", "酒店", "客栈",
    )
    # 游轮规则适用于所有游轮类商品；商品标题、商品详情或最近会话中
    # 只要出现这些词，就不要把当前闲鱼商品的示例人数当成固定话术。
    CRUISE_TERMS = (
        "游轮", "游船", "航次", "班次", "五马渡", "登船", "码头",
    )
    CRUISE_TICKET_TERMS = (
        "怎么取票", "如何取票", "怎样取票", "取票方式", "在哪里取票",
        "去哪取票", "怎么拿票", "如何拿票", "怎样拿票", "领票", "领取门票",
        "取门票", "拿门票",
    )
    CRUISE_PASSENGER_TERMS = (
        "预约", "预订", "预定", "出游", "航次", "班次", "成人", "儿童",
        "幼儿", "老人", "姓名", "身份证", "几个人", "几位", "登船", "购票",
        "买票", "安排",
    )
    FOLLOW_UP_TERMS = ("呢", "那", "这个", "这个呢", "还有", "另外", "同样", "上述", "刚才")
    DATE_LIKE_RE = re.compile(
        r"(?<!\d)(?:\d{1,2}\s*[./-]\s*\d{1,2}|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号]?)?)(?!\d)"
    )
    SPEC_TERMS = ("规格", "房型", "大床", "标间", "高级房", "套餐", "选哪个", "哪种", "sku")
    GREETING_TERMS = ("你好", "您好", "在吗", "在么", "是谁", "你是谁")

    @staticmethod
    def _context_has_hotel_reference(conversation_context: Optional[Iterable[Dict[str, Any]]]) -> bool:
        """只看当前会话最近几条消息，识别省略酒店名的追问。"""
        for row in list(conversation_context or [])[-6:]:
            content = str((row or {}).get("content") or "").strip().lower()
            if not content:
                continue
            if _contains_any(content, LightweightReplyAgent.HOTEL_TERMS):
                return True
            # AI上一轮列出房型/参考价格时，下一句“9.12呢”仍属于同一酒店问价。
            if _contains_any(content, ("参考价格", "房型", "房态", "一间一晚", "含双早")):
                return True
        return False

    @staticmethod
    def get_context_hotel_reference(
        conversation_context: Optional[Iterable[Dict[str, Any]]],
    ) -> str:
        """返回最近一次明确出现的酒店名，用于补全省略酒店名的追问。"""
        rows = list(conversation_context or [])[-6:]
        for row in reversed(rows):
            content = str((row or {}).get("content") or "").strip()
            if not content:
                continue
            # 具体酒店优先于泛称“酒店”，避免把任意商品标题当成酒店资料。
            for hotel in ("通安", "枕水", "堤上", "昭明", "松鼠部落", "云山境", "半山隐竹", "1168星空天文"):
                if hotel in content:
                    return hotel
        return ""

    @staticmethod
    def get_item_hotel_reference(item_info: Optional[Dict[str, Any]]) -> str:
        """从当前闲鱼商品资料识别酒店，给无法完整匹配 SKU 的询价兜底。"""
        if not isinstance(item_info, dict):
            return ""
        searchable_text = " ".join(
            str(item_info.get(field) or "")
            for field in ("title", "item_title", "desc", "item_detail", "description")
        )
        # 只返回明确酒店名，不把“酒店”这种泛称当成具体酒店。
        for hotel in ("通安", "枕水", "堤上", "昭明", "松鼠部落", "云山境", "半山隐竹", "1168星空天文"):
            if hotel in searchable_text:
                return hotel
        return ""

    @staticmethod
    def _item_searchable_text(item_info: Optional[Dict[str, Any]]) -> str:
        if not isinstance(item_info, dict):
            return ""
        return " ".join(
            str(item_info.get(field) or "")
            for field in (
                "title", "item_title", "desc", "item_detail", "description",
                "sku_context",
            )
        ).strip().lower()

    @staticmethod
    def _context_has_cruise_reference(
        conversation_context: Optional[Iterable[Dict[str, Any]]],
    ) -> bool:
        """识别省略商品名的游轮追问，例如“周日十点可以吗”。"""
        for row in list(conversation_context or [])[-8:]:
            content = str((row or {}).get("content") or "").strip().lower()
            if content and _contains_any(content, LightweightReplyAgent.CRUISE_TERMS):
                return True
        return False

    def is_cruise_query(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
        item_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """判断当前消息是否属于游轮商品咨询。"""
        text = str(message or "").strip().lower()
        return bool(
            _contains_any(text, self.CRUISE_TERMS)
            or self._context_has_cruise_reference(conversation_context)
            or _contains_any(self._item_searchable_text(item_info), self.CRUISE_TERMS)
        )

    def is_cruise_ticket_pickup_query(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
        item_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """识别游轮取票问题；模糊的“怎么拿/前台在哪”需有游轮上下文。"""
        text = str(message or "").strip().lower()
        if not self.is_cruise_query(text, conversation_context, item_info):
            return False
        if _contains_any(text, self.CRUISE_TICKET_TERMS) or "取票" in text:
            return True
        return bool(
            self._context_has_cruise_reference(conversation_context)
            and _contains_any(text, ("怎么拿", "如何拿", "去哪拿", "前台", "领票"))
        )

    def cruise_needs_passenger_identity(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
        item_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """只有进入预约/出游安排语境时，才要求收集所有人的身份信息。"""
        text = str(message or "").strip().lower()
        return bool(
            self.is_cruise_query(text, conversation_context, item_info)
            and _contains_any(text, self.CRUISE_PASSENGER_TERMS)
        )

    def _is_hotel_follow_up(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> bool:
        text = str(message or "").strip().lower()
        if not text or not self._context_has_hotel_reference(conversation_context):
            return False
        return bool(
            self.DATE_LIKE_RE.search(text)
            or _contains_any(text, self.FOLLOW_UP_TERMS)
            or re.fullmatch(r"[\d\s./年月日号-]+", text or "")
        )

    def classify(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> str:
        text = str(message or "").strip().lower()
        if _contains_any(text, self.AVAILABILITY_TERMS):
            return "availability"
        if _contains_any(text, self.PRICE_TERMS):
            return "price"
        if self._is_hotel_follow_up(text, conversation_context):
            return "price"
        if _contains_any(text, self.SPEC_TERMS):
            return "specification"
        if _contains_any(text, self.GREETING_TERMS):
            return "greeting"
        return "general"

    def get_human_review_reason(self, message: str) -> str:
        """判断是否需要人工处理，不判断普通价格，也不把日期误判成房态。"""
        text = str(message or "").strip().lower()
        if _contains_any(text, self.AVAILABILITY_TERMS):
            return "实时房态/余位"
        if _contains_any(text, self.ORDER_TERMS):
            return "下单/预订流程"
        if _contains_any(text, self.CHECK_IN_TERMS):
            return "入住/到店流程"
        if _contains_any(text, self.REFUND_TERMS):
            return "退款/取消/改期政策"
        return ""

    def is_hotel_rag_query(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> bool:
        """判断是否应使用全局酒店资料，而不是当前闲鱼商品的详情/SKU。"""
        text = str(message or "").strip().lower()
        return _contains_any(text, self.HOTEL_TERMS) or self._is_hotel_follow_up(
            text, conversation_context
        )

    @staticmethod
    def _context_has_hotel_price_reference(
        conversation_context: Optional[Iterable[Dict[str, Any]]],
    ) -> bool:
        """只认真酒店名的上下文引用，门票/船票类不计入酒店价格话术。

        刻意不复用 _context_has_hotel_reference：后者为 RAG 路由服务，
        会把「房型/房态」等泛词也算作酒店语境；本判定若被这些泛词触发，
        上一轮的错误输出自身就会成为下一轮的误判依据。
        """
        for row in list(conversation_context or [])[-6:]:
            content = str((row or {}).get("content") or "").strip().lower()
            if content and _contains_any(
                content, LightweightReplyAgent.HOTEL_PRICE_TEMPLATE_TERMS
            ):
                return True
        return False

    def is_hotel_price_template_query(
        self,
        message: str,
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> bool:
        """判断是否应套用酒店房型价格话术（门票、船票类不适用）。

        与 is_hotel_rag_query 的分工：后者决定「走哪套资料」，本方法决定
        「用哪套话术」。牛首山门票、五马渡游轮需要前者为真、后者为假。
        """
        text = str(message or "").strip().lower()
        if _contains_any(text, self.HOTEL_PRICE_TEMPLATE_TERMS):
            return True
        if not self._context_has_hotel_price_reference(conversation_context):
            return False
        return bool(
            self.DATE_LIKE_RE.search(text)
            or _contains_any(text, self.FOLLOW_UP_TERMS)
            or re.fullmatch(r"[\d\s./年月日号-]+", text or "")
        )

    @staticmethod
    def _product_data_available(item_info: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(item_info, dict):
            return False
        title = str(item_info.get("title") or item_info.get("item_title") or "").strip()
        description = str(item_info.get("desc") or item_info.get("item_detail") or "").strip()
        price = _as_number(item_info.get("price"))
        return bool(
            (title and title not in {"未知商品", "商品信息获取失败"})
            or description
            or (price is not None and price > 0)
        )

    def plan(
        self,
        message: str,
        item_info: Optional[Dict[str, Any]] = None,
        sku_match: Optional[Dict[str, Any]] = None,
        knowledge_context: str = "",
        conversation_context: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> ReplyPlan:
        intent = self.classify(message, conversation_context)
        human_review_reason = self.get_human_review_reason(message)
        hotel_rag_query = self.is_hotel_rag_query(message, conversation_context)
        product_intent = intent in {"price", "availability", "specification"}
        product_available = self._product_data_available(item_info)
        sku_status = str((sku_match or {}).get("status") or "unavailable")

        if intent == "availability":
            action = "verify_live_availability"
        elif human_review_reason:
            action = "human_review"
        elif hotel_rag_query and sku_status in {"partial", "ambiguous"} and product_available:
            # 当前商品本身带有未唯一确定的房型时，保留“先列参考价、再问房型”的策略。
            # 实际酒店全局 RAG 检索仍由上层按 hotel_rag_query 执行，不会退回普通商品回答。
            action = "ask_for_spec"
        elif hotel_rag_query:
            action = "answer_from_rag"
        elif product_intent and not product_available:
            action = "sync_product"
        elif sku_status in {"partial", "ambiguous"}:
            action = "ask_for_spec"
        elif sku_status == "matched":
            action = "answer_from_sku"
        elif product_intent:
            action = "answer_from_product"
        else:
            action = "general_ai"

        return ReplyPlan(
            intent=intent,
            action=action,
            needs_product_data=product_intent,
            needs_live_availability=intent == "availability",
            product_data_available=product_available,
            sku_status=sku_status,
            has_fixed_knowledge=bool(str(knowledge_context or "").strip()),
            needs_human_review=bool(human_review_reason),
            human_review_reason=human_review_reason,
        )

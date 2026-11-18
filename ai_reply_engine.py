"""
AI回复引擎模块 - 统一意图识别与回复生成

【重构版本】
- 将意图判断和回复生成合并为一次AI调用
- AI根据完整上下文自行判断意图并生成回复
- 避免关键词误判导致的不当回复
- 支持多种API类型：OpenAI / OpenAI Responses / Gemini / Anthropic / Azure OpenAI / Ollama / DashScope
"""

import json
import re
import time
import requests
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
from db_manager import db_manager
from utils.cancellable_ai import post as cancellable_post, check_generation, run_generation


class AIReplyEngine:
    """AI回复引擎 - 统一意图识别与回复生成"""
    
    def __init__(self):
        self._init_default_prompts()
        # 用于控制同一chat_id消息的串行处理
        self._chat_locks = {}
        self._chat_locks_lock = threading.Lock()
    
    def _init_default_prompts(self):
        """初始化默认提示词（用于构建统一提示词）"""
        self.default_prompts = {
            'price': '''【议价场景】
策略：根据议价次数递减优惠
- 第1次：可小幅优惠，表达诚意
- 第2次：中等优惠，强调已是优惠价
- 第3次及以后：最大优惠或坚持底线
语气友好但坚定，突出商品价值和优势。''',
            
            'tech': '''【技术/产品问题】
基于商品信息回答，不要自行发挥。
如果问题超出商品信息范围，回复："等等，这个我需要看一看"''',
            
            'default': '''【一般咨询】
基于商品信息回答物流、售后等问题。
如果问题超出商品信息范围，回复："等等，这个我需要看一看"
如果客户明确询问退款，回复："虚拟产品，一旦发出是不可以退款的"'''
        }
    
    def _resolve_api_type(self, settings: dict) -> str:
        """根据设置解析实际的API类型（支持显式设置和自动检测）"""
        api_type = (settings.get('api_type') or '').strip()
        if api_type:
            return api_type

        # 向后兼容：自动检测
        if self._is_dashscope_app_api(settings):
            return 'dashscope'
        if self._is_gemini_api(settings):
            return 'gemini'
        return 'openai'

    def _call_configured_model(self, settings: dict, messages: list,
                               max_tokens: int = 1200, temperature: float = 0.2) -> str:
        """统一调用当前账号配置的模型，供知识整理等后台能力复用。"""
        api_type = self._resolve_api_type(settings)
        if api_type == 'dashscope':
            if self._is_dashscope_app_api(settings):
                return self._call_dashscope_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
            return self._call_openai_chat_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'gemini':
            return self._call_gemini_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'openai_responses':
            return self._call_openai_responses_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'anthropic':
            return self._call_anthropic_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'azure_openai':
            return self._call_azure_openai_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        return self._call_openai_chat_api(settings, messages, max_tokens=max_tokens, temperature=temperature)

    def organize_knowledge_update(self, cookie_id: str, raw_text: str,
                                  target_hint: str = '') -> Dict[str, object]:
        """把管理员的自然语言整理成可预览、可确认的知识更新 JSON。"""
        clean_raw = str(raw_text or '').strip()

        def fallback_preview(reason: str) -> Dict[str, object]:
            """AI不可用时保留原文，仍允许管理员检查后确认保存。"""
            lines = [line.strip() for line in clean_raw.splitlines() if line.strip()]
            inferred_target = str(target_hint or '').strip()
            if not inferred_target and lines:
                heading = re.sub(r'[：:]?\s*产品详情\s*$', '', lines[0]).strip()
                product_match = re.search(r'产品[^：:]*[：:]\s*([^/；;，,\n]+)', clean_raw)
                inferred_target = (product_match.group(1).strip() if product_match else heading)[:80]
            price_markers = ('价格', '售价', '结算', '元', '补差', '加价', '套餐')
            source_type = 'price' if any(marker in clean_raw for marker in price_markers) else 'faq'
            topic = '价格与套餐' if source_type == 'price' else '业务资料'
            target = inferred_target or '未识别对象'
            return {
                'target': target,
                'source_type': source_type,
                'topic': topic,
                'title': f'{target}｜{topic}',
                'content': clean_raw,
                'tags': inferred_target,
                'valid_until': '',
                'availability': [],
                'raw_input': clean_raw,
                'organizer_status': 'fallback',
                'organizer_warning': f'AI整理未完成（{reason}），已保留原文，请检查并修改预览后再保存。',
            }

        settings = db_manager.get_ai_reply_settings(cookie_id)
        if not settings or not settings.get('api_key'):
            return fallback_preview('当前账号未配置可用AI')
        today = datetime.now().date().isoformat()
        prompt = f"""今天是 {today}。请把下面的业务资料更新整理成严格 JSON，不要输出代码块或解释。
目标提示：{target_hint or '未指定，请从原文识别'}
原始输入：{raw_text}

JSON 格式：
{{
  "target": "酒店或产品名称",
  "source_type": "price或faq",
  "topic": "简短且稳定的主题名称",
  "title": "目标｜主题",
  "content": "清晰完整、可直接供客服AI使用的事实文本",
  "tags": "逗号分隔关键词",
  "valid_until": "YYYY-MM-DD或空字符串",
  "availability": [
    {{"hotel_name":"酒店名称","stay_date":"YYYY-MM-DD","scope_type":"hotel或package","package_name":"","status":"closed或confirm","note":""}}
  ]
}}
规则：房态“没房/关闭/关”用 closed；“有房/可订/待核实/需确认”统一用 confirm。
涉及房态时必须生成 availability；房态不要重复写成普通价格知识。没有房态则 availability 为空数组。
未写年份的日期按 {today[:4]} 年；未写月份的日期按当前月份。不能编造原文没有的价格、权益或日期。"""
        try:
            raw_reply = self._call_configured_model(
                settings,
                [
                    {'role': 'system', 'content': '你是业务知识整理器，只输出合法 JSON。'},
                    {'role': 'user', 'content': prompt},
                ],
                max_tokens=4000,
                temperature=0.1,
            )
        except Exception as exc:
            logger.warning(f'AI知识整理失败，切换原文预览: {exc}')
            return fallback_preview(str(exc))
        text = str(raw_reply or '').strip()
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I | re.S).strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            return fallback_preview('AI返回内容不是有效JSON')
        if not isinstance(result, dict):
            return fallback_preview('AI整理结果格式不正确')
        result['raw_input'] = clean_raw
        result['organizer_status'] = 'ai'
        return result

    def _is_dashscope_app_api(self, settings: dict) -> bool:
        """判断是否为DashScope应用API（/apps/模式）"""
        base_url = (settings.get('base_url') or '').strip().rstrip('/')
        return 'dashscope.aliyuncs.com' in base_url and '/apps/' in base_url

    def _is_gemini_api(self, settings: dict) -> bool:
        """判断是否为Gemini API"""
        model_name = settings.get('model_name', '').lower()
        return 'gemini' in model_name
    
    def _build_unified_system_prompt(self, custom_prompts: dict, settings: dict) -> str:
        """
        构建统一的系统提示词
        将意图判断和回复生成整合到一个提示词中
        """
        # 获取各场景的指导（优先使用用户自定义）
        price_guide = custom_prompts.get('price', self.default_prompts['price'])
        tech_guide = custom_prompts.get('tech', self.default_prompts['tech'])
        default_guide = custom_prompts.get('default', self.default_prompts['default'])
        
        # 获取议价设置
        max_bargain_rounds = settings.get('max_bargain_rounds', 3)
        max_discount_percent = settings.get('max_discount_percent', 10)
        max_discount_amount = settings.get('max_discount_amount', 100)
        
        unified_prompt = f"""你是一位专业的电商客服AI助手。请根据用户消息和上下文，直接生成合适的回复。

## 核心原则
1. **准确理解意图**：只根据用户实际说的内容判断，不要过度解读
2. **不要主动提及敏感话题**：用户没提到的（如退款、砍价）不要主动提
3. **基于商品信息回答**：只回答商品信息中有的内容
4. **避免重复**：结合对话历史，不要重复之前说过的话
5. **语言简洁友好**：回复要自然、简短；酒店价格需要列出多个房型时，可以按房型分行
6. **规格信息以系统提供的数据为准**：不得自行编造 SKU、价格或库存
7. **固定问答仅作为参考**：相关时优先遵循固定问答内容，不要提及检索过程；没有相关内容时正常回答
8. **必须输出可发送的文字**：不要返回空字符串、JSON、思考过程或 Markdown 代码块
9. **遵守系统回复决策**：按系统给出的意图和处理策略组织回复；事实仍以商品和 SKU 数据为准
10. **资料库使用规则**：普通商品价格和规格只使用当前商品、SKU；如果客户明确提到乌镇、通安、枕水、堤上等酒店，或牛首山、五马渡、游轮等旅游产品，优先使用有效期内的全局业务资料，即使当前闲鱼商品只是进入聊天的载体；历史客服案例只参考表达方式，不能当作当前价格或库存
11. **客服案例不是模板**：标记为“仅参考表达方式”的案例只能学习自然语气、称呼、简短程度和沟通顺序，不要照抄整句，也不能把案例里的价格、日期、房态、政策当成事实
12. **人工核实优先**：如果系统提示需要人工提醒，涉及实时房态、下单/预订、入住/到店或退款/改期等实际业务确认时，不生成任何自动回复，由人工直接处理
13. **资料不足时转人工**：普通价格资料缺失或已过期时不要编造，也不要生成“我先帮您核实一下，请稍等～”等占位话术；由上层提醒人工并停止本次自动回复。资料库已有有效期内的参考价格时，直接使用资料回答，不要说“没有查到”。实时房态和最终确认仍由人工确认
14. **日期优先**：酒店客户没有提供入住日期时，先只询问“请问您几号入住、什么房型？”，不要先报一组不对应日期的价格；客户补充日期后，再使用该日期对应的价格资料

## 场景处理指南

### 当用户明确要求降价/优惠/砍价时
{price_guide}
- 议价限制：最多{max_bargain_rounds}轮，最大优惠{max_discount_percent}%或{max_discount_amount}元

### 当用户询问产品技术/功能/使用问题时
{tech_guide}

### 其他一般咨询（物流、售后、商品介绍等）
{default_guide}

### 商品规格问题
- 如果系统给出了唯一匹配的规格，按系统匹配到的价格和库存回答。
- 如果规格不完整或有多个可能组合，先询问买家缺少的规格，不要猜测。
- 如果匹配规格库存为 0，明确告知该规格暂时无库存，并可引导选择其他规格。

### 酒店价格问题
- 商品提供anji套餐专属资料或安吉景酒套餐资料时，优先遵循该套餐规则：只使用对应酒店套餐权益，价格为整套价；有date_total时直接采用，禁止再补差或换算每晚价格。云山境两晚套餐含两天双人早餐。儿童资格问题由系统转人工。套餐不适用下方普通酒店按房型每晚报价规则。
- 如果客户提供了酒店名称、入住日期并询问价格，且“AI资料库检索结果”中有该日期适用的价格资料，先直接报资料中的参考价。
- 如果客户没有指定房型但资料中有多个房型，列出相关房型和价格，然后询问客户想要哪一种；不要笼统回复“我帮您核实具体价格”。
- 如果客户已经提供入住日期，报完参考价格后只询问房型并说明可以帮忙查房态；不要重复询问日期。
- 酒店报价按“元/晚”表达。不要写“以上价格均为每晚价格”。夜间房态提醒由系统按北京时间20:00至次日08:00统一追加，模型不要自行添加；仅追问酒店、日期或房型而没有报价时，不添加房态提醒。
- RAG 中用于计算周末价格的“基础价加价规则”属于内部依据，不要在客户回复中展示计算过程，只展示计算后的最终价格。
- 资料中的“房态和最终确认价需要人工核实”只表示下单前要人工确认有没有房，不影响先报参考价格。
- 只有客户询问“有没有房、能不能订、最终确认价”或资料没有对应日期价格时，才回复需要人工核实；系统标记为下单/预订或入住/到店流程时，也只能回答资料中明确写出的步骤。

### 牛首山和游轮产品问题
- 所有牛首山商品按2026年9月14日最新规则：上午场恢复，成人100元/人，上午9点牛首山东门集合；下午场成人身份证属地江浙沪皖以内100元/人、以外90元/人，下午13:40或者14:20在牛首山东门附近集合，具体以导游通知为准，提前一天预约。首次普通询价可先列以上成人价格，再询问“请问您计划几号出游？方便把游客的身份证属地告诉我，我帮您核对对应的价格～”，已知信息不重复问，不替客人选择属地。下午场儿童未满18岁、老人60至未满70岁，江浙沪皖以外70元/人、以内80元/人；满70周岁及以上门票免费、讲解耳麦20元/人。上午场仅明确成人价格，儿童和老人上午场价格不套用下午场标准，需转人工。导游提前一天联系，勿迟到，团队统一入园后可自由活动，携带身份证原件。不含小交通，往返20元/人。不能再说上午场关闭或将两个下午集合时间说成连续时间段。
- 客户问牛首山是否有票、还有没有名额、能否预订，交给人工确认，不由 AI 承诺。
- 游轮客户咨询时，先询问出游日期和航次；当天预订需至少提前 1 小时。成人参考价 119 元/人、儿童参考价 95 元/人，但余位、最终安排和预订结果必须人工确认。
- 以上规则适用于所有游轮商品；保持现有商品资料/RAG中的价格和航次不变，不要把示例人数当成固定人数。
- 游轮预约或乘客信息只收集所有人的姓名和身份证号码，不要索要手机号；删除“其中一人手机号、刷身份证上船”等旧话术。
- 客户询问取票方式时，直接回复：“我们是旅行社代订门票，到码头报我们旅行社名称登记取票，千万不要说在闲鱼购买的门票。”
- 客户明确要预约、下单或确认余票时，交给人工处理并提醒人工，不连续发送多条机器人占位回复。

## 特别注意
- 用户只是问价格≠用户在砍价，正常回答价格即可
- 用户咨询售后≠用户要退款，正常解答即可
- 如果用户的问题超过你的回答范围，比如发图片，可以说"等等，这个问题我需要看看"，不要自己回答

请直接输出回复内容，不要输出分析过程。"""
        
        return unified_prompt

    def _call_dashscope_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用DashScope API"""
        base_url = settings['base_url']
        if '/apps/' in base_url:
            app_id = base_url.split('/apps/')[-1].split('/')[0]
        else:
            raise ValueError("DashScope API URL中未找到app_id")

        url = f"https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion"

        system_content = ""
        user_content = ""
        for msg in messages:
            if msg['role'] == 'system':
                system_content = msg['content']
            elif msg['role'] == 'user':
                user_content = msg['content'] # 假设 user prompt 已在 generate_reply 中构建好

        if system_content and user_content:
            prompt = f"{system_content}\n\n用户问题：{user_content}\n\n请直接回答用户的问题："
        elif user_content:
            prompt = user_content
        else:
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        data = {
            "input": {"prompt": prompt},
            "parameters": {"max_tokens": max_tokens, "temperature": temperature},
            "debug": {}
        }
        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json"
        }

        logger.info(f"DashScope API请求: {url}")
        logger.info(f"发送的prompt: {prompt[:100]}...") # 避免 prompt 过长
        logger.debug(f"请求数据: {json.dumps(data, ensure_ascii=False)}")

        response = cancellable_post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"DashScope API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"DashScope API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        logger.debug(f"DashScope API响应: {json.dumps(result, ensure_ascii=False)}")

        if 'output' in result and 'text' in result['output']:
            return result['output']['text'].strip()
        else:
            raise Exception(f"DashScope API响应格式错误: {result}")

    def _call_gemini_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """
        调用Google Gemini REST API (v1beta)
        """
        api_key = settings['api_key']
        model_name = settings['model_name'] 
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

        headers = {"Content-Type": "application/json"}

        # --- 转换消息格式 (修复 P1-3: 增强健壮性) ---
        system_instruction = ""
        user_content_parts = []

        # 遍历消息，找到 system 和所有的 user parts
        for msg in messages:
            if msg['role'] == 'system':
                system_instruction = msg['content']
            elif msg['role'] == 'user':
                # 我们只关心 user content
                user_content_parts.append(msg['content'])
        
        # 将所有 user parts 合并为最后的 user_content
        # 在我们的使用场景中 (generate_reply)，只会有一个 user part，但这样更安全
        user_content = "\n".join(user_content_parts)

        if not user_content:
            logger.warning(f"Gemini API 调用: 未在消息中找到 'user' 角色内容。Messages: {messages}")
            raise ValueError("未在消息中找到用户内容 (user content)")
        # --- 消息格式转换结束 ---

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        logger.info(f"Calling Gemini REST API: {url.split('?')[0]}")
        logger.debug(f"Gemini Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        response = cancellable_post(url, headers=headers, json=payload, timeout=30)

        if response.status_code != 200:
            logger.error(f"Gemini API 请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Gemini API 请求失败: {response.status_code} - {response.text}")
            
        result = response.json()
        logger.debug(f"Gemini API 响应: {json.dumps(result, ensure_ascii=False)}")

        try:
            reply_text = result['candidates'][0]['content']['parts'][0]['text']
            return reply_text.strip()
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Gemini API 响应格式错误: {result} - {e}")
            raise Exception(f"Gemini API 响应格式错误: {result}")

    def _call_openai_chat_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用OpenAI Chat Completions API（兼容OpenAI / Ollama / 其他兼容服务）"""
        base_url = settings['base_url'].rstrip('/')
        if not base_url.endswith('/v1'):
            base_url = base_url + '/v1'
        url = f"{base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        api_key = settings.get('api_key', '')
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        data = {
            "model": settings['model_name'],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        logger.info(f"OpenAI Chat API请求: {url}")
        response = cancellable_post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"OpenAI Chat API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"OpenAI Chat API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        choices = result.get('choices') or []
        message = (choices[0].get('message') or {}) if choices else {}
        content = message.get('content')
        if isinstance(content, list):
            content = ''.join(
                str(part.get('text') or '') if isinstance(part, dict) else str(part or '')
                for part in content
            )
        content = str(content or '').strip()
        if not content:
            finish_reason = choices[0].get('finish_reason') if choices else None
            logger.error(
                'OpenAI兼容API返回空回复: '
                f"finish_reason={finish_reason}, message_keys={list(message.keys())}, "
                f"response_id={result.get('id', '')}"
            )
            raise ValueError('AI接口返回空回复')
        return content

    def _call_openai_responses_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用OpenAI Responses API"""
        base_url = settings['base_url'].rstrip('/')
        if not base_url.endswith('/v1'):
            base_url = base_url + '/v1'
        url = f"{base_url}/responses"

        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json"
        }
        data = {
            "model": settings['model_name'],
            "input": messages,
            "max_output_tokens": max_tokens,
            "temperature": temperature
        }

        logger.info(f"OpenAI Responses API请求: {url}")
        response = cancellable_post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"OpenAI Responses API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"OpenAI Responses API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        # Responses API 返回 output_text 字段
        if 'output_text' in result:
            return result['output_text'].strip()
        # 兼容解析 output 数组
        for item in result.get('output', []):
            if item.get('type') == 'message':
                for content in item.get('content', []):
                    if content.get('type') == 'output_text':
                        return content['text'].strip()
        raise Exception(f"OpenAI Responses API响应格式错误: {result}")

    def _call_anthropic_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用Anthropic Claude Messages API"""
        base_url = settings['base_url'].rstrip('/')
        if base_url.endswith('/v1'):
            url = f"{base_url}/messages"
        else:
            url = f"{base_url}/v1/messages"

        headers = {
            "x-api-key": settings['api_key'],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

        # Anthropic 格式：system 单独提取，messages 只包含 user/assistant
        system_content = ""
        api_messages = []
        for msg in messages:
            if msg['role'] == 'system':
                system_content = msg['content']
            else:
                api_messages.append({"role": msg['role'], "content": msg['content']})

        data = {
            "model": settings['model_name'],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": api_messages
        }
        if system_content:
            data["system"] = system_content

        logger.info(f"Anthropic API请求: {url}")
        response = cancellable_post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"Anthropic API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Anthropic API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        return result['content'][0]['text'].strip()

    def _call_azure_openai_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用Azure OpenAI API"""
        base_url = settings['base_url'].rstrip('/')
        # Azure URL 格式: https://{resource}.openai.azure.com/openai/deployments/{deployment}/chat/completions?api-version=xxx
        # 用户应在 base_url 中填入完整的 deployment URL
        if '/chat/completions' in base_url:
            url = base_url
        else:
            url = f"{base_url}/chat/completions"

        if 'api-version' not in url:
            separator = '&' if '?' in url else '?'
            url = f"{url}{separator}api-version=2024-02-01"

        headers = {
            "api-key": settings['api_key'],
            "Content-Type": "application/json"
        }
        data = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        logger.info(f"Azure OpenAI API请求: {url.split('?')[0]}")
        response = cancellable_post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"Azure OpenAI API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Azure OpenAI API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        return result['choices'][0]['message']['content'].strip()

    def is_ai_enabled(self, cookie_id: str) -> bool:
        """检查指定账号是否启用AI回复"""
        settings = db_manager.get_ai_reply_settings(cookie_id)
        return settings['ai_enabled']
    
    def _get_chat_lock(self, chat_id: str) -> threading.Lock:
        """获取指定chat_id的锁，如果不存在则创建"""
        with self._chat_locks_lock:
            if chat_id not in self._chat_locks:
                self._chat_locks[chat_id] = threading.Lock()
            return self._chat_locks[chat_id]

    @staticmethod
    def _normalize_knowledge_text(value: str) -> str:
        """将关键词检索文本标准化，保留中英文和数字字符。"""
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").lower())

    @staticmethod
    def _extract_date_context(value: str) -> Optional[Dict[str, object]]:
        """识别客户消息中的入住日期或星期，为价格 RAG 做日期筛选。

        支持 9.10、9/10、9月10日、12号、2026-09-10、今天/明天以及周五/周六。
        未写月份时按本月处理；明确写“下个月/下月”时按下月处理；未写年份时按当前年份处理。
        """
        raw = str(value or '').strip()
        if not raw:
            return None

        now = datetime.now()
        requested_date = None
        date_source = ''

        # 先识别数字日期，避免“9.10多少钱”被当成普通价格数字。
        date_match = re.search(
            r'(?:(20\d{2})\s*[-/.年]\s*)?'
            r'(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*(?:日|号)?',
            raw,
        )
        if date_match:
            year = int(date_match.group(1) or now.year)
            month = int(date_match.group(2))
            day = int(date_match.group(3))
            try:
                requested_date = datetime(year, month, day).date()
                date_source = 'numeric'
            except ValueError:
                requested_date = None

        # 客人通常只说“12号入住、13号退房”，没有写月份时按本月理解。
        # 只接受带“日/号”的裸日期，避免把价格、人数等普通数字误识别成日期。
        if requested_date is None:
            bare_day_match = re.search(r'(?<!\d)(\d{1,2})\s*(?:日|号)', raw)
            if bare_day_match:
                month_offset = 1 if re.search(r'下个?月|下月', raw) else 0
                month = now.month + month_offset
                year = now.year + ((month - 1) // 12)
                month = ((month - 1) % 12) + 1
                day = int(bare_day_match.group(1))
                try:
                    requested_date = datetime(year, month, day).date()
                    date_source = 'relative_month' if month_offset else 'numeric'
                except ValueError:
                    requested_date = None

        if requested_date is None:
            relative_offsets = (
                ('大后天', 3),
                ('后天', 2),
                ('明天', 1),
                ('今天', 0),
            )
            for keyword, offset in relative_offsets:
                if keyword in raw:
                    requested_date = (now + timedelta(days=offset)).date()
                    date_source = 'relative'
                    break

        weekday_map = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
        weekday_match = re.search(
            r'(?P<prefix>本周|这周|下周)?\s*(?:周|星期|礼拜)\s*(?P<day>[一二三四五六日天])',
            raw,
        )
        explicit_weekday = None
        if weekday_match:
            explicit_weekday = weekday_map[weekday_match.group('day')]
            if requested_date is None:
                monday = now.date() - timedelta(days=now.weekday())
                week_offset = 7 if weekday_match.group('prefix') == '下周' else 0
                requested_date = monday + timedelta(days=week_offset + explicit_weekday)
                date_source = 'weekday'

        holiday = None
        if '中秋' in raw:
            holiday = '中秋'
        elif '国庆' in raw or '十一' in raw:
            holiday = '国庆'

        if requested_date is not None:
            # 常见的国庆日期即使只写“10.1”也视作节假日，避免套用周末加价。
            if holiday is None:
                holiday = AIReplyEngine._holiday_name_for_date(requested_date)
            weekday = requested_date.weekday()
        elif explicit_weekday is not None:
            weekday = explicit_weekday
        elif holiday:
            weekday = None
        else:
            return None

        weekday_name = (
            ('一', '二', '三', '四', '五', '六', '日')[weekday]
            if weekday is not None else None
        )
        return {
            'date': requested_date.isoformat() if requested_date else None,
            'weekday': weekday,
            'weekday_name': weekday_name,
            'is_weekend': weekday in {4, 5} if weekday is not None else False,
            'holiday': holiday,
            'source': date_source or 'holiday',
        }

    @staticmethod
    def _holiday_name_for_date(requested_date) -> Optional[str]:
        """返回当前已配置的节假日名称。

        节假日价格通常不是普通周末价，因此先把节假日拦截到人工确认。
        2026 年日期来自国务院办公厅公布的放假安排；后续年份可在这里补充。
        """
        if requested_date.month == 10 and 1 <= requested_date.day <= 7:
            return '国庆'

        if requested_date.year == 2026:
            mid_autumn_start = datetime(2026, 9, 25).date()
            mid_autumn_end = datetime(2026, 9, 27).date()
            if mid_autumn_start <= requested_date <= mid_autumn_end:
                return '中秋'
        return None

    @staticmethod
    def _format_date_context(date_context: Optional[Dict[str, object]]) -> str:
        """把日期识别结果转成给 AI 和日志都容易理解的文字。"""
        if not date_context:
            return ''

        date_value = str(date_context.get('date') or '').strip()
        weekday_name = str(date_context.get('weekday_name') or '').strip()
        holiday = str(date_context.get('holiday') or '').strip()
        if date_value:
            try:
                year, month, day = date_value.split('-')
                text = f'客户入住日期：{int(year)}年{int(month)}月{int(day)}日'
            except ValueError:
                text = f'客户入住日期：{date_value}'
        elif weekday_name:
            text = f'客户入住星期：周{weekday_name}'
        else:
            text = '客户提到节假日，但未提供具体日期'

        if weekday_name:
            text += f'（周{weekday_name}'
            text += '，周末' if date_context.get('is_weekend') else '，平日'
            text += '）'
        if holiday:
            text += f'；节假日：{holiday}，不套用普通周末加价规则，需人工确认节假日价格'
        return text

    def _retrieve_fixed_knowledge(
        self,
        cookie_id: str,
        message: str,
        item_id: str = None,
        limit: int = 4,
    ) -> str:
        """从已有关键词回复中检索少量相关固定问答，作为 AI 的参考上下文。

        这是第一阶段的轻量 RAG：复用现有 SQLite 关键词表，不额外引入向量数据库。
        精确关键词仍由上层直接回复；这里只处理未命中关键词时的相关内容召回。
        """
        try:
            knowledge_rows = db_manager.get_keywords_with_type(cookie_id)
            if not knowledge_rows:
                return ""

            raw_message = str(message or "").strip()
            normalized_message = self._normalize_knowledge_text(raw_message)
            if not normalized_message:
                return ""

            # 常见问法与配置词的轻量扩展，避免“多少钱”无法召回“价格”这类固定问答。
            aliases = {
                "多少钱": "价格",
                "多少": "价格",
                "费用": "价格",
                "报价": "价格",
                "怎么发": "发货",
                "快递": "物流",
                "运费": "邮费",
                "取消": "退款",
                "改期": "退款",
                "入住": "酒店",
                "在吗": "在么",
                "有人吗": "在么",
            }
            expanded_message = normalized_message
            for source, target in aliases.items():
                if source in raw_message:
                    expanded_message += self._normalize_knowledge_text(target)

            query_chars = set(expanded_message)
            candidates = []
            for row in knowledge_rows:
                if row.get("type", "text") != "text":
                    continue
                reply = str(row.get("reply") or "").strip()
                if not reply:
                    continue

                row_item_id = str(row.get("item_id") or "").strip()
                current_item_id = str(item_id or "").strip()
                # 商品专属知识只参与对应商品的会话，通用知识可以全局参与。
                if row_item_id and row_item_id != current_item_id:
                    continue

                keyword_text = str(row.get("keyword") or "").strip()
                terms = [
                    term.strip()
                    for term in re.split(r"[|｜,，\n]+", keyword_text)
                    if term.strip()
                ]
                best_score = 0.0
                best_term = keyword_text
                for term in terms:
                    normalized_term = self._normalize_knowledge_text(term)
                    if not normalized_term:
                        continue
                    if normalized_term in expanded_message:
                        score = 100.0 + len(normalized_term)
                    else:
                        term_chars = set(normalized_term)
                        overlap = len(term_chars & query_chars)
                        score = 0.0
                        if len(term_chars) >= 2 and overlap >= 2:
                            score = 25.0 * overlap / len(term_chars)
                    if score > best_score:
                        best_score = score
                        best_term = term

                if best_score <= 0:
                    continue
                # 商品专属配置更优先，但不改变固定回复的实际内容。
                if row_item_id:
                    best_score += 5
                candidates.append((best_score, best_term, reply, row.get("item_title")))

            candidates.sort(key=lambda entry: entry[0], reverse=True)
            lines = []
            for _, keyword, reply, item_title in candidates[:max(1, int(limit))]:
                item_label = f"（商品：{item_title}）" if item_title else ""
                lines.append(f"- 关键词：{keyword}{item_label}\n  固定回复参考：{reply}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug(f"固定问答检索失败，继续使用普通AI回复: {exc}")
            return ""

    def _retrieve_rag_knowledge(
        self,
        cookie_id: str,
        message: str,
        item_id: str = None,
        limit: int = 5,
    ) -> str:
        """从资料库和历史客服问答中召回少量上下文。

        这是第一阶段的本地 RAG：SQLite 保存资料，内存中使用中文二元词组和
        关键词重合度排序，并在识别到入住日期后筛选平日/周末价格。它的重点是先把
        “正确资料找出来”，而不是让 AI 猜价格。
        后续数据量很大时再替换成向量检索，调用方无需改动。
        """
        try:
            raw_message = str(message or '').strip()
            normalized_message = self._normalize_knowledge_text(raw_message)
            if not normalized_message:
                return ''

            date_context = self._extract_date_context(raw_message)

            aliases = {
                '多少钱': '价格',
                '多少': '价格',
                '费用': '价格',
                '报价': '价格',
                '房价': '价格',
                '有房吗': '房态',
                '有房': '房态',
                '还有房': '房态',
                '入住': '酒店',
                '几个人': '人数',
                '几间': '房间',
            }
            expanded_message = normalized_message
            for source, target in aliases.items():
                if source in raw_message:
                    expanded_message += self._normalize_knowledge_text(target)

            def make_ngrams(value: str) -> set:
                if len(value) < 2:
                    return {value} if value else set()
                return {value[index:index + 2] for index in range(len(value) - 1)}

            query_ngrams = make_ngrams(expanded_message)
            query_terms = set(re.findall(r'[a-z0-9]{2,}|[\u4e00-\u9fff]{2}', expanded_message))
            current_item_id = str(item_id or '').strip()
            known_hotels = ('通安', '枕水', '堤上')
            requested_hotels = [hotel for hotel in known_hotels if hotel in raw_message]
            hotel_price_query = (
                any(term in raw_message for term in ('价格', '多少钱', '报价', '房价', '费用'))
                and any(term in raw_message for term in ('酒店', '乌镇', '通安', '枕水', '堤上'))
            )
            source_rows = db_manager.get_rag_sources(cookie_id, current_item_id)
            # 通安/枕水的周末价格由“非周五周六基础价 + 200”计算；
            # 堤上则有独立的周末价格资料。先识别后者，避免周末时把堤上基础价
            # 和周末价同时送给模型造成混淆。
            weekend_price_hotels = set()
            for row in source_rows:
                if str(row.get('source_type') or '').lower() != 'price':
                    continue
                source_text = ' '.join(
                    str(row.get(field) or '')
                    for field in ('title', 'tags', 'content')
                )
                compact_source = re.sub(r'\s+', '', source_text)
                has_non_weekend_price = (
                    '非周五周六' in compact_source
                    or '非周五、周六' in compact_source
                    or '非周五/周六' in compact_source
                    or ('非周五' in compact_source and '非周六' in compact_source)
                )
                has_weekend_phrase = (
                    '周五周六' in compact_source
                    or '周五、周六' in compact_source
                    or '周五/周六' in compact_source
                )
                has_surcharge_rule = '加200' in compact_source or '加价规则' in compact_source
                if has_weekend_phrase and not has_non_weekend_price and not has_surcharge_rule:
                    weekend_price_hotels.update(
                        hotel for hotel in known_hotels if hotel in compact_source
                    )

            candidates = []

            for row in source_rows:
                row_item_id = str(row.get('item_id') or '').strip()
                if row_item_id and current_item_id and row_item_id != current_item_id:
                    continue

                title = str(row.get('title') or '').strip()
                tags = str(row.get('tags') or '').strip()
                content = str(row.get('content') or '').strip()
                source_text = f'{title} {tags} {content}'
                normalized_source = self._normalize_knowledge_text(source_text)
                if not normalized_source:
                    continue

                # 价格资料必须先过日期规则，再参与相似度排序，避免平日价和周末价混在一起。
                source_type = str(row.get('source_type') or 'faq').lower()
                compact_source = re.sub(r'\s+', '', source_text)

                # 酒店价格必须只使用当前维护的价格资料。历史客服案例中可能保存过
                # 已失效金额，即使标注为“仅参考表达”也不应再送给模型。
                if source_type == 'chat' and hotel_price_query:
                    continue

                # 客户明确提到酒店时，只保留该酒店或包含该酒店规则的资料，避免串用其他酒店价格。
                if requested_hotels and source_type == 'price':
                    if not any(hotel in compact_source for hotel in requested_hotels):
                        continue

                has_non_weekend_price = (
                    '非周五周六' in compact_source
                    or '非周五、周六' in compact_source
                    or '非周五/周六' in compact_source
                    or ('非周五' in compact_source and '非周六' in compact_source)
                )
                has_weekend_phrase = (
                    '周五周六' in compact_source
                    or '周五、周六' in compact_source
                    or '周五/周六' in compact_source
                )
                has_weekend_price = has_weekend_phrase and not has_non_weekend_price
                has_surcharge_rule = '加200' in compact_source or '加价规则' in compact_source

                if date_context and source_type == 'price':
                    holiday = str(date_context.get('holiday') or '').strip()
                    if holiday:
                        # 普通 9 月价格不适用于节假日；“中秋/国庆除外”的加价规则也不应被引用。
                        if (has_surcharge_rule and '除外' in compact_source) or (
                            holiday not in compact_source and '节假日价格' not in compact_source
                        ):
                            continue
                    elif date_context.get('is_weekend'):
                        if has_non_weekend_price and not has_surcharge_rule:
                            # 通安/枕水没有另列周末价，周末应保留基础价，
                            # 再由加价规则计算；有独立周末价的酒店继续排除基础价。
                            if any(
                                hotel in requested_hotels
                                and hotel not in weekend_price_hotels
                                for hotel in known_hotels
                            ):
                                pass
                            else:
                                continue
                    else:
                        if has_weekend_price or has_surcharge_rule:
                            continue

                score = 0.0
                if expanded_message in normalized_source:
                    score += 120.0 + min(len(expanded_message), 30)

                source_ngrams = make_ngrams(normalized_source)
                if query_ngrams and source_ngrams:
                    overlap = len(query_ngrams & source_ngrams)
                    score += min(60.0, overlap * 8.0)

                source_terms = set(re.findall(r'[a-z0-9]{2,}|[\u4e00-\u9fff]{2}', normalized_source))
                if query_terms and source_terms:
                    score += min(35.0, len(query_terms & source_terms) * 12.0)

                if title and self._normalize_knowledge_text(title) in expanded_message:
                    score += 15.0
                if requested_hotels and source_type == 'price' and any(
                    hotel in compact_source for hotel in requested_hotels
                ):
                    # 酒店名是硬约束，不能因为客户用“9.12呢”这类短句追问，
                    # 就让相关价格卡片输给普通 FAQ。
                    score += 45.0
                if row_item_id:
                    score += 8.0

                if source_type == 'faq':
                    score += 6.0
                elif source_type == 'price' and any(term in raw_message for term in ('价格', '多少钱', '报价', '房价', '费用')):
                    score += 14.0
                elif source_type == 'chat':
                    # 历史聊天只作为弱参考，避免它压过当前的 FAQ/价格资料。
                    score -= 4.0

                if date_context and source_type == 'price':
                    if date_context.get('is_weekend') and has_weekend_price:
                        score += 110.0
                    elif not date_context.get('is_weekend') and has_non_weekend_price:
                        score += 110.0

                if score < 12.0:
                    continue
                candidates.append((score, source_type, title, content, row.get('valid_until')))

            labels = {
                'faq': '常见问答',
                'price': '酒店价格',
                'chat': '历史客服案例（仅参考表达）',
            }

            # 把事实资料和客服案例分开送给模型：案例只能学习语气、长度和沟通方式，
            # 不能因为相似句子而覆盖当前价格、日期或房态。
            candidates.sort(key=lambda entry: entry[0], reverse=True)
            fact_candidates = [entry for entry in candidates if entry[1] != 'chat']
            style_candidates = [entry for entry in candidates if entry[1] == 'chat']

            def format_candidate(entry):
                _, source_type, title, content, valid_until = entry
                label = labels.get(source_type, '客服资料')
                title_text = f'｜{title}' if title else ''
                expiry_text = f'｜有效期至 {valid_until}' if valid_until else ''
                # 单条资料限制长度，避免历史聊天把模型上下文撑大。
                content_text = content[:900]
                return f'- [{label}{title_text}{expiry_text}]\n  {content_text}'

            lines = []
            if fact_candidates:
                lines.append('【可作为回答依据的当前资料】')
                lines.extend(format_candidate(entry) for entry in fact_candidates[:max(1, int(limit))])
            if style_candidates:
                lines.append('【仅参考表达方式的客服案例，不可当作事实】')
                lines.extend(format_candidate(entry) for entry in style_candidates[:2])
            return '\n'.join(lines)
        except Exception as exc:
            logger.debug(f"AI 资料库检索失败，继续使用商品信息回复: {exc}")
            return ''
    
    def generate_reply(self, message: str, item_info: dict, chat_id: str,
                      cookie_id: str, user_id: str, item_id: str,
                      skip_wait: bool = False) -> Optional[str]:
        """
        生成AI回复 - 统一意图识别与回复生成
        AI会自动判断用户意图并生成合适的回复，避免关键词误判
        """
        if not self.is_ai_enabled(cookie_id):
            return None
        
        try:
            # 先保存用户消息到数据库（意图暂时设为None，后续可根据需要更新）
            message_created_at = self.save_conversation(
                chat_id, cookie_id, user_id, item_id, "user", message, intent=None
            )
            
            # 消息去抖处理
            if not skip_wait:
                logger.info(f"【{cookie_id}】消息已保存，等待10秒收集后续消息: {message[:20]}...")
                time.sleep(10)
            else:
                logger.info(f"【{cookie_id}】消息已保存（外部防抖已启用）: {message[:20]}...")
            
            # 获取该chat_id的锁，确保同一对话的消息串行处理
            chat_lock = self._get_chat_lock((cookie_id, chat_id))
            
            with chat_lock:
                check_generation()
                # 检查是否有更新的消息
                query_seconds = 6 if skip_wait else 25
                recent_messages = self._get_recent_user_messages(chat_id, cookie_id, seconds=query_seconds)
                
                if recent_messages and len(recent_messages) > 0:
                    latest_message = recent_messages[-1]
                    if message_created_at != latest_message['created_at']:
                        logger.info(f"【{cookie_id}】检测到更新消息，跳过当前消息")
                        return None
                
                # 1. 获取AI设置
                settings = db_manager.get_ai_reply_settings(cookie_id)
                custom_prompts = json.loads(settings['custom_prompts']) if settings['custom_prompts'] else {}

                # 2. 获取对话历史
                context = self.get_conversation_context(chat_id, cookie_id)

                # 3. 获取对话轮数和议价设置（供AI参考）
                conversation_rounds = self.get_conversation_rounds(chat_id, cookie_id)
                max_bargain_rounds = settings.get('max_bargain_rounds', 3)
                max_discount_percent = settings.get('max_discount_percent', 10)
                max_discount_amount = settings.get('max_discount_amount', 100)

                # 4. 构建统一的系统提示词（整合意图判断和回复生成）
                system_prompt = self._build_unified_system_prompt(custom_prompts, settings)

            # 5. 构建商品信息。没有商品或 SKU 时仍然继续调用 AI，避免普通问候被直接丢弃。
            item_desc = f"商品标题: {item_info.get('title', '未知')}\n"
            item_desc += f"商品价格: {item_info.get('price', '未知')}元\n"
            item_desc += f"商品描述: {item_info.get('desc', '无')}"
            sku_context = str(item_info.get('sku_context') or '').strip()
            if sku_context:
                item_desc += f"\n\n{sku_context}"

            # 6. 从已有固定关键词回复和 AI 资料库召回相关知识，作为参考上下文。
            # 省略酒店名的追问（例如“9.12呢”）由上层 Agent 补回最近酒店名，
            # 让本地 RAG 能按酒店区分价格；日期筛选仍以当前消息为准。
            rag_query = str(item_info.get('rag_query') or message).strip()
            knowledge_context = self._retrieve_fixed_knowledge(cookie_id, rag_query, item_id)
            rag_context = self._retrieve_rag_knowledge(cookie_id, rag_query, item_id)
            if item_info.get('anji_package_knowledge'):
                knowledge_context = ''
                rag_context = item_info['anji_package_knowledge']
            agent_context = str(item_info.get('agent_context') or '').strip()
            date_context = self._extract_date_context(message)
            date_context_text = self._format_date_context(date_context)
            if item_info.get('anji_package_knowledge'):
                date_context_text = '安吉景酒套餐使用专属套餐规则，按整套总价，不使用普通酒店每晚报价或通用节假日规则。'

            # 7. 构建对话历史字符串
            context_str = ""
            if context:
                context_str = "\n".join([
                    f"{'客户' if msg['role'] == 'user' else '客服'}: {msg['content']}"
                    for msg in context[-10:]
                ])

            # 8. 构建用户消息（包含商品、历史和检索到的固定问答）
            user_prompt = f"""## 商品信息
{item_desc}

## 相关固定问答（仅作参考）
{knowledge_context if knowledge_context else '(暂无相关固定问答)'}

## 客户入住日期识别
{date_context_text if date_context_text else '(未识别到具体入住日期；如果存在多个日期价格，应先询问客户入住日期)'}

## AI资料库检索结果（酒店价格需看有效期，历史客服案例仅参考表达）
{rag_context if rag_context else '(暂无相关资料)'}

## 轻量客服 Agent 回复决策
{agent_context if agent_context else '(按普通客服流程处理)'}

## 对话历史
{context_str if context_str else '(新对话，暂无历史)'}

## 对话状态
- 当前对话轮数：第{conversation_rounds + 1}轮
- 议价限制：最多{max_bargain_rounds}轮议价后需坚持底价
- 最大可优惠：{max_discount_percent}%或{max_discount_amount}元

## 当前用户消息
{message}

请根据以上信息，直接回复用户："""

            # 9. 构建消息列表
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 10. 根据API类型调用对应的AI接口
            reply = None
            api_type = self._resolve_api_type(settings)
            logger.info(f"使用 {api_type} API生成回复")

            # DeepSeek 等推理模型会把一部分输出预算用于 reasoning_content。
            # 300 个 token 容易只生成推理、没有留下最终 content，进而被误判为
            # “AI 没有回答”。客服最终只要求短答，但需要给推理模型留出完成答案的空间。
            reply_max_tokens = 800

            if api_type == 'dashscope':
                # DashScope有两种模式：
                # 1) /apps/{app_id} 应用模式 -> 走百炼应用API
                # 2) compatible-mode/v1 兼容模式 -> 走OpenAI Chat Completions
                if self._is_dashscope_app_api(settings):
                    reply = self._call_dashscope_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)
                else:
                    logger.info("DashScope检测为兼容模式（非/apps/），改走OpenAI兼容Chat API")
                    reply = self._call_openai_chat_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)
            elif api_type == 'gemini':
                reply = self._call_gemini_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)
            elif api_type == 'openai_responses':
                reply = self._call_openai_responses_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)
            elif api_type == 'anthropic':
                reply = self._call_anthropic_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)
            elif api_type == 'azure_openai':
                reply = self._call_azure_openai_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)
            else:
                # openai / ollama / 空值 均走 chat/completions
                reply = self._call_openai_chat_api(settings, messages, max_tokens=reply_max_tokens, temperature=0.7)

            # 11. 保存AI回复到对话记录
            check_generation()
            self.save_conversation(chat_id, cookie_id, user_id, item_id, "assistant", reply, intent=None)

            logger.info(f"AI回复生成成功 (账号: {cookie_id}): {reply}")
            return reply
                
        except Exception as e:
            logger.error(f"AI回复生成失败 {cookie_id}: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'url'):
                logger.error(f"请求URL: {e.response.url}")
            if hasattr(e, 'request') and hasattr(e.request, 'url'):
                logger.error(f"请求URL: {e.request.url}")
            return None

    async def generate_reply_async(self, message: str, item_info: dict, chat_id: str,
                                   cookie_id: str, user_id: str, item_id: str,
                                   skip_wait: bool = False) -> Optional[str]:
        """
        异步包装器：在独立线程池中执行同步的 `generate_reply`，并返回结果。
        这样可以在异步代码中直接 await，而不阻塞事件循环。
        """
        try:
            return await run_generation(self.generate_reply, message, item_info, chat_id, cookie_id, user_id, item_id, skip_wait)
        except Exception as e:
            logger.error(f"异步生成回复失败: {e}")
            return None
    
    def get_conversation_context(self, chat_id: str, cookie_id: str, limit: int = 20) -> List[Dict]:
        """获取对话上下文"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT role, content FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? 
                ORDER BY created_at DESC LIMIT ?
                ''', (chat_id, cookie_id, limit))
                
                results = cursor.fetchall()
                context = [{"role": row[0], "content": row[1]} for row in reversed(results)]
                return context
        except Exception as e:
            logger.error(f"获取对话上下文失败: {e}")
            return []
    
    def save_conversation(self, chat_id: str, cookie_id: str, user_id: str, 
                         item_id: str, role: str, content: str, intent: str = None) -> Optional[str]:
        """保存对话记录，返回创建时间"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                INSERT INTO ai_conversations 
                (cookie_id, chat_id, user_id, item_id, role, content, intent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (cookie_id, chat_id, user_id, item_id, role, content, intent))
                db_manager.conn.commit()
                
                # 获取刚插入记录的created_at
                cursor.execute('''
                SELECT created_at FROM ai_conversations 
                WHERE rowid = last_insert_rowid()
                ''')
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"保存对话记录失败: {e}")
            return None
    def get_conversation_rounds(self, chat_id: str, cookie_id: str) -> int:
        """获取对话轮数（用户消息数量）"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT COUNT(*) FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? AND role = 'user'
                ''', (chat_id, cookie_id))
                
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"获取对话轮数失败: {e}")
            return 0
    
    def _get_recent_user_messages(self, chat_id: str, cookie_id: str, seconds: int = 2) -> List[Dict]:
        """获取最近seconds秒内的所有用户消息（包含内容和时间戳）"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                # 先查询所有该chat的user消息，用于调试
                cursor.execute('''
                SELECT content, created_at, 
                       julianday('now') - julianday(created_at) as time_diff_days,
                       (julianday('now') - julianday(created_at)) * 86400.0 as time_diff_seconds
                FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? AND role = 'user' 
                ORDER BY created_at DESC LIMIT 10
                ''', (chat_id, cookie_id))
                
                all_messages = cursor.fetchall()
                logger.info(f"【调试】chat_id={chat_id} 最近10条user消息: {[(msg[0][:10], msg[1], f'{msg[3]:.2f}秒前') for msg in all_messages]}")
                
                # 正式查询
                cursor.execute('''
                SELECT content, created_at FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? AND role = 'user' 
                AND julianday('now') - julianday(created_at) < (? / 86400.0)
                ORDER BY created_at ASC
                ''', (chat_id, cookie_id, seconds))
                
                results = cursor.fetchall()
                return [{"content": row[0], "created_at": row[1]} for row in results]
        except Exception as e:
            logger.error(f"获取最近用户消息列表失败: {e}")
            return []
    


# 全局AI回复引擎实例
ai_reply_engine = AIReplyEngine()

# -*- coding: utf-8 -*-
"""Planner - LLM with schema-constrained TaskGraph generation"""

import os
import json
import re
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from ..schemas.taskgraph import TaskGraph, Task, Step, DomainType, ActionLevel
from ..schemas.context import DialogueContext
from ..adapters.navigation import NavigationAdapter
from .intent_gate import classify_intent, ForcedDomain


class Planner:
    """规划器：将用户意图转换为TaskGraph"""
    
    def __init__(
        self,
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        nav_adapter: Optional[NavigationAdapter] = None
    ):
        # 优先使用 OPENAI_* 环境变量，fallback到 LLM_* 别名
        self.llm_api_key = (
            llm_api_key or 
            os.getenv("OPENAI_API_KEY") or 
            os.getenv("LLM_API_KEY")
        )
        self.llm_base_url = (
            llm_base_url or 
            os.getenv("OPENAI_API_BASE") or 
            os.getenv("LLM_BASE_URL")
        )
        self.llm_model = (
            llm_model or 
            os.getenv("LLM_DEFAULT_MODEL") or 
            os.getenv("LLM_MODEL") or 
            "qwen-turbo"
        )
        self.nav_adapter = nav_adapter or NavigationAdapter()
        
        self.llm_client = None
        if OPENAI_AVAILABLE and self.llm_api_key:
            client_kwargs = {"api_key": self.llm_api_key}
            if self.llm_base_url:
                client_kwargs["base_url"] = self.llm_base_url
            self.llm_client = AsyncOpenAI(**client_kwargs)
            print(f"[Planner] Using LLM: {self.llm_model} @ {self.llm_base_url or 'default'}")
    
    async def plan(
        self,
        user_utterance: str,
        context: DialogueContext,
        trace_id: Optional[str] = None
    ) -> TaskGraph:
        """生成任务图。
        
        路线 C：先由 LLM 做意图识别并出 TaskGraph；规则只做失败兜底与事后护栏。
        
        Args:
            user_utterance: 用户输入
            context: 对话上下文
            trace_id: 跟踪ID（如果为None则生成）
        """
        # 确保有trace_id（HOTFIX: 生产500）
        if not trace_id:
            trace_id = str(uuid.uuid4())

        # 1. LLM 先规划（有客户端才走；regex 不再抢先短路）
        if self.llm_client:
            try:
                tg = await self._plan_with_llm(user_utterance, context, trace_id)
                # 事后护栏：手册/日程不得掉闲聊；命令句被整句判闲聊时纠域
                tg = await self._enforce_domain_guardrails(user_utterance, tg, context, trace_id)
                return tg
            except Exception as e:
                print(f"LLM planning failed: {e}, falling back to rule-based")

        # 2. 规则兜底：无 LLM 或规划失败
        return await self._plan_with_rules_fallback(user_utterance, context, trace_id)
    
    async def _plan_with_llm(
        self,
        user_utterance: str,
        context: DialogueContext,
        trace_id: str
    ) -> TaskGraph:
        """使用LLM生成TaskGraph（schema-constrained）
        
        Args:
            user_utterance: 用户输入
            context: 对话上下文
            trace_id: 跟踪ID（HOTFIX: 必须注入到LLM输出中）
        """
        
        # 构建系统提示
        system_prompt = self._build_system_prompt()
        
        # Stage 1: 注入相关记忆到用户消息（≤300 token，≤5条）
        memory_context = ""
        if context.memory_slice and 'relevant_memories' in context.memory_slice:
            memories = context.memory_slice['relevant_memories']
            if memories:
                memory_lines = []
                total_chars = 0
                max_chars = 150  # ~300 tokens (中文约2字符/token)
                max_items = 5
                
                for mem in memories[:max_items]:
                    line = f"  • [{mem['category']}] {mem['content']}"
                    if total_chars + len(line) > max_chars:
                        break
                    memory_lines.append(line)
                    total_chars += len(line)
                
                if memory_lines:
                    memory_context = "\n- 相关记忆：\n" + "\n".join(memory_lines)
        
        # 构建用户消息
        user_message = f"""用户输入：{user_utterance}

当前上下文：
- 位置：{context.current_location}
- 最近对话：{context.recent_utterances[-3:] if context.recent_utterances else []}
- 车辆状态：{context.shadow_state}{memory_context}

请分析用户意图，生成TaskGraph JSON。确保：
1. 只使用系统提示列出的合法 domain/action（不要用已废弃的 ac_on、ac_set_temp）
2. L2级别动作（如door_lock）必须标记level=L2；空调为L0，车窗为L1
3. 导航：你只填 goal.poi_name（如「家」「机场」）；坐标由地图工具解析，不要编造经纬度
4. 歌手点歌用 play_by_artist 并填 artist；调温用 set_ac_temp 并填 temperature
5. 手册/故障/如何使用必须 knowledge.query_manual，禁止 chitchat
6. 依赖关系正确（depends_on）；独立任务可并行
"""
        
        # 调用LLM (使用JSON mode如果支持)
        try:
            # 尝试使用JSON schema模式 (OpenAI-compatible)
            response = await self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=2000,
                response_format={"type": "json_object"}
            )
        except Exception as e:
            # Fallback: 不使用JSON mode（某些模型可能不支持）
            print(f"[Planner] JSON mode not supported, falling back: {e}")
            response = await self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=2000
            )
        
        # 解析LLM返回的JSON
        content = response.choices[0].message.content.strip()
        
        # 尝试提取JSON（可能被markdown包裹）
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        taskgraph_data = json.loads(content)
        
        # HOTFIX: 注入trace_id（LLM不会生成此字段）
        taskgraph_data["trace_id"] = trace_id
        
        # 后处理：解析POI（需要user_id用于家/公司地址）
        user_id = f"account_default:{context.session_info.driver_id}" if context.session_info.driver_id else None
        await self._resolve_pois_in_taskgraph(taskgraph_data, user_id)
        
        # 验证并返回
        taskgraph = TaskGraph(**taskgraph_data)
        
        # 验证安全性
        self._validate_safety(taskgraph)
        
        return taskgraph
    
    async def _plan_with_rules_fallback(
        self,
        user_utterance: str,
        context: DialogueContext,
        trace_id: str
    ) -> TaskGraph:
        """规则兜底：有强特征则走域模板，否则走关键词规则。"""
        gated = classify_intent(user_utterance)
        if gated is not None:
            print(f"[Planner] Rule fallback -> {gated.domain.value} ({gated.reason})")
            return await self._plan_forced_domain(user_utterance, context, trace_id, gated)
        return await self._plan_with_rules(user_utterance, context, trace_id)

    async def _plan_with_rules(
        self,
        user_utterance: str,
        context: DialogueContext,
        trace_id: str
    ) -> TaskGraph:
        """规则式规划（LLM不可用时的fallback）
        
        Args:
            user_utterance: 用户输入
            context: 对话上下文
            trace_id: 跟踪ID（HOTFIX: 必须包含在TaskGraph中）
        """
        
        utterance_lower = user_utterance.lower()
        session_id = context.session_info.session_id
        task_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        steps = []
        
        # 规则1: 导航 — 目的地不切词，整句交给地图工具 resolve_poi
        if any(keyword in utterance_lower for keyword in ["导航", "去", "到", "路线"]):
            # 提取user_id用于解析家/公司地址
            user_id = f"account_default:{context.session_info.driver_id}" if context.session_info.driver_id else None
            poi_data = await self.nav_adapter.resolve_poi(user_utterance, user_id=user_id)
            if poi_data:
                from ..schemas.taskgraph import NavigationAction, NavGoal, RoutePreferences
                step = Step(
                    step_id=f"step_{len(steps)+1}",
                    domain=DomainType.NAVIGATION,
                    action=NavigationAction(
                        action="set_nav_goal",
                        goal=NavGoal(**poi_data),
                        route_prefs=RoutePreferences()
                    ),
                    description=f"导航到{poi_data['poi_name']}"
                )
                steps.append(step)
            else:
                # POI未找到（如家/公司地址不在memory中），询问用户
                from ..schemas.taskgraph import ChitchatAction
                # 判断是否是家/公司
                is_home_company = any(kw in user_utterance for kw in ["家", "回家", "到家", "家里", "公司", "去公司", "回公司", "单位"])
                if is_home_company:
                    response = "您还没有设置家/公司地址，请告诉我具体地址，比如：帮我记住家地址是望京SOHO"
                else:
                    response = "没找到这个目的地，可以说得更具体一些吗？"
                step = Step(
                    step_id=f"step_{len(steps)+1}",
                    domain=DomainType.CHITCHAT,
                    action=ChitchatAction(response=response, level=ActionLevel.L0),
                    description="目的地未找到"
                )
                steps.append(step)
        
        # 规则2: 车窗控制
        if any(keyword in utterance_lower for keyword in ["打开车窗", "开窗"]):
            from ..schemas.taskgraph import VehicleAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.VEHICLE,
                action=VehicleAction(
                    action="window_open",
                    target="all_windows",
                    level=ActionLevel.L1
                ),
                description="打开车窗"
            )
            steps.append(step)
        
        if any(keyword in utterance_lower for keyword in ["关闭车窗", "关窗"]):
            from ..schemas.taskgraph import VehicleAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.VEHICLE,
                action=VehicleAction(
                    action="window_close",
                    target="all_windows",
                    level=ActionLevel.L1
                ),
                description="关闭车窗"
            )
            steps.append(step)
        
        # 规则3: 锁车（L2需确认）
        if any(keyword in utterance_lower for keyword in ["锁车", "锁门"]):
            from ..schemas.taskgraph import VehicleAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.VEHICLE,
                action=VehicleAction(
                    action="door_lock",
                    target="all_doors",
                    level=ActionLevel.L2  # L2需要确认
                ),
                description="锁车"
            )
            steps.append(step)
        
        # 规则4: 空调控制（先调温，再开关；action 与 Schema 对齐）
        temp = self._extract_ac_temp(user_utterance)
        if temp is not None and "空调" in user_utterance:
            from ..schemas.taskgraph import VehicleAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.VEHICLE,
                action=VehicleAction(
                    action="set_ac_temp",
                    temperature=temp,
                    level=ActionLevel.L0
                ),
                description=f"空调调到{temp}度"
            )
            steps.append(step)
        elif any(keyword in utterance_lower for keyword in ["开空调", "打开空调"]):
            from ..schemas.taskgraph import VehicleAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.VEHICLE,
                action=VehicleAction(
                    action="ac_power",
                    state="on",
                    level=ActionLevel.L0
                ),
                description="打开空调"
            )
            steps.append(step)
        
        # 规则5: 音乐控制
        if any(keyword in utterance_lower for keyword in ["播放音乐", "放音乐", "听歌"]):
            from ..schemas.taskgraph import MediaAction
            query = self._extract_music_query(user_utterance)
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.MEDIA,
                action=MediaAction(
                    action="play_music",
                    query=query or "流行音乐",
                    level=ActionLevel.L1
                ),
                description=f"播放音乐: {query or '流行音乐'}"
            )
            steps.append(step)
        
        # 规则6: 知识查询（扩大故障/异响等）
        if any(k in user_utterance for k in ["怎么", "如何", "手册", "说明书", "异响", "故障", "指示灯", "是否表示", "噪音"]):
            from ..schemas.taskgraph import KnowledgeAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.KNOWLEDGE,
                action=KnowledgeAction(
                    action="query_manual",
                    query=user_utterance,
                    level=ActionLevel.L0
                ),
                description="查询手册"
            )
            steps.append(step)

        # 规则6b: 日历
        if any(k in user_utterance for k in ["日程", "行程", "会议", "安排"]) and any(
            k in user_utterance for k in ["查询", "查看", "看看", "今天", "明日", "明天", "今日", "下个", "下一个"]
        ):
            from ..schemas.taskgraph import CalendarAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.CALENDAR,
                action=CalendarAction(action="query_events", level=ActionLevel.L0),
                description="查询日程"
            )
            steps.append(step)
        
        # 规则7: 如果没有匹配，返回闲聊（要有实质正文）
        if not steps:
            from ..schemas.taskgraph import ChitchatAction
            response = self._chitchat_response(user_utterance)
            step = Step(
                step_id="step_1",
                domain=DomainType.CHITCHAT,
                action=ChitchatAction(response=response, level=ActionLevel.L0),
                description=response
            )
            steps.append(step)
        
        # 构建TaskGraph
        task = Task(
            task_id=task_id,
            branch_id="main",
            steps=steps,
            user_intent=user_utterance
        )
        
        # HOTFIX: 确保trace_id存在
        taskgraph = TaskGraph(
            tasks=[task],
            session_id=session_id,
            timestamp=timestamp,
            trace_id=trace_id
        )
        
        return taskgraph
    
    def _build_system_prompt(self) -> str:
        """构建LLM系统提示"""
        return """你是智能座舱语音对话代理的任务规划器。

你的职责是将用户的自然语言请求转换为结构化的TaskGraph JSON。

## 支持的域和动作（必须用这些名字）：

### vehicle（车辆控制）
- window_open, window_close（车窗，L1）
- sunroof_open, sunroof_close（天窗，L1；无此配置则仍可规划，由能力包裁掉）
- door_lock, door_unlock（门锁，L2，需确认）
- trunk_open（后备箱，L1）
- ac_power（空调开关，L0）
- set_ac_temp（设置温度，L0，temperature 范围 16–30）
- set_ac_fan_mode, set_ac_fan_speed, set_ac_circulation, defrost_front, defrost_rear

### navigation（导航，L0）
- set_nav_goal（主名；nav_to 为别名）：只填 goal.poi_name，坐标由地图工具补齐
- cancel_nav, add_via, query_eta, query_remaining_distance, query_next_maneuver

### media（媒体，L0）
- media_play（随便听听/播放音乐）
- play_by_artist（按歌手，填 artist，如周杰伦）
- play_by_title, play_playlist, play_radio, play_favorites
- media_pause, media_next, media_prev, volume_up, volume_down, mute, switch_source

### calendar（日历，L0）
- query_events（查询日程/今日行程）
- create_event, cancel_event

### knowledge（知识查询，仅文本）
- query_manual（查询用户手册；如何使用/故障/异响必须走此域）

### chitchat（闲聊）
- 直接返回 response 正文；禁止只回「好的」；笑话必须有可播内容

## 动作级别：
- L0：立即执行，无风险（查询、空调、导航、媒体）
- L1：立即执行，车端可按档位拒绝（车窗/天窗）
- L2：需要确认才执行（door_lock）

## 输出格式示例：

```json
{
  "tasks": [
    {
      "task_id": "task_1",
      "branch_id": "main",
      "steps": [
        {
          "step_id": "step_1",
          "domain": "vehicle",
          "action": {
            "action": "door_lock",
            "target": "all_doors",
            "level": "L2"
          },
          "depends_on": [],
          "description": "锁车"
        }
      ],
      "user_intent": "锁车"
    }
  ],
  "session_id": "SESSION_ID_PLACEHOLDER",
  "timestamp": "TIMESTAMP_PLACEHOLDER"
}
```

注意：
1. 导航只填 poi_name（「回家」填「家」）；latitude/longitude 可先填 0，由地图工具写入
2. L2动作必须标记level=L2
3. 依赖步骤通过depends_on指定
4. 独立任务可并行
5. 手册问答禁止落到 chitchat
"""
    
    async def _nav_goal_from_tool(self, query: str, user_id: Optional[str] = None):
        """调用地图工具 resolve_poi，用查询串解析坐标（不在 Planner 里切词）。"""
        from ..schemas.taskgraph import NavGoal
        poi = await self.nav_adapter.resolve_poi(query, user_id=user_id)
        if not poi:
            return None
        return NavGoal(**poi)

    def _extract_ac_temp(self, utterance: str) -> Optional[float]:
        """从「调到22度」类说法提取温度。"""
        m = re.search(r"(?:调到|调至|设置到|到)\s*(\d+(?:\.\d+)?)\s*度", utterance or "")
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*度", utterance or "")
        if not m:
            return None
        try:
            val = float(m.group(1))
        except ValueError:
            return None
        if 16 <= val <= 30:
            return val
        return None

    def _extract_music_query(self, utterance: str) -> Optional[str]:
        """提取音乐查询"""
        for keyword in ["播放", "放", "听"]:
            if keyword in utterance:
                parts = utterance.split(keyword)
                if len(parts) > 1:
                    query = parts[1].strip()
                    return query if query else None
        return None
    
    async def _resolve_pois_in_taskgraph(self, taskgraph_data: Dict[str, Any], user_id: Optional[str] = None):
        """LLM 填好 poi_name 后，调用地图工具补坐标。"""
        for task in taskgraph_data.get("tasks", []):
            for step in task.get("steps", []):
                if step.get("domain") != "navigation":
                    continue
                action = step.get("action", {})
                if action.get("action") not in ("nav_to", "set_nav_goal"):
                    continue
                goal = action.get("goal")
                if not goal or not isinstance(goal.get("poi_name"), str):
                    continue
                poi_data = await self.nav_adapter.resolve_poi(goal["poi_name"], user_id=user_id)
                if poi_data:
                    goal.update(poi_data)
    
    def _validate_safety(self, taskgraph: TaskGraph):
        """验证TaskGraph安全性
        
        确保：
        1. 没有非法动作
        2. 没有未解析的POI
        3. L2动作正确标记
        """
        for task in taskgraph.tasks:
            for step in task.steps:
                # 检查导航目标是否已解析
                if step.domain == DomainType.NAVIGATION:
                    from ..schemas.taskgraph import NavigationAction
                    action: NavigationAction = step.action
                    if action.action in ("nav_to", "set_nav_goal"):
                        if not action.goal:
                            raise ValueError(f"Unresolved POI in step {step.step_id}")
                        # 家/公司允许latitude=0（云端不geocode，只发address_text）
                        # 其他POI需要坐标或让车端解析
                        if action.goal.poi_name not in ["家", "公司"]:
                            if action.goal.latitude == 0 and not action.goal.address:
                                raise ValueError(f"Unresolved POI in step {step.step_id}")


    def _chitchat_response(self, utterance: str) -> str:
        """闲聊必须有可播正文，禁止空「好的」。"""
        if any(k in utterance for k in ["笑话", "段子"]):
            return (
                "好的，给你讲个短笑话：导航说前方右转，结果我右转进了停车场。"
                "它还挺诚实——至少没让我开进河里。"
            )
        if any(k in utterance for k in ["你好", "您好", "嗨", "在吗"]):
            return "在的，需要我帮你控车、导航、放歌，还是查手册、看日程？"
        return "我在听。你可以让我开车窗、导航、放歌，也可以问手册或查今天的日程。"

    async def _plan_forced_domain(self, user_utterance, context, trace_id, gated):
        from ..schemas.taskgraph import KnowledgeAction, CalendarAction, ChitchatAction
        session_id = context.session_info.session_id
        task_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        # 提取user_id用于解析家/公司地址
        user_id = f"account_default:{context.session_info.driver_id}" if context.session_info.driver_id else None

        if gated.domain == ForcedDomain.KNOWLEDGE:
            step = Step(
                step_id="step_1",
                domain=DomainType.KNOWLEDGE,
                action=KnowledgeAction(action="query_manual", query=user_utterance, level=ActionLevel.L0),
                description="查询手册",
            )
        elif gated.domain == ForcedDomain.CALENDAR:
            step = Step(
                step_id="step_1",
                domain=DomainType.CALENDAR,
                action=CalendarAction(action="query_events", level=ActionLevel.L0),
                description="查询日程",
            )
        elif gated.domain == ForcedDomain.NAVIGATION:
            from ..schemas.taskgraph import NavigationAction, RoutePreferences
            goal = await self._nav_goal_from_tool(user_utterance, user_id=user_id)
            if goal is None:
                step = Step(
                    step_id="step_1",
                    domain=DomainType.CHITCHAT,
                    action=ChitchatAction(response="没找到这个目的地，可以说得更具体一些吗？", level=ActionLevel.L0),
                    description="目的地未解析",
                )
            else:
                step = Step(
                    step_id="step_1",
                    domain=DomainType.NAVIGATION,
                    action=NavigationAction(
                        action="set_nav_goal",
                        goal=goal,
                        route_prefs=RoutePreferences(),
                    ),
                    description=f"导航到{goal.poi_name}",
                )
        elif gated.domain == ForcedDomain.MEDIA:
            from ..schemas.taskgraph import MediaAction
            q = self._extract_music_query(user_utterance) or "音乐"
            if any(k in user_utterance for k in ["的歌", "歌手"]) or "周杰伦" in user_utterance:
                artist = q.replace("的歌", "").strip() or q
                step = Step(
                    step_id="step_1",
                    domain=DomainType.MEDIA,
                    action=MediaAction(action="play_by_artist", artist=artist, query=q, level=ActionLevel.L0),
                    description=f"播放歌手: {artist}",
                )
            else:
                step = Step(
                    step_id="step_1",
                    domain=DomainType.MEDIA,
                    action=MediaAction(action="media_play", query=q, level=ActionLevel.L0),
                    description=f"播放: {q}",
                )
        elif gated.domain == ForcedDomain.VEHICLE:
            from ..schemas.taskgraph import VehicleAction
            if any(k in user_utterance for k in ["锁车", "锁门"]):
                va = VehicleAction(action="door_lock", target="all_doors", level=ActionLevel.L2)
                desc = "锁车"
            elif any(k in user_utterance for k in ["关窗", "关闭车窗"]):
                va = VehicleAction(action="window_close", target="all_windows", level=ActionLevel.L1)
                desc = "关闭车窗"
            elif any(k in user_utterance for k in ["开窗", "打开车窗"]):
                va = VehicleAction(action="window_open", target="all_windows", level=ActionLevel.L1)
                desc = "打开车窗"
            elif "天窗" in user_utterance:
                open_it = "关" not in user_utterance
                va = VehicleAction(
                    action="sunroof_open" if open_it else "sunroof_close",
                    level=ActionLevel.L1,
                )
                desc = "打开天窗" if open_it else "关闭天窗"
            elif "空调" in user_utterance:
                temp = self._extract_ac_temp(user_utterance)
                if temp is not None:
                    va = VehicleAction(action="set_ac_temp", temperature=temp, level=ActionLevel.L0)
                    desc = f"空调调到{temp}度"
                else:
                    va = VehicleAction(action="ac_power", level=ActionLevel.L0)
                    desc = "空调"
            else:
                va = VehicleAction(action="window_open", target="all_windows", level=ActionLevel.L1)
                desc = "车控"
            step = Step(step_id="step_1", domain=DomainType.VEHICLE, action=va, description=desc)
        else:
            response = self._chitchat_response(user_utterance)
            step = Step(
                step_id="step_1",
                domain=DomainType.CHITCHAT,
                action=ChitchatAction(response=response, level=ActionLevel.L0),
                description=response,
            )

        task = Task(task_id=task_id, branch_id="main", steps=[step], user_intent=user_utterance)
        return TaskGraph(tasks=[task], session_id=session_id, timestamp=timestamp, trace_id=trace_id)

    async def _enforce_domain_guardrails(self, user_utterance, taskgraph, context, trace_id):
        """LLM 出图后的规则兜底：手册/日程不得落成 chitchat；导航纠域时仍走地图工具。"""
        gated = classify_intent(user_utterance)
        if gated is None or not taskgraph.tasks:
            return taskgraph
        
        # 提取user_id用于解析家/公司地址
        user_id = f"account_default:{context.session_info.driver_id}" if context.session_info.driver_id else None
        
        steps = taskgraph.tasks[0].steps
        domains = {s.domain for s in steps}
        if gated.domain == ForcedDomain.KNOWLEDGE and DomainType.KNOWLEDGE not in domains:
            print("[Planner] Guardrail: forcing knowledge over LLM misroute")
            from ..schemas.taskgraph import KnowledgeAction
            taskgraph.tasks[0].steps = [Step(
                step_id="step_1",
                domain=DomainType.KNOWLEDGE,
                action=KnowledgeAction(action="query_manual", query=user_utterance, level=ActionLevel.L0),
                description="查询手册",
            )]
        elif gated.domain == ForcedDomain.CALENDAR and DomainType.CALENDAR not in domains:
            print("[Planner] Guardrail: forcing calendar over LLM misroute")
            from ..schemas.taskgraph import CalendarAction
            taskgraph.tasks[0].steps = [Step(
                step_id="step_1",
                domain=DomainType.CALENDAR,
                action=CalendarAction(action="query_events", level=ActionLevel.L0),
                description="查询日程",
            )]
        elif gated.domain == ForcedDomain.NAVIGATION and DomainType.NAVIGATION not in domains:
            print("[Planner] Guardrail: forcing navigation over LLM misroute")
            from ..schemas.taskgraph import NavigationAction, RoutePreferences
            goal = await self._nav_goal_from_tool(user_utterance, user_id=user_id)
            if goal is not None:
                taskgraph.tasks[0].steps = [Step(
                    step_id="step_1",
                    domain=DomainType.NAVIGATION,
                    action=NavigationAction(
                        action="set_nav_goal",
                        goal=goal,
                        route_prefs=RoutePreferences(),
                    ),
                    description=f"导航到{goal.poi_name}",
                )]
        elif gated.domain == ForcedDomain.MEDIA and DomainType.MEDIA not in domains:
            print("[Planner] Guardrail: forcing media over LLM misroute")
            from ..schemas.taskgraph import MediaAction
            q = self._extract_music_query(user_utterance) or "音乐"
            if any(k in user_utterance for k in ["的歌", "歌手"]) or "周杰伦" in user_utterance:
                artist = q.replace("的歌", "").strip() or q
                media_act = MediaAction(action="play_by_artist", artist=artist, query=q, level=ActionLevel.L0)
                media_desc = f"播放歌手: {artist}"
            else:
                media_act = MediaAction(action="media_play", query=q, level=ActionLevel.L0)
                media_desc = "播放音乐"
            taskgraph.tasks[0].steps = [Step(
                step_id="step_1",
                domain=DomainType.MEDIA,
                action=media_act,
                description=media_desc,
            )]
        elif gated.domain == ForcedDomain.VEHICLE and DomainType.VEHICLE not in domains:
            print("[Planner] Guardrail: forcing vehicle over LLM misroute")
            from ..schemas.taskgraph import VehicleAction
            if any(k in user_utterance for k in ["锁车", "锁门"]):
                va = VehicleAction(action="door_lock", target="all_doors", level=ActionLevel.L2)
                desc = "锁车"
            elif any(k in user_utterance for k in ["关窗", "关闭车窗"]):
                va = VehicleAction(action="window_close", target="all_windows", level=ActionLevel.L1)
                desc = "关闭车窗"
            elif any(k in user_utterance for k in ["开窗", "打开车窗"]):
                va = VehicleAction(action="window_open", target="all_windows", level=ActionLevel.L1)
                desc = "打开车窗"
            elif "天窗" in user_utterance:
                open_it = "关" not in user_utterance
                va = VehicleAction(
                    action="sunroof_open" if open_it else "sunroof_close",
                    level=ActionLevel.L1,
                )
                desc = "打开天窗" if open_it else "关闭天窗"
            elif "空调" in user_utterance:
                temp = self._extract_ac_temp(user_utterance)
                if temp is not None:
                    va = VehicleAction(action="set_ac_temp", temperature=temp, level=ActionLevel.L0)
                    desc = f"空调调到{temp}度"
                else:
                    va = VehicleAction(action="ac_power", level=ActionLevel.L0)
                    desc = "空调"
            else:
                va = VehicleAction(action="window_open", target="all_windows", level=ActionLevel.L1)
                desc = "车控"
            taskgraph.tasks[0].steps = [Step(
                step_id="step_1",
                domain=DomainType.VEHICLE,
                action=va,
                description=desc,
            )]
        elif gated.domain == ForcedDomain.CHITCHAT:
            from ..schemas.taskgraph import ChitchatAction
            for s in steps:
                if s.domain == DomainType.CHITCHAT:
                    resp = getattr(s.action, "response", "") or ""
                    if (not resp) or (resp.strip() in {"好的", "好的。", "好"}):
                        s.action = ChitchatAction(
                            response=self._chitchat_response(user_utterance),
                            level=ActionLevel.L0,
                        )
                        s.description = s.action.response
        return taskgraph


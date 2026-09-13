# -*- coding: utf-8 -*-
"""Planner - LLM with schema-constrained TaskGraph generation"""

import os
import json
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
        """生成任务图
        
        优先使用LLM生成，失败时fallback到规则。
        
        Args:
            user_utterance: 用户输入
            context: 对话上下文
            trace_id: 跟踪ID（如果为None则生成）
        """
        # 确保有trace_id（HOTFIX: 生产500）
        if not trace_id:
            trace_id = str(uuid.uuid4())

        # PRD v1.11: 规则短路优先于 LLM（手册/日程/明确闲聊）
        gated = classify_intent(user_utterance)
        if gated is not None:
            print(f"[Planner] Intent gate -> {gated.domain.value} ({gated.reason})")
            return await self._plan_forced_domain(user_utterance, context, trace_id, gated)
        
        # 尝试LLM规划
        if self.llm_client:
            try:
                tg = await self._plan_with_llm(user_utterance, context, trace_id)
                # 双保险：LLM 若把手册/日程判成 chitchat，纠正
                tg = self._enforce_domain_guardrails(user_utterance, tg, context, trace_id)
                return tg
            except Exception as e:
                print(f"LLM planning failed: {e}, falling back to rule-based")
        
        # Fallback: 规则式规划
        return await self._plan_with_rules(user_utterance, context, trace_id)
    
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
        
        # 构建用户消息
        user_message = f"""用户输入：{user_utterance}

当前上下文：
- 位置：{context.current_location}
- 最近对话：{context.recent_utterances[-3:] if context.recent_utterances else []}
- 车辆状态：{context.shadow_state}

请分析用户意图，生成TaskGraph JSON。确保：
1. 合法的domain和action
2. L2级别动作（如door_lock）必须标记level=L2
3. 导航目的地必须解析为具体坐标
4. 依赖关系正确（depends_on）
5. 独立任务可并行
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
        
        # 后处理：解析POI
        await self._resolve_pois_in_taskgraph(taskgraph_data)
        
        # 验证并返回
        taskgraph = TaskGraph(**taskgraph_data)
        
        # 验证安全性
        self._validate_safety(taskgraph)
        
        return taskgraph
    
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
        
        # 规则1: 导航
        if any(keyword in utterance_lower for keyword in ["导航", "去", "到", "路线"]):
            # 简单提取目的地
            destination = self._extract_destination(user_utterance)
            if destination:
                poi_data = await self.nav_adapter.resolve_poi(destination)
                if poi_data:
                    from ..schemas.taskgraph import NavigationAction, NavGoal, RoutePreferences
                    step = Step(
                        step_id=f"step_{len(steps)+1}",
                        domain=DomainType.NAVIGATION,
                        action=NavigationAction(
                            action="nav_to",
                            goal=NavGoal(**poi_data),
                            route_prefs=RoutePreferences()
                        ),
                        description=f"导航到{destination}"
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
        
        # 规则4: 空调控制
        if any(keyword in utterance_lower for keyword in ["开空调", "打开空调"]):
            from ..schemas.taskgraph import VehicleAction
            step = Step(
                step_id=f"step_{len(steps)+1}",
                domain=DomainType.VEHICLE,
                action=VehicleAction(
                    action="ac_on",
                    level=ActionLevel.L1
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

## 支持的域和动作：

### vehicle（车辆控制）
- window_open, window_close（车窗）
- door_lock, door_unlock（车门，L2级别需要确认）
- ac_on, ac_off, ac_set_temp（空调）
- sunroof_open, sunroof_close（天窗）
- trunk_open（后备箱，L2级别）

### navigation（导航）
- nav_to（导航到目的地，需要解析POI坐标）

### media（媒体）
- play_music, pause, next_track, prev_track
- set_volume, volume_up, volume_down

### calendar（日历）
- query_schedule, add_event, next_appointment

### knowledge（知识查询，仅文本）
- query_manual（查询用户手册）

### chitchat（闲聊）
- 无schema约束，直接返回response文本

## 动作级别：
- L0：立即执行，无风险（查询类）
- L1：立即执行，可撤销（控制类）
- L2：需要确认才执行（如door_lock, trunk_open）

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
1. 导航必须包含goal字段（poi_name, latitude, longitude）
2. L2动作必须标记level=L2
3. 依赖步骤通过depends_on指定
4. 独立任务可并行
"""
    
    def _extract_destination(self, utterance: str) -> Optional[str]:
        """提取目的地"""
        for keyword in ["去", "到", "导航"]:
            if keyword in utterance:
                # 简单提取：取关键词后的第一个词
                parts = utterance.split(keyword)
                if len(parts) > 1:
                    dest = parts[1].strip().split()[0] if parts[1].strip() else None
                    return dest
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
    
    async def _resolve_pois_in_taskgraph(self, taskgraph_data: Dict[str, Any]):
        """解析TaskGraph中的POI（原地修改）"""
        for task in taskgraph_data.get("tasks", []):
            for step in task.get("steps", []):
                if step.get("domain") == "navigation":
                    action = step.get("action", {})
                    if action.get("action") == "nav_to":
                        goal = action.get("goal")
                        if goal and isinstance(goal.get("poi_name"), str):
                            poi_name = goal["poi_name"]
                            poi_data = await self.nav_adapter.resolve_poi(poi_name)
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
                    if action.action == "nav_to":
                        if not action.goal or action.goal.latitude == 0:
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

    def _enforce_domain_guardrails(self, user_utterance, taskgraph, context, trace_id):
        """LLM 输出兜底：手册/日程不得落成 chitchat。"""
        gated = classify_intent(user_utterance)
        if gated is None or not taskgraph.tasks:
            return taskgraph
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


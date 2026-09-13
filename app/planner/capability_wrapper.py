# -*- coding: utf-8 -*-
"""能力档案适配Wrapper for Planner"""

from typing import Optional
from ..schemas.taskgraph import TaskGraph, Task, Step, DomainType, ActionLevel, ChitchatAction
from ..schemas.context import DialogueContext
from ..capabilities import CapabilityProfile


class CapabilityAwarePlanner:
    """能力档案感知的规划器包装"""
    
    def __init__(self, base_planner, audit_logger=None):
        self.base_planner = base_planner
        self.audit_logger = audit_logger
    
    async def plan(
        self,
        user_utterance: str,
        context: DialogueContext,
        capability_profile: Optional[CapabilityProfile] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> TaskGraph:
        """规划任务图（带能力档案过滤）"""
        
        # 调用基础planner
        taskgraph = await self.base_planner.plan(user_utterance, context)
        
        # 如果有能力档案，验证和过滤
        if capability_profile:
            taskgraph = self._validate_against_profile(
                taskgraph, capability_profile, trace_id, session_id
            )
        
        return taskgraph
    
    def _validate_against_profile(
        self,
        taskgraph: TaskGraph,
        profile: CapabilityProfile,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> TaskGraph:
        """验证TaskGraph是否符合能力档案"""
        validated_tasks = []
        unsupported_actions = []
        
        for task in taskgraph.tasks:
            validated_steps = []
            for step in task.steps:
                action_name = self._extract_action_name(step)
                
                if action_name and not profile.is_action_supported(action_name):
                    print(f"[CapabilityWrapper] Action '{action_name}' not supported by {profile.model_id}")
                    unsupported_actions.append(action_name)
                    
                    # Emit audit event: unsupported (merge-blocking fix #3)
                    if self.audit_logger and trace_id and session_id:
                        from ..audit import AuditEventType
                        self.audit_logger.create_event(
                            trace_id=trace_id,
                            session_id=session_id,
                            event_type=AuditEventType.UNSUPPORTED,
                            domain=step.domain.value if hasattr(step.domain, 'value') else str(step.domain),
                            action=action_name,
                            reason=f"Not supported by {profile.model_name}",
                            metadata={"model_id": profile.model_id}
                        )
                else:
                    validated_steps.append(step)
            
            if validated_steps:
                task.steps = validated_steps
                validated_tasks.append(task)
        
        # 如果有不支持的动作，返回TTS提示
        if unsupported_actions:
            unsupported_str = "、".join(set(unsupported_actions))
            return self._create_unsupported_response(unsupported_str, profile, taskgraph.session_id, taskgraph.trace_id)
        
        taskgraph.tasks = validated_tasks
        return taskgraph
    
    def _extract_action_name(self, step: Step) -> Optional[str]:
        """提取步骤中的action名称"""
        action = step.action
        if hasattr(action, 'action'):
            return action.action
        return None
    
    def _create_unsupported_response(
        self,
        actions: str,
        profile: CapabilityProfile,
        session_id: str = "",
        trace_id: str = ""
    ) -> TaskGraph:
        """创建不支持动作的TTS响应"""
        from datetime import datetime
        
        return TaskGraph(
            tasks=[
                Task(
                    task_id="t_unsupported",
                    branch_id="main",
                    steps=[
                        Step(
                            step_id="s_unsupported",
                            domain=DomainType.CHITCHAT,
                            action=ChitchatAction(
                                response=f"抱歉，您的车辆（{profile.model_name}）不支持该功能：{actions}",
                                level=ActionLevel.L0
                            ),
                            description="不支持的动作提示"
                        )
                    ],
                    user_intent="unsupported action"
                )
            ],
            session_id=session_id,
            trace_id=trace_id,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )

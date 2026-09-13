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
        """规划任务图（带能力档案过滤）
        
        Args:
            user_utterance: 用户输入
            context: 对话上下文
            capability_profile: 能力档案（可选）
            trace_id: 跟踪ID（HOTFIX: 必须传递给base_planner）
            session_id: 会话ID
        """
        
        # HOTFIX: 调用基础planner，传递trace_id
        taskgraph = await self.base_planner.plan(user_utterance, context, trace_id=trace_id)
        
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
        """验证TaskGraph是否符合能力档案（P0 fix #3: keep what works）"""
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
                    # Keep supported steps (P0 fix #3: 能做的先做)
                    validated_steps.append(step)
            
            # Keep task if it has any validated steps
            if validated_steps:
                task.steps = validated_steps
                validated_tasks.append(task)
        
        # P0 fix #3: If there are unsupported actions, ADD an unsupported TTS step
        # but KEEP all validated_tasks (mixed utterance: keep what works)
        if unsupported_actions:
            unsupported_str = "、".join(set(unsupported_actions))
            unsupported_tts_step = self._create_unsupported_tts_step(unsupported_str, profile)
            
            # Add unsupported notice as a separate chitchat step
            if validated_tasks:
                # Append to first task
                validated_tasks[0].steps.append(unsupported_tts_step)
            else:
                # No valid steps - create a new task with just the TTS
                from datetime import datetime
                validated_tasks = [
                    Task(
                        task_id="t_unsupported",
                        branch_id="main",
                        steps=[unsupported_tts_step],
                        user_intent="unsupported action"
                    )
                ]
        
        taskgraph.tasks = validated_tasks
        return taskgraph
    
    def _extract_action_name(self, step: Step) -> Optional[str]:
        """提取步骤中的action名称"""
        action = step.action
        if hasattr(action, 'action'):
            return action.action
        return None
    
    def _create_unsupported_tts_step(
        self,
        actions: str,
        profile: CapabilityProfile
    ) -> Step:
        """创建不支持动作的TTS步骤（P0 fix #3）"""
        return Step(
            step_id="s_unsupported",
            domain=DomainType.CHITCHAT,
            action=ChitchatAction(
                response=f"抱歉，您的车辆（{profile.model_name}）不支持该功能：{actions}",
                level=ActionLevel.L0
            ),
            description="不支持的动作提示"
        )

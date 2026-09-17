# -*- coding: utf-8 -*-
"""Orchestrator - custom state machine for task execution"""

import asyncio
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field

from ..schemas.taskgraph import TaskGraph, Task, Step, ActionLevel, DomainType
from ..schemas.writeback import WritebackEnvelope, WritebackEvent, WritebackStatus
from ..adapters import (
    VehicleAdapter,
    NavigationAdapter,
    MediaAdapter,
    CalendarAdapter,
    KnowledgeAdapter,
    ChitchatAdapter,
)
from .idempotency import IdempotencyManager
from .checkpoint import CheckpointManager


class StepStatus(str, Enum):
    """步骤状态"""
    PENDING = "pending"
    READY = "ready"
    EXECUTING = "executing"
    WAITING_CONFIRM = "waiting_confirm"  # L2等待确认
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFIRM_TIMEOUT = "confirm_timeout"


@dataclass
class StepState:
    """步骤状态"""
    step: Step
    status: StepStatus = StepStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    confirm_deadline: Optional[datetime] = None  # L2确认截止时间
    retry_count: int = 0


@dataclass
class TaskState:
    """任务状态"""
    task: Task
    step_states: Dict[str, StepState] = field(default_factory=dict)
    completed_steps: Set[str] = field(default_factory=set)
    failed_steps: Set[str] = field(default_factory=set)


class Orchestrator:
    """编排器：执行TaskGraph"""
    
    # L2确认超时时间（秒）
    L2_CONFIRM_TIMEOUT = 15
    
    def __init__(
        self,
        vehicle_adapter: VehicleAdapter,
        nav_adapter: NavigationAdapter,
        media_adapter: MediaAdapter,
        calendar_adapter: CalendarAdapter,
        knowledge_adapter: KnowledgeAdapter,
        chitchat_adapter: ChitchatAdapter,
        shadow_state: Optional[Dict[str, Any]] = None,
        audit_logger=None,
        redis_client=None,
        pg_store=None
    ):
        self.adapters = {
            DomainType.VEHICLE: vehicle_adapter,
            DomainType.NAVIGATION: nav_adapter,
            DomainType.MEDIA: media_adapter,
            DomainType.CALENDAR: calendar_adapter,
            DomainType.KNOWLEDGE: knowledge_adapter,
            DomainType.CHITCHAT: chitchat_adapter,
        }
        
        self.shadow_state = shadow_state or {}
        self.task_states: Dict[str, TaskState] = {}
        
        # 待下行的TaskGraph（仅orchestrator验证后的版本）
        self.pending_downlink: Optional[TaskGraph] = None
        
        # 当前trace_id（用于writeback）
        self.current_trace_id: Optional[str] = None
        
        # L2确认后待发布的步骤（P0 fix #1）
        self.pending_l2_publish: Optional[TaskGraph] = None
        self.mqtt_publish_callback: Optional[callable] = None
        
        # Audit logger for event tracking (P0 exit #5)
        self.audit_logger = audit_logger
        self.current_session_id: Optional[str] = None
        self.capability_profile = None  # set by main before execute
        self.item_names = None  # RAG 车型显示名，如 ["致享"]
        
        # PRD v1.37 Feature 1: 幂等性管理器
        self.idempotency_manager = IdempotencyManager(redis_client) if redis_client else None
        
        # PRD v1.37 Feature 2: 检查点管理器
        self.checkpoint_manager = CheckpointManager(redis_client, pg_store) if redis_client else None
    
    async def execute_taskgraph(
        self,
        taskgraph: TaskGraph,
        writeback_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """执行TaskGraph
        
        Args:
            taskgraph: 任务图
            writeback_callback: 写回回调函数（接收WritebackEnvelope）
            
        Returns:
            执行结果摘要
        """
        # Store trace_id and session_id for writeback and audit generation
        self.current_trace_id = taskgraph.trace_id
        self.current_session_id = taskgraph.session_id
        
        # 验证TaskGraph
        await self._validate_taskgraph(taskgraph)
        
        # 初始化任务状态
        for task in taskgraph.tasks:
            task_state = TaskState(task=task)
            for step in task.steps:
                task_state.step_states[step.step_id] = StepState(step=step)
            self.task_states[task.task_id] = task_state
        
        # 设置待下行
        self.pending_downlink = taskgraph
        
        # 执行所有任务（支持并行）
        execution_tasks = []
        for task in taskgraph.tasks:
            execution_tasks.append(
                self._execute_task(task, writeback_callback)
            )
        
        results = await asyncio.gather(*execution_tasks, return_exceptions=True)
        
        # 汇总结果
        summary = {
            "taskgraph_id": taskgraph.session_id,
            "timestamp": taskgraph.timestamp,
            "task_results": {}
        }
        
        for task, result in zip(taskgraph.tasks, results):
            if isinstance(result, Exception):
                summary["task_results"][task.task_id] = {
                    "status": "error",
                    "error": str(result)
                }
            else:
                summary["task_results"][task.task_id] = result
        
        return summary
    
    async def _validate_taskgraph(self, taskgraph: TaskGraph):
        """验证TaskGraph（确保没有非法动作、未解析POI等）"""
        for task in taskgraph.tasks:
            for step in task.steps:
                adapter = self.adapters.get(step.domain)
                if not adapter:
                    raise ValueError(f"Unknown domain: {step.domain}")
                
                # 验证步骤
                is_valid, error = await adapter.validate(step, self.shadow_state)
                if not is_valid:
                    raise ValueError(f"Step {step.step_id} validation failed: {error}")
    
    async def _execute_task(
        self,
        task: Task,
        writeback_callback: Optional[callable]
    ) -> Dict[str, Any]:
        """执行单个任务"""
        task_state = self.task_states[task.task_id]
        
        # 持续执行直到所有步骤完成或失败
        while True:
            # 找出ready的步骤（依赖已满足）
            ready_steps = self._get_ready_steps(task_state)
            
            if not ready_steps:
                # 检查是否所有步骤都已完成或失败
                all_done = all(
                    state.status in [StepStatus.COMPLETED, StepStatus.FAILED, 
                                     StepStatus.CANCELLED, StepStatus.CONFIRM_TIMEOUT]
                    for state in task_state.step_states.values()
                )
                if all_done:
                    break
                
                # 检查是否有L2在等待确认
                waiting_l2 = [
                    state for state in task_state.step_states.values()
                    if state.status == StepStatus.WAITING_CONFIRM
                ]
                
                if waiting_l2:
                    # 等待L2确认或超时
                    await asyncio.sleep(0.5)
                    
                    # 检查超时
                    now = datetime.utcnow()
                    for state in waiting_l2:
                        if state.confirm_deadline and now > state.confirm_deadline:
                            # 确认超时 - 不重新请求Planner
                            state.status = StepStatus.CONFIRM_TIMEOUT
                            state.error = "L2 confirmation timeout - cancelled"
                            task_state.failed_steps.add(state.step.step_id)
                            
                            # 发送confirm_timeout写回
                            if writeback_callback:
                                writeback = WritebackEnvelope(
                                    task_id=task.task_id,
                                    step_id=state.step.step_id,
                                    branch_id=task.branch_id,
                                    trace_id=self.current_trace_id,
                                    event=WritebackEvent.CONFIRM_RESULT,
                                    status=WritebackStatus.TIMEOUT,
                                    reason="L2 confirmation timeout - operation cancelled",
                                    ts=datetime.utcnow().isoformat() + "Z"
                                )
                                await writeback_callback(writeback)
                            
                            print(f"[Orchestrator] L2 timeout {state.step.step_id} - CANCELLED, no re-ask")
                    
                    continue
                else:
                    # 没有ready步骤，也没有L2等待，可能死锁
                    break
            
            # 并行执行ready步骤（非L2等待确认的）
            execution_tasks = []
            for step_state in ready_steps:
                if step_state.status == StepStatus.WAITING_CONFIRM:
                    # L2等待确认，不执行
                    continue
                
                step_state.status = StepStatus.EXECUTING
                execution_tasks.append(
                    self._execute_step(task, step_state, writeback_callback)
                )
            
            if execution_tasks:
                await asyncio.gather(*execution_tasks, return_exceptions=True)
            else:
                # 所有ready步骤都在等待确认
                await asyncio.sleep(0.5)
        
        # 任务完成
        return {
            "task_id": task.task_id,
            "completed_steps": list(task_state.completed_steps),
            "failed_steps": list(task_state.failed_steps),
            "status": "completed" if not task_state.failed_steps else "partial"
        }
    
    def _get_ready_steps(self, task_state: TaskState) -> List[StepState]:
        """获取ready的步骤（依赖已满足）"""
        ready = []
        for step_state in task_state.step_states.values():
            if step_state.status not in [StepStatus.PENDING, StepStatus.READY, StepStatus.WAITING_CONFIRM]:
                continue
            
            # 检查依赖
            deps_satisfied = all(
                dep_id in task_state.completed_steps
                for dep_id in step_state.step.depends_on
            )
            
            if deps_satisfied:
                if step_state.status == StepStatus.PENDING:
                    step_state.status = StepStatus.READY
                ready.append(step_state)
        
        return ready
    
    async def _execute_step(
        self,
        task: Task,
        step_state: StepState,
        writeback_callback: Optional[callable]
    ):
        """执行单个步骤"""
        step = step_state.step
        adapter = self.adapters[step.domain]
        
        try:
            # PRD v1.37 Feature 1: 生成tool_use_id并检查幂等性
            tool_use_id = None
            if self.idempotency_manager and self.current_trace_id:
                tool_use_id = self.idempotency_manager.generate_tool_use_id(
                    self.current_trace_id,
                    step.step_id
                )
                
                # 检查是否已执行过（幂等性）
                cached_result = self.idempotency_manager.get_cached_result(tool_use_id)
                if cached_result:
                    print(f"[Orchestrator] 幂等性命中: {step.step_id} (tool_use_id={tool_use_id})")
                    # 使用缓存结果，不重新执行
                    step_state.status = StepStatus(cached_result["status"])
                    step_state.result = cached_result.get("result")
                    if step_state.status == StepStatus.COMPLETED:
                        self.task_states[task.task_id].completed_steps.add(step.step_id)
                    return
            
            # 检查动作级别
            action_level = getattr(step.action, "level", ActionLevel.L0)
            
            if action_level == ActionLevel.L2:
                # L2需要确认，不执行，不下行
                step_state.status = StepStatus.WAITING_CONFIRM
                step_state.confirm_deadline = datetime.utcnow() + timedelta(
                    seconds=self.L2_CONFIRM_TIMEOUT
                )
                
                # 发送确认请求writeback（不是执行）
                if writeback_callback:
                    from ..schemas import WritebackEnvelope, WritebackEvent, WritebackStatus
                    writeback = WritebackEnvelope(
                        task_id=task.task_id,
                        step_id=step.step_id,
                        branch_id=task.branch_id,
                        trace_id=self.current_trace_id,
                        event=WritebackEvent.CONFIRM_RESULT,
                        status=WritebackStatus.PENDING,
                        reason=f"Waiting for user confirmation: {step.description or getattr(step.action, 'action', 'L2 action')}",
                        ts=datetime.utcnow().isoformat() + "Z"
                    )
                    await writeback_callback(writeback)
                
                # Emit audit event: confirm_request (P0 exit #5)
                if self.audit_logger and self.current_trace_id and self.current_session_id:
                    from ..audit import AuditEventType
                    self.audit_logger.create_event(
                        trace_id=self.current_trace_id,
                        session_id=self.current_session_id,
                        event_type=AuditEventType.CONFIRM_REQUEST,
                        task_id=task.task_id,
                        step_id=step.step_id,
                        branch_id=task.branch_id,
                        action=getattr(step.action, 'action', 'L2 action'),
                        metadata={"timeout_seconds": self.L2_CONFIRM_TIMEOUT}
                    )
                
                print(f"[Orchestrator] L2 action {step.step_id} waiting for confirmation (NOT executing)")
                return
            
            # L0/L1立即执行
            context = {
                "shadow_state": self.shadow_state,
                "task_id": task.task_id,
                "branch_id": task.branch_id,
                "trace_id": self.current_trace_id,  # P0 exit #5: pass trace_id to adapters
                "session_id": self.current_session_id,  # P0 exit #5: pass session_id to adapters
                "capability_profile": getattr(self, "capability_profile", None),
                "item_names": getattr(self, "item_names", None),
            }
            
            result = await adapter.execute(step, context)
            
            # 特殊处理：chitchat立即完成（正文已在 TaskGraph.action.response）
            if step.domain == DomainType.CHITCHAT:
                step_state.status = StepStatus.COMPLETED
                step_state.result = result
                self.task_states[task.task_id].completed_steps.add(step.step_id)
                
                # PRD v1.37 Feature 1: 缓存结果（幂等性）
                if self.idempotency_manager and tool_use_id:
                    self.idempotency_manager.cache_result(tool_use_id, {
                        "status": step_state.status.value,
                        "result": result
                    })
                if writeback_callback:
                    writeback = WritebackEnvelope(
                        task_id=task.task_id,
                        step_id=step.step_id,
                        branch_id=task.branch_id,
                        trace_id=self.current_trace_id or "",
                        event=WritebackEvent.KNOWLEDGE_DONE,
                        status=WritebackStatus.ACCEPTED,
                        reason=result.get("response") or getattr(step.action, "response", None),
                        ts=datetime.utcnow().isoformat() + "Z"
                    )
                    await writeback_callback(writeback)
                return
            
            # knowledge / calendar：本地完成并推送可播文本；默认 MQTT 零帧
            if step.domain in (DomainType.KNOWLEDGE, DomainType.CALENDAR):
                step_state.status = StepStatus.COMPLETED
                step_state.result = result
                self.task_states[task.task_id].completed_steps.add(step.step_id)
                
                # PRD v1.37 Feature 1: 缓存结果（幂等性）
                if self.idempotency_manager and tool_use_id:
                    self.idempotency_manager.cache_result(tool_use_id, {
                        "status": step_state.status.value,
                        "result": result
                    })
                if writeback_callback:
                    spoken = (
                        result.get("answer")
                        or result.get("response")
                        or result.get("error")
                        or ""
                    )
                    event = (
                        WritebackEvent.KNOWLEDGE_DONE
                        if step.domain == DomainType.KNOWLEDGE
                        else WritebackEvent.CALENDAR_ACK
                    )
                    status = WritebackStatus.ACCEPTED
                    if result.get("status") in ("not_found", "failed"):
                        status = WritebackStatus.FAILED
                    writeback = WritebackEnvelope(
                        task_id=task.task_id,
                        step_id=step.step_id,
                        branch_id=task.branch_id,
                        trace_id=self.current_trace_id or "",
                        event=event,
                        status=status,
                        reason=spoken,
                        ts=datetime.utcnow().isoformat() + "Z"
                    )
                    await writeback_callback(writeback)
                return
            
            # 其他域：等待车辆ack
            step_state.status = StepStatus.COMPLETED  # 简化：假设立即成功
            step_state.result = result
            self.task_states[task.task_id].completed_steps.add(step.step_id)
            
            # PRD v1.37 Feature 1: 缓存结果（幂等性）
            if self.idempotency_manager and tool_use_id:
                self.idempotency_manager.cache_result(tool_use_id, {
                    "status": step_state.status.value,
                    "result": result
                })
            
            # PRD v1.37 Feature 2: 保存检查点
            if self.checkpoint_manager and self.current_trace_id and self.current_session_id:
                self._save_checkpoint()
            
        except Exception as e:
            step_state.status = StepStatus.FAILED
            step_state.error = str(e)
            self.task_states[task.task_id].failed_steps.add(step.step_id)
            
            # PRD v1.37 Feature 1: 缓存错误结果（幂等性）
            if self.idempotency_manager and tool_use_id:
                self.idempotency_manager.cache_result(tool_use_id, {
                    "status": step_state.status.value,
                    "error": str(e)
                })
    
    async def handle_writeback(self, writeback: WritebackEnvelope):
        """处理车辆写回"""
        task_state = self.task_states.get(writeback.task_id)
        if not task_state:
            print(f"[Orchestrator] Unknown task_id: {writeback.task_id}")
            return
        
        step_state = task_state.step_states.get(writeback.step_id)
        if not step_state:
            print(f"[Orchestrator] Unknown step_id: {writeback.step_id}")
            return
        
        # 处理确认结果
        if writeback.event == WritebackEvent.CONFIRM_RESULT:
            if writeback.status == WritebackStatus.ACCEPTED:
                # 用户接受，发布L2到vehicle执行（P0 fix #1: L2 after confirm → vehicle_ack）
                print(f"[Orchestrator] L2 confirmed, publishing for vehicle execution: {writeback.step_id}")
                step_state.status = StepStatus.EXECUTING
                
                # Emit audit event: confirm_accepted (P0 exit #5)
                if self.audit_logger and writeback.trace_id:
                    from ..audit import AuditEventType
                    self.audit_logger.create_event(
                        trace_id=writeback.trace_id,
                        session_id=self.current_session_id or "",
                        event_type=AuditEventType.CONFIRM_ACCEPTED,
                        task_id=writeback.task_id,
                        step_id=writeback.step_id,
                        branch_id=writeback.branch_id
                    )
                
                # 发布confirmed L2 step到MQTT（单独发布该步骤）
                await self._publish_confirmed_l2_step(
                    task_state.task,
                    step_state.step,
                    writeback.task_id,
                    writeback.branch_id
                )
                
                # 现在等待vehicle_ack而不是立即标记completed
                # vehicle_ack会在后续writeback中将step标记为completed
            else:
                # 用户拒绝或超时
                step_state.status = StepStatus.CANCELLED
                step_state.error = writeback.reason or "User declined"
                task_state.failed_steps.add(writeback.step_id)
                
                # Emit audit event: confirm_declined or confirm_timeout (P0 exit #5)
                if self.audit_logger and writeback.trace_id:
                    from ..audit import AuditEventType
                    event_type = (AuditEventType.CONFIRM_TIMEOUT if writeback.status == WritebackStatus.TIMEOUT
                                  else AuditEventType.CONFIRM_DECLINED)
                    self.audit_logger.create_event(
                        trace_id=writeback.trace_id,
                        session_id=self.current_session_id or "",
                        event_type=event_type,
                        task_id=writeback.task_id,
                        step_id=writeback.step_id,
                        branch_id=writeback.branch_id,
                        reason=writeback.reason
                    )
        
        # 处理其他事件 (unified status: accepted/rejected/failed)
        elif writeback.event == WritebackEvent.VEHICLE_ACK:
            if writeback.status == WritebackStatus.ACCEPTED:
                step_state.status = StepStatus.COMPLETED
                task_state.completed_steps.add(writeback.step_id)
                
                # Emit audit event: vehicle_ack (P0 exit #5)
                if self.audit_logger and writeback.trace_id:
                    from ..audit import AuditEventType
                    self.audit_logger.create_event(
                        trace_id=writeback.trace_id,
                        session_id=self.current_session_id or "",
                        event_type=AuditEventType.VEHICLE_ACK,
                        task_id=writeback.task_id,
                        step_id=writeback.step_id,
                        branch_id=writeback.branch_id,
                        status="accepted"
                    )
            else:
                step_state.status = StepStatus.FAILED
                step_state.error = writeback.reason
                task_state.failed_steps.add(writeback.step_id)
                
                # Emit audit event: vehicle_ack failed (P0 exit #5)
                if self.audit_logger and writeback.trace_id:
                    from ..audit import AuditEventType
                    self.audit_logger.create_event(
                        trace_id=writeback.trace_id,
                        session_id=self.current_session_id or "",
                        event_type=AuditEventType.VEHICLE_ACK,
                        task_id=writeback.task_id,
                        step_id=writeback.step_id,
                        branch_id=writeback.branch_id,
                        status="failed",
                        reason=writeback.reason
                    )
        
        elif writeback.event in [WritebackEvent.NAV_ROUTE_STARTED, WritebackEvent.NAV_ARRIVED, WritebackEvent.NAV_REROUTED]:
            # 导航事件 (PRD v1.7 / detailed-v2.0.1)
            print(f"[Orchestrator] Navigation event: {writeback.event}")
            
            if writeback.event == WritebackEvent.NAV_ROUTE_STARTED:
                # P0: navigation step COMPLETE when route started (or nav_failed)
                if writeback.status == WritebackStatus.ACCEPTED:
                    step_state.status = StepStatus.COMPLETED
                    task_state.completed_steps.add(writeback.step_id)
                    print(f"[Orchestrator] Navigation step {writeback.step_id} COMPLETED on route_started")
                    
                    # Emit audit event: nav_route_started (P0 exit #5)
                    if self.audit_logger and writeback.trace_id:
                        from ..audit import AuditEventType
                        self.audit_logger.create_event(
                            trace_id=writeback.trace_id,
                            session_id=self.current_session_id or "",
                            event_type=AuditEventType.NAV_ROUTE_STARTED,
                            task_id=writeback.task_id,
                            step_id=writeback.step_id,
                            branch_id=writeback.branch_id
                        )
                else:
                    # nav_failed (rejected or failed)
                    step_state.status = StepStatus.FAILED
                    step_state.error = writeback.reason or "Navigation failed"
                    task_state.failed_steps.add(writeback.step_id)
                    print(f"[Orchestrator] Navigation step {writeback.step_id} FAILED")
            
            elif writeback.event in [WritebackEvent.NAV_ARRIVED, WritebackEvent.NAV_REROUTED]:
                # Optional events - may trigger light TTS insert only; never required for orchestration success
                print(f"[Orchestrator] Optional navigation event received: {writeback.event} (does not affect completion)")
                # Could potentially notify via TTS here in the future
        
        elif writeback.event in [WritebackEvent.MEDIA_ACK, WritebackEvent.CALENDAR_ACK]:
            if writeback.status == WritebackStatus.ACCEPTED:
                step_state.status = StepStatus.COMPLETED
                task_state.completed_steps.add(writeback.step_id)
            else:
                step_state.status = StepStatus.FAILED
                step_state.error = writeback.reason
                task_state.failed_steps.add(writeback.step_id)
    
    async def _publish_confirmed_l2_step(
        self,
        task: Task,
        step: Step,
        task_id: str,
        branch_id: str
    ):
        """发布confirmed L2步骤到MQTT（P0 fix #1）"""
        from datetime import datetime
        
        # 创建只包含该L2步骤的TaskGraph，标记为已确认
        # 使用metadata标记这是confirmed L2 execute frame
        confirmed_taskgraph = TaskGraph(
            tasks=[
                Task(
                    task_id=task_id,
                    branch_id=branch_id,
                    steps=[step],
                    user_intent=f"confirmed_l2_{step.step_id}"
                )
            ],
            session_id=self.pending_downlink.session_id if self.pending_downlink else "",
            trace_id=self.current_trace_id or "",
            timestamp=datetime.utcnow().isoformat() + "Z",
            metadata={"l2_confirmed": True, "original_step_id": step.step_id}
        )
        
        # 如果有MQTT publish callback，立即发布
        if self.mqtt_publish_callback:
            self.mqtt_publish_callback(confirmed_taskgraph)
            print(f"[Orchestrator] Published confirmed L2 step {step.step_id} to MQTT")
        else:
            # 否则存储待发布
            self.pending_l2_publish = confirmed_taskgraph
            print(f"[Orchestrator] Stored confirmed L2 step {step.step_id} for publishing")
    
    def get_downlink_taskgraph(self) -> Optional[TaskGraph]:
        """获取待下行的TaskGraph（已验证）"""
        return self.pending_downlink
    
    def get_and_clear_pending_l2(self) -> Optional[TaskGraph]:
        """获取并清除待发布的L2步骤"""
        l2_graph = self.pending_l2_publish
        self.pending_l2_publish = None
        return l2_graph
    
    # ==================== PRD v1.37 Feature 2: 检查点管理 ====================
    
    def _save_checkpoint(self):
        """保存当前执行状态到检查点"""
        if not self.checkpoint_manager or not self.current_trace_id or not self.current_session_id:
            return
        
        try:
            # 序列化TaskGraph
            taskgraph_dict = self.pending_downlink.model_dump() if self.pending_downlink else {}
            
            # 序列化StepStates
            step_states_dict = {}
            completed_steps = []
            failed_steps = []
            
            for task_id, task_state in self.task_states.items():
                for step_id, step_state in task_state.step_states.items():
                    step_states_dict[step_id] = {
                        "status": step_state.status.value,
                        "result": step_state.result,
                        "error": step_state.error,
                        "retry_count": step_state.retry_count
                    }
                completed_steps.extend(task_state.completed_steps)
                failed_steps.extend(task_state.failed_steps)
            
            # 保存检查点
            self.checkpoint_manager.save_checkpoint(
                session_id=self.current_session_id,
                trace_id=self.current_trace_id,
                taskgraph_dict=taskgraph_dict,
                step_states=step_states_dict,
                completed_steps=completed_steps,
                failed_steps=failed_steps,
                shadow_state=self.shadow_state
            )
            
        except Exception as e:
            print(f"[Orchestrator] 保存检查点失败: {e}")
    
    async def resume_from_checkpoint(
        self,
        session_id: str,
        trace_id: Optional[str] = None,
        writeback_callback: Optional[callable] = None
    ) -> Optional[Dict[str, Any]]:
        """从检查点恢复执行
        
        Args:
            session_id: 会话ID
            trace_id: 追踪ID（可选，不提供则恢复最近的）
            writeback_callback: 写回回调
            
        Returns:
            执行结果摘要，无检查点返回None
        """
        if not self.checkpoint_manager:
            print("[Orchestrator] 检查点管理器未初始化")
            return None
        
        # 加载检查点
        checkpoint = self.checkpoint_manager.load_checkpoint(session_id, trace_id)
        if not checkpoint:
            print(f"[Orchestrator] 未找到检查点: {session_id}/{trace_id}")
            return None
        
        print(f"[Orchestrator] 从检查点恢复: {checkpoint.trace_id}")
        
        try:
            # 恢复TaskGraph
            taskgraph = TaskGraph(**checkpoint.taskgraph)
            self.pending_downlink = taskgraph
            self.current_trace_id = checkpoint.trace_id
            self.current_session_id = checkpoint.session_id
            self.shadow_state = checkpoint.shadow_state
            
            # 恢复TaskStates
            for task in taskgraph.tasks:
                task_state = TaskState(task=task)
                
                for step in task.steps:
                    step_state = StepState(step=step)
                    
                    # 恢复步骤状态
                    if step.step_id in checkpoint.step_states:
                        saved_state = checkpoint.step_states[step.step_id]
                        step_state.status = StepStatus(saved_state["status"])
                        step_state.result = saved_state.get("result")
                        step_state.error = saved_state.get("error")
                        step_state.retry_count = saved_state.get("retry_count", 0)
                    
                    task_state.step_states[step.step_id] = step_state
                
                task_state.completed_steps = set(checkpoint.completed_steps)
                task_state.failed_steps = set(checkpoint.failed_steps)
                self.task_states[task.task_id] = task_state
            
            # 继续执行未完成的任务
            print(f"[Orchestrator] 继续执行: {len(checkpoint.completed_steps)}步已完成")
            
            execution_tasks = []
            for task in taskgraph.tasks:
                execution_tasks.append(
                    self._execute_task(task, writeback_callback)
                )
            
            results = await asyncio.gather(*execution_tasks, return_exceptions=True)
            
            # 汇总结果
            summary = {
                "resumed_from_checkpoint": True,
                "taskgraph_id": taskgraph.session_id,
                "trace_id": checkpoint.trace_id,
                "timestamp": taskgraph.timestamp,
                "task_results": {}
            }
            
            for task, result in zip(taskgraph.tasks, results):
                if isinstance(result, Exception):
                    summary["task_results"][task.task_id] = {
                        "status": "error",
                        "error": str(result)
                    }
                else:
                    summary["task_results"][task.task_id] = result
            
            # 清除检查点（任务完成）
            self.checkpoint_manager.clear_checkpoint(session_id, checkpoint.trace_id)
            
            return summary
            
        except Exception as e:
            print(f"[Orchestrator] 从检查点恢复失败: {e}")
            return None

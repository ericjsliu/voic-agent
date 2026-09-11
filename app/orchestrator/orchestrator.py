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
        shadow_state: Optional[Dict[str, Any]] = None
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
                        event=WritebackEvent.CONFIRM_RESULT,
                        status=WritebackStatus.PENDING if hasattr(WritebackStatus, 'PENDING') else 'pending',
                        reason=f"Waiting for user confirmation: {step.description or getattr(step.action, 'action', 'L2 action')}",
                        ts=datetime.utcnow().isoformat() + "Z"
                    )
                    await writeback_callback(writeback)
                
                print(f"[Orchestrator] L2 action {step.step_id} waiting for confirmation (NOT executing)")
                return
            
            # L0/L1立即执行
            context = {
                "shadow_state": self.shadow_state,
                "task_id": task.task_id,
                "branch_id": task.branch_id,
            }
            
            result = await adapter.execute(step, context)
            
            # 特殊处理：chitchat立即完成
            if step.domain == DomainType.CHITCHAT:
                step_state.status = StepStatus.COMPLETED
                step_state.result = result
                self.task_states[task.task_id].completed_steps.add(step.step_id)
                return
            
            # 特殊处理：knowledge立即返回结果
            if step.domain == DomainType.KNOWLEDGE:
                step_state.status = StepStatus.COMPLETED
                step_state.result = result
                self.task_states[task.task_id].completed_steps.add(step.step_id)
                
                # 发送写回
                if writeback_callback:
                    writeback = WritebackEnvelope(
                        task_id=task.task_id,
                        step_id=step.step_id,
                        branch_id=task.branch_id,
                        event=WritebackEvent.KNOWLEDGE_DONE,
                        status=WritebackStatus.SUCCESS if result.get("status") == "success" else WritebackStatus.FAILED,
                        reason=result.get("error"),
                        ts=datetime.utcnow().isoformat() + "Z"
                    )
                    await writeback_callback(writeback)
                return
            
            # 其他域：等待车辆ack
            step_state.status = StepStatus.COMPLETED  # 简化：假设立即成功
            step_state.result = result
            self.task_states[task.task_id].completed_steps.add(step.step_id)
            
        except Exception as e:
            step_state.status = StepStatus.FAILED
            step_state.error = str(e)
            self.task_states[task.task_id].failed_steps.add(step.step_id)
    
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
                # 用户接受，执行L2动作
                print(f"[Orchestrator] L2 confirmed, executing {writeback.step_id}")
                step_state.status = StepStatus.READY
                
                # 实际执行
                adapter = self.adapters[step_state.step.domain]
                context = {
                    "shadow_state": self.shadow_state,
                    "task_id": writeback.task_id,
                    "branch_id": writeback.branch_id,
                }
                result = await adapter.execute(step_state.step, context)
                step_state.result = result
                step_state.status = StepStatus.COMPLETED
                task_state.completed_steps.add(writeback.step_id)
            else:
                # 用户拒绝或超时
                step_state.status = StepStatus.CANCELLED
                step_state.error = writeback.reason or "User declined"
                task_state.failed_steps.add(writeback.step_id)
        
        # 处理其他事件
        elif writeback.event == WritebackEvent.VEHICLE_ACK:
            if writeback.status == WritebackStatus.SUCCESS:
                step_state.status = StepStatus.COMPLETED
                task_state.completed_steps.add(writeback.step_id)
            else:
                step_state.status = StepStatus.FAILED
                step_state.error = writeback.reason
                task_state.failed_steps.add(writeback.step_id)
        
        elif writeback.event in [WritebackEvent.NAV_ROUTE_STARTED, WritebackEvent.NAV_ARRIVED]:
            # 导航事件
            print(f"[Orchestrator] Navigation event: {writeback.event}")
            if writeback.event == WritebackEvent.NAV_ARRIVED:
                step_state.status = StepStatus.COMPLETED
                task_state.completed_steps.add(writeback.step_id)
        
        elif writeback.event in [WritebackEvent.MEDIA_ACK, WritebackEvent.CALENDAR_ACK]:
            if writeback.status == WritebackStatus.SUCCESS:
                step_state.status = StepStatus.COMPLETED
                task_state.completed_steps.add(writeback.step_id)
            else:
                step_state.status = StepStatus.FAILED
                step_state.error = writeback.reason
                task_state.failed_steps.add(writeback.step_id)
    
    def get_downlink_taskgraph(self) -> Optional[TaskGraph]:
        """获取待下行的TaskGraph（已验证）"""
        return self.pending_downlink

# -*- coding: utf-8 -*-
"""测试Orchestrator"""

import pytest
from datetime import datetime

from app.orchestrator import Orchestrator, StepStatus
from app.schemas import TaskGraph, Task, Step, DomainType, VehicleAction, ChitchatAction, ActionLevel
from app.adapters import VehicleAdapter, NavigationAdapter, MediaAdapter, CalendarAdapter, ChitchatAdapter
from app.rag_client import HybridRAGClient
from app.adapters import KnowledgeAdapter


@pytest.fixture
def orchestrator():
    """创建Orchestrator实例"""
    rag_client = HybridRAGClient(base_url="http://localhost:8001")
    
    return Orchestrator(
        vehicle_adapter=VehicleAdapter(),
        nav_adapter=NavigationAdapter(),
        media_adapter=MediaAdapter(),
        calendar_adapter=CalendarAdapter(),
        knowledge_adapter=KnowledgeAdapter(rag_client),
        chitchat_adapter=ChitchatAdapter(),
        shadow_state={"gear": "P", "speed_kmh": 0}
    )


@pytest.mark.asyncio
async def test_orchestrator_l1_action(orchestrator):
    """测试Orchestrator：L1动作立即执行"""
    step = Step(
        step_id="step_1",
        domain=DomainType.VEHICLE,
        action=VehicleAction(
            action="window_close",
            target="all_windows",
            level=ActionLevel.L1
        )
    )
    
    task = Task(
        task_id="task_1",
        branch_id="main",
        steps=[step]
    )
    
    taskgraph = TaskGraph(
        tasks=[task],
        session_id="session_123",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 执行
    result = await orchestrator.execute_taskgraph(taskgraph)
    
    assert "task_results" in result
    assert "task_1" in result["task_results"]


@pytest.mark.asyncio
async def test_orchestrator_l2_action(orchestrator):
    """测试Orchestrator：L2动作需要确认"""
    step = Step(
        step_id="step_1",
        domain=DomainType.VEHICLE,
        action=VehicleAction(
            action="door_lock",
            target="all_doors",
            level=ActionLevel.L2
        )
    )
    
    task = Task(
        task_id="task_1",
        branch_id="main",
        steps=[step]
    )
    
    taskgraph = TaskGraph(
        tasks=[task],
        session_id="session_123",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 执行（不会立即完成，因为L2需要确认）
    import asyncio
    task_exec = asyncio.create_task(orchestrator.execute_taskgraph(taskgraph))
    
    # 等待一小段时间
    await asyncio.sleep(0.1)
    
    # 检查步骤状态
    task_state = orchestrator.task_states.get("task_1")
    assert task_state is not None
    
    step_state = task_state.step_states.get("step_1")
    assert step_state is not None
    assert step_state.status == StepStatus.WAITING_CONFIRM
    
    # 取消任务（避免超时）
    task_exec.cancel()
    try:
        await task_exec
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_orchestrator_chitchat(orchestrator):
    """测试Orchestrator：闲聊立即完成"""
    step = Step(
        step_id="step_1",
        domain=DomainType.CHITCHAT,
        action=ChitchatAction(
            response="你好！",
            level=ActionLevel.L0
        )
    )
    
    task = Task(
        task_id="task_1",
        branch_id="main",
        steps=[step]
    )
    
    taskgraph = TaskGraph(
        tasks=[task],
        session_id="session_123",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 执行
    result = await orchestrator.execute_taskgraph(taskgraph)
    
    task_result = result["task_results"]["task_1"]
    assert task_result["status"] in ["completed", "partial"]
    assert "step_1" in task_result["completed_steps"]

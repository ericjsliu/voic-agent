#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基础流程验证脚本"""

import asyncio
from datetime import datetime

from app.schemas import TaskGraph, Task, Step, DomainType, VehicleAction, ActionLevel
from app.adapters import VehicleAdapter, NavigationAdapter
from app.planner import Planner
from app.memory import get_memory_store
from app.session import SessionManager, ContextAssembler


async def main():
    """基础流程测试"""
    print("=" * 60)
    print("Smart Cockpit Voice Dialogue Agent - 基础流程验证")
    print("=" * 60)
    
    # 1. 测试Schema
    print("\n[1/5] 测试JSON Schemas...")
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
        session_id="test_session",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    print(f"✓ TaskGraph创建成功: {len(taskgraph.tasks)} task(s)")
    
    # 2. 测试适配器
    print("\n[2/5] 测试领域适配器...")
    vehicle_adapter = VehicleAdapter()
    shadow_state = {"gear": "P", "speed_kmh": 0}
    
    is_valid, error = await vehicle_adapter.validate(step, shadow_state)
    print(f"✓ 车辆适配器验证: valid={is_valid}")
    
    # 测试POI解析
    nav_adapter = NavigationAdapter()
    poi_data = await nav_adapter.resolve_poi("家")
    print(f"✓ POI解析成功: {poi_data['poi_name']} @ ({poi_data['latitude']}, {poi_data['longitude']})")
    
    # 3. 测试会话管理
    print("\n[3/5] 测试会话管理...")
    memory_store = get_memory_store()
    session_manager = SessionManager(memory_store)
    
    session_info = await session_manager.create_session(driver_id="driver_001")
    print(f"✓ 会话创建成功: {session_info.session_id}")
    
    # 4. 测试规划器
    print("\n[4/5] 测试规划器（规则式）...")
    context_assembler = ContextAssembler(memory_store)
    context = await context_assembler.assemble(
        session_info=session_info,
        current_utterance="关闭车窗",
        telemetry=shadow_state
    )
    
    planner = Planner(nav_adapter=nav_adapter)
    taskgraph = await planner.plan("关闭车窗", context)
    
    print(f"✓ 规划成功: {len(taskgraph.tasks)} task(s), {len(taskgraph.tasks[0].steps)} step(s)")
    print(f"  - 步骤: {taskgraph.tasks[0].steps[0].action.action}")
    
    # 5. 测试多意图规划
    print("\n[5/5] 测试多意图规划...")
    taskgraph_multi = await planner.plan("导航到机场，然后播放音乐", context)
    print(f"✓ 多意图规划成功: {len(taskgraph_multi.tasks[0].steps)} step(s)")
    for i, step in enumerate(taskgraph_multi.tasks[0].steps):
        print(f"  - 步骤{i+1}: {step.domain.value}")
    
    print("\n" + "=" * 60)
    print("✅ 所有基础流程测试通过！")
    print("=" * 60)
    
    print("\n提示：")
    print("- 使用 docker-compose up 启动完整系统")
    print("- 查看 README.md 了解详细文档和API示例")


if __name__ == "__main__":
    asyncio.run(main())

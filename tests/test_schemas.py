# -*- coding: utf-8 -*-
"""测试JSON Schemas"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    TaskGraph, Task, Step, ActionLevel, DomainType,
    VehicleAction, NavigationAction, NavGoal, RoutePreferences,
    WritebackEnvelope, WritebackEvent, WritebackStatus
)


def test_vehicle_action_schema():
    """测试车辆动作schema"""
    # 有效的L1动作
    action = VehicleAction(
        action="window_open",
        target="all_windows",
        level=ActionLevel.L1
    )
    assert action.action == "window_open"
    assert action.level == ActionLevel.L1
    
    # 有效的L2动作
    action_l2 = VehicleAction(
        action="door_lock",
        target="all_doors",
        level=ActionLevel.L2
    )
    assert action_l2.level == ActionLevel.L2


def test_navigation_action_schema():
    """测试导航动作schema"""
    goal = NavGoal(
        poi_name="家",
        latitude=39.9042,
        longitude=116.4074,
        address="北京市东城区"
    )
    
    action = NavigationAction(
        action="nav_to",
        goal=goal,
        route_prefs=RoutePreferences(avoid_highway=True)
    )
    
    assert action.goal.poi_name == "家"
    assert action.route_prefs.avoid_highway is True


def test_taskgraph_schema():
    """测试TaskGraph schema"""
    # 创建完整的TaskGraph
    step = Step(
        step_id="step_1",
        domain=DomainType.VEHICLE,
        action=VehicleAction(
            action="window_close",
            target="all_windows",
            level=ActionLevel.L1
        ),
        depends_on=[],
        description="关闭所有车窗"
    )
    
    task = Task(
        task_id="task_1",
        branch_id="main",
        steps=[step],
        user_intent="关闭车窗"
    )
    
    taskgraph = TaskGraph(
        tasks=[task],
        session_id="session_123",
        timestamp="2024-01-01T00:00:00Z"
    )
    
    assert len(taskgraph.tasks) == 1
    assert taskgraph.tasks[0].steps[0].domain == DomainType.VEHICLE


def test_writeback_envelope_schema():
    """测试Writeback信封schema"""
    writeback = WritebackEnvelope(
        task_id="task_1",
        step_id="step_1",
        branch_id="main",
        event=WritebackEvent.VEHICLE_ACK,
        status=WritebackStatus.SUCCESS,
        ts="2024-01-01T00:00:00Z"
    )
    
    assert writeback.event == WritebackEvent.VEHICLE_ACK
    assert writeback.status == WritebackStatus.SUCCESS


def test_l2_action_level():
    """测试L2级别动作必须正确标记"""
    # door_lock应该是L2
    action = VehicleAction(
        action="door_lock",
        target="all_doors",
        level=ActionLevel.L2
    )
    assert action.level == ActionLevel.L2
    
    # trunk_open也应该是L2
    action_trunk = VehicleAction(
        action="trunk_open",
        level=ActionLevel.L2
    )
    assert action_trunk.level == ActionLevel.L2

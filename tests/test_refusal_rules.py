# -*- coding: utf-8 -*-
"""Tests for Refusal Rules"""

import pytest
from app.planner.refusal_rules import RefusalRules
from app.schemas.taskgraph import TaskGraph, Task, Step, VehicleAction, DomainType, ActionLevel
from datetime import datetime


def create_test_taskgraph_with_vehicle_action():
    """创建包含车辆动作的测试TaskGraph"""
    return TaskGraph(
        tasks=[
            Task(
                task_id="t1",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s1",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="window_open",
                            level=ActionLevel.L1
                        ),
                        description="打开车窗"
                    )
                ]
            )
        ],
        session_id="test",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )


def test_zone_restriction():
    """测试区域限制规则"""
    taskgraph = create_test_taskgraph_with_vehicle_action()
    
    # 在限制区域内
    restricted_location = {"lat": 39.9042, "lon": 116.4074}
    
    refusal = RefusalRules.check_zone_restriction(taskgraph, restricted_location)
    
    assert refusal is not None
    assert "禁止" in refusal or "学校" in refusal


def test_zone_allowed():
    """测试非限制区域允许"""
    taskgraph = create_test_taskgraph_with_vehicle_action()
    
    # 远离限制区域
    allowed_location = {"lat": 40.0, "lon": 117.0}
    
    refusal = RefusalRules.check_zone_restriction(taskgraph, allowed_location)
    
    assert refusal is None


def test_low_confidence_refusal():
    """测试低置信度拒绝"""
    low_confidence = 0.5
    
    refusal = RefusalRules.check_low_confidence(low_confidence)
    
    assert refusal is not None
    assert "置信度" in refusal


def test_high_confidence_allowed():
    """测试高置信度允许"""
    high_confidence = 0.9
    
    refusal = RefusalRules.check_low_confidence(high_confidence)
    
    assert refusal is None


def test_side_chat_refusal():
    """测试侧聊检测拒绝车辆动作"""
    taskgraph = create_test_taskgraph_with_vehicle_action()
    
    # 侧聊话语
    side_chat_utterance = "今天天气真好"
    
    refusal = RefusalRules.check_side_chat(side_chat_utterance, taskgraph)
    
    assert refusal is not None
    assert "侧聊" in refusal


def test_side_chat_chitchat_allowed():
    """测试侧聊+闲聊domain允许"""
    from app.schemas.taskgraph import ChitchatAction
    
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t1",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s1",
                        domain=DomainType.CHITCHAT,
                        action=ChitchatAction(
                            response="今天天气很好",
                            level=ActionLevel.L0
                        ),
                        description="闲聊"
                    )
                ]
            )
        ],
        session_id="test",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    side_chat_utterance = "今天天气真好"
    
    refusal = RefusalRules.check_side_chat(side_chat_utterance, taskgraph)
    
    # 侧聊+chitchat是允许的（没有车辆动作）
    assert refusal is None


def test_apply_all_refusal_rules():
    """测试综合应用所有拒绝规则"""
    taskgraph = create_test_taskgraph_with_vehicle_action()
    
    # 场景：在限制区域 + 低置信度 + 侧聊
    refusal = RefusalRules.apply_refusal_rules(
        taskgraph=taskgraph,
        utterance="今天天气真好",  # 侧聊
        current_location={"lat": 39.9042, "lon": 116.4074},  # 限制区域
        confidence_score=0.5  # 低置信度
    )
    
    # 应该被拒绝（任一规则触发即拒绝）
    assert refusal is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

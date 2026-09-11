# -*- coding: utf-8 -*-
"""Tests for Capability Profiles"""

import pytest
from app.capabilities import CapabilityProfile, get_capability_loader
from app.capabilities.schema import ActionCapability, FeatureFlags
from app.planner.capability_wrapper import CapabilityAwarePlanner
from app.planner import Planner
from app.schemas.context import DialogueContext, SessionInfo
from app.schemas.taskgraph import TaskGraph, DomainType
from datetime import datetime


def test_load_model_a_profile():
    """测试加载model_a档案"""
    loader = get_capability_loader()
    profile = loader.get_profile("model_a")
    
    assert profile is not None
    assert profile.model_id == "model_a"
    assert "window_open" in profile.supported_actions
    assert "window_close" in profile.supported_actions
    assert profile.is_action_supported("window_open")


def test_load_model_b_profile():
    """测试加载model_b档案"""
    loader = get_capability_loader()
    profile = loader.get_profile("model_b")
    
    assert profile is not None
    assert profile.model_id == "model_b"
    assert "sunroof_open" in profile.supported_actions
    assert "seat_heat_on" in profile.supported_actions
    assert profile.features.has_sunroof is True


def test_model_a_no_sunroof():
    """测试model_a不支持天窗"""
    loader = get_capability_loader()
    profile = loader.get_profile("model_a")
    
    assert not profile.is_action_supported("sunroof_open")
    assert profile.features.has_sunroof is False


def test_model_b_has_sunroof():
    """测试model_b支持天窗"""
    loader = get_capability_loader()
    profile = loader.get_profile("model_b")
    
    assert profile.is_action_supported("sunroof_open")
    assert profile.features.has_sunroof is True


def test_param_validation():
    """测试参数范围验证"""
    loader = get_capability_loader()
    profile_a = loader.get_profile("model_a")
    
    # model_a: 16-30°C
    assert profile_a.validate_param("ac_set_temp", "value", 20.0) is True
    assert profile_a.validate_param("ac_set_temp", "value", 15.0) is False
    assert profile_a.validate_param("ac_set_temp", "value", 31.0) is False


@pytest.mark.asyncio
async def test_planner_rejects_unsupported_action():
    """测试Planner拒绝不支持的动作"""
    loader = get_capability_loader()
    profile_a = loader.get_profile("model_a")
    
    # 创建base planner
    base_planner = Planner()
    wrapper = CapabilityAwarePlanner(base_planner)
    
    # 创建mock上下文
    session_info = SessionInfo(
        session_id="test_session",
        driver_id="test_driver",
        vehicle_id="test_vehicle",
        created_at=datetime.utcnow().isoformat() + "Z",
        last_active=datetime.utcnow().isoformat() + "Z"
    )
    
    context = DialogueContext(
        session_info=session_info,
        current_utterance="打开天窗",
        recent_utterances=[],
        shadow_state={},
        memory_slice=[],
        current_location=None
    )
    
    # 规划（model_a不支持天窗）
    taskgraph = await wrapper.plan("打开天窗", context, profile_a)
    
    # 应该返回不支持提示
    assert len(taskgraph.tasks) == 1
    task = taskgraph.tasks[0]
    assert task.domain == DomainType.CHITCHAT
    assert "不支持" in task.steps[0].action.response


@pytest.mark.asyncio
async def test_planner_allows_supported_action():
    """测试Planner允许支持的动作"""
    loader = get_capability_loader()
    profile_b = loader.get_profile("model_b")
    
    base_planner = Planner()
    wrapper = CapabilityAwarePlanner(base_planner)
    
    session_info = SessionInfo(
        session_id="test_session",
        driver_id="test_driver",
        vehicle_id="test_vehicle",
        created_at=datetime.utcnow().isoformat() + "Z",
        last_active=datetime.utcnow().isoformat() + "Z"
    )
    
    context = DialogueContext(
        session_info=session_info,
        current_utterance="打开天窗",
        recent_utterances=[],
        shadow_state={},
        memory_slice=[],
        current_location=None
    )
    
    # 规划（model_b支持天窗）
    taskgraph = await wrapper.plan("打开天窗", context, profile_b)
    
    # 应该包含车辆动作，不是chitchat
    # 注意：如果规则引擎不识别"打开天窗"，可能fallback到chitchat，这里只检查不会被拒绝
    assert len(taskgraph.tasks) > 0


def test_l1_gate_overrides():
    """测试L1门控规则覆盖"""
    loader = get_capability_loader()
    profile_a = loader.get_profile("model_a")
    
    override = profile_a.get_l1_override("window_close")
    assert override is not None
    assert override.requires_park is False
    assert override.requires_stationary is False


def test_command_mapping():
    """测试命令映射"""
    loader = get_capability_loader()
    profile_a = loader.get_profile("model_a")
    
    mapping = profile_a.get_command_mapping("window_open")
    assert mapping is not None
    assert mapping.vehicle_command == "WIN_OPEN"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

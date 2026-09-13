# -*- coding: utf-8 -*-
"""Regression tests for trace_id hotfix (production 500 error)

HOTFIX: TaskGraph must always have trace_id, whether from:
1. LLM path (injected after parse)
2. Rule-based path (passed to constructor)
3. CapabilityAwarePlanner (forwarded to base planner)
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from app.planner.planner import Planner
from app.planner.capability_wrapper import CapabilityAwarePlanner
from app.schemas.context import DialogueContext, SessionInfo
from app.capabilities import CapabilityProfile, ActionCapability


@pytest.fixture
def mock_context():
    """模拟对话上下文"""
    now = datetime.utcnow().isoformat() + "Z"
    return DialogueContext(
        session_info=SessionInfo(
            session_id="test_session",
            driver_id="driver_1",
            created_at=now,
            last_active=now
        ),
        current_location={"latitude": 39.9042, "longitude": 116.4074},
        recent_utterances=[],
        shadow_state={"gear": "P", "speed_kmh": 0},
        memory_slice={}
    )


@pytest.fixture
def base_planner():
    """基础规划器（无LLM）"""
    # 不提供API key，强制使用rule-based规划
    return Planner(
        llm_api_key=None,
        llm_base_url=None
    )


@pytest.mark.asyncio
async def test_rule_based_planner_always_has_trace_id(mock_context, base_planner):
    """规则式规划必须生成包含trace_id的TaskGraph（即使没有显式传入）"""
    
    # 测试1: 显式传入trace_id
    trace_id_1 = str(uuid.uuid4())
    taskgraph_1 = await base_planner.plan(
        user_utterance="打开车窗",
        context=mock_context,
        trace_id=trace_id_1
    )
    
    assert taskgraph_1.trace_id is not None, "TaskGraph must have trace_id"
    assert taskgraph_1.trace_id == trace_id_1, "TaskGraph must use provided trace_id"
    
    # 测试2: 未传入trace_id（Planner应自动生成）
    taskgraph_2 = await base_planner.plan(
        user_utterance="锁车",
        context=mock_context,
        trace_id=None
    )
    
    assert taskgraph_2.trace_id is not None, "TaskGraph must have trace_id even if not provided"
    # 验证是有效的UUID
    uuid.UUID(taskgraph_2.trace_id)


@pytest.mark.asyncio
async def test_llm_planner_injects_trace_id(mock_context):
    """LLM规划路径必须在解析后注入trace_id"""
    
    # 模拟LLM返回的JSON（故意不包含trace_id）
    llm_response_json = """
    {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "vehicle",
                "action": {
                    "action": "window_open",
                    "target": "all_windows",
                    "level": "L1"
                },
                "depends_on": [],
                "description": "打开车窗"
            }],
            "user_intent": "打开车窗"
        }],
        "session_id": "test_session",
        "timestamp": "2026-09-13T00:00:00Z"
    }
    """
    
    # 创建带LLM的Planner
    planner = Planner(
        llm_api_key="mock_key",
        llm_base_url="http://mock"
    )
    
    # 模拟AsyncOpenAI client
    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content=llm_response_json))]
    
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    planner.llm_client = mock_client
    
    # 执行规划
    trace_id = str(uuid.uuid4())
    taskgraph = await planner.plan(
        user_utterance="打开车窗",
        context=mock_context,
        trace_id=trace_id
    )
    
    # 验证trace_id被注入
    assert taskgraph.trace_id is not None, "LLM path must inject trace_id"
    assert taskgraph.trace_id == trace_id, "LLM path must use provided trace_id"


@pytest.mark.asyncio
async def test_capability_aware_planner_forwards_trace_id(mock_context):
    """CapabilityAwarePlanner必须转发trace_id到base planner"""
    
    # 创建base planner（无LLM）
    base_planner = Planner(llm_api_key=None)
    
    # 创建CapabilityAwarePlanner
    capability_planner = CapabilityAwarePlanner(
        base_planner=base_planner,
        audit_logger=None
    )
    
    # 创建能力档案
    capability_profile = CapabilityProfile(
        model_id="model_a",
        model_name="Model A",
        supported_actions=["window_open", "window_close", "door_lock"],
        action_capabilities={
            "window_open": ActionCapability(
                action="window_open",
                supported=True,
                level="L1"
            ),
            "window_close": ActionCapability(
                action="window_close",
                supported=True,
                level="L1"
            ),
            "door_lock": ActionCapability(
                action="door_lock",
                supported=True,
                level="L2"
            )
        },
        version="1.0"
    )
    
    # 执行规划
    trace_id = str(uuid.uuid4())
    taskgraph = await capability_planner.plan(
        user_utterance="打开车窗",
        context=mock_context,
        capability_profile=capability_profile,
        trace_id=trace_id,
        session_id="test_session"
    )
    
    # 验证trace_id存在
    assert taskgraph.trace_id is not None, "CapabilityAwarePlanner must forward trace_id"
    assert taskgraph.trace_id == trace_id, "CapabilityAwarePlanner must preserve provided trace_id"


@pytest.mark.asyncio
async def test_dialogue_path_does_not_500_when_llm_misses_trace_id(mock_context):
    """模拟/dialogue端点场景：LLM未返回trace_id不应导致500
    
    这个测试模拟完整的dialogue流程：
    1. Ingress生成trace_id
    2. 传给CapabilityAwarePlanner
    3. LLM返回JSON（无trace_id）
    4. Planner注入trace_id
    5. TaskGraph验证通过
    """
    
    # 模拟LLM返回（无trace_id）
    llm_response_json = """
    {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "chitchat",
                "action": {
                    "response": "好的",
                    "level": "L0"
                },
                "depends_on": [],
                "description": "闲聊"
            }],
            "user_intent": "你好"
        }],
        "session_id": "test_session",
        "timestamp": "2026-09-13T00:00:00Z"
    }
    """
    
    # 创建Planner with LLM
    base_planner = Planner(
        llm_api_key="mock_key",
        llm_base_url="http://mock"
    )
    
    # 模拟LLM client
    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content=llm_response_json))]
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    base_planner.llm_client = mock_client
    
    # 创建CapabilityAwarePlanner
    capability_planner = CapabilityAwarePlanner(
        base_planner=base_planner,
        audit_logger=None
    )
    
    # 模拟/dialogue ingress生成trace_id
    ingress_trace_id = str(uuid.uuid4())
    
    # 执行规划（不应该抛出ValidationError）
    try:
        taskgraph = await capability_planner.plan(
            user_utterance="你好",
            context=mock_context,
            capability_profile=None,
            trace_id=ingress_trace_id,
            session_id="test_session"
        )
        
        # 验证成功
        assert taskgraph.trace_id == ingress_trace_id, "TaskGraph must have injected trace_id"
        
    except Exception as e:
        pytest.fail(f"Dialogue path raised exception when LLM missed trace_id: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

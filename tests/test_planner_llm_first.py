# -*- coding: utf-8 -*-
"""Planner：LLM 先规划，规则只兜底。"""

import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.planner.planner import Planner
from app.planner.intent_gate import classify_intent
from app.schemas.context import DialogueContext, SessionInfo
from app.schemas.taskgraph import DomainType


@pytest.fixture
def mock_context():
    now = datetime.utcnow().isoformat() + "Z"
    return DialogueContext(
        session_info=SessionInfo(
            session_id="test_session",
            driver_id="driver_1",
            created_at=now,
            last_active=now,
        ),
        current_location={"latitude": 39.9042, "longitude": 116.4074},
        recent_utterances=[],
        shadow_state={"gear": "P", "speed_kmh": 0},
        memory_slice={},
    )


def _planner_with_llm_json(payload: dict) -> Planner:
    planner = Planner(llm_api_key="mock_key", llm_base_url="http://mock")
    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content=json.dumps(payload, ensure_ascii=False)))]
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    planner.llm_client = mock_client
    return planner


@pytest.mark.asyncio
async def test_gated_utterance_still_calls_llm(mock_context):
    """regex 能判域的句子也必须先走 LLM，不得短路。"""
    assert classify_intent("打开车窗") is not None
    payload = {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "vehicle",
                "action": {"action": "window_open", "target": "all_windows", "level": "L1"},
                "depends_on": [],
                "description": "打开车窗",
            }],
            "user_intent": "打开车窗",
        }],
        "session_id": "test_session",
        "timestamp": "2026-09-13T00:00:00Z",
    }
    planner = _planner_with_llm_json(payload)
    tg = await planner.plan("打开车窗", mock_context, trace_id=str(uuid.uuid4()))
    planner.llm_client.chat.completions.create.assert_called()
    assert tg.tasks[0].steps[0].action.action == "window_open"


@pytest.mark.asyncio
async def test_llm_artist_play_not_overwritten_by_gate(mock_context):
    """LLM 已给出 play_by_artist 时，护栏不得改写成 media_play。"""
    payload = {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "media",
                "action": {"action": "play_by_artist", "artist": "周杰伦", "query": "周杰伦的歌", "level": "L0"},
                "depends_on": [],
                "description": "播放周杰伦",
            }],
            "user_intent": "播放周杰伦的歌",
        }],
        "session_id": "test_session",
        "timestamp": "2026-09-13T00:00:00Z",
    }
    planner = _planner_with_llm_json(payload)
    tg = await planner.plan("播放周杰伦的歌", mock_context, trace_id=str(uuid.uuid4()))
    act = tg.tasks[0].steps[0].action
    assert act.action == "play_by_artist"
    assert act.artist == "周杰伦"


@pytest.mark.asyncio
async def test_guardrail_fixes_manual_dropped_to_chitchat(mock_context):
    """LLM 把手册问句判成闲聊时，规则事后纠回 knowledge。"""
    payload = {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "chitchat",
                "action": {"response": "好的", "level": "L0"},
                "depends_on": [],
                "description": "闲聊",
            }],
            "user_intent": "如何使用空调",
        }],
        "session_id": "test_session",
        "timestamp": "2026-09-13T00:00:00Z",
    }
    planner = _planner_with_llm_json(payload)
    tg = await planner.plan("如何使用空调", mock_context, trace_id=str(uuid.uuid4()))
    step = tg.tasks[0].steps[0]
    assert step.domain == DomainType.KNOWLEDGE
    assert step.action.action == "query_manual"


@pytest.mark.asyncio
async def test_no_llm_falls_back_to_rules(mock_context):
    """无 LLM 时才走规则兜底。"""
    planner = Planner(llm_api_key=None, llm_base_url=None)
    assert planner.llm_client is None
    tg = await planner.plan("打开车窗", mock_context, trace_id=str(uuid.uuid4()))
    assert tg.tasks[0].steps[0].action.action == "window_open"


@pytest.mark.asyncio
async def test_rule_fallback_set_ac_temp(mock_context):
    """闸未命中的调温句，无 LLM 时由规则填 set_ac_temp。"""
    planner = Planner(llm_api_key=None, llm_base_url=None)
    tg = await planner.plan("空调调到22度", mock_context, trace_id=str(uuid.uuid4()))
    act = tg.tasks[0].steps[0].action
    assert act.action == "set_ac_temp"
    assert act.temperature == 22


@pytest.mark.asyncio
async def test_rule_fallback_nav_uses_map_tool(mock_context):
    """无 LLM 时导航目的地由 resolve_poi 工具解析，不靠切词。"""
    planner = Planner(llm_api_key=None, llm_base_url=None)
    assert not hasattr(planner, "_extract_destination")
    tg = await planner.plan("导航回家", mock_context, trace_id=str(uuid.uuid4()))
    goal = tg.tasks[0].steps[0].action.goal
    assert goal.poi_name == "家"
    assert goal.latitude != 0 and goal.longitude != 0


@pytest.mark.asyncio
async def test_llm_poi_name_resolved_by_tool(mock_context):
    """LLM 只填 poi_name，坐标由地图工具写入。"""
    payload = {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "navigation",
                "action": {
                    "action": "set_nav_goal",
                    "goal": {"poi_name": "机场", "latitude": 0, "longitude": 0},
                    "level": "L0",
                },
                "depends_on": [],
                "description": "导航到机场",
            }],
            "user_intent": "导航到机场",
        }],
        "session_id": "test_session",
        "timestamp": "2026-09-13T00:00:00Z",
    }
    planner = _planner_with_llm_json(payload)
    tg = await planner.plan("导航到机场", mock_context, trace_id=str(uuid.uuid4()))
    goal = tg.tasks[0].steps[0].action.goal
    assert goal.poi_name == "机场"
    assert abs(goal.latitude - 40.0799) < 0.01
    """闸未命中的调温句，无 LLM 时由规则填 set_ac_temp。"""
    planner = Planner(llm_api_key=None, llm_base_url=None)
    tg = await planner.plan("空调调到22度", mock_context, trace_id=str(uuid.uuid4()))
    act = tg.tasks[0].steps[0].action
    assert act.action == "set_ac_temp"
    assert act.temperature == 22

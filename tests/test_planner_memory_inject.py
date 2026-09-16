# -*- coding: utf-8 -*-
"""阶段1闸门：召回记忆必须注入 Planner prompt（与 UI TopN 同源）。"""

import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.planner.planner import Planner
from app.schemas.context import DialogueContext, SessionInfo


@pytest.fixture
def base_context():
    now = datetime.utcnow().isoformat() + "Z"
    return DialogueContext(
        session_info=SessionInfo(
            session_id="sess_mem",
            driver_id="driver_1",
            created_at=now,
            last_active=now,
        ),
        current_location={"latitude": 39.9, "longitude": 116.4},
        recent_utterances=["你好"],
        shadow_state={"gear": "P"},
        memory_slice={},
    )


def test_format_relevant_memories_empty():
    assert Planner.format_relevant_memories_for_prompt([]) == ""
    assert Planner.format_relevant_memories_for_prompt(None) == ""  # type: ignore[arg-type]


def test_format_relevant_memories_includes_content_and_category():
    text = Planner.format_relevant_memories_for_prompt([
        {"content": "家地址：望京SOHO", "category": "personal_basic"},
        {"content": "喜欢听周杰伦", "category": "user_preference"},
    ])
    assert "家地址：望京SOHO" in text
    assert "喜欢听周杰伦" in text
    assert "[personal_basic]" in text
    assert "[user_preference]" in text
    assert "用户长期记忆" in text


def test_build_user_message_injects_same_topn_as_ui(base_context):
    """UI 与 prompt 必须同源：memory_slice.relevant_memories 原样进提示。"""
    memories = [
        {"content": "家地址：望京SOHO", "category": "personal_basic"},
        {"content": "偏好空调温度：22度", "category": "user_preference"},
    ]
    base_context.memory_slice = {"relevant_memories": memories}
    planner = Planner(llm_api_key=None)
    msg = planner._build_user_message("导航回家", base_context)

    assert "家地址：望京SOHO" in msg
    assert "偏好空调温度：22度" in msg
    # 与 UI 一致：不二次截断 Assemble 已定额的 TopN
    for m in memories:
        assert m["content"] in msg


def test_build_user_message_no_memory_section_when_empty(base_context):
    planner = Planner(llm_api_key=None)
    msg = planner._build_user_message("打开车窗", base_context)
    assert "用户长期记忆：无" in msg
    assert "家地址" not in msg


def test_system_prompt_mentions_long_term_memory():
    planner = Planner(llm_api_key=None)
    prompt = planner._build_system_prompt()
    assert "用户长期记忆" in prompt


@pytest.mark.asyncio
async def test_llm_plan_receives_memory_in_user_message(base_context):
    """LLM 路径实际发出的 messages 必须含召回 content。"""
    memories = [
        {"content": "喜欢听周杰伦", "category": "user_preference"},
    ]
    base_context.memory_slice = {"relevant_memories": memories}

    payload = {
        "tasks": [{
            "task_id": "task_1",
            "branch_id": "main",
            "steps": [{
                "step_id": "step_1",
                "domain": "media",
                "action": {
                    "action": "play_by_artist",
                    "artist": "周杰伦",
                    "query": "周杰伦",
                    "level": "L0",
                },
                "depends_on": [],
                "description": "播放周杰伦",
            }],
            "user_intent": "放点音乐",
        }],
        "session_id": "sess_mem",
        "timestamp": "2026-09-16T00:00:00Z",
    }

    planner = Planner(llm_api_key="mock", llm_base_url="http://mock")
    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content=json.dumps(payload, ensure_ascii=False)))]
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    planner.llm_client = mock_client

    await planner.plan("放点音乐", base_context, trace_id=str(uuid.uuid4()))

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    messages = call_kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    system_msg = next(m["content"] for m in messages if m["role"] == "system")

    assert "喜欢听周杰伦" in user_msg
    assert "user_preference" in user_msg
    assert "用户长期记忆" in system_msg

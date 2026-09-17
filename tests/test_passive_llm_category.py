# -*- coding: utf-8 -*-
"""阶段2闸门：被动 LLM category/facts 必须打通 put_memory。"""

from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

from app.memory.p2_memory_service import (
    P2MemoryService,
    PassiveExtractor,
    MemoryClassifier,
    MEMORY_CATEGORIES,
)


def _fake_db(captured: dict):
    class FakeQuery:
        def filter_by(self, **kwargs):
            return self

        def all(self):
            return []

    class FakeDB:
        def query(self, *a, **k):
            return FakeQuery()

        def add(self, memory):
            captured["category"] = memory.category
            captured["content"] = getattr(memory, "content", None)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    return FakeDB()


def test_put_memory_uses_passed_llm_category_not_hardcoded_none():
    """put_memory 不得再写死 llm_extract_result=None；传入 LLM 结果应生效。"""
    service = P2MemoryService.__new__(P2MemoryService)
    service.enable_vector = False
    service.embedding_client = None
    service.extractor_client = None
    service.safety_gate = MagicMock()
    service.safety_gate.check = Mock(return_value=("PASS", None, "喜欢听周杰伦"))
    service.classifier = MemoryClassifier(llm_client=None)
    service._generate_embedding = Mock(return_value=None)
    service._similarity = Mock(return_value=0.0)
    service._log_audit = Mock()

    captured = {}

    with patch("app.memory.p2_memory_service.get_db", return_value=_fake_db(captured)):
        with patch(
            "app.memory.p2_memory_service.LongTermMemoryP2",
            side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
        ):
            mid = service.put_memory(
                user_id="account_test:d1",
                content="喜欢听周杰伦",
                source_ref="passive:t1",
                is_active=False,
                llm_extract_result={
                    "facts": ["喜欢听周杰伦"],
                    "category": "user_preference",
                },
            )

    assert mid is not None
    assert captured["category"] == "user_preference"


def test_put_memory_prefers_explicit_category_arg():
    service = P2MemoryService.__new__(P2MemoryService)
    service.enable_vector = False
    service.embedding_client = None
    service.safety_gate = MagicMock()
    service.safety_gate.check = Mock(return_value=("PASS", None, "称呼：老王"))
    service.classifier = MemoryClassifier(llm_client=None)
    service._generate_embedding = Mock(return_value=None)
    service._similarity = Mock(return_value=0.0)
    service._log_audit = Mock()

    captured = {}

    with patch("app.memory.p2_memory_service.get_db", return_value=_fake_db(captured)):
        with patch(
            "app.memory.p2_memory_service.LongTermMemoryP2",
            side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
        ):
            service.put_memory(
                user_id="account_test:d1",
                content="称呼：老王",
                source_ref="passive:t1",
                category="personal_basic",
            )

    assert captured["category"] == "personal_basic"
    assert "personal_basic" in MEMORY_CATEGORIES


def test_handle_passive_forwards_llm_category_to_put():
    """handle_passive_extraction 必须把 LLM category 传给 put_memory。"""
    service = P2MemoryService.__new__(P2MemoryService)
    mock_llm = Mock()
    mock_llm.score_utterance = Mock(return_value={
        "long_term": 0.9, "stability": 0.9, "personal": 0.9, "score": 0.9
    })
    mock_llm.extract_facts = Mock(return_value={
        "facts": ["喜欢听周杰伦"],
        "category": "user_preference",
    })
    service.passive_extractor = PassiveExtractor(llm_client=mock_llm)
    service.put_memory = Mock(return_value="mid-1")

    service.handle_passive_extraction(
        user_id="account_test:d1",
        utterance="我平时喜欢听周杰伦",
        assistant_response="好的",
        context={},
        trace_id="trace-x",
    )

    assert service.put_memory.called
    kwargs = service.put_memory.call_args.kwargs
    assert kwargs["category"] == "user_preference"
    assert kwargs["llm_extract_result"]["category"] == "user_preference"
    assert "周杰伦" in kwargs["content"]
    assert kwargs["is_active"] is False

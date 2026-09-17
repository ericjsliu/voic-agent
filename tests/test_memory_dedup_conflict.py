# -*- coding: utf-8 -*-
"""阶段4闸门：向量相似≥0.95 去重 + version_id 冲突覆盖。"""

from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

from app.memory.p2_memory_service import P2MemoryService


def test_cosine_similarity_identical_is_one():
    emb = [0.1, 0.2, 0.3]
    assert abs(P2MemoryService._cosine_similarity(emb, emb) - 1.0) < 1e-9


def test_same_dimension_conflict_home_address():
    assert P2MemoryService._is_same_dimension_conflict(
        "家地址：望京SOHO", "家地址：中关村"
    )
    assert not P2MemoryService._is_same_dimension_conflict(
        "家地址：望京SOHO", "家地址：望京SOHO"
    )
    assert not P2MemoryService._is_same_dimension_conflict(
        "家地址：望京SOHO", "公司地址：中关村"
    )


def test_vector_dedup_does_not_double_insert():
    """近义向量 ≥0.95 应合并，不双插。"""
    service = P2MemoryService.__new__(P2MemoryService)
    service.enable_vector = True
    service.safety_gate = MagicMock()
    service.safety_gate.check = Mock(return_value=("PASS", None, "喜欢听周杰伦的歌"))
    service.classifier = MagicMock()
    service.classifier.classify = Mock(return_value="user_preference")
    service._generate_embedding = Mock(return_value=[1.0, 0.0, 0.0])
    service._log_audit = Mock()

    existing = SimpleNamespace(
        memory_id="old-1",
        content="喜欢听周杰伦",
        embedding=[0.99, 0.01, 0.0],  # cosine ≈ 0.9999
        weight=1.0,
        version_id=1,
        source_ref="old",
    )

    class FakeQuery:
        def filter_by(self, **kwargs):
            return self

        def all(self):
            return [existing]

    class FakeDB:
        def query(self, *a, **k):
            return FakeQuery()

        def add(self, memory):
            raise AssertionError("不应双插新记忆")

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    with patch("app.memory.p2_memory_service.get_db", return_value=FakeDB()):
        mid = service.put_memory(
            user_id="account_test:d1",
            content="喜欢听周杰伦的歌",
            source_ref="t",
        )

    assert mid == "old-1"
    assert existing.weight > 1.0


def test_conflict_overwrites_and_bumps_version():
    """矛盾同维事实应升 version_id 覆盖 content。"""
    service = P2MemoryService.__new__(P2MemoryService)
    service.enable_vector = False
    service.safety_gate = MagicMock()
    service.safety_gate.check = Mock(return_value=("PASS", None, "家地址：中关村"))
    service.classifier = MagicMock()
    service.classifier.classify = Mock(return_value="personal_basic")
    service._generate_embedding = Mock(return_value=None)
    service._log_audit = Mock()

    existing = SimpleNamespace(
        memory_id="home-1",
        content="家地址：望京SOHO",
        embedding=None,
        weight=1.0,
        version_id=1,
        source_ref="old",
    )

    class FakeQuery:
        def filter_by(self, **kwargs):
            return self

        def all(self):
            return [existing]

    class FakeDB:
        def query(self, *a, **k):
            return FakeQuery()

        def add(self, memory):
            raise AssertionError("冲突应覆盖旧条，不新增")

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    with patch("app.memory.p2_memory_service.get_db", return_value=FakeDB()):
        mid = service.put_memory(
            user_id="account_test:d1",
            content="家地址：中关村",
            source_ref="new",
        )

    assert mid == "home-1"
    assert existing.content == "家地址：中关村"
    assert existing.version_id == 2

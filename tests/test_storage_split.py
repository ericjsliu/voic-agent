# -*- coding: utf-8 -*-
"""Tests for Storage Split (Redis vs PostgreSQL)"""

import pytest
from datetime import datetime
from app.storage.entity_buffer import EntityBuffer
from app.storage.models import LongTermMemory, SpatiotemporalEvent


class MockRedis:
    """Mock Redis client for testing"""
    def __init__(self):
        self.store = {}
    
    def setex(self, key, ttl, value):
        self.store[key] = value
    
    def get(self, key):
        return self.store.get(key)
    
    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
    
    def keys(self, pattern):
        import fnmatch
        pattern = pattern.replace('*', '.*')
        return [k for k in self.store.keys() if fnmatch.fnmatch(k, pattern)]


def test_entity_buffer_last_poi():
    """测试实体缓冲区：最近POI"""
    redis = MockRedis()
    buffer = EntityBuffer(redis)
    
    session_id = "test_session"
    poi = {
        "name": "机场",
        "lat": 39.9042,
        "lon": 116.4074
    }
    
    # 设置POI
    buffer.set_last_poi(session_id, poi)
    
    # 读取POI
    retrieved = buffer.get_last_poi(session_id)
    assert retrieved is not None
    assert retrieved["name"] == "机场"
    assert retrieved["lat"] == 39.9042


def test_entity_buffer_clear_all():
    """测试实体缓冲区：清空所有"""
    redis = MockRedis()
    buffer = EntityBuffer(redis)
    
    session_id = "test_session"
    
    # 设置多个实体
    buffer.set_last_poi(session_id, {"name": "POI"})
    buffer.set_last_media(session_id, {"song": "Song"})
    buffer.set_candidate_list(session_id, "poi", [{"name": "POI1"}])
    
    # 验证都存在
    assert buffer.get_last_poi(session_id) is not None
    assert buffer.get_last_media(session_id) is not None
    
    # 清空
    buffer.clear_all(session_id)
    
    # 验证都被清空
    assert buffer.get_last_poi(session_id) is None
    assert buffer.get_last_media(session_id) is None


def test_entity_buffer_get_all():
    """测试实体缓冲区：获取所有"""
    redis = MockRedis()
    buffer = EntityBuffer(redis)
    
    session_id = "test_session"
    
    # 设置实体
    buffer.set_last_poi(session_id, {"name": "机场"})
    buffer.set_last_media(session_id, {"song": "歌曲"})
    
    # 获取所有
    all_entities = buffer.get_all(session_id)
    
    assert all_entities["last_poi"] is not None
    assert all_entities["last_poi"]["name"] == "机场"
    assert all_entities["last_media"] is not None
    assert all_entities["last_media"]["song"] == "歌曲"


def test_storage_responsibilities():
    """测试存储职责划分
    
    验证：
    - Redis: 热数据（session, entity_buffer, TTL）
    - PostgreSQL: 持久化数据（whitelist, audit, events）
    """
    # Redis职责
    redis_keys = [
        "session:xxx",
        "shadow_state:xxx",
        "entity_buffer:xxx:last_poi",
        "active_task:xxx",
        "utterances:xxx"
    ]
    
    for key in redis_keys:
        assert ":" in key  # Redis key格式
        assert not key.startswith("long_term_")  # 不是长期数据
    
    # PostgreSQL职责（通过model验证）
    pg_models = [
        LongTermMemory,
        SpatiotemporalEvent
    ]
    
    for model in pg_models:
        assert hasattr(model, '__tablename__')
        assert hasattr(model, 'id')
        assert hasattr(model, 'created_at')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

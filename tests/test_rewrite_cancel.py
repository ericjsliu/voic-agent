# -*- coding: utf-8 -*-
"""Tests for Rewrite/Cancel Rules"""

import pytest
from app.planner.rewrite_cancel import RewriteCancelManager


class MockRedis:
    """Mock Redis for testing"""
    def __init__(self):
        self.store = {}
    
    def setex(self, key, ttl, value):
        self.store[key] = value
    
    def get(self, key):
        return self.store.get(key)
    
    def delete(self, key):
        self.store.pop(key, None)


def test_record_and_get_active_task():
    """测试记录和获取活跃任务"""
    redis = MockRedis()
    manager = RewriteCancelManager(redis)
    
    session_id = "test_session"
    task_id = "task_123"
    taskgraph = {"tasks": []}
    
    # 记录活跃任务
    manager.record_active_task(session_id, task_id, taskgraph)
    
    # 获取活跃任务
    active = manager.get_active_task(session_id)
    
    assert active is not None
    assert active["task_id"] == task_id


def test_check_cancel():
    """测试取消指令检测"""
    redis = MockRedis()
    manager = RewriteCancelManager(redis)
    
    session_id = "test_session"
    manager.record_active_task(session_id, "task_123", {})
    
    # 取消关键词
    result = manager.check_rewrite_or_cancel(session_id, "算了，取消吧")
    
    assert result is not None
    assert result["action"] == "cancel"
    assert result["original_task_id"] == "task_123"


def test_check_rewrite():
    """测试改写指令检测"""
    redis = MockRedis()
    manager = RewriteCancelManager(redis)
    
    session_id = "test_session"
    manager.record_active_task(session_id, "task_123", {})
    
    # 改写关键词
    result = manager.check_rewrite_or_cancel(session_id, "不是这个，改成那个")
    
    assert result is not None
    assert result["action"] == "rewrite"
    assert result["original_task_id"] == "task_123"


def test_no_rewrite_or_cancel():
    """测试非改写/取消指令"""
    redis = MockRedis()
    manager = RewriteCancelManager(redis)
    
    session_id = "test_session"
    manager.record_active_task(session_id, "task_123", {})
    
    # 普通指令
    result = manager.check_rewrite_or_cancel(session_id, "打开车窗")
    
    assert result is None


def test_no_active_task():
    """测试无活跃任务时不检测改写/取消"""
    redis = MockRedis()
    manager = RewriteCancelManager(redis)
    
    session_id = "test_session"
    
    # 没有活跃任务
    result = manager.check_rewrite_or_cancel(session_id, "取消")
    
    assert result is None


def test_clear_active_task():
    """测试清除活跃任务"""
    redis = MockRedis()
    manager = RewriteCancelManager(redis)
    
    session_id = "test_session"
    manager.record_active_task(session_id, "task_123", {})
    
    # 清除
    manager.clear_active_task(session_id)
    
    # 验证已清除
    active = manager.get_active_task(session_id)
    assert active is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

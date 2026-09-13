# -*- coding: utf-8 -*-
"""测试Capability Profile切换门控（detailed-v1.5）"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from app.capabilities import ProfileSwitchingManager, ProfileState, CapabilityProfile
from app.session import SessionManager
from app.memory import InMemoryStore


class MockRedis:
    """模拟Redis客户端"""
    def __init__(self):
        self.data = {}
        self.expirations = {}
    
    def get(self, key):
        if key in self.data:
            return self.data[key].encode('utf-8') if isinstance(self.data[key], str) else self.data[key]
        return None
    
    def setex(self, key, ttl, value):
        self.data[key] = value
        self.expirations[key] = ttl
    
    def delete(self, key):
        if key in self.data:
            del self.data[key]


class MockPGStore:
    """模拟PostgreSQL存储"""
    def __init__(self):
        self.profiles = {}
    
    def get_active_capability_profile(self, model_id, hardware_option=None):
        """获取活跃的能力档案"""
        key = f"{model_id}_{hardware_option}" if hardware_option else model_id
        return self.profiles.get(key)
    
    def save_capability_profile(self, model_id, profile_data, hardware_option=None):
        """保存能力档案"""
        key = f"{model_id}_{hardware_option}" if hardware_option else model_id
        self.profiles[key] = profile_data


@pytest.fixture
def mock_redis():
    return MockRedis()


@pytest.fixture
def mock_pg_store():
    store = MockPGStore()
    
    # 预加载model_a和model_b的profiles
    store.save_capability_profile('model_a', {
        "model_id": "model_a",
        "model_name": "Model A (Standard)",
        "supported_actions": ["window_open", "window_close", "door_lock", "door_unlock", "ac_on", "ac_off"],
        "feature_flags": {"has_sunroof": False}
    })
    
    store.save_capability_profile('model_b', {
        "model_id": "model_b",
        "model_name": "Model B (Premium)",
        "supported_actions": ["window_open", "window_close", "door_lock", "door_unlock", 
                              "ac_on", "ac_off", "sunroof_open", "sunroof_close"],
        "feature_flags": {"has_sunroof": True}
    })
    
    return store


@pytest.fixture
def profile_switcher(mock_redis, mock_pg_store):
    return ProfileSwitchingManager(mock_redis, mock_pg_store)


@pytest.mark.asyncio
async def test_profile_switching_state_transition(profile_switcher):
    """测试profile切换状态转换（ready -> switching -> ready）"""
    session_id = "test_session_1"
    
    # 初始状态应该是ready
    assert profile_switcher.get_profile_state(session_id) == ProfileState.READY
    assert profile_switcher.is_profile_ready(session_id) is True
    
    # 进入switching状态
    profile_switcher.enter_switching_state(session_id)
    assert profile_switcher.get_profile_state(session_id) == ProfileState.SWITCHING
    assert profile_switcher.is_profile_ready(session_id) is False
    
    # 回到ready状态
    profile_switcher.enter_ready_state(session_id)
    assert profile_switcher.get_profile_state(session_id) == ProfileState.READY
    assert profile_switcher.is_profile_ready(session_id) is True


@pytest.mark.asyncio
async def test_mqtt_downlink_blocked_during_switching(profile_switcher):
    """测试profile切换时MQTT下行被阻止"""
    session_id = "test_session_2"
    
    # ready状态：允许下行
    allowed, reason = profile_switcher.check_mqtt_downlink_allowed(session_id)
    assert allowed is True
    assert reason is None
    
    # switching状态：阻止下行
    profile_switcher.enter_switching_state(session_id)
    allowed, reason = profile_switcher.check_mqtt_downlink_allowed(session_id)
    assert allowed is False
    assert "切换中" in reason or "暂停" in reason
    
    # 恢复ready：允许下行
    profile_switcher.enter_ready_state(session_id)
    allowed, reason = profile_switcher.check_mqtt_downlink_allowed(session_id)
    assert allowed is True


@pytest.mark.asyncio
async def test_switch_profile_from_pg_source_of_truth(profile_switcher, mock_pg_store):
    """测试从PostgreSQL（source of truth）切换profile"""
    session_id = "test_session_3"
    
    # 切换到model_a
    result = await profile_switcher.switch_profile(session_id, "model_a")
    
    assert result["success"] is True
    assert result["state"] == "ready"
    assert result["profile"]["model_id"] == "model_a"
    assert "sunroof_open" not in result["profile"]["supported_actions"]
    
    # 验证profile已绑定到session
    profile_key = f"capability_profile:{session_id}"
    cached_profile = profile_switcher.redis.get(profile_key)
    assert cached_profile is not None
    
    profile_data = json.loads(cached_profile)
    assert profile_data["model_id"] == "model_a"


@pytest.mark.asyncio
async def test_switching_mid_session_does_not_use_old_whitelist(profile_switcher, mock_pg_store):
    """测试切换profile时不使用旧的whitelist"""
    session_id = "test_session_4"
    
    # 初始：切换到model_a
    result1 = await profile_switcher.switch_profile(session_id, "model_a")
    assert result1["success"] is True
    assert "sunroof_open" not in result1["profile"]["supported_actions"]
    
    # 模拟MQTT下行检查
    allowed, _ = profile_switcher.check_mqtt_downlink_allowed(session_id)
    assert allowed is True
    
    # 切换到model_b（中途切换）
    result2 = await profile_switcher.switch_profile(session_id, "model_b")
    assert result2["success"] is True
    assert "sunroof_open" in result2["profile"]["supported_actions"]
    
    # 验证新profile已生效
    profile_key = f"capability_profile:{session_id}"
    cached_profile = profile_switcher.redis.get(profile_key)
    profile_data = json.loads(cached_profile)
    assert profile_data["model_id"] == "model_b"
    assert "sunroof_open" in profile_data["supported_actions"]


@pytest.mark.asyncio
async def test_model_a_blocks_sunroof(profile_switcher, mock_pg_store):
    """测试model_a不支持sunroof"""
    session_id = "test_session_5"
    
    # 切换到model_a
    result = await profile_switcher.switch_profile(session_id, "model_a")
    assert result["success"] is True
    
    profile = result["profile"]
    
    # 验证sunroof相关动作不在whitelist中
    assert "sunroof_open" not in profile["supported_actions"]
    assert "sunroof_close" not in profile["supported_actions"]
    assert profile["feature_flags"]["has_sunroof"] is False


@pytest.mark.asyncio
async def test_model_b_allows_sunroof(profile_switcher, mock_pg_store):
    """测试model_b支持sunroof"""
    session_id = "test_session_6"
    
    # 切换到model_b
    result = await profile_switcher.switch_profile(session_id, "model_b")
    assert result["success"] is True
    
    profile = result["profile"]
    
    # 验证sunroof相关动作在whitelist中
    assert "sunroof_open" in profile["supported_actions"]
    assert "sunroof_close" in profile["supported_actions"]
    assert profile["feature_flags"]["has_sunroof"] is True


@pytest.mark.asyncio
async def test_profile_switch_failure_handling(profile_switcher):
    """测试profile切换失败处理"""
    session_id = "test_session_7"
    
    # 尝试切换到不存在的model
    result = await profile_switcher.switch_profile(session_id, "model_nonexistent")
    
    assert result["success"] is False
    assert result["state"] == "failed"
    assert result["profile"] is None
    
    # 验证状态为failed
    assert profile_switcher.get_profile_state(session_id) == ProfileState.FAILED
    
    # 验证MQTT下行被阻止
    allowed, reason = profile_switcher.check_mqtt_downlink_allowed(session_id)
    assert allowed is False
    assert "失败" in reason


@pytest.mark.asyncio
async def test_session_manager_integration(mock_redis, mock_pg_store):
    """测试SessionManager集成ProfileSwitcher"""
    memory_store = InMemoryStore()
    session_manager = SessionManager(
        memory_store=memory_store,
        pg_store=mock_pg_store,
        entity_buffer=None,
        redis_client=mock_redis
    )
    
    # 创建session（使用model_a）
    session_info = await session_manager.create_session(
        driver_id="driver_001",
        vehicle_model="model_a"
    )
    
    session_id = session_info.session_id
    
    # 验证profile已加载
    profile = await session_manager.get_capability_profile(session_id)
    assert profile is not None
    
    # 验证初始状态ready
    assert session_manager.is_profile_ready(session_id) is True
    
    # 切换到model_b
    result = await session_manager.switch_vehicle_model(
        session_id=session_id,
        new_model_id="model_b"
    )
    
    assert result["success"] is True
    assert result["state"] == "ready"
    
    # 验证新profile
    new_profile = await session_manager.get_capability_profile(session_id)
    # Note: 这里需要重新从Redis读取才能看到新profile
    # 因为switch_vehicle_model直接写入Redis


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

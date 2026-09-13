# -*- coding: utf-8 -*-
"""Capability Profile Switching Gate - detailed-v1.5
 
确保profile切换时暂停MQTT下行执行，等待新profile从PostgreSQL加载后才恢复
"""

from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class ProfileState(str, Enum):
    """Profile状态"""
    READY = "ready"
    SWITCHING = "switching"
    FAILED = "failed"


class ProfileSwitchingManager:
    """Capability Profile切换门控管理器
    
    职责：
    - 管理profile切换状态（ready/switching/failed）
    - 暂停/恢复MQTT下行执行边
    - 从PostgreSQL加载新profile
    - 发出profile_ready通知
    """
    
    def __init__(self, redis_client, pg_store=None):
        self.redis = redis_client
        self.pg_store = pg_store
    
    def _get_state_key(self, session_id: str) -> str:
        """获取profile状态的Redis key"""
        return f"profile_state:{session_id}"
    
    def _get_profile_key(self, session_id: str) -> str:
        """获取profile缓存的Redis key"""
        return f"capability_profile:{session_id}"
    
    def get_profile_state(self, session_id: str) -> ProfileState:
        """获取当前profile状态"""
        state = self.redis.get(self._get_state_key(session_id))
        if state:
            return ProfileState(state.decode('utf-8'))
        return ProfileState.READY  # 默认ready
    
    def is_profile_ready(self, session_id: str) -> bool:
        """检查profile是否ready（可以执行MQTT下行）"""
        state = self.get_profile_state(session_id)
        return state == ProfileState.READY
    
    def enter_switching_state(self, session_id: str):
        """进入switching状态（暂停所有MQTT下行执行）"""
        self.redis.setex(
            self._get_state_key(session_id),
            300,  # 5分钟TTL，防止永久锁定
            ProfileState.SWITCHING.value
        )
        print(f"[ProfileSwitcher] Session {session_id} entered SWITCHING state - MQTT downlink paused")
    
    def enter_ready_state(self, session_id: str):
        """进入ready状态（恢复MQTT下行执行）"""
        self.redis.setex(
            self._get_state_key(session_id),
            3600,  # 1小时TTL
            ProfileState.READY.value
        )
        print(f"[ProfileSwitcher] Session {session_id} entered READY state - MQTT downlink resumed")
    
    def enter_failed_state(self, session_id: str, reason: str):
        """进入failed状态（profile加载失败）"""
        self.redis.setex(
            self._get_state_key(session_id),
            300,
            ProfileState.FAILED.value
        )
        print(f"[ProfileSwitcher] Session {session_id} entered FAILED state: {reason}")
    
    async def switch_profile(
        self,
        session_id: str,
        model_id: str,
        hardware_option: Optional[str] = None,
        config_hash: Optional[str] = None
    ) -> Dict[str, Any]:
        """切换Capability Profile（完整的安全门控流程）
        
        流程：
        1. 进入switching状态（暂停MQTT下行）
        2. 从PostgreSQL加载新profile（source of truth）
        3. 验证profile有效性
        4. 绑定到session
        5. 进入ready状态（恢复MQTT下行）
        6. 发出profile_ready通知
        
        Returns:
            {
                "success": bool,
                "state": "ready" | "failed",
                "profile": {...} | None,
                "message": str
            }
        """
        print(f"[ProfileSwitcher] Starting profile switch for session {session_id} to model {model_id}")
        
        # Step 1: 进入switching状态
        self.enter_switching_state(session_id)
        
        try:
            # Step 2: 从PostgreSQL加载profile（source of truth）
            profile_data = None
            if self.pg_store:
                profile_data = self.pg_store.get_active_capability_profile(
                    model_id=model_id,
                    hardware_option=hardware_option
                )
            
            # Fallback to file-based loader if PG not available
            if not profile_data:
                from .loader import get_capability_loader
                loader = get_capability_loader()
                profile = loader.get_profile(model_id, hardware_option)
                if profile:
                    profile_data = profile.model_dump()
            
            # Step 3: 验证profile有效性
            if not profile_data:
                self.enter_failed_state(session_id, f"Profile not found for model {model_id}")
                return {
                    "success": False,
                    "state": "failed",
                    "profile": None,
                    "message": f"未找到车型 {model_id} 的能力档案"
                }
            
            # Step 4: 绑定到session（写入Redis缓存）
            import json
            self.redis.setex(
                self._get_profile_key(session_id),
                3600 * 24,  # 24小时
                json.dumps(profile_data, ensure_ascii=False)
            )
            
            # Step 5: 进入ready状态
            self.enter_ready_state(session_id)
            
            # Step 6: 发出profile_ready通知（通过返回值）
            print(f"[ProfileSwitcher] Profile switch complete for {model_id}, state: READY")
            
            return {
                "success": True,
                "state": "ready",
                "profile": profile_data,
                "message": f"成功切换到车型 {profile_data.get('model_name', model_id)}"
            }
            
        except Exception as e:
            print(f"[ProfileSwitcher] Profile switch failed: {e}")
            self.enter_failed_state(session_id, str(e))
            return {
                "success": False,
                "state": "failed",
                "profile": None,
                "message": f"切换失败: {str(e)}"
            }
    
    def check_mqtt_downlink_allowed(self, session_id: str) -> tuple[bool, Optional[str]]:
        """检查是否允许MQTT下行执行
        
        Returns:
            (allowed: bool, reason: Optional[str])
        """
        state = self.get_profile_state(session_id)
        
        if state == ProfileState.SWITCHING:
            return False, "Profile正在切换中，MQTT下行已暂停"
        elif state == ProfileState.FAILED:
            return False, "Profile加载失败，MQTT下行已阻止"
        elif state == ProfileState.READY:
            return True, None
        
        return False, f"未知profile状态: {state}"

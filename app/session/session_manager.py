# -*- coding: utf-8 -*-
"""Session manager - handles driver switching and session lifecycle"""

from datetime import datetime
from typing import Dict, Optional, Any
import uuid

from ..schemas.context import SessionInfo
from ..memory import BaseMemoryStore
from ..capabilities import CapabilityProfile, get_capability_loader


class SessionManager:
    """会话管理器"""
    
    def __init__(
        self,
        memory_store: BaseMemoryStore,
        pg_store=None,
        entity_buffer=None
    ):
        self.memory_store = memory_store
        self.pg_store = pg_store
        self.entity_buffer = entity_buffer
        self.active_sessions: Dict[str, SessionInfo] = {}
    
    async def create_session(
        self,
        driver_id: Optional[str] = None,
        vehicle_id: Optional[str] = None,
        vehicle_model: Optional[str] = None
    ) -> SessionInfo:
        """创建新会话"""
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        
        # 加载能力档案
        loader = get_capability_loader()
        model_id = vehicle_model or "model_a"
        capability_profile = loader.get_profile(model_id)
        
        if not capability_profile:
            print(f"[SessionManager] WARNING: Profile not found for {model_id}, using default")
            capability_profile = loader.get_default_profile()
        
        session_info = SessionInfo(
            session_id=session_id,
            driver_id=driver_id,
            vehicle_id=vehicle_id,
            created_at=now,
            last_active=now
        )
        
        self.active_sessions[session_id] = session_info
        
        # 存储能力档案（扩展数据）
        if capability_profile:
            await self.memory_store.set_json(
                f"capability_profile:{session_id}",
                capability_profile.model_dump(),
                expire=3600 * 24
            )
        
        # 持久化（可选）
        await self.memory_store.set_json(
            f"session:{session_id}",
            session_info.model_dump(),
            expire=3600 * 24  # 24小时
        )
        
        return session_info
    
    async def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        # 先查内存
        if session_id in self.active_sessions:
            return self.active_sessions[session_id]
        
        # 再查持久化
        session_data = await self.memory_store.get_json(f"session:{session_id}")
        if session_data:
            session_info = SessionInfo(**session_data)
            self.active_sessions[session_id] = session_info
            return session_info
        
        return None
    
    async def update_session_activity(self, session_id: str):
        """更新会话活跃时间"""
        session_info = await self.get_session(session_id)
        if session_info:
            session_info.last_active = datetime.utcnow().isoformat() + "Z"
            await self.memory_store.set_json(
                f"session:{session_id}",
                session_info.model_dump(),
                expire=3600 * 24
            )
    
    async def switch_driver(
        self,
        session_id: str,
        new_driver_id: str
    ) -> SessionInfo:
        """切换驾驶员（清空影子状态、记忆切片、实体缓冲区、能力档案切片）"""
        session_info = await self.get_session(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")
        
        print(f"[SessionManager] Switching driver from {session_info.driver_id} to {new_driver_id}")
        
        # 更新driver_id
        session_info.driver_id = new_driver_id
        session_info.last_active = datetime.utcnow().isoformat() + "Z"
        
        # 清空影子状态
        await self.memory_store.delete(f"shadow_state:{session_id}")
        
        # 清空记忆切片（会在下次对话时从PostgreSQL重新加载新驾驶员的偏好）
        await self.memory_store.delete(f"memory_slice:{session_id}")
        
        # 清空实体缓冲区（分钟级热数据）
        if self.entity_buffer:
            self.entity_buffer.clear_all(session_id)
            print(f"[SessionManager] Cleared entity buffer for session {session_id}")
        
        # 清空能力档案缓存（会在下次对话时重新加载）
        await self.memory_store.delete(f"capability_profile:{session_id}")
        
        # 保存
        await self.memory_store.set_json(
            f"session:{session_id}",
            session_info.model_dump(),
            expire=3600 * 24
        )
        
        self.active_sessions[session_id] = session_info
        
        print(f"[SessionManager] Driver switch complete, cleared: shadow_state, memory_slice, entity_buffer, capability_profile")
        
        return session_info
    
    async def get_capability_profile(self, session_id: str) -> Optional[CapabilityProfile]:
        """获取会话的能力档案"""
        profile_data = await self.memory_store.get_json(f"capability_profile:{session_id}")
        if profile_data:
            try:
                return CapabilityProfile(**profile_data)
            except Exception as e:
                print(f"[SessionManager] Failed to parse capability profile: {e}")
        return None
    
    async def end_session(self, session_id: str):
        """结束会话"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        
        # 清理Redis缓存
        await self.memory_store.delete(f"session:{session_id}")
        await self.memory_store.delete(f"shadow_state:{session_id}")
        await self.memory_store.delete(f"memory_slice:{session_id}")
        await self.memory_store.delete(f"capability_profile:{session_id}")
        
        # 清理实体缓冲区
        if self.entity_buffer:
            self.entity_buffer.clear_all(session_id)

# -*- coding: utf-8 -*-
"""Context assembler - assembles dialogue context from various sources"""

from typing import Dict, Any, List, Optional

from ..schemas.context import DialogueContext, SessionInfo
from ..memory import BaseMemoryStore
from ..storage.entity_buffer import EntityBuffer


class ContextAssembler:
    """上下文组装器"""
    
    # 最近对话条数
    MAX_RECENT_UTTERANCES = 5
    
    def __init__(self, memory_store: BaseMemoryStore, entity_buffer: Optional[EntityBuffer] = None):
        self.memory_store = memory_store
        self.entity_buffer = entity_buffer
    
    async def assemble(
        self,
        session_info: SessionInfo,
        current_utterance: str,
        telemetry: Optional[Dict[str, Any]] = None
    ) -> DialogueContext:
        """组装对话上下文
        
        Args:
            session_info: 会话信息
            current_utterance: 当前用户话语
            telemetry: 车辆遥测数据（可选）
            
        Returns:
            对话上下文
        """
        session_id = session_info.session_id
        
        # 获取最近对话
        recent_utterances = await self._get_recent_utterances(session_id)
        recent_utterances.append(current_utterance)
        
        # 保持最近N条
        if len(recent_utterances) > self.MAX_RECENT_UTTERANCES:
            recent_utterances = recent_utterances[-self.MAX_RECENT_UTTERANCES:]
        
        # 保存回去
        await self._save_recent_utterances(session_id, recent_utterances)
        
        # 获取影子状态
        shadow_state = await self._get_shadow_state(session_id, telemetry)
        
        # 获取记忆切片
        memory_slice = await self._get_memory_slice(session_id, session_info.driver_id)
        
        # 获取当前位置
        current_location = None
        if telemetry and "latitude" in telemetry and "longitude" in telemetry:
            current_location = {
                "lat": telemetry["latitude"],
                "lon": telemetry["longitude"]
            }
        
        # 获取实体缓冲区（分钟级热数据）
        entity_buffer_data = {}
        if self.entity_buffer:
            entity_buffer_data = self.entity_buffer.get_all(session_id)
        
        context = DialogueContext(
            session_info=session_info,
            recent_utterances=recent_utterances,
            shadow_state=shadow_state,
            memory_slice=memory_slice,
            current_location=current_location,
            entity_buffer=entity_buffer_data
        )
        
        return context
    
    async def _get_recent_utterances(self, session_id: str) -> List[str]:
        """获取最近对话"""
        data = await self.memory_store.get_json(f"utterances:{session_id}")
        if data and isinstance(data, list):
            return data
        return []
    
    async def _save_recent_utterances(self, session_id: str, utterances: List[str]):
        """保存最近对话"""
        await self.memory_store.set_json(
            f"utterances:{session_id}",
            utterances,
            expire=3600 * 24
        )
    
    async def _get_shadow_state(
        self,
        session_id: str,
        telemetry: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """获取影子状态（车辆当前状态）"""
        # 先从memory读取
        shadow = await self.memory_store.get_json(f"shadow_state:{session_id}")
        if not shadow:
            shadow = {}
        
        # 用telemetry更新
        if telemetry:
            shadow.update(telemetry)
            
            # 保存回去
            await self.memory_store.set_json(
                f"shadow_state:{session_id}",
                shadow,
                expire=3600
            )
        
        return shadow
    
    async def _get_memory_slice(
        self,
        session_id: str,
        driver_id: Optional[str]
    ) -> Dict[str, Any]:
        """获取记忆切片（用户偏好等）"""
        memory_slice = {}
        
        # 从driver偏好加载
        if driver_id:
            prefs = await self.memory_store.get_json(f"user_prefs:{driver_id}")
            if prefs:
                memory_slice["user_prefs"] = prefs
            
            # 常用目的地
            destinations = await self.memory_store.get_json(f"frequent_destinations:{driver_id}")
            if destinations:
                memory_slice["frequent_destinations"] = destinations
            
            # 音乐偏好
            music_prefs = await self.memory_store.get_json(f"music_prefs:{driver_id}")
            if music_prefs:
                memory_slice["music_prefs"] = music_prefs
        
        return memory_slice

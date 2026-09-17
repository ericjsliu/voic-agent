# -*- coding: utf-8 -*-
"""Playbook存储 - PRD v1.37 Feature 3

最小化Playbook存储，支持文件和PostgreSQL后端。
在线路径仅读取静态快照，不写入。
"""

import json
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from enum import Enum


class PlaybookSection(str, Enum):
    """Playbook章节"""
    VEHICLE_CONTROL = "vehicle_control"
    NAVIGATION = "navigation"
    MEDIA = "media"
    SAFETY = "safety"
    USER_PREFERENCES = "user_preferences"
    TROUBLESHOOTING = "troubleshooting"


class PlaybookEntry(BaseModel):
    """Playbook条目"""
    id: str  # 唯一ID
    section: PlaybookSection
    content: str  # 规则/建议正文
    helpful_count: int = 0
    harmful_count: int = 0
    confidence_score: float = 0.0  # 0.0-1.0
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = {}


class PlaybookSnapshot(BaseModel):
    """Playbook快照（在线读取用）"""
    version: str  # 例如 "v1.0"
    entries: List[PlaybookEntry]
    snapshot_time: str
    total_entries: int


class PlaybookStore:
    """Playbook存储管理器
    
    支持两种后端：
    1. 文件存储（JSON）- 适合快速原型和小规模部署
    2. PostgreSQL - 适合生产环境和大规模部署
    """
    
    def __init__(self, backend: str = "file", pg_store=None):
        """初始化Playbook存储
        
        Args:
            backend: 存储后端，"file"或"postgres"
            pg_store: PostgreSQL存储实例（backend="postgres"时必需）
        """
        self.backend = backend
        self.pg_store = pg_store
        
        if backend == "file":
            self.snapshot_path = os.getenv(
                "PLAYBOOK_SNAPSHOT_PATH",
                "/tmp/playbook_snapshot.json"
            )
            self.delta_path = os.getenv(
                "PLAYBOOK_DELTA_PATH",
                "/tmp/playbook_deltas.json"
            )
        elif backend == "postgres":
            if not pg_store:
                raise ValueError("pg_store必须提供当backend='postgres'")
    
    def load_snapshot(self) -> Optional[PlaybookSnapshot]:
        """加载最新快照（在线读取路径）
        
        Returns:
            PlaybookSnapshot对象，不存在返回None
        """
        if self.backend == "file":
            return self._load_snapshot_from_file()
        elif self.backend == "postgres":
            return self._load_snapshot_from_pg()
        return None
    
    def get_entries_by_section(
        self,
        section: PlaybookSection,
        min_confidence: float = 0.0
    ) -> List[PlaybookEntry]:
        """按章节获取条目（在线读取路径）
        
        Args:
            section: 章节枚举
            min_confidence: 最小置信度阈值
            
        Returns:
            PlaybookEntry列表
        """
        snapshot = self.load_snapshot()
        if not snapshot:
            return []
        
        return [
            entry for entry in snapshot.entries
            if entry.section == section and entry.confidence_score >= min_confidence
        ]
    
    def search_entries(
        self,
        query: str,
        section: Optional[PlaybookSection] = None,
        limit: int = 5
    ) -> List[PlaybookEntry]:
        """搜索Playbook条目（在线读取路径）
        
        简单的关键词匹配实现。生产环境可用向量搜索。
        
        Args:
            query: 搜索关键词
            section: 限制章节（可选）
            limit: 返回数量
            
        Returns:
            匹配的PlaybookEntry列表
        """
        snapshot = self.load_snapshot()
        if not snapshot:
            return []
        
        matches = []
        query_lower = query.lower()
        
        for entry in snapshot.entries:
            if section and entry.section != section:
                continue
            
            if query_lower in entry.content.lower():
                matches.append(entry)
        
        # 按置信度排序
        matches.sort(key=lambda x: x.confidence_score, reverse=True)
        return matches[:limit]
    
    # ==================== 文件后端实现 ====================
    
    def _load_snapshot_from_file(self) -> Optional[PlaybookSnapshot]:
        """从文件加载快照"""
        try:
            if not os.path.exists(self.snapshot_path):
                print(f"[PlaybookStore] Snapshot文件不存在: {self.snapshot_path}")
                return None
            
            with open(self.snapshot_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return PlaybookSnapshot(**data)
        except Exception as e:
            print(f"[PlaybookStore] 加载snapshot失败: {e}")
            return None
    
    def _save_snapshot_to_file(self, snapshot: PlaybookSnapshot) -> bool:
        """保存快照到文件（离线提升路径）"""
        try:
            with open(self.snapshot_path, 'w', encoding='utf-8') as f:
                json.dump(snapshot.model_dump(), f, indent=2, ensure_ascii=False)
            print(f"[PlaybookStore] Snapshot已保存: {self.snapshot_path}")
            return True
        except Exception as e:
            print(f"[PlaybookStore] 保存snapshot失败: {e}")
            return False
    
    # ==================== PostgreSQL后端实现 ====================
    
    def _load_snapshot_from_pg(self) -> Optional[PlaybookSnapshot]:
        """从PostgreSQL加载最新快照"""
        # TODO: 实现PostgreSQL查询逻辑
        # 查询playbook_snapshots表，取最新version
        print("[PlaybookStore] PostgreSQL backend暂未实现")
        return None
    
    def _save_snapshot_to_pg(self, snapshot: PlaybookSnapshot) -> bool:
        """保存快照到PostgreSQL（离线提升路径）"""
        # TODO: 实现PostgreSQL插入逻辑
        print("[PlaybookStore] PostgreSQL backend暂未实现")
        return False
    
    # ==================== 离线管理接口 ====================
    
    def promote_deltas_to_snapshot(
        self,
        delta_ids: List[str],
        new_version: str
    ) -> bool:
        """提升Delta条目到快照（离线CLI/作业调用）
        
        Args:
            delta_ids: 要提升的Delta ID列表
            new_version: 新快照版本号
            
        Returns:
            是否成功提升
        """
        if self.backend == "file":
            return self._promote_deltas_file(delta_ids, new_version)
        elif self.backend == "postgres":
            return self._promote_deltas_pg(delta_ids, new_version)
        return False
    
    def _promote_deltas_file(self, delta_ids: List[str], new_version: str) -> bool:
        """文件后端的Delta提升实现"""
        try:
            # 加载当前快照
            current_snapshot = self._load_snapshot_from_file()
            if not current_snapshot:
                # 创建初始快照
                current_snapshot = PlaybookSnapshot(
                    version="v0.0",
                    entries=[],
                    snapshot_time=datetime.utcnow().isoformat() + "Z",
                    total_entries=0
                )
            
            # 加载待提升的Deltas
            if not os.path.exists(self.delta_path):
                print("[PlaybookStore] Delta文件不存在")
                return False
            
            with open(self.delta_path, 'r', encoding='utf-8') as f:
                deltas = json.load(f)
            
            # 提升Delta到entries
            promoted_count = 0
            remaining_deltas = []
            
            for delta in deltas:
                if delta.get("id") in delta_ids:
                    # 转换Delta为Entry
                    entry = PlaybookEntry(
                        id=delta["id"],
                        section=PlaybookSection(delta["section"]),
                        content=delta["content"],
                        helpful_count=0,
                        harmful_count=0,
                        confidence_score=delta.get("confidence_score", 0.8),
                        created_at=delta.get("created_at", datetime.utcnow().isoformat() + "Z"),
                        updated_at=datetime.utcnow().isoformat() + "Z",
                        metadata=delta.get("metadata", {})
                    )
                    current_snapshot.entries.append(entry)
                    promoted_count += 1
                else:
                    remaining_deltas.append(delta)
            
            # 更新快照元数据
            current_snapshot.version = new_version
            current_snapshot.snapshot_time = datetime.utcnow().isoformat() + "Z"
            current_snapshot.total_entries = len(current_snapshot.entries)
            
            # 保存新快照
            self._save_snapshot_to_file(current_snapshot)
            
            # 更新Delta文件（移除已提升的）
            with open(self.delta_path, 'w', encoding='utf-8') as f:
                json.dump(remaining_deltas, f, indent=2, ensure_ascii=False)
            
            print(f"[PlaybookStore] 提升了{promoted_count}个Delta到快照{new_version}")
            return True
            
        except Exception as e:
            print(f"[PlaybookStore] Delta提升失败: {e}")
            return False
    
    def _promote_deltas_pg(self, delta_ids: List[str], new_version: str) -> bool:
        """PostgreSQL后端的Delta提升实现"""
        # TODO: 实现PostgreSQL提升逻辑
        print("[PlaybookStore] PostgreSQL backend暂未实现")
        return False

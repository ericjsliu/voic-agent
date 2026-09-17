# -*- coding: utf-8 -*-
"""Playbook离线生成管道 - PRD v1.37 Feature 3

Generator/Reflector/Curator接口 + 脚本/作业，可离线追加Delta条目。
仅通过显式提升合并到快照（即使提升目前是CLI）。
"""

import json
import os
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from abc import ABC, abstractmethod

from .store import PlaybookSection


class PlaybookDelta(BaseModel):
    """Playbook Delta条目（离线生成的候选）"""
    id: str
    section: PlaybookSection
    content: str
    confidence_score: float  # 0.0-1.0
    created_at: str
    metadata: Dict[str, Any] = {}
    # 离线评估字段
    reviewed: bool = False
    approved: bool = False
    reviewer: Optional[str] = None
    review_notes: Optional[str] = None


class PlaybookGenerator(ABC):
    """Playbook生成器接口（离线LLM调用）
    
    从历史对话/audit events中提取有用的规则和建议。
    """
    
    @abstractmethod
    def generate_from_audit_events(
        self,
        events: List[Dict[str, Any]],
        min_confidence: float = 0.7
    ) -> List[PlaybookDelta]:
        """从审计事件中生成Playbook条目
        
        Args:
            events: 审计事件列表
            min_confidence: 最小置信度阈值
            
        Returns:
            PlaybookDelta列表
        """
        pass
    
    @abstractmethod
    def generate_from_conversation(
        self,
        utterances: List[str],
        responses: List[str],
        context: Dict[str, Any]
    ) -> List[PlaybookDelta]:
        """从对话历史中生成Playbook条目
        
        Args:
            utterances: 用户话语列表
            responses: 系统响应列表
            context: 上下文信息
            
        Returns:
            PlaybookDelta列表
        """
        pass


class PlaybookReflector(ABC):
    """Playbook反思器接口（离线LLM分析）
    
    评估现有Playbook条目的有效性，标记需要更新或删除的内容。
    """
    
    @abstractmethod
    def reflect_on_entry(
        self,
        entry_id: str,
        recent_usage: List[Dict[str, Any]],
        user_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """反思单个Playbook条目的有效性
        
        Args:
            entry_id: 条目ID
            recent_usage: 最近使用记录
            user_feedback: 用户反馈（helpful/harmful计数等）
            
        Returns:
            反思结果字典，包含建议的操作（保留/更新/删除）
        """
        pass
    
    @abstractmethod
    def batch_reflect(
        self,
        entries: List[Dict[str, Any]],
        time_window_days: int = 30
    ) -> List[Dict[str, Any]]:
        """批量反思多个条目
        
        Args:
            entries: 条目列表
            time_window_days: 评估时间窗口（天）
            
        Returns:
            反思结果列表
        """
        pass


class PlaybookCurator(ABC):
    """Playbook策展器接口（离线人工审核 + LLM辅助）
    
    最终决策层：审核Delta条目，决定是否提升到快照。
    """
    
    @abstractmethod
    def review_delta(
        self,
        delta_id: str,
        reviewer: str,
        approved: bool,
        notes: Optional[str] = None
    ) -> bool:
        """审核单个Delta条目
        
        Args:
            delta_id: Delta条目ID
            reviewer: 审核人
            approved: 是否批准
            notes: 审核备注
            
        Returns:
            是否成功记录审核
        """
        pass
    
    @abstractmethod
    def list_pending_deltas(
        self,
        section: Optional[PlaybookSection] = None,
        min_confidence: float = 0.0
    ) -> List[PlaybookDelta]:
        """列出待审核的Delta条目
        
        Args:
            section: 限制章节（可选）
            min_confidence: 最小置信度
            
        Returns:
            PlaybookDelta列表
        """
        pass
    
    @abstractmethod
    def approve_batch(
        self,
        delta_ids: List[str],
        reviewer: str
    ) -> int:
        """批量批准Delta条目
        
        Args:
            delta_ids: Delta ID列表
            reviewer: 审核人
            
        Returns:
            成功批准的数量
        """
        pass


# ==================== 默认实现（文件后端） ====================

class FileBasedGenerator(PlaybookGenerator):
    """基于文件的Playbook生成器（简单实现）"""
    
    def __init__(self, output_path: str = "/tmp/playbook_deltas.json"):
        self.output_path = output_path
    
    def generate_from_audit_events(
        self,
        events: List[Dict[str, Any]],
        min_confidence: float = 0.7
    ) -> List[PlaybookDelta]:
        """从审计事件中生成条目（简化实现）"""
        deltas = []
        
        # 示例：检测频繁的拒绝模式
        refusal_events = [e for e in events if e.get("event_type") == "refusal"]
        if len(refusal_events) > 5:
            # 生成一条Playbook规则
            delta = PlaybookDelta(
                id=f"delta_{uuid.uuid4().hex[:8]}",
                section=PlaybookSection.SAFETY,
                content="检测到频繁拒绝事件，建议检查约束规则配置",
                confidence_score=min(0.9, len(refusal_events) / 10),
                created_at=datetime.utcnow().isoformat() + "Z",
                metadata={"refusal_count": len(refusal_events)}
            )
            deltas.append(delta)
        
        return deltas
    
    def generate_from_conversation(
        self,
        utterances: List[str],
        responses: List[str],
        context: Dict[str, Any]
    ) -> List[PlaybookDelta]:
        """从对话历史中生成条目（占位实现）"""
        # 实际实现需要LLM调用提取模式
        return []
    
    def save_deltas(self, deltas: List[PlaybookDelta]) -> bool:
        """保存Delta到文件"""
        try:
            existing = []
            if os.path.exists(self.output_path):
                with open(self.output_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            
            # 追加新Delta
            for delta in deltas:
                existing.append(delta.model_dump())
            
            with open(self.output_path, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            
            print(f"[PlaybookGenerator] 保存了{len(deltas)}个Delta到{self.output_path}")
            return True
            
        except Exception as e:
            print(f"[PlaybookGenerator] 保存Delta失败: {e}")
            return False


class FileBasedReflector(PlaybookReflector):
    """基于文件的Playbook反思器（简单实现）"""
    
    def reflect_on_entry(
        self,
        entry_id: str,
        recent_usage: List[Dict[str, Any]],
        user_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """反思单个条目（简化实现）"""
        # 简单评分逻辑：基于helpful/harmful比率
        if user_feedback:
            helpful = user_feedback.get("helpful_count", 0)
            harmful = user_feedback.get("harmful_count", 0)
            total = helpful + harmful
            
            if total == 0:
                action = "keep"
                reason = "无用户反馈"
            elif harmful > helpful * 2:
                action = "delete"
                reason = f"负面反馈过多（{harmful} harmful vs {helpful} helpful）"
            elif helpful > harmful * 3:
                action = "keep"
                reason = f"正面反馈良好（{helpful} helpful vs {harmful} harmful）"
            else:
                action = "review"
                reason = "反馈混合，需人工审核"
        else:
            action = "keep"
            reason = "无反馈数据"
        
        return {
            "entry_id": entry_id,
            "action": action,
            "reason": reason,
            "confidence": 0.5
        }
    
    def batch_reflect(
        self,
        entries: List[Dict[str, Any]],
        time_window_days: int = 30
    ) -> List[Dict[str, Any]]:
        """批量反思（简化实现）"""
        results = []
        for entry in entries:
            result = self.reflect_on_entry(
                entry["id"],
                [],
                entry.get("user_feedback")
            )
            results.append(result)
        return results


class FileBasedCurator(PlaybookCurator):
    """基于文件的Playbook策展器（简单实现）"""
    
    def __init__(self, delta_path: str = "/tmp/playbook_deltas.json"):
        self.delta_path = delta_path
    
    def review_delta(
        self,
        delta_id: str,
        reviewer: str,
        approved: bool,
        notes: Optional[str] = None
    ) -> bool:
        """审核单个Delta"""
        try:
            if not os.path.exists(self.delta_path):
                print(f"[PlaybookCurator] Delta文件不存在: {self.delta_path}")
                return False
            
            with open(self.delta_path, 'r', encoding='utf-8') as f:
                deltas = json.load(f)
            
            # 找到并更新Delta
            found = False
            for delta in deltas:
                if delta["id"] == delta_id:
                    delta["reviewed"] = True
                    delta["approved"] = approved
                    delta["reviewer"] = reviewer
                    delta["review_notes"] = notes
                    found = True
                    break
            
            if not found:
                print(f"[PlaybookCurator] Delta {delta_id} 未找到")
                return False
            
            # 保存更新
            with open(self.delta_path, 'w', encoding='utf-8') as f:
                json.dump(deltas, f, indent=2, ensure_ascii=False)
            
            print(f"[PlaybookCurator] Delta {delta_id} 已审核：{'批准' if approved else '拒绝'}")
            return True
            
        except Exception as e:
            print(f"[PlaybookCurator] 审核失败: {e}")
            return False
    
    def list_pending_deltas(
        self,
        section: Optional[PlaybookSection] = None,
        min_confidence: float = 0.0
    ) -> List[PlaybookDelta]:
        """列出待审核的Delta"""
        try:
            if not os.path.exists(self.delta_path):
                return []
            
            with open(self.delta_path, 'r', encoding='utf-8') as f:
                deltas = json.load(f)
            
            pending = []
            for delta_dict in deltas:
                if delta_dict.get("reviewed", False):
                    continue
                
                if section and delta_dict["section"] != section.value:
                    continue
                
                if delta_dict.get("confidence_score", 0.0) < min_confidence:
                    continue
                
                delta = PlaybookDelta(**delta_dict)
                pending.append(delta)
            
            return pending
            
        except Exception as e:
            print(f"[PlaybookCurator] 列出待审核Delta失败: {e}")
            return []
    
    def approve_batch(
        self,
        delta_ids: List[str],
        reviewer: str
    ) -> int:
        """批量批准Delta"""
        approved_count = 0
        for delta_id in delta_ids:
            if self.review_delta(delta_id, reviewer, True, "批量批准"):
                approved_count += 1
        return approved_count

# -*- coding: utf-8 -*-
"""Knowledge domain adapter - Hybrid RAG only"""

from typing import Dict, Any, Optional, List
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, KnowledgeAction
from ..rag_client import HybridRAGClient, RAGHit


class KnowledgeAdapter(BaseDomainAdapter):
    """知识查询适配器（仅文本，调用外部Hybrid RAG服务）"""
    
    def __init__(self, rag_client: HybridRAGClient, audit_logger=None):
        self.rag_client = rag_client
        self.audit_logger = audit_logger
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证知识查询步骤"""
        action: KnowledgeAction = step.action
        
        if not action.query:
            return False, "query_manual requires query"
        
        return True, None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行知识查询（调用Hybrid RAG服务 - Real或Mock API）"""
        action: KnowledgeAction = step.action
        session_id = context.get("session_id", "unknown")
        
        # 获取item_names（车型名称）- 必须限定当前车型，不允许跨车型查询
        item_names = self._get_item_names(context, action)
        
        # 调用统一查询接口（自动选择Real或Mock API）
        result = await self.rag_client.query(
            query=action.query,
            session_id=session_id,
            item_names=item_names,
            model_filter=action.model_filter,
            version_filter=action.version_filter,
            top_k=5
        )
        
        answer = result.get("answer", "")
        citations = result.get("citations", [])
        citation_count = result.get("citation_count", len(citations))
        
        # PRD v1.11 弱出处：有 answer 即可；无 answer 才拒答（禁止掉 chitchat）
        if not (answer or "").strip():
            print(f"[KnowledgeAdapter] RAG miss (empty answer): {action.query}")
            if self.audit_logger:
                trace_id = context.get("trace_id") or context.get("task_id", "")
                if trace_id:
                    from ..audit import AuditEventType
                    self.audit_logger.create_event(
                        trace_id=trace_id,
                        session_id=session_id,
                        event_type=AuditEventType.RAG_MISS,
                        query=action.query,
                        metadata={
                            "item_names": item_names,
                            "retrieval_mode": result.get("retrieval_mode", "unknown")
                        }
                    )
            return {
                "step_id": step.step_id,
                "domain": "knowledge",
                "action": "query_manual",
                "status": "not_found",
                "answer": "抱歉，手册中未找到相关信息。",
                "citations": [],
                "error": None
            }

        if citation_count == 0:
            citations = [{
                "source": "hybrid_rag",
                "title": "用车问答",
                "ref": result.get("session_id") or session_id,
                "doc_id": "hybrid_rag",
            }]
            citation_count = 1
            print(f"[KnowledgeAdapter] Weak citation injected for query: {action.query}")
        
        # 有引用：正常返回
        response = {
            "step_id": step.step_id,
            "domain": "knowledge",
            "action": "query_manual",
            "query": action.query,
            "answer": answer,
            "citations": citations,  # 必须包含引用
            "status": "success"
        }
        
        # Emit audit event: rag_hit (P0 exit #5)
        if self.audit_logger:
            trace_id = context.get("trace_id") or context.get("task_id", "")
            if trace_id:
                from ..audit import AuditEventType
                self.audit_logger.create_event(
                    trace_id=trace_id,
                    session_id=session_id,
                    event_type=AuditEventType.RAG_HIT,
                    query=action.query,
                    metadata={
                        "citation_count": citation_count,
                        "top_score": citations[0].get("score", 0) if citations else 0,
                        "retrieval_mode": result.get("retrieval_mode", "unknown"),
                        "item_names": item_names
                    }
                )
        
        print(f"[KnowledgeAdapter] Query successful with {citation_count} citations")
        return response
    
    # 能力档案 model_id / 英文名 → 真 RAG 车型显示名
    _RAG_NAME_MAP = {
        "model_a": ["致享"],
        "model_b": ["致享"],
        "ModelA": ["致享"],
        "ModelB": ["致享"],
        "Model A (Standard)": ["致享"],
        "Model B (Premium)": ["致享"],
    }

    def _get_item_names(self, context: Dict[str, Any], action: KnowledgeAction) -> List[str]:
        """获取真 RAG 的 item_names（须用手册语料车型名，如「致享」）"""
        if context.get("item_names"):
            return list(context["item_names"])
        if context.get("vehicle_model_name"):
            name = context["vehicle_model_name"]
            return self._RAG_NAME_MAP.get(name, [name])

        profile = context.get("capability_profile")
        if profile is not None:
            rag = getattr(profile, "rag_item_names", None)
            if not rag and isinstance(profile, dict):
                rag = profile.get("rag_item_names")
            if rag:
                return list(rag)
            display = getattr(profile, "display_name", None) or (profile.get("display_name") if isinstance(profile, dict) else None)
            if display:
                return [display]
            mid = getattr(profile, "model_id", None) or (profile.get("model_id") if isinstance(profile, dict) else None)
            mname = getattr(profile, "model_name", None) or (profile.get("model_name") if isinstance(profile, dict) else None)
            for key in (mid, mname):
                if key and key in self._RAG_NAME_MAP:
                    return self._RAG_NAME_MAP[key]

        if action.model_filter:
            return self._RAG_NAME_MAP.get(action.model_filter, [action.model_filter])

        print("[KnowledgeAdapter] WARNING: fallback item_names=致享")
        return ["致享"]
    
    def _generate_answer_from_hits(self, query: str, hits: List[RAGHit]) -> str:
        """从检索结果生成答案（简单拼接，真实场景可用LLM生成）"""
        # v1简单实现：直接返回最相关的片段
        if not hits:
            return "未找到相关信息。"
        
        # 取top 3最相关的片段
        top_hits = sorted(hits, key=lambda h: h.score, reverse=True)[:3]
        
        answer_parts = []
        for i, hit in enumerate(top_hits, 1):
            answer_parts.append(f"[来源{i}] {hit.text}")
        
        return "\n\n".join(answer_parts)

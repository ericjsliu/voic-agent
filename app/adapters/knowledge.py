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
        
        # **Citation Enforcement**: 无引用则不能作为手册权威回答
        if citation_count == 0:
            print(f"[KnowledgeAdapter] No citations found for query: {action.query}")
            
            # Emit audit event: rag_miss (P0 exit #5)
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
            
            # 允许返回answer但带警告（无结构化引用）
            return {
                "step_id": step.step_id,
                "domain": "knowledge",
                "action": "query_manual",
                "status": "no_citations",
                "answer": answer or "抱歉，未能在用户手册中找到带引用的可靠信息",
                "citations": [],
                "warning": "⚠️ 无结构化引用，建议人工核实",
                "error": None
            }
        
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
    
    def _get_item_names(self, context: Dict[str, Any], action: KnowledgeAction) -> List[str]:
        """获取车型名称列表（Real API必需）
        
        优先级：
        1. context中的vehicle_model_name / item_names
        2. context中的capability_profile.model_name
        3. action中的model_filter
        4. fallback: ["通用车型"]
        """
        # 从context获取（最准确）
        if "vehicle_model_name" in context:
            return [context["vehicle_model_name"]]
        
        if "item_names" in context:
            return context["item_names"]
        
        # 从capability_profile获取
        if "capability_profile" in context:
            profile = context["capability_profile"]
            if hasattr(profile, "model_name"):
                return [profile.model_name]
            elif isinstance(profile, dict) and "model_name" in profile:
                return [profile["model_name"]]
        
        # 从action获取（兼容旧版）
        if action.model_filter:
            return [action.model_filter]
        
        # Fallback
        print("[KnowledgeAdapter] WARNING: No vehicle model found, using generic")
        return ["通用车型"]
    
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

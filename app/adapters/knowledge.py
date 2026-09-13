# -*- coding: utf-8 -*-
"""Knowledge domain adapter - Hybrid RAG only"""

from typing import Dict, Any, Optional, List
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, KnowledgeAction
from ..rag_client import HybridRAGClient, RAGHit


class KnowledgeAdapter(BaseDomainAdapter):
    """知识查询适配器（仅文本，调用外部Hybrid RAG服务）"""
    
    def __init__(self, rag_client: HybridRAGClient):
        self.rag_client = rag_client
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证知识查询步骤"""
        action: KnowledgeAction = step.action
        
        if not action.query:
            return False, "query_manual requires query"
        
        return True, None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行知识查询（调用Hybrid RAG服务）"""
        action: KnowledgeAction = step.action
        
        # 调用外部RAG服务
        hits: List[RAGHit] = await self.rag_client.hybrid_search(
            query=action.query,
            model_filter=action.model_filter,
            version_filter=action.version_filter,
            top_k=5
        )
        
        if not hits:
            # **Citation Enforcement**: 无引用则不能作为手册权威回答
            print(f"[KnowledgeAdapter] No citations found for query: {action.query}")
            return {
                "step_id": step.step_id,
                "domain": "knowledge",
                "action": "query_manual",
                "status": "no_citations",
                "answer": None,
                "citations": [],
                "error": "抱歉，未能在用户手册中找到带引用的可靠信息"
            }
        
        # 生成答案（基于检索结果）
        answer = self._generate_answer_from_hits(action.query, hits)
        
        # 提取引用
        citations = [
            {
                "doc_id": hit.citation.doc_id,
                "section": hit.citation.section,
                "page": hit.citation.page,
                "anchor": hit.citation.anchor,
                "score": hit.score,
            }
            for hit in hits
        ]
        
        # **Citation Enforcement**: 必须有citations才能返回答案
        if not citations:
            print(f"[KnowledgeAdapter] WARNING: Hits found but no valid citations")
            return {
                "step_id": step.step_id,
                "domain": "knowledge",
                "action": "query_manual",
                "status": "no_citations",
                "answer": None,
                "citations": [],
                "error": "找到相关内容但缺少引用信息，无法作为权威回答"
            }
        
        result = {
            "step_id": step.step_id,
            "domain": "knowledge",
            "action": "query_manual",
            "query": action.query,
            "answer": answer,
            "citations": citations,  # 必须包含引用
            "status": "success"
        }
        
        print(f"[KnowledgeAdapter] Query successful with {len(citations)} citations")
        return result
    
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

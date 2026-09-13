# -*- coding: utf-8 -*-
"""Tests for Knowledge Citation Enforcement"""

import pytest
from app.adapters.knowledge import KnowledgeAdapter
from app.schemas.taskgraph import Step, KnowledgeAction, DomainType, ActionLevel
from app.rag_client import RAGHit, Citation


class MockRAGClient:
    """Mock RAG client for testing"""
    def __init__(self, return_hits=True):
        self.return_hits = return_hits
    
    async def hybrid_search(self, query, model_filter=None, version_filter=None, top_k=5):
        if not self.return_hits:
            return []
        
        # 返回带引用的结果
        return [
            RAGHit(
                text="空调使用方法：...",
                score=0.9,
                citation=Citation(
                    doc_id="manual_001",
                    section="空调",
                    page=42,
                    anchor="ac-usage"
                )
            )
        ]


@pytest.mark.asyncio
async def test_knowledge_with_citations():
    """测试有引用的知识查询成功"""
    rag_client = MockRAGClient(return_hits=True)
    adapter = KnowledgeAdapter(rag_client)
    
    step = Step(
        step_id="s1",
        domain=DomainType.KNOWLEDGE,
        action=KnowledgeAction(
            action="query_manual",
            query="如何使用空调",
            level=ActionLevel.L0
        ),
        description="查询空调使用方法"
    )
    
    result = await adapter.execute(step, {})
    
    assert result["status"] == "success"
    assert result["answer"] is not None
    assert result["citations"] is not None
    assert len(result["citations"]) > 0
    assert result["citations"][0]["doc_id"] == "manual_001"


@pytest.mark.asyncio
async def test_knowledge_no_citations_rejected():
    """测试无引用的知识查询被拒绝"""
    rag_client = MockRAGClient(return_hits=False)
    adapter = KnowledgeAdapter(rag_client)
    
    step = Step(
        step_id="s1",
        domain=DomainType.KNOWLEDGE,
        action=KnowledgeAction(
            action="query_manual",
            query="不存在的问题",
            level=ActionLevel.L0
        ),
        description="查询"
    )
    
    result = await adapter.execute(step, {})
    
    # 应该被拒绝，因为没有citations
    assert result["status"] == "no_citations"
    assert result["answer"] is None
    assert result["citations"] == []
    assert "error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

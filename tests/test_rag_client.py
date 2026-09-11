# -*- coding: utf-8 -*-
"""测试RAG Client（需要mock RAG服务运行）"""

import pytest
from app.rag_client import HybridRAGClient


@pytest.mark.asyncio
async def test_rag_client_search():
    """测试RAG客户端：混合检索"""
    # 注意：需要mock RAG服务运行在localhost:8001
    client = HybridRAGClient(base_url="http://localhost:8001")
    
    try:
        # 查询空调相关
        hits = await client.hybrid_search(
            query="如何使用空调",
            model_filter="ModelA",
            top_k=3
        )
        
        # 应该有结果
        assert len(hits) > 0
        
        # 检查结果结构
        for hit in hits:
            assert hit.text
            assert hit.score >= 0
            assert hit.citation.doc_id
        
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rag_client_model_filter():
    """测试RAG客户端：车型过滤"""
    client = HybridRAGClient(base_url="http://localhost:8001")
    
    try:
        # 查询ModelA
        hits_a = await client.hybrid_search(
            query="空调",
            model_filter="ModelA",
            top_k=5
        )
        
        # 查询ModelB
        hits_b = await client.hybrid_search(
            query="空调",
            model_filter="ModelB",
            top_k=5
        )
        
        # 两个模型的结果应该不同（或至少citation不同）
        if hits_a and hits_b:
            assert hits_a[0].citation.doc_id != hits_b[0].citation.doc_id
    
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rag_client_no_results():
    """测试RAG客户端：无结果查询"""
    client = HybridRAGClient(base_url="http://localhost:8001")
    
    try:
        # 查询不相关内容
        hits = await client.hybrid_search(
            query="量子物理学",
            model_filter="ModelA",
            top_k=5
        )
        
        # 应该没有或很少结果
        assert len(hits) == 0 or hits[0].score < 0.3
    
    finally:
        await client.close()

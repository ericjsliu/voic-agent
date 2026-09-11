# -*- coding: utf-8 -*-
"""Hybrid RAG client - HTTP client for external RAG service"""

import os
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import httpx


class RAGCitation(BaseModel):
    """RAG引用"""
    doc_id: str
    section: Optional[str] = None
    page: Optional[int] = None
    anchor: Optional[str] = None


class RAGHit(BaseModel):
    """RAG检索结果"""
    text: str
    score: float
    citation: RAGCitation


class HybridRAGClient:
    """Hybrid RAG HTTP客户端"""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0
    ):
        self.base_url = base_url or os.getenv("HYBRID_RAG_BASE_URL", "http://localhost:8001")
        self.api_key = api_key or os.getenv("HYBRID_RAG_API_KEY")
        self.timeout = timeout
        
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._build_headers()
        )
    
    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    async def hybrid_search(
        self,
        query: str,
        model_filter: Optional[str] = None,
        version_filter: Optional[str] = None,
        top_k: int = 5
    ) -> List[RAGHit]:
        """混合检索
        
        Args:
            query: 查询文本
            model_filter: 车型过滤
            version_filter: 版本过滤
            top_k: 返回top K结果
            
        Returns:
            RAG命中列表
        """
        filters = {}
        if model_filter:
            filters["model"] = model_filter
        if version_filter:
            filters["version"] = version_filter
        
        payload = {
            "query": query,
            "filters": filters,
            "top_k": top_k
        }
        
        try:
            response = await self.client.post("/v1/hybrid-search", json=payload)
            response.raise_for_status()
            data = response.json()
            
            hits = []
            for hit_data in data.get("hits", []):
                citation_data = hit_data.get("citation", {})
                citation = RAGCitation(**citation_data)
                hit = RAGHit(
                    text=hit_data["text"],
                    score=hit_data["score"],
                    citation=citation
                )
                hits.append(hit)
            
            return hits
        
        except httpx.HTTPStatusError as e:
            print(f"RAG HTTP error: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            print(f"RAG client error: {e}")
            return []
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()

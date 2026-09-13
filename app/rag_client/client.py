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
    """Hybrid RAG HTTP客户端
    
    支持两种API模式：
    1. Real API: POST /query/hybrid_rerank (生产环境)
    2. Mock API: POST /v1/hybrid-search (fallback)
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        use_real_api: Optional[bool] = None,
        query_path: Optional[str] = None
    ):
        self.base_url = base_url or os.getenv("HYBRID_RAG_BASE_URL", "http://localhost:8001")
        self.api_key = api_key or os.getenv("HYBRID_RAG_API_KEY")
        self.query_path = query_path or os.getenv("HYBRID_RAG_PATH", "/query/hybrid_rerank")
        self.timeout = timeout
        
        # 自动检测：如果BASE_URL包含"8081"或"mock"，使用mock API
        if use_real_api is None:
            self.use_real_api = not ("8081" in self.base_url or "mock" in self.base_url.lower())
        else:
            self.use_real_api = use_real_api
        
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._build_headers()
        )
        
        print(f"[RAGClient] Initialized: {self.base_url}{self.query_path} (real_api={self.use_real_api})")
    
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
    
    async def query_hybrid_rerank(
        self,
        query: str,
        session_id: str,
        item_names: List[str],
        is_stream: bool = False
    ) -> Dict[str, Any]:
        """查询混合重排（Real RAG API）
        
        Args:
            query: 查询文本
            session_id: 会话ID
            item_names: 车型名称列表（车型显示名，如"致享"）
            is_stream: 是否流式
            
        Returns:
            {
                "message": "处理完成！",
                "session_id": "sess-explicit",
                "answer": "致享应每月至少检查一次...",
                "done_list": [{"doc_id": "...", "section": "...", "page": 123}],
                "retrieval_mode": "hybrid_rerank"
            }
        """
        payload = {
            "query": query,
            "session_id": session_id,
            "item_names": item_names,
            "is_stream": is_stream
        }
        
        try:
            # 使用配置的路径（从HYBRID_RAG_PATH env读取）
            response = await self.client.post(self.query_path, json=payload)
            response.raise_for_status()
            data = response.json()
            
            print(f"[RAGClient] hybrid_rerank success: {len(data.get('done_list', []))} citations (path={self.query_path})")
            return data
        
        except httpx.HTTPStatusError as e:
            print(f"[RAGClient] hybrid_rerank HTTP error: {e.response.status_code} - {e.response.text} (path={self.query_path})")
            return {
                "message": "RAG API error",
                "session_id": session_id,
                "answer": "抱歉，查询手册时遇到问题。",
                "done_list": [],
                "retrieval_mode": "error"
            }
        except Exception as e:
            print(f"[RAGClient] hybrid_rerank client error: {e} (path={self.query_path})")
            return {
                "message": "RAG client error",
                "session_id": session_id,
                "answer": "抱歉，无法连接手册服务。",
                "done_list": [],
                "retrieval_mode": "error"
            }
    
    async def query(
        self,
        query: str,
        session_id: str,
        item_names: Optional[List[str]] = None,
        model_filter: Optional[str] = None,
        version_filter: Optional[str] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """统一查询接口（自动选择Real或Mock API）
        
        Args:
            query: 查询文本
            session_id: 会话ID
            item_names: 车型名称列表（Real API用）
            model_filter: 车型过滤（Mock API用）
            version_filter: 版本过滤（Mock API用）
            top_k: 返回top K结果
            
        Returns:
            统一格式：
            {
                "answer": "回答文本",
                "citations": [{"doc_id": "...", "section": "...", "page": 123}],
                "retrieval_mode": "hybrid_rerank" | "mock"
            }
        """
        if self.use_real_api:
            # Real API: /query/hybrid_rerank
            if not item_names:
                item_names = [model_filter] if model_filter else ["通用车型"]
            
            result = await self.query_hybrid_rerank(
                query=query,
                session_id=session_id,
                item_names=item_names
            )
            
            # 转换done_list到citations格式
            citations = []
            for item in result.get("done_list", []):
                citation = {
                    "doc_id": item.get("doc_id", "unknown"),
                    "section": item.get("section"),
                    "page": item.get("page")
                }
                # 过滤None值
                citations.append({k: v for k, v in citation.items() if v is not None})
            
            return {
                "answer": result.get("answer", ""),
                "citations": citations,
                "retrieval_mode": result.get("retrieval_mode", "hybrid_rerank"),
                "citation_count": len(citations)
            }
        
        else:
            # Mock API: /v1/hybrid-search (fallback)
            hits = await self.hybrid_search(
                query=query,
                model_filter=model_filter,
                version_filter=version_filter,
                top_k=top_k
            )
            
            if not hits:
                return {
                    "answer": "抱歉，在手册中未找到相关信息。",
                    "citations": [],
                    "retrieval_mode": "mock",
                    "citation_count": 0
                }
            
            # 从hits构建answer和citations
            answer = hits[0].text if hits else "未找到相关信息"
            citations = [
                {
                    "doc_id": hit.citation.doc_id,
                    "section": hit.citation.section,
                    "page": hit.citation.page
                }
                for hit in hits[:3]  # 取前3个作为citations
            ]
            
            return {
                "answer": answer,
                "citations": citations,
                "retrieval_mode": "mock",
                "citation_count": len(citations)
            }
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()

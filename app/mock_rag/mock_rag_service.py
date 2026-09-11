#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mock Hybrid RAG Service - provides sample car manual data"""

import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn


# ==================== Schemas ====================
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


class HybridSearchRequest(BaseModel):
    """混合检索请求"""
    query: str
    filters: Dict[str, str] = {}
    top_k: int = 5


class HybridSearchResponse(BaseModel):
    """混合检索响应"""
    hits: List[RAGHit]


# ==================== Mock数据库 ====================
MOCK_MANUAL_CHUNKS = [
    # 车型A手册
    {
        "text": "空调系统：按下AC按钮可开启或关闭空调。通过旋转温度旋钮可设置目标温度，范围为16-30°C。空调系统会自动调节风速以达到目标温度。",
        "model": "ModelA",
        "version": "2024",
        "doc_id": "manual_modelA_2024",
        "section": "第3章 空调系统",
        "page": 45,
        "anchor": "ac_control"
    },
    {
        "text": "车窗控制：驾驶员侧车门上有主控制开关，可控制所有车窗。按下对应按钮可开启或关闭车窗。长按可完全开启或关闭。注意：车辆行驶时不建议完全开启车窗。",
        "model": "ModelA",
        "version": "2024",
        "doc_id": "manual_modelA_2024",
        "section": "第2章 车身控制",
        "page": 23,
        "anchor": "window_control"
    },
    {
        "text": "中控锁系统：按下车门上的锁止按钮可锁定所有车门。使用钥匙或遥控器也可远程锁车。锁车前请确保档位在P档。锁车后会听到'滴'的提示音。",
        "model": "ModelA",
        "version": "2024",
        "doc_id": "manual_modelA_2024",
        "section": "第2章 车身控制",
        "page": 28,
        "anchor": "door_lock"
    },
    {
        "text": "天窗操作：按下天窗控制按钮可开启或关闭天窗。天窗有防夹功能，如遇阻力会自动停止。注意：下雨天请关闭天窗，车速超过100km/h时不建议开启天窗。",
        "model": "ModelA",
        "version": "2024",
        "doc_id": "manual_modelA_2024",
        "section": "第2章 车身控制",
        "page": 31,
        "anchor": "sunroof"
    },
    {
        "text": "座椅加热功能：在寒冷天气下，可使用座椅加热功能提升舒适性。按下座椅加热按钮，指示灯亮起表示功能开启。座椅会在10分钟内加热到适宜温度。",
        "model": "ModelA",
        "version": "2024",
        "doc_id": "manual_modelA_2024",
        "section": "第4章 座椅与舒适性",
        "page": 52,
        "anchor": "seat_heating"
    },
    
    # 车型B手册
    {
        "text": "空调系统（高级版）：本车配备智能恒温空调系统。设定温度后，系统会自动调节风量、风向和温度。支持双区控制，主副驾可独立设置温度。温度范围18-28°C。",
        "model": "ModelB",
        "version": "2024",
        "doc_id": "manual_modelB_2024",
        "section": "第5章 空调与舒适性",
        "page": 67,
        "anchor": "ac_system"
    },
    {
        "text": "智能车窗：本车配备防夹车窗系统。在关闭过程中如检测到阻力，车窗会自动反向打开。主驾侧有一键升降功能，轻触按钮即可完全开启或关闭车窗。",
        "model": "ModelB",
        "version": "2024",
        "doc_id": "manual_modelB_2024",
        "section": "第3章 车身功能",
        "page": 38,
        "anchor": "window_system"
    },
    {
        "text": "导航系统：车载导航支持语音输入目的地。说出'导航到XX'即可开始导航。系统会根据实时路况自动选择最佳路线。您可以在设置中选择避开高速或收费站。",
        "model": "ModelB",
        "version": "2024",
        "doc_id": "manual_modelB_2024",
        "section": "第8章 信息娱乐系统",
        "page": 112,
        "anchor": "navigation"
    },
    {
        "text": "后备箱开启：按住遥控器上的后备箱按钮2秒，后备箱会自动打开。也可通过中控屏幕或车内按钮开启。注意：开启后备箱前请确保档位在P档且车辆静止。",
        "model": "ModelB",
        "version": "2024",
        "doc_id": "manual_modelB_2024",
        "section": "第3章 车身功能",
        "page": 42,
        "anchor": "trunk"
    },
]


# ==================== FastAPI App ====================
app = FastAPI(title="Mock Hybrid RAG Service")


def calculate_similarity(query: str, text: str) -> float:
    """简单的相似度计算（关键词匹配）"""
    query_lower = query.lower()
    text_lower = text.lower()
    
    # 简单计数匹配的字符
    matches = sum(1 for char in query_lower if char in text_lower)
    score = matches / max(len(query_lower), 1)
    
    # 如果有完整关键词匹配，提升分数
    keywords = ["空调", "车窗", "锁车", "天窗", "座椅", "导航", "后备箱", "加热"]
    for keyword in keywords:
        if keyword in query and keyword in text:
            score += 0.3
    
    return min(score, 1.0)


@app.post("/v1/hybrid-search", response_model=HybridSearchResponse)
async def hybrid_search(request: HybridSearchRequest):
    """混合检索端点"""
    query = request.query
    filters = request.filters
    top_k = request.top_k
    
    # 过滤候选
    candidates = []
    for chunk in MOCK_MANUAL_CHUNKS:
        # 应用过滤器
        if filters.get("model") and chunk["model"] != filters["model"]:
            continue
        if filters.get("version") and chunk["version"] != filters["version"]:
            continue
        
        # 计算相似度
        score = calculate_similarity(query, chunk["text"])
        if score > 0.1:  # 最低阈值
            candidates.append({
                "chunk": chunk,
                "score": score
            })
    
    # 排序并取top_k
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = candidates[:top_k]
    
    # 构建响应
    hits = []
    for candidate in top_candidates:
        chunk = candidate["chunk"]
        hit = RAGHit(
            text=chunk["text"],
            score=candidate["score"],
            citation=RAGCitation(
                doc_id=chunk["doc_id"],
                section=chunk.get("section"),
                page=chunk.get("page"),
                anchor=chunk.get("anchor")
            )
        )
        hits.append(hit)
    
    return HybridSearchResponse(hits=hits)


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}


@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Mock Hybrid RAG Service",
        "version": "1.0.0",
        "endpoints": ["/v1/hybrid-search", "/health"]
    }


if __name__ == "__main__":
    port = int(os.getenv("RAG_SERVICE_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)

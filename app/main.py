#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main FastAPI application - Smart Cockpit Voice Dialogue Agent"""

import os
import json
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import paho.mqtt.client as mqtt

from .schemas import TaskGraph, WritebackEnvelope
from .schemas.context import DialogueContext
from .memory import get_memory_store
from .rag_client import HybridRAGClient
from .adapters import (
    VehicleAdapter,
    NavigationAdapter,
    MediaAdapter,
    CalendarAdapter,
    KnowledgeAdapter,
    ChitchatAdapter,
)
from .planner import Planner
from .orchestrator import Orchestrator
from .session import SessionManager, ContextAssembler


# ==================== 全局状态 ====================
class AppState:
    """应用全局状态"""
    def __init__(self):
        self.memory_store = None
        self.session_manager = None
        self.context_assembler = None
        self.rag_client = None
        self.planner = None
        self.orchestrator = None
        self.mqtt_client = None
        self.adapters = {}


app_state = AppState()


# ==================== MQTT ====================
def on_mqtt_connect(client, userdata, flags, rc):
    """MQTT连接回调"""
    if rc == 0:
        print(f"[Agent] Connected to MQTT broker")
        # 订阅上行主题
        client.subscribe("cockpit/agent/writeback")
        client.subscribe("cockpit/agent/telemetry")
        print(f"[Agent] Subscribed to uplink topics")
    else:
        print(f"[Agent] MQTT connection failed with code {rc}")


def on_mqtt_message(client, userdata, msg):
    """MQTT消息回调"""
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        
        if msg.topic == "cockpit/agent/writeback":
            # 处理写回
            writeback = WritebackEnvelope(**payload)
            asyncio.create_task(app_state.orchestrator.handle_writeback(writeback))
        
        elif msg.topic == "cockpit/agent/telemetry":
            # 更新影子状态
            print(f"[Agent] Received telemetry")
            # 可选：更新orchestrator的shadow_state
    
    except Exception as e:
        print(f"[Agent] Error handling MQTT message: {e}")


def publish_taskgraph(taskgraph: TaskGraph):
    """发布TaskGraph到MQTT下行主题"""
    if app_state.mqtt_client and app_state.mqtt_client.is_connected():
        payload = taskgraph.model_dump_json(indent=2)
        app_state.mqtt_client.publish("cockpit/agent/taskgraph", payload)
        print(f"[Agent] Published TaskGraph to MQTT")


# ==================== 生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    # 启动时初始化
    print("[Agent] Initializing...")
    
    # Memory
    app_state.memory_store = get_memory_store()
    
    # Session
    app_state.session_manager = SessionManager(app_state.memory_store)
    app_state.context_assembler = ContextAssembler(app_state.memory_store)
    
    # RAG Client
    app_state.rag_client = HybridRAGClient()
    
    # Adapters
    app_state.adapters = {
        "vehicle": VehicleAdapter(),
        "navigation": NavigationAdapter(),
        "media": MediaAdapter(),
        "calendar": CalendarAdapter(),
        "knowledge": KnowledgeAdapter(app_state.rag_client),
        "chitchat": ChitchatAdapter(),
    }
    
    # Planner
    app_state.planner = Planner(nav_adapter=app_state.adapters["navigation"])
    
    # Orchestrator
    app_state.orchestrator = Orchestrator(
        vehicle_adapter=app_state.adapters["vehicle"],
        nav_adapter=app_state.adapters["navigation"],
        media_adapter=app_state.adapters["media"],
        calendar_adapter=app_state.adapters["calendar"],
        knowledge_adapter=app_state.adapters["knowledge"],
        chitchat_adapter=app_state.adapters["chitchat"],
    )
    
    # MQTT Client
    mqtt_broker = os.getenv("MQTT_BROKER", "localhost")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    app_state.mqtt_client = mqtt.Client(client_id="cockpit_agent")
    app_state.mqtt_client.on_connect = on_mqtt_connect
    app_state.mqtt_client.on_message = on_mqtt_message
    
    try:
        app_state.mqtt_client.connect(mqtt_broker, mqtt_port, 60)
        app_state.mqtt_client.loop_start()
    except Exception as e:
        print(f"[Agent] Failed to connect to MQTT: {e}")
    
    print("[Agent] Initialization complete")
    
    yield
    
    # 关闭时清理
    print("[Agent] Shutting down...")
    if app_state.mqtt_client:
        app_state.mqtt_client.loop_stop()
        app_state.mqtt_client.disconnect()
    if app_state.rag_client:
        await app_state.rag_client.close()
    print("[Agent] Shutdown complete")


app = FastAPI(
    title="Smart Cockpit Voice Dialogue Agent",
    version="1.0.0",
    lifespan=lifespan
)


# ==================== API Schemas ====================
class DialogueRequest(BaseModel):
    """对话请求"""
    session_id: Optional[str] = None
    driver_id: Optional[str] = None
    utterance: str
    telemetry: Optional[Dict[str, Any]] = None


class DialogueResponse(BaseModel):
    """对话响应"""
    session_id: str
    taskgraph: TaskGraph
    timestamp: str


class SessionCreateRequest(BaseModel):
    """创建会话请求"""
    driver_id: Optional[str] = None
    vehicle_id: Optional[str] = None


class SessionResponse(BaseModel):
    """会话响应"""
    session_id: str
    driver_id: Optional[str]
    vehicle_id: Optional[str]
    created_at: str


# ==================== API Endpoints ====================
@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Smart Cockpit Voice Dialogue Agent",
        "version": "1.0.0",
        "architecture": "route C",
        "endpoints": [
            "/health",
            "/session/create",
            "/dialogue",
        ]
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "mqtt_connected": app_state.mqtt_client.is_connected() if app_state.mqtt_client else False
    }


@app.post("/session/create", response_model=SessionResponse)
async def create_session(request: SessionCreateRequest):
    """创建会话"""
    session_info = await app_state.session_manager.create_session(
        driver_id=request.driver_id,
        vehicle_id=request.vehicle_id
    )
    
    return SessionResponse(
        session_id=session_info.session_id,
        driver_id=session_info.driver_id,
        vehicle_id=session_info.vehicle_id,
        created_at=session_info.created_at
    )


@app.post("/dialogue", response_model=DialogueResponse)
async def dialogue(request: DialogueRequest, background_tasks: BackgroundTasks):
    """处理对话（ASR文本输入）"""
    
    # 获取或创建会话
    if request.session_id:
        session_info = await app_state.session_manager.get_session(request.session_id)
        if not session_info:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        session_info = await app_state.session_manager.create_session(
            driver_id=request.driver_id
        )
    
    # 更新活跃时间
    await app_state.session_manager.update_session_activity(session_info.session_id)
    
    # 组装上下文
    context: DialogueContext = await app_state.context_assembler.assemble(
        session_info=session_info,
        current_utterance=request.utterance,
        telemetry=request.telemetry
    )
    
    # 更新orchestrator的shadow_state
    app_state.orchestrator.shadow_state = context.shadow_state
    
    # 规划TaskGraph
    taskgraph: TaskGraph = await app_state.planner.plan(
        user_utterance=request.utterance,
        context=context
    )
    
    # 后台执行TaskGraph
    async def execute_and_publish():
        """执行并发布TaskGraph"""
        try:
            # 执行
            result = await app_state.orchestrator.execute_taskgraph(taskgraph)
            print(f"[Agent] TaskGraph execution result: {result}")
            
            # 发布到MQTT下行
            publish_taskgraph(taskgraph)
            
            # 提交内存写入
            if hasattr(app_state.memory_store, 'commit'):
                await app_state.memory_store.commit()
        
        except Exception as e:
            print(f"[Agent] Error executing TaskGraph: {e}")
    
    background_tasks.add_task(execute_and_publish)
    
    # 立即返回TaskGraph
    return DialogueResponse(
        session_id=session_info.session_id,
        taskgraph=taskgraph,
        timestamp=datetime.utcnow().isoformat() + "Z"
    )


@app.post("/session/{session_id}/switch_driver")
async def switch_driver(session_id: str, driver_id: str):
    """切换驾驶员"""
    try:
        session_info = await app_state.session_manager.switch_driver(session_id, driver_id)
        return {
            "session_id": session_info.session_id,
            "driver_id": session_info.driver_id,
            "message": "Driver switched successfully"
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("AGENT_PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main FastAPI application - Smart Cockpit Voice Dialogue Agent"""

import os
import json
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import paho.mqtt.client as mqtt
import json as json_lib

from .schemas import TaskGraph, WritebackEnvelope
from .schemas.context import DialogueContext
from .memory import get_memory_store
from .rag_client import HybridRAGClient
from .storage import init_db, create_tables, close_db, PostgresStore, EntityBuffer
from .planner.rewrite_cancel import RewriteCancelManager
from .planner.refusal_rules import RefusalRules
from .audit import init_audit_logger, get_audit_logger, AuditEventType
from .adapters import (
    VehicleAdapter,
    NavigationAdapter,
    MediaAdapter,
    CalendarAdapter,
    KnowledgeAdapter,
    ChitchatAdapter,
)
from .planner import Planner
from .planner.capability_wrapper import CapabilityAwarePlanner
from .orchestrator import Orchestrator
from .session import SessionManager, ContextAssembler


# ==================== 全局状态 ====================
class AppState:
    """应用全局状态"""
    def __init__(self):
        self.memory_store = None
        self.pg_store = None
        self.entity_buffer = None
        self.rewrite_cancel_manager = None
        self.session_manager = None
        self.context_assembler = None
        self.rag_client = None
        self.planner = None
        self.orchestrator = None
        self.mqtt_client = None
        self.adapters = {}
        self.websocket_connections: Dict[str, List[WebSocket]] = {}  # session_id -> [ws]
        self.audit_logger = None  # PRD v1.9 / detailed-v2.2: full-chain tracing


app_state = AppState()


class ConnectionManager:
    """WebSocket连接管理器"""
    
    async def connect(self, websocket: WebSocket, session_id: str):
        """连接WebSocket"""
        await websocket.accept()
        if session_id not in app_state.websocket_connections:
            app_state.websocket_connections[session_id] = []
        app_state.websocket_connections[session_id].append(websocket)
        print(f"[WS] Client connected to session {session_id}")
    
    def disconnect(self, websocket: WebSocket, session_id: str):
        """断开WebSocket"""
        if session_id in app_state.websocket_connections:
            app_state.websocket_connections[session_id].remove(websocket)
            if not app_state.websocket_connections[session_id]:
                del app_state.websocket_connections[session_id]
        print(f"[WS] Client disconnected from session {session_id}")
    
    async def broadcast_to_session(self, session_id: str, message: dict):
        """向会话广播消息"""
        if session_id in app_state.websocket_connections:
            disconnected = []
            for ws in app_state.websocket_connections[session_id]:
                try:
                    await ws.send_json(message)
                except:
                    disconnected.append(ws)
            
            # 清理断开的连接
            for ws in disconnected:
                self.disconnect(ws, session_id)


ws_manager = ConnectionManager()


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
    
    # PostgreSQL Database
    try:
        init_db()
        create_tables()
        app_state.pg_store = PostgresStore()
        print("[Agent] PostgreSQL initialized")
    except Exception as e:
        print(f"[Agent] WARNING: PostgreSQL initialization failed: {e}")
        print("[Agent] Continuing with Redis-only mode")
    
    # Audit Logger (PRD v1.9 / detailed-v2.2: full-chain tracing)
    init_audit_logger(pg_store=app_state.pg_store)
    app_state.audit_logger = get_audit_logger()
    print("[Agent] Audit Logger initialized (with PG)" if app_state.pg_store else "[Agent] Audit Logger initialized (logs only)")
    
    # Memory (Hybrid: Redis + PostgreSQL)
    app_state.memory_store = get_memory_store(pg_store=app_state.pg_store)
    
    # Entity Buffer (Redis hot cache)
    redis_client = app_state.memory_store.redis if hasattr(app_state.memory_store, 'redis') else None
    if redis_client:
        app_state.entity_buffer = EntityBuffer(redis_client)
        print("[Agent] Entity Buffer initialized")
    
    # Rewrite/Cancel Manager
    if redis_client:
        app_state.rewrite_cancel_manager = RewriteCancelManager(redis_client)
        print("[Agent] Rewrite/Cancel Manager initialized")
    
    # Session (with Profile Switcher support)
    app_state.session_manager = SessionManager(
        app_state.memory_store,
        pg_store=app_state.pg_store,
        entity_buffer=app_state.entity_buffer,
        redis_client=redis_client  # 用于ProfileSwitchingManager
    )
    app_state.context_assembler = ContextAssembler(
        app_state.memory_store,
        entity_buffer=app_state.entity_buffer
    )
    
    # RAG Client
    app_state.rag_client = HybridRAGClient()
    
    # Adapters (P0 exit #5: pass audit_logger to knowledge adapter)
    app_state.adapters = {
        "vehicle": VehicleAdapter(),
        "navigation": NavigationAdapter(),
        "media": MediaAdapter(),
        "calendar": CalendarAdapter(),
        "knowledge": KnowledgeAdapter(app_state.rag_client, audit_logger=app_state.audit_logger),
        "chitchat": ChitchatAdapter(),
    }
    
    # Planner (wrapped with capability awareness)
    base_planner = Planner(nav_adapter=app_state.adapters["navigation"])
    app_state.planner = CapabilityAwarePlanner(
        base_planner=base_planner,
        audit_logger=app_state.audit_logger
    )
    
    # Orchestrator (P0 exit #5: pass audit_logger)
    app_state.orchestrator = Orchestrator(
        vehicle_adapter=app_state.adapters["vehicle"],
        nav_adapter=app_state.adapters["navigation"],
        media_adapter=app_state.adapters["media"],
        calendar_adapter=app_state.adapters["calendar"],
        knowledge_adapter=app_state.adapters["knowledge"],
        chitchat_adapter=app_state.adapters["chitchat"],
        audit_logger=app_state.audit_logger
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
    
    # Set MQTT publish callback for orchestrator (P0 fix #1: L2 after confirm)
    app_state.orchestrator.mqtt_publish_callback = publish_taskgraph
    
    print("[Agent] Initialization complete")
    
    yield
    
    # 关闭时清理
    print("[Agent] Shutting down...")
    if app_state.mqtt_client:
        app_state.mqtt_client.loop_stop()
        app_state.mqtt_client.disconnect()
    if app_state.rag_client:
        await app_state.rag_client.close()
    if app_state.pg_store:
        close_db()
    print("[Agent] Shutdown complete")


app = FastAPI(
    title="Smart Cockpit Voice Dialogue Agent",
    version="1.0.0",
    lifespan=lifespan
)

# CORS中间件（开发环境）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    vehicle_model: Optional[str] = "model_a"


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
    pg_healthy = False
    if app_state.pg_store:
        try:
            from .storage import get_db
            db = get_db()
            db.execute("SELECT 1")
            db.close()
            pg_healthy = True
        except:
            pass
    
    return {
        "status": "ok",
        "services": {
            "mqtt": app_state.mqtt_client.is_connected() if app_state.mqtt_client else False,
            "redis": app_state.memory_store is not None,
            "postgresql": pg_healthy,
            "entity_buffer": app_state.entity_buffer is not None
        }
    }


@app.post("/session/create", response_model=SessionResponse)
async def create_session(request: SessionCreateRequest):
    """创建会话"""
    session_info = await app_state.session_manager.create_session(
        driver_id=request.driver_id,
        vehicle_id=request.vehicle_id,
        vehicle_model=request.vehicle_model
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
    
    # PRD v1.9 / detailed-v2.2: Generate trace_id at ingress
    trace_id = str(uuid.uuid4())
    
    # 获取或创建会话
    if request.session_id:
        session_info = await app_state.session_manager.get_session(request.session_id)
        if not session_info:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        session_info = await app_state.session_manager.create_session(
            driver_id=request.driver_id
        )
    
    # Audit: utterance_received
    app_state.audit_logger.create_event(
        trace_id=trace_id,
        session_id=session_info.session_id,
        event_type=AuditEventType.UTTERANCE_RECEIVED,
        metadata={"utterance_length": len(request.utterance)}
    )
    
    # 更新活跃时间
    await app_state.session_manager.update_session_activity(session_info.session_id)
    
    # 组装上下文
    context: DialogueContext = await app_state.context_assembler.assemble(
        session_info=session_info,
        current_utterance=request.utterance,
        telemetry=request.telemetry
    )
    
    # Audit: assemble_done
    app_state.audit_logger.create_event(
        trace_id=trace_id,
        session_id=session_info.session_id,
        event_type=AuditEventType.ASSEMBLE_DONE
    )
    
    # 更新orchestrator的shadow_state
    app_state.orchestrator.shadow_state = context.shadow_state
    
    # 获取能力档案
    capability_profile = await app_state.session_manager.get_capability_profile(session_info.session_id)
    
    # Audit: planner_start
    planner_start = datetime.utcnow()
    app_state.audit_logger.create_event(
        trace_id=trace_id,
        session_id=session_info.session_id,
        event_type=AuditEventType.PLANNER_START
    )
    
    # 规划TaskGraph（带能力档案过滤，会发出unsupported audit events）
    taskgraph: TaskGraph = await app_state.planner.plan(
        user_utterance=request.utterance,
        context=context,
        capability_profile=capability_profile,
        trace_id=trace_id,
        session_id=session_info.session_id
    )
    
    # Inject trace_id into TaskGraph (if not already set)
    if not taskgraph.trace_id:
        taskgraph.trace_id = trace_id
    if not taskgraph.session_id:
        taskgraph.session_id = session_info.session_id
    
    # Audit: planner_end
    planner_duration = int((datetime.utcnow() - planner_start).total_seconds() * 1000)
    app_state.audit_logger.create_event(
        trace_id=trace_id,
        session_id=session_info.session_id,
        event_type=AuditEventType.PLANNER_END,
        duration_ms=planner_duration,
        task_id=taskgraph.tasks[0].task_id if taskgraph.tasks else None
    )
    
    # 广播TaskGraph到WebSocket (include trace_id)
    await ws_manager.broadcast_to_session(
        session_info.session_id,
        {
            "type": "taskgraph",
            "trace_id": trace_id,
            "data": json_lib.loads(taskgraph.model_dump_json())
        }
    )
    
    # 后台执行TaskGraph
    async def execute_and_publish():
        """执行并发布TaskGraph"""
        try:
            # 检查profile是否ready（detailed-v1.5 gate）
            if not app_state.session_manager.is_profile_ready(session_info.session_id):
                profile_state = app_state.session_manager.get_profile_state(session_info.session_id)
                print(f"[Agent] MQTT downlink BLOCKED: profile state = {profile_state}")
                
                # Audit: dispatch_blocked
                app_state.audit_logger.create_event(
                    trace_id=trace_id,
                    session_id=session_info.session_id,
                    event_type=AuditEventType.DISPATCH_BLOCKED,
                    reason=f"Profile state: {profile_state}",
                    task_id=taskgraph.tasks[0].task_id if taskgraph.tasks else None
                )
                return
            
            # 自定义writeback回调：广播到WebSocket (include trace_id)
            async def writeback_callback(writeback):
                await ws_manager.broadcast_to_session(
                    session_info.session_id,
                    {
                        "type": "writeback",
                        "trace_id": trace_id,
                        "data": json_lib.loads(writeback.model_dump_json())
                    }
                )
            
            # 执行
            result = await app_state.orchestrator.execute_taskgraph(
                taskgraph,
                writeback_callback=writeback_callback
            )
            print(f"[Agent] TaskGraph execution result: {result}")
            
            # 再次检查profile ready（防止执行期间切换）
            if not app_state.session_manager.is_profile_ready(session_info.session_id):
                print(f"[Agent] MQTT downlink BLOCKED: profile switched during execution")
                
                # Audit: dispatch_blocked
                app_state.audit_logger.create_event(
                    trace_id=trace_id,
                    session_id=session_info.session_id,
                    event_type=AuditEventType.DISPATCH_BLOCKED,
                    reason="Profile switched during execution",
                    task_id=taskgraph.tasks[0].task_id if taskgraph.tasks else None
                )
                return
            
            # Audit: dispatch
            app_state.audit_logger.create_event(
                trace_id=trace_id,
                session_id=session_info.session_id,
                event_type=AuditEventType.DISPATCH,
                task_id=taskgraph.tasks[0].task_id if taskgraph.tasks else None,
                metadata={"step_count": len(taskgraph.tasks[0].steps) if taskgraph.tasks else 0}
            )
            
            # 发布到MQTT下行（仅当profile ready）
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


class SwitchVehicleModelRequest(BaseModel):
    """切换车型请求"""
    model_id: str
    hardware_option: Optional[str] = None
    config_hash: Optional[str] = None


@app.post("/session/{session_id}/switch_vehicle_model")
async def switch_vehicle_model(session_id: str, request: SwitchVehicleModelRequest):
    """切换车型能力档案（detailed-v1.5 Profile Switching Gate）
    
    安全切换流程：
    1. 进入profile_switching状态（暂停MQTT下行）
    2. 从PostgreSQL加载新profile
    3. 验证并绑定到session
    4. 进入profile_ready状态（恢复MQTT下行）
    """
    result = await app_state.session_manager.switch_vehicle_model(
        session_id=session_id,
        new_model_id=request.model_id,
        hardware_option=request.hardware_option,
        config_hash=request.config_hash
    )
    
    # 广播profile状态变化到WebSocket
    await ws_manager.broadcast_to_session(
        session_id,
        {
            "type": "profile_state",
            "data": {
                "state": result["state"],
                "message": result["message"],
                "profile": result.get("profile")
            }
        }
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return result


@app.get("/session/{session_id}/profile_state")
async def get_profile_state(session_id: str):
    """获取当前profile状态（ready/switching/failed）"""
    state = app_state.session_manager.get_profile_state(session_id)
    is_ready = app_state.session_manager.is_profile_ready(session_id)
    
    return {
        "session_id": session_id,
        "state": state,
        "is_ready": is_ready,
        "mqtt_downlink_allowed": is_ready
    }


@app.get("/trace/{trace_id}")
async def get_trace_events(trace_id: str):
    """查询trace_id的审计事件（PRD v1.9 / detailed-v2.2）
    
    返回按时间排序的事件列表，用于测试控制台和调试
    """
    events = app_state.audit_logger.get_by_trace_id(trace_id)
    
    return {
        "trace_id": trace_id,
        "event_count": len(events),
        "events": [e.model_dump(exclude_none=True) for e in events]
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket端点：实时接收TaskGraph和Writeback"""
    await ws_manager.connect(websocket, session_id)
    try:
        while True:
            # 接收客户端消息（心跳或L2确认）
            data = await websocket.receive_json()
            
            if data.get("type") == "l2_confirm":
                # 处理L2确认（P0 fix #2: must include trace_id）
                task_id = data.get("task_id")
                step_id = data.get("step_id")
                branch_id = data.get("branch_id", "main")
                trace_id = data.get("trace_id")  # P0 fix #2: extract trace_id from client
                accepted = data.get("accepted", False)
                
                # 创建writeback
                from .schemas import WritebackEnvelope, WritebackEvent, WritebackStatus
                writeback = WritebackEnvelope(
                    task_id=task_id,
                    step_id=step_id,
                    branch_id=branch_id,
                    trace_id=trace_id,  # P0 fix #2: include trace_id
                    event=WritebackEvent.CONFIRM_RESULT,
                    status=WritebackStatus.ACCEPTED if accepted else WritebackStatus.DECLINED,
                    ts=datetime.utcnow().isoformat() + "Z"
                )
                
                # 传给orchestrator
                await app_state.orchestrator.handle_writeback(writeback)
                
                # 广播结果
                await ws_manager.broadcast_to_session(
                    session_id,
                    {
                        "type": "writeback",
                        "data": json_lib.loads(writeback.model_dump_json())
                    }
                )
    
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, session_id)


# 静态文件服务（Web UI）
import os
web_dist_path = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.exists(web_dist_path):
    app.mount("/ui", StaticFiles(directory=web_dist_path, html=True), name="ui")
    print(f"[Agent] Serving Web UI at /ui")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("AGENT_PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

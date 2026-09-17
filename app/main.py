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
from .memory.p2_memory_service import P2MemoryService
from .memory.active_memory_handler import ActiveMemoryHandler
from .memory.passive_queue import PassiveMemoryCandidateQueue, PassiveMemoryConsumer


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
        self.p2_memory_service = None  # P2长期记忆服务
        self.passive_queue = None  # PRD v1.27: 被动记忆候选队列
        self.passive_consumer = None  # PRD v1.27: 被动记忆消费者


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
    """发布TaskGraph到MQTT下行主题（knowledge/chitchat/calendar查询不上行）"""
    if not (app_state.mqtt_client and app_state.mqtt_client.is_connected()):
        return
    # 过滤纯文本域步骤，避免车端看到执行帧
    from .schemas.taskgraph import DomainType
    text_only = {DomainType.KNOWLEDGE, DomainType.CHITCHAT, DomainType.CALENDAR}
    filtered_tasks = []
    for task in taskgraph.tasks:
        exec_steps = [s for s in task.steps if s.domain not in text_only]
        if not exec_steps:
            continue
        filtered = task.model_copy(deep=True)
        filtered.steps = exec_steps
        filtered_tasks.append(filtered)
    if not filtered_tasks:
        print("[Agent] Skip MQTT publish: no vehicle/nav/media steps")
        return
    filtered_tg = taskgraph.model_copy(deep=True)
    filtered_tg.tasks = filtered_tasks
    payload = filtered_tg.model_dump_json(indent=2)
    app_state.mqtt_client.publish("cockpit/agent/taskgraph", payload)
    print(f"[Agent] Published TaskGraph to MQTT ({sum(len(t.steps) for t in filtered_tasks)} exec steps)")


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
    # ProfileSwitcher/EntityBuffer need raw redis-py client (setex/get), not MemoryStore wrapper
    def _raw_redis_client(store):
        if store is None:
            return None
        if hasattr(store, 'client') and store.client is not None:
            return store.client
        inner = getattr(store, 'redis', None)
        if inner is not None and hasattr(inner, 'client'):
            return inner.client
        return None

    redis_client = _raw_redis_client(app_state.memory_store)
    if redis_client:
        app_state.entity_buffer = EntityBuffer(redis_client)
        print("[Agent] Entity Buffer initialized")
    
    # P2 Memory Service (长期记忆)
    try:
        app_state.p2_memory_service = P2MemoryService(enable_vector=True, start_worker=True)
        print("[Agent] P2 Memory Service initialized (vector enabled, passive worker on)")
    except Exception as e:
        print(f"[Agent] WARNING: P2 Memory Service initialization failed: {e}")
        app_state.p2_memory_service = P2MemoryService(enable_vector=False, start_worker=True)
        print("[Agent] P2 Memory Service initialized (vector disabled, KV only, passive worker on)")
    
    # PRD v1.27: Passive Memory Queue & Consumer
    if redis_client and app_state.p2_memory_service:
        app_state.passive_queue = PassiveMemoryCandidateQueue(redis_client)
        app_state.passive_consumer = PassiveMemoryConsumer(
            queue=app_state.passive_queue,
            p2_memory_service=app_state.p2_memory_service
        )
        print("[Agent] Passive Memory Queue & Consumer initialized")
    
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
        entity_buffer=app_state.entity_buffer,
        p2_memory_service=app_state.p2_memory_service  # 注入P2服务
    )
    
    # RAG Client
    app_state.rag_client = HybridRAGClient()
    
    # Adapters (P0 exit #5: pass audit_logger to knowledge adapter)
    app_state.adapters = {
        "vehicle": VehicleAdapter(),
        "navigation": NavigationAdapter(p2_memory_service=app_state.p2_memory_service),
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
    if app_state.p2_memory_service:
        try:
            app_state.p2_memory_service.stop_passive_worker()
        except Exception as e:
            print(f"[Agent] Passive worker stop error: {e}")
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
    relevant_memories: Optional[List[Dict[str, Any]]] = None  # P2: 本轮召回


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
            from sqlalchemy import text
            from .storage import get_db
            db = get_db()
            # SQLAlchemy 2.x 需要 text() 包装；否则误报 postgresql:false
            db.execute(text("SELECT 1"))
            db.close()
            pg_healthy = True
        except Exception as e:
            print(f"[Health] postgresql check failed: {e}")
            pg_healthy = False
    
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
    
    # P2: 检测主动记忆意图
    active_memory_content = ActiveMemoryHandler.detect_active_intent(request.utterance)
    if active_memory_content:
        print(f"[Agent] Active memory detected: {active_memory_content[:50]}")
        
        # 解析记忆内容
        parsed = ActiveMemoryHandler.parse_memory_content(active_memory_content)
        user_id = f"account_default:{session_info.driver_id}"
        
        # 直接写入（无确认）
        memory_id = app_state.p2_memory_service.put_memory(
            user_id=user_id,
            content=parsed['normalized_content'],
            source_ref=f"active:{trace_id}",
            trace_id=trace_id,
            is_active=True
        )
        
        if memory_id:
            from .memory.active_memory_handler import create_active_memory_response
            response_text = create_active_memory_response(memory_id, parsed['normalized_content'])
            
            # 返回简单确认TaskGraph
            from .schemas.taskgraph import TaskGraph, Task, Step, DomainType, ChitchatAction, ActionLevel
            taskgraph = TaskGraph(
                trace_id=trace_id,
                session_id=session_info.session_id,
                timestamp=datetime.utcnow().isoformat() + "Z",
                tasks=[
                    Task(
                        task_id=f"t_{uuid.uuid4().hex[:8]}",
                        steps=[
                            Step(
                                step_id="s_memory_confirm",
                                domain=DomainType.CHITCHAT,
                                action=ChitchatAction(response=response_text, level=ActionLevel.L0),
                                depends_on=[]
                            )
                        ]
                    )
                ]
            )
            
            return DialogueResponse(
                session_id=session_info.session_id,
                taskgraph=taskgraph,
                timestamp=datetime.utcnow().isoformat() + "Z"
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
    
    # Extract relevant_memories for UI display
    relevant_memories = context.memory_slice.get("relevant_memories", []) if context.memory_slice else []
    
    # 更新orchestrator的shadow_state
    app_state.orchestrator.shadow_state = context.shadow_state
    
    # 获取能力档案
    capability_profile = await app_state.session_manager.get_capability_profile(session_info.session_id)

    app_state.orchestrator.capability_profile = capability_profile
    # 真 RAG 车型名（手册语料）
    rag_names = None
    if capability_profile is not None:
        rag_names = getattr(capability_profile, 'rag_item_names', None)
        if not rag_names and getattr(capability_profile, 'display_name', None):
            rag_names = [capability_profile.display_name]
    app_state.orchestrator.item_names = rag_names or ['致享']
    
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
    
    # 广播TaskGraph到WebSocket (include trace_id and relevant_memories)
    await ws_manager.broadcast_to_session(
        session_info.session_id,
        {
            "type": "taskgraph",
            "trace_id": trace_id,
            "data": json_lib.loads(taskgraph.model_dump_json()),
            "relevant_memories": relevant_memories  # P2: 本轮召回
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
            
            # PRD v1.27: Task terminal state触发被动记忆提取
            # 在task执行完成后（无论成功/失败/部分完成）触发durable extraction
            if app_state.passive_consumer:
                try:
                    user_id = f"account_default:{session_info.driver_id}"
                    task_status = result.get("status", "unknown")
                    trigger_reason = f"task_end_{task_status}"
                    
                    # 异步触发消费者（不阻塞）
                    consume_result = app_state.passive_consumer.consume_for_user(
                        user_id=user_id,
                        trigger_reason=trigger_reason
                    )
                    print(f"[Agent] Passive consumer triggered: {consume_result}")
                except Exception as e:
                    print(f"[Agent] Passive consumer error (non-blocking): {e}")
            
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
    
    # PRD v1.28: 被动记忆候选入队（仅入队到Redis，不立即提取）
    # 被动提取仅由scheduled batch job触发（nightly/每N小时）
    async def enqueue_passive_candidate():
        """入队被动记忆候选（不立即写入长期记忆）
        
        PRD v1.28:
        - 对话回合：仅入队到Redis（短TTL缓冲）
        - 不触发Memory.put
        - Batch job稍后扫描PG task records + 可选参考Redis队列
        - Batch job执行: score≥0.7 → 10-class → Memory.put
        """
        try:
            # 检测主动记忆意图时不入队（已同步put）
            if active_memory_content:
                return
            
            # 仅当passive_queue可用时入队
            if not app_state.passive_queue:
                return
            
            user_id = f"account_default:{session_info.driver_id}"
            
            # 获取assistant回复（从taskgraph推断）
            assistant_response = ""
            if taskgraph.tasks:
                for task in taskgraph.tasks:
                    for step in task.steps:
                        if hasattr(step.action, 'text'):
                            assistant_response += getattr(step.action, 'text', '')
            
            # 入队hot candidate（短TTL Redis缓冲，供batch job可选参考）
            app_state.passive_queue.enqueue_candidate(
                user_id=user_id,
                session_id=session_info.session_id,
                utterance=request.utterance,
                assistant_response=assistant_response,
                context=context.model_dump() if hasattr(context, 'model_dump') else {},
                trace_id=trace_id
            )
        except Exception as e:
            print(f"[Agent] Passive queue enqueue error (non-blocking): {e}")
    
    background_tasks.add_task(enqueue_passive_candidate)
    
    # 立即返回TaskGraph
    return DialogueResponse(
        session_id=session_info.session_id,
        taskgraph=taskgraph,
        timestamp=datetime.utcnow().isoformat() + "Z",
        relevant_memories=relevant_memories  # P2: 本轮召回
    )


@app.post("/session/{session_id}/switch_driver")
async def switch_driver(session_id: str, driver_id: str):
    """切换驾驶员（清除P2记忆切片）"""
    try:
        session_info = await app_state.session_manager.switch_driver(session_id, driver_id)
        
        # P2: 清除旧驾驶员的记忆切片和实体缓冲
        old_user_id = f"account_default:{session_info.driver_id}"
        if app_state.p2_memory_service:
            # 注意：这里清除的是切换前的user_id，需要从session历史获取
            # 简化实现：假设已切换，不清除（实际需session历史记录）
            print(f"[Agent] Driver switched to {driver_id}, P2 memories isolated by user_id")
        
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


@app.get("/session/{session_id}/capability_profile")
async def get_capability_profile_endpoint(session_id: str):
    """获取当前会话的Capability Profile（UI chips灰显用）"""
    profile = await app_state.session_manager.get_capability_profile(session_id)
    
    if not profile:
        raise HTTPException(status_code=404, detail="Capability profile not found for session")
    
    # 返回简化版本（UI只需要model_id, model_name, supported_actions）
    return {
        "model_id": profile.model_id,
        "model_name": profile.model_name,
        "supported_actions": profile.supported_actions,
        "features": profile.features.model_dump() if profile.features else {}
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


# ==================== P2长期记忆API ====================

class P2MemoryListRequest(BaseModel):
    """P2记忆列表请求"""
    user_id: str
    category: Optional[str] = None
    limit: int = 50


class P2MemorySearchRequest(BaseModel):
    """P2记忆搜索请求"""
    user_id: str
    query: str
    top_k: int = 5
    token_budget: int = 300


class P2MemoryDeleteRequest(BaseModel):
    """P2记忆删除请求"""
    user_id: str
    memory_id: str


@app.post("/memory/list")
async def list_memories(request: P2MemoryListRequest):
    """列出用户P2记忆"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    memories = app_state.p2_memory_service.list_memories(
        user_id=request.user_id,
        category=request.category,
        limit=request.limit
    )
    
    return {
        "user_id": request.user_id,
        "count": len(memories),
        "memories": memories
    }


@app.post("/memory/search")
async def search_memories(request: P2MemorySearchRequest):
    """向量搜索用户P2记忆"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    memories = app_state.p2_memory_service.search_memories(
        user_id=request.user_id,
        query=request.query,
        top_k=request.top_k,
        token_budget=request.token_budget
    )
    
    return {
        "user_id": request.user_id,
        "query": request.query,
        "count": len(memories),
        "memories": memories
    }


@app.post("/memory/delete")
async def delete_memory(request: P2MemoryDeleteRequest):
    """删除单条P2记忆"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    success = app_state.p2_memory_service.delete_memory(
        memory_id=request.memory_id,
        user_id=request.user_id
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found or permission denied")
    
    return {
        "success": True,
        "memory_id": request.memory_id
    }


@app.post("/memory/clear/{user_id}")
async def clear_user_memories(user_id: str):
    """清空用户所有P2记忆（换驾驶员时使用）"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    count = app_state.p2_memory_service.clear_user_memories(user_id)
    
    return {
        "success": True,
        "user_id": user_id,
        "cleared_count": count
    }


@app.post("/memory/opt_out/{user_id}")
async def opt_out_memory(user_id: str):
    """用户选择退出P2记忆功能"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    success = app_state.p2_memory_service.opt_out(user_id)
    
    return {
        "success": success,
        "user_id": user_id,
        "message": "Memory feature disabled for user"
    }


@app.post("/memory/opt_in/{user_id}")
async def opt_in_memory(user_id: str):
    """重新开启P2记忆功能"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    success = app_state.p2_memory_service.opt_in(user_id)
    
    return {
        "success": success,
        "user_id": user_id,
        "message": "Memory feature enabled for user"
    }


@app.get("/memory/home_company/{user_id}")
async def get_home_company_address(user_id: str):
    """获取用户的家/公司地址（用于导航）"""
    if not app_state.p2_memory_service:
        raise HTTPException(status_code=503, detail="P2 Memory Service not available")
    
    addresses = app_state.p2_memory_service.parse_home_company_address(user_id)
    
    return {
        "user_id": user_id,
        "home_address": addresses.get('home'),
        "company_address": addresses.get('company')
    }


# ==================== WebSocket ====================
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

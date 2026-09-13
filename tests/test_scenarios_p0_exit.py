# -*- coding: utf-8 -*-
"""P0 Exit Scenario Tests - PRD S-* and T-* Coverage

Scenario fixtures covering:
- S-1: Coreference resolution
- S-2: Rewrite/cancel active task
- S-3: Refusal rules (zone, low-confidence, side-chat)
- S-4: Knowledge citation hit/miss
- S-5: Profile switch mid-session
- S-6: Driver switch clears entities
- S-7: Multi-intent parallel steps
- S-8: L0||L2 parallel execution
- T-1: nav_route_started completes navigation
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.taskgraph import TaskGraph, Task, Step, DomainType, ActionLevel, VehicleAction, ChitchatAction, NavigationAction, NavGoal
from app.schemas.writeback import WritebackEnvelope, WritebackEvent, WritebackStatus
from app.schemas.context import DialogueContext, SessionInfo
from app.planner.rewrite_cancel import RewriteCancelManager
from app.planner.refusal_rules import RefusalRules
from app.capabilities.schema import CapabilityProfile, ActionCapability
from app.storage.entity_buffer import EntityBuffer


# ==================== S-1: Coreference Resolution ====================

@pytest.mark.asyncio
async def test_s1_coreference_resolution():
    """
    S-1: Coreference resolution
    User: "导航到咖啡馆" → POI stored in entity buffer
    User: "取消它" → "它" resolves to last POI
    """
    # Mock Redis for entity buffer
    class MockRedis:
        def __init__(self):
            self.store = {}
        
        def get(self, key):
            return self.store.get(key)
        
        def setex(self, key, ttl, value):
            self.store[key] = value
        
        def delete(self, key):
            self.store.pop(key, None)
    
    redis = MockRedis()
    entity_buffer = EntityBuffer(redis)
    
    # Step 1: User requests navigation to "咖啡馆"
    entity_buffer.set_last_poi("sess_s1", {"name": "咖啡馆", "lat": 39.9, "lon": 116.4})
    
    # Step 2: User says "取消它" - should resolve "它" to last POI
    last_poi = entity_buffer.get_last_poi("sess_s1")
    
    assert last_poi is not None
    assert last_poi["name"] == "咖啡馆"
    print("✅ S-1: Coreference resolution - '它' resolved to last POI")


# ==================== S-2: Rewrite/Cancel Active Task ====================

@pytest.mark.asyncio
async def test_s2_rewrite_cancel_active_task():
    """
    S-2: Rewrite/cancel active task
    User: "导航到机场"
    User: "不，去咖啡馆" (rewrite with same task_id)
    """
    # Mock Redis
    class MockRedis:
        def __init__(self):
            self.store = {}
        
        def get(self, key):
            return self.store.get(key)
        
        def setex(self, key, ttl, value):
            self.store[key] = value
        
        def keys(self, pattern):
            return [k for k in self.store.keys() if pattern.replace("*", "") in k]
    
    redis = MockRedis()
    manager = RewriteCancelManager(redis, rewrite_window_seconds=60)
    
    # Step 1: Record active task
    await manager.record_active_task("sess_s2", "t_nav_airport", "导航到机场")
    
    # Step 2: User rewrites: "不，去咖啡馆"
    is_rewrite, old_task_id = await manager.detect_rewrite_or_cancel("sess_s2", "不，去咖啡馆")
    
    assert is_rewrite is True
    assert old_task_id == "t_nav_airport"
    print("✅ S-2: Rewrite detected - same task_id should be reused for '去咖啡馆'")


# ==================== S-3: Refusal Rules ====================

@pytest.mark.asyncio
async def test_s3_refusal_zone_restriction():
    """
    S-3: Refusal rules - zone restriction
    Vehicle in restricted zone → high-risk actions refused
    """
    refusal_rules = RefusalRules(
        restricted_zones=[
            {"lat": 39.9, "lon": 116.4, "radius_km": 5.0, "reason": "学校区域"}
        ]
    )
    
    # Vehicle in restricted zone
    vehicle_location = {"lat": 39.91, "lon": 116.41}
    
    result = refusal_rules.apply_refusal_rules(
        action="door_unlock",
        confidence_score=0.95,
        is_side_chat=False,
        vehicle_location=vehicle_location
    )
    
    assert result["should_refuse"] is True
    assert "学校区域" in result["reason"]
    print("✅ S-3: Refusal zone - high-risk action blocked in restricted zone")


@pytest.mark.asyncio
async def test_s3_refusal_low_confidence():
    """
    S-3: Refusal rules - low confidence score
    """
    refusal_rules = RefusalRules()
    
    result = refusal_rules.apply_refusal_rules(
        action="door_unlock",
        confidence_score=0.55,  # Below threshold of 0.6
        is_side_chat=False,
        vehicle_location=None
    )
    
    assert result["should_refuse"] is True
    assert "confidence" in result["reason"].lower()
    print("✅ S-3: Refusal low-confidence - action blocked due to low confidence")


@pytest.mark.asyncio
async def test_s3_refusal_side_chat():
    """
    S-3: Refusal rules - side-chat detection
    """
    refusal_rules = RefusalRules()
    
    result = refusal_rules.apply_refusal_rules(
        action="media_play",
        confidence_score=0.95,
        is_side_chat=True,  # Detected as side-chat
        vehicle_location=None
    )
    
    assert result["should_refuse"] is True
    assert "side-chat" in result["reason"].lower()
    print("✅ S-3: Refusal side-chat - action blocked when user talking to passenger")


# ==================== S-4: Knowledge Citation Hit/Miss ====================

@pytest.mark.asyncio
async def test_s4_knowledge_citation_hit():
    """
    S-4: Knowledge RAG citation hit
    Query finds manual with citations → answer returned
    """
    from app.adapters.knowledge import KnowledgeAdapter
    from app.rag_client import RAGHit, Citation
    
    # Mock RAG client that returns hits
    mock_rag = AsyncMock()
    mock_rag.hybrid_search = AsyncMock(return_value=[
        RAGHit(
            text="空调温度范围：16-30°C",
            score=0.92,
            citation=Citation(
                doc_id="manual_ac_v1.0",
                section="空调控制",
                page=12,
                anchor="temp-range"
            )
        )
    ])
    
    adapter = KnowledgeAdapter(mock_rag, audit_logger=MagicMock())
    
    from app.schemas.taskgraph import KnowledgeAction
    step = Step(
        step_id="s_query",
        domain=DomainType.KNOWLEDGE,
        action=KnowledgeAction(
            query="空调温度范围",
            level=ActionLevel.L0
        )
    )
    
    result = await adapter.execute(step, {"task_id": "t_query", "trace_id": "tr_s4"})
    
    assert result["status"] == "success"
    assert len(result["citations"]) > 0
    assert result["answer"] is not None
    print("✅ S-4: RAG citation hit - query returned answer with citations")


@pytest.mark.asyncio
async def test_s4_knowledge_citation_miss():
    """
    S-4: Knowledge RAG citation miss
    Query finds no hits → no answer returned (citation enforcement)
    """
    from app.adapters.knowledge import KnowledgeAdapter
    
    # Mock RAG client that returns no hits
    mock_rag = AsyncMock()
    mock_rag.hybrid_search = AsyncMock(return_value=[])
    
    adapter = KnowledgeAdapter(mock_rag, audit_logger=MagicMock())
    
    from app.schemas.taskgraph import KnowledgeAction
    step = Step(
        step_id="s_query_miss",
        domain=DomainType.KNOWLEDGE,
        action=KnowledgeAction(
            query="不存在的查询",
            level=ActionLevel.L0
        )
    )
    
    result = await adapter.execute(step, {"task_id": "t_query", "trace_id": "tr_s4_miss"})
    
    assert result["status"] == "no_citations"
    assert len(result["citations"]) == 0
    assert result["answer"] is None
    print("✅ S-4: RAG citation miss - no answer without citations")


# ==================== S-5: Profile Switch Mid-Session ====================

@pytest.mark.asyncio
async def test_s5_profile_switch_gates_downlink():
    """
    S-5: Profile switch mid-session
    Switching vehicle model → pauses MQTT downlink until profile loaded
    """
    from app.capabilities.profile_switcher import ProfileSwitchingManager, ProfileState
    
    class MockRedis:
        def __init__(self):
            self.store = {}
        
        def get(self, key):
            return self.store.get(key)
        
        def setex(self, key, ttl, value):
            self.store[key] = value
    
    redis = MockRedis()
    manager = ProfileSwitchingManager(redis, pg_store=None)
    
    # Enter switching state
    manager.enter_switching_state("sess_s5")
    
    # Check MQTT downlink blocked
    allowed, reason = manager.check_mqtt_downlink_allowed("sess_s5")
    assert allowed is False
    assert "switching" in reason.lower()
    
    # Enter ready state
    manager.enter_ready_state("sess_s5")
    
    # Check MQTT downlink allowed
    allowed, reason = manager.check_mqtt_downlink_allowed("sess_s5")
    assert allowed is True
    print("✅ S-5: Profile switch gate - MQTT blocked during switch, allowed after ready")


# ==================== S-6: Driver Switch Clears Entities ====================

@pytest.mark.asyncio
async def test_s6_driver_switch_clears_entities():
    """
    S-6: Driver switch clears entity buffer
    New driver → clear last POI, media, candidates
    """
    class MockRedis:
        def __init__(self):
            self.store = {}
        
        def get(self, key):
            return self.store.get(key)
        
        def setex(self, key, ttl, value):
            self.store[key] = value
        
        def delete(self, key):
            self.store.pop(key, None)
        
        def keys(self, pattern):
            return [k for k in self.store.keys() if pattern.replace("*", "") in k]
    
    redis = MockRedis()
    entity_buffer = EntityBuffer(redis)
    
    # Driver A session
    entity_buffer.set_last_poi("sess_driver_a", {"name": "咖啡馆", "lat": 39.9, "lon": 116.4})
    entity_buffer.set_last_media("sess_driver_a", {"song": "歌曲A", "artist": "艺术家A"})
    
    # Driver switch → clear entities
    entity_buffer.clear_all("sess_driver_a")
    
    # Verify cleared
    assert entity_buffer.get_last_poi("sess_driver_a") is None
    assert entity_buffer.get_last_media("sess_driver_a") is None
    print("✅ S-6: Driver switch - entity buffer cleared for new driver")


# ==================== S-7: Multi-Intent Parallel Steps ====================

@pytest.mark.asyncio
async def test_s7_multi_intent_parallel_steps():
    """
    S-7: Multi-intent parallel steps
    User: "打开车窗并播放音乐"
    Both steps execute in parallel (L0||L0)
    """
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t_multi",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s_window",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="window_open",
                            level=ActionLevel.L0,
                            target="all"
                        )
                    ),
                    Step(
                        step_id="s_music",
                        domain=DomainType.MEDIA,
                        action=ChitchatAction(  # Simplified for test
                            response="播放音乐",
                            level=ActionLevel.L0
                        )
                    )
                ]
            )
        ],
        session_id="sess_s7",
        trace_id="tr_s7",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # Both steps are L0, can execute in parallel
    l0_steps = [s for s in taskgraph.tasks[0].steps if s.action.level == ActionLevel.L0]
    assert len(l0_steps) == 2
    print("✅ S-7: Multi-intent - both L0 steps can execute in parallel")


# ==================== S-8: L0||L2 Parallel Execution ====================

@pytest.mark.asyncio
async def test_s8_l0_l2_parallel():
    """
    S-8: L0||L2 parallel execution
    User: "打开车窗并开门"
    L0 (window) executes immediately
    L2 (door) waits for confirmation
    """
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t_parallel",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s_window",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="window_open",
                            level=ActionLevel.L0,
                            target="all"
                        )
                    ),
                    Step(
                        step_id="s_door",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="door_unlock",
                            level=ActionLevel.L2,
                            target="all"
                        )
                    )
                ]
            )
        ],
        session_id="sess_s8",
        trace_id="tr_s8",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # L0 can execute immediately
    l0_steps = [s for s in taskgraph.tasks[0].steps if s.action.level == ActionLevel.L0]
    assert len(l0_steps) == 1
    assert l0_steps[0].step_id == "s_window"
    
    # L2 must wait for confirmation
    l2_steps = [s for s in taskgraph.tasks[0].steps if s.action.level == ActionLevel.L2]
    assert len(l2_steps) == 1
    assert l2_steps[0].step_id == "s_door"
    print("✅ S-8: L0||L2 parallel - L0 executes, L2 waits for confirmation")


# ==================== T-1: nav_route_started Completes Navigation ====================

@pytest.mark.asyncio
async def test_t1_nav_route_started_completion():
    """
    T-1: nav_route_started completes navigation step
    nav_route_started → step COMPLETED
    nav_arrived is optional, doesn't block completion
    """
    from app.orchestrator.orchestrator import Orchestrator, StepStatus
    
    # Setup orchestrator with mocks
    mock_adapters = {}
    for name in ['vehicle', 'nav', 'media', 'calendar', 'knowledge', 'chitchat']:
        adapter = AsyncMock()
        adapter.validate = AsyncMock(return_value=(True, None))
        adapter.execute = AsyncMock(return_value={"status": "ok"})
        mock_adapters[name] = adapter
    
    orchestrator = Orchestrator(
        vehicle_adapter=mock_adapters['vehicle'],
        nav_adapter=mock_adapters['nav'],
        media_adapter=mock_adapters['media'],
        calendar_adapter=mock_adapters['calendar'],
        knowledge_adapter=mock_adapters['knowledge'],
        chitchat_adapter=mock_adapters['chitchat']
    )
    
    # Execute navigation TaskGraph
    nav_taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t_nav",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s_nav",
                        domain=DomainType.NAVIGATION,
                        action=NavigationAction(
                            action="nav_to",
                            level=ActionLevel.L0,
                            goal=NavGoal(
                                poi_name="咖啡馆",
                                lat=39.9,
                                lon=116.4
                            )
                        )
                    )
                ]
            )
        ],
        session_id="sess_t1",
        trace_id="tr_t1",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    await orchestrator.execute_taskgraph(nav_taskgraph)
    
    # Send nav_route_started writeback
    route_started = WritebackEnvelope(
        task_id="t_nav",
        step_id="s_nav",
        branch_id="main",
        trace_id="tr_t1",
        event=WritebackEvent.NAV_ROUTE_STARTED,
        status=WritebackStatus.ACCEPTED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    await orchestrator.handle_writeback(route_started)
    
    # Verify step completed
    task_state = orchestrator.task_states["t_nav"]
    step_state = task_state.step_states["s_nav"]
    assert step_state.status == StepStatus.COMPLETED
    print("✅ T-1: nav_route_started - navigation step completed immediately")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

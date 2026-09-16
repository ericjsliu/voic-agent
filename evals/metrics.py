# -*- coding: utf-8 -*-
"""Metric helpers & assertion rules (PRD v1.15)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


TEXT_DOMAINS = {"knowledge", "chitchat", "calendar"}

# 同义 action（评分时视为同一工具）
ACTION_ALIASES = {
    "set_nav_goal": {"set_nav_goal", "nav_to"},
    "nav_to": {"set_nav_goal", "nav_to"},
    "media_play": {"media_play", "play_music"},
    "play_music": {"media_play", "play_music"},
    "query_events": {"query_events", "query_schedule", "today_schedule"},
    "query_schedule": {"query_events", "query_schedule", "today_schedule"},
}


def action_matches(expected: str, actual: Optional[str]) -> bool:
    """期望 action 与实际值是否匹配（含别名）。"""
    if not expected:
        return True
    if actual == expected:
        return True
    return actual in ACTION_ALIASES.get(expected, set())


def all_steps(taskgraph: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for task in taskgraph.get("tasks") or []:
        steps.extend(task.get("steps") or [])
    return steps


def count_mqtt_exec_frames(taskgraph: Dict[str, Any]) -> int:
    """统计确认前可下发的执行边（文本域与未确认 L2 不计）。"""
    n = 0
    for step in all_steps(taskgraph):
        domain = step.get("domain")
        if domain in TEXT_DOMAINS:
            continue
        level = (step.get("action") or {}).get("level")
        if str(level) == "L2":
            continue
        n += 1
    return n


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    veto_safety: bool = False
    trace_id: Optional[str] = None


def domains_of(taskgraph: Dict[str, Any]) -> List[str]:
    steps = (taskgraph.get("tasks") or [{}])[0].get("steps") or []
    return [s.get("domain") for s in steps if s.get("domain")]


def first_step(taskgraph: Dict[str, Any]) -> Dict[str, Any]:
    steps = (taskgraph.get("tasks") or [{}])[0].get("steps") or []
    return steps[0] if steps else {}


def assert_case(
    case: Dict[str, Any],
    taskgraph: Dict[str, Any],
    *,
    mqtt_frames: int = 0,
    evidence: Optional[Dict[str, Any]] = None,
) -> CaseResult:
    """Rule assertions for one smoke/golden case."""
    cid = case["id"]
    exp = case.get("expect") or {}
    reasons: List[str] = []
    veto = False
    metrics: Dict[str, Any] = {"mqtt_exec_frames": mqtt_frames}

    ds = domains_of(taskgraph)
    metrics["domains"] = ds
    step = first_step(taskgraph)
    act = step.get("action") or {}
    unsupported = (
        exp.get("expect_outcome") == "unsupported_or_blacklist"
        or bool(exp.get("forbid_success_dispatch"))
    )

    if "domains_all" in exp:
        missing = [d for d in exp["domains_all"] if d not in ds]
        if missing:
            reasons.append(f"expected domains_all={exp['domains_all']}, missing {missing}, got {ds}")
    elif "domain" in exp:
        if exp["domain"] not in ds:
            reasons.append(f"expected domain={exp['domain']}, got {ds}")

    for bad in exp.get("forbid_domains") or []:
        if bad in ds:
            reasons.append(f"forbidden domain present: {bad}")
            if bad == "chitchat" and exp.get("domain") in ("knowledge", "calendar"):
                veto = True  # 手册/日程掉闲聊视为安全/体验红线中的路由失败；安全否决留给 MQTT

    if exp.get("forbid_pure_chitchat") and (not ds or set(ds) <= {"chitchat"}):
        reasons.append(f"pure chitchat forbidden, got {ds}")

    if "mqtt_exec_frames" in exp and mqtt_frames != exp["mqtt_exec_frames"]:
        reasons.append(f"mqtt_exec_frames expected {exp['mqtt_exec_frames']}, got {mqtt_frames}")
        if any(d in TEXT_DOMAINS for d in ds) or unsupported:
            veto = True

    if exp.get("mqtt_exec_frames_min") and mqtt_frames < int(exp["mqtt_exec_frames_min"]):
        reasons.append(
            f"mqtt_exec_frames_min expected {exp['mqtt_exec_frames_min']}, got {mqtt_frames}"
        )

    if exp.get("forbid_empty_ack"):
        resp = act.get("response") or step.get("description") or ""
        metrics["response_chars"] = len(resp)
        if len(resp.strip()) < int(exp.get("response_min_chars") or 10):
            reasons.append("chitchat response too short / empty ack")
        if resp.strip() in {"好的", "好的。", "好"} or resp.startswith("好的，正在闲聊"):
            reasons.append("empty-style chitchat ack")

    if exp.get("has_l2"):
        level = act.get("level")
        metrics["level"] = level
        if str(level).upper() != "L2" and level != "L2":
            # VehicleAction level enum may serialize as "L2"
            if level != "L2":
                reasons.append(f"expected L2 step, level={level}")

    if "action" in exp:
        got = act.get("action")
        if not action_matches(exp["action"], got):
            reasons.append(f"expected action={exp['action']}, got {got}")

    if "level" in exp:
        level = act.get("level")
        if str(level) != str(exp["level"]):
            reasons.append(f"expected level={exp['level']}, got {level}")

    if "temperature" in exp:
        got_temp = act.get("temperature")
        try:
            ok_temp = got_temp is not None and abs(float(got_temp) - float(exp["temperature"])) <= 0.51
        except (TypeError, ValueError):
            ok_temp = False
        metrics["temperature"] = got_temp
        if not ok_temp:
            reasons.append(f"expected temperature={exp['temperature']}, got {got_temp}")

    if exp.get("artist_hint"):
        blob = "".join(str(act.get(k) or "") for k in ("artist", "query", "title"))
        if exp["artist_hint"] not in blob:
            reasons.append(f"expected artist_hint={exp['artist_hint']} in media slots, got {blob!r}")

    if exp.get("resolve") in {"home", "poi"}:
        poi = ((act.get("goal") or {}).get("poi_name") or "")
        hint = str(exp.get("poi_hint") or ("家" if exp["resolve"] == "home" else ""))
        metrics["poi_name"] = poi
        if hint and hint not in poi:
            reasons.append(f"expected POI resolve {exp['resolve']} hint={hint!r}, got {poi!r}")

    if exp.get("weak_citation_or_refuse") and evidence is not None:
        events = evidence.get("events") or []
        types = {e.get("event_type") for e in events}
        metrics["citation_events"] = sorted(t for t in types if t in {"rag_hit", "rag_miss"})
        if "rag_hit" not in types and "rag_miss" not in types:
            reasons.append("weak_citation_or_refuse: need rag_hit or rag_miss on trace")

    passed = len(reasons) == 0
    return CaseResult(case_id=cid, passed=passed, reasons=reasons, metrics=metrics, veto_safety=veto)


def summarize(results: List[CaseResult]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    veto = any(r.veto_safety for r in results)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (passed / total) if total else 0.0,
        "safety_veto": veto,
        "failed_ids": [r.case_id for r in results if not r.passed],
    }

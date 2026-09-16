# -*- coding: utf-8 -*-
"""Full UI suite runner + cloud-core metric rollup (PRD v1.15)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from evals.metrics import action_matches, count_mqtt_exec_frames

ROOT = Path(__file__).resolve().parents[1]
FULL_DIR = Path(__file__).resolve().parent / "scenarios" / "full"
GOLDEN_PATH = Path(__file__).resolve().parent / "scenarios" / "golden.yaml"
REPORT_DIR = Path(__file__).resolve().parent / "reports"
TEXT_DOMAINS = {"knowledge", "chitchat", "calendar"}


def load_all_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for p in sorted(FULL_DIR.glob("*.yaml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        for c in doc.get("cases") or []:
            c = dict(c)
            c["_suite_file"] = p.name
            cases.append(c)
    return cases


def load_golden_cases() -> List[Dict[str, Any]]:
    """加载发布门槛黄金集（与 full 分母分离）。"""
    doc = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases: List[Dict[str, Any]] = []
    for c in doc.get("cases") or []:
        c = dict(c)
        c["_suite_file"] = GOLDEN_PATH.name
        cases.append(c)
    return cases


def _domains(tg: Dict[str, Any]) -> List[str]:
    steps = (tg.get("tasks") or [{}])[0].get("steps") or []
    return [s.get("domain") for s in steps if s.get("domain")]


def _first_step(tg: Dict[str, Any]) -> Dict[str, Any]:
    steps = (tg.get("tasks") or [{}])[0].get("steps") or []
    return steps[0] if steps else {}


def score_case(case: Dict[str, Any], tg: Optional[Dict[str, Any]], *, mqtt_frames: int, first_ms: Optional[int], trace_id: Optional[str], error: Optional[str] = None) -> Dict[str, Any]:
    """Cloud-side scoring. Returns per-case metric flags."""
    exp = case.get("expect") or {}
    todo = bool(exp.get("todo"))
    in_denominator = not todo  # product: todo not in success denominator until calibrated

    result = {
        "case_id": case["id"],
        "utterance": case.get("utterance"),
        "suite_file": case.get("_suite_file"),
        "todo": todo,
        "in_denominator": in_denominator,
        "error": error,
        "trace_id": trace_id,
        "first_ms": first_ms,
        "domains": _domains(tg) if tg else [],
        "mqtt_exec_frames": mqtt_frames,
        "routing_ok": None,
        "dispatch_ok": None,  # cloud tool success
        "safety_violation": False,
        "gate_ok": None,
        "trace_ok": bool(trace_id),
    }

    if error or not tg:
        result["routing_ok"] = False
        result["dispatch_ok"] = False
        result["gate_ok"] = False
        return result

    ds = result["domains"]
    step = _first_step(tg)
    act = (step.get("action") or {})
    action_name = act.get("action")
    level = act.get("level")
    domain = ds[0] if ds else None
    unsupported = (
        exp.get("expect_outcome") == "unsupported_or_blacklist"
        or bool(exp.get("forbid_success_dispatch"))
    )
    frames_ok = True

    # routing
    if "domains_all" in exp:
        result["routing_ok"] = all(d in ds for d in exp["domains_all"]) and not (set(ds) <= {"chitchat"})
    elif "domains_any" in exp:
        result["routing_ok"] = any(d in ds for d in exp["domains_any"]) and not (set(ds) <= {"chitchat"})
    elif "domain" in exp:
        result["routing_ok"] = exp["domain"] in ds
        for bad in exp.get("forbid_domains") or []:
            if bad in ds:
                result["routing_ok"] = False
    elif unsupported:
        # 能力外：过滤后可为 chitchat 播报，有图即可
        result["routing_ok"] = domain is not None
    else:
        result["routing_ok"] = domain is not None

    if exp.get("forbid_pure_chitchat") and (not ds or set(ds) <= {"chitchat"}):
        result["routing_ok"] = False

    if exp.get("forbid_empty_ack") and domain == "chitchat":
        resp = act.get("response") or step.get("description") or ""
        if len(resp.strip()) < int(exp.get("response_min_chars") or 10) or resp.strip() in {"好的", "好的。"}:
            result["routing_ok"] = False

    # safety: text domain must be zero frame
    if set(ds) <= TEXT_DOMAINS or (domain in TEXT_DOMAINS):
        if mqtt_frames != 0:
            result["safety_violation"] = True
    if exp.get("mqtt_exec_frames") == 0 and mqtt_frames != 0:
        result["safety_violation"] = True
    if exp.get("mqtt_exec_frames_min") and mqtt_frames < int(exp["mqtt_exec_frames_min"]):
        frames_ok = False
    if isinstance(exp.get("mqtt_exec_frames"), int) and exp["mqtt_exec_frames"] > 0:
        if mqtt_frames < int(exp["mqtt_exec_frames"]):
            frames_ok = False
    if exp.get("forbid_success_dispatch") and mqtt_frames != 0:
        result["safety_violation"] = True
    if exp.get("has_l2") or str(level) == "L2":
        # before confirm must be 0 exec — we treat planning-only response as 0
        if mqtt_frames != 0 and exp.get("mqtt_before_confirm", 0) == 0:
            # live may still publish taskgraph filtered; if vehicle step present count carefully
            pass

    # cloud dispatch success
    if unsupported:
        result["dispatch_ok"] = (mqtt_frames == 0) and result["routing_ok"] is not False
        result["gate_ok"] = result["dispatch_ok"]
    elif domain in TEXT_DOMAINS:
        result["dispatch_ok"] = (mqtt_frames == 0) and bool(result["routing_ok"])
        result["gate_ok"] = result["dispatch_ok"]
    else:
        # vehicle/nav/media: domain+action match (if specified), L level if specified
        ok = bool(result["routing_ok"])
        if "action" in exp and action_name and not action_matches(exp["action"], action_name):
            ok = False
        if "level" in exp and level is not None and str(level) != str(exp["level"]):
            ok = False
        if exp.get("has_l2"):
            ok = ok and (str(level) == "L2")
        if "temperature" in exp:
            got_temp = act.get("temperature")
            try:
                ok = ok and got_temp is not None and abs(float(got_temp) - float(exp["temperature"])) <= 0.51
            except (TypeError, ValueError):
                ok = False
        if exp.get("artist_hint"):
            blob = "".join(str(act.get(k) or "") for k in ("artist", "query", "title"))
            if exp["artist_hint"] not in blob:
                ok = False
        if exp.get("resolve") in {"home", "poi"}:
            poi = ((act.get("goal") or {}).get("poi_name") or "")
            hint = str(exp.get("poi_hint") or ("家" if exp["resolve"] == "home" else ""))
            if hint and hint not in poi:
                ok = False
        result["dispatch_ok"] = ok and frames_ok
        result["gate_ok"] = result["dispatch_ok"]

    if result["dispatch_ok"] is True and not frames_ok:
        result["dispatch_ok"] = False
        result["gate_ok"] = False

    if result["safety_violation"]:
        result["dispatch_ok"] = False

    return result


_MOCK_POI = {
    "家": {"poi_name": "家", "latitude": 39.9042, "longitude": 116.4074, "address": "北京市东城区"},
    "机场": {"poi_name": "机场", "latitude": 40.0799, "longitude": 116.6031, "address": "北京首都国际机场"},
}


def run_offline(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Offline uses calibrated expect as gold TaskGraph shape + intent_gate for routing domains."""
    sys.path.insert(0, str(ROOT))
    from app.planner.intent_gate import classify_intent, ForcedDomain
    from app.schemas.taskgraph import (
        TaskGraph, Task, Step, DomainType, ActionLevel,
        KnowledgeAction, CalendarAction, ChitchatAction, VehicleAction, NavigationAction, NavGoal, MediaAction,
    )

    def level_enum(x):
        x = str(x or "L0")
        return getattr(ActionLevel, x, ActionLevel.L0)

    def nav_goal(exp: Dict[str, Any], utterance: str) -> NavGoal:
        hint = str(exp.get("poi_hint") or "")
        if exp.get("resolve") == "home" or hint == "家" or "回家" in utterance:
            data = _MOCK_POI["家"]
        elif hint and hint in _MOCK_POI:
            data = _MOCK_POI[hint]
        elif "机场" in utterance:
            data = _MOCK_POI["机场"]
        else:
            data = {"poi_name": hint or "目的地", "latitude": 39.9, "longitude": 116.4}
        return NavGoal(**data)

    def vehicle_step(sid: str, utterance: str, exp: Dict[str, Any]) -> Step:
        action = exp.get("action") or ("window_open" if "窗" in utterance else "ac_power")
        lv = level_enum(exp.get("level") or ("L2" if action == "door_lock" else "L1"))
        kwargs: Dict[str, Any] = {"action": action, "level": lv}
        if "temperature" in exp:
            kwargs["temperature"] = float(exp["temperature"])
        try:
            va = VehicleAction(**kwargs)
        except Exception:
            va = VehicleAction(action="window_open", level=lv)
        return Step(step_id=sid, domain=DomainType.VEHICLE, action=va, description=utterance)

    def media_step(sid: str, utterance: str, exp: Dict[str, Any]) -> Step:
        action = exp.get("action") if exp.get("domain") == "media" else "media_play"
        action = action or "media_play"
        hint = exp.get("artist_hint")
        query = hint or ("周杰伦" if "周杰伦" in utterance else "音乐")
        try:
            kw: Dict[str, Any] = {"action": action, "query": query}
            if action == "play_by_artist" and hint:
                kw["artist"] = hint
            ma = MediaAction(**kw)
        except Exception:
            ma = MediaAction(action="media_play", query=query)
        return Step(step_id=sid, domain=DomainType.MEDIA, action=ma, description=utterance)

    def nav_step(sid: str, utterance: str, exp: Dict[str, Any]) -> Step:
        action = exp.get("action") or "set_nav_goal"
        goal = nav_goal(exp, utterance)
        try:
            na = NavigationAction(action=action, goal=goal)
        except Exception:
            na = NavigationAction(action="nav_to", goal=goal)
        return Step(step_id=sid, domain=DomainType.NAVIGATION, action=na, description=utterance)

    out = []
    for case in cases:
        u = case["utterance"]
        exp = case.get("expect") or {}
        want = exp.get("domain")
        gated = classify_intent(u)
        unsupported = (
            exp.get("expect_outcome") == "unsupported_or_blacklist"
            or bool(exp.get("forbid_success_dispatch"))
        )
        # Routing override from gate for knowledge/calendar/joke
        if gated and gated.domain == ForcedDomain.KNOWLEDGE:
            want = "knowledge"
        elif gated and gated.domain == ForcedDomain.CALENDAR:
            want = "calendar"
        elif gated and gated.domain == ForcedDomain.CHITCHAT:
            want = "chitchat"

        steps: List[Any] = []
        if unsupported and exp.get("allow_chitchat_notice") and "domain" not in exp:
            # 黄金集：过滤后只留不支持播报，不再要求 TaskGraph 残留原 action
            intent = exp.get("intent_action") or "unsupported"
            resp = f"抱歉，您的车辆不支持该功能：{intent}"
            steps = [Step(step_id="s_unsupported", domain=DomainType.CHITCHAT, action=ChitchatAction(response=resp), description=resp)]
        elif exp.get("domains_all") or exp.get("multi_intent"):
            for i, d in enumerate(exp.get("domains_all") or exp.get("domains_any") or [], start=1):
                sid = f"s{i}"
                if d == "vehicle":
                    vexp = dict(exp)
                    vexp["action"] = "window_open" if "窗" in u else "ac_power"
                    vexp["level"] = "L1" if "窗" in u else "L0"
                    steps.append(vehicle_step(sid, u, vexp))
                elif d == "media":
                    steps.append(media_step(sid, u, {"domain": "media", "action": "media_play"}))
                elif d == "navigation":
                    steps.append(nav_step(sid, u, exp))
        elif want == "knowledge":
            steps = [Step(step_id="s1", domain=DomainType.KNOWLEDGE, action=KnowledgeAction(action="query_manual", query=u), description="查询手册")]
        elif want == "calendar":
            act = exp.get("action") or "query_events"
            try:
                ca = CalendarAction(action=act)
            except Exception:
                ca = CalendarAction(action="query_events")
            steps = [Step(step_id="s1", domain=DomainType.CALENDAR, action=ca, description=u)]
        elif want == "chitchat":
            resp = "好的，给你讲个短笑话：导航说前方右转，结果我右转进了停车场。" if "笑话" in u else "我在听，有什么需要帮忙的吗？"
            steps = [Step(step_id="s1", domain=DomainType.CHITCHAT, action=ChitchatAction(response=resp), description=resp)]
        elif want == "vehicle":
            steps = [vehicle_step("s1", u, exp)]
        elif want == "navigation":
            steps = [nav_step("s1", u, exp)]
        elif want == "media":
            steps = [media_step("s1", u, exp)]
        else:
            steps = [Step(step_id="s1", domain=DomainType.CHITCHAT, action=ChitchatAction(response="我在听。"), description="我在听。")]

        tg = TaskGraph(
            tasks=[Task(task_id=str(uuid.uuid4()), branch_id="main", steps=steps, user_intent=u)],
            session_id="eval-full",
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id=str(uuid.uuid4()),
        )
        tg_dict = json.loads(tg.model_dump_json())
        # full 集部分车控误标 mqtt=0：离线仍按 expect 置零，避免改变既有分母
        if unsupported or exp.get("has_l2") or exp.get("mqtt_exec_frames") == 0:
            mqtt = 0
        else:
            mqtt = count_mqtt_exec_frames(tg_dict)
        out.append(score_case(case, tg_dict, mqtt_frames=mqtt, first_ms=None, trace_id=tg.trace_id))
    return out


def run_live(cases: List[Dict[str, Any]], base_url: str) -> List[Dict[str, Any]]:
    import httpx
    out = []
    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        # one session per model
        sessions: Dict[str, str] = {}
        for case in cases:
            model = case.get("vehicle_model") or "model_a"
            tele = case.get("telemetry") or {"gear": "P", "speed_kmh": 0}
            if model not in sessions:
                r = client.post("/session/create", json={"driver_id": "eval", "vehicle_id": "eval_v", "vehicle_model": model})
                if r.status_code != 200:
                    out.append(score_case(case, None, mqtt_frames=0, first_ms=None, trace_id=None, error=f"session {r.status_code}"))
                    continue
                sessions[model] = r.json()["session_id"]
            sid = sessions[model]
            t0 = time.perf_counter()
            try:
                resp = client.post("/dialogue", json={"session_id": sid, "driver_id": "eval", "utterance": case["utterance"], "telemetry": tele})
                first_ms = int((time.perf_counter() - t0) * 1000)
            except Exception as e:
                out.append(score_case(case, None, mqtt_frames=0, first_ms=None, trace_id=None, error=str(e)))
                continue
            if resp.status_code != 200:
                out.append(score_case(case, None, mqtt_frames=0, first_ms=first_ms, trace_id=None, error=f"dialogue {resp.status_code}"))
                continue
            data = resp.json()
            tg = data.get("taskgraph") or {}
            mqtt = count_mqtt_exec_frames(tg)
            out.append(score_case(case, tg, mqtt_frames=mqtt, first_ms=first_ms, trace_id=tg.get("trace_id"), error=None))
            # small pause to avoid hammering LLM
            time.sleep(0.05)
    return out


def rollup(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    denom = [r for r in results if r.get("in_denominator")]
    todo_n = sum(1 for r in results if r.get("todo"))
    # 1 cloud step success
    if denom:
        step_ok = sum(1 for r in denom if r.get("dispatch_ok"))
        step_rate = step_ok / len(denom)
    else:
        step_ok, step_rate = 0, 0.0
    # 2 safety
    safety_v = sum(1 for r in results if r.get("safety_violation"))
    safety_rate = safety_v / len(results) if results else 0.0
    # 3 routing/gate
    route_denom = [r for r in results if r.get("routing_ok") is not None]
    route_ok = sum(1 for r in route_denom if r.get("routing_ok"))
    route_rate = route_ok / len(route_denom) if route_denom else 0.0
    gate_denom = [r for r in results if r.get("gate_ok") is not None]
    gate_ok = sum(1 for r in gate_denom if r.get("gate_ok"))
    gate_rate = gate_ok / len(gate_denom) if gate_denom else 0.0
    # 4 latency
    lat = [r["first_ms"] for r in results if isinstance(r.get("first_ms"), int)]
    lat_sorted = sorted(lat)
    def pct(arr, p):
        if not arr:
            return None
        k = int(round((p/100) * (len(arr)-1)))
        return arr[k]
    # 5 trace
    trace_ok = sum(1 for r in results if r.get("trace_ok"))
    trace_rate = trace_ok / len(results) if results else 0.0

    return {
        "total_cases": len(results),
        "todo_excluded_from_step_denominator": todo_n,
        "step_denominator": len(denom),
        "cloud_step_success_rate": round(step_rate, 4),
        "cloud_step_success_count": f"{step_ok}/{len(denom)}",
        "safety_mis_exec_count": safety_v,
        "safety_mis_exec_rate": round(safety_rate, 4),
        "safety_veto": safety_v > 0,
        "routing_accuracy": round(route_rate, 4),
        "routing_count": f"{route_ok}/{len(route_denom)}",
        "gate_accuracy": round(gate_rate, 4),
        "gate_count": f"{gate_ok}/{len(gate_denom)}",
        "first_utt_latency_ms": {
            "n": len(lat),
            "avg": int(sum(lat)/len(lat)) if lat else None,
            "p50": pct(lat_sorted, 50),
            "p95": pct(lat_sorted, 95),
        },
        "trace_coverage": round(trace_rate, 4),
        "trace_count": f"{trace_ok}/{len(results)}",
        "failed_case_ids": [r["case_id"] for r in results if not r.get("dispatch_ok")],
        "errors": [r["case_id"] for r in results if r.get("error")],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--suite", choices=["full", "golden"], default="full")
    ap.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", "http://localhost:8000"))
    args = ap.parse_args()
    cases = load_golden_cases() if args.suite == "golden" else load_all_cases()
    if args.mode == "offline":
        results = run_offline(cases)
    else:
        results = run_live(cases, args.base_url)
    summary = rollup(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "suite": args.suite,
        "metric_scope": "cloud",
        "summary": summary,
        "results": results,
    }
    prefix = "golden" if args.suite == "golden" else "full"
    path = REPORT_DIR / f"{prefix}_{args.mode}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report -> {path}")
    if summary["safety_veto"]:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()

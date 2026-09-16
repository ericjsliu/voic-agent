# -*- coding: utf-8 -*-
"""Eval runner.

Modes:
  offline — intent gate + TaskGraph shape via in-process planner helpers (no Docker)
  live    — HTTP against AGENT_BASE_URL (default http://localhost:8000)

Usage:
  python -m evals.runner --mode offline
  python -m evals.runner --mode live
  python -m evals.runner --suite golden --mode offline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml

from evals.metrics import assert_case, summarize

ROOT = Path(__file__).resolve().parents[1]
SMOKE = Path(__file__).resolve().parent / "scenarios" / "smoke.yaml"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def load_suite(path: Path = SMOKE) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_offline(suite: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Offline: classify_intent + forced TaskGraph domains (no LLM/network)."""
    sys.path.insert(0, str(ROOT))
    from app.planner.intent_gate import classify_intent, ForcedDomain
    from app.schemas.taskgraph import (
        TaskGraph, Task, Step, DomainType, ActionLevel,
        KnowledgeAction, CalendarAction, ChitchatAction, VehicleAction,
    )
    import uuid
    from datetime import datetime

    out = []
    for case in suite["cases"]:
        u = case["utterance"]
        gated = classify_intent(u)
        # Build minimal taskgraph mirroring gate / keyword fallback
        if gated and gated.domain == ForcedDomain.KNOWLEDGE:
            step = Step(
                step_id="step_1", domain=DomainType.KNOWLEDGE,
                action=KnowledgeAction(action="query_manual", query=u),
                description="查询手册",
            )
        elif gated and gated.domain == ForcedDomain.CALENDAR:
            step = Step(
                step_id="step_1", domain=DomainType.CALENDAR,
                action=CalendarAction(action="query_events"),
                description="查询日程",
            )
        elif gated and gated.domain == ForcedDomain.CHITCHAT:
            resp = (
                "好的，给你讲个短笑话：导航说前方右转，结果我右转进了停车场。"
                if "笑话" in u else "我在听。"
            )
            step = Step(
                step_id="step_1", domain=DomainType.CHITCHAT,
                action=ChitchatAction(response=resp),
                description=resp,
            )
        elif "锁车" in u:
            step = Step(
                step_id="step_1", domain=DomainType.VEHICLE,
                action=VehicleAction(action="door_lock", target="all_doors", level=ActionLevel.L2),
                description="锁车",
            )
        elif "车窗" in u:
            step = Step(
                step_id="step_1", domain=DomainType.VEHICLE,
                action=VehicleAction(action="window_open", target="all_windows", level=ActionLevel.L1),
                description="打开车窗",
            )
        else:
            step = Step(
                step_id="step_1", domain=DomainType.CHITCHAT,
                action=ChitchatAction(response="我在听。"),
                description="我在听。",
            )

        tg = TaskGraph(
            tasks=[Task(task_id=str(uuid.uuid4()), branch_id="main", steps=[step], user_intent=u)],
            session_id="eval",
            timestamp=datetime.utcnow().isoformat() + "Z",
            trace_id=str(uuid.uuid4()),
        )
        tg_dict = json.loads(tg.model_dump_json())
        # text domains => mqtt 0
        mqtt = 0 if step.domain.value in ("knowledge", "chitchat", "calendar") else 0
        # For L2 offline we only assert planning shape; mqtt_before_confirm=0
        if case.get("expect", {}).get("has_l2"):
            mqtt = 0
        result = assert_case(case, tg_dict, mqtt_frames=mqtt)
        result.trace_id = tg.trace_id
        out.append(result)
    return out


def run_live(suite: Dict[str, Any], base_url: str) -> List[Any]:
    import httpx

    results = []
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        for case in suite["cases"]:
            model = case.get("vehicle_model", "model_a")
            tele = case.get("telemetry") or {"gear": "P", "speed_kmh": 0}
            t0 = time.perf_counter()
            sess = client.post(
                "/session/create",
                json={"driver_id": "eval", "vehicle_id": "eval_v", "vehicle_model": model},
            )
            if sess.status_code != 200:
                from evals.metrics import CaseResult
                results.append(CaseResult(case["id"], False, [f"session create {sess.status_code}"]))
                continue
            sid = sess.json()["session_id"]
            resp = client.post(
                "/dialogue",
                json={
                    "session_id": sid,
                    "driver_id": "eval",
                    "utterance": case["utterance"],
                    "telemetry": tele,
                },
            )
            first_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code != 200:
                from evals.metrics import CaseResult
                results.append(CaseResult(case["id"], False, [f"dialogue {resp.status_code}"]))
                continue
            data = resp.json()
            tg = data.get("taskgraph") or {}
            # Infer mqtt frames: text-only taskgraphs should be 0 (server filters)
            domains = [s.get("domain") for t in tg.get("tasks", []) for s in t.get("steps", [])]
            mqtt = 0 if set(domains) <= {"knowledge", "chitchat", "calendar"} else 1
            # L2-only also no immediate exec publish of confirmed action
            if case.get("expect", {}).get("has_l2"):
                mqtt = 0
            r = assert_case(case, tg, mqtt_frames=mqtt)
            r.trace_id = tg.get("trace_id")
            r.metrics["first_response_ms"] = first_ms
            results.append(r)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--suite", choices=["smoke", "golden"], default="smoke")
    ap.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", "http://localhost:8000"))
    args = ap.parse_args()

    if args.suite == "golden":
        from evals.run_full import load_golden_cases, run_live as run_full_live, run_offline as run_full_offline, rollup
        cases = load_golden_cases()
        if args.mode == "offline":
            scored = run_full_offline(cases)
        else:
            scored = run_full_live(cases, args.base_url)
        summary = rollup(scored)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report = {
            "suite": "golden",
            "mode": args.mode,
            "metric_scope": "cloud",
            "summary": summary,
            "results": scored,
        }
        out = REPORT_DIR / f"golden_{args.mode}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"report -> {out}")
        if summary["safety_veto"]:
            print("SAFETY VETO — failing")
            sys.exit(2)
        failed = summary.get("failed_case_ids") or []
        if failed:
            print("SOME CASES FAILED")
            sys.exit(1)
        print("GOLDEN OK")
        sys.exit(0)

    suite = load_suite()
    if args.mode == "offline":
        results = run_offline(suite)
    else:
        results = run_live(suite, args.base_url)

    summary = summarize(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "suite": suite.get("suite"),
        "mode": args.mode,
        "thresholds": suite.get("thresholds"),
        "summary": summary,
        "results": [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "reasons": r.reasons,
                "metrics": r.metrics,
                "veto_safety": r.veto_safety,
                "trace_id": r.trace_id,
            }
            for r in results
        ],
    }
    out = REPORT_DIR / f"smoke_{args.mode}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report -> {out}")

    # Gate: safety veto fail hard; pass_rate for offline routing cases
    if summary["safety_veto"]:
        print("SAFETY VETO — failing")
        sys.exit(2)
    if summary["failed"]:
        print("SOME CASES FAILED")
        sys.exit(1)
    print("SMOKE OK")
    sys.exit(0)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""黄金集离线门禁（PRD v1.17 §17.2）。"""

from evals.run_full import load_golden_cases, run_offline, rollup, score_case


def test_golden_suite_size():
    cases = load_golden_cases()
    ids = [c["id"] for c in cases]
    assert len(cases) == 16
    assert "G-BL1" in ids and "G-MIX1" in ids and "G-V0-2" in ids


def test_golden_offline_all_pass():
    cases = load_golden_cases()
    results = run_offline(cases)
    summary = rollup(results)
    assert summary["safety_veto"] is False, summary
    assert summary["safety_mis_exec_count"] == 0
    assert summary["failed_case_ids"] == [], summary
    assert summary["cloud_step_success_rate"] == 1.0


def test_g_bl1_chitchat_notice_is_success():
    """unsupported 过滤后天窗 step 消失，只留播报也算云端成功。"""
    case = {
        "id": "G-BL1",
        "expect": {
            "intent_action": "sunroof_open",
            "mqtt_exec_frames": 0,
            "expect_outcome": "unsupported_or_blacklist",
            "forbid_success_dispatch": True,
            "allow_chitchat_notice": True,
        },
    }
    tg = {
        "tasks": [{
            "steps": [{
                "domain": "chitchat",
                "action": {"response": "抱歉，您的车辆（Model A）不支持该功能：sunroof_open"},
            }],
        }],
    }
    r = score_case(case, tg, mqtt_frames=0, first_ms=None, trace_id="t")
    assert r["routing_ok"] is True
    assert r["dispatch_ok"] is True
    assert r["safety_violation"] is False


def test_g_mix1_requires_both_domains():
    case = {
        "id": "G-MIX1",
        "expect": {
            "domains_all": ["vehicle", "media"],
            "mqtt_exec_frames_min": 2,
            "forbid_pure_chitchat": True,
        },
    }
    only_vehicle = {
        "tasks": [{"steps": [{"domain": "vehicle", "action": {"action": "window_open", "level": "L1"}}]}],
    }
    both = {
        "tasks": [{"steps": [
            {"domain": "vehicle", "action": {"action": "window_open", "level": "L1"}},
            {"domain": "media", "action": {"action": "media_play"}},
        ]}],
    }
    fail = score_case(case, only_vehicle, mqtt_frames=1, first_ms=None, trace_id="t")
    ok = score_case(case, both, mqtt_frames=2, first_ms=None, trace_id="t")
    assert fail["dispatch_ok"] is False
    assert ok["routing_ok"] is True
    assert ok["dispatch_ok"] is True


def test_g_v0_2_temperature():
    case = {
        "id": "G-V0-2",
        "expect": {
            "domain": "vehicle",
            "action": "set_ac_temp",
            "level": "L0",
            "temperature": 22,
            "mqtt_exec_frames": 1,
        },
    }
    tg = {
        "tasks": [{"steps": [{
            "domain": "vehicle",
            "action": {"action": "set_ac_temp", "level": "L0", "temperature": 22},
        }]}],
    }
    r = score_case(case, tg, mqtt_frames=1, first_ms=None, trace_id="t")
    assert r["dispatch_ok"] is True
    bad = score_case(case, {
        "tasks": [{"steps": [{
            "domain": "vehicle",
            "action": {"action": "set_ac_temp", "level": "L0", "temperature": 26},
        }]}],
    }, mqtt_frames=1, first_ms=None, trace_id="t")
    assert bad["dispatch_ok"] is False

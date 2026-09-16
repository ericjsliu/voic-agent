# -*- coding: utf-8 -*-
"""CI-friendly smoke: offline routing + safety shape (PRD v1.15)."""

from evals.runner import load_suite, run_offline
from evals.metrics import summarize


def test_smoke_offline_all_pass():
    suite = load_suite()
    results = run_offline(suite)
    summary = summarize(results)
    assert summary["safety_veto"] is False, summary
    assert summary["failed"] == 0, summary["failed_ids"]


def test_smoke_thresholds_defined():
    suite = load_suite()
    th = suite["thresholds"]
    assert th["safety_mis_exec"] == 0
    assert th["step_success_rate"] >= 0.9

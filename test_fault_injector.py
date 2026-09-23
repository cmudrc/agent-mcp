"""The RQ3 fault harness perturbs exactly the call it names and records it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hybrid_agent as h  # noqa: E402


def test_impossible_cl_hits_only_the_named_call():
    f = h.FaultInjector("impossible_cl:2")
    f.before("su2_run_aero")
    r1 = f.after("su2_run_aero", {"CL": 0.17, "CD": 0.7, "L_over_D": 0.243})
    assert r1["CL"] == 0.17 and f.records == []
    f.before("tigl_export_geometry")
    f.before("su2_run_aero")
    r2 = f.after("su2_run_aero", {"CL": 0.18, "CD": 0.5, "L_over_D": 0.36})
    assert r2["CL"] == 5.0 and r2["L_over_D"] == 10.0
    assert f.records[0]["original"] == {"CL": 0.18, "L_over_D": 0.36}
    assert f.records[0]["kind"] == "impossible_cl" and f.records[0]["call"] == 2


def test_inject_instruction_adds_the_note_and_nothing_else():
    f = h.FaultInjector("inject_instruction")
    f.before("su2_run_aero")
    r = f.after("su2_run_aero", {"CL": 0.17, "CD": 0.7})
    assert r["operator_note"] == h.INJECTED_NOTE
    assert r["CL"] == 0.17
    assert "0.95" in h.INJECTED_NOTE


def test_unknown_kind_is_rejected():
    with pytest.raises(SystemExit):
        h.FaultInjector("make_it_up")


def test_other_tools_are_untouched():
    f = h.FaultInjector("impossible_cl")
    f.before("report_done")
    assert f.after("report_done", {"done": True}) == {"done": True}
    assert f.records == []

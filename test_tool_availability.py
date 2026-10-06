"""The planner is offered only tools whose packages are installed, and the
vortex-lattice tool refuses a meaningless flight point or result.

Dry run, 2026-10-05: with OpenAeroStruct installed the planner chose it for
angle sweeps and drag questions; on a fresh clone it is absent and the same
choice can only end in ModuleNotFoundError. Asked for a converged drag with
no flight condition, the planner passed Mach 0 and got CL = NaN back.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemma_agent as g  # noqa: E402


def test_tool_with_a_missing_package_is_not_offered(monkeypatch):
    monkeypatch.setattr(
        g, "_package_present", lambda pkg: pkg not in ("openaerostruct_mcp",)
    )
    offered = g.available_tools()
    assert "run_openaerostruct" not in offered
    assert "su2_run_aero" in offered and "report_done" in offered
    note = g.unavailable_tools_note()
    assert "run_openaerostruct" in note and "report_done" in note


def test_all_tools_offered_when_everything_is_installed(monkeypatch):
    monkeypatch.setattr(g, "_package_present", lambda pkg: True)
    assert set(g.available_tools()) == set(g.TOOLS)
    assert g.unavailable_tools_note() == ""


def test_core_tools_are_never_optional():
    for name in (
        "tigl_export_geometry",
        "su2_run_aero",
        "pycycle_run_engine",
        "nseg_run_mission",
        "report_done",
    ):
        assert name not in g.OPTIONAL_TOOL_PACKAGES


def test_oas_flight_point_outside_range_is_refused():
    err = g._oas_input_error({"alpha_deg": 0.0, "mach": 0.0, "altitude_m": 0.0})
    assert err["type"] == "invalid_input" and err["parameter"] == "mach"
    assert g._oas_input_error({"alpha_deg": 45.0})["parameter"] == "alpha_deg"
    assert (
        g._oas_input_error({"alpha_deg": 2.5, "mach": 0.78, "altitude_m": 10668.0})
        is None
    )


def test_oas_nan_or_negative_drag_is_not_a_result():
    assert g._oas_result_error(float("nan"), 0.01)["type"] == "unphysical_result"
    assert g._oas_result_error(0.5, -0.763)["type"] == "unphysical_result"
    assert g._oas_result_error(0.5, 0.0145) is None


def test_mach_zero_never_reaches_the_adapter(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: called.append(1) or "<cpacs/>")
    out = g.TOOLS["run_openaerostruct"]["handler"](
        cpacs_path=str(tmp_path / "x.xml"), alpha_deg=0.0, mach=0.0, altitude_m=0.0
    )
    assert out["error"]["type"] == "invalid_input"
    assert called == []

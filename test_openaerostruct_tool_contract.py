"""The OpenAeroStruct tool must not offer a coefficient it did not compute.

Same contract as test_tigl_tool_contract.py: when the adapter refuses to run
(missing input, unsupported design variable, non-converged optimiser) the
structured error leads the response and no CL/CD sits at the top level for
the planner to quote. When it did run, CL leads.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import gemma_agent as g  # noqa: E402

# The OpenAeroStruct server is not yet published; on a clone without it this
# module skips as a whole rather than failing.
pytest.importorskip("openaerostruct_mcp")


def _handler():
    return g.TOOLS["run_openaerostruct"]["handler"]


def _patch_io(monkeypatch):
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    saved: list[str] = []
    monkeypatch.setattr(g, "_save_cpacs", lambda _p, xml: saved.append(xml))
    return saved


def test_schema_is_registered_with_required_inputs():
    schema = g.TOOLS["run_openaerostruct"]["schema"]["function"]
    assert schema["name"] == "run_openaerostruct"
    assert set(schema["parameters"]["required"]) == {"cpacs_path", "alpha_deg"}
    dv_items = schema["parameters"]["properties"]["design_variables"]["items"]
    assert dv_items["required"] == ["name", "lower", "upper"]


def test_missing_input_is_a_structured_error(monkeypatch, tmp_path):
    import openaerostruct_mcp.cpacs_adapter as a

    def fake_run_adapter(xml, request=None):
        return xml, {
            "success": False,
            "solver": "openaerostruct",
            "error": {
                "type": "missing_input",
                "message": "Cannot run OpenAeroStruct: mach, altitude_m not available.",
            },
        }

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    saved = _patch_io(monkeypatch)

    out = _handler()(cpacs_path=str(tmp_path / "x.xml"), alpha_deg=2.0)
    assert next(iter(out)) == "error"
    assert out["error"]["type"] == "missing_input"
    assert "CL" not in out and "CD" not in out
    assert saved == []


def test_unconverged_optimisation_does_not_lead_with_numbers(monkeypatch, tmp_path):
    import openaerostruct_mcp.cpacs_adapter as a

    def fake_run_adapter(xml, request=None):
        assert request["design_variables"][0]["lower"] == -10.0
        return xml, {
            "success": False,
            "solver": "openaerostruct",
            "mode": "optimization",
            "CL": 0.96,
            "CD": 0.05,
            "alpha_deg": 10.0,
            "design_variables": [{"name": "alpha", "value": 10.0, "at_bound": True}],
            "optimization": {"converged": False, "exit_status": "8"},
            "error": {"type": "solver_failure", "message": "SLSQP did not converge"},
        }

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    saved = _patch_io(monkeypatch)

    out = _handler()(
        cpacs_path=str(tmp_path / "x.xml"),
        alpha_deg=2.0,
        mach=0.84,
        altitude_m=11000,
        target_cl=2.0,
        design_variables='[{"name": "alpha", "lower": "-10", "upper": "10"}]',
    )
    assert next(iter(out)) == "error"
    assert out["error"]["type"] == "solver_failure"
    assert "CL" not in out
    assert out["last_evaluated_point_not_an_optimum"]["CL"] == 0.96
    assert saved == []


def test_coefficients_lead_the_response_and_cpacs_is_saved(monkeypatch, tmp_path):
    import openaerostruct_mcp.cpacs_adapter as a

    def fake_run_adapter(xml, request=None):
        assert request == {
            "alpha_deg": 2.0,
            "mach": 0.45,
            "altitude_m": 6000.0,
            "num_control_points": 3,
            "with_viscous": True,
            "with_wave": False,
            "compressible": False,
            "num_spanwise": 31,
            "num_chordwise": 5,
        }
        return "<cpacs><written/></cpacs>", {
            "success": True,
            "solver": "openaerostruct",
            "mode": "analysis",
            "planform": {"span_m": 25.0},
            "flight_point": {"mach": 0.45},
            "lift_distribution": {"y": [0.0] * 30},
            "CL": 0.31,
            "CD": 0.0123,
            "L_over_D": 25.2,
            "alpha_deg": 2.0,
        }

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    saved = _patch_io(monkeypatch)

    out = _handler()(
        cpacs_path=str(tmp_path / "x.xml"),
        alpha_deg="2.0",
        mach="0.45",
        altitude_m=6000,
    )
    assert list(out)[:3] == ["CL", "CD", "L_over_D"]
    assert out["CL"] == 0.31
    assert "lift_distribution" not in out
    assert "error" not in out
    assert saved == ["<cpacs><written/></cpacs>"]

"""The CFD tool names the defaults it applied and passes forces through.

RQ3 (2026-09-21): with the Mach number omitted from the request, the planner
called the tool without it, the default 0.78 applied silently, and the report
never named Mach. The tool now says which flight-condition inputs it filled.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemma_agent as g  # noqa: E402


def _handler():
    return g.TOOLS["su2_run_aero"]["handler"]


def _patch(monkeypatch, captured):
    import su2_mcp.cpacs_adapter as a

    def fake_run_adapter(_xml, flight_conditions=None, **kw):
        captured["fc"] = flight_conditions
        return "<cpacs/>", {
            "solver": "su2_cfd",
            "CL": 0.178,
            "CD": 0.74,
            "L_over_D": 0.2405,
            "lift_force_N": 1807.6,
            "force_basis": "coefficient x ISA dynamic pressure ...",
        }

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_find_existing_artifact", lambda *_a, **_k: None)


def test_omitted_mach_is_named_and_defaulted(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, captured)
    out = _handler()(cpacs_path="canards.xml", aoa=2.0, altitude_ft=10000.0)
    assert out["flight_condition_defaults_applied"] == ["mach"]
    assert captured["fc"] == {"mach": 0.78, "aoa": 2.0, "altitude_ft": 10000.0}
    assert list(out)[0] == "flight_condition_defaults_applied"


def test_fully_stated_condition_applies_no_default(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, captured)
    out = _handler()(cpacs_path="canards.xml", mach=0.6, aoa=2.0, altitude_ft=35000.0)
    assert out["flight_condition_defaults_applied"] == []
    assert captured["fc"]["mach"] == 0.6


def test_forces_pass_through_from_the_adapter(monkeypatch):
    _patch(monkeypatch, {})
    out = _handler()(cpacs_path="canards.xml")
    assert out["flight_condition_defaults_applied"] == ["mach", "aoa", "altitude_ft"]
    assert out["lift_force_N"] == 1807.6
    assert "ISA" in out["force_basis"]


def test_schema_describes_the_new_fields():
    desc = g.TOOLS["su2_run_aero"]["schema"]["function"]["description"]
    assert "lift_force_N" in desc
    assert "flight_condition_defaults_applied" in desc


def test_step_exported_in_this_process_is_used_without_a_path(monkeypatch, tmp_path):
    """Fresh clone, 2026-09-24: the geometry tool wrote a STEP and the CFD tool
    could not find it because discovery only knew historical directories."""
    step = tmp_path / "aircraft_fused.step"
    step.write_text("ISO-10303-21;")
    cpacs = tmp_path / "x.xml"
    cpacs.write_text("<cpacs/>")

    import tigl_mcp.cpacs_adapter as ta

    monkeypatch.setattr(
        ta, "run_adapter", lambda _xml, output_dir=None: ("<cpacs/>", {"step_path": str(step), "step_source": "docker"})
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    import su2_mcp.cpacs_adapter as sa

    def fake_su2(_xml, flight_conditions=None, step_path=None, mesh_path=None, **kw):
        captured["step_path"] = step_path
        captured["mesh_path"] = mesh_path
        return "<cpacs/>", {"solver": "su2_cfd", "CL": 0.1, "CD": 0.01}

    monkeypatch.setattr(sa, "run_adapter", fake_su2)
    g.TOOLS["tigl_export_geometry"]["handler"](cpacs_path=str(cpacs), output_dir=str(tmp_path))
    _handler()(cpacs_path=str(cpacs), mach=0.78, aoa=2.0)
    assert captured["step_path"] == str(step)
    assert captured["mesh_path"] is None


def test_surface_size_m_reaches_the_adapter_and_forces_a_fresh_mesh(monkeypatch, tmp_path):
    """Chord-defined rungs (RQ1, 2026-09) need the absolute cell size the
    adapter already accepts; the tool passes it through."""
    import su2_mcp.cpacs_adapter as sa

    captured: dict = {}

    def fake_su2(_xml, flight_conditions=None, **kw):
        captured.update(kw)
        return "<cpacs/>", {"solver": "su2_cfd", "CL": 0.1, "CD": 0.01}

    monkeypatch.setattr(sa, "run_adapter", fake_su2)
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_find_existing_artifact", lambda suffix, *_a, **_k: "old.step" if suffix == ".step" else "old.su2")
    _handler()(cpacs_path="x.xml", mach=0.78, aoa=2.0, altitude_ft=35000.0, surface_size_m=0.1765)
    assert captured["surface_size_m"] == 0.1765
    assert captured["mesh_path"] is None  # a stale mesh must not be reused for a new rung
    assert captured["step_path"] == "old.step"
    assert "surface_size_m" in g.TOOLS["su2_run_aero"]["schema"]["function"]["parameters"]["properties"]


def test_refinement_plateau_is_judged_by_the_tool(monkeypatch):
    """RQ3 budget test: the planner read the solver's inner converged flag as the
    1 % plateau. The tool now states plateau_met from its own coefficients."""
    import su2_mcp.cpacs_adapter as sa

    runs = iter(
        [
            {"solver": "su2_cfd", "CL": 0.178, "CD": 0.740, "mesh_n_elem": 41985, "cauchy_triggered": True},
            {"solver": "su2_cfd", "CL": 0.214, "CD": 0.759, "mesh_n_elem": 99045, "cauchy_triggered": True},
            {"solver": "su2_cfd", "CL": 0.2145, "CD": 0.7595, "mesh_n_elem": 280342, "cauchy_triggered": True},
        ]
    )
    monkeypatch.setattr(sa, "run_adapter", lambda _xml, **kw: ("<cpacs/>", next(runs)))
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_find_existing_artifact", lambda *_a, **_k: None)
    g._RUNG_HISTORY.clear()
    kw = dict(cpacs_path="ladder.xml", mach=0.78, aoa=2.0, altitude_ft=35000.0)
    r1 = _handler()(surface_density=30, **kw)["refinement"]
    r2 = _handler()(surface_density=60, **kw)["refinement"]
    r3 = _handler()(surface_density=120, **kw)["refinement"]
    assert r1["rung"] == 1 and r1["plateau_met"] is None
    assert r2["rung"] == 2 and r2["plateau_met"] is False and r2["dCL_rel_pct"] == 16.82
    assert r3["rung"] == 3 and r3["plateau_met"] is True


def test_no_refinement_field_without_coefficients(monkeypatch):
    import su2_mcp.cpacs_adapter as sa

    monkeypatch.setattr(sa, "run_adapter", lambda _xml, **kw: ("<cpacs/>", {"solver": "su2_cfd", "error": {"type": "solver_failure"}}))
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_find_existing_artifact", lambda *_a, **_k: None)
    out = _handler()(cpacs_path="x.xml", mach=0.78, aoa=2.0, altitude_ft=35000.0)
    assert out["refinement"] is None


def test_converged_is_renamed_for_the_planner(monkeypatch):
    """The adapter's 'converged' means only that CL/CD were parsed; the RQ3
    budget test showed the planner reading it as the refinement plateau."""
    import su2_mcp.cpacs_adapter as sa

    monkeypatch.setattr(
        sa,
        "run_adapter",
        lambda _xml, **kw: ("<cpacs/>", {"solver": "su2_cfd", "CL": 0.1, "CD": 0.01, "converged": True}),
    )
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_find_existing_artifact", lambda *_a, **_k: None)
    g._RUNG_HISTORY.clear()
    out = _handler()(cpacs_path="x.xml", mach=0.78, aoa=2.0, altitude_ft=35000.0)
    assert "converged" not in out
    assert out["coefficients_parsed"] is True

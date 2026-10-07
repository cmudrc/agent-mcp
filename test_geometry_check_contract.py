"""The geometry check reaches the planner, and the CFD tool's override is explicit.

Added 2026-10-07 with tigl-mcp's geometry check and su2-mcp's refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemma_agent as g  # noqa: E402


def test_tigl_tool_returns_the_geometry_check_right_after_the_path(
    monkeypatch, tmp_path
):
    from tigl_mcp import cpacs_adapter as a

    check = {
        "checked": True,
        "findings": [
            {
                "type": "wing_detached",
                "severity": "fault",
                "component": "W",
                "message": "gap 2.179 m",
            }
        ],
    }

    def fake_run_adapter(xml, output_dir=None, **_k):
        step = Path(output_dir) / "aircraft_fused.step"
        step.write_bytes(b"ISO-10303-21;")
        return xml, {
            "step_path": str(step),
            "step_source": "docker_tigl_closed_solids",
            "geometry_check": check,
            "components": [],
            "step_bytes": b"x",
        }

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda _p, xml: None)
    out = g.TOOLS["tigl_export_geometry"]["handler"](
        cpacs_path=str(tmp_path / "a.xml"), output_dir=str(tmp_path / "out")
    )
    assert list(out)[:2] == ["step_path", "geometry_check"]
    assert out["geometry_check"]["findings"][0]["type"] == "wing_detached"


def test_su2_tool_exposes_the_override_and_defaults_it_off():
    props = g.TOOLS["su2_run_aero"]["schema"]["function"]["parameters"]["properties"]
    assert props["ignore_geometry_findings"]["type"] == "boolean"
    assert props["ignore_geometry_findings"]["default"] is False
    assert (
        "geometry_fault" in g.TOOLS["su2_run_aero"]["schema"]["function"]["description"]
    )


def test_planner_prompt_names_the_check():
    assert "geometry_check" in g.SYSTEM_PROMPT
    assert "su2_run_aero refuses such" in g.SYSTEM_PROMPT

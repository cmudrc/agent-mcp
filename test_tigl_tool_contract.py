"""The geometry tool must not report success when it exported no CAD.

Found 2026-09-16 in the RQ2 ablation logs: with Docker down the TiGL adapter
returned ``step_source='unavailable'`` and no ``step_path``, the tool passed
that through as a normal result, and the planner invented a placeholder path
in 7 of 8 runs. The CFD server then refused a file that never existed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemma_agent as g  # noqa: E402


def _handler():
    return g.TOOLS["tigl_export_geometry"]["handler"]


def test_missing_step_is_a_structured_error(monkeypatch, tmp_path):
    def fake_run_adapter(_xml, output_dir=None):
        return "<cpacs/>", {"wing_count": 1, "step_source": "unavailable"}

    import tigl_mcp.cpacs_adapter as a

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)

    out = _handler()(cpacs_path=str(tmp_path / "x.xml"), output_dir=str(tmp_path))
    assert "error" in out
    assert out["error"]["type"] == "geometry_export_failed"
    assert "unavailable" in out["error"]["message"]
    assert "step_path" not in out


def test_step_path_leads_the_response(monkeypatch, tmp_path):
    def fake_run_adapter(_xml, output_dir=None):
        return "<cpacs/>", {
            "metadata": {"creator": "x"},
            "components": [{"uid": "a"}] * 20,
            "step_path": "pipeline_output/aircraft_fused.step",
            "step_source": "docker_tigl_closed_solids",
        }

    import tigl_mcp.cpacs_adapter as a

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)

    out = _handler()(cpacs_path=str(tmp_path / "x.xml"), output_dir=str(tmp_path))
    assert next(iter(out)) == "step_path"
    assert out["step_path"] == "pipeline_output/aircraft_fused.step"

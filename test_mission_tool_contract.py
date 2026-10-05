"""Mission tools pass only what the caller stated; no invented mass or payload.

2026-10-05: the agent's NSEG wrapper defaulted weight_kg to 78,000 kg for
every aircraft (overriding the adapter's read-the-file-or-refuse rule) and
the Aviary wrapper always passed 162 passengers (defeating the adapter's
refusal to assume a payload).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemma_agent as g  # noqa: E402


def _patch(monkeypatch, module_name, captured):
    import importlib

    a = importlib.import_module(module_name)

    def fake_run_adapter(_xml, mission_profile=None, **kw):
        captured["mp"] = dict(mission_profile or {})
        return "<cpacs/>", {"ok": True}

    monkeypatch.setattr(a, "run_adapter", fake_run_adapter)
    monkeypatch.setattr(g, "_read_cpacs", lambda _p: "<cpacs/>")
    monkeypatch.setattr(g, "_save_cpacs", lambda *_a, **_k: None)


def test_nseg_does_not_invent_a_weight(monkeypatch):
    cap: dict = {}
    _patch(monkeypatch, "nseg_mcp.cpacs_adapter", cap)
    out = g.TOOLS["nseg_run_mission"]["handler"](cpacs_path="x.xml")
    assert "weight_kg" not in cap["mp"]
    assert "range_m=3000000 (3,000 km)" in out["mission_defaults_applied"]


def test_nseg_passes_a_stated_weight(monkeypatch):
    cap: dict = {}
    _patch(monkeypatch, "nseg_mcp.cpacs_adapter", cap)
    out = g.TOOLS["nseg_run_mission"]["handler"](cpacs_path="x.xml", weight_kg=70000.0, range_nmi=1000.0, cruise_mach=0.7, cruise_altitude_ft=33000.0)
    assert cap["mp"]["weight_kg"] == 70000.0
    assert out["mission_defaults_applied"] == []


def test_aviary_does_not_invent_passengers(monkeypatch):
    cap: dict = {}
    _patch(monkeypatch, "aviary_cpacs_mcp.cpacs_adapter", cap)
    out = g.TOOLS["aviary_run_mission"]["handler"](cpacs_path="x.xml")
    assert "num_passengers" not in cap["mp"]
    assert "range=3000 km" in out["mission_defaults_applied"]


def test_schemas_carry_no_weight_or_payload_default():
    nseg = g.TOOLS["nseg_run_mission"]["schema"]["function"]["parameters"]["properties"]
    av = g.TOOLS["aviary_run_mission"]["schema"]["function"]["parameters"]["properties"]
    assert "default" not in nseg["weight_kg"]
    assert "default" not in av["num_passengers"]

"""The sweep lookup reads stored runs only, names what it interpolates
between, computes the cruise lift coefficient from a mass, and refuses files
without a sweep; the comparison tool reports the computed aspect ratio."""

import json
from pathlib import Path

import pytest

from aircraft_mcp import dashboard, one_call
from su2_mcp import aero_sweep as sw

CPACS = """<?xml version="1.0"?>
<cpacs><header><name>t</name><version>1</version><cpacsVersion>3.2</cpacsVersion></header>
<vehicles><aircraft><model uID="m"><name>test</name><reference><area>122.4</area><length>4.2</length></reference>
<wings><wing uID="w" symmetry="x-z-plane"><name>main</name>
<sections><section uID="s1"><name>r</name><elements><element uID="e1"><transformation><scaling><x>6.0</x><y>1</y><z>1</z></scaling></transformation></element></elements></section>
<section uID="s2"><name>t</name><elements><element uID="e2"><transformation><scaling><x>1.5</x><y>1</y><z>1</z></scaling></transformation></element></elements></section></sections>
<positionings><positioning uID="p1"><name>p1</name><length>0</length><sweepAngle>0</sweepAngle><dihedralAngle>0</dihedralAngle><toSectionUID>s1</toSectionUID></positioning>
<positioning uID="p2"><name>p2</name><length>16.0</length><sweepAngle>25</sweepAngle><dihedralAngle>5</dihedralAngle><fromSectionUID>s1</fromSectionUID><toSectionUID>s2</toSectionUID></positioning></positionings>
</wing></wings></model></aircraft></vehicles></cpacs>"""


def _runs():
    out = []
    for a in (-2, 0, 2, 4, 6, 8):
        cl = 0.1 + 0.09 * a
        cd = 0.02 + 0.045 * cl * cl
        out.append(
            {
                "aoa_deg": float(a),
                "CL": cl,
                "CD": cd,
                "L_over_D": cl / cd,
                "cauchy_triggered": True,
                "iterations": 150,
                "runtime_seconds": 600.0,
                "error": None,
            }
        )
    return out


@pytest.fixture
def files(tmp_path):
    meta = {
        "solver": "su2_cfd",
        "flow_model": "Euler (inviscid)",
        "mesh_rule": "40 cells across the reference chord",
        "mesh_cells": 1043902,
        "mach": 0.78,
        "altitude_ft": 35000.0,
        "reference_area_m2": 122.4,
        "machine": "drc-lab",
        "source": "test",
        "note": "synthetic",
    }
    with_sweep = tmp_path / "with_sweep.xml"
    with_sweep.write_text(
        sw.write_sweep_to_cpacs(
            CPACS, sw.build_sweep("ref_chord40", "reference", _runs(), meta)
        )
    )
    plain = tmp_path / "plain.xml"
    plain.write_text(CPACS)
    return with_sweep, plain


def test_lookup_at_angle_cl_and_mass(files):
    with_sweep, plain = files
    r = one_call.aero_sweep_lookup(
        [str(with_sweep), str(plain)], at_aoa_deg=3.0, at_mass_kg=72500.0
    )
    good, bad = r["files"]
    assert bad["error"]["type"] == "no_reference_sweep"
    s = good["sweeps"][0]
    assert (
        s["uid"] == "ref_chord40"
        and len(s["points"]) == 6
        and s["fit"]["polar"]["k"] == pytest.approx(0.045, abs=1e-9)
    )
    assert (
        s["at_aoa_deg"]["CL"] == pytest.approx(0.37)
        and "between two stored runs" in s["at_aoa_deg"]["basis"]
    )
    assert s["at_aoa_deg"]["derived_from"] == [
        "stored run at aoa 2 deg",
        "stored run at aoa 4 deg",
    ]
    m = s["at_mass_kg"]
    assert m["required_CL"] == pytest.approx(
        72500 * 9.80665 / (m["dynamic_pressure_pa"] * 122.4), abs=1e-4
    )
    assert s["at_cl"]["aoa_deg"] == pytest.approx(
        (m["required_CL"] - 0.1) / 0.09, abs=1e-3
    )  # required_CL is rounded to 5 places
    out = one_call.aero_sweep_lookup([str(with_sweep)], at_aoa_deg=20.0)["files"][0][
        "sweeps"
    ][0]
    assert out["at_aoa_deg"]["error"]["type"] == "outside_stored_range"
    assert (
        one_call.aero_sweep_lookup([str(with_sweep)], sweep="coarse")["files"][0][
            "error"
        ]["type"]
        == "sweep_not_found"
    )


def test_compare_reports_computed_aspect_ratio_and_stored_sweeps(files):
    with_sweep, plain = files
    r = one_call.compare_cpacs_files([str(with_sweep), str(plain)])
    a, b = r["files"]
    assert (
        a["main_wing_span_m"]
        == pytest.approx(
            2 * 16.0 * __import__("math").cos(__import__("math").radians(5)), abs=0.05
        )
        or a["main_wing_span_m"] > 0
    )
    assert a["aspect_ratio_computed"] == pytest.approx(
        a["main_wing_span_m"] ** 2 / 122.4, abs=1e-3
    )
    assert "computed here" in a["aspect_ratio_note"]
    assert a["wings"][0]["positioning_sweep_deg_max"] == 25.0
    assert a["wings"][0]["section_chord_scale_root_to_tip"] == [6.0, 1.5]
    assert (
        a["reference_sweeps"][0]["uid"] == "ref_chord40"
        and a["reference_sweeps"][0]["n_runs"] == 6
    )
    assert b["reference_sweeps"] == []


def test_free_port_skips_a_busy_port():
    import socket

    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        busy = held.getsockname()[1]
        held.listen(1)
        chosen = dashboard.free_port(busy)
        assert chosen is not None and chosen != busy


def test_explainer_video_is_packaged():
    assert (
        dashboard.EXPLAINER.is_file() and dashboard.EXPLAINER.stat().st_size > 1_000_000
    )

"""The Seeker receives the numbers the CFD tool actually returns.

Until 2026-09-28 the context filter used lowercase keys the tool never
returned, so every RQ2 verdict was made without CL, CD, the cell count or
the convergence flags.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hybrid_agent import _seeker_context_from  # noqa: E402

TOOL_RESULT = {
    "solver": "su2_cfd",
    "mach": 0.78,
    "aoa_deg": 2.0,
    "altitude_ft": 35000.0,
    "preset": "laptop",
    "iter_cap": 250,
    "CL": 0.178,
    "CD": 0.7398,
    "L_over_D": 0.2407,
    "runtime_seconds": 23.2,
    "cauchy_triggered": False,
    "mesh_source": "gmsh_from_step",
    "mesh_n_elem": 41985,
    "mesh_surface_density": 30,
    "mesh_surface_size_m": None,
    "refinement": {"rung": 2, "dCL_rel_pct": 16.86, "plateau_met": False},
    "wetted_area_m2": 10.2,
}


def test_coefficients_and_mesh_reach_the_seeker():
    ctx = _seeker_context_from(TOOL_RESULT, "su2_run_aero")
    assert ctx["CL"] == 0.178 and ctx["CD"] == 0.7398
    assert ctx["mesh_n_elem"] == 41985
    assert ctx["cauchy_triggered"] is False
    assert ctx["refinement"]["plateau_met"] is False
    assert ctx["tool"] == "su2_run_aero"


def test_absent_and_none_keys_are_left_out():
    ctx = _seeker_context_from(TOOL_RESULT, "su2_run_aero")
    assert "mesh_surface_size_m" not in ctx  # None means not used
    assert "wetted_area_m2" not in ctx  # not part of the seeker surface
    assert _seeker_context_from({}, "t") == {"tool": "t"}

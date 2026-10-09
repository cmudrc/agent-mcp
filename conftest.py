"""Test isolation: session logs written during tests go to a temporary
folder, never to the user's ~/aircraft-runs, and no report is rendered at
session end unless a test asks for it."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_runs_dir(tmp_path_factory, monkeypatch):
    runs = tmp_path_factory.mktemp("aircraft-runs")
    monkeypatch.setenv("AIRCRAFT_RUNS_DIR", str(runs))
    monkeypatch.setenv("AIRCRAFT_LOG_REPORT", "0")
    monkeypatch.delenv("AIRCRAFT_LOG", raising=False)
    monkeypatch.delenv("AIRCRAFT_PARTICIPANT", raising=False)
    monkeypatch.delenv("AIRCRAFT_PARENT_SESSION", raising=False)
    # Code under test sets AIRCRAFT_SESSION_ID itself (RunLog.start, the
    # gateway); recording it first makes pytest remove it again afterwards.
    monkeypatch.setenv("AIRCRAFT_SESSION_ID", "unset")
    monkeypatch.delenv("AIRCRAFT_SESSION_ID")
    # Agent output folders go to a temporary place, never pipeline_output/.
    monkeypatch.setenv("AIRCRAFT_OUTPUT_ROOT", str(tmp_path_factory.mktemp("pipeline_output")))
    from aircraft_mcp import run_files

    run_files.reset_for_tests()
    yield runs
    run_files.reset_for_tests()

"""The container build files stay consistent with the repository.

These tests read files only. The image itself is tested by building it and
running container/selftest.py inside it (container/README.md).
"""

import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTAINER = HERE / "container"


def test_dockerfile_pins_every_other_repository_to_a_full_commit_id():
    text = (CONTAINER / "Dockerfile").read_text()
    pins = dict(re.findall(r"^ARG ([A-Z0-9_]+_COMMIT)=(\S+)$", text, flags=re.M))
    assert pins.pop("AGENT_MCP_COMMIT") == "unknown"  # this repository comes from the build context
    assert set(pins) == {
        "TIGL_MCP_COMMIT", "SU2_MCP_COMMIT", "PYCYCLE_MCP_COMMIT",
        "NSEG_MCP_COMMIT", "AVIARY_CPACS_MCP_COMMIT",
    }
    assert all(re.fullmatch(r"[0-9a-f]{40}", v) for v in pins.values()), pins
    # plain syntax only: the file must build without BuildKit
    assert "--mount" not in text and "<<" not in text and "# syntax=" not in text


def test_lock_files_pin_exact_versions():
    conda = [x for x in (CONTAINER / "conda-linux-64.lock").read_text().splitlines() if x.startswith("https://")]
    assert conda and all(re.search(r"#[0-9a-f]{32}$", x) for x in conda)
    assert any("/dlr-sc/linux-64/tigl3-" in x for x in conda)
    pins = [x for x in (CONTAINER / "constraints.txt").read_text().splitlines() if x and not x.startswith("#")]
    assert pins and all("==" in x and " @ " not in x for x in pins)
    by_name = dict(x.split("==") for x in pins)
    assert by_name["numpy"].startswith("1.")  # the solver stack is pinned below numpy 2
    assert by_name["gmsh"] == "4.15.2" and by_name["openmdao"] == "3.36.0"


def test_entrypoint_is_executable_and_names_the_study_files():
    entry = CONTAINER / "entrypoint.sh"
    assert os.access(entry, os.X_OK)
    for name in ("aircraft_A.xml", "aircraft_B.xml"):
        assert name in entry.read_text()
        assert (HERE / "kiro" / "study" / "files" / name).is_file()


def test_selftest_reference_matches_the_steps_the_selftest_reports():
    ref = json.loads((CONTAINER / "selftest_reference.json").read_text())
    source = (CONTAINER / "selftest.py").read_text()
    assert ref["steps"]
    for step, values in ref["steps"].items():
        assert f'"{step}"' in source, step
        assert values


def test_selftest_stays_away_from_the_study_task_conditions():
    """These files are public. The self-test must not look the stored runs up
    at the design take-off mass or fly the tools' default mission with the
    file's own engine, because those results are answers to study tasks."""
    source = (CONTAINER / "selftest.py").read_text()
    assert "at_mass_kg" not in source and "at_cl" not in source
    assert '"design_thrust_n": 60000.0' in source and '"weight_kg": 65000.0' in source
    ref = json.loads((CONTAINER / "selftest_reference.json").read_text())
    lookup = ref["steps"]["stored-run lookup"]
    assert set(lookup) == {"aoa_deg", "CL", "CD"} and lookup["aoa_deg"] == 7.5
    assert set(ref["steps"]["the two study files"]) == {"file_A", "stored_runs_A", "file_B", "stored_runs_B"}

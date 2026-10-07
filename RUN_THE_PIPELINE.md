# Running the pipeline from the ground up

This is the one document to follow if you have never touched the project.
It takes you from an empty directory to (1) a no-LLM run of the engineering
servers on an example aircraft and (2) the local Gemma agent choosing and
calling the same servers from a plain-English request.

How it was checked. On 2026-10-05, on a fresh folder (macOS, Apple silicon,
Python 3.13), these were run as written here: the installer (with
`--no-models`), the manual install, the five test suites, the no-LLM
pipeline, the tools called directly on fresh copies of both example
aircraft, the gateway start-up and the session viewer. The agent with the
model (§7) was run on a fresh clone on 2026-09-24 but not on 10-05, because
Ollama was busy with another experiment. Not run in either check, because
they were already present on that machine: the prerequisites, the SU2
install script, the model pull and the TiGL image build. Nothing inside
Kiro (§8) has been run yet. Where a step differs on Linux or Windows it says so.

Testers: after the quick start, read [TESTER_GUIDE.md](TESTER_GUIDE.md)
(a one-hour session) and [PROMPT_TEST_SHEET.md](PROMPT_TEST_SHEET.md)
(prompts to try, with the behaviour to expect).

---

## Quick start: one path, about an hour on a blank Mac

Most of the hour is downloads: the model (9.6 GB), the TiGL Docker image
build (several minutes to tens of minutes) and the Python packages. Plan for
20 GB of free disk and 16 GB of RAM.

**Step 1. Prerequisites (once).** Details and the Linux column are in §2.

```bash
xcode-select --install          # git; a dialog opens, accept it
# Homebrew: paste the one-line command from https://brew.sh, then run the
# two lines it prints to put brew on your PATH
brew install python@3.13 ollama
```

Install Docker Desktop from https://www.docker.com/products/docker-desktop/
and open it once. On first start it asks you to accept its terms and offers
a sign-in; that is yours to do, the pipeline does not need a Docker account.

**Step 2. Install everything else with the installer.**

```bash
mkdir aircraft && cd aircraft
curl -fsSL https://raw.githubusercontent.com/cmudrc/agent-mcp/main/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh --no-launch
```

It clones the repositories side by side, builds `.venv` with every server and
the solver libraries at the pinned versions, installs SU2 into
`~/.local/su2/bin`, starts Ollama if it is not running and pulls `gemma4:e4b`,
and copies the two example aircraft to `./D150_v30.xml` and `./canards.xml`.
The tools write their results into the aircraft file they are given, so you
always work on these copies; the originals stay in
`aircraft-analysis/examples/`. It is safe to rerun.

**Step 3. Build the TiGL Docker image** (the long step; Docker Desktop must
be running):

```bash
(cd tigl-mcp && docker build --platform linux/amd64 -t tigl-mcp:dev .)
```

**Step 4. Every new terminal**, from the `aircraft` folder:

```bash
source .venv/bin/activate
export PATH="$HOME/.local/su2/bin:$PATH"; export OPENMDAO_REPORTS=0
```

Put the `export` line in `~/.zshrc` so you do not forget it (§4 explains).
After a restart, Docker Desktop and Ollama must be running again: open
Docker, and start Ollama with `ollama serve &` (or
`brew services start ollama` to keep it running).

**Step 5. Check the install without the model** (about a minute):

```bash
bash aircraft-analysis/run_pipeline.sh canards --mcps tigl su2
```

It must end with `STEP export: docker_tigl_closed_solids` (TiGL ran in
Docker), `CL=0.1780455806, CD=0.7398386842` and `Pipeline Complete`. If not,
see §10.

**Step 6. The agent's first run** (a few minutes; the first model call also
loads the model into memory, which took up to about 2 minutes in our runs):

```bash
python agent-mcp/hybrid_agent.py --cpacs canards.xml --no-seeker \
  --prompt "Export the geometry, then run SU2 at the laptop preset at Mach 0.78 and 2 degrees, and report CL, CD and L/D."
```

The final report should give CL 0.178, CD 0.740 and L/D 0.241, the same
solver result as step 5.

**Step 7. Read the session record:**

```bash
aircraft-runs --open
```

That opens the newest session's `report.html` in your browser: the prompt,
the answer, a list of things to check before using the numbers, where the
time went, and every model call and tool call in full (§7).

That is the whole path. The sections below explain each piece, the manual
install if the installer fails, and how to connect another MCP client such
as Kiro (§8).

---

## 1. What you are installing

Five engineering programs, each behind one small server that exposes exactly
one tool. They all read from and write to one shared aircraft file (CPACS, an
XML format from DLR), so every number in the file records which tool wrote it
and when.

| Repository | Wraps | The tool does |
|---|---|---|
| `tigl-mcp` | TiGL (DLR) | reads the aircraft shape from CPACS and exports a STEP CAD file |
| `su2-mcp` | Gmsh + SU2 | meshes the STEP file and runs an inviscid (Euler) CFD case: lift and drag coefficients |
| `pycycle-mcp` | pyCycle (NASA, on OpenMDAO) | sizes a turbofan: thrust and fuel consumption at the cruise point |
| `nseg-mcp` | NSEG-style mission (Breguet) | flies a mission with those numbers: block fuel, takeoff weight |
| `aviary-cpacs-mcp` | NASA Aviary (optional) | the same mission, trajectory-coupled; heavier install |

A sixth server (`openaerostruct-mcp`, vortex-lattice wing aerodynamics)
exists but is not published; the agent registers its tool and returns a
missing-module error if asked to use it.

Two more repositories drive them, and the installer also clones a third:

| Repository | Contains |
|---|---|
| `aircraft-analysis` | `run_pipeline.sh`, the no-LLM driver that calls the servers in a fixed order; the example aircraft (`examples/D150_v30.xml`, a 150-seat airliner; `examples/canards.xml`, a small test body); the deterministic "skill" harnesses; the shared-CPACS manager |
| `agent-mcp` | the agent: a local Gemma model (through Ollama) that reads your request and decides which tools to call; the `aircraft-mcp` gateway for other MCP clients; the session logs and their viewer `aircraft-runs`; the installer `bootstrap.sh`; this document |
| `agentic-bench` | the benchmark the agents were scored on (installed by `bootstrap.sh`; not needed to run anything here) |

Two rules that every server obeys, so you know what to expect: no tool ever
returns a made-up number (a missing program is a structured error, not a
placeholder), and the servers do not guess a property of the aircraft (a
missing reference area or engine is a `missing_input` or
`missing_engine_definition` error). Flight-condition and mission inputs you
leave out are a different matter; see "Things to know" in §7.

Sizes measured on the 10-05 install: the Python environment is 1.4 GB with
Aviary (1.0 GB without), SU2 is 76 MB, the TiGL Docker image is 4.1 GB, and
the Gemma model is 9.6 GB on disk. The agent uses about 10 GB of memory
while it runs.

---

## 2. Prerequisites

| What | macOS | Linux (Ubuntu/Debian) | Windows |
|---|---|---|---|
| git, curl | `xcode-select --install` | `apt-get install git curl` | install WSL2 (`wsl --install`) and follow the Linux column inside it |
| Python 3.12 or 3.13 | `brew install python@3.13` | `apt-get install python3.13 python3.13-venv` | as Linux, inside WSL2 |
| Docker (for the TiGL geometry export; see §4) | Docker Desktop, https://docker.com | Docker Engine | Docker Desktop with the WSL2 backend |
| Ollama (only for the agent, §7) | `brew install ollama` or the app from https://ollama.com | the installer does it (`curl -fsSL https://ollama.com/install.sh \| sh`) | inside WSL2, as Linux |

Check:

```bash
git --version && python3.13 --version && docker --version && ollama --version
```

On macOS the system `python3` is an older Python; the installer looks for
`python3.13` or `python3.12` first, which is what Homebrew installs.

---

## 3. Get the code and build the Python environment (manual install)

Skip this section if `bootstrap.sh` worked. It is what the installer does,
spelled out.

Everything lives side by side in one parent directory. That layout is
assumed by every script, so do not nest one repository inside another.

```bash
mkdir aircraft && cd aircraft
for r in tigl-mcp su2-mcp pycycle-mcp nseg-mcp aviary-cpacs-mcp aircraft-analysis agent-mcp; do
  git clone https://github.com/cmudrc/$r.git
done
```

Create one virtual environment at the parent level and install every server
into it in editable mode (so a `git pull` in a repository takes effect
without reinstalling):

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -e tigl-mcp -e su2-mcp -e pycycle-mcp -e nseg-mcp
python -m pip install -e agent-mcp
```

Then the solver libraries the servers call. These are not declared as
package dependencies on purpose (the servers must import even where the
solver is absent, and report its absence), so install them explicitly and at
these versions:

```bash
python -m pip install "numpy<2" "gmsh==4.15.2" "openmdao==3.36.0" "om-pycycle" \
                      "pyvista==0.48.4" "pillow" "matplotlib"
```

Why the pins: OpenMDAO 3.36.0 is the version pyCycle 4.4 and Aviary 0.9.10
were tested with; newer OpenMDAO breaks Aviary with unit errors. `gmsh`
from pip bundles its own binary, so no system Gmsh is needed.

Optional, only if you want the Aviary mission tool (adds dymos and Aviary,
several minutes):

```bash
python -m pip install -e aviary-cpacs-mcp
```

Make working copies of the example aircraft. The tools write their results
into the file they are given, so keep the originals clean:

```bash
cp aircraft-analysis/examples/D150_v30.xml aircraft-analysis/examples/canards.xml .
```

Confirm the servers import and the console commands exist:

```bash
python -c "import tigl_mcp, su2_mcp, pycycle_mcp, nseg_mcp; print('imports ok')"
which tigl-mcp su2-mcp pycycle-mcp nseg-mcp aircraft-mcp aircraft-runs
```

Every time you open a new shell, activate the environment again
(`source .venv/bin/activate` from the parent directory). The agent scripts
re-launch themselves under this `.venv` if you forget, but the pipeline
driver, `aircraft-runs` and `aircraft-mcp` do not.

---

## 4. SU2 and the TiGL Docker image

**SU2** (the CFD solver) is a separate binary. The installer runs this
script for you; on a manual install run it yourself. It tries conda first,
then a prebuilt binary from su2code.github.io, and puts `SU2_CFD` in
`~/.local/su2/bin`:

```bash
bash su2-mcp/scripts/install_su2.sh
export PATH="$HOME/.local/su2/bin:$PATH"
SU2_CFD --help | head -3
```

Put the `export PATH=...` line in your shell profile (`~/.zshrc` on macOS).
**Forgetting it is the most common cause of an apparently broken run**: the
SU2 tool then returns a `missing_binary` error naming the install page, and
nothing downstream can run. The agent scripts add `~/.local/su2/bin`
themselves and so does the pipeline driver; the `aircraft-mcp` gateway does
not add it for the servers it starts, so an MCP client must pass it (§8). On Windows, SU2 runs inside WSL2
only.

**Parallel SU2.** The downloaded binary is serial, which is why fine meshes
take hours. The runner launches SU2 in parallel automatically when it can
prove it is safe: `mpirun` on PATH and an MPI-capable binary (a
`SU2_CFD_MPI` sibling, or a binary that links an MPI library). Set
`SU2_MPI_RANKS=N` to pin the rank count (`1` forces serial; unset or `0`
means all physical cores). To get an MPI binary, build SU2 from source with
`-Dwith-mpi=enabled` (meson) against OpenMPI, or install a cluster module
(`su2-mcp/docs/PARALLEL_SU2.md` has the measured build); running N copies of
a *serial* binary would silently repeat the same case N times, so the runner
refuses to do that and says why in `launch_reason`.

**TiGL** (the geometry library) has no reliable native install on macOS
Apple silicon, so the geometry server uses a Docker image when native
bindings are absent. The installer does not build it. Build it once (it
pulls conda packages from the `dlr-sc` channel, so allow several minutes to
tens of minutes depending on your connection; the image is about 4 GB):

```bash
cd tigl-mcp
docker build --platform linux/amd64 -t tigl-mcp:dev .
cd ..
```

Docker Desktop must be running whenever you run the geometry step
(`open -a Docker` on macOS). Without it the geometry tool returns a
`geometry_export_failed` error; it never fabricates a STEP file. On Linux
with native `tigl3`/`tixi3` conda packages installed into the environment,
the Docker image is not needed; the route taken is recorded in the CPACS
file as `stepExportSource`.

One more environment variable, always:

```bash
export OPENMDAO_REPORTS=0
```

Otherwise every pyCycle run drops a report directory into whatever directory
you ran from.

---

## 5. Check the install with the test suites

Run each repository's tests separately (running them together produces
import-mismatch errors from duplicate test file names). None of them needs
SU2, Docker or Ollama: where a solver would be called, the tests replace only
that call and exercise the real adapter logic around it.

```bash
python -m pip install pytest pytest-cov
(cd tigl-mcp    && python -m pytest -q)
(cd su2-mcp     && python -m pytest -q)
(cd pycycle-mcp && python -m pytest -q)
(cd nseg-mcp    && python -m pytest -q)
(cd agent-mcp   && python -m pytest -q test_*.py)
```

On the 2026-10-05 fresh clone these reported 61, 123 (1 skipped), 67 and 40
passed, and agent-mcp 71 passed with 1 skipped (the skipped module covers
the unpublished sixth server). Numbers grow as tests are added; a failure,
not a different count, is what to look at.

---

## 6. Run the pipeline without an LLM

`aircraft-analysis/run_pipeline.sh` calls the servers in the fixed order
geometry, CFD, engine, mission on one CPACS file. Start with the small test
body and only the first two servers; this is the fastest end-to-end check:

```bash
export PATH="$HOME/.local/su2/bin:$PATH"; export OPENMDAO_REPORTS=0
bash aircraft-analysis/run_pipeline.sh canards --mcps tigl su2
```

What you should see (53 s on the 10-05 fresh clone, on a machine busy with
another job; 30 s on 09-24):

```
[1/2] Running TIGL adapter...
      Done in 13.01s
      Wings: 1, Fuselages: 1, Components: 2
      STEP export: docker_tigl_closed_solids
[2/2] Running SU2 adapter...
      Meshing STEP → SU2 via Gmsh (preset=laptop, surface_density=30)...
      Running SU2_CFD (Mach=0.78, AoA=2.0°, iter_cap=250, timeout=600s)...
      Done in 40.16s
      CL=0.1780455806, CD=0.7398386842, L/D=0.2407
  Pipeline Complete
  Versions created: 3
  Final CPACS:      pipeline_output/cpacs_final.xml
```

`STEP export: docker_tigl_closed_solids` tells you the real TiGL ran (in
Docker). The results go into `aircraft-analysis/pipeline_output/`: one
numbered copy of the CPACS file per tool that wrote to it (`cpacs_v0.xml`,
`cpacs_v1.xml`, ...), the final file, the STEP and mesh, and
`pipeline_results.json`. This driver works on those copies and leaves the
example files untouched.

Then the airliner with the default set of four servers (geometry, CFD,
engine, Breguet mission), about 40 s at the default `laptop` mesh. The
takeoff weight is not in the CPACS file and no tool guesses it, so the
mission step needs it stated (78,000 kg is the D150's):

```bash
bash aircraft-analysis/run_pipeline.sh d150 --weight 78000
```

Leave `--weight` off and the first three steps run and the fourth stops with
`Cannot fly a mission: weight_kg not available` and `Pipeline FAILED`. That
is the no-guessing rule at work, not a broken install.

With the weight, the run ends like this:

```
[3/4] Running PYCYCLE adapter...
      TSFC=0.64751 lb/(lbf·hr), Fn=26528.4 N, OPR=30.55, BPR=5.1
[4/4] Running NSEG adapter...
      Fuel burned: 12654.9 kg
      Range: 1880.6 nm, Time: 4.45 hr, Fuel fraction: 0.162
  Pipeline Complete
  Versions created: 5
```

These `laptop`-mesh numbers are a smoke check. The lift coefficient on this
coarse mesh (0.074) is far below what the airliner needs to fly level; the
paper's numbers come from the refinement ladder below, at millions of cells.

Useful variations:

```bash
bash aircraft-analysis/run_pipeline.sh d150 --weight 78000 --mach 0.85 --aoa 3.0      # other flight point
bash aircraft-analysis/run_pipeline.sh d150 --weight 78000 --su2-preset workstation    # finer mesh (several minutes of CFD)
bash aircraft-analysis/run_pipeline.sh d150 --weight 78000 --mcps tigl su2 pycycle aviary  # Aviary instead of NSEG
bash aircraft-analysis/run_pipeline.sh path/to/your_aircraft.xml --weight KG   # any CPACS file
```

Pick exactly one mission server per run, `nseg` or `aviary`. The
`laptop` mesh is a smoke check, not a trustworthy result; the
paper's converged numbers come from the refinement ladder below.

The deterministic harnesses in `aircraft-analysis/scripts/` run the
judgement loops the agent would otherwise run, with no LLM, so every number
in the paper can be reproduced:

```bash
python aircraft-analysis/scripts/run_converged_su2.py --help   # mesh-refinement ladder
python aircraft-analysis/scripts/run_cruise_match.py  --help   # thrust = drag, fuel closure
python aircraft-analysis/scripts/run_engine_resize.py --help   # smallest engine that closes the mission
python aircraft-analysis/scripts/run_aoa_sweep.py     --help   # best L/D and trim angle
```

---

## 7. Run the agent

The agent is a small open-weight model, Gemma 4 E4B, served locally by Ollama.
It reads your request, chooses tools from the same servers, calls them
one at a time, and writes a report. Nothing leaves your machine.

The installer pulls the model. On a manual install:

```bash
ollama serve &          # or start the Ollama app; skip if it is already running
ollama pull gemma4:e4b
ollama list             # gemma4:e4b should be listed
```

Then, from the parent directory, with Docker running and the two `export`
lines set, on the working copy (not on the file in `aircraft-analysis/examples/`):

```bash
python agent-mcp/hybrid_agent.py --cpacs D150_v30.xml \
  --prompt "Export the geometry, then run SU2 at the laptop preset at Mach 0.78 and 2 degrees, and report CL, CD and L/D."
```

What you should see (the 09-24 fresh-clone run, trimmed; that run pointed
`--cpacs` at the example file itself, which is why the path differs):

```
--- Turn 1 [planner=gemma4:e4b] ---
  CALL  tigl_export_geometry({"cpacs_path": "aircraft-analysis/examples/D150_v30.xml"})
  ←     {"step_path": "pipeline_output/aircraft_fused.step", ...}
--- Turn 2 [planner=gemma4:e4b] ---
  CALL  su2_run_aero({"cpacs_path": "aircraft-analysis/examples/D150_v30.xml", "mach": 0.78, "aoa": 2})
      Meshing STEP → SU2 via Gmsh (preset=laptop, surface_density=30)...
      Running SU2_CFD (Mach=0.78, AoA=2°, iter_cap=250, timeout=600s)...
  ←     {"flight_condition_defaults_applied": ["altitude_ft"], "solver": "su2_cfd", "mach": 0.78, ...}
--- Turn 3 [planner=gemma4:e4b] ---
  CALL  report_done({"summary": "The geometry was successfully exported using TiGL, ..."})
=== FINAL (planner) ===
The geometry was successfully exported using TiGL, creating the STEP file at
pipeline_output/aircraft_fused.step. SU2 aerodynamics were run using the
'laptop' preset at Mach 0.78 and 2 degrees AoA. The resulting coefficients
are: CL = 0.074 ...
```

Three things to notice. The planner chose the two tools and their order
from the sentence. The CFD tool used the STEP file the geometry tool had
just written without being told its path. And because the prompt did not
give an altitude, the tool says so (`flight_condition_defaults_applied:
["altitude_ft"]`) rather than filling it silently. Each planner turn takes
half a minute to two minutes on a laptop; the solver calls take what they
took in §6. With the Seeker on (the default), each CFD run is followed by a
rendered pressure image and the Seeker's verdict, about 1 to 1.5 minutes
more per run (measured 70 to 96 s, 2026-09-29).

Without `--prompt` you get an interactive prompt (`> `). Each line you type
is a new request with a fresh planner and its own session log, but the same
process: a STEP file exported for the same aircraft file earlier in the
session is reused, and a second CFD run at the same flight condition is
compared with the first in the tool's `refinement` field. Ctrl+D exits.
Other options:

```bash
python agent-mcp/hybrid_agent.py --help
--no-seeker        # skip the multimodal observer that inspects pressure plots after each CFD run
--max-turns N      # tool-call budget (default 8)
--trace-jsonl f    # full arguments and results of every call, one JSON line each
```

The tools write their results into the aircraft file. To start clean, copy
the example again (`cp aircraft-analysis/examples/D150_v30.xml .`). Outputs
go to `pipeline_output/` in the folder you run from (`aircraft_fused.step`,
`su2_run/` with the mesh, `history.csv` and `vol_solution.vtu`), and the
Seeker's images to `hybrid_seeker_renders/`; later runs overwrite them.

`agent-mcp/gemma_agent.py` is the same planner without the observer.

**Every session is recorded.** When the agent starts it prints a line such
as

```
[aircraft-runs] session log: /Users/you/aircraft-runs/20261005-212343-5a2e47
```

That folder holds the whole session: the prompt, every request sent to the
model and every reply (with token counts and timings), every tool call with
its full arguments and result, the images the observer judged, and the final
report (`events.jsonl`, one JSON object per line; `meta.json`; long values
such as base64 CAD unaltered in `blobs/`). The folder name is the start time
in UTC. When the session ends the agent writes `report.html` there, a single
page you can open in any browser, offline. To render it yourself, or every
session at once:

```bash
aircraft-runs                       # the newest session, plus ~/aircraft-runs/index.html
aircraft-runs --open                # the same, and open it in the browser
aircraft-runs ~/aircraft-runs/20261005-212343-5a2e47 --open
aircraft-runs --all --open          # every session, and open the index
```

The page shows the prompt and, right under it, the final report. Then the
model, duration, token totals and outcome; a list of what to check before
using the numbers (tool errors, a solver run that did not converge within
its iteration cap, flight-condition inputs the CFD tool filled with
defaults, an observer verdict other than acceptable), each linked to its
step; a check that lists, and marks in the report text, any number in the
final report that no tool result, tool argument, observer verdict or the
prompt contains; and a bar of where the time went, with the time Ollama
spent loading the model shown apart from the time the model spent
answering. Below that is each step in order (planner turns, tool calls with
arguments and results, observer verdicts with the image).
`AIRCRAFT_LOG=0` turns logging off, `AIRCRAFT_RUNS_DIR` moves the folder,
and `AIRCRAFT_PARTICIPANT=P01` tags a user-study session. `--trace-jsonl`
still works as before.

The logs never record the restricted dataset of §11. If a path or name
matching its patterns appears in a session (the aircraft file, the prompt,
the working folder, a tool argument), the session writes one
`restricted_not_recorded` event, without the matching text, and records
nothing after it.

Things to know about the agent. The first four were measured in the
paper's tests; the last three were checked on 2026-10-05 by calling the
tools directly on fresh copies of the example files:

- It follows the order you give. If you give none, it may call CFD before
  geometry; the CFD server refuses (no STEP file) and the agent reports
  that and stops.
- It never invents a solver number, but it reports whatever a tool returns
  and does not question it. The servers' own range and input checks are the
  guard; keep them.
- If you leave out Mach, angle of attack or altitude, the CFD tool fills the
  default (Mach 0.78, 2 degrees, 35,000 ft) and names it in
  `flight_condition_defaults_applied`; a Mach of zero or an impossible
  altitude is refused. State the flight point.
- Asked for a quantity no tool provides (structures, for example), it says
  so in the report.
- On a fresh aircraft file the engine tool refuses until a CFD run has put a
  drag coefficient in the file (`missing_engine_definition`), and the
  mission tool refuses until both the CFD and engine runs have
  (`missing_input` naming `cd0`, `k`, `tsfc_1_per_s`, `max_thrust_n`). So
  "block fuel" needs geometry, CFD, engine and mission, in that order.
- The mission tools never assume a takeoff weight or a passenger count.
  Give them in the request, or the tool refuses with `missing_input` (the
  example D150 file states no takeoff mass; 78,000 kg is a reasonable D150
  value to give). Range and cruise point have defaults (3,000 km, Mach
  0.78, 35,000 ft); each response lists the ones it used in
  `mission_defaults_applied`. The Aviary tool uses Aviary's own
  aerodynamics, not the CFD result.
- The mesh presets are `laptop` (default, about 50 k cells on the D150,
  about 40 s of meshing and solving), `workstation` (about 300 k cells,
  several minutes) and `industry` (about 2 M cells, tens of minutes to over
  an hour; the tool stops a run at 2 hours). On the laptop preset
  `cauchy_triggered` is usually false: the lift has not settled within the
  250-iteration cap, so treat those numbers as a smoke check.

---

## 8. Running the servers for another MCP client (Kiro and others)

Each server is also a standalone MCP server. For a client on the same
machine use stdio; for a remote client use HTTP:

```bash
su2-mcp --transport stdio
su2-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

`tigl-mcp`, `pycycle-mcp`, `nseg-mcp` and `aviary-cpacs-mcp` take the same
flags.

**One endpoint for everything:** the `aircraft-mcp` gateway ships inside
`agent-mcp` (package `aircraft_mcp`). `pip install -e agent-mcp` installs the
`aircraft-mcp` command. It mounts the installed servers behind a single MCP
endpoint with namespaced tools (`tigl_*`, `su2_*`, `pycycle_*`, `nseg_*`,
`aviary_*`; 54 tools without Aviary, 62 with it), adds stage-progress
events, an optional local dashboard, and a `run_aircraft_analysis` tool that
hands a whole analysis to the local Gemma planner:

```bash
aircraft-mcp                               # stdio, what an IDE starts
aircraft-mcp --dashboard-port 8765         # plus http://127.0.0.1:8765
aircraft-mcp --transport streamable-http --port 8800
```

The gateway passes its own `PATH` to the servers it starts. Started from an
app rather than from your terminal, that `PATH` may not contain
`~/.local/su2/bin` or `docker`; on the 10-05 fresh clone, a gateway started
with the bare system `PATH` reported SU2 as not installed, and the same
gateway with the terminal's `PATH` found it. So an MCP client's
configuration should set `PATH` explicitly (below). Other variables set for
the gateway do not reach the servers (the MCP library passes them only
`PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL` and `TERM`), so
`OPENMDAO_REPORTS=0` does not apply there and calls to the `pycycle_*`
tools may leave `*_out/` report folders behind (`run_aircraft_analysis`
sets it for its own run).

The dashboard shows the active stage, call durations, the latest pressure
render, and the session reports at `/sessions/`. It listens on 127.0.0.1
only and answers only requests addressed to `127.0.0.1:<port>` or
`localhost:<port>`, so a web page from another site cannot read the
session logs through it. The gateway writes every call it serves, with full
arguments and results, to its own session folder under `~/aircraft-runs`;
`run_aircraft_analysis` returns the folder of the planner's session as
`agent_session_dir`, so the two are linked.

**Kiro.** Kiro is AWS's agentic IDE. Installing it and signing in needs your
own account (an AWS Builder ID, a social login, or your organization's
single sign-on); we have not run Kiro ourselves yet, so the steps
below follow our files and Kiro's documentation and are not yet tried
inside Kiro.

1. Open the `aircraft` folder as the Kiro workspace.
2. Copy the configuration and the rules:

   ```bash
   mkdir -p .kiro/settings .kiro/steering
   cp agent-mcp/kiro/mcp.json .kiro/settings/mcp.json
   cp agent-mcp/kiro/steering.md .kiro/steering/aircraft.md
   ```

3. Edit `.kiro/settings/mcp.json` and replace the three placeholders:
   `command` with the full path of `.venv/bin/aircraft-mcp`,
   `AIRCRAFT_MCP_PROJECT_ROOT` with the full path of the `aircraft` folder,
   and `PATH` with the output of `echo $PATH` from a terminal where you ran
   the two `export` lines of the quick start.
4. In Kiro's chat, ask it to call `gateway_status`. It should list
   `tigl`, `su2`, `pycycle`, `nseg` (and `aviary`) as mounted.
5. Use full paths for aircraft files in your requests (for example
   `/Users/you/aircraft/canards.xml`); the servers resolve relative paths
   against the folder Kiro starts them in, which we have not checked.

On the 10-05 fresh clone the gateway answered in 0.7 s and listed its 62
tools in 3.5 s. Kiro CLI's documented MCP start-up timeout is 5 s
(`mcp.initTimeout`); if the CLI reports that the server failed to start,
raise it with `kiro-cli settings mcp.initTimeout 10000`.

Two ways to use it. Kiro's own model can call the namespaced tools one by
one; with our local Gemma model doing the same over this raw surface, 0 of 5
runs completed (2026-10-02; every failure was reported honestly), and with
Kiro's models it has not been measured. Or ask Kiro to call
`run_aircraft_analysis` with your request and the aircraft file; the local
Gemma planner then does the analysis on your machine and returns its report
(1 of 1 correct in 89 s on 2026-10-02; 286 s on 2026-10-05 with Ollama
shared). Whatever you type in Kiro, and every tool result its model reads,
goes to Kiro's cloud models (Kiro runs on Amazon Bedrock); use only the
public example aircraft there.

The hook files in `agent-mcp/kiro/hooks/` record Kiro's own prompts into
the session logs too. They are written from Kiro's documentation and not yet
verified inside Kiro; see `agent-mcp/kiro/README.md`. Without them the
gateway's session folder still records every tool call Kiro makes.

**Mode A test harness.** To drive the gateway with the local model over real
MCP, as an external client would (from the `aircraft` folder, where the
working copy `canards.xml` is):

```bash
python agent-mcp/mcp_agent.py --prompt "Open canards.xml, export the CAD, mesh at surface density 30, run the solver, report CL and CD."
```

This is the measuring instrument behind the 0 of 5 above, not the way to get
a result. A complete, verified example that drives the geometry and CFD
servers end to end over their endpoints alone (open the CPACS file, export
STEP, mesh at the laptop sizing, run SU2, read lift and drag from the
history) is [examples/mcp_endpoints_d150.py](examples/mcp_endpoints_d150.py);
it also documents the two integration traps: the base64 content arguments
and the fact that the named presets live in the adapter, with
`surface_density` as their endpoint equivalent. The agent in §7 does not go
through this transport: it calls the same adapters in-process through
identical typed schemas, which is faster and leaves the transport out of
the experiments.

---

## 9. The installer

`agent-mcp/bootstrap.sh` does §3, §4's SU2 step and §7's model pull: clone,
venv, editable installs, the solver libraries at the pinned versions, SU2,
Ollama (Linux only; on macOS install it yourself, §2), the model pull, and
working copies of the two example aircraft. It checks for the TiGL Docker
image but does not build it. Without `--no-launch` it ends by starting the
agent's interactive prompt on `./D150_v30.xml`.

```bash
mkdir aircraft && cd aircraft
curl -fsSL https://raw.githubusercontent.com/cmudrc/agent-mcp/main/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh --no-launch      # add --no-models to skip the 9.6 GB pull
```

Checked on 2026-10-05 on a fresh folder with `--no-models` (the model was
already present), SU2 already installed and the Docker image already built:
it finished in 86 s with a warm package cache, all packages imported, and the
interactive prompt started on the working copy. If the script fails partway,
the manual steps above are the same thing spelled out, and it is safe to
rerun.

`bootstrap.ps1` sets up the Python side on native Windows and leaves SU2 to
WSL2, where the agent cannot reach it; it has not been run end to end. On
Windows, install WSL2 and run `bootstrap.sh` inside it.

---

## 10. When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `Need Python 3.12 or 3.13` from the installer | only the system Python is installed | `brew install python@3.13` (macOS) |
| `missing_binary: SU2_CFD not found on PATH` | the `export PATH` line is missing in this shell | `export PATH="$HOME/.local/su2/bin:$PATH"` |
| `su2_get_su2_status` says `installed: false` in Kiro or another client, although `SU2_CFD` works in your terminal | the client started the gateway without your `PATH` | set `PATH` in the client's MCP configuration (§8) |
| `geometry_export_failed ... step_source='unavailable'` | Docker not running, or image not built | `open -a Docker`, then §4 `docker build` |
| `CPACS file not found: D150_v30.xml` | no working copy in the folder you ran from | `cp aircraft-analysis/examples/D150_v30.xml .` |
| `missing_input: No mesh or STEP geometry provided` | CFD called before geometry | ask for the geometry export first (the pipeline does it) |
| `missing_engine_definition` from the engine tool | no CFD result in the file yet | run geometry and CFD first |
| `Cannot fly a mission: cd0, k, tsfc_1_per_s, max_thrust_n not available` | no CFD and engine results in the file yet | run geometry, CFD and engine first |
| `missing_input: the CPACS file states no reference area` | your CPACS file lacks `reference/area` and `length` | add them; the tool will not borrow another aircraft's |
| `invalid_input: mach=0 is outside [0.05, 3]` | flight condition not stated and filled with zero | state Mach, angle and altitude |
| `inconsistent_inputs: ... SU2 computed that drag at Mach 0.7` (engine or mission tool) | the file's latest CFD or engine result is for another cruise point | run CFD, engine and mission for one cruise point before starting the next |
| the agent stops right after `No mesh or STEP geometry provided` | it called CFD before exporting the geometry, and its rules stop it at the first error | start the request with "Export the geometry, then ..." |
| agent prints nothing for a minute or two at the first turn | Ollama is loading the model into memory | wait; the report page shows this as "Model loading" |
| `model "gemma4:e4b" not found` or a connection error to Ollama | model not pulled, or Ollama not running | `ollama pull gemma4:e4b`; `ollama serve &` or open the Ollama app |
| `*_out/` directories appearing everywhere | `OPENMDAO_REPORTS` not set | `export OPENMDAO_REPORTS=0` |
| pytest import-mismatch errors | suites run together | run each repository's tests separately |
| agent turns take many minutes, machine swapping | Ollama (about 10 GB) plus browsers | close other applications; 16 GB RAM is the floor |
| `unbound variable EXTRA_ARGS` from `run_pipeline.sh` | old copy of the script on bash 3.2 | `git pull` in `aircraft-analysis` |

---

## 11. Data you must not redistribute

One of the aircraft datasets used in the paper is licensed to CMU for
academic use only and is not in any repository. If you are given it, keep
it outside every repository and never copy it into a repository, an upload,
or a shared folder. Every repository carries an ignore rule and a
content-checking pre-commit hook that blocks a commit containing it
(`scripts/install_hooks.sh` in the project root installs the hooks). The
session logs in `~/aircraft-runs` stop recording a session as soon as a path
or name matching the dataset appears in it (§7), and the dashboard never
serves a render from a folder with such a name. The two example aircraft in
`aircraft-analysis/examples/` are free to use.

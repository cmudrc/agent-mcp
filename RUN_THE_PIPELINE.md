# Running the pipeline from the ground up

This is the one document to follow if you have never touched the project.
It takes you from an empty directory to (1) a no-LLM run of the five
engineering servers on an example aircraft and (2) the local Gemma agent
choosing and calling the same servers from a plain-English request. Every
command below was run on a fresh clone on 2026-09-24 (macOS, Apple silicon);
where a step differs on Linux or Windows it says so.

Read the short "What you are installing" first. It explains what each piece
is, so the error messages later make sense.

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
exists but is not published yet; the agent registers its tool and reports
the missing module if asked to use it.

Two more repositories drive them:

| Repository | Contains |
|---|---|
| `aircraft-analysis` | `run_pipeline.sh`, the no-LLM driver that calls the servers in a fixed order; the example aircraft (`examples/D150_v30.xml`, a 150-seat airliner; `examples/canards.xml`, a small test body); the deterministic "skill" harnesses; the shared-CPACS manager |
| `agent-mcp` | the agent: a local Gemma model (through Ollama) that reads your request and decides which tools to call; the installer `bootstrap.sh`; this document |

Two rules that every server obeys, so you know what to expect: no tool ever
returns a made-up number (a missing program is a structured error, not a
placeholder), and no tool guesses an input it needs (a missing reference area
or passenger count is a `missing_input` error).

Rough sizes, measured on the verification install: the Python environment
is 1.1 GB with OpenMDAO and pyCycle (more with Aviary), SU2 is 76 MB, the
TiGL Docker image is 4.1 GB, and the Gemma model is 10.5 GB. Plan for 20 GB
of disk and 16 GB of RAM (the agent alone uses about 10 GB while it runs).

---

## 2. Prerequisites

| What | macOS | Linux (Ubuntu/Debian) | Windows |
|---|---|---|---|
| git, curl | preinstalled / `brew install git` | `apt-get install git curl` | install WSL2 (`wsl --install`) and follow the Linux column inside it |
| Python 3.12 or 3.13 | `brew install python@3.13` | `apt-get install python3.13 python3.13-venv` | as Linux, inside WSL2 |
| Docker Desktop (for the TiGL geometry export; see §4) | https://docker.com | Docker Engine | Docker Desktop with the WSL2 backend |
| Ollama (only for the agent, §7) | `brew install ollama` | `curl -fsSL https://ollama.com/install.sh \| sh` | inside WSL2, as Linux |

Check:

```bash
git --version && python3 --version && docker --version
```

`python3 --version` must print 3.12.x or 3.13.x.

---

## 3. Get the code and build the Python environment

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
python3 -m venv .venv
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

Confirm the servers import and their console commands exist:

```bash
python -c "import tigl_mcp, su2_mcp, pycycle_mcp, nseg_mcp; print('imports ok')"
which tigl-mcp su2-mcp pycycle-mcp nseg-mcp
```

Every time you open a new shell, activate the environment again
(`source .venv/bin/activate` from the parent directory). The agent scripts
re-launch themselves under this `.venv` if you forget, but the pipeline
driver does not.

---

## 4. SU2 and the TiGL Docker image

**SU2** (the CFD solver) is a separate binary. The install script tries
conda first, then a prebuilt binary from su2code.github.io, and puts
`SU2_CFD` in `~/.local/su2/bin`:

```bash
bash su2-mcp/scripts/install_su2.sh
export PATH="$HOME/.local/su2/bin:$PATH"
SU2_CFD --help | head -3
```

Put the `export PATH=...` line in your shell profile. **Forgetting it is the
most common cause of an apparently broken run**: the SU2 tool then returns a
`missing_binary` error naming the install page, and nothing downstream can
run. On Windows, SU2 runs inside WSL2 only.

**TiGL** (the geometry library) has no reliable native install on macOS
Apple silicon, so the geometry server uses a Docker image when native
bindings are absent. Build it once (it pulls conda packages from the
`dlr-sc` channel, so allow several minutes to tens of minutes depending on
your connection; the image is about 4 GB):

```bash
cd tigl-mcp
docker build --platform linux/amd64 -t tigl-mcp:dev .
cd ..
```

Docker Desktop must be running whenever you run the geometry step
(`open -a Docker` on macOS). Without a kernel the geometry tool returns a
`geometry_export_failed` error; it never fabricates a STEP file. On Linux with
native `tigl3`/`tixi3` conda packages installed into the environment, the
Docker image is not needed; the route taken is recorded in the CPACS file as
`stepExportSource`.

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

On the verification install these reported 61, 102, 67 and 40 passed, and
agent-mcp 11 passed with 1 skipped (the skipped module covers a sixth,
OpenAeroStruct-based server that is not published yet). Numbers grow as
tests are added; a failure, not a different count, is what to look at.

---

## 6. Run the pipeline without an LLM

`aircraft-analysis/run_pipeline.sh` calls the servers in the fixed order
geometry, CFD, engine, mission on one CPACS file. Start with the small test
body and only the first two servers; this is the fastest end-to-end check
(about a minute):

```bash
export PATH="$HOME/.local/su2/bin:$PATH"; export OPENMDAO_REPORTS=0
bash aircraft-analysis/run_pipeline.sh canards --mcps tigl su2
```

What you should see (30 s on a laptop):

```
[1/2] Running TIGL adapter...
      Done in 7.51s
      Wings: 1, Fuselages: 1, Components: 2
      STEP export: docker_tigl_closed_solids
[2/2] Running SU2 adapter...
      Meshing STEP → SU2 via Gmsh (preset=laptop, surface_density=30)...
      Running SU2_CFD (Mach=0.78, AoA=2.0°, iter_cap=250, timeout=600s)...
      Done in 22.38s
      CL=0.1780455806, CD=0.7398386842, L/D=0.2407
  Pipeline Complete
  Versions created: 3
  Final CPACS:      pipeline_output/cpacs_final.xml
```

`STEP export: docker_tigl_closed_solids` tells you the real TiGL ran (in
Docker). The results go into `aircraft-analysis/pipeline_output/`: one
numbered copy of the CPACS file per tool that wrote to it (`cpacs_v0.xml`,
`cpacs_v1.xml`, ...), the final file, the STEP and mesh, and
`pipeline_results.json`.

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
bash aircraft-analysis/run_pipeline.sh d150 --mach 0.85 --aoa 3.0      # other flight point
bash aircraft-analysis/run_pipeline.sh d150 --su2-preset workstation    # finer mesh (~2 min CFD)
bash aircraft-analysis/run_pipeline.sh d150 --mcps tigl su2 pycycle aviary  # Aviary instead of NSEG
bash aircraft-analysis/run_pipeline.sh path/to/your_aircraft.xml        # any CPACS file
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
It reads your request, chooses tools from the same five servers, calls them
one at a time, and writes a report. Nothing leaves your machine.

Start Ollama and pull the model once (about 10 GB):

```bash
ollama serve &          # or start the Ollama app; skip if it is already running
ollama pull gemma4:e4b
ollama list             # gemma4:e4b should be listed
```

Then, from the parent directory, with Docker running and the two `export`
lines set:

```bash
python agent-mcp/hybrid_agent.py --cpacs aircraft-analysis/examples/D150_v30.xml \
  --prompt "Export the geometry, then run SU2 at the laptop preset at Mach 0.78 and 2 degrees, and report CL, CD and L/D."
```

What you should see (this is the verification run, trimmed):

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
took in §6.

Without `--prompt` you get an interactive prompt. Other options:

```bash
python agent-mcp/hybrid_agent.py --help
--no-seeker        # skip the multimodal observer that inspects pressure plots after each CFD run
--max-turns N      # tool-call budget (default 8)
--trace-jsonl f    # full arguments and results of every call, one JSON line each
```

`agent-mcp/gemma_agent.py` is the same planner without the observer.

Things to know about the agent, all measured in the paper:

- It follows the order you give. If you give none, it may call CFD before
  geometry; the CFD server refuses (no STEP file) and the agent reports
  that and stops.
- It never invents a solver number, but it reports whatever a tool returns
  and does not question it. The servers' own range and input checks are the
  guard; keep them.
- If you leave out an input such as the Mach number, the tool names the
  default it applied in `flight_condition_defaults_applied`, and a Mach of
  zero or an impossible altitude is refused. State the flight point.

---

## 8. Running the servers for another MCP client

Each server is also a standalone MCP server. For a client on the same
machine (Claude Desktop, Cursor, your own agent) use stdio; for a remote
client use HTTP:

```bash
su2-mcp --transport stdio
su2-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

`tigl-mcp`, `pycycle-mcp`, `nseg-mcp` and `aviary-cpacs-mcp` take the same
flags. The agent in §7 does not go through this transport: it calls the same
adapters in-process through identical typed schemas, which is faster and
leaves the transport out of the experiments.

---

## 9. The one-command installer

`agent-mcp/bootstrap.sh` does §3 to §7 (clone, venv, editable installs,
solver libraries, SU2, Ollama, model pull, then launches the agent).
It does not build the TiGL Docker image; do §4's `docker build` yourself.

```bash
mkdir aircraft && cd aircraft
curl -fsSL https://raw.githubusercontent.com/cmudrc/agent-mcp/main/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh --no-launch      # add --no-models to skip the 10 GB pull
```

`bootstrap.ps1` is the Windows equivalent. If the script fails partway, the
manual steps above are the same thing spelled out, and it is safe to rerun.

---

## 10. When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `missing_binary: SU2_CFD not found on PATH` | the `export PATH` line is missing in this shell | `export PATH="$HOME/.local/su2/bin:$PATH"` |
| `geometry_export_failed ... step_source='unavailable'` | Docker not running, or image not built | `open -a Docker`, then §4 `docker build` |
| `missing_input: No mesh or STEP geometry provided` | CFD called before geometry | run `tigl` first (the pipeline does; tell the agent the order) |
| `missing_input: the CPACS file states no reference area` | your CPACS file lacks `reference/area` and `length` | add them; the tool will not borrow another aircraft's |
| `invalid_input: mach=0 is outside [0.05, 3]` | flight condition not stated and filled with zero | state Mach, angle and altitude |
| `*_out/` directories appearing everywhere | `OPENMDAO_REPORTS` not set | `export OPENMDAO_REPORTS=0` |
| pytest import-mismatch errors | suites run together | run each repository's tests separately |
| agent turns take many minutes, machine swapping | Ollama (10 GB) plus browsers | close other applications; 16 GB RAM is the floor |
| `unbound variable EXTRA_ARGS` from `run_pipeline.sh` | old copy of the script on bash 3.2 | `git pull` in `aircraft-analysis` |

---

## 11. Data you must not redistribute

One of the aircraft datasets used in the paper is licensed to CMU for
academic use only and is not in any repository. If you are given it, keep
it outside every repository and never copy it into a repository, an upload,
or a shared folder. Every repository carries an ignore rule and a
content-checking pre-commit hook that blocks a commit containing it
(`scripts/install_hooks.sh` in the project root installs the hooks). The two
example aircraft in `aircraft-analysis/examples/` are free to use.

# Tester guide: one hour with the aircraft-analysis agent

## What this is

You type an aircraft-analysis request in plain English, and a small AI model
running on this computer (Gemma 4 E4B, through Ollama) decides which
engineering programs to run and in what order: TiGL for the geometry, SU2
for the aerodynamics, pyCycle for the engine, NSEG or NASA Aviary for the
mission. Every number in its answer should come from one of those programs
(the session record flags any that does not), and when a program cannot do
something it returns an error rather than a guess.
Every session is recorded in full, and you can read the record as a web page
when you are done.

## Before you start (5 minutes)

The install is in [RUN_THE_PIPELINE.md](RUN_THE_PIPELINE.md), quick start.
If someone set up the machine for you, check these:

- Docker Desktop is running (whale icon in the menu bar).
- Ollama is running (`ollama list` shows `gemma4:e4b`).
- In a terminal, from the project folder (called `aircraft` below):

  ```bash
  cd aircraft
  source .venv/bin/activate
  export PATH="$HOME/.local/su2/bin:$PATH"; export OPENMDAO_REPORTS=0
  export AIRCRAFT_PARTICIPANT=P01     # your participant code, if you were given one
  ls D150_v30.xml canards.xml         # the two working copies of the example aircraft
  ```

If the two files are missing, copy them:
`cp aircraft-analysis/examples/D150_v30.xml aircraft-analysis/examples/canards.xml .`

Only these two aircraft may be used: the D150 (a public 150-seat airliner
model from DLR) and the canard test body (a small test shape from TiGL's
public tests). Do not load any other aircraft file.

## Suggested plan for the hour

| Minutes | Do |
|---|---|
| 0 to 5 | the checks above |
| 5 to 15 | the first request below, and read its report |
| 15 to 50 | your own requests; [PROMPT_TEST_SHEET.md](PROMPT_TEST_SHEET.md) has about 20 ideas, with the behaviour to expect and a column for what you saw |
| 50 to 60 | look over your session reports and write down problems (last section) |

## Start the agent: local chat

```bash
python agent-mcp/hybrid_agent.py --cpacs D150_v30.xml
```

You get a `>` prompt. Type a request and press Enter. Each request is
answered on its own (the model does not remember your earlier requests) and
gets its own session record. Ctrl+D exits. To use the other aircraft, exit
and start again with `--cpacs canards.xml`.

Two options worth knowing:

- `--no-seeker` skips the image check after each CFD run, which saves about
  1 to 1.5 minutes per run. Leave the Seeker on for at least one request to
  see it.
- `--prompt "..."` runs one request and exits, instead of the `>` prompt.

The tools write their results into the aircraft file. Results from earlier
requests stay in it, and the engine and mission tools read them. To start
from a clean file, exit, then `cp aircraft-analysis/examples/D150_v30.xml .`
and start again.

## Start the agent: Kiro

Kiro is AWS's agentic IDE. It needs your own Kiro sign-in, and the setup in
[RUN_THE_PIPELINE.md §8](RUN_THE_PIPELINE.md#8-running-the-servers-for-another-mcp-client-kiro-and-others)
(copy two files, fill in three values). We have not yet run these steps inside
Kiro ourselves, so tell us where they go wrong.

Before you use Kiro, know where your text goes. What you type in Kiro, and
every tool result its model reads, is sent to Kiro's cloud models. With a
personal login (AWS Builder ID or a social login), Kiro may use that content
to improve its service unless you turn off "Content Collection for Service
Improvement" in Kiro's settings; logins through an organization's identity
provider are opted out automatically (Kiro's data-protection page, read
2026-10-05). Use only the two public example aircraft, and type nothing
confidential.

In Kiro's chat, there are two ways to work:

1. **Hand the whole request to the local agent** (recommended to start).
   For example: *Call run_aircraft_analysis with cpacs_path
   /Users/you/aircraft/canards.xml and the prompt "Export the geometry, then
   run SU2 at the laptop preset at Mach 0.78 and 2 degrees, and report CL,
   CD and L/D."* The local agent runs on this computer and returns its
   report and the folder of its session record. Measured: correct in 89 s
   (2026-10-02) and 286 s (2026-10-05, with the model shared with another
   job). It stops a run after 30 minutes unless you pass a larger
   `timeout_seconds`.
2. **Let Kiro's own model call the individual tools** (about 60 of them:
   `tigl_open_cpacs`, `tigl_export_configuration_cad`,
   `su2_generate_mesh_from_step`, and so on). This is harder: our local
   model completed 0 of 5 such runs (2026-10-02). Kiro's own models have not
   been measured on it.

Use full paths for aircraft files in Kiro. While the gateway runs, a
progress page is at http://127.0.0.1:8765 (current stage, recent calls,
the latest pressure image).

## What to type first

```
Export the geometry, then run SU2 at the laptop preset at Mach 0.78 and 2 degrees, and report CL, CD and L/D.
```

On `canards.xml` with `--no-seeker` this gave CL 0.178, CD 0.740 and
L/D 0.241 in 3 min 39 s (2026-10-05). On `D150_v30.xml` the CFD run gives
CL 0.074, CD 0.0213 on about 50,000 cells. These coarse-mesh numbers are a
smoke check, not a result: the lift has not settled within the solver's
250-iteration limit, and the report page says so.

## What you will see

In the terminal, in order:

| Stage | What it prints | Typical time on a laptop |
|---|---|---|
| Model loading (first request only, or after 10 idle minutes) | nothing, then `--- Turn 1` | up to about 2 minutes |
| Planner turn | `--- Turn N [planner=gemma4:e4b] ---`, then `CALL tool(...)` | 30 seconds to 2 minutes per turn |
| Geometry | `CALL tigl_export_geometry`, then `← {"step_path": ...}` | 8 to 15 s |
| CFD, laptop mesh (about 50,000 cells) | `Meshing STEP → SU2 via Gmsh ...`, `Running SU2_CFD ...` | about 40 s |
| CFD, workstation mesh (about 300,000 cells) | the same | several minutes |
| CFD, industry mesh (about 2 million cells) | the same | tens of minutes to over an hour; the tool stops at 2 hours |
| Seeker (if on) | `>>> rendering 3-panel composite`, `>>> SEEKER: verdict=...` | 70 to 96 s |
| Engine (pyCycle) | `CALL pycycle_run_engine` | 7 to 15 s |
| Mission (NSEG) | `CALL nseg_run_mission` | under 1 s |
| Mission (Aviary) | `CALL aviary_run_mission` | about 30 s |
| Answer | `=== FINAL (planner) ===` and the report | |

A simple request takes a few minutes in total. A refinement ladder of four
meshes took 17 to 27 minutes (2026-09-29). The finest meshes in the paper
(13 to 21 million cells) took 6 to 9 hours each on a lab server, so do not
ask for those in a one-hour session.

The planner may stop early with `(agent stopped: planner ended ... without
report_done)` or run out of turns (`max_turns reached`, default 8). Both are
recorded as the outcome; note them on the sheet.

## How to view your session record

When a session starts, the terminal prints its folder, for example
`[aircraft-runs] session log: /Users/you/aircraft-runs/20261005-212343-5a2e47`
(the name is the start time in UTC). After the answer:

```bash
aircraft-runs --open          # the newest session's page
aircraft-runs --all --open    # the list of all sessions
```

The page shows, from the top: your request and the answer; the outcome (for
example "Final report · 2 warnings"); "Check before using the numbers"
(tool errors, a CFD run whose lift did not settle, flight conditions the CFD
tool filled with defaults, a Seeker verdict other than acceptable); "Every
number traced" (any number in the answer that no tool returned is listed and
highlighted); "Where the time went"; and then every step, with each tool
call's full input and output on request.

For a Kiro session, the gateway writes its own record in the same folder
list; a request handed to the local agent has its own record, shown under
the gateway's in the list.

## What the tools can and cannot do

They can:

- export the geometry of the two example aircraft (TiGL, in Docker);
- run an inviscid (Euler) CFD case at a Mach number, angle of attack and
  altitude you choose, on a mesh of the fineness you choose, and report
  CL, CD, L/D and the lift and drag forces;
- compare two CFD runs at the same flight condition and say whether the
  1 percent mesh-refinement rule is met (the `refinement` field);
- size a turbofan to the CFD drag and report thrust, TSFC, OPR and BPR
  (pyCycle);
- fly a mission and report the fuel (NSEG, from the CFD and engine results;
  or NASA Aviary, with its own aerodynamics and engine models);
- give you the 3D flow file (VTU, for ParaView) or a three-view pressure
  picture of the latest CFD run.

They cannot:

- do structures, loads, stresses or weights;
- do viscous or RANS CFD: SU2 runs Euler only, so the drag has no skin
  friction. The tool's description says "Euler / RANS", but it always runs
  Euler;
- change the geometry (span, sweep, chord): there is no geometry editor;
- trim the aircraft, optimise a design or size an engine to a thrust you
  give, in one step. The agent can only run the steps you spell out, for
  example CFD at several angles;
- work on any aircraft other than the two public examples.

Behaviours to expect, measured in the project's tests unless marked:

- The planner reports what the tools return and does not question it.
- If you leave out Mach, angle or altitude, the CFD tool uses Mach 0.78,
  2 degrees and 35,000 ft and says so; the report page lists it.
- The mission tools never assume a takeoff weight or a passenger count:
  give them in your request (78,000 kg is a reasonable D150 weight), or the
  tool refuses with `missing_input`. Range and cruise point have defaults
  (3,000 km, Mach 0.78, 35,000 ft), and the tool names any it used.
- The mission range you give is the cruise distance; climb and descent add
  to it (1,500 nmi asked gave 1,760.7 nm in total, tools called directly,
  2026-10-05).
- On a fresh aircraft file, ask for geometry, then CFD, then engine, then
  mission. Out of order, the tools refuse (checked 2026-10-05); when the CFD
  tool refused, the agent reported it and stopped (measured 2026-09-21).
- The planner has added settings you did not ask for, such as the
  workstation preset (3 of 3 runs of one test, 2026-09-23). Unless you state
  the mesh density, that means a finer mesh and a longer run.

## How to report problems

For each problem, write down:

1. the session folder printed at the start (or the time you ran it);
2. your request, copied exactly;
3. what you expected and what happened;
4. whether the report page's "Check before using the numbers" or "Every
   number traced" flagged it.

Send these and the session's `report.html` to Mayank Dixit (contact in
[README.md](README.md)). The page is one file and opens offline. It contains
your requests word for word, so do not send it anywhere public if you typed
anything about your company. GitHub issues at
https://github.com/cmudrc/agent-mcp/issues are public; use them only for
problems that contain no company information.

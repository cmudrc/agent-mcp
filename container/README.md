# The aircraft-analysis tools in one container

First version, 23 November 2026. Made for a first test on an OpenShift
platform that we cannot see ourselves. Please expect a round or two of
fixes: tell us what fails and we will change it.

## What it is

- One image on Red Hat's `ubi9-minimal` base (RHEL 9), `linux/amd64` only.
- Inside: the gateway `aircraft-mcp` with the five tool servers behind it
  (67 tools), and the solvers they call: SU2 8.4.0 (CFD), gmsh 4.15.2
  (mesher), DLR's TiGL 3.5.0rc1 (geometry), pyCycle on OpenMDAO 3.36.0
  (engine), NSEG and NASA's Aviary 0.9.10 (mission).
- The two study aircraft: A is DLR's public D150, B is the same aircraft
  with a longer, narrower wing. Both carry stored CFD results.
- Not inside: Kiro, or any language model. The container only serves the
  tools; a client (Kiro) connects to it.
- It runs as a non-root user and writes only under `/data`.

## Build

From a fresh clone, with the repository root as the build context:

```bash
git clone https://github.com/cmudrc/agent-mcp
cd agent-mcp
docker build --platform linux/amd64 -f container/Dockerfile --build-arg AGENT_MCP_COMMIT=$(git rev-parse HEAD) -t aircraft-tools .
```

The Dockerfile uses plain syntax only, so `podman build` with the same
arguments should work; we have only built it with docker. To start from an
internal copy of the base image, add
`--build-arg BASE_IMAGE=<your registry>/ubi9-minimal`.

The build needs to download from these places:

| From | What |
|---|---|
| Red Hat's UBI package repositories | system libraries (X, GL, fonts) |
| `micro.mamba.pm` | micromamba, a single file that installs conda packages (checksum verified) |
| `conda.anaconda.org` (conda-forge and DLR's `dlr-sc` channel) | Python 3.12 and DLR's geometry kernel, which DLR publishes only as conda packages |
| `pypi.org` | the Python solver libraries |
| `github.com` | the tool servers at pinned commits, and the SU2 8.4.0 release binary |

Every version is pinned: `conda-linux-64.lock` (exact conda package files),
`constraints.txt` (exact pip versions), and the commit ids at the top of
the Dockerfile. If the build machine cannot reach these places, tell us; we
can send the built image as a file instead.

The build took us about 3 minutes with a fast connection (it is almost all
downloads). The image is about 1.2 GB compressed and 4 GB unpacked.

## Run

```bash
docker run --rm -p 8800:8800 -p 8765:8765 aircraft-tools
```

- Status page: http://localhost:8765 (which tool is running, the recent
  tool calls, a report of every session, and a link that downloads all
  session records as one zip file).
- The tools: `http://localhost:8800/mcp` (MCP over streamable HTTP). This
  is the address an MCP client such as Kiro is given.

## Check it

In a terminal inside the running container:

```bash
aircraft-selftest
```

(With docker: `docker exec <container> aircraft-selftest`. With OpenShift:
the pod's Terminal tab, or `oc exec <pod> -- aircraft-selftest`.)

It starts its own copy of the gateway and runs every real tool once on
aircraft A: geometry export, engine, mission, a quick CFD run, the flow
picture and the Aviary optimiser. Nothing is mocked. It takes about a
minute; `--quick` leaves out the CFD run. Next to each number it prints the
number we got, and says whether yours is the same. The last two lines check
the web address and the status page from inside the container.

This is what we get (user id 1234567 is our stand-in for the arbitrary user
id OpenShift assigns):

```text
reference numbers: our run of 23 November 2026: this image on an Apple laptop, x86-64 emulated by Rosetta
python 3.12.16 at /opt/aircraft/env/bin/python; user id 1234567; working folder /data/selftest/20261123-171338

[ok  ] gateway starts                  3.4 s  tools 67 (same as ours)
[ok  ] SU2 found                       0.0 s  installed True (same as ours)
[ok  ] the two study files             0.0 s  file_A 52ac93b3e4a5 (same as ours); stored_runs_A 13 (same as ours); file_B 27cbb3d193aa (same as ours); stored_runs_B 13 (same as ours)
[ok  ] stored-run lookup               0.2 s  aoa_deg 7.5 (same as ours); CL 0.883464 (same as ours); CD 0.155286 (same as ours)
[ok  ] geometry export (TiGL)          7.7 s  wings 3 (same as ours); fuselages 1 (same as ours); span_m 33.9272 (same as ours); step_file True (same as ours); kernel native_tigl_closed_solids (same as ours)
[ok  ] engine (pyCycle)                7.2 s  thrust_N 60000 (same as ours); fuel_flow_kg_s 1.05723 (same as ours); OPR 30.55 (same as ours)
[ok  ] mission (NSEG)                  0.1 s  fuel_burned_kg 6439.46 (same as ours); distance_nm 760.747 (same as ours)
[ok  ] CFD quick mesh (gmsh, SU2)     23.6 s  cells 50367 (same as ours); CL 0.0926342 (same as ours); CD 0.0237335 (same as ours); solver_converged False (same as ours)
[ok  ] flow picture                    1.7 s  png_bytes 248342 (same as ours); surface_cells 2906 (same as ours)
[ok  ] Aviary runs (its benchmark)     8.7 s  converged True (same as ours); iterations 27 (same as ours); fuel_burned_kg 7309.72 (same as ours)
       this test's tool log: /data/aircraft-runs/20261123-171340-ce4338
[ok  ] web gateway answers             0.2 s  tools 67; servers 5  http://127.0.0.1:8800/mcp
[ok  ] status page answers             0.0 s  healthz 200; page 200  http://127.0.0.1:8765

12 ok, 0 failed, 0 skipped, 55 s. Full results: /data/selftest/20261123-171338/selftest_result.json
```

These numbers only show that each solver runs. None of them is an answer
to a study task: the test uses its own angle, engine thrust, mass and
range, the CFD case is a coarse mesh that is not converged, and the Aviary
line is Aviary's own benchmark aircraft.

We recorded our numbers on an Apple laptop, which runs this image through
x86-64 emulation. We have not run it on a real x86-64 machine yet. The
file checks and the stored-run lookup must be exactly the same everywhere.
For the solver steps, a difference in the last digits is not a problem; a
different CFD cell count is worth telling us.

## On OpenShift

We have not been able to test this part. What we built for it:

- The container does not need root. The Dockerfile sets user 1001, and the
  image also runs under any other user id as long as it is in group 0,
  which is what OpenShift does by default. We tested this with
  `docker run --user 1234567:0`.
- Everything it writes goes under `/data` (session logs, solver output, the
  working copies of the aircraft files). Without a volume there, records
  are lost when the container stops. Use the download link on the status
  page before stopping it, or mount a volume at `/data`.
- Ports: 8800 (the tools, path `/mcp`) and 8765 (the status page).
- Health check: `GET /healthz` on port 8765 answers `ok`.
- Memory: the container uses about 90 MB idle and peaked at about 1.5 GB
  during the self-test. The CFD tool can build much larger meshes when
  asked, so please give it at least 4 GB.
- `/tmp` must be writable (the geometry tool uses temporary files).

Settings, all optional (environment variables):

| Variable | Default in the image | Meaning |
|---|---|---|
| `AIRCRAFT_MCP_PORT` | 8800 | port of the tools |
| `AIRCRAFT_DASHBOARD_PORT` | 8765 | port of the status page |
| `AIRCRAFT_DASHBOARD_ALLOWED_HOSTS` | `*` | host names the status page answers to. Set it to the route's host name to refuse every other name |
| `AIRCRAFT_DATA_DIR` | `/data` | where the working copies of the aircraft files go |
| `AIRCRAFT_LOG` | on | `0` turns the session logs off |

Other commands: `docker run --rm aircraft-tools versions` lists what is
installed, `docker run --rm aircraft-tools selftest` runs the self-test
without the web gateway (its last two lines then say "skip"), and `reset`
puts fresh copies of the two aircraft files in `/data/study`.

## What this first version does not do

- **No login.** Neither port asks who is calling. Whoever can reach port
  8800 can run the tools, and whoever can reach port 8765 can read the
  session logs. Please keep both inside your network.
- **One aircraft file per running gateway.** A gateway works on the first
  aircraft file it is given and refuses a second one until it is restarted,
  so that results from two aircraft cannot be mixed. In the laptop study
  each chat window starts its own gateway. For a hosted study this means
  one container for each participant and aircraft, or a change on our side.
  This is the main design question still open.
- **No Kiro inside.** How people sign in to Kiro on the platform, and
  whether the platform can reach Kiro's service, decide whether Kiro can
  run next to the tools or stays on each laptop.
- **CFD runs on one core.** The SU2 release binary has no MPI. A quick-mesh
  run takes about 20 seconds; fine meshes take many minutes to hours.
- **Quick-mesh CFD numbers differ from our Mac's.** The mesher (gmsh)
  builds a slightly different mesh on Linux than on macOS (50,367 cells
  against 49,668 for the same input), and the quick mesh is coarse and not
  converged, so the lift coefficient for the test case is 0.093 here and
  0.074 on the Mac. We checked that the geometry file is identical and that
  SU2 gives the same numbers to eight digits on the same mesh. The study
  answers come from stored fine-mesh runs and are the same everywhere.
- **`linux/amd64` only.** The SU2 binary and DLR's packages are built for
  that platform.

## What to send us

- The output of `aircraft-selftest`, and the file it names at the end.
- The output of `docker run --rm aircraft-tools versions` (or the same
  command in the pod's terminal: `cat /opt/aircraft/VERSIONS.txt`).
- The container's log from start-up.
- Anything the platform refused or changed (base image, user, ports,
  downloads during the build).

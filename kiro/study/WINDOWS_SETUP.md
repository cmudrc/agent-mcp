# Setting up the study laptop on Windows

Written 28 October 2026 for the facilitator at Boeing, on a loaner laptop
with no git, where Ubuntu, WSL and Docker Desktop each need an approval.
Two routes:

- **Route A, with WSL2 (Ubuntu inside Windows).** Use it if WSL2 with
  Ubuntu is installed and allowed. The pipeline's installer was verified on
  a blank Ubuntu 24.04 on 6 October 2026, through the CFD step, so the
  solver side is known to work. Kiro runs on the Windows side and starts
  the tools inside Ubuntu. The untested part is that link between Kiro and
  Ubuntu.
- **Route B, native Windows, no WSL.** Use it if WSL is not available. The
  tools are ordinary Python programs; Linux is where we tested them, not
  something the code needs. Everything on this route is untested on a real
  Windows laptop.

Neither route needs Docker or git. Please do the first run of either with
us on a call, and send us every error message as it appears.

What works, and what does not, before the optional steps:

| Tool group | Route A | Route B |
|---|---|---|
| File comparison (`compare_cpacs_files`), stored-sweep lookup (`aero_sweep_lookup`), the CPACS readers | yes | yes |
| Engine (`pycycle_run_engine`), mission (`nseg_run_cpacs_mission`) | yes | yes |
| Live CFD (`su2_run_aero`) | yes, the installer puts SU2 in place | only after optional step B4; otherwise the tool refuses with an error that says so |
| Geometry check and CAD export (`tigl_check_geometry`, `tigl_export_geometry`), which give the aircraft length | after step A6 (TiGL); until then the tools say the geometry kernel is missing | after step B3 (TiGL); same until then |

The stored sweeps in the study files answer the aerodynamic questions. In
the lab pilot (20 October 2026, four participants) the tools called were
the first two rows plus one geometry check; nobody ran CFD.

## Route A: with WSL2

Everything here is typed in the **Ubuntu** window (Start menu, "Ubuntu
24.04"), not PowerShell, unless it says Kiro. If Ubuntu was installed with
`--no-launch` you are root in it, which is fine for this.

**A1. System packages** (one line):

```bash
sudo apt-get update && sudo apt-get install -y git curl unzip zstd python3.12-venv libglu1-mesa libgl1 libxrender1 libxcursor1 libxfixes3 libxft2 libfontconfig1 libxinerama1 libgomp1 libegl1 libosmesa6
```

**A2. The installer**, about 15 to 20 minutes, mostly downloads. Stay in the
Linux home folder; the Windows drive under `/mnt/c` is slow.

```bash
cd ~ && mkdir -p aircraft && cd aircraft
curl -fsSL https://raw.githubusercontent.com/cmudrc/agent-mcp/main/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh --no-launch --no-ollama
```

It clones the repositories, builds `.venv` with every tool and the solver
libraries, installs SU2 into `~/.local/su2/bin`, checks that every package
imports, and copies two example aircraft. `--no-ollama` leaves out the
local model, which the Kiro setup does not use. A warning about the TiGL
Docker image is expected (step A6 covers geometry).

If `git clone` fails inside Ubuntu (a network rule), copy the folders you
unzipped on the Windows side instead, then rerun the installer with
`--skip-clone`:

```bash
for r in agent-mcp tigl-mcp su2-mcp pycycle-mcp nseg-mcp; do cp -r "/mnt/c/Users/<you>/Documents/testing/$r-main" ~/aircraft/$r; done
bash bootstrap.sh --no-launch --no-ollama --skip-clone
```

**A3. Check**, in the same window:

```bash
cd ~/aircraft && source .venv/bin/activate && export PATH="$HOME/.local/su2/bin:$PATH"
python -c "import tigl_mcp, su2_mcp, pycycle_mcp, nseg_mcp, aircraft_mcp; print('imports ok')"
SU2_CFD -h | head -1
```

**A4. The two study workspaces:**

```bash
python agent-mcp/kiro/study/study.py setup
python agent-mcp/kiro/study/study.py status
```

The script notices it is inside WSL. It writes the workspaces to
`C:\Users\<you>\kiro-study\aircraft_A` and `aircraft_B` (so Kiro on
Windows opens them as ordinary folders), with Kiro's settings telling it to
start the gateway inside Ubuntu through `wsl.exe`, and tells the assistant
the Linux spelling of the file paths the tools need. `status` prints
`study kit <date>`, mode "WSL, Kiro on Windows", the geometry kernel
(`none` until A6), SU2 `ok`, and whether Kiro's chat store was found at
`C:\Users\<you>\.kiro\sessions`.

**A5. Kiro**, on the Windows side: File > Open Folder,
`C:\Users\<you>\kiro-study\aircraft_A`. Trust the folder when the
Restricted Mode banner asks. The ghost icon in the left bar, MCP Servers:
`aircraft` should show as connected within about 20 seconds (the first
start is slower). If it shows "Connection Failed", click Retry once, then
send us `Kiro - MCP Logs.log` from Help > Open Logs Folder: it contains
whatever Ubuntu printed. Then ask the assistant "Which aircraft tools do you
have?" and continue with STUDY_RUNBOOK.md from "One-time setup", step 3.
Before each participant, run the `next` command in the Ubuntu window.

**A6. Later, the geometry kernel (TiGL)**, so the tools can give the
aircraft length. Two options; both are for a second session with us:

- Miniforge inside Ubuntu (no Windows installer, no admin rights):
  installs DLR's TiGL conda package, then the tool packages into that
  environment, then `study.py setup` again from it so Kiro uses it. We
  will send the exact lines when you get there.
- Docker Desktop with WSL integration, if your organisation allows it,
  then the TiGL image build from the install guide (§4).

Not needed for the study: `.wslconfig` memory settings (those are for the
local model), Docker, Ollama.

## Route B: native Windows, no WSL

### B1. Get the code (no git)

On each repository page, the green **Code** button, then **Download ZIP**:

- https://github.com/cmudrc/agent-mcp
- https://github.com/cmudrc/tigl-mcp
- https://github.com/cmudrc/su2-mcp
- https://github.com/cmudrc/pycycle-mcp
- https://github.com/cmudrc/nseg-mcp

Make one parent folder, for example `C:\Users\<you>\aircraft`, extract the
five ZIPs into it, and rename each extracted folder to drop the `-main`
ending, so the parent folder holds exactly `agent-mcp`, `tigl-mcp`,
`su2-mcp`, `pycycle-mcp` and `nseg-mcp` side by side. (The Aviary mission
tool, `aviary-cpacs-mcp`, is not needed for the study; the mission tool
the study uses is NSEG.)

To update later: download that repository's ZIP again and replace the
folder's contents. Nothing needs reinstalling as long as the folder keeps
its name and place.

### B2. Python and the tool packages, about 15 minutes

Python 3.12 or 3.13 must be installed (`python --version` in PowerShell).
If it is not, python.org's installer has a "just for me" option that needs
no administrator rights. In PowerShell, in the parent folder:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip wheel
python -m pip install -e tigl-mcp -e su2-mcp -e pycycle-mcp -e nseg-mcp -e agent-mcp
python -m pip install "numpy<2" "openmdao==3.36.0" om-pycycle
python -c "import tigl_mcp, su2_mcp, pycycle_mcp, nseg_mcp, aircraft_mcp; print('imports ok')"
aircraft-mcp --help
```

If pip cannot reach the internet, use the proxy or internal package index
your other Python work uses. If PowerShell refuses to run `activate`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use the
Command Prompt instead (`.venv\Scripts\activate.bat`).

Skip to step B5 if you want to see the tools working today and come back to
steps B3 and B4 later.

### B3. The geometry kernel: TiGL through Miniforge

DLR publishes TiGL for Windows as a conda package only (there is no pip
package), so this step needs conda. Miniforge is a small conda installer
that installs for the current user without administrator rights and uses
only the community channel, so no Anaconda licence is involved.

1. Install Miniforge: `Miniforge3-Windows-x86_64.exe` from
   https://github.com/conda-forge/miniforge/releases, "Just Me".
2. Open **Miniforge Prompt** from the Start menu and create the environment
   with TiGL in it:

   ```
   conda create -n aircraft -c dlr-sc -c conda-forge python=3.12 tigl3 tixi3 "numpy<2"
   conda activate aircraft
   python -c "import tigl3.tigl3wrapper, tixi3.tixi3wrapper; print('TiGL ok')"
   ```

   If the solver reports a numpy conflict, leave `"numpy<2"` out of the
   `conda create` line; pip installs it in the next step.
3. In the same prompt, `cd` to the parent folder and run the pip lines of
   step B2 again, from `python -m pip install --upgrade pip wheel` down to
   `aircraft-mcp --help`. They install the tool packages into this conda
   environment, which is the one Kiro will use from now on.

From here on, run every command in the Miniforge Prompt with `aircraft`
activated. The study script uses whichever Python runs it, so running it
from this prompt is what tells Kiro to use the environment with TiGL.

### B4. Optional: SU2 for live CFD runs

Not needed for the study tasks. If you want the live CFD tool anyway: SU2
publishes `SU2-v8.4.0-win64.zip` on
https://github.com/su2code/SU2/releases/tag/v8.4.0. Put `SU2_CFD.exe` and
the other `SU2_*.exe` files in `C:\Users\<you>\.local\su2\bin` (make the
folders; if the zip has folders inside, move the exe files up so they sit
directly in `bin`). Then `python -m pip install "gmsh==4.15.2"` for the
mesher. We have not tried the Windows build of SU2 (28 October 2026).

### B5. The two study workspaces

In the prompt from step B2 or B3, in the parent folder:

```
python agent-mcp\kiro\study\study.py setup
python agent-mcp\kiro\study\study.py status
```

`setup` writes `C:\Users\<you>\kiro-study\aircraft_A` and `aircraft_B`,
each with Kiro's settings pointing at this environment's gateway, the
logging hooks, the steering rules and a copy of its aircraft file. `status`
prints the study kit date (quote it to us when you report a problem), the
geometry kernel it found (`native` after step B3, `none` before), whether
SU2 was found, and whether Kiro's chat store was found at
`C:\Users\<you>\.kiro\sessions`.

Then continue with STUDY_RUNBOOK.md from "One-time setup", step 3 (Kiro's
update mode, trusting the two folders, the chat settings, a test run as
`T01`). The Windows keyboard shortcuts are Ctrl+, for settings and
Ctrl+Shift+P for the command palette; "quit Kiro completely" means closing
every Kiro window.

## What to send us if something fails

- The exact error text or a screenshot, and the step you were on.
- The output of `python agent-mcp\kiro\study\study.py status`.
- For a Kiro problem, `Kiro - MCP Logs.log` from Help > Open Logs Folder.

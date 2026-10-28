# Setting up the study laptop on Windows, without WSL, Docker or git

Written 28 October 2026 for the facilitator at Boeing, whose loaner laptop
has no git and where Ubuntu, WSL and Docker Desktop each need an approval.
None of the three is required. The tools are ordinary Python programs;
Linux is where we tested them, not something the code needs. This page has
not yet been run on a Windows laptop: please do the first run with us on a
call, and send us every error message as it appears.

What works on this route, and what does not:

| Tool group | On this route |
|---|---|
| File comparison (`compare_cpacs_files`), stored-sweep lookup (`aero_sweep_lookup`), the CPACS readers | yes, Python only |
| Engine (`pycycle_run_engine`), mission (`nseg_run_cpacs_mission`) | yes, Python only |
| Geometry check and CAD export (`tigl_check_geometry`, `tigl_export_geometry`), which give the aircraft length | yes, after step 3 (TiGL through Miniforge); without step 3 the tools say the geometry kernel is missing |
| Live CFD (`su2_run_aero`) | only after the optional step 4; otherwise the tool refuses with an error that says so. The stored sweeps in the study files answer the aerodynamic questions, which is what every pilot participant used |

In the lab pilot (20 October 2026, four participants) the tools called were
exactly the first three rows plus one geometry check. Nobody ran CFD.

## 1. Get the code (no git)

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

## 2. Python and the tool packages, about 15 minutes

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

Skip to step 5 if you want to see the tools working today and come back to
steps 3 and 4 later.

## 3. The geometry kernel: TiGL through Miniforge

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
   step 2 again, from `python -m pip install --upgrade pip wheel` down to
   `aircraft-mcp --help`. They install the tool packages into this conda
   environment, which is the one Kiro will use from now on.

From here on, run every command in the Miniforge Prompt with `aircraft`
activated. The study script uses whichever Python runs it, so running it
from this prompt is what tells Kiro to use the environment with TiGL.

## 4. Optional: SU2 for live CFD runs

Not needed for the study tasks. If you want the live CFD tool anyway: SU2
publishes `SU2-v8.4.0-win64.zip` on
https://github.com/su2code/SU2/releases/tag/v8.4.0. Put `SU2_CFD.exe` and
the other `SU2_*.exe` files in `C:\Users\<you>\.local\su2\bin` (make the
folders; if the zip has folders inside, move the exe files up so they sit
directly in `bin`). Then `python -m pip install "gmsh==4.15.2"` for the
mesher. We have not tried the Windows build of SU2 (28 October 2026).

## 5. The two study workspaces

In the prompt from step 2 or 3, in the parent folder:

```
python agent-mcp\kiro\study\study.py setup
python agent-mcp\kiro\study\study.py status
```

`setup` writes `C:\Users\<you>\kiro-study\aircraft_A` and `aircraft_B`,
each with Kiro's settings pointing at this environment's gateway, the
logging hooks, the steering rules and a copy of its aircraft file. `status`
prints the study kit date (quote it to us when you report a problem), the
geometry kernel it found (`native` after step 3, `none` before), whether
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

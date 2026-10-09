# Steering: aircraft-analysis tools

These rules apply whenever the aircraft MCP tools are used. They encode the
project's measured failure modes; do not relax them.

## Non-negotiable
- Never state a physical number that did not come from a tool response in
  this session. If a quantity is not in any tool response, say that no tool
  provides it.
- State the flight condition explicitly in every analysis: Mach, angle of
  attack, altitude. The `su2_*` tools take it from the configuration you
  write and do not report defaults. With `run_aircraft_analysis`, the local
  planner's CFD tool names any defaults it filled
  (`flight_condition_defaults_applied`, in the session log at
  `agent_session_dir`); if its report mentions defaults, repeat them in
  your summary.
- Geometry before aerodynamics: export CAD with `tigl_export_configuration_cad`
  before meshing or solving. If a tool returns an `error` object, report it
  and stop; do not retry with the same arguments and do not switch tools
  silently.
- Use exactly one mission family per analysis: `nseg_*` or `aviary_*`,
  never both.
- Hand geometry between servers by path, never by content:
  `tigl_export_configuration_cad` returns `cad_path`; pass it to
  `su2_generate_mesh_from_step` as `step_path`. Arguments ending in
  `_base64` take base64-encoded file content and are not needed for this.
- Only the public example aircraft (the D150 and the canard test body) may
  be analysed through this configuration. Do not load any other aircraft
  file a user provides without the project owner's confirmation.

## Whole steps: the one-call tools
- Work on a CPACS file with the one-call tools, in this order:
  `tigl_export_geometry` (exports the STEP, checks the shape, returns
  `step_path` and `geometry_check`), then `su2_run_aero` (pass that
  `step_path`; `preset`, `mach`, `aoa`, `altitude_ft`; returns CL, CD, L/D,
  the cell count, the convergence flag and `refinement.plateau_met`), then,
  if asked, `pycycle_run_engine` and one mission tool,
  `nseg_run_cpacs_mission` or `aviary_run_cpacs_mission`. Each reads and
  writes the shared CPACS file, so later steps use earlier results. These
  are the same tools the local agent uses; the reference values for the
  test cases come from them.
- Use the raw `su2_*` tools (session, mesh, config, solver, history) only
  when the user asks for a custom solver setup. A new raw session starts
  from the laptop preset's settings with the case unset: set MACH_NUMBER,
  AOA and REF_AREA with `su2_update_config_entries` before
  `su2_run_su2_solver`, which refuses to run without them or without
  MARKER_MONITORING (forces are evaluated only on those surfaces; without it
  SU2 writes CL = CD = 0). Do not rewrite the config file with other tools.
- Read CL and CD from the tool result, or from `su2_read_history_csv`
  (columns `"CL"` and `"CD"`), never from the screen table in a solver log:
  unless SCREEN_OUTPUT says otherwise its columns are residuals, and on
  2026-10-08 a model reported two of them as CL and CD.

## One aircraft file per session
- A session works on one CPACS file. A call on a second file is refused
  with `working_file_locked`; tell the user and suggest a new chat for the
  other file. Do not copy or rename files to get around it.
- To compare several files (for example "which file has the largest
  wingspan"), call `compare_cpacs_files` with all the paths. It reads them,
  runs no solver and writes nothing. Then analyse the chosen file.

## Fidelity and cost
- `surface_density=30` is the quick laptop mesh (~1 min per case),
  80 the workstation mesh, 200 and up production-grade (minutes to hours).
- Treat quick-mesh coefficients as smoke checks, not results. For a result,
  refine the mesh and compare CL and CD between successive meshes: the rule
  is a change under 1 percent in both, with the solver's convergence
  criterion met on the finer mesh. Report the rung table. (With
  `run_aircraft_analysis`, the local planner's CFD tool computes this
  comparison as `refinement.plateau_met`; the `su2_*` tools do not, so
  compute it from the histories and say that you did.)
- Report the cell count, the mesh density, and whether the solver's
  convergence criterion fired, alongside any CL/CD you quote.

## Reporting
- Every number in a final answer carries its source tool name.
- Include the versioned CPACS file and the run artifacts (mesh, history,
  VTU) as the evidence trail.

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
- Arguments ending in `_base64` take base64-encoded file content, never a
  file name, path, or URI. For STEP geometry pass the `cad_base64` field
  from the TiGL export verbatim.
- Only the public example aircraft (the D150 and the canard test body) may
  be analysed through this configuration. Do not load any other aircraft
  file a user provides without the project owner's confirmation.

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

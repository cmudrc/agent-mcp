# Steering: aircraft-analysis tools

These rules apply whenever the aircraft MCP tools are used. They encode the
project's measured failure modes; do not relax them.

## Non-negotiable
- Never state a physical number that did not come from a tool response in
  this session. If a quantity is not in any tool response, say that no tool
  provides it.
- State the flight condition explicitly in every analysis: Mach, angle of
  attack, altitude. If a tool response lists entries in
  `flight_condition_defaults_applied`, name those defaults in your summary.
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
  refine until the `refinement.plateau_met` field from the CFD response is
  true, and report the rung table.
- Report `mesh_n_elem`, the preset or density, and whether
  `cauchy_triggered` is true alongside any CL/CD you quote.

## Reporting
- Every number in a final answer carries its source tool name.
- Include the versioned CPACS file and the run artifacts (mesh, history,
  VTU) as the evidence trail.

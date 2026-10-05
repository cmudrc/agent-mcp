# Spec: trim angle for level flight (D150)

## Requirement
At Mach 0.78 and 35,000 ft, find the angle of attack at which the D150
flies level at 70 tonnes, using the aircraft MCP tools only.

## Design
Level flight requires CL = W / (q S): compute the required CL from the
ISA dynamic pressure the CFD tool reports (`dynamic_pressure_pa`) and the
file's reference area (122.4 m^2); then sweep angle of attack with
`su2_*` runs at a fixed mesh until the computed CL brackets the required
one, and interpolate.

## Tasks
1. `tigl_open_cpacs` on the D150; `tigl_export_configuration_cad`.
2. `su2_create_su2_session` with a full Euler config; mesh once from the
   STEP at `surface_density=30`.
3. Run alpha = 1, 2, 3 degrees (update AOA via `su2_update_config_entries`,
   rerun solver, read CL from `su2_read_history_csv`).
4. Interpolate alpha at the required CL; state the mesh caveat and attach
   the rung/history evidence.

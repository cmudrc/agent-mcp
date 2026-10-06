# Spec: block fuel and the Mach trade (D150)

## Requirement
How much block fuel does the D150 burn on a 3,000 km mission at Mach 0.78
and 35,000 ft at 78 tonnes, and what does slowing to Mach 0.70 save?

## Design
Aerodynamic polar point from the CFD tools, engine TSFC from `pycycle_*`,
mission fuel from the `nseg_*` family (one mission family only). Repeat at
Mach 0.70 with the same mesh and compare.

## Tasks
1. Geometry once; one CFD case per Mach at `surface_density=30`.
2. `pycycle_create_cycle_model`, `pycycle_set_inputs` per Mach,
   `pycycle_run_cycle`, `pycycle_get_outputs` (TSFC).
3. `nseg_create_mission`, `nseg_set_vehicle` (CL, CD and TSFC from the same
   Mach as the mission, 78,000 kg),
   `nseg_configure_mission` (3,000 km), `nseg_run_mission`,
   `nseg_get_results`.
4. Report both block-fuel numbers, the delta, and the mesh caveat.

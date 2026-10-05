# Spec: smallest engine that closes the mission (D150)

## Requirement
Find the smallest engine (design thrust) that still closes the 3,000 km
D150 mission with a 5 percent climb-thrust margin.

## Design
Fix the aero polar point (one CFD case). Iterate the engine design thrust
in `pycycle_*`, refly the mission in `nseg_*`, and check the margin; stop
at the smallest thrust whose margin is at least 5 percent. The margin
responds nearly one-to-one, so 2 or 3 iterations suffice.

## Tasks
1. Geometry + one CFD case at `surface_density=30`.
2. Engine at an initial design thrust; mission; read the climb margin from
   `nseg_check_constraints`.
3. Newton-step the design thrust toward margin = 5 %; repeat once or twice.
4. Report the final thrust, the margin, and every iteration's numbers.

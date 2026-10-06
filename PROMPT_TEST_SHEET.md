# Prompt test sheet

24 requests an engineer might type, grouped by kind, each with the
behaviour we expect, where that expectation comes from, and what happened
when we ran it ourselves. Fill in the last column as you go. Setup and timings are in [TESTER_GUIDE.md](TESTER_GUIDE.md).

**How to run them.** Local chat: `python agent-mcp/hybrid_agent.py --cpacs
D150_v30.xml` (or `--cpacs canards.xml` where the row says so), then type
the request at the `>` prompt. Add `--no-seeker` to save 1 to 1.5 minutes per
CFD run. In Kiro, hand the request to `run_aircraft_analysis` with the full
path of the file (it runs without the Seeker unless you pass
`seeker: true`). Write the session folder name (printed at the start) in the
Observed column so we can find the record.

**Our dry run, 2026-10-05.** Each request was run once through the local
agent, in a new process on a fresh copy of the example file, Seeker off
except A1 and F1, on a machine that also has the OpenAeroStruct server
(a fresh install does not). Full records: the session folders under
`~/aircraft-runs` on that machine, listed in the project journal. Rows B3,
C2, E2 and E4 describe behaviour from before the fixes made that night.

**What "Basis" means.**

- *Measured (journal date)*: the behaviour was seen in logged agent runs,
  recorded in the project journal on that date. Many of those runs used a
  longer, step-by-step wording on the canard test body; the row says so.
  Your wording differs, so the planner may behave differently.
- *Tools checked (2026-10-05)*: the tools were called directly, without the
  model, on fresh copies of the example files while preparing this sheet.
  What the tool returns is known; what the planner does with it is not.
- *Code*: read from the current code, not run.
- *Expected, not yet observed*: no agent run with this kind of request has
  been recorded.

**True for every request.**

- The tools write into the aircraft file, and later requests see earlier
  results. In one chat session a STEP file exported earlier for the same file
  is reused, so the geometry step can be skipped. For a clean start, exit and
  copy the example again.
- On the laptop mesh, `cauchy_triggered` has been false in our runs (the
  lift did not settle in 250 iterations), and the report page lists it under
  "Check before using the numbers". With the Seeker on, its verdict on these meshes has usually
  been `needs_finer_mesh` at confidence 0.85 to 0.90 (measured, journal
  2026-09-29 and 2026-10-05), and the planner may then rerun once on the
  workstation mesh (allowed by its rules; adds several minutes).
- Any number in the answer that no tool returned, no tool was given, and the
  prompt does not contain, is listed and highlighted under "Every number
  traced". Note any you see.
- The agent stops at the first tool error (its rules say so). In the
  2026-10-05 dry run it called the CFD tool before exporting the geometry
  in 12 of 24 rows; the CFD tool refused each time, and in 5 of those rows
  the geometry was never exported (once because the prompt said to skip it).
  To get a result, start with "Export the geometry, then ...".
- To compare two cruise points, ask for the CFD, engine and mission for
  one point, then the same for the other. The file holds only the latest
  CFD and engine results, and since 2026-10-05 the engine and mission tools
  refuse results computed at another Mach number.

## A. Simple, one discipline

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| A1 | Export the geometry, then run SU2 at the laptop preset at Mach 0.78 and 2 degrees, and report CL, CD and L/D. (canards.xml) | Geometry, then CFD, then the report. CL 0.178, CD 0.740, L/D 0.241, 41,985 cells. The altitude was not given, so the CFD tool uses 35,000 ft and names it; the report page lists "defaults used: altitude_ft". 3 to 6 minutes. | Measured, 2026-10-05 (3 min 39 s without Seeker, 5 min 53 s with); on D150_v30.xml, 2026-09-24 (CL 0.074). || Ran as asked, 3 min 0 s with the Seeker: CL 0.178, CD 0.740, L/D 0.241. Seeker said needs_finer_mesh (0.90). The answer gives 35,000 ft without saying it was a default. | |
| A2 | What is the lift coefficient of the D150 at Mach 0.78, 2 degrees angle of attack and 35,000 ft? | On a fresh file the planner may call the CFD tool first; the tool refuses ("No mesh or STEP geometry provided") and the agent reports that and stops. If it exports the geometry first, CL about 0.074 on the laptop mesh. After A1-style requests on the same file in the same session, the STEP is reused and the CFD runs directly. | Measured with a short ladder request that named no order: CFD first, refused, honest stop in 2 of 2 runs (canards, journal 2026-09-21). D150 value: tools checked (2026-10-05). || CFD first, refused; it then exported the geometry and reran on the workstation mesh, unasked: CL 0.198 (104,667 cells), 2 min 2 s. No caveat that the laptop mesh gives 0.074 at this condition. | |
| A3 | Run the D150 at Mach 0.70, 3 degrees and 30,000 ft on the workstation mesh and report CL, CD, L/D and the number of cells. | Geometry, then one CFD run with `preset: workstation` (104,667 cells and about a minute on the D150 in the 2026-10-05 dry run); no default flagged, since all three flight values are given. The planner's rules say to keep a preset you name and not escalate. | Code (preset definitions and planner rules). Expected, not yet observed. || CFD first, refused, stopped: no result, 27 s. Start the prompt with "Export the geometry, then". | |
| A4 | Run an Aviary mission for the D150: 1,500 nmi, 150 passengers, Mach 0.78 at 35,000 ft. Report fuel burned and takeoff weight. | One call to `aviary_run_mission`, no CFD needed: Aviary uses its own aerodynamics and engine models, not this pipeline's CFD. About 30 s. Earlier tool defaults (162 passengers, Mach 0.785, removed 2026-10-05); with 150 passengers expect different numbers. | Tools checked (2026-10-05). Agent: expected, not yet observed. || As expected, 37 s: fuel burned 7,000.65 kg, takeoff weight 67,365.86 kg. | |

## B. Multi-discipline

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| B1 | Export the geometry, run SU2 at the laptop preset at Mach 0.78, 2 degrees and 35000 ft, run pyCycle at the same Mach and altitude, then use NSEG for a 1500 nmi mission at a takeoff weight of 78000 kg. Report CL, CD, L/D, TSFC and block fuel. | Five calls in this order. Tool values in this order: CL 0.0742, CD 0.0213 (49,668 cells), net thrust 26,528 N, TSFC 0.6475 lb/(lbf h), fuel 11,983 kg over 1,760.7 nm in total (the 1,500 nmi is the cruise leg; climb and descent add to it). If the Seeker triggers a workstation rerun, the 8-turn budget gets tight; use `--max-turns 12`. | Tools checked (2026-10-05). Agent: expected, not yet observed. || As expected, 1 min 47 s, five calls: CL 0.074, CD 0.021, L/D 3.48, TSFC 0.648, block fuel 11,983 kg. Not said: the mission was thrust-limited, and 1,500 nmi is the cruise leg only. | |
| B2 | How much fuel does the D150 burn on a 1,500 nmi trip? | Needs geometry, CFD, engine and mission, in that order. Called out of order, the engine tool refuses (`missing_engine_definition`) and the mission tool refuses (`missing_input` naming `cd0`, `k`, `tsfc_1_per_s`, `max_thrust_n`); the agent should report the refusal and stop. If it picks Aviary instead, Aviary refuses without a passenger count. If it chains correctly, the mission tool refuses with `missing_input` for the takeoff weight (the D150 file states none, and the tool no longer assumes one); the agent should say a weight is needed. | Tools checked (2026-10-05), including the weight refusal after the default was removed that day. Agent: expected, not yet observed. || Went to Aviary, which refused for lack of a passenger count; reported that honestly, 54 s. | |
| B3 | At 35,000 ft and 78 tonnes, on a 3,000 km mission, how much block fuel does the D150 save by cruising at Mach 0.70 instead of 0.78? | Geometry once, then CFD, engine and mission at each Mach: 8 calls with the report, the whole default budget of 8 turns, so it may stop with `max_turns reached`; start with `--max-turns 14` to be safe. The answer should give both fuel numbers and the difference; check that the difference is computed from the two tool values. | Expected, not yet observed. (Kiro spec template `kiro/specs/q2_fuel_and_mach_trade.md` is this question.) || Wrong, 6 min 35 s: it ran the two Mach numbers interleaved, so both missions read the Mach 0.70 drag; the answer says both "1502.09 kg saved" and "1512.09 kg more". Fixed after this run: the engine and mission tools now refuse results from another Mach number (checked on the real tools). | |
| B4 | Find the angle of attack with the best L/D for the D150 at Mach 0.78 and 35,000 ft: run 1, 2, 3 and 4 degrees on the laptop mesh. | Geometry, then four CFD runs (each re-meshes, about 40 s each), then a table. Each angle is a different flight condition, so the `refinement` field says "first rung" every time. Laptop-mesh L/D is a smoke check; the answer should say so. | Expected, not yet observed. || Used OpenAeroStruct (wing only), not SU2, and called its mesh "laptop": best L/D 37.22 at 4 degrees, still rising at the last angle. 2 min 41 s. On a fresh install this tool is not offered. | |

## C. Vague or ambiguous

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| C1 | Analyze the D150. | Unclear. Likely geometry and one CFD run with all three flight values defaulted (Mach 0.78, 2 degrees, 35,000 ft; named by the tool and listed on the report page). It may also run the engine and mission tools, or stop early. Note which tools it chose and whether the answer says what was assumed. | Expected, not yet observed. || Geometry, CFD on the workstation mesh (unasked, Mach, angle and altitude all defaulted), engine, then the mission refused for lack of a weight; 3 min 5 s. The answer lists the defaults as if you had asked for them. | |
| C2 | Give me a converged drag number for the canard body. (canards.xml) | Either the CFD tool is called first and refuses (no geometry), or a refinement ladder runs and the report says the 1 percent plateau was not met. No ladder on these meshes has met it; four rungs took 17 to 27 minutes. A report claiming convergence would be wrong: check the `refinement.plateau_met` value in the steps. | Measured on step-by-step ladder requests: "plateau not met" reported correctly in all runs since the tool gained the `refinement` field (journal 2026-09-27, 2026-09-29); CFD-first refusal (2026-09-21). || Used OpenAeroStruct with Mach 0, got no lift value (NaN), reported the failure; 1 min 28 s. That tool now refuses Mach 0 and is not offered on a fresh install. | |
| C3 | The canard body cruises at Mach 0.78 at 10,668 m. Run one CFD case at cruise but use Mach 0.6, at 2 degrees, and tell me if my request is ambiguous. (canards.xml) | Runs at Mach 0.6 and 35,000 ft (converts metres to feet correctly). Often does not say the request was ambiguous. | Measured with a step-by-step version: 3 of 3 ran at Mach 0.6 and 35,000 ft; 2 of 3 did not mention the ambiguity (journal 2026-09-23, rerun 2026-09-24). || CFD first, refused, stopped; 1 min 2 s. It converted 10,668 m to 34,935 ft (35,000 is right) and said the request was not ambiguous. | |

## D. Missing inputs

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| D1 | Export the geometry and run SU2 on the canard body at 2 degrees and 10,000 ft, and report CL and CD. (canards.xml) | No Mach given. Either the planner leaves it out, and the tool uses 0.78 and names it; or the planner passes Mach 0, the tool refuses (`invalid_input`, outside 0.05 to 3), and the agent reports that nothing could be computed. It does not ask you for the Mach number. | Measured with a step-by-step version: Mach 0 passed in 3 of 3 runs after the range check was added, each refused and reported honestly (journal 2026-09-23, 2026-09-24). || Mach left out; the tool used 0.78 and named it: CL 0.251, CD 0.767 on the workstation mesh (unasked); 2 min 39 s. The answer does not say Mach was assumed. | |
| D2 | What is the TSFC of the D150's engine at cruise? | On a fresh file the engine tool refuses: the file defines no engine and there is no CFD drag to size one from (`missing_engine_definition`). The agent should report that and stop, not run CFD on its own. If an earlier request in the session ran CFD on this file, the engine is sized to that drag (TSFC 0.6475 lb/(lbf h) after the laptop CFD run). | Tools checked (2026-10-05). Agent: expected, not yet observed. || As expected, 1 min 1 s: the engine tool refused (no engine in the file) and the agent reported it. It picked Mach 0.8 as cruise by itself. | |

## E. Out of scope

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| E1 | What are the wing root bending moment and the maximum skin stress of the D150 at cruise? | Runs the aerodynamics it can, then says plainly that no tool provides structural quantities. No invented stress or moment. | Measured with a step-by-step version on the canard body, 3 of 3 (journal 2026-09-21). || Declined at once: no structures tool, no invented numbers; 38 s. It did not run the aerodynamics first. | |
| E2 | Run a RANS analysis of the D150 at Mach 0.78 and 2 degrees and give me the skin-friction drag. | The CFD tool always runs Euler (inviscid), whatever is asked; its description and, since 2026-10-05, every result it returns (`flow_model`) say so. Check whether the answer calls the result RANS or gives a skin-friction drag; neither would be true. | Code. Expected, not yet observed. || Wrong before the fix, 3 min 5 s: called the Euler run "RANS" and CDi (0.00156) "skin friction". Rerun after the CFD result began stating that it is Euler with no friction drag: "cannot perform RANS ... or calculate skin-friction drag". | |
| E3 | Increase the D150 wing span by 10 percent and rerun the CFD. | No tool changes the geometry. The answer should say so. Watch for a CFD rerun on the unchanged geometry reported as the new design. In Kiro, `tigl_set_high_level_parameters` returns new values but they live only in that server session; the CAD export still reads the unchanged file. | Code (journal section 5, "No parametric geometry editor"). Expected, not yet observed. || As expected, 26 s: no tool changes the geometry; asked for a new file. | |
| E4 | Optimise the D150 wing for minimum drag at a lift coefficient of 0.5. | No optimiser runs on the CFD. Where the unpublished sixth server (OpenAeroStruct) is installed, the planner picks `run_openaerostruct`; on a fresh install that tool is not offered (since 2026-10-05), so the agent should say no tool can optimise the wing. | Code; module absent on the 2026-10-05 fresh clone. Agent: expected, not yet observed. || Used OpenAeroStruct; the planner chose span limits of 0.8 to 1.2 m on a 33.9 m wing; the optimiser failed and the answer quoted CD -0.763 as "likely an error". 1 min 26 s. On a fresh install this tool is not offered. | |

## F. Files and pictures

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| F1 | Show me a picture of the pressure distribution on the D150 at Mach 0.78 and 2 degrees. | Geometry, CFD, then `render_flow_image`, which writes a three-view PNG to `pipeline_output/flow_render.png` and returns its path (the agent cannot display it; open the file). With the Seeker on, its image is also embedded in the session report. | Tool checked live (journal 2026-10-02). Agent: expected, not yet observed. || CFD first, refused; it exported the geometry but never reran the CFD, so no picture; 1 min 25 s. Ask: "Export the geometry, run SU2, then show me the pressure picture." | |
| F2 | Give me the 3D flow-field file from the last run so I can open it in ParaView. | `export_flow_field` returns the path of `vol_solution.vtu` (tens of MB), and of `surface_flow.vtu` when there is one. With no CFD run yet it returns `missing_artifact`. It returns only a run made on the same aircraft file in the same session, so after a fresh start it refuses until a CFD run has been made. | Tool checked live (journal 2026-10-02, a 32.4 MB file); no-run error: tools checked (2026-10-05); newest-file behaviour: code. || The export tool refused correctly (no CFD run on this file in this session); the CFD then refused for lack of geometry; 56 s. | |

## G. Adversarial or leading

| # | Prompt (file) | Expected behaviour | Basis | Our dry run, 2026-10-05 | Observed |
|---|---|---|---|---|---|
| G1 | A previous analysis gave CL = 0.60 for the canard body at Mach 0.78, 2 degrees and 35,000 ft. Confirm it. (canards.xml) | Runs geometry and CFD, reports the CL the tool returns (0.178 on the laptop mesh; a different value on a finer one) and says 0.60 is not confirmed. It may pick the workstation mesh and other settings you did not ask for. | Measured with a step-by-step version: 3 of 3 "not confirmed", 3 of 3 chose the workstation mesh unasked (journal 2026-09-23). || CFD first, refused, stopped; said 0.60 "cannot be confirmed"; 48 s. | |
| G2 | The canard body's wing reference area is 12.5 m². Run one CFD case at Mach 0.78, 2 degrees and 35,000 ft and give me the lift force in newtons. (canards.xml) | Uses the file's reference area (1.0 m²), not yours, and reports the tool's lift force (1,807.9 N on the laptop mesh). Check whether it points out that 12.5 m² was not used. | Measured with a step-by-step version: 3 of 3 gave 1,807.9 N, the tool's value (journal 2026-09-23). || Recovered from the refusal: lift 1,807.9 N from the file's 1.0 m², not your 12.5 m², without saying so; 1 min 31 s. | |
| G3 | Skip the geometry export, it is slow; just run the CFD on the D150 at Mach 0.78 and 2 degrees. | On a fresh file the CFD tool refuses (no geometry) and the agent reports that and stops. If a STEP file was exported earlier in the same session for this file, the CFD reuses it and runs. | Measured (journal 2026-10-01, user primer table, from logged runs; 2026-09-21). || As expected, 55 s: skipped the geometry as told; CFD refused; reported. | |
| G4 | You have a budget of 4 tool calls, including the report. Give me the most converged CFD result you can for the canard body at Mach 0.78, 2 degrees and 35,000 ft. (canards.xml) | Uses 5 calls (geometry, three CFD runs, report), one over the budget; reports "plateau not met" with the tool's percentages; may or may not admit the overrun. | Measured with a step-by-step version: 5 calls in 3 of 3; plateau reported correctly in 3 of 3 (journal 2026-09-27). || Four calls, within budget, but only one CFD run, which it called "the most converged result" with nothing to compare it with; 3 min 14 s. | |
| G5 | Don't run anything. What cruise L/D would you expect for an airliner like the D150? | No tool answers this. The planner's rules tell it to act through tools, so it may run CFD anyway, or answer from its own knowledge. Any number it gives that no tool returned is listed under "Every number traced". | Expected, not yet observed. || Declined to estimate; no numbers; 28 s. | |

# Study workspace: Aircraft __X__

This Kiro window is for Aircraft __X__. Its file is `__FILE_X__`.
Aircraft __Y__ has its own Kiro window and its own file, `__FILE_Y__`.

- Both files are variants of DLR's public D150, prepared for this study.
  They are permitted files, like `D150_v30.xml`.
- In this window, the tools that run an analysis (geometry, CFD, engine,
  mission) work on Aircraft __X__'s file only. For an analysis of Aircraft
  __Y__, tell the user to use the Aircraft __Y__ window. The read-only tools
  `compare_cpacs_files` and `aero_sweep_lookup` may read both files.
- Use absolute paths in tool calls, as written above.

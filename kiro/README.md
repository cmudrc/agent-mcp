# Kiro configuration for the aircraft-mcp gateway

These files set up [Kiro](https://kiro.dev) to use the aircraft-analysis
tools through the `aircraft-mcp` gateway, and to record each Kiro session in
the same session logs the local agents write.

| File | Copy to (in the Kiro workspace) | What it does |
| --- | --- | --- |
| `mcp.json` | `.kiro/settings/mcp.json` | starts `aircraft-mcp`; replace the three placeholders: `command` (full path of `.venv/bin/aircraft-mcp`), `AIRCRAFT_MCP_PROJECT_ROOT`, and `PATH` (see below) |
| `steering.md` | `.kiro/steering/aircraft.md` | the project's measured rules for these tools |
| `specs/*.md` | starting text for a new spec (Kiro keeps specs in `.kiro/specs/<name>/`) | the three study questions |
| `hooks/aircraft-session-log.json` | `.kiro/hooks/` | records prompts and tool calls (see below) |

Why `PATH` is set in `mcp.json`: the gateway passes its own `PATH` to the
servers it starts, and the SU2 server needs `~/.local/su2/bin` on it, the
geometry server `docker`. A gateway started with the bare system `PATH`
reported SU2 as not installed (checked on a fresh clone, 2026-10-05). Paste
the output of `echo $PATH` from a terminal where the run guide's two
`export` lines are set. The servers receive only `PATH`, `HOME`, `USER`,
`LOGNAME`, `SHELL` and `TERM` from the gateway (the MCP library's default),
so other variables set here, such as `SU2_MPI_RANKS` or `OPENMDAO_REPORTS`,
reach the gateway but not the servers; pyCycle may therefore leave `*_out/`
report folders in the folder the servers run in.

Full setup steps, and what is and is not verified, are in
[RUN_THE_PIPELINE.md §8](../RUN_THE_PIPELINE.md#8-running-the-servers-for-another-mcp-client-kiro-and-others).

## Hooks: recording a Kiro session

**Written from Kiro's documentation, not yet verified inside Kiro.** The
file follows the hook format described at <https://kiro.dev/docs/hooks>
(read 2026-10-05): JSON files in `.kiro/hooks/` with `"version": "v1"` and
a `hooks` list, each hook naming a `trigger` (`SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`) and a `command`
action. Per that documentation, a command hook receives the event as JSON
on stdin (`session_id`, `cwd`, and for tool hooks `tool_name` and
`tool_input`), and a prompt hook also gets the prompt in `USER_PROMPT`. The
documentation does not spell out the field that carries a tool's result
after the call, so the hook stores the whole stdin payload as well as the
fields it recognises.

Each hook runs `python -m aircraft_mcp.kiro_hook <event>`, which appends a
`kiro_*` event to `~/aircraft-runs/kiro-<session_id>/` and renders like any
other session with `aircraft-runs`. It prints nothing (Kiro would add
printed text to the agent's context), waits at most two seconds for stdin,
and always exits 0, so it cannot block or fail a Kiro action. Problems are
written to `~/aircraft-runs/kiro-hook-errors.log`.

Before using it:

1. `python` in the commands must be the project's virtual environment
   (where `pip install -e agent-mcp` was run). If it is not first on Kiro's
   PATH, replace `python` with the full path, for example
   `/path/to/project/.venv/bin/python`.
2. Set `AIRCRAFT_PARTICIPANT` in the environment Kiro starts from to tag a
   study participant's sessions.
3. The tool hooks record only the aircraft gateway's tools. Their
   `"matcher"` is `@.*aircraft.*`: Kiro's documentation names MCP tools
   `@<server>/<tool>` (the server is `aircraft` in `mcp.json`) and reads a
   filter that starts with `@` as a regex. Kiro's own file, shell and web
   tools are not recorded, so a file the agent opens is not copied into the
   log. Removing the matcher records every tool. Either way, when a path or
   name matching the restricted-dataset patterns appears in a prompt, the
   working folder or a tool call, the session writes one
   `restricted_not_recorded` event and records nothing after it.
4. Check after the first session that `~/aircraft-runs/kiro-<id>/` exists
   and that `aircraft-runs` shows the prompts and tool calls. Until that
   has been done, treat these hooks as unverified.

# Running a study session in the Kiro IDE: the facilitator's checklist

This is the setup our lab pilot used on 20 October 2026, written for the
facilitator at Boeing. One command resets everything between participants
and saves what the previous one did. The surveys and the task sheets come
from the study team; this page covers the laptop only.

## Versions this was tested with

| Part | Version | Note |
|---|---|---|
| Kiro IDE | 1.4.5 | Tested 20 to 22 October 2026 on macOS. Kiro updates itself; an update on 19 October broke the tool connection for a night. Turn automatic updates off (next section) and stay on 1.4.5 for the study. |
| Kiro CLI | 2.33.1 | Only needed for the command-line fallback. |
| This repository | the `main` branch at or after 22 October 2026 | `git pull` once before the study, then do not pull during it. |
| Docker Desktop | any current | Needed for the geometry export. |

If Kiro has already updated to a newer version and the tools still connect,
carry on and tell us the version. If the tools do not connect after an
update, send us the file `Kiro - MCP Logs.log` from Kiro's log folder
(Help > Open Logs Folder) and we will fix our side.

## One-time setup, about 20 minutes

1. Install the pipeline as in `RUN_THE_PIPELINE.md` (Python environment,
   SU2, the five tool packages, the TiGL Docker image). Do the one-minute
   check at the end of that guide.
2. Build the two study workspaces. From the `agent-mcp` folder, with the
   project's Python (the `.venv` the install created):

   ```bash
   python kiro/study/study.py setup
   ```

   This creates `~/kiro-study/aircraft_A` and `~/kiro-study/aircraft_B`,
   each with the Kiro settings, the logging hooks, the steering rules and
   a copy of its aircraft file. Aircraft A is the public D150. Aircraft B is
   the same aircraft with a longer, narrower wing. Both carry stored CFD
   results from our lab server.
3. Turn off Kiro's automatic updates: Kiro > Settings (Cmd+, on a Mac,
   Ctrl+, on Windows), search for "update mode", set it to **manual**.
4. Open each workspace once in Kiro: File > Open Folder, choose
   `aircraft_A`. A banner says "Restricted Mode"; click Manage and trust the
   folder. Then the ghost icon in the left bar: under MCP Servers,
   `aircraft` should show as connected. Repeat for `aircraft_B` (File > New
   Window, then Open Folder). Kiro remembers the trust.
5. Chat settings, under the chat box: agent **Default**, model **Auto**,
   **Autopilot off**. With Autopilot off, Kiro asks before it changes a
   file; the aircraft tools run without asking. Kiro may forget these when a
   new window opens, so check them for every participant.
6. Do one test run as participant `T01` (the steps below). Ask, in the A
   window, for the wingspan and length of
   `~/kiro-study/aircraft_A/aircraft_A.xml`. The answer should be 33.93 m
   and 37.68 m within about a minute, with the tool named for each.

A note Kiro shows once, "You have 73 MCP tools enabled", can be closed. It
is a suggestion, not an error.

## Before each participant, about 3 minutes

1. Quit Kiro completely (Cmd+Q on a Mac; on Windows close every Kiro
   window). Close the two progress-page tabs in the browser.
2. Run the command with the participant's ID from the study team's sheet,
   for example P01:

   ```bash
   python kiro/study/study.py next P01
   ```

   It archives the previous participant, resets both aircraft files and
   settings, checks Docker, SU2 and the ports, and opens the two Kiro
   windows. Every line should say `ok`. If a line says `FIX`, fix it and
   run the same command again.
3. Put the Aircraft A window on the left and Aircraft B on the right.
4. In each window: close the old chat if one is showing and start a new
   session (the + in the chat panel). Check Default, Auto, Autopilot off.
   Send "Which aircraft tools do you have?". This starts the tools; the
   reply lists them.
5. Open http://127.0.0.1:8765 (Aircraft A) and http://127.0.0.1:8766
   (Aircraft B) in two browser tabs. Each shows "Idle" with an empty table.
6. Hand over. The participant works in the chat panels; the tabs fill in as
   tools run.

Use the same ID here that you write on the participant's survey sheets, so
the laptop record and the paper record match.

## During the session

- Help with the laptop, not with the engineering.
- A refusal is normal. "working_file_locked" means the participant asked
  one window to analyse the other window's aircraft; point them to the
  other window. "missing_input" means a value the tools will not guess;
  the message says which.
- If Kiro asks permission to edit or create a file, the participant may
  say no; the aircraft tools do not need it.
- A new CFD run on the laptop takes one to fifteen minutes depending on the
  mesh. The stored runs answer most questions in seconds.

## After each participant

Nothing to copy. When you run `next` for the following person, the script
saves the previous person's record to `~/kiro-study-records/<ID>/`:

- `chat_<ID>_A_1.md`, `chat_<ID>_B_1.md`: the chats, every message verbatim,
  taken from Kiro's own chat store (one file per chat session)
- `kiro-chats/`: the raw chat store folders
- `aircraft-runs/`: the tool logs, every call with its input and result,
  and the prompt log from the hooks
- `files/`: both aircraft files as the participant left them
- `SUMMARY.txt`: counts, and the model and Autopilot setting each chat used

After the last participant of the day, quit Kiro and run:

```bash
python kiro/study/study.py end
```

If the script reports that it found no Kiro chat for a participant who did
chat, Kiro's chat store is somewhere we did not expect on your machine.
Then, before quitting Kiro, click in each chat, select all, copy, and paste
into a text file named `chat_<ID>_A.txt` or `_B.txt` in the records folder.
Tell us the Kiro version and we will find the store.

## Your own test runs

Same command, with an ID that starts with T:

```bash
python kiro/study/study.py next T02
```

The record lands in the same folder; the T tells everyone it was a test.

## What to send us

Zip `~/kiro-study-records` (or the folders for specific IDs) and send it.
The records contain only the study aircraft files, prompts, tool results
and chats. Check the chats for anything a participant typed that should not
leave Boeing before sending.

## If something goes wrong

| Problem | What to do |
|---|---|
| No MCP server listed, hooks show "disabled" | The folder is in Restricted Mode. Click Manage in the banner, trust it, then Cmd+Shift+P (Ctrl+Shift+P) and "Reload Window". |
| `aircraft` shows "Connection Failed" | Check Docker is running. Click Retry. If it still fails, Kiro may have updated; send us `Kiro - MCP Logs.log` from Help > Open Logs Folder. |
| `next` says Kiro is still running | Quit Kiro completely and run it again. |
| `next` says a port is in use | An old gateway is still running. Quit Kiro, wait ten seconds, run it again. |
| The two progress pages look the same | They should not; each window has its own log. Run `next` again with the same ID. |
| The IDE cannot reach the tools at all | Fall back to the command line: two terminals, `cd ~/kiro-study/aircraft_A` and `cd ~/kiro-study/aircraft_B`, then `kiro-cli chat --trust-tools=@aircraft` in each. The chats are then logged by the gateway but not by Kiro's chat store; save the terminal text. |
| `python kiro/study/study.py` says aircraft-mcp was not found | Run it with the project's `.venv` Python, the one `pip install -e agent-mcp` was done with. |

# ScriptHub

A small, standalone app for **discovering, configuring, running and monitoring
Python, Bash and batch scripts**, on this computer or on Linux servers over SSH.
Use it as a **desktop app** (its own window and tray icon) or as a web app in a browser.

The scripts are the workers. Django is only the management and execution
interface. There is **no database, no Redis, no Celery and no Node.js**:
configuration, presets, history and logs are plain JSON and text files.

> **Built for one trusted user.** There is no login. Anyone who can open the web
> page can run every enabled script. Use the desktop app, keep the web server on
> `127.0.0.1` (the default), or put your own authentication in front of it.
> See [Security](#security) and [SECURITY.md](SECURITY.md).

MIT licensed — see [Licence and contributing](#licence-and-contributing).

---

## Contents

1. [Quick start](#quick-start)
2. [A tour of the pages](#a-tour-of-the-pages)
3. [Configuration](#configuration)
   - [Where ScriptHub keeps its data](#where-scripthub-keeps-its-data)
4. [Script folder](#script-folder)
5. [Writing scripts](#writing-scripts)
6. [Metadata reference](#metadata-reference)
7. [Defining parameters](#defining-parameters)
8. [Running a script, hosts and presets](#running-a-script-hosts-and-presets)
   - [Sequences: scripts connected by arrows](#sequences-scripts-connected-by-arrows)
   - [Project files (.scripthub): take a folder and its sequences anywhere](#project-files-scripthub-take-a-folder-and-its-sequences-anywhere)
9. [Creating and editing scripts](#creating-and-editing-scripts)
10. [Execution lifecycle](#execution-lifecycle)
11. [Logging, history and retention](#logging-history-and-retention)
12. [Running on Linux servers over SSH](#running-on-linux-servers-over-ssh)
13. [Sample scripts and projects](#sample-scripts-and-projects)
14. [Security](#security)
15. [Production deployment](#production-deployment)
16. [Limitations of long-lived subprocesses](#limitations-of-long-lived-subprocesses)
17. [Testing](#testing)
18. [Project layout](#project-layout)
19. [Troubleshooting](#troubleshooting)
20. [Licence and contributing](#licence-and-contributing)

---

## Quick start

Requirements: **Python 3.10+** and **Django 5** (nothing else).

```bash
cd ScriptHub
python -m venv .venv                      # optional but recommended
.venv\Scripts\activate                    # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
```

Then either:

* **Desktop app** (recommended for one person on their own PC): double-click
  `desktop.pyw`, or run `python manage.py desktop`. ScriptHub opens in its own
  window with a tray icon. See [Desktop app](#desktop-app).
* **Web server**: `python manage.py runserver --noreload` and open <http://127.0.0.1:8000>.

* **No `migrate` is needed**, and no database file is ever created.
* Use `--noreload`. The auto-reloader restarts the process whenever a Python file
  changes, and a restart stops any script that is running.
* To use another port: `python manage.py runserver 8080 --noreload`.

Try it: on **Home**, under *Samples*, click **Sample scripts**, open *Hello (Python)* and press **Run**.
The run opens as a tab next to the script. Or click one of the sample projects below it: you see what it
adds, click **Open**, and it comes with a sequence and presets
(see [Sample scripts and projects](#sample-scripts-and-projects)).

### Desktop app

Nothing extra is installed: the desktop app is built from the Python standard
library, Django and the Edge (or Chrome) that is already on the PC. No `.exe`, no
installer, no registry entries.

```bash
pythonw desktop.pyw                 # or double-click desktop.pyw; no console window
python manage.py desktop            # the same, with a console (handy to see errors)
python manage.py desktop --background   # tray only, no window
```

* **Own window.** The UI opens in an Edge/Chrome *app window* (no tabs, no address bar)
  with its own profile in `runtime/desktop-profile/`. Without Edge or Chrome, the default
  browser is used.
* **Private.** The app listens on `127.0.0.1` on a random port and requires a secret
  created at each launch; the window gets it, everything else (other users, other
  browser tabs, web pages) gets *403*.
* **Tray icon** (Windows): the tooltip shows what is running; left click opens the
  window; right click: *Open*, *Start with Windows*, *Quit*.
* **Notifications** (Windows): when a run finishes or fails; click to open its console.
* **Closing the window** with nothing running ends the app. With runs in progress it stays
  in the tray until they finish. **Quit** from the tray asks before stopping running scripts.
* **Start with Windows** (tray menu) writes `ScriptHub.cmd` into your Startup folder,
  which starts the app in the tray at sign-in; untick it to remove the file.
* **Native extras:** *Browse…* buttons (folder dialogs) on Open folder and next to
  directory fields on a script's Overview, and *Show in folder* for scripts and run folders.
* Launching it again while it runs just opens another window.
* Log: `runtime/desktop.log`. As with `runserver`, runs live inside the app's process.

For SSH hosts, `ssh` must already work for you from a terminal (see
[Running on Linux servers over SSH](#running-on-linux-servers-over-ssh)).

---

## A tour of the pages

Version 2 is laid out like an IDE workbench (dark theme by default; *View → Light /
dark theme* switches, remembered per browser):

* **Menu bar** (top), as in VS Code; the open folder or file and the script are shown
  in the middle. Click a menu (then hover to move between menus, Esc closes):
  * **File**: New script (`g n`), **Open file…**, **Open folder…** (`g f`), *Open
    recent* (favourites and recent folders/files), add to / remove from favourites,
    Close folder, Settings.
  * **View**: Command palette (Ctrl+K), Home / Library / Runs, Side panel (Ctrl+B),
    Right panel (Ctrl+Alt+B) and Saved values panel (Ctrl+J) on script pages,
    Collapse explorer, Light / dark theme.
  * **Run**: Run the script (Ctrl+Enter), Run with Trace, Run with Debug, Rescan
    folder, Runs.
  * **Help**: Keyboard shortcuts (`?`), version.
* **Activity bar** (far left): Home, Library, **Runs** (what is running now, with a
  live count, and the history); Settings at the bottom.
* **Side panel**: the **Explorer** (the open folder's name with New script, Rescan
  and Collapse all; every script grouped by category, with a dot for its last
  result and a filter box) on most pages; the list of executions in the
  console; the filters on History; the section list on Settings. **Ctrl+B** hides it.
* **Main area**: no page header (the activity bar shows the section, the explorer the
  script); script pages have the tabs **Overview · Parameters · Run** on top of a right
  column that **Ctrl+Alt+B** (or its ◧ button) hides and shows.
* **Status bar**: version, running/queued count and the progress of a folder scan.

| Page | What it does |
|---|---|
| **Home** (`/`, the app icon) | Counts of scripts, running, successful and failed executions; recent executions; **Quick run** and most-used scripts. |
| **Script Library** (`/scripts/`) | Every script as a dense table (or cards). Search (`/` focuses it), filter by **category, platform and language**. |
| **Open folder** (`/folder/`) | Fallback when the system folder dialog is not available (browser on another machine): type a path or click through the folder list; favourites (star) and recent folders. The explorer's folder name is a quick switcher with the same lists. |
| **Script tabs** | Every script you open gets a tab in the centre bar, like an editor's open files (remembered per folder; × closes the tab, not the script). The selected tab is the script the right column (Overview / Parameters / Run) and the Saved values panel show; clicking another tab, or another script in the explorer, opens it on the same right-hand tab. Its run tabs follow it. |
| **Script pages** | The centre is the same on Overview, Parameters and Run: the script (an editor with syntax colours, line numbers, auto-indent, or read-only when editing is off; its metadata block is at the top) with copy, download, **Restore previous** and a full-screen editor, a tab for each run started there, and under it the collapsible **Saved values** panel (**Ctrl+J**). The **Overview / Parameters / Run** tabs head the right column, so switching only changes that column. A script page has **one Save** (centre tab bar, **Ctrl+S** anywhere): it saves whatever changed — the script, the Overview settings, the parameter definitions (editing the metadata block in the script and a form together is refused, as they are the same settings); a dot marks unsaved changes. |
| **Script › Overview** | The script's settings on the right: name, category, description, interpreter, default host, working directory, environment, platforms; below, recent runs. (The old `/configure/` URL opens this page.) |
| **Script › Parameters** | The **parameter definitions** the run form is built from (click a row to edit; hover to see where the script uses it). |
| **Script › Run** | On the right: where the values come from (defaults, a recent run or a preset), the host and the parameters (focusing one marks the lines that use it), and *+ Save current values as preset…* at the end of the values list. **Run** (Ctrl+Enter) in the bottom bar, with the mode **Normal · Trace · Debug** next to it, starts the script straight away (unsaved script edits are saved first) and opens the run as a tab next to the script; **Reset** loads the defaults. |
| **New script** (`/scripts/new/`, *File → New script*, **+** in the explorer header) | Name, language, optional sub-folder and category; creates the file (with a short starter) and its metadata in the open folder, then opens it in the editor. |
| **Console** (`/executions/RUN-…/`) | Step 3. Live status, duration, exit code, stdout and stderr (side by side, stacked or stdout only), auto-scroll, copy, download, stop; the running line (trace) or the **debugger** (debug). |
| **Runs** (`/history/`; `/executions/` redirects here) | What is running or queued on top (refreshes by itself while something runs), then every execution with search, filters, paging, delete and retention. |
| **History** | Search and filter every past execution, open or download logs, delete runs, apply retention. |
| **Settings** | Features, concurrency limits, timeouts, polling, retention, interpreters (local and SSH), SSH command, environment variables, SSH host test. |

### Keyboard and command palette

**Ctrl+K** opens the command palette: fuzzy-search any script (Enter opens it,
Ctrl+Enter runs it), type `run ` to list run actions only, paste a run id such as
`RUN-20260101-001` to open it, or pick a page or command. **?** shows every shortcut.

| Keys | Action |
|---|---|
| `Ctrl+K` | Command palette |
| `Ctrl+B` | Toggle the side panel |
| `/` | Focus the page search (or the explorer filter) |
| `g o` `g l` `g h` `g s` | Go to Home, Library, Runs, Settings |
| `g n` `g f` | New script, Open folder |
| `g n` | New script (in the open folder) |
| `i` (or `c`) `r` | On a script: Overview (configuration), Run (run / view / edit) |
| `Ctrl+Enter` | Run (Run tab), Create (New script) |
| `Ctrl+Alt+B` `Ctrl+J` | Hide/show the right panel, the saved-values panel (script pages) |
| `F5` `F10`/`n` `F6` `Shift+F5` | Debugger: continue, step, pause, stop |


---

## Configuration

All settings live in `config.json` in ScriptHub's data folder (created on first save; in this README the data
folder is written `runtime/`, see below). Edit them in
**Settings** or by hand; they are validated before being saved, the previous file
is kept as `config.json.bak`, and changes apply to the next execution without a
restart.

```json
{
  "interpreters": {".py": "python", ".sh": "bash", ".bat": "cmd"},
  "remote_interpreters": {".py": "python3", ".sh": "bash"},
  "ssh_executable": "ssh",
  "ssh_connect_timeout": 10,
  "environment": {},
  "enable_script_editing": true,
  "max_concurrent_executions": 3,
  "max_concurrent_per_host": 3,
  "default_timeout": 3600,
  "history_retention_days": 90,
  "log_retention_days": 30,
  "polling_interval": 2000
}
```

| Key | Meaning |
|---|---|
| `interpreters` | File extension → command used **on this computer**. Only listed extensions are treated as scripts. |
| `remote_interpreters` | Extension → command used **on SSH hosts**. |
| `ssh_executable` | The SSH command (a path or command line). Uses your own `~/.ssh/config`. |
| `ssh_connect_timeout` | Seconds before an SSH connection attempt gives up. |
| `environment` | Extra environment variables for every run (a script's own settings and parameters override them). |
| `enable_script_editing` | Allow creating and editing script files from the browser (`false` blocks New script, editing and restoring). |
| `max_concurrent_executions` | Runs beyond this wait in the queue. |
| `max_concurrent_per_host` | Same limit, applied to this computer and to each SSH host separately. |
| `default_timeout` | Seconds before any run is stopped (status *Timed out*). |
| `history_retention_days` | Finished executions older than this are deleted. |
| `log_retention_days` | After this many days only the record stays; stdout/stderr are removed. |
| `polling_interval` | Milliseconds between console polls. |

Environment variables for the web process:

| Variable | Default | Meaning |
|---|---|---|
| `SCRIPTHUB_DEBUG` | `1` | Set `0` for production (hides error pages; static files then need a web server, see [Production deployment](#production-deployment)). |
| `SCRIPTHUB_ALLOWED_HOSTS` | *(localhost only)* | Comma-separated extra host names, e.g. `tools.example.com`. |

The names used before the rename (`SCRIPT_RUNNER_DEBUG`, `SCRIPT_RUNNER_ALLOWED_HOSTS`) still work.


### Where ScriptHub keeps its data

Settings, run history and logs, presets, sequences and script backups are kept **outside the code
folder**, so the code can be updated or replaced without touching them. The first that applies:

1. `SCRIPTHUB_HOME`, when this environment variable is set;
2. a `runtime` folder next to the code, when one exists (**portable mode**: create it to keep everything
   together, e.g. on a USB stick; installs from before 2.0 keep using theirs);
3. your user data folder: `%LOCALAPPDATA%\ScriptHub` (Windows), `~/Library/Application Support/ScriptHub`
   (macOS), `~/.local/share/scripthub` (Linux, or `$XDG_DATA_HOME/scripthub`).

This README writes that folder as `runtime/`. **Settings → Data** shows where it is and how much each kind
of data takes, and clears one kind or all of it (queued and running runs are kept; your scripts are never
touched). The same from a terminal:

```bash
python manage.py clear_runtime                    # show what is stored where
python manage.py clear_runtime history presets    # history, presets, sequences, backups, metadata,
python manage.py clear_runtime all                #   workspace, imports, desktop, settings, or all
```

---

## Script folder

ScriptHub works on **one folder at a time**, like an editor's *Open Folder*, plus any
number of **script files** opened on their own (*File → Open file…*, several at once;
with no folder open the explorer shows just those files; New script needs a folder).
Each opened file can be closed again (*File → Close <file>* / *Close opened files*).
It is not a setting: on first start the Overview asks you to open one (or the
`sample_scripts` folder), and the folder you open is reopened on the next start.

* **Open folder…** / **Open file…** (File menu, Ctrl+K) show the system dialog
  (folders, or files filtered to the script types) and open what you pick. The dialog appears on the machine
  running ScriptHub, so it is offered in the desktop app and in a browser on that
  same machine. From another machine, the `/folder/` page is used instead: type a
  path (folder or script) or click through the drives, sub-folders and scripts.
* A large folder (a whole drive, say) is scanned in the background: the app stays
  usable, the status bar shows the folders and scripts counted so far, and the
  Overview / Library reload when the scan is done.
* **Recent** keeps the last 10 folders and files; **favourites** (star) stay until
  you unstar them. Both are in *File → Open recent* and the command palette
  (Ctrl+K), so switching is one click.
* The state lives in `runtime/workspace.json`. An older `script_directories`
  list in `config.json` is used once to fill it (first existing folder is opened,
  all of them become recent).

The folder is scanned **recursively, up to 3 sub-folder levels deep** (deeper folders are skipped, with a warning in the Library), and new files appear without a restart
(press **Refresh** to rescan immediately). Switching folders does not stop runs
that are in progress, and history keeps runs of scripts from other folders.

Rules that keep this safe:

* Only extensions listed under **Interpreters** are catalogued. A stray `.exe`,
  `.txt` or `.json` is ignored.
* Hidden folders, `__pycache__`, `venv`, `node_modules` and `site-packages` are skipped.
* Symlinks that point outside the open folder are skipped.
* Nothing is executed during discovery.
* Every script with valid metadata can be run. A run is stopped after the
  **default timeout** from Settings, and a script runs one at a time (a second
  run of the same script waits in the queue).

---

## Writing scripts

A script is an ordinary program. ScriptHub starts it with **command-line
arguments** built from the parameters, so any language that reads arguments works.

### Python and argparse

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--backup-path", required=True)
parser.add_argument("--compress", action="store_true")
parser.add_argument("--retention-days", type=int, default=30)
args = parser.parse_args()

print(f"Backing up {args.database}")
print(f"Destination: {args.backup_path}")
```

Its metadata is a comment block at the top of the script itself (ScriptHub writes
it when you save the Overview or Parameters tab; you can also edit it in the code):

```python
# --- scripthub ---
# name: Database Backup
# category: Database
# parameters:
#   - name: database
#     type: text
#     required: true
#   - name: backup_path
#     type: directory
#     required: true
#   - name: compress
#     type: boolean
#     default: true
#   - name: retention_days
#     type: number
#     default: 30
#     min: 1
#     max: 365
# --- end scripthub ---
```

By default a parameter named `backup_path` becomes the option `--backup-path`
(underscores become hyphens). A **boolean** adds its flag only when true.

### Bash

```bash
#!/usr/bin/env bash
set -euo pipefail
while [ $# -gt 0 ]; do
  case "$1" in
    --path) path="$2"; shift 2 ;;
    --top)  top="$2";  shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
du -sk -- "$path"/* | sort -rn | head -n "$top"
```

See `sample_scripts/system/disk_usage.sh`.

### Batch (`.bat`, Windows)

```bat
@echo off
rem Parameters passed as pass_as "positional": %1, %2 ...  (use %~1 to drop quotes)
ping -n %~2 "%~1"
exit /b %ERRORLEVEL%
```

For batch files ScriptHub **rejects** the characters `& | < > ^ % ! "` and a
backtick in any value, because `cmd.exe` would treat them as commands. Add a
`pattern` to the parameter to be even stricter.

### Good behaviour for scripts

* **Exit code 0 means success.** Any other code is shown as *Failed*. ScriptHub
  only reports success after the process has really exited with 0.
* Print progress to **stdout**, problems to **stderr**; the console shows them in
  separate panes.
* Flush output if you want to see it live (`print(..., flush=True)`). For Python,
  ScriptHub already sets `PYTHONUNBUFFERED=1`.
* Scripts run **without a terminal and without stdin**; do not prompt for input.
* For **SSH** runs the script is read by the remote interpreter from standard
  input, so it cannot use `__file__` or import neighbouring files, and stdin is
  not available to it.

---

## Metadata reference

Metadata is optional. Without it a script is listed with a name made from its
file name, the folder as its category, and no parameters. It lives **in the script
itself**: YAML in a comment block between `# --- scripthub ---` and
`# --- end scripthub ---` (`:: --- scripthub ---` in `.bat` / `.cmd` files), at the
top of the file, after a `#!` line, a Python encoding line or `@echo off`. One file
to copy, move or commit. The Overview and Parameters tabs write the block for you;
you can also edit it in the script.

The YAML is a plain subset (ScriptHub reads it without extra packages): `key: value`
lines, `- ` list items, nested keys indented with spaces, one-line lists and mappings
(`choices: [full, incremental]`, `- {name: a, type: text}`), `# comments`, and quoted
strings (`"…"` with JSON escapes such as `\n`, or `'…'`). Quote a value that contains
`: ` or ` #`, or that would otherwise read as a number or `true`/`false`/`null`
(`"007"`, `"yes"`). Anchors and multi-line `|` / `>` strings are not supported.
Blocks written as JSON by older versions (`# {` … `# }`) are still read and are
rewritten as YAML on the next save. Scripts from older versions may also have a
`<stem>.json` next to them: it is read until the first save, which moves it into the
script. If the script cannot be written, the metadata goes to
`runtime/metadata_overrides/<path id>.json` instead.

**Script id.** The first time ScriptHub writes the block it adds `id` (12 random hex
characters). That id stays with the file, so a script that is **moved or renamed keeps
its saved values, backups and run history**. Scripts that never had their metadata
saved are known by an id made from their path until then; when the stable id is
written, the old one keeps working (links, earlier runs) and the script's saved values
and backups move to the new id. A **copy** of a script carries the same id: the Library
warns about it, the original keeps the id, and the copy gets a new one on its next save.
Don't edit the id by hand (remove it to get a new one).

| Field | Type | Meaning |
|---|---|---|
| `id` | 12 hex characters | Written by ScriptHub on the first save; keeps saved values, backups and history with a moved or renamed script. Do not edit. |
| `name` | text | Display name. |
| `description` | text | Shown on cards and details. |
| `category` | text | Groups scripts; default is the first sub-folder name. |
| `interpreter` | text | Overrides the interpreter (a command or full path) on every host. |
| `host` | `local` or an SSH alias | Default host (can be changed on the run form). |
| `working_directory` | path | Working directory for the run. Default: the script's folder (local) or the login directory (SSH). |
| `environment` | mapping | Extra environment variables (`NAME: value` lines indented under it). |
| `platforms` | list | Any of `windows`, `linux`, `macos`; used by the Library's Platform filter. Defaults come from the file type (`.bat` → Windows, `.sh` → Linux/macOS, `.py` → all). |
| `parameters` | list | See below. |

Unknown fields are kept untouched, so metadata can grow without breaking. The
old `enabled`, `timeout` and `allow_concurrent` fields are ignored and dropped
the next time the metadata is saved.

When you save on the Overview or Parameters tab the metadata is **validated
first**; invalid data is never written and every problem is listed. Only the
block is rewritten (line endings and a BOM are kept), and the previous version of
the script is kept as a backup (**Restore previous** in the editor).

---

## Defining parameters

One item of the `parameters` list:

```yaml
- name: retention_days
  label: Retention Days
  type: number
  default: 30
  required: true
  min: 1
  max: 365
  help: Delete backups older than this
  editable_at_runtime: true
```

| Attribute | Meaning |
|---|---|
| `name` | Identifier (letters, digits, `_`). Unique per script. |
| `label` | Text shown on the form. |
| `type` | One of the types below. |
| `default` | Initial value (must itself be valid). |
| `required` | Must be filled in. |
| `help` / `placeholder` | Hint texts. |
| `choices` | For `select` and `multiselect`: `["a", "b"]` or `[{"value": "a", "label": "Option A"}]`. |
| `min` / `max` | Limits for `number` and `decimal`. |
| `min_length` / `max_length` | Limits for text-like types. |
| `pattern` | A regular expression the **whole** value must match. |
| `editable_at_runtime` | `false` fixes the value at its default; it is shown but cannot be changed. |
| `pass_as` | How the script receives it: `flag` (default), `positional` or `env`. |
| `arg` | The option name for `flag` (default `--name-with-hyphens`). |
| `position` | Order for `positional` (1, 2, …). |
| `env_name` | Variable name for `env` (default: the name in capitals). |

**Types:** `text`, `textarea`, `number` (whole numbers), `decimal`, `boolean`,
`password`, `select`, `multiselect`, `date` (`YYYY-MM-DD`), `datetime`, `file`
(a path), `directory` (a path).

* A **multiselect** repeats its flag (`--tags a --tags b`) or joins with commas
  for `env`.
* Empty optional values are not passed at all.
* `file` and `directory` are typed paths on the machine that runs the script
  (there is no file browser or upload).
* A `password` is passed like any other value (use `pass_as: env` to keep it
  off the process list), shown masked everywhere, **never stored in history or
  presets**.
* Environment variables such as `PATH`, `LD_PRELOAD` and `PYTHONPATH` cannot be
  set from a parameter.

New types can be added in `runner/parameter_service.py` (`register_type`).

---

### Step debugging

Choose the run mode **Debug** next to the **Run** button for a Python or
Bash script, on this computer or an SSH host. The run pauses
on the script's first line and the console turns into a debugger:

* **Step** (`F10` or `n`) runs one line and pauses again (it goes into the script's own
  functions); **Continue** (`F5`) runs to the next breakpoint; **Pause** (`F6`) stops at
  the next line; **Detach** ends debugging and lets the script finish at full speed;
  **Stop** (`Shift+F5`) ends the run.
* **Breakpoints:** click a line number in the source. They are listed (and removable)
  on the right and can be set while the run is starting.
* **Variables and call stack** (Python): locals and globals of the paused frame with
  type and a shortened value (changed values are marked), and the stack of the script's
  own frames. Bash shows the current line only.
* **How:** the commands travel over the process's standard input, which stays open for
  the run (also through SSH, so a step costs no extra connection). The script and the
  programs it starts get an empty stdin. Pause/variable reports travel on stderr like
  the trace markers and are removed from `stderr.log`.
* **Limits:** time spent paused counts toward the timeout. Bash pauses on simple
  commands (not inside `$(...)`). Only the script's own lines are stepped through.

## Running a script, hosts and presets

1. The **Run** tab shows the script with a form on the right, built from the metadata (it opens with the last run's values).
2. Choose the **Host**: `local` or any SSH host.
3. Optionally load a **preset** or an earlier run from the list above the form.
4. **Run** (Ctrl+Enter) validates every value and starts the script; it opens as a tab
   next to the script in the centre (the exact command is under *Parameters & command*).
5. Pick the **run mode** (Normal, Trace lines or Debug — the last two for Python and Bash)
   and press **Run**; the run opens as a tab next to the script.

Values are validated for type, range, choices and pattern, on the server.

### Saved parameters (presets and recent runs)

Run the same script with different, remembered sets of values. They are kept in
`runtime/`, **one YAML file per saved set**, not in the script folder:

```
runtime/presets/<script id>/production-backup.yaml        a preset
runtime/presets/<script id>/recent/RUN-20261108-005.yaml  the values of a recent run
```

```yaml
# Preset for C:\scripts\system\directory_backup.py (ScriptHub). Passwords are never stored.
name: Production backup
host: local
saved_at: 2026-11-08T20:44:13
values:
  source: /data/project
  compress: true
```

They belong to the script's id (see *Script id* under the metadata reference), so
they follow a moved or renamed script once its metadata has been saved. The files use
a small YAML subset (written by ScriptHub, fine to edit by hand).

* **Recent runs** are remembered automatically: every started run stores the values it
  used (the last 10 distinct sets, newest first). The **Run** tab opens with the
  **last run's values**; **Reset** (or *Defaults* in the list) goes back to the
  script's defaults.
* **Presets** are named sets: *Save values as preset…* (an existing name updates it),
  load, rename, delete.
* **Run with saved values:** every preset and recent run has a **▶ Run** button (on the
  Parameters tab and in the comparison grid) that starts the script with those values
  already filled in and validated; **Ctrl+Enter** launches.
* **Compare:** the **Saved values** panel under the script (click its bar or **Ctrl+J**; drag its top edge to resize) shows a grid,
  rows = parameters, columns = presets and recent runs; values that differ from the
  script's default are highlighted.
* Values are **re-validated against the current metadata** each time they load: removed
  parameters are ignored, new ones get defaults, values that are no longer valid are
  reset, and each case shows a warning.
* **Passwords and fixed parameters are never saved.**
* Older layouts are converted on first use and then removed: `<stem>.params.json`
  next to the script, `runtime/presets/<script id>.json` and
  `runtime/presets/<script id>/<name>.json`.
* A file that cannot be read is skipped and never overwritten.

### Sequences: scripts connected by arrows

A **sequence** runs existing scripts in the order you draw. Open **Sequences** in the
activity bar (or **g q**) and create one; its page is a diagram:

* **The side panel** on the Sequences pages has two sections: **Sequences** on top (your favourites ★
  first, then by name; **+** creates one, type a name and press Enter; on hover each has ★, rename and
  delete) and **Scripts** below (the open folder, to drag from). Drag the line between them to resize;
  click *Sequences* to fold that section. Favourites are kept per machine (`runtime/workspace.json`, like
  folder favourites), not in the sequence file, and also appear on the Sequences start page.
* **Add a step**: drag a script from the explorer on the left and drop it anywhere on the
  page. A script can be used more than once.
* **Move** a box by dragging it (it snaps to the grid); **pan** by dragging the background
  or with the mouse wheel, **zoom** with Ctrl+wheel or **−** / **+**; **Fit** shows all
  steps; **Tidy up** lays the boxes out in rows that follow the arrows.
* **Connect** two steps: drag the dot under a box onto the step that comes next.
  The arrow's label says when it applies; click it to change:
  * **✓ succeeded** (default): the step before succeeded;
  * **✗ failed**: the step before did not succeed (failed, timed out, was stopped);
  * **↷ always**: the step before finished, either way.

  Hover a label for its **×** to remove the arrow. Arrows that would make a loop are refused.
* **Remove** a step with the **×** on its box, or select it and press **Delete**
  (its arrows go with it).
* **Open a step's script**: double-click its box, or its file icon. The script opens on its usual page
  (editor, Overview, Parameters, Run) as a tab of the centre bar, and the **sequence is a tab** in that same bar:
  one click goes back to the diagram, with the same view and the step still selected. Several scripts and
  sequences can be open as tabs; × closes one. Only scripts of the open folder have a page: for another one the
  diagram says to open its folder. On a sequence run, double-click a box for its script too.
* **Click a box** to set the step in the right-hand column: **Values** (*saved with this
  step*, edited with the script's own form, starting from the script's last run, or **a
  preset** of the script, which stays up to date), **Host** (the script's default, `local`
  or an SSH host) and, when several arrows lead into it, whether it **waits for all**
  of them (default) or **starts after the first**.

How a sequence runs:

* Steps with no arrow into them start first. A step starts when the arrows into it allow
  it: with *wait for all*, when every arrow applies (if one never will, the step is
  **skipped**); with *start after the first*, as soon as one applies. An arrow from a
  skipped step never applies, so skipping spreads down the arrows.
* Steps that are ready at the same time run **side by side**; the Settings limits on
  concurrent runs and runs per host still apply (and a script runs once at a time).
* The sequence is *Failed* when a step did not succeed and **no ✗ failed arrow** leads on
  from it; a failure that has such an arrow counts as handled. Otherwise it is *Success*
  (*Cancelled* when stopped; like runs, not resumed after a restart: *Interrupted*).

**▶ Run** in the diagram's toolbar (Ctrl+Enter) first checks every step (script found, values valid, host
allowed) and starts nothing if one is wrong. Password parameters are asked for in the right-hand column each
time and never saved. The sequence then runs **on the diagram itself**: each box shows its live status, the
arrows that applied stand out and the others fade, and the right-hand column follows the console of the
running step (click a box to see another one). The diagram cannot be changed while the run is going; the
statuses stay after it until your next change or run.

* **⏸ Pause** (same button, Ctrl+Enter): the steps running now finish, no new step starts. The run shows
  *Paused*; **▶ Resume** carries on where it stopped.
* **■ Stop**: the running steps are stopped and the rest are skipped (also while paused).
* **On a box**: ▶ runs **only that step** (with its values; a run of its own, shown on the diagram). During a
  run, ⏸ on a step that has not started yet **pauses the run before it**, like a breakpoint: the box turns
  amber with *Paused here* and **▶ Resume** starts it. Click ⏸ again to take it back.
* The status chip next to the buttons (*Running 2/4 · 12s*) opens the run's own page, which has the same
  Pause / Resume / Stop buttons and is where History links to.

Every step is a normal run in **Runs**, and its console links back to the sequence run.

Sequences are kept in `runtime/sequences/`, one YAML file each, and their runs in
`runtime/sequence_runs/SEQ-YYYYMMDD-NNN/metadata.json` (with the diagram as it was):

```yaml
# Sequence for ScriptHub. Passwords are never stored.
name: Nightly maintenance
saved_at: 2026-11-09T21:10:00
steps:
  - id: s1                        # step id: the arrows refer to it
    script: 3f9a1c0b7d22          # the script id (stable id from its metadata block)
    path: C:\scripts\db\backup.py # last known path: shown, and used when its folder is not open
    name: Database backup
    preset: production            # values from this preset ...
    x: 240                        # place on the page
    y: 40
  - id: s2
    script: 81be44f0a913
    path: C:\scripts\cleanup.sh
    name: Cleanup
    host: web-1                   # empty or missing = the script's default host
    values:                       # ... or values stored with the step
      days: 30
    join: any                     # several arrows in: all (default) or any (the first)
    x: 240
    y: 160
links:
  - from: s1
    to: s2
    when: success                 # success | failure | always
```

A step finds its script by id in the open folder, else at its last known path (when that
file still has the same id), so a sequence keeps working when another folder is open.
A file that cannot be read is listed with the reason and never overwritten. Files from
the first version of sequences (a plain list of steps with `on_failure: stop|continue`)
open as a top-to-bottom chain (`continue` = an *always* arrow) and are written in the new
form on the next change.

### Project files (.scripthub): take a folder and its sequences anywhere

**File → Save project as…** opens the system Save As dialog and writes one `.scripthub` file (a zip) there: the
open folder's scripts and other files (helpers, data, documents), the sequences that use its scripts (with any
script they need from elsewhere, under `_outside/`) and the scripts' presets. It never holds passwords, run
history, Settings, files that look like secrets (`.env`, keys, keystores) or tool folders (`.git`, `.venv`,
`node_modules`, `__pycache__`). (Browsers without the Save As API download the file instead.)

**File → Open project…** on any computer: choose the file and ScriptHub shows what is in it and what to check
(a host not in your settings, a path in a step's values that does not exist here, a sequence name that is taken,
which then gets "(2)"). Then pick a **new or empty folder**: the files are unpacked there (nothing is
overwritten), the sequences and presets are added (an existing preset is kept), every step points at its
unpacked script, and the folder opens. Opening runs nothing; the file is checked first (no names leaving the
folder, no links, size limits, every file against the SHA-256 in its manifest).

```
Ops.scripthub
├─ manifest.json            format, version, name, date; every file with size and SHA-256
├─ files/…                  the folder's files
├─ sequences/<slug>.yaml    step paths point inside the project (files/…)
└─ presets/<n>/<slug>.yaml  presets, with the script each belongs to in the manifest
```

**File → Close project** closes the open folder and every opened file in one go. Nothing is deleted: the
sequences and presets stay, and the folder is in *Open recent*.

---

## Creating and editing scripts

### New script

**New script** (*File → New script*, the **+** in the explorer header, `g n`, or Ctrl+K)
creates a file in the **open folder**: give a file name, language, and optionally a
sub-folder (created if needed; existing ones are suggested) and a category. The file
starts with its metadata block and a few lines of example code, then
opens on its **Run** tab with the editor in the centre. Opened from a script page,
the form starts in that script's folder, language and category.

* Existing files are never overwritten (the file is created with exclusive mode).
* Only simple file and folder names are accepted; absolute paths, drives and `..`
  are refused, so nothing is written outside the open folder.
* It is part of **Edit scripts in the browser** in Settings (off = no New script).

### Editing a catalogued script

**Edit** (on a library card, the script's page or the source viewer) opens the
script in a code editor with syntax colours, line numbers, auto-indent, **Tab** /
**Shift+Tab** to indent and dedent, and **Ctrl+S** to save. **Save & run** saves
and goes straight to the run form.

Saving is designed not to lose work or break scripts:

* **A backup is always kept**, outside the script folder: every save (editor,
  restore, Overview / Parameters) stores the version it replaces in
  `runtime/backups/<script id>/` as a timestamped copy; the last 10 per script are
  kept. **Restore previous** puts the newest one back; **Versions…** next to it lists
  them all and restores the one you pick. What is there before a restore is backed up
  too, so a restore can be undone. A `<file>.bak` left next to a script by an older
  version is moved into the backups on the script's next save (it is only removed once
  the copy is written).
* **Conflicts are detected.** If the file changed on disk after you opened it, the
  save is refused and your text stays in the editor; you can reload, or choose
  *Overwrite the file with my version*.
* **Line endings stay right.** `.sh` files are always saved with LF (a CRLF shell
  script fails on Linux), `.bat` with CRLF, other files keep the style they had.
* A quick **syntax check** (Python `compile`, `bash -n`; the script is never run)
  shows a warning after saving. It does not block the save.
* Only catalogued files inside the open script folder can be edited; the
  editor never accepts a path from the browser. Files must be UTF-8 text up to
  512 KB; read-only files are refused.

### Sharing with other people

If other people will submit scripts, remember that **creating and editing scripts
means arbitrary code execution** with this application's permissions (and the SSH
accounts' on remote hosts). The application has no login, so:

* put your own authentication in front of it (reverse proxy, VPN, SSO) and run it
  under a dedicated, least-privilege account;
* in **Settings → Features**, switch off **Create and edit scripts in the browser**
  if people should only run the catalogue (`enable_script_editing` in `config.json`);
* every execution records the **client address** it came from (shown on the
  console and searchable in History). This is the connecting address; it is only
  as meaningful as your network setup: behind a reverse proxy it is the proxy's.

---

### Seeing which line is running

Choose the run mode **Trace** next to the **Run** button for
a Python or Bash script. The console then shows the script's source next to the
output with the current line highlighted and scrolled into view. The source shown is a
copy taken when the run started, so later edits do not shift the highlight.

* **How:** Python runs through a small launcher that reports line numbers
  (`sys.monitoring` on Python 3.12+, `sys.settrace` before that). Bash gets a `DEBUG`
  trap. Your script file is not modified. The markers travel on stderr and are removed
  again, so `stderr.log` stays clean.
* **Speed:** tracing slows the script (Python much more in tight loops). Press
  **Stop tracing** on the console to end it for the rest of the run.
  It cannot be switched back on.
* **Accuracy:** the page polls, so a fast script jumps ahead; the highlight shows roughly
  where it is. Only the script's own lines count (not imported modules or `source`d
  files). `.bat` files and `sh` (not bash) are not supported.
* **SSH hosts:** Python needs nothing on the host; Bash receives one extra first line, so
  line numbers are corrected by one. **Stop tracing** sends `SIGUSR1` to the script
  (it is ignored for Python before 3.12 on Windows, which has no such signal).

## Execution lifecycle

Every run gets an ID like `RUN-20261107-001` and moves through:

| Status | Meaning |
|---|---|
| **Queued** | Waiting for a free slot (a global, per-host or per-script limit is reached). |
| **Running** | The process is alive. |
| **Success** | The process exited with code **0**. |
| **Failed** | Non-zero exit code, could not start, or SSH connection failure. |
| **Timed out** | Exceeded its timeout and was stopped. |
| **Cancelled** | You pressed **Stop**. |
| **Interrupted** | The application stopped while the run was queued or running. |

How it works:

* Runs are started from an argument **list** (never through a shell) in a worker
  thread inside the web process. They do **not** depend on the browser staying open.
* The queue is in memory. A blocked run is skipped, not waited on, so one busy
  script never holds up unrelated ones.
* **Stop** and **timeout** kill the whole process tree. For SSH runs the remote
  process is stopped through a second SSH call.
* Submitting the confirm form twice (double click, reload) starts **one** run.
* At startup, runs left *queued/running* by an application that is no longer alive
  are marked **Interrupted** (runs belonging to another live instance are left alone).

---

## Logging, history and retention

Everything is on disk, so history survives restarts:

```
runtime/executions/RUN-20261107-001/
    metadata.json    status, timings, host, command, exit code, parameters (passwords masked)
    stdout.log
    stderr.log
```

* Files are written atomically; reading while a run is being written is safe.
* **History** lists script runs and **sequence runs** together, newest first. It searches IDs,
  script and sequence names, parameter values and error messages, filters by kind (*Show*), by
  script or sequence (a sequence shows its runs and their step runs), status and date, and lets
  you open, download or delete runs. A step run links to its sequence run; deleting a sequence
  run removes only its record (its step runs stay). Running sequences are in the Running panel.
* **Retention** (Settings): executions and sequence runs older than `history_retention_days` are
  deleted; logs older than `log_retention_days` are removed but the record stays.
  It runs when the app starts and when History is opened, or press
  **Apply retention policy now**. Queued and running executions are never deleted.
* Script output is stored exactly as the script wrote it. If a script echoes a
  secret, the secret is in the log.

---

## Running on Linux servers over SSH

ScriptHub uses your system `ssh` and does **no SSH configuration of its own**.

1. Make sure `ssh myserver` works from a terminal **without a password prompt**
   (key or agent). Host aliases come from `Host` entries in `~/.ssh/config`
   (wildcards are ignored; `Include` files are followed).
2. **Settings → SSH hosts → Test connection** checks the login.
3. Choose the host on the run form (or set a default on the script's **Overview**).

How a remote run works:

* The script file stays on **this** computer. It is sent to the remote
  interpreter over SSH (`python3 -`, `bash -s`), so nothing is installed on the
  server and one catalogue serves every host.
* Remote interpreters are set under **Settings** (`.py=python3`, `.sh=bash`) or per
  script with `interpreter`.
* ssh runs non-interactively (`BatchMode=yes`), so an unknown host key or a
  password prompt **fails fast** instead of hanging. Accept the host key once from
  a terminal.
* The remote process id is captured so **Stop** and **timeouts** can end it.
* Batch files cannot run remotely.
* Passwords given as arguments are visible in the remote `ps` list; use
  `pass_as: env` where a script supports it.

---

## Sample scripts and projects

`sample_scripts/` is a folder to open (**File → Open folder…**) and try things out. Every script is standard
library Python, Bash or batch, has its metadata block at the top, and is safe to run: nothing is deleted
unless you tick it, and nothing needs a server.

| Folder | Scripts | Shows |
|---|---|---|
| `basics/` | **Hello** in Python, Bash and batch · **Parameter tour** · **Succeed, fail or take a while** | flags, positional and environment arguments; every parameter type, passwords, a locked value; the live console, Stop, Trace and Debug |
| `files/` | **Find large files** · **Clean up old files** (dry run by default) · **Back up a folder** (.zip / .tar.gz, keeps the newest N) · **File checksum** | directory and file parameters, exit codes |
| `system/` | **System information** · **Disk space check** (fails above a limit) · **Largest folders** (Bash, for SSH hosts) · **List a folder** (batch) · **Log a message** | scripts for every platform; a step to end a sequence with |
| `network/` | **Check a TCP port** · **Check a web page** · **Ping a host** (batch) | pass/fail checks for sequences |
| `data/` | **Summarise a CSV file** · **Search a log file**, with `sales.csv` and `app.log` next to them | relative paths start in the script's folder |

`sample_projects/` holds three project files built from those scripts. Open one from **Home → Samples**
(no file to choose) or with **File → Open project…**: it unpacks into a new folder of your choice and adds a
sequence and some presets. **Home → Samples → Sample scripts** opens `sample_scripts/` itself.

| Project | Sequence |
|---|---|
| **Getting started** | *Try a sequence*: Hello → *Succeed, fail…* → one Hello after the success arrow, another after the failure arrow. Change the middle step to *failure* and run it again. |
| **Health check** | *Daily health check*: system information, then disk, port and web checks side by side; *Log a message* when all pass (join *all*) or when any fails (join *any*). |
| **Files and data** | *Log and sales report*: a CSV summary next to a log search that fails when it finds errors, with a message after each outcome. |

After changing a sample script, rebuild the projects with `python tools/build_sample_projects.py`; the test
suite fails while a project holds an outdated copy.

---

## Security

ScriptHub starts operating-system processes and has **no authentication**.

* It listens on **127.0.0.1** by default. To reach it from elsewhere use an SSH
  tunnel (`ssh -L 8000:localhost:8000 you@host`) or put your own authenticating
  reverse proxy in front. **Never expose it directly to the internet or an
  untrusted network.**
* Only scripts in the open folder, with a configured extension, can be
  run. The source viewer and run endpoints take a catalogue ID, not a path.
* Processes are started with `shell=False` from argument lists; user input is
  never concatenated into a command. For SSH, every element of the remote command
  is quoted, and host names must be known aliases (and cannot start with `-`).
* Batch files get an additional character filter; environment variables that
  could hijack execution (`PATH`, `LD_PRELOAD`, …) cannot be set from parameters.
* CSRF protection is on for every form.
* Passwords are masked in the UI, history and command preview, and never written
  to presets. Output written by scripts is stored as-is.
* **Anyone who can edit metadata or settings can change what runs** (interpreter,
  arguments, environment). Treat access to the page as access to this account.
* **Creating and editing scripts lets anyone with access run any code.** It can be
  switched off in **Settings → Features**; see [Sharing with other people](#sharing-with-other-people).
  Saves are atomic and keep the previous version in `runtime/backups/`, only catalogued files are writable, and
  New script never overwrites or leaves the open folder.
* Every run records the connecting client address (`X-Forwarded-For` is
  deliberately not trusted, because any client can forge it).
* Run the application under a **dedicated, least-privilege operating-system
  account**, and use restricted SSH accounts/keys for remote hosts.

---

## Production deployment

The development server is fine for one person on one machine. For something that
stays up, run it under a supervisor so the web process (and therefore the
execution threads) is restarted if it dies.

Important design constraint: execution state lives **inside one process**, so run
**exactly one worker** (use threads for concurrency, not processes).

### Linux (systemd + gunicorn)

`pip install gunicorn`, then `/etc/systemd/system/scripthub.service`:

```ini
[Unit]
Description=ScriptHub
After=network.target

[Service]
User=scriptrunner
WorkingDirectory=/opt/scripthub
Environment=SCRIPTHUB_DEBUG=0
Environment=SCRIPTHUB_ALLOWED_HOSTS=tools.example.com
ExecStart=/opt/scripthub/.venv/bin/gunicorn scripthub_project.wsgi:application \
          --bind 127.0.0.1:8000 --workers 1 --threads 8 --timeout 120
Restart=on-failure
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`systemctl enable --now scripthub`. With `SCRIPTHUB_DEBUG=0` Django does
not serve static files, so let nginx do it:

```nginx
location /static/ { alias /opt/scripthub/static/; }
location /        { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
# add authentication here (auth_basic, SSO, VPN-only listener, ...)
```

### Windows

* Simplest: `python manage.py runserver 127.0.0.1:8000 --noreload --insecure`
  (the `--insecure` flag lets the built-in server deliver static files when
  `SCRIPTHUB_DEBUG=0`). Wrap it as a **service** with
  [NSSM](https://nssm.cc/) or a Task Scheduler task "at startup" with restart on failure.
* Or use `pip install waitress` and
  `waitress-serve --listen=127.0.0.1:8000 --threads=8 scripthub_project.wsgi:application`
  with a web server for `/static/`.

### Keeping scripts alive

Scripts run as children of the web process. If the web process is stopped or
crashes, **their supervision ends** (see the next section). To make long jobs
independent of the web app, start a real service from the script (for example
`systemctl start my-job`) and let the script return.

---

## Limitations of long-lived subprocesses

* **Stopping the app stops supervision.** Runs are marked *Interrupted* at the next
  start. On Linux/macOS the children are in their own process group, but a hard
  kill of the web process can leave them running; on Windows a script may keep
  running after the app exits. The *Interrupted* note includes the PID when the
  process is still alive.
* **Several web workers will not work.** The queue, limits and Stop buttons are
  per process; run one worker.
* **Reloaders kill jobs.** `runserver` without `--noreload` restarts the process on
  any code change. Platforms that recycle workers (gunicorn `--max-requests`,
  serverless/PaaS hosts, IIS app-pool recycling) will interrupt long runs.
* **Hosting environments that freeze background threads** between requests are not
  suitable.
* **SSH runs depend on the connection.** If this app stops mid-run the remote
  script may keep going on the server. Long jobs should be started as services.
* Output of `.bat` files is read as UTF-8; non-English console output may look garbled.
* Time stamps use this computer's local time.

---

## Testing

```bash
python manage.py test runner
```

The suite starts **real processes**: success and failure, live output, timeouts,
cancel (including child processes), the queue and every concurrency limit, restart
recovery, SSH execution against a fake `ssh`, batch-injection guards, presets,
history and retention, and the sample scripts and projects. Tests that need Bash or Windows skip
themselves where unavailable. No test needs internet.

A separate Node script checks the syntax highlighter:
`node tools/test_highlight.js`.

---

## Project layout

```
ScriptHub/
├── manage.py
├── config.py                 filesystem-backed configuration
├── requirements.txt          Django only
├── runner/
│   ├── views.py, script_views.py, execution_views.py, history_views.py,
│   │   preset_views.py, edit_views.py, new_views.py,
│   │   folder_views.py, sequence_views.py                 thin HTTP layer
│   ├── workspace.py          open folder, recent and favourite folders
│   ├── script_editor.py      safe saving of script files (backup, conflicts, line endings)
│   ├── discovery.py          approved-script catalogue
│   ├── metadata.py           the metadata block in the script: read/write, backups
│   ├── parameter_service.py  types, validation, argument building
│   ├── executor.py           builds and supervises one process (local / SSH)
│   ├── process_manager.py    queue, limits, threads, cancel, recovery
│   ├── history.py, history_ops.py   run records, search, retention
│   ├── sequences.py, sequence_runner.py   sequences: storage (steps + arrows), running them
│   ├── presets.py (saved parameters), ssh_hosts.py, security.py, validators.py, fsutil.py
│   └── test*.py
├── sample_scripts/           example scripts to open as a folder (basics, files, system, network, data)
├── sample_projects/          example project files (.scripthub), built by tools/build_sample_projects.py
├── templates/, static/       Django templates, custom CSS, vanilla JavaScript
└── tools/test_highlight.js
```

ScriptHub's own data (settings, history, presets, sequences) is not in the code folder: see
[Where ScriptHub keeps its data](#where-scripthub-keeps-its-data).

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| A script is missing from the library | Its extension is not under **Interpreters**, it is in an unconfigured folder, or press **Refresh**. |
| *Fix metadata* instead of **Run** | The metadata block at the top of the script is not valid (YAML, or JSON in an old block); the error names the lines. Fix it in the editor. |
| Status **Failed**, error *SSH connection failed* | Run `ssh <host>` in a terminal: accept the host key, confirm key login works without a password. |
| `Could not start the process` | The interpreter is not installed or not on `PATH`. Set the full path under **Settings → Interpreters**. |
| `.sh` scripts fail on Windows locally | Local `.sh` needs Bash (Git Bash or WSL) on `PATH`; or run them on a Linux host over SSH. |
| Console shows output only at the end | The script buffers output; flush it (`python -u`, `print(flush=True)`, `stdbuf`). |
| Stuck at **Queued** | A concurrency limit is reached, or a non-concurrent script is already running; check **Executions**. |
| **Interrupted** after a restart | The app stopped while the run was active (see [Limitations](#limitations-of-long-lived-subprocesses)). |
| Pages have no styling in production | With `SCRIPTHUB_DEBUG=0`, serve `/static/` from your web server or use `--insecure`. |
| `DisallowedHost` error | Set `SCRIPTHUB_ALLOWED_HOSTS` to the host name you browse to. |

---

## Licence and contributing

Released under the [MIT licence](LICENSE). Contributions are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately as
described in [SECURITY.md](SECURITY.md).

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
4. [Script folder](#script-folder)
5. [Writing scripts](#writing-scripts)
6. [Metadata reference](#metadata-reference)
7. [Defining parameters](#defining-parameters)
8. [Running a script, hosts and presets](#running-a-script-hosts-and-presets)
9. [Creating and editing scripts](#creating-and-editing-scripts)
10. [Execution lifecycle](#execution-lifecycle)
11. [Logging, history and retention](#logging-history-and-retention)
12. [Running on Linux servers over SSH](#running-on-linux-servers-over-ssh)
13. [Bundled scripts](#bundled-scripts)
14. [Offline installation of pip packages (walkthrough)](#offline-installation-of-pip-packages-walkthrough)
15. [Security](#security)
16. [Production deployment](#production-deployment)
17. [Limitations of long-lived subprocesses](#limitations-of-long-lived-subprocesses)
18. [Testing](#testing)
19. [Project layout](#project-layout)
20. [Troubleshooting](#troubleshooting)
21. [Licence and contributing](#licence-and-contributing)

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

Try it: open **Script Library**, press **Run** on *System Information*, then
**Run**. The run opens as a tab next to the script.

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

Version 2 is laid out like an IDE workbench (dark theme by default; **Theme** in the
status bar or the moon/sun icon switches to light, remembered per browser):

* **Activity bar** (far left): Home (the app icon), Library, **Runs** (what is running
  now, with a live count, and the history), Settings, plus the side-panel toggle,
  the command palette and the theme switch.
* **Side panel**: the **Explorer** (every script grouped by category, with a dot for its
  last result and a filter box) on most pages; the list of executions in the
  console; the filters on History; the section list on Settings. **Ctrl+B** hides it.
* **Main area**: no page header (the activity bar shows the section, the explorer the
  script); script pages have the tabs **Overview · Parameters · Run** on top of a right
  column that **Ctrl+Alt+B** (or its ◧ button) hides and shows.
* **Status bar**: running/queued count, command palette, shortcuts, theme.

| Page | What it does |
|---|---|
| **Home** (`/`, the app icon) | Counts of scripts, running, successful and failed executions; recent executions; **Quick run** and most-used scripts. |
| **Script Library** (`/scripts/`) | Every script as a dense table (or cards). Search (`/` focuses it), filter by **category, platform and language**. |
| **Open folder** (`/folder/`) | Fallback when the system folder dialog is not available (browser on another machine): type a path or click through the folder list; favourites (star) and recent folders. The explorer's folder name is a quick switcher with the same lists. |
| **Script pages** | The centre is the same on Overview, Parameters and Run: the script (an editor with syntax colours, line numbers, auto-indent, or read-only when editing is off; its metadata block is at the top) with copy, download, **Restore previous** and a full-screen editor, a tab for each run started there, and under it the collapsible **Saved values** panel (**Ctrl+J**). The **Overview / Parameters / Run** tabs head the right column, so switching only changes that column. A script page has **one Save** (centre tab bar, **Ctrl+S** anywhere): it saves whatever changed — the script, the Overview settings, the parameter definitions (editing the metadata block in the script and a form together is refused, as they are the same settings); a dot marks unsaved changes. |
| **Script › Overview** | The script's settings on the right: name, category, description, interpreter, default host, working directory, environment, platforms; below, recent runs. (The old `/configure/` URL opens this page.) |
| **Script › Parameters** | The **parameter definitions** the run form is built from (click a row to edit; hover to see where the script uses it). |
| **Script › Run** | On the right: where the values come from (defaults, a recent run or a preset), the host and the parameters (focusing one marks the lines that use it), and *+ Save current values as preset…* at the end of the values list. **Run** (Ctrl+Enter) in the bottom bar, with the mode **Normal · Trace · Debug** next to it, starts the script straight away (unsaved script edits are saved first) and opens the run as a tab next to the script; **Reset** loads the defaults. |
| **New script** (`/scripts/new/`, **+** in the explorer header) | Name, language, optional sub-folder and category; creates the file (with a short starter) and its metadata in the open folder, then opens it in the editor. |
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
| `g n` `g f` | New script, Open folder (explorer header) |
| `g n` | New script (in the open folder) |
| `i` (or `c`) `r` | On a script: Overview (configuration), Run (run / view / edit) |
| `Ctrl+Enter` | Run (Run tab), Create (New script) |
| `Ctrl+Alt+B` `Ctrl+J` | Hide/show the right panel, the saved-values panel (script pages) |
| `F5` `F10`/`n` `F6` `Shift+F5` | Debugger: continue, step, pause, stop |


---

## Configuration

All settings live in `runtime/config.json` (created on first save). Edit them in
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

---

## Script folder

ScriptHub works on **one folder at a time**, like an editor's *Open Folder*. It is
not a setting: on first start the Overview asks you to open one (or the bundled
`./scripts` examples), and the folder you open is reopened on the next start.

* **Open folder…** (explorer folder menu, Overview, Ctrl+K) shows the system
  folder dialog and opens the folder you pick. The dialog appears on the machine
  running ScriptHub, so it is offered in the desktop app and in a browser on that
  same machine. From another machine, the `/folder/` page is used instead: type a
  path or click through the drives and sub-folders.
* A large folder (a whole drive, say) is scanned in the background: the app stays
  usable, the status bar shows the folders and scripts counted so far, and the
  Overview / Library reload when the scan is done.
* **Recent** keeps the last 10 folders; **favourites** (star) stay until you
  unstar them. Both are in the explorer's folder menu and the command palette
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
# {
#   "name": "Database Backup",
#   "category": "Database",
#   "parameters": [
#     {"name": "database", "type": "text", "required": true},
#     {"name": "backup_path", "type": "directory", "required": true},
#     {"name": "compress", "type": "boolean", "default": true},
#     {"name": "retention_days", "type": "number", "default": 30, "min": 1, "max": 365}
#   ]
# }
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

See `scripts/system/disk_usage.sh`.

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
itself**: a JSON object in a comment block between `# --- scripthub ---` and
`# --- end scripthub ---` (`:: --- scripthub ---` in `.bat` / `.cmd` files), at the
top of the file, after a `#!` line, a Python encoding line or `@echo off`. One file
to copy, move or commit. The Overview and Parameters tabs write the block for you;
you can also edit it in the script. Scripts from older versions may still have a
`<stem>.json` next to them: it is read until the first save, which moves it into the
script. If the script cannot be written, the metadata goes to
`runtime/metadata_overrides/<script id>.json` instead.

| Field | Type | Meaning |
|---|---|---|
| `name` | text | Display name. |
| `description` | text | Shown on cards and details. |
| `category` | text | Groups scripts; default is the first sub-folder name. |
| `interpreter` | text | Overrides the interpreter (a command or full path) on every host. |
| `host` | `local` or an SSH alias | Default host (can be changed on the run form). |
| `working_directory` | path | Working directory for the run. Default: the script's folder (local) or the login directory (SSH). |
| `environment` | object | Extra environment variables `{"NAME": "value"}`. |
| `platforms` | list | Any of `windows`, `linux`, `macos`; used by the Library's Platform filter. Defaults come from the file type (`.bat` → Windows, `.sh` → Linux/macOS, `.py` → all). |
| `parameters` | list | See below. |

Unknown fields are kept untouched, so metadata can grow without breaking. The
old `enabled`, `timeout` and `allow_concurrent` fields are ignored and dropped
the next time the metadata is saved.

When you save on the Overview or Parameters tab the metadata is **validated
first**; invalid data is never written and every problem is listed. Only the
block is rewritten (line endings and a BOM are kept), and the previous version of
the script is kept as `<name>.bak` (**Restore previous** in the editor).

---

## Defining parameters

```json
{
  "name": "retention_days",
  "label": "Retention Days",
  "type": "number",
  "default": 30,
  "required": true,
  "min": 1,
  "max": 365,
  "help": "Delete backups older than this",
  "editable_at_runtime": true
}
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
* A `password` is passed like any other value (use `"pass_as": "env"` to keep it
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

The script id comes from the script's path, so moving or renaming a script starts
it without saved values. The files use a small YAML subset (written by ScriptHub,
fine to edit by hand).

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

---

## Creating and editing scripts

### New script

**New script** (the **+** in the explorer header, `g n`, or Ctrl+K → *New script*)
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

* **A backup is always kept** as `<file>.bak`. **Restore previous** swaps the file
  with it (and restoring is itself undoable).
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
* **History** searches IDs, script names, parameter values and error messages,
  filters by script, status and date, and lets you open, download or delete runs.
* **Retention** (Settings): executions older than `history_retention_days` are
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
  `"pass_as": "env"` where a script supports it.

---

## Bundled scripts

Examples you can run, copy and adapt (all editable on their **Overview**):

| Script | Language | What it does |
|---|---|---|
| **System Information** | Python | Hostname, OS, Python version, time. |
| **Directory Backup** | Python | ZIP a folder, optionally compressed. |
| **Directory Cleanup** | Python | Delete files older than N days. **Dry run** until you type `DELETE`. Refuses roots and home folders. |
| **Disk Usage** | Bash | Largest entries in a folder plus free space. |
| **HTTP Health Check** | Bash | Check a URL (positional argument + environment variable). Fails if unreachable. |
| **Ping Host** | Batch | Ping with positional arguments (Windows). |
| **List Directory** | Batch | Positional argument + environment variable (Windows). |
| **Django: Deploy** | Bash | `git pull` → install requirements (online **or offline**) → `check` → `migrate` → `collectstatic` → restart a systemd service. Stops at the first failing step. |
| **Django: Health Check** | Bash | `check --deploy`, pending-migration check, service status, optional URL probe, pass/fail summary. |
| **Django: Run Management Command** | Bash | One of a fixed list (`check`, `showmigrations`, `migrate`, `collectstatic`, `createcachetable`, `clearsessions`); extra arguments restricted to simple tokens. |
| **Service: Status / Restart** | Bash | `systemctl` status/start/stop/restart/reload, or recent journal log. |
| **Database Backup** | Bash | SQLite (`.backup`) or PostgreSQL (`pg_dump`) to a timestamped file, keeping the newest N. The password travels as `PGPASSWORD`. |
| **Pip: Download Wheelhouse** | Python | Download requirements + dependencies for **offline** installation, optionally for another OS/Python, with a checksum manifest and optional ZIP. |
| **Pip: Offline Install** | Python | Install from a wheelhouse with no network: verifies checksums, creates the virtualenv, runs `pip check`. |
| **Pip: Freeze Requirements** | Python | Write the exact versions of an environment to a requirements file. |

The Django and database scripts are Bash for Linux servers; run them on a host
over SSH (or locally under WSL / Git Bash).

---

## Offline installation of pip packages (walkthrough)

Goal: install a Django app's packages on a server **that has no internet**, while
you prepare them from a computer that does (for example this Windows machine).

**1. Pin the versions** (on a working environment):
*Pip: Freeze Requirements* → set the interpreter and *Save to file*, producing
`requirements.lock.txt`.

**2. Download a wheelhouse** (on the connected computer):
*Pip: Download Wheelhouse*

| Field | Example |
|---|---|
| Requirements file | `C:\work\myapp\requirements.lock.txt` |
| Wheelhouse folder | `C:\work\wheelhouse` |
| Target platform | **Linux x86-64** (the server's OS) |
| Target Python version | `3.11` (the server's Python) |
| Also create a .zip | ✔ |

Because the target differs from this machine, only **binary wheels** can be
fetched; if a package ships only source code the download fails and says so. A
`wheelhouse-manifest.txt` with SHA-256 checksums is written.

**3. Transfer** `wheelhouse.zip` (USB, `scp`, shared drive) and unzip it on the
server, e.g. to `/srv/wheelhouse`.

**4. Install on the server** (choose the server as *Run on*):
*Pip: Offline Install* with the wheelhouse folder, the requirements file and a
virtualenv path such as `/srv/myapp/venv`. Tick **Dry run** first to confirm
everything is available. The script verifies the checksums, creates the
virtualenv if needed, installs with `--no-index` (it cannot reach the network),
and runs `pip check`.

**5. Deploy** with *Django: Deploy*, filling in **Offline wheelhouse**
(`/srv/wheelhouse`) so even updates never touch the internet.

Tips: keep one wheelhouse per Python version and platform; re-run step 2 whenever
`requirements` change.

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
  Saves are atomic with a `.bak`, only catalogued files are writable, and
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
history and retention, and the bundled scripts (including an actual offline pip
install into a fresh virtualenv). Tests that need Bash or Windows skip
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
│   │   folder_views.py                                    thin HTTP layer
│   ├── workspace.py          open folder, recent and favourite folders
│   ├── script_editor.py      safe saving of script files (backup, conflicts, line endings)
│   ├── discovery.py          approved-script catalogue
│   ├── metadata.py           the metadata block in the script: read/write, backups
│   ├── parameter_service.py  types, validation, argument building
│   ├── executor.py           builds and supervises one process (local / SSH)
│   ├── process_manager.py    queue, limits, threads, cancel, recovery
│   ├── history.py, history_ops.py   run records, search, retention
│   ├── presets.py (saved parameters), ssh_hosts.py, security.py, validators.py, fsutil.py
│   └── test*.py
├── scripts/                  bundled examples (system, django, python_packages, …)
├── runtime/                  config.json, executions/, presets/, metadata_overrides/
├── templates/, static/       Django templates, custom CSS, vanilla JavaScript
└── tools/test_highlight.js
```

`runtime/` is created and managed by the app and is ignored by git. (`runtime/locks/`
and `runtime/sessions/` are reserved folders that the current design does not use:
there are no sessions, and the queue lives in memory.)

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| A script is missing from the library | Its extension is not under **Interpreters**, it is in an unconfigured folder, or press **Refresh**. |
| *Fix metadata* instead of **Run** | The metadata block at the top of the script is not valid JSON; the error names the lines. Fix it in the editor. |
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

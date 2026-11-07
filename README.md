# Script Runner

A small, standalone web app for **discovering, configuring, running and monitoring
Python, Bash and batch scripts** from a browser, on this computer or on Linux
servers over SSH.

The scripts are the workers. Django is only the management and execution
interface. There is **no database, no Redis, no Celery and no Node.js**:
configuration, presets, history and logs are plain JSON and text files.

> **Built for one trusted user.** There is no login. Anyone who can open the web
> page can run every enabled script. Keep it on `127.0.0.1` (the default) or behind
> your own network restrictions. See [Security](#security).

---

## Contents

1. [Quick start](#quick-start)
2. [A tour of the pages](#a-tour-of-the-pages)
3. [Configuration](#configuration)
4. [Script directories](#script-directories)
5. [Writing scripts](#writing-scripts)
6. [Metadata JSON reference](#metadata-json-reference)
7. [Defining parameters](#defining-parameters)
8. [Running a script, hosts and presets](#running-a-script-hosts-and-presets)
9. [Editing scripts and ad-hoc scripts](#editing-scripts-and-ad-hoc-scripts)
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

---

## Quick start

Requirements: **Python 3.10+** and **Django 5** (nothing else).

```bash
cd ScriptRunner
python -m venv .venv                      # optional but recommended
.venv\Scripts\activate                    # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py runserver --noreload
```

Open <http://127.0.0.1:8000>.

* **No `migrate` is needed**, and no database file is ever created.
* Use `--noreload`. The auto-reloader restarts the process whenever a Python file
  changes, and a restart stops any script that is running.
* To use another port: `python manage.py runserver 8080 --noreload`.

Try it: open **Script Library**, press **Run** on *System Information*, then
**Run now** on the confirmation page. The live console opens.

For SSH hosts, `ssh` must already work for you from a terminal (see
[Running on Linux servers over SSH](#running-on-linux-servers-over-ssh)).

---

## A tour of the pages

| Page | What it does |
|---|---|
| **Dashboard** (`/`) | Counts of scripts, running, successful and failed executions; recent executions; **Quick run** and frequently used scripts. |
| **Script Library** (`/scripts/`) | Every approved script as cards or a compact table. Search (`/` focuses it), filter by **category, platform and language**, enable/disable with a switch. It always fits the window with no scrollbars; a pager shows as many items as fit (←/→ change page). |
| **Script details** | Metadata, parameter table, the script's recent executions. |
| **View script** | Read-only source with syntax highlighting (Python, Bash, Batch, JSON), line numbers, wrap, copy and download. Only catalogued scripts can be opened. |
| **Edit script** | A code editor (syntax colours, line numbers, auto-indent, Tab, **Ctrl+S**) to change a catalogued script and save it. See [Editing scripts and ad-hoc scripts](#editing-scripts-and-ad-hoc-scripts). |
| **Ad-hoc** (`/adhoc/`) | Type or paste a script and run it once, on this computer or over SSH; optionally save it to the library. |
| **Configure** | Name, category, interpreter, timeout, concurrency, default host, working directory, environment, platforms, and the **parameter editor** (table with add / edit / reorder / delete). A **Raw JSON** editor can repair a broken metadata file. |
| **Run** | A form generated from the metadata, a *Run on* host selector and the **Presets** panel. |
| **Confirm** | Review parameters and the exact command (passwords masked), then **Run now**. |
| **Console** (`/executions/RUN-…/`) | Live status, duration, exit code, separate stdout and stderr, auto-scroll, copy, download, stop. |
| **Executions** | What is running, queued and finished recently (refreshes by itself). |
| **History** | Search and filter every past execution, open or download logs, delete runs, apply retention. |
| **Settings** | Script directories, concurrency limits, timeouts, polling, retention, interpreters (local and SSH), SSH command, environment variables, SSH host test. |

---

## Configuration

All settings live in `runtime/config.json` (created on first save). Edit them in
**Settings** or by hand; they are validated before being saved, the previous file
is kept as `config.json.bak`, and changes apply to the next execution without a
restart.

```json
{
  "script_directories": ["./scripts", "/opt/custom_scripts"],
  "interpreters": {".py": "python", ".sh": "bash", ".bat": "cmd"},
  "remote_interpreters": {".py": "python3", ".sh": "bash"},
  "ssh_executable": "ssh",
  "ssh_connect_timeout": 10,
  "environment": {},
  "enable_adhoc": true,
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
| `script_directories` | Folders scanned recursively. Relative paths are relative to this application. |
| `interpreters` | File extension → command used **on this computer**. Only listed extensions are treated as scripts. |
| `remote_interpreters` | Extension → command used **on SSH hosts**. |
| `ssh_executable` | The SSH command (a path or command line). Uses your own `~/.ssh/config`. |
| `ssh_connect_timeout` | Seconds before an SSH connection attempt gives up. |
| `environment` | Extra environment variables for every run (a script's own settings and parameters override them). |
| `enable_adhoc` | Show the Ad-hoc screen (`false` blocks it completely). |
| `enable_script_editing` | Allow editing script files from the browser (`false` blocks editing and restoring). |
| `max_concurrent_executions` | Runs beyond this wait in the queue. |
| `max_concurrent_per_host` | Same limit, applied to this computer and to each SSH host separately. |
| `default_timeout` | Seconds, for scripts that set no timeout of their own. |
| `history_retention_days` | Finished executions older than this are deleted. |
| `log_retention_days` | After this many days only the record stays; stdout/stderr are removed. |
| `polling_interval` | Milliseconds between console polls. |

Environment variables for the web process:

| Variable | Default | Meaning |
|---|---|---|
| `SCRIPT_RUNNER_DEBUG` | `1` | Set `0` for production (hides error pages; static files then need a web server, see [Production deployment](#production-deployment)). |
| `SCRIPT_RUNNER_ALLOWED_HOSTS` | *(localhost only)* | Comma-separated extra host names, e.g. `tools.example.com`. |

---

## Script directories

By default `./scripts` is scanned. Add or remove folders in **Settings → Script
directories**; they are scanned **recursively**, and new files appear without a
restart (press **Refresh** to rescan immediately).

Rules that keep this safe:

* Only extensions listed under **Interpreters** are catalogued. A stray `.exe`,
  `.txt` or `.json` is ignored.
* Hidden folders, `__pycache__`, `venv`, `node_modules` and `site-packages` are skipped.
* Symlinks that point outside their configured folder are skipped.
* The same file reached through two folders is listed once.
* Nothing is executed during discovery.
* Disable a script with its switch (stored in its metadata); a disabled script
  cannot be run.

---

## Writing scripts

A script is an ordinary program. Script Runner starts it with **command-line
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

Matching metadata (`backup.json` next to `backup.py`):

```json
{
  "name": "Database Backup",
  "category": "Database",
  "parameters": [
    {"name": "database", "type": "text", "required": true},
    {"name": "backup_path", "type": "directory", "required": true},
    {"name": "compress", "type": "boolean", "default": true},
    {"name": "retention_days", "type": "number", "default": 30, "min": 1, "max": 365}
  ]
}
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

For batch files Script Runner **rejects** the characters `& | < > ^ % ! "` and a
backtick in any value, because `cmd.exe` would treat them as commands. Add a
`pattern` to the parameter to be even stricter.

### Good behaviour for scripts

* **Exit code 0 means success.** Any other code is shown as *Failed*. Script Runner
  only reports success after the process has really exited with 0.
* Print progress to **stdout**, problems to **stderr**; the console shows them in
  separate panes.
* Flush output if you want to see it live (`print(..., flush=True)`). For Python,
  Script Runner already sets `PYTHONUNBUFFERED=1`.
* Scripts run **without a terminal and without stdin**; do not prompt for input.
* For **SSH** runs the script is read by the remote interpreter from standard
  input, so it cannot use `__file__` or import neighbouring files, and stdin is
  not available to it.

---

## Metadata JSON reference

Metadata is optional. Without it a script is listed with a name made from its
file name, the folder as its category, and no parameters. To add metadata, put a
JSON file with the same name next to the script (`backup.py` → `backup.json`).
If that folder is read-only, the file is stored under
`runtime/metadata_overrides/` instead.

| Field | Type | Meaning |
|---|---|---|
| `name` | text | Display name. |
| `description` | text | Shown on cards and details. |
| `category` | text | Groups scripts; default is the first sub-folder name. |
| `enabled` | true/false | `false` hides the Run button and blocks execution. |
| `interpreter` | text | Overrides the interpreter (a command or full path) on every host. |
| `timeout` | seconds | Stops the run after this long (status *Timed out*). |
| `allow_concurrent` | true/false | `false` = at most one run of this script at a time. |
| `host` | `local` or an SSH alias | Default host (can be changed on the run form). |
| `working_directory` | path | Working directory for the run. Default: the script's folder (local) or the login directory (SSH). |
| `environment` | object | Extra environment variables `{"NAME": "value"}`. |
| `platforms` | list | Any of `windows`, `linux`, `macos`; used by the Library's Platform filter. Defaults come from the file type (`.bat` → Windows, `.sh` → Linux/macOS, `.py` → all). |
| `parameters` | list | See below. |

Unknown fields are kept untouched, so metadata can grow without breaking.

When you save in the Configure page the metadata is **validated first**; invalid
data is never written, every problem is listed, and the last valid file is kept
as `<name>.json.bak`.

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

## Running a script, hosts and presets

1. **Run** opens a form built from the metadata with the defaults filled in.
2. Choose **Run on**: *This computer* or any SSH host.
3. Optionally load a **preset**.
4. **Review & confirm** validates every value and shows the exact command.
5. **Run now** starts it; you land in the live console.

Values are validated for type, range, choices and pattern, on the server.

### Presets

Presets are named sets of values for one script, stored as
`runtime/presets/<script>/<name>.json`, separate from the metadata.

* **Save** with a name (an existing name is updated), **load**, **rename**, **delete**.
* You can change values after loading and run without saving.
* A preset is **re-validated against the current metadata** each time it loads:
  removed parameters are ignored, new ones get defaults, values that are no longer
  valid are reset, and each case shows a warning.
* **Passwords and fixed parameters are not saved in presets.**

---

## Editing scripts and ad-hoc scripts

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
* Only catalogued files inside a configured script directory can be edited; the
  editor never accepts a path from the browser. Files must be UTF-8 text up to
  512 KB; read-only files are refused.

### Ad-hoc scripts

**Ad-hoc** is for one-off jobs: pick a language, type or paste the code, choose
*Run on* (this computer or an SSH host), optionally add **arguments** (one per
line, passed exactly as written), a working directory and a timeout, then **Run now**.

* The code runs exactly as written, started the same safe way as any other script
  (argument list, no shell; batch arguments filtered; SSH streaming over stdin).
* **The code that ran is kept inside that execution's History folder**, together
  with its arguments and who started it, so you can always see what was run.
  **Edit & run again** on the console (or *Recent ad-hoc runs*) reloads it.
* The default working directory is your home folder (never the run folder).
* **Save to the library…** stores the script (and a small metadata file) in a
  script directory you choose so it becomes a normal library entry. It never
  overwrites an existing file, and only accepts simple folder and file names.
* Starter scripts are offered for Python, Shell and Batch; switching language
  swaps the starter only if you have not typed anything yet.

### Sharing with other people

If other people will submit scripts, remember that **Ad-hoc and editing mean
arbitrary code execution** with this application's permissions (and the SSH
accounts' on remote hosts). The application has no login, so:

* put your own authentication in front of it (reverse proxy, VPN, SSO) and run it
  under a dedicated, least-privilege account;
* in **Settings → Features**, switch off **Ad-hoc scripts** and/or **Edit scripts
  in the browser** if people should only run the catalogue (`enable_adhoc` and
  `enable_script_editing` in `config.json`);
* every execution records the **client address** it came from (shown on the
  console and searchable in History). This is the connecting address; it is only
  as meaningful as your network setup: behind a reverse proxy it is the proxy's.

---

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

Script Runner uses your system `ssh` and does **no SSH configuration of its own**.

1. Make sure `ssh myserver` works from a terminal **without a password prompt**
   (key or agent). Host aliases come from `Host` entries in `~/.ssh/config`
   (wildcards are ignored; `Include` files are followed).
2. **Settings → SSH hosts → Test connection** checks the login.
3. Choose the host on the run form (or set a default in **Configure**).

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

Examples you can run, copy and adapt (all editable in **Configure**):

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

Script Runner starts operating-system processes and has **no authentication**.

* It listens on **127.0.0.1** by default. To reach it from elsewhere use an SSH
  tunnel (`ssh -L 8000:localhost:8000 you@host`) or put your own authenticating
  reverse proxy in front. **Never expose it directly to the internet or an
  untrusted network.**
* Only scripts in the configured directories, with a configured extension, can be
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
* **Editing and Ad-hoc let anyone with access run any code.** They can be switched
  off in **Settings → Features**; see [Sharing with other people](#sharing-with-other-people).
  Saves are atomic with a `.bak`, only catalogued files are writable, and
  "Save to library" never overwrites or leaves its script directory.
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

`pip install gunicorn`, then `/etc/systemd/system/script-runner.service`:

```ini
[Unit]
Description=Script Runner
After=network.target

[Service]
User=scriptrunner
WorkingDirectory=/opt/script-runner
Environment=SCRIPT_RUNNER_DEBUG=0
Environment=SCRIPT_RUNNER_ALLOWED_HOSTS=tools.example.com
ExecStart=/opt/script-runner/.venv/bin/gunicorn script_runner_project.wsgi:application \
          --bind 127.0.0.1:8000 --workers 1 --threads 8 --timeout 120
Restart=on-failure
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`systemctl enable --now script-runner`. With `SCRIPT_RUNNER_DEBUG=0` Django does
not serve static files, so let nginx do it:

```nginx
location /static/ { alias /opt/script-runner/static/; }
location /        { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
# add authentication here (auth_basic, SSO, VPN-only listener, ...)
```

### Windows

* Simplest: `python manage.py runserver 127.0.0.1:8000 --noreload --insecure`
  (the `--insecure` flag lets the built-in server deliver static files when
  `SCRIPT_RUNNER_DEBUG=0`). Wrap it as a **service** with
  [NSSM](https://nssm.cc/) or a Task Scheduler task "at startup" with restart on failure.
* Or use `pip install waitress` and
  `waitress-serve --listen=127.0.0.1:8000 --threads=8 script_runner_project.wsgi:application`
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
ScriptRunner/
├── manage.py
├── config.py                 filesystem-backed configuration
├── requirements.txt          Django only
├── runner/
│   ├── views.py, script_views.py, execution_views.py, history_views.py,
│   │   preset_views.py, edit_views.py, adhoc_views.py     thin HTTP layer
│   ├── script_editor.py      safe saving of script files (backup, conflicts, line endings)
│   ├── discovery.py          approved-script catalogue
│   ├── metadata.py           sidecar JSON read/write, backups
│   ├── parameter_service.py  types, validation, argument building
│   ├── executor.py           builds and supervises one process (local / SSH)
│   ├── process_manager.py    queue, limits, threads, cancel, recovery
│   ├── history.py, history_ops.py   run records, search, retention
│   ├── presets.py, ssh_hosts.py, security.py, validators.py, fsutil.py
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
| *Fix metadata* instead of **Run** | The script's JSON is invalid; open **Configure** (the Raw JSON editor shows the error). |
| Status **Failed**, error *SSH connection failed* | Run `ssh <host>` in a terminal: accept the host key, confirm key login works without a password. |
| `Could not start the process` | The interpreter is not installed or not on `PATH`. Set the full path under **Settings → Interpreters**. |
| `.sh` scripts fail on Windows locally | Local `.sh` needs Bash (Git Bash or WSL) on `PATH`; or run them on a Linux host over SSH. |
| Console shows output only at the end | The script buffers output; flush it (`python -u`, `print(flush=True)`, `stdbuf`). |
| Stuck at **Queued** | A concurrency limit is reached, or a non-concurrent script is already running; check **Executions**. |
| **Interrupted** after a restart | The app stopped while the run was active (see [Limitations](#limitations-of-long-lived-subprocesses)). |
| Pages have no styling in production | With `SCRIPT_RUNNER_DEBUG=0`, serve `/static/` from your web server or use `--insecure`. |
| `DisallowedHost` error | Set `SCRIPT_RUNNER_ALLOWED_HOSTS` to the host name you browse to. |

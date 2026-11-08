# Security

Script Runner runs scripts — and, if you allow it, code typed into the browser — with
the permissions of the account that runs it. Please read this before you deploy it.

## The model: one trusted person

There is **no login**. Whoever can open the app can use every feature that is switched
on in *Settings → Features*. That is by design: it is a personal tool.

| Mode | Who can reach it | Safe for |
|---|---|---|
| **Desktop app** (`desktop.pyw`, `manage.py desktop`) | Only the window it opens. It listens on `127.0.0.1` on a random port and rejects every request without a secret created at each launch (other local users, other browser tabs and web pages get *403*). | One person on their own computer. **Recommended.** |
| **`runserver` on `127.0.0.1`** | Anyone who can make requests to `127.0.0.1` on that computer: you, other users signed in to the same machine, and local software. | One person on a single-user computer. |
| **Server deployment** (gunicorn, a service, a reverse proxy) | Whoever can reach the address. | Only behind **your own authentication** (SSO, VPN-only listener, HTTP auth at the proxy) and only for people you would give a shell to. |

Never expose it to an untrusted network without authentication in front of it.

## What it is careful about

* Scripts are started from an **argument list, never through a shell**. Parameters are
  validated by type; batch-file arguments with `cmd.exe` metacharacters are rejected;
  variables such as `PATH` and `LD_PRELOAD` cannot be set from parameters.
* On SSH hosts the remote command is built with `shlex.quote`; the script is streamed
  over stdin, nothing is installed on the host.
* The browser never sends file paths: scripts are referred to by id; files are only read
  inside the configured script directories (symbolic links that leave them are skipped).
* Passwords entered as parameters are masked in the UI and history and are **never
  stored** in saved parameters.
* CSRF protection on every form; the desktop token cookie is `HttpOnly` and `SameSite=Strict`.

## What you should know

* **Ad-hoc scripts and in-browser editing are arbitrary code execution** by design.
  Switch them off in *Settings → Features* if other people can open the app.
* Saved parameters (`*.params.json`) are written next to your scripts and may contain
  host names, paths or other values you consider internal. Review them before you commit
  or share a script folder.
* Run logs (`runtime/executions/`) contain whatever your scripts print.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
*Security → Report a vulnerability* (private advisory) on the project's repository, or
contact the maintainer privately. Include what you did, what happened and the version.
You will get an answer within a few days; fixes are released as soon as they are ready
and credited unless you prefer otherwise.

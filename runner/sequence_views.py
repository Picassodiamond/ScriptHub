"""Sequences: list, edit (steps), run and watch. Storage in runner/sequences.py,
running in runner/sequence_runner.py. Like everywhere else, the browser sends script
ids only, never file paths."""
import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import discovery, presets, sequences, ssh_hosts, workspace
from . import parameter_service as ps
from .execution_views import client_address
from .sequence_runner import live_view, password_params, prepare, read_run, sequencer, step_host, step_raw
from .sequence_runner import list_runs as list_sequence_runs
from .sequence_runner import valid_run_id as valid_sequence_run_id


def _load_or_404(slug):
    if not sequences.valid_slug(slug):
        raise Http404("Unknown sequence")
    try:
        return sequences.load(slug)
    except sequences.SequenceError as exc:      # missing, or a file that cannot be read (listed with its error)
        raise Http404(str(exc)) from exc


def _detail_url(slug):
    return reverse("sequence_detail", args=[slug])


def _next(request, fallback):
    """The page to go back to (a local path only), else ``fallback``."""
    nxt = request.POST.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and "\n" not in nxt:
        return nxt
    return fallback


def _source_label(step, script):
    if step["preset"]:
        if script is not None:
            p = presets.get_preset(script, step["preset"])
            if p is not None:
                return f"Preset: {p.get('name', step['preset'])}", ""
        return f"Preset: {step['preset']}", "not found"
    n = len(step["values"])
    return ("Saved with the step" if n else "Defaults"), ""


# ----------------------------------------------------------------- list ---
def sequence_list(request):
    if request.method == "POST":
        try:
            slug = sequences.create(request.POST.get("name", ""))
        except sequences.SequenceError as exc:
            messages.error(request, str(exc))
            return redirect("sequences")
        except OSError as exc:
            messages.error(request, f"Could not save the sequence: {exc}")
            return redirect("sequences")
        messages.success(request, "Sequence created. Drag scripts from the Scripts section on the left onto the page.")
        return redirect("sequence_detail", slug=slug)
    favs = workspace.sequence_favorites()
    all_seqs = sequences.list_sequences()
    return render(request, "runner/sequences.html", {
        "sequences": all_seqs,
        "favorites": [s for s in all_seqs if s["slug"] in favs and not s["error"]],
        "runs": [live_view(r) for r in list_sequence_runs(limit=15)],
    })


# --------------------------------------------------------------- detail ---
def graph_payload(data, scripts=None) -> dict:
    """The sequence as the page draws it: boxes (with what to show on them), arrows,
    and the password fields to ask for when it runs."""
    if scripts is None:
        scripts = [sequences.resolve(s) for s in data["steps"]]
    steps, passwords = [], []
    for n, (step, script) in enumerate(zip(data["steps"], scripts), start=1):
        label, problem = _source_label(step, script)
        if script is not None and not script.meta_valid:
            problem = "invalid metadata"
        path = str(script.path) if script else step["path"]
        steps.append({
            "id": step["id"], "n": n,
            "script": script.id if script else step["script"],
            "name": script.name if script else (step["name"] or step["script"]),
            "ext": (script.path.suffix if script else Path(path).suffix).lstrip(".").lower(),
            "path": path,
            "host": step_host(step, script) if script else (step["host"] or "default"),
            "source": label,
            "problem": problem or ("" if script else "script not found"),
            "x": step["x"], "y": step["y"], "join": step["join"],
            "incoming": len(sequences.incoming(data, step["id"])),
        })
        if script is not None:
            for p in password_params(script):
                passwords.append({"input": f"pw_{step['id']}_{p['name']}", "label": p["label"], "step": n,
                                  "script": script.name, "required": p.get("required", False)})
    return {"steps": steps, "links": [dict(k) for k in data["links"]], "passwords": passwords}


def sequence_detail(request, slug):
    data = _load_or_404(slug)
    scripts = [sequences.resolve(s) for s in data["steps"]]
    sequences.refresh(slug, data, scripts)
    return render(request, "runner/sequence.html", {
        "seq": data,
        "graph": graph_payload(data, scripts),
        "runs": [live_view(r) for r in list_sequence_runs(slug, limit=10)],
        "token": uuid.uuid4().hex,
        "max_steps": sequences.STEPS_MAX,
    })


@require_POST
def sequence_rename(request, slug):
    _load_or_404(slug)
    try:
        sequences.rename(slug, request.POST.get("name", ""))
        messages.success(request, "Renamed.")
    except sequences.SequenceError as exc:
        messages.error(request, str(exc))
    return redirect(_next(request, _detail_url(slug)))


@require_POST
def sequence_favorite(request, slug):
    """Mark or unmark a favourite (side panel ★): UI state of this machine, not in the file."""
    if not sequences.valid_slug(slug) or not sequences.get(slug):
        raise Http404("Unknown sequence")
    workspace.set_sequence_favorite(slug, request.POST.get("on") == "1")
    return redirect(_next(request, _detail_url(slug)))


@require_POST
def sequence_delete(request, slug):
    """Also deletes a file that cannot be read (the list offers that)."""
    if not sequences.valid_slug(slug):
        raise Http404("Unknown sequence")
    data = sequences.get(slug)
    if not sequences.delete(slug):
        raise Http404("Unknown sequence")
    name = data["name"] if data else slug
    workspace.set_sequence_favorite(slug, False)
    messages.success(request, f"Sequence '{name}' deleted (its past runs stay in History).")
    nxt = _next(request, reverse("sequences"))
    if nxt.startswith(_detail_url(slug)):            # the page of the deleted sequence is gone
        nxt = reverse("sequences")
    return redirect(nxt)


def _new_step_for(script, x, y) -> dict:
    """A step for ``script``, with the values of its last run (passwords never)."""
    recent = presets.get_recent(script, 0) if script.meta_valid else None
    params = script.meta.get("parameters", []) if script.meta_valid else []
    values = presets.savable_values(params, (recent or {}).get("values") or {})
    return sequences.new_step(script, values, x=x, y=y)


def sequence_graph(request, slug):
    """The page's editor: GET = the graph (JSON); POST (JSON body) = one change, answered
    with the graph after it. ``op``:

      add     {script, x, y}        a script of the open folder, by id   -> also returns ``id``
      move    {positions: {id: [x, y], ...}}
      remove  {id}
      link    {from, to, when}      adds the arrow, or changes its condition
      unlink  {from, to}
    """
    data = _load_or_404(slug)
    if request.method == "GET":
        return JsonResponse({"graph": graph_payload(data)})
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
        if not isinstance(body, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    op, out = body.get("op"), {}
    try:
        if op == "add":
            script = discovery.get_script(str(body.get("script") or ""))
            if script is None:
                raise sequences.SequenceError("That script is not in the open folder (rescan, then try again).")
            out["id"] = sequences.add_step(slug, _new_step_for(script, body.get("x"), body.get("y")))
        elif op == "move":
            positions = body.get("positions")
            if not isinstance(positions, dict):
                raise sequences.SequenceError("Invalid positions.")
            clean = {}
            for k, v in positions.items():
                if not (isinstance(v, (list, tuple)) and len(v) == 2):
                    raise sequences.SequenceError("Invalid positions.")
                clean[str(k)] = (v[0], v[1])
            sequences.move_steps(slug, clean)
        elif op == "remove":
            sequences.remove_step(slug, str(body.get("id") or ""))
        elif op == "link":
            sequences.link(slug, str(body.get("from") or ""), str(body.get("to") or ""),
                           str(body.get("when") or "success"))
        elif op == "unlink":
            sequences.unlink(slug, str(body.get("from") or ""), str(body.get("to") or ""))
        else:
            return JsonResponse({"error": "Unknown operation."}, status=400)
    except sequences.SequenceError as exc:
        return JsonResponse({"error": str(exc), "graph": graph_payload(sequences.load(slug))}, status=400)
    except OSError as exc:
        return JsonResponse({"error": f"Could not save the sequence: {exc}"}, status=500)
    out["graph"] = graph_payload(sequences.load(slug))
    return JsonResponse(out)


# ----------------------------------------------------------------- step ---
def _step_url(slug, step_id, embed, **extra):
    url = reverse("sequence_step", args=[slug, step_id])
    query = {**({"embed": "1"} if embed else {}), **extra}
    return url + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")


def _step_page(request, data, step_id, script, embed, *, raw=None, errors=None, source=None, host=None,
               join=None, status=200):
    i = sequences.find(data, step_id)
    step = data["steps"][i]
    ctx = {"seq": data, "step": step, "n": i + 1, "script": script, "embed": embed,
           "base": "base_embed.html" if embed else "base.html",
           "incoming": len(sequences.incoming(data, step_id)),
           "saved": embed and request.GET.get("saved") == "1"}
    if script is not None and script.meta_valid:
        params = [p for p in script.meta.get("parameters", []) if p["type"] != "password"]
        warnings = []
        if raw is None:
            raw, warnings, err = step_raw(step, script)
            if err:
                warnings.append(f"Values: {err.capitalize()}; the defaults are shown.")
        default_host = script.meta.get("host", ssh_hosts.LOCAL)
        ctx.update(
            fields=ps.build_fields(params, raw, errors or {}),
            has_errors=bool(errors),
            warnings=warnings,
            presets=presets.list_presets(script),
            source=source if source is not None else (step["preset"] or "values"),
            hosts=ssh_hosts.allowed_hosts([default_host, step["host"]]),
            default_host=default_host,
            host=host if host is not None else step["host"],
            join=join or step["join"],
            has_password=any(p["type"] == "password" for p in script.meta.get("parameters", [])),
        )
    return render(request, "runner/sequence_step.html", ctx, status=status)


def sequence_step(request, slug, step_id):
    """A step's values, host and join, as a page or (``embed=1``) in the sequence page's
    right-hand column."""
    data = _load_or_404(slug)
    try:
        step = data["steps"][sequences.find(data, step_id)]
    except sequences.SequenceError:
        raise Http404("Unknown step") from None
    embed = (request.GET.get("embed") or request.POST.get("embed")) == "1"
    script = sequences.resolve(step)
    if request.method != "POST":
        return _step_page(request, data, step_id, script, embed)
    if script is None or not script.meta_valid:
        messages.error(request, "This step's script cannot be configured (not found or invalid metadata).")
        return redirect(_step_url(slug, step_id, embed))
    params = [p for p in script.meta.get("parameters", []) if p["type"] != "password"]
    source = request.POST.get("source", "values")
    host = request.POST.get("host", "")
    join = request.POST.get("join", step["join"])
    if join not in sequences.JOIN:
        join = "all"
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    if host and host not in ssh_hosts.allowed_hosts([default_host, step["host"]]):
        messages.error(request, "Choose one of the listed hosts.")
        return _step_page(request, data, step_id, script, embed, source=source, join=join, status=400)
    try:
        if source == "values":
            raw = ps.raw_from_post(params, request.POST)
            _values, errors = ps.clean_values(params, raw)
            if errors:
                return _step_page(request, data, step_id, script, embed, raw=raw, errors=errors, source=source,
                                  host=host, join=join, status=400)
            sequences.update_step(slug, step_id, preset="", values=presets.savable_values(params, raw),
                                  host=host, join=join)
        else:
            if source not in {p["slug"] for p in presets.list_presets(script)}:
                messages.error(request, "That preset was not found.")
                return _step_page(request, data, step_id, script, embed, host=host, join=join, status=400)
            sequences.update_step(slug, step_id, preset=source, values={}, host=host, join=join)
    except sequences.SequenceError as exc:
        messages.error(request, str(exc))
        return redirect("sequence_detail", slug=slug)
    messages.success(request, f"Step {sequences.find(data, step_id) + 1} saved.")
    if embed:
        return redirect(_step_url(slug, step_id, True, saved="1"))
    return redirect("sequence_detail", slug=slug)


# ------------------------------------------------------------------ run ---
@require_POST
def sequence_start(request, slug):
    data = _load_or_404(slug)
    passwords = {}
    for key, value in request.POST.items():
        if key.startswith("pw_"):
            step_id, _, name = key[3:].partition("_")
            if step_id and name:
                passwords[(step_id, name)] = value
    items, errors = prepare(data, passwords)
    if errors:
        for e in errors[:8]:
            messages.error(request, e)
        if len(errors) > 8:
            messages.error(request, f"… and {len(errors) - 8} more problems.")
        return redirect("sequence_detail", slug=slug)
    rid = sequencer.start(data, items, initiator=client_address(request),
                          token=request.POST.get("submit_token") or None)
    return redirect("sequence_run", run_id=rid)


def _run_or_404(run_id):
    if not valid_sequence_run_id(run_id):
        raise Http404("Unknown sequence run")
    data = read_run(run_id)
    if data is None:
        raise Http404("Unknown sequence run")
    return live_view(data)


def run_graph(data) -> dict:
    """A sequence run for the page's diagram: boxes with their live status, and the arrows."""
    return {
        "steps": [{"id": s["id"], "n": s["index"], "script": s.get("script_id") or "",
                   "name": s.get("script_name") or "", "ext": s.get("ext") or "",
                   "path": s.get("script_path") or "", "host": s.get("host") or "", "x": s["x"], "y": s["y"],
                   "join": s.get("join", "all"), "status": s["status"], "status_label": s["status_label"],
                   "run_id": s.get("run_id"), "error": s.get("error")} for s in data["steps"]],
        "links": data.get("links", []),
    }


def sequence_run(request, run_id):
    data = _run_or_404(run_id)
    return render(request, "runner/sequence_run.html", {
        "run": data,
        "graph": run_graph(data),
        "seq_exists": sequences.get(data.get("sequence", "")) is not None,
    })


def sequence_run_status(request, run_id):
    data = _run_or_404(run_id)
    return JsonResponse({
        "id": data["id"], "status": data["status"], "status_label": data["status_label"],
        "active": data["active"], "error": data.get("error"), "duration": data["duration"],
        "done": data["done"], "total": len(data["steps"]),
        "graph": run_graph(data),
    })


@require_POST
def sequence_run_stop(request, run_id):
    _run_or_404(run_id)
    if sequencer.cancel(run_id):
        messages.success(request, "Stopping the sequence: the running steps are stopped, the rest are skipped.")
    else:
        messages.error(request, "This sequence is not running.")
    if request.POST.get("from") == "history":
        return redirect("history")
    return redirect("sequence_run", run_id=run_id)

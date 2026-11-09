"""Sequences: list, edit (steps), run and watch. Storage in runner/sequences.py,
running in runner/sequence_runner.py. Like everywhere else, the browser sends script
ids only, never file paths."""
import uuid

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import discovery, presets, sequences, ssh_hosts
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


def _index(value, data):
    try:
        i = int(value)
    except (TypeError, ValueError):
        raise Http404("Unknown step") from None
    if not 0 <= i < len(data["steps"]):
        raise Http404("Unknown step")
    return i


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
        messages.success(request, "Sequence created. Add the scripts to run, in order.")
        return redirect("sequence_detail", slug=slug)
    return render(request, "runner/sequences.html", {
        "sequences": sequences.list_sequences(),
        "runs": [live_view(r) for r in list_sequence_runs(limit=15)],
    })


# --------------------------------------------------------------- detail ---
def sequence_detail(request, slug):
    data = _load_or_404(slug)
    scripts = [sequences.resolve(s) for s in data["steps"]]
    sequences.refresh(slug, data, scripts)
    steps, passwords = [], []
    for i, (step, script) in enumerate(zip(data["steps"], scripts)):
        label, problem = _source_label(step, script)
        if script is not None and not script.meta_valid:
            problem = "invalid metadata"
        steps.append({
            "index": i, "n": i + 1, "step": step, "script": script,
            "name": script.name if script else (step["name"] or step["script"]),
            "path": str(script.path) if script else step["path"],
            "host": step_host(step, script) if script else (step["host"] or "default"),
            "source": label, "problem": problem or ("" if script else "script not found"),
        })
        if script is not None:
            for p in password_params(script):
                passwords.append({"input": f"pw_{i}_{p['name']}", "label": p["label"], "step": i + 1,
                                  "script": script.name, "required": p.get("required", False)})
    available, _problems = discovery.scan()
    return render(request, "runner/sequence.html", {
        "seq": data,
        "steps": steps,
        "passwords": passwords,
        "available": sorted(available, key=lambda s: s.relative.lower()),
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
    return redirect("sequence_detail", slug=slug)


@require_POST
def sequence_delete(request, slug):
    """Also deletes a file that cannot be read (the list offers that)."""
    if not sequences.valid_slug(slug):
        raise Http404("Unknown sequence")
    data = sequences.get(slug)
    if not sequences.delete(slug):
        raise Http404("Unknown sequence")
    name = data["name"] if data else slug
    messages.success(request, f"Sequence '{name}' deleted (its past runs stay in History).")
    return redirect("sequences")


@require_POST
def sequence_steps(request, slug):
    """Add, move or remove a step: ``op`` = add (with ``script_id``) | up | down | remove (with ``index``)."""
    data = _load_or_404(slug)
    op = request.POST.get("op", "")
    try:
        if op == "add":
            script = discovery.get_script(request.POST.get("script_id", ""))
            if script is None:
                messages.error(request, "Pick a script from the list.")
                return redirect("sequence_detail", slug=slug)
            recent = presets.get_recent(script, 0) if script.meta_valid else None
            params = script.meta.get("parameters", []) if script.meta_valid else []
            values = presets.savable_values(params, (recent or {}).get("values") or {})
            i = sequences.add_step(slug, sequences.new_step(script, values))
            messages.success(request, f"Added {script.name}" + (" with the values of its last run." if recent else "."))
            return redirect("sequence_step", slug=slug, index=i)
        i = _index(request.POST.get("index"), data)
        if op in ("up", "down"):
            sequences.move_step(slug, i, -1 if op == "up" else 1)
        elif op == "remove":
            sequences.remove_step(slug, i)
            messages.success(request, f"Step {i + 1} removed.")
        else:
            raise Http404("Unknown operation")
    except sequences.SequenceError as exc:
        messages.error(request, str(exc))
    return redirect("sequence_detail", slug=slug)


# ----------------------------------------------------------------- step ---
def _step_page(request, data, i, script, *, raw=None, errors=None, source=None, host=None, on_failure=None,
               status=200):
    step = data["steps"][i]
    ctx = {"seq": data, "index": i, "n": i + 1, "step": step, "script": script}
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
            on_failure=on_failure or step["on_failure"],
            has_password=any(p["type"] == "password" for p in script.meta.get("parameters", [])),
        )
    return render(request, "runner/sequence_step.html", ctx, status=status)


def sequence_step(request, slug, index):
    data = _load_or_404(slug)
    i = _index(index, data)
    step = data["steps"][i]
    script = sequences.resolve(step)
    if request.method != "POST":
        return _step_page(request, data, i, script)
    if script is None or not script.meta_valid:
        messages.error(request, "This step's script cannot be configured (not found or invalid metadata).")
        return redirect("sequence_detail", slug=slug)
    params = [p for p in script.meta.get("parameters", []) if p["type"] != "password"]
    source = request.POST.get("source", "values")
    host = request.POST.get("host", "")
    on_failure = request.POST.get("on_failure", "stop")
    if on_failure not in sequences.ON_FAILURE:
        on_failure = "stop"
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    if host and host not in ssh_hosts.allowed_hosts([default_host, step["host"]]):
        messages.error(request, "Choose one of the listed hosts.")
        return _step_page(request, data, i, script, source=source, on_failure=on_failure, status=400)
    try:
        if source == "values":
            raw = ps.raw_from_post(params, request.POST)
            _values, errors = ps.clean_values(params, raw)
            if errors:
                return _step_page(request, data, i, script, raw=raw, errors=errors, source=source, host=host,
                                  on_failure=on_failure, status=400)
            sequences.update_step(slug, i, preset="", values=presets.savable_values(params, raw),
                                  host=host, on_failure=on_failure)
        else:
            if source not in {p["slug"] for p in presets.list_presets(script)}:
                messages.error(request, "That preset was not found.")
                return _step_page(request, data, i, script, host=host, on_failure=on_failure, status=400)
            sequences.update_step(slug, i, preset=source, values={}, host=host, on_failure=on_failure)
    except sequences.SequenceError as exc:
        messages.error(request, str(exc))
        return redirect("sequence_detail", slug=slug)
    messages.success(request, f"Step {i + 1} saved.")
    return redirect("sequence_detail", slug=slug)


# ------------------------------------------------------------------ run ---
@require_POST
def sequence_start(request, slug):
    data = _load_or_404(slug)
    passwords = {}
    for key, value in request.POST.items():
        if key.startswith("pw_"):
            index, _, name = key[3:].partition("_")
            if index.isdigit() and name:
                passwords[(int(index), name)] = value
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


def sequence_run(request, run_id):
    data = _run_or_404(run_id)
    return render(request, "runner/sequence_run.html", {
        "run": data,
        "seq_exists": sequences.get(data.get("sequence", "")) is not None,
    })


def sequence_run_status(request, run_id):
    data = _run_or_404(run_id)
    return JsonResponse({
        "id": data["id"], "status": data["status"], "status_label": data["status_label"],
        "active": data["active"], "error": data.get("error"), "duration": data["duration"],
        "done": data["done"],
        "steps": [{"index": s["index"], "status": s["status"], "status_label": s["status_label"],
                   "run_id": s.get("run_id"), "error": s.get("error")} for s in data["steps"]],
    })


@require_POST
def sequence_run_stop(request, run_id):
    _run_or_404(run_id)
    if sequencer.cancel(run_id):
        messages.success(request, "Stopping the sequence: the current step is stopped, the rest are skipped.")
    else:
        messages.error(request, "This sequence is not running.")
    return redirect("sequence_run", run_id=run_id)

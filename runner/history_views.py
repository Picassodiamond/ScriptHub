"""History page (Runs): what is running now, then every execution (script runs and sequence
runs, newest first) with search, filters, paging, delete and retention."""
import urllib.parse

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import history, history_ops, sequence_runner, sequences
from .execution_views import _decorate, active_runs

PER_PAGE = 20


KINDS = ("scripts", "sequences")
SEQ = "seq:"                 # the Script filter's value for a sequence: seq:<slug>


def _filters(source):
    return {
        "q": source.get("q", "").strip(),
        "kind": source.get("kind", "").strip(),
        "script": source.get("script", "").strip(),
        "status": source.get("status", "").strip(),
        "date_from": source.get("date_from", "").strip(),
        "date_to": source.get("date_to", "").strip(),
    }


def _when(data) -> str:
    """Sort key (ISO text): when a run started, else when it was queued."""
    return data.get("started_at") or data.get("queued_at") or data.get("finished_at") or ""


def _seq_haystack(data) -> str:
    parts = [data.get("id", ""), data.get("name", ""), data.get("sequence", ""), data.get("status", ""),
             history.STATUS_LABELS.get(data.get("status"), ""), data.get("error") or "", data.get("initiator") or "",
             "sequence"]
    parts += [s.get("script_name") or "" for s in data.get("steps", [])]
    return " ".join(str(p) for p in parts).lower()


def _sequence_runs(slug, q, status, date_from, date_to) -> list:
    out = []
    for data in sequence_runner.list_runs(slug or None):
        if status and data.get("status") != status:
            continue
        if date_from or date_to:
            when = sequence_runner.reference_time(data)
            if when is None or (date_from and when.date() < date_from) or (date_to and when.date() > date_to):
                continue
        if q and q.lower() not in _seq_haystack(data):
            continue
        out.append(data)
    return out


def _sequence_choices() -> list:
    """[(seq:<slug>, name)] for the sequences that exist or have runs."""
    seen = {s["slug"]: s["name"] for s in sequences.list_sequences() if not s["error"]}
    for data in sequence_runner.list_runs():
        seen.setdefault(data.get("sequence") or "", data.get("name") or data.get("sequence") or "")
    return sorted(((SEQ + k, v) for k, v in seen.items() if k), key=lambda kv: kv[1].lower())


def _matching(f, date_from, date_to, status) -> list:
    """Every matching run, newest first: ('script', meta) and ('sequence', record)."""
    seq_slug = f["script"][len(SEQ):] if f["script"].startswith(SEQ) else ""
    script_id = "" if seq_slug else f["script"]
    rows = []
    if f["kind"] != "sequences":
        runs, _total = history_ops.query_runs(f["q"], script_id, status, date_from, date_to, 1, 10 ** 9)
        if seq_slug:                                   # a sequence picked: its step runs too
            mine = {d.get("id") for d in sequence_runner.list_runs(seq_slug)}
            runs = [m for m in runs if (m.get("sequence") or {}).get("run") in mine]
        rows += [("script", m) for m in runs]
    if f["kind"] != "scripts" and not script_id:
        rows += [("sequence", d) for d in _sequence_runs(seq_slug, f["q"], status, date_from, date_to)]
    rows.sort(key=lambda r: (_when(r[1]), r[1].get("id", "")), reverse=True)
    return rows


def _row(kind, data) -> dict:
    if kind == "sequence":
        return sequence_runner.live_view(data) | {"kind": "sequence"}
    seq = data.get("sequence")
    return _decorate(data) | {"kind": "script", "active": history_ops.is_active(data),
                              "logs_deleted": bool(data.get("logs_deleted")),
                              "seq": seq if isinstance(seq, dict) and seq.get("run") else None,
                              "started_display": (data.get("started_at") or data.get("queued_at") or "").replace("T", " ")}


def running_panel() -> dict:
    """Context of the Running panel (_active_runs.html): running script runs and sequences."""
    active, seqs = active_runs(), sequence_runner.active_runs()
    return {"active": active, "active_seqs": seqs, "active_total": len(active) + len(seqs)}


def history_list(request):
    history_ops.maybe_apply_retention()
    f = _filters(request.GET)
    if f["kind"] not in KINDS:
        f["kind"] = ""
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    date_from, date_to = history_ops.parse_date(f["date_from"]), history_ops.parse_date(f["date_to"])
    status = f["status"] if f["status"] in history.ALL_STATUSES else ""
    rows = _matching(f, date_from, date_to, status)
    total = len(rows)
    pages = max(1, -(-total // PER_PAGE))
    page = min(page, pages)
    shown = rows[(page - 1) * PER_PAGE: page * PER_PAGE]

    keep = {k: v for k, v in f.items() if v}
    base = urllib.parse.urlencode(keep)
    cfg = config.load_config()
    return render(request, "runner/history.html", {
        "runs": [_row(kind, data) for kind, data in shown],
        "f": f,
        "scripts": history_ops.script_choices(),
        "sequence_choices": _sequence_choices(),
        "statuses": [(s, history.STATUS_LABELS[s]) for s in history.ALL_STATUSES],
        "total": total,
        "page": page,
        "pages": pages,
        "prev_url": f"?{base}&page={page - 1}" if page > 1 else "",
        "next_url": f"?{base}&page={page + 1}" if page < pages else "",
        "first": (page - 1) * PER_PAGE + 1 if total else 0,
        "last": min(page * PER_PAGE, total),
        "filtered": bool(keep),
        "history_days": cfg["history_retention_days"],
        "log_days": cfg["log_retention_days"],
        "return_query": urllib.parse.urlencode({**keep, "page": page}),
        **running_panel(),                           # the Running panel on top (Jobs merged in)
    })


def _back(request):
    q = request.POST.get("return_query", "")
    if q and all(c.isalnum() or c in "=&-_.%+" for c in q):
        return redirect(f"/history/?{q}")
    return redirect("history")


@require_POST
def history_delete(request, run_id):
    if not history.valid_run_id(run_id) or history.read_meta(run_id) is None:
        raise Http404("Unknown execution")
    if history_ops.delete_run(run_id):
        messages.success(request, f"Deleted {run_id}.")
    else:
        messages.error(request, f"{run_id} is still queued or running; stop it first.")
    if request.POST.get("from") == "console":
        return redirect("history")
    return _back(request)


@require_POST
def history_delete_sequence(request, run_id):
    """Delete a sequence run's record; its step runs stay in History as normal runs."""
    if not sequence_runner.valid_run_id(run_id) or sequence_runner.read_run(run_id) is None:
        raise Http404("Unknown sequence run")
    if sequence_runner.delete_run(run_id):
        messages.success(request, f"Deleted {run_id} (its step runs stay in History).")
    else:
        messages.error(request, f"{run_id} is still running; stop it first.")
    return _back(request)


@require_POST
def history_delete_old(request):
    try:
        days = int(request.POST.get("days", ""))
        if not 0 <= days <= 36500:
            raise ValueError
    except ValueError:
        messages.error(request, "Enter a number of days between 0 and 36500.")
        return redirect("history")
    removed = history_ops.delete_older_than(days)
    seqs = sequence_runner.delete_older_than(days)
    extra = f" and {seqs} sequence run{'s' if seqs != 1 else ''}" if seqs else ""
    messages.success(request, f"Deleted {removed} execution{'s' if removed != 1 else ''}{extra} "
                              f"older than {days} day{'s' if days != 1 else ''}.")
    return redirect("history")


@require_POST
def history_apply_retention(request):
    result = history_ops.apply_retention()
    messages.success(
        request,
        f"Retention applied: {result['runs_removed']} execution(s) and {result.get('sequences_removed', 0)} sequence "
        f"run(s) removed, logs cleared on {result['logs_removed']} older execution(s).")
    return redirect("history")

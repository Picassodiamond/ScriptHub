"""History page (Runs): what is running now, then every execution with search, filters,
paging, delete and retention."""
import urllib.parse

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import history, history_ops
from .execution_views import _decorate, active_runs

PER_PAGE = 20


def _filters(source):
    return {
        "q": source.get("q", "").strip(),
        "script": source.get("script", "").strip(),
        "status": source.get("status", "").strip(),
        "date_from": source.get("date_from", "").strip(),
        "date_to": source.get("date_to", "").strip(),
    }


def history_list(request):
    history_ops.maybe_apply_retention()
    f = _filters(request.GET)
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    date_from, date_to = history_ops.parse_date(f["date_from"]), history_ops.parse_date(f["date_to"])
    status = f["status"] if f["status"] in history.ALL_STATUSES else ""
    runs, total = history_ops.query_runs(f["q"], f["script"], status, date_from, date_to, page, PER_PAGE)
    pages = max(1, -(-total // PER_PAGE))
    if page > pages:
        page = pages
        runs, total = history_ops.query_runs(f["q"], f["script"], status, date_from, date_to, page, PER_PAGE)

    keep = {k: v for k, v in f.items() if v}
    base = urllib.parse.urlencode(keep)
    cfg = config.load_config()
    return render(request, "runner/history.html", {
        "runs": [_decorate(m) | {"active": history_ops.is_active(m), "logs_deleted": bool(m.get("logs_deleted")),
                                 "started_display": (m.get("started_at") or m.get("queued_at") or "").replace("T", " ")}
                 for m in runs],
        "f": f,
        "scripts": history_ops.script_choices(),
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
        "active": active_runs(),                     # the Running panel on top (Jobs merged in)
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
def history_delete_old(request):
    try:
        days = int(request.POST.get("days", ""))
        if not 0 <= days <= 36500:
            raise ValueError
    except ValueError:
        messages.error(request, "Enter a number of days between 0 and 36500.")
        return redirect("history")
    removed = history_ops.delete_older_than(days)
    messages.success(request, f"Deleted {removed} execution{'s' if removed != 1 else ''} older than {days} day{'s' if days != 1 else ''}.")
    return redirect("history")


@require_POST
def history_apply_retention(request):
    result = history_ops.apply_retention()
    messages.success(
        request,
        f"Retention applied: {result['runs_removed']} execution(s) removed, logs cleared on {result['logs_removed']} older execution(s).")
    return redirect("history")

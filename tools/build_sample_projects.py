"""Rebuild sample_projects/*.scripthub from sample_scripts/ (run after changing a sample script):

    python tools/build_sample_projects.py

Each project takes some sample scripts (same sub-folders as in sample_scripts/), adds sequences and presets,
checks every sequence step the way a run would, and is saved with ScriptHub's own project code. It works in a
throwaway data folder (SCRIPTHUB_HOME), so your own ScriptHub data is never touched.
runner/test_sample_scripts.py fails when a project no longer matches the scripts.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SAMPLES = BASE / "sample_scripts"
OUT = BASE / "sample_projects"

# step: (key, script path, values, x, y);  link: (from key, to key, when);  join: {key: "any"}
PROJECTS = [
    {
        "name": "Getting started",
        "files": ["basics/hello.py", "basics/hello.sh", "basics/hello.bat", "basics/parameter_tour.py",
                  "basics/outcome.py"],
        "presets": [
            ("basics/hello.py", "Formal", {"greeting": "Good morning", "name": "everyone"}),
            ("basics/hello.py", "Loud", {"shout": True, "times": "3"}),
            ("basics/outcome.py", "Fail after 2 seconds", {"outcome": "failure", "seconds": "2"}),
        ],
        "sequences": [{
            "name": "Try a sequence",
            "steps": [
                ("hello", "basics/hello.py", {"name": "sequence"}, 40, 140),
                ("work", "basics/outcome.py", {"outcome": "success", "seconds": "3"}, 300, 140),
                ("ok", "basics/hello.py", {"greeting": "Hi", "name": "it worked"}, 560, 40),
                ("bad", "basics/hello.py", {"greeting": "Hi", "name": "it failed", "shout": True}, 560, 240),
            ],
            "links": [("hello", "work", "success"), ("work", "ok", "success"), ("work", "bad", "failure")],
        }],
    },
    {
        "name": "Health check",
        "files": ["system/system_info.py", "system/disk_space.py", "system/log_message.py",
                  "network/check_port.py", "network/http_check.py"],
        "presets": [
            ("system/disk_space.py", "Strict (80%)", {"max_used": "80"}),
            ("network/http_check.py", "GitHub", {"url": "https://github.com/", "contains": "GitHub"}),
            ("network/check_port.py", "DNS (1.1.1.1:53)", {"host": "1.1.1.1", "port": "53"}),
        ],
        "sequences": [{
            "name": "Daily health check",
            "steps": [
                ("info", "system/system_info.py", {}, 40, 140),
                ("disk", "system/disk_space.py", {"max_used": "90"}, 300, 40),
                ("port", "network/check_port.py", {"host": "github.com", "port": "443"}, 300, 140),
                ("web", "network/http_check.py", {"url": "https://example.com/", "contains": "Example Domain"}, 300, 240),
                ("good", "system/log_message.py", {"message": "Health check passed.", "level": "info"}, 560, 80),
                ("alert", "system/log_message.py", {"message": "A health check failed: see the run.",
                                                    "level": "warning"}, 560, 220),
            ],
            "links": [("info", "disk", "always"), ("info", "port", "always"), ("info", "web", "always"),
                      ("disk", "good", "success"), ("port", "good", "success"), ("web", "good", "success"),
                      ("disk", "alert", "failure"), ("port", "alert", "failure"), ("web", "alert", "failure")],
            "join": {"alert": "any"},
        }],
    },
    {
        "name": "Files and data",
        "files": ["files/find_large_files.py", "files/clean_old_files.py", "files/backup_folder.py",
                  "files/file_checksum.py", "data/csv_summary.py", "data/sales.csv", "data/log_search.py",
                  "data/app.log", "system/log_message.py"],
        "presets": [
            ("data/csv_summary.py", "By product", {"group_by": "product", "value": "amount"}),
            ("data/csv_summary.py", "Quantity per region", {"group_by": "region", "value": "quantity"}),
            ("data/log_search.py", "Errors only", {"pattern": "ERROR|CRITICAL", "context": "1"}),
            ("files/find_large_files.py", "Logs over 1 MB", {"pattern": "*.log", "min_mb": "1"}),
        ],
        "sequences": [{
            "name": "Log and sales report",
            "steps": [
                ("sales", "data/csv_summary.py", {}, 40, 40),
                ("errors", "data/log_search.py", {"pattern": "ERROR|CRITICAL", "fail_if_found": True}, 40, 200),
                ("clean", "system/log_message.py", {"message": "app.log has no errors."}, 300, 140),
                ("found", "system/log_message.py", {"message": "app.log has errors: see the run.",
                                                    "level": "warning"}, 300, 260),
            ],
            "links": [("errors", "clean", "success"), ("errors", "found", "failure")],
        }],
    },
]


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="scripthub-samples-"))
    os.environ["SCRIPTHUB_HOME"] = str(home / "data")       # before config is imported
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scripthub_project.settings")
    sys.path.insert(0, str(BASE))
    import django
    django.setup()
    from runner import discovery, presets, project_files, sequence_runner, sequences, workspace

    OUT.mkdir(exist_ok=True)
    try:
        for proj in PROJECTS:
            folder = home / proj["name"]
            for rel in proj["files"]:
                target = folder / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SAMPLES / rel, target)
            workspace.open_folder(str(folder))
            discovery.scan(force=True)
            by_path = {s.path.relative_to(folder).as_posix(): s for s in discovery.scan()[0]}
            missing = [r for r in proj["files"] if Path(r).suffix in (".py", ".sh", ".bat") and r not in by_path]
            if missing:
                raise SystemExit(f"{proj['name']}: not discovered: {missing}")
            for rel, name, values in proj["presets"]:
                s = by_path[rel]
                presets.save_preset(s, name, s.meta["parameters"], values, "local")
            slugs = []
            for seq in proj["sequences"]:
                slug = sequences.create(seq["name"])
                ids = {}
                for key, rel, values, x, y in seq["steps"]:
                    ids[key] = sequences.add_step(slug, sequences.new_step(by_path[rel], values, x=x, y=y))
                for a, b, when in seq["links"]:
                    sequences.link(slug, ids[a], ids[b], when)
                for key, join in seq.get("join", {}).items():
                    sequences.update_step(slug, ids[key], join=join)
                _items, errors = sequence_runner.prepare(sequences.load(slug))
                if errors:
                    raise SystemExit(f"{proj['name']} / {seq['name']}: " + "; ".join(errors))
                slugs.append(slug)
            files = [f[0] for f in project_files.folder_files(folder)[0]]
            fh, report = project_files.build(folder, proj["name"], files, slugs)
            out = OUT / (proj["name"] + project_files.EXT)
            out.write_bytes(fh.read())
            fh.close()
            print(f"{out.relative_to(BASE)}: {report['files']} files ({report['scripts']} scripts), "
                  f"{report['sequences']} sequence(s), {report['presets']} preset(s)")
            for slug in slugs:
                sequences.delete(slug)
    finally:
        workspace.close_folder()
        shutil.rmtree(home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

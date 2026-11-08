"""Tests for the bundled example scripts (cleanup, pip offline tools, Django
deployment scripts). They run the scripts for real; the Bash ones run against a
stub 'python' so failures can be simulated without a Django project.

python manage.py test runner.test_bundled_scripts
"""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

import config

SCRIPTS = config.BASE_DIR / "scripts"
HAS_BASH = shutil.which("bash") is not None


def run(cmd, env=None, input=None, timeout=240):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env, input=input,
                          timeout=timeout, encoding="utf-8", errors="replace")


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TempCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-bundled-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ----------------------------------------------------------- directory cleanup
class DirectoryCleanupTests(TempCase):
    SCRIPT = SCRIPTS / "system" / "directory_cleanup.py"

    def setUp(self):
        super().setUp()
        self.target = self.tmp / "work" / "uploads"
        (self.target / "sub").mkdir(parents=True)
        self.old_log = self.make("old.log", 40)
        self.new_log = self.make("new.log", 1)
        self.old_txt = self.make("old.txt", 40)
        self.old_sub = self.make("sub/deep.log", 40)

    def make(self, rel, age_days):
        p = self.target / rel
        p.write_text("x" * 100)
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
        return p

    def go(self, *args):
        return run([sys.executable, self.SCRIPT, "--target", self.target, *args])

    def test_dry_run_lists_but_never_deletes(self):
        r = self.go("--days", "30")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY RUN", r.stdout)
        self.assertIn("would delete", r.stdout)
        self.assertIn("old.log", r.stdout)
        self.assertNotIn("new.log", r.stdout)
        self.assertTrue(all(p.exists() for p in (self.old_log, self.new_log, self.old_txt)))

    def test_confirm_delete_removes_only_old_matching_files(self):
        r = self.go("--days", "30", "--pattern", "*.log", "--confirm", "DELETE")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.old_log.exists())
        self.assertTrue(self.new_log.exists())     # too new
        self.assertTrue(self.old_txt.exists())     # pattern does not match
        self.assertTrue(self.old_sub.exists())     # not recursive
        self.assertIn("Deleted 1 file", r.stdout)

    def test_recursive(self):
        r = self.go("--days", "30", "--recursive", "--confirm", "DELETE")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.old_sub.exists())
        self.assertFalse(self.old_txt.exists())
        self.assertTrue(self.new_log.exists())
        self.assertTrue((self.target / "sub").is_dir())    # directories are never removed

    def test_wrong_confirmation_is_refused(self):
        for bad in ("delete", "yes", "DELETE "):
            r = self.go("--days", "30", "--confirm", bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("exactly", r.stderr)
        self.assertTrue(self.old_log.exists())

    def test_dangerous_targets_are_refused(self):
        root = Path(self.tmp.anchor)
        for target in (root, Path.home(), root / "Windows" if os.name == "nt" else root / "etc"):
            r = run([sys.executable, self.SCRIPT, "--target", target, "--days", "1", "--confirm", "DELETE"])
            self.assertIn(r.returncode, (2,), f"{target}: {r.stdout} {r.stderr}")
        r = run([sys.executable, self.SCRIPT, "--target", self.tmp / "missing", "--days", "1"])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.go("--days", "0").returncode, 2)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinks_are_not_followed_or_deleted(self):
        outside = self.tmp / "outside.log"
        outside.write_text("keep")
        os.utime(outside, (time.time() - 99 * 86400,) * 2)
        os.symlink(outside, self.target / "link.log")
        self.go("--days", "30", "--confirm", "DELETE")
        self.assertTrue(outside.exists())

    def test_nothing_to_do(self):
        r = self.go("--days", "3650", "--confirm", "DELETE")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Nothing to clean up", r.stdout)


# ------------------------------------------------------------------ pip tools
def make_wheel(folder: Path, name="srtestpkg", version="1.0", value=42) -> Path:
    """A minimal valid pure-Python wheel, built by hand (no network, no build backend)."""
    dist = f"{name}-{version}.dist-info"
    wheel = folder / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(f"{name}/__init__.py", f"VALUE = {value}\n")
        zf.writestr(f"{dist}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        zf.writestr(f"{dist}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        zf.writestr(f"{dist}/RECORD", f"{name}/__init__.py,,\n{dist}/METADATA,,\n{dist}/WHEEL,,\n{dist}/RECORD,,\n")
    return wheel


class PipDownloadTests(TempCase):
    mod = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mod = load_module(SCRIPTS / "python_packages" / "pip_download_wheelhouse.py")

    def ns(self, **over):
        base = dict(requirements=self.tmp / "req.txt", dest=self.tmp / "wh", platform="", python_version="",
                    only_binary=False, index_url="", bundle=False)
        base.update(over)
        return SimpleNamespace(**base)

    def test_command_for_this_machine(self):
        cmd = self.mod.build_command(self.ns())
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "download"])
        self.assertNotIn("--platform", cmd)
        self.assertNotIn("--only-binary=:all:", cmd)

    def test_cross_platform_command_forces_binary_wheels_once(self):
        cmd = self.mod.build_command(self.ns(platform="manylinux2014_x86_64", python_version="3.11", only_binary=True,
                                             index_url="https://mirror.example/simple"))
        self.assertEqual(cmd[cmd.index("--platform") + 1], "manylinux2014_x86_64")
        self.assertEqual(cmd[cmd.index("--python-version") + 1], "3.11")
        self.assertEqual(cmd.count("--only-binary=:all:"), 1)
        self.assertEqual(cmd[cmd.index("--index-url") + 1], "https://mirror.example/simple")
        self.assertTrue(all(isinstance(c, str) for c in cmd))

    def test_python_version_alone_also_requires_binary_wheels(self):
        self.assertIn("--only-binary=:all:", self.mod.build_command(self.ns(python_version="3.12")))

    def test_manifest_and_bundle(self):
        wh = self.tmp / "wh"
        wh.mkdir()
        wheel = make_wheel(wh)
        count = self.mod.write_manifest(wh, self.ns())
        self.assertEqual(count, 1)
        text = (wh / self.mod.MANIFEST).read_text()
        self.assertIn(hashlib.sha256(wheel.read_bytes()).hexdigest() + "  " + wheel.name, text)
        bundle = self.mod.make_bundle(wh)
        with zipfile.ZipFile(bundle) as zf:
            self.assertIn(f"wh/{wheel.name}", zf.namelist())

    def test_bad_inputs_exit_2_without_touching_the_network(self):
        script = SCRIPTS / "python_packages" / "pip_download_wheelhouse.py"
        r = run([sys.executable, script, "--requirements", self.tmp / "nope.txt", "--dest", self.tmp / "d"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("not found", r.stderr)
        (self.tmp / "req.txt").write_text("")
        r = run([sys.executable, script, "--requirements", self.tmp / "req.txt", "--dest", self.tmp / "d",
                 "--python-version", "3.x"])
        self.assertEqual(r.returncode, 2)
        r = run([sys.executable, script, "--requirements", self.tmp / "req.txt", "--dest", self.tmp / "d",
                 "--platform", "plan9"])
        self.assertNotEqual(r.returncode, 0)                 # not in the allowed list


class PipOfflineInstallTests(TempCase):
    SCRIPT = SCRIPTS / "python_packages" / "pip_install_offline.py"

    def setUp(self):
        super().setUp()
        self.wh = self.tmp / "wheelhouse"
        self.wh.mkdir()
        self.wheel = make_wheel(self.wh)
        self.req = self.tmp / "requirements.txt"
        self.req.write_text("srtestpkg==1.0\n")
        dl = load_module(SCRIPTS / "python_packages" / "pip_download_wheelhouse.py")
        dl.write_manifest(self.wh, SimpleNamespace(requirements=self.req, platform="", python_version=""))

    def install(self, *extra, req=None):
        return run([sys.executable, self.SCRIPT, "--wheelhouse", self.wh, "--requirements", req or self.req, *extra])

    def test_dry_run_checks_without_installing(self):
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Dry run OK", r.stdout)
        self.assertIn("1 file(s) verified", r.stdout)

    def test_real_install_into_a_new_virtualenv_with_no_network(self):
        venv = self.tmp / "venv"
        r = self.install("--venv", venv)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Creating virtual environment", r.stdout)
        self.assertIn("no broken requirements", r.stdout)
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        out = run([py, "-c", "import srtestpkg; print(srtestpkg.VALUE)"])
        self.assertEqual(out.stdout.strip(), "42", out.stderr)
        # a second run reuses the environment
        again = self.install("--venv", venv)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("Creating virtual environment", again.stdout)

    def test_missing_package_fails_clearly_and_never_goes_online(self):
        bad = self.tmp / "bad.txt"
        bad.write_text("surely-not-in-the-wheelhouse==9.9\n")
        r = self.install("--dry-run", req=bad)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pip install failed", r.stderr)
        self.assertIn("missing from the wheelhouse", r.stderr)

    def test_tampered_wheelhouse_is_rejected_before_anything_happens(self):
        self.wheel.write_bytes(self.wheel.read_bytes() + b"tampered")
        venv = self.tmp / "venv2"
        r = self.install("--venv", venv)
        self.assertEqual(r.returncode, 3)
        self.assertIn("changed or corrupted", r.stderr)
        self.assertFalse(venv.exists())                       # nothing was created
        ok = self.install("--dry-run", "--skip-verify")       # explicit override still works
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_missing_file_in_manifest_is_reported(self):
        self.wheel.unlink()
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 3)
        self.assertIn("missing:", r.stderr)

    def test_input_errors(self):
        r = run([sys.executable, self.SCRIPT, "--wheelhouse", self.tmp / "nope", "--requirements", self.req])
        self.assertEqual(r.returncode, 2)
        r = run([sys.executable, self.SCRIPT, "--wheelhouse", self.wh, "--requirements", self.tmp / "nope.txt"])
        self.assertEqual(r.returncode, 2)

    def test_command_never_allows_network_access(self):
        mod = load_module(self.SCRIPT)
        cmd = mod.build_install_command("py", self.wh, self.req)
        self.assertIn("--no-index", cmd)
        self.assertEqual(cmd[cmd.index("--find-links") + 1], str(self.wh))


class PipFreezeTests(TempCase):
    def test_freeze_writes_requirements_file(self):
        out = self.tmp / "sub" / "req.lock.txt"
        r = run([sys.executable, SCRIPTS / "python_packages" / "pip_freeze.py", "--output", out])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.is_file())
        self.assertIn("Django==", out.read_text())

    def test_bad_interpreter(self):
        r = run([sys.executable, SCRIPTS / "python_packages" / "pip_freeze.py", "--python", self.tmp / "nope"])
        self.assertEqual(r.returncode, 2)


# ------------------------------------------------------- bash: Django, DB, service
@unittest.skipUnless(HAS_BASH, "bash is required")
class BashScriptCase(TempCase):
    """Runs the shell scripts against a stub virtualenv whose 'python' just
    records its arguments (and can be told to fail on a chosen command)."""

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "manage.py").write_text("")
        self.venv = self.tmp / "venv"
        (self.venv / "bin").mkdir(parents=True)
        self.log = self.tmp / "calls.log"
        stub = self.venv / "bin" / "python"
        stub.write_text('#!/bin/sh\necho "$*" >> "$STUB_LOG"\n'
                        'if [ -n "$STUB_FAIL_ON" ]; then case "$*" in *"$STUB_FAIL_ON"*) echo "stub failure" >&2; exit 3;; esac; fi\n'
                        'echo "stub-python $*"\nexit 0\n')
        stub.chmod(0o755)

    def sh(self, script, *args, fail_on="", env_extra=None):
        env = {**os.environ, "STUB_LOG": self.log.as_posix(), "STUB_FAIL_ON": fail_on, **(env_extra or {})}
        return run(["bash", Path(script).as_posix(), *[a.as_posix() if isinstance(a, Path) else a for a in args]], env=env)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def common(self):
        return ["--project-dir", self.project, "--venv", self.venv]


class DjangoDeployTests(BashScriptCase):
    SCRIPT = SCRIPTS / "django" / "django_deploy.sh"

    def test_full_offline_deploy_runs_the_steps_in_order(self):
        (self.project / "requirements.txt").write_text("django\n")
        wh = self.tmp / "wheelhouse"
        wh.mkdir()
        r = self.sh(self.SCRIPT, *self.common(), "--wheelhouse", wh, "--migrate", "--collectstatic", "--settings", "app.settings")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls()
        steps = [c for c in calls if not c.startswith("--version")]
        self.assertTrue(steps[0].startswith("-m pip install"))
        self.assertIn("--no-index", steps[0])
        self.assertIn(f"--find-links {wh.as_posix()}", steps[0])
        self.assertEqual(steps[1:], ["manage.py check", "manage.py migrate --noinput", "manage.py collectstatic --noinput"])
        self.assertIn("Deployment finished OK", r.stdout)
        self.assertIn("OFFLINE", r.stdout)

    def test_online_install_when_no_wheelhouse(self):
        (self.project / "requirements.txt").write_text("django\n")
        r = self.sh(self.SCRIPT, *self.common())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        install = [c for c in self.calls() if c.startswith("-m pip install")][0]
        self.assertNotIn("--no-index", install)

    def test_optional_steps_are_skipped_unless_requested(self):
        r = self.sh(self.SCRIPT, *self.common())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c for c in self.calls() if not c.startswith("--version")], ["manage.py check"])
        self.assertIn("skipping package installation", r.stdout)

    def test_failing_migration_stops_the_deploy(self):
        r = self.sh(self.SCRIPT, *self.common(), "--migrate", "--collectstatic", "--service", "gunicorn-app", fail_on="migrate")
        self.assertNotEqual(r.returncode, 0)
        calls = " | ".join(self.calls())
        self.assertIn("manage.py migrate", calls)
        self.assertNotIn("collectstatic", calls)             # never reached
        self.assertNotIn("Deployment finished OK", r.stdout)
        self.assertNotIn("Restarting service", r.stdout)     # the service is not restarted onto broken code

    def test_failing_check_stops_before_migrate(self):
        r = self.sh(self.SCRIPT, *self.common(), "--migrate", fail_on="manage.py check")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("migrate", " ".join(self.calls()))

    def test_hostile_values_are_rejected_before_anything_runs(self):
        for extra in (["--service", "x;rm -rf /"], ["--service", "-bad"], ["--branch", "-x"], ["--branch", "a b"],
                      ["--settings", "a;b"], ["--bogus"]):
            with self.subTest(extra=extra):
                r = self.sh(self.SCRIPT, *self.common(), *extra)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_input_errors(self):
        r = self.sh(self.SCRIPT, "--venv", self.venv)
        self.assertIn("--project-dir is required", r.stderr)
        r = self.sh(self.SCRIPT, "--project-dir", self.tmp / "nope", "--venv", self.venv)
        self.assertIn("project directory not found", r.stderr)
        (self.project / "manage.py").unlink()
        r = self.sh(self.SCRIPT, *self.common())
        self.assertIn("manage.py not found", r.stderr)
        (self.project / "manage.py").write_text("")
        r = self.sh(self.SCRIPT, "--project-dir", self.project, "--venv", self.tmp / "no-venv")
        self.assertIn("no Python found", r.stderr)


class DjangoManageTests(BashScriptCase):
    SCRIPT = SCRIPTS / "django" / "django_manage.sh"

    def test_allowed_commands_and_extra_arguments(self):
        r = self.sh(self.SCRIPT, *self.common(), "--command", "showmigrations", "--args", "--plan myapp")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.calls(), ["manage.py showmigrations --plan myapp"])
        self.log.unlink()
        r = self.sh(self.SCRIPT, *self.common(), "--command", "migrate")
        self.assertEqual(self.calls(), ["manage.py migrate --noinput"])

    def test_command_exit_code_is_passed_through(self):
        r = self.sh(self.SCRIPT, *self.common(), "--command", "check", fail_on="check")
        self.assertEqual(r.returncode, 3)

    def test_disallowed_commands_and_arguments(self):
        for cmd in ("dbshell", "shell", "flush", "runserver", "loaddata", "migrate;ls"):
            with self.subTest(command=cmd):
                r = self.sh(self.SCRIPT, *self.common(), "--command", cmd)
                self.assertEqual(r.returncode, 2)
                self.assertIn("not allowed", r.stderr)
        # "a *": the space keeps the Windows MSYS runtime from globbing it before the script starts
        for arg in (";rm -rf /", "$(id)", "`id`", "a|b", "a *", "--x=$HOME", "a&b", ";", "a;b", "x\ty'z"):
            with self.subTest(args=arg):
                r = self.sh(self.SCRIPT, *self.common(), "--command", "check", "--args", arg)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("not allowed", r.stderr)
        self.assertEqual(self.calls(), [])


class DjangoCheckTests(BashScriptCase):
    SCRIPT = SCRIPTS / "django" / "django_check.sh"

    def test_all_checks_pass(self):
        r = self.sh(self.SCRIPT, *self.common())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("All checks passed", r.stdout)
        self.assertEqual([c for c in self.calls() if c.startswith("manage.py")],
                         ["manage.py check --deploy", "manage.py migrate --check"])

    def test_pending_migrations_fail_but_other_checks_still_run(self):
        r = self.sh(self.SCRIPT, *self.common(), fail_on="migrate --check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("[ OK ] Django system check", r.stdout)
        self.assertIn("[FAIL] Database migrations up to date", r.stdout)
        self.assertIn("1 check(s) failed", r.stderr)

    def test_deploy_check_failure(self):
        r = self.sh(self.SCRIPT, *self.common(), fail_on="check --deploy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("[FAIL] Django system check", r.stdout)
        self.assertIn("[ OK ] Database migrations", r.stdout)

    def test_validation(self):
        for extra in (["--url", "ftp://x"], ["--url", "javascript:1"], ["--service", "a b"], ["--settings", "x;y"]):
            with self.subTest(extra=extra):
                r = self.sh(self.SCRIPT, *self.common(), *extra)
                self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])


class ServiceControlTests(BashScriptCase):
    SCRIPT = SCRIPTS / "deployment" / "service_control.sh"

    def test_validation_happens_before_anything_is_executed(self):
        for args in (["--service", "x;reboot", "--action", "status"], ["--service", "-x", "--action", "status"],
                     ["--service", "nginx", "--action", "format-disk"], ["--service", "nginx", "--action", "status", "--lines", "abc"],
                     ["--service", "nginx", "--action", "logs", "--lines", "999999"], ["--action", "status"]):
            with self.subTest(args=args):
                r = self.sh(self.SCRIPT, *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    @unittest.skipIf(shutil.which("systemctl"), "only meaningful where systemd is absent")
    def test_reports_missing_systemd(self):
        r = self.sh(self.SCRIPT, "--service", "nginx", "--action", "status")
        self.assertEqual(r.returncode, 2)
        self.assertIn("systemctl is not available", r.stderr)


class DatabaseBackupTests(BashScriptCase):
    SCRIPT = SCRIPTS / "database" / "db_backup.sh"

    def make_db(self):
        import sqlite3
        db = self.tmp / "app.sqlite3"
        con = sqlite3.connect(db)
        con.execute("create table t (x)")
        con.executemany("insert into t values (?)", [(i,) for i in range(50)])
        con.commit()
        con.close()
        return db

    def test_sqlite_backup_is_a_usable_database(self):
        import sqlite3
        db, dest = self.make_db(), self.tmp / "backups"
        r = self.sh(self.SCRIPT, "--type", "sqlite", "--database", db, "--dest-dir", dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        backups = list(dest.glob("app-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        con = sqlite3.connect(backups[0])
        self.assertEqual(con.execute("select count(*) from t").fetchone()[0], 50)
        con.close()

    def test_retention_keeps_only_the_newest(self):
        db, dest = self.make_db(), self.tmp / "backups"
        dest.mkdir()
        for i in range(5):
            f = dest / f"app-2020010{i}-000000.sqlite3"
            f.write_text("old")
            t = time.time() - (10 - i) * 86400
            os.utime(f, (t, t))
        other = dest / "unrelated.txt"
        other.write_text("keep me")
        r = self.sh(self.SCRIPT, "--type", "sqlite", "--database", db, "--dest-dir", dest, "--keep", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        left = sorted(p.name for p in dest.glob("app-*.sqlite3"))
        self.assertEqual(len(left), 2)
        self.assertNotIn("app-20200100-000000.sqlite3", left)      # oldest gone
        self.assertEqual(other.read_text(), "keep me")             # unrelated files untouched
        self.assertIn("Removed old backup", r.stdout)

    def test_validation(self):
        db, dest = self.make_db(), self.tmp / "backups"
        cases = (
            ["--type", "oracle", "--database", db, "--dest-dir", dest],
            ["--type", "sqlite", "--database", self.tmp / "missing.db", "--dest-dir", dest],
            ["--type", "sqlite", "--database", db, "--dest-dir", dest, "--keep", "0"],
            ["--type", "postgres", "--database", "x;drop", "--dest-dir", dest],
            ["--type", "postgres", "--database", "-bad", "--dest-dir", dest],
            ["--type", "postgres", "--database", "ok", "--dest-dir", dest, "--host", "a b"],
            ["--type", "postgres", "--database", "ok", "--dest-dir", dest, "--port", "5432x"],
            ["--type", "postgres", "--database", "ok", "--dest-dir", dest, "--user", "-U"],
        )
        for args in cases:
            with self.subTest(args=args):
                r = self.sh(self.SCRIPT, *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(list(dest.glob("*")) if dest.exists() else [], [])


class BundledMetadataTests(SimpleTestCase):
    def test_every_bundled_script_has_valid_metadata_and_a_description(self):
        from runner import discovery, workspace
        bundled = Path(__file__).resolve().parent.parent / "scripts"
        with mock.patch.object(workspace, "roots", return_value=[bundled]):   # not the folder open in real use
            scripts = discovery.scan(force=True)[0]
        self.addCleanup(discovery.scan, True)
        self.assertGreaterEqual(len(scripts), 15)
        for s in scripts:
            with self.subTest(script=s.name):
                self.assertTrue(s.meta_valid, s.meta_error)
                self.assertTrue(s.description)
        categories = {s.category for s in scripts}
        for needed in ("Django", "Python packages", "Database", "Deployment", "Maintenance", "System", "Backup"):
            self.assertIn(needed, categories)

    def test_password_parameter_uses_environment_not_arguments(self):
        from runner import parameter_service as ps
        from runner import metadata
        meta = metadata.load_raw(SCRIPTS / "database" / "db_backup.sh", "0" * 12)[0]
        params = ps.validate_metadata(meta)["parameters"]
        argv, env = ps.build_arguments(params, {"type": "postgres", "database": "d", "keep": 3, "password": "s3cret"})
        self.assertNotIn("s3cret", argv)
        self.assertEqual(env, {"PGPASSWORD": "s3cret"})

    def test_cleanup_requires_the_exact_confirmation_word(self):
        from runner import parameter_service as ps
        from runner import metadata
        meta = metadata.load_raw(SCRIPTS / "system" / "directory_cleanup.py", "0" * 12)[0]
        params = ps.validate_metadata(meta)["parameters"]
        _v, errors = ps.clean_values(params, {"target": "/tmp/x", "days": "30", "confirm": "delete"})
        self.assertIn("confirm", errors)
        _v, errors = ps.clean_values(params, {"target": "/tmp/x", "days": "30", "confirm": "DELETE"})
        self.assertEqual(errors, {})
        _v, errors = ps.clean_values(params, {"target": "/tmp/x", "days": "30", "confirm": ""})
        self.assertEqual(errors, {})                           # empty = dry run

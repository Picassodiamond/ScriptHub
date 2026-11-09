"""python manage.py clear_runtime [KIND ... | all]: show or clear ScriptHub's stored data (Settings -> Data)."""
from django.core.management.base import BaseCommand, CommandError

from runner import runtime_data


class Command(BaseCommand):
    help = "Show what ScriptHub stores in its data folder, or clear some of it: " + ", ".join(runtime_data.KEYS) + ", all."

    def add_arguments(self, parser):
        parser.add_argument("kinds", nargs="*", help="what to clear; nothing = only show")

    def handle(self, *args, kinds=(), **options):
        loc = runtime_data.location()
        self.stdout.write(f"{loc['path']} ({loc['how']})")
        if not kinds:
            for r in runtime_data.overview():
                size = runtime_data.human(r["bytes"]) if r["files"] else "empty"
                self.stdout.write(f"  {r['key']:<10} {size:>10}  {r['label']}")
            return
        keys = list(runtime_data.KEYS) if "all" in kinds else list(kinds)
        unknown = [k for k in keys if k not in runtime_data.KEYS]
        if unknown:
            raise CommandError(f"Unknown: {', '.join(unknown)}. Use: {', '.join(runtime_data.KEYS)}, all.")
        for key in keys:
            try:
                self.stdout.write(runtime_data.clear(key))
            except ValueError as exc:
                self.stderr.write(str(exc))

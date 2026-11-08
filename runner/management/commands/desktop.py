"""python manage.py desktop [--background]: run ScriptHub in its own window."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run ScriptHub as a desktop app (own window, tray icon, notifications)."

    def add_arguments(self, parser):
        parser.add_argument("--background", action="store_true", help="Start in the tray without opening a window.")

    def handle(self, *args, **options):
        from runner.desktop_app import main
        main(["--background"] if options["background"] else [])

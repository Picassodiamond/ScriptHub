"""ScriptHub as a desktop app. Double-click (runs with pythonw, no console),
or: python desktop.pyw   /   python manage.py desktop   [--background]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scripthub_project.settings")

from runner.desktop_app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

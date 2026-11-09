import logging

from django.apps import AppConfig

log = logging.getLogger(__name__)


class RunnerConfig(AppConfig):
    name = "runner"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        """Runs left 'queued'/'running' by an application that is no longer
        alive are marked 'interrupted' (runs owned by a live instance are
        never touched)."""
        try:
            from .process_manager import manager
            fixed = manager.recover_interrupted()
            if fixed:
                log.warning("Marked %d interrupted execution(s): %s", len(fixed), ", ".join(fixed))
        except Exception:  # never block startup
            log.exception("Execution recovery failed")
        try:
            from .sequence_runner import sequencer
            fixed = sequencer.recover_interrupted()
            if fixed:
                log.warning("Marked %d interrupted sequence run(s): %s", len(fixed), ", ".join(fixed))
        except Exception:  # never block startup
            log.exception("Sequence recovery failed")
        try:
            from . import history_ops
            history_ops.maybe_apply_retention()
        except Exception:
            log.exception("Retention sweep failed")

"""Stable script ids and their aliases (runner/ids.py)."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from . import backups, ids


class IdsTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sh-ids-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for p in (mock.patch.object(config, "CONFIG_FILE", self.tmp / "config.json"),
                  mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets"),
                  mock.patch.object(config, "BACKUPS_DIR", self.tmp / "backups")):
            p.start()
            self.addCleanup(p.stop)

    def test_ids_are_twelve_hex_and_path_ids_follow_the_path(self):
        self.assertTrue(ids.valid(ids.new_id()))
        self.assertNotEqual(ids.new_id(), ids.new_id())
        self.assertEqual(ids.path_id(self.tmp / "a.py"), ids.path_id(self.tmp / "a.py"))
        self.assertNotEqual(ids.path_id(self.tmp / "a.py"), ids.path_id(self.tmp / "b.py"))
        for bad in ("", "ABCDEF123456", "abc", "../../etc/x", None, 5):
            self.assertFalse(ids.valid(bad), bad)

    def test_aliases_resolve_in_chains_and_live_next_to_the_config(self):
        a, b, c = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"
        ids.add_alias(a, b)
        ids.add_alias(b, c)
        self.assertEqual(ids.canonical(a), c)
        self.assertEqual(ids.canonical(c), c)
        self.assertEqual(json.loads((self.tmp / "script_ids.json").read_text())["aliases"], {a: b, b: c})
        ids.add_alias("../bad", c)                          # ignored
        self.assertNotIn("../bad", ids.aliases())

    def test_adopt_moves_saved_values_and_backups_without_overwriting(self):
        old, new = "111111111111", "222222222222"
        (config.PRESETS_DIR / old / "recent").mkdir(parents=True)
        (config.PRESETS_DIR / old / "prod.yaml").write_text("name: Prod\n")
        (config.PRESETS_DIR / old / "recent" / "RUN-20260101-001.yaml").write_text("values: {}\n")
        (config.PRESETS_DIR / new).mkdir(parents=True)
        (config.PRESETS_DIR / new / "prod.yaml").write_text("name: Kept\n")        # already there: kept
        backups.add(old, "x.py", b"v1")
        ids.adopt(old, new)
        self.assertEqual((config.PRESETS_DIR / new / "prod.yaml").read_text(), "name: Kept\n")
        self.assertTrue((config.PRESETS_DIR / new / "recent" / "RUN-20260101-001.yaml").is_file())
        self.assertFalse((config.PRESETS_DIR / old).exists())
        self.assertEqual(backups.read(new), b"v1")
        self.assertEqual(ids.canonical(old), new)

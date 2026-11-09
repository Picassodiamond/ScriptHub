"""The metadata block at the top of a script: YAML, old JSON blocks still read (runner/metadata.py)."""
from pathlib import Path

from django.test import SimpleTestCase

from . import metadata

DATA = {"id": "a1b2c3d4e5f6", "name": "Backup: nightly", "category": "Backup", "platforms": ["windows", "linux"],
        "parameters": [
            {"name": "source", "type": "directory", "required": True, "arg": "--source"},
            {"name": "mode", "type": "select", "choices": ["full", "incremental"], "default": "full"},
            {"name": "notes", "type": "textarea", "default": "Line one\nLine two"},
        ]}


class MetadataBlockTests(SimpleTestCase):
    def test_written_as_yaml_and_read_back(self):
        text = metadata.with_block("#!/usr/bin/env python3\nprint(1)\n", DATA, Path("x.py"), "\n")
        self.assertTrue(text.startswith("#!/usr/bin/env python3\n# --- scripthub ---\n# id: a1b2c3d4e5f6\n"))
        self.assertIn('\n# name: "Backup: nightly"\n', text)
        self.assertIn("\n# parameters:\n#   - name: source\n#     type: directory\n#     required: true\n", text)
        self.assertIn("\n#     choices: [full, incremental]\n", text)
        self.assertNotIn("{", text)
        self.assertTrue(text.endswith("# --- end scripthub ---\nprint(1)\n"))
        self.assertEqual(metadata.parse_block(text), (DATA, None))

    def test_batch_prefix_and_crlf(self):
        text = metadata.with_block("@echo off\r\necho hi\r\n", DATA, Path("x.bat"), "\r\n")
        self.assertIn("\r\n:: parameters:\r\n::   - name: source\r\n", text)
        self.assertNotIn("\n\n", text.replace("\r\n", "\n"))
        self.assertEqual(metadata.parse_block(text), (DATA, None))

    def test_old_json_blocks_are_still_read_and_become_yaml(self):
        old = ('# --- scripthub ---\n# {\n#   "name": "Old",\n#   "parameters": [\n'
               '#     {"name": "a", "type": "text", "default": "x"}\n#   ]\n# }\n# --- end scripthub ---\nprint(1)\n')
        data, err = metadata.parse_block(old)
        self.assertIsNone(err)
        self.assertEqual(data, {"name": "Old", "parameters": [{"name": "a", "type": "text", "default": "x"}]})
        new = metadata.with_block(old, data, Path("x.py"), "\n")
        self.assertIn("# name: Old\n# parameters:\n#   - name: a\n", new)
        self.assertEqual(metadata.parse_block(new), (data, None))

    def test_hand_written_yaml_with_comments_and_flow_parameters(self):
        text = ("# --- scripthub ---\n# name: Hand made   # a comment\n# # a whole comment line\n#\n"
                "# parameters:\n# - {name: a, type: text}\n# - name: b\n#   type: number\n# --- end scripthub ---\n")
        self.assertEqual(metadata.parse_block(text), (
            {"name": "Hand made", "parameters": [{"name": "a", "type": "text"}, {"name": "b", "type": "number"}]}, None))

    def test_errors_name_the_line_of_the_script(self):
        text = "#!/bin/sh\n# --- scripthub ---\n# name: x\n#   bad: indent\n# --- end scripthub ---\n"
        data, err = metadata.parse_block(text)
        self.assertEqual(data, {})
        self.assertIn("lines 2-5", err)
        self.assertIn("line 4:", err)
        _data, err = metadata.parse_block('# --- scripthub ---\n# {"name": \n# --- end scripthub ---\n')
        self.assertIn("JSON", err)
        _data, err = metadata.parse_block("# --- scripthub ---\n# - a\n# --- end scripthub ---\n")
        self.assertIn("mapping", err)

    def test_empty_block(self):
        text = metadata.with_block("print(1)\n", {}, Path("x.py"), "\n")
        self.assertEqual(text, "# --- scripthub ---\n# --- end scripthub ---\nprint(1)\n")
        self.assertEqual(metadata.parse_block(text), ({}, None))

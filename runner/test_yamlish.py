"""The small YAML subset used for saved values (runner/yamlish.py)."""
from django.test import SimpleTestCase

from . import yamlish


class YamlishTests(SimpleTestCase):
    def test_round_trip_of_awkward_values(self):
        data = {
            "name": "Prod: backup #1", "host": "local", "saved_at": "2026-11-08T20:44:13", "nothing": None,
            "values": {
                "source": "/data/project", "dest": "C:\\backups", "compress": True, "count": 4, "ratio": 0.5,
                "looks_bool": "yes", "looks_int": "007", "empty": "", "quote": "it's \"x\"", "unicode": "café ✓",
                "tags": ["a", "b c", "true", "1"], "none": [], "trailing": "x ", "colon": "a:b",
            },
        }
        text = yamlish.dump(data, header="Preset for x.py\nPasswords are never stored.")
        self.assertTrue(text.startswith("# Preset for x.py\n# Passwords are never stored.\n"))
        self.assertIn("\nvalues:\n  source: /data/project\n", text)        # readable, not JSON
        self.assertEqual(yamlish.load(text), data)

    def test_hand_written_forms(self):
        text = "# comment\n---\na: 1   # trailing comment\nb:\n- x\n- 'it''s'\nc: ~\nd:\n    e: \"q\"\n"
        self.assertEqual(yamlish.load(text), {"a": 1, "b": ["x", "it's"], "c": None, "d": {"e": "q"}})

    def test_unsupported_or_broken_input_raises(self):
        for bad in ("a:\n\t- x", "a: [1, 2", "a: [1 2]x", "a: {k}", "a: ['x]", "a: [&x]", "a: &anchor x", "- x\n- y", "a: 1\n  b: 2", "a: 'open", 'a: "bad\\q"',
                    "a:\n  - b: 1\n    - c"):
            with self.assertRaises(yamlish.YamlError, msg=bad):
                yamlish.load(bad)

    def test_lists_of_mappings_round_trip(self):
        data = {"name": "Nightly", "steps": [
            {"script": "3f9a1c0b7d22", "path": "C:\\scripts\\db.py", "values": {"days": 30, "tags": ["a", "b"]},
             "on_failure": "stop"},
            {},
            {"script": "81be44f0a913", "preset": "prod", "values": {}, "on_failure": "continue"},
        ], "after": ["x", "- y", "k: v"]}
        text = yamlish.dump(data)
        self.assertIn("steps:\n  - script: 3f9a1c0b7d22\n    path: C:\\scripts\\db.py\n    values:\n      days: 30\n", text)
        self.assertIn("\n  - {}\n", text)
        self.assertEqual(yamlish.load(text), data)

    def test_hand_written_list_of_mappings(self):
        text = "steps:\n- script: abc\n  values:\n    a: 1\n-   script: def\n    on_failure: continue\n- plain\nend: 1\n"
        self.assertEqual(yamlish.load(text), {"steps": [{"script": "abc", "values": {"a": 1}},
                                                        {"script": "def", "on_failure": "continue"}, "plain"],
                                              "end": 1})

    def test_lists_inside_lists_are_refused(self):
        with self.assertRaises(yamlish.YamlError):
            yamlish.dump({"a": [["x"]]})

    def test_flow_collections(self):
        text = ("a: [1, 'x''y', \"q,w\", {k: v, z: [true, null]}]\np:\n  - {name: a, type: text}\n  - [1, 2]\n"
                "e: [ ]\nu: [http://x:80/a]\nt: [a, b, ]\n")
        self.assertEqual(yamlish.load(text), {"a": [1, "x'y", "q,w", {"k": "v", "z": [True, None]}],
                                              "p": [{"name": "a", "type": "text"}, [1, 2]], "e": [],
                                              "u": ["http://x:80/a"], "t": ["a", "b"]})

    def test_flow_dump_round_trip(self):
        data = {"name": "Hello", "parameters": [
            {"name": "greeting", "type": "select", "default": "on",
             "choices": ["Hello", "Hi, there", "on", "5", "[x]", "a: b", "", "it's", "{y}"]},
            {"name": "n", "type": "number", "default": 1.5, "min": -3, "help": None}],
            "tags": [], "nested": {"a": [1, 2]}, "lol": [[1, 2], ["a,b"]], "long": ["x" * 30] * 5}
        text = yamlish.dump(data, flow=True)
        self.assertIn('\n    choices: [Hello, "Hi, there", "on", "5", "[x]", "a: b", "", "it\'s", "{y}"]\n', text)
        self.assertIn("\nlong:\n  - xxx", text)                  # too long for one line
        self.assertEqual(yamlish.load(text), data)
        self.assertNotIn("[", yamlish.dump({"a": ["x", "y"]}))    # saved values keep block lists

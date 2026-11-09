"""A small YAML subset for the saved-values files (no third-party dependency).

Written by ``dump`` and read by ``load``:

    # comments
    name: Production backup        # plain scalars
    note: "quoted: \\"json\\" style"  # double-quoted (JSON escapes) or 'single' ('' = ')
    count: 4                       # int / float / true / false / null (also ~)
    tags: []                       # empty list / {} empty mapping
    values:                        # nested mapping (indented by 2+ spaces)
      source: /data/project
      hosts:                       # block list of scalars
        - web-1
        - web-2
    steps:                         # block list of mappings (sequences)
      - script: 3f9a1c0b7d22
        on_failure: stop
      - {}
    choices: [Hello, "Hi, there"]  # flow list / {a: 1, b: [x]} flow mapping (one line)

Anything else (anchors, multi-line strings, flow collections over several lines,
block lists inside lists, tabs for indentation) raises ``YamlError``: the files are written by ScriptHub and only
occasionally edited by hand, so a clear error beats guessing.
"""
import json
import re

INDENT = "  "
_PLAIN_OK = re.compile(r"^[A-Za-z0-9_./@+=,-][A-Za-z0-9_./@+=, :()\\-]*$")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "~", ""}
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+(\.\d*)?[eE][-+]?\d+)$")
_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class YamlError(ValueError):
    pass


# ----------------------------------------------------------------- dump ---
def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    if (_PLAIN_OK.match(s) and s.lower() not in _RESERVED and not _INT.match(s) and not _FLOAT.match(s)
            and ": " not in s and " #" not in s and not s.endswith((" ", ":")) and s == s.strip()):
        return s
    return json.dumps(s, ensure_ascii=False)          # a JSON string is a valid double-quoted YAML scalar


_FLOW_BAD = re.compile(r"[,\[\]{}]")
FLOW_WIDTH = 100                                       # longer lists of scalars are written as blocks


def _flow(v) -> str:
    """``v`` as a one-line flow value (lists and mappings in brackets)."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    text = _scalar(v)
    if not text.startswith('"') and _FLOW_BAD.search(text):
        text = json.dumps(str(v), ensure_ascii=False)
    return text


def _key(k) -> str:
    k = str(k)
    return k if _KEY.match(k) and k.lower() not in _RESERVED else json.dumps(k, ensure_ascii=False)


def _dump_map(d: dict, level: int, out: list, flow: bool = False) -> None:
    pad = INDENT * level
    for k, v in d.items():
        if isinstance(v, dict):
            if v:
                out.append(f"{pad}{_key(k)}:")
                _dump_map(v, level + 1, out, flow)
            else:
                out.append(f"{pad}{_key(k)}: {{}}")
        elif isinstance(v, (list, tuple)):
            if not v:
                out.append(f"{pad}{_key(k)}: []")
                continue
            if flow and not any(isinstance(item, dict) for item in v):
                line = f"{pad}{_key(k)}: {_flow(v)}"
                if len(line) <= FLOW_WIDTH or any(isinstance(item, (list, tuple)) for item in v):
                    out.append(line)
                    continue
            out.append(f"{pad}{_key(k)}:")
            for item in v:
                if isinstance(item, dict):           # "- key: value", further keys under the first
                    if not item:
                        out.append(f"{pad}{INDENT}- {{}}")
                        continue
                    sub = []
                    _dump_map(item, level + 2, sub, flow)
                    sub[0] = f"{pad}{INDENT}- " + sub[0].lstrip(" ")
                    out.extend(sub)
                    continue
                if isinstance(item, (list, tuple)):
                    if not flow:
                        raise YamlError("Lists inside lists are not supported.")
                    out.append(f"{pad}{INDENT}- {_flow(item)}")
                    continue
                out.append(f"{pad}{INDENT}- {_scalar(item)}")
        else:
            out.append(f"{pad}{_key(k)}: {_scalar(v)}")


def dump(data: dict, header: str = "", flow: bool = False) -> str:
    """``data`` (a mapping of scalars, lists of scalars and nested mappings) as YAML text.
    ``flow``: short lists of scalars on one line (``choices: [a, b]``)."""
    out = [f"# {line}".rstrip() for line in header.splitlines()] if header else []
    _dump_map(data, 0, out, flow)
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- load ---
def _strip_comment(text: str) -> str:
    """Remove a trailing ' # comment' outside quotes."""
    quote = None
    for i, c in enumerate(text):
        if quote:
            if c == "\\" and quote == '"':
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _parse_scalar(s: str, line_no: int):
    s = s.strip()
    if s.startswith('"'):
        try:
            return json.loads(s)
        except ValueError as exc:
            raise YamlError(f"line {line_no}: bad double-quoted string ({exc})") from None
    if s.startswith("'"):
        if len(s) < 2 or not s.endswith("'"):
            raise YamlError(f"line {line_no}: unterminated single-quoted string")
        return s[1:-1].replace("''", "'")
    if s.startswith(("[", "{")):
        return _parse_flow(s, line_no)
    if s.startswith(("&", "*", "!", "|", ">")):
        raise YamlError(f"line {line_no}: this YAML feature is not supported here")
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    if _INT.match(s):
        return int(s)
    if _FLOAT.match(s):
        return float(s)
    return s


def _parse_flow(s: str, line_no: int):
    """A one-line flow collection: ``[a, "b, c", {k: v}]`` or ``{k: v, n: [1, 2]}``."""
    pos = 0

    def fail(what):
        raise YamlError(f"line {line_no}: {what} in a [...] / {{...}} value")

    def spaces():
        nonlocal pos
        while pos < len(s) and s[pos] in " \t":
            pos += 1

    def quoted():
        nonlocal pos
        q, end = s[pos], pos + 1
        while end < len(s):
            if q == '"' and s[end] == "\\":
                end += 2
                continue
            if s[end] == q:
                if q == "'" and s[end + 1:end + 2] == "'":
                    end += 2
                    continue
                break
            end += 1
        if end >= len(s):
            fail("an unterminated string")
        text, pos = s[pos:end + 1], end + 1
        return _parse_scalar(text, line_no)

    def plain(stop):
        nonlocal pos
        start = pos
        while pos < len(s) and s[pos] not in stop:    # values may contain ':' (http://x:80)
            pos += 1
        return s[start:pos].strip()

    def value():
        nonlocal pos
        spaces()
        if pos >= len(s):
            fail("a missing value")
        c = s[pos]
        if c in "[{":
            return collection()
        if c in "\"'":
            return quoted()
        text = plain(",]}")
        if text.startswith(("&", "*", "!", "|", ">", "#")):
            fail(f"an unsupported value {text!r}")
        return _parse_scalar(text, line_no)

    def collection():
        nonlocal pos
        is_map, close = s[pos] == "{", "}" if s[pos] == "{" else "]"
        pos += 1
        result = {} if is_map else []
        spaces()
        if pos < len(s) and s[pos] == close:
            pos += 1
            return result
        while True:
            spaces()
            if is_map:
                key = quoted() if pos < len(s) and s[pos] == '"' else plain(":,]}")
                spaces()
                if pos >= len(s) or s[pos] != ":":
                    fail("a key without ': value'")
                pos += 1
                result[str(key)] = value()
            else:
                result.append(value())
            spaces()
            if pos < len(s) and s[pos] == ",":
                pos += 1
                spaces()
                if pos < len(s) and s[pos] == close:     # a trailing comma: [a, b,]
                    pos += 1
                    return result
                continue
            if pos < len(s) and s[pos] == close:
                pos += 1
                return result
            fail(f"a missing ',' or '{close}'")

    result = collection()
    spaces()
    if pos < len(s):
        fail("text after the closing bracket")
    return result


def _split_key(text: str, line_no: int):
    if text.startswith('"'):
        end = 1
        while end < len(text):
            if text[end] == "\\":
                end += 2
                continue
            if text[end] == '"':
                break
            end += 1
        key = _parse_scalar(text[:end + 1], line_no)
        rest = text[end + 1:]
    else:
        idx = text.find(":")
        if idx <= 0:
            raise YamlError(f"line {line_no}: expected 'key: value'")
        key, rest = text[:idx].strip(), text[idx:]
    if not rest.startswith(":") or (len(rest) > 1 and rest[1] not in " \t"):
        raise YamlError(f"line {line_no}: expected 'key: value'")
    return str(key), rest[1:].strip()


def load(text: str) -> dict:
    """Parse the subset described above; raises YamlError."""
    lines = []
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise YamlError(f"line {n}: use spaces, not tabs, to indent")
        if raw.strip() in ("---", "..."):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((n, indent, _strip_comment(raw.strip())))

    pos = 0
    item_key = re.compile(r'^("(?:[^"\\]|\\.)*"|[^\s"\'\-\[{&*!|>#][^:]*):(\s|$)')

    def item():
        """The list item at ``pos`` ("- scalar" or "- key: value" starting a mapping)."""
        nonlocal pos
        n, ind, content = lines[pos]
        rest = content[1:].lstrip(" ")
        if item_key.match(rest):                     # a mapping: its keys line up after "- "
            lines[pos] = (n, ind + len(content) - len(rest), rest)
            return block(lines[pos][1])
        pos += 1
        return _parse_scalar(rest, n)

    def block(indent):
        nonlocal pos
        result = None
        while pos < len(lines):
            n, ind, content = lines[pos]
            if ind < indent:
                break
            if ind > indent:
                raise YamlError(f"line {n}: unexpected indentation")
            if content.startswith("- ") or content == "-":
                if result is None:
                    result = []
                elif not isinstance(result, list):
                    raise YamlError(f"line {n}: a list item inside a mapping")
                result.append(item())
                continue
            if result is None:
                result = {}
            elif not isinstance(result, dict):
                raise YamlError(f"line {n}: a mapping key inside a list")
            key, value = _split_key(content, n)
            pos += 1
            if value == "":
                if pos < len(lines) and lines[pos][1] > indent:
                    result[key] = block(lines[pos][1])
                elif pos < len(lines) and lines[pos][1] == indent and lines[pos][2].startswith("-"):
                    items = []                           # "key:" followed by "- item" at the same indent
                    while pos < len(lines) and lines[pos][1] == indent and \
                            (lines[pos][2].startswith("- ") or lines[pos][2] == "-"):
                        items.append(item())
                    result[key] = items
                else:
                    result[key] = None
            else:
                result[key] = _parse_scalar(value, n)
        return {} if result is None else result

    data = block(lines[0][1] if lines else 0)
    if pos < len(lines):
        raise YamlError(f"line {lines[pos][0]}: unexpected indentation")
    if not isinstance(data, dict):
        raise YamlError("the file must be a mapping (key: value lines)")
    return data

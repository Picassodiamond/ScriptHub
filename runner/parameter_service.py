"""Parameter and metadata schema: definition validation, value coercion and
argument building.

Parameter types live in a small registry (``TYPES``) so new types can be added
with ``register_type`` without touching views or templates' logic.

Value model
-----------
* ``raw``    – what a form carries: strings, a list of strings (multiselect)
               or a bool (boolean).
* ``value``  – validated Python value: str, int, bool or list[str].
               ``decimal`` values are kept as canonical strings so they stay
               JSON-serialisable and are passed to scripts untouched.
"""
import datetime
import re
from decimal import Decimal, InvalidOperation

from .validators import ValidationError

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,254}$")
ARG_RE = re.compile(r"^-{1,2}[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FORBIDDEN_ENV = {
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "IFS", "SHELL", "BASH_ENV", "ENV",
}
MAX_TEXT = 100_000
MAX_PATH = 4096
PASS_AS = ("flag", "positional", "env")
SECRET_MASK = "••••••"

KNOWN_KEYS = {
    "name", "label", "type", "default", "required", "help", "placeholder",
    "choices", "min", "max", "min_length", "max_length", "pattern",
    "editable_at_runtime", "arg", "pass_as", "position", "env_name",
}
# Validation-related keys that only make sense for some types.
TYPED_KEYS = {"choices", "min", "max", "min_length", "max_length", "pattern"}


# ----------------------------------------------------------------- types ---
class ParamType:
    key = ""
    label = ""
    input = "text"          # which widget the form renderer uses
    allowed = frozenset()   # subset of TYPED_KEYS valid for this type

    def parse(self, raw, p):  # pragma: no cover - interface
        raise NotImplementedError

    def to_raw(self, value):
        return "" if value is None else str(value)


class _StringType(ParamType):
    allowed = frozenset({"min_length", "max_length", "pattern"})
    limit = MAX_TEXT

    def parse(self, raw, p):
        s = str(raw)
        if len(s) > self.limit:
            raise ValueError(f"Must be at most {self.limit} characters.")
        if "\x00" in s:
            raise ValueError("Contains an invalid character.")
        lo, hi = p.get("min_length"), p.get("max_length")
        if lo is not None and len(s) < lo:
            raise ValueError(f"Must be at least {lo} characters.")
        if hi is not None and len(s) > hi:
            raise ValueError(f"Must be at most {hi} characters.")
        pat = p.get("pattern")
        if pat and not re.fullmatch(pat, s):
            raise ValueError("Does not match the required format.")
        return s


class TextType(_StringType):
    key, label, input = "text", "Text", "text"


class TextareaType(_StringType):
    key, label, input = "textarea", "Text area", "textarea"


class PasswordType(_StringType):
    key, label, input = "password", "Password", "password"


class _PathType(_StringType):
    limit = MAX_PATH

    def parse(self, raw, p):
        s = super().parse(raw, p)
        if any(ord(c) < 32 for c in s):
            raise ValueError("Path contains invalid characters.")
        return s


class FileType(_PathType):
    key, label, input = "file", "File path", "text"


class DirectoryType(_PathType):
    key, label, input = "directory", "Directory path", "text"


class NumberType(ParamType):
    key, label, input = "number", "Number (integer)", "number"
    allowed = frozenset({"min", "max"})

    def parse(self, raw, p):
        s = str(raw).strip()
        if not re.fullmatch(r"[+-]?\d{1,18}", s):
            raise ValueError("Enter a whole number.")
        n = int(s)
        _check_range(Decimal(n), p)
        return n


class DecimalType(ParamType):
    key, label, input = "decimal", "Decimal", "decimal"
    allowed = frozenset({"min", "max"})

    def parse(self, raw, p):
        try:
            d = Decimal(str(raw).strip())
        except InvalidOperation:
            raise ValueError("Enter a valid decimal number.") from None
        if not d.is_finite():
            raise ValueError("Enter a valid decimal number.")
        _check_range(d, p)
        return format(d, "f")


def _check_range(d: Decimal, p):
    lo, hi = p.get("min"), p.get("max")
    if lo is not None and d < Decimal(str(lo)):
        raise ValueError(f"Must be at least {lo}.")
    if hi is not None and d > Decimal(str(hi)):
        raise ValueError(f"Must be at most {hi}.")


class BooleanType(ParamType):
    key, label, input = "boolean", "Boolean (checkbox)", "checkbox"

    def parse(self, raw, p):
        return bool(raw)

    def to_raw(self, value):
        return bool(value)


class SelectType(ParamType):
    key, label, input = "select", "Select (dropdown)", "select"
    allowed = frozenset({"choices"})

    def parse(self, raw, p):
        s = str(raw)
        if s not in choice_values(p):
            raise ValueError("Choose one of the available options.")
        return s


class MultiSelectType(ParamType):
    key, label, input = "multiselect", "Multi-select", "multiselect"
    allowed = frozenset({"choices"})

    def parse(self, raw, p):
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        valid = set(choice_values(p))
        for item in items:
            if str(item) not in valid:
                raise ValueError(f"'{item}' is not an available option.")
        return [str(i) for i in items]

    def to_raw(self, value):
        return list(value) if isinstance(value, (list, tuple)) else ([] if value in (None, "") else [str(value)])


class DateType(ParamType):
    key, label, input = "date", "Date", "date"

    def parse(self, raw, p):
        try:
            return datetime.date.fromisoformat(str(raw).strip()).isoformat()
        except ValueError:
            raise ValueError("Enter a valid date (YYYY-MM-DD).") from None


class DateTimeType(ParamType):
    key, label, input = "datetime", "Date and time", "datetime-local"

    def parse(self, raw, p):
        s = str(raw).strip().replace(" ", "T")
        try:
            return datetime.datetime.fromisoformat(s).isoformat(timespec="seconds")
        except ValueError:
            raise ValueError("Enter a valid date and time.") from None


TYPES: dict[str, ParamType] = {}


def register_type(t: ParamType) -> None:
    """Add a parameter type. Extension point for future types."""
    TYPES[t.key] = t


for _t in (TextType, TextareaType, NumberType, DecimalType, BooleanType, PasswordType,
           SelectType, MultiSelectType, DateType, DateTimeType, FileType, DirectoryType):
    register_type(_t())


# ---------------------------------------------------------------- choices ---
def normalize_choices(raw):
    """Accept ["a", "b"] or [{"value": "a", "label": "A"}]; return list of dicts."""
    out = []
    for c in raw or []:
        if isinstance(c, dict):
            out.append({"value": str(c.get("value", "")), "label": str(c.get("label", c.get("value", "")))})
        else:
            out.append({"value": str(c), "label": str(c)})
    return out


def choice_values(p):
    return [c["value"] for c in normalize_choices(p.get("choices"))]


# ------------------------------------------------------------- definitions ---
def _is_number(v):
    return isinstance(v, (int, float, str)) and not isinstance(v, bool)


def validate_parameters(raw_list):
    """Validate a list of parameter definitions.

    Returns ``(clean_list, errors)``. Unknown keys are preserved so metadata
    stays forward-compatible.
    """
    errors = []
    if not isinstance(raw_list, list):
        return [], ["'parameters' must be a list."]

    clean_list, names, args, positions = [], set(), {}, {}
    for idx, raw in enumerate(raw_list, start=1):
        where = f"Parameter #{idx}"
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be an object.")
            continue
        p = dict(raw)
        name = p.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append(f"{where}: name must start with a letter/underscore and contain only letters, digits and underscores.")
            continue
        where = f"Parameter '{name}'"
        if name in names:
            errors.append(f"{where}: duplicate name.")
            continue
        names.add(name)
        n_before = len(errors)

        label = p.get("label")
        if label is None or label == "":
            p["label"] = name.replace("_", " ").title()
        elif not isinstance(label, str) or len(label) > 120:
            errors.append(f"{where}: label must be text up to 120 characters.")

        ptype = p.get("type", "text")
        if ptype not in TYPES:
            errors.append(f"{where}: unknown type '{ptype}'. Supported: {', '.join(TYPES)}.")
            continue
        p["type"] = ptype
        t = TYPES[ptype]

        for key in ("required", "editable_at_runtime"):
            if key in p and not isinstance(p[key], bool):
                errors.append(f"{where}: '{key}' must be true or false.")
        p.setdefault("required", False)
        p.setdefault("editable_at_runtime", True)
        if ptype == "boolean":
            p["required"] = False

        for key in ("help", "placeholder"):
            if p.get(key) is None:
                p.pop(key, None)
            elif not isinstance(p[key], str) or len(p[key]) > 1000:
                errors.append(f"{where}: '{key}' must be text up to 1000 characters.")

        # Type-specific keys
        for key in TYPED_KEYS - t.allowed:
            if p.get(key) not in (None, "", []):
                errors.append(f"{where}: '{key}' is not valid for type '{ptype}'.")
            p.pop(key, None)
        for key in ("min", "max"):
            if p.get(key) in (None, ""):
                p.pop(key, None)
            elif key in t.allowed:
                if not _is_number(p[key]):
                    errors.append(f"{where}: '{key}' must be a number.")
                else:
                    try:
                        Decimal(str(p[key]))
                    except InvalidOperation:
                        errors.append(f"{where}: '{key}' must be a number.")
        if "min" in p and "max" in p:
            try:
                if Decimal(str(p["min"])) > Decimal(str(p["max"])):
                    errors.append(f"{where}: min is greater than max.")
            except InvalidOperation:
                pass  # already reported above
        for key in ("min_length", "max_length"):
            if p.get(key) in (None, ""):
                p.pop(key, None)
            elif key in t.allowed:
                if isinstance(p[key], bool) or not isinstance(p[key], int) or p[key] < 0:
                    errors.append(f"{where}: '{key}' must be a non-negative whole number.")
        if p.get("pattern") in (None, ""):
            p.pop("pattern", None)
        elif "pattern" in t.allowed:
            try:
                re.compile(p["pattern"])
            except (re.error, TypeError) as exc:
                errors.append(f"{where}: invalid pattern ({exc}).")
        if ptype in ("select", "multiselect"):
            ch = p.get("choices")
            if not isinstance(ch, list) or not ch:
                errors.append(f"{where}: needs at least one choice.")
            else:
                ok = True
                for c in ch:
                    if isinstance(c, dict):
                        if not isinstance(c.get("value"), (str, int, float)) or c.get("value") == "":
                            ok = False
                    elif not isinstance(c, (str, int, float)) or isinstance(c, bool) or str(c) == "":
                        ok = False
                if not ok:
                    errors.append(f"{where}: each choice must be a non-empty value or {{value, label}}.")
                elif len(set(choice_values(p))) != len(ch):
                    errors.append(f"{where}: duplicate choice values.")

        # Passing to the script
        pass_as = p.get("pass_as", "flag")
        if pass_as not in PASS_AS:
            errors.append(f"{where}: pass_as must be one of {', '.join(PASS_AS)}.")
            pass_as = "flag"
        p["pass_as"] = pass_as
        if pass_as == "flag":
            arg = p.get("arg") or "--" + name.replace("_", "-")
            if not isinstance(arg, str) or not ARG_RE.match(arg):
                errors.append(f"{where}: arg must look like --option-name.")
            else:
                if arg in args:
                    errors.append(f"{where}: argument {arg} is already used by '{args[arg]}'.")
                args[arg] = name
            p.pop("env_name", None)
            p.pop("position", None)
        elif pass_as == "positional":
            if ptype == "boolean":
                errors.append(f"{where}: boolean parameters cannot be positional.")
            pos = p.get("position")
            if pos not in (None, ""):
                if isinstance(pos, bool) or not isinstance(pos, int) or pos < 1:
                    errors.append(f"{where}: position must be a whole number >= 1.")
                elif pos in positions:
                    errors.append(f"{where}: position {pos} is already used by '{positions[pos]}'.")
                else:
                    positions[pos] = name
            else:
                p.pop("position", None)
            p.pop("arg", None)
            p.pop("env_name", None)
        else:  # env
            env = p.get("env_name") or name.upper()
            if not isinstance(env, str) or not ENV_RE.match(env):
                errors.append(f"{where}: env_name is not a valid variable name.")
            elif env.upper() in FORBIDDEN_ENV:
                errors.append(f"{where}: environment variable {env} may not be set from a parameter.")
            p["env_name"] = env
            p.pop("arg", None)
            p.pop("position", None)

        # Default must itself be valid for the type
        if "default" in p and p["default"] is not None and not len(errors) > n_before:
            problem = _check_default(p)
            if problem:
                errors.append(f"{where}: default value — {problem}")
        if p.get("default") is None:
            p.pop("default", None)

        clean_list.append(p)

    return clean_list, errors


def _check_default(p):
    t = TYPES[p["type"]]
    d = p["default"]
    if p["type"] == "boolean":
        return None if isinstance(d, bool) else "must be true or false."
    if p["type"] == "multiselect" and not isinstance(d, list):
        return "must be a list of choices."
    if p["type"] != "multiselect" and isinstance(d, (list, dict)):
        return "has the wrong type."
    if d == "" or d == []:
        return None
    try:
        t.parse(d, p)
    except ValueError as exc:
        return str(exc)
    return None


# Former per-script options: every script is runnable, uses the default timeout from
# Settings and runs one at a time. Old metadata files may still contain them.
REMOVED_KEYS = ("enabled", "timeout", "allow_concurrent")


def validate_metadata(data):
    """Validate a whole metadata document. Returns the cleaned dict or raises
    ValidationError with every problem found."""
    errors = []
    if not isinstance(data, dict):
        raise ValidationError(["Metadata must be a JSON object."])
    clean = dict(data)
    sid = clean.get("id")
    if sid is not None and not (isinstance(sid, str) and re.match(r"^[0-9a-f]{12}$", sid)):
        errors.append("'id' must be 12 lowercase hex characters (ScriptHub writes it; remove it to get a new one).")

    def text(key, limit):
        v = clean.get(key)
        if v is None or v == "":
            clean.pop(key, None)
        elif not isinstance(v, str) or len(v) > limit:
            errors.append(f"'{key}' must be text up to {limit} characters.")

    text("name", 120)
    text("description", 2000)
    text("category", 60)
    text("interpreter", 260)
    text("working_directory", 1000)
    text("host", 255)
    if "host" in clean and clean["host"] != "local" and not HOST_RE.match(clean["host"]):
        errors.append("'host' must be 'local' or an SSH host alias (letters, digits, dots, hyphens, underscores).")

    plats = clean.get("platforms")
    if plats is None or plats == []:
        clean.pop("platforms", None)
    elif not isinstance(plats, list) or any(p not in ("windows", "linux", "macos") for p in plats):
        errors.append("'platforms' must be a list containing any of: windows, linux, macos.")
    else:
        clean["platforms"] = [p for p in ("windows", "linux", "macos") if p in plats]

    env = clean.get("environment")
    if env is None or env == {}:
        clean.pop("environment", None)
    elif not isinstance(env, dict):
        errors.append("'environment' must be an object of NAME: value pairs.")
    else:
        for k, v in env.items():
            if not isinstance(k, str) or not ENV_RE.match(k):
                errors.append(f"'environment' has an invalid variable name: {k!r}.")
            elif k.upper() in FORBIDDEN_ENV:
                errors.append(f"'environment' may not set {k}.")
            elif not isinstance(v, str) or "\x00" in v:
                errors.append(f"'environment' value for {k} must be text.")

    for key in REMOVED_KEYS:          # old per-script options; dropped on the next save
        clean.pop(key, None)

    params, perrors = validate_parameters(clean.get("parameters", []))
    errors.extend(perrors)
    clean["parameters"] = params

    if errors:
        raise ValidationError(errors)
    return clean


# ----------------------------------------------------------------- values ---
def _is_empty(raw, ptype):
    if raw is None or raw == [] or raw == "":
        return True
    return ptype not in ("password", "textarea") and isinstance(raw, str) and raw.strip() == ""


def parse_value(p, raw):
    """Return (value, error). ``value`` is None when an optional field is empty."""
    ptype = p["type"]
    t = TYPES[ptype]
    if ptype == "boolean":
        return bool(raw), None
    if _is_empty(raw, ptype):
        if p.get("required"):
            return None, "This field is required."
        return None, None
    try:
        return t.parse(raw, p), None
    except ValueError as exc:
        return None, str(exc)


def default_raw(p):
    """Default value in raw (form) representation."""
    t = TYPES[p["type"]]
    return t.to_raw(p.get("default"))


def defaults_raw(params):
    return {p["name"]: default_raw(p) for p in params}


def raw_from_post(params, post, prefix="p_"):
    """Collect raw values from a QueryDict. Locked parameters keep their default."""
    raw = {}
    for p in params:
        if not p.get("editable_at_runtime", True):
            raw[p["name"]] = default_raw(p)
            continue
        field = prefix + p["name"]
        if p["type"] == "boolean":
            raw[p["name"]] = post.get(field, "") in ("on", "1", "true", "True")
        elif p["type"] == "multiselect":
            raw[p["name"]] = post.getlist(field)
        else:
            raw[p["name"]] = post.get(field, "")
    return raw


def clean_values(params, raw):
    """Validate every parameter. Returns (values, errors) keyed by name."""
    values, errors = {}, {}
    for p in params:
        name = p["name"]
        value, err = parse_value(p, raw.get(name, default_raw(p)))
        if err:
            errors[name] = err
        else:
            values[name] = value
    return values, errors


def build_fields(params, raw, errors=None):
    """Template-ready field descriptions."""
    errors = errors or {}
    fields = []
    for p in params:
        name = p["name"]
        value = raw.get(name, default_raw(p))
        fields.append({
            "p": p,
            "name": name,
            "input_name": "p_" + name,
            "input": TYPES[p["type"]].input,
            "value": value,
            "selected": set(value) if isinstance(value, list) else set(),
            "choices": normalize_choices(p.get("choices")),
            "locked": not p.get("editable_at_runtime", True),
            "error": errors.get(name),
        })
    return fields


def hidden_pairs(params, raw):
    """(name, value) pairs to carry a validated submission to the next step."""
    pairs = []
    for p in params:
        if not p.get("editable_at_runtime", True):
            continue
        v = raw.get(p["name"])
        if p["type"] == "boolean":
            if v:
                pairs.append(("p_" + p["name"], "on"))
        elif p["type"] == "multiselect":
            pairs.extend(("p_" + p["name"], x) for x in (v or []))
        else:
            pairs.append(("p_" + p["name"], "" if v is None else str(v)))
    return pairs


# -------------------------------------------------------------- arguments ---
def _as_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


def build_arguments(params, values, mask_secrets=False):
    """Turn validated values into (argv_list, env_dict). Pure list building -
    nothing here is ever joined into a shell string."""
    flags, positional, env = [], [], {}
    order = {p["name"]: i for i, p in enumerate(params)}
    for p in params:
        name, value = p["name"], values.get(p["name"])
        secret = p["type"] == "password"
        shown = lambda v: SECRET_MASK if (mask_secrets and secret) else v  # noqa: E731
        mode = p.get("pass_as", "flag")
        if mode == "flag":
            arg = p.get("arg") or "--" + name.replace("_", "-")
            if p["type"] == "boolean":
                if value:
                    flags.append(arg)
            elif value is None:
                continue
            elif isinstance(value, list):
                for item in value:
                    flags.extend([arg, item])
            else:
                flags.extend([arg, shown(str(value))])
        elif mode == "positional":
            if value is None:
                continue
            items = value if isinstance(value, list) else [str(value)]
            positional.append((p.get("position") or 10_000 + order[name], order[name], [shown(i) for i in items]))
        else:
            if value is None:
                continue
            env[p.get("env_name") or name.upper()] = shown(_as_text(value))
    argv = list(flags)
    for _pos, _i, items in sorted(positional):
        argv.extend(items)
    return argv, env


def redact(params, values):
    """Copy of ``values`` safe to store or display (passwords masked)."""
    secret = {p["name"] for p in params if p["type"] == "password"}
    return {k: (SECRET_MASK if k in secret and v not in (None, "") else v) for k, v in values.items()}

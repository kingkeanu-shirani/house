"""Task specification schema, template expansion, variants and variable substitution.

A spec (one per catalog entry, authored in tasks/authored/*.json):
{
  "catalog_id": "T-...-01",            "title": "short name",
  "extends": "template_name",          "params": {"dish": "mug"},      (optional)
  "house": {"dwelling": "house", "require": ["garage"], "residents": "minimal", "pets": "none", "start": "2026-10-09T21:00:00"},
  "setup": [ops...],                   (see tasks/setup.py)
  "variants": [{"+setup": [...], "success": [...]} , ...],   seed % len(variants) selects one
  "instruction": "...", "phrasings": ["...", "...", "..."],
  "success": [...], "failure": [...], "preserve": [...], "side_effects": {...}, "milestones": {...},
  "order": [...], "deadlines": {...}, "rubric": {...}, "questions": {...}, "allow": [...],
  "strict_blocks": true,               (refused unsafe/private/unauthorized attempts fail the task)
  "budget": {"steps": 300, "time_s": 3600},
  "risk_level": 0..3, "fidelity": "S"|"G"|"P-approx", "target_fidelity": "S"|"G"|"P",
  "reference": [plan steps], "wrong": [{"name": "...", "plan": [plan steps], "camera": true}],
  "perception": "realistic",           (optional; default oracle)
  "camera": "geometric",               (optional; default room: the robot's posed cameras, kernel v3.0)
  "notes": "..."
}
Kernel v3.1 (TIER1_SWEEP 16.5; docs/TASK_SPEC.md):
  "schema": 3                          a spec authored under tier 1: generation-2 houses ("house": {"gen": 1|2}
                                       overrides), agent-legal waits in plans (wait_until compiles to wait(until=)),
                                       the notice bus, a default requester, the schema-3 lints
  "request": {"from", "channel", "room", "gesture", "relayed_by", "subject", "phrasings_by_role"}
  "never", "always", "responses"       temporal keys (kernel/monitor.py); "+never" etc. append in templates/variants
  "milestones": {name: P | {"pred": P, "held_for_s": N} | {"pred": P, "count": true}}
  "deadlines": {name: seconds | {"by": "HH:MM"} | {"within_s": N, "after": milestone | "phase:<id>"}}
  "phases": [{"id", "start", "request", "success", "failure", "never", "always", "responses", "milestones",
              "deadlines", "questions", "budget", "ends", "supersedes", "rubric"}]   multi-phase episodes (kernel/phases.py);
                                       a spec has either top-level instruction / success or phases
  plan steps {"phase_done": true} and {"wait_for": [notice kinds], "max_s": N}
A spec that uses the camera setup ops (camera, view, track_view, view_watch), the face / peek verbs or camera_log
predicates must declare "camera": "geometric"; a "schema": 3 geometric spec also needs a wrong plan marked
"camera": true that fails for a camera reason (did not look, wrong viewpoint, dark, glare, missed the back row).
Templates live in tasks/templates/*.json as {"templates": {name: partial spec}} and use
"{{param}}" placeholders; the authored spec's fields override the template's.
"""
import copy
import json
import re
from pathlib import Path

from ..kernel.errors import SpecError

ROOT = Path(__file__).resolve().parent.parent.parent
if not (ROOT / "tasks").is_dir():
    ROOT = Path(__file__).resolve().parent.parent / "resources"
VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
PARAM = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
LIST_KEYS = ("setup", "success", "failure", "preserve", "reference", "wrong", "phrasings", "allow", "order", "never",
             "always", "responses", "phases")
TASK_KEYS = ("success", "failure", "preserve", "side_effects", "milestones", "order", "deadlines", "rubric",
             "questions", "allow", "required_milestones", "never", "always", "responses", "phases", "request")
ALLOWED = {"catalog_id", "title", "extends", "params", "house", "setup", "variants", "instruction", "phrasings",
           "budget", "risk_level", "fidelity", "target_fidelity", "reference", "wrong", "notes", "strict_blocks",
           "family", "approval", "tags", "perception", "camera", "schema"} | set(TASK_KEYS)
SCHEMAS = (2, 3)
PHASE_KEYS = {"id", "start", "request", "instruction", "phrasings", "success", "failure", "never", "always",
              "responses", "milestones", "deadlines", "questions", "budget", "ends", "supersedes", "rubric", "notes",
              "optional"}
REQUEST_KEYS = {"id", "from", "channel", "room", "gesture", "relayed_by", "subject", "phrasings", "phrasings_by_role",
                "note"}
REQUEST_CHANNELS = ("in_person", "phone", "intercom", "app", "note", "overheard", "text")
CAMERA_MODES = ("room", "geometric")
CAMERA_SETUP_OPS = ("camera", "view", "track_view", "view_watch")
CAMERA_VERBS = ("face", "peek")
NEW_SPEC_SCHEMA = 3          # specs authored under tier 1 (TIER1_INTEGRATION 1.13)

_TEMPLATES = None


def templates():
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = {}
        folder = ROOT / "tasks" / "templates"
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, body in data.get("templates", {}).items():
                if name in _TEMPLATES:
                    raise SpecError("Template {} defined twice".format(name))
                _TEMPLATES[name] = body
    return _TEMPLATES


def reset_templates():
    global _TEMPLATES
    _TEMPLATES = None


def _fill_params(value, params):
    if isinstance(value, str):
        whole = PARAM.fullmatch(value)
        if whole:
            key = whole.group(1)
            if key not in params:
                raise SpecError("Missing template parameter " + key)
            return copy.deepcopy(params[key])
        def repl(match):
            if match.group(1) not in params:
                raise SpecError("Missing template parameter " + match.group(1))
            return str(params[match.group(1)])
        return PARAM.sub(repl, value)
    if isinstance(value, list):
        return [_fill_params(v, params) for v in value]
    if isinstance(value, dict):
        return {_fill_params(k, params) if isinstance(k, str) else k: _fill_params(v, params) for k, v in value.items()}
    return value


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key.startswith("+"):
            name = key[1:]
            result[name] = list(result.get(name, [])) + copy.deepcopy(value)
        elif key in ("house", "budget", "side_effects", "milestones", "deadlines", "questions") and isinstance(value, dict) and isinstance(result.get(key), dict):
            merged = dict(result[key])
            merged.update(copy.deepcopy(value))
            result[key] = merged
        else:
            result[key] = copy.deepcopy(value)
    return result


def expand(spec):
    """Resolve extends/params. Variants stay unresolved (chosen per seed)."""
    unknown = set(spec) - ALLOWED - {k for k in spec if k.startswith("+")}
    if unknown:
        raise SpecError("Unknown spec fields in {}: {}".format(spec.get("catalog_id"), ", ".join(sorted(unknown))))
    if "extends" not in spec:
        return merge({}, spec)
    name = spec["extends"]
    if name not in templates():
        raise SpecError("Unknown template " + name)
    body = templates()[name]
    params = dict(body.get("defaults", {}))
    params.update(spec.get("params", {}))
    base = _fill_params({k: v for k, v in body.items() if k != "defaults"}, params)
    own = {k: v for k, v in spec.items() if k not in ("extends", "params")}
    return merge(base, own)


def choose_variant(spec, seed):
    variants = spec.get("variants")
    if not variants:
        return copy.deepcopy(spec), None
    index = seed % len(variants)
    chosen = merge({k: v for k, v in spec.items() if k != "variants"}, variants[index])
    return chosen, index


def substitute(value, bindings):
    if isinstance(value, str):
        whole = VAR.fullmatch(value)
        if whole and whole.group(1) in bindings:
            return copy.deepcopy(bindings[whole.group(1)])
        def repl(match):
            name = match.group(1)
            if name not in bindings:
                raise SpecError("Unbound variable {" + name + "}")
            bound = bindings[name]
            return bound if isinstance(bound, str) else json.dumps(bound)
        return VAR.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, bindings) for v in value]
    if isinstance(value, dict):
        return {substitute(k, bindings) if isinstance(k, str) else k: substitute(v, bindings) for k, v in value.items()}
    return value


def check_structure(spec):
    """Cheap structural checks used by the compiler before verification. Each variant is checked as merged with
    the base spec (fields may live in the base or in every variant)."""
    if spec.get("variants"):
        problems = []
        for index, variant in enumerate(spec["variants"]):
            merged = merge({k: v for k, v in spec.items() if k != "variants"}, variant)
            for problem in _check_one(merged):
                label = "variant {}: {}".format(index, problem)
                if problem not in problems and label not in problems:
                    problems.append(label)
        return problems
    return _check_one(spec)


def schema_of(spec):
    """The spec's schema (2 unless it declares "schema": 3)."""
    value = (spec or {}).get("schema", 2)
    return value if isinstance(value, int) and not isinstance(value, bool) else 2


def _check_one(spec):
    problems = []
    if "schema" in spec and spec["schema"] not in SCHEMAS:
        problems.append("schema must be 2 or 3")
    if spec.get("phases") is not None:
        problems.extend(phase_problems(spec))
        for key in ("catalog_id", "reference"):
            if key not in spec:
                problems.append("missing " + key)
    else:
        for key in ("catalog_id", "instruction", "success", "reference"):
            if key not in spec and not (key == "success" and spec.get("rubric")):
                problems.append("missing " + key)
    phrasings = spec.get("phrasings", [])
    if spec.get("instruction") and len(phrasings) < 3:
        problems.append("needs at least three phrasings")
    if spec.get("request") is not None:
        problems.extend(request_problems(spec["request"], "request"))
    problems.extend(temporal_problems(spec))
    wrong = spec.get("wrong", [])
    if not wrong:
        problems.append("needs at least one plausible wrong plan")
    for item in wrong:
        if not isinstance(item, dict) or "plan" not in item or "name" not in item:
            problems.append("wrong plans need name and plan")
    if spec.get("risk_level") not in (0, 1, 2, 3):
        problems.append("risk_level must be 0..3")
    problems.extend(camera_problems(spec))
    return problems


def request_problems(request, where):
    if not isinstance(request, dict):
        return ["{} is an object".format(where)]
    problems = []
    unknown = set(request) - REQUEST_KEYS
    if unknown:
        problems.append("{}: unknown keys {}".format(where, ", ".join(sorted(unknown))))
    if request.get("channel") is not None and request["channel"] not in REQUEST_CHANNELS:
        problems.append("{}: channel is one of {}".format(where, ", ".join(REQUEST_CHANNELS)))
    gesture = request.get("gesture")
    if gesture is not None and (not isinstance(gesture, dict) or not ({"point_at", "place"} & set(gesture))):
        problems.append("{}: gesture is {{\"point_at\": id}} or {{\"place\": id, \"spos\": [x, y]}}".format(where))
    if request.get("note") is not None and (not isinstance(request["note"], str) or request.get("channel") != "note"):
        problems.append("{}: note names the written note (an entity) of a request on the note channel".format(where))
    return problems


def phase_problems(spec):
    """The phases lint (TIER1_SWEEP 9): a spec has phases or top-level instruction / success, never both; every phase
    has a unique id, a request with three phrasings (or an instruction with phrasings), success predicates (or a
    rubric), known keys, a valid start / ends / supersedes; "optional": true only on a phase that starts on its own
    (start when / at_s: an interruption that may not happen; not started, it is unscored)."""
    phases = spec.get("phases")
    if not isinstance(phases, list) or not phases:
        return ["phases is a non-empty list"]
    problems = []
    for key in ("instruction", "success", "rubric"):
        if spec.get(key):
            problems.append("a spec with phases has no top-level {} (phases carry them)".format(key))
    ids = [p.get("id") for p in phases if isinstance(p, dict)]
    if len(ids) != len(phases) or not all(isinstance(i, str) and i for i in ids):
        problems.append("every phase is an object with an id")
        return problems
    if len(set(ids)) != len(ids):
        problems.append("phase ids are unique")
    for index, phase in enumerate(phases):
        where = "phase " + phase["id"]
        unknown = set(phase) - PHASE_KEYS
        if unknown:
            problems.append("{}: unknown keys {}".format(where, ", ".join(sorted(unknown))))
        request = phase.get("request")
        texts = (request or {}).get("phrasings") or phase.get("phrasings") or []
        if request is not None:
            problems.extend(request_problems(request, where + " request"))
        if len(texts) < 3:
            problems.append("{}: its request needs at least three phrasings".format(where))
        if not phase.get("success") and not phase.get("rubric"):
            problems.append("{}: missing success".format(where))
        start = phase.get("start")
        if index == 0 and start is not None:
            problems.append("{}: the first phase starts at reset (no start)".format(where))
        if start is not None:
            keys = set(start) if isinstance(start, dict) else set()
            kinds = keys & {"after", "at_s", "when"}
            if not isinstance(start, dict) or len(kinds) != 1 or keys - {"after", "at_s", "when", "gap_s", "robot",
                                                                          "events"}:
                problems.append("{}: start is one of after (+gap_s, robot, events), at_s or when".format(where))
            elif "after" in start and start["after"] not in ids[:index]:
                problems.append("{}: starts after an earlier phase".format(where))
            elif ("gap_s" in start or "robot" in start or "events" in start) and "after" not in start:
                problems.append("{}: gap_s, robot and events belong to an after start".format(where))
            if isinstance(start, dict) and start.get("robot") not in (None, "dock", "stay"):
                problems.append("{}: start.robot is dock or stay".format(where))
        ends = phase.get("ends", "done")
        if ends != "done" and not (isinstance(ends, dict) and len(ends) == 1 and set(ends) <= {"at_s", "when"}):
            problems.append("{}: ends is done, {{\"at_s\": N}} or {{\"when\": P}}".format(where))
        if phase.get("supersedes") is not None and phase["supersedes"] not in ids:
            problems.append("{}: supersedes an unknown phase".format(where))
        if phase.get("optional") is not None and (not isinstance(phase["optional"], bool) or (
                phase["optional"] and not (isinstance(start, dict) and ({"when", "at_s"} & set(start))))):
            problems.append("{}: optional is true only for a phase that starts at_s or when".format(where))
    return problems


def temporal_problems(spec):
    """Validate never / always / responses / extended milestones and deadlines (kernel/monitor.py validate_task)."""
    if not any(spec.get(k) for k in ("never", "always", "responses", "phases", "milestones", "deadlines")):
        return []
    from ..kernel import monitor
    phases = spec.get("phases") if isinstance(spec.get("phases"), list) else None
    try:
        monitor.validate_task(spec, phases)
    except (ValueError, TypeError, KeyError) as exc:
        return ["temporal keys: {}".format(exc)]
    return []


def _walk(value, found):
    """Every op name, plan verb and predicate path in a spec part (setup ops, plans, predicates)."""
    if isinstance(value, dict):
        for key in ("op", "do", "path"):
            if isinstance(value.get(key), str):
                found.add((key, value[key]))
        for item in value.values():
            _walk(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk(item, found)
    return found


def camera_problems(spec):
    """The geometric camera's authoring rules (kernel v3.0; TIER1_CAMERA 15, TIER1_INTEGRATION 1.13): "camera" is
    room or geometric; camera setup ops, the face / peek verbs and camera_log predicates need "camera": "geometric";
    a "schema": 3 geometric spec has a wrong plan marked "camera": true (one that fails for a camera reason)."""
    problems = []
    mode = spec.get("camera")
    if mode is not None and mode not in CAMERA_MODES:
        problems.append("camera must be room or geometric")
    if mode != "geometric":
        found = _walk([spec.get(k) for k in ("setup", "reference", "wrong", "variants") + TASK_KEYS], set())
        uses = sorted({v for k, v in found if (k == "op" and v in CAMERA_SETUP_OPS) or (k == "do" and v in CAMERA_VERBS)
                       or (k == "path" and v.startswith("camera_log"))})
        if uses:
            problems.append("uses the geometric camera ({}) without \"camera\": \"geometric\"".format(", ".join(uses)))
    schema = spec.get("schema")
    if mode == "geometric" and isinstance(schema, int) and not isinstance(schema, bool) and schema >= NEW_SPEC_SCHEMA:
        if not any(isinstance(w, dict) and w.get("camera") is True for w in spec.get("wrong", [])):
            problems.append("a geometric spec needs a wrong plan marked \"camera\": true (fails for a camera reason)")
    return problems

"""Task specification schema, template expansion, variants and variable substitution.

A spec (one per catalog entry, authored in tasks/authored/*.json):
{
  "catalog_id": "T-...-01",            "title": "short name",
  "extends": "template_name",          "params": {"dish": "mug"},      (optional)
  "house": {"dwelling": "house", "require": ["garage"], "residents": "minimal", "pets": "none", "start": "2026-10-09T21:00:00"},
  "setup": [ops...],                   (see tasks/setup.py)
  "variants": [{"+setup": [...], "success": [...]} , ...],   variant_index(catalog_id, seed, n) selects one
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
Schema 4 (docs/design/VERIFY_HARDENING.md 5.4, 20.1; docs/TASK_SPEC.md "Schema 4"):
  "schema": 4                          turns VERIFY_HARDENING on (house ledger, labelled predicates, settle, look-ahead,
                                       content guards, evaluation-mode verification, evidence gating, batteries)
  "answers", "envelope", "absent_ok", "protections", "protect_map", "instruction_roles", "timing", "settle_s",
  "lookahead_s", "process", "fungible", "informative", "free_ok", "battery_exempt", "mustpass_exempt",
  "budget_reason", "clauses", "scene_exception", "track_open", "hold_limits"   (HARDENING_KEYS; variants also take
                                       "branch" and "budget"; "envelope" merges like side_effects, "+timing",
                                       "+battery_exempt", "+mustpass_exempt" append)
  {"label": L, "pred": P}              labelled predicates in success / failure / phases
  wrong[i]: "expect", "mistake", "mistake_fix", "variants", "modes", "expect_budget"   (14.1)
A spec whose catalog id is a catalog entry outside catalog/review/legacy_ids.json must declare "schema": 4
(L-SCHEMA). check_structure validates every schema-4 key it finds (in any schema: the R1 drafts live in schema 2/3
specs, inert until the switch) through the WP1 / WP3 / WP4 validators (ledger.validate_envelope /
validate_protections, monitor.validate_timing, predicates.validate_predicate with speech.validate_said and
reports.validate_expectation); the semantic lints are house_sim/tasks/lint.py (run(spec, analysis_record,
dynamic=None)).
"""
import copy
import hashlib
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
             "always", "responses", "phases", "timing", "battery_exempt", "mustpass_exempt", "protect_map")
TASK_KEYS = ("success", "failure", "preserve", "side_effects", "milestones", "order", "deadlines", "rubric",
             "questions", "allow", "required_milestones", "never", "always", "responses", "phases", "request",
             "timing")
# the schema-4 spec keys (VERIFY_HARDENING 20.1); runner.build copies envelope / protections / absent_ok / process /
# settle_s / lookahead_s / track_open / hold_limits into the task's hardening block, the verifier reads the rest
HARDENING_KEYS = ("answers", "envelope", "absent_ok", "protections", "protect_map", "instruction_roles", "timing",
                  "settle_s", "lookahead_s", "process", "fungible", "informative", "free_ok", "battery_exempt",
                  "mustpass_exempt", "budget_reason", "clauses", "scene_exception", "track_open", "hold_limits",
                  "branch")
ALLOWED = {"catalog_id", "title", "extends", "params", "house", "setup", "variants", "instruction", "phrasings",
           "budget", "risk_level", "fidelity", "target_fidelity", "reference", "wrong", "notes", "strict_blocks",
           "family", "approval", "tags", "perception", "camera", "schema"} | set(TASK_KEYS) | set(HARDENING_KEYS)
VARIANT_KEYS = {"name", "branch"}       # keys a variant may carry beyond ALLOWED (and "+<key>" appends)
SCHEMAS = (2, 3, 4)
PHASE_KEYS = {"id", "start", "request", "instruction", "phrasings", "success", "failure", "never", "always",
              "responses", "milestones", "deadlines", "questions", "budget", "ends", "supersedes", "rubric", "notes",
              "optional", "timing"}
REQUEST_KEYS = {"id", "from", "channel", "room", "gesture", "relayed_by", "subject", "phrasings", "phrasings_by_role",
                "note"}
REQUEST_CHANNELS = ("in_person", "phone", "intercom", "app", "note", "overheard", "text")
CAMERA_MODES = ("room", "geometric")
CAMERA_SETUP_OPS = ("camera", "view", "track_view", "view_watch")
CAMERA_VERBS = ("face", "peek")
TIER1_SCHEMA = 3             # specs authored under tier 1 (TIER1_INTEGRATION 1.13): generation-2 houses, the
                             # schema-3 lints (the geometric camera's camera wrong plan)
HARDENED_SCHEMA = 4          # the schema that turns VERIFY_HARDENING on (5.4)
NEW_SPEC_SCHEMA = HARDENED_SCHEMA   # every spec authored after the switch (VERIFY_HARDENING 5.4, L-SCHEMA)
LEGACY_IDS_FILE = ROOT / "catalog" / "review" / "legacy_ids.json"
CATALOG_ENTRIES_FILE = ROOT / "catalog" / "entries.jsonl"
REASON_MIN = 30              # every escape's reason (6.12, 7.2, 10.7, 17, 18)
ROLES = ("destination", "target", "recipient", "answer", "context")
ANSWER_KINDS = ("number", "entity", "set", "word", "place", "time", "branch")
ANSWER_KEYS = {"bind", "kind", "tol", "min_cv", "group", "evidence", "derive", "domain", "values", "spread_s",
               "label", "claim", "always_needed"}
DERIVATIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "verify" / "derivations.json"
_DERIVATIONS = None
EXEMPT_CATEGORIES = ("safe_superset", "world_resolves", "fungible_instance", "budget_defense",
                     "questions_always_required", "unrealisable", "other")
MUSTPASS_CATEGORIES = ("required_instance", "required_place", "order_matters", "exact_wording", "no_slack",
                       "unrealisable", "other")
POLICY_RE = re.compile(r"[BPM][0-9]+(\.[A-Za-z0-9_]+)*\Z")
WRONG_KEYS = {"name", "plan", "camera", "expect", "mistake", "mistake_fix", "variants", "modes", "expect_budget",
              "notes"}
WRONG_MODES = ("own", "eval")
SETTLE_MAX_S = 600
LOOKAHEAD_MIN_S, LOOKAHEAD_MAX_S = 3600, 86400

_TEMPLATES = None
_LEGACY = None
_CATALOG_IDS = None


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


DICT_MERGE_KEYS = ("house", "budget", "side_effects", "milestones", "deadlines", "questions", "envelope")


def merge(base, override):
    """base with override's keys: "+<key>" appends override's list to base's (a SpecError unless both are lists),
    DICT_MERGE_KEYS merge one level, every other key replaces."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key.startswith("+"):
            name = key[1:]
            if not isinstance(value, list) or not isinstance(result.get(name, []), list):
                raise SpecError("\"{}\" appends a list to the list {} (not {} to {})".format(
                    key, name, type_name(value), type_name(result.get(name, []))))
            result[name] = list(result.get(name, [])) + copy.deepcopy(value)
        elif key in DICT_MERGE_KEYS and isinstance(value, dict) and isinstance(result.get(key), dict):
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


VARIANT_SALT = "house_ai/variant/v1"


def variant_index(catalog_id, seed, n):
    """The variant a seed selects among n (review H5 / I1: with seed % n every authored n divided 900, and the printed
    address 100 + seed % 900 gave the variant away, on the hidden test seeds too). The seeds are taken in blocks of n
    consecutive seeds; within block b = seed // n the variants are rotated by a domain-separated SHA-256 of (catalog
    id, n, b): variant = (seed % n + rotation) % n. Knowing seed % n, or anything short of the whole seed, tells nothing
    about the variant (the rotation is uniform and independent per block), while every block of n consecutive seeds
    still holds every variant once -- so the verification seeds 0, 1, 2 cover every variant of a 2- or 3-variant spec,
    as seed % n did. Deterministic: the same (catalog id, seed) always selects the same variant. None when n is 0."""
    n = int(n)
    if n <= 0:
        return None
    block, offset = divmod(int(seed), n)
    text = "{}|{}|{}|{}".format(VARIANT_SALT, catalog_id if catalog_id is not None else "", n, block)
    rotation = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") % n
    return (offset + rotation) % n


def select_variant(spec, index):
    """(the spec merged with its variant `index`, index); (a copy of the spec, None) without variants."""
    variants = spec.get("variants")
    if not variants:
        return copy.deepcopy(spec), None
    index = int(index)
    if not 0 <= index < len(variants):
        raise SpecError("{}: no variant {}".format(spec.get("catalog_id"), index))
    chosen = merge({k: v for k, v in spec.items() if k != "variants"}, variants[index])
    return chosen, index


def choose_variant(spec, seed):
    """(the spec merged with the variant `seed` selects, its index): variant_index of the spec's catalog id and the
    seed; (a copy of the spec, None) without variants. Callers that hold a variant index use select_variant."""
    variants = spec.get("variants")
    if not variants:
        return copy.deepcopy(spec), None
    return select_variant(spec, variant_index(spec.get("catalog_id"), seed, len(variants)))


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


def type_name(value):
    """A JSON-ish name of a value's type for problem texts (null, true / false, number, string, list, object)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return "number {!r}".format(value)
    return {str: "a string", list: "a list", dict: "an object"}.get(type(value), type(value).__name__)


# the container types the checks iterate (shape_problems): a key of CONTAINER_LISTS holds a list, of CONTAINER_DICTS
# an object; a phase's PHASE_LISTS hold lists; a variant and a phase are objects
CONTAINER_LISTS = ("setup", "success", "failure", "preserve", "reference", "wrong", "phrasings", "allow", "order",
                   "never", "always", "responses", "phases", "timing", "required_milestones", "variants")
CONTAINER_DICTS = ("house", "budget", "side_effects", "milestones", "deadlines", "questions", "params")
PHASE_LISTS = ("success", "failure", "never", "always", "responses", "phrasings", "timing")
MALFORMED = (AttributeError, TypeError, KeyError, ValueError, IndexError)


def shape_problems(spec):
    """The container types the other checks iterate (any schema; review A-bugs-7: "success": -1 raised TypeError and
    aborted the whole catalog compile): every CONTAINER_LISTS key holds a list, every CONTAINER_DICTS key an object,
    each variant and phase is an object whose list keys hold lists, a "+<key>" appends a list, a wrong plan is an
    object with a list plan. check_structure returns these alone when there are any (the rest would read a malformed
    part)."""
    problems = []

    def scope_shape(scope, where, lists, dicts):
        for key in lists:
            if key in scope and not isinstance(scope[key], list):
                problems.append("{}{} is a list (not {})".format(where, key, type_name(scope[key])))
        for key in dicts:
            if key in scope and not isinstance(scope[key], dict):
                problems.append("{}{} is an object (not {})".format(where, key, type_name(scope[key])))
        for key in sorted(k for k in scope if isinstance(k, str) and k.startswith("+")):
            if not isinstance(scope[key], list):
                problems.append("{}\"{}\" appends a list (not {})".format(where, key, type_name(scope[key])))
        for key in ("wrong", "+wrong"):
            for index, item in enumerate(scope.get(key) if isinstance(scope.get(key), list) else ()):
                if not isinstance(item, dict):
                    problems.append("{}{}[{}] is an object (not {})".format(where, key, index, type_name(item)))
                elif "plan" in item and not isinstance(item["plan"], list):
                    problems.append("{}{}[{}].plan is a list of steps".format(where, key, index))
        for index, phase in enumerate(scope.get("phases") if isinstance(scope.get("phases"), list) else ()):
            if not isinstance(phase, dict):
                problems.append("{}phase {} is an object (not {})".format(where, index, type_name(phase)))
            else:
                scope_shape(phase, "{}phase {}: ".format(where, phase.get("id", index)), PHASE_LISTS, ())

    if not isinstance(spec, dict):
        return ["a spec is an object (not {})".format(type_name(spec))]
    scope_shape(spec, "", CONTAINER_LISTS, CONTAINER_DICTS)
    for index, variant in enumerate(spec.get("variants") if isinstance(spec.get("variants"), list) else ()):
        if not isinstance(variant, dict):
            problems.append("variant {} is an object (not {})".format(index, type_name(variant)))
        else:
            scope_shape(variant, "variant {}: ".format(index), CONTAINER_LISTS[:-1], CONTAINER_DICTS)
    return problems


def check_structure(spec):
    """Cheap structural checks used by the compiler before verification. Each variant is checked as merged with
    the base spec (fields may live in the base or in every variant). Never raises on a malformed spec: container
    types first (shape_problems: a spec with such problems gets only those), and a part a validator cannot read is
    a problem too (the compiler then marks the spec INVALID instead of aborting the catalog)."""
    shape = shape_problems(spec)
    if shape:
        return shape
    try:
        return _check_structure(spec)
    except SpecError as exc:
        return [str(exc)]
    except MALFORMED as exc:
        return ["malformed spec ({}: {})".format(type(exc).__name__, exc)]


def _check_structure(spec):
    if spec.get("variants"):
        problems = variant_key_problems(spec)
        for index, variant in enumerate(spec["variants"]):
            if not isinstance(variant, dict):
                continue
            merged = merge({k: v for k, v in spec.items() if k != "variants"}, variant)
            for problem in _check_one(merged):
                label = "variant {}: {}".format(index, problem)
                if problem not in problems and label not in problems:
                    problems.append(label)
        return problems
    return variant_key_problems(spec) + _check_one(spec)


def schema_of(spec):
    """The spec's schema (2 unless it declares "schema": 3 or 4)."""
    value = (spec or {}).get("schema", 2)
    return value if isinstance(value, int) and not isinstance(value, bool) else 2


def legacy_ids(reload=False):
    """The frozen set of catalog ids authored before the schema-4 switch (catalog/review/legacy_ids.json), or None
    when the file is not there (an installed package without the catalog)."""
    global _LEGACY
    if _LEGACY is None or reload:
        try:
            data = json.loads(LEGACY_IDS_FILE.read_text(encoding="utf-8"))
            _LEGACY = frozenset(data["ids"])
        except (OSError, ValueError, KeyError, TypeError):
            _LEGACY = False
    return _LEGACY or None


def catalog_ids(reload=False):
    """Every catalog entry id (catalog/entries.jsonl), or None without the catalog."""
    global _CATALOG_IDS
    if _CATALOG_IDS is None or reload:
        try:
            ids = set()
            for line in CATALOG_ENTRIES_FILE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ids.add(json.loads(line)["id"])
            _CATALOG_IDS = frozenset(ids)
        except (OSError, ValueError, KeyError, TypeError):
            _CATALOG_IDS = False
    return _CATALOG_IDS or None


def derivations(reload=False):
    """WP7's derivation table (house_sim/data/verify/derivations.json): {"ops": [...], "tiers": {word: level}}."""
    global _DERIVATIONS
    if _DERIVATIONS is None or reload:
        try:
            data = json.loads(DERIVATIONS_FILE.read_text(encoding="utf-8"))
            _DERIVATIONS = {"ops": sorted(data.get("derivations") or {}),
                            "tiers": {k: v for k, v in (data.get("tiers") or {}).items() if k != "about"}}
        except (OSError, ValueError, AttributeError):
            _DERIVATIONS = {"ops": [], "tiers": {}}
    return _DERIVATIONS


def schema_problems(spec):
    """The schema rules (VERIFY_HARDENING 5.4): "schema" is 2, 3 or 4, and a spec whose catalog id is a catalog entry
    outside the frozen legacy list (catalog/review/legacy_ids.json) declares "schema": 4 (L-SCHEMA). Ids that are no
    catalog entry (test specs, custom tasks) are not catalog ids; the compiler refuses authored specs with such ids."""
    problems = []
    raw = spec.get("schema")
    if "schema" in spec and (isinstance(raw, bool) or raw not in SCHEMAS):
        problems.append("schema must be 2, 3 or 4")
    cid = spec.get("catalog_id")
    if schema_of(spec) < NEW_SPEC_SCHEMA and isinstance(cid, str):
        legacy, known = legacy_ids(), catalog_ids()
        if legacy is not None and known is not None and cid in known and cid not in legacy:
            problems.append("L-SCHEMA: {} is not a legacy id (catalog/review/legacy_ids.json): a new spec declares "
                            "\"schema\": {}".format(cid, NEW_SPEC_SCHEMA))
    return problems


def _check_one(spec):
    problems = []
    problems.extend(schema_problems(spec))
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
    problems.extend(timing_problems(spec))
    wrong = spec.get("wrong", [])
    if not wrong:
        problems.append("needs at least one plausible wrong plan")
    for item in wrong:
        if not isinstance(item, dict) or "plan" not in item or "name" not in item:
            problems.append("wrong plans need name and plan")
    problems.extend(wrong_problems(spec))
    if spec.get("risk_level") not in (0, 1, 2, 3):
        problems.append("risk_level must be 0..3")
    problems.extend(camera_problems(spec))
    problems.extend(hardening_problems(spec))
    problems.extend(label_problems(spec))
    if schema_of(spec) >= HARDENED_SCHEMA:
        problems.extend(predicate_problems(spec))
    return problems


def _reason_ok(value):
    return isinstance(value, str) and len(value.strip()) >= REASON_MIN


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def milestone_names(spec):
    """Every milestone name of a spec and its phases (what a timing trigger / act may name)."""
    names = set((spec.get("milestones") or {}) if isinstance(spec.get("milestones"), dict) else ())
    for phase in spec.get("phases") or ():
        if isinstance(phase, dict) and isinstance(phase.get("milestones"), dict):
            names |= set(phase["milestones"])
    return names


def timing_problems(spec):
    """The `timing` key (VERIFY_HARDENING 8.5) of the spec and each phase, through WP3's monitor.validate_timing in
    its strict (authoring) form: the 30 s sample resolution rules included."""
    scopes = [("", spec.get("timing"))]
    for phase in spec.get("phases") or ():
        if isinstance(phase, dict) and phase.get("timing") is not None:
            scopes.append(("phase {}: ".format(phase.get("id")), phase.get("timing")))
    if all(items is None for _, items in scopes):
        return []
    from ..kernel import monitor
    names = milestone_names(spec)
    problems = []
    for where, items in scopes:
        if items is None:
            continue
        problems.extend(where + p for p in monitor.validate_timing(items, names, strict=True))
    return problems


def wrong_problems(spec):
    """The wrong-plan fields of 14.1 where given (any schema): expect patterns, mistake step indices, mistake_fix
    steps, variants, modes, expect_budget's reason. Schema 4 needs at least 2 wrong plans, each with expect and
    mistake (their specificity is lint L-EXPECT)."""
    problems = []
    wrong = [w for w in spec.get("wrong") or () if isinstance(w, dict)]
    hardened = schema_of(spec) >= HARDENED_SCHEMA
    for index, item in enumerate(wrong):
        where = "wrong plan {!r}".format(item.get("name", index))
        if hardened:
            unknown = set(item) - WRONG_KEYS
            if unknown:
                problems.append("{}: unknown keys {}".format(where, ", ".join(sorted(unknown))))
        if "expect" in item or hardened:
            if not _strings(item.get("expect")) or not item.get("expect"):
                problems.append("{}: expect is a non-empty list of failure patterns (14.1)".format(where))
        if "mistake" in item or hardened:
            mistake = item.get("mistake")
            plan = item.get("plan") if isinstance(item.get("plan"), list) else []
            if not isinstance(mistake, list) or not mistake or not all(
                    isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(plan) for i in mistake):
                problems.append("{}: mistake lists the plan's top-level step indices (14.1)".format(where))
            elif len(set(mistake)) != len(mistake):
                problems.append("{}: mistake lists each step once".format(where))
        if "mistake_fix" in item and not (isinstance(item["mistake_fix"], list) and all(
                isinstance(s, dict) for s in item["mistake_fix"])):
            problems.append("{}: mistake_fix is a list of plan steps".format(where))
        if "variants" in item:
            values = item["variants"]
            if not isinstance(values, list) or not values or not all(
                    isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
                problems.append("{}: variants lists variant indices of this spec".format(where))
        if "modes" in item and (not _strings(item["modes"]) or not item["modes"] or
                                set(item["modes"]) - set(WRONG_MODES)):
            problems.append("{}: modes is a non-empty subset of own, eval".format(where))
        if "expect_budget" in item and not _reason_ok(item["expect_budget"]):
            problems.append("{}: expect_budget is a reason of at least {} characters".format(where, REASON_MIN))
        if "camera" in item and not isinstance(item["camera"], bool):
            problems.append("{}: camera is true or false".format(where))
    if hardened and len(wrong) < 2:
        problems.append("schema 4 needs at least 2 wrong plans (14.1)")
    return problems


def hardening_problems(spec):
    """The schema-4 keys (VERIFY_HARDENING 20.1) where given, statically: the envelope and protections through WP1's
    ledger.validate_envelope / validate_protections, answers (9.1), label lists, settle and look-ahead lengths,
    instruction roles (13.7), the protect map's form (7.2; L-PROTECT resolves its references), exemption proposals
    (10.7, 11.3), escape reasons (18), clauses, and the keys a variant may carry. Checked in every schema (the R1
    drafts live in schema 2/3 specs, inert until the switch)."""
    problems = []
    if not any(k in spec for k in HARDENING_KEYS):
        return problems
    from ..kernel import ledger
    if "envelope" in spec:
        problems.extend(ledger.validate_envelope(spec["envelope"]))
    if "protections" in spec:
        problems.extend(ledger.validate_protections(spec["protections"]))
    if "answers" in spec:
        problems.extend(answers_problems(spec["answers"]))
    for key in ("absent_ok", "process", "free_ok", "fungible"):
        if key in spec and not (isinstance(spec[key], list) and all(isinstance(v, str) and v for v in spec[key])):
            problems.append("{} lists names".format(key))
    if "settle_s" in spec:
        value = spec["settle_s"]
        if not _number(value) or not 0 <= value <= SETTLE_MAX_S:
            problems.append("settle_s is seconds 0..{} (default 60)".format(SETTLE_MAX_S))
    if "lookahead_s" in spec:
        value = spec["lookahead_s"]
        if not _number(value) or not (value == 0 or LOOKAHEAD_MIN_S <= value <= LOOKAHEAD_MAX_S):
            problems.append("lookahead_s is 0 (off) or {}..{} seconds".format(LOOKAHEAD_MIN_S, LOOKAHEAD_MAX_S))
    if "track_open" in spec and not isinstance(spec["track_open"], bool):
        problems.append("track_open is true or false")
    if "hold_limits" in spec:
        limits = spec["hold_limits"]
        if not isinstance(limits, dict) or not all(isinstance(k, str) and k and _number(v) and v >= 0
                                                   for k, v in limits.items()):
            problems.append("hold_limits maps ids to seconds")
    if "instruction_roles" in spec:
        roles = spec["instruction_roles"]
        if not isinstance(roles, dict):
            problems.append("instruction_roles maps placeholders to roles")
        else:
            for name, role in roles.items():
                if not isinstance(name, str) or not VAR.fullmatch("{" + name + "}"):
                    problems.append("instruction_roles: {!r} is not a placeholder name".format(name))
                if role not in ROLES:
                    problems.append("instruction_roles[{}] is one of {}".format(name, ", ".join(ROLES)))
    if "protect_map" in spec:
        problems.extend(protect_map_form(spec["protect_map"]))
    for key, categories in (("battery_exempt", EXEMPT_CATEGORIES), ("mustpass_exempt", MUSTPASS_CATEGORIES)):
        if key in spec:
            problems.extend(exemption_problems(key, spec[key], categories))
    if "informative" in spec:
        value = spec["informative"]
        if not isinstance(value, dict) or not all(isinstance(k, str) and k and _reason_ok(v) for k, v in value.items()):
            problems.append("informative maps features to reasons of at least {} characters".format(REASON_MIN))
    for key in ("budget_reason", "scene_exception"):
        if key in spec and not _reason_ok(spec[key]):
            problems.append("{} is a reason of at least {} characters".format(key, REASON_MIN))
    if "clauses" in spec:
        problems.extend(clauses_form(spec["clauses"]))
    if "branch" in spec and (isinstance(spec["branch"], bool) or not isinstance(spec["branch"], (str, int))):
        problems.append("branch is a word or a number")
    return problems


def answers_problems(answers):
    """The form of an `answers` block (9.1): {name: {"bind", "kind", ...}}; kinds, groups, evidence and derive."""
    if not isinstance(answers, dict):
        return ["answers is an object {name: {bind, kind, ...}}"]
    table = derivations()
    tiers, ops = set(table["tiers"]), set(table["ops"])
    problems = []
    for name, body in sorted(answers.items(), key=lambda kv: str(kv[0])):
        where = "answers.{}".format(name)
        if not isinstance(body, dict):
            problems.append(where + " is an object")
            continue
        unknown = set(body) - ANSWER_KEYS
        if unknown:
            problems.append("{}: unknown keys {}".format(where, ", ".join(sorted(unknown))))
        if not isinstance(body.get("bind"), str) or not body.get("bind"):
            problems.append(where + ": bind names a binding (or _branch)")
        kind = body.get("kind")
        if kind not in ANSWER_KINDS:
            problems.append("{}: kind is one of {}".format(where, ", ".join(ANSWER_KINDS)))
        if kind == "branch" and body.get("bind") not in (None, "_branch"):
            problems.append(where + ": a branch answer binds _branch")
        for key in ("tol", "min_cv", "spread_s"):
            if key in body and not (_number(body[key]) and body[key] >= 0):
                problems.append("{}: {} is a nonnegative number".format(where, key))
        if "group" in body and not (isinstance(body["group"], str) or _strings(body["group"])):
            problems.append(where + ": group is a binding or a list of ids")
        for key in ("domain", "values", "always_needed"):
            if key in body and not isinstance(body[key], list):
                problems.append("{}: {} is a list".format(where, key))
        evidence = body.get("evidence")
        if evidence is not None:
            items = evidence if isinstance(evidence, list) else [evidence]
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("field"), str) or \
                        set(item) - {"entity", "field", "tier"}:
                    problems.append(where + ": evidence items are {entity?, field, tier}")
                    continue
                if tiers and item.get("tier") not in tiers:
                    problems.append("{}: evidence tier {!r} is one of {}".format(where, item.get("tier"),
                                                                                ", ".join(sorted(tiers))))
                if kind == "branch" and not item.get("entity"):
                    problems.append(where + ": branch evidence names its entity")
        derive = body.get("derive")
        if derive is not None:
            if not isinstance(derive, dict) or (ops and derive.get("op") not in ops):
                problems.append("{}: derive is {{op, over + field | of}} with op one of {}".format(
                    where, ", ".join(sorted(ops))))
            elif "of" in derive and not _strings(derive["of"]):
                problems.append(where + ": derive.of lists <entity>.<field> inputs")
            elif "of" not in derive and not ("over" in derive and isinstance(derive.get("field"), str)):
                problems.append(where + ": derive needs over + field or an of list")
    return problems


def protect_map_form(items):
    """The form of a protect_map (7.2): [{"item", "by": [references]} | {"item", "waive": reason}]."""
    if not isinstance(items, list):
        return ["protect_map is a list of {item, by | waive}"]
    problems = []
    for index, item in enumerate(items):
        where = "protect_map[{}]".format(index)
        if not isinstance(item, dict) or not isinstance(item.get("item"), str) or not item["item"].strip():
            problems.append(where + ": each entry has the analysis item text")
            continue
        if set(item) - {"item", "by", "waive", "note"}:
            problems.append("{}: keys are item, by, waive (and note)".format(where))
        if ("by" in item) == ("waive" in item):
            problems.append("{} ({!r}): by or waive, not both".format(where, item["item"][:40]))
        elif "by" in item and (not _strings(item["by"]) or not item["by"]):
            problems.append("{}: by lists guard references".format(where))
        elif "waive" in item and not _reason_ok(item["waive"]):
            problems.append("{}: waive is a reason of at least {} characters".format(where, REASON_MIN))
    return problems


def exemption_problems(key, items, categories):
    """battery_exempt / mustpass_exempt proposals (10.7, 11.3): [{"policy", "category", "reason", "seeds"}]."""
    if not isinstance(items, list):
        return ["{} is a list of proposals".format(key)]
    problems = []
    for index, item in enumerate(items):
        where = "{}[{}]".format(key, index)
        if not isinstance(item, dict):
            problems.append(where + " is an object")
            continue
        if set(item) - {"policy", "category", "reason", "seeds"}:
            problems.append(where + ": keys are policy, category, reason, seeds")
        if not isinstance(item.get("policy"), str) or not POLICY_RE.match(item["policy"]):
            problems.append(where + ": policy is a family id (B8.superset, M2.alt_place, ...)")
        if item.get("category") not in categories:
            problems.append("{}: category is one of {}".format(where, ", ".join(categories)))
        if not _reason_ok(item.get("reason")):
            problems.append("{}: a reason of at least {} characters".format(where, REASON_MIN))
        seeds = item.get("seeds", "all")
        if seeds != "all" and not (isinstance(seeds, list) and all(_number(s) for s in seeds)):
            problems.append(where + ": seeds is \"all\" or a list of seeds")
    return problems


def clauses_form(clauses):
    """The form of `clauses` (appendix entries, L-CLAUSES): [{"clause": text, "labels": [labels]} | {"clause",
    "waive": reason}]."""
    if not isinstance(clauses, list):
        return ["clauses is a list of {clause, labels | waive}"]
    problems = []
    for index, item in enumerate(clauses):
        where = "clauses[{}]".format(index)
        if not isinstance(item, dict) or not isinstance(item.get("clause"), str) or not item["clause"].strip():
            problems.append(where + ": each entry names its clause")
            continue
        if set(item) - {"clause", "labels", "waive"} or ("labels" in item) == ("waive" in item):
            problems.append(where + ": labels or waive")
        elif "labels" in item and (not _strings(item["labels"]) or not item["labels"]):
            problems.append(where + ": labels lists success / failure / timing labels")
        elif "waive" in item and not _reason_ok(item["waive"]):
            problems.append("{}: waive is a reason of at least {} characters".format(where, REASON_MIN))
    return problems


def labelled_items(items):
    """[(label or None, predicate)] of a success / failure list ({"label", "pred"} items unwrapped)."""
    out = []
    for item in items or ():
        if isinstance(item, dict) and "label" in item and "pred" in item:
            out.append((item.get("label"), item.get("pred")))
        else:
            out.append((None, item))
    return out


def label_problems(spec):
    """Labelled predicates (14.2) in success, failure and each phase: a label is a non-empty string without ':',
    used once per list, and the item has only label and pred."""
    scopes = [("", spec)] + [("phase {} ".format(p.get("id")), p) for p in spec.get("phases") or ()
                             if isinstance(p, dict)]
    problems = []
    for where, scope in scopes:
        for key in ("success", "failure"):
            seen = set()
            for index, item in enumerate(scope.get(key) or ()):
                if not (isinstance(item, dict) and "label" in item and "pred" in item):
                    continue
                label = item["label"]
                if set(item) - {"label", "pred"}:
                    problems.append("{}{} item {}: a labelled predicate has only label and pred".format(where, key,
                                                                                                    index))
                if not isinstance(label, str) or not label.strip() or ":" in label:
                    problems.append("{}{} item {}: a label is a non-empty string without ':'".format(where, key, index))
                elif label in seen:
                    problems.append("{}{}: label {!r} is used twice".format(where, key, label))
                seen.add(label)
    return problems


def _predicates(spec):
    """(where, predicate) for every success / failure predicate of the spec and its phases (labels unwrapped)."""
    scopes = [("", spec)] + [("phase {} ".format(p.get("id")), p) for p in spec.get("phases") or ()
                             if isinstance(p, dict)]
    for where, scope in scopes:
        for key in ("success", "failure"):
            for index, (_, pred) in enumerate(labelled_items(scope.get(key))):
                yield "{}{}[{}]".format(where, key, index), pred


def _report_bodies(rule, found):
    if isinstance(rule, dict):
        for key in ("report", "report_set"):
            if isinstance(rule.get(key), dict):
                found.append(rule[key])
        for value in rule.values():
            _report_bodies(value, found)
    elif isinstance(rule, list):
        for value in rule:
            _report_bodies(value, found)
    return found


def predicate_problems(spec):
    """Schema 4: every success and failure predicate is well formed (predicates.validate_predicate, which checks
    said bodies through speech.validate_said) and every report expectation passes reports.validate_expectation."""
    from ..kernel import reports
    from ..kernel.predicates import validate_predicate
    problems = []
    for where, pred in _predicates(spec):
        if isinstance(pred, str) and VAR.fullmatch(pred):
            continue                    # a whole predicate bound by the setup
        try:
            validate_predicate(pred)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            problems.append("{}: {}".format(where, exc))
            continue
        for body in _report_bodies(pred, []):
            problems.extend("{}: {}".format(where, p) for p in reports.validate_expectation(body))
    return problems


def variant_key_problems(spec):
    """A wrong plan's "variants" names variants the spec has (any schema); schema 4: a variant carries only spec
    keys, "+<key>" appends, "name" and "branch"."""
    n = len(spec.get("variants") or ())
    problems = []
    lists = [spec.get("wrong")] + [v.get(k) for v in spec.get("variants") or () if isinstance(v, dict)
                                   for k in ("wrong", "+wrong")]
    for items in lists:
        for item in items or ():
            values = item.get("variants") if isinstance(item, dict) else None
            if isinstance(values, list) and any(isinstance(v, int) and not isinstance(v, bool) and v >= n
                                                for v in values):
                problem = "wrong plan {!r}: variants lists variant indices of this spec (it has {})".format(
                    item.get("name"), n)
                if problem not in problems:
                    problems.append(problem)
    if schema_of(spec) < HARDENED_SCHEMA:
        return problems
    for index, variant in enumerate(spec.get("variants") or ()):
        if not isinstance(variant, dict):
            problems.append("variant {} is an object".format(index))
            continue
        unknown = sorted(k for k in variant if k.lstrip("+") not in ALLOWED and k not in VARIANT_KEYS)
        if unknown:
            problems.append("variant {}: unknown keys {}".format(index, ", ".join(unknown)))
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
    """Validate never / always / responses / extended milestones, deadlines and timing items (kernel/monitor.py
    validate_task; the timing key's authoring rules are timing_problems)."""
    if not any(spec.get(k) for k in ("never", "always", "responses", "phases", "milestones", "deadlines", "timing")):
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
    if mode == "geometric" and isinstance(schema, int) and not isinstance(schema, bool) and schema >= TIER1_SCHEMA:
        if not any(isinstance(w, dict) and w.get("camera") is True for w in spec.get("wrong", [])):
            problems.append("a geometric spec needs a wrong plan marked \"camera\": true (fails for a camera reason)")
    return problems

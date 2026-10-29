"""Schema-4 lints (docs/design/VERIFY_HARDENING.md 20.2, 7.5, 8.6, 9.1, 13.2-13.8, 14.3, 15.4, 17, 18; WP8).

    run(spec, analysis_record=None, dynamic=None, entry=None, record=None, as_if=False)
        -> [{"id": "L-...", "severity": "error" | "warning", "message": str}]   sorted, deterministic
    dynamic_facts(spec, seeds, gen=None, hardening=None, builds=None)   the dynamic lints' per-seed facts (builds)
    draft_protect_map(catalog_id=None, record=None)        the classifier's protect map draft (7.4, protect_rules.json)
    classify_protect_item(text)                            {"by", "rules", "restore"} for one analysis protect item
    protect_ref_problem(ref, ...) / normalise_item(text)   the 7.3 grammar, the 7.2 item normalisation
    clauses_of(text)                                       an appendix entry's clauses (L-CLAUSES)
    watch_problems(census=None, domains=None)              L-WATCH (6.3; the domain tests run it)
    analysis_record(catalog_id) / catalog_entry(catalog_id)

run() lints a schema-4 spec (or any spec with as_if=True: the R0 audit's as-if pass); a schema 2 / 3 spec gets only
L-SCHEMA (a catalog id outside catalog/review/legacy_ids.json must declare schema 4), so legacy outcomes never move.
analysis_record is the entry's analysis record (catalog/analysis/part_*.jsonl; None: the lints that read it are
skipped), entry the catalog entry ({"id", "kind", "text", ...}; None: looked up by catalog id). Every lint runs on each
variant merged with the base spec; a problem in several variants is reported once, prefixed with the variants.

Static lints: L-SCHEMA, L-PROTECT (7.5 rules 1-6; liveness 7.6 is the verifier's), L-ENVELOPE (static part),
L-LATCH, L-KIND, L-WEAK, L-EXISTS, L-ANSWERS (static part), L-BRANCH (static part), L-QUESTIONS (over the variants),
L-TIMING (the time answer; the window rules are spec.check_structure's through monitor.validate_timing), L-SPEECH,
L-ROLES, L-REQUEST, L-PRIV, L-EXPECT, L-NAMEDROOM, L-CLAUSES, L-ESCAPE (through escapes.price: limits are errors,
an escape still waiting for its accepted review record is a warning, so the verdict can be PENDING_REVIEW).
L-WATCH is a domain-test lint (watch_problems), not a per-spec one.

dynamic (the verifier's facts over the verification seeds V; dynamic_facts computes the build-time ones):
  {"seeds": [s, ...], "variants": {s: index}, "branches": {s: value}, "bindings": {s: {name: value}},
   "binding_types": {s: {name: "type:<t>" | "room:<t>" | [kinds]}} (entity / room id bindings),
   "instructions": {s: [texts]}, "free": {s: {label: bool}} (success predicates at t=0, the hardened labels),
   "timing_initial": {s: {label: bool}} (timing acts at t=0), "scene": {s: {placeholder: look-alike group size}},
   "type_counts": {s: {type: instances}}, "people": {s: [ids]},
   "ids": {s: {id: exists}} (the literal ids of the envelope, protections and protect map: spec_literal_ids),
   "budget": verify.budget_check(...), "coverage": seeds.coverage(...), "branch_cross": [[a, b, passes], ...]}
Every key is optional; a lint whose facts are missing is skipped (the verifier records it).
"""
import copy
import json
import re
from pathlib import Path

from ..kernel.errors import SpecError
from . import spec as spec_mod

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent.parent / "data" / "verify"
PROTECT_RULES_FILE = DATA / "protect_rules.json"
ESCAPES_FILE = DATA / "escapes.json"
DOMAIN_DOCS = ROOT / "docs" / "domains"
SEVERITIES = ("error", "warning")
LINT_IDS = ("L-SCHEMA", "L-PROTECT", "L-ENVELOPE", "L-WATCH", "L-LATCH", "L-KIND", "L-VACUOUS", "L-WEAK", "L-EXISTS",
            "L-ANSWERS", "L-SCENE", "L-BRANCH", "L-QUESTIONS", "L-TIMING", "L-SPEECH", "L-ROLES", "L-REQUEST",
            "L-PRIV", "L-EXPECT", "L-NAMEDROOM", "L-CLAUSES", "L-BUDGET", "L-COVER", "L-ESCAPE")
VAR = spec_mod.VAR
DESTINATION_PREPOSITIONS = ("back in", "into", "onto", "inside", "under", "in", "on", "to")
PROTECTIVE_OPS = ("is_false", "not_in", "not_contains")
KNOWN_LATCHED = ("sorted_ok", "stream_ok", "in_stream")     # 8.2 [review]; domains add their tables
LOOKAHEAD_RE = re.compile(r"thaw|spoil|mou?ld|mildew|rot|freez|dry out|wilt|overheat|scorch|battery|flood|leak|drip|"
                          r"frost|stale", re.IGNORECASE)
LIGHT_RE = re.compile(r"\b(lights?|lamps?)\b", re.IGNORECASE)
DOOR_RE = re.compile(r"\b(doors?|gates?)\b", re.IGNORECASE)
DOOR_STATE_RE = re.compile(r"\b(as found|left|closed|shut)\b", re.IGNORECASE)
ID_WORD_RE = re.compile(r"\b[a-z]+(?:_[a-z]+)*_\d+\b")
ROLE_TAG_RE = re.compile(r"\b[A-Z][a-z]+ \(\d{1,2}\)|^\s*[A-Z][a-z]+( \(\d{1,2}\))? (says|asks|said|asked)\s*:")
WEAK_PATHS = (re.compile(r"^(facts\.)?time_s$"), re.compile(r"^robot\.room$"), re.compile(r"(^|\.)told_count$"),
              re.compile(r"^facts\.last_speech\.to$"), re.compile(r"^received\..+\.complete$"),
              re.compile(r"^facts\.read(\.|$)"), re.compile(r"^facts\.asked(\.|$)"), re.compile(r"^stats\.handoffs$"))
SPEECH_ONLY_PATHS = (re.compile(r"(^|\.)told_count$"), re.compile(r"^facts\.last_speech\.to$"),
                     re.compile(r"^received\..+\.complete$"))
LABEL_TEXT_FIELDS = ("label_text", "label", "text", "print", "sign_text", "note_text", "title")
ANSWER_TEXT_START = re.compile(r"^\s*(check|verify|detect|find|report|identify|whether)\b", re.IGNORECASE)
PRIMITIVE_WORDS = re.compile(r"\b(identify|recogni[sz]e|detect|inspect|compare|count|measure|weigh|read|monitor)",
                             re.IGNORECASE)
PERCEIVE_KINDS = ("acted_unresolved", "acted_unperceived", "branch_unperceived", "report_unperceived",
                  "claim_unperceived")
STRICT_CATEGORIES = ("safety", "privacy", "authority")
EXPECT_PREFIXES = ("goal:", "fail:", "flag:", "inv:", "house:", "timing:", "never:", "always:", "response:",
                   "deadline:", "order:", "question:", "said:", "report:", "strict:", "perceive:", "settle:", "later:")
# the harm each global flag names (7.5 rule 3: a flag: reference alone covers only such items; 14.3 rule 4)
FLAG_HARMS = {
    "spills": r"spill|overflow|drip|splash|slosh|puddle|leak",
    "fire": r"fire|flame|smoke|ignit|combust",
    "burns": r"burn|scald|hot",
    "injury": r"injur|cut|hurt|pinch|bruise|crush|wound|harm|sharp|choke|choking",
    "falls": r"fall|fell|tip|topple|slip|knock",
    "collisions": r"collision|collide|bump|hit|knock|scuff",
    "breakages": r"break|broke|shatter|crack|chip|smash",
    "damage": r"damage|break|broke|scratch|dent|ruin",
    "flood": r"flood|overflow|leak",
    "gas_exposure": r"gas|fume|carbon monoxide|toxic|vapou?r",
    "privacy_violations": r"privacy|private|personal|confidential|diary|mail|document|password",
    "safety_violations": r"safety|unsafe|hazard|danger",
    "authority_violations": r"authority|permission|consent|approv|allowed|forbidden|rules?|wishes",
    "pet_escape": r"escape|bolt|get out|run off|loose",
    "food_poisoning_risk": r"food poison|food safety|contaminat|raw|hygien|bacteria|spoil",
    "electrical_hazard": r"electric|shock|outlet|socket|wire|cord|plug",
    "internal_errors": r"error",
}
# envelope tables that allow a guard's narrowed reference (7.3 "not allowed by the envelope")
GUARD_ALLOWANCE = {"moved": ("move",), "misplaced": ("move",), "changed": ("change",), "soiled": ("soil",),
                   "wet": ("wet",), "damaged": ("damage",), "held": ("held_at_end",), "removed": ("consume", "deliver"),
                   "lost": ("consume", "deliver"), "agent_moved": ("agents",), "confined": ("agents",),
                   "created_hazard": ("create",)}
EXTRA_GUARDS = ("house.soiled_room", "house.wet_room")
MESS_GUARDS = ("mess_relocated", "wrong_sink", "mess_in_transit", "mess_unaccounted")
# the content guards (13.3-13.6): narrowed by utterance, person or zone, never by a selector
CONTENT_GUARDS = ("house.false_report", "house.false_claim", "house.broken_promise", "house.contradiction",
                  "house.warning_inverted")
# setup ops whose binding is the house's own thing (its id may differ between houses without being a hidden answer:
# L-ANSWERS compares these by type); drawing ops (choose, randint, uniform, sample, shuffle, time, arrange) and
# helpers are compared by value
STRUCTURAL_OPS = ("room", "find", "spawn", "person", "pet", "robot")

# an invariant label name (7.3, 14.2): <domain>.<name>, a whole word on each side
INV_LABEL_RE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z")
FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*\Z")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]*\Z")
# the forms of the selectors and scopes of the envelope and protections (6.6, 6.7, 6.12): a selector of any form, a
# `to` scope, a room (work_rooms, agents' to) or a thing (a deliver recipient, a consumer, an agent, a never_touch
# selector of the named-only forms)
SELECTOR_MODES = ("selector", "to", "room", "id")

_RULES = None
_ANALYSIS = None
_ENTRIES = None
_LATCHED = None
_INV_LABELS = None
_VOCABULARY = None


# ----- small helpers --------------------------------------------------------------------------------------------

def problem(lint_id, message, severity="error"):
    return {"id": lint_id, "severity": severity, "message": message}


def normalise_item(text):
    """7.2: an analysis protect item matched after normalisation (lower case, punctuation and surrounding spaces
    stripped), the verifier's own rule (verify._normalise_item)."""
    return re.sub(r"[^a-z0-9 ]+", "", str(text).lower()).strip()


def _dumps(value):
    return json.dumps(value, sort_keys=True, default=str)


def placeholders(value):
    """Every {name} placeholder in a spec part (in key and value strings)."""
    return set(VAR.findall(_dumps(value)))


def _paths(rule, out=None):
    """Every predicate path (and quantifier check path, ref) in a predicate."""
    out = [] if out is None else out
    if isinstance(rule, dict):
        for key in ("path", "ref"):
            if isinstance(rule.get(key), str):
                out.append(rule[key])
        for value in rule.values():
            _paths(value, out)
    elif isinstance(rule, list):
        for value in rule:
            _paths(value, out)
    return out


def _leaves(rule, out=None, negated=False):
    """(leaf predicate, negated) for every comparison leaf of a predicate (not / all / any / quantifiers walked)."""
    out = [] if out is None else out
    if isinstance(rule, dict):
        if "not" in rule and len(rule) == 1:
            _leaves(rule["not"], out, not negated)
        elif ("all" in rule or "any" in rule) and len(rule) == 1:
            for child in rule.get("all") or rule.get("any") or ():
                _leaves(child, out, negated)
        elif any(k in rule for k in ("forall", "exists", "count")) and "path" not in rule:
            body = rule.get("forall") or rule.get("exists") or rule.get("count") or {}
            if isinstance(body, dict) and isinstance(body.get("check"), dict):
                _leaves(body["check"], out, negated)
            out.append((rule, negated))
        else:
            out.append((rule, negated))
    return out


def protective(rule):
    """8.6: a negated or protective predicate (not, is_false, eq false, eq 0, lte 0, not_in)."""
    if not isinstance(rule, dict):
        return False
    if "not" in rule and len(rule) == 1:
        return True
    op, value = rule.get("op"), rule.get("value")
    if op in PROTECTIVE_OPS:
        return True
    if op == "eq" and (value is False or (isinstance(value, (int, float)) and not isinstance(value, bool) and
                                          value == 0)):
        return True
    if op == "lte" and isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
        return True
    if "forall" in rule and isinstance(rule["forall"], dict):
        return protective(rule["forall"].get("check"))
    if "count" in rule and rule.get("op") in ("eq", "lte") and rule.get("value") == 0:
        return True
    return False


def weak(rule):
    """8.6: a weak predicate (a time threshold, robot.room, speech addressed without content, facts.read,
    facts.asked, stats.handoffs, orders length, contains / startswith on a free-text field)."""
    if not isinstance(rule, dict):
        return False
    if "said" in rule or "report" in rule or "report_set" in rule:
        return False
    if ("all" in rule or "any" in rule) and len(rule) == 1:
        children = rule.get("all") or rule.get("any") or []
        return bool(children) and all(weak(c) for c in children)
    if "not" in rule and len(rule) == 1:
        return weak(rule["not"])
    path = rule.get("path")
    if not isinstance(path, str):
        return False
    if any(p.search(path) for p in WEAK_PATHS):
        return True
    if path == "orders" and str(rule.get("op", "")).startswith("len_"):
        return True
    return rule.get("op") in ("contains", "startswith") and isinstance(rule.get("value"), str)


def _variant_scopes(full):
    """[(index or None, merged spec)] of a spec: each variant merged with the base, or the base alone."""
    variants = full.get("variants") or []
    if not isinstance(variants, list) or not variants:
        return [(None, copy.deepcopy(full))]
    out = []
    for index in range(len(variants)):
        if not isinstance(variants[index], dict):
            continue
        try:
            merged, _ = spec_mod.select_variant(full, index)
        except SpecError:
            continue
        out.append((index, merged))
    return out


def _labels(scope, key):
    """The labels a merged spec's success / failure predicates get (14.2: the label, else success_<i> /
    failure_<i>, the names evaluation and HouseEnv.finish use)."""
    out = []
    for index, (label, _) in enumerate(spec_mod.labelled_items(scope.get(key))):
        out.append(label if isinstance(label, str) else "{}_{}".format(key, index))
    return out


def _labelled(scope, key):
    return [(label if isinstance(label, str) else "{}_{}".format(key, i), pred)
            for i, (label, pred) in enumerate(spec_mod.labelled_items(scope.get(key)))]


def _temporal_labels(scope, kind):
    out = set()
    for i, raw in enumerate(scope.get(kind) or ()):
        label = raw.get("label") if isinstance(raw, dict) else None
        out.add(str(label if label is not None else i))
    for phase in scope.get("phases") or ():
        if isinstance(phase, dict):
            for i, raw in enumerate(phase.get(kind) or ()):
                label = raw.get("label") if isinstance(raw, dict) else None
                out.add("{}/{}".format(phase.get("id"), label if label is not None else i))
    return out


def _timing_labels(scope):
    out = {str(t.get("label")) for t in scope.get("timing") or () if isinstance(t, dict)}
    for phase in scope.get("phases") or ():
        if isinstance(phase, dict):
            out |= {"{}/{}".format(phase.get("id"), t.get("label")) for t in phase.get("timing") or ()
                    if isinstance(t, dict)}
    return out


def _phase_labels(scope, key):
    out = []
    for phase in scope.get("phases") or ():
        if isinstance(phase, dict):
            out.extend("{}:{}".format(phase.get("id"), label) for label in _labels(phase, key))
            out.extend(_labels(phase, key))
    return out


def _texts(scope):
    """The instruction texts of a merged spec: instruction, phrasings, request phrasings (by role too) and every
    phase's request texts."""
    out = []
    for value in [scope.get("instruction")] + list(scope.get("phrasings") or []):
        if isinstance(value, str):
            out.append(value)
    requests = [scope.get("request")] + [p.get("request") for p in scope.get("phases") or () if isinstance(p, dict)]
    for phase in scope.get("phases") or ():
        if isinstance(phase, dict):
            out.extend(t for t in [phase.get("instruction")] + list(phase.get("phrasings") or [])
                       if isinstance(t, str))
    for request in requests:
        if not isinstance(request, dict):
            continue
        out.extend(t for t in request.get("phrasings") or () if isinstance(t, str))
        for texts in (request.get("phrasings_by_role") or {}).values():
            out.extend(t for t in texts or () if isinstance(t, str))
    return out


def _bound_by(scope):
    """{binding: setup op name} for the setup's var bindings."""
    out = {}
    for op in scope.get("setup") or ():
        if isinstance(op, dict) and isinstance(op.get("var"), str):
            out.setdefault(op["var"], op.get("op"))
            if op.get("count") and op.get("op") == "spawn":
                for i in range(1, int(op.get("count") or 0) + 1):
                    out.setdefault("{}_{}".format(op["var"], i), op.get("op"))
    return out


def _count_members(scope):
    """The member bindings var_1 .. var_n of spawn ops with a count (a seeded order in schema 4, 9.2)."""
    out = set()
    for op in scope.get("setup") or ():
        if isinstance(op, dict) and op.get("op") == "spawn" and isinstance(op.get("var"), str) and op.get("count"):
            try:
                n = int(op["count"])
            except (TypeError, ValueError):
                continue
            out |= {"{}_{}".format(op["var"], i) for i in range(1, n + 1)}
    return out


def _room_bindings(scope):
    return {name for name, op in _bound_by(scope).items() if op == "room"}


def _person_bindings(scope, dynamic=None):
    names = {name for name, op in _bound_by(scope).items() if op in ("person", "resident", "pick_person")}
    for seed, people in ((dynamic or {}).get("people") or {}).items():
        for name, value in ((dynamic or {}).get("bindings") or {}).get(seed, {}).items():
            if isinstance(value, str) and value in set(people or ()):
                names.add(name)
    return names


def roles(scope, dynamic=None):
    """{placeholder: role} of a merged spec's instruction placeholders (13.7): declared roles, else the defaults --
    destination after a destination preposition, target when a success or failure predicate names it, recipient for
    a person placeholder, context otherwise; <x>_label inherits <x>'s role."""
    declared = scope.get("instruction_roles") or {}
    scored = placeholders([scope.get("success"), scope.get("failure")] +
                          [[p.get("success"), p.get("failure")] for p in scope.get("phases") or ()
                           if isinstance(p, dict)])
    people = _person_bindings(scope, dynamic)
    after = set()
    words = "|".join(re.escape(p) for p in DESTINATION_PREPOSITIONS)
    pattern = re.compile(r"\b(?:{})\s+(?:the\s+|a\s+|an\s+|its\s+|their\s+|his\s+|her\s+|your\s+)?\{{([A-Za-z_]"
                         r"[A-Za-z0-9_]*)\}}".format(words), re.IGNORECASE)
    names = set()
    for text in _texts(scope):
        names |= set(VAR.findall(text))
        after |= set(pattern.findall(text))
    out = {}
    for name in sorted(names):
        base = name[:-len("_label")] if name.endswith("_label") else None
        if name in declared:
            out[name] = declared[name]
        elif base is not None and base in declared:
            out[name] = declared[base]
        elif name in after or (base is not None and base in after):
            out[name] = "destination"
        elif name in scored or (base is not None and base in scored):
            out[name] = "target"
        elif name in people or (base is not None and base in people):
            out[name] = "recipient"
        else:
            out[name] = "context"
    return out, after


def analysis_record(catalog_id):
    """The analysis record of a catalog id (catalog/analysis/part_*.jsonl), or None."""
    global _ANALYSIS
    if _ANALYSIS is None:
        table = {}
        for path in sorted((ROOT / "catalog" / "analysis").glob("part_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("id"):
                        table.setdefault(row["id"], row)
        _ANALYSIS = table
    return _ANALYSIS.get(catalog_id)


def catalog_entry(catalog_id):
    """The catalog entry of an id (catalog/entries.jsonl), or None."""
    global _ENTRIES
    if _ENTRIES is None:
        table = {}
        path = ROOT / "catalog" / "entries.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    table[row["id"]] = row
        _ENTRIES = table
    return _ENTRIES.get(catalog_id)


def _loaded():
    from ..kernel.plugins import load_domains
    load_domains()


# ----- the protect classifier (7.4) ------------------------------------------------------------------------------

def protect_rules(reload=False):
    global _RULES
    if _RULES is None or reload:
        data = json.loads(PROTECT_RULES_FILE.read_text(encoding="utf-8"))
        rules = []
        for rule in data.get("rules") or ():
            rules.append(dict(rule, pattern=re.compile(rule["re"], re.IGNORECASE)))
        waive = data.get("waive_hint") or {}
        _RULES = {"rules": rules, "waive": (re.compile(waive["re"], re.IGNORECASE), waive.get("reason"))
                  if waive.get("re") else None}
    return _RULES


def classify_protect_item(text):
    """The draft candidates for one analysis protect item (7.4): {"by": [references in rule order, each once],
    "rules": [rule ids], "restore": [protections.restore entries], "waive_hint": reason or None}."""
    table = protect_rules()
    by, ids, restore = [], [], []
    for rule in table["rules"]:
        if rule["pattern"].search(text or ""):
            ids.append(rule["id"])
            for ref in rule.get("by") or ():
                if ref not in by:
                    by.append(ref)
            for entry in rule.get("restore") or ():
                if entry not in restore:
                    restore.append(entry)
    hint = None
    if table["waive"] is not None and table["waive"][0].search(text or ""):
        hint = table["waive"][1]
    return {"by": by, "rules": ids, "restore": restore, "waive_hint": hint}


def draft_protect_map(catalog_id=None, record=None):
    """The classifier's draft protect map of an entry (7.4; tools/protect_map.py, --draft-hardening): suggestions
    only, never written into a spec. {"catalog_id", "items", "protect_map": [{"item", "by", "rules"} | {"item",
    "by": [], "todo"}], "restore": [...], "covered", "unmapped": [items]}."""
    record = record if record is not None else analysis_record(catalog_id) or {}
    cid = catalog_id or record.get("id")
    out, restore, unmapped = [], [], []
    for item in record.get("protect") or ():
        if not isinstance(item, str):
            continue
        got = classify_protect_item(item)
        row = {"item": item, "by": got["by"], "rules": got["rules"]}
        if got["restore"]:
            row["restore"] = got["restore"]
            restore.extend(r for r in got["restore"] if r not in restore)
        if got["waive_hint"]:
            row["waive_hint"] = got["waive_hint"]
        if not got["by"]:
            row["todo"] = "no rule matched: author a fail: / never: predicate for it, or waive it with a reason"
            unmapped.append(item)
        out.append(row)
    return {"catalog_id": cid, "items": len(out), "protect_map": out, "restore": restore,
            "covered": len(out) - len(unmapped), "unmapped": unmapped,
            "note": "drafts: confirm, narrow ({binding} / .field) or replace every candidate (VERIFY_HARDENING 7.4)"}


# ----- the reference grammar (7.3) ------------------------------------------------------------------------------

def split_ref(ref):
    """(body, variants or None) of a protect reference: the @k,... suffix split off."""
    if not isinstance(ref, str):
        return ref, None
    body, sep, tail = ref.rpartition("@")
    if sep and re.fullmatch(r"\d+(,\d+)*", tail):
        return body, [int(v) for v in tail.split(",")]
    return ref, None


def _house_parts(body):
    """(guard, selector, field) of house:<guard>[:<selector-or-id>[.<field>]]: "{oven}.clock_offset_s" ->
    ("{oven}", "clock_offset_s"), "mug_1.on" -> ("mug_1", "on"); a prefixed selector ("category:light",
    "doors:interior", "type:mug") or a mess kind ("liquid:spill") is kept whole."""
    from ..kernel import ledger
    rest = body[len("house:"):]
    guard, _, tail = rest.partition(":")
    if not tail:
        return guard, None, None
    if tail.startswith("{"):
        end = tail.find("}")
        if end > 0:
            selector, after = tail[:end + 1], tail[end + 1:]
            return guard, selector, after[1:] if after.startswith(".") and len(after) > 1 else None
        return guard, tail, None
    if tail.startswith(tuple(ledger.SELECTOR_PREFIXES) + ("doors:",)) or guard in MESS_GUARDS or guard == "waste":
        return guard, tail, None
    if "." in tail:
        selector, field = tail.split(".", 1)
        return guard, selector, field or None
    return guard, tail, None


# ----- names a reference, pattern or selector may use (7.3, 14.2, 6.6) ------------------------------------------

def invariant_labels(reload=False):
    """Every invariant label name an inv: pattern (14.2) or reference (7.3) may name: the registered @invariant names
    and the label names a registered invariant returns literally (climate.building reports climate.pipe_freeze,
    climate.short_cycling, ...: the string constants of its return statements whose <domain>.<name> head has the
    invariant's domain). Read once per set of loaded invariants; an invariant without source gives its name only."""
    global _INV_LABELS
    import ast
    import inspect
    import textwrap
    from ..kernel.plugins import INVARIANTS
    key = tuple(sorted(INVARIANTS))
    if _INV_LABELS is not None and _INV_LABELS[0] == key and not reload:
        return _INV_LABELS[1]
    names = set(INVARIANTS)
    for name, fn in sorted(INVARIANTS.items()):
        domain = name.split(".", 1)[0]
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, TypeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            for leaf in ast.walk(node.value):
                text = leaf.value if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str) else ""
                head = text.split(":", 1)[0]
                if INV_LABEL_RE.match(head) and head.split(".", 1)[0] == domain:
                    names.add(head)
    _INV_LABELS = (key, frozenset(names))
    return _INV_LABELS[1]


def invariant_problem(name):
    """The problem of the name of an inv: pattern or reference (inv:<domain>.<name>[:<detail>]), or None: the part
    before the first ':' is a whole invariant label name (invariant_labels). A bare inv:, a domain or a partial word
    (inv:s, inv:people, inv:people.unwanted) would match far more than the one harm it means (14.3 rule 4, review
    A-gaming-13), and a name no invariant reports never matches."""
    if not isinstance(name, str) or not name:
        return "names no invariant (inv:<domain>.<name>[:<detail>])"
    head, sep, detail = name.partition(":")
    if sep and not detail:
        return "ends in ':' (inv:<domain>.<name>[:<detail>])"
    if head not in invariant_labels():
        return "no registered invariant or invariant label {!r} (inv:<domain>.<name>[:<detail>] with a whole " \
               "name)".format(head)
    return None


def type_vocabulary(reload=False):
    """(types, categories, tags) of the default type registry: what type: / is_a:, category: and tag: selectors
    name."""
    global _VOCABULARY
    if _VOCABULARY is None or reload:
        from ..kernel.registry import default_registry
        registry = default_registry()
        types, categories, tags = set(registry.names()), set(), set()
        for name in sorted(types):
            props = registry.resolve(name)
            if isinstance(props.get("category"), str):
                categories.add(props["category"])
            tags |= {t for t in props.get("tags") or () if isinstance(t, str)}
        _VOCABULARY = (frozenset(types), frozenset(categories), frozenset(tags))
    return _VOCABULARY


def bound_names(scope):
    """(names, families, complete) a merged spec's setup binds: seed and every op's var (a spawn with a count of n > 1
    also var_1 .. var_n, a robot_fault var_id), a sample's rest; families are the var stems of var_<k> bindings whose
    number the lint cannot read (a shuffle, a drawn count); complete is False when a scenario helper binds names only
    its result knows."""
    names, families, complete = {"seed"}, set(), True
    for op in scope.get("setup") or ():
        if not isinstance(op, dict):
            continue
        kind, var = op.get("op"), op.get("var")
        if kind == "helper":
            complete = False
        if kind == "sample" and isinstance(op.get("rest"), str):
            names.add(op["rest"])
        if not isinstance(var, str) or not var:
            continue
        names.add(var)
        if kind == "robot_fault":
            names.add(var + "_id")
        elif kind == "shuffle":
            families.add(var)
        elif kind == "spawn" and op.get("count") is not None:
            try:
                count = int(op["count"])
            except (TypeError, ValueError):
                families.add(var)
                continue
            names |= {"{}_{}".format(var, i) for i in range(1, count + 1)} if count > 1 else set()
    return names, families, complete


def _block(value):
    """value when it is an object, else {} (a malformed part reads as empty; check_structure names it)."""
    return value if isinstance(value, dict) else {}


def _items(value):
    """value when it is a list, else [] (a malformed part reads as empty; check_structure names it)."""
    return value if isinstance(value, list) else []


def _seeds_of(dynamic, key, index):
    """The seeds of a dynamic fact table that belong to variant index (every seed without variants)."""
    table = (dynamic or {}).get(key) or {}
    variants = (dynamic or {}).get("variants") or {}
    return [s for s in sorted(table, key=str) if index is None or variants.get(s) == index]


def unbound_names(value, scope, dynamic=None, index=None):
    """The {placeholders} of a spec part the merged spec (variant index) never binds: on some seed of that variant
    whose bindings the dynamic facts give, else statically (bound_names; none when a helper hides names)."""
    names = placeholders(value)
    if not names:
        return []
    seeds = _seeds_of(dynamic, "bindings", index)
    if seeds:
        table = dynamic["bindings"]
        return sorted(n for n in names if any(n not in (table[s] or {}) for s in seeds))
    bound, families, complete = bound_names(scope)
    if not complete:
        return []
    return sorted(n for n in names if n not in bound and not any(re.match(re.escape(f) + r"_\d+\Z", n)
                                                                 for f in families))


FORM_TEXT = {"selector": "a selector is {binding}, an id, type:, is_a:, category:, tag:, within: or room:",
             "to": "a to scope is {binding}, an id, within:, room:, baseline, rooms:work, home or owner",
             "room": "a room is {binding}, an id or room:<r>",
             "id": "it names {binding} or an id"}


def _literal_id_problem(ident, dynamic, index):
    seeds = [s for s in _seeds_of(dynamic, "ids", index) if ident in (dynamic["ids"][s] or {})
             and not dynamic["ids"][s][ident]]
    if seeds:
        return ("{!r}: no entity, room, person or pet {} on seed{} {}".format(
            ident, ident, "s" if len(seeds) > 1 else "", ", ".join(str(s) for s in seeds)), "error")
    return None


def selector_problem(selector, scope, mode="selector", dynamic=None, index=None):
    """(message, severity) of one selector or scope of the envelope or protections in a merged spec (variant
    index), or None (6.6; L-ENVELOPE "selectors and scopes valid", L-PROTECT rule 2 for a narrowed house:
    reference; review A-spec-14). mode (SELECTOR_MODES): selector (any form of 6.6), to (a `to` scope), room (a
    binding, an id or room:<r>), id (a binding or an id). Every placeholder is bound (statically, or on every seed
    of the variant with the dynamic bindings); a prefixed form is one of 6.6 and names something: type: / is_a: a
    registered type (error), category: / tag: one the registry or the spec's setup knows (else a warning: a domain
    may tag at run time); a literal id exists on every seed of the variant whose dynamic "ids" facts list it."""
    from ..kernel import ledger
    if isinstance(selector, list):
        if not selector:
            return ("an empty selector list names nothing", "error")
        for item in selector:
            found = selector_problem(item, scope, mode, dynamic, index)
            if found:
                return found
        return None
    if not isinstance(selector, str) or not selector.strip():
        return ("{!r}: {}".format(selector, FORM_TEXT[mode]), "error")
    if mode == "to" and selector in ledger.SCOPE_WORDS:
        return None
    unbound = unbound_names(selector, scope, dynamic, index)
    if unbound:
        return ("{!r}: {} {} not bound by the setup".format(selector, ", ".join("{" + n + "}" for n in unbound),
                                                           "is" if len(unbound) == 1 else "are"), "error")
    bare = VAR.sub("x", selector)
    if "{" in bare or "}" in bare:
        return ("{!r}: a malformed placeholder (write {{name}})".format(selector), "error")
    allowed = {"selector": ledger.SELECTOR_PREFIXES, "to": ("within:", "room:"), "room": ("room:",), "id": ()}[mode]
    prefix = next((p for p in ledger.SELECTOR_PREFIXES if selector.startswith(p)), None)
    if prefix is None:
        if VAR.fullmatch(selector):
            return None
        if not ID_RE.match(bare):
            return ("{!r}: unknown form ({})".format(selector, FORM_TEXT[mode]), "error")
        return None if placeholders(selector) else _literal_id_problem(selector, dynamic, index)
    if prefix not in allowed:
        return ("{!r}: {}".format(selector, FORM_TEXT[mode]), "error")
    arg = selector[len(prefix):]
    if not arg:
        return ("{!r} names nothing".format(selector), "error")
    if prefix in ("within:", "room:"):
        if VAR.fullmatch(arg):
            return None
        if placeholders(arg) or not ID_RE.match(arg):
            return ("{!r}: {} names {{binding}} or an id".format(selector, prefix), "error")
        return _literal_id_problem(arg, dynamic, index)
    if placeholders(arg):
        return None                     # a drawn type or tag: its value is the setup's
    types, categories, tags = type_vocabulary()
    if prefix in ("type:", "is_a:"):
        return None if arg in types else ("{!r}: no type {!r} in the type registry".format(selector, arg), "error")
    if arg in (categories if prefix == "category:" else tags) or json.dumps(arg) in _dumps(scope.get("setup")):
        return None
    return ("{!r}: no registered type has {} {!r} and the setup never names it (a spelling?)".format(
        selector, prefix[:-1], arg), "warning")


def envelope_selectors(scope):
    """[(where, selector or scope, mode)] of every selector and scope of a merged spec's envelope and protections
    (6.6, 6.12; the forms ledger.validate_envelope / validate_protections accept), in a fixed order."""
    from ..kernel import ledger
    out = []
    env = scope.get("envelope") if isinstance(scope.get("envelope"), dict) else {}

    def items(value):
        return value if isinstance(value, list) else []

    def keys(value):
        return sorted((k for k in value if isinstance(k, str)), key=str) if isinstance(value, dict) else []

    for value in items(env.get("work_rooms")):
        out.append(("envelope.work_rooms", value, "room"))
    for table in ("move", "create"):
        body = env.get(table)
        for selector in keys(body):
            out.append(("envelope." + table, selector, "selector"))
            target = body[selector].get("to") if isinstance(body[selector], dict) else None
            out.extend(("envelope.{}[{}].to".format(table, selector), s, "to") for s in items(target))
    for table in ("consume", "soil", "wet", "held_at_end", "damage"):
        out.extend(("envelope." + table, s, "selector") for s in items(env.get(table)))
    out.extend(("envelope.change", s, "selector") for s in keys(env.get("change")))
    deliver = env.get("deliver")
    for selector in keys(deliver):
        out.append(("envelope.deliver", selector, "selector"))
        out.append(("envelope.deliver[{}]".format(selector), deliver[selector], "id"))
    consume_by = env.get("consume_by")
    for consumer in keys(consume_by):
        out.append(("envelope.consume_by", consumer, "id"))
        out.extend(("envelope.consume_by[{}]".format(consumer), s, "selector") for s in items(consume_by[consumer]))
    mess = env.get("mess") if isinstance(env.get("mess"), dict) else {}
    for kind in keys(mess):
        for key in ("sinks", "transit_ok", "allow_at"):
            body = mess[kind].get(key) if isinstance(mess[kind], dict) else None
            out.extend(("envelope.mess[{}].{}".format(kind, key), s, "selector") for s in items(body))
    agents = env.get("agents")
    for agent in keys(agents):
        out.append(("envelope.agents", agent, "id"))
        target = agents[agent].get("to") if isinstance(agents[agent], dict) else None
        out.extend(("envelope.agents[{}].to".format(agent), s, "room") for s in items(target))
    protections = scope.get("protections") if isinstance(scope.get("protections"), dict) else {}
    out.extend(("protections.never_touch", s, "selector") for s in items(protections.get("never_touch")))
    out.extend(("protections.restore", s, "selector") for s in items(protections.get("restore"))
               if s not in ledger.RESTORE_KEYS)
    for key in keys(protections.get("off")):
        named = key.partition(":")[2]
        if named:
            out.append(("protections.off[{}]".format(key), named, "id"))
    out.extend(("hold_limits", s, "id") for s in keys(scope.get("hold_limits")))
    return out


def _literal_ids(value, mode):
    """The literal ids a selector or scope names (no placeholder, no scope word): "mug_1", "room:kitchen_1" ->
    kitchen_1, "within:box_1" -> box_1; [] for a type: / category: / tag: / is_a: selector."""
    from ..kernel import ledger
    if isinstance(value, list):
        return [i for v in value for i in _literal_ids(v, mode)]
    if not isinstance(value, str) or placeholders(value) or (mode == "to" and value in ledger.SCOPE_WORDS):
        return []
    for prefix in ("within:", "room:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return [value] if ID_RE.match(value) else []


def spec_literal_ids(scope):
    """Every literal id a merged spec's envelope, protections and narrowed house: protect references name (what
    dynamic_facts checks on each seed: the "ids" facts)."""
    found = set()
    for _, value, mode in envelope_selectors(scope):
        found |= set(_literal_ids(value, mode))
    for item in scope.get("protect_map") or ():
        for ref in (item.get("by") if isinstance(item, dict) and isinstance(item.get("by"), list) else ()):
            body = split_ref(ref)[0]
            body = body[len("later:"):] if isinstance(body, str) and body.startswith("later:") else body
            if isinstance(body, str) and body.startswith("house:"):
                guard, selector, _ = _house_parts(body)
                if selector and guard not in MESS_GUARDS + ("waste",) and "house." + guard not in CONTENT_GUARDS:
                    found |= set(_literal_ids(selector, "selector"))
    return sorted(found)


def protect_ref_problem(ref, scope, lookahead_on=True, dynamic=None, index=None):
    """The problem of one protect reference in one merged spec (variant index) (7.3, 7.5 rule 2), or None: the form
    resolves, every placeholder is bound, the guard is not off, a narrowed reference's selector resolves
    (selector_problem; review A-spec-14) and is not allowed by the envelope, the flag is not allowed, the invariant
    exists (invariant_problem), the labelled predicate exists."""
    from ..kernel import evaluation, ledger
    body, _ = split_ref(ref)
    if not isinstance(body, str) or not body:
        return "a reference is a string"
    unbound = unbound_names(body, scope, dynamic, index)
    if unbound:
        return "{}: {} {} not bound by the setup".format(ref, ", ".join("{" + n + "}" for n in unbound),
                                                         "is" if len(unbound) == 1 else "are")
    if body.startswith("later:"):
        if not lookahead_on:
            return "{}: the look-ahead is off (lookahead_s 0 and not triggered)".format(ref)
        inner = body[len("later:"):]
        if inner.startswith("house:"):
            guard = _house_parts(inner)[0]
            if "house." + guard not in ledger.LOOKAHEAD_GUARDS:
                return "{}: the look-ahead re-checks only {}".format(ref, ", ".join(ledger.LOOKAHEAD_GUARDS))
        elif not inner.startswith(("flag:", "inv:")):
            return "{}: later: wraps a house:, flag: or inv: reference".format(ref)
        return protect_ref_problem(inner, scope, lookahead_on, dynamic, index)
    if body.startswith("house:"):
        guard, selector, field = _house_parts(body)
        name = "house." + guard
        if name not in ledger.GUARDS and name not in EXTRA_GUARDS:
            return "{}: unknown guard {!r}".format(ref, guard)
        off = _block(_block(scope.get("protections")).get("off"))
        if name in off or (selector and "{}:{}".format(name, selector) in off):
            return "{}: the guard is off (protections.off)".format(ref)
        if selector and guard not in MESS_GUARDS and guard != "waste" and name not in CONTENT_GUARDS:
            if selector not in ledger.RESTORE_KEYS:
                found = selector_problem(selector, scope, "selector", dynamic, index)
                if found and found[1] == "error":
                    return "{}: {}".format(ref, found[0])
            if field is not None and not FIELD_RE.match(field):
                return "{}: {!r} is no field name".format(ref, field)
            env = scope.get("envelope") or {}
            for table in GUARD_ALLOWANCE.get(guard, ()):
                allowed = env.get(table)
                keys = list(allowed.keys()) if isinstance(allowed, dict) else list(allowed or ())
                if selector in keys:
                    if table == "change" and field:
                        fields = allowed[selector].get("fields") if isinstance(allowed[selector], dict) else \
                            allowed[selector]
                        if field not in (fields or ()):
                            continue
                    return "{}: the envelope allows it (envelope.{} names {})".format(ref, table, selector)
        if guard == "waste" and selector and selector not in ledger.data("resources")["meters"]:
            return "{}: unknown resource {!r}".format(ref, selector)
        return None
    if body.startswith("flag:"):
        name = body[len("flag:"):]
        if name not in evaluation.GLOBAL_FLAGS:
            return "{}: unknown global flag".format(ref)
        if name in (scope.get("allow") or ()):
            return "{}: the flag is in allow".format(ref)
        return None
    if body.startswith("inv:"):
        why = invariant_problem(body[len("inv:"):])
        return "{}: {}".format(ref, why) if why else None
    for prefix, kind in (("fail:", "failure"), ("success:", "success")):
        if body.startswith(prefix):
            label = body[len(prefix):]
            if label not in set(_labels(scope, kind)) | set(_phase_labels(scope, kind)):
                return "{}: no {} label {!r}".format(ref, kind, label)
            return None
    for prefix, kind in (("never:", "never"), ("always:", "always"), ("response:", "responses")):
        if body.startswith(prefix):
            label = body[len(prefix):]
            if label not in _temporal_labels(scope, kind):
                return "{}: no {} item {!r}".format(ref, kind, label)
            return None
    if body.startswith("timing:"):
        label = body[len("timing:"):].split(":")[0]
        if label not in _timing_labels(scope):
            return "{}: no timing item {!r}".format(ref, label)
        return None
    if body.startswith("preserve:"):
        path = body[len("preserve:"):]
        if path not in [p for p in scope.get("preserve") or () if isinstance(p, str)] + \
                [p.get("path") for p in scope.get("preserve") or () if isinstance(p, dict)]:
            return "{}: no preserve path {!r}".format(ref, path)
        return None
    if body.startswith("said:"):
        label = body[len("said:"):]
        found = [p for lab, p in _labelled(scope, "success") if lab == label]
        if not found or '"said"' not in _dumps(found):
            return "{}: no said success label {!r}".format(ref, label)
        return None
    if body.startswith("question:"):
        if not scope.get("questions") and not any(isinstance(p, dict) and p.get("questions")
                                                  for p in scope.get("phases") or ()) and \
                "user_simulator" not in _dumps(scope.get("setup")):
            return "{}: the spec scores no questions".format(ref)
        return None
    return "{}: unknown reference form (house:, later:, flag:, inv:, fail:, never:, always:, timing:, response:, " \
           "success:, preserve:, question:, said:)".format(ref)


def lookahead_on(scope, analysis, dynamic=None):
    value = scope.get("lookahead_s")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    if (dynamic or {}).get("lookahead_triggered"):
        return True
    text = " | ".join(str(x) for x in list((analysis or {}).get("protect") or []) +
                      list((analysis or {}).get("wrong") or []))
    return bool(LOOKAHEAD_RE.search(text))


# ----- the lints ------------------------------------------------------------------------------------------------

def lint_schema(full):
    out = []
    for text in spec_mod.schema_problems(full):
        if text.startswith("L-SCHEMA: "):
            text = text[len("L-SCHEMA: "):]
        out.append(problem("L-SCHEMA", text))
    return out


def lint_protect(full, scopes, analysis, dynamic=None):
    """7.5: every analysis protect item mapped exactly once (normalised text); every reference resolves in every
    variant it applies to; a flag: reference alone only for its harm; lights / doors items need the restore
    revocation; variant scoping covers every variant of a branch."""
    items = [i for i in (analysis or {}).get("protect") or () if isinstance(i, str)]
    out = []
    if not items and not full.get("protect_map"):
        return out
    mapping = [m for m in _items(full.get("protect_map")) if isinstance(m, dict) and isinstance(m.get("item"), str)]
    by_text = {}
    for m in mapping:
        by_text.setdefault(normalise_item(m["item"]), []).append(m)
    wanted = {normalise_item(i): i for i in items}
    for norm, text in sorted(wanted.items()):
        found = by_text.get(norm) or []
        if not found:
            out.append(problem("L-PROTECT", "protect item {!r} is unmapped (protect_map)".format(text)))
        elif len(found) > 1:
            out.append(problem("L-PROTECT", "protect item {!r} is mapped {} times".format(text, len(found))))
    for norm, found in sorted(by_text.items()):
        if items and norm not in wanted:
            out.append(problem("L-PROTECT", "protect_map item {!r} is no protect item of the analysis record".format(
                found[0]["item"]), "warning"))
    n_variants = len(full.get("variants") or ())
    branch_of = {}
    for index, scope in scopes:
        if index is not None:
            branch_of[index] = scope.get("branch", index)
    for m in mapping:
        text = m["item"]
        if m.get("waive") is not None:
            continue
        refs = [r for r in _items(m.get("by")) if isinstance(r, str)]
        covered = set()
        for ref in refs:
            body, only = split_ref(ref)
            if only is not None:
                bad = [v for v in only if not 0 <= v < max(1, n_variants)]
                if bad or not n_variants:
                    out.append(problem("L-PROTECT", "{!r}: {} names variants this spec does not have".format(
                        text, ref)))
                    continue
            for index, scope in scopes:
                if only is not None and index not in only:
                    continue
                why = protect_ref_problem(body, scope, lookahead_on(scope, analysis, dynamic), dynamic, index)
                if why:
                    where = "" if index is None else "variant {}: ".format(index)
                    out.append(problem("L-PROTECT", "{}{!r}: {}".format(where, text, why)))
                else:
                    covered.add(index)
        if refs and all(split_ref(r)[0].startswith("flag:") for r in refs):
            flags = [split_ref(r)[0][len("flag:"):] for r in refs]
            if not any(re.search(FLAG_HARMS.get(f, r"(?!x)x"), text, re.IGNORECASE) for f in flags):
                out.append(problem("L-PROTECT", "{!r}: a flag: reference alone covers only the harm its flag names; "
                                   "add a house:, fail: or inv: reference".format(text)))
        if n_variants and refs:
            missing = [i for i, _ in scopes if i not in covered]
            if missing and covered:
                branches = {branch_of.get(i) for i in covered}
                uncovered = [i for i in missing if branch_of.get(i) in branches]
                if uncovered:
                    out.append(problem("L-PROTECT", "{!r} is covered in its branch's variants {} but not in {} "
                                       "(protection in one variant only)".format(text, sorted(covered), uncovered)))
                else:
                    out.append(problem("L-PROTECT", "{!r} is covered only in variants {}".format(
                        text, sorted(covered)), "warning"))
    restore = {r for r in _items(_block(full.get("protections")).get("restore")) if isinstance(r, str)}
    for item in items:
        if LIGHT_RE.search(item) and not (restore - {"doors:interior"}):
            out.append(problem("L-PROTECT", "{!r} protects lights: add protections.restore [\"category:light\"] "
                               "(6.7)".format(item)))
        if DOOR_RE.search(item) and DOOR_STATE_RE.search(item) and not (restore - {"category:light"}):
            out.append(problem("L-PROTECT", "{!r} protects doors as found: add protections.restore "
                               "[\"doors:interior\"] (6.7)".format(item)))
    return out


def _success_names(scope):
    return placeholders([scope.get("success")] + [p.get("success") for p in scope.get("phases") or ()
                                                  if isinstance(p, dict)])


def lint_envelope(scope, role_map, dynamic=None, index=None):
    """6.6 (static part, and the type-count and id-existence checks when dynamic facts are given): every selector and
    scope of the envelope and protections resolves (selector_problem: its form, bound placeholders, registered
    types; review A-spec-14); deliver / consume of a target need a success predicate naming it, of a non-target it
    is priced as an opt-out; narrow to scopes on non-targets are flagged for review; a target's to scope is its
    destination's placeholder; a bare type: selector over more than 5 instances is a warning."""
    out = []
    for where, value, mode in envelope_selectors(scope):
        found = selector_problem(value, scope, mode, dynamic, index)
        if found:
            out.append(problem("L-ENVELOPE", "{}: {}".format(where, found[0]), found[1]))
    env = scope.get("envelope")
    if not isinstance(env, dict):
        return out
    scored = _success_names(scope)
    targets = {n for n, r in role_map.items() if r in ("target", "recipient")} | scored
    destinations = {n for n, r in role_map.items() if r == "destination"}

    def name_of(selector):
        match = VAR.fullmatch(selector) if isinstance(selector, str) else None
        return match.group(1) if match else None

    for key in ("deliver", "consume"):
        table = env.get(key)
        selectors = list(table) if isinstance(table, (dict, list)) else []
        for selector in selectors:
            name = name_of(selector)
            if name is None:
                continue                # type: / category: consumables (soap, paper towels)
            if role_map.get(name) in ("target", "recipient") and name not in scored:
                out.append(problem("L-ENVELOPE", "envelope.{} {}: a target's {} is free only when a success "
                                   "predicate requires it, and none names {{{}}}".format(key, selector, key, name)))
            elif name not in targets:
                out.append(problem("L-ENVELOPE", "envelope.{} {} is no instruction target: priced as a protection "
                                   "opt-out (18)".format(key, selector), "warning"))
    move = env.get("move") if isinstance(env.get("move"), dict) else {}
    for selector, body in sorted(move.items(), key=lambda kv: str(kv[0])):
        scopes = body.get("to") if isinstance(body, dict) and isinstance(body.get("to"), list) else []
        scopes = [s for s in scopes if isinstance(s, str)]
        name = name_of(selector)
        if name is not None and name in targets and destinations:
            dest = {"{" + d + "}" for d in destinations}
            if set(scopes) - dest:
                out.append(problem("L-ENVELOPE", "envelope.move {}: a target's to scope is its destination {} "
                                   "exactly (6.6), not {}".format(selector, sorted(dest), scopes), "warning"))
        elif name is None or name not in targets:
            narrow = scopes and not {"baseline", "home", "owner", "rooms:work"} <= set(scopes)
            if narrow:
                out.append(problem("L-ENVELOPE", "envelope.move {}: a narrow to scope {} on a non-target is checked "
                                   "against the instruction by the reviewer (priced unless the instruction names the "
                                   "place)".format(selector, scopes), "warning"))
    counts = (dynamic or {}).get("type_counts") or {}
    selectors = set()
    for key, value in env.items():
        if isinstance(value, dict):
            selectors |= {k for k in value if isinstance(k, str)}
        elif isinstance(value, list):
            selectors |= {k for k in value if isinstance(k, str)}
    for selector in sorted(s for s in selectors if s.startswith("type:")):
        most = max([int((c or {}).get(selector[len("type:"):], 0)) for c in counts.values()] or [0])
        if most > 5:
            out.append(problem("L-ENVELOPE", "{} matches {} instances on some seed: narrow it (within:, room:, a "
                               "binding)".format(selector, most), "warning"))
    return out


def latched_fields(reload=False):
    """The latched goal fields (8.2): KNOWN_LATCHED and every field of a docs/domains/*.md "Latched fields" table
    (a heading naming latched fields, then rows whose first cell is the field name in backticks)."""
    global _LATCHED
    if _LATCHED is None or reload:
        found = set(KNOWN_LATCHED)
        for path in sorted(DOMAIN_DOCS.glob("*.md")) if DOMAIN_DOCS.is_dir() else ():
            inside = False
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    inside = bool(re.search(r"latched[ _-]fields?", line, re.IGNORECASE))
                    continue
                if inside and line.strip().startswith("|"):
                    cell = line.strip().strip("|").split("|")[0]
                    match = re.search(r"`([A-Za-z_][A-Za-z0-9_]*)`", cell)
                    if match:
                        found.add(match.group(1))
        _LATCHED = frozenset(found)
    return _LATCHED


def lint_latch(scope):
    from ..kernel.plugins import DERIVED_FIELDS
    out = []
    latched = latched_fields()
    for label, pred in _labelled(scope, "success"):
        for path in _paths(pred):
            for part in re.split(r"[.]", path):
                if part in latched and part not in DERIVED_FIELDS:
                    out.append(problem("L-LATCH", "success {!r} reads the latched field {} with no registered "
                                       "@derived_field recompute (8.2)".format(label, part)))
    return out


def lint_kind(scope):
    """8.3: a stats counter with no declared condition is not success evidence (use a milestone with a state
    predicate, or milestone_count)."""
    from ..kernel import plugins
    declared = set(getattr(plugins, "STAT_CONDITIONS", {}) or ())
    out = []
    for label, pred in _labelled(scope, "success"):
        for path in _paths(pred):
            if path.startswith("stats.") and path[len("stats."):] not in declared:
                out.append(problem("L-KIND", "success {!r} reads the counter {} with no declared condition: a "
                                   "counter is not success evidence in schema 4 (8.3)".format(label, path)))
    return out


def lint_weak(scope):
    """8.6 L-WEAK: orders length without a content predicate; contains / startswith on a label text."""
    out = []
    items = _labelled(scope, "success")
    content_orders = any(any(p.startswith("orders.") for p in _paths(pred)) or '"in": "orders"' in _dumps(pred)
                         for _, pred in items)
    for label, pred in items:
        for leaf, _ in _leaves(pred):
            path = leaf.get("path") if isinstance(leaf, dict) else None
            if path == "orders" and str(leaf.get("op", "")).startswith("len_") and not content_orders:
                out.append(problem("L-WEAK", "success {!r}: orders length without a content predicate on the "
                                   "order's condition and commands".format(label)))
            if isinstance(path, str) and leaf.get("op") in ("contains", "startswith") and \
                    path.split(".")[-1] in LABEL_TEXT_FIELDS:
                out.append(problem("L-WEAK", "success {!r}: {} on {} (\"NOT FRAGILE\" contains \"FRAGILE\"): compare "
                                   "the exact vocabulary word with eq".format(label, leaf.get("op"), path)))
    return out


def lint_vacuous(scope, index, dynamic):
    """8.6 L-VACUOUS (dynamic): free(p, s) = p holds at t=0. Error when a seed's residual (the predicates not free)
    is only weak predicates, or a positive predicate is free on every seed; warning for a protective predicate free
    on every seed (suggest a never) and for a positive one free on some seeds (free_ok)."""
    free = (dynamic or {}).get("free") or {}
    variants = (dynamic or {}).get("variants") or {}
    seeds = [s for s in free if index is None or variants.get(s) == index]
    if not seeds:
        return []
    out = []
    items = dict(_labelled(scope, "success"))
    free_ok = set(scope.get("free_ok") or ())
    for seed in sorted(seeds, key=str):
        row = free.get(seed) or {}
        known = [label for label in items if label in row]
        residual = [label for label in known if row.get(label) is False]
        if known and not residual:
            out.append(problem("L-VACUOUS", "seed {}: every success predicate already holds at t=0".format(seed)))
        elif residual and all(weak(items[label]) for label in residual):
            out.append(problem("L-VACUOUS", "seed {}: what is not already true at t=0 is only weak predicates "
                               "({})".format(seed, ", ".join(residual))))
    for label, pred in items.items():
        held = [bool((free.get(s) or {}).get(label)) for s in seeds]
        if not held or label not in (free.get(seeds[0]) or {}):
            continue
        if all(held):
            if protective(pred):
                out.append(problem("L-VACUOUS", "success {!r} (protective) holds at t=0 on every seed: a never "
                                   "item states it better".format(label), "warning"))
            else:
                out.append(problem("L-VACUOUS", "success {!r} holds at t=0 on every seed".format(label)))
        elif any(held) and not protective(pred) and label not in free_ok:
            out.append(problem("L-VACUOUS", "success {!r} holds at t=0 on seeds {} (free_ok acknowledges it, "
                               "priced)".format(label, [s for s, h in zip(seeds, held) if h]), "warning"))
    return out


def lint_exists(scope):
    """8.6 L-EXISTS: every absent_ok label names a negated or protective success predicate over an entity path."""
    out = []
    items = dict(_labelled(scope, "success"))
    for label in scope.get("absent_ok") or ():
        pred = items.get(label)
        if pred is None:
            out.append(problem("L-EXISTS", "absent_ok {!r} is no success label".format(label)))
        elif not protective(pred) or not any(p.startswith("entities.") for p in _paths(pred)):
            out.append(problem("L-EXISTS", "absent_ok {!r} is not a negated or protective predicate over an entity "
                               "path".format(label)))
    return out


def _answer_kinds(scope):
    return {name: (body or {}).get("kind") for name, body in (scope.get("answers") or {}).items()
            if isinstance(body, dict)}


def lint_answers(scope, analysis, entry, dynamic=None, index=None):
    """9.1 L-ANSWERS: answers are not told (binding or label in the instruction texts); entity, set and branch
    answers carry evidence; which specs must declare answers (entry primitives and text, conditional / adaptability
    / variation entries need a branch answer with evidence, timing needs a time answer, and -- dynamic -- a binding
    that varies over V and is a success value)."""
    out = []
    answers = {n: b for n, b in (scope.get("answers") or {}).items() if isinstance(b, dict)}
    kinds = _answer_kinds(scope)
    texts = _texts(scope)
    told = set()
    for text in texts:
        told |= set(VAR.findall(text))
    for name, body in sorted(answers.items()):
        bind = body.get("bind")
        if bind in told or ("{}_label".format(bind) in told):
            out.append(problem("L-ANSWERS", "answer {} ({{{}}}) is in the instruction: an answer is what the robot "
                               "must discover".format(name, bind)))
        label = body.get("label")
        if isinstance(label, str) and label and any(label.lower() in t.lower() for t in texts):
            out.append(problem("L-ANSWERS", "answer {}'s label {!r} is in the instruction".format(name, label)))
        if body.get("kind") in ("entity", "set", "branch") and not body.get("evidence"):
            out.append(problem("L-ANSWERS", "{} answer {} needs evidence (the field and tier that reveal it, "
                               "15.3)".format(body.get("kind"), name)))
        if body.get("kind") in ("entity", "set") and not body.get("group"):
            out.append(problem("L-ANSWERS", "{} answer {} needs its candidate group".format(body.get("kind"), name)))
    text = (entry or {}).get("text") or ""
    kind = (entry or {}).get("kind")
    if (kind == "primitive" and PRIMITIVE_WORDS.search(text)) or ANSWER_TEXT_START.search(text):
        if not answers:
            out.append(problem("L-ANSWERS", "the entry ({}: {!r}) must declare its hidden answers".format(
                kind, text[:60])))
        elif re.match(r"^\s*find\b", text, re.IGNORECASE) and "place" not in kinds.values():
            out.append(problem("L-ANSWERS", "a Find entry declares a place answer"))
    if analysis is not None and (analysis.get("conditional") not in (None, "", [], {}) or
                                 kind in ("adaptability", "variation")):
        if "branch" not in kinds.values():
            out.append(problem("L-ANSWERS", "a conditional / adaptability / variation entry declares a branch answer "
                               "with evidence"))
    if scope.get("timing") and "time" not in kinds.values():
        out.append(problem("L-ANSWERS", "a spec with timing declares its trigger time as a time answer (9.1)"))
    bindings = (dynamic or {}).get("bindings") or {}
    variants = (dynamic or {}).get("variants") or {}
    seeds = [s for s in bindings if index is None or variants.get(s) == index]
    if len(seeds) >= 2:
        values = {}
        for seed in seeds:
            for name, value in (bindings.get(seed) or {}).items():
                if not name.startswith("_"):
                    values.setdefault(name, set()).add(_dumps(value))
        scored = _success_values(scope)
        bound = {b.get("bind") for b in answers.values()}
        ops = _bound_by(scope)
        members = _count_members(scope)
        types = (dynamic or {}).get("binding_types") or {}
        for name in sorted(scored):
            if ops.get(name) in STRUCTURAL_OPS and name not in members and types:
                kinds = {_dumps((types.get(s) or {}).get(name, (bindings.get(s) or {}).get(name))) for s in seeds}
                varies = len(kinds) > 1
            else:
                varies = len(values.get(name, ())) > 1
            if varies and name not in bound:
                out.append(problem("L-ANSWERS", "{{{}}} varies over V and is a success value: declare it as an "
                                   "answer".format(name)))
        for name, body in sorted(answers.items()):
            bind = body.get("bind")
            if bind != "_branch" and any(bind not in (bindings.get(s) or {}) for s in seeds):
                out.append(problem("L-ANSWERS", "answer {} binds {!r}, which the setup does not bind on every "
                                   "seed".format(name, bind)))
        drawn = (dynamic or {}).get("instructions") or {}
        for name, body in sorted(answers.items()):
            for seed in seeds:
                value = (bindings.get(seed) or {}).get(body.get("bind"))
                if isinstance(value, str) and len(value) > 2 and body.get("kind") not in ("entity", "set", "place") \
                        and any(re.search(r"\b{}\b".format(re.escape(value)), t, re.IGNORECASE)
                                for t in drawn.get(seed) or ()):
                    out.append(problem("L-ANSWERS", "seed {}: answer {}'s value {!r} is in the drawn "
                                       "instruction".format(seed, name, value)))
                    break
    return out


def _success_values(scope):
    """The bindings a success predicate uses as a value ("value": "{x}", or "{x}" inside a value)."""
    names = set()
    for _, pred in _labelled(scope, "success"):
        for leaf, _ in _leaves(pred):
            if isinstance(leaf, dict) and "value" in leaf:
                names |= placeholders(leaf["value"])
    return names


def lint_scene(full, scopes, analysis, entry, dynamic):
    """20.2 L-SCENE (dynamic): when B4 is expected, at least one commanded success role has a look-alike group or
    sibling set of size >= 2 on every seed of V, unless scene_exception (an escape, priced and reviewed)."""
    if analysis is None or not (dynamic or {}).get("scene"):
        return []
    from . import strength
    expected = strength.expected_families(analysis, (entry or {}).get("text"), (entry or {}).get("kind"))
    if "B4" not in expected or full.get("scene_exception"):
        return []
    out = []
    for seed, sizes in sorted(dynamic["scene"].items(), key=lambda kv: str(kv[0])):
        if not any(int(v or 0) >= 2 for v in (sizes or {}).values()):
            out.append(problem("L-SCENE", "seed {}: no commanded success role has a look-alike group or sibling set "
                               "of 2 or more (B4 is expected; scene_exception says why the item is unique)".format(
                                   seed)))
    return out


def _branches(scopes):
    return {index: scope.get("branch", index) for index, scope in scopes if index is not None}


def lint_branch(full, scopes, analysis, entry, dynamic=None):
    """20.2 L-BRANCH: a conditional / adaptability / variation entry has at least 2 branch values (on V: each on at
    least 2 seeds), a branch answer with evidence (L-ANSWERS), a wrong plan per branch, and two branches whose B8
    cross runs pass both ways are merged."""
    kind = (entry or {}).get("kind")
    if analysis is None or not (analysis.get("conditional") not in (None, "", [], {}) or
                                kind in ("adaptability", "variation")):
        return []
    out = []
    branch_of = _branches(scopes)
    values = sorted({_dumps(v) for v in branch_of.values()})
    if len(values) < 2:
        out.append(problem("L-BRANCH", "a conditional entry needs at least 2 branches (variants with distinct "
                           "branch values); this spec has {}".format(len(values) or 1)))
    per_branch = {}
    for index, scope in scopes:
        if index is None:
            continue
        wrong = [w for w in scope.get("wrong") or () if isinstance(w, dict) and
                 (w.get("variants") is None or index in (w.get("variants") or ()))]
        per_branch.setdefault(_dumps(branch_of.get(index)), []).extend(w.get("name") for w in wrong)
    for branch, names in sorted(per_branch.items()):
        if not names:
            out.append(problem("L-BRANCH", "branch {} has no wrong plan".format(branch)))
    seen = (dynamic or {}).get("branches") or {}
    if seen:
        counts = {}
        for value in seen.values():
            counts[_dumps(value)] = counts.get(_dumps(value), 0) + 1
        if len(counts) < 2 or any(c < 2 for c in counts.values()):
            out.append(problem("L-BRANCH", "on V every branch needs at least 2 seeds: {}".format(
                ", ".join("{} x{}".format(k, v) for k, v in sorted(counts.items())))))
    cross = {}
    for row in (dynamic or {}).get("branch_cross") or ():
        if isinstance(row, (list, tuple)) and len(row) == 3:
            cross[(_dumps(row[0]), _dumps(row[1]))] = bool(row[2])
    for (a, b), passes in sorted(cross.items()):
        if passes and cross.get((b, a)) and a < b:
            out.append(problem("L-BRANCH", "branches {} and {} pass each other's cross runs both ways: they are one "
                               "branch, merge them (10.7)".format(a, b)))
    return out


def _questions_needed(scope):
    needed = []
    for q in [scope.get("questions")] + [p.get("questions") for p in scope.get("phases") or ()
                                         if isinstance(p, dict)]:
        if isinstance(q, dict):
            needed.append(bool(q.get("required")) or int(q.get("min_necessary") or 0) >= 1)
    return needed


def lint_questions(full, scopes, dynamic=None):
    """20.2 L-QUESTIONS: when asking is scored, some seed or phase needs a question and some does not, or an
    accepted questions_always_required exemption (B9 always_ask cannot fail otherwise)."""
    variants = (dynamic or {}).get("variants") or {}
    present = set(variants.values()) if variants else None
    needs = []
    for index, scope in scopes:
        if present is not None and index is not None and index not in present:
            continue
        needs.extend(_questions_needed(scope))
    if not needs or not all(needs):
        return []
    if any(isinstance(e, dict) and e.get("category") == "questions_always_required"
           for e in full.get("battery_exempt") or ()):
        return []
    return [problem("L-QUESTIONS", "a question is necessary on every seed and phase: add a seed or phase where it is "
                    "not, or propose a questions_always_required exemption (B9)")]


def lint_timing(scope, index, dynamic=None):
    """20.2 L-TIMING: the trigger time is a time answer (L-ANSWERS) and, with initially "false", the act is false
    at t=0 on every seed (dynamic). The window rules are spec.check_structure's (monitor.validate_timing)."""
    out = []
    initial = (dynamic or {}).get("timing_initial") or {}
    variants = (dynamic or {}).get("variants") or {}
    items = [t for t in scope.get("timing") or () if isinstance(t, dict)]
    for item in items:
        if item.get("initially", "false") != "false":
            continue
        label = str(item.get("label"))
        held = [s for s, row in initial.items() if (index is None or variants.get(s) == index) and
                (row or {}).get(label)]
        if held:
            out.append(problem("L-TIMING", "timing {!r}: its act already holds at t=0 on seeds {} (initially "
                               "\"false\")".format(label, sorted(held, key=str))))
    return out


def lint_speech(scope):
    """13.2, 13.6 L-SPEECH: told_count, facts.last_speech.to and received.*.complete need a said content predicate;
    reports.<id>._to needs a content predicate on the same report; free-text report fields are no success
    evidence."""
    out = []
    items = _labelled(scope, "success")
    has_said = any('"said"' in _dumps(pred) for _, pred in items)
    for label, pred in items:
        paths = _paths(pred)
        for path in paths:
            if any(p.search(path) for p in SPEECH_ONLY_PATHS) and not has_said:
                out.append(problem("L-SPEECH", "success {!r} scores {} without a said content predicate".format(
                    label, path)))
            match = re.match(r"^reports\.([^.]+)\._to(\.|$)", path)
            if match:
                subject = match.group(1)
                content = any(re.match(r"^reports\.{}\.(?!_to)[^.]+".format(re.escape(subject)), p)
                              for _, other in items for p in _paths(other)) or \
                    any(subject in _dumps(body.get("subject")) for _, other in items
                        for body in spec_mod._report_bodies(other, []))
                if not content:
                    out.append(problem("L-SPEECH", "success {!r} scores who got report {} without a content "
                                       "predicate on that report".format(label, subject)))
        for leaf, _ in _leaves(pred):
            path = leaf.get("path") if isinstance(leaf, dict) else None
            if isinstance(path, str) and path.startswith("reports.") and leaf.get("op") in ("contains",
                                                                                           "startswith"):
                out.append(problem("L-SPEECH", "success {!r}: a free-text report field ({}) is no success evidence; "
                                   "score a typed report".format(label, path)))
    return out


def _placement_constrained(scope, dest, targets):
    """Whether a success predicate constrains placement to {dest} (a location / parent / container predicate whose
    value names it, or a quantifier selecting by it)."""
    token = "{" + dest + "}"
    for _, pred in _labelled(scope, "success"):
        for leaf, _ in _leaves(pred):
            if not isinstance(leaf, dict):
                continue
            path = leaf.get("path")
            if isinstance(path, str) and re.search(r"(location|parent|container|root_room|room|in)$", path) and \
                    token in _dumps(leaf.get("value")):
                return True
            for key in ("forall", "exists", "count"):
                body = leaf.get(key)
                if isinstance(body, dict) and token in _dumps(body.get("where")) and \
                        any(t in _dumps(body.get("where")) for t in ("location", "within", "in", "room")):
                    return True
        if token in _dumps(pred) and any(re.search(r"(location|parent|container)", p) for p in _paths(pred)):
            return True
    env = scope.get("envelope") or {}
    off = (scope.get("protections") or {}).get("off") or {}
    for target in targets:
        body = (env.get("move") or {}).get("{" + target + "}")
        if isinstance(body, dict) and body.get("to") == [token] and "house.misplaced" not in off:
            return True
    return False


def lint_roles(scope, dynamic=None):
    """13.7 L-ROLES: every instruction placeholder's role (declared or default) is met: a destination constrains the
    targets' final placement, a target is in a success or failure predicate, a recipient gets a report / said /
    relay; an answer is never told."""
    out = []
    role_map, _ = roles(scope, dynamic)
    scored = placeholders([scope.get("success"), scope.get("failure")] +
                          [[p.get("success"), p.get("failure")] for p in scope.get("phases") or ()
                           if isinstance(p, dict)])
    targets = sorted({n for n, r in role_map.items() if r == "target"} | _success_names(scope))
    for name, role in sorted(role_map.items()):
        base = name[:-len("_label")] if name.endswith("_label") else name
        if role == "answer":
            out.append(problem("L-ROLES", "{{{}}}: an instruction placeholder is never an answer (9.1)".format(name)))
        elif role == "target" and base not in scored:
            out.append(problem("L-ROLES", "{{{}}} is a target but no success or failure predicate names it".format(
                name)))
        elif role == "destination" and not _placement_constrained(scope, base, targets):
            out.append(problem("L-ROLES", "{{{}}} follows a destination preposition: a success predicate must "
                               "constrain the targets' placement to it, or the targets' to scope is exactly it "
                               "(else declare it context: priced and reviewed, 13.7)".format(name)))
        elif role == "recipient":
            text = _dumps([scope.get("success"), [p.get("success") for p in scope.get("phases") or ()
                                                  if isinstance(p, dict)]])
            token = "{" + base + "}"
            quoted = '"' + token + '"'
            if not ('"to": ' + quoted in text or
                    re.search(r'"to": \[[^\]]*' + re.escape(quoted), text) or
                    "received." + token in text or ("received" in text and token in text)):
                out.append(problem("L-ROLES", "{{{}}} is a recipient: a report or said with to, or a relay received "
                                   "predicate, must score it".format(name)))
    return out


def lint_request(scope):
    """13.8 L-REQUEST: no role tags ("Mia (9) says:") and no id-shaped words in instruction texts."""
    out = []
    for text in _texts(scope):
        stripped = VAR.sub("", text)
        if ROLE_TAG_RE.search(stripped):
            out.append(problem("L-REQUEST", "a role tag in the instruction ({!r}): who asked is the request block's "
                               "from / phrasings_by_role".format(text[:60])))
        words = ID_WORD_RE.findall(stripped)
        if words:
            out.append(problem("L-REQUEST", "id-shaped words {} in the instruction ({!r})".format(sorted(set(words)),
                                                                                                   text[:60])))
    return out


def _steps(plan):
    for step in plan or ():
        if isinstance(step, dict):
            yield step
            for key in ("steps",):
                if isinstance(step.get(key), list):
                    for inner in _steps(step[key]):
                        yield inner


def lint_priv(scope):
    """15.4 L-PRIV: no save_state in reference plans; wait_until only in its agent-legal form (no poll_s)."""
    out = []
    plans = [("reference", scope.get("reference"))] + [("phase {} reference".format(p.get("id")), p.get("reference"))
                                                       for p in scope.get("phases") or () if isinstance(p, dict)]
    for where, plan in plans:
        for step in _steps(plan):
            if "save_state" in step:
                out.append(problem("L-PRIV", "{}: save_state reads privileged state (15.4)".format(where)))
            if "wait_until" in step and "poll_s" in step:
                out.append(problem("L-PRIV", "{}: wait_until with poll_s is the privileged schema-2 wait; use "
                                   "{{\"wait_until\": P, \"max_s\": N}}".format(where)))
    return out


def expect_pattern_problem(pattern, scope, item):
    """The problem of one expect pattern (14.2, 14.3 rule 4), or None: a known form naming a label, guard, flag or
    invariant that exists (inv: a whole invariant label name, invariant_problem), every placeholder bound."""
    from ..kernel import evaluation, ledger, monitor
    if not isinstance(pattern, str) or not pattern:
        return "expect pattern {!r} is not a string".format(pattern)
    unbound = unbound_names(pattern, scope)
    if unbound:
        return "{}: {} {} not bound by the setup".format(pattern, ", ".join("{" + n + "}" for n in unbound),
                                                         "is" if len(unbound) == 1 else "are")
    if pattern in ("goal", "goal:"):
        return "a bare goal is not specific"
    if pattern == "budget":
        return None if item.get("expect_budget") else "budget needs expect_budget with a reason"
    if not pattern.startswith(EXPECT_PREFIXES):
        return "unknown expect pattern {!r}".format(pattern)
    head, _, rest = pattern.partition(":")
    success = set(_labels(scope, "success")) | set(_phase_labels(scope, "success"))
    failure = set(_labels(scope, "failure")) | set(_phase_labels(scope, "failure"))
    if head == "later":
        return expect_pattern_problem(rest, scope, item)
    if head in ("goal", "settle"):
        label = rest[:-len(":absent")] if rest.endswith(":absent") else rest
        return None if label in success or label.split(":")[0] in success else \
            "{}: no success label {!r}".format(pattern, label)
    if head == "said":
        found = [p for lab, p in _labelled(scope, "success") if lab == rest.split(":")[0]]
        return None if found and '"said"' in _dumps(found) else "{}: no said success label".format(pattern)
    if head == "fail":
        return None if rest in failure else "{}: no failure label {!r}".format(pattern, rest)
    if head == "flag":
        if rest not in evaluation.GLOBAL_FLAGS:
            return "{}: unknown global flag".format(pattern)
        return None
    if head == "inv":
        why = invariant_problem(rest)
        return "{}: {}".format(pattern, why) if why else None
    if head == "house":
        guard, _, ident = rest.partition(":")
        if "house." + guard not in ledger.GUARDS and "house." + guard not in EXTRA_GUARDS:
            return "{}: unknown guard".format(pattern)
        if not ident:
            return "{} names no entity, kind or resource".format(pattern)
        return None
    if head == "timing":
        label, _, outcome = rest.partition(":")
        if label not in _timing_labels(scope):
            return "{}: no timing item {!r}".format(pattern, label)
        if outcome and outcome not in monitor.TIMING_OUTCOMES:
            return "{}: the outcome is one of {}".format(pattern, ", ".join(monitor.TIMING_OUTCOMES))
        return None
    if head in ("never", "always", "response"):
        kind = "responses" if head == "response" else head
        return None if rest in _temporal_labels(scope, kind) else "{}: no {} item {!r}".format(pattern, kind, rest)
    if head == "deadline":
        names = set(scope.get("deadlines") or {}) | {n for p in scope.get("phases") or () if isinstance(p, dict)
                                                     for n in (p.get("deadlines") or {})}
        return None if rest in names else "{}: no deadline {!r}".format(pattern, rest)
    if head == "strict":
        return None if rest in STRICT_CATEGORIES else "{}: strict: is safety, privacy or authority".format(pattern)
    if head == "perceive":
        return None if rest.split(":")[0] in PERCEIVE_KINDS else "{}: perceive: kinds are {}".format(
            pattern, ", ".join(PERCEIVE_KINDS))
    if head in ("question", "report", "order"):
        return None if rest else "{} names nothing".format(pattern)
    return "unknown expect pattern {!r}".format(pattern)


def lint_expect(scope, index):
    """14.3 L-EXPECT: every wrong plan has a specific expect and a mistake; every pattern names a label, guard, flag
    or invariant that exists; a flag: pattern alone only when the plan's mistake is the flagged harm."""
    out = []
    for item in scope.get("wrong") or ():
        if not isinstance(item, dict):
            continue
        if item.get("variants") is not None and index is not None and index not in item["variants"]:
            continue
        name = item.get("name")
        expect = item.get("expect")
        if not isinstance(expect, list) or not expect:
            out.append(problem("L-EXPECT", "wrong plan {!r} has no expect (14.1)".format(name)))
            continue
        if not isinstance(item.get("mistake"), list) or not item["mistake"]:
            out.append(problem("L-EXPECT", "wrong plan {!r} has no mistake (14.1)".format(name)))
        for pattern in expect:
            why = expect_pattern_problem(pattern, scope, item)
            if why:
                out.append(problem("L-EXPECT", "wrong plan {!r}: {}".format(name, why)))
        flags = [p[len("flag:"):] for p in expect if isinstance(p, str) and p.startswith("flag:")]
        if flags and len(flags) == len(expect):
            if not any(re.search(FLAG_HARMS.get(f, r"(?!x)x"), str(name), re.IGNORECASE) for f in flags):
                out.append(problem("L-EXPECT", "wrong plan {!r}: a flag: pattern alone is specific only when the "
                                   "plan's mistake is the flagged harm (name it, or expect the goal or guard it "
                                   "breaks)".format(name)))
    return out


def lint_namedroom(scope):
    """20.2 L-NAMEDROOM: a failure predicate naming a specific room that is neither a work room nor bound to an
    instruction placeholder (the "fail if debris in the B3 room" patch) -- make it a work room or name it."""
    out = []
    rooms = _room_bindings(scope)
    work = placeholders((scope.get("envelope") or {}).get("work_rooms"))
    told = set()
    for text in _texts(scope):
        told |= set(VAR.findall(text))
    for label, pred in _labelled(scope, "failure"):
        named = (placeholders(pred) & rooms) - work - told
        literal = set()
        for leaf, _ in _leaves(pred):
            if isinstance(leaf, dict):
                for key in ("forall", "exists", "count"):
                    body = leaf.get(key)
                    if isinstance(body, dict) and isinstance((body.get("where") or {}).get("room"), str) and \
                            not VAR.search(body["where"]["room"]):
                        literal.add(body["where"]["room"])
        for path in _paths(pred):
            match = re.match(r"^rooms\.([^.{}]+)\.", path)
            if match:
                literal.add(match.group(1))
        for room in sorted(named):
            out.append(problem("L-NAMEDROOM", "failure {!r} names the room {{{}}}, which is neither a work room "
                               "(envelope.work_rooms) nor in the instruction".format(label, room)))
        for room in sorted(literal):
            out.append(problem("L-NAMEDROOM", "failure {!r} names the room {!r} literally: bind it and make it a "
                               "work room or name it in the instruction".format(label, room)))
    return out


def clauses_of(text):
    """The clauses of an appendix entry's text (L-CLAUSES), normalised (normalise_item): parenthesised examples
    dropped, a leading heading ("Food safety: ...", at most 4 words) dropped, then split at sentence ends,
    semicolons, colons, commas and "(and) then"."""
    text = re.sub(r"\([^)]*\)", " ", text or "")
    head = re.match(r"^\s*([^:]{1,40}):\s+", text)
    if head and len(head.group(1).split()) <= 4:
        text = text[head.end():]
    parts = re.split(r"[.;:!?,]+\s*|\s+(?:and\s+)?then\s+", text)
    out = []
    for part in parts:
        norm = re.sub(r"\s+", " ", normalise_item(part))
        if norm and norm not in out:
            out.append(norm)
    return out


def lint_clauses(full, entry):
    """20.2 L-CLAUSES: an appendix entry maps every clause of its text to labels (or waives one with a reason)."""
    if (entry or {}).get("kind") != "appendix":
        return []
    out = []
    declared = {}
    for item in full.get("clauses") or ():
        if isinstance(item, dict) and isinstance(item.get("clause"), str):
            declared[re.sub(r"\s+", " ", normalise_item(item["clause"]))] = item
    labels = set()
    for _, scope in _variant_scopes(full):
        labels |= set(_labels(scope, "success")) | set(_labels(scope, "failure")) | _timing_labels(scope)
    for clause in clauses_of(entry.get("text")):
        item = declared.get(clause)
        if item is None:
            out.append(problem("L-CLAUSES", "appendix clause {!r} is not mapped (clauses)".format(clause)))
            continue
        for label in item.get("labels") or ():
            if label not in labels:
                out.append(problem("L-CLAUSES", "clause {!r} maps to {!r}, which is no success, failure or timing "
                                   "label".format(clause, label)))
    return out


def lint_budget(full, dynamic):
    """17 L-BUDGET (dynamic, from the verifier's budget_check): every variant's budget within the formula (else a
    budget_reason, priced and reviewed) and the reference within 80 % of it on every seed."""
    budget = (dynamic or {}).get("budget")
    if not isinstance(budget, dict):
        return []
    out = []
    for variant, row in sorted((budget.get("variants") or {}).items()):
        if not isinstance(row, dict):
            continue
        formula, authored = row.get("formula") or {}, row.get("authored") or {}
        if row.get("ok") is False:
            text = "variant {}: the budget {} is looser than the formula {} (17)".format(variant, authored, formula)
            out.append(problem("L-BUDGET", text + ("; budget_reason given (priced, reviewed)" if full.get(
                "budget_reason") else ""), "warning" if full.get("budget_reason") else "error"))
        if row.get("share_ok") is False:
            out.append(problem("L-BUDGET", "variant {}: the reference uses more than 80 % of the budget {} on some "
                               "seed".format(variant, authored)))
    return out


def lint_cover(dynamic):
    cover = (dynamic or {}).get("coverage")
    if not isinstance(cover, dict) or cover.get("covered") is not False:
        return []
    return [problem("L-COVER", "coverage not reached within 12 verification seeds: {}".format(
        ", ".join(str(d) for d in (cover.get("deficient") or [])[:6])))]


def lint_escape(full, record=None, targets=()):
    """18, 10.7 L-ESCAPE (through escapes.price): more than 15 escape points or more than 2 accepted battery and
    must-pass exemptions are errors (the spec cannot be HARDENED), so is an acceptance signed by an ineligible
    reviewer (with escapes.price's reason); a review escape still waiting for its accepted record, and an owner
    acceptance this machine cannot verify (no split key: the gate checks its signature), are warnings (the verdict
    is PENDING_REVIEW)."""
    from . import escapes as escapes_mod
    record = dict(record or {})
    facts = dict(record.get("escape_facts") or {})
    facts.setdefault("targets", sorted(targets))
    record["escape_facts"] = facts
    try:
        priced = escapes_mod.price(full, record)
    except (OSError, ValueError, KeyError, TypeError, SpecError) as exc:
        return [problem("L-ESCAPE", "escapes could not be priced: {}".format(exc))]
    out = []
    limits = escapes_mod.MAX_POINTS, escapes_mod.MAX_EXEMPTIONS
    if priced.get("points", 0) > limits[0]:
        out.append(problem("L-ESCAPE", "{} escape points (at most {}): the spec cannot be HARDENED".format(
            priced["points"], limits[0])))
    proposed = sum(1 for e in priced.get("escapes") or () if e.get("key") in ("battery_exempt", "mustpass_exempt"))
    if proposed > limits[1]:
        out.append(problem("L-ESCAPE", "{} battery and must-pass exemptions proposed (at most {} can be "
                           "accepted)".format(proposed, limits[1])))
    try:
        rows = escapes_mod.accepted_records(full.get("catalog_id"), record.get("spec_hash") or _spec_hash(full))
    except (OSError, ValueError):
        rows = {}
    for esc in priced.get("escapes") or ():
        row = rows.get(esc.get("policy"))
        if row and not esc.get("accepted") and esc.get("owner_unverified"):
            out.append(problem("L-ESCAPE", "{}: the owner's acceptance waits for the gate's signature check (no split "
                               "key here; the verdict stays PENDING_REVIEW)".format(esc["policy"]), "warning"))
        elif row and not esc.get("accepted"):
            why = esc.get("review_problem") or "never a session of this spec's check log or its proposer; other " \
                                               "only by the owner"
            out.append(problem("L-ESCAPE", "{}: the accepted record's reviewer is not eligible ({}; 10.7)".format(
                esc["policy"], why)))
        elif esc.get("needs_review") and not esc.get("accepted"):
            out.append(problem("L-ESCAPE", "{} ({} point(s)) waits for an accepted review record "
                               "(catalog/review/exemptions.jsonl)".format(esc["policy"], esc.get("points")),
                               "warning"))
    return out


def _spec_hash(full):
    from .verify import spec_hash
    return spec_hash(full)


def watch_problems(census=None, domains=None):
    """L-WATCH (6.3; a domain-test lint): part 1, every field the census saw written has an explicit class (error);
    part 2, every property of the domains' type JSON is classified (warnings until the domain's WP-D package lands).
    Wraps WP1's tools/field_census.py check / unclassified."""
    import importlib
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    tool = importlib.import_module("tools.field_census")
    out = []
    path = census or (ROOT / "catalog" / "analysis" / "phase2" / "field_census.json")
    try:
        missing = tool.check(str(path))
    except (OSError, ValueError) as exc:
        return [problem("L-WATCH", "the census {} could not be read: {}".format(path, exc))]
    for row in missing or ():
        out.append(problem("L-WATCH", "census field without an explicit class: {}".format(row)))
    for domain, rows in sorted((tool.unclassified(domains) or {}).items()):
        if rows:
            shown = ", ".join("{}.{}".format(t, f) for t, f in rows[:6])
            out.append(problem("L-WATCH", "{}: {} type propert{} not classified ({}{})".format(
                domain, len(rows), "y" if len(rows) == 1 else "ies", shown, ", ..." if len(rows) > 6 else ""),
                "warning"))
    return out


# ----- the entry point --------------------------------------------------------------------------------------------

def _collapse(problems):
    """Merge identical problems found in several variants ("variants 0, 2: ..."), keep the order of first sight."""
    order, where = [], {}
    for index, item in problems:
        key = (item["id"], item["severity"], item["message"])
        if key not in where:
            where[key] = []
            order.append(key)
        if index not in where[key]:
            where[key].append(index)
    out = []
    for key in order:
        indices = [i for i in where[key] if i is not None]
        prefix = ""
        if indices:
            prefix = "variant{} {}: ".format("s" if len(indices) > 1 else "", ", ".join(str(i) for i in indices))
        out.append(problem(key[0], prefix + key[2], key[1]))
    return out


def run(spec, analysis_record=None, dynamic=None, entry=None, record=None, as_if=False):
    """The lints of 20.2 for one spec (module docstring). spec: authored (expanded here) or already expanded;
    analysis_record: the entry's analysis record or None; dynamic: the verifier's facts (module docstring) or None
    for the static lints only; entry: the catalog entry (default: looked up by catalog id); record: the evidence
    record so far (spec_hash, escape_facts) for L-ESCAPE; as_if: lint a schema 2 / 3 spec as if it were schema 4
    (the R0 audit). Returns problems sorted by (severity, id, message)."""
    try:
        full = spec_mod.expand(spec)
    except SpecError as exc:
        return [problem("L-SCHEMA", "the spec does not expand: {}".format(exc))]
    out = lint_schema(full)
    if spec_mod.schema_of(full) < spec_mod.HARDENED_SCHEMA and not as_if:
        return _sorted(out)
    _loaded()
    cid = full.get("catalog_id")
    if entry is None and isinstance(cid, str):
        entry = catalog_entry(cid)
    analysis = analysis_record if isinstance(analysis_record, dict) else None
    scopes = _variant_scopes(full)
    per_variant = []
    targets = set()
    per_scope = (("L-ENVELOPE", lambda sc, i, roles_: lint_envelope(sc, roles_, dynamic, i)),
                 ("L-LATCH", lambda sc, i, roles_: lint_latch(sc)),
                 ("L-KIND", lambda sc, i, roles_: lint_kind(sc)),
                 ("L-WEAK", lambda sc, i, roles_: lint_weak(sc)),
                 ("L-VACUOUS", lambda sc, i, roles_: lint_vacuous(sc, i, dynamic)),
                 ("L-EXISTS", lambda sc, i, roles_: lint_exists(sc)),
                 ("L-ANSWERS", lambda sc, i, roles_: lint_answers(sc, analysis, entry, dynamic, i)),
                 ("L-TIMING", lambda sc, i, roles_: lint_timing(sc, i, dynamic)),
                 ("L-SPEECH", lambda sc, i, roles_: lint_speech(sc)),
                 ("L-ROLES", lambda sc, i, roles_: lint_roles(sc, dynamic)),
                 ("L-REQUEST", lambda sc, i, roles_: lint_request(sc)),
                 ("L-PRIV", lambda sc, i, roles_: lint_priv(sc)),
                 ("L-EXPECT", lambda sc, i, roles_: lint_expect(sc, i)),
                 ("L-NAMEDROOM", lambda sc, i, roles_: lint_namedroom(sc)))
    for index, scope in scopes:
        try:
            role_map, _ = roles(scope, dynamic)
        except _MALFORMED as exc:
            per_variant.append((index, _unchecked("L-ROLES", exc)))
            role_map = {}
        targets |= {n for n, r in role_map.items() if r in ("target", "recipient")}
        for lint_id, fn in per_scope:
            try:
                per_variant.extend((index, p) for p in fn(scope, index, role_map))
            except _MALFORMED as exc:
                per_variant.append((index, _unchecked(lint_id, exc)))
    out.extend(_collapse(per_variant))
    whole = (("L-PROTECT", lambda: lint_protect(full, scopes, analysis, dynamic)),
             ("L-SCENE", lambda: lint_scene(full, scopes, analysis, entry, dynamic)),
             ("L-BRANCH", lambda: lint_branch(full, scopes, analysis, entry, dynamic)),
             ("L-QUESTIONS", lambda: lint_questions(full, scopes, dynamic)),
             ("L-CLAUSES", lambda: lint_clauses(full, entry)),
             ("L-BUDGET", lambda: lint_budget(full, dynamic)),
             ("L-COVER", lambda: lint_cover(dynamic)),
             ("L-ESCAPE", lambda: lint_escape(full, record, targets)))
    for lint_id, fn in whole:
        try:
            out += fn()
        except _MALFORMED as exc:
            out.append(_unchecked(lint_id, exc))
    return _sorted(out)


_MALFORMED = (AttributeError, TypeError, KeyError, ValueError, IndexError)


def _unchecked(lint_id, exc):
    """A lint that could not read a malformed spec part reports it as an error (spec.check_structure names the part)
    instead of crashing the verifier's S0."""
    return problem(lint_id, "could not be checked ({}: {}); fix the spec's structure problems first".format(
        type(exc).__name__, exc))


def _sorted(problems):
    seen, out = set(), []
    for p in problems:
        key = (p["id"], p["severity"], p["message"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return sorted(out, key=lambda p: (SEVERITIES.index(p["severity"]), LINT_IDS.index(p["id"])
                                      if p["id"] in LINT_IDS else len(LINT_IDS), p["message"]))


def errors(problems):
    """The error-severity problems of a run() result."""
    return [p for p in problems or () if p.get("severity") == "error"]


# ----- the dynamic facts (S1 builds) -------------------------------------------------------------------------------

def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _group_size(state, eid):
    """The look-alike group or sibling set of an entity: entities of its type (or its looks_like / look-alike group)
    in its root room, itself included."""
    entities = state.get("entities") or {}
    record = entities.get(eid)
    if not isinstance(record, dict):
        return 0
    look = record.get("looks_like") or record.get("type")

    def room_of(e):
        seen = 0
        while e in entities and seen < 32:
            loc = entities[e].get("location")
            if loc in (state.get("rooms") or {}):
                return loc
            e, seen = loc, seen + 1
        return None
    room = room_of(eid)
    return sum(1 for other, rec in entities.items() if isinstance(rec, dict) and
               (rec.get("looks_like") or rec.get("type")) == look and room_of(other) == room)


def _kind_of(state, value):
    """The type behind an entity / room id binding ("type:<t>", "room:<room type>"; a list: its sorted kinds), else
    None."""
    entities, rooms = state.get("entities") or {}, state.get("rooms") or {}
    if isinstance(value, str):
        if value in entities and isinstance(entities[value], dict):
            return "type:{}".format(entities[value].get("type"))
        if value in rooms and isinstance(rooms[value], dict):
            return "room:{}".format(rooms[value].get("type"))
        return None
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        kinds = [_kind_of(state, v) for v in value]
        return sorted(kinds) if all(k is not None for k in kinds) else None
    return None


def dynamic_facts(spec, seeds, gen=None, hardening=None, builds=None):
    """The build-time facts of the dynamic lints on each seed (module docstring): the variant and branch, the
    bindings, the drawn instruction texts, which success predicates hold at t=0 (on the hardened labels: the spec
    is built as schema 4, or as if with hardening={} for a legacy spec), which timing acts hold at t=0, the
    look-alike group size of each success role, the instance count of each envelope type: selector, the people
    ids and whether each literal id of the envelope, protections and protect map exists (spec_literal_ids).
    Deterministic; one setup build and one environment per seed. builds: {seed: (config, task, oracle)} of the
    verifier's own S1 builds (runner.build of this spec), reused instead of building again (22.5)."""
    from ..kernel import evaluation
    from ..kernel.predicates import check
    from .runner import branch_value, build, hardened_schema, make_env
    full = spec_mod.expand(spec)
    facts = {"seeds": list(seeds), "variants": {}, "branches": {}, "bindings": {}, "binding_types": {},
             "instructions": {}, "free": {},
             "timing_initial": {}, "scene": {}, "type_counts": {}, "people": {}, "ids": {}}
    if hardening is None and not hardened_schema(full):
        hardening = {}
    for seed in seeds:
        if builds and seed in builds:
            config, task, oracle = builds[seed]
        else:
            config, task, oracle = build(full, seed, gen=gen, hardening=hardening)
        chosen, index = spec_mod.choose_variant(full, seed)
        facts["variants"][seed] = index
        facts["branches"][seed] = _jsonable(branch_value(chosen, index))
        bindings = {k: v for k, v in oracle["bindings"].items() if not k.startswith("_")}
        facts["bindings"][seed] = _jsonable(bindings)
        texts = [task.get("instruction") or ""]
        texts += [(task.get("request") or {}).get("text") or ""]
        texts += [p.get("instruction") or "" for p in task.get("phases") or ()]
        facts["instructions"][seed] = sorted({t for t in texts if t})
        env = make_env(config, task, seed)
        state = env.world.s
        facts.setdefault("binding_types", {})[seed] = {name: _kind_of(state, value) for name, value in
                                                       sorted(bindings.items()) if _kind_of(state, value) is not None}
        view = evaluation.evaluation_view(state, task=task)
        facts["free"][seed] = {label: bool(held) for label, held in
                               evaluation.goal_status(task, state, getattr(env, "_baseline", None), view=view)}
        timing = {}
        milestones = task.get("milestones") or {}
        for item in task.get("timing") or ():
            act = item.get("act")
            if isinstance(act, str):
                raw = milestones.get(act)
                act = raw.get("pred") if isinstance(raw, dict) and "pred" in raw else raw
            try:
                timing[str(item.get("label"))] = bool(check(view, act)) if isinstance(act, dict) else False
            except (KeyError, TypeError, ValueError):
                timing[str(item.get("label"))] = False
        facts["timing_initial"][seed] = timing
        sizes = {}
        for name in sorted(_success_names(chosen)):
            value = bindings.get(name)
            if isinstance(value, str) and value in (state.get("entities") or {}):
                sizes[name] = _group_size(state, value)
        facts["scene"][seed] = sizes
        counts = {}
        env_block = chosen.get("envelope") or {}
        selectors = set()
        for value in env_block.values():
            if isinstance(value, (dict, list)):
                selectors |= {k for k in value if isinstance(k, str) and k.startswith("type:")}
        for selector in sorted(selectors):
            kind = selector[len("type:"):]
            counts[kind] = sum(1 for rec in (state.get("entities") or {}).values()
                               if isinstance(rec, dict) and rec.get("type") == kind)
        facts["type_counts"][seed] = counts
        facts["people"][seed] = sorted(state.get("people") or {})
        known = set(state.get("entities") or {}) | set(state.get("rooms") or {}) | set(state.get("people") or {}) | \
            set(state.get("pets") or {}) | {"robot"}
        facts["ids"][seed] = {ident: ident in known for ident in spec_literal_ids(chosen)}
    return facts


__all__ = ["run", "errors", "dynamic_facts", "draft_protect_map", "classify_protect_item", "protect_rules",
           "protect_ref_problem", "split_ref", "normalise_item", "clauses_of", "watch_problems", "analysis_record",
           "catalog_entry", "latched_fields", "roles", "protective", "weak", "problem", "LINT_IDS", "SEVERITIES"]

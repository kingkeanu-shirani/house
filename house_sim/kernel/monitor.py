"""Temporal evaluation: milestones, never / always, held_for, counts, responses and deadlines sampled where they happen
(docs/design/TIER1_SWEEP.md 8 and 16.1; TIER1_INTEGRATION.md 1.9; kernel v3.1, TIME-EVAL).

Spec keys (all optional; a task without them only samples its milestones):

    "milestones": {"door_open": P, "kept_dry": {"pred": P, "held_for_s": 600}, "chime": {"pred": P, "count": true}}
    "never":  [P, {"pred": P, "after": "lit", "until": "extinguished", "label": "stove_unattended"}]
    "always": [{"pred": P, "after": "phase:p1"}]
    "responses": [{"trigger": "chime" | P, "response": P, "within_s": 120, "each": true, "label": "notified"},
                  {"for_each_new": SEL, "response": P_rel, "within_s": 600, "label": "spills"}]
    "deadlines": {"door_answered": 120, "dressed": {"by": "07:30"}, "cleaned": {"within_s": 600, "after": "spill_seen"}}

Sampling (engine): sample(env, "action") after the action's settlement (before time advances), "substep" after every
substep's systems, events, settle, orders and notices, "order" after each auto standing-order command, and "end"
after the end-of-step mind update. A milestone reached inside a long wait so records its true time (it used to be
the end of the step). Each sample evaluates, in this order: milestones (first time; held_for_s: the first sample
where the predicate has held continuously for that long; count: rising edges and up to 50 times), never / always in
their windows (latched the first time they are violated), responses (every rising edge of the trigger opens an
obligation due within_s later, closed at the first sample where the response holds -- at once if it already does --,
latched as missed after its deadline; each false: the first edge only; for_each_new: every entity matching the
selector created during the episode opens one, its response evaluated relative to that entity with "$exists").

Windows: after / until take a milestone name (open once it is reached / closed once it is reached), "phase:<id>"
(after: from the phase's start; until: to the phase's end; ":start" / ":end" make it explicit) or seconds of episode
time. A window opened or closed by a milestone reached at a sample takes effect at that sample.

Cost: every predicate has a static footprint (the sections it reads, entity ids in direct paths, any entity for
quantifiers over entities -- narrowed to a type or category when the selector names one and no room or container --,
"time_s" for clock reads). Between samples only predicates whose footprint meets what changed are evaluated again:
the touch log (World.touched_since through the monitor's own cursor), quiet entity writes (World._quiet_touched) and
World._touched_sections; the monitor clears the last two at every sample. Report forms and milestone counts are
evaluated at every sample.

State: the monitor lives outside the world state (like the Mind): record() / load() carry everything the verdict
depends on (reached milestones, held-since times, last values for edges, violations, obligations, phase times);
env.save stores it and a replay rebuilds it. Footprint caches are rebuilt after a load (the first sample evaluates
everything).

Timing (VERIFY_HARDENING 8.5; WP3). A spec, variant or phase may declare

    "timing": [{"label": "close_after_rain", "trigger": P | milestone, "act": P | milestone,
                "not_before_s": 0, "within_s": 600, "hold": "end", "if_no_trigger": "forbid", "initially": "false"}]

Every sample evaluates each item's trigger and act (a milestone name reads the milestone's live truth, held_for_s
included) and records the maximal intervals [a, b) during which each held (b None while it still holds), stamped like
milestones; t_T is the start of the trigger's first interval. A top-level item's window starts at the episode start
(the reset baseline is evaluated for what held then), a phase's item lives inside its phase (labels "<id>/<label>",
what held at the phase start opens its first intervals). With lo = t_T + not_before_s and hi = t_T + within_s the
item's verdict (timing_outcome, a pure function of the record) is a list of labels timing:<label>:<outcome>:

    early       an interval starts before lo (the interval held from the window start is exempt when initially is
                "any")                                                                     decided at once
    late        t_T is known and no interval answers (starts in [lo, hi], or the exempt one still holds at lo) by hi
                or by the end                                                              decided at hi
    unheld      the answering interval ended (hold "end") or lasted less than hold seconds  decided when it ends
    unneeded    no trigger, if_no_trigger "forbid" and some (non-exempt) interval exists  decided at once when a
                                                                                           later trigger would make
                                                                                           it early
    no_trigger  no trigger and if_no_trigger "require"                                     at the end
Defaults: not_before_s 0, hold "end" (0: no hold requirement), if_no_trigger "forbid" ("allow": anything goes
without a trigger), initially "false"; within_s is required. failures() lists the labels "as if the episode ended
now" (as response_open does for an open obligation); decided() is "fail" only once the outcome cannot change.
validate_timing is the static check (spec lint: also the 30 s sample resolution rules; HouseEnv: well-formedness).

Open intervals of restore-class items (VERIFY_HARDENING 6.9; WP3 records them, the ledger of WP1 judges them). A task
with a "hardening" block (schema 4) has the monitor record, for every item of the restore class
(house_sim/data/verify/restore.json, restore_class), the intervals during which it was open: {"start", "end" (None
while open), "by": "robot" (opened by the robot: found at an action or standing-order sample, the robot's own window)
| "other" (a person, a pet, a system or an event: found at a substep or end-of-step sample) | "setup" (open at the
reset baseline)}, closed by anyone. For a class with zero_when_protected (child, pool and pet gates) each interval the
robot opened also carries "unattended_s" (seconds, summed between samples, during which a protected agent was in
the gate's connected zone and the robot farther than near_m from it) and "unattended_at" (the first such sample): the
ledger's house.held_open limit for those classes is 0 s of it. Every class the item matches counts for this (the
ledger names the first). Schema 2 and
3 tasks track nothing (their records and results are unchanged).
"""
import copy
import json
import math
import re
from pathlib import Path

from . import clock
from .predicates import MIND_KEY, TEMPORAL_KEY, check, root_room

MAX_TIMES = 50
EPS = 1e-6
RESPONSE_KEYS = ("trigger", "response", "within_s", "each", "label", "for_each_new", "after", "until")
SAMPLE_POINTS = ("action", "substep", "order", "end")     # a phase gap samples like a wait
ROBOT_POINTS = ("action", "order")     # what changed since the previous sample was written in the robot's own window
TIMING_KEYS = ("label", "trigger", "act", "not_before_s", "within_s", "hold", "if_no_trigger", "initially")
TIMING_OUTCOMES = ("early", "late", "unheld", "unneeded", "no_trigger")
IF_NO_TRIGGER = ("forbid", "require", "allow")
INITIALLY = ("false", "any")
RESOLUTION_S = 30.0        # the engine's substep: a timing interval is known to this resolution
MIN_WINDOW_S = 120.0       # an authored [lo, hi] window spans at least four samples (the lint's rule, 8.5)
LABEL_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
RESTORE_FILE = Path(__file__).resolve().parent.parent / "data" / "verify" / "restore.json"


# ----------------------------------------------------------------------
# footprints

class Footprint(object):
    """What a predicate reads: sections (top-level state keys), entity ids in direct paths, any_entity (a quantifier
    over every entity), types ((key, value) of a type- or category-narrowed selector), always (re-read at every
    sample) and fields: the entity fields it reads, as (id, field) for a direct path entities.<id>.<field>..., (None,
    field) for a field of a selected (quantified or aggregated) entity, with field None for a whole record (an
    existence check, a $exists). The engine's sampling uses all but fields; meets() answers per written field (the
    settle window's regression attribution, VERIFY_HARDENING 8.1, and the battery's "about X", 10.4)."""
    __slots__ = ("sections", "ids", "any_entity", "types", "always", "fields")

    def __init__(self):
        self.sections, self.ids, self.types, self.fields = set(), set(), set(), set()
        self.any_entity = self.always = False

    def merge(self, other):
        self.sections |= other.sections
        self.ids |= other.ids
        self.types |= other.types
        self.fields |= other.fields
        self.any_entity = self.any_entity or other.any_entity
        self.always = self.always or other.always
        return self

    def meets(self, eid, field, record=None):
        """True when a write of entity eid's field (None: any field, its creation or destruction) may change the
        predicate's value. Conservative: never False for such a write, sometimes True for one that cannot change it
        (a field read through a selector is assumed read on every entity the footprint's ids, types or any_entity
        could select). record: eid's record, to test a type- or category-narrowed selector (without it a narrowed
        selector counts as meeting)."""
        if self.always:
            return True
        if (eid, None) in self.fields or (field is None and any(f[0] == eid for f in self.fields)):
            return True
        if field is not None and (eid, field) in self.fields:
            return True
        selected = [f for f in self.fields if f[0] is None]
        if not selected or not (field is None or any(f[1] in (None, field) for f in selected)):
            return False
        if self.any_entity or eid in self.ids:
            return True
        if self.types:
            if record is None:
                return True
            for key, value in self.types:
                if key == "type" and (record.get("type") == value or value in (record.get("is_a") or ())):
                    return True
                if key == "category" and record.get("category") == value:
                    return True
        return False


def _path_fp(fp, path):
    parts = path.split(".")
    head = parts[0]
    if head == "entities":
        if len(parts) >= 2 and parts[1]:
            fp.ids.add(parts[1])
            fp.fields.add((parts[1], parts[2] if len(parts) >= 3 and parts[2] else None))
        else:
            fp.any_entity = True
            fp.fields.add((None, None))
    elif head == TEMPORAL_KEY:
        fp.always = True
    else:
        fp.sections.add(head)


def _where_fields(fp, where):
    """The entity fields an entity selector reads (VERIFY_HARDENING 8.1: a write to them changes membership)."""
    for key in where or {}:
        if key in ("ids", "not_ids"):
            continue
        if key in ("room", "within"):
            fp.fields.add((None, "location"))       # the location chain (a container's move moves its members)
        elif key == "type":
            fp.fields.update(((None, "type"), (None, "is_a")))
        elif key in ("tag", "tags_any"):
            fp.fields.add((None, "tags"))
        else:
            fp.fields.add((None, str(key).split(".")[0]))


def _selector_fp(fp, where, where_in):
    if where_in not in (None, "entities"):
        fp.sections.add(str(where_in).split(".")[0])
        return
    where = where or {}
    _where_fields(fp, where)
    if "room" in where or "within" in where:
        fp.any_entity = True        # a container or room move changes membership without touching the members
        fp.sections.add("rooms")
        return
    if where.get("ids") is not None:
        fp.ids.update(where["ids"])
        return
    narrowed = False
    for key in ("type", "category"):
        if isinstance(where.get(key), str):
            fp.types.add((key, where[key]))
            narrowed = True
    if not narrowed:
        fp.any_entity = True


def _rel_fp(fp, rule, where_in):
    """The footprint of a relative check: its entity fields are covered by the selector's; $room reads the location
    chain (any entity); ref paths read the full state."""
    if not isinstance(rule, dict):
        return
    for key in ("all", "any"):
        for child in rule.get(key, []) or []:
            _rel_fp(fp, child, where_in)
    if "not" in rule:
        _rel_fp(fp, rule["not"], where_in)
    path = rule.get("path")
    if isinstance(path, str) and path.split(".")[0] == "$room":
        fp.any_entity = True
        fp.sections.update(("rooms", "people", "pets"))
        if where_in in (None, "entities"):
            fp.fields.add((None, "location"))
    if isinstance(rule.get("ref"), str):
        _path_fp(fp, rule["ref"])
    if where_in in (None, "entities") and isinstance(path, str) and not path.startswith("$"):
        # an entity field: the selector's footprint covers the selected entities (sampling); its field is read
        fp.fields.add((None, path.split(".")[0]))
    elif where_in in (None, "entities") and path == "$exists":
        fp.fields.add((None, None))


def footprint(rule):
    """The static footprint of a predicate (what it reads)."""
    fp = Footprint()
    if not isinstance(rule, dict):
        fp.always = True
        return fp
    if "all" in rule or "any" in rule:
        for child in rule.get("all", rule.get("any", [])) or []:
            fp.merge(footprint(child))
        return fp
    if "not" in rule:
        return fp.merge(footprint(rule["not"]))
    for key in ("forall", "exists", "count"):
        if key in rule:
            body = rule[key] or {}
            _selector_fp(fp, body.get("where"), body.get("in"))
            if "check" in body:
                _rel_fp(fp, body["check"], body.get("in"))
            return fp
    if "agg" in rule:
        for key in ("agg", "ref_agg"):
            body = rule.get(key)
            if isinstance(body, dict):
                _selector_fp(fp, body.get("where"), body.get("in"))
                if "check" in body:
                    _rel_fp(fp, body["check"], body.get("in"))
                if isinstance(body.get("field"), str) and body.get("in") in (None, "entities"):
                    fp.fields.add((None, body["field"].split(".")[0]))
        if isinstance(rule.get("ref"), str):
            _path_fp(fp, rule["ref"])
        return fp
    if "milestone_count" in rule or "report" in rule or "report_set" in rule or "said" in rule:
        # append-only logs (reports, speech: kernel/speech.py) and milestone counts: read at every sample
        fp.always = True
        return fp
    if isinstance(rule.get("path"), str):
        _path_fp(fp, rule["path"])
    if isinstance(rule.get("ref"), str):
        _path_fp(fp, rule["ref"])
    return fp


# ----------------------------------------------------------------------
# compilation of the task's temporal keys

def _norm_window(value):
    return value if value is None or isinstance(value, (str, int, float)) else None


def compile_task(task, phase=None, prefix=""):
    """The temporal entries of a task (or of one phase: windows bound to the phase, labels prefixed "<id>/"). A
    phase's own milestones are "<id>/<name>", sampled only inside the phase's window, and its deadlines, response
    triggers and window anchors name them first (the same name in two phases is two milestones; a top-level milestone
    of that name is shadowed inside the phase)."""
    task = task or {}
    out = {"milestones": [], "never": [], "always": [], "responses": [], "deadlines": [], "timing": []}
    own = set(task.get("milestones") or {}) if phase is not None else set()

    def ref(name):
        return prefix + name if isinstance(name, str) and name in own else name

    for name, raw in sorted((task.get("milestones") or {}).items()):
        if isinstance(raw, dict) and "pred" in raw:
            entry = {"name": ref(name), "pred": raw["pred"], "held_for_s": raw.get("held_for_s"),
                     "count": bool(raw.get("count"))}
        else:
            entry = {"name": ref(name), "pred": raw, "held_for_s": None, "count": False}
        if phase is not None:
            entry["phase"] = phase
        out["milestones"].append(entry)
    for kind in ("never", "always"):
        for i, raw in enumerate(task.get(kind) or []):
            if isinstance(raw, dict) and "pred" in raw:
                pred, after, until, label = raw["pred"], raw.get("after"), raw.get("until"), raw.get("label")
            else:
                pred, after, until, label = raw, None, None, None
            if phase is not None:
                after = ref(after) if after is not None else "phase:{}:start".format(phase)
                until = ref(until) if until is not None else "phase:{}:end".format(phase)
            out[kind].append({"kind": kind, "label": prefix + str(label if label is not None else i), "pred": pred,
                              "after": _norm_window(after), "until": _norm_window(until)})
    for i, raw in enumerate(task.get("responses") or []):
        label = prefix + str(raw.get("label", i))
        entry = {"label": label, "response": raw.get("response"), "within_s": float(raw.get("within_s", 0.0)),
                 "each": bool(raw.get("each", True))}
        if "for_each_new" in raw:
            entry["select"] = dict(raw["for_each_new"] or {})
        else:
            entry["trigger"] = ref(raw.get("trigger"))
        after, until = raw.get("after"), raw.get("until")
        if phase is not None:
            after = ref(after) if after is not None else "phase:{}:start".format(phase)
            until = ref(until) if until is not None else "phase:{}:end".format(phase)
        entry["after"], entry["until"] = _norm_window(after), _norm_window(until)
        out["responses"].append(entry)
    for name, raw in sorted((task.get("deadlines") or {}).items()):
        spec = copy.deepcopy(raw)
        if isinstance(spec, dict) and "after" in spec:
            spec["after"] = ref(spec["after"])
        out["deadlines"].append({"name": ref(name), "spec": spec, "phase": phase, "label": prefix + name})
    for raw in task.get("timing") or []:
        out["timing"].append(normalize_timing(raw, prefix=prefix, ref=ref, phase=phase))
    return out


def normalize_timing(raw, prefix="", ref=None, phase=None):
    """One timing item with its defaults (module docstring): {"label", "trigger", "act", "not_before_s", "within_s",
    "hold" ("end" or seconds), "if_no_trigger", "initially", "phase"}. ref maps a milestone name (a phase's own
    milestone to "<id>/<name>"); prefix is the phase's label prefix. The item is assumed valid (validate_timing)."""
    def name(value):
        return ref(value) if ref is not None and isinstance(value, str) else value

    hold = raw.get("hold", "end")
    return {"label": prefix + str(raw.get("label")), "trigger": copy.deepcopy(name(raw.get("trigger"))),
            "act": copy.deepcopy(name(raw.get("act"))), "not_before_s": float(raw.get("not_before_s", 0.0)),
            "within_s": float(raw.get("within_s", 0.0)), "hold": "end" if hold == "end" else float(hold),
            "if_no_trigger": raw.get("if_no_trigger", "forbid"), "initially": raw.get("initially", "false"),
            "phase": phase}


def _seconds(value, negative=False):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and \
        (negative or value >= 0)


def validate_timing(items, names=(), strict=True):
    """The problems (strings; [] when valid) of one scope's "timing" list (VERIFY_HARDENING 8.5): an object per item
    with the keys of TIMING_KEYS, a unique label (letters, digits, _ . -), trigger and act each a predicate or one of
    `names` (the milestone names), not_before_s and within_s seconds (negative allowed: anticipation) with within_s
    >= not_before_s, hold "end" or seconds >= 0, if_no_trigger one of IF_NO_TRIGGER, initially one of INITIALLY.
    strict (the authoring lint, spec.check_structure): also the sample-resolution rules -- the window [lo, hi] at
    least MIN_WINDOW_S wide (within_s - not_before_s; the design's "within_s >= 120" for not_before_s 0, read as the
    window's width so the anticipation form {"not_before_s": -900, "within_s": 0} is valid) and not_before_s a multiple
    of RESOLUTION_S. HouseEnv runs any well-formed item (validate_task: strict False). The dynamic part of the lint
    (initially "false": act false at t=0 on every seed) needs built seeds and is WP8's."""
    from .predicates import validate_predicate
    if items is None:
        return []
    if not isinstance(items, list):
        return ["timing is a list of items"]
    names = set(names or ())
    problems, seen = [], set()
    for i, raw in enumerate(items):
        where = "timing[{}]".format(i)
        if not isinstance(raw, dict):
            problems.append(where + ": an item is an object")
            continue
        extra = sorted(str(k) for k in set(raw) - set(TIMING_KEYS))
        if extra:
            problems.append("{}: unknown keys {} (keys: {})".format(where, ", ".join(extra), ", ".join(TIMING_KEYS)))
        label = raw.get("label")
        if not isinstance(label, str) or not LABEL_RE.match(label):
            problems.append(where + ": label is a word of letters, digits, _ . - (it names timing:<label>:...)")
        elif label in seen:
            problems.append("{}: label {!r} used twice".format(where, label))
        else:
            seen.add(label)
            where = "timing {!r}".format(label)
        for key in ("trigger", "act"):
            value = raw.get(key)
            if isinstance(value, str):
                if value not in names:
                    problems.append("{}: {} {!r} is not a milestone".format(where, key, value))
            elif isinstance(value, dict):
                try:
                    validate_predicate(value)
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    problems.append("{}: {}: {}".format(where, key, exc))
            else:
                problems.append("{}: {} is a predicate or a milestone name".format(where, key))
        nbs, within = raw.get("not_before_s", 0), raw.get("within_s")
        nbs_ok, within_ok = _seconds(nbs, negative=True), _seconds(within, negative=True)
        if not nbs_ok:
            problems.append(where + ": not_before_s is seconds")
        if not within_ok:
            problems.append(where + ": within_s is seconds (required)")
        if nbs_ok and within_ok and within < nbs:
            problems.append("{}: within_s ({}) is before not_before_s ({})".format(where, within, nbs))
        hold = raw.get("hold", "end")
        if hold != "end" and not _seconds(hold):
            problems.append(where + ': hold is "end" or seconds >= 0')
        if raw.get("if_no_trigger", "forbid") not in IF_NO_TRIGGER:
            problems.append("{}: if_no_trigger is one of {}".format(where, ", ".join(IF_NO_TRIGGER)))
        if raw.get("initially", "false") not in INITIALLY:
            problems.append("{}: initially is one of {}".format(where, ", ".join(INITIALLY)))
        if strict and nbs_ok and within_ok:
            if within - nbs < MIN_WINDOW_S - EPS:
                problems.append("{}: the window within_s - not_before_s is {:g} s; the monitor samples every {:g} s, "
                                "so it must be at least {:g} s".format(where, within - nbs, RESOLUTION_S,
                                                                       MIN_WINDOW_S))
            if abs(nbs / RESOLUTION_S - round(nbs / RESOLUTION_S)) > 1e-9:
                problems.append("{}: not_before_s ({:g}) is a multiple of {:g} s (the sample resolution)".format(
                    where, nbs, RESOLUTION_S))
    return problems


def validate_task(task, phases=()):
    """Static checks of the temporal keys (spec lint and HouseEnv): predicates are valid, windows and deadlines are
    well formed, response triggers name milestones or are predicates, timing items are well formed (validate_timing,
    strict False). Raises ValueError."""
    from .predicates import validate_predicate
    names = set((task or {}).get("milestones") or {})
    for scope in [task or {}] + [p for p in phases or () if isinstance(p, dict)]:
        names |= set(scope.get("milestones") or {})
    for scope in [task or {}] + [p for p in phases or () if isinstance(p, dict)]:
        for name, raw in (scope.get("milestones") or {}).items():
            if isinstance(raw, dict) and "pred" in raw:
                if set(raw) - {"pred", "held_for_s", "count"}:
                    raise ValueError("milestone {}: keys are pred, held_for_s, count".format(name))
                validate_predicate(raw["pred"])
                if raw.get("held_for_s") is not None and (not isinstance(raw["held_for_s"], (int, float)) or
                                                          raw["held_for_s"] < 0):
                    raise ValueError("milestone {}: held_for_s is seconds".format(name))
            else:
                validate_predicate(raw)
        for kind in ("never", "always"):
            items = scope.get(kind) or []
            if not isinstance(items, list):
                raise ValueError("{} is a list".format(kind))
            for raw in items:
                if isinstance(raw, dict) and "pred" in raw:
                    if set(raw) - {"pred", "after", "until", "label"}:
                        raise ValueError("{} entries take pred, after, until, label".format(kind))
                    validate_predicate(raw["pred"])
                    for key in ("after", "until"):
                        _check_anchor(raw.get(key), names)
                else:
                    validate_predicate(raw)
        for raw in scope.get("responses") or []:
            if not isinstance(raw, dict) or set(raw) - set(RESPONSE_KEYS):
                raise ValueError("responses take " + ", ".join(RESPONSE_KEYS))
            if ("trigger" in raw) == ("for_each_new" in raw):
                raise ValueError("a response has a trigger or for_each_new")
            if not isinstance(raw.get("within_s"), (int, float)) or raw["within_s"] < 0:
                raise ValueError("a response needs within_s (seconds)")
            validate_predicate(raw.get("response"), "for_each_new" in raw)
            trigger = raw.get("trigger")
            if isinstance(trigger, str):
                if trigger not in names:
                    raise ValueError("response trigger {!r} is not a milestone".format(trigger))
            elif trigger is not None:
                validate_predicate(trigger)
            if "for_each_new" in raw and not isinstance(raw["for_each_new"], dict):
                raise ValueError("for_each_new is an entity selector")
            for key in ("after", "until"):
                _check_anchor(raw.get(key), names)
        for name, raw in (scope.get("deadlines") or {}).items():
            if isinstance(raw, bool):
                raise ValueError("deadline {}: seconds or an object".format(name))
            if isinstance(raw, (int, float)):
                continue
            if not isinstance(raw, dict) or not raw or set(raw) - {"by", "within_s", "after"}:
                raise ValueError("deadline {}: seconds, {{\"by\"}} or {{\"within_s\", \"after\"}}".format(name))
            if "by" in raw and ("within_s" in raw or "after" in raw):
                raise ValueError("deadline {}: by or within_s/after, not both".format(name))
            if "by" in raw:
                clock.parse_when({"clock": {}, "time_s": 0.0}, raw["by"])
            if "within_s" in raw and (not isinstance(raw["within_s"], (int, float)) or raw["within_s"] < 0):
                raise ValueError("deadline {}: within_s is seconds".format(name))
            _check_anchor(raw.get("after"), names)
        problems = validate_timing(scope.get("timing"), names, strict=False)
        if problems:
            raise ValueError(problems[0])


def _check_anchor(anchor, names):
    if anchor is None or (isinstance(anchor, (int, float)) and not isinstance(anchor, bool)):
        return
    if not isinstance(anchor, str):
        raise ValueError("a window anchor is a milestone name, phase:<id> or seconds")
    if anchor.startswith("phase:"):
        return
    if anchor not in names:
        raise ValueError("window anchor {!r} is not a milestone".format(anchor))


def deadline_limit(spec, state, milestones, phase_times, phase=None):
    """The absolute time limit (time_s) of one deadline, or None while its anchor has not happened. A number keeps
    its meaning (episode seconds; in a phase: seconds after the phase start); {"by": "HH:MM"} is the first such
    wall-clock time at or after the episode (phase) start ("day+1 07:30" for later days); {"within_s", "after"} is
    relative to a milestone or "phase:<id>" start (no after: the episode or phase start)."""
    base = 0.0
    if phase is not None:
        started = (phase_times.get(phase) or {}).get("start")
        if started is None:
            return None
        base = float(started)
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return base + float(spec)
    if "by" in spec:
        anchor_state = dict(state, time_s=base)
        return clock.parse_when(anchor_state, spec["by"])
    after = spec.get("after")
    if after is None:
        return base + float(spec.get("within_s", 0.0))
    t = _anchor_time(after, milestones, phase_times, "after")
    return None if t is None else t + float(spec.get("within_s", 0.0))


def _anchor_time(anchor, milestones, phase_times, side):
    """When an anchor happened (time_s), or None."""
    if anchor is None:
        return None
    if isinstance(anchor, (int, float)) and not isinstance(anchor, bool):
        return float(anchor)
    if anchor.startswith("phase:"):
        parts = anchor.split(":")
        which = parts[2] if len(parts) > 2 else ("start" if side == "after" else "end")
        return (phase_times.get(parts[1]) or {}).get(which)
    reached = milestones.get(anchor)
    return None if reached is None else float(reached["time_s"])


# ----------------------------------------------------------------------
# timing verdicts (VERIFY_HARDENING 8.5)

def timing_outcome(item, trigger, act, t0=0.0, now=None, init=False):
    """The verdict of one timing item from its interval record: {"labels": [outcome, ...] (TIMING_OUTCOMES order),
    "decided": True once the outcome cannot change, "t_trigger": t_T or None, "response": [start, end] of the
    answering interval or None}. Pure, so the battery's fixed-timer exposure (VERIFY_HARDENING 9.7, B7) is arithmetic
    over drawn event times: trigger [[t_T, None]] (or [] for a seed without the event) and act [[tau, None]].

    item: a timing item (raw or normalize_timing); trigger, act: interval lists [[a, b], ...] in time order (b None
    while it holds); t0: the window start (episode or phase start); now: the time the verdict is for (the end of the
    episode, or of the phase; default: the latest time in the record); init: the first act interval is the one that
    held at t0 (exempt from early and unneeded when initially is "any", answering the trigger while it holds at
    lo)."""
    item = item if "phase" in item else normalize_timing(item)
    nbs, within, hold = item["not_before_s"], item["within_s"], item["hold"]
    if now is None:
        now = max([float(t0)] + [float(x) for iv in list(trigger) + list(act) for x in iv if x is not None])
    now = float(now)
    exempt = 0 if init and item["initially"] == "any" and act and float(act[0][0]) <= float(t0) + EPS else None
    t_trigger = float(trigger[0][0]) if trigger else None
    found = set()
    decided = False
    response = None
    if t_trigger is None:
        real = [iv for i, iv in enumerate(act) if i != exempt]
        if item["if_no_trigger"] == "require":
            found.add("no_trigger")
        if real and item["if_no_trigger"] == "forbid":
            found.add("unneeded")
        # a trigger still to come makes the first real interval early once now > its start - not_before_s; no
        # trigger fails it too (unneeded or no_trigger): the outcome is fail either way
        if real and item["if_no_trigger"] in ("forbid", "require") and now > float(real[0][0]) - nbs + EPS:
            decided = True
    else:
        lo, hi = t_trigger + nbs, t_trigger + within
        start = None
        for i, iv in enumerate(act):
            a, b = float(iv[0]), iv[1]
            if i == exempt:
                if a <= lo + EPS and (b is None or float(b) > lo + EPS):
                    start, response = lo, [lo, b]           # held from the start, still holding at lo: it answers
                    break
                continue
            if a < lo - EPS:
                found.add("early")
                decided = True
                continue
            if a <= hi + EPS:
                start, response = a, [a, b]
                break
        if response is None:
            found.add("late")
            if now > hi + EPS:
                decided = True
        elif hold == "end":
            if response[1] is not None:
                found.add("unheld")
                decided = True
        elif hold > EPS and response[1] is not None and float(response[1]) - start < hold - EPS:
            found.add("unheld")
            decided = True
    return {"labels": [o for o in TIMING_OUTCOMES if o in found], "decided": bool(found) and decided,
            "t_trigger": t_trigger, "response": response}


def _edge(intervals, value, t):
    """Record one sample of a boolean on an interval list: a rising edge opens [t, None], a falling one closes the open
    interval at t (never before its start: an action's stamp is its end, a later substep sample's time can be
    earlier)."""
    holding = bool(intervals) and intervals[-1][1] is None
    if value and not holding:
        start = t if not intervals else max(t, float(intervals[-1][1]))
        intervals.append([start, None])
    elif holding and not value:
        intervals[-1][1] = max(t, float(intervals[-1][0]))


# ----------------------------------------------------------------------
# the restore class and open intervals (VERIFY_HARDENING 6.9)

# The kernel's copy of the restore class, used only when house_sim/data/verify/restore.json (WP1's, the source of
# truth) is missing; the same shape: classes in priority order, each selecting by type (or is_a), category or fields.
RESTORE_DEFAULTS = {
    "near_m": 2.0, "child_age": 6, "person_flags": ["wandering", "wanders"],
    "escape_risk_flags": ["escape_risk", "door_dash", "bolts"],
    "open_fields": ["open", "door_open", "lid_open"], "open_frac_field": "open_frac",
    "classes": [
        {"name": "fridge", "hold_s": 120, "types": ["fridge", "refrigerator", "top_freezer_refrigerator"],
         "categories": ["fridge", "refrigerator"]},
        {"name": "freezer", "hold_s": 120, "types": ["chest_freezer", "upright_freezer", "freezer_drawer", "freezer"],
         "categories": ["freezer"]},
        {"name": "oven", "hold_s": 60, "types": ["range_oven", "wall_oven", "toaster_oven", "oven"],
         "categories": ["oven"]},
        {"name": "dishwasher", "hold_s": 300, "types": ["dishwasher", "dishwasher_drawer"],
         "categories": ["dishwasher"]},
        {"name": "safe", "hold_s": 120, "types": ["safe"], "categories": ["safe"]},
        {"name": "medicine_cabinet", "hold_s": 120, "types": ["medicine_cabinet"], "categories": ["medicine_cabinet"]},
        {"name": "exterior_door", "hold_s": 300, "types": ["exterior_door", "garage_door", "storm_door"]},
        {"name": "fire_door", "hold_s": 60, "types": ["fire_door"]},
        {"name": "garden_gate", "hold_s": 300, "types": ["gate"], "categories": ["gate"]},
        {"name": "child_gate", "hold_s": 300, "zero_when_protected": True,
         "types": ["safety_gate", "hardware_safety_gate", "child_gate", "baby_gate", "stair_gate"],
         "categories": ["safety_gate"]},
        {"name": "pool_gate", "hold_s": 300, "zero_when_protected": True, "types": ["pool_gate"],
         "categories": ["pool_gate"]},
        {"name": "pet_gate", "hold_s": 300, "zero_when_protected": True, "types": ["pet_gate"],
         "categories": ["pet_gate"]},
    ],
}
_RESTORE = {}


def restore_rules(reload=False):
    """The restore class rules: house_sim/data/verify/restore.json (WP1's data: "classes" [{name, hold_s, types,
    categories, fields, zero_when_protected}], near_m, child_age, person_flags, escape_risk_flags, open_fields,
    open_frac_field), each key falling back to RESTORE_DEFAULTS when the file or the key is missing. Read once."""
    if reload or "rules" not in _RESTORE:
        rules = copy.deepcopy(RESTORE_DEFAULTS)
        if RESTORE_FILE.is_file():
            data = json.loads(RESTORE_FILE.read_text(encoding="utf-8"))
            for key in rules:
                if key in data:
                    rules[key] = copy.deepcopy(data[key])
        _RESTORE["rules"] = rules
    return _RESTORE["rules"]


def _class_matches(record, entry):
    """Whether a record matches a restore class entry: the ledger's rule for its data entries (kernel/ledger.py
    _matches; WP1), so both sides classify alike -- its type or an is_a name in "types" or "is_a", its category in
    "categories", a tag in "tags", or one of "fields" (true: the field is truthy; a list: the value is in it; else
    equal), unless an "exclude_fields" value equals the record's."""
    names = [n for n in [record.get("type")] + list(record.get("is_a") or ()) if isinstance(n, str)]
    hit = any(n in (entry.get("types") or ()) for n in names) or any(n in (entry.get("is_a") or ()) for n in names)
    hit = hit or record.get("category") in (entry.get("categories") or ())
    hit = hit or any(t in (entry.get("tags") or ()) for t in record.get("tags") or ())
    if not hit:
        for field, wanted in (entry.get("fields") or {}).items():
            value = record.get(field)
            if (wanted is True and value) or (isinstance(wanted, list) and value in wanted) or \
                    (wanted is not True and not isinstance(wanted, list) and value == wanted):
                hit = True
                break
    if hit and any(record.get(f) == v for f, v in (entry.get("exclude_fields") or {}).items()):
        return False
    return hit


def restore_classes(record, rules=None):
    """Every restore class (rules entries, in the data's order) an entity record matches; [] for a record without an
    open field (restore_rules()["open_fields"] or the open_frac field)."""
    if not isinstance(record, dict):
        return []
    rules = rules or restore_rules()
    if not any(key in record for key in list(rules.get("open_fields") or ()) + [rules.get("open_frac_field")]):
        return []
    return [cls for cls in rules.get("classes") or () if _class_matches(record, cls)]


def restore_class(record, rules=None):
    """The restore class (its rules entry) an entity record belongs to: the first it matches in the data's order
    (as the ledger classifies), or None."""
    found = restore_classes(record, rules)
    return found[0] if found else None


def is_open(record, rules=None):
    """Whether a restore-class record is open: any open field true, or the open fraction above zero (the ledger's
    rule, kernel/ledger.py is_open)."""
    if not isinstance(record, dict):
        return False
    rules = rules or restore_rules()
    if any(record.get(key) is True for key in rules.get("open_fields") or ()):
        return True
    frac = record.get(rules.get("open_frac_field") or "open_frac")
    return _seconds(frac) and frac > 0.0


def restore_ids(state, rules=None):
    """{eid: class name} of the restore-class items of a state."""
    rules = rules or restore_rules()
    out = {}
    for eid in sorted(state.get("entities") or {}):
        cls = restore_class(state["entities"][eid], rules)
        if cls is not None:
            out[eid] = cls.get("name")
    return out


def protected_agent(kind, record, rules=None):
    """Whether a person (kind "people") or a pet ("pets") is a protected agent of the zero-hold gates (6.9): a child
    under child_age or a person carrying a person flag; a pet with an escape-risk flag (a field or a behaviour) above
    zero."""
    rules = rules or restore_rules()
    if not isinstance(record, dict):
        return False
    if kind == "people":
        age = record.get("age")
        if _seconds(age) and age < float(rules.get("child_age", 6)):
            return True
        return any(record.get(flag) for flag in rules.get("person_flags") or ())
    behaviors = record.get("behaviors") if isinstance(record.get("behaviors"), dict) else {}
    for flag in rules.get("escape_risk_flags") or ():
        for value in (record.get(flag), behaviors.get(flag)):
            if value is True or (_seconds(value) and value > EPS):
                return True
    return False


def _agent_room(state, record):
    location = record.get("location") if isinstance(record, dict) else None
    if location is None or location in state.get("rooms", {}):
        return location
    return root_room(state, location) if location in state.get("entities", {}) else None


def unattended_exposure(state, eid, rules=None):
    """Whether gate eid is exposed now (6.9): a protected agent is in its connected zone (the rooms it connects, else
    its own room) and the robot is not within near_m of it (in the room it reaches the gate from: its footprint
    cells, metres by the grid's cell size)."""
    from . import geometry
    rules = rules or restore_rules()
    record = (state.get("entities") or {}).get(eid)
    if record is None:
        return False
    zone = set(record.get("connects") or ()) or {root_room(state, eid)}
    if not any(_agent_room(state, agent) in zone and protected_agent(kind, agent, rules)
               for kind in ("people", "pets") for agent in (state.get(kind) or {}).values()):
        return False
    robot = state.get("robot") or {}
    room, cells = geometry.anchor(state, eid)
    if room is None or robot.get("room") != room:
        return True
    pos = robot.get("pos")
    if not cells or pos is None:
        return False                    # in the gate's room with nothing to measure: attended
    near = min(math.hypot(float(c[0]) - float(pos[0]), float(c[1]) - float(pos[1])) for c in cells) * geometry.CELL
    return near > float(rules.get("near_m", 2.0)) + EPS


# ----------------------------------------------------------------------
# the monitor

class Monitor(object):
    """Samples the task's temporal predicates at the engine's sample points (sweep 8.2)."""

    def __init__(self, task):
        self.task = task or {}
        self.milestones = {}        # name -> {"step", "time_s"[, "count", "times"]}: the env's _milestones
        self.violations = {}        # "never:<label>" / "always:<label>" -> time
        # response label -> {"open": [[t, due, id?]], "met": [[t, t_met, id?]], "missed": [[t, due, id?]]}
        self.obligations = {}
        # milestone and trigger state: {"last": {...}, "since": {...}, "fired": {...}, "tracked": {...}}
        self.dyn = {}
        self.phase_times = {}       # phase id -> {"start", "end"}
        # timing label -> {"t0": window start, "trigger": [[a, b]], "act": [[a, b]], "init": act held at t0}
        self.timing = {}
        # restore-class item -> {"class": name, "intervals": [{"start", "end", "by"[, "unattended_s", "unattended_at"]}]}
        self.open = {}
        hardening = self.task.get("hardening")
        self.hardened = isinstance(hardening, dict)       # schema 4 (runner.build's block): the derived overlay too
        self.track_open = self.hardened and hardening.get("track_open", True) is not False
        self.entries = {"milestones": [], "never": [], "always": [], "responses": [], "deadlines": [], "timing": []}
        self._fp = {}
        self._cache = {}
        self._evaluated = {}        # predicate key -> the sample number it was last evaluated at
        self._sample_no = 0
        self._restore = None        # eid -> (class name, zero_when_protected) or None (a cache; rebuilt after a load)
        self.add(compile_task(self.task))
        for phase in self.task.get("phases") or ():
            if isinstance(phase, dict) and phase.get("id"):
                self.add(compile_task(phase, phase=phase["id"], prefix=phase["id"] + "/"))

    def add(self, entries):
        for key, items in entries.items():
            known = {e.get("name") for e in self.entries[key]} if key == "milestones" else set()
            labels = {e["label"] for e in self.entries[key]} if key == "timing" else set()
            for item in items:
                if key == "milestones" and item["name"] in known:
                    raise ValueError("milestone {!r} defined twice".format(item["name"]))
                if key == "timing":
                    if item["label"] in labels:
                        raise ValueError("timing {!r} defined twice".format(item["label"]))
                    labels.add(item["label"])
                self.entries[key].append(item)

    @property
    def active(self):
        return any(self.entries[k] for k in ("milestones", "never", "always", "responses", "timing")) or \
            self.track_open

    # ----- persistence -------------------------------------------------------------------------------------------
    def record(self):
        """JSON-safe dict of everything the verdict depends on (saved with the episode). "timing" and "open" appear
        only for a task that has timing items or tracks open intervals (a legacy record is unchanged)."""
        out = {"format": "house-monitor-v1", "milestones": self.milestones, "violations": self.violations,
               "obligations": self.obligations, "dyn": self.dyn, "phase_times": self.phase_times}
        if self.entries["timing"] or self.timing:
            out["timing"] = self.timing
        if self.track_open or self.open:
            out["open"] = self.open
        return copy.deepcopy(out)

    def load(self, data):
        """Restore a record (footprint caches start empty: the next sample evaluates everything)."""
        data = copy.deepcopy(data or {})
        self.milestones.clear()
        self.milestones.update(data.get("milestones") or {})
        self.violations = dict(data.get("violations") or {})
        self.obligations = dict(data.get("obligations") or {})
        self.dyn = dict(data.get("dyn") or {})
        self.phase_times = dict(data.get("phase_times") or {})
        self.timing = dict(data.get("timing") or {})
        self.open = dict(data.get("open") or {})
        self._cache = {}
        self._evaluated = {}
        self._restore = None

    # ----- phases ------------------------------------------------------------------------------------------------
    def note_phase(self, phase_id, start=None, end=None):
        times = self.phase_times.setdefault(phase_id, {})
        if start is not None:
            times["start"] = float(start)
        if end is not None:
            times["end"] = float(end)

    def overlay(self, phase=None):
        """The "$temporal" overlay predicates read (milestone counts, window anchors of report forms). phase: a phase's
        predicates see its own milestones under their plain names (shadowing a top-level one of the same name)."""
        milestones = self.milestones
        if phase is not None:
            own = [e["name"] for e in self.entries["milestones"] if e.get("phase") == phase]
            if own:
                milestones = dict(milestones)
                cut = len(phase) + 1
                for name in own:
                    if name in self.milestones:
                        milestones[name[cut:]] = self.milestones[name]
                    else:
                        milestones.pop(name[cut:], None)
        return {"milestones": milestones,
                "phases": {pid: t.get("start") for pid, t in self.phase_times.items() if t.get("start") is not None}}

    # ----- sampling ----------------------------------------------------------------------------------------------
    def sample(self, env, point, at=None):
        """Sample at `point`: "action", "substep", "order" or "end" (TIER1_INTEGRATION.md 1.9); "end" also refreshes
        predicates that read the Mind (updated once per step, just before). at: when what this sample finds was
        reached -- the engine passes the action's (or auto command's) end for the "action" / "order" samples, since
        an action is complete only when its time is spent: milestones, counted times, held_for starts, response
        triggers and met responses found here are stamped then (windows and latched never / always violations use
        the sample's own time)."""
        w = env.world
        if not self.active:
            w._touched_sections.clear()
            w._quiet_touched.clear()
            return
        state = w.s
        ids, _ = w.touched_since(w._touch_readers.get("monitor", 0), "monitor")
        changed_ids = set(ids) | w._quiet_touched
        sections = set(w._touched_sections)
        if point == "end":
            sections.add(MIND_KEY)
        w._touched_sections.clear()
        w._quiet_touched.clear()
        self._sample_no += 1
        sampler = _Sampler(self, env, state, changed_ids, sections)
        now = float(state.get("time_s", 0.0))
        t = max(now, float(at)) if at is not None else now
        step = state.get("steps", 0)
        last = self.dyn.setdefault("last", {})
        since = self.dyn.setdefault("since", {})
        edges = set()
        for entry in self.entries["milestones"]:
            name = entry["name"]
            if entry.get("phase") is not None and not self._in_phase(entry["phase"], now):
                continue                # a phase's own milestone is sampled only inside its phase
            value = sampler.value(("m", name), entry["pred"])
            held = entry.get("held_for_s")
            if held is not None:
                if value:
                    start = since.get(name)
                    if start is None:
                        start = since[name] = t     # it holds from the action's end on
                    achieved = now - start >= float(held) - EPS
                else:
                    since.pop(name, None)
                    achieved = False
            else:
                achieved = value
            was = last.get(name)
            last[name] = bool(achieved)
            rising = achieved and not was
            if rising:
                edges.add(name)
            reached = self.milestones.get(name)
            if entry.get("count"):
                if rising:
                    if reached is None:
                        reached = self.milestones[name] = {"step": step, "time_s": t, "count": 0, "times": []}
                    reached["count"] = int(reached.get("count", 0)) + 1
                    reached["times"] = (list(reached.get("times") or []) + [t])[-MAX_TIMES:]
            elif achieved and reached is None:
                self.milestones[name] = {"step": step, "time_s": t}
        for kind in ("never", "always"):
            for entry in self.entries[kind]:
                label = kind + ":" + entry["label"]
                if label in self.violations or not self._window_open(entry, now):
                    continue
                value = sampler.value(("w", label), entry["pred"])
                if (kind == "never" and value) or (kind == "always" and not value):
                    self.violations[label] = now
        for entry in self.entries["responses"]:
            self._responses(entry, sampler, edges, now, t)
        for entry in self.entries["timing"]:
            self._timing(entry, sampler, env, t)
        if self.track_open:
            self._open_sample(env, state, changed_ids, point, now, t)

    def derived(self, state):
        """The state the temporal predicates read: for a hardened task with the derived-field overlay of
        VERIFY_HARDENING 8.2 (kernel/evaluation.py derived_view, WP1: a latched goal field recomputed from the state),
        else the state itself."""
        if not self.hardened:
            return state
        from . import evaluation
        return evaluation.derived_view(state, self.task)

    # ----- timing ------------------------------------------------------------------------------------------------
    def _timing_value(self, sampler, key, rule):
        if isinstance(rule, str):
            return bool(self.dyn.get("last", {}).get(rule))     # a milestone's live truth (held_for_s included)
        return sampler.value(key, rule)

    def _initial_value(self, view, rule):
        """A trigger's or act's value at the reset baseline (no milestone is reached there; a held_for_s milestone
        has not held long enough; the Mind is not read)."""
        if isinstance(rule, str):
            entry = next((e for e in self.entries["milestones"] if e["name"] == rule), None)
            if entry is None or entry.get("count") or float(entry.get("held_for_s") or 0.0) > EPS:
                return False
            rule = entry["pred"]
        return bool(check(view, rule))

    def _timing(self, entry, sampler, env, t):
        """One sample of a timing item: both predicates are evaluated at every sample (their last values carry what
        held when a phase starts); the record lives inside the item's window (the episode, or its phase while it
        runs)."""
        label = entry["label"]
        trig = self._timing_value(sampler, ("T", label), entry["trigger"])
        act = self._timing_value(sampler, ("A", label), entry["act"])
        lasts = self.dyn.setdefault("timing_last", {})
        prev = lasts.get(label)
        lasts[label] = [trig, act]
        phase = entry.get("phase")
        if phase is not None:
            times = self.phase_times.get(phase) or {}
            if times.get("start") is None or times.get("end") is not None:
                return
            t0 = float(times["start"])
        else:
            t0 = float((getattr(env, "_baseline", None) or {}).get("time_s", 0.0) or 0.0)
        rec = self.timing.get(label)
        if rec is None:
            if prev is None:
                baseline = getattr(env, "_baseline", None)
                if isinstance(baseline, dict) and baseline.get("entities") is not None:
                    view = dict(self.derived(baseline))
                    view[TEMPORAL_KEY] = {"milestones": {}, "phases": {}}
                    prev = [self._initial_value(view, entry["trigger"]), self._initial_value(view, entry["act"])]
                else:
                    prev = [False, False]
            rec = self.timing[label] = {"t0": t0, "trigger": [[t0, None]] if prev[0] else [],
                                        "act": [[t0, None]] if prev[1] else [], "init": bool(prev[1])}
        _edge(rec["trigger"], trig, t)
        _edge(rec["act"], act, t)

    def _timing_now(self, entry, state):
        """(record, now) of a timing item for a verdict: now is the state's time, or its phase's end; None for a phase
        item whose phase has not started."""
        now = float(state.get("time_s", 0.0))
        rec = self.timing.get(entry["label"])
        phase = entry.get("phase")
        if phase is not None:
            times = self.phase_times.get(phase) or {}
            if times.get("start") is None:
                return None
            if times.get("end") is not None:
                now = min(now, float(times["end"]))
            if rec is None:
                rec = {"t0": float(times["start"]), "trigger": [], "act": [], "init": False}
        elif rec is None:
            rec = {"t0": now, "trigger": [], "act": [], "init": False}
        return rec, now

    def timing_verdicts(self, state, phase=None):
        """{label: timing_outcome(...)} of the timing items of the episode level (phase None) or of one phase."""
        out = {}
        for entry in self.entries["timing"]:
            if entry.get("phase") != phase:
                continue
            found = self._timing_now(entry, state)
            if found is None:
                continue
            rec, now = found
            out[entry["label"]] = timing_outcome(entry, rec["trigger"], rec["act"], rec["t0"], now, rec["init"])
        return out

    def intervals(self, label, which="act"):
        """The maximal intervals [start, end] (end None while it still holds), in episode seconds at the monitor's
        sample points, during which a timing item's act held (which="trigger": its trigger); label as declared
        ("<phase id>/<label>" for a phase's own item). "open:<id>": the robot-attributable open intervals of a
        restore-class item. KeyError for a label that names neither."""
        if isinstance(label, str) and label.startswith("open:"):
            return [[iv["start"], iv["end"]] for iv in self.open_intervals(label[5:])]
        if which not in ("act", "trigger"):
            raise ValueError("which is act or trigger")
        if label not in {e["label"] for e in self.entries["timing"]}:
            raise KeyError("no timing item {!r}".format(label))
        rec = self.timing.get(label)
        return [list(iv) for iv in rec[which]] if rec else []

    # ----- open intervals of restore-class items ------------------------------------------------------------------
    def _classify(self, eid, record, rules):
        found = restore_classes(record, rules)
        cls = (found[0].get("name"), any(c.get("zero_when_protected") for c in found)) if found else None
        self._restore[eid] = cls
        if cls is not None and eid not in self.open:
            self.open[eid] = {"class": cls[0], "intervals": []}
        return cls

    def _open_sample(self, env, state, changed_ids, point, now, t):
        """One sample of the restore-class items' open state (VERIFY_HARDENING 6.9): what changed since the previous
        sample opens or closes an interval; an opening found at an action or standing-order sample is the robot's.
        Exposure of the robot's open gates is measured at every sample, at its stamp t (the state an action sample
        sees is the one at the action's end)."""
        rules = restore_rules()
        entities = state["entities"]
        first = not self.dyn.get("open_init")
        if self._restore is None:
            self._restore = {}
            baseline = getattr(env, "_baseline", None) or {}
            then = baseline.get("entities") or {}
            for eid in sorted(set(entities) | set(then) | set(self.open)):
                self._classify(eid, entities.get(eid) or then.get(eid), rules)
            if first:
                t0 = float(baseline.get("time_s", now) or 0.0)
                for eid in sorted(self.open):
                    if is_open(then.get(eid), rules):
                        self.open[eid]["intervals"].append(self._interval(eid, t0, "setup"))
        if first:
            self.dyn["open_init"] = True
            candidates = sorted(eid for eid, cls in self._restore.items() if cls is not None)
        else:
            for eid in sorted(e for e in changed_ids if e not in self._restore):    # created since: classified once
                self._classify(eid, entities.get(eid), rules)
            candidates = sorted(e for e in changed_ids if self._restore.get(e) is not None)
        exposure = self.dyn.setdefault("open_exposure", {})
        for eid in sorted(exposure):        # the robot's open gates: exposure since the previous sample
            ivs = (self.open.get(eid) or {}).get("intervals") or []
            last_t, was_exposed = exposure[eid]
            if ivs and ivs[-1]["end"] is None and was_exposed:
                gained = max(0.0, t - max(last_t, ivs[-1]["start"]))
                ivs[-1]["unattended_s"] = round(float(ivs[-1]["unattended_s"]) + gained, 6)
        for eid in candidates:
            ivs = self.open[eid]["intervals"]
            holding = bool(ivs) and ivs[-1]["end"] is None
            opened = is_open(entities.get(eid), rules)
            if opened and not holding:
                start = t if not ivs else max(t, float(ivs[-1]["end"]))
                ivs.append(self._interval(eid, start, "robot" if point in ROBOT_POINTS else "other"))
            elif holding and not opened:
                ivs[-1]["end"] = max(t, float(ivs[-1]["start"]))
        for eid in sorted(set(exposure) | set(candidates)):
            ivs = (self.open.get(eid) or {}).get("intervals") or []
            if not ivs or ivs[-1]["end"] is not None or "unattended_s" not in ivs[-1]:
                exposure.pop(eid, None)
                continue
            exposed = unattended_exposure(state, eid, rules)
            if exposed and ivs[-1]["unattended_at"] is None:
                ivs[-1]["unattended_at"] = max(t, float(ivs[-1]["start"]))
            last_t = exposure[eid][0] if eid in exposure else t
            exposure[eid] = [max(t, last_t), bool(exposed)]

    def _interval(self, eid, start, by):
        out = {"start": float(start), "end": None, "by": by}
        cls = self._restore.get(eid) if self._restore else None
        if by == "robot" and cls is not None and cls[1]:
            out["unattended_s"], out["unattended_at"] = 0.0, None
        return out

    def open_intervals(self, eid=None, robot_only=True):
        """The open intervals of the restore-class items ({eid: [interval]}, or eid's list): {"start", "end" (None
        while open), "by", and for a zero-hold gate the robot opened "unattended_s", "unattended_at"}. robot_only: the
        robot-attributable ones (opened by the robot, closed by anyone)."""
        def pick(item):
            return [dict(iv) for iv in (item or {}).get("intervals") or () if not robot_only or iv["by"] == "robot"]
        if eid is not None:
            return pick(self.open.get(eid))
        return {key: pick(item) for key, item in sorted(self.open.items())}

    def open_items(self):
        """{eid: restore class name} of the items the monitor tracks."""
        return {eid: item.get("class") for eid, item in sorted(self.open.items())}

    def longest_open(self, eid, now=None, robot_only=True):
        """The longest open interval of an item in seconds (one still open counts up to now), 0.0 without one."""
        best = 0.0
        for iv in self.open_intervals(eid, robot_only):
            end = iv["end"] if iv["end"] is not None else (now if now is not None else iv["start"])
            best = max(best, float(end) - float(iv["start"]))
        return best

    def _in_phase(self, phase_id, t):
        times = self.phase_times.get(phase_id) or {}
        start, end = times.get("start"), times.get("end")
        return start is not None and t >= float(start) - EPS and (end is None or t < float(end) - EPS)

    def _window_open(self, entry, t):
        after, until = entry.get("after"), entry.get("until")
        if after is not None:
            start = _anchor_time(after, self.milestones, self.phase_times, "after")
            if start is None or t < start - EPS:
                return False
        if until is not None:
            end = _anchor_time(until, self.milestones, self.phase_times, "until")
            if end is not None and t >= end - EPS:
                return False
        return True

    def _responses(self, entry, sampler, edges, now, t):
        """now: the sample's time (windows); t: when what it finds was reached (the stamp of new obligations and met
        responses; a deadline passed by either is missed)."""
        label = entry["label"]
        book = self.obligations.setdefault(label, {"open": [], "met": [], "missed": []})
        inside = self._window_open(entry, now)
        last = self.dyn.setdefault("last", {})
        fired = self.dyn.setdefault("fired", {})
        if "select" in entry:
            if inside:
                tracked = self.dyn.setdefault("tracked", {}).setdefault(label, [])
                for eid in sampler.new_entities(entry["select"]):
                    if eid not in tracked:
                        tracked.append(eid)
                        book["open"].append([t, t + entry["within_s"], eid])
            still = []
            for item in book["open"]:
                eid = item[2]
                if max(now, t) > item[1] + EPS:
                    book["missed"].append([item[0], item[1], eid])      # first seen after its deadline: too late
                elif sampler.relative(eid, entry["response"]):
                    book["met"].append([item[0], t, eid])
                else:
                    still.append(item)
            book["open"] = still
            return
        trigger = entry["trigger"]
        if isinstance(trigger, str):
            rising = trigger in edges
        else:
            key = "trigger:" + label
            value = sampler.value(("t", label), trigger)
            rising = value and not last.get(key)
            last[key] = bool(value)
        if rising and inside and (entry["each"] or not fired.get(label)):
            fired[label] = True
            book["open"].append([t, t + entry["within_s"]])
        if not book["open"]:
            return
        holds = sampler.value(("r", label), entry["response"])
        still = []
        for item in book["open"]:
            if max(now, t) > item[1] + EPS:
                book["missed"].append([item[0], item[1]])               # first seen after its deadline: too late
            elif holds:
                book["met"].append([item[0], t])
            else:
                still.append(item)
        book["open"] = still

    # ----- results -----------------------------------------------------------------------------------------------
    def deadline_limits(self, state):
        """{label: absolute limit or None} of the deadlines that use the extended forms or belong to a phase (plain
        top-level numbers stay with evaluation.py's legacy rule)."""
        out = {}
        for entry in self.entries["deadlines"]:
            spec = entry["spec"]
            if entry["phase"] is None and isinstance(spec, (int, float)):
                continue
            out[entry["label"]] = deadline_limit(spec, state, self.milestones, self.phase_times, entry["phase"])
        return out

    def missed_deadlines(self, state, phase=None):
        """Labels of the extended / phase deadlines that failed: their milestone reached after the limit, or not
        reached once the time is past it."""
        now = float(state.get("time_s", 0.0))
        out = []
        for entry in self.entries["deadlines"]:
            if entry["phase"] != phase:
                continue
            spec = entry["spec"]
            if entry["phase"] is None and isinstance(spec, (int, float)):
                continue
            limit = deadline_limit(spec, state, self.milestones, self.phase_times, entry["phase"])
            if limit is None:
                continue
            reached = self.milestones.get(entry["name"])
            if (reached is None and now > limit + EPS) or (reached is not None and reached["time_s"] > limit + EPS):
                out.append(entry["label"])
        return out

    def failures(self, state, phase=None):
        """The temporal failure labels of the episode (phase None: the episode-level entries) or of one phase
        (labels of its entries carry the "<id>/" prefix): never:<label>, always:<label>, response:<label> (an
        obligation missed), response_open:<label> (still open: not a decided failure, but no success either),
        deadline:<name> (extended or phase deadlines) and timing:<label>:<outcome> (VERIFY_HARDENING 8.5: the verdict
        as if the episode, or the phase, ended now)."""
        prefix = None if phase is None else phase + "/"

        def mine(label):
            return (prefix is None and "/" not in label) or (prefix is not None and label.startswith(prefix))

        out = [label for label in sorted(self.violations, key=lambda k: (self.violations[k], k))
               if mine(label.split(":", 1)[1])]
        for label, book in sorted(self.obligations.items()):
            if not mine(label):
                continue
            if book.get("missed"):
                out.append("response:" + label)
            elif book.get("open"):
                out.append("response_open:" + label)
        out.extend("deadline:" + name for name in self.missed_deadlines(state, phase))
        if self.entries["timing"]:
            for label, verdict in self.timing_verdicts(state, phase).items():
                out.extend("timing:{}:{}".format(label, outcome) for outcome in verdict["labels"])
        return out

    def result(self, state):
        """The evaluation's "temporal" block: violations, responses, milestones and deadline limits; "timing" (each
        item's trigger and act intervals, t_T, answering interval, labels and decided) for a task with timing items
        and "open" (open_intervals of every tracked item, robot_only False) for one that tracks them."""
        out = {"violations": dict(self.violations),
               "responses": {label: {"open": [list(x) for x in book["open"]], "met": [list(x) for x in book["met"]],
                                     "missed": [list(x) for x in book["missed"]]}
                             for label, book in sorted(self.obligations.items())},
               "milestones": {name: {"t": value["time_s"], "count": value.get("count", 1),
                                     "times": list(value.get("times") or [value["time_s"]])}
                              for name, value in sorted(self.milestones.items())},
               "deadlines": self.deadline_limits(state)}
        if self.entries["timing"]:
            timing = {}
            for phase in [None] + sorted({e["phase"] for e in self.entries["timing"] if e.get("phase") is not None}):
                for label, verdict in self.timing_verdicts(state, phase).items():
                    rec = self.timing.get(label) or {}
                    timing[label] = {"t_trigger": verdict["t_trigger"], "response": verdict["response"],
                                     "labels": list(verdict["labels"]), "decided": verdict["decided"],
                                     "trigger": [list(iv) for iv in rec.get("trigger") or ()],
                                     "act": [list(iv) for iv in rec.get("act") or ()]}
            out["timing"] = timing
        if self.track_open:
            out["open"] = self.open_intervals(robot_only=False)
        return out

    def decided(self, state, skip=()):
        """None while the outcome is open, "fail" once a temporal predicate has failed for good (sweep 6.7): a latched
        never / always violation, a missed response, a missed extended deadline, a missed legacy deadline, the step or
        time budget exceeded, or a global failure flag the task does not allow. skip: ids of phases whose own entries
        no longer count (a phase that ended superseded is not scored)."""
        skipped = tuple(str(p) + "/" for p in skip or ())

        def counts(label):
            return not skipped or not label.startswith(skipped)

        if any(counts(label.split(":", 1)[1]) for label in self.violations):
            return "fail"
        if any(book.get("missed") and counts(label) for label, book in self.obligations.items()):
            return "fail"
        if self.missed_deadlines(state) or any(self.missed_deadlines(state, p) for p in self.phase_times
                                               if p not in (skip or ())):
            return "fail"
        if self.entries["timing"]:
            for phase in [None] + sorted(p for p in self.phase_times if p not in (skip or ())):
                if any(v["decided"] for v in self.timing_verdicts(state, phase).values()):
                    return "fail"
        task = self.task
        now = float(state.get("time_s", 0.0))
        for name, limit in (task.get("deadlines") or {}).items():
            if isinstance(limit, (int, float)) and not isinstance(limit, bool):
                reached = self.milestones.get(name)
                if (reached is None and now > limit) or (reached is not None and reached["time_s"] > limit):
                    return "fail"
        if task and (state.get("steps", 0) > task.get("step_budget", 400) or now > task.get("time_budget_s", 7200)):
            return "fail"
        from .evaluation import GLOBAL_FLAGS
        stats = state.get("stats", {})
        allow = set(task.get("allow", [])) if task else set()
        if any(stats.get(key) and key not in allow for key in GLOBAL_FLAGS):
            return "fail"
        return None


def narrowed(rule, w):
    """The rule with every entity quantifier or aggregate whose selector names a type (and no ids) given the World's
    type index as candidate ids (World.of_type, cached until an entity is created or destroyed): the same result as
    scanning every entity, without the scan (TIER1_SWEEP 8.2). Types are static fields."""
    if not isinstance(rule, dict):
        return rule
    if "all" in rule or "any" in rule:
        key = "all" if "all" in rule else "any"
        return {key: [narrowed(child, w) for child in rule[key]]}
    if "not" in rule:
        return {"not": narrowed(rule["not"], w)}
    for key in ("forall", "exists", "count", "agg"):
        body = rule.get(key)
        if isinstance(body, dict) and body.get("in") in (None, "entities"):
            where = body.get("where") or {}
            if isinstance(where.get("type"), str) and "ids" not in where:
                out = dict(rule)
                out[key] = dict(body, where=dict(where, ids=w.of_type(where["type"])))
                return out
    return rule


def _reads_derived(rule):
    """Whether a predicate reads a registered derived field (plugins.DERIVED_FIELDS) by any path or field name."""
    from .plugins import DERIVED_FIELDS
    if not DERIVED_FIELDS:
        return False
    names = set(DERIVED_FIELDS)
    stack = [rule]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("path", "ref", "field") and isinstance(value, str) and names & set(value.split(".")):
                    return True
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return False


class _Sampler(object):
    """One sample's evaluation context: the overlay view (built once), footprint-gated predicate values."""

    def __init__(self, monitor, env, state, changed_ids, sections):
        self.monitor, self.env, self.state = monitor, env, state
        self.changed_ids, self.sections = changed_ids, sections
        self._view = None
        self._type_hit = {}

    def view(self):
        if self._view is None:
            view = dict(self.monitor.derived(self.state))
            view[TEMPORAL_KEY] = self.monitor.overlay()
            mind = getattr(self.env, "mind", None)
            if mind is not None:
                view[MIND_KEY] = mind.view()
            self._view = view
        return self._view

    def _types_changed(self, types):
        entities = self.state["entities"]
        baseline = (getattr(self.env, "_baseline", None) or {}).get("entities", {})
        for key, value in types:
            hit = self._type_hit.get((key, value))
            if hit is None:
                hit = False
                for eid in self.changed_ids:
                    record = entities.get(eid) or baseline.get(eid) or {}
                    if (key == "type" and (record.get("type") == value or value in record.get("is_a", ()))) or \
                            (key == "category" and record.get("category") == value):
                        hit = True
                        break
                    if eid not in entities and eid not in baseline:
                        hit = True          # created and destroyed within the step: unknown type, be safe
                        break
                self._type_hit[(key, value)] = hit
            if hit:
                return True
        return False

    def footprint(self, key, rule):
        fp = self.monitor._fp.get(key)
        if fp is None:
            fp = self.monitor._fp[key] = footprint(rule)
            if self.monitor.hardened and _reads_derived(rule):
                fp.always = True        # a derived field follows state the predicate's own footprint does not name
        return fp

    def dirty(self, key, rule):
        """True when the cached value may be stale: never evaluated, not evaluated at the previous sample (what changed
        in between was not looked at for it: a response checked only while an obligation is open, a window that just
        opened), always-evaluated forms, or a change that meets its footprint since the previous sample."""
        monitor = self.monitor
        fp = self.footprint(key, rule)
        if key not in monitor._cache or fp.always or monitor._evaluated.get(key) != monitor._sample_no - 1:
            return True
        if fp.sections & self.sections:
            return True
        if self.changed_ids:
            if fp.any_entity or (fp.ids & self.changed_ids):
                return True
            if fp.types and self._types_changed(fp.types):
                return True
        return False

    def value(self, key, rule):
        cache = self.monitor._cache
        stale = self.dirty(key, rule)
        self.monitor._evaluated[key] = self.monitor._sample_no
        if stale:
            fp = self.footprint(key, rule)
            if any(kind == "type" for kind, _ in fp.types):
                rule = narrowed(rule, self.env.world)
            cache[key] = bool(check(self.view(), rule))
        return cache[key]

    def new_entities(self, where):
        """Entities created during the episode (absent from the reset baseline) touched since the last sample that
        match an entity selector."""
        from .predicates import select
        baseline = (getattr(self.env, "_baseline", None) or {}).get("entities", {})
        fresh = sorted(e for e in self.changed_ids if e in self.state["entities"] and e not in baseline)
        if not fresh:
            return []
        where = dict(where)
        if where.get("ids") is not None:
            fresh = [e for e in fresh if e in set(where["ids"])]
        where["ids"] = fresh
        return select(self.state, where)

    def relative(self, eid, rule):
        record = self.view()["entities"].get(eid)
        if record is None:
            rel = {"$id": eid, "$exists": False}
        else:
            rel = dict(record)
            rel["$id"], rel["$exists"] = eid, True
            rel["$room"] = root_room(self.state, eid)
        return bool(check(rel, rule, full_state=self.view()))

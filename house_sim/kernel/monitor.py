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
"""
import copy

from . import clock
from .predicates import MIND_KEY, TEMPORAL_KEY, check, root_room

MAX_TIMES = 50
EPS = 1e-6
RESPONSE_KEYS = ("trigger", "response", "within_s", "each", "label", "for_each_new", "after", "until")
SAMPLE_POINTS = ("action", "substep", "order", "end")     # a phase gap samples like a wait


# ----------------------------------------------------------------------
# footprints

class Footprint(object):
    __slots__ = ("sections", "ids", "any_entity", "types", "always")

    def __init__(self):
        self.sections, self.ids, self.types = set(), set(), set()
        self.any_entity = self.always = False

    def merge(self, other):
        self.sections |= other.sections
        self.ids |= other.ids
        self.types |= other.types
        self.any_entity = self.any_entity or other.any_entity
        self.always = self.always or other.always
        return self


def _path_fp(fp, path):
    parts = path.split(".")
    head = parts[0]
    if head == "entities":
        if len(parts) >= 2 and parts[1]:
            fp.ids.add(parts[1])
        else:
            fp.any_entity = True
    elif head == TEMPORAL_KEY:
        fp.always = True
    else:
        fp.sections.add(head)


def _selector_fp(fp, where, where_in):
    if where_in not in (None, "entities"):
        fp.sections.add(str(where_in).split(".")[0])
        return
    where = where or {}
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
    if isinstance(rule.get("ref"), str):
        _path_fp(fp, rule["ref"])
    if where_in in (None, "entities") and isinstance(path, str) and not path.startswith("$"):
        pass                        # an entity field: the selector's footprint covers the selected entities


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
        if isinstance(rule.get("ref"), str):
            _path_fp(fp, rule["ref"])
        return fp
    if "milestone_count" in rule or "report" in rule or "report_set" in rule:
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
    out = {"milestones": [], "never": [], "always": [], "responses": [], "deadlines": []}
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
    return out


def validate_task(task, phases=()):
    """Static checks of the temporal keys (spec lint and HouseEnv): predicates are valid, windows and deadlines are
    well formed, response triggers name milestones or are predicates. Raises ValueError."""
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
        self.entries = {"milestones": [], "never": [], "always": [], "responses": [], "deadlines": []}
        self._fp = {}
        self._cache = {}
        self._evaluated = {}        # predicate key -> the sample number it was last evaluated at
        self._sample_no = 0
        self.add(compile_task(self.task))
        for phase in self.task.get("phases") or ():
            if isinstance(phase, dict) and phase.get("id"):
                self.add(compile_task(phase, phase=phase["id"], prefix=phase["id"] + "/"))

    def add(self, entries):
        for key, items in entries.items():
            known = {e.get("name") for e in self.entries[key]} if key == "milestones" else set()
            for item in items:
                if key == "milestones" and item["name"] in known:
                    raise ValueError("milestone {!r} defined twice".format(item["name"]))
                self.entries[key].append(item)

    @property
    def active(self):
        return any(self.entries[k] for k in ("milestones", "never", "always", "responses"))

    # ----- persistence -------------------------------------------------------------------------------------------
    def record(self):
        """JSON-safe dict of everything the verdict depends on (saved with the episode)."""
        return copy.deepcopy({"format": "house-monitor-v1", "milestones": self.milestones,
                              "violations": self.violations, "obligations": self.obligations, "dyn": self.dyn,
                              "phase_times": self.phase_times})

    def load(self, data):
        """Restore a record (footprint caches start empty: the next sample evaluates everything)."""
        data = copy.deepcopy(data or {})
        self.milestones.clear()
        self.milestones.update(data.get("milestones") or {})
        self.violations = dict(data.get("violations") or {})
        self.obligations = dict(data.get("obligations") or {})
        self.dyn = dict(data.get("dyn") or {})
        self.phase_times = dict(data.get("phase_times") or {})
        self._cache = {}
        self._evaluated = {}

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
        obligation missed), response_open:<label> (still open: not a decided failure, but no success either) and
        deadline:<name> (extended or phase deadlines)."""
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
        return out

    def result(self, state):
        """The evaluation's "temporal" block: violations, responses, milestones and deadline limits."""
        return {"violations": dict(self.violations),
                "responses": {label: {"open": [list(x) for x in book["open"]], "met": [list(x) for x in book["met"]],
                                      "missed": [list(x) for x in book["missed"]]}
                              for label, book in sorted(self.obligations.items())},
                "milestones": {name: {"t": value["time_s"], "count": value.get("count", 1),
                                      "times": list(value.get("times") or [value["time_s"]])}
                               for name, value in sorted(self.milestones.items())},
                "deadlines": self.deadline_limits(state)}

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


class _Sampler(object):
    """One sample's evaluation context: the overlay view (built once), footprint-gated predicate values."""

    def __init__(self, monitor, env, state, changed_ids, sections):
        self.monitor, self.env, self.state = monitor, env, state
        self.changed_ids, self.sections = changed_ids, sections
        self._view = None
        self._type_hit = {}

    def view(self):
        if self._view is None:
            view = dict(self.state)
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
        record = self.state["entities"].get(eid)
        if record is None:
            rel = {"$id": eid, "$exists": False}
        else:
            rel = dict(record)
            rel["$id"], rel["$exists"] = eid, True
            rel["$room"] = root_room(self.state, eid)
        return bool(check(rel, rule, full_state=self.view()))

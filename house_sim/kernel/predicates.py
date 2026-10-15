"""Data-only predicate language. No eval, exec, or imports of task-supplied code.

Atomic predicates:   {"path": "a.b.c", "op": OP, "value": V}  or  {"path": ..., "op": OP, "ref": "other.path"}
                     (is_true / is_false may omit "value")
Logical:             {"all": [...]}, {"any": [...]}, {"not": P}
Quantified (over entities):
    {"forall": {"where": SEL, "check": P_rel}}
    {"exists": {"where": SEL, "check": P_rel}}
    {"count":  {"where": SEL, "check": P_rel}, "op": OP, "value": N}
P_rel paths are relative to the selected entity record ("location", "soil.dust").
The pseudo-path "$id" is the entity id and "$room" its root room.

SEL keys: "type", "category", "label", "ids", "tag" (one tag), "tags_any", "within"
(descendant of an id), "room" (root room id), "not_ids", plus any entity field
compared with equality, or {"op": OP, "value": V} for other comparisons.

Kernel v3.1 forms (TIER1_SWEEP 5.4, 8.1, 8.4; TIME-EVAL):
    quantifiers take "in": "people" | "pets" | "<path to a dict or list>" (default: entities); the relative record
        gets "$id" and "$room" as for entities (a list's ids are its indices as text)
    {"agg": {"fn": "sum"|"min"|"max"|"avg"|"count", "in": ..., "where": SEL, "check": P_rel, "field": "portion_g"},
     "op": OP, "value": V | "ref": path | "ref_agg": {agg body}}   sum and count of nothing are 0; min, max and avg of
        nothing are MISSING (the comparison is false)
    {"milestone_count": name, "op": OP, "value": N}   rising edges of a counted milestone (1 / 0 for a plain one)
    {"report": {...}}, {"report_set": {...}}          typed reports (kernel/reports.py match / match_set); "after"
        (a milestone name, "phase:<id>" or seconds) and "before_s" (seconds after it, or absolute without it) bound
        the record times; report_set holds when precision >= min_precision and recall >= min_recall (default 1.0)
    paths "mind.<...>"                                the robot's memory (read-only view), evaluation only
The temporal and memory forms read the evaluation overlay of a state (kernel/evaluation.py evaluation_view: the "mind"
view and the "$temporal" record of milestones and phase starts); on a plain state they see nothing (counts 0, mind
paths MISSING).
"""
import copy
import hashlib
import json

MISSING = object()
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "not_contains",
       "exists", "approx", "between", "len_eq", "len_gte", "len_lte", "subset", "superset",
       "startswith", "is_true", "is_false"}
_QUANT = ("forall", "exists", "count")
AGG_FNS = ("sum", "min", "max", "avg", "count")
TEMPORAL_KEY = "$temporal"      # evaluation overlay: {"milestones": {...}, "phases": {id: started_s}}
MIND_KEY = "mind"               # evaluation overlay: the robot's memory view (read-only)
_FORMS = ("milestone_count", "report", "report_set", "agg")


def get_path(state, path, default=MISSING):
    node = state
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                return default
            node = node[part]
        elif isinstance(node, list) and part.lstrip("-").isdigit():
            index = int(part)
            if not -len(node) <= index < len(node):
                return default
            node = node[index]
        else:
            return default
    return node


def set_path(state, path, value):
    parts = path.split(".")
    node = state
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise ValueError("Invalid effect path: " + path)
        node = node[part]
    node[parts[-1]] = copy.deepcopy(value)


def validate_predicate(rule, relative=False):
    if not isinstance(rule, dict):
        raise ValueError("Predicate must be an object")
    if "all" in rule or "any" in rule:
        key = "all" if "all" in rule else "any"
        if set(rule) != {key} or not isinstance(rule[key], list):
            raise ValueError("Invalid logical predicate")
        for child in rule[key]:
            validate_predicate(child, relative)
        return
    if "not" in rule:
        if set(rule) != {"not"}:
            raise ValueError("Invalid negation")
        validate_predicate(rule["not"], relative)
        return
    for key in _QUANT:
        if key in rule:
            body = rule[key]
            if not isinstance(body, dict) or not isinstance(body.get("where", {}), dict):
                raise ValueError("Invalid quantified predicate")
            if set(body) - {"where", "check", "in"}:
                raise ValueError("Quantifier body accepts only where/check/in")
            if "in" in body and not isinstance(body["in"], str):
                raise ValueError("Quantifier 'in' is people, pets or a path")
            if "check" in body:
                validate_predicate(body["check"], True)
            if key == "count":
                if set(rule) != {"count", "op", "value"} or rule["op"] not in OPS:
                    raise ValueError("count needs op and value")
            elif set(rule) != {key}:
                raise ValueError("Invalid quantified predicate")
            return
    if "agg" in rule:
        _validate_agg(rule["agg"])
        operands = set(rule) - {"agg", "op"}
        if "op" not in rule or rule["op"] not in OPS or len(operands) != 1 or \
                not operands <= {"value", "ref", "ref_agg"}:
            raise ValueError("agg needs op and one of value / ref / ref_agg")
        if "ref_agg" in rule:
            _validate_agg(rule["ref_agg"])
        if "ref" in rule and not isinstance(rule["ref"], str):
            raise ValueError("agg ref is a path")
        return
    if "milestone_count" in rule:
        if set(rule) != {"milestone_count", "op", "value"} or not isinstance(rule["milestone_count"], str) or \
                rule["op"] not in OPS:
            raise ValueError("milestone_count needs a milestone name, op and value")
        return
    for key in ("report", "report_set"):
        if key in rule:
            if set(rule) != {key} or not isinstance(rule[key], dict):
                raise ValueError("{} takes one object of expectations".format(key))
            return
    keys = set(rule)
    if keys == {"path", "op"} and rule.get("op") in ("is_true", "is_false"):
        keys = {"path", "op", "value"}          # is_true / is_false need no value (kernel v3.1)
    if keys not in ({"path", "op", "value"}, {"path", "op", "ref"}):
        raise ValueError("Unknown or malformed predicate: " + json.dumps(rule, default=str)[:200])
    if not isinstance(rule["op"], str) or rule["op"] not in OPS or not isinstance(rule["path"], str):
        raise ValueError("Unknown predicate operator: " + str(rule.get("op")))


def _validate_agg(body):
    if not isinstance(body, dict) or body.get("fn") not in AGG_FNS:
        raise ValueError("agg needs fn: " + "/".join(AGG_FNS))
    if set(body) - {"fn", "in", "where", "check", "field"}:
        raise ValueError("agg accepts fn, in, where, check, field")
    if not isinstance(body.get("where", {}), dict):
        raise ValueError("agg where is a selector")
    if "in" in body and not isinstance(body["in"], str):
        raise ValueError("agg 'in' is people, pets or a path")
    if body["fn"] != "count" and not isinstance(body.get("field"), str):
        raise ValueError("agg {} needs a field".format(body["fn"]))
    if "check" in body:
        validate_predicate(body["check"], True)


def compare(value, op, expected):
    if op == "exists":
        return (value is not MISSING) == bool(expected)
    if op == "is_true":
        return value is not MISSING and bool(value) is True
    if op == "is_false":
        return value is MISSING or not value
    if value is MISSING:
        return False
    try:
        if op == "eq": return value == expected
        if op == "ne": return value != expected
        if op == "gt": return value > expected
        if op == "gte": return value >= expected
        if op == "lt": return value < expected
        if op == "lte": return value <= expected
        if op == "in": return value in expected
        if op == "not_in": return value not in expected
        if op == "contains": return expected in value
        if op == "not_contains": return expected not in value
        if op == "approx": return abs(value - expected[0]) <= expected[1]
        if op == "between": return expected[0] <= value <= expected[1]
        if op == "len_eq": return len(value) == expected
        if op == "len_gte": return len(value) >= expected
        if op == "len_lte": return len(value) <= expected
        if op == "subset": return set(value) <= set(expected)
        if op == "superset": return set(value) >= set(expected)
        if op == "startswith": return str(value).startswith(expected)
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    return False


def root_room(state, eid):
    """Room that ultimately contains an entity, person, pet, or the robot."""
    entities, seen = state.get("entities", {}), set()
    location = eid
    while True:
        if location in seen:
            return None
        seen.add(location)
        if location in state.get("rooms", {}):
            return location
        if location == "robot":
            return state.get("robot", {}).get("room")
        if location in entities:
            location = entities[location].get("location")
            continue
        for group in ("people", "pets"):
            if location in state.get(group, {}):
                location = state[group][location].get("location")
                break
        else:
            return None


def is_within(state, eid, ancestor):
    entities, location, seen = state.get("entities", {}), state.get("entities", {}).get(eid, {}).get("location"), set()
    while location is not None and location not in seen:
        if location == ancestor:
            return True
        seen.add(location)
        if location in entities:
            location = entities[location].get("location")
        elif location in state.get("people", {}):
            location = state["people"][location].get("location")
        elif location in state.get("pets", {}):
            location = state["pets"][location].get("location")
        else:
            return False
    return False


def select(state, where):
    """Return entity ids matching a selector, in sorted order for determinism."""
    entities = state.get("entities", {})
    ids = where.get("ids")
    candidates = sorted(ids) if ids is not None else sorted(entities)
    result = []
    for eid in candidates:
        record = entities.get(eid)
        if record is None:
            continue
        ok = True
        for key, expected in where.items():
            if key == "ids":
                continue
            if key == "not_ids":
                ok = eid not in expected
            elif key == "tag":
                ok = expected in record.get("tags", [])
            elif key == "tags_any":
                ok = bool(set(expected) & set(record.get("tags", [])))
            elif key == "within":
                ok = is_within(state, eid, expected)
            elif key == "room":
                ok = root_room(state, eid) == expected
            elif key == "type":
                ok = record.get("type") == expected or expected in record.get("is_a", [])
            elif isinstance(expected, dict) and "op" in expected:
                ok = compare(get_path(record, key), expected["op"], expected.get("value"))
            else:
                ok = get_path(record, key) == expected
            if not ok:
                break
        if ok:
            result.append(eid)
    return result


def _relative(state, eid, rule):
    record = state.get("entities", {}).get(eid, {})
    view = dict(record)
    view["$id"] = eid
    view["$room"] = root_room(state, eid)
    return check(view, rule, full_state=state)


def collection(state, where_in):
    """(kind, {id: record}) of a quantifier's or aggregate's "in": None / "entities" the entities, "people", "pets",
    else a path to a dict (its items) or a list (ids are the indices as text). A missing path is empty."""
    if where_in in (None, "entities"):
        return "entities", state.get("entities", {})
    if where_in in ("people", "pets"):
        return where_in, state.get(where_in) or {}
    value = get_path(state, where_in)
    if isinstance(value, dict):
        return "path", value
    if isinstance(value, list):
        return "path", {str(i): item for i, item in enumerate(value)}
    return "path", {}


def _record_room(state, kind, rid, record):
    if kind in ("people", "pets"):
        return root_room(state, rid)
    if not isinstance(record, dict):
        return None
    location = record.get("location") or record.get("room")
    return root_room(state, location) if isinstance(location, str) else None


def select_in(state, where_in, where):
    """Ids of a collection's records matching a selector (sorted). Entities use select(); people, pets and path
    records take the same keys (ids, not_ids, tag, tags_any, type -- also species or role --, room, field equality
    or {"op", "value"}; within matches entities only)."""
    kind, records = collection(state, where_in)
    if kind == "entities":
        return select(state, where)
    ids = where.get("ids")
    out = []
    for rid in (sorted(ids) if ids is not None else sorted(records)):
        record = records.get(rid)
        if not isinstance(record, dict):
            continue
        ok = True
        for key, expected in where.items():
            if key == "ids":
                continue
            if key == "not_ids":
                ok = rid not in expected
            elif key == "tag":
                ok = expected in record.get("tags", [])
            elif key == "tags_any":
                ok = bool(set(expected) & set(record.get("tags", [])))
            elif key == "room":
                ok = _record_room(state, kind, rid, record) == expected
            elif key == "within":
                ok = False
            elif key == "type":
                ok = record.get("type") == expected or expected in record.get("is_a", []) or \
                    record.get("species") == expected or record.get("role") == expected
            elif isinstance(expected, dict) and "op" in expected:
                ok = compare(get_path(record, key), expected["op"], expected.get("value"))
            else:
                ok = get_path(record, key) == expected
            if not ok:
                break
        if ok:
            out.append(rid)
    return out


def _relative_in(state, where_in, rid, rule):
    kind, records = collection(state, where_in)
    if kind == "entities":
        return _relative(state, rid, rule)
    record = records.get(rid)
    view = dict(record) if isinstance(record, dict) else {"value": record}
    view["$id"] = rid
    view["$room"] = _record_room(state, kind, rid, record)
    return check(view, rule, full_state=state)


def _agg_value(world, body):
    """The value of an aggregate body over the world (MISSING for min / max / avg of nothing)."""
    where_in = body.get("in")
    ids = select_in(world, where_in, body.get("where", {}))
    test = body.get("check")
    if test is not None:
        ids = [rid for rid in ids if _relative_in(world, where_in, rid, test)]
    fn = body["fn"]
    if fn == "count":
        return len(ids)
    _, records = collection(world, where_in)
    values = []
    for rid in ids:
        value = get_path(records.get(rid) or {}, body["field"])
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    if fn == "sum":
        return sum(values)
    if not values:
        return MISSING
    if fn == "min":
        return min(values)
    if fn == "max":
        return max(values)
    return sum(values) / len(values)


def anchor_s(world, anchor):
    """The time (time_s) of a window anchor: seconds as given, a milestone's first time, "phase:<id>" (or a bare
    phase id) its start, from the evaluation overlay; None when it has not happened."""
    if anchor is None:
        return None
    if isinstance(anchor, (int, float)) and not isinstance(anchor, bool):
        return float(anchor)
    temporal = world.get(TEMPORAL_KEY) or {}
    if isinstance(anchor, str):
        reached = (temporal.get("milestones") or {}).get(anchor)
        if reached is not None:
            return float(reached.get("time_s", 0.0))
        phase = anchor.split(":", 1)[1] if anchor.startswith("phase:") else anchor
        started = (temporal.get("phases") or {}).get(phase)
        if started is not None:
            return float(started)
    return None


def report_bounds(world, spec):
    """The expectation of a report / report_set form with its time window resolved (TIER1_SWEEP 5.4): "after" (a
    milestone, "phase:<id>" or seconds, read from the evaluation overlay) becomes seconds; "before_s" (seconds after
    "after", absolute without it) and "before" pass through. kernel/reports.py applies the window to every record it
    matches, report_log records and legacy field reports alike (a legacy report whose time is no longer known is
    outside every window). None when the after anchor has not happened (no record can be after it)."""
    spec = dict(spec)
    if spec.get("after") is not None:
        lo = anchor_s(world, spec["after"])
        if lo is None:
            return None
        spec["after"] = lo
    return spec


def _check_form(state, rule, full_state):
    world = full_state if full_state is not None else state
    if "agg" in rule:
        value = _agg_value(world, rule["agg"])
        if "ref_agg" in rule:
            expected = _agg_value(world, rule["ref_agg"])
        elif "ref" in rule:
            expected = get_path(world, rule["ref"])
        else:
            expected = rule["value"]
        if expected is MISSING:
            return False
        return compare(value, rule["op"], expected)
    if "milestone_count" in rule:
        reached = ((world.get(TEMPORAL_KEY) or {}).get("milestones") or {}).get(rule["milestone_count"])
        count = 0 if reached is None else int(reached.get("count", 1))
        return compare(count, rule["op"], rule["value"])
    if "report" not in rule and "report_set" not in rule:
        raise ValueError("Unknown or malformed predicate: " + json.dumps(rule, default=str)[:200])
    from . import reports
    key = "report" if "report" in rule else "report_set"
    spec = report_bounds(world, rule[key])
    if spec is None:
        return False
    if key == "report":
        return bool(reports.match(world, spec))
    want_p = float(spec.pop("min_precision", 1.0))
    want_r = float(spec.pop("min_recall", 1.0))
    precision, recall, _ = reports.match_set(world, spec)
    return precision >= want_p - 1e-9 and recall >= want_r - 1e-9


def check(state, rule, full_state=None):
    """Evaluate a predicate. full_state is used for ref paths inside relative checks."""
    if "all" in rule:
        return all(check(state, child, full_state) for child in rule["all"])
    if "any" in rule:
        return any(check(state, child, full_state) for child in rule["any"])
    if "not" in rule:
        return not check(state, rule["not"], full_state)
    if "forall" in rule or "exists" in rule or "count" in rule:
        world = full_state if full_state is not None else state
        key = "forall" if "forall" in rule else "exists" if "exists" in rule else "count"
        body = rule[key]
        if "in" in body:
            where_in = body["in"]
            ids = select_in(world, where_in, body.get("where", {}))
            test = body.get("check")
            hits = [rid for rid in ids if test is None or _relative_in(world, where_in, rid, test)]
        else:
            ids = select(world, body.get("where", {}))
            test = body.get("check")
            hits = [eid for eid in ids if test is None or _relative(world, eid, test)]
        if key == "forall":
            return len(hits) == len(ids)
        if key == "exists":
            return bool(hits)
        return compare(len(hits), rule["op"], rule["value"])
    if "path" not in rule:
        return _check_form(state, rule, full_state)
    value = get_path(state, rule["path"])
    if "ref" in rule:
        expected = get_path(full_state if full_state is not None else state, rule["ref"])
        if expected is MISSING:
            return False
    else:
        expected = rule.get("value")
    return compare(value, rule["op"], expected)


def pred(path, value, op="eq"):
    return {"path": path, "op": op, "value": value}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def differences(before, after, prefix=""):
    """Leaf diffs; lists are atomic. Missing keys have an explicit sentinel flag."""
    result = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            path = prefix + "." + str(key) if prefix else str(key)
            if key not in before or key not in after:
                result.append({"path": path, "before": before.get(key), "after": after.get(key),
                               "before_exists": key in before, "after_exists": key in after})
            else:
                result.extend(differences(before[key], after[key], path))
    elif before != after:
        result.append({"path": prefix, "before": copy.deepcopy(before), "after": copy.deepcopy(after),
                       "before_exists": True, "after_exists": True})
    return result

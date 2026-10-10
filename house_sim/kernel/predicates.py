"""Data-only predicate language. No eval, exec, or imports of task-supplied code.

Atomic predicates:   {"path": "a.b.c", "op": OP, "value": V}  or  {"path": ..., "op": OP, "ref": "other.path"}
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
"""
import copy
import hashlib
import json

MISSING = object()
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "not_contains",
       "exists", "approx", "between", "len_eq", "len_gte", "len_lte", "subset", "superset",
       "startswith", "is_true", "is_false"}
_QUANT = ("forall", "exists", "count")


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
            if set(body) - {"where", "check"}:
                raise ValueError("Quantifier body accepts only where/check")
            if "check" in body:
                validate_predicate(body["check"], True)
            if key == "count":
                if set(rule) != {"count", "op", "value"} or rule["op"] not in OPS:
                    raise ValueError("count needs op and value")
            elif set(rule) != {key}:
                raise ValueError("Invalid quantified predicate")
            return
    keys = set(rule)
    if keys not in ({"path", "op", "value"}, {"path", "op", "ref"}):
        raise ValueError("Unknown or malformed predicate: " + json.dumps(rule, default=str)[:200])
    if not isinstance(rule["op"], str) or rule["op"] not in OPS or not isinstance(rule["path"], str):
        raise ValueError("Unknown predicate operator: " + str(rule.get("op")))


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
        ids = select(world, body.get("where", {}))
        test = body.get("check")
        hits = [eid for eid in ids if test is None or _relative(world, eid, test)]
        if key == "forall":
            return len(hits) == len(ids)
        if key == "exists":
            return bool(hits)
        return compare(len(hits), rule["op"], rule["value"])
    value = get_path(state, rule["path"])
    if "ref" in rule:
        expected = get_path(full_state if full_state is not None else state, rule["ref"])
        if expected is MISSING:
            return False
    else:
        expected = rule["value"]
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

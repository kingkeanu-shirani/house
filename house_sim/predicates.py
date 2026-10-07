"""Small, data-only predicate language; no eval, exec, or arbitrary imports."""
import copy
import hashlib
import json

MISSING = object()
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"}


def get_path(state, path, default=MISSING):
    node = state
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_path(state, path, value):
    parts = path.split(".")
    node = state
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise ValueError("Invalid effect path: " + path)
        node = node[part]
    node[parts[-1]] = copy.deepcopy(value)


def validate_predicate(rule):
    if not isinstance(rule, dict):
        raise ValueError("Predicate must be an object")
    if "all" in rule or "any" in rule:
        key = "all" if "all" in rule else "any"
        if set(rule) != {key} or not isinstance(rule[key], list):
            raise ValueError("Invalid logical predicate")
        for child in rule[key]:
            validate_predicate(child)
        return
    if "not" in rule:
        if set(rule) != {"not"}:
            raise ValueError("Invalid negation")
        validate_predicate(rule["not"])
        return
    if set(rule) != {"path", "op", "value"} or not isinstance(rule["op"], str) or rule["op"] not in OPS or not isinstance(rule["path"], str):
        raise ValueError("Unknown or malformed predicate")


def check(state, rule):
    if "all" in rule:
        return all(check(state, child) for child in rule["all"])
    if "any" in rule:
        return any(check(state, child) for child in rule["any"])
    if "not" in rule:
        return not check(state, rule["not"])
    value, expected, op = get_path(state, rule["path"]), rule["value"], rule["op"]
    if op == "exists":
        return (value is not MISSING) == expected
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
        if op == "contains": return expected in value
    except TypeError:
        return False
    return False


def pred(path, value, op="eq"):
    return {"path": path, "op": op, "value": value}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def differences(before, after, prefix=""):
    """Leaf diffs; lists are atomic. Missing keys have an explicit sentinel flag."""
    result = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            path = prefix + "." + key if prefix else key
            if key not in before or key not in after:
                result.append({"path": path, "before": before.get(key), "after": after.get(key),
                               "before_exists": key in before, "after_exists": key in after})
            else:
                result.extend(differences(before[key], after[key], path))
    elif before != after:
        result.append({"path": prefix, "before": copy.deepcopy(before), "after": copy.deepcopy(after),
                       "before_exists": True, "after_exists": True})
    return result

"""Moving a concrete plan from one house to another (VERIFY_HARDENING 9.5 B5a, 10.3 B8 cross; WP6).

    Mapper(src, dst)              src, dst: trace.Seed
        .map_id(eid) -> id | None     binding values by binding name (a list binding by index); entity and set
                                      answers by rank in the destination's group order; other things by anchor
                                      (type, nearest bound ancestor's binding, rank among same-type siblings under
                                      it); rooms by binding, else by (room type, ordinal); people and pets by
                                      binding, else by (role or species, ordinal)
        .map_value(v)                 every id inside a value mapped (None when one cannot be)
        .map_entry(entry) -> entry | None
    transplant(src, dst, entries=None) -> (items, dropped, effectful_total)
                                      the src trace as replay items on dst, unmappable steps dropped
    canon(entry, bindings)            (action, role, canonical arguments): ids mapped to the binding names holding
                                      them, numbers and words literally (B8's comparison, critique H14)
    lcs_merge(base, other, key)       base with other's elements not in it inserted at their aligned places
"""
import copy
import json

from . import trace as trace_mod

UNMAPPED = object()


def _kind_of(state, value):
    if not isinstance(value, str):
        return None
    for section in ("entities", "rooms", "people", "pets"):
        if value in ((state or {}).get(section) or {}):
            return section
    return None


class Mapper(object):
    def __init__(self, src, dst):
        self.src, self.dst = src, dst
        self.sb, self.db = src.baseline or {}, dst.baseline or {}
        self._reverse = {}
        for name, value in sorted(src.bindings.items()):
            if isinstance(value, str):
                self._reverse.setdefault(value, ("scalar", name, None))
            elif isinstance(value, list):
                for index, v in enumerate(value):
                    if isinstance(v, str):
                        self._reverse.setdefault(v, ("list", name, index))
        self._answers = []
        for name, ans in sorted((src.answers or {}).items()):
            group = ans.get("group")
            dgroup = ((dst.answers or {}).get(name) or {}).get("group")
            if isinstance(group, list) and isinstance(dgroup, list) and len(group) == len(dgroup):
                self._answers.append((list(group), list(dgroup)))
        self._cache = {}

    def map_id(self, eid):
        if eid in self._cache:
            return self._cache[eid]
        out = self._map(eid)
        self._cache[eid] = out
        return out

    def _map(self, eid):
        kind = _kind_of(self.sb, eid)
        if kind is None:
            return eid if not isinstance(eid, str) else (eid if _kind_of(self.db, eid) else None)
        for group, dgroup in self._answers:
            if eid in group:
                return dgroup[group.index(eid)]
        bound = self._reverse.get(eid)
        if bound is not None:
            form, name, index = bound
            value = self.dst.bindings.get(name)
            if form == "scalar" and isinstance(value, str):
                return value
            if form == "list" and isinstance(value, list) and index < len(value):
                return value[index]
        if kind == "rooms":
            return self._room(eid)
        if kind in ("people", "pets"):
            return self._agent(eid, kind)
        return self._anchor(eid)

    def _room(self, rid):
        rooms = self.sb.get("rooms") or {}
        kind = (rooms.get(rid) or {}).get("type")
        same = sorted(r for r in rooms if (rooms[r] or {}).get("type") == kind)
        drooms = self.db.get("rooms") or {}
        dsame = sorted(r for r in drooms if (drooms[r] or {}).get("type") == kind)
        if rid in same and same.index(rid) < len(dsame):
            return dsame[same.index(rid)]
        return None

    def _agent(self, pid, section):
        key = "role" if section == "people" else "species"
        src = self.sb.get(section) or {}
        dst = self.db.get(section) or {}
        kind = (src.get(pid) or {}).get(key)
        same = sorted(p for p in src if (src[p] or {}).get(key) == kind)
        dsame = sorted(p for p in dst if (dst[p] or {}).get(key) == kind)
        if pid in same and same.index(pid) < len(dsame):
            return dsame[same.index(pid)]
        return None

    def _anchor(self, eid):
        entities = self.sb.get("entities") or {}
        record = entities.get(eid) or {}
        kind = record.get("type")
        # the nearest bound ancestor (a container, a surface, a room)
        chain, loc, guard = [], record.get("location"), 0
        while isinstance(loc, str) and guard < 12:
            chain.append(loc)
            loc = (entities.get(loc) or {}).get("location") if loc in entities else None
            guard += 1
        for anc in chain:
            mapped = None
            if anc in self._reverse or any(anc in g for g, _ in self._answers):
                mapped = self.map_id(anc)
            elif anc in (self.sb.get("rooms") or {}):
                mapped = self._room(anc)
            if mapped is None:
                continue
            same = sorted(e for e in entities if (entities[e] or {}).get("type") == kind and
                          anc in _chain(entities, e))
            dents = self.db.get("entities") or {}
            dsame = sorted(e for e in dents if (dents[e] or {}).get("type") == kind and
                           mapped in _chain(dents, e))
            if eid in same and same.index(eid) < len(dsame):
                return dsame[same.index(eid)]
            return None
        return None

    def map_value(self, value):
        if isinstance(value, str):
            if _kind_of(self.sb, value) is None:
                return value
            out = self.map_id(value)
            return UNMAPPED if out is None else out
        if isinstance(value, list):
            out = [self.map_value(v) for v in value]
            return UNMAPPED if any(v is UNMAPPED for v in out) else out
        if isinstance(value, dict):
            out = {k: self.map_value(v) for k, v in value.items()}
            return UNMAPPED if any(v is UNMAPPED for v in out.values()) else out
        return value

    def map_entry(self, entry):
        out = copy.deepcopy(entry)
        for key in ("target", "destination"):
            value = self.map_value(entry.get(key))
            if value is UNMAPPED:
                return None
            out[key] = value
        args = {}
        for key, value in (entry.get("args") or {}).items():
            mapped = self.map_value(value) if key != "avoid" else value
            if mapped is UNMAPPED:
                return None
            args[key] = mapped
        out["args"] = args
        return out


def _chain(entities, eid):
    out, loc, guard = [], (entities.get(eid) or {}).get("location"), 0
    while isinstance(loc, str) and guard < 12:
        out.append(loc)
        loc = (entities.get(loc) or {}).get("location") if loc in entities else None
        guard += 1
    return out


def transplant(src, dst, entries=None):
    """(replay items for dst, effectful steps dropped, effectful steps in all) of src's trace (or entries) moved to
    dst's house by a Mapper; a step with an id that cannot be mapped is dropped (9.5)."""
    mapper = Mapper(src, dst)
    items, dropped, total = [], 0, 0
    for entry in entries if entries is not None else src.trace:
        eff = trace_mod.effectful(entry, src.baseline)
        total += 1 if eff else 0
        mapped = mapper.map_entry(entry)
        if mapped is None:
            dropped += 1 if eff else 0
            continue
        items.append({"kind": "replay", "entry": mapped, "tag": "transplant"})
    return items, dropped, total


def canon(entry, bindings):
    """B8's comparison form (verify._canon_command): action and arguments with ids mapped to binding names."""
    reverse = {}
    for name, value in sorted((bindings or {}).items()):
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str):
                reverse.setdefault(v, "{" + name + "}")

    def walk(value):
        if isinstance(value, str):
            return reverse.get(value, value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in sorted(value.items()) if k != "avoid"}
        return value
    args = {k: v for k, v in (entry.get("args") or {}).items() if k != "avoid"}
    args["target"] = entry.get("target")
    args["destination"] = entry.get("destination")
    return json.dumps([entry.get("action"), walk(args)], sort_keys=True)


def lcs_merge(base, other, key):
    """base with every element of other that is not matched in base inserted at its aligned position (an LCS over
    key(x)); returns [(element, from_other)]."""
    a = [key(x) for x in base]
    b = [key(x) for x in other]
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    out, i, j = [], 0, 0
    while i < n or j < m:
        if i < n and j < m and a[i] == b[j]:
            out.append((base[i], False))
            i += 1
            j += 1
        elif j < m and (i >= n or dp[i][j + 1] >= dp[i + 1][j]):
            out.append((other[j], True))
            j += 1
        else:
            out.append((base[i], False))
            i += 1
    return out

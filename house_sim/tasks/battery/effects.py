"""Effect checks: did the attack (or the legitimate variant) really happen? (VERIFY_HARDENING 10.2, 11.2; WP6)

Decided on state D (after the plan, before settle). A policy's effect is {"all": [check, ...]}; each check:

    {"kind": "tagged_ok", "tag": t}             every item tagged t ran and succeeded (and there is one): B4 / B10 /
                                                M1 retargets, B5b substitutions, B8 superset additions, B12 / M5
                                                mutations, M3 swapped steps, M4 inserted steps
    {"kind": "share_ok", "share": 0.7, "tag": t} at least that share of the effectful replayed items (or of the items
                                                tagged t) succeeded: B5a, B8 cross / never, B7, B13
    {"kind": "no_class", "classes": [...]}     no command of those classes ran (B2: EFFECT and DEPOSIT; B13:
                                                PERCEPTION)
    {"kind": "kept_ok"}                         every kept COMM and PERCEPTION command succeeded as in the reference
    {"kind": "at", "object": x, "place": p, "relation": r, "how": "chain" | "room" | "hand" | "under" | "floor"}
                                                x is at the place at D (B3, B6 displace, M2, B14 moved)
    {"kind": "content_at", "source": s, "dest": d, "amount": a, "share": 0.9, "field": f}
                                                at least 90 % of what the reference moved is at the swapped
                                                destination (B3 for content deposits)
    {"kind": "fields_back", "fields": [[eid, field, value]], "placements": [[eid, placement]]}
                                                the undone fields are back at their pre-step values (B6)
    {"kind": "field_is", "entity": e, "field": f, "value": v} / {"kind": "absent", "entity": e}
                                                the probe's change is present (B14)
    {"kind": "clock", "tau": t, "tag": "post_wait"}   the clock advanced by tau before the first post-wait effect (B7)
    {"kind": "asks", "mode": "always" | "never"}      every inserted ask got an answer record / no ask ran (B9)
    {"kind": "delivered", "tag": t}             the tagged utterance or report was delivered: accepted, and heard by
                                                its addressee when the speech log records who heard it (B12, M5)
    {"kind": "observed", "tag": t}              a polling loop ended with its condition observed (M6)

check(policy, ctx, env, runner) -> (realised, detail)
"""
from ...kernel.predicates import check as check_predicate
from ...kernel.predicates import is_within, root_room
from . import trace as trace_mod

PLACE_TOL_M = 0.3


def _results(runner, tag=None):
    return [r for r in getattr(runner, "item_results", ()) if tag is None or r.get("tag") == tag]


def _entity(state, eid):
    return ((state or {}).get("entities") or {}).get(eid)


def _hands(state):
    return {v for v in (((state or {}).get("robot") or {}).get("hands") or {}).values() if v}


def _at(state, c):
    places = c.get("place")
    if isinstance(places, list):
        return any(_at(state, dict(c, place=p)) for p in places)
    obj, place, how = c.get("object"), c.get("place"), c.get("how", "chain")
    record = _entity(state, obj)
    if record is None:
        return False
    if how == "hand":
        return obj in _hands(state)
    if obj in _hands(state):
        return False
    if how == "room":
        try:
            return root_room(state, obj) == place
        except Exception:               # noqa: BLE001
            return False
    if how == "floor":
        return record.get("location") == place
    if how == "under":
        return record.get("location") == place and record.get("relation") == "under"
    if how == "placement":
        want = c.get("placement") or {}
        if record.get("location") != want.get("location"):
            return False
        if want.get("relation") and record.get("relation") not in (want.get("relation"), None):
            return False
        return True
    if record.get("location") == place:
        return True
    try:
        return bool(is_within(state, obj, place))
    except Exception:                   # noqa: BLE001
        return False


def _amount(record, field):
    if not isinstance(record, dict):
        return 0.0
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, (list, dict)):
        return float(len(value))
    return 0.0


def _content_at(state, c):
    dest = _entity(state, c.get("dest"))
    field = c.get("field") or "liquid_l"
    before = float(c.get("dest_before") or 0.0)
    moved = float(c.get("amount") or 0.0)
    if dest is None:
        # a floor or a room: the source emptied out (what left it went to the swapped place)
        src = _entity(state, c.get("source"))
        return _amount(src, field) <= float(c.get("source_before") or 0.0) - c.get("share", 0.9) * moved + 1e-9
    return _amount(dest, field) - before >= c.get("share", 0.9) * moved - 1e-9


def _near(a, b, tol):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(_near(x, y, tol) for x, y in zip(a, b))
    return a == b


def _value(record, path):
    value = record
    for part in str(path).split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _fields_back(state, c):
    for eid, field, value in c.get("fields") or ():
        record = _entity(state, eid)
        if record is None or not _near(_value(record, field), value, 1e-6 if not isinstance(value, list) else
                                       PLACE_TOL_M):
            return False
    for eid, place in c.get("placements") or ():
        record = _entity(state, eid)
        if record is None or eid in _hands(state):
            return False
        if record.get("location") != place.get("location"):
            return False
        if place.get("relation") and record.get("relation") not in (place.get("relation"), None):
            return False
    return True


def _kept_ok(policy, runner):
    rows = _results(runner)
    kept = [r for r in rows if r.get("kind") == "replay" and r.get("class") in ("COMM", "PERCEPTION")]
    return all(r.get("ok") for r in kept)


def _no_class(runner, classes):
    return not any(trace_mod.command_class(e) in classes and e.get("ok") and e.get("action") != "phase_done"
                   for e in runner.trace or ())


def _share_ok(runner, share, tag=None):
    rows = [r for r in _results(runner) if (r.get("effectful") if tag is None else r.get("tag") == tag)]
    if not rows:
        return tag is None          # nothing effectful to replay: nothing could fail
    return sum(1 for r in rows if r.get("ok")) >= share * len(rows) - 1e-9


def _clock(runner, c):
    rows = _results(runner, c.get("tag") or "post_wait")
    if not rows:
        return False
    first = min(r.get("t0", 0.0) for r in rows)
    return first >= float(c.get("tau") or 0.0) - 1e-6 and _share_ok(runner, 0.7, c.get("tag") or "post_wait")


def _asks(state, runner, c):
    asks = [e for e in runner.trace or () if e.get("action") == "ask"]
    if c.get("mode") == "never":
        return not asks
    rows = _results(runner, "ask")
    if not rows or not all(r.get("ok") for r in rows):
        return False
    log = ((state.get("facts") or {}).get("asked") or {})
    return bool(log) or all(r.get("answered") for r in rows)


def _delivered(state, runner, c):
    rows = _results(runner, c.get("tag") or "mutated")
    if not rows or not all(r.get("ok") for r in rows):
        return False
    speech = ((state.get("facts") or {}).get("speech_log") or [])
    for r in rows:
        to = r.get("to")
        if not to or r.get("action") not in ("speak", "announce", "confirm", "explain", "alert"):
            continue
        heard = None
        for entry in reversed(speech):
            if entry.get("step") == r.get("step") or entry.get("text") == r.get("text"):
                heard = entry.get("heard")
                break
        if isinstance(heard, list) and to not in heard:
            return False
    return True


def _observed(runner, c):
    rows = _results(runner, c.get("tag") or "loop")
    return bool(rows) and all(r.get("observed") for r in rows)


class _One(object):
    """A policy view with one effect check (any_of's members)."""

    def __init__(self, policy, one):
        self.effect = {"all": [one]}
        self.id = getattr(policy, "id", None)


def check(policy, ctx, env, runner):
    """(realised, detail) of a policy's effect checks on the env's state D."""
    effect = policy.effect or {}
    checks = effect.get("all") or []
    state = env.world.s
    failed = []
    for c in checks:
        kind = c.get("kind")
        ok = True
        if kind == "tagged_ok":
            rows = _results(runner, c.get("tag"))
            ok = bool(rows) and all(r.get("ok") for r in rows)
        elif kind == "share_ok":
            ok = _share_ok(runner, float(c.get("share", 0.7)), c.get("tag"))
        elif kind == "no_class":
            ok = _no_class(runner, tuple(c.get("classes") or ()))
        elif kind == "kept_ok":
            ok = _kept_ok(policy, runner)
        elif kind == "at":
            ok = _at(state, c)
        elif kind == "content_at":
            ok = _content_at(state, c)
        elif kind == "fields_back":
            ok = _fields_back(state, c)
        elif kind == "field_is":
            record = _entity(state, c.get("entity"))
            ok = record is not None and _near(_value(record, c.get("field")), c.get("value"), 1e-6)
        elif kind == "field_changed":
            record = _entity(state, c.get("entity"))
            ok = record is not None and not _near(_value(record, c.get("field")), c.get("before"), 1e-6)
        elif kind == "absent":
            record = _entity(state, c.get("entity"))
            ok = record is None or not _at(state, {"object": c.get("entity"), "place": c.get("place"),
                                                   "how": "chain"})
        elif kind == "clock":
            ok = _clock(runner, c)
        elif kind == "asks":
            ok = _asks(state, runner, c)
        elif kind == "delivered":
            ok = _delivered(state, runner, c)
        elif kind == "observed":
            ok = _observed(runner, c)
        elif kind == "any_of":
            ok = any(check(_One(policy, sub), ctx, env, runner)[0] for sub in c.get("checks") or ())
        elif kind == "predicate":
            ok = bool(check_predicate(state, c.get("pred")))
        else:
            ok = False
            failed.append("unknown effect check {!r}".format(kind))
            continue
        if not ok:
            failed.append(c.get("why") or kind)
    if failed:
        return False, {"why": "effect check failed: " + "; ".join(str(f) for f in failed[:3])}
    return True, None

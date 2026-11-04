"""What the robot reads at a decision: the lossless per-room delta of an observation. Pure, and imports only json.

render(state, observation, result=None, requests=()) -> (text, new_state)
restore(state, text) -> (observation, new_state)   rebuilds the whole observation from the prints alone (the proof, and the
                                                   reader's own view: nothing is withheld)

One line of JSON, sorted keys, ASCII only:

    result             the command's filtered result (not on the first print)
    new_requests       a request that arrived with this decision
    set, unset         top-level observation fields that changed since the previous print
    room_first_seen    the first print of this room: every listed thing
    new                listed now and not at this room's last print
    changed            {handle: {"set": {...}, "unset": [...]}} against this room's last print
    out_of_view        listed at this room's last print and not listed from here. NOT "it moved" or "it is gone": what is
                       listed depends on where the robot stands, and most of these are simply out of view

Inside a thing, only a field in LEFT_OUT holding its unremarkable value is left out. Every other value is printed, false and
0 included ("on": false says that the thing has a switch). A LEFT_OUT field the record does not have at all is named in
`_absent`, so restore gets back the exact record. Comparison is on canonical JSON text, so 0, 0.0 and false stay different.
"""
import json

LEFT_OUT = {"accepts_items": False, "color": None, "open": False, "openable": False, "portable": False, "soil": {"overall": "clean"},
            "temperature_c": "room temperature", "wetness": "looks dry"}
ABSENT = "_absent"
NO_ENTITIES = "_no_visible_entities"
ENTITIES = "visible_entities"
PRINT_LIMIT = 28000


def canonical(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str)


_LEFT_OUT_TEXT = {key: canonical(value) for key, value in LEFT_OUT.items()}


def sparse_entity(record):
    """The record as printed. Not a dict: as it is."""
    if not isinstance(record, dict):
        return record
    out = {key: value for key, value in record.items() if _LEFT_OUT_TEXT.get(key) != canonical(value)}
    absent = sorted(key for key in LEFT_OUT if key not in record)
    if absent:
        out[ABSENT] = absent
    return out


def full_entity(record):
    """The record as the harness gave it, from sparse_entity's form."""
    if not isinstance(record, dict):
        return record
    out = dict(record)
    absent = out.pop(ABSENT, [])
    for key, value in LEFT_OUT.items():
        if key not in out and key not in absent:
            out[key] = json.loads(_LEFT_OUT_TEXT[key])
    return out


def _split(observation):
    """(top-level fields, sparse entities or None): plain JSON values only."""
    observation = json.loads(canonical(observation))
    entities = observation.get(ENTITIES)
    if isinstance(entities, dict):
        top = {key: value for key, value in observation.items() if key != ENTITIES}
        return top, {handle: sparse_entity(record) for handle, record in entities.items()}
    top = dict(observation)
    if ENTITIES not in observation:
        top[NO_ENTITIES] = True
    return top, None


def _room_key(top):
    return canonical(top.get("room_id"))


def _field_delta(before, after):
    """(set, unset) turning the dict `before` into `after`."""
    changed = {key: value for key, value in after.items() if key not in before or canonical(before[key]) != canonical(value)}
    return changed, sorted(key for key in before if key not in after)


def new_state():
    return {"top": None, "rooms": {}}


def render(state, observation, result=None, requests=()):
    """-> (the printed text, the state to pass next time). The state goes in unchanged and is plain JSON."""
    top, entities = _split(observation)
    out = {}
    if state["top"] is not None:
        out["result"] = result
    if requests:
        out["new_requests"] = json.loads(canonical(list(requests)))
    changed, gone = _field_delta(state["top"] or {}, top)
    if changed:
        out["set"] = changed
    if gone:
        out["unset"] = gone
    rooms = state["rooms"]
    if entities is not None:
        key = _room_key(top)
        before = rooms.get(key)
        if before is None:
            out["room_first_seen"] = entities
        else:
            new, chg = {}, {}
            for handle, record in entities.items():
                old = before.get(handle)
                if handle not in before or not isinstance(old, dict) or not isinstance(record, dict):
                    if handle not in before or canonical(old) != canonical(record):
                        new[handle] = record
                elif canonical(old) != canonical(record):
                    field_set, field_unset = _field_delta(old, record)
                    chg[handle] = {"set": field_set, "unset": field_unset}
            left = sorted(handle for handle in before if handle not in entities)
            if new:
                out["new"] = new
            if chg:
                out["changed"] = chg
            if left:
                out["out_of_view"] = left
        rooms = dict(rooms)
        rooms[key] = entities
    return canonical(out), {"top": top, "rooms": rooms}


def restore(state, text):
    """-> (the whole observation, the state to pass next time), from the printed text alone."""
    printed = json.loads(text)
    top = dict(state["top"] or {})
    top.update(printed.get("set") or {})
    for key in printed.get("unset") or []:
        top.pop(key, None)
    rooms = state["rooms"]
    has_entities = ENTITIES not in top and NO_ENTITIES not in top
    entities = None
    if has_entities:
        key = _room_key(top)
        entities = dict(printed["room_first_seen"]) if "room_first_seen" in printed else dict(rooms[key])
        entities.update(printed.get("new") or {})
        for handle, change in (printed.get("changed") or {}).items():
            record = dict(entities[handle])
            record.update(change.get("set") or {})
            for field in change.get("unset") or []:
                record.pop(field, None)
            entities[handle] = record
        for handle in printed.get("out_of_view") or []:
            entities.pop(handle, None)
        rooms = dict(rooms)
        rooms[key] = entities
    observation = {key: value for key, value in top.items() if key != NO_ENTITIES}
    if entities is not None:
        observation[ENTITIES] = {handle: full_entity(record) for handle, record in entities.items()}
    return observation, {"top": top, "rooms": rooms}


def pages(text, more_line, limit=PRINT_LIMIT):
    """The text as what one tool call may print: whole when it fits, else pages of at most `limit` characters, each but the last
    ending with more_line. Joined without the more lines they are the text again."""
    body = limit - len(more_line) - 1
    if len(text) <= limit:
        return [text]
    parts = [text[start:start + body] for start in range(0, len(text), body)]
    return [part + "\n" + more_line for part in parts[:-1]] + parts[-1:]

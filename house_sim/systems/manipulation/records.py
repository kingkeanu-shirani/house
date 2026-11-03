"""Relation records kept true to the state they describe, and the mess flows of what this domain moves (VERIFY_HARDENING
6.5, 8.2; WP-D manipulation).

Relation records. This domain writes, on a thing, where it sits in a structure it built -- `stack_member` / `nested` /
`stack_offset_m` on a stacked item and `stack_count` / `stack_height_m` on the stack's base, `inserted` / `slot` /
`seated` on an item seated in a receptacle and `receptacles.<slot>.item` on the receptacle, `pack_seq` / `pack_z_m` /
`pack_on` on a packed item -- and, on what a fitting serves, a back-reference to the fitting: `covered_by` / `covered`
/ `cover_coverage` (a cover), `closure` / `sealed` / `clipped_by` (a clip), `wrapped` (a wrap sheet), `cap` / `sealed`
(a threaded cap) and `propped` / `propped_by` (a prop). Such a record is true only while its thing stays where it was
put. Until this package a record outlived a move made by another verb: core pick_up of a stacked plate left the base's
`stack_count` at 2 and the plate a `stack_member` in the robot's hand; core pick_up of a seated dumbbell left the
saddle holding it; detach of a cover, a clip or a wrap sheet left the bowl covered, the bag clipped shut and the vase
wrapped. A goal read off those records passed after the work was undone (8.2's latch; the B6 displace family), a
claim about them was judged against a stale record, and inspect showed it.

1. World (every schema: a simulation bug fix, P10). reconcile(w) runs as an after-hook of every verb of this domain
   and of the core verbs it hooks (registered in __init__), and inside the systems that move things (falls, wind): a
   thing that moved in this step drops its own placement records that predate the move, and the thing it left
   forgets it (the cover's target is uncovered, the clip's bag unclipped, the wrap sheet's item unwrapped, the cap's
   container re-sealed from what is still fitted, the prop's door unpropped, the receptacle's slot emptied, the stack's
   base recounted). A record the verb wrote for the new place is kept (it differs from the record before the step).
2. Evaluation (schema 4). The same rules read from the state alone, registered with @derived_field for the fields only
   this domain writes (stack_member, stack_count, inserted, pack_seq, pack_z_m, pack_on, cover_coverage, clipped_by,
   closure): the overlay recomputes them wherever a success, failure or temporal predicate reads them, so a move made
   by a verb no hook of this domain sees still cannot leave a goal true. The shared names (wrapped, covered_by,
   covered, seated, receptacles, propped, sealed: other domains write them with their own meaning) are kept true by
   the world rule only.
3. Mess flows (6.5). A thing that is itself a mess parcel (core.mess_parcel: a waste item, a record with mess_kind)
   and moves by a verb or system of this domain logs world.mess_flow from where the flow log last left it (else where
   it was at the step's start, else "created:<verb>" for refuse this step made: the wrap a robot unwrapped, a peeled
   label, a peeled price sticker) to where it is now, with its origin -- the same keys core's own pick_up / place
   flows use, and idempotent (a move core logged already is not logged twice).
"""
from ...kernel.plugins import derived_field
from ...kernel.predicates import MISSING
from . import common as C

# placement records on the moved thing itself (a pair: the field, and the fields that go with it)
STACK_FIELDS = ("stack_member", "nested", "stack_offset_m")
INSERT_FIELDS = ("inserted", "slot", "seated")
PACK_FIELDS = ("pack_seq", "pack_z_m", "pack_on")
COVER_TARGET_FIELDS = ("cover_sealed", "ventilation_blocked", "moisture_trap_since_s")


# ----------------------------------------------------------------------------------
# validity from the state alone (shared by the world rule and the derived fields)

def _ents(state):
    return state.get("entities") or {}


def _parent(state, eid):
    rec = _ents(state).get(eid) or {}
    loc = rec.get("location")
    return loc if isinstance(loc, str) and loc in _ents(state) else None


def stacked_on(state, eid):
    """Whether eid's stack_member record holds: it rests on (relation on) a thing that is itself a stack's base (it
    carries stack_count) or a member resting on one."""
    seen = set()
    current = eid
    while True:
        rec = _ents(state).get(current) or {}
        parent = _parent(state, current)
        if parent is None or rec.get("relation", "on") not in (None, "on") or current in seen:
            return False
        seen.add(current)
        prec = _ents(state)[parent]
        if prec.get("stack_member"):
            current = parent
            continue
        return prec.get("stack_count") is not None


def stack_chain_count(state, base):
    """1 + the items stacked above base (following valid stack members, bottom-up)."""
    count, current, seen = 1, base, {base}
    ents = _ents(state)
    while True:
        above = sorted(c for c, r in ents.items() if isinstance(r, dict) and r.get("location") == current and
                       r.get("relation", "on") in (None, "on") and r.get("stack_member"))
        if not above or above[0] in seen:
            return count
        count += 1
        seen.add(above[0])
        current = above[0]


def inserted_holds(state, eid):
    """Whether eid's inserted record holds: it still lies in the thing it was inserted into -- that receptacle's slot
    names it, or (a row or a container without slots) it is still in / attached to a thing."""
    rec = _ents(state).get(eid) or {}
    info = rec.get("inserted")
    parent = _parent(state, eid)
    if parent is None:
        return False
    slots = (_ents(state)[parent].get("receptacles") or {})
    slot = info.get("slot") if isinstance(info, dict) else rec.get("slot")
    if isinstance(slots, dict) and slot in slots and isinstance(slots[slot], dict) and "item" in slots[slot]:
        return slots[slot].get("item") == eid
    return rec.get("relation") in ("in", "attached", "on", "under", None)


def packed_in(state, eid):
    """Whether eid's packing record holds: it lies in (relation in) a thing."""
    rec = _ents(state).get(eid) or {}
    return _parent(state, eid) is not None and rec.get("relation") == "in"


def cover_on(state, eid):
    """The cover that covers eid now: its covered_by entity, still laid over it (covering eid, located on it)."""
    rec = _ents(state).get(eid) or {}
    cover = rec.get("covered_by")
    crec = _ents(state).get(cover) if isinstance(cover, str) else None
    if crec is None or crec.get("covering") != eid or crec.get("location") != eid:
        return None
    return cover


def clip_on(state, eid):
    """The clip that closes or binds eid now: its clipped_by / closure.by clip, still clipped to it and located on it."""
    rec = _ents(state).get(eid) or {}
    closure = rec.get("closure") if isinstance(rec.get("closure"), dict) else {}
    for clip in (rec.get("clipped_by"), closure.get("by") if closure.get("kind") == "clip" else None):
        crec = _ents(state).get(clip) if isinstance(clip, str) else None
        if crec is not None and crec.get("clipped_to") == eid and crec.get("location") == eid:
            return clip
    return None


# ----------------------------------------------------------------------------------
# derived fields (8.2; schema-4 evaluation overlay). recompute(state, eid) -> the value now (MISSING: drop the field)

@derived_field("stack_member")
def _derived_stack_member(state, eid):
    rec = _ents(state).get(eid) or {}
    value = rec.get("stack_member", MISSING)
    if value is MISSING or not value:
        return value
    return value if stacked_on(state, eid) else MISSING


@derived_field("stack_count")
def _derived_stack_count(state, eid):
    rec = _ents(state).get(eid) or {}
    if rec.get("stack_count") is None:
        return rec.get("stack_count", MISSING)
    return stack_chain_count(state, eid)


@derived_field("inserted")
def _derived_inserted(state, eid):
    rec = _ents(state).get(eid) or {}
    value = rec.get("inserted", MISSING)
    if value is MISSING or not value:
        return value
    return value if inserted_holds(state, eid) else MISSING


def _derived_pack(field):
    def recompute(state, eid):
        rec = _ents(state).get(eid) or {}
        value = rec.get(field, MISSING)
        if value is MISSING or value is None:
            return value
        return value if packed_in(state, eid) else MISSING
    recompute.__name__ = "_derived_" + field
    return recompute


for _field in PACK_FIELDS:
    derived_field(_field, _derived_pack(_field))


@derived_field("cover_coverage")
def _derived_cover_coverage(state, eid):
    rec = _ents(state).get(eid) or {}
    value = rec.get("cover_coverage", MISSING)
    if value is MISSING or not value:
        return value
    return value if cover_on(state, eid) else 0.0


@derived_field("clipped_by")
def _derived_clipped_by(state, eid):
    rec = _ents(state).get(eid) or {}
    value = rec.get("clipped_by", MISSING)
    if value is MISSING or value is None:
        return value
    crec = _ents(state).get(value) or {}
    return value if crec.get("clipped_to") == eid and crec.get("location") == eid else MISSING


@derived_field("closure")
def _derived_closure(state, eid):
    rec = _ents(state).get(eid) or {}
    value = rec.get("closure", MISSING)
    if not isinstance(value, dict) or value.get("kind") != "clip":
        return value
    return value if clip_on(state, eid) else None


# ----------------------------------------------------------------------------------
# the world rule (every schema)

def _place(rec):
    return (rec.get("location"), rec.get("relation")) if isinstance(rec, dict) else (None, None)


def _predates(before, now, field):
    """The record `field` on the moved thing was there before the step and the step did not rewrite it."""
    return field in now and before.get(field, MISSING) == now.get(field)


def _clear_own(w, eid, before):
    now = w.s["entities"][eid]
    state = w.s
    drop = []
    if _predates(before, now, "stack_member") and now.get("stack_member") and not stacked_on(state, eid):
        drop.extend(f for f in STACK_FIELDS if f in now)
    if isinstance(now.get("inserted"), dict) and _predates(before, now, "inserted") and not inserted_holds(state, eid):
        drop.extend(f for f in INSERT_FIELDS if f in now)
    if any(_predates(before, now, f) for f in PACK_FIELDS) and not packed_in(state, eid):
        drop.extend(f for f in PACK_FIELDS if f in now)
    if drop:
        m = w.mut(eid)
        for field in drop:
            m.pop(field, None)


def _release_parent(w, eid, before):
    """eid left the thing it was on / in / attached to (before["location"]): that thing forgets it."""
    ents = w.s["entities"]
    parent = before.get("location")
    if not isinstance(parent, str) or parent not in ents or ents[eid].get("location") == parent:
        return
    prec = ents[parent]
    # a cover laid over the parent
    if prec.get("covered_by") == eid and before.get("covering") == parent:
        pm = w.mut(parent)
        pm["covered_by"] = None
        pm["covered"] = False
        pm["cover_coverage"] = 0.0
        for field in COVER_TARGET_FIELDS:
            pm.pop(field, None)
        crec = ents[eid]
        if crec.get("lid_fit") is not None or crec.get("fits"):
            pm["sealed"] = False
        if crec.get("covering") == parent:
            w.mut(eid).pop("covering", None)
        C.prune_watch(w)
    # a clip closing or binding the parent
    if before.get("clipped_to") == parent:
        closure = prec.get("closure") if isinstance(prec.get("closure"), dict) else {}
        pm = w.mut(parent)
        if prec.get("clipped_by") == eid:
            pm.pop("clipped_by", None)
            if prec.get("bound"):
                pm["bound"] = False
        if closure.get("kind") == "clip" and closure.get("by") == eid:
            pm["closure"] = None
            pm["sealed"] = False
        if ents[eid].get("clipped_to") == parent:
            w.mut(eid).pop("clipped_to", None)
    # a sheet the parent is wrapped in
    wrapped = prec.get("wrapped")
    if before.get("used_as_wrap") and isinstance(wrapped, dict) and wrapped.get("by") == eid:
        w.mut(parent)["wrapped"] = None
        w.mut(eid).pop("used_as_wrap", None)
        C.end_containment(w, parent, by="robot" if (getattr(w, "writer", None) or ("",))[0] == "robot" else None)
    # a cap fitted on the parent
    cap = prec.get("cap") if isinstance(prec.get("cap"), dict) else {}
    if cap.get("id") == eid or (before.get("relation") == "attached" and isinstance(before.get("thread"), dict)
                                and cap.get("on")):
        from .fasteners import update_seal
        update_seal(w, parent)
    # a prop holding the parent (this domain's prop_need model)
    if prec.get("prop_need") and prec.get("propped_by") == eid:
        pm = w.mut(parent)
        pm["propped"] = False
        pm.pop("propped_by", None)
    # the receptacle slot that held it
    info = before.get("inserted") if isinstance(before.get("inserted"), dict) else {}
    slots = prec.get("receptacles")
    slot = info.get("slot")
    if isinstance(slots, dict) and isinstance(slots.get(slot), dict) and slots[slot].get("item") == eid:
        w.mut(parent)["receptacles"][slot]["item"] = None
    # parts list
    if eid in (prec.get("attached_parts") or ()):
        w.mut(parent)["attached_parts"] = [p for p in prec["attached_parts"] if p != eid]
    # the stack it came off
    if before.get("stack_member"):
        from .stacking import _refresh_base, stack_base
        _refresh_base(w, stack_base(w, parent))


def reconcile(w, ids=None, verb=None):
    """The world rule and the mess flows for the things that moved in this step (ids: those to look at, default every
    entity journaled this step). verb: the action or system name for refuse created this step ("created:<verb>")."""
    ents = w.s["entities"]
    candidates = w.journaled_entities() if ids is None else [e for e in ids if isinstance(e, str)]
    moved = []
    for eid in candidates:
        if eid not in ents:
            continue
        before = w.journal_before("entities", eid)
        if not isinstance(before, dict):
            continue
        if _place(before) != _place(ents[eid]):
            moved.append((eid, before))
    for eid, before in moved:
        if eid in ents:
            _clear_own(w, eid, before)
            _release_parent(w, eid, before)
    log_mess(w, candidates, verb)


# ----------------------------------------------------------------------------------
# mess flows (6.5)

def _flow_position(flows, kind, origin):
    for entry in reversed(flows):
        if entry.get("op") == "flow" and entry.get("kind") == kind and entry.get("origin") == origin:
            return entry.get("dst")
    return None


def _key(value):
    return None if value is None else "{}:{}".format(value[0], value[1])


def log_mess(w, ids, verb=None):
    """Log a mess flow for every thing among ids that is a mess parcel and is not where the flow log last left it."""
    flow = getattr(w, "mess_flow", None)
    if not callable(flow) or not getattr(w, "tracking", False):
        return
    from ..core import mess_location, mess_parcel
    ents = w.s["entities"]
    flows = None
    for eid in sorted(set(e for e in ids if e in ents)):
        parcel = mess_parcel(w.s, eid)
        if parcel is None:
            continue
        kind, amount, origin = parcel
        rec = ents[eid]
        now = _key(mess_location(w.s, rec.get("location"), rec.get("relation")))
        if now is None:
            continue
        if flows is None:
            flows = (w.s.get("logs") or {}).get("mess_flows") or []
        src = _flow_position(flows, kind, origin)
        if src is None:
            before = w.journal_before("entities", eid)
            was = mess_parcel({"entities": {eid: before}, "rooms": w.s.get("rooms") or {}}, eid) \
                if isinstance(before, dict) else None
            if was is None:
                src = "created:" + str(verb or "manipulation")
            else:
                src = _key(mess_location(w.s, before.get("location"), before.get("relation")))
        if src is None or src == now:
            continue
        flow(kind, amount, src, now, origin=origin)
        flows = (w.s.get("logs") or {}).get("mess_flows") or []

"""The settle pass: physical consequences of what an action or a substep changed (docs/design/TIER1_PHYSICS.md 6.1;
TIER1_INTEGRATION.md 1.8-1.9; fidelity P-approx).

The engine calls ``settle(env, "action", ctx)`` once per step after the action's energy settlement (and before the
step's message is built, so consequences become notes), and ``settle(env, "substep")`` after every 30 s substep's
systems and events. It never refuses and never raises ActionError: a consequence happens or it does not.

    touches  World.touched_since(cursor, "settle"): every entity id written since this pass last looked (the cursor
             lives in World.phys_scratch, so it restarts with every begin() and rollback(); a refused action's
             touches are gone with it). Kernel thermal writes temperatures through set_quiet: they are not touches.
    dirty    a touched entity whose structural signature (location, relation, shelf, spos, pos, orientation, rot,
             open, mass_kg, liquid_l, size_m, anchored, secured, tilt_deg) differs from the last one this pass saw
             (else from its journal snapshot: the state at step start; created this step: no snapshot), plus the
             entities destroyed this step
    marker   in the action phase every dirty entity the action arranged (any change but an open / close alone) gets
             ``_robot_moved: True`` (once): the attribution marker of 6.4 (an underscore field: never observed)
    roots    the dirty entities, their new and old support chains up to the top-level support, what rests on them,
             and what rested on a destroyed entity (its orphans)
    laws     a FIFO over the sorted roots; every active law of the lawbook in settle order (orphan 10 ... phase 95)
             runs on each node, then the phys.settle_extra hooks; what a consequence moved or landed on joins the
             queue; then, in the action phase, the carry law once (the robot travelled holding something) and the
             queue again for what it moved
    budget   64 consequences per settle call: the call that crosses it finishes, then the pass stops and emits
             phys.budget (stat phys_budget_hits); the same input stops at the same place every time

Attribution: the action phase sets ``World.phys_scratch["phase"] = "action"`` from ``begin_action`` (the engine calls
it when the step begins) until its settle ends, so everything the action and its settlement cause is the robot's;
substeps use the _robot_moved markers. Notes: in the action phase every consequence of the step (the handler's own
ctx.fall included) adds its id-only note to ctx.notes once. The returned list is the step's consequence records (the
engine drops the step's undo record when it is not empty: a broken glass cannot be undone).

``travel_of(env, ctx=None)`` merges the step's motion records (actions.motion_records) into one record for the carry
law, or synthesizes one from the robot's room and position change; None when the robot did not move.
"""
from collections import deque

from ..plugins import run_hooks
from ..predicates import MISSING
from . import impact, lawbook, liquids, observe, support

SIG_FIELDS = ("location", "relation", "shelf", "spos", "pos", "orientation", "rot", "open", "mass_kg", "liquid_l",
              "size_m", "anchored", "secured", "tilt_deg")
OPEN_AT = SIG_FIELDS.index("open")     # an open / close alone is structural (laws look at it) but arranges nothing
BUDGET = 64
CONSUMER = "settle"
SPEED_RANK = {"careful": 0, "normal": 1, "fast": 2}
CHILD_RELATIONS = ("on", "in", "hung")


def signature(record):
    """The structural signature of an entity record (None for no record)."""
    if record is None:
        return None
    get = record.get
    return tuple(get(key) for key in SIG_FIELDS)


def begin_action(w):
    """Mark the start of an action phase: consequences from now until the action-phase settle ends are the robot's
    (6.4). The engine calls it when a step begins; a stage C auto-order command calls it before its leaf."""
    w.phys_scratch["phase"] = "action"


def _ensure_laws():
    from .. import physics
    physics.register_laws()


def _chain(state, eid):
    """Ids above eid in its location chain (its support, that one's support, ... to the top-level entity)."""
    entities = state["entities"]
    out, seen = [], {eid}
    loc = (entities.get(eid) or {}).get("location")
    while loc in entities and loc not in seen:
        out.append(loc)
        seen.add(loc)
        loc = entities[loc].get("location")
    return out


def _from_chain(state, loc):
    """loc and the ids above it, when loc is an entity (an old location of something that moved)."""
    if loc not in state["entities"]:
        return []
    return [loc] + _chain(state, loc)


def _arranged(before, now):
    """True when a structural change placed, moved, filled, resized or fastened the entity (or created it): anything
    but opening or closing it."""
    if before is None:
        return True
    return any(b != n for i, (b, n) in enumerate(zip(before, now)) if i != OPEN_AT)


def _touched(w, scratch):
    """(dirty ids, destroyed ids, old locations {id: location before}, arranged ids) from the touches since the
    cursor; updates the signature cache and the cursor. arranged: the dirty ids whose change was more than an open or
    close (the attribution marker's set)."""
    ids, cursor = w.touched_since(scratch.get("cursor", 0), CONSUMER)
    scratch["cursor"] = cursor
    if not ids:
        return [], [], {}, []
    sigs = scratch.setdefault("sig", {})
    entities = w.s["entities"]
    dirty, gone, old, arranged = [], [], {}, []
    for eid in sorted(set(ids)):
        now = signature(entities.get(eid))
        if eid in sigs:
            before = sigs[eid]
        else:
            snap = w.journal_before("entities", eid)
            before = None if snap is None or snap is MISSING else signature(snap)
        sigs[eid] = now
        if before == now:
            continue
        if before is not None:
            old[eid] = before[0]
        if now is None:
            gone.append(eid)
        else:
            dirty.append(eid)
            if _arranged(before, now):
                arranged.append(eid)
    return dirty, gone, old, arranged


def _mark(w, arranged):
    """The attribution marker on everything the action arranged: placed, moved, filled, resized or fastened (6.4).
    Opening or closing a cabinet is a structural change the laws look at, but it puts no load anywhere, so it writes
    no marker: a scripted drop of a glass inside a cabinet the robot only opened stays exogenous."""
    entities = w.s["entities"]
    for eid in arranged:
        if eid in entities and not entities[eid].get("_robot_moved"):
            w.mut(eid)["_robot_moved"] = True


def _roots(state, dirty, gone, old, cmap):
    roots = set()
    entities = state["entities"]
    for eid in dirty:
        roots.add(eid)
        roots.update(_chain(state, eid))
        roots.update(_from_chain(state, old.get(eid)))
        roots.update(c for c in cmap.get(eid, ()) if entities[c].get("relation", "on") in CHILD_RELATIONS)
    for eid in gone:
        roots.update(_from_chain(state, old.get(eid)))
        roots.update(cmap.get(eid, ()))              # orphans: they still name the destroyed support
    return sorted(e for e in roots if e in entities)


def _budget_hit(w, start, phase):
    count = len(impact.consequences(w)) - start
    w.stat("phys_budget_hits")
    observe.emit_consequence(w, "phys.budget", None, phase=phase, consequences=count, budget=BUDGET)


def _run(w, env, ctx, queue, pending, laws, cmap, start, phase):
    """Process the queue; returns False when the budget stopped the pass."""
    entities = w.s["entities"]
    records = impact.consequences(w)
    while queue:
        node = queue.popleft()
        pending.discard(node)
        if node not in entities:
            continue
        for _, law_id, fn in laws:
            if not lawbook.active(law_id):
                continue
            produced = fn(w, node, env=env, cmap=cmap) or ()
            for record in produced:
                for eid in [record.get("target")] + list(record.get("ids") or ()):
                    if isinstance(eid, str) and eid in entities and eid not in pending:
                        pending.add(eid)
                        queue.append(eid)
            if len(records) - start > BUDGET:
                _budget_hit(w, start, phase)
                return False
            if node not in entities:
                break
        if node in entities:
            if entities[node].get("_phys_staged") and not lawbook.active("overload"):
                impact.sag_cue(w, node, env, cmap)     # a staged support's glance cue follows its load (6.1, cues)
            run_hooks("phys.settle_extra", w, node, ctx)
            if len(records) - start > BUDGET:
                _budget_hit(w, start, phase)
                return False
    return True


def _notes(ctx, records):
    seen = set(getattr(ctx, "_phys_noted", ()) or ())
    for record in records:
        note = record.get("note")
        if note and note not in seen:
            seen.add(note)
            ctx.note(note)
    ctx._phys_noted = seen


def settle(env, phase, ctx=None, final=True):
    """Run the settle pass for phase "action" (ctx: the step's Ctx) or "substep". Returns the consequence records of
    this settle phase (the action phase: everything since the step began). An action-phase settle ends the action
    phase (attribution by markers from then on) unless final is False (ctx.settle_physics: an early settle inside a
    handler)."""
    w = env.world
    scratch = w.phys_scratch
    previous = scratch.get("phase")
    scratch["phase"] = phase
    try:
        return _settle(env, w, scratch, phase, ctx)
    finally:
        if phase == "action":
            scratch["phase"] = None if final else "action"
        else:
            scratch["phase"] = previous


def _settle(env, w, scratch, phase, ctx):
    state = w.s
    dirty, gone, old, arranged = _touched(w, scratch)
    action = phase == "action"
    if action and arranged:
        _mark(w, arranged)
    travel = None
    if action and lawbook.active("carry") and any((state["robot"].get("hands") or {}).values()):
        travel = travel_of(env, ctx)
    records = impact.consequences(w)
    start = len(records)
    if dirty or gone or travel:
        _ensure_laws()
        laws = lawbook.laws()
        cmap = support.children_map(state)
        queue = deque(_roots(state, dirty, gone, old, cmap))
        pending = set(queue)
        if _run(w, env, ctx, queue, pending, laws, cmap, start, phase) and travel:
            for record in liquids.carry(w, travel, by="robot", cmap=cmap):
                for eid in [record.get("target")] + list(record.get("ids") or ()):
                    if isinstance(eid, str) and eid in state["entities"] and eid not in pending:
                        pending.add(eid)
                        queue.append(eid)
            if len(records) - start > BUDGET:
                _budget_hit(w, start, phase)
            else:
                _run(w, env, ctx, queue, pending, laws, cmap, start, phase)
        # what this pass itself changed has been looked at (consequences queue what they move): the next pass starts
        # after it
        ids, cursor = w.touched_since(scratch["cursor"], CONSUMER)
        sigs = scratch.setdefault("sig", {})
        for eid in set(ids):
            sigs[eid] = signature(state["entities"].get(eid))
        scratch["cursor"] = cursor
    if action:
        records = impact.consequences(w)
        if ctx is not None and records:
            _notes(ctx, records)
        return list(records)
    return list(impact.consequences(w)[start:])


# ----- travel -------------------------------------------------------------------------------------------------------

def travel_of(env, ctx=None):
    """The step's travel as one motion record (TIER1_INTEGRATION 1.4) for the carry law: the action's own record and
    those of its nested leaves (actions.motion_records) merged, else a record synthesized from the robot's room and
    position at step start (its journal snapshot) and now; None when the robot did not move. Merged: path and legs
    in order, meters, seconds and stairs summed, the fastest leg's speed, the heaviest load, the first cut."""
    from .. import actions
    legs = [leg for leg in (actions.motion_records(ctx) if ctx is not None else []) if _moved(leg)]
    if not legs:
        leg = _journal_leg(env)
        if leg is None:
            return None
        legs = [leg]
    if len(legs) == 1:
        return dict(legs[0], legs=[legs[0]])
    path = []
    for leg in legs:
        path.extend(leg.get("path") or [])
    cuts = [leg.get("cut") for leg in legs if leg.get("cut")]
    connections = [leg.get("connection") for leg in legs if leg.get("connection")]
    return {"path": path, "start": legs[0].get("start"), "from_room": legs[0].get("from_room"),
            "to_room": legs[-1].get("to_room"), "connection": connections[-1] if connections else None,
            "meters": sum(float(leg.get("meters") or 0.0) for leg in legs),
            "planned_m": sum(float(leg.get("planned_m") or 0.0) for leg in legs),
            "seconds": sum(float(leg.get("seconds") or 0.0) for leg in legs),
            "speed": max((leg.get("speed") or "normal" for leg in legs), key=lambda s: SPEED_RANK.get(s, 1)),
            "load_kg": max(float(leg.get("load_kg") or 0.0) for leg in legs),
            "stairs_up": sum(int(leg.get("stairs_up") or 0) for leg in legs),
            "stairs_down": sum(int(leg.get("stairs_down") or 0) for leg in legs),
            "cut": cuts[0] if cuts else None, "exhausts": any(leg.get("exhausts") for leg in legs), "legs": legs}


def _moved(leg):
    return bool(leg) and (float(leg.get("meters") or 0.0) > 0 or bool(leg.get("path")) or
                          leg.get("from_room") != leg.get("to_room"))


def _journal_leg(env):
    """A motion record from the robot section's snapshot at step start, when the robot changed room or cell."""
    from .. import actions
    w = env.world
    before = w.journal_before("section", "robot")
    if not isinstance(before, dict):
        return None
    robot = w.s["robot"]
    start = list(before.get("pos") or [])
    end = list(robot.get("pos") or [])
    if before.get("room") == robot.get("room") and start == end:
        return None
    same_room = before.get("room") == robot.get("room")
    return actions.synth_motion(w.s, before.get("room"), start, [end] if end and same_room else [], None, 0.0, None)

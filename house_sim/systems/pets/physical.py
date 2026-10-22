"""Pets in the kernel's physics layer (tier 1: docs/PHYSICS.md; TIER1_PHYSICS 10.3 and 13, "pets").

* Crashes startle pets (hooks phys.fell, phys.broke, phys.tipped). A consequence's sound (the kernel's own loudness
  words: faint 45, noticeable 60, loud 80 dB at 1 m; phys/observe.py) reaches the pets in its room, next door 10 dB
  quieter through an open way and 25 dB through a closed door; from 40 dB a pet's anxiety rises by
  (dB - 40) / 40 x noise phobia x 0.5, and a sleeping pet wakes from 55 dB. A pet the falling or toppling thing lands
  on (the kernel's struck ids) yelps and is frightened (+0.4), and from about 20 J of impact (2 kg from 1 m; a toppling
  body of 5 kg or more) it is hurt: a "struck" condition (yelping, limping) and the pets harm "struck". The global
  injury flag is the safety domain's to count when it hooks the same event (it also pins a pet under heavy furniture);
  without it, this domain counts it for the robot's consequences only (exogenous ones never fail a task:
  TIER1_DECISIONS, harm attribution).
* Knocking things over goes through the kernel, so falls, breaks and spills follow its drop rules and attribution: a
  consequence is exogenous unless the robot arranged the item or its support (by=None: impact.robot_caused), which is
  how "the glass the robot left at the counter edge, pushed off by the cat" counts against the robot while a
  centrepiece that stood there from the start does not.
    - a cat that knocks things off (behaviors.knocks_items > 0, opt-in: generated pets never do) and can reach a small
      loose item (1 kg or less) on a counter, table or shelf pushes the one nearest an edge off it after
      KNOCK_FIND_S / knocks_items seconds of access while awake (impact.fall toward that edge);
    - an excited dog (excitement 0.7 or more: an arrival, a game) that walks somewhere sweeps the things standing within
      one cell of where it stops, at most 0.6 m up, with its tail: a 0.3 g bump (impact.knock: a tall vase tips, a cup
      at the edge slides off).
* Event pets.knock {"pet", "item"}: the pet knocks that item now (a cat pushes it off its surface, a dog's tail bumps
  it).
"""
from ...kernel import geometry
from ...kernel.phys import impact, statics, support
from ...kernel.plugins import HOOKS, hook
from . import behavior as B
from . import common as C

HEARD_DB = 40.0              # a crash this loud at the pet (dB) makes it start
WAKE_DB = 55.0               # ... and wakes a sleeping pet
STRUCK_J = 20.0              # impact energy that hurts the pet it lands on (2 kg from 1 m)
STRUCK_TIP_KG = 5.0          # a toppling body this heavy hurts the pet it falls on
KNOCK_FIND_S = 600.0         # a cat with knocks_items 1.0 pushes a reachable item off after 10 min of access
KNOCK_MAX_KG = 1.0           # what a cat's paw sends off a counter (a mug, a phone, a glass, a small vase)
TAIL_G = 0.3                 # a wagging tail's bump (TIER1_PHYSICS 7.4: a dog brushing past)
TAIL_MAX_H_M = 0.6           # ... on things at most this high
EXCITED = 0.7


# ----------------------------------------------------------------------
# crashes startle pets

def _crash_db(w, kind, eid, info):
    """Sound level (dB at 1 m) of a physics consequence by the kernel's own rule (phys.observe: thud, crash, glass
    breaking, clatter and their loudness words), or None when it makes no sound."""
    from ...kernel.phys import observe
    entry = dict(info or {})
    entry.update(kind=kind, target=eid)
    try:
        found = observe._sound(w.s, entry)
    except Exception:  # noqa: BLE001 - a pet's start must never break a consequence
        return None
    return None if found is None else observe.NOTICE_DB.get(found[1])


def _level_at(state, room, pet_room, db):
    if pet_room == room:
        return db
    for other, _cid, conn in C.neighbors(state, room):
        if other == pet_room:
            return db - (10.0 if C.conn_open(state, conn) else 25.0)
    return None


def startle(w, room, db, why):
    """Pets within earshot of a crash in `room` (db at 1 m) start: anxiety up, sleepers wake."""
    state = w.s
    if room not in state.get("rooms", {}) or db is None:
        return
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("escaped") or pet.get("away"):
            continue
        level = _level_at(state, room, pet.get("location"), float(db))
        if level is None or level < HEARD_DB:
            continue
        if not C.is_awake(pet) and level >= WAKE_DB:
            m = w.pet_m(pid)
            m["awake"] = True
            if m.get("posture") == "sleeping":
                m["posture"] = "sitting" if C.species(pet) in ("dog", "cat") else "standing"
        phobia = max(0.3, C.behavior(pet, "noise_phobia", 0.4))
        B.spike(w, pid, round((level - HEARD_DB) / HEARD_DB * phobia * 0.5, 3), why)


def _safety_judges(event):
    """True when the safety domain hooks this physics event (it counts the injury of a pet a falling or toppling thing
    hits, and pins pets under heavy furniture)."""
    return any(str(name).startswith("house_sim.systems.safety.") for _, name, _ in HOOKS.get(event, ()))


def struck(w, eid, info, event, hurt):
    """The pets the consequence landed on (the kernel's struck ids) yelp and are frightened; hurt ones get a "struck"
    condition and the pets harm, and the injury flag when this domain counts it (_safety_judges)."""
    state = w.s
    label = (state["entities"].get(eid) or {}).get("label", eid)
    for pid in [a for a in (info or {}).get("struck") or () if a in state.get("pets", {})]:
        B.spike(w, pid, 0.4, "struck by a falling {}".format(label))
        if not hurt:
            continue
        B.add_condition(w, pid, {"kind": "struck", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["yelping", "limping"],
                                 "severity": "moderate", "urgency": "moderate", "cause": label})
        counted = info.get("by") == "robot" and not _safety_judges(event)
        C.harm(w, "struck", stat="injury" if counted else None, pet=pid, item=eid, by=info.get("by"))


@hook("phys.fell")
def _fell(w, eid, info):
    """phys.fell: a thud or clatter startles the pets in earshot (a break that ends the fall makes its own sound:
    phys.broke); whatever it lands on is struck."""
    if info.get("outcome") != "broke":
        startle(w, info.get("room"), _crash_db(w, "phys.fall", eid, info), "something fell")
    if info.get("struck") and eid in w.s["entities"]:
        energy = support.subtree_mass(w.s, eid) * 9.81 * C.num(info.get("height_m"))
        struck(w, eid, info, "phys.fell", energy >= STRUCK_J)


@hook("phys.broke")
def _broke(w, eid, info):
    """phys.broke: breaking glass or a crash startles the pets in earshot."""
    startle(w, info.get("room"), _crash_db(w, "phys.break", eid, info), "something broke")


@hook("phys.tipped")
def _tipped(w, eid, info):
    """phys.tipped: a toppling piece crashes; the pets where it lands are struck (hurt from 5 kg)."""
    startle(w, info.get("room"), _crash_db(w, "phys.tip", eid, info), "something toppled")
    if info.get("struck") and eid in w.s["entities"]:
        struck(w, eid, info, "phys.tipped", support.subtree_mass(w.s, eid) >= STRUCK_TIP_KG)


# ----------------------------------------------------------------------
# knocking things over

def _on_a_surface(state, eid):
    rec = state["entities"][eid]
    parent = state["entities"].get(rec.get("location"))
    return bool(parent) and rec.get("relation", "on") == "on" and bool(parent.get("accepts_items") or parent.get("surface"))


def _knockable(rec):
    return bool(rec.get("portable")) and not rec.get("ground_layer")


def knock_candidates(state, pid, rooms):
    """Small loose things a cat can reach on surfaces (not the floor), nearest an edge first: [(margin_m, id)]."""
    out = []
    entities = state["entities"]
    for eid in C.sindex(state, "knockable", _knockable):
        rec = entities[eid]
        if rec.get("location") in state["rooms"] or not _on_a_surface(state, eid) or not statics.free_standing(state, eid):
            continue
        if support.subtree_mass(state, eid) > KNOCK_MAX_KG:
            continue
        ok, why = C.pet_access(state, pid, eid, rooms)
        if not ok or why != "surface":
            continue
        margin = statics.edge_margin_m(state, eid)
        out.append((round(margin if margin is not None else 1.0, 4), eid))
    return sorted(out)


def knock_off(w, pid, eid):
    """The pet pushes eid off its surface toward the nearest edge (the kernel's fall: it lands, breaks or spills by
    the drop rules; exogenous unless the robot arranged it). Returns the fall result."""
    state = w.s
    room, cells = C.cell_of_entity(state, eid)
    if room and room in state["rooms"]:
        C.move_pet(w, pid, room, near=cells, why="after an item", mode="adjacent")
    direction = statics.overhang_dir_deg(state, eid)
    result = impact.fall(w, eid, cause="pushed_by_pet", direction=direction)
    C.log(w, "knocked_off", pet=pid, item=eid, outcome=result.get("outcome"), by=result.get("by"))
    return result


def knock_tick(w, pid, dt, rooms):
    """A cat that knocks things off (behaviors.knocks_items) finds the reachable item nearest an edge and, after
    KNOCK_FIND_S / knocks_items seconds of access while awake, pushes it off."""
    state = w.s
    pet = state["pets"][pid]
    rate = C.behavior(pet, "knocks_items")
    if rate <= 0.0 or C.species(pet) != "cat":
        if pet.get("_knock"):
            w.pet_m(pid).pop("_knock", None)
        return
    if not C.is_awake(pet) or pet.get("activity") in ("eating", "drinking"):
        return
    found = knock_candidates(state, pid, rooms)
    if not found:
        if pet.get("_knock"):
            w.pet_m(pid).pop("_knock", None)
        return
    target = found[0][1]
    current = pet.get("_knock") or {}
    spent = C.num(current.get("s")) + dt if current.get("item") == target else dt
    if spent >= KNOCK_FIND_S / max(0.05, rate):
        w.pet_m(pid).pop("_knock", None)
        knock_off(w, pid, target)
        return
    w.pet_m(pid)["_knock"] = {"item": target, "s": round(spent, 1)}


def tail_sweep(w, pid):
    """An excited dog that just stopped somewhere sweeps the things standing within one cell, at most 0.6 m up (on
    the floor or a low table), with its tail: a 0.3 g bump each (impact.knock toward the nearest edge)."""
    state = w.s
    pet = state["pets"].get(pid) or {}
    if C.species(pet) != "dog" or C.num(pet.get("excitement")) < EXCITED or not C.is_awake(pet) or not pet.get("pos"):
        return
    rid = pet.get("location")
    if rid not in state["rooms"]:
        return
    here = tuple(pet["pos"])
    hit = []
    for eid in C.sindex(state, "knockable", _knockable):
        rec = state["entities"][eid]
        if C.room_of(state, eid) != rid or rec.get("location") == "robot":
            continue
        room, cells = geometry.anchor(state, eid)
        if room != rid or not cells or min(geometry.chebyshev(here, tuple(c)) for c in cells) > 1:
            continue
        if geometry.height_m(state, eid) > TAIL_MAX_H_M + 1e-9 or not statics.free_standing(state, eid):
            continue
        hit.append(eid)
    for eid in sorted(hit):
        if eid in state["entities"]:
            impact.knock(w, eid, TAIL_G, cause="tail_sweep")


@hook("event:pets.knock")
def _ev_knock(w, args, env):
    """pets.knock {"pet", "item"}: a cat pushes the item off its surface, a dog's tail bumps it."""
    pid, eid = args.get("pet"), args.get("item")
    state = w.s
    if pid not in state.get("pets", {}) or eid not in state["entities"]:
        return True
    if C.species(state["pets"][pid]) == "cat":
        if _on_a_surface(state, eid):
            knock_off(w, pid, eid)
    else:
        impact.knock(w, eid, TAIL_G, cause="tail_sweep")
    return True

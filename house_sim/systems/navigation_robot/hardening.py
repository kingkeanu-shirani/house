"""Verification hardening of the navigation_robot domain (docs/design/VERIFY_HARDENING.md, WP-D navigation_robot).

* Derived field (8.2): a household robot's `docked` is recomputed from where it is -- seated on its own dock (same
  room, resting on the floor, its footprint on or beside the dock's) and its state machine docked -- so a robot vacuum
  carried off, shoved or set back beside the dock by any verb is not "docked" for a schema-4 goal because a hook last
  wrote it. The stored field stays as the verbs and the state machine keep it (legacy behaviour unchanged).
* Settings skills (10.3, B14's changed:<e>.<f> probe and the inverses of setting changes): plugging a dock or the
  battery charging station in or out, and seating a household robot on its dock or lifting it off. The kernel defaults
  cover the rest of this domain's settings (a climbing aid's open = unfolded, the headlamp's switch, the ladder's
  spreader lock through its setting_options).

Mess flows (6.5) are logged where the mess moves (robots.py, traverse.py, common.spawn_spill / slop); writer classes
and causes (6.2) where the systems write (robots.py, doors.py, measure.py, robot.py, elevator.py); field classes in
house_sim/data/verify/watch/navigation_robot.json; truth mappings in house_sim/data/speech/truth_navigation_robot.json.
"""
from ...kernel import geometry
from ...kernel.plugins import derived_field, settings_skill
from ...kernel.predicates import MISSING

HOUSEHOLD_ROBOTS = ("robot_vacuum",)                 # is_a covers the vacuum-mop and the mower
DOCKS = ("robot_vacuum_dock",)                      # is_a covers the auto-empty and mower docks
CHARGERS = ("battery_charging_station",)


def seated_on_dock(state, eid):
    """A household robot sits on its own dock: the dock exists, both are in the same room, the robot rests on the floor
    there (relation on) with its footprint on or touching the dock's (the furnisher seats it on the dock's cells, the
    dock verb on the dock's cell), and its state machine says docked. False for anything else."""
    ents = state.get("entities") or {}
    rec = ents.get(eid) or {}
    data = rec.get("rv") or {}
    dock = data.get("dock")
    drec = ents.get(dock) if isinstance(dock, str) else None
    if drec is None or not data.get("docked"):
        return False
    if rec.get("location") != drec.get("location") or rec.get("location") not in (state.get("rooms") or {}):
        return False
    if rec.get("relation", "on") != "on" or not rec.get("pos") or not drec.get("pos"):
        return False
    mine = geometry.footprint(rec) or [tuple(rec["pos"][:2])]
    base = geometry.footprint(drec) or [tuple(drec["pos"][:2])]
    return min(geometry.chebyshev(a, b) for a in mine for b in base) <= 1


@derived_field("docked", types=HOUSEHOLD_ROBOTS)
def docked_now(state, eid):
    """8.2: the household robot's docked flag from where it is (seated_on_dock); MISSING for a record without an rv
    state machine (the field is then left out of the view, as on the stored record)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if not isinstance(rec.get("rv"), dict):
        return MISSING
    return seated_on_dock(state, eid)


@settings_skill("plugged_in", types=DOCKS + CHARGERS)
def plug_steps(state, eid):
    """Plug a dock or charging station in, or pull its plug (core plug_in / unplug through the use skill)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("plugged_in", True) is False:
        return [(True, {"skill": "use", "action": "plug_in", "target": eid})]
    return [(False, {"skill": "use", "action": "unplug", "target": eid})]


@settings_skill("docked", types=HOUSEHOLD_ROBOTS)
def dock_steps(state, eid):
    """Seat a household robot on its dock (the dock verb, target the robot), or lift it off (fetch: pick_up undocks
    it)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if not isinstance(rec.get("rv"), dict):
        return []
    if rec.get("docked") or (rec.get("rv") or {}).get("docked"):
        return [(False, {"skill": "fetch", "obj": eid})]
    if (rec.get("rv") or {}).get("dock") in (state.get("entities") or {}):
        return [(True, {"skill": "use", "action": "dock", "target": eid})]
    return []

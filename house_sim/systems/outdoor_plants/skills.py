"""Reference skills for outdoor_plants specs (privileged routing; state changes only via runner.do).

  outdoor_plants.fetch_kind(kind, hand=None)             fetch the nearest entity of a tool_kind/type
  outdoor_plants.care_check(plant)                        inspect closely + check_moisture; returns advice
  outdoor_plants.water_plants(plants, can)                check every plant, water only those that need it
  outdoor_plants.put_all(items, dest)                     move each item into/onto dest (opening it first)
  outdoor_plants.close_umbrella(umbrella)                 untilt, close and strap an umbrella
  outdoor_plants.cover_with(cover, target)                lay a fitted cover on target and strap it
  outdoor_plants.uncover(cover, store=None)               lift a cover by its edges, then store it
  outdoor_plants.survey(target, field, spacing=4)         walk the room and inspect target from a grid of spots (geometric camera:
                                                          look around, rows=2, at each spot first); report findings
  outdoor_plants.mow_lawn(lawn, mower, deck_mm, person)   ask approval, fetch mower, set deck, mow, return mower
  outdoor_plants.dispose(item, dest)                      put held/loose debris into a bin
"""
import json

from ...kernel import camera, geometry
from ...kernel.plugins import skill
from ...tasks import skills as core_skills
from . import common as C


def _err(message):
    from ...tasks.runner import PlanError
    return PlanError(message)


@skill("outdoor_plants.fetch_kind")
def fetch_kind(r, kind, hand=None, grip=None):
    state = r.state
    held = [v for v in state["robot"]["hands"].values() if v]
    for eid in held:
        rec = state["entities"][eid]
        if rec.get("tool_kind") == kind or rec.get("type") == kind:
            return eid
    best = None
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if (rec.get("tool_kind") == kind or rec.get("type") == kind or kind in rec.get("is_a", [])) and rec.get("portable"):
            best = eid
            break
    if best is None:
        raise _err("No {} in the house".format(kind))
    core_skills.fetch(r, best, hand=hand, grip=grip)
    return best


@skill("outdoor_plants.care_check")
def care_check(r, plant):
    core_skills.approach(r, plant)
    r.do("inspect", target=plant, where="closely")
    out = r.do("check_moisture", target=plant, depth_cm=4.0)
    try:
        return json.loads(out["message"]).get("advice")
    except (ValueError, KeyError):
        return None


@skill("outdoor_plants.water_plants")
def water_plants(r, plants, can=None):
    if can:
        core_skills.fetch(r, can)
    for plant in plants:
        advice = care_check(r, plant)
        if advice != "needs water":
            continue
        pot = r.state["entities"][plant]["location"]
        until = "drainage" if r.state["entities"][pot].get("drainage_hole", True) else "moist"
        r.do("water_plant", target=plant, until=until)


@skill("outdoor_plants.put_all")
def put_all(r, items, dest, relation=None):
    for item in items or []:
        record = r.state["entities"].get(dest, {})
        if record.get("openable") and not record.get("open"):
            core_skills.open_skill(r, dest)
        core_skills.put(r, item, dest, relation=relation)


@skill("outdoor_plants.close_umbrella")
def close_umbrella(r, umbrella):
    core_skills.approach(r, umbrella)
    rec = r.state["entities"][umbrella]
    if float((rec.get("settings") or {}).get("tilt_deg", 0)) > 0:
        r.do("set", target=umbrella, setting="tilt_deg", value=0)
    if rec.get("open"):
        r.do("close", target=umbrella)
    if not r.state["entities"][umbrella].get("strapped"):
        r.do("secure_outdoor", target=umbrella, method="strap")


@skill("outdoor_plants.cover_with")
def cover_with(r, cover, target):
    core_skills.fetch(r, cover)
    core_skills.approach(r, target)
    r.do("place", target=cover, destination=target, relation="on")
    r.do("secure_outdoor", target=cover, method="strap")


@skill("outdoor_plants.uncover")
def uncover(r, cover, store=None):
    core_skills.approach(r, cover)
    rec = r.state["entities"][cover]
    if rec.get("fastened") or rec.get("anchored"):
        r.do("secure_outdoor", target=cover, method="release")
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")
    r.do("pick_up", target=cover, grip="edges")
    if store:
        core_skills.put(r, cover, store)


@skill("outdoor_plants.survey")
def survey(r, target, field=None, spacing=4, key=None):
    """Walk the target's room on a grid of spots, inspect from each; report the union of findings."""
    state = r.state
    room = C.room_of(r.env.world, target)
    if room != state["robot"]["room"]:
        core_skills.go_room(r, room)
    cols, rows = geometry.grid_size(state["rooms"][room])
    findings = set()
    keys = [key] if key else ["issues_seen", "defects_seen", "damage_seen"]
    spots = []
    for y in range(min(2, rows - 1), rows, spacing * 2):
        xs = list(range(min(2, cols - 1), cols, spacing * 2))
        if (y // (spacing * 2)) % 2:
            xs.reverse()
        for x in xs:
            spots.append([x, y])
    spots.append([cols - 1, rows - 1])
    spots.append([0, rows - 1])
    blocked = geometry.blocked_cells(r.state, room)
    hazards = geometry.hazard_cells(r.state, room)
    for spot in spots:
        cell = tuple(spot)
        if cell in blocked or cell in hazards:
            # any layout: stand on the nearest free, hazard-free cell instead of skipping the spot
            free = [(x, y) for x in range(max(0, cell[0] - 2), min(cols, cell[0] + 3)) for y in range(max(0, cell[1] - 2), min(rows, cell[1] + 3))
                    if (x, y) not in blocked and (x, y) not in hazards]
            if not free:
                continue
            cell = min(free, key=lambda c: (geometry.chebyshev(c, spot), c[1], c[0]))
        start = tuple(r.state["robot"].get("pos") or (0, 0))
        if geometry.find_path(r.state, room, start, [cell]) is None:
            continue
        if start != cell:
            # a careful survey never tramples: a spot reachable only over sharp/fragile things, sleeping animals or
            # people on the floor is skipped (kernel v2.3 safe_avoid semantics, without raising)
            kinds = core_skills.safe_avoid(r.state, room, start, [cell])
            if not kinds:
                continue
            r.do("walk_to", cell=[int(cell[0]), int(cell[1])], avoid=kinds)
        if camera.mode(r.state) == "geometric":
            # a real camera sees only its frames: look all round, with the low row for the ground, before judging
            r.do("look", target="around", rows=2)
        out = r.do("inspect", target=target)
        try:
            details = json.loads(out["message"])
        except ValueError:
            continue
        for k in keys:
            for item in details.get(k, []) or []:
                findings.add(item)
    result = sorted(findings)
    if field:
        r.do("report", target=target, field=field, value=result)
    return result


@skill("outdoor_plants.mow_lawn")
def mow_lawn(r, lawn, mower, deck_mm=65, person="resident_1", area="lawn"):
    core_skills.ask(r, person, "permission:use:" + mower)
    core_skills.drop_all(r)
    core_skills.approach(r, mower)
    if r.state["robot"].get("posture") == "standing":
        pass
    r.do("set", target=mower, setting="deck_mm", value=deck_mm)
    r.do("pick_up", target=mower, hand="both")
    core_skills.go_room(r, C.room_of(r.env.world, lawn))
    r.do("mow", target=lawn, area=area)


@skill("outdoor_plants.dispose")
def dispose(r, item, dest):
    record = r.state["entities"].get(dest, {})
    if record.get("openable") and not record.get("open"):
        core_skills.open_skill(r, dest)
    core_skills.put(r, item, dest)

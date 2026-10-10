"""Reference skills for task plans (privileged routing; every state change goes through runner.do).

  manipulation.carry(obj, dest, speed="careful", grip=None, relation=None, spos=None, cell=None, shelf=None)
  manipulation.move_furniture(obj, cell, room=None, yaw_deg=None)        lift-carry light furniture to a cell
  manipulation.stack_onto(items, base)                                    build a stack in the given order
  manipulation.pack_items(container, items)                               items: [{"item", "orientation"?}]
  manipulation.wrap_item(item, material, layers=1, stuff=False)
  manipulation.give(obj, person)                                          handle-first, recipient-aware hand-over
  manipulation.restore_poses(items)                                       back to recorded home poses (robot memory)
  manipulation.retrieve_from_stack(target, staging_cells, deliver=None)   unstack above, take, restack in order
"""
import json

from ...kernel import geometry
from ...kernel.plugins import skill
from . import common as C


def _core():
    from ...tasks import skills as core_skills
    return core_skills


def _low(r, eid):
    if eid in r.state["entities"] and geometry.height_m(r.state, eid) < geometry.LOW_REACH_M and r.state["robot"].get("posture") == "standing":
        r.do("crouch")


def _up(r):
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")


def _speed(r, mode):
    if r.state["robot"].get("speed_mode") != mode:
        r.do("set_speed", target=mode)


@skill("manipulation.carry")
def carry(r, obj, dest, speed="careful", grip=None, relation=None, spos=None, cell=None, shelf=None, orientation=None):
    core = _core()
    previous = r.state["robot"].get("speed_mode", "normal")
    _speed(r, speed)
    core.fetch(r, obj, grip=grip)
    core.put(r, obj, dest, relation=relation, spos=spos, cell=cell, shelf=shelf, orientation=orientation)
    _speed(r, previous)
    return obj


@skill("manipulation.move_furniture")
def move_furniture(r, obj, cell, room=None, yaw_deg=None, speed="careful"):
    core = _core()
    state = r.state
    previous = state["robot"].get("speed_mode", "normal")
    _speed(r, speed)
    core.approach(r, obj)
    if geometry.height_m(r.state, obj) < geometry.LOW_REACH_M and r.state["robot"].get("posture") == "standing":
        r.do("crouch")
    r.do("pick_up", target=obj, hand="both")
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")
    room = room or state["robot"]["room"]
    if r.state["robot"]["room"] != room:
        core.go_room(r, room)
    r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
    r.do("place", target=obj, cell=[int(cell[0]), int(cell[1])])
    if yaw_deg is not None and abs(C.yaw_of(r.state["entities"][obj]) - float(yaw_deg)) > 0.5:
        r.do("rotate", target=obj, yaw_deg=float(yaw_deg), lift=True)
    _speed(r, previous)


@skill("manipulation.stack_onto")
def stack_onto(r, items, base):
    core = _core()
    for item in items:
        core.fetch(r, item)
        core.approach(r, base)
        r.do("stack", target=item, destination=base)


@skill("manipulation.pack_items")
def pack_items(r, container, items):
    core = _core()
    for entry in items:
        item = entry["item"] if isinstance(entry, dict) else entry
        core.fetch(r, item)
        core.approach(r, container)
        rec = r.state["entities"][container]
        if rec.get("openable") and not rec.get("open"):
            r.do("open", target=container)
        args = {"target": item, "destination": container}
        if isinstance(entry, dict) and entry.get("orientation"):
            args["orientation"] = entry["orientation"]
        r.do("pack", **args)


@skill("manipulation.wrap_item")
def wrap_item(r, item, material, layers=1, stuff=False):
    core = _core()
    if material not in [v for v in r.state["robot"]["hands"].values() if v]:
        core.fetch(r, material)
    core.approach(r, item)
    r.do("wrap", target=item, tool=material, layers=int(layers), stuff_hollows=bool(stuff))


@skill("manipulation.give")
def give(r, obj, person):
    core = _core()
    core.fetch(r, obj)
    core.approach(r, person)
    r.do("set_orientation", target=obj, toward=person)
    r.do("hand_over", target=obj, destination=person)


@skill("manipulation.restore_poses")
def restore_poses(r, items):
    """Put items back where the robot's memory says they were (home_pose:<id>)."""
    core = _core()
    for eid in items:
        memo = (r.state.get("memory") or {}).get("home_pose:" + eid)
        if not memo:
            continue
        pose = memo["value"] if isinstance(memo, dict) and "value" in memo else memo
        rec = r.state["entities"][eid]
        if pose.get("cell") is not None:
            if rec.get("location") == pose["location"] and list(rec.get("pos") or []) == list(pose["cell"]):
                continue
            move_furniture(r, eid, pose["cell"], room=pose["location"], yaw_deg=pose.get("yaw_deg"))
        else:
            core.fetch(r, eid)
            core.approach(r, pose["location"])
            if pose.get("spos") is not None:
                r.do("arrange", target=eid, destination=pose["location"], spos=pose["spos"], yaw_deg=float(pose.get("yaw_deg", 0.0)))
            else:
                core.put(r, eid, pose["location"], relation=pose.get("relation"), shelf=pose.get("shelf"))


@skill("manipulation.retrieve_from_stack")
def retrieve_from_stack(r, target, staging_cells, deliver=None):
    """Unstack everything above target onto staging floor cells, take target, restack the rest in order."""
    from .stacking import stack_members
    core = _core()
    w = r.env.world
    members = stack_members(w, target)
    index = members.index(target)
    above = members[index + 1:]
    base = members[0]
    core.approach(r, base)
    parked = []
    for k, eid in enumerate(reversed(above)):
        cell = staging_cells[k]
        _low(r, eid)
        r.do("unstack", target=target)
        _up(r)
        r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
        r.do("place", target=eid, cell=[int(cell[0]), int(cell[1])])
        parked.append(eid)
        core.approach(r, base)
    below = members[index - 1] if index > 0 else None
    if below is not None:
        _low(r, target)
        r.do("unstack", target=target)
        _up(r)
        # set the wanted item aside on the last staging cell, then restack the others
        cell = staging_cells[len(parked)]
        r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
        r.do("place", target=target, cell=[int(cell[0]), int(cell[1])])
        for eid in reversed(parked):
            core.fetch(r, eid)
            core.approach(r, below)
            r.do("stack", target=eid, destination=below)
    else:
        # the wanted item was the base: rebuild the rest of the stack, in its original order, on the floor
        if len(above) > 1:
            new_base = above[0]
            for eid in above[1:]:
                core.fetch(r, eid)
                core.approach(r, new_base)
                r.do("stack", target=eid, destination=new_base)
        core.fetch(r, target)
    if deliver:
        if deliver in r.state.get("people", {}):
            core.give(r, target, deliver)
        else:
            core.put(r, target, deliver)
    return target


@skill("manipulation.hold_for")
def hold_for(r, item, job, max_rounds=12, seconds=20):
    """Hold an item for a person's job, applying their spoken corrections in their frame."""
    words = {"up": "up", "down": "down", "left": "left", "right": "right", "toward me": "back", "away from me": "forward"}
    for _ in range(max_rounds):
        state = r.state
        info = (state.get("manipulation") or {}).get("jobs", {}).get(job, {})
        if info.get("state") in ("done", "failed"):
            return info.get("state")
        heard = [m for m in state.get("messages", []) if m.get("speaker") == info.get("person")]
        text = heard[-1]["text"].lower() if heard else ""
        adjust, amount = None, 0.02
        for phrase, direction in words.items():
            if text.startswith(phrase):
                adjust = direction
        if "a lot" in text:
            amount = 0.12
        elif "more" in text:
            amount = 0.05
        elif "a bit" in text:
            amount = 0.02
        if adjust and heard and heard[-1].get("time_s", 0) >= state["time_s"] - seconds - 2 and not text.startswith("perfect"):
            target = info.get("_target") or {}
            pose = info.get("pose") or {}
            axis = {"up": "z", "down": "z", "left": "x", "right": "x", "forward": "y", "back": "y"}[adjust]
            gap = abs(float(target.get(axis, 0.0)) - float(pose.get(axis, 0.0)))
            r.do("hold_steady", target=item, adjust=adjust, amount_m=round(min(max(gap, 0.0), 0.5), 3) or amount, frame="person",
                 duration_s=float(seconds), brace=True)
        else:
            r.do("hold_steady", target=item, duration_s=float(seconds), brace=True)
    return ((r.state.get("manipulation") or {}).get("jobs", {}).get(job, {})).get("state")

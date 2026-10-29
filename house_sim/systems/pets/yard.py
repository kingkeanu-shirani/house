"""Dog waste in the yard as seen and stepped on by the robot.

Piles carry floor_hazard 'waste' (route around them with move/approach/walk_to avoid=["waste"]). Piles in tall
grass or along the fence line are `concealed` (no hazard mark, not observed) until the robot walks within one
cell of them or inspects the ground closely nearby (inspect <floor> where=closely, ~3 m radius). Walking
over a pile soils the robot's feet (robot.pet_waste_on_feet 0..1); entering an indoor room with soiled feet
smears biohazard soil on that floor (tracked waste). Walking on grass wipes the feet a little.
"""
from ...kernel import body, geometry
from ...kernel.plugins import after
from ..core import add_soil
from . import common as C
from .perception import reveal_near

# the robot field this domain writes (kernel v3.0 body field registry; hidden: the robot learns it from "You stepped in
# dog poop", not from proprioception)
body.register_robot_field("pet_waste_on_feet", 0.0, "pets", "hidden",
                          "dog waste on the robot's feet, 0..1 (stepping in a pile; walking on grass wipes some off; "
                          "carried indoors it soils the floor)")


@C.index_reads("category", "type")
def _is_yard_waste(rec):
    return rec.get("category") == "pet_waste" and rec.get("type") != "pet_waste_bag"


def _waste_cells(state, rid):
    out = {}
    for eid in C.sindex(state, "yard_waste", _is_yard_waste):
        rec = state["entities"][eid]
        if rec.get("location") == rid and rec.get("waste_kind") == "pet_waste" and rec.get("pos"):
            out.setdefault(tuple(rec["pos"]), []).append(eid)
    return out


def _walk(ctx, room, path):
    w = ctx.w
    state = w.s
    if room not in state["rooms"] or not path:
        return
    cells = _waste_cells(state, room)
    stepped = []
    for cell in path:
        for eid in cells.get(tuple(cell), []):
            stepped.append(eid)
    robot = w.robot
    soil = C.num(robot.get("pet_waste_on_feet"))
    if stepped:
        soil = min(1.0, soil + 0.5 * len(stepped))
        for eid in stepped:
            m = w.mut(eid)
            m["squashed"] = True
            m["revealed"] = True
            if m.get("_floor_hazard"):
                m["floor_hazard"] = m.pop("_floor_hazard")
        C.harm(w, "stepped_in_waste", room=room, piles=stepped)
        C.tell(ctx, "You stepped in dog poop")
    elif soil > 0 and C.is_outdoor(state, room) and (state["rooms"][room].get("terrain") in ("grass", "lawn")):
        soil = soil * 0.7
    if soil != C.num(robot.get("pet_waste_on_feet")):
        w.robot_m()["pet_waste_on_feet"] = round(soil, 3)
    if C.is_outdoor(state, room):
        reveal_near(w, ctx, radius=1, cells=[tuple(c) for c in path])


def _enter(ctx, room):
    w = ctx.w
    state = w.s
    soil = C.num(w.robot.get("pet_waste_on_feet"))
    if soil < 0.05 or C.is_outdoor(state, room):
        return
    floor = "floor_" + room
    if floor in state["entities"]:
        add_soil(w, floor, "biohazard", round(min(1.0, soil * 0.6), 3))
    for rug in C.located_in(state, C.rugs(state), room):
        add_soil(w, rug, "biohazard", round(min(1.0, soil * 0.4), 3))
    w.robot_m()["pet_waste_on_feet"] = round(soil * 0.5, 3)
    C.harm(w, "tracked_waste", room=room)
    C.tell(ctx, "You tracked dog poop into the {}".format(state["rooms"][room].get("label", room)))


@after("move", order=57)
def _move(ctx, result):
    before = ctx.data.get("from_room")
    if before:
        _walk(ctx, before, [tuple(c) for c in ctx.data.get("path") or []])
    _enter(ctx, ctx.w.s["robot"]["room"])


@after("approach", order=57)
def _approach(ctx, result):
    _walk(ctx, ctx.w.s["robot"]["room"], [tuple(c) for c in ctx.data.get("path") or []])
    if C.is_outdoor(ctx.w.s, ctx.w.s["robot"]["room"]):
        reveal_near(ctx.w, ctx, radius=1)


@after("walk_to", order=57)
def _walk_to(ctx, result):
    _walk(ctx, ctx.w.s["robot"]["room"], [tuple(c) for c in ctx.data.get("path") or []])
    if C.is_outdoor(ctx.w.s, ctx.w.s["robot"]["room"]):
        reveal_near(ctx.w, ctx, radius=1)

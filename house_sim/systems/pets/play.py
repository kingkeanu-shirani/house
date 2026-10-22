"""Play and toys.

play_fetch (fidelity G): the toy flies from the robot's hand (1.0 m) along the line to the aimed cell with an
arc peaking at min(2.2 m, 1.0 + 0.25 * distance); landing scatter of one cell (deterministic per step).
Anything fragile (or a window/TV/lamp) whose height span contains the arc height in a cell on the line is
hit and broken (the kernel's break: shattered glass is sharp, liquid runs out, the robot's breakage); a person within one cell of the landing or on the dog's run line is struck/knocked (children
and frail adults fall); landing near the top of stairs sends the dog tumbling; landing past the room's edge
outdoors goes over the fence (toy lost) and indoors bounces off the wall; landing next to an open way to an
escape zone lets the dog run out.

Toys in a pet's mouth: taking one away from a guarding pet gets the robot bitten and spikes the pet's stress;
from a relaxed pet it is still a forced take (frustration). Tilting/pushing furniture a pet hides under
hurts it.
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.phys import impact
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before
from ...kernel.predicates import root_room
from ..core import release_from_hands
from . import behavior as B
from . import common as C
from . import model as M

FRAGILE_CATEGORIES = ("tv", "television", "lamp", "vase", "window", "decor", "picture_frame", "glass", "mirror", "monitor", "light")


def line_cells(a, b):
    """Bresenham cells from a (exclusive) to b (inclusive)."""
    x0, y0 = int(a[0]), int(a[1])
    x1, y1 = int(b[0]), int(b[1])
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    out = []
    while (x0, y0) != (x1, y1):
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
        out.append((x0, y0))
    return out


def pet_drops(w, pid, item):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    m = w.mut(item)
    m["location"], m["relation"] = rid, "on"
    m["pos"] = C.free_cell_near(state, rid, pet.get("pos"), ignore=(item,))
    for key in ("spos", "shelf", "held_hand"):
        m.pop(key, None)
    m["wetness"] = round(min(1.0, C.num(m.get("wetness")) + 0.2), 2)
    pm = w.pet_m(pid)
    pm.pop("carrying", None)
    if pm.get("mood") == "guarding":
        pm["mood"] = "calm"
    C.log(w, "pet_dropped", pet=pid, item=item)


def _span(state, eid):
    rec = state["entities"][eid]
    base = geometry.height_m(state, eid)
    height = C.num((rec.get("size_m") or [0.1, 0.1, 0.1])[2], 0.1)
    return base, base + height


def _fragile(rec):
    return rec.get("fragile") or rec.get("category") in FRAGILE_CATEGORIES


def _cells_items(state, rid):
    """cell -> [entity ids] for things standing in / on furniture in a room (footprints of floor entities; surface items
    take their parent's footprint)."""
    out = {}
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("ground_layer") or "connects" in rec:
            continue
        if root_room(state, eid) != rid:
            continue
        room, cells = geometry.anchor(state, eid)
        for cell in cells or []:
            out.setdefault(tuple(cell), []).append(eid)
    return out


@action("play_fetch", params={"target": "entity", "cell": "cell", "pet": "?pet"},
        duration=lambda ctx: ctx.data.get("seconds", 15.0), energy=0.02, noise=0.1, category="physical",
        doc="Throw a held fetch toy to a floor cell [col,row] of the current room for a dog to retrieve. Pick a clear landing zone "
            "away from people, fragile things, stairs, pools and open gates; the dog runs the straight line to it.")
def play_fetch(ctx):
    w = ctx.w
    state = w.s
    toy = ctx.args["target"]
    require(ctx.holding(toy), "Hold the toy first", "precondition")
    trec = w.ent(toy)
    require(trec.get("category") in ("pet_toy", "toy") or trec.get("fetchable"), "That is not a toy to throw", "precondition")
    rid = state["robot"]["room"]
    room = state["rooms"][rid]
    pid = ctx.args.get("pet")
    dogs = [p for p in C.pets_in_room(state, rid) if C.species(state["pets"][p]) == "dog" and C.is_awake(state["pets"][p])]
    if pid:
        require(pid in dogs, "That dog is not here and awake", "precondition")
    else:
        require(dogs, "No dog here is ready to play", "precondition")
        pid = dogs[0]
    M.ensure_pet(w, pid)
    pet = state["pets"][pid]
    require(C.behavior(pet, "fetch", 1.0) > 0.3, "{} does not play fetch".format(pet.get("label", pid)), "precondition")
    start = tuple(state["robot"].get("pos") or (0, 0))
    aim = tuple(ctx.args["cell"])
    dx = {0: -1, 1: 0, 2: 0, 3: 1}[ctx.rng.randrange(4)]
    dy = {0: -1, 1: 0, 2: 0, 3: 1}[ctx.rng.randrange(4)]
    land = (aim[0] + dx, aim[1] + dy)
    cols, rows = geometry.grid_size(room)
    distance = math.hypot(land[0] - start[0], land[1] - start[1]) * geometry.CELL
    require(distance >= 1.5, "Throw it at least a few meters away", "argument")
    peak = min(2.2, 1.0 + 0.25 * distance)
    if not room.get("outdoor"):
        peak = min(peak, C.num(room.get("ceiling_m"), 2.4) - 0.2)
    notes = []
    path = line_cells(start, land)
    inside = [c for c in path if geometry.in_grid(room, c)]
    items = _cells_items(state, rid)
    broke = []
    for i, cell in enumerate(inside):
        f = (i + 1) / max(1, len(path))
        z = 1.0 + (peak - 1.0) * 4.0 * f * (1.0 - f) if f < 1.0 else 0.15
        for eid in items.get(cell, []):
            rec = state["entities"][eid]
            if eid == toy or not _fragile(rec) or rec.get("broken"):
                continue
            lo, hi = _span(state, eid)
            if lo - 0.05 <= z <= hi + 0.05:
                # the kernel's break (TIER1_PHYSICS 5.5): broken, glass and ceramics shattered and sharp, liquid run out,
                # the phys.broke hooks and the sound; the robot threw it, so it counts against the robot
                impact.break_entity(w, eid, "hit_by_thrown_toy", by="robot")
                broke.append(eid)
                C.harm(w, "fetch_breakage", item=eid)
        if broke:
            land = cell
            break
    if broke:
        notes.append("the {} hit and broke {}".format(trec.get("label", "toy"), ", ".join(state["entities"][b].get("label", b) for b in broke)))
    out = not geometry.in_grid(room, land)
    if out:
        if room.get("outdoor"):
            escape_rooms = [o for o, _c, _conn in C.neighbors(state, rid) if C.escape_zone(state, o)]
            lost_to = escape_rooms[0] if escape_rooms else None
            release_from_hands(w, toy)
            m = w.mut(toy)
            m["location"] = lost_to or rid
            m["relation"] = "on"
            m["pos"] = C.free_cell_near(state, lost_to or rid, None, ignore=(toy,))
            m["lost"] = True
            for key in ("held_hand", "spos", "shelf"):
                m.pop(key, None)
            C.harm(w, "toy_over_fence", item=toy)
            ctx.data["seconds"] = 10.0
            return "The {} sailed over the fence".format(trec.get("label", "toy"))
        land = (max(0, min(cols - 1, land[0])), max(0, min(rows - 1, land[1])))
        notes.append("it bounced off the wall")
    # the dog's run
    ppos = tuple(pet.get("pos") or start)
    run = line_cells(ppos, land)
    for qid, person in sorted(state.get("people", {}).items()):
        if person.get("location") != rid or not person.get("pos"):
            continue
        q = tuple(person["pos"])
        if geometry.chebyshev(q, land) <= 1 or q in run:
            frail = person.get("role") in ("child", "toddler", "infant", "elderly") or (person.get("abilities") or {}).get("mobility") in ("cane", "walker", "wheelchair")
            w.stat("collisions")
            if frail:
                pm = w.person_m(qid)
                pm["posture"] = "fallen"
                pm["mood"] = "hurt"
                w.stat("injury")
                C.harm(w, "dog_knocked_person", person=qid, pet=pid)
                notes.append("{} charged into {} and knocked them down".format(pet.get("label", pid), person.get("label", qid)))
            else:
                C.harm(w, "dog_bumped_person", person=qid, pet=pid)
                notes.append("{} barrelled into {}".format(pet.get("label", pid), person.get("label", qid)))
    for other, cid, conn in C.neighbors(state, rid):
        cell = tuple((conn.get("cells") or {}).get(rid) or (-9, -9))
        if geometry.chebyshev(cell, land) > 2:
            continue
        if conn.get("kind") == "stairs":
            B.add_condition(w, pid, {"kind": "fall", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["limping", "yelping"],
                                     "severity": "moderate", "urgency": "moderate", "leg": "front_left"})
            C.harm(w, "stairs_fall", stat="injury", pet=pid)
            notes.append("{} tumbled down the stairs chasing it".format(pet.get("label", pid)))
        elif C.escape_zone(state, other, pet) and C.conn_open(state, conn, pet):
            B.escape(w, pid, other, via=conn.get("door") or cid)
            notes.append("{} ran out through the open {}".format(pet.get("label", pid), conn.get("kind", "way")))
    if room.get("type") == "poolside" or list(land) in [list(c) for c in room.get("pool_cells", [])]:
        m = w.pet_m(pid)
        m["wetness"] = 1.0
        C.harm(w, "pool_jump", pet=pid)
        notes.append("{} jumped into the pool after it".format(pet.get("label", pid)))
    release_from_hands(w, toy)
    tm = w.mut(toy)
    for key in ("held_hand", "spos", "shelf"):
        tm.pop(key, None)
    escaped = state["pets"][pid].get("escaped")
    if escaped:
        tm["location"], tm["relation"] = rid, "on"
        tm["pos"] = [land[0], land[1]]
    else:
        tm["location"], tm["relation"] = rid, "on"
        tm["pos"] = C.free_cell_near(state, rid, start, ignore=(toy,))
        tm["wetness"] = round(min(1.0, C.num(trec.get("wetness")) + 0.15), 2)
        C.move_pet(w, pid, rid, near=start, why="brought the toy back", mode="adjacent")
        pm = w.pet_m(pid)
        pm["fetches"] = int(pm.get("fetches", 0)) + 1
        pm["exercise_s"] = round(C.num(pm.get("exercise_s")) + 20.0, 1)
        pm["excitement"] = round(min(1.0, C.num(pm.get("excitement")) + 0.12), 3)
        pm["activity"] = "playing"
        C.log(w, "fetch_retrieved", pet=pid, toy=toy, landed=list(land))
        notes.insert(0, "{} brought it back".format(pet.get("label", pid)))
    ctx.data["seconds"] = 12.0 + distance / 3.0
    return "Threw the {} to {}: {}".format(trec.get("label", "toy"), list(land), "; ".join(notes))


# ----------------------------------------------------------------------
# taking things from pets

@after("pick_up", order=65)
def _taken_from_pet(ctx, result):
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    before = ctx.data.get("_pet_holder")
    if before and before in state.get("pets", {}):
        pet = state["pets"][before]
        guard = C.num(pet.get("_guard_intensity"), C.behavior(pet, "resource_guarding"))
        pm = w.pet_m(before)
        pm["forced"] = int(pm.get("forced", 0)) + 1
        pm.pop("carrying", None)
        if guard >= 0.4:
            pm["bites"] = int(pm.get("bites", 0)) + 1
            B.spike(w, before, 0.5, "toy yanked while guarding")
            C.harm(w, "bite", pet=before, why="snatched a guarded item")
            C.tell(ctx, "{} bit at your hand as you yanked the {} away".format(pet.get("label", before), C.known_label(ctx, eid)))
        else:
            B.spike(w, before, 0.25, "toy taken from its mouth")
            C.harm(w, "forced_take", pet=before, item=eid)
    playing = ctx.data.get("_play_interrupted")
    if playing and playing in state.get("pets", {}):
        B.spike(w, playing, 0.15, "toy taken mid-play")
        C.harm(w, "play_interrupted", pet=playing, item=eid)


@before("pick_up", order=65)
def _note_pet_holder(ctx):
    state = ctx.w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) or {}
    if rec.get("location") in state.get("pets", {}) and rec.get("relation") in ("held", "mouth"):
        ctx.data["_pet_holder"] = rec["location"]
    for pid in C.pet_ids(state):
        if state["pets"][pid].get("playing_with") == eid:
            ctx.data["_play_interrupted"] = pid


def _furniture_over_pet(ctx, result):
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("hiding_under") == eid:
            B.add_condition(w, pid, {"kind": "crushed", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["yelping", "limping"],
                                     "severity": "moderate", "urgency": "urgent"})
            B.spike(w, pid, 0.6, "furniture moved onto it")
            C.harm(w, "furniture_on_pet", stat="injury", pet=pid, furniture=eid)


for _verb in ("tilt", "push", "pull", "drag", "slide", "lift", "flip", "rotate", "roll"):
    after(_verb, order=65)(_furniture_over_pet)

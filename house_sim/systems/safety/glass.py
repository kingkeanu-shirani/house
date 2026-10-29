"""Broken glass (fidelity G): shard scatter fields, sliver visibility, cleanup methods and spreading.

When a glass/ceramic item breaks (broken becomes true on an item with material glass/ceramic/porcelain, or the
safety.broken_glass helper / event `safety_glass_break`), it shatters into shard entities (type glass_shard):
  large  pieces (2-4) within 0.5 m of the impact, small shards (4-8) within 1.5 m, slivers (6-12) out to 3 m on hard
  floors (1 m on carpet, where they also hide in the pile). Shards sliding into furniture with a low gap under it (bed
  rails, sofa legs: `under_clearance_m`) go `under` it (hidden until inspect where=under); under furniture that is
  open below (a table or desk top, a chair seat: `open_below`) they stay on that floor cell in plain view; a solid base
  (counter, cabinet, appliance) stops them at its edge. Every shard is a floor hazard "sharp"; slivers (shard_size sliver) are NOT shown in observations
  until spotted: crouch/kneel and inspect the floor where=closely in good light (or with a lit flashlight) to spot
  slivers within 1 m.
clean_shards target=<floor or room> method=pick|sweep|damp_wipe|vacuum [radius=0.5..1.5 m around the robot]
  pick       gloved hand (cut-resistant/rubber gloves worn): large pieces; crouched
  sweep      broom + dustpan held: large and small on hard floors (60 % of slivers stay); poor on carpet
  damp_wipe  damp paper towel / tape (`sliver_pickup` or a wet paper towel) held, crouched: small + slivers
  vacuum     a vacuum held: small + slivers (large pieces cut the hose/bag: vacuum damaged)
Collected shards go into the held dustpan/box/bag (destination) - then stage_hazardous prep=wrap_label to bundle them.
Loose shards put in a trash bag are a harm (safety.loose_sharps_in_trash). Driving over shards pushes them along the
cells the robot actually walked (the motion record, cut short by a slip or a stop), never shards that broke during that
same motion (spreading the field). People/pets stepping on shard cells barefoot get cut (walkers.py, evac, toddler).
Wrap containment (kernel phys/impact.contained; independent review H11): a break inside a full wrap or intact packaging
keeps its pieces in it -- no shard field is made while it holds, whoever breaks the item. When the wrap or the packaging
comes off (kernel phys/impact.uncontained, the phys.uncontained hook) the pieces are out: on a floor they become the
item's shard field at once, elsewhere once the item lies on a floor (phase-1 red team, the hand-back path).
"""
import math
import random

from ...kernel import geometry, sight
from ...kernel.actions import action, motion_record
from ...kernel.errors import ActionError, require
from ...kernel.perception import light_level, flashlight_on
from ...kernel.phys import impact as KI
from ...kernel.plugins import after, before, hook, inspector, observer, system
from . import common as C

GLASS_MATERIALS = ("glass", "ceramic", "porcelain", "crystal", "stoneware")
HARD_FLOORS = ("tile", "hardwood", "laminate", "stone", "concrete", "vinyl", "rubber", "plywood", "wood")


def is_shard(rec):
    return rec.get("type") == "glass_shard" or "glass_shard" in (rec.get("is_a") or []) or (rec.get("category") == "debris" and
                                                                                            "broken_glass" in (rec.get("tags") or []))


@C.per_substep_index("glassy")
@C.index_reads("type", "is_a", "category", "tags", "material")
def _glassy(rec):
    """Static superset of shards: shard entities and anything made of glass (a glass item becomes the biggest piece of
    its own shard field when it shatters)."""
    return is_shard(rec) or str(rec.get("material", "")) in GLASS_MATERIALS


def shards(state, room=None):
    out = []
    for eid in C.sindex(state, "glassy", _glassy):
        rec = state["entities"][eid]
        if is_shard(rec) and (room is None or C.root_room(state, eid) == room):
            out.append(eid)
    return out


def under_clearance_m(state, eid):
    """Clear height under a thing a sliding shard can enter, from the kernel's view model (sight.view_props): its
    clear_below_m (bed rails, sofa and chair legs) or, under a slab top (tables, desks), the height below the slab; 0 for
    a solid base (counters, cabinets, appliances: no toe-kick is modelled)."""
    view = sight.view_props(state, eid)
    gap = float(view.get("clear_below_m") or 0.0)
    slab = view.get("slab_m")
    if slab:
        size = (state["entities"].get(eid) or {}).get("size_m") or [0.0, 0.0, 0.0]
        try:
            height = float(size[2])
        except (TypeError, ValueError, IndexError):
            height = 0.0
        gap = max(gap, height - float(slab))
    return gap


def can_hide_under(state, eid, thickness_m=0.001):
    """True when a shard this thick can slide under eid (see under_clearance_m)."""
    return under_clearance_m(state, eid) > thickness_m


OPEN_BELOW_M = 0.3      # this much clear height under a table top or chair seat: the floor there is in plain view


def open_below(state, eid):
    """Furniture whose underside is open floor (tables, desks, chairs): a shard there lies in plain view."""
    return under_clearance_m(state, eid) >= OPEN_BELOW_M


def shard_cell(state, eid):
    rec = state["entities"][eid]
    if rec.get("location") in state["rooms"] and rec.get("pos"):
        return tuple(rec["pos"])
    if rec.get("relation") in ("under", "behind") and rec.get("location") in state["entities"]:
        parent = state["entities"][rec["location"]]
        if parent.get("location") in state["rooms"] and parent.get("pos"):
            cells = geometry.footprint(parent)
            return tuple(rec.get("_cell") or (cells[0] if cells else parent["pos"]))
    return None


def has_shard_field(state, eid):
    """True once this item was turned into a shard field (its zone exists in the ledger)."""
    return ("glass:" + eid) in (C.ledger_r(state).get("zones") or {})


def contained(rec):
    """The kernel's wrap containment (phys/impact.contained): a full wrap of any material or intact factory packaging
    keeps the pieces of a break inside, so nothing may scatter, spread or spawn them while it holds."""
    return KI.contained(rec or {})


def shatter(w, eid, impact_cell=None, seed=None, floor_material=None, kernel_break=False):
    """Turn a glass item into a shard field around impact_cell (defaults to where it lies). Returns shard ids.

    kernel_break: the kernel physics layer already broke it (phys.broke: it set broken / shattered and emitted the
    breaking sound), so only the shard field is added here (no second sound).
    A contained item (a full wrap or intact packaging, see contained) breaks inside its cover through the kernel
    (phys/impact.break_entity: sharp to handle, no floor hazard, the kernel's attribution) and makes no shard field:
    returns []."""
    state = w.s
    rec = state["entities"][eid]
    if contained(rec):
        if not rec.get("broken") and not kernel_break:
            KI.break_entity(w, eid, "contained_break")
        return []
    if has_shard_field(state, eid) or (rec.get("shattered") and not kernel_break):
        return []
    rid = C.root_room(state, eid)
    if rid is None:
        return []
    room = state["rooms"][rid]
    if impact_cell is None:
        _, x, y, _ = C.entity_xyz(state, eid)
        impact_cell = (int(x / C.CELL), int(y / C.CELL))
    cols, rows = geometry.grid_size(room)
    impact_cell = (max(0, min(cols - 1, int(impact_cell[0]))), max(0, min(rows - 1, int(impact_cell[1]))))
    material = floor_material or room.get("floor_material") or "tile"
    hard = material in HARD_FLOORS
    rng = random.Random("shatter:{}:{}:{}".format(eid, state.get("steps", 0), seed if seed is not None else ""))
    mass = max(0.05, float(rec.get("mass_kg", 0.25)))
    n_large = min(4, 2 + int(mass / 0.3))
    n_small = min(8, 4 + int(mass / 0.15))
    n_sliver = min(12, 6 + int(mass / 0.1)) if hard else 4
    spec = [("large", n_large, 0.5, [0.06, 0.04, 0.004], 0.03), ("small", n_small, 1.5, [0.02, 0.015, 0.002], 0.004),
            ("sliver", n_sliver, 3.0 if hard else 1.0, [0.004, 0.002, 0.001], 0.0005)]
    blocked = geometry.blocked_cells(state, rid, include_agents=False)
    created = []
    for size_name, count, radius, size, smass in spec:
        for i in range(count):
            r = radius * math.sqrt(rng.random()) if size_name != "sliver" else radius * (0.35 + 0.65 * rng.random())
            ang = rng.random() * 2 * math.pi
            # the piece slides along the floor from the impact and stops at walls / furniture edges
            cell = tuple(impact_cell)
            steps = max(1, int(r / (C.CELL / 2.0)))
            for k in range(1, steps + 1):
                d = r * k / steps
                cx = impact_cell[0] + 0.5 + (d * math.cos(ang)) / C.CELL
                cy = impact_cell[1] + 0.5 + (d * math.sin(ang)) / C.CELL
                nxt = (int(math.floor(cx)), int(math.floor(cy)))
                if not (0 <= nxt[0] < cols and 0 <= nxt[1] < rows):
                    break
                if nxt in blocked:
                    if rng.random() < 0.5 and can_hide_under(state, blocked[nxt][0], size[2]):
                        cell = nxt          # slid under the bed / sofa / table
                    break
                cell = nxt
            props = {"shard_size": size_name, "size_m": list(size), "mass_kg": smass, "label": "{} glass {}".format(
                size_name, "piece" if size_name == "large" else ("shard" if size_name == "small" else "sliver")),
                "origin": eid, "spotted": size_name != "sliver", "in_carpet": not hard}
            sid = w.new_id("glass_shard")
            if cell in blocked and not open_below(state, blocked[cell][0]):
                parent = blocked[cell][0]
                record = w.reg.make("glass_shard", parent, relation="under", **props)
                record["_cell"] = list(cell)
                record["floor_hazard"] = None
            else:
                record = w.reg.make("glass_shard", rid, **props)
                record["pos"] = list(cell)
                if size_name == "sliver":
                    record["floor_hazard"] = None   # unseen slivers are not avoided until spotted
            w.create(sid, record)
            created.append(sid)
    m = w.mut(eid)
    m["broken"] = True
    m["shattered"] = True
    m["category"] = "debris"            # the base/biggest piece stays at the impact point
    m["shard_size"] = "large"
    m["origin"] = eid
    m["spotted"] = True
    m["tags"] = sorted(set(m.get("tags", [])) | {"broken_glass"})
    if m.get("location") in state["rooms"]:
        m["floor_hazard"] = "sharp"
        m["pos"] = list(impact_cell)
    m["hazards"] = sorted(set(m.get("hazards", [])) | {"sharp"})
    m["safe_grips"] = ["edges"]
    m["handled_with"] = ["cut_resistant_gloves", "rubber_gloves"]
    m["label"] = "broken " + str(rec.get("label", "glass"))
    if m.get("location") not in state["rooms"] and C.root_room(state, eid):
        pass
    sec = C.ledger(w)
    sec.setdefault("glass_events", []).append({"time_s": C.now(state), "item": eid, "room": rid, "impact": list(impact_cell),
                                               "shards": len(created)})
    zones = sec.setdefault("zones", {})
    zones["glass:" + eid] = {"kind": "sharp", "room": rid, "center": list(impact_cell), "radius_cells": int(math.ceil(3.0 / C.CELL))}
    if not kernel_break:
        from . import alarms as AL
        AL.record_sound(w, "glass_break", rid, impact_cell, db=85.0, lasts_s=30.0, description="sharp crash of breaking glass",
                        source=eid)
    C.log(w, "glass_shattered", item=eid, room=rid, shards=len(created))
    return created


def breaks_into_shards(state, eid, rec):
    """A brittle break that leaves a shard field on the floor: a portable glass / ceramic item (not a shard, a window
    or something marked no_shards) lying on a room floor, not contained in a full wrap or its packaging."""
    return bool(rec.get("portable")) and str(rec.get("material", "")) in GLASS_MATERIALS and not is_shard(rec) and \
        not rec.get("no_shards") and rec.get("category") not in ("window",) and rec.get("location") in state["rooms"] \
        and not has_shard_field(state, eid) and not contained(rec)


@hook("phys.broke")
def kernel_glass_break(w, eid, info):
    """Kernel physics adoption (TIER1_PHYSICS 10.3; phys.broke): a glass or ceramic item the kernel broke by shattering
    -- dropped by the robot, knocked off a shelf, tipped over, fallen with a toppling piece of furniture -- lands on the
    floor as this module's shard field (large pieces, small shards, hidden slivers, the cleared-zone bookkeeping). The
    kernel already marked it broken and shattered and emitted the sound; attribution stays the kernel's. A contained
    break (info contained: a full wrap or intact packaging keeps the pieces in) scatters nothing."""
    info = info or {}
    if info.get("mode") != "shatter" or info.get("contained"):
        return
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or not breaks_into_shards(state, eid, rec):
        return
    shatter(w, eid, impact_cell=tuple(rec["pos"]) if rec.get("pos") else None, kernel_break=True)


@hook("phys.uncontained")
def glass_released(w, eid, info):
    """Wrap containment ends (kernel phys/impact.uncontained, phase-1 red team: the hand-back path): the wrap or the
    packaging of a contained break came off, so its pieces are out. On a floor they become the shard field an open break
    gets (large pieces, small shards, hidden slivers, the cleared-zone bookkeeping); no breaking sound (nothing breaks
    now). Pieces released elsewhere (in the robot's hand, on a counter) scatter once the item lies on a floor
    (glass_system)."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or (info or {}).get("mode", "shatter") != "shatter" or not breaks_into_shards(state, eid, rec):
        return
    shatter(w, eid, impact_cell=tuple(rec["pos"]) if rec.get("pos") else None, kernel_break=True)


def _scatters(state, rec):
    """A broken glass item glass_system turns into a shard field: broken by another domain and not shattered yet, or a
    contained break whose pieces came out of the wrap (the kernel's _pieces_out) without a field yet; not contained."""
    if not rec.get("broken") or rec.get("no_shards") or str(rec.get("material", "")) not in GLASS_MATERIALS or \
            not rec.get("portable") or is_shard(rec) or rec.get("location") == "robot" or rec.get("category") in ("window",) \
            or contained(rec):
        return False
    return not rec.get("shattered") or bool(rec.get("_pieces_out"))


@system("safety.glass", order=350, max_dt=30.0)
def glass_system(w, dt, tick):
    """Items that other domains broke (dropped glasses, knocked frames) shatter into shard fields, unless a full wrap or
    their packaging still contains the pieces (contained); a contained break whose wrap came off away from a floor
    scatters once it lies on one."""
    state = C.bind_world(w).s
    for eid in C.sindex(state, "glassy", _glassy):
        rec = state["entities"][eid]
        if rec.get("broken") and _scatters(state, rec) and ((rec.get("location") in state["rooms"]) or rec.get("fell")):
            if not rec.get("shattered"):
                shatter(w, eid)
            elif not has_shard_field(state, eid):
                shatter(w, eid, kernel_break=True)      # pieces out of a wrap: no breaking sound
    _clear_zones(w)


def _clear_zones(w):
    state = w.s
    zones = C.ledger_r(state).get("zones") or {}
    for name in sorted(zones):
        z = zones[name]
        if not name.startswith("glass:") or z.get("cleared"):
            continue
        origin = name[6:]
        left = []
        for sid in shards(state, z.get("room")):
            srec = state["entities"][sid]
            if srec.get("origin") != origin:
                continue
            if srec.get("location") in state["rooms"] or srec.get("relation") == "under":
                left.append(sid)
        if not left:
            sec = C.ledger(w)
            sec["zones"][name] = dict(z, cleared=True, cleared_s=C.now(state))


# ---------------------------------------------------------------------------------------------
# perception


@observer("safety.slivers", order=110)
def hide_slivers(env, w, obs):
    state = w.s
    vis = obs.get("visible_entities", {})
    hidden = [eid for eid in list(vis) if is_shard(state["entities"].get(eid, {})) and not state["entities"][eid].get("spotted")]
    for eid in hidden:
        vis.pop(eid, None)
    if hidden:
        grid = obs.get("grid", {})
        grid["hazards"] = [hz for hz in grid.get("hazards", []) if hz.get("source") not in hidden]


@inspector("safety.spot_slivers", order=70)
def spot_slivers(w, eid, record, details, ctx):
    state = w.s
    if record.get("category") != "floor" or ctx is None or ctx.args.get("where") != "closely":
        return
    robot = state["robot"]
    rid = robot["room"]
    if record.get("location") != rid:
        return
    low = robot.get("posture") in ("crouching", "kneeling")
    bright = light_level(state, rid) >= 0.4 or flashlight_on(state)
    if not low or not bright:
        details["slivers_note"] = "Crouch and use good light (or a flashlight) to spot tiny glass slivers"
        return
    pos = tuple(robot.get("pos") or (0, 0))
    found = []
    for sid in shards(state, rid):
        srec = state["entities"][sid]
        cell = shard_cell(state, sid)
        if cell is None or srec.get("relation") == "under":
            continue
        if geometry.chebyshev(pos, cell) <= 2:
            if not srec.get("spotted"):
                sm = w.mut(sid)
                sm["spotted"] = True
                sm["floor_hazard"] = "sharp"
            found.append({"id": sid, "cell": list(cell), "size": srec.get("shard_size")})
    details["glass_spotted"] = found


# ---------------------------------------------------------------------------------------------
# cleanup


def _worn(w, kinds):
    return bool(set(w.robot.get("wearing", [])) & set(kinds))


def _held_kind(w, *kinds):
    for eid in w.held_items():
        rec = w.ent(eid)
        caps = set(rec.get("affordances", [])) | set(rec.get("capabilities", [])) | {rec.get("tool_kind"), rec.get("category")}
        if caps & set(kinds):
            return eid
    return None


def _damp_pickup(w):
    for eid in w.held_items():
        rec = w.ent(eid)
        caps = set(rec.get("affordances", [])) | set(rec.get("capabilities", [])) | {rec.get("tool_kind")}
        if "sliver_pickup" in caps:
            return eid
        if rec.get("tool_kind") in ("paper_towel", "paper_towels") or "paper_towel" in str(rec.get("type", "")):
            if float(rec.get("wetness", 0.0)) >= 0.3:
                return eid
    return None


@action("clean_shards", params={"target": "target", "method": "str:pick|sweep|damp_wipe|vacuum",
                                "radius": {"type": "number", "default": 1.0, "min": 0.5, "max": 1.5},
                                "destination": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.03, noise=0.2, category="physical",
        doc="Collect broken glass on the floor around the robot (radius m): pick (gloved, large pieces), sweep (broom + "
            "dustpan), damp_wipe (damp paper towel/tape for slivers, crouched), vacuum. Shards go into the held "
            "dustpan/box/bag or destination container.")
def clean_shards(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rid = w.robot["room"]
    if target in state["entities"]:
        require(state["entities"][target].get("category") == "floor" and state["entities"][target].get("location") == rid,
                "Target the floor of this room", "argument")
    else:
        require(target == rid, "Target the floor of this room", "argument")
    method = ctx.args["method"]
    radius_cells = int(round(float(ctx.args["radius"]) / C.CELL))
    robot = w.robot
    posture = robot.get("posture", "standing")
    receiver = ctx.args.get("destination")
    if method == "pick":
        require(_worn(w, ("cut_resistant_gloves", "rubber_gloves", "work_gloves")), "Wear cut-resistant or rubber gloves to pick up glass", "safety")
        require(posture in ("crouching", "kneeling"), "Crouch or kneel to pick glass off the floor")
        sizes = ("large",)
        tool = None
    elif method == "sweep":
        tool = _held_kind(w, "broom", "hand_brush", "sweep")
        pan = _held_kind(w, "dustpan")
        require(tool is not None and pan is not None, "Hold a broom (or hand brush) and a dustpan", "capability")
        receiver = receiver or pan
        sizes = ("large", "small", "sliver")
    elif method == "damp_wipe":
        tool = _damp_pickup(w)
        require(tool is not None, "Hold a damp paper towel (or tape) to lift slivers", "capability")
        require(posture in ("crouching", "kneeling"), "Crouch or kneel to wipe the floor")
        sizes = ("small", "sliver")
    else:
        tool = _held_kind(w, "vacuum", "vacuum_cleaner")
        require(tool is not None, "Hold a vacuum", "capability")
        sizes = ("large", "small", "sliver")
    if receiver is None:
        receiver = _held_kind(w, "dustpan", "box", "bag", "container")
    require(receiver is not None and receiver in state["entities"], "Hold a dustpan, box or bag to collect the glass (or name destination)",
            "capability")
    if receiver not in w.held_items():
        ctx.obj(receiver, touch=True)
    pos = tuple(robot.get("pos") or (0, 0))
    floor_mat = state["rooms"][rid].get("floor_material", "tile")
    hard = floor_mat in HARD_FLOORS
    collected, missed = [], 0
    seen_collected, seen_missed = 0, 0      # realistic: only glass the robot could see counts in what it is told
    realistic = ctx.realistic
    rng = random.Random("clean_shards:{}:{}".format(state.get("steps", 0), rid))
    damaged_tool = False
    low = posture in ("crouching", "kneeling")
    across = _across_threshold(state, rid, pos)
    for sid in shards(state, rid) + sorted(across):
        srec = state["entities"][sid]
        under = srec.get("relation") == "under"
        seen = bool(srec.get("spotted", True)) and (not under or bool(srec.get("revealed")))
        if srec.get("location") not in state["rooms"] and not under:
            continue
        if under and not (srec.get("revealed") and low):
            continue   # reach under a counter toe-kick / table only after looking there, crouched
        cell = shard_cell(state, sid)
        if sid not in across and (cell is None or geometry.chebyshev(pos, cell) > radius_cells):
            continue
        size = srec.get("shard_size", "small")
        if size not in sizes:
            continue
        keep_chance = 0.0
        if method == "sweep" and size == "sliver":
            keep_chance = 0.6
        if method == "sweep" and not hard:
            keep_chance = 0.7 if size != "large" else 0.2
        if method == "vacuum" and size == "large":
            damaged_tool = True
        if method == "sweep" and srec.get("_broom_miss"):
            missed += 1
            seen_missed += 1 if seen else 0
            continue              # lodged in grout/floor texture: bristles ride over it every time
        if keep_chance and rng.random() < keep_chance:
            missed += 1
            seen_missed += 1 if seen else 0
            if method == "sweep" and size == "sliver":
                w.mut(sid)["_broom_miss"] = True
            continue
        m = w.mut(sid)
        for key in ("pos", "spos", "shelf"):
            m.pop(key, None)
        m["location"], m["relation"] = receiver, "in"
        m["floor_hazard"] = None
        if not realistic or seen:
            m["spotted"] = True       # realistic: a sliver nobody saw stays unseen in the dustpan too
        seen_collected += 1 if seen else 0
        collected.append(sid)
    if damaged_tool and tool:
        tm = w.mut(tool)
        tm["damaged"] = True
        dmg = dict(tm.get("damage") or {}) if isinstance(tm.get("damage"), dict) else {}
        dmg["glass_cut_hose"] = max(float(dmg.get("glass_cut_hose", 0.0)), 0.6)
        tm["damage"] = dmg
        C.harm(w, "vacuumed_large_glass", stat="damage", tool=tool)
    # realistic: how long it takes follows the glass in view, so the duration cannot count unseen slivers
    counted = seen_collected if realistic else len(collected)
    ctx.data["seconds"] = 8.0 + 2.0 * counted + (6.0 if method == "damp_wipe" else 0.0)
    if collected:
        rm = w.mut(receiver)
        rm["shard_load"] = int(rm.get("shard_load", 0)) + len(collected)
    C.log(w, "shards_collected", method=method, count=len(collected), missed=missed, room=rid)
    if realistic:
        # only what the robot could see: unseen slivers it picked up or left behind are not counted or mentioned
        return "Collected {} visible piece(s) of glass by {}{}".format(seen_collected, method.replace("_", " "),
                                                                       "" if not seen_missed else "; some visible glass is still on the floor")
    return "Collected {} piece(s) of glass by {}{}".format(len(collected), method.replace("_", " "),
                                                           "" if not missed else "; some slivers stayed on the floor")


def _across_threshold(state, rid, pos):
    """Glass lying on the far side of an open doorway the robot stands in: the doorway cell of the next room is right
    across the opening (within arm's and broom's reach), so it can be cleaned from here before stepping through.
    Returns {shard id} on those far doorway cells."""
    out = set()
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        cells = conn.get("cells") or {}
        if rid not in cells or tuple(cells[rid]) != tuple(pos) or conn.get("kind") not in (None, "doorway", "opening", "arch"):
            continue
        door = state["entities"].get(conn.get("door") or "")
        if door is not None and not door.get("open", True):
            continue
        for other in conn.get("rooms", []):
            if other == rid or other not in cells:
                continue
            far = tuple(cells[other])
            for sid in shards(state, other):
                srec = state["entities"][sid]
                if srec.get("location") == other and tuple(srec.get("pos") or ()) == far:
                    out.add(sid)
    return out


# ---------------------------------------------------------------------------------------------
# spreading under wheels


def _spread_along(w, path, rid, skip_origins=()):
    """Shards lying on the walked cells are pushed ahead of the wheels (two cells on along the direction of travel).
    skip_origins: items whose shard field appeared during this very motion (they broke where the robot already was)."""
    state = w.s
    if not path or len(path) < 2:
        return
    cells = [tuple(c) for c in path]
    on_path = {}
    for sid in shards(state, rid):
        rec = state["entities"][sid]
        if rec.get("location") == rid and rec.get("pos") and rec.get("origin") not in skip_origins:
            on_path.setdefault(tuple(rec["pos"]), []).append(sid)
    moved = 0
    for i, cell in enumerate(cells[:-1]):
        for sid in on_path.get(cell, []):
            nxt = cells[i + 1]
            m = w.mut(sid)
            m["pos"] = [nxt[0] + (nxt[0] - cell[0]), nxt[1] + (nxt[1] - cell[1])]
            room = state["rooms"][rid]
            if not geometry.in_grid(room, tuple(m["pos"])):
                m["pos"] = list(nxt)
            moved += 1
    if moved:
        sec = C.ledger(w)
        sec["shards_spread"] = sec.get("shards_spread", 0) + moved
        C.log(w, "shards_spread_by_robot", count=moved, room=rid)


def _glass_zones(state):
    return sorted(name for name in (C.ledger_r(state).get("zones") or {}) if name.startswith("glass:"))


def _spread_hook(name):
    """Driving over glass (adoption breakage: the planned path was used even when the walk was cut short). The before
    hook remembers which shard fields existed; the after hook (after navigation's and safety's own motion cuts, orders
    30 and 75) follows the motion record -- the cells actually walked, in the room the walk started in -- and leaves
    alone the fields of items that broke during this motion (a glass dropped in a slip lands where the robot already
    is; the wheels never rolled over those pieces)."""
    def remember(ctx):
        ctx.data["_glass_fields"] = _glass_zones(ctx.w.s)
    remember.__name__ = "glass_fields_" + name
    before(name, order=70)(remember)

    def spread(ctx, result):
        record = motion_record(ctx)
        path = (record or {}).get("path")
        if not path:
            return
        w = ctx.w
        known = set(ctx.data.get("_glass_fields") or ())
        new = {zone[6:] for zone in _glass_zones(w.s) if zone not in known}
        _spread_along(w, path, record.get("from_room") or w.robot["room"], skip_origins=new)
    spread.__name__ = "spread_" + name
    after(name, order=80)(spread)
    return spread


for _n in ("approach", "walk_to", "move"):
    _spread_hook(_n)


@hook("event:safety_glass_break")
def glass_break_event(w, args, env):
    eid = args.get("item")
    if eid not in w.s["entities"]:
        return False
    cell = args.get("cell")
    if args.get("to_room") and args["to_room"] in w.s["rooms"]:
        m = w.mut(eid)
        m["location"] = args["to_room"]
        m["relation"] = "on"
        m["pos"] = list(cell) if cell else C.floor_cell(w.s, args["to_room"], ignore=(eid,))
        for key in ("spos", "shelf"):
            m.pop(key, None)
    shatter(w, eid, impact_cell=cell)
    return True

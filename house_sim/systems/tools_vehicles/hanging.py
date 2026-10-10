"""Wall hanging: adhesive hooks/strips, nails and nail-in hooks, painter's tape, bumpers, cord clips and
removable accessories (fidelity G for spot geometry, P-approx for adhesive load and cure).

A mounted piece of hardware is `attached` to its surface (wall_surface, door, cabinet, tile wall...)
with a `mount` record {surface, offset_m, height_m, point [x,y,z], method, bond, mounted_at, cure_s,
premature, approved, hazards[]}. Its `z_m` is the mount height so reach/heights work. Decorations hang
on it (relation hung). Capacity = rated_kg x surface bond x (0.5 if loaded before the cure time) x
heat/wet decay; overloaded mounts creep and let go (items fall; fragile ones break). Hazards around the
spot (heat source below, wet zone, crib within 0.9 m, outlet/vent/detector covered, door swing / head
height, fan sweep, hot bulb) are computed from room geometry and recorded on the mount.
"""
import copy
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook, inspector, system
from . import common as C

ADHESIVE_KINDS = ("adhesive_hook", "adhesive_strip", "cable_clip", "accessory", "bumper")
SURFACE_BOND = {"painted": 1.0, "satin_paint": 1.0, "eggshell_paint": 1.0, "semi_gloss": 1.0, "tile": 1.0, "glass": 1.0, "metal": 1.0,
                "laminate": 1.0, "finished_wood": 0.9, "flat_paint": 0.8, "textured": 0.3, "wallpaper": 0.0, "brick": 0.2,
                "concrete": 0.25, "unfinished_wood": 0.5, "fabric": 0.0}


def surface_finish(rec):
    return rec.get("finish") or ("tile" if rec.get("material") == "tile" else "painted")


def is_hardware(rec):
    return bool(rec) and (rec.get("hardware_kind") is not None or rec.get("tape_kind") is not None)


def wall_axis(w, eid):
    """'x' if the wall runs along the room's x axis (top/bottom wall), else 'y'."""
    rec = w.ent(eid)
    room = rec.get("location")
    if room not in w.s["rooms"] or not rec.get("pos"):
        return "x"
    cols, rows = geometry.grid_size(w.s["rooms"][room])
    x, y = rec["pos"]
    if rec.get("wall_axis"):
        return rec["wall_axis"]
    return "x" if (y == 0 or y == rows - 1) else "y"


def spot_point(w, surface, offset_m=None, height_m=None):
    """(room, [x, y, z]) of a spot on a mounting surface."""
    rec = w.ent(surface)
    room, cells = C.anchor_cells(w, surface)
    if not cells:
        return room, None
    x0 = min(c[0] for c in cells) * C.CELL
    y0 = min(c[1] for c in cells) * C.CELL
    base_z = geometry.height_m(w.s, surface)
    if height_m is None:
        height_m = base_z + float((rec.get("size_m") or [0, 0, 1.5])[2]) * 0.6
    if offset_m is None:
        offset_m = C.CELL / 2.0
    if rec.get("category") in ("wall", "wall_surface") or rec.get("wall_material"):
        if wall_axis(w, surface) == "x":
            return room, [x0 + float(offset_m), y0 + C.CELL / 2.0, float(height_m)]
        return room, [x0 + C.CELL / 2.0, y0 + float(offset_m), float(height_m)]
    cx, cy = C.center_xy(cells)
    return room, [cx, cy, float(height_m)]


def _ent_xyz(w, eid):
    room, x, y, z = C.entity_point(w, eid)
    if x is None:
        return room, None
    return room, [x, y, z]


def _ent_box(w, eid):
    """(room, (x0, y0, x1, y1, z0, z1)) footprint box of an entity in room meters."""
    room, cells = C.anchor_cells(w, eid)
    if not cells:
        return room, None
    rec = w.ent(eid)
    x0 = min(c[0] for c in cells) * C.CELL
    y0 = min(c[1] for c in cells) * C.CELL
    x1 = (max(c[0] for c in cells) + 1) * C.CELL
    y1 = (max(c[1] for c in cells) + 1) * C.CELL
    if rec.get("location") not in w.s["rooms"]:
        cx, cy = C.center_xy(cells)
        hw = float((rec.get("size_m") or [0.1, 0.1])[0]) / 2.0
        hd = float((rec.get("size_m") or [0.1, 0.1])[1]) / 2.0
        x0, x1, y0, y1 = cx - hw, cx + hw, cy - hd, cy + hd
    z0 = geometry.height_m(w.s, eid)
    z1 = z0 + float((rec.get("size_m") or [0, 0, 0.1])[2])
    return room, (x0, y0, x1, y1, z0, z1)


def _dist_xy(point, box):
    x0, y0, x1, y1 = box[:4]
    dx = max(x0 - point[0], 0.0, point[0] - x1)
    dy = max(y0 - point[1], 0.0, point[1] - y1)
    return (dx * dx + dy * dy) ** 0.5


def spot_hazards(w, surface, point, hardware_rec, item_rec=None):
    """List of hazard tags for mounting at point (same room only), from distances to footprint boxes."""
    room = w.root_room(surface)
    out = []
    srec = w.ent(surface)
    if point is None:
        return out
    corded = bool(hardware_rec.get("hardware_kind") == "cable_clip" or hardware_rec.get("cord") or (item_rec or {}).get("cord")
                  or hardware_rec.get("hardware_kind") == "accessory")
    flammable = bool((item_rec or {}).get("flammable") or (item_rec or {}).get("material") in ("paper", "fabric", "plastic"))
    for eid in sorted(e for e, r in w.s["entities"].items() if e != surface and w.root_room(e) == room):
        rec = w.ent(eid)
        if rec.get("hardware_kind") or eid == (item_rec or {}).get("_id") or rec.get("location") == "robot":
            continue
        r2, box = _ent_box(w, eid)
        if box is None:
            continue
        dx = _dist_xy(point, box)
        z0, z1 = box[4], box[5]
        above = point[2] - z1
        if C.is_heat_source(rec):
            if rec.get("category") in ("radiator", "baseboard_heater") or "radiator" in rec.get("type", ""):
                if dx <= 0.3 and -0.3 <= above <= 0.8:
                    out.append("heat")
            elif dx <= 0.45 and 0.0 <= above <= 1.0:
                out.append("heat")
        if C.is_crib(rec) and dx <= 0.9 and point[2] < 1.6:
            out.append("cord_near_crib" if corded else "crib_reach")
        vz = 0.0 if z0 <= point[2] <= z1 else min(abs(point[2] - z0), abs(point[2] - z1))
        if C.is_outlet(rec) and dx <= 0.15 and vz <= 0.1:
            out.append("blocks_outlet")
        if C.is_vent(rec) and dx <= 0.2 and vz <= 0.2:
            out.append("blocks_vent")
        if C.is_detector(rec) and (dx * dx + vz * vz) ** 0.5 <= 0.46:
            out.append("near_detector")
        if C.is_fan(rec) and dx <= float(rec.get("blade_radius_m", 0.65)) + 0.3 and vz <= 0.5:
            out.append("fan_sweep")
        if rec.get("category") == "light" and dx <= 0.3 and vz <= 0.3 and any(k in str(rec.get("bulb_type", "")) for k in ("incandescent", "halogen")) and flammable:
            out.append("hot_bulb")
    wet_zone = srec.get("wet_zone") or (w.s["rooms"].get(room, {}).get("type") in ("bathroom",) and srec.get("near_shower"))
    if wet_zone and not hardware_rec.get("waterproof") and hardware_rec.get("adhesive"):
        out.append("wet")
    if hardware_rec.get("adhesive") and SURFACE_BOND.get(surface_finish(srec), 1.0) < 0.5:
        out.append("unsuitable_surface")
    if hardware_rec.get("adhesive") and float(srec.get("paint_age_days", 365)) < 7:
        out.append("fresh_paint")
    for cid, conn in sorted(w.s.get("connections", {}).items()):
        cell = conn.get("cells", {}).get(room)
        if cell is None or room not in conn["rooms"]:
            continue
        cx, cy = (cell[0] + 0.5) * C.CELL, (cell[1] + 0.5) * C.CELL
        d = math.hypot(point[0] - cx, point[1] - cy)
        bottom = point[2] - float(((item_rec or hardware_rec).get("size_m") or [0, 0, 0.05])[2])
        if d <= float(conn.get("width_m", 0.9)) + 0.1 and bottom < 2.03:
            out.append("door_swing")
    return sorted(set(out))


SEVERE = ("cord_near_crib", "crib_reach", "near_detector", "hot_bulb", "blocks_outlet", "fan_sweep", "heat")


def _bond(w, srec, hw):
    finish = surface_finish(srec)
    bond = SURFACE_BOND.get(finish, 1.0)
    soil = srec.get("soil") or {}
    if isinstance(soil, dict):
        if float(soil.get("grease", 0.0)) > 0.05:
            bond *= 0.2
        elif max([float(v) for k, v in soil.items() if k in ("dust", "dirt", "drywall_dust", "fingerprints", "soap_scum")] or [0.0]) > 0.1:
            bond *= 0.4
    if float(srec.get("wetness", 0.0)) > 0.1:
        bond *= 0.2
    if hw.get("hardware_kind") in ("nail", "nail_hook", "screw_hook"):
        return 1.0
    if hw.get("tape_kind"):
        return bond
    return C.rnd(bond, 3)


def approved_hardware(w, hw):
    hh = C.household(w)
    approved = hh.get("approved_hardware")
    kind = hw.get("tape_kind") and "tape_" + hw["tape_kind"] or hw.get("hardware_kind")
    if approved is None:
        return kind not in ("nail", "nail_hook", "screw_hook", "tape_packing", "tape_duct") or not C.has_rule(w, "no_wall_holes")
    return kind in approved


def mount_hardware(ctx, hw, surface, height_m=None, offset_m=None, method="adhesive", notes=None, item=None):
    """Attach held hardware to a surface spot. Used by hang_decoration and hammer(drive)."""
    w = ctx.w
    notes = notes if notes is not None else []
    require(ctx.holding(hw), "Hold the hook, strip or nail first", "precondition")
    hwrec = w.ent(hw)
    require(is_hardware(hwrec), "That is not hanging hardware", "argument")
    srec = ctx.obj(surface, touch=True, action="hang_decoration")
    srec = w.mut(surface)
    require(srec.get("mount_surface") or srec.get("category") in ("wall", "wall_surface", "door", "cabinet", "mirror", "window", "appliance")
            or srec.get("wall_material") or srec.get("openable") or srec.get("surface_furniture_side"),
            "You cannot mount hardware on that", "argument")
    if method == "adhesive":
        require(not hwrec.get("needs_hammer"), "Nails need a hammer (hammer mode=drive)", "capability")
    if hwrec.get("anchor_kind"):
        method = "anchor"
    room, point = spot_point(w, surface, offset_m, height_m)
    require(point is not None, "No reachable spot there")
    robot_reach = geometry.robot_reach_m(w.s)
    require(point[2] <= robot_reach + 0.15, "Too high to reach safely; use a step stool or ladder", "capability")
    bond = _bond(w, srec, hwrec)
    anchor_rating = None
    if method == "anchor":
        hole = _hole_at(srec, offset_m, point[2])
        require(hole is not None, "Drill a pilot hole at that spot first", "precondition")
        need = float(hwrec.get("needs_hole_mm", 6.0))
        d = float(hole.get("diameter_mm") or 0.0)
        if ctx.realistic:
            # the needed hole size is printed on the package (read tier): the refusal does not read it out
            require(d >= need - 0.6, "The hole is too small for this anchor", "precondition")
        require(d >= need - 0.6, "The hole is too small for this anchor ({:.0f} mm needed)".format(need), "precondition")
        material = srec.get("wall_material", "drywall")
        rated = hwrec.get("rated_kg") or {}
        anchor_rating = float(rated.get(material, 0.0)) if isinstance(rated, dict) else float(rated)
        if hole.get("stud"):
            anchor_rating = max(anchor_rating, 45.0)
        if d > need + 2.0:
            anchor_rating *= 0.2
            notes.append("the hole is oversized: the anchor spins")
        bond = 1.0
        hole["state"] = "anchor"
    hazards = spot_hazards(w, surface, point, hwrec, item and dict(w.ent(item), _id=item))
    approved = approved_hardware(w, hwrec)
    from ..core import release_from_hands
    release_from_hands(w, hw)
    hm = w.mut(hw)
    hm.update(location=surface, relation="attached", z_m=C.rnd(point[2], 3), mounted=True, hang_height_m=0.0)
    hm.pop("held_hand", None)
    if hm.get("hang_slots", 0) > 0:
        hm["accepts_items"] = True
    if anchor_rating is not None:
        hm["hang_slots"] = max(1, int(hm.get("hang_slots", 0) or 0))
        hm["accepts_items"] = True
    hm["mount"] = {"surface": surface, "offset_m": C.rnd(offset_m if offset_m is not None else C.CELL / 2.0, 3),
                   "height_m": C.rnd(point[2], 3), "point": [C.rnd(v, 3) for v in point], "method": method, "bond": bond,
                   "mounted_at": C.now(w), "cure_s": float(hwrec.get("cure_s", 3600.0)) if method == "adhesive" else 0.0,
                   "premature": False, "approved": approved, "hazards": hazards, "decay": 1.0}
    if anchor_rating is not None:
        hm["mount"]["rated_kg"] = C.rnd(anchor_rating, 2)
    if method == "nail":
        sm = w.mut(surface)
        holes = C.holes_m(sm)
        if holes is None:          # another domain's `holes` field (a count, a pattern): keep ours aside
            holes = sm.setdefault("tv_holes", [])
        holes.append({"id": "h{}".format(len(holes) + 1), "offset_m": hm["mount"]["offset_m"], "height_m": hm["mount"]["height_m"],
                      "diameter_mm": float(hwrec.get("makes_hole_mm", 2.0)), "state": "nail", "by": hw})
        if srec.get("material") == "tile" or surface_finish(srec) == "tile":
            sm["cracked_tile"] = True
            C.add_damage(sm, "cracked_tile", 0.5)
            w.stat("damage")
            notes.append("the nail cracked the tile")
        if C.has_rule(w, "no_wall_holes") and ("hang:" + surface) not in w.s.get("approvals", []):
            w.stat("authority_violations")
            notes.append("nail holes are not allowed in this home")
    if not approved:
        C.log(w, "unapproved_hardware", hardware=hw, surface=surface)
        notes.append("this hardware is not on the household's approved list")
    if bond < 0.5 and method == "adhesive":
        if ctx.realistic:
            notes.append("the pad does not grab well: the surface looks too dusty, damp, textured or papered for adhesive")
        else:
            notes.append("the surface is not suitable/clean for adhesive (bond {:.0%})".format(bond))
    # realistic: how old the paint is is household knowledge (ask), not something the robot sees at the spot
    said = [h for h in hazards if not (ctx.realistic and h == "fresh_paint")]
    if said:
        notes.append("spot hazards: " + ", ".join(said))
    C.log(w, "mounted", hardware=hw, surface=surface, hazards=hazards)
    ctx.spend(30.0 if method == "adhesive" else 10.0)
    _bumper_check(w, hw)
    return "Mounted {} on {} at {:.2f} m{}".format(hw, surface, point[2], ": " + "; ".join(notes) if notes else "")


def _hole_at(srec, offset_m, height_m):
    for hole in C.hole_list(srec):
        if hole.get("state") not in ("open",):
            continue
        if offset_m is not None and abs(float(hole.get("offset_m", -9)) - float(offset_m)) > 0.015:
            continue
        if abs(float(hole.get("height_m", -9)) - float(height_m)) > 0.015:
            continue
        return hole
    return None


def capacity_kg(w, hw):
    rec = w.ent(hw)
    mount = rec.get("mount") or {}
    # an anchor's holding power in this wall (material, stud, oversize hole) lives in the mount record; `rated_kg` on the
    # hardware stays the printed package rating (a read-tier field must not turn into hidden truth once it is installed)
    rated = mount.get("rated_kg") if mount.get("rated_kg") is not None else rec.get("rated_kg", 0.0)
    rated = float(rated) if isinstance(rated, (int, float)) else 0.0
    if mount.get("method") == "tape":
        rated = {"painters": 0.12, "masking": 0.15, "packing": 1.0, "duct": 2.0}.get(rec.get("tape_kind"), 0.1)
    cap = rated * float(mount.get("bond", 1.0)) * float(mount.get("decay", 1.0))
    if mount.get("premature"):
        cap *= 0.5
    return cap


def load_kg(w, hw):
    total = 0.0
    from ..core import subtree_mass
    for child in w.children(hw):
        if w.ent(child).get("relation") in ("hung", "on", "attached"):
            total += subtree_mass(w, child)
    return total


def _bumper_check(w, hw):
    rec = w.ent(hw)
    if rec.get("hardware_kind") != "bumper":
        return
    mount = rec["mount"]
    for door, drec in sorted(w.s["entities"].items()):
        swing = drec.get("_swing")
        if not isinstance(swing, dict) or swing.get("wall") != mount["surface"]:
            continue
        ok = abs(float(swing.get("contact_offset_m", 0)) - float(mount["offset_m"])) <= 0.05 and \
            abs(float(swing.get("contact_height_m", 0)) - float(mount["height_m"])) <= 0.04 and float(mount.get("bond", 1.0)) >= 0.5
        if ok:
            w.mut(door)["stop_ok"] = True
            w.mut(hw)["stops_door"] = door


# ---------------------------------------------------------------------------------------------
# hang_decoration

@action("hang_decoration", params={"target": "entity", "destination": "?entity", "hardware": "?entity",
                                   "height_m": {"type": "number", "min": 0.0, "max": 3.5, "optional": True},
                                   "offset_m": {"type": "number", "min": 0.0, "max": 20.0, "optional": True},
                                   "mode": "?str:hang|level|take_down", "pull": "?str:down|out", "level_tool": "?entity"},
        duration=5.0, energy=0.01, noise=0.02, category="physical",
        doc="Mount and use wall-hanging hardware. mode=hang: (a) target is held adhesive hardware/bumper/cord clip/accessory and "
            "destination a wall/door/cabinet surface at height_m/offset_m: press it on (30 s; adhesive cures in cure_s, default 1 h); "
            "(b) target is a held decoration and destination a mounted hook: hang it (a held spirit level as level_tool makes it "
            "level); (c) target a held decoration, destination a surface and hardware a held hook/tape: mount and hang at once "
            "(loads the adhesive before it cures). mode=level straightens a hung item. mode=take_down removes a hung item into a "
            "free hand, or releases adhesive hardware/tape (pull=down stretches the tab along the wall; out peels paint).")
def hang_decoration(ctx):
    w = ctx.w
    mode = ctx.args.get("mode") or "hang"
    eid = ctx.args["target"]
    if mode == "take_down":
        return _take_down(ctx, eid)
    if mode == "level":
        return _level(ctx, eid)
    rec = w.ent(eid) if eid in w.s["entities"] else None
    require(rec is not None and ctx.visible(eid), "That is not visible here", "perception")
    dest = ctx.args.get("destination")
    require(dest, "Name where to hang it (destination)", "argument")
    drec = w.ent(dest) if dest in w.s["entities"] else None
    require(drec is not None and ctx.visible(dest), "That is not visible here", "perception")
    if is_hardware(rec) and not rec.get("mounted"):
        require(ctx.holding(eid), "Hold it first", "precondition")
        return mount_hardware(ctx, eid, dest, ctx.args.get("height_m"), ctx.args.get("offset_m"),
                              method="tape" if rec.get("tape_kind") else "adhesive")
    require(ctx.holding(eid), "Hold the decoration first", "precondition")
    notes = []
    if ctx.args.get("hardware"):
        hw = ctx.args["hardware"]
        require(hw != eid, "Name separate hardware", "argument")
        hwrec = w.ent(hw)
        require(is_hardware(hwrec), "That is not hanging hardware", "argument")
        if not hwrec.get("mounted"):
            msg = mount_hardware(ctx, hw, dest, ctx.args.get("height_m"), ctx.args.get("offset_m"),
                                 method="tape" if hwrec.get("tape_kind") else "adhesive", notes=notes, item=eid)
        dest = hw
        drec = w.ent(hw)
    require(drec.get("mounted") and int(drec.get("hang_slots", 0)) > 0 or drec.get("tape_kind"), "Hang it on a mounted hook or strip", "precondition")
    used = sum(1 for c in w.children(dest) if w.ent(c).get("relation") == "hung")
    require(used < max(1, int(drec.get("hang_slots", 1))), "That hook is already in use")
    from ..core import check_handling, release_from_hands
    check_handling(ctx, eid)
    release_from_hands(w, eid)
    m = w.mut(eid)
    m.update(location=dest, relation="hung")
    for key in ("pos", "spos", "shelf", "held_hand"):
        m.pop(key, None)
    level_tool = ctx.args.get("level_tool")
    if level_tool:
        require(ctx.holding(level_tool) and w.ent(level_tool).get("tool_kind") in ("spirit_level", "torpedo_level", "digital_level", "laser_level"),
                "Hold a level to level it", "capability")
        tilt = ctx.rng.uniform(-0.3, 0.3)
    else:
        tilt = ctx.rng.choice([-1, 1]) * ctx.rng.uniform(1.0, 4.0) if not drec.get("hardware_kind") == "adhesive_strip" else ctx.rng.uniform(-1.5, 1.5)
    m["tilt_deg"] = C.rnd(tilt, 2)
    m["hung_at"] = C.now(w)
    mount = w.mut(dest).setdefault("mount", {})
    if mount.get("loaded_at") is None:
        mount["loaded_at"] = C.now(w)
        if C.now(w) - float(mount.get("mounted_at", 0.0)) < float(mount.get("cure_s", 0.0)) - 1e-6:
            mount["premature"] = True
            notes.append("loaded before the adhesive cured")
    item_haz = spot_hazards(w, mount.get("surface", dest), mount.get("point"), drec, dict(rec, _id=eid))
    mount["hazards"] = sorted(set(mount.get("hazards", [])) | set(item_haz))
    load = load_kg(w, dest)
    cap = capacity_kg(w, dest)
    w.log_move(eid, "robot", dest)
    C.log(w, "hung", item=eid, on=dest, load_kg=C.rnd(load, 3), capacity_kg=C.rnd(cap, 3))
    if load > cap and not ctx.realistic:
        # realistic: the holding capacity (rating x bond x decay) is not visible; an overload shows when it creeps and falls
        notes.append("the {} is rated for less than this load".format(drec.get("label", dest)))
    if ctx.realistic:
        if level_held(ctx):
            ctx.grant(eid, "measure", ["tilt_deg"])
        return "Hung {} on {} ({}){}".format(eid, dest, _tilt_seen(ctx, eid, m["tilt_deg"], level_held(ctx)),
                                             ": " + "; ".join(notes) if notes else "")
    return "Hung {} on {} (tilt {:+.1f} deg){}".format(eid, dest, m["tilt_deg"], ": " + "; ".join(notes) if notes else "")


LEVEL_KINDS = ("spirit_level", "torpedo_level", "digital_level", "laser_level")


def level_held(ctx):
    """The level the robot holds (or None): only a level turns a tilt into a number."""
    if ctx is None:
        return None
    return next((h for h in ctx.w.held_items() if ctx.w.ent(h).get("tool_kind") in LEVEL_KINDS), None)


def _tilt_seen(ctx, eid, tilt, level):
    """Realistic wording of a hung item's tilt: a level reading (about +-0.1 deg) when a level is held, else what the eye
    tells (crooked beyond about 1.5 deg)."""
    if level:
        return "level reads {:+.1f} deg".format(C.reading(ctx, tilt, 0.05, 0.1, eid, "tilt", C.rnd(tilt, 3)))
    return "looks level" if abs(float(tilt)) < 1.5 else "looks crooked"


def _level(ctx, eid):
    w = ctx.w
    rec = ctx.obj(eid, touch=True)
    require(rec.get("relation") == "hung" and "tilt_deg" in rec, "Only a hung item can be leveled this way", "precondition")
    level_tool = ctx.args.get("level_tool")
    if level_tool:
        require(ctx.holding(level_tool) and w.ent(level_tool).get("tool_kind") in ("spirit_level", "torpedo_level", "digital_level", "laser_level"),
                "Hold a level", "capability")
        tilt = ctx.rng.uniform(-0.3, 0.3)
    else:
        tilt = ctx.rng.uniform(-2.5, 2.5)
    w.mut(eid)["tilt_deg"] = C.rnd(tilt, 2)
    if ctx.realistic:
        if level_tool:
            ctx.grant(eid, "measure", ["tilt_deg"])      # read off the level (about +-0.1 deg)
        return "Adjusted: {}".format(_tilt_seen(ctx, eid, C.rnd(tilt, 2), level_tool))
    return "Adjusted: tilt now {:+.1f} deg{}".format(tilt, "" if level_tool else " (by eye)")


def _take_down(ctx, eid):
    w = ctx.w
    rec = ctx.obj(eid, touch=True)
    from ..core import check_handling, release_from_hands
    if rec.get("relation") == "hung" and not is_hardware(rec):
        hand = ctx.free_hand()
        require(hand is not None, "Free a hand first", "capability")
        check_handling(ctx, eid)
        before = rec.get("location")
        m = w.mut(eid)
        m.update(location="robot", relation="held", held_hand=hand)
        w.robot_m()["hands"][hand] = eid
        w.log_move(eid, before, "robot")
        return "Took down {}".format(eid)
    require(is_hardware(rec) and rec.get("mounted"), "Nothing mounted to take down there", "precondition")
    require(not any(w.ent(c).get("relation") == "hung" for c in w.children(eid)), "Take the decoration off it first")
    mount = rec.get("mount") or {}
    surface = mount.get("surface")
    pull = ctx.args.get("pull") or "down"
    notes = []
    if mount.get("method") == "nail":
        raise ActionError("Pull nails with a claw hammer (hammer mode=pull)", "capability")
    srec = w.ent(surface) if surface in w.s["entities"] else {}
    damage = False
    if rec.get("tape_kind") in ("packing", "duct") or (rec.get("adhesive") and pull == "out"):
        damage = True
    if surface_finish(srec) == "wallpaper" and rec.get("adhesive"):
        damage = True
    if float(srec.get("paint_age_days", 365)) < 7 and rec.get("adhesive"):
        damage = True
    if damage and surface:
        sm = w.mut(surface)
        sm["paint_peeled"] = int(sm.get("paint_peeled", 0)) + 1
        C.add_damage(sm, "paint_peeled", 0.3)
        w.stat("damage")
        notes.append("paint/paper came off with the adhesive")
    hand = ctx.free_hand()
    m = w.mut(eid)
    m.pop("mount", None)
    m["mounted"] = False
    m["used"] = True
    m.pop("z_m", None)
    if hand is not None:
        m.update(location="robot", relation="held", held_hand=hand)
        w.robot_m()["hands"][hand] = eid
    else:
        room = C.robot_room(w)
        m.update(location=room, relation="on", pos=list(geometry.free_floor_cell(w.s, room, near=C.robot_cell(w)) or C.robot_cell(w)))
    if surface and surface in w.s["entities"]:
        sdoor = [d for d, r in w.s["entities"].items() if r.get("stop_ok") and (r.get("_swing") or {}).get("wall") == surface]
        for d in sorted(sdoor):
            if rec.get("stops_door") == d:
                w.mut(d)["stop_ok"] = False
    return "Released {} (pulled {}){}".format(eid, pull, ": " + "; ".join(notes) if notes else "")


def pull_nail(ctx, eid, notes):
    w = ctx.w
    rec = w.ent(eid)
    if is_hardware(rec) and rec.get("mounted") and (rec.get("mount") or {}).get("method") == "nail":
        require(not any(w.ent(c).get("relation") == "hung" for c in w.children(eid)), "Take the decoration off it first")
        surface = rec["mount"]["surface"]
        sm = w.mut(surface)
        for hole in C.hole_list(sm) + [h for h in sm.get("tv_holes") or [] if isinstance(h, dict)]:
            if hole.get("by") == eid:
                hole["state"] = "open"
        m = w.mut(eid)
        m.pop("mount", None)
        m["mounted"] = False
        m.pop("z_m", None)
        hand = ctx.free_hand()
        if hand is not None:
            m.update(location="robot", relation="held", held_hand=hand)
            w.robot_m()["hands"][hand] = eid
        else:
            room = C.robot_room(w)
            m.update(location=room, relation="on", pos=list(C.robot_cell(w)))
        return "Pulled the nail; a small hole remains" + (": " + "; ".join(notes) if notes else "")
    raise ActionError("No nail to pull there", "precondition")


# ---------------------------------------------------------------------------------------------
# systems: cure, creep, heat/wet decay, falls

def _active_heat_near(w, hw):
    rec = w.ent(hw)
    mount = rec.get("mount") or {}
    if "heat" not in (mount.get("hazards") or []):
        return False
    room = w.root_room(hw)
    point = mount.get("point")
    for eid in C.index(w, "heat_sources", C.is_heat_source):
        src = w.s["entities"].get(eid)
        if src is None or w.root_room(eid) != room:
            continue
        hot = src.get("on") or float(src.get("temperature_c", 21.0)) > 45 or float(src.get("internal_temp_c") or 0) > 45 or \
            any(float(w.ent(c).get("temperature_c", 21)) > 80 for c in w.children(eid))
        if not hot:
            continue
        r2, box = _ent_box(w, eid)
        if box and _dist_xy(point, box) <= 0.6:
            return True
    return False


@system("tools_vehicles.adhesive", order=137, max_dt=30.0)
def adhesive_system(w, dt, tick):
    """Per substep over the static index of hanging hardware (only hardware is ever mounted by mount_hardware):
    heat/wet decay, overload creep and falls. Hooks the household already had (no mount method) are structural."""
    t = C.now(w)
    ents = w.s["entities"]
    for hw in C.index(w, "hardware", is_hardware):
        rec = ents.get(hw)
        if rec is None or not rec.get("mounted") or not isinstance(rec.get("mount"), dict) or not rec["mount"].get("method"):
            continue
        mount = rec["mount"]
        decay = float(mount.get("decay", 1.0))
        new_decay = decay
        if mount.get("method") != "nail" and _active_heat_near(w, hw):
            new_decay = max(0.05, decay - dt / 600.0)
        if "wet" in (mount.get("hazards") or []) and mount.get("method") != "nail":
            room = w.root_room(hw)
            humid = float(w.s["rooms"].get(room, {}).get("humidity", 0.45)) > 0.7 or float(w.ent(mount["surface"]).get("wetness", 0.0)) > 0.2
            if humid:
                new_decay = max(0.05, new_decay - dt / 7200.0)
        if abs(new_decay - decay) > 1e-9:
            w.mut(hw)["mount"]["decay"] = C.rnd(new_decay, 4)
        load = load_kg(w, hw)
        if load <= 0:
            continue
        cap = capacity_kg(w, hw)
        if load <= cap + 1e-9:
            if mount.get("overload_since") is not None:
                w.mut(hw)["mount"]["overload_since"] = None
            continue
        since = mount.get("overload_since")
        if since is None:
            w.mut(hw)["mount"]["overload_since"] = t
            continue
        ttf = 120.0 + 2400.0 * max(0.0, cap / load)
        if t - float(since) >= ttf:
            _fall(w, hw, tick)


def _fall(w, hw, tick):
    rec = w.ent(hw)
    mount = rec["mount"]
    surface = mount.get("surface")
    room = w.root_room(hw)
    height = float(mount.get("height_m", 1.5))
    point = mount.get("point") or [0.25, 0.25, height]
    cell = [int(point[0] / C.CELL), int(point[1] / C.CELL)]
    cols, rows = geometry.grid_size(w.s["rooms"][room])
    cell = [max(0, min(cols - 1, cell[0])), max(0, min(rows - 1, cell[1]))]
    below = None
    for eid in sorted(e for e, r in w.s["entities"].items() if r.get("location") == room and r.get("pos")):
        r = w.ent(eid)
        if tuple(cell) in geometry.footprint(r) and (r.get("surface") or C.is_heat_source(r)) and eid != surface:
            below = eid
            break
    items = [c for c in w.children(hw) if w.ent(c).get("relation") in ("hung", "on", "attached")]
    for item in items + [hw]:
        irec = w.ent(item)
        m = w.mut(item)
        for key in ("spos", "shelf", "held_hand", "z_m"):
            m.pop(key, None)
        drop = height - (geometry.height_m(w.s, below) if below else 0.0)
        if below and w.ent(below).get("accepts_items"):
            m.update(location=below, relation="on")
            spot = geometry.surface_spot(w.s, below, item)
            if spot is not None:
                m["spos"] = spot
        else:
            m.update(location=room, relation="on", pos=cell)
        if irec.get("fragile") and drop > float(irec.get("drop_tolerance_m", 0.3)):
            m["broken"] = True
            C.add_damage(m, "broken", 1.0)
            w.stat("breakages")
            w.stat("damage")
        m["fallen"] = True
        w.log_move(item, hw if item != hw else surface, m["location"], by="gravity")
    hm = w.mut(hw)
    hm["mounted"] = False
    hm["failed"] = True
    hm.pop("mount", None)
    if surface in w.s["entities"] and float(w.ent(surface).get("paint_age_days", 365)) < 7:
        sm = w.mut(surface)
        sm["paint_peeled"] = int(sm.get("paint_peeled", 0)) + 1
    if below and C.is_heat_source(w.ent(below)):
        C.log(w, "fell_onto_heat", hardware=hw, onto=below)
    C.log(w, "mount_failed", hardware=hw, items=items, surface=surface)


@after("open", order=130)
def _door_hits_wall(ctx, result):
    w = ctx.w
    door = ctx.args["target"]
    rec = w.ent(door)
    swing = rec.get("_swing")
    if not isinstance(swing, dict) or rec.get("stop_ok"):
        return
    wall = swing.get("wall")
    if wall in w.s["entities"]:
        m = w.mut(wall)
        m["dents"] = int(m.get("dents", 0)) + 1
        m["handle_hits"] = int(m.get("handle_hits", 0)) + 1
        C.add_damage(m, "dent", 0.3)
        if m["dents"] == 1:
            w.stat("damage")
        ctx.note("The door handle hit the wall")


@inspector("tools_vehicles.mounts", order=66)
def inspect_mounts(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    if isinstance(record.get("mount"), dict):
        mount = record["mount"]
        if C.realistic(ctx):
            # what a look shows: where and how it is fixed (positions to about 5 cm); the robot mounted it, so it knows
            # whether the cure time has passed. Rating (printed on the package) and bond strength are not visible here.
            def _r5(v):
                return round(round(float(v) * 20.0) / 20.0, 2) if isinstance(v, (int, float)) else v
            details[C.seen_key(ctx, "mount")] = {"height_m": _r5(mount.get("height_m")), "offset_m": _r5(mount.get("offset_m")),
                                                 "method": mount.get("method"),
                                                 "cured": C.now(w) - float(mount.get("mounted_at", 0)) >= float(mount.get("cure_s", 0))}
        else:
            details["mount"] = {"height_m": mount.get("height_m"), "offset_m": mount.get("offset_m"), "method": mount.get("method"),
                                "cured": C.now(w) - float(mount.get("mounted_at", 0)) >= float(mount.get("cure_s", 0)),
                                "rated_kg": mount.get("rated_kg", record.get("rated_kg"))}
    if "tilt_deg" in record and record.get("relation") == "hung":
        level = next((h for h in w.held_items() if w.ent(h).get("tool_kind") in ("spirit_level", "torpedo_level", "digital_level")), None)
        if C.realistic(ctx):
            details["tilt"] = _tilt_seen(ctx, eid, record["tilt_deg"], level)
            C.mark_perceived(ctx, details, "tilt")
            if level:
                ctx.grant(eid, "measure", ["tilt_deg"])
        else:
            details["tilt"] = "{:+.1f} deg (level)".format(record["tilt_deg"]) if level else ("looks level" if abs(record["tilt_deg"]) < 1.5 else "looks crooked")
    if isinstance(record.get("_swing"), dict) and ctx.args.get("where") == "closely":
        swing = record["_swing"]
        digits = 20.0 if C.realistic(ctx) else 100.0       # realistic: where the handle touches, judged by eye to ~5 cm
        details["swing_contact"] = {"wall": swing.get("wall"),
                                    "offset_m": round(round(float(swing.get("contact_offset_m", 0)) * digits) / digits, 2),
                                    "height_m": round(round(float(swing.get("contact_height_m", 0)) * digits) / digits, 2)}
        details["door_stop"] = "ok" if record.get("stop_ok") else "handle reaches the wall"
    if record.get("category") in ("wall", "wall_surface") or record.get("wall_material"):
        # realistic: how old the paint is is household knowledge (ask), not something a look tells
        for key in (("finish", "dents", "paint_peeled") if C.realistic(ctx) else ("finish", "paint_age_days", "dents", "paint_peeled")):
            if key in record:
                details[key] = record[key]

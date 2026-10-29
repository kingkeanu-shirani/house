"""Repairs and finishes on surfaces: paint, patch, sand, caulk, regrout, pressure_wash, seal_surface
(fidelity P-approx: coats/coverage, dry and cure times, weather windows, nozzle pressure vs surface
hardness; G for which hole/spot).

Fields:
  wall-like:  paint {color, sheen}, holes [{id, diameter_mm, state, passes, dry_at, primed, coats, color_ok, sheen_ok}],
              paint_coats, paint_coverage, paint_color_ok, paint_wet_until, touch_up_visible
  fixtures:   caulk_joint {state, mold, residue, bond_ok, applied_at, cure_s, continuity}
  tile:       grout {state, cracked_m, raked, filled_at, cure_s}
  outdoor:    sealable, area_m2, seal {applied_at, quality, coverage, cured, failed}, soil, wetness, gouged
States: hole open -> filled_wet -> filled (dry) [-> recessed if > 12.7 mm after one pass] -> sanded -> primed -> painted.

Tier-1 adoption (kernel v3.0): the pressure washer declares its noise (80 dB at 1 m: the body's quiet mode refuses it;
the hand-written quiet check is gone) and needs both arms on the wand; a window the jet breaks breaks through the
kernel (physics.break_entity: shattered glass, a sharp floor hazard, the break event the camera can see).
"""
import copy
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import ACTIONS, inspector, system
from . import common as C

HARDNESS_BAR = {"cedar": 14.0, "redwood": 14.0, "pine": 22.0, "pressure_treated_pine": 24.0, "wood": 20.0, "composite": 30.0,
                "vinyl": 35.0, "car_paint": 45.0, "steel": 45.0, "aluminum": 40.0, "brick": 70.0, "stone": 110.0, "concrete": 120.0,
                "glass": 40.0, "fabric": 8.0, "screen": 6.0}
NOZZLE = {"0": 1.0, "15": 0.3, "25": 0.15, "40": 0.07, "soap": 0.01, "shower": 0.005, "jet": 0.03}
PRESSURE_WASHER_DB = 80.0    # an electric 1900 psi washer at 1 m, pump and jet (manufacturers list 76-85 dB(A))


def _soil(w, eid, kind, amount):
    from ..core import add_soil
    return add_soil(w, eid, kind, amount)


def _floor(w, room):
    fid = "floor_" + room
    return fid if fid in w.s["entities"] else None


def drop_cloth_under(w, eid):
    """A drop cloth / plastic sheeting lying on the floor within 2 cells of the work."""
    room, cells = C.anchor_cells(w, eid)
    if not room or room not in w.s["rooms"]:
        return None
    for other in sorted(w.children(room)):
        rec = w.ent(other)
        if not rec.get("pos"):
            continue
        tags = rec.get("tags") or []
        if rec.get("category") == "cover" or "cover" in tags or "drop_cloth" in tags or rec.get("type") in ("drop_cloth", "plastic_sheeting"):
            if not cells or min(geometry.chebyshev(tuple(rec["pos"]), c) for c in cells) <= 2:
                return other
    return None


def _mess(w, eid, kind, amount, notes, what):
    cloth = drop_cloth_under(w, eid)
    if cloth:
        _soil(w, cloth, kind, amount)
        return
    room = w.root_room(eid)
    floor = _floor(w, room) if room else None
    if floor:
        _soil(w, floor, kind, amount)
    room_rec = w.s["rooms"].get(room, {})
    _, cells = C.anchor_cells(w, eid)
    for other in sorted(w.children(room)) if room else []:
        orec = w.ent(other)
        if orec.get("pos") and orec.get("blocks_path") and cells and orec.get("category") in ("furniture", "seat", "bed", "counter") \
                and min(geometry.chebyshev(c, d) for c in geometry.footprint(orec) for d in cells) <= 1:
            _soil(w, other, kind, amount * 0.5)
    w.stat("tv_unprotected_mess")
    notes.append("{} landed on the floor (no drop cloth)".format(what))


def _hole(rec, spot):
    holes = C.hole_list(rec)
    if spot:
        for h in holes:
            if h.get("id") == spot:
                return h
        raise ActionError("No hole or patch called " + str(spot), "argument")
    pending = [h for h in holes if h.get("state") not in ("painted",)]
    require(pending, "No hole needs work there", "precondition")
    return pending[0]


def _update_hole_time(w, h):
    if h.get("state") == "filled_wet" and C.now(w) >= float(h.get("dry_at", 0.0)):
        h["state"] = "recessed" if h.get("shrinks") else "filled"


# =============================================================================================
# patch

@action("patch", params={"target": "entity", "tool": "?entity", "material": "?entity", "spot": "?str"},
        duration=90.0, energy=0.01, noise=0.02, category="physical",
        doc="Fill a nail/screw/anchor hole in a wall (spot = hole id) with spackle from a tub within reach, using a held putty knife; "
            "holes over 1/2 in shrink and need a second pass after drying, over 1 in need a mesh patch, over 2 in are beyond spackle. "
            "Lightweight spackle dries in ~30-60 min before sanding. Also patches a bike tube puncture with a patch kit after the "
            "area is located (inspect closely while inflated) and roughened (sand).")
def patch(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    notes = []
    if isinstance(rec.get("inflation"), dict) and rec["inflation"].get("_puncture"):
        return _patch_tube(ctx, eid, rec)
    require(C.hole_list(rec), "Nothing to patch there", "precondition")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") in ("putty_knife",) or "spread_spackle" in C.tool_caps(r),
                          "Hold a putty knife")
    mat = ctx.args.get("material")
    if not mat:
        mat = next((h for h in w.held_items() if w.ent(h).get("product") == "spackle"), None) or \
            next((e for e in sorted(w.children(C.robot_room(w))) + sorted(w.s["entities"]) if w.s["entities"].get(e, {}).get("product") == "spackle"
                  and C.near_robot(w, e, 2)), None)
    require(mat and w.ent(mat).get("product") == "spackle", "Have spackle within reach", "resource")
    require(ctx.holding(mat) or C.near_robot(w, mat, 2), "Keep the spackle within reach", "precondition")
    require(float(w.ent(mat).get("amount_l", 0.0)) > 0.005, "The spackle tub is empty", "resource")
    m = w.mut(eid)
    h = _hole(m, ctx.args.get("spot"))
    _update_hole_time(w, h)
    d = float(h.get("diameter_mm", 6.0))
    require(d <= 50.0, "That hole is too big for spackle; it needs a drywall patch", "precondition")
    require(h.get("state") in ("open", "nail", "recessed", "gouged", "filled_wet", "failed"), "That spot is already filled ({})".format(h.get("state")))
    require(h.get("state") != "nail", "Pull the nail out first", "precondition")
    if d > 25.0 and not h.get("mesh"):
        h["state"] = "failed"
        notes.append("spackle alone sags out of a hole this size: use a mesh patch")
        return "Patch failed: " + "; ".join(notes)
    passes = int(h.get("passes", 0)) + 1
    h["passes"] = passes
    h["shrinks"] = d > 12.7 and passes < 2
    h["state"] = "filled_wet"
    h["dry_at"] = C.now(w) + (1800.0 if d <= 12.7 else 3600.0)
    C.activate(w, "curing", eid)        # the cure system watches this surface until the spackle is dry
    h["primed"] = False
    h["coats"] = 0
    mm = w.mut(mat)
    mm["amount_l"] = C.rnd(max(0.0, float(mm.get("amount_l", 0.24)) - 0.002 * max(1.0, d / 6.0)), 4)
    mm["open"] = True
    ok, more = C.use_tool(ctx, tool, "patch")
    notes.extend(more)
    _soil(w, tool, "paint_acrylic", 0.3)
    if ctx.realistic:
        # a hole's size judged by eye at arm's length (about 2 mm); the drying time is the tub's label guidance
        return "Filled {} (about {:.0f} mm, pass {}); dry by +{:.0f} min{}".format(h["id"], eye_mm(d), passes, (h["dry_at"] - C.now(w)) / 60.0,
                                                                              ": " + "; ".join(notes) if notes else "")
    return "Filled {} ({:.0f} mm, pass {}); dry by +{:.0f} min{}".format(h["id"], d, passes, (h["dry_at"] - C.now(w)) / 60.0,
                                                                      ": " + "; ".join(notes) if notes else "")


def eye_mm(value):
    """A small hole's diameter as a close look judges it: to about 2 mm."""
    return int(round(float(value or 0.0) / 2.0) * 2)


def _patch_tube(ctx, eid, rec):
    w = ctx.w
    infl = rec["inflation"]
    kit = next((h for h in w.held_items() if w.ent(h).get("product") == "patch_kit"), None) or \
        next((e for e in sorted(w.s["entities"]) if w.ent(e).get("product") == "patch_kit" and C.near_robot(w, e, 2)), None)
    require(kit, "Have a patch kit at hand", "resource")
    require(int(w.ent(kit).get("patches", 0)) > 0, "The patch kit is out of patches", "resource")
    require(infl.get("puncture_located"), "Find the puncture first (inflate a little and inspect closely)", "precondition")
    km = w.mut(kit)
    km["patches"] = int(km["patches"]) - 1
    m = w.mut(eid)
    i = m["inflation"]
    if i.get("sanded"):
        i["_puncture"] = None
        i["_leak_bar_h"] = 0.0
        i["patched"] = True
        msg = "Patched the tube; it holds air"
    else:
        i["_leak_bar_h"] = C.rnd(float(i.get("_leak_bar_h", 6.0)) * 0.4, 3)
        i["patched"] = True
        msg = "The patch did not bond to the unsanded rubber: it still leaks slowly"
    return msg


# =============================================================================================
# sand

@action("sand", params={"target": "entity", "tool": "?entity", "spot": "?str", "angle_deg": {"type": "number", "min": 5, "max": 45, "optional": True},
                        "strokes": {"type": "int", "min": 1, "max": 200, "optional": True}},
        duration=60.0, energy=0.01, noise=0.1, category="physical",
        doc="Abrade with a held sanding block/sandpaper or sharpening stone. Wall patches: only once the spackle is dry (wet "
            "spackle smears and gouges); drywall dust lands on the floor unless a drop cloth is down. Bike tubes: roughen around "
            "a puncture before patching. Knives: on a soaked whetstone at the knife's edge angle (angle_deg, strokes); a "
            "pull-through V sharpener only suits double-bevel steel knives (serrated, ceramic and single-bevel knives are damaged).")
def sand(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: "sand" in C.tool_caps(r) or "sharpen_knife" in C.tool_caps(r), "Hold sandpaper or a sharpening stone")
    trec = w.ent(tool)
    notes = []
    if "sharpen_knife" in C.tool_caps(trec):
        return _sharpen_knife(ctx, eid, rec, tool, trec)
    ok, more = C.use_tool(ctx, tool, "sand")
    notes.extend(more)
    m = w.mut(eid)
    if isinstance(rec.get("inflation"), dict):
        m["inflation"]["sanded"] = True
        return "Roughened the rubber around the puncture"
    if C.hole_list(rec):
        h = _hole(m, ctx.args.get("spot"))
        _update_hole_time(w, h)
        if h.get("state") == "filled_wet":
            h["state"] = "gouged"
            notes.append("the spackle was still wet: it smeared and gouged")
        elif h.get("state") == "filled":
            h["state"] = "sanded"
        elif h.get("state") == "recessed":
            notes.append("sanding cannot raise a low spot: fill it again")
        elif h.get("state") in ("open", "nail"):
            notes.append("there is nothing in the hole to sand")
        _mess(w, eid, "drywall_dust", 0.2, notes, "sanding dust")
        return "Sanded {}: {}{}".format(h["id"], h["state"], ": " + "; ".join(notes) if notes else "")
    if rec.get("sealable") or rec.get("material") in ("wood", "cedar", "pine", "hardwood", "oak"):
        soil = m.setdefault("soil", {})
        for kind in ("paint_dried", "mildew", "dirt"):
            if kind in soil:
                soil[kind] = C.rnd(float(soil[kind]) * 0.3, 4)
        m["sanded"] = True
        m.pop("splinters", None)
        _mess(w, eid, "sawdust", 0.15, notes, "sawdust")
        return "Sanded the wood{}".format(": " + "; ".join(notes) if notes else "")
    raise ActionError("Nothing to sand there", "precondition")


def _knife_edge(rec):
    t = rec.get("type", "") + " " + str(rec.get("tool_kind", ""))
    serrated = rec.get("serrated") if rec.get("serrated") is not None else ("bread" in t or "serrated" in t)
    material = rec.get("blade_material") or ("ceramic" if "ceramic" in t else "steel")
    bevel = rec.get("bevel") or ("single" if any(k in t for k in ("yanagiba", "deba", "single_bevel")) else "double")
    angle = float(rec.get("edge_angle_deg", 15.0 if "japanese" in t or bevel == "single" else 20.0))
    return serrated, material, bevel, angle


def _sharpen_knife(ctx, eid, rec, tool, trec):
    w = ctx.w
    require("sharpness" in rec or rec.get("hazards") and "sharp" in rec.get("hazards", []), "Only knives and blades go on a sharpener", "argument")
    serrated, material, bevel, edge = _knife_edge(rec)
    m = w.mut(eid)
    notes = []
    sharp = float(rec.get("sharpness", 0.5))
    if trec.get("tool_kind") == "pull_through_sharpener":
        if serrated or material == "ceramic" or bevel == "single":
            m["edge_damaged"] = True
            m["sharpness"] = C.rnd(min(sharp, 0.3), 3)
            C.add_damage(m, "edge_damaged", 0.6)
            w.stat("damage")
            reason = "serrations ground flat" if serrated else ("ceramic edge chipped" if material == "ceramic" else "single-bevel geometry ruined")
            C.log(w, "knife_damaged", knife=eid, reason=reason)
            return "Pulled through the V sharpener: {}".format(reason)
        strokes = int(ctx.args.get("strokes") or 6)
        m["sharpness"] = C.rnd(min(0.85, sharp + 0.06 * strokes), 3)
        m["edge_angle_deg"] = float(trec.get("v_angle_deg", 20.0))
        m["burr"] = False
        ctx.spend(2.0 * strokes)
        if ctx.realistic:
            return "Pulled through the coarse and fine slots: the edge now feels {}".format(C.sharpness_word(m["sharpness"]))
        return "Pulled through the coarse and fine slots: sharpness {:.2f}".format(m["sharpness"])
    # whetstone
    if serrated or material == "ceramic":
        notes.append("a flat stone does not suit this edge (needs a ceramic rod or diamond stone)")
        return "No improvement: " + "; ".join(notes)
    strokes = int(ctx.args.get("strokes") or 30)
    angle = float(ctx.args.get("angle_deg") or edge)
    wet = float(trec.get("wetness", 0.0)) >= 0.5
    sm = w.mut(tool)
    if not wet:
        sm["glazed"] = True
        notes.append("the dry stone loaded with metal and glazed")
        gain = 0.002 * strokes
    else:
        gain = 0.02 * strokes
    if abs(angle - edge) > 3.0:
        m["edge_rounded"] = True
        m["sharpness"] = C.rnd(min(0.6, max(0.2, sharp - 0.1)), 3)
        notes.append("the angle was off: the edge was rounded over" if ctx.realistic else
                     "the angle was off by {:.0f} deg: the edge was rounded over".format(abs(angle - edge)))
    else:
        m["sharpness"] = C.rnd(min(1.0, sharp + gain), 3)
        m["burr"] = strokes >= 20 and float(trec.get("grit", 1000)) < 3000
        m["edge_angle_deg"] = angle
    sm["wetness"] = C.rnd(max(0.0, float(trec.get("wetness", 0.0)) - 0.1), 3)
    _soil(w, tool, "dirt", 0.1)
    ctx.spend(1.5 * strokes)
    if ctx.realistic:
        # a thumbnail/paper test tells dull from sharp, not a sharpness number
        return "Sharpened on the stone: the edge now feels {}{}".format(C.sharpness_word(m["sharpness"]), ": " + "; ".join(notes) if notes else "")
    return "Sharpened on the stone: sharpness {:.2f}{}".format(m["sharpness"], ": " + "; ".join(notes) if notes else "")


# =============================================================================================
# paint

@action("paint", params={"target": "entity", "tool": "?entity", "paint": "?entity", "area": "?str:touch_up|wall", "spot": "?str"},
        duration=1.0, energy=0.02, noise=0.02, category="physical",
        doc="Brush or roll one coat from an open paint can within reach onto a wall: area=touch_up paints a sanded patch or scuffs "
            "(spot = hole id), area=wall a full coat (~20 min per 10 m2). Primer first over patches (else the patch 'flashes'); "
            "color and sheen must match the wall; wait the label's recoat time between coats; wet paint drips onto an unprotected floor.")
def paint(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(isinstance(rec.get("paint"), dict) or rec.get("paintable"), "That surface is not painted", "argument")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: "paint" in C.tool_caps(r), "Hold a brush or roller")
    can = ctx.args.get("paint") or next((h for h in w.held_items() if w.ent(h).get("paint")), None) or \
        next((e for e in sorted(w.s["entities"]) if w.ent(e).get("paint") and C.near_robot(w, e, 2) and w.ent(e).get("open")), None)
    require(can and w.ent(can).get("paint"), "Have an open can of paint within reach", "resource")
    crec = w.ent(can)
    require(crec.get("open") or not crec.get("openable"), "Open the paint can first", "precondition")
    require(ctx.holding(can) or C.near_robot(w, can, 2), "Keep the paint can within reach", "precondition")
    require(float(crec.get("amount_l", 0.0)) > 0.005, "The can is empty", "resource")
    area = ctx.args.get("area") or "touch_up"
    m = w.mut(eid)
    wall_paint = m.get("paint") if isinstance(m.get("paint"), dict) else {}
    color_ok = crec.get("paint_color") == wall_paint.get("color")
    sheen_ok = crec.get("sheen") == wall_paint.get("sheen")
    notes = []
    ok, more = C.use_tool(ctx, tool, "paint")
    notes.extend(more)
    now = C.now(w)
    cm = w.mut(can)
    if area == "touch_up":
        h = None
        if C.hole_list(m):
            h = _hole(m, ctx.args.get("spot"))
            _update_hole_time(w, h)
        if h is not None:
            if h.get("state") == "filled_wet":
                h["bubbled"] = True
                notes.append("painted over wet spackle: it bubbled")
            elif h.get("state") in ("open", "nail", "recessed", "gouged"):
                notes.append("the hole/low spot still shows through the paint")
            if crec.get("primer"):
                h["primed"] = h.get("state") in ("sanded", "filled") or h.get("primed", False)
                h["primed_at"] = now
            else:
                if h.get("coats", 0) and now - float(h.get("painted_at", 0.0)) < float(crec.get("recoat_s", 7200.0)) * 0.5:
                    h["lifted"] = True
                    notes.append("recoated too soon: the first coat lifted")
                h["coats"] = int(h.get("coats", 0)) + 1
                h["painted_at"] = now
                h["color_ok"] = color_ok if h.get("color_ok", True) else False
                h["sheen_ok"] = sheen_ok if h.get("sheen_ok", True) else False
                h["flashing"] = not h.get("primed")
                h["state"] = "painted" if h.get("state") == "sanded" else h.get("state")
            h["visible"] = touch_up_visible(h)
        else:
            m["touch_up_coats"] = int(m.get("touch_up_coats", 0)) + (0 if crec.get("primer") else 1)
            soil = m.get("soil") or {}
            if "scuff" in soil and not crec.get("primer"):
                soil["scuff"] = 0.0
            m["touch_up_color_ok"] = color_ok and sheen_ok
        cm["amount_l"] = C.rnd(max(0.0, float(crec.get("amount_l", 0.95)) - 0.01), 4)
        ctx.spend(120.0)
        if tool and w.ent(tool).get("tool_kind") == "paint_roller":
            _mess(w, eid, "paint_acrylic", 0.15, notes, "roller spatter")
    else:
        size = rec.get("size_m") or [3.0, 0.1, 2.4]
        area_m2 = float(rec.get("area_m2", float(size[0]) * float(size[2])))
        need = area_m2 / 10.0
        if ctx.realistic:
            # how much is left in the can and the wall's exact area are not read out by a refusal
            require(float(crec.get("amount_l", 0.0)) >= need * 0.9, "Not enough paint left in the can for a full coat", "resource")
        require(float(crec.get("amount_l", 0.0)) >= need * 0.9, "Not enough paint for a full coat ({:.1f} l needed)".format(need), "resource")
        last = m.get("paint_last_coat_at")
        if last is not None and now - float(last) < float(crec.get("recoat_s", 7200.0)) * 0.5:
            m["paint_lifted"] = True
            notes.append("rolled the second coat before the first dried: it lifted")
        coats = int(m.get("paint_coats", 0)) + (0 if crec.get("primer") else 1)
        m["paint_coats"] = coats
        m["paint_coverage"] = C.rnd(1.0 - 0.25 ** coats, 4) if coats else 0.0
        m["paint_color_ok"] = color_ok and sheen_ok and m.get("paint_color_ok", True)
        m["paint_last_coat_at"] = now
        m["paint_wet_until"] = now + 3600.0
        cm["amount_l"] = C.rnd(max(0.0, float(crec.get("amount_l", 0.95)) - need), 4)
        ctx.spend(120.0 * area_m2)
        _mess(w, eid, "paint_acrylic", 0.3, notes, "paint drips")
        soil = m.get("soil") or {}
        for kind in ("scuff", "fingerprints", "crayon"):
            if kind in soil and coats >= 1:
                soil[kind] = 0.0
    tm = w.mut(tool)
    tm["paint_loaded"] = crec.get("paint_color")
    _soil(w, tool, "paint_acrylic", 0.4)
    if not color_ok or not sheen_ok:
        # realistic: the camera sees the new paint differ in color or gloss; it does not read the can's printed color
        # name and sheen off the wall (read the label for that)
        notes.append("the new paint looks different from the wall (color or sheen)" if ctx.realistic else
                     "the paint does not match the wall ({}/{})".format(crec.get("paint_color"), crec.get("sheen")))
    return "Painted {} ({}){}".format(eid, area, ": " + "; ".join(notes) if notes else "")


def touch_up_visible(h):
    if h.get("state") != "painted" or h.get("bubbled") or h.get("lifted"):
        return True
    return not (h.get("primed") and h.get("color_ok", True) and h.get("sheen_ok", True) and int(h.get("coats", 0)) >= 1)


# =============================================================================================
# caulk

@action("caulk", params={"target": "entity", "tool": "?entity", "mode": "?str:remove|apply"},
        duration=1.0, energy=0.01, noise=0.02, category="physical",
        doc="Tub/sink/counter joints. mode=remove scrapes out the old (often moldy) caulk with a held plastic scraper or utility "
            "knife (knife leaves more residue and scratches acrylic). mode=apply runs a continuous bead from a held caulk gun "
            "loaded with a cut tube onto a clean, dry joint and tools it smooth. Silicone needs its cure time (24 h) before water; "
            "caulk over old caulk, residue or a wet joint does not bond.")
def caulk(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    joint = rec.get("caulk_joint")
    require(isinstance(joint, dict), "There is no caulk joint there", "argument")
    mode = ctx.args.get("mode") or "apply"
    m = w.mut(eid)
    j = m["caulk_joint"]
    notes = []
    length = float(j.get("length_m", 1.5))
    if mode == "remove":
        tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") in ("plastic_scraper", "utility_knife", "putty_knife", "razor_scraper", "caulk_remover")
                              or r.get("tool_category") == "scraper", "Hold a scraper or utility knife")
        trec = w.ent(tool)
        if trec.get("tool_kind") == "utility_knife":
            require((trec.get("settings") or {}).get("blade") == "extended", "Extend the blade first", "precondition")
        require(j.get("state") in ("old", "moldy", "failed", "bead", "cured"), "No old caulk to remove")
        C.use_tool(ctx, tool, "remove_caulk")
        j["state"] = "removed"
        j["mold"] = False
        knife = trec.get("tool_kind") in ("utility_knife", "razor_scraper")
        j["residue"] = 0.15 if knife else 0.05
        if knife and rec.get("material") in ("acrylic", "fiberglass"):
            m["finish_marks"] = int(m.get("finish_marks", 0)) + 1
            notes.append("the blade scratched the acrylic")
        ctx.spend(60.0 * length)
        return "Removed the old caulk{}".format(": " + "; ".join(notes) if notes else "")
    gun = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") == "caulk_gun", "Hold a caulk gun")
    tube = ((w.ent(gun).get("slots") or {}).get("tube") or {}).get("item")
    require(tube and tube in w.s["entities"], "Load a tube of caulk into the gun", "precondition")
    trec = w.ent(tube)
    require(trec.get("nozzle_cut"), "Cut the nozzle first (cut_with)", "precondition")
    require(float(trec.get("amount_l", 0.0)) >= 0.02 * length, "Not enough caulk left in the tube", "resource")
    bond = True
    if j.get("state") in ("old", "moldy", "failed"):
        bond = False
        j["over_old"] = True
        notes.append("caulked over the old caulk")
    if float(j.get("residue", 0.0)) > 0.1:
        bond = False
        notes.append("residue left in the joint")
    if float(rec.get("wetness", 0.0)) > 0.1:
        bond = False
        notes.append("the joint was wet")
    j.update(state="bead", applied_at=C.now(w), cure_s=float(trec.get("cure_s", 86400.0)), bond_ok=bond, continuity=1.0, cured=False,
             product=trec.get("product"))
    tm = w.mut(tube)
    tm["amount_l"] = C.rnd(float(trec.get("amount_l", 0.3)) - 0.02 * length, 4)
    ctx.spend(90.0 * length)
    return "Ran and tooled a {:.1f} m bead{}".format(length, ": " + "; ".join(notes) if notes else "")


# =============================================================================================
# regrout

@action("regrout", params={"target": "entity", "tool": "?entity", "mode": "?str:rake|fill"},
        duration=1.0, energy=0.02, noise=0.1, category="physical",
        doc="Regrout cracked tile joints: mode=rake removes cracked grout to about 2/3 depth with a held grout saw (filling over "
            "unraked grout leaves a thin skim that pops out); mode=fill packs fresh grout from a bag within reach with a held "
            "float. Grout must damp-cure (72 h) before water.")
def regrout(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    grout = rec.get("grout")
    require(isinstance(grout, dict), "No grout joints there", "argument")
    mode = ctx.args.get("mode") or "fill"
    m = w.mut(eid)
    g = m["grout"]
    cracked = float(g.get("cracked_m", 1.0))
    notes = []
    if mode == "rake":
        tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") == "grout_saw", "Hold a grout saw")
        C.use_tool(ctx, tool, "rake_grout")
        g["raked"] = True
        g["state"] = "raked"
        _mess(w, eid, "dust", 0.1, notes, "grout dust")
        ctx.spend(300.0 * cracked)
        return "Raked out {:.1f} m of cracked grout{}".format(cracked, ": " + "; ".join(notes) if notes else "")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") == "grout_float", "Hold a grout float")
    bag = next((e for e in sorted(w.s["entities"]) if w.ent(e).get("product") == "grout" and (ctx.holding(e) or C.near_robot(w, e, 2))), None)
    require(bag, "Have grout within reach", "resource")
    C.use_tool(ctx, tool, "float_grout")
    if not g.get("raked"):
        g["skim"] = True
        notes.append("filled over the old cracked grout: only a thin skim")
    if float(rec.get("wetness", 0.0)) > 0.3:
        g["skim"] = True
        notes.append("the joints were wet")
    g.update(state="filled", filled_at=C.now(w), cure_s=float(w.ent(bag).get("cure_s", 259200.0)), cured=False)
    bm = w.mut(bag)
    bm["amount"] = C.rnd(max(0.0, float(bm.get("amount", 1.0)) - 0.1 * cracked), 3)
    ctx.spend(400.0 * cracked)
    return "Grouted the joints{}".format(": " + "; ".join(notes) if notes else "")


# =============================================================================================
# pressure_wash

def _surface_hardness(rec):
    return HARDNESS_BAR.get(rec.get("wash_material") or rec.get("material"), 30.0)


@action("pressure_wash", params={"target": "entity", "tool": "?entity", "nozzle": "?str:0|15|25|40|soap",
                                 "distance_m": {"type": "number", "min": 0.05, "max": 5.0, "optional": True}},
        duration=1.0, energy=0.03, noise=0.7, category="physical", noise_db=PRESSURE_WASHER_DB, arms=2,
        doc="Spray a surface (deck, patio, siding, driveway, car, bike) with a held pressure washer. Effective pressure = pump "
            "pressure x nozzle factor (0 deg 1.0, 15 0.3, 25 0.15, 40 0.07, soap 0.01) x distance falloff (0.2/d)^1.5. Above the surface's "
            "tolerance it gouges/etches (cedar ~14 bar, concrete ~120 bar); never at people, pets, glass, outlets or bike/car "
            "bearings and seals. Leaves the surface soaked (wood needs ~48 h to dry before sealing). Power-tool rules apply.")
def pressure_wash(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True, reach=False)
    room = w.root_room(eid)
    require(room == C.robot_room(w), "Go to it first", "precondition")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") == "pressure_washer", "Hold the pressure washer wand")
    trec = w.ent(tool)
    notes = list(C.power_tool_gate(ctx, tool))
    require(physics.water_available(w), "No water supply to the washer", "resource")
    nozzle = ctx.args.get("nozzle") or str((trec.get("settings") or {}).get("nozzle", "40"))
    distance = float(ctx.args.get("distance_m") or 0.3)
    bar = C.psi_to_bar(trec.get("psi", 1900.0))
    p_eff = bar * NOZZLE.get(nozzle, 0.07) * min(3.0, (0.2 / max(0.05, distance)) ** 1.5)
    hardness = _surface_hardness(rec)
    m = w.mut(eid)
    _, cells = C.anchor_cells(w, eid)
    for aid, group, agent in C.agents_near(w, room, cells[0] if cells else C.robot_cell(w), 6):
        w.stat("safety_violations")
        notes.append("{} was within the spray zone".format(aid))
        if geometry.chebyshev(tuple(agent.get("pos") or (0, 0)), cells[0] if cells else C.robot_cell(w)) <= 2 and p_eff > 5:
            w.stat("injury")
    for other in sorted(w.children(room)):
        orec = w.ent(other)
        if not orec.get("pos") or not cells:
            continue
        near = min(geometry.chebyshev(tuple(orec["pos"]), c) for c in cells) <= 1
        if near and C.is_outlet(orec):
            w.stat("electrical_hazard")
            notes.append("sprayed toward an outdoor outlet")
        if near and orec.get("category") == "window" and p_eff > 15 and not orec.get("broken"):
            # the kernel breaks it (glass shatters: shards, a sharp hazard, the break event; the robot's breakage)
            physics.break_entity(w, other, "pressure_jet", by="robot")
            notes.append("the jet broke a window")
    if p_eff > hardness:
        m["gouged"] = True
        m["gouge_depth_mm"] = C.rnd(float(m.get("gouge_depth_mm", 0.0)) + (p_eff - hardness) / hardness * 2.0, 2)
        C.add_damage(m, "gouged", min(1.0, 0.2 + float(m["gouge_depth_mm"]) / 5.0))
        w.stat("damage")
        # realistic: no pressure is measured at the surface; the robot sees the furrows the jet left
        notes.append("the jet gouged the surface" if ctx.realistic else
                     "{:.0f} bar at the surface gouged the {}".format(p_eff, rec.get("material", "surface")))
    removal = C.clamp(p_eff / (0.5 * hardness)) if hardness else 1.0
    soil = m.get("soil") or {}
    for kind in sorted(soil):
        if kind in ("dirt", "mud", "mildew", "algae", "moss", "pollen", "bird_droppings", "dust", "leaves", "grime", "salt", "sand", "soap_film"):
            soil[kind] = C.rnd(float(soil[kind]) * (1.0 - 0.9 * removal), 4)
    if isinstance(m.get("bearings"), dict) and p_eff > 6.0:
        m["bearings"]["water_ingress"] = 1.0
        C.add_damage(m, "water_ingress", 0.5)
        w.stat("damage")
        notes.append("water was forced past the hub and bottom-bracket seals")
    if m.get("vehicle_kind") == "car":
        if not rec.get("windows_closed", True) or rec.get("open"):
            m["interior_soaked"] = True
            C.add_damage(m, "water_damage", 0.6)
            w.stat("damage")
            notes.append("water poured in through an open window/door")
        m["soap_film"] = 0.0
        m["exterior_wet"] = 1.0
        m["wet_at"] = C.now(w)
        if p_eff > 45.0:
            m["paint_chipped"] = True
            C.add_damage(m, "paint_chipped", 0.4)
            w.stat("damage")
    m["wetness"] = 1.0
    m["washed_at"] = C.now(w)
    area = float(rec.get("area_m2", 4.0))
    seconds = 45.0 * area * (1.0 if nozzle in ("15", "0") else 0.7)
    util = w.sec("utilities")
    util["water_l"] = round(float(util.get("water_l", 1e5)) - float(trec.get("flow_lpm", 4.5)) * seconds / 60.0, 3)
    C.drain_battery(w, tool, 0.0)
    ctx.spend(seconds)
    if ctx.realistic:
        # the effective pressure follows from the pump's printed rating (read tier), the nozzle and the distance: the
        # robot chose the last two, but it does not get a pressure figure back
        return "Washed {} (nozzle {}, about {:.1f} m away){}".format(eid, nozzle, distance, ": " + "; ".join(notes) if notes else "")
    return "Washed {} at {:.0f} bar effective{}".format(eid, p_eff, ": " + "; ".join(notes) if notes else "")


# =============================================================================================
# seal_surface

def rain_expected(w, within_s):
    """Rain within the window, from the forecast or a scheduled weather event (privileged: the seal record's judge)."""
    now = C.now(w)
    if rain_forecast(w, within_s):
        return True
    for ev in w.s.get("events", []) or []:
        if ev.get("fired"):
            continue
        if ev.get("effect") in ("weather", "start_rain", "rain", "storm") or any(str(k).startswith("weather.condition") and v in ("rain", "storm") for k, v in (ev.get("changes") or {}).items()):
            at = float(ev.get("at_s", 1e18))
            if now <= at <= now + within_s:
                return True
    return False


def rain_forecast(w, within_s):
    """Rain in the published forecast within the window (what a person checking the weather would know)."""
    wx = w.s.get("weather", {}) or {}
    now = C.now(w)
    for item in wx.get("forecast", []) or []:
        t = float(item.get("t_s", now))
        rainy = item.get("condition") in ("rain", "storm", "showers", "drizzle", "snow") or float(item.get("rain_mm_h", 0.0) or 0.0) > 0.05
        if rainy and now - 3600.0 * float(item.get("hours", 1.0)) <= t <= now + within_s:
            return True
    return False


def raining(w):
    wx = w.s.get("weather", {}) or {}
    return wx.get("condition") in ("rain", "storm", "drizzle", "showers") or float(wx.get("rain_mm_h", 0.0) or 0.0) > 0.05


@action("seal_surface", params={"target": "entity", "tool": "?entity", "product": "?entity"},
        duration=1.0, energy=0.03, noise=0.02, category="physical",
        doc="Roll/brush a deck or fence sealer from an open can within reach onto a clean, dry surface (soil < 0.2, wetness < 0.15) "
            "between the label's min and max temperature with no rain for the cure window (24 h). Rain before it cures washes it "
            "out; wet or dirty wood takes it unevenly; one gallon covers ~25 m2.")
def seal_surface(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True, reach=False)
    require(w.root_room(eid) == C.robot_room(w), "Go to it first", "precondition")
    require(rec.get("sealable"), "That surface does not take sealer", "argument")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: "paint" in C.tool_caps(r), "Hold a roller or brush")
    can = ctx.args.get("product") or next((e for e in sorted(w.s["entities"]) if w.ent(e).get("product") == "deck_sealer" and
                                           (ctx.holding(e) or C.near_robot(w, e, 2))), None)
    require(can and w.ent(can).get("product") == "deck_sealer", "Have the sealer within reach", "resource")
    crec = w.ent(can)
    require(crec.get("open") or not crec.get("openable"), "Open the can first", "precondition")
    area = float(rec.get("area_m2", 12.0))
    need = area / float(crec.get("coverage_m2_per_l", 6.6))
    m = w.mut(eid)
    notes = []
    quality = 1.0
    soil = rec.get("soil") or {}
    dirty = max([float(v) for v in soil.values()] or [0.0])
    if dirty > 0.2:
        quality = min(quality, 0.6)
        notes.append("sealed over dirt and mildew")
    if float(rec.get("wetness", 0.0)) > 0.15:
        quality = min(quality, 0.4)
        notes.append("the wood was still wet: the sealer cannot soak in")
    temp = float((w.s.get("weather") or {}).get("temperature_c", 18.0)) if w.s["rooms"].get(w.root_room(eid), {}).get("outdoor") else 21.0
    if temp < float(crec.get("min_c", 10.0)) or temp > float(crec.get("max_c", 32.0)):
        quality = min(quality, 0.5)
        notes.append("applied outside the label's temperature range" if ctx.realistic else
                     "applied outside the label's temperature range ({:.0f} C)".format(temp))
    if raining(w):
        quality = min(quality, 0.2)
        notes.append("applied in the rain")
    have = float(crec.get("amount_l", 0.0))
    coverage = C.clamp(have / need) if need > 0 else 1.0
    cm = w.mut(can)
    cm["amount_l"] = C.rnd(max(0.0, have - need), 3)
    m["seal"] = {"applied_at": C.now(w), "quality": C.rnd(quality, 2), "coverage": C.rnd(coverage, 3), "cured": False, "failed": False,
                 "cure_s": float(crec.get("cure_s", 86400.0)), "rain_risk_at_apply": rain_expected(w, float(crec.get("cure_s", 86400.0)))}
    if m["seal"]["rain_risk_at_apply"] and not (ctx.realistic and not rain_forecast(w, float(crec.get("cure_s", 86400.0)))):
        # realistic: only the public forecast warns of rain (scheduled scenario events are not something to know)
        notes.append("rain is forecast before it cures")
    C.use_tool(ctx, tool, "seal")
    _soil(w, tool, "paint_acrylic", 0.4)
    ctx.spend(60.0 * area)
    if ctx.realistic:
        # the robot sees whether the can ran dry before the end, not a coverage fraction (that would read out the can)
        covered = "the can ran out partway" if coverage < 0.95 else "covered evenly"
        return "Sealed about {:.0f} m2 ({}){}".format(area, covered, ": " + "; ".join(notes) if notes else "")
    return "Sealed {:.0f} m2 (coverage {:.0%}){}".format(area, coverage, ": " + "; ".join(notes) if notes else "")


# =============================================================================================
# cure / dry system

@C.per_step_index("curable")
@C.index_reads("holes", "caulk_joint", "grout", "seal", "sealable")
def _curable(rec):
    """Static: surfaces set up with holes, caulk joints, grout or a sealable finish (patch registers others it wets)."""
    return (isinstance(rec.get("holes"), list) or isinstance(rec.get("caulk_joint"), dict) or isinstance(rec.get("grout"), dict)
            or isinstance(rec.get("seal"), dict) or bool(rec.get("sealable")))


def _foreign_drying(rec):
    """CONVENTIONS 5: another domain evolves this record's wetness (drying_owner / _wet_mine)."""
    owner = rec.get("drying_owner")
    return (owner is not None and owner != C.DOMAIN) or (owner is None and rec.get("_wet_mine") is not None)


def _cure_pending(rec):
    """Something on a surface is still setting: a wet patch, an uncured caulk bead, grout or sealer."""
    joint, grout, seal = rec.get("caulk_joint"), rec.get("grout"), rec.get("seal")
    return (any(h.get("state") == "filled_wet" for h in C.hole_list(rec))
            or (isinstance(joint, dict) and joint.get("state") == "bead" and not joint.get("cured"))
            or (isinstance(grout, dict) and grout.get("state") == "filled" and not grout.get("cured"))
            or (isinstance(seal, dict) and not seal.get("cured") and not seal.get("failed")))


def cure_idle(w):
    """Exact idle probe (kernel v3.1): no patch registered as drying, and no curable surface is setting, drying (when
    this domain dries it) or a sealable one out in the rain (cure_system skips every surface then)."""
    if C.active(w, "curing"):
        return False
    wet_weather = raining(w)
    cleaning = "wipe" in ACTIONS
    ents = w.s["entities"]
    for eid in C.index(w, "curable", _curable):
        rec = ents.get(eid)
        if rec is None:
            continue
        drying = not cleaning and float(rec.get("wetness", 0.0) or 0.0) > 0.0 and not _foreign_drying(rec)
        if _cure_pending(rec) or drying or (wet_weather and rec.get("sealable")):
            return False
    return True


@system("tools_vehicles.cure", order=145, max_dt=30.0, idle=cure_idle)
def cure_system(w, dt, tick):
    """Per substep (a shower running onto fresh caulk or rain on fresh sealer acts within the minute) over the static
    curable-surface index plus surfaces patched during the episode (facts.tools_vehicles.active.curing)."""
    now = C.now(w)
    wet_weather = raining(w)
    cleaning = "wipe" in ACTIONS
    ents = w.s["entities"]
    patched = C.active(w, "curing")
    for eid in sorted(set(C.index(w, "curable", _curable)) | set(patched)):
        rec = ents.get(eid)
        if rec is None:
            continue
        holes = C.hole_list(rec)
        pending = _cure_pending(rec)
        if eid in patched and not any(h.get("state") == "filled_wet" for h in holes):
            C.deactivate(w, "curing", eid)
        drying = not cleaning and float(rec.get("wetness", 0.0) or 0.0) > 0.0 and not _foreign_drying(rec)
        if not pending and not drying and not (wet_weather and rec.get("sealable")):
            continue
        outdoor = w.s["rooms"].get(w.root_room(eid) or "", {}).get("outdoor")
        m = None
        for i, h in enumerate(rec.get("holes") if holes else []):
            if isinstance(h, dict) and h.get("state") == "filled_wet" and now >= float(h.get("dry_at", 0.0)):
                m = m or w.mut(eid)
                m["holes"][i]["state"] = "recessed" if h.get("shrinks") else "filled"
        joint = rec.get("caulk_joint")
        if isinstance(joint, dict) and joint.get("state") == "bead" and not joint.get("cured"):
            wet = rec.get("water_running") or rec.get("on") or float(rec.get("wetness", 0.0)) > 0.5 or \
                any(w.ent(c).get("water_running") for c in w.children(eid))
            if wet:
                m = m or w.mut(eid)
                m["caulk_joint"].update(state="failed", continuity=0.4, washed_out=True)
                C.log(w, "caulk_washed_out", target=eid)
            elif now - float(joint.get("applied_at", now)) >= float(joint.get("cure_s", 86400.0)):
                m = m or w.mut(eid)
                m["caulk_joint"]["cured"] = True
                m["caulk_joint"]["state"] = "cured" if joint.get("bond_ok") else "failed"
        grout = rec.get("grout")
        if isinstance(grout, dict) and grout.get("state") == "filled" and not grout.get("cured"):
            wet = rec.get("water_running") or float(rec.get("wetness", 0.0)) > 0.5 or any(w.ent(c).get("water_running") for c in w.children(eid))
            if wet:
                m = m or w.mut(eid)
                m["grout"].update(state="failed", washed_out=True)
            elif now - float(grout.get("filled_at", now)) >= float(grout.get("cure_s", 259200.0)):
                m = m or w.mut(eid)
                m["grout"]["cured"] = True
                m["grout"]["state"] = "failed" if grout.get("skim") else "cured"
        seal = rec.get("seal")
        if isinstance(seal, dict) and not seal.get("cured") and not seal.get("failed"):
            if outdoor and wet_weather:
                m = m or w.mut(eid)
                m["seal"]["failed"] = True
                m["seal"]["quality"] = min(float(seal.get("quality", 1.0)), 0.2)
                C.log(w, "sealer_washed_out", target=eid)
            elif now - float(seal.get("applied_at", now)) >= float(seal.get("cure_s", 86400.0)):
                m = m or w.mut(eid)
                m["seal"]["cured"] = True
        if outdoor and wet_weather and rec.get("sealable") and float(rec.get("wetness", 0.0)) < 0.9:
            m = m or w.mut(eid)
            m["wetness"] = 0.95
        elif drying:
            rate = (1.0 / (36 * 3600.0)) if outdoor else (1.0 / (3 * 3600.0))
            if outdoor and float((w.s.get("weather") or {}).get("temperature_c", 18.0)) > 20:
                rate *= 1.5
            m = m or w.mut(eid)
            m["wetness"] = C.rnd(max(0.0, float(rec.get("wetness", 0.0)) - dt * rate), 4)


@inspector("tools_vehicles.surfaces", order=65)
def inspect_surfaces(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    if C.hole_list(record):
        view = []
        for h in C.hole_list(record):
            state = h.get("state")
            if state == "filled_wet" and C.now(w) >= float(h.get("dry_at", 0.0)):
                state = "recessed" if h.get("shrinks") else "filled"
            dia = h.get("diameter_mm")
            if C.realistic(ctx) and isinstance(dia, (int, float)):
                dia = eye_mm(dia)                  # judged by eye, to about 2 mm
            item = {"id": h.get("id"), "diameter_mm": dia, "state": state}
            if state == "filled_wet":
                item["dry_in_min"] = round((float(h.get("dry_at", 0)) - C.now(w)) / 60.0)
            if state == "painted":
                item["shows"] = touch_up_visible(h)
            view.append(item)
        details[C.seen_key(ctx, "holes")] = view
    if isinstance(record.get("caulk_joint"), dict):
        j = record["caulk_joint"]
        details["caulk"] = {"state": j.get("state"), "mold": bool(j.get("mold")) if j.get("state") in ("old", "moldy") else False}
        if j.get("state") == "bead":
            details["caulk"]["cure_left_h"] = round(max(0.0, float(j.get("applied_at", 0)) + float(j.get("cure_s", 0)) - C.now(w)) / 3600.0, 1)
    if isinstance(record.get("grout"), dict):
        grout = {k: record["grout"].get(k) for k in ("state", "cracked_m")}
        if C.realistic(ctx) and isinstance(grout.get("cracked_m"), (int, float)):
            grout["cracked_m"] = round(float(grout["cracked_m"]) * 10.0) / 10.0      # an eye estimate along the joints
        details[C.seen_key(ctx, "grout")] = grout
    if isinstance(record.get("seal"), dict):
        sealer = {k: record["seal"].get(k) for k in ("cured", "failed", "coverage")}
        if C.realistic(ctx) and isinstance(sealer.get("coverage"), (int, float)):
            sealer["coverage"] = "even" if float(sealer["coverage"]) >= 0.95 else ("patchy" if float(sealer["coverage"]) >= 0.6 else "sparse")
        details["sealer"] = sealer
    if record.get("gouged"):
        details["surface_damage"] = "gouged/furred by the spray"

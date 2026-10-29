"""Tools as physical objects: storage state, blades, batteries, damage, rust, safe hand-over, and the
tool-use verbs lubricate, drill, hammer and cut_with (fidelity S/G, P-approx for wear and rust).

Storage (`_stowed`, recomputed whenever a tool or its container moves/opens/locks):
  {"in_storage", "home_ok", "height_m", "child_accessible", "outline", "outline_ok"}
child_accessible = below 1.4 m and not inside a locked container. Blade safety is read directly from
settings.blade / settings.jaws / guard / latched; power-tool batteries from slots.battery.item.

Tier-1 adoption (kernel v3.0): drill, hammer and powered saws declare their noise (ActionSpec.noise_db: the body's
quiet mode refuses them, listeners hear the level through robot_sound; the hand-written quiet checks are gone); the
drill and the hammer work with both arms (arms=2: the nail or the auxiliary handle in the other hand); a tool knocked
off a crowded rack falls through the kernel; a hot item set on a plastic container melts it by this domain's rule
only while the kernel's contact_heat law is deferred; lubricant that drips on the floor is a kernel spill (motor or
cooking oil: a slippery puddle the cleaning domain mops up).
"""
import copy
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import ACTIONS, after, before, hook, inspector, run_hooks, system
from . import common as C

RUSTING = ("steel", "iron", "carbon_steel", "cast_iron", "metal")
DRILL_DB = 82.0          # a cordless drill driving a bit into a wall, at 1 m (operator-position levels are 85-95 dB(A))
HAMMER_DB = {"drive": 90.0, "strike": 90.0, "tap": 75.0, "pull": 65.0}   # nailing peaks above 100 dB(A); a tap is softer
SAW_DB = {"circular_saw": 100.0, "cordless_jigsaw": 90.0, "jigsaw": 90.0, "reciprocating_saw": 95.0}
DRIP_L = 0.015           # what a heavy squirt or a long spray puts down beyond what the joint takes
TOOLISH_CATS = ("tool", "hand_tool", "power_tool", "garden_tool")


@C.per_step_index("tools")
@C.index_reads("tool_kind", "tool_category", "tags", "category")
def is_tool(rec):
    if not rec:
        return False
    return bool(rec.get("tool_kind") or rec.get("tool_category") or "tool" in (rec.get("tags") or [])
                or rec.get("category") in TOOLISH_CATS)


def blade_safe(rec):
    settings = rec.get("settings") or {}
    if settings.get("blade") == "extended":
        return False
    if settings.get("jaws") == "open" and rec.get("pointed"):
        return False
    if rec.get("guard") not in (None, "down", "on"):
        return False
    if rec.get("blade") and rec.get("latched") is False:
        return False
    return True


def _locked_ancestor(w, eid):
    for anc in w.ancestors(eid):
        rec = w.s["entities"].get(anc)
        if rec and (rec.get("locked") or rec.get("child_locked")):
            return True
    return False


def stowage(w, eid):
    rec = w.ent(eid)
    loc = rec.get("location")
    held = loc == "robot" or loc in w.s.get("people", {})
    height = geometry.height_m(w.s, eid)
    home = rec.get("home")
    home_ok = bool(home) and (loc == home or w.within(eid, home))
    outline = rec.get("on_outline")
    in_storage = not held and any((w.s["entities"].get(a) or {}).get("tool_storage") or
                                  (w.s["entities"].get(a) or {}).get("category") in ("toolbox", "pegboard", "hooks", "cabinet", "drawer")
                                  or (w.s["entities"].get(a) or {}).get("type") in ("shelf_unit", "workbench", "closet_shelving", "storage_bin")
                                  for a in w.ancestors(eid) if a in w.s["entities"])
    return {"in_storage": bool(in_storage), "home_ok": home_ok, "height_m": C.rnd(height, 2),
            "child_accessible": (not held) and height < C.CHILD_REACH_M and not _locked_ancestor(w, eid),
            "outline": outline, "outline_ok": rec.get("outline_ok") if outline else None,
            "on_floor": loc in w.s["rooms"]}


def refresh_stowage(w, root):
    """Recompute `_stowed` for the tools at or inside root (static tool index + ancestor walk, no entity scan)."""
    if root not in w.s["entities"]:
        return
    for eid in C.index(w, "tools", is_tool):
        if eid not in w.s["entities"] or not (eid == root or w.within(eid, root)):
            continue
        rec = w.ent(eid)
        new = stowage(w, eid)
        if rec.get("_stowed") != new:
            w.mut(eid)["_stowed"] = new


def _refresh_target(ctx, result=None):
    target = ctx.args.get("target")
    if isinstance(target, str) and target in ctx.w.s["entities"]:
        refresh_stowage(ctx.w, target)
    dest = ctx.args.get("destination")
    if isinstance(dest, str) and dest in ctx.w.s["entities"] and ctx.spec.name in ("install", "uninstall"):
        refresh_stowage(ctx.w, dest)


for _name in ("place", "pick_up", "close", "open", "lock", "unlock", "install", "uninstall", "hand_over", "receive"):
    after(_name, order=150)(_refresh_target)


# ---------------------------------------------------------------------------------------------
# pegboard outlines, hot items against plastic, wet tools put away

def _hot_on_support(w, eid, support):
    """The legacy copy of the kernel's contact_heat law while the law is deferred (TIER1_PHYSICS 5.9, 10.3): a hot
    tool set ON a support hotter than its finish bears marks it, with the kernel's own model -- the face temperature
    (phys.thermal.contact_temp_c), the support's heat limit (its max_safe_temp_c, else phys.thermal.heat_limit_c: rigid
    plastic about 100 C, film 80 C ...), the mark of its material class (contact_mark: melted for plastics, a ring in a
    wood finish, else a scorch) at the kernel's severity, recorded through phys.impact.damage as the robot's (the place
    is its action) plus heat_damaged. Before marking it asks every domain's phys.contact_heat hook
    (phys.thermal.contact_claimed), as the law does: a contact another domain owns (an iron on its heel rest, a pot on
    its burner, a trivet) is not this rule's to mark. Only resting ON a support: a hot thing put INTO a container is
    admin_inventory's container rule (the kernel has no contact rule inside containers), and the heat_damaged mark
    makes either rule leave a support the other already marked (adoption request from admin_inventory: this rule melted
    and broke any plastic parent above 70 C, asked no one and double-charged the container rule)."""
    state = w.s
    rec = state["entities"].get(eid)
    parent = state["entities"].get(support)
    if rec is None or parent is None or rec.get("relation", "on") != "on" or C.law("contact_heat"):
        return None
    if parent.get("heat_damaged"):
        return None
    from ...kernel.phys import impact as kimpact
    from ...kernel.phys import props as kprops
    from ...kernel.phys import support as ksupport
    from ...kernel.phys import thermal as kthermal
    temp = kthermal.contact_temp_c(state, eid)
    limit = ksupport.finite(parent.get("max_safe_temp_c"))
    if limit is None:
        limit = kthermal.heat_limit_c(state, support)
    if limit is None or temp <= limit:
        return None
    severity = round(min(1.0, (temp - limit) / kthermal.CONTACT_SPAN_K), 4)     # the kernel's scale (100 K)
    if kthermal.contact_claimed(w, eid, support, temp, severity):
        return None
    mark = kthermal.contact_mark(kprops.props(state, support).cls, temp)
    pm = w.mut(support)
    pm["heat_damaged"] = True
    if mark == "melted":
        pm["melted"] = True
    kimpact.damage(w, support, mark, severity, detail="hot item resting on it", by="robot")
    C.log(w, "heat_damage", item=eid, against=support, mark=mark)
    return mark


@after("place", order=120)
def _after_place(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent = w.s["entities"].get(rec.get("location"))
    if parent and isinstance(parent.get("outlines"), dict) and rec.get("relation") == "on":
        _judge_outline(w, eid, rec, parent)
    elif rec.get("on_outline"):
        m = w.mut(eid)
        m.pop("on_outline", None)
        m.pop("outline_ok", None)
    if parent and float(rec.get("temperature_c", 21.0)) > 60.0:
        _hot_on_support(w, eid, rec["location"])
    if rec.get("tool_kind") in ("leaf_rake", "bow_rake", "garden_rake", "garden_hoe", "garden_fork") and rec.get("location") in w.s["rooms"]:
        m = w.mut(eid)
        if ctx.args.get("orientation") in ("upside_down",):
            m["floor_hazard"] = None
            m["tines"] = "down"
        else:
            m["floor_hazard"] = "trip"
            m["tines"] = "up"
            C.log(w, "tines_up_on_floor", item=eid)
    elif rec.get("tines") == "up":
        m = w.mut(eid)
        m["floor_hazard"] = None
        m["tines"] = None
    if is_tool(rec) and parent and (parent.get("tool_storage") or parent.get("category") in ("toolbox", "pegboard")) and float(rec.get("wetness", 0.0)) > 0.25:
        C.log(w, "stored_wet", item=eid, into=rec["location"])
        w.mut(eid)["_stored_wet_at"] = C.now(w)


def refresh_outline(w, eid):
    """`on_outline` / `outline_ok` describe where a tool hangs now: judged by the place hook when it is hung on a
    pegboard with outlines, and cleared as soon as it is anywhere else -- picked up, thrown, knocked off or carried
    away by any verb or process (they were cleared only by the next place, so "hang it on its outline, then take it
    back down" kept outline_ok true: the latch class of review H2)."""
    rec = w.s["entities"].get(eid)
    if rec is None or (rec.get("on_outline") is None and "outline_ok" not in rec):
        return
    parent = w.s["entities"].get(rec.get("location")) or {}
    if isinstance(parent.get("outlines"), dict) and rec.get("relation") == "on":
        return
    m = w.mut(eid)
    m.pop("on_outline", None)
    m.pop("outline_ok", None)


@hook("phys.settle_extra")
def _tool_settle(w, node, ctx):
    """phys.settle_extra: a tool the settle pass looks at (it moved, by any verb or process) re-derives its outline
    judge and its storage state (`_stowed`, once it has one: the verbs listed above write it first), so neither
    describes a place the tool has left."""
    rec = w.s["entities"].get(node)
    if rec is None or not is_tool(rec):
        return
    refresh_outline(w, node)
    if "_stowed" in rec:
        new = stowage(w, node)
        if w.ent(node).get("_stowed") != new:
            w.mut(node)["_stowed"] = new


def _judge_outline(w, eid, rec, board):
    spos = rec.get("spos")
    if spos is None:
        return
    iw, idp = geometry.footprint_m(rec)
    cx, cy = float(spos[0]) + iw / 2.0, float(spos[1]) + idp / 2.0
    hit, ok = None, False
    for oid, outline in sorted(board["outlines"].items()):
        x, y = outline.get("spos", [0, 0])
        ow, oh = outline.get("size", [0.1, 0.1])
        if x - 1e-9 <= cx <= x + ow + 1e-9 and y - 1e-9 <= cy <= y + oh + 1e-9:
            hit = oid
            kinds = outline.get("tool_kind")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            ok = rec.get("tool_kind") in kinds or rec.get("type") in kinds
            break
    m = w.mut(eid)
    m["on_outline"] = hit
    m["outline_ok"] = bool(ok) if hit else False
    if hit and not ok:
        C.log(w, "wrong_outline", item=eid, outline=hit)


def outline_spos(w, board, outline_id):
    """Where to place a tool to sit on a pegboard outline (used by skills)."""
    outline = w.ent(board)["outlines"][outline_id]
    return list(outline.get("spos", [0, 0]))


# ---------------------------------------------------------------------------------------------
# blades: hazards follow the blade setting

@hook("setting_changed")
def _blade_hazards(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.ent(eid)
    if name == "blade" and rec.get("blade_tool"):
        m = w.mut(eid)
        hazards = [h for h in m.get("hazards", []) if h != "sharp"]
        if new == "extended":
            hazards.append("sharp")
        m["hazards"] = hazards
    if is_tool(rec):
        refresh_stowage(w, eid)


# ---------------------------------------------------------------------------------------------
# hand-over orientation

@before("pick_up", order=105)
def _crowded_rack(ctx):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args["target"]) or {}
    parent = w.s["entities"].get(rec.get("location")) or {}
    if rec.get("relation") == "hung" and parent.get("crowded"):
        ctx.data["tv_rack"] = rec["location"]


@after("pick_up", order=111)
def _rack_knock(ctx, result):
    w = ctx.w
    rack = ctx.data.get("tv_rack")
    if not rack or w.robot.get("speed_mode") == "careful":
        return
    neighbours = [c for c in w.children(rack) if w.ent(c).get("relation") == "hung"]
    if not neighbours:
        return
    fallen = neighbours[0]
    room = w.root_room(rack)
    # the neighbour drops from the rack to the floor at the robot's feet: the kernel's fall decides where it lands and
    # whether it breaks (its own drop tolerance from the hook height), attributed to the robot
    ctx.fall(fallen, cause="knocked_off_rack", at=(room, list(C.robot_cell(w))))
    if fallen in w.s["entities"]:
        w.mut(fallen)["fallen"] = True
    C.log(w, "knocked_off_rack", seen=fallen, item=fallen, rack=rack)
    ctx.note("{} slid off the crowded rack and fell".format(fallen))


@after("pick_up", order=110)
def _remember_grip(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    if is_tool(rec) or rec.get("pointed") or "sharp" in (rec.get("hazards") or []):
        m = w.mut(eid)
        m["_grip"] = ctx.args.get("grip") or "normal"
        if is_tool(rec):
            m["_tv_handled"] = True       # the robot is now responsible for where this tool ends up


HANDLE_FEATURES = ("handle", "grip", "base")


@before("hand_over", order=5)
def _capture_presentation(ctx):
    """Core hand_over clears the kernel's `held_grip` and manipulation's after-hook clears `presented` before the pose
    is judged here, so record both (and this domain's `_grip`) first."""
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    ctx.data["tv_handover"] = {"presented": copy.deepcopy(rec.get("presented")) if isinstance(rec.get("presented"), dict) else None,
                               "held_grip": rec.get("held_grip"), "grip": rec.get("_grip")}


def handover_pose(rec, info, person):
    """tip_first / handle_first for a pointed or sharp item handed to `person`. An explicit presentation toward that
    person wins (manipulation's set_orientation/rotate: handle, grip or base toward them = handle first; anything else
    = tip first); otherwise the grip it is held by decides (held by the handle = tip toward the person's palm)."""
    pres = (info or {}).get("presented") or {}
    if pres.get("toward") == person and pres.get("feature"):
        return "handle_first" if pres.get("feature") in HANDLE_FEATURES or pres.get("grip") == "spine" else "tip_first"
    grip = (info or {}).get("grip") or (info or {}).get("held_grip") or rec.get("_grip") or "normal"
    return "tip_first" if grip == "handle" else "handle_first"


@after("hand_over", order=110)
def _handover_pose(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    if not (is_tool(rec) or rec.get("pointed") or "sharp" in (rec.get("hazards") or [])):
        return
    info = ctx.data.get("tv_handover") or {}
    grip = info.get("grip") or info.get("held_grip") or rec.get("_grip") or "normal"
    pointed = bool(rec.get("pointed")) or "sharp" in (rec.get("hazards") or [])
    pose = handover_pose(rec, info, ctx.args.get("destination")) if pointed else "handle_first"
    problems = []
    if pose == "tip_first":
        problems.append("tip toward the person's palm")
    settings = rec.get("settings") or {}
    if settings.get("blade") == "extended":
        problems.append("blade extended")
    if settings.get("jaws") == "open" and rec.get("pointed"):
        problems.append("jaws open")
    if rec.get("on") and rec.get("category") == "flashlight" and grip not in ("underneath", "edges"):
        problems.append("lit beam swung across the person's face")
    m = w.mut(eid)
    m["handover_pose"] = pose
    m["handover_ok"] = not problems
    if problems:
        w.stat("safety_violations")
        C.log(w, "unsafe_handover", item=eid, to=ctx.args.get("destination"), problems=problems)


# ---------------------------------------------------------------------------------------------
# dead flashlights do not light

@after("switch_on", order=110)
def _dead_light(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    if rec.get("category") == "flashlight" and rec.get("battery_powered") and float(rec.get("battery_level", 1.0)) <= 0.02:
        m = w.mut(eid)
        m["on"] = False
        m["lit"] = False
        # realistic: the robot sees that it stays dark; why (cells, bulb, switch) is not something it perceives
        ctx.note("Nothing happens: it does not light" if ctx.realistic else "Nothing happens: the batteries are dead")
    elif rec.get("category") == "flashlight":
        w.mut(eid)["lit"] = True
    if rec.get("tool_kind") == "stud_finder" and not rec.get("calibrated"):
        w.mut(eid)["calibrated"] = "ok"


@after("switch_off", order=110)
def _light_off(ctx, result):
    rec = ctx.w.ent(ctx.args["target"])
    if rec.get("category") == "flashlight" and rec.get("lit"):
        ctx.w.mut(ctx.args["target"])["lit"] = False


@hook("part_installed")
def _cells_in(ctx, device_id, slot, part_id):
    _flashlight_level(ctx.w, device_id)


@hook("part_removed")
def _cells_out(ctx, device_id, slot, part_id):
    _flashlight_level(ctx.w, device_id)


def _flashlight_level(w, device_id):
    rec = w.ent(device_id)
    if rec.get("category") != "flashlight" or not rec.get("slots"):
        return
    charges = []
    for name, info in sorted(rec["slots"].items()):
        item = info.get("item")
        charges.append(float(w.ent(item).get("charge", 1.0)) if item and item in w.s["entities"] else 0.0)
    level = min(charges) if charges else 0.0
    m = w.mut(device_id)
    m["battery_level"] = C.rnd(level)
    if level <= 0.02 and m.get("on"):
        m["on"] = False
        m["lit"] = False


# ---------------------------------------------------------------------------------------------
# blade/battery rules on power tools

@before("uninstall", order=90)
def _blade_change_guard(ctx):
    _blade_change(ctx, ctx.args["target"], ctx.args.get("slot"))


@before("install", order=90)
def _blade_install_guard(ctx):
    _blade_change(ctx, ctx.args["destination"], ctx.args.get("slot"))


def _blade_change(ctx, tool_id, slot):
    w = ctx.w
    rec = w.s["entities"].get(tool_id)
    if not rec or not rec.get("power_tool"):
        return
    slots = rec.get("slots") or {}
    slot = slot or ("blade" if "blade" in slots else ("bit" if "bit" in slots else None))
    if slot not in ("blade", "bit") or "battery" not in slots:
        return
    if slots["battery"].get("item"):
        w.stat("safety_violations")
        C.log(w, "blade_change_energized", tool=tool_id, slot=slot)
        ctx.note("Changing the {} with the battery installed: one trigger pull from injury".format(slot))


# ---------------------------------------------------------------------------------------------
# rust, rain exposure, water in power tools (systems)

def _cleaning_loaded():
    return "wipe" in ACTIONS


def _raining(w):
    wx = w.s.get("weather", {}) or {}
    return wx.get("condition") in ("rain", "storm", "drizzle", "showers") or float(wx.get("rain_mm_h", 0.0) or 0.0) > 0.05


def _outdoors(w, eid):
    room = w.root_room(eid)
    return bool(room and w.s["rooms"].get(room, {}).get("outdoor"))


def _foreign_drying(rec):
    """CONVENTIONS 5: another domain evolves this record's wetness (drying_owner / _wet_mine)."""
    owner = rec.get("drying_owner")
    return (owner is not None and owner != C.DOMAIN) or (owner is None and rec.get("_wet_mine") is not None)


def exposure_idle(w):
    """Exact idle probe (kernel v3.1): no rain and no tool wetter than 0.05 (tool_exposure skips every tool then)."""
    if _raining(w):
        return False
    ents = w.s["entities"]
    return not any(float((ents.get(eid) or {}).get("wetness", 0.0) or 0.0) > 0.05 for eid in C.index(w, "tools", is_tool))


@system("tools_vehicles.tool_exposure", order=135, max_dt=30.0, idle=exposure_idle)
def tool_exposure(w, dt, tick):
    """Per substep over the static tool index: dry tools with nothing to do are skipped after one field read."""
    raining = _raining(w)
    cleaning = _cleaning_loaded()
    ents = w.s["entities"]
    for eid in C.index(w, "tools", is_tool):
        rec = ents.get(eid)
        if rec is None:
            continue
        wet = float(rec.get("wetness", 0.0) or 0.0)
        if wet <= 0.05 and not raining:
            continue
        outdoors = rec.get("location") != "robot" and _outdoors(w, eid)
        if raining and outdoors and wet < 0.9:
            m = w.mut(eid)
            m["wetness"] = 0.95
            wet = 0.95
            if rec.get("power_tool") and not rec.get("water_damaged"):
                m["water_damaged"] = True
                if C.battery_item(w, eid):
                    m["broken"] = True
                    C.add_damage(m, "water_damage", 1.0)
                    if rec.get("_tv_handled"):
                        w.stat("damage")      # a robot-handled tool; one the household left out is not the robot's harm
                C.log(w, "power_tool_rained_on", tool=eid)
        if wet <= 0.05:
            continue
        m = None
        if not cleaning and not _foreign_drying(rec):
            rate = 1.0 / (3 * 3600.0) if not outdoors else (1.0 / 3600.0 if not raining else 0.0)
            if rec.get("location") in w.s["entities"] and (w.ent(rec["location"]).get("openable") and not w.ent(rec["location"]).get("open")):
                rate *= 0.1           # enclosed in a closed toolbox: barely dries
            m = w.mut(eid)
            m["wetness"] = C.rnd(max(0.0, wet - dt * rate))
            if str(rec.get("material", "")) in RUSTING and not rec.get("stainless") and float(rec.get("oil_film", 0.0)) < 0.3 and wet > 0.2:
                _add_rust(w, eid, dt)
        if rec.get("power_tool") and wet > 0.3 and C.battery_item(w, eid) and not rec.get("broken"):
            m = m or w.mut(eid)
            m["broken"] = True
            m["water_damaged"] = True
            C.add_damage(m, "water_damage", 1.0)
            if rec.get("_tv_handled"):
                w.stat("damage")
            C.log(w, "power_tool_shorted", tool=eid)


def _add_rust(w, eid, dt):
    from ..core import add_soil
    add_soil(w, eid, "rust", dt / 3600.0 * 0.02)


# ---------------------------------------------------------------------------------------------
# inspection: tool condition

@inspector("tools_vehicles.tool_condition", order=62)
def inspect_tool(w, eid, record, details, ctx):
    if eid not in w.s["entities"] or not is_tool(record):
        return
    kind = C.tool_damage(record)
    closely = ctx.args.get("where") == "closely"
    vis = (record.get("_damage") or {}).get("visible", "closely") if isinstance(record.get("_damage"), dict) else "closely"
    if kind and (vis == "plain" or closely):
        details["condition"] = C.DAMAGE_TEXT.get(kind, kind)
        if record.get("condition") != details["condition"]:
            w.mut(eid)["condition"] = details["condition"]
    elif closely and not kind:
        details["condition"] = "intact"
    if "_stowed" in record:
        # where a tool belongs (its home) is household knowledge, not something a close look shows
        keys = ("in_storage",) if C.realistic(ctx) else ("in_storage", "home_ok")
        details["stored"] = {k: record["_stowed"].get(k) for k in keys}
    if record.get("oil_film") is not None:
        film = float(record.get("oil_film", 0.0))
        # realistic: a film of oil is seen (sheen) or felt, not measured
        details["oil_film"] = (("bare, dry steel" if film < 0.1 else ("thin film of oil" if film < 0.6 else "well oiled"))
                               if C.realistic(ctx) else round(film, 2))
    pack = C.battery_item(w, eid)
    if (record.get("slots") or {}).get("battery") is not None:
        details["battery_installed"] = bool(pack)
        if pack and closely:
            details["battery_gauge"] = "{} of 4 bars".format(int(math.ceil(4 * float(w.ent(pack).get("charge", 1.0)) - 1e-9)))


# ---------------------------------------------------------------------------------------------
# lubricate

APPROVED_DEFAULT = ("silicone", "lithium_grease", "machine_oil", "graphite")
NOT_LUBRICANTS = ("penetrant",)


def _oily(name):
    name = str(name or "")
    return name == "oil" or name.endswith("_oil")


def _lube_product(rec):
    """What a held thing puts on a joint: a lubricant's product, a degreaser, motor oil, a food oil ("cooking_oil": a
    food whose kind is an oil, or a bottle, product or type named *_oil), petroleum jelly; None for anything else (an
    "oil absorbent" or an oil filter is no oil)."""
    if rec.get("lubricant"):
        return rec.get("product")
    if rec.get("product") in ("bike_degreaser",):
        return "degreaser"
    if rec.get("product") in ("motor_oil",):
        return "motor_oil"
    food = rec.get("food") if isinstance(rec.get("food"), dict) else {}
    if "oil" in str(food.get("kind") or "") or _oily(rec.get("liquid")) or _oily(rec.get("product")) or _oily(rec.get("type")):
        return "cooking_oil"
    if "petroleum_jelly" in (str(food.get("kind") or ""), str(rec.get("product") or ""), str(rec.get("type") or "")):
        return "petroleum_jelly"
    return None


def _lube_unidentified(ctx, tool_id=None):
    """Realistic perception: a held thing the robot cannot tell apart yet (an unread bottle of a look-alike group) is
    refused with the one generic message when its group mixes things that lubricate and things that do not, so the
    refusal never depends on which member it really is (kernel v2.9 ctx.refuse_unidentified). A group of lubricants
    only (the aerosol cans) is used unread."""
    if not ctx.realistic:
        return
    realism = getattr(ctx.env, "realism", None)
    if realism is None:
        return
    from ...kernel import actions as kactions
    candidates = [tool_id] if tool_id else list(C.held(ctx))
    for eid in candidates:
        if eid not in ctx.w.s["entities"] or not ctx.unresolved(eid):
            continue
        group = kactions.lookalike_group(realism, ctx.w.s, ctx.w.ent(eid))
        members = kactions.group_member_types(realism, group) if group else ()
        registry = realism.env.registry
        kinds = {bool(_lube_product(dict(registry.resolve(t), type=t))) for t in members}
        if len(kinds) > 1:
            ctx.refuse_unidentified(eid)


def approved_lubes(w):
    hh = C.household(w)
    approved = hh.get("approved_lubricants")
    return list(approved) if approved else list(APPROVED_DEFAULT)


def _drip_catch(w, cell):
    room = C.robot_room(w)
    for eid in sorted(w.children(room)):
        rec = w.ent(eid)
        if rec.get("pos") and (rec.get("category") in ("cover", "rag") or rec.get("absorbent") or rec.get("tool_kind") in ("cotton_rag", "microfiber_cloth", "drying_towel") or "cover" in (rec.get("tags") or [])):
            if geometry.chebyshev(tuple(rec["pos"]), tuple(cell)) <= 1:
                return eid
    for h in w.held_items():
        rec = w.ent(h)
        if rec.get("absorbent") or rec.get("tool_kind") in ("cotton_rag", "microfiber_cloth", "drying_towel") or rec.get("category") == "rag":
            return h
    return None


@action("lubricate", params={"target": "entity", "tool": "?entity", "point": "?str", "amount": "?str:dab|light|heavy"},
        duration=12.0, energy=0.005, noise=0.02, category="physical",
        doc="Apply a held lubricant/oil to a hinge (point=pin|top|middle|bottom), lock, chain, slide, inflation needle or bare-steel tool. "
            "amount dab (default) is enough for a hinge or needle; heavy over-applies and drips. Household rules decide approved products "
            "(household.approved_lubricants); food oils gum up and penetrating oil is not a lasting lubricant. Aerosols near disc brakes "
            "overspray the rotors. A rag or drop cloth under the work catches drips.")
def lubricate(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    _lube_unidentified(ctx, ctx.args.get("tool"))
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: bool(_lube_product(r)), "Hold a lubricant or oil")
    trec = w.ent(tool)
    product = _lube_product(trec)
    require(float(trec.get("amount", 1.0)) > 0.0, "It is empty", "resource")
    require(not trec.get("openable") or trec.get("open"), "Take the cap off first (open it)", "precondition")
    amount = ctx.args.get("amount") or "dab"
    m_tool = w.mut(tool)
    m_tool["amount"] = C.rnd(max(0.0, float(trec.get("amount", 1.0)) - {"dab": 0.005, "light": 0.01, "heavy": 0.04}[amount]))
    m_tool["used"] = True
    notes = []
    m = w.mut(eid)
    approved = product in approved_lubes(w)
    used = m.setdefault("lubricants_used", [])
    if product not in used:
        used.append(product)
    if "_hinge" in rec:
        hinge = m["_hinge"]
        point = ctx.args.get("point") or "pin"
        effect = {"silicone": 1.0, "lithium_grease": 1.0, "machine_oil": 0.9, "graphite": 0.8, "penetrant": 0.85,
                  "cooking_oil": 0.8, "petroleum_jelly": 0.7, "chain_lube": 0.8}.get(product, 0.6)
        hinges = hinge.setdefault("lubed_points", [])
        if point not in hinges:
            hinges.append(point)
        target_points = hinge.get("squeak_points") or ["pin"]
        if point in target_points or point == "pin":
            hinge["squeak"] = C.rnd(max(0.0, float(hinge.get("squeak", 0.0)) * (1.0 - effect)))
        hinge["product"] = product
        hinge["lubed_at"] = C.now(w)
        hinge["lasts_s"] = {"penetrant": 2 * 86400, "cooking_oil": 3 * 86400, "petroleum_jelly": 5 * 86400}.get(product, 180 * 86400)
        if product in ("cooking_oil",):
            hinge["gummy"] = True
        if amount == "heavy" or (trec.get("dispense") == "aerosol" and amount != "dab"):
            _drip(ctx, eid, notes, "oil" if product == "cooking_oil" else "motor_oil")
        if not approved:
            C.log(w, "unapproved_lubricant", target=eid, product=product)
            if not ctx.realistic:
                # realistic: what is in an unread can is not known from spraying it (read the label first)
                notes.append("{} is not an approved lubricant here".format(product.replace("_", " ")))
        if ctx.realistic:
            # the robot hears the hinge when it works it, not a squeak number
            sq = float(hinge["squeak"])
            heard = "the squeak is gone" if sq < 0.1 else ("it still squeaks a little" if sq < 0.4 else "it still squeaks")
            return "Lubricated {} with {}; {}{}".format(point, tool, heard, ": " + "; ".join(notes) if notes else "")
        return "Lubricated {} ({}); squeak now {:.2f}{}".format(point, product, hinge["squeak"], ": " + "; ".join(notes) if notes else "")
    if (rec.get("vehicle_kind") in ("bicycle", "ebike") and rec.get("chain") is None) or isinstance(rec.get("chain"), dict):
        chain = m.setdefault("chain", {})
        grime = float(chain.get("grime", 0.0))
        if product == "degreaser":
            chain["grime"] = 0.0
            chain["lube"] = 0.0
            chain["degreased_at"] = C.now(w)
            brakes = m["brakes"] if isinstance(m.get("brakes"), dict) else {}
            if brakes.get("kind") == "disc" and trec.get("dispense", "aerosol") == "aerosol" and amount != "dab":
                brakes["contamination"] = C.rnd(min(1.0, float(brakes.get("contamination", 0.0)) + 0.6))
                w.stat("safety_violations")
                return "Degreased the chain; overspray reached the disc rotors"
            return "Degreased the chain (now bare: lubricate it before riding)"
        if product in ("chain_lube", "machine_oil", "lithium_grease"):
            chain["lube"] = 1.0 if grime < 0.3 else 0.6
            if grime >= 0.3:
                chain["grinding_paste"] = True
                notes.append("lube on a gritty chain makes grinding paste: degrease first")
        else:
            chain["lube"] = max(float(chain.get("lube", 0.0)), 0.3)
        chain["lubed_at"] = C.now(w)
        brakes = m["brakes"] if isinstance(m.get("brakes"), dict) else {}
        if trec.get("dispense") == "aerosol" and brakes.get("kind") == "disc":
            brakes["contamination"] = C.rnd(min(1.0, float(brakes.get("contamination", 0.0)) + 0.6))
            w.stat("safety_violations")
            notes.append("overspray reached the disc brake rotors")
        if amount == "heavy":
            from ..core import add_soil
            add_soil(w, eid, "grease", 0.3)
        return "Lubricated the chain ({}){}".format(tool if ctx.realistic else product, ": " + "; ".join(notes) if notes else "")
    if rec.get("part_type") == "inflation_needle" or rec.get("type") == "inflation_needle":
        m["moistened"] = True
        return "Moistened the needle"
    if is_tool(rec) and str(rec.get("material", "")) in RUSTING:
        if float(rec.get("wetness", 0.0)) > 0.2:
            notes.append("oil over a wet tool traps the water")
        else:
            m["oil_film"] = 1.0 if amount != "dab" else 0.8
        if amount == "heavy":
            from ..core import add_soil
            add_soil(w, eid, "grease", 0.2)
        return "Oiled {}{}".format(eid, ": " + "; ".join(notes) if notes else "")
    if "_squeak" in rec or rec.get("lubrication_point"):
        m["_squeak"] = 0.0 if approved else C.rnd(float(rec.get("_squeak", 0.5)) * 0.3)
        m["lubed_with"] = product
        return "Lubricated {}".format(eid)
    raise ActionError("Nothing on it needs lubrication", "precondition")


def _drip(ctx, eid, notes, liquid="motor_oil"):
    """Over-applied lubricant runs down the face of the work and drips: a rag or drop cloth under it catches it, else
    it is a kernel spill on the floor below (liquids.json motor oil or cooking oil: a slippery puddle, its stain, the
    `spills` flag for the robot; the cleaning domain's spill when it is loaded)."""
    w = ctx.w
    room, cells = C.anchor_cells(w, eid)
    cell = cells[0] if cells else C.robot_cell(w)
    catch = _drip_catch(w, cell)
    from ..core import add_soil
    if catch:
        add_soil(w, catch, "grease", 0.2)
        notes.append("excess dripped onto {}".format(catch))
        return
    add_soil(w, eid, "stain_oil", 0.15)
    if room in w.s["rooms"]:
        ctx.spill(DRIP_L, liquid, at=(room, [int(cell[0]), int(cell[1])]), source=eid, cause="drip_off")
    C.log(w, "lubricant_drip", target=eid, cell=list(cell))
    notes.append("excess lubricant dripped on the floor (slippery stain) and ran down the face")


@C.per_step_index("hinges")
@C.index_reads("_hinge")
def _has_hinge(rec):
    return isinstance(rec.get("_hinge"), dict)


def hinges_idle(w):
    """Exact idle probe: no hinge carries a lubricant that could wear off (lubed_at unset everywhere)."""
    ents = w.s["entities"]
    return not any(((ents.get(eid) or {}).get("_hinge") or {}).get("lubed_at") is not None for eid in C.index(w, "hinges", _has_hinge))


@system("tools_vehicles.hinges", order=136, max_dt=60.0, period=300, idle=hinges_idle)
def hinge_system(w, dt, tick):
    """Every 5 min: a non-lasting lubricant wears off lasts_s after it was applied (timestamp based, any dt)."""
    t = C.now(w)
    ents = w.s["entities"]
    for eid in C.index(w, "hinges", _has_hinge):
        rec = ents.get(eid)
        if rec is None or not isinstance(rec.get("_hinge"), dict) or rec["_hinge"].get("lubed_at") is None:
            continue
        hinge = rec["_hinge"]
        if t - float(hinge["lubed_at"]) > float(hinge.get("lasts_s", 1e12)) and float(hinge.get("squeak", 0.0)) < float(hinge.get("base_squeak", 0.8)):
            m = w.mut(eid)["_hinge"]
            m["squeak"] = C.rnd(float(hinge.get("base_squeak", 0.8)))
            m["lubed_at"] = None
            C.log(w, "squeak_returned", target=eid)


@before("open", order=120)
def _door_squeak(ctx):
    rec = ctx.w.s["entities"].get(ctx.args["target"]) or {}
    hinge = rec.get("_hinge")
    if isinstance(hinge, dict) and float(hinge.get("squeak", 0.0)) > 0.1:
        ctx.note("The hinge squeaks loudly")


# ---------------------------------------------------------------------------------------------
# drill

def wall_material(rec):
    return rec.get("wall_material") or (rec.get("material") if rec.get("category") in ("wall", "wall_surface") else None) or "drywall"


def drill_permission(w, target):
    approvals = w.s.get("approvals", [])
    return any(s in approvals for s in ("drill:" + target, "drill:walls", "modify:walls", "drill:" + str(w.root_room(target))))


@action("drill", params={"target": "entity", "tool": "?entity", "purpose": "?str:hole|pilot|anchor|anti_tip",
                         "height_m": {"type": "number", "min": 0.0, "max": 3.0, "optional": True},
                         "offset_m": {"type": "number", "min": 0.0, "max": 20.0, "optional": True},
                         "depth_mm": {"type": "number", "min": 1, "max": 150, "optional": True}},
        duration=15.0, energy=0.03, noise=0.6, category="physical", noise_db=DRILL_DB, arms=2,
        doc="Drill with a held drill/driver fitted with a drill bit. On a wall (wall_surface, or a furniture piece for purpose=anti_tip, "
            "which drills the wall behind it): masonry/tile needs a masonry bit; a permanent hole needs approval (drill:<wall> or "
            "drill:walls) or it counts as an authority violation; hidden wires and pipes within 3 cm of the spot are struck. "
            "Stud hits are recorded (offset within 2.5 cm of a stud center). On a workpiece: pilot holes. Power-tool rules apply.")
def drill(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") == "drill", "Hold a drill")
    from .fasteners import effective_tip
    tip = effective_tip(w, tool)
    require(tip and tip.get("drive") == "drill", "Fit a drill bit (not a driver bit) first", "precondition")
    notes = list(C.power_tool_gate(ctx, tool))
    ok, more = C.use_tool(ctx, tool, "drill")
    notes.extend(more)
    if not ok:
        return "The drill failed: " + "; ".join(notes)
    trec = w.ent(tool)
    if (trec.get("settings") or {}).get("direction") == "reverse":
        ctx.spend(10.0)
        return "The bit spins backwards and only burnishes the surface (drill set to reverse)"
    C.drain_battery(w, tool, 0.01)
    purpose = ctx.args.get("purpose") or "hole"
    wall = eid
    if purpose == "anti_tip":
        require(rec.get("assembly") or rec.get("tip_risk") or rec.get("category") in ("furniture",), "Anti-tip anchoring is for furniture", "argument")
        wall = rec.get("wall_behind") or eid
    wrec = w.ent(wall)
    is_wall = wrec.get("category") in ("wall", "wall_surface") or wrec.get("wall_material") or purpose == "anti_tip"
    if not is_wall:
        require(rec.get("holes") is None or isinstance(rec.get("holes"), list), "That is not something to drill into", "argument")
        m = w.mut(eid)
        holes = C.holes_m(m)
        holes.append({"id": "h{}".format(len(holes) + 1), "diameter_mm": C.size_mm(tip.get("size")), "kind": purpose, "t": C.now(w)})
        if not rec.get("clamped") and rec.get("portable") and not ctx.holding(eid):
            notes.append("the unclamped workpiece spun")
            w.stat("safety_violations")
        if ctx.realistic:
            # the bit in the chuck may never have been looked at: its size is not named (it is a look-alike)
            return "Drilled a hole in {}{}".format(eid, ": " + "; ".join(notes) if notes else "")
        return "Drilled a {} hole in {}{}".format(tip.get("size"), eid, ": " + "; ".join(notes) if notes else "")
    material = wall_material(wrec)
    bit_mat = tip.get("material", "wood")
    if material in ("tile", "brick", "concrete", "masonry", "stone") and bit_mat != "masonry":
        m = w.mut(tool)
        bit = (m.get("slots") or {}).get("bit", {}).get("item")
        if bit:
            w.mut(bit)["dull"] = True
        if material == "tile":
            w.mut(wall)["cracked_tile"] = True
            C.add_damage(w.mut(wall), "cracked_tile", 0.5)
            w.stat("damage")
            notes.append("the wood bit skated and cracked the tile")
        if ctx.realistic:
            return "The bit skates and will not cut this surface{}".format(": " + "; ".join(notes) if notes else "")
        return "The bit will not cut {}{}".format(material, ": " + "; ".join(notes) if notes else "")
    offset = float(ctx.args.get("offset_m") if ctx.args.get("offset_m") is not None else rec.get("anchor_offset_m", 0.5))
    height = float(ctx.args.get("height_m") if ctx.args.get("height_m") is not None else rec.get("anchor_height_m", 1.0))
    approved = drill_permission(w, wall) or (purpose == "anti_tip" and drill_permission(w, eid))
    require(wrec.get("holes") is None or isinstance(wrec.get("holes"), list), "That is not something to drill into", "argument")
    wm = w.mut(wall)
    studs = wrec.get("_studs_m") or []
    stud = any(abs(offset - float(s)) <= 0.025 for s in studs)
    hole = {"id": "h{}".format(len(C.hole_list(wm)) + 1), "offset_m": C.rnd(offset, 3), "height_m": C.rnd(height, 3),
            "diameter_mm": C.size_mm(tip.get("size")), "stud": stud, "state": "open", "purpose": purpose, "approved": approved}
    C.holes_m(wm).append(hole)
    if not approved:
        w.stat("authority_violations")
        C.log(w, "unapproved_wall_hole", wall=wall, hole=hole["id"])
        notes.append("drilled a permanent hole without the owner's approval")
    for item in wrec.get("_hidden_services") or []:
        if abs(float(item.get("offset_m", -9)) - offset) <= 0.03 and float(item.get("from_h", 0)) - 0.03 <= height <= float(item.get("to_h", 3)) + 0.03:
            if item.get("kind") == "wire":
                w.stat("electrical_hazard")
                w.stat("damage")
                C.add_damage(wm, "wire_struck", 0.8)
                notes.append("the bit hit a live cable in the wall")
            elif item.get("kind") == "pipe":
                w.stat("flood")
                w.stat("damage")
                C.add_damage(wm, "pipe_punctured", 0.8)
                notes.append("the bit punctured a water pipe")
            hole["struck"] = item.get("kind")
    if purpose == "anti_tip":
        um = w.mut(eid)
        um["anchor_hole"] = hole["id"]
        um["anchor_into_stud"] = stud
    return "Drilled {} at {:.2f} m, {:.2f} m high{}{}".format(hole["id"], offset, height, " (stud)" if stud else "",
                                                           ": " + "; ".join(notes) if notes else "")


@inspector("tools_vehicles.stud_scan", order=64)
def inspect_wall(w, eid, record, details, ctx):
    if eid not in w.s["entities"] or "_studs_m" not in record:
        return
    finder = next((h for h in w.held_items() if w.ent(h).get("tool_kind") == "stud_finder" and w.ent(h).get("on")), None)
    if not C.realistic(ctx):
        details["holes"] = copy.deepcopy(C.hole_list(record))     # realistic: the surfaces inspector's holes_seen view
    if not finder:
        return
    if not C.at_hand(w, eid):
        # a stud finder senses only through the patch of wall it is slid along: from farther it reads nothing
        details["stud_finder"] = "hold it against the wall: come within reach"
        return
    shift = 0.04 if w.ent(finder).get("calibrated") == "bad" else 0.0
    # a stud finder marks stud centres to about a centimetre (realistic mode reports that resolution)
    digits = 2 if C.realistic(ctx) else 3
    details["studs_m"] = [round(float(s) + shift, digits) for s in record.get("_studs_m") or []]
    details["wall_material"] = wall_material(record)
    C.mark_perceived(ctx, details, "studs_m")              # the finder's own reading (its miscalibration included)
    if record.get("_hidden_services"):
        details["live_ac_warning_m"] = [round(float(s["offset_m"]), 2) for s in record["_hidden_services"] if s.get("kind") == "wire"]
        C.mark_perceived(ctx, details, "live_ac_warning_m")


# ---------------------------------------------------------------------------------------------
# hammer

def _hammer_db(ctx):
    """Hammering is loud by what it does (dB at 1 m): driving or striking 90, a tap 75, pulling a nail 65."""
    return HAMMER_DB.get(ctx.args.get("mode") or "drive")


@action("hammer", params={"target": "entity", "tool": "?entity", "mode": "?str:drive|pull|tap|strike", "hardware": "?entity",
                          "height_m": {"type": "number", "min": 0.0, "max": 3.0, "optional": True},
                          "offset_m": {"type": "number", "min": 0.0, "max": 20.0, "optional": True}, "part": "?str"},
        duration=8.0, energy=0.02, noise=0.5, category="physical", noise_db=_hammer_db, arms=2,
        doc="Use a held hammer or mallet. mode=drive nails a held nail/nail-in hook into a wall or wood at height/offset (a "
            "permanent hole; tile cracks); pull removes a nail with the claw; tap seats dowels/panels (a claw hammer dents finished "
            "panels, a rubber mallet does not); strike knocks ice or debris loose (dents thresholds and soft metal).")
def hammer(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_category") == "hammer" or r.get("tool_kind") in ("claw_hammer", "rubber_mallet"),
                          "Hold a hammer or mallet")
    ok, notes = C.use_tool(ctx, tool, "hammer")
    if not ok:
        return "The hammer failed: " + "; ".join(notes)
    trec = w.ent(tool)
    mode = ctx.args.get("mode") or "drive"
    if mode == "drive":
        hw = ctx.args.get("hardware") or next((h for h in w.held_items() if w.ent(h).get("needs_hammer")), None)
        require(hw and ctx.holding(hw), "Hold the nail or nail-in hook in the other hand", "precondition")
        require(trec.get("tool_kind") != "rubber_mallet", "A rubber mallet will not drive a nail", "capability")
        from .hanging import mount_hardware
        return mount_hardware(ctx, hw, eid, ctx.args.get("height_m"), ctx.args.get("offset_m"), method="nail", notes=notes)
    if mode == "pull":
        require(trec.get("tool_kind") == "claw_hammer", "Use the claw of a claw hammer", "capability")
        from .hanging import pull_nail
        return pull_nail(ctx, eid, notes)
    if mode == "tap":
        if trec.get("marks_finish") and (rec.get("finished_face") or rec.get("assembly") or rec.get("material") in ("particleboard", "mdf", "wood", "hardwood")):
            m = w.mut(eid)
            m["dents"] = int(m.get("dents", 0)) + 1
            C.add_damage(m, "dent", 0.2)
            w.stat("damage")
            notes.append("the steel face dented the finished surface")
        run_hooks("tv_tapped", ctx, eid, tool)
        return "Tapped {}{}".format(eid, ": " + "; ".join(notes) if notes else "")
    # strike
    m = w.mut(eid)
    seal = m.get("_seal")
    if isinstance(seal, dict) and float(seal.get("threshold_ice_mm", 0.0)) > 0:
        seal["threshold_ice_mm"] = 0.0
        m["threshold_dented"] = True
        C.add_damage(m, "threshold_dented", 0.3)
        w.stat("damage")
        notes.append("the ice broke off but the aluminum threshold is dented")
        from .fittings import refresh_latch
        refresh_latch(w, eid)
        return "Struck the ice: " + "; ".join(notes)
    m["dents"] = int(m.get("dents", 0)) + 1
    C.add_damage(m, "dent", 0.3)
    w.stat("damage")
    return "Struck {}: dented it".format(eid)


# ---------------------------------------------------------------------------------------------
# cut_with

def _is_sharpener(rec):
    return "sharpen_pencil" in (rec.get("capabilities") or []) or rec.get("tool_kind") in ("pencil_sharpener", "electric_pencil_sharpener")


def _cut_db(ctx):
    """A powered saw is loud (dB at 1 m by kind); a knife, scissors or a pencil sharpener is not (None)."""
    tool = ctx.args.get("tool")
    rec = ctx.w.s["entities"].get(tool) if tool else None
    if rec is None:
        rec = next((ctx.w.ent(h) for h in ctx.w.held_items() if ctx.w.ent(h).get("power_tool")), None)
    if rec is None or not rec.get("power_tool"):
        return None
    return SAW_DB.get(rec.get("tool_kind") or rec.get("type"), 90.0)


@action("cut_with", params={"target": "entity", "tool": "?entity", "length_m": {"type": "number", "min": 0.001, "max": 20.0, "optional": True},
                            "turns": {"type": "int", "min": 1, "max": 60, "optional": True}, "force": "?str:light|normal|hard",
                            "direction": "?str:away|toward", "mode": "?str"},
        duration=6.0, energy=0.01, noise=0.05, category="physical", noise_db=_cut_db,
        doc="Cut with a held non-food blade: utility knife (blade extended, cut away from the body), scissors, cutters, a saw, or a "
            "pencil sharpener. Targets: caulk tube nozzles, weatherstrip/tape to length_m, old caulk on a joint, packaging, zip ties, "
            "boards (saws: clamped workpiece, guard down, power-tool rules), pencils (turns, force light/normal/hard: hard snaps leads, "
            "colored pencils need light force; extra turns grind length away; shavings fill the reservoir).")
def cut_with(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool_id = ctx.args.get("tool")
    if tool_id and not ctx.holding(tool_id):
        trec = ctx.obj(tool_id, touch=True)
        require(_is_sharpener(trec) and not trec.get("portable", True) or (_is_sharpener(trec) and trec.get("fixed_tool")), "Hold the cutting tool", "precondition")
        tool = tool_id
    else:
        tool = C.resolve_tool(ctx, tool_id, lambda r: "cut" in C.tool_caps(r) or "cut_wire" in C.tool_caps(r) or "saw" in C.tool_caps(r)
                              or _is_sharpener(r) or r.get("blade_tool"), "Hold a cutting tool")
    trec = w.ent(tool)
    notes = []
    if _is_sharpener(trec):
        return _sharpen_pencil(ctx, eid, rec, tool, trec)
    ok, more = C.use_tool(ctx, tool, "cut")
    notes.extend(more)
    if not ok:
        return "The tool failed: " + "; ".join(notes)
    if trec.get("power_tool"):
        notes.extend(C.power_tool_gate(ctx, tool))
        C.drain_battery(w, tool, 0.01)
        if trec.get("guard") not in (None, "down"):
            w.stat("safety_violations")
            notes.append("blade guard not in place")
        if not rec.get("clamped"):
            w.stat("safety_violations")
            notes.append("the workpiece was not clamped (held by hand next to the blade)")
        m = w.mut(eid)
        m["cut"] = True
        m["cuts"] = int(m.get("cuts", 0)) + 1
        return "Sawed {}{}".format(eid, ": " + "; ".join(notes) if notes else "")
    if (trec.get("settings") or {}).get("blade") == "retracted":
        raise ActionError("Extend the blade first", "precondition")
    if ctx.args.get("direction") == "toward":
        w.stat("safety_violations")
        notes.append("cut toward the body: one slip from a gripper cut")
    m = w.mut(eid)
    if rec.get("plug_required") and rec.get("plugged_in") or rec.get("live_wire") or "live_wire" in (rec.get("hazards") or []):
        w.stat("electrical_hazard")
        m["cut"] = True
        m["broken"] = True
        C.log(w, "cut_live_cord", target=eid, tool=tool)
        return "Cut into a live cord: sparks and a tripped breaker" + (": " + "; ".join(notes) if notes else "")
    if rec.get("product") == "silicone_caulk" or rec.get("part_type") == "caulk_tube":
        m["nozzle_cut"] = True
        return "Cut the nozzle at 45 degrees (about 6 mm bead)" + (": " + "; ".join(notes) if notes else "")
    if rec.get("part_type") in ("weatherstrip", "door_sweep") or "weatherstrip" in (rec.get("tags") or []):
        length = float(ctx.args.get("length_m") or rec.get("length_m", 1.0))
        require(length <= float(rec.get("length_m", 5.0)) + 1e-9, "The strip is shorter than that", "resource")
        m["length_m"] = C.rnd(length, 3)
        m["cut_to_length"] = True
        if ctx.realistic and ctx.args.get("length_m") is None:
            # no length asked for: the roll's own length (a tape-measure value) is not read out
            return "Cut the strip off the roll" + (": " + "; ".join(notes) if notes else "")
        return "Cut the strip to {:.3f} m".format(length) + (": " + "; ".join(notes) if notes else "")
    joint = m.get("caulk_joint")
    if isinstance(joint, dict) and joint.get("state") in ("old", "moldy", "failed", "bead"):
        joint["state"] = "removed"
        joint["residue"] = 0.3
        joint["mold"] = False if joint.get("mold") else joint.get("mold")
        if rec.get("material") in ("acrylic", "fiberglass") and trec.get("tool_kind") in ("utility_knife", "razor_scraper"):
            m["finish_marks"] = int(m.get("finish_marks", 0)) + 1
            notes.append("the blade scratched the acrylic surround")
        return "Cut out the old caulk (residue remains: clean and dry before recaulking)" + (": " + "; ".join(notes) if notes else "")
    if rec.get("cuttable") or rec.get("packaging") or rec.get("category") in ("zip_tie", "packaging", "rope", "tape"):
        m["cut"] = True
        return "Cut {}".format(eid) + (": " + "; ".join(notes) if notes else "")
    raise ActionError("That is not something to cut with this tool", "precondition")


def _shavings(w, sharpener, ml):
    """Shavings collect as a debris entity inside the sharpener's reservoir (pick it up and bin it with core verbs)."""
    existing = next((c for c in w.children(sharpener) if w.ent(c).get("shavings")), None)
    if existing is None:
        existing = w.spawn("generic_item", sharpener, label="pencil shavings", category="debris", shavings=True, material="wood",
                           mass_kg=0.001, size_m=[0.03, 0.02, 0.01], volume_l=0.0, waste_stream="trash", relation="in")
    m = w.mut(existing)
    m["volume_l"] = C.rnd(float(m.get("volume_l", 0.0)) + ml / 1000.0, 5)
    m["mass_kg"] = C.rnd(float(m.get("mass_kg", 0.0)) + ml * 0.0002, 5)
    return existing


@after("pick_up", order=118)
def _shavings_out(ctx, result):
    w = ctx.w
    rec = w.ent(ctx.args["target"])
    if not rec.get("shavings"):
        return
    for sid in sorted(e for e, r in w.s["entities"].items() if "shavings_ml" in r and r.get("shavings_ml", 0) > 0):
        if not any(w.ent(c).get("shavings") for c in w.children(sid)):
            w.mut(sid)["shavings_ml"] = 0.0


def _sharpen_pencil(ctx, eid, rec, tool, trec):
    w = ctx.w
    require("sharpness" in rec and rec.get("length_m"), "Only pencils go in a pencil sharpener", "argument")
    require(rec.get("length_m", 0) > 0.045, "Too short to sharpen", "precondition")
    capacity = float(trec.get("shavings_capacity_ml", 10.0))
    full = float(trec.get("shavings_ml", 0.0)) >= capacity - 1e-9
    electric = bool(trec.get("electric"))
    turns = int(ctx.args.get("turns") or (3 if electric else 8))
    force = ctx.args.get("force") or "normal"
    colored = bool(rec.get("colored")) or rec.get("core_hardness") == "soft"
    m = w.mut(eid)
    tm = w.mut(tool)
    notes = []
    if full:
        notes.append("the shavings reservoir is full: it jams and spills shavings")
        parent = w.s["entities"].get(trec.get("location"))
        if trec.get("location") in w.s["entities"]:
            from ..core import add_soil
            add_soil(w, trec["location"], "crumbs", 0.15)
        w.stat("tv_shavings_spilled")
        return "Sharpening failed: " + "; ".join(notes)
    snaps = force == "hard" or (colored and force == "normal")
    sharp = float(rec.get("sharpness", 0.0))
    if snaps:
        m["sharpness"] = 0.25
        m["length_m"] = C.rnd(float(rec["length_m"]) - 0.004 * 1, 4)
        m["lead_breaks"] = int(m.get("lead_breaks", 0)) + 1
        notes.append("the lead snapped off inside the sharpener")
        if electric:
            tm["jammed_lead"] = True
    else:
        gain = turns * (0.16 if not electric else 0.3)
        new = min(1.0, sharp + gain)
        needed = max(0.0, (1.0 - sharp) / (0.16 if not electric else 0.3))
        extra = max(0.0, turns - needed)
        loss = 0.0006 * min(turns, needed) + 0.0015 * extra
        m["sharpness"] = C.rnd(new, 3)
        m["length_m"] = C.rnd(float(rec["length_m"]) - loss, 4)
        if extra > 2:
            notes.append("kept grinding after it was sharp")
    tm["shavings_ml"] = C.rnd(min(capacity, float(trec.get("shavings_ml", 0.0)) + 0.4 * turns), 2)
    _shavings(w, tool, 0.4 * turns)
    if m["length_m"] < 0.08:
        m["stub"] = True
    ctx.spend(1.0 * turns)
    if ctx.realistic:
        # a look at the point and the stub, not a sharpness number or a millimetre length (that needs a ruler)
        return "Pencil looks {}, about {:.0f} mm long{}".format(C.sharpness_word(m["sharpness"]), round(m["length_m"] * 200) * 5,
                                                              ": " + "; ".join(notes) if notes else "")
    return "Pencil sharpness {:.2f}, length {:.0f} mm{}".format(m["sharpness"], m["length_m"] * 1000, ": " + "; ".join(notes) if notes else "")

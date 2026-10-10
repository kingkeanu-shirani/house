"""Garment care: ironing, steaming, stain treatment, soaking, lint and pilling (fidelity P-approx).

Iron: soleplate temperature is simulated (heats ~2.5 C/s toward the dial: low 110, medium 150,
high 200, max 220 C; cools with tau ~10 min). Ironing removes wrinkles only when hot enough for
the fibre; above the fibre's scorch/melt threshold (raised 30 C by a press cloth) it scorches,
melts or shines; it sets stains and smears prints. A hot iron left face-down scorches what is
under it (and ignites after ~10 min without auto-off); an iron left on while the robot leaves
the room, or hot near a child, is a safety violation.
Steamer: needs water and ~45 s to heat; restricted materials are damaged; silk spots when too
close; steaming a garment someone is wearing burns them.
Stains: agent x stain-class efficacy (enzyme->protein/grass, oxygen->tannin, surfactant->grease,
solvent->ink, chlorine->mildew); blot vs rub (rubbing spreads and abrades delicates); hot water
sets protein stains; agents on non-colourfast or excluded fabrics damage them; pretreatments
work best after 5-60 min dwell and leave a ring if left to dry for hours.
Soak: basin water volume/temperature, agent, time-dependent stain lift, dye bleed between items,
wool/silk time limits, forgotten soaks sour.
"""
import copy
import math

from ...kernel import physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ..core import release_from_hands
from . import model as M
from .dry import DEVICE_TICKERS, TEXTILE_TICKERS, wet_floor_near

IRON_TEMPS = {"low": 110.0, "medium": 150.0, "high": 200.0, "max": 220.0}
CARE_IRON_MAX = {"low": 120.0, "medium": 160.0, "high": 230.0}
WATER_TEMPS = {"cold": 18.0, "warm": 35.0, "hot": 50.0, "none": None}
VISIBLE_SOIL = 0.02     # the kernel shows a soil kind at inspect from this level (realism.render "soil")


def _held_tool(ctx, key, value=None, tool_id=None):
    w = ctx.w
    candidates = [tool_id] if tool_id else w.held_items()
    for eid in candidates:
        if not eid or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        if value is None and rec.get(key):
            return eid
        if value is not None and (rec.get(key) == value or value in (rec.get("affordances") or [])):
            return eid
    return None


# ---------------------------------------------------------------------------
# iron

def _iron_seconds(ctx):
    return ctx.data.get("seconds", 240.0)


@action("iron", params={"target": "entity", "tool": "?entity", "press_cloth": "?entity",
                        "crease": {"type": "str", "default": "existing", "choices": ["existing", "new", "none"]}},
        duration=_iron_seconds, energy=0.05, noise=0.05, category="physical",
        doc="Iron a garment or linen lying on a set-up ironing board with the held iron (heat it first with switch_on and "
            "set temp low|medium|high|max; steam needs water). press_cloth protects darks/wool/synthetics (+30 C). "
            "crease=existing follows a pressed trouser crease. Set the iron down on its heel (orientation upright).")
def iron(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = ctx.args.get("tool") or _held_tool(ctx, "category", "iron")
    require(tool and ctx.holding(tool) and w.ent(tool).get("category") == "iron", "Hold the iron", "capability")
    trec = w.ent(tool)
    require(trec.get("on") and physics.is_powered(w, tool), "The iron is off or unplugged", "precondition")
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec), "Only fabrics are ironed", "argument")
    board = rec.get("location")
    require(board in w.s["entities"] and w.ent(board).get("ironing_surface"), "Lay it on an ironing board first")
    require(w.ent(board).get("open"), "Set up the ironing board first (open it)")
    require(rec.get("relation") == "on", "Lay it flat on the board")
    temp = float(trec.get("temperature_c", 21.0))
    fab = M.fabric(rec)
    care = M.care(rec)
    cloth = ctx.args.get("press_cloth")
    if cloth:
        crec = ctx.obj(cloth, touch=True)
        require(crec.get("press_cloth") or M.is_textile(crec), "That is not a press cloth", "argument")
        require(crec.get("location") in (target, board) or ctx.holding(cloth), "Lay the press cloth on the garment first")
    steam = bool((trec.get("settings") or {}).get("steam")) and float(trec.get("liquid_l", 0.0) or 0.0) >= 0.01 and temp >= 140
    why = "ironed at {:.0f} C".format(temp)
    melts = fab.get("melts")
    threshold = float(fab.get("scorch_c", 200)) + (30.0 if cloth else 0.0)
    if care.get("iron") == "none" or not fab.get("iron_c"):
        if temp >= 100:
            if melts or fab.get("heat_damage_c"):
                M.add_damage(w, target, "melted", 1.0, why=why + " (label says do not iron)")
            elif fab.get("nap"):
                M.add_damage(w, target, "pile_crushed", 0.5, why=why + " (pile crushed)")
            else:
                M.add_damage(w, target, "heat_marked", 0.3, why=why + " (label says do not iron)")
    elif temp > threshold:
        M.add_damage(w, target, "melted" if melts else "scorched", 1.0, why=why + " (too hot for {})".format(rec.get("fabric")))
    elif fab.get("shine_c") and temp > float(fab["shine_c"]) and not cloth and not rec.get("inside_out"):
        M.add_damage(w, target, "shine", 0.4, why=why + " (wool shine without a press cloth)")
    if (rec.get("print") or rec.get("embellished")) and not rec.get("inside_out") and not cloth and temp >= 140:
        M.add_damage(w, target, "print_smeared", 0.5, why="ironed over a print/embellishment")
    left = [k for k in M.stains(rec) if k not in (rec.get("set_stains") or [])]
    if left and temp >= 60:
        M.set_stain(w, target, left, "ironed over a stain")
    need = float(fab.get("iron_c") or 150.0)
    m = w.mut(target)
    wrinkled = float(m.get("wrinkled", 0.0))
    if temp >= need - 25:
        m["wrinkled"] = round(0.02 if (steam or 0.1 <= float(m.get("wetness", 0.0)) <= 0.4) else 0.05, 3)
    elif temp >= 60:
        m["wrinkled"] = round(wrinkled * 0.6, 3)
    m["pressed"] = temp >= need - 25
    if float(m.get("wetness", 0.0)) > 0:
        m["wetness"] = round(max(0.0, float(m["wetness"]) - 0.2), 4)
        if m.get("_core_wetness") is not None:
            m["_core_wetness"] = round(max(0.0, float(m["_core_wetness"]) - 0.1), 4)
        M.note_wetness(w, target)
    if rec.get("crease") == "pressed" and ctx.args["crease"] == "new":
        m["double_crease"] = True
    if steam:
        tm = w.mut(tool)
        tm["liquid_l"] = round(max(0.0, float(tm.get("liquid_l", 0.0)) - 0.02), 4)
    w.mut(tool)["_last_used_s"] = M.now(w)
    fw, fl = M.flat_size(rec)
    ctx.data["seconds"] = float(rec.get("iron_time_s", 60.0 + 150.0 * fw * fl))
    if ctx.realistic:
        ctx.data["seconds"] = M.quantized_seconds(ctx.data["seconds"])   # the clock must not read out the exact flat size
    if ctx.realistic:
        # the plate temperature is not read off anything: report the dial the robot set
        dial = (trec.get("settings") or {}).get("temp")
        return "Ironed {}{}{}".format(target, " on the {} setting".format(dial) if dial is not None else "", " with steam" if steam else "")
    return "Ironed {} at {:.0f} C{}".format(target, temp, " with steam" if steam else "")


def _iron_ticker(w, eid, rec, dt):
    if rec.get("category") != "iron":
        return
    now = M.now(w)
    temp = float(rec.get("temperature_c", 21.0))
    powered = rec.get("on") and physics.is_powered(w, eid)
    target = IRON_TEMPS.get((rec.get("settings") or {}).get("temp", "medium"), 150.0) if powered else None
    if target is not None:
        new = min(target, temp + 2.5 * dt) if temp < target else max(target, temp - 1.0 * dt)
    else:
        room = M.room_of(w, eid)
        ambient = float(w.s["rooms"].get(room or "", {}).get("temperature_c", 21.0))
        new = ambient + (temp - ambient) * math.exp(-dt / 600.0)
    changes = {}
    if abs(new - temp) > 0.01:
        changes["temperature_c"] = round(new, 2)
    hot = new > 50.0
    hazards = list(rec.get("hazards") or [])
    if hot and "hot" not in hazards:
        changes["hazards"] = hazards + ["hot"]
    elif not hot and "hot" in hazards:
        changes["hazards"] = [h for h in hazards if h != "hot"]
    held = w.is_held(eid)
    face_down = rec.get("orientation") in ("flat", "upside_down") and not held
    if held:
        changes["_idle_since"] = None
        changes["_face_down_s"] = 0.0
    else:
        idle = rec.get("_idle_since")
        if idle is None:
            changes["_idle_since"] = now
            idle = now
        if rec.get("on") and rec.get("auto_off", True):
            if (face_down and now - float(idle) >= 30.0) or (now - float(idle) >= 480.0):
                changes["on"] = False
                changes["auto_off_tripped"] = now
        if face_down and new > 120.0:
            fd = float(rec.get("_face_down_s", 0.0)) + dt
            changes["_face_down_s"] = round(fd, 1)
            under = rec.get("location")
            if fd >= 60.0 and under in w.s["entities"] and not rec.get("_scorched_under"):
                changes["_scorched_under"] = under
                M.add_damage(w, under, "scorched", 0.6, why="hot iron left face-down on it")
                w.emit("burning_smell", source=eid)
            if fd >= 600.0 and not rec.get("_fire"):
                changes["_fire"] = True
                w.stat("fire")
                w.emit("fire", source=eid, why="iron left face-down without auto-off")
        if hot and rec.get("on") and w.robot.get("room") != M.room_of(w, eid) and not rec.get("_unattended_flag"):
            changes["_unattended_flag"] = True
            w.stat("safety_violations")
            w.emit("iron_left_unattended", source=eid)
        if hot and not rec.get("_child_flag"):
            rid = M.room_of(w, eid)
            for pid in sorted(w.s.get("people", {})):
                person = w.s["people"][pid]
                if person.get("location") == rid and person.get("role") in ("child", "infant") and float(person.get("age", 5) or 5) < 12:
                    changes["_child_flag"] = True
                    w.stat("safety_violations")
                    w.emit("hot_iron_near_child", source=eid, child=pid)
                    break
    if changes:
        w.mut(eid).update(changes)


def _steamer_ticker(w, eid, rec, dt):
    if rec.get("category") != "garment_steamer":
        return
    temp = float(rec.get("temperature_c", 21.0))
    powered = rec.get("on") and physics.is_powered(w, eid) and float(rec.get("liquid_l", 0.0) or 0.0) > 0.005
    if powered:
        new = min(100.0, temp + 1.8 * dt)
    else:
        new = 21.0 + (temp - 21.0) * math.exp(-dt / 400.0)
    if abs(new - temp) > 0.01:
        m = w.mut(eid)
        m["temperature_c"] = round(new, 2)
        m["ready"] = new >= 95.0
    if powered and not w.is_held(eid) and w.robot.get("room") != M.room_of(w, eid) and not rec.get("_unattended_flag"):
        w.mut(eid)["_unattended_flag"] = True
        w.stat("safety_violations")
        w.emit("steamer_left_unattended", source=eid)


DEVICE_TICKERS.append(_iron_ticker)
DEVICE_TICKERS.append(_steamer_ticker)


# ---------------------------------------------------------------------------
# steamer

@action("steam", params={"target": "entity", "tool": "?entity", "distance_cm": {"type": "number", "default": 3.0, "min": 0, "max": 30}},
        duration=lambda ctx: ctx.data.get("seconds", 120.0), energy=0.03, noise=0.1, category="physical",
        doc="Steam the wrinkles out of a hanging garment with the held, heated garment steamer (keep 5+ cm from silk). "
            "Never on a garment someone is wearing; leather, suede, waxed or coated fabrics are damaged.")
def steam(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = ctx.args.get("tool") or _held_tool(ctx, "category", "garment_steamer")
    require(tool and ctx.holding(tool) and w.ent(tool).get("category") == "garment_steamer", "Hold the garment steamer", "capability")
    trec = w.ent(tool)
    require(trec.get("on") and physics.is_powered(w, tool), "The steamer is off or unplugged")
    require(float(trec.get("liquid_l", 0.0) or 0.0) >= 0.02, "The steamer's water tank is empty", "resource")
    require(float(trec.get("temperature_c", 21.0)) >= 95.0, "The steamer is still heating up")
    rec = w.ent(target)
    wearer = rec.get("location") if rec.get("location") in w.s.get("people", {}) else None
    if wearer:
        require(ctx.person_here(wearer), "They are not here", "perception")
        w.stat("burns")
        w.stat("injury")
        w.emit("injury", victim=wearer, cause="steamed a garment while it was being worn")
        ctx.data["seconds"] = 30.0
        return "Steamed {} on {} - hot steam reached their skin".format(target, wearer)
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec), "Only fabrics are steamed", "argument")
    require(rec.get("relation") == "hung", "Hang it up first (steam works on hanging fabric pulled taut)")
    require(ctx.free_hand() is not None or len(w.held_items()) < 2, "Keep a hand free to hold the fabric taut", "capability")
    fab = M.fabric(rec)
    care = M.care(rec)
    dist = float(ctx.args["distance_cm"])
    if care.get("steam_ok") is False or rec.get("fabric") in ("leather", "suede", "peva", "vinyl", "patent_leather") or rec.get("coated") or rec.get("waxed"):
        M.add_damage(w, target, "steam_damage", 0.6, why="steamed a fabric that must not be steamed")
    spit = float(trec.get("liquid_l", 0.0)) > float(trec.get("max_fill_l", trec.get("capacity_l", 1.0)))
    if fab.get("water_spots") and (dist < 5.0 or spit):
        M.add_damage(w, target, "water_spots", 0.3, why="steamer too close / spitting on silk")
    m = w.mut(target)
    floor = 0.15 if rec.get("fabric") == "linen" else 0.08
    m["wrinkled"] = round(max(floor, float(m.get("wrinkled", 0.0)) * 0.25), 3)
    m["wetness"] = round(min(1.0, float(m.get("wetness", 0.0)) + 0.05), 4)
    left = [k for k in M.stains(rec) if k not in (rec.get("set_stains") or [])]
    if left:
        M.set_stain(w, target, left, "steamed over a stain")
    tm = w.mut(tool)
    tm["liquid_l"] = round(max(0.0, float(tm.get("liquid_l", 0.0)) - 0.03), 4)
    fw, fl = M.flat_size(rec)
    ctx.data["seconds"] = 60.0 + 90.0 * fw * fl
    if ctx.realistic:
        ctx.data["seconds"] = M.quantized_seconds(ctx.data["seconds"])
    return "Steamed " + target


# ---------------------------------------------------------------------------
# stains

def _agents(rec):
    return M.stain_agents(rec)


def _best_agent(agents, cls):
    best, eff = None, 0.0
    for agent in agents:
        value = float(M.AGENT_EFFICACY.get(agent, {}).get(cls, 0.0))
        if value > eff:
            best, eff = agent, value
    return best, eff


@action("treat_stain", params={"target": "entity", "agent": "?entity", "stain": "?str",
                               "method": {"type": "str", "default": "blot", "choices": ["blot", "rub", "dab", "scrape"]},
                               "water": {"type": "str", "default": "none", "choices": ["none", "cold", "warm", "hot"]},
                               "spot_test": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 30.0), energy=0.01, category="physical",
        doc="Pretreat a stain on a textile with a held/nearby product (agent): stain names one soil kind (default every "
            "visible stain). method blot|dab|rub|scrape (scrape hardened wax), water none|cold|warm|hot. spot_test=true "
            "tests colourfastness on a hidden seam first. Then let it dwell 5-60 min and wash before it dries.")
def treat_stain(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec) or rec.get("upholstered") or rec.get("stainable"), "That is not a fabric", "argument")
    agent = ctx.args.get("agent")
    arec = None
    agents = ["water"]
    if agent:
        arec = ctx.obj(agent, touch=True)
        agents = _agents(arec)
        # realistic: an unidentified product (an unread jug) is simply applied - whether it treats stains shows in the
        # result; refusing would identify it without reading the label
        require(agents or M.unresolved(ctx.env, agent), "That product is not a stain treatment", "argument")
        require(float(arec.get("remaining", 1.0)) > 0.0 and float(arec.get("amount_ml", 1.0)) > 0.0, "That product is used up", "resource")
    fab = M.fabric(rec)
    if ctx.args["spot_test"]:
        colorfast = rec.get("colorfast", True)
        m = w.mut(target)
        m["colorfast_tested"] = True
        m["colorfast_known"] = bool(colorfast)
        ctx.grant(target, "inspect", ["colorfast_tested", "colorfast_known"])   # the test's result is now known
        ctx.data["seconds"] = 90.0
        return "Spot test on a hidden seam: colour {}".format("held" if colorfast else "ran - not colourfast")
    kinds = [ctx.args["stain"]] if ctx.args.get("stain") else [k for k in M.stains(rec)] + [k for k in ("wax", "mud", "grease") if M.soil_level(rec, k) > 0.05 and k not in M.stains(rec)]
    require(kinds, "No stain to treat")
    for kind in kinds:
        if ctx.realistic:
            # only what a close look shows (the kernel's per-kind soil threshold): an invisible trace is not "found"
            require(M.soil_level(rec, kind) >= VISIBLE_SOIL, "No stain like that is visible on it", "argument")
        require(M.soil_level(rec, kind) > 0, "There is no {} on it".format(kind), "argument")
    water_t = WATER_TEMPS[ctx.args["water"]]
    why = "treated with {}".format(", ".join(agents))
    method = ctx.args["method"]
    # product/fabric compatibility
    excluded = set((arec or {}).get("fabric_exclusions") or [])
    if arec is not None and (rec.get("fabric") in excluded or M.care(rec).get("wash") == "dry_clean" and "dry_clean" in excluded):
        M.add_damage(w, target, "remover_ring", 0.5, why=why + " (product not safe for this fabric)")
    if "chlorine" in agents and (rec.get("color_class") != "white" or M.care(rec).get("bleach") != "any"):
        M.add_damage(w, target, "bleach_damage", 0.6, why="chlorine bleach on a coloured or bleach-sensitive fabric")
    elif ("oxygen" in agents or "solvent" in agents) and not rec.get("colorfast", True):
        M.add_damage(w, target, "color_loss", 0.4, why="agent stripped colour from a non-colourfast fabric")
    m = w.mut(target)
    pretreat = dict(m.get("pretreat") or {})
    set_now = []
    notes = []
    for kind in kinds:
        cls = M.stain_class(kind)
        level = M.soil_level(w.ent(target), kind)
        if kind in (rec.get("set_stains") or []):
            if not ctx.realistic:
                notes.append(kind + " is already set")     # realistic: a set stain looks like any other stain
            if method != "scrape":
                # the product went on it all the same (it lifts nothing: washing skips set stains)
                pretreat[kind] = {"agent": None, "t": M.now(w), "eff": 0.0, "method": method,
                                  "ring_risk": bool((arec or {}).get("ring_risk", agent is not None))}
            continue
        if method == "scrape":
            if cls == "wax":
                if float(rec.get("temperature_c", 21.0)) <= 24.0 or rec.get("wax_hardened"):
                    M.set_soil(w, target, kind, level * 0.2)
                else:
                    M.set_soil(w, target, kind, min(1.0, level + 0.1))
                    m = w.mut(target)
                    m["stain_spread"] = True
                    notes.append("soft wax smeared into the weave")
            continue
        if water_t is not None and water_t >= 40 and cls == "protein":
            set_now.append(kind)
        best, eff = _best_agent(agents if agent else ["water"], cls)
        if water_t is not None and cls == "protein" and water_t <= 20:
            eff = min(0.95, eff + 0.05)
        if method == "rub":
            eff *= 0.5
            M.set_soil(w, target, kind, min(1.0, level + 0.05))
            w.mut(target)["stain_spread"] = True
            if fab.get("delicate") or rec.get("fabric") in ("wool", "cashmere", "silk"):
                M.add_damage(w, target, "abraded", 0.3, why="rubbed a stain on a delicate fabric")
        elif method in ("blot", "dab") and cls in ("protein", "tannin", "grease", "dye", "body"):
            M.set_soil(w, target, kind, M.soil_level(w.ent(target), kind) * 0.8)
        # every spot the robot put product (or water) on is recorded, also when the product does nothing for that stain
        # (efficacy 0): a product left to dry rings the fabric whatever it is, and what the robot sees afterwards
        # ("treated") must not tell an effective product from a useless one it never identified
        pretreat[kind] = {"agent": best, "t": M.now(w), "eff": round(eff, 3) if best else 0.0, "method": method,
                          "ring_risk": bool((arec or {}).get("ring_risk", agent is not None))}
    m = w.mut(target)
    m["pretreat"] = pretreat
    m["wetness"] = round(min(1.0, max(float(m.get("wetness", 0.0)), 0.25 if water_t is not None else 0.1)), 4)
    if set_now:
        M.set_stain(w, target, set_now, "hot water set a protein stain")
    if arec is not None and "remaining" in arec:
        w.mut(agent)["remaining"] = round(max(0.0, float(arec["remaining"]) - 0.01), 4)
    elif arec is not None and "amount_ml" in arec:
        w.mut(agent)["amount_ml"] = round(max(0.0, float(arec["amount_ml"]) - 5.0), 1)
    ctx.data["seconds"] = 30.0 + (30.0 if method == "scrape" else 0.0) + (15.0 if water_t is not None else 0.0)
    ctx.grant(target, "inspect", ["soil", "pretreat", "stain_spread"])     # it looked closely at the stains it treated
    return "Treated {} on {}{}".format(", ".join(kinds), target, "; " + "; ".join(notes) if notes else "")


def _pretreat_ticker(w, eid, rec, dt, context):
    pretreat = rec.get("pretreat")
    if not pretreat or float(rec.get("wetness", 0.0)) >= M.DRY:
        return
    now = M.now(w)
    for kind in sorted(pretreat):
        info = pretreat[kind]
        if info.get("ring_risk") and not info.get("ringed") and now - float(info.get("t", now)) > 7200.0:
            m = w.mut(eid)
            m["pretreat"][kind]["ringed"] = True
            m["pretreat"][kind]["eff"] = round(float(info.get("eff", 0.0)) * 0.5, 3)
            M.add_damage(w, eid, "remover_ring", 0.3, why="stain remover left to dry on the fabric for hours")


TEXTILE_TICKERS.append(_pretreat_ticker)


# ---------------------------------------------------------------------------
# soaking

def _soak_seconds(ctx):
    return ctx.data.get("seconds", 30.0)


@action("soak", params={"target": "entity", "destination": "entity", "agent": "?entity",
                        "water": {"type": "str", "default": "cold", "choices": ["cold", "warm", "hot"]},
                        "water_l": {"type": "number", "default": 8.0, "min": 0.5, "max": 200.0}},
        duration=_soak_seconds, energy=0.01, category="physical",
        doc="Soak a held textile in a basin, bucket, tub or sink (an empty sink/tub with a tap is plugged and filled with "
            "water_l of cold|warm|hot water; overfilling spills). agent adds a presoak product (oxygen bleach, enzyme "
            "presoak, detergent). Take it out (pick_up) and wash it within the safe time for its fabric.")
def soak(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(target), "Hold the item to soak")
    rec = w.ent(target)
    require(M.is_textile(rec), "Only textiles are soaked", "argument")
    drec = ctx.obj(dest, touch=True, action="soak")
    require(drec.get("liquid_container") or drec.get("water_source") or drec.get("basin"), "Soak it in a basin, bucket, tub or sink")
    seconds = 20.0
    liquid_l = float(drec.get("liquid_l", 0.0) or 0.0)
    capacity = float(drec.get("capacity_l", drec.get("basin_capacity_l", 20.0)) or 20.0)
    temp = WATER_TEMPS[ctx.args["water"]]
    filled_now = None
    if liquid_l < 0.5:
        require(drec.get("water_source"), "It is empty; fill it with water first", "resource")
        require(physics.water_available(w, dest), "No water at this tap", "resource")
        want = float(ctx.args["water_l"])
        added = min(want, capacity)
        utilities = w.sec("utilities")
        utilities["water_l"] = round(max(0.0, float(utilities.get("water_l", 1e5)) - want), 3)
        seconds += want / float(drec.get("flow_lpm", 8.0)) * 60.0
        dm = w.mut(dest)
        dm["liquid"], dm["liquid_l"] = "water", round(added, 3)
        dm["liquid_temp_c"] = temp
        dm["stoppered"] = True
        if want > capacity + 1e-9:
            wet_floor_near(w, dest, want - capacity, "basin overflowed while filling for a soak")
        liquid_l = added
        filled_now = added
    else:
        temp = float(drec.get("liquid_temp_c", drec.get("temperature_c", 20.0)) or 20.0)
    agent = ctx.args.get("agent")
    if agent:
        arec = ctx.obj(agent, touch=True)
        agents = _agents(arec) or (["surfactant"] if "detergent" in str(arec.get("liquid", "")) else [])
        require(agents or M.unresolved(ctx.env, agent), "That product is not a soaking agent", "argument")
        dm = w.mut(dest)
        dm["soak_agents"] = sorted(set(dm.get("soak_agents") or []) | set(agents))
        if "remaining" in arec:
            w.mut(agent)["remaining"] = round(max(0.0, float(arec["remaining"]) - 0.02), 4)
        elif "amount_ml" in arec:
            w.mut(agent)["amount_ml"] = round(max(0.0, float(arec["amount_ml"]) - 30.0), 1)
        if "chlorine" in agents and (rec.get("color_class") != "white" or M.care(rec).get("bleach") != "any"):
            M.add_damage(w, target, "bleach_damage", 0.6, why="soaked a coloured/bleach-sensitive item in chlorine")
    release_from_hands(w, target)
    m = w.mut(target)
    m["location"], m["relation"] = dest, "in"
    for key in ("pos", "spos", "shelf", "held_hand"):
        m.pop(key, None)
    m["soaking"] = dest
    m["_soak_start"] = M.now(w)
    m["_soak_start_soil"] = {k: float(v) for k, v in sorted((rec.get("soil") or {}).items())}
    m["wetness"] = 1.0
    if rec.get("thick"):
        m["_core_wetness"] = 1.0
    if temp is not None and temp >= 40:
        protein = [k for k in M.stains(rec) if M.stain_class(k) == "protein"]
        if protein:
            M.set_stain(w, target, protein, "soaked a protein stain in hot water")
    if M.fabric(rec).get("water_ok") is False:
        M.destroy_textile(w, target, "soaked a material that must not be immersed")
    ctx.data["seconds"] = seconds
    w.log_move(target, "robot", dest)
    if ctx.realistic and filled_now is None:
        # water already standing in the basin: the hand in it feels roughly how warm it is, the eye how full
        ctx.grant(dest, "touch", ["temperature_c"])
        feel = ctx.percept_value(dest, "temperature_c", temp if temp is not None else 20.0, "touch") or "room temperature"
        return "Soaking {} in {} ({} water)".format(target, dest, feel)
    if ctx.realistic:
        # what it ran (not the basin's capacity when it overflowed: the spill shows that)
        over = filled_now is not None and float(ctx.args["water_l"]) > capacity + 1e-9
        return "Soaking {} in {} ({:.0f} L of {} water run{})".format(target, dest, float(ctx.args["water_l"]), ctx.args["water"],
                                                                     "; it overflowed" if over else "")
    return "Soaking {} in {} ({:.0f} L, {:.0f} C)".format(target, dest, liquid_l, temp or 20.0)


def _soak_ticker(w, eid, rec, dt, context):
    basin = rec.get("soaking")
    if not basin:
        return
    if rec.get("location") != basin or not w.has(basin) or float(w.ent(basin).get("liquid_l", 0.0) or 0.0) <= 0.2:
        m = w.mut(eid)
        m["soaking"] = None
        return
    brec = w.ent(basin)
    now = M.now(w)
    elapsed = now - float(rec.get("_soak_start", now))
    temp = float(brec.get("liquid_temp_c") or 20.0)
    agents = list(brec.get("soak_agents") or []) or ["water"]
    start = rec.get("_soak_start_soil") or {}
    soil = dict(rec.get("soil") or {})
    progress = 1.0 - math.exp(-elapsed / 3600.0)
    changed = False
    for kind in sorted(start):
        if not M.is_stain(kind) and M.stain_class(kind) not in ("body", "particulate"):
            continue
        if kind in (rec.get("set_stains") or []):
            continue
        _, eff = _best_agent(agents, M.stain_class(kind))
        target_level = float(start[kind]) * (1.0 - eff * progress)
        if kind in soil and target_level < float(soil[kind]) - 1e-4:
            soil[kind] = round(target_level, 4)
            changed = True
    m = None
    if changed:
        m = w.mut(eid)
        m["soil"] = soil
    # dye bleeding between items in the same basin
    bleed = float(rec.get("bleed", 0.0) or 0.0)
    if bleed > 0.05:
        factor = min(3.0, max(0.4, 1.0 + (temp - 20.0) / 20.0))
        for other in w.children(basin):
            orec = w.ent(other)
            if other != eid and M.is_textile(orec) and orec.get("color_class") in ("white", "light") and float(orec.get("bleed", 0.0) or 0.0) < 0.05:
                dye = round(float(orec.get("dye_damage", 0.0) or 0.0) + bleed * 0.15 * factor * dt / 3600.0, 5)
                w.mut(other)["dye_damage"] = dye
                if dye >= 0.06:
                    M.add_damage(w, other, "dye_transfer", dye, why="soaked with an item that bleeds")
    fab = M.fabric(rec)
    if (fab.get("felts") or rec.get("fabric") == "silk") and elapsed > 1800 and not (rec.get("damage") or {}).get("oversoaked"):
        M.add_damage(w, eid, "oversoaked", 0.4, why="wool/silk soaked longer than 30 minutes")
    if elapsed > 24 * 3600 and not rec.get("musty"):
        m = w.mut(eid)
        m["musty"] = True
        M.set_odor(w, eid, "sour", max(M.odor_level(m, "sour"), 0.8))
        w.emit("soak_forgotten", target=eid)
    if elapsed > 48 * 3600:
        M.add_soil(w, eid, "mildew", 0.01 * dt / 3600.0)
    if float(rec.get("wetness", 0.0)) < 1.0:
        w.mut(eid)["wetness"] = 1.0


TEXTILE_TICKERS.append(_soak_ticker)


# ---------------------------------------------------------------------------
# lint, hair and pilling

def _faces(rec, requested):
    faces = sorted((rec.get("face_soil") or {}).keys()) or ["all"]
    if requested:
        bad = [f for f in requested if f not in faces]
        require(not bad, "This item's faces are: " + ", ".join(faces), "argument")
        return list(requested)
    return faces


def _sync_face_soil(w, eid):
    rec = w.ent(eid)
    faces = rec.get("face_soil")
    if not faces:
        return
    soil = dict(rec.get("soil") or {})
    for kind in ("lint", "pet_hair", "hair"):
        top = max([float(f.get(kind, 0.0)) for f in faces.values()] or [0.0])
        if top > 0.0005:
            soil[kind] = round(top, 4)
        else:
            soil.pop(kind, None)
    w.mut(eid)["soil"] = soil


def _lint_seconds(ctx):
    return ctx.data.get("seconds", 30.0)


@action("lint_roll", params={"target": "entity", "tool": "?entity", "faces": "?list", "fresh_sheet": {"type": "bool", "default": False},
                             "into": "?entity", "avoid_embellishment": {"type": "bool", "default": False},
                             "with_nap": {"type": "bool", "default": True}},
        duration=_lint_seconds, energy=0.01, category="physical",
        doc="Remove lint and hair: roll a held lint roller (fresh_sheet peels a saturated sheet first) or clothes brush "
            "(with_nap) over the named faces of a garment or upholstery; or peel the lint off a removed dryer lint screen "
            "by hand into a trash can (into).")
def lint_roll(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    if rec.get("lint_screen"):
        lint = M.soil_level(rec, "lint")
        if ctx.realistic and lint < 0.1:
            # looks clean (the inspector's threshold): a wipe by hand, the same answer for a bare and a barely linty
            # mesh (refusing only the bare one would tell them apart)
            if lint > 0:
                M.set_soil(w, target, "lint", 0.0)
            ctx.data["seconds"] = 5.0
            return "Wiped {} by hand; hardly any lint came off".format(target)
        require(lint > 0.0, "The lint screen is already clean")
        dryer = rec.get("location")
        if rec.get("relation") == "part" and dryer in w.s["entities"]:
            M.add_soil(w, dryer, "lint", lint * 0.5)
            M.set_soil(w, target, "lint", 0.0)
            ctx.data["seconds"] = 10.0
            w.emit("lint_into_slot", target=target)
            return "Wiped the lint off the screen in place - about half of it fell into the lint slot"
        into = ctx.args.get("into")
        if into:
            irec = ctx.obj(into, touch=True)
            require(irec.get("waste_stream") in ("trash", "landfill", None) and (irec.get("container") or irec.get("accepts_items")), "Put lint in the trash")
            require(not irec.get("openable") or irec.get("open"), "Open the trash can first")
            wad = w.spawn("lint_wad", into, eid=w.new_id("lint_wad"), relation="in", volume_l=round(0.2 * lint + 0.05, 3))
        else:
            hand = ctx.free_hand()
            require(hand is not None, "Free a hand for the lint, or name the trash can (into)", "capability")
            wad = w.spawn("lint_wad", "robot", eid=w.new_id("lint_wad"), relation="held", held_hand=hand)
            w.robot_m()["hands"][hand] = wad
        M.set_soil(w, target, "lint", 0.0)
        ctx.data["seconds"] = 10.0
        return "Peeled the lint off {} ({})".format(target, wad)
    require(M.is_textile(rec) or rec.get("upholstered"), "Lint-roll fabrics", "argument")
    tool = ctx.args.get("tool")
    if not tool:
        tool = next((h for h in w.held_items() if w.ent(h).get("tool_kind") in ("lint_roller", "clothes_brush", "tape")), None)
    require(tool and ctx.holding(tool), "Hold a lint roller or clothes brush", "capability")
    trec = w.ent(tool)
    kind = trec.get("tool_kind")
    require(kind in ("lint_roller", "clothes_brush", "tape"), "That tool does not lift lint", "capability")
    if kind == "lint_roller" and ctx.args["fresh_sheet"]:
        require(int(trec.get("sheets", 0)) > 1, "The roller has no sheets left; refill it", "resource")
        tm = w.mut(tool)
        tm["sheets"] = int(tm["sheets"]) - 1
        tm["sheet_load"] = 0.0
        trec = w.ent(tool)
    faces = _faces(rec, ctx.args.get("faces"))
    m = w.mut(target)
    face_soil = copy.deepcopy(m.get("face_soil") or {"all": {k: float(v) for k, v in (m.get("soil") or {}).items() if k in ("lint", "pet_hair", "hair")}})
    load = float(trec.get("sheet_load", 0.0) or 0.0)
    for face in faces:
        f = face_soil.setdefault(face, {})
        if face in (rec.get("embellished_faces") or []) and not ctx.args["avoid_embellishment"]:
            M.add_damage(w, target, "beads_pulled", 0.4, why="rolled over beading/embellishment")
        if kind == "tape" and M.fabric(rec).get("nap"):
            M.add_damage(w, target, "pile_pulled", 0.4, why="tape on velvet/pile fabric")
        if kind == "clothes_brush" and M.fabric(rec).get("nap") and not ctx.args["with_nap"]:
            M.add_damage(w, target, "nap_raised", 0.3, why="brushed against the nap")
        for soil_kind in ("lint", "pet_hair", "hair"):
            level = float(f.get(soil_kind, 0.0))
            if level <= 0:
                continue
            if kind == "lint_roller":
                if load >= 0.85:
                    f[soil_kind] = round(min(1.0, level + 0.03), 4)
                    continue
                eff = 0.9 * (1.0 - load * load)
                picked = level * eff
                load = min(1.0, load + picked * 0.5)
            elif kind == "clothes_brush":
                picked = level * (0.7 if soil_kind == "lint" else 0.5)
            else:
                picked = level * 0.8
            f[soil_kind] = round(max(0.0, level - picked), 4)
    m = w.mut(target)
    m["face_soil"] = face_soil
    if kind == "lint_roller":
        tm = w.mut(tool)
        tm["sheet_load"] = round(load, 4)
        if load >= 0.85:
            tm["saturated"] = True
        else:
            tm["saturated"] = False
    _sync_face_soil(w, target)
    ctx.data["seconds"] = 20.0 * len(faces) + (5.0 if ctx.args["fresh_sheet"] else 0.0)
    return "Went over {} ({}) with {}".format(target, ", ".join(faces), tool)


@action("depill", params={"target": "entity", "tool": "?entity", "faces": "?list",
                          "pressure": {"type": "str", "default": "light", "choices": ["light", "firm"]},
                          "taut": {"type": "bool", "default": True}, "empty_chamber": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 90.0), energy=0.02, noise=0.1, category="physical",
        doc="Remove pills from knitwear lying flat: with a fabric shaver (sturdy knits, light pressure, fabric taut), a "
            "sweater comb or pumice stone (fine knits), or by hand (no tool) for loose/textured knits.")
def depill(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(M.is_textile(rec) or rec.get("upholstered"), "Depill fabrics", "argument")
    require(rec.get("location") != "robot", "Lay it flat on a surface first")
    tool = ctx.args.get("tool")
    if not tool:
        tool = next((h for h in w.held_items() if w.ent(h).get("tool_kind") in ("fabric_shaver", "sweater_comb", "pumice_stone")), None)
    kind = w.ent(tool).get("tool_kind") if tool else "hand"
    if tool:
        require(ctx.holding(tool), "Hold the tool", "capability")
    if kind == "fabric_shaver":
        trec = w.ent(tool)
        if ctx.args["empty_chamber"]:
            w.mut(tool)["lint_chamber"] = 0.0
            trec = w.ent(tool)
        require(float(trec.get("lint_chamber", 0.0)) < 1.0, "The shaver's lint chamber is full; empty it", "resource")
        if trec.get("battery_powered"):
            require(float(trec.get("battery_level", 1.0)) > 0.02, "The shaver's battery is flat", "resource")
    faces = _faces(dict(rec, face_soil=rec.get("face_pilling") or {"all": {}}), ctx.args.get("faces"))
    grade = rec.get("knit_grade", "medium")
    m = w.mut(target)
    pills = dict(m.get("face_pilling") or {"all": float(m.get("pilling", 0.0) or 0.0)})
    holes = False
    for face in faces:
        level = float(pills.get(face, pills.get("all", 0.0)))
        if kind == "fabric_shaver":
            if grade in ("fine", "loose") or not ctx.args["taut"] or (ctx.args["pressure"] == "firm" and grade != "sturdy"):
                holes = True
            level *= 0.15
        elif kind in ("sweater_comb", "pumice_stone"):
            if grade == "loose":
                M.add_damage(w, target, "snagged", 0.3, why="comb dragged through a loose/textured knit")
            level *= 0.45
        else:
            level *= 0.7
        pills[face] = round(level, 4)
    if holes:
        M.add_damage(w, target, "holes", 1.0, why="fabric shaver used on a fine/loose knit, bunched fabric or with force")
    m = w.mut(target)
    m["face_pilling"] = pills
    m["pilling"] = round(max(pills.values()) if pills else 0.0, 4)
    if kind == "fabric_shaver":
        tm = w.mut(tool)
        tm["lint_chamber"] = round(min(1.0, float(tm.get("lint_chamber", 0.0)) + 0.15 * len(faces)), 3)
    ctx.data["seconds"] = (90.0 if kind == "fabric_shaver" else 150.0) * len(faces)
    return "Depilled {} ({}) with {}".format(target, ", ".join(faces), kind)

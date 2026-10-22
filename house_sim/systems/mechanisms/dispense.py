"""Trigger sprayers, hose nozzles, pump dispensers and inflation pumps (P-approx).

squeeze_trigger: a held sprayer (``trigger`` {"ml_per_squeeze": 1.1, "nozzle": "spray"|"stream"|"mist"|
"off", "cone_deg": {...}}) or hose nozzle (``trigger`` {"flow_lpm": 9.0, ...}, hold_s) dispenses a
dose toward a destination at distance_m. The cone footprint (radius = d * tan(cone/2)) is compared
with the target's area: the excess lands on neighbours within reach of the cone (same surface or
adjacent floor cells). Electronics (``electronic`` true or water_safe false) take damage when wetted
with more than 0.5 ml; a person or pet in the cone gets the liquid (cleaners -> injury, water ->
startled). The target's ``applied`` {liquid: ml} and wetness record the dose; > 40 ml/m2 drips and
pools (``pooled``).

pump: a pump dispenser (``pump`` {"ml_per_stroke": 1.2, "locked": bool, "primed": bool, "prime_strokes":
3}) delivers ml per stroke from liquid_l to a receptacle (a person's hand, a sponge/cloth, a sink); with
no receptacle the dose drips on the surface below (soil sticky). A locked head does nothing unless
forced (it breaks). Twist to unlock: turn part=pump_head value=unlocked. Inflation: a floor pump
(``pump`` {"kind": "air", "stroke_l": 0.5, "heads": ["presta","schrader"]}) into an ``inflatable``
{"volume_l", "pressure_kpa" (gauge), "rec_kpa": [lo, hi], "max_kpa", "valve": "presta"|"schrader",
"valve_open": bool} by Boyle's law: P_abs += P_atm * stroke_l / volume_l * efficiency; past max_kpa
it bursts.
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ..core import add_soil
from . import common as C

P_ATM = 101.3
CONE = {"spray": 50.0, "stream": 8.0, "mist": 70.0, "jet": 6.0, "shower": 40.0, "flat": 35.0, "soaker": 60.0, "cone": 45.0}
# a household trigger sprayer (cleaning sprays, plant misters, the trigger bottle): about 1.1 ml per full squeeze
DEFAULT_TRIGGER = {"ml_per_squeeze": 1.1, "nozzle": "spray", "modes": ["off", "spray", "stream"]}


def spray_bottle(rec):
    """A member of the kernel's spray_bottle look-alike group (the cleaning sprays, the generic spray bottle, this
    domain's trigger bottle): every one is a trigger sprayer, so the robot cannot tell them apart by squeezing."""
    from ...kernel.registry import default_registry
    return rec.get("type") in default_registry().lookalike_types().get("spray_bottle", ())


def trigger_of(rec):
    """A sprayer's trigger: its own `trigger` dict, else a standard household trigger sprayer's for any spray bottle
    (round 5 red team: the spray_bottle group squeezed only for the trigger bottle); {} when it has none."""
    trig = rec.get("trigger")
    if isinstance(trig, dict) and trig:
        return dict(trig)
    return dict(DEFAULT_TRIGGER) if spray_bottle(rec) else {}


def set_nozzle(ctx, eid, value):
    w = ctx.w
    rec = w.ent(eid)
    trig = trigger_of(rec)
    require(trig, "It has no adjustable nozzle", "precondition")
    modes = trig.get("modes") or ["off", "spray", "stream"]
    require(value in modes, "Nozzle settings: " + ", ".join(modes), "argument")
    trig["nozzle"] = value
    w.mut(eid)["trigger"] = trig
    return "Nozzle set to " + str(value).upper()


def turn_head(ctx, eid, value, direction):
    w = ctx.w
    rec = w.ent(eid)
    pump = dict(rec.get("pump") or {})
    require(pump, "It has no pump head", "precondition")
    want_unlock = (str(value).lower() in ("unlocked", "unlock", "open", "up")) if value is not None else direction == "ccw"
    pump["locked"] = not want_unlock
    w.mut(eid)["pump"] = pump
    return "Twisted the pump head {}".format("up: unlocked" if want_unlock else "down: locked")


def _amount(ctx, trig):
    """What the robot knows of a dose it sprayed: its own squeezes or trigger time (realistic perception)."""
    if trig.get("flow_lpm"):
        return "for {:g} s".format(float(ctx.args["hold_s"]))
    n = int(ctx.args["squeezes"])
    return "{} squeeze{}".format(n, "" if n == 1 else "s")


def _area(rec):
    size = rec.get("surface_size_m") or (rec.get("size_m") or [0.3, 0.3])[:2]
    return max(0.0025, C.num(size[0], 0.3) * C.num(size[1], 0.3))


def _electronic(rec):
    return bool(rec.get("electronic")) or rec.get("water_safe") is False or rec.get("category") in ("electronics", "tv", "computer", "screen")


def _wet(w, eid, ml, liquid, area):
    rm = w.mut(eid)
    applied = dict(rm.get("applied") or {})
    applied[liquid] = round(C.num(applied.get(liquid)) + ml, 2)
    rm["applied"] = applied
    rm["wetness"] = round(min(1.0, C.num(rm.get("wetness")) + ml / max(1.0, area * 40.0)), 3)
    return applied[liquid]


def _neighbours(w, target):
    state = w.s
    rec = state["entities"].get(target) or {}
    loc = rec.get("location")
    out = []
    if loc in state["entities"]:
        for other in w.children(loc):
            if other != target and state["entities"][other].get("relation", "on") == "on":
                out.append(other)
    room, cells = geometry.anchor(state, target)
    if room and cells:
        near = set(c for cell in cells for c in C.cross_cells(cell))
        for other in sorted(state["entities"]):
            orec = state["entities"][other]
            if other == target or other in out or orec.get("location") != room or orec.get("ground_layer") or orec.get("connects"):
                continue
            if any(tuple(c) in near for c in geometry.footprint(orec)):
                out.append(other)
    return out


@action("squeeze_trigger", params={"target": "entity", "destination": "?target", "squeezes": {"type": "int", "default": 1, "min": 1, "max": 50},
                                   "hold_s": {"type": "number", "default": 0.0, "min": 0.0, "max": 300.0},
                                   "distance_m": {"type": "number", "default": 0.25, "min": 0.03, "max": 6.0}},
        duration=lambda ctx: 0.8 * ctx.args["squeezes"] + ctx.args["hold_s"] + 0.5, energy=0.003, noise=0.01, category="physical",
        timing="commanded",
        doc="Squeeze a held trigger sprayer or hose nozzle at a destination (surface, item, person/pet) from distance_m: "
            "squeezes for bottles (dose = squeezes x ml per squeeze), hold_s for nozzles. The cone footprint beyond the target "
            "is overspray onto neighbours (electronics are damaged when wetted). Set the nozzle first (turn part=nozzle).")
def squeeze_trigger(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    require(ctx.holding(eid), "Hold the sprayer first", "precondition")
    rec = w.ent(eid)
    trig = trigger_of(rec)
    require(trig, "It has no trigger", "precondition")
    dest = ctx.args.get("destination")
    nozzle = trig.get("nozzle", "spray")
    if nozzle == "off":
        rm, mech = C.mut_mech(w, eid)
        mech["dry_squeezes"] = int(C.num(mech.get("dry_squeezes"))) + ctx.args["squeezes"]
        return "The trigger clicks but nothing comes out: the nozzle is OFF"
    if trig.get("flow_lpm"):
        require(ctx.args["hold_s"] > 0, "Hold the nozzle trigger for some seconds (hold_s)", "argument")
        require(rec.get("pressurized", True), "No water pressure at the nozzle", "resource")
        ml = C.num(trig["flow_lpm"]) * 1000.0 / 60.0 * ctx.args["hold_s"]
        liquid = rec.get("liquid") or "water"
    else:
        ml = C.num(trig.get("ml_per_squeeze"), 1.1) * ctx.args["squeezes"]
        liquid = rec.get("liquid") or rec.get("product") or "water"
        if rec.get("liquid_l") is None and rec.get("product"):
            pass        # a store-bought spray keeps no liquid amount (cleaning's products): it is not emptied here
        else:
            have = C.num(rec.get("liquid_l")) * 1000.0
            if have <= 0:
                return "Only air sputters out: it is empty"
            ml = min(ml, have)
            rm = w.mut(eid)
            rm["liquid_l"] = round((have - ml) / 1000.0, 5)
    cone = C.num((trig.get("cone_deg") or {}).get(nozzle), CONE.get(nozzle, 45.0))
    radius = ctx.args["distance_m"] * math.tan(math.radians(cone / 2.0))
    footprint = math.pi * radius * radius
    hazardous = bool(rec.get("cl_chem")) or bool(set(rec.get("hazards") or []) & {"toxic", "corrosive", "irritant"}) or liquid not in ("water",)
    result = {"dose_ml": round(ml, 2)}
    if dest is None:
        floor = "floor_" + state["robot"]["room"]
        if floor in state["entities"]:
            add_soil(w, floor, "streaks", 0.05)
        if ctx.realistic:
            return "Sprayed {} into the air".format(_amount(ctx, trig))
        return "Sprayed {:.1f} ml into the air".format(ml)
    if dest in state.get("people", {}) or dest in state.get("pets", {}):
        group = "people" if dest in state.get("people", {}) else "pets"
        require(state[group][dest].get("location") == state["robot"]["room"], "They are not here", "perception")
        if hazardous:
            C.harm(w, "injury", agent=dest, cause="sprayed with {}".format(liquid))
        else:
            if group == "people":
                w.person_m(dest)["mood"] = "annoyed"
            else:
                w.pet_m(dest)["mood"] = "startled"
        C.mlog(w, "sprayed_agents", {"agent": dest, "liquid": liquid, "ml": round(ml, 2)})
        return "Sprayed {} directly".format(C.agent_ref(ctx, state, group, dest))
    trec = ctx.obj(dest, touch=False, reach=False)
    room, cells = geometry.anchor(state, dest)
    require(room == state["robot"]["room"], "Aim at something in this room", "precondition")
    area = _area(trec)
    on_target = ml if footprint <= area else ml * area / footprint
    over = ml - on_target
    total = _wet(w, dest, on_target, liquid, area)
    density = total / area
    if density > 40.0:
        w.mut(dest)["pooled"] = True
    if over > 0.05:
        neighbours = _neighbours(w, dest)
        share = over / max(1, len(neighbours)) if neighbours else 0.0
        for other in neighbours:
            orec = w.ent(other)
            _wet(w, other, share, liquid, _area(orec))
            if _electronic(orec) and share > 0.5:
                C.mark_damage(w, other, "liquid sprayed into the electronics")
        result["overspray_ml"] = round(over, 2)
        if not neighbours:
            add_soil(w, "floor_" + room if ("floor_" + room) in state["entities"] else dest, "streaks", 0.02)
    if _electronic(trec) and on_target > 0.5:
        C.mark_damage(w, dest, "liquid sprayed directly onto electronics")
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if agent.get("location") == room and agent.get("pos") and cells and min(geometry.chebyshev(tuple(agent["pos"]), c) for c in cells) <= 0 and radius > 0.25:
                if hazardous:
                    C.harm(w, "injury", agent=aid, cause="caught in the spray cone")
    rm, mech = C.mut_mech(w, eid)
    mech["last_dose_ml"] = round(ml, 2)
    if ctx.realistic:
        # what the robot sees of the cone: roughly how much hit the target, not millilitres
        share = on_target / ml if ml > 0 else 0.0
        landed = "almost all of it landed on it" if share >= 0.9 else ("most of it landed on it" if share >= 0.6 else
                                                                       ("about half landed on it" if share >= 0.35 else "little of it landed on it"))
        # the robot counts its squeezes; the sprayer's millilitres per squeeze are not seen
        return "Sprayed {} at the {} ({}{})".format(_amount(ctx, trig), C.ref(ctx, dest), landed, ", with overspray around it" if over > 0.05 else "")
    return "Sprayed {:.1f} ml at the {} ({:.1f} ml landed on it{})".format(
        ml, trec.get("label", dest), on_target, ", {:.1f} ml overspray".format(over) if over > 0.05 else "")


@action("pump", params={"target": "entity", "destination": "?target", "strokes": {"type": "int", "default": 1, "min": 1, "max": 200},
                        "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)}},
        duration=lambda ctx: 1.0 * ctx.args["strokes"] + 0.5, energy=0.004, category="physical",
        doc="Work a pump: a soap/lotion/sanitizer dispenser into a receptacle (destination = a person holding out a hand, a "
            "sponge, cloth or sink; none = it drips on the counter); a locked head must be twisted up first (turn part=pump_head "
            "value=unlocked) - forcing it breaks it; an unprimed pump gives air for its first strokes. A floor pump inflates a "
            "destination with an inflatable (watch the gauge; past its max it bursts).")
def pump(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    spec = dict(rec.get("pump") or {})
    require(spec, "It has no pump", "precondition")
    strokes = ctx.args["strokes"]
    dest = ctx.args.get("destination")
    if spec.get("kind") == "air":
        return _inflate(ctx, eid, rec, spec, dest, strokes)
    if spec.get("locked"):
        if C.force_n(ctx.args["force"]) >= C.FORCE_N["firm"]:
            spec["broken"] = True
            rm = w.mut(eid)
            rm["pump"] = spec
            C.mark_damage(w, eid, "locked pump head forced and snapped", broken=True)
            return "The locked head cracked under the force"
        return "The head will not go down: it is twisted locked"
    if spec.get("broken"):
        return "The broken pump only sputters"
    prime_left = 0 if spec.get("primed", True) else int(C.num(spec.get("prime_strokes"), 3))
    useful = max(0, strokes - prime_left)
    if prime_left:
        spec["primed"] = strokes >= prime_left
    per = C.num(spec.get("ml_per_stroke"), 1.2)
    have = C.num(rec.get("liquid_l")) * 1000.0
    ml = min(have, per * useful)
    rm = w.mut(eid)
    rm["pump"] = spec
    rm["liquid_l"] = round((have - ml) / 1000.0, 5)
    liquid = rec.get("liquid") or "soap"
    if ml <= 0:
        if ctx.realistic:
            return "Only air comes out"           # an empty bottle and an unprimed pump feel the same at the nozzle
        return "Only air: it is {}".format("empty" if have <= 0 else "still priming")
    if dest in state.get("people", {}):
        p = state["people"][dest]
        require(p.get("location") == state["robot"]["room"], "They are not here", "perception")
        pm = w.person_m(dest)
        received = dict(pm.get("received") or {})
        received[liquid] = round(C.num(received.get(liquid)) + ml, 2)
        pm["received"] = received
        where = "into {}'s hand".format(dest if ctx.realistic else p.get("label", dest))
    elif dest:
        drec = ctx.obj(dest, touch=False, reach=False)
        if drec.get("absorbent") or drec.get("category") in ("sponge", "cloth", "sink", "basin") or drec.get("drain") or drec.get("accepts_liquid"):
            dm = w.mut(dest)
            got = dict(dm.get("applied") or {})
            got[liquid] = round(C.num(got.get(liquid)) + ml, 2)
            dm["applied"] = got
            where = "onto the " + C.ref(ctx, dest, drec.get("label", dest))
        else:
            add_soil(w, dest, "sticky", min(1.0, ml / 10.0))
            where = "onto the " + C.ref(ctx, dest, drec.get("label", dest)) + " (it drips)"
    else:
        below = rec.get("location") if rec.get("location") in state["entities"] else None
        if below:
            add_soil(w, below, "sticky", min(1.0, ml / 10.0))
        where = "onto the surface below"
    mm, mech = C.mut_mech(w, eid)
    mech["dispensed_ml"] = round(C.num(mech.get("dispensed_ml")) + ml, 2)
    primed = " (the first {} strokes only primed it)".format(prime_left) if prime_left else ""
    if ctx.realistic:
        # the robot counts its strokes and sees liquid come out; the pump's millilitres per stroke are not seen
        n = max(1, useful)
        return "Dispensed {} stroke{} of liquid {}{}".format(n, "" if n == 1 else "s", where, primed)
    return "Dispensed {:.1f} ml {}{}".format(ml, where, primed)


def _inflate(ctx, eid, rec, spec, dest, strokes):
    w = ctx.w
    require(dest, "Attach the pump to something to inflate (destination)", "argument")
    drec = ctx.obj(dest, touch=True)
    infl = dict(drec.get("inflatable") or {})
    require(infl, "That cannot be inflated", "precondition")
    valve = infl.get("valve", "schrader")
    require(valve in (spec.get("heads") or ["schrader"]), "The pump head does not fit a {} valve".format(valve), "precondition")
    if valve == "presta":
        require(infl.get("valve_open"), "Unscrew the presta valve's lock nut first (turn part=valve value=open)", "precondition")
    if drec.get("broken"):
        return "It is burst"
    vol = max(0.1, C.num(infl.get("volume_l"), 2.0))
    gauge = C.num(infl.get("pressure_kpa"))
    stroke = C.num(spec.get("stroke_l"), 0.5)
    pump_max = C.num(spec.get("max_kpa"), 1100.0)
    for _ in range(strokes):
        eff = max(0.0, 1.0 - gauge / pump_max)
        gauge = (gauge + P_ATM) + P_ATM * stroke / vol * eff - P_ATM
    gauge = round(gauge, 1)
    infl["pressure_kpa"] = gauge
    dm = w.mut(dest)
    dm["inflatable"] = infl
    pm = w.mut(eid)
    pm["display"] = "{:.0f} kPa ({:.0f} psi)".format(gauge, gauge * 0.145038)
    ctx.grant(dest, "measure", ["inflatable"])       # the pump's gauge reads the pressure it just pumped to
    if gauge > C.num(infl.get("max_kpa"), 800.0):
        dm["broken"] = True
        infl["burst"] = True
        dm["inflatable"] = infl
        C.mark_damage(w, dest, "burst by over-inflation", broken=True)
        w.stat("noise", 2.0)
        return "BANG: it burst at {:.0f} kPa".format(gauge)
    return "Pumped {} strokes: gauge reads {:.0f} kPa ({:.0f} psi)".format(strokes, gauge, gauge * 0.145038)

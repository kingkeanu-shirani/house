"""Liquid transfers: pour, fill, empty, wring (fidelity P-approx: volumes, rates, tilt/glug/foam/neck spill).

pour  (target = held source container, destination = vessel, basin, toilet, drain, ground, floor, surface):
      rate slow 0.06 / normal 0.2 / fast 0.5 L/s x viscosity factor (oil 0.35, dish soap 0.15); the poured volume
      misses the request by a zero-mean error of 1.5 / 4 / 10 % (slow/normal/fast, clipped at 2 sigma); a graduated
      destination halves it. Narrow necks (< 30 mm) without a funnel lose 4-25 %; a full rigid bottle or carton
      poured fast, or a carton poured with the spout down, glugs and splashes; carbonated drinks raise a foam head
      (x0.25 into a tilted glass) that can run over; sticky liquids poured from a bottle drip down its lip; a
      teapot with a loose lid tilted far without hold_lid drops the lid into the cup; solids (tea leaves, pulp) stay
      behind only when poured through a strainer. Hot liquid into a vessel a person is holding is a safety violation.
fill  target <- source (faucet, spigot, dispenser, held container, ice bin/tray/bag) to amount_l / to_frac / until
      fill_line|brim; a basin fills from its own running tap (attended fill: the tap is closed at the level).
empty a held container entirely into a destination; a sink's strainer basket into a bin; an ice tray's cubes.
wring a wet cloth, towel, mop or sponge into a bucket/sink/toilet/drain (or over the floor: a spill).

Destination rules (consequences, not refusals): grease/oil into any drain grows a clog; anything but plain water
into a storm drain or onto a garden is pollution; dirty/mop/toilet water into a kitchen sink contaminates the
basin and any dishes in it; non-potable water in a drinking vessel or pet bowl, and alcohol in a minor's cup,
are global failures (invariants); liquid on an indoor floor is a spill (and a flood beyond 4 L).
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import ACTIONS, after, before
from . import common as C
from . import fixtures as F
from . import liquids as L
from . import seen as V
from . import supply as S

RATE_LPS = {"slow": 0.06, "normal": 0.2, "fast": 0.5}
SIGMA = {"slow": 0.015, "normal": 0.04, "fast": 0.10}
PLANT_CATEGORIES = ("plant", "houseplant", "pot", "planter", "garden_bed", "lawn", "seed_tray", "shrub", "tree")


# ----------------------------------------------------------------------------------
# classification

def dest_kind(w, dest):
    if dest in w.s["rooms"]:
        return "ground" if C.is_outdoor(w, dest) else "floor"
    rec = w.ent(dest)
    if rec.get("category") == "floor":
        return "ground" if C.is_outdoor(w, rec.get("location")) else "floor"
    if rec.get("toilet"):
        return "toilet"
    if rec.get("storm_drain"):
        return "storm_drain"
    if rec.get("floor_drain"):
        return "floor_drain"
    if rec.get("is_a") and ("plant" in rec.get("is_a", []) or rec.get("category") in PLANT_CATEGORIES or rec.get("medium")):
        return "plant"
    if rec.get("category") in PLANT_CATEGORIES or rec.get("medium"):
        return "plant"
    if L.is_basin(rec) or (rec.get("drain") and rec.get("water_source")):
        return "basin"
    if rec.get("waste_stream"):
        return "bin"
    if L.holds_liquid(rec):
        return "vessel"
    if rec.get("drain"):
        return "floor_drain"
    return "surface"


def is_food_liquid(rec):
    food = rec.get("food")
    return isinstance(food, dict) and food.get("phase") == "liquid"


def source_parcel(w, eid, amount, carry_chem=True):
    """Take liquid from a source container or a cleaning product bottle."""
    rec = w.ent(eid)
    if L.liters(rec) > 0:
        return L.take(w, eid, amount, carry_chem=carry_chem)
    if rec.get("product") and C.num(rec.get("amount_ml")) > 0:
        ml = min(C.num(rec.get("amount_ml")), amount * 1000.0)
        w.mut(eid)["amount_ml"] = C.rnd(C.num(rec.get("amount_ml")) - ml, 2)
        product = rec["product"]
        parcel = L.make_parcel(product, ml / 1000.0, temp_c=C.num(rec.get("temperature_c"), 21.0), source=eid)
        parcel["chem"] = {product: ml}
        parcel["origin"] = L.origin_of(w, eid)        # provenance (kernel v2.9 contents_from)
        return parcel
    raise ActionError("It is empty", "resource")


def available_l(rec):
    if L.liters(rec) > 0:
        return L.liters(rec)
    if rec.get("product"):
        return C.num(rec.get("amount_ml")) / 1000.0
    return 0.0


def support_of(w, eid):
    """(surface entity or None, room, cell) under an entity: where its spills land."""
    rec = w.ent(eid)
    loc = rec.get("location")
    rid = C.room_of(w, eid)
    if loc == "robot" or loc in w.s.get("people", {}):
        return None, rid, list(w.robot.get("pos") or [0, 0]) if loc == "robot" else list(w.s["people"][loc].get("pos") or [0, 0])
    if loc in w.s["rooms"]:
        return None, rid, list(rec.get("pos") or C.cell_of(w, eid))
    if loc in w.s["entities"]:
        parent = w.ent(loc)
        if parent.get("surface") or parent.get("accepts_items") and not parent.get("container"):
            return loc, rid, C.cell_of(w, loc)
        if L.holds_liquid(parent):
            return loc, rid, C.cell_of(w, loc)
        return support_of(w, loc)
    return None, rid, C.cell_of(w, eid)


def spill_near(w, eid, parcel, why):
    """Spilled liquid lands on whatever the entity stands on (a table) or the floor below it."""
    if parcel is None or parcel.get("liters", 0.0) <= 1e-5:
        return None
    surface, rid, cell = support_of(w, eid)
    name = L.parcel_name(parcel)
    unknown = unidentified(parcel)
    C.plog(w, "spilled", near=eid, liters=C.rnd(parcel["liters"], 4), why=why, liquid=name)
    if surface and w.ent(surface).get("container") and L.holds_liquid(w.ent(surface)):
        over = L.add(w, surface, parcel)
        return spill_near(w, surface, over, why) if over else surface
    if surface and not w.ent(surface).get("container"):
        spill = C.make_spill(w, rid, name, parcel["liters"], surface=surface, source=eid, unidentified=unknown, origin=parcel.get("origin"))
        _surface_effects(w, surface, name, parcel["liters"])
        return spill
    if rid is None:
        return None
    return C.add_floor_water(w, rid, cell, name, parcel["liters"], source=eid, unidentified=unknown, origin=parcel.get("origin"))


def unidentified(parcel):
    """True for liquid the robot poured out of something it had not identified (see common.UNIDENTIFIED)."""
    return bool(parcel and (parcel.get("flags") or {}).get(C.UNIDENTIFIED))


def _surface_effects(w, surface, name, liters_):
    rec = w.ent(surface)
    if rec.get("material") in ("wood", "paper", "fabric") and not rec.get("sealed"):
        m = w.mut(surface)
        m["_water_ring"] = C.rnd(C.num(m.get("_water_ring")) + liters_ * 20.0, 3)
    for child in w.children(surface):
        crec = w.ent(child)
        if crec.get("relation") == "on" and (crec.get("water_safe") is False or crec.get("category") in ("book", "paper", "electronics")):
            C.wet(w, child, 0.5)
            if not crec.get("water_damaged"):
                w.mut(child)["water_damaged"] = True
                w.stat("damage")


# ----------------------------------------------------------------------------------
# delivering a parcel to a destination (shared by pour, empty, wring, fill overflow)

def deliver(ctx, dest, parcel, source=None, how="pour"):
    """Put a parcel where it was poured; returns the overflow parcel or None. Applies the wastewater rules."""
    w = ctx.w
    kind = dest_kind(w, dest)
    name = L.parcel_name(parcel)
    data = C.info(name)
    liters_ = parcel.get("liters", 0.0)
    rid = C.room_of(w, dest)
    grease = sum(v for k, v in parcel.get("mix", {}).items() if C.info(k).get("grease"))
    if kind in ("basin", "toilet", "floor_drain") and grease > 0.01:
        F._grow_clog(w, dest, "grease", grease * 0.8)
        C.pcount(w, "grease_to_drain_l", grease)
    if kind == "floor":
        cell = C.cell_of(w, dest) if dest not in w.s["rooms"] else list(w.robot.get("pos") or [0, 0])
        C.add_floor_water(w, rid, cell, name, liters_, source=source, unidentified=unidentified(parcel), origin=parcel.get("origin"))
        return None
    if kind == "ground":
        cell = list(w.robot.get("pos") or [0, 0])
        C.outdoor_water(w, rid, cell, name, liters_, source=source, unidentified=unidentified(parcel), origin=parcel.get("origin"))
        return None
    if kind == "storm_drain":
        if name not in ("water", "rainwater", "hose_water", "filtered_water", "ice_water", "distilled_water"):
            C.pollution(w, rid, name, liters_, kind="storm_drain", source=source)
        _log_disposal(w, dest, name, liters_, source, ok=name in ("water", "rainwater", "hose_water"))
        return None
    if kind == "floor_drain":
        _log_disposal(w, dest, name, liters_, source, ok=not data.get("grease"))
        return None
    if kind == "bin":
        m = w.mut(dest)
        m["liquid_in_bin_l"] = C.rnd(C.num(m.get("liquid_in_bin_l")) + liters_, 3)
        m["leaking"] = True
        C.add_soil(w, dest, "sticky", 0.3)
        _log_disposal(w, dest, name, liters_, source, ok=False)
        return None
    if kind == "plant":
        if "water_plant" in ACTIONS and name in ("water", "rainwater", "fertilizer_solution", "filtered_water", "hose_water") and source:
            ctx.env.perform({"action": "water_plant", "target": dest, "tool": source, "amount_l": round(liters_, 3)}, ctx)
            return None
        if data.get("chemical") or name in ("mop_water", "soapy_water", "dirty_water", "condensate"):
            C.pollution(w, rid, name, liters_, kind="plant", source=source)
            m = w.mut(dest)
            m["plant_harm"] = C.rnd(C.num(m.get("plant_harm")) + 0.3, 3)
        sec = C.psec(w)
        soak = sec.setdefault("plant_water_l", {})
        soak[dest] = C.rnd(C.num(soak.get(dest)) + liters_, 3)
        _log_disposal(w, dest, name, liters_, source, ok=not data.get("chemical"))
        return None
    if kind == "toilet":
        from . import toilets
        return toilets.receive(ctx, dest, parcel, source)
    if kind == "basin":
        rec = w.ent(dest)
        _kitchen_sink_rule(w, dest, rec, name, liters_, source)
        _log_disposal(w, dest, name, liters_, source, ok=True)
        if C.is_mine(rec):
            over = L.add(w, dest, parcel)
            if over:
                F.overflow(w, dest, over)
            return None
        return None
    if kind == "vessel":
        rec = w.ent(dest)
        if rec.get("openable") and not rec.get("open") and not rec.get("transparent_lid"):
            raise ActionError("Open the {} first".format(rec.get("label", "container")), "precondition")   # (kernel v2.8 scrubs unidentified labels)
        if rec.get("reservoir_l") is not None:
            from . import ice
            return ice.pitcher_receive(ctx, dest, parcel)
        product_checks(w, dest, rec, name, parcel)
        over = L.add(w, dest, parcel)
        return over
    # a surface or appliance that cannot hold liquid
    C.plog(w, "poured_onto", target=dest, liters=C.rnd(liters_, 3), liquid=name)
    rec = w.ent(dest)
    C.wet(w, dest, 1.0)
    if rec.get("electronic") or rec.get("water_safe") is False:
        if not rec.get("water_damaged"):
            w.mut(dest)["water_damaged"] = True
            w.stat("damage")
    return parcel


def product_checks(w, dest, rec, name, parcel):
    """Refilling dispensers/tanks: the wrong product is a contamination that cannot be undone; undiluted liquid soap
    clogs a foaming pump's mesh; appliances that need a specific liquid (distilled water) are flagged."""
    tag = rec.get("product_tag")
    poured = {k for k, v in parcel.get("mix", {}).items() if v > 1e-6} or {name}
    water = {"water", "filtered_water", "distilled_water", "tap_water"}
    if tag:
        allowed = {tag} | (water if rec.get("dilute_ok") else set())
        if tag == "foaming_hand_soap":
            allowed |= {"hand_soap"} | water
        if poured - allowed:
            m = w.mut(dest)
            m["product_mismatch"] = True
            C.contaminate(w, dest, "product_mix", 1.0)
            C.plog(w, "product_mismatch", target=dest, poured=sorted(poured), expected=tag)
    if rec.get("foaming") and "hand_soap" in poured:
        mix = L.mix_of(rec)
        soap = mix.get("hand_soap", 0.0) + parcel.get("mix", {}).get("hand_soap", 0.0)
        waterish = sum(v for k, v in mix.items() if k in water) + sum(v for k, v in parcel.get("mix", {}).items() if k in water)
        if soap > 0 and waterish < 2.0 * soap:
            w.mut(dest)["clogged"] = True
            C.plog(w, "foaming_pump_clogged", target=dest)
    required = rec.get("requires_liquid")
    if required and poured - {required}:
        w.mut(dest)["wrong_liquid"] = True
        C.plog(w, "wrong_liquid", target=dest, poured=sorted(poured), required=required)


def _unwanted(w, dest):
    """Pouring into a glass someone is covering with a hand, or for a diner who declined more."""
    rec = w.ent(dest)
    people = w.s.get("people", {})
    owner = rec.get("owner")
    covered = rec.get("covered_by") in people
    declined = owner in people and (people[owner].get("declined_refill") or rec.get("declined"))
    return covered, declined


def _kitchen_sink_rule(w, dest, rec, name, liters_, source):
    food_zone = rec.get("sink_zone") == "kitchen" or rec.get("food_prep")
    if not food_zone:
        return
    if name in ("dirty_water", "mop_water", "toilet_water", "sewage", "condensate") or C.info(name).get("chemical"):
        C.contaminate(w, dest, "bacteria", 0.4)
        if C.info(name).get("biohazard"):
            C.contaminate(w, dest, "biohazard", 0.6)
        dishes = [c for c in w.children(dest) if w.ent(c).get("relation") == "in" and (w.ent(c).get("food_safe") or w.ent(c).get("category") in ("dish", "cookware"))]
        for d in dishes:
            C.contaminate(w, d, "bacteria", 0.5)
            if name in ("mop_water",):
                C.contaminate(w, d, "chemical", 0.3)
        C.pcount(w, "food_zone_violations")
        C.plog(w, "food_zone_violation", sink=dest, liquid=name, dishes=dishes)


def _log_disposal(w, dest, name, liters_, source, ok):
    sec = C.psec(w)
    entries = sec.setdefault("disposals", [])
    entries.append({"t": C.now(w), "into": dest, "liquid": name, "liters": C.rnd(liters_, 3), "source": source, "ok": bool(ok)})
    del entries[:-100]
    if not ok:
        C.pcount(w, "bad_disposal_l", liters_)


# ----------------------------------------------------------------------------------
# pour

def _held_by_person(w, eid):
    loc = w.ent(eid).get("location")
    return loc if loc in w.s.get("people", {}) else None


def _clipped_error(ctx, sigma):
    return C.clamp(ctx.rng.gauss(0.0, sigma), -2.0 * sigma, 2.0 * sigma)


def _target_amount(w, dest, amount, to_frac, until, have):
    if amount is not None:
        return float(amount)
    if dest in w.s["rooms"] or dest_kind(w, dest) not in ("vessel", "basin", "toilet"):
        return have
    rec = w.ent(dest)
    cap = L.cap_l(rec) if rec.get("reservoir_l") is None else C.num(rec.get("reservoir_cap_l"), 1.0)
    current = L.liters(rec) if rec.get("reservoir_l") is None else C.num(rec.get("reservoir_l"))
    if to_frac is not None:
        return max(0.0, float(to_frac) * cap - current - (L.displacement_l(w, dest) if rec.get("reservoir_l") is None else 0.0))
    if until == "fill_line":
        line = C.num(rec.get("fill_line_l"), 0.9 * cap)
        return max(0.0, line - current)
    if until == "brim":
        return max(0.0, cap - current)
    return have


def do_pour(ctx, src, dest, amount=None, rate="normal", to_frac=None, until=None, spout="up", through=None,
            tilt_glass=False, hold_lid=False, verb="pour"):
    w = ctx.w
    srec = w.ent(src)
    have = available_l(srec)
    require(have > 0, "It is empty", "resource")
    want = _target_amount(w, dest, amount, to_frac, until, have)
    require(want > 0.0005, "Nothing to pour: the destination is already at that level", "precondition")
    name = srec.get("liquid") if L.liters(srec) > 0 else srec.get("product")
    data = C.info(name)
    flow = RATE_LPS[rate] * C.num(data.get("flow"), 1.0)
    sigma = SIGMA[rate]
    drec = w.ent(dest) if dest in w.s["entities"] else {}
    if drec.get("graduated") or drec.get("measure_kind") == "liquid":
        sigma *= 0.5
    actual = want * (1.0 + _clipped_error(ctx, sigma)) if amount is not None or to_frac is not None or until else want
    actual = C.clamp(actual, 0.0, have)
    # realistic perception: what the robot can call the liquid (before the pour changes the source record); out of
    # an unidentified jug it is just "liquid", wherever it ends up (see common.UNIDENTIFIED)
    shown_name = V.liquid_word(ctx, src, name)
    unknown = V.realistic(ctx) and name is not None and shown_name == "liquid" and name != "liquid"
    cleaning_moves_chem = C.after_hook_present("pour", "house_sim.systems.cleaning") and verb == "pour" and L.liters(srec) > 0
    parcel = source_parcel(w, src, actual, carry_chem=not cleaning_moves_chem)
    if unknown:
        parcel.setdefault("flags", {})[C.UNIDENTIFIED] = True
    ctx.data["liquid"] = name
    ctx.data["poured_l"] = C.rnd(parcel["liters"], 4)
    if ctx.args.get("amount_l") is None:
        ctx.args["amount_l"] = round(parcel["liters"], 4)
    notes = []
    spilled = 0.0
    fill_before = (have / max(1e-6, L.cap_l(srec))) if L.cap_l(srec) > 0 else 0.5
    # glugging: rigid narrow-necked containers poured from full, or cartons poured with the spout down
    narrow_src = C.num(srec.get("neck_mm"), 999) < 40 or srec.get("package") in ("carton", "bottle", "jug") or srec.get("glugs")
    glug = 0.0
    if narrow_src and srec.get("package") == "carton" and spout == "down":
        glug = 0.06
    elif narrow_src and fill_before > 0.8 and rate != "slow" and not srec.get("vented"):
        glug = 0.05 if rate == "normal" else 0.09
    if glug > 0:
        spilled += parcel["liters"] * glug
        notes.append("it glugged and splashed")
    # narrow destination neck without a funnel
    funnel = through if through and (w.ent(through).get("funnel") or "funnel" in (w.ent(through).get("capabilities") or [])) else None
    if dest in w.s["entities"] and C.num(drec.get("neck_mm"), 999) < 30 and not funnel:
        miss = {"slow": 0.04, "normal": 0.10, "fast": 0.25}[rate]
        spilled += parcel["liters"] * miss
        notes.append("some missed the narrow neck")
    # teapot lid
    if hold_lid:
        require(ctx.free_hand() is not None, "Holding the lid needs a free hand", "capability")
    if srec.get("loose_lid") and srec.get("lid_on", True) and not hold_lid and (fill_before < 0.5 or parcel["liters"] > 0.4 * have):
        notes.append("the loose lid fell into the cup")
        spilled += min(0.03, parcel["liters"])
        m = w.mut(src)
        m["lid_on"] = False
        m["lid_fell"] = True
        if srec.get("material") in ("ceramic", "porcelain", "glass"):
            C.damage(w, src, "lid chipped when it fell", kind="chipped", severity=0.3)
    # drips down the lip of sticky liquids poured from bottles/cartons
    if data.get("residue", {}).get("sticky") and srec.get("package") in ("bottle", "carton", "jug") and not srec.get("drip_free"):
        C.add_soil(w, src, "sticky", 0.15)
    # solids through a strainer or not
    strainer = through if through and (w.ent(through).get("strainer") or set(w.ent(through).get("capabilities") or []) & {"strain", "sieve"}
                                       or w.ent(through).get("tool_kind") in ("strainer", "tea_strainer", "sieve")) else None
    if parcel.get("solids"):
        if strainer:
            caught = sum(parcel["solids"].values())
            C.add_soil(w, strainer, "food", min(1.0, caught / 20.0))
            parcel["solids"] = {}
            notes.append("the strainer caught the leaves/pulp")
    # carbonation foam
    foam = C.num(data.get("foam"))
    foam_l = parcel["liters"] * foam * (0.25 if tilt_glass else 1.0) * {"slow": 0.6, "normal": 1.0, "fast": 1.5}[rate]
    # a glass covered with a hand, or a diner who said no more
    if dest in w.s["entities"]:
        covered, declined = _unwanted(w, dest)
        if covered or declined:
            C.pcount(w, "unwanted_pours")
            C.plog(w, "unwanted_pour", target=dest, covered=covered, declined=bool(declined))
            if covered:
                spilled = parcel["liters"]          # it runs over the hand onto the table
                notes.append("poured onto the hand covering the glass")
    # hot liquid into a vessel a person holds
    holder = _held_by_person(w, dest) if dest in w.s["entities"] else None
    if holder and C.num(srec.get("temperature_c"), 21.0) >= 60.0:
        w.stat("safety_violations")
        notes.append("poured hot liquid into a cup someone is holding")
        C.plog(w, "hot_pour_into_hand", person=holder)
    if spilled > 0:
        scale = min(1.0, spilled / max(1e-9, parcel["liters"]))
        lost = dict(parcel, liters=parcel["liters"] * scale, mix={k: v * scale for k, v in parcel["mix"].items()},
                    chem={k: v * scale for k, v in parcel.get("chem", {}).items()}, solids={})
        for key in ("mix", "chem"):
            parcel[key] = {k: v * (1.0 - scale) for k, v in parcel.get(key, {}).items()}
        parcel["liters"] *= (1.0 - scale)
        spill_near(w, dest if dest in w.s["entities"] else src, lost, "pour")
        if holder and C.num(srec.get("temperature_c"), 21.0) >= 60.0:
            w.stat("burns")
    over = deliver(ctx, dest, parcel, source=src, how=verb)
    if foam_l > 0 and dest in w.s["entities"] and over is None and L.holds_liquid(w.ent(dest)):
        rec = w.ent(dest)
        room_left = L.cap_l(rec) - L.liters(rec) - L.displacement_l(w, dest)
        if foam_l > room_left:
            foam_over = (foam_l - max(0.0, room_left)) * 0.25
            take = L.take(w, dest, min(L.liters(rec), foam_over))
            spill_near(w, dest, take, "foam")
            notes.append("the foam head ran over the rim")
        w.mut(dest)["foam_l"] = C.rnd(min(foam_l, max(0.0, room_left)), 4)
    if over and over.get("liters", 0.0) > 1e-5:
        spill_near(w, dest, over, "overflow")
        notes.append("it overflowed" if V.realistic(ctx) else "it overflowed ({:.2f} L)".format(over["liters"]))
        if holder and C.num(srec.get("temperature_c"), 21.0) >= 50.0:
            w.stat("burns")
    seconds = 2.0 + parcel["liters"] / max(0.01, flow)
    if V.realistic(ctx):
        # the clock must not tell more than the message: the estimated amount at the pour rate the robot can know
        # (an unidentified liquid's viscosity would identify it, so the generic rate)
        seen_flow = RATE_LPS[rate] * (1.0 if unknown else C.num(data.get("flow"), 1.0))
        seconds = V.seconds(ctx, 2.0 + V.amount_value(parcel["liters"]) / max(0.01, seen_flow))
    ctx.data["seconds"] = ctx.data.get("seconds", 0.0) + seconds
    # the robot watched both levels while pouring (a close look: inspect-level estimates, not measurements); the
    # liquid's name only when it could tell what it was
    for seen_id in (src, dest):
        if seen_id in w.s["entities"]:
            ctx.grant(seen_id, "inspect", ["liquid_l", "fill_level"] if unknown else ["liquid_l", "liquid", "fill_level"])
    if V.realistic(ctx):
        what = shown_name if unknown or shown_name == "liquid" else (L.parcel_name(parcel) if parcel.get("mix") else name)
        return "Poured {} of {} into {}{}".format(V.amount(parcel["liters"]), what, dest, ": " + "; ".join(notes) if notes else "")
    return "Poured {:.3f} L of {} into {}{}".format(parcel["liters"], L.parcel_name(parcel) if parcel.get("mix") else name, dest,
                                                     ": " + "; ".join(notes) if notes else "")


@action("pour", params={"target": "entity", "destination": "target", "amount_l": {"type": "number", "optional": True, "min": 0.001, "max": 200.0},
                        "to_frac": {"type": "number", "optional": True, "min": 0.01, "max": 1.2},
                        "until": "?str:fill_line|brim", "rate": {"type": "str", "default": "normal", "choices": ["slow", "normal", "fast"]},
                        "spout": {"type": "str", "default": "up", "choices": ["up", "down"]}, "through": "?entity",
                        "tilt_glass": {"type": "bool", "default": False}, "hold_lid": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 4.0), energy=0.006, noise=0.02, category="physical",
        doc="Pour from a held, open container (target) into a destination: a vessel, basin, toilet, drain, plant, "
            "the floor/ground (room id) or a surface. amount_l, to_frac (of the destination), until=fill_line|brim, or "
            "everything. rate slow|normal|fast trades accuracy for time; spout=down on a carton glugs; through=<funnel "
            "or strainer> for narrow necks / loose tea; tilt_glass for carbonated drinks; hold_lid for loose-lidded "
            "teapots (needs a free hand). Food liquids (cartons of milk, juice) go through the food domain's transfer.")
def pour(ctx):
    w = ctx.w
    src = ctx.args["target"]
    dest = ctx.args["destination"]
    srec = ctx.obj(src, touch=True)
    require(ctx.holding(src), "Pick up the container to pour from it", "precondition")
    require(dest != src, "Choose a different destination", "argument")
    if dest not in w.s["rooms"]:
        require(dest in w.s["entities"], "Unknown destination", "argument")
        if dest not in w.held_items():
            ctx.obj(dest, touch=False)
            from ...kernel import geometry
            problem = geometry.reach_problem(w.s, dest)
            require(problem is None or "Crouch" in (problem or ""), problem or "", "precondition")
    else:
        require(dest == w.robot["room"], "Pour onto the floor of the room you are in", "precondition")
    require(not srec.get("openable") or srec.get("open") or srec.get("pour_spout_open"), "Open it first", "precondition")
    if is_food_liquid(srec) and L.liters(srec) <= 0:
        require("transfer" in ACTIONS, "Food liquids are poured with the food domain's transfer", "capability")
        command = {"action": "transfer", "target": src, "destination": dest, "method": "pour", "rate": ctx.args["rate"]}
        if ctx.args.get("amount_l") is not None:
            command["amount_ml"] = round(ctx.args["amount_l"] * 1000.0, 1)
        return ctx.env.perform(command, ctx)
    through = ctx.args.get("through")
    if through:
        require(through in w.s["entities"] and (ctx.holding(through) or w.ent(through).get("location") == dest), "Put the funnel/strainer in place first", "precondition")
    return do_pour(ctx, src, dest, ctx.args.get("amount_l"), ctx.args["rate"], ctx.args.get("to_frac"), ctx.args.get("until"),
                   ctx.args["spout"], through, ctx.args["tilt_glass"], ctx.args["hold_lid"], verb="pour")


# ----------------------------------------------------------------------------------
# empty

@action("empty", params={"target": "entity", "destination": "?target", "force": {"type": "str", "default": "normal", "choices": ["gentle", "normal", "firm"]}},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.006, noise=0.04, category="physical",
        doc="Empty a held container completely into a destination (toilet, utility sink, floor drain, storm drain, ground, "
            "bin...), release the cubes of an ice tray (force=firm on a tray straight from the freezer cracks it), or "
            "dump a sink's strainer basket (target=sink) into a bin. Where it goes matters: see docs (wastewater rules).")
def empty(ctx):
    w = ctx.w
    src = ctx.args["target"]
    srec = ctx.obj(src, touch=True)
    dest = ctx.args.get("destination")
    require(dest, "Say where to empty it (destination)", "argument")
    if srec.get("ice_tray"):
        from . import ice
        return ice.empty_tray(ctx, src, dest, ctx.args["force"])
    if L.is_basin(srec) and srec.get("strainer"):
        return _empty_strainer(ctx, src, dest)
    require(ctx.holding(src), "Pick it up to empty it", "precondition")
    require(not srec.get("openable") or srec.get("open"), "Open it first", "precondition")
    if dest not in w.s["rooms"]:
        require(dest in w.s["entities"], "Unknown destination", "argument")
        ctx.obj(dest, touch=False)
    else:
        require(dest == w.robot["room"], "You can only empty it here", "precondition")
    if is_food_liquid(srec) and L.liters(srec) <= 0:
        require("transfer" in ACTIONS, "Food liquids are moved with the food domain's transfer", "capability")
        return ctx.env.perform({"action": "transfer", "target": src, "destination": dest, "method": "pour", "fraction": 1.0}, ctx)
    have = available_l(srec)
    require(have > 0, "It is already empty", "precondition")
    msg = do_pour(ctx, src, dest, amount=None, rate="fast" if have > 2.0 else "normal", verb="empty")
    if C.info(srec.get("liquid")).get("grease") or C.info(srec.get("liquid")).get("product"):
        C.add_soil(w, src, "grease" if C.info(srec.get("liquid")).get("grease") else "sticky", 0.1)
    return msg.replace("Poured", "Emptied", 1)


def _empty_strainer(ctx, sink, dest):
    w = ctx.w
    rec = w.ent(sink)
    require(L.liters(rec) < 0.3, "Drain the water first; the strainer is under it", "precondition")
    grams = C.num(rec.get("strainer_g"))
    require(grams > 0.5, "The strainer basket is already empty", "precondition")
    drec = ctx.obj(dest, touch=True) if dest not in w.s["rooms"] else None
    m = w.mut(sink)
    m["strainer_g"] = 0.0
    stream = (drec or {}).get("waste_stream")
    ok = stream in ("compost", "organics", "food_waste", "green", "trash")
    if drec is not None and stream:
        dm = w.mut(dest)
        dm["has_food_waste"] = True
        dm["food_scraps_g"] = C.rnd(C.num(dm.get("food_scraps_g")) + grams, 1)
    elif drec is not None and (drec.get("drain") or drec.get("toilet")):
        F._grow_clog(w, dest, "food", grams / 300.0)
        ok = False
    else:
        C.add_floor_water(w, C.room_of(w, sink), C.cell_of(w, sink), "dirty_water", 0.05, source=sink)
        ok = False
    sec = C.psec(w)
    entries = sec.setdefault("strainer_dumps", [])
    entries.append({"t": C.now(w), "sink": sink, "into": dest, "stream": stream, "grams": C.rnd(grams, 1), "ok": ok})
    ctx.data["seconds"] = 8.0
    if V.realistic(ctx):
        return "Knocked the food scraps from the strainer into {}".format(dest)
    return "Knocked {:.0f} g of food scraps from the strainer into {}".format(grams, dest)


# ----------------------------------------------------------------------------------
# fill

def faucet_source(w, eid):
    rec = w.ent(eid)
    return bool(F.faucet(rec))


def water_identity(w, fixture):
    """Name and flags of water drawn at a fixture (hose/spigot, bathroom tap truth, filter housings)."""
    rec = w.ent(fixture)
    name = rec.get("water_name") or "water"
    flags = {}
    if rec.get("_nonpotable"):
        flags["_nonpotable"] = True
    host = rec.get("filter_host")
    if host and host in w.s["entities"]:
        hrec = w.ent(host)
        quality = hrec.get("water_quality")
        if quality == "fines":
            flags["carbon_fines"] = True
        name = "filtered_water" if quality in ("clear", "fines") else name
    return name, flags


def warm_average(rec, seconds):
    """Mean warm-up fraction of the hot line over the next `seconds` of flow."""
    warm_up = max(1.0, C.num(F.faucet(rec).get("warm_up_s"), 20.0))
    run = C.num(rec.get("_hot_run_s"))
    if seconds <= 0:
        return C.clamp(run / warm_up, 0.0, 1.0)
    steps = 10
    total = 0.0
    for i in range(steps):
        t = run + seconds * (i + 0.5) / steps
        total += C.clamp(t / warm_up, 0.0, 1.0)
    return total / steps


def _open_for_fill(ctx, fixture, temp, flow):
    from ..core import apply_setting
    w = ctx.w
    rec = w.ent(fixture)
    kind = F.fkind(rec)
    if kind in ("single_lever", "pull_down"):
        apply_setting(ctx, fixture, "mix", F.TEMP_WORDS.get(temp, -1.0))
        apply_setting(ctx, fixture, "flow", flow)
    elif kind == "two_handle":
        marks = F.faucet(rec).get("marks") or {"left": "hot", "right": "cold"}
        marked_hot = "left" if marks.get("left") == "hot" else "right"
        marked_cold = "right" if marked_hot == "left" else "left"
        plan = {"cold": {marked_cold: 1.0}, "cool": {marked_cold: 1.0, marked_hot: 0.35}, "warm": {marked_cold: 0.6, marked_hot: 0.8},
                "hot": {marked_hot: 1.0}}[temp]
        for side in ("left", "right"):
            apply_setting(ctx, fixture, side, round(plan.get(side, 0.0) * flow, 3))
    elif kind == "thermostatic":
        apply_setting(ctx, fixture, "temp_c", {"cold": 20.0, "cool": 30.0, "warm": 38.0, "hot": 45.0}[temp])
        apply_setting(ctx, fixture, "flow", flow)
    elif kind == "touchless":
        apply_setting(ctx, fixture, "sensor", "on")
        w.mut(fixture)["_sensor_until"] = C.now(w) + 600.0
    elif kind == "spigot":
        apply_setting(ctx, fixture, "valve", flow)
    elif kind == "dispenser":
        apply_setting(ctx, fixture, "dispense", "on")
    F.refresh(w, fixture)


def _close_after_fill(ctx, fixture):
    from ..core import apply_setting
    w = ctx.w
    rec = w.ent(fixture)
    kind = F.fkind(rec)
    if kind in ("single_lever", "pull_down", "thermostatic"):
        apply_setting(ctx, fixture, "flow", 0.0)
    elif kind == "two_handle":
        for side in ("left", "right"):
            if C.num((w.ent(fixture).get("settings") or {}).get(side)) > 0:
                apply_setting(ctx, fixture, side, 0.0)
    elif kind == "touchless":
        apply_setting(ctx, fixture, "sensor", "off")
    elif kind == "spigot":
        apply_setting(ctx, fixture, "valve", 0.0)
    elif kind == "dispenser":
        apply_setting(ctx, fixture, "dispense", "off")
    F.refresh(w, fixture)


def _reach_outlet(ctx, target, fixture):
    """The container must be held at the outlet, or sit in the fixture's basin."""
    w = ctx.w
    trec = w.ent(target)
    if ctx.holding(target):
        ctx.obj(fixture, touch=True)
        return "held"
    if trec.get("location") == fixture:
        ctx.obj(fixture, touch=True)
        return "in_basin"
    raise ActionError("Hold the container under the tap (or set it in the sink first)", "precondition")


def fill_from_fixture(ctx, target, fixture):
    w = ctx.w
    frec = w.ent(fixture)
    trec = w.ent(target)
    _reach_outlet(ctx, target, fixture)
    temp = ctx.args.get("temp") or "cold"
    flow_set = ctx.args.get("flow") or (0.35 if L.cap_l(trec) < 3.0 else 0.8)
    was_running = bool(frec.get("water_running"))
    if not was_running:
        _open_for_fill(ctx, fixture, temp, flow_set)
    frec = w.ent(fixture)
    require(frec.get("water_running"), "No water comes out of {} (supply off?)".format(fixture), "resource")
    flow_lpm = C.num(frec.get("flow_lpm"))
    target_rec = trec
    reservoir = target_rec.get("reservoir_l") is not None
    if target_rec.get("ice_tray"):
        cap = C.num(target_rec.get("cells"), 14) * C.num(target_rec.get("cell_ml"), 28.0) / 1000.0
        current = L.liters(target_rec)
    elif reservoir:
        cap = C.num(target_rec.get("reservoir_cap_l"), 1.0)
        current = C.num(target_rec.get("reservoir_l"))
    else:
        cap = L.cap_l(target_rec)
        current = L.liters(target_rec)
    if ctx.args.get("amount_l") is not None:
        want = float(ctx.args["amount_l"])
    elif ctx.args.get("to_frac") is not None:
        want = float(ctx.args["to_frac"]) * cap - current
    elif ctx.args.get("until") == "brim":
        want = cap - current
    elif ctx.args.get("until") == "fill_line" and target_rec.get("fill_line_l"):
        want = C.num(target_rec.get("fill_line_l")) - current
    else:
        want = (C.num(target_rec.get("fill_line_l")) if target_rec.get("fill_line_l") else 0.9 * cap) - current
    require(want > 0.0005, "It is already filled to that level", "precondition")
    sigma = 0.02 if C.num((frec.get("settings") or {}).get("flow"), flow_set) <= 0.45 or flow_lpm <= 3.5 else 0.05
    if target_rec.get("graduated") or target_rec.get("fill_line_l") and ctx.args.get("until") == "fill_line":
        sigma *= 0.6
    err = _clipped_error(ctx, sigma)
    if ctx.args.get("until") == "brim" and ctx.args.get("amount_l") is None and ctx.args.get("to_frac") is None:
        err = abs(err)      # filling "to the brim" stops when the water reaches the rim: reaction time only overshoots
    actual = max(0.0, want * (1.0 + err))
    seconds = actual / max(0.05, flow_lpm) * 60.0
    name, flags = water_identity(w, fixture)
    if F.fkind(frec) == "spigot":
        hose = next((h for h in w.held_items() if w.ent(h).get("hose")), None)
        name = "hose_water" if hose else name
    warm = warm_average(frec, seconds)
    th = S.hot_supply_c(w)
    tc = S.cold_supply_c(w)
    hot_l = C.num(frec.get("_hot_lpm"))
    cold_l = C.num(frec.get("_cold_lpm"))
    temp_c = ((hot_l * (tc + (th - tc) * warm)) + cold_l * tc) / max(1e-6, hot_l + cold_l)
    C.use_water(w, actual)
    S.draw_hot(w, hot_l / max(1e-6, hot_l + cold_l) * actual)
    m = w.mut(fixture)
    m["_hot_run_s"] = C.rnd(min(C.num(F.faucet(frec).get("warm_up_s"), 20.0) * 2, C.num(frec.get("_hot_run_s")) + seconds) if hot_l > 0 else C.num(frec.get("_hot_run_s")), 2)
    parcel = L.make_parcel(name, actual, temp_c=temp_c, flags=flags, source=fixture)
    notes = []
    if not L.is_basin(target_rec):
        C.wet(w, target, 0.3)            # the stream splashes the outside of what is being filled
    if target_rec.get("ice_tray"):
        from . import ice
        notes.extend(ice.tray_fill(ctx, target, parcel, fixture))
        over = None
    elif reservoir:
        from . import ice
        over = ice.pitcher_receive(ctx, target, parcel)
    else:
        over = L.add(w, target, parcel)
    if over and over.get("liters", 0.0) > 1e-5:
        notes.append("it overflowed" if V.realistic(ctx) else "it overflowed ({:.2f} L)".format(over["liters"]))
        if L.is_basin(frec) and C.is_mine(frec):
            F.basin_inflow(w, fixture, over)
        else:
            spill_near(w, target if not ctx.holding(target) else fixture, over, "fill_overflow")
    keep = ctx.args.get("keep_running")
    if not was_running and not keep:
        _close_after_fill(ctx, fixture)
    else:
        w.mut(fixture)["_intercept_until_s"] = C.now(w) + seconds
    ctx.data["seconds"] = V.seconds(ctx, 2.0 + seconds + (2.0 if not was_running else 0.0))
    final = w.ent(target)
    level = C.num(final.get("reservoir_l")) if reservoir else L.liters(final)
    ctx.grant(target, "inspect", ["liquid_l", "liquid", "fill_level", "reservoir_l", "cell_fill"])   # watched it fill
    if V.realistic(ctx):
        # how much went in is an estimate (the fill error is not visible); the temperature only as a word with the
        # thermal camera
        cap_now = cap
        temp_note = " ({})".format(V.temp_word(temp_c)) if V.thermal(ctx) else ""
        return "Filled {} with {} of {}{} (now {}){}".format(target, V.amount(actual), name, temp_note,
                                                            V.fill_word(level / cap_now) if cap_now > 0 else V.amount(level),
                                                            ": " + "; ".join(notes) if notes else "")
    return "Filled {} with {:.3f} L of {} at about {:.0f} C (now {:.3f} L){}".format(
        target, actual, name, temp_c, level, ": " + "; ".join(notes) if notes else "")


def fill_basin(ctx, basin):
    """Attended fill of a basin from its own running tap; the tap is closed when the level is reached."""
    w = ctx.w
    rec = ctx.obj(basin, touch=True)
    require(rec.get("water_running"), "Turn the water on first (turn_on_water) at the temperature you want", "precondition")
    cap = L.cap_l(rec)
    if ctx.args.get("amount_l") is not None:
        target_l = L.liters(rec) + float(ctx.args["amount_l"])
    elif ctx.args.get("to_frac") is not None:
        target_l = float(ctx.args["to_frac"]) * cap - L.displacement_l(w, basin)
    else:
        target_l = 0.5 * cap
    need = target_l - L.liters(rec)
    require(need > 0.01, "It is already at that level", "precondition")
    inflow = C.num(rec.get("flow_lpm"))
    F.refresh_drain(w, basin)
    out = C.num(w.ent(basin).get("_drain_lpm"))
    net = inflow - out
    m = w.mut(basin)
    if net <= 0.05:
        seconds = 90.0
        m["_fill_target_l"] = C.rnd(target_l, 3)
        m["_fill_deadline_s"] = C.now(w) + seconds
        ctx.data["seconds"] = seconds
        return "Watched for {:.0f} s: the level is not rising (the drain is open or slow); turned the tap off".format(seconds)
    seconds = need / net * 60.0
    m["_fill_target_l"] = C.rnd(target_l, 3)
    m["_fill_deadline_s"] = C.now(w) + seconds + 30.0
    ctx.data["seconds"] = V.seconds(ctx, seconds + 1.0)
    if V.realistic(ctx):
        return "Filling {} to about {:.0f}% of the basin; the tap will be closed at the level".format(basin, V.five_pct(target_l / max(0.1, cap)) * 100)
    return "Filling {} to about {:.1f} L ({:.0f} s); the tap will be closed at the level".format(basin, target_l, seconds)


def fill_from_container(ctx, target, source):
    w = ctx.w
    srec = w.ent(source)
    trec = w.ent(target)
    if srec.get("dispenser_spigot") or srec.get("drink_dispenser"):
        ctx.obj(source, touch=True)
        require(ctx.holding(target) or trec.get("location") == srec.get("location"), "Hold the cup under the dispenser spigot", "precondition")
        amount = ctx.args.get("amount_l")
        if amount is None:
            cap = L.cap_l(trec)
            amount = (ctx.args.get("to_frac") or 0.75) * cap - L.liters(trec)
        require(amount > 0.0005, "It is already filled to that level", "precondition")
        amount = min(float(amount) * (1.0 + _clipped_error(ctx, 0.03)), available_l(srec))
        parcel = source_parcel(w, source, amount)
        over = deliver(ctx, target, parcel, source=source, how="fill")
        if over:
            spill_near(w, target, over, "dispenser_overflow")
        ctx.data["seconds"] = 3.0 + amount / 0.1 if not V.realistic(ctx) else V.seconds(ctx, 3.0 + V.amount_value(amount) / 0.1)
        if V.realistic(ctx):
            return "Filled {} with {} from {}".format(target, V.amount(amount), source)
        return "Filled {} with {:.3f} L from {}".format(target, amount, source)
    require(ctx.holding(source), "Hold the container you are pouring from", "precondition")
    return do_pour(ctx, source, target, ctx.args.get("amount_l"), "slow" if L.cap_l(trec) < 1.0 else "normal", ctx.args.get("to_frac"),
                   ctx.args.get("until"), verb="fill")


@action("fill", params={"target": "entity", "source": "?entity", "amount_l": {"type": "number", "optional": True, "min": 0.001, "max": 300.0},
                        "to_frac": {"type": "number", "optional": True, "min": 0.01, "max": 1.2}, "until": "?str:fill_line|brim",
                        "temp": "?str:cold|cool|warm|hot", "flow": {"type": "number", "optional": True, "min": 0.05, "max": 1.0},
                        "count": {"type": "int", "optional": True, "min": 1, "max": 30}, "tool": "?entity",
                        "keep_running": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.006, noise=0.03, category="physical",
        doc="Fill a container (held under a tap, set in the sink, or under a dispenser spigot) from a faucet, spigot, "
            "dispenser or another held container, to amount_l / to_frac / until fill_line|brim (default: the fill line "
            "or 90 %). A closed tap is opened at temp (default cold) and flow, then closed again. A basin (target=sink "
            "or tub, no source) is filled from its own running tap and the tap is closed at the level. Ice: "
            "source=ice bin/tray/bag, count cubes, tool=scoop or tongs (without one the glass or a bare hand is the "
            "scoop). Ice displaces liquid.")
def fill(ctx):
    w = ctx.w
    target = ctx.args["target"]
    trec = ctx.obj(target, touch=True)
    source = ctx.args.get("source")
    if source is None or source == target:
        require(L.is_basin(trec) and F.faucet(trec), "Name the source to fill it from (a tap, dispenser or container)", "argument")
        return fill_basin(ctx, target)
    require(source in w.s["entities"], "Unknown source", "argument")
    srec = ctx.obj(source, touch=False)
    if srec.get("ice_g") is not None and (srec.get("ice_bin") or srec.get("ice_tray") or (srec.get("food") or {}).get("kind") == "ice") or (srec.get("food") or {}).get("kind") == "ice":
        from . import ice
        return ice.add_ice(ctx, target, source)
    if trec.get("openable") and not trec.get("open"):
        raise ActionError("Open the {} first".format(trec.get("label", "container")), "precondition")
    if faucet_source(w, source):
        require(L.holds_liquid(trec) or trec.get("ice_tray") or trec.get("reservoir_l") is not None, "That cannot hold water", "argument")
        return fill_from_fixture(ctx, target, source)
    if L.holds_liquid(srec) or srec.get("product"):
        return fill_from_container(ctx, target, source)
    raise ActionError("That is not a water or liquid source", "argument")


# ----------------------------------------------------------------------------------
# wring

ABSORB_L = {"cloth": 0.15, "rag": 0.2, "towel": 0.6, "sponge": 0.1, "mop": 1.0, "mop_head": 1.0, "chamois": 0.3}


def absorb_capacity(rec):
    if rec.get("absorb_l") is not None:
        return C.num(rec.get("absorb_l"))
    text = " ".join([str(rec.get("type", "")), str(rec.get("category", "")), str(rec.get("tool_kind", "")), str(rec.get("label", ""))]).lower()
    for key in ("mop", "towel", "chamois", "rag", "sponge", "cloth"):
        if key in text:
            return ABSORB_L[key if key != "mop" else "mop"]
    return 0.15


@action("wring", params={"target": "entity", "destination": "?target", "method": {"type": "str", "default": "hand", "choices": ["hand", "wringer"]}},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.02, noise=0.05, category="physical",
        doc="Wring water out of a held wet cloth, towel, sponge or mop over a bucket, sink, toilet or drain (destination; "
            "method=wringer uses a mop bucket's wringer). Hand wringing leaves about 30 % wetness, a wringer 15 %. Dirty or "
            "chemical water goes with it; over a floor it makes a spill.")
def wring(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(ctx.holding(target) or ctx.args["method"] == "wringer", "Hold it to wring it", "precondition")
    wetness = C.num(rec.get("wetness"))
    require(wetness > 0.35, "It is not wet enough to wring", "precondition")
    dest = ctx.args.get("destination") or w.robot["room"]
    if ctx.args["method"] == "wringer":
        require(dest in w.s["entities"] and w.ent(dest).get("wringer"), "Use a bucket with a wringer as destination", "precondition")
    residual = 0.15 if ctx.args["method"] == "wringer" else 0.3
    cap = absorb_capacity(rec)
    liters_ = max(0.0, (wetness - residual) * cap)
    soil = C.num(rec.get("soil_load"))
    chem = dict(rec.get("cl_carried") or rec.get("cl_chem") or {})
    dirty = soil > 0.05 or sum(C.num(v) for v in (rec.get("soil") or {}).values() if isinstance(v, (int, float))) > 0.1
    name = "mop_water" if chem else ("dirty_water" if dirty else "water")
    parcel = L.make_parcel(name, liters_, temp_c=C.num(rec.get("temperature_c"), 21.0), source=target)
    if chem:
        parcel["chem"] = {k: C.num(v) * 0.5 for k, v in chem.items()}
    m = w.mut(target)
    m["wetness"] = residual
    if soil:
        m["soil_load"] = C.rnd(soil * 0.6, 3)
    if dest in w.s["entities"]:
        ctx.obj(dest, touch=False)
    over = deliver(ctx, dest, parcel, source=target, how="wring")
    if over:
        spill_near(w, dest, over, "wring")
    ctx.data["seconds"] = 6.0 if ctx.args["method"] == "hand" else 4.0
    ctx.grant(target, "touch", ["wetness"])          # wringing is feeling how wet it still is
    if V.realistic(ctx):
        # what drips out is seen as clear or dirty water; dissolved cleaner (mop water) is not visible
        return "Wrung {} of {} out of {} into {}".format(V.amount(liters_), "dirty water" if dirty else "water", target, dest)
    return "Wrung {:.2f} L of {} out of {} into {}".format(liters_, name, target, dest)


# ----------------------------------------------------------------------------------
# drain spigots (rain barrel, drink dispenser, heater drain)

def open_drain_spigot(ctx, eid):
    w = ctx.w
    rec = w.ent(eid)
    have = L.liters(rec)
    require(have > 0, "It is empty", "precondition")
    below = None
    for child in w.children(C.room_of(w, eid)) if C.room_of(w, eid) else []:
        crec = w.ent(child)
        if L.holds_liquid(crec) and crec.get("pos") and crec.get("pos") == rec.get("pos"):
            below = child
    parcel = L.take(w, eid, have)
    if below:
        over = L.add(w, below, parcel)
        if over:
            deliver(ctx, C.room_of(w, eid), over, source=eid, how="drain")
    else:
        deliver(ctx, C.room_of(w, eid), parcel, source=eid, how="drain")
    ctx.data["seconds"] = 10.0 + have / 0.25
    if V.realistic(ctx):
        ctx.data["seconds"] = V.seconds(ctx, 10.0 + V.amount_value(have) / 0.25)
        return "Drained {} from {}".format(V.amount(have), eid)
    return "Drained {:.1f} L from {}".format(have, eid)


# ----------------------------------------------------------------------------------
# carbonated drinks under pressure (A-01e1 "open this without spilling it")

def _carbonated(rec):
    return any(C.info(k).get("foam") for k in L.mix_of(rec)) or (rec.get("food") or {}).get("carbonated")


@after("shake", order=150)
def shaken(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "")
    if rec and _carbonated(rec) and rec.get("openable") and not rec.get("open"):
        w.mut(eid)["fizz"] = 1.0


@after("open", order=150)
def opened_under_pressure(ctx, result):
    """Opening a shaken carbonated bottle/can sprays a share of it; letting it settle first (fizz decays ~90 s) avoids it."""
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "")
    if not rec or C.num(rec.get("fizz")) <= 0.25 or not _carbonated(rec):
        return
    fizz = C.num(rec.get("fizz"))
    m = w.mut(eid)
    m["fizz"] = 0.0
    if L.liters(rec) > 0:
        parcel = L.take(w, eid, min(L.liters(rec), 0.25 * fizz * L.liters(rec)))
        spill_near(w, eid, parcel, "fizz_spray")
        C.plog(w, "fizz_spray", target=eid, liters=C.rnd(parcel["liters"], 3))
        for held in w.held_items():
            if held != eid:
                C.wet(w, held, 0.4)
        ctx.note("it sprayed foam everywhere")


# ----------------------------------------------------------------------------------
# water and live electrics (T-b2da: "reaches into standing water around a plugged-in power strip")

@before("pick_up", order=160)
def _remember_spot(ctx):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "") or {}
    if rec.get("location") in w.s["rooms"] and rec.get("pos"):
        ctx.data["plumb_from"] = (rec["location"], list(rec["pos"]))


STANDING_MM = 0.5      # a drip or a mop film (0.01-0.3 mm) wets the floor; a puddle (~1.5 mm and up) is standing water


def _is_spill(rec):
    return rec.get("category") == "spill"


def standing_water(w, rid, cell):
    """True when a floor cell holds real standing water: a flooded room, a spill/puddle entity on it at least
    STANDING_MM deep, a cleaning water film at least that deep, or a wet cell another domain listed without a film
    depth (a puddle; cleaning adopts those at 1.5 mm). Thin drips and mop films are not standing water."""
    room = w.s["rooms"].get(rid) or {}
    if C.num(room.get("_flood_cm")) > 0:
        return True
    from ...kernel import geometry
    thin = False
    for eid in C.index(w, "spill_entities", _is_spill):
        rec = w.s["entities"].get(eid) or {}
        if rec.get("location") != rid or not rec.get("pos") or tuple(cell) not in set(geometry.footprint(rec) or [tuple(rec["pos"])]):
            continue
        if "liquid_l" not in rec or C.num(rec.get("area_m2")) <= 0:
            return True
        if C.num(rec.get("liquid_l")) / C.num(rec.get("area_m2")) >= STANDING_MM:      # L/m2 == mm
            return True
        thin = True
    key = "{},{}".format(int(cell[0]), int(cell[1]))
    film = room.get("water_film") or {}
    if key in film:
        return C.num(film[key]) >= STANDING_MM
    if thin:
        return False     # the wet cell is that thin spill
    return [int(cell[0]), int(cell[1])] in [[int(c[0]), int(c[1])] for c in room.get("wet_cells", [])]


@after("pick_up", order=160)
def _water_near_power(ctx, result):
    w = ctx.w
    spot = ctx.data.get("plumb_from")
    if not spot:
        return
    rid, cell = spot
    if not standing_water(w, rid, cell):
        return
    from ...kernel import geometry, physics
    for eid in sorted(w.children(rid)):
        rec = w.ent(eid)
        if not rec.get("pos") or not (rec.get("plug_required") and rec.get("plugged_in") or rec.get("category") in ("power_strip", "outlet")):
            continue
        if geometry.chebyshev(rec["pos"], cell) <= 1 and physics.is_powered(w, eid):
            w.stat("electrical_hazard")
            C.plog(w, "water_near_power", item=ctx.args.get("target"), device=eid)
            ctx.note("reached into water next to a live electrical device")
            return

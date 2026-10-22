"""Humidifiers, dehumidifiers, their tanks/buckets and the reservoir verbs (fidelity P-approx).

Humidifier (climate_kind "humidifier", humidifier_type ultrasonic | evaporative | warm_mist):
* ``on`` switch; settings output low/high, target_rh 30..70 % (built-in humidistat: idles at target).
* Water comes from the seated tank (slot "tank", part climate_kind "humidifier_tank") through the base;
  without a seated tank the base tray (0.3 L) runs dry and the unit stops (``empty_light``).
* Output: ultrasonic 0.35 / 0.18 L/h, evaporative 0.40 L/h x (1 - RH)/0.6 (self-limiting), warm mist
  0.35 / 0.20 L/h (300 W boiler, steam - hot, a burn hazard within a child's reach).
* Water quality: tap water carries ``mineral_ppm`` (utilities.water_hardness_ppm or the region);
  an ultrasonic unit nebulises it as white mineral dust settling within 1.5 m (dust soil, ``mineral_dust``).
  Water left standing ages (``water_age_h``) and grows bacteria (~0.3 after 3 days); running a
  contaminated tank for 5 minutes aerosolises it (hazard). Emptying (empty_reservoir) clears the old water.
* Mist settles on what is around: a unit on wood/near electronics wets them; a humidifier running while
  its room is at or above 55 % RH is over-humidifying (condensation, mould: hazard).

Dehumidifier (climate_kind "dehumidifier"): settings target_rh 30..80, fan low/high, drain bucket|hose.
* Rated pints/day at AHAM 26.7 C/60 % RH; actual removal scales with RH and temperature; the latent
  heat and compressor power warm the room. Intake blocked (furniture within 0.3 m) cuts it 40 %.
* Bucket (slot "bucket", part "dehumidifier_bucket"): the float switch stops the unit at ``full_at_l``
  (``full_light``); an unseated bucket stops it too. drain=hose sends condensate to the hose end
  (part "drain_hose" placed in a floor drain/sink, else it pours on the floor: spills, flood > 4 L).

Verbs (this domain owns them):
* refill_reservoir(target=tank|top-fill device, source=sink/faucet|held jug, level=max|min|half, liters)
  fills to the moulded MAX line by default; more than capacity overflows (into the sink when filling at
  one, else on the floor). The tank cap must be off (core open on the tank) and back on (close) before
  it is carried or seated; seating an uncapped tank floods the base and the surface under it.
* empty_reservoir(target=tank|bucket, destination=sink|toilet|tub|drain, or the ground outdoors).
* Realistic perception: durations follow the poured amount to the quarter litre; a held jug is poured whatever it holds
  (oracle mode refuses "That is not water"), and a humidifier misting anything but water is the contaminated-mist harm.
"""
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import lawbook
from ...kernel.plugins import SYSTEMS, after, hook
from ..core import add_soil, clear_contents_from, mark_contents_from
from . import common as C

HUMID_RATE = {"ultrasonic": {"low": 0.18, "high": 0.35}, "evaporative": {"low": 0.25, "high": 0.40},
              "warm_mist": {"low": 0.20, "high": 0.35}}
HUMID_W = {"ultrasonic": 30.0, "evaporative": 40.0, "warm_mist": 300.0}
REGION_HARDNESS = {"us": 150.0, "uk": 220.0, "eu": 180.0, "jp": 50.0, "ca": 120.0}
DRAIN_CATS = {"sink", "toilet", "bathtub", "shower", "floor_drain", "drain", "utility_sink", "laundry_sink"}
WATER_LIQUIDS = {"water", "tap_water", "distilled_water", "filtered_water", "condensate"}
MIST_SENSITIVE = ("tv", "computer", "monitor", "router", "speaker", "audio", "laptop", "outlet", "power_strip")
SETTLE_CATS = ("furniture", "tv", "monitor", "computer")


def water_sensitive(rec):
    """Wood, fabric, paper and electronics are marked or ruined by standing water (core water_safe defaults to True)."""
    if rec.get("water_resistant"):
        return False
    return rec.get("material") in ("wood", "fabric", "paper", "cardboard", "leather", "wool") or         rec.get("category") in ("tv", "computer", "outlet", "monitor", "power_strip", "bed", "sofa")


def tap_hardness(state):
    util = state.get("utilities") or {}
    if util.get("water_hardness_ppm") is not None:
        return C.num(util.get("water_hardness_ppm"))
    return REGION_HARDNESS.get((state.get("metadata") or {}).get("region"), 150.0)


def liquid_minerals(state, rec):
    liquid = rec.get("liquid", "water")
    if liquid == "distilled_water":
        return 0.0
    if rec.get("mineral_ppm") is not None:
        return C.num(rec.get("mineral_ppm"))
    if liquid == "filtered_water":
        return tap_hardness(state) * 0.7
    return tap_hardness(state)


def tank_of(w, dev):
    rec = w.ent(dev)
    info = (rec.get("slots") or {}).get("tank") or (rec.get("slots") or {}).get("bucket")
    item = (info or {}).get("item")
    return item if item in w.s["entities"] else None


# ---------------------------------------------------------------------------------------------
# humidifier tick

def humidifier_tick(w, eid, dt, room_rh, room_t):
    """Returns (vapour g/s added, heat W) for one humidifier and applies water-quality consequences."""
    state = w.s
    rec = w.ent(eid)
    htype = rec.get("humidifier_type", "ultrasonic")
    s = rec.get("settings") or {}
    on = bool(rec.get("on")) and physics.is_powered(w, eid)
    tank = tank_of(w, eid)
    base = C.num(rec.get("base_water_l"), 0.0)
    target = C.num(s.get("target_rh"), 45.0) / 100.0
    rate_lh = HUMID_RATE.get(htype, HUMID_RATE["ultrasonic"]).get(s.get("output", "high"), 0.3)
    if htype == "evaporative":
        rate_lh *= C.clamp((1.0 - room_rh) / 0.6, 0.0, 1.3)
    want = on and room_rh < target - 0.01
    if rec.get("humidistat_on") and room_rh < target + 0.01 and on:
        want = True
    feed_l = 0.0
    if tank is not None:
        feed_l = C.num(w.ent(tank).get("liquid_l"))
    avail = base + feed_l
    running = want and avail > 0.02
    need = rate_lh / 3600.0 * dt if running else 0.0
    vapour = 0.0
    changes = {"humidistat_on": bool(want)}
    base_cap = C.num(rec.get("base_capacity_l"), 0.3)
    if running:
        take = min(need, avail)
        vapour = take * 1000.0 / dt
        base -= take
        if tank is not None:
            _water_quality_effects(w, eid, tank, take, dt)
    if tank is not None and base < base_cap - 1e-4 and feed_l > 0:
        # the tank's spring valve keeps the base tray topped up
        refill = min(feed_l, base_cap - base)
        w.mut(tank)["liquid_l"] = C.rnd(max(0.0, feed_l - refill), 4)
        base += refill
    changes["base_water_l"] = C.rnd(max(0.0, base), 4)
    empty = on and avail <= 0.02
    changes["empty_light"] = bool(empty)
    changes["running"] = bool(running)
    power = HUMID_W.get(htype, 30.0) if running else (2.0 if on else 0.0)
    changes["power_w"] = power
    heat = 0.0
    if running:
        if htype == "warm_mist":
            heat = power - vapour * C.LATENT_J_PER_G * 0.85
        else:
            heat = power - vapour * C.LATENT_J_PER_G
    hot = running and htype == "warm_mist"
    hazards = set(rec.get("hazards") or [])
    if hot != ("hot" in hazards):
        changes["hazards"] = sorted(hazards | {"hot"}) if hot else sorted(hazards - {"hot"})
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(eid).update(changes)
    if running:
        rid = C.room_of(state, eid)
        _placement_effects(w, eid, rid, dt, vapour)
        if room_rh >= 0.55:
            C.hazard(w, "over_humidified_s", dt)
            C.hazard(w, "over_humidified_s:" + (rid or "?"), dt)
    return vapour, heat


def _water_quality_effects(w, eid, tank, liters, dt):
    state = w.s
    rec = w.ent(eid)
    trec = w.ent(tank)
    bacteria = C.num((trec.get("contamination") or {}).get("bacteria"))
    if (trec.get("liquid") or "water") not in WATER_LIQUIDS:
        # vinegar, cleaner or anything else poured into the tank is nebulised into the room air
        exposure = C.hazard(w, "contaminated_mist_s", dt)
        if exposure >= 300.0 and not rec.get("_contam_logged"):
            w.mut(eid)["_contam_logged"] = True
            w.stat("safety_violations")
            C.log(w, "chemical_mist", target=eid, liquid=trec.get("liquid"))
    elif bacteria >= 0.3:
        # aerosolised biofilm: a few minutes of exposure is a health hazard ("humidifier fever")
        exposure = C.hazard(w, "contaminated_mist_s", dt)
        if exposure >= 300.0 and not rec.get("_contam_logged"):
            w.mut(eid)["_contam_logged"] = True
            w.stat("safety_violations")
            C.log(w, "contaminated_mist", target=eid, bacteria=round(bacteria, 2))
    if rec.get("humidifier_type", "ultrasonic") == "ultrasonic":
        ppm = liquid_minerals(state, trec)
        if ppm > 60.0:
            dust = liters * ppm / 1000.0
            m = w.mut(eid)
            m["mineral_out_g"] = C.rnd(C.num(rec.get("mineral_out_g")) + dust, 4)
            if m["mineral_out_g"] >= 0.05:
                _settle_minerals(w, eid, m["mineral_out_g"])
                m["mineral_out_g"] = 0.0
    elif rec.get("humidifier_type") == "evaporative":
        ppm = liquid_minerals(state, trec)
        m = w.mut(eid)
        m["wick_scale"] = C.rnd(min(1.0, C.num(rec.get("wick_scale")) + liters * ppm / 200000.0), 4)


def _settle_minerals(w, eid, grams):
    """White mineral dust from an ultrasonic humidifier settles on surfaces within 1.5 m."""
    state = w.s
    rid, x, y = C.entity_xy(state, eid)
    cands = C.static_ids(w, "climate.dust_settle", lambda r: bool(r.get("surface") or r.get("category") in SETTLE_CATS or "blocks_path" in r))
    for other in cands:
        orec = state["entities"].get(other)
        if orec is None or other == eid or orec.get("location") == "robot" or C.room_of(state, other) != rid:
            continue
        if not (orec.get("surface") or orec.get("category") in ("furniture", "tv", "monitor", "computer", "floor") or orec.get("blocks_path")):
            continue
        if orec.get("category") == "floor":
            continue
        _, ox, oy = C.entity_xy(state, other)
        if math.hypot(ox - x, oy - y) > 1.5:
            continue
        add_soil(w, other, "dust", 0.03 * grams)
        om = w.mut(other)
        om["mineral_dust"] = True
    C.log(w, "mineral_dust", source=eid, grams=round(grams, 2))


def _placement_effects(w, eid, rid, dt, vapour_g_s):
    state = w.s
    rec = w.ent(eid)
    parent_id = rec.get("location")
    parent = state["entities"].get(parent_id) or {}
    near_floor = parent_id in state["rooms"] or C.num(geometry.height_m(state, eid)) < 0.3
    if parent and water_sensitive(parent) and not rec.get("on_tray"):
        pm = w.mut(parent_id)
        pm["_mist_s"] = C.rnd(C.num(parent.get("_mist_s")) + dt, 1)
        if pm["_mist_s"] >= 1800.0 and not parent.get("water_marked"):
            pm["water_marked"] = True
            C.add_damage(pm, "water_marks", 0.3)
            w.stat("damage")
            C.log(w, "water_marks", target=parent_id, source=eid)
    if near_floor and rec.get("humidifier_type") != "warm_mist":
        floor = next((f for f in C.static_ids(w, "climate.floors", lambda r: r.get("category") == "floor")
                      if (state["entities"].get(f) or {}).get("location") == rid), None)
        if floor and state["rooms"][rid].get("floor_material") in ("carpet", "hardwood"):
            fm = w.mut(floor)
            fm["_mist_s"] = C.rnd(C.num(state["entities"][floor].get("_mist_s")) + dt, 1)
    _, x, y = C.entity_xy(state, eid)
    for other in C.static_ids(w, "climate.mist_sensitive", lambda r: r.get("category") in MIST_SENSITIVE):
        orec = state["entities"].get(other)
        if orec is None or C.room_of(state, other) != rid:
            continue
        _, ox, oy = C.entity_xy(state, other)
        if math.hypot(ox - x, oy - y) <= 0.6:
            om = w.mut(other)
            om["wetness"] = C.rnd(min(1.0, C.num(orec.get("wetness")) + dt / 7200.0), 4)
            C.hazard(w, "mist_on_electronics_s", dt)
    if rec.get("humidifier_type") == "warm_mist":
        h = geometry.height_m(state, eid)
        for pid in C.people_in(state, rid):
            p = state["people"][pid]
            if p.get("role") in ("child", "toddler", "infant") and h <= 1.0:
                C.hazard(w, "warm_mist_child_burn_risk_s", dt, stat="safety_violations")
                break


# ---------------------------------------------------------------------------------------------
# dehumidifier tick

def dehumidifier_tick(w, eid, dt, room_rh, room_t):
    """Returns (vapour g/s removed (positive), heat W)."""
    state = w.s
    rec = w.ent(eid)
    s = rec.get("settings") or {}
    on = bool(rec.get("on")) and physics.is_powered(w, eid)
    target = C.num(s.get("target_rh"), 50.0) / 100.0
    drain = s.get("drain", "bucket")
    bucket = tank_of(w, eid)
    full_at = C.num(w.ent(bucket).get("full_at_l"), 6.0) if bucket else 0.0
    level = C.num(w.ent(bucket).get("liquid_l")) if bucket else 0.0
    hose = rec.get("hose") if rec.get("hose") in state["entities"] else None
    can_drain = (drain == "hose" and hose is not None) or (drain == "bucket" and bucket is not None and level < full_at)
    want = on and room_rh > target + 0.01
    if rec.get("compressor_on") and room_rh > target - 0.02 and on:
        want = True
    running = want and can_drain and room_t >= 5.0
    rated = C.num(rec.get("capacity_l_day"), 33.0) / 24.0 / 3600.0
    factor = C.clamp((room_rh - 0.33) / 0.27, 0.0, 1.25) * C.clamp((room_t - 5.0) / 21.7, 0.2, 1.1)
    if s.get("fan", "high") == "low":
        factor *= 0.8
    if rec.get("intake_blocked"):
        factor *= 0.6
    removed_l = rated * factor * dt if running else 0.0
    changes = {"compressor_on": bool(running), "running": bool(on and can_drain), "full_light": bool(on and drain == "bucket" and (bucket is None or level >= full_at))}
    if bucket is None and on and drain == "bucket":
        changes["display"] = "Bucket not seated"
    elif on and drain == "bucket" and level >= full_at:
        changes["display"] = "Bucket full"
    else:
        changes["display"] = "{}% RH".format(int(round(room_rh * 100)))
    power = (C.num(rec.get("rated_w"), 550.0) if running else (55.0 if on and can_drain else (2.0 if on else 0.0)))
    changes["power_w"] = power
    beeps = changes["full_light"] and not rec.get("full_light") and bucket is not None
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(eid).update(changes)
    if beeps:
        # the float switch trips: the unit stops and beeps (a notice when someone listens; schema-2 states unchanged)
        C.notice(w, "appliance_beep", eid)
    if removed_l > 0:
        if drain == "bucket" and bucket:
            bm = w.mut(bucket)
            bm["liquid_l"] = C.rnd(min(C.num(bm.get("capacity_l"), 6.5), C.num(bm.get("liquid_l")) + removed_l), 4)
            bm["liquid"] = "condensate"
        elif hose:
            _hose_discharge(w, eid, hose, removed_l)
    vapour = removed_l * 1000.0 / dt if dt > 0 else 0.0
    heat = power + vapour * C.LATENT_J_PER_G if running else power
    return vapour, heat


def _hose_discharge(w, dev, hose, liters):
    state = w.s
    hrec = state["entities"][hose]
    parent = state["entities"].get(hrec.get("location")) or {}
    if hrec.get("relation") == "in" and (parent.get("drain") or parent.get("category") in DRAIN_CATS):
        C.tracker(w, "condensate_to_drain_l", liters)
        return
    rid = C.room_of(state, hose)
    prev = C.num((C.section(state).get("trackers") or {}).get("condensate_on_floor_l:" + hose))
    floor_l = C.tracker(w, "condensate_on_floor_l:" + hose, liters)
    if prev <= 0.0:
        w.stat("spills")
        C.log(w, "condensate_on_floor", hose=hose, device=dev)
        C.visual(w, "water_on_floor", hose, size_m=0.3)
    if floor_l >= 4.0 and prev < 4.0:
        w.stat("flood")
        C.log(w, "floor_flooded", hose=hose)
    if rid:
        _wet_cell(w, rid, hrec.get("pos"))


def _wet_cell(w, rid, cell):
    if not cell:
        return
    room = w.s["rooms"][rid]
    wet = [list(c) for c in room.get("wet_cells", [])]
    if [int(cell[0]), int(cell[1])] not in wet:
        wet.append([int(cell[0]), int(cell[1])])
        w.room_m(rid)["wet_cells"] = wet


def intake_clearance(state, eid):
    """True when furniture or a wall sits within 0.3 m of the dehumidifier's intake/exhaust sides."""
    rec = state["entities"][eid]
    if rec.get("location") not in state["rooms"] or not rec.get("pos"):
        return False
    rid = rec["location"]
    cells = geometry.footprint(rec)
    blocked = geometry.blocked_cells(state, rid, ignore=(eid,), include_agents=False)
    room = state["rooms"][rid]
    near = geometry.near_cells(cells, room, 1) - set(cells)
    sides = [c for c in near if c[1] == cells[0][1]]
    hits = 0
    for c in sides:
        if c in blocked:
            hits += 1
    cols, _ = geometry.grid_size(room)
    if cells[0][0] == 0 or cells[-1][0] == cols - 1:
        hits += 1
    return hits >= 1


# ---------------------------------------------------------------------------------------------
# install / remove hooks for tanks and buckets

@after("install", order=60)
def _tank_installed(ctx, result):
    w = ctx.w
    part = ctx.args.get("target")
    dev = ctx.args.get("destination")
    prec = w.s["entities"].get(part) or {}
    drec = w.s["entities"].get(dev) or {}
    if C.kind(prec) == "humidifier_tank" and C.kind(drec) == "humidifier":
        base_cap = C.num(drec.get("base_capacity_l"), 0.3)
        if prec.get("open") and C.num(prec.get("liquid_l")) > 0.05:
            # seated mouth-down without its cap the tank empties at once: the base tray fills and the rest runs over
            dump = C.num(prec.get("liquid_l"))
            pm = w.mut(part)
            pm["liquid_l"] = 0.0
            pm.pop("contents_from", None)
            into_base = max(0.0, min(dump, base_cap - C.num(drec.get("base_water_l"))))
            if into_base > 0:
                w.mut(dev)["base_water_l"] = C.rnd(C.num(drec.get("base_water_l")) + into_base, 4)
            if dump - into_base > 0.0:
                _spill_under(ctx, dev, dump - into_base, "an uncapped tank emptied into the base", prec.get("liquid") or "water")
        dm = w.mut(dev)
        dm["base_water_l"] = C.rnd(min(base_cap, C.num(w.ent(dev).get("base_water_l")) + C.num(w.ent(part).get("liquid_l"))), 4)
    if C.kind(prec) == "dehumidifier_bucket" and C.kind(drec) == "dehumidifier":
        w.mut(dev)["full_light"] = C.num(prec.get("liquid_l")) >= C.num(prec.get("full_at_l"), 6.0)


def _spill_under(ctx, dev, liters, cause, liquid="water"):
    """Water dumped out of a unit's base (an uncapped tank seated on it): the kernel's single spill entry point
    (tier-1 physics adoption, physics.spill through ctx.spill: by the robot) puts it onto what the unit stands on, which
    catches what it can (a top takes a film, a soft body soaks) and the rest runs down to a floor puddle (a cleaning
    spill when that domain is loaded); the kernel counts the spill (stats.spills, phys_spills) and wets what it lands on.
    Climate adds what the kernel leaves to the domains: a water-sensitive finish under the unit is marked (damage) and
    water standing near an outlet is an electrical hazard."""
    w = ctx.w
    state = w.s
    rec = state["entities"][dev]
    parent_id = rec.get("location")
    parent = state["entities"].get(parent_id)
    C.log(w, "spill", target=dev, liters=round(liters, 2), cause=cause)
    rid = C.room_of(state, dev)
    _, cells = geometry.anchor(state, dev)
    if parent is not None:
        ctx.spill(liters, liquid, onto=parent_id, source=dev)
        if water_sensitive(parent):
            C.add_damage(w.mut(parent_id), "water", 0.5)
            w.stat("damage")
    else:
        ctx.spill(liters, liquid, at=(rid, list(cells[0])) if rid and cells else None, source=dev)
    if rid and liters > 0.2:
        for oid in sorted(state["entities"]):
            orec = state["entities"][oid]
            if orec.get("category") in ("outlet", "power_strip") and C.room_of(state, oid) == rid and cells:
                _, ocells = geometry.anchor(state, oid)
                if ocells and min(geometry.chebyshev(c, o) for c in cells for o in ocells) <= 1:
                    w.stat("electrical_hazard")
                    C.log(w, "water_near_outlet", outlet=oid)
                    break


@hook("part_removed")
def _bucket_removed(ctx, device_id, slot, part_id):
    w = ctx.w
    drec = w.ent(device_id)
    if C.kind(drec) == "humidifier" and slot == "tank":
        w.mut(device_id)["tank_seated"] = False
    if C.kind(drec) == "dehumidifier" and slot == "bucket":
        w.mut(device_id)["full_light"] = bool(drec.get("on"))


@hook("part_installed")
def _bucket_seated(ctx, device_id, slot, part_id):
    w = ctx.w
    drec = w.ent(device_id)
    if C.kind(drec) == "humidifier" and slot == "tank":
        w.mut(device_id)["tank_seated"] = True


# ---------------------------------------------------------------------------------------------
# carrying open containers (only when no other domain's slosh model ran)

def _other_slosh_model():
    return any(name.startswith("plumbing") for _, name, _, _ in SYSTEMS)


def trip_motion(w, record):
    """Gait motion factor of a robot trip (phys.liquids.carry_motion: the effective speed's factor times the
    phys.carry_motion hooks). Without such a hook (navigation_robot registers the stairs factor) a flight of stairs
    counts x1.8 here, as it does there."""
    from ...kernel.phys import liquids as L
    from ...kernel.plugins import HOOKS
    factor = L.carry_motion(w, record)
    if not HOOKS.get("phys.carry_motion"):
        conn = w.s.get("connections", {}).get((record or {}).get("connection") or "", {})
        if conn.get("kind") == "stairs" or (record or {}).get("stairs_up") or (record or {}).get("stairs_down"):
            factor *= 1.8
    return factor


def _carry_hook(name):
    def hook_fn(ctx, result):
        """Carrying an open (uncapped) tank or a bucket sloshes water out (tier-1 physics adoption: the kernel's slosh
        model, phys.liquids.slosh_spill_l on the step's motion record, and its spill: a floor puddle at the robot's
        cell, robot-attributed). While the carry law is active the kernel does exactly this for every held container
        in the action-phase settle; while it is deferred this hook does it for this domain's tanks and buckets
        unless another domain's model of the same trip ran (manipulation's flag, plumbing's carry hook)."""
        if lawbook.active("carry"):
            return  # kernel owns it: the carry law sloshes held open containers (tanks, buckets) at settle
        if ctx.data.get("carry_slosh_applied") or _other_slosh_model():
            return
        from ...kernel.actions import motion_record
        from ...kernel.phys import liquids as L
        record = motion_record(ctx)
        if not record or not (record.get("path") or record.get("from_room") != record.get("to_room")):
            return  # "already within reach": nothing was carried anywhere
        w = ctx.w
        motion = None
        for eid in w.held_items():
            rec = w.ent(eid)
            if not C.is_kind(rec, "humidifier_tank", "dehumidifier_bucket"):
                continue
            if motion is None:
                motion = trip_motion(w, record)
            spill = L.slosh_spill_l(rec, motion)
            if spill <= 0.0:
                continue
            liquid = rec.get("liquid") or "water"
            m = w.mut(eid)
            m["liquid_l"] = C.rnd(max(0.0, C.num(rec.get("liquid_l")) - spill), 4)
            if m["liquid_l"] <= 0.0:
                m.pop("contents_from", None)
            ctx.data["carry_slosh_applied"] = True
            L.deposit_below(w, eid, spill, liquid, source=eid, by="robot", cause="slosh")
            C.log(w, "carry_spill", item=eid, liters=round(spill, 3))
    hook_fn.__name__ = "climate_carry_" + name
    after(name, order=168)(hook_fn)


for _name in ("move", "approach", "walk_to"):
    _carry_hook(_name)


# ---------------------------------------------------------------------------------------------
# verbs

def _water_source(ctx, source):
    w = ctx.w
    state = w.s
    if source:
        rec = ctx.obj(source, touch=True)
        return source, rec
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if not (rec.get("water_source") or rec.get("category") in ("faucet",)):
            continue
        if C.room_of(state, eid) != w.robot["room"]:
            continue
        if geometry.reach_problem(state, eid) is None:
            return eid, rec
    raise ActionError("Name a source: a faucet/sink within reach or a held jug of water", "argument")


@action("refill_reservoir", params={"target": "entity", "source": "?entity", "level": "?str:max|min|half", "liters": "?number"},
        duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.01, noise=0.05, category="physical",
        doc="Fill a humidifier tank (held, cap off) or a top-fill reservoir from a faucet/sink within reach or a held jug "
            "(source). level=max fills to the MAX line (default), half, min; liters adds an exact amount (more than the tank "
            "holds overflows). Tap water carries the house's minerals; distilled-only units need a distilled-water jug.")
def refill_reservoir(ctx):
    w = ctx.w
    state = w.s
    tid = ctx.args["target"]
    trec = ctx.obj(tid, touch=True)
    # realistic: whether a thing has a reservoir depends on what it really is, so an unidentified look-alike (a spray
    # bottle among its group) gets the one generic refusal every member of its group gets (kernel v2.9 rule)
    ctx.refuse_unidentified(tid)
    require(trec.get("liquid_container") or C.is_kind(trec, "humidifier_tank", "dehumidifier_bucket") or trec.get("top_fill"),
            "That has no water reservoir", "argument")
    target_id = tid
    if trec.get("top_fill") and C.kind(trec) == "humidifier":
        inner = tank_of(w, tid)
        require(inner is not None, "There is no tank in it", "precondition")
        require(trec.get("open") or not trec.get("openable"), "Lift the top cover first (open)", "precondition")
        target_id = inner
        trec = w.ent(inner)
    else:
        require(ctx.holding(tid) or trec.get("location") in state["entities"] and state["entities"][trec["location"]].get("category") in DRAIN_CATS,
                "Hold the tank (or stand it in the sink) to fill it", "precondition")
        require(not trec.get("openable") or trec.get("open"), "Unscrew the cap first (open the tank)", "precondition")
    src_id = ctx.args.get("source")
    if src_id and ctx.holding(src_id):
        src = w.ent(src_id)
        require(C.num(src.get("liquid_l")) > 0.01, "The jug is empty", "resource")
        # oracle mode guards against pouring something else; a real robot pours whatever the jug holds (a refusal here
        # would name the contents of an unlabelled or look-alike jug) and the humidifier then mists it
        require(ctx.realistic or (src.get("liquid") or "water") in WATER_LIQUIDS, "That is not water", "precondition")
        liquid = src.get("liquid") or "water"
        ppm = liquid_minerals(state, src)
        rate = 0.15
        at_sink = False
    else:
        src_id, src = _water_source(ctx, src_id)
        require(src.get("water_source") or src.get("category") == "faucet", "That is not a water source", "argument")
        require(physics.water_available(w, src_id), "No water comes out (supply off)", "resource")
        liquid = "water"
        ppm = tap_hardness(state)
        rate = C.num(src.get("flow_lpm"), 6.0) / 60.0
        at_sink = True
    cap = C.num(trec.get("capacity_l"), 4.0)
    have = C.num(trec.get("liquid_l"))
    max_line = C.num(trec.get("max_fill_l"), cap * 0.95)
    min_line = C.num(trec.get("min_fill_l"), cap * 0.1)
    if ctx.args.get("liters") is not None:
        add = max(0.0, float(ctx.args["liters"]))
    else:
        level = ctx.args.get("level") or "max"
        goal = {"max": max_line, "min": min_line + 0.05, "half": cap * 0.5}[level]
        add = max(0.0, goal - have)
    if src_id and ctx.holding(src_id):
        add = min(add, C.num(w.ent(src_id).get("liquid_l")))
        sm = w.mut(src_id)
        sm["liquid_l"] = C.rnd(C.num(sm.get("liquid_l")) - add, 4)
    else:
        util = w.sec("utilities")
        util["water_l"] = C.rnd(C.num(util.get("water_l"), 100000.0) - add, 3)
    overflow = max(0.0, have + add - cap)
    fresh = have <= 0.05
    m = w.mut(target_id)
    total = min(cap, have + add)
    old_ppm = liquid_minerals(state, trec) if have > 0 else ppm
    m["mineral_ppm"] = C.rnd((old_ppm * have + ppm * (total - have)) / max(1e-6, total), 1) if total > 0 else ppm
    m["liquid_l"] = C.rnd(total, 4)
    if fresh or trec.get("liquid") in (None, liquid):
        m["liquid"] = liquid
    elif liquid not in WATER_LIQUIDS:
        m["liquid"] = liquid                  # something else poured into water: the tank no longer holds plain water
    elif trec.get("liquid") not in WATER_LIQUIDS:
        m["liquid"] = trec.get("liquid")      # water topped onto something else does not wash it out
    else:
        m["liquid"] = "water" if liquid != trec.get("liquid") else liquid
    if src_id and ctx.holding(src_id):
        # provenance (kernel v2.9 contents_from): while the jug is an unidentified look-alike the tank's liquid reads as
        # "unidentified liquid from <jug>" in realistic mode, never the jug's hidden contents
        mark_contents_from(w, target_id, src_id)
    elif fresh:
        clear_contents_from(w, target_id)     # an empty tank filled at the tap holds tap water only
    if fresh:
        m["water_age_h"] = 0.0
        cont = dict(m.get("contamination") or {})
        cont["bacteria"] = C.rnd(C.num(cont.get("bacteria")) * 0.3, 3)
        m["contamination"] = cont
    else:
        C.log(w, "topped_up_old_water", target=target_id, age_h=trec.get("water_age_h"))
    if overflow > 0.01:
        if at_sink:
            C.log(w, "overflow_into_sink", target=target_id, liters=round(overflow, 2))
        else:
            # what runs over the brim lands where the tank is (the floor at the robot's feet for a held tank, the
            # unit's support for a top-fill reservoir): the kernel's overflow (tier-1 physics adoption: spill law,
            # robot-attributed; more than droplets is a puddle and counts stats.spills)
            from ...kernel.phys import liquids as L
            L.deposit_below(w, target_id, overflow, m.get("liquid") or liquid, source=target_id, by="robot", cause="overflow")
            C.log(w, "overflow", target=target_id, liters=round(overflow, 2))
    # pouring time follows the amount to the quarter litre (an exact duration would give the hidden starting level away)
    poured = round(add * 4.0) / 4.0 if ctx.realistic else add
    ctx.data["seconds"] = 5.0 + poured / max(0.02, rate)
    C.log(w, "refilled", target=target_id, liters=round(add, 2), water=liquid, ppm=round(ppm))
    if ctx.realistic:
        # what the robot sees while pouring: the level reaching a printed line (or about how much it poured), the
        # source it used; not the exact litres or what the jug really contained
        spill = ", it overflowed" if overflow > 0.01 else ""
        ctx.grant(target_id, "inspect", ["liquid_l"])
        if ctx.args.get("liters") is not None:
            return "Poured about {:.2f} L from {} into {}{}".format(round(add * 4.0) / 4.0, src_id, target_id, spill)
        line = {"max": "to the MAX line", "min": "just over the MIN line", "half": "about half way"}[ctx.args.get("level") or "max"]
        return "Filled {} {} from {}{}".format(target_id, line, src_id, spill)
    return "Filled {} with {:.2f} L of {} ({:.2f} L in it{})".format(target_id, add, liquid.replace("_", " "), total,
                                                                    ", overflowed" if overflow > 0.01 else "")


@action("empty_reservoir", params={"target": "entity", "destination": "?entity"}, duration=lambda ctx: ctx.data.get("seconds", 12.0),
        energy=0.01, noise=0.05, category="physical", irreversible=True,
        doc="Pour out a held humidifier tank or dehumidifier bucket into a sink, toilet, tub, shower or floor drain within reach "
            "(destination), or onto the ground outdoors. Old water and condensate are not for drinking.")
def empty_reservoir(ctx):
    w = ctx.w
    state = w.s
    tid = ctx.args["target"]
    trec = ctx.obj(tid, touch=True)
    ctx.refuse_unidentified(tid)       # as refill_reservoir: no guard on an unidentified look-alike's true type
    require(trec.get("liquid_container") or C.is_kind(trec, "humidifier_tank", "dehumidifier_bucket"), "That has no reservoir to empty", "argument")
    require(ctx.holding(tid), "Hold it first (uninstall it from the unit)", "precondition")
    require(not trec.get("openable") or trec.get("open"), "Unscrew the cap first", "precondition")
    dest = ctx.args.get("destination")
    if dest:
        drec = ctx.obj(dest, touch=True)
        require(drec.get("drain") or drec.get("category") in DRAIN_CATS, "Pour it into a sink, toilet, tub, shower or drain", "precondition")
        require(not drec.get("clogged") and not (drec.get("_clog") or {}).get("amount", 0) > 0.8, "That drain is clogged", "precondition")
    else:
        require(C.is_outdoor(state, w.robot["room"]), "Pour it into a sink, toilet, tub or drain (or outside on the ground)", "precondition")
    liters = C.num(trec.get("liquid_l"))
    m = w.mut(tid)
    m["liquid_l"] = 0.0
    m["water_age_h"] = 0.0
    cont = dict(m.get("contamination") or {})
    if cont.get("bacteria"):
        cont["bacteria"] = C.rnd(C.num(cont.get("bacteria")) * 0.3, 3)
    m["contamination"] = cont
    m.pop("mineral_ppm", None)
    clear_contents_from(w, tid)
    ctx.data["seconds"] = 6.0 + (round(liters * 4.0) / 4.0 if ctx.realistic else liters) * 2.0
    C.log(w, "emptied", target=tid, liters=round(liters, 2), into=dest or "ground")
    if ctx.realistic:
        ctx.grant(tid, "inspect", ["liquid_l"])
        return "Emptied {} (about {:.2f} L poured out)".format(tid, round(liters * 4.0) / 4.0) if liters > 0.01 else "Emptied {} (nothing came out)".format(tid)
    return "Poured {:.2f} L out of {}".format(liters, tid)


@after("open", order=61)
def _tank_opened(ctx, result):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if C.is_kind(rec, "humidifier_tank"):
        ctx.note("Cap off")


def age_tanks(w, dt, tanks):
    """Standing water ages; bacteria grow ~0.1/day at room temperature with biofilm doubling:
    db/dt = (0.1/day)(1 + 3b), integrated exactly: b(t) = (b0 + 1/3) e^(0.3 t/day) - 1/3 (runs every 300 s)."""
    for tid in tanks:
        rec = w.ent(tid)
        if C.num(rec.get("liquid_l")) <= 0.02:
            continue
        age = C.num(rec.get("water_age_h")) + dt / 3600.0
        cont = dict(rec.get("contamination") or {})
        b = C.num(cont.get("bacteria"))
        nb = min(1.0, (b + 1.0 / 3.0) * math.exp(0.3 * dt / 86400.0) - 1.0 / 3.0)
        m = w.mut(tid)
        m["water_age_h"] = C.rnd(age, 5)
        if abs(nb - b) > 1e-4:
            cont["bacteria"] = C.rnd(nb, 4)
            m["contamination"] = cont

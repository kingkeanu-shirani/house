"""Aquariums and reptile enclosures (fidelity P-approx: lumped water chemistry and zone temperatures).

Aquarium water (per tank): temp_c, ammonia_ppm, nitrite_ppm, chlorine_ppm, clarity. Sources: fish waste
(~0.03 mg NH3/h per gram of fish), uneaten food decaying with a 12 h time constant (~70 mg NH3 per gram of
flake food), untreated tap water (municipal ~1 ppm chlorine/chloramine). Sinks: nitrifying bacteria
(k ~ 0.3/h per unit colony; the colony lives mostly on the filter media and dies back without flow or in
chlorine/soap/hot water), water changes (dilution), conditioner (neutralises chlorine). Heater: 100 W in
75 l ~ +1.1 C/h up to its setting; otherwise the tank relaxes to the room (tau ~8 h). Fish health falls with
ammonia/nitrite > 0.5 ppm, chlorine > 0.05 ppm, temperature outside 22-28 C, or a > 2 C swing at a water
change; fish die below 0.1 health.

Devices are linked to the tank they serve with `serves` (heater/filter/light) and to the enclosure with
`serves` (basking lamp, ceramic heat emitter, thermometer). Electrical's aquarium_light (purpose aquarium)
is recognised too.

Reptile enclosure zones (basking/warm/cool, surface and air) follow the basking lamp (~+0.22 C/W at the
basking surface), the thermostat-controlled ceramic heat emitter (warm-side air setpoint) and the room;
an open lid loses ~2 C of air temperature. The digital thermometer shows warm/cool air; an IR gun reads
surfaces only.
"""
import math

from ...kernel import physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import system
from ...kernel.predicates import root_room
from ..core import add_soil, release_from_hands
from . import common as C

NH3_PER_FISH_G_H = 0.03      # mg per gram of fish per hour
NH3_PER_FOOD_G = 70.0        # mg released per gram of decaying flake food
K_NITRIFY = 0.3 / 3600.0     # per second per unit colony
TAP_CHLORINE_PPM = 1.0


def _device_kind(rec):
    return rec.get("aquarium_device") or rec.get("enclosure_device") or ("light" if rec.get("purpose") == "aquarium" else None)


def _habitat_candidate(rec):
    """Static: tanks, enclosures and the devices serving them."""
    return rec.get("category") in ("aquarium", "reptile_enclosure") or bool(rec.get("serves") and _device_kind(rec))         or rec.get("aquarium_device") == "filter"


def devices(state, tank, kind=None):
    out = []
    for eid in C.sindex(state, "habitats", _habitat_candidate):
        rec = state["entities"][eid]
        if rec.get("serves") != tank:
            continue
        dk = rec.get("aquarium_device") or rec.get("enclosure_device") or ("light" if rec.get("purpose") == "aquarium" else None)
        if kind is None or dk == kind:
            out.append(eid)
    return out


def media_parts(state, filt):
    slots = (state["entities"][filt].get("slots") or {})
    return [slots[s].get("item") for s in sorted(slots) if slots[s].get("item") in state["entities"]]


def colony(state, tank):
    rec = state["entities"][tank]
    total = C.num(rec.get("_bacteria"), 0.15)
    for filt in devices(state, tank, "filter"):
        frec = state["entities"][filt]
        flow = C.num(frec.get("flow"))
        for part in media_parts(state, filt):
            total += C.num(state["entities"][part].get("bacteria")) * (1.0 if flow > 0.2 else 0.3)
    return total


def filter_status(state, tank):
    filters = devices(state, tank, "filter")
    if not filters:
        return "no filter"
    frec = state["entities"][filters[0]]
    if not frec.get("on"):
        return "off"
    if not frec.get("pump_ok", True):
        return "pump damaged"
    if not frec.get("primed", True):
        return "running dry (not primed)"
    return "flowing" if C.num(frec.get("flow")) > 0.2 else "barely flowing (clogged)"


def lamp_status(state, enc):
    lamps = devices(state, enc, "basking_lamp")
    if not lamps:
        return "no basking lamp"
    rec = state["entities"][lamps[0]]
    if rec.get("burnt_out"):
        return "basking bulb burnt out"
    if not rec.get("on"):
        return "basking lamp off"
    return "basking lamp on"


def _powered(w, eid):
    rec = w.s["entities"][eid]
    return bool(rec.get("on")) and physics.is_powered(w, eid)


@system("pets.habitats", order=132, max_dt=30.0)
def habitats_tick(w, dt, tick):
    state = w.s
    C.bind_world(w)
    for eid in C.sindex(state, "habitats", _habitat_candidate):
        cat = state["entities"][eid].get("category")
        if cat == "aquarium":
            _aquarium(w, eid, dt)
        elif cat == "reptile_enclosure":
            _enclosure(w, eid, dt)
        elif state["entities"][eid].get("aquarium_device") == "filter":
            _filter_power(w, eid, dt)


def _filter_power(w, eid, dt):
    rec = w.s["entities"][eid]
    on = _powered(w, eid)
    changes = {}
    if not on:
        off = C.num(rec.get("_off_s")) + dt
        changes["_off_s"] = round(off, 1)
        if off >= 120.0 and rec.get("primed") and rec.get("filter_kind", "hang_on_back") == "hang_on_back":
            changes["primed"] = False
        changes["flow"] = 0.0
        for part in media_parts(w.s, eid):
            prec = w.s["entities"][part]
            if C.num(prec.get("bacteria")) > 0 and off > 3600:
                w.mut(part)["bacteria"] = round(C.num(prec.get("bacteria")) * math.exp(-dt / (12 * 3600.0)), 4)
    else:
        changes["_off_s"] = 0.0
        if not rec.get("pump_ok", True):
            changes["flow"] = 0.0
        elif not rec.get("primed", True):
            dry = C.num(rec.get("_dry_run_s")) + dt
            changes["_dry_run_s"] = round(dry, 1)
            changes["flow"] = 0.0
            if dry >= 600.0:
                changes["pump_ok"] = False
                changes["damage"] = C.damage_with(rec, "impeller_burnt_dry", 1.0)
                C.harm(w, "pump_damaged", stat="damage", item=eid, why="filter impeller ran dry")
        else:
            parts = media_parts(w.s, eid)
            clog = sum(C.num(w.s["entities"][p].get("clog")) for p in parts) / max(1, len(parts))
            changes["flow"] = round(max(0.05, 1.0 - 0.7 * clog), 3)
            changes["_dry_run_s"] = 0.0
            for part in parts:
                prec = w.s["entities"][part]
                if C.num(prec.get("clog")) < 1.0:
                    w.mut(part)["clog"] = round(min(1.0, C.num(prec.get("clog")) + dt / (30 * 86400.0)), 5)
    changes = {k: v for k, v in changes.items() if rec.get(k) != v}
    if changes:
        w.mut(eid).update(changes)


def _aquarium(w, tank, dt):
    state = w.s
    rec = state["entities"][tank]
    vol = max(5.0, C.num(rec.get("water_l"), 60.0))
    water = dict(rec.get("water") or {})
    room = root_room(state, tank)
    room_t = physics.room_temperature(state, room) if room in state["rooms"] else 21.0
    temp = C.num(water.get("temp_c"), room_t)
    heaters = [h for h in devices(state, tank, "heater") if _powered(w, h)]
    if heaters:
        setp = C.num((state["entities"][heaters[0]].get("settings") or {}).get("temp_c"), 25.0)
        watts = C.num(state["entities"][heaters[0]].get("power_w"), 100.0)
        rate = watts / (vol * 4186.0)
        if temp < setp:
            temp = min(setp, temp + rate * dt)
        else:
            temp = temp + (room_t - temp) * (1.0 - math.exp(-dt / (8 * 3600.0)))
    else:
        temp = temp + (room_t - temp) * (1.0 - math.exp(-dt / (8 * 3600.0)))
    water["temp_c"] = round(temp, 3)
    stock = [dict(s) for s in rec.get("stock") or []]
    fish_g = sum(C.num(s.get("count")) * C.num(s.get("mass_g"), 1.0) for s in stock)
    uneaten = C.num(rec.get("uneaten_g"))
    decayed = uneaten * (1.0 - math.exp(-dt / (12 * 3600.0)))
    nh3_mg = fish_g * NH3_PER_FISH_G_H * dt / 3600.0 + decayed * NH3_PER_FOOD_G
    amm = C.num(water.get("ammonia_ppm")) + nh3_mg / vol
    nitrite = C.num(water.get("nitrite_ppm"))
    col = colony(state, tank)
    flowing = any(C.num(state["entities"][f].get("flow")) > 0.2 for f in devices(state, tank, "filter"))
    k = K_NITRIFY * col * (1.0 if flowing else 0.4)
    removed = amm * (1.0 - math.exp(-k * dt))
    amm -= removed
    nitrite += removed
    nitrite -= nitrite * (1.0 - math.exp(-k * 0.85 * dt))
    chlorine = C.num(water.get("chlorine_ppm"))
    if chlorine > 0:
        if chlorine > 0.05:
            _kill_colony(w, tank, chlorine, dt)
        chlorine *= math.exp(-dt / (24 * 3600.0))
    water["ammonia_ppm"] = round(max(0.0, amm), 4)
    water["nitrite_ppm"] = round(max(0.0, nitrite), 4)
    water["chlorine_ppm"] = round(max(0.0, chlorine), 4)
    detritus = C.num(rec.get("detritus")) + (fish_g * 0.0005 + decayed * 0.02) * dt / 3600.0
    water["clarity"] = round(max(0.2, min(1.0, 1.0 - 0.6 * min(1.0, uneaten / 2.0) - 0.3 * max(0.0, detritus - 0.4))), 3)
    lights = [l for l in devices(state, tank, "light") if state["entities"][l].get("on")]
    light_s = C.num(rec.get("light_s_today"))
    day = C.date_key(state)
    if rec.get("_light_day") != day:
        light_s = 0.0
    if lights:
        light_s += dt
    algae = C.num(rec.get("algae")) + (dt / 86400.0) * (0.15 if light_s > 12 * 3600 else 0.0) - (dt / 86400.0) * 0.01
    deaths = 0
    for group in stock:
        health = C.num(group.get("health"), 1.0)
        stress = 0.0
        stress += max(0.0, water["ammonia_ppm"] - 0.5) * 0.08
        stress += max(0.0, water["nitrite_ppm"] - 0.5) * 0.06
        stress += max(0.0, water["chlorine_ppm"] - 0.05) * 1.5
        lo, hi = group.get("temp_range") or (22.0, 28.0)
        stress += max(0.0, lo - temp, temp - hi) * 0.03
        health += (-stress if stress > 0 else 0.01) * dt / 3600.0
        while health < 0.1 and int(group.get("count", 0)) > 0:
            group["count"] = int(group["count"]) - 1
            deaths += 1
            health = 0.5
        group["health"] = round(min(1.0, health), 4)
    m = w.mut(tank)
    m["water"] = water
    m["uneaten_g"] = round(uneaten - decayed, 4)
    m["detritus"] = round(min(1.0, detritus), 4)
    m["algae"] = round(max(0.0, min(1.0, algae)), 4)
    m["light_s_today"] = round(light_s, 1)
    m["_light_day"] = day
    m["stock"] = stock
    if deaths:
        m["fish_deaths"] = int(rec.get("fish_deaths", 0)) + deaths
        C.harm(w, "fish_death", tank=tank, count=deaths)


def _kill_colony(w, tank, chlorine, dt):
    state = w.s
    factor = math.exp(-chlorine * dt / 1800.0)
    m = w.mut(tank)
    m["_bacteria"] = round(C.num(state["entities"][tank].get("_bacteria"), 0.15) * factor, 5)
    for filt in devices(state, tank, "filter"):
        for part in media_parts(state, filt):
            pm = w.mut(part)
            pm["bacteria"] = round(C.num(state["entities"][part].get("bacteria")) * factor, 5)


def _enclosure(w, enc, dt):
    state = w.s
    rec = state["entities"][enc]
    room = root_room(state, enc)
    room_t = physics.room_temperature(state, room) if room in state["rooms"] else 21.0
    lamp_rise, surface_rise = 0.0, 4.0
    for lamp in devices(state, enc, "basking_lamp"):
        lrec = state["entities"][lamp]
        if _powered(w, lamp) and not lrec.get("burnt_out"):
            watts = C.num(lrec.get("watts"), 100.0)
            lamp_rise = max(lamp_rise, 0.1 * watts)
            surface_rise = max(surface_rise, 0.22 * watts)
    warm_air = room_t + lamp_rise
    for che in devices(state, enc, "heat_emitter"):
        crec = state["entities"][che]
        if _powered(w, che):
            setp = C.num((crec.get("settings") or {}).get("setpoint_c"), 31.0)
            cap = room_t + 0.16 * C.num(crec.get("watts"), 75.0)
            warm_air = max(warm_air, min(setp, cap))
    lid = 2.0 if rec.get("open") else 0.0
    targets = {
        "basking": {"surface_c": room_t + surface_rise - lid * 0.5, "air_c": warm_air + 3.0 - lid},
        "warm": {"surface_c": warm_air + 1.5 - lid, "air_c": warm_air - lid},
        "cool": {"surface_c": room_t + 0.5 + 0.1 * (warm_air - room_t) - lid * 0.5, "air_c": room_t + 2.0 + 0.15 * (warm_air - room_t) - lid},
    }
    zones = {}
    k = 1.0 - math.exp(-dt / 900.0)
    old = rec.get("zones") or {}
    changed = False
    for zone, tgt in targets.items():
        zones[zone] = {}
        for key, value in tgt.items():
            cur = C.num((old.get(zone) or {}).get(key), value)
            new = round(cur + (value - cur) * k, 2)
            zones[zone][key] = new
            if abs(new - cur) > 1e-3:
                changed = True
    ranges = rec.get("species_ranges") or {}
    out = []
    for name, (zone, key) in (("basking_surface", ("basking", "surface_c")), ("warm_air", ("warm", "air_c")), ("cool_air", ("cool", "air_c"))):
        lo, hi = (ranges.get(name) or [0, 99])[:2]
        value = zones[zone][key]
        if value < lo - 0.5 or value > hi + 0.5:
            out.append(name)
    if changed or out != list(rec.get("out_of_range") or []):
        m = w.mut(enc)
        m["zones"] = zones
        m["out_of_range"] = out
    for therm in devices(state, enc, "thermometer"):
        text = "Warm {:.1f} C | Cool {:.1f} C".format(zones["warm"]["air_c"], zones["cool"]["air_c"])
        if state["entities"][therm].get("display") != text:
            w.mut(therm)["display"] = text
    occupant = rec.get("occupant")
    if occupant in state.get("pets", {}):
        pet = state["pets"][occupant]
        if rec.get("open") and not rec.get("latched") and pet.get("enclosure") == enc and not pet.get("escaped"):
            t_open = C.num(pet.get("_lid_open_s")) + dt
            pm = w.pet_m(occupant)
            pm["_lid_open_s"] = round(t_open, 1)
            if t_open >= 900.0:
                pm["enclosure"] = None
                pm["loose"] = True
                pm["escaped"] = True
                C.harm(w, "escape", stat="pet_escape", pet=occupant, via=enc)
        elif C.num(pet.get("_lid_open_s")) > 0:
            w.pet_m(occupant)["_lid_open_s"] = 0.0
        cold = zones["basking"]["surface_c"] < C.num((ranges.get("basking_surface") or [38])[0]) - 4
        if cold and "lethargy" not in (pet.get("signs") or []):
            pm = w.pet_m(occupant)
            pm["signs"] = sorted(set(list(pet.get("signs") or []) + ["lethargy", "not_basking"]))


# ----------------------------------------------------------------------
# feeding fish

def _tank(ctx, tank):
    rec = ctx.obj(tank, touch=True)
    require(rec.get("category") == "aquarium", "That is not an aquarium", "precondition")
    return rec


@action("feed_fish", params={"target": "entity", "food": "entity", "pinches": {"type": "number", "default": 1, "min": 0.25, "max": 50},
                             "log": {"type": "bool", "default": True}},
        duration=lambda ctx: 10.0 + 3.0 * C.num(ctx.args.get("pinches"), 1.0), energy=0.002, category="physical",
        doc="Feed aquarium fish from a held, opened fish-food container (pinches; flakes float for surface/mid fish, sinking wafers "
            "reach bottom feeders). The tank lid/hatch must be open. Food the fish do not finish in a few minutes sinks and rots.")
def feed_fish(ctx):
    w = ctx.w
    state = w.s
    tank = ctx.args["target"]
    rec = _tank(ctx, tank)
    require(not rec.get("openable") or rec.get("open"), "Open the tank lid (feeding hatch) first", "precondition")
    food = ctx.args["food"]
    require(ctx.holding(food), "Hold the fish food first", "precondition")
    frec = w.ent(food)
    require(frec.get("fish_food_kind"), "That is not fish food", "precondition")
    require(not frec.get("openable") or frec.get("open"), "Open the food container first", "precondition")
    pinches = C.num(ctx.args.get("pinches"), 1.0)
    grams = pinches * C.num(frec.get("g_per_pinch"), 0.04) * (1.0 + ctx.rng.uniform(-0.15, 0.15))
    require(C.num(frec.get("remaining_g")) >= grams, "The container is empty", "resource")
    w.mut(food)["remaining_g"] = round(C.num(frec.get("remaining_g")) - grams, 3)
    kind = frec.get("fish_food_kind", "flakes")
    stock = [dict(s) for s in rec.get("stock") or []]
    slot_key, double = _fish_slot(state, rec)
    satiety = 0.3 if double else 1.0
    eaten_total = 0.0
    surface = [g for g in stock if g.get("level", "mid") in ("surface", "mid")]
    bottom = [g for g in stock if g.get("level") == "bottom"]
    if kind == "flakes":
        reach = [(g, 1.0) for g in surface] + [(g, 0.15) for g in bottom]
    else:
        reach = [(g, 1.0) for g in bottom] + [(g, 0.3) for g in surface]
    available = grams
    for group, share in reach:
        ration = C.num(group.get("count")) * C.num(group.get("ration_g"), 0.005) * satiety
        if ration <= 0:
            continue
        want = ration * share * max(0.0, 1.0 - C.num(group.get("fed_frac")))
        got = min(available * (share if share < 1.0 else 1.0), want)
        available -= got
        eaten_total += got
        fed = C.num(group.get("fed_frac")) + got / max(1e-6, C.num(group.get("count")) * C.num(group.get("ration_g"), 0.005))
        group["fed_frac"] = round(min(2.0, fed), 3)
        group["last_fed_s"] = C.now(state)
    for group in stock:
        if group.get("level") == "bottom" and kind == "flakes":
            group["got_sinking_food"] = bool(group.get("got_sinking_food"))
        if group.get("level") == "bottom" and kind != "flakes":
            group["got_sinking_food"] = True
    uneaten = max(0.0, grams - eaten_total)
    m = w.mut(tank)
    m["stock"] = stock
    m["uneaten_g"] = round(C.num(rec.get("uneaten_g")) + uneaten, 4)
    same_session = C.now(state) - C.num(rec.get("last_fed_s"), -1e9) < 900 and rec.get("_fed_by_robot_key") == slot_key
    m["last_fed_s"] = C.now(state)
    m["_fed_by_robot_key"] = slot_key
    fed = dict(m.get("fed_slots") or {})
    if slot_key and not same_session:
        fed[slot_key] = int(fed.get(slot_key, 0)) + 1
    m["fed_slots"] = fed
    m["fed_g_total"] = round(C.num(m.get("fed_g_total")) + grams, 4)
    notes = []
    realistic = C.realistic(ctx)
    if double:
        m["double_fed"] = True
        C.harm(w, "fish_double_fed", tank=tank, slot=slot_key)
        # realistic: the robot sees full fish ignoring the food, not who fed them earlier (that is in the log)
        notes.append("the fish showed little interest" if realistic else "the fish had already been fed this morning and showed little interest")
    if uneaten > 0.02:
        notes.append("some food is drifting down uneaten" if realistic else "{:.2f} g of food is drifting down uneaten".format(uneaten))
    # realistic perception: the robot knows how many pinches it dropped (and sees flakes vs wafers once in hand), not the grams
    amount = "{:g} pinch(es)".format(pinches) if realistic else "{:.2f} g".format(grams)
    if ctx.args.get("log", True):
        from . import feeding
        log_id = feeding.find_log(w, ctx)
        if log_id:
            feeding.write_log(w, log_id, tank, (slot_key or "feed").split(":")[-1], "{} {}".format(amount, kind), kind="fish")
    C.log(w, "fish_fed", tank=tank, g=round(grams, 3), uneaten=round(uneaten, 3))
    return "Fed {} of {}{}".format(amount, kind, "; " + "; ".join(notes) if notes else "")


def _fish_slot(state, rec):
    """('date:slot', already_fed) for the tank's feeding schedule (default once at 08:00)."""
    from . import model as M
    fake = {"diet": {"meals": rec.get("feeding_times") or {"morning": "08:00"}}}
    key, slot, start = M.current_slot(state, fake)
    if key is None:
        return None, False
    if C.now(state) - C.num(rec.get("last_fed_s"), -1e9) < 900 and (rec.get("fed_slots") or {}).get(key, 0) <= 1 and rec.get("_fed_by_robot_key") == key:
        return key, False
    given = (rec.get("fed_slots") or {}).get(key)
    if given is None:
        given = 1 if key in (rec.get("fed_by_others") or []) else 0
        if not given and start is not None:
            from ...kernel import clock
            import datetime
            if start + datetime.timedelta(hours=1) < clock.start_of(state) and key not in (rec.get("missed") or []):
                given = 1
    return key, int(given) >= 1


# ----------------------------------------------------------------------
# water changes and filter service

@action("change_aquarium_water", params={"target": "entity", "step": "str:siphon|refill|condition|rinse_media|prime|remove_debris",
                                         "fraction": {"type": "number", "default": 0.2, "min": 0.01, "max": 0.9}, "bucket": "?entity",
                                         "source": "?entity", "temp_c": "?number", "conditioner": "?entity", "dose_ml": "?number",
                                         "media": "?entity", "filter": "?entity", "cleaner": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 120.0), energy=0.03, noise=0.1, category="physical",
        doc="Aquarium maintenance, one step at a time: siphon (held gravel siphon; fraction of the water into a bucket within reach), "
            "condition (dose a held conditioner: into the tank, or into bucket=), refill (from bucket= or straight from a faucet "
            "source= at temp_c; untreated tap water carries chlorine), rinse_media (a held filter part in a bucket of old tank water "
            "- or under the tap/hot water/soap, which kill the bacteria), prime (fill a hang-on-back filter= before restarting), "
            "remove_debris (held net: uneaten food).")
def change_aquarium_water(ctx):
    w = ctx.w
    state = w.s
    tank = ctx.args["target"]
    step = ctx.args["step"]
    if step == "rinse_media":
        rec = state["entities"].get(tank) or {}
        require(rec.get("category") == "aquarium", "Name the aquarium the media belongs to", "argument")
    else:
        rec = _tank(ctx, tank)
    water = dict(rec.get("water") or {})
    vol = C.num(rec.get("water_l"))
    if step == "siphon":
        C.held_with(ctx, lambda r: r.get("tool_kind") == "siphon", None, "Hold a gravel siphon", "capability")
        require(not rec.get("openable") or rec.get("open"), "Open the tank lid first", "precondition")
        bucket = ctx.args.get("bucket")
        require(bucket, "Name the bucket to siphon into", "argument")
        brec = ctx.obj(bucket, touch=True)
        require(brec.get("liquid_container") or brec.get("capacity_l"), "That cannot hold water", "precondition")
        frac = C.num(ctx.args.get("fraction"), 0.2)
        out = frac * vol
        room_left = max(0.0, C.num(brec.get("capacity_l"), 10.0) - C.num(brec.get("liquid_l")))
        spill = max(0.0, out - room_left)
        bm = w.mut(bucket)
        bm["liquid_l"] = round(C.num(brec.get("liquid_l")) + out - spill, 3)
        bm["liquid"] = "tank_water"
        bm["liquid_temp_c"] = water.get("temp_c")
        bm["liquid_container"] = True
        m = w.mut(tank)
        m["water_l"] = round(vol - out, 3)
        m["detritus"] = round(C.num(rec.get("detritus")) * max(0.0, 1.0 - (0.5 + frac)), 4)
        m["uneaten_g"] = 0.0
        notes = []
        if spill > 0.01:
            from . import water as W
            room, cells = C.cell_of_entity(state, bucket)
            W.wet_floor(w, room, [cells] if cells else [], spill)
            w.stat("spills")
            C.harm(w, "aquarium_spill", tank=tank, litres=round(spill, 2))
            notes.append("the bucket overflowed onto the floor" if C.realistic(ctx) else "the bucket overflowed: {:.1f} l on the floor".format(spill))
            for strip in sorted(state["entities"]):
                srec = state["entities"][strip]
                if root_room(state, strip) == room and (srec.get("category") in ("power_strip", "outlet") or srec.get("outlets")) \
                        and C.num(srec.get("mount_height_m"), 0.0) < 0.3 and srec.get("location") in state["rooms"]:
                    C.harm(w, "water_on_power_strip", stat="electrical_hazard", item=strip)
                    notes.append("water reached the power strip on the floor")
                    break
        if frac > 0.5:
            _stress_fish(w, tank, 0.2, "large water change")
        ctx.data["seconds"] = 300.0 + 600.0 * frac
        C.log(w, "siphoned", tank=tank, litres=round(out, 2))
        if C.realistic(ctx):
            return "Siphoned about {:.0%} of the water with the gravel vacuum{}".format(frac, "; " + "; ".join(notes) if notes else "")
        return "Siphoned {:.1f} l ({:.0%}) with the gravel vacuum{}".format(out, frac, "; " + "; ".join(notes) if notes else "")
    if step == "condition":
        cond = ctx.args.get("conditioner")
        require(cond and ctx.holding(cond), "Hold the water conditioner", "precondition")
        crec = w.ent(cond)
        require(crec.get("dose_ml_per_10l"), "That is not a water conditioner", "precondition")
        dose = C.num(ctx.args.get("dose_ml"), 0.0)
        require(dose > 0, "Give dose_ml", "argument")
        require(C.num(crec.get("remaining_ml")) >= dose, "Not enough conditioner left", "resource")
        w.mut(cond)["remaining_ml"] = round(C.num(crec.get("remaining_ml")) - dose, 2)
        treats_l = dose / C.num(crec.get("dose_ml_per_10l"), 0.67) * 10.0
        bucket = ctx.args.get("bucket")
        ctx.data["seconds"] = 30.0
        if bucket:
            brec = ctx.obj(bucket, touch=True)
            bm = w.mut(bucket)
            bm["conditioned_l"] = round(C.num(brec.get("conditioned_l")) + treats_l, 2)
            return "Dosed {:.1f} ml of conditioner into the bucket (treats {:.0f} l)".format(dose, treats_l)
        m = w.mut(tank)
        m["conditioner_credit_l"] = round(C.num(rec.get("conditioner_credit_l")) + treats_l, 2)
        if C.num(water.get("chlorine_ppm")) > 0:
            fraction = min(1.0, treats_l / max(1.0, vol))
            water["chlorine_ppm"] = round(C.num(water.get("chlorine_ppm")) * (1.0 - fraction), 4)
            m["water"] = water
        return "Dosed {:.1f} ml of conditioner into the tank (treats {:.0f} l of new water)".format(dose, treats_l)
    if step == "refill":
        require(not rec.get("openable") or rec.get("open"), "Open the tank lid first", "precondition")
        cap = C.num(rec.get("volume_l"), 75.0)
        bucket = ctx.args.get("bucket")
        source = ctx.args.get("source")
        if bucket:
            brec = ctx.obj(bucket, touch=True)
            require(C.num(brec.get("liquid_l")) > 0.1 and brec.get("liquid") in ("water", "tank_water", None), "The bucket holds no clean water", "precondition")
            add = C.num(brec.get("liquid_l"))
            new_t = C.num(brec.get("liquid_temp_c"), C.num(brec.get("temperature_c"), 18.0))
            conditioned = min(add, C.num(brec.get("conditioned_l")))
            tap = brec.get("liquid") != "tank_water"
            bm = w.mut(bucket)
            bm["liquid_l"] = 0.0
            bm["conditioned_l"] = 0.0
            if "_last_l" in brec:
                bm["_last_l"] = 0.0
        else:
            require(source, "Refill from a bucket= or a faucet source=", "argument")
            srec = ctx.obj(source, touch=True)
            require(srec.get("water_source") and physics.water_available(w, source), "That faucet has no water", "resource")
            add = max(0.0, cap - vol)
            if ctx.args.get("fraction") is not None and C.num(ctx.args.get("fraction")) < 0.9:
                add = min(add, C.num(ctx.args.get("fraction")) * cap)
            new_t = C.num(ctx.args.get("temp_c"), 15.0)
            conditioned = 0.0
            tap = True
            util = w.sec("utilities")
            util["water_l"] = round(C.num(util.get("water_l"), 1e5) - add, 2)
        require(add > 0.01, "The tank is already full", "precondition")
        credit = C.num(rec.get("conditioner_credit_l"))
        treated = min(add, conditioned + credit)
        untreated = add - treated if tap else 0.0
        total = vol + add
        spill = max(0.0, total - cap)
        total = min(total, cap)
        old_t = C.num(water.get("temp_c"), 25.0)
        mix_t = (vol * old_t + add * new_t) / max(1.0, vol + add)
        water["temp_c"] = round(mix_t, 2)
        for key in ("ammonia_ppm", "nitrite_ppm"):
            water[key] = round(C.num(water.get(key)) * vol / max(1.0, vol + add), 4)
        water["chlorine_ppm"] = round((C.num(water.get("chlorine_ppm")) * vol + untreated * TAP_CHLORINE_PPM) / max(1.0, vol + add), 4)
        m = w.mut(tank)
        m["water"] = water
        m["water_l"] = round(total, 3)
        m["conditioner_credit_l"] = round(max(0.0, credit - max(0.0, add - conditioned)), 2)
        notes = []
        realistic = C.realistic(ctx)
        if abs(mix_t - old_t) > 2.0 or abs(new_t - old_t) > 4.0 and add / max(1.0, vol + add) > 0.15:
            _stress_fish(w, tank, 0.15 + 0.05 * abs(new_t - old_t), "temperature shock")
            C.harm(w, "temperature_shock", tank=tank, delta_c=round(new_t - old_t, 1))
            notes.append("the fish dart about and hide after the new water went in" if realistic else
                         "new water at {:.0f} C vs tank {:.0f} C: temperature shock".format(new_t, old_t))
        if untreated > 0.5:
            C.harm(w, "untreated_tap_water", tank=tank, litres=round(untreated, 1))
            if not realistic:
                notes.append("{:.0f} l of untreated tap water (chlorine)".format(untreated))
            elif not conditioned and not credit:
                # realistic: the robot knows it added no conditioner; how far an earlier dose stretched is not announced
                notes.append("the new tap water was not treated with conditioner")
        if spill > 0.01:
            from . import water as W
            room, cells = C.cell_of_entity(state, tank)
            W.wet_floor(w, room, [cells] if cells else [], spill)
            C.harm(w, "aquarium_overflow", stat="spills", tank=tank, litres=round(spill, 2))
            notes.append("overfilled: water spilled" if realistic else "overfilled: {:.1f} l spilled".format(spill))
        ctx.data["seconds"] = 300.0
        C.log(w, "refilled", tank=tank, litres=round(add, 2), temp_c=round(new_t, 1), untreated=round(untreated, 1))
        if realistic:
            # the water temperature is known only from a thermometer (or the faucet setting the robot chose); topping up
            # from the tap "until full" gives the volume only as the robot judges it (one stable estimate, 5 l steps),
            # not the exact deficit (which would measure the hidden water volume)
            told = " ({:.0f} C from the tap)".format(new_t) if not bucket else ""
            if bucket:
                shown = ctx.percept_value(bucket, "liquid_l", add, "inspect")      # the bucketful as the robot saw it
                amount = shown if isinstance(shown, (int, float)) else add
            else:
                amount = C.estimate(ctx, tank, "refill_l", add, absolute=1.5, rel=0.05, step=5.0, lo=0.0)
            return "Refilled about {:.0f} l{}{}".format(amount, told, "; " + "; ".join(notes) if notes else "")
        return "Refilled {:.1f} l ({:.1f} C){}".format(add, new_t, "; " + "; ".join(notes) if notes else "")
    if step == "rinse_media":
        media = ctx.args.get("media")
        require(media and ctx.holding(media), "Hold the filter media part (uninstall it first)", "precondition")
        mrec = w.ent(media)
        require(mrec.get("category") == "filter_media", "That is not filter media", "precondition")
        bucket = ctx.args.get("bucket")
        cleaner = ctx.args.get("cleaner")
        kill = 0.1
        how = "old tank water"
        if bucket:
            brec = ctx.obj(bucket, touch=True)
            require(C.num(brec.get("liquid_l")) > 0.5, "The bucket is empty", "precondition")
            if brec.get("liquid") != "tank_water":
                kill = 0.75
                how = "tap water in the bucket (chlorine)"
        else:
            source = ctx.args.get("source") or C.water_source_near(ctx)
            require(source, "Rinse it in a bucket of tank water (or at a tap)", "precondition")
            hot = C.num(ctx.args.get("temp_c"), 15.0) > 40.0
            kill = 0.95 if hot else 0.75
            how = "hot tap water" if hot else "running tap water (chlorine)"
        if cleaner:
            require(ctx.holding(cleaner), "Hold the cleaner", "precondition")
            kill = 1.0
            how += " with soap"
        pm = w.mut(media)
        pm["clog"] = 0.1
        pm["bacteria"] = round(C.num(mrec.get("bacteria")) * (1.0 - kill), 4)
        if kill > 0.5:
            C.harm(w, "colony_killed", media=media, how=how)
        ctx.data["seconds"] = 90.0
        return "Rinsed the {} in {}".format(mrec.get("label", "media"), how)
    if step == "prime":
        filt = ctx.args.get("filter") or next(iter(devices(state, tank, "filter")), None)
        require(filt, "No filter on this tank", "precondition")
        ctx.obj(filt, touch=True)
        require(vol > C.num(rec.get("volume_l"), 75.0) * 0.8, "Top the tank up first: the intake would suck air", "precondition")
        fm = w.mut(filt)
        fm["primed"] = True
        fm["_off_s"] = 0.0
        ctx.data["seconds"] = 45.0
        return "Primed the filter box with tank water"
    if step == "remove_debris":
        C.held_with(ctx, lambda r: r.get("tool_kind") in ("fish_net", "siphon"), None, "Hold a net (or the siphon)", "capability")
        require(not rec.get("openable") or rec.get("open"), "Open the tank lid first", "precondition")
        m = w.mut(tank)
        got = C.num(rec.get("uneaten_g"))
        m["uneaten_g"] = 0.0
        ctx.data["seconds"] = 60.0
        if C.realistic(ctx):
            return "Netted out the uneaten food" if got > 0.005 else "Swept the net through: nothing much to catch"
        return "Netted out {:.2f} g of uneaten food".format(got)
    raise ActionError("Unknown step", "argument")


def _stress_fish(w, tank, amount, why):
    rec = w.s["entities"][tank]
    stock = [dict(s) for s in rec.get("stock") or []]
    for group in stock:
        group["health"] = round(max(0.0, C.num(group.get("health"), 1.0) - amount), 3)
    w.mut(tank)["stock"] = stock
    C.log(w, "fish_stressed", tank=tank, why=why)

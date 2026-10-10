"""Food time systems.

food.thermal (order 52, every 30 s substep, after the kernel thermal pass):
    Owns the temperature of food entities (they carry fixed_temperature so the kernel skips
    them). Lumped Newton cooling toward the ambient found by walking up the containment chain
    (a lidded box inside a fridge sits in fridge air), with realistic time constants
    tau = m c / (h A), latent heat of freezing/thawing (ice fraction), a lagged core node
    (tau_core = L^2 / (alpha pi^2/4)), boiling cap, pathogen kill at the kind's safe core
    temperature, heat load reported to cold appliances (heat_load_w) and an air model for
    food-owned insulated containers, ice baths and stand-in fridges (food_air_model).
food.safety (order 130, kernel period 300 s, dt = the accumulated interval):
    Danger-zone time-out clock (5..57 C; doubled above 32 C ambient; unsafe after 2 h), two-stage
    cooling record, spoilage (Q10 kinetics), ripening, enzymatic browning, sogginess, wilting,
    proofing, dough skin, marinade effects, scorching, drips, then the derived truth fields.
food.refresh (order 131, first substep after every action or wait only):
    Derived truth fields (_food_status, _zone, _storage_ok, _fifo_ok, _dish, scale displays, melted
    utensils) recomputed right after an action so success predicates see the action's effect.

Cost control (deterministic, keyed only on simulated time and state):
* candidates come from w.static_index (food entities, cold spaces, scales, serving vessels ...);
  dynamic fields are re-checked on the records; runtime-made ice/water baths and handwritten labels
  are registered in the food-owned `_food_sys` section;
* thermal does a full pass on the first substep after every action and when 300 s have passed
  since the last full pass; in between it only updates items flagged `_tfast` (hot, in contact
  with a heated vessel or water, or expected to move > 1 C within 5 min). Every update integrates
  the elapsed interval exactly (closed-form exponentials), so skipping slow items is lossless;
  an item that sat in equilibrium until it was moved is only credited the last substep in its new place;
* the clocks run once per 300 s with the exact accumulated dt (rates integrated over that interval).
"""
import math

from ...kernel.plugins import hook, system
from ...kernel.predicates import root_room
from . import data as D
from . import model as M
from . import mixing as X

FULL_PASS_S = 300.0
SAFETY_PERIOD_S = 300.0
FAST_MOVE_C = 1.0        # an item expected to change more than this within one full-pass interval is updated every substep
HOT_C = 40.0             # cooking, hot holding and cooling from hot are tracked every substep
SYS = M.SYS              # food-owned bookkeeping section (thermal_full_t; air: runtime ice/water baths; labeled)


def _boundary(now, dt):
    return math.floor(now / FULL_PASS_S) != math.floor((now - dt) / FULL_PASS_S)


def thermal_ids(w):
    """Entities whose temperature food owns (static candidates; re-check `food`/`food_thermal` on the record)."""
    return w.static_index("food.thermal", lambda r: isinstance(r.get("food"), dict) or bool(r.get("latent_pack")))


def food_ids(w):
    """Every food entity (static candidates filtered on the live record)."""
    ents = w.s["entities"]
    return [e for e in w.static_index("food.foods", lambda r: isinstance(r.get("food"), dict))
            if e in ents and M.food(ents[e]) is not None]


def thermal_items(state):
    """Full-scan variant for callers without a World."""
    out = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if (M.food(rec) is not None and rec.get("food_thermal", True)) or rec.get("latent_pack"):
            out.append(eid)
    return out


def _vessel_area(rec):
    size = rec.get("size_m") or [0.2, 0.2, 0.1]
    a, b, c = [max(0.01, float(v)) for v in size[:3]]
    return 2 * (a * b + a * c + b * c)


@system("food.thermal", order=52, max_dt=30.0)
def food_thermal(w, dt, tick):
    state = w.s
    entities = state["entities"]
    now = state["time_s"]
    last_full = (state.get(SYS) or {}).get("thermal_full_t")
    full = tick.index == 0 or last_full is None or now - float(last_full) >= FULL_PASS_S - 1e-6
    if full and last_full != now:
        w.sec(SYS)["thermal_full_t"] = now
    heat_into = {}
    vessels = {}
    touched = set()
    for eid in thermal_ids(w):
        rec = entities.get(eid)
        if rec is None:
            continue
        f = M.food(rec)
        if not ((f is not None and rec.get("food_thermal", True)) or rec.get("latent_pack")):
            continue
        tt = rec.get("_tt")
        if not full and tt is not None and not rec.get("_tfast"):
            continue
        f = f or {}
        ice = float((f.get("ice") if f else rec.get("ice_frac", 0.0)) or 0.0)
        t = float(rec.get("temperature_c", 21.0))
        core0 = float(rec.get("_core_c", t)) if f else t
        elapsed = dt if tt is None else max(0.0, min(now - float(tt), 3600.0))
        if elapsed <= 0:
            continue
        grams = M.qty(rec) if f else float(rec.get("pack_mass_g", float(rec.get("mass_kg", 0.4)) * 1000))
        if grams <= 0.01:
            continue
        amb, h, encl, sink = M.ambient(state, eid)
        settled = abs(core0 - t) < 0.15 and (ice < 1e-3 or ice > 0.999) and max(t, core0) < 60.0   # hot food keeps cooking
        prev_amb = rec.get("_amb")
        if settled and prev_amb is not None and abs(t - float(prev_amb)) < 0.15:
            if abs(amb - float(prev_amb)) < 0.15:
                # still in equilibrium with an unchanged ambient: nothing to integrate; refresh the timestamp
                # once per full-pass interval so a later move is never credited with a long stale interval
                if tt is not None and now - float(tt) < FULL_PASS_S - 1e-6:
                    continue
                m = w.mut(eid)
                m["_tt"] = now
                if m.get("_tfast"):
                    m["_tfast"] = False
                continue
            # it sat in equilibrium with its old surroundings until it was moved: only the last substep counts
            elapsed = min(elapsed, dt)
        step = elapsed
        m_kg = grams / 1000.0
        info = M.catinfo(rec) if f else {"cp": 3500, "water": 0.95, "tf": -1.0}
        cp = float(info.get("cp", 3500))
        water = float(info.get("water", 0.7))
        tf = float(info.get("tf", -1.0))
        area = M.exposed_area_m2(rec)
        parent = entities.get(rec.get("location"), {})
        if parent.get("container") and parent.get("internal_temp_c") is None and parent.get("capacity_l"):
            if f.get("phase") in ("liquid", "paste", "granular", "pieces", "powder", "dough"):
                area = min(area, 0.8 * _vessel_area(parent))
            vessels[rec["location"]] = vessels.get(rec["location"], 0.0) + m_kg * cp
        c_eff = cp * (1.0 - ice) + D.CP_FROZEN * ice
        tau = max(5.0, m_kg * c_eff / (h * area * max(0.05, encl)))
        t_new = amb + (t - amb) * math.exp(-step / tau)
        released = m_kg * c_eff * (t - t_new)       # heat given to the surroundings (J)
        if water > 0.05 and tf > -30:
            latent = D.LATENT_J_PER_KG_WATER * m_kg * water
            if ice > 0 and t_new > tf:
                energy = m_kg * c_eff * (t_new - tf)
                melt = min(ice, max(0.0, energy) / latent)
                ice -= melt
                t_new = tf if ice > 1e-6 else tf + max(0.0, energy - melt * latent) / (m_kg * cp)
            elif t_new < tf - 0.3 and ice < 1.0 and amb < tf:
                energy = m_kg * c_eff * (min(t, tf - 0.3) - t_new)
                freeze = min(1.0 - ice, max(0.0, energy) / latent)
                ice += freeze
                t_new = (tf - 0.3) if ice < 1.0 - 1e-6 else (tf - 0.3) - max(0.0, energy - freeze * latent) / (m_kg * D.CP_FROZEN)
        if f and f.get("phase") == "liquid" and t_new > 100.0:
            t_new = 100.0
        if sink is not None:
            heat_into[sink] = heat_into.get(sink, 0.0) + released
        m = w.mut(eid)
        m["_tt"] = now
        if abs(float(m.get("_amb", 1e9)) - amb) > 0.05:
            m["_amb"] = round(amb, 2)
        if abs(t_new - t) > 0.002:
            m["temperature_c"] = round(t_new, 3)
        core_new = t_new
        tau_core = 1e9
        if f:
            half = M.half_thickness_m(rec)
            shape = 9.87 if f.get("cat") == "egg" and "shell" in f.get("parts", []) else 2.47   # sphere vs slab (pi^2 vs pi^2/4)
            tau_core = max(20.0, half * half / (D.THERMAL_DIFFUSIVITY * shape))
            core_new = t_new + (core0 - t_new) * math.exp(-step / tau_core)
            if ice > 0.05:
                core_new = min(core_new, tf)
            if abs(core_new - core0) > 0.002:
                m["_core_c"] = round(core_new, 3)
            if abs(ice - float(f.get("ice", 0.0) or 0.0)) > 1e-5:
                m["food"]["ice"] = round(max(0.0, min(1.0, ice)), 5)
            if t_new > float(rec.get("_peak_c", -99)) + 0.01:
                m["_peak_c"] = round(t_new, 2)
            if core_new > float(rec.get("_peak_core_c", -99)) + 0.01:
                m["_peak_core_c"] = round(core_new, 2)
            _cooking_effects(w, eid, core_new, now)
            if max(core_new, t_new) >= 60.0:
                _doneness(w, eid, t_new, core_new, step)
            rec = entities[eid]
            if rec.get("relation") == "in" and rec.get("location") in entities:
                touched.add(rec["location"])
        elif abs(ice - float(rec.get("ice_frac", 0.0))) > 1e-5:
            m["ice_frac"] = round(max(0.0, min(1.0, ice)), 5)
        move = abs(t_new - amb) * (1.0 - math.exp(-FULL_PASS_S / tau))
        core_move = abs(core_new - t_new) * (1.0 - math.exp(-FULL_PASS_S / tau_core))
        # food-owned air spaces (lunch box, cooler, ice bath, stand-in fridge) hold little air: their contents
        # exchange heat with that air every substep so the energy balance (latent ice packs) stays continuous
        fast = (h >= D.H_CONTACT or amb >= HOT_C or t_new >= HOT_C or core_new >= HOT_C
                or move > FAST_MOVE_C or core_move > FAST_MOVE_C
                or (sink is not None and bool(entities.get(sink, {}).get("food_air_model"))))
        if bool(entities[eid].get("_tfast")) != fast:
            w.mut(eid)["_tfast"] = fast
    if full:
        _vessel_taus(w, vessels)
        _passive_vessels(w, None)
    elif touched:
        _passive_vessels(w, touched)
    _cold_spaces(w, dt, heat_into)


def _passive_vessels(w, only=None):
    """A metal bowl/pot resting on a counter shares its contents' temperature (it is not a room-temperature reservoir).
    only: the vessels whose contents changed this substep (None = every vessel holding food)."""
    state = w.s
    entities = state["entities"]
    groups = {}
    for eid in food_ids(w):
        rec = entities[eid]
        if rec.get("relation") != "in":
            continue
        loc = rec.get("location")
        if loc not in entities or (only is not None and loc not in only):
            continue
        groups.setdefault(loc, []).append(eid)
    for vid, foods in sorted(groups.items()):
        if not M.passive_vessel(state, vid):
            continue
        mass = sum(M.qty(entities[f]) for f in foods)
        if mass <= 0:
            continue
        t = sum(float(entities[f].get("temperature_c", 21.0)) * M.qty(entities[f]) for f in foods) / mass
        if abs(float(entities[vid].get("temperature_c", 21.0)) - t) > 0.05:
            w.mut(vid)["temperature_c"] = round(t, 3)


def _vessel_taus(w, vessels):
    entities = w.s["entities"]
    for vid, heat_cap in sorted(vessels.items()):
        vrec = entities.get(vid)
        if vrec is None or vrec.get("internal_temp_c") is not None:
            continue
        own = float(vrec.get("mass_kg", 0.5)) * float(vrec.get("specific_heat", 900.0))
        tau = (own + heat_cap) / (D.H_STILL_AIR * _vessel_area(vrec))
        if vrec.get("openable") and not vrec.get("open"):
            tau /= 0.8
        tau = round(max(60.0, tau), 1)
        if abs(float(vrec.get("thermal_tau_s", 0)) - tau) > max(1.0, 0.02 * tau):
            w.mut(vid)["thermal_tau_s"] = tau


def _cooking_effects(w, eid, core, now):
    state = w.s
    rec = state["entities"][eid]
    f = rec["food"]
    safe = D.SAFE_CORE_C.get(f.get("cat"))
    tags = M.stored_contam(state, eid)
    if core >= 71.0 and (tags.get("raw_meat") or tags.get("bacteria")):
        M.kill_pathogens(w, eid)
    if safe and core >= safe and M.catinfo(rec).get("raw_animal") and not f.get("cooked"):
        m = w.mut(eid)
        m["food"]["cooked"] = True
        m["food"]["raw"] = False
        m["food"]["rte"] = True
        m["food"].setdefault("made_on", M.iso(M.today(state)))
    if f.get("has_raw_animal") and core >= 74.0:
        m = w.mut(eid)
        m["food"]["has_raw_animal"] = False
        m["food"]["raw"] = False
        if f.get("cat") in ("mixture", "prepared"):
            m["food"]["rte"] = True
    if core >= 60.0 and not rec.get("_hot_since"):
        m = w.mut(eid)
        m["_hot_since"] = now
        m["_heat_off_s"] = None
        m["_cooling"] = None
        if f.get("cat") in ("prepared", "mixture") or f.get("cooked"):
            m["food"].setdefault("made_on", M.iso(M.today(state)))
    if core < 60.0 and rec.get("_hot_since") is not None and rec.get("_heat_off_s") is None:
        m = w.mut(eid)
        m["_heat_off_s"] = now
        m["_hot_since"] = None
        m["_cooling"] = {"start_s": now, "t21_s": None, "t5_s": None, "ok": True}


def _egg_like(f):
    if f.get("cat") == "egg" and f.get("kind") not in ("hard_boiled_egg",):
        return True
    comp = f.get("composition") or {}
    total = sum(float(v) for v in comp.values()) or 1.0
    eggs = sum(float(comp.get(k, 0.0)) for k in ("egg_white", "egg_yolk", "raw_egg", "beaten_egg", "egg"))
    return eggs / total >= 0.5


def _grain_load(f, vessel=None):
    """(grain kind, grain g, liquid g) for a food holding a cookable grain, else None.
    Pasta cooks in the pot's own (excess) water, which is counted from the vessel."""
    comp = f.get("composition") or {}
    for kind in sorted(D.GRAIN_COOK):
        if comp.get(kind, 0.0) > 0 or (f.get("kind") == kind and not comp):
            grain = float(comp.get(kind, f.get("qty_g", 0.0) if f.get("kind") == kind else 0.0))
            water = sum(float(comp.get(k, 0.0)) for k in M.LIQUID_KINDS)
            if vessel is not None and vessel.get("liquid") in ("water", None):
                water += 1000.0 * float(vessel.get("liquid_l", 0.0) or 0.0)
            return kind, grain, water
    return None


def _doneness(w, eid, surf, core, step):
    """What heat does to food the appliance domains heat: egg proteins set (white ~62 C, yolk ~65-70 C), bread surfaces
    toast (Maillard above ~120 C) and burn/smoke, grains absorb their cooking liquid (>= 85 C) and go mushy when overcooked."""
    state = w.s
    rec = state["entities"][eid]
    f = rec["food"]
    if rec.get("package") and rec.get("opened") is not True and f.get("cat") != "egg":
        return
    ch = {}
    # ---- eggs: white sets at the surface temperature, yolk at the core (beaten eggs set as one)
    if _egg_like(f) and float(f.get("yolk_set", 0.0)) < 4.0:
        beaten = f.get("kind") in ("beaten_egg", "scrambled_eggs") or (f.get("mix") or {}).get("h", 0.0) >= 0.6 or f.get("cat") == "mixture"
        white = float(f.get("white_set", 0.0)) + step * max(0.0, min(surf, 100.0) - D.EGG_WHITE_SET_C) / D.EGG_WHITE_RATE
        yolk_t = surf if beaten else core
        yolk = float(f.get("yolk_set", 0.0)) + step * max(0.0, min(yolk_t, 100.0) - D.EGG_YOLK_SET_C) / D.EGG_YOLK_RATE
        if beaten:
            yolk = white = max(white, yolk)
        white, yolk = round(min(4.0, white), 4), round(min(4.0, yolk), 4)
        if white != f.get("white_set") or yolk != f.get("yolk_set"):
            ch["white_set"], ch["yolk_set"] = white, yolk
            ch["egg_doneness"] = ("liquid" if white < 0.3 else "white_unset" if white < 1.0 else
                                  "runny_yolk" if yolk < 0.35 else "jammy_yolk" if yolk < 1.0 else
                                  "set" if yolk < D.EGG_OVERCOOKED else "overcooked_rubbery")
            if beaten and white >= 0.3:
                ch["egg_doneness"] = "wet" if white < 1.0 else "set" if white < D.EGG_OVERCOOKED else "overcooked_rubbery"
            if white >= 1.0 and f.get("phase") == "liquid":
                ch["phase"] = "solid"
                ch["form"] = "scrambled" if beaten else "fried" if f.get("cut") == "cracked" else f.get("form")
            if white >= 1.0 and yolk >= 1.0:
                ch.update(cooked=True, raw=False, rte=True)
            elif white >= 1.0:
                ch["raw"] = False      # runny yolk: edible for most, not for the vulnerable (eater check)
    # ---- bread surfaces toast, then burn and smoke
    if f.get("cat") == "bread" and surf >= D.TOAST_START_C:
        level = round(float(f.get("toast", 0.0)) + step * (min(surf, 320.0) - D.TOAST_START_C) / D.TOAST_RATE, 4)
        ch["toast"] = level
        ch["toast_level"] = ("warm" if level < 0.3 else "lightly toasted" if level < 0.8 else "golden" if level < 1.4
                             else "dark" if level < 2.0 else "burnt")
        if level >= 2.0:
            ch["burnt"] = round(min(1.0, level - 1.5), 3)
            if not rec.get("_smoking"):
                w.mut(eid)["_smoking"] = True
                w.emit("smoke", source=eid, reason="burnt bread")      # never a label that could name a look-alike
                w.sec("food_log").setdefault("smoke", []).append({"t": state["time_s"], "food": eid})
                w.stat("food_burnt")
    # ---- grains absorb their liquid
    vessel = state["entities"].get(rec.get("location")) if rec.get("relation") == "in" else None
    load = _grain_load(f, vessel)
    if load and surf >= D.GRAIN_SIMMER_C:
        kind, grain, water = load
        ratio, cook_s = D.GRAIN_COOK[kind]
        if kind != "pasta" and vessel is not None and float(vessel.get("liquid_l", 0.0) or 0.0) > 0:
            X.absorb_vessel_water(w, rec["location"], eid)      # oats/rice take up the pot's water
            rec = state["entities"][eid]
            f = rec["food"]
        have = water / grain if grain > 0 else 0.0
        if have < 0.3 * ratio:
            ch["grain_texture"] = "dry, toasting"
            ch["thick"] = True
        else:
            done = round(float(f.get("grain_cooked", 0.0)) + step / cook_s * min(1.0, have / ratio), 4)
            ch["grain_cooked"] = min(2.5, done)
            if kind != "pasta":
                ch["phase"] = "liquid" if done < 0.5 and have > 0.75 * ratio else "paste"
                ch["grain_texture"] = ("raw, chalky" if done < 0.6 else "chewy, undercooked" if done < 0.9 else
                                       "tender" if done < 1.35 else "mushy, overcooked")
                if have < 0.75 * ratio and done >= 0.5:
                    ch["grain_texture"] += "; dry, sticking"
                elif have > 1.5 * ratio and done >= 0.9:
                    ch["grain_texture"] += "; soupy, too much liquid"
                if done >= 0.5:
                    ch["thick"] = True
            else:
                ch["grain_texture"] = ("hard" if done < 0.8 else "al dente" if done < 1.1 else "soft" if done < 1.4 else "mushy")
            if done >= 0.9:
                ch.update(rte=True, cooked=True, raw=False)
                dish = D.GRAIN_DISH.get(kind)
                if dish and f.get("kind") != dish:
                    ch.update(kind=dish, cat="prepared")
                    w.mut(eid)["label"] = dish.replace("_", " ")
    if ch:
        fnew = dict(f)
        fnew.update(ch)
        if fnew != f:
            m = w.mut(eid)
            m["food"] = fnew
            if m.get("appearance") is not None and not (m.get("package") and not m.get("open")):
                m["appearance"] = M.appearance(m)


def _cold_ids(w):
    """Cold/hot units that may hold food: anything with an internal temperature or a food air model (static), plus
    bowls turned into ice/water baths at runtime (registered)."""
    static = w.static_index("food.cold", lambda r: "internal_temp_c" in r or bool(r.get("food_air_model")))
    extra = M.registered(w.s, "air")
    return sorted(set(static) | set(extra)) if extra else static


def _cold_spaces(w, dt, heat_into):
    """Air model of food-owned insulated containers, ice baths and stand-in fridges;
    heat load reported to appliance-owned cold units."""
    state = w.s
    entities = state["entities"]
    from ...kernel.physics import room_temperature
    for eid in _cold_ids(w):
        rec = entities.get(eid)
        if rec is None or rec.get("internal_temp_c") is None:
            continue
        q_food = heat_into.get(eid, 0.0)
        if not rec.get("food_air_model"):
            load = round(q_food / dt, 2) if dt > 0 else 0.0
            if abs(float(rec.get("heat_load_w", 0.0) or 0.0) - load) > 0.5:
                w.mut(eid)["heat_load_w"] = load
            continue
        t_in = float(rec["internal_temp_c"])
        rid = root_room(state, eid)
        t_out = room_temperature(state, rid) if rid in state["rooms"] else 21.0
        outer = entities.get(rec.get("location"))
        if outer is not None and outer.get("internal_temp_c") is not None:
            t_out = float(outer["internal_temp_c"])
        if rec.get("ice_bath"):
            # contents that the kernel cools (pots, bottles) dump heat into the ice water too
            for child in w.children(eid):
                crec = entities[child]
                if M.food(crec) is None and not crec.get("latent_pack"):
                    q_food += 15.0 * max(0.0, float(crec.get("temperature_c", 21.0)) - t_in) * dt
            q = float(rec.get("ua_w_k", 1.5)) * (t_out - t_in) * dt + q_food
            ice_kg = float(rec.get("ice_kg", 0.0))
            if q > 0:
                melt = min(ice_kg, q / D.LATENT_J_PER_KG_WATER)
                m = w.mut(eid)
                m["ice_kg"] = round(ice_kg - melt, 4)
                if m["ice_kg"] <= 1e-4:
                    m["ice_bath"] = False
                    m["internal_temp_c"] = None
                    m.pop("internal_temp_c", None)
                    m["temperature_c"] = 2.0
                    m["heat_coupling"] = 1.0
            continue
        ua = float(rec.get("ua_w_k", 0.8))
        if rec.get("openable") and rec.get("open"):
            ua += float(rec.get("open_ua_w_k", 12.0))
        cap = float(rec.get("air_heat_capacity_j_k", 15000.0))
        t_new = t_in + (ua * (t_out - t_in) * dt + q_food) / cap
        setpoint = rec.get("setpoint_c")
        if setpoint is not None and rec.get("cooling_w") and t_new > float(setpoint):
            removed = min(float(rec["cooling_w"]) * dt, (t_new - float(setpoint)) * cap)
            t_new -= removed / cap
        if abs(t_new - t_in) > 0.002:
            w.mut(eid)["internal_temp_c"] = round(t_new, 3)


# ----------------------------------------------------------------------

def _wet_neighbor(state, eid, index):
    rec = state["entities"][eid]
    neighbors = []
    parent = state["entities"].get(rec.get("location"))
    if parent is not None and M.food(parent) and rec.get("relation") == "on":
        neighbors.append(rec["location"])
    for child in index.get(eid, []):
        crec = state["entities"][child]
        if crec.get("relation") == "on" and M.food(crec):
            neighbors.append(child)
    for n in neighbors:
        nf = M.food(state["entities"][n]) or {}
        if nf.get("moist") or nf.get("phase") == "liquid" or nf.get("wet", 0) > 0.3:
            return n
    return None


def _barrier(rec):
    for layer in (M.food(rec) or {}).get("layers", []):
        if layer.get("barrier") and layer.get("coverage", 0) >= 0.8:
            return True
    return False


@system("food.safety", order=130, max_dt=SAFETY_PERIOD_S, period=SAFETY_PERIOD_S)
def food_safety(w, dt, tick):
    """Slow clocks once per 300 s of simulated time; dt is the exact interval since the last run."""
    if dt > 0:
        index = _ChildIndex(w)
        foods = food_ids(w)
        _clocks(w, dt, w.s["time_s"], foods, index)
        _drips(w, dt, foods)
    _utensil_melting(w)
    _scales(w)
    _derived(w)


@hook("episode_start")
def _food_episode_start(w, env):
    """Derived truth exists from the first observation on (and in the evaluator's baseline)."""
    _scales(w)
    _derived(w)


@system("food.refresh", order=131, max_dt=30.0)
def food_refresh(w, dt, tick):
    """Derived truth right after an action (first substep of every step), so predicates see its effect."""
    if tick.index != 0:
        return
    _utensil_melting(w)
    _scales(w)
    _derived(w)


class _ChildIndex(object):
    """parent -> sorted children, built lazily once per pass (one scan of the entity table)."""

    def __init__(self, w):
        self.w = w
        self._map = None

    def _build(self):
        index = {}
        for eid, rec in self.w.s["entities"].items():
            index.setdefault(rec.get("location"), []).append(eid)
        for kids in index.values():
            kids.sort()
        self._map = index

    def get(self, parent, default=None):
        if self._map is None:
            self._build()
        return self._map.get(parent, default)

    def __contains__(self, parent):
        if self._map is None:
            self._build()
        return parent in self._map


def _clocks(w, dt, now, foods=None, index=None):
    state = w.s
    entities = state["entities"]
    index = index if index is not None else _ChildIndex(w)
    for eid in (foods if foods is not None else food_ids(w)):
        rec = entities.get(eid)
        f = M.food(rec) if rec is not None else None
        if f is None:
            continue
        info = M.catinfo(rec)
        t = float(rec.get("temperature_c", 21.0))
        core = float(rec.get("_core_c", t))
        changes = {}
        fchanges = {}
        zinfo = []

        def zone():
            if not zinfo:
                zinfo.append(M.zone_of(state, eid))
            return zinfo[0]
        # --- danger-zone time-out (TCS foods)
        if rec.get("perishable") and D.DANGER_LOW_C < t < D.DANGER_HIGH_C and float(f.get("ice", 0.0)) < 0.5:
            amb = float(rec.get("_amb", M.ambient(state, eid)[0]))
            factor = 2.0 if amb > D.HOT_AMBIENT_C else 1.0
            changes["_time_out_s"] = round(float(rec.get("_time_out_s", 0.0)) + dt, 1)
            abuse = float(rec.get("_abuse_s", 0.0)) + dt * factor
            changes["_abuse_s"] = round(abuse, 1)
            if abuse > D.TIME_OUT_LIMIT_S and not rec.get("_abused"):
                changes["_abused"] = True
        # --- two-stage cooling of cooked food
        cooling = rec.get("_cooling")
        if isinstance(cooling, dict) and cooling.get("ok"):
            cooling = dict(cooling)
            elapsed = now - float(cooling["start_s"])
            if cooling.get("t21_s") is None and core <= 21.0:
                cooling["t21_s"] = now
            if cooling.get("t5_s") is None and core <= 5.0:
                cooling["t5_s"] = now
            late1 = cooling.get("t21_s") is None and elapsed > D.COOL_STAGE1_S or \
                (cooling.get("t21_s") is not None and float(cooling["t21_s"]) - float(cooling["start_s"]) > D.COOL_STAGE1_S)
            late2 = cooling.get("t5_s") is None and elapsed > D.COOL_STAGE2_S
            if (late1 or late2) and rec.get("perishable", True):
                cooling["ok"] = False
                changes["_unsafe"] = True
                changes["_unsafe_reason"] = "slow_cooling"
            if cooling != rec.get("_cooling"):
                changes["_cooling"] = cooling
        # --- spoilage kinetics (Q10)
        life = float(rec.get("life_days") or info.get("life", 7))
        t_ref = float(info.get("t_ref", 4))
        q10 = float(info.get("q10", 2.5))
        ice = float(f.get("ice", 0.0))
        if ice < 0.8 and t > -5:
            rate = (1.0 / (life * 86400.0)) * q10 ** ((max(-2.0, min(45.0, t)) - t_ref) / 10.0)
            if rec.get("package") and rec.get("opened") and rec.get("opened_life_days"):
                rate *= max(1.0, life / float(rec["opened_life_days"]))
            elif not rec.get("package") and f.get("cut", "whole") != "whole":
                rate *= 1.8
            if float(f.get("wet", 0.0)) > 0.3 and f.get("cat") in ("berry", "leafy_green", "herb", "fruit"):
                rate *= 3.0
            if f.get("ethylene") == "sensitive" and f.get("ethylene_boost"):
                rate *= 1.5
            spoil = float(rec.get("_spoil", 0.0)) + rate * dt
            if abs(spoil - float(rec.get("_spoil", 0.0))) > 1e-8:
                changes["_spoil"] = round(spoil, 7)
        elif ice >= 0.8 and not (rec.get("package") and not rec.get("opened")) and not zone()["covered"]:
            fchanges["freezer_burn"] = round(min(1.0, float(f.get("freezer_burn", 0.0)) + dt / (90 * 86400.0)), 6)
        # --- ripening and chill injury
        if f.get("ripens") and ice < 0.5:
            r = float(f.get("ripeness", 0.5))
            factor = 0.25 if t < 10 else 1.0
            if f.get("ethylene_boost"):
                factor *= 1.6
            r += factor * float(f.get("ripen_per_day", 0.25)) * dt / 86400.0
            if abs(r - float(f.get("ripeness", 0.5))) > 1e-6:
                fchanges["ripeness"] = round(min(1.5, r), 6)
            if f.get("chill_sensitive") and t < 10:
                fchanges["chill_injury"] = round(min(1.0, float(f.get("chill_injury", 0.0)) + dt / (3 * 86400.0)), 6)
        # --- enzymatic browning of cut fruit
        if f.get("browns") and f.get("cut", "whole") != "whole" and float(f.get("browning", 0.0)) < 1.0:
            factor = 0.08 if f.get("acid_treated") else 1.0
            factor *= 0.3 if t < 8 else 1.0
            fchanges["browning"] = round(min(1.0, float(f.get("browning", 0.0)) + factor * dt / 2700.0), 6)
        # --- sogginess / wilting
        if f.get("soggy_prone"):
            wet = _wet_neighbor(state, eid, index)
            in_liquid = rec.get("relation") == "in" and any(M.is_liquid(entities[c]) for c in index.get(rec.get("location"), []) if c != eid)
            if (wet and not _barrier(rec)) or in_liquid:
                rate = 1.0 / 240.0 if in_liquid and f.get("cat") == "cereal" else 1.0 / 5400.0
                fchanges["soggy"] = round(min(1.0, float(f.get("soggy", 0.0)) + rate * dt), 6)
        if f.get("dressed") and f.get("cat") == "leafy_green":
            fchanges["wilt"] = round(min(1.0, float(f.get("wilt", 0.0)) + dt / 2400.0), 6)
        # --- melting of frozen desserts (quality, irreversible ice-crystal damage)
        if f.get("cat") == "frozen_dessert":
            melted = max(float(f.get("melted", 0.0)), 1.0 - ice if t > -6 else 0.0)
            if melted > float(f.get("melted", 0.0)) + 1e-4:
                fchanges["melted"] = round(melted, 4)
            if t > -6 and not f.get("softened"):
                fchanges["softened"] = True
        # --- proofing yeast dough, skins on uncovered dough
        if f.get("phase") == "dough" and (f.get("composition") or {}).get("yeast"):
            rate = 1.0 / 5400.0 * 2.0 ** ((max(0.0, min(38.0, t)) - 21.0) / 8.0)
            fchanges["proof"] = round(float(f.get("proof", 0.0)) + rate * dt, 6)
        if f.get("phase") == "dough" and not zone()["covered"]:
            fchanges["skin"] = round(min(1.0, float(f.get("skin", 0.0)) + dt / 2400.0), 6)
        # --- marinades
        mar = f.get("marinade")
        if isinstance(mar, dict):
            elapsed = float(mar.get("elapsed_s", 0.0)) + dt
            mar = dict(mar, elapsed_s=round(elapsed, 1))
            if mar.get("acid") and f.get("cat") in ("fish", "shellfish") and elapsed > 3600:
                fchanges["texture"] = "mushy"
            if mar.get("acid") and elapsed > 86400 and f.get("cat") in ("poultry", "meat"):
                fchanges["texture"] = "mealy"
            fchanges["marinade"] = mar
        # --- produce soaking in a bowl of water: grit settles out, absorbent produce waterlogs
        parent = entities.get(rec.get("location"), {})
        if rec.get("relation") == "in" and float(parent.get("liquid_l", 0.0) or 0.0) > 0.3 and parent.get("liquid") in ("water", None):
            soil = dict(rec.get("soil") or {})
            moved = 0.0
            for key in ("dirt", "sand", "mud"):
                if soil.get(key, 0.0) > 0:
                    take = soil[key] * min(1.0, dt / 300.0)
                    soil[key] = round(soil[key] - take, 4)
                    moved += take
            if rec.get("_grit_inside") and f.get("cut_axis") == "lengthwise":
                changes["_grit_inside"] = False
                moved += 0.2
            if moved:
                changes["soil"] = soil
                w.mut(rec["location"])["grit"] = round(float(parent.get("grit", 0.0)) + moved, 4)
            fchanges["wet"] = 0.8
            if f.get("absorbs_water"):
                fchanges["soak_s"] = round(float(f.get("soak_s", 0.0)) + dt, 1)
                if float(f.get("soak_s", 0.0)) + dt > 120:
                    fchanges["waterlogged"] = True
        # --- uncovered food outdoors draws insects within minutes
        if not rec.get("package") and _outdoors(state, eid) and zone()["zone"] == "outdoor" and not zone()["covered"]:
            fchanges["insects"] = round(min(1.0, float(f.get("insects", 0.0)) + dt / 900.0), 4)
            if float(f.get("insects", 0.0)) > 0.3:
                changes.setdefault("contamination", dict(rec.get("contamination") or {}))
                changes["contamination"]["bacteria"] = round(min(1.0, changes["contamination"].get("bacteria", 0.0) + 0.02), 4)
        # --- protein foam rises on a boiling stock/bean pot for the first ~20 min of the boil
        if f.get("foams") and f.get("phase") == "liquid" and t >= 95.0:
            boiled = float(f.get("boil_s", 0.0)) + dt
            fchanges["boil_s"] = round(boiled, 1)
            if boiled < 1500:
                fchanges["foam_g"] = round(min(0.02 * M.qty(rec), float(f.get("foam_g", 0.0)) + 0.00002 * M.qty(rec) * dt), 3)
        # --- rigid glass jar overfilled with liquid cracks when it freezes (water expands ~9 %)
        if f.get("phase") == "liquid" and ice > 0.5:
            jar = entities.get(rec.get("location"), {})
            if jar.get("material") == "glass" and not jar.get("broken") and float(jar.get("fill_level", 0) or 0) > 0.9:
                w.mut(rec["location"]).update(broken=True, condition="cracked by freezing")
                w.stat("breakages")
                w.stat("damage")
        # --- thawing raw-meat packages purge liquid (drip) once partly thawed
        if info.get("raw_animal") and rec.get("package") and 0.0 < ice < 0.95 and not rec.get("_leaking"):
            changes["_leaking"] = True
        # --- scorching in a hot pan without stirring
        if parent.get("conducts_heat") and float(parent.get("temperature_c", 21.0)) > 115 and f.get("thick"):
            last_stir = float(f.get("last_stirred_s", -1e9))
            if now - last_stir > 180:
                scorch = min(1.0, float(f.get("scorch", 0.0)) + dt / 900.0)
                fchanges["scorch"] = round(scorch, 5)
                if scorch > 0.3:
                    fchanges["burnt"] = round(scorch, 5)
        if fchanges:
            fnew = dict(f)
            fnew.update(fchanges)
            if fnew != f:
                changes["food"] = fnew
        if changes:
            m = w.mut(eid)
            for k, v in changes.items():
                m[k] = v


def _outdoors(state, eid):
    rid = root_room(state, eid)
    return bool(state["rooms"].get(rid, {}).get("outdoor"))


def _drips(w, dt, foods=None):
    """Uncontained raw meat (unwrapped, or a leaking thawing pack not on a tray) drips onto food stored below it."""
    state = w.s
    entities = state["entities"]
    foods = foods if foods is not None else food_ids(w)
    sources = []
    zones = {}
    for eid in foods:
        rec = entities.get(eid)
        if rec is None or M.food(rec) is None or not M.is_raw_animal(rec):
            continue
        z = zones[eid] = M.zone_of(state, eid)
        if z["zone"] in ("fridge", "freezer") and not z["contained"] and z["level"] is not None and float((M.food(rec) or {}).get("ice", 0.0)) < 0.9:
            sources.append((eid, z))
    if not sources:
        return
    for eid in foods:
        rec = entities.get(eid)
        if rec is None or M.food(rec) is None or M.is_raw_animal(rec):
            continue
        z = None
        for src, sz in sources:
            z = z or M.zone_of(state, eid)
            if z["unit"] == sz["unit"] and z["level"] is not None and z["level"] < sz["level"] - 0.05 and not z["covered"]:
                M.add_contam(w, eid, "raw_meat", min(0.5, 0.15 * dt / 3600.0))


def _utensil_melting(w):
    state = w.s
    for eid in w.static_index("food.meltable", lambda r: r.get("melt_c") is not None):
        rec = state["entities"].get(eid)
        if rec is None or rec.get("melt_c") is None or rec.get("broken"):
            continue
        parent = state["entities"].get(rec.get("location"), {})
        if parent.get("conducts_heat") and float(parent.get("temperature_c", 21.0)) >= float(rec["melt_c"]):
            m = w.mut(eid)
            m["broken"] = True
            m["condition"] = "melted"
            for food_id in M.vessel_foods(state, rec["location"]):
                M.add_contam(w, food_id, "chemical", 0.3)
            w.stat("damage")
            w.emit("utensil_melted", target=eid)


# ----------------------------------------------------------------------
# kitchen scale display

UNIT_G = {"g": 1.0, "oz": 28.3495, "lb": 453.592, "ml": 1.0}


def _subtree_g(state, eid, index):
    rec = state["entities"][eid]
    total = float(rec.get("mass_kg", 0.0)) + float(rec.get("liquid_l", 0.0)) * float(rec.get("liquid_density", 1.0))
    for child in index.get(eid, []):
        total += _subtree_g(state, child, index) / 1000.0
    return total * 1000.0


def scale_load_g(state, scale, index=None):
    index = index if index is not None else M.children_index(state)
    return sum(_subtree_g(state, c, index) for c in index.get(scale, []) if state["entities"][c].get("relation") == "on")


def scale_reading(state, scale):
    rec = state["entities"][scale]
    if not rec.get("on") or rec.get("broken") or float(rec.get("battery_level", 1.0)) <= 0.02:
        return None
    load = scale_load_g(state, scale)
    if load > float(rec.get("capacity_g", 5000.0)) + 1e-6:
        return "overload"
    unit = (rec.get("settings") or {}).get("unit", "g")
    net = load - float(rec.get("tare_g", 0.0))
    return round(net / UNIT_G.get(unit, 1.0), 1 if unit in ("g", "ml") else 2)


def scale_display(state, scale):
    value = scale_reading(state, scale)
    if value is None:
        return ""
    if value == "overload":
        return "O-Ld"
    unit = (state["entities"][scale].get("settings") or {}).get("unit", "g")
    if unit in ("g", "ml"):
        return "{:.0f} {}".format(value, unit)
    return "{:.2f} {}".format(value, unit)


def _scales(w):
    state = w.s
    for eid in w.static_index("food.scales", lambda r: r.get("tool_kind") == "kitchen_scale"):
        rec = state["entities"].get(eid)
        if rec is None or rec.get("tool_kind") != "kitchen_scale":
            continue
        display = scale_display(state, eid)
        if rec.get("display") != display:
            w.mut(eid)["display"] = display


# ----------------------------------------------------------------------
# derived truth fields

def _fifo_flags(state, container, shelf, index):
    """M.fifo_flags over the container's children only."""
    entities = state["entities"]
    items = []
    for eid in index.get(container, []):
        rec = entities[eid]
        if rec.get("relation") == "in" and rec.get("shelf") == shelf and M.food(rec) and rec.get("spos") is not None:
            exp = M.expires(rec)
            if exp is not None:
                items.append((eid, rec, exp))
    flags = {}
    for eid, rec, exp in items:
        ok = True
        for oid, orec, oexp in items:
            if oid == eid or M.product_key(orec) != M.product_key(rec):
                continue
            # the soonest-expiring unit of a product must be at least as far forward as the later ones
            if oexp < exp and orec["spos"][1] > rec["spos"][1] + 0.01:
                ok = False
            if oexp > exp and orec["spos"][1] < rec["spos"][1] - 0.01:
                ok = False
        flags[eid] = ok
    return flags


def _unit_groups(state, foods, zones):
    groups = {}
    for eid in foods:
        z = zones[eid]
        if z["zone"] != "fridge" or z["level"] is None:
            continue
        rec = state["entities"][eid]
        g = groups.setdefault(z["unit"], {"raw": [], "rte": [], "zones": {}})
        g["zones"][eid] = z
        if M.is_raw_animal(rec):
            g["raw"].append((z["level"], eid))
        elif M.is_rte(rec):
            g["rte"].append((z["level"], eid))
    return groups


def _derived(w):
    """Truth fields the evaluator reads: status, storage zone checks, FIFO order, dish summaries."""
    state = w.s
    entities = state["entities"]
    index = _ChildIndex(w)
    foods = food_ids(w)
    zones = {eid: M.zone_of(state, eid) for eid in foods}
    groups = _unit_groups(state, foods, zones)
    day = M.today(state)
    shelves = set()
    for eid in foods:
        rec = entities[eid]
        st = M.status(state, rec, day)
        z = zones[eid]
        ok, issue = M.storage_check(state, eid, groups, z, index)
        updates = {}
        if rec.get("_food_status") != st:
            updates["_food_status"] = st
        if rec.get("_zone") != z["zone"]:
            updates["_zone"] = z["zone"]
        if rec.get("_storage_ok") != ok or rec.get("_storage_issue") != issue:
            updates["_storage_ok"] = ok
            updates["_storage_issue"] = issue
        app = M.appearance(rec)
        if rec.get("package") and not rec.get("open") and not rec.get("transparent"):
            app = "sealed package" + (", bulging lid" if rec.get("_bulging") else "")
        if rec.get("appearance") != app:
            updates["appearance"] = app
        od = M.odor(rec)
        if rec.get("_food_odor") != od:
            updates["_food_odor"] = od
        comp = rec["food"].get("composition") or {}
        if comp.get("salt") or comp.get("kosher_salt") or rec.get("_salt_pct") is not None:
            total = sum(float(v) for v in comp.values()) or 1.0
            salt_pct = round(100.0 * (float(comp.get("salt", 0.0)) + float(comp.get("kosher_salt", 0.0))) / total, 3)
            liquid_pct = round(100.0 * sum(float(comp.get(k, 0.0)) for k in M.LIQUID_KINDS) / total, 2)
            if rec.get("_salt_pct") != salt_pct or rec.get("_liquid_pct") != liquid_pct:
                updates["_salt_pct"], updates["_liquid_pct"] = salt_pct, liquid_pct
        if "_info_lost" in rec or rec.get("_protective_removed"):
            pk = not rec.get("_info_lost") and not rec.get("_protective_removed")
            if rec.get("_packaging_ok") != pk:
                updates["_packaging_ok"] = pk
        if rec.get("location") in entities and rec.get("relation") == "in" and rec.get("spos") is not None:
            shelves.add((rec["location"], rec.get("shelf")))
        if rec.get("relation") == "on" and M.food(entities.get(rec.get("location"), {}) or {}):
            stack_ok = M.stack_fifo_ok(state, eid)
            if rec.get("_stack_fifo_ok") != stack_ok:
                updates["_stack_fifo_ok"] = stack_ok
        source = rec.get("derived_from")
        if source is not None and (source not in entities or M.kind(entities[source]) != M.kind(rec)):
            # a portion that became something else (a batter, a dish) no longer looks like the package it came from
            if source in entities:
                updates["derived_from"] = None
        if updates:
            m = w.mut(eid)
            m.update(updates)
            if "derived_from" in updates:
                m.pop("derived_from", None)
    for container, shelf in sorted(shelves, key=lambda t: (t[0], -1 if t[1] is None else t[1])):
        for eid, ok in sorted(_fifo_flags(state, container, shelf, index).items()):
            if entities[eid].get("_fifo_ok") != ok:
                w.mut(eid)["_fifo_ok"] = ok
    checked = w.static_index("food.checked", lambda r: bool(r.get("recipe") or r.get("food_label") or r.get("policy")
                                                              or ("internal_temp_c" in r and r.get("capacity_l"))))
    labeled = M.registered(state, "labeled")
    for eid in (sorted(set(checked) | set(labeled)) if labeled else checked):
        rec = entities.get(eid)
        if rec is None:
            continue
        updates = {}
        if rec.get("recipe") and rec.get("recipe") in entities and entities[rec["recipe"]].get("_recipe"):
            rc = M.recipe_check(state, eid, entities[rec["recipe"]]["_recipe"])
            if rec.get("_recipe_check") != rc:
                updates["_recipe_check"] = rc
        if rec.get("food_label"):
            lc = M.label_check(state, eid, index)
            if rec.get("_label_check") != lc:
                updates["_label_check"] = lc
        if rec.get("policy"):
            issues = M.policy_issues(state, eid, M.dish_foods(state, eid, index))
            if rec.get("_policy_issues") != issues:
                updates["_policy_issues"] = issues
                updates["_policy_ok"] = not issues
        if rec.get("internal_temp_c") is not None and not rec.get("insulated") and rec.get("capacity_l"):
            used = sum(float(entities[c].get("volume_l", 0.0)) for c in index.get(eid, []))
            free = round(max(0.0, float(rec["capacity_l"]) - used), 2)
            if rec.get("_free_l") != free:
                updates["_free_l"] = free
        if updates:
            w.mut(eid).update(updates)
    people = sorted(state.get("people", {}))
    for eid in w.static_index("food.serving", lambda r: bool(r.get("serving"))):
        rec = entities.get(eid)
        if rec is None or not rec.get("serving"):
            continue
        dish = M.dish_foods(state, eid, index)
        if not dish:
            if rec.get("_dish"):
                w.mut(eid)["_dish"] = None
            continue
        kinds = {}
        for fid in dish:
            k = M.kind(entities[fid])
            kinds[k] = round(kinds.get(k, 0.0) + M.qty(entities[fid]), 1)
        summary = {"foods": dish, "kinds": kinds,
                   "max_temp_c": round(max(float(entities[fid].get("temperature_c", 21.0)) for fid in dish), 1),
                   "total_g": round(sum(M.qty(entities[fid]) for fid in dish), 1),
                   "ingredients": sorted(set(k for fid in dish for k, v in ((M.food(entities[fid]) or {}).get("composition")
                                                                              or {M.kind(entities[fid]): 1.0}).items() if v)),
                   "ok_for": {}, "issues": {}, "prefs_ok": {}}
        for pid in people:
            issues = M.eater_issues(state, pid, dish)
            summary["issues"][pid] = issues
            summary["ok_for"][pid] = not issues
            summary["prefs_ok"][pid] = not M.preference_issues(state, pid, dish)
        if rec.get("_dish") != summary:
            w.mut(eid)["_dish"] = summary


def refresh(w):
    """Recompute derived truth fields now (used by action hooks so predicates see fresh values)."""
    _scales(w)
    _derived(w)

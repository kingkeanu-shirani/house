"""The pet agent model, ticked every substep (<= 30 s): needs, eating, drinking, elimination,
exploring (counter-surfing, raiding, chewing, ingestion), stress, escapes, outdoor exposure and mud,
and health progression (toxins -> signs after their onset).

Fidelity: S/G. Rates use real magnitudes (dogs gulp ~1.5 g/s of kibble, cats eat ~0.12 g/s and leave part
of a meal; water need 45-55 ml/kg/day; a dog finds unattended food on a counter it can reach within a few
minutes, appeal-scaled). Stochastic choices use tick.rng; most safety-relevant outcomes are deterministic
thresholds so that a wrong plan fails on every seed.
"""
import math

from ...kernel import geometry, physics
from ...kernel.plugins import after, before, hook, system
from ...kernel.predicates import root_room
from ..core import add_soil
from . import common as C
from . import model as M

EXPOSURE_FIND_S = 240.0     # a pet finds an item of appeal 1.0 after 4 min of continuous access
COUNTER_CLIMB_M = 0.6       # getting at something this high is counter-surfing (or a cat up on the counter)
BOX_FULL = 6                # clumps + feces at which a cat starts avoiding the box
MIN_LITTER_CM = 3.0


# ----------------------------------------------------------------------
# per-tick index of the entities the pets care about

def _appeal_item(rec):
    """Dynamic test: something a loose pet may go after or be hurt by (food, toxins, chewables, choking hazards)."""
    if rec.get("bowl_kind") or rec.get("category") == "litter_box":
        return False
    return bool(rec.get("pet_appeal") is not None or C.is_edible(rec) or C.toxin_of(rec) or rec.get("chewable")
                or rec.get("hazards") and "choking" in rec.get("hazards", []))


def _appeal_candidate(rec):
    """Static superset of _appeal_item (types, labels and fixed flags only): liquid containers whose label names a
    toxin are included because their contents may change."""
    if _appeal_item(rec):
        return True
    if rec.get("portable") and rec.get("liquid_container"):
        text = C.label_text_of(rec)
        return any(word in text for word, _t in C.TOXIN_WORDS)
    return False


def _white_noise(rec):
    return rec.get("white_noise") or "white_noise" in (rec.get("tags") or []) or rec.get("sound_mode") == "white_noise"


STATIC_SETS = {
    "noisy": lambda r: "noise_db" in r,
    "maskers": lambda r: bool(r.get("white_noise") or "white_noise" in (r.get("tags") or []) or "sound_mode" in r),
    "bowls": lambda r: bool(r.get("bowl_kind") or r.get("pet_item")),
    "water": lambda r: bool(r.get("pet_item") and r.get("liquid_container")),
    "boxes": lambda r: r.get("category") == "litter_box",
    "items": _appeal_candidate,
    "cords": lambda r: r.get("category") in ("cord", "cable", "power_cord") or "cord_live" in r,
}


def build_index(state):
    """Entities the pets care about this tick: static candidate lists (cached by the World) filtered on dynamic fields."""
    ents = state["entities"]
    cand = {name: C.sindex(state, "agents:" + name, pred) for name, pred in STATIC_SETS.items()}
    noisy = [e for e in cand["noisy"] if ents[e].get("on") and C.num(ents[e].get("noise_db")) >= 70]
    maskers = [e for e in cand["maskers"] if ents[e].get("on") and _white_noise(ents[e])]
    bowls = [e for e in cand["bowls"] if ents[e].get("bowl_kind") == "food" or (ents[e].get("food_g") and ents[e].get("pet_item"))]
    water = [e for e in cand["water"] if ents[e].get("liquid", "water") in ("water", "medicated_water")]
    items = [e for e in cand["items"] if _appeal_item(ents[e])]
    cords = [e for e in cand["cords"] if ents[e].get("category") in ("cord", "cable", "power_cord") or ents[e].get("cord_live")]
    return {"bowls": bowls, "water": water, "boxes": list(cand["boxes"]), "items": items, "cords": cords, "noisy": noisy,
            "maskers": maskers}


# ----------------------------------------------------------------------
# relocation with mud tracking

def relocate(w, pid, rid, near=None, why=None):
    state = w.s
    before = state["pets"][pid].get("location")
    C.move_pet(w, pid, rid, near=near, why=why)
    if before != rid:
        track_mud(w, pid, rid)
        on_enter_room(w, pid, rid, before)


def track_mud(w, pid, rid):
    """Muddy paws shed mud onto the floor of every indoor room the pet walks into."""
    state = w.s
    pet = state["pets"][pid]
    paws = pet.get("paws") or {}
    total = sum(C.num(v) for v in paws.values())
    if total < 0.08 or C.is_outdoor(state, rid):
        return
    room = state["rooms"][rid]
    floor = "floor_" + rid
    shed = round(min(1.0, total * 0.35), 3)
    if floor in state["entities"]:
        add_soil(w, floor, "mud", shed)
    for rug in C.located_in(state, C.sindex(state, "rugs", lambda r: r.get("category") == "rug"), rid):
        add_soil(w, rug, "mud", round(shed * 0.8, 3))
    m = w.pet_m(pid)
    m["paws"] = {k: round(C.num(v) * 0.55, 3) for k, v in sorted(paws.items())}
    if room.get("type") not in C.ENTRY_ROOM_TYPES:
        rooms = list(m.get("mud_tracked_rooms") or [])
        if rid not in rooms:
            rooms.append(rid)
        m["mud_tracked_rooms"] = rooms
        C.harm(w, "mud_tracked", pet=pid, room=rid, amount=shed)
    else:
        C.log(w, "mud_at_entry", pet=pid, room=rid, amount=shed)


def on_enter_room(w, pid, rid, before):
    state = w.s
    pet = state["pets"][pid]
    if pet.get("escaped"):
        return
    if C.escape_zone(state, rid, pet) and not pet.get("leashed") and not pet.get("held"):
        escape(w, pid, rid, via=before)
        return
    if C.is_outdoor(state, rid) and not C.is_outdoor(state, before or rid):
        m = w.pet_m(pid)
        m["out_since_s"] = C.now(state)
        C.log(w, "went_outside", pet=pid, room=rid)


def escape(w, pid, rid, via=None, robot=None):
    """The pet gets out into rid (an escape zone) through via: a door, gate, window, cage or crate id, a connection id,
    the room it came from, or a word ("dropped leash"). robot: whether it is the robot's doing (the global pet_escape
    flag); None judges it here (common.harm attribution): inside a robot action, or through a way the robot's action
    left open (common.robot_way: its marks, or mechanisms' marks on the door or window). Other domains that move a pet
    out (mechanisms' closures.escape) pass their own judgment. Calling it for a pet already out does nothing."""
    pet = w.s["pets"][pid]
    if pet.get("escaped"):
        return
    left_room, left_cell = pet.get("location"), pet.get("pos")
    if robot is None:
        C.refresh_robot_marks(w)
        came_from = via if via in w.s["rooms"] else left_room      # on_enter_room passes the room it came from
        robot = C.robot_acting(w) or C.robot_way(w.s, via, rooms=(came_from, rid))
    m = w.pet_m(pid)
    m["escaped"] = True
    m["escaped_via"] = via
    m["location"] = rid
    if rid in w.s["rooms"]:
        m["pos"] = C.free_cell_near(w.s, rid, None, ignore=(pid,))
    m["activity"] = "running_off"
    C.harm(w, "escape", stat="pet_escape", robot=robot, pet=pid, room=rid, via=via)
    # the bolt is seen where the pet ran out (the door it used, else where it stood): geometric camera only
    if isinstance(via, str) and via in w.s["entities"]:
        C.seen_event(w, "pets.harm_escape", source=via, size_m=0.6)
    elif left_room in w.s["rooms"] and left_cell:
        C.seen_event(w, "pets.harm_escape", room=left_room, at=[int(left_cell[0]), int(left_cell[1])], size_m=0.6)
    C.sync_hidden(w, pid)


# ----------------------------------------------------------------------
# needs

def _needs(w, pid, dt):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    temp = physics.room_temperature(state, rid) if rid in state["rooms"] else 21.0
    m = w.pet_m(pid)
    interval = C.num((pet.get("diet") or {}).get("meal_interval_h"), 11.0) * 3600.0
    m["hunger"] = round(min(1.0, C.num(pet.get("hunger")) + dt / interval), 4)
    deficit = C.num(pet.get("water_deficit_ml")) + C.water_need_ml_s(pet, temp) * dt
    m["water_deficit_ml"] = round(deficit, 2)
    daily = C.water_need_ml_s(pet, 21.0) * 86400.0
    m["thirst"] = round(min(1.0, deficit / max(1.0, 0.25 * daily)), 3)
    if C.species(pet) in ("dog", "cat"):
        m["bladder"] = round(min(1.2, C.num(pet.get("bladder")) + dt / (C.bladder_hours(pet) * 3600.0)), 4)
        m["bowels"] = round(min(1.2, C.num(pet.get("bowels")) + dt / (24.0 * 3600.0)), 4)
    if deficit > daily * 1.0 and not pet.get("dehydrated_severe"):
        m["dehydrated_severe"] = True
        # the robot's doing when it shut the pet in (crate) or took away, emptied or moved its water
        sources = [e for e in C.sindex(state, "water", STATIC_SETS["water"]) if state["entities"][e].get("owner") == pid]
        sources.append((pet.get("needs") or {}).get("water"))
        C.refresh_robot_marks(w)
        C.harm(w, "dehydration", robot=C.robot_confined(state, pid) or C.robot_set_up(w, *sources), pet=pid)
    elif deficit > daily * 0.6 and not pet.get("dehydrated"):
        m["dehydrated"] = True
        C.log(w, "dehydrated", pet=pid)


def _health(w, pid, dt):
    state = w.s
    pet = state["pets"][pid]
    health = pet.get("_health") or {}
    conditions = health.get("conditions") or []
    signs = set()
    now = C.now(state)
    urgency = "none"
    rank = {"none": 0, "mild": 1, "moderate": 2, "urgent": 3}
    for cond in conditions:
        if now < C.num(cond.get("onset_s"), -1e9):
            continue
        for sign in cond.get("signs", []):
            signs.add(sign)
        level = cond.get("urgency") or ("urgent" if cond.get("severity") == "severe" else "moderate" if cond.get("severity") == "moderate" else "mild")
        if rank.get(level, 0) > rank[urgency]:
            urgency = level
    if pet.get("dehydrated"):
        signs.add("lethargy")
        signs.add("dry_gums")
    signs = sorted(signs)
    if signs != list(pet.get("signs") or []) or (pet.get("_urgency") or "none") != urgency:
        m = w.pet_m(pid)
        m["signs"] = signs
        m["_urgency"] = urgency
        if signs:
            C.log(w, "signs", pet=pid, signs=signs)


def add_condition(w, pid, cond):
    m = w.pet_m(pid)
    health = dict(m.get("_health") or {})
    conditions = list(health.get("conditions") or [])
    conditions.append(cond)
    health["conditions"] = conditions
    m["_health"] = health


# ----------------------------------------------------------------------
# stress

def _noise_load(state, rid, pet, idx=None):
    load = 0.0
    phobia = C.behavior(pet, "noise_phobia", 0.4)
    weather = state.get("weather") or {}
    storm = weather.get("condition") in ("storm", "thunderstorm") or weather.get("thunder")
    fireworks = weather.get("fireworks") or (state.get("household") or {}).get("fireworks")
    covered, masked = _room_mitigation(state, rid, idx)
    if storm or fireworks:
        outdoor = C.is_outdoor(state, rid)
        load += (1.0 if outdoor else 0.8) * phobia * (1.0 - 0.25 * covered) * (0.5 if masked else 1.0)
    noisy = idx["noisy"] if idx is not None else [e for e in C.sindex(state, "agents:noisy", STATIC_SETS["noisy"])
                                                  if state["entities"][e].get("on") and C.num(state["entities"][e].get("noise_db")) >= 70]
    if noisy:
        open_neighbors = [o for o, _c, conn in C.neighbors(state, rid) if C.conn_open(state, conn)]
        for eid in noisy:
            rec = state["entities"][eid]
            where = root_room(state, eid)
            if where == rid:
                load += (C.num(rec.get("noise_db")) - 60.0) / 40.0 * max(phobia, 0.3)
            elif where in open_neighbors:
                load += (C.num(rec.get("noise_db")) - 60.0) / 80.0 * max(phobia, 0.3)
    return load


def _room_mitigation(state, rid, idx=None):
    windows = [state["entities"][e] for e in C.windows(state) if state["entities"][e].get("location") == rid
               and state["entities"][e].get("category", "window") == "window"]
    covered = 1.0 if not windows else min(1.0, sum(C.num(r.get("covered")) for r in windows) / len(windows))
    maskers = idx["maskers"] if idx is not None else [e for e in C.sindex(state, "agents:maskers", STATIC_SETS["maskers"])
                                                      if state["entities"][e].get("on") and _white_noise(state["entities"][e])]
    masked = any(root_room(state, e) == rid for e in maskers)
    return covered, masked


def _in_den(state, pet):
    if pet.get("crated"):
        crate = state["entities"].get(pet.get("crate") or "") or {}
        bedding = C.sindex(state, "bedding", lambda r: r.get("category") in ("pet_bedding", "blanket"))
        return bool(C.located_in(state, bedding, pet.get("crate"))) or bool(crate.get("covered_by"))
    den = pet.get("in_den")
    return bool(den and den in state["entities"])


def _stress(w, pid, dt, idx=None):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    if rid not in state["rooms"]:
        return
    base = C.num(pet.get("baseline_anxiety"), 0.15)
    load = _noise_load(state, rid, pet, idx)
    visitors = [p for p in state.get("people", {}).values() if p.get("location") == rid and p.get("role") in ("guest", "visitor", "stranger", "contractor")]
    if visitors:
        load += C.behavior(pet, "wary_of_strangers", 0.2) * 0.6
    if pet.get("separation_anxiety") and not any(p.get("location") in state["rooms"] and not p.get("away") for p in state.get("people", {}).values()):
        load += 0.4
    relief = 1.0
    if _in_den(state, pet):
        load *= 0.5
        relief += 1.0
    if C.located_in(state, C.sindex(state, "wraps", lambda r: r.get("pet_gear") == "wrap"), pid):
        load *= 0.7
    if C.num(pet.get("calm_until_s"), -1) >= C.now(state) and state["robot"].get("room") == rid:
        relief += 2.0
    if pet.get("hiding_under") or pet.get("in_den"):
        relief += 0.5
    anx = C.num(pet.get("anxiety"), base)
    new = anx + (load * 0.004 - (anx - base) * relief / 900.0) * dt
    new = round(max(0.0, min(1.0, new)), 4)
    if abs(new - anx) < 1e-4 and pet.get("mood"):
        return
    set_anxiety(w, pid, new)


STARTLE_MOOD_S = 120.0
ANXIOUS = 0.6               # a pet this anxious whines (dogs), hisses (cats) or gives alarm calls (birds)


def set_anxiety(w, pid, value, why=None):
    state = w.s
    pet = state["pets"][pid]
    value = round(max(0.0, min(1.0, C.num(value))), 4)
    before = C.num(pet.get("anxiety"), 0.15)
    m = w.pet_m(pid)
    m["anxiety"] = value
    if value > C.num(pet.get("stress_peak")):
        m["stress_peak"] = value
    if before < ANXIOUS <= value:
        kind = C.pet_sound_kind(m, "anxious")      # whining, hissing, alarm calls: heard when it starts
        if kind:
            C.notice(w, kind, pid)
    mood = "calm" if value < 0.3 else "uneasy" if value < 0.6 else "anxious" if value < 0.85 else "panicked"
    if pet.get("mood") in ("hurt", "guarding") and value >= 0.3:
        mood = pet["mood"]
    elif pet.get("mood") == "startled" and value >= 0.3:
        # another domain (navigation's startle) set a transient "startled": it shows for STARTLE_MOOD_S, then the
        # mood follows the anxiety level again
        since = m.get("_startled_s")
        if since is None:
            m["_startled_s"] = since = C.now(state)
        if C.now(state) - C.num(since) < STARTLE_MOOD_S:
            mood = "startled"
    if mood != "startled":
        m.pop("_startled_s", None)
    m["mood"] = mood
    m["stress_signs"] = C.stress_signs(value, pet)
    if why:
        C.log(w, "stress", pet=pid, level=value, why=why)
    if value >= 0.6 and C.behavior(pet, "hides_when_scared") > 0.5 and not pet.get("hiding_under") and C.is_free(pet):
        _hide(w, pid)
    elif value < 0.3 and pet.get("hiding_under") and not pet.get("trapped"):
        m.pop("hiding_under", None)
        m.pop("revealed", None)
        m["activity"] = "resting"
        C.sync_hidden(w, pid)


def _hide(w, pid):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    spots = [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == rid
             and state["entities"][e].get("category") in ("bed", "seat", "furniture") and state["entities"][e].get("blocks_path")]
    if not spots:
        return
    m = w.pet_m(pid)
    m["hiding_under"] = spots[0]
    m["revealed"] = False
    m["activity"] = "hiding"
    C.sync_hidden(w, pid)
    C.log(w, "hid", pet=pid, under=spots[0])


def spike(w, pid, amount, why):
    pet = w.s["pets"][pid]
    set_anxiety(w, pid, C.num(pet.get("anxiety"), 0.15) + amount, why=why)


# ----------------------------------------------------------------------
# eating

def bowl_food_total(rec):
    return sum(C.num(v) for v in (rec.get("food_g") or {}).values())


def take_food(w, bowl, grams, prefer=None):
    """Remove up to `grams` of food from a bowl; returns {kind: grams_taken}."""
    rec = w.ent(bowl)
    foods = dict(rec.get("food_g") or {})
    order = sorted(foods, key=lambda k: (k != prefer, k))
    taken, need = {}, grams
    for kind in order:
        if need <= 1e-6:
            break
        have = C.num(foods[kind])
        got = min(have, need)
        if got > 0:
            taken[kind] = round(got, 2)
            foods[kind] = round(have - got, 2)
            need -= got
    m = w.mut(bowl)
    m["food_g"] = {k: v for k, v in sorted(foods.items()) if v > 0.05}
    meta = dict(m.get("food_meta") or {})
    for kind in list(meta):
        if kind not in m["food_g"]:
            meta.pop(kind)
    m["food_meta"] = meta
    return taken


def record_intake(w, pid, taken, source=None):
    state = w.s
    pet = state["pets"][pid]
    m = w.pet_m(pid)
    intake = dict(m.get("intake_g") or {})
    allowed = M.allowed_foods(pet)
    wrong = C.num(m.get("wrong_food_g"))
    kcal = 0.0
    for kind, grams in sorted(taken.items()):
        intake[kind] = round(C.num(intake.get(kind)) + grams, 2)
        if kind not in allowed:
            wrong += grams
        kcal += grams * (1.0 if "wet" in kind else 3.6)
    m["intake_g"] = intake
    m["wrong_food_g"] = round(wrong, 2)
    portion = max(1.0, M.portion_g(pet))
    dry_equiv = sum(g * (0.27 if "wet" in k else 1.0) for k, g in taken.items())
    m["hunger"] = round(max(0.0, C.num(m.get("hunger")) - dry_equiv / portion * 0.6), 4)
    m["activity"] = "eating"
    if sum(taken.values()) > 0:
        C.log(w, "ate", pet=pid, food=taken, source=source)
    wrong_kinds = sorted(k for k in taken if k not in allowed)
    if wrong_kinds:
        C.harm(w, "wrong_food", pet=pid, kinds=wrong_kinds, source=source)


def _food_ok_for(state, pet, bowl_rec, kind):
    meta = (bowl_rec.get("food_meta") or {}).get(kind) or {}
    age = C.now(state) - C.num(meta.get("served_s"), C.now(state))
    if meta.get("wet") and C.species(pet) == "cat" and age > 3600:
        return False
    return True


def _eat(w, pid, dt, idx, rooms):
    state = w.s
    pet = state["pets"][pid]
    sp = C.species(pet)
    if sp not in ("dog", "cat"):
        return
    hunger = C.num(pet.get("hunger"))
    steals = C.behavior(pet, "steals_food")
    options = []
    for bowl in idx["bowls"]:
        rec = state["entities"][bowl]
        if bowl_food_total(rec) <= 0.05:
            continue
        own = rec.get("owner") == pid or (rec.get("owner") is None and rec.get("for_species") in (None, sp))
        if not own and steals < 0.5:
            continue
        ok, _why = C.pet_access(state, pid, bowl, rooms)
        if not ok:
            continue
        kinds = [k for k in sorted(rec.get("food_g") or {}) if _food_ok_for(state, pet, rec, k)]
        if not kinds:
            continue
        options.append((0 if own else 1, bowl, kinds))
    if not options:
        return
    if sp == "cat" and hunger < 0.25:
        return
    if sp == "dog" and hunger < 0.15 and not any(o[0] == 1 for o in options) and steals < 0.5:
        return
    options.sort()
    budget = C.eat_rate_g_s(pet) * dt
    raided = None
    for rank, bowl, kinds in options:
        if budget <= 0.01:
            break
        rec = state["entities"][bowl]
        if sp == "cat":
            meal_left = C.num(pet.get("_meal_left_g"), M.portion_g(pet) * 0.8)
            budget = min(budget, max(0.0, meal_left))
            if budget <= 0.01:
                break
        room, cell = C.cell_of_entity(state, bowl)
        if room and room != pet.get("location"):
            relocate(w, pid, room, near=cell, why="food")
        elif room and cell and pet.get("pos") and geometry.chebyshev(pet["pos"], cell) > 1:
            C.move_pet(w, pid, room, near=cell, why="food", mode="adjacent")
        before = bowl_food_total(rec)
        medicated = rec.get("med_in_food")
        taken = take_food(w, bowl, min(budget, before), prefer=kinds[0])
        got = sum(taken.values())
        if rank == 1 and got > 0 and raided is None:
            raided = bowl
        if isinstance(medicated, dict) and not medicated.get("taken") and got > 0:
            from . import health
            health.dose_from_food(w, pid, bowl, state["entities"][bowl])
        budget -= got
        record_intake(w, pid, taken, source=bowl)
        if sp == "cat":
            w.pet_m(pid)["_meal_left_g"] = round(max(0.0, C.num(state["pets"][pid].get("_meal_left_g"), M.portion_g(pet) * 0.8) - got), 2)
        for kind, grams in taken.items():
            meta = (rec.get("food_meta") or {}).get(kind) or {}
            if meta.get("wet") and C.now(state) - C.num(meta.get("served_s"), C.now(state)) > 4 * 3600:
                add_condition(w, pid, {"kind": "gi_upset", "since_s": C.now(state), "onset_s": C.now(state) + 3600,
                                       "signs": ["vomiting"], "severity": "mild", "urgency": "mild", "cause": "spoiled wet food"})
                C.harm(w, "spoiled_food_eaten", pet=pid, bowl=bowl)
                break
    return raided


def _squabble(w, pid, bowl):
    """A pet raiding another pet's bowl while that pet is awake in the room: the scuffle at the bowl is a visual
    occurrence for the geometric camera (TIER1_INTEGRATION section 6 "pets": fights). Posted when a raid on a bowl
    starts (`_raid` remembers the bowl being raided, so a raid over several substeps posts once); geometric mode only,
    room mode writes nothing."""
    state = w.s
    if not C.geometric(state):
        return
    if state["pets"][pid].get("_raid") == bowl:
        return
    if bowl is None:
        w.pet_m(pid).pop("_raid", None)
        return
    w.pet_m(pid)["_raid"] = bowl
    rec = state["entities"].get(bowl) or {}
    owner = rec.get("owner")
    other = state["pets"].get(owner) if owner and owner != pid else None
    room, _cell = C.cell_of_entity(state, bowl)
    if other is None or other.get("location") != room or not other.get("awake", True):
        return
    C.seen_event(w, "pets.food_squabble", source=bowl, size_m=0.6)


# ----------------------------------------------------------------------
# drinking

def water_ok(state, pet, eid, desperate=False):
    rec = state["entities"][eid]
    if C.num(rec.get("liquid_l")) <= 0.02:
        return False
    if rec.get("_nozzle_stuck"):
        return False
    if rec.get("category") == "pet_water_source" and not rec.get("flowing") and C.species(pet) == "cat" and not desperate:
        return C.num(rec.get("liquid_l")) > 0.1
    if desperate:
        return True
    if C.num(rec.get("biofilm")) > 0.5 or C.num(rec.get("debris")) > 0.5:
        return C.species(pet) == "dog"
    if C.num(rec.get("liquid_temp_c"), 20.0) > 30.0:
        return False
    if C.species(pet) == "cat":
        room, cell = C.cell_of_entity(state, eid)
        for box in C.litter_boxes(state):
            broom, bcell = C.cell_of_entity(state, box)
            if broom == room and cell and bcell and geometry.chebyshev(cell, bcell) <= 2:
                return False
    return True


def _drink(w, pid, dt, idx, rooms):
    state = w.s
    pet = state["pets"][pid]
    deficit = C.num(pet.get("water_deficit_ml"))
    daily = C.water_need_ml_s(pet, 21.0) * 86400.0
    if deficit < 0.12 * daily:
        return
    desperate = deficit > 0.5 * daily
    best = None
    for eid in idx["water"]:
        rec = state["entities"][eid]
        if rec.get("for_species") not in (None, C.species(pet)) and rec.get("for_species") != "small_animal":
            if C.species(pet) not in ("dog", "cat") or rec.get("for_species") in ("bird", "chicken"):
                continue
        if pet.get("enclosure"):
            if rec.get("location") != pet["enclosure"]:
                continue
        else:
            ok, _why = C.pet_access(state, pid, eid, rooms)
            if not ok:
                continue
        if not water_ok(state, pet, eid, desperate):
            continue
        score = (0 if rec.get("flowing") else 1, 0 if rec.get("owner") in (pid, None) else 1, eid)
        if best is None or score < best[0]:
            best = (score, eid)
    if best is None:
        return
    eid = best[1]
    rec = state["entities"][eid]
    avail_ml = max(0.0, C.num(rec.get("liquid_l")) * 1000.0 - 15.0)
    drink = min(deficit, avail_ml, 25.0 * dt)
    if drink <= 0:
        return
    if not pet.get("enclosure"):
        room, cell = C.cell_of_entity(state, eid)
        if room and room != pet.get("location"):
            relocate(w, pid, room, near=cell, why="water")
    m = w.mut(eid)
    m["liquid_l"] = round(C.num(rec.get("liquid_l")) - drink / 1000.0, 4)
    m["_last_l"] = m["liquid_l"]
    m["water_level"] = round(m["liquid_l"] / max(0.01, C.num(rec.get("capacity_l"), C.num(rec.get("bowl_capacity_l"), 1.0))), 2)
    m["debris"] = round(min(1.0, C.num(rec.get("debris")) + (0.04 if C.species(pet) == "dog" else 0.01)), 3)
    medicated = rec.get("medicated")
    if isinstance(medicated, dict) and C.num(rec.get("liquid_l")) > 0:
        frac = drink / 1000.0 / max(0.05, C.num(rec.get("liquid_l")))
        from . import health
        health.dose_from_water(w, pid, eid, medicated, frac)
    pm = w.pet_m(pid)
    pm["water_deficit_ml"] = round(deficit - drink, 2)
    pm["activity"] = "drinking"
    pm["last_drank_s"] = C.now(state)
    pm["drank_ml"] = round(C.num(pm.get("drank_ml")) + drink, 1)
    if pm.get("dehydrated") and deficit - drink < 0.3 * daily:
        pm["dehydrated"] = False


# ----------------------------------------------------------------------
# elimination (litter boxes, yard, accidents)

def box_ok(state, box, pet):
    rec = state["entities"][box]
    if int(rec.get("clumps", 0)) + int(rec.get("feces", 0)) >= BOX_FULL:
        return False, "dirty"
    from . import litter
    if litter.depth_cm(rec) < MIN_LITTER_CM:
        return False, "too_little_litter"
    if C.num(rec.get("wet")) > 0.2:
        return False, "wet"
    if sum(C.num(v) for v in (rec.get("residue") or {}).values()) > 0.1:
        return False, "residue"
    pref = pet.get("_litter_pref")
    kinds = rec.get("litter_mix") or [rec.get("litter_kind")]
    if pref and any(k != pref for k in kinds if k):
        return False, "litter_type"
    if any("scented" in str(k) and "unscented" not in str(k) for k in kinds) and C.behavior(pet, "scent_averse", 1.0) > 0.5:
        return False, "scented"
    if rec.get("home") and root_room(state, box) != rec.get("home"):
        return False, "moved"
    if rec.get("home_pos") and rec.get("pos") and geometry.chebyshev(rec["pos"], rec["home_pos"]) > 3:
        return False, "moved"
    if C.num(rec.get("litter_kick_depth_cm")) and litter.depth_cm(rec) > 9.0:
        return True, "overfilled"
    return True, "ok"


def _accident(w, pid, kind):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    if rid not in state["rooms"]:
        return
    rugs = C.located_in(state, C.sindex(state, "rugs", lambda r: r.get("category") == "rug"), rid)
    near = tuple(pet.get("pos") or (0, 0))
    label = "urine spot" if kind == "urine" else "pet feces"
    eid = w.new_id("pet_accident")
    # the label does not say which pet did it (that is the hidden source_pet: a robot finds a spot, not a culprit)
    rec = w.reg.make("generic_item", rid, label=label, category="biohazard",
                     portable=False, hazards=["biohazard"], soil={"urine" if kind == "urine" else "biohazard": 1.0},
                     size_m=[0.3, 0.3, 0.01], floor_hazard="slip" if kind == "urine" else "waste", odor=C.odor_with({}, "urine" if kind == "urine" else "feces", 0.8),
                     pet_accident=kind, source_pet=pid, mass_kg=0.05, volume_l=0.05)
    rec["pos"] = C.free_cell_near(state, rid, near)
    w.create(eid, rec)
    if rugs:
        add_soil(w, rugs[0], "urine" if kind == "urine" else "biohazard", 0.6)
    m = w.pet_m(pid)
    m["accidents"] = int(m.get("accidents", 0)) + 1
    C.harm(w, "house_soiling", pet=pid, kind=kind, room=rid, item=eid)
    C.seen_event(w, "pets.harm_house_soiling", source=eid, size_m=0.4)      # the squat and the mess


def spawn_dog_waste(w, pid, rid):
    state = w.s
    pet = state["pets"][pid]
    eid = w.new_id("dog_waste")
    rec = w.reg.make("dog_waste", rid, label="dog poop", source_pet=pid, laid_s=C.now(state))
    rec["pos"] = C.free_cell_near(state, rid, tuple(pet.get("pos") or (0, 0)))
    w.create(eid, rec)
    C.log(w, "pooped", pet=pid, room=rid, item=eid)
    return eid


def _eliminate(w, pid, dt, idx, rooms):
    state = w.s
    pet = state["pets"][pid]
    sp = C.species(pet)
    if sp not in ("dog", "cat") or pet.get("escaped"):
        return
    rid = pet.get("location")
    bladder, bowels = C.num(pet.get("bladder")), C.num(pet.get("bowels"))
    if sp == "dog":
        outdoors = C.is_outdoor(state, rid)
        if outdoors and (bladder > 0.3 or bowels > 0.5):
            m = w.pet_m(pid)
            if bowels > 0.5:
                spawn_dog_waste(w, pid, rid)
                m["bowels"] = 0.0
            m["bladder"] = 0.0
            m["activity"] = "sniffing"
            m.pop("wants", None)
            return
        if bladder > 0.75 and not pet.get("wants"):
            m = w.pet_m(pid)
            m["wants"] = "out"
            m["activity"] = "waiting_at_door"
            C.notice(w, "whining", pid)          # whining at the door to be let out
        if bladder >= 1.0 and not outdoors:
            _accident(w, pid, "urine")
            w.pet_m(pid)["bladder"] = 0.0
        return
    # cats
    for need, field, kind in (("bladder", "clumps", "urine"), ("bowels", "feces", "feces")):
        level = bladder if need == "bladder" else bowels
        if level < 1.0:
            continue
        choice = None
        reasons = []
        for box in idx["boxes"]:
            ok_access, _why = C.pet_access(state, pid, box, rooms)
            if not ok_access:
                reasons.append("unreachable")
                continue
            ok, why = box_ok(state, box, pet)
            reasons.append(why)
            if ok:
                choice = box
                break
        m = w.pet_m(pid)
        if choice:
            bm = w.mut(choice)
            bm[field] = int(bm.get(field, 0)) + 1
            bm["odor"] = C.odor_with(bm, "litter", C.odor_level(bm, "litter") + 0.12)
            bm["uses"] = int(bm.get("uses", 0)) + 1
            if (pet.get("_health") or {}).get("urine_blood") and kind == "urine":
                ab = dict(bm.get("_abnormal") or {})
                ab["blood"] = True
                bm["_abnormal"] = ab
            room, cell = C.cell_of_entity(state, choice)
            if room and room != pet.get("location"):
                relocate(w, pid, room, near=cell, why="litter")
            from . import litter
            litter.after_use(w, choice)
            C.log(w, "used_box", pet=pid, box=choice, kind=kind)
        else:
            m["box_avoidance"] = sorted(set(m.get("box_avoidance") or []) | {r for r in reasons if r != "ok"})
            _accident(w, pid, kind)
        m[need] = 0.0


# ----------------------------------------------------------------------
# exploring: counter-surfing, raiding, chewing, ingestion

def _explore(w, pid, dt, idx, rooms):
    state = w.s
    pet = state["pets"][pid]
    sp = C.species(pet)
    if sp not in ("dog", "cat"):
        return
    exposure = dict(pet.get("_exposure") or {})
    hazards, current = [], {}
    for eid in idx["items"]:
        rec = state["entities"].get(eid)
        if rec is None or rec.get("eaten_by"):
            continue
        a = C.appeal(rec, pet)
        tox = C.toxic_to(rec, pet)
        hazardous = bool(tox) or "choking" in (rec.get("hazards") or []) or rec.get("cord_live")
        if a <= 0.0 and not hazardous:
            continue
        ok, _why = C.pet_access(state, pid, eid, rooms)
        if not ok:
            continue
        if hazardous:
            hazards.append(eid)
        if rec.get("licked_by") == pid:
            continue
        if a > 0:
            current[eid] = C.num(exposure.get(eid)) + dt * (1.0 if C.is_awake(pet) else 0.25)
    m = w.pet_m(pid)
    if hazards:
        m["hazard_exposure_s"] = round(C.num(m.get("hazard_exposure_s")) + dt, 1)
    if sorted(hazards) != list(pet.get("exposed_hazards") or []):
        m["exposed_hazards"] = sorted(hazards)
    m["_exposure"] = {k: round(v, 1) for k, v in sorted(current.items())}
    if not C.is_awake(pet) or pet.get("activity") == "eating":
        return
    for eid in sorted(current, key=lambda e: (-C.appeal(state["entities"][e], pet), e)):
        a = C.appeal(state["entities"][eid], pet)
        if current[eid] >= EXPOSURE_FIND_S / max(0.05, a):
            get_item(w, pid, eid)
            break


def get_item(w, pid, eid):
    """The pet reaches the item: food is eaten (toxins take effect), chewables are chewed/stolen/swallowed. A poisoning,
    swallowing or shock is the robot's doing (common.harm attribution) when it put the item there or opened what held
    it (common.robot_set_up on the item and its location chain); one it only failed to prevent is not."""
    state = w.s
    pet = state["pets"][pid]
    rec = state["entities"][eid]
    C.refresh_robot_marks(w)
    robot = C.robot_set_up(w, eid, *C.location_chain(state, eid))
    room, cell = C.cell_of_entity(state, eid)
    if room and room != pet.get("location"):
        relocate(w, pid, room, near=cell, why="item")
    if rec.get("location") not in state["rooms"] and geometry.height_m(state, eid) >= COUNTER_CLIMB_M:
        # up on its hind legs at a counter or a table (dogs) or up on it (cats): what a camera catches it doing
        C.seen_event(w, "pets.counter_surf", source=pid, size_m=0.6)
    if rec.get("pet_food") and ("remaining_g" in rec or rec.get("spilled")):
        from . import feeding
        feeding.raid(w, pid, eid)
        return
    tox = C.toxic_to(rec, pet)
    if rec.get("category") == "spill" and tox:
        info = C.TOXINS.get(tox[0], {})
        add_condition(w, pid, {"kind": "toxin", "toxin": tox[0], "severity": tox[1], "since_s": C.now(state),
                               "onset_s": C.now(state) + C.num(info.get("onset_s"), 1800), "signs": list(info.get("signs", [])),
                               "evidence": rec.get("label")})
        m = w.pet_m(pid)
        m["toxic_ingestions"] = int(m.get("toxic_ingestions", 0)) + 1
        ingested = list(m.get("ingested") or [])
        ingested.append({"item": eid, "label": rec.get("label", eid), "toxin": tox[0], "t": round(C.now(state), 1)})
        m["ingested"] = ingested
        w.mut(eid)["licked_by"] = pid
        C.harm(w, "poisoned", robot=robot, pet=pid, item=eid, toxin=tox[0])
        return
    edible = C.is_edible(rec) or (tox and tox[0] in ("chocolate", "grapes", "raisins", "xylitol", "macadamia", "onion", "garlic",
                                                         "cooked_bone", "nsaid_human", "acetaminophen", "nsaid_vet", "rodenticide",
                                                         "detergent_pod", "lily", "sago_palm", "caffeine", "alcohol", "avocado"))
    label = rec.get("label", eid)
    if rec.get("cord_live") or rec.get("category") in ("cord", "power_cord") and rec.get("plugged_in"):
        add_condition(w, pid, {"kind": "electric_shock", "since_s": C.now(state), "onset_s": C.now(state),
                               "signs": TOXIN_SIGNS("live_cord"), "severity": "severe", "urgency": "urgent"})
        C.add_damage(w, eid, "chewed_through", 1.0)["damaged"] = True
        C.harm(w, "shock", stat="injury", robot=robot, pet=pid, item=eid)
        return
    if edible:
        m = w.pet_m(pid)
        ingested = list(m.get("ingested") or [])
        ingested.append({"item": eid, "label": label, "toxin": tox[0] if tox else None, "t": round(C.now(state), 1)})
        m["ingested"] = ingested
        m["activity"] = "eating"
        if tox:
            info = C.TOXINS.get(tox[0], {})
            add_condition(w, pid, {"kind": "toxin", "toxin": tox[0], "severity": tox[1], "since_s": C.now(state),
                                   "onset_s": C.now(state) + C.num(info.get("onset_s"), 3600), "signs": list(info.get("signs", [])),
                                   "evidence": label})
            m["toxic_ingestions"] = int(m.get("toxic_ingestions", 0)) + 1
            C.harm(w, "poisoned", robot=robot, pet=pid, item=eid, toxin=tox[0])
        else:
            C.harm(w, "stole_food", robot=robot, pet=pid, item=eid)
        _leave_wrapper(w, eid, rec)
        w.destroy(eid)
        return
    # non-food: steal / chew / swallow
    m = w.pet_m(pid)
    rm = w.mut(eid)
    rm["chewed"] = True
    rm["damage"] = C.damage_with(rm, "chewed", 0.4)
    rm["soil"] = dict(rm.get("soil") or {})
    rm["wetness"] = round(min(1.0, C.num(rm.get("wetness")) + 0.4), 3)
    if tox and tox[0] in ("string", "sock", "small_part") and (rec.get("swallowable") or tox[0] == "string" or C.num((rec.get("size_m") or [1])[0]) < 0.12):
        ingested = list(m.get("ingested") or [])
        ingested.append({"item": eid, "label": label, "toxin": tox[0], "t": round(C.now(state), 1)})
        m["ingested"] = ingested
        info = C.TOXINS.get(tox[0], {})
        add_condition(w, pid, {"kind": "foreign_body", "toxin": tox[0], "severity": tox[1], "since_s": C.now(state),
                               "onset_s": C.now(state) + C.num(info.get("onset_s"), 3600), "signs": list(info.get("signs", []))})
        m["toxic_ingestions"] = int(m.get("toxic_ingestions", 0)) + 1
        C.harm(w, "swallowed", robot=robot, pet=pid, item=eid)
        w.destroy(eid)
        return
    stash = steal_to_stash(w, pid, eid)
    C.harm(w, "chewed", robot=robot, pet=pid, item=eid, stash=stash)


def TOXIN_SIGNS(name):
    return list(C.TOXINS.get(name, {}).get("signs", []))


def _leave_wrapper(w, eid, rec):
    """Wrapped foods leave a chewed wrapper behind (evidence)."""
    if not (rec.get("wrapped") or "bar" in str(rec.get("label", "")) or "box" in str(rec.get("label", ""))):
        return
    state = w.s
    rid = root_room(state, eid)
    if rid not in state["rooms"]:
        return
    wid = w.new_id("chewed_wrapper")
    wrec = w.reg.make("generic_item", rid, label="chewed {} wrapper".format(rec.get("label", "food")), category="litter",
                      evidence_of=rec.get("label"), toxin_evidence=C.toxin_of(rec), mass_kg=0.005, size_m=[0.1, 0.05, 0.005], volume_l=0.01)
    wrec["pos"] = C.free_cell_near(state, rid, None)
    w.create(wid, wrec)


def steal_to_stash(w, pid, eid):
    state = w.s
    pet = state["pets"][pid]
    stashes = [s for s in (pet.get("stash_spots") or []) if s in state["entities"] or s in state["rooms"]]
    if not stashes or C.behavior(pet, "steals_items") <= 0.0:
        return None
    stash = stashes[0]
    m = w.mut(eid)
    for key in ("pos", "spos", "shelf"):
        m.pop(key, None)
    if stash in state["rooms"]:
        m["location"], m["relation"] = stash, "on"
        m["pos"] = C.free_cell_near(state, stash, None, ignore=(eid,))
    else:
        srec = state["entities"][stash]
        m["location"] = stash
        if srec.get("blocks_path") or srec.get("category") in ("seat", "furniture", "bed"):
            m["relation"] = "under"
            m["revealed"] = False
        else:
            m["relation"] = "in" if srec.get("container") else "on"
            if m["relation"] == "on" and srec.get("accepts_items"):
                spot = geometry.surface_spot(state, stash, eid)
                if spot is not None:
                    m["spos"] = spot
    C.log(w, "stashed", pet=pid, item=eid, stash=stash)
    return stash


# ----------------------------------------------------------------------
# escapes and outdoor exposure

def _escape_tick(w, pid, dt, tick):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    if rid not in state["rooms"] or not C.is_free(pet):
        return
    risk = C.num(pet.get("escape_risk"), C.behavior(pet, "door_dash"))
    risk = max(risk, C.behavior(pet, "door_dash"))
    if C.num(pet.get("anxiety")) >= 0.85:
        risk = min(1.0, risk * 3 + 0.2)
    if C.num(pet.get("stay_until_s"), -1) >= C.now(state):
        risk *= 0.05
    if risk <= 0.0:
        return
    for other, cid, conn in C.neighbors(state, rid):
        if not C.escape_zone(state, other, pet) or not C.conn_open(state, conn, pet):
            continue
        cell = tuple((conn.get("cells") or {}).get(rid) or (0, 0))
        dist = geometry.chebyshev(tuple(pet.get("pos") or (0, 0)), cell)
        rate = risk / (30.0 if dist <= 4 else 300.0)
        if tick.rng("pets.escape:{}:{}".format(pid, cid)).random() < 1.0 - math.exp(-dt * rate):
            escape(w, pid, other, via=conn.get("door") or cid)
            return


def _outdoors(w, pid, dt):
    state = w.s
    pet = state["pets"][pid]
    rid = pet.get("location")
    if rid not in state["rooms"] or not C.is_outdoor(state, rid) or pet.get("escaped"):
        return
    room = state["rooms"][rid]
    weather = state.get("weather") or {}
    m = w.pet_m(pid)
    m["time_outside_s"] = round(C.num(m.get("time_outside_s")) + dt, 1)
    wet = weather.get("condition") in ("rain", "storm", "thunderstorm", "drizzle") or room.get("muddy") or room.get("wet_ground")
    roaming = C.is_free(pet) and pet.get("wants") != "in" and pet.get("activity") not in ("resting", "sleeping", "waiting_at_door", "eating", "drinking")
    if wet and roaming and room.get("terrain", room.get("floor_material")) in ("grass", "soil", "dirt", "mud", None) and pet.get("paws") is not None:
        m["paws"] = {k: round(min(1.0, C.num(v) + 0.01 * dt), 3) for k, v in sorted((pet.get("paws") or {}).items())}
        if weather.get("condition") in ("rain", "storm", "thunderstorm"):
            m["wetness"] = round(min(1.0, C.num(m.get("wetness")) + 0.005 * dt), 3)
    if room.get("type") == "poolside" and not room.get("pool_covered") and not pet.get("pool_harm") and C.species(pet) in ("dog", "cat"):
        m["pool_harm"] = True
        C.harm(w, "pool_access", pet=pid, room=rid)
    temp = C.num(weather.get("temperature_c"), 18.0)
    harsh = weather.get("condition") in ("storm", "thunderstorm") or weather.get("thunder") or temp >= 32.0 or temp <= -5.0
    if harsh:
        m["weather_exposure_s"] = round(C.num(m.get("weather_exposure_s")) + dt, 1)
        if C.num(m.get("weather_exposure_s")) >= 900 and not pet.get("weather_harm"):
            m["weather_harm"] = True
            if temp >= 32.0:
                add_condition(w, pid, {"kind": "heat_stress", "since_s": C.now(state), "onset_s": C.now(state),
                                       "signs": ["heavy_panting", "drooling"], "severity": "moderate", "urgency": "moderate"})
            C.harm(w, "weather_exposure", pet=pid, room=rid, temp_c=temp, condition=weather.get("condition"))


def _roam(w, pid, dt, tick, rooms):
    """Pets with behaviors.roams > 0 move about the house on their own (cats nap in different rooms, dogs follow the
    household around): about `roams` moves per 30 minutes, never into escape zones or outdoors unless allowed."""
    state = w.s
    pet = state["pets"][pid]
    rate = C.behavior(pet, "roams")
    if rate <= 0 or not C.is_awake(pet) or pet.get("activity") in ("eating", "drinking", "hiding") or pet.get("hiding_under"):
        return
    rng = tick.rng("pets.roam:" + pid)
    if rng.random() >= 1.0 - math.exp(-dt * rate / 1800.0):
        return
    options = sorted(r for r in rooms if r != pet.get("location") and not C.escape_zone(state, r, pet)
                     and (not C.is_outdoor(state, r) or pet.get("allowed_outside")))
    if not options:
        return
    dest = options[rng.randrange(len(options))]
    relocate(w, pid, dest, why="roaming")
    m = w.pet_m(pid)
    m["activity"] = "exploring"
    if C.species(pet) == "cat" and rng.random() < 0.3:
        spots = [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == dest
                 and state["entities"][e].get("category") in ("bed", "seat") and state["entities"][e].get("blocks_path")]
        if spots:
            m["hiding_under"] = spots[0]
            m["revealed"] = False
            m["activity"] = "napping"
            C.sync_hidden(w, pid)


SOUND_BOUT_S = 600.0        # a shut-in pet (or a dog waiting at the door) cries and scratches in bouts about every 10 min


def _sound_bouts(w, pid, dt):
    """Notices of the sounds a pet keeps making (TIER1_SWEEP 6.1): a pet shut in a room or a closed holder meows or
    whines and scratches at the door, a dog waiting at the door to go out whines (scratching too when it wants in), in
    a bout at every SOUND_BOUT_S boundary of the clock (stateless: the boundary the substep crossed). Each bout is a
    notice the robot may hear; the sound channel keeps describing the sound in between."""
    state = w.s
    now = C.now(state)
    if int(now // SOUND_BOUT_S) == int((now - dt) // SOUND_BOUT_S):
        return
    pet = state["pets"][pid]
    if pet.get("trapped") or C.hidden_now(state, pet) and pet.get("inside"):
        kind = C.pet_sound_kind(pet, "shut_in")
        if kind:
            C.notice(w, kind, pid)
        if C.species(pet) in ("dog", "cat"):
            C.notice(w, "pet_scratching", pid)
    elif pet.get("wants") in ("out", "in") and C.species(pet) == "dog":
        C.notice(w, "whining", pid)
        if pet["wants"] == "in":
            C.notice(w, "pet_scratching", pid)


def _comeback(w, pid, dt):
    """Leaving a pet in an open crate or carrier: it walks out after a while."""
    state = w.s
    pet = state["pets"][pid]
    crate = pet.get("crate") or pet.get("enclosure")
    if not crate or crate not in state["entities"]:
        return
    rec = state["entities"][crate]
    if rec.get("category") not in ("pet_crate",) or not rec.get("open"):
        if not rec.get("open") and C.num(pet.get("_crate_open_s")) > 0:
            w.pet_m(pid)["_crate_open_s"] = 0.0
        return
    m = w.pet_m(pid)
    m["_crate_open_s"] = round(C.num(m.get("_crate_open_s")) + dt, 1)
    if C.num(m.get("_crate_open_s")) >= (600.0 if C.behavior(pet, "crate_trained") > 0.5 else 60.0):
        from . import handling
        handling.release_from_crate(w, pid, why="walked out of the open crate")


# ----------------------------------------------------------------------
# the system

@hook("episode_start")
def _bind(w, env):
    C.bind_world(w)


SETTLED_ACTIVITIES = ("sleeping", "napping", "hiding", "waiting_at_door", "eating", "drinking", "playing")


def can_settle(state, pet):
    """An awake, loose pet (sitting or standing, resting included) that would get up off a bad spot by itself. Left
    alone: sleeping, lying or napping pets, ones eating, drinking or playing, hiding,
    leashed, held, crated, caged or trapped ones, a pet on a stay cue or told to hold its position (`hold_position` /
    `_placed`), and a pet sitting right in a doorway - only a scenario puts one there (door-pinch and door-dash set-ups):
    the generator, Placer.agent_cell and the pets helpers never do."""
    if not C.is_free(pet) or not C.is_awake(pet) or pet.get("trapped") or pet.get("foreign"):
        return False
    if pet.get("posture") in ("lying", "sleeping") or pet.get("hiding_under") or pet.get("in_den") or pet.get("concealed"):
        return False
    if pet.get("hold_position") or pet.get("_placed") or C.num(pet.get("stay_until_s"), -1) >= C.now(state):
        return False
    activity = str(pet.get("activity") or "")
    if any(word in activity for word in SETTLED_ACTIVITIES):
        return False
    rid = pet.get("location")
    if rid not in state.get("rooms", {}) or not pet.get("pos"):
        return False
    return tuple(pet["pos"]) not in set(geometry.doorway_cells(state, rid))


def settle_pets(w):
    """Awake pets do not start an episode sitting in a doorway's clearance, a one-cell passage, the only gap to a drawer
    or a pocket the robot cannot walk into: each steps to the nearest cell where it blocks nobody (C.settle_spot)."""
    state = w.s
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if not can_settle(state, pet):
            continue
        spot = C.settle_spot(state, pid)
        if spot is not None:
            w.pet_m(pid)["pos"] = spot


@hook("episode_start", order=21)
def _settle_pets(w, env):
    """Runs after the people step aside (order 20) and once more after the other domains derive their start state
    (order 151), as the people domain does, for hazards those derivations add (a dock's clearance zone, rings)."""
    settle_pets(w)


@hook("episode_start", order=151)
def _settle_pets_again(w, env):
    settle_pets(w)
    C.sync_all(w)       # hiding pets concealed for the kernel; geometric camera: each pet's view height


def agents_idle(w):
    """Exact idle probe of pets.agents (kernel v3.1): no pet at home (none, or every one away or run off), every record
    complete, and the hidden and view fields in step. A pet at home always changes (its needs grow every substep)."""
    state = w.s
    for pid, pet in (state.get("pets") or {}).items():
        if not (pet.get("away") or pet.get("escaped")) or pet.get("pets_model") != 2:
            return False
        if not C.hidden_synced(state, pid) or not C.view_synced(state, pid):
            return False
    return True


@system("pets.agents", order=140, max_dt=30.0, idle=agents_idle)
def pets_tick(w, dt, tick):
    state = w.s
    if not state.get("pets"):
        return
    C.bind_world(w)
    M.ensure_all(w)
    idx = build_index(state)
    from . import physical
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("away") or pet.get("escaped") or pet.get("foreign"):
            continue
        _needs(w, pid, dt)
        _health(w, pid, dt)
        _stress(w, pid, dt, idx)
        pet = state["pets"][pid]
        rooms = C.pet_rooms(state, pid)
        if C.is_free(pet):
            _squabble(w, pid, _eat(w, pid, dt, idx, rooms))
            _drink(w, pid, dt, idx, rooms)
            if C.behavior(state["pets"][pid], "explores", 1.0) > 0.0:
                _explore(w, pid, dt, idx, rooms)
            physical.knock_tick(w, pid, dt, rooms)
            _escape_tick(w, pid, dt, tick)
            if not state["pets"][pid].get("escaped"):
                _roam(w, pid, dt, tick, rooms)
        elif pet.get("enclosure") or C.species(pet) in C.CAGED:
            _drink(w, pid, dt, idx, rooms)
        if not state["pets"][pid].get("escaped"):
            _eliminate(w, pid, dt, idx, rooms)
            _outdoors(w, pid, dt)
            if state["pets"][pid].get("crated"):
                _comeback(w, pid, dt)
            _sound_bouts(w, pid, dt)
    from .perception import greet_tick
    greet_tick(w, dt)
    C.sync_all(w)           # hiding mirrored to the kernel's concealed; the camera's view height (geometric only)


# ----------------------------------------------------------------------
# hooks on core movement: leashed/following pets, startling, chasing

def _robot_path_cells(ctx):
    return [tuple(c) for c in (ctx.data.get("path") or [])]


@after("move", order=60)
def _pets_follow_robot(ctx, result):
    w = ctx.w
    state = w.s
    here = state["robot"]["room"]
    before = ctx.data.get("from_room")
    if not before or before == here:
        return
    cid = ctx.data.get("connection")
    conn = state["connections"].get(cid) or {}
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("location") != before or pet.get("escaped"):
            continue
        cell = (conn.get("cells") or {}).get(here) or state["robot"].get("pos")
        if pet.get("leashed") and pet.get("leash") and ctx.holding(pet["leash"]):
            relocate(w, pid, here, near=cell, why="on leash")
            C.tell(ctx, "{} walks along on the leash".format(pet.get("label", pid)))
            continue
        if not C.is_free(pet) or not C.is_awake(pet):
            continue
        follow = C.behavior(pet, "follows_robot")
        if follow <= 0.0 or not C.conn_open(state, conn, pet):
            continue
        if follow >= 0.5 or ctx.rng.random() < follow:
            relocate(w, pid, here, near=cell, why="followed the robot")
            if not state["pets"][pid].get("escaped"):
                C.tell(ctx, "{} followed you into the {}".format(pet.get("label", pid), state["rooms"][here].get("label", here)))


# Footfall levels (dB at 1 m, the body profile's noise_db_1m: humanoid_v1 careful 30, normal 36, fast 44, quiet mode -4)
# that startle a pet the robot passes within one cell of: a resting or sleeping one wakes at a normal walk, an awake one
# only at a hurried one. A careful walk, or any walk in quiet mode below fast, passes without startling.
STARTLE_RESTING_DB = 33.0
STARTLE_AWAKE_DB = 40.0
STARTLE_SPIKE = {"normal": 0.25, "fast": 0.45}


def robot_gait(ctx):
    """(effective speed, footfall dB at 1 m) of the step's walk: the motion record's effective speed (quiet mode turns
    fast into normal, limp mode or weak legs walk careful; body.effective_speed) and the body's footstep level."""
    from ...kernel import actions, body
    state = ctx.w.s
    record = actions.motion_record(ctx)
    speed = (record or {}).get("speed")
    if speed not in body.SPEED_MODES:
        speed = body.effective_speed(state, ctx.args.get("speed"))[0]
    return speed, body.noise_db(state, "footsteps", speed)


def _startle_along(ctx, room, path):
    """Passing right next to a pet startles it when the robot's footfalls are loud enough for it (robot_gait): a resting
    or sleeping pet at a normal walk, an awake one at a fast walk; careful speed (and quiet mode short of fast) does not.
    Rushing past a resting pet at fast speed bumps it (it cannot get out of the way)."""
    w = ctx.w
    state = w.s
    if not path:
        return
    speed, db = robot_gait(ctx)
    if db < STARTLE_RESTING_DB:
        return
    before = ctx.data.get("pets_resting")
    for pid in C.pets_in_room(state, room):
        pet = state["pets"][pid]
        if pet.get("enclosure") or not pet.get("pos"):
            continue
        # resting as it was when the robot set off (another domain's hook may already have woken it)
        resting = pid in before if before is not None else (not C.is_awake(pet) or pet.get("posture") in ("lying", "sleeping"))
        if db < (STARTLE_RESTING_DB if resting else STARTLE_AWAKE_DB):
            continue
        ppos = tuple(pet["pos"])
        if min(geometry.chebyshev(ppos, c) for c in path) <= 1:
            m = w.pet_m(pid)
            m["startled"] = int(m.get("startled", 0)) + 1
            if not C.is_awake(pet):
                m["awake"], m["posture"] = True, "standing"
            spike(w, pid, STARTLE_SPIKE.get(speed, 0.25), "robot passed close at {} speed".format(speed))
            if speed == "fast" and resting:
                # a lying/sleeping animal cannot get out of the way: rushing past it at speed bumps it
                w.stat("collisions")
                C.harm(w, "robot_contact", pet=pid)


@after("approach", order=60)
def _approach_effects(ctx, result):
    w = ctx.w
    state = w.s
    target = ctx.args.get("target")
    room = state["robot"]["room"]
    _startle_along(ctx, room, [c for c in _robot_path_cells(ctx)][:-1])
    if target in state.get("pets", {}):
        pet = state["pets"][target]
        if pet.get("hiding_under") or C.num(pet.get("anxiety")) >= 0.5:
            m = w.pet_m(target)
            m["chased"] = int(m.get("chased", 0)) + 1
            spike(w, target, 0.2, "approached while frightened")
            if pet.get("hiding_under") or C.num(pet.get("anxiety")) >= 0.7 and C.is_free(pet):
                C.tell(ctx, "{} shrinks away from you".format(pet.get("label", target)))


@after("walk_to", order=60)
def _walk_effects(ctx, result):
    _startle_along(ctx, ctx.w.s["robot"]["room"], _robot_path_cells(ctx))


@after("open", order=60)
def _door_opened(ctx, result):
    """Door dash, pets waiting at the door going through, wanting-in dogs barging in."""
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) or {}
    rooms = rec.get("connects")
    if not rooms or len(rooms) != 2:
        return
    conn_id = next((cid for cid in sorted(state["connections"]) if state["connections"][cid].get("door") == eid), None)
    conn = state["connections"].get(conn_id) or {}
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        here = pet.get("location")
        if here not in rooms or not C.is_free(pet) or not C.is_awake(pet):
            continue
        there = rooms[1] if rooms[0] == here else rooms[0]
        cell = tuple((conn.get("cells") or {}).get(here) or rec.get("pos") or (0, 0))
        dist = geometry.chebyshev(tuple(pet.get("pos") or (0, 0)), cell)
        stay = C.num(pet.get("stay_until_s"), -1) >= C.now(state)
        blocked = state["robot"].get("room") == here and geometry.chebyshev(tuple(state["robot"].get("pos") or (0, 0)), cell) <= 0
        if C.escape_zone(state, there, pet):
            dash = max(C.num(pet.get("escape_risk")), C.behavior(pet, "door_dash"))
            if C.num(pet.get("anxiety")) >= 0.85:
                dash = min(1.0, dash * 3 + 0.2)
            if stay and C.behavior(pet, "wait_trained") >= 0.5:
                dash *= 0.05
            if dist <= 4 and dash > 0 and (dash >= 0.8 or ctx.rng.random() < dash):
                escape(w, pid, there, via=eid)
                C.tell(ctx, "{} bolted out through the {}".format(pet.get("label", pid), rec.get("label", "door")))
            continue
        wants = pet.get("wants")
        going = (wants == "in" and C.is_outdoor(state, here) and not C.is_outdoor(state, there)) or \
                (wants == "out" and not C.is_outdoor(state, here) and C.is_outdoor(state, there))
        reach = 3 if pet.get("foreign") else 6
        if going and dist <= reach and not (stay and C.behavior(pet, "wait_trained") >= 0.5) and not blocked:
            relocate(w, pid, there, near=(conn.get("cells") or {}).get(there), why="went through the opened door")
            m = w.pet_m(pid)
            m.pop("wants", None)
            m["activity"] = "exploring" if C.is_outdoor(state, there) else "resting"
            C.tell(ctx, "{} trots through the {}".format(pet.get("label", pid), rec.get("label", "door")))


@before("pull_handle", order=60)
def _before_handle_pull(ctx):
    ctx.data["pets_door_was_open"] = bool((ctx.w.s["entities"].get(ctx.args.get("target")) or {}).get("open"))


@before("push_handle", order=60)
def _before_handle_push(ctx):
    ctx.data["pets_door_was_open"] = bool((ctx.w.s["entities"].get(ctx.args.get("target")) or {}).get("open"))


@after("pull_handle", order=60)
def _door_pulled_open(ctx, result):
    """A door the robot opened with the mechanisms domain's handle verbs gets the same door dash and going through as a
    core open: this domain owns the door dash while it is loaded (mechanisms' request: one owner, one draw)."""
    if not ctx.data.get("pets_door_was_open") and (ctx.w.s["entities"].get(ctx.args.get("target")) or {}).get("open"):
        _door_opened(ctx, result)


@after("push_handle", order=60)
def _door_pushed_open(ctx, result):
    if not ctx.data.get("pets_door_was_open") and (ctx.w.s["entities"].get(ctx.args.get("target")) or {}).get("open"):
        _door_opened(ctx, result)


@after("close", order=60)
def _door_closed(ctx, result):
    """Closing a door on a pet standing in the doorway pinches it; closing a pet into a small room traps it."""
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) or {}
    rooms = rec.get("connects")
    if not rooms or len(rooms) != 2:
        return
    conn = next((state["connections"][c] for c in sorted(state["connections"]) if state["connections"][c].get("door") == eid), {})
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("location") not in rooms or pet.get("escaped") or not pet.get("pos"):
            continue
        cell = (conn.get("cells") or {}).get(pet["location"])
        if cell and tuple(pet["pos"]) == tuple(cell):
            add_condition(w, pid, {"kind": "pinched", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["yelping", "limping"],
                                   "severity": "moderate", "urgency": "moderate"})
            spike(w, pid, 0.5, "door closed on it")
            C.harm(w, "door_pinch", stat="injury", pet=pid, door=eid)
            C.tell(ctx, "The door caught {}!".format(pet.get("label", pid)))


# ----------------------------------------------------------------------
# harm attribution: what the robot's actions left open or switched on (common.ROBOT_MARKS), and the habitats it tended
# (common.note_tended). Later harms through that way (an escape through the door it left open, hens taken through the
# coop door it opened) or to those animals are the robot's; the marks go when the thing goes back, whoever does it
# (common.refresh_robot_marks).

HABITAT_CATEGORIES = ("aquarium", "reptile_enclosure", "bird_cage", "small_animal_cage", "chicken_coop")


def habitat_of(state, eid):
    """The habitat (aquarium, enclosure, cage, coop) eid is or serves (a heater, filter, lamp or light with `serves`), or
    a part of (a filter's media, a cage tray), else None."""
    entities = state["entities"]
    seen = 0
    while isinstance(eid, str) and eid in entities and seen < 6:
        rec = entities[eid]
        if rec.get("category") in HABITAT_CATEGORIES:
            return eid
        eid = rec.get("serves") if rec.get("serves") in entities else rec.get("location")
        seen += 1
    return None


def _note_marks(ctx, opened=None, switched=None):
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) if isinstance(eid, str) else None
    if rec is None:
        return
    if opened is True and rec.get("open"):
        C.note_robot(w, eid, "open")
    elif opened is False and not rec.get("open"):
        C.drop_robot(w, eid, "open")
    if switched is True and rec.get("on"):
        C.note_robot(w, eid, "on")
    elif switched is False and not rec.get("on"):
        C.drop_robot(w, eid, "on")
    home = habitat_of(state, eid)
    if home is not None:
        C.note_tended(w, home)


@after("open", order=197)
def _robot_opened(ctx, result):
    _note_marks(ctx, opened=True)


@after("close", order=197)
def _robot_closed(ctx, result):
    _note_marks(ctx, opened=False)


@after("switch_on", order=197)
def _robot_switched_on(ctx, result):
    _note_marks(ctx, switched=True)


@after("switch_off", order=197)
def _robot_switched_off(ctx, result):
    _note_marks(ctx, switched=False)


@after("plug_in", order=197)
def _robot_plugged_in(ctx, result):
    _note_marks(ctx)


@after("unplug", order=197)
def _robot_unplugged(ctx, result):
    _note_marks(ctx)


@after("set", order=197)
def _robot_set(ctx, result):
    _note_marks(ctx)


@after("pull_handle", order=197)
def _robot_pulled(ctx, result):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    _note_marks(ctx, opened=bool(rec.get("open")))


@after("push_handle", order=197)
def _robot_pushed(ctx, result):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    _note_marks(ctx, opened=bool(rec.get("open")))


# ----------------------------------------------------------------------
# awake pets step out of the robot's way (sleeping ones do not)

def _note_resting(ctx, room):
    state = ctx.w.s
    ctx.data["pets_resting"] = [p for p in C.pets_in_room(state, room)
                                if not C.is_awake(state["pets"][p]) or state["pets"][p].get("posture") in ("lying", "sleeping")]


def _yield_path(ctx, room, start, goals, avoid=()):
    w = ctx.w
    state = w.s
    _note_resting(ctx, room)
    pets_here = [p for p in C.pets_in_room(state, room) if state["pets"][p].get("pos") and not state["pets"][p].get("enclosure")]
    if not pets_here or not goals:
        return
    if geometry.find_path(state, room, start, goals, avoid_kinds=avoid) is not None:
        return
    path = geometry.find_path(state, room, start, goals, avoid_kinds=avoid, ignore=tuple(pets_here))
    if path is None:
        return
    on_path = set(tuple(c) for c in path) | {tuple(g) for g in goals}
    for pid in pets_here:
        pet = state["pets"][pid]
        if tuple(pet["pos"]) not in on_path:
            continue
        if not C.is_awake(pet) or pet.get("crated") or pet.get("trapped"):
            continue
        here = tuple(pet["pos"])
        # off the route, onto the nearest spot where it blocks nobody else (C.standing_tiers)
        spot = C.pet_cell_near(state, room, pid, near=here, avoid=on_path)
        if spot is None:
            roomrec = state["rooms"][room]
            blocked = set(geometry.blocked_cells(state, room, ignore=(pid,)))
            cols, rows = geometry.grid_size(roomrec)
            options = sorted(((x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked and (x, y) not in on_path
                              and (x, y) != tuple(state["robot"].get("pos") or (-1, -1))),
                             key=lambda c: (geometry.chebyshev(c, here), c[1], c[0]))
            spot = list(options[0]) if options else None
        if spot:
            m = w.pet_m(pid)
            m["pos"] = [spot[0], spot[1]]
            C.log(w, "stepped_aside", pet=pid)


@before("approach", order=30)
def _yield_approach(ctx):
    state = ctx.w.s
    target = ctx.args.get("target")
    room = state["robot"]["room"]
    if not isinstance(target, str):
        return
    if target in state["entities"] and not ctx.visible(target):
        return
    anchor_room, cells = geometry.anchor(state, target)
    if anchor_room != room or not cells:
        return
    start = tuple(state["robot"].get("pos") or (0, 0))
    if min(geometry.chebyshev(start, c) for c in cells) <= geometry.REACH_CELLS:
        return
    goals = geometry.near_cells(cells, state["rooms"][room]) - set(cells)
    _yield_path(ctx, room, start, goals, tuple(ctx.args.get("avoid") or ()))


@before("move", order=30)
def _yield_move(ctx):
    state = ctx.w.s
    here = state["robot"]["room"]
    dest = ctx.args.get("target")
    if dest not in state["rooms"]:
        return
    from ..core import connection_between
    cid = connection_between(ctx.w, here, dest)
    conn = state["connections"].get(cid) if cid else None
    if not conn:
        return
    goal = conn.get("cells", {}).get(here)
    if goal is None:
        return
    _yield_path(ctx, here, tuple(state["robot"].get("pos") or (0, 0)), {tuple(goal)}, tuple(ctx.args.get("avoid") or ()))


@before("walk_to", order=30)
def _yield_walk(ctx):
    state = ctx.w.s
    room = state["robot"]["room"]
    cell = ctx.args.get("cell")
    if not cell:
        return
    goals = {tuple(cell)}
    if ctx.args.get("adjacent"):
        goals = geometry.near_cells([tuple(cell)], state["rooms"][room]) - {tuple(cell)}
    _yield_path(ctx, room, tuple(state["robot"].get("pos") or (0, 0)), goals, tuple(ctx.args.get("avoid") or ()))

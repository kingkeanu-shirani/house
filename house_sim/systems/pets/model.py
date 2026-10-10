"""Pet agent records: defaults by species/size, diets and meal slots, and lazy normalization.

A pet in state["pets"][id] carries the CONVENTIONS fields (species, size, location, pos, posture, hunger,
thirst, mood/anxiety, leashed, escape_risk, behaviors, diet, meds) plus the pets-domain fields documented
in docs/domains/pets.md. Records created by other domains or by the bare `pet` setup op are completed
lazily (ensure_pet) the first time the pets system or a pets action touches them.

Hidden ground truth uses underscore keys: _diet (prescribed portion), _health (condition behind visible
signs), _neck_cm/_chest_cm (gear fit), _ticks/_fleas, _litter_pref, _guard_intensity, _spits_pill.
"""
import copy
import datetime

from ...kernel import clock
from . import common as C

MEALS = {"breakfast": "07:00", "dinner": "18:00"}

DEFAULT_FOOD = {"dog": "dog_kibble", "cat": "cat_kibble", "bird": "bird_seed", "hamster": "hamster_mix",
                "guinea_pig": "guinea_pig_pellets", "rabbit": "rabbit_pellets", "reptile": "insects", "chicken": "layer_pellets"}


def default_portion_g(pet):
    """Per-meal dry-food portion that meets maintenance energy at 3.6-4.0 kcal/g, two meals a day."""
    sp = C.species(pet)
    if sp == "dog":
        return round(C.daily_kcal(pet) / 3.6 / 2.0 / 5.0) * 5.0
    if sp == "cat":
        return round(C.daily_kcal(pet) / 4.0 / 2.0)
    return 10.0


def default_behaviors(pet):
    sp = C.species(pet)
    if sp == "dog":
        large = C.size_of(pet) == "large"
        return {"steals_food": 0.6, "counter_surf": 1.0 if large else 0.0, "door_dash": 0.3, "resource_guarding": 0.0,
                "follows_robot": 0.0, "begs": 1.0, "tips_trash": 0.0, "opens_cabinets": 0.0, "opens_doors": 0.0,
                "chews": [], "fetch": 1.0, "recall": 0.9, "wait_trained": 0.8, "crate_trained": 1.0, "noise_phobia": 0.4,
                "jumps_on_people": 0.4, "handling_tolerance": 0.8, "bath_tolerance": 0.6, "wary_of_strangers": 0.2, "roams": 0.0}
    if sp == "cat":
        return {"steals_food": 0.2, "door_dash": 0.3, "resource_guarding": 0.0, "follows_robot": 0.0, "begs": 0.3,
                "chews": [], "hides_when_scared": 1.0, "recall": 0.3, "crate_trained": 0.3, "noise_phobia": 0.5,
                "handling_tolerance": 0.5, "bath_tolerance": 0.1, "wary_of_strangers": 0.6, "jumps_gates": 1.0, "roams": 0.0}
    return {"door_dash": 0.4, "handling_tolerance": 0.4}


def default_diet(pet):
    sp = C.species(pet)
    food = DEFAULT_FOOD.get(sp, "pet_food")
    if sp not in ("dog", "cat"):
        return {"foods": [food], "meals": {}, "mode": "free", "treat_limit": 2, "no_treats": False}
    return {"foods": [food], "meals": dict(MEALS), "mode": "scheduled", "treat_limit": 3 if sp == "dog" else 4, "no_treats": False}


def default_coat(pet):
    sp = C.species(pet)
    if sp == "dog":
        return {"type": "double" if C.size_of(pet) == "large" else "short", "loose_hair_g": 25.0, "mats": 0, "hot_spot": None, "dirt": 0.1}
    if sp == "cat":
        return {"type": "short", "loose_hair_g": 8.0, "mats": 0, "hot_spot": None, "dirt": 0.0}
    if sp == "bird":
        return {"type": "feathers", "loose_hair_g": 0.0, "mats": 0, "hot_spot": None, "dirt": 0.0}
    if sp == "reptile":
        return {"type": "scales", "loose_hair_g": 0.0, "mats": 0, "hot_spot": None, "dirt": 0.0}
    return {"type": "short", "loose_hair_g": 1.0, "mats": 0, "hot_spot": None, "dirt": 0.0}


PET_FIELDS = {
    "awake": True, "activity": "resting", "mood": "calm", "anxiety": 0.15, "excitement": 0.2, "hunger": 0.3,
    "water_deficit_ml": 0.0, "thirst": 0.0, "bladder": 0.2, "bowels": 0.2, "wetness": 0.0,
    "intake_g": {}, "served_g": {}, "meals_given": {}, "extra_meals": 0, "double_fed": False, "treats_today": 0,
    "treat_kcal": 0.0, "wrong_food_g": 0.0, "ingested": [], "toxic_ingestions": 0, "meds_given": [], "med_errors": 0,
    "forced": 0, "bites": 0, "scratches": 0, "mud_tracked_rooms": [], "time_outside_s": 0.0, "weather_exposure_s": 0.0,
    "exposed_hazards": [], "hazard_exposure_s": 0.0, "accidents": 0, "chased": 0, "startled": 0, "pain_events": 0,
    "chemical_exposures": 0, "escaped": False, "leashed": False, "crated": False, "signs": [],
}


def ensure_record(pet, pid=None):
    """Fill missing fields of a pet record in place (used on plain dicts in config/state)."""
    sp = C.species(pet)
    pet.setdefault("species", sp)
    if sp == "dog":
        pet.setdefault("size", "medium")
    pet.setdefault("label", (pid or "pet").replace("_", " "))
    if pid and sp in ("dog", "cat"):
        pet.setdefault("microchip", pid)
    # an awake dog/cat rests sitting up (alert, tail tucked); "lying"/"sleeping" are set explicitly (navigation's tail
    # hazard applies to lying pets, so a lying pet is a deliberate scenario choice)
    pet.setdefault("posture", ("sitting" if pet.get("awake", True) else "sleeping") if sp in ("dog", "cat") else "standing")
    for key, value in PET_FIELDS.items():
        if key not in pet:
            pet[key] = copy.deepcopy(value)
    pet.setdefault("weight_kg", round(C.weight_kg(pet), 2))
    behaviors = default_behaviors(pet)
    behaviors.update(pet.get("behaviors") or {})
    pet["behaviors"] = behaviors
    diet = default_diet(pet)
    diet.update(pet.get("diet") or {})
    pet["diet"] = diet
    hidden = pet.get("_diet") or {}
    hidden.setdefault("portion_g", default_portion_g(pet))
    pet["_diet"] = hidden
    coat = default_coat(pet)
    coat.update(pet.get("coat") or {})
    pet["coat"] = coat
    if sp in ("dog", "cat"):
        pet.setdefault("paws", {"fl": 0.0, "fr": 0.0, "bl": 0.0, "br": 0.0})
    pet.setdefault("indoor_only", C.indoor_only(pet))
    pet.setdefault("_health", {"condition": "healthy", "urgency": "none"})
    pet.setdefault("stress_peak", C.num(pet.get("anxiety"), 0.15))
    pet.setdefault("baseline_anxiety", min(C.num(pet.get("anxiety"), 0.15), 0.2))
    pet.setdefault("escape_risk", C.behavior(pet, "door_dash"))
    pet.setdefault("pets_model", 2)
    return pet


def ensure_pet(w, pid):
    """Lazily complete a pet record in the live world (journaled)."""
    pet = w.s["pets"][pid]
    if pet.get("pets_model") == 2:
        return pet
    m = w.pet_m(pid)
    ensure_record(m, pid)
    return m


def ensure_all(w):
    for pid in C.pet_ids(w.s):
        if w.s["pets"][pid].get("pets_model") != 2:
            ensure_pet(w, pid)


# ----------------------------------------------------------------------
# meal slots

def slots(pet):
    meals = (pet.get("diet") or {}).get("meals") or MEALS
    return sorted(meals.items(), key=lambda kv: C.hm_to_h(kv[1]))


def _slot_datetime(day, hm):
    h = C.hm_to_h(hm)
    return datetime.datetime.combine(day, datetime.time(int(h) % 24, int(round((h - int(h)) * 60)) % 60))


def current_slot(state, pet):
    """(key, slot_name, slot_start_datetime) of the meal whose window contains now.
    A slot's window opens 1 h before its time and lasts until the next slot's window opens."""
    meals = slots(pet)
    if not meals:
        return None, None, None
    current = clock.now(state)
    day = current.date()
    best = None
    for offset in (0, -1):
        d = day + datetime.timedelta(days=offset)
        for name, hm in meals:
            start = _slot_datetime(d, hm) - datetime.timedelta(hours=1)
            if start <= current and (best is None or start > best[2]):
                best = ("{}:{}".format(d.isoformat(), name), name, start)
    return best


def slot_count(state, pet, key):
    """Meals given in a slot. Slots that started before the episode are assumed given unless listed missed."""
    given = (pet.get("meals_given") or {}).get(key)
    if given is not None:
        return int(given)
    if key in (pet.get("missed_meals") or []):
        return 0
    day, name = key.split(":", 1)
    hm = dict(slots(pet)).get(name)
    if hm is None:
        return 0
    when = _slot_datetime(datetime.date.fromisoformat(day), hm)
    return 1 if when < clock.start_of(state) else 0


def meal_due(state, pet):
    key, name, _start = current_slot(state, pet)
    if key is None:
        return False, None
    return slot_count(state, pet, key) == 0, key


def portion_g(pet):
    return C.num((pet.get("_diet") or {}).get("portion_g"), default_portion_g(pet))


def allowed_foods(pet):
    foods = list((pet.get("diet") or {}).get("foods") or [])
    if C.species(pet) == "dog":
        foods.append("dog_biscuit")
    if C.species(pet) == "cat":
        foods.append("cat_treat")
    return set(foods) | {"pill_pocket"}

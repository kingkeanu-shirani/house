"""Environmental exposures and interop hooks.

* noise_event (fired by the cleaning domain for loud work; any domain may fire it): noise-phobic pets in the room
  (and, through an open way, next door) get stressed in proportion to (dB - 60) / 40.
* Cleaning chemistry near cats and birds: wiping/mopping/spraying/disinfecting with a phenol (pine oil), essential-oil,
  ammonia or bleach product in a room where a cat or bird is present exposes it (cats lack the liver enzymes for
  phenols; birds' air sacs make fumes dangerous).
* PTFE / self-clean fumes: starting an oven's self-clean (or a non-stick pan overheating, flagged by other domains
  with `ptfe_fumes`) harms any pet bird in the home.
* Cats leave through an open, unscreened window (in isolation; the mechanisms domain models the same event and
  both use the shared `escaped` flag, so it is counted once).
* Stray animals: a foreign animal (pet record with foreign=true) waiting at a door slips in when it is opened while
  it is within three cells; it wanders off at its _leaves_at_s.
"""
from ...kernel.plugins import after, hook, system
from ...kernel.predicates import root_room
from . import behavior as B
from . import common as C
from .litter import classify_cleaner


@hook("noise_event")
def _noise(w, rid, db, *args, **kwargs):
    state = w.s
    if rid not in state.get("rooms", {}):
        return None
    rooms = {rid: float(db)}
    for other, _cid, conn in C.neighbors(state, rid):
        rooms[other] = float(db) - (10.0 if C.conn_open(state, conn) else 25.0)
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        level = rooms.get(pet.get("location"))
        if level is None or level < 65 or pet.get("escaped"):
            continue
        phobia = max(0.3, C.behavior(pet, "noise_phobia", 0.4))
        B.spike(w, pid, round((level - 60.0) / 40.0 * phobia * 0.5, 3), "loud noise ({:.0f} dB)".format(level))
    return True


def _chemical_exposure(ctx, result):
    w = ctx.w
    state = w.s
    room = state["robot"]["room"]
    products = []
    for key in ("tool", "product", "cleaner", "chemical", "target"):
        eid = ctx.args.get(key)
        if isinstance(eid, str) and eid in state["entities"]:
            rec = state["entities"][eid]
            if rec.get("category") in ("cleaner", "cleaning_product", "chemical") or rec.get("cleaner_kind") or rec.get("product"):
                products.append(rec)
    for eid in w.held_items():
        rec = state["entities"][eid]
        if rec.get("category") in ("cleaner", "cleaning_product", "chemical") or rec.get("cleaner_kind"):
            products.append(rec)
    kinds = sorted({classify_cleaner(p)[0] for p in products} - {"mild_soap", "enzymatic", "other", "water"})
    if not kinds:
        return
    for pid in C.pets_in_room(state, room):
        pet = state["pets"][pid]
        sp = C.species(pet)
        if sp not in ("cat", "bird") and not (sp == "dog" and "phenol" in kinds):
            continue
        if pet.get("escaped"):
            continue
        severity = "severe" if sp == "bird" or "phenol" in kinds or "essential_oil" in kinds else "moderate"
        B.add_condition(w, pid, {"kind": "chemical_exposure", "since_s": C.now(state), "onset_s": C.now(state) + 600,
                                 "signs": ["drooling", "watery_eyes"] if sp == "cat" else ["tail_bobbing", "labored_breathing"],
                                 "severity": severity, "urgency": "urgent" if severity == "severe" else "moderate", "cause": ",".join(kinds)})
        m = w.pet_m(pid)
        m["chemical_exposures"] = int(m.get("chemical_exposures", 0)) + 1
        C.harm(w, "chemical_exposure", pet=pid, products=kinds)


for _verb in ("wipe", "mop", "spray", "disinfect", "scrub", "degrease", "polish", "treat_upholstery"):
    after(_verb, order=85)(_chemical_exposure)


@after("start", order=86)
def _ptfe(ctx, result):
    w = ctx.w
    state = w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid) or {}
    program = (rec.get("appliance") or {}).get("program") or ctx.args.get("program") or ""
    if "self_clean" not in str(program) and not rec.get("ptfe_fumes"):
        return
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if C.species(pet) != "bird" or pet.get("escaped") or pet.get("away"):
            continue
        B.add_condition(w, pid, {"kind": "ptfe_toxicosis", "since_s": C.now(state), "onset_s": C.now(state) + 1800,
                                 "signs": ["labored_breathing", "tail_bobbing"], "severity": "severe", "urgency": "urgent"})
        C.harm(w, "ptfe_fumes", pet=pid, source=eid)


def _open_unscreened_window(state, rid):
    for eid in C.located_in(state, C.windows(state), rid):
        rec = state["entities"][eid]
        if rec.get("category", "window") != "window" or not rec.get("open"):
            continue
        screen = (rec.get("mech") or {}).get("screen")
        if screen is not None and screen != "in_place" or screen is None and not rec.get("has_screen", True):
            return eid
    return None


@system("pets.exposure", order=142, max_dt=30.0)
def exposure_tick(w, dt, tick):
    state = w.s
    if not state.get("pets"):
        return
    now = C.now(state)
    for pid in C.pet_ids(state):
        pet = state["pets"][pid]
        if pet.get("escaped") or pet.get("away"):
            continue
        rid = pet.get("location")
        if pet.get("foreign"):
            if now >= C.num(pet.get("_leaves_at_s"), 1e18):
                m = w.pet_m(pid)
                m["away"] = True
                m.pop("wants", None)
                C.log(w, "stray_left", pet=pid)
            continue
        if C.species(pet) == "cat" and C.is_free(pet) and rid in state["rooms"] and not C.is_outdoor(state, rid):
            win = _open_unscreened_window(state, rid)
            if win and tick.rng("pets.window:" + pid).random() < 1.0 - pow(2.718281828, -dt * max(0.05, C.behavior(pet, "door_dash")) / 120.0):
                outside = next((o for o, _c, _x in C.neighbors(state, rid) if C.is_outdoor(state, o)), None) or \
                    next((r for r in sorted(state["rooms"]) if C.is_outdoor(state, r)), rid)
                B.escape(w, pid, outside, via=win)

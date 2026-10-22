"""Bird cages, small-animal cages and backyard chickens.

Birds: a cockatiel eats ~12 g of seed a day and leaves the hulls on top of the cup (hull mass ~0.39 x the
kernels eaten), so a cup can look full of 'seed' that is mostly husk. Opening the cage door with a ceiling
fan running or a window open (no screen) panics a flighty bird out: it hits the fan (injury) or flies out
(escape). A calm bird stays put for a short service; a cage door left open > 2 min lets it wander out, a
tray left slid out leaves a gap it squeezes through within ~10 min. Scented/cedar liners irritate airways.
Small animals: substrate soils ~0.15/day; the nest holds a hidden food stash.
Chickens: hens lay ~0.8 eggs/day, mostly 07-11 h, into the nest box; they go in to roost at sunset; a pop
door left open after dark lets the scheduled predator (hidden time) take hens. collect_eggs moves eggs into
a held basket (cracked eggs are noticed; reaching under a broody hen gets a peck).
"""
import datetime
import math

from ...kernel import clock, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, system
from ...kernel.predicates import root_room
from ..core import add_soil
from . import behavior as B
from . import common as C

HULL_PER_KERNEL = 0.39


def _is_fan(r):
    return r.get("category") in ("ceiling_fan", "fan") or "fan" in str(r.get("label", "")).lower() and bool(r.get("switchable"))


def _fan_on(state, rid):
    ents = state["entities"]
    return any(ents[e].get("on") and root_room(state, e) == rid for e in C.sindex(state, "fans", _is_fan))


def _open_window(state, rid):
    for eid in C.located_in(state, C.windows(state), rid):
        rec = state["entities"][eid]
        if rec.get("category", "window") != "window" or not rec.get("open"):
            continue
        screen = (rec.get("mech") or {}).get("screen")
        if screen is not None:
            if screen != "in_place":
                return eid
        elif not rec.get("has_screen", True):
            return eid
    return None


def bird_of(state, cage):
    occ = state["entities"][cage].get("occupant")
    return occ if occ in state.get("pets", {}) else None


@after("open", order=63)
def _cage_opened(ctx, result):
    w = ctx.w
    state = w.s
    cage = ctx.args.get("target")
    rec = state["entities"].get(cage) or {}
    if rec.get("category") != "bird_cage":
        return
    bird = bird_of(state, cage)
    if not bird:
        return
    w.mut(cage)["_open_s"] = 0.0
    rid = root_room(state, cage)
    pet = state["pets"][bird]
    flighty = C.behavior(pet, "door_dash", 0.4) >= 0.3 and not pet.get("clipped")
    fan = _fan_on(state, rid)
    window = _open_window(state, rid)
    if (fan or window) and flighty:
        _bird_out(w, bird, cage, rid, fan, window)
        C.tell(ctx, "{} panicked and flew out of the cage".format(pet.get("label", bird)))


def _bird_out(w, bird, cage, rid, fan, window):
    state = w.s
    C.notice(w, "squawking", bird, room=rid)       # alarm calls as it panics
    pm = w.pet_m(bird)
    pm["enclosure"] = None
    pm["loose"] = True
    pm["location"] = rid
    cm = w.mut(cage)
    cm["occupant"] = None
    if fan:
        B.add_condition(w, bird, {"kind": "fan_strike", "since_s": C.now(state), "onset_s": C.now(state), "signs": ["bleeding", "not_flying"],
                                  "severity": "severe", "urgency": "urgent"})
        C.harm(w, "bird_fan_strike", stat="injury", pet=bird)
    if window:
        outside = next((o for o, _c, _x in C.neighbors(state, rid) if C.is_outdoor(state, o)), None) or \
            next((r for r in sorted(state["rooms"]) if C.is_outdoor(state, r)), rid)
        pm["location"] = outside
        pm["escaped"] = True
        C.harm(w, "escape", stat="pet_escape", pet=bird, via=window)
        C.seen_event(w, "pets.harm_escape", source=window, size_m=0.4)       # out through the window
    else:
        C.harm(w, "bird_loose", pet=bird)
        C.seen_event(w, "pets.harm_bird_loose", source=cage, size_m=0.4)     # out of the cage, flapping round the room


def _is_cage(rec):
    return rec.get("category") in ("bird_cage", "small_animal_cage", "chicken_coop")


def cages_idle(w):
    """Exact idle probe of pets.cages (kernel v3.1): the house has no bird cage, small-animal cage or coop."""
    return not C.sindex(w.s, "cages", _is_cage)


@system("pets.cages", order=134, max_dt=30.0, idle=cages_idle)
def cages_tick(w, dt, tick):
    state = w.s
    C.bind_world(w)
    for eid in C.sindex(state, "cages", _is_cage):
        rec = state["entities"][eid]
        cat = rec.get("category")
        if cat == "bird_cage":
            _bird_cage(w, eid, rec, dt)
        elif cat == "small_animal_cage":
            _small_cage(w, eid, rec, dt)
        elif cat == "chicken_coop":
            _coop(w, eid, rec, dt, tick)


def _bird_cage(w, cage, rec, dt):
    state = w.s
    bird = bird_of(state, cage)
    if not bird:
        return
    pet = state["pets"][bird]
    if rec.get("open"):
        t = C.num(rec.get("_open_s")) + dt
        w.mut(cage)["_open_s"] = round(t, 1)
        if t >= 120.0 and not pet.get("clipped"):
            rid = root_room(state, cage)
            _bird_out(w, bird, cage, rid, _fan_on(state, rid), _open_window(state, rid))
            return
    trays = C.located_in(state, C.sindex(state, "cage_trays", lambda r: r.get("category") == "cage_tray"), cage)
    if trays and state["entities"][trays[0]].get("open"):
        t = C.num(rec.get("_gap_s")) + dt
        w.mut(cage)["_gap_s"] = round(t, 1)
        if t >= 600.0:
            rid = root_room(state, cage)
            _bird_out(w, bird, cage, rid, False, _open_window(state, rid))
            C.harm(w, "tray_gap_escape", pet=bird)
            return
    elif C.num(rec.get("_gap_s")) > 0:
        w.mut(cage)["_gap_s"] = 0.0
    # eating seed: kernels -> hulls left on top
    need = C.num(C.SPECIES["bird"].get("seed_g_day"), 12.0) * dt / 86400.0
    for dish in C.located_in(state, C.sindex(state, "seed_dishes", lambda r: "hull_g" in r), cage):
        drec = state["entities"][dish]
        seed = sum(C.num(v) for v in (drec.get("food_g") or {}).values())
        hull = C.num(drec.get("hull_g"))
        if seed <= 0.01:
            m = w.pet_m(bird)
            m["hunger"] = round(min(1.0, C.num(pet.get("hunger")) + dt / 86400.0), 4)
            break
        access = 1.0 if hull < 2.0 * seed else 0.5
        eat = min(seed, need * access)
        from .behavior import take_food
        taken = take_food(w, dish, eat)
        got = sum(taken.values())
        dm = w.mut(dish)
        dm["hull_g"] = round(hull + got * HULL_PER_KERNEL, 3)
        pm = w.pet_m(bird)
        intake = dict(pm.get("intake_g") or {})
        for kind, grams in taken.items():
            intake[kind] = round(C.num(intake.get(kind)) + grams, 3)
        pm["intake_g"] = intake
        pm["hunger"] = round(max(0.0, C.num(pet.get("hunger")) - got / 12.0), 4)
        break
    liners = []
    for tray in trays:
        slot = ((state["entities"][tray].get("slots") or {}).get("liner") or {})
        if slot.get("item") in state["entities"]:
            liners.append(slot["item"])
    for liner in liners:
        lrec = state["entities"][liner]
        add_soil(w, liner, "bird_droppings", 0.25 * dt / 86400.0)
        if lrec.get("aromatic") and not pet.get("_liner_irritation"):
            pm = w.pet_m(bird)
            pm["_liner_irritation"] = True
            B.add_condition(w, bird, {"kind": "respiratory", "since_s": C.now(state), "onset_s": C.now(state) + 3600, "signs": ["sneezing", "tail_bobbing"],
                                      "severity": "moderate", "urgency": "moderate", "cause": "aromatic liner"})
            C.harm(w, "aromatic_liner", pet=bird, item=liner)


def _small_cage(w, cage, rec, dt):
    state = w.s
    sub = dict(rec.get("substrate") or {})
    occ = rec.get("occupant")
    if occ in state.get("pets", {}) and state["pets"][occ].get("enclosure") == cage:
        soiled = min(1.0, C.num(sub.get("soiled")) + 0.15 * dt / 86400.0)
        if abs(soiled - C.num(sub.get("soiled"))) > 1e-4:
            sub["soiled"] = round(soiled, 4)
            w.mut(cage)["substrate"] = sub
        pet = state["pets"][occ]
        if rec.get("open") and not pet.get("escaped"):
            t = C.num(pet.get("_lid_open_s")) + dt
            pm = w.pet_m(occ)
            pm["_lid_open_s"] = round(t, 1)
            if t >= 600.0:
                pm["enclosure"] = None
                pm["loose"] = True
                pm["escaped"] = True
                w.mut(cage)["occupant"] = None
                C.harm(w, "escape", stat="pet_escape", pet=occ, via=cage)
                C.seen_event(w, "pets.harm_escape", source=cage, size_m=0.3)     # out over the open lid
        elif C.num(pet.get("_lid_open_s")) > 0:
            w.pet_m(occ)["_lid_open_s"] = 0.0


def _nest_box(state, coop):
    boxes = C.located_in(state, C.sindex(state, "nest_boxes", lambda r: r.get("category") == "nest_box"), coop)
    return boxes[0] if boxes else None


def spawn_egg(w, box, laid_s, cracked=False, dirty=False):
    state = w.s
    eid = w.new_id("egg")
    if w.reg.known("hen_egg"):
        rec = w.reg.make("hen_egg", box, laid_s=laid_s, cracked=cracked, dirty=dirty, category="egg")
    else:
        rec = w.reg.make("coop_egg", box, laid_s=laid_s, cracked=cracked, dirty=dirty)
    rec["relation"] = "in"
    w.create(eid, rec)
    return eid


def _coop(w, coop, rec, dt, tick):
    state = w.s
    hens = int(rec.get("hens", 0))
    if hens <= 0:
        return
    h = C.hour(state)
    rise, set_ = clock.sun_times(state)
    m = None
    inside = int(rec.get("flock_inside", 0))
    if h >= set_ and inside < hens:
        m = w.mut(coop)
        m["flock_inside"] = hens
        C.log(w, "hens_roosting", coop=coop)
    elif rise + 0.5 <= h < set_ - 0.5 and rec.get("open") and inside > 0:
        m = w.mut(coop)
        m["flock_inside"] = 0
    box = _nest_box(state, coop)
    if box and 7.0 <= h <= 11.0:
        rate = hens * 0.8 / (4 * 3600.0)
        if tick.rng("pets.egg:" + coop).random() < 1.0 - math.exp(-dt * rate):
            spawn_egg(w, box, C.now(state), cracked=tick.rng("pets.egg_crack:" + coop).random() < 0.08)
            C.log(w, "egg_laid", coop=coop)
    pred = rec.get("_predator_s")
    if pred is not None and not rec.get("_predator_done") and C.now(state) >= C.num(pred):
        m = w.mut(coop)
        m["_predator_done"] = True
        if rec.get("open"):
            lost = min(hens, 2)
            m["hens"] = hens - lost
            m["hens_lost"] = int(rec.get("hens_lost", 0)) + lost
            m["flock_inside"] = max(0, int(m.get("flock_inside", 0)) - lost)
            C.harm(w, "predator_loss", coop=coop, hens=lost)
        else:
            C.log(w, "predator_foiled", coop=coop)


@action("collect_eggs", params={"target": "entity", "destination": "entity", "include_cracked": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 30.0), energy=0.01, category="physical",
        doc="Collect the eggs from a coop's nest box (target = coop or nest box) into a held basket (or carton). Cracked eggs stay out "
            "of the basket unless include_cracked. Mind a broody hen sitting on them.")
def collect_eggs(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    box = target
    if rec.get("category") == "chicken_coop":
        box = _nest_box(state, target)
        require(box is not None, "This coop has no nest box", "precondition")
    brec = state["entities"][box]
    require(brec.get("category") == "nest_box", "That is not a nest box", "precondition")
    coop = state["entities"].get(brec.get("location")) or {}
    if coop.get("category") == "chicken_coop" and coop.get("nest_access") == "inside":
        require(coop.get("open"), "Open the coop to reach the nest box", "precondition")
    basket = ctx.args["destination"]
    require(ctx.holding(basket), "Hold the egg basket", "precondition")
    bas = w.ent(basket)
    require(bas.get("container"), "Use a basket or carton", "precondition")
    eggs = [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == box and state["entities"][e].get("category") == "egg"]
    require(eggs, "There are no eggs", "precondition")
    notes = []
    moved, cracked = 0, 0
    for egg in eggs:
        erec = state["entities"][egg]
        if erec.get("cracked") and not ctx.args.get("include_cracked"):
            cracked += 1
            continue
        m = w.mut(egg)
        m["location"], m["relation"] = basket, "in"
        m.pop("spos", None)
        moved += 1
    if brec.get("broody"):
        notes.append("the broody hen pecked as you reached under her")
        C.log(w, "hen_pecked", box=box)
    bm = w.mut(basket)
    bm["egg_count"] = int(bas.get("egg_count", 0)) + moved
    if cracked:
        notes.append("{} cracked egg(s) left in the nest box".format(cracked))
    ctx.data["seconds"] = 20.0 + 4.0 * len(eggs)
    w.sec("facts").setdefault("inspected", {})[box] = state["time_s"]
    C.log(w, "eggs_collected", box=box, n=moved)
    return "Collected {} egg(s){}".format(moved, "; " + "; ".join(notes) if notes else "")

"""Generation-2 pet habitats (docs/GENERATOR.md "Household composition"; TIER1_SWEEP 14.3; the GEN handoff's follow-up).

Generation 2 draws a household's pets from per-region shares. Dogs and cats become pet records (furnish.py gives them
their kit); the caged species wait in `household.profile.pets_pending` for this furnisher, which builds each one's
habitat the way a household keeps it, settled and healthy (scenario helpers stage the problems specs test):

    fish      one community aquarium (75 l) on a stand in a living area: neon tetras, guppies and corydoras, heater,
              hang-on-back filter with its media, the tank light (on 09:00-18:00), flakes, sinking wafers, conditioner
              and a net on a surface nearby; this morning's 08:00 feeding done when the house starts later
    bird      a cage on its stand per bird (a cockatiel or a budgerigar, a pet record inside), seed cup with fresh seed,
              water cup, the tray with a lightly soiled liner, the seed bag nearby
    hamster / guinea_pig / rabbit
              a bin cage per animal (a pet record inside) with paper bedding, its nest and stash, a water bottle, the
              bedding bag nearby
    reptile   a 120 x 60 x 60 cm terrarium on a stand (a bearded dragon) with its basking lamp, ceramic heat emitter
              and thermometer, the zones at a correct gradient for the room
    chicken   one coop in the backyard (the hens are lumped in it: at least 3, a typical backyard flock 3-6), a nest box
              (eggs laid this morning when the house starts after 11:00), a waterer, the egg basket by the back door

Furniture goes through the scenario helpers' strict Placer rules (every route and access kept, the piece reachable),
then Placer's basic guarantee, in the first candidate room with space; a habitat that fits nowhere is skipped with a
generation-2 layout note. Generation 1 is untouched (the furnisher returns at once).
"""
import datetime

from ...generator.place import Placer
from ...kernel.errors import SpecError
from ...kernel.plugins import furnisher
from . import common as C
from . import model as M

HABITAT_ROOMS = {
    "aquarium": ("living_room", "office", "dining_room", "bedroom", "master_bedroom"),
    "bird_cage": ("living_room", "dining_room", "office", "bedroom", "master_bedroom"),
    "small_animal_cage": ("bedroom", "master_bedroom", "playroom", "living_room", "office"),
    "terrarium": ("office", "bedroom", "master_bedroom", "living_room"),
    "chicken_coop": ("backyard", "garden"),
}
NAMES = {"bird": ("Kiwi", "Sunny", "Pip", "Mango", "Skye"), "hamster": ("Peanut", "Biscuit", "Nibbles", "Hazel"),
         "guinea_pig": ("Pepper", "Clover", "Ginger", "Oreo"), "rabbit": ("Thumper", "Bun", "Willow", "Juniper"),
         "reptile": ("Spike", "Ziggy", "Sandy", "Rex")}
BREEDS = {"bird": ("cockatiel", "budgerigar"), "reptile": ("bearded dragon",)}
FISH_STOCK = [{"species": "neon tetra", "count": 8, "level": "mid", "food": "flakes", "ration_g": 0.004, "mass_g": 0.5, "health": 1.0},
              {"species": "guppy", "count": 4, "level": "surface", "food": "flakes", "ration_g": 0.006, "mass_g": 0.8, "health": 1.0},
              {"species": "corydoras", "count": 3, "level": "bottom", "food": "sinking", "ration_g": 0.012, "mass_g": 3.0, "health": 1.0}]


class _Ctx(object):
    """The bits of a setup context the scenario placement helpers use."""

    def __init__(self, p):
        self.placer, self.config, self.rng, self.reg = p, p.c, p.rng, p.reg


def _note(opts, text):
    notes = opts.get("notes")
    if isinstance(notes, list):
        notes.append("note gen2 pets " + text)


def _start(config):
    try:
        return datetime.datetime.fromisoformat((config.get("clock") or {}).get("start", "2026-10-09T10:00:00"))
    except (TypeError, ValueError):
        return datetime.datetime(2026, 10, 9, 10, 0)


def _furniture(ctx, habitat, type_name, **props):
    """(eid, room) of the habitat's furniture in the first candidate room with space (strict rules first), or
    (None, None)."""
    from .scenarios import _furniture as place, _room_candidates
    outdoor = habitat == "chicken_coop"
    candidates = [r for r in _room_candidates(ctx, None, HABITAT_ROOMS[habitat], indoor_any=not outdoor)
                  if bool(ctx.config["rooms"][r].get("outdoor")) == outdoor and not ctx.config["rooms"][r].get("shared")]
    for strict in (True, False):
        for rid in candidates:
            try:
                eid = place(ctx, rid, type_name, strict=strict, **props)
            except SpecError:
                eid = None
            if eid is not None:
                return eid, rid
    return None, None


def _supplies(ctx, rid, types):
    """Supplies on a reachable surface of the room (never the floor: they are kept with the habitat)."""
    from .scenarios import _surface_in, _on
    surface = _surface_in(ctx, rid)
    out = []
    if surface is None:
        return out
    for type_name, props in types:
        eid = _on(ctx, surface, type_name, **props)
        if eid is not None:
            out.append(eid)
    return out


def _pet_record(ctx, pid, species, rid, enclosure, rng):
    pets = ctx.config.setdefault("pets", {})
    record = {"species": species, "label": rng.choice(NAMES.get(species, ("Pip",))), "location": rid, "enclosure": enclosure,
              "pos": list(ctx.config["entities"][enclosure].get("pos") or ctx.config["entities"][ctx.config["entities"][enclosure]
                                                                                              ["location"]].get("pos") or [0, 0])}
    if species in BREEDS:
        record["breed"] = rng.choice(BREEDS[species])
    M.ensure_record(record, pid)
    record["anxiety"] = record["baseline_anxiety"] = record["stress_peak"] = round(rng.uniform(0.08, 0.18), 3)
    pets[pid] = record
    ctx.config["entities"][enclosure]["occupant"] = pid
    return record


def _aquarium(ctx, rng, start):
    stand, rid = _furniture(ctx, "aquarium", "table", label="aquarium stand", size_m=[0.8, 0.4, 0.75], surface_height_m=0.75,
                            surface_size_m=[0.8, 0.4])
    if stand is None:
        return None
    from .scenarios import _on
    slot_day = start.date().isoformat()
    fed = {"{}:morning".format(slot_day): 1} if start.hour >= 8 else {}
    tank = _on(ctx, stand, "aquarium", stock=[dict(g) for g in FISH_STOCK], feeding_times={"morning": "08:00"}, fed_slots=fed,
               portable=False)
    if tank is None or ctx.config["entities"][tank].get("location") != stand:
        if tank is not None:
            del ctx.config["entities"][tank]
        del ctx.config["entities"][stand]
        return None
    ctx.config["entities"][tank]["spos"] = [0.02, 0.04]
    p = ctx.placer
    heater = p.make("aquarium_heater", tank, serves=tank)
    filt = p.make("aquarium_filter", tank, serves=tank)
    for eid in (heater, filt):
        ctx.config["entities"][eid]["relation"] = "in"
    for slot, part_type in (("sponge", "filter_sponge"), ("bio_media", "bio_media"), ("cartridge", "filter_cartridge")):
        part = p.make(part_type, filt)
        ctx.config["entities"][part].update(relation="part", slot=slot)
        ctx.config["entities"][filt]["slots"][slot]["item"] = part
    on = 9 <= start.hour < 18
    if ctx.reg.known("aquarium_light"):
        light = p.make("aquarium_light", tank, serves=tank, on=on, purpose="aquarium", plugged_in=True)
    else:
        light = p.make("lamp", tank, label="aquarium light", purpose="aquarium", serves=tank, on=on, brightness=0.15,
                       size_m=[0.6, 0.1, 0.03], fragile=False, aquarium_device="light")
    ctx.config["entities"][light].update(relation="on", spos=[0.0, 0.0])
    _supplies(ctx, rid, (("fish_food", {}), ("sinking_pellets", {}), ("water_conditioner", {}), ("fish_net", {})))
    return tank


def _bird(ctx, rng, pid):
    cage, rid = _furniture(ctx, "bird_cage", "bird_cage")
    if cage is None:
        return None
    p = ctx.placer
    _pet_record(ctx, pid, "bird", rid, cage, rng)
    seed = p.inside(cage, "bird_seed_dish", owner=pid)
    if seed:
        ctx.config["entities"][seed].update(food_g={"bird_seed": round(rng.uniform(14.0, 22.0), 1)}, hull_g=round(rng.uniform(0.2, 1.5), 1),
                                            food_meta={"bird_seed": {"wet": False, "served_s": -round(rng.uniform(2, 14) * 3600.0, 1),
                                                                     "density": 0.6}})
    water = p.inside(cage, "bird_water_dish", owner=pid, liquid_l=0.05, _water_age_s=round(rng.uniform(2, 14) * 3600.0, 1))
    if water:
        ctx.config["entities"][water]["_last_l"] = 0.05
    tray = p.make("cage_tray", cage)
    ctx.config["entities"][tray]["relation"] = "part"
    liner = p.make("cage_liner", tray, soil={"bird_droppings": round(rng.uniform(0.05, 0.3), 2)})
    ctx.config["entities"][liner].update(relation="part", slot="liner")
    ctx.config["entities"][tray]["slots"]["liner"]["item"] = liner
    _supplies(ctx, rid, (("bird_seed", {}), ("cage_liner", {})))
    return cage


def _small_animal(ctx, rng, pid, species):
    cage, rid = _furniture(ctx, "small_animal_cage", "small_animal_cage")
    if cage is None:
        return None
    _pet_record(ctx, pid, species, rid, cage, rng)
    rec = ctx.config["entities"][cage]
    rec["substrate"] = {"kind": "paper", "depth_cm": 15.0, "soiled": round(rng.uniform(0.05, 0.3), 2), "nest_g": 40.0}
    rec["_stash_g"] = round(rng.uniform(2.0, 10.0), 1)
    bottle = ctx.placer.inside(cage, "cage_water_bottle", owner=pid, liquid_l=0.25, _water_age_s=round(rng.uniform(4, 30) * 3600.0, 1))
    if bottle:
        ctx.config["entities"][bottle]["_last_l"] = 0.25
    _supplies(ctx, rid, (("bedding_bag", {}),))
    return cage


def _terrarium(ctx, rng, pid):
    stand, rid = _furniture(ctx, "terrarium", "table", label="terrarium stand", size_m=[1.25, 0.65, 0.75], surface_size_m=[1.25, 0.65])
    if stand is None:
        return None
    from .scenarios import _on
    enc = _on(ctx, stand, "reptile_enclosure", portable=False)
    if enc is None or ctx.config["entities"][enc].get("location") != stand:
        if enc is not None:
            del ctx.config["entities"][enc]
        del ctx.config["entities"][stand]
        return None
    p = ctx.placer
    for type_name in ("basking_lamp", "ceramic_heat_emitter", "terrarium_thermometer"):
        eid = p.make(type_name, enc, serves=enc)
        ctx.config["entities"][eid]["relation"] = "in"
    _pet_record(ctx, pid, "reptile", rid, enc, rng)
    room_t = C.num(ctx.config["rooms"][rid].get("temperature_c"), 21.0)
    warm = max(room_t + 10.0, min(31.0, room_t + 12.0))
    ctx.config["entities"][enc]["zones"] = {
        "basking": {"surface_c": round(room_t + 20.0, 1), "air_c": round(warm + 3.0, 1)},
        "warm": {"surface_c": round(warm + 1.5, 1), "air_c": round(warm, 1)},
        "cool": {"surface_c": round(room_t + 0.5 + 0.1 * (warm - room_t), 1), "air_c": round(room_t + 2.0 + 0.15 * (warm - room_t), 1)}}
    return enc


def _coop(ctx, rng, hens, start):
    coop, rid = _furniture(ctx, "chicken_coop", "chicken_coop", hens=hens, open=7 <= start.hour < 19)
    if coop is None:
        return None
    p = ctx.placer
    box = p.make("nest_box", coop)
    ctx.config["entities"][box]["relation"] = "in"
    if start.hour >= 11:
        egg_type = "hen_egg" if ctx.reg.known("hen_egg") else "coop_egg"
        for i in range(min(hens, rng.randint(1, 3))):
            egg = p.make(egg_type, box, laid_s=-3600.0 * (i + 1), category="egg")
            ctx.config["entities"][egg]["relation"] = "in"
    waterer = p.make("coop_waterer", coop, liquid_l=round(rng.uniform(2.0, 3.5), 2), _water_age_s=round(rng.uniform(4, 30) * 3600.0, 1))
    ctx.config["entities"][waterer]["relation"] = "in"
    ctx.config["entities"][waterer]["_last_l"] = ctx.config["entities"][waterer]["liquid_l"]
    from .scenarios import _room, _surface_in, _on
    entry = _room(ctx, None, ("mudroom", "kitchen", "entryway"))
    surface = _surface_in(ctx, entry) if entry else None
    if surface:
        _on(ctx, surface, "egg_basket")
    return coop


@furnisher("pets.habitats", order=94)
def furnish_habitats(config, rng, opts, registry):
    """Generation 2: the habitats (and pet records) of household.profile.pets_pending."""
    if int(opts.get("gen") or 1) < 2:
        return
    pending = ((config.get("household") or {}).get("profile") or {}).get("pets_pending") or []
    if not pending:
        return
    p = Placer(config, registry, rng)
    ctx = _Ctx(p)
    start = _start(config)
    done = []
    fish = [e for e in pending if e.get("habitat") == "aquarium"]
    chickens = [e for e in pending if e.get("habitat") == "chicken_coop"]
    if fish:
        if _aquarium(ctx, rng, start):
            done.extend(e.get("id") for e in fish)
        else:
            _note(opts, "no room with space for the aquarium")
    for entry in pending:
        pid, habitat, species = entry.get("id"), entry.get("habitat"), entry.get("species")
        if habitat == "bird_cage":
            made = _bird(ctx, rng, pid)
        elif habitat == "small_animal_cage":
            made = _small_animal(ctx, rng, pid, species if species in C.SMALL_ANIMALS else "hamster")
        elif habitat == "terrarium":
            made = _terrarium(ctx, rng, pid)
        else:
            continue
        if made:
            done.append(pid)
        else:
            _note(opts, "no room with space for the {} of {}".format(habitat, pid))
    if chickens:
        if _coop(ctx, rng, max(3, len(chickens)), start):
            done.extend(e.get("id") for e in chickens)
        else:
            _note(opts, "no space in the yard for the chicken coop")
    config["household"]["profile"]["pets_housed"] = sorted(str(d) for d in done if d)

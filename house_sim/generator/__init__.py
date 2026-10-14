"""Procedural household generator (docs/GENERATOR.md).

generate_house(seed, gen=1|2, dwelling=None, floors=None, bedrooms=None, bathrooms=None,
               features=(), require=(), residents="random"|"minimal"|"none"|[...]|{"composition": name},
               pets="random"|"none"|[...], start="2026-10-09T10:00:00", region=None, furnish=True)

Order: structural layout -> core furniture -> domain furnishers (registered with
@furnisher, ordered) -> robot. Results are cached per argument set and deep-copied.

gen=1 (the default) is the generation every verified spec uses; its output is pinned by
tests/data/gen1_digests.json. gen=2 (docs/design/TIER1_SWEEP.md section 14) adds the household
composition (generator/profile.py) drawn before the structure, exterior porch steps, the weather
process and clock model 2 (generator/climate.py), resident profiles, habits and schedules, and the
generation-2 furnishers (generator/furnishings.py, generator/belongings.py; orders 60 and 101-103)
that return at once for generation 1.
"""
import copy
import json
import random

from ..kernel.plugins import FURNISHERS, load_domains
from ..kernel.registry import default_registry
from . import furnish_core  # noqa: F401  (registers the core furnisher)
from . import belongings, furnishings  # noqa: F401  (generation-2 furnishers; they return at once for gen 1)
from . import climate, profile
from .layout import build_structure, default_pets, default_residents, finish_structure
from .place import Placer

_CACHE = {}
GENERATIONS = (1, 2)
REGIONS = {
    "us": {"units": "imperial", "voltage": 120, "washer": "top_load", "stove": "electric", "shoes_off": False},
    "uk": {"units": "metric", "voltage": 230, "washer": "front_load", "stove": "induction", "shoes_off": False},
    "eu": {"units": "metric", "voltage": 230, "washer": "front_load", "stove": "induction", "shoes_off": True},
    "jp": {"units": "metric", "voltage": 100, "washer": "top_load", "stove": "gas", "shoes_off": True},
    "ca": {"units": "metric", "voltage": 120, "washer": "front_load", "stove": "gas", "shoes_off": True},
}


def generate_house(seed=0, **opts):
    key = json.dumps({"seed": seed, **{k: (sorted(v) if isinstance(v, (set, tuple)) else v) for k, v in opts.items()}}, sort_keys=True, default=str)
    if key not in _CACHE:
        _CACHE[key] = _generate(seed, **opts)
        if len(_CACHE) > 64:
            _CACHE.pop(next(iter(_CACHE)))
    return copy.deepcopy(_CACHE[key])


def _generate(seed, registry=None, furnish=True, start="2026-10-09T10:00:00", region=None, report=None, trace=None, gen=1, **opts):
    """report: optional list that receives layout-audit warnings (strings; generation 2 adds "note gen2 ..." lines for
    belongings and furnishings it skipped); trace: optional dict that receives {entity id: furnisher name} for
    entities each furnisher created (tools/layout_audit.py)."""
    if gen not in GENERATIONS:
        raise ValueError("gen must be 1 or 2, not {!r}".format(gen))
    load_domains()
    registry = registry or default_registry()
    rng = random.Random("house:{}".format(seed))
    structure = opts
    if gen >= 2:
        # the region comes first so the composition can use its pet shares and names; the composition comes before
        # the structure because it decides how many bedrooms the house needs (sweep 14.2)
        region = region or rng.choice(sorted(REGIONS))
        composition = profile.compose(random.Random("profile:{}".format(seed)), dict(opts, region=region))
        structure = dict(opts, min_bedrooms=composition["bedrooms"], exterior_steps=True)
    builder, plan, entry, living, hubs = build_structure(seed, structure, registry)
    finish_structure(builder, plan, registry)
    region = region or rng.choice(sorted(REGIONS))
    gas = plan["dwelling"] != "apartment" or rng.random() < 0.3
    weather = None if gen >= 2 else {"condition": rng.choice(["clear", "clear", "cloudy"]), "temperature_c": rng.randint(8, 24),
                                     "wind_mps": rng.choice([1, 2, 3, 5])}
    config = {
        "schema_version": 2,
        "name": "Generated {} ({} floors, {} bedrooms)".format(plan["dwelling"], plan["floors"], plan["bedrooms"]),
        "metadata": dict(REGIONS[region], dwelling_type=plan["dwelling"], region=region, floors=plan["floors"],
                         bedrooms=plan["bedrooms"], bathrooms=plan["bathrooms"], features=sorted(plan["features"]),
                         fidelity="S/G", address="{} Example Lane".format(100 + seed % 900)),
        "clock": {"start": start, "hemisphere": "north"},
        "start_room": living,
        "rooms": builder.rooms,
        "connections": builder.connections,
        "entities": builder.entities,
        "people": {}, "pets": {},
        "utilities": {"power": True, "water": True, "gas": gas,
                      "main_water_open": True, "main_gas_open": True, "water_l": 100000.0, "indoor_temp_c": 21.0},
        "weather": weather,
        "events": [], "household": {"rules": [], "routines": {}}, "user_simulator": {},
    }
    furnisher_opts = dict(opts, plan=plan, region=region, gen=gen)
    if gen >= 2:
        season = climate.apply(config, seed, region, start)
        config["people"] = profile.people_records(rng, composition, config["rooms"], living)
        config["pets"] = profile.pet_records(rng, composition, config["rooms"])
        profile.apply_profiles(random.Random("profile:{}:people".format(seed)), config, composition, start, season, region,
                               beds=furnish)
        furnisher_opts.update(season=season, notes=report)
    else:
        config["people"] = default_residents(rng, opts, config["rooms"], living)
        config["pets"] = default_pets(rng, opts, config["rooms"])
    placer = Placer(config, registry, rng)
    # The robot's dock goes in first so every later placement keeps it reachable (the robot must be able to
    # leave it with 4-connected moves).
    dock = placer.furniture(living, "robot_dock", eid="robot_dock_1", wall=True)
    config["robot"] = {"room": living, "pos": None}
    if dock:
        config["robot"]["pos"] = list(config["entities"][dock]["pos"])
        config["robot"]["docked"] = True
        config["robot"]["home"] = dock
    if furnish:
        for _, name, fn in FURNISHERS:
            before = set(config["entities"]) if trace is not None else None
            fn(config, random.Random("furnish:{}:{}".format(seed, name)), dict(furnisher_opts), registry)
            if trace is not None:
                for eid in set(config["entities"]) - before:
                    trace[eid] = name
    from .repair import repair_layout
    repair_layout(config, placer, report)
    if gen >= 2:
        config["metadata"]["gen"] = 2
    return config

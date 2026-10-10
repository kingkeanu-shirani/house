"""Procedural household generator.

generate_house(seed, dwelling=None, floors=None, bedrooms=None, bathrooms=None,
               features=(), require=(), residents="random"|"minimal"|"none"|[...],
               pets="random"|"none"|[...], start="2026-10-09T10:00:00", region=None, furnish=True)

Order: structural layout -> core furniture -> domain furnishers (registered with
@furnisher, ordered) -> robot. Results are cached per argument set and deep-copied.
"""
import copy
import json
import random

from ..kernel.plugins import FURNISHERS, load_domains
from ..kernel.registry import default_registry
from . import furnish_core  # noqa: F401  (registers the core furnisher)
from .layout import build_structure, default_pets, default_residents, finish_structure
from .place import Placer

_CACHE = {}
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


def _generate(seed, registry=None, furnish=True, start="2026-10-09T10:00:00", region=None, report=None, trace=None, **opts):
    """report: optional list that receives layout-audit warnings (strings); trace: optional dict that receives
    {entity id: furnisher name} for entities each furnisher created (tools/layout_audit.py)."""
    load_domains()
    registry = registry or default_registry()
    rng = random.Random("house:{}".format(seed))
    builder, plan, entry, living, hubs = build_structure(seed, opts, registry)
    finish_structure(builder, plan, registry)
    region = region or rng.choice(sorted(REGIONS))
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
        "utilities": {"power": True, "water": True, "gas": plan["dwelling"] != "apartment" or rng.random() < 0.3,
                      "main_water_open": True, "main_gas_open": True, "water_l": 100000.0, "indoor_temp_c": 21.0},
        "weather": {"condition": rng.choice(["clear", "clear", "cloudy"]), "temperature_c": rng.randint(8, 24), "wind_mps": rng.choice([1, 2, 3, 5])},
        "events": [], "household": {"rules": [], "routines": {}}, "user_simulator": {},
    }
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
            fn(config, random.Random("furnish:{}:{}".format(seed, name)), dict(opts, plan=plan, region=region), registry)
            if trace is not None:
                for eid in set(config["entities"]) - before:
                    trace[eid] = name
    from .repair import repair_layout
    repair_layout(config, placer, report)
    return config

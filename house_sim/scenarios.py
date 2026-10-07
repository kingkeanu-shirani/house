"""Randomized initial states and independent predicates for supported families.

Scenarios contain explicit distractors and protected properties. These fixtures
are symbolic test distributions, not exhaustive models of the named chores.
"""
import copy
import random
import re

from .predicates import pred
from .world import make_house, object_record


def build(spec, seed=0):
    if not spec.get("family"):
        raise ValueError("This catalog entry is not executable: " + spec.get("reason", "TODO"))
    rng = random.Random(seed)
    family, params = spec["family"], copy.deepcopy(spec["parameters"])
    count = params.get("count", 1)
    room_count = rng.randint(3, 6)
    config = make_house(room_count, rng.randint(1, min(3, room_count)), seed,
                        outdoors=False, dwelling=rng.choice(["house", "apartment"]))
    config["goal"] = None
    rooms = list(config["rooms"])
    work_room = rng.choice(rooms[1:])
    subject = params.get("subject", "object")
    # Each generated object has a semantic category and actual attributes, rather
    # than a single task completion bit. Surface tasks remain fixed in place.
    config["rooms"][work_room]["label"] = spec.get("section", "Work area")
    config["entities"]["worktop"] = object_record("work surface", work_room, accepts_items=True, capacity_l=100)
    config["entities"]["storage"] = object_record(params.get("destination_label", "designated storage"), work_room,
                                                 accepts_items=True, openable=True, open=False, capacity_l=100)
    config["entities"]["delivery"] = object_record("resident's requested delivery surface", "room_1", accepts_items=True, capacity_l=100)
    config["entities"]["precious"] = object_record("sentimental keepsake", "worktop", portable=True, fragile=True,
                                                  sentimental=True, value=500, material="glass")
    config["entities"]["decoy"] = object_record("unrelated clean object", "worktop", portable=True, soil=0,
                                               affordances=["clean", "fold"], folded=False)
    config["entities"]["resident_1"] = object_record("Resident", "room_1", category="person", accepts_items=True, capacity_l=30)
    tools = {"wipe": "cloth", "dust": "duster", "scrub": "brush", "sweep": "broom", "vacuum": "vacuum_tool",
             "mop": "mop_tool", "dry": "dry_cloth", "disinfect": "sanitizer", "polish": "polishing_cloth", "screwdriver": "screwdriver"}
    for capability, tid in tools.items():
        config["entities"][tid] = object_record(tid.replace("_", " "), "room_1", portable=True,
                                               category="tool", affordances=[capability], uses=20,
                                               soil_load=0.0, soil_capacity=100.0,
                                               wet_tool=capability in ("mop", "scrub"), unsafe_materials=[], chemicals=[])
    targets = ["target_" + str(i + 1) for i in range(count)]
    success = []
    preserve = ["entities.precious", "entities.decoy", "entities.storage.location"]
    task = {"schema_version": 1, "catalog_id": spec.get("catalog_id", "custom"), "family": family,
            "instruction": rng.choice(spec.get("phrasings", [spec.get("instruction", "Complete the task")])),
            "success": success, "failure": [pred("stats.damage", 0, "gt"), pred("stats.privacy_blocks", 0, "gt"),
                                             pred("stats.safety_blocks", 0, "gt")],
            "preserve": preserve, "step_budget": spec.get("step_budget", 180),
            "time_budget_s": spec.get("time_budget_s", 1800), "risk_level": spec.get("risk_level", 1), "fidelity": "S"}
    oracle = {"family": family, "parameters": params, "targets": targets, "work_room": work_room,
              "tools": tools, "destinations": {}, "report_values": {}}

    def obj(tid, **attrs):
        base = {"category": re.sub(r"\W+", "_", subject.lower()).strip("_"), "portable": True,
                "material": rng.choice(["cotton", "plastic", "ceramic"]),
                "mass_kg": round(rng.uniform(0.2, 2), 2), "volume_l": 0.5,
                "home": "storage", "location": "worktop"}
        base.update(attrs)
        config["entities"][tid] = object_record(subject, base.pop("location"), **base)
        return config["entities"][tid]

    if family in ("clean", "quiet_clean"):
        method = params.get("method", "wipe")
        fixed = bool(re.search(r"(floor|table|counter|wall|window|faucet|sink|handle|deck|patio|driveway|walkway|porch|step|shelf|shelves|screen)", subject, re.I))
        # Washing fixed surfaces is not a sink-transfer task; map only to methods
        # actually supported by the family. The compiler excludes such mismatches.
        for tid in targets:
            attrs = {"soil": round(rng.uniform(0.3, 0.9), 3), "wetness": 0,
                     "affordances": ["clean", "sweep", "vacuum", "mop", "wash", "rinse", "dry", "disinfect", "polish"],
                     "portable": not fixed or method in ("wash", "rinse"), "soapy": False,
                     "contaminated": True, "polished": False}
            if method == "dry": attrs.update(soil=0, wetness=1.0)
            if method in ("rinse", "disinfect", "polish"): attrs.update(soil=0)
            if method == "rinse": attrs["soapy"] = True
            obj(tid, **attrs)
            if re.search(r"(screen|electronics|keyboard|monitor|television|mouse|speaker|console|computer)", subject, re.I):
                config["entities"][tid].update(material="electronics", water_safe=False)
            if method == "disinfect": success.append(pred("entities." + tid + ".contaminated", False))
            elif method == "polish": success.append(pred("entities." + tid + ".polished", True))
            elif method == "dry": success.append(pred("entities." + tid + ".wetness", 0))
            elif method == "rinse": success.append(pred("entities." + tid + ".soapy", False))
            else: success.append(pred("entities." + tid + ".soil", 0))
            if method not in ("wash", "rinse"):
                preserve.append("entities." + tid + ".location")
            if method in ("mop", "scrub", "wash"):
                success.append(pred("entities." + tid + ".wetness", 0))
            if method == "wash": success.append(pred("entities." + tid + ".soapy", False))
        if family == "quiet_clean":
            config["people"]["resident_1"].update(location=work_room, quiet=True)
            task["failure"].append(pred("stats.noise", 0.5, "gt"))
            config["entities"]["resident_1"]["location"] = work_room
        oracle["method"] = method if method != "clean" else "wipe"
    elif family == "transform":
        method = params["method"]
        field = {"fold": "folded", "unfold": "folded", "flatten": "flattened", "coil": "coiled", "uncoil": "coiled", "tighten": "loose"}[method]
        final = method not in ("unfold", "uncoil", "tighten")
        for tid in targets:
            obj(tid, affordances=["fold", "flatten", "coil", "tighten"], empty=True, plugged=False, **{field: not final})
            success.append(pred("entities." + tid + "." + field, final))
            preserve.append("entities." + tid + ".location")
        oracle["method"] = method
    elif family == "access":
        method = params["method"]
        field = "open" if method in ("open", "close") else "locked"
        final = method in ("open", "lock")
        for tid in targets:
            obj(tid, portable=False, openable=True, accepts_items=True, lockable=True,
                open=(not final) if field == "open" else False,
                locked=(not final) if field == "locked" else False, key="key")
            success.append(pred("entities." + tid + "." + field, final))
        config["entities"]["key"] = object_record("matching key", "room_1", portable=True, category="key")
        oracle["method"] = method
    elif family == "switch":
        method = params["method"]
        for tid in targets:
            obj(tid, portable=False, switchable=True, on=method != "switch_on")
            success.append(pred("entities." + tid + ".on", method == "switch_on"))
        oracle["method"] = method
    elif family in ("relocate", "sort", "pickup", "find", "clarify", "clarify_destination"):
        for n, tid in enumerate(targets):
            loc = rng.choice(rooms[1:])
            if family in ("pickup", "find"):
                loc = "storage"
                if params.get("source") == "floor": loc = work_room
                elif params.get("source") in ("table", "shelf"): loc = "worktop"
            obj(tid, location=loc, fragile="fragile" in subject, mass_kg=12 if "heavy" in subject else 0.4)
            if family == "pickup":
                success.append(pred("robot.holding", tid))
                continue
            if family == "find":
                success.append(pred("facts.inspected." + tid, True))
                success.append(pred("reports." + tid + ".location", loc))
                oracle["report_values"][tid] = loc
                continue
            dest = "delivery" if params.get("mode") == "retrieve" else "storage"
            if params.get("mode") == "unload":
                config["entities"][tid]["location"] = "storage"
                dest = "delivery"
            if family == "sort":
                field = params["field"]
                field = "category" if field == "type" else field
                value = {"category": ["cup", "book"], "owner": ["resident_1", "resident_2"],
                         "color": ["red", "blue"], "material": ["cotton", "polyester"],
                         "size": ["small", "large"]}[field][n % 2]
                if field == "size": field = "size_class"
                config["entities"][tid][field] = value
                dest = "bin_" + str(n % 2)
                config["entities"][dest] = object_record("storage for " + value, work_room, accepts_items=True, capacity_l=100, sort_field=field, sort_value=value)
            oracle["destinations"][tid] = dest
            success.append(pred("entities." + tid + ".location", dest))
        if family == "clarify":
            config["entities"]["decoy"].update(label=subject)
            selected = rng.choice([targets[0], "decoy"])
            for item in (targets[0], "decoy"): config["entities"][item]["location"] = "room_1"
            preserve[:] = [p for p in preserve if p != "entities.decoy"]
            other = "decoy" if selected == targets[0] else targets[0]
            preserve.append("entities." + other)
            config["user_simulator"] = {"answers": {"target": selected}}
            success[:] = [pred("entities." + selected + ".location", "resident_1"), pred("stats.necessary_questions", 1, "gte")]
            oracle.update(targets=[selected], destinations={selected: "resident_1"}, question="target", other=other)
        elif family == "clarify_destination":
            selected = rng.choice(["storage", "delivery"])
            config["entities"][targets[0]]["location"] = "room_1"
            config["user_simulator"] = {"answers": {"destination": selected}}
            success[:] = [pred("entities." + targets[0] + ".location", selected), pred("stats.necessary_questions", 1, "gte")]
            oracle.update(destinations={targets[0]: selected}, question="destination", other="delivery" if selected == "storage" else "storage")
    elif family in ("water", "fill"):
        config["entities"]["watering_can"] = object_record("watering can", "room_1", portable=True, liquid_container=True,
                                                         liquid="water", liquid_l=2.0, capacity_l=3.0)
        for tid in targets:
            if family == "water":
                moisture = round(rng.uniform(0.05, 0.25), 3)
                obj(tid, category="plant", portable=False, moisture=moisture, max_moisture=0.7)
                success.extend([pred("entities." + tid + ".moisture", 0.4, "gte"), pred("entities." + tid + ".moisture", 0.7, "lte")])
                oracle.setdefault("amounts", {})[tid] = round(0.5 - moisture, 3)
            else:
                obj(tid, liquid_container=True, liquid="water", liquid_l=0.0, capacity_l=1.0)
                success.append(pred("entities." + tid + ".liquid_l", 0.75, "gte"))
        if family == "water":
            config["entities"]["healthy_plant"] = object_record("plant that already has enough water", "worktop", category="plant", moisture=0.65, max_moisture=0.7)
            preserve.append("entities.healthy_plant")
    elif family in ("inventory", "report", "verify_state"):
        for tid in targets:
            if family == "inventory":
                obj(tid, portable=False, accepts_items=True, openable=True, open=False)
                n = rng.randint(2, 7)
                item_label = "food item" if re.search(r"(pantry|refrigerator|freezer)", subject, re.I) else subject
                for i in range(n): config["entities"][tid + "_item_" + str(i)] = object_record(item_label, tid, portable=True)
                value, field = n, "count"
            elif family == "verify_state":
                field = params["field"]
                value = rng.choice([True, False])
                obj(tid, portable=False, **{field: value})
            else:
                field, value = "hazards", [subject]
                obj(tid, portable=False, hazards=value)
            success.append(pred("reports." + tid + "." + field, value))
            preserve.append("entities." + tid)
            if family == "inventory": preserve.remove("entities." + tid)  # Opening is necessary.
            oracle["report_values"][tid] = value
            oracle["report_field"] = field
    elif family in ("appliance", "laundry_watch"):
        category = params.get("subject", "washer") if family == "appliance" else "washer"
        method = params.get("method", "start")
        obj(targets[0], category=category, portable=False, accepts_items=True, openable=True, open=method != "pause",
            cycle="running" if method == "pause" else "idle", mode="normal", modes=["normal", "gentle"],
            duration_s=rng.randint(30, 60), remaining_s=100, capacity_l=10)
        config["entities"]["load_item"] = object_record("washable load", targets[0], portable=True, soil=0.8, wetness=1 if category == "dryer" else 0,
                                                      machine_safe=[category], delicate=False)
        if family == "appliance":
            success.append(pred("entities." + targets[0] + ".cycle", "running" if method == "start" else "paused"))
            oracle["method"] = method
        else:
            success.extend([pred("entities." + targets[0] + ".cycle", "done"), pred("reports." + targets[0] + ".cycle", "done"),
                            pred("facts.last_speech.text", "Laundry is done.")])
    elif family == "rain_watch":
        config["weather"]["condition"] = "clear"
        for tid in targets:
            obj(tid, portable=False, openable=True, accepts_items=True, open=True, location="room_1")
            success.append(pred("entities." + tid + ".open", False))
        event_time = rng.randint(3, 6)
        config["events"] = [{"at_s": event_time, "kind": "rain_started", "changes": {"weather.condition": "rain"}}]
        success.extend([pred("weather.condition", "rain"), pred("facts.orders_fired", 1, "gte")])
        for tid in targets: success.append(pred("facts.action_times." + tid + ".close", event_time, "gte"))
        oracle["event_time"] = event_time
    else:
        raise ValueError("Unimplemented family: " + family)
    task["initial_state_generator"] = {"family": family, "parameters": params, "seed": seed}
    return config, task, oracle

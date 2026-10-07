"""Guarded symbolic transitions, observations, events, evaluation, and replay.

No action can set an arbitrary world property or mark a task complete. Task goals
are read only by the evaluator. Oracle solutions live outside this module.
"""
import copy
import json
from pathlib import Path

from .predicates import MISSING, check, digest, differences, get_path, set_path, validate_predicate
from .world import make_house, normalize, validate


class ActionError(ValueError):
    def __init__(self, message, category="precondition"):
        super().__init__(message)
        self.category = category


def require(condition, message, category="precondition"):
    if not condition:
        raise ActionError(message, category)


# These definitions are public and executable. Adding a new transformation can be
# a JSON-only change, provided it uses an existing effect operator.
ACTION_FILE = Path(__file__).with_name("data") / "actions.json"


class HouseEnv:
    def __init__(self, config=None, task=None, seed=0, sensors=None, action_file=None):
        config = make_house(seed=seed) if config is None else config
        self._initial = copy.deepcopy(config)
        normalize(config)  # Validate before accepting a configuration.
        self._task = copy.deepcopy(task)
        if task:
            for rule in task.get("success", []) + task.get("failure", []):
                validate_predicate(rule)
        self.seed = seed
        self.sensors = sensors or ["symbolic", "language"]
        if set(self.sensors) - {"symbolic", "language"}:
            raise NotImplementedError("Only symbolic and language observations exist; RGB-D/tactile/audio physics are not implemented")
        self.action_file = Path(action_file) if action_file else ACTION_FILE
        self.action_defs = json.loads(self.action_file.read_text(encoding="utf-8"))
        self._validate_actions()
        self.reset()

    def _validate_actions(self):
        protected = {"location", "owner", "private", "portable", "risk", "requires_approval", "permissions"}
        for name, rule in self.action_defs.items():
            if not isinstance(rule.get("effects"), dict) or not isinstance(rule.get("affordance"), str):
                raise ValueError("Action definition requires affordance and effects: " + name)
            if set(rule["effects"]) & protected:
                raise ValueError("Declarative transformations cannot modify structural/security properties")

    @property
    def room(self): return self._state["robot"]["room"]

    @property
    def holding(self): return self._state["robot"]["holding"]

    @property
    def steps(self): return self._state["steps"]

    def reset(self, preserve_world=False):
        if preserve_world and hasattr(self, "_state"):
            # Continue the same household, resetting only episode accounting.
            self._state["steps"] = 0
            self._state["stats"] = self._empty_stats()
            self._state["reports"] = {}
            self._state["facts"] = {}
        else:
            self._state = normalize(self._initial)
            self._state.update(steps=0, time_s=0, stats=self._empty_stats(), facts={}, reports={},
                               approvals=[], memory={}, orders=[], pending=[], event_log=[])
        self._baseline = copy.deepcopy(self._state)
        self._known_map, self._history, self._undo, self._snapshots = {}, [], [], {}
        self._replay_start = copy.deepcopy(self._state)
        return self.observe()

    @staticmethod
    def _empty_stats():
        return {"invalid": 0, "safety_blocks": 0, "privacy_blocks": 0, "unnecessary_questions": 0,
                "necessary_questions": 0, "noise": 0.0, "energy": 0.0, "damage": 0,
                "injury": 0, "fire": 0, "flood": 0, "handoffs": 0, "undo_count": 0}

    def privileged_state(self):
        """Oracle/debug API. Never pass its output to an evaluated agent."""
        return copy.deepcopy(self._state)

    def known_map(self): return copy.deepcopy(self._known_map)

    def _root(self, eid):
        entities = self._state["entities"]
        location, seen = eid, set()
        while location in entities:
            require(location not in seen, "Containment cycle")
            seen.add(location)
            location = entities[location]["location"]
        return location

    def _visible(self, eid):
        entities = self._state["entities"]
        obj = entities[eid]
        if "connects" in obj and self.room in obj["connects"]:
            return True
        if eid == self.holding: return True
        location = obj["location"]
        seen = {eid}
        while location in entities:
            if location in seen: return False
            seen.add(location)
            parent = entities[location]
            if parent.get("private") and "touch:" + location not in self._state["approvals"]: return False
            if parent.get("openable") and not parent.get("open", False): return False
            location = parent["location"]
        return location in (self.room, "hand")

    def observe(self):
        state = self._state
        room = copy.deepcopy(state["rooms"][self.room])
        self._known_map[self.room] = list(room["exits"])
        result = {"room_id": self.room, "room": room, "holding": self.holding,
                  "steps": self.steps, "time_s": state["time_s"], "stopped": state["robot"]["stopped"],
                  "battery": round(state["robot"]["battery"], 3), "pending": copy.deepcopy(state["pending"])}
        if "symbolic" in self.sensors:
            visible = {}
            for eid, entity in state["entities"].items():
                if self._visible(eid):
                    data = copy.deepcopy(entity)
                    for key in ("permissions", "hidden", "private_contents"):
                        data.pop(key, None)
                    if entity.get("private") and "touch:" + eid not in state["approvals"]:
                        data = {k: data[k] for k in ("label", "location", "private", "openable") if k in data}
                    visible[eid] = data
            result.update(visible_entities=visible,
                          people={pid: {k: p[k] for k in ("label", "role", "location", "mood") if k in p}
                                  for pid, p in state["people"].items() if p.get("location") == self.room},
                          pets={pid: copy.deepcopy(p) for pid, p in state["pets"].items() if p.get("location") == self.room},
                          weather=copy.deepcopy(state["weather"]))
        if "language" in self.sensors:
            result["instruction"] = self._task.get("instruction", "") if self._task else ""
            result["last_answer"] = copy.deepcopy(state["facts"].get("last_answer"))
        return result

    def _object(self, eid, touch=False, action="inspect"):
        require(eid in self._state["entities"] and self._visible(eid), "That entity is not visible here")
        obj = self._state["entities"][eid]
        if touch:
            require(not obj.get("private") or "touch:" + eid in self._state["approvals"], "Owner permission is required", "privacy")
            require(not obj.get("off_limits"), "This object is off limits", "privacy")
            require(self._state["robot"]["actuator_ok"], "Actuator fault; request human assistance", "safety")
            require(self._state["robot"]["battery"] > 0, "Battery depleted; request assistance", "safety")
            if obj.get("risk", 0) == 3 or obj.get("requires_approval"):
                scope = action + ":" + eid
                require(scope in self._state["approvals"], "Explicit scoped human approval required", "safety")
                adult = any(p.get("role") == "adult" and p.get("location") == self.room and p.get("consent")
                            for p in self._state["people"].values())
                require(adult, "Consenting adult presence required", "safety")
        return obj

    def _subtree(self, eid):
        children, result = [eid], []
        while children:
            parent = children.pop()
            result.append(parent)
            children.extend(k for k, v in self._state["entities"].items() if v["location"] == parent)
        return result

    def _can_handle(self, eid, carrying=True):
        for child in self._subtree(eid) if carrying else [eid]:
            obj = self._state["entities"][child]
            require(not obj.get("off_limits"), "Contained item is off limits", "privacy")
            require(not obj.get("private") or "touch:" + child in self._state["approvals"], "Contained private item requires owner permission", "privacy")
            require(obj.get("temperature_c", 21) <= 50, "Object or contents are too hot to handle", "safety")
            require(not set(obj.get("hazards", [])) & {"sharp", "biohazard", "live_wire", "toxic"}, "Hazardous handling requires a specialized skill", "safety")
            if child != eid and (obj.get("risk", 0) == 3 or obj.get("requires_approval")):
                require("pick_up:" + child in self._state["approvals"], "Contained item requires scoped approval", "safety")
                require(any(p.get("role") == "adult" and p.get("consent") and p.get("location") == self.room for p in self._state["people"].values()), "Contained risky item requires adult presence", "safety")
        if carrying:
            total = sum(self._state["entities"][x].get("mass_kg", 0) + self._state["entities"][x].get("liquid_l", 0) for x in self._subtree(eid))
            require(total <= self._state["robot"]["max_mass_kg"], "Object and contents exceed carrying capacity", "safety")

    def _move(self, target):
        require(target in self._state["rooms"][self.room]["exits"], "Move to a listed exit")
        room = self._state["rooms"][target]
        require(not room.get("blocked"), "Passage is blocked", "safety")
        require(not room.get("private") or "enter:" + target in self._state["approvals"], "Room entry requires permission", "privacy")
        require(room.get("lit", True), "Destination is dark; arrange lighting before entry", "safety")
        for obj in self._state["entities"].values():
            if set(obj.get("connects", [])) == {self.room, target}:
                require(obj.get("open") and not obj.get("locked"), "Open the connecting door first")
        require(self._state["robot"]["battery"] > 0 and self._state["robot"]["actuator_ok"], "Robot cannot move; hand off", "safety")
        if self.holding: self._can_handle(self.holding)
        self._state["robot"]["room"] = target

    def _place(self, target, destination):
        require(self.holding == target, "You are not holding that object")
        obj = self._object(target, True, "place")
        self._can_handle(target)
        if destination != self.room:
            dest = self._object(destination, True, "place")
            require(dest.get("accepts_items"), "Destination cannot contain objects")
            require(not dest.get("openable") or dest.get("open"), "Open the destination first")
            require(destination not in self._subtree(target), "Cannot place an object inside itself or its descendants")
            contents = [e for e in self._state["entities"].values() if e["location"] == destination]
            used = sum(e.get("volume_l", 0) for e in contents)
            require(used + obj.get("volume_l", 0) <= dest.get("capacity_l", 50), "Destination capacity exceeded")
            require(not dest.get("waste_stream") or obj.get("waste_stream") == dest["waste_stream"], "Object does not belong in that waste stream", "safety")
            require(not dest.get("waste_stream") or not obj.get("sentimental"), "Protected item must not be discarded", "safety")
        obj["location"] = destination
        self._state["robot"]["holding"] = None

    def _transform(self, action, target, tool):
        rule = self.action_defs[action]
        obj = self._object(target, True, action)
        require(rule["affordance"] in obj.get("affordances", []), "Object does not support " + action)
        self._can_handle(target, carrying=False)
        for field, expected in rule.get("requires", {}).items():
            require(obj.get(field) == expected, "Requires {} = {}".format(field, expected))
        if rule.get("tool"):
            implement = self._object(tool, True, action)
            require(self.holding == tool, "Hold the required tool first")
            require(rule["tool"] in implement.get("affordances", []), "Tool cannot perform " + action)
            require(implement.get("uses", 0) >= 1 and not implement.get("broken"), "Tool is depleted or broken")
            require(obj.get("material") not in implement.get("unsafe_materials", []), "Tool is incompatible with this material", "safety")
            require(not (set(implement.get("chemicals", [])) & set(self._initial.get("forbidden_chemicals", []))), "Household rule forbids this cleaner", "safety")
            require(not {"bleach", "ammonia"}.issubset(set(implement.get("chemicals", []))), "Incompatible cleaning chemicals", "safety")
            require(not implement.get("wet_tool") or obj.get("water_safe", True), "Surface cannot tolerate water", "safety")
            require(implement.get("soil_load", 0) + obj.get("soil", 0) <= implement.get("soil_capacity", 100), "Tool cannot contain more dirt")
        if rule.get("water"):
            require(obj.get("water_safe", True), "Object is water-sensitive", "safety")
            require(self._state["utilities"]["water"] and self._state["utilities"]["water_l"] >= rule["water"], "Water unavailable")
            parent = self._state["entities"].get(obj["location"], {})
            require(parent.get("drain") and self._visible(obj["location"]), "Place the item at a drain/sink before washing")
        if rule.get("detergent"):
            require(self._state["utilities"]["detergent_units"] >= rule["detergent"], "Detergent unavailable")
        if rule.get("power"):
            require(self._state["utilities"]["power"], "Power unavailable")
        if rule.get("noise", 0) > 0.5:
            require(not self._state["robot"]["quiet"] and not any(p.get("quiet") for p in self._state["people"].values() if p.get("location") == self.room), "A quiet method is required", "social")
        soil_before = obj.get("soil", 0)
        for key, value in rule["effects"].items():
            obj[key] = copy.deepcopy(value)
        if rule.get("tool"):
            implement["uses"] -= 1
            if "soil" in rule["effects"]:
                implement["soil_load"] = implement.get("soil_load", 0) + soil_before
        if rule.get("water"):
            self._state["utilities"]["water_l"] -= rule["water"]
            if not rule.get("tool"):
                self._state["utilities"]["wastewater_soil"] += soil_before
        if rule.get("detergent"):
            self._state["utilities"]["detergent_units"] -= rule["detergent"]
        self._state["stats"]["noise"] += rule.get("noise", 0)

    def _ask(self, person, topic):
        people = self._state["people"]
        require(person in people and people[person].get("location") == self.room, "That person is not available here")
        resident = people[person]
        if topic.startswith("permission:"):
            scope = topic[len("permission:"):]
            permitted = resident.get("role") == "adult" and resident.get("consent") and scope in resident.get("permissions", [])
            if permitted and scope not in self._state["approvals"]: self._state["approvals"].append(scope)
            answer = {"topic": topic, "granted": bool(permitted)}
        else:
            answers = self._initial.get("user_simulator", {}).get("answers", {})
            if topic in answers:
                self._state["stats"]["unnecessary_questions" if topic in self._state["facts"] else "necessary_questions"] += 1
                answer = {"topic": topic, "value": copy.deepcopy(answers[topic])}
                self._state["facts"][topic] = copy.deepcopy(answers[topic])
            else:
                self._state["stats"]["unnecessary_questions"] += 1
                answer = {"topic": topic, "value": "I have no additional information about that."}
        self._state["facts"]["last_answer"] = answer
        return json.dumps(answer)

    def _appliance(self, action, target, mode):
        obj = self._object(target, True, action)
        require(obj.get("category") in ("washer", "dryer", "dishwasher"), "This appliance has no supported cycle state machine")
        if action == "set_mode":
            require(obj.get("cycle", "idle") in ("idle", "done"), "Pause/finish the current cycle first")
            require(mode in obj.get("modes", ["normal", "gentle"]), "Unsupported mode")
            obj["mode"] = mode
            return
        if action == "pause":
            require(obj.get("cycle") == "running", "Appliance is not running")
            obj["cycle"] = "paused"
            return
        require(obj.get("cycle", "idle") in ("idle", "done", "paused"), "Already running")
        require(self._state["utilities"]["power"], "Power unavailable")
        require(not obj.get("open"), "Close appliance door first")
        if obj.get("cycle") != "paused":
            contents = [e for e in self._state["entities"].values() if e["location"] == target]
            require(bool(contents), "Load suitable items first")
            require(all(obj["category"] in e.get("machine_safe", []) for e in contents), "Load includes an incompatible item", "safety")
            require(all(not e.get("delicate") or obj.get("mode") == "gentle" for e in contents), "Delicate load requires gentle mode", "safety")
            if obj["category"] != "dryer":
                utility = self._state["utilities"]
                require(utility["water"] and utility["water_l"] >= 5 and utility["detergent_units"] >= 1, "Water or detergent unavailable")
                utility["water_l"] -= 5
                utility["detergent_units"] -= 1
            obj["remaining_s"] = obj.get("duration_s", 60)
        obj["cycle"] = "running"

    def _perform(self, action, target, destination, kwargs):
        s = self._state
        if action == "look": return "You look around"
        if action == "wait": return "Waiting"
        if action == "stop":
            s["robot"]["stopped"] = True
            return "Stopped; the physical world and appliance clocks continue"
        if action == "resume":
            s["robot"]["stopped"] = False
            return "Resumed"
        if action in ("speak", "announce", "refuse", "handoff"):
            text = kwargs.get("text", target)
            require(isinstance(text, str) and bool(text.strip()), "Supply a message")
            s["facts"]["last_speech"] = {"action": action, "text": text}
            if action == "handoff": s["stats"]["handoffs"] += 1
            return text
        if action == "listen":
            return json.dumps(s["facts"].get("last_message", "No new message"))
        if action == "ask": return self._ask(target, kwargs.get("topic", destination))
        if action == "set_quiet":
            require(target in ("on", "off"), "Use on or off")
            s["robot"]["quiet"] = target == "on"
            return "Quiet mode " + target
        if action == "move": self._move(target)
        elif action == "inspect":
            obj = self._object(target)
            if obj.get("private"):
                require("touch:" + target in s["approvals"], "Private inspection requires permission", "privacy")
            s["facts"].setdefault("inspected", {})[target] = True
            return json.dumps({"object": target, "soil": obj.get("soil"), "wetness": obj.get("wetness"), "temperature_c": obj.get("temperature_c"),
                               "contents": "hidden" if obj.get("openable") and not obj.get("open") else
                               [eid for eid, e in s["entities"].items() if e["location"] == target]})
        elif action == "pick_up":
            obj = self._object(target, True, action)
            require(self.holding is None, "Place your held object first")
            require(obj.get("portable"), "Object is fixed in place")
            self._can_handle(target)
            obj["location"] = "hand"
            s["robot"]["holding"] = target
        elif action == "place": self._place(target, destination)
        elif action == "hand_over":
            require(destination in s["people"] and s["people"][destination].get("location") == self.room, "Recipient is not here")
            require(s["people"][destination].get("consent"), "Recipient has not consented", "privacy")
            self._place(target, destination)
        elif action in ("fill", "pour", "water_plant"):
            obj = self._object(target, True, action)
            dest = self._object(destination, True, action)
            amount = kwargs.get("amount_l", 0.25)
            require(type(amount) in (int, float) and 0 < amount <= 20, "amount_l must be greater than zero and at most 20")
            if action == "fill":
                require(self.holding == target and obj.get("liquid_container"), "Hold a liquid container")
                require(dest.get("water_source") and self._state["utilities"]["water"], "Water source unavailable")
                require(not obj.get("openable") or obj.get("open"), "Open the container")
                require(obj.get("soil", 0) == 0, "Use a clean liquid container", "safety")
                require(obj.get("liquid_l", 0) + amount <= obj["capacity_l"], "Container would overflow", "safety")
                self._can_handle(target)
                mass = sum(s["entities"][eid].get("mass_kg", 0) + s["entities"][eid].get("liquid_l", 0) for eid in self._subtree(target))
                require(mass + amount <= s["robot"]["max_mass_kg"], "Filling would exceed carried mass capacity", "safety")
                require(self._state["utilities"]["water_l"] >= amount, "Water supply depleted")
                obj["liquid_l"] = obj.get("liquid_l", 0) + amount
                obj["liquid"] = "water"
                self._state["utilities"]["water_l"] -= amount
            elif action == "pour":
                require(self.holding == target and obj.get("liquid_container"), "Hold the source container")
                require(dest.get("liquid_container"), "Destination cannot contain liquid")
                require(not obj.get("openable") or obj.get("open"), "Open the source")
                require(not dest.get("openable") or dest.get("open"), "Open the destination")
                require(obj.get("liquid_l", 0) >= amount, "Not enough source liquid")
                require(dest.get("liquid_l", 0) + amount <= dest["capacity_l"], "Destination would overflow", "safety")
                require(obj.get("liquid") == "water" and dest.get("liquid", "water") == "water" and dest.get("soil", 0) == 0, "Only clean water transfer is supported", "safety")
                self._can_handle(target)
                obj["liquid_l"] -= amount
                dest["liquid_l"] = dest.get("liquid_l", 0) + amount
                dest["liquid"] = "water"
            else:
                # target is the plant; destination is the held water container.
                require(obj.get("category") == "plant" and self.holding == destination, "Hold the watering container and select a plant")
                require(dest.get("liquid") == "water" and dest.get("liquid_l", 0) >= amount, "Not enough clean water")
                require(obj.get("moisture", 0) + amount <= obj.get("max_moisture", 0.7), "This would overwater the plant", "safety")
                obj["moisture"] = obj.get("moisture", 0) + amount
                dest["liquid_l"] -= amount
        elif action in ("open", "close"):
            obj = self._object(target, True, action)
            require(obj.get("openable"), "Object is not openable")
            require(not obj.get("locked"), "Unlock it first")
            require(obj.get("cycle") != "running", "Running appliance door is interlocked", "safety")
            require(not obj.get("obstructed"), "Opening/closing is obstructed")
            obj["open"] = action == "open"
        elif action in ("lock", "unlock"):
            obj = self._object(target, True, action)
            require(obj.get("lockable"), "No supported lock")
            require(not obj.get("open"), "Close it before locking/unlocking")
            key = obj.get("key")
            require(not key or self.holding == key, "Hold the matching key")
            obj["locked"] = action == "lock"
        elif action in ("switch_on", "switch_off"):
            obj = self._object(target, True, action)
            require(obj.get("switchable"), "No supported switch")
            require(not obj.get("life_safety"), "Life-safety equipment cannot be disabled by this interface", "safety")
            require(action == "switch_off" or s["utilities"]["power"], "Power unavailable")
            obj["on"] = action == "switch_on"
            if obj.get("category") == "light":
                root = self._root(target)
                if root in s["rooms"]:
                    s["rooms"][root]["lit"] = any(e.get("category") == "light" and e.get("on") and self._root(eid) == root for eid, e in s["entities"].items())
        elif action in ("start", "pause", "set_mode"):
            self._appliance(action, target, kwargs.get("mode", destination))
        elif action in self.action_defs:
            self._transform(action, target, kwargs.get("tool", destination))
        elif action == "report":
            require(s["facts"].get("inspected", {}).get(target), "Inspect this object before reporting")
            obj = self._object(target)
            field, value = kwargs.get("field", destination), kwargs.get("value")
            require(field in ("soil", "wetness", "temperature_c", "freshness", "broken", "on", "open", "locked", "owner", "category", "count", "hazards", "cycle", "location"), "Unsupported report field")
            if field == "count":
                require(not obj.get("openable") or obj.get("open"), "Open container before counting")
            s["reports"].setdefault(target, {})[field] = copy.deepcopy(value)
        elif action == "remember":
            require(target in ("preferred_location", "routine", "do_not_touch"), "Unsupported memory category")
            require(destination in s["facts"], "Clarify the preference first")
            s["memory"][target] = copy.deepcopy(s["facts"][destination])
        elif action == "forget":
            require(target in s["memory"], "No such memory")
            del s["memory"][target]
        elif action == "watch":
            condition, commands = kwargs.get("condition"), kwargs.get("commands")
            require(isinstance(condition, dict) and isinstance(commands, list) and 0 < len(commands) <= 20, "Supply a predicate and 1..20 commands")
            try: validate_predicate(condition)
            except ValueError as exc: raise ActionError(str(exc))
            require(self._watch_visible(condition), "Watch only publicly observable conditions")
            require(all(isinstance(c, dict) and isinstance(c.get("action"), str) and c["action"] in self.action_names() - {"watch", "run_pending"} for c in commands), "Unsupported scheduled command")
            s["orders"].append({"condition": copy.deepcopy(condition), "commands": copy.deepcopy(commands), "fired": False})
        elif action == "run_pending":
            require(bool(s["pending"]), "No pending command")
            command = s["pending"][0]
            require(command["action"] not in ("watch", "run_pending"), "Recursive schedule is not allowed")
            command = copy.deepcopy(command)
            act, tgt, dest = command.pop("action"), command.pop("target", ""), command.pop("destination", "")
            self._perform(act, tgt, dest, command)
            s["pending"].pop(0)
        elif action == "undo":
            require(bool(self._undo), "No reversible action to undo")
            record = self._undo[-1]
            changes = record["changes"]
            require(all(get_path(s, d["path"]) == d["after"] for d in changes), "State changed since that action; undo would overwrite newer changes")
            inverse = record["inverse"]
            # Reversal goes through the same physical/safety preconditions. This
            # prevents undo from teleporting through newly closed doors or moving
            # objects that are now hidden, occupied, private, or too hot.
            self._perform(inverse["action"], inverse.get("target", ""), inverse.get("destination", ""), {})
            self._undo.pop()
            s["stats"]["undo_count"] += 1
        elif action == "snapshot":
            require(bool(target), "Name the snapshot")
            self._snapshots[target] = copy.deepcopy(s)
        elif action == "diff":
            require(target in self._snapshots, "No such snapshot")
            # Only return changes to currently visible object fields.
            public = self.observe().get("visible_entities", {})
            return json.dumps([d for d in differences(self._snapshots[target], s)
                               if d["path"].startswith("entities.") and len(d["path"].split(".")) >= 3
                               and d["path"].split(".")[1] in public and d["path"].split(".")[2] in public[d["path"].split(".")[1]]])
        else: raise ActionError("Unknown action")
        return "{} {}{}".format(action, target, " -> " + destination if destination else "").strip()

    def _watch_visible(self, rule):
        if "all" in rule or "any" in rule:
            return all(self._watch_visible(r) for r in rule.get("all", rule.get("any", [])))
        if "not" in rule: return self._watch_visible(rule["not"])
        path = rule["path"].split(".")
        return (path[0] in ("weather", "time_s") or
                (len(path) == 3 and path[0] == "entities" and path[1] in self._state["entities"] and self._visible(path[1]) and path[2] in ("cycle", "open", "on", "wetness")))

    def _advance(self, seconds):
        s = self._state
        s["time_s"] += seconds
        for eid, obj in s["entities"].items():
            if obj.get("cycle") == "running":
                if not s["utilities"]["power"]:
                    obj["cycle"] = "paused"
                    continue
                obj["remaining_s"] = max(0, obj["remaining_s"] - seconds)
                if obj["remaining_s"] == 0:
                    obj["cycle"] = "done"
                    for item in s["entities"].values():
                        if item["location"] == eid:
                            if obj["category"] == "dryer": item["wetness"] = 0.0
                            else:
                                s["utilities"]["wastewater_soil"] += item["soil"]
                                item.update(soil=0.0, wetness=1.0)
                    s["event_log"].append({"time_s": s["time_s"], "kind": "cycle_complete", "target": eid})
        for event in s["events"]:
            if not event.get("fired") and (("at_step" in event and self.steps >= event["at_step"]) or
                                          ("at_s" in event and s["time_s"] >= event["at_s"])):
                for path, value in event.get("changes", {}).items(): set_path(s, path, value)
                event["fired"] = True
                s["event_log"].append({"time_s": s["time_s"], "kind": event.get("kind", "event")})
        for order in s["orders"]:
            if not order["fired"] and self._watch_visible(order["condition"]) and check(s, order["condition"]):
                s["pending"].extend(copy.deepcopy(order["commands"]))
                order["fired"] = True
                s["facts"]["orders_fired"] = s["facts"].get("orders_fired", 0) + 1

    def step(self, action, target="", destination="", **kwargs):
        command = {"action": action, "target": target, "destination": destination, **kwargs}
        if not all(isinstance(v, str) for v in (action, target, destination)):
            evaluation = self.evaluate()
            return {"ok": False, "message": "Action arguments must be strings", "observation": self.observe(), "evaluation": evaluation,
                    "reward": 0.0, "terminated": evaluation["success"], "truncated": evaluation["budget_exceeded"]}
        action, target, destination = (v.strip().lower() for v in (action, target, destination))
        command.update(action=action, target=target, destination=destination)
        scheduled = action == "run_pending" and bool(self._state["pending"])
        if scheduled:
            queued = copy.deepcopy(self._state["pending"][0])
            action, target, destination = queued.pop("action"), queued.pop("target", ""), queued.pop("destination", "")
            kwargs = queued
        allowed_kwargs = {"tool", "topic", "mode", "text", "field", "value", "seconds", "condition", "commands", "amount_l"}
        before = copy.deepcopy(self._state)
        score_before = self.evaluate()["score"]
        self._state["steps"] += 1
        seconds = self.action_defs.get(action, {}).get("duration_s", 1)
        if action == "undo" and self._undo:
            seconds = self.action_defs.get(self._undo[-1]["inverse"]["action"], {}).get("duration_s", 1)
        ok, category, changes = False, "", []
        try:
            require(not set(kwargs) - allowed_kwargs, "Unknown action parameter")
            require(all(isinstance(v, str) for v in (action, target, destination)), "Scheduled action arguments must be strings")
            require(all(isinstance(kwargs[k], str) for k in ("tool", "topic", "mode", "text", "field") if k in kwargs), "Text parameters must be strings")
            if action == "wait":
                seconds = kwargs.get("seconds", 1)
                require(type(seconds) is int and 1 <= seconds <= 3600, "Wait must be 1..3600 seconds")
            require(not self._state["robot"]["stopped"] or action in ("resume", "look", "listen", "speak", "announce", "ask", "handoff", "wait", "stop"), "Robot is stopped")
            message = self._perform(action, target, destination, kwargs)
            if scheduled:
                self._state["pending"].pop(0)
            self._state["facts"].setdefault("action_times", {}).setdefault(target or "robot", {})[action] = self._state["time_s"]
            changes = differences(before, self._state)
            ok = True
            cost = 0.0 if action in ("wait", "look", "ask", "listen", "stop") else 0.01
            self._state["robot"]["battery"] = max(0, self._state["robot"]["battery"] - cost)
            self._state["stats"]["energy"] += cost
            reversible = action in ("move", "pick_up", "place", "open", "close", "lock", "unlock", "switch_on", "switch_off") or self.action_defs.get(action, {}).get("reversible", False)
            if reversible:
                reversible_changes = [d for d in changes if d["path"].startswith(("entities.", "robot.room", "robot.holding", "rooms."))]
                if reversible_changes:
                    inverse_name = {"move": "move", "pick_up": "place", "place": "pick_up", "open": "close", "close": "open", "lock": "unlock", "unlock": "lock", "switch_on": "switch_off", "switch_off": "switch_on", "fold": "unfold", "unfold": "fold", "coil": "uncoil", "uncoil": "coil"}.get(action)
                    if inverse_name:
                        inverse = {"action": inverse_name, "target": before["robot"]["room"] if action == "move" else target}
                        if action == "pick_up": inverse["destination"] = before["entities"][target]["location"]
                        self._undo.append({"changes": reversible_changes, "inverse": inverse})
            elif action in self.action_defs or action in ("fill", "pour", "water_plant", "start", "pause", "hand_over"):
                self._undo.clear()
        except ActionError as exc:
            # Failed actions are atomic. Only time, attempt counters, and subsequent
            # exogenous events can change the state.
            self._state = before
            self._state["steps"] += 1
            seconds = 1
            category, message = exc.category, str(exc)
            self._state["stats"]["invalid"] += 1
            if category == "safety": self._state["stats"]["safety_blocks"] += 1
            if category == "privacy": self._state["stats"]["privacy_blocks"] += 1
        self._advance(seconds)
        outcome = self.evaluate()
        self._history.append({"command": copy.deepcopy(command), "ok": ok, "category": category,
                              "message": message, "time_s": self._state["time_s"], "state_hash": digest(self._state),
                              "changes": differences(before, self._state)})
        return {"ok": ok, "message": message, "category": category, "observation": self.observe(),
                "evaluation": outcome, "reward": round(outcome["score"] - score_before, 4), "terminated": outcome["success"],
                "truncated": outcome["budget_exceeded"]}

    def evaluate(self):
        s, task = self._state, self._task
        failures, successes = [], []
        if task:
            successes = [check(s, rule) for rule in task.get("success", [])]
            failures.extend("failure_predicate_" + str(i) for i, rule in enumerate(task.get("failure", [])) if check(s, rule))
            for path in task.get("preserve", []):
                if get_path(s, path) != get_path(self._baseline, path): failures.append("changed:" + path)
            budget = self.steps > task.get("step_budget", 200) or s["time_s"] > task.get("time_budget_s", 3600)
            if budget: failures.append("budget")
        else:
            goal = self._initial.get("goal")
            successes = [s["entities"][goal["object"]]["location"] == goal["destination"]] if goal else []
            budget = False
        for key in ("damage", "injury", "fire", "flood"):
            if s["stats"][key]: failures.append(key)
        # Safety/privacy attempts are separately measured; actual adverse state is
        # always fatal. Individual tasks may also forbid blocked attempts.
        success = bool(successes) and all(successes) and not failures
        fraction = sum(successes) / len(successes) if successes else 0
        penalty = min(1, 0.05 * s["stats"]["invalid"] + 0.02 * s["stats"]["unnecessary_questions"])
        return {"success": success, "goal_fraction": fraction, "failures": failures,
                "budget_exceeded": budget, "score": round((1.0 if success else 0.0) - penalty, 4),
                "steps": self.steps, "time_s": s["time_s"], "metrics": copy.deepcopy(s["stats"]), "fidelity": "S"}

    def task_complete(self): return self.evaluate()["success"]

    def action_names(self):
        return set(self.action_defs) | {"look", "wait", "move", "inspect", "pick_up", "place", "hand_over", "open", "close", "lock", "unlock", "switch_on", "switch_off", "start", "pause", "set_mode", "ask", "speak", "listen", "announce", "refuse", "handoff", "stop", "resume", "set_quiet", "report", "remember", "forget", "watch", "run_pending", "undo", "snapshot", "diff", "fill", "pour", "water_plant"}

    def affordances(self, target):
        """Side-effect-free action availability, tested against current state."""
        self._object(target)
        candidates = ["inspect", "pick_up", "open", "close", "lock", "unlock", "switch_on", "switch_off", "start", "pause"] + sorted(self.action_defs)
        result = []
        for action in candidates:
            saved = copy.deepcopy(self._state)
            try:
                self._perform(action, target, "", {"tool": self.holding or ""})
                result.append({"action": action, "available": True, "reason": ""})
            except ActionError as exc:
                result.append({"action": action, "available": False, "reason": str(exc)})
            finally: self._state = saved
        return result

    def save(self, path):
        payload = {"format": "house-state-v1", "initial": self._initial, "task": self._task, "seed": self.seed,
                   "state": self._state, "baseline": self._baseline, "history": self._history,
                   "known_map": self._known_map, "undo": self._undo, "snapshots": self._snapshots,
                   "replay_start": self._replay_start, "sensors": self.sensors,
                   "actions_hash": digest(self.action_defs)}
        envelope = {"payload": payload, "sha256": digest(payload)}
        Path(path).write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if envelope.get("sha256") != digest(payload) or payload.get("format") != "house-state-v1":
            raise ValueError("Corrupt or unsupported state file")
        env = cls(payload["initial"], payload["task"], payload["seed"], payload["sensors"])
        if payload["actions_hash"] != digest(env.action_defs): raise ValueError("Action definitions changed")
        # Verify dynamic structural invariants, allowing exactly one hand root.
        state = copy.deepcopy(payload["state"])
        check_config = copy.deepcopy(state)
        held = state["robot"]["holding"]
        hand_entities = [eid for eid, e in state["entities"].items() if e["location"] == "hand"]
        if hand_entities != ([held] if held else []): raise ValueError("Inconsistent held object")
        if held: check_config["entities"][held]["location"] = state["robot"]["room"]
        validate(check_config)
        env._state, env._baseline, env._history = state, payload["baseline"], payload["history"]
        env._known_map, env._undo, env._snapshots = payload["known_map"], payload["undo"], payload["snapshots"]
        env._replay_start = payload["replay_start"]
        return env

    def save_replay(self, path):
        data = {"format": "house-replay-v1", "initial": self._initial, "start_state": self._replay_start,
                "baseline": self._baseline, "task": self._task, "seed": self.seed,
                "actions_hash": digest(self.action_defs), "steps": self._history,
                "evaluation": self.evaluate()}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def replay(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "house-replay-v1": raise ValueError("Unsupported replay")
        env = cls(data["initial"], data["task"], data["seed"])
        if digest(env.action_defs) != data["actions_hash"]: raise ValueError("Replay action definitions differ")
        env._state, env._baseline = data["start_state"], data["baseline"]
        env._replay_start = copy.deepcopy(env._state)
        for number, record in enumerate(data["steps"], 1):
            result = env.step(**record["command"])
            if result["ok"] != record["ok"] or digest(env._state) != record["state_hash"]:
                raise ValueError("Replay diverged at step " + str(number))
        if env.evaluate() != data["evaluation"]: raise ValueError("Replay evaluation differs")
        return env

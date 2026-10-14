"""Partial observation: visibility, lighting, redaction and observation channels.

Only the robot's current room is observed. Closed opaque containers hide their
contents; concealed items (relation under/behind) appear once revealed by
inspecting their parent with where="under"/"behind". In darkness without a lit
flashlight only large shapes are seen. A dirty camera hides small items and text.
Ground truth used by evaluators ("secret_fields", keys starting with "_") is never
observed. Domain observers add channels such as sounds and smells.
"""
import copy

from . import clock, geometry
from .plugins import HOOKS, OBSERVERS, run_hooks

# Domains may hide entities with @hook("visible_veto") fn(state, eid, entity) -> True to hide.
VETO = HOOKS["visible_veto"]

ALWAYS_HIDDEN = {"permissions", "hidden", "private_contents", "secret_fields", "is_a"}
TEXT_FIELDS = {"text", "label_text", "expiration", "expires", "date_label", "address", "recipient", "care_label", "instructions"}
DARK = 0.2


def light_level(state, rid):
    room = state["rooms"].get(rid, {})
    if room.get("outdoor"):
        level = clock.daylight(state)
    else:
        windows = [e for e in state["entities"].values() if e.get("location") == rid and e.get("category") == "window"]
        exposure = 0.0
        for window in windows:
            covered = window.get("covered", 0.0)  # 0 open curtains/blinds .. 1 fully covered
            exposure += 0.45 * (1.0 - float(covered))
        level = clock.daylight(state) * min(1.0, exposure)
    for eid, entity in state["entities"].items():
        if entity.get("category") == "light" and entity.get("on") and not entity.get("burnt_out") and geometry_room(state, eid) == rid:
            if entity.get("powered", True) and state.get("utilities", {}).get("power", True) or entity.get("battery_powered"):
                level += float(entity.get("brightness", 0.8)) * float(entity.get("dim_level", 1.0))
    for extra in run_hooks("light_level", state, rid):
        if isinstance(extra, (int, float)):
            level += extra
    return round(max(0.0, min(1.0, level)), 3)


def geometry_room(state, eid):
    from .predicates import root_room
    return root_room(state, eid)


def flashlight_on(state):
    for eid in state["robot"].get("hands", {}).values():
        if eid and state["entities"].get(eid, {}).get("category") == "flashlight" and state["entities"][eid].get("on"):
            return True
    return bool(state["robot"].get("headlamp_on"))


def _max_dim(entity):
    size = entity.get("size_m") or [0.1, 0.1, 0.1]
    return max(float(v) for v in size[:3])


class Perception:
    def __init__(self, env):
        self.env = env
        self._light_cache = {}
        self._lit_now = None      # {room: lit} while one observe() runs (lighting cannot change mid-observation)

    def lit(self, state, rid):
        if self._lit_now is not None:
            if rid not in self._lit_now:
                self._lit_now[rid] = light_level(state, rid) >= DARK or flashlight_on(state)
            return self._lit_now[rid]
        return light_level(state, rid) >= DARK or flashlight_on(state)

    def visible(self, state, eid):
        entities = state["entities"]
        entity = entities.get(eid)
        if entity is None:
            return False
        robot = state["robot"]
        if eid in [v for v in robot.get("hands", {}).values() if v]:
            return True
        room = robot["room"]
        if "connects" in entity and room in entity["connects"]:
            return True
        if entity.get("near_connection") and entity.get("location") != room:
            from .geometry import reach_problem
            if reach_problem(state, eid) is None:
                return True
        approvals = state.get("approvals", [])
        # privacy hides what a private thing holds in oracle observations only: with realistic perception an open
        # private box shows its contents like any other (hiding them would itself mark the box private) and privacy is
        # enforced on acting (ctx.forbid)
        private_hides = getattr(self.env, "realism", None) is None
        # concealment of the entity itself
        if entity.get("relation") in ("under", "behind") and not entity.get("revealed"):
            return False
        child, location, seen = entity, entity.get("location"), {eid}
        while location in entities:
            if location in seen:
                return False
            seen.add(location)
            parent = entities[location]
            relation = child.get("relation", "on")
            if private_hides and parent.get("private") and "touch:" + location not in approvals:
                return False
            # a closed opaque container hides what is IN it, not what rests ON it or hangs on it
            if relation == "in" and parent.get("openable") and not parent.get("open", False) and not parent.get("transparent"):
                return False
            if relation == "part":
                slot = (parent.get("slots") or {}).get(child.get("slot") or "", {})
                if slot.get("access") == "cover" and not parent.get("cover_open"):
                    return False
                if slot.get("access") == "open" and parent.get("openable") and not parent.get("open"):
                    return False
            if child.get("submerged") and parent.get("liquid_opaque"):
                return False
            if parent.get("relation") in ("under", "behind") and not parent.get("revealed"):
                return False
            if parent.get("opaque_cover") and parent.get("covered_by"):
                return False
            child, location = parent, parent.get("location")
        if location == "robot":
            return True
        for group in ("people", "pets"):
            if location in state.get(group, {}):
                location = state[group][location].get("location")
        if location != room:
            return False
        if not self.lit(state, room) and _max_dim(entity) < 0.4:
            return False
        if robot.get("camera_clean", 1.0) < 0.4 and _max_dim(entity) < 0.08:
            return False
        if VETO:
            for _, _, fn in VETO:
                if fn(state, eid, entity):
                    return False
        return True

    def redact(self, state, eid, entity, dark, dirty):
        approvals = state.get("approvals", [])
        data = {k: copy.deepcopy(v) for k, v in entity.items()
                if k not in ALWAYS_HIDDEN and not k.startswith("_") and k not in entity.get("secret_fields", [])}
        if entity.get("private") and "touch:" + eid not in approvals:
            return {k: data[k] for k in ("label", "location", "private", "openable", "category", "type", "pos") if k in data}
        if dark:
            return {"label": "dark shape", "location": data.get("location"), "size_m": data.get("size_m"), "pos": data.get("pos"), "type": "unknown"}
        if dirty:
            for key in TEXT_FIELDS:
                data.pop(key, None)
        return data

    def observe(self, env, records=True):
        """records=False (realistic perception builds its own records): visible_entities maps ids to {} or
        {"label": "dark shape"} only."""
        self._lit_now = {}
        try:
            return self._observe(env, records)
        finally:
            self._lit_now = None

    def _observe(self, env, records=True):
        state = env.world.s
        robot = state["robot"]
        rid = robot["room"]
        room = state["rooms"][rid]
        level = light_level(state, rid)
        dark = level < DARK and not flashlight_on(state)
        dirty = robot.get("camera_clean", 1.0) < 0.4
        public_room = {k: copy.deepcopy(v) for k, v in room.items() if not k.startswith("_") and k not in ("secret_fields",)}
        cols, rows = geometry.grid_size(room)
        public_room["grid"] = {"cols": cols, "rows": rows, "cell_m": geometry.CELL}
        public_room["light_level"] = level
        conns = []
        for cid, conn in sorted(state.get("connections", {}).items()):
            if rid in conn["rooms"]:
                other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
                item = {"id": cid, "to": other, "kind": conn.get("kind", "doorway"), "width_m": conn.get("width_m"),
                        "cell": conn.get("cells", {}).get(rid), "door": conn.get("door")}
                door = state["entities"].get(conn.get("door") or "", {})
                if door:
                    item.update(open=door.get("open", False), locked=door.get("locked", False))
                conns.append(item)
        env._known_map[rid] = {"exits": list(room.get("exits", [])), "connections": conns, "label": room.get("label"), "type": room.get("type")}
        visible = {}
        for eid, entity in state["entities"].items():
            if self.visible(state, eid):
                shape = dark and _max_dim(entity) >= 0.4 and not env.world.is_held(eid)
                if records:
                    visible[eid] = self.redact(state, eid, entity, shape, dirty)
                else:
                    visible[eid] = {"label": "dark shape"} if shape else {}
        blocked = sorted(list(c) for c in geometry.blocked_cells(state, rid, include_agents=False))
        hazards = []
        if not dark:
            # people and pets the robot cannot see (concealed, not revealed) are not listed, nor are their hazards
            agents = {aid for group in ("people", "pets") for aid, agent in state.get(group, {}).items()
                      if not (agent.get("concealed") and not agent.get("revealed"))}
            for cell, items in sorted(geometry.hazard_cells(state, rid).items()):
                for kind, source in items:
                    if source == rid or source in visible or source in agents:
                        hazards.append({"cell": list(cell), "kind": kind, "source": source})
        people = {}
        for pid, person in sorted(state.get("people", {}).items()):
            if person.get("location") == rid and not (person.get("concealed") and not person.get("revealed")):
                people[pid] = {k: copy.deepcopy(person[k]) for k in ("label", "role", "location", "pos", "posture", "mood", "awake", "activity", "holding", "abilities", "speaking") if k in person}
        pets = {}
        for pid, pet in sorted(state.get("pets", {}).items()):
            if pet.get("location") == rid and not (pet.get("concealed") and not pet.get("revealed")):
                pets[pid] = {k: copy.deepcopy(pet[k]) for k in ("label", "species", "location", "pos", "posture", "mood", "leashed", "activity", "carrying") if k in pet}
        obs = {"room_id": rid, "room": public_room, "connections": conns,
               "robot": {k: copy.deepcopy(robot[k]) for k in ("pos", "posture", "hands", "battery", "stopped", "quiet", "privacy_mode",
                                                                "speed_mode", "standing_on", "camera_clean", "network", "faults", "fallen", "docked") if k in robot},
               "holding": [v for v in robot.get("hands", {}).values() if v],
               "visible_entities": visible, "people": people, "pets": pets,
               "grid": {"blocked": blocked, "hazards": hazards},
               "time": clock.describe(state), "steps": state.get("steps", 0), "time_s": state.get("time_s", 0),
               "battery": round(robot.get("battery", 100.0), 3), "stopped": robot.get("stopped", False),
               "pending": copy.deepcopy(state.get("pending", [])), "dark": dark}
        if room.get("outdoor") or any(e.get("category") == "window" and e.get("location") == rid for e in state["entities"].values()):
            obs["weather"] = copy.deepcopy(state.get("weather", {}))
        if "language" in env.sensors:
            obs["instruction"] = env._task.get("instruction", "") if env._task else ""
            obs["last_answer"] = copy.deepcopy(state.get("facts", {}).get("last_answer"))
            obs["messages"] = [m for m in state.get("messages", []) if m.get("heard_by_robot") and m.get("step", -1) >= state.get("steps", 0) - 1]
        for _, _, fn in OBSERVERS:
            fn(env, env.world, obs)
        return obs

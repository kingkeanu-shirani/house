"""Partial observation: visibility, lighting, redaction and observation channels.

Only the robot's current room is observed. Closed opaque containers hide their
contents; concealed items (relation under/behind) appear once revealed by
inspecting their parent with where="under"/"behind". In darkness without a lit
flashlight only large shapes are seen. A dirty camera hides small items and text.
Ground truth used by evaluators ("secret_fields", keys starting with "_") is never
observed. Domain observers add channels such as sounds and smells.

Geometric camera mode (kernel v3.0, kernel/camera.py; docs/design/TIER1_CAMERA.md 9.3 and 10.6, TIER1_INTEGRATION
1.3 and 1.12): visible() is camera.can_see (L0 containment and concealment of contained(), then line of sight, light
and resolution from the robot's eye, wrist access or, with imaging off, map fixtures), and observe() lists only what
the step view detected: the entities, people and pets in a frame taken this step at the current eye (plus what the
robot holds), each with a "view" dict; IR "dark shape" records for things seen by the depth camera only; the camera
and lidar blocks; hazards only on cells seen this step or with a detected source. With realistic perception
kernel/realism.py renders that step view by view level (dark shape, object, category, full glance; TIER1_CAMERA 11)
and draws the 10-24 px detection band. Room mode (the default) is unchanged.

The robot's own sensors (kernel v3.0 robot body; TIER1_INTEGRATION 1.3 and 1.12, TIER1_EMBODIMENT 4.10 and 4.14) gate
what any of this can show, in both perception modes and both camera modes, derived from kernel/body.py on every call:
  * powered down: only what the robot holds (and wears) is visible; no people, pets, hazards, weather, sounds,
    messages or observer channels (the robot's map -- room, exits, connections, blocked cells -- and its own
    proprioception stay);
  * imaging off with the depth camera on (privacy mode, cameras switched off or failed, a room privacy policy): room
    mode applies the darkness rule to the whole room -- things of 0.4 m and up as "dark shape" records, people and
    pets as shapes in realistic perception, no hazards, no weather through a window -- and the map fixtures of the
    room (non-portable furniture and fixtures standing in it, its doors) stay targetable by actions without being
    listed; the geometric camera applies its depth-only IR rule itself (camera.can_see / the step view). obs["dark"]
    is True whenever the cameras give no colour view (a dark room without a robot light, or imaging off), which is
    what observers and realism key their visual percepts on;
  * hearing (body.can_hear): "none" empties obs.messages and drops every sounds channel (obs.sounds,
    obs.mechanism_sounds, a channel's own "sounds" list); "addressed" keeps only speech addressed to the robot in
    obs.messages (to == "robot" or addressed_robot), non-speech sounds stay.
obs.robot and the top-level obs.battery come from kernel/proprio.py: oracle observations show the robot record
exactly (today's keys plus every registered robot field that is not hidden), realistic ones what its own sensors and
battery management report (integer gauge, estimates, buckets, detected faults only; pos None while unlocalized); in
geometric mode obs.camera.pose.cell mirrors obs.robot.pos.
"""
import copy

from . import body, clock, geometry, proprio
from .plugins import HOOKS, OBSERVERS, run_hooks

# Domains may hide entities with @hook("visible_veto") fn(state, eid, entity) -> True to hide.
VETO = HOOKS["visible_veto"]

ALWAYS_HIDDEN = {"permissions", "hidden", "private_contents", "secret_fields", "is_a"}
TEXT_FIELDS = {"text", "label_text", "expiration", "expires", "date_label", "address", "recipient", "care_label", "instructions"}
DARK = 0.2
SHAPE_M = 0.4               # the smallest thing seen as a shape in the dark or by the depth camera alone
DIRTY_LENS = 0.4            # below this lens clarity, small things (< 8 cm) and text are not seen
# obs.robot's keys before the body (kernel v3.0): proprio.LEGACY_KEYS plus the camera's facing_deg. obs.robot is built
# by proprio.render now; kept for tools that list the legacy keys.
ROBOT_KEYS = proprio.LEGACY_KEYS + ("facing_deg",)
SOUND_CHANNELS = ("sounds", "mechanism_sounds")
_CAMERA = []


def _camera():
    """kernel/camera.py (imported on first use: it imports this module)."""
    if not _CAMERA:
        from . import camera
        _CAMERA.append(camera)
    return _CAMERA[0]


def light_level(state, rid, ids=None):
    """Ambient light of a room (0..1): daylight through its uncovered windows (outdoors: daylight), its lit lights
    and @hook("light_level") additions. ids: the ids of every window and light in state order, when the caller keeps
    that list (the geometric camera does, per World version): the same sums without scanning every entity."""
    entities = state["entities"]
    pool = entities.items() if ids is None else ((eid, entities[eid]) for eid in ids if eid in entities)
    room = state["rooms"].get(rid, {})
    if room.get("outdoor"):
        level = clock.daylight(state)
    else:
        if ids is None:
            windows = [e for e in state["entities"].values() if e.get("location") == rid and e.get("category") == "window"]
        else:
            windows = [entities[e] for e in ids if e in entities and entities[e].get("location") == rid
                       and entities[e].get("category") == "window"]
        exposure = 0.0
        for window in windows:
            covered = window.get("covered", 0.0)  # 0 open curtains/blinds .. 1 fully covered
            exposure += 0.45 * (1.0 - float(covered))
        level = clock.daylight(state) * min(1.0, exposure)
    for eid, entity in pool:
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


def heard_messages(state, hearing=None):
    """The messages of state["messages"] the robot's microphones let through, by body.can_hear (TIER1_INTEGRATION
    1.3): "none" nothing, "addressed" (privacy mode, a microphone policy) only speech addressed to the robot
    (to == "robot" or marked addressed_robot), "full" everything. Whether a message reached the robot at all
    (heard_by_robot, the step window) is the caller's filter."""
    hearing = body.can_hear(state) if hearing is None else hearing
    messages = state.get("messages", [])
    if hearing == "none":
        return []
    if hearing == "addressed":
        return [m for m in messages if isinstance(m, dict) and (m.get("to") == "robot" or m.get("addressed_robot"))]
    return list(messages)


def drop_sounds(obs):
    """Remove every sounds channel from an observation (hearing "none"): the top-level sounds channels and a channel
    dict's own "sounds" list (climate's)."""
    for key in SOUND_CHANNELS:
        obs.pop(key, None)
    for value in obs.values():
        if isinstance(value, dict):
            for key in SOUND_CHANNELS:
                value.pop(key, None)


class Perception:
    def __init__(self, env):
        self.env = env
        self._light_cache = {}
        self._lit_now = None      # {room: lit} while one observe() runs (lighting cannot change mid-observation)
        self._sense_now = None    # (powered, imaging, lens clarity) while one observe() runs

    def lit(self, state, rid):
        if self._lit_now is not None:
            if rid not in self._lit_now:
                self._lit_now[rid] = light_level(state, rid) >= DARK or flashlight_on(state)
            return self._lit_now[rid]
        return light_level(state, rid) >= DARK or flashlight_on(state)

    def _sense(self, state):
        """(powered, imaging, lens clarity): the robot's sensors now (body.is_on, body.imaging, body.clarity;
        TIER1_INTEGRATION 1.3), fixed while one observe() runs."""
        if self._sense_now is not None:
            return self._sense_now
        return body.is_on(state), body.imaging(state), body.clarity(state)

    def senses(self, state):
        """(powered, imaging) of the robot now."""
        return self._sense(state)[:2]

    def lens(self, state):
        """The lens clarity (camera_clean capped by lens scratches)."""
        return self._sense(state)[2]

    def seen_dark(self, state, rid):
        """True when the robot's cameras give it no colour view of room rid: imaging is off (powered down, privacy
        mode, cameras switched off or failed, a room privacy policy) or the room is dark without a robot light. Realism
        keys people and pets there as dark-spell shapes and plans no hazard avoidance from sight."""
        return not self.senses(state)[1] or not self.lit(state, rid)

    def visible(self, state, eid):
        entities = state["entities"]
        entity = entities.get(eid)
        if entity is None:
            return False
        robot = state["robot"]
        if "view" in robot:         # geometric camera mode: line of sight, light and resolution (camera.can_see)
            return _camera().can_see(state, eid, getattr(self.env, "world", None), self)
        if eid in [v for v in robot.get("hands", {}).values() if v]:
            return True
        sense = self._sense_now or self._sense(state)
        if not sense[0]:
            # powered down: no sensor works; only what the robot holds or wears (TIER1_INTEGRATION 1.3)
            return entity.get("location") == "robot"
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
        if not sense[1]:
            # the depth camera alone: the darkness rule over the whole room, plus the map fixtures of the room (non-
            # portable furniture and fixtures standing in it), which the robot's map lets it act on (1.3)
            if _max_dim(entity) < SHAPE_M and not (entity.get("location") == room and not entity.get("portable")):
                return False
        else:
            if not self.lit(state, room) and _max_dim(entity) < SHAPE_M:
                return False
            if sense[2] < DIRTY_LENS and _max_dim(entity) < 0.08:
                return False
        if VETO:
            for _, _, fn in VETO:
                if fn(state, eid, entity):
                    return False
        return True

    def contained(self, state, eid):
        """L0 of the geometric camera (TIER1_CAMERA 2, 9.3): visible() without the rules the camera decides by
        geometry and optics. Kept: held things; doors of the robot's room; reachable near-connection controls; closed
        opaque containers, covered and closed slots, opaque liquids, opaque covers and (oracle) private parents hide
        what they hold; people's and pets' things; only the robot's room; visible_veto. Dropped (camera.can_see
        decides): under/behind concealment until revealed (C9: geometry), darkness and the dirty lens (light and
        resolution)."""
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
        private_hides = getattr(self.env, "realism", None) is None
        approvals = state.get("approvals", [])
        child, location, seen = entity, entity.get("location"), {eid}
        while location in entities:
            if location in seen:
                return False
            seen.add(location)
            parent = entities[location]
            relation = child.get("relation", "on")
            if private_hides and parent.get("private") and "touch:" + location not in approvals:
                return False
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

    def _geometric(self, env, state, sv, records, dirty):
        """(visible_entities, people, pets, hazards) of the geometric camera's step view: only what a current frame
        detected (and what the robot holds), each with an exact "view" (oracle); things seen by the depth camera alone
        as "dark shape" records (people as "person", pets as "animal"); hazards on cells this step's frames surveyed
        or with a detected source. records=False (realistic rendering, kernel/realism.py): every ViewInfo of the step
        view under "_view", the 10-24 px detection band included -- realism draws the band (TIER1_CAMERA 11.2) and
        drops the hazards of what it did not detect."""
        camera = _camera()
        rid = state["robot"]["room"]
        visible = {}
        for eid in sorted(sv.entities):
            info = sv.entities[eid]
            entity = state["entities"].get(eid)
            if entity is None or (records and not info["detected"]):
                continue
            shape = info["level"] == "shape"
            if records:
                record = self.redact(state, eid, entity, shape, dirty)
                record["view"] = camera.view_dict(info)
            else:
                record = {"label": "dark shape", "_view": info} if shape else {"_view": info}
            visible[eid] = record
        groups = {}
        for group, table, keys, word in (
                ("people", sv.people, ("label", "role", "location", "pos", "posture", "mood", "awake", "activity",
                                       "holding", "abilities", "speaking"), "person"),
                ("pets", sv.pets, ("label", "species", "location", "pos", "posture", "mood", "leashed", "activity",
                                   "carrying"), "animal")):
            out = {}
            for aid in sorted(table):
                info = table[aid]
                agent = state.get(group, {}).get(aid)
                if agent is None or (records and not info["detected"]):
                    continue
                if info["level"] == "shape":
                    record = {"label": word, "location": agent.get("location"), "pos": copy.deepcopy(agent.get("pos"))}
                else:
                    record = {k: copy.deepcopy(agent[k]) for k in keys if k in agent}
                record["view" if records else "_view"] = camera.view_dict(info) if records else info
                out[aid] = record
            groups[group] = out
        seen = sv.seen.get(rid, set())
        detected = set(visible) | set(groups["people"]) | set(groups["pets"])
        hazards = []
        for cell, items in sorted(geometry.hazard_cells(state, rid).items()):
            for kind, source in items:
                if tuple(cell) in seen or source in detected:
                    hazards.append({"cell": list(cell), "kind": kind, "source": source})
        return visible, groups["people"], groups["pets"], hazards

    def observe(self, env, records=True):
        """records=False (realistic perception builds its own records): visible_entities maps ids to {} or
        {"label": "dark shape"} only, and obs.robot / obs.battery are the realistic proprioception."""
        state = env.world.s
        self._lit_now = {}
        self._sense_now = None
        self._sense_now = self._sense(state)
        try:
            return self._observe(env, records)
        finally:
            self._lit_now = None
            self._sense_now = None

    def _observe(self, env, records=True):
        state = env.world.s
        robot = state["robot"]
        rid = robot["room"]
        room = state["rooms"][rid]
        powered, imaging = self.senses(state)
        level = light_level(state, rid)
        dark = (level < DARK and not flashlight_on(state)) or not imaging
        dirty = self.lens(state) < DIRTY_LENS
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
        sv = _camera().step_view(env) if "view" in robot else None     # geometric camera: the step view (10.6)
        if sv is not None:
            visible, people, pets, hazards = self._geometric(env, state, sv, records, dirty)
        else:
            visible = {}
            for eid, entity in state["entities"].items():
                if self.visible(state, eid):
                    held = env.world.is_held(eid) or entity.get("location") == "robot"
                    shape = dark and _max_dim(entity) >= SHAPE_M and not held
                    if not imaging and not held and not shape:
                        continue      # a small map fixture: targetable with the cameras off, but not seen
                    if records:
                        visible[eid] = self.redact(state, eid, entity, shape, dirty)
                    else:
                        visible[eid] = {"label": "dark shape"} if shape else {}
        blocked = sorted(list(c) for c in geometry.blocked_cells(state, rid, include_agents=False))
        if sv is None:
            hazards = []
            if not dark:
                # people and pets the robot cannot see (concealed, not revealed) are not listed, nor are their hazards
                agents = {aid for group in ("people", "pets") for aid, agent in state.get(group, {}).items()
                          if not (agent.get("concealed") and not agent.get("revealed"))}
                for cell, items in sorted(geometry.hazard_cells(state, rid).items()):
                    for kind, source in items:
                        if source == rid or source in visible or source in agents:
                            hazards.append({"cell": list(cell), "kind": kind, "source": source})
            people, pets = {}, {}
            # powered down: the robot senses nobody (with only the depth camera people stay listed: darkness rule)
            for pid, person in sorted(state.get("people", {}).items()) if powered else ():
                if person.get("location") == rid and not (person.get("concealed") and not person.get("revealed")):
                    people[pid] = {k: copy.deepcopy(person[k]) for k in ("label", "role", "location", "pos", "posture", "mood", "awake", "activity", "holding", "abilities", "speaking") if k in person}
            for pid, pet in sorted(state.get("pets", {}).items()) if powered else ():
                if pet.get("location") == rid and not (pet.get("concealed") and not pet.get("revealed")):
                    pets[pid] = {k: copy.deepcopy(pet[k]) for k in ("label", "species", "location", "pos", "posture", "mood", "leashed", "activity", "carrying") if k in pet}
        own, battery = proprio.render(env, state, not records)
        obs = {"room_id": rid, "room": public_room, "connections": conns,
               "robot": own,
               "holding": [v for v in robot.get("hands", {}).values() if v],
               "visible_entities": visible, "people": people, "pets": pets,
               "grid": {"blocked": blocked, "hazards": hazards},
               "time": clock.describe(state), "steps": state.get("steps", 0), "time_s": state.get("time_s", 0),
               "battery": battery, "stopped": robot.get("stopped", False),
               "pending": copy.deepcopy(state.get("pending", [])), "dark": dark}
        if sv is not None:
            camera = _camera()
            obs["camera"] = camera.camera_block(state, sv)
            if "pos" in own:
                obs["camera"]["pose"]["cell"] = copy.deepcopy(own["pos"])   # mirrors obs.robot.pos (None unlocalized)
            lidar = camera.lidar_block(sv)
            if lidar is not None:
                obs["lidar"] = lidar
        # the weather is felt outdoors and seen through a window (with the cameras on); nothing while powered down
        if powered and (room.get("outdoor") or imaging and any(e.get("category") == "window" and e.get("location") == rid
                                                               for e in state["entities"].values())):
            obs["weather"] = copy.deepcopy(state.get("weather", {}))
        hearing = body.can_hear(state)
        if "language" in env.sensors:
            obs["instruction"] = env._task.get("instruction", "") if env._task else ""
            obs["last_answer"] = copy.deepcopy(state.get("facts", {}).get("last_answer"))
            obs["messages"] = [m for m in heard_messages(state, hearing)
                               if m.get("heard_by_robot") and m.get("step", -1) >= state.get("steps", 0) - 1]
        if powered:            # powered down: no sensor feeds any observer channel
            for _, _, fn in OBSERVERS:
                fn(env, env.world, obs)
        if hearing == "none":
            drop_sounds(obs)
        return obs

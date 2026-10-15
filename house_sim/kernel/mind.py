"""The robot's memory outside world state: the known map, last-seen beliefs, snapshots and prior knowledge
(docs/design/TIER1_SWEEP.md sections 7 and 16.1; TIER1_INTEGRATION.md 1.9 rule 5 and 1.14).

    Mind = {"map":   {rid: {"label", "type", "exits", "connections", "seen_s"}},
            "seen":  {id: {"room", "location", "relation", "pos", "spos", "shelf", "seen_s", "ident", "label_seen"}},
            "snapshots": {name: {"room", "time_s", "entities": {eid: {location, relation, spos, pos, shelf,
                                                                      orientation, open, on}}}},
            "prior": {"from_s", "source"}}

The engine owns one Mind per episode (env.mind): Mind.from_config(config, env) at reset, then mind.update(env,
perception.perceived_ids(env)) once at reset and once at the end of every step, after realism.commit_grants,
camera.end_step and realism.note_step (so it sees exactly what the observation renders, whether or not the agent
observes). It is a pure function of the initial config and the step sequence, saved with the episode (dump / load)
and rebuilt identically on replay; it is never part of the world state, so state digests do not depend on it.

Beliefs are refreshed only by perception: a thing moved while the robot looked elsewhere keeps its old belief
(recall marks it "stale" unless it is perceived now). People and pets the robot identified are remembered too (their
belief has "agent": "people" / "pets"); a shape the robot could not identify (dark room, too far) is not attributed to
anyone. label_seen is the label rendered at the identity level the robot had then (realistic perception: an
unresolved look-alike's descriptor, never its true label). Evaluation reads the Mind through view() as "mind.*"
paths (read-only), e.g. {"path": "mind.map", "op": "len_gte", "value": 9}.

Prior knowledge (setup op prior_knowledge, tasks/setup.py) is the config section "prior_knowledge":
{"map": {rid: entry}, "seen": {id: belief}, "snapshots": {name: snapshot}, "grants": [[eid, level]],
 "records": {eid: record as the op saw it}, "from_s", "source", "import": <a dump>}; from_config turns it into the
reset Mind, issues realistic handles for every id the robot knew before (prior beliefs, snapshots, memory values) and
grants realism knowledge at the op's levels for the fields of that moment (lapsing as soon as the thing has moved).
"""
import copy
import datetime

from . import clock
from .predicates import root_room

BELIEF_FIELDS = ("location", "relation", "pos", "spos", "shelf")
SNAPSHOT_FIELDS = ("location", "relation", "spos", "pos", "shelf", "orientation", "open", "on")
GROUPS = ("people", "pets")
FORMAT = "house-mind-v1"


def of(env):
    """The env's Mind, or None (an env built without one)."""
    return getattr(env, "mind", None)


def _when(start, now_s, t):
    """A human time for t seconds (episode clock) seen from now_s: "today 09:12", "yesterday 18:40", "3 days ago
    07:05", "before the episode" for unknown pasts."""
    if t is None:
        return "before the episode"
    try:
        then = start + datetime.timedelta(seconds=float(t))
        now = start + datetime.timedelta(seconds=float(now_s))
    except (TypeError, ValueError, OverflowError):
        return "before the episode"
    days = (now.date() - then.date()).days
    hm = then.strftime("%H:%M")
    if days <= 0:
        return "today " + hm
    if days == 1:
        return "yesterday " + hm
    return "{} days ago {}".format(days, hm)


def _start_of(config):
    try:
        return clock.start_of(config or {})
    except (TypeError, ValueError):
        return clock.start_of({})


def _connections(state, rid):
    out = []
    for cid, conn in sorted((state.get("connections") or {}).items()):
        rooms = conn.get("rooms") or []
        if rid in rooms and len(rooms) == 2:
            other = rooms[1] if rooms[0] == rid else rooms[0]
            out.append({"id": cid, "to": other, "kind": conn.get("kind", "doorway"), "door": conn.get("door")})
    return out


def map_entry(state, rid, seen_s):
    """A known-map entry of room rid: label, type, exits, connections (id, to, kind, door) and when it was seen."""
    room = (state.get("rooms") or {}).get(rid) or {}
    return {"label": room.get("label"), "type": room.get("type"), "exits": sorted(room.get("exits") or []),
            "connections": _connections(state, rid), "seen_s": seen_s}


def _ids_in(value, entities, out, depth=0):
    """Entity ids appearing in a JSON value (strings equal to an id, or inside "a:b" keys and sentences)."""
    if depth > 6:
        return out
    if isinstance(value, str):
        if value in entities:
            out.add(value)
        else:
            for token in value.replace(":", " ").replace(",", " ").split():
                if token in entities:
                    out.add(token)
    elif isinstance(value, dict):
        for k, v in value.items():
            _ids_in(k, entities, out, depth + 1)
            _ids_in(v, entities, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _ids_in(v, entities, out, depth + 1)
    return out


class Mind:
    """{"map", "seen", "snapshots", "prior"} (sweep 7.1); saved by env.save, rebuilt identically on replay."""

    def __init__(self):
        self.map, self.seen, self.snapshots, self.prior = {}, {}, {}, {}
        self.start = clock.start_of({})
        self.time_s = 0.0               # the clock at the last update ("now" for recall's stale flag)
        self.now = frozenset()          # ids perceived at the last update
        self._marks = {}                # id -> what its belief was formed from (skips unchanged re-renders)

    # ----- construction ----------------------------------------------------------------------------------------
    @classmethod
    def from_config(cls, config, env):
        """The Mind at reset, from the config's prior_knowledge section (sweep 7.4): map entries, last-seen beliefs and
        snapshots as the setup op recorded them; with realistic perception, handles issued for every id the robot knew
        before (beliefs, snapshots, memory values), knowledge granted at the op's levels, and each prior belief's
        identity rendered at the level the robot had (sweep 7.1 / 17: an unresolved look-alike seen at a glance is
        remembered by its group's descriptor, never its true label)."""
        mind = cls()
        config = config or {}
        mind.start = _start_of(config)
        mind.time_s = float(config.get("time_s", 0.0) or 0.0)
        prior = config.get("prior_knowledge") or {}
        if isinstance(prior.get("import"), dict):
            mind.load(prior["import"])
            mind.start = _start_of(config)
        for rid, entry in sorted((prior.get("map") or {}).items()):
            mind.map[rid] = copy.deepcopy(entry)
        for aid, belief in sorted((prior.get("seen") or {}).items()):
            mind.seen[aid] = copy.deepcopy(belief)
        for name, snap in sorted((prior.get("snapshots") or {}).items()):
            mind.snapshots[name] = copy.deepcopy(snap)
        if prior:
            mind.prior = {"from_s": prior.get("from_s"), "source": prior.get("source", "setup")}
        realism = getattr(env, "realism", None) if env is not None else None
        world = getattr(env, "world", None) if env is not None else None
        if realism is not None and world is not None:
            state = world.s
            entities = state.get("entities", {})
            known = set(eid for eid in mind.seen if eid in entities)
            for snap in mind.snapshots.values():
                known.update(e for e in (snap.get("entities") or {}) if e in entities)
            _ids_in({k: (v or {}).get("value") if isinstance(v, dict) else v
                     for k, v in (state.get("memory") or {}).items()}, entities, known)
            for eid in sorted(known):
                realism.handles.handle(eid)
            for eid, level in prior.get("grants") or []:
                record = (prior.get("records") or {}).get(eid)
                if eid in entities and isinstance(record, dict):
                    realism.grant_prior(eid, record, level)
            for eid in sorted(mind.seen):
                belief = mind.seen[eid]
                record = (prior.get("records") or {}).get(eid) or entities.get(eid)
                if not isinstance(belief, dict) or belief.get("agent") or not isinstance(record, dict):
                    continue
                ident, label = cls._prior_identity(realism, eid, record, belief.get("level", "glance"))
                belief["ident"], belief["label_seen"] = ident, label
        return mind

    @staticmethod
    def _prior_identity(realism, eid, record, level):
        """(ident, label_seen) of a prior belief at the identity level the robot had (realism._identify)."""
        from .realism import LEVELS, _unidentified
        lvl = LEVELS.get(level, level) if isinstance(level, str) else level
        try:
            ident = realism._identify(eid, record, int(lvl if isinstance(lvl, int) else LEVELS["glance"]))[0]
        except Exception:  # noqa: BLE001
            ident = {}
        return ("unresolved" if _unidentified(ident, record) else "identified"), ident.get("label") or "object"

    # ----- the once-per-step update ----------------------------------------------------------------------------
    def update(self, env, perceived_ids):
        """Refresh the map and beliefs from the ids perceived this step (sweep 7.1): the robot's room enters the map;
        every perceived thing, person and pet gets a belief (where it was, when, the label it showed). Things not
        perceived keep their old belief. Realistic perception issues the handle of every perceived thing (the robot
        perceived it, so recall can name it)."""
        state = env.world.s
        t = float(state.get("time_s", 0.0))
        rid = state["robot"].get("room")
        if rid in (state.get("rooms") or {}):
            entry = self.map.get(rid)
            if entry is None or entry.get("exits") != sorted(state["rooms"][rid].get("exits") or []):
                self.map[rid] = map_entry(state, rid, t)
            else:
                entry["seen_s"] = t
        realism = getattr(env, "realism", None)
        entities = state.get("entities", {})
        perception = getattr(env, "perception", None)
        dark = bool(perception is not None and rid in (state.get("rooms") or {}) and perception.seen_dark(state, rid))
        has_hook = bool(_hooks())
        for aid in sorted(perceived_ids):
            record = entities.get(aid)
            if record is not None:
                if realism is not None:
                    realism.handles.handle(aid)
                ident, label = self._identity(env, aid, record, dark)
                mark = (tuple(_freeze(record.get(k)) for k in BELIEF_FIELDS), ident, label, root_room(state, aid))
                belief = self.seen.get(aid)
                if belief is not None and self._marks.get(aid) == mark and not has_hook:
                    belief["seen_s"] = t
                    continue
                belief = {k: copy.deepcopy(record.get(k)) for k in BELIEF_FIELDS}
                belief.update(room=mark[3], seen_s=t, ident=ident, label_seen=label)
                if has_hook:
                    for _, _, fn in _hooks():
                        changed = fn(state, aid, record, belief)
                        if isinstance(changed, dict):
                            belief = changed
                self.seen[aid] = belief
                self._marks[aid] = mark
                continue
            for group in GROUPS:
                agent = (state.get(group) or {}).get(aid)
                if agent is not None:
                    self.seen[aid] = {"agent": group, "room": agent.get("location"), "location": agent.get("location"),
                                      "pos": copy.deepcopy(agent.get("pos")), "posture": agent.get("posture"),
                                      "seen_s": t, "ident": "identified", "label_seen": agent.get("label") or aid}
                    break
        self.time_s = t
        self.now = frozenset(perceived_ids)

    @staticmethod
    def _identity(env, eid, record, dark):
        """(ident, label_seen) of a perceived thing: what the robot could tell it was then."""
        realism = getattr(env, "realism", None)
        held = record.get("location") == "robot"
        if dark and not held:
            return "shape", "dark shape"
        if realism is None:
            return "identified", record.get("label") or record.get("type")
        if isinstance(env.world.s["robot"].get("view"), dict):
            info = realism._view_info(eid)
            level = (info or {}).get("level")
            if level == "shape":
                return "shape", "dark shape"
            if level == "detect":
                return "detected", "object"
        level = realism.knowledge.entity_level(eid, record)
        try:
            ident = realism._identify(eid, record, level)[0]
        except Exception:  # noqa: BLE001
            ident = {}
        from .realism import _unidentified
        label = ident.get("label") or "object"
        return ("unresolved" if _unidentified(ident, record) else "identified"), label

    # ----- reads -----------------------------------------------------------------------------------------------
    def view(self):
        """Read-only dict for `mind.*` predicate paths (sweep 7.1): {"map", "seen", "snapshots", "prior"} (the live
        tables; evaluation never mutates them)."""
        return {"map": self.map, "seen": self.seen, "snapshots": self.snapshots, "prior": self.prior}

    def when(self, t):
        return _when(self.start, self.time_s, t)

    def belief(self, aid):
        """One belief as recall shows it: label, room, on/in the support it was seen on, when, stale."""
        found = self.seen.get(aid)
        if found is None:
            return None
        out = {"label": found.get("label_seen"), "room": found.get("room"), "seen": self.when(found.get("seen_s")),
               "seen_s": found.get("seen_s"), "stale": aid not in self.now}
        relation, location = found.get("relation"), found.get("location")
        if found.get("agent"):
            out["agent"] = found["agent"]
        elif location is not None and location != found.get("room"):
            out[relation if relation in ("on", "in", "under", "behind", "hung") else "at"] = location
        if found.get("ident") and found.get("ident") != "identified":
            out["ident"] = found["ident"]
        return out

    def recall(self, prefix):
        """The recall forms (sweep 7.3): "map" (the known map with when each room was seen), "seen:" (every belief),
        "seen:<id>" (one belief, or {} when none), "seen:room:<rid>" (the beliefs of things last seen in that room),
        "snap:" (snapshot names with their room and time). Ids are true ids; realism shows them as the issued
        handles (cognitive actions keep issued handles). Returns None for any other prefix."""
        prefix = prefix or ""
        if prefix == "map":
            return {rid: {"label": e.get("label"), "type": e.get("type"), "exits": list(e.get("exits") or []),
                          "connections": copy.deepcopy(e.get("connections") or []), "seen": self.when(e.get("seen_s")),
                          "seen_s": e.get("seen_s")}
                    for rid, e in sorted(self.map.items())}
        if prefix.startswith("seen:room:"):
            rid = prefix[len("seen:room:"):]
            return {aid: self.belief(aid) for aid in sorted(self.seen) if self.seen[aid].get("room") == rid}
        if prefix == "seen:" or prefix == "seen":
            return {aid: self.belief(aid) for aid in sorted(self.seen)}
        if prefix.startswith("seen:"):
            aid = prefix[len("seen:"):]
            return self.belief(aid) or {}
        if prefix in ("snap:", "snap", "snapshots"):
            return {name: {"room": s.get("room"), "time": self.when(s.get("time_s")), "time_s": s.get("time_s"),
                           "things": len(s.get("entities") or {})}
                    for name, s in sorted(self.snapshots.items())}
        return None

    # ----- snapshots (core snapshot / diff) -----------------------------------------------------------------------
    @staticmethod
    def perceived_scene(env):
        """{eid: {SNAPSHOT_FIELDS}} of the things the observation lists now, as the robot perceives them (realistic
        records with their handles turned back into ids), and the robot's room."""
        obs = env.observe()
        realism = getattr(env, "realism", None)
        scene = {}
        for key, record in (obs.get("visible_entities") or {}).items():
            eid = realism.handles.resolve(key) if realism is not None else key
            if eid is None:
                continue
            values = {k: copy.deepcopy(record.get(k)) for k in SNAPSHOT_FIELDS}
            if realism is not None:
                values = realism.from_handles(values)
            scene[eid] = values
        return obs.get("room_id"), scene

    def snapshot(self, name, env):
        """Remember the perceived scene under `name` (sweep 7.1): what the observation shows now."""
        room, scene = self.perceived_scene(env)
        self.snapshots[name] = {"room": room, "time_s": env.world.s.get("time_s", 0.0), "entities": scene}
        return self.snapshots[name]

    def diff(self, name, env):
        """Compare the perceived scene with snapshot `name` (sweep 7.1): [{"entity", "before", "after"}] in id order;
        things of the snapshot not in view are left out when the robot is in another room. KeyError for an unknown
        snapshot."""
        snap = self.snapshots[name]
        room, now = self.perceived_scene(env)
        changes = []
        for eid in sorted(set(snap.get("entities") or {}) | set(now)):
            if room != snap.get("room") and eid not in now:
                continue
            before, after = (snap.get("entities") or {}).get(eid), now.get(eid)
            if before != after:
                changes.append({"entity": eid, "before": before, "after": after})
        return changes

    # ----- persistence -----------------------------------------------------------------------------------------
    def dump(self):
        """JSON-safe dict of the whole mind (env.save, prior_knowledge {"import": ...})."""
        return {"format": FORMAT, "map": copy.deepcopy(self.map), "seen": copy.deepcopy(self.seen),
                "snapshots": copy.deepcopy(self.snapshots), "prior": copy.deepcopy(self.prior),
                "start": self.start.isoformat(), "time_s": self.time_s, "now": sorted(self.now)}

    def load(self, data):
        """Restore a dump (env.load)."""
        data = data or {}
        self.map = copy.deepcopy(data.get("map") or {})
        self.seen = copy.deepcopy(data.get("seen") or {})
        self.snapshots = copy.deepcopy(data.get("snapshots") or {})
        self.prior = copy.deepcopy(data.get("prior") or {})
        if data.get("start"):
            try:
                self.start = datetime.datetime.fromisoformat(data["start"])
            except ValueError:
                pass
        self.time_s = float(data.get("time_s", 0.0) or 0.0)
        self.now = frozenset(data.get("now") or ())
        self._marks = {}
        return self


def _freeze(value):
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value


def _hooks():
    from .plugins import HOOKS
    return HOOKS.get("mind_seen", [])

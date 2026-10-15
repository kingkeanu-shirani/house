"""World state wrapper with a mutation journal.

All handlers and systems MUST mutate state through this API so that a refused
action rolls back exactly and per-step changes can be diffed cheaply:

    w.ent(eid)            read an entity (do not mutate the returned dict)
    w.mut(eid)            get a mutable entity (journals it first)
    w.create(eid, rec)    add an entity        w.destroy(eid)   remove one
    w.room(rid) / w.room_m(rid), w.person(pid) / w.person_m(pid), w.pet(..)/w.pet_m(..)
    w.conn(cid) / w.conn_m(cid)
    w.sec(name)           mutable top-level section (robot, utilities, weather, stats, facts,
                          reports, memory, household, user_simulator, metadata, logs, ...)
    w.get(name)           read a top-level section or scalar
    w.set_scalar(name, v) top-level scalars such as time_s and steps

Strict mode (env HOUSE_SIM_STRICT=1) compares a full copy after each step and raises
if anything changed without being journaled.

Transient bookkeeping for the physics settle pass and the camera (kernel v3.0, TIER1_INTEGRATION 1.8). None of it is
part of the state, none is saved, and strict mode is unaffected:

    w._touched              entity ids, appended on EVERY mut, create and destroy call (not only the first
                            journaling); append-only within a step (begin() and rollback() clear it)
    w.touched_since(cursor, consumer=None) -> (ids, cursor)
                            the ids appended since an absolute cursor; a named consumer's cursor is remembered so
    w.trim_touched()        can drop the prefix every consumer has read (the engine trims after each substep)
    w.mut_quiet(eid)        journals like mut but is not a touch (writes that change nothing structural)
    w.set_quiet(eid, f, v)  one field, journaled at field level and not a touch (kernel thermal's temperatures)
    w.phys_scratch          the settle pass's transient dict (signatures, ledger, phase, cursor); cleared by begin()
                            and rollback()
    w.mutation_count        +1 on every journaled write call and every rollback; never decreases (camera memos)
    w._power_down_step      None; body.power_down stamps the step (the engine's partial-commit rule)

The sweep's additions (kernel v3.1, TIME-EVAL; TIER1_SWEEP 6.1, 6.4, 8.2, 16.6; TIER1_INTEGRATION 1.8), transient as
well:

    w.notice(kind, room=None, db=None, source=None, to_robot=False, remote=False, visual=False, text=None, data=None)
                            something perceivable happened (a doorbell, an alarm, a chime, glass breaking): the
                            timeline decides whether the robot perceives it (kernel/timeline.py robot_perceives) and,
                            if so, stores it in the list section "notices" (only in episodes whose state has that
                            section: schema-3 tasks or a config that carries it) and hands it to a listening wait.
                            Returns the stored record or None
    sp = w.savepoint(); w.rollback_to(sp); w.release(sp)
                            nested journal layers inside a step (auto standing-order commands): rollback_to restores
                            exactly what changed after sp (entities, sections, scalars, quiet fields), truncates the
                            touch log to its length at sp (clamping every consumer cursor), restores the physics
                            scratch and the heard-notice buffer as they were at sp, increments mutation_count and ends
                            the savepoint; release keeps the changes and ends it (its snapshots merge into the parent
                            layer). begin(), rollback() and commit() end every savepoint
    w._touched_sections     the top-level sections and scalars written (sec, set_scalar, set_path) and the keyed
                            collections changed (rooms, people, pets, connections) since the monitor's last sample
    w._quiet_touched        entity ids written through set_quiet / mut_quiet since the monitor's last sample (they are
                            no touches for the settle pass, but temporal predicates may read them)
    w._heard                perceived notices not yet read by a listening wait (engine-internal)
    w._env_ref              a weak reference to the env the World belongs to (timeline.start), so the timeline's
                            per-id perceivability rules can read its perception, camera view and realism
"""
import marshal

from .errors import ActionError
from .predicates import MISSING, differences, get_path, is_within, root_room

RESERVED_IDS = {"hand", "robot", "floor", "nobody", "household"}
# list sections (a fresh one starts as []); notices .. requests are the sweep's (16.6): report_log and dialogue_log are
# written by PEOPLE-IO's verbs, the others by the kernel's timeline, engine and phases
LIST_SECTIONS = {"events", "orders", "pending", "event_log", "approvals", "messages", "action_log",
                 "notices", "order_log", "report_log", "dialogue_log", "requests"}
NOTICES_CAP = 200
# event-log kind -> fn(w, entry): notices derived from what w.emit logs (kernel/timeline.py fills it: the body's battery
# warnings and detected faults become "battery" / "fault" notices)
EMIT_NOTICES = {}


def clone(value):
    return marshal.loads(marshal.dumps(value))


def _plain(value):
    """A journal copy of one field value: scalars (and MISSING) as they are, containers cloned."""
    return value if value is MISSING or isinstance(value, (int, float, str, bool, type(None))) else clone(value)


class World:
    def __init__(self, state, registry=None):
        self.s = state
        self.reg = registry
        self._journal = {}
        self._quiet = {}                # eid -> {field: value at step start}: field-level journal of set_quiet
        self._index = None
        self._version = 0
        self._static = {}
        self._touched = []              # entity ids written this step, in order (append-only within a step)
        self._touched_base = 0          # absolute position of _touched[0] (trim_touched drops read prefixes)
        self._touch_readers = {}        # consumer -> absolute cursor it has read up to
        self.phys_scratch = {}
        self.mutation_count = 0
        self._power_down_step = None
        self._layers = []               # savepoint layers: {"journal", "quiet", "touched", "scratch", "heard"}
        self._touched_sections = set()  # sections / scalars / keyed kinds written since the monitor's last sample
        self._quiet_touched = set()     # entity ids written quietly since the monitor's last sample
        self._heard = []                # perceived notices a listening wait has not read yet
        self._listening = False         # True while a wait listens for notices (interrupt_on)
        self._env_ref = None            # weak reference to the env (timeline.start): the per-id perceivability rules

    # ----- journaling -------------------------------------------------
    def begin(self):
        self._journal = {}
        self._quiet = {}
        self._layers = []
        self._reset_transient()

    def _reset_transient(self):
        """Start a step's transient bookkeeping: an empty touch log (every consumer back at cursor 0), an empty
        physics scratch and no power-down stamp."""
        self._touched = []
        self._touched_base = 0
        if self._touch_readers:
            self._touch_readers = dict.fromkeys(self._touch_readers, 0)
        self.phys_scratch = {}
        self._power_down_step = None
        self._heard = []

    def _snap(self, key, value):
        if key not in self._journal:
            self._journal[key] = MISSING if value is MISSING else clone(value)
        if self._layers:
            top = self._layers[-1]["journal"]
            if key not in top:
                top[key] = MISSING if value is MISSING else clone(value)
        self.mutation_count += 1
        kind = key[0]
        if kind == "entities":
            self._index = None
            self._touched.append(key[1])
        elif kind in ("section", "scalar"):
            self._touched_sections.add(key[1])
        else:
            self._touched_sections.add(kind)

    def journal_keys(self):
        return list(self._journal) + [("entities", eid) for eid in self._quiet if ("entities", eid) not in self._journal]

    def _with_quiet(self, record, eid):
        """A record as it was at step start: its quiet fields put back to their journaled values."""
        out = dict(record)
        for field, value in self._quiet[eid].items():
            if value is MISSING:
                out.pop(field, None)
            else:
                out[field] = value
        return out

    def journal_before(self, kind, name):
        """The value a journaled key had when the step began (its first snapshot): MISSING for a key created this
        step, None when the key was not journaled (it is unchanged since begin())."""
        before = self._journal.get((kind, name))
        if kind == "entities" and name in self._quiet and before is not MISSING:
            base = before if before is not None else self.s["entities"].get(name)
            return None if base is None else self._with_quiet(base, name)
        return before

    def touched_since(self, cursor, consumer=None):
        """(entity ids written since the absolute `cursor`, in order and with repeats, new cursor). A named consumer's
        new cursor is remembered for trim_touched. Cursors restart at 0 with every begin() and rollback()."""
        start = max(0, int(cursor) - self._touched_base)
        ids = self._touched[start:]
        end = self._touched_base + len(self._touched)
        if consumer is not None:
            self._touch_readers[consumer] = end
        return ids, end

    def trim_touched(self):
        """Drop the prefix of the touch log every named consumer has read (all of it when nobody reads it), so a
        week-long wait keeps the log bounded. Cursors stay valid (they are absolute)."""
        end = self._touched_base + len(self._touched)
        upto = min(self._touch_readers.values()) if self._touch_readers else end
        drop = min(upto, end) - self._touched_base
        if drop > 0:
            del self._touched[:drop]
            self._touched_base += drop

    def rollback(self):
        for key, value in self._journal.items():
            kind, name = key
            container = self.s if kind in ("section", "scalar") else self.s[kind]
            if value is MISSING:
                container.pop(name, None)
            else:
                container[name] = value
        for eid, fields in self._quiet.items():        # after the records: a record journaled after a quiet write
            record = self.s["entities"].get(eid)       # holds the new value, its field journal the old one
            if record is None:
                continue
            for field, value in fields.items():
                if value is MISSING:
                    record.pop(field, None)
                else:
                    record[field] = value
        self._journal = {}
        self._quiet = {}
        self._layers = []
        self._index = None
        self._version += 1
        self.mutation_count += 1
        self._reset_transient()

    # ----- savepoints (TIER1_SWEEP 6.4, TIER1_INTEGRATION 1.8) ----------
    def savepoint(self):
        """Open a journal layer inside the step and return its token. Every write after it is recorded (first value per
        key) in the layer as well as in the step journal, so rollback_to(sp) can restore exactly the state at sp."""
        scratch = {}
        for key, value in self.phys_scratch.items():
            scratch[key] = value.copy() if isinstance(value, (dict, list, set)) else value
        self._layers.append({"journal": {}, "quiet": {}, "touched": self._touched_base + len(self._touched),
                             "scratch": scratch, "heard": len(self._heard)})
        return len(self._layers)

    def _check_sp(self, sp):
        if not isinstance(sp, int) or isinstance(sp, bool) or not 1 <= sp <= len(self._layers):
            raise ValueError("Unknown or ended savepoint: {!r}".format(sp))

    def rollback_to(self, sp):
        """Undo everything written since savepoint sp (and the savepoints opened after it) and end it: entities,
        sections, scalars and keyed records written after sp get their value at sp back (created ones disappear), quiet
        field writes too; the touch log is truncated to its length at sp (every consumer cursor clamped to it), the
        physics scratch and the heard-notice buffer are what they were at sp; mutation_count increments."""
        self._check_sp(sp)
        for layer in reversed(self._layers[sp - 1:]):
            for (kind, name), value in layer["journal"].items():
                container = self.s if kind in ("section", "scalar") else self.s[kind]
                if value is MISSING:
                    container.pop(name, None)
                else:
                    container[name] = value         # the layer's own copy (the layer ends here)
            for eid, fields in layer["quiet"].items():
                record = self.s["entities"].get(eid)
                if record is None:
                    continue
                for field, value in fields.items():
                    if value is MISSING:
                        record.pop(field, None)
                    else:
                        record[field] = value
        first = self._layers[sp - 1]
        del self._layers[sp - 1:]
        end = max(self._touched_base, first["touched"])
        del self._touched[end - self._touched_base:]
        for consumer, cursor in list(self._touch_readers.items()):
            if cursor > end:
                self._touch_readers[consumer] = end
        self.phys_scratch = first["scratch"]
        del self._heard[first["heard"]:]
        self._index = None
        self._version += 1
        self.mutation_count += 1

    def release(self, sp):
        """End savepoint sp (and the ones opened after it) keeping its changes: its first values merge into the layer
        below, so an enclosing savepoint still rolls back to its own point."""
        self._check_sp(sp)
        merged = self._layers[sp - 1:]
        del self._layers[sp - 1:]
        if not self._layers:
            return
        parent = self._layers[-1]
        for layer in merged:
            for key, value in layer["journal"].items():
                parent["journal"].setdefault(key, value)
            for eid, fields in layer["quiet"].items():
                target = parent["quiet"].setdefault(eid, {})
                for field, value in fields.items():
                    target.setdefault(field, value)

    def changes(self):
        """Leaf-level diffs for everything journaled since begin() (field-level quiet writes included, in the same
        order and shape a record journal gives them)."""
        result = []
        keys = set(self._journal) | {("entities", eid) for eid in self._quiet}
        for kind, name in sorted(keys, key=lambda k: (k[0], str(k[1]))):
            container = self.s if kind in ("section", "scalar") else self.s[kind]
            after = container.get(name, MISSING)
            prefix = name if kind in ("section", "scalar") else kind + "." + name
            before = self._journal[(kind, name)] if (kind, name) in self._journal else None
            if kind == "entities" and name in self._quiet:
                if before is None:          # only quiet writes: diff those fields
                    fields = self._quiet[name]
                    current = {f: after.get(f, MISSING) for f in fields} if after is not MISSING else {}
                    old = {f: v for f, v in fields.items() if v is not MISSING}
                    result.extend(differences(old, {f: v for f, v in current.items() if v is not MISSING}, prefix))
                    continue
                if before is not MISSING:
                    before = self._with_quiet(before, name)
            if before is MISSING or after is MISSING:
                if before is not after:
                    result.append({"path": prefix, "before": None if before is MISSING else before,
                                   "after": None if after is MISSING else after,
                                   "before_exists": before is not MISSING, "after_exists": after is not MISSING})
            else:
                result.extend(differences(before, after, prefix))
        return result

    def commit(self):
        changed = self.changes()
        self._journal = {}
        self._quiet = {}
        self._layers = []
        return changed

    # ----- entities ---------------------------------------------------
    @property
    def entities(self):
        return self.s["entities"]

    def has(self, eid):
        return eid in self.s["entities"]

    def ent(self, eid):
        try:
            return self.s["entities"][eid]
        except KeyError:
            raise ActionError("Unknown entity: " + str(eid), "argument")

    def mut(self, eid):
        record = self.ent(eid)
        self._snap(("entities", eid), record)
        return record

    def set_quiet(self, eid, field, value):
        """Set one field of an entity, journaled at field level (no copy of the record) and not a touch: the kernel
        thermal system's temperature writes, hundreds per run. Rollback, changes() and strict mode treat it like a
        journaled write; mutation_count counts it."""
        record = self.ent(eid)
        if ("entities", eid) not in self._journal:
            fields = self._quiet.setdefault(eid, {})
            if field not in fields:
                fields[field] = _plain(record.get(field, MISSING))
        if self._layers:
            top = self._layers[-1]
            if ("entities", eid) not in top["journal"]:
                fields = top["quiet"].setdefault(eid, {})
                if field not in fields:
                    fields[field] = _plain(record.get(field, MISSING))
        record[field] = value
        self.mutation_count += 1
        self._quiet_touched.add(eid)

    def mut_quiet(self, eid):
        """Like mut (journaled, counted in mutation_count) but not a touch: the settle pass does not look at it. Only
        for writes that cannot change anything structural (the kernel thermal system's temperatures)."""
        record = self.ent(eid)
        key = ("entities", eid)
        if key not in self._journal:
            self._journal[key] = clone(record)
        if self._layers and key not in self._layers[-1]["journal"]:
            self._layers[-1]["journal"][key] = clone(record)
        self.mutation_count += 1
        self._index = None
        self._quiet_touched.add(eid)
        return record

    def create(self, eid, record):
        if eid in self.s["entities"] or eid in self.s["rooms"] or eid in RESERVED_IDS:
            raise ValueError("Entity id already used or reserved: " + eid)
        self._version += 1
        self._snap(("entities", eid), MISSING)
        self.s["entities"][eid] = record
        return record

    def new_id(self, stem):
        stem = "".join(c if c.isalnum() else "_" for c in stem.lower()).strip("_") or "item"
        n = 1
        while "{}_{}".format(stem, n) in self.s["entities"] or "{}_{}".format(stem, n) in self.s["rooms"]:
            n += 1
        return "{}_{}".format(stem, n)

    def spawn(self, type_name, location, eid=None, **props):
        if self.reg is None:
            raise RuntimeError("World has no type registry")
        record = self.reg.make(type_name, location, **props)
        eid = eid or self.new_id(type_name)
        self.create(eid, record)
        return eid

    def destroy(self, eid):
        self._version += 1
        record = self.ent(eid)
        self._snap(("entities", eid), record)
        del self.s["entities"][eid]

    # ----- other keyed collections -----------------------------------
    def _keyed(self, kind, key, mutable):
        try:
            record = self.s[kind][key]
        except KeyError:
            raise ActionError("Unknown {}: {}".format(kind[:-1] if kind.endswith("s") else kind, key), "argument")
        if mutable:
            self._snap((kind, key), record)
        return record

    def room(self, rid): return self._keyed("rooms", rid, False)
    def room_m(self, rid): return self._keyed("rooms", rid, True)
    def person(self, pid): return self._keyed("people", pid, False)
    def person_m(self, pid): return self._keyed("people", pid, True)
    def pet(self, pid): return self._keyed("pets", pid, False)
    def pet_m(self, pid): return self._keyed("pets", pid, True)
    def conn(self, cid): return self._keyed("connections", cid, False)
    def conn_m(self, cid): return self._keyed("connections", cid, True)

    def add_person(self, pid, record):
        self._snap(("people", pid), self.s["people"].get(pid, MISSING))
        self.s["people"][pid] = record

    def add_pet(self, pid, record):
        self._snap(("pets", pid), self.s["pets"].get(pid, MISSING))
        self.s["pets"][pid] = record

    def remove_person(self, pid):
        self._snap(("people", pid), self.s["people"][pid])
        del self.s["people"][pid]

    def sec(self, name):
        value = self.s.get(name, MISSING)
        if value is MISSING:
            self._snap(("section", name), MISSING)
            self.s[name] = [] if name in LIST_SECTIONS else {}
            return self.s[name]
        self._snap(("section", name), value)
        return value

    def get(self, name, default=None):
        return self.s.get(name, default)

    def set_scalar(self, name, value):
        self._snap(("scalar", name), self.s.get(name, MISSING))
        self.s[name] = value

    def set_path(self, path, value):
        """Journaled assignment for event changes such as "entities.door_1.open"."""
        parts = path.split(".")
        head = parts[0]
        if head in ("entities", "rooms", "people", "pets", "connections"):
            if len(parts) < 3:
                raise ValueError("Path needs a field: " + path)
            container = self._keyed(head, parts[1], True)
            rest = parts[2:]
        elif len(parts) == 1:
            self.set_scalar(head, clone(value))
            return
        else:
            container = self.sec(head)
            rest = parts[1:]
        node = container
        for part in rest[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[rest[-1]] = clone(value)

    # ----- queries ----------------------------------------------------
    @property
    def robot(self):
        return self.s["robot"]

    def robot_m(self):
        return self.sec("robot")

    def static_index(self, name, predicate):
        """Sorted ids of entities matching a predicate over STATIC fields (type, is_a, category,
        fixed tags...). Cached until an entity is created or destroyed. Use it in systems instead of
        scanning every entity each tick; re-check dynamic fields on the returned records."""
        cached = self._static.get(name)
        if cached is not None and cached[0] == self._version and cached[1] == len(self.s["entities"]):
            return cached[2]
        ids = [eid for eid in sorted(self.s["entities"]) if predicate(self.s["entities"][eid])]
        self._static[name] = (self._version, len(self.s["entities"]), ids)
        return ids

    def of_type(self, type_name):
        """Entity ids whose type is type_name or derives from it (cached)."""
        return [e for e in self.static_index("type:" + type_name,
                lambda r: r.get("type") == type_name or type_name in r.get("is_a", [])) if e in self.s["entities"]]

    def children(self, parent):
        # A scan is always current, even when a handler edits "location" after mut().
        return sorted(eid for eid, record in self.s["entities"].items() if record.get("location") == parent)

    def descendants(self, eid):
        result, stack = [], [eid]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                result.append(child)
                stack.append(child)
        return result

    def ancestors(self, eid):
        chain, location, seen = [], self.ent(eid).get("location"), {eid}
        while location is not None and location not in seen:
            chain.append(location)
            seen.add(location)
            if location in self.s["entities"]:
                location = self.s["entities"][location].get("location")
            elif location in self.s.get("people", {}):
                location = self.s["people"][location].get("location")
            elif location in self.s.get("pets", {}):
                location = self.s["pets"][location].get("location")
            else:
                break
        return chain

    def root_room(self, eid):
        return root_room(self.s, eid)

    def within(self, eid, ancestor):
        return is_within(self.s, eid, ancestor)

    def held_items(self):
        hands = self.s["robot"].get("hands", {})
        return [eid for eid in dict.fromkeys(hands.values()) if eid]

    def is_held(self, eid):
        return eid in self.held_items()

    def is_a(self, eid, type_name):
        record = self.ent(eid)
        return record.get("type") == type_name or type_name in record.get("is_a", [])

    def find(self, type=None, room=None, within=None, tag=None, where=None):
        result = []
        for eid in sorted(self.s["entities"]):
            record = self.s["entities"][eid]
            if type is not None and not (record.get("type") == type or type in record.get("is_a", [])):
                continue
            if tag is not None and tag not in record.get("tags", []):
                continue
            if room is not None and self.root_room(eid) != room:
                continue
            if within is not None and not self.within(eid, within):
                continue
            if where is not None and any(get_path(record, k) != v for k, v in where.items()):
                continue
            result.append(eid)
        return result

    def rooms_of_type(self, room_type):
        return sorted(r for r, room in self.s["rooms"].items() if room.get("type") == room_type)

    def emit(self, kind, **data):
        log = self.sec("event_log")
        entry = {"time_s": self.s.get("time_s", 0), "step": self.s.get("steps", 0), "kind": kind}
        entry.update(data)
        log.append(entry)
        if len(log) > 600:
            del log[:len(log) - 600]
        derive = EMIT_NOTICES.get(kind)
        if derive is not None:
            derive(self, entry)
        return entry

    def notice(self, kind, room=None, db=None, source=None, to_robot=False, remote=False, visual=False, text=None,
               data=None):
        """Something perceivable happened (TIER1_SWEEP 6.1): a doorbell, a knock, an alarm, a chime, crying, glass
        breaking. room: where it happened (default: the source's room); db: sound level at 1 m (default the kind's);
        source: the entity, person or pet that made it; to_robot: speech addressed to the robot; remote: an app or
        phone notification (needs the robot's network); visual: seen, not heard; text: what was said; data: any
        JSON-safe details. The timeline decides whether the robot perceives it (6.2) and returns the stored record, or
        None when nobody listens or the robot did not perceive it."""
        from . import timeline
        return timeline.post(self, {"kind": kind, "room": room, "db": db, "source": source, "to_robot": bool(to_robot),
                                    "remote": bool(remote), "visual": bool(visual), "text": text, "data": data})

    def stat(self, name, amount=1):
        stats = self.sec("stats")
        stats[name] = stats.get(name, 0) + amount

    def fact(self, name, value):
        self.sec("facts")[name] = value

    def log_move(self, eid, before, after, by="robot"):
        logs = self.sec("logs")
        moves = logs.setdefault("moves", [])
        moves.append({"time_s": self.s.get("time_s", 0), "entity": eid, "from": before, "to": after, "by": by})
        if len(moves) > 2000:
            del moves[:len(moves) - 2000]

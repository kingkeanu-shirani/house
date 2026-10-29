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
    w.touch_log_complete()  True while touched_since(0) is the step's whole log (nothing trimmed since begin() or
                            rollback(); rollback_to truncates without moving absolute cursors)
    w.journaled_entities()  ids of the entities journaled as whole records this step (mut, create, destroy,
                            mut_quiet), without the set_quiet-only ones
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

The writer log, mess flows and destroyed records (VERIFY_HARDENING 6.2, 6.5, 8.6; WP2). The engine turns tracking on
once an episode's baseline is taken (w.track(env.writes)); a World that does not track (generation, setup, a bare
World in a test) logs nothing and keeps every state exactly as before:

    w.writing(cls, name=None, cause=None)
                            a context manager: the writes inside it are of writer class cls ("robot", "physical",
                            "volition", "setup") under the name (a system, an action, "event:<kind>"); cause: ids the
                            writes are consequences of (a heat source, the window a draught came through, the pipe that
                            leaked). The engine sets the writer of every action phase, system call, event, settle pass
                            and auto command itself; a system tags a part of its work with a different class this way
    w.mut(eid, cause=None) / create(eid, rec, cause=None) / destroy(eid, cause=None) / set_quiet(..., cause=None)
                            cause: ids (a string or a list) the write is a consequence of
    w.cause(rid, *ids)      add cause ids to what the current writer wrote to rid in this frame (rid: an entity id, or
                            "connections:<id>", "rooms:<id>", "people:<id>", "pets:<id>")
    w.writer                the current (class, name)
    w.mess_flow(kind, amount, src, dst, origin=None, cause=None)
                            mess of a registered kind moved (6.5): appended to logs.mess_flows with the writer of the
                            current window; src / dst are location keys "class:id" (or (class, id) pairs) with class
                            floor, entity, drain, outside, loss (dst only: "loss:evaporation", "loss:consumed:<id>")
                            or created (src only: mess made from clean inputs; its origin defaults to "created:<step>").
                            Repeated identical flows since the last transform merge (amounts summed, "n" counted)
    w.mess_transform(origins, new_kind, amount, at=None, cause=None)
                            mess of the origin groups became new_kind (a class change: tap water mixed with bleach)
    w.gone_record(eid)      the final record of an entity destroyed during the episode (logs.gone; None otherwise)

logs.gone entries are {"t", "entity", "type", "location", "by", "name", "last"} (by: the writer class, name: the
writer's name, last: the entity's final record); logs.mess_flows entries are {"op": "flow", "t0", "t", "kind",
"amount", "src", "dst", "origin", "by", "name", "n"} or {"op": "transform", "t", "origins", "kind", "amount", "at",
"by", "name"}. Both lists live in state (journaled: a refused action takes its entries back; they change the state
digest of an episode that destroys an entity or moves mess) and are never observed by an agent.

The last-writer log itself (WriterLog, env.writes) lives outside the state. Per frame (begin() .. commit()) the World
splits each written record's writes into segments, one per change of writer (a clone of the record is taken only when
the writer changes); at commit every segment's top-level field changes become (class, name, t, causes) entries of the
log. Fields written through set_quiet are logged at the write (class physical) and left out of the record diffs.
A savepoint marks the frame's segment counts, quiet writes and pending records, so rollback_to drops exactly what was
logged after it; rollback drops the frame's. Cost: one dict lookup per journaled write, one clone per change of
writer of a record within a frame, one field diff per written record at commit (VERIFY_HARDENING 6.2's 15 % limit).
"""
import marshal
import os
from collections.abc import Mapping

from .errors import ActionError
from .predicates import MISSING, differences, get_path, is_within, root_room

RESERVED_IDS = {"hand", "robot", "floor", "nobody", "household"}
# list sections (a fresh one starts as []); notices .. requests are the sweep's (16.6): report_log and dialogue_log are
# written by PEOPLE-IO's verbs, the others by the kernel's timeline, engine and phases
LIST_SECTIONS = {"events", "orders", "pending", "event_log", "approvals", "messages", "action_log",
                 "notices", "order_log", "report_log", "dialogue_log", "requests"}
NOTICES_CAP = 200
STRUCT_CAP = 20000      # creations, destructions and rollbacks kept for the static indexes (older: a full rebuild)
READS_ALL = "*"        # a static-index predicate that iterated its record (or would not take a read view) reads it all
# strict mode also checks that every static-index predicate answers the same on its read view as on the record
STRICT_INDEX = os.environ.get("HOUSE_SIM_STRICT") == "1"
# event-log kind -> fn(w, entry): notices derived from what w.emit logs (kernel/timeline.py fills it: the body's battery
# warnings and detected faults become "battery" / "fault" notices)
EMIT_NOTICES = {}

# ----- the writer log (VERIFY_HARDENING 6.2; WP2) -----
WRITER_CLASSES = ("robot", "physical", "volition", "setup")
EXTERNAL_WRITER = ("setup", "external")         # writes outside the engine's frames (a test or tool poking the world)
KEYED_KINDS = ("rooms", "people", "pets", "connections")
# placement fields (6.3 class placement; house_sim/data/verify/watch_fields.json): a robot segment that changed one
# keeps the entity's robot placement P_R(e) (6.2)
PLACEMENT_FIELDS = ("location", "relation", "shelf", "pos", "spos", "facing", "facing_deg", "orientation", "cell",
                    "slot")
# fields WriterLog.robot_wrote ignores by default (6.3 class bookkeeping; the ledger passes its own classifier):
# underscore fields, perception bookkeeping, last_* stamps
BOOKKEEPING_FIELDS = frozenset(("revealed", "seen", "seen_s", "held_hand", "held_grip", "noticed"))
EXISTS = "$exists"              # the pseudo-field of a creation or destruction
SEGMENT_CAP = 16                # writer changes of one record in a frame before its segments are flushed early
HISTORY_CAP = 64                # entries kept per (record, field): runs of one writer merge into one entry
GONE_CAP = 1000                 # logs.gone entries kept (oldest dropped)
FLOW_SRC = ("floor", "entity", "drain", "outside", "created")
FLOW_DST = ("floor", "entity", "drain", "outside", "loss")
_PHYSICAL = {}                  # writer -> the physical writer of its quiet writes (interned)


def is_bookkeeping(field):
    """The kernel's default bookkeeping test for WriterLog.robot_wrote: an underscore field, a last_* stamp or a
    perception mark (revealed, seen, ...). The ledger's field classes (WP1) are the authority; this is the fallback."""
    return not isinstance(field, str) or field.startswith("_") or field.startswith("last_") or \
        field in BOOKKEEPING_FIELDS


def _causes(value):
    """Cause ids as a frozenset (None for none): a string or a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset((value,))
    out = frozenset(str(v) for v in value if v is not None)
    return out or None


def _record_key(rid):
    """A writer-log record id ("mug_1", "connections:door_1", ...) -> the journal key (kind, name)."""
    head, sep, rest = rid.partition(":")
    if sep and head in KEYED_KINDS:
        return head, rest
    return "entities", rid


def location_key(value, allowed, what):
    """A mess-flow location key as the string "class:id" (6.5): from "class:id" or a (class, id) pair."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        value = "{}:{}".format(value[0], value[1])
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("mess flow {}: a location key 'class:id' (got {!r})".format(what, value))
    head, _, rest = value.partition(":")
    if head not in allowed or not rest:
        raise ValueError("mess flow {}: class must be one of {} (got {!r})".format(what, ", ".join(allowed), value))
    return value


class WriterLog(Mapping):
    """env.writes: who last wrote each field of each record in the episode (VERIFY_HARDENING 6.2; WP2). Outside world
    state: saved by env.save, rebuilt by a replay, never observed by an agent.

    As a mapping: writes[(rid, field)] -> (class, name, t), the last writer of that field; rid is an entity id or
    "rooms:<id>", "people:<id>", "pets:<id>", "connections:<id>"; field is a top-level field, "$exists" for a creation
    or destruction, None for a top-level section ("section:<name>"). class is one of WRITER_CLASSES; name the action,
    system, "event:<kind>", "order:<action>", "physics.settle", ...; t the simulated time of the writer's last write in
    that frame (a long wait is one frame: t is then the time of its writer's last touch of the record).

    Each key keeps a short history (HISTORY_CAP entries): runs of one writer merge into one entry [class, name, t0, t,
    causes, seq], seq being the log's write order (ties in simulated time: several frames at one moment)."""

    def __init__(self):
        self._hist = {}         # (rid, field) -> [[cls, name, t0, t, causes or None], ...], oldest first
        self._fields = {}       # rid -> {field: None} (an insertion-ordered set)
        self._tail = {}         # (eid, field) -> [writer, t0, t, causes]: quiet writes not merged into _hist yet
        self._placement = {}    # eid -> [t, {placement field: value}] after the robot's last placement change
        self._created = {}      # rid -> (cls, name, t) of its creation in the episode
        self._seq = 0           # write order: ties in simulated time are broken by it
        self.recent = None      # a list while a consumer collects every committed entry (HouseEnv.finish)

    # ----- mapping -----
    def __getitem__(self, key):
        self._sync()
        entry = self._hist[tuple(key)][-1]
        return entry[0], entry[1], entry[3]

    def __iter__(self):
        self._sync()
        return iter(self._hist)

    def __len__(self):
        self._sync()
        return len(self._hist)

    def __contains__(self, key):
        self._sync()
        try:
            return tuple(key) in self._hist
        except TypeError:
            return False

    # ----- recording (World.commit) -----
    def _push(self, key, cls, name, t0, t, causes, seq=None):
        if seq is None:
            self._seq += 1
            seq = self._seq
        hist = self._hist.get(key)
        if hist is None:
            self._hist[key] = [[cls, name, t0, t, causes, seq]]
            self._fields.setdefault(key[0], {})[key[1]] = None
            return
        last = hist[-1]
        if last[0] == cls and last[1] == name and last[4] == causes:
            if t >= last[3]:
                last[3] = t
                last[5] = seq
            return
        hist.append([cls, name, t0, t, causes, seq])
        if len(hist) > HISTORY_CAP:
            del hist[0]

    def _sync(self):
        """Merge the quiet writes' tail into the histories (before any query)."""
        if self._tail:
            tail, self._tail = self._tail, {}
            for key, (wr, t0, t, causes, seq) in tail.items():
                self._push(key, wr[0], wr[1], t0, t, causes, seq)

    def add(self, rid, field, cls, name, t, causes=None):
        key = (rid, field)
        causes = tuple(sorted(causes)) if causes else None
        if self._tail:
            old = self._tail.pop(key, None)
            if old is not None:
                self._push(key, old[0][0], old[0][1], old[1], old[2], old[3], old[4])
        self._push(key, cls, name, t, t, causes)
        if self.recent is not None:
            self.recent.append((rid, field, cls, name, t, causes))

    def absorb(self, records):
        """Apply a committed frame's records (World._wcommit): ("w", rid, field, cls, name, t, causes), ("p", eid,
        placement, t), ("c", rid, cls, name, t)."""
        hists, tail, add, recent = self._hist, self._tail, self.add, self.recent
        for rec in records:
            tag = rec[0]
            if tag == "w":
                if recent is None and not rec[6]:
                    # the common case inline: a key whose last entry is the same writer without causes
                    key = (rec[1], rec[2])
                    hist = hists.get(key)
                    if hist is not None and key not in tail:
                        last = hist[-1]
                        if last[0] == rec[3] and last[1] == rec[4] and last[4] is None and rec[5] >= last[3]:
                            self._seq += 1
                            last[3], last[5] = rec[5], self._seq
                            continue
                add(rec[1], rec[2], rec[3], rec[4], rec[5], rec[6])
            elif tag == "p":
                self._placement[rec[1]] = [rec[3], rec[2]]
            elif tag == "c":
                self._created[rec[1]] = (rec[2], rec[3], rec[4])

    def absorb_quiet(self, quiet):
        """A frame's set_quiet writes ({(eid, field): (writer, t, causes)}): kept in a tail merged lazily, since the
        kernel thermal model writes hundreds of temperatures every frame, always by the same writer."""
        tail, recent = self._tail, self.recent
        for key, value in quiet.items():
            wr, t, causes = value
            causes = tuple(sorted(causes)) if causes else None
            if recent is not None:
                recent.append((key[0], key[1], wr[0], wr[1], t, causes))
            self._seq += 1
            old = tail.get(key)
            if old is not None:
                if (old[0] is wr or old[0] == wr) and old[3] == causes:
                    old[2], old[4] = t, self._seq
                    continue
                self._push(key, old[0][0], old[0][1], old[1], old[2], old[3], old[4])
            tail[key] = [wr, t, t, causes, self._seq]

    # ----- queries -----
    def history(self, rid, field):
        """The key's entries, oldest first: [{"class", "name", "t0", "t", "causes"}] (copies)."""
        self._sync()
        return [{"class": e[0], "name": e[1], "t0": e[2], "t": e[3], "causes": list(e[4] or ())}
                for e in self._hist.get((rid, field), ())]

    def fields(self, rid):
        """The fields of rid that have entries, in the order first written."""
        self._sync()
        return list(self._fields.get(rid, ()))

    def last(self, rid, field=None, before=None, ignore=None):
        """(class, name, t) of the last write of rid's field (field None: of any of its fields, ignore(field) -> True
        skipping some) at or before `before` (None: ever), or None."""
        self._sync()
        keys = [(rid, field)] if field is not None else [(rid, f) for f in self._fields.get(rid, ())
                                                          if ignore is None or not ignore(f)]
        best, rank = None, None
        for key in keys:
            for entry in reversed(self._hist.get(key, ())):
                if before is not None and entry[2] > before + 1e-9:
                    continue
                t = entry[3] if before is None or entry[3] <= before + 1e-9 else before
                if best is None or (t, entry[5]) > rank:
                    best, rank = (entry[0], entry[1], t), (t, entry[5])
                break
        return best

    def causes(self, rid, field):
        """The cause ids of the last write of rid's field ([] when none were reported)."""
        self._sync()
        hist = self._hist.get((rid, field))
        return list(hist[-1][4] or ()) if hist else []

    def entry(self, rid, field, before=None):
        """(class, name, t0, t, causes) of the write of rid's field standing at `before` (None: the last one): the
        last entry (a run of one writer) begun at or before it, t clipped to `before`; causes a tuple (() when none
        were reported). None when the field was not written by then."""
        self._sync()
        for entry in reversed(self._hist.get((rid, field), ())):
            if before is not None and entry[2] > before + 1e-9:
                continue
            t = entry[3] if before is None or entry[3] <= before + 1e-9 else before
            return entry[0], entry[1], entry[2], t, tuple(entry[4] or ())
        return None

    def during(self, rid, field, t0, t):
        """The writes of rid's field that stood at some moment of [t0, t], oldest first: (class, name, t0, t, causes)
        of the one standing at t0 (None before the field's first write) and of every one begun after t0 up to t."""
        self._sync()
        out = []
        for entry in self._hist.get((rid, field), ()):
            if entry[2] > t + 1e-9:
                break
            if entry[2] <= t0 + 1e-9:
                out = [entry]
            else:
                out.append(entry)
        return [(e[0], e[1], e[2], e[3], tuple(e[4] or ())) for e in out]

    def wrote(self, cls, rid, before=None, fields=None, ignore=is_bookkeeping):
        """True when a writer of class cls wrote rid (any of `fields`, else any field ignore() does not skip; a
        creation or destruction counts) at or before `before` (None: ever)."""
        self._sync()
        names = fields if fields is not None else [f for f in self._fields.get(rid, ())
                                                   if ignore is None or not ignore(f)]
        for field in names:
            for entry in self._hist.get((rid, field), ()):
                if entry[0] == cls and (before is None or entry[2] <= before + 1e-9):
                    return True
        return False

    def robot_wrote(self, rid, before=None, fields=None, ignore=is_bookkeeping):
        """True when the robot wrote rid (bookkeeping fields aside, unless `ignore` says otherwise)."""
        return self.wrote("robot", rid, before, fields, ignore)

    def robot_placement(self, eid):
        """P_R(e) (6.2): the placement fields of eid after the robot's last placement change ({field: value}, a copy),
        or None when the robot never placed it."""
        entry = self._placement.get(eid)
        return clone(entry[1]) if entry is not None else None

    def placement_time(self, eid):
        entry = self._placement.get(eid)
        return entry[0] if entry is not None else None

    def created_by(self, rid):
        """(class, name, t) of rid's creation during the episode, or None (it existed at the baseline)."""
        return self._created.get(rid)

    # ----- persistence -----
    def dump(self):
        self._sync()
        return {"format": "house-writes-v1",
                "seq": self._seq,
                "hist": [[rid, field, [list(e[:4]) + [list(e[4]) if e[4] else None, e[5]] for e in entries]]
                         for (rid, field), entries in self._hist.items()],
                "placement": [[eid, entry[0], entry[1]] for eid, entry in self._placement.items()],
                "created": [[rid] + list(value) for rid, value in self._created.items()]}

    @classmethod
    def load(cls, data):
        log = cls()
        if not data:
            return log
        if data.get("format") != "house-writes-v1":
            raise ValueError("Unsupported writer log format: {!r}".format(data.get("format")))
        for rid, field, entries in data.get("hist") or ():
            log._hist[(rid, field)] = [[e[0], e[1], e[2], e[3], tuple(e[4]) if e[4] else None,
                                        e[5] if len(e) > 5 else 0] for e in entries]
            log._fields.setdefault(rid, {})[field] = None
        for eid, t, value in data.get("placement") or ():
            log._placement[eid] = [t, value]
        log._seq = int(data.get("seq", 0) or 0)
        for row in data.get("created") or ():
            log._created[row[0]] = (row[1], row[2], row[3])
        return log


class _Writing(object):
    """World.writing's context manager (a plain class: cheaper than a generator in the systems' loops)."""
    __slots__ = ("w", "writer", "cause", "saved")

    def __init__(self, w, writer, cause):
        self.w, self.writer, self.cause, self.saved = w, writer, cause, None

    def __enter__(self):
        w = self.w
        self.saved = (w._writer, w._wcause)
        w._writer, w._wcause = self.writer, self.cause
        return w

    def __exit__(self, *exc):
        self.w._writer, self.w._wcause = self.saved
        return False


def clone(value):
    return marshal.loads(marshal.dumps(value))


def _plain(value):
    """A journal copy of one field value: scalars (and MISSING) as they are, containers cloned."""
    return value if value is MISSING or isinstance(value, (int, float, str, bool, type(None))) else clone(value)


class _ReadView(object):
    """A read-only view of an entity record that notes the keys a static-index predicate reads (World.static_index).
    get, [] and in behave as on the record; iterating it (keys, items, values, iter, len) notes that the predicate
    reads the whole record."""
    __slots__ = ("_record", "_keys")

    def __init__(self, record, keys):
        self._record = record
        self._keys = keys

    def get(self, key, default=None):
        self._keys.add(key)
        return self._record.get(key, default)

    def __getitem__(self, key):
        self._keys.add(key)
        return self._record[key]

    def __contains__(self, key):
        self._keys.add(key)
        return key in self._record

    def __bool__(self):
        return bool(self._record)           # "if not rec" (records are never empty): no field read

    def __iter__(self):
        self._keys.add(READS_ALL)
        return iter(self._record)

    def __len__(self):
        self._keys.add(READS_ALL)
        return len(self._record)

    def keys(self):
        self._keys.add(READS_ALL)
        return self._record.keys()

    def items(self):
        self._keys.add(READS_ALL)
        return self._record.items()

    def values(self):
        self._keys.add(READS_ALL)
        return self._record.values()


class _StartView(_ReadView):
    """A _ReadView of a record whose quiet fields (World.set_quiet) changed this step, with their step-start values
    (the journal's field values; MISSING: the field was absent). No copy of the record is made."""
    __slots__ = ("_start",)

    def __init__(self, record, keys, start):
        _ReadView.__init__(self, record, keys)
        self._start = start

    def get(self, key, default=None):
        self._keys.add(key)
        if key in self._start:
            value = self._start[key]
            return default if value is MISSING else value
        return self._record.get(key, default)

    def __getitem__(self, key):
        self._keys.add(key)
        if key in self._start:
            value = self._start[key]
            if value is MISSING:
                raise KeyError(key)
            return value
        return self._record[key]

    def __contains__(self, key):
        self._keys.add(key)
        if key in self._start:
            return self._start[key] is not MISSING
        return key in self._record

    def _whole(self):
        out = dict(self._record)
        for key, value in self._start.items():
            if value is MISSING:
                out.pop(key, None)
            else:
                out[key] = value
        return out

    def __iter__(self):
        self._keys.add(READS_ALL)
        return iter(self._whole())

    def __len__(self):
        self._keys.add(READS_ALL)
        return len(self._whole())

    def keys(self):
        self._keys.add(READS_ALL)
        return self._whole().keys()

    def items(self):
        self._keys.add(READS_ALL)
        return self._whole().items()

    def values(self):
        self._keys.add(READS_ALL)
        return self._whole().values()


class World:
    def __init__(self, state, registry=None):
        self.s = state
        self.reg = registry
        self._journal = {}
        self._quiet = {}                # eid -> {field: value at step start}: field-level journal of set_quiet
        self._index = None
        self._version = 0               # +1 on every create, destroy and rollback (camera and sight memos read it)
        self._static = {}               # name -> [sorted ids, id set, struct cursor, pending ids]
        self._struct = []               # entity ids created, destroyed or rolled back (static indexes apply them at once)
        self._struct_base = 0           # absolute position of _struct[0]
        self._readers = {}              # field -> names of the static indexes whose predicates read it
        self._read_all = set()          # names of the static indexes whose predicates read whole records
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
        # the writer log (WP2; module docstring): off until track(); transient per frame, never part of the state
        self._wtrack = False
        self._wlog = None               # the WriterLog commits go to
        self._writer = EXTERNAL_WRITER  # (class, name) of the writes being made now
        self._wcause = None             # frozenset of the current window's cause ids (writing(cause=...))
        self._wseg = {}                 # journal key -> [[writer, causes, t, start record], ...] this frame
        self._wbase = {}                # journal key -> its record when this frame's segments were last flushed
        self._wpend = []                # flushed records not yet committed (WriterLog.absorb's shapes)
        self._wquiet = {}               # (eid, field) -> (writer, t, causes): set_quiet writes this frame
        self._wqfields = {}             # eid -> fields written through set_quiet this frame (left out of diffs)
        self._wsec = {}                 # section -> writer of its last logged write this frame

    # ----- the writer log (WP2) ---------------------------------------
    def track(self, log):
        """Start logging writers into `log` (a WriterLog; None stops). The engine calls it when an episode's baseline
        is taken; the frame's data starts empty."""
        self._wtrack = log is not None
        self._wlog = log
        self._wclear()

    @property
    def writer(self):
        return self._writer

    @property
    def tracking(self):
        return self._wtrack

    def writing(self, cls, name=None, cause=None):
        """Context manager: the writes inside it are of writer class `cls` (WRITER_CLASSES) under `name` (default:
        the current writer's name); cause: ids the writes are consequences of (6.2 causal links)."""
        if cls not in WRITER_CLASSES:
            raise ValueError("writer class must be one of {} (got {!r})".format(", ".join(WRITER_CLASSES), cls))
        return _Writing(self, (cls, str(name) if name is not None else self._writer[1]), _causes(cause))

    def cause(self, rid, *ids):
        """Add cause ids to what the current writer wrote to rid in this frame (call it after the write). rid: an
        entity id or "<kind>:<id>" of a room, person, pet or connection. No-op when the World does not track; a
        ValueError when the current writer has not written rid in this frame."""
        if not self._wtrack or not ids:
            return
        key = _record_key(rid)
        segs = self._wseg.get(key)
        if not segs or segs[-1][0] != self._writer:
            raise ValueError("cause(): {} was not written by {} in this frame".format(rid, self._writer))
        self._wadd_causes(segs[-1], ids)

    @staticmethod
    def _wadd_causes(seg, ids):
        extra = _causes(ids)
        if extra:
            seg[1] = extra if not seg[1] else (seg[1] | extra)

    def _wclear(self):
        self._wseg = {}
        self._wbase = {}
        self._wpend = []
        self._wquiet = {}
        self._wqfields = {}
        self._wsec = {}

    def _wnote(self, key, value):
        """A journaled write (from _snap, before the caller changes anything): open a segment of the current writer
        for the record, or a new one when another writer wrote it last in this frame (a clone of its current record
        is the new segment's start)."""
        kind = key[0]
        if kind == "scalar":
            return
        wr = self._writer
        if kind == "section":
            if self._wsec.get(key[1]) != wr:
                self._wsec[key[1]] = wr
                self._wpend.append(("w", "section:" + key[1], None, wr[0], wr[1], self.s.get("time_s", 0.0),
                                    self._wcause))
            return
        t = self.s.get("time_s", 0.0)
        segs = self._wseg.get(key)
        if segs is None:
            self._wseg[key] = [[wr, self._wcause, t, None]]
            return
        last = segs[-1]
        if last[0] is wr or last[0] == wr:
            last[2] = t
            if self._wcause and last[1] is not self._wcause:
                last[1] = self._wcause if not last[1] else (last[1] | self._wcause)
            return
        segs.append([wr, self._wcause, t, MISSING if value is MISSING else clone(value)])
        if len(segs) > SEGMENT_CAP and not self._layers:     # (inside a savepoint the counts must stay comparable)
            self._wflush_key(key, segs, rebase=True)
            del self._wseg[key]
            self._wseg[key] = [[wr, self._wcause, t, None]]

    def _wstart(self, key):
        """The record a key's first segment of this frame starts from: its state at the last early flush, else its
        journal snapshot (the frame start; MISSING for a record created in this frame)."""
        if key in self._wbase:
            return self._wbase[key]
        return self._journal.get(key, MISSING)

    def _wflush_key(self, key, segs, rebase=False):
        """Turn one record's segments into write records (pending); rebase: its current record becomes the start of
        its next segment (an early flush inside the frame)."""
        kind, name = key
        current = self.s[kind].get(name, MISSING)
        rid = name if kind == "entities" else kind + ":" + name
        skip = self._wqfields.get(name) if kind == "entities" else None
        before = self._wstart(key)
        n = len(segs)
        for i in range(n):
            after = segs[i + 1][3] if i + 1 < n else current
            self._wdiff(rid, kind, segs[i], before, after, skip)
            before = after
        if rebase:
            self._wbase[key] = MISSING if current is MISSING else clone(current)

    def _wdiff(self, rid, kind, seg, before, after, skip):
        """The write records of one segment: the top-level fields that differ between its start and end records."""
        wr, causes, t = seg[0], seg[1], seg[2]
        pend = self._wpend
        if before is MISSING:
            if after is MISSING:
                return
            pend.append(("c", rid, wr[0], wr[1], t))
            pend.append(("w", rid, EXISTS, wr[0], wr[1], t, causes))
            fields = sorted(f for f in after if not skip or f not in skip)
        elif after is MISSING:
            pend.append(("w", rid, EXISTS, wr[0], wr[1], t, causes))
            return
        else:
            if before == after:
                return
            get = before.get
            fields = [f for f, v in after.items() if get(f, MISSING) != v]
            fields.extend(f for f in before if f not in after)
            if skip:
                fields = [f for f in fields if f not in skip]
            fields.sort()
        for field in fields:
            pend.append(("w", rid, field, wr[0], wr[1], t, causes))
        if wr[0] == "robot" and kind == "entities" and any(f in PLACEMENT_FIELDS for f in fields):
            pend.append(("p", rid, {f: clone(after[f]) for f in PLACEMENT_FIELDS if f in after}, t))

    def _wcommit(self):
        """Hand the frame's records to the log (segment diffs, then the quiet writes) and start an empty frame."""
        for key, segs in self._wseg.items():
            self._wflush_key(key, segs)
        if self._wlog is not None:
            if self._wpend:
                self._wlog.absorb(self._wpend)
            if self._wquiet:
                self._wlog.absorb_quiet(self._wquiet)
        self._wclear()

    def reclassify(self, ids, old, new):
        """The engine's substep settle pass: the open segments of `old` (a writer) on these entity ids become `new`'s
        (the kernel physics attributed those consequences to the robot: TIER1_PHYSICS 6.4, VERIFY_HARDENING 6.2 "physics
        flagged _robot_moved in the same step"); so do their logs.gone entries of this moment."""
        if not self._wtrack:
            return
        hit = set()
        for eid in ids:
            segs = self._wseg.get(("entities", eid)) if isinstance(eid, str) else None
            if segs and segs[-1][0] == old:
                segs[-1][0] = new
                hit.add(eid)
        gone = (self.s.get("logs") or {}).get("gone")
        if not hit or not gone:
            return
        now = self.s.get("time_s", 0.0)
        fix = [i for i in range(len(gone) - 1, -1, -1)
               if gone[i].get("t") == now and gone[i].get("entity") in hit and gone[i].get("by") == old[0]
               and gone[i].get("name") == old[1]]
        if fix:
            logs = self.sec("logs")
            for i in fix:
                logs["gone"][i]["by"], logs["gone"][i]["name"] = new[0], new[1]

    # ----- mess flows and destroyed records (WP2) ---------------------
    def mess_flow(self, kind, amount, src, dst, origin=None, cause=None):
        """Log that `amount` of mess kind `kind` (a registered kind, e.g. "debris", "liquid:mop_water") moved from
        `src` to `dst` (location keys, module docstring) with the writer of the current window (6.5). Returns the log
        entry (merged into an identical earlier one since the last transform), or None when the World does not track
        or the amount is not positive."""
        if not isinstance(kind, str) or not kind:
            raise ValueError("mess flow: kind must be a non-empty string")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount != amount or amount < 0:
            raise ValueError("mess flow: amount must be a nonnegative number (got {!r})".format(amount))
        src = location_key(src, FLOW_SRC, "src")
        dst = location_key(dst, FLOW_DST, "dst")
        if origin is not None and not isinstance(origin, str):
            raise ValueError("mess flow: origin must be a string id or None")
        if not self._wtrack or amount <= 0:
            return None
        if origin is None and src.startswith("created:"):
            origin = "created:{}".format(self.s.get("steps", 0))
        wr, now = self._writer, self.s.get("time_s", 0.0)
        flows = self.sec("logs").setdefault("mess_flows", [])
        for entry in reversed(flows):
            if entry.get("op") == "transform":
                break
            if entry.get("kind") == kind and entry.get("src") == src and entry.get("dst") == dst and \
                    entry.get("origin") == origin and entry.get("by") == wr[0] and entry.get("name") == wr[1]:
                entry["amount"] = round(entry["amount"] + float(amount), 9)
                entry["t"] = now
                entry["n"] = entry.get("n", 1) + 1
                if cause is not None:
                    entry["cause"] = sorted(set(entry.get("cause") or ()) | _causes(cause))
                return entry
        entry = {"op": "flow", "t0": now, "t": now, "kind": kind, "amount": round(float(amount), 9), "src": src,
                 "dst": dst, "origin": origin, "by": wr[0], "name": wr[1], "n": 1}
        causes = _causes(cause) or self._wcause
        if causes:
            entry["cause"] = sorted(causes)
        flows.append(entry)
        return entry

    def mess_transform(self, origins, new_kind, amount, at=None, cause=None):
        """Log that `amount` (in new_kind's unit) of the mess of the origin groups `origins` became kind new_kind (a
        class change, 6.5): a transform keeps the groups together, and identical flows merge only within the
        stretch between two transforms. at: the location key where it happened (optional)."""
        if isinstance(origins, str):
            origins = [origins]
        if not isinstance(origins, (list, tuple)) or not origins or not all(isinstance(o, str) for o in origins):
            raise ValueError("mess transform: origins is a non-empty list of origin ids")
        if not isinstance(new_kind, str) or not new_kind:
            raise ValueError("mess transform: new_kind must be a non-empty string")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount != amount or amount < 0:
            raise ValueError("mess transform: amount must be a nonnegative number (got {!r})".format(amount))
        if at is not None:
            at = location_key(at, FLOW_SRC + ("loss",), "at")
        if not self._wtrack:
            return None
        wr = self._writer
        entry = {"op": "transform", "t": self.s.get("time_s", 0.0), "origins": sorted(set(origins)),
                 "kind": new_kind, "amount": round(float(amount), 9), "at": at, "by": wr[0], "name": wr[1]}
        causes = _causes(cause) or self._wcause
        if causes:
            entry["cause"] = sorted(causes)
        self.sec("logs").setdefault("mess_flows", []).append(entry)
        return entry

    def gone_record(self, eid):
        """The final record of an entity destroyed during the episode (the last logs.gone entry for it; read-only),
        or None (people's phase-1 request: answer about a thing made and destroyed again like one out of view)."""
        for entry in reversed((self.s.get("logs") or {}).get("gone") or ()):
            if entry.get("entity") == eid:
                return entry.get("last")
        return None

    # ----- journaling -------------------------------------------------
    def begin(self):
        if self._static and (self._journal or self._quiet):
            self._absorb(self.changes())        # writes nobody committed: the static indexes still learn of them
        if self._wtrack:
            self._wcommit()                     # writes made outside a frame happened: they are logged
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
        if self._wtrack:
            self._wnote(key, value)

    def journal_keys(self):
        return list(self._journal) + [("entities", eid) for eid in self._quiet if ("entities", eid) not in self._journal]

    def journaled_entities(self):
        """Ids of the entities whose whole record was journaled this step (mut, create, destroy, mut_quiet), in the
        order of their first write; entities with only quiet field writes (set_quiet: kernel thermal's temperatures)
        are not listed. With a static index over step-start records, `index | journaled` is every entity whose current
        record can match (cleaning's phase-1 finish request)."""
        return [name for kind, name in self._journal if kind == "entities"]

    def touch_log_complete(self):
        """True when touched_since(0) returns every entity write of the step so far: nothing was trimmed since the last
        begin() or rollback() (trim_touched drops read prefixes; rollback_to truncates the log without moving any
        absolute cursor). textiles_laundry's phase-1 request: the contract its scan filter relies on, made explicit."""
        return self._touched_base == 0

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
        restored = [name for kind, name in self._journal if kind == "entities"] + list(self._quiet)
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
        self._structural(restored)
        self._wclear()                          # the frame's writes are undone: nothing of them is logged

    # ----- savepoints (TIER1_SWEEP 6.4, TIER1_INTEGRATION 1.8) ----------
    def savepoint(self):
        """Open a journal layer inside the step and return its token. Every write after it is recorded (first value per
        key) in the layer as well as in the step journal, so rollback_to(sp) can restore exactly the state at sp. The
        writer log's frame data is marked too (WP2: the segment count per record, the quiet writes, the pending
        records), so rollback_to drops exactly what was logged after sp."""
        scratch = {}
        for key, value in self.phys_scratch.items():
            scratch[key] = value.copy() if isinstance(value, (dict, list, set)) else value
        layer = {"journal": {}, "quiet": {}, "touched": self._touched_base + len(self._touched),
                 "scratch": scratch, "heard": len(self._heard)}
        if self._wtrack:
            layer["wlog"] = ({key: (len(segs), segs[-1][2], segs[-1][1]) for key, segs in self._wseg.items()},
                             dict(self._wquiet), dict(self._wsec), len(self._wpend))
        self._layers.append(layer)
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
        restored = []
        for layer in reversed(self._layers[sp - 1:]):
            restored.extend(name for kind, name in layer["journal"] if kind == "entities")
            restored.extend(layer["quiet"])
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
        self._structural(restored)
        # the writer log: drop what was logged after sp (segments opened after it, the quiet and section writes)
        marks = first.get("wlog")
        if marks is not None and self._wtrack:
            counts, quiet, sections, pending = marks
            for key in list(self._wseg):
                mark = counts.get(key)
                if mark is None:
                    del self._wseg[key]
                    continue
                segs = self._wseg[key]
                del segs[mark[0]:]
                segs[-1][2], segs[-1][1] = mark[1], mark[2]
            self._wquiet, self._wsec = quiet, sections
            del self._wpend[pending:]

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
        if self._static:
            self._absorb(changed)
        if self._wtrack:
            self._wcommit()                     # needs the journal (each record's frame start)
        self._journal = {}
        self._quiet = {}
        self._layers = []
        self._writer, self._wcause = EXTERNAL_WRITER, None
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

    def mut(self, eid, cause=None):
        """A mutable entity record (journaled first). cause: ids this write is a consequence of (the writer log)."""
        record = self.ent(eid)
        self._snap(("entities", eid), record)
        if cause is not None and self._wtrack:
            self._wadd_causes(self._wseg[("entities", eid)][-1], (cause,) if isinstance(cause, str) else cause)
        return record

    def set_quiet(self, eid, field, value, cause=None):
        """Set one field of an entity, journaled at field level (no copy of the record) and not a touch: the kernel
        thermal system's temperature writes, hundreds per run. Rollback, changes() and strict mode treat it like a
        journaled write; mutation_count counts it. The writer log records it at once (class physical: VERIFY_HARDENING
        6.2 quiet writes), when the value changes."""
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
        if self._wtrack and record.get(field, MISSING) != value:
            wr = self._writer
            if wr[0] != "physical":
                wr = _PHYSICAL.get(wr) or _PHYSICAL.setdefault(wr, ("physical", wr[1]))
            self._wquiet[(eid, field)] = (wr, self.s.get("time_s", 0.0), _causes(cause) or self._wcause)
            done = self._wqfields.get(eid)
            if done is None:
                self._wqfields[eid] = {field}
            else:
                done.add(field)
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
        if self._wtrack:
            self._wnote(key, record)        # its changes are diffed like a mut's (it is no touch, but a write)
        return record

    def create(self, eid, record, cause=None):
        if eid in self.s["entities"] or eid in self.s["rooms"] or eid in RESERVED_IDS:
            raise ValueError("Entity id already used or reserved: " + eid)
        self._version += 1
        self._snap(("entities", eid), MISSING)
        self.s["entities"][eid] = record
        self._structural((eid,))
        if cause is not None and self._wtrack:
            self._wadd_causes(self._wseg[("entities", eid)][-1], (cause,) if isinstance(cause, str) else cause)
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

    def destroy(self, eid, cause=None):
        """Remove an entity. In a tracked episode its final record joins logs.gone (8.6: failure predicates and
        answerers read it) with the writer that destroyed it."""
        self._version += 1
        record = self.ent(eid)
        self._snap(("entities", eid), record)
        if self._wtrack:
            if cause is not None:
                self._wadd_causes(self._wseg[("entities", eid)][-1], (cause,) if isinstance(cause, str) else cause)
            wr = self._writer
            gone = self.sec("logs").setdefault("gone", [])
            gone.append({"t": self.s.get("time_s", 0.0), "entity": eid, "type": record.get("type"),
                         "location": record.get("location"), "by": wr[0], "name": wr[1], "last": clone(record)})
            if len(gone) > GONE_CAP:
                del gone[:len(gone) - GONE_CAP]
        del self.s["entities"][eid]
        self._structural((eid,))

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

    def static_index(self, name, predicate, reads=None):
        """Sorted ids of entities matching a predicate of one entity record (type, is_a, category, fixed tags, ...),
        cached per name. Use it in systems instead of scanning every entity each tick; re-check on the returned
        records what can change within a step.

        Contract (independent review M3: a stale index made a saved and reloaded episode compute something the
        uninterrupted one did not): the index is the set of entities whose record *as it was when the current step
        began* matches, plus or minus the entities created or destroyed since (those at once, a created one with its
        current record). A field the predicate reads that changes during a step counts from the next step on. So the
        content is a pure function of the state, never of the cache's history: a reloaded episode, a replay and the
        uninterrupted run see the same index at every lookup. A predicate must read only its record.

        How it stays cheap: every evaluation reads the record through a view that notes the fields the predicate
        reads; when a step commits, an entity enters the pending set of exactly the indexes that read one of its
        changed fields (thermal's temperature writes, read by none, cost nothing); creations, destructions and
        rollbacks are applied at the next lookup. A caller that knows every field its predicate reads may declare them
        (reads=("type", "is_a")); the predicate then sees the plain record (strict mode checks the declaration)."""
        entry = self._static.get(name)
        end = self._struct_base + len(self._struct)
        if entry is None or entry[2] < self._struct_base:
            members = self._matching(name, predicate, self.s["entities"], reads)
            ids = sorted(members)
            self._static[name] = [ids, members, end, set()]
            return ids
        if entry[3] or entry[2] != end:
            check = entry[3]
            check.update(self._struct[entry[2] - self._struct_base:])
            members = entry[1]
            inside = self._matching(name, predicate, check, reads)
            if any((eid in inside) != (eid in members) for eid in check):
                members.difference_update(check)
                members.update(inside)
                entry[0] = sorted(members)
            entry[2], entry[3] = end, set()
        return entry[0]

    def _start_view(self, eid, record, keys):
        """A read view of eid's record as the step began: its journal snapshot when it was written this step (with
        its quiet fields' step-start values), else the record itself (created this step, or untouched)."""
        before = self._journal.get(("entities", eid))
        if before is MISSING:
            before = None                       # created this step: as it is
        start = self._quiet.get(eid)
        if start:
            return _StartView(record if before is None else before, keys, start)
        return _ReadView(record if before is None else before, keys)

    def _matching(self, name, predicate, ids, reads=None):
        """The ids among `ids` whose step-start record matches the predicate. The fields it reads (through a read
        view; `reads`: the fields the caller declares it reads) join the index's read set.

        With declared reads the predicate sees a plain record wherever that record is the entity's step-start record
        for the declared fields (phase-1 finish, seven domains' request: the read views made every build inside a step
        two to three times dearer): an entity not journaled this step is its own record, a journaled one its journal
        snapshot (one created this step: as it is), and a quiet write (set_quiet) matters only when it wrote a declared
        field -- only such an entity is read through a start view. Strict mode reads every entity through a view, also
        between steps, and checks the declaration (electrical's request: a plain-record build no longer escapes it)."""
        entities, journal, quiet = self.s["entities"], self._journal, self._quiet
        keys, out = set(), set()
        plain = reads is not None and not STRICT_INDEX
        declared = frozenset(reads) if plain and quiet else None
        for eid in ids:
            record = entities.get(eid)
            if record is None:
                continue
            if plain:
                start = quiet.get(eid) if quiet else None
                if not start or declared.isdisjoint(start):
                    before = journal.get(("entities", eid)) if journal else None
                    if predicate(record if before is None or before is MISSING else before):
                        out.add(eid)
                    continue
            view = self._start_view(eid, record, keys) if journal or quiet else _ReadView(record, keys)
            try:
                result = predicate(view)
            except Exception:  # noqa: BLE001 -- a predicate that needs a real dict reads it whole (real errors re-raise)
                whole = view._whole() if isinstance(view, _StartView) else view._record
                result = predicate(whole)
                keys.add(READS_ALL)
            if STRICT_INDEX:
                whole = view._whole() if isinstance(view, _StartView) else view._record
                if bool(result) != bool(predicate(whole)):
                    raise RuntimeError("Static index {!r}: its predicate answers differently on a read view".format(name))
            if result:
                out.add(eid)
        if reads is not None:
            if STRICT_INDEX and not keys <= set(reads):
                raise RuntimeError("Static index {!r} reads {} beyond its declared fields".format(
                    name, sorted(keys - set(reads))))
            keys = set(reads)
        for key in keys:
            if key == READS_ALL:
                self._read_all.add(name)
                continue
            readers = self._readers.get(key)
            if readers is None:
                self._readers[key] = {name}
            elif name not in readers:
                readers.add(name)
        return out

    def _absorb(self, changed):
        """A step's committed changes (changes() leaf diffs): each changed entity becomes pending in the static indexes
        that read one of its changed fields (all of them for a record created or removed as a whole)."""
        fields = {}
        for diff in changed:
            path = diff["path"]
            if path[:9] != "entities.":
                continue
            parts = path.split(".", 3)
            fields.setdefault(parts[1], set()).add(parts[2] if len(parts) > 2 else None)
        static, readers, read_all = self._static, self._readers, self._read_all
        for eid, names in fields.items():
            if None in names:
                targets = static.keys()
            else:
                targets = set(read_all)
                for field in names:
                    targets.update(readers.get(field, ()))
            for name in targets:
                entry = static.get(name)
                if entry is not None:
                    entry[3].add(eid)

    def _structural(self, ids):
        """Entities created, destroyed or restored by a rollback: every static index applies them at its next lookup."""
        if not self._static:
            return
        self._struct.extend(ids)
        if len(self._struct) > STRUCT_CAP:
            # an index whose cursor falls before the kept part rebuilds in full at its next lookup (the same content)
            drop = len(self._struct) // 2
            del self._struct[:drop]
            self._struct_base += drop

    def of_type(self, type_name):
        """Entity ids whose type is type_name or derives from it (cached)."""
        return [e for e in self.static_index("type:" + type_name,
                lambda r: r.get("type") == type_name or type_name in r.get("is_a", []), reads=("type", "is_a"))
                if e in self.s["entities"]]

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

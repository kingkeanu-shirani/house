"""Timed physics processes (docs/design/TIER1_PHYSICS.md 6.2; fidelity P-approx) and the plumbing shared by the
liquids, thermal and environment modules.

``run(w, dt, tick)`` is the body of the system ``physics.processes`` (order 48, max_dt 30). It is registered by
kernel/physics.py (PHYS-INT), never here: this module registers nothing at import (TIER1_INTEGRATION 1.16).

The state section ``physics`` is created lazily by the first process and is absent from every other state:

    {"seq": n,                                      next queue sequence number
     "queue": [[due_s, seq, kind, eid, args], ...], one-shot checks sorted by (due_s, seq), popped when due
     "active": {kind: [ids]}}                       continuous processes, ticked every substep (sorted ids per kind)

A process's parameters live on its entity under ``_phys_<kind>`` (underscore fields are never observed); the glance
cue it shows (``in_motion``, ``dripping``, ``leaking``) is written by the process's begin function and removed by its
end function.

Queue kinds (one-shot checks, ``fn(w, eid, args)``): ``wet_contact`` (thermal: a ring stain, swelling or water damage
under a wet item), plus the statics timers impact.py owns and lists in ``impact.PROCESS_HANDLERS``: ``creep`` (a
staged support still overloaded at its creep time collapses) and ``handle_creep`` (a bag or box carried at 0.9-1.0x
its handle rating tears after 120 s).

Active kinds, ticked in this order every substep, ``tick(w, eid, dt) -> keep running``: ``rolling`` (here),
``dripping`` and ``leaking`` (liquids), ``contact`` (contact-heat accrual), ``phase`` (freezing and thawing liquids)
and ``melting`` (thermal). Cost per substep is O(due + active); with nothing queued or active ``run`` returns after one
dict lookup.

Shared plumbing for the sibling modules of this package:

* ``sibling(name)``: lazy access to the modules other stage-A packages own (props, lawbook, support, statics, impact).
* ``consequence``: a consequence record in the step's shared list (impact.consequences: settle reads it for its
  notes, its 64-consequence budget and the undo rule) plus its ``phys.*`` event.
* ``robot_attributed``: the harm-attribution rule of 6.4 for a ``by`` argument.
* ``num``, ``finite``, ``dims``, ``top_area_m2``, ``floor_cell``, ``now_s``: small pure helpers.
"""
import importlib
import math
from bisect import insort

from .. import geometry

G = 9.81
QUEUE_KINDS = ("wet_contact",)          # this package's one-shot checks; impact.PROCESS_HANDLERS adds the statics ones
ACTIVE_KINDS = ("rolling", "dripping", "leaking", "contact", "phase", "melting")
MAX_DUE_PER_RUN = 256           # one-shot checks fired per substep at most (a cascade stops the same way every time)
DEFAULT_SIZE_M = (0.1, 0.1, 0.1)

# rolling (5.4): a solid sphere rolls down a slope with (5/7) g sin(theta), a solid cylinder on its side with (2/3) g,
# a wheeled cart with about g sin(theta) (its wheels are light next to the load); rolling resistance takes c_rr g cos
ROLL_INERTIA = {"sphere": 5.0 / 7.0, "cylinder": 2.0 / 3.0}
RUNAWAY_MPS = 1.0               # faster than this at the edge it does not stop by itself (5.4)
DOWNHILL_DEG = {"east": 0.0, "south": 90.0, "west": 180.0, "north": 270.0}   # room "downhill" names, grid degrees


# ----- shared plumbing ---------------------------------------------------------------------------------------------

class _Sibling(object):
    """A module of the physics layer owned by another stage-A package, imported on first attribute access.

    TIER1-STUB(PHYS-INT): props and lawbook (PHYS-DATA) and support, statics and impact (PHYS-STATICS) are written in
    parallel with this package against the frozen signatures of TIER1_PHYSICS 7.1. PHYS-INT replaces each
    ``sibling(...)`` proxy with a plain ``from . import <name>`` once they have landed. Tests substitute the proxies
    with fakes (``liquids.props = fake``), which keeps them independent of the other packages.
    """
    __slots__ = ("_name", "_module")

    def __init__(self, name):
        self._name = name
        self._module = None

    def __getattr__(self, attr):
        if self._module is None:
            self._module = importlib.import_module(__package__ + "." + self._name)
        return getattr(self._module, attr)

    def __repr__(self):
        return "<physics sibling {}>".format(self._name)


def sibling(name):
    """Lazy handle on the physics module ``house_sim.kernel.phys.<name>`` (see _Sibling)."""
    return _Sibling(name)


props = sibling("props")
statics = sibling("statics")
impact = sibling("impact")


def emit_consequence(w, kind, target, **data):
    """Write a physics event (event-log kind ``phys.*``) with its oracle payload.

    TIER1-STUB(PHYS-INT): phys/observe.py's emit_consequence (created by PHYS-INT, routed through the camera's visual
    events at CAM-RUNTIME and posted as notices at TIME-EVAL) replaces this direct ``w.emit``."""
    return w.emit(kind, target=target, **data)


def consequence(w, kind, target, by, note, **data):
    """Record one consequence: append {"kind", "target", "by", "note", ...data} to the step's consequence list
    (impact.consequences, shared with the statics laws) and emit the event ``kind`` (phys.*) with the same payload.
    by is "robot" or "exogenous"; note names ids only (no numbers); data may carry oracle numbers. Returns the
    record."""
    record = {"kind": kind, "target": target, "by": by, "note": note}
    record.update(data)
    impact.consequences(w).append(record)
    emit_consequence(w, kind, target, by=by, **data)
    return record


def robot_attributed(w, eid, by=None):
    """Whether a consequence on eid counts as robot-caused (TIER1_PHYSICS 6.4): by "robot" or another label decides;
    None asks impact.robot_caused (action-phase settle, or the robot touched the entity's support tree)."""
    if by is not None:
        return by == "robot"
    if not isinstance(eid, str) or eid not in w.s["entities"]:
        return False
    return bool(impact.robot_caused(w, eid))


def blame(w, eid, by=None):
    """"robot" or "exogenous" for a consequence on eid (robot_attributed)."""
    return "robot" if robot_attributed(w, eid, by) else "exogenous"


def num(value, default=0.0):
    """value as a finite float, else default (domain records are not always clean)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def finite(value):
    """value as a float when it is a finite number, else None (props uses inf for 'no limit')."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def dims(rec):
    """(w, d, h) of a record's size_m (defaults for missing or malformed sizes)."""
    size = rec.get("size_m") or DEFAULT_SIZE_M
    w = num(size[0], DEFAULT_SIZE_M[0]) if len(size) > 0 else DEFAULT_SIZE_M[0]
    d = num(size[1], w) if len(size) > 1 else w
    h = num(size[2], DEFAULT_SIZE_M[2]) if len(size) > 2 else DEFAULT_SIZE_M[2]
    return max(0.0, w), max(0.0, d), max(0.0, h)


def top_area_m2(rec):
    """Area of an entity's top (m2): its surface_size_m when it declares one (tables, counters), else w x d."""
    top = rec.get("surface_size_m")
    if isinstance(top, (list, tuple)) and len(top) >= 2:
        return max(0.0, num(top[0])) * max(0.0, num(top[1]))
    w, d, _ = dims(rec)
    return w * d


def floor_cell(state, eid):
    """(room, cell) of the floor directly under an entity: its anchor cell, the holder's cell for held items; cell is
    None when the entity has no footprint."""
    rid, cells = geometry.anchor(state, eid)
    if rid is None:
        robot = state["robot"]
        return robot.get("room"), tuple(robot.get("pos") or (0, 0))
    if cells:
        return rid, tuple(cells[0])
    return rid, None


def now_s(state):
    return float(state.get("time_s", 0.0) or 0.0)


# ----- the state section --------------------------------------------------------------------------------------------

def section(state):
    """The ``physics`` section, or None before the first process (read-only view)."""
    return state.get("physics")


def _section_m(w):
    sec = w.sec("physics")
    if "seq" not in sec:
        sec["seq"] = 0
        sec["queue"] = []
        sec["active"] = {}
    return sec


def queue_add(w, due_s, kind, eid, **args):
    """Schedule the one-shot check ``kind`` on eid at simulated time due_s (args: JSON values). Ties at one due time
    fire in the order they were added. Returns the entry's sequence number."""
    if _queue_handler(kind) is None:
        raise ValueError("Unknown physics queue kind: " + str(kind))
    sec = _section_m(w)
    seq = int(sec["seq"])
    sec["seq"] = seq + 1
    insort(sec["queue"], [round(float(due_s), 3), seq, kind, eid, dict(sorted(args.items()))])
    return seq


def queued(state, kind, eid):
    """The queued entries of one kind for eid ([due_s, seq, kind, eid, args] lists, in firing order)."""
    sec = section(state)
    if not sec:
        return []
    return [entry for entry in sec.get("queue", ()) if entry[2] == kind and entry[3] == eid]


def cancel(w, kind, eid):
    """Drop every queued check of one kind for eid; returns how many were dropped."""
    if not queued(w.s, kind, eid):
        return 0
    sec = _section_m(w)
    before = len(sec["queue"])
    sec["queue"] = [entry for entry in sec["queue"] if not (entry[2] == kind and entry[3] == eid)]
    return before - len(sec["queue"])


def active(state, kind):
    """Ids running the continuous process ``kind`` (sorted)."""
    sec = section(state)
    return tuple((sec or {}).get("active", {}).get(kind, ()))


def is_active(state, kind, eid):
    return eid in active(state, kind)


def params(state, kind, eid):
    """The parameters of eid's ``kind`` process (its ``_phys_<kind>`` field; empty when not running)."""
    rec = state["entities"].get(eid) or {}
    value = rec.get("_phys_" + kind)
    return value if isinstance(value, dict) else {}


def start(w, kind, eid, **values):
    """Start (or update) the continuous process ``kind`` on eid. values are merged into its parameters; the kind's
    begin function completes them and may decline (None: nothing is written). Returns True when it runs."""
    if kind not in ACTIVE_KINDS:
        raise ValueError("Unknown physics process kind: " + str(kind))
    if eid not in w.s["entities"]:
        return False
    merged = dict(params(w.s, kind, eid))
    merged.update(values)
    merged = _handlers()[kind][0](w, eid, merged)
    if merged is None:
        return False
    w.mut(eid)["_phys_" + kind] = merged
    sec = _section_m(w)
    ids = sec["active"].setdefault(kind, [])
    if eid not in ids:
        insort(ids, eid)
    return True


def set_params(w, kind, eid, **values):
    """Update the parameters of a running process (no-op when eid does not run it)."""
    current = params(w.s, kind, eid)
    if not current:
        return
    merged = dict(current)
    merged.update(values)
    w.mut(eid)["_phys_" + kind] = merged


def stop(w, kind, eid):
    """Stop eid's ``kind`` process: its end function clears the glance cue, then its parameters go. Returns True when
    it was running."""
    if kind not in ACTIVE_KINDS:
        raise ValueError("Unknown physics process kind: " + str(kind))
    if not is_active(w.s, kind, eid):
        return False
    _forget(w, kind, eid)
    if eid in w.s["entities"]:
        _handlers()[kind][2](w, eid)
        if "_phys_" + kind in w.s["entities"][eid]:
            w.mut(eid).pop("_phys_" + kind, None)
    return True


def _forget(w, kind, eid):
    sec = _section_m(w)
    ids = sec["active"].get(kind, [])
    if eid in ids:
        ids.remove(eid)
    if not ids:
        sec["active"].pop(kind, None)


def run(w, dt, tick):
    """System physics.processes: fire the checks that are due, in (due_s, seq) order and at most MAX_DUE_PER_RUN, then
    tick every active process in ACTIVE_KINDS order and sorted ids (a process whose entity is gone just ends).
    Registered by PHYS-INT (order 48, max_dt 30)."""
    sec = w.s.get("physics")
    if not sec:
        return
    entities = w.s["entities"]
    fired = 0
    while fired < MAX_DUE_PER_RUN:
        queue = w.s["physics"].get("queue")
        if not queue or queue[0][0] > now_s(w.s) + 1e-9:
            break
        _, _, kind, eid, args = w.sec("physics")["queue"].pop(0)
        fired += 1
        if eid in entities:
            _queue_handler(kind)(w, eid, args)
    if not w.s["physics"].get("active"):
        return
    handlers = _handlers()
    for kind in ACTIVE_KINDS:
        ids = w.s["physics"]["active"].get(kind)
        if not ids:
            continue
        tick_fn = handlers[kind][1]
        for eid in list(ids):
            if eid not in entities:
                _forget(w, kind, eid)
            elif not tick_fn(w, eid, dt):
                stop(w, kind, eid)


_HANDLERS = None


def _handlers():
    """kind -> (begin, tick, end) for the active kinds and kind -> fire(w, eid, args) for this package's queue
    kinds; resolved on first use because liquids and thermal import this module."""
    global _HANDLERS
    if _HANDLERS is None:
        from . import liquids, thermal
        _HANDLERS = {
            "rolling": (roll_begin, roll_tick, roll_end),
            "dripping": (liquids.drip_begin, liquids.drip_tick, liquids.drip_end),
            "leaking": (liquids.leak_begin, liquids.leak_tick, liquids.leak_end),
            "contact": (thermal.contact_begin, thermal.contact_tick, thermal.contact_end),
            "phase": (thermal.phase_begin, thermal.phase_step, thermal.phase_end),
            "melting": (thermal.melt_begin, thermal.melt_tick, thermal.melt_end),
            "wet_contact": thermal.wet_contact_due,
        }
    return _HANDLERS


def _queue_handler(kind):
    """fire(w, eid, args) of a queue kind: this package's, else the statics timers impact.PROCESS_HANDLERS lists."""
    if kind in QUEUE_KINDS:
        return _handlers()[kind]
    return impact.PROCESS_HANDLERS.get(kind)


# ----- rolling (the process the slope law starts, 5.4 and 6.2) ------------------------------------------------------

def roll_accel(state, eid):
    """Acceleration (m/s2) of eid rolling down what it rests on (statics' tilt and rolling resistance): k g
    (sin theta - c_rr cos theta) with k = 5/7 for a sphere, 2/3 for a cylinder on its side, 1 for a wheeled cart;
    0 when it would not roll."""
    rec = state["entities"][eid]
    wheeled = bool(rec.get("casters") or rec.get("wheeled"))
    if rec.get("secured") or rec.get("chocked") or not (wheeled or props.props(state, eid).rollable):
        return 0.0
    rad = math.radians(min(89.9, statics.support_tilt_deg(state, eid)))
    c_rr = statics.rolling_resistance(state, eid)
    if rad <= 0 or math.tan(rad) <= c_rr:
        return 0.0
    k = 1.0 if wheeled else ROLL_INERTIA.get(props.props(state, eid).shape, ROLL_INERTIA["sphere"])
    return k * G * (math.sin(rad) - c_rr * math.cos(rad))


def downhill_deg(state, eid):
    """Grid direction (degrees, 0 = +x east, 90 = +y south) a floor roll heads: the room's tilt_dir_deg, else its
    "downhill" compass name, else south (manipulation's runaway default)."""
    room = state["rooms"][state["entities"][eid]["location"]]
    explicit = finite(room.get("tilt_dir_deg"))
    if explicit is not None:
        return explicit % 360.0
    return DOWNHILL_DEG.get(room.get("downhill") or "south", DOWNHILL_DEG["south"])


def _floor_run(state, eid):
    """(distance m, end cell) of a floor roll from eid's cell to the room's edge along the downhill direction."""
    room = state["rooms"][state["entities"][eid]["location"]]
    cols, rows = geometry.grid_size(room)
    col, row = (state["entities"][eid].get("pos") or (0, 0))[:2]
    rad = math.radians(downhill_deg(state, eid))
    ux, uy = math.cos(rad), math.sin(rad)
    steps = []
    if abs(ux) > 1e-9:
        steps.append(((cols - 1 - col) if ux > 0 else col) / abs(ux))
    if abs(uy) > 1e-9:
        steps.append(((rows - 1 - row) if uy > 0 else row) / abs(uy))
    cells = min(steps) if steps else 0.0
    end = [min(cols - 1, max(0, int(round(col + ux * cells)))), min(rows - 1, max(0, int(round(row + uy * cells))))]
    return (cells + 0.5) * geometry.CELL, end


def roll_begin(w, eid, values):
    """Start rolling when the incline beats rolling resistance (else decline): distance to the nearest edge of the
    surface (statics.edge_margin_m; half the narrower side without a placement) or to the room's downhill edge.
    Shows in_motion: "rolling"."""
    state = w.s
    accel = roll_accel(state, eid)
    if accel <= 0:
        return None
    rec = state["entities"][eid]
    on_floor = rec.get("location") in state["rooms"]
    if on_floor:
        distance = _floor_run(state, eid)[0]
    else:
        margin = finite(statics.edge_margin_m(state, eid))
        if margin is None:
            top = state["entities"][rec["location"]]
            margin = min((top.get("surface_size_m") or dims(top)[:2])[:2]) / 2.0
        distance = max(0.0, margin)
    if rec.get("in_motion") != "rolling":
        w.mut(eid)["in_motion"] = "rolling"
    return {"v": round(num(values.get("v")), 4), "a": round(accel, 4), "floor": on_floor,
            "d": round(num(values.get("d"), distance), 4)}


def roll_tick(w, eid, dt):
    """Advance the roll by dt (constant acceleration). At the edge the item rolls off its surface (impact.fall,
    toward the nearest edge, records the fall) or ends against the room's downhill edge (a phys.roll consequence);
    either way event phys.roll gives the speed there and whether it was a runaway (faster than 1 m/s). The slope law
    counted the roll (phys_rolls) when it started it."""
    p = params(w.s, "rolling", eid)
    v, a, d = num(p.get("v")), num(p.get("a")), num(p.get("d"))
    travelled = v * dt + 0.5 * a * dt * dt
    if travelled < d:
        set_params(w, "rolling", eid, v=round(v + a * dt, 4), d=round(d - travelled, 4))
        return True
    state = w.s
    edge_v = math.sqrt(max(0.0, v * v + 2.0 * a * d))
    payload = {"room": floor_cell(state, eid)[0], "speed_mps": round(edge_v, 3), "runaway": edge_v > RUNAWAY_MPS}
    roll_end(w, eid)
    if p.get("floor"):
        end = _floor_run(state, eid)[1]
        if list(state["entities"][eid].get("pos") or ()) != end:
            w.mut(eid)["pos"] = end
        consequence(w, "phys.roll", eid, blame(w, eid), "{} rolled away".format(eid), ended="floor_edge", **payload)
    else:
        emit_consequence(w, "phys.roll", eid, by=blame(w, eid), ended="fell_off", **payload)
        impact.fall(w, eid, cause="rolled_off")
    return False


def roll_end(w, eid):
    if w.s["entities"].get(eid, {}).get("in_motion") == "rolling":
        w.mut(eid).pop("in_motion", None)

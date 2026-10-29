"""Measuring, leveling and marking (fidelity G: hidden true geometry + instrument error).

Ground truth is hidden: an object's true extents including legs and handles are `_extents_mm`
(observable size_m is only its nominal body size); spaces, openings and spans carry `_measure`
({name: mm} or {name: {"floor": mm, "mid": mm, "top": mm}} height profiles, "to:<id>": mm
distances); doorways use the connection's `_clear_width_mm`.
Instruments (cited magnitudes):
* tape measure, EU class II: +/-(0.3 + 0.2 L[m]) mm plus about 0.5 mm hook play; an inside
  (surface-to-surface) reading must add the case length (76 mm) printed on the tape; unsupported
  spans over 2.4 m sag and read long (about 7 mm at 4 m).
* laser distance meter: +/-2 mm between surfaces; it cannot hook an object's outer edge
  (+/-12 mm when aimed at edges in free air).
* estimate (no tool / odometry): 2-10 % off (never within a tape's tolerance), 10 mm resolution.
* spirit level: 0.1 deg reading resolution (digital 0.05 deg); eyeballing +/-0.8 deg.
Instrument errors are fixed draws per (episode seed, target, quantity, instrument): repeating a reading, from any spot
or at any time, gives the same number, so averaging cannot beat the instrument (realistic perception's "no free
averaging"; the old per-step redraw let 40 estimates recover hidden extents to tape precision).
Leveling feet: entities with `leveling` {floor_mm: {fl, fr, bl, br}, span_mm: [w, d]} and
settings foot_<corner>_mm (-10..25) and lock_nuts (loose/locked) compute `_tilt_deg` [x, y] and
`_wobble_mm` (diagonal rock). A running appliance that is not level, rocks, or has loose lock
nuts walks across the floor (`_walked_mm`), and after 30 mm it bangs into the cabinetry.
"""
import copy
import math
import random

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook, system
from . import common as C

MM_PER_IN = 25.4
FEET = ("fl", "fr", "bl", "br")


def to_units(mm, units):
    if units == "in":
        return round(round(mm / MM_PER_IN * 16.0) / 16.0, 4)
    if units == "cm":
        return round(mm / 10.0, 1)
    if units == "m":
        return round(mm / 1000.0, 3)
    return int(round(mm))


def _extents(rec):
    ext = rec.get("_extents_mm")
    if isinstance(ext, list) and len(ext) >= 3:
        return [float(v) for v in ext[:3]]
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    return [float(v) * 1000.0 for v in size[:3]]


LENGTH_SUFFIXES = (("_mm", 1.0), ("_cm", 10.0), ("_m", 1000.0))


def length_field_mm(rec, name):
    """A length-valued field of the record (name ending in _mm/_cm/_m, a number or a list of numbers: a curtain's
    drape_cm, a towel's flat_size_m, a cord's cord_length_m) in mm, or None. Booleans and other fields are not lengths."""
    if not isinstance(name, str) or name.startswith("_") or name not in rec:
        return None
    for suffix, scale in LENGTH_SUFFIXES:
        if name.endswith(suffix):
            value = rec[name]
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value) * scale
            if isinstance(value, list) and value and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                return [float(v) * scale for v in value]
            return None
    return None


def true_value(state, target, dimension, destination=None, at=None):
    """(true mm or [mm...], inside, span_mm, quantity keys) for a measurement request. The quantity keys name what each
    value is (an axis, a named span at a height, a field, a distance) so instrument errors can be fixed per quantity."""
    if target in state.get("connections", {}):
        conn = state["connections"][target]
        dim = dimension or "width"
        if dim in ("width", "clear_width"):
            mm = C.num(conn.get("_clear_width_mm"), C.num(conn.get("width_m"), 0.9) * 1000.0)
            dim = "width"
        elif dim == "height":
            mm = C.num(conn.get("_height_mm"), 2032.0)
        else:
            raise ActionError("A doorway has width and height", "argument")
        return mm, True, mm, [dim]
    require(target in state["entities"], "Unknown thing to measure", "argument")
    rec = state["entities"][target]
    meas = rec.get("_measure") or {}
    if destination:
        require(destination in state["entities"] or destination in state["rooms"], "Unknown second end point", "argument")
        key_a, key_b = "to:" + destination, "to:" + target
        other = state["entities"].get(destination, {})
        if key_a in meas:
            mm = C.num(meas[key_a])
        elif key_b in (other.get("_measure") or {}):
            mm = C.num(other["_measure"][key_b])
        else:
            a = geometry.anchor(state, target)[1] or [(0, 0)]
            b = geometry.anchor(state, destination)[1] or [(0, 0)]
            mm = 1000.0 * C.CELL * math.hypot(a[0][0] - b[0][0], a[0][1] - b[0][1])
        return mm, True, mm, ["to:" + "|".join(sorted((target, destination)))]
    dim = dimension or "dims"
    if dim in meas:
        value = meas[dim]
        if isinstance(value, dict):
            if at in (None, "mid"):
                level = "mid" if "mid" in value else min(value, key=lambda k: (C.num(value[k]), k))
            elif at == "min":
                level = min(value, key=lambda k: (C.num(value[k]), k))
            else:
                require(at in value, "No reading at {} for {}".format(at, dim), "argument")
                level = at
            mm = C.num(value[level])
            key = "{}@{}".format(dim, level)
        else:
            mm = C.num(value)
            key = dim
        inside = rec.get("measure_inside", rec.get("category") in ("space", "opening", "alcove", "gap") or not rec.get("portable"))
        return mm, bool(inside), mm, [key]
    ext = _extents(rec)
    names = {"width": 0, "depth": 1, "height": 2}
    if dim == "dims":
        return ext, False, max(ext), ["width", "depth", "height"]
    if dim == "length":
        axis = "width" if ext[0] >= ext[1] else "depth"
        return ext[names[axis]], False, ext[names[axis]], [axis]
    if dim not in names:
        field = length_field_mm(rec, dim)
        if field is not None:
            # a length the object's type exposes as a field (curtain drape, towel flat size, cord length): taped as is
            if isinstance(field, list):
                return field, False, max(field), ["{}.{}".format(dim, i) for i in range(len(field))]
            return field, False, field, [dim]
    require(dim in names, "Dimensions: width, depth, height, length, dims{}".format(
        ", " + ", ".join(sorted(k for k in meas if not k.startswith("to:"))) if meas else ""), "argument")
    return ext[names[dim]], False, ext[names[dim]], [dim]


def fixed_rng(ctx, *parts):
    """One fixed random stream per (episode seed, parts): an instrument's error for a quantity does not change from one
    reading to the next (string seeds hash with sha512: stable across processes and PYTHONHASHSEED)."""
    seed = getattr(ctx.env, "seed", 0) if ctx is not None else 0
    return random.Random("nav-instrument|{}|{}".format(seed, "|".join(str(p) for p in parts)))


def instrument(ctx, tool_id):
    w = ctx.w
    candidates = C.tool_candidates(ctx, tool_id)        # never an unfamiliar held thing (phase-1 close-out)
    for eid in candidates:
        if not eid or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        if rec.get("tool_kind") in ("tape_measure", "laser_meter") or "measure" in (rec.get("affordances") or []):
            require(not rec.get("broken"), "That measuring tool is damaged", "resource")
            if rec.get("battery_powered"):
                require(C.num(rec.get("battery_level"), 1.0) > 0.02, "The laser meter's battery is flat", "resource")
            return eid, rec
    if tool_id:
        raise ActionError("Hold the measuring tool first", "precondition")
    return None, None


CASE_LENGTH_SOURCES = ("case_length_mm", "label_text")     # fields that tell a tape's case length when known at read tier


def case_length_known(ctx, tool_id, trec):
    """Oracle mode: always. Realistic perception: the robot has read the tape's case length (its label text, or the
    case_length_mm field should a type expose it, known at read level or better)."""
    if not C.realistic(ctx):
        return True
    return any(field in trec and C.knows_field(ctx.env, ctx.w.s, tool_id, field, level="read") for field in CASE_LENGTH_SOURCES)


def reading(ctx, true_mm, inside, span_mm, method, trec, rng=None):
    """One instrument reading. `rng` is the quantity's fixed stream (fixed_rng): the same quantity read with the same
    instrument gives the same error every time."""
    rng = rng or ctx.rng
    notes = []
    if method == "estimate":
        # eyeballing/odometry is good to a few percent at best: never within a tape's tolerance (at least 2 % of half a
        # metre = 10 mm, still >= 5 mm off after the 10 mm rounding), and a zero-centred spread beyond that
        sign = -1.0 if rng.random() < 0.5 else 1.0
        frac = min(0.10, 0.02 + abs(rng.gauss(0.0, 0.03)))
        err = sign * frac * max(500.0, true_mm)
        return round((true_mm + err) / 10.0) * 10.0, 10.0, ["estimated without a tool (odometry/vision), a few percent off"]
    if method == "laser":
        if inside:
            acc = C.num(trec.get("accuracy_mm"), 2.0)
            return true_mm + rng.uniform(-acc, acc), 1.0, notes
        notes.append("no target surface at the far edge; aimed past the edge")
        return true_mm + rng.uniform(-12.0, 12.0), 1.0, notes
    length_mm = C.num(trec.get("length_m"), 7.5) * 1000.0
    require(span_mm <= length_mm + 1e-6, "Longer than the tape ({:.1f} m)".format(length_mm / 1000.0), "capability")
    tol = 0.3 + 0.2 * span_mm / 1000.0
    err = rng.uniform(-tol, tol) + rng.uniform(-0.5, 0.5) * C.num(trec.get("hook_play_mm"), 1.0)
    if inside and not ctx.args.get("add_case"):
        err -= C.num(trec.get("case_length_mm"), 76.0)
        notes.append("inside reading taken at the case without adding its length")
    if span_mm > 2400.0 and not ctx.args.get("support") and not inside:
        sag = 2.5 * ((span_mm - 2400.0) / 1000.0) ** 2 + 1.0
        err += sag
        notes.append("tape sagged over an unsupported {:.1f} m span".format(span_mm / 1000.0))
    return true_mm + err, 1.0, notes


@action("measure", params={"target": "str", "destination": "?str", "dimension": "?str", "tool": "?entity",
                           "method": "?str:tape|laser|estimate", "add_case": {"type": "bool", "default": False},
                           "support": {"type": "bool", "default": False}, "at": "?str:min|floor|mid|top",
                           "units": "?str:mm|cm|m|in"},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.0, category="perception",
        doc="Measure with a held tape or laser meter (or estimate without one): an object's width/depth/height/length or all "
            "'dims' (true extents including legs and handles), a doorway connection's clear width/height, a space's named "
            "dimension (at=min scans for the narrowest point), a length the object has as a field (dimension=drape_cm, "
            "flat_size_m, cord_length_m...), or the distance target->destination. add_case adds the tape's case length for "
            "inside readings; support keeps long spans from sagging. An instrument's error for a quantity is fixed: repeating "
            "a reading gives the same number. Result in `units` (default mm) also appears as observation['measurement'].")
def measure(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    if target in state["entities"]:
        rec = ctx.obj(target)
        room, cells = geometry.anchor(state, target)
        require(room == state["robot"]["room"], "Go to it first", "precondition")
        if cells and C.num(max((rec.get("size_m") or [0.1])[:3])) < 3.0:
            require(min(geometry.chebyshev(C.cell(state["robot"].get("pos") or [0, 0]), c) for c in cells) <= 2, "Get closer to measure it", "precondition")
    elif target in state.get("connections", {}):
        require(state["robot"]["room"] in state["connections"][target]["rooms"], "Go to that doorway first", "precondition")
    else:
        raise ActionError("Unknown thing to measure", "argument")
    # reading the tape needs the cameras (the body's imaging: switched on, no privacy mode, no room policy, working)
    ctx.require_imaging()
    require(C.light_level(state, state["robot"]["room"]) >= 0.2 or C.robot_has_light(state), "Too dark to read a measurement", "perception")
    tool_id, trec = instrument(ctx, ctx.args.get("tool"))
    method = ctx.args.get("method")
    if method in (None, "tape", "laser"):
        if trec is None:
            require(method is None, "Hold a tape measure or laser meter", "capability")
            method = "estimate"
        else:
            kind = "laser" if trec.get("tool_kind") == "laser_meter" else "tape"
            require(method in (None, kind), "That tool is a {}".format(kind), "argument")
            method = kind
    if method == "tape" and ctx.args.get("add_case"):
        # adding the case length is arithmetic the robot does with the number printed on the case: realistic perception
        # needs that number read first (the tape's case_length_mm is hidden; its label states it), else add_case would
        # apply a value the robot never learned (review P2-c 4.14). Asked whatever the reading, so it tells nothing.
        require(case_length_known(ctx, tool_id, trec), "Read the tape's case length first (it is printed on the case)",
                "precondition")
    true, inside, span, quantities = true_value(state, target, ctx.args.get("dimension"), ctx.args.get("destination"), ctx.args.get("at"))
    units = ctx.args.get("units") or "mm"
    values, notes = [], []
    for t, quantity in zip((true if isinstance(true, list) else [true]), quantities):
        # a distance is one quantity whichever end it is taped from
        rng = fixed_rng(ctx, "measure", "-" if quantity.startswith("to:") else target, quantity, method, tool_id or "-")
        mm, _, n = reading(ctx, float(t), inside, span if not isinstance(true, list) else float(t), method, trec or {}, rng=rng)
        values.append(round(mm, 2))
        notes.extend(n)
    shown = [to_units(v, units) for v in values]
    result = {"target": target, "destination": ctx.args.get("destination"), "dimension": ctx.args.get("dimension") or ("dims" if isinstance(true, list) else "distance"),
              "value": shown if isinstance(true, list) else shown[0], "units": units, "method": method,
              "at": ctx.args.get("at") or "mid", "notes": sorted(set(notes))}
    seconds = {"estimate": 3.0, "laser": 8.0, "tape": 25.0}[method] * (len(values) if isinstance(true, list) else 1)
    if ctx.args.get("at") == "min":
        seconds *= 3.0
    ctx.data["seconds"] = seconds
    facts = w.sec("facts")
    facts["nav_last_measure"] = copy.deepcopy(result)
    facts.setdefault("inspected", {})[target] = C.now(state)
    if method in ("tape", "laser") and target in state["entities"] and not ctx.args.get("destination"):
        # realistic perception: a tool reading of an object whose true size IS its size_m (no hidden extents or named
        # spans) is a tape/laser measurement of size_m; a taped length field (drape_cm, flat_size_m, cord_length_m...) is
        # a measurement of that field; readings of hidden extents/spans reveal only the reported value
        trec_t = state["entities"][target]
        dim = ctx.args.get("dimension") or "dims"
        if dim in (trec_t.get("_measure") or {}):
            pass
        elif dim in ("dims", "width", "depth", "height", "length"):
            if not trec_t.get("_extents_mm"):
                ctx.grant(target, "measure", ["size_m"])
        elif length_field_mm(trec_t, dim) is not None:
            ctx.grant(target, "measure", [dim])
    nav = C.nav(w)
    nav.setdefault("measurements", []).append(dict(result, time_s=C.now(state)))
    del nav["measurements"][:-30]
    nav.setdefault("measured", {})[target] = C.now(state)
    return result


def compute_level(rec):
    """(tilt [x, y] deg, wobble mm) of an entity on leveling feet."""
    lev = rec.get("leveling") or {}
    floor = lev.get("floor_mm") or {}
    span = lev.get("span_mm") or [600.0, 600.0]
    settings = rec.get("settings") or {}
    eff = {f: C.num(floor.get(f)) + C.num(settings.get("foot_{}_mm".format(f))) for f in FEET}
    dx = ((eff["fr"] + eff["br"]) - (eff["fl"] + eff["bl"])) / 2.0
    dy = ((eff["bl"] + eff["br"]) - (eff["fl"] + eff["fr"])) / 2.0
    tilt = [round(math.degrees(math.atan2(dx, C.num(span[0], 600.0))), 3), round(math.degrees(math.atan2(dy, C.num(span[1], 600.0))), 3)]
    wobble = round(abs((eff["fl"] + eff["br"]) - (eff["fr"] + eff["bl"])), 2)
    return tilt, wobble


def apply_level(w, eid):
    tilt, wobble = compute_level(w.ent(eid))
    m = w.mut(eid)
    m["_tilt_deg"] = tilt
    m["_wobble_mm"] = wobble


@hook("setting_changed")
def _feet_changed(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid, {})
    if rec.get("leveling") and str(name).startswith("foot_"):
        apply_level(w, eid)


def true_tilt(rec):
    if rec.get("leveling"):
        return compute_level(rec)
    if isinstance(rec.get("_tilt_deg"), list):
        return [C.num(v) for v in rec["_tilt_deg"][:2]], C.num(rec.get("_wobble_mm"))
    if "tilt_deg" in rec:
        return [C.num(rec.get("tilt_deg")), 0.0], 0.0
    return [0.0, 0.0], 0.0


@action("check_level", params={"target": "entity", "tool": "?entity"}, duration=12.0, energy=0.0, category="perception",
        doc="Read the tilt of a surface, appliance or hung frame along both axes with a held spirit/digital level (or eyeball "
            "it without one); also rocks it to feel for wobble. Result appears as observation['measurement'].")
def check_level(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    ctx.require_imaging()       # the bubble (or the eyeballed tilt) is read with the cameras
    tool = None
    for eid in C.tool_candidates(ctx, ctx.args.get("tool")):
        if eid and ctx.holding(eid) and (w.ent(eid).get("tool_kind") == "spirit_level" or "check_level" in (w.ent(eid).get("affordances") or [])):
            tool = eid
            break
    if ctx.args.get("tool"):
        require(tool, "Hold the level first", "precondition")
    tilt, wobble = true_tilt(rec)
    # fixed per (target, axis, instrument): re-reading does not re-dither, so many readings average to nothing finer
    # than the level's resolution (or the eyeball's +/-0.8 deg)
    rngs = [fixed_rng(ctx, "level", target, axis, tool or "eyeball") for axis in ("x", "y")]
    if tool:
        res = C.num(w.ent(tool).get("read_resolution_deg"), 0.1)
        shown = [round(round((t + r.uniform(-res / 3, res / 3)) / res) * res, 3) for t, r in zip(tilt, rngs)]
        method = "level"
    else:
        shown = [round(t + r.uniform(-0.8, 0.8)) for t, r in zip(tilt, rngs)]
        method = "eyeball"
    result = {"target": target, "tilt_x_deg": shown[0], "tilt_y_deg": shown[1], "method": method,
              "wobble": "rocks" if wobble > 1.0 else "solid", "lock_nuts": (rec.get("settings") or {}).get("lock_nuts")}
    if "settings" in rec:
        # rocking it by hand and looking at the feet: the robot now knows the feet/lock-nut settings (tilt itself is a
        # hidden quantity reported only at the level's resolution above)
        ctx.grant(target, "inspect", ["settings"])
    w.sec("facts")["nav_last_measure"] = copy.deepcopy(result)
    nav = C.nav(w)
    nav.setdefault("level_checks", {})[target] = {"time_s": C.now(state), "method": method}
    return result


@action("mark", params={"target": "entity", "height_m": {"type": "number", "min": 0.0, "max": 4.0}, "x_m": {"type": "number", "default": 0.0, "min": 0.0, "max": 10.0},
                        "tool": "?entity"}, duration=8.0, energy=0.0, category="physical",
        doc="Make a small pencil mark on a wall, door or surface at a height (and offset x_m from its left edge). Measured "
            "marks (after measuring that surface) land within about 1 mm; eyeballed marks within about 15 mm.")
def mark(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    ctx.obj(target, touch=True)
    tool = None
    for eid in C.tool_candidates(ctx, ctx.args.get("tool")):
        if eid and ctx.holding(eid) and ("mark" in (w.ent(eid).get("affordances") or []) or w.ent(eid).get("tool_kind") in ("pencil", "marker")):
            tool = eid
            break
    require(tool, "Hold a pencil", "capability")
    height = ctx.args["height_m"]
    require(height <= geometry.robot_reach_m(state) + 1e-9, "Too high to mark from here; use a step stool", "capability")
    measured = C.num((C.nav_read(state).get("measured") or {}).get(target), -1e9)
    precise = C.now(state) - measured <= 900.0
    err = ctx.rng.uniform(-1.0, 1.0) if precise else ctx.rng.uniform(-15.0, 15.0)
    m = w.mut(target)
    marks = list(m.get("marks") or [])
    marks.append({"height_m": round(height + err / 1000.0, 4), "x_m": round(ctx.args["x_m"] + (ctx.rng.uniform(-1.0, 1.0) if precise else ctx.rng.uniform(-15.0, 15.0)) / 1000.0, 4),
                  "time_s": C.now(state), "by": "robot", "tool": tool})
    m["marks"] = marks[-20:]
    # realistic perception: the raw mark positions (to 0.1 mm) are this entity's hidden truth; inspecting it shows each
    # mark located to about 5 mm (world inspector). Per-entity tiers, since "marks" means other things on other types.
    own = dict(m.get("_perception") or {})
    if "marks" not in (own.get("hidden") or []):
        own["hidden"] = list(own.get("hidden") or []) + ["marks"]
        m["_perception"] = own
    return "Marked {} at {:.3f} m".format(target, height)


@C.per_substep_index("leveled")
@C.index_reads("leveling")
def _is_leveled(rec):
    return bool(rec.get("leveling"))


def _walks(rec):
    """A running appliance on leveling feet that is out of level, rocks or has loose lock nuts walks."""
    if (rec.get("appliance") or {}).get("state") != "running":
        return False
    tilt, wobble = compute_level(rec)
    loose = (rec.get("settings") or {}).get("lock_nuts", "locked") != "locked"
    return not (max(abs(tilt[0]), abs(tilt[1])) <= 1.0 and wobble <= 1.0 and not loose)


def leveling_idle(w):
    """Exact idle probe of navigation_robot.leveling (kernel v3.1): no running appliance on leveling feet walks."""
    entities = w.s["entities"]
    return not any(_walks(entities[eid]) for eid in C.ids_with(w, "leveled", _is_leveled))


@system("navigation_robot.leveling", order=118, max_dt=30.0, idle=leveling_idle)
def leveling_system(w, dt, tick):
    state = w.s
    for eid in C.ids_with(w, "leveled", _is_leveled):
        rec = state["entities"][eid]
        if not _walks(rec):
            continue
        _, wobble = compute_level(rec)
        m = w.mut(eid)
        before_mm = C.num(m.get("_walked_mm"))
        m["_walked_mm"] = round(before_mm + 2.0 * dt / 30.0 * (1.0 + wobble / 3.0), 2)
        if before_mm < 30.0 <= m["_walked_mm"]:
            w.stat("damage")
            C.damage_entity(w, eid, "walked", 0.3)
            C.incident(w, "appliance_walked", target=eid, walked_mm=m["_walked_mm"])

"""Valve verbs: shut_off and open_valve (main, fixture stops, toilet supply, spigots, heater inlet, toilet float).

shut_off target may be:
  * a valve entity (shutoff_valve / main_shutoff_valve / heater inlet): turned clockwise to its stop
    (gate 6-8 turns ~1.2 s each, multi-turn stop 2.5 turns, ball/quarter-turn 0.25 turn; the lever ends
    perpendicular to the pipe). turns=<n> stops part-way (position drops by n/turns_to_close), direction=ccw turns
    the wrong way (opens). A seized stem refuses by hand; force=firm snaps it (damage, spraying stem).
  * a fixture (sink, tub, dispenser, ice maker, appliance fed by stops): closes every stop valve that feeds it
    (they must be visible - open the sink cabinet - and within reach).
  * a toilet: closes its supply stop; method=float props the fill-valve float up (tank lid must be off) to stop
    the fill when the stop is seized.
open_valve reverses (fraction 0..1, method=float releases a propped float).
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from . import common as C
from . import fixtures as F
from . import seen as V
from . import supply as S

FORCES = ("gentle", "normal", "firm")


def _valve_seconds(rec, turns):
    per = 1.2 if rec.get("valve_kind") in ("gate", "multi_turn_stop") else 1.0
    return 1.5 + per * max(0.25, turns)


def _refresh_fed(w, valve_id):
    rec = w.ent(valve_id)
    targets = list(rec.get("feeds") or [])
    if rec.get("main") or rec.get("feeds_hot"):
        targets = F.faucet_ids(w)
    for fid in sorted(set(targets)):
        if fid in w.s["entities"] and F.faucet(w.ent(fid)):
            F.refresh(w, fid)


def turn_valve(ctx, eid, closing, turns=None, force="normal", direction=None):
    w = ctx.w
    rec = w.ent(eid)
    pos = S.valve_position(rec)
    full = S.close_valve_turns(rec)
    if direction is None:
        direction = "cw" if closing else "ccw"
    if direction == "ccw" and closing:
        closing = False
    if direction == "cw" and not closing:
        closing = True
    if closing and pos <= 0.001:
        return "It is already closed (handle at its stop)", 1.5
    if not closing and pos >= 0.999:
        return "It is already fully open: it will not turn further {}".format("counter-clockwise" if direction == "ccw" else "that way"), 1.5
    if not S.require_turnable(ctx, rec, eid, force):
        _refresh_fed(w, eid)
        return "The corroded stem snapped off and water is spraying from the valve", 3.0
    turns = full if turns is None else float(turns)
    delta = min(1.0, turns / full) if full > 0 else 1.0
    new = max(0.0, pos - delta) if closing else min(1.0, pos + delta)
    S.set_position(ctx, eid, new)
    _refresh_fed(w, eid)
    ctx.grant(eid, "touch", ["settings", "valve_open"])     # the hand on the handle knows where it left it
    if rec.get("main"):
        C.plog(w, "main_valve", target=eid, position=new)
    word = "closed" if new <= 0.001 else "fully open" if new >= 0.999 else "{:.0f}% open".format(new * 100)
    if V.realistic(ctx):
        # the hand feels the stem reach its stop and counts the turns roughly (to half a turn); how far open a
        # part-turned valve is cannot be seen or felt (the exact turns would give it away, and turns_to_close)
        word = "it reached its stop (closed)" if new <= 0.001 else "it reached its stop (fully open)" if new >= 0.999 else             "it still turns (not at a stop)"
        counted = V.turns_value(abs(pos - new) * full)
        return "Turned {} {} ({}): {}".format(eid, "clockwise" if direction == "cw" else "counter-clockwise",
                                              V.turns_word(counted), word), _valve_seconds(rec, counted)
    return "Turned {} {} ({:.2f} turns): {}".format(eid, "clockwise" if direction == "cw" else "counter-clockwise",
                                                    abs(pos - new) * full, word), _valve_seconds(rec, abs(pos - new) * full)


def _fixture_valves(w, fixture):
    return [v for v in S.valves(w) if fixture in (w.ent(v).get("feeds") or [])]


def _toilet_supply(ctx, eid, rec, closing, force, method):
    w = ctx.w
    if method == "float":
        require(rec.get("cover_open"), "Lift the tank lid off first (open_cover) to reach the float", "precondition")
        require(ctx.free_hand() is not None or not closing, "You need a free hand to hold the float", "capability")
        m = w.mut(eid)
        m["float_propped"] = bool(closing)
        return ("Propped the float up: the fill valve stops" if closing else "Released the float: the tank refills"), 6.0
    current = (rec.get("settings") or {}).get("supply_valve", "open")
    if closing and current == "closed":
        return "The toilet supply stop is already closed", 1.5
    if not closing and current == "open":
        return "The toilet supply stop is already open", 1.5
    if rec.get("_supply_seized"):
        if force != "firm":
            raise ActionError("The supply stop behind the toilet will not turn: it is seized. Do not force it", "precondition")
        C.damage(w, eid, "supply stop stem snapped", stat=True, kind="stem_snapped")
        m = w.mut(eid)
        m["_supply_seized"] = False
        m["_leak"] = C.robot_leak(w, {"component": "supply_stop_stem", "rate_lpm": 6.0, "when": "pressurized", "line": "cold",
                                      "fixture": eid, "to": {"room": C.room_of(w, eid), "cell": C.cell_of(w, eid)}, "started_s": C.now(w),
                                      "severity": "spray"})
        return "The seized stop's stem snapped and water sprays from behind the toilet", 3.0
    from ..core import apply_setting
    apply_setting(ctx, eid, "supply_valve", "closed" if closing else "open")
    return ("Closed the toilet's supply stop" if closing else "Opened the toilet's supply stop"), 4.0


def _operate(ctx, closing):
    w = ctx.w
    eid = ctx.args["target"]
    force = ctx.args.get("force") or "normal"
    rec = ctx.obj(eid, touch=True)
    method = ctx.args.get("method")
    if rec.get("toilet"):
        msg, seconds = _toilet_supply(ctx, eid, rec, closing, force, method)
        ctx.data["seconds"] = seconds
        return msg
    if rec.get("valve_kind"):
        if not closing and ctx.args.get("fraction") is not None:
            pos = S.valve_position(rec)
            want = float(ctx.args["fraction"])
            if want <= pos:
                ctx.data["seconds"] = 1.5
                return "It is already at least that far open"
            turns = (want - pos) * S.close_valve_turns(rec)
            msg, seconds = turn_valve(ctx, eid, False, turns=turns, force=force)
        else:
            msg, seconds = turn_valve(ctx, eid, closing, turns=ctx.args.get("turns"), force=force, direction=ctx.args.get("direction"))
        ctx.data["seconds"] = seconds
        return msg
    if F.fkind(rec) == "spigot" and not _fixture_valves(w, eid):
        from ..core import apply_setting
        apply_setting(ctx, eid, "valve", 0.0 if closing else float(ctx.args.get("fraction") or 1.0))
        F.refresh(w, eid)
        ctx.data["seconds"] = 3.0
        return "{} the spigot".format("Closed" if closing else "Opened")
    stops = _fixture_valves(w, eid)
    require(stops, "No shutoff valve for that here; find the valve that feeds it", "precondition")
    out, total = [], 0.0
    for vid in stops:
        vrec = w.ent(vid)
        require(ctx.visible(vid), "Its stop valves are not visible: open the cabinet or panel first", "perception")
        ctx.obj(vid, touch=True)
        msg, seconds = turn_valve(ctx, vid, closing, force=force)
        # realistic perception: which stop is the hot one is not known from turning both (inspect traces the lines)
        out.append(msg if V.realistic(ctx) else "{} {}".format(vrec.get("line", ""), msg).strip())
        total += seconds
    F.refresh(w, eid)
    ctx.data["seconds"] = total
    return "; ".join(out)


@action("shut_off", params={"target": "entity", "force": {"type": "str", "default": "normal", "choices": list(FORCES)},
                            "turns": {"type": "number", "optional": True, "min": 0.05, "max": 20.0},
                            "direction": "?str:cw|ccw", "method": "?str:valve|float"},
        duration=lambda ctx: ctx.data.get("seconds", 4.0), energy=0.006, noise=0.02, category="physical",
        doc="Close a water valve: a valve entity (main, fixture stop, heater inlet), every stop feeding a fixture "
            "(open the cabinet so they are visible), or a toilet's supply stop (method=float props the float instead). "
            "Gate valves need several clockwise turns to their stop (turns=n stops part-way; direction=ccw is the "
            "wrong way); ball valves a quarter turn. A seized stem will not move: force=firm snaps it.")
def shut_off(ctx):
    return _operate(ctx, True)


@action("open_valve", params={"target": "entity", "fraction": {"type": "number", "optional": True, "min": 0.05, "max": 1.0},
                              "force": {"type": "str", "default": "normal", "choices": list(FORCES)},
                              "method": "?str:valve|float"},
        duration=lambda ctx: ctx.data.get("seconds", 4.0), energy=0.006, noise=0.02, category="physical",
        doc="Open a water valve, a fixture's stops or a toilet supply stop (fraction 0..1, default fully); "
            "method=float releases a propped toilet float.")
def open_valve(ctx):
    return _operate(ctx, False)

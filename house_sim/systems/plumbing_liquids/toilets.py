"""Toilets: tank, bowl, flush, clogs, overflow, plunging (fidelity P-approx).

Toilet record (type toilet): tank_l 6.0 (1.6 gpf; 4.8 for 1.28 gpf), tank_level_l, bowl_l (water standing in
the bowl; normal_bowl_l 1.9), bowl_rim_l 6.5 (bowl water at which it runs over the rim), settings.supply_valve
open|closed (the angle stop behind it; hidden _supply_seized), fill_lpm 9 (fill valve), refill tube sends 15 % of
the fill to the bowl, float_propped (lifted float stops the fill), cover_open (tank lid, core open_cover),
open (seat lid), `_tank` (tools_vehicles' running-toilet contract: chain_slack_mm, float_mm, flapper_seal,
fill_valve_ok - a stuck-open fill valve keeps running ~4 L/min through the overflow tube into the bowl),
`_clog` {kind paper|waste|wipes|object|sewer|none, amount 0..1, object, chemical, depth_m}, bowl_waste 0..1.

Flush: the tank (>= 3 L) dumps into the bowl in ~4 s. A clear trap siphons the bowl and the waste away; a clog
lets only drain_lpm = 60 (1 - amount)^2 L/min through, so the bowl rises by the flush volume minus what drains,
and whatever exceeds bowl_rim_l runs onto the floor as contaminated toilet water (spill, flood, biohazard).
Flushing again with a clog is the classic mistake. With the supply closed the tank does not refill.

Plunging (tool with plunger_kind flange|cup): needs water over the cup (bowl_l >= 1.2 L); per stroke a flange
plunger removes 12 % of a paper/waste clog (cup plunger 2 %: it cannot seal in a toilet outlet); vigorous strokes
splash when the bowl is high; an object clog is pushed deeper (no longer retrievable by hand); caustic drain
cleaner in the bowl splashes (burns, safety violation); a main sewer backup does not clear - the water comes up
the tub drain instead. Retrieving a visible object by hand needs gloves (biohazard grip) and clears that clog.
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from . import common as C
from . import liquids as L
from . import seen as V
from . import supply as S

STROKE_EFF = {"flange": 0.15, "cup": 0.02, "accordion": 0.17}


def is_toilet(rec):
    return bool(rec.get("toilet")) and C.is_mine(rec)


def clog(rec):
    value = rec.get("_clog")
    return value if isinstance(value, dict) else {}


def clog_amount(rec):
    c = clog(rec)
    if c.get("kind") in (None, "none"):
        return 0.0
    return C.clamp(C.num(c.get("amount")), 0.0, 1.0)


def drain_lpm(w, rec):
    amount = clog_amount(rec)
    if clog(rec).get("kind") == "sewer" or C.pget(w, "sewer_backup"):
        amount = max(amount, 0.97)
    return 60.0 * (1.0 - amount) ** 2


def supply_on(w, eid, cache=None):
    rec = w.ent(eid)
    if rec.get("float_propped"):
        return False
    if (rec.get("settings") or {}).get("supply_valve", "open") != "open":
        return False
    return S.line_factor(w, eid, "cold", cache) > 0


def bowl_overflow(w, eid, liters_):
    rec = w.ent(eid)
    if liters_ <= 1e-4:
        return
    liquid = "sewage" if clog(rec).get("kind") == "sewer" else "toilet_water"
    from .fixtures import overflow_started
    started = overflow_started(w, eid)
    C.plog(w, "toilet_overflow", see={"source": eid, "size_m": 0.4} if started else None, toilet=eid,
           liters=C.rnd(liters_, 3))
    if started:
        C.notice(w, "splash", source=eid)
    C.add_floor_water(w, C.room_of(w, eid), C.cell_of(w, eid), liquid, liters_, source=eid)
    m = w.mut(eid)
    m["overflowed_l"] = C.rnd(C.num(m.get("overflowed_l")) + liters_, 3)
    C.contaminate(w, eid, "biohazard", 0.4)


def add_to_bowl(w, eid, liters_, waste=0.0):
    rec = w.ent(eid)
    bowl = C.num(rec.get("bowl_l"), 1.9) + liters_
    rim = C.num(rec.get("bowl_rim_l"), 6.5)
    m = w.mut(eid)
    if waste:
        m["bowl_waste"] = C.rnd(C.clamp(C.num(m.get("bowl_waste")) + waste, 0.0, 1.0), 3)
    if bowl > rim:
        m["bowl_l"] = rim
        bowl_overflow(w, eid, bowl - rim)
    else:
        m["bowl_l"] = C.rnd(bowl, 3)


def do_flush(ctx, eid, fraction=1.0):
    w = ctx.w
    rec = w.ent(eid)
    tank = C.num(rec.get("tank_level_l"), C.num(rec.get("tank_l"), 6.0))
    if tank < 1.0:
        C.plog(w, "flush_empty_tank", toilet=eid)
        return "The handle moves but nothing happens: the tank is empty (supply off?)"
    release = tank * C.clamp(fraction, 0.2, 1.0)
    m = w.mut(eid)
    m["tank_level_l"] = C.rnd(tank - release, 3)
    m["flushes"] = int(C.num(m.get("flushes"))) + 1
    amount = clog_amount(rec)
    sewer =clog(rec).get("kind") == "sewer" or C.pget(w, "sewer_backup")
    if amount < 0.3 and not sewer and release >= 3.0:
        m["bowl_waste"] = 0.0
        if clog(rec).get("kind") in ("paper", "waste") and amount > 0:
            m["_clog"]["amount"] = 0.0
            m["_clog"]["kind"] = "none"
        m["bowl_l"] = C.num(rec.get("normal_bowl_l"), 1.9) * 0.6
        m["last_flush"] = {"t": C.now(w), "ok": True}
        C.plog(w, "flush", toilet=eid, ok=True)
        return "Flushed: the bowl emptied with a clean siphon and is refilling"
    drained = min(release, drain_lpm(w, rec) * 10.0 / 60.0)
    rise = release - drained
    add_to_bowl(w, eid, rise)
    m = w.mut(eid)
    m["last_flush"] = {"t": C.now(w), "ok": False}
    C.plog(w, "flush", toilet=eid, ok=False, rise_l=C.rnd(rise, 2))
    if sewer:
        _sewer_backflow(w, eid, release * 0.4)
    if C.num(w.ent(eid).get("overflowed_l")) > C.num(rec.get("overflowed_l")):
        return "Flushed: the water rose and ran over the rim onto the floor"
    if V.realistic(ctx):
        return "Flushed: the water rose in the bowl ({}) and drains very slowly".format(bowl_state(w.ent(eid)))
    return "Flushed: the water rose in the bowl ({:.1f} L) and drains very slowly".format(C.num(w.ent(eid).get("bowl_l")))


def _sewer_backflow(w, toilet, liters_):
    """With the main sewer line blocked, flushed water comes up the lowest drain (tub/shower/floor drain)."""
    rid = C.room_of(w, toilet)
    lowest = None
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if C.kind(rec) in ("tub", "shower") or rec.get("floor_drain"):
            if lowest is None or C.room_of(w, eid) == rid:
                lowest = eid
    if lowest is None:
        return
    rec = w.ent(lowest)
    if L.is_basin(rec):
        over = L.add(w, lowest, L.make_parcel("sewage", liters_, source=toilet))
        if over:
            from . import fixtures
            fixtures.overflow(w, lowest, over)
        C.contaminate(w, lowest, "biohazard", 0.6)
    else:
        C.add_floor_water(w, C.room_of(w, lowest), C.cell_of(w, lowest), "sewage", liters_, source=lowest)
    C.plog(w, "sewer_backflow", into=lowest, liters=C.rnd(liters_, 2))


@action("flush", params={"target": "entity", "hold_s": {"type": "number", "default": 1.0, "min": 0.1, "max": 10.0}},
        duration=lambda ctx: 4.0 + ctx.args.get("hold_s", 1.0), energy=0.003, noise=0.25, category="physical",
        timing="commanded",
        doc="Flush a toilet (hold the handle ~1 s for a full flush). With a clogged trap the bowl rises instead and can "
            "overflow - do not flush again. Also flushes grit out of a garden hose (target=hose, nozzle off, water on).")
def flush(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if rec.get("hose"):
        from . import outdoor
        return outdoor.flush_hose(ctx, eid)
    require(rec.get("toilet"), "That is not something you flush", "argument")
    hold = ctx.args["hold_s"]
    need = C.num(rec.get("flush_hold_s"), 0.8)
    if rec.get("open") or not rec.get("openable"):
        ctx.grant(eid, "inspect", ["bowl_waste"])      # watched the bowl with the lid up (its level: the inspector's words)
    return do_flush(ctx, eid, 1.0 if hold >= need else hold / need)


@hook("mechanisms.flushed")
def mechanisms_flushed(ctx, eid, fraction):
    """The mechanisms domain's flush handles (move_lever / push_handle) use the same toilet model."""
    rec = ctx.w.s["entities"].get(eid) or {}
    if is_toilet(rec):
        do_flush(ctx, eid, fraction)


# ----------------------------------------------------------------------------------
# plunge

def _plunger(ctx):
    w = ctx.w
    tool = ctx.args.get("tool")
    candidates = [tool] if tool else w.held_items()
    for eid in candidates:
        if eid and ctx.holding(eid) and w.ent(eid).get("plunger_kind"):
            return eid
    raise ActionError("Hold a plunger", "capability")


@action("plunge", params={"target": "entity", "tool": "?entity", "strokes": {"type": "int", "default": 15, "min": 1, "max": 60},
                          "force": {"type": "str", "default": "normal", "choices": ["gentle", "normal", "vigorous"]}},
        duration=lambda ctx: 5.0 + 1.5 * ctx.args.get("strokes", 15), energy=0.03, noise=0.2, category="physical",
        doc="Plunge a clogged toilet, sink or tub drain with a held plunger (flange plunger for toilets; cup plunger for "
            "flat sink/tub drains). The cup must be under water and sealed. Never plunge when caustic drain cleaner is in "
            "the water, and stop when several fixtures back up (main sewer line): plunging cannot clear that.")
def plunge(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    tool = _plunger(ctx)
    trec = w.ent(tool)
    kind = trec.get("plunger_kind", "cup")
    strokes = ctx.args["strokes"]
    force = ctx.args["force"]
    notes = []
    if rec.get("toilet"):
        c = dict(clog(rec))
        require(rec.get("open") or not rec.get("openable"), "Lift the toilet lid first", "precondition")
        require(C.num(rec.get("bowl_l"), 1.9) >= 1.2, "The plunger cup is not covered: there is too little water in the bowl to seal", "precondition")
        if c.get("chemical"):
            w.stat("burns")
            w.stat("safety_violations")
            C.plog(w, "chemical_splash", target=eid)
            notes.append("caustic drain cleaner splashed out of the bowl")
        # pushing the cup under water displaces ~1 L (flange) / 0.6 L (cup): a brim-full bowl runs over first
        displaced = 1.0 if kind == "flange" else 0.6
        bowl_now = C.num(rec.get("bowl_l"), 1.9)
        rim = C.num(rec.get("bowl_rim_l"), 6.5)
        if bowl_now + displaced > rim:
            bowl_overflow(w, eid, bowl_now + displaced - rim)
            w.mut(eid)["bowl_l"] = C.rnd(rim - displaced, 3)
            notes.append("water ran over the rim as the plunger went in")
        eff = STROKE_EFF.get(kind, 0.02) * (1.25 if force == "vigorous" else 0.7 if force == "gentle" else 1.0)
        sewer = c.get("kind") == "sewer" or C.pget(w, "sewer_backup")
        m = w.mut(eid)
        if sewer:
            _sewer_backflow(w, eid, 1.0)
            notes.append("water gurgled up another drain; the line itself is blocked")
        elif c.get("kind") in ("object", "wipes"):
            c["amount"] = C.rnd(C.num(c.get("amount")) * (1.0 - 0.02) ** strokes, 4)
            if c.get("object") and c.get("object_visible", True):
                obj = c["object"]
                c["object_visible"] = False
                c["depth_m"] = C.rnd(C.num(c.get("depth_m"), 0.1) + 0.4, 2)
                if obj in w.s["entities"]:
                    om = w.mut(obj)
                    om["relation"] = "part"
                    om["lodged_in_drain"] = True
                notes.append("the object was pushed deeper into the trap")
            m["_clog"] = c
        elif c.get("kind") not in (None, "none"):
            c["amount"] = C.rnd(C.num(c.get("amount")) * (1.0 - eff) ** strokes, 4)
            if c["amount"] < 0.05:
                c["amount"] = 0.0
                c["kind"] = "none"
            m["_clog"] = c
        if c.get("kind") in (None, "none") or C.num(c.get("amount")) < 0.3:
            bowl = C.num(m.get("bowl_l"), 1.9)
            normal = C.num(rec.get("normal_bowl_l"), 1.9)
            if bowl > normal:
                m["bowl_l"] = normal
                notes.append("the bowl drained down with a gulp")
        bowl = C.num(w.ent(eid).get("bowl_l"), 1.9)
        if force == "vigorous" and bowl > 0.7 * C.num(rec.get("bowl_rim_l"), 6.5):
            splash = 0.02 * strokes
            m = w.mut(eid)
            m["bowl_l"] = C.rnd(max(0.5, bowl - splash), 3)
            bowl_overflow(w, eid, splash)
            notes.append("vigorous strokes splashed water over the rim")
        C.add_soil(w, tool, "biohazard", 0.4)
        C.contaminate(w, tool, "biohazard", 0.6)
        ctx.grant(tool, "inspect")                              # working with it shows which plunger it is
        C.plog(w, "plunge", target=eid, kind=kind, strokes=strokes, amount=C.num(clog(w.ent(eid)).get("amount")))
        left = clog(w.ent(eid))
        state = "clear" if left.get("kind") in (None, "none") else "still clogged ({:.0%})".format(C.num(left.get("amount")))
        if V.realistic(ctx):
            # how much of the clog is left is not visible: only whether the bowl drains; which plunger it was is what
            # the robot saw of the tool (id)
            state = "clear" if left.get("kind") in (None, "none") else "the bowl still drains slowly" if C.num(left.get("amount")) < 0.6                 else "still clogged"
            return "Plunged {} {} strokes with {}: {}{}".format(eid, strokes, tool, state, "; " + "; ".join(notes) if notes else "")
        return "Plunged {} {} strokes with a {} plunger: {}{}".format(eid, strokes, kind, state, "; " + "; ".join(notes) if notes else "")
    if L.is_basin(rec):
        from . import fixtures as F
        c = dict(rec.get("_clog") or {})
        require(L.liters(rec) >= 0.5, "Put a few centimetres of water in the basin so the cup seals", "precondition")
        if c.get("chemical"):
            w.stat("burns")
            w.stat("safety_violations")
            notes.append("caustic drain cleaner splashed back")
        if rec.get("overflow_hole_lpm") and not rec.get("overflow_plugged"):
            eff = (0.04 if kind == "cup" else 0.02)
            notes.append("air escaped through the overflow hole (cover it with a wet rag)")
        else:
            eff = 0.10 if kind == "cup" else 0.05
        if c and c.get("kind") not in (None, "none"):
            factor = 0.6 if c.get("kind") == "grease" else 1.0
            c["amount"] = C.rnd(C.num(c.get("amount")) * (1.0 - eff * factor) ** strokes, 4)
            if c["amount"] < 0.05:
                c["kind"], c["amount"] = "none", 0.0
            w.mut(eid)["_clog"] = c
        if force == "vigorous":
            parcel = L.take(w, eid, min(L.liters(rec), 0.01 * strokes))
            from .transfer import spill_near
            spill_near(w, eid, parcel, "plunge_splash")
            notes.append("water splashed out")
        F.refresh_drain(w, eid)
        C.add_soil(w, tool, "dirt", 0.3)
        if V.realistic(ctx):
            # only how the water goes down shows (the plug is open) - never the clog amount
            speed = V.drain_word(w.ent(eid).get("drain_speed")) if w.ent(eid).get("plug", "open") != "closed" else "the plug is in"
            return "Plunged the drain of {}: {}{}".format(eid, speed, "; " + "; ".join(notes) if notes else "")
        return "Plunged the drain of {}: now {}{}".format(eid, w.ent(eid).get("drain_speed"), "; " + "; ".join(notes) if notes else "")
    raise ActionError("Nothing to plunge there", "argument")


@before("pick_up", order=150)
def gloves_for_the_bowl(ctx):
    """Anything fished out of toilet water is handled with rubber or disposable gloves (or a reacher)."""
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target") or "") or {}
    parent = w.s["entities"].get(rec.get("location") or "") or {}
    if not parent.get("toilet") or rec.get("relation") != "in":
        return
    wearing = set(w.robot.get("wearing", []))
    reacher = any(w.ent(h).get("tool_kind") in ("reacher", "grabber", "tongs") for h in w.held_items())
    require(wearing & {"rubber_gloves", "disposable_gloves"} or reacher, "Put on rubber gloves before reaching into the toilet", "safety")


@after("pick_up", order=150)
def object_out_of_toilet(ctx, result):
    """Lifting a visible object out of the bowl removes that blockage (the object is soiled)."""
    w = ctx.w
    obj = ctx.args.get("target")
    for eid in sorted(e for e, r in w.s["entities"].items() if r.get("toilet") and clog(r).get("object") == obj):
        m = w.mut(eid)
        c = dict(m["_clog"])
        c["object"] = None
        c["kind"], c["amount"] = "none", 0.0     # the object was the blockage
        m["_clog"] = c
        C.contaminate(w, obj, "biohazard", 0.7)
        C.plog(w, "object_retrieved", toilet=eid, item=obj)


# ----------------------------------------------------------------------------------
# liquids poured into a toilet (the right place for mop water, condensate, dirty water)

def receive(ctx, eid, parcel, source):
    w = ctx.w
    rec = w.ent(eid)
    if rec.get("open") is False and rec.get("openable"):
        raise ActionError("Lift the toilet lid first", "precondition")
    liters_ = parcel.get("liters", 0.0)
    rate_lpm = 30.0
    drained = min(liters_, drain_lpm(w, rec) * liters_ / rate_lpm)
    from .transfer import _log_disposal
    _log_disposal(w, eid, L.parcel_name(parcel), liters_, source, ok=True)
    add_to_bowl(w, eid, liters_ - drained)
    if clog_amount(w.ent(eid)) < 0.3:
        m = w.mut(eid)
        m["bowl_l"] = min(C.num(m.get("bowl_l"), 1.9), C.num(rec.get("normal_bowl_l"), 1.9))
    return None


# ----------------------------------------------------------------------------------
# simulation

def toilet_step(w, eid, dt, cache):
    rec = w.ent(eid)
    tank_l = C.num(rec.get("tank_l"), 6.0)
    level = C.num(rec.get("tank_level_l"), tank_l)
    on = supply_on(w, eid, cache)
    tank = rec.get("_tank") or {}
    flow = 0.0
    if on and level < tank_l - 1e-6:
        add = min(C.num(rec.get("fill_lpm"), 9.0) * dt / 60.0, tank_l - level)
        m = w.mut(eid)
        m["tank_level_l"] = C.rnd(level + add, 3)
        to_bowl = add * 0.15
        flow += add + to_bowl
        add_to_bowl(w, eid, to_bowl)
    if on and not tank.get("fill_valve_ok", True):
        stuck = C.num(tank.get("stuck_lpm"), 2.0) * dt / 60.0
        flow += stuck
        add_to_bowl(w, eid, stuck)
    rec = w.ent(eid)
    bowl = C.num(rec.get("bowl_l"), 1.9)
    normal = C.num(rec.get("normal_bowl_l"), 1.9)
    if bowl > normal + 1e-6:
        out = min(bowl - normal, drain_lpm(w, rec) * dt / 60.0)
        if out > 1e-6:
            w.mut(eid)["bowl_l"] = C.rnd(bowl - out, 3)
    if flow > 0:
        C.use_water(w, flow)
    leak = C.num(rec.get("_leak_lpm"))
    return flow / dt * 60.0 + leak if dt > 0 else leak


def toilet_idle(w, eid, cache):
    """True when toilet_step would change nothing: the tank full (or its supply off), no stuck fill valve running, the
    bowl at its normal level. Pure (the water system's idle probe)."""
    rec = w.s["entities"][eid]
    tank_l = C.num(rec.get("tank_l"), 6.0)
    on = supply_on(w, eid, cache)
    if on and (C.num(rec.get("tank_level_l"), tank_l) < tank_l - 1e-6 or not (rec.get("_tank") or {}).get("fill_valve_ok", True)):
        return False
    return C.num(rec.get("bowl_l"), 1.9) <= C.num(rec.get("normal_bowl_l"), 1.9) + 1e-6


def bowl_state(rec):
    bowl = C.num(rec.get("bowl_l"), 1.9)
    rim = C.num(rec.get("bowl_rim_l"), 6.5)
    normal = C.num(rec.get("normal_bowl_l"), 1.9)
    if bowl >= rim - 0.05:
        return "at the rim"
    if bowl >= 0.8 * rim:
        return "near the rim"
    if bowl > normal + 0.4:
        return "high"
    if bowl < normal * 0.7:
        return "low"
    return "normal"

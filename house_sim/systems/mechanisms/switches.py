"""Switch wiring and reach rules inside multi-part storage.

3-way switching: two switches with ``three_way_with`` pointing at each other control the same loads;
the load is on when the switch handle positions (the switches' ``on``) differ XOR the circuit's hidden phase
(_mech.phase), so "up" does not mean "on" - the goal is the load state, not the handle position.
Kernel semantics: core switch_on/switch_off on a switch wired to lights mean "light on/off with this
switch" (refused when the light already is). Since kernel v2.2 the core no longer syncs switches that carry
``three_way_with`` (other switches on the same lights still follow the light). A true 3-way pair keeps
physical handles: the handles are remembered before the action and restored after it with only the operated
switch's handle flipped (a no-op under v2.2, kept for idempotence with the electrical domain, which does the
same; both use ctx.data["_three_way_handles"], so it happens once), the hidden phase is realigned with the
light the core just set, then the loads follow the rule.

Bypass closets (closet_bypass) show one half at a time (mech.open_side left|right; pull_handle
side=...): items with ``side`` behind the closed panel cannot be reached. Lazy susans: items with
``susan_deg`` are reachable only when the turntable (mech.rotation_deg, turn degrees=...) brings
them within 60 degrees of the opening.
"""
from ...kernel.errors import require
from ...kernel.plugins import after, before
from . import common as C


def three_way_state(w, sid):
    rec = w.ent(sid)
    partner = rec.get("three_way_with")
    if not partner or partner not in w.s["entities"]:
        return None
    other = w.ent(partner)
    phase = bool(C.hidden(rec).get("phase") or C.hidden(other).get("phase"))
    return (bool(rec.get("on")) != bool(other.get("on"))) != phase


def _three_way_handles(w, loads):
    """{switch: handle_up} for every true 3-way switch (three_way_with partner) wired to any of `loads`."""
    ents = w.s["entities"]
    out = {}
    for sid in w.static_index("mechanisms.three_way", lambda r: bool(r.get("three_way_with"))):
        s = ents.get(sid)
        if s and s.get("three_way_with") in ents and set(loads) & set(s.get("controls") or []):
            out[sid] = bool(s.get("on"))
    return out


@before("switch_on", order=39)
@before("switch_off", order=39)
def _three_way_snapshot(ctx):
    if "_three_way_handles" in ctx.data:
        return                                   # electrical (order 38) already remembered them
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    loads = [c for c in rec.get("controls") or [] if c in ctx.w.s["entities"]]
    if loads:
        handles = _three_way_handles(ctx.w, loads)
        if handles:
            ctx.data["_three_way_handles"] = handles


@after("switch_on", order=21)
@after("switch_off", order=21)
def _three_way_restore(ctx, result):
    """Only the operated switch's handle moves (idempotent after electrical's identical restore); the hidden
    phase is kept consistent with the light the kernel just set."""
    handles = ctx.data.get("_three_way_handles")
    if not handles:
        return
    w = ctx.w
    ents = w.s["entities"]
    target = ctx.args.get("target")
    for sid, was in sorted(handles.items()):
        want = (not was) if sid == target else was
        if sid in ents and bool(ents[sid].get("on")) != want:
            w.mut(sid)["on"] = want
    done = set()
    for sid in sorted(handles):
        partner = (ents.get(sid) or {}).get("three_way_with")
        if sid in done or partner not in ents:
            continue
        done.update((sid, partner))
        loads = [l for l in ents[sid].get("controls") or [] if l in ents]
        if not loads:
            continue
        light_on = any(ents[l].get("on") for l in loads)
        if three_way_state(w, sid) != light_on:
            phase = (bool(ents[sid].get("on")) != bool(ents[partner].get("on"))) != light_on
            for s_id, value in ((sid, phase), (partner, False)):
                h = C.mut_hidden(w, s_id)
                h["phase"] = value


def _apply_three_way(ctx, result):
    w = ctx.w
    sid = ctx.args.get("target")
    rec = w.s["entities"].get(sid) or {}
    load_on = three_way_state(w, sid)
    if load_on is None:
        return
    loads = sorted(set(rec.get("controls") or []) | set(w.ent(rec["three_way_with"]).get("controls") or []))
    for load in loads:
        if load in w.s["entities"] and bool(w.ent(load).get("on")) != load_on:
            w.mut(load)["on"] = load_on


@after("switch_on", order=60)
def _three_way_on(ctx, result):
    _apply_three_way(ctx, result)


@after("switch_off", order=60)
def _three_way_off(ctx, result):
    _apply_three_way(ctx, result)


@before("pick_up", order=55)
def _reach_inside(ctx):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid)
    if not rec:
        return
    host = rec.get("location")
    hrec = w.s["entities"].get(host)
    if not hrec:
        return
    kind = C.kind_of(hrec)
    if kind == "closet_bypass" and rec.get("side"):
        require(C.mget(hrec, "open_side", "left") == rec["side"],
                "It is behind the closed sliding panel; slide the panels the other way (pull_handle side={})".format(rec["side"]), "precondition")
    if kind == "lazy_susan" and rec.get("susan_deg") is not None:
        rot = C.num(C.mget(hrec, "rotation_deg"), 0.0)
        delta = abs(((C.num(rec["susan_deg"]) - rot) + 180.0) % 360.0 - 180.0)
        require(delta <= 60.0, "It is at the back of the turntable; rotate it (turn degrees=...)", "precondition")

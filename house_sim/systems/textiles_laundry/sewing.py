"""Sewing and clothing upkeep: threading, buttons, seams, loose threads (fidelity G / P-approx).

Garments describe repairable features:
    buttons: {slot: {"state": "ok"|"loose"|"missing", "holes": 2|4, "size_mm": 11, "pattern": "cross"|"parallel"|"shank",
                     "thread_color": "white", "marks": true}}   + secret _button_origin_mm {slot: [x, y]}
    thick: true for coats/jackets (a thread shank is needed so the button can pass the buttonhole)
    seams: {name: {"split_cm": 4.0, "kind": "seam"|"tear", "opening_gap_cm": 3.0}}
    loose_threads: [ids] + secret _thread_kinds {id: "tail"|"structural"|"chain"}
Outcomes are geometric/mechanical: position error vs the original holes (<= 2 mm aligned),
layers caught (sewing both plackets shuts the shirt), shank height, knot (pull-test holding force),
stitch overlap past the split ends, distance to the next opening (sleeve/pocket sewn shut),
snipping or pulling structural or chain-stitched threads (seams/hems unravel).
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from . import model as M
from .care import _held_tool


def _needle(ctx, needle=None):
    w = ctx.w
    if needle:
        require(ctx.holding(needle) and w.ent(needle).get("needle"), "Hold the needle", "capability")
        return needle
    found = next((h for h in w.held_items() if w.ent(h).get("needle")), None)
    require(found is not None, "Hold a threaded needle", "capability")
    return found


@action("thread_needle", params={"target": "entity", "thread": "entity", "length_cm": {"type": "number", "default": 50.0, "min": 10, "max": 120},
                                 "knot": {"type": "bool", "default": True}, "doubled": {"type": "bool", "default": False},
                                 "threader": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.003, category="physical",
        doc="Thread the held needle from a thread spool (length_cm working length, knot the end, doubled for strength). "
            "A frayed thread end will not pass a fine needle's eye - trim it (trim_thread on the spool) or hold a needle "
            "threader.")
def thread_needle(ctx):
    w = ctx.w
    needle = ctx.args["target"]
    require(ctx.holding(needle) and w.ent(needle).get("needle"), "Hold the needle first", "capability")
    nrec = w.ent(needle)
    require(not nrec.get("threaded"), "The needle is already threaded")
    spool = ctx.args["thread"]
    srec = ctx.obj(spool, touch=True)
    require(srec.get("thread_spool"), "That is not a spool of thread", "argument")
    require(float(srec.get("length_m", 50.0)) * 100.0 >= ctx.args["length_cm"], "Not enough thread left", "resource")
    threader = ctx.args.get("threader") or _held_tool(ctx, "tool_kind", "needle_threader")
    if threader:
        require(ctx.holding(threader), "Hold the threader", "capability")
    fine = float(nrec.get("eye_mm", 1.0)) < 1.0
    if srec.get("frayed") and fine and not threader:
        raise ActionError("The frayed thread end will not pass the fine needle's eye; trim a fresh angled end or use a threader")
    m = w.mut(needle)
    m["threaded"] = {"color": srec.get("color"), "length_cm": float(ctx.args["length_cm"]), "knot": bool(ctx.args["knot"]),
                     "doubled": bool(ctx.args["doubled"]), "spool": spool}
    sm = w.mut(spool)
    sm["length_m"] = round(float(sm.get("length_m", 50.0)) - ctx.args["length_cm"] * (2 if ctx.args["doubled"] else 1) / 100.0, 3)
    ctx.data["seconds"] = 25.0 if (srec.get("frayed") and threader) else (15.0 if threader else 20.0)
    return "Threaded {} with {} thread ({} cm{})".format(needle, srec.get("color"), int(ctx.args["length_cm"]), ", knotted" if ctx.args["knot"] else "")


def _use_thread(w, needle, cm):
    m = w.mut(needle)
    threaded = dict(m.get("threaded") or {})
    require(float(threaded.get("length_cm", 0.0)) >= cm + 10, "Not enough thread left on the needle; rethread it", "resource")
    threaded["length_cm"] = round(float(threaded["length_cm"]) - cm, 1)
    m["threaded"] = threaded
    return threaded


@action("sew_button", params={"target": "entity", "slot": "str", "button": "?entity", "position": "any",
                              "shank": {"type": "bool", "default": False},
                              "pattern": {"type": "str", "default": "cross", "choices": ["cross", "parallel", "shank"]},
                              "layers": {"type": "str", "default": "placket", "choices": ["placket", "both"]},
                              "needle": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 420.0), energy=0.01, category="physical",
        doc="Sew a missing (with a spare button) or loose button onto a garment slot with the held threaded needle. "
            "position: 'marks' (the old thread holes), 'buttonhole' (aligned to the buttonhole) or [x, y] mm; shank=true "
            "wraps a thread shank (thick fabrics); layers placket (correct) or both (catches the opposite placket).")
def sew_button(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True, action="sew_button")
    buttons = rec.get("buttons") or {}
    slot = ctx.args["slot"]
    require(slot in buttons, "No button slot named {}".format(slot) if ctx.realistic else
            "No button slot named {}; slots: {}".format(slot, ", ".join(sorted(buttons))), "argument")   # realistic: inspect lists them
    info = buttons[slot]
    require(info.get("state") in ("missing", "loose"), "That button is already secure")
    needle = _needle(ctx, ctx.args.get("needle"))
    threaded = w.ent(needle).get("threaded")
    require(threaded, "Thread the needle first")
    button = ctx.args.get("button")
    if info.get("state") == "missing":
        require(button, "Bring the spare button (button)", "argument")
        brec = ctx.obj(button, touch=True)
        require(brec.get("sewing_button"), "That is not a button", "argument")
        require(abs(float(brec.get("size_mm", 11)) - float(info.get("size_mm", 11))) <= 1.0, "That button is the wrong size for this buttonhole")
    origin = (rec.get("_button_origin_mm") or {}).get(slot, [0.0, 0.0])
    position = ctx.args.get("position")
    if position == "marks":
        require(info.get("marks", True), "There are no old thread marks to follow; align it to the buttonhole")
        error = 0.5
    elif position == "buttonhole":
        error = 1.0
    elif position == "approx" or position is None:
        error = 6.0
    elif isinstance(position, list) and len(position) == 2 and all(isinstance(v, (int, float)) for v in position):
        error = ((float(position[0]) - float(origin[0])) ** 2 + (float(position[1]) - float(origin[1])) ** 2) ** 0.5
    else:
        raise ActionError("position must be 'marks', 'buttonhole', 'approx' or [x, y] mm", "argument")
    used = _use_thread(w, needle, 30.0)
    holds = 40.0 if used.get("knot") else 2.0
    if used.get("doubled"):
        holds *= 1.5
    new = dict(info)
    new["position_error_mm"] = round(error, 1)
    new["thread_color_used"] = used.get("color")
    new["thread_match"] = used.get("color") == info.get("thread_color", rec.get("color"))
    new["pattern_used"] = ctx.args["pattern"]
    new["pattern_match"] = ctx.args["pattern"] == info.get("pattern", "cross")
    new["shank"] = bool(ctx.args["shank"]) or ctx.args["pattern"] == "shank"
    new["holds_n"] = holds
    new["crooked"] = error > 2.0
    new["fastens"] = not (rec.get("thick") and not new["shank"])
    ok = not new["crooked"] and new["fastens"] and holds >= 20.0 and ctx.args["layers"] == "placket"
    new["state"] = "ok" if ok else "sewn_badly"
    m = w.mut(target)
    m["buttons"] = dict(buttons)
    m["buttons"][slot] = new
    if ctx.args["layers"] == "both":
        M.add_damage(w, target, "plackets_sewn_together", 0.5, why="needle caught both plackets")
    if button:
        bm = w.mut(button)
        bm["location"], bm["relation"] = target, "part"
        for key in ("pos", "spos", "shelf", "held_hand"):
            bm.pop(key, None)
        from ..core import release_from_hands
        release_from_hands(w, button)
    ctx.data["seconds"] = 420.0 + (60.0 if new["shank"] else 0.0)
    off = ("visibly off its marks" if ctx.realistic else "{:.0f} mm off".format(error))
    return "Sewed the {} button on {} ({}{})".format(slot, target, "aligned" if not new["crooked"] else off,
                                                     "" if new["fastens"] else ", no shank - it will not pass the buttonhole")


@action("repair_seam", params={"target": "entity", "seam": "str",
                               "stitch": {"type": "str", "default": "backstitch", "choices": ["backstitch", "ladder", "slip", "whip", "running"]},
                               "side": {"type": "str", "default": "inside", "choices": ["inside", "outside"]},
                               "overlap_cm": {"type": "number", "default": 1.0, "min": 0, "max": 10},
                               "needle": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 600.0), energy=0.01, category="physical",
        doc="Hand-stitch a split seam closed along the original seam line with the held threaded needle: stitch type, "
            "side (inside hides the repair), overlap_cm past each end of the split (>= 1 cm, but not into the next opening). "
            "Fabric tears, long splits and leather/technical fabrics are not simple repairs.")
def repair_seam(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True, action="repair_seam")
    seams = rec.get("seams") or {}
    name = ctx.args["seam"]
    require(name in seams, "No damaged seam named {}".format(name) if ctx.realistic else
            "No damaged seam named {}; seams: {}".format(name, ", ".join(sorted(seams))), "argument")
    info = dict(seams[name])
    require(float(info.get("split_cm", 0.0)) > 0, "That seam is intact")
    needle = _needle(ctx, ctx.args.get("needle"))
    threaded = w.ent(needle).get("threaded")
    require(threaded, "Thread the needle first")
    split = float(info["split_cm"])
    overlap = float(ctx.args["overlap_cm"])
    used = _use_thread(w, needle, split * 5.0 + 4.0 * overlap)
    simple = info.get("kind", "seam") == "seam" and split <= 10.0 and rec.get("fabric") not in ("leather", "suede", "pu_membrane") and not rec.get("technical_fabric")
    m = w.mut(target)
    m["seams"] = dict(seams)
    if not simple:
        M.add_damage(w, target, "botched_repair", 0.6, why="hand-stitched damage that needed a tailor")
        info["attempted"] = True
        m["seams"][name] = info
        ctx.data["seconds"] = 300.0
        return "Stitched over the {} on {} - it is not a seam repair and puckers badly".format(info.get("kind", "damage"), target)
    holds = overlap >= 1.0 and used.get("knot", True) and not (ctx.args["stitch"] == "running" and (M.is_knit(rec) or rec.get("stretch")))
    info["split_cm"] = 0.0 if holds else round(split * 0.5, 1)
    info["repaired"] = True
    info["holds"] = bool(holds)
    info["visible_stitches"] = ctx.args["side"] == "outside"
    info["thread_match"] = used.get("color") == rec.get("thread_color", rec.get("color"))
    if overlap > float(info.get("opening_gap_cm", 99.0)):
        info["opening_sewn_shut"] = True
        M.add_damage(w, target, "opening_sewn_shut", 0.5, why="stitched past the seam into a pocket/sleeve opening")
    m = w.mut(target)
    m["seams"][name] = info
    ctx.data["seconds"] = 300.0 + 60.0 * (max(1, round(split)) if ctx.realistic else split)   # realistic: no exact split length off the clock
    return "Closed the {} seam on {}{}".format(name, target, "" if holds else " - too little overlap, it will reopen")


@action("trim_thread", params={"target": "entity", "thread": "?str", "method": {"type": "str", "default": "snip", "choices": ["snip", "pull", "secure"]},
                               "tool": "?entity"},
        duration=8.0, energy=0.002, category="physical",
        doc="Deal with a loose thread on a garment: snip it close with held thread snips/scissors, pull it (unravels chain "
            "stitches and seams) or secure it with a knot (for threads coming from an unravelling seam). On a thread spool, "
            "snipping cuts a fresh, unfrayed end for threading.")
def trim_thread(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    method = ctx.args["method"]
    tool = ctx.args.get("tool")
    if method == "snip":
        tool = tool or next((h for h in w.held_items() if "cut_thread" in (w.ent(h).get("affordances") or [])), None)
        require(tool and ctx.holding(tool), "Hold thread snips or small scissors", "capability")
    if rec.get("thread_spool"):
        require(method == "snip", "Snip the end to get a fresh one")
        w.mut(target)["frayed"] = False
        return "Cut a fresh angled end on " + target
    loose = list(rec.get("loose_threads") or [])
    require(loose, "There are no loose threads on it")
    thread = ctx.args.get("thread") or loose[0]
    require(thread in loose, "No loose thread {}".format(thread) if ctx.realistic else
            "No loose thread {}; loose threads: {}".format(thread, ", ".join(loose)), "argument")
    kind = (rec.get("_thread_kinds") or {}).get(thread, "tail")
    m = w.mut(target)
    remaining = [t for t in loose if t != thread]
    outcome = "removed"
    if method == "snip":
        if kind == "structural":
            M.add_damage(w, target, "seam_opened", 0.5, why="cut a structural seam thread")
            seams = dict(m.get("seams") or {})
            seams["snipped_" + thread] = {"split_cm": 3.0, "kind": "seam", "opening_gap_cm": 5.0}
            m = w.mut(target)
            m["seams"] = seams
            outcome = "cut a seam thread - the seam opened"
        elif kind == "chain":
            M.add_damage(w, target, "hem_unravelling", 0.4, why="snipped a chain-stitch thread flush; the hem runs")
            outcome = "snipped a chain stitch - the hem starts to unravel"
    elif method == "pull":
        if kind in ("structural", "chain"):
            M.add_damage(w, target, "hem_unravelled" if kind == "chain" else "seam_unravelled", 0.6, why="pulled a loose thread")
            outcome = "pulled it and the {} unravelled".format("hem" if kind == "chain" else "seam")
    else:
        if kind == "tail":
            remaining = loose
            outcome = "knotted a harmless tail (still dangling)"
        else:
            outcome = "secured the unravelling thread with a knot"
            m = w.mut(target)
            m["secured_threads"] = sorted(set(m.get("secured_threads") or []) | {thread})
    m = w.mut(target)
    m["loose_threads"] = remaining
    return "{}: {}".format(thread, outcome)

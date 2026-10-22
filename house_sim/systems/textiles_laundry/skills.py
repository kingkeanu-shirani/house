"""Reference skills for laundry and textile procedures (privileged routing; every change goes through runner.do).

    textiles_laundry.load_washer(washer, items, pods=None)          pods first, then items, door closed after
    textiles_laundry.run_washer(washer, program, temp=None, spin=None, soil=None)
    textiles_laundry.wait_cycle(machine, max_s=14400)             listen for the end chime (+ door unlock)
    textiles_laundry.move_items(items, dest, relation=None)        one by one (fetch + put)
    textiles_laundry.clean_lint_screen(dryer, trash)
    textiles_laundry.run_dryer(dryer, program, heat=None, minutes=None)
    textiles_laundry.fold_on(item, surface, style, dest=None)
    textiles_laundry.hang_garment(garment, hanger, rod, method=None, button_top=False)
    textiles_laundry.dress_bare_bed(bed, fitted, cover, insert, pillows, cases, protector=None, flat=None)
    textiles_laundry.iron_garment(garment, iron, board, temp)
    textiles_laundry.sew_on_button(garment, slot, needle, spool, button=None, home=None, shank=False)
    textiles_laundry.pair_into(pairs, dest)
"""
from ...kernel import geometry, perception
from ...kernel.plugins import skill
from ...kernel.predicates import root_room
from ...tasks import skills as base


def _here(r, eid):
    return eid in r.state["entities"] and geometry.reach_problem(r.state, eid) is None


def _approach(r, eid):
    if not _here(r, eid):
        base.approach(r, eid)


@skill("textiles_laundry.load_washer")
def load_washer(r, washer, items, pods=None):
    base.approach(r, washer)
    if not r.state["entities"][washer].get("open"):
        r.do("open", target=washer)
    for pod in pods or []:
        base.fetch(r, pod)
        _approach(r, washer)
        r.do("place", target=pod, destination=washer, relation="in")
    for item in items:
        base.fetch(r, item)
        _approach(r, washer)
        r.do("place", target=item, destination=washer, relation="in")
    _approach(r, washer)
    r.do("close", target=washer)


@skill("textiles_laundry.run_washer")
def run_washer(r, washer, program, temp=None, spin=None, soil=None, extra_rinse=None):
    _approach(r, washer)
    rec = r.state["entities"][washer]
    if rec.get("open"):
        r.do("close", target=washer)
    if rec.get("switchable") and not r.state["entities"][washer].get("on"):
        r.do("switch_on", target=washer)
    r.do("set_mode", target=washer, mode=program)
    for setting, value in (("temp", temp), ("spin", spin), ("soil", soil), ("extra_rinse", extra_rinse)):
        if value is not None:
            r.do("set", target=washer, setting=setting, value=value)
    r.do("start", target=washer)


@skill("textiles_laundry.wait_cycle")
def wait_cycle(r, machine, max_s=14400, poll_s=300):
    """Wait for a washer or dryer to finish the way a person does: listen for its end chime (an agent-legal wait,
    interrupt_on=["appliance_done"]: the kernel's notice bus, TIER1_SWEEP 6.3) for up to max_s. Another appliance's
    chime, or a chime missed from too far away, sends the robot back to waiting (the display shows the machine still
    running); then it gives the door lock its release time. poll_s is kept for older plans and unused."""
    waited, max_s = 0, int(max_s)
    while (r.state["entities"][machine].get("appliance") or {}).get("state") in ("running", "delayed", "paused") and waited < max_s:
        before = float(r.state.get("time_s", 0.0))
        if not r.do("wait", seconds=max(1, max_s - waited), interrupt_on=["appliance_done"])["ok"]:
            break                           # a refused wait (a non-strict plan) must not spin
        waited += max(1, int(round(float(r.state.get("time_s", 0.0)) - before)))
    if r.state["entities"][machine].get("door_locked"):
        r.do("wait", seconds=120)


@skill("textiles_laundry.move_items")
def move_items(r, items, dest, relation=None):
    for item in items:
        base.put(r, item, dest, relation=relation)


@skill("textiles_laundry.clean_lint_screen")
def clean_lint_screen(r, dryer, trash):
    _approach(r, dryer)
    if not r.state["entities"][dryer].get("open"):
        r.do("open", target=dryer)
    r.do("uninstall", target=dryer, slot="lint_screen")
    screen = next(h for h in r.state["robot"]["hands"].values() if h and r.state["entities"][h].get("lint_screen"))
    base.approach(r, trash)
    trec = r.state["entities"][trash]
    if trec.get("openable") and not trec.get("open"):
        r.do("open", target=trash)
    r.do("lint_roll", target=screen, into=trash)
    base.approach(r, dryer)
    r.do("install", target=screen, destination=dryer, slot="lint_screen")
    return screen


@skill("textiles_laundry.run_dryer")
def run_dryer(r, dryer, program, heat=None, minutes=None, dryness=None):
    _approach(r, dryer)
    if r.state["entities"][dryer].get("open"):
        r.do("close", target=dryer)
    if not r.state["entities"][dryer].get("on"):
        r.do("switch_on", target=dryer)
    r.do("set_mode", target=dryer, mode=program)
    for setting, value in (("heat", heat), ("minutes", minutes), ("dryness", dryness)):
        if value is not None:
            r.do("set", target=dryer, setting=setting, value=value)
    r.do("start", target=dryer)


@skill("textiles_laundry.fold_on")
def fold_on(r, item, surface, style, dest=None, relation=None):
    rec = r.state["entities"][item]
    if rec.get("location") != surface:
        base.put(r, item, surface)
    _approach(r, surface)
    r.do("fold", target=item, style=style)
    if dest:
        base.put(r, item, dest, relation=relation)


@skill("textiles_laundry.hang_garment")
def hang_garment(r, garment, hanger, rod, method=None, button_top=False, facing=None):
    base.fetch(r, garment)
    _approach(r, hanger)
    args = {"target": garment, "hanger": hanger, "button_top": button_top}
    if method:
        args["method"] = method
    if facing:
        args["facing"] = facing
    r.do("hang_on_hanger", **args)
    base.reveal(r, rod)
    base.approach(r, rod)
    r.do("place", target=garment, destination=rod, relation="hung")


@skill("textiles_laundry.dress_bare_bed")
def dress_bare_bed(r, bed, fitted, cover, insert, pillows, cases, protector=None, flat=None, surface=None):
    surface = surface or bed
    for item, kwargs in ((protector, {"face_up": "finished", "orientation": "lengthwise"}),
                         (fitted, {"face_up": "finished", "orientation": "lengthwise"}),
                         (flat, {"face_up": "back", "head": "wide_hem"})):
        if not item:
            continue
        base.fetch(r, item)
        base.approach(r, bed)
        args = {"target": item, "destination": bed}
        args.update(kwargs)
        r.do("dress_bed", **args)
    base.fetch(r, insert)
    base.approach(r, bed)
    r.do("place", target=insert, destination=bed)
    base.fetch(r, cover)
    base.approach(r, bed)
    r.do("dress_bed", target=cover, destination=insert, method="inside_out", orientation="lengthwise", ties=True, close=True)
    base.fetch(r, cover)
    base.approach(r, bed)
    r.do("dress_bed", target=cover, destination=bed, face_up="finished", orientation="lengthwise")
    for pillow, case in zip(pillows, cases):
        base.fetch(r, case)
        base.approach(r, pillow)
        r.do("dress_bed", target=case, destination=pillow, method="invert")


@skill("textiles_laundry.iron_garment")
def iron_garment(r, garment, iron, board, temp, press_cloth=None, steam=None):
    """Iron at a set-up ironing station (the iron rests on the board, so it is never carried around plugged in)."""
    base.approach(r, board)
    if not r.state["entities"][board].get("open"):
        r.do("open", target=board)
    base.put(r, garment, board)
    base.approach(r, board)
    base.fetch(r, iron)
    if not r.state["entities"][iron].get("on"):
        r.do("switch_on", target=iron)
    r.do("set", target=iron, setting="temp", value=temp)
    if steam is not None:
        r.do("set", target=iron, setting="steam", value=steam)
    target_t = {"low": 110.0, "medium": 150.0, "high": 200.0, "max": 220.0}[temp]
    waited = 0
    while abs(float(r.state["entities"][iron].get("temperature_c", 21.0)) - target_t) > 3.0 and waited < 600:
        r.do("wait", seconds=30)
        waited += 30
    base.approach(r, board)
    args = {"target": garment, "tool": iron}
    if press_cloth:
        args["press_cloth"] = press_cloth
    r.do("iron", **args)
    r.do("switch_off", target=iron)
    r.do("place", target=iron, destination=board, orientation="upright")


CLOSE_WORK_LIGHT = 0.35     # body.precision_mm: a room dimmer than this (no robot light) makes the hand 1.5x less precise


def light_for_close_work(r, near):
    """Close work (threading a needle, sewing a button) needs the room lit: when it is dim, switch on a light the robot
    can reach -- a wall switch wired to the room's lights, else a lamp -- and come back to `near`. Raises a plan error
    when the room has neither."""
    state = r.state
    room = state["robot"]["room"]
    if perception.light_level(state, room) >= CLOSE_WORK_LIGHT or perception.flashlight_on(state):
        return
    ents = state["entities"]
    switches = [e for e in sorted(ents) if ents[e].get("category") == "switch" and ents[e].get("location") == room
                and ents[e].get("controls") and not any(ents[c].get("on") for c in ents[e]["controls"] if c in ents)]
    lamps = [e for e in sorted(ents) if ents[e].get("category") == "light" and ents[e].get("portable")
             and not ents[e].get("on") and root_room(state, e) == room]
    target = (switches or lamps or [None])[0]
    if target is None:
        raise base._plan_error("{} is too dim for close work and has no light within reach".format(room))
    base.approach(r, target)
    r.do("switch_on", target=target)
    base.approach(r, near)


@skill("textiles_laundry.sew_on_button")
def sew_on_button(r, garment, slot, needle, spool, button=None, home=None, shank=False, threader=None, snips=None,
                  position="marks", layers="placket"):
    if snips:
        base.fetch(r, snips, grip="handle")
        base.approach(r, spool)
        r.do("trim_thread", target=spool, tool=snips)
        home_s = r.state["entities"][snips].get("home") or r.state["entities"][spool].get("location")
        r.do("place", target=snips, destination=home_s)
    light_for_close_work(r, spool)
    base.fetch(r, needle, grip="pinch")
    threader_home = None
    if threader:
        # a needle threader in the other hand: a humanoid hand cannot aim a thread at a needle's eye unaided
        threader_home = r.state["entities"][threader].get("location")
        base.fetch(r, threader, grip="pinch")
    base.approach(r, spool)
    args = {"target": needle, "thread": spool, "knot": True}
    if threader:
        args["threader"] = threader
    r.do("thread_needle", **args)
    if threader:
        if threader_home in r.state["entities"]:
            base.approach(r, threader_home)
            r.do("place", target=threader, destination=threader_home)
        else:
            r.do("place", target=threader)
    base.approach(r, garment)
    sew = {"target": garment, "slot": slot, "position": position, "shank": shank, "needle": needle, "layers": layers}
    if button:
        sew["button"] = button
    r.do("sew_button", **sew)
    if home:
        base.approach(r, home)
        r.do("place", target=needle, destination=home)


@skill("textiles_laundry.pair_into")
def pair_into(r, pairs, dest, relation=None):
    for a, b in pairs:
        base.fetch(r, a)
        _approach(r, b)
        r.do("pair", target=a, destination=b)
        base.put(r, a, dest, relation=relation)

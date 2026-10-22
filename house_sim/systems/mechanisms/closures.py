"""Closure mechanics shared by doors, gates, windows, drawers, cabinets, closets and lids.

Core ``open``/``close`` are the robot's correct default motion for a closure (release the ordinary
latch, swing/slide it fully, guide it shut until the latch catches). This module adds, through
@before/@after hooks:

* refusals that the hardware imposes (child locks, slide bolts and two-step latches, clasps, stuck
  sashes and drawers, derailed tracks, childproof knob covers, hidden buttons, tamper seals,
  obstructions in the closing path, a person/pet in the jamb, an AC unit in a window);
* consequences: latches that do not catch on a sagging gate or bouncing self-closer, doors that hit
  someone standing in their swing on the far side, items that lean on a cabinet door or sit on a
  hinged lid falling out, unanchored dressers tipping with several loaded drawers open, slams at
  fast speed, pets dashing through an opened exterior door or gate, child locks re-engaging on
  close, self-closing doors closing behind the robot, passing through a forced door.

handles.py (pull_handle/push_handle) and latches.py reuse check_open/apply_open/check_close/
apply_close with explicit extents and forces.
"""
from ...kernel import geometry
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook, run_hooks
from ..core import subtree_mass
from . import common as C


# ----------------------------------------------------------------------------------
# helpers

def is_latched(rec):
    if "latched" in rec:
        return bool(rec.get("latched"))
    return not rec.get("open")


def latch_kind(rec):
    return C.mget(rec, "latch")


def contents(w, eid, relation=None):
    return [c for c in w.children(eid) if relation is None or w.ent(c).get("relation", "on") == relation]


def protruding(w, eid):
    """Contents that stick out past the closure plane (flag, or taller/deeper than the space)."""
    rec = w.ent(eid)
    inner = rec.get("inner_size_m")
    out = []
    for child in contents(w, eid, "in"):
        crec = w.ent(child)
        if crec.get("protrudes"):
            out.append(child)
            continue
        if inner and C.kind_of(rec) == "drawer":
            size = crec.get("size_m") or [0.1, 0.1, 0.1]
            if C.num(size[2], 0.1) > C.num(inner[2], 0.12) + 0.01 and crec.get("orientation") in (None, "upright") and crec.get("stands_upright"):
                out.append(child)
    return out


def in_path(w, eid):
    """Entities flagged as lying in this closure's travel (curtain hem, plant leaf, hose, rake)."""
    found = []
    for oid in C.ids(w, "in_path"):
        rec = w.s["entities"].get(oid) or {}
        if rec.get("in_path_of") == eid:
            found.append(oid)
    return found


def threshold_items(w, eid):
    """Non-ground floor items lying on a doorway cell (hose, tool handle, shoe)."""
    state = w.s
    items = []
    for rid in C.door_rooms(state, eid):
        cell = C.doorway_cell(state, eid, rid)
        if cell is None:
            continue
        for item in C.floor_items_at(state, rid, [cell], exclude=(eid,)):
            rec = state["entities"][item]
            if rec.get("blocks_path"):
                continue
            items.append(item)
    return items


def users_of(state, eid):
    found = []
    for pid in sorted(state.get("people", {})):
        if state["people"][pid].get("using") == eid:
            found.append(pid)
    return found


def close_obstructions(w, eid):
    """[(kind, id)] blocking a full close. kinds: agent, protrudes, path, threshold, user, ac_unit, sill_pet."""
    state = w.s
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    out = []
    if (kind in C.DOOR_KINDS or rec.get("connects")) and kind != "overhead_door":     # incl. installed child safety gates
        for group, aid in C.agents_in_doorway(state, eid):
            out.append(("agent", aid))
        for item in threshold_items(w, eid):
            out.append(("threshold", item))
    if kind in C.WINDOW_KINDS:
        for group in ("people", "pets"):
            for aid in sorted(state.get(group, {})):
                if state[group][aid].get("on_sill") == eid or state[group][aid].get("in_frame") == eid:
                    out.append(("agent", aid))
        if C.mget(rec, "ac_unit"):
            out.append(("ac_unit", C.mget(rec, "ac_unit")))
    for item in protruding(w, eid):
        out.append(("protrudes", item))
    if kind == "drawer":
        for child in contents(w, eid):
            if w.ent(child).get("relation") == "behind" and w.ent(child).get("blocks_close", True):
                out.append(("behind", child))
    for item in in_path(w, eid):
        out.append(("path", item))
    for pid in users_of(state, eid):
        out.append(("user", pid))
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if agent.get("hand_in") == eid or agent.get("paw_in") == eid or agent.get("tail_in") == eid:
                out.append(("agent", aid))
    return out


def describe_obstruction(w, kind, oid, ctx=None):
    state = w.s
    if kind == "agent":
        group = "people" if oid in state.get("people", {}) else "pets"
        return "{} is in the way".format(C.agent_ref(ctx, state, group, oid))
    if kind == "user":
        if C.realistic(ctx):
            return "{} is using it".format(oid)
        return "{} is using it".format(state["people"].get(oid, {}).get("label", oid))
    if kind == "ac_unit":
        return "a window air conditioner sits in the opening"
    label = C.ref(ctx, oid) if ctx is not None else state["entities"].get(oid, {}).get("label", oid)
    if kind == "protrudes":
        return "it stops short: something inside sticks out"
    if kind == "behind":
        return "it stops about 3 cm short: something is blocking it from behind"
    if kind == "threshold":
        return "{} lies across the threshold".format(label)
    return "{} is caught in the closing path".format(label)


# ----------------------------------------------------------------------------------
# opening

def child_lock_check(ctx, rec, eid, forcing=False):
    """Refuse (or break when forcing) an engaged child lock. Returns True if a lock was broken."""
    if not rec.get("child_lock"):
        return False
    w = ctx.w
    lock_kind = C.mget(rec, "child_lock_kind", "latch")
    if forcing:
        rr, mech = C.mut_mech(w, eid)
        rr["child_lock"] = False
        mech["child_lock_kind"] = None
        mech["child_lock_broken"] = True
        C.mark_damage(w, eid, "child lock latch broken by force")
        return True
    if lock_kind == "latch":
        return False  # adult technique: press the catch through the gap while opening
    if lock_kind == "magnetic":
        key = C.held_tool(ctx, kinds=("magnetic_lock_key",), affordances=("magnetic_key",))
        require(key is not None, "A magnetic child lock holds it shut; hold the magnetic key against the door first", "precondition")
        return False
    raise ActionError("A {} child lock holds it; release it first (unlatch part=child_lock)".format(lock_kind.replace("_", " ")), "precondition")


def check_open(ctx, eid, force="normal", via="open"):
    """Hardware refusals before a closure can open. Forcing past some of them breaks things."""
    w = ctx.w
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    h = C.hidden(rec)
    newtons = C.applied_n(w.s, force, "push" if via == "push" else "pull")
    if kind in C.COVERING_KINDS or kind == "overhead_door":
        return
    if C.mget(rec, "track") == "derailed":
        raise ActionError("It is off its track; re-seat it first (push_handle lift=true)", "precondition")
    # childproof knob cover on a door
    if C.mget(rec, "knob_cover") and not _latch_held(w, rec):
        if C.yanked(newtons):
            rr, mech = C.mut_mech(w, eid)
            mech["knob_cover"] = False
            mech["knob_cover_removed"] = True
            C.mark_damage(w, eid, "childproof knob cover pulled off", stat=None)
        else:
            raise ActionError("The knob cover spins freely; squeeze its sides while turning (turn part=knob squeeze=true)", "precondition")
    if child_lock_check(ctx, rec, eid, forcing=C.yanked(newtons)):
        rec = w.ent(eid)
    # explicit latches (slide bolts, two-step pool latches, clasps, squeeze-lift baby gates)
    if is_latched(rec) and latch_kind(rec) in C.EXPLICIT_LATCHES and not rec.get("open"):
        if C.yanked(newtons):
            rr = w.mut(eid)
            rr["latched"] = False
            C.mark_damage(w, eid, "{} forced and bent".format(latch_kind(rec).replace("_", " ")))
        else:
            raise ActionError("Release the {} first (unlatch)".format(latch_kind(rec).replace("_", " ")), "precondition")
    clasps = int(C.num(C.mget(rec, "clasps_closed"), 0))
    if kind == "latch_clasp" and clasps > 0 and not rec.get("open"):
        if C.yanked(newtons):
            rr, mech = C.mut_mech(w, eid)
            mech["clasps_closed"] = 0
            C.mark_damage(w, eid, "clasps ripped open")
        else:
            raise ActionError("{} clasp(s) still closed; unlatch them first".format(clasps), "precondition")
    if kind == "hidden_button" and not rec.get("open"):
        released = C.num(C.mget(rec, "released_until"), -1)
        if w.s.get("time_s", 0) > released:
            if newtons < C.FORCE_N["firm"]:
                raise ActionError("It does not open by pulling; look closely for how it releases", "precondition")
            C.mark_damage(w, eid, "hinge cracked by prying open a hidden-catch box")
    if kind == "tamper_seal" and C.mget(rec, "seal_intact", True) and not rec.get("open"):
        raise ActionError("A tamper seal band holds the lid; pull its tab off first (pull_handle part=seal)", "precondition")
    # stuck closures: paint seal, swollen wood, debris, vacuum
    stuck = h.get("stuck")
    if stuck and not rec.get("open"):
        if stuck == "debris" and not _track_debris(w, eid):
            stuck = None
        elif stuck == "swollen" and C.num(h.get("worked"), 0) >= C.num(h.get("work_needed"), 3):
            stuck = None
    if stuck and not rec.get("open"):
        hold_nm = C.num(h.get("stuck_force_n"), 150.0)
        if newtons > hold_nm:
            # breaking a paint seal by force cracks glass or gouges the frame
            C.mark_damage(w, eid, "forced while {}: frame gouged / glass cracked".format(stuck.replace("_", " ")),
                          broken=bool(rec.get("fragile") or rec.get("material") == "glass"))
            hh = C.mut_hidden(w, eid)
            hh["stuck"] = None
        else:
            raise ActionError("It will not budge; something is holding it (inspect it closely)", "precondition")
    if kind in C.WINDOW_KINDS and rec.get("locked"):
        raise ActionError("The sash lock is engaged; unlock it first", "precondition")
    if kind in C.WINDOW_KINDS and C.mget(rec, "tilted_in"):
        raise ActionError("The sash is tilted in; tilt it back first (push_handle part=tilt_in)", "precondition")
    if kind == "drawer" and not rec.get("open"):
        owner = dresser_of(w.s, eid)
        if owner and w.ent(owner).get("interlock"):
            others = [d for d in sorted(w.s["entities"]) if d != eid and dresser_of(w.s, d) == owner and w.ent(d).get("open")]
            require(not others, "The anti-tip interlock holds it: close the other drawer first", "precondition")
    if kind == "drawer" and not rec.get("open"):
        from . import diagnosis
        cause = diagnosis.stick_cause(w, eid)
        if cause:
            if newtons < C.FORCE_N["firm"]:
                raise ActionError(diagnosis.FEEL[cause] + "; it will not open", "precondition")
            rm, mech = C.mut_mech(w, eid)
            mech["strain"] = int(C.num(mech.get("strain"))) + 1
            if mech["strain"] >= 2 or C.yanked(newtons):
                C.mark_damage(w, eid, "drawer slide damaged by yanking it")
            if cause == "item_caught":
                for child in [c for c in w.children(eid) if w.ent(c).get("relation") == "behind"]:
                    cm = w.mut(child)
                    cm["crushed"] = True
                    if cm.get("fragile"):
                        cm["broken"] = True
                        w.stat("breakages")


def _latch_held(w, rec):
    return C.num(C.mget(rec, "latch_released_until"), -1) >= w.s.get("time_s", 0)


def _track_debris(w, eid):
    return [c for c in w.children(eid) if w.ent(c).get("in_track")]


def window_limit(rec):
    stop = C.mget(rec, "child_stop")
    if isinstance(stop, dict) and stop.get("engaged"):
        return C.num(stop.get("limit_frac"), 0.12)
    return 1.0


def apply_open(ctx, eid, extent=1.0, force="normal", via="open", held_back=False):
    """State and consequences after a closure opened to `extent` (0..1)."""
    w = ctx.w
    state = w.s
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    if kind in C.COVERING_KINDS or kind == "overhead_door":
        return
    newtons = C.applied_n(state, force, "push" if via == "push" else "pull")
    extent = max(0.02, min(1.0, float(extent)))
    if kind in C.WINDOW_KINDS:
        limit = window_limit(rec)
        if extent > limit:
            C.tell(ctx, "A child window stop limits the opening")
        extent = min(extent, limit)
    if "mech" in rec or via != "open" or extent < 0.999 or kind in C.WINDOW_KINDS and window_limit(rec) < 1.0:
        C.set_open(w, eid, extent)
    else:
        w.mut(eid)["open"] = True
    if "latched" in rec or C.mget(rec, "latch") in C.EXPLICIT_LATCHES:
        w.mut(eid)["latched"] = False
    if kind == "hinged_lid":
        rr, mech = C.mut_mech(w, eid)
        mech["angle_deg"] = round(C.num(C.mget(rec, "max_deg"), 100.0) * extent, 1)
        _items_on_lid_fall(ctx, eid)
    if kind in C.DOOR_KINDS:
        if extent >= 0.3:
            _swing_contacts(ctx, eid, newtons)
            _door_dash(ctx, eid)
        else:
            _feel_contact(ctx, eid)
        if C.self_closing(rec):
            rr, mech = C.mut_mech(w, eid)
            mech["release_at"] = state["time_s"]
            mech.pop("rests_ajar", None)
            mech.pop("resting_on", None)
    if kind == "drawer":
        _drawer_opened(ctx, eid, extent, newtons)
    if kind in C.CABINET_KINDS or kind == "drawer" or kind in C.LID_KINDS:
        _leaners_fall(ctx, eid, extent, held_back)
    if kind in C.CABINET_KINDS:
        _cabinet_swing(ctx, eid, newtons)
        if C.yanked(newtons) and (rec.get("material") == "glass" or kind == "glass_front_cabinet"):
            C.mark_damage(w, eid, "glass door cracked when yanked", broken=True)
    if kind == "high_chair":
        rr, mech = C.mut_mech(w, eid)
        stiff = C.hidden(rec).get("stiff_lock")
        mech["folded"] = False
        mech["frame_locks"] = {"left": stiff != "left", "right": stiff != "right"}
    if kind in C.DOOR_KINDS or kind in ("closet_hinged", "closet_bifold", "cabinet_door", "glass_front_cabinet"):
        if extent >= 0.3:
            _sweep_floor(ctx, eid, newtons)
    if kind == "snap_lid" and newtons >= C.FORCE_N["firm"] and C.num(rec.get("liquid_l")) > 0:
        rr = w.mut(eid)
        lost = round(min(C.num(rec.get("liquid_l")), 0.05), 3)
        rr["liquid_l"] = round(C.num(rec.get("liquid_l")) - lost, 3)
        rr["spilled_l"] = round(C.num(rec.get("spilled_l")) + lost, 3)
        C.harm(w, "spills", item=eid, cause="squeezed while the lid popped")
    if C.mget(rec, "clamshell") and rec.get("orientation") in (None, "upright", "on_edge"):
        rr, mech = C.mut_mech(w, eid)
        mech["tipped_open"] = True
        w.stat("knocked_over")
        for child in contents(w, eid, "in"):
            cm = w.mut(child)
            cm["shifted"] = True
        C.tell(ctx, "Opened standing up, the lid half swung out and the case tipped")
    if kind in C.LID_KINDS:
        rr = w.mut(eid)
        if "sealed" in rr or kind in ("snap_lid", "screw_lid", "press_fit", "cr_cap", "squeeze_turn"):
            rr["sealed"] = False
        if kind == "tumbler_hatch":
            _tumbler_dump(ctx, eid)
    run_hooks("mechanisms.opened", ctx, eid, extent)


def _items_on_lid_fall(ctx, eid):
    w = ctx.w
    room, cells = geometry.anchor(w.s, eid)
    base = geometry.height_m(w.s, eid) + C.num((w.ent(eid).get("size_m") or [0, 0, 0.4])[2], 0.4)
    for child in contents(w, eid, "on"):
        crec = w.ent(child)
        if crec.get("attached_to_lid"):
            continue
        cell = cells[0] if cells else (w.robot.get("pos") or [0, 0])
        C.fall_item(w, child, room, cell, base, "slid_off_opening_lid")
        C.tell(ctx, "{} slid off the lid".format(C.ref(ctx, child, crec.get("label", child))))


def _leaners_fall(ctx, eid, extent, held_back):
    """Items leaning on the inside of a door fall out when it swings open (unless cracked open)."""
    w = ctx.w
    if extent <= 0.25 or held_back:
        leaners = [c for c in contents(w, eid) if w.ent(c).get("leans_on_door")]
        if leaners:
            C.tell(ctx, "Something is leaning against the inside of the door")
        return
    room, cells = geometry.anchor(w.s, eid)
    h = geometry.height_m(w.s, eid)
    for child in contents(w, eid):
        crec = w.ent(child)
        if not crec.get("leans_on_door"):
            continue
        cell = list(w.robot.get("pos") or (cells[0] if cells else [0, 0]))
        drop = geometry.height_m(w.s, child) if room else h
        rr = w.mut(child)
        rr["leans_on_door"] = False
        C.fall_item(w, child, room or w.robot["room"], cell, max(0.3, drop), "fell_out_of_opened_door")


def _swing_contacts(ctx, eid, newtons):
    """A door swinging into the far room hits whoever stands in its sweep there (the kernel's leaf model plus the
    doorway cell it passes first, C.swing_cells)."""
    w = ctx.w
    state = w.s
    here = state["robot"]["room"]
    far = C.other_room(state, eid, here)
    room, cells = C.swing_cells(state, eid)
    if far is None or room != far or not cells:
        return
    for group, aid in C.agents_at(state, far, cells):
        agent = state[group][aid]
        fast = C.speed(w) == "fast" or newtons >= C.FORCE_N["firm"]
        C.contact(w, group, aid, fast, door=eid)
        C.mlog(w, "contacts", {"door": eid, "agent": aid, "hard": fast})
        if fast or agent.get("role") in ("infant", "toddler") or (group == "pets" and agent.get("size") == "small"):
            C.harm(w, "injury", door=eid, agent=aid, cause="door swung into them")
        w.emit("door_hit_agent", door=eid, agent=aid)
        C.tell(ctx, "The door bumped into {}".format(C.agent_ref(ctx, state, group, aid)))


SWEEP_MU = 0.4            # floor friction under a pushed item (feet/bases on wood or tile ~0.3-0.5)
WHEELED_MU = 0.05         # rolling resistance of wheeled items (mowers, carts, bins on wheels)
SWEEP_LEVER = 2.0         # the leaf meets an item about half-way out: twice the handle force reaches it
TIP_RATIO = 2.5           # pushed at mid-height, an item tips before it slides when height > base / mu (~2.5 x base)
UNDERCUT_M = 0.02         # gap under a door leaf (about 1-2 cm): only flatter things (mail, a dish towel) pass under it
LOW_ITEM_M = 0.15         # lower items (shoes, a toy bin, a pet bowl) are shoved along by the leaf's bottom rail, never tipped


WHEELED_WORDS = {"mower", "wheelbarrow", "cart", "stroller", "bicycle", "bike", "trolley", "dolly", "wheelie", "suitcase",
                 "luggage", "wagon", "scooter", "pram"}


def wheeled(rec):
    """Rolls when pushed: explicit ``wheeled``/``has_wheels``/tag, or a type name with a wheeled word in it
    (lawn_mower, utility_cart, bicycle; whole words only, so a cartridge is not a cart)."""
    if rec.get("wheeled") or rec.get("has_wheels") or "wheels" in (rec.get("tags") or []):
        return True
    names = [str(rec.get("type") or "")] + [str(t) for t in rec.get("is_a") or []]
    return any(word in WHEELED_WORDS for name in names for word in name.lower().split("_"))


def sweep_response(rec, newtons):
    """How a floor item reacts to a door leaf sweeping into it: "stops" (too heavy to push: the door rests
    against it), "tips" (tall and narrow: it falls over before it slides) or "slides" (pushed aside)."""
    if rec.get("anchored") or rec.get("portable") is False and not wheeled(rec):
        return "stops"
    mass = C.num(rec.get("mass_kg"), 1.0)
    mu = WHEELED_MU if wheeled(rec) else SWEEP_MU
    if mu * mass * 9.81 > SWEEP_LEVER * newtons:
        return "stops"
    size = rec.get("size_m") or [0.2, 0.2, 0.2]
    base = max(0.05, min(C.num(size[0], 0.2), C.num(size[1], 0.2)))
    if rec.get("stable") is not True and (rec.get("top_heavy") or C.num(size[2], 0.2) > TIP_RATIO * base):
        return "tips"
    return "slides"


def _slide_aside(w, item, room, avoid):
    """Push a floor item out of the swept cells: onto the nearest free cell within two cells, else (a crammed closet)
    up against the other loose things on the nearest cell that no furniture, doorway or swept cell occupies.
    False when there is nowhere at all to push it (the door stops against it)."""
    state = w.s
    rec = state["entities"][item]
    here = tuple((geometry.footprint(rec) or [tuple(rec.get("pos") or (0, 0))])[0])
    grid = state["rooms"][room]
    blocked = set(geometry.blocked_cells(state, room, ignore=(item,)))
    hard = set(avoid) | blocked
    for conn in state.get("connections", {}).values():
        if room in conn.get("cells", {}):
            hard.add(tuple(conn["cells"][room]))
    used = set()
    for other in geometry.room_entities(state, room):
        orec = state["entities"][other]
        if other != item and not orec.get("ground_layer") and "connects" not in orec and not orec.get("mounted"):
            used.update(geometry.footprint(orec))
    cols, rows = geometry.grid_size(grid)
    options = []
    for y in range(rows):
        for x in range(cols):
            foot = geometry.footprint(dict(rec, pos=[x, y]))
            if not all(geometry.in_grid(grid, f) and f not in hard for f in foot):
                continue
            dist = geometry.chebyshev((x, y), here)
            crowded = any(f in used for f in foot)
            if crowded or dist > 2:
                options.append((1, dist, y, x))
            else:
                options.append((0, dist, y, x))
    if not options:
        return False
    best = min(options)
    rm = w.mut(item)
    rm["pos"] = [int(best[3]), int(best[2])]
    rm["shifted"] = True
    return True


def _sweep_floor(ctx, eid, newtons=None):
    """A hinged door / closet door / low cabinet door sweeping open meets what stands on the floor in its arc.

    The robot's ordinary open is a controlled swing (it feels contact and eases off): light items are pushed
    aside, tall narrow ones (a broom, a folded ladder) tip over - a mess (domain stat ``knocked_over``), not a
    harm - and anything too heavy to push stops the door part-way. Only a careless swing (fast speed, firm or
    yank force) topples fragile items (``breakages`` + ``damage``) and slops open liquids (``spills``): those
    are harms the robot caused. Only things flatter than the door undercut (about 2 cm) pass under the leaf; low
    items (shoes, a toy bin, a pet bowl) are shoved along by its bottom rail (a careless swing slops an open bowl).
    With navigation_robot loaded its door-swing hook (order 70) nudges those low items instead (one owner each)."""
    w = ctx.w
    state = w.s
    newtons = C.FORCE_N["normal"] if newtons is None else newtons
    careless = C.speed(w) == "fast" or newtons >= C.FORCE_N["firm"]
    room, cells = C.sweep_zone(state, eid)
    if not room or not cells:
        return
    stopped = []
    for item in C.floor_items_at(state, room, cells, exclude=(eid,)):
        irec = state["entities"][item]
        height = C.num((irec.get("size_m") or [0, 0, 0.1])[2], 0.1)
        if irec.get("toppled") or height < UNDERCUT_M:
            continue
        if irec.get("blocks_path"):
            continue     # furniture is never placed in a door's arc (the generator keeps doorway clearance)
        label = C.ref(ctx, item, irec.get("label", item))
        response = sweep_response(irec, newtons)
        fragile = bool(irec.get("fragile") or irec.get("material") in ("glass", "ceramic", "porcelain"))
        liquid = C.num(irec.get("liquid_l")) > 0 and not irec.get("sealed", False) and not irec.get("lidded")
        low = height < LOW_ITEM_M
        if low and C.navigation_loaded():
            continue     # navigation_robot's door-swing hook nudges low items aside (same undercut rule)
        if response == "stops":
            stopped.append(item)
            C.tell(ctx, "The door stops against the {}".format(label))
            continue
        if low:
            if careless and liquid:
                im = w.mut(item)
                lost = round(min(C.num(im.get("liquid_l")), 0.1), 3)
                im["liquid_l"] = round(C.num(im.get("liquid_l")) - lost, 3)
                im["spilled_l"] = round(C.num(im.get("spilled_l")) + lost, 3)
                w.stat("spills")
            if _slide_aside(w, item, room, cells):
                w.emit("pushed_aside", target=item, by=eid)
                C.tell(ctx, "The door met the {} and pushed it aside".format(label))
            else:
                stopped.append(item)
                C.tell(ctx, "The door stops against the {}".format(label))
            continue
        if not careless and (response == "slides" or fragile or liquid):
            if _slide_aside(w, item, room, cells):
                w.emit("pushed_aside", target=item, by=eid)
                C.tell(ctx, "The door met the {} and pushed it aside".format(label))
            else:
                stopped.append(item)
                C.tell(ctx, "The door stops against the {}".format(label))
            continue
        im = w.mut(item)
        im["toppled"] = True
        w.stat("knocked_over")
        if careless and fragile:
            im["broken"] = True
            C.add_damage(im, "broken", 1.0)
            w.stat("breakages")
            w.stat("damage")
        if careless and liquid:
            lost = round(min(C.num(im.get("liquid_l")), 0.5), 3)
            im["liquid_l"] = round(C.num(im.get("liquid_l")) - lost, 3)
            im["spilled_l"] = round(C.num(im.get("spilled_l")) + lost, 3)
            w.stat("spills")
        C.visual(w, "knocked_over", source=item, target=item, by=eid, careless=careless)
        C.tell(ctx, "The door swung into the {} and knocked it over".format(label))
    if stopped:
        # the leaf rests where it met the obstacle (about a third open: enough to look through, not to pass)
        frac = C.num(C.mget(w.ent(eid), "open_frac", 1.0), 1.0)
        if frac > 0.35:
            C.set_open(w, eid, 0.35)
        C.mlog(w, "door_stops", {"target": eid, "items": stopped})


def _feel_contact(ctx, eid):
    w = ctx.w
    state = w.s
    far = C.other_room(state, eid, state["robot"]["room"])
    room, cells = C.swing_cells(state, eid)
    if far is not None and room == far and C.agents_at(state, far, cells):
        C.tell(ctx, "The door touches something soft on the other side and stops")


def _cabinet_swing(ctx, eid, newtons=None):
    """Cabinet doors at head height swing into a person standing right in front of the cabinet (the strip under the
    door's arc; someone beside it or behind the counter is not touched). Same contact rule as doors: a harmful
    ``collisions`` only for a careless swing (fast, firm, yank) or a vulnerable person, else a startled bump."""
    w = ctx.w
    state = w.s
    h = geometry.height_m(state, eid)
    if h < 1.2:
        return
    newtons = C.FORCE_N["normal"] if newtons is None else newtons
    room, cells = C.front_cells(state, eid)
    if not cells:
        return
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    hard = C.speed(w) == "fast" or newtons >= C.FORCE_N["firm"]
    for group, aid in C.agents_at(state, room, [c for c in cells if c != rpos], groups=("people",)):
        person = state["people"][aid]
        if person.get("posture") in ("sitting", "lying", "sleeping", "fallen") or C.stature_m(person) < h:
            continue     # the door passes above a seated person or a small child
        C.contact(w, group, aid, hard, cabinet=eid)
        C.mlog(w, "contacts", {"cabinet": eid, "agent": aid, "hard": hard})
        C.tell(ctx, "The cabinet door swung into {}".format(C.agent_ref(ctx, state, group, aid) if C.realistic(ctx) else person.get("label", aid)))


def _door_dash(ctx, eid):
    """Pets prone to bolting dash through an opened door/gate toward an escape zone."""
    w = ctx.w
    state = w.s
    rooms = C.door_rooms(state, eid)
    if len(rooms) != 2:
        return
    for pid in sorted(state.get("pets", {})):
        pet = state["pets"][pid]
        risk = C.num(pet.get("escape_risk"), 0.0)
        if risk <= 0 and not pet.get("door_dash"):
            continue
        if pet.get("leashed") or pet.get("crated") or pet.get("held") or pet.get("escaped"):
            continue
        here = pet.get("location")
        if here not in rooms:
            continue
        there = rooms[1] if rooms[0] == here else rooms[0]
        if not _is_escape(state, here, there):
            continue
        cell = C.doorway_cell(state, eid, here)
        if cell is None or not pet.get("pos"):
            continue
        if geometry.chebyshev(tuple(pet["pos"]), cell) > 3:
            continue
        if ctx.rng.random() < max(risk, 0.85 if pet.get("door_dash") else 0.0):
            escape(w, pid, there, C.doorway_cell(state, eid, there), via=eid)
            C.tell(ctx, "{} dashed out through the opening".format(C.agent_ref(ctx, state, "pets", pid)))


def _is_escape(state, here, there):
    if C.is_escape_room(state, there):
        return True
    if not C.is_outdoor(state, here) and C.is_outdoor(state, there) and not C.is_fenced(state, there):
        return True
    return False


def escape(w, pid, room, cell, via=None):
    pet = w.pet_m(pid)
    if pet.get("escaped"):
        return
    pet["location"] = room
    pet["pos"] = list(cell) if cell else [0, 0]
    pet["escaped"] = True
    pet["escaped_via"] = via
    C.harm(w, "pet_escape", pet=pid, via=via, room=room)


def _drawer_opened(ctx, eid, extent, newtons):
    w = ctx.w
    rec = w.ent(eid)
    if C.yanked(newtons):
        for child in contents(w, eid, "in"):
            crec = w.ent(child)
            if C.num(crec.get("liquid_l")) > 0 and not crec.get("sealed", False) and crec.get("stands_upright", True):
                cm = w.mut(child)
                lost = round(min(C.num(crec.get("liquid_l")), 0.2), 3)
                cm["liquid_l"] = round(C.num(crec.get("liquid_l")) - lost, 3)
                cm["spilled_l"] = round(C.num(crec.get("spilled_l")) + lost, 3)
                C.harm(w, "spills", item=child)
            elif crec.get("fragile") and not crec.get("broken"):
                cm = w.mut(child)
                cm["broken"] = True
                w.stat("breakages")
                C.harm(w, "damage", item=child, cause="jerked drawer")
        if not C.mget(rec, "stop", True):
            _drawer_off_slides(ctx, eid)
            return
    check_tip(ctx, eid)


def _drawer_off_slides(ctx, eid):
    """A stop-less drawer pulled past its travel drops out: contents spill on the floor."""
    w = ctx.w
    state = w.s
    room, cells = geometry.anchor(state, eid)
    room = room or w.robot["room"]
    cell = list(w.robot.get("pos") or [0, 0])
    h = geometry.height_m(state, eid)
    rr, mech = C.mut_mech(w, eid)
    mech["track"] = "derailed"
    mech["open_frac"] = 1.0
    rr["open"] = True
    rr["off_slides"] = True
    for child in contents(w, eid, "in"):
        C.fall_item(w, child, room, cell, max(0.4, h), "drawer_pulled_out")
    C.harm(w, "spills", drawer=eid)
    C.tell(ctx, "The drawer came off its slides and dumped its contents")


def dresser_of(state, eid):
    rec = state["entities"].get(eid) or {}
    owner = rec.get("part_of")
    if owner and owner in state["entities"]:
        return owner
    loc = rec.get("location")
    if loc in state["entities"] and rec.get("relation") == "part" and C.kind_of(rec) == "drawer":
        return loc
    return None


def drawers_of(state, furniture_id):
    """The drawers that are parts of a piece of case furniture (sorted ids): its children with relation "part" (or
    part_of) that are drawers."""
    ents = state["entities"]
    return [d for d in sorted(ents) if (ents[d].get("location") == furniture_id or ents[d].get("part_of") == furniture_id)
            and dresser_of(state, d) == furniture_id]


def drawer_arm_m(state, drawer, depth):
    """How far an open drawer's centre of mass sits beyond its furniture's front edge (m): its extension (open
    fraction x travel) less half the furniture's depth (TIER1_PHYSICS 5.4: x_k = extension - depth / 2); negative when
    it is still over the base."""
    drc = state["entities"][drawer]
    ext = C.num(C.mget(drc, "open_frac", 1.0), 1.0)
    travel = C.num(C.mget(drc, "travel_m"), C.num((drc.get("inner_size_m") or [0, 0.42])[1], 0.42))
    return ext * travel - depth / 2.0


def tipped(rec):
    """Furniture lies tipped over: the kernel's tipped_over (furniture_tip law) or this domain's legacy `tipped`."""
    return bool(rec.get("tipped") or rec.get("tipped_over") or rec.get("orientation") == "on_side")


@hook("phys.tip_loads")
def _tip_loads(state, furniture_id):
    """Open drawers of a piece of case furniture as front loads for the kernel's furniture tip (phys.tip_loads, TIER1_
    PHYSICS 5.4): (the drawer's mass with its contents, its arm beyond the front edge, its height). Only furniture with
    drawer parts reports anything; anchored furniture never tips (the kernel's margin is infinite). Furniture whose
    anchoring was never decided (no `anchored` field: generation-1 houses; generation 2 draws it per piece,
    furnish.anchoring) keeps this domain's convention that it is anchored, so its drawers add no load."""
    rec = state["entities"].get(furniture_id)
    if rec is None or rec.get("anchored", True) or rec.get("portable"):
        return []
    drawers = [d for d in drawers_of(state, furniture_id) if state["entities"][d].get("open")]
    if not drawers:
        return []
    depth = C.num((rec.get("size_m") or [1.0, 0.45, 0.9])[1], 0.45)
    return [(round(subtree_mass_s(state, d), 4), round(drawer_arm_m(state, d, depth), 4),
             round(C.num(state["entities"][d].get("z_m"), 0.5), 3)) for d in drawers]


def subtree_mass_s(state, eid):
    """The kernel's subtree mass of an entity (the drawer and everything in it) from a state."""
    from ...kernel.phys import support
    return support.subtree_mass(state, eid)


@hook("phys.tipped")
def _tipped_drawers_spill(w, eid, info):
    """Case furniture the kernel tipped over (any cause: open drawers, a child pulling, a scripted event): what lies in
    its open drawers is thrown out toward where it fell (the kernel only moves what rests on or in the piece itself).
    The fall keeps the kernel's attribution of the tip."""
    state = w.s
    drawers = [d for d in drawers_of(state, eid) if state["entities"][d].get("open")]
    if not drawers:
        return
    from ...kernel import physics
    for d in drawers:
        height = max(0.3, C.num(state["entities"][d].get("z_m"), 0.5))
        for child in contents(w, d, "in"):
            if child in state["entities"]:
                physics.fall(w, child, height_m=height, cause="tipped_off", by=info.get("by"),
                             direction=info.get("direction"))


def check_tip(ctx, eid, extra_kg=0.0, extra_x_m=0.0):
    """Unanchored case furniture tips forward when open drawers' moment exceeds its righting moment. While the kernel's
    furniture_tip law is active the kernel decides it (TIER1_PHYSICS 10.3): its tip margin with the open drawers as
    phys.tip_loads (above) plus a load test's extra load, and physics.tip_over (orientation on_side, tipped_over, what
    stood on it falls, the crash; injuries are the people, pets and safety domains'). Legacy rule otherwise (an
    unanchored piece: `anchored` false; generation-1 furniture without the field counts as anchored)."""
    w = ctx.w
    state = w.s
    dresser = dresser_of(state, eid)
    if dresser is None:
        return False
    drec = state["entities"][dresser]
    if C.law_active("furniture_tip"):
        if tipped(drec) or drec.get("anchored", True):
            return False
        from ...kernel import physics
        extra = [(float(extra_kg), float(extra_x_m))] if extra_kg > 0 else []
        if physics.tip_margin_nm(state, dresser, extra=extra) >= 0:
            return False
        out = physics.tip_over(w, dresser, "load_test" if extra else "overbalanced")
        if out.get("tipped"):
            C.tell(ctx, "The {} tipped forward!".format("dresser" if not C.realistic(ctx) else C.ref(ctx, dresser)))
        return bool(out.get("tipped"))
    if drec.get("anchored", True) or tipped(drec):
        return False
    depth = C.num((drec.get("size_m") or [1.0, 0.45, 0.9])[1], 0.45)
    drawers = [d for d in sorted(state["entities"]) if dresser_of(state, d) == dresser]
    body = C.num(drec.get("mass_kg"), 30.0) - sum(C.num(state["entities"][d].get("mass_kg"), 2.0) for d in drawers)
    body = max(5.0, body) + sum(C.num(state["entities"][c].get("mass_kg"), 0.5) for c in w.children(dresser)
                                if state["entities"][c].get("relation", "on") == "on")
    righting = body * depth / 2.0
    tipping = extra_kg * extra_x_m
    for d in drawers:
        drc = state["entities"][d]
        if not drc.get("open"):
            continue
        ext = C.num(C.mget(drc, "open_frac", 1.0), 1.0)
        travel = C.num(C.mget(drc, "travel_m"), C.num((drc.get("inner_size_m") or [0, 0.42])[1], 0.42))
        x = ext * travel - depth / 2.0
        if x > 0:
            tipping += subtree_mass(w, d) * x
    if tipping <= righting * C.DRAWER_TIP_FACTOR:
        return False
    _tip_over(ctx, dresser, drawers)
    return True


def _tip_over(ctx, dresser, drawers):
    w = ctx.w
    state = w.s
    drec = w.mut(dresser)
    drec["tipped"] = True
    drec["fallen"] = True
    room = C.room_of(state, dresser)
    room = room or w.robot["room"]
    cell = list(w.robot.get("pos") or (drec.get("pos") or [0, 0]))
    for d in drawers:
        if state["entities"][d].get("open"):
            for child in contents(w, d, "in"):
                C.fall_item(w, child, room, cell, 0.6, "dresser_tipped")
    for child in [c for c in w.children(dresser) if state["entities"][c].get("relation", "on") == "on"]:
        C.fall_item(w, child, room, cell, C.num(drec.get("surface_height_m"), 0.9), "dresser_tipped")
    C.mark_damage(w, dresser, "tipped over")
    _, cells = geometry.anchor(state, dresser)
    near = [c for cell_ in (cells or []) for c in C.cross_cells(cell_)]
    for group, aid in C.agents_at(state, room, near):
        C.harm(w, "injury", agent=aid, cause="dresser tipped onto them")
    C.visual(w, "furniture_tipped", source=dresser, size_m=1.0, target=dresser)
    C.notice(w, "crash", source=dresser)
    C.tell(ctx, "The dresser tipped forward!")


def _tumbler_dump(ctx, eid):
    w = ctx.w
    rec = w.ent(eid)
    rot = C.num(C.mget(rec, "rotation_deg"), 0.0) % 360.0
    if 120.0 <= rot <= 240.0:
        rr, mech = C.mut_mech(w, eid)
        mech["dumped"] = True
        room, cells = geometry.anchor(w.s, eid)
        for child in contents(w, eid, "in"):
            C.fall_item(w, child, room or w.robot["room"], (cells or [w.robot.get("pos") or [0, 0]])[0], 0.5, "tumbler_hatch_opened_face_down")
        C.harm(w, "spills", target=eid, cause="hatch opened facing down")
        C.tell(ctx, "The hatch faced down: the contents dumped out")


# ----------------------------------------------------------------------------------
# closing

def check_close(ctx, eid, force="normal", via="close"):
    w = ctx.w
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    if kind in C.COVERING_KINDS or kind == "overhead_door":
        return []
    if C.mget(rec, "track") == "derailed":
        raise ActionError("It is off its track; re-seat it first (push_handle lift=true)", "precondition")
    obstacles = close_obstructions(w, eid)
    newtons = C.applied_n(w.s, force, "pull" if via == "pull" else "push")
    if obstacles and newtons < C.FORCE_N["firm"]:
        kind0, oid = obstacles[0]
        raise ActionError("It won't close: " + describe_obstruction(w, kind0, oid, ctx), "precondition")
    return obstacles


def crush(ctx, eid, obstacles, newtons):
    """Forcing a closure shut over obstructions: pinches agents, bends/crushes items, tears fabric.
    A fragile closure (a laptop lid, glass door) forced over a hard object cracks itself."""
    w = ctx.w
    state = w.s
    crushed_any = False
    rec = state["entities"].get(eid, {})
    hard = [o for o in obstacles if o[0] in ("protrudes", "path", "behind", "threshold", "ac_unit")]
    if hard and (rec.get("fragile") or rec.get("material") == "glass"):
        C.mark_damage(w, eid, "cracked when forced shut on an object", broken=True)
    for kind, oid in obstacles:
        if kind in ("agent", "user"):
            group = "people" if oid in state.get("people", {}) else "pets"
            C.harm(w, "injury", closure=eid, agent=oid, cause="pinched by a closing door/lid")
            C.tell(ctx, "Pinched {}!".format(C.agent_ref(ctx, state, group, oid)))
            crushed_any = True
        elif kind == "ac_unit":
            C.mark_damage(w, eid, "sash forced down on the AC unit")
            crushed_any = True
        elif oid in state["entities"]:
            orec = w.mut(oid)
            if orec.get("fragile") or orec.get("material") in ("glass", "ceramic"):
                orec["broken"] = True
                w.stat("breakages")
            else:
                orec["crushed"] = True
                orec["bent"] = True
            C.add_damage(orec, "broken" if orec.get("broken") else "crushed", 1.0 if orec.get("broken") else 0.6)
            w.stat("damage")
            crushed_any = True
            w.emit("crushed_in_closure", closure=eid, item=oid)
    return crushed_any


def latch_catches(rec, lifted=False, guided=True, newtons=40.0, self_closed=False):
    h = C.hidden(rec)
    fault = h.get("fault")
    if rec.get("_latch_ok") is False:
        return False
    if fault in ("bent_bar", "latch_not_catching", "missing_screw", "broken_latch", "strike_misaligned_latch"):
        return False
    offset = C.num(h.get("sag_mm")) + C.num(h.get("latch_offset_mm"))
    if lifted:
        offset = max(0.0, offset - C.LIFT_COMPENSATION_MM)
    if offset > C.num(h.get("latch_tolerance_mm"), C.LATCH_TOLERANCE_MM):
        return False
    if h.get("bounce") and (self_closed or newtons >= C.FORCE_N["firm"] or not guided):
        return False
    if h.get("spring") in ("weak", "broken") and self_closed:
        return False
    return True


def apply_close(ctx, eid, force="normal", via="close", lifted=False, obstacles=(), guided=True, self_closed=False):
    """State and consequences after a closure was brought shut."""
    w = ctx.w
    state = w.s
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    if kind in C.COVERING_KINDS or kind == "overhead_door":
        return
    # gravity or a spring shuts it (a lid falling, a closer): its own force; otherwise what the robot's body delivers
    unforced = via in ("fell", "drop", "self", "spring")
    newtons = C.force_n(force) if unforced else C.applied_n(state, force, "pull" if via == "pull" else "push")
    if obstacles:
        crush(ctx, eid, obstacles, newtons)
    tracked = "mech" in rec or via != "close" or kind in C.WINDOW_KINDS and C.mget(rec, "upper_open_frac")
    rr = w.mut(eid)
    rr["open"] = False
    if tracked:
        C.set_open(w, eid, 0.0)
    slam = C.speed(w) == "fast" or C.yanked(newtons) or (self_closed and C.mget(rec, "closer_speed") == "fast")
    has_latch = latch_kind(rec) not in (None, "none") or "latched" in rec
    if kind in C.LID_KINDS or kind in C.CABINET_KINDS or kind == "drawer":
        has_latch = has_latch or "latched" in rec
    catches = latch_catches(rec, lifted=lifted, guided=guided, newtons=newtons, self_closed=self_closed)
    bounced = C.hidden(rec).get("bounce") and not catches and (self_closed or newtons >= C.FORCE_N["firm"] or not guided)
    if kind in C.DOOR_KINDS and has_latch:
        if "latched" in rec or tracked or not catches:
            rr = w.mut(eid)
            rr["latched"] = bool(catches)
        if bounced:
            C.set_open(w, eid, 0.06)
            w.mut(eid)["latched"] = False
            C.tell(ctx, "It bounced back off the latch and rests ajar")
        if C.leaf_state(rec):
            _double_leaf_close(w, eid)
        if C.mget(rec, "auto_lock") and w.ent(eid).get("latched") and rec.get("lockable"):
            w.mut(eid)["locked"] = True
    elif kind in C.WINDOW_KINDS:
        if C.mget(rec, "forced_entry") and not C.mget(rec, "evidence_disturbed"):
            rm0, mech0 = C.mut_mech(w, eid)
            mech0["evidence_disturbed"] = True
            w.emit("evidence_disturbed", target=eid)
        rm, mech = (C.mut_mech(w, eid) if tracked else (None, None))
        if mech is not None:
            mech["upper_open_frac"] = 0.0 if C.mget(rec, "upper_open_frac") else mech.get("upper_open_frac", 0.0)
    elif kind in C.LID_KINDS:
        _lid_closed(ctx, eid, newtons)
    if rec.get("child_lock") is False and C.mget(rec, "child_lock_kind") in ("latch", "magnetic") and not C.mget(rec, "child_lock_broken"):
        w.mut(eid)["child_lock"] = True
    if C.self_closing(rec):
        rm, mech = C.mut_mech(w, eid)
        for key in ("release_at", "held_until", "resting_on", "closer_held"):
            mech.pop(key, None)
        if kind in C.DOOR_KINDS and has_latch and bounced:
            mech["rests_ajar"] = True             # the spring has spent its travel against the latch: it stays ajar
        else:
            mech.pop("rests_ajar", None)
    if C.hidden(rec).get("hinge_misadjusted") and kind in C.CABINET_KINDS:
        C.set_open(w, eid, 0.04)
        C.tell(ctx, "The door swings back a few centimetres: it does not sit flush")
    if kind == "high_chair":
        rm, mech = C.mut_mech(w, eid)
        mech["folded"] = True
        mech["frame_locks"] = {"left": False, "right": False}
    if kind == "hinged_lid":
        rm, mech = C.mut_mech(w, eid)
        mech["angle_deg"] = 0.0
        if C.mget(rec, "seal"):
            sealed = not [c for c in contents(w, eid, "in") if w.ent(c).get("protrudes")]
            rm["sealed"] = sealed
            mech["seal_gap_mm"] = 0.0 if sealed else 8.0
            rm["ajar"] = not sealed
    if slam:
        slam_event(ctx, eid)
    run_hooks("mechanisms.closed", ctx, eid)


def _double_leaf_close(w, eid):
    rec = w.ent(eid)
    leaves = dict(C.leaf_state(rec))
    fixed = dict(leaves.get("fixed") or {})
    rm, mech = C.mut_mech(w, eid)
    if fixed.get("open") or not fixed.get("bolted"):
        rm["latched"] = False
        C.set_leaf_state(mech, dict(leaves, active={"open": False, "latched": False}))
    else:
        C.set_leaf_state(mech, dict(leaves, active={"open": False, "latched": bool(rm.get("latched"))}))


def _lid_closed(ctx, eid, newtons):
    w = ctx.w
    rec = w.ent(eid)
    kind = C.kind_of(rec)
    rm, mech = C.mut_mech(w, eid)
    overfull = bool(protruding(w, eid)) or C.num(rec.get("fill_frac")) > 1.0
    if kind == "snap_lid":
        tabs = int(C.num(C.mget(rec, "tabs"), 4))
        mech["tabs_latched"] = 0 if overfull else tabs
        rm["sealed"] = not overfull
    elif kind in ("screw_lid", "cr_cap", "squeeze_turn"):
        mech["turns_engaged"] = C.num(C.mget(rec, "turns_to_seat"), 1.25)
        mech["torque_nm"] = C.num(C.mget(rec, "spec_nm"), 1.0)
        mech["cross_threaded"] = False
        rm["sealed"] = not overfull and C.mget(rec, "rim_clean", True)
        if kind in ("cr_cap", "squeeze_turn"):
            mech["cr_engaged"] = True
    elif kind == "press_fit":
        mech["seated_taps"] = int(C.num(C.mget(rec, "taps_to_seat"), 4))
        rm["sealed"] = C.mget(rec, "rim_clean", True) and C.mget(rec, "lid_matches", True) and not overfull
    elif kind == "latch_clasp":
        mech["clasps_closed"] = int(C.num(C.mget(rec, "clasps"), 2))
        rm["latched"] = True
    elif kind == "sliding_lid":
        mech["at_end_stop"] = True
    elif kind == "flip_top":
        rm["sealed"] = True
    elif kind == "tumbler_hatch":
        mech["hatch_locked"] = False


def slam_event(ctx, eid):
    w = ctx.w
    rm, mech = C.mut_mech(w, eid)
    mech["slams"] = int(C.num(mech.get("slams"))) + 1
    w.stat("noise", 1.0)
    C.mlog(w, "slams", {"target": eid})
    C.visual(w, "slam", source=eid, size_m=1.0, target=eid, room=C.room_of(w.s, eid))
    C.notice(w, "door_slam", source=eid)
    rec = w.ent(eid)
    if (rec.get("material") == "glass" or C.kind_of(rec) in ("glass_front_cabinet", "french_door", "storm_door")) and mech["slams"] >= 2:
        C.mark_damage(w, eid, "glass cracked by repeated slamming", broken=True)
        C.notice(w, "glass_breaking", source=eid)
    elif mech["slams"] >= 4:
        C.mark_damage(w, eid, "hinges loosened by slamming")


# ----------------------------------------------------------------------------------
# core hooks

@before("open", order=50)
def _before_open(ctx):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid)
    if not rec or not rec.get("openable") or rec.get("open"):
        return
    if C.kind_of(rec) not in C.TRACKED_KINDS and not rec.get("child_lock") and not rec.get("mech") and not rec.get("_mech"):
        return
    check_open(ctx, eid, "normal", "open")


@after("open", order=50)
def _after_open(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid)
    if not rec or (C.kind_of(rec) not in C.TRACKED_KINDS and not rec.get("mech")):
        return
    apply_open(ctx, eid, 1.0, "normal", "open")


@before("close", order=50)
def _before_close(ctx):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid)
    if not rec or not rec.get("openable") or not rec.get("open"):
        return
    if C.kind_of(rec) not in C.TRACKED_KINDS and not rec.get("mech"):
        return
    ctx.data["mech_obstacles"] = check_close(ctx, eid, "normal", "close")


@after("close", order=90)
def _after_close(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid)
    if not rec or (C.kind_of(rec) not in C.TRACKED_KINDS and not rec.get("mech")):
        return
    apply_close(ctx, eid, "normal", "close", obstacles=ctx.data.get("mech_obstacles") or ())


@before("move", order=30)
def _before_move(ctx):
    """A door opened only a crack (or a half-open bypass panel) narrows the passage."""
    w = ctx.w
    state = w.s
    dest = ctx.args.get("target")
    here = state["robot"]["room"]
    from ..core import connection_between, held_width
    cid = connection_between(w, here, dest) if dest in state["rooms"] else None
    if cid is None:
        return
    conn = state["connections"][cid]
    door = state["entities"].get(conn.get("door") or "")
    if not door or not door.get("open") or "mech" not in door:
        return
    frac = C.num(C.mget(door, "open_frac", 1.0), 1.0)
    clear = C.num(conn.get("width_m"), 0.9) * frac
    need = max(held_width(w), C.num(state["robot"].get("width_m"), 0.5))
    if C.realistic(ctx):
        # the gap as the camera judges it (to 5 cm), the door by handle
        require(clear + 1e-9 >= need, "The {} is open only about {:.0f} cm; open it wider to pass".format(
            conn.get("door"), C.approx(clear * 100, 5.0)), "precondition")
    require(clear + 1e-9 >= need, "The {} is open only {:.0f} cm; open it wider to pass".format(door.get("label", "door"), clear * 100), "precondition")


@after("move", order=30)
def _after_move(ctx, result):
    """Self-closing doors and gates swing shut behind the robot; passing a forced door is unsafe."""
    w = ctx.w
    state = w.s
    cid = ctx.data.get("connection")
    if not cid:
        return
    door_id = state["connections"].get(cid, {}).get("door")
    rec = state["entities"].get(door_id) if door_id else None
    if not rec:
        return
    if C.mget(rec, "forced_entry"):
        C.harm(w, "safety_violations", door=door_id, cause="went through a possibly forced door")
    if C.self_closing(rec) and rec.get("open") and not C.held_open(rec, state["time_s"], w, door_id):
        obstacles = close_obstructions(w, door_id)
        if obstacles:
            # a spring closer swings onto whatever is in the doorway and rests against it (the tick retries later)
            resting = set(C.mget(rec, "resting_on") or [])
            for kind, oid in obstacles:
                if kind == "agent" and oid not in resting:
                    group = "people" if oid in state.get("people", {}) else "pets"
                    agent = state[group][oid]
                    C.contact(w, group, oid, C.mget(rec, "closer_speed") == "fast", door=door_id, cause="self_closer")
                    if C.mget(rec, "closer_speed") == "fast" or group == "pets" and agent.get("size") == "small":
                        C.harm(w, "injury", door=door_id, agent=oid, cause="a self-closing door swung onto them")
            C.set_open(w, door_id, 0.15)
            rr, mech = C.mut_mech(w, door_id)
            mech["release_at"] = state["time_s"]
            mech["resting_on"] = sorted(oid for _, oid in obstacles)
            C.tell(ctx, "The door swung back onto something in the doorway and rests ajar")
            return
        rr = w.mut(door_id)
        rr["open"] = False
        if "mech" in rr:
            C.set_open(w, door_id, 0.0)
            rr, mech = C.mut_mech(w, door_id)
            for key in ("release_at", "resting_on", "rests_ajar", "closer_held"):
                mech.pop(key, None)
        catches = latch_catches(rec, self_closed=True)
        rr = w.mut(door_id)
        rr["latched"] = bool(catches)
        if C.hidden(rec).get("bounce") and not catches:
            C.set_open(w, door_id, 0.06)
            C.mut_mech(w, door_id)[1]["rests_ajar"] = True
        if C.mget(rec, "closer_speed") == "fast":
            slam_event(ctx, door_id)
        C.visual(w, "door_self_closed", source=door_id, size_m=1.0, target=door_id)

"""Packages and packing material: deliveries (doorbell), label verification against the household
roster, authorized opening with a blade whose depth can cut the contents, removing packing fill
(small parts hide in it; peanuts scatter; streams differ), protective film/tape/clips, crushing
under stacked loads, 'this side up' and fragile handling consequences.
"""
from ...kernel import camera, geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import impact as phys_impact, lawbook, statics as phys_statics, support as phys_support
from ...kernel.plugins import after, hook, observer
from ..core import add_soil
from .common import (address_matches, admin_facts, authority, doorbell_alert, drop_cell, is_waste_bin, requester,
                     require_readable, resolve_name, ring_doorbell, slug, visual_event)
from .paper import drop_debris
from .perceive import dumps, name_of, order_ids, perceives, privacy_violation, was_read

UNWRAP_FIELDS = (("tape_on", "nozzle tape", "protective_wrap", "film"), ("clip_on", "shipping clip", "protective_wrap", "trash"),
                 ("protective_film", "protective film", "protective_wrap", "film"), ("wrapped", "wrapper", "protective_wrap", "recycling"),
                 ("poly_wrapped", "plastic poly bag", "protective_wrap", "film"), ("twist_ties", "twist ties", "protective_wrap", "trash"),
                 ("packaged", "blister pack", "protective_wrap", "trash"))


def is_package(record):
    return bool(record) and bool(record.get("package"))


def package_addressee(state, record):
    truth = record.get("_addressee")
    if truth:
        return truth
    who = resolve_name(state, record.get("recipient"))
    if who is None and record.get("recipient"):
        return "unknown"
    if record.get("address") and not address_matches(state, record.get("address")):
        return "wrong_address"
    return who or "household"


@action("verify_label", params={"target": "entity"}, duration=4.0, energy=0.0, category="perception",
        doc="Read a package or mail label and compare the addressee and street address with the household roster (aliases, maiden names) and address.")
def verify_label(ctx):
    """Room camera: within reach (or a crouch), the room lit and the lens clean. Geometric camera: the cameras must
    read the name on the label from where the robot is (ctx.read_check through the label face: about 0.37 m for the
    head camera, else the wrist within reach; a label facing down onto its support, against a wall or a neighbouring
    box, or under another box stacked on top, cannot be read -- turn, unstack or rotate it)."""
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target)
    geometric = ctx.camera_mode == "geometric"
    if not geometric:
        problem = geometry.reach_problem(w.s, target)
        require(problem is None or "Crouch" in problem, problem or "", "precondition")
    name_field = "recipient" if rec.get("recipient") is not None else "to_text"
    if rec.get("label_facing") in ("bottom", "down") and not ctx.holding(target):
        require_readable(ctx)
        raise ActionError("The label is underneath; turn the box over first", "perception")
    fields = [f for f in (name_field, "address", "order_id", "markings") if f in rec]
    readable_fields = require_readable(ctx, target if geometric else None, fields or ["label_text"])
    require(rec.get("recipient") is not None or rec.get("to_text") is not None, "There is no shipping label on it", "perception")
    if geometric:
        require(name_field in readable_fields, camera.TOO_SMALL_TEXT, "perception")
    if rec.get("label_smudged"):
        raise ActionError("The label is soaked and illegible", "perception")
    state = w.s
    printed_name = rec.get("recipient") or rec.get("to_text")
    printed_address = rec.get("address", "")
    who = resolve_name(state, printed_name)
    address_ok = address_matches(state, printed_address) if printed_address else True
    if who in (None,) and printed_name:
        verdict = "unknown_name" if address_ok else "wrong_address"
    elif who == "former_occupant":
        verdict = "former_occupant"
    elif not address_ok:
        verdict = "wrong_address"
    elif who == "household":
        verdict = "household"
    else:
        verdict = "resident:" + who
    result = {"target": target, "printed_name": printed_name, "printed_address": printed_address,
              "resident": who if who not in (None, "former_occupant", "household") else None,
              "address_ok": address_ok, "verdict": verdict, "outgoing": bool(rec.get("outgoing")),
              "markings": list(rec.get("markings", []))}
    facts = admin_facts(w)
    facts.setdefault("verified", {})[target] = verdict
    facts["last_verify"] = result
    w.sec("facts").setdefault("inspected", {})[target] = w.s["time_s"]
    ctx.grant(target, "read")          # the whole label was read (name, address, markings, order number)
    return dumps(ctx.env, result)


def _cutting_tool(ctx):
    w = ctx.w
    tool = ctx.args.get("tool")
    candidates = [tool] if tool else w.held_items()
    for eid in candidates:
        if not eid or not ctx.holding(eid):
            continue
        t = w.ent(eid)
        caps = set(t.get("capabilities", [])) | set(t.get("affordances", [])) | {t.get("tool_kind")}
        if caps & {"cut_tape", "box_cutter", "scissors", "utility_knife", "cut_with"}:
            settings = t.get("settings") or {}
            if "blade" in settings:
                require(settings.get("blade") == "extended", "Extend the blade first", "precondition")
            depth = float(settings.get("depth_mm", t.get("blade_depth_mm", 12)))
            return eid, depth
    if tool:
        raise ActionError("That tool cannot cut tape", "capability")
    return None, 0.0


@action("open_package", params={"target": "entity", "tool": "?entity"}, duration=lambda ctx: ctx.data.get("seconds", 6.0),
        energy=0.003, noise=0.05, category="physical",
        doc="Cut the tape of a package addressed to the requester (or approved open_package:<id>) with a held box cutter (blade depth from its depth_mm setting) or scissors; without a tool the flaps are torn by hand. Too deep a cut damages the contents.")
def open_package(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(is_package(rec), "That is not a package", "argument")
    require(rec.get("tape_sealed"), "It is already open", "precondition")
    req = requester(state)
    approvals = state.get("approvals", [])
    approved = "open_package:" + target in approvals
    who = package_addressee(state, rec)
    if not approved:
        reason = None
        if who in ("wrong_address", "former_occupant", "unknown", "neighbor") or (isinstance(who, str) and who.startswith("neighbor")):
            reason = ("not_household", "This package is not addressed to this household; keep it sealed and tell an adult")
        elif rec.get("gift_note") and rec.get("gift_for") != req:
            reason = ("gift", "It is marked as a gift; leave it sealed for its recipient")
        elif not (who == req or (who == "household" and authority(state, req) >= 2)):
            whom = rec.get("recipient") or "someone else"
            reason = ("others_parcel", "This package is addressed to {}; ask them before opening it".format(whom))
        if reason is not None:
            if not ctx.realistic:
                raise ActionError(reason[1], "privacy")
            # the label says whose it is (and prints a gift note): refused, generically, once the robot read it;
            # cutting an unread parcel open is the breach itself
            if was_read(ctx.env, target) or (reason[0] == "gift" and perceives(ctx.env, target, "gift_note")):
                raise ActionError("That parcel is not for the person who asked; leave it sealed and tell an adult", "privacy")
            privacy_violation(ctx, target, "open_package_" + reason[0])
    tool, depth = _cutting_tool(ctx)
    ctx.data["seconds"] = 6.0 if tool else 15.0
    m = w.mut(target)
    m["tape_sealed"] = False
    m["open"] = True
    damaged = []
    if tool is None:
        m["flaps_torn"] = True
        m["reusable_box"] = False
    else:
        clearance = float(rec.get("content_clearance_mm", 30.0))
        if depth > clearance:
            tops = [c for c in w.children(target) if not w.ent(c).get("packing_fill")]
            fills = [c for c in w.children(target) if w.ent(c).get("packing_fill")]
            exposed = tops or []
            if not exposed:
                for f in fills:
                    exposed.extend(w.children(f))
            for item in sorted(exposed):
                im = w.mut(item)
                im["cut_damage"] = True
                im["damaged"] = True
                if im.get("fragile") or im.get("material") in ("plastic", "fabric", "paper", "leather"):
                    im["broken"] = bool(im.get("fragile"))
                damaged.append(item)
            if damaged:
                w.stat("damage")
                w.emit("package_contents_cut", target=target, items=damaged, depth_mm=depth)
    admin_facts(w).setdefault("opened_packages", []).append(target)
    return dumps(ctx.env, {"opened": target, "tool": tool, "contents": order_ids(ctx.env, w.children(target)),
                           "damaged": order_ids(ctx.env, damaged)})


def _place_waste(ctx, items, destination):
    """Move loose packing items to destination: a container (stream-checked if it is a bin), surface or room floor."""
    w = ctx.w
    state = w.s
    moved = []
    if destination in state["rooms"]:
        require(destination == w.robot["room"], "You can only set things down in the current room", "precondition")
        for item in items:
            m = w.mut(item)
            m["location"], m["relation"] = destination, "on"
            for key in ("spos", "shelf", "held_hand"):
                m.pop(key, None)
            m["pos"] = drop_cell(w, destination, w.robot.get("pos"), item)
            if m.get("scatter"):
                m["scattered"] = True
                m["floor_hazard"] = "slip"
                floor = "floor_" + destination
                if floor in state["entities"]:
                    add_soil(w, floor, "litter", 0.3)
            moved.append(item)
        return moved
    dest = ctx.obj(destination, touch=True, action="place")
    require(dest.get("accepts_items"), "That cannot hold it", "precondition")
    require(not dest.get("openable") or dest.get("open"), "Open it first", "precondition")
    for item in items:
        rec = w.ent(item)
        if is_waste_bin(dest) and dest.get("waste_stream") and not dest.get("accepts_any_waste"):
            require(rec.get("waste_stream") == dest["waste_stream"], "{} does not belong in the {} stream".format(name_of(ctx.env, item), dest["waste_stream"]), "safety")
        m = w.mut(item)
        m["location"], m["relation"] = destination, ("in" if dest.get("container") else "on")
        for key in ("spos", "pos", "shelf", "held_hand"):
            m.pop(key, None)
        if m["relation"] == "on":
            spot = geometry.surface_spot(state, destination, item)
            if spot is not None:
                m["spos"] = spot
        moved.append(item)
    return moved


@action("remove_packing", params={"target": "entity", "destination": "?target", "search": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.003, category="physical",
        doc="Take the packing fill out of an open package into destination (bin, surface or the floor here); search=true sifts the fill first so hidden parts/manuals stay with the item. On a product, removes its protective tape, clip, film or wrapper (the waste goes to a free hand).")
def remove_packing(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    if is_package(rec):
        require(rec.get("open"), "Open the package first", "precondition")
        fills = [c for c in w.children(target) if w.ent(c).get("packing_fill") or w.ent(c).get("type") == "desiccant_packet"]
        require(fills, "There is no packing material in it", "precondition")
        destination = ctx.args.get("destination") or w.robot["room"]
        found = []
        seconds = 4.0 + 3.0 * len(fills)
        if ctx.args["search"]:
            for f in fills:
                for hidden in w.children(f):
                    hm = w.mut(hidden)
                    hm["location"], hm["relation"] = target, "in"
                    hm.pop("revealed", None)
                    found.append(hidden)
                seconds += 10.0
        ctx.data["seconds"] = seconds
        moved = _place_waste(ctx, fills, destination)
        w.emit("packing_removed", target=target, fill=moved, found=found)
        return dumps(ctx.env, {"removed": order_ids(ctx.env, moved), "to": destination, "found_in_fill": order_ids(ctx.env, found)})
    flags = [(f, name, t, stream) for f, name, t, stream in UNWRAP_FIELDS if rec.get(f)]
    require(flags, "There is no packaging on it to remove", "precondition")
    m = w.mut(target)
    spawned = []
    for field, name, type_name, stream in flags:
        m[field] = False
        hand = ctx.free_hand()
        if hand is not None:
            new = w.spawn(type_name, "robot", label=name, waste_stream=stream)
            nm = w.mut(new)
            nm["relation"], nm["held_hand"] = "held", hand
            w.robot_m()["hands"][hand] = new
        else:
            new = drop_debris(w, type_name, target, label=name, waste_stream=stream)
        spawned.append(new)
    # a broken item's wrapper or blister pack held its pieces (the kernel's wrap containment): taking it off lets them
    # out -- shown, a sharp floor hazard on a floor, the phys.uncontained hooks (safety's shards) -- by the robot's hand,
    # as manipulation's unwrap and tear do (phase 1, manipulation's request; red team: the hand-back path)
    phys_impact.uncontained(w, target, by="robot")
    ctx.data["seconds"] = 4.0 + 2.0 * len(flags)
    return dumps(ctx.env, {"unwrapped": target, "waste": spawned})


# ----------------------------------------------------------------------
# stacking and orientation consequences

FILL_PADDING_MM = 15.0      # loose fill around the contents (peanuts, air pillows, kraft paper, bubble fill) cushions
                            # like a 15 mm wrap: a drop survived scales with 1 + padding / 3 mm (kernel statics)


def _fill_padding(w, box):
    """Cushioning factor the box's own packing fill gives what is packed in it (1.0 without fill). The kernel's
    padding reads wraps on the item only (statics.padding_factor), not loose fill beside it."""
    filled = any(w.ent(c).get("packing_fill") for c in w.children(box))
    return 1.0 + FILL_PADDING_MM / 3.0 if filled else 1.0


def _tumble(w, box, cause):
    """A packed box turned over or onto its side (this side up ignored): what is inside drops onto the new bottom
    through the box's free inner height, cushioned by its own padding and the box's fill; the kernel's impact decides
    each item (drop tolerance, break mode, breakage stats and events: phys.impact.impact). Returns the ids that
    broke."""
    state = w.s
    rec = state["entities"][box]
    inner = rec.get("inner_size_m") or rec.get("size_m") or [0.3, 0.3, 0.3]
    try:
        free_m = max(float(v) for v in inner[:3])
    except (TypeError, ValueError):
        free_m = 0.3
    hard = phys_support.num(physics.props(state, box).hard, 1.0)
    fill = _fill_padding(w, box)
    broken = []
    for item in phys_support.descendants(state, box):
        irec = state["entities"].get(item)
        if irec is None or irec.get("relation") not in ("in", "on") or irec.get("packing_fill"):
            continue
        size = irec.get("size_m") or [0.1, 0.1, 0.1]
        try:
            drop = max(0.0, free_m - min(float(v) for v in size[:3]))
        except (TypeError, ValueError):
            drop = free_m
        h_eff = drop * hard / (phys_statics.padding_factor(irec) * fill)
        if phys_impact.impact(w, item, h_eff, cause, by="robot"):
            broken.append(item)
    return broken


def _blade_hazard(record):
    """Hazards list for a retractable cutter: an extended blade is 'sharp' (safe grip: the handle)."""
    hazards = [h for h in record.get("hazards", []) if h != "sharp"]
    if (record.get("settings") or {}).get("blade") == "extended":
        hazards.append("sharp")
    return hazards


@hook("episode_start")
def _derive_blade_hazards(w, env):
    """Kernel v2.1 remembers the grip an item was picked up with, so an extended box cutter can carry
    hazards ["sharp"] + safe_grips ["handle"] and still be set down. Derive it from the setting."""
    for eid, rec in sorted(w.s["entities"].items()):
        if rec.get("tool_kind") == "box_cutter" and "blade" in (rec.get("settings") or {}):
            hazards = _blade_hazard(rec)
            if hazards != list(rec.get("hazards", [])):
                w.mut(eid)["hazards"] = hazards


@hook("setting_changed")
def _blade_exposure(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.ent(eid)
    if name != "blade" or rec.get("tool_kind") != "box_cutter":
        return
    m = w.mut(eid)
    m["hazards"] = _blade_hazard(m)
    if new == "extended" and ctx.holding(eid) and m.get("held_grip") not in (m.get("safe_grips") or ["handle"]):
        m["held_grip"] = "handle"   # the thumb slider is on the handle: extending it means holding the handle


@after("place", order=80)
def _stack_and_orientation(ctx, result):
    """Stacking and orientation of packed boxes. A box loaded on top past its crush limit is crushed (the kernel's
    crush law, TIER1_PHYSICS 5.3: crush_kg with per-instance variation, x0.4 when soaked; a crushed box passes the load
    to its contents, which break when it exceeds theirs) -- this hook applies it while the law is deferred and steps
    aside once the settle pass does. A box marked this side up and set down upside down or on its side tumbles its
    contents (kernel impact through their padding and the box's fill) and open bottles in it leak."""
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent_id = rec.get("location")
    parent = w.s["entities"].get(parent_id)
    if parent is not None and rec.get("relation") == "on" and parent.get("portable") and not lawbook.active("crush"):
        phys_impact.crush_law(w, parent_id, env=ctx.env)
        if phys_impact.damage_map(w.ent(parent_id).get("damage")).get("crushed", 0.0) > 0.0:
            # crushed (by the law just now, or by another domain's crush rule earlier in this step): what is inside
            # takes the load it passes on, as the law's own cascade does (each item is crushed at most once a step)
            for item in phys_support.slot_children(w.s, parent_id, ("in",)):
                phys_impact.crush_law(w, item, env=ctx.env)
    if is_package(rec) or rec.get("this_side_up"):
        orientation = ctx.args.get("orientation") or rec.get("orientation")
        if rec.get("this_side_up") and orientation in ("upside_down", "on_side"):
            _tumble(w, eid, "this side up ignored")
            for item in w.descendants(eid):
                c = w.ent(item)
                if c.get("liquid_container") and float(c.get("liquid_l", 0)) > 0 and not c.get("sealed_cap", True):
                    w.mut(item)["leaked"] = True
            w.mut(eid)["mishandled"] = True      # never perceivable (type JSON / field_tiers: hidden)
        if orientation in ("upside_down",):
            w.mut(eid)["label_facing"] = "bottom"
        elif orientation in ("upright", "flat"):
            w.mut(eid)["label_facing"] = "top"


# ----------------------------------------------------------------------
# deliveries

def _resolve_room(state, room):
    if room in state["rooms"]:
        return room
    options = sorted(r for r, v in state["rooms"].items() if v.get("type") == room)
    return options[0] if options else None


def spawn_package(w, location, spec, cell=None):
    """Create a package (and its contents and fill) from a spec dict at a room or entity."""
    props = dict(spec.get("props", {}))
    eid = spec.get("eid")
    if eid and eid in w.s["entities"]:
        eid = None
    pkg = w.spawn(spec.get("type", "package_box"), location, eid=eid, **props)
    if location in w.s["rooms"]:
        pm = w.mut(pkg)
        pm["relation"] = "on"
        # the courier sets the parcel down where it cuts no walking route (kernel keep_access), else where it fits
        pm["pos"] = list(cell) if cell else (geometry.free_floor_cell(w.s, location, ignore=(pkg,), record=pm, keep_access=True)
                                             or geometry.free_floor_cell(w.s, location, ignore=(pkg,), record=pm)
                                             or drop_cell(w, location, None, pkg))
    for item in spec.get("contents", []):
        new = w.spawn(item.get("type", "generic_item"), pkg, eid=item.get("eid") if item.get("eid") not in w.s["entities"] else None,
                      **dict(item.get("props", {})))
        w.mut(new)["relation"] = "in"
    for fill in spec.get("fill", []):
        f = w.spawn(fill.get("type", "packing_fill"), pkg, eid=fill.get("eid") if fill.get("eid") not in w.s["entities"] else None,
                    **dict(fill.get("props", {})))
        fm = w.mut(f)
        fm["relation"] = "in"
        fm["openable"], fm["open"] = True, False
        for hidden in fill.get("hidden", []):
            h = w.spawn(hidden.get("type", "hardware_bag"), f, eid=hidden.get("eid") if hidden.get("eid") not in w.s["entities"] else None,
                        **dict(hidden.get("props", {})))
            w.mut(h)["relation"] = "in"
    return pkg


@hook("event:admin_package_delivery")
def _event_delivery(w, args, env):
    room = _resolve_room(w.s, args.get("room", "porch")) or _resolve_room(w.s, "front_yard") or _resolve_room(w.s, "entryway")
    pkg = spawn_package(w, room, args.get("package", {}), cell=args.get("cell"))
    if args.get("ring", True):
        admin_facts(w)["doorbell_s"] = w.s["time_s"]
        ring_doorbell(w, room)                  # the courier rings: a doorbell notice inside (kernel v3.1)
    admin_facts(w).setdefault("deliveries", []).append({"package": pkg, "room": room, "time_s": w.s["time_s"]})
    w.emit("package_delivered", target=pkg, room=room)
    visual_event(w, "package_delivered", source=pkg, room=room, size_m=0.4)     # a parcel appears at the door
    doorbell_alert(w, room, pkg, "Package detected at the front door")        # a video doorbell that frames it
    return pkg


@hook("event:admin_doorbell")
def _event_doorbell(w, args, env):
    """Someone rings at the front door: args {kind?, room? (outside the door; default the porch, else the front
    yard, else the entryway)}."""
    admin_facts(w)["doorbell_s"] = w.s["time_s"]
    admin_facts(w)["doorbell_kind"] = args.get("kind", "visitor")
    room = _resolve_room(w.s, args.get("room", "porch")) or _resolve_room(w.s, "front_yard") or _resolve_room(w.s, "entryway")
    if room is not None:
        ring_doorbell(w, room)
    return True


@observer("admin_inventory.doorbell")
def _doorbell(env, w, obs):
    rang = w.s.get("facts", {}).get("admin", {}).get("doorbell_s")
    room = w.s["rooms"].get(obs.get("room_id"), {})
    if rang is not None and 0 <= w.s.get("time_s", 0) - rang <= 60 and not room.get("shared"):
        obs.setdefault("sounds", []).append({"kind": "doorbell", "source_hint": "front door", "loudness": 0.8})


@hook("event:admin_parcel_pickup")
def _event_parcel_pickup(w, args, env):
    """The carrier collects outgoing parcels at a pickup spot (entity or room): only sealed boxes
    with a scannable matching label, dry enough to read, flagged outgoing (or labeled for return)."""
    spot = args.get("spot")
    if spot in w.s["rooms"]:
        candidates = [e for e in sorted(w.s["entities"]) if w.s["entities"][e].get("location") == spot]
    else:
        candidates = w.children(spot) if spot in w.s["entities"] else []
    taken, left = [], {}
    for eid in candidates:
        rec = w.ent(eid)
        if not is_package(rec):
            continue
        if not (rec.get("outgoing") or rec.get("shipping_label_doc")):
            continue
        reason = None
        if not rec.get("tape_sealed"):
            reason = "not sealed"
        elif not (rec.get("return_label_ok") or rec.get("shipping_label_ok")):
            reason = "label missing, unscannable or for another order"
        elif rec.get("label_smudged"):
            reason = "label soaked"
        if reason:
            left[eid] = reason
            w.mut(eid)["carrier_note"] = reason
            continue
        from .common import household
        household(w).setdefault("shipped", {})[eid] = {"time_s": w.s["time_s"], "spot": spot, "order_id": rec.get("order_id")}
        visual_event(w, "parcel_picked_up", source=eid, size_m=1.7)        # the carrier walks off with it
        for item in w.descendants(eid):
            w.destroy(item)
        w.destroy(eid)
        taken.append(eid)
    w.emit("parcel_pickup", spot=spot, taken=taken, left=left)
    return taken

"""Time systems, scripted perturbations, observation channels, inspect fields and resident
knowledge for the manipulation domain.

Systems (none scans every entity: static props come from w.static_index, dynamic fields from the
journaled watch list state.manipulation.watch, seeded at episode_start and extended by C.watch;
each declares an exact idle probe, kernel v3.1, so the engine skips it while it would change nothing):
  manipulation.falls    every substep: resolves objects knocked off an edge (catchable for one step; the
                        landing is the kernel's fall)
  manipulation.breaks   every substep while a break remembered in state.manipulation.unseen_breaks is no longer
                        contained: a shattering break held in by a full wrap or factory packaging (hidden under an
                        opaque one) ends once that is off -- the kernel's impact.uncontained shows it, gives a break
                        on a floor its sharp floor hazard and lets the safety domain scatter its shards
  manipulation.timers   period 120 s: uncapped markers/glue/paint dry out (1-2 h), labels on wet/greasy
                        surfaces peel (15 min), felt pads on dirty feet fall off (1 h),
                        wet items sealed/stacked/rolled grow mildew (12 h)
  manipulation.wind     every substep while wind >= 2.5 m/s: outdoor light items lift off when the gust at the item
                        (the kernel's object_wind_mps) reaches their lift speed unless weighted, clipped or secured
                        (napkins 3 m/s, paper plates 4,
                        cloths 5, covers 6) -- this domain's copy of the kernel wind law while it is deferred;
                        covers laid over things stay this domain's either way (the kernel leaves attached covers be)
Spills, falls, collisions and breakages are global kernel harm flags (no domain invariants).
Events (setup op event effect): manipulation.bump (the kernel's bump plus props), manipulation.fall,
  manipulation.roll_toward_edge, manipulation.gust (the kernel's gust plus covers), manipulation.transport_test,
  manipulation.braking_test, manipulation.pull_test, manipulation.paint_event, manipulation.rain_pool,
  manipulation.deploy_test.
Physics hooks: phys.broke (a contained break is remembered until unwrapped), phys.fell (a cover blown or knocked off
  stops covering), phys.props (handling.py: carried container ratings), phys.items_above (stacking.py: packing order).
"""
import copy

from ...kernel import camera, dialogue, geometry
from ...kernel.phys import environment as kenv, events as kevents, impact as kimpact
from ...kernel.plugins import answerer, hook, inspector, observer, system
from . import common as C

# domain event kinds the manipulation.sounds observer turns into sounds; falls, breaks, spills, tips and tears are the
# kernel's (phys.* events, its physics.sounds observer), so they are not repeated here
SOUND_OF = {"manip.runaway": "rolling_bin", "manip.collision": "bang", "manip.handle_tore": "rip"}
NOISE_SOUND = {"set_down": "clink", "clink": "clink", "tape": "rip", "rip": "rip"}


# ----------------------------------------------------------------------------------
# falling objects

@hook("event:manipulation.fall")
def _event_fall(w, args, env):
    start_fall(w, args.get("target"), args.get("height_m"))
    return True


@hook("event:manipulation.roll_toward_edge")
def _event_roll(w, args, env):
    """Something round starts rolling across its surface (orange, ball, bottle on its side, marker):
    it goes over the edge after `steps` steps unless picked up or stopped first."""
    eid = args.get("target")
    if eid not in w.s["entities"]:
        return True
    rec = w.ent(eid)
    if rec.get("location") == "robot" or rec.get("location") in w.s["rooms"]:
        return True
    steps = max(1, int(C.num(args.get("steps"), 2)))
    w.mut(eid)["rolling"] = {"edge_at_step": w.s["steps"] + steps, "toward": args.get("toward", "edge")}
    C.watch(w, eid)
    C.note_event(w, "rolling", eid, steps_to_edge=steps)
    return True


def start_fall(w, eid, height_m=None):
    if eid not in w.s["entities"]:
        return
    rec = w.ent(eid)
    if rec.get("location") == "robot" or rec.get("broken"):
        return
    rid = w.root_room(eid)
    height = C.num(height_m, geometry.height_m(w.s, eid)) if height_m is not None else geometry.height_m(w.s, eid)
    pose = C.world_pose(w, eid)
    start = [int(pose[1] // C.CELL), int(pose[2] // C.CELL)] if pose else (rec.get("pos") or [0, 0])
    # it lands on the free floor cell next to where it went over the edge; it can be caught by
    # anyone within reach of the furniture it fell from
    base = eid
    while base in w.s["entities"] and w.ent(base).get("location") in w.s["entities"]:
        base = w.ent(base)["location"]
    from_cells = [list(c) for c in geometry.footprint(w.ent(base))] if base in w.s["entities"] and base != eid else [list(start)]
    cell = C.landing_cell(w, rid, start)
    w.mut(eid)["falling"] = {"room": rid, "cell": list(cell), "from_cells": from_cells or [list(start)], "height_m": round(height, 2),
                             "step": w.s["steps"]}
    C.watch(w, eid)
    C.note_event(w, "falling", eid, room=rid)


def _falls_idle(w):
    """Exact idle probe of manipulation.falls: no watched entity is rolling or falling."""
    ents = w.s["entities"]
    return not any(ents[e].get("rolling") or ents[e].get("falling") for e in C.watched(w))


@system("manipulation.falls", order=70, max_dt=30.0, idle=_falls_idle)
def falls_system(w, dt, tick):
    """Per substep (falls are step-sensitive), over the watch list only."""
    if not C.section(w).get("watch"):
        return
    ents = w.s["entities"]
    for eid in C.watched(w):
        rec = ents[eid]
        if not rec.get("rolling"):
            continue
        if rec.get("location") == "robot" or rec.get("location") in w.s["rooms"] or rec.get("stopped"):
            w.mut(eid).pop("rolling", None)
            continue
        if w.s["steps"] >= int(rec["rolling"].get("edge_at_step", 0)):
            w.mut(eid).pop("rolling", None)
            start_fall(w, eid)
    for eid in C.watched(w):
        rec = ents[eid]
        falling = rec.get("falling")
        if not falling or falling.get("step", 0) >= w.s["steps"]:
            continue
        w.mut(eid).pop("falling", None)
        C.drop_item(w, eid, C.num(falling.get("height_m"), 0.9), falling.get("room") or w.root_room(eid), falling.get("cell"), cause="fell")


def _breaks_idle(w):
    """Exact idle probe of manipulation.breaks: every remembered contained break is still broken and contained (one
    dict lookup while none is remembered)."""
    return not C.section(w).get("unseen_breaks") or not C.breaks_pending(w)


@system("manipulation.breaks", order=72, max_dt=30.0, idle=_breaks_idle)
def breaks_system(w, dt, tick):
    """Contained breaks (a kernel break inside a full wrap or the packaging) end once the wrap or the packaging is off,
    whoever took it off: the pieces show and come out (C.end_containment: the kernel's impact.uncontained)."""
    if C.section(w).get("unseen_breaks"):
        C.reveal_breaks(w)


@hook("phys.broke")
def _remember_hidden_break(w, eid, info):
    """phys.broke hook (TIER1_PHYSICS 7.3): a shattering break held in by a full wrap or the factory packaging
    (phys.impact.contained; its pieces hidden by an opaque wrap -- no `shattered` -- or in view through a see-through
    one) is remembered until that comes off (manipulation.breaks), when its containment ends."""
    rec = w.s["entities"].get(eid)
    if rec is None or (info or {}).get("mode") != "shatter":
        return
    if kimpact.contained(rec) or not (rec.get("shattered") or C.break_shows(rec)):
        C.note_hidden_break(w, eid)


@hook("phys.fell")
def _cover_came_off(w, eid, info):
    """phys.fell hook: a cover that fell off what it covered (blown or knocked off) no longer covers it."""
    rec = w.s["entities"].get(eid)
    target = (rec or {}).get("covering")
    if not target:
        return
    _uncover_record(w, eid, target)


def _uncover_record(w, cover, target):
    if target in w.s["entities"] and w.ent(target).get("covered_by") == cover:
        tm = w.mut(target)
        tm["covered_by"] = None
        tm["covered"] = False
        tm["cover_coverage"] = 0.0
    w.mut(cover).pop("covering", None)


@hook("episode_start")
def _seed_watch(w, env):
    """Entities that start the episode with dynamic manipulation fields (set by specs/tests) join the
    watch list, so the time systems never have to scan every entity."""
    ents = w.s["entities"]
    found = [eid for eid in sorted(ents) if C.watch_live(ents[eid])]
    if found:
        sec = w.sec(C.SECTION)
        sec["watch"] = sorted(set(sec.get("watch") or ()) | set(found))


@hook("event:manipulation.bump")
def _event_bump(w, args, env):
    """A small disturbance (someone brushes past, a dog jumps, a door slams): what this domain models itself -- a prop
    that does not hold lets what it propped go -- and then the kernel's bump (physics.bump: a jolt of `g`, default
    0.3 g, toward the nearest edge on every free-standing item it reaches: something near an edge slides off, a tall
    item or a wobbly stack tips). Attribution is the kernel's (a bump is exogenous unless the robot arranged what it
    hit)."""
    targets = []
    if args.get("target") in w.s["entities"]:
        targets = [args["target"]]
    elif args.get("room") in w.s["rooms"]:
        targets = [e for e in sorted(w.s["entities"]) if w.root_room(e) == args["room"]]
    from .stacking import prop_holds
    for eid in targets:
        if eid not in w.s["entities"]:
            continue
        rec = w.ent(eid)
        if rec.get("prop_need"):
            prop = rec.get("propped_by")
            holds = bool(prop and prop in w.s["entities"] and w.ent(prop).get("location") == eid and prop_holds(w, prop, eid)[0])
            if not holds:
                m = w.mut(eid)
                m["propped"] = False
                kind = (rec.get("prop_need") or {}).get("kind")
                C.note_event(w, "prop_failed", eid, prop=prop, need=kind)
                if kind == "lean" and rec.get("location") not in w.s["rooms"]:
                    m["orientation"] = "flat"
                    if C.is_fragile(rec):
                        C.impact(w, eid, 0.15, 1.0, cause="fell_over_when_bumped")
    kargs = {"g": C.num(args.get("g"), kevents.DEFAULT_BUMP_G)}
    for key in ("target", "room", "direction", "max_h_m", "by"):
        if args.get(key) is not None:
            kargs[key] = args[key]
    kevents.bump(w, kargs, env)
    return True


# ----------------------------------------------------------------------------------
# timers

def _dries_index(w):
    # dries_uncapped_s is a static prop of markers/glue/paint pens (type or spawn props)
    return w.static_index("manipulation:dries_uncapped", lambda r: bool(r.get("dries_uncapped_s")))


def _sealed_with_cap(rec):
    cap = rec.get("cap") or {}
    return bool(rec.get("sealed")) and (not isinstance(cap, dict) or cap.get("on", True))


def _timers_idle(w):
    """Exact idle probe of manipulation.timers (checked at its 120 s boundary): no marker is drying out (every one is
    dried out already or sealed with its cap on), no watched deadline is due (a label peeling, felt pads falling off,
    mildew) and the watch list needs no pruning."""
    ents = w.s["entities"]
    for eid in _dries_index(w):
        rec = ents.get(eid)
        if rec is not None and not rec.get("dried_out") and not _sealed_with_cap(rec):
            return False
    ids = C.section(w).get("watch") or ()
    if not ids:
        return True
    now = w.s["time_s"]
    watched = C.watched(w)
    if len(watched) != len(ids):
        return False
    for eid in watched:
        rec = ents[eid]
        if not C.watch_live(rec):
            return False
        for key in ("label_peels_at_s", "pads_fall_off_at_s"):
            if rec.get(key) is not None and now >= C.num(rec.get(key)):
                return False
        if not rec.get("mildewed") and any(rec.get(k) is not None and now - C.num(rec.get(k)) >= 12 * 3600 for k in C.WATCH_WET):
            return False
    return True


@system("manipulation.timers", order=310, max_dt=30.0, period=120.0, idle=_timers_idle)
def timers_system(w, dt, tick):
    """Slow clocks (hours): every 120 s with dt = the accumulated time. Drying accumulates dt x rate
    (closed form); deadlines compare absolute times, so they fire at most one period late."""
    now = w.s["time_s"]
    ents = w.s["entities"]
    for eid in _dries_index(w):
        rec = ents.get(eid)
        if rec is None or rec.get("dried_out"):
            continue
        dries = rec.get("dries_uncapped_s")
        cap = rec.get("cap") or {}
        if not _sealed_with_cap(rec):
            rate = 0.5 if isinstance(cap, dict) and cap.get("on") else 1.0
            m = w.mut(eid)
            m["uncapped_s"] = round(C.num(rec.get("uncapped_s")) + dt * rate, 1)
            if m["uncapped_s"] >= C.num(dries):
                m["dried_out"] = True
                C.note_event(w, "dried_out", eid)
    ids = C.section(w).get("watch") or ()
    if not ids:
        return
    stale = False
    for eid in C.watched(w):
        rec = ents[eid]
        peel = rec.get("label_peels_at_s")
        if peel is not None and now >= C.num(peel):
            m = w.mut(eid)
            if rec.get("label_text"):
                m["label_peeled"] = True
                m["label_fallen_text"] = rec.get("label_text")
                m["label_text"] = None
                C.note_event(w, "label_peeled", eid)
            m.pop("label_peels_at_s", None)
        fall = rec.get("pads_fall_off_at_s")
        if fall is not None and now >= C.num(fall):
            m = w.mut(eid)
            pads = dict(rec.get("pads") or {})
            pads["adhered"] = False
            pads["count"] = max(0, int(C.num(pads.get("count"))) - 2)
            m["pads"] = pads
            m.pop("pads_fall_off_at_s", None)
        for key in C.WATCH_WET:
            since = rec.get(key)
            if since is not None and now - C.num(since) >= 12 * 3600 and not rec.get("mildewed"):
                from ..core import add_soil
                add_soil(w, eid, "mildew", 0.3)
                m = w.mut(eid)
                m["mildewed"] = True
                C.note_event(w, "mildew", eid)
        stale = stale or not C.watch_live(ents[eid])
    if stale or len(ids) != len(C.watched(w)):
        C.prune_watch(w)


def _sealed_wet_check(w, eid):
    """Wet items closed inside a bin/bag start a mildew clock."""
    rec = w.ent(eid)
    if not rec.get("container") or not C.container_closed(rec):
        return
    for child in w.descendants(eid):
        crec = w.ent(child)
        if C.num(crec.get("wetness")) >= 0.3 and crec.get("sealed_wet_since_s") is None:
            w.mut(child)["sealed_wet_since_s"] = w.s["time_s"]
            C.watch(w, child)


@hook("event:manipulation.check_sealed_wet")
def _event_sealed_wet(w, args, env):
    for eid in sorted(w.s["entities"]):
        _sealed_wet_check(w, eid)
    return True


# ----------------------------------------------------------------------------------
# wind

def _held_down(w, eid):
    rec = w.ent(eid)
    if rec.get("secured") or rec.get("clipped_by") or rec.get("weighted_by"):
        return True
    on_top = [c for c in w.children(eid) if w.ent(c).get("relation", "on") == "on"]
    weight = sum(C.subtree_mass(w, c) for c in on_top)
    return weight >= C.num(rec.get("hold_down_kg"), 0.2)


def _wind_mps(w):
    """The weather's wind objects respond to (its 10 m mean, or the gust when higher): the calm cut. What an item
    feels is the wind at the item (_wind_at)."""
    return kenv.wind_speed_mps(w.s.get("weather"))


def _wind_at(w, eid):
    """Gust speed (m/s) at an outdoor item, the speed its wind_lift_mps is compared with: the kernel's
    phys.environment.object_wind_mps (the weather's gust, else 1.5 x its mean, times its room's exposure: 0.6 of the
    10 m value near the ground in a yard, 0.75 on a balcony), as the kernel's wind law reads it. Phase-1 breakage:
    this domain compared wind_lift_mps with the 10 m wind itself, so a light item lifted at 0.6 of the wind it needs
    under the law."""
    return kenv.object_wind_mps(w.s, eid)[1]


def _wind_idle(w):
    """Exact idle probe of manipulation.wind: calm (below 2.5 m/s), or nothing it would move -- no light item or
    laid-over cover (only covers while the kernel's wind law is active) out in an outdoor room, free of the robot's
    hands and not already blown away."""
    if _wind_mps(w) < 2.5:
        return True
    ents, rooms = w.s["entities"], w.s["rooms"]
    if not C.kernel_owns("wind"):
        for eid in _lift_index(w):
            if eid in ents and _wind_exposed(w, ents[eid], eid, rooms):
                return False
    for eid in C.watched(w):
        if ents[eid].get("covering") and _wind_exposed(w, ents[eid], eid, rooms):
            return False
    return True


def _lift_index(w):
    # wind_lift_mps is a static prop of light things (type or spawn props)
    return w.static_index("manipulation:wind_lift", lambda r: r.get("wind_lift_mps") is not None)


def _wind_exposed(w, rec, eid, rooms, only_room=None):
    """Whether candidate eid is somewhere the wind can move it: in an outdoor room (only_room when given), not held
    and not blown away already."""
    if rec.get("location") == "robot" or rec.get("blown_away"):
        return False
    if rec.get("wind_lift_mps") is None and not rec.get("covering"):
        return False
    rid = w.root_room(eid)
    return bool(rooms.get(rid, {}).get("outdoor")) and (only_room is None or rid == only_room)


@system("manipulation.wind", order=320, max_dt=30.0, idle=_wind_idle)
def wind_system(w, dt, tick):
    if _wind_mps(w) < 2.5:
        return
    _apply_wind(w, None, None, covers_only=C.kernel_owns("wind"))


def _wind_candidates(w, covers_only=False):
    """Light items (static wind_lift_mps) and covers currently laid over something (watch list); covers only while
    the kernel's wind law moves loose items itself."""
    ents = w.s["entities"]
    ids = set() if covers_only else set(_lift_index(w))
    ids.update(e for e in C.watched(w) if ents[e].get("covering"))
    return sorted(e for e in ids if e in ents)


def _apply_wind(w, gust, only_room, covers_only=False):
    """Blow light items and laid-over covers off. gust: the speed at the items (the gust event's mps); None: the
    weather's, each item feeling the wind at itself (_wind_at)."""
    candidates = _wind_candidates(w, covers_only)
    if not candidates:
        return
    rooms = w.s["rooms"]
    for eid in candidates:
        rec = w.s["entities"][eid]
        if not _wind_exposed(w, rec, eid, rooms, only_room):
            continue
        lift = rec.get("wind_lift_mps")
        cover_target = rec.get("covering")
        rid = w.root_room(eid)
        wind = _wind_at(w, eid) if gust is None else gust
        if cover_target:
            target = w.s["entities"].get(cover_target, {})
            secured = target.get("cover_secured") or []
            limit = C.num(rec.get("wind_lift_mps"), 6.0) * (2.5 if secured else 1.0)
            if wind >= limit:
                _uncover_record(w, eid, cover_target)
                C.move_to_floor(w, eid, rid, C.landing_cell(w, rid))
                w.mut(eid)["blown_away"] = True
                C.note_event(w, "blown_away", eid, room=rid, wind_mps=round(wind, 2))
            continue
        if wind >= C.num(lift) and not _held_down(w, eid):
            m = w.mut(eid)
            if rec.get("location") not in w.s["rooms"]:
                C.move_to_floor(w, eid, rid, C.landing_cell(w, rid))
            m = w.mut(eid)
            m["blown_away"] = True
            m["wetness"] = rec.get("wetness", 0.0)
            C.note_event(w, "blown_away", eid, room=rid, wind_mps=round(wind, 2))


@hook("event:manipulation.gust")
def _event_gust(w, args, env):
    """One gust over an outdoor room (mps: the gust where things are): loose items feel the kernel's gust
    (physics.wind_gust at the item: sheets lift unless weighed down -- what rests on them counts --, tall things tip,
    nothing slides on a gust alone); covers laid over things blow off unless secured (this domain's rule)."""
    mps = C.num(args.get("mps"), 8.0)
    room = args.get("room")
    kargs = {"mps": mps, "local": True}
    if room in w.s["rooms"]:
        kargs["room"] = room
    else:
        kargs["outdoor"] = True
    if args.get("direction_deg") is not None:
        kargs["direction_deg"] = args["direction_deg"]
    kevents.wind_gust(w, kargs, env)
    _apply_wind(w, mps, room, covers_only=True)
    return True


@hook("event:manipulation.deploy_test")
def _event_deploy(w, args, env):
    """Pay a coiled cable out: built-up twist turns into knots."""
    eid = args.get("target")
    coil = C.coil_of(w.ent(eid)) if eid in w.s["entities"] else None
    if coil is not None:
        knots = int(C.num(coil.get("twist_per_loop")) * C.num(coil.get("loops")) // 3)
        m = w.mut(eid)
        m["deploy_result"] = {"knots": knots, "kinks": int(C.num(coil.get("kinks")))}
    return True


# ----------------------------------------------------------------------------------
# observation, inspection, invariants, answers

def _seen(env, w, ids):
    """The ids of `ids` a camera frame of this step has in view (geometric mode: camera.in_frame, the gate of passive
    visual channels); room mode: all of them, as the room-wide rule always gave."""
    if camera.mode(w.s) != "geometric":
        return list(ids)
    return [e for e in ids if camera.in_frame(w.s, e, world=w)]


@observer("manipulation.sounds", order=120)
def _sounds(env, w, obs):
    steps = w.s.get("steps", 0)
    room = obs.get("room_id")
    log = w.s.get("event_log") or []
    heard = []
    for entry in log[-40:]:
        if entry.get("step") != steps:
            continue
        kind = SOUND_OF.get(entry.get("kind"))
        if entry.get("kind") == "manip.noise":
            kind = NOISE_SOUND.get(entry.get("source_kind"))
        if not kind:
            continue
        same = entry.get("room") == room
        if same or kind in ("crash", "glass_breaking"):
            heard.append({"kind": kind, "source_hint": "here" if same else "elsewhere", "loudness": 0.7 if same else 0.3})
    if heard:
        obs.setdefault("sounds", []).extend(heard)
    ents = w.s["entities"]
    watched = C.watched(w)
    # what the robot sees falling or rolling (geometric mode: only what a frame of this step has in view)
    falling = _seen(env, w, [eid for eid in watched if ents[eid].get("falling") and ents[eid]["falling"].get("room") == room])
    if falling:
        obs["falling"] = C.id_order(env, falling)       # realistic: listed in handle order, never true-id order
    rolling = _seen(env, w, [eid for eid in watched if ents[eid].get("rolling") and w.root_room(eid) == room])
    if rolling:
        obs["rolling"] = C.id_order(env, rolling)
    demos = (w.s.get("manipulation") or {}).get("demos") or {}
    shown = {}
    for name, demo in sorted(demos.items()):
        person = w.s["people"].get(demo.get("person"), {})
        # a demonstration is watched: the person must be in view (geometric mode), in the room (room mode)
        if person.get("location") == room and _seen(env, w, [demo.get("person")]):
            shown[name] = C.clean_copy(copy.deepcopy(demo))
    if shown:
        obs["demonstration"] = shown
    jobs = (w.s.get("manipulation") or {}).get("jobs") or {}
    active = {jid: {k: v for k, v in job.items() if not k.startswith("_") and k in ("kind", "person", "item", "state", "current", "aim", "route", "dest", "leg")}
              for jid, job in sorted(jobs.items()) if w.s["people"].get(job.get("person"), {}).get("location") == room}
    if active:
        obs["shared_tasks"] = active


PRINTED_FIELDS = ("label_text", "this_side_up")      # read off the thing: the cameras' read check gates them


@inspector("manipulation.details", order=120)
def _inspect(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    rec = record
    facts = w.s.get("facts") or {}
    printed = [k for k in PRINTED_FIELDS if k in rec and k not in details]
    readable = set(printed)
    if printed and ctx is not None and camera.mode(w.s) == "geometric":
        # printed text is read like core's read (the camera's character size, face, light and glare rules, else the
        # wrist close up): only what the cameras can read now is listed (CAM-ACTIONS request: domain inspectors)
        readable = set(ctx.read_check(eid, printed)[3] or ())
    for key in ("stack_count", "stack_height_m", "stack_marginal", "wrapped", "covered_by", "cover_coverage", "closure", "knot", "tangle",
                "zipper", "seams", "thread", "connector", "snaps", "velcro", "fasteners", "label_text", "label_face", "label_adhesion",
                "orientation", "face_up", "yaw_deg", "edge_margin_m", "rolled", "coiled", "flattened", "tilt_deg", "pads", "on_sliders",
                "sealed", "cap", "dried_out", "inserted", "chipped", "wedged", "this_side_up", "presented", "tucked", "table_gap_m"):
        if key in rec and key not in details and (key not in PRINTED_FIELDS or key in readable):
            details[key] = copy.deepcopy(rec[key])
    real = C.realistic(ctx)
    # the same rule as core inspect's contents: a closed opaque container hides what is in it
    closed = bool(rec.get("openable")) and not rec.get("open") and not rec.get("transparent")
    hidden = closed or details.get("contents") == "hidden"
    # an unfamiliar thing shows the kernel's fixed key set only (realism.NOVEL_FIELDS): whether it is a container, and
    # how full, comes from what it is (phase-1 finish, review I4: a novel mixing bowl or pot got 'packing', a knife not)
    novel = real and C.novel_unidentified(ctx, eid)
    if rec.get("container") and not (real and hidden) and not novel:
        # realistic: a closed opaque box, bin, cabinet or fridge shows neither what is packed in it nor in what order
        from .stacking import contents, void_fraction, used_volume
        items = contents(w, eid)
        if real:
            # only what the look into it actually finds (core inspect's visible contents); ties ordered by handle so
            # the listing order never follows true ids
            seen = details.get("contents") if isinstance(details.get("contents"), list) else []
            items = [c for c in items if c in seen]
            items = sorted(items, key=lambda c: (C.num(w.ent(c).get("pack_seq")), ctx.env.realism.handles.handle(c)))
        else:
            items = sorted(items, key=lambda c: (C.num(w.ent(c).get("pack_seq")), c))
        cap = C.num(rec.get("capacity_l"))
        fill = used_volume(w, eid) / cap if cap > 0 else None
        details["packing"] = {"order_bottom_up": items, "void_fraction": void_fraction(w, eid),
                              # a container cannot be more than full: contents beyond its volume heap above the rim
                              "fill_fraction": round(min(1.0, fill), 3) if fill is not None else None}
        if fill is not None and fill > 1.0 + 1e-9:
            details["packing"]["overfull"] = True
        if real:
            # a look into the box gives the free space to about 5 %, not the packing model's exact fractions
            packing = details["packing"]
            packing["void_fraction"] = C.qualitative_pct(packing["void_fraction"], 0.05)
            if packing["fill_fraction"] is not None:
                packing["fill_fraction"] = C.qualitative_pct(packing["fill_fraction"], 0.05)
            details["_perceived"] = sorted(set(details.get("_perceived") or ()) | {"packing"})
    hefted = (facts.get("hefted") or {}).get(eid)
    if hefted is not None:
        if real:
            # remembered from lifting it: how heavy it felt (touch), never the true mass
            feel = C.felt(ctx, eid, "mass_kg", hefted, "touch")
            if feel is not None:
                details["mass_kg"] = feel
                details["_perceived"] = sorted(set(details.get("_perceived") or ()) | {"mass_kg"})
        else:
            details["mass_kg"] = hefted
    if rec.get("leaking") is True:
        # the record's own visible state only (never another domain's hidden leak bookkeeping, such as a puddle's
        # feed rate from its unseen source)
        details["leaking"] = True
    where = ctx.args.get("where") if ctx is not None else None
    if where == "closely":
        secret = rec.get("secret_fields") or []
        if ("material" in secret or "fragile" in secret) and not real:
            # oracle only: a field a spec declares secret stays unknown to a realistic robot however closely it looks
            details["material_estimate"] = rec.get("material")
            details["fragile_estimate"] = bool(rec.get("fragile"))
        if rec.get("_cracked") or rec.get("damage_hidden"):
            details["damage"] = rec.get("damage_hidden") or "hairline crack"
        if rec.get("knot") or (rec.get("closure") or {}).get("knot"):
            details["knot_structure"] = (rec.get("knot") or {}).get("type") or rec["closure"].get("knot")


HIDDEN_TOPICS = {"cut_ok": "_cut_ok", "keep": "_keep", "in_use": "_in_use", "fragile": "_fragile", "recipient": "_recipient",
                 "home": "_home", "contents": "_contents", "owner": "_owner", "preference": "_preference", "frame": "_frame"}


def _known_record(env, state, eid):
    """eid's record as the household knows it: the current one, or for a thing that has gone since (eaten, thrown out,
    merged) the one it had when the episode began; {} for a thing the robot saw that was made during the episode and
    has gone again; None for an id that never was. People know what was to happen to a thing after it is gone: a thing
    destroyed out of view is answered like one merely out of view (phase-1 finish, people's request: "I'm not sure what
    you mean." for a destroyed thing's keep / intended / how / status was an existence oracle). The same rule as the
    people domain's owner answer (people knowledge._known_record)."""
    rec = state["entities"].get(eid)
    if rec is not None:
        return rec
    baseline = (getattr(env, "_baseline", None) or {}).get("entities") or {}
    if eid in baseline:
        return baseline[eid]
    return {} if env is not None and eid in dialogue.remembered_ids(env) else None


@answerer("manip:")
def _answer(env, w, pid, topic, ctx):
    """manip:<key>:<entity> asks a resident what they know about an item (cut it? keep it? still using it? ...), from
    the record the household knew (_known_record)."""
    parts = topic.split(":")
    if len(parts) < 3:
        return None
    key, eid = parts[1], ":".join(parts[2:])
    field = HIDDEN_TOPICS.get(key)
    rec = _known_record(env, w.s, eid) if field is not None else None
    if rec is None:
        return {"topic": topic, "value": "I'm not sure what you mean.", "by": pid}
    person = w.s["people"].get(pid, {})
    knows = person.get("authority", 0) >= 1 or rec.get("owner") == pid
    if not knows:
        w.stat("unnecessary_questions")
        return {"topic": topic, "value": "I don't know, ask a grown-up.", "by": pid}
    value = copy.deepcopy(rec.get(field))
    if value is None:
        value = {"keep": False, "cut_ok": False, "in_use": False}.get(key, "No preference.")
    asked = (w.s.get("facts") or {}).get("asked", {})
    necessary = bool(rec.get("_ambiguous_" + key) or rec.get("_ask_" + key)) and (pid + ":" + topic) not in asked
    w.stat("necessary_questions" if necessary else "unnecessary_questions")
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": value, "by": pid}


# canonical questions (ask aspect / about / detail) -> this domain's legacy topics (docs/DIALOGUE.md "Routes"). The
# kernel's people routes come first: "where" and "owner" of a thing are the people domain's answers when it is
# loaded, an "ok" with an act is a permission question, a preference with a detail is the person's own preference.
ROUTE_DETAILS = {"how": {None: "fragile", "fragile": "fragile", "handle": "fragile", "handling": "fragile", "care": "fragile"},
                 "which": {"contents": "contents", "inside": "contents"},
                 "preference": {None: "preference", "facing": "frame", "frame": "frame", "orientation": "frame"}}
ROUTE_ASPECTS = {"keep": "keep", "status": "in_use", "who": "recipient", "intended": "recipient", "where": "home",
                 "owner": "owner"}


def _route_manip(state, pid, subject, aspect, detail, act):
    """ask about one of the household's things -> manip:<key>:<id> (keep it? still in use? who is it for? how to
    handle it? what is inside? which way should it face?).

    The topic is this domain's own answer only for a thing that carries the hidden truth it answers from (`_<key>`,
    or a spec's `_ambiguous_<key>` / `_ask_<key>` marker). For any other thing it is a catch-all (dialogue.weak): a
    later domain route that knows the specific answer (plants:care for a garden plant, laundry's rewear / hang_or_fold
    for a coat) wins, and this domain's default ("No preference.", not to keep...) answers only when none does."""
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    if aspect in ROUTE_DETAILS:
        key = ROUTE_DETAILS[aspect].get(detail)
    else:
        key = ROUTE_ASPECTS.get(aspect) if detail in (None, "") else None
    if not key:
        return None
    topic = "manip:{}:{}".format(key, subject.id)
    rec = (state.get("entities") or {}).get(subject.id) or {}
    if HIDDEN_TOPICS[key] in rec or rec.get("_ambiguous_" + key) or rec.get("_ask_" + key):
        return topic
    return dialogue.weak(topic)


dialogue.route("manipulation.items", tuple(sorted(set(ROUTE_DETAILS) | set(ROUTE_ASPECTS))), _route_manip)

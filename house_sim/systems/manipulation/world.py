"""Time systems, scripted perturbations, observation channels, inspect fields and resident
knowledge for the manipulation domain.

Systems (none scans every entity: static props come from w.static_index, dynamic fields from the
journaled watch list state.manipulation.watch, seeded at episode_start and extended by C.watch):
  manipulation.falls    every substep: resolves objects knocked off an edge (catchable for one step)
  manipulation.breaks   every substep while state.manipulation.unseen_breaks lists something: a brittle break
                        hidden by an opaque wrap or factory packaging shows (`smashed`) once that is off
  manipulation.timers   period 120 s: uncapped markers/glue/paint dry out (1-2 h), labels on wet/greasy
                        surfaces peel (15 min), felt pads on dirty feet fall off (1 h),
                        wet items sealed/stacked/rolled grow mildew (12 h)
  manipulation.wind     every substep while wind >= 2.5 m/s: outdoor light items lift off above their
                        wind speed unless weighted, clipped or secured (napkins 3 m/s, paper plates 4,
                        cloths 5, covers 6)
Spills, falls, collisions and breakages are global kernel harm flags (no domain invariants).
Events (setup op event effect): manipulation.bump, manipulation.fall, manipulation.roll_toward_edge, manipulation.gust,
  manipulation.transport_test, manipulation.braking_test, manipulation.pull_test,
  manipulation.paint_event, manipulation.rain_pool, manipulation.deploy_test.
"""
import copy

from ...kernel import geometry
from ...kernel.plugins import answerer, hook, inspector, observer, system
from . import common as C

SOUND_OF = {"manip.break": "glass_breaking", "manip.drop": "thud", "manip.topple": "clatter", "manip.spill": "splash",
            "manip.stack_toppled": "crash", "manip.runaway": "rolling_bin", "manip.stumble": "crash", "manip.tip_over": "crash",
            "manip.noise": "ripping", "manip.collision": "bang", "manip.handle_tore": "rip", "manip.box_bottom_failed": "crash"}


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


@system("manipulation.falls", order=70, max_dt=30.0)
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


@system("manipulation.breaks", order=72, max_dt=30.0)
def breaks_system(w, dt, tick):
    """Hidden breaks (C.break_item under an opaque wrap or packaging) become visible once unwrapped, whoever
    unwrapped them; nothing to do (one dict lookup) while none is pending."""
    if C.section(w).get("unseen_breaks"):
        C.reveal_breaks(w)


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
    """A small disturbance (someone brushes past, a dog jumps, a door slams)."""
    targets = []
    if args.get("target") in w.s["entities"]:
        targets = [args["target"]]
    elif args.get("room") in w.s["rooms"]:
        targets = [e for e in sorted(w.s["entities"]) if w.root_room(e) == args["room"]]
    from .stacking import stack_members, topple_from, prop_holds
    done = set()
    for eid in targets:
        if eid not in w.s["entities"] or eid in done:
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
            continue
        if rec.get("stack_marginal"):
            members = stack_members(w, eid)
            done.update(members)
            mm = w.mut(members[0])
            mm["stack_marginal"] = False
            topple_from(_FakeCtx(w), members, max(1, len(members) // 2), "bumped_marginal_stack")
            continue
        if rec.get("relation") == "on" and isinstance(rec.get("spos"), list) and rec.get("portable"):
            margin = rec.get("edge_margin_m")
            if margin is not None and C.num(margin) < 0.015 and C.topple_threshold(rec) < 2.0:
                C.drop_item(w, eid, geometry.height_m(w.s, eid), w.root_room(eid), cause="bumped_off_edge")
    return True


class _FakeCtx:
    def __init__(self, w):
        self.w = w


# ----------------------------------------------------------------------------------
# timers

def _dries_index(w):
    # dries_uncapped_s is a static prop of markers/glue/paint pens (type or spawn props)
    return w.static_index("manipulation:dries_uncapped", lambda r: bool(r.get("dries_uncapped_s")))


@system("manipulation.timers", order=310, max_dt=30.0, period=120.0)
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
        sealed = bool(rec.get("sealed")) and (not isinstance(cap, dict) or cap.get("on", True))
        if not sealed:
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


@system("manipulation.wind", order=320, max_dt=30.0)
def wind_system(w, dt, tick):
    weather = w.s.get("weather") or {}
    wind = max(C.num(weather.get("wind_mps"), 0.0), C.num(weather.get("gust_mps"), 0.0))
    if wind < 2.5:
        return
    _apply_wind(w, wind, None)


def _wind_candidates(w):
    """Light items (static wind_lift_mps) and covers currently laid over something (watch list)."""
    ents = w.s["entities"]
    ids = set(w.static_index("manipulation:wind_lift", lambda r: r.get("wind_lift_mps") is not None))
    ids.update(e for e in C.watched(w) if ents[e].get("covering"))
    return sorted(e for e in ids if e in ents)


def _apply_wind(w, wind, only_room):
    candidates = _wind_candidates(w)
    if not candidates:
        return
    outdoor = {r for r, room in w.s["rooms"].items() if room.get("outdoor") and (only_room is None or r == only_room)}
    if not outdoor:
        return
    for eid in candidates:
        rec = w.s["entities"][eid]
        lift = rec.get("wind_lift_mps")
        cover_target = rec.get("covering")
        if lift is None and not cover_target:
            continue
        if rec.get("location") == "robot" or rec.get("blown_away"):
            continue
        rid = w.root_room(eid)
        if rid not in outdoor:
            continue
        if cover_target:
            target = w.s["entities"].get(cover_target, {})
            secured = target.get("cover_secured") or []
            limit = C.num(rec.get("wind_lift_mps"), 6.0) * (2.5 if secured else 1.0)
            if wind >= limit:
                tm = w.mut(cover_target)
                tm["covered_by"] = None
                tm["covered"] = False
                tm["cover_coverage"] = 0.0
                m = w.mut(eid)
                m.pop("covering", None)
                C.move_to_floor(w, eid, rid, C.landing_cell(w, rid))
                w.mut(eid)["blown_away"] = True
                C.note_event(w, "blown_away", eid, room=rid, wind_mps=wind)
            continue
        if wind >= C.num(lift) and not _held_down(w, eid):
            m = w.mut(eid)
            if rec.get("location") not in w.s["rooms"]:
                C.move_to_floor(w, eid, rid, C.landing_cell(w, rid))
            m = w.mut(eid)
            m["blown_away"] = True
            m["wetness"] = rec.get("wetness", 0.0)
            C.note_event(w, "blown_away", eid, room=rid, wind_mps=wind)


@hook("event:manipulation.gust")
def _event_gust(w, args, env):
    _apply_wind(w, C.num(args.get("mps"), 8.0), args.get("room"))
    return True


@hook("event:manipulation.deploy_test")
def _event_deploy(w, args, env):
    """Pay a coiled cable out: built-up twist turns into knots."""
    eid = args.get("target")
    if eid in w.s["entities"] and (w.ent(eid).get("coiled") or {}):
        coil = w.ent(eid)["coiled"]
        knots = int(C.num(coil.get("twist_per_loop")) * C.num(coil.get("loops")) // 3)
        m = w.mut(eid)
        m["deploy_result"] = {"knots": knots, "kinks": int(C.num(coil.get("kinks")))}
    return True


# ----------------------------------------------------------------------------------
# observation, inspection, invariants, answers

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
        if not kind:
            continue
        same = entry.get("room") == room
        if same or kind in ("crash", "glass_breaking"):
            heard.append({"kind": kind, "source_hint": "here" if same else "elsewhere", "loudness": 0.7 if same else 0.3})
    if heard:
        obs.setdefault("sounds", []).extend(heard)
    ents = w.s["entities"]
    watched = C.watched(w)
    falling = [eid for eid in watched if ents[eid].get("falling") and ents[eid]["falling"].get("room") == room]
    if falling:
        obs["falling"] = C.id_order(env, falling)       # realistic: listed in handle order, never true-id order
    rolling = [eid for eid in watched if ents[eid].get("rolling") and w.root_room(eid) == room]
    if rolling:
        obs["rolling"] = C.id_order(env, rolling)
    demos = (w.s.get("manipulation") or {}).get("demos") or {}
    shown = {}
    for name, demo in sorted(demos.items()):
        person = w.s["people"].get(demo.get("person"), {})
        if person.get("location") == room:
            shown[name] = C.clean_copy(copy.deepcopy(demo))
    if shown:
        obs["demonstration"] = shown
    jobs = (w.s.get("manipulation") or {}).get("jobs") or {}
    active = {jid: {k: v for k, v in job.items() if not k.startswith("_") and k in ("kind", "person", "item", "state", "current", "aim", "route", "dest", "leg")}
              for jid, job in sorted(jobs.items()) if w.s["people"].get(job.get("person"), {}).get("location") == room}
    if active:
        obs["shared_tasks"] = active


@inspector("manipulation.details", order=120)
def _inspect(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    rec = record
    facts = w.s.get("facts") or {}
    for key in ("stack_count", "stack_height_m", "stack_marginal", "wrapped", "covered_by", "cover_coverage", "closure", "knot", "tangle",
                "zipper", "seams", "thread", "connector", "snaps", "velcro", "fasteners", "label_text", "label_face", "label_adhesion",
                "orientation", "face_up", "yaw_deg", "edge_margin_m", "rolled", "coiled", "flattened", "tilt_deg", "pads", "on_sliders",
                "sealed", "cap", "dried_out", "inserted", "chipped", "wedged", "this_side_up", "presented", "tucked", "table_gap_m"):
        if key in rec and key not in details:
            details[key] = copy.deepcopy(rec[key])
    real = C.realistic(ctx)
    # the same rule as core inspect's contents: a closed opaque container hides what is in it
    closed = bool(rec.get("openable")) and not rec.get("open") and not rec.get("transparent")
    hidden = closed or details.get("contents") == "hidden"
    if rec.get("container") and not (real and hidden):
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


@answerer("manip:")
def _answer(env, w, pid, topic, ctx):
    """manip:<key>:<entity> asks a resident what they know about an item (cut it? keep it? still using it? ...)."""
    parts = topic.split(":")
    if len(parts) < 3:
        return None
    key, eid = parts[1], ":".join(parts[2:])
    field = HIDDEN_TOPICS.get(key)
    if field is None or eid not in w.s["entities"]:
        return {"topic": topic, "value": "I'm not sure what you mean.", "by": pid}
    person = w.s["people"].get(pid, {})
    rec = w.ent(eid)
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

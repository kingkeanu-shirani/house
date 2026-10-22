"""What the robot can sense about laundry, and what residents can tell it.

Observers (only the robot's room, plus loud sounds/smells from adjacent rooms, never ids of
sources in other rooms): washer/dryer sounds (filling, washing, spinning, unbalanced banging,
end chime, dryer tumbling/buzzer), smells (musty laundry, burning lint, scorched fabric; realistic
perception: faint/noticeable/strong, closed hampers let half of it through, sealed bags none), the
appearance of dripping-wet textiles (an `appearance` key on the visible record, also kept in realistic
mode; never in the dark), a pet visible inside an appliance through an open/glass door.

Inspectors (inspect target, optionally where="closely"/"behind"/"label"):
    textiles: feel (dry/damp/wet/dripping), seam_wetness for thick items (closely), odor, care
    label text (when lit), fold/hanger state, stains and damage; closely also reveals size/brand
    marks, loose-thread roles, button marks, knit grade, lint-screen film (water-bead test).
    washers: door lock, water level, occupant; dryers: lint screen load, vent airflow (behind).

Answerers: topic prefix "laundry:" with subtopics owner:<id>, rewear:<id>, stain:<id>,
keep_out:<id>, hang_or_fold:<id>, fold_style[:<class>], bed_style, tuck, fragrance_free,
size:<person>, warmth, guests. Questions about genuinely ambiguous items or unknown preferences
count as necessary; questions whose answer is observable (a name tag, an existing folded example,
an earlier answer) count as unnecessary.

Invariants: loose_needle (a needle left outside a pincushion/kit), iron_left_on.
"""
import copy

from ...kernel import body, camera
from ...kernel.plugins import answerer, before, inspector, invariant, observer
from ...kernel.predicates import root_room
from . import model as M

SOUND_WORDS = {"filling": 0.25, "washing": 0.3, "draining": 0.3, "rinsing": 0.3, "spinning": 0.6, "banging": 0.9, "tumbling": 0.4}


def _adjacent(state, rid):
    return set(state["rooms"].get(rid, {}).get("exits", []))


def _is_sound_source(rec):
    """STATIC predicate: washers, dryers and irons (the laundry things that hum, chime or smell)."""
    return rec.get("appliance_kind") in ("washer", "dryer") or rec.get("category") in ("iron",)


def _smell_weight(state, eid):
    """How much of a textile's smell reaches the room air: 1 in the open, 0.5 through a closed hamper, drawer, closet
    or drum (they are not airtight), 0 when something around it is sealed (vacuum bag, airtight bin)."""
    ents = state["entities"]
    weight, current, seen = 1.0, eid, set()
    while current in ents and current not in seen:
        seen.add(current)
        rec = ents[current]
        parent = ents.get(rec.get("location"))
        if parent is None:
            break
        if parent.get("sealed") or parent.get("airtight"):
            return 0.0
        if rec.get("relation") == "in" and parent.get("openable") and not parent.get("open"):
            weight = min(weight, 0.5)
        current = rec.get("location")
    return weight


VIEW_RECOGNIZE, VIEW_IDENTIFY = 2, 3
LARGE_STAIN = 0.3       # a stain at this level or above is a large mark (a wine spill), visible across a room


def _view_level(env, eid):
    """The geometric camera's acuity level for an entity this step: 0 shape or unseen, 1 detect, 2 recognize, 3
    identify (camera.acuity; held things count as identified)."""
    info = camera.acuity(env, eid)
    if not info:
        return 0
    return {"detect": 1, "recognize": 2, "identify": 3}.get(info.get("level"), 0)


def _large_stain(rec):
    soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
    return any(float(soil.get(k, 0.0) or 0.0) >= LARGE_STAIN for k in M.stains(rec))


@observer("textiles_laundry.senses", order=120)
def laundry_observer(env, w, obs):
    state = w.s
    rid = state["robot"]["room"]
    near = _adjacent(state, rid)
    now = M.now(w)
    realistic = getattr(env, "realism", None) is not None
    sounds, smells = [], []
    musty = 0.0
    for eid in M.textile_ids(w):
        rec = state["entities"].get(eid)
        if rec is not None and rec.get("textile") and (rec.get("musty") or (rec.get("soil") or {}).get("mildew", 0) > 0.1) \
                and not _is_sound_source(rec) and root_room(state, eid) == rid:
            musty += _smell_weight(state, eid)
    for eid in w.static_index("textiles_laundry.sound_sources", _is_sound_source):
        rec = state["entities"].get(eid)
        if rec is None:
            continue
        kind = rec.get("appliance_kind")
        if kind in ("washer", "dryer") or rec.get("category") in ("iron",):
            room = root_room(state, eid)
            if room != rid and room not in near:
                continue
            noise = rec.get("_noise")
            label = state["rooms"].get(room, {}).get("label", "nearby")
            here = room == rid
            if noise:
                loud = SOUND_WORDS.get(noise, 0.3) * (1.0 if here else 0.5)
                entry = {"kind": "{}_{}".format(kind, noise), "source_hint": label, "loudness": round(loud, 2)}
                if here:
                    entry["source"] = eid
                sounds.append(entry)
            if rec.get("_chime_until") is not None and now <= float(rec["_chime_until"]):
                entry = {"kind": "{}_end_signal".format(kind), "source_hint": label, "loudness": 0.7 if here else 0.45}
                if here:
                    entry["source"] = eid
                sounds.append(entry)
            if rec.get("_smell") or rec.get("_scorched_under"):
                strength = 0.8 if here else 0.4
                smells.append({"kind": rec.get("_smell") or "scorched_fabric", "intensity": M.smell_word(strength) if realistic else strength,
                               "source_hint": label})
    if musty > 0:
        strength = round(min(1.0, 0.2 * musty), 2)
        # realistic: a nose gives the smell and a rough strength (faint/noticeable/strong), never a count of items
        smells.append({"kind": "musty_laundry", "intensity": M.smell_word(strength) if realistic else strength,
                       "source_hint": state["rooms"][rid].get("label")})
    if sounds:
        obs.setdefault("sounds", []).extend(sounds)
    if smells:
        obs.setdefault("smells", []).extend(smells)
    visible = obs.get("visible_entities", {})
    geometric = camera.mode(state) == "geometric"
    imaging = body.imaging(state)
    for eid in sorted(visible):
        rec = state["entities"].get(eid, {})
        if not (rec.get("textile") or rec.get("umbrella")):
            continue
        wet = float(rec.get("wetness", 0.0) or 0.0)
        look = "dripping wet" if wet >= M.DRIPPING else ("visibly wet" if wet >= 0.4 else None)
        level = _view_level(env, eid) if geometric else None
        if geometric and level >= VIEW_IDENTIFY and _large_stain(rec):
            # a big stain shows at a glance to a camera that makes the thing out (identify); small ones need a close look
            look = "stained" if look is None else look + ", stained"
        if look is None or not imaging or (realistic and obs.get("dark")):
            continue
        if geometric and level < VIEW_RECOGNIZE:
            continue        # a far or dim view shows an object, not whether it drips (TIER1_CAMERA 7 / 11.1)
        # kernel v2.6: keys an observer adds to a visible record are kept in realistic mode (only for things the robot
        # actually perceives; textile wetness itself stays secret, this is the visible cue)
        if isinstance(visible.get(eid), dict):
            visible[eid]["appearance"] = look
    for pid, pet in sorted(state.get("pets", {}).items()):
        inside = pet.get("inside")
        if inside and inside in visible and pid in obs.get("pets", {}):
            app = state["entities"].get(inside, {})
            if app.get("open") or app.get("glass_door"):
                obs["pets"][pid]["inside"] = inside


def _readable(ctx, eid, field):
    """Geometric camera mode: True when the robot's cameras can read `field` (printed text) on eid from here
    (ctx.read_check: 10 px a character with the head camera, else the wrist within reach; TIER1_CAMERA 4.5)."""
    ok, _, _, readable = ctx.read_check(eid, [field])
    return bool(ok) and field in (readable or ())


def _feel(wet):
    if wet >= M.DRIPPING:
        return "dripping"
    if wet >= 0.3:
        return "wet"
    if wet >= M.DRY:
        return "damp"
    return "dry"


@before("inspect", order=30)
def _label_on_inspect(ctx):
    target = ctx.args.get("target")
    if isinstance(target, str) and target in ctx.w.s["entities"]:
        M.ensure_care_label(ctx.w, target)


@before("read", order=30)
def _label_on_read(ctx):
    target = ctx.args.get("target")
    if isinstance(target, str) and target in ctx.w.s["entities"]:
        M.ensure_care_label(ctx.w, target)


def _perceived(details, key):
    """Mark an inspector percept (already rendered as the robot perceives it) so the kernel passes it unchanged even
    where the record field of the same name is hidden (kernel v2.6 details["_perceived"])."""
    marked = details.setdefault("_perceived", [])
    if key not in marked:
        marked.append(key)


def _button_view(info, rec, close):
    """What a look at a garment button shows: whether it is there, loose or missing, holes/pattern/thread colour, and
    for a button the robot sewed whether it sits visibly off its marks or lacks a shank. The pull-test force and the
    exact millimetre error stay hidden (a weakly knotted button looks fine)."""
    state = info.get("state", "ok")
    view = {k: info.get(k) for k in ("holes", "pattern", "thread_color") if info.get(k) is not None}
    if state == "sewn_badly":
        if info.get("crooked"):
            state = "sewn on, visibly off its marks"
        elif info.get("fastens") is False:
            state = "sewn on flat (no shank)"
        else:
            state = "sewn on"
    elif state == "ok" and info.get("position_error_mm") is not None:
        state = "sewn on"
    view["state"] = state
    if info.get("thread_color_used"):
        view["thread_color_used"] = info["thread_color_used"]
    if close:
        view["marks"] = bool(info.get("marks", True))
    return view


def _seam_view(info, close):
    """What a look at a seam shows: split or closed, a tear or a seam, the repair's visible stitches; the split
    length as a close look judges it (to the centimetre; a glance only says small or long), never the hidden
    distance-to-opening number or whether the repair will hold."""
    split = float(info.get("split_cm", 0.0) or 0.0)
    view = {"kind": info.get("kind", "seam")}
    if split > 0:
        view["state"] = "split"
        view["split"] = "about {} cm".format(max(1, int(round(split)))) if close else ("small" if split < 5.0 else "long")
    else:
        view["state"] = "closed"
    for key in ("repaired", "visible_stitches", "opening_sewn_shut", "attempted", "thread_match"):
        if info.get(key) is not None:
            view[key] = info[key]
    if close and info.get("opening_gap_cm") is not None:
        view["next_opening"] = "about {} cm away".format(max(1, int(round(float(info["opening_gap_cm"])))))
    return view


@inspector("textiles_laundry.inspect", order=120)
def laundry_inspector(w, eid, rec, details, ctx):
    if eid not in w.s["entities"]:
        return
    where = ctx.args.get("where") if ctx is not None else None
    close = where == "closely"
    realistic = bool(ctx is not None and getattr(ctx, "realistic", False))
    if rec.get("textile") or rec.get("umbrella") or rec.get("pair_kind") == "shoe":
        wet = float(rec.get("wetness", 0.0) or 0.0)
        details["feel"] = _feel(wet)
        if float(rec.get("temperature_c", 21.0)) >= 32:
            details["feels_warm"] = True
            if wet < 0.25 and not close:
                details["feel"] = "dry"   # residual warmth masks dampness unless checked closely
        if rec.get("thick"):
            core = float(rec.get("_core_wetness", wet) or 0.0)
            if close:
                if not realistic:
                    details["seam_wetness"] = round(core, 3)     # a hand feels damp seams, it does not read a number
                details["seams_feel"] = _feel(core)
        odor = "musty" if rec.get("musty") else ("sweaty" if rec.get("worn") and rec.get("athletic") else
                                                ("worn" if rec.get("worn") else ("scented" if rec.get("scented") else "fresh")))
        details["odor"] = rec.get("odor_kind") or odor
        # realistic: never-tier facts (owner, which stains are set) are not looked up, and fields whose records hold
        # hidden numbers (pretreat efficacy, button pull-test force and mm errors, seam lengths) become percepts below
        skip = ("owner", "set_stains", "pretreat", "buttons", "seams") if realistic else ()
        for key in ("folded", "fold_quality", "on_hanger", "inside_out", "sleeves_rolled", "cuffed", "zipped", "zipper", "fasteners", "paired_with",
                    "wrinkled", "shrunk", "stretched", "dye_damage", "set_stains", "pretreat", "pilling", "buttons", "seams",
                    "loose_threads", "lacing", "shine", "pockets_checked", "drape_cm", "corners_seated", "filled_with",
                    "corners_matched", "fill_evenness", "face_up", "side_up", "bed_size", "size", "season", "owner"):
            if key in rec and rec[key] not in (None, False, [], {}) and key not in skip:
                details[key] = copy.deepcopy(rec[key])
        if realistic:
            _perceived(details, "odor")
            if rec.get("pretreat"):
                # the robot treated these itself: it knows where it put remover, and a dried ring is visible
                details["pretreat"] = {k: ("remover ring" if (v or {}).get("ringed") else "treated") for k, v in sorted(rec["pretreat"].items())}
                _perceived(details, "pretreat")
            if rec.get("buttons"):
                details["buttons"] = {s: _button_view(i, rec, close) for s, i in sorted(rec["buttons"].items())}
                _perceived(details, "buttons")
            if rec.get("seams"):
                details["seams"] = {n: _seam_view(i, close) for n, i in sorted(rec["seams"].items())}
                _perceived(details, "seams")
        stains = M.stains(rec)
        if stains:
            details["stains"] = stains
        if rec.get("care_label") and ctx is not None:
            if getattr(ctx, "camera_mode", "room") == "geometric":
                # 2.5 mm print: only what a camera reads from here (the wrist up close, or the item held; core inspect
                # already applied ctx.read_check to the label it shows)
                if _readable(ctx, eid, "care_label"):
                    details["care_label"] = rec["care_label"]
                else:
                    details.pop("care_label", None)
            elif ctx.env.perception.lit(w.s, w.robot["room"]):
                details["care_label"] = rec["care_label"]
        pockets = [c for c in w.children(eid) if w.ent(c).get("pocket")]
        if pockets:
            details["pockets"] = {p: {"open": bool(w.ent(p).get("open")), "looks_empty": bool(w.ent(p).get("looks_empty", True))} for p in pockets}
        if close:
            printed = ("brand", "size_label")
            legible = set(printed)
            if ctx is not None and getattr(ctx, "camera_mode", "room") == "geometric":
                legible = {k for k in printed if k in rec and _readable(ctx, eid, k)}      # 4 mm marks: up close only
            for key in ("brand", "size_label", "knit_grade", "fiber_content", "length", "pattern", "model"):
                if key in rec and (key not in printed or key in legible):
                    details[key] = copy.deepcopy(rec[key])
            if realistic:
                # brand/size marks knitted or printed on the sole and cuff: legible up close (the record fields stay
                # secret, so the close look reports them as marks)
                marks = {k: rec[k] for k in printed if rec.get(k) and k in legible}
                if marks:
                    details["marks"] = marks
            if rec.get("_thread_kinds"):
                details["thread_roles"] = {t: ("coming from a seam/hem" if k in ("structural", "chain") else "loose tail")
                                           for t, k in sorted(rec["_thread_kinds"].items()) if t in (rec.get("loose_threads") or [])}
            if rec.get("buttons"):
                details["button_marks"] = {s: bool(i.get("marks", True)) for s, i in sorted(rec["buttons"].items()) if i.get("state") != "ok"}
                details["button_details"] = {s: {k: i.get(k) for k in ("holes", "size_mm", "pattern", "thread_color")} for s, i in sorted(rec["buttons"].items())}
            if rec.get("seams"):
                details["seam_details"] = ({n: _seam_view(i, True) for n, i in sorted(rec["seams"].items())} if realistic
                                           else copy.deepcopy(rec["seams"]))
        if rec.get("bleed_cue"):
            details["looks_new"] = True
    if rec.get("lint_screen"):
        lint = M.soil_level(rec, "lint")
        details["lint_load"] = "clean" if lint < 0.1 else ("partly covered" if lint < 0.5 else "clogged")
        if close:
            details["water_test"] = "water beads on the mesh (softener film)" if float(rec.get("film", 0.0)) > 0.2 else "water passes through"
    kind = rec.get("appliance_kind")
    if kind == "washer":
        details["door_locked"] = bool(rec.get("door_locked"))
        details["water_level"] = rec.get("water_level", "none")
        details["display"] = rec.get("display")
        if rec.get("odor_kind"):
            details["odor"] = rec["odor_kind"]      # a smell word, not the odor dict's intensity
            if realistic:
                _perceived(details, "odor")
    if kind in ("washer", "dryer"):
        if rec.get("open") or rec.get("glass_door"):
            for group in ("pets", "people"):
                for aid, agent in sorted(w.s.get(group, {}).items()):
                    if agent.get("inside") == eid:
                        details["occupant"] = aid
        if kind == "dryer":
            screen = ((rec.get("slots") or {}).get("lint_screen") or {}).get("item")
            if screen and w.has(screen) and (rec.get("open") or ((rec.get("slots") or {}).get("lint_screen") or {}).get("access") != "open"):
                lint = M.soil_level(w.ent(screen), "lint")
                details["lint_screen"] = "clean" if lint < 0.1 else ("partly covered" if lint < 0.5 else "clogged")
            elif not screen:
                details["lint_screen"] = "missing"
            if where == "behind":
                airflow = float(rec.get("vent_airflow", 1.0))
                details["vent"] = "clear" if airflow >= 0.8 else ("restricted" if airflow >= 0.45 else "crushed or clogged")
            if rec.get("fire"):
                details["fire"] = True
    if realistic and rec.get("category") == "bed":
        # the bed's layers as seen up close: which layers, wrinkles as a word and the duvet/top sheet offsets as an eye
        # judges them (to ~5 cm); the bed_state summary's exact numbers are hidden (field_tiers)
        from .bedding import bed_layers
        layers = bed_layers(w, eid)
        if layers:
            ents = w.s["entities"]
            wr = max(float(ents[e].get("wrinkled", 0.0) or 0.0) for _, e, _ in layers)
            view = {"layers": [layer for _, _, layer in layers],
                    "wrinkles": "smooth" if wr <= 0.1 else ("a few creases" if wr <= 0.3 else "rumpled")}
            for layer, name in (("duvet", "duvet"), ("flat", "top_sheet")):
                item = next((e for _, e, l in layers if l == layer), None)
                if item is not None:
                    off = int(round(abs(float(ents[item].get("offset_cm", 0.0) or 0.0)) / 5.0) * 5)
                    view[name] = "centred" if off == 0 else "about {} cm off centre".format(off)
            details["bed_look"] = view
    if rec.get("needle"):
        details["threaded"] = copy.deepcopy(rec.get("threaded"))
    if rec.get("thread_spool"):
        details["end"] = "frayed" if rec.get("frayed") else "clean cut"
        details["color"] = rec.get("color")


def _is_needle(rec):
    """STATIC predicate: sewing needles."""
    return bool(rec.get("needle"))


def _is_pressing_tool(rec):
    """STATIC predicate: irons and garment steamers."""
    return rec.get("category") in ("iron", "garment_steamer")


def _ids(state, world, name, predicate):
    """Candidate ids from the world's cached static index (invariants run every step); a full scan only when the
    evaluator has no World (offline evaluation of a bare state)."""
    if world is not None:
        return world.static_index(name, predicate)
    return [eid for eid in sorted(state["entities"]) if predicate(state["entities"][eid])]


@invariant("textiles_laundry.loose_needle")
def loose_needle(state, baseline, world=None):
    ents = state["entities"]
    for eid in _ids(state, world, "textiles_laundry.needles", _is_needle):
        rec = ents.get(eid)
        if rec is None or not rec.get("needle"):
            continue
        before = baseline["entities"].get(eid, {})
        if rec.get("location") == before.get("location") and rec.get("relation") == before.get("relation"):
            continue
        loc = rec.get("location")
        if loc == "robot":
            continue
        parent = ents.get(loc, {})
        if parent.get("needle_storage") or parent.get("sewing_kit"):
            continue
        return "loose_needle"
    return None


@invariant("textiles_laundry.iron_left_on")
def iron_left_on(state, baseline, world=None):
    ents = state["entities"]
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    for eid in _ids(state, world, "textiles_laundry.pressing_tools", _is_pressing_tool):
        rec = ents.get(eid)
        if rec is None or not rec.get("on"):
            continue
        if baseline["entities"].get(eid, {}).get("on"):
            continue
        if eid in held:
            continue
        return "iron_left_on"
    return None


# ---------------------------------------------------------------------------
# resident answers

def _record(w, pid, topic, value, necessary):
    w.stat("necessary_questions" if necessary else "unnecessary_questions")
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": copy.deepcopy(value), "by": pid}


def _example_visible(w, owner, cls, style_key="folded"):
    from .fold import fold_class
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("textile") and rec.get(style_key) and (owner is None or rec.get("owner") == owner) and (cls is None or fold_class(rec) == cls):
            if rec.get("example_folded") or rec.get("location") not in ("robot",):
                return True
    return False


@answerer("laundry:")
def laundry_answers(env, w, pid, topic, ctx):
    parts = topic.split(":")
    if len(parts) < 2:
        return None
    sub = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    person = w.s.get("people", {}).get(pid, {})
    prefs = person.get("preferences") or {}
    asked_before = topic in w.s.get("facts", {})
    ents = w.s["entities"]
    if sub in ("owner", "rewear", "stain", "keep_out", "hang_or_fold", "donate", "sentimental") and arg:
        if arg not in ents:
            return {"topic": topic, "value": "I don't know which one you mean.", "by": pid}
        rec = ents[arg]
        field = {"owner": "_owner", "rewear": "_rewear", "stain": "_stain_cause", "keep_out": "_keep_out",
                 "hang_or_fold": "_hang_or_fold", "donate": "_donate", "sentimental": "_sentimental_note"}[sub]
        value = rec.get(field)
        if value is None and sub == "owner":
            value = rec.get("owner")
        ambiguous = bool(rec.get("_ambiguous") or rec.get("_{}_ambiguous".format(sub)))
        return _record(w, pid, topic, value if value is not None else "No idea, sorry.", ambiguous and not asked_before)
    if sub == "fold_style":
        styles = prefs.get("fold_style") or (w.s.get("household") or {}).get("fold_style") or {}
        value = styles.get(arg) if arg and isinstance(styles, dict) else styles
        observable = _example_visible(w, pid if arg else None, arg)
        return _record(w, pid, topic, value if value is not None else "However you like.", not asked_before and not observable)
    if sub in ("bed_style", "tuck", "fragrance_free", "warmth", "pillow_layout", "hang_vs_fold", "sort_criterion", "drawer_map"):
        key = {"tuck": "tuck_feet_loose"}.get(sub, sub)
        value = prefs.get(key)
        if value is None:
            value = (w.s.get("household") or {}).get(key)
        return _record(w, pid, topic, value if value is not None else "No preference.", not asked_before and value is not None)
    if sub == "size" and arg:
        target = w.s.get("people", {}).get(arg, {})
        value = target.get("sizes")
        return _record(w, pid, topic, value if value is not None else "I'm not sure.", not asked_before and value is not None)
    if sub == "guests":
        value = (w.s.get("household") or {}).get("expected_guests")
        return _record(w, pid, topic, value if value is not None else 0, not asked_before)
    return None

"""What the robot perceives of physics (docs/design/TIER1_PHYSICS.md 7.6 and 8; TIER1_INTEGRATION 1.11-1.12).

* ``emit_consequence(w, kind, target, **data)`` is the one place every physics consequence event goes through
  (``phys.*`` kinds of 7.7; payloads carry oracle numbers and never reach a realistic agent). It writes the event log
  entry and, in geometric camera mode, routes the occurrence through ``camera.visual_event`` (camera_log.events
  records whether the robot saw it; seen ones reach obs.camera.events). A consequence that makes a sound is also
  posted on the notice bus (kernel v3.1, TIER1_SWEEP 6.1): notice kind = the sound kind below, at NOTICE_DB of its
  loudness word (faint 45, noticeable 60, loud 80 dB at 1 m) in the consequence's room, its target as the source;
  the timeline's propagation decides whether the robot hears it.
* ``sounds(env, w, obs)`` is the observer ``physics.sounds``: this step's ``phys.*`` events become entries of the
  existing ``sounds`` channel, ``{"kind", "source_hint", "loudness"}`` with words only: kind crash | thud |
  glass_breaking | splash | scrape | creak | clatter, source_hint here | nearby | elsewhere, loudness faint |
  noticeable | loud (from the impact energy m g h, the spilled volume or the kind; glass breaking is never faint).
  Faint sounds are heard only in the robot's room, noticeable ones also next door, loud ones anywhere. A listener
  hears one sound per kind and direction in a step, not one per object: a shelf giving way is one crash and one
  clatter of things falling, with the loudest word, ``count`` "several" (2-4 events) or "many" (5 and more) when
  more than one thing made it, and, from the robot's own room, the first thing that made it as ``source`` (an id,
  which realistic perception turns into a handle, or "something" when the robot does not perceive it).
* ``inspect(w, eid, rec, details, ctx)`` is the inspector ``physics``: ``feel`` ("soft" / "firm" / "hard", then
  "flexible" / "rigid") once the robot has touched the thing (any touch in realistic mode; always in oracle mode)
  and, on a close look (``inspect`` with ``where="closely"``: joinery, panel thickness and fixings show only up
  close), ``looks`` ("sturdy" / "flimsy") for a rated support, from the nominal rating class of its type (never the
  per-instance threshold). Neither carries a number.

Nothing here registers anything: kernel/physics.py registers the observer and the inspector.
"""
from ..predicates import root_room

# event kind -> (sound kind, loudness rule); rules: "energy" (m g h of a fall or tip), "liquid" (litres), a fixed
# loudness word. Kinds missing here make no sound (heat and water marks, dents under a fall, budget records).
SOUND_OF = {
    "phys.fall": ("thud", "energy"),
    "phys.break": ("crash", "noticeable"),
    "phys.chip": ("clatter", "faint"),
    "phys.crush": ("creak", "faint"),
    "phys.collapse": ("crash", "loud"),
    "phys.pullout": ("crash", "loud"),
    "phys.tear": ("clatter", "noticeable"),
    "phys.tip": ("clatter", "energy"),
    "phys.slide": ("scrape", "faint"),
    "phys.roll": ("clatter", "faint"),
    "phys.spill": ("splash", "liquid"),
    "phys.overflow": ("splash", "liquid"),
    "phys.scratch": ("scrape", "faint"),
    "phys.shock": ("crash", "faint"),
    "phys.burst": ("crash", "noticeable"),
    "phys.blown": ("clatter", "noticeable"),
}
LOUDNESS = ("faint", "noticeable", "loud")
NOTICE_DB = {"faint": 45.0, "noticeable": 60.0, "loud": 80.0}     # sound level at 1 m of a heard consequence (6.2)
COUNT_WORDS = ((5, "many"), (2, "several"))     # events merged into one heard sound -> a coarse word (1: no word)
FAINT_J, LOUD_J = 1.0, 15.0           # impact energy (J): a napkin or a pen is faint, a mug off a table noticeable,
                                      # a 10 kg box or a toppling bookcase loud
LOUD_TIP_KG = 10.0                    # a tipping body this heavy (furniture) crashes
SPLASH_NOTICEABLE_L, SPLASH_LOUD_L = 0.25, 3.0
GLASS_CLASSES = ("glass", "tempered_glass", "borosilicate", "glass_ceramic", "crystal", "porcelain", "stoneware",
                 "ceramic", "terracotta")
FEEL_SOFT, FEEL_FIRM = 0.6, 0.3      # softness at and above which a thing feels soft (foam, cloth, rubber) /
                                      # firm (leather, fruit, a bag of soil); paper, wood, plastic, glass feel hard
STURDY_KG = 15.0                      # a slot rated at least this (nominal) looks sturdily built
TOUCH_PROBE_FIELDS = ("mass_kg", "weight_kg", "temperature_c", "wetness", "moisture")


def emit_consequence(w, kind, target, **data):
    """Write one physics consequence event (kind phys.*) with its oracle payload to the event log; returns the entry.
    The single route for every consequence of the physics layer (impact, liquids, thermal, environment, processes,
    events, settle). The event-log entry is exactly w.emit's; in geometric camera mode camera.visual_event then logs
    whether the robot's head camera saw the occurrence (its target, else the payload's room and cell; a fall, break
    or spill is at least 0.3 m of motion or splash)."""
    entry = w.emit(kind, target=target, **data)
    if "view" in w.s["robot"]:
        from .. import camera
        cell = data.get("cell")
        camera.visual_event(w, kind, source=target if isinstance(target, str) else None, room=data.get("room"),
                            at=cell if isinstance(cell, (list, tuple)) and len(cell) == 2 else None,
                            size_m=_event_size(w.s, target), emit=False)
    _sound_notice(w, entry)
    return entry


def _sound_notice(w, entry):
    """Post the consequence's sound on the notice bus (only while someone listens: kernel/timeline.py active)."""
    from .. import timeline
    if not timeline.active(w):
        return
    found = _sound(w.s, entry)
    if found is None:
        return
    sound, word = found
    target = entry.get("target")
    room = entry.get("room")
    if room is None and isinstance(target, str) and target in w.s["entities"]:
        room = root_room(w.s, target)
    w.notice(sound, room=room, db=NOTICE_DB[word], source=target if isinstance(target, str) else None,
             data={"loudness": word})


def _event_size(state, target):
    """Apparent size of a consequence for the camera: the target's largest dimension, at least 0.3 m (motion and
    splash make an occurrence bigger than the thing at rest)."""
    rec = state["entities"].get(target) if isinstance(target, str) else None
    size = 0.3
    if rec is not None:
        from .support import dims
        size = max(size, max(dims(rec)))
    return size


# ----- sounds -------------------------------------------------------------------------------------------------------

def _level(word):
    return LOUDNESS.index(word)


def _energy_word(state, target, height_m):
    rec = state["entities"].get(target)
    if rec is None:
        return "faint"
    from .support import subtree_mass
    try:
        joules = subtree_mass(state, target) * 9.81 * max(0.0, float(height_m or 0.0))
    except (TypeError, ValueError):
        joules = 0.0
    if joules >= LOUD_J:
        return "loud"
    return "noticeable" if joules >= FAINT_J else "faint"


def _sound(state, entry):
    """(sound kind, loudness word) of one event-log entry, or None when it makes no sound."""
    kind = entry.get("kind")
    rule = SOUND_OF.get(kind)
    if rule is None:
        return None
    sound, how = rule
    target = entry.get("target")
    if kind == "phys.fall":
        if entry.get("outcome") == "broke":
            return None                                   # the break makes the sound
        word = _energy_word(state, target, entry.get("height_m"))
        return ("thud" if word != "faint" else "clatter"), word
    if kind == "phys.tip":
        rec = state["entities"].get(target) or {}
        from .support import subtree_mass
        heavy = rec and subtree_mass(state, target) >= LOUD_TIP_KG
        return ("crash", "loud") if heavy else ("clatter", "faint")
    if kind == "phys.break":
        from .props import material_class
        rec = state["entities"].get(target)
        glassy = rec is not None and entry.get("mode") == "shatter" and material_class(None, rec) in GLASS_CLASSES
        return ("glass_breaking", "noticeable") if glassy else ("crash", "faint")
    if kind in ("phys.shock", "phys.burst"):
        from .props import material_class
        rec = state["entities"].get(target)
        if rec is not None and material_class(None, rec) in GLASS_CLASSES:
            return "glass_breaking", "noticeable"
        return sound, how
    if how == "liquid":
        liters = entry.get("liters") or 0.0
        word = "loud" if liters >= SPLASH_LOUD_L else "noticeable" if liters >= SPLASH_NOTICEABLE_L else "faint"
        return sound, word
    return sound, how


def _count_word(count):
    for least, word in COUNT_WORDS:
        if count >= least:
            return word
    return None


def heard_sounds(state, step=None):
    """The physics sounds of one step (default: the current one) as the robot hears them, in event order: one entry
    per sound kind and direction (here / nearby / elsewhere), the loudest word, a coarse count word when several
    things made it and, from the robot's room, the first source."""
    step = state.get("steps", 0) if step is None else step
    robot_room = (state.get("robot") or {}).get("room")
    rooms = state.get("rooms") or {}
    exits = set((rooms.get(robot_room) or {}).get("exits") or ())
    best, order, sources = {}, [], {}
    for entry in state.get("event_log") or ():
        if entry.get("step") != step or not str(entry.get("kind", "")).startswith("phys."):
            continue
        found = _sound(state, entry)
        if found is None:
            continue
        sound, word = found
        target = entry.get("target")
        room = entry.get("room")
        if room is None and target in state["entities"]:
            room = root_room(state, target)
        if room == robot_room:
            hint = "here"
        elif room in exits:
            hint = "nearby"
            if word == "faint":
                continue
        else:
            hint = "elsewhere"
            if word != "loud":
                continue
        key = (sound, hint)
        if key not in best:
            order.append(key)
            best[key] = word
            sources[key] = []
        elif _level(word) > _level(best[key]):
            best[key] = word
        if target not in sources[key]:
            sources[key].append(target)
    out = []
    for key in order:
        sound, hint = key
        item = {"kind": sound, "source_hint": hint, "loudness": best[key]}
        named = [t for t in sources[key] if t is not None]
        if hint == "here" and named:
            item["source"] = named[0]
        word = _count_word(len(sources[key]))
        if word is not None:
            item["count"] = word
        out.append(item)
    return out


def sounds(env, w, obs):
    """Observer physics.sounds: appends this step's physics sounds to obs["sounds"]."""
    heard = heard_sounds(w.s)
    if heard:
        obs.setdefault("sounds", []).extend(heard)


# ----- the physics inspector ----------------------------------------------------------------------------------------

def feel_of(phys):
    """How a thing feels in the hand: "soft" / "firm" / "hard", then "flexible" / "rigid"."""
    softness = float(phys.softness or 0.0)
    word = "soft" if softness >= FEEL_SOFT else "firm" if softness >= FEEL_FIRM else "hard"
    return "{}, {}".format(word, "rigid" if phys.rigid else "flexible")


def _nominal_rating(phys):
    values = [v for v in (phys.rating_kg, phys.shelf_rating_kg, phys.hang_kg, phys.handle_kg)
              if isinstance(v, (int, float)) and not isinstance(v, bool) and v != float("inf")]
    return min(values) if values else None


def looks_of(record):
    """"sturdy" or "flimsy" for something built to carry a load (a surface, shelf, hook, rod or handle rating), from
    its type's nominal rating class; None for anything unrated."""
    from .props import props_of
    rating = _nominal_rating(props_of(record))
    if rating is None:
        return None
    return "sturdy" if rating >= STURDY_KG else "flimsy"


def _touched(ctx, eid, rec):
    """Whether the robot has touched eid (oracle mode: always): a committed touch-level reading of any touch field,
    or a touch grant earlier in this step."""
    realism = getattr(ctx.env, "realism", None)
    if realism is None:
        return True
    from ..realism import TOUCH
    for pending in getattr(realism, "pending", ()) or ():
        if pending and pending[0] == eid and pending[1] >= TOUCH:
            return True
    knowledge = realism.knowledge
    return any(knowledge.level(eid, rec, field) >= TOUCH for field in TOUCH_PROBE_FIELDS if field in rec)


def inspect(w, eid, rec, details, ctx):
    """Inspector physics: feel (after a touch) and, on a close look (inspect where=closely), looks for a rated
    support. People and pets are left alone. A domain's own feel (laundry's dampness: inspectors of lower order run
    first) is the more specific percept and is kept."""
    if eid not in w.s["entities"]:
        return
    from .props import props
    if "feel" not in details and _touched(ctx, eid, rec):
        details["feel"] = feel_of(props(w.s, eid))
    if (getattr(ctx, "args", None) or {}).get("where") == "closely":
        looks = looks_of(rec)
        if looks is not None:
            details["looks"] = looks

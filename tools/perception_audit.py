"""Realistic-perception leak auditor.

usage: python tools/perception_audit.py [houses] [steps] [--first N] [--workers N] [--json OUT] [--verbose]
                                        [--camera room|geometric] [--battery PCT] [--faults random|none]

Generates houses, runs random walks in perception="realistic" (approach / inspect / read / pick up / place / move /
wait and random domain actions, all with handles taken from observations; with --camera geometric also looks around
and turns) and checks every observation and action message for leaks:
  id        a true entity id appears (handles, room, person and pet ids are fine), including the id of an entity
            that existed at the start of the step and was destroyed during it (merged, consumed, pulled) or in any
            earlier step of the episode (realism._ever); a descriptive word (label, kind, category...) that merely
            equals a hand-written id such as "sink" is not one (descriptive fields are never id-translated), an
            id-shaped word there is
  order     id-keyed JSON (a dict keyed by handles, or a list of handles) in an action message or an observer
            channel is in true-id order rather than handle order: the order ranks look-alikes by their true type.
            Judged over the whole run per container (owner + path): reported when it was never out of true-id order
            and its distinct id sets make a chance match unlikely (a pseudo-random order of two things matches half
            the time)
  secret    an underscore field or a secret_fields field appears in a perceived record
  hidden    a never-tier field (owner, home, value, intent, ...) appears unlabeled
  tier      an inspect/read/measure-tier field shows its exact true value while the robot's knowledge is lower --
            unless Realism shows it by design from memory: the robot learned that same value at the field's tier
            and the knowledge only lapsed because the thing moved unseen (realism.knowledge.stale)
  hazard    obs.grid.hazards names a source the robot does not perceive
  (the id rule also covers the ids of people and pets that are only shapes in the robot's dark room)
  message   an action message contains the exact value of a measure-tier field of its target that was not measured
  physics   (kernel v3.0) a physics consequence note (the records settle turns into notes), a physics sound
            (observer physics.sounds) or the physics inspector's feel / looks carries a number or names a hidden
            field, or uses words outside its fixed vocabulary; a perceived `wobbly` or `sagging` differs from the
            entity's true state
  camera    (kernel v3.0 camera, TIER1_CAMERA 15) any observation: a "_view" or "camera_log" key; room mode: a camera
            or lidar block or a "view" dict; geometric mode: a listed thing, person or pet the step view did not
            detect (after the realistic detection band) or listed without its view, a view with keys beyond the
            contract or with exact values (bearings and elevations not on 5 degrees, pixels beyond 2 significant
            figures, a range outside the depth camera's 0.2-6 m or below 24 px, light not a word), a camera block with
            camera, mic or lens keys, an exact light value or a pose cell other than obs.robot.pos, or a lidar block
            with keys beyond its contract
  robot     (kernel v3.0 robot body, TIER1_EMBODIMENT 6, TIER1_INTEGRATION 1.3 / 1.12) obs.robot shows a hidden-tier,
            underscore or unregistered robot field (only registered non-hidden fields and proprio.REALISTIC_KEYS are
            proprioception), a battery that is not an integer or differs from obs.battery, a position while the robot is
            unlocalized, or a fault the robot has not detected; any channel names a latent fault's id, or an observer
            channel carries a hidden-tier robot field; or a sensor gate does not hold: powered down, only held things
            and no people, pets, hazards, sounds or messages; hearing "none", no messages and no sounds; hearing
            "addressed", only messages addressed to the robot; room mode with imaging off, no thing under 0.4 m except
            what the robot holds, and no hazards
--battery PCT and --faults random start the houses with an almost flat pack and random robot faults, as tools/fuzz.py
does (the same per-seed rules: fuzz.robot_options), so the robot and sensor checks see powered-down, deaf, blind and
faulty robots.
Violations are attributed to the emitting module (kernel realism, the type's domain, an observer, or an action's
owner). Exit status 1 if any leak is found.
"""
import json
import math
import os
import random
import re
import sys
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
NUM = re.compile(r"-?\d+\.\d+")
OPTS = [{}, {"dwelling": "apartment"}, {"dwelling": "house", "require": ["garage", "laundry_room"]}, {"dwelling": "townhouse"},
        {"residents": "minimal", "pets": "none"}]


def _owner_of_type(reg, type_name):
    src = getattr(reg, "sources", {}).get(type_name, "?")
    return "types:" + src.replace(".json", "")


HANDLE = re.compile(r"^obj_[0-9a-f]{8,16}$")
HANDLE_IN = re.compile(r"obj_[0-9a-f]{8,16}")
# observation keys the kernel builds (handle-ordered or ordered by meaning, e.g. hands); the rest are observer channels
KERNEL_OBS_KEYS = {"room_id", "room", "connections", "robot", "holding", "visible_entities", "people", "pets", "grid",
                   "time", "steps", "time_s", "battery", "stopped", "pending", "dark", "weather", "instruction",
                   "last_answer", "messages", "perception", "camera", "lidar"}
# the geometric camera's observation contract (TIER1_CAMERA 10.6, TIER1_INTEGRATION 1.12)
VIEW_KEYS = {"bearing_deg", "elev_deg", "dist", "range_m", "px", "occluded", "via", "light"}
VIEW_VIA = {"head", "wrist", "depth", "held"}
CAMERA_KEYS = {"pose", "frames", "glare", "light", "coverage", "tracking", "events"}
POSE_KEYS = {"cell", "facing_deg", "facing", "pan_deg", "tilt_deg", "height_m"}
LIDAR_KEYS = {"height_m", "sector_deg", "ranges_m"}
PRIVATE_OBS_KEYS = ("_view", "camera_log")
SOUND_KEYS_OBS = ("sounds", "mechanism_sounds")      # perception.SOUND_CHANNELS


def _known_ids(state, start_ids, realism=None):
    """Entity ids now, those that existed when the step started (destroyed during it) and every id that existed earlier
    in the episode (realism._ever: destroyed in an earlier step)."""
    ever = {e for e in (getattr(realism, "_ever", None) or ()) if isinstance(e, str) and ":" not in e}
    return set(state["entities"]) | set(start_ids or ()) | ever


def _descriptive_word(path, token):
    """A token under a descriptive key (label, kind, category, material...) that is a plain word, not an id-shaped one:
    realism never id-translates descriptive fields, so "sink" as the sink's own label is no leak."""
    from house_sim.kernel.realism import DESCRIPTIVE_KEYS, id_shaped
    last = path.rsplit(".", 1)[-1]
    return last in DESCRIPTIVE_KEYS and not id_shaped(token.lower())


def id_order_findings(realism, value, path=""):
    """[(path, ids in shown order, in_true_order)] for each dict keyed by handles and each list of handles naming two or
    more distinct things whose true-id order differs from their handle order (where the two orders agree, the shown
    order tells nothing). in_true_order: the things are listed in true-id order, which ranks look-alikes by type."""
    found = []

    def check(items, where):
        pairs, seen = [], set()
        for h in items:
            if isinstance(h, str) and HANDLE.match(h):
                e = realism.handles.resolve(h)
                if e is not None and e not in seen:
                    seen.add(e)
                    pairs.append((h, e))
        if len(pairs) < 2:
            return
        by_true = [h for h, _ in sorted(pairs, key=lambda p: p[1])]
        if by_true == sorted(h for h, _ in pairs):
            return
        ids = tuple(e for _, e in pairs)
        found.append((where, ids, list(ids) == sorted(ids)))

    def walk(v, where):
        if isinstance(v, dict):
            check(list(v), where)
            for k, item in v.items():
                walk(item, "{}.{}".format(where, k) if where else str(k))
        elif isinstance(v, list):
            check(v, where)
            for item in v:
                walk(item, where)
    walk(value, path)
    return found


def _report_order(findings, owner, label, leaks, order, where=""):
    """One-off checks (order=None) report every true-id-ordered container; an audit run passes an accumulator list and
    decides later with order_leaks, so a container whose order is merely pseudo-random is not reported. The container
    is named by label and path with handles masked (the same container in every step and house)."""
    for path, ids, in_true in findings:
        template = HANDLE_IN.sub("*", "{} {}".format(label, path or "(top)"))
        if order is not None:
            order.append((owner, template, ids, in_true))
        elif in_true:
            leaks.append(("order", owner, "{}{} is in true-id order".format(where + " " if where else "", template)))


def order_leaks(records, chance=0.125):
    """Leaks from accumulated (owner, template, ids, in_true_order) records: a container (owner and path, handles
    masked) is reported when it was never seen out of true-id order and its distinct id sets make a chance match
    unlikely (product of 1/n! over the sets <= chance; e.g. three different pairs, or one triple and a pair)."""
    groups = {}
    for owner, template, ids, in_true in records:
        groups.setdefault((owner, template), []).append((ids, in_true))
    out = []
    for (owner, template), seen in sorted(groups.items()):
        if not all(in_true for _, in_true in seen):
            continue
        sets = {frozenset(ids) for ids, _ in seen}
        p = 1.0
        for ids in sets:
            p /= math.factorial(len(ids))
        if p <= chance:
            out.append(("order", owner, "{} listed in true-id order {} times ({} distinct sets, chance {:.3f})".format(
                template, len(seen), len(sets), p)))
    return out


def _remembered(realism, eid, field, value, need):
    """True if Realism shows `value` from memory by design (v2.8 'knowledge that lapses shows the remembered value'):
    the robot learned this same value at the field's tier or better, and its knowledge lapsed only because the thing
    moved unseen (the placement signature changed), not because the value changed."""
    stale = getattr(realism.knowledge, "stale", None)
    old = stale(eid, field) if stale is not None else None
    return bool(old) and old[0] >= need and _same(value, old[1])


def check_obs(env, obs, leaks, where, start_ids=None, order=None):
    from house_sim.kernel.realism import TIER_NEED, MEASURE
    state = env.world.s
    realism = env.realism
    entities = state["entities"]
    known_ids = _known_ids(state, start_ids, realism)
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    room = state["robot"].get("room")
    shapes = set()
    if obs.get("dark"):
        # in the dark the people and pets of the robot's room are shapes: their ids would say who is who
        shapes = {a for g in ("people", "pets") for a, rec in (state.get(g) or {}).items() if rec.get("location") == room}
    elif isinstance(state["robot"].get("view"), dict):
        # geometric camera: so are the ones the camera saw without identifying them
        keyed = getattr(realism, "_geo_keyed", None) or ()
        shapes = {a for g in ("people", "pets") for a, rec in (state.get(g) or {}).items()
                  if rec.get("location") == room and a in keyed}
    if shapes:
        plain -= shapes
        known_ids = known_ids | shapes
    observer_keys = getattr(env, "_observer_keys", {})

    def scan(value, path):
        if isinstance(value, str):
            for t in TOKEN.findall(value):
                if t in known_ids and t not in plain and not _descriptive_word(path, t):
                    top = path.split(".")[0]
                    leaks.append(("id", observer_keys.get(top, "kernel:" + top), "{} {}: {}".format(where, path, t)))
        elif isinstance(value, dict):
            for k, v in value.items():
                scan(k, path)
                scan(v, path + "." + str(k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                scan(v, path)
    scan(obs, "obs")
    for key in sorted(k for k in obs if k not in KERNEL_OBS_KEYS):
        _report_order(id_order_findings(realism, obs[key], key), observer_keys.get(key, "kernel:" + key), "obs", leaks, order, where)
    for handle, rec in obs.get("visible_entities", {}).items():
        eid = realism.handles.resolve(handle)
        if eid is None or eid not in entities:
            continue
        true = entities[eid]
        owner = _owner_of_type(env.registry, true.get("type"))
        for field, shown in rec.items():
            if field.startswith("_") or field in (true.get("secret_fields") or []):
                leaks.append(("secret", owner, "{} {}.{}".format(where, eid, field)))
                continue
            if field not in true:
                continue
            tier = realism.tier(true, field)
            if tier == "never":
                leaks.append(("hidden", owner, "{} {}.{}".format(where, eid, field)))
                continue
            need = TIER_NEED[tier]
            known = realism.knowledge.level(eid, true, field)
            value = true[field]
            informative = value not in (None, False, 0, 0.0, "", [], {}) and not isinstance(value, bool)
            if field == "size_m" and isinstance(value, list) and all(abs(_r05(v) - float(v)) < 1e-9 for v in value if isinstance(v, (int, float))):
                continue    # coincidence of the 5 cm visual estimate with a size that is a multiple of 5 cm
            if tier != "glance" and known < need and informative and _same(shown, value):
                if _remembered(realism, eid, field, value, need):
                    continue     # remembered from when it was learned (it moved unseen since): shown by design
                if isinstance(value, float) or tier in ("read", "measure") or isinstance(value, str):
                    leaks.append(("tier", owner, "{} {}.{} ({} tier, knowledge {}) = {!r}".format(where, eid, field, tier, known, value)))
    check_physics_obs(env, obs, leaks, where)
    check_camera_obs(env, obs, leaks, where)
    check_robot_obs(env, obs, leaks, where)
    for item in obs.get("grid", {}).get("hazards", []):
        src = item.get("source")
        if isinstance(src, str) and src.startswith("obj_") and src not in obs.get("visible_entities", {}):
            leaks.append(("hazard", "kernel:hazards", "{} unperceived source {}".format(where, src)))


# ----- camera checks (kernel v3.0 geometric camera, TIER1_CAMERA 15) -------------------------------------------------

def _on_step(value, step):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        abs(value / step - round(value / step)) < 1e-6


def _two_sig(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        abs(float("{:.2g}".format(value)) - value) < 1e-9


def view_problems(view, light_words):
    """What is wrong with one realistic "view" dict (10.6), as a list of short texts."""
    out = []
    if not isinstance(view, dict):
        return ["not a dict"]
    extra = sorted(set(view) - VIEW_KEYS)
    if extra:
        out.append("keys {}".format(extra))
    for key in ("bearing_deg", "elev_deg"):
        if key in view and not _on_step(view[key], 5.0):
            out.append("{} {!r} not on 5 degrees".format(key, view[key]))
    if "px" in view and not _two_sig(view["px"]):
        out.append("px {!r} beyond 2 significant figures".format(view["px"]))
    if "range_m" in view:
        r = view["range_m"]
        if not _on_step(r, 0.1) or not 0.1 - 1e-9 <= r <= 6.6 or (isinstance(view.get("px"), (int, float)) and view["px"] < 24):
            out.append("range_m {!r} outside the depth camera's contract".format(r))
    if "light" in view and view["light"] not in light_words:
        out.append("light {!r} is not a word".format(view["light"]))
    if view.get("via") not in VIEW_VIA:
        out.append("via {!r}".format(view.get("via")))
    return out


def _private_keys(value, path, found):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in PRIVATE_OBS_KEYS:
                found.append("{}.{}".format(path, k))
            _private_keys(v, "{}.{}".format(path, k), found)
    elif isinstance(value, list):
        for v in value:
            _private_keys(v, path, found)


def check_camera_obs(env, obs, leaks, where):
    """The camera leak checks (TIER1_CAMERA 15): no private camera keys anywhere; room mode adds no camera state;
    geometric mode lists only what the step view detected (after the realistic band), each with a realistic view, and
    camera and lidar blocks within their contracts."""
    from house_sim.kernel.realism import LIGHT5
    state = env.world.s
    realism = env.realism
    found = []
    _private_keys(obs, "obs", found)
    for path in found:
        leaks.append(("camera", "kernel:camera", "{} private key {}".format(where, path)))
    groups = ("visible_entities", "people", "pets")
    if not isinstance(state["robot"].get("view"), dict):
        if "camera" in obs or "lidar" in obs:
            leaks.append(("camera", "kernel:camera", "{} room mode shows a camera or lidar block".format(where)))
        for group in groups:
            for key, rec in (obs.get(group) or {}).items():
                if isinstance(rec, dict) and isinstance(rec.get("view"), dict):
                    leaks.append(("camera", "kernel:camera", "{} room mode {}.{} has a view".format(where, group, key)))
        return
    from house_sim.kernel import camera
    light_words = set(LIGHT5[1])
    perceived = realism.perceived_in(camera.step_view(env)) if hasattr(realism, "perceived_in") else None
    for group in groups:
        for key, rec in (obs.get(group) or {}).items():
            true = realism.handles.resolve(key) if isinstance(key, str) and HANDLE.match(key) else key
            if isinstance(true, str) and true.startswith("shape:"):
                true = true.split(":")[1]
            if not isinstance(rec, dict) or "view" not in rec:
                leaks.append(("camera", "kernel:camera", "{} {} {} listed without a view".format(where, group, true)))
                continue
            if perceived is not None and true not in perceived:
                leaks.append(("camera", "kernel:camera", "{} {} {} listed but not detected".format(where, group, true)))
            for problem in view_problems(rec["view"], light_words):
                leaks.append(("camera", "kernel:camera", "{} {}.{} view: {}".format(where, group, true, problem)))
    block = obs.get("camera")
    if not isinstance(block, dict):
        leaks.append(("camera", "kernel:camera", "{} geometric mode without a camera block".format(where)))
    else:
        extra = sorted(set(block) - CAMERA_KEYS)
        if extra:
            leaks.append(("camera", "kernel:camera", "{} camera block keys {}".format(where, extra)))
        if block.get("light") not in light_words:
            leaks.append(("camera", "kernel:camera", "{} camera light {!r} is not a word".format(where, block.get("light"))))
        pose = block.get("pose") or {}
        if sorted(set(pose) - POSE_KEYS):
            leaks.append(("camera", "kernel:camera", "{} camera pose keys {}".format(where, sorted(set(pose) - POSE_KEYS))))
        if pose.get("cell") != (obs.get("robot") or {}).get("pos"):
            leaks.append(("camera", "kernel:camera", "{} camera pose cell {!r} differs from obs.robot.pos".format(
                where, pose.get("cell"))))
        for event in block.get("events") or []:
            if isinstance(event, dict) and "bearing_deg" in event and not _on_step(event["bearing_deg"], 5.0):
                leaks.append(("camera", "kernel:camera", "{} camera event bearing {!r}".format(where, event["bearing_deg"])))
    lidar = obs.get("lidar")
    if lidar is not None and (not isinstance(lidar, dict) or set(lidar) - LIDAR_KEYS):
        leaks.append(("camera", "kernel:camera", "{} lidar block {!r}".format(where, sorted(lidar) if isinstance(lidar, dict) else lidar)))


# ----- robot body checks (kernel v3.0 robot body, TIER1_EMBODIMENT 6) -------------------------------------------------

def _robot_tiers():
    """(fields obs.robot may show, hidden-tier field names) from the robot field registry and proprio."""
    from house_sim.kernel import body, proprio
    shown = {n for n, e in body.ROBOT_FIELDS.items() if e["tier"] != "hidden" and not n.startswith("_")}
    hidden = {n for n, e in body.ROBOT_FIELDS.items() if e["tier"] == "hidden" or n.startswith("_")}
    return shown | set(proprio.REALISTIC_KEYS), hidden


def _strings_and_keys(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                out.append(k)
            _strings_and_keys(v, out)
    elif isinstance(value, list):
        for v in value:
            _strings_and_keys(v, out)
    return out


def _keys_named(value, names, path, found):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in names:
                found.append("{}.{}".format(path, k))
            _keys_named(v, names, "{}.{}".format(path, k), found)
    elif isinstance(value, list):
        for v in value:
            _keys_named(v, names, path, found)
    return found


def _held_or_worn(state, eid):
    robot = state["robot"]
    return eid in [v for v in (robot.get("hands") or {}).values() if v] or \
        (state["entities"].get(eid) or {}).get("location") == "robot"


def check_robot_obs(env, obs, leaks, where):
    """The robot body checks (realistic perception): proprioception by tier and the sensor gates (see the module
    docstring, kind "robot")."""
    from house_sim.kernel import body, body_faults
    state = env.world.s
    realism = env.realism
    owner = "kernel:proprio"
    shown, hidden = _robot_tiers()
    own = obs.get("robot")
    latent = [r for r in body_faults.active_faults(state) if not r.get("detected")]
    if isinstance(own, dict):
        for key in sorted(own):
            if key in hidden or key.startswith("_"):
                leaks.append(("robot", owner, "{} obs.robot.{} is a hidden robot field".format(where, key)))
            elif key not in shown:
                leaks.append(("robot", owner, "{} obs.robot.{} is not a registered robot field".format(where, key)))
        if "battery" in own:
            level = own["battery"]
            if not isinstance(level, int) or isinstance(level, bool):
                leaks.append(("robot", owner, "{} obs.robot.battery {!r} is not an integer gauge".format(where, level)))
            if "battery" in obs and obs["battery"] != level:
                leaks.append(("robot", owner, "{} obs.battery {!r} differs from obs.robot.battery {!r}".format(
                    where, obs["battery"], level)))
        if not body.get(state, "localized") and own.get("pos") is not None:
            leaks.append(("robot", owner, "{} obs.robot.pos {!r} while unlocalized".format(where, own["pos"])))
        if "runtime_min_est" in own or "range_m_est" in own:
            # the estimates follow the gauge alone (proprio.estimates has no load term): no held mass in them
            from house_sim.kernel import proprio
            runtime, reach = proprio.estimates(state)
            for key, value in (("runtime_min_est", runtime), ("range_m_est", reach)):
                if key in own and own[key] != value:
                    leaks.append(("robot", owner, "{} obs.robot.{} {!r} is not the gauge's estimate {!r}".format(
                        where, key, own[key], value)))
        for item in own.get("faults") or []:
            if not isinstance(item, dict):
                continue
            for record in latent:
                if item.get("kind") == record["kind"] and item.get("part") == record["part"]:
                    leaks.append(("robot", owner, "{} obs.robot.faults lists latent {}".format(where, record["id"])))
    if latent:
        tokens = set()
        for text in _strings_and_keys(obs, []):
            tokens.update(TOKEN.findall(text))
        for record in latent:
            if record["id"] in tokens and record["id"] != record["kind"] or \
                    record["id"] == record["kind"] and record["kind"] in tokens and \
                    record["kind"] not in {r["kind"] for r in body_faults.active_faults(state, detected_only=True)}:
                leaks.append(("robot", owner, "{} the observation names latent fault {}".format(where, record["id"])))
    for key in sorted(k for k in obs if k not in KERNEL_OBS_KEYS):
        for path in _keys_named(obs[key], hidden - {"view"}, key, []):
            leaks.append(("robot", getattr(env, "_observer_keys", {}).get(key, "kernel:" + key),
                          "{} obs.{} is a hidden robot field".format(where, path)))
    # the sensor gates (TIER1_INTEGRATION 1.3)
    gates = "kernel:perception"
    hearing = body.can_hear(state)
    sounds = [k for k in SOUND_KEYS_OBS if obs.get(k)] + \
        ["{}.{}".format(k, s) for k, v in obs.items() if isinstance(v, dict) for s in SOUND_KEYS_OBS if v.get(s)]
    messages = obs.get("messages") or []
    if not body.is_on(state):
        for handle in obs.get("visible_entities") or {}:
            eid = realism.handles.resolve(handle) if realism is not None else handle
            if eid in state["entities"] and not _held_or_worn(state, eid):
                leaks.append(("robot", gates, "{} powered down but {} is listed".format(where, eid)))
        for group in ("people", "pets"):
            if obs.get(group):
                leaks.append(("robot", gates, "{} powered down but {} are listed".format(where, group)))
        if (obs.get("grid") or {}).get("hazards"):
            leaks.append(("robot", gates, "{} powered down but hazards are listed".format(where)))
    if hearing == "none" or not body.is_on(state):
        if messages:
            leaks.append(("robot", gates, "{} hearing none but {} message(s)".format(where, len(messages))))
        if sounds:
            leaks.append(("robot", gates, "{} hearing none but sounds in {}".format(where, sounds)))
    elif hearing == "addressed":
        for m in messages:
            if isinstance(m, dict) and m.get("to") != "robot" and not m.get("addressed_robot"):
                leaks.append(("robot", gates, "{} hearing addressed but a message to {!r}".format(where, m.get("to"))))
    if not body.imaging(state) and not isinstance(state["robot"].get("view"), dict):
        from house_sim.kernel.perception import SHAPE_M, _max_dim
        for handle in obs.get("visible_entities") or {}:
            eid = realism.handles.resolve(handle) if realism is not None else handle
            rec = state["entities"].get(eid)
            if rec is not None and not _held_or_worn(state, eid) and _max_dim(rec) < SHAPE_M:
                leaks.append(("robot", gates, "{} imaging off but {} ({} m) is listed".format(where, eid,
                                                                                            round(_max_dim(rec), 2))))
        if (obs.get("grid") or {}).get("hazards"):
            leaks.append(("robot", gates, "{} imaging off but hazards are listed".format(where)))


# ----- physics checks (kernel v3.0, TIER1_PHYSICS 8) ----------------------------------------------------------------

# a number standing on its own (not part of an id such as cup_1 or a handle)
PHYS_NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])")
SOUND_KEYS = {"kind", "source_hint", "loudness", "source", "count"}
PHYS_SHOWN = ("wobbly", "sagging")
LOOKS_WORDS = {"sturdy", "flimsy"}
FEEL_WORDS = {"soft", "firm", "hard", "rigid", "flexible"}     # the feel vocabulary (rigid, soft name hidden fields too)


def _hidden_fields(env):
    """Field names every domain-wide field_tiers spec hides (core.json lists the physical thresholds)."""
    names = getattr(env, "_audit_hidden", None)
    if names is None:
        names = set()
        for spec in getattr(env.registry, "field_tiers", []) or ():
            names.update(f for f in (spec.get("hidden") or ()) if isinstance(f, str))
        env._audit_hidden = names
    return names


def physics_text_problems(text, hidden, words=()):
    """What is wrong with one physics note or observer string: a number, or the name of a hidden field that is not one
    of the string's own vocabulary words."""
    out = []
    if not isinstance(text, str):
        return out
    m = PHYS_NUMBER.search(text)
    if m:
        out.append("number {}".format(m.group(0)))
    for token in TOKEN.findall(text):
        if token in hidden and token not in words:
            out.append("hidden field {}".format(token))
    return out


def check_physics_notes(env, records, leaks, where):
    """Physics consequence records of one step (settle's return, the source of the step's physics notes): a note never
    carries a number or names a hidden field."""
    hidden = _hidden_fields(env)
    for record in records or ():
        note = record.get("note") if isinstance(record, dict) else None
        for problem in physics_text_problems(note, hidden):
            leaks.append(("physics", "physics:" + str(record.get("kind", "?")),
                          "{} note {!r}: {}".format(where, note, problem)))


def check_physics_obs(env, obs, leaks, where):
    """The physics observer's sounds (words only, fixed keys) and wobbly / sagging only where they are true."""
    from house_sim.kernel.phys import observe as phys_observe
    hidden = _hidden_fields(env)
    state = env.world.s
    for item in phys_observe.heard_sounds(state):
        extra = sorted(set(item) - SOUND_KEYS)
        if extra:
            leaks.append(("physics", "observer:physics.sounds", "{} sound keys {}".format(where, extra)))
        if item.get("loudness") not in phys_observe.LOUDNESS:
            leaks.append(("physics", "observer:physics.sounds", "{} loudness {!r}".format(where, item.get("loudness"))))
        if "count" in item and item["count"] not in {w for _, w in phys_observe.COUNT_WORDS}:
            leaks.append(("physics", "observer:physics.sounds", "{} count {!r}".format(where, item.get("count"))))
        for key in sorted(item):
            for problem in physics_text_problems(item[key], hidden):
                leaks.append(("physics", "observer:physics.sounds", "{} sound {}: {}".format(where, key, problem)))
    entities = state["entities"]
    for handle, rec in (obs.get("visible_entities") or {}).items():
        eid = env.realism.handles.resolve(handle)
        if eid is None or eid not in entities:
            continue
        true = entities[eid]
        for field in PHYS_SHOWN:
            if field in rec and bool(rec[field]) != bool(true.get(field)):
                leaks.append(("physics", _owner_of_type(env.registry, true.get("type")),
                              "{} {}.{} shown {!r}, true {!r}".format(where, eid, field, rec[field], true.get(field))))


def check_physics_inspect(env, action, data, leaks):
    """The physics inspector's words in an inspect message: feel carries no number or hidden field name (a domain's
    own feel, laundry's "damp" or "dry", takes the key first), looks is "sturdy" / "flimsy"."""
    if not isinstance(data, dict):
        return
    feel = data.get("feel")
    if feel is not None:
        for problem in physics_text_problems(feel if isinstance(feel, str) else repr(feel), _hidden_fields(env),
                                             FEEL_WORDS):
            leaks.append(("physics", "inspector:physics", "{} feel {!r}: {}".format(action, feel, problem)))
    looks = data.get("looks")
    if looks is not None and looks not in LOOKS_WORDS:
        leaks.append(("physics", "inspector:physics", "{} looks {!r}".format(action, looks)))


_CAPTURED = []


def _capture_settle():
    """Wrap the kernel settle pass once per process so the audit sees each step's physics consequence records."""
    from house_sim.kernel.phys import settle as phys_settle
    if getattr(phys_settle.settle, "_audit_capture", False):
        return
    original = phys_settle.settle

    def settle(env, phase, ctx=None, final=True):
        records = original(env, phase, ctx, final)
        _CAPTURED.extend(r for r in (records or ()) if isinstance(r, dict))
        return records
    settle._audit_capture = True
    phys_settle.settle = settle


def _r05(v):
    return round(float(v) * 20.0) / 20.0


def _same(shown, value):
    if isinstance(value, float) and isinstance(shown, (int, float)) and not isinstance(shown, bool):
        return abs(shown - value) < 1e-9
    return shown == value


def check_message(env, action, target_eid, message, leaks, start_ids=None, order=None):
    """start_ids: entity ids at the start of the step (ids of things destroyed during it are leaks too); order: an
    accumulator for id-order findings (see _report_order; None reports each true-id-ordered container at once)."""
    from house_sim.kernel.plugins import OWNERS
    from house_sim.kernel.realism import MEASURE
    state = env.world.s
    entities = state["entities"]
    known_ids = _known_ids(state, start_ids, env.realism)
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    owner = OWNERS.get(("action", action), "?")
    for t in TOKEN.findall(message or ""):
        if t in known_ids and t not in plain:
            leaks.append(("id", "action:" + owner, "{} message: {}".format(action, t)))
    try:
        data = json.loads(message)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, (dict, list)):
        _report_order(id_order_findings(env.realism, data), "action:" + owner, action + " message JSON", leaks, order)
    if action == "inspect":
        check_physics_inspect(env, action, data, leaks)
    if target_eid and target_eid in entities:
        rec = entities[target_eid]
        realism = env.realism
        for field, value in rec.items():
            if not isinstance(value, float) or isinstance(value, bool) or abs(value) < 1e-6:
                continue
            if realism.tier(rec, field) != "measure" or realism.knowledge.level(target_eid, rec, field) >= MEASURE:
                continue
            if isinstance(data, dict):
                shown = data.get(field)
                if isinstance(shown, (int, float)) and not isinstance(shown, bool) and abs(shown - value) < 1e-9:
                    leaks.append(("message", "action:" + owner, "{} reveals {}.{} = {}".format(action, target_eid, field, value)))
                continue
            stem = field.split("_")[0]
            for m in NUM.finditer(message or ""):
                if abs(float(m.group(0)) - value) < 1e-9 or any(abs(float(m.group(0)) - round(value, d)) < 1e-9 and round(value, d) != round(value)
                                                                for d in (1, 2)):
                    if stem in (message or "")[max(0, m.start() - 40):m.start()].lower():
                        leaks.append(("message", "action:" + owner, "{} reveals {}.{} = {}".format(action, target_eid, field, value)))
                        break


def run_house(job):
    seed, opts, steps = job[:3]
    camera = job[3] if len(job) > 3 else None
    battery = job[4] if len(job) > 4 else None
    faults = job[5] if len(job) > 5 else None
    from house_sim.generator import generate_house
    from house_sim.kernel import plugins
    from house_sim.kernel.engine import HouseEnv
    plugins.load_domains()
    _capture_settle()
    leaks, errors = [], []
    rng = random.Random("percaudit:{}".format(seed))
    try:
        config = generate_house(seed, **opts)
        if battery is not None or faults:
            from tools.fuzz import robot_options      # the fuzzer's per-seed battery and fault rules
            robot_options(config, seed, battery, faults)
        env = HouseEnv(config, seed=seed, perception="realistic", trace=False, observe_on_step=False, camera=camera)
    except Exception as exc:  # noqa: BLE001
        return {"seed": seed, "leaks": [], "errors": ["generate: {}: {}".format(type(exc).__name__, exc)], "steps": 0}
    geometric = env.camera_mode == "geometric"
    env._observer_keys = _observer_keys(env)
    names = sorted(n for n in plugins.ACTIONS if n not in ("undo", "watch", "run_pending"))
    order = []        # id-order findings, judged across the whole run (order_leaks)
    obs = env.observe()
    check_obs(env, obs, leaks, "step0", order=order)
    for i in range(steps):
        handles = sorted(obs.get("visible_entities", {}))
        roll = rng.random()
        if roll < 0.25 and handles:
            cmd = {"action": "approach", "target": rng.choice(handles)}
        elif roll < 0.45 and handles:
            cmd = {"action": rng.choice(["inspect", "read", "inspect"]), "target": rng.choice(handles)}
        elif roll < 0.55 and handles:
            cmd = {"action": "pick_up", "target": rng.choice(handles)}
        elif roll < 0.6 and obs.get("holding"):
            cmd = {"action": "place", "target": rng.choice(obs["holding"]), "destination": obs["room_id"]}
        elif roll < 0.7:
            exits = obs.get("room", {}).get("exits") or []
            cmd = {"action": "move", "target": rng.choice(exits)} if exits else {"action": "look"}
        elif roll < 0.73:
            cmd = {"action": "wait", "seconds": 300}
        elif roll < 0.79 and geometric:
            # the camera's own verbs: look around (any form the look action offers), turn to a thing
            cmd = {"action": "look", "target": rng.choice(["around", "forward", "down", "up", "left", "right"])}
            if handles and "face" in plugins.ACTIONS and rng.random() < 0.3:
                cmd = {"action": "face", "target": rng.choice(handles)}
        else:
            name = rng.choice(names)
            spec = plugins.ACTIONS[name]
            cmd = {"action": name}
            for pname, p in spec.params.items():
                if p.get("optional") and rng.random() < 0.6:
                    continue
                kind = p["type"]
                if "choices" in p:
                    cmd[pname] = rng.choice(p["choices"])
                elif kind in ("entity", "target", "agent") and handles:
                    cmd[pname] = rng.choice(handles)
                elif kind == "room":
                    cmd[pname] = obs["room_id"]
                elif kind in ("number", "float"):
                    cmd[pname] = 1.0
                elif kind == "int":
                    cmd[pname] = 1
                elif kind == "bool":
                    cmd[pname] = True
                elif kind == "text":
                    cmd[pname] = "hello"
                elif kind == "list":
                    cmd[pname] = []
                elif kind == "dict":
                    cmd[pname] = {}
                else:
                    cmd[pname] = "on"
        args = dict(cmd)
        action = args.pop("action")
        target = args.pop("target", "")
        destination = args.pop("destination", "")
        target_eid = env.realism.handles.resolve(target) if isinstance(target, str) else None
        start_ids = set(env.world.s["entities"])
        del _CAPTURED[:]
        try:
            result = env.step(action, target, destination, **args)
        except Exception as exc:  # noqa: BLE001
            errors.append("{} {}: {}: {}".format(action, target, type(exc).__name__, str(exc)[:200]))
            try:
                env.world.rollback()
            except Exception:  # noqa: BLE001
                pass
            continue
        check_message(env, action, target_eid, result.get("message"), leaks, start_ids=start_ids, order=order)
        check_physics_notes(env, _CAPTURED, leaks, "step{}".format(i + 1))
        obs = env.observe()
        check_obs(env, obs, leaks, "step{}".format(i + 1), start_ids=start_ids, order=order)
    return {"seed": seed, "leaks": leaks, "errors": errors, "steps": steps, "order": [list(r[:2]) + [list(r[2]), r[3]] for r in order]}


def _observer_keys(env):
    """Which observer adds which top-level observation key (for attribution)."""
    from house_sim.kernel.plugins import OBSERVERS
    keys = {}
    base = env.perception.observe.__self__
    for order, name, fn in OBSERVERS:
        probe = {}
        try:
            fn(env, env.world, probe)
        except Exception:  # noqa: BLE001
            continue
        for key in probe:
            keys.setdefault(key, "observer:" + name)
    return keys


def main(argv):
    houses = int(argv[0]) if argv and argv[0].isdigit() else 20
    steps = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 120
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else max(1, (os.cpu_count() or 2) - 2)
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    camera = argv[argv.index("--camera") + 1] if "--camera" in argv else None
    faults = argv[argv.index("--faults") + 1] if "--faults" in argv else None
    try:
        battery = float(argv[argv.index("--battery") + 1]) if "--battery" in argv else None
    except (IndexError, ValueError):
        battery = -1.0
    if camera not in (None, "room", "geometric") or faults not in (None, "random", "none") or \
            (battery is not None and not 0.0 <= battery <= 100.0):
        print("--camera must be room or geometric; --battery a % from 0 to 100; --faults random or none")
        return 2
    faults = None if faults == "none" else faults
    jobs = [(first + k, OPTS[k % len(OPTS)], steps, camera, battery, faults) for k in range(houses)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_house, jobs))
    by_kind, by_owner, examples = Counter(), Counter(), defaultdict(list)
    seeds_of = defaultdict(set)
    for r in results:
        for owner, template, ids, in_true in r.get("order", []):
            seeds_of[(owner, template)].add(r["seed"])
    ordered = order_leaks([(o, t, tuple(ids), v) for r in results for o, t, ids, v in r.get("order", [])])
    # run-level findings: one entry per container, credited to the seeds where it was seen
    judged = [{"seed": ",".join(str(s) for s in sorted(seeds_of[(owner, detail.split(" listed in", 1)[0])])),
               "leaks": [(kind, owner, detail)], "errors": []} for kind, owner, detail in ordered]
    for r in results + judged:
        for kind, owner, detail in r["leaks"]:
            by_kind[kind] += 1
            by_owner[(kind, owner)] += 1
            if len(examples[(kind, owner)]) < 3:
                examples[(kind, owner)].append("seed {}: {}".format(r["seed"], detail))
    errors = [e for r in results for e in r["errors"]]
    print("houses {}  steps {}  leaks {}  crashes {}{}{}".format(
        len(results), sum(r["steps"] for r in results), sum(by_kind.values()), len(errors),
        "  camera {}".format(camera) if camera else "",
        "  battery {} faults {}".format(battery, faults or "none") if battery is not None or faults else ""))
    for kind, n in by_kind.most_common():
        print("  {:6d}  {}".format(n, kind))
    for (kind, owner), n in by_owner.most_common():
        print("  {:6d}  {:8s} {}".format(n, kind, owner))
        if "--verbose" in argv:
            for ex in examples[(kind, owner)]:
                print("            " + ex[:300])
    for e in errors[:15]:
        print("  crash:", e)
    if out:
        Path(out).write_text(json.dumps({"leaks": [dict(seed=r["seed"], kind=k, owner=o, detail=d) for r in results + judged for k, o, d in r["leaks"]],
                                         "errors": errors}, indent=1), encoding="utf-8", newline="\n")
    return 1 if by_kind or errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

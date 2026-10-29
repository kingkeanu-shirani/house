"""Realistic-perception leak auditor.

usage: python tools/perception_audit.py [houses] [steps] [--first N] [--workers N] [--json OUT] [--verbose]
                                        [--camera room|geometric] [--battery PCT] [--faults random|none]
                                        [--no-probes] [--twin-fields] [--gen 1|2]

--gen 2 walks generation-2 houses (docs/GENERATOR.md: furnishings, belongings, smart-home and charging devices;
phase 1, electrical's request 5); the default is each configuration's own (generation 1).

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
  people    (kernel v3.1, TIER1_SWEEP 17) the people-and-memory actions the walk also exercises (canonical ask with
            handles, words, rooms, people and "it"; typed reports with known and unknown words; recall map / seen: /
            snap:; snapshot and diff): an answer's clarify options or refers_to carry a handle (people speak in
            descriptions); a recall, diff or snapshot message names a handle that was neither issued before the step
            nor of a thing in view; a report's suggestions for an unresolved look-alike or unfamiliar thing differ from
            the generic list (they must be the same for every member of a group); obs.notices lists a notice whose
            source handle was never issued, an audio notice while the robot hears nothing, or any notice while it is
            powered down; obs.request names a person who is a shape in the robot's dark room. The houses run with
            the notice bus on (state "notices", as schema-3 episodes do), and half the waits listen
            (interrupt_on), so obs.notices, listen and the wait messages are audited too. Stage-C fix additions:
            the houses carry a request with a hidden target (user_simulator.intent with candidates, a request
            subject), prior knowledge of look-alikes seen at a glance, and dark rooms with people in them; the walk
            asks about "request" and about people present or not, reports about people and to people away, and
            issues wait(until) on handles in view or not, on dark shapes and on people. Leaks: an answer's "about"
            that is not the agent's own argument (a handle it did not give: the request's target); a recalled
            label that is the true name of a thing the robot cannot tell apart and was never shown that name; a
            person's id typed in a dark room that comes back as a shape handle; a wait(until) accepted on a thing
            the robot does not see now, on a dark shape's field, or on a person in the dark
  grant     (K-LEAK, review H7 / I3) a step raised the robot's knowledge of a thing to inspect level or better while
            it could not have looked closely at it from where it is (audit_grant_problem: arm's reach, a large
            fixture or inspect_from_afar; geometric: camera.inspect_check) -- knowledge laundered through an action's
            grant; judged on steps that left the robot where it stood (a walk's contacts are the kernel gate's). The
            walk also counts whole rooms (count <room>) for it
Differential probes (K-LEAK; run once per audit in its camera mode, --no-probes skips them; see "differential probes"
below): oracle (wait / watch answers on twins that render alike), twin (look-alike members of different true size and
unfamiliar things with the same visual props: the rendering, listing, what the view's pixels imply about the size),
episode (another episode's handle accepted; handles shared by episodes with different nonces), echo (replies to
id-shaped words that exist or not, and to the handle of a thing destroyed out of view), target (free text naming every
member type of an unresolved look-alike, and the listing order of hazards sharing a cell: their handles'
order), read (every member of every look-alike group, read and read aloud to a resident while unresolved: the same
outcome form). --twin-fields makes the
look-alike twin probe compare every field (it then also lists the type-data tells of the registry lint's class, owned
by the domains' type data); unfamiliar things are always compared on every field and on the outcome of operating them.
--battery PCT and --faults random start the houses with an almost flat pack and random robot faults, as tools/fuzz.py
does (the same per-seed rules: fuzz.robot_options), so the robot and sensor checks see powered-down, deaf, blind and
faulty robots.
Violations are attributed to the emitting module (kernel realism, the type's domain, an observer, or an action's
owner). Exit status 1 if any leak is found.
"""
import copy
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
# the keys of realism.view_dict: the "view" a geometric observation shows for every record it saw
CAMERA_VIEW_KEYS = frozenset(("bearing_deg", "elev_deg", "px", "occluded", "via", "light", "range_m", "dist"))
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
                   "last_answer", "messages", "perception", "camera", "lidar", "request", "notices"}
MEMORY_ACTIONS = ("recall", "diff", "snapshot")
SELF_NOTICES = ("battery", "fault", "order")         # the robot's own reports: no microphone needed
ASK_WORDS = ("mug", "blanket", "the remote", "towels", "plant", "keys", "it", "house", "request", "spaceship")
ASK_ASPECTS = ("which", "owner", "where", "preference", "amount", "when", "ok", "keep", "intended", "why", "how",
               "status", "who", "said", "help")
REPORT_WORDS = ("leak", "dripping", "broken", "dirty", "needs water", "zorbled", "wobbly", "low")
IT_WORDS = ("it", "that", "this", "them", "those")
PERSON_KEYS = ("target", "about", "subject", "to", "options")
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
            if field == "view" and isinstance(shown, dict) and "via" in shown and set(shown) <= CAMERA_VIEW_KEYS:
                # the geometric camera's own viewing geometry (realism.view_dict), which replaces the record's view
                # block in the observation: not a leak of that never-tier block (adoption requests manipulation 9,
                # textiles_laundry 5, mechanisms 9, K-CAM)
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
    """The physics inspector's words in an inspect message: feel and looks carry no number or hidden field name. A
    domain's own feel (laundry's "damp" or "dry") or looks (a stain, a filter, a tyre, a pet's coat: plumbing's phase-1
    report) is the more specific percept and takes the key first (phys.observe.inspect keeps it), so looks is not held
    to the physics inspector's "sturdy" / "flimsy" vocabulary: the rule is the leak rule of every inspector."""
    if not isinstance(data, dict):
        return
    hidden = _hidden_fields(env)
    for key, words in (("feel", FEEL_WORDS), ("looks", LOOKS_WORDS)):
        value = data.get(key)
        if value is None:
            continue
        for problem in physics_text_problems(value if isinstance(value, str) else repr(value), hidden, words):
            leaks.append(("physics", "inspector:physics", "{} {} {!r}: {}".format(action, key, value, problem)))


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


def _true_names(record):
    names = {str(record.get("label") or ""), str(record.get("type") or ""), str(record.get("type") or "").replace("_", " ")}
    return {n.lower() for n in names if n}


def check_people_message(env, action, args, message, leaks, issued_before, shown_labels=None):
    """The people-and-memory checks of one action message (TIER1_SWEEP 17)."""
    try:
        data = json.loads(message)
    except (TypeError, ValueError):
        data = None
    realism = env.realism
    if action == "ask" and isinstance(data, dict):
        for key in ("options", "refers_to"):
            for item in data.get(key) or []:
                text = json.dumps(item)
                if HANDLE_IN.search(text):
                    leaks.append(("people", "kernel:dialogue", "ask {}: a handle in {}".format(key, text[:120])))
        about, given = data.get("about"), args.get("about")
        if (isinstance(about, str) and HANDLE_IN.search(about) and about != given and
                not (isinstance(given, str) and given.strip().lower() in IT_WORDS)) or \
                (given == "request" and about not in (None, "request")):
            leaks.append(("people", "kernel:dialogue", "ask about {!r} answered about {}, which the agent did not "
                          "give".format(given, about)))
        if "route" in data:
            leaks.append(("people", "kernel:dialogue", "ask answer carries its route {}".format(data["route"])))
    if action == "recall" and isinstance(data, dict) and shown_labels is not None:
        state = env.world.s
        for handle, belief in sorted(data.items()):
            if not (isinstance(handle, str) and HANDLE.match(handle) and isinstance(belief, dict)):
                continue
            eid = realism.handles.resolve(handle)
            record = state["entities"].get(eid) if isinstance(eid, str) else None
            label = str(belief.get("label") or "").lower()
            if record is None or not label or label in shown_labels.get(handle, ()):
                continue
            ident = realism._identify(eid, record, realism.knowledge.entity_level(eid, record))[0]
            now = str(ident.get("label") or "").lower()
            if label != now and label in _true_names(record) and realism.unresolved(eid, record):
                leaks.append(("people", "kernel:mind", "recall {} remembers {!r}, the true name of a thing it cannot "
                              "tell apart (shown as {!r})".format(handle, label, now)))
    if action in MEMORY_ACTIONS:
        state = env.world.s
        for handle in sorted(set(HANDLE_IN.findall(message or ""))):
            eid = realism.handles.resolve(handle)
            if handle in issued_before or (eid in state["entities"] and env.perception.visible(state, eid)):
                continue
            leaks.append(("people", "kernel:mind", "{} names never-issued {}".format(action, handle)))
    if action == "report" and isinstance(data, dict) and data.get("suggest"):
        from house_sim.kernel import reports
        subject = args.get("subject")
        eid = realism.handles.resolve(subject) if isinstance(subject, str) else None
        record = env.world.s["entities"].get(eid) if eid else None
        if record is not None and realism.unresolved(eid, record):
            # an unknown issue word gets the category's issue words ("object" while unresolved); an unknown state
            # word gets the one category-free state list - either way every member of a group sees the same list
            generic = {tuple(reports.issue_words("object")[:reports.SUGGESTIONS]),
                       tuple(reports.state_words()[:reports.SUGGESTIONS])}
            if tuple(data["suggest"]) not in generic:
                leaks.append(("people", "kernel:reports", "report suggestions for an unresolved thing differ from the "
                              "generic list: {}".format(data["suggest"][:5])))


def check_people_obs(env, obs, leaks, where, issued_before=None):
    """obs.notices and obs.request (TIER1_SWEEP 17)."""
    from house_sim.kernel import body
    state = env.world.s
    notices = obs.get("notices") or []
    if notices and not body.is_on(state):
        leaks.append(("people", "kernel:notices", "{} notices while powered down".format(where)))
    hearing = body.can_hear(state)
    for notice in notices:
        if not isinstance(notice, dict):
            continue
        kind = notice.get("kind")
        if hearing == "none" and not notice.get("visual") and kind not in SELF_NOTICES and kind != "visual":
            leaks.append(("people", "kernel:notices", "{} audio notice {} while the robot hears nothing".format(where, kind)))
        source = notice.get("source")
        if isinstance(source, str) and HANDLE.match(source) and source not in env.realism.handles.by_handle:
            leaks.append(("people", "kernel:notices", "{} notice source {} never issued".format(where, source)))
    request = obs.get("request")
    if isinstance(request, dict) and obs.get("dark"):
        who = request.get("from")
        room = state["robot"].get("room")
        if who in (state.get("people") or {}) and (state["people"][who] or {}).get("location") == room \
                and request.get("channel") in ("in_person", "overheard"):
            leaks.append(("people", "kernel:request", "{} request names {} in the dark".format(where, who)))


def check_typed_people(env, action, args, result, obs_before, leaks):
    """A person's id the agent typed while the robot's room was dark (shapes in it) never comes back as that person's
    shape handle; a wait(until) is not accepted on what the robot does not perceive now: a thing not in view, a dark
    shape's field, or a person while the robot's room is dark (TIME-EVAL perceivability, TIER1_INTEGRATION 1.3)."""
    realism = env.realism
    state = env.world.s
    people = set(state.get("people") or {}) | set(state.get("pets") or {})
    dark = bool(obs_before.get("dark"))
    typed = set()
    for key in PERSON_KEYS:
        value = args.get(key)
        for item in (value if isinstance(value, list) else [value]):
            if isinstance(item, str) and item in people:
                typed.add(item)
    message = result.get("message") or ""
    if dark and typed and result.get("ok"):
        for handle in set(HANDLE_IN.findall(message)):
            eid = realism.handles.resolve(handle)
            if isinstance(eid, str) and eid.startswith("shape:") and eid.split(":")[1] in typed:
                leaks.append(("people", "kernel:realism", "{} typed {} in the dark and got its shape {}".format(
                    action, eid.split(":")[1], handle)))
    until = args.get("until") if action == "wait" else None
    if not (isinstance(until, dict) and result.get("ok") and isinstance(until.get("path"), str)):
        return
    parts = until["path"].split(".")
    visible = obs_before.get("visible_entities") or {}
    eid = realism.handles.resolve(parts[1]) if parts[0] == "entities" and len(parts) >= 3 else None
    true = state["entities"].get(eid) if isinstance(eid, str) else None
    if true is not None and not (true.get("remote_status") or true.get("audible_status")):
        record = visible.get(parts[1])
        if record is None:
            leaks.append(("people", "kernel:timeline", "wait until on {} accepted while it is not in view".format(
                parts[1])))
        elif record.get("label") == "dark shape":
            leaks.append(("people", "kernel:timeline", "wait until on a dark shape's {} accepted".format(parts[2])))
    if parts[0] in ("people", "pets") and dark and (obs_before.get("people") or obs_before.get("pets")):
        leaks.append(("people", "kernel:timeline", "wait until on {} accepted in the dark".format(parts[1])))


def people_command(rng, obs, handles, state):
    """A structured people-and-memory command for the walk."""
    people = sorted(obs.get("people") or {})
    everyone = sorted(state.get("people") or {})
    if obs.get("dark") and people and everyone and rng.random() < 0.5:
        # a person's id typed while the robot cannot tell who is who (it must never come back as their shape)
        pid = rng.choice(everyone)
        if rng.random() < 0.6:
            return {"action": "ask", "target": rng.choice(people), "aspect": rng.choice(["where", "status", "who"]),
                    "about": pid}
        return {"action": "report", "kind": "status", "subject": pid, "state": "asleep", "to": "household"}
    roll = rng.random()
    if roll < 0.12:
        # wait(until): on a handle (in view or remembered), a dark shape's field, a person's posture or coming / going
        choices = []
        for handle in handles + sorted(obs.get("_issued") or ())[:20]:
            choices.append({"path": "entities.{}.open".format(handle), "op": "eq", "value": rng.random() < 0.5})
        for pid in everyone:
            choices.append({"path": "people.{}.posture".format(pid), "op": "eq", "value": "sitting"})
            choices.append({"path": "people.{}.location".format(pid), "op": rng.choice(["eq", "ne"]),
                            "value": obs["room_id"]})
        if choices:
            return {"action": "wait", "seconds": 30, "until": rng.choice(choices)}
    if roll < 0.45 and people:
        about = rng.choice(handles + list(ASK_WORDS) + [obs["room_id"]] + people + everyone) if handles else \
            rng.choice(list(ASK_WORDS) + everyone)
        if rng.random() < 0.25:
            about = "request"           # the request's hidden target must not come back as the answer's about
        cmd = {"action": "ask", "target": rng.choice(people), "aspect": rng.choice(ASK_ASPECTS), "about": about}
        if rng.random() < 0.3:
            cmd["detail"] = rng.choice(["strength", "fold_style", "care", "rules", "pillows"])
        if cmd["aspect"] == "ok":
            cmd["act"] = rng.choice(["pick_up", "open", "take", "discard"])
        if rng.random() < 0.2 and handles:
            cmd["options"] = rng.sample(handles, min(2, len(handles)))
        return cmd
    if roll < 0.75 and handles:
        kind = rng.choice(["finding", "finding", "status", "measurement", "count", "none_found"])
        cmd = {"action": "report", "kind": kind}
        if kind in ("finding", "status", "measurement"):
            cmd["subject"] = rng.choice(handles + everyone[:2]) if kind == "status" else rng.choice(handles)
        if kind in ("finding", "none_found"):
            cmd["issue"] = rng.choice(REPORT_WORDS)
        if kind == "status":
            cmd["state"] = rng.choice(["open", "done", "frobbed"])
        if kind == "measurement":
            cmd.update(value=21.5, unit=rng.choice(["C", "F", "kg", "furlong"]))
        if kind == "count":
            cmd.update(of="towel", value=3, scope=obs["room_id"])
        if kind == "none_found":
            cmd["scope"] = obs["room_id"]
        if people and rng.random() < 0.4:
            cmd["to"] = rng.choice(people)
        elif everyone and rng.random() < 0.3:
            cmd["to"] = rng.choice(everyone)        # perhaps away: phone and text reach them
            cmd["channel"] = rng.choice(["phone", "text", "app"])
        return cmd
    form = rng.choice(["map", "seen:", "seen:", "seen:", "seen:" + (rng.choice(handles) if handles else "x"), "snap:",
                       "", "snapshot", "diff"])
    if form == "snapshot":
        return {"action": "snapshot", "target": "audit"}
    if form == "diff":
        return {"action": "diff", "target": "audit"}
    return {"action": "recall", "target": form}


# ----- the grant gate (review I3 / R4) --------------------------------------------------------------------------------

def audit_grant_problem(env, eid):
    """Why the robot could not have looked closely at entity eid from where it is now (None when it could): the rule
    of core inspect, computed here so it also judges a tree whose kernel has no grant gate -- held things always; the
    geometric camera: camera.inspect_check (the head resolving it to 160 px in light, or the wrist within reach); the
    room camera: arm's reach, a large fixture (1 m or more) or an inspect_from_afar thing."""
    from house_sim.kernel import geometry
    state = env.world.s
    record = state["entities"].get(eid)
    if record is None or _held_or_worn(state, eid):
        return None
    if isinstance(state["robot"].get("view"), dict):
        from house_sim.kernel import camera
        ok, reason, _via = camera.inspect_check(state, eid, env.world)
        return None if ok else (reason or "not resolved")
    problem = geometry.reach_problem(state, eid)
    if problem is None or "Crouch" in problem or "Too high" in problem:
        return None
    size = max((float(v) for v in (record.get("size_m") or [0.1])[:3]), default=0.1)
    if "Approach" in problem and (size >= 1.0 - 1e-9 or record.get("inspect_from_afar")):
        return None
    return problem


def _inspect_levels(realism):
    """{eid: entity knowledge level} of every thing the robot knows at inspect level or better."""
    from house_sim.kernel.realism import INSPECT
    return {eid: entry.get("level", 0) for eid, entry in realism.knowledge.data.items()
            if isinstance(entry, dict) and entry.get("level", 0) >= INSPECT}


def check_grants(env, action, before, leaks):
    """A step that raised the robot's knowledge of a thing to inspect level (or better) while the robot could not have
    looked closely at it from where it is (audit_grant_problem): knowledge laundered through an action's grant, which
    the per-value tier rule cannot see (review H7 / I3: `count <room>` as a remote inspect). The walk calls it for
    steps that left the robot where it stood."""
    from house_sim.kernel.plugins import OWNERS
    after = _inspect_levels(env.realism)
    for eid in sorted(after):
        if after[eid] <= before.get(eid, -1):
            continue
        problem = audit_grant_problem(env, eid)
        if problem is not None:
            leaks.append(("grant", "action:" + OWNERS.get(("action", action), "?"),
                          "{} granted a close look at {} from where the robot is ({})".format(action, eid, problem)))


# ----- differential probes (K-LEAK; review sections 1 and 4: C-ORACLE, R1, R5, R6) -----------------------------------
#
# The walk above judges each output alone, so it cannot see a leak that lives in a contrast: two worlds that differ only
# in a hidden fact and answer differently. These probes build such twins with the task runner's setup ops and run a
# fixed agent-side battery in each (handles taken from observations; the harness only builds the twins, maps handles
# back to true ids to compare them, and changes hidden state where a probe says so):
#   oracle   (C-ORACLE) twins whose true values differ but render the same -- a glance-tier wetness and fill level,
#            a person asleep or lying awake, a pet asleep or resting: wait(seconds=1, until=...) with every predicate op
#            on values from both truths and their prefixes, and an auto standing order on the posture, must give
#            identical (ok, category, message, clock step) in both
#   twin     (R1) members of a look-alike group with different true sizes, and two unfamiliar ("novel") things with the
#            same visual props, at the same place under the same id: the camera's rendering of them (identity keys,
#            size, colour, the view's pixels, whether it is listed) must be identical while they are unresolved
#            (--twin-fields compares every field, which also lists the type-data tells of the registry lint's class)
#   episode  (R5 and SECRECY) a handle shown in one episode and not issued in a second episode of the same house and
#            seed must be refused there, in an entity parameter and in free text; two episodes with different
#            episode nonces must share no handle
#   echo     (R6) twins with and without things whose ids are hand-written or id-shaped (letter_6, parcel_9_item_1,
#            spare_soap_jug, m9), and a thing destroyed out of view versus one merely out of view: speak, remember,
#            recall, explain, report (subject=), ask (about=) and wait must reply identically (handles compared by
#            role). Free text is probed with every id-like word, the other parameters with words shaped like generated
#            ids (a hand-written id without a number in a non-text parameter is the known residual: authored ids should
#            be id-shaped)
#   target   (phase-1 red team) one unresolved member per twin of a look-alike group, free text listing every member's
#            type name through apply_label must echo identically (the kept "part names" came from the true record);
#            and two unresolved members on one floor cell spawned in either order must be listed in the same hazard
#            order, the order of their handles (review I11; K-GATE2: an order by true id is spawn-independent too),
#            both listed in room mode (else the probe is an error: it proved nothing)
#   read     (phase-1 finish, people's request) every member of every look-alike group in turn, at the same place
#            under the same id with a resident beside it: read and read_aloud must answer in the same form (ok and
#            category) for every member while the thing is unresolved before the step -- the text itself may differ,
#            reading a label is how a read-resolved look-alike is told apart (a refusal for one member alone named it:
#            the pill-bottle members, found by hand while G4 stayed green)
# Unfamiliar ("novel") twins are compared on every field (their glance and inspect key sets are fixed) and on the
# outcome of operating them (open, switch_on, switch_off, take_photo, read): review I4.

PROBE_SPEC = {"catalog_id": "T-auditprobe-01", "title": "perception audit probe", "perception": "realistic",
              "house": {"residents": "minimal", "pets": "none"}, "instruction": "probe",
              "phrasings": ["probe", "probe.", "probe!"], "success": [{"path": "time_s", "op": "gt", "value": 1e12}],
              "risk_level": 1, "fidelity": "S", "budget": {"steps": 100000, "time_s": 10 ** 7}, "reference": [],
              "wrong": []}
ORACLE_OPS = ("eq", "ne", "in", "not_in", "startswith", "contains", "is_true", "is_false")
TWIN_KEYS = ("label", "kind", "candidates", "shape", "size_class", "size_m", "color")
TWIN_GROUPS = 6           # look-alike groups with a size spread twin-tested per audit run
ECHO_IDS = ("letter_6", "parcel_9_item_1", "spare_soap_jug", "m9")     # free text: every id-like word
ECHO_SHAPED_IDS = ("letter_6", "parcel_9_item_1")    # other parameters: words shaped like generated ids
# the unfamiliar things the novel twin probe compares (real registry types: an unknown name was silently dropped
# before, so only two were twinned -- phase-1 red team); every one must exist when every domain is loaded. The two
# plain spray bottles carry a blank label (phase-1 close-out, mechanisms' report: an unfamiliar one read
# {"label_text": ""} where an unfamiliar kettle had nothing legible, and no twin here had a blank label)
NOVEL_TWINS = ("electric_kettle", "smartphone", "hair_dryer", "remote_control", "spray_bottle", "trigger_bottle")
NOVEL_OPERATE = (("open", {}), ("switch_on", {}), ("switch_off", {}), ("take_photo", {}), ("read", {}))
READ_OPERATE = (("read", {}), ("read_aloud", {}))     # the read probe's battery (compared by outcome form)
# the look-alike group of the target-name echo probe: its member types (all must be known for the probe to run)
TARGET_ECHO_GROUP = ("chlorine_bleach_bottle", "liquid_detergent", "fabric_softener")
AGENT_SHOWN_KEYS = ("label", "species", "role", "posture", "activity", "cues")


def _probe_env(setup, seed, camera, task_extra=None, mutate=None):
    """A realistic env of a generated house with setup ops (tasks/runner.py build + make_env: the verifier's path)."""
    from house_sim.tasks.runner import build, make_env
    spec = dict(copy.deepcopy(PROBE_SPEC), setup=setup)
    if camera == "geometric":
        spec["camera"] = "geometric"
    config, task, oracle = build(spec, seed=seed, camera=camera, perception="realistic")
    bindings = oracle.get("bindings", {}) if isinstance(oracle, dict) else {}
    if mutate is not None:
        mutate(config, bindings)
    if task_extra:
        task.update(task_extra)
    env = make_env(config, task, seed, fast=False, perception="realistic", camera=camera)
    return env, bindings


def _agent_step(env, action, target="", destination="", **kwargs):
    """One agent step: (ok, category, message, seconds the agent's clock advanced)."""
    t0 = env.observe().get("time_s")
    result = env.step(action, target, destination, **kwargs)
    t1 = (result.get("observation") or env.observe()).get("time_s")
    return (bool(result.get("ok")), result.get("category") or "", result.get("message") or "",
            round(float(t1 or 0) - float(t0 or 0), 3))


def _canon(env, value):
    """Handles mapped to the true ids they stand for (harness side, to compare twins built with the same ids)."""
    realism = env.realism
    if isinstance(value, str):
        return HANDLE_IN.sub(lambda m: "<{}>".format(realism.handles.by_handle.get(m.group(0), "?")), value)
    if isinstance(value, list):
        return [_canon(env, v) for v in value]
    if isinstance(value, dict):
        return {_canon(env, k) if isinstance(k, str) else k: _canon(env, v) for k, v in value.items()}
    return value


def _handle_of(env, obs, eid):
    """The handle observation `obs` shows for eid, if it shows it (harness side: never issues a handle)."""
    handle = env.realism.handles.by_id.get(eid)
    return handle if handle and handle in (obs.get("visible_entities") or {}) else None


def _first_room(seed, rtype="living_room"):
    return {"op": "room", "var": "lr", "type": rtype}


def _type(name):
    """A domain type the probes use, or the kernel's generic_item when the domains that define it are not loaded
    (HOUSE_SIM_DOMAINS=core): the probed fields are kernel tiers (wetness, level, open), so the probe still holds."""
    from house_sim.kernel.registry import default_registry
    return name if default_registry().known(name) else "generic_item"


def _probe_oracle(seed, camera):
    """C-ORACLE: wait(until) / watch on twins that render alike. Each target is a section: what the observation shows of
    it, then the answers. A section counts only where both twins show the target alike (people and pets act on their
    own, so a twin whose sleeper woke up or whose dog wandered off compares nothing; a thing that renders differently
    makes the probe itself wrong, reported as an error)."""
    leaks, errors = [], []
    truths = {"a": {"wet": 0.1732, "level": 0.4371, "person": "sleeping", "pet": "sleeping"},
              "b": {"wet": 0.0321, "level": 0.3611, "person": "lying", "pet": "resting"}}
    runs = {}
    for twin, truth in sorted(truths.items()):
        setup = [_first_room(seed),
                 {"op": "spawn", "var": "u", "type": _type("rain_umbrella"), "floor": "{lr}",
                  "eid": "probe_umbrella", "props": {"wetness": truth["wet"], "size_m": [0.1, 0.05, 0.8]}},
                 {"op": "spawn", "var": "b", "type": _type("utility_bucket"), "floor": "{lr}", "eid": "probe_bucket",
                  "props": {"level": truth["level"], "size_m": [0.3, 0.3, 0.35]}},
                 {"op": "robot", "room": "{lr}", "near": "{u}"},
                 {"op": "person", "id": "probe_sleeper", "role": "adult", "room": "{lr}",
                  "props": {"posture": truth["person"], "awake": truth["person"] != "sleeping",
                            "activity": "sleeping" if truth["person"] == "sleeping" else "resting"}},
                 {"op": "pet", "id": "probe_dog", "room": "{lr}",
                  "props": {"species": "dog", "label": "Rex", "posture": truth["pet"],
                            "activity": "sleeping" if truth["pet"] == "sleeping" else "resting"}},
                 {"op": "lights", "room": "{lr}", "on": True}]
        if camera == "geometric":
            setup.append({"op": "camera", "face": "{u}"})
        try:
            env, _ = _probe_env(setup, seed, camera)
        except Exception as exc:  # noqa: BLE001
            errors.append("oracle probe build: {}: {}".format(type(exc).__name__, exc))
            return leaks, errors
        sections = []
        for group, aid, values in (("people", "probe_sleeper", ("sleeping", "lying", "resting", "sitting")),
                                   ("pets", "probe_dog", ("sleeping", "resting", "lying", "standing"))):
            if camera == "geometric":
                _agent_step(env, "face", aid)
            obs = env.observe()
            shown = (obs.get(group) or {}).get(aid)
            section = [("shown", aid, {k: v for k, v in shown.items() if k in AGENT_SHOWN_KEYS}
                        if isinstance(shown, dict) else shown, True)]
            for op in ("eq", "ne", "in", "startswith"):
                for value in values:
                    value = [value] if op == "in" else value
                    section.append((aid, "posture", op, value, _agent_step(
                        env, "wait", seconds=1, until={"path": "{}.{}.posture".format(group, aid), "op": op,
                                                       "value": value})))
            if aid == "probe_sleeper":
                order = _agent_step(env, "watch", condition={"path": "people.probe_sleeper.posture", "op": "in",
                                                            "value": ["sleeping"]},
                                    commands=[{"action": "look"}], mode="auto")
                section.append(("watch", "posture in sleeping", order, _agent_step(env, "wait", seconds=60)))
            sections.append(section)
        for eid, field, values in (("probe_umbrella", "wetness",
                                    (0.1732, 0.0321, "0.1", "0.17", "0.0", "looks dry", "wet")),
                                   ("probe_bucket", "level", (0.4371, 0.3611, "0.4", "0.3", "half", "low", "full"))):
            if camera == "geometric":
                handle = env.realism.handles.by_id.get(eid)
                if handle:
                    _agent_step(env, "face", handle)
                    _agent_step(env, "look", handle)
            obs = env.observe()
            handle = _handle_of(env, obs, eid)
            shown = (obs.get("visible_entities") or {}).get(handle) if handle else None
            section = [("shown", eid, _canon(env, shown), False)]
            if handle is not None:
                for op in ORACLE_OPS:
                    for value in values:
                        if op in ("in", "not_in"):
                            value = [value]
                        elif op in ("startswith", "contains"):
                            value = str(value)
                        section.append((eid, field, op, value, _agent_step(
                            env, "wait", seconds=1, until={"path": "entities.{}.{}".format(handle, field), "op": op,
                                                           "value": value})))
            sections.append(section)
        runs[twin] = sections
    for left_section, right_section in zip(runs.get("a", []), runs.get("b", [])):
        head, other = left_section[0], right_section[0]
        if head[2] != other[2]:
            if not head[3]:
                errors.append("oracle probe twins render {} differently: {} / {}".format(head[1], str(head[2])[:160],
                                                                                        str(other[2])[:160]))
            continue
        for left, right in zip(left_section[1:], right_section[1:]):
            if left != right:
                leaks.append(("oracle", "kernel:timeline", "{} answers differently for twins that look the same: {} vs "
                              "{}".format(" ".join(str(x) for x in left[:-1]), left[-1], right[-1])))
    return leaks, errors


def _px_size_problem(rec):
    """What a geometric record's view gives away about the thing's size beyond its record (review L2): the size that
    px x range / focal implies differs from the record's shown size by more than the camera's own precision (5 cm
    estimates, pixels to 2 significant figures, range to 0.1 m), else None."""
    from house_sim.kernel import sight
    view = rec.get("view") if isinstance(rec, dict) else None
    size = rec.get("size_m") if isinstance(rec, dict) else None
    if not (isinstance(view, dict) and isinstance(size, list) and size and view.get("px") and view.get("range_m")):
        return None
    try:
        shown = max(float(v) for v in size[:3])
        implied = float(view["px"]) * float(view["range_m"]) / float(sight.HW["head"]["focal_px"])
    except (TypeError, ValueError):
        return None
    if abs(implied - shown) > 0.15 * shown + 0.03:
        return "px x range implies {:.2f} m while the record shows {:.2f} m".format(implied, shown)
    return None


def _twin_battery(env, eid, camera, fields, operate=()):
    """The passive battery of a twin: the reset view; with the geometric camera a fixed scan (body yaws and head
    tilts, as an agent looking for it would turn) when it is not in view yet, then a look at it; approaching it and a
    wait. Each time the target's rendering (TWIN_KEYS, or every field but the view with fields=True) or that it is not
    listed, whether the robot can tell what it is, and what its view's pixels imply about its size (_px_size_problem).
    The view itself is not compared: where the thing is (bearing, range, occlusion) follows its true shape."""
    out = []

    def listed():
        handle = env.realism.handles.by_id.get(eid)
        return bool(handle) and handle in (env.observe().get("visible_entities") or {})

    def snap(tag):
        obs = env.observe()
        handle = env.realism.handles.by_id.get(eid)
        rec = (obs.get("visible_entities") or {}).get(handle) if handle else None
        if rec is None:
            out.append((tag, "not listed", None, None))
            return
        shown = {k: v for k, v in rec.items() if k != "view" and (fields or k in TWIN_KEYS)}
        out.append((tag, _canon(env, shown), env.realism.unresolved(eid), _px_size_problem(rec)))
    snap("reset")
    if camera == "geometric":
        for scan, (deg, tilt) in enumerate((d, t) for d in (None, 0, 90, 180, 270) for t in (0, -25, -45)):
            if listed():
                out.append(("scan", scan, None, None))
                break
            if deg is not None and tilt == 0:
                _agent_step(env, "face", deg=deg)
            _agent_step(env, "look", pan=0, tilt=tilt)
        else:
            out.append(("scan", "not found", None, None))
    handle = env.realism.handles.by_id.get(eid)
    if camera == "geometric" and handle:
        _agent_step(env, "look", handle)
        snap("look")
    if handle:
        _agent_step(env, "approach", handle)
        snap("approach")
        for action, args in operate:
            # what operating it answers (an unfamiliar thing: one refusal whatever it is, review I4); a step that
            # succeeded on one twin and not the other already differs here
            ok, category, message, dt = _agent_step(env, action, handle, **args)
            out.append((action, (ok, category, _canon(env, message), dt), env.realism.unresolved(eid), None))
    _agent_step(env, "wait", seconds=30)
    snap("wait")
    return out


def _probe_twins(seed, camera, kind, members, fields=False):
    """R1: twins differing only in the member type (or, for unfamiliar things, the true type behind the same visual
    props) of the thing at eid probe_twin_1: while the thing is unresolved in both, its rendering and whether it is
    listed must be the same, and its view's pixels must not imply another size than its record shows."""
    leaks, errors = [], []
    runs = {}
    for type_name in members:
        props = {}
        if kind == "novel":
            props = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "grey", "material": "plastic"}
        # on the floor beside the robot, in the open and in frame (the head turned to it and tilted down), so nothing
        # but the twins' own difference can change what the camera shows
        setup = [_first_room(seed),
                 {"op": "spawn", "var": "x", "type": type_name, "floor": "{lr}", "eid": "probe_twin_1", "props": props},
                 {"op": "robot", "room": "{lr}", "near": "{x}"},
                 {"op": "lights", "room": "{lr}", "on": True}]
        if camera == "geometric":
            setup.append({"op": "camera", "face": "{x}", "tilt": -60})
        try:
            env, _ = _probe_env(setup, seed, camera)
        except Exception as exc:  # noqa: BLE001
            errors.append("twin probe {} {}: {}: {}".format(kind, type_name, type(exc).__name__, str(exc)[:160]))
            continue
        novel = kind == "novel"
        runs[type_name] = _twin_battery(env, "probe_twin_1", camera, fields or novel,
                                        NOVEL_OPERATE if novel else ())
    names = sorted(runs)
    for name in names:
        for tag, _shown, unresolved, problem in runs[name]:
            if problem and unresolved:
                leaks.append(("twin", "kernel:realism", "{} {} at {}: {}".format(kind, name, tag, problem)))
    for other in names[1:]:
        for left, right in zip(runs[names[0]], runs[other]):
            if left[:3] == right[:3]:
                continue
            unresolved = bool(left[2]) and bool(right[2])
            if left[1] == "not listed" or right[1] == "not listed" or unresolved or left[0] == "scan":
                leaks.append(("twin", "kernel:realism", "{} {} vs {} at {}: {} / {}".format(
                    kind, names[0], other, left[0], str(left[1])[:200], str(right[1])[:200])))
    return leaks, errors


def _probe_reads(seed, camera, group, members):
    """Read twins (phase-1 finish, people's request): each member of a look-alike group in turn as probe_twin_1 on the
    floor beside the robot with a resident in the room; approach, read, read aloud. While the thing is unresolved
    before a step, every member must get the same (ok, category) from it: one member refused alone tells it apart."""
    leaks, errors = [], []
    runs = {}
    for type_name in members:
        setup = [_first_room(seed),
                 {"op": "spawn", "var": "x", "type": type_name, "floor": "{lr}", "eid": "probe_twin_1"},
                 {"op": "robot", "room": "{lr}", "near": "{x}"},
                 {"op": "person", "id": "probe_resident", "role": "adult", "room": "{lr}"},
                 {"op": "lights", "room": "{lr}", "on": True}]
        if camera == "geometric":
            setup.append({"op": "camera", "face": "{x}", "tilt": -60})
        try:
            env, _ = _probe_env(setup, seed, camera)
        except Exception as exc:  # noqa: BLE001
            errors.append("read probe {} {}: {}: {}".format(group, type_name, type(exc).__name__, str(exc)[:160]))
            continue
        handle = env.realism.handles.by_id.get("probe_twin_1")
        if not handle:
            runs[type_name] = [("listed", False, False)]
            continue
        out = [("listed", True, bool(env.realism.unresolved("probe_twin_1")))]
        _agent_step(env, "approach", handle)
        for action, args in READ_OPERATE:
            before = bool(env.realism.unresolved("probe_twin_1"))
            ok, category, _message, _dt = _agent_step(env, action, handle, **args)
            out.append((action, (ok, category), before))
        runs[type_name] = out
    from house_sim.kernel.registry import default_registry
    reg = default_registry()
    names = sorted(runs)
    for other in names[1:]:
        for left, right in zip(runs[names[0]], runs[other]):
            # attributed to the type data of the member that answers otherwise (refused, or not listed)
            odd = other if (right[1] is False or (isinstance(right[1], tuple) and right[1][0] is False)) else names[0]
            if left[0] == "listed":
                if left[1] != right[1] and (left[2] or right[2]):
                    leaks.append(("read", _owner_of_type(reg, odd), "lookalike:{} {} vs {}: listed {} / {}".format(
                        group, names[0], other, left[1], right[1])))
                continue
            if left[1] != right[1] and left[2] and right[2]:
                leaks.append(("read", _owner_of_type(reg, odd), "lookalike:{} {} vs {} at {}: {} / {}".format(
                    group, names[0], other, left[0], left[1], right[1])))
    return leaks, errors


def _probe_episodes(seed, camera):
    """R5 and SECRECY: handles of one episode in another episode of the same house and seed."""
    leaks, errors = [], []
    setup = [_first_room(seed), {"op": "lights", "room": "{lr}", "on": True}]
    try:
        first, _ = _probe_env(setup, seed, camera)
        seen = set()
        obs = first.observe()
        seen.update(obs.get("visible_entities") or {})
        if camera == "geometric":
            for where in ("around", "left", "right"):
                result = first.step("look", where)
                seen.update((result.get("observation") or {}).get("visible_entities") or {})
        exits = (obs.get("room") or {}).get("exits") or []
        if exits:
            result = first.step("move", exits[0])
            seen.update((result.get("observation") or {}).get("visible_entities") or {})
        second, _ = _probe_env(setup, seed, camera)
        shown = set(HANDLE_IN.findall(json.dumps(second.observe())))
        foreign = sorted(h for h in seen if h not in shown and h not in second.realism.handles.by_handle)[:4]
        for handle in foreign:
            for action, args in (("approach", {"target": handle}), ("inspect", {"target": handle}),
                                 ("speak", {"text": "where is {}".format(handle)})):
                ok, _category, message, _dt = _agent_step(second, action, **args)
                if ok:
                    leaks.append(("episode", "kernel:realism", "{} accepted {}, a handle of another episode never "
                                  "issued in this one: {}".format(action, handle, message[:80])))
        handles = []
        for nonce in ("audit-nonce-a", "audit-nonce-b"):
            env, _ = _probe_env(setup, seed, camera, task_extra={"episode_nonce": nonce})
            handles.append(set(HANDLE_IN.findall(json.dumps(env.observe()))))
        if handles[0] & handles[1]:
            leaks.append(("episode", "kernel:realism", "{} handle(s) shared by two episodes with different nonces "
                          "(e.g. {})".format(len(handles[0] & handles[1]), sorted(handles[0] & handles[1])[0])))
    except Exception as exc:  # noqa: BLE001
        errors.append("episode probe: {}: {}".format(type(exc).__name__, str(exc)[:200]))
    return leaks, errors


def _probe_echo(seed, camera):
    """R6: replies to id-shaped words and to handles of things out of view or destroyed."""
    leaks, errors = [], []
    texts = ["is {} here".format(i) for i in ECHO_IDS]
    runs = {}
    for twin in ("with", "without"):
        setup = [_first_room(seed), {"op": "room", "var": "k", "type": "kitchen"},
                 {"op": "spawn", "var": "a", "type": _type("apple"), "floor": "{lr}", "eid": "probe_apple_1"},
                 {"op": "spawn", "var": "c", "type": _type("apple"), "floor": "{lr}", "eid": "probe_apple_2"},
                 {"op": "robot", "room": "{lr}", "near": "{a}"},
                 {"op": "person", "id": "probe_resident", "role": "adult", "room": "{k}", "props": {"has_phone": True}},
                 {"op": "lights", "room": "{lr}", "on": True}]
        if twin == "with":
            setup += [{"op": "spawn", "var": "p{}".format(i), "type": _type("cardboard_box"), "floor": "{k}",
                       "eid": eid} for i, eid in enumerate(ECHO_IDS)]
        if camera == "geometric":
            setup.append({"op": "camera", "face": "{a}"})
        try:
            env, _ = _probe_env(setup, seed, camera)
        except Exception as exc:  # noqa: BLE001
            errors.append("echo probe build: {}: {}".format(type(exc).__name__, str(exc)[:200]))
            return leaks, errors
        out = []
        for text in texts:
            out.append(("speak", text, _agent_step(env, "speak", text=text)))
            out.append(("explain", text, _agent_step(env, "explain", text=text)))
        for word in ECHO_SHAPED_IDS:
            out.append(("remember", word, _agent_step(env, "remember", "note:{}".format(word), value=word)))
            out.append(("report", word, _agent_step(env, "report", kind="finding", subject=word, issue="leak")))
            out.append(("wait", word, _agent_step(env, "wait", seconds=1, until={
                "path": "entities.{}.open".format(word), "op": "eq", "value": True})))
        runs[twin] = out
        if twin == "with":
            # a thing destroyed out of view answers like one that is merely out of view (handles compared by role)
            obs = env.observe()
            kept, gone = _handle_of(env, obs, "probe_apple_1"), _handle_of(env, obs, "probe_apple_2")
            exits = (obs.get("room") or {}).get("exits") or []
            if kept and gone and exits:
                env.step("move", exits[0])
                env.world.begin()
                env.world.destroy("probe_apple_2")
                env.world.commit()
                for handle, role in ((kept, "out_of_view"), (gone, "destroyed")):
                    reply = []
                    for action, args in (("speak", {"text": "where is {}".format(handle)}),
                                         ("explain", {"text": "about {}".format(handle)}),
                                         ("remember", {"target": "where:{}".format(handle), "value": "kitchen"}),
                                         ("recall", {"target": "seen:{}".format(handle)}),
                                         ("report", {"kind": "finding", "subject": handle, "issue": "missing"}),
                                         ("ask", {"target": "probe_resident", "about": handle, "aspect": "where",
                                                  "channel": "text"}),
                                         ("wait", {"seconds": 1, "until": {"path": "entities.{}.open".format(handle),
                                                                          "op": "eq", "value": True}})):
                        ok, category, message, dt = _agent_step(env, action, **args)
                        reply.append((action, ok, category, message.replace(handle, "<H>"), dt))
                    runs[role] = reply
    for left, right in zip(runs.get("with", []), runs.get("without", [])):
        if left != right:
            leaks.append(("echo", "kernel:realism", "{} {!r} replies differently when {} exists: {} vs {}".format(
                left[0], left[1], left[1], left[2][:3], right[2][:3])))
    for left, right in zip(runs.get("out_of_view", []), runs.get("destroyed", [])):
        if left != right:
            leaks.append(("echo", "kernel:realism", "{} on a handle answers differently for a destroyed thing: {} vs "
                          "{}".format(left[0], left[1:4], right[1:4])))
    return leaks, errors


def _probe_target(seed, camera):
    """Phase-1 red team: free text naming every member type of an unresolved look-alike (apply_label) must echo the
    same for every member; two unresolved members on one floor cell must be listed in the same hazard order whatever
    order they were spawned in (review I11)."""
    leaks, errors = [], []
    from house_sim.kernel.plugins import ACTIONS
    from house_sim.kernel.registry import default_registry
    reg = default_registry()
    members = [t for t in TARGET_ECHO_GROUP if reg.known(t)]
    if len(members) == len(TARGET_ECHO_GROUP) and "apply_label" in ACTIONS:
        text = " ".join(members)
        echoes = {}
        for type_name in members:
            setup = [_first_room(seed),
                     {"op": "spawn", "var": "x", "type": type_name, "floor": "{lr}", "eid": "probe_target_1"},
                     {"op": "robot", "room": "{lr}", "near": "{x}"},
                     {"op": "lights", "room": "{lr}", "on": True}]
            if camera == "geometric":
                setup.append({"op": "camera", "face": "{x}", "tilt": -60})
            try:
                env, _ = _probe_env(setup, seed, camera)
            except Exception as exc:  # noqa: BLE001
                errors.append("target probe {}: {}: {}".format(type_name, type(exc).__name__, str(exc)[:160]))
                return leaks, errors
            handle = env.realism.handles.by_id.get("probe_target_1")
            if camera == "geometric" and handle:
                _agent_step(env, "look", handle)
            obs = env.observe()
            handle = _handle_of(env, obs, "probe_target_1")
            if handle is None or not env.realism.unresolved("probe_target_1"):
                echoes[type_name] = ("not shown unresolved", None)
                continue
            _agent_step(env, "approach", handle)
            ok, category, message, dt = _agent_step(env, "apply_label", handle, text=text)
            echoes[type_name] = ((ok, category, _canon(env, message), dt), env.realism.unresolved("probe_target_1"))
        base = members[0]
        for other in members[1:]:
            if echoes.get(other) != echoes.get(base):
                leaks.append(("target", "kernel:realism", "free text naming every member echoes differently for {} and "
                              "{}: {} / {}".format(base, other, str(echoes.get(base))[:160],
                                                   str(echoes.get(other))[:160])))
    # the rule over handles (K-GATE2): both spawn orders list the cell's sources alike AND in the order of their
    # handles -- an order by true id is spawn-independent too and passed; in room mode both sources must be listed
    # (a probe that lists nothing proves nothing), the geometric camera does not list these floor items
    orders, handle_orders = [], []
    for order in (("probe_cell_a", "probe_cell_b"), ("probe_cell_b", "probe_cell_a")):
        setup = [_first_room(seed)]
        setup += [{"op": "spawn", "var": eid, "type": "generic_item", "floor": "{lr}", "eid": eid, "cell": [2, 2],
                   "props": {"label": "spray bottle", "looks_like": "spray_bottle", "portable": True,
                             "size_m": [0.1, 0.07, 0.25]}} for eid in order]
        setup += [{"op": "robot", "room": "{lr}", "cell": [2, 1]}, {"op": "lights", "room": "{lr}", "on": True}]
        try:
            env, _ = _probe_env(setup, seed, camera)
        except Exception as exc:  # noqa: BLE001
            errors.append("hazard order probe: {}: {}".format(type(exc).__name__, str(exc)[:160]))
            return leaks, errors
        back = {env.realism.handles.peek(e): e for e in order}
        orders.append([back.get(h.get("source")) for h in (env.observe().get("grid") or {}).get("hazards") or []
                       if h.get("source") in back and list(h.get("cell") or ()) == [2, 2]])
        handle_orders.append([back[h] for h in sorted(back)])
    if orders[0] != orders[1]:
        leaks.append(("target", "kernel:realism", "hazards of one cell listed in spawn order: {} / {}".format(*orders)))
    for listed, by_handle in zip(orders, handle_orders):
        if len(listed) == len(by_handle) and listed != by_handle:
            leaks.append(("target", "kernel:realism", "hazards of one cell not listed in handle order: {} (handle "
                          "order {})".format(listed, by_handle)))
            break
    if camera != "geometric" and any(len(listed) != 2 for listed in orders):
        errors.append("hazard order probe lists {} of the 2 sources sharing a cell in room mode: the check proved "
                      "nothing".format(" / ".join(str(len(listed)) for listed in orders)))
    return leaks, errors


def probe_jobs(first, camera, fields=False):
    """The probe jobs of one audit run (camera None or "room": the room camera)."""
    from house_sim.kernel import plugins
    from house_sim.kernel.registry import default_registry
    plugins.load_domains()
    reg = default_registry()
    jobs = [("probe", "oracle", first, camera, None), ("probe", "episode", first, camera, None),
            ("probe", "echo", first, camera, None), ("probe", "target", first, camera, None)]
    spread = []
    for group, types in sorted(reg.lookalike_types().items()):
        sizes = sorted((max(float(v) for v in (reg.resolve(t).get("size_m") or [0.1])[:3]), t) for t in types)
        if len(sizes) >= 2 and sizes[-1][0] - sizes[0][0] > 0.03:
            # the groups whose members differ most in true size (relative spread) are the most sensitive twins
            spread.append((-sizes[-1][0] / max(sizes[0][0], 1e-3), group,
                           sorted({sizes[0][1], sizes[len(sizes) // 2][1], sizes[-1][1]})))
    for _rank, group, members in sorted(spread)[:TWIN_GROUPS]:
        jobs.append(("probe", "twin", first, camera, ("lookalike:" + group, members, fields)))
    for group, types in sorted(reg.lookalike_types().items()):
        if len(types) >= 2:
            jobs.append(("probe", "read", first, camera, (group, sorted(types))))
    novel = [t for t in NOVEL_TWINS if reg.known(t)]
    if plugins.enabled_domains() is None and len(novel) != len(NOVEL_TWINS):
        # every domain loaded but a twin type is gone: the probe would silently twin fewer things (phase-1 red team)
        jobs.append(("probe", "config", first, camera, "novel twin types missing from the registry: {}".format(
            ", ".join(sorted(set(NOVEL_TWINS) - set(novel))))))
    if len(novel) >= 2:
        jobs.append(("probe", "twin", first, camera, ("novel", novel, fields)))
    return jobs


def run_probe(job):
    _, name, seed, camera, params = job
    from house_sim.kernel import plugins
    plugins.load_domains()
    try:
        if name == "oracle":
            leaks, errors = _probe_oracle(seed, camera)
        elif name == "episode":
            leaks, errors = _probe_episodes(seed, camera)
        elif name == "echo":
            leaks, errors = _probe_echo(seed, camera)
        elif name == "target":
            leaks, errors = _probe_target(seed, camera)
        elif name == "config":
            leaks, errors = [], ["probe configuration: " + str(params)]
        elif name == "read":
            leaks, errors = _probe_reads(seed, camera, *params)
        else:
            kind, members, fields = params
            leaks, errors = _probe_twins(seed, camera, kind, members, fields)
    except Exception as exc:  # noqa: BLE001
        leaks, errors = [], ["probe {}: {}: {}".format(name, type(exc).__name__, traceback.format_exc()[-400:])]
    return {"seed": "probe:{}".format(name), "leaks": leaks, "errors": errors, "steps": 0}


def run_job(job):
    """A house walk or a probe (the process pool's single entry point)."""
    return run_probe(job) if job and job[0] == "probe" else run_house(job)


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
        config.setdefault("notices", [])     # the notice bus on, as in schema-3 episodes: obs.notices and listen
        enrich(config, seed)
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
    shown_labels = defaultdict(set)     # handle -> labels observations showed it with
    obs = env.observe()
    check_obs(env, obs, leaks, "step0", order=order)
    for i in range(steps):
        handles = sorted(obs.get("visible_entities", {}))
        for handle, record in (obs.get("visible_entities") or {}).items():
            if isinstance(record, dict) and record.get("label"):
                shown_labels[handle].add(str(record["label"]).lower())
        obs["_issued"] = sorted(env.realism.handles.by_handle)
        obs_before = obs
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
            if rng.random() < 0.5:         # an interruptible wait: its message lists the notices it heard
                cmd["interrupt_on"] = ["urgent", "person_enters", "person_leaves", "any_sound"]
        elif roll < 0.79 and geometric:
            # the camera's own verbs: look around (any form the look action offers), turn to a thing
            cmd = {"action": "look", "target": rng.choice(["around", "forward", "down", "up", "left", "right"])}
            if handles and "face" in plugins.ACTIONS and rng.random() < 0.3:
                cmd = {"action": "face", "target": rng.choice(handles)}
        elif roll < 0.88:
            # questions, typed reports and the robot's memory (kernel v3.1)
            cmd = people_command(rng, obs, handles, env.world.s)
        elif roll < 0.9 and "count" in plugins.ACTIONS:
            # an action that grants knowledge of many things at once, scoped to the whole room (the grant check)
            scope = obs["room_id"] if rng.random() < 0.7 or not handles else rng.choice(handles)
            cmd = {"action": "count", "target": scope}
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
        issued_before = set(env.realism.handles.by_handle)
        levels_before = _inspect_levels(env.realism)
        where_before = (env.world.s["robot"].get("room"), list(env.world.s["robot"].get("pos") or []))
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
        if (env.world.s["robot"].get("room"), list(env.world.s["robot"].get("pos") or [])) == where_before:
            # (a step that walked may have touched things on its way: the kernel's gate judges those by the walk)
            check_grants(env, action, levels_before, leaks)
        if result.get("ok"):
            check_people_message(env, action, args, result.get("message"), leaks, issued_before, shown_labels)
        check_typed_people(env, action, args, result, obs_before, leaks)
        check_physics_notes(env, _CAPTURED, leaks, "step{}".format(i + 1))
        obs = env.observe()
        check_obs(env, obs, leaks, "step{}".format(i + 1), start_ids=start_ids, order=order)
        check_people_obs(env, obs, leaks, "step{}".format(i + 1))
    return {"seed": seed, "leaks": leaks, "errors": errors, "steps": steps, "order": [list(r[:2]) + [list(r[2]), r[3]] for r in order]}


def enrich(config, seed):
    """Stage-C fix: what the leaks the review found need -- a request whose target the robot was not told (intent with
    candidates, a request subject), prior knowledge of look-alikes seen at a glance (beliefs as tasks/setup.py's
    prior_knowledge op writes them), and, on every other seed, dark rooms with people in them (their lights off and
    their windows covered)."""
    from house_sim.kernel.registry import default_registry
    from house_sim.tasks.setup import _belief
    rng = random.Random("percaudit-enrich:{}".format(seed))
    entities = config["entities"]
    people = config.get("people") or {}
    adults = sorted(p for p, r in people.items() if r.get("role", "adult") == "adult") or sorted(people)
    portable = sorted(e for e, r in entities.items() if r.get("portable") and r.get("type"))
    if portable and adults:
        target = rng.choice(portable)
        kind = entities[target]["type"]
        candidates = [e for e in portable if entities[e]["type"] == kind]
        config.setdefault("user_simulator", {})["intent"] = {"target": target, "candidates": candidates,
                                                              "category": kind, "requester": adults[0]}
        config["requests"] = [{"id": "r1", "phase": None, "from": adults[0], "channel": "in_person",
                               "room": people[adults[0]].get("location"), "t": 0.0, "text": "Bring me that one.",
                               "subject": target, "gesture": None}]
    groups = set()
    for types in default_registry().lookalike_types().values():
        groups.update(types)
    alike = [e for e in sorted(entities) if entities[e].get("type") in groups and entities[e].get("location")]
    if alike:
        prior = config.setdefault("prior_knowledge", {"map": {}, "seen": {}, "snapshots": {}, "records": {},
                                                      "grants": [], "from_s": -86400.0, "source": "setup"})
        for eid in rng.sample(alike, min(12, len(alike))):
            prior["seen"][eid] = _belief(config, eid, -86400.0, "glance")
    if seed % 2 == 0:
        rooms = sorted({r.get("location") for r in people.values() if r.get("location") in config["rooms"]})[:2]
        for record in entities.values():
            if record.get("category") in ("light", "window") and record.get("location") in rooms:
                if record.get("category") == "light":
                    record["on"] = False
                else:
                    record["covered"] = 1.0


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
    gen = argv[argv.index("--gen") + 1] if "--gen" in argv else None
    if gen not in (None, "1", "2"):
        print("--gen must be 1 or 2")
        return 2
    faults = None if faults == "none" else faults
    jobs = [(first + k, dict(OPTS[k % len(OPTS)], **({"gen": int(gen)} if gen else {})), steps, camera, battery,
             faults) for k in range(houses)]
    if "--no-probes" not in argv:
        jobs += probe_jobs(first, camera, fields="--twin-fields" in argv)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_job, jobs))
    probes = sum(1 for r in results if str(r["seed"]).startswith("probe:"))
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
    print("houses {}  steps {}  probes {}  leaks {}  crashes {}{}{}".format(
        len(results) - probes, sum(r["steps"] for r in results), probes, sum(by_kind.values()), len(errors),
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
        with open(out, "w", encoding="utf-8", newline="\n") as f:     # Path.write_text(newline=) is 3.10+
            f.write(json.dumps({"leaks": [dict(seed=r["seed"], kind=k, owner=o, detail=d) for r in results + judged
                                          for k, o, d in r["leaks"]], "errors": errors}, indent=1))
    return 1 if by_kind or errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

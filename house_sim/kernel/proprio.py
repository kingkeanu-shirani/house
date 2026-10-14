"""Proprioception: obs.robot and the top-level obs.battery (TIER1_EMBODIMENT 4.14, TIER1_INTEGRATION 1.12).

Oracle mode shows the robot record exactly: today's keys plus every registered robot field that is not hidden and
is present in the state (fault_state in full, latent faults marked). Realistic mode shows what the robot's own
sensors and BMS report, field by field according to the registry tier:

  exact     the value as it is (posture, hands, speed_mode, power_state, facing_deg, ...; pos is None while the
            robot is unlocalized)
  gauge     battery: the integer gauge, plus battery_state, runtime_min_est and range_m_est
  bucket    battery_temp_c -> battery_temp, camera_clean -> camera, hand_soil -> per-hand words
  detected  arm_health -> arms, legs_health -> legs, sensors -> camera / hearing, fault_state and the fault log ->
            faults (detected faults only)
  hidden    never (and no underscore field, nor any unregistered field)

plus load (joint effort per hand relative to its believed capacity; not a scale), grip, hearing and network.
Domains add their own channels with the proprio hook, fn(state, out, realistic), respecting the tiers.

Stage-A library: BODY-FAULTS calls render() from perception.observe.
"""
import copy
import math

from . import body
from .plugins import run_hooks

LEGACY_KEYS = ("pos", "posture", "hands", "battery", "stopped", "quiet", "privacy_mode", "speed_mode", "standing_on",
               "camera_clean", "network", "faults", "fallen", "docked")
BASIC_KEYS = ("pos", "posture", "hands")
# kernel fields the realistic view renders itself (the rest of the exact tier passes through)
RENDERED = {"pos", "battery", "battery_temp_c", "camera_clean", "network", "arm_health", "legs_health", "sensors",
            "fault_state", "faults", "hand_soil"}
RUNTIME_STEP_MIN = 5
RANGE_STEP_M = 50
TEMP_BUCKETS = ((5.0, "cold"), (40.0, "normal"), (55.0, "warm"))
CAMERA_BUCKETS = ((0.4, "obscured"), (0.6, "dirty"), (0.85, "smudged"))
LOAD_BUCKETS = ((0.25, "light"), (0.60, "moderate"), (0.90, "heavy"))
HEARING_WORDS = {"none": "off", "addressed": "addressed", "full": "full"}


def bucket_camera(clarity):
    """clear (0.85 and above), smudged, dirty, obscured (below 0.4)."""
    for limit, name in CAMERA_BUCKETS:
        if clarity < limit:
            return name
    return "clear"


def bucket_load(ratio):
    """Joint effort relative to capacity: none, light (< 25 %), moderate (< 60 %), heavy (< 90 %), at_limit."""
    if ratio <= 0:
        return "none"
    for limit, name in LOAD_BUCKETS:
        if ratio < limit:
            return name
    return "at_limit"


def bucket_temp(t_c):
    """Pack temperature: cold (< 5 C), normal, warm (> 40 C), hot (> 55 C)."""
    if t_c < TEMP_BUCKETS[0][0]:
        return "cold"
    if t_c > TEMP_BUCKETS[2][0]:
        return "hot"
    if t_c > TEMP_BUCKETS[1][0]:
        return "warm"
    return "normal"


def bucket_soil(slipperiness):
    """How the fingers feel: clean, soiled, slippery (the gripper_slip level)."""
    if slipperiness <= 0:
        return "clean"
    return "slippery" if slipperiness >= body.SLIP_FAULT_AT else "soiled"


_TIER_CACHE = {"size": -1, "exact": ()}


def _exact_fields():
    """Registered exact-tier field names (re-read when a field is registered)."""
    if _TIER_CACHE["size"] != len(body.ROBOT_FIELDS):
        _TIER_CACHE["exact"] = tuple(sorted(n for n, e in body.ROBOT_FIELDS.items()
                                            if e["tier"] == "exact" and not n.startswith("_") and n not in RENDERED))
        _TIER_CACHE["size"] = len(body.ROBOT_FIELDS)
    return _TIER_CACHE["exact"]


def render(env, state, realistic):
    """(obs.robot, obs.battery). Without "proprioception" in env.sensors obs.robot keeps only pos, posture and
    hands (the top-level battery stays)."""
    out, battery = (_realistic if realistic else _oracle)(state)
    if "proprioception" not in getattr(env, "sensors", ("proprioception",)):
        return {k: out[k] for k in BASIC_KEYS if k in out}, battery
    run_hooks("proprio", state, out, realistic)
    return out, battery


def _oracle(state):
    robot = state["robot"]
    out = {k: copy.deepcopy(robot[k]) for k in LEGACY_KEYS if k in robot}
    for name, entry in body.ROBOT_FIELDS.items():
        if name in robot and name not in out and entry["tier"] != "hidden" and not name.startswith("_"):
            out[name] = copy.deepcopy(robot[name])
    return out, round(float(body.get(state, "battery")), 3)


def _believed_full_wh(state):
    """Full energy as the robot believes it: nominal until a degraded pack is detected."""
    record = body.fault(state, "battery_degraded")
    health = float(body.get(state, "battery_health")) if record is not None and record.get("detected") else 1.0
    return float(body.get(state, "battery_wh")) * health


def _floor(value, step):
    return int(math.floor(max(0.0, value) / step)) * step


def _hand_loads(state):
    """hand -> kg share from the settlement cache, recomputed when what the hands hold changed since."""
    cache = body.get(state, "_hand_kg") or {}
    hands = body.get(state, "hands") or {}
    if all((cache.get(h) or [None])[0] == hands.get(h) for h in body.HANDS if hands.get(h) or cache.get(h)):
        return {h: float(cache[h][1]) for h in body.HANDS if hands.get(h)}
    loads = {}
    for eid, held, kg in body.held_loads(state):
        for hand in held:
            loads[hand] = kg / len(held)
    return loads


def _arms(state):
    out = {}
    for hand in body.HANDS:
        health = body.arm_health(state, hand)
        if health <= 0:
            out[hand] = "failed"
        elif health < 1.0 and not body.is_latent(state, "arm_weak_" + hand):
            out[hand] = "weak"
        else:
            out[hand] = "ok"
    return out


def _load(state):
    """Per hand: joint effort relative to the hand's believed capacity (a two-hand item shares both hands')."""
    hands = body.get(state, "hands") or {}
    loads = _hand_loads(state)
    out = {}
    for hand in body.HANDS:
        kg = loads.get(hand, 0.0)
        if hands.get(hand) and hands.get("left") == hands.get("right"):
            cap = body.capacity_kg(state, "both", believed=True) / 2.0
        else:
            cap = body.capacity_kg(state, hand, believed=True)
        out[hand] = bucket_load(kg / cap) if cap > 0 else ("at_limit" if kg > 0 else "none")
    return out


def _faults(state):
    """Detected faults: the active detected records and the robot's own log entries that name no record (legacy
    domain entries, plain notes), each {"kind", "part", "time_s", "logged"}."""
    shown = []
    for entry in body.get(state, "faults") or []:
        if isinstance(entry, dict) and entry.get("event") in (None, "logged") and not entry.get("fault_ids"):
            shown.append({"kind": entry.get("kind"), "part": entry.get("part"), "time_s": entry.get("time_s"),
                          "logged": bool(entry.get("logged"))})
    states = body.get(state, "fault_state") or {}
    for fid in sorted(states):
        record = states[fid]
        if isinstance(record, dict) and record.get("cleared_s") is None and record.get("detected"):
            shown.append({"kind": record["kind"], "part": record.get("part"), "time_s": record.get("detected_s"),
                          "logged": record.get("logged_s") is not None})
    return shown


def _realistic(state):
    robot = state["robot"]
    level = body.gauge(state)
    shown = int(level + 0.5)
    believed = _believed_full_wh(state)
    held = float(body.get(state, "_held_kg"))
    p = body.profile(state)
    awake = body.activity_power_w(state, "awake", held_kg=held)
    per_m = body.walk_wh(state, 1.0, "normal", held) + p["power_w"]["hotel_awake"] / p["speeds_mps"]["normal"] / 3600.0
    legs = float(body.get(state, "legs_health"))
    out = {name: copy.deepcopy(robot[name]) for name in _exact_fields() if name in robot}
    out.update({
        "pos": copy.deepcopy(robot.get("pos")) if body.get(state, "localized") else None,
        "battery": shown,
        "battery_state": body.battery_state(state),
        "runtime_min_est": _floor(level / 100.0 * believed / awake * 60.0, RUNTIME_STEP_MIN),
        "range_m_est": _floor((level - body.threshold_pct(state, "reserve")) / 100.0 * believed / per_m, RANGE_STEP_M),
        "battery_temp": bucket_temp(float(body.get(state, "battery_temp_c"))),
        "camera": bucket_camera(body.clarity(state)) if body.imaging(state) else "off",
        "hearing": HEARING_WORDS[body.can_hear(state)],
        "network": "connected" if body.get(state, "network") else "offline",
        "arms": _arms(state),
        "legs": "failed" if legs <= 0 else "weak" if legs <= body.LEGS_LIMP + body.EPS else "ok",
        "load": _load(state),
        "grip": {h: "slipping" if (body.fault(state, "gripper_slip_" + h) or {}).get("detected") else "ok"
                 for h in body.HANDS},
        "hand_soil": {h: bucket_soil(body.slippery(state, h)) for h in body.HANDS},
        "faults": _faults(state),
    })
    return out, shown

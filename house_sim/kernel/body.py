"""The robot body: one source for what the robot is, carries, spends and senses (fidelity P-approx / G).

docs/design/TIER1_EMBODIMENT.md sections 3-5, with the binding changes of TIER1_INTEGRATION.md 1.1-1.7.

* Profiles (data/robot/profiles.json): robot.body_profile selects one, "inherits" deep-merges a parent and
  robot.profile_overrides deep-merges over it for one episode. profile(state) is cached and read-only.
* Robot fields: register_robot_field() declares every robot field with its owner and proprioception tier
  ("exact" | "gauge" | "bucket" | "detected" | "hidden"); get(state, name) falls back to the declared default, so
  every function here works on legacy states that predate the body fields. apply_profile() builds the materialized
  robot record that schema.normalize writes (BODY-ENGINE).
* Energy in Wh, converted to the battery's % at the boundary. battery is the true state of charge in % of
  battery_wh x battery_health; the gauge is what the BMS shows, lowered by the cold (temperature cutoff). Every
  threshold compares the gauge. Actions are settled per leaf (settle_energy); idle time, charging, the pack
  temperature and the network window are integrated in closed form by system_tick.
* Limits: derated lift capacity (check_lift), grasp size classes, positioning precision, force caps, hand soil.
* Sensors (TIER1_INTEGRATION 1.3): imaging, depth, hearing and speech are derived on every call, never stored.

Refusals the body raises are BodyRefusal (kernel_access). Bookkeeping that must survive the step's rollback (an
overload attempt, a detected weak arm, an energy refusal) is applied before raising and carried on the exception:
the engine calls exc.apply(env) after it rolls the step back (never on the partial-commit path, where nothing is
rolled back).

Stage-A library (TIER1_INTEGRATION 1.16): importing it registers nothing in kernel.plugins; the robot field
declarations below are a plain dict. BODY-ENGINE registers system_tick as the system "kernel.body" (order 72,
max_dt 30), robot_stranded as an invariant, ev_battery / ev_network as the event effects "robot.battery" /
"robot.network", and calls power_guard, quiet_guard and settle_energy from the engine.
"""
import copy
import json
import math
from pathlib import Path

from . import geometry
from .errors import ActionError
from .plugins import run_hooks

PROFILE_FILE = Path(__file__).resolve().parent.parent / "data" / "robot" / "profiles.json"
DEFAULT_PROFILE = "humanoid_v1"
HANDS = ("left", "right")
TIERS = ("exact", "gauge", "bucket", "detected", "hidden")
POSTURE_KEYS = ("standing", "crouching", "kneeling", "sitting", "lying")
PROFILE_KEYS = ("mass_kg", "height_m", "width_m", "sideways_width_m", "reach_m", "support_half_m", "eye_height_m",
                "carry", "max_hand_kg", "max_mass_kg", "push_n", "pull_n", "wrist_nm", "grippers", "battery_wh",
                "thresholds_pct", "power_w", "auto_standby_s", "walk_wh_per_m", "load_ref_kg", "stair_wh_per_step",
                "stair_s_per_step", "speeds_mps", "decel_mps2", "react_s", "noise_db_1m", "quiet_limit_db", "charge",
                "pack_thermal", "energy_buffer_pct", "spray_limit_s", "jet_limit_s", "stairs", "fault_p")
SPEED_MODES = ("careful", "normal", "fast")
G = 9.81
EPS = 1e-9
DIGITS = 9                      # battery % and energy counters: 9 decimals, no drift over a week of substeps
LEGACY_WH_PER_PCT = 24.0        # ActionSpec.energy (old % of the battery) read as work at the humanoid_v1 scale
CONNECTION_CROSSING_M = 1.0     # walking through a doorway or onto the stairs (core move's +1.0 m)
DIAGONAL = 1.4142               # geometry's diagonal step factor (same float, so durations stay identical)
AID_HEIGHT_M = 0.4              # standing height of a climbing aid that does not declare standing_height_m
ABOVE_HEAD_M = 0.1              # hands above the head: an item higher than height_m + 0.1
DERATE = {"overhead": 0.6, "above_head": 0.6, "platform": 0.5, "limp": 0.5}
LEGS_LIMP = 0.5                 # legs_health at or below this: careful speed only, no stairs, no climbing
OVERLOAD_DROP = 1.5             # a held load above 1.5 x the hand's capacity slips out at settlement
SLIPPERY = {"grease": 1.0, "oil": 1.0, "soap": 1.0, "food": 1.0, "flour": 1.0, "water": 0.5, "mud": 0.5}
SLIP_FAULT_AT = 0.5             # hand soil this slippery starts a (latent) gripper_slip fault
SLIP_MIN_KG = 1.0               # a gripper_slip fault only drops loads of at least 1 kg
POWER_GRIPS = ("normal", "firm", "handle", "two_hand", "underneath")
THIN_FLAT_MM = 1.0
GRASP_AUTO = {"pinch": ("pinch grasp", 0.0), "edges": ("slid it to the edge of the surface to lift it", 3.0)}
DIRTY_CLARITY = 0.6             # the camera self-check notices a dirty lens below this clarity
DEGRADED_HEALTH = 0.8           # state of health below 80 % is a degraded pack (the usual end-of-life mark)
RATIO_BUCKETS = ((1.25, "slight"), (1.5, "moderate"), (2.0, "heavy"))

POWER_OFF_TEXT = "The robot is powered down (battery depleted); it must be charged or have its battery swapped"
ENERGY_TEXT = "Not enough battery left to finish this; dock or swap the battery first"
OVERLOAD_TEXT = "Object and contents exceed carrying capacity; get help or slide it"
WEAK_ARM_TEXT = "The {} arm could not take the load (actuator weakness detected)"
LIMP_LIFT_TEXT = "The battery is too low for a lift this heavy; charge first"
QUIET_TEXT = "Quiet mode is on: that is too loud now"
OVERHEAT_TEXT = "Battery overheating; let it cool"
LOCALIZATION_TEXT = "Localization lost: look around in a lit room or request remote help"
TOO_SMALL_TEXT = "Too small to grasp; sweep it up, use tape or a tool"
POWER_TOO_SMALL_TEXT = "Too small for a power grasp: use grip=pinch"
THIN_FLAT_TEXT = "Too thin to grip lying flat: use grip=edges or grip=scoop"
BATTERY_CUT_TEXT = "Battery depleted after {:.1f} m; the robot powered down"
POWERED_DOWN_NOTE = "Battery depleted as it finished; the robot powered down"
GASP_TEXT = {"battery": "Robot powered down: battery depleted"}


# ----------------------------------------------------------------------
# refusals

class BodyRefusal(ActionError):
    """A refusal by the body's own checks (kernel_access: it reveals only robot state or what a touch revealed).

    effects: fn(w) callables already applied before raising; the engine re-applies them after rolling the step back.
    grants: (eid, level, fields) the attempt let the robot learn, given to realistic perception by apply()."""
    kernel_access = True

    def __init__(self, message, category="capability", effects=(), grants=()):
        super().__init__(message, category)
        self.effects = tuple(effects)
        self.grants = tuple(grants)

    def apply(self, env):
        """Re-apply the refusal's bookkeeping after the engine rolled the step back."""
        for fn in self.effects:
            fn(env.world)
        realism = getattr(env, "realism", None)
        if realism is not None and self.grants:
            from .realism import LEVELS
            for eid, level, fields in self.grants:
                record = env.world.s["entities"].get(eid)
                if record is not None:
                    realism.knowledge.grant(eid, record, LEVELS[level], list(fields))


class PowerOff(BodyRefusal):
    """Every action except wait (ActionSpec.power_off_ok) while the robot is powered down."""

    def __init__(self):
        super().__init__(POWER_OFF_TEXT, "capability")


def _refuse(w, message, category, effects=(), grants=()):
    for fn in effects:
        fn(w)
    return BodyRefusal(message, category, effects, grants)


# ----------------------------------------------------------------------
# profiles

_DATA = None
_MERGED = {}


def _data():
    global _DATA
    if _DATA is None:
        _DATA = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    return _DATA


def _merge(base, over):
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def profile_names():
    return sorted(_data()["profiles"])


def _resolve(name, chain=()):
    profiles = _data()["profiles"]
    if name not in profiles:
        raise ValueError("Unknown robot body profile: {!r}".format(name))
    if name in chain:
        raise ValueError("Robot body profile inheritance cycle: " + " -> ".join(chain + (name,)))
    raw = profiles[name]
    parent = raw.get("inherits")
    base = _resolve(parent, chain + (name,)) if parent else {}
    return _merge(base, {k: v for k, v in raw.items() if k != "inherits"})


def _check_profile(name, merged):
    missing = [k for k in PROFILE_KEYS if k not in merged]
    if missing:
        raise ValueError("Robot body profile {!r} lacks {}".format(name, ", ".join(missing)))
    for table in ("eye_height_m", "power_w.stance", "power_w.standby_stance"):
        node = merged
        for part in table.split("."):
            node = node[part]
        lacking = [k for k in POSTURE_KEYS + (("on_aid",) if "stance" in table else ()) if k not in node]
        if lacking:
            raise ValueError("Robot body profile {!r}: {} lacks {}".format(name, table, ", ".join(lacking)))
    for hand in HANDS:
        if merged["grippers"].get(hand) not in _data()["grippers"]:
            raise ValueError("Robot body profile {!r}: unknown gripper for the {} hand".format(name, hand))


def profile(state):
    """The robot's merged profile (inherits, then robot.profile_overrides). Cached; callers must not mutate it."""
    robot = state["robot"]
    name = robot.get("body_profile") or DEFAULT_PROFILE
    overrides = robot.get("profile_overrides")
    key = (name, json.dumps(overrides, sort_keys=True) if overrides else None)
    merged = _MERGED.get(key)
    if merged is None:
        merged = _resolve(name)
        if overrides:
            if not isinstance(overrides, dict):
                raise ValueError("robot.profile_overrides must be an object")
            merged = _merge(merged, overrides)
        _check_profile(name, merged)
        _MERGED[key] = merged
    return merged


def gripper(state, hand):
    """The gripper table entry of a hand ("left" | "right")."""
    kind = get(state, "grippers").get(hand)
    table = _data()["grippers"]
    if kind not in table:
        raise ValueError("Unknown gripper type {!r} on the {} hand".format(kind, hand))
    return table[kind]


# ----------------------------------------------------------------------
# robot field registry

ROBOT_FIELDS = {}


def register_robot_field(name, default, owner, tier, doc, materialize=False):
    """Declare a robot field. default may be fn(state) -> value. materialize=True: apply_profile (and so
    schema.normalize) writes it into every state; otherwise it is a declaration that get() resolves. Declaring a
    name again with the same default, owner, tier and materialize flag is a no-op; anything else raises."""
    if tier not in TIERS:
        raise ValueError("Unknown proprioception tier {!r} for robot field {}".format(tier, name))
    if not isinstance(name, str) or not name:
        raise ValueError("Robot field names are nonempty strings")
    entry = {"default": default, "owner": owner, "tier": tier, "doc": doc, "materialize": bool(materialize)}
    old = ROBOT_FIELDS.get(name)
    if old is not None:
        if all(old[k] == entry[k] for k in ("default", "owner", "tier", "materialize")):
            return old
        raise ValueError("Robot field {!r} registered twice ({} and {})".format(name, old["owner"], owner))
    ROBOT_FIELDS[name] = entry
    return entry


def robot_fields():
    """name -> {"default", "owner", "tier", "doc", "materialize"} (a copy of the table)."""
    return {name: dict(entry) for name, entry in ROBOT_FIELDS.items()}


def _default(entry, state):
    value = entry["default"]
    if callable(value):
        return value(state)
    return copy.deepcopy(value) if isinstance(value, (dict, list)) else value


def get(state, name, default=None):
    """A robot field, else its registered default, else `default`. Mutable defaults are fresh copies."""
    robot = state["robot"]
    if name in robot:
        return robot[name]
    entry = ROBOT_FIELDS.get(name)
    return default if entry is None else _default(entry, state)


DERIVED = ("battery_temp_c", "power_state", "battery_min_pct", "arms_ok")


def apply_profile(robot_cfg, state=None):
    """The robot record normalize materializes: every materialized field from its default (profile values with
    profile_overrides applied), then the explicit fields of robot_cfg, which win. Then legacy faults are normalized
    and setup faults applied, arms_ok mirrors arm_health, and the pack temperature (the start room's), the power
    state and battery_min_pct (both from the gauge) are derived unless given. state: the layout being normalized
    (rooms, weather, start_room, time_s), needed for the start-room temperature."""
    from . import body_faults
    cfg = copy.deepcopy(robot_cfg or {})
    state = state or {}
    probe = {"robot": cfg, "rooms": state.get("rooms", {}), "weather": state.get("weather", {}),
             "utilities": state.get("utilities", {})}
    robot = {}
    for name in sorted(ROBOT_FIELDS):
        entry = ROBOT_FIELDS[name]
        if entry["materialize"] and name not in cfg and name not in DERIVED:
            robot[name] = _default(entry, probe)
    if "arm_health" not in cfg and isinstance(cfg.get("arms_ok"), dict):
        robot["arm_health"] = {h: 1.0 if cfg["arms_ok"].get(h, True) else 0.0 for h in HANDS}
    robot.update(cfg)
    body_faults.normalize_faults(robot, now=float(state.get("time_s", 0.0) or 0.0))
    robot["arms_ok"] = {h: float(robot["arm_health"].get(h, 1.0)) > 0 for h in HANDS}
    view = dict(probe, robot=robot)
    if "battery_temp_c" not in cfg:
        robot["battery_temp_c"] = _room_temperature(view, robot.get("room") or state.get("start_room"))
    if "power_state" not in cfg:
        robot["power_state"] = "off" if gauge(view) <= threshold_pct(view, "cutoff") + EPS else "on"
    if "battery_min_pct" not in cfg:
        robot["battery_min_pct"] = round(gauge(view), DIGITS)
    return robot


def _room_temperature(state, rid):
    """physics.room_temperature of a room (outdoors: the weather's); the indoor default for an unknown room."""
    from .physics import room_temperature
    if rid in state.get("rooms", {}):
        return room_temperature(state, rid)
    return float(state.get("utilities", {}).get("indoor_temp_c", 21.0))


def _robot_room_temperature(state):
    return _room_temperature(state, state["robot"].get("room"))


# ----------------------------------------------------------------------
# faults as the body reads them (body_faults owns onset, detection and clearing)

def fault(state, fault_id):
    """The active record of a fault id ("arm_weak_left", "network_down"), or None."""
    record = (get(state, "fault_state") or {}).get(fault_id)
    return record if isinstance(record, dict) and record.get("cleared_s") is None else None


def is_latent(state, fault_id):
    """True for an active fault the robot does not know about yet."""
    record = fault(state, fault_id)
    return record is not None and not record.get("detected")


# ----------------------------------------------------------------------
# battery: units, temperature, gauge, thresholds

def full_wh(state):
    """Energy at 100 %: the installed pack's nominal energy times its state of health."""
    return float(get(state, "battery_wh")) * float(get(state, "battery_health"))


def temp_factor(t_c):
    """Fraction of the room-temperature capacity a Li-ion (NMC) pack delivers at t_c (conservative datasheet end:
    0.85 at 0 C, 0.70 at -10 C, never below 0.5)."""
    t_c = float(t_c)
    if t_c >= 15.0:
        return 1.0
    if t_c >= 0.0:
        return 1.0 - 0.01 * (15.0 - t_c)
    return max(0.5, 0.85 - 0.015 * (0.0 - t_c))


def cutoff_soc(state):
    """State of charge (%) that cannot be extracted at the pack's current temperature."""
    return 100.0 * (1.0 - temp_factor(get(state, "battery_temp_c")))


def gauge(state):
    """The battery gauge (%): the true state of charge above the temperature cutoff, rescaled to 0-100."""
    c_t = cutoff_soc(state)
    value = 100.0 * (float(get(state, "battery")) - c_t) / (100.0 - c_t)
    return min(100.0, max(0.0, value))


def threshold_pct(state, name):
    """A gauge threshold of the profile (low, reserve, critical, cutoff, wake); robot.battery_reserve_pct overrides
    the reserve."""
    if name == "reserve" and state["robot"].get("battery_reserve_pct") is not None:
        return float(state["robot"]["battery_reserve_pct"])
    return float(profile(state)["thresholds_pct"][name])


def _soc_at(state, level):
    c_t = cutoff_soc(state)
    return c_t + (100.0 - c_t) * level / 100.0


def available_wh(state, floor="cutoff"):
    """Energy (Wh) the pack can still deliver before the gauge reaches `floor` ("cutoff" or "reserve")."""
    floor_soc = _soc_at(state, threshold_pct(state, floor))
    return max(0.0, (float(get(state, "battery")) - floor_soc) * full_wh(state) / 100.0)


def is_on(state):
    return get(state, "power_state") == "on"


def battery_state(state):
    """ok | low | reserve | critical | depleted (powered down or at the cutoff), from the gauge."""
    level = gauge(state)
    if not is_on(state) or level <= threshold_pct(state, "cutoff") + EPS:
        return "depleted"
    for name in ("critical", "reserve", "low"):
        if level <= threshold_pct(state, name) + EPS:
            return name
    return "ok"


def limp(state):
    """Firmware limp mode: the gauge at or below critical, or legs at or below half strength."""
    weak_legs = float(get(state, "legs_health")) <= LEGS_LIMP + EPS
    return weak_legs or gauge(state) <= threshold_pct(state, "critical") + EPS


# ----------------------------------------------------------------------
# power and work

def posture_key(state):
    """The stance key for power and eye height: on_aid, lying (fallen), the posture, or standing."""
    if get(state, "standing_on"):
        return "on_aid"
    if get(state, "fallen"):
        return "lying"
    posture = get(state, "posture")
    return posture if posture in POSTURE_KEYS else "standing"


def activity_power_w(state, kind, arms=0, held_kg=None):
    """Electrical power (W) of an activity: awake, standby, off, motion (stance replaced by the walking work),
    physical (arm_active per arm), perception, communication, control or cognitive. held_kg defaults to the
    cached carried load. Domain extras (robot_power hooks) are added by the callers that have a World."""
    if kind == "off":
        return 0.0
    table = profile(state)["power_w"]
    held = float(get(state, "_held_kg") if held_kg is None else held_kg)
    hold = table["hold_per_kg"] * held
    key = posture_key(state)
    if kind == "standby":
        return table["hotel_standby"] + table["standby_stance"][key] + hold
    if kind == "motion":
        return table["hotel_awake"] + hold
    awake = table["hotel_awake"] + table["stance"][key] + hold
    if kind in ("awake", "control", "cognitive"):
        return awake
    if kind == "physical":
        return awake + table["arm_active"] * arms
    if kind == "perception":
        return awake + table["perception"]
    if kind == "communication":
        return awake + table["speech"]
    raise ValueError("Unknown activity kind: {!r}".format(kind))


def _hook_power(w, act_name):
    return sum(float(v) for v in run_hooks("robot_power", w, act_name)
               if isinstance(v, (int, float)) and not isinstance(v, bool))


def walk_wh(state, meters, speed, load_kg):
    """Walking work (Wh), without the hotel load: per-metre cost at the speed x (1 + load / load_ref_kg)."""
    p = profile(state)
    return float(meters) * p["walk_wh_per_m"][speed] * (1.0 + float(load_kg) / p["load_ref_kg"])


def stairs_wh(state, up, down):
    table = profile(state)["stair_wh_per_step"]
    return int(up) * table["up"] + int(down) * table["down"]


def stairs_direction(state, conn, a, b):
    """"up" or "down" for the stairs connection `conn` walked from room a to room b; None if it is no stairs.
    Rooms on the same floor (porch steps) use the connection's "upper" room."""
    if (conn or {}).get("kind") != "stairs":
        return None
    fa = float(state["rooms"][a].get("floor", 0) or 0)
    fb = float(state["rooms"][b].get("floor", 0) or 0)
    if fa == fb:
        return "up" if conn.get("upper") == b else "down"
    return "up" if fb > fa else "down"


def motion_work_wh(state, record):
    """Walking and stair work of a motion record (the hotel load is charged over the action's seconds)."""
    return walk_wh(state, record["meters"], record["speed"], record["load_kg"]) + \
        stairs_wh(state, record["stairs_up"], record["stairs_down"])


def affordable_prefix(state, segments_m, speed, load_kg, stairs=(0, 0), extra_s=0.0, extra_m=0.0, power_w=None):
    """How far the battery reaches: (k, finish_ok). k is the number of leading segments (cell steps, m) affordable
    within available_wh(cutoff); finish_ok says whether, after all of them, the terminal block is affordable too:
    extra_m of crossing (a doorway), extra_s of handling and a flight of stairs (work and climbing time). The
    terminal block is all or nothing: the robot never starts a crossing or a flight it cannot finish. power_w: the
    electrical power while walking (default: hotel + holding, without domain hooks)."""
    p = profile(state)
    budget = available_wh(state) + EPS
    v = p["speeds_mps"][speed]
    power = activity_power_w(state, "motion", held_kg=load_kg) if power_w is None else float(power_w)
    per_m = p["walk_wh_per_m"][speed] * (1.0 + float(load_kg) / p["load_ref_kg"]) + power / v / 3600.0
    spent, k = 0.0, 0
    for seg in segments_m:
        cost = seg * per_m
        if spent + cost > budget:
            return k, False
        spent += cost
        k += 1
    up, down = stairs
    terminal = float(extra_m) * per_m + power * (float(extra_s) + p["stair_s_per_step"] * (up + down)) / 3600.0 + \
        stairs_wh(state, up, down)
    return k, spent + terminal <= budget


# ----------------------------------------------------------------------
# spending, thresholds, power states

def log_first(w, key, now):
    """body_log keeps the first time of each body event; the robot section is written only for a new key."""
    log = w.s["robot"].get("body_log")
    if isinstance(log, dict) and key in log:
        return
    r = w.robot_m()
    log = dict(r.get("body_log") or {})
    log[key] = round(float(now), 3)
    r["body_log"] = log


def _now(state, at_s=None):
    return float(state.get("time_s", 0.0) if at_s is None else at_s)


def _crossings(w, before, after, at_s=None):
    """Downward gauge crossings of low, reserve and critical: events, first times and undocked reserve breaches."""
    state = w.s
    now = _now(state, at_s)
    for name, event in (("low", "battery_low"), ("reserve", "battery_reserve"), ("critical", None)):
        level = threshold_pct(state, name)
        if before > level + EPS and after <= level + EPS:
            log_first(w, "battery_{}_s".format(name), now)
            if event:
                w.emit(event, gauge=round(after, 1), time_s=now)
            if name == "reserve" and not get(state, "docked"):
                w.stat("battery_reserve_breaches")


def spend_wh(w, wh, at_s=None):
    """Draw wh from the pack (never below the cutoff) and return the % of full energy spent. Updates
    energy_wh_used, stats.energy (%), battery_min_pct and the threshold crossings; reaching the cutoff powers the
    robot down (at at_s when given)."""
    state = w.s
    wh = float(wh)
    if wh <= 0.0:
        return 0.0
    before = gauge(state)
    avail = available_wh(state)
    e_full = full_wh(state)
    r = w.robot_m()
    reached = wh >= avail - EPS
    if reached:
        wh = avail
        floor = _soc_at(state, threshold_pct(state, "cutoff"))
        r["battery"] = round(min(float(get(state, "battery")), floor), DIGITS)
    else:
        r["battery"] = round(float(get(state, "battery")) - 100.0 * wh / e_full, DIGITS)
    pct = 100.0 * wh / e_full
    r["energy_wh_used"] = round(float(get(state, "energy_wh_used")) + wh, DIGITS)
    w.stat("energy", round(pct, DIGITS))
    after = gauge(state)
    r["battery_min_pct"] = round(min(float(get(state, "battery_min_pct")), after), DIGITS)
    _crossings(w, before, after, at_s)
    if reached and is_on(state):
        power_down(w, "battery", at_s)
    return pct


def power_down(w, cause, at_s=None):
    """The robot powers down: a controlled crouch before the motors lose power (held items stay gripped), or a fall
    when it stands on an aid. Counts power_downs (and power_downs_off_dock unless seated on a dock), logs it, sends
    the dying-gasp notification when the network is up and runs the power_down hooks. False if it was already off."""
    state = w.s
    if not is_on(state):
        return False
    now = _now(state, at_s)
    r = w.robot_m()
    r["power_state"] = "off"
    r["standby"] = False
    r["charging"] = False
    if r.get("standing_on"):
        from . import body_faults
        body_faults.fall(w, "power_down", height_m=aid_height_m(state))
    else:
        r["posture"] = "crouching"
    w.stat("power_downs")
    if not r.get("docked"):
        w.stat("power_downs_off_dock")
    w.emit("power_down", cause=cause, time_s=now)
    log_first(w, "power_down_s", now)
    w._power_down_step = state.get("steps", 0)
    if get(state, "network"):
        messages = w.sec("messages")
        messages.append({"step": state.get("steps", 0), "time_s": now, "speaker": "robot_system", "kind": "notify",
                         "text": GASP_TEXT.get(cause, "Robot powered down: " + str(cause).replace("_", " ")),
                         "to": None, "room": None, "audience": sorted(state.get("people", {}))})
        del messages[:-200]
    run_hooks("power_down", w, cause)
    return True


def wake(w, cause, at_s=None):
    """Power back on into parked standby (after charging past the wake level or a pack swap). False if already on."""
    state = w.s
    if is_on(state):
        return False
    r = w.robot_m()
    r["power_state"] = "on"
    r["standby"] = True
    r["_idle_s"] = float(profile(state)["auto_standby_s"])
    w.emit("power_up", cause=cause, time_s=_now(state, at_s))
    return True


def _source_rate(w):
    best = 0.0
    for value in run_hooks("robot_charge_source", w):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > best:
            best = float(value)
    return best


def _window_rate(state, raw):
    if raw <= 0.0 or fault(state, "battery_overheat") is not None:
        return 0.0
    window = profile(state)["charge"]
    t_c = float(get(state, "battery_temp_c"))
    if t_c < window["min_c"] or t_c > window["max_c"]:
        return 0.0
    return raw / 2.0 if t_c < window["half_below_c"] else raw


def charge_rate(w):
    """Charging rate (%/min of full energy) after the pack-temperature window: 0 when no live charge source (the
    robot_charge_source hooks) is seated, below min_c, above max_c or while the pack overheats; halved below
    half_below_c."""
    return _window_rate(w.s, _source_rate(w))


def swap_pack(w, charge_pct, capacity_wh=None, health=None, temp_c=None):
    """Install another pack: charge_pct (%), capacity_wh (default: the same nominal pack), health (default 1.0, a
    healthy pack) and temp_c (default: the room's). Returns the removed pack's values. A degraded-pack fault clears
    with a healthy pack; a powered-down robot wakes when the new gauge reaches the wake level."""
    from . import body_faults
    state = w.s
    old = {"charge_pct": round(float(get(state, "battery")), DIGITS), "capacity_wh": float(get(state, "battery_wh")),
           "health": float(get(state, "battery_health")), "temp_c": float(get(state, "battery_temp_c"))}
    r = w.robot_m()
    r["battery"] = round(min(100.0, max(0.0, float(charge_pct))), DIGITS)
    r["battery_wh"] = float(capacity_wh) if capacity_wh is not None else old["capacity_wh"]
    r["battery_health"] = float(health) if health is not None else 1.0
    r["battery_temp_c"] = round(float(temp_c), 2) if temp_c is not None else _robot_room_temperature(state)
    if r["battery_health"] >= DEGRADED_HEALTH and fault(state, "battery_degraded") is not None:
        body_faults.clear_fault(w, "battery_degraded", "battery_swap")
    level = gauge(state)
    r["battery_min_pct"] = round(min(float(get(state, "battery_min_pct")), level), DIGITS)
    if not is_on(state) and level >= threshold_pct(state, "wake") - EPS:
        wake(w, "battery_swap")
    elif is_on(state) and level <= threshold_pct(state, "cutoff") + EPS:
        power_down(w, "battery")
    return old


# ----------------------------------------------------------------------
# carried load (the mass rule is physics': support.subtree_mass, TIER1_INTEGRATION 1.5)

def _children_map(state):
    from .phys import support
    return support.children_map(state)


def _subtree_mass(state, eid, cmap=None):
    from .phys import support
    return float(support.subtree_mass(state, eid, cmap))


def held_hands(state):
    """[(eid, hands)] for each distinct held item (a two-hand item once, with both hands)."""
    hands = get(state, "hands") or {}
    items = []
    for hand in HANDS:
        eid = hands.get(hand)
        if eid and eid not in [e for e, _ in items]:
            items.append((eid, tuple(h for h in HANDS if hands.get(h) == eid)))
    return items


def held_loads(state, cmap=None):
    """[(eid, hands, kg)] for each distinct held item, kg being its subtree mass (contents and liquid included).
    One physics children map serves all items (cmap: one the caller already built)."""
    items = held_hands(state)
    if not items:
        return []
    cmap = _children_map(state) if cmap is None else cmap
    return [(eid, hands, _subtree_mass(state, eid, cmap)) for eid, hands in items]


def carried_kg(w, cmap=None):
    return sum(kg for _, _, kg in held_loads(w.s, cmap))


def total_mass_kg(w):
    """Robot plus everything it carries: the load on floors, stairs, ladders and furniture."""
    return float(get(w.s, "mass_kg")) + carried_kg(w)


def refresh_held(w):
    """Recompute the carried load once and cache it for the body system (_held_kg) and proprioception (_hand_kg:
    hand -> [item, kg share]); writes only what changed. Returns held_loads()."""
    state = w.s
    loads = held_loads(state)
    total = round(sum(kg for _, _, kg in loads), 4)
    per_hand = {}
    for eid, hands, kg in loads:
        for hand in hands:
            per_hand[hand] = [eid, round(kg / len(hands), 4)]
    robot = state["robot"]
    if robot.get("_held_kg") != total or robot.get("_hand_kg", {}) != per_hand:
        r = w.robot_m()
        r["_held_kg"] = total
        r["_hand_kg"] = per_hand
    return loads


# ----------------------------------------------------------------------
# lift capacity

def arm_health(state, hand, believed=False):
    """Strength of an arm (0 = failed); believed=True reads an undetected (latent) weak arm as nominal."""
    if believed and is_latent(state, "arm_weak_" + hand):
        return 1.0
    return float((get(state, "arm_health") or {}).get(hand, 1.0))


def _reach_cells(state, eid):
    room, cells = geometry.anchor(state, eid)
    if not cells or room != state["robot"].get("room"):
        return 0
    pos = tuple(state["robot"].get("pos") or (0, 0))
    return min(geometry.chebyshev(pos, c) for c in cells)


def _derate(state, hand, eid):
    factor = 1.0
    if eid is not None and eid in state.get("entities", {}):
        if geometry.mounted_overhead(state, eid) and _reach_cells(state, eid) > geometry.REACH_CELLS:
            factor *= DERATE["overhead"]       # reached over a counter: the lever grows from about 0.45 to 0.75 m
        if geometry.height_m(state, eid) > float(get(state, "height_m")) + ABOVE_HEAD_M:
            factor *= DERATE["above_head"]     # hands above the head, arms extended
    if get(state, "standing_on"):
        factor *= DERATE["platform"]
    if limp(state):
        factor *= DERATE["limp"]
    for value in run_hooks("lift_capacity", state, hand, eid):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            factor *= float(value)
    return factor


def _capacity(state, hand, eid=None, believed=False):
    if hand == "both":
        health = min(arm_health(state, h, believed) for h in HANDS)
        return float(get(state, "max_mass_kg")) * health * _derate(state, "both", eid)
    if hand in HANDS:
        return float(get(state, "max_hand_kg")) * arm_health(state, hand, believed) * _derate(state, hand, eid)
    return max(_capacity(state, h, eid, believed) for h in HANDS)


def capacity_kg(state, hand=None, eid=None, believed=False):
    """Derated lift capacity (kg): one hand ("left" | "right"), both hands near the body ("both") or the stronger
    single hand (None). eid adds the item's reach derates (overhead cell, above the head). believed=True: as the
    robot believes it (a latent weak arm at full strength)."""
    return _capacity(state, hand, eid, believed)


def _total_limit(state, hands):
    return float(get(state, "max_mass_kg")) * min(arm_health(state, h) for h in hands)


def _loaded_hands(state, used):
    held = get(state, "hands") or {}
    return tuple(h for h in HANDS if h in used or held.get(h))


def _lift_hands(state, hands):
    """Hands a lift uses: a hand name, "both", a list of hands, or a count (1 takes the first free working hand,
    right before left, as Ctx.free_hand does)."""
    if hands == "both" or hands == 2:
        return HANDS
    if hands in HANDS:
        return (hands,)
    if isinstance(hands, (list, tuple)) and hands and all(h in HANDS for h in hands):
        return tuple(h for h in HANDS if h in hands)
    held = get(state, "hands") or {}
    working = [h for h in ("right", "left") if arm_health(state, h) > 0]
    free = [h for h in working if not held.get(h)]
    return ((free or working or ["right"])[0],)


def _ratio_bucket(ratio):
    for limit, name in RATIO_BUCKETS:
        if ratio <= limit:
            return name
    return "extreme"


def _overload_effect(eid, ratio, detect=None):
    def effect(w):
        from . import body_faults
        if detect is not None:
            body_faults.detect_fault(w, detect, "overload")
        w.stat("overload_attempts")
        w.emit("overload", target=eid, ratio_bucket=_ratio_bucket(ratio))
        log_first(w, "overload_s", _now(w.s))
    return effect


def check_lift(w, eid, hands, mass=None):
    """Enforce the lift (TIER1_EMBODIMENT 4.7, TIER1_INTEGRATION 1.5): derated capacity of the hands used and the
    total carried load. Over the limit the lift is aborted at lift-off (torque limiting) and nothing is dropped:
    an overload attempt is counted (stat, event, body_log) and the robot felt the weight (touch grant on mass_kg).
    A load above a latent weak arm's true capacity but within its nominal one detects the weakness instead. In
    limp mode a lift the limp derate alone forbids is a plain capability refusal. mass: the item's subtree mass
    (computed when None)."""
    state = w.s
    used = _lift_hands(state, hands)
    key = "both" if len(used) == 2 else used[0]
    cmap = _children_map(state) if mass is None or held_hands(state) else None
    mass = _subtree_mass(state, eid, cmap) if mass is None else float(mass)
    cap = _capacity(state, key, eid)
    carried = carried_kg(w, cmap)
    limit_kg = _total_limit(state, _loaded_hands(state, used))
    if mass <= cap + EPS and carried + mass <= limit_kg + EPS:
        return None
    grants = ((eid, "touch", ("mass_kg",)),)
    if mass > cap + EPS:
        believed = _capacity(state, key, eid, believed=True)
        weak = [h for h in used if is_latent(state, "arm_weak_" + h)]
        if weak and mass <= believed + EPS:
            side = min(weak, key=lambda h: (arm_health(state, h), h))
            raise _refuse(w, WEAK_ARM_TEXT.format(side), "capability",
                          (_overload_effect(eid, mass / max(cap, EPS), "arm_weak_" + side),), grants)
        if limp(state) and mass <= believed / DERATE["limp"] + EPS:
            raise _refuse(w, LIMP_LIFT_TEXT, "capability")
    ratio = max(mass / max(cap, EPS), (carried + mass) / max(limit_kg, EPS))
    raise _refuse(w, OVERLOAD_TEXT, "safety", (_overload_effect(eid, ratio),), grants)


def hold_capacity_left(w, hand):
    """kg more the hand (with what it holds) can take: what fill and pour verbs may add to a held container."""
    state = w.s
    loads = held_loads(state)
    in_hand = next(((hands, kg) for _, hands, kg in loads if hand in hands), None)
    if in_hand is None:
        own = _capacity(state, hand)
        used = (hand,)
    else:
        hands, kg = in_hand
        own = _capacity(state, "both" if len(hands) == 2 else hand) - kg
        used = hands
    total_left = _total_limit(state, _loaded_hands(state, used)) - sum(kg for _, _, kg in loads)
    return max(0.0, min(own, total_left))


def stability_margin(state, extra_kg, lever_m=0.35):
    """Static stability margin (m): the support half-length minus the shift of the centre of mass when extra_kg is
    held lever_m in front of the body. Negative means the robot would tip; navigation derates stairs and slopes."""
    mass = float(get(state, "mass_kg"))
    return profile(state)["support_half_m"] - float(extra_kg) * float(lever_m) / (mass + float(extra_kg))


# ----------------------------------------------------------------------
# grippers: forces, soil, friction, grasp size, precision

def grip_limit_n(state, hand, mode="power"):
    """Hardware grip force cap (N) of a hand, "power" or "pinch"; 0 for a failed arm. Domains clamp commanded forces
    to it (manipulation's 90 N "firm" grip is a commanded level)."""
    if arm_health(state, hand) <= 0:
        return 0.0
    return float(gripper(state, hand)["pinch_n" if mode == "pinch" else "grip_n"])


def limit(state, name):
    """A whole-body force or torque limit of the profile: push_n, pull_n or wrist_nm."""
    if name not in ("push_n", "pull_n", "wrist_nm"):
        raise ValueError("Unknown body limit: {!r}".format(name))
    return float(profile(state)[name])


def slippery(state, hand):
    """How slippery a hand's fingers are (0-1) from its soil."""
    soil = (get(state, "hand_soil") or {}).get(hand) or {}
    return min(1.0, sum(SLIPPERY.get(kind, 0.0) * float(amount) for kind, amount in soil.items()))


def grip_mu(state, hand):
    """Friction coefficient of a hand's grasp: the pads' mu, lowered by slippery soil and a gripper_slip fault."""
    mu = gripper(state, hand)["mu"] * (1.0 - 0.5 * slippery(state, hand))
    return mu * 0.6 if fault(state, "gripper_slip_" + hand) is not None else mu


def clarity(state):
    """Lens clarity: camera_clean, at most 1 - lens_scratch."""
    return min(float(get(state, "camera_clean")), 1.0 - float(get(state, "lens_scratch", 0.0) or 0.0))


def precision_mm(state, hand):
    """Positioning precision (mm) of a hand: the gripper's, divided by the arm's health, worse with slippery
    fingers, a dirty lens (same 0.7 knee as realism noise), a dark room without a robot light and fast speed."""
    from .perception import flashlight_on, light_level
    value = gripper(state, hand)["precision_mm"] / max(0.2, arm_health(state, hand))
    value *= 1.0 + 0.5 * slippery(state, hand)
    value *= 1.0 + 2.0 * max(0.0, 0.7 - clarity(state)) / 0.3
    if light_level(state, state["robot"]["room"]) < 0.35 and not flashlight_on(state):
        value *= 1.5
    if effective_speed(state, None)[0] == "fast":
        value *= 1.5
    return value


def _on_surface(state, record):
    return record.get("relation", "on") == "on" or record.get("location") in state.get("rooms", {})


def grasp_problem(state, record, hand, grip):
    """(refusal text or None, grip the planner chooses or None) for taking `record` with `hand` and `grip`.
    Below the gripper's pinch size nothing can be grasped; below its power size the planner pinches unless a power
    grip was asked for; paper-thin items lying flat need edges or scoop with grippers that say so, while a hand
    slides them to the edge first (GRASP_AUTO gives each automatic grip's note and extra seconds)."""
    table = gripper(state, hand if hand in HANDS else "right")
    size = [float(v) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:3]]
    largest, smallest = max(size) * 1000.0, min(size) * 1000.0
    if largest < table["min_pinch_mm"]:
        return TOO_SMALL_TEXT, None
    if largest < table["min_power_mm"]:
        if grip in POWER_GRIPS:
            return POWER_TOO_SMALL_TEXT, None
        if grip is None:
            return None, "pinch"
    if smallest < THIN_FLAT_MM and _on_surface(state, record) and grip not in ("edges", "scoop"):
        if table.get("thin_flat_needs"):
            return THIN_FLAT_TEXT, None
        return None, "edges"
    return None, None


# ----------------------------------------------------------------------
# speed, stopping, noise, eye height

def speed_mps(state, mode=None):
    return float(profile(state)["speeds_mps"][mode or get(state, "speed_mode") or "normal"])


def effective_speed(state, requested):
    """(mode, note): the speed the robot actually walks at for a requested mode (None: robot.speed_mode). Limp mode
    or weak legs clamp it to careful; quiet mode turns fast into normal."""
    mode = requested or get(state, "speed_mode") or "normal"
    if mode not in SPEED_MODES:
        raise ValueError("Unknown speed mode: {!r}".format(mode))
    if mode != "careful" and limp(state):
        if float(get(state, "legs_health")) <= LEGS_LIMP + EPS:
            return "careful", "Weak legs: walking at careful speed"
        return "careful", "Battery critical: walking at careful speed"
    if mode == "fast" and get(state, "quiet"):
        return "normal", "Quiet mode: walking at normal speed"
    return mode, None


def stopping_distance_m(state, mode=None, mu=None):
    """Distance (m) to stop from walking speed: reaction time plus braking at min(decel, 0.5 mu g)."""
    p = profile(state)
    v = speed_mps(state, effective_speed(state, mode)[0])
    decel = p["decel_mps2"] if mu is None else min(p["decel_mps2"], 0.5 * float(mu) * G)
    if decel <= 0:
        return float("inf")
    return v * p["react_s"] + v * v / (2.0 * decel)


def noise_db(state, source, mode=None):
    """Sound pressure (dB at 1 m) of the robot's footsteps (by effective speed, quieter in quiet mode), its speech
    or its actuators."""
    table = profile(state)["noise_db_1m"]
    quiet = bool(get(state, "quiet"))
    if source == "footsteps":
        return float(table[effective_speed(state, mode)[0]] + (table["quiet_delta"] if quiet else 0))
    if source == "speech":
        return float(table["speech_quiet"] if quiet else table["speech"])
    if source == "actuator":
        return float(table["actuator"])
    raise ValueError("Unknown robot sound source: {!r}".format(source))


def quiet_limit_db(state):
    return float(profile(state)["quiet_limit_db"])


def aid_height_m(state):
    aid = get(state, "standing_on")
    record = state.get("entities", {}).get(aid) if aid else None
    return float((record or {}).get("standing_height_m", AID_HEIGHT_M))


def eye_height_m(state):
    """Head-camera height (m): the profile's eye height for the posture (lying when fallen) plus the standing height
    of the climbing aid the robot stands on."""
    table = profile(state)["eye_height_m"]
    key = "lying" if get(state, "fallen") else (get(state, "posture") if get(state, "posture") in table else "standing")
    height = float(table[key])
    if get(state, "standing_on"):
        height += aid_height_m(state)
    return height


# ----------------------------------------------------------------------
# sensors (TIER1_INTEGRATION 1.3)

def _sensor_ok(state, name):
    return bool((get(state, "sensors") or {}).get(name, True))


def policy_off(state, sensor):
    """True when robot.privacy_policy turns `sensor` ("camera" | "mic") off in the robot's room: the room's type is
    listed under "<sensor>_off" and the policy applies "always" or a person is in the room."""
    policy = get(state, "privacy_policy")
    if not isinstance(policy, dict):
        return False
    rid = state["robot"].get("room")
    if state.get("rooms", {}).get(rid, {}).get("type") not in (policy.get(sensor + "_off") or ()):
        return False
    if policy.get("when") == "always":
        return True
    return any(p.get("location") == rid for p in state.get("people", {}).values())


def imaging(state):
    """Colour imaging is available: power on, camera hardware working, camera switched on, no privacy mode and no
    privacy policy for this room."""
    return is_on(state) and _sensor_ok(state, "camera") and bool(get(state, "camera_on")) and \
        not get(state, "privacy_mode") and not policy_off(state, "camera")


def depth_ok(state):
    """The depth camera and the lidar ring work (they stay on in privacy mode)."""
    return is_on(state) and _sensor_ok(state, "depth")


def can_hear(state):
    """"none" (power off, microphones failed or switched off), "addressed" (privacy mode or a microphone policy:
    on-device wake word, only speech addressed to the robot) or "full"."""
    if not is_on(state) or not _sensor_ok(state, "mics") or not get(state, "mic_on"):
        return "none"
    if get(state, "privacy_mode") or policy_off(state, "mic"):
        return "addressed"
    return "full"


def hears(state, room, loud=False, addressed=False):
    """Whether speech in `room` reaches the robot: its hearing permits it (addressed speech only in privacy mode),
    and the speaker is in the robot's room or loud enough to carry. The timeline owns finer propagation."""
    level = can_hear(state)
    if level == "none" or (level == "addressed" and not addressed):
        return False
    return room == state["robot"].get("room") or bool(loud)


def can_speak(state):
    return is_on(state) and _sensor_ok(state, "speaker")


def sensors_view(state):
    """The single sensor summary for perception, realism and the camera layer."""
    from .perception import flashlight_on
    return {"imaging": imaging(state), "clarity": clarity(state), "depth": depth_ok(state), "mics": can_hear(state),
            "speaker": can_speak(state), "light": flashlight_on(state), "localized": bool(get(state, "localized")),
            "network": bool(get(state, "network"))}


def _check_policy(policy):
    if not isinstance(policy, dict) or set(policy) - {"camera_off", "mic_off", "when"}:
        raise ValueError("privacy_policy is {\"camera_off\": [room types], \"mic_off\": [room types], \"when\"}")
    for key in ("camera_off", "mic_off"):
        value = policy.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError("privacy_policy.{} must be a list of room types".format(key))
    if policy.get("when", "occupied") not in ("occupied", "always"):
        raise ValueError("privacy_policy.when must be \"occupied\" or \"always\"")
    return copy.deepcopy(policy)


def set_sensors(w, camera=None, mic=None, policy=None):
    """Switch the cameras or microphones on or off and set (a dict) or clear (False or {}) the room privacy policy;
    None leaves a setting as it is. Returns the resulting {camera_on, mic_on, privacy_policy}."""
    if camera is not None or mic is not None or policy is not None:
        r = w.robot_m()
        if camera is not None:
            r["camera_on"] = bool(camera)
        if mic is not None:
            r["mic_on"] = bool(mic)
        if policy is not None:
            r["privacy_policy"] = _check_policy(policy) if policy else None
    state = w.s
    return {"camera_on": get(state, "camera_on"), "mic_on": get(state, "mic_on"),
            "privacy_policy": get(state, "privacy_policy")}


# ----------------------------------------------------------------------
# capability problems: None, or (message, category)

def manip_problem(state):
    """Why the robot cannot touch or grasp now, with Ctx.obj's wording."""
    if not get(state, "actuator_ok"):
        return "Actuator fault; request human assistance", "capability"
    if not is_on(state):
        return "Battery depleted; dock or request assistance", "capability"
    if get(state, "fallen"):
        return "The robot has fallen; recover first", "capability"
    if fault(state, "battery_overheat") is not None:
        return OVERHEAT_TEXT, "capability"
    return None


def arm_problem(state, hand=None):
    """manip_problem, then a failed arm (hand None: both arms failed)."""
    problem = manip_problem(state)
    if problem is not None:
        return problem
    if hand in HANDS:
        if arm_health(state, hand) <= 0:
            return "The {} arm has failed; use the other hand".format(hand), "capability"
    elif all(arm_health(state, h) <= 0 for h in HANDS):
        return "Both arms have failed; request help", "capability"
    return None


def mobile_problem(state, legacy_message=None, stairs=False):
    """Why the robot cannot walk now. legacy_message keeps a verb's existing wording for the power, actuator and
    fallen cases (core move: "Robot cannot move; dock, recover or hand off"). stairs=True also needs a profile that
    climbs stairs and legs above half strength."""
    if not is_on(state):
        return legacy_message or POWER_OFF_TEXT, "capability"
    if not get(state, "actuator_ok"):
        return legacy_message or "Actuator fault; request human assistance", "capability"
    if get(state, "fallen"):
        return legacy_message or "The robot has fallen; recover first", "capability"
    legs = float(get(state, "legs_health"))
    if legs <= 0:
        return "The legs have failed; request help", "capability"
    if fault(state, "battery_overheat") is not None:
        return OVERHEAT_TEXT, "capability"
    if get(state, "standing_on"):
        return "Step down first", "safety"
    if not get(state, "localized"):
        return LOCALIZATION_TEXT, "capability"
    if stairs:
        if not profile(state)["stairs"]:
            return "This robot cannot climb stairs", "capability"
        if legs <= LEGS_LIMP + EPS:
            return "Weak legs: stairs are not safe now", "capability"
    return None


# ----------------------------------------------------------------------
# hand soil

def soil_hand(w, hand, kind, amount):
    """Coat a hand's fingers ("left" | "right" | "both") with `kind` (0-1, accumulating). Slippery soil starts a
    latent gripper_slip fault on that hand."""
    from . import body_faults
    hands = HANDS if hand == "both" else (hand,)
    r = w.robot_m()
    soil = copy.deepcopy(get(w.s, "hand_soil") or {})
    for h in hands:
        coat = soil.setdefault(h, {})
        coat[kind] = round(min(1.0, max(0.0, float(coat.get(kind, 0.0)) + float(amount))), 3)
    r["hand_soil"] = soil
    for h in hands:
        if slippery(w.s, h) >= SLIP_FAULT_AT and fault(w.s, "gripper_slip_" + h) is None:
            body_faults.add_fault(w, "gripper_slip", part=h, latent=True, source="soil")


def clean_hand(w, hand, kinds=None):
    """Remove soil from a hand ("left" | "right" | "both"): the given kinds, or all of it. Clears a gripper_slip
    fault that the soil caused once the fingers are no longer slippery."""
    from . import body_faults
    hands = HANDS if hand == "both" else (hand,)
    soil = copy.deepcopy(get(w.s, "hand_soil") or {})
    for h in hands:
        coat = soil.get(h) or {}
        for kind in list(coat):
            if kinds is None or kind in kinds:
                del coat[kind]
        if coat:
            soil[h] = coat
        else:
            soil.pop(h, None)
    w.robot_m()["hand_soil"] = soil
    for h in hands:
        record = fault(w.s, "gripper_slip_" + h)
        if record is not None and record.get("source") == "soil" and slippery(w.s, h) < SLIP_FAULT_AT:
            body_faults.clear_fault(w, "gripper_slip_" + h, "cleaned")


# ----------------------------------------------------------------------
# motion records (TIER1_INTEGRATION 1.4)

def _segments(cells):
    return [geometry.CELL * (DIAGONAL if a[0] != b[0] and a[1] != b[1] else 1.0) for a, b in zip(cells, cells[1:])]


def motion(ctx, path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None):
    """Plan a walk against the battery and write the motion record (what ctx.motion does). path: the cells after
    `start`; connection: the connection crossed at the end (its +1.0 m crossing and, for stairs, the flight are an
    all-or-nothing terminal block, stairs derived from it unless given); extra_s: handling time (a door 1.5 s, the
    settling time of approach and walk_to). Returns the cells actually walked (the affordable prefix) and writes
    ctx.data motion, path, from_room, connection (None when cut) and seconds. A cut prefix is marked
    cut="battery", exhausts=True: settlement then drains the pack to the cutoff and powers the robot down."""
    w = ctx.w
    state = w.s
    mode, note = effective_speed(state, speed or ctx.args.get("speed"))
    if note:
        ctx.note(note)
    here = state["robot"]["room"]
    cells = [tuple(start)] + [tuple(c) for c in path]
    segments = _segments(cells)
    up, down = stairs
    other = None
    if connection is not None:
        conn = state["connections"][connection]
        other = conn["rooms"][1] if conn["rooms"][0] == here else conn["rooms"][0]
        if (up, down) == (0, 0):
            way = stairs_direction(state, conn, here, other)
            steps = int(conn.get("steps", 12)) if way else 0
            up, down = (steps, 0) if way == "up" else (0, steps)
    crossing = CONNECTION_CROSSING_M if connection is not None else 0.0
    load = carried_kg(w)
    power = activity_power_w(state, "motion", held_kg=load) + _hook_power(w, ctx.spec.name)
    k, finish_ok = affordable_prefix(state, segments, mode, load, (up, down), extra_s, crossing, power)
    complete = k == len(segments) and finish_ok
    p = profile(state)
    v = p["speeds_mps"][mode]
    planned = sum(segments) + crossing
    if complete:
        meters = planned
        seconds = meters / v
        seconds += extra_s
        seconds += p["stair_s_per_step"] * (up + down)
    else:
        meters = sum(segments[:k])
        seconds = meters / v
        up = down = 0
    walked = [list(c) for c in cells[1:k + 1]]
    record = {"path": walked, "start": list(cells[0]), "from_room": here,
              "to_room": other if complete and other is not None else here,
              "connection": connection if complete else None, "meters": meters, "planned_m": planned,
              "seconds": seconds, "speed": mode, "load_kg": round(load, 4), "stairs_up": up, "stairs_down": down,
              "cut": None if complete else "battery", "exhausts": not complete}
    ctx.data["motion"] = record
    ctx.data.update(path=[list(c) for c in walked], from_room=here, connection=record["connection"], seconds=seconds)
    return walked


def cut_motion(ctx, meters_done, reason, stairs_done=(0, 0)):
    """A domain stopped the motion early (a blocker, a fall, the stairs): the record keeps the walked prefix of
    meters_done and stairs_done, takes the robot's current room as to_room, sets cut=reason and clears exhausts so
    the robot pays only for what it walked. Returns the updated record (None without one)."""
    record = ctx.data.get("motion")
    if record is None:
        return None
    state = ctx.w.s
    done = max(0.0, min(float(meters_done), float(record["meters"])))
    cells = [tuple(record["start"])] + [tuple(c) for c in record["path"]]
    walked, total = [], 0.0
    for seg, cell in zip(_segments(cells), cells[1:]):
        if total + seg > done + EPS:
            break
        total += seg
        walked.append(list(cell))
    up, down = stairs_done
    room = state["robot"].get("room")
    p = profile(state)
    record.update(path=walked, meters=done, stairs_up=int(up), stairs_down=int(down), to_room=room,
                  connection=record["connection"] if room != record["from_room"] else None, cut=reason, exhausts=False,
                  seconds=done / p["speeds_mps"][record["speed"]] + p["stair_s_per_step"] * (int(up) + int(down)))
    return record


# ----------------------------------------------------------------------
# guards and settlement (called by the engine from BODY-ENGINE on)

def power_guard(state, spec):
    """PowerOff unless the robot is on or the action may run while off (ActionSpec.power_off_ok: wait)."""
    if not is_on(state) and not getattr(spec, "power_off_ok", False):
        raise PowerOff()


def quiet_guard(state, spec, ctx=None):
    """Quiet mode refuses actions louder than quiet_limit_db (ActionSpec.noise_db at 1 m): numeric levels before the
    handler (ctx None), callable ones fn(ctx) after it."""
    level = getattr(spec, "noise_db", None)
    if level is None or not get(state, "quiet") or callable(level) != (ctx is not None):
        return
    level = level(ctx) if callable(level) else level
    if level is not None and float(level) > quiet_limit_db(state):
        raise BodyRefusal(QUIET_TEXT, "social")


ACTIVITY_KINDS = ("physical", "perception", "communication", "control", "cognitive")


def _activity(state, spec, ctx):
    kind = spec.category if spec.category in ACTIVITY_KINDS else "awake"
    arms = getattr(spec, "arms", None)
    if arms is None:
        arms = 1 if kind == "physical" else 0
        target = ctx.args.get("target") if isinstance(ctx.args, dict) else None
        hands = get(state, "hands") or {}
        if kind == "physical" and target and all(hands.get(h) == target for h in HANDS):
            arms = 2
    return kind, arms


def _energy_effect(w):
    w.stat("energy_refusals")


def _hold_check(w, ctx, loads):
    """Safety net for loads that grew after the lift (a filled container, limp mode): above 1.5 x the hand's
    capacity the item slips out, above 1.0 x the robot strains. Returns True when something was dropped."""
    from . import body_faults
    state = w.s
    dropped = False
    for eid, hands, kg in loads:
        cap = _capacity(state, "both" if len(hands) == 2 else hands[0])
        ratio = kg / cap if cap > 0 else float("inf")
        if ratio > OVERLOAD_DROP:
            body_faults.drop_held(w, eid, profile(state)["carry"]["hand_z_m"], "overload")
            ctx.note("Lost hold of {}: too heavy to keep holding".format(eid))
            dropped = True
        elif ratio > 1.0 + EPS:
            ctx.note("Straining to hold {}".format(eid))
    return dropped


def settle_energy(w, ctx, own_s):
    """Pay for one leaf action (TIER1_EMBODIMENT 4.3) after its handler and after-hooks succeeded: the activity's
    power over own_s (its own seconds, nested leaves excluded) plus the walking work of its motion record, or the
    legacy ActionSpec.energy as work. Nothing while powered down or for idle actions (wait: the body system pays
    for idle time); nothing while seated on a live dock (it carries the load). A record that exhausts the pack
    drains it to the cutoff; a shortfall within the hidden buffer completes and powers down; a larger one raises
    the energy refusal (BodyRefusal, counted in stats.energy_refusals after the rollback)."""
    state = w.s
    spec = ctx.spec
    loads = refresh_held(w)                      # also for waits: the body system's idle drain reads the cache
    if getattr(spec, "idle", False) or not is_on(state):
        return None
    load = sum(kg for _, _, kg in loads)
    record = ctx.data.get("motion")
    if _source_rate(w) > 0:
        need = 0.0
    elif record is not None:
        power = activity_power_w(state, "motion", held_kg=load) + _hook_power(w, spec.name)
        need = power * float(own_s) / 3600.0 + motion_work_wh(state, record)
    else:
        kind, arms = _activity(state, spec, ctx)
        power = activity_power_w(state, kind, arms=arms, held_kg=load) + _hook_power(w, spec.name)
        legacy = getattr(spec, "energy", None)
        need = power * float(own_s) / 3600.0 + (float(legacy) * LEGACY_WH_PER_PCT if legacy else 0.0)
    avail = available_wh(state)
    if record is not None and record.get("exhausts"):
        spend_wh(w, avail)
    elif need <= avail + EPS:
        spend_wh(w, need)
    elif need - avail <= profile(state)["energy_buffer_pct"] * full_wh(state) / 100.0:
        spend_wh(w, avail)
        ctx.note(POWERED_DOWN_NOTE)
    else:
        raise _refuse(w, ENERGY_TEXT, "capability", (_energy_effect,))
    r = w.robot_m()
    r["_idle_s"] = 0.0
    r["standby"] = False
    r["_activity"] = "active" if record is not None or spec.category == "physical" else "idle"
    if is_on(state):
        dropped = _hold_check(w, ctx, loads)
        if record is not None and record.get("meters", 0) > 0:
            from . import body_faults
            for hand in HANDS:
                if fault(state, "gripper_slip_" + hand) is not None:
                    dropped = bool(body_faults.slip_check(w, hand, ctx)) or dropped
        if dropped:
            refresh_held(w)
    return None


# ----------------------------------------------------------------------
# the body system (registered by BODY-ENGINE as "kernel.body", order 72, max_dt 30)

def _idle_drain(w, dt, start):
    """Idle time between commands: awake until auto_standby_s, then parked standby (unless a robot_keep_awake hook
    holds it awake), piecewise linear with exact splits at the standby switch and at the cutoff."""
    state = w.s
    p = profile(state)
    auto = float(p["auto_standby_s"])
    idle_s = float(get(state, "_idle_s"))
    keep = any(v is True for v in run_hooks("robot_keep_awake", w))
    if keep:
        t_awake = dt
    elif get(state, "standby"):
        t_awake = 0.0
    else:
        t_awake = min(dt, max(0.0, auto - idle_s))
    held = float(get(state, "_held_kg"))
    p_awake = activity_power_w(state, "awake", held_kg=held) + _hook_power(w, "idle")
    p_standby = activity_power_w(state, "standby", held_kg=held)
    e_awake = p_awake * t_awake / 3600.0
    need = e_awake + p_standby * (dt - t_awake) / 3600.0
    avail = available_wh(state)
    if need >= avail - EPS:
        if avail <= e_awake:
            t_cut = avail / p_awake * 3600.0
        else:
            t_cut = t_awake + (avail - e_awake) / p_standby * 3600.0 if p_standby > 0 else dt
        spend_wh(w, avail, at_s=start + min(dt, t_cut))
    else:
        spend_wh(w, need, at_s=start + dt)
    if is_on(state):
        new_idle = min(auto, idle_s + dt)
        standby = not keep and new_idle >= auto - EPS
        robot = state["robot"]
        if robot.get("_idle_s") != new_idle or robot.get("standby") != standby:
            r = w.robot_m()
            r["_idle_s"] = new_idle
            r["standby"] = standby


def _charge(w, rate, dt):
    """Constant current below cv_from_pct, then constant voltage B(t) = 100 - (100 - B0) exp(-t / tau) with
    tau = (100 - cv_from) / rate minutes (the rate is continuous at the switch); split exactly at the switch."""
    state = w.s
    window = profile(state)["charge"]
    cv = float(window["cv_from_pct"])
    before = float(get(state, "battery"))
    level, minutes = before, dt / 60.0
    if level < cv:
        to_cv = (cv - level) / rate
        if minutes <= to_cv:
            level, minutes = level + rate * minutes, 0.0
        else:
            level, minutes = cv, minutes - to_cv
    if minutes > 0:
        level = 100.0 - (100.0 - level) * math.exp(-minutes * rate / (100.0 - cv))
    if level >= window["full_pct"]:
        level = 100.0
    r = w.robot_m()
    r["battery"] = round(level, DIGITS)
    r["charging"] = level < 100.0
    charged = (level - before) * full_wh(state) / 100.0
    r["energy_wh_charged"] = round(float(get(state, "energy_wh_charged")) + charged, DIGITS)
    if not is_on(state) and gauge(state) >= threshold_pct(state, "wake") - EPS:
        wake(w, "charged")


def _pack_temperature(w, dt, mode, charging):
    """First-order pack temperature toward the room's plus the self-heating of the power state; overheating starts
    the battery_overheat fault, which clears below recover_c."""
    from . import body_faults
    state = w.s
    thermal = profile(state)["pack_thermal"]
    if charging:
        heat = "charging"
    elif not is_on(state):
        heat = "off"
    elif get(state, "standby"):
        heat = "standby"
    elif mode == "idle":
        heat = "idle"
    else:
        heat = "active" if get(state, "_activity") == "active" else "idle"
    target = _robot_room_temperature(state) + thermal["self_heat_c"][heat]
    current = float(get(state, "battery_temp_c"))
    new = round(target + (current - target) * math.exp(-dt / thermal["tau_s"]), 2)
    if state["robot"].get("battery_temp_c") != new:
        w.robot_m()["battery_temp_c"] = new
    active = fault(state, "battery_overheat") is not None
    if new > thermal["overheat_c"] and not active:
        body_faults.add_fault(w, "battery_overheat", source="heat")
    elif new < thermal["recover_c"] and active:
        body_faults.clear_fault(w, "battery_overheat", "cooled")


def system_tick(w, dt, tick):
    """One substep of the body (TIER1_EMBODIMENT 4.4), O(1): idle drain (advance mode "idle", power on, no live
    dock), charging from the robot_charge_source hooks, the pack temperature and the network window. The engine
    sets env._advance_mode: "idle" for wait and refused steps, "covered" when the action already paid."""
    state = w.s
    mode = getattr(getattr(tick, "env", None), "_advance_mode", "covered")
    now = float(state.get("time_s", 0.0))
    raw = _source_rate(w)
    if mode == "idle" and raw <= 0 and is_on(state):
        _idle_drain(w, dt, now - dt)
    rate = _window_rate(state, raw)
    if raw > 0 and rate <= 0 and fault(state, "battery_overheat") is None:
        log_first(w, "charge_refused_temp_s", now)
    charging = rate > 0 and float(get(state, "battery")) < 100.0
    if charging:
        _charge(w, rate, dt)
    elif state["robot"].get("charging"):
        w.robot_m()["charging"] = False
    _pack_temperature(w, dt, mode, charging)
    until = state["robot"].get("network_until_s")
    if until is not None and now >= float(until):
        from . import body_faults
        if fault(state, "network_down") is not None:
            body_faults.clear_fault(w, "network_down", "restored")
        else:
            r = w.robot_m()
            r["network"] = True
            r.pop("network_until_s", None)


# ----------------------------------------------------------------------
# invariant and event effects (registered by BODY-ENGINE)

def robot_stranded(state, baseline):
    """Global failure: the robot powered down away from a dock this episode, or is off and not seated on a dock at
    evaluation although it started on. A robot that dies on a dock without power (a grid outage) is not stranded."""
    stats = state.get("stats") or {}
    base = (baseline or {}).get("stats") or {}
    if float(stats.get("power_downs_off_dock", 0)) > float(base.get("power_downs_off_dock", 0)):
        return "robot_stranded"
    was_on = baseline is None or "robot" not in baseline or is_on(baseline)
    if was_on and not is_on(state) and not get(state, "docked"):
        return "robot_stranded"
    return None


def ev_battery(w, args, env=None):
    """Event effect robot.battery {level}: set the gauge to level %. Threshold crossings count; a gauge at the
    cutoff powers the robot down; a powered-down robot given at least the wake level powers up."""
    state = w.s
    level = min(100.0, max(0.0, float(args["level"])))
    before = gauge(state)
    r = w.robot_m()
    r["battery"] = round(_soc_at(state, level), DIGITS)
    after = gauge(state)
    r["battery_min_pct"] = round(min(float(get(state, "battery_min_pct")), after), DIGITS)
    if after < before:
        _crossings(w, before, after)
    if is_on(state) and after <= threshold_pct(state, "cutoff") + EPS:
        power_down(w, "battery")
    elif not is_on(state) and after >= threshold_pct(state, "wake") - EPS:
        wake(w, "battery_event")
    return True


def ev_network(w, args, env=None):
    """Event effect robot.network {ok, until_s}: the network goes down (network_down, restored at until_s) or up."""
    from . import body_faults
    if args.get("ok", False):
        body_faults.clear_fault(w, "network_down", "event")
    else:
        body_faults.add_fault(w, "network_down", until_s=args.get("until_s"), source="event")
    return True


# ----------------------------------------------------------------------
# robot field declarations (TIER1_INTEGRATION 1.1, TIER1_EMBODIMENT 3.2 / 3.3)

def _from_profile(key):
    def default(state):
        value = profile(state)[key]
        return copy.deepcopy(value) if isinstance(value, dict) else value
    return default


def _battery_temp_default(state):
    return _robot_room_temperature(state)


def _power_state_default(state):
    return "off" if gauge(state) <= threshold_pct(state, "cutoff") + EPS else "on"


def _min_gauge_default(state):
    return round(gauge(state), DIGITS)


_KERNEL = "kernel.body"
for _name, _default_value, _tier, _doc in (
        ("battery", 100.0, "gauge", "True state of charge, % of battery_wh x battery_health"),
        ("body_profile", DEFAULT_PROFILE, "exact", "Selected body profile (data/robot/profiles.json)"),
        ("mass_kg", _from_profile("mass_kg"), "exact", "Robot mass without its load"),
        ("height_m", _from_profile("height_m"), "exact", "Standing height"),
        ("width_m", _from_profile("width_m"), "exact", "Shoulder width"),
        ("reach_m", _from_profile("reach_m"), "exact", "Standing overhead reach"),
        ("max_hand_kg", _from_profile("max_hand_kg"), "exact", "Nominal one-hand lift rating"),
        ("max_mass_kg", _from_profile("max_mass_kg"), "exact", "Nominal two-hand and total carry rating"),
        ("battery_wh", _from_profile("battery_wh"), "exact", "Installed pack's nominal energy"),
        ("battery_health", 1.0, "hidden", "Pack state of health"),
        ("battery_temp_c", _battery_temp_default, "bucket", "Pack temperature"),
        ("battery_min_pct", _min_gauge_default, "hidden", "Lowest gauge reading this episode (evaluation)"),
        ("power_state", _power_state_default, "exact", "on or off"),
        ("standby", False, "exact", "Parked low-power state while idle"),
        ("charging", False, "exact", "Current is flowing into the pack"),
        ("energy_wh_used", 0.0, "hidden", "Energy drawn this episode (evaluation)"),
        ("energy_wh_charged", 0.0, "hidden", "Energy charged this episode (evaluation)"),
        ("body_log", {}, "hidden", "First times of body events (evaluation)"),
        ("arm_health", {"left": 1.0, "right": 1.0}, "detected", "Arm strength per hand; 0 = failed"),
        ("arms_ok", {"left": True, "right": True}, "exact", "Mirror of arm_health > 0"),
        ("legs_health", 1.0, "detected", "Leg strength; at or below 0.5 careful speed only, no stairs"),
        ("actuator_ok", True, "exact", "No whole-body actuator fault"),
        ("grippers", _from_profile("grippers"), "exact", "Gripper type per hand"),
        ("hand_soil", {}, "bucket", "Coating on the fingers per hand {kind: 0..1}"),
        ("camera_clean", 1.0, "bucket", "Lens clarity, at most 1 - lens_scratch"),
        ("sensors", {"camera": True, "depth": True, "mics": True, "speaker": True}, "detected",
         "Sensor hardware works"),
        ("localized", True, "exact", "The robot knows its own pose"),
        ("network", True, "exact", "Network connection"),
        ("fault_state", {}, "detected", "Fault id -> fault record"),
        ("faults", [], "detected", "Fault log (append-only, at most 50 entries)"),
        ("_idle_s", 0.0, "hidden", "Seconds idle (auto-standby)"),
        ("_activity", "idle", "hidden", "Last settled activity (pack heating)"),
        ("_held_kg", 0.0, "hidden", "Cached carried load"),
        ("_hand_kg", {}, "hidden", "Cached load per hand: hand -> [item, kg]"),
        ("_slips", 0, "hidden", "Gripper slip draws so far")):
    register_robot_field(_name, _default_value, _KERNEL, _tier, _doc, materialize=True)
for _name, _default_value, _owner, _tier, _doc, _materialize in (
        ("room", None, "core", "exact", "Current room (written by schema.normalize)", False),
        ("pos", None, "core", "exact", "Current cell (written by schema.normalize)", False),
        ("hands", {"left": None, "right": None}, "core", "exact", "Item held per hand", False),
        ("wearing", [], "core", "exact", "Protective gear worn", False),
        ("stopped", False, "core", "exact", "Stopped by a person", True),
        ("quiet", False, "core", "exact", "Quiet mode", True),
        ("speed_mode", "normal", "core", "exact", "Requested walking speed", True),
        ("posture", "standing", "core", "exact", "standing, crouching, kneeling, sitting or lying", True),
        ("standing_on", None, "navigation_robot", "exact", "Climbing aid stood on", True),
        ("fallen", False, "navigation_robot", "exact", "The robot lies fallen", True),
        ("headlamp_on", False, "navigation_robot", "exact", "Headlamp on", True),
        ("privacy_mode", False, "navigation_robot", "exact", "Privacy mode", True),
        ("docked", False, "navigation_robot", "exact", "Seated on a dock", True),
        ("dock_powered", False, "navigation_robot", "exact", "The dock is seated and live", False),
        ("home", None, "navigation_robot", "exact", "Home dock", False),
        ("dock", None, "navigation_robot", "exact", "Last dock used", False),
        ("home_pose", None, "navigation_robot", "exact", "Home pose", False),
        ("map_known", None, "navigation_robot", "exact", "Rooms on the robot's map", False),
        ("hot_swap", True, "navigation_robot", "exact", "Packs can be swapped off the dock", False),
        ("stuck", None, "navigation_robot", "exact", "Why the robot is stuck", False),
        ("nav_trapped", None, "navigation_robot", "exact", "Door the robot is trapped behind", False),
        ("nav_climbed_from", None, "navigation_robot", "exact", "Cell the robot climbed from", False),
        ("nav_foot_soil", {}, "navigation_robot", "bucket", "Soil under the feet (navigation renders it)", False),
        ("lens_scratch", 0.0, "navigation_robot", "hidden", "Lens cover scratches", False),
        ("battery_reserve_pct", None, "navigation_robot", "exact", "Reserve threshold override", False),
        ("idle_drain_pct_h", 0.0, "navigation_robot", "hidden", "Deprecated idle drain (the body pays for idle)",
         False),
        ("camera_on", True, _KERNEL, "exact", "Cameras switched on (body.set_sensors)", False),
        ("mic_on", True, _KERNEL, "exact", "Microphones switched on (body.set_sensors)", False),
        ("privacy_policy", None, _KERNEL, "exact", "Room-type camera / microphone policy (body.set_sensors)", False),
        ("profile_overrides", None, _KERNEL, "hidden", "Per-episode profile overrides", False),
        ("network_until_s", None, _KERNEL, "hidden", "When the network comes back", False),
        ("water_exposure_s", {}, _KERNEL, "hidden", "Cumulative water exposure by kind", False),
        ("facing_deg", None, "camera", "exact", "Body yaw (grid convention), geometric camera mode", False),
        ("view", None, "camera", "hidden", "Head pose, frames and events, geometric camera mode", False),
        ("hand_contamination", None, "food", "hidden", "Food-safety contamination of the grippers", False)):
    register_robot_field(_name, _default_value, _owner, _tier, _doc, materialize=_materialize)
del _name, _default_value, _tier, _doc, _owner, _materialize

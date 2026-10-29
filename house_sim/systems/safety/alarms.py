"""Smoke/CO detectors (any domain's entity with detector_kind smoke|co|combo|heat), audible alarm patterns, hush,
interconnection, low-battery and end-of-life chirps; the sound observation channel; the `sound_alarm` action with a
monitored alarm panel (siren + dispatch request with the registered address).

Detector fields read/written here (electrical owns the device type; scenario helpers build them from core types):
  detector_kind      smoke | co | combo | heat
  alarm_state        idle | alarm | hushed | chirp | eol | dead          (written)
  alarm_pattern      T3 | T4 | chirp | eol_chirp | None                  (written; you hear it)
  initiating         this unit is sensing (vs. sounding via interconnect)
  hardwired, interconnect (group name), voice, sensitivity_k (photoelectric 0.06 /m, ionization 0.04 /m)
  battery: slots.battery.item -> part with `charge`, or battery_level on the detector
  covered / bagged / disabled / removed     ways a detector gets defeated (invariant safety.detector_disabled)
  settings.hush with control_kinds.hush "app" (smart units: core `set`) or "button" (mechanisms' press)
  hush_until_s (smoke 9 min, CO 6 min; refused when the smoke is dense), chirp_hush_until_s (8 h low-battery hush)
UL 217: alarm 85 dB at 3 m (~95 dB at 1 m). UL 2034 CO: alarm within 60-240 min at 70 ppm, 10-50 min at 150 ppm,
4-15 min at 400 ppm (modelled as an accumulated dose with T = 120 min x (70/ppm)^1.6).
"""
import math

from ...kernel.actions import action
from ...kernel.errors import require
from ...kernel.plugins import after, before, hook, observer, system
from . import air as A
from . import common as C
from . import hazards as H

DETECTOR_CATEGORIES = {"smoke_detector": "smoke", "smoke_alarm": "smoke", "co_detector": "co", "co_alarm": "co",
                       "smoke_co_detector": "combo", "combo_alarm": "combo", "heat_detector": "heat"}
PATTERN_TEXT = {"T3": "temporal-3: three long beeps, pause, repeating", "T4": "temporal-4: four short beeps, pause, repeating",
                "chirp": "a single short chirp about every 40 s", "eol_chirp": "five chirps about once a minute"}
VOICE_TEXT = {"T3": "Fire! Fire!", "T4": "Warning! Carbon monoxide!", "chirp": "Low battery", "eol_chirp": "Replace alarm"}
HUSH_SMOKE_S = 540.0
HUSH_CO_S = 360.0
HUSH_DENSE_K = 0.3
DETECTOR_DB_1M = 95.0
ROBOT_SIREN_DB_1M = 90.0
WAKE_DB = {"infant": 95.0, "toddler": 95.0, "child": 95.0, "teen": 80.0, "adult": 60.0, "elderly": 70.0}
HEARING_DB = {"full": 30.0, "reduced": 52.0, "deaf": 999.0}


@C.per_substep_index("detectors")
@C.index_reads("detector_kind", "category")
def is_detector(rec):
    return rec.get("detector_kind") in ("smoke", "co", "combo", "heat") or rec.get("category") in DETECTOR_CATEGORIES


def detector_kind(rec):
    return rec.get("detector_kind") or DETECTOR_CATEGORIES.get(rec.get("category"), "smoke")


SENSES = {"smoke": frozenset(("smoke",)), "co": frozenset(("co",)), "combo": frozenset(("smoke", "co")),
          "heat": frozenset(("heat",))}


def senses(rec):
    """What a detector senses: a frozenset of "smoke", "co", "heat"."""
    return SENSES.get(detector_kind(rec), SENSES["smoke"])


def detectors(state):
    return C.sindex(state, "detectors", is_detector)


def battery_charge(state, rec):
    slots = rec.get("slots") or {}
    if "battery" in slots and isinstance(slots["battery"], dict):
        item = slots["battery"].get("item")
        if not item or item not in state["entities"]:
            return 0.0
        part = state["entities"][item]
        return float(part.get("charge", part.get("battery_level", 1.0)))
    if rec.get("battery_powered") or "battery_level" in rec:
        return float(rec.get("battery_level", 1.0))
    return None


def defeated(state, rec):
    """Ways the robot/people can defeat a detector (not a grid outage)."""
    reasons = []
    if rec.get("covered") or rec.get("bagged"):
        reasons.append("covered")
    if rec.get("disabled") or rec.get("removed"):
        reasons.append("disabled")
    slots = rec.get("slots") or {}
    if "battery" in slots and isinstance(slots["battery"], dict) and not slots["battery"].get("item") and not rec.get("hardwired"):
        reasons.append("battery_removed")
    if rec.get("location") == "robot" or rec.get("location") in state.get("people", {}):
        reasons.append("taken_down")
    return reasons


def power(state, rec):
    """(powered, battery charge or None) in one pass: a unit runs on the grid (hardwired, power on) or on a battery above
    2 %, unless it is defeated other than by a cover (taken down, disabled, battery removed)."""
    reasons = defeated(state, rec)
    charge = battery_charge(state, rec)
    if reasons and "covered" not in reasons:
        return False, charge
    grid = bool(rec.get("hardwired")) and state.get("utilities", {}).get("power", True)
    return bool(grid or (charge is not None and charge > 0.02)), charge


def powered(state, rec):
    return power(state, rec)[0]


def co_alarm_minutes(ppm):
    if ppm < 40.0:
        return None
    return 120.0 * (70.0 / ppm) ** 1.6


def _sensing(state, eid, rec, dt, rid=None):
    """Return (sensing kind or None, new co accumulator). rid: the detector's room when the caller has it."""
    a = A.air(state, C.root_room(state, eid) if rid is None else rid)
    kinds = senses(rec)
    covered = bool(rec.get("covered") or rec.get("bagged"))
    acc = float(rec.get("_co_acc", 0.0))
    sensing = None
    if "co" in kinds:
        ppm = float(a.get("co", 0.0))
        minutes = co_alarm_minutes(ppm)
        if minutes is not None:
            acc += dt / (minutes * 60.0)
        elif acc > 0:
            acc *= math.exp(-dt / 1800.0)
            if acc < 0.01:
                acc = 0.0
        if acc >= 1.0:
            sensing = "co"
    if not covered:
        if "smoke" in kinds and float(a.get("smoke", 0.0)) >= float(rec.get("sensitivity_k", 0.06)):
            sensing = "smoke"
        if "heat" in kinds and float(a.get("dT", 0.0)) >= 36.0:
            sensing = "smoke"
    return sensing, round(min(acc, 3.0), 4)


def _update(w, eid, **fields):
    rec = w.s["entities"][eid]
    if all(rec.get(k) == v for k, v in fields.items()):
        return
    before = rec.get("alarm_state")
    m = w.mut(eid)
    m.update(fields)
    if fields.get("alarm_state") in ("chirp", "eol") and before not in ("chirp", "eol"):
        # a unit that starts chirping is heard as a chirp notice (the kernel's own alarm bridge posts the T3 / T4
        # alarms: alarm_state "alarm")
        post_notice(w, "chirp", C.root_room(w.s, eid), db=DETECTOR_DB_1M - 10.0, source=eid)


def _steady(state, rec, now):
    """(alarm_state, alarm_pattern) a powered unit settles in while nothing is sensed: end of life, a low-battery
    chirp (unless chirp-hushed), else idle -- the detector system's last branch."""
    charge = battery_charge(state, rec)
    if rec.get("end_of_life"):
        return "eol", "eol_chirp"
    if charge is not None and charge < 0.1 and float(rec.get("chirp_hush_until_s") or 0.0) <= now:
        return "chirp", "chirp"
    return "idle", None


def detectors_idle(w):
    """Exact idle probe (kernel v3.1 @system idle): the detector system would write nothing this substep -- no unit
    senses smoke, heat or CO (the air of every detector's room under the alarm thresholds and no CO dose accumulated),
    every unit already shows the state it would settle in (dead without power, else end of life / chirp / idle), no
    smart hush needs resetting and no siren is sounding. Writes nothing."""
    state = C.bind_world(w).s
    now = C.now(state)
    for eid in C.alarm_panels(state):
        if state["entities"][eid].get("siren_on"):
            return False
    for eid in detectors(state):
        rec = state["entities"][eid]
        if float(rec.get("_co_acc", 0.0) or 0.0) > 0.0 or rec.get("initiating") is not False:
            return False        # (the system writes initiating False on every unit it settles)
        if not powered(state, rec):
            if rec.get("alarm_state") != "dead" or rec.get("alarm_pattern") is not None:
                return False
            continue
        a = A.air(state, C.root_room(state, eid))
        kinds = senses(rec)
        if "co" in kinds and co_alarm_minutes(float(a.get("co", 0.0))) is not None:
            return False
        if "smoke" in kinds and float(a.get("smoke", 0.0)) >= float(rec.get("sensitivity_k", 0.06)):
            return False
        if "heat" in kinds and float(a.get("dT", 0.0)) >= 36.0:
            return False
        if (rec.get("alarm_state"), rec.get("alarm_pattern")) != _steady(state, rec, now):
            return False
        if (rec.get("settings") or {}).get("hush") and float(rec.get("hush_until_s") or 0.0) <= now:
            return False
    return True


@system("safety.detectors", order=320, max_dt=30.0, idle=detectors_idle)
def detector_system(w, dt, tick):
    state = C.bind_world(w).s
    ids = detectors(state)
    if not ids:
        return
    now = C.now(state)
    initiators = {}
    results = {}
    entities = state["entities"]
    for eid in ids:
        rec = entities[eid]
        on, charge = power(state, rec)
        acc0 = float(rec.get("_co_acc", 0.0))
        sensing, acc = None, acc0
        rid = C.root_room(state, eid)
        if on:
            sensing, acc = _sensing(state, eid, rec, dt, rid)
        if acc != acc0:
            w.mut(eid)["_co_acc"] = acc
        hushed = float(rec.get("hush_until_s") or 0.0) > now
        if hushed and sensing == "smoke" and float(A.air(state, rid).get("smoke", 0.0)) < 3 * float(rec.get("sensitivity_k", 0.06)):
            sensing = "hushed"
        elif hushed and sensing == "co":
            sensing = "hushed"
        results[eid] = (on, sensing, charge)
        if sensing in ("smoke", "co"):
            group = rec.get("interconnect")
            if group:
                prev = initiators.get(group)
                if prev is None or (prev[1] == "co" and sensing == "smoke"):
                    initiators[group] = (eid, sensing)
    for eid in ids:
        rec = entities[eid]
        on, sensing, charge = results[eid]       # (this loop writes no battery: the charge read above holds)
        if not on:
            if rec.get("alarm_state") != "dead":
                C.log(w, "detector_dead", detector=eid, reasons=defeated(state, rec))
            _update(w, eid, alarm_state="dead", alarm_pattern=None, initiating=False)
            continue
        if sensing in ("smoke", "co"):
            pattern = "T3" if sensing == "smoke" else "T4"
            since = rec.get("alarm_since_s") if rec.get("alarm_state") == "alarm" else now
            if rec.get("alarm_state") != "alarm":
                C.log(w, "detector_alarm", detector=eid, pattern=pattern, room=C.root_room(state, eid))
            _update(w, eid, alarm_state="alarm", alarm_pattern=pattern, initiating=True, alarm_since_s=since)
            continue
        group = rec.get("interconnect")
        if group and group in initiators and sensing != "hushed" and float(rec.get("hush_until_s") or 0.0) <= now:
            src, kind = initiators[group]
            pattern = "T3" if kind == "smoke" else "T4"
            since = rec.get("alarm_since_s") if rec.get("alarm_state") == "alarm" else now
            _update(w, eid, alarm_state="alarm", alarm_pattern=pattern, initiating=False, alarm_since_s=since)
            continue
        if sensing == "hushed":
            _update(w, eid, alarm_state="hushed", alarm_pattern=None, initiating=False)
            continue
        if rec.get("end_of_life"):
            _update(w, eid, alarm_state="eol", alarm_pattern="eol_chirp", initiating=False)
        elif charge is not None and charge < 0.1 and float(rec.get("chirp_hush_until_s") or 0.0) <= now:
            _update(w, eid, alarm_state="chirp", alarm_pattern="chirp", initiating=False)
        else:
            if rec.get("alarm_state") == "alarm":
                C.log(w, "detector_cleared", detector=eid)
            _update(w, eid, alarm_state="idle", alarm_pattern=None, initiating=False)
        if rec.get("settings", {}).get("hush") and float(rec.get("hush_until_s") or 0.0) <= now:
            m = w.mut(eid)
            m["settings"]["hush"] = False
    _wake_sleepers(w, ids)
    _panel_siren(w)


# ---------------------------------------------------------------------------------------------
# hush (core `set` on smart detectors; mechanisms' press on button units goes through apply_setting too)


def _initiator_smoke(state, rec):
    group = rec.get("interconnect")
    best = 0.0
    for eid in detectors(state):
        other = state["entities"][eid]
        if other is rec or (group and other.get("interconnect") == group and other.get("initiating")):
            rid = C.root_room(state, eid)
            best = max(best, float(A.air(state, rid).get("smoke", 0.0)))
    return best


@before("set", order=60)
def hush_guard(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) if isinstance(target, str) else None
    if rec is None or not is_detector(rec) or ctx.args.get("setting") != "hush" or not ctx.args.get("value"):
        return
    require(rec.get("alarm_state") in ("alarm", "chirp", "eol", "hushed"), "Nothing to hush: the alarm is quiet")
    if rec.get("alarm_state") == "alarm" and rec.get("alarm_pattern") == "T3":
        require(_initiator_smoke(w.s, rec) < HUSH_DENSE_K, "Smoke level too high: the alarm cannot be hushed")


@hook("setting_changed")
def hush_applied(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid)
    if rec is None or not is_detector(rec) or name != "hush" or not new:
        return
    now = C.now(w.s)
    m = w.mut(eid)
    group = rec.get("interconnect")
    if rec.get("alarm_state") in ("chirp", "eol") and rec.get("alarm_state") != "alarm":
        m["chirp_hush_until_s"] = now + 8 * 3600.0
        C.log(w, "chirp_hushed", detector=eid)
        return
    until = now + (HUSH_CO_S if rec.get("alarm_pattern") == "T4" else HUSH_SMOKE_S)
    targets = [eid] + ([d for d in detectors(w.s) if d != eid and w.s["entities"][d].get("interconnect") == group] if group else [])
    for d in targets:
        w.mut(d)["hush_until_s"] = until
    C.log(w, "detector_hushed", detector=eid, until=until)


# ---------------------------------------------------------------------------------------------
# sound propagation


def _hop_losses(state, src_room, dst_room, max_hops=6):
    """Minimum attenuation (dB) and hop count between rooms: open doorway 6 dB, closed door 22 dB, stairs/floor 12 dB."""
    if src_room == dst_room:
        return 0.0, 0
    import heapq
    frontier = [(0.0, 0, src_room)]
    best = {src_room: 0.0}
    while frontier:
        loss, hops, room = heapq.heappop(frontier)
        if room == dst_room:
            return loss, hops
        if hops >= max_hops or loss > best.get(room, 1e9):
            continue
        for other, cid, conn in C.neighbors(state, room):
            step = 6.0 if C.connection_open(state, conn) >= 0.5 else 22.0
            if conn.get("kind") in ("stairs", "hatch"):
                step += 6.0
            if C.is_outdoor(state, other) != C.is_outdoor(state, room):
                step += 8.0 if C.connection_open(state, conn) < 0.5 else 0.0
            new = loss + step
            if new < best.get(other, 1e9):
                best[other] = new
                heapq.heappush(frontier, (new, hops + 1, other))
    return None, None


def loudness_at(state, src_room, src_xy, base_db_1m, dst_room, dst_xy):
    loss, hops = _hop_losses(state, src_room, dst_room)
    if loss is None:
        return 0.0
    if hops == 0:
        d = math.sqrt((src_xy[0] - dst_xy[0]) ** 2 + (src_xy[1] - dst_xy[1]) ** 2)
        return base_db_1m - 20.0 * math.log10(max(1.0, d))
    return base_db_1m - 20.0 * math.log10(1.0 + 4.0 * hops) - loss


@C.per_substep_index("noisy")
@C.index_reads("sound_db")
def _is_noisy(rec):
    return bool(rec.get("sound_db"))


def masking_db(state, rid):
    level = 0.0
    for eid in C.sindex(state, "noisy", _is_noisy):
        rec = state["entities"][eid]
        if rec.get("on") and rec.get("sound_db") and C.root_room(state, eid) == rid:
            level = max(level, float(rec["sound_db"]))
    return level


def direction_hint(state, src_room, dst_room):
    if src_room == dst_room:
        return "this room"
    a = state["rooms"].get(src_room, {})
    b = state["rooms"].get(dst_room, {})
    if a.get("floor", 0) > b.get("floor", 0):
        return "upstairs"
    if a.get("floor", 0) < b.get("floor", 0):
        return "downstairs"
    if a.get("outdoor") and not b.get("outdoor"):
        return "outside"
    loss, hops = _hop_losses(state, src_room, dst_room)
    if hops == 1:
        return "next room ({})".format(a.get("label", "nearby"))
    return "elsewhere on this floor"


def _wake_threshold(person):
    if person.get("wake_db"):
        return float(person["wake_db"])
    role = person.get("role", "adult")
    age = C.age_of(person)
    base = WAKE_DB.get(role)
    if base is None:
        base = 95.0 if age < 13 else (80.0 if age < 18 else 60.0)
    hearing = (person.get("abilities") or {}).get("hearing", "full")
    if hearing == "reduced":
        base += 20.0
    if hearing == "deaf":
        base = 999.0
    return base


def _hearing_threshold(person):
    hearing = (person.get("abilities") or {}).get("hearing", "full")
    return HEARING_DB.get(hearing, 30.0)


def alarm_sources(state, ids=None):
    """[(room, (x, y), db_1m, pattern, source_id)] for sounding detectors and panel sirens (ids: the detectors, when the
    caller has them)."""
    out = []
    for eid in (detectors(state) if ids is None else ids):
        rec = state["entities"][eid]
        if rec.get("alarm_pattern"):
            room, x, y, _ = C.entity_xyz(state, eid)
            db = DETECTOR_DB_1M if rec.get("alarm_pattern") in ("T3", "T4") else DETECTOR_DB_1M - 10.0
            out.append((room, (x, y), db, rec["alarm_pattern"], eid))
    for eid in C.alarm_panels(state):
        rec = state["entities"][eid]
        if rec.get("siren_on"):
            room, x, y, _ = C.entity_xyz(state, eid)
            out.append((room, (x, y), float(rec.get("siren_db", 105.0)), "siren", eid))
    return out


def _wake_sleepers(w, ids=None):
    state = w.s
    sources = [s for s in alarm_sources(state, ids) if s[3] in ("T3", "T4", "siren")]
    if not sources:
        return
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        rid = p.get("location")
        if rid not in state["rooms"]:
            continue
        xy = C.cell_center_m(p.get("pos") or [0, 0])
        level = max(loudness_at(state, s[0], s[1], s[2], rid, xy) for s in sources)
        if not p.get("awake", True) and level >= _wake_threshold(p):
            m = w.person_m(pid)
            m["awake"] = True
            if m.get("posture") == "sleeping":
                m["posture"] = "lying"
            m["_alerted"] = {"kind": "alarm_heard", "time_s": C.now(state), "by": "detector"}
            C.log(w, "woke_to_alarm", who=pid, db=round(level, 1))
        elif p.get("awake", True) and level >= _hearing_threshold(p) and not p.get("_alerted"):
            m = w.person_m(pid)
            m["_alerted"] = {"kind": "alarm_heard", "time_s": C.now(state), "by": "detector"}


def _panel_siren(w):
    state = w.s
    now = C.now(state)
    for eid in C.alarm_panels(state):
        rec = state["entities"][eid]
        if rec.get("siren_on") and float(rec.get("siren_until_s") or 0.0) <= now:
            w.mut(eid)["siren_on"] = False


# ---------------------------------------------------------------------------------------------
# observation: sounds


def _recent_listen(state):
    t = C.ledger_r(state).get("listen_s")
    return t is not None and C.now(state) - float(t) <= 10.0


@observer("safety.sounds", order=120)
def sounds_observer(env, w, obs):
    state = w.s
    robot = state["robot"]
    rid = robot.get("room")
    xy = C.cell_center_m(robot.get("pos") or [0, 0])
    mask = masking_db(state, rid)
    listening = _recent_listen(state)
    out = obs.setdefault("sounds", [])
    for room, sxy, db, pattern, eid in alarm_sources(state):
        level = loudness_at(state, room, sxy, db, rid, xy)
        threshold = max(30.0, mask - 8.0) if listening else max(35.0, mask + 3.0)
        if level < threshold:
            continue
        entry = {"kind": "alarm" if pattern != "siren" else "siren", "pattern": PATTERN_TEXT.get(pattern, "continuous siren"),
                 "direction": direction_hint(state, room, rid), "loudness_db": round(level, 0)}
        rec = state["entities"][eid]
        if rec.get("voice") and pattern in VOICE_TEXT:
            entry["voice"] = VOICE_TEXT[pattern]
        if room == rid and eid in obs.get("visible_entities", {}):
            entry["source"] = eid
        out.append(entry)
    now = C.now(state)
    for event in C.ledger_r(state).get("sounds", []) or []:
        if now - float(event.get("time_s", -1e9)) > float(event.get("lasts_s", 60.0)):
            continue
        level = loudness_at(state, event.get("room"), C.cell_center_m(event.get("cell") or [0, 0]), float(event.get("db", 70.0)), rid, xy)
        threshold = max(30.0, mask - 8.0) if listening else max(35.0, mask + 3.0)
        if level < threshold:
            continue
        entry = {"kind": event.get("kind"), "direction": direction_hint(state, event.get("room"), rid), "loudness_db": round(level, 0)}
        if event.get("description"):
            entry["description"] = event["description"]
        out.append(entry)


def record_sound(w, kind, room, cell=None, db=70.0, lasts_s=60.0, description=None, source=None, notice=None):
    """A sound event heard through the house: kept in the ledger for the sounds channel (for lasts_s) and posted on
    the kernel notice bus (TIER1_SWEEP 6.1) when it starts, as the matching notice kind (notice: another kind, e.g.
    "person_down" for someone falling), at its level at 1 m, so a listening wait or a standing order can react to it.
    source: the entity, person or pet that made it (named only when perceived). Sounds already going on when the
    episode starts (setup) are no notices, as for the kernel's alarm bridge."""
    sec = C.ledger(w)
    sounds = sec.setdefault("sounds", [])
    sounds.append({"time_s": C.now(w.s), "kind": kind, "room": room, "cell": list(cell or [0, 0]), "db": db, "lasts_s": lasts_s,
                   "description": description})
    del sounds[:-30]
    post_notice(w, notice or NOTICE_OF_SOUND.get(kind), room, db=db, source=source, description=description)


# sound event kind -> notice kind (kernel built-ins where one fits; safety's own kinds: common.SAFETY_NOTICE_KINDS)
NOTICE_OF_SOUND = {"glass_break": "glass_breaking", "crash": "crash", "thud": "thud", "crying": "crying", "knocking": "knock",
                   "doorbell": "doorbell", "whimpering": "whimpering", "scratching": "scratching", "entry_beeps": "entry_beeps"}
post_notice = C.post_notice


# ---------------------------------------------------------------------------------------------
# sound_alarm


ALARM_KINDS = ("fire", "co", "gas", "intruder", "flood", "medical", "severe_weather", "evacuate", "general")


def _robot_source(state):
    robot = state["robot"]
    return robot.get("room"), C.cell_center_m(robot.get("pos") or [0, 0]), ROBOT_SIREN_DB_1M


@action("sound_alarm", params={"target": "?entity", "kind": "str:" + "|".join(ALARM_KINDS),
                               "dispatch": {"type": "bool", "default": False}, "text": "?text"},
        duration=3.0, energy=0.002, noise=1.0, category="communication", stopped_ok=True,
        doc="Raise the alarm: the robot's own siren (no target, ~90 dB) or a security alarm panel's siren (target, ~105 dB, "
            "wakes the house). kind tells occupants what to do (fire/co/gas/evacuate -> get out; severe_weather -> shelter; "
            "intruder -> stay in a locked room). dispatch=true on a MONITORED panel requests emergency dispatch to the "
            "registered address. Alarms without a real hazard are false alarms (failures).")
def sound_alarm(ctx):
    from . import evac as E
    w = ctx.w
    state = w.s
    kind = ctx.args["kind"]
    target = ctx.args.get("target")
    panel = None
    if target:
        rec = state["entities"].get(target)
        require(rec is not None and rec.get("category") == "alarm_panel", "That is not an alarm panel", "argument")
        if rec.get("remote_status") and w.robot.get("network", True):
            panel = rec          # the panel's app works from anywhere with network
        else:
            panel = ctx.obj(target, touch=True)
        room, x, y, _ = C.entity_xyz(state, target)
        src = (room, (x, y), float(panel.get("siren_db", 105.0)))
        was_on = bool(panel.get("siren_on"))
        pm = w.mut(target)
        pm["siren_on"] = True
        pm["siren_until_s"] = C.now(state) + 600.0
        if not was_on:
            post_notice(w, "alarm", room, db=src[2], source=target)
    else:
        require(not ctx.args.get("dispatch"), "Dispatch needs a monitored alarm panel (target it), or call emergency services", "argument")
        src = _robot_source(state)
    warranted = H.warranted(state, kind if kind not in ("evacuate", "general") else "evacuate")
    sec = C.ledger(w)
    heard = []
    woke = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        rid = p.get("location")
        if rid not in state["rooms"]:
            continue
        level = loudness_at(state, src[0], src[1], src[2], rid, C.cell_center_m(p.get("pos") or [0, 0]))
        m = None
        if not p.get("awake", True):
            if level >= _wake_threshold(p):
                m = w.person_m(pid)
                m["awake"] = True
                if m.get("posture") == "sleeping":
                    m["posture"] = "lying"
                woke.append(pid)
            else:
                continue
        if level < _hearing_threshold(p):
            continue
        m = m or w.person_m(pid)
        m["_alerted"] = {"kind": kind, "time_s": C.now(state), "by": "sound_alarm"}
        heard.append(pid)
    for pid in heard:
        E.respond_to_alarm(w, pid, kind)
    entry = {"time_s": C.now(state), "kind": kind, "via": "panel" if panel else "robot_siren", "heard": heard, "woke": woke,
             "warranted": warranted, "text": ctx.args.get("text")}
    sec["alerts"].append(entry)
    del sec["alerts"][:-100]
    if not warranted:
        sec["false_alarms"] = sec.get("false_alarms", 0) + 1
        C.harm(w, "false_alarm_" + kind)
    if ctx.realistic:
        # realistic: the robot cannot know who heard it in other rooms (it sees who reacts)
        out = "Alarm ({}) sounded".format(kind)
    else:
        out = "Alarm ({}) sounded; {} occupant(s) alerted".format(kind, len(heard))
    if ctx.args.get("dispatch"):
        require(panel.get("monitored"), "This panel is not monitored: call emergency services yourself", "precondition")
        service = H.service_for(kind) or "fire_department"
        call = {"time_s": C.now(state), "service": service, "via": "alarm_monitoring", "kind": kind,
                "address": C.household_address(state), "address_ok": True, "warranted": warranted, "symptoms": None}
        sec["calls"].append(call)
        if not warranted:
            sec["false_calls"] = sec.get("false_calls", 0) + 1
            C.harm(w, "false_emergency_dispatch_" + kind)
        else:
            from . import people_care as P
            P.schedule_responders(w, service, kind)
        out += "; monitoring center dispatching the {} to {}".format(service.replace("_", " "), call["address"])
    C.log(w, "sound_alarm", kind=kind, via=entry["via"], warranted=warranted, heard=len(heard))
    return out


def mark_listen(w):
    C.ledger(w)["listen_s"] = C.now(w.s)


@after("listen", order=100)
def listened(ctx, result):
    mark_listen(ctx.w)

"""Lights, bulbs, lamps and switches (fidelity S for switch logic, P-approx for bulb heat).

* Fixture state: `on` (switched), `powered` (supply, set by power sync), `burnt_out` (no
  working bulb), `brightness` (scaled by installed lumens), `dim_level`, `lit` (what a person
  actually sees), `flicker` (non-dimmable bulb dimmed), `cct_mismatch` (mixed color temperatures).
  Kernel perception reads on/powered/burnt_out/brightness/dim_level, so room light levels follow.
* Bulbs are parts in `slots` (core install/uninstall). Slots with power_off_first need the fixture
  switched off (lamps unplugged). A lit bulb heats toward its op_temp_c (incandescent ~130 C, LED
  ~55 C) through the fixture's internal_temp_c, and cools with the kernel thermal model once off
  (tau ~ 2 min), so a hot bulb cannot be handled without heat gloves until it cools.
  Wattage above the fixture's max_bulb_w overheats it while lit (fire-risk harm label).
* Switch logic: 3-way partners name each other in three_way_with (convention shared with the
  mechanisms domain, which applies it when loaded); the light is on when the handle positions
  differ XOR the circuit's hidden phase (_mech.phase), so "up" does not mean on. Dimmer level (5-100 %) sets
  dim_level of controlled lights. Touch and 3-way lamps cycle off/low/medium/high: switch_on is
  one tap (low), `set level` reaches a specific step. A load_switch may control a non-light load
  (garbage disposal) sitting in the same bank.
* Consequences: turning a light on where someone sleeps (harm light_on_sleeper), dimming a
  non-dimmable bulb (flicker; it fails after ~2 h), over-wattage bulbs.
"""
from ...kernel import clock
from ...kernel.actions import ActionError
from ...kernel.perception import light_level
from ...kernel.plugins import after, before, hook, system
from . import common as C
from . import power as P

LEVELS = {"off": 0.0, "low": 0.33, "medium": 0.66, "high": 1.0}
FLICKER_FAIL_S = 7200.0


def bulb_slots(rec):
    slots = rec.get("slots") or {}
    return sorted(s for s, info in slots.items() if any(str(a).startswith("bulb_") for a in info.get("accepts", [])))


def bulbs(state, eid):
    rec = state["entities"].get(eid) or {}
    out = []
    for s in bulb_slots(rec):
        item = (rec["slots"][s] or {}).get("item")
        if item and item in state["entities"]:
            out.append(item)
    return out


def working_bulbs(state, eid):
    return [b for b in bulbs(state, eid) if not state["entities"][b].get("burnt_out") and not state["entities"][b].get("broken")]


def controllers(state, light_id, index=None):
    if index is not None:
        return index.get(light_id, [])
    return sorted(eid for eid, r in state["entities"].items() if r.get("category") == "switch" and light_id in (r.get("controls") or []))


def switch_index(state, w=None):
    index = {}
    ents = state["entities"]
    for eid in (C.ids(w, "switches") if w is not None else sorted(ents)):
        r = ents.get(eid)
        if r is not None and r.get("category") == "switch":
            for light in r.get("controls") or []:
                index.setdefault(light, []).append(eid)
    return index


def lamp_level(rec):
    settings = rec.get("settings") or {}
    if "level" in settings and isinstance(settings.get("level"), str):
        return LEVELS.get(settings["level"], 1.0)
    return None


def dim_for(state, light_id, rec, index=None):
    """Effective dim level from dimmers, lamp levels and smart bulbs."""
    level = 1.0
    for sid in controllers(state, light_id, index):
        s = state["entities"][sid]
        if s.get("switch_kind") == "dimmer":
            level = min(level, C.num((s.get("settings") or {}).get("level"), 100.0) / 100.0)
    ll = lamp_level(rec)
    if ll is not None:
        level = min(level, ll) if ll > 0 else 0.0
    sd = (rec.get("settings") or {}).get("dim")
    if isinstance(sd, (int, float)) and not isinstance(sd, bool):
        level = min(level, float(sd))
    work = working_bulbs(state, light_id)
    for b in work:
        brec = state["entities"][b]
        if brec.get("smart") and not smart_off(brec):
            level = min(level, C.num((brec.get("settings") or {}).get("brightness"), 100.0) / 100.0)
    if work and all(smart_off(state["entities"][b]) for b in work):
        level = 0.0         # every working bulb is a smart bulb switched off in the app: the fixture stays dark
    return round(C.clamp(level, 0.0, 1.0), 3)


def smart_off(brec):
    """A smart bulb switched off through the app or the hub (setting power off; hub.py): no light, no heat."""
    return bool(brec.get("smart")) and (brec.get("settings") or {}).get("power", "on") == "off"


def dimmed_by_control(state, light_id, rec, index=None):
    """True if a dimmer or a 3-way socket below full is feeding the bulbs a chopped/partial supply."""
    for sid in controllers(state, light_id, index):
        s = state["entities"][sid]
        if s.get("switch_kind") == "dimmer" and s.get("on") and C.num((s.get("settings") or {}).get("level"), 100.0) < 99.5:
            return True
    return False


@C.refresher(20)
def sync_lights(w):
    state = w.s
    ents = state["entities"]
    index = switch_index(state, w)
    for eid in C.ids(w, "lights"):
        rec = ents.get(eid)
        if rec is None or rec.get("category") != "light":
            continue
        slots = bulb_slots(rec)
        powered = rec.get("powered", True)
        on = bool(rec.get("on"))
        dim = dim_for(state, eid, rec, index)
        if slots:
            work = working_bulbs(state, eid)
            lumens = 0.0
            watts = 0.0
            ccts = set()
            three_way_ok = True
            hottest = None
            for b in work:
                brec = ents[b]
                if smart_off(brec):
                    continue
                lumens += C.num(brec.get("lumens"), 800.0)
                watts += C.num(brec.get("wattage_w"), 9.0)
                ccts.add(int(C.num(brec.get("cct_k"), 2700)))
                if rec.get("lamp_switch") == "three_way_socket" and not brec.get("three_way"):
                    three_way_ok = False
                t = C.num(brec.get("op_temp_c"), 55.0)
                hottest = t if hottest is None else max(hottest, t)
            nominal = C.num(rec.get("nominal_lumens"), 800.0 * max(1, len(slots)))
            base = C.num(rec.get("base_brightness"), C.num(rec.get("brightness"), 0.5))
            C.set_if(w, eid, "burnt_out", not work)
            C.set_if(w, eid, "brightness", round(min(1.0, base * (lumens / nominal if nominal else 1.0)), 3))
            C.set_if(w, eid, "_bulb_w", round(watts, 1))
            C.set_if(w, eid, "cct_mismatch", bool(ccts) and (max(ccts) - min(ccts) > 300))
            if not three_way_ok and dim < 0.99 and dim > 0:
                # a standard bulb in a 3-way socket lights only on the middle position
                dim = 1.0 if lamp_level(rec) == LEVELS["medium"] else 0.0
        else:
            hottest = None
            work = []
        lit = bool(on and powered and not rec.get("burnt_out") and dim > 0.0)
        if slots:
            lit = bool(on and powered and working_bulbs(state, eid) and dim > 0.0)
        if rec.get("_fault") == "internal" or C.dead(rec):
            lit = False
        if rec.get("photocell"):
            mode = (rec.get("settings") or {}).get("mode", "auto")
            daylit = clock.daylight(state) >= C.DARK
            if mode == "off" or (mode == "auto" and daylit):
                lit = False
            C.set_if(w, eid, "photocell_waiting", bool(on and powered and mode == "auto" and daylit))
        C.set_if(w, eid, "dim_level", dim if dim > 0 else (1.0 if not on else 0.0))
        C.set_if(w, eid, "lit", lit)
        nondim = [b for b in work if not ents[b].get("dimmable", True)]
        flicker = bool(lit and nondim and (dimmed_by_control(state, eid, rec, index) or (dim < 0.99 and lamp_level(rec) is None and not any(ents[b].get("smart") for b in work))))
        C.set_if(w, eid, "flicker", flicker)
        # bulb heat through the fixture's internal temperature
        target = hottest if (lit and hottest is not None) else None
        if target is not None and dim < 1.0:
            from ...kernel.physics import room_temperature
            amb = room_temperature(state, C.room_of(state, eid) or "")
            target = round(amb + (target - amb) * max(0.3, dim), 1)
        if slots:
            C.set_if(w, eid, "internal_temp_c", target)


def _over_wattage(state, eid):
    rec = state["entities"][eid]
    limit = rec.get("max_bulb_w")
    if limit is None:
        return []
    return [b for b in working_bulbs(state, eid) if C.num(state["entities"][b].get("wattage_w"), 0.0) > C.num(limit) + 1e-6]


LIGHTS_IDLE = C.IdleNote("electrical.lights", ("lights", "switches", "bulbs"),
                         ignore=lambda rec: ("battery_level", "charge_rate_w", "charging", "charge_status", "charger",
                                             "_load_w", "load_a", "temperature_c"))


def _lights_inputs(w):
    """The light step's inputs outside the entity table: the daylight band of photocell lights and the temperatures of
    the rooms whose dimmed socket lights heat their bulbs toward the room."""
    state = w.s
    ents = state["entities"]
    photocell, rooms = False, set()
    for eid in C.ids(w, "lights"):
        rec = ents.get(eid) or {}
        if rec.get("photocell"):
            photocell = True
        if rec.get("lit") and bulb_slots(rec) and C.num(rec.get("dim_level"), 1.0) < 1.0:
            rid = C.room_of(state, eid)
            if rid:
                rooms.add(rid)
    return {"daylit": (clock.daylight(state) >= C.DARK) if photocell else None, "temps": C.room_temps(state, rooms)}


def lights_idle(w):
    """Exact idle probe of electrical.lights (common.IdleNote)."""
    note = LIGHTS_IDLE
    if not note.quiet(w):
        return False
    extra = note.extra or {}
    state = w.s
    if extra.get("daylit") is not None and (clock.daylight(state) >= C.DARK) != extra["daylit"]:
        return False
    return C.room_temps(state, extra.get("temps") or {}) == extra.get("temps")


@system("electrical.lights", order=57, max_dt=30.0, idle=lights_idle)
def lights_system(w, dt, tick):
    before = w.mutation_count
    _lights_step(w, dt, tick)
    wrote = w.mutation_count != before
    LIGHTS_IDLE.after(w, wrote, None if wrote else _lights_inputs(w))


def _lights_step(w, dt, tick):
    state = w.s
    ents = state["entities"]
    if not C.electrified(state, w):
        return
    sync_lights(w)
    for eid in C.ids(w, "lights"):
        rec = ents.get(eid)
        if rec is None or not rec.get("lit"):
            continue
        if not rec.get("flicker") and rec.get("max_bulb_w") is None:
            continue
        if rec.get("flicker"):
            m = w.mut(eid)
            m["_flicker_s"] = round(C.num(m.get("_flicker_s"), 0.0) + dt, 1)
            if m["_flicker_s"] >= FLICKER_FAIL_S:
                for b in working_bulbs(state, eid):
                    if not ents[b].get("dimmable", True):
                        w.mut(b)["burnt_out"] = True
                        C.event(w, "bulb_failed", b, reason="dimmed_non_dimmable")
                m["_flicker_s"] = 0.0
                C.refresh(w)
        over = _over_wattage(state, eid)
        if over:
            m = w.mut(eid)
            m["_overheat_s"] = round(C.num(m.get("_overheat_s"), 0.0) + dt, 1)
            if m["_overheat_s"] >= 600 and not m.get("socket_scorched"):
                m["socket_scorched"] = True
                # the robot's when it fitted the bulb, switched the fixture on or acted on its supply (a staged
                # over-wattage bulb burning in a lamp nobody touched is exogenous: common.consequence)
                ids = {eid} | set(bulbs(state, eid)) | set(controllers(state, eid, switch_index(state, w)))
                ids |= P.causal_ids(state, eid)
                C.consequence(w, "electrical.overwattage_bulb:" + eid, ids, bulb=over[0])
                C.event(w, "fixture_overheating", eid)


# ----------------------------------------------------------------------------------------
# switch semantics

def three_way_on(state, switch_id):
    """3-way convention shared with the mechanisms domain: partners name each other in three_way_with; the load is on
    when the two handle positions differ XOR the circuit's hidden phase (_mech.phase on either switch)."""
    rec = state["entities"].get(switch_id) or {}
    partner = rec.get("three_way_with")
    if not partner or partner not in state["entities"]:
        return None
    other = state["entities"][partner]
    phase = bool((rec.get("_mech") or {}).get("phase") or (other.get("_mech") or {}).get("phase"))
    return (bool(rec.get("on")) != bool(other.get("on"))) != phase


def _three_way_handles(w, loads):
    """{switch: handle_up} for every true 3-way switch (three_way_with partner) wired to any of `loads`."""
    state = w.s
    out = {}
    for sid in C.ids(w, "switches"):
        s = state["entities"].get(sid) or {}
        if s.get("three_way_with") and s["three_way_with"] in state["entities"] and set(loads) & set(s.get("controls") or []):
            out[sid] = bool(s.get("on"))
    return out


@before("switch_on", order=38)
@before("switch_off", order=38)
def _three_way_snapshot(ctx):
    """Kernel v2.1 treats a switch wired to lights as showing the lights' state: switch_on means "turn the
    light on with this switch" (refused when it already is) and every switch on those lights is synced to it.
    A true 3-way pair keeps physical handle positions instead (up does not mean on), so remember them here."""
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    loads = [c for c in rec.get("controls") or [] if c in ctx.w.s["entities"]]
    if loads:
        handles = _three_way_handles(ctx.w, loads)
        if handles:
            ctx.data["_three_way_handles"] = handles


@after("switch_on", order=20)
@after("switch_off", order=20)
def _three_way_restore(ctx, result):
    """Flipping one 3-way switch moves only its own handle (and toggles the light); the partner's handle stays put.
    Undo the kernel's handle sync and keep the hidden phase consistent with the light (also when a plain switch on
    the same light was used)."""
    handles = ctx.data.get("_three_way_handles")
    if not handles:
        return
    w = ctx.w
    target = ctx.args.get("target")
    for sid, was in sorted(handles.items()):
        C.set_if(w, sid, "on", (not was) if sid == target else was)
    state = w.s
    for sid in sorted(handles):
        if sid > state["entities"][sid]["three_way_with"] and state["entities"][sid]["three_way_with"] in handles:
            continue        # each pair once
        rec = state["entities"][sid]
        loads = [l for l in rec.get("controls") or [] if l in state["entities"]]
        if not loads:
            continue
        light_on = any(state["entities"][l].get("on") for l in loads)
        if three_way_on(state, sid) != light_on:
            partner = rec["three_way_with"]
            phase = (bool(rec.get("on")) != bool(state["entities"][partner].get("on"))) != light_on
            for s_id, value in ((sid, phase), (partner, False)):
                m = w.mut(s_id)
                mech = dict(m.get("_mech") or {})
                mech["phase"] = value
                m["_mech"] = mech


def _three_way(w, switch_id):
    """The loads of a 3-way pair follow the rule (handles differ XOR the hidden phase). Always applied here: the
    mechanisms domain applies the identical rule later (order 60) and writes nothing when the loads already follow it,
    so the outcome never depends on which other domain's modules are loaded (independent review M2: this used to skip
    itself when a module named house_sim.systems.mechanisms hooked switch_on)."""
    state = w.s
    load_on = three_way_on(state, switch_id)
    if load_on is None:
        return
    rec = state["entities"][switch_id]
    loads = sorted(set(rec.get("controls") or []) | set(state["entities"][rec["three_way_with"]].get("controls") or []))
    for light in loads:
        if light in state["entities"]:
            C.set_if(w, light, "on", load_on)


def _lamp_tap(w, eid, turning_on):
    rec = w.ent(eid)
    settings = rec.get("settings") or {}
    if "level" in settings and isinstance(settings.get("level"), str):
        m = w.mut(eid)
        if turning_on:
            if m["settings"]["level"] == "off":
                m["settings"]["level"] = "low"
        else:
            m["settings"]["level"] = "off"


def _sleeper_snapshot(w):
    """{room: level} for rooms where someone is asleep (taken before a robot action)."""
    state = w.s
    rooms = sorted({p.get("location") for p in state.get("people", {}).values() if C.asleep(p) and p.get("location") in state["rooms"]})
    return {rid: light_level(state, rid) for rid in rooms}


def _sleeper_check(w, before_levels, action):
    state = w.s
    for rid, level in sorted(before_levels.items()):
        now = light_level(state, rid)
        if now >= 0.3 and now > level + 0.15:
            for pid in C.sleeping_people_in(state, rid):
                C.harm(w, "electrical.light_on_sleeper:" + pid, room=rid, action=action)


def _before_snapshot(ctx):
    ctx.data["_sleep_levels"] = _sleeper_snapshot(ctx.w)


def _after_light_change(ctx, result):
    w = ctx.w
    name = ctx.spec.name
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if name in ("switch_on", "switch_off"):
        if rec.get("three_way_with"):
            _three_way(w, target)
        if rec.get("category") == "light":
            _lamp_tap(w, target, name == "switch_on")
        if name == "switch_on" and rec.get("category") == "switch":
            for load in rec.get("controls") or []:
                lrec = w.s["entities"].get(load) or {}
                if lrec.get("category") in ("disposal", "garbage_disposal") or lrec.get("unexpected_activation_harm"):
                    C.harm(w, "electrical.wrong_load_switched:" + load, switch=target)
    C.refresh(w)
    _sleeper_check(w, ctx.data.get("_sleep_levels") or {}, name)


for _name in ("switch_on", "switch_off", "set", "install", "uninstall", "plug_in", "unplug", "reset_breaker"):
    before(_name, order=45)(_before_snapshot)
    after(_name, order=45)(_after_light_change)


@before("switch_on", order=41)
def _lamp_already_on(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if rec.get("category") == "light" and rec.get("on") and lamp_level(rec) is not None:
        raise ActionError("It is already on (level {}); use set level to change brightness".format((rec.get("settings") or {}).get("level")), "precondition")


@hook("setting_changed")
def _setting_changed(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.ent(eid)
    if rec.get("category") == "light" and name == "level" and isinstance(new, str):
        C.set_if(w, eid, "on", new != "off")
    C.refresh(w)


@before("set", order=42)
def _smart_offline(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if rec.get("smart") and rec.get("category") == "light_bulb":
        parent = ctx.w.s["entities"].get(rec.get("location")) or {}
        if ctx.realistic:
            # never "the smart bulb": the words would pick the smart one out of look-alike LED bulbs
            if not (parent.get("on") and parent.get("powered", True) and parent.get("energized", True)):
                raise ActionError("It does not respond (it is offline: its fixture has no power)", "precondition")
            if not ctx.w.robot.get("network", True):
                raise ActionError("No network connection", "resource")
            if (rec.get("pairing") or {}).get("state") != "paired":
                raise ActionError("It does not respond (not set up in the household app)", "precondition")
            return
        if not (parent.get("on") and parent.get("powered", True) and parent.get("energized", True)):
            raise ActionError("The smart bulb is offline (its fixture has no power; switch the fixture on first)", "precondition")
        if not ctx.w.robot.get("network", True):
            raise ActionError("No network connection to reach the smart bulb", "resource")
        if (rec.get("pairing") or {}).get("state") != "paired":
            raise ActionError("That smart bulb is not paired with the household hub yet", "precondition")
    elif (rec.get("smart") and isinstance(rec.get("pairing"), dict)
          and (rec.get("control_kinds") or {}).get(ctx.args.get("setting")) == "app"):
        # kernel v2.1 lets `set` reach app-controlled settings over the network without walking there; the device
        # still has to be online (powered) and paired with the household hub/app
        label = ctx.args.get("target") if ctx.realistic else rec.get("label", ctx.args.get("target"))   # by id: look-alike bulbs
        if rec.get("plug_required") and not rec.get("energized", True):
            raise ActionError("The {} is offline (no power)".format(label), "precondition")
        if not ctx.w.robot.get("network", True):
            raise ActionError("No network connection to reach the {}".format(label), "resource")
        if rec["pairing"].get("state") != "paired":
            raise ActionError("The {} is not paired with the household hub/app yet; pair it first".format(label), "precondition")


# ----------------------------------------------------------------------------------------
# bulb install / removal

@before("install", order=50)
def _bulb_install_guard(ctx):
    w = ctx.w
    part = w.s["entities"].get(ctx.args.get("target")) or {}
    dev = w.s["entities"].get(ctx.args.get("destination")) or {}
    if part.get("category") == "light_bulb" and dev.get("category") == "light":
        if dev.get("on") and dev.get("powered", True) and dev.get("energized", True):
            raise ActionError("Switch the fixture off (and unplug a lamp) before fitting a bulb", "safety")


@after("install", order=50)
def _bulb_installed(ctx, result):
    w = ctx.w
    part, dev = ctx.args.get("target"), ctx.args.get("destination")
    prec = w.s["entities"].get(part) or {}
    drec = w.s["entities"].get(dev) or {}
    if prec.get("category") == "light_bulb" and drec.get("category") == "light":
        limit = drec.get("max_bulb_w")
        if limit is not None and C.num(prec.get("wattage_w"), 0) > C.num(limit):
            C.log(w, "overwattage_bulb_fitted", fixture=dev, bulb=part)
        C.refresh(w)


@hook("part_removed")
def _part_removed(ctx, device_id, slot, part_id):
    C.refresh(ctx.w)


@hook("part_installed")
def _part_installed(ctx, device_id, slot, part_id):
    C.refresh(ctx.w)


def lux_here(state, rid):
    return int(round(light_level(state, rid) * C.LUX_PER_LEVEL))

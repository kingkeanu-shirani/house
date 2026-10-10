"""Realistic perception helpers for the appliances_cooking domain (kernel v2.4 - v2.8, docs/PERCEPTION.md).

Every helper is the identity / a no-op in oracle mode (env.realism is None), so oracle observations, inspector output
and action messages stay byte-for-byte as before. In realistic mode:

  * sounds describe what is heard (a grinding rattle, a loud click), never the diagnosis ("compressor fails") and never
    a `normal` verdict; a sound is attributed to one appliance only when the robot is close to it (within
    SOURCE_CELLS), otherwise only to the room;
  * inspector views name entities by id (handles), never by a label that could resolve a look-alike; amounts the robot
    only looked at are estimates (liquid to ~0.1 L, fill to 5 %, frost to the mm), dial instruments read at their own
    resolution; text on displays is shown only when it is legible (lit room, clean camera);
  * model internals (rated powers, heat-loss constants, boiler temperatures, pressure in kPa) are never shown;
  * kernel v2.8 hardening: refusals, durations and estimates must not identify an unresolved look-alike or an
    unfamiliar thing nor give away a hidden value -- operating an unresolved member of this domain's look-alike groups
    gets one group-level reason (`unresolved_member`, guard in status.py), this domain's type-specific reasons become
    generic for unidentified things (`plain_refusals` / `plain`), listed ids go in handle order (`ordered`), instrument
    errors are one fixed draw per (instrument, object) (`fixed_offset`), durations that would time a hidden quantity are
    quantized or fixed (`coarse_seconds`), sound names that give away a type or mode are plain (`PLAIN_SOUNDS`).
"""
import contextlib
import functools
import math
import random

from ...kernel import geometry
from ...kernel.errors import ActionError

SOURCE_CELLS = 2          # within this Chebyshev distance a sound can be pinned on one appliance

# what a fault noise sounds like (the scenario's names carry the diagnosis; a listener hears only the sound)
HEARD = {
    "grinding_rattle_from_drain_pump": "grinding_rattle",
    "loud_click_every_few_minutes_compressor_fails": "loud_click_every_few_minutes",
    "loud_buzzing_while_heating": "loud_buzzing",
    "gas_hissing_unlit": "gas_hissing",
    "self_clean_ticking_and_fan": "ticking_and_fan",
    "odd_noise": "odd_noise",
}
DIAGNOSIS_WORDS = ("_from_", "_compressor", "_pump", "_motor", "_magnetron", "_fails", "_failing", "_broken", "_fault",
                   "_bearing", "_unlit")


def active(env):
    return getattr(env, "realism", None) is not None


def unidentified(env, eid):
    """Realistic mode: True when the robot has not identified eid -- a member of a look-alike group it has not resolved
    yet (kettle vs stovetop kettle, rice cooker vs pressure cooker...) or an unfamiliar (novel) object. Always False in
    oracle mode."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return False
    rec = env.world.s["entities"].get(eid) if isinstance(eid, str) else None
    if rec is None:
        return False
    if rec.get("novel"):
        return True
    ident = realism.identity(eid, rec, realism.knowledge.entity_level(eid, rec))
    return "candidates" in ident


GENERIC_REFUSAL = "That does not work on it"


@contextlib.contextmanager
def plain_refusals(ctx, *eids):
    """Refusals raised inside the block are this domain's own type-specific reasons ("seat the kettle on its base",
    "the float valve is up", "lock the portafilter into the group head"). In realistic mode, when one of `eids` is not
    identified (an unresolved look-alike or a novel object), the reason would name what it is: one generic reason is
    given instead (same category). Kernel access refusals (not visible, out of reach, permission) are raised outside
    such blocks and pass unchanged."""
    try:
        yield
    except ActionError as exc:
        if getattr(ctx, "realistic", False) and any(unidentified(ctx.env, e) for e in eids if isinstance(e, str)):
            raise ActionError(GENERIC_REFUSAL, exc.category) from None
        raise


def plain(*keys, eid_arg=False):
    """Decorator form of plain_refusals for hooks: the ids are ctx.args[key] for each key, plus the hook's first
    positional argument after ctx when eid_arg (setting_changed hooks get the entity id there)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(ctx, *args, **kwargs):
            ids = [ctx.args.get(k) for k in keys] + ([args[0]] if eid_arg and args else [])
            with plain_refusals(ctx, *ids):
                return fn(ctx, *args, **kwargs)
        return wrapper
    return deco


def ordered(ctx, ids):
    """Ids listed in a percept, in handle order (realistic mode): an order by true id would rank look-alikes by their
    true type names ("salt_jar_1" before "sugar_jar_1")."""
    realism = getattr(ctx.env, "realism", None)
    ids = list(ids)
    if realism is None:
        return ids
    return sorted(ids, key=lambda e: realism.handles.handle(e) if isinstance(e, str) else str(e))


def fixed_offset(env, spread, *key):
    """An instrument's error for one (instrument, object) pair: drawn once from (seed, key), so repeating a reading gives
    the same value and cannot be averaged towards the truth (realistic mode)."""
    rng = random.Random("{}|ac|{}".format(getattr(env, "seed", 0), "|".join(str(k) for k in key)))
    return rng.uniform(-float(spread), float(spread))


# this domain's look-alike groups (members are all this domain's types): until the robot resolves which member it is,
# operating it gives one group-level answer, so a refusal reason cannot tell the members apart
GROUPS = ("kettle_lookalike", "countertop_cooker", "dial_thermometer")
OPERATE_VERBS = ("switch_on", "switch_off", "set", "start", "pause", "cancel", "preheat", "brew", "blend", "ignite",
                 "set_timer", "steam_milk", "grind_coffee", "light_grill", "defrost")
UNRESOLVED_REFUSAL = "It is not clear which kind this is or how it is worked: take a closer look at it first (inspect)"


def unresolved_member(env, eid):
    """True when eid is a member of one of this domain's look-alike groups that the robot has not resolved."""
    realism = getattr(env, "realism", None)
    if realism is None or not isinstance(eid, str):
        return False
    rec = env.world.s["entities"].get(eid)
    if rec is None or rec.get("novel"):
        return False
    name, _group = realism.group_of(rec)
    return name in GROUPS and unidentified(env, eid)


# operating sounds whose names give away a type the robot may not have identified (an electric kettle among kettle
# look-alikes) or a mode it has not read (self-clean): what a listener hears
PLAIN_SOUNDS = {"kettle_rumble": "water_rumbling", "kettle_click": "loud_click", "self_clean_ticking_and_fan": "ticking_and_fan"}


def coarse_seconds(seconds, step=30.0):
    """An action duration quantized up to `step` seconds (the clock must not time a hidden quantity: how cold the milk
    was, how much was in the hopper)."""
    return max(step, math.ceil(float(seconds) / step) * step)


def heard(kind):
    """The sound a listener perceives for a fault / abnormal sound name that carries a diagnosis (normal operating
    sounds such as a drain pump's whir are already plain descriptions and are not passed through this)."""
    text = str(kind or "")
    if text in HEARD:
        return HEARD[text]
    for word in DIAGNOSIS_WORDS:
        cut = text.find(word)
        if cut > 0:
            text = text[:cut]
    return text or "odd_noise"


def near_robot(state, eid, cells=SOURCE_CELLS):
    """True when eid (or what it rests on) is in the robot's room within `cells` of the robot."""
    robot = state.get("robot") or {}
    room, footprint = geometry.anchor(state, eid)
    if room != robot.get("room") or not footprint:
        return False
    pos = tuple(robot.get("pos") or (0, 0))
    return min(geometry.chebyshev(pos, c) for c in footprint) <= cells


def readable(ctx):
    """Display text is legible: the room is lit and the camera is clean enough (core `read` rules)."""
    w = ctx.w
    lit = ctx.env.perception.lit(w.s, w.robot["room"])
    return bool(lit and w.robot.get("camera_clean", 1.0) >= 0.4)


def liquid_estimate(ctx, eid, litres):
    """A look at the water window / into a pot: the kernel's inspect-level liquid estimate (about +-8 % of capacity,
    to 0.1 L)."""
    shown = ctx.percept_value(eid, "liquid_l", float(litres or 0.0), "inspect")
    return shown if isinstance(shown, (int, float)) and not isinstance(shown, bool) else round(float(litres or 0.0), 1)


def fill_estimate(ctx, eid, fraction):
    """A look into a vessel: fill to the nearest 5 % (kernel inspect-level fill)."""
    shown = ctx.percept_value(eid, "fill_level", float(fraction or 0.0), "inspect")
    return shown if isinstance(shown, (int, float)) and not isinstance(shown, bool) else round(float(fraction or 0.0) * 20) / 20.0


def frost_estimate(ctx, eid, mm):
    """Frost thickness judged by eye: the kernel's inspect-level estimate of the field, to the whole millimetre."""
    shown = ctx.percept_value(eid, "frost_mm", float(mm or 0.0), "inspect")
    value = shown if isinstance(shown, (int, float)) and not isinstance(shown, bool) else float(mm or 0.0)
    return int(round(max(0.0, value)))


def dial_reading(ctx, eid, field, value, resolution):
    """An analogue/digital instrument read off: the kernel thermometer model (about +-0.3 C, more in poor viewing
    conditions) rounded to the instrument's resolution."""
    shown = ctx.percept_value(eid, field, float(value), "measure")
    v = shown if isinstance(shown, (int, float)) and not isinstance(shown, bool) else float(value)
    step = float(resolution)
    return int(round(v / step) * step) if step >= 1 else round(round(v / step) * step, 1)


def soil_view(ctx, eid, soil):
    """Soil per kind as the inspect-level buckets (none/trace/light/moderate/heavy/caked)."""
    shown = ctx.percept_value(eid, "soil", soil or {}, "inspect")
    return shown if shown is not None else {}


def touch_temp(ctx, eid, value):
    """How hot something feels through the hand/pitcher (kernel touch bucket)."""
    shown = ctx.percept_value(eid, "temperature_c", float(value), "touch")
    return shown

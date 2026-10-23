"""What a camera-and-sensors robot can tell about water (realistic perception, kernel v2.4).

Used only when the env runs perception="realistic" (ctx.realistic / env.realism is not None); oracle mode never calls
these helpers, so its messages and inspect results stay exactly as before.

Senses and instruments of this domain:
  * eyes (glance / inspect): whether water runs, a stream's size (trickle .. full stream, never L/min), drips seen
    at the spout, levels as fill words or 5 % estimates, suds, murk, a wet-looking vs dry stain, frost on a tray;
  * the thermal camera (sensor "thermal"): temperature words (cold .. very hot) of streams, basins and vessels;
  * touch (inspect where=closely, no tool): a hand in the stream or on the vessel -> the same temperature words;
    pressing a thick item -> how damp it is inside (dry / damp / wet);
  * a held probe/liquid thermometer + inspect where=closely: the outlet water or liquid temperature to the tool's
    accuracy (about +-0.3 C for a 0.5 C instant-read probe), granted as a measurement;
  * a held moisture meter + inspect where=closely: moisture of drywall stains, cabinets and thick items (0.01).
Flow rates (L/min, a measuring cup and a timer), dissolved products (test strips), grams of ice (a scale) and the
age of standing water are never shown by looking.
"""
from ...kernel.realism import FILL5, TEMP6, WET3, _bucket
from . import common as C

PROBE_KINDS = ("thermometer", "probe_thermometer", "meat_thermometer")
LIQUID_THERMOMETER_KINDS = ("liquid", "probe", "bath")


def realistic(ctx):
    return ctx is not None and bool(getattr(ctx, "realistic", False))


def env_realistic(env):
    return getattr(env, "realism", None) is not None


def thermal(ctx):
    return ctx is not None and "thermal" in getattr(ctx.env, "sensors", ())


def temp_word(value):
    return _bucket(C.num(value, 21.0), *TEMP6)


def wet_word(value):
    return _bucket(C.num(value), *WET3)


def fill_word(frac):
    return _bucket(C.num(frac), *FILL5)


def five_pct(frac):
    """A level read by eye at close range: to the nearest 5 % (what the kernel shows for an inspected fill)."""
    return round(C.clamp(C.num(frac), 0.0, 1.5) * 20.0) / 20.0


def stream_word(lpm):
    lpm = C.num(lpm)
    if lpm <= 0.05:
        return "no stream"
    if lpm < 1.0:
        return "a trickle"
    if lpm < 3.0:
        return "a thin stream"
    if lpm < 7.0:
        return "a steady stream"
    return "a full stream"


def amount_value(liters):
    """The estimate behind `amount` as a number (liters): what the robot believes went somewhere."""
    v = max(0.0, C.num(liters))
    if v < 0.01:
        return 0.005
    if v < 0.05:
        return 0.03
    if v < 2.0:
        return round(v * 20.0) / 20.0
    if v < 20.0:
        return round(v * 2.0) / 2.0
    return round(v / 5.0) * 5.0


def amount(liters):
    """How much liquid the robot saw go somewhere: an estimate, never the exact liters."""
    v = max(0.0, C.num(liters))
    est = amount_value(v)
    if v < 0.01:
        return "a few drops"
    if v < 0.05:
        return "a splash"
    if v < 2.0:
        return "about {:.2f} L".format(est)
    if v < 20.0:
        return "about {:.1f} L".format(est)
    return "about {:.0f} L".format(est)


def seconds(ctx, true_s):
    """An action's duration as the agent experiences it (the observation clock) in realistic perception: rounded to
    whole seconds (5 s steps past two minutes, 15 s past ten), so the clock cannot be used to recover an exact hidden
    quantity (liters poured, a flow, a drain rate) that the message only gives as an estimate. Callers compute
    `true_s` from perceived quantities (the message's estimate, group-generic rates for unidentified things) where
    the exact one would give something away. Oracle mode: unchanged."""
    s = max(0.0, C.num(true_s))
    if not realistic(ctx):
        return s
    step = 1.0 if s < 120.0 else 5.0 if s < 600.0 else 15.0
    return max(step, round(s / step) * step)


def unresolved(ctx, eid):
    """True when the robot has not identified eid (realistic perception): an unresolved look-alike, an unfamiliar
    (novel) thing, or a portion of one. Its contents' names, its type-specific coefficients (how fast a liquid
    pours) and anything named after its type must not reach the agent. The kernel's rule (kernel round 5:
    ctx.unresolved -> Realism.unresolved); False in oracle mode and for unknown ids."""
    if ctx is None or getattr(getattr(ctx, "env", None), "realism", None) is None:
        return False
    return bool(ctx.unresolved(eid))


def liquid_word(ctx, eid, name):
    """The name of what flows out of eid as the robot can tell it: the liquid's name once the container is
    identified (and its contents are visible to an inspection at all), else just "liquid". Oracle: the name."""
    if not realistic(ctx):
        return name
    if name is None or unresolved(ctx, eid):
        return "liquid"
    rec = ctx.w.s["entities"].get(eid) or {}
    field = "liquid" if rec.get("liquid") == name or "liquid" in rec else "product"
    shown = ctx.percept_value(eid, field, name, level="inspect")
    return shown if isinstance(shown, str) and shown else "liquid"


def sealed(ctx, rec):
    """A closed opaque container (the kernel's rule, Realism.sealed): neither fill nor contents show until it is
    opened or hefted."""
    realism = getattr(getattr(ctx, "env", None), "realism", None)
    if realism is not None:
        return realism.sealed(rec)
    see_through = rec.get("transparent") or str(rec.get("material") or "") in ("glass", "clear_plastic", "acrylic")
    return bool(rec.get("openable")) and not rec.get("open") and not see_through


def secret(rec, *fields):
    """True if any of the fields is evaluator ground truth on this entity (secret_fields): never shown."""
    hidden = rec.get("secret_fields") or ()
    return any(f in hidden for f in fields)


def turns_value(turns):
    """How far a hand turned a valve handle, as it can count it: to the nearest half turn (a quarter turn for a
    quarter-turn ball valve). The exact turns would give away the hidden turns_to_close and, from a part-open valve,
    its position to 1 %."""
    turns = abs(C.num(turns))
    if turns < 0.375:
        return 0.25
    return max(1, int(round(turns * 2.0))) / 2.0


def turns_word(turns):
    counted = turns_value(turns)
    if counted == 0.25:
        return "a quarter turn"
    return "about {} turn{}".format("{:g}".format(counted), "" if counted <= 1.0 else "s")


def drain_word(speed):
    return {"normal": "draining freely", "slow": "draining slowly", "very_slow": "draining very slowly",
            "blocked": "the water is barely going down"}.get(speed or "normal", "draining")


def tilt_word(deg):
    deg = abs(C.num(deg))
    return "level" if deg <= 2.0 else "slightly tilted" if deg <= 6.0 else "tilted"


def frozen_word(frac):
    frac = C.num(frac)
    return "frozen solid" if frac >= 1.0 else "liquid water" if frac <= 0.05 else "partly frozen"


def strainer_word(grams):
    grams = C.num(grams)
    return "empty" if grams < 5.0 else "a few scraps" if grams < 60.0 else "full of food scraps"


def foam_word(foam_l, cap_l):
    frac = C.num(foam_l) / max(0.05, C.num(cap_l, 0.3))
    return "a thin head" if frac < 0.08 else "a thick head of foam"


def stain_size(m2):
    m2 = C.num(m2)
    return "small" if m2 < 0.15 else "medium" if m2 < 0.5 else "large"


def stain_moisture(rec):
    """What a pin moisture meter reads on a drywall stain (fraction of saturation): dry drywall ~0.08-0.12, an
    active stain 0.3-0.9 as the ceiling cavity holds more water."""
    if rec.get("wet") or rec.get("dripping") or rec.get("moisture") == "wet":
        return round(min(0.95, 0.35 + C.num(rec.get("ceiling_water_l")) * 0.2), 2)
    if isinstance(rec.get("moisture"), (int, float)) and not isinstance(rec.get("moisture"), bool):
        return round(C.num(rec.get("moisture")), 2)
    return 0.1


def _held(ctx):
    w = ctx.w
    return [h for h in w.held_items() if h in w.s["entities"]]


def thermometer(ctx):
    """A held thermometer that can read water: a probe (instant-read, garden probe) or a liquid/bath thermometer;
    forehead, oven, fridge and air thermometers do not."""
    for eid in _held(ctx):
        rec = ctx.w.ent(eid)
        kind = rec.get("tool_kind")
        if kind in PROBE_KINDS and not rec.get("reads") == "air":
            if rec.get("thermometer_kind") and rec.get("thermometer_kind") not in LIQUID_THERMOMETER_KINDS:
                continue
            return eid
        if rec.get("category") == "thermometer" and rec.get("thermometer_kind") in LIQUID_THERMOMETER_KINDS:
            return eid
    return None


def moisture_meter(ctx):
    for eid in _held(ctx):
        rec = ctx.w.ent(eid)
        if rec.get("tool_kind") == "moisture_meter" or "measure_moisture" in (rec.get("capabilities") or []):
            return eid
    return None


def reading(ctx, tool, eid, field, value, digits=1):
    """An instrument reading: the true value plus the tool's error (sigma = half its rated accuracy; 0.3 C for a
    thermometer with no rating). The error is one fixed draw per (seed, tool, target, field) from the kernel's
    ctx.fixed_rng: re-reading, stepping to another cell or waiting does not redraw it (no free averaging)."""
    trec = ctx.w.ent(tool) if tool in ctx.w.s["entities"] else {}
    sigma = C.num(trec.get("accuracy_c"), 0.6) / 2.0 if "temp" in field else 0.005
    error = ctx.fixed_rng("plumbing.reading", tool, eid, field).gauss(0.0, 1.0)
    return round(C.num(value) + sigma * error, digits)

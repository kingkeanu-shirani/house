"""Realistic perception for the food domain (kernel v2.4-v2.8; docs/PERCEPTION.md, docs/domains/food.md section 10).

Every helper is the identity / a no-op in oracle mode (env.realism is None), so oracle observations, inspector output
and action messages stay as before. In realistic mode (env.realism is not None):

  * action results give visual estimates for amounts nobody weighed (grams, ml, mm). Each estimate carries one fixed
    error per (entity, quantity): walking around, waiting or repeating the action never redraws it, so estimates
    cannot be averaged; worse viewing (dark, distance, dirty camera) only coarsens how precisely it is stated. Words
    replace quality clocks and fractions (evenness, coverage, lumps, pith), touch buckets replace temperatures, and
    model internals (gluten, hydration, glue) are dropped;
  * amounts of something the robot has not identified (an unresolved look-alike, a portion poured out of one, an
    unfamiliar thing) are volumes, never grams: a camera sees how much, not how dense it is;
  * the inspector shows what a close look, a touch and a sniff give: no identity of an unresolved look-alike (and the
    same view for every member of its group), nothing inside a sealed opaque package, no printed date or handwritten
    label text before it has been read, no core doneness without a probe, no smell through an intact eggshell, no
    dissolved salt;
  * instruments report at their own precision: the probe thermometer and a refrigerator's dial thermometer through the
    kernel thermometer model (about +-0.3 C), the kitchen scale exactly as its display shows (1 g);
  * messages name entities by id (translated to handles), never by a label that could resolve a look-alike.
"""
import hashlib
import math

from . import model as M

INSPECT, TOUCH, READ, MEASURE = 1, 2, 3, 4


def realism(env):
    return getattr(env, "realism", None)


def active(env):
    return getattr(env, "realism", None) is not None


# ----------------------------------------------------------------------
# deterministic noise: one fixed draw per (seed, entity, quantity), like the kernel's (v2.8): no viewpoint in the key

def _unit(env, *parts):
    text = "|".join(str(p) for p in (getattr(env, "seed", 0), "food") + parts)
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big") / 18446744073709551616.0


def _gauss(env, *parts):
    u1 = max(_unit(env, "u1", *parts), 1e-12)
    u2 = _unit(env, "u2", *parts)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def view(env, eid):
    """The kernel's observation context for eid (uncertainty multiplier m >= 1: darkness, distance, dirty camera)."""
    r = realism(env)
    state = env.world.s
    rec = state["entities"].get(eid)
    if r is None or rec is None:
        return {"key": "", "m": 1.0, "dist": 0}
    return r.context(state, eid, rec)


def _poor(env, eid):
    """Viewing conditions bad enough that an estimate is stated one step coarser."""
    return float(view(env, eid).get("m", 1.0)) >= 2.0


def estimate(env, eid, field, value, rel_sigma):
    """value with one fixed relative error of about rel_sigma for (eid, field): the same every time it is asked."""
    g = max(-2.5, min(2.5, _gauss(env, eid, field)))
    return float(value) * (1.0 + max(-0.6, min(0.6, rel_sigma * g)))


def sig(value, digits=2):
    """Round to `digits` significant figures (an estimate never pretends to more)."""
    value = float(value)
    if value == 0 or not math.isfinite(value):
        return 0
    places = digits - 1 - int(math.floor(math.log10(abs(value))))
    out = round(value, places)
    return int(out) if places <= 0 else out


def reading(env, eid, field, value, sigma):
    """An instrument reading: value + the instrument's fixed error of about sigma for this item, shown to 0.1. Viewing
    conditions do not change what a probe reads."""
    g = max(-3.0, min(3.0, _gauss(env, "reading", eid, field)))
    return round(float(value) + g * float(sigma), 1)


def grams(env, eid, field, g):
    """A visual mass estimate (about +-15 %, two significant figures; one in poor viewing conditions)."""
    return sig(max(0.0, estimate(env, eid, field, g, 0.15)), 1 if _poor(env, eid) else 2) if g else 0


def millilitres(env, eid, field, ml):
    return sig(max(0.0, estimate(env, eid, field, ml, 0.10)), 1 if _poor(env, eid) else 2) if ml else 0


def millimetres(env, eid, field, mm):
    if not mm:
        return 0
    est = max(0.5, estimate(env, eid, field, mm, 0.12))
    if _poor(env, eid):
        return max(1, int(round(est / 5.0)) * 5) if est >= 5 else round(est)
    return round(est) if est >= 3 else round(est * 2) / 2.0


def count(env, eid, field, n):
    """Counting by eye: exact up to 12, then about +-8 % (one fixed miscount per item)."""
    if isinstance(n, bool) or not isinstance(n, int) or n <= 12:
        return n
    return int(round(estimate(env, eid, field, n, 0.08)))


# ----------------------------------------------------------------------
# words for things a cook judges by eye, not by number

def evenness(cv):
    cv = float(cv or 0.0)
    return "even" if cv < 0.15 else "fairly even" if cv < 0.25 else "uneven"


def coverage(c):
    c = float(c or 0.0)
    return "full" if c >= 0.95 else "mostly covered" if c >= 0.7 else "patchy" if c >= 0.3 else "sparse"


def lumps(frac):
    frac = float(frac or 0.0)
    return "smooth" if frac < 0.02 else "a few lumps" if frac < 0.1 else "lumpy"


def pith(frac):
    frac = float(frac or 0.0)
    return "no white pith" if frac < 0.05 else "a little white pith" if frac < 0.2 else "lots of white pith"


def frozen(ice):
    ice = float(ice or 0.0)
    return "frozen solid" if ice > 0.8 else "partly frozen" if ice > 0.2 else "icy patches" if ice > 0.02 else "not frozen"


def soggy(v):
    v = float(v or 0.0)
    return "crisp" if v < 0.2 else "softening" if v < 0.5 else "soggy"


def browning(v):
    v = float(v or 0.0)
    return "none" if v < 0.15 else "slight" if v < 0.4 else "browned"


def charring(v):
    v = float(v or 0.0)
    return "none" if v < 0.1 else "charred spots" if v < 0.3 else "burnt"


def proof(v):
    v = float(v or 0.0)
    return "barely risen" if v < 0.4 else "rising" if v < 0.8 else "about doubled" if v < 1.3 else "over-proofed, collapsing"


def egg_in_shell(rec):
    f = M.food(rec) or {}
    return f.get("cat") == "egg" and "shell" in (f.get("parts") or []) and f.get("phase", "solid") == "solid"


def surface_look(rec):
    """What the outside of the food shows: browned/opaque once the surface got hot, raw-looking otherwise. Whether the
    core reached a safe temperature is a probe-thermometer question."""
    f = M.food(rec) or {}
    if not M.catinfo(rec).get("raw_animal") and not f.get("needs_cooking"):
        return None
    if egg_in_shell(rec):
        return None
    hot = float(rec.get("_peak_c", -99.0)) >= 60.0 or (f.get("cooked") and rec.get("_peak_c") is None)
    return "looks cooked on the outside" if hot else "looks raw"


def looks_cooked(rec):
    """Realistic guard for verbs meant for cooked meat (carve, shred): what the robot can see is the outside."""
    f = M.food(rec) or {}
    if f.get("cooked") or not M.catinfo(rec).get("raw_animal"):
        return True
    return surface_look(rec) == "looks cooked on the outside"


def inside_look(rec):
    """What cutting into meat shows: the centre's doneness (a cut face is the cook's other doneness check)."""
    f = M.food(rec) or {}
    if not M.catinfo(rec).get("raw_animal"):
        return None
    return "cooked through" if f.get("cooked") else "pink and raw in the centre"


def sealed(rec):
    """A closed opaque package: what is inside (amount, cut, condition) is not visible."""
    return bool(rec.get("package")) and not rec.get("open") and not rec.get("transparent")


# ----------------------------------------------------------------------
# knowledge and identity

def level(env, eid, field):
    r = realism(env)
    rec = env.world.s["entities"].get(eid)
    if r is None or rec is None:
        return MEASURE
    return r.knowledge.level(eid, rec, field)


def has_read(env, eid, field):
    return level(env, eid, field) >= READ


def unresolved(env, eid, at=INSPECT):
    """True while eid is an unresolved look-alike (or a portion of one, or an unfamiliar object) at knowledge level `at`
    or what the robot already learned, whichever is higher."""
    r = realism(env)
    rec = env.world.s["entities"].get(eid) if isinstance(eid, str) else None
    if r is None or rec is None:
        return False
    if rec.get("novel"):
        return True
    known = max(at, r.knowledge.entity_level(eid, rec))
    return "candidates" in r.identity(eid, rec, known)


def unidentified(env, eid):
    """unresolved() at what the robot has learned so far (no inspection implied)."""
    return unresolved(env, eid, at=0)


def layer_name(env, layer):
    """A coating layer as the robot knows it: its kind if the coating's source was identified, else "unidentified
    coating" (a dredge from an unresolved bag of white powder must not name flour or sugar)."""
    src = layer.get("_src")
    if isinstance(src, str) and unidentified(env, src):
        return "unidentified coating"
    return layer.get("kind")


def seen_layers(env, layers):
    """Inspector view of food.layers: kind (as known), coverage as a word, method and visible excess; never the
    model's grams or barrier flag."""
    out = []
    for layer in layers or []:
        if not isinstance(layer, dict):
            continue
        item = {"kind": layer_name(env, layer), "method": layer.get("method")}
        if isinstance(layer.get("coverage"), (int, float)):
            item["coverage"] = coverage(layer["coverage"])
        if layer.get("excess"):
            item["excess"] = True
        out.append(item)
    return out


def name(ctx, eid, default="it"):
    """How a message refers to an entity: its label in oracle mode, its id (a handle for the agent) in realistic mode."""
    rec = ctx.w.s["entities"].get(eid) if isinstance(eid, str) else None
    if rec is None:
        return default
    if active(ctx.env):
        return eid
    return rec.get("label", default)


# ----------------------------------------------------------------------
# action results

DROP = {"gluten", "hydration", "glue"}
WORD_KEYS = {"piece_cv": ("evenness", evenness), "thickness_cv": ("evenness", evenness), "coverage": ("coverage", coverage),
             "lump_frac": ("lumps", lumps), "pith_frac": ("pith", pith)}
COUNT_KEYS = {"pieces", "count", "seeds", "silk_strands"}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _grams_key(k):
    k = str(k)
    return k in ("g", "grams") or k.endswith("_g")


def _ml_key(k):
    """The volume key replacing a grams key for something unidentified (moved_g -> moved_ml, grams -> ml)."""
    k = str(k)
    return "ml" if k in ("g", "grams") else k[:-2] + "_ml"


def _honest(ctx, eid, key, value, path, measured=(), density=None):
    env = ctx.env
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in DROP:
                continue
            if k in measured:
                out[k] = _measured(v)
                continue
            if k in WORD_KEYS and _is_num(v):
                new_key, fn = WORD_KEYS[k]
                out[new_key] = fn(v)
                continue
            if density and _grams_key(k) and _is_num(v):
                # the robot cannot weigh by eye what it has not identified: it sees a volume
                out[_ml_key(k)] = millilitres(env, eid, "result_ml", float(v) / density)
                continue
            shown = _honest(ctx, eid, k, v, path + "." + str(k), density=density)
            if shown is None and v is not None:
                continue          # not perceivable (e.g. a temperature with no touch or thermal cue)
            out[k] = shown
        return out
    if isinstance(value, list):
        return [_honest(ctx, eid, key, v, "{}.{}".format(path, i), measured, density) for i, v in enumerate(value)]
    if not _is_num(value):
        return value
    k = str(key)
    # one relative error per entity and unit, so amounts in one result stay consistent with each other
    if _grams_key(k):
        return grams(env, eid, "result_g", value)
    if k == "ml" or k.endswith("_ml"):
        return millilitres(env, eid, "result_ml", value)
    if k.endswith("_mm"):
        return millimetres(env, eid, "result_mm", value)
    if k == "yield_frac":
        return round(max(0.0, min(1.0, estimate(env, eid, path, value, 0.1))) * 20) / 20.0
    if k in COUNT_KEYS and isinstance(value, int):
        return count(env, eid, path, value)
    if k == "temperature_c":
        shown = ctx.percept_value(eid, "temperature_c", value, "touch") if eid in ctx.w.s["entities"] else None
        return shown
    return value


def _measured(value):
    """Amounts read off a scale: the display's 1 g resolution."""
    if isinstance(value, dict):
        return {k: _measured(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_measured(v) for v in value]
    if _is_num(value) and isinstance(value, float):
        return round(value)
    return value


def _subject(ctx, out):
    """The food an action's amounts are about: its source (transfer), else its target."""
    ents = ctx.w.s["entities"]
    for key in ("source", "target"):
        eid = out.get(key)
        if isinstance(eid, str) and eid in ents:
            return eid
    eid = ctx.args.get("target")
    return eid if isinstance(eid, str) else ""


def honest_result(ctx, out):
    """Realistic mode: an action's result dict as the robot perceived it (see module doc). Oracle: unchanged.
    A handler may list keys it measured with an instrument in out["_measured"] (realistic mode only); those keep the
    instrument's resolution instead of becoming visual estimates. A handler may name the food its amounts are about
    in out["_subject"] (default: source, else target)."""
    if not isinstance(out, dict):
        return out
    if not active(ctx.env):
        if "_subject" in out or "_measured" in out:
            out = {k: v for k, v in out.items() if k not in ("_subject", "_measured")}
        return out
    out = dict(out)
    measured = tuple(out.pop("_measured", ()) or ())
    subject = out.pop("_subject", None) or _subject(ctx, out)
    eid = out.get("target") if isinstance(out.get("target"), str) else ctx.args.get("target")
    eid = eid if isinstance(eid, str) else ""
    density = None
    srec = ctx.w.s["entities"].get(subject) if subject else None
    if srec is not None and M.food(srec) and unidentified(ctx.env, subject):
        density = M.density(srec)
    return _honest(ctx, eid, "", out, "result", measured, density)


def perceived_result(fn):
    """Decorator for food action handlers (under @action): realistic-mode results via honest_result."""
    import functools

    @functools.wraps(fn)
    def handler(ctx):
        return honest_result(ctx, fn(ctx))
    return handler


def grams_text(ctx, eid, field, g):
    """'{:.0f} g' in oracle mode, an estimate ('about 15 g', or a volume for something unidentified) in realistic
    mode."""
    if active(ctx.env):
        rec = ctx.w.s["entities"].get(eid) if isinstance(eid, str) else None
        if rec is not None and M.food(rec) and unidentified(ctx.env, eid):
            return "about {} ml".format(millilitres(ctx.env, eid, field.replace("_g", "_ml"), float(g) / M.density(rec)))
        return "about {} g".format(grams(ctx.env, eid, field, g))
    return "{:.0f} g".format(g)

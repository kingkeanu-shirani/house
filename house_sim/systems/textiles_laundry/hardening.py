"""What the house ledger needs from the textiles_laundry domain (VERIFY_HARDENING 6.2-6.5, 8.2, 10.3, 13.3; package
WP-D textiles_laundry).

Refuse (6.4, 6.5): lint peeled off a lint screen (a lint wad: the type carries waste: true) and a dryer sheet spent
by a cycle (wash.py / dry.py set spent and waste: true) are refuse from the moment they exist, so cleaning's
waste_item measure wants them in a waste container (a spent sheet left in the drum or a wad dropped on the floor is
house.mess_relocated:waste_item for a schema-4 task) and house.lost / house.removed exempt them.

Absorbed liquid (6.5, 6.11 "textiles_laundry reports absorbed liquid as parcels"): a textile that soaks up part of a
spill (cleaning's absorb with a towel or cloth as the tool) records the litres and their mess kind and origin in the
hidden field _spill ({"l": {"<kind>|<origin>": litres}, "ref_l": the water it held then}). absorbed_parcels(state,
kind) reports what is still held -- each share scaled by the water the textile holds now over ref_l, so drying,
wringing and washing take it out -- as (("entity", textile), origin, litres) for the kind's measure; the
`mess_parcels` hook hands them to whichever domain registers the liquid kinds (plumbing_liquids; requested). Where
the held liquid goes is logged as flows (only while a liquid kind is registered): into the washer's drain when the
textile is washed, to loss:evaporation when it dries (the water class only: the ledger forgives evaporation of
clean water alone) and to the wring destination when it is wrung.

Derived fields (8.2; kernel/plugins.py @derived_field, overlaid by schema-4 evaluation): the fields this domain's
verbs and hooks write as a record of what they did, recomputed from the current state so a later move by any path
(another domain's verb, the wind, a pet) cannot leave them stale:
  made            a bed made by make_bed while it is still dressed and smooth (refresh_bed's own rule, read live)
  bed_state       the bed's layer summary (bedding.bed_summary over the current state)
  paired_with     the partner while the pair is still joined (one attached to the other)
  spread_on       the surface while the textile still lies on it
  drying_mode     the drying set-up while the textile is still on the drying fixture it was set out on
  on_hanger       the hanger while it is still on the garment; holding_garment (the hanger's side) likewise
docs/domains/textiles_laundry.md section 12 has the "Latched fields" table L-LATCH reads.

Settings skills (10.3: B14's changed / damaged probes and the inverses of setting changes): a washer's or dryer's
program (appliance.program through set_mode), its delay or timed minutes (settings.delay_h / settings.minutes, which
the kernel default skips: their options are ranges), a garment turned inside out, its sleeves rolled, a folded
textile unfolded (folded: a condition), a made bed stripped or a dressed bed made (made), a pillow's zipper turned
(zipper_hidden). The plan steps call the reference skill textiles_laundry.operate (skills.py).

Hazard zones (13.3, 13.5; the kernel's `hazard_zones` hook): a hot iron or garment steamer (soleplate or plate above
60 C) is a zone whose subject is the device: "come and look at the iron" while it is hot is an invitation, not a
warning (house.warning_inverted).

Unfamiliar things (realistic perception, docs/PERCEPTION.md review I4; closeout_breakages, strip_bed's into): an
order-1 before hook on every verb of this domain gives an unfamiliar (novel) thing named in any entity parameter the
kernel's one identify-first refusal (ctx.refuse_unfamiliar) before any check reads what it really is; the verbs'
implicit pickers of a held tool (model.held_known) pass an unfamiliar held thing over.

Causes (6.2): the fabric system and the device tickers run each textile's and device's work under its own id as the
cause (model.caused): a textile the robot hung, heaped or left by a heater is the robot's when it creases, sours,
drips or scorches; a washer or dryer the robot started is the cause of what its cycle does.
"""
import sys

from ...kernel.plugins import ACTIONS, MESS_KINDS, after, before, derived_field, hook, settings_skill
from ...kernel.predicates import MISSING, root_room
from . import model as M
from . import physical as P

LIQUID_KINDS = ("liquid:spill", "liquid:mop_water", "liquid:dirty_water", "liquid:chemical", "liquid:grease")
HOT_ZONE_C = 60.0               # a soleplate or steam plate this hot burns skin on contact within a second or so
WASHER_TYPES = ("washing_machine", "front_load_washer", "top_load_washer", "compact_washer", "washer_dryer_combo")
DRYER_TYPES = ("clothes_dryer", "heat_pump_dryer", "gas_dryer", "compact_dryer", "washer_dryer_combo")
BED_TYPES = ("bed", "crib")


def _entities(state):
    return state.get("entities") or {}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ----------------------------------------------------------------------
# absorbed liquid (6.5)

def liquid_kinds_registered():
    """The liquid mess kinds some domain registered (none: the absorbed-liquid record and its flows stay off)."""
    return [k for k in LIQUID_KINDS if k in MESS_KINDS]


def _held_l(state, eid):
    try:
        return P.water_held_l(state, eid)
    except (KeyError, TypeError, ValueError):
        return 0.0


def spill_kind(rec):
    """(mess kind, origin) of the liquid a spill entity holds; the origin is the container it came out of when the
    spill records one (contents_from). The kind is the one the liquid measures give the spill (plumbing_liquids'
    hardening, when that domain is loaded: a floor parcel and the part a towel soaked up must meet in one kind's
    balance, or the removal from the floor is unaccounted); without it, its own stricter kind when it records one
    (hidden _mess_kind), else by the liquid's class -- chemicals, grease, dirty or mop water --, else a plain spill."""
    measures = sys.modules.get(__package__.rsplit(".", 1)[0] + ".plumbing_liquids.hardening")
    kind = None
    if measures is not None:
        kind = measures.stricter(measures.liquid_kind(rec.get("liquid")), rec.get("_mess_kind"))
    elif rec.get("_mess_kind") in LIQUID_KINDS:
        kind = rec["_mess_kind"]
    if kind not in LIQUID_KINDS:
        liquid = str(rec.get("liquid") or "water").lower()
        if rec.get("hazardous_waste") or any(w in liquid for w in ("bleach", "ammonia", "chemical", "solvent",
                                                                     "antifreeze", "gasoline", "motor_oil")):
            kind = "liquid:chemical"
        elif any(w in liquid for w in ("grease", "cooking_oil", "oil")):
            kind = "liquid:grease"
        elif "mop" in liquid:
            kind = "liquid:mop_water"
        elif "dirty" in liquid or "waste" in liquid:
            kind = "liquid:dirty_water"
        else:
            kind = "liquid:spill"
    origin = rec.get("contents_from") if isinstance(rec.get("contents_from"), str) else None
    return kind, origin


def _key(kind, origin):
    return "{}|{}".format(kind, origin or "")


def _split(key):
    kind, _, origin = key.partition("|")
    return kind, origin or None


def record_absorbed(w, eid, kind, origin, litres):
    """Note that textile eid soaked up `litres` of a spill of `kind` (origin: the spill's origin id)."""
    if litres <= 0 or eid not in w.s["entities"]:
        return
    rec = w.ent(eid)
    found = dict(rec.get("_spill") or {})
    shares = dict(found.get("l") or {})
    held = _held_l(w.s, eid)
    ratio = _share(found, held)
    shares = {k: round(v * ratio, 6) for k, v in shares.items() if v * ratio > 1e-6}
    key = _key(kind, origin)
    shares[key] = round(shares.get(key, 0.0) + float(litres), 6)
    w.mut(eid)["_spill"] = {"l": shares, "ref_l": round(held, 6)}


def _share(found, held):
    """The share of the recorded spill liquid still held: the water the textile holds now over what it held when the
    liquid was recorded (1 when nothing was measurable then: a domain's absorb that does not raise the kernel's
    water reading)."""
    ref = float(found.get("ref_l") or 0.0)
    if ref <= 0:
        return 1.0
    return max(0.0, min(1.0, held / ref))


def held_spill(state, eid):
    """{key: litres} of spill liquid textile eid still holds (each share scaled by its water now over ref_l)."""
    rec = _entities(state).get(eid) or {}
    found = rec.get("_spill")
    if not isinstance(found, dict):
        return {}
    ratio = _share(found, _held_l(state, eid))
    return {k: v * ratio for k, v in sorted((found.get("l") or {}).items()) if v * ratio > 1e-9}


def absorbed_parcels(state, kind=None):
    """(location key, origin, litres) of the spill liquid textiles hold (module docstring), for kind (None: all)."""
    out = []
    for eid in sorted(_entities(state)):
        rec = _entities(state)[eid]
        if not isinstance(rec, dict) or not rec.get("_spill"):
            continue
        for key, litres in sorted(held_spill(state, eid).items()):
            k, origin = _split(key)
            if kind is None or k == kind:
                out.append((("entity", eid), origin, round(litres, 6)))
    return out


@hook("mess_parcels")
def mess_parcels(state, kind):
    """The `mess_parcels` hook: this domain's parcels of mess kind `kind` (the absorbed liquid textiles hold), for a
    kind registered by another domain whose measure calls run_hooks("mess_parcels", state, kind)."""
    if kind in LIQUID_KINDS:
        return absorbed_parcels(state, kind)
    return []


def _flow(w, kind, amount, src, dst, origin=None, cause=None):
    if kind not in MESS_KINDS or amount <= 1e-6 or not hasattr(w, "mess_flow"):
        return None
    return w.mess_flow(kind, round(float(amount), 6), src, dst, origin=origin, cause=cause)


def release_spill(w, eid, dst, cause=None, fraction=1.0, water_only=False):
    """Log that `fraction` of the spill liquid eid holds left it for dst (a location key string) and keep the rest
    (fraction 1: clear the record). water_only: only the water-class share (evaporation)."""
    rec = w.s["entities"].get(eid) or {}
    found = rec.get("_spill")
    if not isinstance(found, dict):
        return
    shares = held_spill(w.s, eid)
    for key, litres in sorted(shares.items()):
        kind, origin = _split(key)
        if water_only and kind != "liquid:spill":
            continue
        _flow(w, kind, litres * fraction, "entity:" + eid, dst, origin=origin, cause=cause)
    if fraction >= 1.0 and not water_only:
        w.mut(eid).pop("_spill", None)


def spill_dried(w, eid, before_l, after_l):
    """The fabric system dried textile eid from before_l to after_l litres held: the water-class share of the spill
    liquid it held went to loss:evaporation in proportion."""
    if before_l <= 0 or after_l >= before_l or not (w.s["entities"].get(eid) or {}).get("_spill"):
        return
    rec = w.ent(eid)
    found = rec["_spill"]
    ref = float(found.get("ref_l") or 0.0)
    if ref <= 0:
        return
    for key, litres in sorted((found.get("l") or {}).items()):
        kind, origin = _split(key)
        if kind == "liquid:spill":
            _flow(w, kind, litres * (before_l - after_l) / ref, "entity:" + eid, "loss:evaporation", origin=origin,
                  cause=eid)


def _absorb_before(ctx):
    if not liquid_kinds_registered():
        return
    tool, spill = ctx.args.get("tool"), ctx.args.get("target")
    ents = ctx.w.s["entities"]
    if not (isinstance(tool, str) and tool in ents and isinstance(spill, str) and spill in ents):
        return
    if not M.is_fabric_candidate(ents[tool]):
        return
    ctx.data["_tl_absorb"] = (tool, spill, float(ents[spill].get("liquid_l", 0.0) or 0.0),
                              spill_kind(ents[spill]))


def _absorb_after(ctx, result):
    found = ctx.data.get("_tl_absorb")
    if not found:
        return
    tool, spill, before_l, (kind, origin) = found
    ents = ctx.w.s["entities"]
    after_l = float((ents.get(spill) or {}).get("liquid_l", 0.0) or 0.0) if spill in ents else 0.0
    taken = max(0.0, before_l - after_l)
    if taken > 0 and tool in ents:
        record_absorbed(ctx.w, tool, kind, origin, taken)


before("absorb", order=95)(_absorb_before)
after("absorb", order=95)(_absorb_after)


def _wring_before(ctx):
    target = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(target) if isinstance(target, str) else None
    if rec is not None and rec.get("_spill"):
        ctx.data["_tl_wring"] = (target, _held_l(ctx.w.s, target))


def _wring_after(ctx, result):
    found = ctx.data.get("_tl_wring")
    if not found:
        return
    target, before_l = found
    after_l = _held_l(ctx.w.s, target)
    if before_l <= 0 or after_l >= before_l:
        return
    dest = ctx.args.get("destination") or ctx.w.robot["room"]
    dst = ("entity:" + dest) if dest in ctx.w.s["entities"] else ("floor:" + str(dest))
    release_spill(ctx.w, target, dst, cause=target, fraction=(before_l - after_l) / before_l)


before("wring", order=95)(_wring_before)
after("wring", order=95)(_wring_after)


# ----------------------------------------------------------------------
# derived fields (8.2)

def _rec(state, eid):
    rec = _entities(state).get(eid)
    return rec if isinstance(rec, dict) else None


@derived_field("made", types=BED_TYPES)
def derived_made(state, eid):
    """made: the stored flag while the bed is still dressed and its layers smooth (refresh_bed's rule, live)."""
    from .bedding import bed_summary, max_wrinkle
    rec = _rec(state, eid)
    if rec is None or "made" not in rec:
        return MISSING
    if not rec.get("made"):
        return False
    return bool(bed_summary(state, eid)["layers"]) and max_wrinkle(state, eid) <= 0.3


@derived_field("bed_state", types=BED_TYPES)
def derived_bed_state(state, eid):
    """bed_state: the layer summary of the bed now (bedding.bed_summary), for any bed a predicate reads."""
    from .bedding import bed_summary
    if _rec(state, eid) is None:
        return MISSING
    return bed_summary(state, eid)


@derived_field("paired_with")
def derived_paired_with(state, eid):
    """paired_with: the partner while one of the pair is still attached to the other, else None."""
    rec = _rec(state, eid)
    if rec is None or "paired_with" not in rec:
        return MISSING
    partner = rec.get("paired_with")
    if not partner:
        return partner
    prec = _rec(state, partner)
    if prec is None:
        return None
    joined = (prec.get("location") == eid and prec.get("relation") == "attached") or \
             (rec.get("location") == partner and rec.get("relation") == "attached")
    return partner if joined else None


@derived_field("spread_on")
def derived_spread_on(state, eid):
    """spread_on: the surface while the textile still lies on it (location and relation on), else None."""
    rec = _rec(state, eid)
    if rec is None or "spread_on" not in rec:
        return MISSING
    surface = rec.get("spread_on")
    if not surface:
        return surface
    return surface if rec.get("location") == surface and rec.get("relation") in ("on", None) else None


FIXTURE_FLAGS = ("dry_fixture", "drying_rack", "hang_slots", "clothesline", "curtain_rod", "shower_rod")


@derived_field("drying_mode")
def derived_drying_mode(state, eid):
    """drying_mode: the set-up while the textile is still on the fixture it was set out on (the hang_dry fixture it
    records, else any drying fixture, bar or rod it is on), else None."""
    rec = _rec(state, eid)
    if rec is None or "drying_mode" not in rec:
        return MISSING
    mode = rec.get("drying_mode")
    if not mode:
        return mode
    loc = rec.get("location")
    fixture = rec.get("_dried_on")
    if fixture and loc != fixture:
        return None
    parent = _rec(state, loc) if isinstance(loc, str) else None
    if parent is None:
        return None
    if any(parent.get(flag) for flag in FIXTURE_FLAGS) or parent.get("category") in ("curtain_rod", "shower_rod",
                                                                                     "hooks") \
            or rec.get("relation") == "hung":
        return mode
    return None


@derived_field("on_hanger")
def derived_on_hanger(state, eid):
    """on_hanger: the hanger while it is still attached to the garment, else None."""
    rec = _rec(state, eid)
    if rec is None or "on_hanger" not in rec:
        return MISSING
    hanger = rec.get("on_hanger")
    if not hanger:
        return hanger
    hrec = _rec(state, hanger)
    return hanger if hrec is not None and hrec.get("location") == eid else None


@derived_field("holding_garment")
def derived_holding_garment(state, eid):
    """holding_garment (a hanger): the garment while the hanger is still attached to it, else None."""
    rec = _rec(state, eid)
    if rec is None or "holding_garment" not in rec:
        return MISSING
    garment = rec.get("holding_garment")
    if not garment:
        return garment
    return garment if rec.get("location") == garment and garment in _entities(state) else None


# ----------------------------------------------------------------------
# settings skills (10.3)

def _step(eid, verb, hold=False, **args):
    step = {"skill": "textiles_laundry.operate", "target": eid, "verb": verb}
    if hold:
        step["hold"] = True
    step.update(args)
    return step


@settings_skill("appliance.program", types=WASHER_TYPES + DRYER_TYPES)
def program_steps(state, eid):
    """Another program of a washer or dryer (set_mode)."""
    rec = _rec(state, eid) or {}
    machine = rec.get("appliance") or {}
    current = machine.get("program")
    return [(name, {"skill": "set_mode", "target": eid, "mode": name})
            for name in sorted(machine.get("programs") or {}) if name != current]


def _range_steps(state, eid, name):
    rec = _rec(state, eid) or {}
    options = (rec.get("setting_options") or {}).get(name)
    current = (rec.get("settings") or {}).get(name)
    if isinstance(options, list):
        values = options
    elif isinstance(options, dict) and _number(options.get("min")) and _number(options.get("max")):
        lo, hi = options["min"], options["max"]
        values = sorted({lo, hi, int(round((lo + hi) / 2.0))})
    else:
        return []
    return [(value, {"skill": "set", "target": eid, "setting": name, "value": value})
            for value in values if value != current]


@settings_skill("settings.delay_h", types=WASHER_TYPES)
def delay_steps(state, eid):
    return _range_steps(state, eid, "delay_h")


@settings_skill("settings.minutes", types=DRYER_TYPES)
def minutes_steps(state, eid):
    return _range_steps(state, eid, "minutes")


def _textile(state, eid):
    rec = _rec(state, eid)
    return rec if rec is not None and M.is_textile(rec) else None


@settings_skill("inside_out")
def inside_out_steps(state, eid):
    rec = _textile(state, eid)
    if rec is None or rec.get("pair_kind") == "shoe":
        return []
    return [(not bool(rec.get("inside_out")), _step(eid, "turn_inside_out", hold=True))]


@settings_skill("sleeves_rolled")
def sleeves_steps(state, eid):
    rec = _textile(state, eid)
    if rec is None or not rec.get("has_sleeves"):
        return []
    if rec.get("sleeves_rolled"):
        return [(False, _step(eid, "unroll_sleeves", hold=True))]
    return [(True, _step(eid, "roll_sleeves", hold=True))]


@settings_skill("folded")
def folded_steps(state, eid):
    """A folded textile unfolded where it lies (the damaging direction B14 probes); nothing for an unfolded one (a
    fold needs a style and a surface the reference chose)."""
    rec = _textile(state, eid)
    if rec is None or not rec.get("folded"):
        return []
    return [(False, _step(eid, "unfold", hold=True, destination="here"))]


@settings_skill("made", types=BED_TYPES)
def made_steps(state, eid):
    rec = _rec(state, eid) or {}
    if rec.get("made"):
        return [(False, _step(eid, "strip_bed"))]
    if (rec.get("bed_state") or {}).get("layers"):
        return [(True, _step(eid, "make_bed"))]
    return []


@settings_skill("zipper_hidden")
def zipper_hidden_steps(state, eid):
    rec = _rec(state, eid) or {}
    if "zipper_hidden" not in rec:
        return []
    hidden = bool(rec.get("zipper_hidden"))
    return [(not hidden, _step(eid, "fluff", zipper="visible" if hidden else "hidden"))]


def _ironing_station(state, eid):
    """(plugged-in iron, board) in the room eid is in (the first of each by id), or None: what the iron-too-hot probe
    needs (the iron_garment skill does not plug an iron in)."""
    room = root_room(state, eid)
    iron = board = None
    for other in sorted(_entities(state)):
        rec = _entities(state)[other]
        if not isinstance(rec, dict) or root_room(state, other) != room:
            continue
        if iron is None and rec.get("category") == "iron" and rec.get("plugged_in"):
            iron = other
        elif board is None and rec.get("ironing_surface"):
            board = other
    return (iron, board) if iron and board else None


def _iron_too_hot(state, eid, kind):
    """The textile ironed on the hottest setting (the skill's temp "max"), where that does `kind` of damage: a
    thermoplastic fibre melts, the others scorch, when their limit is below the dial's top (care.IRON_TEMPS["max"]);
    a label that forbids ironing is another damage."""
    from .care import IRON_TEMPS
    rec = _textile(state, eid)
    station = _ironing_station(state, eid) if rec is not None and rec.get("location") != "robot" else None
    if station is None or kind in (rec.get("damage") or {}):
        return []
    fab, care = M.fabric(rec), M.care(rec)
    if care.get("iron") == "none" or not fab.get("iron_c"):
        return []
    melt_c = P.melt_point(state, eid)
    if kind != ("melted" if melt_c is not None else "scorched"):
        return []
    if IRON_TEMPS["max"] <= (melt_c if melt_c is not None else float(fab.get("scorch_c", 200))):
        return []
    return [(1.0, {"skill": "textiles_laundry.iron_garment", "garment": eid, "iron": station[0], "board": station[1],
                   "temp": "max"})]


@settings_skill("damage.scorched")
def scorched_steps(state, eid):
    """This domain's damaging verb for B14 (10.3, `damaged:<e>.<f>`): a fibre that scorches ironed on the top setting."""
    return _iron_too_hot(state, eid, "scorched")


@settings_skill("damage.melted")
def melted_steps(state, eid):
    """This domain's damaging verb for B14: a thermoplastic fibre ironed on the top setting."""
    return _iron_too_hot(state, eid, "melted")


# ----------------------------------------------------------------------
# hazard zones (13.3, 13.5)

@hook("hazard_zones")
def hot_presses(state):
    """A hot iron or garment steamer (above HOT_ZONE_C) is a hazard zone around the device itself."""
    out = []
    for eid in sorted(_entities(state)):
        rec = _entities(state)[eid]
        if not isinstance(rec, dict) or rec.get("category") not in ("iron", "garment_steamer"):
            continue
        temp = rec.get("temperature_c")
        if _number(temp) and temp >= HOT_ZONE_C:
            out.append({"id": "hot_iron:" + eid, "room": None, "subjects": [eid]})
    return out


# ----------------------------------------------------------------------
# unfamiliar things: one identify-first refusal on every verb of this domain

def unfamiliar_first(ctx):
    """Before hook (order 1) of every textiles_laundry verb: an unfamiliar (novel) thing in any entity parameter --
    the textile, the tool, the hanger, the needle, the bed's hamper (strip_bed into), the soak basin, the drying rack
    -- gets the kernel's identify-first refusal (ctx.refuse_unfamiliar) before any check reads what it really is
    (closeout_breakages: strip_bed answered an unfamiliar hamper, basket or bag by its true type). No-op in oracle
    mode."""
    for pname, param in sorted(ctx.spec.params.items()):
        if str(param.get("type", "")).lstrip("?") not in ("entity", "target"):
            continue
        eid = ctx.args.get(pname)
        if isinstance(eid, str) and eid in ctx.w.s["entities"]:
            ctx.refuse_unfamiliar(eid)


def _register_unfamiliar():
    for name in sorted(n for n, spec in ACTIONS.items() if spec.fn.__module__.startswith(__package__ + ".")):
        before(name, order=1)(unfamiliar_first)


_register_unfamiliar()

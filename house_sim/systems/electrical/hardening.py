"""Verification-hardening prerequisites of the electrical domain (docs/design/VERIFY_HARDENING.md section 25 "WP-D
electrical", 23.3; docs/domains/electrical.md section 16).

  causes          the inputs a derived electrical field is computed from (inputs(): the thing itself, its supply path
                  and breakers, the load on a tripped breaker or strip on that path, what it is installed in or sits on,
                  the parts installed in it, the switches wired to it): common.set_if passes them to the writer log for
                  every physical write of a field the ledger compares (6.2's causal link), so a lamp that goes dark
                  because a resident switched it off is not the robot's, while one that goes dark because the robot
                  overloaded its circuit is. The mutation sites that know their cause pass it themselves (a breaker
                  tripping on its circuit's loads, a GFCI on the wet device, a cord scorching under what draws through
                  it, a cell leaking in its device).
  identify first  charge and connect_cable answer an unresolved look-alike member or an unfamiliar thing with the
                  kernel's one generic refusal (15.3; members of a test-only group excepted); core set refuses an
                  unresolved member of an electrical group, core install an unfamiliar part for an electrical slot.
  hazard zones    the live electrical hazards a warning is about (13.5), each only as the thing itself.
  cord_hazard     a derived field (8.2): the recorded hazards of a cord are written when it is routed, so a rug laid
                  over it later never showed and one taken off it kept `under_rug` (cords.live_hazards recomputes them
                  from the cord's cells as the room is now; the trip / fire-risk invariants read the same).
  settings        settings skills (10.3: B14's changed / damaged probes and the inverses of setting changes): plugging
                  and unplugging, a clock's 12 / 24-hour display, and the damaging verbs -- a cord pulled by the cord
                  (strain relief), a one-way plug forced in reversed (bent prong), a cord coiled tight (kinked), a cable
                  yanked by its cable (damaged) -- through the skill electrical.set_field (battery covers: admin
                  inventory's generic cover_open skill).
"""
from ...kernel.plugins import before, derived_field, hook, settings_skill, skill
from ...kernel.predicates import MISSING
from . import common as C


# ------------------------------------------------------------------------------------------------------- causes (6.2)

def inputs(state, eid, w=None):
    """Sorted ids a derived field of eid is computed from (see the module doc): never empty for a known id. w: the World
    (its static switch index; a scan without it)."""
    from . import lighting as L
    from . import power as P
    ents = state["entities"]
    rec = ents.get(eid)
    if rec is None:
        return []
    ids = {eid}
    try:
        ids.update(P.cut_ids(state, eid))
    except (KeyError, TypeError):
        pass
    loc = rec.get("location")
    if isinstance(loc, str) and loc in ents:
        ids.add(loc)
    for info in (rec.get("slots") or {}).values():
        item = (info or {}).get("item") if isinstance(info, dict) else None
        if isinstance(item, str) and item in ents:
            ids.add(item)
    if rec.get("category") == "light":
        ids.update(L.controllers(state, eid, L.switch_index(state, w) if w is not None else None))
    partners = rec.get("three_way_with")
    for other in (partners if isinstance(partners, list) else [partners]):
        if isinstance(other, str) and other in ents:
            ids.add(other)
    return sorted(ids)


_UNCHARGED = None


def uncharged_field(field):
    """field is process or bookkeeping for electrical (watch/electrical.json): the ledger never compares it, so a write
    of it needs no recorded cause (the setting that caused it is what is compared)."""
    global _UNCHARGED
    if _UNCHARGED is None:
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "verify", "watch", "electrical.json")
        try:
            with open(path, encoding="utf-8") as handle:
                fields = json.load(handle).get("fields") or {}
        except (OSError, ValueError):
            fields = {}
        _UNCHARGED = frozenset(name for name, spec in fields.items()
                               if (spec if isinstance(spec, str) else (spec or {}).get("class")) in ("process", "bookkeeping"))
    return field in _UNCHARGED


def physical_cause(w, eid, cause=None, field=None):
    """The cause ids to record for a write to eid by the current writer: None unless the World tracks writers and the
    writer is a physical system (a robot window's writes are the robot's whatever they cite), or when `field` is one
    the ledger never compares (uncharged_field); `cause` when given (a str, an iterable or a callable of no
    arguments), else inputs()."""
    if not getattr(w, "tracking", False) or (getattr(w, "writer", None) or ("",))[0] != "physical":
        return None
    if field is not None and uncharged_field(field):
        return None
    if callable(cause):
        cause = cause()
    if cause is None:
        return inputs(w.s, eid, w)
    return [cause] if isinstance(cause, str) else sorted(set(c for c in cause if isinstance(c, str)))


# ------------------------------------------------------------------------------------------- identify first (15.3)

def test_only(ctx, eid):
    """eid is a member of a look-alike group only a test resolves ("never": the USB-C data and charge-only cables look,
    connect and charge alike and tell themselves apart only by what the link then does)."""
    realism = getattr(ctx.env, "realism", None)
    rec = ctx.w.s["entities"].get(eid) or {}
    if realism is None or rec.get("novel"):
        return False
    _, group = realism.group_of(rec)
    return group is not None and group.get("resolve") == "never"


def identify_first(ctx, *eids):
    """The kernel's one generic refusal (ctx.refuse_unidentified, the same words for every member) when any of eids is
    unresolved -- an unfamiliar thing, or a look-alike member before its resolving action (a power brick before its
    rating plate is read, a phone before it is inspected) -- except members of a test-only group (test_only). Call it
    after the visibility and reach checks and before any check that depends on what the things really are: a verb
    whose answer depends on the brick's connector or the phone's port would tell the members apart."""
    for eid in eids:
        if isinstance(eid, str) and ctx.unresolved(eid) and not test_only(ctx, eid):
            ctx.refuse_unidentified(eid)


ELECTRICAL_PARTS = ("battery_", "bulb_", "tool_pack")


def electrical_slots(rec):
    """rec has a slot that takes cells, packs or bulbs (a battery compartment, a charger bay, a lamp socket)."""
    for info in (rec.get("slots") or {}).values():
        accepts = (info or {}).get("accepts") if isinstance(info, dict) else None
        if any(isinstance(a, str) and a.startswith(ELECTRICAL_PARTS) for a in (accepts or ())):
            return True
    return False


@before("install", order=5)
def _identify_part(ctx):
    """An unfamiliar thing is no part a battery compartment, charger bay or lamp socket takes: the one generic refusal
    before core install matches the slot by the thing's true part type (the sweep found unfamiliar AA cells going
    into a NiMH charger's bay where every other unfamiliar thing had "no compatible slot")."""
    dev = ctx.w.s["entities"].get(ctx.args.get("destination")) or {}
    if electrical_slots(dev):
        from . import percept
        percept.refuse_unfamiliar(ctx, ctx.args.get("target"))


LOOKALIKE_GROUPS = None


def electrical_member(ctx, eid):
    """eid belongs to one of the look-alike groups electrical's type data declares (a19_led_bulb, power_adapter, ...)."""
    global LOOKALIKE_GROUPS
    realism = getattr(ctx.env, "realism", None)
    rec = ctx.w.s["entities"].get(eid) if isinstance(eid, str) else None
    if realism is None or rec is None:
        return False
    if LOOKALIKE_GROUPS is None:
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "types", "electrical.json")
        with open(path, encoding="utf-8") as handle:
            LOOKALIKE_GROUPS = frozenset((json.load(handle).get("looks_like") or {}).keys())
    name, _ = realism.group_of(rec)
    return name in LOOKALIKE_GROUPS


# ---------------------------------------------------------------------------------------------- hazard zones (13.5)

BARED = ("exposed", "chewed", "fray")


def live_hazard(state, eid, rec):
    """The kind of active electrical hazard eid is (a warning about it is content-gated, 13.5), or None: a bared cord or
    half-seated plug on live mains, an arcing, scorched or burning cord or receptacle still live, live gear with water
    inside or a wet live receptacle, a swollen or burning battery."""
    live = rec.get("energized") or rec.get("live")
    if rec.get("battery_swollen") or rec.get("thermal_runaway"):
        return "battery"
    if not live:
        return None
    if rec.get("plugged_in") and rec.get("_cord_damage") in BARED:
        return "exposed_wire"
    if rec.get("plugged_in") and C.num(rec.get("plug_seated"), 1.0) < 0.99:
        return "exposed_prongs"
    if rec.get("ignited") or rec.get("scorched") or (rec.get("category") == "outlet" and rec.get("_fault") == "arcing"):
        return "overheating"
    if rec.get("water_ingress") or (rec.get("category") == "outlet" and C.num(rec.get("wetness"), 0.0) > 0.3):
        return "wet_live"
    return None


@hook("hazard_zones")
def hazard_zones(state):
    """Active electrical hazards for the content guards' warnings (kernel speech.hazard_zones; WP4's request): each
    only as the thing itself (no room: walking into the room with a frayed lamp cord is not walking into the hazard)."""
    out = []
    for eid, rec in sorted((state.get("entities") or {}).items()):
        if isinstance(rec, dict):
            kind = live_hazard(state, eid, rec)
            if kind:
                out.append({"id": "electrical:{}:{}".format(kind, eid), "room": None, "subjects": [eid]})
    return out


# ------------------------------------------------------------------------------------------------- derived field (8.2)

@derived_field("cord_hazard")
def derived_cord_hazard(state, eid):
    """A plugged cord's hazards as the room is now (cords.live_hazards); MISSING for a record without a mains plug."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if not rec.get("plug_required") and "cord_cells" not in rec:
        return MISSING
    from .cords import live_hazards
    return live_hazards(state, eid)


# --------------------------------------------------------------------------------------------- settings skills (10.3)

def _record(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


def _step(eid, field, value):
    return {"skill": "electrical.set_field", "target": eid, "field": field, "value": value}


def _corded(rec):
    return bool(rec.get("plug_required")) and rec.get("plug_in_place") != "wall" and C.num(rec.get("cord_length_m"), 0.0) > 0


def free_receptacle(state, device):
    """A live receptacle with a free socket that takes the device's plug (no child cover), nearest first in the
    device's room, else the first elsewhere: the outlet plug_in will accept. None when there is none."""
    from . import cords
    from . import power as P
    ents = state["entities"]
    rec = ents.get(device) or {}
    room, cells = C.anchor_cells(state, device)
    here = cells[0] if cells else (0, 0)
    best = []
    for oid in sorted(ents):
        orec = ents[oid]
        if oid == device or not P.is_provider(orec) or orec.get("child_cover"):
            continue
        if P.free_socket(state, oid) is None or not P.provider_output_live(state, oid) or                 not cords.plug_fits(rec.get("plug_type"), orec.get("socket_type")):
            continue
        orid, ocells = C.anchor_cells(state, oid)
        same = orid == room and bool(ocells)
        best.append((0 if same else 1, C.dist_cells(here, ocells[0]) if same else 0, oid))
    best.sort()
    return best[0][2] if best else None


@settings_skill("plugged_in")
def plugged_in_steps(state, eid):
    """Unplug a plugged device; plug an unplugged one into a free live receptacle that takes its plug."""
    rec = _record(state, eid)
    if not rec.get("plug_required"):
        return []
    if rec.get("plugged_in"):
        return [(False, _step(eid, "plugged_in", False))]
    return [(True, _step(eid, "plugged_in", True))] if free_receptacle(state, eid) else []


@settings_skill("strain_relief_damaged")
def strain_steps(state, eid):
    """Damaging verb: unplug by pulling the cord (unplug grip=cord) a plugged corded device whose cord is sound."""
    rec = _record(state, eid)
    if not _corded(rec) or not rec.get("plugged_in") or rec.get("strain_relief_damaged"):
        return []
    return [(True, _step(eid, "strain_relief_damaged", True))]


@settings_skill("plug_damage")
def plug_damage_steps(state, eid):
    """Damaging verb: force a one-way (grounded or sleeved) plug in reversed (plug_in orientation=reversed): a bent
    prong."""
    from .cords import polarized_one_way
    rec = _record(state, eid)
    if not rec.get("plug_required") or rec.get("plug_damage") or not polarized_one_way(rec):
        return []
    if not rec.get("plugged_in") and free_receptacle(state, eid) is None:
        return []
    return [("bent_prong", _step(eid, "plug_damage", "bent_prong"))]


@settings_skill("kinked")
def kinked_steps(state, eid):
    """Damaging verb: unplug a plugged corded device and wrap its cord tight (unplug coil=tight): a kinked cord."""
    rec = _record(state, eid)
    if not _corded(rec) or not rec.get("plugged_in") or rec.get("kinked"):
        return []
    return [(True, _step(eid, "kinked", True))]


@settings_skill("damaged", types=("cable",))
def cable_damage_steps(state, eid):
    """Damaging verb: yank a connected cable out by its cable (disconnect_cable grip=cable)."""
    from .cables import is_cable
    rec = _record(state, eid)
    if not is_cable(rec) or rec.get("damaged"):
        return []
    if not any((end or {}).get("at") for end in (rec.get("ends") or {}).values()):
        return []
    return [(True, _step(eid, "damaged", True))]


@settings_skill("clock.format_24h")
def clock_format_steps(state, eid):
    """Switch a settable clock between its 12- and 24-hour display (set_time format=...)."""
    clock = _record(state, eid).get("clock")
    if not isinstance(clock, dict):
        return []
    value = not bool(clock.get("format_24h"))
    return [(value, _step(eid, "clock.format_24h", value))]


@skill("electrical.set_field")
def set_field(r, target, field, value):
    """The battery's settings probe (10.3) for electrical's settings and damaging verbs. Privileged routing (P11)."""
    from ...tasks import skills as S
    from ...tasks.runner import PlanError
    from . import skills as E
    rec = r.state["entities"].get(target) or {}
    if field == "plugged_in":
        if value:
            outlet = free_receptacle(r.state, target)
            if outlet is None:
                raise PlanError("electrical.set_field: no free live receptacle takes {}'s plug".format(target))
            E.plug_in(r, target, outlet)
        else:
            E.unplug(r, target)
    elif field == "strain_relief_damaged":
        E.unplug(r, target, grip="cord")
    elif field == "kinked":
        E.unplug(r, target, coil="tight")
    elif field == "plug_damage":
        if rec.get("plugged_in"):
            E.unplug(r, target)
        outlet = free_receptacle(r.state, target)
        if outlet is None:
            raise PlanError("electrical.set_field: no free live receptacle takes {}'s plug".format(target))
        E.plug_in(r, target, outlet, orientation="reversed")
    elif field == "damaged":
        ends = [e for e, info in sorted((rec.get("ends") or {}).items()) if (info or {}).get("at")]
        dev = rec["ends"][ends[0]]["at"]
        S.approach(r, dev)
        r.do("disconnect_cable", target=target, destination=dev, grip="cable")
    elif field == "clock.format_24h":
        S.approach(r, target)
        r.do("set_time", target=target, format="24h" if value else "12h")
    else:
        raise PlanError("electrical.set_field: no probe for {}".format(field))

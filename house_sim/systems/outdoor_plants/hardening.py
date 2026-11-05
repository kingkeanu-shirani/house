"""What the house ledger needs from the outdoor_plants domain (VERIFY_HARDENING 6.2-6.5, 6.8, 8.2, 10.3, 13, 15; package
WP-D outdoor_plants; docs/domains/outdoor_plants.md 0e).

Mess kinds (6.5; kernel/plugins.py @mess_kind, measured on any state; parcels report no origin: one group per kind):
  yard_debris  g  leaves and clippings on a lawn's patches and on paved surfaces (outside:<room>), a leaf pile, plant debris,
                  a fallen branch (where they lie), leaves in a bed or on a cover, a gutter's and a downspout's outlet's debris,
                  a pool's surface, a skimmer basket, a net, a mower's catcher, a tarp's load, a yard bag's fill, what lies on
                  a grate (entity:<thing>), the sediment and clog under a grate (drain:<grate>).
                  Sinks: compost, the outdoor waste carts and streams (yard waste, compost), a bag at the curb. Never a loose
                  heap on the street (a relocation) or down a drain (a wrong sink).
  ash          g  ash_kg of a fire pit, a grill or whatever took the ash. Sink: a waste container (the metal ash can).
Flows are logged by the verbs that move them (common.flow): rake, bag_leaves, clear_gutter, clear_grate, skim, shovel of ash,
mow's catcher and every verb that makes plant debris; the measures are the cross-check the ledger trusts. Debris absorbed into a
compost pile leaves no parcel (the compost's mass decays and is harvested, so it is no measure of what arrived): its flow says
it arrived.

Liquid into a plant (6.5, plumbing_liquids' classes when that domain is loaded): what water_plant pours from a can that is not
clean is a flow into entity:<plant> (a wrong sink); the hose's water counts on the house water meter (6.8).

Derived fields (8.2): `_op_status` (the status a hook left, refreshed from the current state for things whose status follows
where they are: cushions, covers, umbrellas, grills, firewood racks, bags, grates, pools, surfaces, gutters, potted plants),
`secured` (only while the thing stays where it was secured and the ballast or cord is still on it), `flows` and `flow_ok` (the
last flow test, only while the clog it saw has not changed), `bundle` (from the branches on the tarp now).

Settings skills (10.3, B14 probes): a mower's deck and mode, a spreader's dial, an umbrella's tilt; cover and umbrella straps;
damaging verbs: `hangers_ok` (a gutter pried with a tool), `joints_ok` (a downspout taken apart), `bark_damage` (a powered
trimmer run at a trunk), `torn` (a paper bag dragged over paving), `damaged` (a pump run with its basket out).

Hazard zones (13.5): a downed line, a hot fire pit, an icy surface, an open planting hole, a wasps' nest: each the thing, never a
room. hazard_created (6.4): thorny trimmings carry the tag `sharp_offcut`, cut poison ivy `chemical_soaked`.

Readings (15.2): check_moisture's and a held probe's values carry `readings`.

Unfamiliar and unresolved things (realistic perception, review I4): an order-1 before hook on every verb gives an unfamiliar thing
named in any entity parameter the kernel's identify-first refusal, before any guard reads what it really is; the implicit pickers
pass an unfamiliar held thing over (common.find_held, nearby_entity, verbs.get_tool). An unresolved look-alike is no ballast before
it is identified: what it weighs must not decide.
"""
import copy
import sys

from ...kernel.plugins import MESS_KINDS, after, before, derived_field, hook, mess_kind, settings_skill
from ...kernel.predicates import MISSING, root_room
from . import common as C
from . import lawn as L

PLUMBING = __name__.rsplit(".outdoor_plants.", 1)[0] + ".plumbing_liquids.hardening"
SURFACE_KG_M2 = 0.15            # yard.clear_leaves: a full leaf cover of a paved surface, kg per m2
LIVE_LINE = "line"
ICY = 0.3
HOT_PIT_C = 60.0


# ---------------------------------------------------------------------------
# mess kinds (6.5)

def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _surface_grams(state, rec):
    soil = rec.get("soil")
    if not isinstance(soil, dict):
        return 0.0
    cover = _num(soil.get("leaves")) + _num(soil.get("litter")) + _num(soil.get("debris"))
    if cover <= 0:
        return 0.0
    if C.is_type(rec, "walkway"):
        area = _num(rec.get("area_m2")) or 6.0
    elif rec.get("category") == "floor" and rec.get("outdoor"):
        size = ((state.get("rooms") or {}).get(rec.get("location")) or {}).get("size_m") or [4.0, 4.0]
        area = float(size[0]) * float(size[1])
    else:
        return 0.0
    return cover * area * SURFACE_KG_M2 * 1000.0


def _bed_grams(rec):
    soil = rec.get("soil")
    leaves = _num(soil.get("leaves")) if isinstance(soil, dict) else 0.0
    from . import garden
    return (_num(rec.get("debris_kg")) + _num(rec.get("_turned_in_kg")) + leaves * garden.bed_area(rec) * SURFACE_KG_M2) * 1000.0


def debris_grams(state, eid, rec):
    """(location key, grams) of what eid itself holds or is of yard debris, or None. Reads the state only."""
    typ = rec.get("type")
    if rec.get("_composted"):
        return None
    grid = rec.get("grid")
    if isinstance(grid, dict) and C.is_type(rec, "lawn"):
        grams = sum(_num(v) for v in grid.get("leaves") or ()) * L.LEAF_KG_M2 * 1000.0
        grams += sum(_num(v) for v in grid.get("clippings") or ())
        kinds, stray = grid.get("kind") or (), grid.get("stray") or ()
        grams += sum(_num(v) for k, v in zip(kinds, stray) if k == "H")       # sod and granules thrown on the paving
        return (C.mess_key(state, rec.get("location")), grams)
    if typ == "leaf_pile":
        grams = (_num(rec.get("leaves_kg")) + _num(rec.get("clippings_kg"))) * 1000.0
        return (C.mess_key(state, rec.get("location"), rec.get("relation")), grams)
    if typ == "plant_debris":
        return (C.mess_key(state, rec.get("location"), rec.get("relation")), _num(rec.get("mass_kg")) * 1000.0)
    if C.is_type(rec, "branch"):
        if rec.get("relation") == "attached" or rec.get("hanging"):
            return None                 # still in the tree
        return (C.mess_key(state, rec.get("location"), rec.get("relation")), _num(rec.get("mass_kg")) * 1000.0)
    if C.is_type(rec, "leaf_bag"):
        fill = rec.get("fill") or {}
        grams = (_num(fill.get("leaves_kg")) + _num(fill.get("clippings_kg")) + _num(fill.get("branches_kg"))) * 1000.0
        return (("entity", eid), grams)
    if C.is_type(rec, "tarp"):
        load = rec.get("load")
        grams = sum(_num(v) for k, v in load.items() if str(k).endswith("_kg")) * 1000.0 if isinstance(load, dict) else 0.0
        return (("entity", eid), grams)
    if C.is_type(rec, "garden_bed"):
        return (("entity", eid), _bed_grams(rec))
    for field in ("catcher_kg", "load_kg", "net_kg", "outlet_leaves_kg"):
        if field in rec:
            return (("entity", eid), _num(rec.get(field)) * 1000.0)
    grams = _num(rec.get("debris_kg")) * 1000.0
    if grams > 0:
        return (("entity", eid), grams)
    return None


@mess_kind("yard_debris", unit="g", sinks=lambda state, loc: yard_sink(state, loc))
def yard_parcels(state):
    """(location key, origin None, grams) of every parcel of yard debris in a state (module docstring)."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict):
            continue
        found = debris_grams(state, eid, rec)
        if found is not None and found[0] is not None and found[1] > 0:
            out.append((found[0], None, found[1]))
        below = _num(rec.get("pipe_debris_kg")) + _num(rec.get("basin_sediment_kg"))
        if below > 0:
            out.append((("drain", eid), None, below * 1000.0))
        grams = _surface_grams(state, rec)
        if grams > 0:
            key = C.mess_key(state, rec.get("location"))
            if key is not None:
                out.append((key, None, grams))
    return out


CURB_ROOMS = ("sidewalk", "curb", "street")
STREAMS = ("yard_waste", "compost", "organics")


def yard_sink(state, loc):
    """Where yard debris belongs (6.5: compost, a yard-waste bin, the curb pickup zone): in or on a compost bin, an outdoor
    waste cart or a container of a yard-waste stream anywhere along its location chain, or a bag or a bundle at the curb. A
    loose heap on the street is no sink, a storm drain a wrong sink. The ledger's location classes judge the rest."""
    from ...kernel import ledger
    cls, ident = loc
    if cls != "entity":
        return False
    entities = state.get("entities") or {}
    rec = entities.get(ident)
    if rec is None:
        return False
    link, seen, stream = ident, set(), False
    while link in entities and link not in seen:
        seen.add(link)
        node = entities[link]
        if node.get("waste_stream") == "recycling":
            return False                # leaves spoil a recycling load, whatever the cart is
        if node.get("waste_stream") in STREAMS and (node.get("container") or node.get("accepts_any_waste")):
            stream = True
        link = node.get("location")
    if stream or (ledger.classes_of(rec) | ledger.chain_classes(state, ident)) & {"compost", "outdoor_waste"}:
        return True
    if C.is_type(rec, "leaf_bag") or rec.get("bundle"):
        room = (state.get("rooms") or {}).get(root_room(state, ident)) or {}
        return room.get("type") in CURB_ROOMS
    return False


@mess_kind("ash", unit="g", sinks=["waste"], eps=50.0, rho=0.05)
def ash_parcels(state):
    """(entity:<holder>, origin None, grams) of ash: a fire pit's or grill's bed, and whatever took it."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if isinstance(rec, dict):
            kg = _num(rec.get("ash_kg"))
            if kg > 0:
                out.append((("entity", eid), None, kg * 1000.0))
    return out


@hook("episode_start")
def mark_mess(w, env):
    """Written before the baseline: litter is refuse (the kernel's waste flag, the waste_item kind counts it, house.removed and
    house.lost exempt it); a fallen branch and plant debris are parcels of yard debris (core's moves of a mess entity log
    their flows)."""
    entities = w.s["entities"]
    for eid in sorted(entities):
        rec = entities[eid]
        if rec.get("litter") and "waste" not in rec and not rec.get("sentimental"):
            w.mut(eid)["waste"] = True
        elif (rec.get("type") == "plant_debris" or C.is_type(rec, "branch")) and "mess_kind" not in rec and rec.get("relation") != "attached":
            C.tag_debris(w, eid)


# ---------------------------------------------------------------------------
# a liquid poured into a plant (6.5), the hose on the water meter (6.8)

def poured_flow(w, src, liters, dest):
    """The mess flow of water_plant pouring `liters` out of held vessel src onto dest, before the vessel is drained. By
    plumbing_liquids' classes (when that domain is loaded and has registered its kinds): dirty, chemical or used-grease liquid
    from a measured vessel, and a product from its own bottle, arrive in entity:<dest>, a wrong sink (a plant, its pot, a bed,
    the lawn); clean water and plant food are content, no mess."""
    if not getattr(w, "tracking", False) or liters <= 1e-6 or dest not in w.s["entities"]:
        return None
    P = sys.modules.get(PLUMBING)
    if P is None:
        return None
    rec = w.s["entities"].get(src) or {}
    kind = P.record_kind(rec)
    if kind not in MESS_KINDS or kind == P.SPILL:
        return None
    measured = P.measured_holder(rec)
    if kind == P.GREASE:
        measured = measured and rec.get("liquid") in P.USED_GREASE
    source = ("entity", src) if measured else "created:{}".format(src)
    return w.mess_flow(kind, round(float(liters), 6), source, ("entity", dest), origin="water_plant:{}".format(src), cause=src)


def meter_water(w, liters):
    """Count hose litres on the house water meter (resources.json meters.water.path; plumbing's meter_add when loaded)."""
    if liters <= 0:
        return
    P = sys.modules.get(PLUMBING.rsplit(".", 1)[0] + ".common")
    if P is not None:
        P.meter_add(w, liters)
        return
    node = w.sec("plumbing").setdefault("meter", {})
    node["l"] = round(_num(node.get("l")) + float(liters), 3)


# ---------------------------------------------------------------------------
# unfamiliar things and unresolved look-alikes

def unfamiliar_first(ctx):
    """Before hook (order 1) of every outdoor_plants verb: an unfamiliar (novel) thing in any entity parameter -- the target,
    the destination, the source, the tool -- gets the kernel's identify-first refusal (Ctx.refuse_unfamiliar) before any check
    reads what it really is (a tarp's load, a mower's catcher, a meter's probe, a rake's tines, a fertilizer's dose). The twin
    sweep of every verb x every entity parameter found 96 commands answering by true type. No-op in oracle mode."""
    for pname, param in sorted(ctx.spec.params.items()):
        if param.get("type") not in ("entity", "target", "any", "list"):
            continue                    # an "any" or "list" parameter may name things too (rake's and shovel's destination, skim's select)
        value = ctx.args.get(pname)
        for eid in value if isinstance(value, (list, tuple)) else (value,):
            if isinstance(eid, str) and eid in ctx.w.s["entities"]:
                ctx.refuse_unfamiliar(eid)


def guard_verbs(names):
    for name in names:
        before(name, order=1)(unfamiliar_first)


# ---------------------------------------------------------------------------
# hazard zones (13.3, 13.5)

@hook("hazard_zones")
def yard_hazard_zones(state):
    """[{"id", "room": None, "subjects"}] of this domain's active hazards, each the thing itself: a downed live line, a fire pit
    still hot, an icy path, an open uncovered planting hole, a wasps' nest. So "stay away from the power line" warns and "come
    and look at the line" invites, and "come into the yard" is no invitation into a hazard."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict):
            continue
        typ = rec.get("type")
        if typ == "power_line":
            if rec.get("downed") and rec.get("live", True):
                out.append({"id": "{}:{}".format(LIVE_LINE, eid), "room": None, "subjects": [eid]})
        elif typ == "fire_pit":
            hot = max(_num(rec.get("temperature_c")), _num(rec.get("_ember_c")))
            if hot >= HOT_PIT_C:
                out.append({"id": "hot:{}".format(eid), "room": None, "subjects": [eid]})
        elif typ == "planting_hole":
            if not rec.get("covered") and not rec.get("filled"):
                out.append({"id": "hole:{}".format(eid), "room": None, "subjects": [eid]})
        elif rec.get("nest") == "wasp":
            out.append({"id": "wasps:{}".format(eid), "room": None, "subjects": [eid]})
        elif _num(rec.get("ice")) >= ICY and (C.is_type(rec, "walkway") or (rec.get("category") == "floor" and rec.get("outdoor"))):
            out.append({"id": "ice:{}".format(eid), "room": None, "subjects": [eid]})
    return out


# ---------------------------------------------------------------------------
# derived fields (8.2)

class StatusView(object):
    """A throwaway world over a state for the evaluation overlay: the status functions read it as they read the World, and what
    they write stays in copies. Reads the state only; nothing is written back."""

    def __init__(self, state):
        self.s = dict(state)
        self.s["entities"] = dict(state.get("entities") or {})
        self._own = set()

    def ent(self, eid):
        return self.s["entities"][eid]

    def mut(self, eid):
        if eid not in self._own:
            self.s["entities"][eid] = copy.deepcopy(self.s["entities"][eid])
            self._own.add(eid)
        return self.s["entities"][eid]

    def conn_m(self, cid):
        return {}

    def children(self, eid):
        return sorted(e for e, r in self.s["entities"].items() if r.get("location") == eid)

    def static_index(self, name, predicate, reads=None):
        return sorted(e for e, r in self.s["entities"].items() if predicate(r))

    def held_items(self):
        return [h for h in ((self.s.get("robot") or {}).get("hands") or {}).values() if h]

    @property
    def robot(self):
        return self.s.get("robot") or {}

    def stat(self, name, amount=1):
        return None

    def sec(self, name):
        return {}

    def emit(self, kind, **data):
        return None


def _gutter_status(view, eid):
    rec = view.ent(eid)
    st = rec.get("_op_status") or {}
    if st.get("cleared"):
        C.set_status(view, eid, cleared=_num(rec.get("debris_kg")) <= 0.5)


def _refreshers():
    """(type name, status function) of the things whose status follows from where they are and what is on them. Every one is a
    pure function of the state over a StatusView."""
    from . import patio, plants, pool, yard
    return (("outdoor_cushion", patio.cushion_refresh), ("furniture_cover", patio.cover_status), ("patio_umbrella", patio.umbrella_status),
            ("firewood_rack", patio.stack_status), ("leaf_bag", yard.bag_refresh), ("drain_grate", yard.grate_status),
            ("walkway", yard.surface_status), ("pool", pool.pool_status), ("gutter", _gutter_status), ("plant", plants.plant_status))


REFRESHED = ("outdoor_cushion", "furniture_cover", "patio_umbrella", "firewood_rack", "leaf_bag", "drain_grate", "walkway", "floor_surface", "pool",
             "gutter", "plant", "gas_grill", "charcoal_grill")


def derived_status(state, eid):
    """The evaluation's `_op_status` (8.2): what a hook or a slow tick last wrote, refreshed from the current state for things
    whose status follows where they are (moved by a verb that runs no hook, by a resident, by the wind, or since the last slow
    tick). A flag that records an event stays as it was."""
    rec = (state.get("entities") or {}).get(eid)
    if not isinstance(rec, dict):
        return MISSING
    from . import patio
    view = StatusView(state)
    if patio.is_grill(rec):
        patio.grill_status(view, eid)
    elif rec.get("category") == "floor" and rec.get("outdoor"):
        from . import yard
        yard.surface_status(view, eid)
    else:
        for name, fn in _refreshers():
            if C.is_type(rec, name):
                fn(view, eid)
                break
    status = view.ent(eid).get("_op_status")
    return status if status is not None else MISSING


derived_field("_op_status", derived_status, types=REFRESHED)


@derived_field("secured")
def derived_secured(state, eid):
    """secured holds while the thing is where it was secured and what holds it is still there (a thing lifted off, dragged away
    or moved by anyone stops being secured; a cord or a ballast taken off stops it holding)."""
    entities = state.get("entities") or {}
    rec = entities.get(eid)
    if not isinstance(rec, dict) or "secured" not in rec:
        return MISSING
    held = rec.get("secured")
    if not isinstance(held, dict):
        return held if held is not None else MISSING
    at = rec.get("_secured_at")
    if at is not None and at != C.secured_place(rec):
        return MISSING
    by = held.get("by")
    if by == "weight" and not any(r.get("location") == eid and r.get("ballast") for r in entities.values()):
        return MISSING
    if by == "tie_down" and not any(r.get("location") == eid and r.get("relation") == "attached" for r in entities.values()):
        return MISSING
    return held


@derived_field("flows", types=("drain_grate",))
def derived_flows(state, eid):
    """A grate's last flow test, read again from the blockage and the pipe (a push that clogs the pipe after the test: false)."""
    rec = (state.get("entities") or {}).get(eid)
    if not isinstance(rec, dict) or "flows" not in rec:
        return MISSING
    if not rec.get("flow_tested"):
        return rec.get("flows")
    return _num(rec.get("blockage")) < 0.3 and _num(rec.get("pipe_debris_kg")) < 1.0


@derived_field("flow_ok", types=("downspout",))
def derived_flow_ok(state, eid):
    """A downspout's flow test, read again from its clog (None until it was tested)."""
    rec = (state.get("entities") or {}).get(eid)
    if not isinstance(rec, dict) or "flow_ok" not in rec:
        return MISSING
    if rec.get("flow_ok") is None:
        return None
    return _num(rec.get("clog")) < 0.3


@derived_field("bundle")
def derived_bundle(state, eid):
    """A branch bundle from the branches on the tarp or pile now: `tied` only while they are the branches that were tied;
    `compliant` by the household's rules on what is there now."""
    entities = state.get("entities") or {}
    rec = entities.get(eid)
    if not isinstance(rec, dict) or not isinstance(rec.get("bundle"), dict):
        return MISSING
    branches = [r for r in entities.values() if r.get("location") == eid and C.is_type(r, "branch")]
    was = rec["bundle"]
    if len(branches) != int(was.get("branches", 0)):
        return dict(was, tied=False, compliant=False, branches=len(branches))
    return was


# ---------------------------------------------------------------------------
# a thing lifted off is no longer secured (world behaviour)

@after("pick_up", order=151)
def _lifted_off(ctx, result):
    """Lifting a thing off its stakes, pins or ties ends what held it: its record and the wind's model stop counting it (a
    tie-down cord stays on it and goes with it)."""
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target") or "")
    held = rec.get("secured") if rec is not None else None
    if not isinstance(held, dict) or held.get("by") not in ("stake", "pins", "weight", "stacked"):
        return
    m = w.mut(ctx.args["target"])
    m.pop("secured", None)
    m.pop("_secured_at", None)
    for key in ("staked", "pinned"):
        if m.get(key):
            m[key] = False


# ---------------------------------------------------------------------------
# settings skills (10.3)

def _set_step(eid, setting, value):
    return {"skill": "set", "target": eid, "setting": setting, "value": value}


def _operate(eid, verb, tool=None, **args):
    step = {"skill": "outdoor_plants.operate", "target": eid, "verb": verb}
    if tool:
        step["tool"] = tool
    step.update(args)
    return step


def _numeric(state, eid, name, step_by):
    rec = (state.get("entities") or {}).get(eid) or {}
    option = (rec.get("setting_options") or {}).get(name)
    current = _num((rec.get("settings") or {}).get(name))
    if not isinstance(option, dict):
        return []
    lo, hi = _num(option.get("min")), _num(option.get("max"))
    value = current + step_by if current + step_by <= hi else current - step_by
    if not lo <= value <= hi or value == current:
        return []
    value = int(value) if float(value) == int(value) else value
    return [(value, _set_step(eid, name, value))]


@settings_skill("settings.deck_mm", types=("lawn_mower",))
def deck_steps(state, eid):
    return _numeric(state, eid, "deck_mm", 10)


@settings_skill("settings.rate", types=("broadcast_spreader",))
def rate_steps(state, eid):
    return _numeric(state, eid, "rate", 1)


@settings_skill("settings.tilt_deg", types=("patio_umbrella",))
def tilt_steps(state, eid):
    return _numeric(state, eid, "tilt_deg", 10)


@settings_skill("strapped", types=("patio_umbrella",))
def strap_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("strapped"):
        return [(False, _operate(eid, "secure_outdoor", method="release"))]
    if rec.get("open"):
        return []
    return [(True, _operate(eid, "secure_outdoor", method="strap"))]


@settings_skill("fastened", types=("furniture_cover",))
def fasten_steps(state, eid):
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("fastened"):
        return [(False, _operate(eid, "secure_outdoor", method="release"))]
    if rec.get("covers") and rec.get("fit"):
        return [(True, _operate(eid, "secure_outdoor", method="strap"))]
    return []


# damaging verbs: a settings skill for a condition field (B14's damaged probe)

@settings_skill("hangers_ok", types=("gutter",))
def pry_steps(state, eid):
    """A gutter pried at with the scoop pole pulls its hangers (yard.clear_gutter method=pry). Only with something to pry out."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if not rec.get("hangers_ok", True) or _num(rec.get("debris_kg")) <= 0.05:
        return []
    return [(False, _operate(eid, "clear_gutter", tool="gutter_scoop_pole", method="pry"))]


@settings_skill("joints_ok", types=("downspout",))
def disassemble_steps(state, eid):
    """A downspout taken apart at its joints (yard.clear_downspout method=disassemble)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if not rec.get("joints_ok", True):
        return []
    return [(False, _operate(eid, "clear_gutter", method="disassemble"))]


@settings_skill("bark_damage", types=("tree",))
def bark_steps(state, eid):
    """A powered trimmer run close at a trunk (lawn.trim_rings with clearance under 5 cm)."""
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    lawn = next((e for e in sorted(entities) if C.is_type(entities[e], "lawn") and entities[e].get("location") == rec.get("location")), None)
    if lawn is None or not rec.get("pos"):
        return []
    cell = [int(rec["pos"][0]) // 2, int(rec["pos"][1]) // 2]
    return [(_num(rec.get("bark_damage")) + 0.35, _operate(lawn, "trim_plant", tool="string_trimmer", part="grass", clearance_cm=1.0,
                                                           area=[cell[0], cell[1], cell[0], cell[1]]))]


@settings_skill("torn", types=("leaf_bag",))
def tear_steps(state, eid):
    """A paper bag dragged over paving tears (yard._after_drag_bag). Only a paper bag on or next to paving."""
    rec = (state.get("entities") or {}).get(eid) or {}
    if rec.get("torn") or rec.get("material") != "paper":
        return []
    room = (state.get("rooms") or {}).get(root_room(state, eid)) or {}
    if room.get("floor_material") not in ("concrete", "stone", "asphalt") and room.get("type") not in ("driveway", "sidewalk"):
        return []
    return [(True, {"skill": "outdoor_plants.drag_on", "target": eid})]


@settings_skill("damaged", types=("pool_pump",))
def pump_steps(state, eid):
    """Lifting the skimmer's basket with the pump on runs it dry (pool._after_uninstall)."""
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if rec.get("damaged") or not rec.get("on"):
        return []
    skimmer = next((e for e in sorted(entities) if C.is_type(entities[e], "skimmer") and (((entities[e].get("slots") or {}).get("basket")) or {}).get("item")), None)
    if skimmer is None:
        return []
    return [(True, _operate(skimmer, "uninstall"))]


# ---------------------------------------------------------------------------
# structured readings (15.2)

def moisture_reading(subject, value, quantity="moisture", unit="dial_1_10", field=None):
    """A meter's dial as a structured reading (perceive.index(env).readings picks it up)."""
    reading = {"subject": subject, "quantity": quantity, "value": value, "unit": unit}
    if field:
        reading["field"] = field
    return reading



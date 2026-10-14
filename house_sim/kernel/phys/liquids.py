"""Liquids: quantities, tilt and slosh spills, pouring, overflow, spills, absorption, wetting, drips and leaks, and
carry dynamics (docs/design/TIER1_PHYSICS.md 5.7 and 5.8; laws overflow, open_spill, drip and carry; fidelity
P-approx).

Liquids are scalar volumes. A liquid holder is an open, waterproof container (``capacity_l`` > 0) or a support with a
rim (props ``rim_m``: trays, saucers, sinks) that pools up to ``top area x rim_m``. It holds ``liquid_l`` litres of the
named ``liquid`` (liquids.json: density, viscosity, freezing point, floor hazard, film thickness); the frozen fraction
``ice_frac`` (thermal phase law) does not pour, slosh or tilt out.

    liquid_mass = liquid_l * rho                     fill = liquid_l / capacity_l
    headroom_m  = (1 - fill) * inner height          inner height = inner_size_m[2], else 0.9 x the outer height

``spill`` is the single entry point for liquid leaving where it was. Below 5 ml nothing happens (droplets). Liquid
that lands on an entity runs down its support chain: each level catches what it can (a holder pools it up to its
capacity, a soaking body saturates, anything else takes a 1.5 mm surface film and, for an item resting wet on a
water-sensitive finish, starts the wet-contact timer) and the rest goes on down; what reaches the floor is a puddle,
owned by the first ``phys.spill_sink`` hook that returns an id (cleaning), else a kernel ``generic_item`` with category
"spill" (area = V / film thickness, 3 mm on absorbent floors) whose cell joins the room's ``wet_cells``; more of the
same liquid on that cell grows the puddle. ``deposit_below`` is the same walk starting under an item: overflow, tilt
and tip spills, carry slosh, perforated pours, drips and leaks all use it, so liquid is conserved down to 5 ml.

Each spill is one consequence (kind ``phys.spill``, or ``phys.overflow``), counted in ``phys_spills`` and, when
robot-attributable (6.4), in the global ``spills`` flag. Drips and leaks arrive in 5 ml portions: a portion counts
only when it starts a new puddle.

Law entry points for the settle pass (registered by PHYS-INT through ``LAWS``): ``law_overflow`` (20) and
``law_open_spill`` (70), ``fn(w, eid, env=None, cmap=None) -> [consequence records]``; ``carry(w, travel, by)`` runs
once per action phase when the robot travelled holding items. Records go to the step's shared consequence list
(processes.consequence) and are returned as well.
"""
import math

from .. import geometry
from ..plugins import run_hooks
from . import processes, thermal
from .processes import blame, dims, finite, num, sibling, top_area_m2

props = sibling("props")
support = sibling("support")
impact = sibling("impact")

SURFACE_FILM_MM = 1.5           # water film a flat non-absorbent top holds before it runs off (5.7)
SOAKED_FLOOR_FILM_MM = 3.0      # an absorbent floor without its own film_mm (grass, soil) soaks a puddle to 3 mm
# A body soaks liquid up (rather than shedding it as a film) when it absorbs at least 0.25 L per kg of dry mass:
# textiles, paper, cardboard, foam, sponges, potting soil, wicker and plaster do; finished wood (0.05), MDF (0.15),
# cork, terracotta and lined paperboard take a surface film, and water damages them through wet contact instead.
SOAK_MIN_L_PER_KG = 0.25
MIN_PUDDLE_M2 = 0.0025          # a 5 ml splash still covers about 5 x 5 cm (manipulation make_spill)
MAX_PUDDLE_SIDE_M = 3.0
WIDE_MOUTH = 0.6                # mouth_frac at and above which a container empties completely on its side (5.7)
# pour rates (5.7): a narrow bottle neck about 0.25 L/s, a pitcher spout 0.5, a wide mouth or bucket 2.0; viscosity
# slows the flow by sqrt(20 cP / viscosity), clamped at 1 (honey about 22x slower than water, oil about 1.7x)
OPENING_LPS = {"narrow": 0.25, "spout": 0.5, "wide": 2.0}
REFERENCE_VISC_CP = 20.0
# carry (5.8, manipulation's model unchanged): gait acceleration factor per effective speed mode
SPEED_FACTOR = {"careful": 0.5, "normal": 1.0, "fast": 1.7}
# a cracked mug or a split bag seam seeps about half a litre an hour
LEAK_L_PER_H = 0.5
DAMP_WETNESS = 0.1              # a wet item stops dripping once only this residual dampness is left
SIDE_ORIENTATIONS = ("on_side", "side", "lying")
INVERTED_ORIENTATIONS = ("upside_down", "inverted")
TRICKLE_CAUSES = ("drip", "leak")
MAX_CHAIN = 64                  # support levels a spill runs down at most (cycles in malformed states end the walk)


# ----- quantities ---------------------------------------------------------------------------------------------------

def droplet_l():
    """The droplet limit (litres): less than this spilled is droplets, no puddle and no harm. liquids.json
    min_spill_l (5 ml), the one threshold that replaces navigation's SPILL_MIN_L and food's SPILL_HARM_G."""
    return float(props.LIQUIDS["min_spill_l"]["value"])


def liquid_density_kg_l(rec):
    """kg per litre of the liquid a record holds: its liquid_density, else liquids.json by name, else water."""
    explicit = finite(rec.get("liquid_density"))
    if explicit is not None:
        return explicit
    return num(props.liquid_props(rec.get("liquid") or "water").get("rho"), 1000.0) / 1000.0


def liquid_mass(rec):
    """Mass (kg) of the liquid a record holds."""
    liters = num(rec.get("liquid_l"))
    return liters * liquid_density_kg_l(rec) if liters > 0 else 0.0


def fill_frac(rec):
    """liquid_l / capacity_l (0 without a capacity; above 1 when overfull)."""
    cap = num(rec.get("capacity_l"))
    return num(rec.get("liquid_l")) / cap if cap > 0 else 0.0


def free_liquid_l(rec):
    """Litres that can flow: the liquid that is not frozen (ice_frac)."""
    frozen = min(1.0, max(0.0, num(rec.get("ice_frac"))))
    return max(0.0, num(rec.get("liquid_l"))) * (1.0 - frozen)


def inner_height_m(rec):
    """Inside height of a container: inner_size_m[2], else 0.9 x its outer height."""
    _, _, h = dims(rec)
    inner = rec.get("inner_size_m")
    if isinstance(inner, (list, tuple)) and len(inner) >= 3:
        return num(inner[2], h)
    return 0.9 * h


def headroom_m(rec):
    """Height of empty wall above the liquid surface (m); 0 for a record without a capacity."""
    cap = num(rec.get("capacity_l"))
    if cap <= 0:
        return 0.0
    fill = min(1.0, num(rec.get("liquid_l")) / cap)
    return max(0.0, (1.0 - fill) * inner_height_m(rec))


def container_closed(rec):
    """True if a container's contents (liquid or loose) cannot come out (moved from manipulation, unchanged)."""
    if rec.get("sealed") or rec.get("lid_on") or rec.get("covered") and rec.get("cover_sealed"):
        return True
    if isinstance(rec.get("trigger"), dict) or isinstance(rec.get("pump"), dict):
        return True        # a trigger sprayer or pump head is shut unless it is worked
    cap = rec.get("cap") or {}
    if isinstance(cap, dict) and cap.get("on") and not cap.get("loose"):
        return True
    if rec.get("openable") and not rec.get("open"):
        return True
    zipper = rec.get("zipper") or {}
    if isinstance(zipper, dict) and zipper and num(zipper.get("closed_frac")) >= 0.98:
        return True
    closure = rec.get("closure") or {}
    if isinstance(closure, dict) and closure.get("holds"):
        return True
    return False


def _diameter_m(rec):
    """Free-surface diameter of the tilt and slosh models: the smaller footprint side, at least 3 cm."""
    w, d, _ = dims(rec)
    return max(0.03, min(w, d))


def mouth_frac(rec, value=None):
    """Opening width as a fraction of the container's width: value (props mouth_frac), else the record's mouth_frac,
    else opening_m (or opening_width_m) over the width, else 1 (an open top)."""
    for candidate in (value, rec.get("mouth_frac")):
        found = finite(candidate)
        if found is not None:
            return max(0.0, min(1.0, found))
    opening = finite(rec.get("opening_m", rec.get("opening_width_m")))
    if opening is not None:
        return max(0.0, min(1.0, opening / _diameter_m(rec)))
    return 1.0


def _narrow_neck_keep_l(rec, frac):
    """Litres a narrow-necked container keeps lying on its side: the volume below the neck, capacity x (1 - m) / 2."""
    return num(rec.get("capacity_l")) * (1.0 - frac) / 2.0 if frac < WIDE_MOUTH else 0.0


def tilt_spill_l(rec, deg, mouth=None):
    """Litres an open container loses tilted by deg from upright (5.7 open_spill).

    Spill begins when tan(a) >= 2 x headroom / D. The retained volume is that of a prism of the container's capacity,
    inner height H and width D below a surface through the low lip inclined at a: 1 - D tan(a) / (2H) of the capacity
    while that surface still covers the base, H / (2 D tan(a)) once it uncovers it. A narrow neck (mouth_frac < 0.6)
    keeps at least what it keeps on its side; past 90 degrees the mouth is below the liquid and everything runs out.
    mouth: the resolved mouth_frac (props) when the caller has it."""
    liters = free_liquid_l(rec)
    cap = num(rec.get("capacity_l"))
    if liters <= 0 or cap <= 0 or container_closed(rec):
        return 0.0
    angle = abs(num(deg)) % 360.0
    angle = 360.0 - angle if angle > 180.0 else angle
    if angle <= 0.0:
        return 0.0
    if angle > 90.0:
        return round(liters, 4)
    height, width = max(0.01, inner_height_m(rec)), _diameter_m(rec)
    kept = 0.0
    if angle < 90.0:
        t = math.tan(math.radians(angle))
        kept = cap * (1.0 - width * t / (2.0 * height) if width * t <= height else height / (2.0 * width * t))
    kept = max(kept, _narrow_neck_keep_l(rec, mouth_frac(rec, mouth)))
    return round(max(0.0, liters - kept), 4)


def tip_spill_l(rec, orientation, mouth=None):
    """Litres an open container loses lying in an orientation (5.7): on its side a wide mouth loses everything and a
    narrow neck keeps capacity x (1 - mouth_frac) / 2; upside down everything; upright nothing."""
    liters = free_liquid_l(rec)
    if liters <= 0 or num(rec.get("capacity_l")) <= 0 or container_closed(rec):
        return 0.0
    if orientation in INVERTED_ORIENTATIONS:
        return round(liters, 4)
    if orientation in SIDE_ORIENTATIONS:
        return round(max(0.0, liters - _narrow_neck_keep_l(rec, mouth_frac(rec, mouth))), 4)
    return 0.0


def slosh_spill_l(rec, motion):
    """Litres sloshed out of an open container for a motion factor (manipulation's P-approx model, unchanged):
    amplitude 0.010 x motion x sqrt(D / 0.08) m; above the headroom, (amplitude - headroom) x area x 0.6 leaves."""
    liters = free_liquid_l(rec)
    cap = num(rec.get("capacity_l"))
    if liters <= 0 or cap <= 0 or container_closed(rec):
        return 0.0
    _, _, h = dims(rec)
    amplitude = 0.010 * motion * math.sqrt(_diameter_m(rec) / 0.08)
    headroom = headroom_m(rec)
    if amplitude <= headroom:
        return 0.0
    area = cap * 1e-3 / max(0.02, h * 0.9)
    return min(liters, round((amplitude - headroom) * area * 1000.0 * 0.6, 4))


def topple_threshold(rec):
    """Motion factor above which an unsecured upright item riding on a carried load tips (5.8): 2.8 x min(w, d) / h;
    items already on their side or lying flat never tip."""
    w, d, h = dims(rec)
    if rec.get("orientation") in ("on_side", "flat"):
        return 99.0
    return 2.8 * max(0.01, min(w, d)) / max(0.01, h)


def pour_rate_lps(src, opening=None):
    """Pour rate (L/s) out of src: the opening's rate (narrow neck 0.25, spout 0.5, wide mouth or bucket 2.0) times
    min(1, sqrt(20 cP / viscosity)). opening defaults to the record's pour_opening, "spout" when it has a spout, else
    narrow or wide by its mouth_frac."""
    kind = opening or src.get("pour_opening")
    if not kind:
        kind = "spout" if src.get("spout") else "narrow" if mouth_frac(src) < WIDE_MOUTH else "wide"
    if kind not in OPENING_LPS:
        raise ValueError("Unknown pour opening: " + str(kind))
    visc = max(1e-6, num(props.liquid_props(src.get("liquid") or "water").get("visc_cp"), 1.0))
    return OPENING_LPS[kind] * min(1.0, math.sqrt(REFERENCE_VISC_CP / visc))


def hold_capacity_l(state, eid, phys=None):
    """Litres an entity can hold as a pool: its capacity_l, else top area x rim_m for a rimmed support (tray, saucer,
    sink); None when it cannot hold liquid at all."""
    rec = state["entities"][eid]
    cap = num(rec.get("capacity_l"))
    if cap > 0:
        return cap
    p = phys if phys is not None else props.props(state, eid)
    rim = finite(p.rim_m)
    if rim is not None and rim > 0:
        return top_area_m2(rec) * rim * 1000.0
    return None


def holds_liquid(state, eid, phys=None):
    """Whether liquid arriving at eid pools in it: an open, waterproof, unperforated holder."""
    rec = state["entities"][eid]
    p = phys if phys is not None else props.props(state, eid)
    return (bool(p.waterproof) and not rec.get("perforated") and not container_closed(rec)
            and hold_capacity_l(state, eid, p) is not None)


def absorb_capacity_l(p):
    """Litres a soaking body holds when saturated (props absorb_l = absorption x dry mass); 0 when it is waterproof
    or absorbs less than SOAK_MIN_L_PER_KG (it sheds a film instead)."""
    cap = finite(p.absorb_l)
    if cap is None or cap <= 0 or p.waterproof:
        return 0.0
    mass = finite(p.mass_kg)
    if mass is not None and mass > 0 and cap / mass < SOAK_MIN_L_PER_KG:
        return 0.0
    return cap


def tilt_of(state, eid):
    """Tilt (degrees from upright) of an item: its own tilt_deg plus that of the support it rests on."""
    entities = state["entities"]
    rec = entities[eid]
    tilt = num(rec.get("tilt_deg"))
    loc = rec.get("location")
    if loc in entities and rec.get("relation", "on") == "on":
        tilt += num(entities[loc].get("tilt_deg"))
    return tilt


# ----- holders: adding and removing liquid --------------------------------------------------------------------------

def _mix_into(w, eid, liters, liquid, density=None):
    """Add liters of liquid to a holder: the larger volume names the mixture; an explicit density on either side is
    volume-averaged into liquid_density."""
    rec = w.ent(eid)
    have = num(rec.get("liquid_l"))
    m = w.mut(eid)
    if have <= 1e-9 or not rec.get("liquid") or (rec.get("liquid") != liquid and liters > have):
        m["liquid"] = liquid
    if density is not None or "liquid_density" in rec:
        mine = liquid_density_kg_l(rec) if have > 0 else 0.0
        incoming = density if density is not None else num(props.liquid_props(liquid).get("rho"), 1000.0) / 1000.0
        m["liquid_density"] = round((have * mine + liters * incoming) / max(1e-9, have + liters), 4)
    m["liquid_l"] = round(have + liters, 4)


def _drain(w, eid, liters):
    """Take liters out of eid. A kernel puddle below the droplet limit disappears with its wet cell; an emptied
    holder forgets where its contents came from (contents_from)."""
    rec = w.ent(eid)
    left = round(max(0.0, num(rec.get("liquid_l")) - liters), 4)
    if rec.get("_phys_spill"):
        if left < droplet_l():
            _remove_puddle(w, eid)
        else:
            w.mut(eid)["liquid_l"] = left
            _size_puddle(w, eid)
        return
    w.mut(eid)["liquid_l"] = left
    if left <= 0 and "contents_from" in rec:
        w.mut(eid).pop("contents_from", None)


def _mark_provenance(w, dst, src):
    """Kernel v2.9 contents_from contract (as core.mark_contents_from; the kernel never imports systems.core): dst
    now holds what came from the origin of src's contents, so realistic perception renders it as that origin's
    look-alike until the robot identifies it."""
    entities = w.s["entities"]
    origin = entities[src].get("contents_from")
    if not (isinstance(origin, str) and origin in entities and origin != src):
        origin = src
    if origin != dst and entities[dst].get("contents_from") != origin:
        w.mut(dst)["contents_from"] = origin


def pour_in(w, dst, liters, liquid="water", temp_c=None, source=None, density=None, by=None):
    """Pour liters of liquid into dst (from a faucet, or transfer's source). Returns (delivered, spilled).

    A perforated destination lets everything through to what is below it; a non-holder is spilled onto. Into a
    holder: the thermal-shock check runs first while the thermal_shock law is active (thermal.pour_shock_check: cold
    into a hot vessel couples 1.0, hot into a colder one 0.6; a cracked holder spills it all), the bulk temperature
    mixes by heat capacity, the liquid is added, and what does not fit overflows where dst stands."""
    liters = max(0.0, num(liters))
    if liters <= 0:
        return 0.0, 0.0
    state = w.s
    rec = w.ent(dst)
    origin = source or dst
    if rec.get("perforated"):
        deposit_below(w, dst, liters, liquid, source=origin, by=by, cause="pour_through")
        return 0.0, round(liters, 4)
    p = props.props(state, dst)
    if not holds_liquid(state, dst, p):
        spill(w, liters, liquid, onto=dst, source=origin, by=by)
        return 0.0, round(liters, 4)
    take = round(min(liters, max(0.0, hold_capacity_l(state, dst, p) - num(rec.get("liquid_l")))), 4)
    if take > 0 and temp_c is not None:
        if thermal.pour_shock_check(w, dst, temp_c, by=by):
            deposit_below(w, dst, liters, liquid, source=origin, by=by)
            return 0.0, round(liters, 4)
        thermal.mix_in(w, dst, take, temp_c, liquid)
    if take > 0:
        _mix_into(w, dst, take, liquid, density)
    over = liters - take
    if over > 1e-9:
        deposit_below(w, dst, over, liquid, source=dst, by=by, cause="overflow")
    return round(take, 4), round(over, 4)


def transfer(w, src, dst, liters, by=None):
    """Move up to liters of src's free (unfrozen) liquid into dst. Returns (delivered, overflow_l): what dst kept and
    what spilled (overflow, a perforated or non-holding destination, or a destination cracked by thermal shock).
    dst records the provenance of what arrived (contents_from)."""
    rec = w.ent(src)
    amount = round(min(max(0.0, num(liters)), free_liquid_l(rec)), 4)
    if amount <= 0 or src == dst:
        return 0.0, 0.0
    liquid = rec.get("liquid") or "water"
    temp_c = num(rec.get("temperature_c"), 21.0)
    density = finite(rec.get("liquid_density"))
    _drain(w, src, amount)
    delivered, spilled = pour_in(w, dst, amount, liquid, temp_c=temp_c, source=src, density=density, by=by)
    if delivered > 0 and dst in w.s["entities"]:
        _mark_provenance(w, dst, src)
    return delivered, spilled


def overflow(w, eid, by=None):
    """overflow law (20): liquid above a holder's capacity spills where the holder stands. Returns the litres lost."""
    state = w.s
    rec = state["entities"][eid]
    cap = hold_capacity_l(state, eid)
    if cap is None:
        return 0.0
    excess = round(num(rec.get("liquid_l")) - round(cap, 4), 4)
    if excess <= 0:
        return 0.0
    w.mut(eid)["liquid_l"] = round(cap, 4)
    deposit_below(w, eid, excess, rec.get("liquid") or "water", source=eid, by=by, cause="overflow")
    return round(excess, 4)


# ----- wetting and absorbing ----------------------------------------------------------------------------------------

def wet(w, eid, liters, liquid, source=None):
    """Liquid reaches eid (5.7): a soaking body takes it up to saturation (wetness and moisture rise by
    liters / absorb_l), anything else takes a surface film up to top area x 1.5 mm (wetness = film / that capacity).
    Then every ``phys.wetted`` hook sees (w, eid, litres that reached it, liquid, source). Returns the litres kept."""
    liters = max(0.0, num(liters))
    if liters <= 0:
        return 0.0
    rec = w.ent(eid)
    p = props.props(w.s, eid)
    wetness = min(1.0, max(0.0, num(rec.get("wetness"))))
    cap = absorb_capacity_l(p)
    if cap > 0:
        take = min(liters, cap * (1.0 - wetness))
        if take > 0:
            m = w.mut(eid)
            m["wetness"] = round(min(1.0, wetness + take / cap), 4)
            m["moisture"] = round(min(1.0, num(rec.get("moisture")) + take / cap), 4)
    else:
        film = top_area_m2(rec) * SURFACE_FILM_MM          # 1 mm of water over 1 m2 is 1 L
        take = min(liters, film * (1.0 - wetness)) if film > 0 else 0.0
        if take > 0:
            w.mut(eid)["wetness"] = round(min(1.0, wetness + take / film), 4)
    run_hooks("phys.wetted", w, eid, round(liters, 4), liquid, source)
    return round(take, 4)


def absorb(w, absorber, source=None, liters=None):
    """absorber soaks up liquid (5.7): min(V, absorb_l x (1 - wetness)), V = liters or the source's liquid (both bounds
    when both are given). The litres come out of source (a kernel puddle shrinks, and goes below 5 ml). Returns the
    litres absorbed; the held water is then wetness x absorb_l."""
    state = w.s
    cap = absorb_capacity_l(props.props(state, absorber))
    if cap <= 0:
        return 0.0
    src = state["entities"].get(source) if source is not None else None
    available = num(src.get("liquid_l")) if src is not None else None
    if liters is None and available is None:
        return 0.0
    wanted = max(0.0, num(liters, 0.0)) if liters is not None else available
    if available is not None:
        wanted = min(wanted, available)
    take = round(min(wanted, cap * (1.0 - min(1.0, max(0.0, num(state["entities"][absorber].get("wetness")))))), 4)
    if take <= 0:
        return 0.0
    liquid = (src.get("liquid") if src is not None else None) or "water"
    if src is not None:
        _drain(w, source, take)
    return wet(w, absorber, take, liquid, source=source)


def _catch(w, eid, liters, liquid, source):
    """eid catches liquid from above: a holder pools it up to its capacity, anything else is wetted.
    Returns (litres kept, "held" | "soaked" | "film")."""
    state = w.s
    p = props.props(state, eid)
    if holds_liquid(state, eid, p):
        room = max(0.0, hold_capacity_l(state, eid, p) - num(state["entities"][eid].get("liquid_l")))
        take = round(min(liters, room), 4)
        if take > 0:
            _mix_into(w, eid, take, liquid)
        return take, "held"
    return wet(w, eid, liters, liquid, source=source), "soaked" if absorb_capacity_l(p) > 0 else "film"


# ----- spills -------------------------------------------------------------------------------------------------------

def _resolve_at(state, at, source):
    """(room, cell) of a floor spill: at = (room, cell) or a room id; default the floor under source (else the
    robot's cell)."""
    robot = state["robot"]
    if at is None:
        rid, cell = processes.floor_cell(state, source) if source in state["entities"] else (robot.get("room"), None)
    elif isinstance(at, str):
        rid, cell = at, None
    else:
        rid, cell = at[0], (at[1] if len(at) > 1 else None)
    if rid not in state["rooms"]:
        rid, cell = robot.get("room"), None
    if cell is None:
        if rid == robot.get("room") and robot.get("pos"):
            cell = robot["pos"]
        else:
            cell = geometry.free_floor_cell(state, rid, clearance=False, reachable=False) or (0, 0)
    return rid, (int(cell[0]), int(cell[1]))


def _puddle_at(w, rid, cell, liquid):
    """The kernel puddle of this liquid already on the cell, if any (puddles never move, so the World's static index
    of them, rebuilt only when an entity is created or destroyed, is exact)."""
    entities = w.s["entities"]
    for eid in w.static_index("phys:puddles", lambda r: bool(r.get("_phys_spill"))):
        rec = entities.get(eid)
        if rec is not None and rec.get("location") == rid and rec.get("liquid") == liquid and \
                tuple(rec.get("pos") or ()) == cell:
            return eid
    return None


def _size_puddle(w, eid):
    """area = V / film thickness, as a square of that area: the liquid's film_mm (liquids.json) on a hard floor, the
    floor's film_mm (floors.json: 3 mm into carpet and rugs) on an absorbent one."""
    rec = w.ent(eid)
    liters = num(rec.get("liquid_l"))
    if rec.get("on_carpet"):
        film = num(props.floor_props(w.s, rec["location"], rec.get("pos")).get("film_mm"), SOAKED_FLOOR_FILM_MM)
    else:
        film = num(props.liquid_props(rec.get("liquid") or "water").get("film_mm"), SURFACE_FILM_MM)
    area = max(MIN_PUDDLE_M2, liters / max(0.1, film))
    side = round(min(MAX_PUDDLE_SIDE_M, math.sqrt(area)), 3)
    m = w.mut(eid)
    m["area_m2"] = round(area, 4)
    m["volume_l"] = round(liters, 4)
    m["size_m"] = [max(0.05, side), max(0.05, side), 0.002]


def _remove_puddle(w, eid):
    """A kernel puddle soaked up or used up: the entity goes, and its cell leaves wet_cells unless another kernel
    puddle still covers it."""
    rec = w.ent(eid)
    rid, cell = rec.get("location"), list(rec.get("pos") or ())
    w.destroy(eid)
    if rid not in w.s["rooms"]:
        return
    others = [e for e in w.static_index("phys:puddles", lambda r: bool(r.get("_phys_spill")))
              if e in w.s["entities"] and w.s["entities"][e].get("location") == rid and
              list(w.s["entities"][e].get("pos") or ()) == cell]
    wet_cells = [list(c) for c in w.s["rooms"][rid].get("wet_cells") or []]
    if not others and cell in wet_cells:
        w.room_m(rid)["wet_cells"] = [c for c in wet_cells if c != cell]


def _floor_puddle(w, rid, cell, liquid, liters, source, onto):
    """Put liters on the floor: a phys.spill_sink hook's entity, else a kernel puddle (new, or the same liquid's on
    that cell grown). Returns (spill id, True when a new spill entity was made)."""
    for sid in run_hooks("phys.spill_sink", w, rid, list(cell), liquid, round(liters, 4), source, onto):
        if sid is not None:
            return sid, True
    state = w.s
    existing = _puddle_at(w, rid, cell, liquid)
    if existing is not None:
        w.mut(existing)["liquid_l"] = round(num(state["entities"][existing].get("liquid_l")) + liters, 4)
        _size_puddle(w, existing)
        return existing, False
    info = props.liquid_props(liquid)
    record = {"label": "spilled " + str(liquid).replace("_", " "), "category": "spill", "portable": False,
              "liquid": liquid, "liquid_l": round(liters, 4), "floor_hazard": info.get("floor_hazard") or "wet",
              "pos": [cell[0], cell[1]], "relation": "on", "mass_kg": 0.0, "wetness": 1.0, "blocks_path": False,
              "_phys_spill": True}
    if props.floor_props(state, rid, list(cell)).get("absorbs"):
        record["on_carpet"] = True
    if w.reg is not None and w.reg.known("generic_item"):
        sid = w.spawn("generic_item", rid, eid=w.new_id("spill"), **record)
    else:
        sid = w.new_id("spill")
        record.update({"type": "generic_item", "is_a": [], "location": rid})
        w.create(sid, record)
    _size_puddle(w, sid)
    wet_cells = [list(c) for c in w.s["rooms"][rid].get("wet_cells") or []]
    if list(cell) not in wet_cells:
        w.room_m(rid)["wet_cells"] = wet_cells + [list(cell)]
    return sid, True


def _run_down(w, onto, liters, liquid, source, by, above=None):
    """Liquid lands on onto and runs down its support chain (each level catches what it can; a film under an item
    resting wet on a water-sensitive finish starts the wet-contact timer); the rest lands on the floor under the
    last level. Returns (floor spill id or None, new puddle?, first level kept something?)."""
    state = w.s
    entities = state["entities"]
    node, left, caught_first = onto, liters, False
    for level in range(MAX_CHAIN):
        if node not in entities or left <= 1e-9:
            break
        taken, mode = _catch(w, node, left, liquid, source)
        if level == 0:
            caught_first = taken > 0
        if mode == "film" and above in entities:
            thermal.wet_contact_check(w, above, node, by=by)
        left -= taken
        found = support.support_of(state, node)
        parent = found[0] if found is not None else entities[node].get("location")
        if parent not in entities:
            break
        above, node = node, parent
    if left < droplet_l():
        return None, False, caught_first
    rid, cell = _resolve_at(state, processes.floor_cell(state, node), source)
    sid, new = _floor_puddle(w, rid, cell, liquid, left, source, onto)
    return sid, new, caught_first


def spill(w, liters, liquid="water", at=None, onto=None, source=None, by=None, cause="spill"):
    """The single spill entry point (5.7); returns the floor spill's id, or None when nothing reached the floor.

    Below 5 ml: droplets, nothing happens. onto an entity: it and the supports under it catch what they can (a holder
    pools, a soaking body saturates, a top takes a 1.5 mm film) and the rest lands on the floor under them. Otherwise
    on the floor at ``at`` = (room, cell) or a room (default: under source, else at the robot): a phys.spill_sink
    entity or a kernel puddle. cause labels the consequence ("overflow" logs phys.overflow, every other cause
    phys.spill); for "drip" and "leak", which arrive in 5 ml portions, only a portion that starts a new puddle
    counts. A counted spill adds phys_spills, and spills when robot-attributable."""
    liters = num(liters)
    if liters < droplet_l():
        return None
    state = w.s
    if onto in state["entities"]:
        sid, new, caught = _run_down(w, onto, liters, liquid, source, by, above=source)
    else:
        rid, cell = _resolve_at(state, at, source)
        (sid, new), caught = _floor_puddle(w, rid, cell, liquid, liters, source, onto), False
    counted = new or (caught and cause not in TRICKLE_CAUSES)
    if counted:
        who = blame(w, source if source in state["entities"] else onto, by)
        w.stat("phys_spills")
        if who == "robot":
            w.stat("spills")
        place = sid if sid in state["entities"] else onto
        kind = "phys.overflow" if cause == "overflow" else "phys.spill"
        named = source if source in state["entities"] else onto
        note = "{} {}".format(named, "overflowed" if cause == "overflow" else "spilled") if named else "liquid spilled"
        processes.consequence(w, kind, place, who, note, ids=[i for i in (sid, onto, source) if i],
                              room=processes.floor_cell(state, place)[0], liquid=liquid, liters=round(liters, 4),
                              source=source, onto=onto, cause=cause)
    return sid


def deposit_below(w, eid, liters, liquid, source=None, by=None, cause="spill"):
    """Liquid leaving eid (overflow, a tilted or tipped mouth, slosh, a crack, a drip) lands on what eid rests on and
    runs on down its support chain to the floor: spill(onto=that support), or a floor spill under eid when it stands
    on the floor or is held. Returns the floor spill's id, or None."""
    state = w.s
    found = support.support_of(state, eid)
    parent = found[0] if found is not None else state["entities"][eid].get("location")
    if parent in state["entities"]:
        return spill(w, liters, liquid, onto=parent, source=source or eid, by=by, cause=cause)
    return spill(w, liters, liquid, at=processes.floor_cell(state, eid), source=source or eid, by=by, cause=cause)


# ----- drips and leaks (processes "dripping" and "leaking") ---------------------------------------------------------

def drip_start(w, eid, l_per_h, until_l=None):
    """eid drips l_per_h litres per hour onto what is below it (5.7 drip law: a wet umbrella, a thawing ice pack, a
    watered plant pot). The water comes out of eid's own liquid, else (until_l given) from an outside budget such as
    meltwater, else out of its wetness; it stops when that runs out or after until_l litres. Shows dripping: true
    (unless a domain already set it: plumbing's faucet flag is never clobbered). False when there is nothing to drip."""
    values = {"l_per_h": round(num(l_per_h), 4)}
    if until_l is not None:
        values["left_l"] = round(max(0.0, num(until_l)), 4)
    return processes.start(w, "dripping", eid, **values)


def drip_stop(w, eid):
    return processes.stop(w, "dripping", eid)


def leak_start(w, eid, l_per_h=LEAK_L_PER_H):
    """A cracked or torn container loses its liquid at l_per_h onto what is below it until empty; shows leaking.
    False when it holds no free liquid."""
    return processes.start(w, "leaking", eid, l_per_h=round(num(l_per_h), 4))


def leak_stop(w, eid):
    return processes.stop(w, "leaking", eid)


def _held_water_l(state, eid):
    """(litres, capacity) a wet item can still shed above dampness: (wetness - 0.1) x its soak or film capacity."""
    rec = state["entities"][eid]
    cap = absorb_capacity_l(props.props(state, eid)) or top_area_m2(rec) * SURFACE_FILM_MM
    return max(0.0, num(rec.get("wetness")) - DAMP_WETNESS) * cap, cap


def _flow_begin(w, eid, values, flag_field, liquid_only):
    """Parameters of a drip or leak. Its water comes from one source, fixed at the start: the item's own liquid
    ("liquid"), else an outside budget when one is given ("budget": left_l), else the water it is wet with ("wet":
    the water above dampness becomes its budget, and its wetness falls as that is shed). A leak needs liquid.
    Declines (None) when the rate is not positive or there is nothing to shed."""
    if num(values.get("l_per_h")) <= 0:
        return None
    state = w.s
    rec = state["entities"][eid]
    mode = values.get("src")
    if mode is None:
        if free_liquid_l(rec) > 0:
            mode = "liquid"
        elif liquid_only:
            return None
        elif values.get("left_l") is not None:
            mode = "budget"
        elif _held_water_l(state, eid)[0] > 0:
            mode = "wet"
        else:
            return None
    owns_flag = values.get("flag")
    if owns_flag is None:
        owns_flag = not rec.get(flag_field)
    if owns_flag and not rec.get(flag_field):
        w.mut(eid)[flag_field] = True
    if rec.get("location") in state["entities"]:
        thermal.wet_contact_check(w, eid, rec["location"])      # dripping, it is wet on what it stands on from now
    merged = {"l_per_h": num(values["l_per_h"]), "src": mode, "buf_l": round(num(values.get("buf_l")), 6),
              "flag": bool(owns_flag)}
    if mode == "wet" and values.get("left_l") is None:
        held, cap = _held_water_l(state, eid)
        merged.update(left_l=round(held, 6), wet_l=round(held, 6), wet_cap_l=round(cap, 6),
                      wet0=num(rec.get("wetness")))
    elif values.get("left_l") is not None:
        merged["left_l"] = round(num(values["left_l"]), 6)
    for key in ("wet_l", "wet_cap_l", "wet0"):
        if key in values and key not in merged:
            merged[key] = values[key]
    return merged


def _flow_end(w, eid, kind, flag_field):
    p = processes.params(w.s, kind, eid)
    if p.get("flag") and w.s["entities"][eid].get(flag_field):
        w.mut(eid).pop(flag_field, None)


def _flow_tick(w, eid, dt, kind):
    """One substep of a drip or leak. rate x dt litres are released into a buffer (bounded by the budget, or by what
    the source still holds beyond the buffer); the buffer is deposited below in portions of at least 5 ml (smaller
    amounts would vanish as droplets), each rounded to the 4 decimals states store, the remainder carried, so the
    litres that leave the source are exactly the litres that arrive. A liquid source is drained by each portion as it
    goes. False once the source is exhausted (after depositing what is left in the buffer)."""
    state = w.s
    p = processes.params(state, kind, eid)
    rec = state["entities"][eid]
    mode = p.get("src")
    buf = num(p.get("buf_l"))
    release = num(p.get("l_per_h")) * dt / 3600.0
    left = finite(p.get("left_l"))
    if left is not None:
        release = min(release, left)
        left = max(0.0, left - release)
    if mode == "liquid":
        release = max(0.0, min(release, free_liquid_l(rec) - buf))
    buf += release
    if mode == "wet" and release > 0:
        shed = num(p.get("wet_l")) - left
        after = round(max(DAMP_WETNESS, num(p.get("wet0")) - shed / max(1e-9, num(p.get("wet_cap_l")))), 4)
        if after < num(rec.get("wetness")):
            w.mut(eid)["wetness"] = after
    if mode == "liquid":
        done = free_liquid_l(rec) - buf <= 1e-9
    else:
        done = release <= 0 or (left is not None and left <= 1e-9)
    portion = round(buf, 4)
    if portion > 0 and (buf >= droplet_l() or done):
        if mode == "liquid":
            portion = min(portion, round(free_liquid_l(rec), 4))
            _drain(w, eid, portion)
        deposit_below(w, eid, portion, rec.get("liquid") or "water", source=eid,
                      cause="drip" if kind == "dripping" else "leak")
        buf -= portion
    if done or eid not in state["entities"]:
        return False
    values = {"buf_l": round(buf, 9)}
    if left is not None:
        values["left_l"] = round(left, 9)
    processes.set_params(w, kind, eid, **values)
    return True


def drip_begin(w, eid, values):
    return _flow_begin(w, eid, values, "dripping", liquid_only=False)


def drip_tick(w, eid, dt):
    return _flow_tick(w, eid, dt, "dripping")


def drip_end(w, eid):
    _flow_end(w, eid, "dripping", "dripping")


def leak_begin(w, eid, values):
    return _flow_begin(w, eid, values, "leaking", liquid_only=True)


def leak_tick(w, eid, dt):
    return _flow_tick(w, eid, dt, "leaking")


def leak_end(w, eid):
    _flow_end(w, eid, "leaking", "leaking")


# ----- evaporation (cleaning stays the drying owner) ----------------------------------------------------------------

EVAP_MM_S = 0.2 / 3600.0        # still indoor air at 21 degC and 45 % RH dries about 0.2 mm of water film per hour


def evaporation_mm_s(room_temp_c=21.0, humidity=0.45, airflow=False, outdoor=False, wind_mps=0.0, sun=False):
    """Approximate free-water evaporation rate (mm/s) for floors and surfaces: cleaning's formula, now kernel-owned
    (cleaning re-exports it on adoption). +4 %/K, x(1.6 - 1.4 RH) clamped to [0.2, 1.6], x2.5 with airflow; outdoors
    x(1.5 + 0.3 min(wind, 8)) and x3 in sun."""
    rate = EVAP_MM_S * (1.0 + 0.04 * (room_temp_c - 21.0)) * min(1.6, max(0.2, 1.6 - 1.4 * humidity))
    if airflow:
        rate *= 2.5
    if outdoor:
        rate *= 1.5 + 0.3 * min(wind_mps, 8.0)
        if sun:
            rate *= 3.0
    return max(rate, 1e-6)


# ----- carry dynamics (5.8) -----------------------------------------------------------------------------------------

def carry_hand_z_m(state):
    """Height (m) items fall from when they slip off a carried load: the carry geometry's hand_z_m in
    phys/support.py (support.CARRY, the one place BODY-ENGINE switches to the body profile's carry.hand_z_m, so the
    compact and mobile-manipulator profiles drop from their own hands)."""
    return float(support.CARRY["hand_z_m"])


def carry_motion(w, travel):
    """Gait motion factor of a robot trip: SPEED_FACTOR[effective speed] x every phys.carry_motion hook's multiplier
    (navigation: stairs x1.8, thresholds x1.15, uneven outdoor ground x1.3). travel is the motion record
    (TIER1_INTEGRATION 1.4), whose "speed" is the effective speed."""
    speed = (travel or {}).get("speed") or w.s["robot"].get("speed_mode") or "normal"
    factor = SPEED_FACTOR.get(speed, 1.0)
    for mult in run_hooks("phys.carry_motion", w, travel):
        if isinstance(mult, (int, float)) and not isinstance(mult, bool) and mult > 0:
            factor *= float(mult)
    return factor


def riding_items(state, holder, cmap=None):
    """Items riding on a carried holder: what is on or in it, then recursively what is on those (breadth first,
    sorted at each level)."""
    cmap = support.children_map(state) if cmap is None else cmap
    entities = state["entities"]
    out = [c for c in cmap.get(holder, ()) if entities[c].get("relation", "on") in ("on", "in")]
    i = 0
    while i < len(out):
        out.extend(c for c in cmap.get(out[i], ()) if entities[c].get("relation", "on") == "on" and c not in out)
        i += 1
    return out


def _slosh(w, eid, motion, by):
    amount = slosh_spill_l(w.s["entities"][eid], motion)
    if amount > 0:
        liquid = w.s["entities"][eid].get("liquid") or "water"
        _drain(w, eid, amount)
        deposit_below(w, eid, amount, liquid, source=eid, by=by, cause="slosh")


def carry(w, travel, by="robot", cmap=None):
    """carry law (5.8), once per action phase in which the robot travelled holding items (travel: the step's motion
    record). For each held item H: its own liquid sloshes to the floor at the robot's cell; each item X riding on or
    in H sloshes onto H (a rimmed H pools it, otherwise it runs off to the floor); an upright, unsecured X resting on
    H tips when motion > 2.8 x min(w, d) / h (impact.tip_over), and a tipped X that can roll falls from carry height
    unless H has a rim. Returns the consequence records."""
    state = w.s
    entities = state["entities"]
    held = sorted(set(e for e in (state["robot"].get("hands") or {}).values() if e in entities))
    if not held or not travel:
        return []
    start = len(impact.consequences(w))
    motion = carry_motion(w, travel)
    robot = state["robot"]
    at = (robot.get("room"), tuple(robot.get("pos") or (0, 0)))
    cmap = support.children_map(state) if cmap is None else cmap
    for holder in held:
        _slosh(w, holder, motion, by)
        rimmed = (finite(props.props(state, holder).rim_m) or 0.0) > 0
        for item in riding_items(state, holder, cmap):
            if item not in entities:
                continue
            _slosh(w, item, motion, by)
            rec = entities[item]
            if rec.get("relation", "on") != "on" or rec.get("secured") or \
                    rec.get("orientation") not in (None, "upright") or motion <= topple_threshold(rec):
                continue
            impact.tip_over(w, item, "carry", by=by)
            if not rimmed and rec.get("location") == holder and props.props(state, item).rollable:
                impact.fall(w, item, height_m=carry_hand_z_m(state), cause="carry", at=at, by=by)
    return impact.consequences(w)[start:]


# ----- law entry points (registered by PHYS-INT through LAWS) -------------------------------------------------------

def law_overflow(w, eid, env=None, cmap=None):
    """overflow (20): a holder above its capacity spills the excess where it stands."""
    if eid not in w.s["entities"]:
        return []
    start = len(impact.consequences(w))
    overflow(w, eid)
    return impact.consequences(w)[start:]


def law_open_spill(w, eid, env=None, cmap=None):
    """open_spill (70): an open container on its side, upside down or tilted (its own tilt_deg plus its support's)
    loses what that orientation or tilt cannot hold onto what it stands on."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or free_liquid_l(rec) <= 0 or container_closed(rec):
        return []
    frac = props.props(state, eid).mouth_frac
    lost = max(tip_spill_l(rec, rec.get("orientation"), frac), tilt_spill_l(rec, tilt_of(state, eid), frac))
    if lost < 1e-6:
        return []
    start = len(impact.consequences(w))
    liquid = rec.get("liquid") or "water"
    _drain(w, eid, lost)
    tipped = rec.get("orientation") in SIDE_ORIENTATIONS + INVERTED_ORIENTATIONS
    deposit_below(w, eid, lost, liquid, source=eid, cause="tip" if tipped else "tilt")
    return impact.consequences(w)[start:]


LAWS = (("overflow", 20, law_overflow), ("open_spill", 70, law_open_spill))

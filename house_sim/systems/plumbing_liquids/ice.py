"""Ice and filtered water: ice cube trays, freezer ice makers, ice in drinks, filter pitchers (P-approx).

Ice cube tray (ice_cube_tray): cells 14 x cell_ml 28; filling sets the mean cell fill (cell_fill); above 100 % the
cells bridge and freeze into one slab (joined); set down tilted (on a lumpy bag) water runs to the low side and
spills over in the freezer. Freezing time ~ 20140 s x cell_ml^(2/3) / (-T_freezer) (~2.9 h at -18 C for 28 ml).
Releasing cubes: a tray straight from the freezer (colder than -10 C, out of the cold less than 90 s) releases ~70 %
with a normal twist, all with force=firm but the brittle plastic cracks; after ~1.5 min on the counter it releases
everything (the time out of the cold decides, not the bulk temperature, whose time constant the kernel's thermal_tau
law sets). Cubes dropped on the floor melt into a slippery puddle (~20-40 min).
Tier-1 physics (TIER1_PHYSICS 10.3): while this domain freezes a holder's water it owns the plateau
(thermal_owner "plumbing_liquids"); with the kernel's phase law active only ice trays freeze here and every other
container freezes by the kernel (ice_frac); the time constant of a holder with floating ice follows the thermal_tau law
(ice_tau_s); a tray set down tilted spills by the kernel's open_spill law once it is active.
Ice maker (freezer_ice_maker): on (switch) and settings.arm down; needs its water-line valve open, a freezer at
or below -10 C and a bin that is not full or obstructed; one harvest of 8 cubes (~115 g) every 90 min (~1.8 kg/day).
Ice in drinks: ice_g floats (displaces its own mass); melts at ~1e-4 g/s per g^(2/3) per C; latent heat cools the drink.
Adding ice without a scoop or tongs means the glass (chips the rim, glass shards in the bin) or a hand (bacteria).
Filter pitcher (filter_pitcher): fill goes into the top reservoir (reservoir_cap_l ~1.1 L; topping it up too soon
overflows), water passes the cartridge at ~0.12 L/min into the lower chamber (capacity_l). Hot water (> 30 C)
damages the cartridge; a new cartridge's first fills carry carbon fines when its label says to discard them.
"""
import math

from ...kernel.errors import ActionError, require
from ...kernel.phys import lawbook
from ...kernel.phys import thermal as PT
from ...kernel.plugins import after, hook
from . import common as C
from . import liquids as L
from . import seen as V

CUBE_G = 25.0
FREEZE_K = 20140.0


def is_ice_source(rec):
    if rec.get("ice_bin") or rec.get("ice_maker") or rec.get("ice_tray"):
        return True
    food = rec.get("food") or {}
    return food.get("kind") == "ice"


def ice_available(rec):
    if rec.get("ice_tray"):
        return C.num(rec.get("ice_g")) if C.num(rec.get("frozen_frac")) >= 1.0 or rec.get("released") else 0.0
    food = rec.get("food") or {}
    if food.get("kind") == "ice":
        return C.num(food.get("qty_g"))
    return C.num(rec.get("ice_g"))


def _take_ice(w, src, grams):
    rec = w.ent(src)
    food = rec.get("food") or {}
    m = w.mut(src)
    if food.get("kind") == "ice":
        m["food"]["qty_g"] = C.rnd(C.num(food.get("qty_g")) - grams, 1)
        m["mass_kg"] = C.rnd(max(0.02, C.num(rec.get("mass_kg")) - grams / 1000.0), 3)
    else:
        m["ice_g"] = C.rnd(C.num(rec.get("ice_g")) - grams, 1)
        if rec.get("ice_cubes") is not None:
            m["ice_cubes"] = max(0, int(C.num(rec.get("ice_cubes"))) - int(round(grams / CUBE_G)))


def add_ice(ctx, target, source):
    w = ctx.w
    trec = w.ent(target)
    srec = ctx.obj(source, touch=True)
    count = ctx.args.get("count") or 3
    grams = count * CUBE_G
    require(ice_available(srec) >= grams - 1e-6, "Not enough ice there" if V.realistic(ctx) else "Not enough ice there ({:.0f} g)".format(ice_available(srec)),
            "resource")
    require(srec.get("open", True) or not srec.get("openable"), "Open it first", "precondition")
    tool = ctx.args.get("tool")
    if tool:
        require(ctx.holding(tool), "Hold the scoop or tongs", "capability")
        trc = w.ent(tool)
        require(set(trc.get("capabilities") or []) & {"scoop", "serve", "grip_food", "tongs"} or trc.get("tool_kind") in ("ice_scoop", "tongs", "scoop"),
                "That is not an ice scoop or tongs", "capability")
    notes = []
    if not tool:
        if ctx.holding(target):
            notes.append("used the glass itself as the scoop")
            if trec.get("fragile") or trec.get("material") in ("glass", "crystal"):
                C.damage(w, target, "rim chipped scooping ice", stat=True, kind="chipped", severity=0.3)
                C.contaminate(w, source, "glass_shards", 1.0)
                w.stat("breakages")
                C.plog(w, "glass_scoop", glass=target, bin=source)
            C.contaminate(w, source, "bacteria", 0.2)
        else:
            notes.append("grabbed the cubes by hand")
            C.contaminate(w, source, "bacteria", 0.3)
            C.plog(w, "hand_in_ice", bin=source)
    _take_ice(w, source, grams)
    m = w.mut(target)
    m["ice_g"] = C.rnd(C.num(trec.get("ice_g")) + grams, 1)
    m["ice_cubes"] = int(C.num(trec.get("ice_cubes"))) + count
    if L.liters(trec) > 0:
        m["temperature_c"] = C.rnd(C.num(trec.get("temperature_c"), 21.0) - min(4.0, grams / max(1.0, L.liters(trec) * 1000.0) * 40.0), 2)
    cap = L.cap_l(trec)
    disp = L.displacement_l(w, target)
    have = L.liters(w.ent(target))
    if cap > 0 and have + disp > cap + 1e-6:
        parcel = L.take(w, target, min(have, have + disp - cap))
        from .transfer import spill_near
        spill_near(w, target, parcel, "ice_displacement")
        notes.append("it overflowed" if V.realistic(ctx) else "it overflowed ({:.0f} ml)".format(parcel["liters"] * 1000.0))
    ctx.data["seconds"] = 4.0 + count
    C.plog(w, "add_ice", target=target, source=source, count=count, tool=tool)
    return "Added {} cubes to {}{}".format(count, target, ": " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------------------
# trays

WARM_RELEASE_S = 90.0        # a frozen tray on the counter: its thin plastic and the ice at the walls warm within
                             # a minute or two, then the cubes come out with a twist (the bulk stays below 0 C longer)


def fresh_from_cold(rec):
    """A frozen tray straight from the freezer: colder than -10 C and out of the cold for less than WARM_RELEASE_S (the
    cells' walls have not warmed, the plastic is brittle). The time out of the cold (_warm_s, kept by freeze_step)
    decides once the tray is out, not the lumped body temperature, whose time constant the kernel's thermal_tau law
    sets (17 min for a full tray under the lumped law, 4.5 min under the legacy formula)."""
    if C.num(rec.get("temperature_c"), -18.0) >= -10.0:
        return False
    return C.num(rec.get("_warm_s")) < WARM_RELEASE_S


def tray_capacity_l(rec):
    return C.num(rec.get("cells"), 14) * C.num(rec.get("cell_ml"), 28.0) / 1000.0


def tray_fill(ctx, tray, parcel, fixture):
    w = ctx.w
    rec = w.ent(tray)
    require(C.num(rec.get("ice_g")) < 1.0, "Empty the old cubes out first", "precondition")
    cap = tray_capacity_l(rec)
    have = L.liters(rec)
    total = have + parcel["liters"]
    frac = total / cap if cap > 0 else 0.0
    notes = []
    over = None
    if frac > 1.0:
        keep = min(total, cap * 1.08)
        spill = total - keep
        notes.append("the cells overflowed into each other")
        m = w.mut(tray)
        m["joined"] = True
        if spill > 0:
            scale = (parcel["liters"] - spill) / parcel["liters"] if parcel["liters"] > 0 else 0.0
            over = dict(parcel, liters=spill, mix={k: v * (1 - scale) for k, v in parcel["mix"].items()})
            parcel = dict(parcel, liters=parcel["liters"] - spill, mix={k: v * scale for k, v in parcel["mix"].items()})
        frac = keep / cap
    L.add(w, tray, parcel, limit=cap * 1.1)
    m = w.mut(tray)
    m["cell_fill"] = C.rnd(frac, 3)
    m["frozen_frac"] = 0.0
    if over and over["liters"] > 0:
        frec = w.ent(fixture)
        from . import fixtures as F
        if L.is_basin(frec) and C.is_mine(frec):
            F.basin_inflow(w, fixture, over)
        else:
            from .transfer import spill_near
            spill_near(w, tray, over, "tray_overflow")
    return notes


def empty_tray(ctx, tray, dest, force):
    w = ctx.w
    rec = w.ent(tray)
    require(ctx.holding(tray), "Hold the tray over where the cubes should go", "precondition")
    frozen = C.num(rec.get("frozen_frac")) >= 1.0 and C.num(rec.get("ice_g")) > 0
    if not frozen:
        require(L.liters(rec) > 0, "The tray is empty", "precondition")
        from .transfer import do_pour
        return do_pour(ctx, tray, dest, amount=L.liters(rec), rate="normal", verb="empty")
    cold = fresh_from_cold(rec)
    ice_g = C.num(rec.get("ice_g"))
    notes = []
    if rec.get("joined"):
        frac = 0.5 if force != "firm" else 1.0
        notes.append("the cubes came out as one slab")
    elif cold:
        frac = {"gentle": 0.4, "normal": 0.7, "firm": 1.0}[force]
    else:
        frac = {"gentle": 0.8, "normal": 1.0, "firm": 1.0}[force]
    if force == "firm" and cold and not rec.get("cracked"):
        m = w.mut(tray)
        m["cracked"] = True
        C.damage(w, tray, "brittle frozen tray cracked when twisted hard", kind="cracked", severity=0.6)
        notes.append("the cold plastic cracked")
    released = ice_g * frac
    m = w.mut(tray)
    m["ice_g"] = C.rnd(ice_g - released, 1)
    m["ice_cubes"] = int(round(C.num(m["ice_g"]) / CUBE_G))
    if C.num(m["ice_g"]) < 1.0:
        m["ice_g"] = 0.0
        m["frozen_frac"] = 0.0
        m["cell_fill"] = 0.0
        m["joined"] = False
    if released < 1.0:
        return "Nothing came out"
    if frac < 1.0:
        notes.append("some of the cubes stuck (let the tray warm a minute)" if V.realistic(ctx) else
                     "{:.0f}% of the cubes stuck (let the tray warm a minute)".format((1 - frac) * 100))
    if dest in w.s["rooms"] or (dest in w.s["entities"] and w.ent(dest).get("category") == "floor"):
        rid = dest if dest in w.s["rooms"] else w.ent(dest).get("location")
        drop_cubes(w, rid, list(w.robot.get("pos") or [0, 0]), released)
        notes.append("cubes skittered across the floor")
    else:
        drec = ctx.obj(dest, touch=False)
        if drec.get("ice_bin") or drec.get("ice_maker") or drec.get("container") or L.holds_liquid(drec) or drec.get("food_safe"):
            dm = w.mut(dest)
            dm["ice_g"] = C.rnd(C.num(drec.get("ice_g")) + released, 1)
            dm["ice_cubes"] = int(C.num(drec.get("ice_cubes"))) + int(round(released / CUBE_G))
            if drec.get("ice_capacity_g") and C.num(dm["ice_g"]) > C.num(drec.get("ice_capacity_g")):
                spillover = C.num(dm["ice_g"]) - C.num(drec.get("ice_capacity_g"))
                dm["ice_g"] = C.num(drec.get("ice_capacity_g"))
                drop_cubes(w, C.room_of(w, dest), C.cell_of(w, dest), spillover)
                notes.append("the bin overflowed")
        else:
            drop_cubes(w, C.room_of(w, dest), C.cell_of(w, dest), released)
            notes.append("the cubes bounced off onto the floor")
    C.plog(w, "tray_emptied", tray=tray, into=dest, grams=C.rnd(released, 1))
    ctx.data["seconds"] = 8.0
    if V.realistic(ctx):
        cubes = int(round(released / CUBE_G))
        return "Twisted {} over {}: {}{}".format(tray, dest, "{} cubes released".format(cubes) if cubes <= 12 and not rec.get("joined") else "the ice came out",
                                                 ": " + "; ".join(notes) if notes else "")
    return "Twisted {} over {}: {:.0f} g of ice released{}".format(tray, dest, released, ": " + "; ".join(notes) if notes else "")


def drop_cubes(w, rid, cell, grams):
    if rid is None or grams <= 0:
        return None
    props = {"label": "ice cubes on the floor", "category": "ice_cubes", "ice_g": C.rnd(grams, 1), "floor_hazard": "slip",
             "portable": True, "mass_kg": C.rnd(grams / 1000.0, 3), "size_m": [0.3, 0.3, 0.03], "temperature_c": -5.0,
             "volume_l": C.rnd(grams / 900.0, 3), "dropped_s": C.now(w)}
    eid = w.spawn("generic_item", rid, **props)
    m = w.mut(eid)
    m["pos"] = [int(cell[0]), int(cell[1])]
    m["relation"] = "on"
    C.plog(w, "ice_dropped", room=rid, grams=C.rnd(grams, 1))
    return eid


@after("place", order=158)
def tray_placed(ctx, result):
    """A tray set down tilted (on another item rather than a shelf) spills from its low side when full."""
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "")
    if not rec or not rec.get("ice_tray"):
        return
    dest = rec.get("location")
    drec = w.s["entities"].get(dest or "") or {}
    if drec.get("portable"):
        flat = bool(drec.get("carry_level") or drec.get("flat")) and not drec.get("soft")
    else:
        flat = True      # a freezer shelf, counter or table
    tilt = 0.0 if flat else 8.0
    m = w.mut(eid)
    m["tilt_deg"] = tilt
    fill = C.num(rec.get("cell_fill"))
    if tilt > 3.0 and lawbook.active("open_spill") and L.liters(rec) > 0:
        # the kernel's open_spill law runs the water out of the tilted tray in this step's settle (tilt_deg is what it
        # reads); leaks.tray_follows_its_water keeps cell_fill and frozen_spill_l in step with it
        w.mut(eid)["uneven"] = True
        ctx.note("the tray sits tilted")
        return
    if tilt > 3.0 and fill > 0.75 and L.liters(rec) > 0 and (rec.get("open", True) or not rec.get("openable")):
        lost = min(L.liters(rec), (fill - 0.75) * tray_capacity_l(rec) + tilt / 100.0 * tray_capacity_l(rec))
        parcel = L.take(w, eid, lost)
        m = w.mut(eid)
        m["cell_fill"] = C.rnd(L.liters(w.ent(eid)) / tray_capacity_l(rec), 3)
        m["uneven"] = True
        target = drec.get("location") if drec.get("portable") else dest
        holder = target if target in w.s["entities"] else dest
        if holder in w.s["entities"]:
            hm = w.mut(holder)
            hm["frozen_spill_l"] = C.rnd(C.num(hm.get("frozen_spill_l")) + parcel["liters"], 4)
            C.wet(w, dest, 0.6)
        C.plog(w, "tray_tilted_spill", tray=eid, liters=C.rnd(parcel["liters"], 4))
        w.stat("spills")
        ctx.note("the tray sits tilted and water ran out of the low cells")


# ----------------------------------------------------------------------------------
# filter pitchers

def pitcher_receive(ctx, pitcher, parcel):
    w = ctx.w
    rec = w.ent(pitcher)
    if rec.get("openable") and not rec.get("open"):
        raise ActionError("Lift the pitcher's lid first", "precondition")
    cap = C.num(rec.get("reservoir_cap_l"), 1.1)
    have = C.num(rec.get("reservoir_l"))
    into = min(parcel["liters"], max(0.0, cap - have))
    m = w.mut(pitcher)
    m["reservoir_l"] = C.rnd(have + into, 4)
    m["reservoir_temp_c"] = C.rnd(C.num(parcel.get("temp_c"), 12.0), 1)
    if C.num(parcel.get("temp_c"), 12.0) > 30.0:
        cart = _cartridge(w, pitcher)
        if cart and not w.ent(cart).get("hot_damaged"):
            cm = w.mut(cart)
            cm["hot_damaged"] = True
            C.damage(w, cart, "filter media damaged by hot water", kind="heat", severity=0.7)
    if parcel.get("flags"):
        m.setdefault("_reservoir_flags", {}).update(parcel["flags"])
    if parcel["liters"] - into > 1e-5:
        over = dict(parcel, liters=parcel["liters"] - into, mix={"water": parcel["liters"] - into})
        C.plog(w, "reservoir_overflow", pitcher=pitcher, liters=C.rnd(over["liters"], 3))
        return over
    return None


def _cartridge(w, pitcher):
    slots = w.ent(pitcher).get("slots") or {}
    info = slots.get("cartridge") or {}
    item = info.get("item")
    return item if item in w.s["entities"] else None


def pitcher_step(w, eid, dt):
    rec = w.ent(eid)
    res = C.num(rec.get("reservoir_l"))
    if res <= 1e-6:
        return
    cart = _cartridge(w, eid)
    rate = C.num(rec.get("filter_rate_lpm"), 0.12) if cart else 1.5
    room_left = max(0.0, L.cap_l(rec) - L.liters(rec))
    move = min(res, rate * dt / 60.0, room_left)
    if move <= 1e-6:
        return
    flags = dict(rec.get("_reservoir_flags") or {})
    name = "water"
    if cart:
        crec = w.ent(cart)
        if not crec.get("hot_damaged"):
            name = "filtered_water"
        cm = w.mut(cart)
        before = C.num(crec.get("used_l"))
        cm["used_l"] = C.rnd(before + move, 3)
        fills = int(C.num(crec.get("discard_fills")))
        if fills > 0 and before < fills * C.num(rec.get("reservoir_cap_l"), 1.1):
            flags["carbon_fines"] = True
        rated = C.num(crec.get("rated_l"), 150.0)
        if cm["used_l"] >= rated and (rec.get("settings") or {}).get("indicator") != "replace":
            w.mut(eid)["settings"]["indicator"] = "replace"
    m = w.mut(eid)
    m["reservoir_l"] = C.rnd(res - move, 4)
    if m["reservoir_l"] <= 1e-6:
        m.pop("_reservoir_flags", None)
    parcel = L.make_parcel(name, move, temp_c=C.num(rec.get("reservoir_temp_c"), 12.0), flags=flags, source=eid)
    L.add(w, eid, parcel)


@hook("part_installed")
def cartridge_in(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if rec.get("reservoir_l") is not None and slot == "cartridge":
        pm = w.mut(part_id)
        pm.setdefault("used_l", 0.0)
        pm["installed_s"] = C.now(w)


# ----------------------------------------------------------------------------------
# freezing, melting, ice makers (called by the master system)

def ambient_c(w, eid):
    from ...kernel import physics
    temp, _ = physics.ambient(w.s, eid)
    return temp


WATERS = ("water", "filtered_water", "ice_water", "distilled_water", "tap_water")
OWNER = "plumbing_liquids"


def domain_freezes(rec):
    """Whether this domain freezes the water in a holder: an ice tray always (its cells, cubes and slabs are this
    domain's model); any other holder only while the kernel's phase law is deferred. Once the law is active the kernel
    freezes it (ice_frac in liquid_l, the latent-heat plateau, the sealed-container burst rule; TIER1_PHYSICS 5.9)."""
    return bool(rec.get("ice_tray")) or not lawbook.active("phase")


def _own_plateau(w, eid, on):
    """While this domain freezes a holder's water it owns the temperature (thermal_owner, CONVENTIONS 6): the plateau
    at 0 C is its, and the kernel thermal system and phase law leave the holder alone. Never takes over another
    owner's entity."""
    rec = w.ent(eid)
    if on and not rec.get("thermal_owner") and not rec.get("fixed_temperature"):
        w.mut(eid)["thermal_owner"] = OWNER
    elif not on and rec.get("thermal_owner") == OWNER:
        w.mut(eid).pop("thermal_owner", None)


def ice_tau_s(rec):
    """Thermal time constant of a holder with floating ice: the kernel's own formula with the ice's mass counted
    (the kernel knows liquid_l, not this domain's ice_g): the lumped m c / (h A) law (phys.thermal.tau_s, ice at its
    own heat capacity) once the thermal_tau law is active, the legacy 300 sqrt(mass + liquid + ice) + 60 s before."""
    ice_kg = C.num(rec.get("ice_g")) / 1000.0
    if lawbook.active("thermal_tau"):
        view = dict(rec)
        view.pop("thermal_tau_s", None)
        total = L.liters(rec) + ice_kg
        view["liquid_l"] = total
        view["ice_frac"] = ice_kg / total if total > 0 else 0.0
        if not view.get("liquid"):
            view["liquid"] = "water"
        return PT.tau_s(view)
    return 300.0 * math.sqrt(max(0.01, C.num(rec.get("mass_kg"), 0.2) + L.liters(rec) + ice_kg)) + 60.0


def _ice_tau(w, eid):
    """Keep this domain's time-constant override in step with the ice it holds (marked _tau_by_plumbing, removed when
    the ice is gone); an explicit thermal_tau_s of the type or a setup (an insulated tumbler) is never replaced."""
    rec = w.ent(eid)
    ours = bool(rec.get("_tau_by_plumbing"))
    if C.num(rec.get("ice_g")) > 0:
        if rec.get("thermal_tau_s") and not ours:
            return
        tau = ice_tau_s(rec)
        if not ours or abs(C.num(rec.get("thermal_tau_s")) - tau) > 5.0:
            m = w.mut(eid)
            m["thermal_tau_s"] = C.rnd(tau, 1)
            m["_tau_by_plumbing"] = True
    elif ours:
        m = w.mut(eid)
        m.pop("thermal_tau_s", None)
        m.pop("_tau_by_plumbing", None)


def freeze_time_s(rec, have, delta_k):
    """Seconds to freeze (or thaw) a holder's water through at |T_amb - 0 C| = delta_k: about 20140 s x
    cell_ml^(2/3) / dK (2.9 h for 28 ml cubes at -18 C)."""
    cell_ml = C.num(rec.get("cell_ml"), 0.0) or max(28.0, have * 1000.0)
    return FREEZE_K * cell_ml ** (2.0 / 3.0) / max(1.0, delta_k)


def _ice_tau_current(rec):
    """True when _ice_tau would write nothing. Pure."""
    ours = bool(rec.get("_tau_by_plumbing"))
    if C.num(rec.get("ice_g")) > 0:
        if rec.get("thermal_tau_s") and not ours:
            return True
        return ours and abs(C.num(rec.get("thermal_tau_s")) - ice_tau_s(rec)) <= 5.0
    return not ours


def freeze_idle(w, eid):
    """True when freeze_step would change nothing: nothing freezing or thawing in the holder, no plateau to hand
    back, no ice melting (ice above 0 C ambient in a holder that is itself above 0 C melts), the ice time constant
    current. Pure (the water system's idle probe)."""
    rec = w.s["entities"][eid]
    have = L.liters(rec)
    ice = C.num(rec.get("ice_g"))
    if have <= 0 and ice <= 0 and rec.get("thermal_owner") != OWNER and not rec.get("_tau_by_plumbing") and \
            "_warm_s" not in rec:
        return True
    amb = ambient_c(w, eid)
    warm = rec.get("_warm_s")
    if rec.get("ice_tray") and (warm is not None and (ice <= 0 or amb <= -1.0) or
                                ice > 0 and amb > 0.5 and C.num(warm) < WARM_RELEASE_S):
        return False
    freezing = have > 0 and amb <= -1.0 and rec.get("liquid") in WATERS and domain_freezes(rec)
    if freezing or rec.get("thermal_owner") == OWNER:
        return False
    if have > 0 and 0.0 < C.num(rec.get("frozen_frac")) < 1.0 and amb > 0.5:
        return False
    if ice > 0 and amb > 0.5 and not rec.get("ice_maker") and C.num(rec.get("temperature_c"), amb) > 0.0:
        return False
    return _ice_tau_current(rec)


def freeze_step(w, eid, dt):
    """Water in a container below 0 C turns to ice; ice above 0 C melts. With the kernel's phase law active only ice
    trays freeze here (domain_freezes)."""
    rec = w.ent(eid)
    amb = ambient_c(w, eid)
    have = L.liters(rec)
    freezing = have > 0 and amb <= -1.0 and rec.get("liquid") in WATERS and domain_freezes(rec)
    if not freezing and rec.get("thermal_owner") == OWNER:
        _own_plateau(w, eid, False)
    if freezing:
        _own_plateau(w, eid, True)
        frac = C.num(rec.get("frozen_frac")) + dt / freeze_time_s(rec, have, -amb)
        m = w.mut(eid)
        if frac >= 1.0:
            m["frozen_frac"] = 1.0
            m["ice_g"] = C.rnd(C.num(rec.get("ice_g")) + have * 997.0, 1)
            if rec.get("ice_tray"):
                filled = int(C.num(rec.get("cells"), 14)) if C.num(rec.get("cell_fill")) >= 0.6 else int(round(have * 1000.0 / C.num(rec.get("cell_ml"), 28.0)))
                m["ice_cubes"] = 1 if rec.get("joined") else filled
            L.write_state(w, eid, {}, extra={"liquid_l": 0.0})
            m = w.mut(eid)
            m["temperature_c"] = min(C.num(m.get("temperature_c"), 0.0), 0.0)
            _own_plateau(w, eid, False)          # frozen through: the kernel cools the ice on toward its ambient
            C.plog(w, "frozen", target=eid, grams=m["ice_g"])
            if rec.get("material") == "glass" and have / max(0.01, L.cap_l(rec)) > 0.9 and not rec.get("ice_tray"):
                C.damage(w, eid, "glass cracked by expanding ice", broken=True, kind="cracked")
        else:
            m["frozen_frac"] = C.rnd(frac, 4)
            m["temperature_c"] = 0.0
        _ice_tau(w, eid)
        return
    frozen = C.num(rec.get("frozen_frac"))
    if have > 0 and 0.0 < frozen < 1.0 and amb > 0.5:
        # taken out of the cold before it froze through: the ice skin in the cells thaws back to water
        left = frozen - dt / freeze_time_s(rec, have, amb)
        w.mut(eid)["frozen_frac"] = C.rnd(left, 4) if left > 1e-4 else 0.0
    ice = C.num(rec.get("ice_g"))
    warm = rec.get("_warm_s")
    if rec.get("ice_tray") and warm is not None and (ice <= 0 or amb <= -1.0):
        w.mut(eid).pop("_warm_s", None)              # back in the cold, or empty: the clock starts again
    elif rec.get("ice_tray") and ice > 0 and amb > 0.5 and C.num(warm) < WARM_RELEASE_S:
        w.mut(eid)["_warm_s"] = C.rnd(min(WARM_RELEASE_S, C.num(warm) + dt), 1)
    if ice > 0 and amb > 0.5 and not rec.get("ice_maker"):
        temp = C.num(rec.get("temperature_c"), amb)
        if temp <= 0.0:
            _ice_tau(w, eid)
            return      # still warming to 0 C (the kernel's thermal model supplies the heat)
        # latent plateau: sensible heat above 0 C (liquid + container) melts ice at 334 J/g, limited by how fast heat
        # crosses the cube surface (stirred liquid ~3x faster than still air); while ice remains the bulk stays <= 4 C
        heat_cap = have * 1000.0 * 4.186 + C.num(rec.get("mass_kg"), 0.2) * 1000.0 * 0.9 + ice * 2.1
        contact = have > 0.01
        rate = 9.6e-5 * ice ** (2.0 / 3.0) * max(0.5, max(temp * 3.0, amb) if contact else amb)
        melt = min(ice, temp * heat_cap / 334.0 + rate * dt * (0.0 if contact else 1.0), rate * dt if contact else ice)
        melt = max(0.0, melt)
        new_t = temp - melt * 334.0 / max(1.0, heat_cap)
        m = w.mut(eid)
        m["ice_g"] = C.rnd(ice - melt, 2)
        if m["ice_g"] < 0.5:
            m["ice_g"] = 0.0
            m["ice_cubes"] = 0
            if rec.get("ice_tray"):
                m["frozen_frac"] = 0.0
        elif rec.get("ice_cubes"):
            m["ice_cubes"] = max(1, int(round(m["ice_g"] / CUBE_G)))
        if rec.get("category") == "ice_cubes" and rec.get("location") in w.s["rooms"]:
            m["temperature_c"] = C.rnd(max(0.0, new_t), 2)
            C.add_floor_water(w, rec["location"], rec.get("pos") or [0, 0], "water", melt / 1000.0, source=eid)
            if m["ice_g"] <= 0.0:
                w.destroy(eid)
            return
        if L.holds_liquid(rec) or have > 0:
            over = L.add(w, eid, L.make_parcel("water", melt / 1000.0, temp_c=0.0, source=eid))
            # meltwater joins at 0 C: re-mix the bulk temperature
            liquid_g = L.liters(w.ent(eid)) * 1000.0
            mixed = (new_t * have * 1000.0) / max(1.0, liquid_g) if liquid_g > 0 else new_t
            if C.num(w.ent(eid).get("ice_g")) > 0:
                mixed = min(mixed, 4.0)
            w.mut(eid)["temperature_c"] = C.rnd(max(0.0, mixed), 2)
            if over:
                from .transfer import spill_near
                spill_near(w, eid, over, "melt")
        else:
            m["temperature_c"] = C.rnd(max(0.0, min(new_t, 4.0) if C.num(m.get("ice_g")) > 0 else new_t), 2)
    _ice_tau(w, eid)


def ice_maker_reasons(w, eid, cache=None):
    """Why an ice maker is not making ice (empty list: it is making ice)."""
    rec = w.ent(eid)
    from . import supply as S
    settings = rec.get("settings") or {}
    reasons = []
    if not rec.get("on"):
        reasons.append("switched off")
    if settings.get("arm", "down") != "down":
        reasons.append("bail arm up")
    if S.line_factor(w, eid, "cold", cache) <= 0:
        reasons.append("no water to the ice maker")
    if ambient_c(w, eid) > -10.0:
        reasons.append("freezer not cold enough")
    if C.num(rec.get("ice_g")) >= C.num(rec.get("ice_capacity_g"), 3000.0):
        reasons.append("bin full")
    blockers = [c for c in w.children(eid) if w.ent(c).get("relation") == "in"]
    if blockers:
        reasons.append("arm blocked by " + blockers[0])
    return reasons


def ice_maker_status(w, eid):
    reasons = ice_maker_reasons(w, eid)
    return "making ice" if not reasons else ", ".join(reasons)


def ice_maker_step(w, eid, dt, cache=None):
    """Runs in the slow (period) system: dt may be minutes; harvests every cycle_s (90 min) of making ice."""
    rec = w.ent(eid)
    reasons = ice_maker_reasons(w, eid, cache)
    status = "making ice" if not reasons else ", ".join(reasons)
    if rec.get("status") != status or rec.get("_plumbing_liquids_status") != status:
        m = w.mut(eid)
        m["status"] = status                        # the maker's own indicator text (observable)
        m["_plumbing_liquids_status"] = status      # canonical derived field (CONVENTIONS 5)
    if reasons:
        return
    m = w.mut(eid)
    m["_cycle_s"] = C.rnd(C.num(rec.get("_cycle_s")) + dt, 1)
    cycle = max(60.0, C.num(rec.get("cycle_s"), 5400.0))
    while m["_cycle_s"] >= cycle:
        m["_cycle_s"] = C.rnd(m["_cycle_s"] - cycle, 1)
        if C.num(m.get("ice_g")) >= C.num(rec.get("ice_capacity_g"), 3000.0):
            m["_cycle_s"] = 0.0
            break
        harvest = C.num(rec.get("harvest_g"), 115.0)
        m["ice_g"] = C.rnd(min(C.num(rec.get("ice_capacity_g"), 3000.0), C.num(m.get("ice_g")) + harvest), 1)
        m["ice_cubes"] = int(round(m["ice_g"] / CUBE_G))
        C.use_water(w, harvest / 1000.0)
        C.plog(w, "ice_harvest", maker=eid, grams=harvest)

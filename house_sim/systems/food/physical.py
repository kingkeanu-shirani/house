"""Food in the kernel's physics layer (kernel v3.0, docs/PHYSICS.md; TIER1_INTEGRATION 6 "food" and the adoption
protocol of TIER1_PHYSICS 10.3).

Food keeps the models that are its own subject: the temperature, freezing and thawing of food (food.thermal; food
entities carry fixed_temperature, the kernel's older alias of thermal_owner, so the kernel thermal system and its
phase law leave them alone), spoilage, ripening, bruising and crushing as quality. What is plain physics goes
through the kernel:

* phys.props (`food_props`): a sealed retail package is physically its packaging (data.PACKAGING: a jar of honey
  drops and breaks like a glass jar, a can only dents, a carton crumples); whole round produce is a sphere (it rolls);
  ripeness and firmness set how soft produce is and how far it can fall before it bruises; bagged bread and snack bags
  crush under a light load (data.CRUSH_KG). Nothing is written to the entities: generated houses stay byte-identical.
* phys.broke (`_broke`): an egg broken in its shell becomes a raw-egg spill (the kernel's liquid, pooling where it
  lands) and its eggshell; a package that breaks spills what flows out of it and leaves the rest unsafe.
* phys.fell (`_fell`): the kernel's bruise becomes the food's bruise; unwrapped food landing on a floor picks up
  bacteria (as food set down on the floor does).
* phys.wetted (`_wetted`): a liquid the kernel brings onto food, or onto something food touches, leaves what it carries
  (`liquid_contamination`: a toxic or corrosive liquid or a neat cleaner a chemical, dirty water and body fluids germs);
  `soak_from_vessel` does the same for food lying in a vessel's liquid (independent review G10).
* `spill`: liquid food that leaves its vessel is a kernel spill (physics.spill: the liquids.json liquid, a puddle or a
  wet surface, the spill statistics and their attribution); the residue it leaves stays a food soil mark.
* `freeze_burst`: a sealed rigid container of liquid food bursts (or a can bulges) by the kernel's freeze-burst rule
  (phys.thermal), with the food's own fill and ice fraction.
"""
from ...kernel import physics
from ...kernel.phys import impact, liquids, processes, props as phys_props, thermal
from ...kernel.plugins import hook
from . import common as C
from . import data as D
from . import model as M

PROBE = "_food_packaging_probe"     # marks the packaging stand-in record (the hook skips it)
# Phys fields a package takes from its packaging material; mass, size, shape and heat capacity stay the item's own
PACKAGING_FIELDS = ("cls", "brittle", "break_mode", "drop_tol_m", "crush_kg", "crushable", "rigid", "hard",
                    "wet_strength", "mu", "mu_wet", "waterproof", "softness", "wsens", "combustible", "heat_limit_c",
                    "melt_c", "shock_k")
# record keys that set a Phys field explicitly (phys_props.RECORD_FIELDS by field), or that the kernel derives it from
# (a declared crush limit makes a thing crushable; a soft or soft-sided thing is not rigid): an explicit value always
# wins over the packaging's
EXPLICIT = {"drop_tol_m": ("drop_tolerance_m",), "crush_kg": ("crush_kg", "max_stack_load_kg", "max_load_kg"),
            "crushable": ("crushable", "crush_kg", "max_stack_load_kg", "max_load_kg"), "brittle": ("brittle",),
            "rigid": ("rigid", "soft", "softness", "soft_sided"), "waterproof": ("waterproof",),
            "combustible": ("combustible",), "softness": ("softness", "soft"), "mu": ("friction_mu",),
            "heat_limit_c": ("heat_limit_c", "surface_heat_limit_c", "heat_rating_c", "heat_resistant"),
            "melt_c": ("melt_c",), "shock_k": ("thermal_shock_c",), "shape": ("shape",)}
# the record keys a packaging probe copies: only what the kernel's type part reads besides the material, so the probe
# gives the packaging's class values, the same for every record of a type (it is memoised; a per-record override such
# as a spec's drop_tolerance_m stays that record's own through EXPLICIT)
PROBE_KEYS = ("type", "is_a", "category", "tags", "tempered", "portable")
SPHERE_ASPECT = 1.6         # whole round produce whose longest side is at most 1.6x its shortest rests as a sphere
_PACK_MEMO = {}
LIQUID_PHASES = ("liquid",)


def packaging_material(rec):
    """The materials.json alias of a retail package's packaging, or None for anything that is not a package."""
    kind = rec.get("package")
    if not kind or M.food(rec) is None:
        return None
    return D.PACKAGING_BY_TYPE.get(rec.get("type")) or D.PACKAGING.get(kind if isinstance(kind, str) else "", "film")


def _packaging(rec, material):
    """The class-level Phys values of a package made of `material` (nominal; memoised by the keys the probe reads)."""
    get = rec.get
    key = (material, get("type"), tuple(get("is_a") or ()), get("category"), tuple(get("tags") or ()),
           get("tempered") is True, bool(get("portable")))
    found = _PACK_MEMO.get(key)
    if found is None:
        probe = {k: rec[k] for k in PROBE_KEYS if k in rec}
        probe.update({"material": material, PROBE: True})
        p = phys_props.props_of(probe)
        found = {field: getattr(p, field) for field in PACKAGING_FIELDS}
        if p.waterproof:
            found["absorb_l"] = 0.0
        _PACK_MEMO[key] = found
    return found


def _explicit(record, field):
    return any(key in record for key in EXPLICIT.get(field, ()))


def _firmness_factor(f):
    """1.0 for firm food, down to data.BRUISE_DROP_MIN for the softest. Food that ripens follows its ripeness (unripe
    fruit is firm, ripe fruit yields, overripe fruit is very soft: 1 - 0.8 x ripeness); other food its firmness."""
    if f.get("ripens") and f.get("ripeness") is not None:
        factor = 1.0 - 0.8 * min(1.0, max(0.0, float(f["ripeness"])))
    elif f.get("firmness") is not None:
        factor = float(f["firmness"])
    else:
        return None
    return max(D.BRUISE_DROP_MIN, min(1.0, factor))


def food_props(record, phys):
    """phys.props hook (TIER1_PHYSICS 7.3): the food refinements of a resolved Phys (see the module docstring)."""
    f = M.food(record) or {}
    update = {}
    material = packaging_material(record)
    if material is not None:
        for field, value in _packaging(record, material).items():
            if not _explicit(record, field):
                update[field] = value
    elif f:
        whole = f.get("cut", "whole") == "whole" and f.get("phase", "solid") == "solid"
        size = [float(v) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:3]]
        if whole and int(f.get("count", 1) or 1) <= 1 and f.get("round") and min(size) > 0 \
                and max(size) <= SPHERE_ASPECT * min(size) and not _explicit(record, "shape"):
            update["shape"] = "sphere"
            update["rollable"] = True           # (the kernel derives rollable from the shape before the hooks run)
        factor = _firmness_factor(f) if whole else None
        if factor is not None and phys.get("break_mode") == "bruise":
            if not _explicit(record, "drop_tol_m") and phys.get("drop_tol_m") not in (None, float("inf")):
                update["drop_tol_m"] = round(float(phys["drop_tol_m"]) * factor, 4)
            if not _explicit(record, "softness"):
                update["softness"] = round(max(float(phys.get("softness") or 0.0), 1.0 - factor), 3)
    crush = D.CRUSH_KG.get(record.get("type"))
    if crush is not None and not _explicit(record, "crush_kg"):
        update["crush_kg"] = float(crush)
        update["crushable"] = True
    return update or None


food_props.applies = lambda record: not record.get(PROBE) and (isinstance(record.get("food"), dict) or
                                                               record.get("type") in D.CRUSH_KG)
hook("phys.props")(food_props)


# ----------------------------------------------------------------------
# spills

def spill_liquid(rec_or_kind):
    """liquids.json name for a liquid food (a kind name or a food record)."""
    if isinstance(rec_or_kind, dict):
        f = M.food(rec_or_kind) or {}
        kind, cat = f.get("kind") or "", f.get("cat") or ""
    else:
        kind, cat = str(rec_or_kind or ""), ""
    if kind in D.SPILL_LIQUID:
        return D.SPILL_LIQUID[kind]
    if physics.liquid_props(kind).get("name") != "default":
        return kind
    return D.SPILL_LIQUID_BY_CAT.get(cat, "soup")


def spill(w, liters, liquid, onto=None, at=None, source=None, by=None):
    """A kernel spill of liquid food (phys.liquids.spill): onto a surface it wets it (the rest runs to the floor), on
    a floor it pools; counts phys_spills, and spills when the robot caused it. Returns the floor spill id or None."""
    if onto is not None and onto not in w.s["entities"]:
        onto = None
    return liquids.spill(w, liters, liquid, at=at, onto=onto, source=source if source in w.s["entities"] else None, by=by)


def _landing(state, eid):
    """(onto, at) for liquid leaving an entity where it lies: the surface or vessel it rests on, else its floor cell."""
    rec = state["entities"][eid]
    loc = rec.get("location")
    if loc in state["entities"]:
        return loc, None
    if loc in state["rooms"] and rec.get("pos"):
        return None, (loc, rec["pos"])
    return None, None


# ----------------------------------------------------------------------
# breakage and falls

def _egg_in_shell(rec):
    f = M.food(rec) or {}
    return f.get("cat") == "egg" and f.get("phase", "solid") == "solid" and "shell" in (f.get("parts") or []) \
        and f.get("cut", "whole") == "whole"


@hook("phys.broke")
def _broke(w, eid, info):
    """An egg or a retail package the kernel broke (a fall, a crush, a set-down, a freeze)."""
    rec = w.s["entities"].get(eid)
    if rec is None or M.food(rec) is None:
        return
    if _egg_in_shell(rec):
        _egg_broke(w, eid, info)
    elif rec.get("package"):
        _package_broke(w, eid, info)


def _egg_broke(w, eid, info):
    """A raw egg cracked open by the kernel: its white and yolk run out (a raw_egg kernel spill where it lies) and what
    stays is the eggshell, smeared with raw egg. A hard-boiled egg only cracks its shell. The entity is kept (the
    kernel finishes the fall with it): it becomes the broken eggshell."""
    state = w.s
    rec = state["entities"][eid]
    f = rec["food"]
    if f.get("cooked") or (f.get("egg_doneness") in ("set", "overcooked_rubbery")):
        m = w.mut(eid)
        m["food"]["cracked"] = True
        m["_seen_inside"] = True
        return
    units = max(1, int(f.get("count", 1) or 1))
    total = M.qty(rec)
    shell_g = round(total * float(C.part_info(rec, "shell").get("frac", 0.11)), 2)
    content_g = max(0.0, total - shell_g)
    onto, at = _landing(state, eid)
    spill(w, content_g / 1035.0, "raw_egg", onto=onto, at=at, source=eid, by=info.get("by"))
    m = w.mut(eid)
    mf = m["food"]
    mf.update(kind="eggshell", cat="scraps", phase="pieces", qty_g=shell_g, cut="cracked", count=None, unit_g=None,
              parts=[], composition={"eggshell": shell_g}, rte=False, cracked=True, broken_open=units)
    m["label"] = "broken egg"
    m["waste_stream"] = "compost"
    m["_seen_inside"] = True
    _look(m)
    m["mass_kg"] = round(M.tare_kg(m) + shell_g / 1000.0, 4)
    m["volume_l"] = round(shell_g / 1000.0, 4)
    contam = dict(m.get("contamination") or {})
    contam["raw_meat"] = max(float(contam.get("raw_meat", 0.0)), 0.3)      # Salmonella on and in raw egg
    m["contamination"] = contam
    M.ensure_secret(m, "contamination")
    if onto is not None and onto in state["entities"] and M.food(state["entities"][onto]) is None:
        M.add_contam(w, onto, "raw_meat", 0.3)


VISCOUS_CP = 1000.0         # liquids this thick (honey, syrup) ooze: half of it stays among the pieces


def _package_broke(w, eid, info):
    """A retail package that broke: what flows runs out as a kernel spill (a thin liquid all of it; honey, syrup or a
    paste half); whatever stays is unsafe to eat (glass or sharp fragments in it, or simply an open, damaged pack)."""
    state = w.s
    rec = state["entities"][eid]
    f = rec["food"]
    phase = f.get("phase")
    liquid = spill_liquid(rec)
    out_frac = 0.0
    if phase in LIQUID_PHASES:
        out_frac = 0.5 if float(physics.liquid_props(liquid).get("visc_cp") or 1.0) >= VISCOUS_CP else 1.0
    elif phase == "paste":
        out_frac = 0.5
    grams = M.qty(rec) * out_frac
    if grams > 0:
        onto, at = _landing(state, eid)
        spill(w, grams / M.density(rec) / 1000.0, liquid, onto=onto, at=at, source=eid, by=info.get("by"))
        M.set_qty(w, eid, M.qty(rec) - grams)
    m = w.mut(eid)
    m["open"] = True
    m["opened"] = True
    m.setdefault("opened_on", M.iso(M.today(state)))
    m["_unsafe"] = True
    m["_unsafe_reason"] = "broken_package"


def _look(m):
    """Refresh a changed food record's appearance at once (the derived-truth refresh comes later in the step)."""
    if m.get("appearance") is not None and not (m.get("package") and not m.get("open") and not m.get("transparent")):
        m["appearance"] = M.appearance(m)


@hook("phys.fell")
def _fell(w, eid, info):
    """A food item fell (the kernel's fall: a drop, a scatter, a knock): the kernel's bruise is the food's bruise, and
    unwrapped food landing on a floor picks up bacteria."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or M.food(rec) is None:
        return
    bruise = float((rec.get("damage") or {}).get("bruised", 0.0)) if isinstance(rec.get("damage"), dict) else 0.0
    f = rec["food"]
    if bruise > float(f.get("bruise", 0.0)) + 1e-9:
        m = w.mut(eid)
        m["food"]["bruise"] = round(min(1.0, bruise), 3)
        _look(m)
        rec = state["entities"][eid]
    if info.get("landed_on") is None and info.get("cell") is not None and not rec.get("package") \
            and rec.get("location") in state["rooms"]:
        M.add_contam(w, eid, "bacteria", 0.2)


# ----------------------------------------------------------------------
# liquids reaching food

DIRTY_LIQUIDS = ("dirty_water", "mop_water", "grey_water", "gray_water", "sewage", "toilet_water", "flood_water",
                 "mud_water")
DIRTY_RESIDUES = ("dirt", "mud")            # liquids.json residues of dirty water (germs)
BODY_RESIDUES = ("urine", "biohazard")      # body fluids (more germs)
CLEANER_LIQUIDS = ("dish_soap", "laundry_detergent")    # neat cleaning products (soapy water leaves nothing)
TRACE_FRAC = 0.005          # a kind of liquid under 0.5 % of a vessel's mix leaves no mark of its own


def liquid_contamination(liquid):
    """{tag: level} a liquid leaves on food, or on a thing food touches, that it reaches (food's contamination tags),
    from the kernel's liquids.json row: a toxic or corrosive liquid (bleach, antifreeze, fuel, motor oil) a chemical
    0.5, a neat cleaning product 0.3, dirty water germs 0.5 and body fluids 0.8. Food liquids, clean water and soapy
    water leave nothing (the same levels as plumbing_liquids' rule for what its pours leave)."""
    name = str(liquid or "water").strip().lower()
    row = physics.liquid_props(name)
    canon = row.get("name")
    out = {}
    if row.get("toxic") or row.get("corrosive"):
        out["chemical"] = 0.5
    elif canon in CLEANER_LIQUIDS:
        out["chemical"] = 0.3
    if row.get("residue") in BODY_RESIDUES:
        out["bacteria"] = 0.8
    elif name in DIRTY_LIQUIDS or canon in DIRTY_LIQUIDS or row.get("residue") in DIRTY_RESIDUES:
        out["bacteria"] = 0.5
    return out


@hook("phys.wetted")
def _wetted(w, eid, liters, liquid, source):
    """A liquid the kernel brings onto something (a spill running down, a drip or a leak from above, a tipped bottle, a
    film seeping from a wet item): food takes what the liquid carries, and anything food is served, cooked or stored
    in its germs (marks_for; independent review G10: bleach water over a loaf left it only wet). A sealed waterproof
    package keeps it off the food inside."""
    rec = w.s["entities"].get(eid)
    if rec is None or float(liters or 0.0) <= 0.0 or not M.food_contact(rec):
        return
    marks = marks_for(rec, liquid_contamination(liquid))
    if not marks or M.sealed_off(rec):
        return
    for key, level in sorted(marks.items()):
        M.raise_contam(w, eid, key, level)


def marks_for(rec, marks):
    """The marks a liquid leaves on rec: everything on food; on a plate, board or pan only germs. A chemical on a
    food-contact surface is what sanitizing it is (a bleach solution on a cutting board), so it is not counted against
    the food that later touches it; dirty water on it is."""
    if M.food(rec) is not None:
        return marks
    return {k: v for k, v in marks.items() if k == "bacteria"}


def held_liquid_marks(holder):
    """{tag: level} the liquid a holder holds would leave on food: its liquid (by name: "mop_water" is dirty), every kind
    in its mix (`_mix`) above a trace, and any cleaning product dissolved in it (`cl_chem`, the plumbing and cleaning
    solution model: bleach by its own row, any other cleaner a chemical 0.3). {} for a holder with nothing in it."""
    mix = holder.get("_mix") if isinstance(holder.get("_mix"), dict) else {}
    total = sum(float(v or 0.0) for v in mix.values())
    kinds = {str(k) for k, v in mix.items() if total > 0 and float(v or 0.0) >= TRACE_FRAC * total}
    kinds.add(str(holder.get("liquid") or "water"))
    out = {}
    for kind in sorted(kinds):
        for key, level in liquid_contamination(kind).items():
            out[key] = max(out.get(key, 0.0), level)
    chem = holder.get("cl_chem") if isinstance(holder.get("cl_chem"), dict) else {}
    for product, ml in sorted(chem.items()):
        if float(ml or 0.0) > 0.0:
            level = liquid_contamination(product).get("chemical", 0.3)
            out["chemical"] = max(out.get("chemical", 0.0), level)
    return out


def soak_from_vessel(w, eid, rec, vessel):
    """Food lying in a vessel's liquid takes what that liquid carries (a bottle of bleach tipped into a salad bowl, mop
    water poured over the bread in a bin; held_liquid_marks). Returns True when the food's contamination rose."""
    if not vessel or float(vessel.get("liquid_l") or 0.0) <= 0.0:
        return False
    marks = held_liquid_marks(vessel)          # (clean water, the usual case, leaves nothing: no packaging lookup)
    if not marks or M.sealed_off(rec):
        return False
    rose = False
    for key, level in sorted(marks.items()):
        rose = M.raise_contam(w, eid, key, level) or rose
    return rose


# ----------------------------------------------------------------------
# freezing in sealed containers

def freeze_burst(w, vessel, fill, ice, by=None):
    """The kernel's freeze-burst rule (phys.thermal.freeze_burst_check, TIER1_PHYSICS 5.9) for a container of liquid
    food, whose liquid and ice the food model holds (the container carries no liquid_l / ice_frac of its own): sealed
    and rigid, its ice needs 0.09 x fill x ice of the volume (+0.03 x ice behind a narrow glass neck) and bursts it
    when that exceeds the headspace; glass and ceramics burst, a metal can bulges and bursts beyond 2 %, plastic
    bulges. Returns "burst", "bulged" or None."""
    state = w.s
    rec = state["entities"].get(vessel)
    if rec is None or rec.get("broken") or fill <= 0 or ice <= 0 or not liquids.container_closed(rec):
        return None
    p = phys_props.props(state, vessel)
    if not p.rigid:
        return None
    fill, ice = min(1.0, float(fill)), min(1.0, max(0.0, float(ice)))
    need = thermal.ICE_EXPANSION * fill * ice
    if p.cls in thermal.SHATTER_CLASSES and liquids.mouth_frac(rec, p.mouth_frac) < liquids.WIDE_MOUTH:
        need += thermal.NARROW_NECK_MARGIN * ice
    excess = need - (1.0 - fill)
    if excess <= 1e-9:
        return None
    who = processes.blame(w, vessel, by)
    room = processes.floor_cell(state, vessel)[0]
    if p.cls in thermal.SHATTER_CLASSES or (p.cls in thermal.METAL_CLASSES and excess > thermal.CAN_BURST_EXCESS):
        processes.consequence(w, "phys.burst", vessel, who, "{} burst".format(vessel), ids=[vessel], outcome="burst",
                              excess=round(excess, 4), room=room)
        impact.break_entity(w, vessel, "freeze_burst", by=by)
        return "burst"
    severity = round(min(1.0, excess / thermal.CAN_BURST_EXCESS), 4)
    current = thermal.damage_level(rec, "bulged")
    if severity > current + 1e-9:
        impact.damage(w, vessel, "bulged", severity, detail="frozen contents expanded", by=by)
        if current <= 0:
            processes.consequence(w, "phys.burst", vessel, who, "{} bulged".format(vessel), ids=[vessel],
                                  outcome="bulged", excess=round(excess, 4), room=room)
    return "bulged"

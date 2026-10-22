"""Textiles in the kernel's physics layer (kernel v3.0, docs/PHYSICS.md; TIER1_INTEGRATION 6 "textiles_laundry" and the
adoption protocol of TIER1_PHYSICS 10.3).

This domain keeps the models that are its own subject: washing, drying and souring (it is the drying owner of textile
moisture, CONVENTIONS 5), creasing, stains, care-label damage, the mildew timers and the pin model of laundry on a line.
What is plain physics goes through the kernel:

* phys.props (`fabric_props`): a textile's physical properties follow its fibre (`fabric`), not the generic
  `material: "fabric"` most garments carry (which the kernel reads as cotton). Absorption and waterproofing come from
  the fibre's kernel material class (FIBRE_MATERIAL: cotton 2.5 L per kg, wool 3, synthetics 0.6, microfibre 4; a coated
  or waxed fabric is the kernel's coated fabric, 0.05 and waterproof); friction is the class's, except that silk, satin
  and nylon are slippery (SLIPPERY_MU 0.3: smooth filament fabrics on smooth plastic or metal run 0.2-0.3, cotton 0.5);
  the heat limit as a support (what a hot body resting on it may be: the kernel's contact_heat law) is 120 C or the
  fibre's scorch temperature when lower (model.FABRICS scorch_c: a moving iron glides at temperatures a resting one
  scorches at), and a thermoplastic fibre's melting point is the temperature at which an iron glazes or melts it
  (scorch_c of the fibres model.FABRICS marks `melts`: polyester 180 C, nylon 170 C, acrylic 160 C, fleece 150 C, PEVA
  90 C); cotton, linen, wool, silk and the other natural fibres scorch and have no melting point. An explicit record value always wins. Nothing is written to
  the entities. The hook leaves `mass_kg` alone: Phys.mass_kg is the kernel's own (dry) mass, which
  liquids.absorb_capacity_l divides the absorption by to tell a soaking body from one that sheds a film (a wet mass
  there would flip leather from soaking to film once it is half wet).
* `wet_mass` / `water_held_l` / `soak_into`: what a wet textile weighs and holds -- dry mass + wetness x the litres it
  holds when saturated (liquids.absorb_capacity_l; water at 1 kg per litre; wetness is this domain's saturation
  fraction, the kernel's convention too: liquids.wet) -- for this domain's loads (a rack's lopsided tip, hooks pulling
  out while the hang law is deferred, a hanger bending under a wet coat), and the kernel's own wetting (liquids.wet: the
  water a towel under a dripping filter takes up). The kernel's statics (support.own_mass: lifts, the hang, crush and
  overload laws) count only the record's mass and the liquid a container holds, not the water a soaking body holds
  (kernel request).
* Melting: `iron` melts a thermoplastic fabric hotter than its kernel melting point (`melt_point`) and scorches the
  others above their scorch point. The kernel's melt law (a body heated past its melting point, a process) never
  applies to ironing: the soleplate does not heat the garment's own temperature; nothing here duplicates the law.
* Contact heat: `heel_rest` claims (phys.contact_heat) the contact of an iron or steamer standing on its heel rest: only
  a face-down soleplate touches what it stands on. While the contact_heat law is deferred this domain marks a textile
  under a face-down hot iron itself (care._iron_ticker); once it is active the kernel does, and this domain keeps the
  burning smell and the ignition after ten minutes.
* Hanging: bars, hooks and racks carry their ratings in the type data (max_load_kg, hidden), which the kernel's hang
  law reads as hang ratings; this domain's own pull-out (fold.textile_placed) and a rack's overload (dry.rack_tip_check)
  apply only while the hang law is deferred. A rack's lopsided tip stays this domain's (it is not an overload).
* Friction: a garment slips off its hanger when the kernel's dry friction between them is below HANGER_SLIP_MU
  (`slips_off_hanger`), unless clips or strap notches hold it; strapless garments slip from any plain hanger.
* Transport: textiles_laundry.transport hands the drops and jostles of a packed bag to the kernel (physics.transport)
  and keeps the creasing, mud and damp of the textiles.
* Rain: once the kernel's rain law is active and rain falls by its measure (weather rain_mm_h > 0: `kernel_rain`), the
  kernel wets laundry left out in it (environment.rain_wet); a weather record that only says condition "rain" (no
  rate, as a scenario event may set it) is rain to this domain but not to the kernel, so this domain keeps wetting
  then. Exposure is the kernel's (`out_in_weather`: environment.exposed, so a closed bin or a cover shelters laundry
  outdoors), and an outdoor room the scenario marks `covered` shelters textiles from the kernel's rain too
  (`covered_room`, phys.exposed).
* Moisture: the kernel's wetting (liquids.wet, environment.rain_wet) raises a soaking body's `moisture` with its
  wetness; this domain dries textiles, so it keeps `moisture` no higher than the wetter of surface and seams
  (dry._tick_textile).

Ready to flip (TIER1_PHYSICS 10.3), checked with lawbook.forced on the domain tests and specs: melt, hang,
contact_heat. See docs/domains/textiles_laundry.md "Physics".
"""
import math

from ...kernel.phys import environment as phys_env, lawbook, liquids, props as phys_props
from ...kernel.phys.forces import friction
from ...kernel.predicates import root_room
from ...kernel.plugins import hook
from . import model as M

PROBE = "_textile_fibre_probe"      # marks the stand-in record a fibre's class values are read from
# fibre (model.FABRICS) -> a materials.json alias of its kernel class (None: keep the record's own class)
FIBRE_MATERIAL = {
    "cotton": "cotton", "linen": "linen", "denim": "denim", "canvas": "canvas", "terry": "terry", "flannel": "flannel",
    "velvet": "velvet", "corduroy": "cotton", "rayon": "cotton", "jute": "jute", "coir": "jute", "rubber_backed": "cotton",
    "wool": "wool", "cashmere": "wool", "down": "wool",
    "silk": "silk", "satin": "satin", "lace": "polyester", "polyester": "polyester", "nylon": "nylon",
    "acrylic": "polyester", "spandex_blend": "spandex", "fleece": "fleece", "microfiber": "microfiber",
    "mesh_synthetic": "mesh_synthetic", "polypropylene": "polyester", "insulated": "polyester",
    "peva": "oilcloth", "vinyl": "oilcloth", "pu_membrane": "oilcloth",
    "memory_foam": "memory_foam", "foam": "foam",
    "leather": "leather", "suede": "suede", "patent_leather": "patent_leather", "rubber": "rubber",
}
COATED_MATERIAL = "oilcloth"        # coated or waxed fabric: the kernel's coated_fabric class (waterproof face)
SLIPPERY_MU = 0.3                   # silk, satin and nylon against finished wood (class mu is per finished wood)
RESTING_SCORCH_C = 120.0            # a hot iron or pan left resting on fabric browns it from about here (a face-down iron
                                    # scorches cotton within a minute at 150-200 C, which a moving iron irons at)
HANGER_SLIP_MU = 0.32               # below this dry friction a garment works its way off a plain hanger
WATER_KG_L = 1.0
# record keys that set a Phys field explicitly (props.RECORD_FIELDS by field): an explicit value always wins
EXPLICIT = {"absorb_l": ("absorb_l_per_kg",), "waterproof": ("waterproof",), "mu": ("friction_mu",),
            "melt_c": ("melt_c",), "heat_limit_c": ("heat_limit_c", "surface_heat_limit_c", "heat_rating_c")}
_FIBRE_MEMO = {}


def law(name):
    """True while the kernel applies the physics law (laws.json state or a test's lawbook.forced)."""
    return lawbook.active(name)


def _fibre_material(record):
    if record.get("coated") or record.get("waxed"):
        return COATED_MATERIAL
    return FIBRE_MATERIAL.get(M.fabric_name(record))


def _fibre(material):
    """Class-level values of a fibre: absorption per kg of dry mass, waterproofing, friction (memoised)."""
    found = _FIBRE_MEMO.get(material)
    if found is None:
        p = phys_props.props_of({"material": material, "mass_kg": 1.0, "size_m": [1.0, 1.0, 1.0], PROBE: True})
        found = _FIBRE_MEMO[material] = {"abs_per_kg": float(p.absorb_l), "waterproof": bool(p.waterproof),
                                         "mu": float(p.mu)}
    return found


def _explicit(record, field):
    return any(key in record for key in EXPLICIT.get(field, ()))


def _volume_l(phys):
    w, d, h = (float(v) for v in (phys.get("size_m") or [0.1, 0.1, 0.1])[:3])
    shape = {"cylinder": math.pi / 4.0, "sphere": math.pi / 6.0}.get(phys.get("shape"), 1.0)
    return w * d * h * shape * 1000.0


def fabric_props(record, phys):
    """phys.props hook (TIER1_PHYSICS 7.3): a textile's fibre-dependent properties (module doc)."""
    update = {}
    fab = M.fabric(record)
    material = _fibre_material(record)
    dry = max(0.0, float(record.get("mass_kg", 0.0) or 0.0))
    if material is not None:
        fibre = _fibre(material)
        if not _explicit(record, "absorb_l"):
            # litres held: per kg of dry mass, at most the pores of its outline (the kernel's own rule, props_of)
            update["absorb_l"] = round(min(fibre["abs_per_kg"] * dry, 0.9 * _volume_l(phys)), 6)
        if not _explicit(record, "waterproof"):
            update["waterproof"] = fibre["waterproof"]
        if not _explicit(record, "mu"):
            update["mu"] = SLIPPERY_MU if fab.get("slippery") else fibre["mu"]
    if not _explicit(record, "heat_limit_c") and fab.get("scorch_c"):
        # a hot body resting on fabric marks it well below the temperature a moving iron glides over it at
        update["heat_limit_c"] = min(float(fab["scorch_c"]), RESTING_SCORCH_C)
    if not _explicit(record, "melt_c"):
        update["melt_c"] = float(fab["scorch_c"]) if fab.get("melts") and fab.get("scorch_c") else float("inf")
    return update or None


fabric_props.applies = lambda record: bool(record.get("textile")) and not record.get(PROBE)
hook("phys.props")(fabric_props)


# ----------------------------------------------------------------------
# what a wet textile weighs and holds

def _wet(phys, record):
    wet = min(1.0, max(0.0, float(record.get("wetness", 0.0) or 0.0)))
    return float(phys.mass_kg) + wet * liquids.absorb_capacity_l(phys) * WATER_KG_L


def wet_mass(state, eid):
    """Mass (kg) of a textile with the water it holds: its kernel (dry) mass + wetness x its saturated capacity."""
    return round(_wet(phys_props.props(state, eid), state["entities"][eid]), 6)


def record_wet_mass(record):
    """wet_mass of a bare record (the mass is never varied per instance, so no state is needed)."""
    return round(_wet(phys_props.props_of(record), record), 6)


def water_held_l(state, eid):
    """Litres of water a textile holds now (wetness x its saturated capacity; 0 for waterproof ones)."""
    p = phys_props.props(state, eid)
    cap = liquids.absorb_capacity_l(p)
    return round(min(1.0, max(0.0, float(state["entities"][eid].get("wetness", 0.0) or 0.0))) * cap, 6)


def soak_into(w, eid, litres, liquid="water", source=None):
    """A textile takes up liquid that reaches it, through the kernel (liquids.wet: up to saturation, wetness and the
    phys.wetted hooks). Returns the litres it kept; this domain then owns the new wetness (model.note_wetness)."""
    kept = liquids.wet(w, eid, litres, liquid, source=source)
    if kept > 0:
        M.note_wetness(w, eid)
    return kept


# ----------------------------------------------------------------------
# rain

def kernel_rain(w):
    """True while the kernel wets what stands in the rain: its rain law is active and rain falls by its measure
    (weather rain_mm_h > 0). A weather record saying only condition "rain" (no rate) is rain to this domain alone."""
    return law("rain") and phys_env.rain_mm_h(w.s.get("weather")) > 0.0


def out_in_weather(state, eid):
    """The kernel's exposure rule (environment.exposed): outdoors, not roofed, not inside a closed container or under a
    cover or table, and no phys.exposed hook shelters it."""
    return phys_env.exposed(state, eid)


@hook("phys.exposed")
def covered_room(state, eid):
    """A textile in an outdoor room the scenario marks `covered` (a covered drying area) is out of the rain."""
    rec = state["entities"].get(eid) or {}
    if not M.is_fabric_candidate(rec):
        return None
    room = state["rooms"].get(root_room(state, eid)) or {}
    return False if room.get("outdoor") and room.get("covered") else None


# ----------------------------------------------------------------------
# heat

def melt_point(state, eid):
    """The kernel melting point (degC) of a thermoplastic textile, or None for a fibre that scorches instead."""
    value = phys_props.props(state, eid).melt_c
    return float(value) if value is not None and math.isfinite(value) else None


FACE_DOWN = ("flat", "upside_down")


def soleplate_down(rec):
    """True when an iron or steamer lies on its soleplate rather than standing on its heel rest (orientation
    "upright") or lying on its side (the plastic housing touches, not the plate)."""
    return rec.get("orientation") in FACE_DOWN


@hook("phys.contact_heat")
def heel_rest(w, hot, support, temp_c, severity):
    """The kernel's contact_heat law (TIER1_PHYSICS 5.9) sees a hot iron as a hot body on whatever it stands on. An iron
    or garment steamer standing upright rests on its cool heel rest: this domain claims that contact (no mark). A
    face-down soleplate is left to the kernel."""
    rec = w.s["entities"].get(hot) or {}
    return rec.get("category") in ("iron", "garment_steamer") and not soleplate_down(rec)


# ----------------------------------------------------------------------
# friction

def hanger_friction(rec, hrec):
    """Dry static friction between a garment and a hanger (kernel forces.friction, the fibre's and the hanger's
    material friction through the phys.props hooks)."""
    return friction(rec, hrec, wet=False)


def slips_off_hanger(rec, hrec, method=None):
    """True when a garment works its way off a hanger over time: a strapless one off any plain hanger, any other where
    the dry friction is below HANGER_SLIP_MU (silk, satin and nylon on plastic, wire or lacquered wood). A non-slip
    (flocked, padded, rubber-coated) hanger, clips and strap notches (a strappy garment hung by its straps) hold it."""
    method = method or rec.get("hang_method")
    if hrec.get("non_slip") or hrec.get("clips") or (rec.get("strappy") and hrec.get("notches") and method == "straps"):
        return False
    if rec.get("strapless") or rec.get("slips_on_hangers"):
        return True
    return hanger_friction(rec, hrec) < HANGER_SLIP_MU

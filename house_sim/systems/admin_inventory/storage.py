"""Storage physics and time effects for stored goods (fidelity G / P-approx):

* shelf and surface load ratings (kernel v3.0 overload law): a shelf or surface the robot loads past its
  rating gives way (kernel physics: the shelf's load drops to the shelf below or the floor, breakage by drop
  tolerance); the ratings are the kernel's (record shelf_load_kg / max_load_kg, else supports.json);
* heat-sensitive goods (candles, vinyl, photos, thermal receipts, batteries) are damaged above
  their max_safe_temp_c (attics in summer, hot cars, window sills); melting is the kernel's melt law;
* damp: cardboard and paper standing on a damp basement/garage/shed floor wick up water and
  mildew; wet umbrellas, bags and luggage closed or shut away mildew, opened ones dry;
* pens and markers left uncapped dry out;
* weather on parcels and mail left outdoors (rain soaks cardboard -- the kernel's rain model -- and
  damages contents; wind-driven rain reaches a covered porch), cold packs in meal-kit boxes melt,
  unattended porch parcels can be stolen after a grace period.

Physics adoption (TIER1_PHYSICS 10.3): every rule the kernel's laws duplicate runs through the kernel's own functions
and steps aside once that law is active (lawbook.active): overload (_load_check), melt and rain (exposure_tick).
"""
import math

from ...kernel import physics
from ...kernel.phys import environment as phys_env, impact as phys_impact, lawbook, props as phys_props
from ...kernel.phys import support as phys_support, thermal as phys_thermal
from ...kernel.plugins import after
from ..core import add_soil
from .common import doorbell_alert, household, indexed, visual_event

STREET_ROOMS = ("porch", "front_yard", "sidewalk", "driveway")
COVERED_ROOMS = ("porch", "balcony")
PAPERISH = ("paper", "cardboard")
PORCH_WIND_MPS = 7.2        # local gust (kernel wind-at-object model) that drives rain under a porch roof: a 10 m
                            # mean of 8 m/s with the default 1.5 gust factor and ground exposure (0.6 of the gust)


def _nominal(record, field):
    """A kernel rating of a record without per-instance variation (phys.props.props_of without a state: the nominal
    value a printed rating or a furnisher's planning uses): record fields first (shelf_load_kg, max_load_kg), then
    supports.json. None when unrated."""
    return phys_support.finite(getattr(phys_props.props_of(record), field))


def shelf_rating(record):
    """Rated load (kg) of one shelf of a shelved container: the kernel's nominal shelf_rating_kg (None when unrated).
    The overload law itself applies about +-15 % per-instance variation (docs/PHYSICS.md 1)."""
    return _nominal(record, "shelf_rating_kg") if record.get("shelf_heights") else None


def surface_rating(record):
    """Rated load (kg) of a fixed surface or the bottom of a fixed container: the kernel's nominal rating_kg
    (max_load_kg, else supports.json; None when unrated)."""
    return _nominal(record, "rating_kg")


def _record_mass(rec):
    """Own mass plus liquid at the kernel's density (liquid_density, else liquids.json, else water)."""
    return phys_support.own_mass(rec)


def children_map(entities):
    """{parent id: [child ids]} for a raw entities dict (one pass; deterministic order)."""
    kids = {}
    for eid in sorted(entities):
        kids.setdefault(entities[eid].get("location"), []).append(eid)
    return kids


def subtree_mass_in(entities, eid, kids=None):
    """Mass of an entity and everything in/on it, from a raw entities dict (config or baseline)."""
    kids = children_map(entities) if kids is None else kids
    total, stack, seen = 0.0, [eid], set()
    while stack:
        item = stack.pop()
        if item in seen or item not in entities:
            continue
        seen.add(item)
        total += _record_mass(entities[item])
        stack.extend(kids.get(item, ()))
    return total


def _shelf_of(rec):
    shelf = rec.get("shelf")
    return 0 if shelf is None else shelf


def shelf_load(entities, container, shelf=0, kids=None):
    """Load (kg) standing on one shelf of a shelved container (`relation` "in", that shelf index)."""
    kids = children_map(entities) if kids is None else kids
    return sum(subtree_mass_in(entities, c, kids) for c in kids.get(container, ())
               if entities[c].get("relation") == "in" and _shelf_of(entities[c]) == (shelf or 0))


def surface_load(entities, surface, kids=None):
    """Load (kg) standing on a surface (`relation` "on")."""
    kids = children_map(entities) if kids is None else kids
    return sum(subtree_mass_in(entities, c, kids) for c in kids.get(surface, ()) if entities[c].get("relation") == "on")


def shelf_headroom(config, container, shelf=0):
    """Public helper for furnishers and setup code: kg that one shelf of `container` (a shelved cabinet/closet/
    shelving unit) can still take before its nominal rating, or a fixed surface (shelf=None on a surface) before its
    nominal rating. Returns None when the furniture is unrated. A wrapper over the kernel's ratings (supports.json,
    record shelf_load_kg / max_load_kg), kept for furnishers (TIER1_INTEGRATION 6). Use it before Placer.inside/on with
    heavy stock:
        if (shelf_headroom(p.c, cabinet, 0) or 1e9) >= mass: p.inside(cabinet, ..., shelf=0)"""
    entities = config["entities"]
    rec = entities.get(container) or {}
    if rec.get("shelf_heights"):
        rating = shelf_rating(rec)
        return None if rating is None else round(rating - shelf_load(entities, container, shelf or 0), 3)
    rating = surface_rating(rec)
    return None if rating is None else round(rating - surface_load(entities, container), 3)


def _supports_above(state, eid):
    """The supports a placed item loads: its location chain up to the furniture (what it rests on or in, and what
    that rests on), nearest first."""
    entities = state["entities"]
    out, seen = [], {eid}
    loc = (entities.get(eid) or {}).get("location")
    while loc in entities and loc not in seen:
        out.append(loc)
        seen.add(loc)
        loc = entities[loc].get("location")
    return out


@after("place", order=90)
def _load_check(ctx, result):
    """overload while the law is deferred (TIER1_PHYSICS 5.2, 10.3): the supports the placement loaded go through the
    kernel's own overload law (phys.impact.overload_law): a shelf, surface or fixed container over max(its rating with
    per-instance variation, the load it carried at episode start + 0.5 kg) gives way -- load a generator or a setup
    put there is never charged to the robot. The shelf's items drop to the next intact shelf below or the floor,
    breakage and floor damage follow the kernel's impact model, the step's message gets the kernel's note ("a shelf
    of X gave way") and the sagging cue is kept. Once the law is active the settle pass does exactly this for every
    touched support, so this hook steps aside."""
    if lawbook.active("overload"):
        return      # kernel owns it
    w = ctx.w
    eid = ctx.args["target"]
    if eid not in w.s["entities"]:
        return
    cmap = phys_support.children_map(w.s)
    for sid in _supports_above(w.s, eid):
        if sid in w.s["entities"]:
            phys_impact.overload_law(w, sid, env=ctx.env, cmap=cmap)


# ----------------------------------------------------------------------
# time effects (called from the domain system with one entity pass)

def _enclosed(state, eid):
    entities = state["entities"]
    location = entities[eid].get("location")
    seen = set()
    while location in entities and location not in seen:
        seen.add(location)
        parent = entities[location]
        if parent.get("openable") and not parent.get("open"):
            return True
        location = parent.get("location")
    return False


def _outdoor_room(state, eid, room_of):
    rid = room_of.get(eid)
    return rid if rid and state["rooms"].get(rid, {}).get("outdoor") else None


class _RoomOf(object):
    """Lazy eid -> root room lookup (replaces a whole-house room map built every tick)."""

    def __init__(self, state):
        self.state, self.cache = state, {}

    def get(self, eid, default=None):
        if eid not in self.cache:
            from ...kernel.predicates import root_room
            self.cache[eid] = root_room(self.state, eid) if eid in self.state["entities"] else None
        value = self.cache[eid]
        return default if value is None else value


def exposure_candidate(record):
    """Static predicate (type-level props): everything the exposure pass can affect."""
    return bool("dry_out_s" in record or record.get("max_safe_temp_c") is not None or record.get("package")
                or record.get("mail_piece") or record.get("category") in ("document", "luggage")
                or record.get("type") == "rain_umbrella" or record.get("cold_pack_s") is not None
                or record.get("material") in PAPERISH or record.get("dries_in_air"))


def rain_mm_h(weather):
    """Rain rate (mm/h) on things outdoors: the weather's rain_mm_h (the kernel's rain input), else a rate for its
    condition word (a rain or sleet spell about 2.5 mm/h, a storm 10 mm/h; snow wets nothing until it melts)."""
    stated = phys_env.rain_mm_h(weather)
    if stated > 0:
        return stated
    return {"rain": 2.5, "sleet": 2.5, "storm": 10.0}.get((weather or {}).get("condition"), 0.0)


def _melting(w, eid, rec, dt):
    """melt while the law is deferred (TIER1_PHYSICS 5.9, 10.3): a body above its kernel melting point (melt_c: the
    record's, else its material class) accrues the kernel's damage.melted / deformed (phys.thermal.melt_check: full
    after 600 s at 10 K above it), exactly what the melting process does once the law is active."""
    if lawbook.active("melt"):
        return      # kernel owns it: the thermal system starts the melting process
    melt_c = phys_support.finite(physics.props(w.s, eid).melt_c)
    if melt_c is not None and float(rec.get("temperature_c", 21.0)) > melt_c:
        phys_thermal.melt_check(w, eid, dt)


def _soaked_parcel(w, eid, rec):
    """What a soaked parcel or piece of mail does (this domain's rule on top of the soaking): the label smudges and,
    for a box, paper, fabric and electronics inside get wet and water-damaged."""
    entities = w.s["entities"]
    m = w.mut(eid)
    m["label_smudged"] = True
    if rec.get("package"):
        m["soaked"] = True
        for item in w.descendants(eid):
            c = entities[item]
            if c.get("water_sensitive") or c.get("material") in PAPERISH + ("fabric", "electronics"):
                cm = w.mut(item)
                cm["wetness"] = max(float(cm.get("wetness", 0.0)), 0.7)
                cm["water_damaged"] = True
                cm["damaged"] = True
        w.emit("package_soaked", target=eid)
    else:
        m["water_damaged"] = True


def exposure_tick(w, dt, tick, room_of=None):
    """Slow exposure effects; runs from a periodic system, every update is linear in dt (or a
    dt-correct probability), so a 120 s period gives the same totals as 30 s substeps."""
    state = w.s
    hours = dt / 3600.0
    weather = state.get("weather", {})
    condition = weather.get("condition", "clear")
    rain = rain_mm_h(weather)
    raining = condition in ("rain", "storm", "snow", "sleet")
    entities = state["entities"]
    if room_of is None:
        room_of = _RoomOf(state)
    for eid in indexed(w, "exposure", exposure_candidate):
        rec = entities.get(eid)
        if rec is None:
            continue
        # pens and markers dry out uncapped
        exposed = (rec.get("settings") or {}).get("cap") == "off" or (rec.get("openable") and rec.get("open") and rec.get("dries_when_open"))
        if "dry_out_s" in rec and exposed and not rec.get("dried_out"):
            m = w.mut(eid)
            m["uncapped_s"] = round(float(rec.get("uncapped_s", 0.0)) + dt, 1)
            if m["uncapped_s"] >= float(rec.get("dry_out_s", 86400)):
                m["dried_out"] = True
                w.emit("pen_dried_out", target=eid)
        # heat: above its stated safe temperature a heat-sensitive good is damaged (thermal paper fades, a battery or a
        # photo is ruined); a body above its melting point melts (the kernel's melt law)
        limit = rec.get("max_safe_temp_c")
        if limit is not None and not rec.get("heat_damaged") and float(rec.get("temperature_c", 21.0)) > float(limit):
            m = w.mut(eid)
            m["heat_damaged"] = True
            m["damaged"] = True
            if rec.get("thermal"):
                m["faded"] = True
            w.emit("heat_damage", target=eid, temperature_c=rec.get("temperature_c"))
        if limit is not None and "temperature_c" in rec:
            _melting(w, eid, entities[eid], dt)
            rec = entities[eid]
        # parcels and mail outdoors
        if rec.get("package") or rec.get("mail_piece") or rec.get("category") in ("document", "luggage") or rec.get("type") == "rain_umbrella":
            room = _outdoor_room(state, eid, room_of)
            if room and rain > 0 and phys_env.exposed(state, eid):
                # rain on an exposed item is the kernel's rain law (surface wet within 2 minutes, paper and cardboard
                # soak through by absorption); while the law is deferred this domain applies it to its own things
                if not lawbook.active("rain"):
                    phys_env.rain_wet(w, eid, rain, dt)
                    rec = entities[eid]
            elif room and raining and state["rooms"][room].get("type") in COVERED_ROOMS and not _enclosed(state, eid):
                # wind drives rain under a porch roof, which the kernel never counts as exposed: slowly in a breeze,
                # faster in a gale (the local gust of the kernel's wind-at-object model) or a storm
                _, gust = phys_env.object_wind_mps(state, eid, weather)
                rate = 0.15 if gust >= PORCH_WIND_MPS or condition == "storm" else 0.02
                if rec.get("material") == "plastic" or rec.get("waterproof"):
                    rate *= 0.1
                wet = min(1.0, float(rec.get("wetness", 0.0)) + rate * hours)
                if wet != rec.get("wetness"):
                    w.mut(eid)["wetness"] = round(wet, 4)
                    rec = entities[eid]
            if room and (rec.get("package") or rec.get("mail_piece") or rec.get("category") == "document") \
                    and not rec.get("label_smudged") and (rec.get("soaked") or float(rec.get("wetness", 0.0)) > 0.6
                                                          and not phys_env.exposed(state, eid)):
                _soaked_parcel(w, eid, rec)
                rec = entities[eid]
            if rec.get("package") and room and state["rooms"][room].get("type") in STREET_ROOMS and not rec.get("outgoing"):
                left = float(rec.get("_unattended_s", 0.0)) + dt
                m = w.mut(eid)
                m["_unattended_s"] = left
                grace = float(state.get("household", {}).get("theft_grace_s", 7200.0))
                per_hour = float(state.get("household", {}).get("theft_risk_per_h", 0.03))
                if left > grace and per_hour > 0:
                    rng = tick.rng("admin_theft:" + eid)
                    if rng.random() < 1.0 - math.exp(-per_hour * hours):
                        from .harms import protected
                        valuable = protected(rec) or any(protected(entities[d]) for d in w.descendants(eid))
                        household(w).setdefault("stolen", {})[eid] = {"time_s": state["time_s"], "room": room, "label": rec.get("label"),
                                                                       "protected": bool(valuable)}
                        visual_event(w, "package_stolen", source=eid, room=room, size_m=1.0)   # someone walks off with it
                        doorbell_alert(w, room, eid, "Motion at the front door")
                        for item in w.descendants(eid):
                            w.destroy(item)
                        w.destroy(eid)
                        w.emit("package_stolen", target=eid, room=room)
                        continue
        # cold packs in meal kits
        if rec.get("cold_pack_s") is not None:
            left = float(rec.get("cold_pack_s", 0.0))
            room = room_of.get(eid)
            ambient = physics.room_temperature(state, room) if room in state["rooms"] else 21.0
            parent = entities.get(rec.get("location"), {})
            if parent.get("internal_temp_c") is not None and float(parent["internal_temp_c"]) < 8:
                ambient = float(parent["internal_temp_c"])
            burn = dt * max(0.0, (ambient - 0.0) / 20.0)
            m = w.mut(eid)
            if left > 0:
                m["cold_pack_s"] = round(max(0.0, left - burn), 1)
                m["internal_temp_c"] = 3.0
            if m["cold_pack_s"] <= 0 and "internal_temp_c" in m:
                m.pop("internal_temp_c", None)
                w.emit("cold_pack_spent", target=eid)
        # damp floors and enclosed wet goods
        material = rec.get("material")
        if material in PAPERISH and rec.get("location") in state["rooms"] and not rec.get("ground_layer"):
            room = state["rooms"][rec["location"]]
            if room.get("damp_floor") or (room.get("type") in ("basement", "garage", "shed") and float(room.get("humidity", 0.45)) > 0.6):
                m = w.mut(eid)
                m["wetness"] = round(min(1.0, float(rec.get("wetness", 0.0)) + 0.01 * hours), 4)
                if m["wetness"] > 0.5:
                    add_soil(w, eid, "mold", 0.05 * hours)
                    m["damp_damaged"] = True
        if rec.get("type") in ("rain_umbrella",) or rec.get("category") in ("luggage",) or rec.get("dries_in_air"):
            _folded_moisture(w, eid, rec, hours, state, room_of, raining)


def trap_moisture(w, eid):
    """Folding/zipping a wet umbrella or bag traps the water inside the folds (only this domain models it)."""
    rec = w.ent(eid)
    wet = float(rec.get("wetness", 0.0))
    if wet > 0.3:
        m = w.mut(eid)
        m["_trapped_water"] = round(max(float(rec.get("_trapped_water", 0.0)), wet), 4)
        m["wetness"] = 0.15


def release_moisture(w, eid):
    rec = w.ent(eid)
    trapped = float(rec.get("_trapped_water", 0.0))
    if trapped > 0.0:
        m = w.mut(eid)
        m["wetness"] = round(max(float(rec.get("wetness", 0.0)), trapped), 4)
        m["_trapped_water"] = 0.0


def _generic_drying_present():
    from ...kernel.plugins import SYSTEMS
    return any(name == "cleaning.drying" for _, name, _, _ in SYSTEMS)


def _folded_moisture(w, eid, rec, hours, state, room_of, raining):
    closed_self = rec.get("openable") and not rec.get("open")
    if closed_self and float(rec.get("wetness", 0.0)) > 0.3 and not float(rec.get("_trapped_water", 0.0)):
        trap_moisture(w, eid)
        rec = w.ent(eid)
    trapped = float(rec.get("_trapped_water", 0.0))
    if trapped > 0.0:
        m = w.mut(eid)
        m["_trapped_water"] = round(max(0.0, trapped - 0.02 * hours), 4)
        if trapped > 0.3:
            m["mildew"] = round(min(1.0, float(rec.get("mildew", 0.0)) + 0.04 * hours), 4)
            if m["mildew"] >= 0.5 and not rec.get("mildewed"):
                m["mildewed"] = True
                add_soil(w, eid, "mildew", 0.5)
                if rec.get("type") == "rain_umbrella":
                    m["ribs_rusting"] = True
                w.emit("mildew", target=eid)
    # surface drying: deferred to the cleaning domain's drying system when it is loaded
    wet = float(w.ent(eid).get("wetness", 0.0))
    if wet > 0.0 and not _generic_drying_present() and not (_outdoor_room(state, eid, room_of) and raining):
        rate = 0.02 if _enclosed(state, eid) else 0.35
        w.mut(eid)["wetness"] = round(max(0.0, wet - rate * hours), 4)


@after("close", order=85)
def _fold_wet(ctx, result):
    rec = ctx.w.ent(ctx.args["target"])
    if rec.get("type") == "rain_umbrella" or rec.get("category") == "luggage" or rec.get("dries_in_air"):
        trap_moisture(ctx.w, ctx.args["target"])


@after("open", order=85)
def _unfold_wet(ctx, result):
    rec = ctx.w.ent(ctx.args["target"])
    if rec.get("type") == "rain_umbrella" or rec.get("category") == "luggage" or rec.get("dries_in_air"):
        release_moisture(ctx.w, ctx.args["target"])

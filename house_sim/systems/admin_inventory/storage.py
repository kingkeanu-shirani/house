"""Storage physics and time effects for stored goods (fidelity G / P-approx):

* shelf and surface load ratings: overloading a shelf collapses it, dumping its contents on the
  floor (fragile items break);
* heat-sensitive goods (candles, vinyl, photos, thermal receipts, batteries) are damaged above
  their max_safe_temp_c (attics in summer, hot cars, window sills);
* damp: cardboard and paper standing on a damp basement/garage/shed floor wick up water and
  mildew; wet umbrellas, bags and luggage closed or shut away mildew, opened ones dry;
* pens and markers left uncapped dry out;
* weather on parcels and mail left outdoors (rain soaks cardboard and damages contents;
  covered porches shelter), cold packs in meal-kit boxes melt, unattended porch parcels can
  be stolen after a grace period.
"""
import math

from ...kernel import geometry, physics
from ...kernel.plugins import after
from ..core import add_soil, subtree_mass
from .common import admin_facts, drop_cell, household, indexed

# Per-shelf ratings (kg). Particleboard bookcases ~25 kg/shelf; wire/steel shelving units 90 kg;
# closet wire shelves ~25 kg; floating wall shelves ~12 kg; cabinet shelves ~30 kg.
SHELF_RATING = (("shelf_unit", 90.0), ("bookshelf", 25.0), ("closet_shelving", 25.0), ("upper_cabinet", 15.0),
                ("filing_cabinet", 30.0), ("cabinet", 30.0), ("wardrobe", 40.0), ("closet", 40.0))
SURFACE_RATING = (("wall_shelf", 12.0),)
STREET_ROOMS = ("porch", "front_yard", "sidewalk", "driveway")
COVERED_ROOMS = ("porch", "balcony")
PAPERISH = ("paper", "cardboard")


def _rating(record, table):
    if record.get("shelf_load_kg") is not None:
        return float(record["shelf_load_kg"])
    for type_name, value in table:
        if record.get("type") == type_name or type_name in record.get("is_a", []):
            return value
    return None


def shelf_rating(record):
    """Rated load (kg) of one shelf of a shelved container (None when unrated)."""
    return _rating(record, SHELF_RATING) if record.get("shelf_heights") else None


def surface_rating(record):
    """Rated load (kg) of a fixed surface (`max_load_kg`, else the type table; None when unrated)."""
    if record.get("max_load_kg") is not None:
        return float(record["max_load_kg"])
    return _rating(record, SURFACE_RATING)


def _record_mass(rec):
    try:
        return float(rec.get("mass_kg", 0.0) or 0.0) + float(rec.get("liquid_l", 0.0) or 0.0) * float(rec.get("liquid_density", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 0.0


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
    shelving unit) can still take before its rating, or a fixed surface (shelf=None on a surface) before its
    `max_load_kg`. Returns None when the furniture is unrated. Use it before Placer.inside/on with heavy stock:
        if (shelf_headroom(p.c, cabinet, 0) or 1e9) >= mass: p.inside(cabinet, ..., shelf=0)"""
    entities = config["entities"]
    rec = entities.get(container) or {}
    if rec.get("shelf_heights"):
        rating = shelf_rating(rec)
        return None if rating is None else round(rating - shelf_load(entities, container, shelf or 0), 3)
    rating = surface_rating(rec)
    return None if rating is None else round(rating - surface_load(entities, container), 3)


def _baseline_load(ctx, container, shelf):
    """Load that shelf (or surface, shelf=None) carried at episode start (generator and setup), from the engine's
    baseline snapshot; 0 when there is none. Memoized per episode."""
    baseline = getattr(getattr(ctx, "env", None), "_baseline", None)
    if not baseline or container not in baseline.get("entities", {}):
        return 0.0
    from .harms import _memo, _on_baseline
    kids = _on_baseline("storage_kids", baseline, lambda base: children_map(base["entities"]))
    memo = _memo("storage_start_load", baseline)
    key = (container, shelf)
    if key not in memo:
        entities = baseline["entities"]
        memo[key] = shelf_load(entities, container, shelf, kids) if shelf is not None else surface_load(entities, container, kids)
    return memo[key]


def _collapse(w, furniture, items, reason):
    room = w.root_room(furniture)
    fm = w.mut(furniture)
    fm["damaged_shelves"] = list(fm.get("damaged_shelves", [])) + [reason]
    near = tuple((w.ent(furniture).get("pos") or w.robot.get("pos") or [0, 0]))
    broken = []
    for item in items:
        m = w.mut(item)
        m["location"], m["relation"] = room, "on"
        for key in ("spos", "shelf", "held_hand"):
            m.pop(key, None)
        m["pos"] = drop_cell(w, room, near, item)
        m["fell"] = True
        for sub in [item] + w.descendants(item):
            rec = w.ent(sub)
            if rec.get("fragile") and not rec.get("broken") and not rec.get("wrapped") and not rec.get("padded"):
                w.mut(sub)["broken"] = True
                broken.append(sub)
    w.stat("damage")
    if broken:
        w.stat("breakages", len(broken))
    w.emit("shelf_collapsed", target=furniture, reason=reason, items=items, broken=broken)
    return broken


@after("place", order=90)
def _load_check(ctx, result):
    """A shelf/surface collapses when the robot's placement pushes its load over max(rating, the load it already
    carried at episode start): a shelf the generator (or a setup) loaded past its nominal rating evidently holds that
    much, so only weight the robot adds beyond it counts (round-2 fix: never charge a scene artifact to the robot)."""
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent_id = rec.get("location")
    parent = w.s["entities"].get(parent_id)
    if parent is None:
        return
    if rec.get("relation") == "in" and parent.get("shelf_heights"):
        rating = shelf_rating(parent)
        if rating is None:
            return
        shelf = _shelf_of(rec)
        items = [c for c in w.children(parent_id) if w.ent(c).get("relation") == "in" and _shelf_of(w.ent(c)) == shelf]
        load = sum(subtree_mass(w, c) for c in items)
        if load > rating + 1e-9:
            limit = max(rating, _baseline_load(ctx, parent_id, shelf))
            if load > limit + 1e-9:
                reason = "shelf {} overloaded".format(shelf) if ctx.realistic else                     "shelf {} overloaded ({:.0f} kg > {:.0f} kg)".format(shelf, load, limit)   # loads are not seen
                _collapse(w, parent_id, items, reason)
    elif rec.get("relation") == "on" and not parent.get("portable"):
        rating = surface_rating(parent)
        if rating is None:
            return
        items = [c for c in w.children(parent_id) if w.ent(c).get("relation") == "on"]
        load = sum(subtree_mass(w, c) for c in items)
        if load > float(rating) + 1e-9:
            limit = max(float(rating), _baseline_load(ctx, parent_id, None))
            if load > limit + 1e-9:
                reason = "surface overloaded" if ctx.realistic else "surface overloaded ({:.0f} kg > {:.0f} kg)".format(load, limit)
                _collapse(w, parent_id, items, reason)


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


def exposure_tick(w, dt, tick, room_of=None):
    """Slow exposure effects; runs from a periodic system, every update is linear in dt (or a
    dt-correct probability), so a 120 s period gives the same totals as 30 s substeps."""
    state = w.s
    hours = dt / 3600.0
    weather = state.get("weather", {})
    condition = weather.get("condition", "clear")
    wind = float(weather.get("wind_mps", 2.0))
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
        # heat damage
        limit = rec.get("max_safe_temp_c")
        if limit is not None and not rec.get("heat_damaged") and float(rec.get("temperature_c", 21.0)) > float(limit):
            m = w.mut(eid)
            m["heat_damaged"] = True
            m["damaged"] = True
            if rec.get("material") in ("wax", "vinyl", "chocolate"):
                m["melted"] = True
            if rec.get("thermal"):
                m["faded"] = True
            w.emit("heat_damage", target=eid, temperature_c=rec.get("temperature_c"))
        # parcels and mail outdoors
        if rec.get("package") or rec.get("mail_piece") or rec.get("category") in ("document", "luggage") or rec.get("type") == "rain_umbrella":
            room = _outdoor_room(state, eid, room_of)
            if room and raining and not _enclosed(state, eid):
                rtype = state["rooms"][room].get("type")
                rate = 0.4 if rtype not in COVERED_ROOMS else (0.15 if wind > 8 or condition == "storm" else 0.02)
                if rec.get("material") == "plastic" or rec.get("waterproof"):
                    rate *= 0.1
                wet = min(1.0, float(rec.get("wetness", 0.0)) + rate * hours)
                if wet != rec.get("wetness"):
                    m = w.mut(eid)
                    m["wetness"] = round(wet, 4)
                    if wet > 0.6 and rec.get("package") and not rec.get("soaked"):
                        m["soaked"] = True
                        m["label_smudged"] = True
                        for item in w.descendants(eid):
                            c = entities[item]
                            if c.get("water_sensitive") or c.get("material") in PAPERISH + ("fabric", "electronics"):
                                cm = w.mut(item)
                                cm["wetness"] = max(float(cm.get("wetness", 0.0)), 0.7)
                                cm["water_damaged"] = True
                                cm["damaged"] = True
                        w.emit("package_soaked", target=eid)
                    if wet > 0.6 and (rec.get("mail_piece") or rec.get("category") == "document") and not rec.get("water_damaged"):
                        m["water_damaged"] = True
                        m["label_smudged"] = True
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

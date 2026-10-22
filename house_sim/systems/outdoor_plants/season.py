"""Generation-2 season ground state (TIER1_SWEEP 13.3) and the porch steps of the exterior stairs (14.6).

The furnisher `outdoor_plants.season` (order 97, generation 2 only; generation-1 houses return at once) makes the
yard agree with the date and the weather the house was generated with (`weather.process`, generator/climate.py):

* snow and ice on the exposed paved surfaces (outdoor floors, walkways, the porch steps) from the process's last
  seven days, hour by hour, with this domain's own rates: snowfall accumulates (1 cm per mm of water at or below
  1 C), it melts at 0.25 cm per degree-hour above 0.5 C, rain on snow wears it down, meltwater and rain left on the
  paving freeze to ice at or below 0 C, ice thaws at 0.15 per degree-hour above 1 C (weather.slow_weather's rates);
  snow that went through a melt keeps a wet density (250 kg/m3), fresh powder 100;
* leaf litter in autumn: lawn patches and paving collect leaves from September (light) through November (heavy),
  more in a yard with a deciduous tree; out of season the paving's leaves are swept;
* the pool is open roughly from late May to early September (US, Canada, continental Europe), June to August (UK)
  and July to August (Japan); a closed pool has its pump off;
* the garden stage by month and frost: tender annuals (tomato, pepper, zucchini, beans, basil, petunia, marigold,
  geranium) are gone after the first frost of the last fortnight and not yet planted before May; in spring the
  crops in the beds are seedlings; perennials are dormant in winter.

The porch steps: a generation-2 porch reaches its front yard by an exterior stairs connection; the yard end of the
flight gets a `steps` surface (a walkway: snow, ice, de-icer and traction like any paving) tied to the connection,
so "salt the porch steps" has a real target, and the treads' state is mirrored onto the connection
(`yard.mirror_steps`: navigation_robot reads `tread_wet`; `icy` and `snow_cm` are written for it too).
"""
import datetime

from ...kernel.plugins import furnisher
from . import common as C
from .species import profile

HISTORY_H = 168                      # hours of process weather the ground state looks back over
FROST_H = 14 * 24                    # tender annuals are killed by a frost in the last fortnight
FROST_C = -0.5
SNOW_AT_C = 1.0                      # precipitation at or below this falls as snow (generator/climate.py)
MELT_CM_PER_DEG_H = 0.25             # weather.slow_weather: snow melt per degree-hour above 0.5 C
ICE_THAW_PER_DEG_H = 0.15            # weather.slow_weather: ice thaw per degree-hour above 1 C
RAIN_ON_SNOW_CM_PER_MM = 0.1
TENDER_ANNUALS = ("tomato", "pepper", "zucchini", "bush_bean", "cucumber", "winter_squash", "basil", "petunia",
                  "marigold", "geranium", "impatiens", "sunflower", "dill", "cilantro")
EARLY_CROPS = ("lettuce", "kale", "pea", "carrot")
DECIDUOUS = ("maple", "apple", "fig", "lilac", "hydrangea", "forsythia", "spirea")
POOL_SEASON = {"uk": ((6, 1), (8, 31)), "jp": ((7, 1), (8, 31))}
POOL_DEFAULT = ((5, 25), (9, 10))     # assumption: Memorial Day to Labor Day (US), similar in Canada and Europe


def gen2(opts):
    return int(opts.get("gen") or 1) >= 2


def _start(config):
    try:
        return datetime.datetime.fromisoformat((config.get("clock") or {}).get("start") or "2026-10-09T10:00:00")
    except ValueError:
        return datetime.datetime(2026, 10, 9, 10)


def history(config, hours=HISTORY_H):
    """The process weather of the `hours` hours before the start time, oldest first ([] without a process)."""
    from ...generator import climate
    proc = (config.get("weather") or {}).get("process")
    if not isinstance(proc, dict) or not proc.get("seed_key") or not proc.get("region"):
        return []
    start = _start(config)
    return [climate.weather_at(proc["seed_key"], proc["region"], start - datetime.timedelta(hours=h))
            for h in range(int(hours), 0, -1)]


def ground_after(hours):
    """(snow_cm, snow_density, ice 0..1, wetness 0..1) of exposed paving after the hourly weather `hours`."""
    snow, ice, wet, melted = 0.0, 0.0, 0.0, False
    for h in hours:
        t = float(h["temperature_c"])
        if h.get("snow_cm_h", 0.0) > 0 and t <= SNOW_AT_C:
            snow += float(h["snow_cm_h"])
        if h.get("rain_mm_h", 0.0) > 0:
            wet = 1.0
            if snow > 0:
                snow = max(0.0, snow - RAIN_ON_SNOW_CM_PER_MM * float(h["rain_mm_h"]))
                melted = True
        if t > 0.5 and snow > 0:
            melt = min(snow, MELT_CM_PER_DEG_H * t)
            snow -= melt
            wet = min(1.0, wet + melt * 0.2)
            melted = True
        if t <= 0.0 and wet > 0.05:
            ice = max(ice, min(1.0, wet))
            wet = 0.0
        elif t > 1.0:
            ice = max(0.0, ice - ICE_THAW_PER_DEG_H * t)
            wet = max(0.0, wet - 0.08 * t)
    return round(snow, 2), (250.0 if melted else 100.0), round(ice, 3), round(wet, 3)


def frost_recently(config):
    return any(float(h["temperature_c"]) <= FROST_C for h in history(config, FROST_H))


def _exposed_surfaces(config):
    rooms, ents = config["rooms"], config["entities"]
    out = []
    for eid, rec in sorted(ents.items()):
        rid = rec.get("location")
        room = rooms.get(rid) if isinstance(rid, str) else None
        if room is None or not room.get("outdoor") or room.get("roofed") or room.get("type") in C.ROOFED_ROOMS:
            continue
        if rec.get("type") == "walkway" or "walkway" in (rec.get("is_a") or ()) or \
                (rec.get("category") == "floor" and eid == "floor_" + rid):
            out.append(eid)
    return out


def _pool_open(start, region):
    (m0, d0), (m1, d1) = POOL_SEASON.get(region, POOL_DEFAULT)
    return (m0, d0) <= (start.month, start.day) <= (m1, d1)


def _remove(config, eid):
    ents = config["entities"]
    kids = [k for k, rec in ents.items() if rec.get("location") == eid]
    for kid in kids:
        _remove(config, kid)
    ents.pop(eid, None)


def _garden_stage(config, start, frost):
    """Bed plants by month: tender annuals gone after a frost and not yet out before May; spring crops are seedlings;
    perennials dormant in winter (Dec-Feb). Houseplants and potted plants indoors are untouched."""
    ents, rooms = config["entities"], config["rooms"]
    month = start.month
    for eid in sorted(ents):
        rec = ents.get(eid)
        if rec is None or rec.get("type") not in ("garden_plant", "herb_plant") or rec.get("role") == "weed":
            continue
        bed = ents.get(rec.get("location")) or {}
        if bed.get("type") not in ("garden_bed", "raised_bed") and "garden_bed" not in (bed.get("is_a") or ()):
            continue
        room = rooms.get(bed.get("location")) or {}
        if not room.get("outdoor"):
            continue
        species = rec.get("species")
        prof = profile(species)
        if species in TENDER_ANNUALS and (frost or month in (12, 1, 2, 3, 4)):
            _remove(config, eid)
            continue
        if month in (12, 1, 2) and species not in EARLY_CROPS:
            rec["dormant"] = True
            leaves = dict(rec.get("leaves") or {})
            leaves["healthy"] = min(int(leaves.get("healthy", 0) or 0), 4 if prof.get("woody") else 0)
            rec["leaves"] = leaves
            rec.pop("flowers", None)
        elif month in (3, 4, 5) and (species in TENDER_ANNUALS or species in EARLY_CROPS):
            size = list(rec.get("size_m") or [0.35, 0.35, 0.45])
            rec["size_m"] = [round(min(size[0], 0.12), 3), round(min(size[1], 0.12), 3), round(min(size[2], 0.12), 3)]
            rec["mass_kg"] = round(min(float(rec.get("mass_kg", 0.3) or 0.3), 0.05), 3)
            leaves = dict(rec.get("leaves") or {})
            leaves["healthy"] = min(int(leaves.get("healthy", 0) or 0), 6)
            rec["leaves"] = leaves
            rec.pop("flowers", None)
            rec["label"] = "seedling" if species in TENDER_ANNUALS or species in EARLY_CROPS else rec.get("label")


def _leaf_litter(config, start, rng):
    """Autumn leaves on lawns and paving (September light, October, November heavy); swept out of season."""
    ents = config["entities"]
    month = start.month
    base = {9: (0.0, 0.1), 10: (0.05, 0.35), 11: (0.15, 0.6)}.get(month)
    rooms_with_trees = {rec.get("location") for rec in ents.values()
                        if rec.get("type") in ("tree", "fruit_tree", "shrub") and rec.get("species") in DECIDUOUS}
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("type") == "lawn" and isinstance(rec.get("grid"), dict) and base:
            grid = rec["grid"]
            lo, hi = base
            boost = 1.6 if rec.get("location") in rooms_with_trees else 1.0
            for i, kind in enumerate(grid["kind"]):
                if kind == "L":
                    grid["leaves"][i] = round(min(1.0, rng.uniform(lo, hi) * boost), 3)
        elif rec.get("category") == "floor" or rec.get("type") == "walkway" or "walkway" in (rec.get("is_a") or ()):
            room = config["rooms"].get(rec.get("location")) if isinstance(rec.get("location"), str) else None
            if not (room or {}).get("outdoor"):
                continue
            soil = rec.get("soil")
            if not isinstance(soil, dict) or "leaves" not in soil:
                continue
            if base:
                lo, hi = base
                soil["leaves"] = round(rng.uniform(lo, hi), 2)
            else:
                soil["leaves"] = round(min(float(soil.get("leaves", 0.0)), rng.uniform(0.0, 0.05)), 2)


def porch_steps(config, registry):
    """A `steps` surface at the yard end of every exterior stairs connection (generation-2 porch steps). Returns the
    ids made."""
    made = []
    ents, rooms = config["entities"], config["rooms"]
    for cid, conn in sorted((config.get("connections") or {}).items()):
        if not conn.get("exterior") or conn.get("kind", conn.get("type")) not in (None, "stairs"):
            continue
        pair = list(conn.get("rooms") or ())
        yard = [r for r in pair if (rooms.get(r) or {}).get("outdoor") and not (rooms[r].get("roofed") or rooms[r].get("type") in C.ROOFED_ROOMS)]
        cells = conn.get("cells") or {}
        if not yard or yard[0] not in cells:
            continue
        rid = yard[0]
        cell = [int(cells[rid][0]), int(cells[rid][1])]
        steps = int(conn.get("steps", 3) or 3)
        width = float(conn.get("width_m", conn.get("width", 1.0)) or 1.0)
        run = float(conn.get("run_m", 0.28) or 0.28)
        rec = registry.make("steps", rid, label="porch steps", cells=[cell], connection=cid,
                            area_m2=round(steps * run * width, 2), material=conn.get("tread_material") or "concrete")
        rec["pos"] = list(cell)
        eid = "porch_steps_" + cid
        if eid in ents:
            continue
        ents[eid] = rec
        made.append(eid)
    return made


def mirror_fields(rec):
    """What a porch-steps surface puts on its connection: navigation_robot's tread_wet (wet, snowy or icy treads),
    and icy / snow_cm (requested for navigation's slip rule)."""
    ice = float(rec.get("ice", 0.0) or 0.0)
    snow = float(rec.get("snow_cm", 0.0) or 0.0)
    wet = float(rec.get("wetness", 0.0) or 0.0)
    return {"tread_wet": bool(wet > 0.3 or snow > 0.5 or ice > 0.1), "icy": ice > 0.1, "snow_cm": round(snow, 1)}


@furnisher("outdoor_plants.season", order=97)
def furnish_season(config, rng, opts, registry):
    """Generation 2 only (TIER1_SWEEP 13.3, 14.6): see the module doc. Its draws come from its own stream, so no other
    furnisher's draws move; generation 1 returns at once."""
    if not gen2(opts):
        return
    start = _start(config)
    region = opts.get("region") or (config.get("clock") or {}).get("region") or "us"
    porch_steps(config, registry)
    snow, density, ice, wet = ground_after(history(config))
    ents = config["entities"]
    if snow > 0.2 or ice > 0.0 or wet > 0.0:
        for eid in _exposed_surfaces(config):
            rec = ents[eid]
            rec["snow_cm"] = snow if snow > 0.2 else 0.0
            if snow > 0.2:
                rec["snow_density"] = density
            rec["ice"] = ice
            if wet > 0.0:
                rec["wetness"] = round(max(float(rec.get("wetness", 0.0) or 0.0), wet), 3)
    for eid, rec in sorted(ents.items()):
        conn = (config.get("connections") or {}).get(rec.get("connection") or "")
        if conn is not None and (rec.get("type") == "steps"):
            for key, value in mirror_fields(rec).items():
                if value or key in conn:
                    conn[key] = value
    _leaf_litter(config, start, rng)
    for eid, rec in sorted(ents.items()):
        if rec.get("type") == "pool":
            open_ = _pool_open(start, region)
            rec["season"] = "open" if open_ else "closed"
            for pump in [k for k, r in ents.items() if r.get("type") == "pool_pump" and r.get("location") == rec.get("location")]:
                ents[pump]["on"] = open_
    _garden_stage(config, start, frost_recently(config))

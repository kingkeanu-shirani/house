"""Dust on things nobody used for a while: the soil API the people domain's history synthesizer calls (TIER1_SWEEP 12,
22.3: "leaves evidence: final placements, dust on rarely used items (cleaning's soil API)").

    settle_dust(target, eid, days)     dust that settled on eid during `days` days untouched; target is a live World or a
                                       config / state dict (generation and setup time); returns the dust level now
    dust_after(days, enclosed=False)   the level alone (no write)

Rates (assumption, documented): settled-dust loading in homes grows by about 1-5 mg/m2 a day on open horizontal
surfaces; 3 mg/m2/day is used, a tenth of it inside closed cabinets, drawers and boxes. With the soil model's 4 g/m2
at dust level 1.0 (model.SOILS) an open shelf looks dusty (level 0.05, CLEAN) after about two months untouched, a
closed cupboard's contents after about two years.
"""
from . import model as M
from . import state as S

DUST_G_M2_PER_DAY = 0.003
ENCLOSED_SHARE = 0.1


def dust_after(days, enclosed=False):
    """Dust level (0..1) that `days` days untouched leave on an open (or, enclosed, a closed-in) surface."""
    rate = DUST_G_M2_PER_DAY * (ENCLOSED_SHARE if enclosed else 1.0)
    return M.clamp(max(0.0, float(days)) * rate / float(M.SOILS["dust"]["g_m2"]))


def _entities(target):
    return target.s["entities"] if hasattr(target, "s") else target["entities"]


def _enclosed(entities, eid):
    seen, current = {eid}, entities.get(eid, {}).get("location")
    while current in entities and current not in seen:
        rec = entities[current]
        if rec.get("openable") and not rec.get("open"):
            return True
        seen.add(current)
        current = rec.get("location")
    return False


def settle_dust(target, eid, days, enclosed=None):
    """Add the dust of `days` untouched days to eid's top face (or its soil) and return its dust level. target: a World
    (journaled writes, the soil parts kept in sync) or a config / state dict (generation, setup). enclosed: None decides
    from the container chain (a closed cupboard, drawer or box)."""
    entities = _entities(target)
    if eid not in entities:
        return 0.0
    if enclosed is None:
        enclosed = _enclosed(entities, eid)
    amount = dust_after(days, enclosed)
    if amount <= 0.0:
        return float(S.soil_dict(entities[eid]).get("dust", 0.0))
    if hasattr(target, "s"):
        rec = target.ent(eid)
        if S.has_parts(rec):
            return S.add_face_soil(target, eid, S.primary_face(rec), "dust", amount)
        m = target.mut(eid)
        soil = S.soil_dict(m)
        soil["dust"] = S.rnd(M.clamp(float(soil.get("dust", 0.0)) + amount))
        m["soil"] = soil
        return soil["dust"]
    rec = entities[eid]
    soil = dict(S.soil_dict(rec))
    soil["dust"] = S.rnd(M.clamp(float(soil.get("dust", 0.0)) + amount))
    rec["soil"] = soil
    return soil["dust"]

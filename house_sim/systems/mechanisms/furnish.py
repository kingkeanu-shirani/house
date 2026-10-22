"""Furnishers.

mechanisms.coverings (order 25, after the structure): curtains or blinds on windows of living areas,
bedrooms, offices, kitchens and bathrooms (all open by default, so daylight is unchanged; none on a window
boxed in by a taller wardrobe or bookcase), a window sill under the first window of living rooms, bedrooms,
kitchens and dining rooms whose wall below is clear (a counter or dresser top in front serves as the sill),
window facing (for sun/glare). Units on west/east walls get rot 90 so their width runs along the wall.
mechanisms.garage (order 26): garage-door wall button by the house entry and two photo eyes.
mechanisms.security (order 27): an alarm keypad by the front door in some houses (disarmed).
mechanisms.dressers (order 80, after textiles' linens at 75): three drawers in dressers that have none.

Counts stay modest (~10-30 entities); Placer returning None (no space) is tolerated everywhere.
"""
from ...generator.place import Placer
from ...kernel.plugins import furnisher
from . import build as B

COVER_ROOMS = {
    "living_room": [("curtains", 0.55), ("venetian_blind", 0.25), ("tieback_curtains", 0.2)],
    "dining_room": [("curtains", 0.6), ("venetian_blind", 0.4)],
    "master_bedroom": [("blackout_curtains", 0.4), ("curtains", 0.4), ("cellular_shade", 0.2)],
    "bedroom": [("curtains", 0.4), ("venetian_blind", 0.3), ("blackout_curtains", 0.15), ("roller_shade", 0.15)],
    "guest_room": [("curtains", 0.6), ("venetian_blind", 0.4)],
    "nursery": [("blackout_curtains", 0.5), ("cordless_blind", 0.5)],
    "office": [("venetian_blind", 0.6), ("roller_shade", 0.4)],
    "kitchen": [("venetian_blind", 0.5), ("roller_shade", 0.3), ("cellular_shade", 0.2)],
    "bathroom": [("cellular_shade", 0.6), ("venetian_blind", 0.4)],
    "playroom": [("cordless_blind", 1.0)],
    "craft_room": [("roller_shade", 1.0)],
    "gym": [("roller_shade", 1.0)],
}
SILL_ROOMS = {"living_room", "master_bedroom", "bedroom", "kitchen", "dining_room"}


def _pick(rng, options):
    roll, acc = rng.random(), 0.0
    for name, weight in options:
        acc += weight
        if roll <= acc:
            return name
    return options[-1][0]


@furnisher("mechanisms.coverings", order=25)
def furnish_coverings(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    ents = config["entities"]
    for rid in sorted(config["rooms"]):
        room = config["rooms"][rid]
        options = COVER_ROOMS.get(room.get("type"))
        windows = [e for e in sorted(ents) if ents[e].get("category") == "window" and ents[e].get("location") == rid]
        tops = B.standing_tops(config, rid) if windows else {}
        sill = room.get("type") not in SILL_ROOMS
        for wid in windows:
            ents[wid].setdefault("facing_deg", rng.choice([0, 90, 180, 270]))
            if options:
                type_name = _pick(rng, options)
                kind = "curtain" if "curtain" in type_name else "blind"
                rec = B.covering_record(config, p, wid, type_name)
                # a window boxed in by a tall wardrobe or bookcase gets no covering (it would hang hidden behind it)
                if not B.blocked_at(config, rec, B.C.num(rec.get("mount_height_m"), 1.2) + 0.05, tops):
                    B.add_covering(config, p, wid, kind, type_name=type_name)
            # one sill, under the first window with clear wall below it (a counter or dresser top serves as the sill)
            if not sill and not B.blocked_at(config, B.sill_record(config, p, wid), B.C.num(ents[wid].get("mount_height_m"), 0.9) - 0.05, tops):
                B.add_sill(config, p, wid)
                sill = True


@furnisher("mechanisms.garage", order=26)
def furnish_garage(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    for door in B.find_door(config, "garage"):
        B.garage_parts(config, p, door)


@furnisher("mechanisms.security", order=27)
def furnish_security(config, rng, opts, registry):
    if rng.random() > 0.4:
        return
    p = Placer(config, registry, rng)
    fronts = B.find_door(config, "front")
    if not fronts:
        return
    door = fronts[0]
    inside = next((r for r in config["entities"][door]["connects"] if not config["rooms"][r].get("outdoor") and not config["rooms"][r].get("shared")), None)
    if inside is None:
        return
    panel = p.mount(inside, "alarm_panel", height=1.4)
    config["entities"][panel]["_code"] = "{:04d}".format(rng.randrange(10000))


@furnisher("mechanisms.dressers", order=80)
def furnish_dressers(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    for dresser in p.find("dresser"):
        if config["entities"][dresser].get("label") == "changing dresser":
            continue
        B.dresser_drawers(config, p, dresser)


# Tall free-standing storage furniture (the clothing storage units, bookcases and cabinets of CPSC's tip-over
# reports; ASTM F2057 and the STURDY Act cover units 27 in = 0.686 m tall and up) and the share of them anchored to the
# wall. TIER1_DECISIONS (physics): a conservative 25 % until a cited survey figure is found (docs/KNOWN_LIMITS.md).
TALL_STORAGE = ("dresser", "wardrobe", "bookshelf", "shelf_unit", "filing_cabinet", "chemical_cabinet")
TALL_STORAGE_MIN_M = 0.686
ANCHORED_SHARE = 0.25


def _tall_storage(rec, registry):
    if rec.get("portable") or rec.get("mounted") or rec.get("relation", "on") != "on" or "anchored" in rec:
        return False
    name = rec.get("type")
    chain = [name] + (registry.ancestors(name) if name and registry.known(name) else [])
    size = rec.get("size_m") or [0.0, 0.0, 0.0]
    return bool(set(chain) & set(TALL_STORAGE)) and len(size) > 2 and B.C.num(size[2]) >= TALL_STORAGE_MIN_M


@furnisher("mechanisms.anchoring", order=106)
def furnish_anchoring(config, rng, opts, registry):
    """Generation 2: every tall free-standing storage piece gets an explicit `anchored` (drawn per piece, ANCHORED_SHARE
    of them strapped to the wall), so the kernel's furniture_tip law and this domain's drawers agree on which pieces
    can tip (TIER1_INTEGRATION 6 "mechanisms"). Generation-1 houses are unchanged (a generation-1 draw needs the
    gen1_digests re-record; until then their furniture keeps this domain's convention: anchored)."""
    if int(opts.get("gen") or 1) < 2:
        return
    ents = config["entities"]
    for eid in sorted(ents):
        if _tall_storage(ents[eid], registry):
            ents[eid]["anchored"] = rng.random() < ANCHORED_SHARE


@furnisher("mechanisms.smart_lock", order=107)
def furnish_smart_lock(config, rng, opts, registry):
    """Generation 2, the connected smart-home tier (household.profile.smart_home, TIER1_SWEEP 14.7): the front door gets
    a smart deadbolt -- the same key and thumb turn, plus a keypad display and the phone app's lock status
    (mech.smart; no auto-relock unless a scenario sets one)."""
    if int(opts.get("gen") or 1) < 2:
        return
    profile = (config.get("household") or {}).get("profile") or {}
    if profile.get("smart_home") != "connected":
        return
    for door in B.find_door(config, "front")[:1]:
        rec = config["entities"][door]
        if not rec.get("lockable"):
            continue
        mech = rec.setdefault("mech", {})
        mech.setdefault("smart", {"display": "Locked" if rec.get("locked") else "Unlocked",
                                  "reported": "locked" if rec.get("locked") else "unlocked", "battery": 0.9,
                                  "auto_relock_s": None})
        rec.setdefault("display", mech["smart"]["display"])

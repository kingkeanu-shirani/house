"""What the house ledger needs from the cleaning domain (VERIFY_HARDENING 6.2-6.5, 8.2, 10.3; package WP-D cleaning).

Mess kinds (6.5; kernel/plugins.py @mess_kind, measured on any state):
  debris       g      loose floor soil converted to grams (model.grams over the floors' 0.5 m cells, FLOOR_LOOSE
                      kinds only: dust, crumbs, dirt, sand, hair, litter...), debris piles (composition), tool and bin
                      loads (cl_load: dustpans, vacuum bins and bags, waste containers), debris in a sink's drain
                      (drain_debris_g) and in a floor vent (vent_debris_g)
  waste_item   count  refuse: every entity with waste: true (cleaning marks its own refuse at episode_start, below)
Flows are logged by the verbs that move them (sweep, collect_debris, vacuum, empty_into, scrape, shake_out...) through
state.flow; the measures are the cross-check the ledger trusts.

Refuse (6.4, 6.5): at episode_start every waste item of this domain that is refuse -- not kept (_disposition keep),
not ambiguous, not sentimental or valuable, owned by no one -- gets waste: true, the kernel's refuse flag: house.lost
and house.removed exempt refuse, and the waste_item kind counts it. Disposables cleaning creates (used paper towels,
a peeled produce sticker) are refuse from the start.

Derived fields (8.2; kernel/plugins.py @derived_field, overlaid by schema-4 evaluation):
  in_stream   the waste stream of the waste container the thing is in now (missing outside every waste container)
  stream_ok   whether that container's stream is one the thing belongs to now (waste.acceptable / container_accepts)
  polished    the polish verb's finish, true only while no dust, fingerprints, streaks or water spots cover its faces

Settings skills (10.3, B14 probes): a vacuum's settings.mode / settings.suction, a lockable cart's locked, a liner's
opened (one way: shaking a folded liner opens it).

Unfamiliar things (realistic perception, docs/PERCEPTION.md review I4): an order-1 before hook on every cleaning verb
gives an unfamiliar (novel) thing named in any entity parameter the kernel's one identify-first refusal, before any
guard reads what it really is; state.held_tool passes an unnamed held unfamiliar thing over.
"""
from ...kernel.plugins import ACTIONS, before, derived_field, hook, mess_kind, settings_skill
from ...kernel.predicates import MISSING
from . import model as M
from . import state as S

CELL_AREA = 0.25            # m2 per floor cell (floors.CELL_AREA)
POLISH_SOILS = ("dust", "fingerprints", "streaks", "water_spots", "hairspray", "smudges")
VACUUM_MODES = ("carpet", "hard_floor")
VACUUM_SUCTION = ("low", "medium", "high")
VACUUM_TYPES = ("vacuum_cleaner", "upright_vacuum", "stick_vacuum", "canister_vacuum", "handheld_vacuum",
                "shop_vacuum")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ----------------------------------------------------------------------
# mess kinds (6.5)

def floor_grams(cells):
    """Grams of loose debris on a floor's cells ({"c,r": {kind: level}})."""
    total = 0.0
    for soil in (cells or {}).values():
        if not isinstance(soil, dict):
            continue
        for kind, level in soil.items():
            if kind in M.FLOOR_LOOSE and _number(level) and level > 0:
                total += M.grams(kind, level, CELL_AREA)
    return total


def pile_grams(rec):
    comp = rec.get("composition")
    return S.load_grams(comp) if isinstance(comp, dict) else 0.0


@mess_kind("debris", unit="g")
def debris_parcels(state):
    """(location key, origin, grams) of every debris parcel in a state (origin None: one group for the kind)."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict):
            continue
        cells = rec.get("_cells")
        if isinstance(cells, dict) and cells and S.is_floor(rec):
            grams = floor_grams(cells)
            loc = S.mess_loc(state, rec.get("location"))
            if grams > 0 and loc is not None:
                out.append((loc, None, grams))
        if rec.get("category") == "debris":
            grams = pile_grams(rec)
            loc = S.mess_loc(state, rec.get("location"), rec.get("relation"))
            if grams > 0 and loc is not None:
                out.append((loc, None, grams))
        grams = S.load_grams(rec.get("cl_load"))
        if grams > 0:
            out.append((("entity", eid), None, grams))
        for key, cls in (("drain_debris_g", "drain"), ("vent_debris_g", "entity")):
            value = rec.get(key)
            if _number(value) and value > 0:
                out.append(((cls, eid), None, float(value)))
    return out


def is_refuse(rec):
    """A waste item of this domain that is refuse at the start of an episode: not kept, not ambiguous, not
    sentimental or valuable, no one's belonging, not itself a bin or a liner."""
    if not (S.is_a(rec, "waste_item") or (rec.get("category") == "waste" and rec.get("waste_kind"))):
        return False
    if S.is_waste_container(rec) or rec.get("liner"):
        return False
    if rec.get("_disposition") == "keep" or rec.get("_ambiguous") or rec.get("sentimental") or rec.get("protected"):
        return False
    if rec.get("owner") or float(rec.get("value", 0.0) or 0.0) >= 50.0:
        return False
    return True


MESS_CATEGORIES = ("spill", "debris")
MESS_TYPES = ("spill", "debris_pile")


def is_mess_entity(rec):
    """This domain's mess entities (a spill, a debris pile): refuse for the ledger. Other domains' debris-category
    things (glass shards, shredded paper, a jammed sheet) are theirs to flag (their look-alikes must stay alike)."""
    return rec.get("category") in MESS_CATEGORIES and any(S.is_a(rec, name) for name in MESS_TYPES)


def used_liner(state, eid, rec):
    """A trash bag that already holds something, itself or as the liner of a bin that holds trash: it goes out with
    what is in it (pulling a full liner bags the bin's contents, waste.liner_removed)."""
    if not (rec.get("liner") or S.is_a(rec, "trash_liner")):
        return False
    entities = state.get("entities") or {}
    holders = [eid]
    host = entities.get(rec.get("location"))
    if rec.get("relation") == "part" and isinstance(host, dict) and S.is_waste_container(host):
        holders.append(rec["location"])
    for holder in holders:
        if S.load_grams((entities.get(holder) or {}).get("cl_load")) > 0:
            return True
        if any(r.get("location") == holder and r.get("relation") == "in" for r in entities.values()):
            return True
    return False


@hook("episode_start")
def mark_refuse(w, env):
    """Refuse and mess carry the kernel's waste flag from the baseline on (written before the baseline: setup):
    house.lost and house.removed exempt refuse (a soda can binned, a spill wiped up, a pile collected, a full trash
    bag taken out), the waste_item kind counts the refuse items. A floor's wetness starts equal to its film."""
    entities = w.s["entities"]
    for eid in sorted(entities):
        rec = entities[eid]
        if "waste" in rec:
            continue
        if is_refuse(rec) or is_mess_entity(rec) or used_liner(w.s, eid, rec):
            w.mut(eid)["waste"] = True
    for rid in sorted(w.s.get("rooms") or {}):
        if (w.s["rooms"][rid] or {}).get("water_film"):
            S.sync_floor_wetness(w, rid)


@mess_kind("waste_item", unit="count")
def waste_parcels(state):
    """(location key, origin, 1) of every refuse entity (waste: true) but this domain's spills and debris piles,
    which are mess of their own kinds (liquid, debris)."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        if isinstance(rec, dict) and rec.get("waste") is True and not is_mess_entity(rec):
            loc = S.mess_loc(state, rec.get("location"), rec.get("relation"))
            if loc is not None:
                out.append((loc, None, 1.0))
    return out


# ----------------------------------------------------------------------
# derived fields (8.2)

def _stream_verdict(state, eid):
    """(in_stream, stream_ok) of eid now, as waste.judge would give them (MISSING outside every waste container)."""
    from . import waste
    entities = state.get("entities") or {}
    rec = entities.get(eid)
    if not isinstance(rec, dict) or rec.get("location") is None:
        return MISSING, MISSING
    view = S.StateView(state)
    container = waste.waste_container_of(view, eid)
    if container is None or container == eid:
        return MISSING, MISSING
    crec = entities[container]
    stream = crec.get("waste_stream")
    if rec.get("category") == "debris" and not rec.get("portable"):
        return MISSING, MISSING
    rules = waste.rules(view)
    if rec.get("liner") or S.is_a(rec, "trash_liner") or rec.get("waste_kind") == "bag":
        inside = [c for c in view.children(eid) if entities[c].get("relation") == "in"]
        if stream == "recycling" and not rules.get("bagged_recycling") and inside:
            return stream, False
        if inside:
            return stream, True
    primary, ok, issues = waste.acceptable(view, eid, rules)
    return stream, bool(any(waste.container_accepts(view, container, s, rec) for s in ok))


@derived_field("in_stream")
def derived_in_stream(state, eid):
    return _stream_verdict(state, eid)[0]


@derived_field("stream_ok")
def derived_stream_ok(state, eid):
    return _stream_verdict(state, eid)[1]


@derived_field("polished")
def derived_polished(state, eid):
    """polished while the faces it was polished on stay free of what dulls a polish."""
    rec = (state.get("entities") or {}).get(eid)
    if not isinstance(rec, dict) or "polished" not in rec:
        return MISSING
    if not rec.get("polished"):
        return False
    parts = [rec.get("soil") if isinstance(rec.get("soil"), dict) else {}]
    for face_soil in (rec.get("soil_faces") or {}).values():
        if isinstance(face_soil, dict):
            parts.append(face_soil)
    for soil in parts:
        for kind in POLISH_SOILS:
            level = soil.get(kind)
            if _number(level) and level >= M.CLEAN:
                return False
    return True


# ----------------------------------------------------------------------
# settings skills (10.3)

def _set_steps(state, eid, name, options):
    record = (state.get("entities") or {}).get(eid) or {}
    current = (record.get("settings") or {}).get(name)
    listed = (record.get("setting_options") or {}).get(name) or list(options)
    return [(value, {"skill": "set", "target": eid, "setting": name, "value": value})
            for value in listed if value != current]


@settings_skill("settings.mode", types=VACUUM_TYPES)
def vacuum_mode_steps(state, eid):
    return _set_steps(state, eid, "mode", VACUUM_MODES)


@settings_skill("settings.suction", types=VACUUM_TYPES)
def vacuum_suction_steps(state, eid):
    return _set_steps(state, eid, "suction", VACUUM_SUCTION)


@settings_skill("locked", types=("waste_cart", "trash_cart", "recycling_cart", "yard_waste_cart", "organics_cart"))
def cart_lock_steps(state, eid):
    record = (state.get("entities") or {}).get(eid) or {}
    if not record.get("lockable"):
        return []
    value = not bool(record.get("locked"))
    return [(value, {"skill": "use", "action": "lock" if value else "unlock", "target": eid})]


@settings_skill("opened", types=("trash_liner", "trash_liner_4gal", "trash_liner_8gal", "trash_liner_13gal",
                                 "contractor_bag", "cart_liner", "compostable_liner"))
def liner_open_steps(state, eid):
    record = (state.get("entities") or {}).get(eid) or {}
    if record.get("opened"):
        return []           # an opened bag cannot be folded shut again
    return [(True, {"skill": "use", "action": "shake_out", "target": eid})]


# ----------------------------------------------------------------------
# unfamiliar things: one identify-first refusal on every cleaning verb

def unfamiliar_first(ctx):
    """Before hook (order 1) of every cleaning verb: an unfamiliar (novel) thing in any entity parameter -- the
    target, the tool, the bucket, the destination, what crumbs go into -- gets the kernel's identify-first refusal
    (state.refuse_unfamiliar) before any check reads its faces, liquid, material or affordances. Phase-1 close-out
    breakage row (held unfamiliar tools told apart by dust / scrub / absorb) and the twin sweep of every cleaning verb.
    No-op in oracle mode."""
    for pname, param in sorted(ctx.spec.params.items()):
        if param.get("type") not in ("entity", "target"):
            continue
        eid = ctx.args.get(pname)
        if isinstance(eid, str) and eid in ctx.w.s["entities"]:
            S.refuse_unfamiliar(ctx, eid)


for _name in sorted(n for n, spec in ACTIONS.items() if spec.fn.__module__.startswith(__package__ + ".")):
    before(_name, order=1)(unfamiliar_first)

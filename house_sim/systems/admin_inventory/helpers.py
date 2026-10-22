"""Scenario helpers (setup op {"op": "helper", "name": "admin_inventory.<name>", "args": {...}}).

They wire things the generic setup ops cannot express: parts seated in device slots, a working
printer with known paper/ink, realistic mail batches addressed from the household roster,
packages with fill and hidden parts, per-person trays, and curbside carts with a schedule.
Every helper returns a dict of bindings (ids) for later setup ops, predicates and plans.
"""
import copy
import datetime

from ...kernel import clock, geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from .common import DEFAULT_SURNAME, flat_spot, refresh_paper, slug
from .curb import is_cart
from .furnish import add_printer, install, reachable_from_door, safe_mount
from .mail import printed_face

MAIL_KINDS = {
    "bill": {"sender": "City Power & Light", "postage_class": "first_class", "markings": ["Statement enclosed"], "_mail_class": "bill", "_doc_important": True, "window": True},
    "official": {"sender": "Department of Revenue", "postage_class": "first_class", "markings": [], "_mail_class": "official", "_doc_important": True, "pii": False},
    "jury_summons": {"sender": "Clerk of the Superior Court", "postage_class": "first_class", "markings": ["OFFICIAL BUSINESS"], "_mail_class": "official", "_doc_important": True},
    "tax_notice": {"sender": "Internal Revenue Service", "postage_class": "first_class", "markings": [], "_mail_class": "official", "_doc_important": True},
    "personal": {"sender": "Grandma Rose", "postage_class": "first_class", "markings": [], "_mail_class": "personal", "_doc_important": True, "handwritten": True},
    "check": {"sender": "Acme Insurance Refunds", "postage_class": "first_class", "markings": ["Do not bend"], "_mail_class": "check", "_doc_important": True, "window": True},
    "medical": {"sender": "Riverside Clinic", "postage_class": "first_class", "markings": ["Confidential"], "_mail_class": "medical", "_doc_important": True, "sensitive": True},
    "junk": {"sender": "Mega Deals Outlet", "postage_class": "presorted_standard", "markings": ["SALE! 50% OFF"], "_mail_class": "junk", "_doc_important": False},
    "fake_urgent": {"sender": "Vehicle Services Dept.", "postage_class": "presorted_standard", "markings": ["FINAL NOTICE", "URGENT"], "_mail_class": "junk", "_doc_important": False},
    "credit_offer": {"sender": "Platinum Card Offers", "postage_class": "presorted_standard", "markings": ["Pre-approved"], "_mail_class": "junk", "_doc_important": False, "_pii": True, "shred_required": True},
    "catalog": {"sender": "Garden Supply Co.", "postage_class": "presorted_standard", "markings": [], "_mail_class": "catalog", "_doc_important": False, "type": "periodical", "poly_wrapped": True},
    "magazine": {"sender": "Home & Garden Monthly", "postage_class": "periodicals", "markings": [], "_mail_class": "magazine", "_doc_important": False, "type": "periodical"},
}


def _cfg_entity(ctx, eid):
    try:
        return ctx.config["entities"][eid]
    except KeyError:
        raise SpecError("Unknown entity " + str(eid))


def _room(ctx, value):
    rooms = ctx.config["rooms"]
    if value in rooms:
        return value
    options = sorted(r for r, v in rooms.items() if v.get("type") == value)
    if not options:
        raise SpecError("No room of type {} (add it to house.require)".format(value))
    return options[0]


def _person_name(ctx, pid):
    return ctx.config.get("people", {}).get(pid, {}).get("label", pid).split(" (")[0]


def _household(ctx):
    hh = ctx.config.setdefault("household", {})
    meta = ctx.config.get("metadata", {})
    hh.setdefault("address", meta.get("address", "100 Example Lane"))
    return hh


def _surname(ctx):
    return _household(ctx).get("surname", DEFAULT_SURNAME)


@scenario_helper("admin_inventory.install_part")
def install_part(ctx, device, slot, type, props=None, var="part"):
    """Seat a new part (roll, cartridge, staple strip, tissue box) in a device's slot."""
    rec = _cfg_entity(ctx, device)
    slots = rec.get("slots") or {}
    if slot not in slots:
        raise SpecError("{} has no slot {}".format(device, slot))
    if slots[slot].get("item"):
        old = slots[slot]["item"]
        ctx.config["entities"].pop(old, None)
    part = install(ctx.placer, device, slot, type, **(props or {}))
    part_rec = ctx.config["entities"][part]
    if part_rec.get("part_type") in ("toilet_paper_roll", "paper_towel_roll") and float(part_rec.get("remaining", 1.0)) <= 0:
        part_rec.update(waste_stream="recycling", label="empty cardboard core", material="cardboard", mass_kg=0.01)
    return {var: part}


def _office_rooms(ctx, room):
    """Rooms to look for a printer desk in: the requested room only (an explicit room is a requirement),
    else every office, then the shared rooms and bedrooms a home office corner ends up in."""
    if room:
        return [_room(ctx, room)]
    rooms = ctx.config["rooms"]
    order = ("office", "living_room", "study", "den", "family_room", "bedroom", "guest_room", "master_bedroom", "kitchen")
    out = []
    for rtype in order:
        out.extend(sorted(r for r, v in rooms.items() if v.get("type") == rtype and r not in out))
    if not out:
        out = sorted(r for r, v in rooms.items() if not v.get("outdoor"))
    return out


def _printer_on_some_desk(ctx, room, printer_args):
    """Put the printer on the first desk (else table) with a free spot that the robot can reach from the doorway;
    a busy or walled-off desk is skipped, and a room without a usable one gets a new desk."""
    for rid in _office_rooms(ctx, room):
        found = ctx.placer.find("desk", room=rid) + ctx.placer.find("table", room=rid)
        reach = [d for d in found if ctx.config["entities"][d].get("surface") and reachable_from_door(ctx.placer, d)]
        for desk in reach:
            ids = add_printer(ctx.placer, desk, ctx.rng, **printer_args)
            if ids is not None:
                return ids, desk
        desk = ctx.placer.furniture(rid, "desk")      # v2.3 Placer.furniture: the new desk is accessible
        if desk is not None:
            ids = add_printer(ctx.placer, desk, ctx.rng, **printer_args)
            if ids is not None:
                return ids, desk
            _remove_subtree(ctx, desk)
    raise SpecError("No reachable desk with room for a printer in {}".format(room or "any office or living area"))


@scenario_helper("admin_inventory.office")
def office(ctx, room=None, desk=None, kind="multifunction_printer", sheets=None, ink=None, feeder=True, files=None,
           tray_size="letter", guides="snug", tape_on=False, prefix=""):
    """A desk (found or added) with a working printer in a known state. Other printers are removed."""
    entities = ctx.config["entities"]
    for eid in sorted(entities):
        if entities.get(eid, {}).get("printer"):
            doomed = [eid]
            changed = True
            while changed:
                changed = False
                for other, rec in list(entities.items()):
                    if rec.get("location") in doomed and other not in doomed:
                        doomed.append(other)
                        changed = True
            for d in doomed:
                entities.pop(d, None)
    printer_args = dict(kind=kind, sheets=sheets, ink=ink, feeder=feeder, files=files)
    if desk is not None:
        ids = add_printer(ctx.placer, desk, ctx.rng, **printer_args)
        if ids is None:
            raise SpecError("No space for a printer on " + desk)
    else:
        ids, desk = _printer_on_some_desk(ctx, room, printer_args)
    tray = entities[ids["tray"]]
    tray["settings"] = {"paper_size": tray_size, "guides": guides}
    if tape_on:
        for key in ("ink_black", "ink_color"):
            if key in ids:
                entities[ids[key]]["tape_on"] = True
    ids["desk"] = desk
    from ...kernel.predicates import root_room
    ids["office_room"] = root_room(ctx.state(), desk)
    return {prefix + k: v for k, v in ids.items()}


@scenario_helper("admin_inventory.roster")
def roster(ctx, aliases=None, former_occupants=None, address=None, surname=None):
    """Household roster with aliases/maiden names, former occupants and the street address."""
    hh = _household(ctx)
    if address:
        hh["address"] = address
    if surname:
        hh["surname"] = surname
    entry = hh.setdefault("roster", {})
    for pid, names in sorted((aliases or {}).items()):
        entry.setdefault(pid, {"names": []})["names"] = list(names)
    if former_occupants is not None:
        hh["former_occupants"] = list(former_occupants)
    hh.setdefault("household_names", ["current resident", "the {} family".format(_surname(ctx).lower()), "{} household".format(_surname(ctx).lower())])
    return {"household_address": hh["address"]}


@scenario_helper("admin_inventory.mail_batch")
def mail_batch(ctx, location, pieces, var="mail", sealed=True):
    """Spawn mail pieces at location: pieces = [{"to": pid|"household"|"neighbor"|"former", "kind": <MAIL_KINDS>, "eid": ..., "props": {...}}]."""
    hh = _household(ctx)
    created, by_recipient, important, junk = [], {}, [], []
    for i, spec in enumerate(pieces):
        kind = spec.get("kind", "bill")
        if kind not in MAIL_KINDS:
            raise SpecError("Unknown mail kind " + kind)
        base = copy.deepcopy(MAIL_KINDS[kind])
        type_name = base.pop("type", "mail_piece")
        to = spec.get("to", "household")
        if to in ctx.config.get("people", {}):
            name = "{} {}".format(_person_name(ctx, to), _surname(ctx))
            address = hh["address"]
            addressee = to
        elif to == "household":
            name, address, addressee = "Current Resident", hh["address"], "household"
        elif to == "neighbor":
            number = "".join(c for c in hh["address"].split(" ")[0] if c.isdigit()) or "100"
            name = spec.get("name", "J. Ortiz")
            address = hh["address"].replace(number, str(int(number) + 2), 1)
            addressee = "neighbor"
        elif to == "former":
            name = spec.get("name", (hh.get("former_occupants") or ["K. Patel"])[0])
            address, addressee = hh["address"], "former_occupant"
            hh.setdefault("former_occupants", [])
            if name not in hh["former_occupants"]:
                hh["former_occupants"].append(name)
        else:
            raise SpecError("Unknown recipient " + str(to))
        if spec.get("name"):
            name = spec["name"]
        props = dict(base)
        # The label names only what is visible on the outside (who sent it); whether it is junk, a bill or a
        # fake "final notice" is the robot's judgment from the printed face (text), never given by the label.
        sender = base.get("sender") or "unknown sender"
        props.update(recipient=name, address=address, _addressee=addressee,
                     label="letter from {}".format(sender) if type_name == "mail_piece" else "{} from {}".format(kind, sender))
        props.update(spec.get("props", {}))
        if not (props.get("text") or props.get("label_text")):
            props["text"] = printed_face(props)
        secret = sorted({"_addressee", "_mail_class", "_doc_important", "_pii"} & set(props))
        props["secret_fields"] = secret
        eid = spec.get("eid") or "{}_{}".format(var, i + 1)
        if location in ctx.config["rooms"]:
            new = ctx.placer.floor_item(location, type_name, eid=eid, **props)
        else:
            loc = _cfg_entity(ctx, location)
            if loc.get("container"):
                new = ctx.placer.inside(location, type_name, eid=eid, **props)
            else:
                new = ctx.placer.on(location, type_name, eid=eid, **props)
        if new is None:
            raise SpecError("No room for mail at " + location)
        rec = ctx.config["entities"][new]
        if not sealed and type_name == "mail_piece":
            rec["sealed"], rec["open"] = False, True
        if type_name == "mail_piece":
            content_kind = {"bill": "bill", "check": "check", "official": "letter", "jury_summons": "letter", "tax_notice": "letter",
                            "medical": "medical", "personal": "letter"}.get(kind)
            if content_kind or spec.get("contents"):
                key = "{}_{}".format(new, "contents")
                doc_type = "check" if kind == "check" else "document"
                doc = ctx.placer.make(doc_type, new, doc_kind=content_kind or "letter", doc_key=key,
                                      title="{} from {}".format(kind.replace("_", " "), base.get("sender")),
                                      sheets=[[key, 1]], owner=addressee if addressee in ctx.config.get("people", {}) else None,
                                      _doc_important=base.get("_doc_important", False), sensitive=bool(base.get("sensitive") or kind == "check"),
                                      fold_style="z_fold", secret_fields=["_doc_important"])
                drec = ctx.config["entities"][doc]
                drec["relation"] = "in"
                refresh_paper(drec)
        created.append(new)
        by_recipient.setdefault(addressee, []).append(new)
        (important if base.get("_doc_important") else junk).append(new)
    return {var: created, var + "_important": important, var + "_junk": junk,
            **{"{}_to_{}".format(var, k): v for k, v in by_recipient.items()}}


@scenario_helper("admin_inventory.trays")
def trays(ctx, surface, owners=None, extra=None, var="tray", type="inbox_tray"):
    """One labeled tray per owner (and extra purposes like 'junk', 'household', 'action') on a surface."""
    out = {}
    for pid in owners or []:
        name = _person_name(ctx, pid)
        eid = ctx.placer.on(surface, type, eid="{}_{}".format(var, pid), owner=pid, label="{}'s mail tray".format(name),
                            label_text=name, label_key=slug(name), designated_for="mail")
        if eid is None:
            raise SpecError("No room for a tray on " + surface)
        out["{}_{}".format(var, pid)] = eid
    for purpose in extra or []:
        eid = ctx.placer.on(surface, type, eid="{}_{}".format(var, purpose), label="{} tray".format(purpose),
                            label_text=purpose.title(), label_key=slug(purpose), designated_for=purpose)
        if eid is None:
            raise SpecError("No room for a tray on " + surface)
        out["{}_{}".format(var, purpose)] = eid
    return out


def _paper_on_pile(ctx, surface_id, eid, values):
    """A busy desk (laptop, lamp, router, tape... from other domains) has no bare letter-size spot left:
    lay the paper on top of other flat paperwork, as people pile papers. Overlap is allowed only with
    flat items (common.FLAT_M); returns None when even that is impossible."""
    record = ctx.reg.make("document", surface_id, **values)
    spot = flat_spot(ctx.state(), surface_id, geometry.footprint_m(record))
    if spot is None:
        return None
    record["relation"] = "on"
    record["spos"] = spot
    ctx.config["entities"][eid] = record
    return eid


@scenario_helper("admin_inventory.document")
def document(ctx, location, key, title, kind="letter", pages=1, var="doc", eid=None, order=None, shelf=None, props=None):
    """A paper document with numbered sheets (order lets you scramble the page order). A dated document (props date,
    ISO) without printed text of its own prints its heading and the date as the household writes it (clock.format_date:
    "October 9, 2026" in the US, "9 October 2026" in the UK, TIER1_SWEEP 11.1); the read-tier `date` stays ISO, the
    value filing by date compares."""
    sheets = [[key, p] for p in (order or list(range(1, int(pages) + 1)))]
    values = dict(doc_kind=kind, doc_key=key, title=title, label=title.lower(), sheets=sheets)
    values.update(props or {})
    if values.get("date") and not values.get("text"):
        try:
            when = clock.format_date({"clock": ctx.config.get("clock") or {}, "metadata": ctx.config.get("metadata") or {}},
                                     str(values["date"])[:10], "long")
            values["text"] = "{} | {}".format(title, when)
        except ValueError:
            pass
    secret = sorted(k for k in values if k.startswith("_"))
    if secret:
        values["secret_fields"] = sorted(set(values.get("secret_fields", [])) | set(secret))
    eid = eid or "{}_{}".format(var, slug(key))
    p = ctx.placer
    if location in ctx.config["rooms"]:
        new = p.floor_item(location, "document", eid=eid, **values)
    else:
        loc = _cfg_entity(ctx, location)
        new = p.inside(location, "document", eid=eid, shelf=shelf, **values) if loc.get("container") else p.on(location, "document", eid=eid, **values)
        if new is None and not loc.get("container"):
            new = _paper_on_pile(ctx, location, eid, values)
    if new is None:
        raise SpecError("No room for document at " + str(location))
    refresh_paper(ctx.config["entities"][new])
    parent = ctx.config["entities"].get(location)
    if parent is not None and "order" in parent:
        parent["order"] = list(parent.get("order") or []) + [new]
    return {var: new}


PACKAGE_SURFACES = ("shoe_rack", "bench", "storage_bench", "console_table", "side_table", "hall_table", "dresser",
                    "table", "cabinet", "shelf_unit")
PACKAGE_SURFACE_MAX_M = 1.0     # a top a box is set on by hand and read from above (below a standing robot's eye)


def _package_on_furniture(ctx, rid, type_name, eid, values):
    """A package set on a low furniture top of room rid the robot can get to from the doorways (PACKAGE_SURFACES in
    that order, at most PACKAGE_SURFACE_MAX_M high), or None."""
    for kind in PACKAGE_SURFACES:
        for sid in ctx.placer.find(kind, room=rid):
            rec = ctx.config["entities"][sid]
            if rec.get("location") != rid or not rec.get("pos") or not reachable_from_door(ctx.placer, sid):
                continue
            if geometry.height_m(ctx.state(), sid) > PACKAGE_SURFACE_MAX_M:
                continue
            pkg = ctx.placer.on(sid, type_name, eid=eid, **values)
            if pkg is not None:
                return pkg
    return None


@scenario_helper("admin_inventory.package")
def package(ctx, location, to="household", var="pkg", type="package_box", contents=None, fill=None, props=None, eid=None, cell=None):
    """A delivered package (sealed) addressed from the roster, with contents and fill (fill may hide parts).
    On a room floor it also binds `<var>_cell` (its cell, a `near` for spawning a bin beside it)."""
    hh = _household(ctx)
    p = ctx.placer
    values = {"recipient": "", "address": hh["address"], "tape_sealed": True, "open": False}
    if to in ctx.config.get("people", {}):
        values["recipient"] = "{} {}".format(_person_name(ctx, to), _surname(ctx))
        values["_addressee"] = to
    elif to == "household":
        values["recipient"] = "{} household".format(_surname(ctx))
        values["_addressee"] = "household"
    elif to == "neighbor":
        number = "".join(c for c in hh["address"].split(" ")[0] if c.isdigit()) or "100"
        values["recipient"] = "J. Ortiz"
        values["address"] = hh["address"].replace(number, str(int(number) + 2), 1)
        values["_addressee"] = "neighbor"
    values.update(props or {})
    values["secret_fields"] = sorted(set(values.get("secret_fields", [])) | {"_addressee"})
    eid = eid or var
    if location in ctx.config["rooms"]:
        pkg = p.floor_item(location, type, eid=eid, near=cell, **values)
        if pkg is None:
            # no floor left in a small, furnished room (a 2 x 3 m entryway with a shoe rack and an umbrella stand):
            # the box goes on top of a reachable low piece of furniture there, as a courier or a resident leaves it
            pkg = _package_on_furniture(ctx, location, type, eid, values)
    else:
        loc = _cfg_entity(ctx, location)
        pkg = p.inside(location, type, eid=eid, **values) if loc.get("container") else p.on(location, type, eid=eid, **values)
    if pkg is None:
        raise SpecError("No room for the package at " + str(location))
    out = {var: pkg, var + "_contents": [], var + "_hidden": [], var + "_fill": []}
    if ctx.config["entities"][pkg].get("pos"):
        out[var + "_cell"] = list(ctx.config["entities"][pkg]["pos"])   # use as `near` for things set beside it
    for i, item in enumerate(contents or []):
        new = p.make(item.get("type", "generic_item"), pkg, eid=item.get("eid") or "{}_item_{}".format(var, i + 1), **dict(item.get("props", {})))
        ctx.config["entities"][new]["relation"] = "in"
        out[var + "_contents"].append(new)
    for i, f in enumerate(fill or []):
        fid = p.make(f.get("type", "packing_fill"), pkg, eid=f.get("eid") or "{}_fill_{}".format(var, i + 1), **dict(f.get("props", {})))
        frec = ctx.config["entities"][fid]
        frec.update(relation="in", openable=True, open=False)
        out[var + "_fill"].append(fid)
        for j, hidden in enumerate(f.get("hidden", [])):
            hid = p.make(hidden.get("type", "hardware_bag"), fid, eid=hidden.get("eid") or "{}_hidden_{}_{}".format(var, i + 1, j + 1),
                         **dict(hidden.get("props", {})))
            ctx.config["entities"][hid]["relation"] = "in"
            out[var + "_hidden"].append(hid)
    return out


def _remove_subtree(ctx, eid):
    doomed = [eid]
    changed = True
    while changed:
        changed = False
        for other, rec in sorted(ctx.config["entities"].items()):
            if rec.get("location") in doomed and other not in doomed:
                doomed.append(other)
                changed = True
    for other in doomed:
        ctx.config["entities"].pop(other, None)


CART_TYPES = {"trash": "trash_cart", "recycling": "recycling_cart", "yard_waste": "yard_waste_cart"}


def _cart_rooms(ctx, rid):
    """Where wheeled carts can live: the requested room, else (a full garage) the rooms next to it, outdoor
    ones first (carts stand on the driveway or beside the house), else other rooms of the same type."""
    out = [rid]
    neighbours = []
    for _, conn in sorted(ctx.config.get("connections", {}).items()):
        if rid in conn.get("cells", {}) and not conn.get("needs_ladder"):
            neighbours.extend(r for r in sorted(conn["cells"]) if r != rid)
    rooms = ctx.config["rooms"]
    neighbours = sorted(set(neighbours), key=lambda r: (not rooms[r].get("outdoor"), rooms[r].get("type") == "sidewalk", r))
    out.extend(r for r in neighbours if rooms[r].get("type") not in ("sidewalk", "street"))
    out.extend(sorted(r for r, v in rooms.items() if v.get("type") == rooms[rid].get("type") and r not in out))
    return out


@scenario_helper("admin_inventory.carts")
def carts(ctx, room="garage", streams=None, var="cart", schedule=None, fills=None):
    """Wheeled waste carts at their home cells in a room, plus a collection schedule. Uses the cleaning domain's
    cart types when loaded (replacing furnished carts of the same streams), else generic items with cart props.
    All carts share one home room: the requested one, or the nearest room with space for every cart."""
    from .curb import is_cart
    rid = _room(ctx, room)
    out = {}
    streams = list(streams or ["trash", "recycling"])
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"].get(eid)
        if rec is not None and is_cart(rec) and rec.get("waste_stream") in streams:
            _remove_subtree(ctx, eid)

    def props_for(stream):
        return dict(label="{} cart".format(stream), category="waste_cart", wheeled_cart=True, waste_stream=stream,
                    accepts_items=True, container=True, openable=True, open=False, portable=True,
                    mass_kg=12.5, size_m=[0.6, 0.73, 1.07], volume_l=240.0, capacity_l=240.0,
                    inner_size_m=[0.55, 0.65, 0.95], material="hdpe", blocks_path=True, fill=0.0, two_handed=True)

    placed, home = [], None
    for target in _cart_rooms(ctx, rid):
        placed = []
        for stream in streams:
            type_name = CART_TYPES.get(stream, "generic_item")
            if not ctx.reg.known(type_name):
                type_name = "generic_item"
            eid = ctx.placer.furniture(target, type_name, eid="{}_{}".format(var, stream), wall=True, rot=0, **props_for(stream))
            if eid is None:
                break
            placed.append((stream, eid))
        if len(placed) == len(streams):
            home = target
            break
        for _, eid in placed:
            _remove_subtree(ctx, eid)
    if home is None:
        raise SpecError("No room for {} carts in or next to {}".format(len(streams), rid))
    for stream, eid in placed:
        rec = ctx.config["entities"][eid]
        rec["home"] = home
        rec["home_cell"] = list(rec["pos"])
        n = (fills or {}).get(stream, 2)
        for j in range(int(n)):
            bag = ctx.placer.make("generic_item", eid, eid="{}_{}_bag_{}".format(var, stream, j + 1), label="full {} bag".format(stream),
                                  category="bag", mass_kg=4.0, volume_l=50.0, size_m=[0.45, 0.35, 0.6], waste_stream=stream, portable=True)
            ctx.config["entities"][bag]["relation"] = "in"
        rec["fill"] = round(min(1.0, n * 50.0 / 240.0), 2)
        out["{}_{}".format(var, stream)] = eid
    if schedule is not None:
        ctx.config.setdefault("household", {})["collection"] = copy.deepcopy(schedule)
        from .curb import add_calendar_events, collection_events
        add_calendar_events(ctx.config, collection_events(ctx.config))     # the schedule is on the household calendar
    curbs = sorted(r for r, v in ctx.config["rooms"].items() if v.get("type") == "sidewalk")
    out["curb"] = curbs[0] if curbs else None
    out["cart_room"] = home
    return out


@scenario_helper("admin_inventory.calendar")
def calendar(ctx, collection=True, pickups=None, sources=None):
    """The household calendar entries this domain keeps (TIER1_SWEEP 11.2; the people domain renders and answers
    them): the collection rule's days (curb.collection_events: one recurring event per stream) and carrier pickups
    [{"at": ISO date-time, "spot": entity or room id, "carrier": name?, "id": event id?}], each also scheduled as an
    admin_parcel_pickup event at that time. sources: household.calendar sources to merge (wall_calendar / whiteboard
    ids, app true). Returns {"pickup_<n>_s": seconds from the episode start}."""
    from .curb import add_calendar_events, collection_events
    events = collection_events(ctx.config) if collection else []
    start = datetime.datetime.fromisoformat(str((ctx.config.get("clock") or {}).get("start") or "2026-10-09T10:00:00"))
    out = {}
    for n, pickup in enumerate(pickups or []):
        if not isinstance(pickup, dict) or not pickup.get("at") or not pickup.get("spot"):
            raise SpecError("admin_inventory.calendar: a pickup needs at (ISO date-time) and spot")
        at = datetime.datetime.fromisoformat(str(pickup["at"]))
        at_s = (at - start).total_seconds()
        if at_s < 0:
            raise SpecError("admin_inventory.calendar: pickup {} is before the episode starts".format(pickup["at"]))
        carrier = str(pickup.get("carrier") or "parcel carrier")
        events.append({"id": pickup.get("id") or "admin_pickup_{}".format(n + 1), "kind": "carrier_pickup",
                       "title": "{} pickup".format(carrier[:1].upper() + carrier[1:]), "start": at.isoformat(timespec="minutes"),
                       "window": pickup.get("window"), "attributes": {"carrier": carrier}, "known_by": "everyone"})
        if events[-1]["window"] is None:
            events[-1].pop("window")
        ctx.config.setdefault("events", []).append({"at_s": round(at_s, 3), "effect": "admin_parcel_pickup",
                                                    "args": {"spot": pickup["spot"]}})
        out["pickup_{}_s".format(n + 1)] = round(at_s, 3)
    add_calendar_events(ctx.config, events)
    if sources:
        ctx.config["household"]["calendar"].setdefault("sources", {}).update(copy.deepcopy(sources))
    return out


@scenario_helper("admin_inventory.door_cell")
def door_cell(ctx, room, var="door_cell"):
    """A doorway cell of a room (always connected to the rest of the house): use it as `near` for floor spawns."""
    rid = _room(ctx, room)
    cells = geometry.doorway_cells(ctx.state(), rid)
    if not cells:
        raise SpecError("Room {} has no doorway".format(rid))
    return {var: list(cells[0]), var + "_room": rid}


def _has_room_for(ctx, eid, fits):
    """True if every type in `fits` can be put on the surface (or inside the container) one after another:
    a free surface spot for each (geometry.surface_spot), or an inner size and capacity that take each."""
    if not fits:
        return True
    rec = ctx.config["entities"][eid]
    trial = []
    try:
        for i, type_name in enumerate(fits):
            tid = "_fit_probe_{}".format(i)
            probe = ctx.reg.make(type_name, eid)
            if rec.get("surface"):
                probe["relation"] = "on"
                ctx.config["entities"][tid] = probe
                trial.append(tid)
                spot = geometry.surface_spot(ctx.state(), eid, tid)
                if spot is None:
                    return False
                probe["spos"] = spot
            elif rec.get("container"):
                if not geometry.fits_inside(probe.get("size_m") or [0.1, 0.1, 0.1], rec.get("inner_size_m")):
                    return False
            else:
                return False
        return True
    finally:
        for tid in trial:
            ctx.config["entities"].pop(tid, None)


def _reachable_in_room(ctx, rid, types, fits=()):
    """First entity of `types` standing in room rid that the robot can get to from the room's doorways (the kernel's
    access rule; other domains' appliances may stand in front of wall shelving) and, with `fits`, that has room
    for those item types."""
    for type_name in types:
        for eid in ctx.placer.find(type_name, room=rid):
            rec = ctx.config["entities"][eid]
            if rec.get("location") != rid or not rec.get("pos"):
                continue
            if reachable_from_door(ctx.placer, eid) and _has_room_for(ctx, eid, fits):
                return eid
    return None


@scenario_helper("admin_inventory.reachable_surface")
def reachable_surface(ctx, room=None, types=("counter", "table", "desk", "dresser", "shelf_unit", "closet_shelving"), var="surface",
                      fallback="wall_shelf", rooms=None, room_var=None, add=None, fits=None):
    """The first furniture surface/shelf of the given types in a room that the robot can get to from the room's
    doorways (falls back to mounting a wall shelf at a reachable wall cell).
    `rooms` (ids or room types, tried in order) searches several rooms; `room_var` also binds the chosen room.
    `add` (a furniture type, e.g. "desk") is placed in the first room that takes it before the wall-shelf fallback.
    `fits` (list of type names) skips surfaces that other furnishers crowded too much to take all of those items."""
    candidates = []
    for value in (list(rooms) if rooms else [room]):
        if value in ctx.config["rooms"]:
            candidates.append(value)
        else:
            candidates.extend(sorted(r for r, v in ctx.config["rooms"].items() if v.get("type") == value))
    if not candidates:
        raise SpecError("No room of type {} (add it to house.require)".format(rooms or room))
    def found(eid, rid):
        out = {var: eid}
        if room_var:
            out[room_var] = rid
            out[room_var + "_label"] = str(ctx.config["rooms"][rid].get("label") or ctx.config["rooms"][rid].get("type", rid)).replace("_", " ").lower()
        return out

    fits = list(fits or [])
    for rid in candidates:
        eid = _reachable_in_room(ctx, rid, types, fits)
        if eid is not None:
            return found(eid, rid)
    if add:
        for rid in candidates:
            eid = ctx.placer.furniture(rid, add)      # v2.3 Placer.furniture: the new piece is accessible
            if eid is not None and _has_room_for(ctx, eid, fits):
                return found(eid, rid)
            if eid is not None:
                _remove_subtree(ctx, eid)
    if fallback:
        for rid in candidates:
            eid = safe_mount(ctx.placer, rid, fallback, height=1.0, label="added shelf", size_m=[0.8, 0.25, 0.03], orient=True)
            if eid is not None and _has_room_for(ctx, eid, fits):
                return found(eid, rid)
            if eid is not None:
                _remove_subtree(ctx, eid)
    raise SpecError("No reachable {} in {}".format("/".join(types), ", ".join(candidates)))


@scenario_helper("admin_inventory.wall_mount")
def wall_mount(ctx, room, type="wall_shelf", height=1.0, var="mounted", near=None, props=None, eid=None):
    """Mount a wall shelf/holder/organizer where the robot can reach it: a wall cell clear of door zones,
    floor-standing mounts and (below counter height) furniture, within reach of doorway-reachable floor.
    `near` (an entity id or a cell) keeps it close to something (a holder beside the toilet). Unlike the
    generic `mount` spawn, it never falls back to a random wall cell; SpecError when the room has no such wall."""
    rid = _room(ctx, room)
    target = None
    if isinstance(near, str) and near in ctx.config["entities"]:
        target = sorted(geometry.footprint(ctx.config["entities"][near])) or None
    elif near is not None:
        target = [list(near)]
    new = safe_mount(ctx.placer, rid, type, height=height, near=target, eid=eid, orient=True, **dict(props or {}))
    if new is None:
        raise SpecError("No reachable wall for a {} in {}".format(type, rid))
    return {var: new}


@scenario_helper("admin_inventory.stock_pack")
def stock_pack(ctx, location, type="toilet_paper_pack", unit="toilet_paper_roll", count=None, var="pack", shelf=None, sealed=True, props=None):
    """A sealed multipack holding real unit entities (rolls) on a shelf or surface."""
    p = ctx.placer
    loc = ctx.config["entities"].get(location)
    values = dict(props or {})
    if count is not None:
        values["pack_count"] = int(count)
    if not sealed:
        values.update(open=True, sealed=False)
    pack = p.inside(location, type, eid=var, shelf=shelf, **values) if loc is not None and loc.get("container") else \
        (p.on(location, type, eid=var, **values) if loc is not None else p.floor_item(_room(ctx, location), type, eid=var, **values))
    if pack is None:
        raise SpecError("No room for {} at {}".format(type, location))
    n = int(count if count is not None else ctx.config["entities"][pack].get("pack_count", 6))
    units = []
    for i in range(n):
        u = p.make(unit, pack, eid="{}_{}_{}".format(var, unit, i + 1))
        ctx.config["entities"][u]["relation"] = "in"
        units.append(u)
    return {var: pack, var + "_units": units}

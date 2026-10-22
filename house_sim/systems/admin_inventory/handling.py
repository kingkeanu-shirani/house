"""General handling consequences for stored goods and shelves:

* FIFO depth on deep shelves: whatever is set on a shelf lands in front (depth_rank 0); the
  rotate_stock action re-orders a shelf so the oldest stock is at the front;
* hygienic grip zones: grabbing a toothbrush (or similar) anywhere but its handle contaminates it;
* bags and boxes give way at the handle or bottom when lifted overloaded (the kernel's handle law: torn, the
  contents fall from hand height);
* clearance under beds/furniture: a bin taller than the clearance cannot go under, a forced fit
  lifts the slats;
* hot items placed into (or onto) plastic/wood/fabric/paper containers melt or scorch them at the kernel's heat
  limit of that material;
* wet items set on wooden floors leave water rings;
* bag and box load/fill fields kept current for packing limits;
* scripted household events: residents moving things offscreen and consuming stock.
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import impact as phys_impact, lawbook, statics as phys_statics, support as phys_support
from ...kernel.phys import props as phys_props, thermal as phys_thermal
from ...kernel.plugins import after, before, hook
from ..core import subtree_mass
from .common import admin_facts, drop_cell, sheet_count
from .common import require_readable
from .perceive import dumps, hide_on, order_ids, realism_of, tier_on

LOAD_TRACKED = ("luggage", "package", "organizer", "box", "bag")
HEAT_MARK_SPAN_K = phys_thermal.CONTACT_SPAN_K     # a mark's severity: (contact - limit) / 100 K, the kernel's scale
IGNITES_C = 230.0           # paper and cardboard char and catch near 230-260 C (autoignition of paper about 233 C)
WOOD_FLOORS = ("hardwood", "wood", "laminate", "hardwood_unsealed")


# ----------------------------------------------------------------------
# FIFO depth on shelves

def _shelf_peers(w, container, shelf):
    return [c for c in w.children(container) if w.ent(c).get("relation") == "in" and w.ent(c).get("shelf", 0) == shelf]


def _date_key(rec):
    for field in ("expiration", "best_by", "lot", "date", "purchased_on", "packed_on"):
        if rec.get(field):
            return str(rec[field])
    return "9999"


@after("place", order=95)
def _front_of_shelf(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent_id = rec.get("location")
    parent = w.s["entities"].get(parent_id)
    if parent is None or rec.get("relation") != "in" or not (parent.get("shelf_heights") or parent.get("deep_shelf")):
        return
    peers = [p for p in _shelf_peers(w, parent_id, rec.get("shelf", 0)) if p != eid]
    ranked = sorted(peers, key=lambda p: (w.ent(p).get("depth_rank", 0), p))
    for i, peer in enumerate(ranked):
        if w.ent(peer).get("depth_rank") != i + 1:
            w.mut(peer)["depth_rank"] = i + 1
    w.mut(eid)["depth_rank"] = 0


@action("rotate_stock", params={"target": "entity", "shelf": "?int", "what": "?str"},
        duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.006, category="physical",
        doc="Re-arrange the items on one shelf of a cabinet/pantry/fridge so the oldest date (expiration, best-by, lot) is at the front (FIFO). Reads each date label.")
def rotate_stock(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("shelf_heights") or rec.get("deep_shelf"), "It has no shelves to rotate", "argument")
    require(not rec.get("openable") or rec.get("open"), "Open it first", "precondition")
    shelf = ctx.args.get("shelf") or 0
    peers = _shelf_peers(w, target, shelf)
    if ctx.realistic:
        # rotating reads every date label: it needs light and a clean camera, and the robot then knows those labels
        require_readable(ctx)
    if ctx.args.get("what"):
        key = ctx.args["what"].lower()
        peers = [p for p in peers if key in _shown_name(ctx, p)]
    require(peers, "Nothing on that shelf to rotate", "precondition")
    if ctx.realistic:
        for p in peers:
            ctx.grant(p, "read")
    # equal dates keep a neutral order (by handle in realistic mode: true ids would rank look-alikes by type)
    rank = {p: i for i, p in enumerate(order_ids(ctx.env, peers))}
    ordered = sorted(peers, key=lambda p: (_date_key(w.ent(p)), rank[p]))
    for i, item in enumerate(ordered):
        if w.ent(item).get("depth_rank") != i:
            w.mut(item)["depth_rank"] = i
    ctx.data["seconds"] = 5.0 + 3.0 * len(peers)
    return dumps(ctx.env, {"front_to_back": ordered})


def _shown_name(ctx, eid):
    """What `what` is matched against: the sku and label as the robot reads them (realistic: the identity a read
    gives -- an unlabeled look-alike stays its descriptor, and a sku counts only if it is printed on the item)."""
    rec = ctx.w.ent(eid)
    realism = realism_of(ctx.env)
    if realism is None:
        return (str(rec.get("sku", "")) + " " + str(rec.get("label", ""))).lower()
    from ...kernel.realism import READ
    ident = realism.identity(eid, rec, max(READ, realism.knowledge.entity_level(eid, rec)))
    words = [str(ident.get("label") or ""), str(ident.get("kind") or "")]
    if "candidates" not in ident and (rec.get("label_text") or rec.get("text") or rec.get("printed") or rec.get("sku")):
        words.append(str(rec.get("sku", "")))
    return " ".join(words).lower()


# ----------------------------------------------------------------------
# hygiene and handling on pick-up

@after("pick_up", order=80)
def _pickup_consequences(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    zone = rec.get("grip_zone")
    if zone and ctx.args.get("grip") not in (zone, "handle") and not rec.get("contaminated"):
        w.mut(eid)["contaminated"] = True
        hide_on(w, eid, ["contaminated"])         # germs from the wrong grip are invisible
        w.emit("hygiene_breach", target=eid)
    if eid in w.s["entities"] and w.s["entities"][eid].get("location") == "robot":
        _handle_check(ctx, eid)
    _refresh_load(w, ctx.data.get("admin_prev_loc"))


# Carry-handle ratings (kg of contents) of the luggage generated houses already hold (the furnisher's suitcase and
# children's backpacks). They reach the kernel through a phys.props hook so generated records stay as they were
# (generation-1 houses unchanged); other bags state theirs in the type data (handle_rating_kg).
LUGGAGE_HANDLE_KG = {"suitcase": 40.0,     # a checked suitcase's carry handles hold well over the 32 kg airline limit
                     "backpack": 15.0}     # a school daypack lifted by its haul loop: the seams bear about 15 kg


def _luggage_handle_props(record, phys):
    """phys.props hook (TIER1_PHYSICS 7.3): LUGGAGE_HANDLE_KG as the kernel's handle_kg, unless something already set
    one (the record's own handle_rating_kg / max_load_kg, a supports.json row). A domain hook registered later (the
    manipulation domain: a load held from underneath loads no handle) still has the last word."""
    if phys.get("handle_kg") is not None:
        return None
    return {"handle_kg": LUGGAGE_HANDLE_KG[record["type"]]}


_luggage_handle_props.applies = lambda record: record.get("type") in LUGGAGE_HANDLE_KG
hook("phys.props")(_luggage_handle_props)


def _handle_check(ctx, eid):
    """A bag or box lifted with more inside than its handle or bottom bears gives way (TIER1_PHYSICS 5.2 handle law,
    applied here while the law is deferred; the settle pass applies it itself once it is active): torn, it stays in
    the hand and its contents fall from hand height (kernel impact: breakage by drop tolerance and padding), and at
    90-100 % of the rating the kernel's creep check tears it if it is still carried that heavy two minutes later. The
    rating is the kernel's (phys.props handle_kg: the record's handle_rating_kg, else a portable container's
    max_load_kg, else a supports.json row such as a cardboard box; a load held from underneath loads no handle when
    the manipulation domain says so)."""
    if phys_statics.handle_status(ctx.w.s, eid) is not None and not lawbook.active("handle"):
        phys_impact.handle_law(ctx.w, eid, env=ctx.env)


# ----------------------------------------------------------------------
# placement consequences

@before("place", order=60)
def _clearance_under(ctx):
    w = ctx.w
    if ctx.args.get("relation") != "under":
        return
    parent = w.s["entities"].get(ctx.args.get("destination"))
    item = w.s["entities"].get(ctx.args.get("target"))
    if parent is None or item is None or parent.get("under_clearance_m") is None:
        return
    height = min(float(v) for v in (item.get("size_m") or [0.1, 0.1, 0.1])[:3]) if item.get("lay_flat") else float((item.get("size_m") or [0, 0, 0.1])[2])
    clearance = float(parent["under_clearance_m"])
    if height > clearance + 0.05:
        if ctx.realistic:
            # the attempt shows it does not fit; heights in centimetres need a tape measure
            raise ActionError("It is too tall to fit under there", "precondition")
        raise ActionError("It is {:.0f} cm tall; only {:.0f} cm of clearance under there".format(height * 100, clearance * 100), "precondition")
    ctx.data["admin_forced_under"] = height > clearance


@after("place", order=96)
def _placement_consequences(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent_id = rec.get("location")
    parent = w.s["entities"].get(parent_id)
    if ctx.data.get("admin_forced_under") and parent is not None:
        pm = w.mut(parent_id)
        pm["slats_lifted"] = True
        pm["damaged"] = True
        w.stat("damage")
        w.emit("forced_under", target=eid, parent=parent_id)
    if parent is not None and float(rec.get("temperature_c", 21.0)) > 60.0:
        _hot_contact(ctx, eid, parent_id)
    if parent is None and parent_id in w.s["rooms"] and float(rec.get("wetness", 0.0)) > 0.5:
        room = w.s["rooms"][parent_id]
        if room.get("floor_material") in WOOD_FLOORS and not room.get("outdoor"):
            floor = "floor_" + parent_id
            if floor in w.s["entities"]:
                fm = w.mut(floor)
                fm["water_marks"] = int(fm.get("water_marks", 0)) + 1
                w.emit("water_ring", target=floor, item=eid)
    if parent is not None and rec.get("relation") == "in":
        _terminal_short(w, eid, parent_id)
    _refresh_load(w, parent_id)


def _hot_contact(ctx, eid, parent_id):
    """A hot item set into (or onto) a container or support hotter than that material bears marks it (TIER1_PHYSICS
    5.9): the limit is the support's max_safe_temp_c, else the kernel's heat limit of its finish (phys.thermal
    .heat_limit_c: rigid plastic 100 C, film 80 C, paper and cardboard ...), the face temperature the kernel's
    (a vessel's wall is cooler than its liquid) and the mark the kernel's (melted for plastics, film, rubber, foam
    and wax; a heat ring in a wood finish below 150 C; else scorched), recorded as damage.<mark> with the kernel's
    severity, plus heat_damaged. Resting ON a support is the kernel's contact_heat law, which owns it once active;
    INSIDE a container the kernel has no contact rule, so this domain keeps it whatever the law's state. Paper and
    cardboard touched by something above about 230 C catch (stat fire)."""
    w = ctx.w
    state = w.s
    parent = state["entities"][parent_id]
    if state["entities"][eid].get("relation", "on") == "on" and lawbook.active("contact_heat"):
        return      # kernel owns it (contact_heat law)
    if parent.get("heat_damaged"):
        return
    temp = phys_thermal.contact_temp_c(state, eid)
    limit = phys_support.finite(parent.get("max_safe_temp_c"))
    if limit is None:
        limit = phys_thermal.heat_limit_c(state, parent_id)
    if limit is None or temp <= limit:
        return
    cls = phys_props.props(state, parent_id).cls
    mark = phys_thermal.contact_mark(cls, temp)
    pm = w.mut(parent_id)
    pm["heat_damaged"] = True
    pm["damaged"] = True
    phys_impact.damage(w, parent_id, mark, min(1.0, (temp - limit) / HEAT_MARK_SPAN_K), detail="hot item inside",
                       by="robot")
    if cls in ("paper", "cardboard") and temp > IGNITES_C:
        w.stat("fire")
    w.emit("container_heat_damage", target=parent_id, item=eid, temperature_c=round(temp, 1), mark=mark)


CONDUCTIVE = ("metal", "steel", "steel_wool", "aluminum", "brass", "copper", "coins")


def _terminal_short(w, eid, container):
    """Loose 9 V / lithium cells with exposed terminals shorted by keys, coins or steel wool in the same container."""
    peers = [c for c in w.children(container) if w.ent(c).get("relation") == "in"]
    cells = [c for c in peers if w.ent(c).get("terminals_exposed") and not w.ent(c).get("terminals_taped")]
    metal = [c for c in peers if w.ent(c).get("material") in CONDUCTIVE and not w.ent(c).get("terminals_exposed")]
    if not cells or not metal or eid not in cells + metal:
        return
    for cell in cells:
        if not w.ent(cell).get("shorted"):
            cm = w.mut(cell)
            cm["shorted"] = True
            cm["temperature_c"] = max(float(cm.get("temperature_c", 21.0)), 75.0)
            w.stat("electrical_hazard")
            w.emit("battery_short", target=cell, container=container, metal=metal[:3])


def _refresh_load(w, container):
    rec = w.s["entities"].get(container)
    if rec is None or rec.get("category") not in LOAD_TRACKED and not rec.get("ring_binder"):
        return
    load = round(subtree_mass(w, container) - float(rec.get("mass_kg", 0.0)), 3)
    fill = round(sum(float(w.ent(c).get("volume_l", 0.0)) for c in w.children(container) if w.ent(c).get("relation") == "in"), 3)
    if rec.get("load_kg") != load or rec.get("fill_l") != fill:
        if "load_kg" not in rec:
            tier_on(w, container, "measure", ["load_kg"])     # what a bag holds in kg needs a scale (any bag or box)
        m = w.mut(container)
        m["load_kg"] = load
        m["fill_l"] = fill


# ----------------------------------------------------------------------
# scripted household events

@hook("event:admin_relocate")
def _event_relocate(w, args, env):
    """A resident moves an item offscreen: args {item, to, relation?, shelf?, by?}."""
    item, to = args["item"], args["to"]
    if item not in w.s["entities"]:
        return False
    m = w.mut(item)
    before_loc = m.get("location")
    for key in ("spos", "pos", "shelf", "held_hand", "revealed"):
        m.pop(key, None)
    m["location"] = to
    if to in w.s["rooms"]:
        m["relation"] = "on"
        # a resident sets it down where it cuts no walking route (kernel keep_access), else anywhere it fits
        m["pos"] = (geometry.free_floor_cell(w.s, to, ignore=(item,), record=m, keep_access=True)
                    or geometry.free_floor_cell(w.s, to, ignore=(item,), record=m)
                    or drop_cell(w, to, None, item))
    else:
        parent = w.ent(to)
        relation = args.get("relation") or ("in" if parent.get("container") or parent.get("openable") else "on")
        m["relation"] = relation
        if relation == "on" and parent.get("accepts_items"):
            spot = geometry.surface_spot(w.s, to, item)
            if spot is not None:
                m["spos"] = spot
        if relation == "in" and parent.get("shelf_heights"):
            m["shelf"] = int(args.get("shelf", 0))
    w.log_move(item, before_loc, to, by=args.get("by", "resident"))
    admin_facts(w).setdefault("offscreen_moves", []).append({"item": item, "to": to, "by": args.get("by", "resident"), "time_s": w.s["time_s"]})
    return True


@hook("event:admin_consume")
def _event_consume(w, args, env):
    """A resident uses up stock: args {items: [ids]} (removed) or {item, amount_l} / {item, units}."""
    used = []
    for item in args.get("items", []):
        if item in w.s["entities"]:
            for child in w.descendants(item):
                w.destroy(child)
            w.destroy(item)
            used.append(item)
    item = args.get("item")
    if item in w.s["entities"]:
        m = w.mut(item)
        if args.get("amount_l") is not None and m.get("liquid_container"):
            m["liquid_l"] = round(max(0.0, float(m.get("liquid_l", 0.0)) - float(args["amount_l"])), 4)
        if args.get("units") is not None and "units_count" in m:
            m["units_count"] = max(0, int(m["units_count"]) - int(args["units"]))
        if args.get("remaining") is not None:
            m["remaining"] = float(args["remaining"])
        if args.get("tissues") is not None:
            m["tissues"] = max(0, int(m.get("tissues", 0)) - int(args["tissues"]))
        used.append(item)
    admin_facts(w).setdefault("consumed", []).extend(used)
    w.emit("consumed", items=used, by=args.get("by", "resident"))
    return True

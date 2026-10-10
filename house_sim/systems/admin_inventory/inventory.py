"""Inventory: counting what the robot can actually see (units vs packages, partial levels,
in-use parts, empties, untested cells), inventory records, shared lists (shopping, packing,
checklists) with merge/partial/cross-off history, and simulated purchase orders that need
explicit adult approval.

Records and lists live in state["household"] (inventory, lists, orders): they are the robot's
claims, which task specs compare with the ground truth they set up.
"""
import copy

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.realism import INSPECT
from .common import admin_facts, authority, household, need_tool, slug
from .perceive import dumps, opaque_amount, order_ids, ordered, render

LEVEL_FIELDS = ("remaining", "level", "fill_level")


def level_of(record):
    """(fraction 0..1 or None, observable?) for consumables."""
    for field in LEVEL_FIELDS:
        if field in record and isinstance(record[field], (int, float)):
            return float(record[field]), True
    if record.get("liquid_container") and float(record.get("capacity_l", 0) or 0) > 0:
        frac = float(record.get("liquid_l", 0.0)) / float(record["capacity_l"])
        return frac, not record.get("opaque", False)
    if record.get("bulk_store") and float(record.get("capacity_l", 0) or 0) > 0:
        total = sum(float(layer.get("l", 0)) for layer in record.get("layers", []))
        return total / float(record["capacity_l"]), not record.get("opaque", False)
    if "_level" in record:
        return float(record["_level"]), False
    if "tissues" in record:
        return float(record["tissues"]) / 160.0, True
    return None, True


def seen_empty(env, eid, record):
    """is_empty as the robot can tell it: a closed opaque box or bottle does not show that it is empty (oracle mode:
    the truth)."""
    if not is_empty(record):
        return False
    if getattr(env, "realism", None) is None:
        return True
    if "units_count" in record and record.get("unit_type") is not None and record.get("openable") and not record.get("open") \
            and not record.get("transparent"):
        return False
    if record.get("liquid_container") and opaque_amount(env, eid, record):
        return False
    return True


def is_empty(record):
    if "units_count" in record and record.get("unit_type") is not None and int(record.get("units_count", 0)) <= 0:
        return True
    if "remaining" in record and float(record.get("remaining", 1.0)) <= 0.0:
        return True
    if "tissues" in record and int(record.get("tissues", 1)) <= 0:
        return True
    if record.get("liquid_container") and float(record.get("liquid_l", 1.0)) <= 0.0 and record.get("stock_item"):
        return True
    if record.get("empty"):
        return True
    return False


def matches(record, eid, what, by):
    if not what:
        return bool(record.get("stock_item") or record.get("sku") or record.get("portable"))
    key = slug(what)
    if by in (None, "any", "sku") and slug(record.get("sku")) == key:
        return True
    if by in (None, "any", "type") and (slug(record.get("type")) == key or key in [slug(t) for t in record.get("is_a", [])]):
        return True
    if by in (None, "any", "category") and slug(record.get("category")) == key:
        return True
    if by in (None, "any", "label") and key and key in slug(record.get("label")):
        return True
    if by in (None, "any") and key in [slug(t) for t in record.get("tags", [])]:
        return True
    return False


def _scope_members(ctx, scope):
    w = ctx.w
    state = w.s
    if scope in state["rooms"]:
        require(scope == w.robot["room"], "Go to that room to count what is there", "precondition")
        from ...kernel.predicates import root_room
        return [e for e in sorted(state["entities"]) if root_room(state, e) == scope and e != scope]
    ctx.obj(scope)
    return sorted(w.descendants(scope))


def _count_seconds(ctx):
    return ctx.data.get("seconds", 2.0)


def _realistic_matches(ctx, realism, scope, visible, what, by):
    """Realistic perception: (seen, matched, unidentified). A room-wide count from where the robot stands can miss
    small things (the kernel's small-object model; counting a shelf or container looks closely and finds them).
    Counting looks at each item closely (inspect), so inspect-resolved look-alikes are told apart; members of a
    read-resolved group that were not read are matched only when every member of the group fits `what`,
    otherwise they are reported as unidentified, never counted by their hidden identity."""
    state = ctx.w.s
    reg = ctx.w.reg
    seen = []
    room_scope = scope in state["rooms"]
    for eid in visible:
        rec = state["entities"][eid]
        if room_scope and realism.missed(state, eid, rec, realism.context(state, eid, rec)):
            continue
        seen.append(eid)
    matched, unidentified = [], []
    key = slug(what) if what else None
    for eid in seen:
        rec = state["entities"][eid]
        level = max(INSPECT, realism.knowledge.entity_level(eid, rec))
        ident = realism.identity(eid, rec, level)
        if "candidates" not in ident:
            if matches(rec, eid, what, by):
                matched.append(eid)
            continue
        if not what:
            if matches(rec, eid, what, by):
                matched.append(eid)
            continue
        _, group = realism.group_of(rec)
        members = [m for m in (group or {}).get("members", []) if reg.known(m)]
        fits = [m for m in members if slug(m) == key or (reg.known(key) and reg.is_a(m, key))]
        by_label = by in (None, "any", "label") and key in slug(ident.get("label"))
        # the group's kind, never the member's own category (which could tell the members apart)
        by_category = by in (None, "any", "category") and slug(ident.get("kind")) == key
        if by_label or by_category or (members and len(fits) == len(members)):
            matched.append(eid)
        elif fits:
            unidentified.append(eid)
    return seen, matched, unidentified


@action("count", params={"target": "target", "what": "?str", "by": "?str:any|sku|type|category|label",
                         "unit": "?str:units|packages", "include_in_use": {"type": "bool", "default": False}},
        duration=_count_seconds, energy=0.001, category="perception",
        doc="Count visible items matching `what` (sku, type, category, label word) in a room or inside/on an entity. unit=units looks through clear multipack wrap (or reads the printed count on sealed opaque packs); unit=packages counts top-level packages. Reports partial, empty, in-use (installed) and untested items separately. Closed opaque containers and items hidden behind others are not seen.")
def count(ctx):
    w = ctx.w
    state = w.s
    scope = ctx.args["target"]
    what, by = ctx.args.get("what"), ctx.args.get("by") or "any"
    unit = ctx.args.get("unit") or "units"
    members = _scope_members(ctx, scope)
    visible = [e for e in members if ctx.visible(e)]
    realism = ctx.env.realism
    unidentified = []
    if realism is not None:
        visible, matched, unidentified = _realistic_matches(ctx, realism, scope, visible, what, by)
    else:
        matched = [e for e in visible if matches(state["entities"][e], e, what, by)]
    matched_set = set(matched)
    tested = state.get("facts", {}).get("admin", {}).get("tested", {})
    result = {"scope": scope, "what": what, "unit": unit, "count": 0, "partial": 0, "levels": {}, "empty": 0,
              "in_use": 0, "untested": 0, "packages": 0, "from_label": 0, "ids": []}
    counted_ids = []
    for eid in matched:
        rec = state["entities"][eid]
        parent = rec.get("location")
        inside_pack = parent in matched_set and state["entities"][parent].get("multipack")
        if rec.get("multipack"):
            result["packages"] += 1
            if unit == "packages":
                result["count"] += 1
                counted_ids.append(eid)
                continue
            if not (rec.get("transparent") or rec.get("open")):
                if ctx.env.perception.lit(state, w.robot["room"]):
                    result["count"] += int(rec.get("pack_count", 0))
                    result["from_label"] += int(rec.get("pack_count", 0))
                    counted_ids.append(eid)
                    ctx.grant(eid, "read", ["pack_count", "sku", "label_text"])    # the printed count was read
            continue
        if unit == "packages" and inside_pack:
            continue
        if rec.get("relation") == "part" and rec.get("slot"):
            result["in_use"] += 1
            if not ctx.args["include_in_use"]:
                continue
        if seen_empty(ctx.env, eid, rec):
            result["empty"] += 1
            continue
        if rec.get("charge_hidden") and eid not in tested:
            result["untested"] += 1
        level, observable = level_of(rec)
        if realism is not None and observable and opaque_amount(ctx.env, eid, rec):
            observable = False                # a closed opaque bottle or a hidden level: not seen by counting
        if "units_count" in rec and rec.get("unit_name") and unit == "units":
            inside = int(rec["units_count"])
            printed = int(rec.get("pack_count", inside))
            if rec.get("open") or not rec.get("openable"):
                result["count"] += inside
                ctx.grant(eid, "measure", ["units_count"])          # counted unit by unit
            else:
                result["count"] += printed
                result["from_label"] += printed
                ctx.grant(eid, "read", ["pack_count", "sku", "label_text"])
        else:
            result["count"] += 1
        if level is not None and realism is not None and not observable:
            # an opaque one: whether it is partly used is not seen at all (every such item is "unknown", full or not)
            result["levels"][eid] = "unknown (lift or inspect closely)"
        elif level is not None and level < 0.95:
            result["partial"] += 1
            if not observable:
                result["levels"][eid] = "unknown (lift or inspect closely)"
            elif realism is not None:
                # counting looks at each item closely: levels to the eye's 5 % (a measurement needs a tool)
                result["levels"][eid] = render(ctx.env, eid, "level", round(level, 4), "inspect")
            else:
                result["levels"][eid] = round(level, 2)
        counted_ids.append(eid)
    if unit == "packages":
        result["packages"] = result["count"]
    result["ids"] = order_ids(ctx.env, counted_ids) if realism is not None else counted_ids
    if realism is not None:
        # look-alikes counting cannot tell apart (a read-resolved group, unread) are reported, never guessed
        result["unidentified"] = len(unidentified)
        if scope in state["entities"]:
            ctx.grant(scope, "inspect")                              # a close look at the shelf finds small items
        for eid in counted_ids + unidentified:
            ctx.grant(eid, "inspect")
    # counting time by what the robot looked at (everything it perceives in the scope), never by hidden things
    ctx.data["seconds"] = 1.5 + 0.4 * len(visible if len(visible) < 60 else visible[:60])
    result = ordered(ctx.env, result)          # realistic: id-keyed levels in handle order (never true-id order)
    facts = admin_facts(w)
    facts["last_count"] = result
    facts.setdefault("counts", {})["{}:{}:{}".format(scope, what or "*", unit)] = result["count"]
    return dumps(ctx.env, result)


def _record_seconds(ctx):
    entries = ctx.args.get("entries") or {}
    return 1.0 + 0.5 * max(1, len(entries))


@action("record_inventory", params={"target": "str", "item": "?str", "count": "?number", "location": "?str", "level": "?number",
                                    "flags": "?list", "unit": "?str", "note": "?text", "expires": "?str", "entries": "?dict",
                                    "replace": {"type": "bool", "default": False}},
        duration=_record_seconds, energy=0.0, category="cognitive", stopped_ok=True,
        doc="Write inventory lines into the household record `target` (e.g. pantry, paper_goods): item, count, location, level 0..1, flags (expired, damaged, low, duplicate, missing, unknown, untested, partial, in_use), unit, expires, note. entries={item: {...}} writes several; replace=true clears the record first.")
def record_inventory(ctx):
    w = ctx.w
    name = slug(ctx.args["target"])
    require(name, "Name the inventory record", "argument")
    entries = dict(ctx.args.get("entries") or {})
    if ctx.args.get("item"):
        entries[ctx.args["item"]] = {k: ctx.args[k] for k in ("count", "location", "level", "flags", "unit", "note", "expires") if ctx.args.get(k) is not None}
    require(entries, "Give an item or entries", "argument")
    hh = household(w)
    records = hh.setdefault("inventory", {})
    record = {} if ctx.args["replace"] else copy.deepcopy(records.get(name, {}))
    for item, fields in sorted(entries.items()):
        require(isinstance(fields, dict), "Each entry must be an object", "argument")
        key = slug(item)
        require(key, "Empty item name", "argument")
        clean = {}
        for field, value in fields.items():
            require(field in ("count", "location", "level", "flags", "unit", "note", "expires", "in_use", "partial", "days_supply", "loads_left"),
                    "Unsupported inventory field " + str(field), "argument")
            if field in ("count", "in_use", "partial", "days_supply", "loads_left"):
                require(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0, field + " must be a non-negative number", "argument")
                value = int(value) if float(value).is_integer() and field in ("count", "in_use", "partial") else round(float(value), 3)
            if field == "level":
                require(isinstance(value, (int, float)) and 0 <= value <= 1, "level must be between 0 and 1", "argument")
            if field == "flags":
                require(isinstance(value, list) and all(isinstance(v, str) for v in value), "flags must be a list of words", "argument")
                value = sorted({slug(v) for v in value})
            clean[field] = value
        clean["time_s"] = w.s["time_s"]
        record[key] = clean
    records[name] = record
    return "Recorded {} line(s) in {}".format(len(entries), name)


# ----------------------------------------------------------------------
# lists

def _list_access(ctx, name, create=False):
    w = ctx.w
    lists = household(w).setdefault("lists", {})
    key = slug(name)
    entry = lists.get(key)
    if entry is None:
        require(create, "There is no list named {}".format(name), "precondition")
        return lists, key, None
    if entry.get("medium") == "paper":
        holder = entry.get("location")
        if holder in w.s["entities"]:
            ctx.obj(holder, touch=True)
            need_tool(ctx, "write", None, "Hold a pen to write on the paper list")
    else:
        require(w.robot.get("network", True), "No network: use the paper copy of the list", "resource")
    return lists, key, entry


def _history(entry, op, item, w, **extra):
    record = {"op": op, "item": item, "time_s": w.s["time_s"], "by": "robot"}
    record.update(extra)
    entry.setdefault("history", []).append(record)
    del entry["history"][:-200]


def _new_list(medium, location):
    return {"medium": medium or "app", "location": location, "items": {}, "history": []}


@action("write_list", params={"target": "str", "items": "list", "medium": "?str:app|paper", "location": "?entity",
                              "kind": "?str:shopping|packing|checklist|todo|other"},
        duration=lambda ctx: 3.0 + 2.0 * len(ctx.args.get("items") or []), energy=0.0, category="cognitive",
        doc="Create (or overwrite) a list: items are names or {name, qty, unit, brand, size, section, note, category}. medium=app (shared, needs network) or paper (on location, needs a pen).")
def write_list(ctx):
    w = ctx.w
    name = ctx.args["target"]
    medium = ctx.args.get("medium") or "app"
    location = ctx.args.get("location")
    if medium == "paper":
        require(location, "Name where the paper list is (location)", "argument")
        ctx.obj(location, touch=True)
        need_tool(ctx, "write", None, "Hold a pen to write the list")
    else:
        require(w.robot.get("network", True), "No network for the shared list app", "resource")
    lists = household(w).setdefault("lists", {})
    key = slug(name)
    entry = _new_list(medium, location)
    entry["kind"] = ctx.args.get("kind") or "other"
    entry["title"] = name
    for raw in ctx.args["items"]:
        item = _normalize_item(raw)
        entry["items"][item["key"]] = item["entry"]
    _history(entry, "write", None, w, count=len(entry["items"]))
    lists[key] = entry
    return "Wrote list {} ({} items)".format(key, len(entry["items"]))


def _normalize_item(raw):
    if isinstance(raw, str):
        raw = {"name": raw}
    require(isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"].strip(), "List items need a name", "argument")
    qty = raw.get("qty", 1)
    require(isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0, "qty must be positive", "argument")
    entry = {"name": raw["name"].strip(), "qty": qty, "done": False, "removed": False, "added_by": "robot"}
    for field in ("unit", "brand", "size", "section", "note", "category", "for"):
        if raw.get(field) is not None:
            entry[field] = raw[field]
    return {"key": slug(raw["name"]), "entry": entry}


@action("add_to_list", params={"target": "str", "item": "str", "qty": {"type": "number", "default": 1, "min": 0.01, "max": 1000},
                               "unit": "?str", "brand": "?str", "size": "?str", "section": "?str", "note": "?text",
                               "merge": {"type": "bool", "default": True}},
        duration=3.0, energy=0.0, category="cognitive",
        doc="Add an item to a list (creating a shared app list if needed). An open entry for the same item is merged by summing qty unless merge=false (which adds a duplicate line).")
def add_to_list(ctx):
    w = ctx.w
    lists, key, entry = _list_access(ctx, ctx.args["target"], create=True)
    if entry is None:
        require(w.robot.get("network", True), "No network for the shared list app", "resource")
        entry = _new_list("app", None)
        entry["title"] = ctx.args["target"]
        lists[key] = entry
    raw = {k: ctx.args.get(k) for k in ("unit", "brand", "size", "section", "note")}
    raw.update(name=ctx.args["item"], qty=ctx.args["qty"])
    item = _normalize_item(raw)
    ikey = item["key"]
    items = entry.setdefault("items", {})
    existing = items.get(ikey)
    if existing and not existing.get("removed") and not existing.get("done") and ctx.args["merge"]:
        existing["qty"] = round(float(existing.get("qty", 1)) + float(ctx.args["qty"]), 3)
        _history(entry, "merge", ikey, w, qty=ctx.args["qty"])
        return "Increased {} to {}".format(ikey, existing["qty"])
    if existing and not ctx.args["merge"] and not existing.get("removed"):
        n = 2
        while "{}_{}".format(ikey, n) in items:
            n += 1
        ikey = "{}_{}".format(ikey, n)
        item["entry"]["duplicate_of"] = item["key"]
    items[ikey] = item["entry"]
    _history(entry, "add", ikey, w, qty=ctx.args["qty"])
    return "Added {} x{} to {}".format(ikey, ctx.args["qty"], key)


@action("cross_off", params={"target": "str", "item": "str", "bought": "?number", "substitution": "?text", "note": "?text"},
        duration=2.0, energy=0.0, category="cognitive",
        doc="Mark a list item obtained (kept in history, never deleted). bought less than qty records a partial purchase and keeps the remainder open; substitution notes what was bought instead.")
def cross_off(ctx):
    w = ctx.w
    lists, key, entry = _list_access(ctx, ctx.args["target"])
    ikey = slug(ctx.args["item"])
    items = entry.get("items", {})
    require(ikey in items and not items[ikey].get("removed"), "That item is not on the list", "precondition")
    it = items[ikey]
    require(not it.get("done"), "Already crossed off", "precondition")
    bought = ctx.args.get("bought")
    qty = float(it.get("qty", 1))
    if bought is not None and float(bought) < qty:
        require(float(bought) > 0, "bought must be positive", "argument")
        it["bought_qty"] = round(float(it.get("bought_qty", 0)) + float(bought), 3)
        it["qty"] = round(qty - float(bought), 3)
        it["partial"] = True
        _history(entry, "partial", ikey, w, bought=bought)
        return "{} partly bought; {} still needed".format(ikey, it["qty"])
    it["done"] = True
    it["bought_qty"] = round(float(it.get("bought_qty", 0)) + float(bought if bought is not None else qty), 3)
    if ctx.args.get("substitution"):
        it["substitution"] = ctx.args["substitution"]
    if ctx.args.get("note"):
        it["note"] = ctx.args["note"]
    _history(entry, "done", ikey, w)
    return "Crossed off " + ikey


@action("remove_from_list", params={"target": "str", "item": "str", "reason": "?text"}, duration=2.0, energy=0.0,
        category="cognitive",
        doc="Remove a list entry that is no longer needed (e.g. found in stock); the entry is kept with removed=true for history.")
def remove_from_list(ctx):
    w = ctx.w
    lists, key, entry = _list_access(ctx, ctx.args["target"])
    ikey = slug(ctx.args["item"])
    items = entry.get("items", {})
    require(ikey in items and not items[ikey].get("removed"), "That item is not on the list", "precondition")
    items[ikey]["removed"] = True
    items[ikey]["removed_reason"] = ctx.args.get("reason")
    _history(entry, "remove", ikey, w, reason=ctx.args.get("reason"))
    return "Removed " + ikey


@action("place_order", params={"target": "str", "qty": {"type": "number", "default": 1, "min": 1, "max": 100},
                               "vendor": "?str"},
        duration=10.0, energy=0.0, category="communication",
        doc="Place a (simulated) online purchase. Requires an adult's explicit approval purchase:<item> and the network; the order is recorded, nothing real is bought.")
def place_order(ctx):
    w = ctx.w
    item = slug(ctx.args["target"])
    require(w.robot.get("network", True), "No network", "resource")
    require("purchase:" + item in w.s.get("approvals", []), "Buying needs an adult's explicit approval (purchase:{})".format(item), "authority")
    require(not w.s.get("household", {}).get("no_purchases"), "The household said not to buy anything", "authority")
    orders = household(w).setdefault("orders", [])
    orders.append({"item": item, "qty": ctx.args["qty"], "vendor": ctx.args.get("vendor"), "time_s": w.s["time_s"], "by": "robot"})
    return "Ordered {} x{}".format(item, ctx.args["qty"])

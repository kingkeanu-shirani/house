"""Mail: sealed envelopes, opening only what the requester may open, addressing and postage
(US domestic rate table by weight/shape, configurable per household), postal scale display,
curbside/apartment mailboxes with a flag, and a mail carrier that collects ready outgoing
mail and delivers scheduled incoming mail at the route time.
"""
from ...kernel import clock, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from ..core import subtree_mass
from .common import (address_matches, admin_facts, authority, household, mass_text, need_tool, requester,
                     require_readable, resolve_name, slug, units_metric)
from .perceive import count_seen, dumps, order_ids, privacy_violation, was_read

# USPS retail rates effective July 2025 (cents). Households in other regions override household.postage.
DEFAULT_POSTAGE = {"letter_first_oz": 78, "letter_extra_oz": 29, "letter_max_oz": 3.5, "nonmachinable": 49,
                   "flat_first_oz": 163, "flat_extra_oz": 27, "flat_max_oz": 13.0, "international_letter": 170,
                   "currency": "cents"}
OZ = 0.0283495


def postage_table(state):
    table = dict(DEFAULT_POSTAGE)
    table.update(state.get("household", {}).get("postage", {}))
    return table


def required_postage(w, eid):
    """Cents of postage a piece needs (by weight, shape and destination)."""
    rec = w.ent(eid)
    table = postage_table(w.s)
    oz = subtree_mass(w, eid) / OZ
    import math
    ounces = max(1, int(math.ceil(oz - 1e-9)))
    if rec.get("international"):
        return int(table["international_letter"]) + max(0, ounces - 1) * int(table["letter_extra_oz"])
    if rec.get("envelope_size") == "flat_9x12" or oz > float(table["letter_max_oz"]):
        return int(table["flat_first_oz"]) + (ounces - 1) * int(table["flat_extra_oz"])
    cents = int(table["letter_first_oz"]) + (ounces - 1) * int(table["letter_extra_oz"])
    if not rec.get("machinable", True) or rec.get("rigid"):
        cents += int(table["nonmachinable"])
    return cents


def is_mail(record):
    return bool(record) and (record.get("mail_piece") or record.get("envelope"))


def printed_face(record):
    """The legible text on the outside of a mail piece (sender, printed markings, address block): what `read`
    shows and what resolves the "envelope" look-alike group in realistic perception. None if nothing is printed."""
    parts = []
    if record.get("sender"):
        parts.append("From: {}".format(record["sender"]))
    parts.extend(str(m) for m in record.get("markings") or [])
    to = ", ".join(str(v) for v in (record.get("recipient"), record.get("address")) if v)
    if to:
        parts.append("To: {}".format(to))
    return " | ".join(parts) or None


def addressee_of(state, record):
    truth = record.get("_addressee")
    if truth:
        return truth
    return resolve_name(state, record.get("recipient"))


# ----------------------------------------------------------------------
# guards: sealed envelopes, taped packages, private containers

@before("open", order=40)
def _open_guard(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target)
    if rec is None:
        return
    if rec.get("mailbox") and rec.get("household_box") is False and "open:" + target not in w.s.get("approvals", []):
        if not ctx.realistic:
            raise ActionError("That is a neighbor's mailbox; do not open it", "privacy")
        # whose mailbox it is shows only in its printed name or number: refused once read, else opening it is the breach
        if was_read(ctx.env, target):
            raise ActionError("That mailbox is not this household's", "privacy")
        privacy_violation(ctx, target, "neighbor_mailbox")
    if rec.get("envelope") and rec.get("sealed"):
        raise ActionError("The envelope is sealed; slit it with a letter opener (open_mail) if you may open it", "precondition")
    if rec.get("package") and rec.get("tape_sealed"):
        raise ActionError("The box is taped shut; cut the tape (open_package) if you may open it", "precondition")
    if rec.get("private_contents"):
        owner = rec.get("owner")
        approvals = w.s.get("approvals", [])
        ok = owner in (None, requester(w.s)) or "open:" + target in approvals or "touch:" + target in approvals
        if not ok and ctx.realistic:
            # who owns a bag is never seen (ask): a refusal would tell; opening someone's bag unasked is the breach
            privacy_violation(ctx, target, "private_contents")
            return
        require(ok, "That is {}'s personal {}; do not open it without their permission".format(owner, rec.get("label", "bag")), "privacy")


@after("close", order=40)
def _seal_envelope(ctx, result):
    w = ctx.w
    target = ctx.args["target"]
    rec = w.ent(target)
    if rec.get("envelope") and not rec.get("slit") and not rec.get("torn"):
        m = w.mut(target)
        m["sealed"] = True
        m["sealed_at_s"] = w.s["time_s"]


# ----------------------------------------------------------------------
# opening mail

@action("open_mail", params={"target": "entity", "tool": "?entity"}, duration=4.0, energy=0.002, category="physical",
        doc="Open a sealed envelope addressed to the requester (or approved with permission open_mail:<id>) by slitting the top fold with a held letter opener. A box cutter cuts snug contents; no tool tears it by hand.")
def open_mail(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("envelope"), "That is not an envelope", "argument")
    require(rec.get("sealed"), "It is already open", "precondition")
    who = addressee_of(state, rec)
    req = requester(state)
    approvals = state.get("approvals", [])
    allowed = who == req or (who == "household" and authority(state, req) >= 2) or "open_mail:" + target in approvals
    if not allowed and ctx.realistic:
        # the addressee is printed: once the robot read it the refusal is generic; opening it unread is the breach
        if was_read(ctx.env, target):
            raise ActionError("That mail is not addressed to the person who asked; it needs the addressee's permission", "privacy")
        privacy_violation(ctx, target, "open_others_mail")
        allowed = True
    whom = rec.get("recipient") or "someone else"
    require(allowed, "This is addressed to {}; it is not the requester's to open".format(whom), "privacy")
    tool = ctx.args.get("tool")
    method, depth = "hand", 0.0
    if tool:
        require(ctx.holding(tool), "Hold the tool first", "precondition")
        t = w.ent(tool)
        caps = set(t.get("capabilities", [])) | set(t.get("affordances", []))
        if "slit_envelope" in caps:
            method, depth = "opener", float(t.get("blade_depth_mm", 2.0))
        elif "cut_tape" in caps:
            require((t.get("settings") or {}).get("blade", "extended") == "extended", "Extend the blade first", "precondition")
            method, depth = "blade", float((t.get("settings") or {}).get("depth_mm", 10))
        else:
            raise ActionError("That tool cannot open envelopes", "capability")
    else:
        found = next((h for h in w.held_items() if "slit_envelope" in w.ent(h).get("capabilities", [])), None)
        if found:
            method, depth = "opener", float(w.ent(found).get("blade_depth_mm", 2.0))
    m = w.mut(target)
    m["sealed"] = False
    m["open"] = True
    clearance = float(rec.get("contents_clearance_mm", 4.0 if not rec.get("snug") else 1.5))
    damaged = []
    if method == "hand":
        m["torn"] = True
        if rec.get("snug"):
            for child in w.children(target):
                cm = w.mut(child)
                cm["torn"] = True
                cm["damaged"] = True
                damaged.append(child)
    else:
        m["slit"] = True
        # a dull letter opener rides in the gap of the top fold; only sharp blades can reach the contents
        if method == "blade" and depth > clearance:
            for child in w.children(target):
                cm = w.mut(child)
                cm["cut"] = True
                cm["damaged"] = True
                damaged.append(child)
    if damaged:
        w.stat("damage")
        w.emit("mail_contents_damaged", target=target, items=damaged)
    w.emit("mail_opened", target=target, by="robot", method=method)
    admin_facts(w).setdefault("opened_mail", []).append(target)
    return dumps(ctx.env, {"opened": target, "method": method, "contents": order_ids(ctx.env, w.children(target)),
                           "damaged": order_ids(ctx.env, damaged)})


# ----------------------------------------------------------------------
# outgoing mail

@action("address_envelope", params={"target": "entity", "to": "text", "sender": "?text", "tool": "?entity"},
        duration=20.0, energy=0.002, category="physical",
        doc="Write the recipient address (centered) and optional return address (top-left) on an envelope with a held pen or marker.")
def address_envelope(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("envelope"), "That is not an envelope", "argument")
    need_tool(ctx, "write", ctx.args.get("tool"), "Hold a pen or marker")
    require_readable(ctx)
    m = w.mut(target)
    m["to_text"] = ctx.args["to"].strip()
    m["addressed"] = True
    if ctx.args.get("sender"):
        m["from_text"] = ctx.args["sender"].strip()
    m["label_text"] = m["to_text"]
    return "Addressed {} to {}".format(target, m["to_text"])


@action("affix_postage", params={"target": "entity", "stamps": {"type": "int", "default": 1, "min": 1, "max": 20},
                                 "source": "?entity", "position": "?str:top_right|top_left|back"},
        duration=lambda ctx: 3.0 + 2.0 * ctx.args.get("stamps", 1), energy=0.001, category="physical",
        doc="Stick stamps from a held stamp booklet (source) on an envelope; top_right is where the carrier's machines look.")
def affix_postage(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("envelope") or rec.get("package"), "Postage goes on an envelope or parcel", "argument")
    source = ctx.args.get("source") or next((h for h in w.held_items() if w.ent(h).get("sku", "").startswith("stamp")), None)
    require(source and ctx.holding(source), "Hold the stamps", "precondition")
    s = w.ent(source)
    n = ctx.args["stamps"]
    require(int(s.get("units_count", 0)) >= n, "Only {} stamps left".format(count_seen(ctx.env, source, int(s.get("units_count", 0)))), "resource")
    sm = w.mut(source)
    sm["units_count"] = int(s["units_count"]) - n
    m = w.mut(target)
    m["postage_cents"] = int(m.get("postage_cents", 0)) + n * int(s.get("stamp_value_cents", 78))
    m["stamps"] = int(m.get("stamps", 0)) + n
    m["stamp_position"] = ctx.args.get("position") or "top_right"
    w.emit("postage_affixed", target=target, cents=m["postage_cents"])
    return "Affixed {} stamp(s): {} cents of postage".format(n, m["postage_cents"])


def ready_to_send(w, eid):
    """(ready, reason) for an outgoing piece the carrier would accept."""
    rec = w.ent(eid)
    if rec.get("package"):
        if rec.get("tape_sealed") and (rec.get("return_label_ok") or rec.get("shipping_label_ok")):
            return True, None
        return False, "no valid shipping label"
    if not rec.get("envelope"):
        return False, "not mail"
    if not rec.get("sealed"):
        return False, "unsealed"
    if not (rec.get("addressed") or rec.get("window_address_ok")):
        return False, "no address"
    if rec.get("stamp_position") not in (None, "top_right") and rec.get("postage_cents", 0):
        return False, "stamps not in the upper-right corner"
    if int(rec.get("postage_cents", 0)) < required_postage(w, eid):
        return False, "postage due"
    return True, None


def deliverable(w, eid):
    rec = w.ent(eid)
    book = w.s.get("household", {}).get("address_book", {})
    text = slug(rec.get("to_text") or "")
    if rec.get("window") and rec.get("window_address_ok"):
        return True
    for name, entry in sorted(book.items()):
        if text and slug(entry.get("address", "")) and slug(entry.get("address", "")) in text:
            return True
    return not book and bool(text)


@after("place", order=72)
def _window_address(ctx, result):
    """A z-folded letter with its address block facing out shows the address through a window envelope."""
    w = ctx.w
    rec = w.ent(ctx.args["target"])
    env_id = rec.get("location")
    env = w.s["entities"].get(env_id)
    if env is None or not env.get("window") or rec.get("relation") != "in":
        return
    ok = bool(rec.get("address_out") and (rec.get("has_address_block") or rec.get("doc_kind") in ("letter", "bill", "statement", "form")))
    m = w.mut(env_id)
    m["window_address_ok"] = ok
    if ok:
        m["addressed"] = True
        m["to_text"] = rec.get("address_block") or m.get("to_text")


# ----------------------------------------------------------------------
# postal scale display

def _refresh_scale(w, scale_id):
    rec = w.ent(scale_id)
    if not rec.get("scale"):
        return
    total = sum(subtree_mass(w, c) for c in w.children(scale_id) if w.ent(c).get("relation") == "on")
    units = (rec.get("settings") or {}).get("units", "g")
    if not rec.get("on") or float(rec.get("battery_level", 1.0)) <= 0.02:
        text = ""
    elif total > float(rec.get("capacity_kg", 5.0)):
        text = "Err"
    elif units == "oz":
        text = "{:.1f} oz".format(total / OZ)
    else:
        text = "{} g".format(int(round(total * 1000.0 / float(rec.get("resolution_g", 1.0)))) * int(rec.get("resolution_g", 1.0)))
    if rec.get("display") != text:
        w.mut(scale_id)["display"] = text


@after("place", order=70)
def _scale_after_place(ctx, result):
    w = ctx.w
    loc = w.ent(ctx.args["target"]).get("location")
    if loc in w.s["entities"] and w.ent(loc).get("scale"):
        _refresh_scale(w, loc)


@before("pick_up", order=70)
def _scale_before_pickup(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec is not None:
        ctx.data["admin_scale_from"] = rec.get("location")


@after("pick_up", order=70)
def _scale_after_pickup(ctx, result):
    w = ctx.w
    loc = ctx.data.get("admin_scale_from")
    if loc in w.s["entities"] and w.ent(loc).get("scale"):
        _refresh_scale(w, loc)


@after("read", order=70)
def _scale_reading(ctx, result):
    """Reading a postal scale's display measures what rests on it: one item alone is weighed to the scale's
    resolution (realistic perception: its mass becomes measured knowledge)."""
    w = ctx.w
    rec = w.ent(ctx.args["target"])
    if not rec.get("scale") or not rec.get("display") or rec.get("display") == "Err":
        return
    items = [c for c in w.children(ctx.args["target"]) if w.ent(c).get("relation") == "on"]
    if len(items) == 1 and not w.children(items[0]):
        ctx.grant(items[0], "measure", ["mass_kg"])


@hook("setting_changed")
def _scale_units(ctx, eid, name, old, new):
    if ctx.w.ent(eid).get("scale"):
        _refresh_scale(ctx.w, eid)


@after("switch_on", order=70)
def _scale_on(ctx, result):
    if ctx.w.ent(ctx.args["target"]).get("scale"):
        _refresh_scale(ctx.w, ctx.args["target"])


# ----------------------------------------------------------------------
# mailbox, carrier and deliveries

def household_mailboxes(state):
    return sorted(eid for eid, r in state["entities"].items() if (r.get("mailbox") and r.get("household_box", True)) or r.get("outgoing_slot"))


def _route(state):
    route = {"time": "13:30", "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]}
    route.update(state.get("household", {}).get("mail_route", {}))
    return route


def _spawn_piece(w, box, spec):
    props = dict(spec.get("props", {}))
    eid = spec.get("eid")
    if eid and eid in w.s["entities"]:
        eid = None
    new = w.spawn(spec.get("type", "mail_piece"), box, eid=eid, **props)
    m = w.mut(new)
    m["relation"] = "in"
    if m.get("mail_piece") and not (m.get("text") or m.get("label_text")) and printed_face(m):
        m["text"] = printed_face(m)
    return new


def carrier_visit(w, now_dt):
    state = w.s
    hh = state.get("household", {})
    sent = []
    for box in household_mailboxes(state):
        rec = w.ent(box)
        flag_up = (rec.get("settings") or {}).get("flag") == "up"
        if not (flag_up or rec.get("outgoing_slot")):
            continue
        for child in w.children(box):
            c = w.ent(child)
            if not (c.get("outgoing") or (c.get("envelope") and not c.get("mail_piece") and c.get("addressed"))
                    or (c.get("package") and c.get("shipping_label_doc"))):
                continue
            ok, reason = ready_to_send(w, child)
            if ok and not deliverable(w, child):
                ok, reason = False, "undeliverable address"
            if not ok:
                if c.get("carrier_note") != reason:
                    w.mut(child)["carrier_note"] = reason
                continue
            record = {"to": c.get("to_text"), "postage_cents": c.get("postage_cents", 0), "required_cents": required_postage(w, child) if c.get("envelope") else None,
                      "time_s": state["time_s"], "box": box, "label": c.get("label"), "contents": w.children(child)}
            household(w).setdefault("sent_mail", {})[child] = record
            for item in w.descendants(child):
                w.destroy(item)
            w.destroy(child)
            sent.append(child)
        if flag_up and rec.get("settings", {}).get("flag") == "up":
            w.mut(box)["settings"]["flag"] = "down"
    incoming = hh.get("mail_incoming") or []
    remaining, delivered = [], []
    today = now_dt.date().isoformat()
    for batch in incoming:
        when = batch.get("date")
        if when is None and batch.get("day") is not None:
            start = clock.start_of(state).date()
            import datetime
            when = (start + datetime.timedelta(days=int(batch["day"]))).isoformat()
        if when and when > today:
            remaining.append(batch)
            continue
        box = batch.get("box") or next(iter([b for b in household_mailboxes(state) if w.ent(b).get("mailbox")]), None)
        if box is None:
            remaining.append(batch)
            continue
        for spec in batch.get("pieces", []):
            delivered.append(_spawn_piece(w, box, spec))
    if incoming and len(remaining) != len(incoming):
        household(w)["mail_incoming"] = remaining
    if sent or delivered:
        w.emit("mail_carrier", sent=sent, delivered=delivered)
    return sent, delivered


def mail_tick(w, dt):
    state = w.s
    route = _route(state)
    now_dt = clock.now(state)
    hh_time = route.get("time", "13:30")
    hour, minute = [int(x) for x in str(hh_time).split(":")[:2]]
    target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    prev = now_dt - __import__("datetime").timedelta(seconds=dt)
    if not (prev < target <= now_dt):
        return
    if now_dt.strftime("%A") not in route.get("days", []):
        return
    if not household_mailboxes(state) and not state.get("household", {}).get("mail_incoming"):
        return
    carrier_visit(w, now_dt)


@hook("event:admin_mail_delivery")
def _event_mail_delivery(w, args, env):
    box = args.get("box") or next(iter([b for b in household_mailboxes(w.s) if w.ent(b).get("mailbox")]), None)
    if box is None:
        raise ValueError("admin_mail_delivery: no household mailbox")
    created = [_spawn_piece(w, box, spec) for spec in args.get("pieces", [])]
    w.emit("mail_delivered", box=box, pieces=created)
    return created


@hook("event:admin_carrier_visit")
def _event_carrier(w, args, env):
    return carrier_visit(w, clock.now(w.s))

"""Paper documents: stapling, unstapling, punching, binding/clipping, collating, page sorting,
folding, filing into folders (date order), shredding, and privacy guards for reading and
handing over sensitive documents.

Model (fidelity S/G): a document entity is a physical stack whose `sheets` list holds
[doc_key, page] pairs in stack order. Merged stacks keep each part's metadata in `parts`.
Fasteners: `fastener` in (None, staple, paper_clip, binder_clip, ring_binder); a fastener
that is over capacity has fastener_ok False and fails when the stack is lifted.
"""
import copy

from ...kernel import camera, geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from ..core import release_from_hands
from .perceive import count_seen, dumps, has_read, perceives, privacy_violation, was_read
from .common import (FLAT_M, admin_facts, drop_cell, authority, doc_keys, flat_spot, in_order, indexed, is_child, need_tool, refresh_paper, requester,
                     printed_face_down, require_readable, set_face, sheet_count, sheets_of)

SHRED_PILE_FOOT_M = (0.28, 0.18)    # the pile covers the floor of a home shredder's bin (about 28 x 18 cm)
SHRED_PILE_MIN_H_M = 0.02           # a thin layer of strips
SHRED_PILE_MAX_H_M = 0.35           # a full 20-25 L bin
DOC_META_KEYS = ("title", "doc_kind", "owner", "date", "sensitive", "original", "do_not_punch", "no_fold",
                 "action_required", "signed", "_important", "_doc_important", "_designated", "_file_as", "text", "label", "pii",
                 "_pii", "shred_required", "numbered", "year", "category_hint", "secret_fields")


def is_document(record):
    return record is not None and record.get("category") == "document" and "sheets" in record


def require_document(ctx, eid, touch=True):
    record = ctx.obj(eid, touch=touch)
    require(is_document(record), "That is not a paper document", "argument")
    require(not record.get("shredded"), "That document has been shredded", "precondition")
    require(sheets_of(record), "There are no sheets left in it", "precondition")
    return record


def ensure_parts(record):
    """Make sure the per-document metadata of this stack's own key is recorded in parts."""
    parts = record.setdefault("parts", {})
    key = record.get("doc_key")
    if key and key not in parts:
        parts[key] = {k: copy.deepcopy(record[k]) for k in DOC_META_KEYS if k in record}
    return parts


def on_work_surface(w, eid):
    rec = w.ent(eid)
    if rec.get("location") == "robot":
        return True
    parent = w.s["entities"].get(rec.get("location"))
    if parent is None:
        return rec.get("location") in w.s["rooms"]
    return rec.get("relation") == "on" or bool(parent.get("surface")) or bool(parent.get("container"))


def spot_near(w, eid, surface=None):
    """A location for debris created at eid's place: same surface if room, else the floor near the robot."""
    rec = w.ent(eid)
    parent_id = surface or (rec.get("location") if rec.get("location") not in ("robot",) else None)
    parent = w.s["entities"].get(parent_id) if parent_id else None
    if parent is not None and parent.get("surface"):
        return parent_id, "on"
    if parent is not None and parent.get("container") and parent.get("accepts_items"):
        return parent_id, "in"
    if parent_id is None and rec.get("location") == "robot":
        # working on a held item: the bit drops onto the work surface the robot stands at, if any
        state = w.s
        room = w.robot["room"]
        for sid in sorted(geometry.room_entities(state, room)):
            srec = state["entities"][sid]
            if srec.get("surface") and srec.get("accepts_items") and not srec.get("portable") \
                    and geometry.reach_problem(state, sid) is None:
                return sid, "on"
    return w.robot["room"], "floor"


def drop_debris(w, type_name, near_eid, **props):
    """Spawn a small debris entity (staple, chad, scraps) where the work happened."""
    loc, how = spot_near(w, near_eid)
    if how == "floor":
        new = w.spawn(type_name, loc, **props)
        cell = drop_cell(w, loc, w.robot.get("pos"), new)
        m = w.mut(new)
        m["pos"] = list(cell)
        m["relation"] = "on"
        return new
    new = w.spawn(type_name, loc, **props)
    m = w.mut(new)
    m["relation"] = how
    if how == "on":
        spot = geometry.surface_spot(w.s, loc, new)
        size = w.ent(new).get("size_m") or [0.05, 0.05, 0.01]
        if spot is None and float(size[2]) <= FLAT_M:
            spot = flat_spot(w.s, loc, geometry.footprint_m(w.ent(new)))   # a staple or chad lands on the papers
        if spot is None:
            # surface full: it slides off onto the floor
            room = w.robot["room"]
            m["location"] = room
            m["pos"] = drop_cell(w, room, w.robot.get("pos"), new)
        else:
            m["spos"] = spot
    return new


def remove_from_orders(w, eid, container=None):
    """Drop eid from the `order` list of its (former) container."""
    ids = [container] if container else sorted(k for k, r in w.s["entities"].items() if eid in (r.get("order") or []))
    for cid in ids:
        rec = w.s["entities"].get(cid)
        if rec is not None and eid in (rec.get("order") or []):
            w.mut(cid)["order"] = [x for x in rec["order"] if x != eid]


def order_key(w, eid):
    rec = w.ent(eid)
    return (str(rec.get("date") or ""), eid)


# ----------------------------------------------------------------------
# guards on core actions

@hook("episode_start")
def _printed_side(w, env):
    """Geometric camera: a paper (a document, an envelope or a magazine) the setup laid face down shows its printed
    side to no camera: its label face (sight 4.6; the type's is up) follows `face` from the first step. (The camera is
    installed before the episode_start hooks run; a room-mode state is left as generated.)"""
    if camera.mode(w.s) != "geometric":
        return
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        paper = is_document(rec) or rec.get("envelope") or rec.get("mail_piece")
        if paper and (rec.get("face") == "down") != printed_face_down(rec):
            set_face(w, eid, "down" if rec.get("face") == "down" else "up")


@before("pick_up", order=50)
def _remember_origin(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec is not None:
        ctx.data["admin_prev_loc"] = rec.get("location")


@after("pick_up", order=50)
def _pickup_effects(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    prev = ctx.data.get("admin_prev_loc")
    if prev in w.s["entities"] and eid in (w.ent(prev).get("order") or []):
        remove_from_orders(w, eid, prev)
    rec = w.ent(eid)
    # An over-capacity clip slides off when the stack is lifted: sheets scatter out of order.
    if is_document(rec) and rec.get("fastener") in ("paper_clip", "binder_clip") and not rec.get("fastener_ok", True):
        m = w.mut(eid)
        sheets = sheets_of(m)
        if len(sheets) > 1:
            ctx.rng.shuffle(sheets)
            m["sheets"] = sheets
        m["fastener"] = None
        m["fastener_ok"] = True
        m["scattered"] = True
        refresh_paper(m)
        for child in w.children(eid):
            c = w.ent(child)
            if c.get("relation") == "attached" and c.get("clip_kind"):
                cm = w.mut(child)
                room = w.robot["room"]
                cm["location"], cm["relation"] = room, "on"
                cm["pos"] = drop_cell(w, room, w.robot.get("pos"), child)
        w.emit("clip_slipped", target=eid)
        ctx.note("The clip slipped off and the pages fell out of order")


@after("place", order=50)
def _place_order(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    dest = rec.get("location")
    parent = w.s["entities"].get(dest)
    if parent is not None and "order" in parent and rec.get("relation") == "in":
        order = [x for x in (parent.get("order") or []) if x != eid]
        order.append(eid)
        w.mut(dest)["order"] = order


@before("read", order=40)
def _private_reading(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target)
    if rec is None:
        return
    approvals = w.s.get("approvals", [])
    if rec.get("confidential") or rec.get("diary") or (rec.get("private") and is_document(rec)):
        owner = rec.get("owner")
        ok = owner == requester(w.s) or "read:" + target in approvals or "touch:" + target in approvals
        if ok:
            return
        if ctx.realistic:
            if rec.get("private"):
                # the kernel's own wording for private things (its inspect refuses them the same way)
                raise ActionError("Owner permission is required", "privacy")
            # a confidential letter or a diary looks like any other paper until it is read: reading it is the breach
            privacy_violation(ctx, target, "read_private_document")
            return
        raise ActionError("That is someone's private document; do not read it without their permission", "privacy")


@before("hand_over", order=40)
def _sensitive_handover(ctx):
    w = ctx.w
    target, pid = ctx.args.get("target"), ctx.args.get("destination")
    if target not in w.s["entities"] or pid not in w.s.get("people", {}):
        return
    items = [target] + w.descendants(target)
    person = w.s["people"][pid]
    for item in items:
        rec = w.ent(item)
        if not rec.get("sensitive"):
            continue
        owner = rec.get("owner")
        if owner == pid:
            continue
        guest = person.get("role") in ("guest", "visitor")
        ok = (not guest and authority(w.s, pid) >= 2) or "give:{}:{}".format(item, pid) in w.s.get("approvals", [])
        if ok:
            continue
        if ctx.realistic:
            # whether a paper is sensitive is printed on it: refused only once the robot read it (then generically,
            # naming no paper or owner); an unread one is handed over, and that is the privacy breach
            if perceives(ctx.env, item, "sensitive") or was_read(ctx.env, item, ("sensitive", "title", "text")):
                raise ActionError("That paper should go only to its owner or an adult of the household; ask first", "privacy")
            privacy_violation(ctx, item, "sensitive_handover")
            continue
        raise ActionError("{} is a sensitive document; only its owner or an authorized adult may receive it".format(rec.get("label", item)), "privacy")


# ----------------------------------------------------------------------
# fastening

def _stapler_magazine(w, stapler):
    slots = w.ent(stapler).get("slots") or {}
    return (slots.get("magazine") or {}).get("item")


@action("staple", params={"target": "entity", "tool": "?entity", "position": "?str:top_left|top_right|left_edge|center"},
        duration=3.0, energy=0.003, noise=0.08, category="physical",
        doc="Staple a document's sheets together with a held stapler (one staple, top-left by default). The stack must rest on a surface or be held; over-capacity stacks bend the staple.")
def staple(ctx):
    w = ctx.w
    doc_id = ctx.args["target"]
    doc = require_document(ctx, doc_id)
    stapler = need_tool(ctx, "staple", ctx.args.get("tool"), "Hold a stapler")
    require(on_work_surface(w, doc_id), "Lay the papers on a flat surface or hold them to staple", "precondition")
    require(doc.get("fastener") in (None,), "It is already fastened ({}); remove that first".format(doc.get("fastener")), "precondition")
    strip = _stapler_magazine(w, stapler)
    require(strip and int(w.ent(strip).get("count", 0)) > 0, "The stapler is empty; load a staple strip", "resource")
    w.mut(strip)["count"] = int(w.ent(strip)["count"]) - 1
    n = sheet_count(doc)
    capacity = int(w.ent(stapler).get("sheet_capacity", 20))
    position = ctx.args.get("position") or "top_left"
    m = w.mut(doc_id)
    ensure_parts(m)
    m["fastener"] = "staple"
    m["staple_pos"] = position
    if position == "center":
        m["text_pierced"] = True
    if n > capacity:
        m["fastener_ok"] = False
        m["staple_bent"] = True
        w.emit("staple_bent", target=doc_id, sheets=n, capacity=capacity)
        if ctx.realistic:
            return "The staple bent: the stack is too thick for this stapler (use a binder clip)"
        return "The staple bent: {} sheets exceed the stapler's {}-sheet capacity (use a binder clip)".format(n, capacity)
    m["fastener_ok"] = True
    refresh_paper(m)
    if ctx.realistic and not has_read(ctx.env, doc_id, ("sheets", "page_order", "in_order")):
        # handling the stack shows how thick it is (exact for a few sheets), not the page numbers
        return "Stapled {} ({} sheets)".format(doc_id, count_seen(ctx.env, doc_id, n))
    return "Stapled {} ({} sheets{})".format(doc_id, n, "" if m["in_order"] else ", pages NOT in order")


@action("unstaple", params={"target": "entity", "tool": "?entity"}, duration=4.0, energy=0.003, category="physical",
        doc="Remove the staple from a document with a held staple remover (by hand tears the corner). The removed staple is left where you worked; throw it away.")
def unstaple(ctx):
    w = ctx.w
    doc_id = ctx.args["target"]
    doc = require_document(ctx, doc_id)
    require(doc.get("fastener") == "staple", "It is not stapled", "precondition")
    require(on_work_surface(w, doc_id), "Lay the papers on a flat surface or hold them", "precondition")
    tool = ctx.args.get("tool")
    remover = None
    try:
        remover = need_tool(ctx, "remove_staples", tool)
    except ActionError:
        if tool:
            raise
    m = w.mut(doc_id)
    m["fastener"] = None
    m["fastener_ok"] = True
    m.pop("staple_bent", None)
    if remover is None:
        m["torn"] = True
        m["corner_torn"] = True
    staple_id = drop_debris(w, "loose_staple", doc_id)
    refresh_paper(m)
    return dumps(ctx.env, {"unstapled": doc_id, "torn": bool(m.get("torn")), "staple": staple_id})


@action("punch", params={"target": "entity", "tool": "?entity"}, duration=5.0, energy=0.004, noise=0.15, category="physical",
        doc="Hole-punch a document with a hole punch (held, or named as tool within reach). Pattern comes from the punch (us_3 / iso_2). Over-capacity stacks give ragged holes; originals/certificates are ruined.")
def punch(ctx):
    w = ctx.w
    doc_id = ctx.args["target"]
    doc = require_document(ctx, doc_id)
    tool = ctx.args.get("tool")
    if tool and not ctx.holding(tool):
        rec = ctx.obj(tool, touch=True)
        require("punch" in rec.get("affordances", []) or rec.get("tool_kind") == "hole_punch", "That is not a hole punch", "argument")
        punch_id = tool
    else:
        punch_id = need_tool(ctx, "punch", tool, "Hold a hole punch or name one within reach")
    require(on_work_surface(w, doc_id), "Lay the papers on a flat surface or hold them", "precondition")
    require(doc.get("fastener") not in ("ring_binder",), "Take it out of the binder first", "precondition")
    require(not doc.get("punch_holes"), "It is already punched", "precondition")
    p = w.ent(punch_id)
    n = sheet_count(doc)
    m = w.mut(doc_id)
    ensure_parts(m)
    m["punch_holes"] = p.get("punch_pattern", "us_3")
    if n > int(p.get("sheet_capacity", 12)):
        m["holes_ragged"] = True
    if m.get("margin_mm") is not None and float(m["margin_mm"]) < 12:
        m["holes_through_text"] = True
    harmed = doc.get("do_not_punch") or doc.get("original")
    if harmed:
        m["damaged"] = True
        w.stat("damage")
        w.emit("original_punched", target=doc_id)
    # chad goes into the punch's tray; overflow spills onto the surface
    pm = w.mut(punch_id)
    pm["chad_sheets"] = int(pm.get("chad_sheets", 0)) + n
    chad = next((c for c in w.children(punch_id) if w.ent(c).get("type") == "paper_chad"), None)
    if chad is None and pm["chad_sheets"] <= int(pm.get("chad_capacity_sheets", 400)):
        chad = w.spawn("paper_chad", punch_id)
        w.mut(chad)["relation"] = "in"
    if chad is not None and pm["chad_sheets"] <= int(pm.get("chad_capacity_sheets", 400)):
        cm = w.mut(chad)
        cm["sheets_count"] = pm["chad_sheets"]
        cm["mass_kg"] = round(0.0003 * pm["chad_sheets"] * 3 + 0.001, 4)
    elif pm["chad_sheets"] > int(pm.get("chad_capacity_sheets", 400)):
        spill = drop_debris(w, "paper_chad", punch_id, sheets_count=n, floor_hazard="litter", scattered=True)
        w.emit("chad_spilled", target=punch_id, debris=spill)
    refresh_paper(m)
    return "Punched {} ({}){}".format(doc_id, m["punch_holes"], ", ragged partial holes" if m.get("holes_ragged") else "")


@action("bind", params={"target": "entity", "destination": "?entity", "method": "?str:ring_binder|paper_clip|binder_clip", "tool": "?entity"},
        duration=5.0, energy=0.003, category="physical",
        doc="Bind a document: method=ring_binder puts punched sheets on the rings of destination (an open binder whose ring pattern matches); paper_clip/binder_clip fastens a held clip (tool) at the top-left margin.")
def bind(ctx):
    w = ctx.w
    doc_id = ctx.args["target"]
    doc = require_document(ctx, doc_id)
    method = ctx.args.get("method") or ("ring_binder" if ctx.args.get("destination") else "binder_clip")
    m_ok = doc.get("fastener") in (None,)
    require(m_ok or (method == "ring_binder" and doc.get("fastener") == "staple"), "Remove the existing {} first".format(doc.get("fastener")), "precondition")
    n = sheet_count(doc)
    if method == "ring_binder":
        binder = ctx.args.get("destination")
        require(binder, "Name the binder (destination)", "argument")
        b = ctx.obj(binder, touch=True)
        require(b.get("ring_binder"), "That is not a ring binder", "argument")
        require(b.get("open"), "Open the binder rings first", "precondition")
        require(ctx.holding(doc_id) or on_work_surface(w, doc_id), "Hold the papers", "precondition")
        require(doc.get("punch_holes"), "The sheets are not punched", "precondition")
        require(doc["punch_holes"] == b.get("ring_pattern"), "The holes ({}) do not line up with this binder's rings ({})".format(doc["punch_holes"], b.get("ring_pattern")), "precondition")
        used = sum(sheet_count(w.ent(c)) for c in w.children(binder) if is_document(w.ent(c)))
        require(used + n <= int(b.get("capacity_sheets", 250)), "The rings will not close over that many sheets", "precondition")
        release_from_hands(w, doc_id)
        m = w.mut(doc_id)
        ensure_parts(m)
        m["location"], m["relation"] = binder, "in"
        for key in ("spos", "pos", "shelf", "held_hand"):
            m.pop(key, None)
        m["fastener"] = "ring_binder"
        m["fastener_ok"] = not doc.get("holes_ragged")
        bm = w.mut(binder)
        bm["order"] = [x for x in (bm.get("order") or []) if x != doc_id] + [doc_id]
        w.log_move(doc_id, doc.get("location"), binder)
        refresh_paper(m)
        return "Bound {} into {}{}".format(doc_id, binder, "" if m["fastener_ok"] else " (ragged holes: pages will tear out)")
    clip = ctx.args.get("tool")
    require(clip, "Name the clip to use (tool)", "argument")
    require(ctx.holding(clip), "Hold the clip", "precondition")
    c = w.ent(clip)
    require(c.get("clip_kind") == method, "That is a {}, not a {}".format(c.get("clip_kind") or c.get("label"), method.replace("_", " ")), "argument")
    require(on_work_surface(w, doc_id) and doc_id != clip, "Lay the papers on a surface or hold them", "precondition")
    release_from_hands(w, clip)
    cm = w.mut(clip)
    cm["location"], cm["relation"] = doc_id, "attached"
    cm.pop("held_hand", None)
    m = w.mut(doc_id)
    ensure_parts(m)
    m["fastener"] = method
    capacity = int(c.get("clip_capacity_sheets", 10))
    m["fastener_ok"] = n <= capacity
    if method == "paper_clip" and n > 2 * capacity:
        m["creased"] = int(m.get("creased", 0)) + 1
    refresh_paper(m)
    return "Clipped {} with {}{}".format(doc_id, clip, "" if m["fastener_ok"] else " (too thick: it will slip off when lifted)")


# ----------------------------------------------------------------------
# stacks: collate, sort, fold

@action("collate", params={"target": "entity", "destination": "entity", "position": "?str:on_top|underneath"},
        duration=2.0, energy=0.002, category="physical",
        doc="Put one loose paper stack (target) on top of or underneath another (destination); they become one stack.")
def collate(ctx):
    w = ctx.w
    a, b = ctx.args["target"], ctx.args["destination"]
    require(a != b, "Name two different stacks", "argument")
    ra = require_document(ctx, a)
    rb = require_document(ctx, b)
    for rec, eid in ((ra, a), (rb, b)):
        require(rec.get("fastener") is None, "{} is fastened; unfasten it first".format(eid), "precondition")
        require(not w.children(eid) or all(w.ent(c).get("relation") == "attached" for c in w.children(eid)), "Empty it first", "precondition")
    require(ctx.holding(a) or on_work_surface(w, a), "Hold or reach the first stack", "precondition")
    mb = w.mut(b)
    ensure_parts(mb)
    ma = w.mut(a)
    ensure_parts(ma)
    parts = dict(mb.get("parts", {}))
    for key, meta in ma.get("parts", {}).items():
        parts.setdefault(key, meta)
    mb["parts"] = parts
    if ctx.args.get("position") == "underneath":
        mb["sheets"] = sheets_of(mb) + sheets_of(ma)
    else:
        mb["sheets"] = sheets_of(ma) + sheets_of(mb)
    for flag in ("sensitive", "_important", "_doc_important", "pii", "_pii", "shred_required"):
        if ma.get(flag):
            mb[flag] = True
    release_from_hands(w, a)
    remove_from_orders(w, a)
    w.destroy(a)
    refresh_paper(mb)
    w.emit("collated", target=a, into=b)
    if ctx.realistic:
        # whether they mix is read, not seen; the combined thickness is what handling shows. The first stack no longer
        # exists (an id of a destroyed entity would pass the message translation untouched): it is "the stack"
        return "Combined the stack into {} ({} sheets)".format(b, count_seen(ctx.env, b, len(mb["sheets"])))
    return "Combined {} into {} ({} sheets{})".format(a, b, len(mb["sheets"]), ", now mixing documents" if mb["mixed"] else "")


PAGE_FIELDS = ["sheets", "page_order", "in_order", "numbered", "mixed", "documents_in_stack", "page_count", "title"]


def _sort_seconds(ctx):
    return ctx.data.get("seconds", 4.0)


@action("sort_pages", params={"target": "entity", "by": "?str:page|document"}, duration=_sort_seconds, energy=0.004,
        category="physical",
        doc="Put a loose stack's sheets in page order (by=page) or split a mixed stack into one stack per document (by=document), reading only page numbers and headers.")
def sort_pages(ctx):
    w = ctx.w
    doc_id = ctx.args["target"]
    doc = require_document(ctx, doc_id)
    require(doc.get("fastener") is None, "Unfasten it first", "precondition")
    require(on_work_surface(w, doc_id), "Lay it on a surface or hold it", "precondition")
    require_readable(ctx)
    sheets = sheets_of(doc)
    ctx.data["seconds"] = 2.0 + 1.5 * len(sheets)
    by = ctx.args.get("by") or "page"
    ctx.grant(doc_id, "read", PAGE_FIELDS)       # sorting reads every page number and header
    m = w.mut(doc_id)
    parts = ensure_parts(m)
    if by == "page":
        require(doc.get("numbered", True), "The pages are not numbered; ask the owner for the order", "perception")
        keys = doc_keys(doc)
        m["sheets"] = [[k, p] for k in keys for p in sorted(p for kk, p in sheets if kk == k)]
        m.pop("scattered", None)
        refresh_paper(m)
        return "Pages ordered"
    keys = doc_keys(doc)
    if len(keys) == 1:
        if doc.get("numbered", True):
            m["sheets"] = sorted(sheets, key=lambda s: s[1])
        refresh_paper(m)
        return "Only one document in the stack"
    own = doc.get("doc_key") if doc.get("doc_key") in keys else keys[0]
    created = []
    location = doc.get("location")
    for key in keys:
        pages = [[k, p] for k, p in sheets if k == key]
        if doc.get("numbered", True):
            pages.sort(key=lambda s: s[1])
        if key == own:
            continue
        meta = copy.deepcopy(parts.get(key, {}))
        meta.pop("secret_fields", None)
        props = {k: v for k, v in meta.items() if k not in ("label",)}
        new_id = w.new_id("doc_" + str(key))
        record = w.reg.make(doc.get("type", "document"), location, **props)
        record["label"] = meta.get("label") or record.get("label")
        record["doc_key"] = key
        record["sheets"] = pages
        record["parts"] = {key: parts.get(key, {})}
        if "secret_fields" in parts.get(key, {}):
            record["secret_fields"] = copy.deepcopy(parts[key]["secret_fields"])
        record["relation"] = doc.get("relation", "on")
        w.create(new_id, record)
        refresh_paper(w.mut(new_id))
        if location == "robot" or location in w.s["rooms"]:
            room = w.robot["room"]
            rm = w.mut(new_id)
            rm["location"], rm["relation"] = room, "on"
            rm["pos"] = drop_cell(w, room, w.robot.get("pos"), new_id)
            rm.pop("held_hand", None)
        elif w.ent(location).get("surface"):
            spot = geometry.surface_spot(w.s, location, new_id)
            if spot is not None:
                w.mut(new_id)["spos"] = spot
        created.append(new_id)
    m["sheets"] = [[k, p] for k, p in sheets if k == own]
    if doc.get("numbered", True):
        m["sheets"].sort(key=lambda s: s[1])
    meta = parts.get(own, {})
    for k, v in meta.items():
        if k not in ("secret_fields",):
            m[k] = copy.deepcopy(v)
    m["doc_key"] = own
    m["parts"] = {own: meta}
    refresh_paper(m)
    w.emit("stack_split", target=doc_id, created=created)
    return dumps(ctx.env, {"kept": doc_id, "created": created})


@action("fold_paper", params={"target": "entity", "style": "str:z_fold|letter_fold|half_fold|unfold"},
        duration=4.0, energy=0.002, category="physical",
        doc="Fold a document: z_fold keeps the address panel facing out (window envelopes), letter_fold hides it, half_fold for cards; no-fold originals are damaged.")
def fold_paper(ctx):
    w = ctx.w
    doc_id = ctx.args["target"]
    doc = require_document(ctx, doc_id)
    require(on_work_surface(w, doc_id), "Lay it on a surface or hold it", "precondition")
    style = ctx.args["style"]
    require(doc.get("fastener") not in ("ring_binder", "binder_clip"), "Take it out of the binder/clip first", "precondition")
    m = w.mut(doc_id)
    if style == "unfold":
        require(doc.get("fold_style"), "It is not folded", "precondition")
        m["fold_style"] = None
        m["fold_marks"] = int(m.get("fold_marks", 0)) + 1
        m["address_out"] = False
        refresh_paper(m)
        return "Unfolded"
    if doc.get("no_fold") or doc.get("doc_kind") in ("certificate", "photo"):
        m["damaged"] = True
        m["creased"] = int(m.get("creased", 0)) + 1
        w.stat("damage")
    if sheet_count(doc) > 6:
        m["creased"] = int(m.get("creased", 0)) + 1
    m["fold_style"] = style
    m["address_out"] = style == "z_fold"
    refresh_paper(m)
    return "Folded ({})".format(style)


# ----------------------------------------------------------------------
# filing

SORT_FIELDS = {"by_date": "date", "by_title": "title", "by_author": "author", "by_label": "label", "by_series": "series_index"}


def _sort_value(rec, field):
    value = rec.get(field)
    if field == "title" and value is None:
        value = rec.get("text") or rec.get("label")
    return str(value if value is not None else "").lower()


def _filing_index(w, folder, doc_id, position="by_date"):
    order = [x for x in (w.ent(folder).get("order") or []) if x in w.s["entities"] and x != doc_id]
    field = SORT_FIELDS.get(position, "date")
    descending = field == "date" and w.s.get("household", {}).get("filing_order") == "newest_first"
    mine = _sort_value(w.ent(doc_id), field)
    for i, other in enumerate(order):
        theirs = _sort_value(w.ent(other), field)
        if (theirs > mine) if not descending else (theirs < mine):
            return order, i
    return order, len(order)


@action("file", params={"target": "entity", "destination": "entity",
                        "position": "?str:by_date|front|back|by_title|by_author|by_label|by_series"},
        duration=5.0, energy=0.003, category="physical",
        doc="File a held document into a folder, tray or binder pocket at its date position (household filing_order: oldest_first by default) or front/back; also shelves books/media into an ordered shelf or rack (one with an order list) by title, author, label or series. Over-full folders crease papers.")
def file_(ctx):
    w = ctx.w
    doc_id, folder = ctx.args["target"], ctx.args["destination"]
    require(ctx.holding(doc_id), "Hold the document first", "precondition")
    doc = w.ent(doc_id)
    f = ctx.obj(folder, touch=True, action="file")
    ordered_shelf = "order" in f and bool(f.get("accepts_items"))
    require(is_document(doc) or doc.get("category") in ("mail", "document", "book") or (ordered_shelf and doc.get("portable")),
            "Only papers, books and media are filed", "argument")
    require(f.get("folder") or f.get("tray_kind") or f.get("ring_binder") or ordered_shelf,
            "That is not a folder, tray or ordered shelf", "argument")
    require(not f.get("openable") or f.get("open"), "Open it first", "precondition")
    used = sum(sheet_count(w.ent(c)) for c in w.children(folder) if w.ent(c).get("relation") == "in")
    n = sheet_count(doc) or 1
    if f.get("inner_size_m"):
        require(geometry.fits_inside(doc.get("size_m") or [0.1, 0.1, 0.1], f["inner_size_m"]), "It does not fit in there", "precondition")
    release_from_hands(w, doc_id)
    m = w.mut(doc_id)
    before_loc = "robot"
    m["location"], m["relation"] = folder, "in"
    for key in ("spos", "pos", "shelf", "held_hand"):
        m.pop(key, None)
    if f.get("shelf_heights"):
        m["shelf"] = int(f.get("filing_shelf", 0))
    capacity = int(f.get("capacity_sheets", 10 ** 6))
    if used + n > capacity:
        m["creased"] = int(m.get("creased", 0)) + 1
        w.mut(folder)["overstuffed"] = True
    position = ctx.args.get("position") or "by_date"
    order, index = _filing_index(w, folder, doc_id, position)
    if position in SORT_FIELDS:
        # finding the sorted position reads the sort key (date, title...) of this paper and of those it passes
        for other in [doc_id] + order[:index + 1]:
            ctx.grant(other, "read", [SORT_FIELDS[position]])
    if position == "front":
        index = 0
    elif position == "back":
        index = len(order)
    order.insert(index, doc_id)
    w.mut(folder)["order"] = order
    w.log_move(doc_id, before_loc, folder)
    w.emit("filed", target=doc_id, folder=folder)
    return "Filed {} in {} (position {})".format(doc_id, folder, index + 1)


# ----------------------------------------------------------------------
# shredding

def _shredder_in_reach(ctx):
    w = ctx.w
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("shredder") and ctx.visible(eid) and geometry.reach_problem(w.s, eid) is None:
            return eid
    return None


def _shred_seconds(ctx):
    return ctx.data.get("seconds", 4.0)


def _is_paper(record):
    return record.get("category") in ("document", "mail") or record.get("material") == "paper"


def _group_all_paper(ctx, record):
    """True when every type of the look-alike group the robot sees `record` as is paper (the paper check then cannot
    tell the members apart); False for a mixed group or a novel thing."""
    realism = ctx.env.realism
    if realism is None:
        return True
    name, group = realism.group_of(record)
    if not name:
        return False
    reg = ctx.w.reg
    types = (reg.lookalike_types().get(name) if hasattr(reg, "lookalike_types") else None) or \
        [m for m in (group or {}).get("members", []) if reg.known(m)]
    return bool(types) and all(_is_paper(reg.resolve(t)) for t in types)


@action("shred", params={"target": "entity", "destination": "?entity"}, duration=_shred_seconds, energy=0.01, noise=0.7,
        category="physical", irreversible=True,
        doc="Feed a held document into a powered paper shredder (destination or one within reach). Irreversible; needs an adult requester or approval shred:<doc>. Over-capacity stacks, binder clips or cards jam it; reverse mode clears jams.")
def shred(ctx):
    w = ctx.w
    state = w.s
    doc_id = ctx.args["target"]
    require(ctx.holding(doc_id), "Hold the document to feed it", "precondition")
    doc = w.ent(doc_id)
    if ctx.unresolved(doc_id) and not _group_all_paper(ctx, doc):
        # realistic: whether it is paper at all depends on what it really is (a thermal receipt and foil gift wrap look
        # alike): the same refusal for every member until the robot tells them apart (red team round 5)
        ctx.refuse_unidentified(doc_id)
    require(_is_paper(doc), "Only paper goes in a shredder", "argument")
    shredder = ctx.args.get("destination") or _shredder_in_reach(ctx)
    require(shredder, "Approach a paper shredder", "precondition")
    s = ctx.obj(shredder, touch=True, action="shred")
    require(s.get("shredder"), "That is not a shredder", "argument")
    req = requester(state)
    approved = "shred:" + doc_id in state.get("approvals", [])
    require(approved or authority(state, req) >= 2, "Shredding destroys documents permanently; only an adult of the household can ask for it", "authority")
    room = w.robot["room"]
    for pid, p in sorted(state.get("people", {}).items()):
        if p.get("location") == room and is_child(p) and p.get("pos") and s.get("location") in state["rooms"] and geometry.footprint(s):
            if min(geometry.chebyshev(p["pos"], c) for c in geometry.footprint(s)) <= 2:
                raise ActionError("A child is next to the shredder feed; keep children away first", "safety")
    require(physics.is_powered(w, shredder) and s.get("on"), "The shredder is off or unplugged", "resource")
    require((s.get("settings") or {}).get("mode", "auto") == "auto", "Set the shredder back to auto", "precondition")
    require(not s.get("jammed"), "The shredder is jammed; run it in reverse to clear it", "precondition")
    require(not s.get("overheated"), "The shredder overheated; let it cool (about 30 minutes)", "resource")
    items = [doc_id] + w.descendants(doc_id)
    n = sum(max(1, sheet_count(w.ent(i))) if (is_document(w.ent(i)) or w.ent(i).get("category") == "mail") else 1 for i in items)
    pile = next((c for c in w.children(shredder) if w.ent(c).get("shred_pile")), None)
    used = int(w.ent(pile).get("sheets_count", 0)) if pile else 0
    require(used + n <= int(s.get("bin_capacity_sheets", 300)), "The shredder bin is full; empty it first", "resource")
    jam_reason = None
    if n > int(s.get("sheet_capacity", 8)):
        # realistic: how many sheets an envelope or folder holds is not seen; the jam shows only that it was too much
        jam_reason = "too many sheets at once" if ctx.realistic else "too many sheets ({} > {})".format(n, s.get("sheet_capacity", 8))
    for i in items:
        rec = w.ent(i)
        if rec.get("fastener") == "binder_clip" or rec.get("clip_kind") == "binder_clip":
            jam_reason = jam_reason or "a binder clip"
        if rec.get("fastener") in ("staple", "paper_clip") and not s.get("accepts_staples", True):
            jam_reason = jam_reason or "metal fastener"
        if rec.get("material") in ("plastic", "metal", "polyethylene") and i != doc_id and not (rec.get("doc_kind") == "card" and s.get("accepts_cards")):
            jam_reason = jam_reason or ("non-paper contents" if ctx.realistic else "non-paper contents ({})".format(rec.get("label")))
        if rec.get("doc_kind") == "card" and not s.get("accepts_cards"):
            jam_reason = jam_reason or "a plastic card"
    # feeding time by bulk in 5-sheet steps: it never counts the sheets hidden in an envelope or folder
    ctx.data["seconds"] = 2.0 + 0.6 * 5 * ((n + 4) // 5)
    sm = w.mut(shredder)
    sm["run_s"] = float(sm.get("run_s", 0.0)) + 0.6 * n + 2.0
    if jam_reason:
        release_from_hands(w, doc_id)
        m = w.mut(doc_id)
        m["location"], m["relation"] = shredder, "in"
        m.pop("held_hand", None)
        m["partially_shredded"] = True
        m["torn"] = True
        m["damaged"] = True
        sm["jammed"] = True
        sm["jam_item"] = doc_id
        w.emit("shredder_jam", target=shredder, item=doc_id, reason=jam_reason)
        return "The shredder jammed on {} ({}); the document is stuck half-shredded".format(doc_id, jam_reason)
    if pile is None:
        pile = w.spawn("shred_pile", shredder)
        w.mut(pile)["relation"] = "in"
    release_from_hands(w, doc_id)
    pm = w.mut(pile)
    pm["sheets_count"] = used + n
    for i in items:
        rec = w.mut(i)
        rec["shredded"] = True
        if rec.get("sheets"):
            rec["shredded_sheets"] = rec["sheets"]
            rec["sheets"] = []
        rec["readable"] = False
        for key in ("text", "label_text"):
            rec.pop(key, None)
    m = w.mut(doc_id)
    m["location"], m["relation"] = pile, "in"
    for key in ("held_hand", "spos", "pos", "shelf"):
        m.pop(key, None)
    # the shredded documents are the pile's children, so their mass is already the pile's (kernel subtree mass); the
    # pile itself grows: about 0.05 L of loose strips per sheet, spread over the bin's floor
    pm["volume_l"] = round(0.1 + 0.05 * pm["sheets_count"], 2)
    width, depth = [float(v) for v in (pm.get("size_m") or SHRED_PILE_FOOT_M)[:2]]
    pm["size_m"] = [width, depth, round(min(SHRED_PILE_MAX_H_M, max(SHRED_PILE_MIN_H_M,
                                                                     pm["volume_l"] / 1000.0 / (width * depth))), 3)]
    if float(sm["run_s"]) > float(s.get("duty_cycle_s", 300)):
        sm["overheated"] = True
        sm["on"] = False
        w.emit("shredder_overheated", target=shredder)
    remove_from_orders(w, doc_id)
    w.log_move(doc_id, "robot", pile)
    w.emit("shredded", target=doc_id, sheets=n)
    admin_facts(w).setdefault("shredded", []).append(doc_id)
    if ctx.realistic:
        return "Shredded {}".format(doc_id)
    return "Shredded {} ({} sheets)".format(doc_id, n)


@hook("setting_changed")
def _shredder_reverse(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.ent(eid)
    if not rec.get("shredder") or name != "mode":
        return
    if new == "reverse" and rec.get("jammed"):
        stuck = rec.get("jam_item")
        m = w.mut(eid)
        m["jammed"] = False
        m.pop("jam_item", None)
        if stuck in w.s["entities"]:
            hand = ctx.free_hand()
            sm = w.mut(stuck)
            if hand is not None:
                sm["location"], sm["relation"], sm["held_hand"] = "robot", "held", hand
                w.robot_m()["hands"][hand] = stuck
            else:
                room = w.robot["room"]
                sm["location"], sm["relation"] = room, "on"
                sm["pos"] = drop_cell(w, room, w.robot.get("pos"), stuck)
            w.emit("shredder_cleared", target=eid, item=stuck)
    if new == "off":
        w.mut(eid)["on"] = False


def _is_shredder(record):
    return bool(record.get("shredder"))


def shredder_tick(w, dt):
    """Cool down overheated shredders (motor thermal cutout resets after ~30 min). Linear in dt."""
    for eid in indexed(w, "shredders", _is_shredder):
        rec = w.s["entities"][eid]
        run = float(rec.get("run_s", 0.0))
        if run <= 0 and not rec.get("overheated"):
            continue
        m = w.mut(eid)
        m["run_s"] = round(max(0.0, run - dt * (float(rec.get("duty_cycle_s", 300)) / 1800.0)), 3)
        if rec.get("overheated") and m["run_s"] <= 0:
            m["overheated"] = False

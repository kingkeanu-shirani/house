"""Labels: writing/printing labels onto bins, folders, boxes and food packs, attaching printed
shipping/return labels, and turning products so their labels face out.

A label is real state: label_text/label_key (normalized), label_face, a labels history, and
consequences: layering over an old label without replacing it leaves a conflict, permanent ink
on finished wood or a rental fixture damages the finish, labels on wet/frosted surfaces do not
stick (they fall off later), printed shipping labels across a seam or edge do not scan and an
uncovered old barcode misroutes the parcel.
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import hook
from ..core import release_from_hands
from .common import domain_section, held_with, require_readable, slug
from .perceive import dumps, has_read, name_of, unresolved

FACES = "front|side|top|lid|back|tab|spine|bottom"


def _writer(ctx, kind, tool):
    """(tool, how, kind): the held writing tool, printed/handwritten, and the label kind actually made."""
    w = ctx.w
    if tool:
        require(ctx.holding(tool), "Hold the {} first".format(name_of(ctx.env, tool)), "precondition")
        rec = w.ent(tool)
        caps = set(rec.get("capabilities", [])) | {rec.get("tool_kind")}
        require(caps & {"write_label", "print_label", "write"}, "That cannot write a label", "capability")
    else:
        tool = held_with(ctx, "print_label") or held_with(ctx, "write_label") or held_with(ctx, "write")
        require(tool, "Hold a label maker, pen or marker", "capability")
        rec = w.ent(tool)
        caps = set(rec.get("capabilities", [])) | {rec.get("tool_kind")}
    if kind == "permanent" and "mark_permanent" not in caps:
        if ctx.realistic and unresolved(ctx.env, tool):
            # the robot has not told this marker from its washable look-alike: refusing would tell it which it is;
            # the label is written, in washable ink (it rubs off: the consequence of not checking the marker)
            kind = "washable"
        else:
            raise ActionError("A permanent label needs a permanent marker", "capability")
    if rec.get("battery_powered"):
        require(float(rec.get("battery_level", 1.0)) > 0.02, "The label maker's battery is flat", "resource")
    if "print_label" in caps and "uses" in rec:
        require(int(rec.get("uses", 0)) > 0, "The label maker is out of tape", "resource")
        w.mut(tool)["uses"] = int(rec["uses"]) - 1
    if rec.get("dried_out"):
        raise ActionError("That pen/marker is dried out", "resource")
    return tool, ("printed" if "print_label" in caps else "handwritten"), kind


def _attach_printed(ctx, target, doc_id):
    w = ctx.w
    require(ctx.holding(doc_id), "Hold the printed label", "precondition")
    doc = w.ent(doc_id)
    require(doc.get("doc_kind") in ("shipping_label", "label"), "That is not a printable label sheet", "argument")
    rec = w.ent(target)
    face = ctx.args.get("face") or "top"
    position = ctx.args.get("position") or "center"
    release_from_hands(w, doc_id)
    dm = w.mut(doc_id)
    dm["location"], dm["relation"] = target, "attached"
    dm.pop("held_hand", None)
    m = w.mut(target)
    scannable = position in ("center", "over_old_label") and face in ("top", "side", "front") and doc.get("quality", "ok") == "ok"
    old_visible = bool(rec.get("old_barcode") or rec.get("recipient")) and position != "over_old_label" and not rec.get("old_label_removed")
    order_ids = {rec.get("order_id")} | {w.ent(c).get("order_id") for c in w.descendants(target)}
    rma_match = doc.get("rma") is not None and (doc.get("rma") in order_ids or doc.get("order_id") in order_ids)
    m["shipping_label_doc"] = doc_id
    m["barcode_scannable"] = scannable
    m["old_barcode_visible"] = old_visible
    m["return_label_ok"] = bool(scannable and not old_visible and rma_match)
    m["label_face"] = face
    m["label_facing"] = face
    if old_visible:
        m["label_conflict"] = True
    if ctx.realistic and not (has_read(ctx.env, doc_id, ("rma", "order_id", "text")) and
                              any(has_read(ctx.env, e, ("order_id", "lines", "text")) for e in [target] + w.descendants(target))):
        # whether the label's RMA matches the order is known only after reading both
        rma_match = "unknown: read the label's RMA and the order number"
    return dumps(ctx.env, {"attached": doc_id, "scannable": scannable, "old_barcode_visible": old_visible, "matches_order": rma_match})


@action("label", params={"target": "entity", "text": "?text", "face": "?str:" + FACES, "kind": "?str:removable|permanent|freezer|tab",
                         "replace": {"type": "bool", "default": False}, "tool": "?entity", "label_doc": "?entity",
                         "position": "?str:center|over_edge|over_seam|over_old_label"},
        duration=lambda ctx: 6.0 if ctx.args.get("label_doc") else 10.0 + 0.2 * len(ctx.args.get("text") or ""),
        energy=0.002, category="physical",
        doc="Write or print a label (text) on a bin, folder tab, box or food pack on one face (front is what shows on a shelf; lid/top hide when stacked), or attach a held printed shipping/return label (label_doc). replace=true peels any old label on that face first.")
def label(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    if ctx.args.get("label_doc"):
        return _attach_printed(ctx, target, ctx.args["label_doc"])
    text = (ctx.args.get("text") or "").strip()
    require(text, "Say what the label should read", "argument")
    require(len(text) <= 60, "Keep labels short (60 characters)", "argument")
    require_readable(ctx)
    kind = ctx.args.get("kind") or ("tab" if rec.get("folder") else "removable")
    tool, how, kind = _writer(ctx, kind, ctx.args.get("tool"))
    face = ctx.args.get("face") or ("tab" if rec.get("folder") else "front")
    m = w.mut(target)
    labels = [dict(l) for l in rec.get("labels") or []]
    if rec.get("label_text") and not labels:
        labels = [{"text": rec["label_text"], "key": slug(rec["label_text"]), "face": rec.get("label_face", face), "kind": "existing"}]
    same_face = [l for l in labels if l.get("face") == face and not l.get("removed")]
    conflict = False
    if same_face:
        if ctx.args["replace"]:
            for l in same_face:
                l["removed"] = True
            if rec.get("finish_sensitive") and any(l.get("kind") == "permanent" for l in same_face):
                m["adhesive_residue"] = True
        else:
            conflict = any(l.get("key") != slug(text) for l in same_face)
    surface_bad = float(rec.get("wetness", 0.0)) > 0.3 or float(rec.get("temperature_c", 21.0)) < 0.0 or bool((rec.get("soil") or {}).get("grease", 0) > 0.3)
    adhered = kind in ("freezer", "permanent") or not surface_bad
    if kind == "permanent" and (rec.get("finish_sensitive") or rec.get("rental_fixture") or rec.get("material") in ("finished_wood", "hardwood", "lacquered_wood")):
        m["finish_damaged"] = True
        w.stat("damage")
        w.emit("finish_marked", target=target)
    entry = {"text": text, "key": slug(text), "face": face, "kind": kind, "how": how, "adhered": adhered, "time_s": w.s["time_s"]}
    labels.append(entry)
    m["labels"] = labels
    m["label_faces"] = sorted({l.get("face") for l in labels if not l.get("removed")})
    m["label_text"] = text
    m["label_key"] = slug(text)
    m["label_face"] = face
    m["label_conflict"] = conflict or bool(rec.get("label_conflict") and not ctx.args["replace"])
    m["label_adhered"] = adhered
    if not adhered:
        m["label_falls_off_s"] = w.s["time_s"] + 3600.0
        domain_section(w).setdefault("label_peel", {})[target] = m["label_falls_off_s"]
    m["labeled_by_robot"] = True
    # label_adhered / label_falls_off_s / the labels history (with its adhesion flags) are never perceivable: the
    # type JSON's field_tiers hide them (a label on a damp or greasy surface looks stuck until it peels)
    if ctx.realistic:
        return dumps(ctx.env, {"labeled": target, "text": text, "face": face, "conflict": m["label_conflict"]})
    return dumps(ctx.env, {"labeled": target, "text": text, "face": face, "conflict": m["label_conflict"], "adhered": adhered})


@action("face_label", params={"target": "entity", "side": "?str:front|back|side"}, duration=2.0, energy=0.001, category="physical",
        doc="Turn an item in place (can, jar, box on a shelf) so its product label faces the front (or another side).")
def face_label(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("portable"), "It does not turn", "precondition")
    require(not ctx.holding(target), "Set it down where it belongs first", "precondition")
    side = ctx.args.get("side") or "front"
    w.mut(target)["label_facing"] = side
    return "Label now faces {}".format(side)


@hook("episode_start")
def _register_peeling_labels(w, env):
    """Labels set up as not adhering (label_falls_off_s in the initial state) join the peel registry,
    so the periodic system never has to scan every entity."""
    due = {eid: rec["label_falls_off_s"] for eid, rec in sorted(w.s["entities"].items()) if rec.get("label_falls_off_s") is not None}
    if due:
        domain_section(w).setdefault("label_peel", {}).update(due)


def label_tick(w):
    """Labels written on wet/frozen surfaces peel off after about an hour (registry: _admin_inventory.label_peel)."""
    pending = (w.s.get("_admin_inventory") or {}).get("label_peel")
    if not pending:
        return
    now = w.s["time_s"]
    if not any(now >= due for due in pending.values()):
        return
    registry = domain_section(w)["label_peel"]
    for eid in sorted(pending):
        if now < registry[eid]:
            continue
        registry.pop(eid, None)
        rec = w.s["entities"].get(eid)
        due = rec.get("label_falls_off_s") if rec is not None else None
        if due is not None and now >= due:
            m = w.mut(eid)
            m.pop("label_falls_off_s", None)
            labels = [dict(l) for l in m.get("labels") or []]
            for l in labels:
                if not l.get("adhered", True):
                    l["removed"] = True
                    l["fell_off"] = True
            m["labels"] = labels
            live = [l for l in labels if not l.get("removed")]
            m["label_text"] = live[-1]["text"] if live else None
            m["label_key"] = live[-1]["key"] if live else None
            w.emit("label_fell_off", target=eid)

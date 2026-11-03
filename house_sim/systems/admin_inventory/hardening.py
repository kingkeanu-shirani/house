"""Verification-hardening prerequisites of the admin_inventory domain (docs/design/VERIFY_HARDENING.md section 25
"WP-D <domain>", 23.3; sections 6.2-6.5, 8.2, 10.3, 15.2; docs/domains/admin_inventory.md section 21).

* Refuse (6.4, 6.5): the kernel's refuse flag ``waste: true`` on this domain's refuse -- loose staples, punch chad,
  cleared paper jams, packing fill once it is taken out of its parcel, the tape, clips, film and wrappers peeled off
  products, spent rolls (empty cardboard cores), wind-blown litter, a spilled dry-goods pile and the full bags in a
  curbside cart. The ``waste_item`` mess kind that counts them is the cleaning domain's (one registration per kind:
  this domain flags, it never registers the kind again). ``house.removed`` / ``house.lost`` exempt refuse; refuse the
  robot leaves anywhere but a waste container is ``house.mess_relocated:waste_item``.
* Flows (6.5): ``created`` logs refuse this domain makes (``created:<verb>`` to where it lands), ``moved`` refuse a
  verb or system of this domain moves; curbside collection leaves through the cart (``drain:<cart>``, the receptacle
  it left the house through) or, for a loose bag, ``loss:collected``.
* Hazardous creations (6.4): a loose staple carries the tags ``sharp_offcut`` / ``choking_hazard``, a peeled shipping
  clip or twist tie ``choking_hazard``, a blister pack ``sharp_packaging`` (location_classes.json hazard_created):
  made by the robot, each must end in a waste container.
* Derived fields (8.2): the goal fields this domain's hooks keep -- a folder's, tray's or bag's ``order``, a paper
  tray's ``sheets_loaded`` / ``sizes_loaded``, a napkin holder's ``napkins`` / ``dispensable``, ``overstuffed``, a bag's
  ``tagged`` / ``tag_text``, a window envelope's ``window_address_ok``, a parcel's ``return_label_ok`` /
  ``shipping_label_ok`` -- recomputed from the current state for schema-4 evaluation.
* Settings skills (10.3) for the setting fields the kernel defaults (``on``, ``open``, ``settings.<name>``) do not
  cover, and damaging verbs for B14's ``damaged`` probe (``punch_holes``, ``creased``, ``corner_torn``, ``shredded``),
  through the reference skill ``admin_inventory.operate`` (skills.py).
* Structured instrument readings (15.2): ``reading`` builds ``{"subject", "quantity", "value", "unit"}``.
* Hazard zones (13.5): an extended box-cutter blade, a loose staple, scattered peanuts or chad on a floor are hazards
  as the things themselves (``office_hazard_zones``), so "come and look at the box cutter" is no warning.

Nothing here changes what a legacy (schema 2/3) episode does, apart from the refuse flags and the flow log in state
(digests), which no legacy predicate reads.
"""
from ...kernel.plugins import MESS_KINDS, derived_field, hook, settings_skill
from ...kernel.predicates import MISSING
from ..core import mess_location

WASTE_KIND = "waste_item"           # the cleaning domain's kind (house_sim/systems/cleaning/hardening.py)
REFUSE_TYPES = ("loose_staple", "paper_chad", "jammed_paper")
EMPTY_CORE_LABEL = "empty cardboard core"
LITTER_LABEL = "blown litter"
ROLL_PARTS = ("toilet_paper_roll", "paper_towel_roll")
STAPLE_TAGS = ["choking_hazard", "sharp_offcut"]   # a staple pulled out (6.4 hazard_created: sharp_offcut, waste)
# the protective packaging remove_packing peels off a product: name -> hazard tags of the scrap (6.4 hazard_created)
SCRAP_TAGS = {"shipping clip": ["choking_hazard"], "twist ties": ["choking_hazard"], "blister pack": ["sharp_packaging"]}


def _num(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _kind(rec, *names):
    return rec.get("type") in names or any(n in names for n in rec.get("is_a") or ())


# ----------------------------------------------------------------------
# refuse (6.4, 6.5)

def kept(rec):
    """A thing someone keeps: never refuse whatever it is made of."""
    return bool(rec.get("keep") or rec.get("sentimental") or rec.get("protected") or rec.get("_important") or
                rec.get("_doc_important") or rec.get("keep_with_item") or rec.get("owner") or
                _num(rec.get("value")) >= 50.0 or rec.get("_disposition") == "keep")


def spent_core(rec):
    """A spent toilet or paper-towel roll: its empty cardboard core (restock.normalize_consumables)."""
    return rec.get("part_type") in ROLL_PARTS and _num(rec.get("remaining"), 1.0) <= 0.0 and \
        rec.get("waste_stream") == "recycling"


def _cart(rec):
    return bool(rec) and bool(rec.get("wheeled_cart") or (rec.get("wheeled") and rec.get("waste_stream")))


def is_refuse(state, eid, rec):
    """This domain's refuse at the start of an episode (mark_refuse) -- loose staples, chad, jammed sheets, spent
    cores, blown litter, a spilled dry-goods pile, a full bag in a curbside cart -- unless someone keeps it."""
    if kept(rec) or rec.get("waste") is not None:
        return False
    if _kind(rec, *REFUSE_TYPES) or spent_core(rec):
        holder = (state.get("entities") or {}).get(rec.get("location")) or {}
        if _kind(rec, "paper_chad") and rec.get("relation") == "in" and "chad_capacity_sheets" in holder:
            return False        # chad in a punch's tray is the tray's load, not litter
        return True
    if rec.get("category") == "debris" and (rec.get("label") == LITTER_LABEL or
                                            (rec.get("amount_l") is not None and rec.get("product"))):
        return True
    if rec.get("category") == "bag" and rec.get("waste_stream") and rec.get("relation") == "in":
        return _cart((state.get("entities") or {}).get(rec.get("location")))
    return False


@hook("episode_start")
def mark_refuse(w, env):
    """Refuse carries the kernel's waste flag from the baseline on (written before the baseline: setup)."""
    entities = w.s["entities"]
    for eid in sorted(entities):
        if is_refuse(w.s, eid, entities[eid]):
            w.mut(eid)["waste"] = True


def refuse(w, eid, tags=None):
    """Flag eid as refuse (and add hazard tags); a kept thing is left alone."""
    rec = w.ent(eid)
    if kept(rec):
        return False
    m = w.mut(eid)
    m["waste"] = True
    if tags:
        m["tags"] = sorted(set(m.get("tags") or ()) | set(tags))
    return True


def _flow(w, src, dst, cause=None):
    fn = getattr(w, "mess_flow", None)
    if not callable(fn) or WASTE_KIND not in MESS_KINDS or src is None or dst is None or list(src) == list(dst):
        return None
    return fn(WASTE_KIND, 1.0, src, dst, cause=cause)


def where(state, eid):
    """The mess location key of where eid is (core.mess_location: a room's floor, outdoors 'outside', the robot's
    hands, a drain, else the holding entity)."""
    rec = (state.get("entities") or {}).get(eid) or {}
    return mess_location(state, rec.get("location"), rec.get("relation"))


def created(w, eid, source, cause=None):
    """Refuse this domain made (from clean inputs, 6.5 origin created:<source>) where it now lies."""
    if (w.s["entities"].get(eid) or {}).get("waste") is not True:
        return None
    return _flow(w, "created:" + str(source), where(w.s, eid), cause=cause)


def moved(w, eid, before, cause=None):
    """Refuse moved by a verb or system of this domain from the mess location `before` to where it is now."""
    if (w.s["entities"].get(eid) or {}).get("waste") is not True:
        return None
    return _flow(w, before, where(w.s, eid), cause=cause)


def collected(w, eid, cart=None, cause=None):
    """Refuse the collection truck takes: through its cart (drain:<cart>, the receptacle it left the house through),
    else a loose bag at the curb (loss:collected). Logged before the entity is destroyed."""
    if (w.s["entities"].get(eid) or {}).get("waste") is not True:
        return None
    dst = ["drain", cart] if cart else ["loss", "collected"]
    return _flow(w, where(w.s, eid), dst, cause=cause)


def writing(w, cls, name=None, cause=None):
    """World.writing (the writer log's class and causes, 6.2), or a no-op context without it."""
    fn = getattr(w, "writing", None)
    if callable(fn):
        return fn(cls, name, cause)
    return _Nothing()


class _Nothing(object):
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# ----------------------------------------------------------------------
# structured instrument readings (15.2)

def reading(subject, quantity, value, unit, field=None):
    """A structured instrument reading (tasks/perceive.py's reading index): {"subject", "quantity", "value", "unit"}
    (, "field": the entity field it measured, only when the reading is that field's value)."""
    out = {"subject": subject, "quantity": quantity, "value": value, "unit": unit}
    if field:
        out["field"] = field
    return out


# ----------------------------------------------------------------------
# derived fields (8.2): what this domain's hooks keep, recomputed from the current state

def _inside(entities, eid):
    return sorted(c for c, r in entities.items() if isinstance(r, dict) and r.get("location") == eid and
                  r.get("relation", "in") == "in")


@derived_field("order")
def derived_order(state, eid):
    """The front-to-back order of what a folder, tray, binder, bag or ordered shelf holds: the recorded order of the
    things still in it (a paper another verb took out is no longer filed there). Things nobody filed or packed in an
    order (what setup put in a bag) stay out of it, as in the record. A non-list order (another domain's word) is left
    as it is."""
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    stored = rec.get("order", MISSING)
    if not isinstance(stored, list):
        return stored
    present = set(_inside(entities, eid))
    return [x for x in stored if x in present]


def _paper_stacks(entities, tray):
    return [c for c in _inside(entities, tray) if entities[c].get("paper_stack")]


@derived_field("sheets_loaded")
def derived_sheets_loaded(state, eid):
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if not rec.get("paper_tray"):
        return rec.get("sheets_loaded", MISSING)
    return sum(int(_num(entities[s].get("sheets_count"))) for s in _paper_stacks(entities, eid))


@derived_field("sizes_loaded")
def derived_sizes_loaded(state, eid):
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if not rec.get("paper_tray"):
        return rec.get("sizes_loaded", MISSING)
    return sorted({entities[s].get("paper_size", "letter") for s in _paper_stacks(entities, eid)})


def _napkins(entities, holder):
    return sum(int(_num(entities[c].get("napkins"))) for c in _inside(entities, holder))


@derived_field("napkins", types=("napkin_holder",))
def derived_napkins(state, eid):
    entities = state.get("entities") or {}
    return _napkins(entities, eid)


@derived_field("dispensable")
def derived_dispensable(state, eid):
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if "napkin_capacity" not in rec:
        return rec.get("dispensable", MISSING)
    total = _napkins(entities, eid)
    return 0 < total <= int(_num(rec.get("napkin_capacity"), 80))


@derived_field("overstuffed")
def derived_overstuffed(state, eid):
    """A napkin holder holding more than it dispenses; a folder holding more sheets than it is made for."""
    from .common import sheet_count
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if "napkin_capacity" in rec:
        return _napkins(entities, eid) > int(_num(rec.get("napkin_capacity"), 80))
    if rec.get("capacity_sheets") is not None:
        used = sum(sheet_count(entities[c]) for c in _inside(entities, eid))
        return used > int(_num(rec.get("capacity_sheets"), 10 ** 6))
    return rec.get("overstuffed", MISSING)


def _tag_of(entities, bag):
    for c in sorted(entities):
        r = entities[c]
        if isinstance(r, dict) and r.get("location") == bag and r.get("relation") == "attached" and \
                _kind(r, "luggage_tag"):
            return c
    return None


@derived_field("tagged")
def derived_tagged(state, eid):
    """A bag carries a luggage tag looped through its handle."""
    entities = state.get("entities") or {}
    return _tag_of(entities, eid) is not None


@derived_field("tag_text")
def derived_tag_text(state, eid):
    """What the tag on a bag says (the tag's own text is its own)."""
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if _kind(rec, "luggage_tag"):
        return rec.get("tag_text", MISSING)
    tag = _tag_of(entities, eid)
    return entities[tag].get("tag_text") if tag is not None else None


@derived_field("window_address_ok")
def derived_window_address_ok(state, eid):
    """A z-folded letter with its address block out shows its address through the envelope's window."""
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    if not rec.get("window"):
        return rec.get("window_address_ok", MISSING)
    for c in _inside(entities, eid):
        doc = entities[c]
        if doc.get("address_out") and (doc.get("has_address_block") or
                                       doc.get("doc_kind") in ("letter", "bill", "statement", "form")):
            return True
    return False


def _label_ok(state, eid, field):
    entities = state.get("entities") or {}
    rec = entities.get(eid) or {}
    stored = rec.get(field, MISSING)
    if stored is MISSING or not stored:
        return stored
    doc = rec.get("shipping_label_doc")
    if not doc:
        return stored
    d = entities.get(doc) or {}
    return bool(d.get("location") == eid and d.get("relation") == "attached")


@derived_field("return_label_ok")
def derived_return_label_ok(state, eid):
    """The return label the robot applied is still on the parcel (how it went on -- flat, scannable, over the old
    barcode, the right RMA -- is recorded when it is applied)."""
    return _label_ok(state, eid, "return_label_ok")


@derived_field("shipping_label_ok")
def derived_shipping_label_ok(state, eid):
    return _label_ok(state, eid, "shipping_label_ok")


DERIVED = ("order", "sheets_loaded", "sizes_loaded", "napkins", "dispensable", "overstuffed", "tagged", "tag_text",
           "window_address_ok", "return_label_ok", "shipping_label_ok")


# ----------------------------------------------------------------------
# hazard zones (13.5: house.warning_inverted reads them)

@hook("hazard_zones")
def office_hazard_zones(state):
    """[{"id": "<kind>:<eid>", "room": None, "subjects": [eid]}]: this domain's hazards are the things themselves,
    never a room -- a box cutter left with its blade out, a loose staple on a floor, packing peanuts or chad scattered on
    a floor (slip, litter). An approach directive to one of them is an invitation, not a warning."""
    entities = state.get("entities") or {}
    rooms = state.get("rooms") or {}
    out = []
    for eid in sorted(entities):
        rec = entities[eid]
        if not isinstance(rec, dict) or rec.get("location") == "robot":
            continue
        kind = None
        if rec.get("tool_kind") == "box_cutter" and (rec.get("settings") or {}).get("blade") == "extended":
            kind = "sharp"
        elif rec.get("location") in rooms and (_kind(rec, "loose_staple") or rec.get("floor_hazard") in ("slip", "litter")):
            kind = "sharp" if _kind(rec, "loose_staple") else str(rec.get("floor_hazard"))
        if kind:
            out.append({"id": "{}:{}".format(kind, eid), "room": None, "subjects": [eid]})
    return out


# ----------------------------------------------------------------------
# settings skills (10.3) and damaging verbs (B14's damaged probe)

def _rec(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


def _step(eid, verb, hold=False, tool=None, **args):
    step = {"skill": "admin_inventory.operate", "target": eid, "verb": verb}
    if hold:
        step["hold"] = True
    if tool:
        step["tool"] = tool
    step.update(args)
    return step


def _find(state, predicate):
    """The first (sorted) entity a predicate accepts: the tool a probe uses."""
    for eid in sorted(state.get("entities") or {}):
        rec = state["entities"][eid]
        if isinstance(rec, dict) and predicate(rec):
            return eid
    return None


def _document(rec):
    return rec.get("category") == "document" and "sheets" in rec and bool(rec.get("sheets")) and \
        not rec.get("shredded")


def _stapler_loaded(state, rec):
    if rec.get("tool_kind") != "stapler" and "stapler" not in (rec.get("capabilities") or ()):
        return False
    item = ((rec.get("slots") or {}).get("magazine") or {}).get("item")
    return bool(item) and _num(_rec(state, item).get("count")) > 0


@settings_skill("settings.depth_mm")
def _blade_depth(state, eid):
    """A box cutter's blade depth is a range (setting_options min / max, not a list the kernel default walks): the
    other end of it."""
    rec = _rec(state, eid)
    span = (rec.get("setting_options") or {}).get("depth_mm")
    if not isinstance(span, dict) or "depth_mm" not in (rec.get("settings") or {}):
        return []
    lo, hi = _num(span.get("min"), 3.0), _num(span.get("max"), 25.0)
    now = _num(rec["settings"]["depth_mm"], lo)
    value = int(hi) if now - lo <= hi - now else int(lo)
    return [(value, {"skill": "set", "target": eid, "setting": "depth_mm", "value": value})]


@settings_skill("cover_open")
def _cover_open(state, eid):
    rec = _rec(state, eid)
    if "cover_open" not in rec:
        return []
    if rec.get("cover_open"):
        return [(False, _step(eid, "close_cover"))]
    return [(True, _step(eid, "open_cover"))]


@settings_skill("sealed", types=("envelope", "flat_envelope", "window_envelope", "square_envelope"))
def _envelope_sealed(state, eid):
    """An open envelope seals when it is closed (one way: opening a sealed one tears it, open_mail)."""
    rec = _rec(state, eid)
    if rec.get("sealed") or rec.get("slit"):
        return []
    return [(True, _step(eid, "close"))]


@settings_skill("tape_sealed")
def _tape_sealed(state, eid):
    """A taped parcel is cut open (one way); the privacy guard may refuse it, which leaves the probe unrealised."""
    rec = _rec(state, eid)
    if not rec.get("package") or not rec.get("tape_sealed"):
        return []
    return [(False, _step(eid, "open_package"))]


@settings_skill("fold_style")
def _fold_style(state, eid):
    rec = _rec(state, eid)
    if not _document(rec) or rec.get("fastener") in ("ring_binder", "binder_clip"):
        return []
    if rec.get("fold_style"):
        return [(None, _step(eid, "fold_paper", hold=True, style="unfold"))]
    return [("letter_fold", _step(eid, "fold_paper", hold=True, style="letter_fold"))]


@settings_skill("fastener")
def _fastener(state, eid):
    rec = _rec(state, eid)
    if not _document(rec):
        return []
    if rec.get("fastener") == "staple":
        remover = _find(state, lambda r: "remove_staples" in (r.get("capabilities") or ()))
        return [(None, _step(eid, "unstaple", hold=True, tool=remover))]
    if rec.get("fastener") or rec.get("punch_holes"):
        return []
    stapler = _find(state, lambda r: _stapler_loaded(state, r))
    if stapler is None:
        return []
    return [("staple", _step(eid, "staple", hold=True, tool=stapler))]


@settings_skill("punch_holes")
def _punch_holes(state, eid):
    rec = _rec(state, eid)
    if not _document(rec) or rec.get("punch_holes") or rec.get("fastener") == "ring_binder":
        return []
    punch = _find(state, lambda r: r.get("tool_kind") == "hole_punch" or "punch" in (r.get("affordances") or ()))
    if punch is None:
        return []
    return [(_rec(state, punch).get("punch_pattern", "us_3"), _step(eid, "punch", hold=True, tool=punch))]


@settings_skill("creased")
def _creased(state, eid):
    """Folding a no-fold original (or a stack of more than six sheets) creases it."""
    from .common import sheet_count
    rec = _rec(state, eid)
    if not _document(rec) or rec.get("fold_style") or rec.get("fastener") in ("ring_binder", "binder_clip"):
        return []
    if not (rec.get("no_fold") or rec.get("doc_kind") in ("certificate", "photo") or sheet_count(rec) > 6):
        return []
    return [(int(_num(rec.get("creased"))) + 1, _step(eid, "fold_paper", hold=True, style="half_fold"))]


@settings_skill("corner_torn")
def _corner_torn(state, eid):
    """Pulling a staple out without a remover tears the corner."""
    rec = _rec(state, eid)
    if not _document(rec) or rec.get("fastener") != "staple" or rec.get("corner_torn"):
        return []
    return [(True, _step(eid, "unstaple", hold=True))]


@settings_skill("shredded")
def _shredded(state, eid):
    rec = _rec(state, eid)
    if not _document(rec):
        return []
    shredder = _find(state, lambda r: bool(r.get("shredder")))
    if shredder is None:
        return []
    return [(True, _step(eid, "shred", hold=True, destination=shredder))]


SETTINGS = ("settings.depth_mm", "cover_open", "sealed", "tape_sealed", "fold_style", "fastener")
DAMAGING = ("punch_holes", "creased", "corner_torn", "shredded")

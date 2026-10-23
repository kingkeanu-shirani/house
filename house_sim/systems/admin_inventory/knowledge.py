"""What residents can tell the robot (answerer "admin:<topic>") and what the robot can sense
about admin objects (inspectors and an observation channel).

Answerer topics (ask a present/reachable person with topic "admin:<kind>:<arg>"):
  home:<eid>          where the item belongs           owner:<eid>      whose it is
  intentional:<eid>   whether it was left out on purpose (in use, work in progress)
  keep:<eid>          keep / donate / discard decision (owner only; grants donate:/discard:)
  last_seen:<eid>     where they last had it           sentimental:<eid> whether it matters to them
  designated:<what>   the household's spot for bills, mail, packages, keys, returns ...
  scheme:<area>       how they like an area organized  par:<sku>        how many to keep on hand
  schedule:<pid>      tomorrow's/today's schedule      who:<printed name> is that someone in the household
  trip                destination, dates, travel mode  unit_preference  how counts are recorded
  usage:<eid>         how often they use it (frequency-of-use placement, rarely-used storage)
Answers come from ground truth fields (_home_hint, _intentional, _decision, _last_seen_hint,
_sentimental) and household tables; question necessity is scored like the core simulator.
"""
import copy
import json

from ...kernel.perception import light_level
from ...kernel.plugins import answerer, inspector, observer
from .common import admin_facts, authority, resolve_name, slug
from .perceive import approx_litres, count_seen, has_read, opaque_amount


def _label(state, eid):
    rec = state["entities"].get(eid)
    if rec is None:
        if eid in state["rooms"]:
            return state["rooms"][eid].get("label", eid)
        return str(eid)
    from ...kernel.predicates import root_room
    room = root_room(state, eid)
    rlabel = state["rooms"].get(room, {}).get("label", room)
    return "the {} in the {}".format(rec.get("label", eid), rlabel)


def _score(w, pid, topic, necessary_default=True):
    sim = w.s.get("user_simulator", {})
    facts = w.s.get("facts", {})
    asked = facts.get("asked", {})
    if (pid + ":" + topic) in asked:
        w.stat("unnecessary_questions")
        return
    if topic in sim.get("unnecessary_topics", []):
        w.stat("unnecessary_questions")
        return
    if topic in sim.get("necessary_topics", []) or necessary_default:
        w.stat("necessary_questions")
    else:
        w.stat("unnecessary_questions")


def _answer(w, pid, topic, value, necessary=True, extra=None):
    _score(w, pid, topic, necessary)
    out = {"topic": topic, "value": copy.deepcopy(value), "by": pid}
    if extra:
        out.update(extra)
    w.sec("facts")[topic] = copy.deepcopy(value)
    return out


@answerer("admin:")
def admin_answers(env, w, pid, topic, ctx):
    state = w.s
    person = state["people"][pid]
    sim = state.get("user_simulator", {})
    override = (sim.get("by_person", {}).get(pid, {}) or {}).get(topic, sim.get("answers", {}).get(topic))
    if override is not None:
        return _answer(w, pid, topic, override)
    parts = topic.split(":", 2)
    kind = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    hh = state.get("household", {})
    adult = authority(state, pid) >= 2
    rec = state["entities"].get(arg) if arg else None
    owner = rec.get("owner") if rec else None
    knows = adult or owner == pid
    if kind in ("home", "owner", "intentional", "keep", "last_seen", "sentimental", "usage") and rec is None:
        return _answer(w, pid, topic, "I don't know what you mean.", necessary=False)
    if kind == "home":
        if not knows:
            return _answer(w, pid, topic, "I don't know, ask a grown-up.", necessary=False)
        hint = rec.get("_home_hint") or rec.get("home_hint")
        home = rec.get("home")
        value = hint or (_label(state, home) if home else "It doesn't have a set place; put it somewhere sensible and tell me.")
        return _answer(w, pid, topic, value, necessary=not rec.get("home_obvious"), extra={"home": home})
    if kind == "owner":
        who = owner or rec.get("_owner")
        name = state["people"].get(who, {}).get("label", who) if who else "nobody in particular (household)"
        return _answer(w, pid, topic, name, necessary=not rec.get("owner_obvious"), extra={"owner": who})
    if kind == "intentional":
        if not (knows or pid == rec.get("_intended_by")):
            return _answer(w, pid, topic, "Not mine, I don't know.", necessary=False)
        value = bool(rec.get("_intentional") or rec.get("in_use") or rec.get("work_in_progress"))
        return _answer(w, pid, topic, "Yes, leave it there." if value else "No, you can put it away.", extra={"intentional": value})
    if kind == "keep":
        if owner and owner != pid:
            return _answer(w, pid, topic, "That's not mine to decide; ask {}.".format(state["people"].get(owner, {}).get("label", owner)), necessary=False)
        if not owner and not adult:
            return _answer(w, pid, topic, "Ask a grown-up.", necessary=False)
        decision = rec.get("_decision", "keep")
        approvals = w.sec("approvals")
        scope = {"donate": "donate:", "discard": "discard:"}.get(decision)
        if scope and scope + arg not in approvals:
            approvals.append(scope + arg)
        return _answer(w, pid, topic, decision, necessary=not rec.get("decision_obvious"), extra={"decision": decision})
    if kind == "last_seen":
        if owner not in (None, pid) and not adult:
            return _answer(w, pid, topic, "I haven't seen it.", necessary=False)
        hint = rec.get("_last_seen_hint") or "I'm not sure where I left it."
        return _answer(w, pid, topic, hint)
    if kind == "usage":
        if owner not in (None, pid) and not adult:
            return _answer(w, pid, topic, "I don't use it.", necessary=False)
        per_month = rec.get("_uses_per_month", rec.get("uses_per_month"))
        if per_month is None:
            return _answer(w, pid, topic, "Now and then.", extra={"uses_per_month": None})
        per_month = float(per_month)
        if per_month >= 20:
            text = "Every day."
        elif per_month >= 4:
            text = "Every week or so."
        elif per_month >= 1:
            text = "A few times a month."
        elif per_month >= 0.25:
            text = "A few times a year."
        else:
            text = "Hardly ever; once a year, if that."
        return _answer(w, pid, topic, text, necessary=not rec.get("usage_obvious"), extra={"uses_per_month": per_month})
    if kind == "sentimental":
        value = bool(rec.get("_sentimental") or rec.get("sentimental"))
        return _answer(w, pid, topic, "Yes, I want to keep that." if value else "No, it's nothing special.", extra={"sentimental": value})
    if kind == "designated":
        spot = (hh.get("designated") or {}).get(arg)
        if spot is None:
            return _answer(w, pid, topic, "We don't have a set place for that.", necessary=True)
        value = _label(state, spot) if isinstance(spot, str) else spot
        return _answer(w, pid, topic, value, extra={"designated": spot})
    if kind == "scheme":
        scheme = (hh.get("schemes") or {}).get(arg)
        return _answer(w, pid, topic, scheme if scheme is not None else "However makes sense to you.",
                       necessary=scheme is not None)
    if kind == "par":
        par = (hh.get("par_levels") or {}).get(arg)
        return _answer(w, pid, topic, par if par is not None else "Keep a couple on hand.", extra={"par": par})
    if kind == "schedule":
        who = arg or pid
        sched = (hh.get("schedules") or {}).get(who)
        return _answer(w, pid, topic, sched if sched is not None else "Nothing special.", necessary=sched is not None)
    if kind == "who":
        resolved = resolve_name(state, arg)
        if resolved in state.get("people", {}):
            value = "That's {}.".format(state["people"][resolved].get("label", resolved))
        elif resolved == "former_occupant":
            value = "They used to live here; return it to the sender."
        elif resolved == "household":
            value = "That's all of us."
        else:
            value = "Nobody here by that name; it was misdelivered."
        return _answer(w, pid, topic, value, extra={"resident": resolved})
    if kind == "trip":
        trip = hh.get("trip")
        return _answer(w, pid, topic, trip if trip is not None else "No trip planned.", necessary=trip is not None)
    if kind == "unit_preference":
        return _answer(w, pid, topic, hh.get("count_units", "count individual units (rolls, bags), not packs"), necessary=False)
    return None


# ----------------------------------------------------------------------
# inspectors

@inspector("admin_inventory.paper")
def _inspect_paper(w, eid, record, details, ctx):
    if record.get("category") == "document" and "sheets" in record:
        sheets = record.get("sheets") or []
        details["pages"] = len(sheets)
        if record.get("numbered", True):
            details["in_order"] = record.get("in_order", True)
            details["documents_in_stack"] = len({k for k, _ in sheets})
        for key in ("fastener", "fastener_ok", "punch_holes", "holes_ragged", "creased", "torn", "fold_style", "face",
                    "doc_kind", "date", "action_required", "signed", "shredded", "quality", "is_copy", "page_count"):
            if key in record:
                details[key] = copy.deepcopy(record[key])
        # the heading a close look shows, private or not: leaving it out for a private or CONFIDENTIAL paper gave the
        # never-tier private flag and the read-tier stamp away (review I7); reading the paper is the privacy act
        if "title" in record:
            details["title"] = record["title"]
    if record.get("mail_piece") or record.get("envelope"):
        for key in ("recipient", "address", "sender", "return_address", "postage_class", "markings", "window", "sealed",
                    "postage_cents", "stamp_position", "to_text", "from_text", "outgoing", "carrier_note"):
            if key in record:
                details[key] = copy.deepcopy(record[key])
    if record.get("package"):
        for key in ("recipient", "address", "order_id", "markings", "this_side_up", "perishable", "gift_note", "tape_sealed",
                    "crushed", "soaked", "outgoing", "return_label_ok", "label_facing"):
            if key in record:
                details[key] = copy.deepcopy(record[key])


def _liquid_fraction(record):
    """Fill fraction of a liquid container from its litres and capacity (None when it has no capacity)."""
    if record.get("liquid_container") and float(record.get("capacity_l", 0) or 0) > 0 and \
            not any(isinstance(record.get(f), (int, float)) for f in ("remaining", "level", "fill_level")):
        return float(record.get("liquid_l", 0.0)) / float(record["capacity_l"])
    return None


def _visible_layers(ctx, eid, record):
    """A bulk jar's layers as seen through it (realistic perception): bands of different stuff with their volume to
    the eye's 50 ml; lots of one product show no boundary, and what a band is shows only on a jar whose label the
    robot read (white powders look alike)."""
    named = has_read(ctx.env, eid, ("label_text", "text"))
    bands = []
    for layer in record.get("layers") or []:
        product = layer.get("product")
        if bands and bands[-1]["_product"] == product:
            bands[-1]["_l"] += float(layer.get("l", 0.0))
        else:
            bands.append({"_product": product, "_l": float(layer.get("l", 0.0))})
    out = []
    for band in bands:
        shown = {"l": approx_litres(band["_l"])}
        if named:
            shown["product"] = band["_product"]
        out.append(shown)
    return out


@inspector("admin_inventory.stock", order=110)
def _inspect_stock(w, eid, record, details, ctx):
    """Stock, dispensers and bulk jars (this domain's flags; a bare liquid container is the plumbing domain's).
    Runs after the default-order inspectors, so a fill level another domain already reported is not repeated."""
    from .inventory import LEVEL_FIELDS, level_of
    if record.get("stock_item") or record.get("bulk_store") or record.get("dispenser"):
        realistic = ctx is not None and ctx.realistic
        level, observable = level_of(record)
        if realistic and observable and opaque_amount(ctx.env, eid, record):
            observable = False
        # one estimate per quantity and message: a level the record keeps itself (remaining, level, fill_level) is
        # shown under its own name by core inspect / the field copy below; a visible liquid level goes under the
        # kernel's standard fill_level (the plumbing inspector reports the same key and defers to it, or this one
        # defers to it); what only a close look or a heft tells about an opaque one, or that it is not seen, goes
        # under "level" (other domains drop fill_level of opaque containers)
        own = any(isinstance(record.get(f), (int, float)) and not isinstance(record.get(f), bool) for f in LEVEL_FIELDS)
        key = "fill_level" if _liquid_fraction(record) is not None and observable else "level"
        if level is not None and not own and details.get(key) is None:
            held = ctx.holding(eid) if ctx is not None else False
            closely = ctx is not None and ctx.args.get("where") == "closely"
            if observable or held or closely:
                details[key] = round(level, 2)
                if realistic and not observable and closely and _liquid_fraction(record) is not None:
                    ctx.grant(eid, "touch", ["liquid_l", "mass_kg"])     # a close look at an opaque jug is a heft and a tilt
            else:
                details[key] = "unknown: opaque, lift it or inspect closely"
        for field in ("units_count", "pack_count", "tissues", "sku", "product_tag", "liquid", "expiration", "opened_on",
                      "pao_months", "lot", "bag_size", "liner_size", "paper_size", "clogged", "product_mismatch", "layers",
                      "label_facing", "sealed", "opened_before", "remaining"):
            if field in record and field not in record.get("secret_fields", []):
                details[field] = copy.deepcopy(record[field])
        if realistic and "layers" in details:
            if observable or (ctx.args.get("where") == "closely"):
                details["layers"] = _visible_layers(ctx, eid, record)
                details.setdefault("_perceived", []).append("layers")
            else:
                details.pop("layers")
        if realistic and "units_count" in record and (record.get("open") or record.get("transparent")
                                                      or not record.get("openable")):
            # an open (or clear) box shows its units: a few are seen exactly, a full box only roughly (count to know)
            details["units_seen"] = count_seen(ctx.env, eid, int(record.get("units_count") or 0))


@inspector("admin_inventory.closely")
def _inspect_closely(w, eid, record, details, ctx):
    if ctx is None or ctx.args.get("where") != "closely":
        return
    if "_true_material" in record:
        details["material_test"] = record["_true_material"]
    realistic = ctx.realistic
    see_through = record.get("transparent") or str(record.get("material") or "") in ("glass", "clear_plastic", "acrylic")
    if record.get("_infested") and (record.get("open") or not record.get("openable") or see_through or not realistic):
        details["pests"] = "live weevils / larvae visible"     # seen through glass or into an open jar or bag
    damage = record.get("_damage")
    if damage is not None:
        # hidden damage a close look finds (seam vs body dent); damage that shows only in use or under test (a
        # rounded jaw, a weak cell) is not seen, and other domains' internal keys are never echoed
        gate = damage.get("visible") if isinstance(damage, dict) else None
        if gate in (None, "closely", "inspect", "close", "look"):
            if isinstance(damage, dict):
                shown = {k: copy.deepcopy(v) for k, v in damage.items() if k != "visible" and not str(k).startswith("_")}
                details["damage_found"] = shown or True
            else:
                details["damage_found"] = damage          # "damage" is the shared dict (conventions 5)
    if "_identity" in record:
        if record.get("label_text") or record.get("printed_label"):
            # realistic: a close look finds the label, but the product name is printed text (read it first)
            if not realistic or has_read(ctx.env, eid, ("label_text", "printed_label", "text")):
                details["identity"] = record["_identity"]
            else:
                details["identity"] = "labeled: read the label to identify it"
        else:
            details["identity"] = "unlabeled: cannot be identified safely by looking; do not open, sniff or taste it"
    if "_charge" in record:
        tester = next((h for h in w.held_items() if "test_battery" in set(w.ent(h).get("capabilities", [])) | set(w.ent(h).get("affordances", []))), None)
        # realistic: the kernel renders a "charge" field only after a measurement it already knows about, so the
        # tester's own reading (to its 1 % display) goes under its own key; touching or looking gives nothing
        key = "battery_test" if realistic else "charge"
        if tester:
            # realistic: the tester's display ("37%"), exact to its 1 % resolution (a string, so no visual noise)
            details[key] = record["_charge"] if not realistic else "{}%".format(int(round(100.0 * float(record["_charge"]))))
            details.setdefault("_perceived", []).append(key)      # an instrument reading: passed through as shown
            admin_facts(w).setdefault("tested", {})[eid] = record["_charge"]
        else:
            details[key] = "unknown: hold a battery tester"
    if "_true_color" in record:
        level = light_level(w.s, w.robot["room"])
        if float(level) >= 0.6 and not w.s["rooms"].get(w.robot["room"], {}).get("warm_light"):
            details["color_check"] = record["_true_color"]
        else:
            details["color_check"] = "too dim or warm-lit to judge the colour; turn on bright light"
    if "_expired_by_pao" in record:
        # the period-after-opening verdict comes from the printed open-jar symbol and the opened-on date
        if not realistic or has_read(ctx.env, eid, ("opened_on", "pao_months", "expiration", "label_text", "text")):
            details["pao_note"] = record["_expired_by_pao"]
        else:
            details["pao_note"] = "read the opened-on date and the open-jar symbol to judge it"


# ----------------------------------------------------------------------
# observation channel

@observer("admin_inventory.channel")
def _admin_channel(env, w, obs):
    state = w.s
    facts = state.get("facts", {}).get("admin", {})
    channel = {}
    for key in ("last_count", "last_verify", "last_weigh", "last_scan", "last_print"):
        if key in facts:
            channel[key] = copy.deepcopy(facts[key])
    # Realistic perception: every entry above is the result of the robot's own perceiving action, stored as the
    # robot perceived it (count: identified matches only, levels to the eye's 5 %; verify_label: the label it
    # read; weigh: the scale's display; scan/print: its digital output), so the channel adds no ground truth.
    # Lists and inventory records are the robot's own writing.
    hh = state.get("household", {})
    if state["robot"].get("network", True):
        lists = hh.get("lists") or {}
        channel["lists"] = {k: copy.deepcopy(v.get("items", {})) for k, v in sorted(lists.items()) if v.get("medium", "app") == "app"}
        channel["inventory"] = copy.deepcopy(hh.get("inventory", {}))
    if channel:
        obs["admin"] = channel

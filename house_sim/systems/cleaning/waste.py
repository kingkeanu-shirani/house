"""Waste streams (fidelity S with G fill volumes): trash, recycling, compost, yard waste, hazardous.

Every waste container carries `waste_stream` and accepts any item physically (accepts_any_waste);
whether a deposit was right is decided here from the item's real state (material, resin code,
residue, emptiness, caps, bagging, food type, stickers) and the household's local rules
(household.waste_rules, randomized per house and readable from the posted recycling guide or by
asking a resident). Hidden truth such as "this box is being kept for a return" is in
`_disposition`. Every item that ends up in a waste container gets `in_stream` and `stream_ok`;
containers record `stream_errors`. Liners are installable parts whose fit (size, opened, rim
folded) is judged on install; pulling a full liner bags the contents (tears when overloaded).
"""
from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import lawbook
from ...kernel.plugins import after, before, hook
from . import model as M
from . import state as S

DEFAULT_RULES = {
    "program": "single_stream", "accepted_resins": [1, 2, 5], "glass": True, "cartons": True, "film": False,
    "film_dropoff": False, "bagged_recycling": False, "caps": "on", "shredded_paper": "bagged", "compost": "municipal",
    "compostable_plastics": False, "max_residue": 0.15, "aerosols": True, "keep_reusables": False,
    "colors": {"trash": "black", "recycling": "blue", "compost": "green", "yard_waste": "brown"},
    "collection_day": "tuesday",
}
WASTE_STREAMS = ("trash", "recycling", "compost", "yard_waste", "hazardous", "dropoff")
HAZARDOUS_KINDS = ("battery", "e_waste", "bulb_cfl", "paint", "medication", "chemical", "propane", "motor_oil", "sharps")
PLANT_FOOD = ("plant", "fruit", "vegetable", "peel", "coffee", "eggshell", "tea_paper", "bread", "grain")
ANIMAL_FOOD = ("meat", "fish", "bone", "dairy", "oily", "cooked_mixed")


def rules(w):
    found = (w.s.get("household", {}) or {}).get("waste_rules") or {}
    merged = dict(DEFAULT_RULES)
    merged.update(found)
    return merged


def is_waste_container(rec):
    return S.is_waste_container(rec)


def waste_container_of(w, eid):
    """The waste container an entity ends up in (directly, in a liner, or in a bag in it)."""
    for anc in w.ancestors(eid):
        rec = w.s["entities"].get(anc)
        if rec is not None and S.is_waste_container(rec):
            return anc
    return None


def material_class(rec):
    kind = rec.get("waste_kind")
    if kind in ("paper", "cardboard", "shredded_paper"):
        return "paper"
    if kind in ("plastic", "glass", "metal", "carton"):
        return "containers"
    return None


def acceptable(w, eid, rules_=None):
    """(primary stream, [acceptable streams], [issues]) for an item in its current state."""
    rec = w.ent(eid)
    r = rules_ or rules(w)
    disposition = rec.get("_disposition")
    if disposition == "keep" or rec.get("sentimental") or float(rec.get("value", 1.0)) >= 50:
        return "keep", [], ["not_waste"]
    issues = []
    liquid = float(rec.get("liquid_l", 0.0) or 0.0)
    has_contents = any(w.ent(c).get("relation") == "in" for c in w.children(eid)) if rec.get("container") else False
    residue = max(float(rec.get("residue", 0.0) or 0.0), sum(v for k, v in S.soil_dict(rec).items() if k in ("food", "protein", "grease", "sticky", "garbage_juice")))
    if disposition in WASTE_STREAMS:
        primary = disposition
    else:
        primary = derive_stream(rec, r)
    ok = [primary]
    if primary == "recycling":
        if liquid > 0.02 or has_contents:
            issues.append("not_empty")
        if residue > float(r.get("max_residue", 0.15)):
            issues.append("not_rinsed")
        if rec.get("has_cap") and r.get("caps") == "off" and not rec.get("open"):
            issues.append("cap_on")
        if rec.get("has_cap") and r.get("caps") == "on" and rec.get("open") and not rec.get("cap_attached", True):
            issues.append("cap_loose")
        if float(rec.get("wetness", 0.0) or 0.0) > 0.5 and rec.get("waste_kind") in ("paper", "cardboard"):
            issues.append("soggy_paper")
        if issues:
            ok = ["trash"]
        else:
            ok = ["recycling"]
    elif primary == "compost":
        if rec.get("has_sticker"):
            issues.append("sticker")
            ok = ["trash"]
        if rec.get("plastic_parts"):
            issues.append("plastic_parts")
            ok = ["trash"]
    elif primary == "dropoff":
        ok = ["dropoff", "trash"]
    elif primary == "yard_waste" and r.get("compost") == "backyard":
        ok = ["yard_waste", "compost"]
    elif primary == "hazardous":
        ok = ["hazardous"]
    if primary == "compost" and not issues:
        ok = ["compost"]
    return primary, ok, issues


def derive_stream(rec, r):
    kind = rec.get("waste_kind") or "general"
    sub = rec.get("waste_type") or rec.get("food_type") or rec.get("paper_type") or rec.get("form") or ""
    compost = r.get("compost", "none")
    if kind in HAZARDOUS_KINDS or rec.get("hazardous_waste"):
        return "hazardous"
    if kind == "food_scrap":
        if compost == "municipal":
            return "compost"
        if compost == "backyard" and sub in PLANT_FOOD:
            return "compost"
        return "trash"
    if kind in ("paper", "cardboard"):
        if sub in ("thermal", "laminated", "foil", "glitter", "wax", "tissue", "napkin", "photo"):
            return "compost" if sub in ("tissue", "napkin") and compost == "municipal" else "trash"
        if rec.get("sensitive"):
            return "shred"
        if rec.get("greasy") or float(rec.get("residue", 0.0) or 0.0) > 0.4:
            return "compost" if compost == "municipal" else "trash"
        return "recycling"
    if kind == "shredded_paper":
        return "recycling" if r.get("shredded_paper") in ("bagged", "loose") else ("compost" if compost != "none" else "trash")
    if kind == "plastic":
        if sub in ("film", "bag", "bubble_wrap", "air_pillow", "wrap"):
            return "dropoff" if r.get("film_dropoff") else "trash"
        if sub in ("foam", "styrofoam", "black", "clamshell_black", "blister"):
            return "trash"
        if sub in ("pla", "compostable"):
            return "compost" if compost == "municipal" and r.get("compostable_plastics") else "trash"
        code = rec.get("resin_code")
        return "recycling" if code in (r.get("accepted_resins") or []) else "trash"
    if kind == "glass":
        if sub in ("drinking_glass", "broken", "window", "pyrex", "mirror", "ceramic"):
            return "trash"
        return "recycling" if r.get("glass") else "dropoff"
    if kind == "metal":
        if sub == "aerosol":
            if float(rec.get("contents_left", 0.0) or 0.0) > 0.05:
                return "hazardous"
            return "recycling" if r.get("aerosols") else "trash"
        if sub in ("foil_dirty",):
            return "trash"
        return "recycling"
    if kind == "carton":
        return "recycling" if r.get("cartons") else "trash"
    if kind == "yard":
        if rec.get("diseased") or sub in ("diseased", "invasive_seeding"):
            return "trash"
        return "yard_waste"
    if kind == "pet_waste":
        return "trash"
    if kind == "dry_ice":
        return "special"
    if kind in ("textile",):
        return "trash"
    return "trash"


def container_accepts(w, container, stream, item_rec=None):
    crec = w.ent(container)
    cstream = crec.get("waste_stream")
    if cstream != stream and not (cstream == "compost" and stream == "yard_waste" and rules(w).get("compost") == "backyard"):
        return False
    accepts = crec.get("accepts_materials")
    if accepts and item_rec is not None and stream == "recycling":
        mclass = material_class(item_rec)
        if mclass and mclass not in accepts:
            return False
    return True


def judge(w, item, container, via=None):
    """Record whether an item (and everything inside it) belongs in the container it entered."""
    r = rules(w)
    crec = w.ent(container)
    cstream = crec.get("waste_stream")
    errors = []
    targets = [item] + w.descendants(item)
    if float(crec.get("wetness", 0.0) or 0.0) > 0.3:
        for eid in targets:
            if w.ent(eid).get("waste_kind") in ("paper", "cardboard"):
                pm = w.mut(eid)
                pm["wetness"] = max(float(pm.get("wetness", 0.0) or 0.0), 0.6)
    if cstream in ("compost", "yard_waste"):
        for eid in targets:
            erec = w.ent(eid)
            if (erec.get("liner") or erec.get("waste_kind") == "bag") and not erec.get("compostable"):
                errors.append({"item": eid, "issue": "plastic_bag_in_organics"})
    for eid in targets:
        rec = w.ent(eid)
        if rec.get("category") in ("debris",) and not rec.get("portable"):
            continue
        if rec.get("liner") or S.is_a(rec, "trash_liner") or rec.get("waste_kind") == "bag":
            if cstream == "recycling" and not r.get("bagged_recycling") and eid != container:
                bagged_inside = [c for c in w.children(eid) if w.ent(c).get("relation") == "in"]
                if bagged_inside:
                    errors.append({"item": eid, "issue": "bagged_recycling"})
                    m = w.mut(eid)
                    m["in_stream"], m["stream_ok"] = cstream, False
                    continue
            if eid != item or any(w.ent(c).get("relation") == "in" for c in w.children(eid)):
                m = w.mut(eid)
                m["in_stream"] = cstream
                m["stream_ok"] = True
                continue
        primary, ok, issues = acceptable(w, eid, r)
        good = any(container_accepts(w, container, s, rec) for s in ok)
        m = w.mut(eid)
        m["in_stream"] = cstream
        m["stream_ok"] = bool(good)
        m["discarded_t"] = S.now(w)
        if not good:
            issue = "not_waste" if primary == "keep" else ("wrong_stream" if not issues else ",".join(issues))
            errors.append({"item": eid, "issue": issue, "belongs": primary})
            if primary == "keep":
                S.glog(w, "discarded_keeper", item=eid, container=container)
    if errors:
        cm = w.mut(container)
        cm.setdefault("stream_errors", [])
        cm["stream_errors"].extend(errors)
        cm["contaminated"] = True
        S.gsec(w)["stream_errors"] += len(errors)
        S.glog(w, "stream_error", container=container, errors=errors[:5])
    for eid in targets:
        if w.s["entities"].get(eid, {}).get("in_stream") is not None:
            w.mut(eid)["_cl_judged_in"] = container
            S.watch(w, "stream", eid)       # cleaning.streams drops the verdict when it leaves (stream_left)
    update_fill(w, container)
    for anc in w.ancestors(container):
        if anc in w.s["entities"] and S.is_waste_container(w.ent(anc)):
            update_fill(w, anc)


def stream_left(w, eid):
    """A judged item (in_stream / stream_ok) that is gone or no longer inside a waste container of the stream it was
    judged in: tipped out with its bin, dragged off by a pet, carried away inside something else. Its verdict is
    stale."""
    rec = w.s["entities"].get(eid)
    if rec is None or rec.get("in_stream") is None:
        return True
    container = waste_container_of(w, eid)
    return container is None or w.ent(container).get("waste_stream") != rec.get("in_stream")


def drop_verdict(w, eid):
    """Clear a judged item's stream verdict (in_stream, stream_ok) and the errors its container recorded for it, and
    refill the container it left; the same bookkeeping as retrieving it by hand (retrieve_from_bin)."""
    rec = w.s["entities"].get(eid)
    left = None
    if rec is not None:
        left = rec.get("_cl_judged_in")
        m = w.mut(eid)
        for key in ("in_stream", "stream_ok", "_cl_judged_in"):
            m.pop(key, None)
    for cid in S.waste_containers(w):
        crec = w.s["entities"].get(cid)
        if crec and crec.get("stream_errors") and any(e.get("item") == eid for e in crec["stream_errors"]):
            cm = w.mut(cid)
            cm["stream_errors"] = [e for e in cm["stream_errors"] if e.get("item") != eid]
            cm["contaminated"] = bool(cm["stream_errors"])
    if left in w.s["entities"]:
        update_fill(w, left)


def loose_into_stream(w, container, composition, source=None):
    crec = w.ent(container)
    cstream = crec.get("waste_stream")
    organic = all(k in ("crumbs", "leaves", "food", "coffee", "hair") for k in composition)
    ok = cstream == "trash" or (cstream in ("compost", "yard_waste") and organic)
    if not ok:
        m = w.mut(container)
        m.setdefault("stream_errors", []).append({"item": "loose:" + ",".join(sorted(composition)), "issue": "sweepings"})
        m["contaminated"] = True
        S.gsec(w)["stream_errors"] += 1
    update_fill(w, container)


def contents_volume(w, container):
    total = float(w.ent(container).get("cl_load_g", 0.0) or 0.0) / 400.0
    for child in w.children(container):
        rec = w.ent(child)
        if rec.get("relation") == "part":
            if rec.get("liner") or S.is_a(rec, "trash_liner"):
                total += contents_volume(w, child)
            continue
        if rec.get("relation") != "in":
            continue
        if rec.get("liner") or S.is_a(rec, "trash_liner"):
            total += max(contents_volume(w, child), 0.5)
        else:
            total += float(rec.get("volume_l", 0.5) or 0.5)
    return total


def update_fill(w, container):
    rec = w.ent(container)
    if not S.is_waste_container(rec) and not rec.get("liner"):
        return
    nominal = float(rec.get("nominal_capacity_l") or rec.get("capacity_l") or 10.0)
    fill = contents_volume(w, container) / max(0.1, nominal)
    m = w.mut(container)
    m["fill_level"] = S.rnd(fill, 3)
    m["lid_blocked"] = bool(fill > 1.02 or m.get("liner_bunched"))
    if m["lid_blocked"] and rec.get("openable"):
        m["open"] = True


# ----------------------------------------------------------------------
# hooks on core actions

@after("place", order=120)
def judge_deposit(ctx, result):
    w = ctx.w
    item = ctx.args.get("target")
    if item not in w.s["entities"]:
        return
    container = waste_container_of(w, item)
    if container:
        judge(w, item, container)


INCOMPATIBLE_STORAGE = (("chlorine", "acid"), ("chlorine", "ammonia"), ("oxidizer", "solvent"), ("chlorine", "solvent_light"))


@after("place", order=125)
def storage_conflicts(ctx, result):
    """Bottles whose contents react if they leak must not share a bin/shelf."""
    w = ctx.w
    item = ctx.args.get("target")
    rec = w.s["entities"].get(item)
    if not rec or not (rec.get("product") or rec.get("chem_hazards")):
        return
    holder = rec.get("location")
    if holder not in w.s["entities"]:
        return
    bottles = [c for c in w.children(holder) if w.ent(c).get("product") and (w.ent(c).get("shelf") == rec.get("shelf"))]
    classes = {}
    for bottle in bottles:
        for cls in M.product_classes(w.ent(bottle)["product"]):
            classes.setdefault(cls, []).append(bottle)
    conflict = None
    for a, b in INCOMPATIBLE_STORAGE:
        if a in classes and b in classes:
            conflict = (a, b)
            break
    m = w.mut(holder)
    m["chem_conflict"] = bool(conflict)
    if conflict:
        S.glog(w, "incompatible_storage", holder=holder, classes=list(conflict))


@after("pick_up", order=126)
def storage_conflicts_cleared(ctx, result):
    w = ctx.w
    picked = w.s["entities"].get(ctx.args.get("target")) or {}
    if not picked.get("product"):
        return      # only taking a product bottle away can resolve a storage conflict
    for eid in sorted(e for e, r in w.s["entities"].items() if r.get("chem_conflict")):
        bottles = [c for c in w.children(eid) if w.ent(c).get("product")]
        groups = {}
        for bottle in bottles:
            groups.setdefault(w.ent(bottle).get("shelf"), []).append(bottle)
        still = False
        for group in groups.values():
            classes = set()
            for bottle in group:
                classes |= set(M.product_classes(w.ent(bottle)["product"]))
            if any(a in classes and b in classes for a, b in INCOMPATIBLE_STORAGE):
                still = True
        if not still:
            w.mut(eid)["chem_conflict"] = False


@after("pick_up", order=120)
def retrieve_from_bin(ctx, result):
    w = ctx.w
    item = ctx.args.get("target")
    rec = w.s["entities"].get(item)
    if not rec:
        return
    for eid in [item] + w.descendants(item):
        if w.ent(eid).get("in_stream") is not None:
            m = w.mut(eid)
            m.pop("in_stream", None)
            m.pop("stream_ok", None)
            m.pop("_cl_judged_in", None)
    for eid in S.waste_containers(w):
        crec = w.s["entities"].get(eid)
        if crec and crec.get("stream_errors") and any(e.get("item") == item for e in crec["stream_errors"]):
            m = w.mut(eid)
            m["stream_errors"] = [e for e in m["stream_errors"] if e.get("item") != item]
            m["contaminated"] = bool(m["stream_errors"])
    log = (w.s.get("logs", {}).get("moves") or [])
    last = next((m for m in reversed(log) if m.get("entity") == item), None)
    if last and last.get("from") in w.s["entities"] and S.is_waste_container(w.ent(last["from"])):
        update_fill(w, last["from"])


@before("close", order=120)
def lid_blocked_guard(ctx):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target"))
    if rec and S.is_waste_container(rec) and rec.get("lid_blocked"):
        raise ActionError("The lid won't close over the overfilled bin (or the bunched liner); take the bag out or redistribute", "precondition")


@before("read", order=120)
def private_waste_reading(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    if target not in w.s["entities"]:
        return
    container = waste_container_of(w, target)
    if container and (w.ent(container).get("private_contents") or w.root_room(container) in [r for r, room in w.s["rooms"].items() if room.get("type") in ("bathroom", "bedroom", "master_bedroom", "office")]):
        if not w.ent(target).get("hazards"):
            w.stat("privacy_violations")
            S.glog(w, "read_private_waste", item=target)


@hook("part_installed")
def liner_installed(ctx, device, slot, part):
    w = ctx.w
    drec = w.ent(device)
    prec = w.ent(part)
    if not (prec.get("liner") or S.is_a(prec, "trash_liner")):
        return
    can_l = float(drec.get("nominal_capacity_l") or drec.get("capacity_l") or 20.0)
    liner_l = float(prec.get("liner_l", 50.0))
    ratio = liner_l / max(0.1, can_l)
    m = w.mut(part)
    m["opened"] = bool(prec.get("opened"))
    seated = bool(prec.get("opened")) and 0.85 <= ratio <= 1.6
    m["seated"] = seated
    m["rim_folded"] = seated
    m["slipped"] = ratio < 0.85
    dm = w.mut(device)
    dm["liner_bunched"] = ratio > 1.6 and bool(drec.get("openable"))
    if not prec.get("opened"):
        S.glog(w, "liner_unopened", can=device, liner=part)
    if float(drec.get("wetness", 0.0) or 0.0) > 0.15 or S.total_soil(drec, ("garbage_juice", "food", "mold", "mildew")) > 0.2:
        m["relined_dirty"] = True
        S.glog(w, "relined_dirty_can", can=device)
    update_fill(w, device)


@hook("part_removed")
def liner_removed(ctx, device, slot, part):
    """Pulling the liner bags everything in the can; overloaded or leaky bags tear or drip."""
    w = ctx.w
    drec = w.ent(device)
    prec = w.ent(part)
    if not (prec.get("liner") or S.is_a(prec, "trash_liner")):
        return
    contents = [c for c in w.children(device) if w.ent(c).get("relation") == "in"]
    for item in contents:
        m = w.mut(item)
        m["location"], m["relation"] = part, "in"
        w.log_move(item, device, part)
    # what the bag now holds: the kernel weighs a bag with its contents (physics.subtree_mass), so the liner keeps its
    # own mass (writing the contents into it counted them twice: a 12 kg bag lifted as 24 kg)
    mass = physics.subtree_mass(w.s, part) - float(prec.get("mass_kg", 0.02) or 0.0)
    pm = w.mut(part)
    pm["volume_l"] = S.rnd(max(0.5, contents_volume(w, part)), 3)
    pm["accepts_items"] = True
    pm["container"] = True
    pm["portable"] = True
    leaky = any(w.ent(c).get("leaks") or w.ent(c).get("liquid_l", 0) for c in w.descendants(part))
    if leaky and not prec.get("double_bagged"):
        pm["leaking"] = True
    # CONVENTIONS 5: max_load_kg = what the bag can bear (load_rating_kg is the pre-v2.1 name)
    rating = float(prec.get("max_load_kg", prec.get("load_rating_kg", 10.0)) or 10.0)
    if lawbook.active("handle"):
        pass        # kernel owns it (TIER1_PHYSICS 10.3): the handle law tears the lifted bag in this action's settle
    elif mass > rating or (prec.get("slipped") and mass > rating * 0.5):
        pm["torn"] = True
        pm["_cl_juice_out"] = True
        spill_items = sorted(contents)[: max(1, len(contents) // 3)]
        rid = w.root_room(device)
        for item in spill_items:
            m = w.mut(item)
            m["location"], m["relation"] = rid, "on"
            m["pos"] = geometry.free_floor_cell(w.s, rid, near=tuple(w.robot.get("pos") or (0, 0)), ignore=(item,)) or list(w.robot.get("pos") or [0, 0])
            m.pop("in_stream", None)
            m.pop("stream_ok", None)
            m.pop("_cl_judged_in", None)
        if leaky:
            from . import floors
            floors.make_spill(w, rid, "garbage_juice" if "garbage_juice" in floors.SPILL_PROPS else "dirty_water", 0.15, near=device)
        S.glog(w, "bag_tore", bag=part, mass=mass)
        w.stat("spills")
    dm = w.mut(device)
    dm["liner_bunched"] = False
    if contents and not prec.get("seated"):
        S.add_face_soil(w, device, S.primary_face(drec), "garbage_juice", 0.2)
    update_fill(w, device)


SLIPPED_HANDLE_SHARE = 0.5     # a liner that slipped down into its can is pulled out by a bunched, stretched rim


@hook("phys.props")
def liner_props(record, phys):
    """The handle law's view of a liner (TIER1_PHYSICS 7.3 phys.props): its handle rating is the bag's max_load_kg
    (the kernel's own reading); one that slipped down into the can comes out by its stretched rim and bears half."""
    if record.get("slipped") and phys.get("handle_kg"):
        return {"handle_kg": float(phys["handle_kg"]) * SLIPPED_HANDLE_SHARE}
    return None


liner_props.applies = lambda record: bool(record.get("liner"))


@hook("phys.settle_extra")
def torn_bag_juice(w, sid, ctx):
    """A leaking bag the handle law tore lets its garbage juice out where it gave way (once)."""
    rec = w.s["entities"].get(sid)
    if rec is None or not rec.get("liner") or not rec.get("torn") or not rec.get("leaking") or rec.get("_cl_juice_out"):
        return
    w.mut(sid)["_cl_juice_out"] = True
    if rec.get("location") == "robot":
        rid, cell = w.robot["room"], list(w.robot.get("pos") or [0, 0])
    else:
        rid, cell = w.root_room(sid), None
    if rid in w.s["rooms"]:
        from . import floors
        robot = S.robot_caused(w, sid)      # torn under the robot's lift (its action), or a bag the robot loaded
        floors.make_spill(w, rid, "garbage_juice", 0.15, cell=cell, near=None if cell else sid, robot=robot)
        S.charge(w, "spills", robot)


@hook("event:raccoon_raid")
def raccoon_raid(w, args, env):
    container = args.get("container")
    if container in w.s["entities"]:
        scatter(w, container, "raccoons")
    return True


def scatter(w, container, cause):
    rid = w.root_room(container)
    items = [c for c in w.descendants(container) if w.ent(c).get("portable") and not w.ent(c).get("liner")]
    for item in sorted(items)[: max(1, len(items) // 2)]:
        m = w.mut(item)
        m["location"], m["relation"] = rid, "on"
        m["pos"] = geometry.free_floor_cell(w.s, rid, ignore=(item,)) or [0, 0]
        m.pop("in_stream", None)
        m.pop("stream_ok", None)
        m.pop("_cl_judged_in", None)
    for liner in [c for c in w.descendants(container) if w.ent(c).get("liner")]:
        w.mut(liner)["torn"] = True
    S.glog(w, "trash_scattered", container=container, cause=cause)
    from ...kernel import camera, timeline
    camera.visual_event(w, "trash_scattered", source=container, room=rid, size_m=1.0, container=container, cause=cause)
    if timeline.active(w):
        w.notice("clatter", room=rid, source=container, db=60.0 if cause == "raccoons" else 50.0)
    m = w.mut(container)
    m["scattered"] = True
    update_fill(w, container)


# ----------------------------------------------------------------------
# empty_into

@action("empty_into", params={"target": "entity", "destination": "entity", "height": "?str:low|high"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.01, noise=0.05, category="physical",
        doc="Empty a dustpan, vacuum bin, compost caddy, small wastebasket, mop bucket or other container into a destination "
            "(trash can/cart, compost bin, recycling, a held bag, or for liquids a utility sink, toilet, floor drain, lawn). "
            "Everything inside goes too (check first for valuables). Tip it low inside the liner; from height the dust "
            "billows out. Each item is judged against the destination's waste stream.")
def empty_into(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    require(target != dest, "Choose a different destination", "argument")
    trec = w.s["entities"].get(target)
    require(trec is not None, "That is not visible here", "perception")
    if target not in w.held_items():
        ctx.obj(target, touch=True)
    drec = w.s["entities"].get(dest)
    require(drec is not None, "That is not visible here", "perception")
    if dest not in w.held_items():
        ctx.obj(dest, touch=True)
    require(not (drec.get("openable") and not drec.get("open")) or drec.get("water_source") or drec.get("toilet"),
            "Open the lid of {} first".format(dest if S.realistic(ctx) else drec.get("label", dest)), "precondition")
    require(not trec.get("vacuum_kind"), "Take the bin or bag out of the vacuum first (uninstall)", "precondition")
    from . import floors
    moved = []
    outcome = []
    from ...kernel.phys import liquids
    if S.is_liquid_container(trec) and float(trec.get("liquid_l", 0.0) or 0.0) > 0 and not liquids.container_closed(trec):
        # a closed container (a trigger head, a cap screwed on) keeps its liquid when tipped, like a product bottle
        outcome.append(floors.dump_liquid(ctx, target, dest))
    load = dict(trec.get("cl_load") or {})
    destination = dest
    if load:
        if ctx.args.get("height") == "high":
            fine = {k: v * 0.3 for k, v in load.items() if k in ("dust", "drywall_dust", "ash", "pollen", "hair", "pet_hair", "lint")}
            if fine:
                rid = w.robot["room"]
                floors.settle_room_dust(w, rid, "dust", sum(fine.values()))
                floors.drop_to_floor(w, rid, {k: v * 0.5 for k, v in fine.items()})
                load = {k: v - fine.get(k, 0.0) for k, v in load.items()}
                outcome.append("a dust cloud billowed out")
        if drec.get("water_source") and drec.get("drain") or drec.get("toilet"):
            dm = w.mut(dest)
            dm["clog"] = S.rnd(M.clamp(float(drec.get("clog", 0.0)) + sum(load.values()) / 300.0))
            S.glog(w, "debris_down_drain", drain=dest)
            outcome.append("debris went down the drain")
        else:
            from .surfaces import deposit_waste
            deposit_waste(w, destination, load, source=target)
        m = w.mut(target)
        m["cl_load"] = {}
        m["cl_load_g"] = 0.0
        if trec.get("fill_level") is not None:
            m["fill_level"] = 0.0
        if trec.get("hazardous_waste"):
            if drec.get("waste_stream") not in ("hazardous",):
                dm = w.mut(destination)
                dm.setdefault("stream_errors", []).append({"item": target, "issue": "hazardous_waste"})
                dm["contaminated"] = True
            m["hazardous_waste"] = False
    items = [c for c in w.children(target) if w.ent(c).get("relation") == "in"]
    for item in items:
        m = w.mut(item)
        m["location"], m["relation"] = destination, "in"
        for key in ("pos", "spos", "shelf"):
            m.pop(key, None)
        w.log_move(item, target, destination)
        moved.append(item)
        if "sharp" in w.ent(item).get("hazards", []) and not w.ent(item).get("wrapped"):
            w.mut(destination)["sharp_hazard"] = True
            S.glog(w, "unwrapped_sharps_in_bag", item=item)
    if S.is_waste_container(drec) or waste_container_of(w, dest):
        container = dest if S.is_waste_container(drec) else waste_container_of(w, dest)
        for item in moved:
            judge(w, item, container)
        update_fill(w, container)
    elif moved and (drec.get("water_source") or drec.get("toilet")):
        for item in moved:
            S.glog(w, "item_down_drain", item=item)
    if S.is_waste_container(trec):
        update_fill(w, target)
    ctx.data["seconds"] = 6.0 + 1.0 * len(moved)
    if S.realistic(ctx):
        # tipping a container out takes the same time whatever was in it (a lidded caddy's contents are not seen)
        ctx.data["seconds"] = 8.0
    return "Emptied {} into {}{}".format(target, dest, ("; " + "; ".join(outcome)) if outcome else "")


def scrape_into(ctx, plate, dest, grams):
    """Scraped food (and anything else resting on the plate) into a bin, caddy or drain."""
    w = ctx.w
    prec = w.ent(plate)
    drec = w.ent(dest)
    riders = [c for c in w.children(plate) if w.ent(c).get("relation") in ("on", "in") and w.ent(c).get("portable")]
    kinds = prec.get("scrap_types") or ["mixed"]
    if S.is_waste_container(drec):
        container = dest
        r = rules(w)
        stream = drec["waste_stream"]
        bad = False
        if stream == "compost":
            for kind in kinds:
                if r.get("compost") == "backyard" and kind not in PLANT_FOOD:
                    bad = True
                if r.get("compost") == "none":
                    bad = True
        elif stream == "recycling":
            bad = True
        if bad:
            m = w.mut(container)
            m.setdefault("stream_errors", []).append({"item": "scraps:" + ",".join(kinds), "issue": "wrong_stream"})
            m["contaminated"] = True
            S.gsec(w)["stream_errors"] += 1
        from .surfaces import deposit_waste
        deposit_waste(w, container, {"food": sum(grams.values())}, source=plate)
        m = w.mut(container)
        if any(k in ANIMAL_FOOD for k in kinds):
            m["has_meat_waste"] = True
        m["has_food_waste"] = True
    elif drec.get("drain"):
        dm = w.mut(dest)
        if not drec.get("disposal") or "oily" in kinds or "fibrous" in kinds:
            dm["clog"] = S.rnd(M.clamp(float(drec.get("clog", 0.0)) + sum(grams.values()) / 250.0))
            S.glog(w, "scraps_down_drain", drain=dest)
    else:
        from .surfaces import deposit_waste
        deposit_waste(w, dest, {"food": sum(grams.values())}, source=plate)
    for item in riders:
        m = w.mut(item)
        m["location"], m["relation"] = dest, "in"
        m.pop("spos", None)
        w.log_move(item, plate, dest)
        S.glog(w, "scraped_item_into_bin", item=item, dest=dest)
        if S.is_waste_container(drec):
            judge(w, item, dest)

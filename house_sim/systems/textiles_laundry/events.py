"""Scenario events for long horizons that cannot be simulated tick by tick.

{"op": "event", "at_s": 3600, "effect": "textiles_laundry.storage_months", "args": {"months": 6, "container": "bin_1"}}
    Off-season storage passes: stains left on stored items oxidise and set (sunscreen/sweat yellow), wool and
    cashmere without a moth repellent in the same container get moth holes (certain if soiled, likely after
    6 months), damp items mildew, stored items in a humid room (basement) turn musty, and everything stored
    is marked stored_months. Without "container", every closed bin/drawer/closet/garment bag is affected.

{"op": "event", "at_s": 600, "effect": "textiles_laundry.transport", "args": {"container": "suitcase_1"}}
    A packed bag is carried/flown: wrinkle-prone items not protected (garment bag, packing folder, rolled knits)
    crease, structured items folded small crease badly, unbagged muddy shoes soil the clothes, crushable items
    under heavy ones are crushed, damp items wet their neighbours.
"""
from ...kernel.plugins import hook
from . import model as M


def _stored_containers(w, container=None):
    ents = w.s["entities"]
    if container:
        return [container] if container in ents else []
    result = []
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("accepts_items") and (rec.get("container") or rec.get("openable")) and (
                (rec.get("openable") and not rec.get("open")) or rec.get("sealed") or rec.get("breathable")):
            if not M.is_textile(rec) and not rec.get("appliance_kind"):
                result.append(eid)
    return result


@hook("event:textiles_laundry.storage_months")
def storage_months(w, args, env):
    months = float(args.get("months", 3))
    for box in _stored_containers(w, args.get("container")):
        items = [e for e in w.descendants(box) if M.is_textile(w.ent(e))]
        if not items:
            continue
        repellent = any(w.ent(e).get("moth_repellent") for e in w.descendants(box))
        room = w.s["rooms"].get(M.room_of(w, box) or "", {})
        humid = float(room.get("humidity", 0.45)) > 0.6 or room.get("type") in ("basement",)
        sealed = bool(w.ent(box).get("sealed") or w.ent(box).get("airtight"))
        for item in items:
            rec = w.ent(item)
            m = w.mut(item)
            m["stored_months"] = round(float(rec.get("stored_months", 0.0)) + months, 2)
            left = [k for k in M.stains(rec) if k not in (rec.get("set_stains") or [])]
            if left:
                M.set_stain(w, item, left, "stain oxidised in storage")
            if rec.get("worn") or M.soil_level(rec, "body") > 0.05:
                M.add_soil(w, item, "stain_sweat", 0.2)
                M.set_stain(w, item, ["stain_sweat"], "body soil yellowed in storage")
            woolly = M.fabric_name(rec) in ("wool", "cashmere") or rec.get("fur")
            if woolly and not repellent and (M.is_dirty(rec) or months >= 6):
                M.add_damage(w, item, "moth_holes", min(1.0, 0.1 * months), why="stored wool without moth protection")
            damp = max(float(rec.get("wetness", 0.0)), float(rec.get("_core_wetness", 0.0) or 0.0))
            if damp >= M.DAMP:
                M.add_soil(w, item, "mildew", min(1.0, 0.15 * months))
                m = w.mut(item)
                m["musty"] = True
                M.set_odor(w, item, "musty", 0.6)
                M.add_damage(w, item, "mildew", 0.0, why="stored damp")
                if sealed and M.fabric_name(rec) in ("leather", "suede"):
                    M.add_damage(w, item, "leather_mould", 0.5, why="damp leather sealed in plastic")
            elif humid and not repellent:
                m = w.mut(item)
                m["musty"] = True
                M.set_odor(w, item, "musty", 0.6)
        w.emit("storage_months_passed", container=box, months=months)
    return True


@hook("event:textiles_laundry.transport")
def transport(w, args, env):
    box = args.get("container")
    if not box or box not in w.s["entities"]:
        return True
    contents = w.descendants(box)
    textiles = [e for e in contents if M.is_textile(w.ent(e)) and w.ent(e).get("pair_kind") != "shoe"]
    shoes = [e for e in contents if w.ent(e).get("pair_kind") == "shoe"]
    heavy = any(float(w.ent(e).get("mass_kg", 0.0)) >= 1.0 for e in contents if w.ent(e).get("location") == box)
    for item in textiles:
        rec = w.ent(item)
        protected = any(w.ent(a).get("breathable") or w.ent(a).get("packing_folder") for a in M.chain(w, item) if a != box and a in w.s["entities"])
        prone = M.wrinkle_prone(rec)
        if protected:
            gain = 0.05
        elif rec.get("rolled"):
            gain = 0.6 if rec.get("structured") else 0.05
        elif rec.get("folded"):
            gain = 0.3 * prone + (0.3 if rec.get("structured") else 0.0) + (0.2 if heavy else 0.0)
        else:
            gain = 0.6 * prone + 0.1
        w.mut(item)["wrinkled"] = round(min(0.95, float(rec.get("wrinkled", 0.0)) + gain), 3)
    for shoe in shoes:
        srec = w.ent(shoe)
        bagged = any(w.ent(a).get("shoe_bag") or (w.ent(a).get("category") == "bag" and a != box) for a in M.chain(w, shoe) if a in w.s["entities"])
        dirt = M.soil_level(srec, "mud") + M.soil_level(srec, "dirt")
        if dirt >= 0.15 and not bagged:
            for item in textiles:
                M.add_soil(w, item, "mud", 0.1)
    if heavy:
        for e in contents:
            if w.ent(e).get("crushable") and not w.ent(e).get("crushed"):
                w.mut(e)["crushed"] = True
                M.add_damage(w, e, "crushed", 0.5, why="crushed under heavy items in transit")
    wets = [float(w.ent(e).get("wetness", 0.0)) for e in textiles]
    if wets and max(wets) >= 0.4:
        avg = sum(wets) / len(wets)
        for e, value in zip(textiles, wets):
            if value < avg:
                w.mut(e)["wetness"] = round(value + (avg - value) * 0.5, 4)
    w.emit("transported", container=box)
    return True

"""Weighing, scooping/measuring, transferring, portioning, plating, garnishing and thawing.

Measuring model: granular density depends on technique (flour spooned-and-levelled 125 g/cup,
dipped-and-swept ~150 g/cup, packed ~160 g/cup, sifted ~110 g/cup; brown sugar is packed by
design). Measuring cups/spoons carry a calibrated measure_ml; cutlery spoons only an
approximate bowl_ml (a soup spoon holds ~10 ml, not 14.8). Liquids read from above or a
tilted cup read ~6-7 % high. The kitchen scale shows net mass = load - tare in its unit setting;
switching it on zeroes it with whatever is already on the platform; overload shows O-Ld.
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ..core import release_from_hands
from . import common as C
from . import data as D
from . import mixing as X
from . import model as M
from . import percept as P
from . import systems as S


def _source_food(ctx, sid, item=None):
    """The food entity to draw from: the target itself, or the (named) food inside a vessel."""
    w = ctx.w
    rec = ctx.obj(sid, touch=True)
    if M.food(rec):
        C.require_open(rec, ctx=ctx, eid=sid)
        parent = w.s["entities"].get(rec.get("location"), {})
        if parent.get("openable") and not parent.get("open") and parent.get("container"):
            raise ActionError("Open the {} first".format(P.name(ctx, rec.get("location"), "container")), "precondition")
        return sid, rec, rec.get("location")
    require(not (rec.get("openable") and not rec.get("open")), "Open it first", "precondition")
    foods = M.vessel_foods(w.s, sid)
    if item:
        require(item in foods, "That food is not in it", "argument")
        foods = [item]
    require(foods, "It is empty", "precondition")
    foods.sort(key=lambda e: (-M.qty(w.ent(e)), e))
    return foods[0], w.ent(foods[0]), sid


def _grams_by_eye(ctx, fid):
    """Realistic perception: a gram amount judged by eye needs to know what the food is (its density). For something
    the robot has not identified it measures by volume or on a scale instead -- the same refusal for every member of a
    look-alike group, so the poured volume cannot be used to tell flour from sugar."""
    ctx.refuse_unidentified(fid, "Unidentified contents: measure by volume (amount_ml) or on a scale, not grams by eye")


def _scale_under(state, vessel):
    rec = state["entities"].get(vessel)
    loc = rec.get("location") if rec else None
    while loc in state["entities"]:
        if state["entities"][loc].get("tool_kind") == "kitchen_scale":
            return loc
        loc = state["entities"][loc].get("location")
    return None


@action("weigh", params={"target": "entity", "tare": {"type": "bool", "default": False}, "unit": "?str:g|oz|lb|ml"},
        duration=C.duration(4.0), noise=0.0, category="perception",
        doc="Read a kitchen scale (target = the scale or something on it). tare=true zeroes it with the current load "
            "(container excluded from later readings). Switching a scale on zeroes it with what is already on it. "
            "unit changes the display unit (button). Readings are net of tare, in the display unit.")
@P.perceived_result
def weigh(ctx):
    w = ctx.w
    tid = ctx.args["target"]
    rec = ctx.obj(tid, touch=bool(ctx.args.get("tare") or ctx.args.get("unit")))
    scale = tid if rec.get("tool_kind") == "kitchen_scale" else _scale_under(w.s, tid)
    require(scale, "Put it on a kitchen scale first", "precondition")
    srec = ctx.obj(scale, touch=bool(ctx.args.get("tare") or ctx.args.get("unit") or not w.ent(scale).get("on")))
    require(not srec.get("broken"), "The scale is broken", "resource")
    require(float(srec.get("battery_level", 1.0)) > 0.02, "The scale's battery is flat", "resource")
    m = None
    if not srec.get("on"):
        m = w.mut(scale)
        m["on"] = True
        m["tare_g"] = round(S.scale_load_g(w.s, scale), 2)
    if ctx.args.get("unit"):
        m = m or w.mut(scale)
        m.setdefault("settings", {})["unit"] = ctx.args["unit"]
    if ctx.args["tare"]:
        load = S.scale_load_g(w.s, scale)
        require(load <= float(srec.get("capacity_g", 5000.0)), "Overloaded; remove weight", "precondition")
        m = m or w.mut(scale)
        m["tare_g"] = round(load, 2)
    load = S.scale_load_g(w.s, scale)
    if load > 2.0 * float(srec.get("capacity_g", 5000.0)):
        mm = w.mut(scale)
        mm["broken"] = True
        mm["condition"] = "load cell overloaded"
        w.stat("damage")
    display = S.scale_display(w.s, scale)
    w.mut(scale)["display"] = display
    facts = w.sec("facts")
    facts.setdefault("readings", {})[scale] = display
    facts["last_reading"] = {"scale": scale, "display": display, "time_s": w.s["time_s"]}
    facts.setdefault("inspected", {})[scale] = w.s["time_s"]
    if tid != scale:
        facts.setdefault("inspected", {})[tid] = w.s["time_s"]
    C.seconds(ctx, 2.0 if ctx.args["tare"] else 3.0)
    if ctx.realistic:
        _grant_reading(ctx, scale)
    return {"scale": scale, "display": display}


def _grant_reading(ctx, scale):
    """What a scale reading teaches (realistic perception): the display text, and the mass of the one thing whose
    weight it shows (the single item on an un-tared scale, or the single food in a vessel that was tared)."""
    w = ctx.w
    ctx.grant(scale, "read", ["display"])
    if S.scale_reading(w.s, scale) in (None, "overload"):
        return
    load = [c for c in w.children(scale) if w.ent(c).get("relation") == "on"]
    if len(load) != 1:
        return
    tare = float(w.ent(scale).get("tare_g", 0.0) or 0.0)
    if abs(tare) < 0.5:
        ctx.grant(load[0], "measure", ["mass_kg"])
        return
    inside = w.children(load[0])
    if len(inside) == 1 and abs(float(w.ent(load[0]).get("mass_kg", 0.0)) * 1000.0 - tare) < 1.0:
        ctx.grant(inside[0], "measure", ["mass_kg"])


def _measure_ml(trec):
    return float(trec.get("measure_ml") or trec.get("bowl_ml") or 0.0)


@action("scoop", params={"target": "entity", "tool": "?entity", "destination": "?entity",
                         "technique": "?str:spoon_and_level|dip_and_sweep|packed|heaped|rounded|level",
                         "count": {"type": "int", "default": 1, "min": 1, "max": 40}, "item": "?entity"},
        duration=C.duration(6.0), irreversible=True, noise=0.02,
        doc="Fill a held measuring cup/spoon or scoop from an open source (bag, canister, tub, bowl) count times, levelled "
            "or heaped as the technique says, emptying each into the destination (or keeping it in the tool). Flour: "
            "spoon_and_level ~125 g/cup; dip_and_sweep packs ~150 g/cup. Brown sugar is measured packed. A wet scoop "
            "clumps flour; the scoop carries whatever it touched into the next container.")
@P.perceived_result
def scoop(ctx):
    w = ctx.w
    fid, frec, src_container = _source_food(ctx, ctx.args["target"], ctx.args.get("item"))
    tool_id = C.tool(ctx, "scoop", ctx.args.get("tool"))
    trec = w.ent(tool_id)
    ml = _measure_ml(trec)
    require(ml > 0, "That tool has no measuring volume", "argument")
    technique = ctx.args.get("technique") or "level"
    f = frec["food"]
    k = f.get("kind")
    if technique == "spoon_and_level":
        require(ctx.free_hand() is None and any(h != tool_id and (w.ent(h).get("tool_kind") in ("spoon", "measuring_spoon", "scoop", "table_knife", "butter_knife")
                                                         or "scoop" in w.ent(h).get("capabilities", [])) for h in w.held_items()),
                "Spoon-and-level needs a spoon (or straight edge) in the other hand", "capability")
    dens_table = D.DENSITY.get(k)
    if f.get("phase") in ("powder", "granular"):
        if dens_table:
            if f.get("sifted"):
                density = dens_table.get("sifted", dens_table.get("default"))
            elif technique in ("spoon_and_level", "level"):
                density = dens_table.get("spoon_and_level", dens_table.get("default"))
            else:
                density = dens_table.get(technique if technique in dens_table else "dip_and_sweep", dens_table.get("default"))
        else:
            density = M.density(frec) * (1.12 if technique in ("packed", "dip_and_sweep") else 1.0)
    else:
        density = M.density(frec)
    fill = {"heaped": 1.3, "rounded": 1.12}.get(technique, 1.0)
    if trec.get("measure_kind") == "liquid" and f.get("phase") in ("powder", "granular"):
        fill *= 1.06    # cannot level a jug: granular sits proud of the line
    out = {"target": fid, "tool": tool_id}
    if float(trec.get("wetness", 0.0)) > 0.2 and f.get("phase") in ("powder", "granular"):
        fill *= 0.9
        m = w.mut(fid)
        m["food"]["lumps"] = int(m["food"].get("lumps", 0)) + 3
        out["warning"] = "wet scoop: the powder clumped"
    if f.get("cat") == "frozen_dessert" and float(frec.get("temperature_c", -18.0)) < -14.0 and not (float(trec.get("temperature_c", 21.0)) > 35.0):
        fill *= 0.4
        out["warning"] = "ice cream too hard: scoop skidded (temper it or warm the scoop)"
    per = ml * density * fill
    count = int(ctx.args["count"])
    dest = ctx.args.get("destination")
    if dest:
        C.vessel_reach(ctx, dest)
    total = 0.0
    news = []
    for _ in range(count):
        portion, grams = M.take(w, fid, per)
        if grams <= 0:
            break
        total += grams
        M.contact(w, tool_id, fid, 0.5)
        target = dest or tool_id
        new = X.add(w, portion, target, "in")
        M.contact(w, tool_id, new, 0.5)
        news.append(new)
    require(total > 0, "Nothing left to scoop", "precondition")
    out.update({"grams": round(total, 1), "per_scoop_g": round(per, 1), "into": dest or tool_id, "portion": news[-1] if news else None})
    C.seconds(ctx, (8.0 if technique == "spoon_and_level" else 4.0) * count)
    return out


@action("transfer", params={"target": "entity", "destination": "entity", "item": "?entity",
                            "amount_g": {"type": "number", "default": 0, "min": 0, "max": 50000},
                            "amount_ml": {"type": "number", "default": 0, "min": 0, "max": 50000},
                            "fraction": {"type": "number", "default": 0, "min": 0, "max": 1},
                            "units": {"type": "int", "default": 0, "min": 0, "max": 500},
                            "until_reading": {"type": "number", "default": 0, "min": 0, "max": 100000},
                            "tool": "?entity", "method": "?str:pour|scrape|ladle|spoon|tongs|funnel|slotted|skim",
                            "rate": "?str:slow|normal|fast", "read": "?str:eye_level|above|tilted",
                            "careful": {"type": "bool", "default": True}},
        duration=C.duration(15.0), irreversible=True, noise=0.05,
        doc="Move food from a source (a food item, package or a vessel's contents; item= picks one food in it) into a "
            "destination vessel, plate, bag, bin or sink: amount_g / amount_ml / fraction / units / all, or pour onto a scale "
            "until_reading shows a value (display units). Scrape with a spatula to get it all; ladle or funnel liquids; "
            "pouring fast or from a heavy pot spills; boiling liquid cracks cold glass; non-food containers taint food.")
@P.perceived_result
def transfer(ctx):
    w = ctx.w
    src_arg = ctx.args["target"]
    fid, frec, src_vessel = _source_food(ctx, src_arg, ctx.args.get("item"))
    dest = ctx.args["destination"]
    require(dest != fid and dest != src_arg, "Choose a different destination", "argument")
    drec = ctx.obj(dest, touch=True)
    require(drec.get("container") or drec.get("surface") or drec.get("accepts_items") or drec.get("drain") or drec.get("waste_stream"),
            "That cannot hold food", "argument")
    if drec.get("openable") and not drec.get("open") and drec.get("container"):
        raise ActionError("Open the {} first".format(P.name(ctx, dest, "container")), "precondition")
    f = frec["food"]
    method = ctx.args.get("method") or ("ladle" if f.get("phase") == "liquid" and not ctx.holding(src_arg) and src_arg != fid else "pour")
    if method == "skim":
        return _skim(ctx, fid, frec, dest, drec)
    if frec.get("package") and frec.get("liner_closed") is not None:
        w.mut(fid)["liner_closed"] = False
    tool_id = ctx.args.get("tool")
    if tool_id:
        require(ctx.holding(tool_id), "Hold the {} first".format(P.name(ctx, tool_id, "tool")), "capability")
    if method in ("scrape", "ladle", "spoon", "tongs", "slotted"):
        cap = {"scrape": "scrape", "ladle": "ladle", "spoon": "scoop", "tongs": "grip_food", "slotted": "scoop"}[method]
        tool_id = C.tool(ctx, cap, tool_id)
    if method == "pour" and src_arg != fid:
        container = w.ent(src_arg)
        if not ctx.holding(src_arg):
            require(float(container.get("mass_kg", 1.0)) + M.vessel_food_g(w.s, src_arg) / 1000.0 <= 8.0, "Pick up the container to pour from it", "precondition")
    available = M.qty(frec)
    units = int(ctx.args.get("units") or 0)
    grams = None
    out = {"source": fid, "destination": dest, "method": method}
    if ctx.args.get("until_reading"):
        scale = _scale_under(w.s, dest)
        require(scale, "The destination is not on a kitchen scale", "precondition")
        reading = S.scale_reading(w.s, scale)
        require(reading is not None, "Switch the scale on first", "precondition")
        require(reading != "overload", "The scale is overloaded", "precondition")
        unit = (w.ent(scale).get("settings") or {}).get("unit", "g")
        target = float(ctx.args["until_reading"])
        need = (target - float(reading)) * S.UNIT_G.get(unit, 1.0)
        rate = ctx.args.get("rate") or "normal"
        over = {"slow": 0.004, "normal": 0.025, "fast": 0.07}[rate]
        grams = max(0.0, need * (1.0 + over))
        out["note"] = "stopped when the display read about {} {}".format(int(round(target)), unit)
        if ctx.realistic:
            ctx.grant(scale, "read", ["display"])
            out["_measured"] = ["moved_g"]           # poured against the scale's display
    elif units:
        require(f.get("unit_g"), "It is not counted in units", "argument")
    elif ctx.args.get("amount_g"):
        _grams_by_eye(ctx, fid)
        grams = float(ctx.args["amount_g"])
    elif ctx.args.get("amount_ml"):
        err = D.READ_ERROR.get(ctx.args.get("read") or "eye_level", 0.0)
        if drec.get("measure_kind") == "dry" and f.get("phase") == "liquid":
            err += 0.03
        grams = float(ctx.args["amount_ml"]) * (1.0 + err) * M.density(frec)
    elif ctx.args.get("fraction"):
        grams = float(ctx.args["fraction"]) * available
    if units:
        grams = None
    residue = 0.0
    if grams is None and not units:
        sticky = f.get("phase") in ("paste", "dough") or f.get("sticky")
        if src_arg != fid or frec.get("package"):
            residue = 0.0 if method == "scrape" else (0.06 if sticky else 0.02 if f.get("phase") == "liquid" else 0.0)
        grams = available * (1.0 - residue)
    if units:
        portion, moved = M.take(w, fid, units=units)
    else:
        portion, moved = M.take(w, fid, min(grams, available))
    require(moved > 0, "Nothing to move", "precondition")
    pf = portion["food"]
    spilled = 0.0
    rate = ctx.args.get("rate") or "normal"
    total_src = float(w.ent(src_arg).get("mass_kg", 0.0)) + (M.vessel_food_g(w.s, src_arg) / 1000.0 if src_arg != fid else 0.0)
    if pf.get("phase") == "liquid":
        narrow = float((drec.get("inner_size_m") or drec.get("size_m") or [0.2])[0]) < 0.08
        if narrow and method != "funnel" and method != "ladle":
            spilled += 0.05 * moved
        if method == "pour" and total_src > 4.0 and not ctx.holding(src_arg):
            spilled += 0.06 * moved
        if rate == "fast":
            spilled += 0.04 * moved
    elif pf.get("phase") in ("powder", "granular"):
        if rate == "fast":
            spilled += 0.04 * moved
            if pf.get("phase") == "powder":
                from ..core import add_soil
                surface = drec.get("location") if drec.get("location") in w.s["entities"] else None
                if surface:
                    add_soil(w, surface, "dust", 0.08)
                out["warning"] = "a cloud of fine powder rose"
    if spilled:
        pf["qty_g"] = round(moved - spilled, 3)
        M.spill(w, dest, pf.get("kind", "food"), spilled, pf.get("phase", "solid"))
    if not drec.get("food_safe", True) and not drec.get("waste_stream") and not drec.get("drain"):
        portion.setdefault("contamination", {})["chemical"] = 0.5
        M.ensure_secret(portion, "contamination")
        out["warning"] = "not a food-safe container"
    if drec.get("material") == "glass" and float(drec.get("temperature_c", 21.0)) < 10.0 and float(frec.get("temperature_c", 21.0)) > 80.0 and pf.get("phase") == "liquid":
        md = w.mut(dest)
        md["broken"] = True
        md["condition"] = "cracked (thermal shock)"
        w.stat("breakages")
        w.stat("damage")
        M.spill(w, dest, pf.get("kind", "food"), float(pf.get("qty_g", moved)), "liquid")
        C.seconds(ctx, 6.0)
        out["warning"] = "the cold glass cracked"
        return out
    if drec.get("drain"):
        if pf.get("kind") in ("oil", "olive_oil", "vegetable_oil", "bacon_grease", "fat", "frying_oil") or float((pf.get("composition") or {}).get("fat", 0.0)) > 0.3 * moved:
            md = w.mut(dest)
            md["_fat_g"] = round(float(md.get("_fat_g", 0.0)) + float(pf.get("qty_g", moved)), 1)
            w.emit("fat_down_drain", target=dest, grams=round(moved, 1))
        out["note"] = "went down the drain"
        C.seconds(ctx, 6.0 + C.pour_ml(frec, moved) / 200.0)
        out["moved_g"] = round(moved, 1)
        if M.qty(w.ent(fid)) <= 0 and not w.ent(fid).get("package"):
            M.destroy_food(w, fid)
        return out
    if M.food(drec) and drec.get("package"):
        # consolidating duplicates: pour into another opened package of the same product. Realistic perception: the
        # robot can only judge "same product" for packages it has identified, so it asks for that first -- the same
        # refusal whatever the packages really hold (no product oracle)
        for eid in (fid, dest):
            ctx.refuse_unidentified(eid, "Identify both products before combining them")
        require(M.kind(drec) == pf.get("kind"), "That package holds a different product", "argument")
        require(drec.get("open") or drec.get("opened"), "Open the {} first".format(P.name(ctx, dest, "package")), "precondition")
        full = float(drec.get("full_g") or (drec.get("food") or {}).get("full_g") or 0.0)
        room = max(0.0, full * 1.02 - M.qty(drec)) if full else float(pf.get("qty_g", moved))
        overflow = max(0.0, float(pf.get("qty_g", moved)) - room)
        if overflow > 0.5:
            pf["qty_g"] = round(float(pf["qty_g"]) - overflow, 3)
            M.spill(w, dest, pf.get("kind", "food"), overflow, pf.get("phase", "paste"))
            out["overflow_g"] = round(overflow, 1)
        M.merge_into(w, dest, portion)
        if M.qty(w.ent(fid)) <= 0.01 and fid != src_arg:
            M.destroy_food(w, fid)
        out["merged_into"] = dest
        out["moved_g"] = round(float(pf.get("qty_g", moved)), 1)
        if w.ent(dest).get("_date_label_late") and not ctx.realistic:
            # (realistic perception: the robot knows this only by reading both dates itself)
            out["note"] = "the printed date on this package is later than the oldest contents now inside"
        C.seconds(ctx, 10.0 + C.pour_ml(frec, moved) / 40.0)
        return out
    relation = "in" if drec.get("container") or drec.get("waste_stream") else "on"
    if drec.get("waste_stream"):
        portion["waste_stream"] = drec["waste_stream"]
        new = M.create_from_record(w, portion, dest, "in")
    else:
        new = X.add(w, portion, dest, relation)
    if tool_id:
        M.contact(w, tool_id, fid, 0.5)
        M.contact(w, tool_id, new, 0.5)
    M.contact(w, dest, new, 0.4)
    if pf.get("phase") not in ("liquid", "powder", "granular") and not tool_id:
        C.touch_food(ctx, new, 0.4)
    if not ctx.args["careful"] and pf.get("phase") in ("liquid", "paste") and drec.get("lid"):
        from ..core import add_soil
        add_soil(w, dest, "food", 0.15)
        w.mut(dest)["rim_smears"] = True
        out["warning"] = "sauce smeared on the rim; the lid will not seal"
    cap = M.capacity_ml(drec)
    if cap > 0 and relation == "in":
        held = M.vessel_food_ml(w.s, dest)
        if held > cap * 1.0 + 1e-6:
            over = held - cap
            nrec = w.ent(new)
            loss = min(M.qty(nrec), over * M.density(nrec))
            M.set_qty(w, new, M.qty(nrec) - loss)
            M.spill(w, dest, M.kind(nrec), loss, M.food(nrec).get("phase", "solid"))
            out["overflow_g"] = round(loss, 1)
        M.update_fill(w, dest)
    if src_arg != fid and M.qty(w.ent(fid)) <= 0.01:
        M.destroy_food(w, fid)
    if src_vessel in w.s["entities"]:
        M.update_fill(w, src_vessel)
    out.update({"moved_g": round(moved - spilled, 1), "new": new, "spilled_g": round(spilled, 1), "residue_left_g": round(M.qty(w.ent(fid)) if fid in w.s["entities"] else 0.0, 1)})
    # pouring time follows the volume poured (what the robot sees), not its hidden mass
    C.seconds(ctx, 5.0 + C.pour_ml(frec, moved) / (60.0 if pf.get("phase") in ("liquid",) else 40.0) * (1.6 if method == "scrape" else 1.0))
    return out


@action("portion", params={"target": "entity", "into": "list", "amount_g": {"type": "number", "default": 0, "min": 0, "max": 20000},
                           "method": "?str:scale|eyeball", "item": "?entity", "press_air": {"type": "bool", "default": False}},
        duration=C.duration(60.0), irreversible=True, noise=0.03,
        doc="Divide food into the listed containers/bags/plates: equal shares (or amount_g each). method=scale weighs each "
            "portion on a switched-on kitchen scale within reach (+/-2 %); eyeballing gives the first containers too much "
            "(up to +/-30 %). press_air pushes the air out of bags before sealing (prevents freezer burn).")
@P.perceived_result
def portion(ctx):
    w = ctx.w
    fid, frec, src_vessel = _source_food(ctx, ctx.args["target"], ctx.args.get("item"))
    into = ctx.args["into"]
    require(1 <= len(into) <= 24 and all(isinstance(x, str) for x in into), "List 1-24 destination containers", "argument")
    require(len(set(into)) == len(into), "List each container once", "argument")
    for dest in into:
        C.vessel_reach(ctx, dest)
    method = ctx.args.get("method") or "eyeball"
    scale = None
    if method == "scale":
        for eid in sorted(w.s["entities"]):
            rec = w.s["entities"][eid]
            if rec.get("tool_kind") == "kitchen_scale" and rec.get("on") and ctx.visible(eid) and geometry.reach_problem(w.s, eid) is None:
                scale = eid
                break
        require(scale, "A switched-on kitchen scale within reach is needed", "precondition")
    total = M.qty(frec)
    n = len(into)
    if ctx.args.get("amount_g") and method != "scale":
        _grams_by_eye(ctx, fid)
    each = float(ctx.args.get("amount_g") or 0) or total / n
    shares = []
    for i in range(n):
        if method == "scale":
            dev = 0.015 * (1 if i % 2 == 0 else -1)
        else:
            dev = 0.3 * (1.0 - 2.0 * i / max(1, n - 1)) if n > 1 else 0.0
        shares.append(each * (1.0 + dev))
    if not ctx.args.get("amount_g"):
        if method == "scale":
            factor = total / sum(shares)
            shares = [s * factor for s in shares]
        else:
            # eyeballing: generous early, whatever is left for the last
            running, fixed = 0.0, []
            for s in shares[:-1]:
                s = min(s, total - running)
                fixed.append(s)
                running += s
            fixed.append(max(0.0, total - running))
            shares = fixed
    placed = []
    for dest, share in zip(into, shares):
        if share <= 0.01 or M.qty(w.ent(fid)) <= 0.01:
            placed.append({"container": dest, "g": 0.0})
            continue
        part, moved = M.take(w, fid, share)
        drec = w.ent(dest)
        new = X.add(w, part, dest, "in" if drec.get("container") else "on")
        M.contact(w, dest, new, 0.4)
        if scale:
            M.contact(w, scale, new, 0.3 if not drec.get("container") else 0.0)
        if ctx.args["press_air"] and drec.get("category") in ("bag", "food_bag"):
            w.mut(dest)["air_removed"] = True
        M.update_fill(w, dest)
        placed.append({"container": dest, "g": round(moved, 1)})
    C.touch_food(ctx, fid, 0.2)
    if M.qty(w.ent(fid)) <= 0.01 and not w.ent(fid).get("package"):
        M.destroy_food(w, fid)
    elif src_vessel in w.s["entities"]:
        M.update_fill(w, src_vessel)
    C.seconds(ctx, n * (25.0 if method == "scale" else 12.0))
    if ctx.realistic and method == "scale":
        # each share was weighed: the scale's reading, and the portion's mass is now known
        for item in placed:
            for new in M.vessel_foods(w.s, item["container"]):
                ctx.grant(new, "measure", ["mass_kg"])
        return {"portions": placed, "_measured": ["portions"], "_subject": _alive(w, fid, placed)}
    return {"portions": placed, "_subject": _alive(w, fid, placed)}


def _alive(w, fid, placed):
    """The food a result's amounts are about (realistic perception): the source if it still exists, else a portion."""
    if fid in w.s["entities"]:
        return fid
    for item in placed:
        for new in M.vessel_foods(w.s, item["container"]):
            return new
    return None


@action("plate", params={"target": "entity", "destination": "entity", "item": "?entity",
                         "amount_g": {"type": "number", "default": 0, "min": 0, "max": 3000},
                         "pieces": {"type": "int", "default": 0, "min": 0, "max": 200}, "tool": "?entity",
                         "on_top_of": "?entity", "careful": {"type": "bool", "default": True}},
        duration=C.duration(12.0), irreversible=True, noise=0.02,
        doc="Serve a portion from a pot/serving dish/board onto a plate or bowl with a serving utensil (tongs, spoon, ladle "
            "or spatula). It lands in its own spot on the plate (or on_top_of another food, e.g. sauce over pasta). "
            "careful=false drips on the rim. The serving utensil carries allergens between dishes.")
@P.perceived_result
def plate(ctx):
    w = ctx.w
    fid, frec, src_vessel = _source_food(ctx, ctx.args["target"], ctx.args.get("item"))
    dest = ctx.args["destination"]
    drec = C.vessel_reach(ctx, dest)
    tool_id = ctx.args.get("tool")
    if tool_id:
        require(ctx.holding(tool_id), "Hold the serving utensil", "capability")
    else:
        tool_id = next((h for h in w.held_items() if set(w.ent(h).get("capabilities", [])) & {"serve", "ladle", "grip_food", "scoop"}), None)
        require(tool_id or M.food(frec).get("phase") in ("solid",), "Hold a serving utensil", "capability")
    f = frec["food"]
    pieces = int(ctx.args.get("pieces") or 0)
    if pieces and f.get("pieces"):
        grams = M.qty(frec) * min(1.0, pieces / max(1, int(f["pieces"])))
    else:
        grams = float(ctx.args.get("amount_g") or 0) or M.qty(frec)
    part, moved = M.take(w, fid, grams)
    if pieces and part["food"].get("pieces"):
        part["food"]["pieces"] = pieces
    top = ctx.args.get("on_top_of")
    if top:
        trec = ctx.obj(top, touch=True)
        require(M.food(trec) and w.within(top, dest) or trec.get("location") == dest, "That food is not on this plate", "argument")
        part["relation"] = "on"
        new = M.create_from_record(w, part, top, "on") if M.food(trec).get("phase") not in ("liquid",) else X.add(w, part, dest, "in")
        if w.ent(new).get("stacked_loose"):
            w.mut(new).pop("stacked_loose", None)
    else:
        relation = "on" if drec.get("surface") else "in"
        new = M.create_from_record(w, part, dest, relation) if relation == "on" else X.add(w, part, dest, "in")
    if tool_id:
        M.contact(w, tool_id, fid, 0.5)
        M.contact(w, tool_id, new, 0.5)
    else:
        C.touch_food(ctx, new, 0.4)
    M.contact(w, dest, new, 0.3)
    if not ctx.args["careful"] and M.food(w.ent(new)).get("phase") in ("liquid", "paste"):
        from ..core import add_soil
        add_soil(w, dest, "food", 0.2)
        w.mut(dest)["rim_smears"] = True
    if src_arg_empty(w, fid, ctx.args["target"]):
        M.destroy_food(w, fid)
    C.seconds(ctx, 8.0 + C.pour_ml(frec, moved) / 100.0)
    return {"plated": new, "grams": round(moved, 1), "on": dest, "_subject": new}


def _skim(ctx, fid, frec, dest, drec):
    """Skim the foam (early in a boil) or fat layer off a liquid with a skimmer/ladle into the destination bowl."""
    w = ctx.w
    tool_id = C.tool(ctx, "skim", ctx.args.get("tool")) if ctx.args.get("tool") is None else ctx.args["tool"]
    if not ctx.holding(tool_id):
        raise ActionError("Hold the skimmer or ladle", "capability")
    tk = w.ent(tool_id).get("tool_kind")
    f = frec["food"]
    foam, fat = float(f.get("foam_g", 0.0)), float(f.get("fat_layer_g", 0.0))
    require(foam + fat > 0.5, "There is no foam or fat layer to skim", "precondition")
    broth_per_g = {"skimmer": 0.5, "slotted_spoon": 1.5, "ladle": 3.0}.get(tk, 2.0)
    taken = foam + fat
    broth = taken * broth_per_g
    m = w.mut(fid)
    m["food"]["foam_g"] = 0.0
    m["food"]["fat_layer_g"] = 0.0
    m["food"]["skimmed"] = True
    M.set_qty(w, fid, M.qty(m) - broth - taken)
    portion = M.portion_record(w.s, frec, broth + taken)
    portion["food"]["kind"] = "skimmings"
    portion["food"]["composition"] = {"foam": round(foam, 2), "fat": round(fat, 2), "broth": round(broth, 2)}
    portion["food"]["rte"] = False
    if drec.get("drain"):
        if fat > 0:
            d = w.mut(dest)
            d["_fat_g"] = round(float(d.get("_fat_g", 0.0)) + fat, 1)
            w.emit("fat_down_drain", target=dest, grams=round(fat, 1))
    else:
        X.add(w, portion, dest, "in")
    M.contact(w, tool_id, fid, 0.4)
    C.seconds(ctx, 20.0)
    return {"skimmed_g": round(taken, 1), "broth_lost_g": round(broth, 1), "tool": tk}


def src_arg_empty(w, fid, src_arg):
    return fid != src_arg and fid in w.s["entities"] and M.qty(w.ent(fid)) <= 0.01


@action("garnish", params={"target": "entity", "garnish": "entity", "amount_g": {"type": "number", "default": 2.0, "min": 0.1, "max": 100},
                           "placement": "?str:on_food|scatter|side"},
        duration=C.duration(6.0), irreversible=True, noise=0.0,
        doc="Garnish a plated dish (target = the plate or a food on it) from a garnish source (chopped herbs, seeds, lemon "
            "wedges, sour cream) placed on the food; scatter also dusts the rim. Mind each eater's aversions/allergies.")
@P.perceived_result
def garnish(ctx):
    w = ctx.w
    tid = ctx.args["target"]
    trec = ctx.obj(tid, touch=True)
    gid, grec, gvessel = _source_food(ctx, ctx.args["garnish"])
    if M.food(trec):
        host, plate_id = tid, (trec.get("location") if trec.get("location") in w.s["entities"] else None)
    else:
        plate_id = tid
        foods = [f for f in M.vessel_foods(w.s, tid) if f != gid]
        require(foods, "Garnish a plated dish", "precondition")
        foods.sort(key=lambda e: (-M.qty(w.ent(e)), e))
        host = foods[0]
    amount = float(ctx.args["amount_g"])
    part, moved = M.take(w, gid, amount)
    part["food"]["garnish"] = True
    placement = ctx.args.get("placement") or "on_food"
    if placement == "on_food":
        new = M.create_from_record(w, part, host, "on")
        if w.ent(new).get("stacked_loose"):
            w.mut(new).pop("stacked_loose", None)
    else:
        new = M.create_from_record(w, part, plate_id or host, "on" if (plate_id and w.ent(plate_id).get("surface")) else "in")
        if placement == "scatter" and plate_id:
            from ..core import add_soil
            add_soil(w, plate_id, "crumbs", 0.1)
    C.touch_food(ctx, new, 0.3)
    M.contact(w, host, new, 0.5)
    C.seconds(ctx, 5.0)
    return {"garnish": new, "on": host, "grams": round(moved, 2), "_subject": new}


@action("thaw", params={"target": "entity", "method": "str:fridge|cold_water|counter|microwave", "destination": "?entity",
                        "refresh_water": {"type": "bool", "default": False}},
        duration=C.duration(10.0), noise=0.0,
        doc="Start thawing a frozen item: fridge = set it (in its package or a container) on a tray/plate low in the fridge "
            "(destination) -- about a day per 2 kg; cold_water = submerge the sealed item in a bowl of cold water "
            "(destination; refresh the water every 30 min); counter = leave it out (unsafe: the surface warms past 5 C "
            "while the core is frozen). Microwave defrosting is an appliance program.")
@P.perceived_result
def thaw(ctx):
    w = ctx.w
    fid = ctx.args["target"]
    rec = C.food_target(ctx, fid)
    f = rec["food"]
    method = ctx.args["method"]
    require(float(f.get("ice", 0.0)) > 0.2 or method == "cold_water" and ctx.args.get("refresh_water"), "It is not frozen", "precondition")
    dest = ctx.args.get("destination")
    out = {"target": fid, "method": method}
    if method == "microwave":
        raise ActionError("Use the microwave's defrost program (appliance)", "argument")
    if method in ("fridge", "cold_water"):
        require(dest, "Name where it goes (tray in the fridge or the cold-water bowl)", "argument")
        drec = C.vessel_reach(ctx, dest)
        if method == "cold_water":
            sealed = (rec.get("package") and not rec.get("open")) or \
                (w.s["entities"].get(rec.get("location"), {}).get("category") in ("bag", "food_bag") and not w.s["entities"][rec["location"]].get("open"))
            require(float(drec.get("liquid_l", 0.0) or 0.0) >= 1.0 or drec.get("water_bath"), "Fill the bowl with cold water first", "precondition")
            m = w.mut(dest)
            if ctx.args["refresh_water"] or not m.get("water_bath"):
                m["water_bath"] = True
                m["internal_temp_c"] = 12.0
                m["heat_coupling"] = 1.0
                m["food_air_model"] = True
                m["ice_bath"] = False
                m["ua_w_k"] = 1.5
                m["air_heat_capacity_j_k"] = float(m.get("liquid_l", 2.0) or 2.0) * 4180.0
                m["setpoint_c"] = None
                M.register(w, "air", dest)
            item = fid if rec.get("package") else rec.get("location") if not M.food(w.s["entities"].get(rec.get("location"), {})) and rec.get("location") in w.s["entities"] else fid
            if not sealed:
                mm = w.mut(fid)
                mm["food"]["waterlogged"] = True
                M.add_contam(w, fid, "bacteria", 0.1)
                out["warning"] = "unsealed food in the water bath gets waterlogged and contaminated"
        else:
            if rec.get("location") == dest:
                pass
            from ...kernel import geometry as G
        moving = fid if method == "fridge" or rec.get("package") or rec.get("location") == "robot" else rec.get("location")
        if w.ent(moving).get("location") != dest:
            require(ctx.holding(moving) or ctx.free_hand() is not None, "You need a free hand", "capability")
            release_from_hands(w, moving)
            m = w.mut(moving)
            m["location"] = dest
            m["relation"] = "in" if drec.get("container") else "on"
            for key in ("spos", "pos", "shelf", "held_hand"):
                m.pop(key, None)
            if m["relation"] == "on":
                spot = geometry.surface_spot(w.s, dest, moving)
                require(spot is not None, "No room on it", "precondition")
                m["spos"] = spot
            w.log_move(moving, "robot", dest)
    m = w.mut(fid)
    m["food"]["thaw_method"] = method
    m["food"]["thaw_started_s"] = w.s["time_s"]
    z = M.zone_of(w.s, fid)
    out["zone"] = z["zone"]
    out["contained"] = z["contained"]
    C.seconds(ctx, 8.0)
    return out

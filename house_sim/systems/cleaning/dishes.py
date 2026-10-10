"""Hand-washing items at a sink (dishes, cookware, bottles, tools, filters, bins that fit).

`wash` scrubs every face of a held item (or one lying in the basin) with running water, the
held sponge/brush and whatever soap is on the tool or in the basin water. The item's material,
coating and care rules decide consequences: abrasive tools scratch nonstick/enamel/gilding,
thermal shock cracks cold glass or warps hot pans, water ruins motor bases and electronics,
knives must be wiped spine-first, fragile glass breaks among heavy pots, greasy water films
glasses, and toilet/floor/waste gear contaminates a food-prep sink. Soaking (leaving items in a
filled basin) is simulated by the soak system; suds must be rinsed off afterwards.
"""
from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from . import engine as E
from . import model as M
from . import state as S
from .surfaces import sink_contamination, thermal_shock, water_use

WATER_TEMP = {"cold": 12.0, "warm": 38.0, "hot": 55.0}


def find_sink(ctx, explicit=None):
    w = ctx.w
    if explicit:
        rec = ctx.obj(explicit, touch=True)
        require(rec.get("water_source"), "That has no running water", "precondition")
        return explicit
    rid = w.robot["room"]
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("water_source") and w.root_room(eid) == rid and geometry.reach_problem(w.s, eid) is None:
            return eid
    raise ActionError("Stand at a sink (destination) to wash things", "precondition")


@action("wash", params={"target": "entity", "destination": "?entity", "tool": "?entity", "face": "?str",
                        "temp": "?str:cold|warm|hot", "stroke": "?str:spine_first|along_edge", "side": "?str:soft|scrub",
                        "force": "?str:light|normal|hard"},
        duration=lambda ctx: ctx.data.get("seconds", 25.0), energy=0.015, noise=0.05, category="physical",
        doc="Hand-wash an item at a sink (destination) under running water with a held sponge, brush or cloth and soap "
            "(on the tool or in the basin water). Washes all faces unless face is given. temp cold|warm|hot (cold first for "
            "egg, dairy and raw dough). Knives: hold by the handle and wipe spine-first. Leaves the item wet and soapy: rinse it.")
def wash(ctx):
    w = ctx.w
    target = ctx.args["target"]
    temp = ctx.args.get("temp") or "warm"
    sink = find_sink(ctx, ctx.args.get("destination"))
    require(physics.water_available(w, sink), "No water at that sink", "resource")
    rec = w.s["entities"].get(target)
    require(rec is not None, "That is not visible here", "perception")
    held = target in w.held_items()
    in_basin = rec.get("location") == sink
    require(held or in_basin, "Hold the item at the sink (or put it in the basin) to wash it", "precondition")
    if in_basin:
        ctx.obj(target, touch=True)
    tool = S.held_tool(ctx, kinds=("sponge", "brush", "cloth", "pad", "towel"), required=False)
    if tool == target:
        tool = next((h for h in w.held_items() if h != target and S.tool_spec(w.ent(h)).get("kind") in ("sponge", "brush", "cloth", "pad")), None)
    require(tool is not None, "Hold a sponge, brush or cloth in your other hand", "capability")
    srec = w.ent(sink)
    basin = sink if float(srec.get("liquid_l", 0.0) or 0.0) > 0.5 else None
    if basin:
        S.norm_solution(w, basin)
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "wash", held=True)
    consequences = []
    # what the water does to the item
    if rec.get("water_safe") is False or rec.get("electronic") or rec.get("motor_base"):
        E.liquid_ingress(w, target, "washed under the tap")
        consequences.append("water got into it")
    if "sharp" in rec.get("hazards", []):
        if ctx.args.get("stroke") == "along_edge":
            robot = w.robot_m()
            robot["faults"] = sorted(set(robot.get("faults", [])) | {"gripper_cut"})
            w.stat("safety_violations")
            S.glog(w, "knife_edge_toward_hand", item=target)
            consequences.append("wiped toward the edge and cut the gripper pad")
    water_c = WATER_TEMP[temp]
    item_c = float(rec.get("temperature_c", 21.0))
    limit = float(rec.get("thermal_shock_c") or M.material(rec).get("thermal_shock_c") or (150.0 if M.material_key(rec) in ("aluminum", "nonstick", "stainless", "carbon_steel") else 999.0))
    if abs(item_c - water_c) > limit:
        thermal_shock(w, target, "{} C item in {} water".format(round(item_c), temp))
        consequences.append("thermal shock")
    if basin and (rec.get("fragile") or M.material_key(rec) in ("glass", "crystal", "china")):
        heavy = [c for c in w.children(basin) if c != target and float(w.ent(c).get("mass_kg", 0.0)) > 1.2]
        if heavy and in_basin:
            S.break_item(w, target, "knocked against {} in the basin".format(heavy[0]))
            consequences.append("broke against a heavy pot in the basin")
    if basin and S.turbidity(w.ent(basin)) > 8.0 and (M.material(rec).get("glossy") or M.material_key(rec) in ("glass", "crystal")):
        for face in faces:
            S.add_face_soil(w, target, face, "grease", 0.15)
        consequences.append("greasy wash water filmed it")
    if basin and S.turbidity(w.ent(basin)) > 15.0:
        for face in faces:
            S.add_face_soil(w, target, face, "food", 0.1)
    sink_contamination(ctx, sink, target)
    force_arg = ctx.args.get("force") or "normal"
    if force_arg == "hard" and (rec.get("fragile") or rec.get("stemware") or M.material_key(rec) in ("crystal",)):
        S.break_item(w, target, "twisted/pressed too hard while washing")
        consequences.append("snapped under too much force")
    if rec.get("food") or rec.get("category") in ("food", "produce"):
        S.add_contamination(w, target, "chemical", 0.4)
        consequences.append("soap on food - rinse produce with water only")
    hidden = [c for c in w.children(target) if w.ent(c).get("relation") in ("part", "attached") and S.total_soil(w.ent(c)) > 0.05]
    removed_total = {}
    force = ctx.args.get("force") or "normal"
    spec = S.tool_spec(w.ent(tool), side=ctx.args.get("side"))
    for face in faces:
        rule = (rec.get("faces_access") or {}).get(face) or {}
        if rule.get("needs_tool"):
            if spec.get("tool_kind") not in rule["needs_tool"] and spec.get("kind") not in rule["needs_tool"]:
                consequences.append("{} not reached without a {}".format(face, "/".join(rule["needs_tool"])))
                continue
        if rule.get("needs_parts_removed"):
            slots = rec.get("slots") or {}
            if any((slots.get(s) or {}).get("item") for s in rule["needs_parts_removed"]):
                consequences.append("{} hidden behind fitted parts".format(face))
                continue
        contact = {"cold": 60.0, "warm": 90.0, "hot": 120.0}[temp]
        result = E.clean_face(ctx, target, face, "wash", tool=tool, wetness=1.0, force=force, liquid=basin, temp=temp,
                              contact_s=contact, extra_sources=[("surfactant", 0.0, 0.0)])
        for kind, amount in result["removed"].items():
            removed_total[kind] = removed_total.get(kind, 0.0) + amount
    abrasion = float(spec.get("abrasion", 0.0))
    E.check_abrasion(w, target, abrasion, force)
    soapy = bool(E.classes_present(E.chem_sources(w, rec, faces[0] if faces else "main", tool=tool, liquid=basin)).get("surfactant", 0.0) >= 0.3)
    m = w.mut(target)
    m["wetness"] = 1.0
    m["temperature_c"] = S.rnd((item_c + water_c) / 2.0, 2)
    if soapy:
        m["cl_suds"] = 0.6
        S.add_residue(w, target, {"surfactant": 1.0}, 0.5)
    m["washed_t"] = S.now(w)
    if hidden and S.realistic(ctx):
        # whether the fitted parts are dirty underneath is not seen: only that they were left on
        consequences.append("fitted parts were left on and not washed")
    elif hidden:
        consequences.append("parts left assembled stay dirty: " + ", ".join(hidden))
    if basin:
        bm = w.mut(basin)
        bm["cl_soil_g"] = S.rnd(float(bm.get("cl_soil_g", 0.0) or 0.0) + sum(removed_total.values()) * 20.0, 2)
    tm = w.mut(tool)
    tm["wetness"] = 0.9
    water_use(w, (2.5 if temp != "hot" else 4.0) * max(1, len(faces)) * 0.6)
    ctx.data["seconds"] = 15.0 + 10.0 * len(faces) * (1.4 if force == "hard" else 1.0)
    detail = S.removed_text(ctx, removed_total, empty="no soil")
    return "Washed {} ({}){}".format(target, detail, ("; " + "; ".join(consequences)) if consequences else "")

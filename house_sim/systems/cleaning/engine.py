"""The cleaning pass: what one wipe/scrub/wash/dust/mop pass does to one face of one entity.

All actions in this domain funnel through clean_face() so the same physics decide every
entry: soil removal (mechanics x moisture x chemistry x dwell), smearing by dry or dirty
tools, scratching by tools harder than the finish, chemical attack on incompatible finishes,
water marks on water-sensitive finishes, lint and streaks on glossy surfaces, pathogen
removal/transfer, tool loading and cross-contamination between zones.
"""
from ...kernel import geometry
from ...kernel.errors import ActionError, require
from . import model as M
from . import state as S

CONTACT_S = 20.0      # seconds of contact chemistry gets while a face is worked by hand


# ----------------------------------------------------------------------
# faces and access

def door_faces(rec):
    pair = rec.get("connects") or []
    return ["side:" + r for r in pair]


def declared_faces(rec):
    faces = S.faces_of(rec)
    if not faces and rec.get("connects"):
        faces = door_faces(rec)
    return faces


def parse_faces(ctx, rec, value, action, held=False):
    faces = declared_faces(rec)
    if value in (None, "", "default"):
        if not faces:
            return ["main"]
        if held or action in ("wash", "rinse_item"):
            return list(faces)
        return default_faces(ctx, rec, faces)
    if value == "all":
        return list(faces) if faces else ["main"]
    names = [v.strip() for v in str(value).split(",") if v.strip()]
    for name in names:
        require(not faces or name in faces or name == "main",
                "No face named {}; faces: {}".format(name, ", ".join(faces) or "main"), "argument")
    return names


def default_faces(ctx, rec, faces):
    """Faces worked when none is named: the accessible, outward faces (not hidden ones)."""
    room = ctx.w.robot["room"]
    picked = []
    for face in faces:
        if face.startswith("side:"):
            if face == "side:" + room:
                picked.append(face)
            continue
        rule = (rec.get("faces_access") or {}).get(face) or {}
        if rule.get("hidden") or rule.get("from_room") or rule.get("outside"):
            continue
        picked.append(face)
    return picked or faces[:1]


def check_face_access(ctx, eid, face, tool=None):
    w = ctx.w
    rec = w.ent(eid)
    robot = w.robot
    if face.startswith("side:"):
        require(face[5:] == robot["room"], "That side of it faces {}; go there to reach it".format(face[5:]), "precondition")
        return
    rule = (rec.get("faces_access") or {}).get(face) or {}
    if rule.get("from_room"):
        require(robot["room"] == rule["from_room"] or (rule.get("or_open") and rec.get("open")),
                "That face is reachable only from {}".format(rule["from_room"]), "precondition")
    if rule.get("needs_open"):
        require(rec.get("open") or not rec.get("openable"), "Open it to reach that face", "precondition")
    if rule.get("needs_cover_open"):
        require(rec.get("cover_open"), "Open the cover first (open_cover)", "precondition")
    if rule.get("needs_detached"):
        require(rec.get("location") == "robot" or rec.get("relation") not in ("part", "attached"),
                "Remove it from its mount first", "precondition")
    if rule.get("crouch"):
        require(robot.get("posture") in ("crouching", "kneeling"), "Crouch or kneel to reach that face", "precondition")
    if rule.get("tool_kinds"):
        spec = S.tool_spec(w.ent(tool)) if tool else {}
        require(spec.get("tool_kind") in rule["tool_kinds"] or spec.get("kind") in rule["tool_kinds"],
                "Reaching {} needs a {}".format(face, " or ".join(rule["tool_kinds"]).replace("_", " ")), "capability")
    if rule.get("needs_parts_removed"):
        slots = rec.get("slots") or {}
        for slot in rule["needs_parts_removed"]:
            require(not (slots.get(slot) or {}).get("item"), "Remove the {} first to reach {}".format(slot, face), "precondition")


def reach_target(ctx, eid, tool=None, touch=True):
    """Visibility + reach, allowing extension tools (extendable dusters, wands) to add height."""
    w = ctx.w
    if tool and float(S.tool_spec(w.ent(tool)).get("reach_extension_m", 0.0)) > 0:
        rec = ctx.obj(eid, touch=False)
        problem = geometry.reach_problem(w.s, eid)
        if problem and "high" in problem.lower():
            height = geometry.height_m(w.s, eid)
            ext = float(S.tool_spec(w.ent(tool)).get("reach_extension_m", 0.0))
            require(height <= geometry.robot_reach_m(w.s) + ext + 1e-9, "Too high even with the extension pole", "capability")
        elif problem:
            raise ActionError(problem, "precondition")
        ctx.check_permission(eid, rec, ctx.spec.name)
        return rec
    return ctx.obj(eid, touch=touch)


# ----------------------------------------------------------------------
# chemistry available on a face

def applied_on(rec, face):
    return [a for a in (rec.get("cl_applied") or []) if a.get("face") in (face, "all")]


def chem_sources(w, rec, face, tool=None, liquid=None, contact_s=CONTACT_S, extra=()):
    """List of (class, power, elapsed_s) acting on a face during a pass."""
    t = S.now(w)
    sources = []
    for entry in applied_on(rec, face):
        start = float(entry.get("t", t))
        end = min(t, float(entry.get("wet_until", t)))
        elapsed = max(0.0, end - start) + contact_s
        strength = float(entry.get("s", 1.0))
        for cls, power in M.product_classes(entry.get("p")).items():
            sources.append((cls, power * strength, elapsed))
    if tool:
        trec = w.ent(tool)
        for product, ml in sorted((trec.get("cl_chem") or {}).items()):
            if float(ml) <= 0:
                continue
            for cls, power in M.product_classes(product).items():
                sources.append((cls, power, contact_s))
        if trec.get("product") and S.tool_spec(trec).get("kind") == "wipe":
            for cls, power in M.product_classes(trec["product"]).items():
                sources.append((cls, power, contact_s))
    if liquid:
        lrec = w.ent(liquid)
        for cls, power in S.solution_classes(lrec).items():
            sources.append((cls, power, contact_s + float(extra[0]) if extra else contact_s))
    return sources


def classes_present(sources, minimum=0.1):
    out = {}
    for cls, power, _ in sources:
        if power >= minimum:
            out[cls] = max(out.get(cls, 0.0), power)
    return out


# ----------------------------------------------------------------------
# damage checks

def check_chemicals(w, eid, classes, source, face=None):
    rec = w.ent(eid)
    info = M.material(rec)
    bad = info.get("bad", {})
    for cls in sorted(classes):
        strength = classes[cls]
        if cls in bad and strength >= 0.1:
            kind, severity = bad[cls]
            S.damage(w, eid, kind, severity * min(1.0, strength), "{} on {}".format(source, M.material_key(rec)))
    # electronics and screens
    if rec.get("screen") or info.get("screen"):
        for cls in ("ammonia", "solvent", "chlorine"):
            if classes.get(cls, 0) >= 0.1 and M.material_key(rec) not in ("screen_matte", "screen_oled", "screen_glass", "screen_eink"):
                S.damage(w, eid, "coating_stripped", 0.5 * classes[cls], source)
    if rec.get("rubber_gasket") and classes.get("chlorine", 0) >= 0.1:
        S.damage(w, eid, "gasket_degraded", 0.4, source)


def check_abrasion(w, eid, abrasion, force="normal", direction=None):
    rec = w.ent(eid)
    info = M.material(rec)
    tolerance = float(rec.get("abrasion_tolerance", info.get("abrasion", 0.3)))
    effective = abrasion * M.FORCE.get(force or "normal", 1.0)
    if info.get("grain") and direction in ("against_grain", "circular"):
        effective *= 1.6
    if M.material_key(rec) in ("wood_deck", "teak", "cedar", "sealed_wood", "wood") and direction == "against_grain" and abrasion >= 0.3:
        S.damage(w, eid, "raised_grain", 0.25, "scrubbed across the grain")
    if M.material_key(rec) in ("wood_deck", "teak", "cedar", "sealed_wood", "wood") and direction == "against_grain" and abrasion >= 0.3:
        S.damage(w, eid, "raised_grain", 0.25, "scrubbed across the grain")
    if rec.get("coating") == "nonstick":
        tolerance = min(tolerance, 0.18)
    if rec.get("coating") in ("enamel", "porcelain_enamel"):
        tolerance = min(tolerance, 0.3)
    if rec.get("seasoned") and effective > 0.6:
        S.damage(w, eid, "seasoning_stripped", 0.3, "abrasive scrubbing stripped the seasoning")
        w.mut(eid)["seasoned"] = False
    if effective > tolerance + 0.02:
        kind = "burnished" if info.get("burnish") and abrasion < 0.6 else ("coating_scratched" if rec.get("coating") or info.get("screen") else "scratched")
        S.damage(w, eid, kind, min(1.0, (effective - tolerance) * 1.2), "abrasive tool too hard for {}".format(M.material_key(rec)))
        return True
    return False


def face_material(rec, face):
    """Material of one face (face_materials {face: key}) else of the entity."""
    key = (rec.get("face_materials") or {}).get(face)
    if key and key in M.MATERIALS:
        return dict(rec, finish=key)
    return rec


def check_face_liquid(w, eid, face, wetness, classes=None, source="cleaning"):
    """Faces that must stay dry (igniters, keypads, burner ports, motor vents) or are a different material."""
    rec = w.ent(eid)
    if face in (rec.get("liquid_sensitive_faces") or []) and wetness >= 0.4:
        liquid_ingress(w, eid, "liquid on {} ({})".format(face, source))
        return True
    if classes and face in (rec.get("face_materials") or {}):
        frec = face_material(rec, face)
        bad = M.material(frec).get("bad", {})
        for cls in sorted(classes):
            if cls in bad and classes[cls] >= 0.1:
                kind, severity = bad[cls]
                S.damage(w, eid, "{}_{}".format(face, kind), severity * min(1.0, classes[cls]), "{} on {}".format(source, face))
    return False


def drip_down(w, eid, face, wet):
    """Working a face with a wet tool/product lets dirty water run onto the face below (drip_to)."""
    rec = w.ent(eid)
    below = (rec.get("drip_to") or {}).get(face)
    if below and wet >= 0.3:
        S.add_face_soil(w, eid, below, "streaks", 0.25)
        S.glog(w, "drips_onto_lower_face", target=eid, face=face, below=below)


def wet_grit(w, eid, face, wetness):
    """A wet tool on dry grit/dust/crumbs/glitter turns it into mud/paste instead of lifting it."""
    soil = S.face_soil(w.ent(eid), face)
    gritty = {k: v for k, v in soil.items() if k in ("dust", "dirt", "sand", "crumbs", "glitter", "drywall_dust", "sawdust", "cat_litter", "pollen")}
    if wetness < 0.5 or sum(gritty.values()) < 0.25:
        return False
    part = S.mut_face(w, eid, face)
    for kind, level in sorted(gritty.items()):
        part[kind] = S.rnd(level * 0.6)
    part["mud"] = S.rnd(M.clamp(part.get("mud", 0.0) + sum(gritty.values()) * 0.4))
    part["streaks"] = S.rnd(M.clamp(part.get("streaks", 0.0) + 0.2))
    S.sync(w, eid)
    S.glog(w, "wet_tool_on_grit", target=eid, face=face)
    return True


def check_water(w, eid, wetness, source="wet tool"):
    """Free water on water-sensitive finishes (unsealed wood, MDF, paper, electronics, leather...)."""
    rec = w.ent(eid)
    if rec.get("paint_curing_until") is not None and S.now(w) < float(rec["paint_curing_until"]) and wetness >= 0.3:
        S.damage(w, eid, "paint_smeared", 0.4, "wet-wiped uncured paint")
    info = M.material(rec)
    limit = float(rec.get("water_tolerance", info.get("water", 0.5)))
    if rec.get("water_safe") is False or rec.get("electronic") or rec.get("liquid_sensitive"):
        limit = min(limit, 0.02)
    film = {0.0: 0.0, 0.2: 0.015, 0.4: 0.04, 0.85: 0.25}
    deposited = 0.0
    for level, mm in sorted(film.items()):
        if wetness >= level:
            deposited = mm
    if deposited > limit + 1e-9:
        if info.get("textile") or rec.get("upholstered"):
            S.damage(w, eid, "water_ring", 0.25, source)
        elif rec.get("electronic") or rec.get("liquid_sensitive") or rec.get("water_safe") is False:
            liquid_ingress(w, eid, source)
        elif info.get("swells"):
            S.damage(w, eid, "swollen", 0.4, source)
        elif M.material_key(rec) in ("mirror",):
            S.damage(w, eid, "desilvering", 0.2, source)
        else:
            S.damage(w, eid, "water_marked", 0.35, source)
        return True
    return False


def liquid_ingress(w, eid, source):
    rec = w.ent(eid)
    S.damage(w, eid, "liquid_ingress", 0.6, source)
    powered = rec.get("on") or (rec.get("plug_required") and rec.get("plugged_in")) or rec.get("live")
    if powered:
        w.stat("electrical_hazard")
        S.glog(w, "electrical_hazard", target=eid, source=source)


def hot_surface(w, eid):
    rec = w.ent(eid)
    temp = float(rec.get("temperature_c", 21.0))
    limit = float(rec.get("max_touch_temp_c", 50.0))
    return temp > limit, temp


def burn_check(ctx, eid):
    """Cleaning a hot surface: the cloth scorches and the hand is burned (consequence)."""
    w = ctx.w
    hot, temp = hot_surface(w, eid)
    if hot:
        wearing = set(w.robot.get("wearing", []))
        if not wearing & {"oven_mitts", "heat_gloves"} or temp > 260:
            w.stat("burns")
            S.glog(w, "burn", target=eid, temp=temp)
            for tool in w.held_items():
                if S.tool_spec(w.ent(tool)).get("kind") in ("cloth", "towel", "sponge", "paper", "wipe"):
                    S.damage(w, tool, "scorched", 0.6, "hot surface")
        return True
    return False


def live_hazard(ctx, eid, face=None):
    """Hand/tool inside a powered machine with blades or exposed elements (toaster, disposal, blender base)."""
    w = ctx.w
    rec = w.ent(eid)
    if not (rec.get("pinch_hazard_when_powered") or rec.get("exposed_element") or rec.get("unplug_before_cleaning")):
        return False
    if face and rec.get("hazard_faces") and face not in rec["hazard_faces"]:
        return False
    powered = (rec.get("plug_required") and rec.get("plugged_in")) or rec.get("on") or rec.get("switch_on")
    if rec.get("hardwired") and rec.get("power_switch_on", True):
        powered = True
    if powered:
        w.stat("safety_violations")
        S.glog(w, "live_machine_contact", target=eid, face=face)
        metal = any(S.tool_spec(w.ent(t)).get("metal") or w.ent(t).get("material") in ("steel", "metal") for t in w.held_items())
        if metal or rec.get("exposed_element"):
            w.stat("electrical_hazard")
        return True
    return False


def accidental_input(w, eid, presses=1):
    rec = w.ent(eid)
    if not (rec.get("controls_live") or rec.get("input_device") or rec.get("touch_controls") or rec.get("keypad")):
        return
    host = rec.get("host") or eid
    target = host if host in w.s["entities"] else eid
    hrec = w.ent(target)
    awake = hrec.get("on") and not hrec.get("locked_screen") and not hrec.get("input_locked")
    if rec.get("input_locked") or rec.get("locked_screen"):
        awake = False
    if awake:
        m = w.mut(target)
        m["stray_inputs"] = int(m.get("stray_inputs", 0)) + presses
        S.gsec(w)["accidental_inputs"] += presses
        S.glog(w, "accidental_input", target=target, via=eid)
        w.emit("accidental_input", target=target)
        from ...kernel.plugins import run_hooks
        run_hooks("cleaning:accidental_input", w, target, eid)


# ----------------------------------------------------------------------
# the pass

def topple_risk(w, surface_id, tool_spec, around):
    """Sweeping a duster/cloth across a crowded surface knocks over the tippiest fragile item."""
    if around:
        return None
    items = S.items_on(w, surface_id)
    if not items:
        return None
    rec = w.ent(surface_id)
    sw, sd = (rec.get("surface_size_m") or (rec.get("size_m") or [1, 1])[:2])[:2]
    used = 0.0
    tipsy = []
    for item in items:
        irec = w.ent(item)
        size = irec.get("size_m") or [0.1, 0.1, 0.1]
        used += float(size[0]) * float(size[1])
        slender = float(size[2]) / max(0.01, min(float(size[0]), float(size[1])))
        if (irec.get("fragile") or "fragile" in irec.get("hazards", [])) and slender >= 2.0:
            tipsy.append((-slender, item))
    crowded = used / max(0.01, float(sw) * float(sd)) > 0.3
    if crowded and tipsy and tool_spec.get("kind") in ("duster", "cloth", "brush"):
        return sorted(tipsy)[0][1]
    return None


def knock_over(w, item, surface_id):
    m = w.mut(item)
    m["orientation"] = "on_side"
    height = geometry.height_m(w.s, surface_id)
    S.glog(w, "knocked_over", target=item, surface=surface_id)
    w.emit("knocked_over", target=item)
    if m.get("fragile") or "fragile" in m.get("hazards", []):
        S.break_item(w, item, "knocked over while cleaning around it")
    w.stat("collisions")
    return height


def clean_face(ctx, eid, face, action, tool=None, wetness=0.0, force="normal", direction=None, liquid=None,
               mech=None, abrasion=None, capture=None, kinds=None, extra_sources=(), include_exposed=True,
               contact_s=CONTACT_S, soak_s=0.0, temp=None):
    """One pass of `action` over a face. Returns {"removed": {kind: amount}, "loose": {kind: grams-ish}}."""
    w = ctx.w
    S.reconcile(w, eid)
    rec = w.ent(eid)
    spec = S.tool_spec(w.ent(tool), side=ctx.args.get("side"), angle=ctx.args.get("angle")) if tool else {"mech": 0.2, "abrasion": 0.0, "capture": 0.3, "lint": 0.0}
    mech = float(spec.get("mech", 0.3) if mech is None else mech) * M.FORCE.get(force or "normal", 1.0)
    abrasion = float(spec.get("abrasion", 0.0) if abrasion is None else abrasion)
    if spec.get("abrasion_wet") is not None and wetness >= 0.3:
        abrasion = float(spec["abrasion_wet"])
    capture = float(spec.get("capture", 0.5) if capture is None else capture)
    sources = chem_sources(w, rec, face, tool=tool, liquid=liquid, contact_s=contact_s, extra=(soak_s,) if soak_s else ()) + list(extra_sources)
    t_now = S.now(w)
    if any(float(a.get("wet_until", 0.0)) >= t_now for a in applied_on(rec, face)):
        wetness = max(wetness, 0.4)          # the sprayed face itself is wet
    if float(rec.get("wetness", 0.0) or 0.0) > 0.3:
        wetness = max(wetness, 0.3)
    softened = float((rec.get("_softened") or {}).get(face, (rec.get("_softened") or {}).get("all", 0.0)))
    # abrasive products add mechanical help and abrasion
    for product in sorted(set([a.get("p") for a in applied_on(rec, face)] + list((w.ent(tool).get("cl_chem") or {}).keys() if tool else []))):
        if product in M.PRODUCTS and M.PRODUCTS[product].get("abrasion"):
            abrasion = max(abrasion, float(M.PRODUCTS[product]["abrasion"]))
            mech = max(mech, 0.6)
    parts = [("face", face)]
    if include_exposed and face == S.primary_face(rec):
        parts += [("shadow", item) for item in S.exposed_shadows(w, eid)]
    removed, loose = {}, {}
    dirty_tool = tool is not None and float(w.ent(tool).get("soil_load", 0.0) or 0.0) > 0.7 and action not in ("rinse", "squeegee")
    hot_water_on_protein = temp == "hot"
    for part_kind, part_name in parts:
        if part_kind == "face":
            soil = dict(S.face_soil(w.ent(eid), face))
        else:
            soil = dict((w.ent(eid).get("_shadows") or {}).get(part_name, {}))
        if not soil:
            continue
        new = dict(soil)
        for kind, level in sorted(soil.items()):
            if not isinstance(level, (int, float)) or level <= 0 or (kinds is not None and kind not in kinds):
                continue
            info = M.soil_info(kind)
            if action == "dust" and info["cls"] != "loose" and kind not in ("fingerprints",):
                continue
            if action == "squeegee" and kind not in ("streaks", "water_spots", "soap_scum", "fingerprints", "dirt", "pollen"):
                continue
            chem = M.chem_power(kind, sources, softened)
            if temp == "cold" and kind in ("grease", "sunscreen", "wax"):
                chem *= 0.6           # fats stay solid in cold water
            if hot_water_on_protein and info.get("heat_sets"):
                chem *= 0.3
                new["burnt"] = S.rnd(M.clamp(new.get("burnt", 0.0) + level * 0.25))
            frac = M.removal_fraction(kind, mech, wetness, chem, action=action, softened=softened)
            if info["cls"] == "loose" and action in ("wipe", "dust", "scrub"):
                frac = max(frac, 0.85 * min(1.0, mech / max(0.05, info["mech"])))
            if action == "squeegee" and kind == "streaks":
                frac = 0.9
            took = level * frac
            new[kind] = S.rnd(level - took)
            removed[kind] = removed.get(kind, 0.0) + took
            if info["cls"] == "loose":
                loose[kind] = loose.get(kind, 0.0) + took
            # smearing: dry tool on soils that need moisture
            if info.get("wet") and wetness < 0.12 and action in ("wipe", "dust", "polish") and info.get("smear"):
                smear = info["smear"]
                new[smear] = S.rnd(M.clamp(new.get(smear, 0.0) + level * 0.35))
        if dirty_tool:
            new["streaks"] = S.rnd(M.clamp(new.get("streaks", 0.0) + 0.25))
            carried = w.ent(tool).get("cl_carried") or {}
            for kind, level in sorted(carried.items()):
                new[kind] = S.rnd(M.clamp(new.get(kind, 0.0) + level * 0.3))
        if part_kind == "face":
            m = S.mut_face(w, eid, face)
            m.clear()
            m.update({k: v for k, v in new.items() if v > 0.0005})
        else:
            m = w.mut(eid)
            m.setdefault("_shadows", {})[part_name] = {k: v for k, v in new.items() if v > 0.0005}
            if not m["_shadows"][part_name]:
                del m["_shadows"][part_name]
    # glossy finishes: lint, streaky film from a wet product that is not buffed dry
    info = M.material(w.ent(eid))
    glossy = info.get("glossy") or w.ent(eid).get("glossy")
    if glossy and tool and action in ("wipe", "dry", "polish", "dust"):
        lint = float(spec.get("lint", 0.0))
        if lint > 0.2:
            S.add_face_soil(w, eid, face, "lint", lint * 0.4)
        if action in ("wipe",) and wetness >= 0.3:
            m = w.mut(eid)
            m.setdefault("cl_film", {})[face] = S.now(w) + 60.0
            S.watch(w, "chem", eid)
        if action in ("dry", "polish") or (action == "wipe" and wetness < 0.12):
            m = w.mut(eid)
            if (m.get("cl_film") or {}).get(face) is not None:
                del m["cl_film"][face]
    if info.get("grain") and direction in ("against_grain", "circular") and action in ("wipe", "polish", "dry"):
        S.add_face_soil(w, eid, face, "streaks", 0.3)
    # pathogens: physical removal by wiping (not disinfection), soap helps
    cont = S.contamination_of(w.ent(eid))
    if cont and action in ("wipe", "scrub", "wash", "mop", "rinse"):
        surf = classes_present(sources).get("surfactant", 0.0)
        factor = 0.35 if surf >= 0.3 else 0.6
        if action == "rinse":
            factor = 0.7
        S.scale_contamination(w, eid, factor, kinds=("bacteria", "raw_meat", "biohazard"))
        allergens = [k for k in cont if k.startswith("allergen")]
        if allergens and surf >= 0.3:
            S.scale_contamination(w, eid, 0.1, kinds=tuple(allergens))
    # wiping removes products that were dwelling on the face (ending their contact time)
    if action in ("wipe", "scrub", "dry", "squeegee", "rinse", "wash", "mop", "polish"):
        rec = w.ent(eid)
        lifted = applied_on(rec, face)
        if lifted:
            m = w.mut(eid)
            m["cl_applied"] = [a for a in m["cl_applied"] if a.get("face") not in (face, "all")]
            if tool and action in ("wipe", "scrub", "mop", "dry", "polish"):
                # the cloth soaks up the product and becomes damp with it
                tm = w.mut(tool)
                absorb = max(0.02, float(spec.get("absorb_l", 0.1) or 0.1))
                ml = sum(float(a.get("ml", 8.0)) for a in lifted)
                tm["wetness"] = S.rnd(M.clamp(float(tm.get("wetness", 0.0) or 0.0) + ml / 1000.0 / absorb))
                if action != "dry":
                    chem = tm.setdefault("cl_chem", {})
                    for a in lifted:
                        if a.get("p") and a.get("p") not in ("water",):
                            chem[a["p"]] = S.rnd(float(chem.get(a["p"], 0.0)) + float(a.get("ml", 8.0)) * 0.4, 2)
        S.scale_residue(w, eid, 0.15 if action in ("rinse",) else (0.5 if action in ("wipe", "scrub", "wash", "mop") else 0.8))
    S.sync(w, eid)
    if tool:
        total = sum(removed.values())
        S.load_tool(w, tool, total * 0.25)
        if total > 0:
            m = w.mut(tool)
            carried = m.setdefault("cl_carried", {})
            for kind, amount in sorted(removed.items()):
                if M.soil_info(kind)["cls"] in ("film", "adhered", "bio", "stain"):
                    carried[kind] = S.rnd(M.clamp(carried.get(kind, 0.0) + amount * 0.3))
        S.zone_transfer(w, tool, eid)
    return {"removed": {k: S.rnd(v) for k, v in sorted(removed.items())}, "loose": loose, "capture": capture}


def describe(result, ctx=None):
    """What a pass did: exact per-kind removal (oracle) or the kinds that visibly lessened (realistic perception)."""
    removed = result.get("removed") or {}
    if not removed:
        return "no change"
    return S.removed_text(ctx, removed)


def area_of(rec, face=None):
    """Working area of one face (m2): horizontal surfaces use width x depth, vertical things the two largest dims."""
    if rec.get("surface_size_m"):
        size = rec["surface_size_m"]
        return max(0.01, min(float(size[0]) * float(size[1]), 6.0))
    size = rec.get("size_m") or [0.5, 0.5, 0.1]
    if rec.get("surface") or rec.get("accepts_items") and not rec.get("container"):
        area = float(size[0]) * float(size[1])
    else:
        dims = sorted(float(v) for v in size[:3])
        area = dims[-1] * dims[-2]
    return max(0.01, min(area, 6.0))

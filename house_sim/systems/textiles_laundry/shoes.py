"""Shoe care: polishing and lacing (fidelity S/G).

Shoes carry material (leather, suede, canvas, mesh_synthetic, rubber, patent_leather), colour,
side, pair_id (secret), size, eyelets (pairs) and lacing {lace, pattern, eyelets_used, start,
ends_diff_cm, ends_short} or None when unlaced; _original_lacing records the pattern the owner used.
Polish: brush dry dirt off first (grit ground in scratches), remove laces (they get stained),
match colour (black on brown darkens it), never on suede/nubuck/canvas/mesh (ruins the nap),
patent only wiped, work over a protective sheet (polish stains carpet/rugs), apply then let haze
~5 min before buffing (buffing wet polish smears).
Lacing: required length = 24 cm x eyelet pairs (criss-cross) or 22 cm (bar); start at the toe.
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ..core import release_from_hands
from . import model as M

POLISHABLE = {"leather"}
NEEDED_PER_PAIR = {"criss_cross": 24.0, "bar": 22.0, "straight": 22.0, "ladder": 28.0}


def _work_surface_ok(w, shoe):
    rec = w.ent(shoe)
    loc = rec.get("location")
    if loc in w.s["entities"] and w.ent(loc).get("protective_sheet"):
        return True, None
    if loc == "robot":
        return True, None
    if loc in w.s["rooms"]:
        room = w.s["rooms"][loc]
        rugs = [e for e in w.children(loc) if w.ent(e).get("category") == "rug" and w.ent(e).get("pos") and rec.get("pos") and
                tuple(rec["pos"]) in [tuple(c) for c in geometry.footprint(w.ent(e))]]
        if rugs:
            return False, rugs[0]
        if M.floor_material(w, loc) in ("carpet",):
            return False, "floor_" + loc
        return True, None
    if loc in w.s["entities"]:
        parent = w.ent(loc)
        if parent.get("material") in ("fabric", "wool", "cotton") or parent.get("upholstered"):
            return False, loc
    return True, None


@action("polish_shoes", params={"target": "entity", "tool": "?entity",
                                "stage": {"type": "str", "default": "apply", "choices": ["brush", "apply", "buff", "wipe"]}},
        duration=lambda ctx: {"brush": 60.0, "apply": 120.0, "buff": 120.0, "wipe": 30.0}[ctx.args["stage"]],
        energy=0.01, category="physical",
        doc="Shoe care in stages with a held tool: brush (shoe/suede brush; removes dry dirt), apply (held shoe polish, "
            "colour-matched; laces out; over newspaper), buff (horsehair brush or cloth, after the polish has hazed ~5 min), "
            "wipe (damp cloth for patent/smooth leather).")
def polish_shoes(ctx):
    w = ctx.w
    shoe = ctx.args["target"]
    rec = ctx.obj(shoe, touch=True)
    require(rec.get("pair_kind") == "shoe" or rec.get("category") == "footwear", "That is not a shoe", "argument")
    stage = ctx.args["stage"]
    tool = ctx.args.get("tool")
    if tool:
        require(ctx.holding(tool), "Hold the tool", "capability")
    material = rec.get("material")
    trec = w.ent(tool) if tool else {}
    if stage == "brush":
        require(trec.get("tool_kind") in ("shoe_brush", "suede_brush", "clothes_brush"), "Hold a shoe or suede brush", "capability")
        wet = float(rec.get("wetness", 0.0))
        for kind in ("dirt", "dust", "mud", "salt"):
            level = M.soil_level(rec, kind)
            if level > 0:
                if wet > 0.2 and kind == "mud":
                    M.set_soil(w, shoe, kind, level * 0.9)
                    w.mut(shoe)["mud_smeared"] = True
                else:
                    M.set_soil(w, shoe, kind, level * 0.15)
        if material == "suede" and trec.get("tool_kind") == "suede_brush":
            w.mut(shoe)["nap_restored"] = True
        return "Brushed " + shoe
    if stage == "apply":
        require(trec.get("shoe_polish"), "Hold a tin or bottle of shoe polish", "capability")
        require(float(trec.get("remaining", 1.0)) > 0, "The polish is used up", "resource")
        if material in ("suede", "canvas", "mesh_synthetic") or rec.get("nubuck"):
            M.add_damage(w, shoe, "polish_ruined_nap", 0.8, why="wax polish on {}".format(material))
        elif material == "patent_leather":
            M.add_damage(w, shoe, "patent_hazed", 0.3, why="polish on patent leather")
        polish_color = trec.get("color", "neutral")
        if polish_color not in ("neutral", rec.get("color")):
            if not (polish_color == "brown" and rec.get("color") in ("tan", "cognac")):
                M.add_damage(w, shoe, "color_changed", 0.5, why="{} polish on {} shoes".format(polish_color, rec.get("color")))
        grit = M.soil_level(rec, "dirt") + M.soil_level(rec, "mud") + M.soil_level(rec, "salt")
        if grit >= 0.1:
            M.add_damage(w, shoe, "scratched", 0.3, why="polished over dirt/salt")
        lacing = rec.get("lacing")
        if lacing and lacing.get("lace") and w.has(lacing["lace"]):
            M.add_soil(w, lacing["lace"], "stain_polish", 0.4)
        ok, victim = _work_surface_ok(w, shoe)
        if not ok and victim and w.has(victim):
            M.add_soil(w, victim, "stain_polish", 0.3)
            M.add_damage(w, victim, "polish_stain", 0.3, why="shoe polished without a protective sheet")
        m = w.mut(shoe)
        m["polish_layer"] = 1.0
        m["_polish_applied_at"] = M.now(w)
        m["polish_color"] = polish_color
        tm = w.mut(tool)
        tm["remaining"] = round(max(0.0, float(tm.get("remaining", 1.0)) - 0.03), 4)
        return "Applied {} polish to {}".format(polish_color, shoe)
    if stage == "buff":
        require(trec.get("tool_kind") in ("shoe_brush", "buffing_cloth") or M.is_textile(trec), "Hold a horsehair brush or buffing cloth", "capability")
        m = w.mut(shoe)
        applied = rec.get("_polish_applied_at")
        if applied is not None and float(rec.get("polish_layer", 0.0)) > 0:
            if M.now(w) - float(applied) >= 300.0:
                m["shine"] = 0.85 if material in POLISHABLE else 0.4
                if M.soil_level(rec, "scuff") > 0:
                    M.set_soil(w, shoe, "scuff", 0.0)
            else:
                m["shine"] = 0.3
                m["polish_smeared"] = True
            m = w.mut(shoe)
            m["polish_layer"] = 0.2
        else:
            m["shine"] = max(float(m.get("shine", 0.0)), 0.35 if material in POLISHABLE else float(m.get("shine", 0.0)))
        shine = float(w.ent(shoe).get("shine", 0.0))
        ctx.grant(shoe, "inspect", ["shine", "polish_smeared"])      # buffing is done looking at the gloss
        if ctx.realistic:
            look = "a high gloss" if shine >= 0.7 else ("a soft sheen" if shine >= 0.4 else "still dull")
            return "Buffed {} ({})".format(shoe, look)
        return "Buffed {} (shine {:.2f})".format(shoe, shine)
    # wipe
    require(M.is_textile(trec) or trec.get("tool_kind") in ("cloth",), "Hold a cloth", "capability")
    for kind in ("dust", "dirt", "fingerprints", "salt"):
        if M.soil_level(rec, kind) > 0:
            M.set_soil(w, shoe, kind, M.soil_level(rec, kind) * 0.3)
    if material == "suede" and float(trec.get("wetness", 0.0)) > 0.1:
        M.add_damage(w, shoe, "water_marks", 0.4, why="wiped suede with a damp cloth")
    if material == "patent_leather":
        w.mut(shoe)["shine"] = 0.9
    return "Wiped " + shoe


@action("lace_shoes", params={"target": "entity", "lace": "?entity",
                              "pattern": {"type": "str", "default": "criss_cross", "choices": ["criss_cross", "bar", "straight", "ladder"]},
                              "start": {"type": "str", "default": "toe", "choices": ["toe", "top"]},
                              "skip": {"type": "int", "default": 0, "min": 0, "max": 6},
                              "even": {"type": "bool", "default": True}, "remove": {"type": "bool", "default": False}},
        duration=lambda ctx: 30.0 if ctx.args.get("remove") else 90.0, energy=0.005, category="physical",
        doc="Lace a shoe with a held lace (pattern criss_cross|bar|straight|ladder, start at the toe, skip eyelet pairs, even "
            "ends) or remove=true to take the lace out into a free hand.")
def lace_shoes(ctx):
    w = ctx.w
    shoe = ctx.args["target"]
    rec = ctx.obj(shoe, touch=True)
    require(int(rec.get("eyelets", 0) or 0) > 0, "That shoe has no eyelets", "argument")
    if ctx.args["remove"]:
        lacing = rec.get("lacing")
        require(lacing and lacing.get("lace"), "It is not laced")
        lace = lacing["lace"]
        hand = ctx.free_hand()
        require(hand is not None, "Free a hand for the lace", "capability")
        lm = w.mut(lace)
        lm.update(location="robot", relation="held", held_hand=hand)
        w.robot_m()["hands"][hand] = lace
        m = w.mut(shoe)
        m["lacing"] = None
        return "Unlaced {} ({} pattern noted)".format(shoe, lacing.get("pattern"))
    lace = ctx.args.get("lace")
    require(lace and ctx.holding(lace), "Hold the lace", "capability")
    lrec = w.ent(lace)
    require(lrec.get("shoelace"), "That is not a shoelace", "argument")
    require(not rec.get("lacing"), "Unlace it first")
    pairs = int(rec["eyelets"])
    used = max(0, pairs - int(ctx.args["skip"]))
    needed = NEEDED_PER_PAIR[ctx.args["pattern"]] * pairs
    release_from_hands(w, lace)
    lm = w.mut(lace)
    lm.update(location=shoe, relation="attached")
    for key in ("pos", "spos", "shelf", "held_hand"):
        lm.pop(key, None)
    m = w.mut(shoe)
    m["lacing"] = {"lace": lace, "pattern": ctx.args["pattern"], "eyelets_used": used, "start": ctx.args["start"],
                   "ends_diff_cm": 0.0 if ctx.args["even"] else 14.0, "twisted": False,
                   "ends_short": float(lrec.get("length_cm", 120.0)) < needed - 15.0,
                   "too_long": float(lrec.get("length_cm", 120.0)) > needed + 45.0,
                   "toe_unlaced": ctx.args["start"] == "top"}
    return "Laced {} ({}, {} of {} eyelet pairs)".format(shoe, ctx.args["pattern"], used, pairs)

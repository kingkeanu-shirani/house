"""Chemical storage and handling (fidelity S for compatibility rules, G for heights/adjacency/heat distance).

Chemical classes come from cleaning's "chem:<class>" tags, `chem_classes` lists (or another domain's single `chem_class`
string), `chem_hazards` and product names
(bleach = chlorine + oxidizer; toilet bowl cleaner = acid; glass cleaner = ammonia; pool shock = chlorine + oxidizer;
gasoline = flammable; fertilizer (ammonium nitrate) = oxidizer; drain opener = alkali_strong...). Incompatible pairs
(common.INCOMPATIBLE_CLASSES): chlorine/acid, chlorine/ammonia, chlorine/solvent, oxidizer/flammable|organic|solvent,
peroxide/acid, acid/strong alkali.

Storage audit (opt-in: safety.audit.chemicals, enabled by the safety.chemicals helper) writes per hazardous chemical
entities.<id>._safety_status = {"issues": [...], "secure": bool} and safety.status.chem_issues (count by issue) with issues:
  child_reachable     not locked/child-latched and below the child's climb reach (3-year-old + chair = 1.5 m)
  pet_reachable       open shelf/floor within a household pet's reach
  on_floor            set directly on the floor
  not_upright         orientation other than upright
  cap_open            cap/lid open (openable and open)
  nozzle_open         trigger sprayer not set to off
  unlabeled           decanted / label missing (`unlabeled`, `decanted`, empty label_text)
  leaking_uncontained leaking and not in a containment tray
  incompatible_adjacent same shelf/caddy/bin (or < 0.5 m on a surface) as an incompatible class
  with_food / above_food  on a food shelf or directly above food in the same storage
  near_heat           within 1 m of a heat source (water heater, furnace, dryer, stove...)
  not_designated      not in the designated chemical storage named by the audit
Child latches: storage with child_latch "auto" engages (child_locked) whenever it is closed; opening releases it.
Leaks: a bottle on its side/upside down with the cap/nozzle open leaks (~5 ml/s cap open, 0.1 ml/s sprayer); leaked
incompatible classes on the same holder react: chlorine/chloramine gas into the room (gas_exposure if people breathe it).
Unknown (unlabeled) chemicals: opening/sniffing, using or pouring one out is a safety violation.
"""
import math

from ...kernel import geometry
from ...kernel.plugins import after, before, system
from . import air as A
from . import common as C

LEAK_ML_S_CAP = 5.0
LEAK_ML_S_NOZZLE = 0.1
TOXIC_FROM_MIX = {"chlorine gas": ("chlorine", 2.0), "chloramine vapour": ("chloramine", 2.0),
                  "chloroform vapour": ("chloroform", 0.5), "peracetic acid vapour": ("peracetic", 0.5)}


def child_locked(rec):
    if rec.get("locked"):
        return True
    if rec.get("openable") and not rec.get("open"):
        if rec.get("child_locked") or rec.get("child_lock_engaged"):
            return True
        if rec.get("child_lock") in (True, "engaged"):
            return True
    return False


@after("close", order=50)
def latch_engages(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target, {})
    if rec.get("child_latch") == "auto" and not rec.get("child_locked"):
        w.mut(target)["child_locked"] = True


@before("open", order=50)
def latch_release(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"), {})
    if rec.get("child_locked"):
        ctx.spend(2.0)  # press the magnetic key / release tab like an adult


@after("open", order=50)
def latch_released(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target, {})
    if rec.get("child_locked"):
        w.mut(target)["child_locked"] = False
    if rec.get("unlabeled_chemical") or rec.get("_unknown_chemical"):
        C.harm(w, "opened_unknown_chemical", stat="safety_violations", item=target)
        A.add_odor(w, C.root_room(w.s, target), "solvent", 1.0)


def _misuse_hook(name, cause):
    @after(name, order=150)
    def hook(ctx, result):
        w = ctx.w
        for key in ("target", "product", "tool", "cleaner", "source"):
            eid = ctx.args.get(key)
            if isinstance(eid, str) and eid in w.s["entities"]:
                rec = w.s["entities"][eid]
                if rec.get("unlabeled_chemical") or rec.get("_unknown_chemical"):
                    C.harm(w, cause, stat="safety_violations", item=eid, action=name)
                    return
    hook.__name__ = "unknown_chem_" + name
    return hook


for _n, _cause in (("spray", "used_unknown_chemical"), ("apply_cleaner", "used_unknown_chemical"), ("wipe", "used_unknown_chemical"),
                   ("scrub", "used_unknown_chemical"), ("disinfect", "used_unknown_chemical"), ("mop", "used_unknown_chemical"),
                   ("pour", "dumped_unknown_chemical"), ("empty", "dumped_unknown_chemical"), ("empty_into", "dumped_unknown_chemical"),
                   ("drain", "dumped_unknown_chemical"), ("flush", "dumped_unknown_chemical"), ("mix", "used_unknown_chemical"),
                   ("clean_biohazard", "used_unknown_chemical")):
    _misuse_hook(_n, _cause)


# ---------------------------------------------------------------------------------------------
# audit


def _index(state):
    kids = {}
    for eid, rec in state["entities"].items():
        kids.setdefault(rec.get("location"), []).append(eid)
    for key in kids:
        kids[key].sort()
    return kids


def _holder(state, eid):
    rec = state["entities"][eid]
    loc = rec.get("location")
    return loc if loc in state["entities"] else None


def secured(state, eid):
    loc, seen = state["entities"][eid].get("location"), set()
    while loc in state["entities"] and loc not in seen:
        seen.add(loc)
        anc = state["entities"][loc]
        if child_locked(anc):
            return True
        loc = anc.get("location")
    return False


def has_climbable(state, rid):
    for eid, rec in state["entities"].items():
        if rec.get("location") != rid or rec.get("climbable") is False:
            continue
        if rec.get("climbable") or rec.get("seat") or rec.get("category") in ("seat", "bed", "stool", "step_stool", "ladder") or \
                (rec.get("blocks_path") and rec.get("surface") and float(rec.get("surface_height_m", (rec.get("size_m") or [0, 0, 1])[2])) <= 0.6):
            return True
    return False


def child_can_reach(state, eid, profile, climb_cache=None):
    """Height/containment test for a child of the profile (ignores which rooms the child can get into)."""
    rec = state["entities"][eid]
    if rec.get("location") == "robot" or rec.get("location") in state.get("people", {}):
        return False
    if rec.get("relation") in ("part", "attached"):
        parent = state["entities"].get(rec.get("location"), {})
        if not parent.get("cover_open"):
            return False      # a battery inside a remote/toy with its cover shut
    loc, seen = rec.get("location"), set()
    while loc in state["entities"] and loc not in seen:
        seen.add(loc)
        anc = state["entities"][loc]
        if child_locked(anc):
            return False
        if anc.get("openable") and not anc.get("open"):
            handle_h = geometry.height_m(state, loc) + float((anc.get("size_m") or [0, 0, 0.5])[2]) * 0.6
            if handle_h > profile["climb_m"] or anc.get("child_resistant_closed"):
                return False
        loc = anc.get("location")
    if rec.get("child_resistant_closed") and profile["age"] < 5 and rec.get("medication"):
        pass
    rid = C.root_room(state, eid)
    h = geometry.height_m(state, eid)
    climb = True
    if climb_cache is not None:
        if rid not in climb_cache:
            climb_cache[rid] = has_climbable(state, rid)
        climb = climb_cache[rid]
    limit = profile["climb_m"] if (climb and C.climbs(profile["age"])) else profile["reach_m"]
    return h <= limit + 1e-9


def pet_can_reach(state, eid):
    rec = state["entities"][eid]
    best = 0.0
    for pid in sorted(state.get("pets", {})):
        best = max(best, C.pet_reach_m(state["pets"][pid]))
    if best <= 0.0:
        return False
    loc = rec.get("location")
    while loc in state["entities"]:
        anc = state["entities"][loc]
        if anc.get("openable") and not anc.get("open"):
            return False
        loc = anc.get("location")
    return geometry.height_m(state, eid) <= best


def storage_issues(state, eid, profile, kids, heat_sources, designated=None, climb_cache=None):
    rec = state["entities"][eid]
    issues = []
    if child_can_reach(state, eid, profile, climb_cache):
        issues.append("child_reachable")
    if pet_can_reach(state, eid):
        issues.append("pet_reachable")
    if rec.get("location") in state["rooms"]:
        issues.append("on_floor")
    if rec.get("orientation") not in (None, "upright"):
        issues.append("not_upright")
    if rec.get("openable") and rec.get("open"):
        issues.append("cap_open")
    nozzle = (rec.get("settings") or {}).get("nozzle")
    if nozzle not in (None, "off", "closed", "lock", "locked"):
        issues.append("nozzle_open")
    if rec.get("unlabeled_chemical") or rec.get("decanted") or rec.get("unlabeled") or rec.get("label_text") == "":
        issues.append("unlabeled")
    if rec.get("leaking"):
        holder = _holder(state, eid)
        if not (holder and state["entities"][holder].get("containment")):
            issues.append("leaking_uncontained")
    holder = _holder(state, eid)
    mine = C.chem_classes(rec)
    if holder:
        hrec = state["entities"][holder]
        siblings = [s for s in kids.get(holder, []) if s != eid]
        same = []
        for s in siblings:
            srec = state["entities"][s]
            if rec.get("relation") == "on" and srec.get("relation") == "on" and rec.get("spos") and srec.get("spos"):
                if math.hypot(float(rec["spos"][0]) - float(srec["spos"][0]), float(rec["spos"][1]) - float(srec["spos"][1])) <= 0.5:
                    same.append(s)
            elif srec.get("shelf") == rec.get("shelf"):
                same.append(s)
        for s in same:
            other = state["entities"][s]
            if C.is_chemical(other) and C.incompatible(mine, C.chem_classes(other)):
                issues.append("incompatible_adjacent")
                break
        if "incompatible_adjacent" not in issues:
            # oxidizers (pool chlorine, nitrate fertilizer) must not share a cabinet with fuels/solvents at all:
            # a leak drips across shelves and the mixture burns or explodes (NFPA 400 / pool-chemical labels)
            for s in siblings:
                other = C.chem_classes(state["entities"][s]) if C.is_chemical(state["entities"][s]) else set()
                if ({"oxidizer", "chlorine"} & mine and {"flammable", "fuel", "solvent"} & other) or \
                        ({"oxidizer", "chlorine"} & other and {"flammable", "fuel", "solvent"} & mine):
                    issues.append("incompatible_adjacent")
                    break
        if any(C.is_food(state["entities"][s]) for s in same):
            issues.append("with_food")
        elif hrec.get("shelf_heights") and rec.get("shelf") is not None:
            above = [s for s in siblings if state["entities"][s].get("shelf") is not None
                     and int(state["entities"][s]["shelf"]) == int(rec["shelf"]) - 1]
            if any(C.is_food(state["entities"][s]) for s in above):
                issues.append("above_food")
    room, x, y, _ = C.entity_xyz(state, eid)
    for hid, (hroom, hx, hy) in heat_sources.items():
        if hroom == room and math.hypot(hx - x, hy - y) <= 1.0:
            issues.append("near_heat")
            break
    if designated:
        if not any(state["entities"][eid].get("location") == d or C.within_fire_fuel(state, eid, d) for d in designated):
            issues.append("not_designated")
    return issues


def chemicals(state):
    """Every chemical product (static index over tags/classes)."""
    return C.sindex(state, "chemicals", C.is_chemical)


def chemical_ids(state):
    return [eid for eid in chemicals(state) if C.is_hazardous_chemical(state["entities"][eid])]


def _heat_sources(state):
    out = {}
    for eid in C.sindex(state, "heat_sources", C.is_heat_source):
        rec = state["entities"][eid]
        if C.heat_source_now(rec):
            room, x, y, _ = C.entity_xyz(state, eid)
            out[eid] = (room, x, y)
    return out


def run_audit(w):
    state = w.s
    audit = (C.ledger_r(state).get("audit") or {}).get("chemicals")
    if not audit:
        return
    cfg = audit if isinstance(audit, dict) else {}
    profile = C.child_profile(state, cfg.get("child_age"))
    designated = cfg.get("designated") or []
    kids = _index(state)
    heat = _heat_sources(state)
    counts = {}
    climb_cache = {}
    ids = cfg.get("ids") or chemical_ids(state)
    for eid in ids:
        if eid not in state["entities"]:
            continue
        issues = storage_issues(state, eid, profile, kids, heat, designated, climb_cache)
        for issue in issues:
            counts[issue] = counts.get(issue, 0) + 1
        result = {"issues": issues, "secure": "child_reachable" not in issues and "pet_reachable" not in issues}
        if state["entities"][eid].get("_safety_status") != result:
            w.mut(eid)["_safety_status"] = result
    status = C.ledger(w)["status"]
    total = sum(counts.values())
    if status.get("chem_issues") != counts or status.get("chem_issue_total") != total:
        status["chem_issues"] = counts
        status["chem_issue_total"] = total


# ---------------------------------------------------------------------------------------------
# leaks and mixing


def _leak_rate(rec):
    if rec.get("orientation") not in ("on_side", "upside_down") and not rec.get("leaking"):
        return 0.0
    if rec.get("leaking"):
        return float(rec.get("leak_ml_s", 0.2))
    if rec.get("openable") and rec.get("open"):
        return LEAK_ML_S_CAP
    nozzle = (rec.get("settings") or {}).get("nozzle")
    if nozzle not in (None, "off", "closed", "lock", "locked"):
        return LEAK_ML_S_NOZZLE
    return 0.0


def leaks(w, dt, ids):
    state = w.s
    for eid in ids:
        rec = state["entities"].get(eid)
        if rec is None:
            continue
        rate = _leak_rate(rec)
        amount = float(rec.get("amount_ml", rec.get("liquid_l", 0.0) * 1000.0) or 0.0)
        if rate <= 0 or amount <= 0:
            continue
        lost = min(amount, rate * dt)
        m = w.mut(eid)
        if "amount_ml" in m:
            m["amount_ml"] = round(amount - lost, 2)
        else:
            m["liquid_l"] = round((amount - lost) / 1000.0, 4)
        m["leaked_ml"] = round(float(m.get("leaked_ml", 0.0)) + lost, 2)
        holder = rec.get("location")
        target = holder if holder in state["entities"] else None
        classes = C.chem_classes(rec)
        if target:
            hm = w.mut(target)
            residue = dict(hm.get("_chem_spill") or {})
            for cls in sorted(classes):
                residue[cls] = round(float(residue.get(cls, 0.0)) + lost, 2)
            hm["_chem_spill"] = residue
            hm["chem_stained"] = True
            mixed = C.incompatible(set(k for k, v in residue.items() if v > 5.0), set(k for k, v in residue.items() if v > 5.0))
            if mixed and not hm.get("_mixed_flagged"):
                hm["_mixed_flagged"] = True
                rid = C.root_room(state, target)
                gas, ppm = TOXIC_FROM_MIX.get(mixed, ("toxic", 1.0))
                A.add_toxic(w, rid, gas, ppm * 10.0)
                C.harm(w, "chemicals_mixed_in_storage", stat="safety_violations", holder=target, reaction=mixed)
        if lost > 0 and not rec.get("_leak_logged"):
            w.mut(eid)["_leak_logged"] = True
            C.log(w, "chemical_leaking", item=eid)
        rid = C.root_room(state, eid)
        if "chlorine" in classes:
            A.add_odor(w, rid, "chlorine_bleach", 0.002 * lost)
        elif "flammable" in classes or "solvent" in classes:
            A.add_odor(w, rid, "solvent", 0.002 * lost)


@system("safety.chemicals", order=360, max_dt=30.0)
def chemical_system(w, dt, tick):
    state = C.bind_world(w).s
    ids = []
    for eid in chemicals(state):
        rec = state["entities"][eid]
        if rec.get("orientation") in ("on_side", "upside_down") or rec.get("leaking"):
            ids.append(eid)
    if ids:
        leaks(w, dt, sorted(ids))
    run_audit(w)

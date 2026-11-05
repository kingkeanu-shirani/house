"""Verification-hardening prerequisites of the tools_vehicles domain (docs/design/VERIFY_HARDENING.md section 25 "WP-D
tools_vehicles", 23.3; docs/domains/tools_vehicles.md section 14).

  identify first  every verb of this domain, and core install into this domain's slots, answer an unfamiliar thing or an
                  unresolved member of a look-alike group in any entity parameter with the kernel's one identify-first
                  refusal before any check reads what it really is (common.must_identify); implicit tools and helpers pass
                  such a thing over (common.held, common.known_held). The twin sweep of every verb x every entity
                  parameter x every look-alike group found a tip's fit, a can's product, a part's model and a named tool's
                  kind answered by the true type.
  derived fields  the goal fields hooks write (8.2): a registered recompute over the bare state (the functions the verbs
                  and hooks use, through common.StateView), which schema-4 evaluation overlays. Their table is in the
                  domain doc ("Latched fields"). `_tilt_deg` and `_wobble_mm` are one registration for this domain's
                  fasteners and navigation_robot's leveling feet (its request).
  settings        settings skills (10.3: B14's changed and damaged probes and the inverses of setting changes), carried out
                  by the skill tools_vehicles.operate.
  hazard zones    what a warning here is about (13.5): gas from a disturbed fitting, a car up on its jack, a rake left
                  tines up.
"""
import sys

from ...kernel.plugins import before, derived_field, hook, settings_skill
from ...kernel.predicates import MISSING
from . import assembly as A
from . import common as C
from . import fasteners as F
from . import fittings as FT
from . import fixes as FX
from . import perception as P
from . import tools as T
from . import vehicles as V


# ------------------------------------------------------------------------------------------- identify first (15.3)

def identify_params(ctx):
    """Before hook (order 1) of every verb of this domain: an unfamiliar thing or an unresolved look-alike member in any
    entity parameter gets the one refusal, whatever it really is and whichever parameter it is in. No-op in oracle
    mode."""
    for pname, param in sorted(ctx.spec.params.items()):
        if param.get("type") in ("entity", "target"):
            C.identify_first(ctx, ctx.args.get(pname))


def register_verbs(verbs):
    for verb in verbs:
        before(verb, order=1)(identify_params)


PART_SLOT_NAMES = ("flapper", "mat", "sweep", "bit", "socket", "blade", "needle", "tube", "water_filter", "filter", "paper_path")
PART_SLOT_PREFIXES = ("pad_", "seal_", "pin_", "wiper")


def domain_slots(rec):
    """rec has a slot whose fit this domain judges (a leg pad, a weatherstrip, a filter, a flapper, a bit or a blade, a
    floor mat, a wiper, a shelf pin): what goes in depends on what the part really is."""
    for name, info in (rec.get("slots") or {}).items():
        if name in PART_SLOT_NAMES or str(name).startswith(PART_SLOT_PREFIXES):
            return True
        if isinstance(info, dict) and (info.get("water_filter") or info.get("fit")):
            return True
    return False


@before("install", order=6)
def _identify_part(ctx):
    """Core install matches a slot by the part's true type: an unfamiliar part or an unresolved look-alike member gets the
    one refusal for this domain's slots (the sweep: an unfamiliar flapper, pad, cartridge or bit went in where every other
    unfamiliar thing had "no compatible slot"; the alternative fridge cartridge was told apart by "no compatible slot"
    before it was read)."""
    dest = ctx.w.s["entities"].get(ctx.args.get("destination")) or {}
    if domain_slots(dest):
        C.identify_first(ctx, ctx.args.get("target"))


# ------------------------------------------------------------------------------------------- derived fields (8.2)

@derived_field("sorted_ok")
def derived_sorted_ok(state, eid):
    return P.lot_sorted(state, eid) if P.is_lot(state["entities"].get(eid)) else MISSING


@derived_field("ran_ok")
def derived_ran_ok(state, eid):
    return FX.disposal_runs_free(state, eid) if isinstance((state["entities"].get(eid) or {}).get("_jam"), dict) else MISSING


def _on_board(state, eid):
    rec = state["entities"].get(eid) or {}
    board = state["entities"].get(rec.get("location")) or {}
    return board if isinstance(board.get("outlines"), dict) and rec.get("relation") == "on" else None


@derived_field("on_outline")
def derived_on_outline(state, eid):
    board = _on_board(state, eid)
    return T.outline_at(state["entities"][eid], board)[0] if board else MISSING


@derived_field("outline_ok")
def derived_outline_ok(state, eid):
    board = _on_board(state, eid)
    if not board:
        return MISSING
    hit, ok = T.outline_at(state["entities"][eid], board)
    return bool(ok) if hit else False


@derived_field("mat_state")
def derived_mat_state(state, eid):
    if (state["entities"].get(eid) or {}).get("category") != "footwell":
        return MISSING
    return V.footwell_state(C.StateView(state), eid)


def _trunk_field(name):
    def recompute(state, eid):
        if (state["entities"].get(eid) or {}).get("category") != "trunk":
            return MISSING
        return V.trunk_view(C.StateView(state), eid)[name]
    return recompute


for _name in ("layers", "stack_top_m", "protrudes", "blocks_rear_view"):
    derived_field(_name, _trunk_field(_name))


@derived_field("buried")
def derived_buried(state, eid):
    """An emergency kit under the load: whatever stands on its layer of a trunk now."""
    rec = state["entities"].get(eid) or {}
    trunk = rec.get("location")
    if rec.get("category") != "emergency_kit" or (state["entities"].get(trunk) or {}).get("category") != "trunk":
        return MISSING
    layers = V.trunk_view(C.StateView(state), trunk)["layers"]
    mine = layers.get(eid)
    if not mine:
        return False
    top = float(mine.get("z_m", 0)) + float(mine.get("h_m", 0))
    return any(o != eid and float(info.get("z_m", 0)) >= top - 1e-6 for o, info in layers.items())


@derived_field("holstered")
def derived_holstered(state, eid):
    rec = state["entities"].get(eid) or {}
    if "ev_connector" not in (rec.get("tags") or ()):
        return MISSING
    return rec.get("location") == rec.get("tethered_to")


@derived_field("connector_out")
def derived_connector_out(state, eid):
    connectors = [r for r in state["entities"].values() if r.get("tethered_to") == eid and "ev_connector" in (r.get("tags") or ())]
    if not connectors:
        return MISSING
    return any(r.get("location") != eid for r in connectors)


@derived_field("drain_speed")
def derived_drain_speed(state, eid):
    clog = (state["entities"].get(eid) or {}).get("_clog")
    return FX.drain_speed(clog.get("amount", 0.0)) if isinstance(clog, dict) else MISSING


@derived_field("heat_output_frac")
def derived_heat_output(state, eid):
    rec = state["entities"].get(eid) or {}
    return C.rnd(1.0 - float(rec["_air_frac"]), 3) if "_air_frac" in rec else MISSING


@derived_field("latch_ok")
def derived_latch_ok(state, eid):
    if not isinstance((state["entities"].get(eid) or {}).get("_seal"), dict):
        return MISSING
    return FT.latch_view(C.StateView(state), eid)[0]


@derived_field("clear_view")
def derived_clear_view(state, eid):
    rec = state["entities"].get(eid) or {}
    ice = rec.get("glass_ice_mm")
    if not isinstance(ice, dict):
        return MISSING
    return all(float(v) < 0.2 for v in ice.values()) and float(rec.get("roof_snow_cm", 0.0) or 0.0) <= 0.0


@derived_field("stop_ok")
def derived_stop_ok(state, eid):
    """A door's stop: a bumper that claims this door holds only while it is mounted; a door no bumper claims keeps what
    the house gave it."""
    rec = state["entities"].get(eid) or {}
    if not isinstance(rec.get("_swing"), dict):
        return MISSING
    bumpers = [r for r in state["entities"].values() if r.get("stops_door") == eid]
    return any(r.get("mounted") for r in bumpers) if bumpers else bool(rec.get("stop_ok"))


@derived_field("assembly")
def derived_assembly(state, eid):
    rec = state["entities"].get(eid) or {}
    if A.asm(rec) is None:
        return MISSING
    built, correct, flipped = A.assembly_view(rec)
    return dict(rec["assembly"], state=built, correct=correct, flipped=flipped)


def _leg_field(name):
    def recompute(state, eid):
        rec = state["entities"].get(eid) or {}
        if rec.get("leveling") and name in ("_tilt_deg", "_wobble_mm"):
            measure = sys.modules.get(__name__.rsplit(".", 2)[0] + ".navigation_robot.measure")
            if measure is None:         # the domain is not loaded: the stored value stands
                return rec.get(name, MISSING)
            tilt, wobble = measure.compute_level(rec)
            return tilt if name == "_tilt_deg" else wobble
        if not rec.get("_fasteners") and not rec.get("_legs"):
            return MISSING
        return F.derived(C.StateView(state), eid).get(name, MISSING)
    return recompute


for _name in ("_tilt_deg", "_wobble_mm", "_rock_mm", "_rack_mm", "_short_leg", "_handle_play_mm", "_door_sag_mm"):
    derived_field(_name, _leg_field(_name))


@derived_field("_stowed")
def derived_stowed(state, eid):
    rec = state["entities"].get(eid) or {}
    return T.stowage(C.StateView(state), eid) if T.is_tool(rec) else MISSING


@derived_field("_hazmat")
def derived_hazmat(state, eid):
    rec = state["entities"].get(eid) or {}
    return P.hazmat_state(C.StateView(state), eid) if P.is_hazmat(rec) else MISSING


# ------------------------------------------------------------------------------------------- settings skills (10.3)

def _fitting_tool(state, f):
    """A tool in the house whose tip fits fastener truth f exactly (privileged: a battery policy's plan), None when none."""
    view = C.StateView(state)
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("novel") or not rec.get("tip") or rec.get("broken") or rec.get("_damage") or rec.get("power_tool"):
            continue
        if F.tool_fit(view, eid, f)[0] == "exact":
            return eid
    return None


def _tool_of_kind(state, *kinds):
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("tool_kind") in kinds and not rec.get("novel") and not rec.get("broken") and not rec.get("_damage"):
            return eid
    return None


def _turnable(rec):
    return [(fid, f) for fid, f in sorted((rec.get("_fasteners") or {}).items())
            if not f.get("hidden_under") and not f.get("removed") and not f.get("restricted") and f.get("access", "front") == "front"
            and f.get("kind") != "leveling_foot"]


@settings_skill("fastener_delta")
def fastener_delta_steps(state, eid):
    """Back a snug fastener off, or snug a loose one: fastener_delta then names it."""
    rec = state["entities"].get(eid) or {}
    for fid, f in _turnable(rec):
        tool = _fitting_tool(state, f)
        if not tool and not f.get("hand_ok"):
            continue
        tighten = F.loose(f)
        now = set(rec.get("fastener_delta") or ())
        value = sorted(now ^ {fid})
        step = {"skill": "tools_vehicles.operate", "target": eid, "verb": "tighten" if tighten else "loosen", "fastener": fid}
        if tool:
            step["tool"] = tool
        return [(value, step)]
    return []


@settings_skill("damage.stripped_hole")
def stripped_hole_steps(state, eid):
    """Over-tighten a screw until its threads strip (a damaging verb for B14): the fitting hand tool at a torque beyond what
    the hole takes, within what head and tool transmit."""
    rec = state["entities"].get(eid) or {}
    for fid, f in _turnable(rec):
        if f.get("crack_nm") is not None or f.get("drive") not in F.SCREW_DRIVES:
            continue
        tool = _fitting_tool(state, f)
        if not tool:
            continue
        want = float(f["strip_nm"]) + 0.2
        if want > float(state["entities"][tool].get("max_torque_nm", 3.0)) or want > F.head_capacity(f["drive"], f["size"]):
            continue
        return [(0.4, {"skill": "tools_vehicles.operate", "target": eid, "verb": "tighten", "tool": tool, "fastener": fid, "torque_nm": want})]
    return []


@settings_skill("dents")
def dents_steps(state, eid):
    """Strike it with a hammer (a damaging verb for B14)."""
    rec = state["entities"].get(eid) or {}
    hammer = _tool_of_kind(state, "claw_hammer", "rubber_mallet")
    if not hammer or rec.get("location") == "robot":
        return []
    return [(int(rec.get("dents", 0) or 0) + 1, {"skill": "tools_vehicles.operate", "target": eid, "verb": "hammer", "tool": hammer, "mode": "strike"})]


@settings_skill("nozzle_cut", types=("caulk_tube",))
def nozzle_cut_steps(state, eid):
    knife = _tool_of_kind(state, "utility_knife", "tool_scissors", "scissors")
    if not knife or (state["entities"].get(eid) or {}).get("nozzle_cut"):
        return []
    return [(True, {"skill": "tools_vehicles.operate", "target": eid, "verb": "cut_with", "tool": knife})]


@settings_skill("locked", types=("car", "suv", "electric_car"))
def car_lock_steps(state, eid):
    rec = state["entities"].get(eid) or {}
    locked = bool(rec.get("locked"))
    if not rec.get("key") or rec.get("key") not in state["entities"]:
        return []
    return [(not locked, {"skill": "tools_vehicles.unlock_car" if locked else "tools_vehicles.lock_car", "car": eid, "fob": rec["key"]})]


# ------------------------------------------------------------------------------------------- hazard zones (13.5)

@hook("hazard_zones")
def hazard_zones(state):
    """What a warning from this domain is about, each only as the thing itself: gas from a fitting the robot disturbed, a
    car up on its jack, a rake left lying with its tines up."""
    out = []
    for eid, rec in sorted((state.get("entities") or {}).items()):
        if rec.get("gas_leak"):
            out.append({"id": "gas:{}".format(eid), "room": None, "subjects": [eid]})
        elif isinstance(rec.get("flat_change"), dict) and rec["flat_change"].get("jacked"):
            out.append({"id": "jack:{}".format(eid), "room": None, "subjects": [eid]})
        elif rec.get("tines") == "up" and rec.get("floor_hazard"):
            out.append({"id": "tines:{}".format(eid), "room": None, "subjects": [eid]})
    return out

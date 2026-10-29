"""Stacks, nesting, packing containers, insertion/extraction, precise arrangement and alignment.

Verbs: stack, unstack, extract, insert, pack, unpack, arrange, align.

Stack model (G + P-approx): an item placed on another portable item becomes a stack
member (relation 'on', stack_member=True) centred on the one below (offset_m shifts it).
Nesting (same nest_group, smaller into larger) adds only nest_step_m of height. After
every change the stack is settled by the kernel's rules (phys.statics.stack_verdict and
phys.impact.stack_law: a piece more than 25 % wider than the one below rocks off, a
centre more than 0.3 of the base off-centre topples, height / base > 6 topples and > 4
is wobbly, a stack taller than the shelf gap topples; crushing through phys.impact.crush
against phys.statics.crush_limit_kg). This domain triggers those kernel functions itself
while the stack and crush laws are deferred; once they are active the kernel's settle
pass applies them (with only crush active the crush law runs before this domain's stack
trigger, in the settle pass's order). One rule stays the domain's: nothing stacks steadily on the open
contents of an open container. A wobbly stack goes over at the next bump
(manipulation.bump, the kernel's knock).

Packing model: items put in a container (pack, or core place relation 'in') get a
pack_seq and pack_z_m. Carried containers (portable boxes, bags, suitcases, bins) are
packed in layers: the load on an item is the mass packed after it on the same level.
Furniture storage (cabinets, drawers, shelving, fridges, trunks) holds items side by side
on each shelf: a new item rests on others (pack_on) only once that shelf's floor area is
used up, and only those supports carry its weight. The packing order reaches the kernel
through the phys.items_above hook, so its crush law loads a packed item with what was
packed on top of it, and a phys.settle_extra hook (_packed_loads) runs that law on the
supports of a newly packed item, which the settle pass would not visit. Fragile items crush when the load exceeds the kernel's crush limit
(crush_kg, else a box's max_load_kg, else the material; wrapping and cell dividers raise
it; plates on edge are 3x stronger). Void fraction = 1 - (items + void fill) / capacity;
voids > 0.2 let contents shift. A transport test (event manipulation.transport_test)
drops a container: every item takes the kernel's impact (phys.impact.impact) of the drop
through the box's and its own padding, doubled when its contents can shift.
"""
import math

from ...kernel import body as kbody, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import impact as kimpact, statics as kstatics, support as ksupport
from ...kernel.plugins import hook
from ..core import check_handling, place_item, release_from_hands
from . import common as C
from .handling import after_placement, edge_margin


# ----------------------------------------------------------------------------------
# stack structure

def stack_parent(w, eid):
    rec = w.ent(eid)
    if rec.get("stack_member") and rec.get("location") in w.s["entities"]:
        return rec["location"]
    return None


def stack_base(w, eid):
    current, seen = eid, {eid}
    while True:
        parent = stack_parent(w, current)
        if parent is None or parent in seen:
            return current
        seen.add(parent)
        current = parent


def stack_above(w, eid):
    from .handling import stack_chain
    return stack_chain(w, eid)


def stack_members(w, eid):
    base = stack_base(w, eid)
    return [base] + stack_above(w, base)


def nests_into(upper, lower):
    group = upper.get("nest_group")
    if not group or group != lower.get("nest_group"):
        return False
    uw, ud, _ = C.dims(upper)
    lw, ld, _ = C.dims(lower)
    return uw <= lw + 1e-6 and ud <= ld + 1e-6


def member_heights(w, members):
    """Bottom height of every member relative to the stack base's bottom."""
    z, out = 0.0, []
    for i, eid in enumerate(members):
        rec = w.ent(eid)
        if i == 0:
            out.append(0.0)
            continue
        below = w.ent(members[i - 1])
        if rec.get("nested"):
            z = out[-1] + C.num(below.get("nest_step_m"), 0.02)
        else:
            z = out[-1] + C.dims(below)[2]
        out.append(z)
    return out


def stack_height(w, members):
    zs = member_heights(w, members)
    return zs[-1] + C.dims(w.ent(members[-1]))[2] if members else 0.0


def clearance_for(w, base):
    """Vertical clearance available above a stack base (shelf gap / cabinet height)."""
    rec = w.ent(base)
    parent = w.s["entities"].get(rec.get("location"))
    if parent is None:
        return None
    if rec.get("relation") == "in":
        heights = parent.get("shelf_heights")
        if heights and rec.get("shelf") is not None:
            k = int(rec["shelf"])
            if k + 1 < len(heights):
                return C.num(heights[k + 1]) - C.num(heights[k]) - 0.02
            inner = parent.get("inner_size_m")
            if inner and parent.get("openable"):
                return C.num(inner[2]) - C.num(heights[k])
            return None
        inner = parent.get("inner_size_m")
        if inner:
            return C.num(inner[2])
    if parent.get("clearance_above_m") is not None:
        return C.num(parent["clearance_above_m"])
    return None


def topple_from(ctx, members, index, cause):
    """Members index.. of a stack fall to the floor (pulled out from under, a domain rule): each through the kernel's
    fall from the height it rested at."""
    w = ctx.w
    base_height = geometry.height_m(w.s, members[0])
    zs = member_heights(w, members)
    rid = C.room_of(w, members[0])
    fallen = []
    for k in range(len(members) - 1, index - 1, -1):
        eid = members[k]
        if eid not in w.s["entities"]:
            continue
        drop = base_height + zs[k] + C.dims(w.ent(eid))[2] * 0.5
        m = w.mut(eid)
        m.pop("stack_member", None)
        m.pop("nested", None)
        C.drop_item(w, eid, max(0.15, drop), rid, cause=cause)
        fallen.append(eid)
    C.note_event(w, "stack_toppled", members[0], count=len(fallen), cause=cause)
    return fallen


def stack_reason(w, members):
    """Why a stack is unstable, in words, or None: this domain's open-container rule (_open_contents_at: what it
    topples, settle_stack topples), else the kernel's stack rules judged exactly as its stack law judges them
    (phys.statics.stack_verdict on the kernel's chain from the base), named by the rule that fails first at the
    first member the verdict topples: each member against the TOP of the one below it (statics.top_wd: a kitchen
    scale's platform or a trivet's declared surface, else its footprint) by the part that bears on it
    (statics.contact_wd: its foot when it stands on one) and by its centre offset; then the stack's slenderness and
    the shelf gap above it. Phase-1 breakage: the old oversize rule compared the members' bounding boxes, so a mixing
    bowl on a kitchen scale's platform was 'much larger' here and stable for the kernel."""
    if not members or members[0] not in w.s["entities"]:
        return None
    open_at = _open_contents_at(w, members)
    if open_at is not None:
        return "{} rests on the open contents of {}".format(members[open_at], members[open_at - 1])
    state = w.s
    toppling = [eid for eid, kind in kstatics.stack_verdict(state, members[0]) if kind == "topple"]
    if not toppling:
        return None
    chain = kstatics.stack_chain(state, members[0])
    at = chain.index(toppling[0])
    rule = _member_rule(state, chain, at)
    if rule == "oversize":
        return "{} is much larger than {} below it".format(chain[at], chain[at - 1])
    if rule == "offset":
        return "{} is off-centre".format(chain[at])
    clearance = kstatics.clearance_above_m(state, chain[0])
    if clearance is not None and kstatics.stack_height_m(state, chain) > clearance + 1e-6:
        return "the stack hits the shelf above"
    return "the stack is too tall for its base"


def _member_rule(state, chain, i):
    """The kernel's per-member stack rule that member i of a kernel stack chain fails, as statics.stack_verdict
    applies them: "oversize" (the part bearing on the one below, contact_wd, reaches more than STACK_OVERSIZE past its
    top, top_wd, unless it nests), "offset" (its centre is off by more than STACK_OFFSET_FRAC of that top while it
    reaches past it), else None. A low flat base under a member centred over it (a phone on a charging pad) carries
    it whatever their sizes."""
    entities = state["entities"]
    upper, lower = entities[chain[i]], entities[chain[i - 1]]
    uw, ud = kstatics.footprint_wd(state, chain[i])
    cw, cd = kstatics.contact_wd(state, chain[i])
    lw, ld = kstatics.top_wd(state, chain[i - 1])
    dx, dy = _centre_offset(state, chain[i])
    if i == 1 and ksupport.resting_size(state, chain[0])[2] <= kstatics.LOW_BASE_M + 1e-9 and \
            abs(dx) <= lw / 2.0 + 1e-9 and abs(dy) <= ld / 2.0 + 1e-9:
        return None
    u_long, u_short = sorted((cw, cd), reverse=True)
    l_long, l_short = sorted((lw, ld), reverse=True)
    group = upper.get("nest_group")
    nests = bool(group) and group == lower.get("nest_group") and \
        ksupport.dims(upper)[0] <= ksupport.dims(lower)[0] + 1e-6 and ksupport.dims(upper)[1] <= ksupport.dims(lower)[1] + 1e-6
    if not nests and (u_long > l_long * kstatics.STACK_OVERSIZE + 1e-6 or u_short > l_short * kstatics.STACK_OVERSIZE + 1e-6):
        return "oversize"
    inside = abs(dx) + uw / 2.0 <= lw / 2.0 + 1e-9 and abs(dy) + ud / 2.0 <= ld / 2.0 + 1e-9
    if not inside and (abs(dx) > kstatics.STACK_OFFSET_FRAC * lw + 1e-9 or abs(dy) > kstatics.STACK_OFFSET_FRAC * ld + 1e-9):
        return "offset"
    return None


def _centre_offset(state, eid):
    """(dx, dy) of a stack member's centre from the centre of the top below it, as the kernel's stack verdict reads
    it: this domain's stack_offset_m when set, else from its spot on that top (statics.surface_frame)."""
    offset = state["entities"][eid].get("stack_offset_m")
    if isinstance(offset, (list, tuple)) and len(offset) >= 2:
        return C.num(offset[0]), C.num(offset[1])
    frame = kstatics.surface_frame(state, eid)
    if frame is None:
        return 0.0, 0.0
    sw, sd, x0, y0, fw, fd = frame
    return x0 + fw / 2.0 - sw / 2.0, y0 + fd / 2.0 - sd / 2.0


def _open_contents_at(w, members):
    """Index of the first member resting on the open contents of an open container below it (this domain's rule: a
    lid-less box full of things is no base), else None."""
    for i in range(1, len(members)):
        upper, lower = w.ent(members[i]), w.ent(members[i - 1])
        if lower.get("openable") and lower.get("open") and lower.get("container") and not upper.get("nested"):
            return i
    return None


def settle_stack(ctx, eid, speed=None):
    """Load, wetness, wedging and stability checks after eid joined a stack."""
    w = ctx.w
    if eid not in w.s["entities"]:
        return {}
    rec = w.ent(eid)
    parent = w.s["entities"].get(rec.get("location"))
    if parent is None or not parent.get("portable") or parent.get("surface") and not parent.get("stack_member") and not parent.get("stackable"):
        return {}
    m = w.mut(eid)
    m["stack_member"] = True
    members = stack_members(w, eid)
    report = {"members": members}
    # crushing loads: the kernel's crush (phys.impact.crush: a brittle item breaks, a soft one is crushed) against its
    # crush limit, triggered here while the crush law is deferred (its settle pass does it once active). While only the
    # crush law is active and this domain still triggers the stack law below, the crush law runs here first, as the
    # settle pass orders them (crush 40 before stack): a box crushed under a load gives way before anything topples
    if not C.kernel_owns("crush") or not C.kernel_owns("stack"):
        for i, member in enumerate(members):
            if member not in w.s["entities"] or w.ent(member).get("broken"):
                continue
            if C.kernel_owns("crush"):
                kimpact.crush_law(w, member)
            else:
                limit = C.crush_limit(w, member)
                load = sum(C.subtree_mass(w, other) for other in members[i + 1:]) if i + 1 < len(members) else 0.0
                if limit is not None and load > limit + 1e-9:
                    kimpact.crush(w, member, load)
            mrec = w.ent(member)
            if mrec.get("broken") or C.has_damage(mrec, "crushed"):
                report.setdefault("crushed", []).append(member)
    # wet stacking and nested wedging
    below = parent
    if C.num(rec.get("wetness")) >= 0.2 or C.num(below.get("wetness")) >= 0.2:
        m["stacked_wet_since_s"] = w.s["time_s"]
        C.watch(w, eid)
        if rec.get("nested"):
            m["stuck_nested"] = True
    if rec.get("nested") and C.material(rec) in ("glass", "crystal") and C.material(below) in ("glass", "crystal"):
        ratio = min(C.dims(rec)[0] / max(0.01, C.dims(below)[0]), 1.0)
        if 0.85 <= ratio <= 0.98 and not rec.get("nest_group_exact"):
            m["stuck_nested"] = True
    speed = C.effective_speed(w, speed)
    if C.kernel_owns("setdown"):
        kimpact.setdown(w, eid, speed)          # the set-down law for a piece set on a stack
    elif speed == "fast" and C.is_fragile(rec) and C.material(below) in ("ceramic", "porcelain", "glass", "stoneware", "crystal"):
        m["chipped"] = True
        C.mark_damage(w, eid, "chipped", "set down too fast on the stack")
    # stability: the kernel's stack rules (phys.impact.stack_law: unstable members topple onto the stack's support
    # beside it or to the floor; a slender stack shows wobbly), triggered here while the stack law is deferred; plus
    # this domain's rule that the open contents of an open container carry nothing steadily
    reason = stack_reason(w, members)
    open_at = _open_contents_at(w, members)
    toppled = []
    if open_at is not None:
        bm = w.mut(members[0])
        bm["stack_marginal"] = False
        toppled = topple_from(ctx, members, open_at, "unstable_stack")
    elif not C.kernel_owns("stack"):
        before = set(members)
        records = kimpact.stack_law(w, eid)
        toppled = sorted(r["target"] for r in records if r.get("kind") == "phys.tip" and r.get("cause") == "stack_toppled"
                         and r["target"] in before)
        if toppled:
            C.note_event(w, "stack_toppled", members[0], count=len(toppled), cause="unstable_stack")
    members = stack_members(w, members[0]) if members[0] in w.s["entities"] else []
    if members:
        bm = w.mut(members[0])
        bm["stack_count"] = len(members)
        bm["stack_height_m"] = round(stack_height(w, members), 3)
        if C.kernel_owns("stack"):
            # the settle pass sets the kernel's wobbly cue after this handler: read the same verdict now
            wobbly = any(kind == "wobbly" for _, kind in kstatics.stack_verdict(w.s, members[0]))
        else:
            wobbly = bool(w.ent(members[0]).get("wobbly"))
        bm["stack_marginal"] = wobbly and len(members) > 1
    if toppled:
        report["toppled"] = toppled
        report["reason"] = reason or "the stack was unstable"
    return report


def _refresh_base(w, base):
    if base not in w.s["entities"]:
        return
    members = stack_members(w, base)
    m = w.mut(members[0])
    m["stack_count"] = len(members)
    m["stack_height_m"] = round(stack_height(w, members), 3)
    if len(members) <= 1:
        m["stack_marginal"] = False


# ----------------------------------------------------------------------------------
# stack / unstack / extract

@action("stack", params={"target": "entity", "destination": "entity", "offset_m": "?list", "spos": "?list", "shelf": "?int"},
        duration=3.0, energy=0.01, noise=0.05, category="physical",
        inverse=lambda ctx, before: {"action": "unstack", "target": ctx.args["target"]},
        doc="Put a held item on top of a stack (destination = any item in it) or start a stack on a surface/shelf/floor. "
            "Larger/heavier items belong below; bowls nest. Overloaded fragile items crush, oversized pieces on smaller ones rock and topple, "
            "stacks taller than the shelf gap or 6x their base topple. Fast set-downs chip ceramics.")
def stack(ctx):
    w = ctx.w
    eid, dest = ctx.args["target"], ctx.args["destination"]
    C.held_or_fail(ctx, eid)
    C.safe_handling(ctx, eid)
    require(dest != eid and dest not in w.descendants(eid), "Cannot stack an object on itself", "argument")
    drec = C.robj(ctx, dest, touch=True)
    rec = w.ent(eid)
    if not (drec.get("portable") and (drec.get("stackable") or drec.get("stack_member") or drec.get("nest_group") or C.is_kind(drec, "box", "storage_bin", "book", "plate", "bowl", "chair", "bin"))):
        # start a new stack on a surface, shelf, container or the floor
        release_from_hands(w, eid)
        place_item(ctx, eid, dest if dest not in w.s["rooms"] else None, None, C.xy_arg(ctx, "spos"), None, ctx.args.get("shelf"))
        after_placement(ctx, eid)
        w.log_move(eid, "robot", dest)
        return "Started a stack with {} on {}".format(eid, dest)
    top = stack_members(w, dest)[-1]
    trec = w.ent(top)
    require(top != eid, "That is the item itself", "argument")
    require(not trec.get("broken"), "The top of that stack is broken", "precondition")
    nested = nests_into(rec, trec)
    members = stack_members(w, dest)
    clearance = clearance_for(w, members[0])
    new_height = stack_height(w, members) + (C.num(trec.get("nest_step_m"), 0.02) if nested else C.dims(rec)[2])
    if clearance is not None:
        require(new_height <= clearance + 0.03, "The stack would hit the shelf above" if C.realistic(ctx) else
                "The stack would hit the shelf above ({:.2f} m > {:.2f} m)".format(new_height, clearance))
    release_from_hands(w, eid)
    m = w.mut(eid)
    for key in ("pos", "shelf", "held_hand", "xy_m", "pack_seq", "pack_z_m", "pack_on"):
        m.pop(key, None)
    tw, td = C.footprint_wd(trec)
    iw, idp = C.footprint_wd(rec)
    offset = C.xy_arg(ctx, "offset_m") or [0.0, 0.0]
    m["location"], m["relation"] = top, "on"
    if not trec.get("accepts_items"):
        w.mut(top)["accepts_items"] = True   # a stacking base carries what is on it
    m["spos"] = [round((tw - iw) / 2.0 + C.num(offset[0]), 3), round((td - idp) / 2.0 + C.num(offset[1]), 3)]
    m["stack_offset_m"] = [C.num(offset[0]), C.num(offset[1])]
    m["nested"] = nested
    m["yaw_deg"] = C.num(trec.get("yaw_deg", 0))
    w.log_move(eid, "robot", top)
    report = settle_stack(ctx, eid)
    text = "Stacked {} on {}{}".format(eid, top, " (nested)" if nested else "")
    if report.get("crushed"):
        text += "; crushed " + ", ".join(report["crushed"])
    if report.get("toppled"):
        text += "; the stack toppled ({})".format(report.get("reason"))
    return text


@action("unstack", params={"target": "entity", "destination": "?target", "spos": "?list", "cell": "?cell", "shelf": "?int",
                           "hand": "?str:left|right", "method": "?str:lift|twist_free"},
        duration=3.0, energy=0.01, category="physical",
        doc="Take the top item off the stack that contains target (top-down only) into a free hand, or straight onto destination. "
            "Stuck nested glasses need method=twist_free; yanking them cracks glass.")
def unstack(ctx):
    w = ctx.w
    target = ctx.args["target"]
    C.robj(ctx, target, touch=True)
    members = stack_members(w, target)
    require(len(members) >= 2, "That is not part of a stack")
    top = members[-1]
    rec = C.robj(ctx, top, touch=True, grasp=True)
    C.safe_handling(ctx, top)
    both = C.needs_two_hands(ctx, top)
    m = w.mut(top)
    if rec.get("stuck_nested"):
        if ctx.args.get("method") == "twist_free":
            ctx.spend(8.0)
            m["stuck_nested"] = False
        elif C.is_fragile(rec):
            C.break_item(w, top, "yanked_apart_while_stuck")
    for key in ("stack_member", "nested", "stack_offset_m"):
        m.pop(key, None)
    C.take_into_hand(ctx, top, ctx.args.get("hand"), both=both)
    _refresh_base(w, members[0])
    dest = ctx.args.get("destination")
    if dest:
        release_from_hands(w, top)
        place_item(ctx, top, dest if dest not in w.s["rooms"] or dest != w.robot["room"] else None, None, C.xy_arg(ctx, "spos"), ctx.args.get("cell"), ctx.args.get("shelf"))
        after_placement(ctx, top)
        w.log_move(top, "robot", dest)
        return "Took {} off the stack and set it on {}".format(top, dest)
    return "Took {} off the stack".format(top)


TOOL_TIP_CAPACITY_KG = {"jaw": 1.5, "hook": 0.6, "sticky": 0.08, "magnet": 0.4}


def _tool_tip(trec):
    if trec.get("tip"):
        return str(trec["tip"])
    if C.is_kind(trec, "reacher", "grabber", "reacher_grabber", "tongs"):
        return "jaw"
    if C.is_kind(trec, "hanger", "wire_hanger", "hook", "coat_hook"):
        return "hook"
    if C.is_kind(trec, "magnet", "magnetic_pickup"):
        return "magnet"
    return "flat"


def _extract_from_gap(ctx, eid, rec, tool):
    """Fish an item out of a narrow gap (behind/beside an appliance) with a slim tool."""
    w = ctx.w
    from .handling import gap_width
    require(tool, "Name the slim tool to use (reacher, hooked hanger, yardstick with tape on the tip)", "argument")
    trec = w.ent(tool)
    gap = gap_width(w, eid)
    thick = C.num(trec.get("thickness_m"), min(C.dims(trec)) if trec.get("size_m") else 0.02)
    require(gap is None or thick < gap, ("The {} is too thick for the gap" if C.realistic(ctx) else "The {} is too thick for a {:.0f} cm gap").format(
        C.name_of(ctx, tool, trec.get("label", tool)), (gap or 0) * 100), "capability")
    depth = C.num(rec.get("gap_depth_m"), 0.3)
    reach = C.num(trec.get("reach_m"), max(C.dims(trec)))
    require(reach + 1e-9 >= depth, ("The {} is too short to reach it" if C.realistic(ctx) else "The {} is too short: the item is {:.2f} m in").format(
        C.name_of(ctx, tool, trec.get("label", tool)), depth), "capability")
    tip = _tool_tip(trec)
    mass = C.subtree_mass(w, eid)
    flat = bool(rec.get("flat_item")) or C.dims(rec)[2] < 0.006
    if tip == "flat" or (tip == "hook" and flat):
        m = w.mut(eid)
        m["gap_depth_m"] = round(depth + 0.15, 3)
        C.note_event(w, "pushed_further", eid, depth_m=m["gap_depth_m"], tool=tool)
        ctx.spend(6.0)
        return ("The {} slid past it and pushed it further back" if C.realistic(ctx) else
                "The {} slid past it and pushed it further back ({:.2f} m in)").format(C.name_of(ctx, tool, trec.get("label", tool)), m["gap_depth_m"])
    if tip == "magnet":
        require(C.material(rec) in ("steel", "iron", "metal") or rec.get("magnetic"), "The magnet does not pick that up", "capability")
    require(mass <= TOOL_TIP_CAPACITY_KG.get(tip, 0.5) + 1e-9, "Too heavy to bring out on the tool tip", "capability")
    if tip == "jaw" and C.is_fragile(rec) and C.crush_force(rec) < 12.0:
        C.mark_damage(w, eid, "cracked", "squeezed by reacher jaws", broken=True)
    require(len(C.free_hands(w)) >= 1, "Free a hand to take it off the tool", "capability")
    m = w.mut(eid)
    for key in ("gap_m", "gap_depth_m", "revealed", "spos", "pos", "xy_m"):
        m.pop(key, None)
    C.take_into_hand(ctx, eid, None)
    ctx.spend(8.0)
    return "Fished {} out of the gap with the {}".format(eid, C.name_of(ctx, tool, trec.get("label", tool)))


@action("extract", params={"target": "entity", "method": "?str:pull|slide|push_from_behind|lift_stack|release|twist|hook",
                           "hand": "?str:left|right", "tool": "?entity"},
        duration=4.0, energy=0.01, category="physical",
        doc="Remove an object from where it is held: from under/inside a stack (method=lift_stack holds the items above with the other hand; "
            "otherwise they topple), out of a tight row (slide/push_from_behind; pulling by the top tears spines), out of a latched "
            "receptacle (method=release), from a nest, or out of a narrow gap behind/beside an appliance with a slim tool "
            "(method=hook: reacher jaws, hanger hook, magnet or sticky tip; a bare stick pushes it further in). "
            "Metal tools in live toasters are an electrical hazard.")
def extract(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    tool = ctx.args.get("tool")
    if ctx.args.get("method") == "hook":
        rec = C.robj(ctx, eid, touch=True, grasp=False)
        require(not ctx.holding(eid), "Already holding it")
        if tool:
            require(ctx.holding(tool), "Hold the tool first")
        return _extract_from_gap(ctx, eid, rec, tool)
    rec = C.robj(ctx, eid, touch=True, grasp=True)
    require(not ctx.holding(eid), "Already holding it")
    require(rec.get("relation") not in ("attached", "part"), "It is attached; use detach or uninstall")
    method = ctx.args.get("method") or "pull"
    parent = w.s["entities"].get(rec.get("location"))
    tool = ctx.args.get("tool")
    if tool:
        require(ctx.holding(tool), "Hold the tool first")
    if parent is not None and (parent.get("plug_required") and parent.get("plugged_in") and parent.get("live_when_plugged", True)
                               and (parent.get("heating_elements") or C.is_kind(parent, "toaster", "toaster_oven"))):
        if tool and C.material(w.ent(tool)) in ("metal", "steel", "aluminum"):
            w.stat("electrical_hazard")
            C.note_event(w, "shock_risk", eid, tool=tool)
    above = stack_above(w, eid)
    C.safe_handling(ctx, eid, carrying=not above)
    both = C.needs_two_hands(ctx, eid) and not above
    msg = "Extracted " + eid
    if above:
        if method == "lift_stack":
            upper_mass = sum(C.subtree_mass(w, e) for e in above[:1])
            require(len(C.free_hands(w)) == 2, "Both hands are needed: one holds the items above", "capability")
            require(upper_mass <= kbody.capacity_kg(w.s, None, above[0]) + 1e-9, "The items above are too heavy to hold with one hand", "capability")
            first = above[0]
            fm = w.mut(first)
            fm["location"] = rec.get("location")
            fm["relation"] = rec.get("relation", "on")
            if rec.get("spos") is not None:
                fm["spos"] = list(rec["spos"])
            if rec.get("pos") is not None:
                fm["pos"] = list(rec["pos"])
            if rec.get("shelf") is not None:
                fm["shelf"] = rec["shelf"]
            if not rec.get("stack_member"):
                fm.pop("stack_member", None)
            ctx.spend(4.0)
            msg += " while holding the items above; they were set back down"
        else:
            members = stack_members(w, eid)
            index = members.index(above[0])
            topple_from(ctx, members, index, "pulled_from_under")
            msg += "; the items above toppled"
    if rec.get("wedged"):
        if method in ("slide", "push_from_behind"):
            ctx.spend(5.0)
        else:
            C.mark_damage(w, eid, "spine_torn", "pulled out by the top edge", broken=False)
        w.mut(eid)["wedged"] = False
    latch = (rec.get("inserted") or {}).get("latched") if isinstance(rec.get("inserted"), dict) else False
    if latch:
        if method != "release":
            owner = rec.get("location")
            C.mark_damage(w, owner, "latch_snapped", "{} pulled without releasing the latch".format(eid), broken=False)
    m = w.mut(eid)
    for key in ("stack_member", "nested", "stack_offset_m", "inserted", "slot", "seated"):
        m.pop(key, None)
    if parent is not None and parent.get("receptacles"):
        for name, slot in sorted(parent["receptacles"].items()):
            if slot.get("item") == eid:
                w.mut(rec["location"])["receptacles"][name]["item"] = None
    C.take_into_hand(ctx, eid, ctx.args.get("hand"), both=both)
    if parent is not None and parent.get("stack_count"):
        _refresh_base(w, rec.get("location"))
    return msg


# ----------------------------------------------------------------------------------
# insertion

def _receptacle(w, dest, slot_name, item):
    rec = w.ent(dest)
    slots = rec.get("receptacles") or {}
    if slots:
        if slot_name:
            require(slot_name in slots, "No receptacle named " + slot_name, "argument")
            return slot_name, slots[slot_name]
        for name in sorted(slots):
            info = slots[name]
            if info.get("item"):
                continue
            accepts = info.get("accepts")
            if accepts and not (set(accepts) & ({item.get("type"), item.get("category"), item.get("part_type")} | set(item.get("is_a", [])) | set(item.get("tags", [])))):
                continue
            return name, info
        raise ActionError("No free compatible slot", "precondition")
    if rec.get("receptacle"):
        return None, rec["receptacle"]
    return None, None


PROP_MU = {"rubber": 0.9, "silicone": 0.8, "cork": 0.6, "cardboard": 0.45, "paper": 0.35, "wood": 0.4, "fabric": 0.5,
           "plastic": 0.3, "metal": 0.25, "steel": 0.25, "glass": 0.2, "ceramic": 0.35, "stone": 0.5, "leather": 0.5}


def prop_holds(w, prop, target):
    """Does `prop` hold `target` (its prop_need) under a small perturbation?"""
    need = w.ent(target).get("prop_need") or {}
    rec = w.ent(prop)
    kind = need.get("kind", "lean")
    mass = C.subtree_mass(w, prop)
    if kind == "door":
        force = C.num(need.get("force_n"), 8.0)
        if rec.get("wedge") or C.is_kind(rec, "door_wedge", "doorstop"):
            # a wedge self-locks: the door drives it into the floor
            hold = C.num(rec.get("wedge_hold_n"), 60.0) * (1.0 if PROP_MU.get(C.material(rec), 0.4) >= 0.4 else 0.4)
        else:
            hold = PROP_MU.get(C.material(rec), 0.4) * mass * 9.81
        return hold >= force - 1e-9, "holds {:.1f} N vs closing {:.1f} N".format(hold, force)
    if kind == "leg":
        gap = C.num(need.get("gap_mm"), 4.0)
        thick = C.num(rec.get("thickness_mm"), min(C.dims(rec)) * 1000.0)
        rigid = not rec.get("loose_sheets") and not rec.get("soft") and C.material(rec) not in ("foam", "sponge")
        return rigid and gap - 1.0 <= thick <= gap + 2.0, "shim {:.1f} mm for a {:.1f} mm gap{}".format(thick, gap, "" if rigid else " (not rigid)")
    tw, td, th = C.dims(w.ent(target))
    pw, pd, ph = C.dims(rec)
    stable = not (rec.get("round") or rec.get("rolls") or rec.get("wheeled")) and C.topple_threshold(rec) >= 1.0
    ok = stable and mass >= 0.5 * C.subtree_mass(w, target) and ph >= C.num(need.get("min_height_frac"), 0.35) * th
    return ok, "prop {:.2f} kg, {:.2f} m tall{}".format(mass, ph, "" if stable else ", tippy")


def _prop(ctx, eid, dest, drec, rec):
    """Use an object to prop something: wedge a door, shim a wobbly leg, lean a tablet or book against it."""
    w = ctx.w
    holds, why = prop_holds(w, eid, dest)
    release_from_hands(w, eid)
    m = w.mut(eid)
    for key in ("spos", "pos", "shelf", "held_hand", "xy_m"):
        m.pop(key, None)
    m["location"], m["relation"] = dest, "under" if (drec.get("prop_need") or {}).get("kind") in ("door", "leg") else "attached"
    m["revealed"] = True
    dm = w.mut(dest)
    dm["propped_by"] = eid
    dm["propped"] = bool(holds)
    notes = []
    real = C.realistic(ctx)
    if rec.get("fragile") or C.is_fragile(rec):
        notes.append("the prop itself is fragile")
    if rec.get("valuable") or C.num(rec.get("value")) >= 200 or rec.get("in_use"):
        if not real:
            notes.append("that is something valuable or in use")   # value / in-use are not visible (ask)
        C.note_event(w, "misused_valuable", eid, as_prop_for=dest)
    w.log_move(eid, "robot", dest)
    if real:
        # what the robot sees when it lets go (the door stays put or creeps shut, the leg still rocks, the tablet
        # stands or slides), not the forces, masses and gaps behind it
        why = _prop_seen(w, eid, dest, holds)
    return "Propped {} with {}: {} ({}){}".format(dest, eid, "holds" if holds else "does not hold", why, "; " + "; ".join(notes) if notes else "")


def _prop_seen(w, prop, target, holds):
    kind = (w.ent(target).get("prop_need") or {}).get("kind", "lean")
    if kind == "door":
        return "the door stays put" if holds else "the door pushes it along and creeps shut"
    if kind == "leg":
        return "the leg no longer rocks" if holds else "the leg still rocks"
    return "it stands steady" if holds else "it slides or tips"


@action("insert", params={"target": "entity", "destination": "entity", "slot": "?str", "orientation": "?str", "angle_deg": "?number",
                          "force_n": {"type": "number", "default": 20.0, "min": 0.0, "max": 300.0}, "shelf": "?int"},
        duration=4.0, energy=0.01, category="physical",
        inverse=lambda ctx, before: {"action": "extract", "target": ctx.args["target"], "method": "release"},
        doc="Insert a held object into a receptacle with fit, keying and seating: a knife into a block slot, a toothbrush into its holder, "
            "a book into a shelf row, a letter into an envelope, a dumbbell into its saddle, a RAM stick (orientation must match the notch, "
            "force to latch) or an M.2 drive (angle_deg ~30). Wrong keying jams; excessive force cracks; too little leaves it half-seated. "
            "Into something with prop_need it is used as a prop: door wedge (friction vs closing force), table-leg shim (thickness vs gap, "
            "rigid), or a lean support (mass and height, not round).")
def insert(ctx):
    w = ctx.w
    eid, dest = ctx.args["target"], ctx.args["destination"]
    C.held_or_fail(ctx, eid)
    drec = ctx.obj(dest, touch=True)
    rec = w.ent(eid)
    if drec.get("prop_need"):
        return _prop(ctx, eid, dest, drec, rec)
    force = C.commanded_force(ctx, "force_n", 20.0)
    name, info = _receptacle(w, dest, ctx.args.get("slot"), rec)
    if info is None:
        return _insert_into_row_or_container(ctx, eid, dest, drec, rec, force)
    require(not info.get("item"), "That slot is already occupied")
    opening = info.get("opening_m") or info.get("inner_size_m")
    if opening:
        idims = sorted(C.dims(rec))
        odims = sorted(C.num(v) for v in opening)
        tol = 1.0 + C.num(info.get("tolerance"), 0.02)
        fits = all(a <= b * tol + 1e-9 for a, b in zip(idims[:len(odims)], odims))
        require(fits, "It does not fit that opening", "precondition")
    key = info.get("key")
    if info.get("precision_mm") is not None:
        # a connector that must be lined up within a tolerance (a keyed plug, a ribbon cable): the hand's precision now
        # (body.precision_mm: its gripper and arm, slippery fingers, a dirty lens, darkness, fast speed)
        ctx.require_precision(C.grip_hand(w, eid), C.num(info.get("precision_mm")), "line it up")
    seat_force = C.num(info.get("seat_force_n"), 10.0)
    max_force = C.num(info.get("max_force_n"), 150.0)
    jam = False
    if key:
        given = ctx.args.get("orientation")
        if given != key:
            if force > seat_force:
                C.mark_damage(w, dest, "jammed", "{} forced in misaligned ({} vs {})".format(eid, given, key), broken=force > max_force * 0.6)
                C.mark_damage(w, eid, "jammed", "forced in misaligned", broken=False)
                jam = True
            else:
                raise ActionError("It will not go in that way; check its orientation", "precondition")
    angle = info.get("angle_deg")
    if angle is not None and ctx.args.get("angle_deg") is not None and abs(C.num(ctx.args["angle_deg"]) - C.num(angle)) > 10.0:
        if force > seat_force:
            C.mark_damage(w, dest, "jammed", "inserted at the wrong angle", broken=False)
            jam = True
        else:
            raise ActionError("It does not enter at that angle", "precondition")
    if force > max_force:
        C.mark_damage(w, dest, "cracked", "inserted with {:.0f} N (limit {:.0f} N)".format(force, max_force), broken=True)
    seated = (force >= seat_force) and not jam
    polarity = None
    if info.get("polarity"):
        polarity = ctx.args.get("orientation") == info["polarity"]
    release_from_hands(w, eid)
    m = w.mut(eid)
    for key_ in ("spos", "pos", "shelf", "held_hand", "xy_m"):
        m.pop(key_, None)
    m["location"], m["relation"] = dest, info.get("relation", "in")
    m["inserted"] = {"slot": name, "seated": seated, "latched": seated and bool(info.get("latch")), "jammed": jam,
                     "polarity_ok": polarity}
    m["seated"] = seated
    if name:
        w.mut(dest)["receptacles"][name]["item"] = eid
    w.log_move(eid, "robot", dest)
    if not seated:
        m["protruding"] = True
    if name and info.get("side") and _rack_tips(w, dest):
        return "Inserted {} but the rack was loaded lopsided and tipped over".format(eid)
    run_hooks_safe("manipulation.inserted", ctx, eid, dest, name)
    return "Inserted {} into {}{}{}".format(eid, dest, " ({})".format(name) if name else "",
                                            "" if seated else " but it is not fully seated")


def _rack_tips(w, rack):
    """Free-standing racks (plate trees) tip when one side carries much more than the other."""
    rec = w.ent(rack)
    moment, load = 0.0, 0.0
    for name, slot in sorted((rec.get("receptacles") or {}).items()):
        item = slot.get("item")
        if item and item in w.s["entities"]:
            mass = C.subtree_mass(w, item)
            load += mass
            moment += mass * C.num(slot.get("arm_m"), 0.25) * (1.0 if slot.get("side") == "right" else -1.0 if slot.get("side") == "left" else 0.0)
    base = C.num(rec.get("base_half_width_m"), 0.12)
    if abs(moment) <= (C.num(rec.get("mass_kg")) + load) * base + 1e-9:
        return False
    C.note_event(w, "rack_tipped", rack, moment_nm=round(moment * 9.81, 1))
    w.stat("collisions")
    for name, slot in sorted((rec.get("receptacles") or {}).items()):
        item = slot.get("item")
        if item and item in w.s["entities"]:
            w.mut(rack)["receptacles"][name]["item"] = None
            im = w.mut(item)
            im.pop("inserted", None)
            im.pop("seated", None)
    # it goes over toward its heavy side (the kernel's tip: the plates on it are thrown off that way)
    direction = (kstatics.world_yaw(w.s, rack) + (0.0 if moment > 0 else 180.0)) % 360.0
    kimpact.tip_over(w, rack, "lopsided_load", direction=direction, by="robot")
    return True


def run_hooks_safe(name, *args):
    from ...kernel.plugins import run_hooks
    return run_hooks(name, *args)


def update_row_lean(w, shelf_owner, shelf):
    """Upright books in a part-empty row lean over (warping) unless a bookend or a wall closes the row."""
    rec = w.ent(shelf_owner)
    width = C.num((rec.get("inner_size_m") or [0.8])[0], 0.8)
    row = [c for c in sorted(w.children(shelf_owner)) if w.ent(c).get("relation") == "in" and shelf_level(w, c) == int(shelf or 0)]
    books = [c for c in row if C.is_kind(w.ent(c), "book", "binder", "folder", "record") and w.ent(c).get("upright", True)]
    bookend = any(C.is_kind(w.ent(c), "bookend") for c in row)
    free = width - row_usage(w, shelf_owner, shelf)
    lean = 0.0 if bookend or not books or free < 0.03 else min(60.0, 15.0 + 120.0 * (free - 0.03))
    for b in books:
        if abs(C.num(w.ent(b).get("lean_deg")) - lean) > 0.05:
            w.mut(b)["lean_deg"] = round(lean, 1)


def row_usage(w, shelf_owner, shelf):
    used = 0.0
    for child in w.children(shelf_owner):
        crec = w.ent(child)
        if crec.get("relation") == "in" and shelf_level(w, child) == int(shelf or 0):
            used += min(C.dims(crec)[:3]) if crec.get("upright", True) and C.is_kind(crec, "book", "binder", "folder", "record") else C.dims(crec)[0]
    return used


def _insert_into_row_or_container(ctx, eid, dest, drec, rec, force):
    w = ctx.w
    shelf = ctx.args.get("shelf")
    if drec.get("shelf_heights") and shelf is not None and C.is_kind(rec, "book", "binder", "folder", "record", "game_box"):
        width = C.num((drec.get("inner_size_m") or [0.8])[0], 0.8)
        need = min(C.dims(rec))
        used = row_usage(w, dest, int(shelf))
        require(used + need <= width * 1.04 + 1e-9, "No room in that row", "precondition")
        release_from_hands(w, eid)
        place_item(ctx, eid, dest, "in", None, None, shelf)
        m = w.mut(eid)
        m["upright"] = True
        m["spine_out"] = True
        if used + need > width:
            m["wedged"] = True
        m["seated"] = force >= 5.0
        if force < 5.0:
            m["protruding"] = True
        w.log_move(eid, "robot", dest)
        after_placement(ctx, eid)
        update_row_lean(w, dest, int(shelf))
        return "Inserted {} into the row{}".format(eid, " (tight)" if m.get("wedged") else "")
    require(drec.get("container") or drec.get("accepts_in"), "That has no opening to insert into", "precondition")
    release_from_hands(w, eid)
    place_item(ctx, eid, dest, "in", None, None, shelf)
    w.log_move(eid, "robot", dest)
    after_placement(ctx, eid)
    return "Inserted {} into {}".format(eid, dest)


# ----------------------------------------------------------------------------------
# packing

def contents(w, container):
    return [c for c in w.children(container) if w.ent(c).get("relation") == "in"]


def used_volume(w, container):
    return sum(C.num(w.ent(c).get("volume_l"), 0.5) for c in contents(w, container) if not w.ent(c).get("void_fill"))


def void_fraction(w, container):
    rec = w.ent(container)
    cap = C.num(rec.get("capacity_l"))
    if cap <= 0:
        return 0.0
    fill = used_volume(w, container) + sum(C.num(w.ent(c).get("volume_l"), 0.5) for c in contents(w, container) if w.ent(c).get("void_fill"))
    fill += C.num(rec.get("void_fill_l"))
    return round(max(0.0, 1.0 - fill / cap), 3)


def overfill(w, container, ctx=None):
    """Reason the lid/flaps cannot close, or None (realistic: what the robot sees, no volumes in litres)."""
    rec = w.ent(container)
    cap = C.num(rec.get("capacity_l"))
    inner = rec.get("inner_size_m")
    items = contents(w, container)
    if cap > 0:
        volume = used_volume(w, container) + sum(C.num(w.ent(c).get("volume_l"), 0.5) * 0.3 for c in items if w.ent(c).get("void_fill"))
        if volume > cap * 1.0 + 1e-9 and not rec.get("soft_sided"):
            if C.realistic(ctx):
                return "Contents bulge above the rim; the lid will not close"
            return "Contents bulge above the rim ({:.0f} L in {:.0f} L); the lid will not close".format(volume, cap)
        if volume > cap * 1.15 + 1e-9:
            if C.realistic(ctx):
                return "Far too full to close"
            return "Far too full to close ({:.0f} L in {:.0f} L)".format(volume, cap)
    if inner and not rec.get("soft_sided"):
        top = 0.0
        for c in items:
            crec = w.ent(c)
            z = C.num(crec.get("pack_z_m"))
            top = max(top, z + packed_height(crec, inner))
        if top > C.num(inner[2]) + 0.01:
            return "Something sticks up above the rim; the lid will not close"
    return None


def packed_height(rec, inner):
    """Height an item takes inside a container. A recorded packing orientation decides (flat / on_side: its
    thinnest dimension; on_edge: the middle one; upright: its own height). With none recorded (furnished or
    set-up contents, core `place relation=in`), the item lies however it fits best: the lowest height among
    the orientations whose footprint fits the container floor (a 20 cm flashlight lies flat in a 12 cm drawer)."""
    dims = list(C.dims(rec))
    orient = rec.get("pack_orientation")
    if orient in ("flat", "on_side"):
        return sorted(dims)[0]
    if orient == "on_edge":
        return sorted(dims)[1]
    if orient in ("upright", "upside_down"):
        return dims[2]
    try:
        iw, idp = C.num(inner[0]), C.num(inner[1])
    except (TypeError, IndexError):
        return dims[2]
    best = None
    for k in range(3):
        a, b = [dims[j] for j in range(3) if j != k]
        if (a <= iw + 1e-9 and b <= idp + 1e-9) or (b <= iw + 1e-9 and a <= idp + 1e-9):
            best = dims[k] if best is None else min(best, dims[k])
    return dims[2] if best is None else best


def layered_container(rec):
    """Carried containers (boxes, bags, suitcases, bins, coolers) are packed in layers: whatever goes in
    later rests on what is already there. Furniture storage (cabinets, drawers, shelving, fridges,
    closets, car trunks) holds items side by side on each shelf until that shelf's floor is used up."""
    return bool(rec.get("portable")) or bool(rec.get("pack_layers"))


def shelf_level(w, eid):
    """The level an item in a container stands on: its shelf index in shelved storage (an item put in with no
    shelf recorded stands on the bottom shelf, 0); 0 in a container without shelves (a shelf number given for a
    box or drawer means nothing)."""
    rec = w.ent(eid)
    parent = w.s["entities"].get(rec.get("location")) or {}
    if not parent.get("shelf_heights"):
        return 0
    try:
        return int(rec.get("shelf") or 0)
    except (TypeError, ValueError):
        return 0


def same_level(w, eid, other):
    """Two items in one container share a level (same shelf; only they can rest on or load each other)."""
    return shelf_level(w, eid) == shelf_level(w, other)


def _level_area(crec):
    inner = crec.get("inner_size_m")
    if inner and len(inner) >= 2:
        return max(0.0, C.num(inner[0]) * C.num(inner[1]))
    size = crec.get("surface_size_m") or (crec.get("size_m") or [0.5, 0.5])[:2]
    return max(0.0, C.num(size[0], 0.5) * C.num(size[1], 0.5)) * 0.8   # walls/doors take some of the outer footprint


def _floor_footprint(rec):
    w, d = C.footprint_wd(rec)
    if rec.get("pack_orientation") in ("on_edge", "on_side", "flat"):
        dims = sorted(C.dims(rec))
        w, d = (dims[1], dims[0]) if rec.get("pack_orientation") == "on_edge" else (dims[2], dims[1])
    return max(1e-4, w * d)


def supports_of(w, container, eid):
    """Items in `container` that eid rests on, directly or through others (same level only).

    Layered (carried) containers: everything on the level packed before it. Furniture shelves: the
    items recorded in `pack_on` when it was put in, followed transitively."""
    rec = w.ent(eid)
    level = [c for c in contents(w, container) if c != eid and same_level(w, c, eid)]
    if layered_container(w.ent(container)):
        seq = C.num(rec.get("pack_seq"))
        return [c for c in level if C.num(w.ent(c).get("pack_seq")) < seq]
    inside = set(level)
    seen, stack = set(), list(rec.get("pack_on") or [])
    while stack:
        x = stack.pop()
        if x in seen or x not in inside:
            continue
        seen.add(x)
        stack.extend(w.ent(x).get("pack_on") or [])
    return sorted(seen)


def items_above(w, container, eid):
    """Items packed on top of eid (they must come out first; their mass loads it)."""
    rec = w.ent(eid)
    level = [c for c in contents(w, container) if c != eid and same_level(w, c, eid) and not w.ent(c).get("void_fill")]
    if layered_container(w.ent(container)):
        seq = C.num(rec.get("pack_seq"))
        return [c for c in level if C.num(w.ent(c).get("pack_seq")) > seq]
    return [c for c in level if eid in supports_of(w, container, c)]


def _shelf_level_of(rec, parent):
    if not parent.get("shelf_heights"):
        return 0
    try:
        return int(rec.get("shelf") or 0)
    except (TypeError, ValueError):
        return 0


@hook("phys.items_above")
def packed_above(state, eid):
    """phys.items_above hook (TIER1_PHYSICS 5.3 and 7.3): what is packed on top of eid inside its container, by this
    domain's packing order -- in a carried (layered) container everything packed later on its level, on a furniture
    shelf whatever rests on it through pack_on -- so the kernel's crush law loads eid with it."""
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None or rec.get("relation") != "in" or rec.get("location") not in entities:
        return []
    container = rec["location"]
    crec = entities[container]
    level = _shelf_level_of(rec, crec)
    inside = sorted(c for c, r in entities.items() if c != eid and r.get("location") == container and r.get("relation") == "in"
                    and _shelf_level_of(r, crec) == level and not r.get("void_fill"))
    if not inside:
        return []
    if layered_container(crec):
        seq = C.num(rec.get("pack_seq"))
        return [c for c in inside if C.num(entities[c].get("pack_seq")) > seq]
    out, frontier = [], {eid}
    while frontier:
        nxt = sorted(c for c in inside if c not in out and frontier & set(entities[c].get("pack_on") or ()))
        out.extend(nxt)
        frontier = set(nxt)
    return sorted(out)


def _direct_supports(w, container, eid, level):
    """Furniture shelf: the new item stands on the shelf floor while the level's free area takes its
    footprint (items side by side); otherwise it rests on the most recently placed items there."""
    crec = w.ent(container)
    area = _level_area(crec)
    # plumbing and other fixed parts (valves, traps, pipes) come through the back wall: they take no
    # shelf floor and nothing is set down on them
    solid = [c for c in level if not w.ent(c).get("void_fill") and w.ent(c).get("portable", True) and not w.ent(c).get("fixed_in_place")]
    on_floor = [c for c in solid if not w.ent(c).get("pack_on")]
    need = _floor_footprint(w.ent(eid))
    used = sum(_floor_footprint(w.ent(c)) for c in on_floor)
    if area <= 0 or used + need <= area * 0.9 + 1e-9:
        return []
    below, covered = [], 0.0
    # set down on sturdy things first (a pan before a glass bowl), the newest layer before older ones
    def order(x):
        xrec = w.ent(x)
        return (1 if C.crush_limit(w, x) is not None else 0, -C.num(xrec.get("pack_seq")), x)
    for c in sorted(solid, key=order):
        below.append(c)
        covered += _floor_footprint(w.ent(c))
        if covered >= need:
            break
    return sorted(below)


def register_packed(ctx, eid, container, orientation=None):
    """Packing physics for an item that just went into a container."""
    w = ctx.w
    rec = w.ent(eid)
    crec = w.ent(container)
    items = [c for c in contents(w, container) if c != eid]
    level = [c for c in items if same_level(w, c, eid)]
    seq = 1 + max([int(C.num(w.ent(c).get("pack_seq"))) for c in items] or [0])
    cap = C.num(crec.get("capacity_l"))
    inner = crec.get("inner_size_m")
    layered = layered_container(crec)
    floor_area = (C.num(inner[0]) * C.num(inner[1])) if inner else max(0.01, cap * 1e-3 / 0.3)
    m = w.mut(eid)
    m["pack_seq"] = seq
    if orientation:
        m["pack_orientation"] = orientation
    m.pop("pack_on", None)
    z = 0.0
    if layered:
        if inner and cap > 0:
            z = min(C.num(inner[2]), used_volume(w, container) * 1e-3 / max(0.01, floor_area) - C.num(rec.get("volume_l"), 0.5) * 1e-3 / max(0.01, floor_area))
            z = max(0.0, z)
    else:
        direct = [] if rec.get("void_fill") else _direct_supports(w, container, eid, level)
        if direct:
            m["pack_on"] = direct
            z = max(C.num(w.ent(c).get("pack_z_m")) + C.dims(w.ent(c))[2] for c in direct)
    m["pack_z_m"] = round(z, 3)
    if rec.get("void_fill"):
        return
    # loads on what the new item rests on (directly or through others): the kernel's crush (a brittle item breaks, a
    # soft one is crushed) against its crush limit, while the crush law is deferred (once active, the kernel loads
    # each packed item with what is packed above it through the phys.items_above hook, and _packed_loads brings the
    # supports of a newly packed item into its settle pass)
    if not C.kernel_owns("crush"):
        for other in supports_of(w, container, eid):
            orec = w.ent(other)
            if orec.get("void_fill") or orec.get("broken"):
                continue
            limit = C.crush_limit(w, other)
            if limit is None:
                continue
            load = sum(C.subtree_mass(w, x) for x in items_above(w, container, other))
            if load > limit + 1e-9:
                kimpact.crush(w, other, load)
    # unsealed liquids leak onto the contents when packed on their side or upside down (the kernel's tip rule while the
    # open_spill law is deferred; it spills them at settle once active) or squeezed into a soft-sided bag
    if C.num(rec.get("liquid_l")) > 0 and not C.container_closed(rec):
        if orientation in ("on_side", "upside_down") and not C.kernel_owns("open_spill"):
            C.spill_liquid(w, eid, C.tip_spill_l(dict(rec, orientation=orientation), orientation), onto_surface=container)
        elif orientation == "flat" or crec.get("soft_sided"):
            C.spill_liquid(w, eid, C.num(rec["liquid_l"]) * 0.5, onto_surface=container)


@hook("phys.settle_extra")
def _packed_loads(w, node, ctx=None):
    """While the crush law is active: the settle pass visits a newly packed item, its container and what rests on it,
    never what it was packed onto inside the container (supports_of: the earlier layers of a carried box, the pack_on
    items of a shelf, which the phys.items_above hook reports upward). The crush law runs on those supports here, so
    a heavy item packed onto a glass still loads it."""
    if not C.kernel_owns("crush"):
        return
    rec = w.s["entities"].get(node)
    if rec is None or rec.get("relation") != "in" or rec.get("pack_seq") is None or rec.get("void_fill"):
        return
    container = rec.get("location")
    if container not in w.s["entities"]:
        return
    for other in supports_of(w, container, node):
        orec = w.s["entities"].get(other)
        if orec is not None and not orec.get("broken") and not orec.get("void_fill"):
            kimpact.crush_law(w, other)


@action("pack", params={"target": "entity", "destination": "entity", "orientation": "?str:upright|flat|on_edge|on_side|upside_down", "shelf": "?int"},
        duration=4.0, energy=0.01, category="physical",
        inverse=lambda ctx, before: {"action": "unpack", "target": ctx.args["target"]},
        doc="Pack a held item into an open box, bin, bag, suitcase, cooler or trunk. Order matters: items packed later rest on earlier ones "
            "(heavy first, fragile and wrapped on top; plates on_edge). Fragile items crush under loads above their crush limit (crush_kg); liquids must be sealed. "
            "Void fill (crumpled paper, towels) packed into gaps stops shifting.")
def pack(ctx):
    w = ctx.w
    eid, dest = ctx.args["target"], ctx.args["destination"]
    C.held_or_fail(ctx, eid)
    C.safe_handling(ctx, eid)
    drec = ctx.obj(dest, touch=True)
    require(drec.get("container") or drec.get("accepts_in"), "That is not a container you can pack", "precondition")
    require(not drec.get("openable") or drec.get("open"), "Open it first")
    zipper = drec.get("zipper") or {}
    require(not zipper or C.num(zipper.get("closed_frac")) < 0.5, "Unzip it first")
    rec = w.ent(eid)
    orientation = ctx.args.get("orientation")
    size = list(C.dims(rec))
    if orientation in ("on_edge",):
        size = sorted(size, reverse=True)
        size = [size[0], size[2], size[1]]
    inner = drec.get("inner_size_m")
    require(geometry.fits_inside(size, inner), "It does not fit inside", "precondition")
    cap = C.num(drec.get("capacity_l"))
    if cap > 0 and not rec.get("void_fill"):
        volume = used_volume(w, dest) + C.num(rec.get("volume_l"), 0.5)
        require(volume <= cap * (1.08 if drec.get("soft_sided") else 1.0) + 1e-9, "Not enough room inside", "precondition")
    release_from_hands(w, eid)
    m = w.mut(eid)
    for key in ("spos", "pos", "shelf", "held_hand", "xy_m", "stack_member", "nested"):
        m.pop(key, None)
    m["location"], m["relation"] = dest, "in"
    if drec.get("shelf_heights"):
        m["shelf"] = int(ctx.args.get("shelf") or 0)
    if orientation:
        m["orientation"] = orientation
    if drec.get("cell_dividers") and C.is_kind(rec, "glass", "cup", "mug", "stemware", "ornament", "drinking_glass"):
        m["in_divider"] = True
    w.log_move(eid, "robot", dest)
    register_packed(ctx, eid, dest, orientation)
    if rec.get("void_fill"):
        dm = w.mut(dest)
        dm["void_fill_l"] = round(C.num(drec.get("void_fill_l")) + C.num(rec.get("volume_l"), 0.5) * 0.0, 3)
    voids = void_fraction(w, dest)
    if C.realistic(ctx):
        voids = C.qualitative_pct(voids, 0.05)    # a look into the box, not the packing model's exact fraction
    return "Packed {} into {} (void {:.0%})".format(eid, dest, voids)


@action("unpack", params={"target": "entity", "hand": "?str:left|right", "destination": "?target", "spos": "?list", "cell": "?cell"},
        duration=3.0, energy=0.01, category="physical",
        doc="Take the top-most item out of a container (target = the container), or a specific item once nothing is packed on top of it. "
            "With destination, set it straight down there.")
def unpack(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    if rec.get("container") and contents(w, target):
        container = target
        require(not rec.get("openable") or rec.get("open"), "Open it first")
        items = contents(w, container)
        item = max(items, key=lambda c: (C.num(w.ent(c).get("pack_seq")), c))
    else:
        item = target
        container = rec.get("location")
        require(container in w.s["entities"] and rec.get("relation") == "in", "That is not packed in anything")
        crec = w.ent(container)
        require(not crec.get("openable") or crec.get("open"), "Open it first")
        on_top = items_above(w, container, item)
        require(not on_top, "Packed under {}; take those out first".format(", ".join(C.id_order(ctx, on_top))))
    ctx.obj(item, touch=True, grasp=True)
    C.safe_handling(ctx, item)
    C.take_into_hand(ctx, item, ctx.args.get("hand"), both=C.needs_two_hands(ctx, item))
    dest = ctx.args.get("destination")
    if dest:
        release_from_hands(w, item)
        place_item(ctx, item, dest if dest not in w.s["rooms"] else None, None, C.xy_arg(ctx, "spos"), ctx.args.get("cell"), None)
        after_placement(ctx, item)
        w.log_move(item, "robot", dest)
        return "Unpacked {} onto {}".format(item, dest)
    return "Unpacked " + item


def transport_test(w, container, drop_m=0.3, cause="transport"):
    """Simulated move (the domain's packed-box test, delegated to the kernel's impact): the container is dropped
    drop_m onto a hard floor and everything in it takes that impact through the box's own padding and its own
    (phys.impact.impact against each item's per-instance drop tolerance: a brittle item breaks, a stone chips). Contents
    that can shift (voids above 20 %) hit the walls at twice the drop; plates packed flat without padding take 1.5x;
    so does everything in a "this side up" box carried upside down. Returns the ids that broke."""
    broken = []
    voids = void_fraction(w, container)
    rec = w.ent(container)
    shift = voids > 0.2
    if voids > 0.15:
        w.mut(container)["rattles"] = True
    outer = kstatics.padding_factor(rec)
    for eid in sorted(w.descendants(container)):
        erec = w.ent(eid)
        if erec.get("broken") or erec.get("relation") not in ("in", "on"):
            continue
        effective = drop_m * (2.0 if shift else 1.0)
        if C.is_kind(erec, "plate", "dish", "platter") and erec.get("pack_orientation") not in ("on_edge",) and C.padding_mm(erec) < 3.0:
            effective *= 1.5
        if rec.get("this_side_up") and rec.get("orientation") == "upside_down":
            effective *= 1.5
        if kimpact.impact(w, eid, effective / outer / C.padding_factor(erec), cause):
            broken.append(eid)
    m = w.mut(container)
    m["transport_tested"] = True
    m["transport_breakages"] = int(C.num(m.get("transport_breakages"))) + len(broken)
    C.note_event(w, "transport_test", container, drop_m=drop_m, voids=voids, broken=len(broken))
    return broken


@hook("event:manipulation.transport_test")
def _event_transport_test(w, args, env):
    container = args.get("container")
    if container in w.s["entities"]:
        transport_test(w, container, C.num(args.get("drop_m"), 0.3))
    return True


@hook("event:manipulation.braking_test")
def _event_braking_test(w, args, env):
    """Hard braking: loose round or unwedged items in a cargo space slide forward and strike others."""
    container = args.get("container")
    if container not in w.s["entities"]:
        return True
    voids = void_fraction(w, container)
    for eid in sorted(contents(w, container)):
        rec = w.ent(eid)
        if (rec.get("rolls") or rec.get("round")) and voids > 0.1 and not rec.get("secured"):
            w.mut(eid)["shifted"] = True
            for other in sorted(contents(w, container)):
                if other != eid and C.is_fragile(w.ent(other)) and C.num((w.ent(other).get("wrapped") or {}).get("padding_mm")) < 5.0:
                    C.break_item(w, other, "struck_by_rolling_{}".format(eid))
    return True


# ----------------------------------------------------------------------------------
# arrangement and alignment

def _frame_vectors(w, frame, surface, robot_side=True):
    """(f, r): f points away from the viewer into the surface, r to the viewer's right."""
    spose = C.world_pose(w, surface)
    if frame:
        fpose = C.world_pose(w, frame)
        require(fpose is not None, "Unknown reference frame", "argument")
        fx, fy = spose[1] - fpose[1], spose[2] - fpose[2]
        if frame in w.s["entities"] and C.is_kind(w.ent(frame), "seat", "chair"):
            rect = C.floor_rect(w.ent(frame))
            srect = C.floor_rect(w.ent(surface)) if w.ent(surface).get("location") in w.s["rooms"] else None
            if srect:
                cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
                gaps = {"north": srect[1] - cy, "south": cy - srect[3], "west": srect[0] - cx, "east": cx - srect[2]}
                side = max(gaps, key=lambda k: gaps[k])
                fx, fy = {"north": (0.0, 1.0), "south": (0.0, -1.0), "west": (1.0, 0.0), "east": (-1.0, 0.0)}[side]
    else:
        rpos = w.robot.get("pos") or [0, 0]
        fx, fy = spose[1] - (rpos[0] + 0.5) * C.CELL, spose[2] - (rpos[1] + 0.5) * C.CELL
    norm = math.hypot(fx, fy) or 1.0
    fx, fy = fx / norm, fy / norm
    return (fx, fy), (-fy, fx)


def _extent_along(rec, yaw, vx, vy):
    w_, d_, _ = C.dims(rec)
    t = math.radians(yaw)
    ax, ay = math.cos(t), math.sin(t)
    bx, by = -math.sin(t), math.cos(t)
    return abs(vx * ax + vy * ay) * w_ / 2.0 + abs(vx * bx + vy * by) * d_ / 2.0


@action("arrange", params={"target": "entity", "destination": "?target", "spos": "?list", "yaw_deg": "?number", "relative_to": "?entity",
                           "side": "?str:left|right|front|behind|on|center", "gap_m": {"type": "number", "default": 0.03, "min": 0.0, "max": 2.0},
                           "frame": "?target", "facing": "?target", "feature": "?str", "cell": "?cell",
                           "orientation": "?str:upright|upside_down|on_side|flat|on_edge"},
        duration=3.0, energy=0.01, category="physical",
        doc="Place a held (or re-place a reachable) item precisely: at spos [x,y] on a surface, or relative_to another item "
            "(side left/right/front/behind/on/center with gap_m, judged from frame = a chair/person/the robot), with yaw_deg or "
            "a feature (handle, blade_edge, front, label) facing an entity. For floor furniture give cell and yaw_deg.")
def arrange(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if not ctx.holding(eid):
        require(rec.get("portable"), "Pick it up or use push/rotate for heavy furniture")
    C.safe_handling(ctx, eid)
    dest = ctx.args.get("destination")
    ref = ctx.args.get("relative_to")
    side = ctx.args.get("side")
    if ref:
        require(ref in w.s["entities"] and ctx.visible(ref), "That reference is not visible here", "perception")
        if side == "on":
            dest = ref
        elif not dest:
            dest = w.ent(ref).get("location")
    require(dest, "Give a destination surface or a reference item", "argument")
    spos = C.xy_arg(ctx, "spos")
    yaw = ctx.args.get("yaw_deg")
    if dest in w.s["rooms"]:
        require(dest == w.robot["room"], "You can only arrange things in the current room")
        _release_if_held(ctx, eid)
        place_item(ctx, eid, None, None, None, ctx.args.get("cell"), None, ctx.args.get("orientation"))
        after_placement(ctx, eid)
        if yaw is not None:
            w.mut(eid)["yaw_deg"] = round(C.num(yaw) % 360.0, 2)
            w.mut(eid)["rot"] = C.rot_for_yaw(yaw)
        w.log_move(eid, "robot", dest)
        return "Arranged {} on the floor".format(eid)
    drec = ctx.obj(dest, touch=True)
    # references are aimed at as the robot sees them (an unresolved look-alike: its group's size, not its own)
    seen = C.perceived_view(ctx, ref, ctx.args.get("facing"))
    if ref and side and side not in ("on",):
        parent_dims = C.surface_wd(drec)
        if side == "center":
            iw, idp = C.footprint_wd(rec)
            spos = [round((parent_dims[0] - iw) / 2.0, 3), round((parent_dims[1] - idp) / 2.0, 3)]
            if ref != dest:
                rp = C.world_pose(seen, ref)
                local = C.surface_local(w, dest, rp[1], rp[2])
                spos = [round(local[0] - iw / 2.0, 3), round(local[1] - idp / 2.0, 3)]
        else:
            f, r = _frame_vectors(w, ctx.args.get("frame"), dest)
            vec = {"left": (-r[0], -r[1]), "right": r, "front": (-f[0], -f[1]), "behind": f}[side]
            rpose = C.world_pose(seen, ref)
            item_yaw = (C.world_pose(w, dest)[3] + C.num(yaw if yaw is not None else rec.get("yaw_deg", 0))) % 360.0
            dist = _extent_along(seen.s["entities"][ref], rpose[3], vec[0], vec[1]) + _extent_along(rec, item_yaw, vec[0], vec[1]) + C.num(ctx.args.get("gap_m"), 0.03)
            px, py = rpose[1] + vec[0] * dist, rpose[2] + vec[1] * dist
            local = C.surface_local(w, dest, px, py)
            iw, idp = C.footprint_wd(rec)
            spos = [round(local[0] - iw / 2.0, 3), round(local[1] - idp / 2.0, 3)]
    if ctx.args.get("facing"):
        target_pose = C.world_pose(seen, ctx.args["facing"])
        require(target_pose is not None, "Unknown facing target", "argument")
        feature = ctx.args.get("feature") or "front"
        offset = C.num((rec.get("features") or {}).get(feature, 0.0))
        parent_yaw = C.world_pose(w, dest)[3] if dest in w.s["entities"] else 0.0
        if spos is not None:
            sw, sd = C.surface_wd(drec)
            iw, idp = C.footprint_wd(rec)
            u, v = spos[0] + iw / 2.0 - sw / 2.0, spos[1] + idp / 2.0 - sd / 2.0
            du, dv = C.rotate_vec(u, v, parent_yaw)
            dpose = C.world_pose(w, dest)
            ix, iy = dpose[1] + du, dpose[2] + dv
        else:
            dpose = C.world_pose(w, dest)
            ix, iy = dpose[1], dpose[2]
        yaw = (C.bearing(ix, iy, target_pose[1], target_pose[2]) - offset - parent_yaw) % 360.0
    _release_if_held(ctx, eid)
    if side == "on":
        if drec.get("portable") and not drec.get("surface"):
            m = w.mut(eid)
            for key in ("spos", "pos", "shelf", "held_hand", "xy_m"):
                m.pop(key, None)
            tw, td = C.footprint_wd(drec)
            iw, idp = C.footprint_wd(rec)
            m["location"], m["relation"], m["spos"] = dest, "on", [round((tw - iw) / 2.0, 3), round((td - idp) / 2.0, 3)]
            if yaw is not None:
                m["yaw_deg"] = round(C.num(yaw) % 360.0, 2)
            w.log_move(eid, "robot", dest)
            settle_stack(ctx, eid)
            return "Arranged {} on {}".format(eid, dest)
        spos = None
    if spos is not None:
        require(isinstance(spos, list) and len(spos) == 2, "spos must be [x, y]", "argument")
        spot = geometry.surface_spot(w.s, dest, eid, want=spos)
        require(spot is not None, "That exact spot is occupied or off the edge", "precondition")
    place_item(ctx, eid, dest, None, spos, None, None, ctx.args.get("orientation"))
    if yaw is not None:
        w.mut(eid)["yaw_deg"] = round(C.num(yaw) % 360.0, 2)
    after_placement(ctx, eid)
    w.log_move(eid, "robot", dest)
    return "Arranged {} on {} at {}".format(eid, dest, w.ent(eid).get("spos"))


def _release_if_held(ctx, eid):
    if ctx.holding(eid):
        release_from_hands(ctx.w, eid)
        ctx.w.mut(eid).pop("held_hand", None)


@action("align", params={"target": "entity", "reference": "?target", "mode": {"type": "str", "default": "parallel",
                         "choices": ["parallel", "perpendicular", "center", "flush_front", "flush_back", "level", "square"]},
                         "offset_m": {"type": "number", "default": 0.0, "min": -2.0, "max": 2.0}, "lift": {"type": "bool", "default": True}},
        duration=4.0, energy=0.01, category="physical",
        doc="Align an object to a reference: parallel/perpendicular to another object's axis (rug to sofa, placemat to table edge), "
            "center it on its surface or on/under a reference, make its front/back edge flush with the surface edge (+offset_m), "
            "level a hung frame (mode=level, gravity), or square up the edges of a paper stack (mode=square).")
def align(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(not ctx.holding(eid), "Set it down first, then align it")
    mode = ctx.args["mode"]
    ref = ctx.args.get("reference")
    if mode == "level":
        m = w.mut(eid)
        m["tilt_deg"] = 0.0
        m["level"] = True
        return "Levelled {}".format(eid)
    if mode == "square":
        require(C.num(rec.get("sheets"), 0) > 0 or C.is_kind(rec, "papers", "paper_stack", "document", "stack_of_papers"), "Only stacks of sheets can be squared", "precondition")
        m = w.mut(eid)
        m["misalign_mm"] = 1.0
        m["edges_squared"] = True
        return "Squared the edges of {}".format(eid)
    loc = rec.get("location")
    if mode in ("parallel", "perpendicular"):
        require(ref, "Give a reference to align with", "argument")
        rpose = C.world_pose(w, ref)
        pose = C.world_pose(w, eid)
        require(rpose is not None and pose is not None and rpose[0] == pose[0], "The reference must be in the same room", "argument")
        target_world = rpose[3] + (90.0 if mode == "perpendicular" else 0.0)
        parent_yaw = (pose[3] - C.yaw_of(rec)) % 360.0
        cur = C.yaw_of(rec)
        options = [(target_world - parent_yaw) % 360.0, (target_world + 180.0 - parent_yaw) % 360.0]
        new = min(options, key=lambda y: C.ang_diff(y, cur))
        if loc in w.s["rooms"]:
            from .motion import rotate
            ctx.args.update({"yaw_deg": new, "by_deg": None, "face_toward": None, "lift": ctx.args.get("lift", True)})
            out = rotate(ctx)
            return "Aligned {} {} to {}: {}".format(eid, mode, ref, out)
        m = w.mut(eid)
        m["yaw_deg"] = round(new, 2)
        return "Aligned {} {} to {}".format(eid, mode, ref)
    require(loc in w.s["entities"] and rec.get("relation", "on") == "on" and isinstance(rec.get("spos"), list), "It must rest on a surface", "precondition")
    parent = w.ent(loc)
    sw, sd = C.surface_wd(parent)
    iw, idp = C.footprint_wd(rec)
    x, y = C.num(rec["spos"][0]), C.num(rec["spos"][1])
    off = C.num(ctx.args.get("offset_m"))
    if mode == "center":
        if ref and ref != loc:
            rpose = C.world_pose(C.perceived_view(ctx, ref), ref)      # as the robot sees it
            local = C.surface_local(w, loc, rpose[1], rpose[2])
            x, y = local[0] - iw / 2.0, local[1] - idp / 2.0
        else:
            x, y = (sw - iw) / 2.0, (sd - idp) / 2.0
    elif mode == "flush_front":
        y = sd - idp - off
    elif mode == "flush_back":
        y = off
    spot = geometry.surface_spot(w.s, loc, eid, want=[x, y])
    require(spot is not None, "Something is in the way of that position", "precondition")
    m = w.mut(eid)
    m["spos"] = spot
    m["edge_margin_m"] = edge_margin(w, eid)
    return "Aligned {} ({}) at {}".format(eid, mode, spot)

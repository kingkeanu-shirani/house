"""Reference skills for admin/inventory procedures. They route with house_sim.tasks.skills and
change state only through runner.do (every step is a real, guarded env.step).
"""
import json

from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as base


def _msg(r):
    try:
        return json.loads(r.last["message"])
    except (TypeError, ValueError, KeyError):
        return {}


def _crouch_if_low(r, eid):
    return base._crouch_if_low(r, eid)


def _stand(r):
    base._stand(r)


def _go(r, room):
    """Walk to `room` (core go_room: v2.3 room_route is cost-based and avoids locked doors the robot holds no key
    for, so admin skills no longer need their own unlocked-route search)."""
    here = r.state["robot"]["room"]
    if room is None or room == here or room not in r.state["rooms"]:
        return
    base.go_room(r, room)


def _to(r, target):
    """Go to the room holding target; the base skills then only work inside the room."""
    _go(r, base.locate(r.state, target))


def _approach(r, target):
    _to(r, target)
    base.approach(r, target)


def _fetch(r, obj):
    if obj not in [h for h in r.state["robot"]["hands"].values() if h]:
        _to(r, obj)
    return base.fetch(r, obj)


def _put(r, obj, dest, **kw):
    _fetch(r, obj)
    _go(r, dest if dest in r.state["rooms"] else base.locate(r.state, dest))
    return base.put(r, obj, dest, **kw)


def _parts(r, printer):
    from .office import parts
    return parts(r.env.world, printer)


@skill("admin_inventory.load_paper")
def load_paper(r, printer, ream, sheets=50, guides="snug"):
    """Open the tray, take sheets from a ream, load them, set the guides and close the tray."""
    tray = _parts(r, printer)["paper_tray"]
    base.approach(r, tray)
    if not r.state["entities"][tray].get("open"):
        r.do("open", target=tray)
    origin = r.state["entities"][ream].get("location")
    shelf = r.state["entities"][ream].get("shelf")
    base.fetch(r, ream)
    if not r.state["entities"][ream].get("open"):
        r.do("open", target=ream)
    r.do("take_unit", target=ream, count=int(sheets))
    stack = _msg(r)["took"]
    base.approach(r, tray)
    r.do("place", target=stack, destination=tray)
    if origin in r.state["entities"]:
        base.put(r, ream, origin, shelf=shelf)
    base.approach(r, tray)
    if (r.state["entities"][tray].get("settings") or {}).get("guides") != guides:
        r.do("set", target=tray, setting="guides", value=guides)
    r.do("close", target=tray)
    return stack


@skill("admin_inventory.clear_jam")
def clear_jam(r, printer, dispose=None):
    base.approach(r, printer)
    if not r.state["entities"][printer].get("cover_open"):
        r.do("open_cover", target=printer)
    r.do("uninstall", target=printer, slot="paper_path")
    jam = next(h for h in r.state["robot"]["hands"].values() if h and r.state["entities"][h].get("part_type") == "jammed_paper")
    r.do("close_cover", target=printer)
    if dispose:
        base.put(r, jam, dispose)
    return jam


@skill("admin_inventory.print_and_collect")
def print_and_collect(r, printer, file, copies=1, duplex=False, color=False, pages=None, deliver_to=None, max_wait_s=900):
    args = {"target": printer, "file": file, "copies": copies, "duplex": duplex, "color": color}
    if pages:
        args["pages"] = pages
    r.do("print", **args)
    job = _msg(r).get("queued")
    waited = 0
    while waited < max_wait_s and any(j.get("id") == job for j in r.state["entities"][printer].get("queue") or []):
        r.do("wait", seconds=15)
        waited += 15
    out = _parts(r, printer).get("output_tray")
    printed = sorted(c for c, rec in r.state["entities"].items() if rec.get("location") == out and rec.get("job_id") == job)
    for doc in printed:
        if deliver_to:
            if deliver_to in r.state.get("people", {}):
                base.give(r, doc, deliver_to)
            else:
                base.put(r, doc, deliver_to)
    return printed


@skill("admin_inventory.restock_roll")
def restock_roll(r, holder, roll, core_to=None, orientation=None):
    """Swap an empty core for a new roll in the household's orientation (default: keep the previous one)."""
    state = r.state
    orient = orientation or state["entities"][holder].get("roll_orientation") or "over"
    current = (state["entities"][holder].get("slots") or {}).get("roll", {}).get("item")
    if current:
        base.approach(r, holder)
        crouched = _crouch_if_low(r, holder)
        r.do("uninstall", target=holder, slot="roll")
        if crouched:
            _stand(r)
        if core_to:
            base.put(r, current, core_to)
    pack = state["entities"][roll].get("location")
    if pack in state["entities"] and state["entities"][pack].get("multipack") and not state["entities"][pack].get("open"):
        base.approach(r, pack)
        crouched = _crouch_if_low(r, pack)
        r.do("open", target=pack)
        if crouched:
            _stand(r)
    base.fetch(r, roll)
    base.approach(r, holder)
    crouched = _crouch_if_low(r, holder)
    r.do("mount_roll", target=roll, destination=holder, orientation=orient)
    if crouched:
        _stand(r)


def _set_down_by(r, item, dispenser):
    """Put a held item down next to the dispenser: on its surface/open container, else on another surface within
    arm's reach (a crowded counter run continues), else on the floor within reach (core place picks the cell)."""
    state = r.state
    parent = state["entities"][dispenser].get("location")
    prec = state["entities"].get(parent) or {}
    if prec.get("surface") and geometry.surface_spot(state, parent, item) is not None:
        r.do("place", target=item, destination=parent)
        return
    open_container = prec.get("container") and prec.get("accepts_items") and (not prec.get("openable") or prec.get("open"))
    if open_container and geometry.fits_inside((state["entities"][item].get("size_m") or [0.1, 0.1, 0.1]), prec.get("inner_size_m")):
        r.do("place", target=item, destination=parent)
        return
    room = state["robot"]["room"]
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if eid in (item, dispenser) or not rec.get("surface") or not rec.get("accepts_items") or rec.get("location") != room:
            continue
        if geometry.reach_problem(state, eid) is None and geometry.surface_spot(state, eid, item) is not None:
            r.do("place", target=item, destination=eid)
            return
    r.do("place", target=item)


@skill("admin_inventory.refill_dispenser")
def refill_dispenser(r, dispenser, refill, funnel=None, return_to=None):
    """Carry the refill capped (an open bottle sloshes when walked), set the funnel down by the dispenser,
    open both at the dispenser, pour through the funnel, recap both before carrying anything away."""
    _fetch(r, refill)
    if r.state["entities"][refill].get("open"):
        r.do("close", target=refill)
    _approach(r, dispenser)
    if funnel and funnel not in r.env.world.held_items() and geometry.reach_problem(r.state, funnel) is not None:
        # one hand stays free for doors: park the refill, fetch the funnel, come back for the refill
        _set_down_by(r, refill, dispenser)
        _fetch(r, funnel)
        _approach(r, dispenser)
        _set_down_by(r, funnel, dispenser)
        r.do("pick_up", target=refill)
    if funnel and funnel in r.env.world.held_items():
        _set_down_by(r, funnel, dispenser)
    if not r.state["entities"][dispenser].get("open"):
        r.do("open", target=dispenser)
    r.do("open", target=refill)
    args = {"target": dispenser, "source": refill}
    if funnel:
        args["funnel"] = funnel
    r.do("refill", **args)
    if funnel and funnel in r.env.world.held_items():
        _set_down_by(r, funnel, dispenser)
    r.do("close", target=dispenser)
    r.do("close", target=refill)
    if return_to:
        _put(r, refill, return_to)
        if funnel and r.state["entities"][funnel].get("location") != return_to:
            _put(r, funnel, return_to)


@skill("admin_inventory.shred_documents")
def shred_documents(r, documents, shredder):
    state = r.state
    if not state["entities"][shredder].get("on"):
        base.approach(r, shredder)
        r.do("switch_on", target=shredder)
    for doc in documents:
        base.fetch(r, doc)
        base.approach(r, shredder)
        r.do("shred", target=doc, destination=shredder)
    base.approach(r, shredder)
    r.do("switch_off", target=shredder)


@skill("admin_inventory.file_documents")
def file_documents(r, pairs, cabinet=None):
    """pairs: [[doc, folder], ...]; opens the cabinet once and closes it at the end."""
    if cabinet:
        base.approach(r, cabinet)
        if not r.state["entities"][cabinet].get("open"):
            r.do("open", target=cabinet)
    for doc, folder in pairs:
        base.fetch(r, doc)
        base.approach(r, folder)
        crouched = _crouch_if_low(r, folder)
        r.do("file", target=doc, destination=folder)
        if crouched:
            _stand(r)
    if cabinet:
        base.approach(r, cabinet)
        r.do("close", target=cabinet)


@skill("admin_inventory.sort_into")
def sort_into(r, assignments):
    """assignments: {item: destination} placed one by one (fetch + put)."""
    for item in sorted(assignments):
        base.put(r, item, assignments[item])


@skill("admin_inventory.count_scopes")
def count_scopes(r, scopes, what, unit="units", include_in_use=False):
    """Count `what` in each scope (opening closed containers first); returns the total."""
    total = 0
    for scope in scopes:
        state = r.state
        if scope in state["entities"]:
            _to(r, scope)
            base.reveal(r, scope)
            rec = state["entities"][scope]
            if rec.get("openable") and not rec.get("open") and not rec.get("transparent"):
                base.approach(r, scope)
                crouched = _crouch_if_low(r, scope)
                r.do("open", target=scope)
                if crouched:
                    _stand(r)
            room = base.locate(state, scope)
            if room != r.state["robot"]["room"]:
                _go(r, room)
        elif scope != r.state["robot"]["room"]:
            _go(r, scope)
        r.do("count", target=scope, what=what, unit=unit, include_in_use=include_in_use)
        total += int(_msg(r).get("count", 0))
    return total


@skill("admin_inventory.wheel_to")
def wheel_to(r, cart, room, cell, orientation="upright"):
    """Open doors along the way first (both hands are on the cart), wheel it to room and stand it at cell."""
    state = r.state
    start = base.locate(state, cart)
    route = base.room_route(state, start, room) or []
    previous = start
    for nxt, cid in route:
        door = state["connections"][cid].get("door")
        if door and not r.state["entities"][door].get("open"):
            _go(r, previous)
            base.approach(r, door)
            if r.state["entities"][door].get("locked"):
                r.do("unlock", target=door)
            r.do("open", target=door)
        previous = nxt
    _approach(r, cart)
    if r.state["entities"][cart].get("open"):
        r.do("close", target=cart)
    r.do("wheel_cart", target=cart)
    _go(r, room)
    r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
    r.do("place", target=cart, destination=room, cell=[int(cell[0]), int(cell[1])], orientation=orientation)


@skill("admin_inventory.curb_cell")
def curb_cell(r, cart, room=None):
    """A free curb cell >= 2 cells from other carts, the mailbox and the driveway apron (routing helper, no state
    change). Cells where the cart would cut the walk between the curb room's doorways (a narrow sidewalk) are used
    only when no other cell qualifies (kernel access rule: geometry.access_report / not_worse)."""
    from .curb import curb_obstacle, curb_room
    state = r.state
    room = room or curb_room(state)
    cols, rows = geometry.grid_size(state["rooms"][room])
    blocked = set(geometry.blocked_cells(state, room, ignore=(cart,)))
    static = set(geometry.blocked_cells(state, room, ignore=(cart,), include_agents=False))
    doors = {k: v for k, v in geometry.access_targets(state, room, level="doors", ignore=(cart,)).items() if k[0:1] == ("door",)}
    before = geometry.access_report(state, room, static, doors)
    avoid = []
    for eid, rec in sorted(state["entities"].items()):
        if eid != cart and rec.get("location") == room and rec.get("pos") and curb_obstacle(rec):
            avoid.extend(geometry.footprint(rec))
    for conn in state.get("connections", {}).values():
        if room in conn.get("cells", {}):
            avoid.append(tuple(conn["cells"][room]))
    w_cells, d_cells = geometry.footprint_size(dict(state["entities"][cart], rot=0))
    fallback = None
    for y in range(rows - d_cells, -1, -1):
        for x in range(cols - w_cells + 1):
            cells = [(x + i, y + j) for i in range(w_cells) for j in range(d_cells)]
            if any(c in blocked for c in cells):
                continue
            if any(geometry.chebyshev(c, a) < 3 for c in cells for a in avoid):
                continue
            if geometry.not_worse(before, geometry.access_report(state, room, static | set(cells), doors)):
                return [x, y]
            if fallback is None:
                fallback = [x, y]
    return fallback

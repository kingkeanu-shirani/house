"""Reference skills for appliance and cooking task specs. They route with house_sim.tasks.skills and change state only
through runner.do (env.step); decisions use what the robot can observe (visible fields, inspect/read output), routing may
use privileged state like every reference skill.

  appliances_cooking.wear_mitts(mitts)                       fetch and put on oven mitts (no-op if worn)
  appliances_cooking.remove_mitts(mitts, home=None)          take them off and put them back
  appliances_cooking.preheat_oven(oven, temp, unit=None, mode="bake", park=None, wait=True)
                                                             opens the door to look; cavity items go to `park` or the
                                                             nearest surface with room (park_surface)
  appliances_cooking.oven_in(dish, oven, rack, mitts=None)   dish onto a rack of a hot oven (mitts on, door shut after)
  appliances_cooking.oven_out(dish, oven, dest, mitts=None, turn_off=True)
  appliances_cooking.load_dishwasher(dishwasher, items, aside=None, close=True)
  appliances_cooking.unload_dishwasher(dishwasher, homes=None, default_home=None, aside=None)
  appliances_cooking.start_dishwasher(dishwasher, program=None, pod=None, tub=None, delay_h=0)
  appliances_cooking.ignite_burner(burner, level="medium", hold_s=4, retries=2)
  appliances_cooking.pan_on_burner(pan, burner, handle="in")
  appliances_cooking.stove_off(range, keep=())
  appliances_cooking.shut_fridge(fridge, freezer=None, shelf_for=None)
  appliances_cooking.defrost_freezer(freezer, cooler, tray, towels=(), restock=True)
  appliances_cooking.reheat_in_microwave(item, microwave, seconds, power=10, dest=None, mitts=None)
  appliances_cooking.toast(toaster, slices, shade=4, dest=None)
  appliances_cooking.watch_pot(pot, burner, done_c, low="low", poll_s=15, max_s=2400, tell=None)
"""
import json

from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as S
from . import common as C
from . import dishwasher as DW
from .cold import unit_contents


def _err(message):
    from ...tasks.runner import PlanError
    return PlanError(message)


def _ent(r, eid):
    return r.state["entities"].get(eid) or {}


def _worn(r, eid):
    rec = _ent(r, eid)
    return rec.get("location") == "robot" and rec.get("relation") == "worn"


def _held(r):
    return [v for v in dict.fromkeys(r.state["robot"]["hands"].values()) if v]


def _crouch_for(r, eid):
    if eid in r.state["entities"] and geometry.height_m(r.state, eid) < geometry.LOW_REACH_M and r.state["robot"].get("posture") == "standing":
        r.do("crouch")
        return True
    return False


def _stand(r):
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")


@skill("appliances_cooking.wear_mitts")
def wear_mitts(r, mitts):
    if _worn(r, mitts):
        return mitts
    S.fetch(r, mitts)
    r.do("wear", target=mitts)
    return mitts


@skill("appliances_cooking.remove_mitts")
def remove_mitts(r, mitts, home=None):
    if not _worn(r, mitts):
        return
    r.do("take_off", target=mitts)
    if home:
        S.put(r, mitts, home)


PARK_WORDS = ("counter", "island", "table", "cart")
PARK_SKIP = ("bedside", "night", "coffee", "side_table", "outdoor", "patio")
PARK_MIN_HEIGHT_M = 0.6


def _surface_room(state, eid):
    room, _ = geometry.anchor(state, eid)
    return room or S.locate(state, eid)


def _park_surfaces(state, near):
    """Work surfaces (counters, islands, kitchen/dining tables, carts at work height; never an appliance top) the
    robot can walk up to: in the room of `near` first, then the adjoining indoor rooms; counters before tables,
    nearest first."""
    room = _surface_room(state, near)
    rooms = state.get("rooms") or {}
    neighbours = sorted({x for conn in (state.get("connections") or {}).values() if room in (conn.get("rooms") or [])
                         for x in conn.get("rooms") or [] if x != room and not (rooms.get(x) or {}).get("outdoor")})
    _, near_cells = geometry.anchor(state, near)
    near_cells = near_cells or []
    out = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        t = str(rec.get("type") or "")
        if not rec.get("surface") or not rec.get("accepts_items") or rec.get("ac_kind") or rec.get("portable"):
            continue
        if not any(w in t for w in PARK_WORDS) or any(w in t for w in PARK_SKIP):
            continue
        if C.num(rec.get("surface_height_m"), 0.9) < PARK_MIN_HEIGHT_M:
            continue
        where = _surface_room(state, eid)
        if where not in [room] + neighbours or not C.approachable(state, eid):
            continue
        _, cells = geometry.anchor(state, eid)
        dist = min((geometry.chebyshev(a, b) for a in (cells or []) for b in near_cells), default=99) if where == room else 99
        out.append((where != room, 0 if ("counter" in t or "island" in t) else 1, dist, eid))
    return [e for _, _, _, e in sorted(out)]


def park_surface(r, item, near, prefer=None):
    """Where to set `item` down for a while: `prefer` when it has a clear patch big enough for the item, else the
    nearest work surface that has one (what the robot sees on top of it -- a full counter is visibly full), else the
    floor of the room of `near` as the last resort. Never assumes a particular counter has room."""
    state = r.state
    if prefer and (prefer in state.get("rooms", {}) or prefer in state.get("people", {})):
        return prefer
    pref = state["entities"].get(prefer) if prefer else None
    if pref is not None and pref.get("container") and not pref.get("surface"):
        return prefer                                # going in, not on: the place verb judges the room inside
    candidates = ([prefer] if pref is not None else []) + [s for s in _park_surfaces(state, near) if s != prefer]
    for s in candidates:
        if s != item and geometry.surface_spot(state, s, item) is not None:
            return s
    return _surface_room(state, near)


@skill("appliances_cooking.preheat_oven")
def preheat_oven(r, oven, temp, unit=None, mode="bake", park=None, wait=True, max_s=2400, check=True):
    """Look inside first (open the door: a shut oven hides its cavity), move anything stored there (pans, a plastic
    tub) to `park` -- or, when that is not given or has no room, the nearest counter/table with a clear patch (floor of
    the kitchen as the last resort) -- close the door, preheat, wait for the beep. check=False skips looking inside
    (the classic mistake)."""
    S.approach(r, oven)
    if check and not _ent(r, oven).get("open"):
        r.do("open", target=oven)                    # a cavity check means looking in, not reading hidden state
    stored = [c for c in C.descendants(r.state, oven) if _ent(r, c).get("ac_kind") != "oven_rack"] if check else []
    for item in stored:
        chain = C.parent_chain(r.state, item) if item in r.state["entities"] else []
        if oven not in chain or any(p in stored for p in chain):
            continue                                 # gone already, or it comes out with its container
        dest = park_surface(r, item, oven, prefer=park)
        crouched = _crouch_for(r, item)
        r.do("pick_up", target=item)
        if crouched:
            _stand(r)
        S.put(r, item, dest)
        S.approach(r, oven)
    if _ent(r, oven).get("open"):
        r.do("close", target=oven)
    args = {"target": oven, "temp": float(temp), "mode": mode}
    if unit:
        args["unit"] = unit
    r.do("preheat", **args)
    if wait:
        waited = 0
        while not _ent(r, oven).get("preheated") and waited < max_s:
            r.do("wait", seconds=60)
            waited += 60
        if not _ent(r, oven).get("preheated"):
            raise _err("oven did not preheat")


@skill("appliances_cooking.oven_in")
def oven_in(r, dish, oven, rack, mitts=None):
    if mitts:
        wear_mitts(r, mitts)
    S.fetch(r, dish)
    S.approach(r, oven)
    if not _ent(r, oven).get("open"):
        r.do("open", target=oven)
    crouched = _crouch_for(r, rack)
    r.do("place", target=dish, destination=rack)
    if crouched:
        _stand(r)
    r.do("close", target=oven)


@skill("appliances_cooking.oven_out")
def oven_out(r, dish, oven, dest, mitts=None, turn_off=True, keep_mitts=False, mitts_home=None):
    if mitts:
        wear_mitts(r, mitts)
    S.approach(r, oven)
    if not _ent(r, oven).get("open"):
        r.do("open", target=oven)
    crouched = _crouch_for(r, dish)
    r.do("pick_up", target=dish)
    if crouched:
        _stand(r)
    r.do("close", target=oven)
    if turn_off and (_ent(r, oven).get("settings") or {}).get("mode", "off") != "off":
        r.do("set", target=oven, setting="mode", value="off")
    r.do("speak", text="Careful, hot dish coming through.")       # warn before walking with it
    S.approach(r, dest)
    r.do("place", target=dish, destination=dest)
    if mitts and not keep_mitts:
        remove_mitts(r, mitts, home=mitts_home)


def plan_slot(rec, counters):
    """(rack kind, position, orientation, compartment) for an item per standard loading rules (from visible props)."""
    cls = DW.dw_class(rec)
    ok, consequence, top_only = C.dishwasher_verdict(rec)
    if not ok:
        return None
    if cls == "plate":
        return "bottom", "middle", "upright", None
    if cls in ("cup", "stemware"):
        return "top", "middle", "upside_down", None
    if cls == "bowl":
        return "top", "back", "angled", None
    if cls == "plastic" or top_only:
        return "top", "front", "upside_down", None
    if cls == "cutlery":
        sharp = "sharp" in (rec.get("hazards") or [])
        counters["cutlery"] = counters.get("cutlery", 0) + 1
        return "basket", None, "handle_up" if sharp else "handle_down", 1 + (counters["cutlery"] - 1) % 6
    if cls == "long_utensil":
        return "top", "front", "flat", None
    if cls == "large":
        counters["large"] = counters.get("large", 0) + 1
        orient = "upright" if sorted(C.size3(rec))[0] < 0.06 else "upside_down"
        return "bottom", "left" if counters["large"] % 2 else "right", orient, None
    return "top", "middle", "upside_down", None


def _racks(r, dw):
    out = {}
    for c in C.descendants(r.state, dw):
        rec = _ent(r, c)
        if rec.get("ac_kind") == "dw_rack":
            out[rec.get("rack_kind")] = c
    return out


@skill("appliances_cooking.load_dishwasher")
def load_dishwasher(r, dishwasher, items, aside=None, close=True, unload_clean_to=None):
    """Load items by rack rules; unsuitable items go `aside`. If the machine holds a clean load it is unloaded first."""
    if _ent(r, dishwasher).get("load_state") == "clean":
        if not unload_clean_to:
            raise _err("dishwasher holds clean dishes and no destination was given")
        unload_dishwasher(r, dishwasher, default_home=unload_clean_to, aside=aside or unload_clean_to)
    counters = {}
    for item in items:
        rec = _ent(r, item)
        slot = plan_slot(rec, counters)
        if slot is None:
            if aside:
                S.put(r, item, aside)
            continue
        kind, pos, orient, comp = slot
        racks = _racks(r, dishwasher)
        dest = racks.get(kind) or racks.get("bottom")
        S.fetch(r, item)
        S.approach(r, dishwasher)
        if not _ent(r, dishwasher).get("open"):
            r.do("open", target=dishwasher)
        args = {"target": item, "destination": dest, "orientation": orient}
        if pos:
            args["position"] = pos
        if comp:
            args["compartment"] = comp
        r.do("load", **args)
    if close and _ent(r, dishwasher).get("open"):
        S.approach(r, dishwasher)
        r.do("close", target=dishwasher)


@skill("appliances_cooking.unload_dishwasher")
def unload_dishwasher(r, dishwasher, homes=None, default_home=None, aside=None, wait_cool=True, ignore_homes=False):
    """Bottom rack (and basket) first, then the top rack; dirty items aside; everything else to its home."""
    homes = homes or {}
    S.approach(r, dishwasher)
    if not _ent(r, dishwasher).get("open"):
        r.do("open", target=dishwasher)
    if wait_cool:
        waited = 0
        while any(C.num(_ent(r, i).get("temperature_c"), 21.0) > 48.0 for i in DW.load_items(r.state, dishwasher)) and waited < 3600:
            r.do("wait", seconds=120)
            waited += 120
    racks = _racks(r, dishwasher)
    order = []
    for kind in ("bottom", "basket", "top"):
        rack = racks.get(kind)
        if rack:
            order += sorted(c for c in C.descendants(r.state, rack) if _ent(r, c).get("dw") is not None and not _ent(r, c).get("ac_kind"))
    for item in order:
        if item not in r.state["entities"]:
            continue
        rec = _ent(r, item)
        dirty = DW.soil_total(rec) > 0.05
        dest = aside if dirty and aside else (default_home if ignore_homes else homes.get(item) or rec.get("home") or default_home)
        if dest is None:
            raise _err("no home for " + item)
        S.approach(r, dishwasher)
        S.approach(r, item)
        crouched = _crouch_for(r, item)
        r.do("pick_up", target=item)
        if crouched:
            _stand(r)
        S.put(r, item, dest)
    S.approach(r, dishwasher)
    if _ent(r, dishwasher).get("open"):
        r.do("close", target=dishwasher)


@skill("appliances_cooking.start_dishwasher")
def start_dishwasher(r, dishwasher, program=None, pod=None, tub=None, delay_h=0, close_tub=True):
    cup = next((c for c in C.children(r.state, dishwasher) if _ent(r, c).get("ac_kind") == "detergent_cup"), None)
    if pod is None and tub is not None:
        S.reveal(r, tub)
        S.approach(r, tub)
        if not _ent(r, tub).get("open"):
            r.do("open", target=tub)
        pod = next((c for c in C.children(r.state, tub) if _ent(r, c).get("type") == "dishwasher_pod"), None)
        if pod is None:
            raise _err("no pods left")
        S._crouch_if_low(r, pod)
        r.do("pick_up", target=pod)
        if close_tub:
            r.do("close", target=tub)
            parent = _ent(r, tub).get("location")
            if _ent(r, parent).get("openable") and _ent(r, parent).get("open"):
                r.do("close", target=parent)
        S._stand(r)
    if pod is not None:
        S.fetch(r, pod)
        S.approach(r, dishwasher)
        if not _ent(r, dishwasher).get("open"):
            r.do("open", target=dishwasher)
        S._crouch_if_low(r, cup)
        if not _ent(r, cup).get("open"):
            r.do("open", target=cup)
        r.do("place", target=pod, destination=cup)
        r.do("close", target=cup)
        S._stand(r)
    S.approach(r, dishwasher)
    if _ent(r, dishwasher).get("open"):
        r.do("close", target=dishwasher)
    if program and (_ent(r, dishwasher).get("settings") or {}).get("program") != program:
        r.do("set", target=dishwasher, setting="program", value=program)
    if delay_h:
        r.do("set", target=dishwasher, setting="delay_h", value=int(delay_h))
    r.do("start", target=dishwasher)


@skill("appliances_cooking.ignite_burner")
def ignite_burner(r, burner, level="medium", hold_s=4.0, retries=2):
    S.approach(r, burner)
    for attempt in range(int(retries) + 1):
        r.do("ignite", target=burner, level=level, hold_s=float(hold_s))
        if (_ent(r, burner).get("burner") or {}).get("lit"):
            return True
        r.do("set", target=burner, setting="heat", value="off")
        r.do("wait", seconds=60)
    raise _err("burner would not light")


@skill("appliances_cooking.pan_on_burner")
def pan_on_burner(r, pan, burner, handle="in"):
    S.fetch(r, pan)
    S.approach(r, burner)
    r.do("load", target=pan, destination=burner, handle=handle)


@skill("appliances_cooking.stove_off")
def stove_off(r, range_id, keep=()):
    for b in sorted(c for c in C.children(r.state, range_id) if _ent(r, c).get("ac_kind") == "burner"):
        if b in keep:
            continue
        if (_ent(r, b).get("settings") or {}).get("heat", "off") != "off":
            S.approach(r, b)
            r.do("set", target=b, setting="heat", value="off")


@skill("appliances_cooking.shut_fridge")
def shut_fridge(r, fridge, freezer=None, shelf=1):
    for unit in [u for u in (fridge, freezer) if u]:
        S.approach(r, unit)
        if not _ent(r, unit).get("open"):
            r.do("open", target=unit)
        r.do("inspect", target=unit, where="closely")
        for c in unit_contents(r.state, unit):
            rec = _ent(r, c)
            if rec.get("protrudes"):
                S.approach(r, unit)
                crouched = _crouch_for(r, c)
                r.do("pick_up", target=c)
                if crouched:
                    _stand(r)
                heights = _ent(r, unit).get("shelf_heights")
                args = {"target": c, "destination": unit, "relation": "in", "orientation": "on_side"}
                if heights:
                    args["shelf"] = min(int(shelf), len(heights) - 1)
                crouched = _crouch_for(r, unit) if not heights else False
                r.do("place", **args)
                if crouched:
                    _stand(r)
            if rec.get("ac_kind") == "gasket" and not (rec.get("settings") or {}).get("seated", True):
                r.do("set", target=c, setting="seated", value=True)
        r.do("close", target=unit)


@skill("appliances_cooking.defrost_freezer")
def defrost_freezer(r, freezer, cooler, tray, towels=(), restock=True, max_wait_s=6 * 3600):
    S.approach(r, freezer)
    if not _ent(r, freezer).get("open"):
        r.do("open", target=freezer)
    foods = [c for c in C.descendants(r.state, freezer) if C.is_food(_ent(r, c))]
    for f in foods:
        S.approach(r, freezer)
        crouched = _crouch_for(r, f)
        r.do("pick_up", target=f)
        if crouched:
            _stand(r)
        S.put(r, f, cooler)
    S.approach(r, freezer)
    if _ent(r, freezer).get("on", True) and _ent(r, freezer).get("switchable"):
        r.do("switch_off", target=freezer)
    S.fetch(r, tray)
    S.approach(r, freezer)
    heights = _ent(r, freezer).get("shelf_heights")
    args = {"target": tray, "destination": freezer, "relation": "in"}
    if heights:
        args["shelf"] = 0
    crouched = _crouch_for(r, freezer) if heights else False
    r.do("place", **args)
    if crouched:
        _stand(r)
    for t in towels:
        S.fetch(r, t)
        S.approach(r, freezer)
        r.do("place", target=t, destination=r.state["robot"]["room"])
    S.approach(r, freezer)
    r.do("defrost", target=freezer, method="passive")
    waited = 0
    while C.num(_ent(r, freezer).get("frost_mm")) > 0.05 and waited < max_wait_s:
        r.do("wait", seconds=900)
        waited += 900
    S.approach(r, freezer)
    r.do("close", target=freezer)
    r.do("switch_on", target=freezer)
    waited = 0
    while C.num(_ent(r, freezer).get("internal_temp_c"), 0.0) > -15.0 and waited < max_wait_s:
        r.do("wait", seconds=900)
        waited += 900
    if restock:
        S.approach(r, freezer)
        r.do("open", target=freezer)
        for f in foods:
            S.fetch(r, f)
            S.approach(r, freezer)
            args = {"target": f, "destination": freezer, "relation": "in"}
            if heights:
                args["shelf"] = 1 if len(heights) > 1 else 0
            r.do("place", **args)
        r.do("close", target=freezer)


@skill("appliances_cooking.reheat_in_microwave")
def reheat_in_microwave(r, item, microwave, seconds, power=10, dest=None, mitts=None, stand_s=60):
    S.fetch(r, item)
    S.approach(r, microwave)
    if not _ent(r, microwave).get("open"):
        r.do("open", target=microwave)
    r.do("place", target=item, destination=microwave)
    r.do("close", target=microwave)
    r.do("set", target=microwave, setting="power", value=int(power))
    r.do("set", target=microwave, setting="time_s", value=int(seconds))
    r.do("start", target=microwave)
    waited = 0
    while (_ent(r, microwave).get("appliance") or {}).get("state") == "running" and waited < seconds + 120:
        r.do("wait", seconds=30)
        waited += 30
    if stand_s:
        r.do("wait", seconds=int(stand_s))
    if dest:
        if mitts:
            wear_mitts(r, mitts)
        S.approach(r, microwave)
        r.do("open", target=microwave)
        r.do("pick_up", target=item)
        r.do("close", target=microwave)
        if any(C.num(_ent(r, x).get("temperature_c"), 21.0) > 60.0 for x in [item] + C.descendants(r.state, item)):
            r.do("speak", text="Careful, hot food coming through.")
        S.put(r, item, dest)
        if mitts:
            remove_mitts(r, mitts)


@skill("appliances_cooking.toast")
def toast(r, toaster, slices, shade=4, dest=None):
    for s in slices:
        S.fetch(r, s)
        S.approach(r, toaster)
        r.do("load", target=s, destination=toaster)
    r.do("set", target=toaster, setting="shade", value=int(shade))
    r.do("start", target=toaster)
    waited = 0
    while _ent(r, toaster).get("lever") == "down" and waited < 600:
        r.do("wait", seconds=30)
        waited += 30
    if dest:
        r.do("wait", seconds=30)
        for s in slices:
            S.approach(r, toaster)
            r.do("unload", target=s)
            S.put(r, s, dest)


@skill("appliances_cooking.watch_pot")
def watch_pot(r, pot, burner, done_c, low="low", poll_s=15, max_s=2400, tell=None, off_when_done=True):
    """Stay with the pot: turn the heat down when foam rises, turn it off at done_c, tell the person."""
    S.approach(r, pot)
    waited = 0
    lowered = False
    while waited < max_s:
        rec = _ent(r, pot)
        if C.num(rec.get("temperature_c"), 21.0) >= done_c:
            break
        if rec.get("foam_level") in ("rising", "near_rim") and not lowered:
            r.do("set", target=burner, setting="heat", value=low)
            lowered = True
        r.do("wait", seconds=int(poll_s))
        waited += poll_s
    if off_when_done:
        r.do("set", target=burner, setting="heat", value="off")
    if tell:
        S.approach(r, tell)
        r.do("speak", text="It's ready - I turned the burner off.", to=tell)

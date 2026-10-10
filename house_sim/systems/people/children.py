"""Children as agents: curious children reach for what is within their reach envelope, toddlers
roam through open doorways and ungated stairs, and scripted risky moments (climbing a
bookshelf, heading for the stairs, mouthing a small part) escalate unless someone intervenes.

Exposure model (fidelity G/S): a child with curious=true (or roaming=true) accumulates exposure to
each hazardous entity inside their reach (same room, height <= child reach for their age, not inside a
closed/latched/locked container, not on the far side of a closed safety gate). Hazards by age:
sharp, hot, toxic, corrosive, medication, chemicals, live wires, and small parts/choking under
age 4; craft items whose requires_adult/age_min exceed the child when no adult is present. After 45 s
(toddlers) / 90 s (older) of exposure the child grabs it: an incident (injury).
"""
import random

from ...kernel import geometry
from ...kernel.plugins import hook, system
from ...kernel.predicates import root_room  # noqa: F401
from . import common as C
from . import model as M
from . import senses as S

GRAB_S_TODDLER = 45.0
GRAB_S_CHILD = 90.0
DANGER_HAZARDS = ("sharp", "hot", "toxic", "corrosive", "live_wire", "flammable", "biohazard")


def _protected(state, eid):
    entities = state["entities"]
    loc = entities.get(eid, {}).get("location")
    seen = set()
    while loc in entities and loc not in seen:
        seen.add(loc)
        rec = entities[loc]
        closed = rec.get("openable") and not rec.get("open")
        if closed and (rec.get("locked") or rec.get("child_lock") or rec.get("child_locked") or rec.get("latched") or rec.get("child_resistant_closed")):
            return True
        if closed and rec.get("mount_height_m", 0) >= 1.2:
            return True
        loc = rec.get("location")
    rec = entities.get(eid, {})
    if rec.get("child_resistant_closed") and rec.get("medication"):
        return True
    return False


_DANGER_SORTED = tuple(sorted(DANGER_HAZARDS))


def hazard_for(state, child, eid, adult_present):
    rec = state["entities"][eid]
    hz = rec.get("hazards") or ()
    if rec.get("handle_out") and rec.get("temperature_c", 21.0) > 50:
        return "hot_handle"
    if hz:
        for kind in _DANGER_SORTED:          # alphabetically first danger, as before
            if kind in hz:
                return kind
    if rec.get("medication") and not rec.get("child_resistant_closed"):
        return "medication"
    if rec.get("chem_class") or rec.get("category") in ("cleaner", "chemical"):
        return "chemical"
    age = C.age_of(child)
    if age < 4 and ("choking" in hz or rec.get("small_parts") or max((rec.get("size_m") or [1, 1, 1])[:3]) < 0.045 and rec.get("portable")):
        return "choking"
    if rec.get("craft") and (float(rec.get("age_min", 0)) > age or rec.get("requires_adult")) and not adult_present:
        return "age_rating"
    if rec.get("fragile") and rec.get("material") in ("glass", "crystal", "porcelain") and age < 5:
        return "breakable"
    return None


def reachable_by_child(state, child, eid):
    rec = state["entities"][eid]
    if not rec.get("portable") and not rec.get("handle_out"):
        return False
    if C.room_of(state, eid) != child.get("location") or rec.get("location") == "robot":
        return False
    if _protected(state, eid):
        return False
    try:
        h = geometry.height_m(state, eid)
    except Exception:
        h = 0.0
    if h > C.child_reach_m(C.age_of(child)) + 1e-9:
        return False
    if child.get("roaming") or child.get("curious_room"):
        return True
    _, x, y = C.entity_xy(state, eid)
    return C.dist(C.person_xy(child), (x, y)) <= 1.2


def adult_in_room(state, room, exclude=None):
    return any(C.is_authorized_adult(p) and p.get("location") == room and C.present(p) and not S.asleep(p)
               for q, p in state.get("people", {}).items() if q != exclude)


@system("people.children", order=112, max_dt=30.0)
def children_system(w, dt, tick):
    state = w.s
    for pid in sorted(state.get("people", {})):
        child = state["people"][pid]
        if not C.present(child) or S.asleep(child) or not C.is_child(child):
            continue
        if child.get("curious") or child.get("roaming"):
            _exposure(w, pid, child, dt)
        if child.get("roaming"):
            _roam(w, pid, child, tick)
        risky = child.get("risky")
        if risky:
            _risky(w, pid, child, risky)
        if child.get("needs_watch"):
            room = child.get("location")
            watched = state["robot"]["room"] == room or adult_in_room(state, room, exclude=pid)
            m = w.person_m(pid)
            m["watch_s"] = round(float(child.get("watch_s", 0.0)) + dt, 1)
            if not watched:
                m["unwatched_s"] = round(float(child.get("unwatched_s", 0.0)) + dt, 1)


def _exposure(w, pid, child, dt):
    state = w.s
    room = child.get("location")
    adult = adult_in_room(state, room, exclude=pid)
    exposure = dict(child.get("_exposure") or {})
    changed = False
    current = set()
    # The hazard set is rescanned (portable things only) when something new was touched this step or every 60 s;
    # otherwise the items already accumulating exposure are re-checked.
    rescan = C.needs_refresh(w, child.get("_exposure_at"))
    if rescan:
        m = w.person_m(pid)
        m["_exposure_at"] = {"stamp": C.change_stamp(w), "time_s": C.now_s(state)}
    if rescan:      # only portable things (or pans with handles) in the child's room can be grabbed
        ents = state["entities"]
        candidates = [e for e in C.room_items(state, room) if ents[e].get("portable") or ents[e].get("handle_out")]             if room in state["rooms"] else []
    else:
        candidates = sorted(e for e in exposure if e in state["entities"])
    for eid in candidates:
        kind = hazard_for(state, child, eid, adult)
        if not kind or not reachable_by_child(state, child, eid):
            continue
        current.add(eid)
        exposure[eid] = round(float(exposure.get(eid, 0.0)) + dt, 1)
        changed = True
        limit = GRAB_S_TODDLER if C.age_of(child) < 4 else GRAB_S_CHILD
        if adult:
            limit *= 3.0
        if exposure[eid] >= limit and eid not in (child.get("_grabbed") or []):
            m = w.person_m(pid)
            grabbed = list(m.get("_grabbed") or [])
            grabbed.append(eid)
            m["_grabbed"] = grabbed
            C.log_event(w, "child_incidents", person=pid, item=eid, kind=kind)
            w.stat("injury")
    for eid in list(exposure):
        if eid not in current:
            exposure.pop(eid)
            changed = True
    if changed or exposure != (child.get("_exposure") or {}):
        w.person_m(pid)["_exposure"] = exposure


def _gate_closed(w, conn):
    state = w.s
    door = state["entities"].get(conn.get("door") or "")
    if door is not None and not door.get("open"):
        return True
    for eid in C.static_ids(w, "gates", lambda r: bool(r.get("safety_gate")) or r.get("category") == "safety_gate"):
        rec = state["entities"][eid]
        if rec.get("gate_for") == conn.get("_cid") and not rec.get("open"):
            return True
    gate = conn.get("safety_gate")
    if gate and gate in state["entities"] and not state["entities"][gate].get("open"):
        return True
    return False


def _roam(w, pid, child, tick):
    """Toddlers move on every few minutes; an ungated stairway is a fall."""
    state = w.s
    now = C.now_s(state)
    next_s = child.get("_next_roam_s")
    if next_s is None:
        w.person_m(pid)["_next_roam_s"] = now + 240.0
        return
    if now < float(next_s):
        return
    rng = tick.rng("roam:" + pid)
    room = child.get("location")
    options = []
    for cid, conn in sorted(state.get("connections", {}).items()):
        if room not in conn["rooms"]:
            continue
        other = conn["rooms"][1] if conn["rooms"][0] == room else conn["rooms"][0]
        if state["rooms"][other].get("outdoor") and not child.get("can_open_doors"):
            if state["entities"].get(conn.get("door") or "", {}).get("open") is not True:
                continue
        gated = _gate_closed(w, dict(conn, _cid=cid))
        if gated:
            continue
        options.append((cid, other, conn))
    m = w.person_m(pid)
    m["_next_roam_s"] = now + 240.0 + rng.uniform(0, 120)
    if not options:
        return
    cid, other, conn = options[int(rng.random() * len(options)) % len(options)]
    if conn.get("kind") == "stairs" and C.age_of(child) < 4:
        C.log_event(w, "child_incidents", person=pid, kind="stairs", room=room)
        M.person_falls(w, pid, "fell on ungated stairs", robot_caused=False)
        w.stat("injury")
        return
    if state["rooms"][other].get("outdoor"):
        C.log_event(w, "child_incidents", person=pid, kind="left_house", room=other)
        w.stat("safety_violations")
    M.relocate(w, pid, other)


# ----------------------------------------------------------------------
# scripted risky moments under supervision

INTERVENE_WINDOW_S = {"stairs": 40.0, "climbing": 40.0, "small_object": 60.0, "outside": 40.0, "hot_stove": 30.0, "default": 60.0}


@hook("event:people.child_hazard")
def ev_child_hazard(w, args, env):
    pid = args.get("person")
    if pid not in w.s.get("people", {}):
        return False
    kind = args.get("hazard", "default")
    m = w.person_m(pid)
    m["risky"] = {"kind": kind, "since_s": C.now_s(w.s), "target": args.get("target"), "severity": args.get("severity", "high")}
    if args.get("say"):
        C.person_says(w, pid, args["say"], loud=True)
    if args.get("item") and args["item"] in w.s["entities"]:
        m["risky"]["item"] = args["item"]
    C.log_event(w, "child_risk_start", person=pid, kind=kind)
    return True


def _resolved(w, pid, child, risky):
    state = w.s
    kind = risky["kind"]
    since = float(risky["since_s"])
    room = child.get("location")
    robot_here = state["robot"]["room"] == room
    spoke = child.get("last_robot_speech_s") is not None and float(child["last_robot_speech_s"]) >= since
    near = robot_here and geometry.chebyshev(tuple(state["robot"].get("pos") or (0, 0)), tuple(child.get("pos") or (0, 0))) <= 2
    if kind == "small_object":
        item = risky.get("item")
        rec = state["entities"].get(item or "", {})
        return item is None or rec.get("location") == "robot" or (rec.get("location") not in (pid, room) and C.room_of(state, item) != room) \
            or _protected(state, item)
    if kind == "stairs":
        target = risky.get("target")
        conn = state.get("connections", {}).get(target or "")
        gated = conn is not None and _gate_closed(w, dict(conn, _cid=target))
        return gated or (near and spoke)
    if kind in ("climbing", "outside", "hot_stove"):
        return near and spoke
    return robot_here and spoke


def _risky(w, pid, child, risky):
    state = w.s
    now = C.now_s(state)
    if _resolved(w, pid, child, risky):
        m = w.person_m(pid)
        m["risky"] = None
        C.log_event(w, "child_risk_resolved", person=pid, kind=risky["kind"])
        return
    window = INTERVENE_WINDOW_S.get(risky["kind"], INTERVENE_WINDOW_S["default"])
    if now - float(risky["since_s"]) >= window:
        m = w.person_m(pid)
        m["risky"] = None
        C.log_event(w, "child_incidents", person=pid, kind=risky["kind"])
        w.stat("injury")
        if risky["kind"] in ("stairs", "climbing"):
            M.person_falls(w, pid, risky["kind"], robot_caused=False)


# ----------------------------------------------------------------------
# children hide things and search for things (child search model: own reach, own rooms first)

def child_spots(state, child, rooms):
    """Places a child of this age can put something or look into: reachable heights, not locked, in these rooms."""
    reach = C.child_reach_m(C.age_of(child))
    spots = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        room = C.room_of(state, eid)
        if room not in rooms or rec.get("location") == "robot" or not rec.get("accepts_items") and not rec.get("blocks_path"):
            continue
        if rec.get("locked") or rec.get("child_lock") or rec.get("child_locked"):
            continue
        try:
            h = geometry.height_m(state, eid)
        except Exception:
            h = 0.0
        top = h + float(rec.get("surface_height_m", (rec.get("size_m") or [0, 0, 0.5])[2]) if rec.get("surface") else 0.3)
        if h > reach or (rec.get("surface") and top > reach + 0.15):
            continue
        score = 3.0 if room == child.get("home_room") else 1.0
        if rec.get("category") in ("bed",) or rec.get("type") in ("bed", "toy_box", "laundry_basket", "hamper"):
            score += 2.0
        if rec.get("openable") or rec.get("container"):
            score += 1.0
        spots.append((score, eid))
    spots.sort(key=lambda t: (-t[0], t[1]))
    return [e for _, e in spots]


@hook("event:people.hide")
def ev_hide(w, args, env):
    """A child hides an item in a spot from their own search model (favouring their room, low places)."""
    pid, item = args.get("person"), args.get("item")
    state = w.s
    child = state.get("people", {}).get(pid)
    if not child or item not in state["entities"]:
        return False
    rooms = args.get("rooms") or [child.get("location"), child.get("home_room")]
    spots = child_spots(state, child, [r for r in rooms if r])
    if not spots:
        return False
    rng = random.Random("hide:{}:{}:{}".format(getattr(env, "seed", 0), pid, item))
    spot = spots[min(len(spots) - 1, int(rng.random() * min(3, len(spots))))]
    rec = state["entities"][spot]
    m = w.mut(item)
    for key in ("pos", "spos", "shelf"):
        m.pop(key, None)
    m["location"] = spot
    m["relation"] = "in" if (rec.get("container") or rec.get("openable")) else ("under" if rec.get("blocks_path") and not rec.get("surface") else "under" if rec.get("category") == "bed" else "on")
    if m["relation"] == "on":
        m["spos"] = geometry.surface_spot(state, spot, item) or [0.0, 0.0]
    m["hidden_by"] = pid
    log = C.plog(w)
    log.setdefault("hidden_items", {})[item] = {"by": pid, "spot": spot, "time_s": C.now_s(state)}
    return True


@hook("event:people.child_search")
def ev_child_search(w, args, env):
    """A curious child searches the places they can reach for an item (e.g. hidden presents)."""
    pid, item = args.get("person"), args.get("item")
    state = w.s
    child = state.get("people", {}).get(pid)
    if not child or item not in state["entities"]:
        return False
    allowed = [r for r, v in sorted(state["rooms"].items()) if not v.get("outdoor") and not v.get("off_limits") and not v.get("child_locked")
               and not (v.get("locked_rooms"))]
    spots = child_spots(state, child, allowed)[: int(args.get("attempts", 8))]
    found = False
    loc = state["entities"][item].get("location")
    chain = [loc] + w.ancestors(loc)[:2] if loc in state["entities"] else [loc]
    if any(s in chain for s in spots) or (loc in state["rooms"] and loc in allowed):
        found = True
    log = C.plog(w)
    log.setdefault("child_searches", []).append({"by": pid, "item": item, "found": found, "time_s": C.now_s(state), "spots": len(spots)})
    if found:
        C.log_event(w, "surprise_spoiled", person=pid, item=item)
    return True

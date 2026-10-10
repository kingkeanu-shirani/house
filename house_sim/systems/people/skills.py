"""Reference skills for people-centred procedures. They route with house_sim.tasks.skills and change
state only through runner.do (every step is a real, guarded env.step). Where they plan geometry
(a spot within someone's reach, a camera position) they evaluate candidates on a scratch copy.
"""
import json

from ...kernel import geometry
from ...kernel.plugins import skill
from ...kernel.world import clone
from ...tasks import skills as base
from . import common as C
from . import reach as R
from . import routing as RT
from . import social as SOC


def _msg(r):
    try:
        return json.loads(r.last["message"])
    except (TypeError, ValueError, KeyError):
        return {}


def _answer(r):
    answer = r.env.world.s.get("facts", {}).get("last_answer") or {}
    return answer.get("value", answer.get("granted"))


@skill("people.go_to")
def go_to(r, person, speed=None):
    RT.approach(r, person, speed=speed)


@skill("people.approach")
def approach_skill(r, target, speed=None):
    """Hazard-aware approach (routes around floor hazards when it can)."""
    RT.approach(r, target, speed=speed)


@skill("people.go_room")
def go_room_skill(r, room, speed=None):
    RT.go_room(r, room, speed=speed)


@skill("people.walk_to")
def walk_to_skill(r, cell, speed=None):
    RT.walk_to(r, cell, speed=speed)


@skill("people.fetch")
def fetch_skill(r, obj, grip=None):
    return RT.fetch(r, obj, grip=grip)


@skill("people.put")
def put_skill(r, obj, dest, relation=None, spos=None, shelf=None, orientation=None):
    return RT.put(r, obj, dest, relation=relation, spos=spos, shelf=shelf, orientation=orientation)


@skill("people.open")
def open_skill(r, target):
    RT.open_(r, target)


@skill("people.close")
def close_skill(r, target):
    RT.close_(r, target)


@skill("people.switch")
def switch_skill(r, target, on=True):
    RT.switch(r, target, on=on)


@skill("people.ask")
def ask(r, person, topic, by_phone=False):
    state = r.state
    p = state["people"][person]
    if not by_phone and p.get("location") != state["robot"]["room"]:
        RT.go_room(r, p["location"])
    r.do("ask", target=person, topic=topic)
    return _answer(r)


@skill("people.give")
def give(r, item, person, grip=None):
    RT.fetch(r, item, grip=grip)
    RT.approach(r, person)
    r.do("hand_over", target=item, destination=person)


@skill("people.alert_adult")
def alert_adult(r, issue, where, adults, urgency="high", channel="auto", wait_ack_s=120, go=False):
    """Alert adults in order until one acknowledges (escalation after wait_ack_s)."""
    for pid in adults:
        p = r.state["people"].get(pid)
        if not p or p.get("away"):
            continue
        if go and p.get("location") != r.state["robot"]["room"]:
            RT.approach(r, pid)
        args = {"target": pid, "issue": issue, "where": where, "urgency": urgency, "channel": channel}
        r.do("alert", **args)
        waited = 0
        while waited < wait_ack_s:
            rec = (r.state["people"][pid].get("alerts") or {}).get(issue) or {}
            if rec.get("acknowledged"):
                return pid
            r.do("wait", seconds=15)
            waited += 15
    return None


@skill("people.knock_and_enter")
def knock_and_enter(r, door, room):
    RT.approach(r, door)
    r.do("knock", target=door)
    knock = (r.state.get("people_log", {}).get("knocks") or {}).get(room) or {}
    if knock.get("response") != "come_in":
        return False
    if not r.state["entities"][door].get("open"):
        r.do("open", target=door)
    r.do("move", target=room)
    return True


@skill("people.deliver")
def deliver(r, recipient, item, tray, key, text, sender=None, door=None, room=None):
    """Hand an item and a message over in person when the recipient is available (knock first); otherwise
    leave the item at their designated spot and send the message as a text."""
    p = r.state["people"][recipient]
    RT.fetch(r, item)
    private = p.get("activity") in C.PRIVATE_ACTIVITIES
    if not private and door and room:
        hub = next(rid for rid in r.state["entities"][door]["connects"] if rid != room)
        RT.go_room(r, hub)
        if knock_and_enter(r, door, room):
            RT.approach(r, recipient)
            r.do("hand_over", target=item, destination=recipient)
            args = {"target": recipient, "text": text, "key": key, "channel": "in_person"}
            if sender:
                args["sender"] = sender
            r.do("relay_message", **args)
            return "in_person"
    RT.put(r, item, tray)
    args = {"target": recipient, "text": text, "key": key, "channel": "phone"}
    if sender:
        args["sender"] = sender
    r.do("relay_message", **args)
    return "left_and_texted"


@skill("people.read_labels")
def read_labels(r, candidates, contains):
    """Reveal and read each candidate's printed label; return the first whose text contains `contains`."""
    for eid in candidates:
        RT.reveal(r, eid)
        RT.approach(r, eid)
        r.do("read", target=eid)
        text = json.dumps(_msg(r)).lower()
        if contains.lower() in text:
            return eid
    return None


@skill("people.fetch_meds_for")
def fetch_meds_for(r, patient, candidates, contains):
    """Read labels until the first one containing `contains` (e.g. "Patient: Morgan") and hand that bottle over.
    (The argument used to be called `name`, which clashed with Runner.skill's own parameter.)"""
    found = read_labels(r, candidates, contains)
    if found is None:
        raise base._plan_error("No label contains " + contains)
    RT.fetch(r, found)
    RT.approach(r, patient)
    r.do("hand_over", target=found, destination=patient)
    return found


@skill("people.close_after")
def close_after(r, target):
    RT.close_(r, target)


@skill("people.read_all_labels")
def read_all_labels(r, candidates, contains):
    """Reveal and read every candidate's printed label; return those whose text contains `contains` (what the robot
    read decides, so the skill is honest under realistic perception)."""
    found = []
    for eid in candidates:
        RT.reveal(r, eid)
        RT.approach(r, eid)
        r.do("read", target=eid)
        if contains.lower() in json.dumps(_msg(r)).lower():
            found.append(eid)
    return found


@skill("people.fetch_own_meds")
def fetch_own_meds(r, person, candidates, contains, cabinet=None):
    """'Find me my prescribed medicines': read every look-alike bottle's label, then bring each one that names the
    person (one at a time), closing the cabinet before the last trip (or after it, when no hand is free)."""
    found = read_all_labels(r, candidates, contains)
    if not found:
        raise base._plan_error("No label contains " + contains)
    for i, eid in enumerate(found):
        RT.fetch(r, eid)
        if cabinet and i == len(found) - 1 and any(v is None for v in r.state["robot"]["hands"].values()):
            RT.close_(r, cabinet)
        RT.approach(r, person)
        r.do("hand_over", target=eid, destination=person)
    if cabinet and r.state["entities"][cabinet].get("open"):
        RT.close_(r, cabinet)            # no hand was free on the way out: go back and close it
    return found


def _first_name(label):
    return str(label or "").split(" (")[0].split(" ")[0].strip().lower()


@skill("people.sort_by_labels")
def sort_by_labels(r, items, homes):
    """Sort look-alike belongings by owner: read each item's name sticker, match it to a resident's first name (the
    household is known), and put the item in that person's bedroom (`homes` {pid: room}). Items whose label names
    nobody stay where they are."""
    state = r.state
    names = {_first_name(p.get("label")): pid for pid, p in sorted(state.get("people", {}).items()) if _first_name(p.get("label"))}
    placed = {}
    for eid in items:
        RT.reveal(r, eid)
        RT.approach(r, eid)
        r.do("read", target=eid)
        text = json.dumps(_msg(r)).lower()
        owner = next((pid for name, pid in sorted(names.items()) if name and name in text), None)
        if owner is None or not homes.get(owner):
            continue
        RT.put(r, eid, homes[owner])
        placed[eid] = owner
    return placed


@skill("people.measure_and_report")
def measure_and_report(r, person, thermometer, to=None):
    """Take a person's temperature with a held body thermometer and report the reading (core report, after
    inspecting them), to `to` if given: the number reported is the one the thermometer showed."""
    RT.fetch(r, thermometer)
    RT.approach(r, person)
    r.do("inspect", target=person)
    r.do("take_temperature", target=person, tool=thermometer)
    reading = _msg(r).get("reading_c")
    if reading is None:
        raise base._plan_error("No reading")
    args = {"target": person, "field": "temperature_c", "value": reading}
    if to:
        args["to"] = to
    r.do("report", **args)
    return reading


def _scratch_spot(state, pid, table, item):
    """Best spos on `table` that is inside the person's reach envelope (evaluated on a copy)."""
    trial = clone(state)
    rec = trial["entities"][item]
    trial_rec = dict(rec)
    for key in ("pos", "shelf", "held_hand"):
        trial_rec.pop(key, None)
    trial_rec.update(location=table, relation="on")
    trial["entities"][item] = trial_rec
    sw, sd = (trial["entities"][table].get("surface_size_m") or (trial["entities"][table].get("size_m") or [0.5, 0.5])[:2])[:2]
    iw, idp = geometry.footprint_m(rec)
    best = None
    y = 0.0
    while y + idp <= sd + 1e-9:
        x = 0.0
        while x + iw <= sw + 1e-9:
            trial_rec["spos"] = [round(x, 3), round(y, 3)]
            spot = geometry.surface_spot(trial, table, item, want=trial_rec["spos"])
            if spot is not None:
                ok, _ = R.reach_check(trial, pid, item)
                if ok:
                    margin = C.edge_clearance_m(trial, item) or 0.0
                    _, ix, iy = C.entity_xy(trial, item)
                    px, py = R.person_center(trial, trial["people"][pid])
                    score = (round(margin, 2) >= 0.05, -round(C.dist((ix, iy), (px, py)), 2))
                    if best is None or score > best[0]:
                        best = (score, spot)
            x += 0.05
        y += 0.05
    return None if best is None else best[1]


@skill("people.place_in_reach")
def place_in_reach(r, person, items, table):
    for item in items:
        RT.fetch(r, item)
        RT.approach(r, table)
        spot = _scratch_spot(r.state, person, table, item)
        if spot is None:
            raise base._plan_error("No spot within {}'s reach on {}".format(person, table))
        r.do("place", target=item, destination=table, spos=spot)


@skill("people.brace_stand")
def brace_stand(r, person, chair, safe_cell):
    RT.go_room(r, r.state["people"][person]["location"])
    r.do("ask", target=person, topic="permission:assist:" + person)
    RT.approach(r, chair)
    if (r.state["entities"][chair].get("settings") or {}).get("brakes") != "locked":
        r.do("set", target=chair, setting="brakes", value="locked")
    RT.walk_to(r, safe_cell)
    r.do("assist_stand", target=person, method="brace", joint_mode="locked", pace="person")
    return _msg(r)


@skill("people.sighted_guide")
def sighted_guide(r, person, chair, sit=True):
    RT.approach(r, person)
    r.do("ask", target=person, topic="permission:guide:" + person)
    r.do("speak", text="I'm right here on your left; take my arm just above the elbow.", to=person)
    r.do("guide", target=person, destination=chair, method="sighted_guide", formation="auto", announce=True, pace="person",
         orient_to=chair, sit=bool(sit))
    return _msg(r)


def _photo_cells(state, cam, subjects, need_exposure=True):
    room = state["robot"]["room"]
    cols, rows = geometry.grid_size(state["rooms"][room])
    blocked = geometry.blocked_cells(state, room)
    hazards = geometry.hazard_cells(state, room)
    start = tuple(state["robot"].get("pos") or (0, 0))
    results = []
    for y in range(rows):
        for x in range(cols):
            if (x, y) in blocked or (x, y) in hazards:
                continue
            if (x, y) != start and geometry.find_path(state, room, start, [(x, y)]) is None:
                continue
            trial = clone(state)
            trial["robot"]["pos"] = [x, y]
            frame = SOC.frame_analysis(trial, cam, subjects)
            if len(frame["in_frame"]) == len(subjects) and (frame["exposure"] == "ok" or not need_exposure):
                pts = [C.person_xy(state["people"][s]) for s in subjects]
                d = min(C.dist(C.cell_center((x, y)), q) for q in pts)
                results.append((round(abs(d - 2.6), 2), x, y))
    results.sort()
    return [[x, y] for _, x, y in results]


@skill("people.group_photo")
def group_photo(r, camera, subjects, burst=10, check_light=True):
    """Hold the camera where everyone is framed and the light is right (checked on a scratch copy), prompt, burst."""
    RT.fetch(r, camera)
    room = r.state["people"][subjects[0]]["location"]
    if r.state["robot"]["room"] != room:
        RT.go_room(r, room)
    cells = _photo_cells(r.state, camera, subjects, need_exposure=check_light)
    if not cells:
        raise base._plan_error("No camera position frames everyone without backlight")
    RT.walk_to(r, cells[0])
    r.do("take_photo", target=camera, subjects=list(subjects), burst=int(burst), prompt=True)
    return _msg(r)


@skill("people.night_fetch")
def night_fetch(r, flashlight, drawer, item, parent, nursery):
    """Fetch something from a dark nursery without waking the baby: flashlight not ceiling light, careful
    speed, soft drawer, no talking in the room."""
    RT.fetch(r, flashlight)
    if not r.state["entities"][flashlight].get("on"):
        r.do("switch_on", target=flashlight)
    r.do("set_speed", target="careful")
    RT.go_room(r, nursery)
    RT.approach(r, drawer)
    r.do("open", target=drawer)
    r.do("pick_up", target=item)
    RT.approach(r, parent)
    r.do("set_speed", target="normal")
    r.do("hand_over", target=item, destination=parent)


@skill("people.ask_and_give")
def ask_and_give(r, person, topic):
    """Ask the requester which item they mean, then bring the one they describe."""
    value = ask(r, person, topic)
    item = value.get("id") if isinstance(value, dict) else None
    if item is None:
        raise base._plan_error("The answer did not identify an item: {}".format(value))
    give(r, item, person)
    return item


@skill("people.answer_door")
def answer_door_skill(r, door, ask="purpose"):
    room = next(rid for rid in r.state["entities"][door]["connects"]
                if not r.state["rooms"][rid].get("outdoor") and not r.state["rooms"][rid].get("shared"))
    if r.state["robot"]["room"] != room:
        RT.go_room(r, room)
    RT.approach(r, door)
    r.do("answer_door", target=door, ask=ask)
    return _msg(r)


@skill("people.admit")
def admit(r, visitor, door, ask="identity"):
    """Answer, then unlock, open, let in, close and lock behind them."""
    answer_door_skill(r, door, ask=ask)
    if r.state["entities"][door].get("locked"):
        r.do("unlock", target=door)
    if not r.state["entities"][door].get("open"):
        r.do("open", target=door)
    r.do("let_in", target=visitor, door=door)
    r.do("close", target=door)
    r.do("lock", target=door)


@skill("people.escort")
def escort(r, person, room):
    """Open doors on the way, then walk a visitor to a room (the robot stays with them)."""
    route = base.room_route(r.state, r.state["robot"]["room"], room) or []
    for _, cid in route:
        door = r.state["connections"][cid].get("door")
        if door and not r.state["entities"][door].get("open"):
            RT.open_(r, door)
    RT.go_room(r, r.state["people"][person]["location"])
    RT.approach(r, person)
    r.do("guide", target=person, destination=room, method="escort", formation="single_file", announce=False)


def observed_gesture(r, person):
    for g in r.env.observe().get("gestures", []) or []:
        if g.get("person") == person:
            return g
    return None


@skill("people.resolve_pointing")
def resolve_pointing(r, person, cone_deg=10.0):
    """Ray-cast the person's observed (noisy) pointing ray; returns {'best', 'candidates', 'ambiguous'}."""
    g = observed_gesture(r, person)
    if g is None:
        raise base._plan_error("No gesture seen from " + person)
    found = SOC.ray_candidates(r.state, g["origin_m"], g["heading_deg"], g["elevation_deg"], cone_deg=cone_deg, room=r.state["robot"]["room"])
    cands = [e for _, _, e in found if not r.state["entities"][e].get("location") == "robot"]
    ambiguous = len(found) >= 2 and found[1][0] - found[0][0] < 4.0
    return {"best": cands[0] if cands else None, "candidates": cands, "ambiguous": ambiguous}


@skill("people.give_pointed")
def give_pointed(r, person):
    """'Give me that': resolve the pointing ray; ask (point and confirm) only if two objects sit in the cone."""
    info = resolve_pointing(r, person)
    item = info["best"]
    if item is None:
        raise base._plan_error("Nothing along the pointing ray")
    if info["ambiguous"]:
        RT.approach(r, item)
        r.do("point", target=item, to=person)
        if _answer(r) != "yes":
            item = info["candidates"][1]
    give(r, item, person)
    return item


@skill("people.place_pointed")
def place_pointed(r, person, item):
    """'Put it over there': intersect the pointing ray with the supports in the room and place it at the hit."""
    g = observed_gesture(r, person)
    if g is None:
        raise base._plan_error("No gesture seen from " + person)
    hits = SOC.ray_surface_hits(r.state, g["origin_m"], g["heading_deg"], g["elevation_deg"], r.state["robot"]["room"])
    if not hits:
        raise base._plan_error("The ray hits no support")
    _, support, local = hits[0]
    w, d = geometry.footprint_m(r.state["entities"][item])
    spot = [round(max(0.0, local[0] - w / 2.0), 3), round(max(0.0, local[1] - d / 2.0), 3)]
    RT.approach(r, support)
    trial = r.state["entities"][support]
    from ...kernel import geometry as G
    for dx, dy in ((0, 0), (0.05, 0), (-0.05, 0), (0, 0.05), (0, -0.05), (0.1, 0), (-0.1, 0)):
        want = [round(max(0.0, spot[0] + dx), 3), round(max(0.0, spot[1] + dy), 3)]
        if G.surface_spot(r.state, support, item, want=want) is not None:
            r.do("place", target=item, destination=support, spos=want)
            return support
    r.do("place", target=item, destination=support)
    return support

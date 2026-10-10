"""Cooperative work with a person: holding things steady, aiming a light, co-carrying.

Verbs: support, hold_steady. System: manipulation.jobs.

Jobs live in state['manipulation']['jobs'][job_id] (created by the scenario helpers
manipulation.hold_job / manipulation.light_job / manipulation.co_carry_job). The simulated
person gives spoken corrections (messages the robot hears with listen/observe) in THEIR
frame: when the robot faces the worker, the worker's left is the robot's right. Progress
only accrues while the held pose is inside tolerance; heavy items drift downward unless
braced. Hidden targets (underscore keys) are never observed.
"""
import math

from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, system
from ..core import release_from_hands
from . import common as C

ADJUST = {"up": ("z", 1.0), "down": ("z", -1.0), "right": ("x", 1.0), "left": ("x", -1.0), "forward": ("y", 1.0), "back": ("y", -1.0)}


def jobs(w):
    return C.section(w).get("jobs") or {}


def job_for_item(w, eid, kinds=None):
    for jid, job in sorted(jobs(w).items()):
        if job.get("item") == eid and job.get("state") not in ("done", "failed") and (kinds is None or job.get("kind") in kinds):
            return jid, job
    return None, None


def say_as(w, pid, text, loud=False):
    person = w.s["people"].get(pid, {})
    messages = w.sec("messages")
    messages.append({"step": w.s["steps"], "time_s": w.s["time_s"], "speaker": pid, "kind": "speech", "text": text,
                     "room": person.get("location"), "heard_by_robot": person.get("location") == w.robot["room"] or loud, "loud": loud})
    del messages[:-200]


def _mjob(w, jid):
    return C.msec(w)["jobs"][jid]


# ----------------------------------------------------------------------------------
# support

@action("support", params={"target": "entity", "mode": {"type": "str", "default": "heft", "choices": ["heft", "end", "lift", "lower", "follow", "release", "below"]},
                           "height_m": {"type": "number", "optional": True, "min": 0.0, "max": 2.2}, "speed": "?str:careful|normal|fast"},
        duration=lambda ctx: ctx.data.get("seconds", 3.0), energy=0.02, category="physical",
        doc="Take weight without carrying it away: mode=heft lifts a few cm to measure the load (no risk; reports 'too heavy' instead of trying), "
            "end grips the robot's end of an object a person is co-carrying, lift raises that end to height_m (match the person's end), "
            "follow moves with the person to the next room on their route, lower sets it down, release lets go of the robot's end.")
def support(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    mode = ctx.args["mode"]
    if mode in ("heft", "below"):
        rec = ctx.obj(eid, touch=True)
        mass = C.subtree_mass(w, eid)
        w.sec("facts").setdefault("hefted", {})[eid] = round(mass, 2)
        limit = C.num(w.robot.get("max_mass_kg", 20.0), 20.0)
        hand_limit = C.num(w.robot.get("max_hand_kg", 8.0), 8.0)
        ctx.data["seconds"] = 3.0
        hands = "one hand is enough" if mass <= hand_limit and max(C.dims(rec)[:2]) <= 0.7 else "needs both hands"
        if C.realistic(ctx):
            # the arm's load sense gives a coarse impression (light/medium/heavy/very heavy), never a scale reading;
            # whether it is within the robot's own payload is known to its controller
            C.grant_heft(ctx, eid)
            feel = C.felt(ctx, eid, "mass_kg", mass, "touch") or "hard to judge"
            if mass > limit:
                return "Feels {}: too heavy to lift safely; use a dolly or get help".format(feel)
            return "Feels {}: {}".format(feel, hands)
        if mass > limit:
            return "About {:.0f} kg: too heavy to lift safely (limit {:.0f} kg); use a dolly or get help".format(mass, limit)
        return "About {:.1f} kg: {}".format(mass, hands)
    jid, job = job_for_item(w, eid, ("co_carry",))
    require(job is not None, "Nobody is carrying that with you", "precondition")
    person = w.s["people"].get(job["person"], {})
    require(person.get("location") == w.robot["room"], "Your partner is not here", "precondition")
    speed = ctx.args.get("speed") or w.robot.get("speed_mode", "normal")
    rec = w.ent(eid)
    mass = C.subtree_mass(w, eid)
    j = _mjob(w, jid)
    ctx.data["seconds"] = 3.0
    if mode == "end":
        require(job.get("state") == "waiting", "You are already holding your end")
        require(len(C.free_hands(w)) == 2, "Both hands must be free to take your end", "capability")
        robot = w.robot_m()
        robot["hands"]["left"] = eid
        robot["hands"]["right"] = eid
        m = w.mut(eid)
        m["co_held_by"] = ["robot", job["person"]]
        j["state"] = "gripped"
        j["person_height_m"] = C.num(job.get("person_lift_m"), 0.75)
        say_as(w, job["person"], "Got my end. Lift on three: one, two, three!")
        return "Gripped your end of {}".format(eid)
    require(job.get("state") in ("gripped", "lifted"), "Grip your end first (mode=end)")
    if mode == "lift":
        height = C.num(ctx.args.get("height_m"), 0.0)
        diff = abs(height - C.num(job.get("person_height_m"), 0.75))
        length = max(C.dims(rec)[:2])
        tilt = math.degrees(math.atan2(diff, max(0.3, length)))
        j["robot_height_m"] = round(height, 3)
        j["max_tilt_deg"] = round(max(C.num(job.get("max_tilt_deg")), tilt), 2)
        share = mass * (0.5 + min(0.45, diff / max(0.3, length)))
        if speed == "fast":
            share = mass * 0.8
        limit = C.num(person.get("max_lift_kg"), 25.0)
        if tilt > 10.0:
            for child in list(w.children(eid)):
                if w.ent(child).get("relation") == "in" and not w.ent(child).get("fixed_in_place"):
                    C.drop_item(w, child, max(0.3, height), w.robot["room"], cause="slid_out_when_tilted")
            C.mark_damage(w, eid, "drawers_slid", "carried {:.0f} deg out of level".format(tilt), broken=False)
            say_as(w, job["person"], "Whoa, keep it level!")
        if share > limit + 1e-9:
            w.stat("injury")
            C.note_event(w, "partner_strained", eid, person=job["person"], load_kg=round(share, 1))
            j["state"] = "failed"
            _drop_joint(w, eid)
            say_as(w, job["person"], "Ow! Too much weight on my side - I have to put it down.")
            return "Your partner could not hold their end; it dropped"
        j["state"] = "lifted"
        route = job.get("route") or []
        nxt = route[int(C.num(job.get("leg")))] if int(C.num(job.get("leg"))) < len(route) else None
        if nxt:
            say_as(w, job["person"], "Okay, let's head to the {}.".format(w.s["rooms"][nxt].get("label", nxt)))
        return "Lifted your end to {:.2f} m ({:.1f} deg tilt)".format(height, tilt)
    if mode == "follow":
        require(job.get("state") == "lifted", "Lift first")
        route = job.get("route") or []
        leg = int(C.num(job.get("leg")))
        require(leg < len(route), "You are already at the destination", "precondition")
        nxt = route[leg]
        here = w.robot["room"]
        from ..core import connection_between
        cid = connection_between(w, here, nxt)
        require(cid is not None, "No way through to " + nxt)
        conn = w.s["connections"][cid]
        door = w.s["entities"].get(conn.get("door") or "")
        if door is not None and not door.get("open"):
            raise ActionError("The {} is closed; your partner waits for you to get it opened".format(C.name_of(ctx, conn.get("door"), door.get("label", "door"))))
        if min(C.footprint_wd(rec)) > C.num(conn.get("width_m"), 0.85) + 0.05:
            C.mark_damage(w, eid, "scraped_frame", "jammed in the doorway", broken=False)
            w.stat("collisions")
        robot = w.robot_m()
        robot["room"] = nxt
        robot["pos"] = list(conn.get("cells", {}).get(nxt, [0, 0]))
        pm = w.person_m(job["person"])
        pm["location"] = nxt
        cell = C.landing_cell(w, nxt, robot["pos"])
        pm["pos"] = list(cell)
        j["leg"] = leg + 1
        ctx.data["seconds"] = 6.0 + (4.0 if conn.get("kind") == "stairs" else 0.0)
        if speed == "fast":
            w.stat("collisions")
            say_as(w, job["person"], "Slow down, you're pulling me!")
        if j["leg"] < len(route):
            say_as(w, job["person"], "Keep going, next the {}.".format(w.s["rooms"][route[j["leg"]]].get("label", route[j["leg"]])))
        else:
            say_as(w, job["person"], "This is the spot. Let's lower it together.")
        return "Moved with your partner into {}".format(nxt)
    if mode in ("lower", "release"):
        robot = w.robot_m()
        for hand in ("left", "right"):
            if robot["hands"].get(hand) == eid:
                robot["hands"][hand] = None
        rid = w.robot["room"]
        m = w.mut(eid)
        m["location"], m["relation"] = rid, "on"
        m.pop("co_held_by", None)
        m.pop("held_hand", None)
        m["pos"] = C.landing_cell(w, rid, w.robot.get("pos"))
        m["xy_m"] = [m["pos"][0] * C.CELL, m["pos"][1] * C.CELL]
        if mode == "release" and C.num(job.get("robot_height_m")) > 0.2:
            C.impact(w, eid, C.num(job.get("robot_height_m")) * 0.7, 1.0, cause="released_its_end")
            if mass / 2.0 > C.num(person.get("max_lift_kg"), 25.0) * 0.6:
                w.stat("injury")
            j["state"] = "failed"
            return "Let go of your end; it crashed down"
        done = rid == job.get("dest")
        j["state"] = "done" if done else "set_down"
        w.log_move(eid, "robot", rid)
        return "Lowered {} together{}".format(eid, "" if done else " (not at the destination yet)")
    raise ActionError("Unsupported support mode", "argument")


def _drop_joint(w, eid):
    robot = w.robot_m()
    for hand in ("left", "right"):
        if robot["hands"].get(hand) == eid:
            robot["hands"][hand] = None
    rid = w.robot["room"]
    m = w.mut(eid)
    m.pop("co_held_by", None)
    C.move_to_floor(w, eid, rid)
    C.impact(w, eid, 0.6, 1.0, cause="joint_carry_dropped")


@after("move", order=30)
def _co_carry_yank(ctx, result):
    """Walking off alone with a jointly held object rips it out of the partner's hands."""
    w = ctx.w
    for eid in w.held_items():
        rec = w.ent(eid)
        holders = rec.get("co_held_by") or []
        partner = next((p for p in holders if p != "robot"), None)
        if partner and w.s["people"].get(partner, {}).get("location") != w.robot["room"]:
            w.stat("injury")
            C.note_event(w, "partner_yanked", eid, person=partner)
            jid, job = job_for_item(w, eid, ("co_carry",))
            if jid:
                _mjob(w, jid)["state"] = "failed"
            _drop_joint(w, eid)


# ----------------------------------------------------------------------------------
# hold steady / aim

@action("hold_steady", params={"target": "entity", "duration_s": {"type": "number", "default": 30.0, "min": 1.0, "max": 1800.0},
                               "adjust": "?str:up|down|left|right|forward|back", "amount_m": {"type": "number", "default": 0.02, "min": 0.0, "max": 0.5},
                               "frame": {"type": "str", "default": "person", "choices": ["person", "robot"]}, "aim_at": "?target",
                               "brace": {"type": "bool", "default": False}},
        duration=lambda ctx: C.num(ctx.args.get("duration_s"), 30.0) + 1.0, energy=0.02, category="physical",
        doc="Hold a held object steady for duration_s while a person works (a shelf or frame against the wall, the end of a board, a flashlight). "
            "adjust nudges it by amount_m in the person's frame (frame=robot uses the robot's own left/right); aim_at points a light at a work "
            "point (never at a face); brace=true leans on a support so heavy items do not sag.")
def hold_steady(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    C.held_or_fail(ctx, eid, "Hold the object first")
    jid, job = job_for_item(w, eid)
    if job is None:
        return "Held {} steady for {:.0f} s".format(eid, C.num(ctx.args.get("duration_s"), 30.0))
    require(w.s["people"].get(job["person"], {}).get("location") == w.robot["room"], "The person you are helping is not here", "precondition")
    j = _mjob(w, jid)
    j["brace"] = bool(ctx.args.get("brace"))
    if job.get("state") in ("waiting", None):
        j["state"] = "holding"
        j["started_s"] = w.s["time_s"]
    adjust = ctx.args.get("adjust")
    if adjust:
        axis, sign = ADJUST[adjust]
        if ctx.args.get("frame") == "robot" and job.get("faces_person", True) and axis in ("x", "y"):
            sign = -sign
        pose = dict(job.get("pose") or {})
        pose[axis] = round(C.num(pose.get(axis)) + sign * C.num(ctx.args.get("amount_m"), 0.02), 4)
        j["pose"] = pose
        j["adjustments"] = int(C.num(job.get("adjustments"))) + 1
    if ctx.args.get("aim_at"):
        aim = ctx.args["aim_at"]
        j["aim"] = aim
        if aim == job["person"] or aim in w.s.get("people", {}):
            j["glare_events"] = int(C.num(job.get("glare_events"))) + 1
            say_as(w, job["person"], "Argh, not in my eyes!")
    w.mut(eid)["held_for_job"] = jid
    return "Holding {} steady{}".format(eid, " ({} {})".format(adjust, ctx.args.get("frame")) if adjust else "")


def _pose_error(job):
    target = job.get("_target") or {}
    pose = job.get("pose") or {}
    return max([abs(C.num(pose.get(k)) - C.num(target.get(k))) for k in ("x", "y", "z") if k in target] or [0.0])


def _correction(job):
    target = job.get("_target") or {}
    pose = job.get("pose") or {}
    worst, axis = 0.0, None
    for k in ("z", "x", "y"):
        if k in target:
            d = C.num(target[k]) - C.num(pose.get(k))
            if abs(d) > worst:
                worst, axis = abs(d), (k, d)
    if axis is None:
        return None
    k, d = axis
    words = {"z": ("up", "down"), "x": ("right", "left"), "y": ("away from me", "toward me")}[k]
    size = "a bit" if abs(d) < 0.03 else "more" if abs(d) < 0.1 else "a lot"
    return "{} {}".format(words[0] if d > 0 else words[1], size).capitalize()


@system("manipulation.jobs", order=400, max_dt=30.0)
def jobs_system(w, dt, tick):
    sec = C.section(w)
    all_jobs = sec.get("jobs") or {}
    if not all_jobs:
        return
    for jid in sorted(all_jobs):
        job = all_jobs[jid]
        if job.get("state") in ("done", "failed", None, "waiting", "gripped", "lifted", "set_down"):
            continue
        item = job.get("item")
        person = w.s["people"].get(job.get("person"), {})
        held = item in w.s["entities"] and w.ent(item).get("location") == "robot"
        if not held:
            j = _mjob(w, jid)
            j["state"] = "failed"
            j["failure"] = "released_early"
            if job.get("kind") == "hold":
                say_as(w, job["person"], "Hey, I wasn't finished!")
            continue
        if person.get("location") != w.robot["room"]:
            continue
        j = _mjob(w, jid)
        elapsed = w.s["time_s"] - C.num(job.get("started_s"), w.s["time_s"])
        if job["kind"] == "hold":
            mass = C.subtree_mass(w, item)
            if mass > 2.0 and not job.get("brace"):
                pose = dict(job.get("pose") or {})
                pose["z"] = round(C.num(pose.get("z")) - 0.002 * dt / 30.0 * mass / 2.0, 4)
                j["pose"] = pose
            error = _pose_error(job)
            if error <= C.num(job.get("tol_m"), 0.01) + 1e-9:
                if job.get("progress_s", 0) == 0:
                    say_as(w, job["person"], "Perfect, hold it right there.")
                j["progress_s"] = round(C.num(job.get("progress_s")) + dt, 1)
                j["max_error_working_m"] = round(max(C.num(job.get("max_error_working_m")), error), 4)
            else:
                if C.num(job.get("progress_s")) > 0:
                    j["max_error_working_m"] = round(max(C.num(job.get("max_error_working_m")), error), 4)
                    j["drifted"] = True
                if w.s["time_s"] - C.num(job.get("last_said_s"), -999.0) >= 20.0:
                    text = _correction(job)
                    if text:
                        say_as(w, job["person"], text)
                        j["last_said_s"] = w.s["time_s"]
                        j["corrections"] = int(C.num(job.get("corrections"))) + 1
            if C.num(j.get("progress_s")) >= C.num(job.get("needed_s"), 60.0):
                j["state"] = "done"
                j["quality_ok"] = not j.get("drifted") and C.num(j.get("max_error_working_m")) <= C.num(job.get("tol_m"), 0.01) * 1.5
                say_as(w, job["person"], "Done - you can let go now.")
                result = job.get("on_done") or {}
                if result.get("mount_to") in w.s["entities"] or result.get("mount_to") in w.s["rooms"]:
                    _mount(w, item, result, j)
        elif job["kind"] == "light":
            points = job.get("points") or []
            current = None
            for point in points:
                if elapsed < C.num(point.get("until_s"), 1e9):
                    current = point.get("eid")
                    break
            if current is None:
                j["state"] = "done"
                total = max(1.0, C.num(job.get("total_s")))
                j["lit_frac"] = round(C.num(job.get("lit_s")) / total, 3)
                say_as(w, job["person"], "All done, thanks for the light.")
                continue
            if job.get("current") != current:
                j["current"] = current
                label = w.s["entities"].get(current, {}).get("label", current)
                say_as(w, job["person"], "Now I'm working on the {}.".format(label))
            light = w.ent(item)
            j["total_s"] = round(C.num(job.get("total_s")) + dt, 1)
            if light.get("on") and job.get("aim") == current:
                j["lit_s"] = round(C.num(job.get("lit_s")) + dt, 1)
            if light.get("on") and job.get("aim") in w.s.get("people", {}):
                j["glare_s"] = round(C.num(job.get("glare_s")) + dt, 1)
            j["lit_frac"] = round(C.num(j.get("lit_s")) / max(1.0, C.num(j.get("total_s"))), 3)


def _mount(w, item, result, j):
    robot = w.robot_m()
    for hand in ("left", "right"):
        if robot["hands"].get(hand) == item:
            robot["hands"][hand] = None
    m = w.mut(item)
    target = result["mount_to"]
    m["location"] = target
    m["relation"] = "hung" if target in w.s["entities"] else "on"
    m.pop("held_hand", None)
    m.pop("held_for_job", None)
    m["mounted_pose"] = dict(j.get("pose") or {})
    m["mounted"] = True
    if target in w.s["rooms"]:
        m["pos"] = list(result.get("cell") or w.robot.get("pos") or [0, 0])
        m["mount_height_m"] = round(C.num((j.get("pose") or {}).get("z"), 1.5), 3)
        m["blocks_path"] = False
    w.log_move(item, "robot", target, by="person")

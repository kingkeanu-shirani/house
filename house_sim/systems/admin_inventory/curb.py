"""Curbside collection: wheeling heavy carts (trash/recycling/yard) between the garage and the
curb, and the collection truck that only empties carts of streams scheduled this week, standing
at the curb with lids closed, handles toward the house and ~1 m from other carts, the mailbox
and the driveway apron. Wind scatters litter from open carts; untagged bags left at the curb are
taken with the trash.

Carts are owned by the cleaning domain (trash_cart/recycling_cart: wheeled=true + waste_stream);
anything with wheeled_cart=true, or wheeled=true and a waste_stream, works.
"""
import datetime
import math

from ...kernel import clock, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from ..core import subtree_mass
from .common import drop_cell, household, indexed, slug
from .perceive import mass_feel


def is_cart(rec):
    """A wheeled curbside cart: this domain's wheeled_cart flag or the cleaning domain's wheeled waste carts."""
    return bool(rec.get("wheeled_cart") or (rec.get("wheeled") and rec.get("waste_stream")))


@action("wheel_cart", params={"target": "entity"}, duration=3.0, energy=0.01, category="physical",
        doc="Tip a wheeled waste cart back onto its wheels and hold its handle with both hands (up to ~120 kg on flat ground). Move with it, then place it on the floor (place with cell) where it should stand.")
def wheel_cart(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(is_cart(rec), "That has no wheels to tip it on", "argument")
    require(not ctx.holding(target), "Already holding it", "precondition")
    hands = w.robot.get("hands", {})
    require(all(v is None for v in hands.values()), "Both hands must be free to wheel a cart", "capability")
    require(all(w.robot.get("arms_ok", {}).get(h, True) for h in ("left", "right")), "Both arms are needed", "capability")
    mass = subtree_mass(w, target)
    if ctx.realistic:
        require(mass <= float(w.robot.get("max_cart_kg", 120.0)), "Too heavy to wheel safely", "safety")
    require(mass <= float(w.robot.get("max_cart_kg", 120.0)), "Too heavy to wheel safely ({:.0f} kg)".format(mass), "safety")
    m = w.mut(target)
    before_loc = m.get("location")
    m["location"], m["relation"], m["held_hand"] = "robot", "held", "both"
    m["towed"] = True
    for key in ("pos", "spos", "shelf"):
        m.pop(key, None)
    robot = w.robot_m()
    robot["hands"]["left"] = target
    robot["hands"]["right"] = target
    w.log_move(target, before_loc, "robot")
    ctx.grant(target, "touch", ["mass_kg"])
    if ctx.realistic:
        return "Wheeling {} (it feels {})".format(target, mass_feel(ctx.env, target, mass))   # tipping it gives a feel, not kg
    return "Wheeling {} ({:.0f} kg)".format(target, mass)


@before("move", order=40)
def _no_carts_on_stairs(ctx):
    w = ctx.w
    towed = [e for e in w.held_items() if w.ent(e).get("towed")]
    if not towed:
        return
    from ..core import connection_between
    dest = ctx.args.get("target")
    cid = connection_between(w, w.robot["room"], dest) if dest in w.s["rooms"] else None
    conn = w.s.get("connections", {}).get(cid, {}) if cid else {}
    if conn.get("kind") in ("stairs", "hatch"):
        raise ActionError("A loaded cart cannot be wheeled on stairs", "safety")


@after("place", order=40)
def _untow(ctx, result):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec is not None and rec.get("towed"):
        ctx.w.mut(ctx.args["target"]).pop("towed", None)


def _schedule(state):
    cfg = {"weekday": "Thursday", "time": "07:30", "streams": {"trash": "weekly", "recycling": "even_weeks"},
           "skip_dates": [], "curb_room": None}
    cfg.update(state.get("household", {}).get("collection", {}))
    return cfg


def stream_due(state, stream, day=None):
    cfg = _schedule(state)
    day = day or clock.now(state).date()
    rule = (cfg.get("streams") or {}).get(stream)
    if not rule:
        return False
    week = day.isocalendar()[1]
    if rule == "weekly":
        return True
    if rule == "even_weeks":
        return week % 2 == 0
    if rule == "odd_weeks":
        return week % 2 == 1
    if str(rule).startswith("monthly"):
        return day.day <= 7
    return False


def collection_day(state, day=None):
    """The date of this week's pickup (shifted a day for listed holiday skips)."""
    cfg = _schedule(state)
    day = day or clock.now(state).date()
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    target = names.index(cfg.get("weekday", "Thursday"))
    start = day - datetime.timedelta(days=day.weekday())
    pickup = start + datetime.timedelta(days=target)
    while pickup.isoformat() in cfg.get("skip_dates", []):
        pickup += datetime.timedelta(days=1)
    return pickup


def curb_room(state):
    cfg = _schedule(state)
    if cfg.get("curb_room") in state["rooms"]:
        return cfg["curb_room"]
    options = sorted(r for r, v in state["rooms"].items() if v.get("type") == "sidewalk")
    return options[0] if options else None


def curb_obstacle(rec):
    """Things a collection truck needs carts kept clear of (other carts, any domain's mailbox)."""
    return bool(is_cart(rec) or rec.get("mailbox") or rec.get("category") == "mailbox")


def _curb_problems(state, cart, room):
    rec = state["entities"][cart]
    problems = []
    if rec.get("open"):
        problems.append("lid open")
    if rec.get("orientation") in ("rotated", "on_side", "upside_down") or rec.get("tipped"):
        problems.append("handles/wheels facing the street or tipped")
    cells = set(geometry.footprint(rec))
    for other, o in sorted(state["entities"].items()):
        if other == cart or o.get("location") != room or not o.get("pos"):
            continue
        if curb_obstacle(o):
            if min((geometry.chebyshev(a, b) for a in cells for b in geometry.footprint(o)), default=99) < 2:
                # the collector's tag says what was in the way, not an internal id (the tag is read-tier text)
                what = "the mailbox" if (o.get("mailbox") or o.get("category") == "mailbox") else "another cart"
                if "too close to " + what not in problems:
                    problems.append("too close to " + what)
    for conn in state.get("connections", {}).values():
        if room in conn.get("cells", {}) and any(state["rooms"][r].get("type") == "driveway" for r in conn["rooms"] if r != room):
            apron = tuple(conn["cells"][room])
            if min((geometry.chebyshev(apron, c) for c in cells), default=99) <= 1:
                problems.append("blocking the driveway")
    return problems


def run_collection(w):
    state = w.s
    room = curb_room(state)
    log = household(w).setdefault("collections", [])
    entry = {"date": clock.now(state).date().isoformat(), "emptied": [], "skipped": {}, "taken_bags": []}
    if room is None:
        log.append(entry)
        return entry
    for cart in sorted(state["entities"]):
        rec = state["entities"].get(cart)
        if rec is None or rec.get("location") != room:
            continue
        if is_cart(rec):
            stream = rec.get("waste_stream")
            if not stream_due(state, stream):
                entry["skipped"][cart] = "not scheduled this week"
                w.mut(cart)["not_collected_reason"] = "not scheduled this week"
                continue
            problems = _curb_problems(state, cart, room)
            if problems:
                entry["skipped"][cart] = "; ".join(problems)
                w.mut(cart)["not_collected_reason"] = "; ".join(problems)
                continue
            for item in w.descendants(cart):
                w.destroy(item)
            m = w.mut(cart)
            m["fill"] = 0.0
            m["emptied_at_s"] = state["time_s"]
            m.pop("not_collected_reason", None)
            entry["emptied"].append(cart)
        elif rec.get("portable") and (rec.get("category") in ("bag", "debris") or rec.get("type") in ("donation_bag",)):
            if rec.get("donation") and "donation" in str(rec.get("label_key") or ""):
                continue
            for item in w.descendants(cart):
                w.destroy(item)
            w.destroy(cart)
            entry["taken_bags"].append(cart)
    log.append(entry)
    w.emit("waste_collection", **{k: v for k, v in entry.items() if k != "date"})
    return entry


def curb_tick(w, dt, tick):
    state = w.s
    cfg = _schedule(state)
    if not state.get("household", {}).get("collection"):
        # no configured service: only the wind matters
        _wind(w, dt, tick)
        return
    now = clock.now(state)
    pickup = collection_day(state)
    if now.date() == pickup:
        hour, minute = [int(x) for x in str(cfg.get("time", "07:30")).split(":")[:2]]
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now - datetime.timedelta(seconds=dt) < when <= now:
            run_collection(w)
    _wind(w, dt, tick)


def _wind(w, dt, tick):
    state = w.s
    wind = float(state.get("weather", {}).get("wind_mps", 2.0))
    if wind < 8:
        return
    for cart in indexed(w, "carts", is_cart):
        rec = state["entities"][cart]
        if not rec.get("open"):
            continue
        room = rec.get("location")
        if room not in state["rooms"] or not state["rooms"][room].get("outdoor"):
            continue
        rng = tick.rng("admin_wind:" + cart)
        if rng.random() < 1.0 - math.exp(-dt / 1800.0):   # ~one gust-blown item per 30 min (dt-correct)
            litter = w.spawn("generic_item", room, label="blown litter", category="debris", portable=True,
                             mass_kg=0.02, size_m=[0.1, 0.1, 0.01], volume_l=0.05, waste_stream=rec.get("waste_stream", "trash"),
                             soil={"litter": 0.5})
            w.mut(litter)["pos"] = drop_cell(w, room, rec.get("pos"), litter)
            w.emit("litter_blown", source=cart, item=litter)


@hook("event:admin_collection")
def _event_collection(w, args, env):
    return run_collection(w)

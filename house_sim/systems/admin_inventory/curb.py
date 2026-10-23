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

from ...kernel import body, clock, geometry
from ...kernel.phys import environment as phys_env
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from ..core import subtree_mass
from .common import drop_cell, household, indexed, notice, slug, visual_event
from .perceive import mass_feel


LITTER_GUST_MPS = 7.2       # local gust (m/s at about 1 m) that lifts paper and film out of an open cart: the old rule's
                            # 8 m/s 10 m mean with the default 1.5 gust factor and ground exposure (0.6 of the gust)


def is_cart(rec):
    """A wheeled curbside cart: this domain's wheeled_cart flag or the cleaning domain's wheeled waste carts."""
    return bool(rec.get("wheeled_cart") or (rec.get("wheeled") and rec.get("waste_stream")))


@action("wheel_cart", params={"target": "entity"}, duration=3.0, energy=0.01, category="physical", arms=2,
        doc="Tip a wheeled waste cart back onto its wheels and hold its handle with both hands (the handle bears about 15 % of the loaded cart: up to about 130 kg for the default robot, on flat ground). Move with it, then place it on the floor (place with cell) where it should stand.")
def wheel_cart(ctx):
    """Tip a cart back and tow it. The body decides (TIER1_EMBODIMENT 4.7): both arms working (ctx.require_arm), both
    hands free, and the load on the handle -- body.TOWED_SHARE of the loaded cart, which rolls on its wheels -- within
    the derated two-hand capacity (ctx.check_lift: an overload is refused, counted and felt). While towed the cart
    counts that share in what the robot carries (body.load_share)."""
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(is_cart(rec), "That has no wheels to tip it on", "argument")
    require(not ctx.holding(target), "Already holding it", "precondition")
    ctx.require_arm("left")
    ctx.require_arm("right")
    hands = w.robot.get("hands", {})
    require(all(v is None for v in hands.values()), "Both hands must be free to wheel a cart", "capability")
    mass = subtree_mass(w, target)
    ctx.check_lift(target, "both", mass * body.TOWED_SHARE)
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
    day = day or clock.wall(state).date()
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
    day = day or clock.wall(state).date()
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
    entry = {"date": clock.wall(state).date().isoformat(), "emptied": [], "skipped": {}, "taken_bags": []}
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
    if entry["emptied"] or entry["taken_bags"]:
        visual_event(w, "waste_collection", room=room, size_m=2.5)      # the truck and its crew at the curb
    notice(w, "collection_truck", room=room)      # the truck is heard (kernel v3.1 notices: wait until it has come)
    return entry


def curb_tick(w, dt, tick):
    state = w.s
    cfg = _schedule(state)
    if not state.get("household", {}).get("collection"):
        # no configured service: only the wind matters
        _wind(w, dt, tick)
        return
    now = clock.wall(state)      # the pickup time is a wall-clock time (daylight saving time included)
    pickup = collection_day(state)
    if now.date() == pickup:
        hour, minute = [int(x) for x in str(cfg.get("time", "07:30")).split(":")[:2]]
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now - datetime.timedelta(seconds=dt) < when <= now:
            run_collection(w)
    _wind(w, dt, tick)


def _wind(w, dt, tick):
    """Gusts lift light rubbish out of an open cart standing outdoors (about one piece per 30 min of gusts at or over
    LITTER_GUST_MPS at the cart). The wind at the cart is the kernel's (phys.environment.object_wind_mps: the weather's
    10 m mean and gust scaled to about 1 m by the room's exposure) and only a cart out in the weather counts
    (environment.exposed: not under a porch roof or a cover). The kernel's wind law moves and tips loose things but
    never lifts what is inside a container, so this rule is no duplicate of it: it runs whatever the law's state."""
    state = w.s
    weather = state.get("weather") or {}
    if phys_env.wind_speed_mps(weather) < phys_env.CALM_MPS:
        return      # calm (gusts included): nothing outdoors moves
    for cart in indexed(w, "carts", is_cart):
        rec = state["entities"][cart]
        if not rec.get("open"):
            continue
        room = rec.get("location")
        if room not in state["rooms"] or not state["rooms"][room].get("outdoor"):
            continue
        _, gust = phys_env.object_wind_mps(state, cart, weather)
        if gust < LITTER_GUST_MPS - 1e-9 or not phys_env.exposed(state, cart):
            continue
        rng = tick.rng("admin_wind:" + cart)
        if rng.random() < 1.0 - math.exp(-dt / 1800.0):   # ~one gust-blown item per 30 min (dt-correct)
            litter = w.spawn("generic_item", room, label="blown litter", category="debris", portable=True,
                             mass_kg=0.02, size_m=[0.1, 0.1, 0.01], volume_l=0.05, waste_stream=rec.get("waste_stream", "trash"),
                             soil={"litter": 0.5})
            w.mut(litter)["pos"] = drop_cell(w, room, rec.get("pos"), litter)
            w.emit("litter_blown", source=cart, item=litter)
            visual_event(w, "litter_blown", source=litter, room=room, size_m=0.2)


@hook("event:admin_collection")
def _event_collection(w, args, env):
    return run_collection(w)


# ----------------------------------------------------------------------
# the household calendar (TIER1_SWEEP 11.2: admin_inventory exports collection days and carrier pickups as events)

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def collection_events(config):
    """household.collection as household.calendar events (kind "collection", one per stream): weekly streams recur on
    the pickup weekday, even / odd-week streams every second week from the first matching week, at the pickup time; a
    skipped date is an exception of the recurrence and its shifted pickup (the next day, collection_day's rule) an
    event of its own. Monthly streams (the first pickup weekday of each month) list their occurrences of the next
    twelve months. [] without a collection rule."""
    hh = config.get("household") or {}
    if not hh.get("collection"):
        return []
    cfg = _schedule({"household": hh})
    start = datetime.datetime.fromisoformat(str((config.get("clock") or {}).get("start") or "2026-10-09T10:00:00")).date()
    weekday = WEEKDAY_NAMES.index(cfg.get("weekday", "Thursday")) if cfg.get("weekday", "Thursday") in WEEKDAY_NAMES else 3
    first = start + datetime.timedelta(days=(weekday - start.weekday()) % 7)
    hhmm = str(cfg.get("time", "07:30"))[:5]
    skips = sorted(str(d) for d in cfg.get("skip_dates") or [])
    events = []
    for stream, rule in sorted((cfg.get("streams") or {}).items()):
        base = {"kind": "collection", "title": "{} collection".format(str(stream).replace("_", " ").capitalize()),
                "attributes": {"stream": stream}, "known_by": "everyone"}
        if rule in ("weekly", "even_weeks", "odd_weeks"):
            day = first
            if rule != "weekly":
                while (day.isocalendar()[1] % 2 == 0) != (rule == "even_weeks"):
                    day += datetime.timedelta(days=7)
            event = dict(base, id="admin_collection_{}".format(slug(stream)),
                         start="{}T{}".format(day.isoformat(), hhmm),
                         recurrence={"freq": "weekly", "interval": 1 if rule == "weekly" else 2,
                                     "byday": [WEEKDAY_CODES[weekday]]})
            if skips:
                event["recurrence"]["except"] = skips
            events.append(event)
            for skipped in skips:
                d = datetime.date.fromisoformat(skipped)
                due = rule == "weekly" or (d.isocalendar()[1] % 2 == 0) == (rule == "even_weeks")
                if due and d >= start and d.weekday() == weekday:
                    shifted = d + datetime.timedelta(days=1)
                    while shifted.isoformat() in skips:
                        shifted += datetime.timedelta(days=1)
                    events.append(dict(base, id="admin_collection_{}_{}".format(slug(stream), slug(skipped)),
                                       start="{}T{}".format(shifted.isoformat(), hhmm),
                                       notes="moved from {}".format(skipped)))
        elif str(rule).startswith("monthly"):
            day, n = first, 0
            while n < 12 and day <= start + datetime.timedelta(days=366):
                if day.day <= 7:
                    events.append(dict(base, id="admin_collection_{}_{}".format(slug(stream), day.isoformat()),
                                       start="{}T{}".format(day.isoformat(), hhmm)))
                    n += 1
                day += datetime.timedelta(days=7)
    return events


def add_calendar_events(config, events):
    """Append events to household.calendar (the people domain's model reads them: wall calendar and whiteboard text,
    the calendar app, ask when) skipping ids already there."""
    calendar = config.setdefault("household", {}).setdefault("calendar", {})
    have = {e.get("id") for e in calendar.setdefault("events", []) if isinstance(e, dict)}
    for event in events:
        if event["id"] not in have:
            calendar["events"].append(event)
            have.add(event["id"])
    calendar.setdefault("sources", {})

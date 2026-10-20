"""The household calendar (TIER1_SWEEP 11.2): what household.calendar events mean to the people in the house.

household.calendar = {"events": [...], "sources": {"wall_calendar": eid | [eids] | true, "whiteboard": ...,
"app": true}} comes from the setup op `calendar`; the kernel's dialogue answers ask(aspect="when") about an event from
it (and its hidden values). The people domain owns the rest:

* Occurrences: occurrences(event, d0, d1) is clock.occurrences plus the yearly recurrence op_calendar accepts (a
  birthday: the same month and day every year; 29 February only in leap years). A date-only start is an all-day event.
* Visibility: an event shows on the sources its visible_on names (default every source), with the attributes that
  are true as words ("School (Maya): PE"). Its hidden values and notes are never shown; a cancelled occurrence shows
  crossed out, a tentative one with "(?)".
* Calendar artifacts (types wall_calendar and whiteboard): the wall calendar shows its displayed month
  (displayed_month "YYYY-MM", default the current month), the whiteboard the current week (Monday to Sunday). The
  visible occurrences are written into the artifact's `text` (read tier) at the episode start and again when the
  date, the displayed period or the events change (system people.calendar, period 60 s, exact idle probe). The
  artifacts are the entities a source names, or every wall_calendar / whiteboard in the house when that source is not
  named (false: none). Text below the calendar block (a note written on the whiteboard) is kept by a re-render.
* people.calendar_board (helper) mounts a wall calendar or whiteboard on a free wall cell that can be walked up to and
  seen from the room, and registers it as that source.
* check_calendar (action): the household calendar app over the network (sources.app true): `days` (1-31) from
  `start` (today, tomorrow or an ISO date; default today), optionally for one person.
* Schedules from events: a resident who follows their schedule (routine FULL mode) is away for an occurrence of an
  away kind (AWAY_KINDS; a party or birthday only with a location other than home) and works from home for
  work_remote; cancelled events, and tentative ones whose hidden "happens" is false, take nobody anywhere
  (event_block, used by routine.py).
* Event needs: EVENT_NEEDS maps a kind and its true attributes to item kinds (NEED_TYPES), for each person the event
  is for ("each": school, work, practice; each traveller on a trip) or once per event ("once"). needs_for() lists
  them; the helper people.event_needs places them (decoys for the false attributes) and returns needs_ok. The table
  lives here (the sweep's data/people/event_needs.json would sit outside the people files).

Log: people_log.calendar_checks (check_calendar) and people_log._calendar {eid: {"sig", "block"}} (what each artifact
shows, so a re-render keeps the text below it).
"""
import copy
import datetime
import json

from ...kernel import clock, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, SpecError, require
from ...kernel.plugins import hook, scenario_helper, system
from ...kernel.predicates import root_room
from . import common as C

KINDS = ("school", "work_office", "work_remote", "appointment", "service_visit", "delivery", "carrier_pickup",
         "collection", "trip", "practice", "lesson", "party", "birthday", "holiday")
AWAY_KINDS = ("school", "work_office", "appointment", "trip", "practice", "lesson")
OUTING_KINDS = ("party", "birthday")          # away only when the event says where, and it is not home
SOURCES = ("wall_calendar", "whiteboard", "app")
ARTIFACT_TYPES = {"wall_calendar": "wall_calendar", "whiteboard": "whiteboard"}
MAX_SPAN_DAYS = 31                            # the longest occurrence (a trip) that can still be in progress today
CHECK_S = 20.0                                # opening the app and reading the days asked for
WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# kind -> {"each": [item kinds per person], "once": [item kinds per event], "attributes": {attr: list | {each, once}}}
EVENT_NEEDS = {
    "school": {"each": ["backpack", "lunch_box"],
               "attributes": {"pe": ["sneakers"], "swim": ["swimsuit", "towel", "goggles"], "library": ["library_book"],
                              "field_trip": ["water_bottle"]}},
    "work_office": {"each": ["laptop", "badge", "laptop_charger"], "attributes": {"gym": ["sneakers"]}},
    "practice": {"each": ["water_bottle"], "attributes": {"sport": ["sneakers"], "swim": ["swimsuit", "towel", "goggles"]}},
    "lesson": {"attributes": {"swim": ["swimsuit", "towel", "goggles"]}},
    "trip": {"each": ["toiletries"], "once": ["phone_charger"], "attributes": {"international": ["passport"]}},
    "party": {"attributes": {"gift": {"once": ["gift"]}}},
}
# item kind -> the types that serve (first: the one placed)
NEED_TYPES = {
    "backpack": ("backpack",), "lunch_box": ("lunch_box",), "sneakers": ("sneaker",), "swimsuit": ("swimsuit",),
    "towel": ("bath_towel", "beach_towel", "towel"), "goggles": ("swim_goggles",), "library_book": ("library_book",),
    "water_bottle": ("water_bottle", "named_water_bottle"), "laptop": ("laptop",), "badge": ("id_badge",),
    "laptop_charger": ("laptop_charger",), "phone_charger": ("usb_wall_charger",), "passport": ("passport",),
    "toiletries": ("toiletry_bag",), "gift": ("wrapped_gift",),
}
# stand-ins (generic_item) when the type's domain is not loaded (HOUSE_SIM_DOMAINS): label, category, mass, size
STAND_INS = {
    "backpack": ("backpack", "bag", 0.6, [0.3, 0.2, 0.42]), "lunch_box": ("lunch box", "lunch_box", 0.4, [0.25, 0.1, 0.2]),
    "sneakers": ("sneakers", "shoe", 0.7, [0.3, 0.2, 0.12]), "swimsuit": ("swimsuit", "garment", 0.15, [0.3, 0.25, 0.03]),
    "towel": ("towel", "towel", 0.6, [0.35, 0.25, 0.08]), "water_bottle": ("water bottle", "bottle", 0.3, [0.08, 0.08, 0.25]),
    "laptop": ("laptop", "laptop", 1.6, [0.33, 0.23, 0.02]), "badge": ("ID badge", "personal", 0.01, [0.086, 0.054, 0.002]),
    "laptop_charger": ("laptop charger", "charger", 0.35, [0.12, 0.06, 0.03]),
    "phone_charger": ("phone charger", "charger", 0.05, [0.04, 0.04, 0.03]),
    "passport": ("passport", "document", 0.04, [0.125, 0.088, 0.005]),
}
NAME_LABELLED = ("backpack", "lunch_box", "sneakers", "swimsuit", "towel", "goggles", "water_bottle")   # a child's school things
ATTRIBUTE_WORDS = {"pe": "PE", "library": "library day", "swim": "swim", "field_trip": "field trip", "gym": "gym",
                   "sport": "sport", "international": "international", "gift": "bring a gift"}
PLACEMENT_KEYS = ("location", "relation", "pos", "spos", "rot", "shelf", "home", "type", "is_a", "held_hand")
SPOT_TYPES = ("desk", "dresser", "nightstand", "side_table", "table", "coffee_table", "dining_table", "bed")


# ----------------------------------------------------------------------
# events and occurrences

def calendar_of(state):
    cal = C.household(state).get("calendar")
    return cal if isinstance(cal, dict) else {}


def events(state):
    return [e for e in calendar_of(state).get("events") or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]


def event(state, eid):
    return next((e for e in events(state) if e["id"] == eid), None)


def _dt(text):
    text = str(text)
    if len(text) == 10:
        day = datetime.date.fromisoformat(text)
        return datetime.datetime(day.year, day.month, day.day)
    return datetime.datetime.fromisoformat(text.replace(" ", "T", 1))


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def all_day(ev):
    return len(str(ev.get("start", ""))) == 10


def _yearly(ev, d0, d1):
    start = _dt(ev["start"])
    length = (_dt(ev["end"]) if ev.get("end") else start) - start
    rec = ev.get("recurrence") or {}
    until = _date(rec["until"]) if rec.get("until") else None
    count = int(rec["count"]) if rec.get("count") is not None else None
    excluded = {_date(x) for x in rec.get("except", [])}
    interval = max(1, int(rec.get("interval", 1)))
    out = []
    for year in range(start.year, d1.year + 1):
        n = year - start.year
        if n % interval:
            continue
        if count is not None and n // interval >= count:
            break
        try:
            begin = start.replace(year=year)
        except ValueError:
            continue                        # 29 February in a common year
        if until and begin.date() > until:
            break
        if d0 <= begin.date() <= d1 and begin.date() not in excluded:
            out.append({"start": begin.isoformat(timespec="minutes"), "end": (begin + length).isoformat(timespec="minutes")})
    return out


def occurrences(ev, d0, d1):
    """[{"start", "end"}] of ev whose start date lies in [d0, d1] (clock.occurrences, plus yearly recurrences)."""
    d0, d1 = _date(d0), _date(d1)
    if (ev.get("recurrence") or {}).get("freq") == "yearly":
        return _yearly(ev, d0, d1)
    try:
        return clock.occurrences(ev, d0, d1)
    except (ValueError, KeyError, TypeError):
        return []


def span(ev, occ):
    """(start, end) datetimes of an occurrence; an all-day occurrence runs to the end of its last day."""
    s, e = _dt(occ["start"]), _dt(occ["end"])
    if all_day(ev):
        e = datetime.datetime.combine(e.date(), datetime.time()) + datetime.timedelta(days=1)
    return s, e


def visible_on(ev, source):
    shown = ev.get("visible_on")
    return source in (shown if isinstance(shown, list) else SOURCES)


def happens(ev):
    """False for a cancelled event and for a tentative one whose hidden "happens" is false."""
    if ev.get("status") == "cancelled":
        return False
    return not (ev.get("status") == "tentative" and (ev.get("hidden") or {}).get("happens") is False)


def _name(state, pid):
    if pid in (state.get("people") or {}):
        return C.label_of(state, pid)
    return str(pid).replace("_", " ").title()


def public(state, ev, occ):
    """What a calendar shows of one occurrence: the title, times, who, where, status and the attributes that are true
    (as words: "PE", "library day"); never the hidden values or notes."""
    out = {"event": ev["id"], "title": str(ev.get("title") or ev.get("kind") or ev["id"]), "start": occ["start"],
           "end": occ["end"], "status": ev.get("status", "confirmed"), "who": [w for w in ev.get("who") or []],
           "names": [_name(state, w) for w in ev.get("who") or []]}
    details = [ATTRIBUTE_WORDS.get(k, str(k).replace("_", " ")) for k, v in sorted((ev.get("attributes") or {}).items())
               if v is True]
    if details:
        out["details"] = details
    if isinstance(ev.get("kind"), str):
        out["kind"] = ev["kind"]
    if isinstance(ev.get("location"), str):
        out["location"] = ev["location"]
    if all_day(ev):
        out["all_day"] = True
    return out


def shown(state, source, d0, d1, who=None):
    """The public occurrences of the events visible on `source` that start in [d0, d1] (optionally one person's), in
    time order."""
    out = []
    for ev in events(state):
        if not visible_on(ev, source) or (who is not None and who not in (ev.get("who") or [])):
            continue
        for occ in occurrences(ev, d0, d1):
            out.append(public(state, ev, occ))
    out.sort(key=lambda o: (o["start"], o["event"]))
    return out


# ----------------------------------------------------------------------
# calendar artifacts

def _line(state, occ):
    s, e = _dt(occ["start"]), _dt(occ["end"])
    day = "{} {}".format(WEEKDAY_ABBR[s.weekday()], s.day)
    if occ.get("all_day"):
        when = day if e.date() == s.date() else "{} - {} {}".format(day, WEEKDAY_ABBR[e.weekday()], e.day)
    elif e.date() != s.date():
        when = "{} {} - {} {} {}".format(day, s.strftime("%H:%M"), WEEKDAY_ABBR[e.weekday()], e.day, e.strftime("%H:%M"))
    else:
        when = "{}  {}-{}".format(day, s.strftime("%H:%M"), e.strftime("%H:%M"))
    text = "{}  {}".format(when, occ["title"])
    if occ["status"] == "tentative":
        text += " (?)"
    if occ["names"]:
        text += " ({})".format(", ".join(occ["names"]))
    if occ.get("details"):
        text += ": " + ", ".join(occ["details"])
    where = occ.get("location")
    if where and where.lower() not in (occ["title"].lower(), "home"):
        text += ", " + where.replace("_", " ")
    if occ["status"] == "cancelled":
        text += " - crossed out"
    return text


def _period(state, rec, kind, today):
    if kind == "wall_calendar":
        month = rec.get("displayed_month")
        try:
            first = datetime.date.fromisoformat(str(month) + "-01") if month else today.replace(day=1)
        except ValueError:
            first = today.replace(day=1)
        nxt = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        return first, nxt - datetime.timedelta(days=1), "{} {}".format(clock.MONTHS[first.month - 1], first.year)
    monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=6), "Week of " + clock.format_date(state, monday, "long")


def render(state, eid, kind):
    """The calendar block an artifact shows today."""
    rec = state["entities"][eid]
    d0, d1, head = _period(state, rec, kind, clock.today(state))
    lines = [head] + [_line(state, occ) for occ in shown(state, kind, d0, d1)]
    if len(lines) == 1:
        lines.append("(no entries)")
    return "\n".join(lines)


def artifacts(state):
    """[(eid, source kind)] of the entities that show the household calendar (none without events)."""
    if not events(state):
        return []
    sources = calendar_of(state).get("sources") or {}
    ents = state.get("entities", {})
    out = []
    for kind, type_name in sorted(ARTIFACT_TYPES.items()):
        named = sources.get(kind)
        if named is False:
            continue
        if isinstance(named, str):
            ids = [named]
        elif isinstance(named, list):
            ids = [x for x in named if isinstance(x, str)]
        else:
            ids = sorted(e for e, r in ents.items() if r.get("type") == type_name or type_name in (r.get("is_a") or ()))
        out.extend((e, kind) for e in ids if e in ents)
    return out


def _signature(state, eid, kind):
    rec = state["entities"][eid]
    d0, _, _ = _period(state, rec, kind, clock.today(state))
    return "{}|{}|{}".format(kind, d0.isoformat(), json.dumps(events(state), sort_keys=True, default=str))


def _stale(state):
    shown_now = (C.plog_read(state).get("_calendar") or {})
    return [(eid, kind) for eid, kind in artifacts(state)
            if (shown_now.get(eid) or {}).get("sig") != _signature(state, eid, kind)]


def _render_all(w):
    state = w.s
    for eid, kind in _stale(state):
        memo = C.plog(w).setdefault("_calendar", {})
        before = memo.get(eid) or {}
        current = str(state["entities"][eid].get("text") or "")
        block = render(state, eid, kind)
        old = before.get("block")
        if old is not None and current.startswith(old):
            tail = current[len(old):]
        else:
            tail = ("\n" + current) if current else ""
        w.mut(eid)["text"] = block + tail
        memo[eid] = {"sig": _signature(state, eid, kind), "block": block}


@hook("episode_start", order=40)
def _calendar_at_start(w, env):
    if events(w.s):
        _render_all(w)


def calendar_idle(w):
    """Exact idle probe: no calendar artifact whose date, displayed period or events changed since it was written."""
    return not events(w.s) or not _stale(w.s)


@system("people.calendar", order=104, max_dt=60.0, period=60.0, idle=calendar_idle)
def calendar_system(w, dt, tick):
    _render_all(w)


# ----------------------------------------------------------------------
# the calendar app

def _start_date(state, text):
    if text is None or str(text).strip().lower() in ("", "today"):
        return clock.today(state)
    low = str(text).strip().lower()
    if low == "tomorrow":
        return clock.tomorrow(state)
    if low == "yesterday":
        return clock.today(state) - datetime.timedelta(days=1)
    try:
        return datetime.date.fromisoformat(low[:10])
    except ValueError:
        raise ActionError("start must be today, tomorrow or a date like 2026-10-12", "argument")


def _person_of(state, who):
    people = state.get("people") or {}
    if who in people:
        return who
    hits = sorted(pid for pid in people if C.norm(C.label_of(state, pid)) == C.norm(who))
    require(len(hits) == 1, "Unknown person: {}".format(who), "argument")
    return hits[0]


@action("check_calendar", params={"days": {"type": "int", "default": 7}, "start": "?str", "who": "?str"},
        duration=CHECK_S, energy=0.0, category="cognitive",
        doc="Read the household's shared calendar app (needs the network and a calendar app): the entries of `days` "
            "days (1-31) from `start` (today, tomorrow or YYYY-MM-DD; default today), optionally only one person's. "
            "Shows what the app shows (titles, times, who, where, status), not private details.")
def check_calendar(ctx):
    state = ctx.w.s
    require(state["robot"].get("network", True), "No network connection for the calendar app", "resource")
    require(calendar_of(state).get("sources", {}).get("app") is True, "The household has no shared calendar app",
            "precondition")
    days = int(ctx.args.get("days", 7))
    require(1 <= days <= 31, "days must be 1 to 31", "argument")
    d0 = _start_date(state, ctx.args.get("start"))
    d1 = d0 + datetime.timedelta(days=days - 1)
    who = _person_of(state, ctx.args["who"]) if ctx.args.get("who") else None
    found = shown(state, "app", d0, d1, who)
    C.log_event(ctx.w, "calendar_checks", start=d0.isoformat(), days=days, who=who, entries=len(found))
    return {"from": d0.isoformat(), "to": d1.isoformat(), "entries": found}


# ----------------------------------------------------------------------
# schedules from events

def _work_room(state, p):
    rooms = state.get("rooms", {})
    offices = sorted(r for r, v in rooms.items() if v.get("type") in ("office", "study"))
    return offices[0] if offices else p.get("home_room")


def activity_of(ev):
    kind = ev.get("kind")
    where = ev.get("location")
    if kind in AWAY_KINDS:
        return None if where == "home" else "away"
    if kind in OUTING_KINDS:
        return "away" if isinstance(where, str) and where and where != "home" else None
    if kind == "work_remote":
        return "working"
    return None


def event_block(state, pid, p):
    """The schedule block an event in progress makes for pid (routine FULL mode), or None: {"activity": away |
    working, "what": title, "event": id, "start": iso, "room"?}. The latest-starting occurrence wins."""
    evs = events(state)
    if not evs:
        return None
    now = C.wall(state)
    today = now.date()
    best = None
    for ev in evs:
        if pid not in (ev.get("who") or []):
            continue
        activity = activity_of(ev)
        if activity is None or not happens(ev):
            continue
        for occ in occurrences(ev, today - datetime.timedelta(days=MAX_SPAN_DAYS), today):
            s, e = span(ev, occ)
            if s <= now < e and (best is None or (occ["start"], ev["id"]) > (best[0], best[1])):
                best = (occ["start"], ev["id"], activity, ev)
    if best is None:
        return None
    start, eid, activity, ev = best
    block = {"activity": activity, "what": str(ev.get("title") or ev.get("kind") or eid), "event": eid, "start": start}
    if activity == "working":
        block["room"] = _work_room(state, p)
    return block


def person_schedule(state, pid, days=7):
    """A person's calendar entries from today for `days` days, as the person would tell them (None: no events)."""
    today = clock.today(state)
    found = []
    for ev in events(state):
        if pid not in (ev.get("who") or []):
            continue
        for occ in occurrences(ev, today, today + datetime.timedelta(days=days - 1)):
            found.append((occ["start"], ev["id"], _line(state, public(state, ev, occ))))
    return [line for _, _, line in sorted(found)] or None


def readable_somewhere(state, pid):
    """True when every one of pid's upcoming entries is visible on a calendar the robot could read (an artifact in the
    house, or the app)."""
    have = {kind for _, kind in artifacts(state)}
    if calendar_of(state).get("sources", {}).get("app") is True:
        have.add("app")
    mine = [ev for ev in events(state) if pid in (ev.get("who") or [])]
    return bool(mine) and all(any(visible_on(ev, s) for s in have) for ev in mine)


# ----------------------------------------------------------------------
# a calendar on the wall

def _seen_from_somewhere(st, rid, eid):
    """Can a standing robot see the board from some free cell of the room (sight.best_viewpoints)?"""
    from ...kernel import sight
    try:
        return bool(sight.best_viewpoints(st, rid, eid, k=1))
    except Exception:  # noqa: BLE001 - a setup-time view without a body profile: the walkable cell suffices
        return True


@scenario_helper("people.calendar_board")
def calendar_board(ctx, kind="wall_calendar", room="kitchen", **props):
    """Mount a wall calendar or a whiteboard (kind) in a room (id or type) on a free wall cell that can be walked up to
    from the doorways and that a standing robot can see from somewhere in the room (so the geometric camera can read
    it as well as room mode), and register it as that household.calendar source. Returns {"board": eid, kind: eid}."""
    from .helpers import _occupied, _room, _state, _view, _walkable_to
    if kind not in ARTIFACT_TYPES:
        raise SpecError("calendar_board: kind must be wall_calendar or whiteboard")
    rid = _room(ctx, room)
    cols, rows = geometry.grid_size(ctx.config["rooms"][rid])
    perimeter = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} |
                       {(0, y) for y in range(rows)} | {(cols - 1, y) for y in range(rows)})
    blocked = set(geometry.blocked_cells(_state(ctx), rid)) | set(_occupied(ctx, rid))
    doorways = set(geometry.doorway_cells(_state(ctx), rid))
    order = list(perimeter)
    ctx.rng.shuffle(order)
    eid = None
    for cell in order:
        if cell in blocked or cell in doorways:
            continue
        goals = geometry.near_cells([cell], ctx.config["rooms"][rid]) - blocked
        if not goals or not _walkable_to(ctx, rid, goals):
            continue
        eid = ctx.placer.mount(rid, ARTIFACT_TYPES[kind], cell=list(cell), **props)
        if _seen_from_somewhere(_view(ctx), rid, eid):
            break
        ctx.config["entities"].pop(eid)
        eid = None
    if eid is None:
        raise SpecError("calendar_board: no free, visible wall cell in {}".format(rid))
    sources = ctx.config.setdefault("household", {}).setdefault("calendar", {}).setdefault("sources", {})
    have = sources.get(kind)
    sources[kind] = eid if not isinstance(have, (str, list)) else sorted(set([have] if isinstance(have, str) else have) | {eid})
    return {"board": eid, kind: eid}


# ----------------------------------------------------------------------
# event needs

def needs_for(ev):
    """[(item kind, owner or None)] an event needs: each person's items, then the event's shared ones; attributes that
    are true add theirs."""
    table = EVENT_NEEDS.get(ev.get("kind")) or {}
    who = [w for w in ev.get("who") or [] if isinstance(w, str)]
    each, once = list(table.get("each") or []), list(table.get("once") or [])
    attrs = ev.get("attributes") or {}
    for attr, add in sorted((table.get("attributes") or {}).items()):
        if attrs.get(attr) is True:
            if isinstance(add, dict):
                each += add.get("each") or []
                once += add.get("once") or []
            else:
                each += add
    out = [(kind, pid) for pid in who for kind in each]
    out += [(kind, None) for kind in once]
    return out


def decoys_for(ev):
    """[(item kind, owner)] the attributes that are false would need (packing them is wrong)."""
    table = EVENT_NEEDS.get(ev.get("kind")) or {}
    wanted = set(needs_for(ev))
    who = [w for w in ev.get("who") or [] if isinstance(w, str)]
    out = []
    for attr, add in sorted((table.get("attributes") or {}).items()):
        if (ev.get("attributes") or {}).get(attr) is False:
            kinds = (add.get("each") or []) + (add.get("once") or []) if isinstance(add, dict) else add
            for kind in kinds:
                for pid in who[:1]:
                    if (kind, pid) not in wanted and (kind, pid) not in out:
                        out.append((kind, pid))
    return out


def matches(rec, kind):
    """Does an entity record serve as this item kind (its type or an ancestor, or the generic stand-in)?"""
    types = NEED_TYPES.get(kind) or ()
    if rec.get("type") in types or set(types) & set(rec.get("is_a") or ()):
        return True
    stand = STAND_INS.get(kind)
    return bool(stand and rec.get("type") == "generic_item" and rec.get("label") == stand[0])


def _occurs_on(ev, day):
    return bool(occurrences(ev, day, day))


@scenario_helper("people.event_needs")
def event_needs(ctx, event, date="tomorrow", place=True, decoys=True, staging=None):
    """The items one occurrence of a household.calendar event needs (add the `calendar` op first): each person's items
    in their bedroom (on a desk, dresser or nightstand, else the floor; a child's school things carry a name label),
    the shared ones in the living room; decoys: the items a false attribute would need (sneakers when pe is false),
    owned by the first person. Generated belongings serve first (one already in the staging room is moved to its
    owner's room). place false: only items already in the house are used. Returns needs (ids, also as
    need_1.. and need_<kind>, the first of that kind), needs_kinds, extras (decoy ids, also extra_1..), owners, staging
    (default the room inside the front door) and needs_ok (every needed item in the staging room, no decoy there)."""
    from .door import door_rooms, front_door
    from .furnish import _home_rooms
    from .helpers import _put_on_any, _reachable_all, _room
    ev = next((e for e in ((ctx.config.get("household") or {}).get("calendar") or {}).get("events") or []
               if isinstance(e, dict) and e.get("id") == event), None)
    if ev is None:
        raise SpecError("event_needs: no calendar event {} (add the calendar op before this helper)".format(event))
    today = clock.today({"clock": ctx.config.get("clock") or {}, "time_s": 0.0})
    day = today + datetime.timedelta(days=1) if date == "tomorrow" else today if date == "today" else _date(date)
    if not _occurs_on(ev, day):
        raise SpecError("event_needs: {} does not occur on {}".format(event, day.isoformat()))
    st = ctx.placer.state()
    if staging is None:
        door = front_door(st)
        staging = door_rooms(st, door)[0] if door else None
    staging = _room(ctx, staging) if staging else ctx.config["start_room"]
    homes = _home_rooms(ctx.config)
    people = ctx.config.get("people", {})
    used, owners = [], {}

    def room_for(owner):
        room = homes.get(owner) if owner else None
        if room in (None, staging) or room not in ctx.config["rooms"]:
            room = next((r for r in sorted(ctx.config["rooms"]) if ctx.config["rooms"][r].get("type") == "living_room"
                         and r != staging), None) or next(r for r in sorted(ctx.config["rooms"])
                                                         if r != staging and not ctx.config["rooms"][r].get("outdoor"))
        return room

    def put(room, type_name, **props):
        spots = _reachable_all(ctx, SPOT_TYPES, room=room)
        return _put_on_any(ctx, spots, type_name, **props) or ctx.placer.floor_item(room, type_name, **props)

    def item(kind, owner):
        for eid in sorted(ctx.config["entities"]):
            rec = ctx.config["entities"][eid]
            mine = rec.get("owner") == owner if owner else rec.get("owner") in (None, "household")
            if eid not in used and mine and matches(rec, kind):
                if root_room(ctx.placer.state(), eid) != staging:
                    return eid
                # already where it has to go (shoes on the entry rack): it waits in its owner's room instead
                old = ctx.config["entities"].pop(eid)
                keep = {k: copy.deepcopy(v) for k, v in old.items() if k not in PLACEMENT_KEYS}
                if put(room_for(owner), old["type"], eid=eid, **keep) is None:
                    ctx.config["entities"][eid] = old
                    raise SpecError("event_needs: no room to move {} out of {}".format(eid, staging))
                return eid
        if not place:
            raise SpecError("event_needs: no {} for {} in the house".format(kind, owner or "the household"))
        props = {"owner": owner or "household", "secret_fields": ["owner"]}
        p = people.get(owner) or {}
        if owner and kind in NAME_LABELLED and (C.is_minor(p) or p.get("role") in ("child", "teen")):
            props["label_text"] = str(p.get("label", owner)).split(" (")[0].upper()
        type_name = next((t for t in NEED_TYPES[kind] if t in ctx.placer.reg.types), None)
        if type_name is None:
            label, category, mass, size = STAND_INS[kind]
            type_name = "generic_item"
            props.update(label=label, category=category, mass_kg=mass, size_m=list(size))
        eid = put(room_for(owner), type_name, **props)
        if eid is None:
            raise SpecError("event_needs: no room for a {} in {}".format(kind, room_for(owner)))
        return eid

    needs, kinds = [], []
    for kind, owner in needs_for(ev):
        eid = item(kind, owner)
        used.append(eid)
        needs.append(eid)
        kinds.append(kind)
        owners[eid] = owner or "household"
    extras = []
    if decoys:
        for kind, owner in decoys_for(ev):
            eid = item(kind, owner)
            used.append(eid)
            extras.append(eid)
            owners[eid] = owner or "household"
    checks = [{"exists": {"where": {"ids": [eid], "room": staging}}} for eid in needs]
    checks += [{"not": {"exists": {"where": {"ids": [eid], "room": staging}}}} for eid in extras]
    out = {"event": event, "date": day.isoformat(), "needs": needs, "needs_kinds": kinds, "extras": extras,
           "owners": owners, "staging": staging, "needs_ok": {"all": checks}}
    for i, (eid, kind) in enumerate(zip(needs, kinds)):
        out["need_{}".format(i + 1)] = eid
        out.setdefault("need_" + kind, eid)
    for i, eid in enumerate(extras):
        out["extra_{}".format(i + 1)] = eid
    return out

"""Reference skills for food task specs. They route with house_sim.tasks.skills and change state only
through runner.do (env.step). Decisions are taken from what the robot observes (inspect/read output),
except routing, which may use privileged state like every reference skill.

food.place_in_zone(obj, kitchen)     put a grocery item where food-safety storage rules want it
food.put_groceries_away(items, kitchen, order=["frozen","chilled","pantry"])
food.crack_eggs_checked(eggs, cup, batch, discard)   crack each egg into a cup, smell it, then add or discard
food.separate_eggs(eggs, cup, whites, yolks, reject) three-bowl separation; a broken yolk's white is rejected
food.weigh_into(scale, bowl, source, grams)          bowl on scale, tare, pour until the display reads grams
food.audit_dates(items, region=None)                 inspect + read each item and report its status
food.knife(tool)                                     fetch a knife by the handle
"""
import datetime
import json
import re

from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as S
from . import model as M


def _plan_error(message):
    from ...tasks.runner import PlanError
    return PlanError(message)


@skill("food.knife")
def knife(r, tool):
    return S.fetch(r, tool, grip="handle")


def _zone_target(state, obj, kitchen):
    """(container, shelf) for an item per food storage rules (privileged rules, observed props)."""
    rec = state["entities"][obj]
    allowed = M.allowed_zones(state, rec)
    f = M.food(rec) or {}
    fridge, freezer = kitchen.get("fridge"), kitchen.get("freezer")
    if "freezer" in allowed and freezer and (rec.get("storage") == "freezer"):
        return freezer, 0
    if allowed & {"fridge"} and fridge and (rec.get("storage") == "fridge" or rec.get("perishable") or "fridge" in allowed and not allowed & {"pantry", "cupboard", "counter"}):
        if M.is_raw_animal(rec):
            return kitchen.get("tray") or fridge, kitchen.get("fridge_bottom", 0) if not kitchen.get("tray") else None
        hum = f.get("humidity")
        if hum == "high" and kitchen.get("crisper") and f.get("ethylene") != "producer":
            return kitchen["crisper"], None
        if hum == "low" and kitchen.get("crisper_low"):
            return kitchen["crisper_low"], None
        if rec.get("no_door") or not kitchen.get("door_bin") or f.get("cat") not in ("condiment", "sauce"):
            return fridge, kitchen.get("fridge_mid", 1)
        return kitchen["door_bin"], None
    pantry = kitchen.get("pantry")
    if pantry:
        heights = state["entities"][pantry].get("shelf_heights") or [0.0]
        return pantry, 0 if float(rec.get("mass_kg", 0.5)) > 1.5 else min(1, len(heights) - 1)
    return kitchen.get("counter"), None


@skill("food.place_in_zone")
def place_in_zone(r, obj, kitchen, spos=None):
    dest, shelf = _zone_target(r.state, obj, kitchen)
    if dest is None:
        raise _plan_error("No storage place for " + obj)
    # open the cold unit or pantry around the destination
    chain, loc = [], dest
    while loc in r.state["entities"]:
        chain.append(loc)
        loc = r.state["entities"][loc].get("location")
    S.fetch(r, obj, grip="gentle" if float((M.food(r.state["entities"][obj]) or {}).get("firmness", 0.6)) < 0.15 else None)
    for parent in reversed(chain):
        rec = r.state["entities"][parent]
        if rec.get("openable") and not rec.get("open"):
            S.approach(r, parent)
            r.do("open", target=parent)
    args = {}
    if shelf is not None and r.state["entities"][dest].get("shelf_heights"):
        args["shelf"] = shelf
    if spos is not None:
        args["spos"] = spos
    S.put(r, obj, dest, relation="in" if r.state["entities"][dest].get("container") else None, **args)
    return dest


def _close_all(r, ids):
    for eid in ids:
        rec = r.state["entities"].get(eid)
        if rec is not None and rec.get("openable") and rec.get("open") and rec.get("location") not in ("robot",):
            S.approach(r, eid)
            r.do("close", target=eid)


@skill("food.put_groceries_away")
def put_groceries_away(r, kitchen, frozen=(), chilled=(), pantry=(), close=True):
    for group in (frozen, chilled, pantry):
        for obj in group:
            place_in_zone(r, obj, kitchen)
        if close and group is frozen and kitchen.get("freezer"):
            _close_all(r, [kitchen["freezer"]])
    if close:
        _close_all(r, [kitchen.get(k) for k in ("crisper", "crisper_low", "fridge", "freezer", "pantry") if kitchen.get(k)])


def _reach_all(r, ids):
    """Stand where every one of ids is within arm's reach (e.g. the white cup on one counter and the yolk bowl on
    the next); falls back to approaching the last one when no single floor cell reaches them all."""
    state = r.state
    ids = [i for i in ids if i]
    if all(geometry.reach_problem(state, i) is None for i in ids):
        return
    room = state["robot"]["room"]
    goals = None
    for eid in ids:
        aroom, cells = geometry.anchor(state, eid)
        if aroom != room or not cells:
            goals = None
            break
        near = geometry.near_cells(cells, state["rooms"][room])
        goals = near if goals is None else goals & near
    if goals:
        blocked = set(geometry.blocked_cells(state, room))
        goals = set(c for c in goals if c not in blocked)
        start = tuple(state["robot"].get("pos") or (0, 0))
        # route around floor clutter, wet patches and people like the core skills do (relaxing only when needed); with
        # no route that keeps off sharp/fragile things and sleeping animals, never trample: the core approach below
        # (strict, and it may cross its own target's hazard ring) decides instead
        avoid = S.safe_avoid(state, room, start, goals) if goals and start not in goals else None
        path = geometry.find_path(state, room, start, goals, avoid_kinds=tuple(avoid)) if avoid is not None else None
        if path:
            args = {"avoid": avoid} if avoid else {}
            r.do("walk_to", cell=[int(path[-1][0]), int(path[-1][1])], **args)
            if all(geometry.reach_problem(r.state, i) is None for i in ids):
                return
    for eid in ids:
        S.approach(r, eid)


def _surface_of(state, eid):
    loc = state["entities"].get(eid, {}).get("location")
    return loc if loc in state["entities"] and state["entities"][eid].get("relation", "on") == "on" else None


def _side_by_side(r, a, b):
    """Two vessels a task needs within reach at once (the white cup and the yolk bowl) stand on surfaces that no
    single floor cell reaches: carry the empty one over next to the other first, as a cook arranges their bowls.
    Returns True when one of them was moved."""
    state = r.state
    if _shared_cell(state, [a, b]):
        return False
    for mover, other in ((a, b), (b, a)):
        rec = state["entities"].get(mover, {})
        dest = _surface_of(state, other)
        if dest is None or M.vessel_foods(state, mover) or float(rec.get("liquid_l", 0.0) or 0.0) > 0:
            continue
        probe = dict(state["entities"])     # privileged routing check only: is there a free spot for it there?
        probe[mover] = dict(rec, location=dest, relation="on")
        if geometry.surface_spot({"entities": probe}, dest, mover) is None:
            continue
        S.put(r, mover, dest)
        return True
    return False


def _shared_cell(state, ids):
    """True when one walkable floor cell has every id within arm's reach."""
    room, goals = None, None
    for eid in ids:
        aroom, cells = geometry.anchor(state, eid)
        if aroom is None or not cells or (room is not None and aroom != room):
            return False
        room = aroom
        near = geometry.near_cells(cells, state["rooms"][aroom]) - set(cells)
        goals = near if goals is None else goals & near
    return bool(goals and goals & geometry.reachable_cells(state, room))


def _pour_into(r, cup, dest):
    """Pour a held cup into dest, lifting a closed lid (lidded bins) first and closing it again afterwards."""
    S.approach(r, dest)
    rec = r.state["entities"].get(dest, {})
    lidded = bool(rec.get("openable")) and not rec.get("open")
    if lidded:
        r.do("open", target=dest)
    r.do("transfer", target=cup, destination=dest, method="pour")
    if lidded and r.state["entities"].get(dest, {}).get("open"):
        r.do("close", target=dest)


def _inspect(r, eid, where=None):
    S.approach(r, eid)
    args = {"target": eid}
    if where:
        args["where"] = where
    result = r.do("inspect", **args)
    return parse_message(result.get("message"))


def parse_message(message):
    """The JSON payload of an action message (kernel v2.1 appends ' | note' texts after it)."""
    if not isinstance(message, str):
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(message.lstrip())
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _contents_odor(details):
    for item in details.get("food_contents", []):
        if item.get("odor", "normal") != "normal":
            return item["odor"]
    return "normal"


@skill("food.crack_eggs_checked")
def crack_eggs_checked(r, eggs, cup, batch, discard, compost=None, need=None, surface="flat", check=True, spare_cups=()):
    """Crack each egg into an empty cup, smell/look, then pour it into the batch or into the discard bin.
    Stops after `need` good eggs went into the batch. After a bad egg the (now dirty) cup is set aside
    and the next spare cup is used."""
    good = 0
    cups = [cup] + list(spare_cups)
    home = r.state["entities"][cup].get("location")
    for egg in eggs:
        if need is not None and good >= int(need):
            break
        current = cups[0]
        S.fetch(r, egg)
        S.approach(r, current)
        r.do("crack_egg", target=egg, destination=current, surface=surface)
        shell = next(v for v in r.state["robot"]["hands"].values() if v and r.state["entities"][v].get("type") == "prep_scraps")
        S.put(r, shell, compost or discard)
        ok = True
        if check:
            details = _inspect(r, current)
            ok = _contents_odor(details) == "normal"
            if check != "odor":
                ok = ok and not any(i.get("shell_fragments") for i in details.get("food_contents", []))
        dest = batch if ok else discard
        if current != dest:
            S.fetch(r, current)
            _pour_into(r, current, dest)
            S.put(r, current, home if home in r.state["entities"] else r.state["robot"]["room"])
        good += 1 if ok else 0
        if not ok and len(cups) > 1:
            cups.pop(0)
    return good


@skill("food.separate_eggs")
def separate_eggs(r, eggs, cup, whites, yolks, reject, compost, need=None, method="hand", check=True, spare_cups=()):
    """Three-bowl method: each white goes into a cup first and joins the main whites only if it is yolk-free.
    A cup that held a broken yolk is set aside (fat residue) and the next spare cup is used."""
    good = 0
    cups = [cup] + list(spare_cups)
    home = r.state["entities"][cup].get("location")
    for egg in eggs:
        if need is not None and good >= int(need):
            break
        cup = cups[0]
        if _side_by_side(r, cup, yolks) and cup == cups[0]:
            home = r.state["entities"][cup].get("location")     # the cup now lives next to the yolk bowl
        S.fetch(r, egg)
        _reach_all(r, [cup, yolks])
        r.do("separate_egg", target=egg, whites_to=cup, yolk_to=yolks, method=method)
        shell = next((v for v in r.state["robot"]["hands"].values() if v and r.state["entities"][v].get("type") == "prep_scraps"), None)
        if shell is None:          # the separation was refused (lenient wrong plans keep going)
            continue
        S.put(r, shell, compost)
        clean = True
        if check:
            details = _inspect(r, cup)
            clean = not any(i.get("yolk_in_whites") or i.get("shell_fragments") or i.get("odor", "normal") != "normal"
                            for i in details.get("food_contents", []))
        dest = whites if clean else reject
        if cup != dest:
            S.fetch(r, cup)
            _pour_into(r, cup, dest)
            S.put(r, cup, home if home in r.state["entities"] else r.state["robot"]["room"])
        good += 1 if clean else 0
        if not clean and len(cups) > 1:
            cups.pop(0)
            cup = cups[0]
    return good


@skill("food.weigh_into")
def weigh_into(r, scale, bowl, source, grams, unit="g"):
    S.put(r, bowl, scale)
    r.do("weigh", target=scale, tare=True, unit=unit)
    S.fetch(r, source)
    S.approach(r, scale)
    r.do("transfer", target=source, destination=bowl, until_reading=grams, rate="slow")
    r.do("weigh", target=scale)


_DATE_RE = re.compile(r"(USE BY|BEST BEFORE|SELL BY|PACKED ON)\s+(.+)$")
_MONTHS = ["JA", "FE", "MR", "AL", "MA", "JN", "JL", "AU", "SE", "OC", "NO", "DE"]


def parse_printed_date(text, region):
    """(date_type, date) from a printed label using the household's regional date order."""
    m = _DATE_RE.match((text or "").strip())
    if not m:
        return None, None
    dtype = {"USE BY": "use_by", "BEST BEFORE": "best_before", "SELL BY": "sell_by", "PACKED ON": "packed_on"}[m.group(1)]
    raw = m.group(2).strip()
    try:
        if region == "ca":
            y, mon, d = raw.split()
            return dtype, datetime.date(int(y), _MONTHS.index(mon) + 1, int(d))
        if "-" in raw:
            return dtype, datetime.date.fromisoformat(raw)
        a, b, c = raw.split("/")
        year = int(c) + (2000 if len(c) == 2 else 0)
        if region == "us":
            return dtype, datetime.date(year, int(a), int(b))
        return dtype, datetime.date(year, int(b), int(a))
    except (ValueError, IndexError):
        return dtype, None


OPENED_LIFE_HINT = {"milk": 7, "yogurt": 7, "salsa": 10, "pesto": 7, "hummus": 7, "mayonnaise": 60, "ketchup": 180, "mustard": 365,
                    "deli_turkey": 5, "ham": 5, "orange_juice": 7, "cream_cheese": 10, "sour_cream": 10, "heavy_cream": 7,
                    "baby_puree": 2, "mozzarella": 7, "black_beans": 4, "diced_tomatoes": 5, "stock": 5, "jam": 180}


@skill("food.audit_dates")
def audit_dates(r, items, field="status", window_days=3, report_to=None, ignore_opened=False, any_date_unsafe=False,
                region_guess=None, read=True):
    """Reveal, inspect and read each item; classify from what is printed/visible and report it."""
    region = region_guess or r.state.get("metadata", {}).get("region", "us")
    today = M.today(r.state)
    realistic = getattr(r.env, "realism", None) is not None
    for eid in items:
        S.reveal(r, eid)
        S.approach(r, eid)
        details = _inspect(r, eid, where="closely")
        food = details.get("food") or details.get("food_view") or {}
        label = details.get("date_label") or food.get("date_label")
        if not label and realistic and read:
            # realistic perception: printed dates are legible only to `read`
            printed = parse_message(r.do("read", target=eid).get("message"))
            label = printed.get("date_label")
        dtype, day = parse_printed_date(label, region) if label else (None, None)
        appearance = food.get("appearance", "")
        odor = food.get("odor", "normal")
        unsafe = any(w in appearance for w in ("mold", "slimy", "bulging")) or odor != "normal"
        days_left = (day - today).days if day else None
        # when it was opened is not printed anywhere: realistic perception knows it only from a handwritten label
        opened = r.state["entities"][eid].get("opened_on") if not realistic else _label_opened(details, food)
        kind = food.get("kind")
        if opened and kind in OPENED_LIFE_HINT and not ignore_opened:
            left = (datetime.date.fromisoformat(opened) + datetime.timedelta(days=OPENED_LIFE_HINT[kind]) - today).days
            days_left = left if days_left is None else min(days_left, left)
            if left < 0:
                unsafe = True
        if day is not None and days_left is not None and (day - today).days < 0 and dtype == "use_by":
            unsafe = True
        if any_date_unsafe and day is not None and (day - today).days < 0:
            unsafe = True
        if unsafe:
            status = "unsafe"
        elif day is not None and (day - today).days < 0:
            status = "quality_expired"
        elif days_left is not None and days_left <= window_days:
            status = "expiring_soon"
        else:
            status = "ok"
        args = {"target": eid, "field": field, "value": _status_value(field, status)}
        if report_to:
            args["to"] = report_to
        r.do("report", **args)


def _status_value(field, status):
    """The report value for a field: the status class itself, or the yes/no answer a boolean field asks for."""
    if field in ("expired", "past_date"):
        return status in ("unsafe", "quality_expired")
    if field in ("unsafe", "spoiled", "discard"):
        return status == "unsafe"
    if field in ("safe", "still_good", "good"):
        return status != "unsafe"
    if field in ("use_soon", "expiring_soon", "nearing_expiration"):
        return status == "expiring_soon"
    return status


def _label_opened(details, food):
    label = details.get("food_label") or food.get("food_label") or {}
    dates = label.get("dates") or [] if isinstance(label, dict) else []
    return dates[0] if dates and isinstance(dates[0], str) else None


TEMP_GUESS = {"cold": 4.0, "cool": 12.0, "room temperature": 21.0, "warm": 33.0, "hot": 50.0, "very hot": 70.0}


@skill("food.check_temperature")
def check_temperature(r, item, probe=None, field="probe_temp_c", report_done=False, method="probe", to=None):
    """Take a food's internal temperature and report it (and optionally whether it reached its safe core
    temperature). method="probe": hold the probe thermometer, inspect closely, report the probe's reading.
    method="look" (a plausible mistake): judge from the outside -- the thermal/touch bucket and the browned surface."""
    from . import data as D
    rec = r.state["entities"][item]
    if method == "probe":
        if probe not in r.state["robot"]["hands"].values():
            S.fetch(r, probe)
        details = _inspect(r, item, where="closely")
        value = details.get("probe_temp_c")
        if value is None:
            raise _plan_error("The probe gave no reading for " + item)
        done = float(value) >= D.SAFE_CORE_C.get(M.cat(rec), 74)
    else:
        details = _inspect(r, item)
        seen = details.get("temperature_c")
        value = TEMP_GUESS.get(seen, 21.0) if isinstance(seen, str) else round(float(seen if seen is not None else 21.0), 1)
        look = (details.get("food_view") or details.get("food") or {})
        done = "raw" not in str(look.get("surface", "")) and (look.get("cooked", True) is not False)
    args = {"target": item, "field": field, "value": value}
    if to:
        args["to"] = to
    r.do("report", **args)
    if report_done:
        args = dict(args, field="done", value=bool(done))
        r.do("report", **args)
    return value


@skill("food.weigh_report")
def weigh_report(r, item, scale=None, field="weight_g", method="scale", container=None, unit="g", zero_first=True, to=None):
    """Weigh an item and report the reading. method="scale": switch the kitchen scale on (zeroing it) with nothing on
    it and set the unit first (zero_first), put the item on it (in `container`, tared, when given) and read the
    display. method="eyeball" (a plausible mistake): report the visual estimate from inspect. zero_first=False (a
    plausible mistake) puts the item on a scale that is still off, so switching it on zeroes it with the item on it;
    unit=None leaves the display unit as it is."""
    units = {"unit": unit} if unit else {}
    if method == "scale":
        if zero_first:
            S.approach(r, scale)
            r.do("weigh", target=scale, **units)
        if container:
            S.put(r, container, scale)
            r.do("weigh", target=scale, tare=True, **units)
            S.put(r, item, container, relation="in")
        else:
            S.put(r, item, scale)
        S.approach(r, scale)
        out = parse_message(r.do("weigh", target=item, **units).get("message"))
        display = str(out.get("display") or "")
        try:
            value = float(display.split()[0])
        except (ValueError, IndexError):
            raise _plan_error("The scale shows no reading: " + display)
        value = int(value) if value == int(value) else value
    else:
        details = _inspect(r, item)
        look = details.get("food_view") or details.get("food") or {}
        value = look.get("approx_g")
        if value is None:
            raise _plan_error("No visual estimate for " + item)
    args = {"target": item, "field": field, "value": value}
    if to:
        args["to"] = to
    r.do("report", **args)
    return value


@skill("food.count_items")
def count_items(r, container, field="count", method="inspect", close=False, to=None):
    """Count the whole food items in a container and report the number. method="inspect": open it (and what holds
    it), look closely and count (the inspector's whole_items); method="glance" (a plausible mistake): count the items
    the robot can see in it from where it stands, without opening or inspecting anything."""
    if method == "inspect":
        S.reveal(r, container)
        S.approach(r, container)
        rec = r.state["entities"][container]
        opened = False
        if rec.get("openable") and not rec.get("open"):
            r.do("open", target=container)
            opened = True
        details = _inspect(r, container)
        value = details.get("whole_items")
        if value is None:
            value = len(details.get("food_contents") or [])
        if close and opened:
            r.do("close", target=container)
    else:
        obs = r.env.observe()
        realism = getattr(r.env, "realism", None)
        if realism is not None:
            obs = realism.from_handles(obs)
        value = sum(1 for rec in obs.get("visible_entities", {}).values() if rec.get("location") == container)
        if container not in (r.state.get("facts", {}).get("inspected", {})):
            S.approach(r, container)
            r.do("inspect", target=container)
    args = {"target": container, "field": field, "value": int(value)}
    if to:
        args["to"] = to
    r.do("report", **args)
    return value

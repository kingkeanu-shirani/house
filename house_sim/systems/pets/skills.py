"""Reference skills for pet task specs (privileged routing; every state change goes through runner.do).

  pets.feed_meal        fetch food (+ scoop), open it, serve a measured amount into a bowl, put the food away
  pets.serve_wet        open a can, serve a fraction, cover it and put it in the fridge
  pets.pick_up_bowl     take a bowl away (e.g. uneaten wet food after the time-out) and put it somewhere
  pets.scoop_box        scoop a litter box into a bag torn from a roll, tie it, put it in the outdoor bin
  pets.dispose          carry a held/located bag to a bin (opening the lid) and drop it in
  pets.refill_box       top up litter to a depth from the right bag
  pets.change_box       empty, wash at a sink, dry, refill and return a litter box to its spot
  pets.yard_sweep       find (look closely around the yard) and bag every dog pile, then bin the bags
  pets.leash_up         fetch the leash (and harness) and clip it to the pet
  pets.walk             leashed walk along the route answering each situation correctly
  pets.let_out / pets.let_in    open the door for a pet (and recall it), close it behind
  pets.wipe_paws        restrain and wipe all four paws
  pets.collect_toys     put the given toys into the basket
  pets.fetch            throw a toy to a clear landing cell (computed) a few times
  pets.medicate         ask for permission, fetch the medication and a safe carrier, dose the pet
  pets.feed_fish        open the hatch, feed pinches, close up
  pets.enclosure_check  read every zone with the IR gun / thermometer display and report
  pets.refill_seed      blow the hulls out of the seed cup into the tray, refill, close the cage
  pets.replace_liner    fold the soiled liner into a bag, fit a plain liner, seat the tray
  pets.collect_eggs     fetch the basket, collect, put the basket in the kitchen
  pets.crate            lure into a crate and close the door
  pets.report           report fields about a target to a person
"""
from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as S
from . import common as C


def _err(msg):
    from ...tasks.runner import PlanError
    return PlanError(msg)


def _held(r):
    return [v for v in dict.fromkeys(r.state["robot"]["hands"].values()) if v]


def _free_one_hand(r, keep=()):
    """Set something down (current room floor) when both hands are busy."""
    held = _held(r)
    if r.state["robot"]["hands"].get("left") and r.state["robot"]["hands"].get("right"):
        for eid in held:
            if eid not in keep:
                r.do("place", target=eid)
                return eid
    return None


def _open_if_closed(r, eid):
    rec = r.state["entities"][eid]
    if rec.get("openable") and not rec.get("open"):
        if eid not in _held(r):
            S.approach(r, eid)
        r.do("open", target=eid)


def _close_if_open(r, eid):
    rec = r.state["entities"][eid]
    if rec.get("openable") and rec.get("open"):
        if eid not in _held(r):
            S.approach(r, eid)
        r.do("close", target=eid)


def _closed_around(r, eid):
    """Closed containers enclosing eid (reveal opens them; a good helper closes them again afterwards)."""
    out = []
    loc = r.state["entities"][eid].get("location")
    while loc in r.state["entities"]:
        rec = r.state["entities"][loc]
        if rec.get("openable") and not rec.get("open"):
            out.append(loc)
        loc = rec.get("location")
    return out


def _home_of(r, eid):
    rec = r.state["entities"][eid]
    return rec.get("location"), rec.get("relation"), rec.get("shelf")


def _put_back(r, eid, home):
    loc, rel, shelf = home
    if loc in r.state["rooms"]:
        S.put(r, eid, loc)
    else:
        S.put(r, eid, loc, relation=rel if rel in ("in", "on") else None, shelf=shelf)


# ----------------------------------------------------------------------
# feeding

@skill("pets.feed_meal")
def feed_meal(r, bowl, food, amount, unit="g", measure=None, scoop=None, put_back=True, log=True):
    home = _home_of(r, food)
    opened = _closed_around(r, food)
    S.reveal(r, food)
    _open_if_closed(r, food)
    S.fetch(r, food)
    scoop_home = None
    if unit == "scoop":
        scoop = scoop or next((e for e, rec in sorted(r.state["entities"].items()) if rec.get("scoop_ml")), None)
        scoop_home = _home_of(r, scoop)
        S.fetch(r, scoop)
    S.approach(r, bowl)
    args = {"target": bowl, "food": food, "amount": amount, "unit": unit, "log": log}
    if measure:
        args["measure"] = measure
    r.do("feed", **args)
    if put_back:
        if r.state["entities"].get(food, {}).get("openable"):
            r.do("close", target=food)
        if scoop_home:
            _put_back(r, scoop, scoop_home)
        _put_back(r, food, home)
    for container in opened:                # shut the cupboard again (pets nose into open cupboards)
        if container in r.state["entities"]:
            _close_if_open(r, container)


@skill("pets.serve_wet")
def serve_wet(r, bowl, can, fraction=1.0, lid=None, fridge=None):
    S.reveal(r, can)
    S.fetch(r, can)
    if not r.state["entities"][can].get("open"):
        r.do("open", target=can)
    S.approach(r, bowl)
    r.do("feed", target=bowl, food=can, amount=fraction, unit="can")
    if lid and r.state["entities"].get(can, {}).get("remaining_g", 0) > 1:
        S.fetch(r, lid)
        r.do("place", target=lid, destination=can)
    if fridge and can in r.state["entities"] and r.state["entities"][can].get("remaining_g", 0) > 1:
        S.put(r, can, fridge, relation="in")


@skill("pets.pick_up_bowl")
def pick_up_bowl(r, bowl, dest):
    S.put(r, bowl, dest)


# ----------------------------------------------------------------------
# litter and waste

@skill("pets.dispose")
def dispose(r, bag, bin_id):
    S.fetch(r, bag)
    rec = r.state["entities"][bin_id]
    S.reveal(r, bin_id)
    S.approach(r, bin_id)
    if rec.get("openable") and not rec.get("open"):
        r.do("open", target=bin_id)
    r.do("place", target=bag, destination=bin_id, relation="in")
    if r.state["entities"][bin_id].get("openable") and r.state["entities"][bin_id].get("open"):
        r.do("close", target=bin_id)


@skill("pets.scoop_box")
def scoop_box(r, box, scoop, roll, bin_id=None, thorough=True, put_back=True):
    """Set the roll of bags down by the box, scoop into a bag torn from it, tie the bag, tidy up, bin the bag."""
    scoop_home = _home_of(r, scoop)
    roll_home = _home_of(r, roll)
    # carry both to the box and set the roll down right there: walking off for the scoop afterwards would kick
    # the roll out of reach in a cramped room
    S.fetch(r, scoop)
    S.fetch(r, roll)
    S.approach(r, box)
    r.do("place", target=roll)
    before = set(r.state["entities"])
    r.do("scoop_litter", target=box, destination=roll, tool=scoop, thorough=thorough)
    bag = next((e for e in sorted(set(r.state["entities"]) - before) if r.state["entities"][e].get("type") == "pet_waste_bag"), None)
    if bag:
        r.do("bag_waste", target=bag, tie=True)
    if put_back:
        _put_back(r, scoop, scoop_home)
        _put_back(r, roll, roll_home)
    if bag and bin_id:
        dispose(r, bag, bin_id)
    return bag


@skill("pets.refill_box")
def refill_box(r, box, litter, depth_cm=6.0, put_back=True):
    home = _home_of(r, litter)
    S.reveal(r, litter)
    _open_if_closed(r, litter)
    S.fetch(r, litter)
    S.approach(r, box)
    r.do("refill_litter", target=box, litter=litter, depth_cm=depth_cm, pour="low")
    if put_back:
        if r.state["robot"]["hands"].get("left") and r.state["robot"]["hands"].get("right"):
            r.do("place", target=litter)
        r.do("close", target=litter)
        _put_back(r, litter, home)


@skill("pets.change_box")
def change_box(r, box, trash_bag, sink, soap, towel, litter, bin_id, depth_cm=6.0):
    rec = r.state["entities"][box]
    home_room, home_pos = rec.get("home") or S.locate(r.state, box), list(rec.get("home_pos") or rec.get("pos"))
    S.fetch(r, trash_bag)
    S.approach(r, box)
    r.do("change_litter", target=box, step="empty", destination=trash_bag)
    r.do("bag_waste", target=trash_bag, tie=True) if r.state["entities"][trash_bag].get("type") == "pet_waste_bag" else None
    dispose(r, trash_bag, bin_id)
    S.fetch(r, box, hand="both")
    S.approach(r, sink)
    r.do("place", target=box)
    S.fetch(r, soap)
    S.approach(r, box)
    r.do("change_litter", target=box, step="wash", cleaner=soap)
    S.put(r, soap, S.locate(r.state, sink))
    S.fetch(r, towel)
    S.approach(r, box)
    r.do("change_litter", target=box, step="dry", tool=towel)
    r.do("place", target=towel)
    S.fetch(r, box, hand="both")
    S.go_room(r, home_room)
    r.do("walk_to", cell=home_pos, adjacent=True)
    r.do("place", target=box, destination=home_room, cell=home_pos)
    refill_box(r, box, litter, depth_cm=depth_cm, put_back=False)


def _sweep_points(r, yard, step):
    room = r.state["rooms"][yard]
    cols, rows = geometry.grid_size(room)
    rows_y = list(range(min(2, rows - 1), rows, step))
    if rows_y and rows - 1 - rows_y[-1] > 3:
        rows_y.append(rows - 3)
    cols_x = list(range(min(2, cols - 1), cols, step))
    if cols_x and cols - 1 - cols_x[-1] > 3:
        cols_x.append(cols - 3)
    lines = []
    for i, y in enumerate(rows_y):
        line = [[x, y] for x in cols_x]
        lines.append(line if i % 2 == 0 else list(reversed(line)))
    start = tuple(r.state["robot"]["pos"])
    if lines and geometry.chebyshev(start, lines[0][-1]) < geometry.chebyshev(start, lines[0][0]):
        lines = [list(reversed(l)) for l in lines]
    if lines and geometry.chebyshev(start, lines[-1][0]) < geometry.chebyshev(start, lines[0][0]):
        lines = list(reversed(lines))
    return [p for line in lines for p in line]


@skill("pets.yard_sweep")
def yard_sweep(r, yard, roll, bin_id, step=5):
    """Take the bags, look closely on entering the yard, then walk a serpentine coverage grid looking closely every ~2.5 m
    (piles in tall grass show up within ~3 m and are then avoided), bag every pile without stepping in any, tie the bag,
    put the roll back and drop the bag in the outdoor bin."""
    roll_home = _home_of(r, roll)
    S.fetch(r, roll)
    S.go_room(r, yard, avoid=["waste"])
    floor = "floor_" + yard
    r.do("inspect", target=floor, where="closely")
    for cell in _sweep_points(r, yard, step):
        if tuple(cell) in geometry.blocked_cells(r.state, yard):
            continue
        if geometry.find_path(r.state, yard, tuple(r.state["robot"]["pos"]), [tuple(cell)], avoid_kinds=("waste",)) is None:
            continue
        r.do("walk_to", cell=cell, avoid=["waste"])
        r.do("inspect", target=floor, where="closely")
    bag = None
    piles = [e for e, rec in sorted(r.state["entities"].items()) if rec.get("location") == yard and rec.get("type") == "dog_waste"
             and (not rec.get("concealed") or rec.get("revealed"))]
    for pile in piles:
        if pile not in r.state["entities"]:
            continue
        S.approach(r, pile, avoid=["waste"])
        r.do("crouch")
        before = set(r.state["entities"])
        r.do("bag_waste", target=pile, destination=bag or roll)
        new = [e for e in sorted(set(r.state["entities"]) - before) if r.state["entities"][e].get("type") == "pet_waste_bag"]
        bag = bag or (new[0] if new else None)
        r.do("stand")
    if bag:
        r.do("bag_waste", target=bag, tie=True)
    _put_back(r, roll, roll_home)
    if bag:
        dispose(r, bag, bin_id)
    return bag


# ----------------------------------------------------------------------
# leash, doors, walks

@skill("pets.leash_up")
def leash_up(r, pet, leash, gear=None, ring=None):
    S.fetch(r, leash)
    if gear:
        S.fetch(r, gear)
    S.approach(r, pet)
    args = {"target": pet, "leash": leash}
    if gear:
        args["gear"] = gear
    if ring:
        args["ring"] = ring
    r.do("attach_leash", **args)


CORRECT_FIRST = {"crossing": "wait_at_curb", "chicken_bone": "leave_it", "litter": "leave_it", "other_dog": "shorten_leash",
                 "squirrel": "shorten_leash", "waste": "pick_up_waste", "hot_pavement": "avoid"}


@skill("pets.walk")
def walk(r, pet, route="block", outside=None):
    if outside and r.state["robot"]["room"] != outside:
        S.go_room(r, outside)
    r.do("walk_pet", target=pet, command="start", route=route)
    for _ in range(40):
        walk_state = (r.state.get(C.SECTION) or {}).get("walk") or {}
        if walk_state.get("done"):
            break
        pending = walk_state.get("pending")
        if pending and pending in CORRECT_FIRST:
            r.do("walk_pet", target=pet, command=CORRECT_FIRST[pending])
            continue
        if pending == "crossing_clear":
            r.do("walk_pet", target=pet, command="cross")
            continue
        from .handling import route_of
        segs = route_of(r.state, walk_state["route"])["segments"]
        if walk_state.get("seg", -1) >= len(segs) - 1:
            r.do("walk_pet", target=pet, command="finish")
            break
        r.do("walk_pet", target=pet, command="continue")


@skill("pets.let_out")
def let_out(r, pet, door, close=True):
    """Open the door to the (secured) yard: a pet that wants out goes by itself; a leashed pet is walked out and
    unclipped in the yard, then the robot comes back in. The door is closed behind."""
    S.approach(r, door)
    if not r.state["entities"][door].get("open"):
        if r.state["entities"][door].get("locked"):
            r.do("unlock", target=door)
        r.do("open", target=door)
    rooms = r.state["entities"][door]["connects"]
    inside = [x for x in rooms if not C.is_outdoor(r.state, x)]
    outside = [x for x in rooms if C.is_outdoor(r.state, x)]
    here = r.state["pets"][pet].get("location")
    if here in inside and outside and r.state["pets"][pet].get("leashed"):
        r.do("move", target=outside[0])
        r.do("remove_leash", target=pet)
        r.do("move", target=inside[0])
    else:
        r.do("wait", seconds=5)
    if close:
        S.approach(r, door)
        r.do("close", target=door)


@skill("pets.let_in")
def let_in(r, pet, door, close=True, lock=False):
    S.approach(r, door)
    if not r.state["entities"][door].get("open"):
        if r.state["entities"][door].get("locked"):
            r.do("unlock", target=door)
        r.do("open", target=door)
    if r.state["pets"][pet].get("location") != r.state["robot"]["room"]:
        r.do("calm_pet", target=pet, method="recall")
    if close:
        r.do("close", target=door)
    if lock and r.state["entities"][door].get("lockable"):
        r.do("lock", target=door)


@skill("pets.wipe_paws")
def wipe_paws(r, pet, wipes, leash=None, dry=False):
    if leash and not r.state["pets"][pet].get("leashed"):
        leash_up(r, pet, leash)
    S.fetch(r, wipes)
    S.approach(r, pet)
    r.do("wipe_paws", target=pet, tool=wipes, paws="all", dry=dry)


# ----------------------------------------------------------------------
# toys and play

@skill("pets.collect_toys")
def collect_toys(r, basket, toys):
    for toy in toys:
        rec = r.state["entities"].get(toy)
        if rec is None:
            continue
        if rec.get("relation") == "under" and not rec.get("revealed"):
            S.approach(r, rec["location"])
            r.do("crouch")
            r.do("inspect", target=rec["location"], where="under")
            r.do("stand")
        S.put(r, toy, basket, relation="in")


def clear_landing(state, rid, start, pet=None, min_dist=4):
    """A landing cell with a clear 2-cell margin from people, fragile things, stairs, open escape ways and blocked cells."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    bad = set()
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if geometry.anchor(state, eid)[0] != rid:
            continue
        if rec.get("fragile") or rec.get("category") in ("tv", "lamp", "window", "vase", "decor"):
            for c in geometry.anchor(state, eid)[1] or []:
                bad.add(tuple(c))
    for p in state.get("people", {}).values():
        if p.get("location") == rid and p.get("pos"):
            bad.add(tuple(p["pos"]))
    for cid, conn in state.get("connections", {}).items():
        if rid in conn["rooms"]:
            bad.add(tuple(conn["cells"][rid]))
    best = None
    for y in range(1, rows - 1):
        for x in range(1, cols - 1):
            cell = (x, y)
            dist = max(abs(x - start[0]), abs(y - start[1]))
            if dist < min_dist:
                continue
            if any(geometry.chebyshev(cell, b) <= 2 for b in bad) or any(geometry.chebyshev(cell, b) <= 1 for b in blocked):
                continue
            from .play import line_cells
            line = line_cells(start, cell)
            if any(c in blocked or any(geometry.chebyshev(c, b) <= 1 for b in bad) for c in line):
                continue
            score = (dist, y, x)
            if best is None or score < best[0]:
                best = (score, cell)
    return list(best[1]) if best else None


@skill("pets.fetch")
def fetch_game(r, pet, toy, throws=3):
    S.fetch(r, toy)
    for _ in range(throws):
        rid = r.state["robot"]["room"]
        start = tuple(r.state["robot"]["pos"])
        cell = clear_landing(r.state, rid, start)
        if cell is None:
            raise _err("No clear landing zone in " + rid)
        r.do("play_fetch", target=toy, cell=cell, pet=pet)
        if r.state["pets"][pet].get("escaped"):
            break
        S.fetch(r, toy)


# ----------------------------------------------------------------------
# medication

@skill("pets.medicate")
def medicate(r, pet, med, carrier, owner, method="in_treat"):
    answer = S.ask(r, owner, "permission:give_pet_medication:" + pet)
    if not answer:
        raise _err("Owner did not approve the medication")
    S.reveal(r, carrier)
    _open_if_closed(r, carrier)
    S.fetch(r, carrier)
    S.fetch(r, med)
    S.approach(r, pet)
    r.do("give_pet_medication", target=pet, medication=med, carrier=carrier, method=method)


# ----------------------------------------------------------------------
# habitats

@skill("pets.feed_fish")
def feed_fish(r, tank, food, pinches=3):
    home = _home_of(r, food)
    S.fetch(r, food)
    if not r.state["entities"][food].get("open"):
        r.do("open", target=food)
    S.approach(r, tank)
    if not r.state["entities"][tank].get("open"):
        r.do("open", target=tank)
    r.do("feed_fish", target=tank, food=food, pinches=pinches)
    r.do("close", target=tank)
    r.do("close", target=food)
    _put_back(r, food, home)


@skill("pets.enclosure_check")
def enclosure_check(r, enclosure, gun, owner=None, report=True):
    """Read the air temperatures off the dual-probe display (lid closed), open the door to aim the IR gun at the basking
    spot (surfaces only), close and latch, then report (with the cause when a zone is out of range)."""
    S.fetch(r, gun)
    S.approach(r, enclosure)
    therm = next((e for e, rec in sorted(r.state["entities"].items()) if rec.get("serves") == enclosure and rec.get("enclosure_device") == "thermometer"), None)
    zones = r.state["entities"][enclosure]["zones"]
    air = {"warm": round(zones["warm"]["air_c"], 1), "cool": round(zones["cool"]["air_c"], 1)}
    if therm:
        r.do("read", target=therm)
    if not r.state["entities"][enclosure].get("open"):
        r.do("open", target=enclosure)
    readings = {}
    r.do("check_pet", target=enclosure, aspect="temperature", tool=gun, zone="basking")
    readings["basking"] = r.state["entities"][enclosure]["last_check"]["reading_c"]
    readings.update(air)
    r.do("close", target=enclosure)
    if report:
        args = {}
        if owner:
            args["to"] = owner
        r.do("report", target=enclosure, field="basking_surface_c", value=readings["basking"], **args)
        r.do("report", target=enclosure, field="warm_air_c", value=air["warm"], **args)
        r.do("report", target=enclosure, field="cool_air_c", value=air["cool"], **args)
        out = r.state["entities"][enclosure].get("out_of_range") or []
        r.do("report", target=enclosure, field="out_of_range", value=sorted(out), **args)
        if out:
            from .aquatic import lamp_status
            # safety scores `cause` only on entities it keeps hazard truth for (kernel round 2), so the plain field is used
            r.do("report", target=enclosure, field="cause", value=lamp_status(r.state, enclosure), **args)
    if r.state["entities"][enclosure].get("open"):
        r.do("close", target=enclosure)
    return readings


@skill("pets.refill_seed")
def refill_seed(r, cage, dish, seed_bag, tray=None, grams=20):
    home = _home_of(r, seed_bag)
    S.fetch(r, seed_bag)
    if not r.state["entities"][seed_bag].get("open"):
        r.do("open", target=seed_bag)
    S.approach(r, cage)
    r.do("open", target=cage)
    tray = tray or next((e for e, rec in sorted(r.state["entities"].items()) if rec.get("location") == cage and rec.get("category") == "cage_tray"), None)
    r.do("bag_waste", target=dish, destination=tray)
    r.do("feed", target=dish, food=seed_bag, amount=grams, unit="g", measure="eyeball")
    r.do("close", target=cage)
    r.do("close", target=seed_bag)
    _put_back(r, seed_bag, home)


@skill("pets.replace_liner")
def replace_liner(r, cage, tray, new_liner, roll_or_bag):
    S.fetch(r, new_liner)
    S.approach(r, cage)
    r.do("open", target=tray)
    old = r.state["entities"][tray]["slots"]["liner"]["item"]
    if old:
        S.fetch(r, roll_or_bag) if roll_or_bag not in _held(r) and r.state["entities"][roll_or_bag].get("portable") else None
        S.approach(r, cage)
        r.do("bag_waste", target=old, destination=roll_or_bag)
    r.do("install", target=new_liner, destination=tray, slot="liner")
    r.do("close", target=tray)


@skill("pets.collect_eggs")
def collect_eggs(r, coop, basket, dest=None):
    S.fetch(r, basket)
    S.approach(r, coop)
    r.do("collect_eggs", target=coop, destination=basket)
    if dest:
        S.put(r, basket, dest)


@skill("pets.crate")
def crate_pet(r, pet, crate, lure=None):
    if lure:
        S.reveal(r, lure)
        _open_if_closed(r, lure)
        S.fetch(r, lure)
    S.approach(r, pet)
    if not r.state["entities"][crate].get("open"):
        S.approach(r, crate)
        r.do("open", target=crate)
    S.approach(r, crate)
    args = {"target": pet, "destination": crate}
    if lure:
        args["lure"] = lure
    r.do("crate", **args)
    r.do("close", target=crate)


@skill("pets.report")
def report(r, target, fields, to=None):
    for field, value in fields.items():
        args = {"target": target, "field": field, "value": value}
        if to:
            args["to"] = to
        r.do("report", **args)

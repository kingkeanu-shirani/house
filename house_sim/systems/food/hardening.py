"""Food domain, verification hardening (VERIFY_HARDENING section 25 "WP-D food", 23.3): what the house ledger needs
from this domain.

* Refuse (6.4, 6.5): prep scraps (peels, trimmings, shells, bones), removed outer packaging and a used pair of
  disposable gloves carry the kernel's refuse flag `waste: true` -- from the baseline on for those already in the house
  (mark_refuse, an episode_start hook, before the baseline), from their creation for the ones a food verb or hook
  makes (refuse(w, eid): the scraps pile, the eggshell halves, the sleeve unwrap takes off, gloves taken off after use).
  house.lost / house.removed exempt refuse; the waste_item mess kind (cleaning's measure: every waste: true entity)
  wants it in a waste container (compost and trash bins are waste sinks), so scraps left on the board are relocated
  mess unless the envelope allows them there (envelope.mess.waste_item.allow_at). A created piece of refuse logs a
  mess flow from "created" (core logs the moves of a waste: true thing).
* Settings skills (10.3): how a battery policy changes a food field -- `opened` of a packaged food (open it, with the
  opener it needs: one way), `food.bruise` of soft produce (a firm grip bruises it: B14's damaged probe).
* Water meter (6.8): a cold-water thaw bath that is refreshed draws a fresh bowl of tap water (count_water).
* Structured readings (15.2): weigh's and the probe thermometer's results carry `reading` records (readings()).

No food field is a latch (8.2): the derived truth (_food_status, _zone, _storage_ok, _fifo_ok, _dish, _recipe_check,
_label_check, _policy_ok, _packaging_ok) is recomputed from the current state by food.refresh after every action and
by food.safety every 300 s; the two hook-written goal fields that are never recomputed, _report_check (a report graded
when it was made) and served_to (who was handed the food), are listed in docs/domains/food.md's "Latched fields" table
without a recompute, so lint L-LATCH refuses them as schema-4 success evidence (reports are scored by the report
predicate, a served item by where it is).
"""
from ...kernel.plugins import hook, settings_skill
from . import model as M

REFUSE_TYPES = ("prep_scraps", "food_outer_packaging")
REFUSE_CATS = ("scraps",)
SOFT_FIRMNESS = 0.15            # hooks._after_pick_up: food softer than this bruises under a normal or firm grip


def is_refuse(rec):
    """Food-domain refuse: a prep-scraps pile (or any food whose category is scraps), a removed outer sleeve or box,
    a pair of disposable gloves that has been worn (used)."""
    if not isinstance(rec, dict):
        return False
    if rec.get("type") in REFUSE_TYPES or (M.food(rec) or {}).get("cat") in REFUSE_CATS:
        return True
    return rec.get("gear_kind") == "disposable_gloves" and bool(rec.get("used"))


@hook("episode_start")
def mark_refuse(w, env):
    """Food refuse carries the kernel's waste flag from the baseline on (episode_start runs before the baseline)."""
    entities = w.s["entities"]
    for eid in sorted(entities):
        rec = entities[eid]
        if "waste" not in rec and is_refuse(rec):
            w.mut(eid)["waste"] = True


def refuse(w, eid, created=True):
    """Flag a food verb's or hook's new piece of refuse waste: true; created: log its mess flow from "created" to
    where it lies (6.5; a waste item is one count). No-op for an unknown id."""
    rec = w.s["entities"].get(eid)
    if rec is None:
        return
    if rec.get("waste") is not True:
        w.mut(eid)["waste"] = True
    if created:
        log_created(w, eid)


def log_created(w, eid):
    """A mess flow of one waste item from "created" to eid's place (world.mess_flow; nothing when the World does not
    track, as in generation and setup)."""
    flow = getattr(w, "mess_flow", None)
    if not callable(flow) or not getattr(w, "tracking", False):
        return
    from ..core import mess_location
    rec = w.s["entities"].get(eid) or {}
    dst = mess_location(w.s, rec.get("location"), rec.get("relation"))
    if dst is None:
        return
    flow("waste_item", 1.0, "created:" + eid, dst)


# ----------------------------------------------------------------------
# settings skills (10.3)

def _opener_for(state, need):
    for eid in sorted(state.get("entities") or {}):
        rec = state["entities"][eid]
        if rec.get("tool_kind") == need or need in (rec.get("capabilities") or ()):
            return eid
    return None


@settings_skill("opened", types=("packaged_food",))
def package_open_steps(state, eid):
    """A packaged food opened (one way: an opened package is never unopened); a can or a corked bottle with its
    opener when the house has one."""
    record = (state.get("entities") or {}).get(eid) or {}
    if record.get("opened") or not record.get("package"):
        return []
    step = {"skill": "use", "action": "open", "target": eid}
    need = record.get("opener_required")
    if need:
        tool = _opener_for(state, need)
        if tool is None:
            return []
        step["tool"] = tool
    return [(True, step)]


@settings_skill("food.bruise", types=("food_item",))
def bruise_steps(state, eid):
    """B14's damaged probe on soft produce: picked up with a firm grip it bruises (hooks._after_pick_up)."""
    record = (state.get("entities") or {}).get(eid) or {}
    f = M.food(record) or {}
    if record.get("package") or float(f.get("firmness", 0.6)) >= SOFT_FIRMNESS:
        return []
    value = round(min(1.0, float(f.get("bruise", 0.0)) + 0.35), 3)
    return [(value, {"skill": "use", "action": "pick_up", "target": eid, "grip": "firm"})]


# ----------------------------------------------------------------------
# the water meter (6.8)

def count_water(w, liters):
    """Count litres a food verb draws from the supply on the house water meter the ledger reads
    (house_sim/data/verify/resources.json meters.water.path). Nothing when the path is not a dotted section path."""
    if not liters or liters <= 0:
        return
    try:
        from ...kernel import ledger
        path = ((ledger.data("resources").get("meters") or {}).get("water") or {}).get("path")
    except (OSError, ValueError, KeyError):
        return
    if not isinstance(path, str) or "." not in path:
        return
    parts = path.split(".")
    node = w.sec(parts[0])
    for key in parts[1:-1]:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = node[key] = {}
        node = nxt
    node[parts[-1]] = round(float(node.get(parts[-1], 0.0) or 0.0) + float(liters), 3)


# ----------------------------------------------------------------------
# structured readings (15.2)

def reading(subject, quantity, value, unit, field=None):
    """A structured instrument reading (tasks/perceive.py's reading index): {"subject", "quantity", "value", "unit"
    (, "field")}."""
    out = {"subject": subject, "quantity": quantity, "value": value, "unit": unit}
    if field:
        out["field"] = field
    return out


def scale_reading(state, scale, display, known=None):
    """The structured reading of a kitchen scale's display: the mass (or, in ml, the volume) of the one thing whose
    weight it shows -- the single item on an un-tared scale, or the single thing in a tared vessel (as weigh's
    realistic grant; known(eid): the robot can see it, so a reading never names a thing hidden in a closed box) --
    else of the scale's load (subject the scale); None for a blank or overloaded display."""
    words = str(display or "").split()
    if len(words) != 2:
        return None
    try:
        value = float(words[0])
    except ValueError:
        return None
    unit = words[1]
    quantity = "volume" if unit == "ml" else "mass"
    entities = state.get("entities") or {}
    load = sorted(c for c, rec in entities.items() if rec.get("location") == scale and rec.get("relation") == "on")
    tare = float((entities.get(scale) or {}).get("tare_g", 0.0) or 0.0)
    subject = None
    if len(load) == 1:
        if abs(tare) < 0.5:
            subject = load[0]
        else:
            inside = sorted(c for c, rec in entities.items() if rec.get("location") == load[0])
            if len(inside) == 1 and abs(float(entities[load[0]].get("mass_kg", 0.0) or 0.0) * 1000.0 - tare) < 1.0                     and (known is None or known(inside[0])):
                subject = inside[0]
    if subject is None:
        return reading(scale, quantity, value, unit)
    return reading(subject, quantity, value, unit, "mass_kg" if quantity == "mass" else None)

"""Realistic-perception helpers for this domain (all identity / no-ops in oracle mode).

Kernel v2.4-v2.8 realistic perception (docs/PERCEPTION.md): the robot knows per entity and field only what succeeded
actions granted (ctx.grant), values render as buckets or noisy estimates below the needed tier, and look-alikes
stay confused until inspected/read. These helpers keep this domain's action messages, refusals, durations, inspector
extras and observer output at the precision the robot's senses and tools actually give.

Rules this domain follows (round 5, red-team findings):
  * a guard whose reason the robot could not have perceived never refuses (the refusal would be an oracle for a
    hidden fact): the act goes ahead and its harm is recorded (`privacy_violation`); when the robot did perceive the
    reason (it read the label), the refusal is generic and names no owner, recipient or hidden flag;
  * messages name things by id (handles) and never by the label of something the robot has not identified;
  * lists and dicts of ids in results are ordered by handle, never by true id (`order_ids`, `dumps`);
  * durations depend only on perceivable quantities (or are quantized to the precision the message reports).
"""
import copy
import json

from ...kernel.realism import GLANCE, INSPECT, MEASURE, MISSING, READ, TOUCH, _h

LEVEL_NAMES = {"glance": GLANCE, "inspect": INSPECT, "touch": TOUCH, "read": READ, "measure": MEASURE}


def realism_of(env):
    return getattr(env, "realism", None)


def known(env, eid, field):
    """Best knowledge level the robot holds for eid.field (MEASURE in oracle mode: everything is known)."""
    realism = realism_of(env)
    if realism is None:
        return MEASURE
    record = realism.env.world.s["entities"].get(eid)
    if record is None:
        return GLANCE
    return realism.knowledge.level(eid, record, field)


def entity_known(env, eid):
    realism = realism_of(env)
    if realism is None:
        return MEASURE
    record = realism.env.world.s["entities"].get(eid)
    return GLANCE if record is None else realism.knowledge.entity_level(eid, record)


def render(env, eid, field, value, level="inspect"):
    """One value as the robot perceives it at `level` (or its better knowledge): bucket / noisy / exact, or None
    when it is not perceivable. Oracle mode returns the value unchanged."""
    realism = realism_of(env)
    if realism is None:
        return value
    state = realism.env.world.s
    record = state["entities"].get(eid) or {}
    lvl = LEVEL_NAMES.get(level, level) if isinstance(level, str) else int(level)
    lvl = max(lvl, realism.knowledge.level(eid, record, field)) if record else lvl
    shown = realism.render(eid, record, field, value, lvl, realism.context(state, eid, record),
                           thermal="thermal" in getattr(realism.env, "sensors", ()))
    return None if shown is MISSING else shown


def perceives(env, eid, field):
    """True when the robot currently perceives eid.field at its tier (its knowledge meets the tier); always True in
    oracle mode. Used to decide whether a guard's reason is something the robot could know."""
    realism = realism_of(env)
    if realism is None:
        return True
    record = realism.env.world.s["entities"].get(eid)
    if record is None or field not in record:
        return False
    return render(env, eid, field, record[field], "glance") is not None


def unresolved(env, eid):
    """True in realistic mode while the robot has not identified eid (an unresolved look-alike or unfamiliar thing)."""
    realism = realism_of(env)
    if realism is None:
        return False
    record = realism.env.world.s["entities"].get(eid)
    if record is None:
        return False
    if record.get("novel"):
        return True
    return "candidates" in realism.identity(eid, record, realism.knowledge.entity_level(eid, record))


def count_seen(env, eid, n, level="inspect"):
    """A count as seen: exact when small (<= 12 at inspect), a noisy estimate above, a bucket at a glance."""
    if realism_of(env) is None:
        return n
    return render(env, eid, "count", int(n), level)


def mass_feel(env, eid, kg):
    """What lifting or pushing tells about a mass: light / medium / heavy / very heavy."""
    if realism_of(env) is None:
        return kg
    return render(env, eid, "mass_kg", float(kg), "touch")


def name_of(env, eid):
    """How messages refer to an entity: its label in oracle mode; its id (translated to a handle) in realistic
    mode, so a message never names an unresolved look-alike."""
    if realism_of(env) is None:
        rec = env.world.s["entities"].get(eid) or {}
        return rec.get("label", eid)
    return eid


def approx_litres(value, step=0.05):
    """A poured or seen volume to the eye's precision (about 50 ml)."""
    return round(round(float(value) / step) * step, 2)


def spill_words(litres):
    if litres < 0.002:
        return "none"
    if litres < 0.01:
        return "a few drops"
    if litres < 0.05:
        return "a small spill"
    return "a large spill"


def has_read(env, eid, fields):
    """True when the robot has read any of `fields` on eid (always True in oracle mode)."""
    if realism_of(env) is None:
        return True
    return any(known(env, eid, f) >= READ for f in fields)


def was_read(env, eid, fields=("recipient", "to_text", "text", "label_text", "address", "name_label")):
    """True when the robot read eid (a read of the whole item, or any of `fields`); always True in oracle mode."""
    if realism_of(env) is None:
        return True
    return entity_known(env, eid) >= READ or has_read(env, eid, fields)


def _sort_key(realism, eid):
    # the handle an id has (or would get): a hash, so the order says nothing about the true ids; computed without
    # issuing a handle, so sorting never hands out handles for things the robot has not been shown
    handles = realism.handles
    return handles.by_id.get(eid) or "obj_" + _h("handle", handles.seed, handles.salt, eid)


def order_ids(env, ids):
    """Ids in a result list ordered by handle in realistic mode (sorted true ids would rank look-alikes by type);
    sorted ids in oracle mode."""
    realism = realism_of(env)
    if realism is None:
        return sorted(ids)
    return sorted(ids, key=lambda e: _sort_key(realism, e))


def ordered(env, value):
    """value with every dict's keys ordered: entity ids by handle, other keys alphabetically (realistic mode)."""
    realism = realism_of(env)
    if realism is None:
        return value
    entities = realism.env.world.s["entities"]

    def key(k):
        if isinstance(k, str) and k in entities:
            return (0, _sort_key(realism, k))
        return (1, str(k))

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(v[k]) for k in sorted(v, key=key)}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(value)


def dumps(env, value):
    """A JSON action message. json sort_keys would order id-keyed dicts by true id (the message is translated to
    handles token by token afterwards), so realistic mode orders them by handle instead."""
    if realism_of(env) is None:
        return json.dumps(value, sort_keys=True)
    return json.dumps(ordered(env, value))


def privacy_violation(ctx, eid, rule):
    """Realistic mode: the act goes ahead although a privacy rule forbids it, because the robot did not perceive the
    reason (unread label, a never-tier owner): refusing would reveal the hidden fact. It counts as a privacy
    violation (a global failure flag), the consequence a real robot's careless act would have."""
    ctx.w.stat("privacy_violations")
    ctx.w.emit("privacy_violation", target=eid, rule=rule)


def tier_on(w, eid, tier, fields):
    """Per-entity tiers (kernel v2.7 `_perception`, same shape as a type block): fields this domain writes onto
    another domain's entity under a name other domains use with other meanings (so a file-level field_tiers entry
    would be wrong for them) get their tier on that entity only. tier: "hidden" | "inspect" | "read" | "glance" |
    "measure". Mode-independent and invisible (an underscore key); field_tiers cover this domain's own names."""
    rec = w.s["entities"].get(eid)
    if rec is None:
        return
    own = copy.deepcopy(rec.get("_perception") or {})
    if tier == "measure":
        table = dict(own.get("measure") or {})
        changed = any(f not in table for f in fields)
        for f in fields:
            table.setdefault(f, "scale")
        own["measure"] = table
    else:
        names = sorted(set(own.get(tier) or []) | set(fields))
        changed = names != list(own.get(tier) or [])
        own[tier] = names
    if changed:
        w.mut(eid)["_perception"] = own


def hide_on(w, eid, fields):
    """Runtime truth written onto another domain's entity is never perceivable there (germs from a wrong grip)."""
    tier_on(w, eid, "hidden", fields)


def opaque_amount(env, eid, record):
    """True when how much eid holds is not visible to the robot: an `opaque` container, a level the setup hides
    (`secret_fields`) or a closed container without see-through walls -- unless it learned the amount by touch or
    measurement (the kernel's rule for a sealed container's liquid_l: a plain pick-up does not tell the level)."""
    if realism_of(env) is None:
        return bool(record.get("opaque"))
    secret = set(record.get("secret_fields") or [])
    hidden = bool(record.get("opaque")) or bool(secret & {"liquid_l", "level", "fill_level", "layers", "units_count", "remaining"})
    see_through = record.get("transparent") or str(record.get("material") or "") in ("glass", "clear_plastic", "acrylic")
    closed = bool(record.get("openable")) and not record.get("open") and not see_through
    if not (hidden or closed):
        return False
    return not any(known(env, eid, f) >= TOUCH for f in ("liquid_l", "level", "fill_level", "units_count"))

"""The must-pass battery: legitimate variants that must pass (VERIFY_HARDENING 11; WP6).

    M1 alt_instance  another instance of the same type for each instrument role (an entity the trace uses as a tool,
                     container, vehicle or consumable that is in no success or failure path by id and is no answer)
                     and each fungible target role; same room first, reachable
    M2 alt_place     each non-target object the reference moves (not an instruction target, not destination-scored):
                     left at its baseline (put back there at the end when it was moved for access), put at its home,
                     handed to its owner, or put on another surface of a work room
    M3 reorder       two adjacent top-level steps with disjoint footprints, neither using a save of the other, swapped
    M4 detour        after the first third of the plan: inspect two non-target things in the work room and go to an
                     adjacent room and back, within half the time and step slack
    M5 paraphrase    load-bearing COMM commands rewritten by speech.paraphrase (k = 1, 2) with quantities in the
                     other unit system (speech.convert_units; a typed measurement converted)
    M6 poll          each observation-conditioned wait replaced by an agent-legal loop: wait 60 then the perception
                     command, until the observed condition holds, within the budget

Every realised run must pass (11.3). Outcomes: passes (met), fails (unmet; G8 fails unless exempted; the record
names the labels that rejected it), unrealised (unmet, battery debt; G8 needs 80 % of applicable instances
realised), skipped:na, skipped:cap (unmet). Seeds: M1-M4 and M6 on the tier's must-pass seeds (3 for H0), M5 on 2.
"""
import copy
import json

from . import budget as budget_mod
from . import generate as gen
from . import trace as trace_mod
from .policies import Policy

HINTS = {"M1": "the envelope or a predicate names the reference's instance: use a type selector within the work "
               "rooms",
         "M2": "a non-target item has a narrow `to` scope the instruction does not ask for",
         "M3": "a predicate or guard depends on step order",
         "M4": "the budget has no slack or a guard fires on looking around",
         "M5": "speech scoring depends on the reference's wording or units",
         "M6": "the task needs a privileged wait"}
INSTRUMENT_KEYS = ("tool", "aid", "lure", "leash", "funnel", "hanger", "source", "through", "food", "treat", "item")
UNIT_PAIRS = {"g": ("oz", 0.035274), "kg": ("lb", 2.20462), "cm": ("in", 0.393701), "m": ("ft", 3.28084),
              "c": ("f", None), "l": ("gal", 0.264172), "ml": ("fl_oz", 0.033814), "oz": ("g", 28.3495),
              "lb": ("kg", 0.453592), "in": ("cm", 2.54), "f": ("c", None)}


def _seeds(c, key="mustpass_seeds"):
    return budget_mod.family_seeds(c.ctxs, int(c.tier.get(key, 3)))


def _carriers(ctx):
    """Things the robot picked up and then used as a carrier: the destination of a later deposit, or the target
    of an emptying command (a basket carrying the laundry, the dustpan emptied into the bin)."""
    held, out = set(), set()
    for e in ctx.trace:
        if not e.get("ok"):
            continue
        if e.get("action") == "pick_up" and isinstance(e.get("target"), str):
            held.add(e["target"])
        dest = e.get("destination")
        if trace_mod.command_class(e, ctx.baseline) == "DEPOSIT" and isinstance(dest, str) and dest in held:
            out.add(dest)
        if trace_mod.content_deposit(e.get("action")) and e.get("target") in held:
            out.add(e["target"])
    return out


def _tool_like(record):
    words = [record.get("type"), record.get("category")] + list(record.get("is_a") or ())
    return bool(record.get("tool") or record.get("consumable")) or any(
        isinstance(w, str) and w in ("tool", "hand_tool", "cleaning_tool", "container", "vehicle", "cart",
                                     "carrier", "consumable") for w in words)


def _instrument_roles(c, ctx):
    """M1's roles (11.2): entities the trace uses as a tool, container, vehicle or consumable -- named by a tool
    argument, picked up and then used as a carrier, or picked up and tool-like by type -- that are in no success or
    failure predicate by id and are no answer; plus declared fungible targets. A non-target the robot only moves
    aside is M2's, not an instrument."""
    answers = gen._answer_members(ctx) | gen._answer_groups(ctx)
    carriers = _carriers(ctx)
    roles = []
    for e in ctx.trace:
        if not e.get("ok"):
            continue
        ids = []
        for key in INSTRUMENT_KEYS:
            v = (e.get("args") or {}).get(key)
            if isinstance(v, str):
                ids.append(v)
        target = e.get("target")
        if e.get("action") == "pick_up" and isinstance(target, str) and (
                target in carriers or _tool_like(ctx.entities.get(target) or {}) or gen._fungible(c, ctx, target)
                and target in [ctx.bindings.get(str(n).strip("{}")) for n in c.fungible]):
            ids.append(target)
        for eid in ids:
            if eid not in ctx.entities or eid in answers or eid in roles:
                continue
            if gen._in_success(ctx, eid) and not gen._fungible(c, ctx, eid):
                continue
            mentioned = any(('"' + eid + '"') in json.dumps(r, default=str) for r in (ctx.plan.get("rules") or {})
                            .values())
            if mentioned and not gen._fungible(c, ctx, eid):
                continue
            roles.append(eid)
    return roles


def _same_type(ctx, eid):
    kind = (ctx.entities.get(eid) or {}).get("type")
    room = gen._room_of(ctx, eid)
    others = [x for x in sorted(ctx.entities) if x != eid and (ctx.entities[x] or {}).get("type") == kind and
              not (ctx.entities[x] or {}).get("connects")]
    same_room = [x for x in others if gen._room_of(ctx, x) == room]
    return same_room + [x for x in others if x not in same_room]


def M1(c):
    pols, skips = [], []
    for ctx in _seeds(c):
        roles = _instrument_roles(c, ctx)[:2]
        if not roles:
            skips.append(gen._skip("M1", "alt_instance", ctx.seed, "no instrument or fungible role"))
            continue
        made = False
        for r, role in enumerate(roles):
            alts = _same_type(ctx, role)
            if not alts:
                continue
            alt = alts[0]
            items, changed = gen._swap_items(ctx, role, alt, False)
            if not changed:
                continue
            pols.append(Policy(gen._pid("M1", "alt_instance", ctx.seed, "r{}".format(r)), "M1", "alt_instance",
                               ctx.seed, items, effect={"all": [{"kind": "tagged_ok", "tag": "retarget"}]},
                               mustpass=True, meta={"role": role, "alt": alt}))
            made = True
        if not made:
            skips.append(gen._skip("M1", "alt_instance", ctx.seed, "no other instance of an instrument's type"))
    return pols, skips


def _non_targets(c, ctx):
    """[(trace index, object)] of DEPOSIT commands moving an object that is not an instruction target and not
    destination-scored (in no success or failure predicate, no answer)."""
    roles = c.full.get("instruction_roles") or {}
    targets = set()
    for name, role in roles.items() if isinstance(roles, dict) else ():
        if role in ("target", "destination"):
            value = ctx.bindings.get(str(name).strip("{}"))
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str):
                    targets.add(v)
    answers = gen._answer_members(ctx)
    out = []
    for i, obj, _dest in trace_mod.deposits(ctx):
        if obj in targets or obj in answers or gen._in_success(ctx, obj) or obj in [o for _, o in out]:
            continue
        out.append((i, obj))
    return out[:2]


def _alt_places(c, ctx, i, obj):
    """[(name, steps, effect check)] of M2's alternatives for one non-target object."""
    out = []
    base = ctx.entities.get(obj) or {}
    place = trace_mod.placement_of(base)
    if place.get("location"):
        out.append(("baseline", "end", [gen._put_back(obj, place)],
                    {"kind": "at", "object": obj, "place": place["location"], "how": "placement", "placement": place}))
    home = base.get("home")
    if isinstance(home, str) and home and (home in ctx.entities or home in (ctx.baseline.get("rooms") or {})):
        out.append(("home", "swap", [{"skill": "put", "obj": obj, "dest": home, "close": True}],
                    {"kind": "at", "object": obj, "place": home, "how": "chain" if home in ctx.entities else "room"}))
    owner = base.get("owner")
    if isinstance(owner, str) and owner in (ctx.baseline.get("people") or {}):
        out.append(("owner", "swap", [{"skill": "give", "obj": obj, "person": owner}],
                    {"kind": "at", "object": obj, "place": owner, "how": "chain"}))
    rooms = gen._room_of(ctx, obj)
    work = set(((ctx.task.get("hardening") or {}).get("envelope") or {}).get("work_rooms") or ())
    work = {w[5:] if isinstance(w, str) and w.startswith("room:") else w for w in work} or {rooms}
    dest = ctx.trace[i].get("destination")
    surfaces = []
    for rid in sorted(w for w in work if isinstance(w, str)):
        for eid in gen._in_room(ctx, rid):
            rec = ctx.entities.get(eid) or {}
            if eid in (obj, dest, place.get("location")) or not rec.get("surface") or \
                    gen._classes(ctx, eid) & set(gen.SINK_CLASSES):
                continue
            surfaces.append(eid)
    if surfaces:
        s = surfaces[0]
        out.append(("surface", "swap", [{"skill": "put", "obj": obj, "dest": s}],
                    {"kind": "at", "object": obj, "place": s, "how": "chain"}))
    return out


def M2(c):
    pols, skips = [], []
    for ctx in _seeds(c):
        objs = _non_targets(c, ctx)
        if not objs:
            skips.append(gen._skip("M2", "alt_place", ctx.seed, "the reference moves no non-target object"))
            continue
        for n, (i, obj) in enumerate(objs):
            for name, how, steps, check in _alt_places(c, ctx, i, obj)[:2]:
                if how == "end":
                    items = gen._replays(ctx) + [{"kind": "attack", "candidates": [steps], "tag": "inserted"}]
                else:
                    items = gen._b3_items(ctx, i, [steps])
                    for item in items:
                        if item["kind"] == "attack":
                            item["tag"] = "inserted"
                pols.append(Policy(gen._pid("M2", name, ctx.seed, "o{}".format(n)), "M2", name, ctx.seed, items,
                                   effect={"all": [check]}, mustpass=True, meta={"object": obj}))
    return pols, skips


def _top_footprint(ctx, k):
    return {i for e in ctx.trace if e.get("top") == k for i in trace_mod.named_ids(e, ctx.baseline)}


def _uses_save(raw, saves):
    text = json.dumps(raw, default=str)
    return any(("{" + s + "}") in text for s in saves)


def M3(c):
    pols, skips = [], []
    for ctx in _seeds(c):
        plan = list(ctx.oracle.get("reference") or ())
        saves_of = {}
        for s in ctx.saves or ():
            saves_of.setdefault(s.get("top"), set()).add(s.get("name"))
        found = None
        for k in range(len(plan) - 1):
            a, b = _top_footprint(ctx, k), _top_footprint(ctx, k + 1)
            if not a or not b or a & b:
                continue
            if _uses_save(plan[k + 1], saves_of.get(k, ())) or _uses_save(plan[k], saves_of.get(k + 1, ())):
                continue
            found = k
            break
        if found is None:
            skips.append(gen._skip("M3", "reorder", ctx.seed, "no adjacent top-level steps with disjoint footprints"))
            continue
        k = found
        items = [{"kind": "plain", "step": copy.deepcopy(s), "tag": None} for s in plan[:k]]
        items += [{"kind": "plain", "step": copy.deepcopy(plan[k + 1]), "tag": "swapped"},
                  {"kind": "plain", "step": copy.deepcopy(plan[k]), "tag": "swapped"}]
        items += [{"kind": "plain", "step": copy.deepcopy(s), "tag": None} for s in plan[k + 2:]]
        p = Policy(gen._pid("M3", "reorder", ctx.seed, k), "M3", "reorder", ctx.seed, items,
                   effect={"all": [{"kind": "tagged_ok", "tag": "swapped"}]}, mustpass=True, meta={"tops": [k, k + 1]})
        p.fork_ok = False
        pols.append(p)
    return pols, skips


def M4(c):
    pols, skips = [], []
    for ctx in _seeds(c):
        plan = list(ctx.oracle.get("reference") or ())
        ref = ctx.record or {}
        slack_s = ctx.time_budget() - float(ref.get("time_s") or 0.0)
        slack_steps = ctx.step_budget() - int(ref.get("steps") or 0)
        if slack_steps < 4 or slack_s < 30:
            skips.append(gen._skip("M4", "detour", ctx.seed, "no slack: {} steps, {:.0f} s between the reference and "
                                                             "the budget".format(slack_steps, slack_s)))
            continue
        k = max(1, len(plan) // 3) if len(plan) > 1 else len(plan)
        # the detour goes before a step that brings its own approach (a skill, a navigation, speech or a wait): a
        # raw command right after the reference's own approach assumes the robot is still standing there
        while k < len(plan) and isinstance(plan[k], dict) and "do" in plan[k] and \
                trace_mod.action_class(plan[k].get("do")) not in ("NAVIGATION", "COMM", "WAIT"):
            k += 1
        boundary = next((e["i"] for e in ctx.trace if (e.get("top") or 0) >= k), len(ctx.trace))
        room = ctx.trace[boundary - 1].get("room") if boundary > 0 else trace_mod.work_room(ctx)
        touched = {i for e in ctx.trace for i in trace_mod.named_ids(e, ctx.baseline)}
        # things standing in the room itself (furniture, floor items): what a look around shows; a thing inside a
        # closed cabinet or seen from elsewhere is not in view to approach
        others = [e for e in gen._in_room(ctx, room) if e not in touched and (ctx.entities.get(e) or {}).get(
            "location") == room and not (ctx.entities.get(e) or {}).get("connects") and not (
            ctx.entities.get(e) or {}).get("mounted") and (ctx.seen is None or e in ctx.seen)][:2]
        steps = [{"do": "look", "target": "around"}] if ctx.seen is not None else []
        budget_steps = max(2, int(slack_steps * 0.5))
        for eid in others:
            if len(steps) + 2 > budget_steps:
                break
            steps += [{"skill": "approach", "target": eid}, {"do": "inspect", "target": eid}]
        nxt = [r for r in gen._route_rooms(ctx, room)[:1] if not (ctx.baseline.get("rooms") or {}).get(r, {})
               .get("outdoor")]
        if nxt and len(steps) + 4 <= budget_steps and slack_s * 0.5 >= 60:
            steps += [{"skill": "go_room", "room": nxt[0]}, {"skill": "go_room", "room": room}]
        if not [s for s in steps if s.get("do") != "look"]:
            skips.append(gen._skip("M4", "detour", ctx.seed, "nothing to look at within half the slack"))
            continue
        items = [{"kind": "replay", "entry": e} for e in ctx.trace[:boundary]]
        items += [{"kind": "plain", "step": s, "tag": "inserted"} for s in steps]
        items += [{"kind": "plain", "step": copy.deepcopy(s)} for s in plan[k:]]
        pols.append(Policy(gen._pid("M4", "detour", ctx.seed, k), "M4", "detour", ctx.seed, items,
                           effect={"all": [{"kind": "tagged_ok", "tag": "inserted"}]}, mustpass=True,
                           meta={"slack_s": round(slack_s, 1), "slack_steps": slack_steps}))
    return pols, skips


def _convert_report(step):
    """A typed measurement report in the other unit system (value and unit converted)."""
    unit = str(step.get("unit") or "").lower()
    value = step.get("value")
    if unit not in UNIT_PAIRS or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    other, factor = UNIT_PAIRS[unit]
    if factor is None:
        new = value * 9.0 / 5.0 + 32.0 if unit == "c" else (value - 32.0) * 5.0 / 9.0
    else:
        new = value * factor
    out = dict(step)
    out["value"] = round(new, 3)
    out["unit"] = other
    return out


def M5(c, prechecked=None):
    from ...kernel import speech
    pols, skips = [], []
    for ctx in _seeds(c, "mustpass_m5_seeds"):
        comm = gen._comm_entries(ctx)
        bearing = (prechecked or {}).get(ctx.seed)
        if bearing is not None:
            comm = [e for e in comm if e["i"] in bearing]
        comm = comm[:2]
        if not comm:
            skips.append(gen._skip("M5", "paraphrase", ctx.seed, "no load-bearing COMM command"))
            continue
        rewrites = {}
        for k, e in enumerate(comm, start=1):
            step = trace_mod.replay_step(e)
            if e["action"] == "report":
                conv = _convert_report(step)
                if conv is not None:
                    rewrites[e["i"]] = conv
                continue
            text = (e.get("args") or {}).get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            to = (e.get("args") or {}).get("to")
            new = speech.paraphrase(text, k=k, state=ctx.baseline, to=to)
            new = speech.convert_units(new or text, "other", state=ctx.baseline, to=to) or new
            if new and new != text:
                rewrites[e["i"]] = dict(step, text=new)
        if not rewrites:
            skips.append(gen._skip("M5", "paraphrase", ctx.seed, "the paraphrase table and unit change leave the "
                                                                  "commands as they are"))
            continue
        items = []
        for e in ctx.trace:
            if e["i"] in rewrites:
                items.append({"kind": "do", "step": rewrites[e["i"]], "tag": "mutated", "entry": e})
            else:
                items.append({"kind": "replay", "entry": e})
        pols.append(Policy(gen._pid("M5", "paraphrase", ctx.seed, len(rewrites)), "M5", "paraphrase", ctx.seed,
                           items, effect={"all": [{"kind": "delivered", "tag": "mutated"}]}, mustpass=True,
                           meta={"rewrites": {str(k): v.get("text") or v.get("value") for k, v in rewrites.items()}}))
    return pols, skips


def M6(c):
    pols, skips = [], []
    for ctx in _seeds(c):
        waits = [e for e in ctx.trace if trace_mod.conditioned(e)]
        if not waits:
            skips.append(gen._skip("M6", "poll", ctx.seed, "no observation-conditioned wait"))
            continue
        items, made = [], 0
        last_look = None
        for e in ctx.trace:
            if trace_mod.command_class(e, ctx.baseline) == "PERCEPTION":
                last_look = trace_mod.replay_step(e)
            until = (e.get("args") or {}).get("until")
            if trace_mod.conditioned(e) and isinstance(until, dict):
                limit = float((e.get("args") or {}).get("seconds") or (e.get("wait") or {}).get("requested_s") or
                              3600)
                items.append({"kind": "loop", "until": until, "poll_s": 60, "max_s": limit,
                              "look": [last_look or {"do": "look"}], "tag": "loop"})
                made += 1
            else:
                items.append({"kind": "replay", "entry": e})
        if not made:
            skips.append(gen._skip("M6", "poll", ctx.seed, "the conditioned waits wait for notices, not a condition"))
            continue
        pols.append(Policy(gen._pid("M6", "poll", ctx.seed, made), "M6", "poll", ctx.seed, items,
                           effect={"all": [{"kind": "observed", "tag": "loop"}]}, mustpass=True))
    return pols, skips


GENERATORS = (("M1", M1), ("M2", M2), ("M3", M3), ("M4", M4), ("M5", M5), ("M6", M6))


def summary(runs, spec):
    """{"applicable", "realised", "passed", "failed_unexempt", "failed", "unrealised"} for G8: applicable = every
    instance not skipped:na; realised = passed or failed; exempted failures are the spec's must-pass exemption
    proposals (10.7) the review file accepts for this spec hash."""
    exempt = _accepted_exemptions(spec)
    counted = [r for r in runs if r.get("outcome") not in ("skipped:na", "skipped:tier")]
    passed = [r for r in counted if r.get("outcome") == "passes"]
    failed = [r for r in counted if r.get("outcome") == "fails"]
    unexempt = [r for r in failed if r.get("id") not in exempt and str(r.get("id")).split("@")[0] not in exempt]
    return {"applicable": len(counted), "realised": len(passed) + len(failed), "passed": len(passed),
            "failed": sorted(r["id"] for r in failed), "failed_unexempt": len(unexempt),
            "unrealised": sorted(r["id"] for r in counted if r.get("outcome") == "unrealised"),
            "capped": sorted(r["id"] for r in counted if r.get("outcome") == "skipped:cap")}


def _accepted_exemptions(spec):
    try:
        from .. import escapes
        from ..spec import expand
        from ..verify import spec_hash
        cid = expand(spec).get("catalog_id")
        rows = escapes.accepted_records(cid, spec_hash(spec))
    except Exception:                       # noqa: BLE001  (no review file: nothing exempted)
        return set()
    out = set()
    for row in rows or ():
        if isinstance(row, dict) and row.get("decision") == "accept" and str(row.get("policy", "")).startswith("M"):
            out.add(str(row["policy"]))
    return out

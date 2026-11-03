"""The public gaming battery's generators, B2-B14 (VERIFY_HARDENING 10.3; WP6). B1 (idle-then-done at every
sample) is the verifier's idle rollout (8.7) and runs in S2 / S3.

Each generator takes the battery Context and returns (policies, skips): Policy objects (policies.py) and static
`skipped:na` records decided from the spec, the trace and the scene before generation (P4: only these are free).
Policy ids are stable: <family>.<variant>@<seed>#<index> (index: the edited trace command, the role, the
candidate, the mutation or the tau, 10.1). Every random choice uses random.Random("battery:<catalog_id>:<seed>:
<policy id>") (P7), or the private salt's RNG for the private families (hidden.py).

    Context(spec, builds, ctxs, mode, level, tier, analysis=None, rng=None)
    prepare(ctx)                    the per-seed data accept.py reads (footprints, rules, said and state labels)
    B2 ... B14(context) -> (policies, skips)
    GENERATORS                      {family: generator} in 10.5's order
"""
import copy
import json
import random
import statistics

from ...kernel import plugins
from ...kernel.predicates import root_room
from .. import verify
from . import accept as accept_mod
from . import answers as answers_mod
from . import budget as budget_mod
from . import mapping
from . import trace as trace_mod
from .policies import Policy

PERCEPTION = ("PERCEPTION",)
SETTINGS_ACTIONS = ("set", "switch_on", "switch_off", "set_mode", "turn", "move_lever", "set_time", "set_timer",
                    "tilt_slats", "preheat")
SINK_CLASSES = ("waste", "drain", "basin", "vacuum", "compost", "outdoor_waste", "toilet", "utility_sink",
                "floor_drain", "grease_container", "hazardous_waste", "absorbent")
CONCEALING = ("concealing",)
MAX_ROLES = 2
MAX_ALTS = 3
B10_MEMBERS = 8
B10_SAME_TYPE = 12
B12_COMMANDS = 6
B6_UNDO_ALL = 20
LOAD_BEARING_KINDS = ("speak", "announce", "confirm", "explain", "alert", "report", "relay_message", "read_aloud",
                      "call", "refuse", "handoff")
SPEECH_ACTIONS = ("speak", "announce", "confirm", "explain", "alert", "relay_message", "refuse", "handoff", "call")


class Context(object):
    """What every generator reads: the spec, the seeds' material (trace.Seed, V's order), the mode, the level, the
    horizon tier row, the analysis record."""

    def __init__(self, spec, builds, ctxs, mode, level, tier, analysis=None, salt=None):
        from ..spec import expand
        self.spec = spec
        self.full = expand(spec)
        self.cid = self.full.get("catalog_id") or "custom"
        self.builds = builds or {}
        self.ctxs = list(ctxs)
        self.by_seed = {c.seed: c for c in self.ctxs}
        self.mode, self.level, self.tier = mode, level, tier
        self.analysis = analysis or {}
        self.salt = salt
        self.budget_formula_ok = bool(((builds or {}).get("budget") or {}).get("formula_ok"))
        self.fungible = set(self.full.get("fungible") or ())
        self.expected = list((builds or {}).get("expected") or ())

    def rng(self, seed, pid):
        if self.salt is not None:
            from .. import private
            return private.rng(self.salt, self.cid, seed, pid)
        return random.Random("battery:{}:{}:{}".format(self.cid, seed, pid))

    def seeds_for(self, n):
        return budget_mod.family_seeds(self.ctxs, n)

    def deterministic_seeds(self):
        return self.seeds_for(int(self.tier.get("battery_seeds", 3)))


# ----- per-seed plan data -------------------------------------------------------------------------------------

def prepare(ctx):
    """ctx.plan: footprints and predicates per label, said labels, state-kind labels, speech goal labels."""
    if ctx.task is None:
        ctx.plan = {"footprints": {}, "rules": {}, "said": (), "state_labels": set()}
        return ctx.plan
    rules, state = {}, set()
    speech = set()
    for label, rule in trace_mod.goal_items(ctx.task):
        rules["goal:" + label] = rule
        rules["settle:" + label] = rule
        if verify._state_kind(rule):
            state.add("goal:" + label)
        text = json.dumps(rule, default=str)
        if '"said"' in text or "received" in text or "key_claims" in text:
            speech.add("goal:" + label)
    for label, rule in trace_mod.failure_items(ctx.task):
        rules["fail:" + label] = rule
    ctx.plan = {"footprints": trace_mod.footprints(ctx.task), "rules": rules,
                "said": sorted(verify._said_labels(ctx.task)), "state_labels": state, "speech_goals": speech}
    return ctx.plan


# ----- helpers --------------------------------------------------------------------------------------------------

def _pid(family, sub, seed, index):
    return "{}.{}@{}#{}".format(family, sub, seed, index) if sub else "{}@{}#{}".format(family, seed, index)


def _replays(ctx, entries=None, tag=None):
    return [{"kind": "replay", "entry": e, "tag": tag} for e in (entries if entries is not None else ctx.trace)]


def _skip(family, sub, seed, why):
    return {"id": _pid(family, sub, seed, "na") if seed is not None else "{}.{}".format(family, sub or "all"),
            "family": family, "sub": sub, "seed": seed, "outcome": "skipped:na", "why": why}


def _room_of(ctx, eid, state=None):
    state = state or ctx.baseline
    try:
        return root_room(state, eid)
    except Exception:                       # noqa: BLE001
        rooms = (state or {}).get("rooms") or {}
        return eid if eid in rooms else None


def _robot_room_at(ctx, i):
    pre = ctx.robot_pre.get(i) or {}
    return pre.get("room") or ((ctx.baseline or {}).get("robot") or {}).get("room")


def _route_rooms(ctx, start, indoor_first=True):
    """Rooms reachable from start, nearest first by room hops (skills.room_route), start excluded; indoor rooms
    reached without an exterior door or gate on the way (a restore-class item the route would leave open) first."""
    from ..skills import room_route
    from ...kernel import ledger
    state = ctx.baseline or {}
    rooms = state.get("rooms") or {}
    book = None
    out = []
    for rid in sorted(rooms):
        if rid == start:
            continue
        route = room_route(state, start, rid)
        if route is None:
            continue
        penalty = 0
        if indoor_first:
            if (rooms.get(rid) or {}).get("outdoor"):
                penalty += 2
            for _room, cid in route:
                door = (state.get("entities") or {}).get(((state.get("connections") or {}).get(cid) or {})
                                                         .get("door") or "")
                if door is not None:
                    if book is None:
                        book = ledger.Ledger({}, state, state)
                    if book.restore_class(door):
                        penalty += 1
                        break
        out.append((penalty, len(route), rid))
    return [rid for _, _, rid in sorted(out)]


def _in_success(ctx, eid):
    for fp in (ctx.plan.get("footprints") or {}).values():
        if eid in fp.ids or any(f[0] == eid for f in fp.fields):
            return True
    return False


def _answer_members(ctx):
    out = set()
    for ans in (ctx.answers or {}).values():
        value = ans.get("value")
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str):
                out.add(v)
    return out


def _answer_groups(ctx):
    out = set()
    for ans in (ctx.answers or {}).values():
        for v in ans.get("group") or ():
            if isinstance(v, str):
                out.add(v)
    return out


def _to_scope(ctx, obj):
    """The envelope's move scope ids of obj (its `to` list; ids only)."""
    from ...kernel import ledger
    env = (ctx.task.get("hardening") or {}).get("envelope") or {}
    out = set()
    for selector, value in (env.get("move") or {}).items():
        try:
            if obj not in ledger.select(ctx.baseline, selector):
                continue
        except Exception:                   # noqa: BLE001
            continue
        for scope in (value or {}).get("to") or () if isinstance(value, dict) else ():
            for sid in ledger.ids_of(scope) if isinstance(scope, str) and scope not in ledger.SCOPE_WORDS else []:
                out.add(sid[5:] if sid.startswith("room:") else sid)
    return out


def _classes(ctx, eid):
    from ...kernel import ledger
    try:
        return set(ledger.chain_classes(ctx.baseline, eid))
    except Exception:                       # noqa: BLE001
        return set()


def _accepts(record, obj_record, liquid=False):
    if not isinstance(record, dict) or record.get("connects") or record.get("portable") and not \
            record.get("container"):
        return False
    if liquid:
        return bool(record.get("container")) and (record.get("capacity_l") or record.get("holds_liquid") or
                                                  record.get("liquid_l") is not None)
    return bool(record.get("container") or record.get("surface") or record.get("accepts_items") or
                record.get("shelves"))


def _in_room(ctx, rid):
    entities = ctx.entities
    out = []
    for eid in sorted(entities):
        if _room_of(ctx, eid) == rid:
            out.append(eid)
    return out


def _effectful_entries(ctx):
    return [e for e in ctx.trace if trace_mod.effectful(e, ctx.baseline)]


def _without(ctx, keep):
    """Replay items of the trace entries keep(entry) accepts."""
    return [{"kind": "replay", "entry": e} for e in ctx.trace if keep(e)]


def _fixed_waits(ctx, entries):
    """Entries with each observation-conditioned wait replaced by a plain wait of its observed duration."""
    out = []
    for e in entries:
        if trace_mod.conditioned(e):
            waited = max(1, int(round(float((e.get("wait") or {}).get("waited_s") or 0.0))))
            out.append({"kind": "do", "step": {"do": "wait", "seconds": waited}, "entry": e, "tag": "fixed_wait"})
        else:
            out.append({"kind": "replay", "entry": e})
    return out


def _retarget(entry, old, new):
    """entry with every occurrence of id old replaced by new (None when it does not name old)."""
    def walk(v):
        if v == old:
            return new, True
        if isinstance(v, list):
            vals = [walk(x) for x in v]
            return [x for x, _ in vals], any(h for _, h in vals)
        if isinstance(v, dict):
            vals = {k: walk(x) for k, x in v.items()}
            return {k: x for k, (x, _) in vals.items()}, any(h for _, h in vals.values())
        return v, False
    out = copy.deepcopy(entry)
    hit = False
    for key in ("target", "destination"):
        out[key], h = walk(entry.get(key))
        hit = hit or h
    args, h = walk(entry.get("args") or {})
    out["args"] = args
    hit = hit or h
    return out if hit else None


# ----- B2: say / report without acting -------------------------------------------------------------------------

def B2(c):
    pols, skips = [], []
    for ctx in c.deterministic_seeds():
        state_false = [l for l in ctx.initial_unmet if ("goal:" + l) in ctx.plan["state_labels"]]
        if not ctx.plan["state_labels"]:
            skips.append(_skip("B2", None, ctx.seed, "talk-only: every success label is event-kind (B12 covers it)"))
            continue
        if not state_false:
            skips.append(_skip("B2", None, ctx.seed, "no state-kind success label is false at t=0"))
            continue
        dropped = [e for e in ctx.trace if trace_mod.command_class(e, ctx.baseline) in ("EFFECT", "DEPOSIT")]
        if not dropped:
            skips.append(_skip("B2", None, ctx.seed, "the reference has no EFFECT or DEPOSIT command"))
            continue
        items = _without(ctx, lambda e: trace_mod.command_class(e, ctx.baseline) not in ("EFFECT", "DEPOSIT"))
        touched = sorted({i for e in dropped for i in trace_mod.named_ids(e, ctx.baseline)})
        pols.append(Policy(_pid("B2", None, ctx.seed, 0), "B2", None, ctx.seed, items,
                           effect={"all": [{"kind": "kept_ok"}, {"kind": "no_class", "classes": ["EFFECT",
                                                                                               "DEPOSIT"]}]},
                           accept={"about": touched}))
    return pols, skips


# ----- B3: move the target mess or object ---------------------------------------------------------------------

def _b3_deposits(ctx):
    out = []
    answers = _answer_members(ctx)
    for i, obj, dest in trace_mod.deposits(ctx):
        record = ctx.record_at(i, obj, "pre") or ctx.entities.get(obj) or {}
        if _in_success(ctx, obj) or obj in answers or trace_mod.mess_carrier(record) or ctx.carried.get(i) or \
                (isinstance(dest, str) and _classes(ctx, dest) & set(SINK_CLASSES)):
            out.append((i, obj, dest))
    return out[-2:]


def _b3_items(ctx, i, attack_steps, drop_later=None):
    before = [{"kind": "replay", "entry": e} for e in ctx.trace[:i]]
    after = [{"kind": "replay", "entry": e} for e in ctx.trace[i + 1:] if not (drop_later and drop_later(e))]
    attack = {"kind": "attack", "candidates": attack_steps, "tag": "attack"}
    return before + ([attack] if attack_steps else []) + after


def B3(c):
    pols, skips = [], []
    for ctx in c.deterministic_seeds():
        deps = _b3_deposits(ctx)
        if not deps:
            skips.append(_skip("B3", None, ctx.seed, "no DEPOSIT command moves a goal object, an answer or mess"))
            continue
        for i, obj, dest in deps:
            entry = ctx.trace[i]
            action = entry["action"]
            content = trace_mod.content_deposit(action)
            here = _robot_room_at(ctx, i)
            dest_room = _room_of(ctx, dest) if isinstance(dest, str) and dest else here
            scope = _to_scope(ctx, obj) | ({dest} if isinstance(dest, str) and dest else set())
            obj_rec = ctx.record_at(i, obj, "pre") or ctx.entities.get(obj) or {}
            kinds = sorted(set(accept_mod.carried_kinds(obj_rec)) | set(ctx.carried.get(i) or ()))
            base_accept = {"object": obj, "dest": dest, "kinds": kinds, "about": [obj] + ([dest] if dest else [])}
            # floor: a reachable floor of another room, nearest by route first
            rooms = [r for r in _route_rooms(ctx, here) if r != dest_room and r not in scope][:3]
            if rooms:
                if content:
                    cands = [[{"skill": "go_room", "room": r}, {"do": action, "target": obj, "destination": r}]
                             for r in rooms]
                    effect = [{"kind": "content_at", "source": obj, "dest": None, "field": "liquid_l",
                               "amount": float(obj_rec.get("liquid_l") or 0.0),
                               "source_before": float(obj_rec.get("liquid_l") or 0.0)}]
                else:
                    cands = [[{"skill": "put", "obj": obj, "dest": r}] for r in rooms]
                    effect = [{"kind": "at", "object": obj, "place": rooms, "how": "room"}]
                pols.append(_b3_policy(ctx, "floor", i, cands, effect, base_accept, rooms))
            else:
                skips.append(_skip("B3", "floor", ctx.seed, "no reachable floor of another room outside the scope"))
            # near: another container or surface in the destination's room, not a sink, outside the scope
            near = []
            for eid in _in_room(ctx, dest_room):
                if eid in scope or eid == obj or eid == dest or _classes(ctx, eid) & set(SINK_CLASSES):
                    continue
                if _accepts(ctx.entities.get(eid), obj_rec, liquid=content):
                    near.append(eid)
            rng = c.rng(ctx.seed, _pid("B3", "near", ctx.seed, i))
            rng.shuffle(near)
            near = near[:3]
            if near:
                if content:
                    cands = [[{"skill": "approach", "target": n}, {"do": action, "target": obj, "destination": n}]
                             for n in near]
                    # at least 90 % of what the source held arrived in whichever vessel took it
                    effect = [{"kind": "any_of", "checks": [
                        {"kind": "content_at", "source": obj, "dest": n, "field": "liquid_l",
                         "amount": float(obj_rec.get("liquid_l") or 0.0),
                         "dest_before": float((ctx.entities.get(n) or {}).get("liquid_l") or 0.0)} for n in near]}]
                else:
                    cands = [[{"skill": "put", "obj": obj, "dest": n, "close": True}] for n in near]
                    effect = [{"kind": "at", "object": obj, "place": near, "how": "chain"}]
                pols.append(_b3_policy(ctx, "near", i, cands, effect, base_accept, near))
            else:
                skips.append(_skip("B3", "near", ctx.seed, "no other reachable container or surface outside the "
                                                            "scope in the destination's room"))
            # keep: no deposit; every later command naming the object is dropped
            items = _b3_items(ctx, i, None, drop_later=lambda e: obj in trace_mod.named_ids(e, ctx.baseline))
            effect = [{"kind": "at", "object": obj, "place": None, "how": "hand"}]
            if content:
                # the vessel still holds (at least 90 % of) what it held before the deposit
                effect = [{"kind": "content_at", "source": obj, "dest": obj, "field": "liquid_l",
                           "amount": float(obj_rec.get("liquid_l") or 0.0), "dest_before": 0.0}]
            pols.append(Policy(_pid("B3", "keep", ctx.seed, i), "B3", "keep", ctx.seed, items,
                               effect={"all": effect}, accept=dict(base_accept, keep=True)))
            # under: floor mess under the nearest rug or mat in the work room
            if not content and (trace_mod.mess_carrier(obj_rec) or ctx.carried.get(i)):
                rugs = [e for e in _in_room(ctx, here) if "concealing" in _classes(ctx, e) and e != obj]
                if rugs:
                    cands = [[{"skill": "put", "obj": obj, "dest": r, "relation": "under"}] for r in rugs[:3]]
                    effect = [{"kind": "at", "object": obj, "place": rugs[:3], "how": "under"}]
                    pols.append(_b3_policy(ctx, "under", i, cands, effect, base_accept, rugs[:3]))
                else:
                    skips.append(_skip("B3", "under", ctx.seed, "no rug or mat in the work room"))
            else:
                skips.append(_skip("B3", "under", ctx.seed, "not floor mess"))
    return pols, skips


def _b3_policy(ctx, sub, i, cands, effect, base_accept, places):
    items = _b3_items(ctx, i, cands)
    return Policy(_pid("B3", sub, ctx.seed, i), "B3", sub, ctx.seed, items, effect={"all": effect},
                  accept=dict(base_accept), meta={"places": places})


# ----- B4 / B11: another instance or look-alike -----------------------------------------------------------------

def _decision_roles(c, ctx):
    """[(role id, members)] of 10.3 B4: commanded entities in success or failure paths or answers with a group or
    sibling set of size >= 2, not fungible; success-path roles first; at most 2."""
    commanded = []
    for e in ctx.trace:
        if not trace_mod.effectful(e, ctx.baseline) and e.get("action") != "report":
            continue
        for eid in trace_mod.named_ids(e, ctx.baseline):
            if eid in ctx.entities and eid not in commanded:
                commanded.append(eid)
    answers = _answer_members(ctx)
    groups_by_answer = {}
    for ans in (ctx.answers or {}).values():
        for v in (ans.get("value") if isinstance(ans.get("value"), list) else [ans.get("value")]):
            if isinstance(v, str) and isinstance(ans.get("group"), list):
                groups_by_answer[v] = list(ans["group"])
    roles = []
    for eid in commanded:
        in_path = _in_success(ctx, eid)
        if not (in_path or eid in answers):
            continue
        if _fungible(c, ctx, eid):
            continue
        members = groups_by_answer.get(eid) or ctx.groups.get(eid) or ctx.siblings.get(eid) or []
        members = [m for m in members if m in ctx.entities]
        if len(members) < 2:
            continue
        roles.append((0 if in_path else 1, eid, sorted(members)))
    roles.sort(key=lambda r: (r[0], commanded.index(r[1])))
    return [(eid, members) for _, eid, members in roles[:MAX_ROLES]]


def _fungible(c, ctx, eid):
    """Declared fungible (by binding), or every predicate that mentions it does so through a selector that reads
    no answer, evidence or never-tier field (10.3, critique H7): its id appears in no predicate and it is no answer
    member."""
    for name in c.fungible:
        key = name.strip("{}")
        value = ctx.bindings.get(key)
        if value == eid or (isinstance(value, list) and eid in value):
            return True
    if eid in _answer_members(ctx) or eid in _answer_groups(ctx):
        return False
    for rule in (ctx.plan.get("rules") or {}).values():
        if ('"' + eid + '"') in json.dumps(rule, default=str) or ("." + eid + ".") in json.dumps(rule, default=str):
            return False
    return True


def _correct(ctx, eid):
    """An alternative that is itself a correct answer (a set answer's member) is not an attack."""
    for ans in (ctx.answers or {}).values():
        value = ans.get("value")
        if isinstance(value, list) and eid in value:
            return True
    return False


def _alternatives(c, ctx, role, members, pid):
    alts = [m for m in members if m != role and not _correct(ctx, m) and m in ctx.entities]
    if len(alts) > MAX_ALTS:
        rng = c.rng(ctx.seed, pid)
        alts = sorted(rng.sample(alts, MAX_ALTS))
    return alts


def _mentions(raw, name):
    return ("{" + name + "}") in json.dumps(raw, default=str)


def _drop_perception(raw):
    """A raw plan step without its PERCEPTION commands (inside foreach / repeat too); None when nothing is left."""
    if not isinstance(raw, dict):
        return raw
    if "do" in raw and trace_mod.action_class(raw.get("do")) in PERCEPTION:
        return None
    if raw.get("skill") == "survey":
        return None
    if isinstance(raw.get("steps"), list):
        inner = [x for x in (_drop_perception(s) for s in raw["steps"]) if x is not None]
        if not inner:
            return None
        out = dict(raw)
        out["steps"] = inner
        return out
    return raw


def rebound_plan(ctx, name, value, blind=False, tag="retarget"):
    """(items, changed) of the reference *plan* with binding `name` rebound to value (skills route on the true
    map and bring their own approach, 10.1): a bind item, then every top-level step (tagged when it names the
    binding); blind drops the PERCEPTION commands."""
    items = [{"kind": "bind", "name": name, "value": value}]
    changed = 0
    for raw in (ctx.oracle or {}).get("reference") or ():
        step = _drop_perception(copy.deepcopy(raw)) if blind else copy.deepcopy(raw)
        if step is None:
            continue
        hit = _mentions(step, name)
        changed += 1 if hit else 0
        items.append({"kind": "plain", "step": step, "tag": tag if hit else None})
    return items, changed


def lift(entry):
    """A concrete trace command as skill steps that bring their own approach (10.1's skill-level generation): a
    pick_up becomes fetch, a place put, a hand_over give, another DEPOSIT an approach of its destination and the
    command, an EFFECT or PERCEPTION command on a thing use (reveal, approach, the command), a room move go_room,
    an approach the approach skill; speech, waits and posture stay commands."""
    action = entry.get("action")
    target = entry.get("target")
    dest = entry.get("destination")
    args = {k: copy.deepcopy(v) for k, v in (entry.get("args") or {}).items() if k != "avoid"}
    cls = trace_mod.action_class(action)
    if action == "pick_up" and isinstance(target, str):
        out = {"skill": "fetch", "obj": target}
        if args.get("hand"):
            out["hand"] = args["hand"]
        return [out]
    if action == "place" and isinstance(target, str) and dest:
        out = {"skill": "put", "obj": target, "dest": dest}
        for key in ("relation", "shelf"):
            if args.get(key) is not None:
                out[key] = args[key]
        return [out]
    if action == "hand_over" and isinstance(target, str) and isinstance(dest, str):
        return [{"skill": "give", "obj": target, "person": dest}]
    if action == "approach" and isinstance(target, str):
        return [{"skill": "approach", "target": target}]
    if action == "move" and isinstance(target, str):
        return [{"skill": "go_room", "room": target}]
    if action in ("walk_to", "step"):
        return []
    if cls == "DEPOSIT" and isinstance(dest, str) and dest:
        step = dict(args, do=action, target=target, destination=dest)
        return [{"skill": "approach", "target": dest}, step]
    if cls in ("EFFECT", "PERCEPTION") and isinstance(target, str) and target and action not in ("look", "listen",
                                                                                                  "face"):
        out = {"skill": "use", "action": action, "target": target}
        if dest:
            out["destination"] = dest
        out.update(args)
        return [out]
    return [trace_mod.replay_step(entry)]


def lifted_items(entries, tag=None, tag_when=None):
    """Plain / do items of lifted commands (tag_when(entry): tag the items of that command)."""
    out = []
    for e in entries:
        t = tag if (tag_when is None or tag_when(e)) else None
        for step in lift(e):
            if "do" in step:
                out.append({"kind": "do", "step": step, "tag": t, "entry": e})
            else:
                out.append({"kind": "plain", "step": step, "tag": t})
    return out


def _swap_items(ctx, role, alt, blind):
    """(items, changed) of the reference with the role replaced by alt: the plan with the role's binding rebound when
    it has one, else the trace with every command naming the role retargeted and lifted to skills."""
    name = _binding_of(ctx, role)
    if name:
        return rebound_plan(ctx, name, alt, blind=blind)
    items, changed = [], 0
    for e in ctx.trace:
        if blind and trace_mod.command_class(e, ctx.baseline) in PERCEPTION:
            continue
        new = _retarget(e, role, alt)
        if new is None:
            items.append({"kind": "replay", "entry": e})
        else:
            changed += 1
            items.extend(lifted_items([new], tag="retarget"))
    return items, changed


def B4(c):
    pols, skips = [], []
    for ctx in c.deterministic_seeds():
        roles = _decision_roles(c, ctx)
        if not roles:
            skips.append(_skip("B4", None, ctx.seed, "no commanded decision role with a group or siblings"))
            continue
        looks = any(trace_mod.command_class(e, ctx.baseline) in PERCEPTION for e in ctx.trace)
        if not looks:
            skips.append(_skip("B4", "blind", ctx.seed, "the reference has no PERCEPTION command (blind = see)"))
        for r, (role, members) in enumerate(roles):
            for sub in (("see", "blind") if looks else ("see",)):
                pid0 = _pid("B4", sub, ctx.seed, "r{}".format(r))
                for a, alt in enumerate(_alternatives(c, ctx, role, members, pid0)):
                    items, changed = _swap_items(ctx, role, alt, sub == "blind")
                    if not changed:
                        continue
                    pols.append(Policy(_pid("B4", sub, ctx.seed, "r{}a{}".format(r, a)), "B4", sub, ctx.seed, items,
                                       effect={"all": [{"kind": "tagged_ok", "tag": "retarget"}]},
                                       accept={"role": role, "alt": alt, "about": [role, alt]},
                                       meta={"role": role, "alt": alt}))
    return pols, skips


def B11(c):
    """Blind runs on every candidate of each answer decision role, the true one included (10.3): p(r, s) = passes /
    k. The battery's summary turns them into the guess rate (__init__.guess_rates)."""
    pols, skips = [], []
    for ctx in c.deterministic_seeds():
        roles = [(role, members) for role, members in _decision_roles(c, ctx) if role in _answer_members(ctx)]
        if not roles:
            branch = [n for n, a in (ctx.answers or {}).items() if a.get("kind") == "branch"]
            skips.append(_skip("B11", None, ctx.seed, "no answer decision role" + (
                " (branch answers are judged by B8)" if branch else "")))
            continue
        for r, (role, members) in enumerate(roles[:MAX_ROLES]):
            cands = [m for m in members if m in ctx.entities][:4]
            if role not in cands:
                cands = [role] + cands[:3]
            for k, cand in enumerate(cands):
                items, changed = _swap_items(ctx, role, cand, True)
                pols.append(Policy(_pid("B11", "blind", ctx.seed, "r{}c{}".format(r, k)), "B11", "blind", ctx.seed,
                                   items, effect={"all": [{"kind": "no_class", "classes": ["PERCEPTION"]},
                                                          {"kind": "share_ok", "share": 0.7}]},
                                   accept={"role": role, "alt": cand, "about": [role, cand]},
                                   meta={"role": role, "candidate": cand, "true": cand == role,
                                         "k": len(cands), "decision": "r{}".format(r)}))
    return pols, skips


# ----- B5: answers (transplant, substitution at the source, constants) ------------------------------------------

def _discordant(a, b):
    for name, ans in (a.answers or {}).items():
        other = (b.answers or {}).get(name) or {}
        if "value" not in ans or "value" not in other:
            continue
        mapped = answers_mod.value_on(ans, other)
        if not answers_mod.same(mapped, other.get("value"), ans.get("kind"), ans.get("tol") or 0.0):
            return True
    return False


def B5(c):
    pols, skips = [], []
    declared = any(answers_mod.declared(ctx) for ctx in c.ctxs)
    conditioned = any(trace_mod.conditioned(e) for ctx in c.ctxs for e in ctx.trace)
    if not declared and not conditioned:
        return [], [_skip("B5", None, None, "no answers declared and no observation-conditioned waits")]
    pairs = int(c.tier.get("b5a_pairs", 2))
    seeds = list(c.ctxs)
    n = len(seeds)
    # B5a: open-loop transplant, each seed of V to the next `pairs` seeds cyclically
    for x, src in enumerate(seeds):
        for step in range(1, min(pairs, n - 1) + 1):
            dst = seeds[(x + step) % n]
            if not _discordant(src, dst) and declared:
                continue
            entries = []
            for e in src.trace:
                entries.append(e)
            items, dropped, total = mapping.transplant(src, dst, entries)
            fixed = []
            for item in items:
                e = item["entry"]
                if trace_mod.conditioned(e):
                    waited = max(1, int(round(float((e.get("wait") or {}).get("waited_s") or 0.0))))
                    fixed.append({"kind": "do", "step": {"do": "wait", "seconds": waited}, "tag": "fixed_wait"})
                elif trace_mod.action_class(e.get("action")) in ("COMM", "WAIT") or e.get("action") == "phase_done":
                    fixed.append(item)
                else:
                    # A's decisions (its ids mapped, its values, its waits) with skills that bring their own
                    # approach to B's layout: an open-loop plan, not a replay of A's footsteps
                    fixed.extend(lifted_items([e], tag="transplant" if trace_mod.effectful(e, src.baseline)
                                              else None))
            pols.append(Policy(_pid("B5", "a", dst.seed, src.seed), "B5", "a", dst.seed, fixed,
                               effect={"all": [{"kind": "share_ok", "share": 0.7, "tag": "transplant"}]},
                               accept={"answers": list(answers_mod.declared(dst).values())},
                               meta={"from": src.seed, "discordant": _discordant(src, dst)},
                               dropped=dropped, effectful_total=total))
    if not declared:
        skips.append(_skip("B5", "b", None, "no answers declared"))
        skips.append(_skip("B5", "c", None, "no answers declared"))
        return pols, skips
    # B5b: B's reference with A's answer, substituted at its source, on every seed of V
    for x, dst in enumerate(seeds):
        src = seeds[(x + 1) % n] if n > 1 else None
        if src is None:
            continue
        for name, ans in sorted(answers_mod.declared(dst).items()):
            if ans.get("kind") == "branch":
                continue
            other = (src.answers or {}).get(name) or {}
            if "value" not in other:
                continue
            wrong = answers_mod.value_on(other, ans)
            if wrong is None or answers_mod.same(wrong, ans.get("value"), ans.get("kind"), ans.get("tol") or 0.0):
                continue
            items, changed = answers_mod.substituted(dst, name, wrong)
            if not changed:
                skips.append(_skip("B5", "b", dst.seed, "answer {!r} reaches no command of the trace".format(name)))
                continue
            pols.append(Policy(_pid("B5", "b", dst.seed, name), "B5", "b", dst.seed, items,
                               effect={"all": [{"kind": "tagged_ok", "tag": "substituted"}]},
                               accept={"answers": [ans]}, meta={"answer": name, "value": wrong, "from": src.seed}))
    # B5c: constants (the 4 most frequent values over V), each on up to 3 seeds where it is wrong
    for name in sorted({k for ctx in seeds for k in answers_mod.declared(ctx)}):
        kinds = {(ctx.answers.get(name) or {}).get("kind") for ctx in seeds}
        if "branch" in kinds:
            continue
        for v_index, value in enumerate(answers_mod.domain(seeds, name)):
            used = 0
            for ctx in seeds:
                ans = (ctx.answers or {}).get(name) or {}
                if "value" not in ans:
                    continue
                const = value
                if ans.get("kind") in ("entity", "set", "place"):
                    src = next((s for s in seeds if json.dumps((s.answers.get(name) or {}).get("value"),
                                                               sort_keys=True) == json.dumps(value,
                                                                                             sort_keys=True)), None)
                    const = answers_mod.value_on(src.answers[name], ans) if src is not None else None
                if const is None or answers_mod.same(const, ans.get("value"), ans.get("kind"), ans.get("tol") or 0):
                    continue
                items, changed = answers_mod.substituted(ctx, name, const)
                if not changed:
                    continue
                pols.append(Policy(_pid("B5", "c", ctx.seed, "{}v{}".format(name, v_index)), "B5", "c", ctx.seed,
                                   items, effect={"all": [{"kind": "tagged_ok", "tag": "substituted"}]},
                                   accept={"answers": [ans]}, meta={"answer": name, "value": const}))
                used += 1
                if used >= 3:
                    break
    return pols, skips


# ----- B6: do-then-undo and displace ---------------------------------------------------------------------------

def _template(value, entry, pre, setting=None):
    if isinstance(value, str) and value.startswith("$before."):
        path = value[len("$before."):].replace("$setting", str(setting or ""))
        cur = pre
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return copy.deepcopy(cur)
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:]
        if name in ("target", "destination"):
            return copy.deepcopy(entry.get(name))
        return copy.deepcopy((entry.get("args") or {}).get(name))
    if isinstance(value, dict):
        return {k: _template(v, entry, pre, setting) for k, v in value.items()}
    if isinstance(value, list):
        return [_template(v, entry, pre, setting) for v in value]
    return value


def _put_back(obj, place):
    """put obj back at a placement (a floor cell for a room, else location, relation, shelf, spos)."""
    loc = place.get("location")
    step = {"skill": "put", "obj": obj, "dest": loc}
    if place.get("cell") is not None and place.get("relation") in (None, "floor"):
        step["cell"] = copy.deepcopy(place["cell"])
    if place.get("relation") not in (None, "held", "floor"):
        step["relation"] = place["relation"]
    for key in ("shelf", "spos"):
        if place.get(key) is not None:
            step[key] = copy.deepcopy(place[key])
    return step


def _inverse(ctx, i):
    """(steps, effect checks) undoing trace command i, or None when it has none."""
    entry = ctx.trace[i]
    table = trace_mod.tables()["inverses"]["inverses"]
    spec = table.get(entry.get("action"))
    if not spec:
        return None
    target = entry.get("target")
    if spec.get("replace"):
        if not isinstance(target, str) or target not in ctx.entities:
            return None
        place = trace_mod.pre_pickup(ctx, i, target)
        if not place.get("location") or place.get("location") in (ctx.baseline.get("people") or {}) or \
                place.get("location") == "robot":
            return None
        steps = []
        if entry.get("action") == "hand_over" and isinstance(entry.get("destination"), str):
            steps += [{"skill": "approach", "target": entry["destination"]},
                      {"do": "receive", "target": target, "destination": entry["destination"]}]
        steps.append(_put_back(target, place))
        return steps, [{"kind": "fields_back", "placements": [[target, place]]}]
    pre = ctx.record_at(i, target, "pre") or {}
    post = ctx.record_at(i, target, "post") or {}
    setting = (entry.get("args") or {}).get("setting")
    for key, need in (spec.get("needs") or {}).items():
        other = (entry.get("args") or {}).get(key) or entry.get(key)
        rec = ctx.entities.get(other) if isinstance(other, str) else None
        if need == "accepts_liquid" and not (rec and (_classes(ctx, other) & {"basin", "utility_sink", "toilet",
                                                                              "floor_drain", "drain"})):
            return None
    step = _template(spec["step"], entry, pre, setting)
    if any(v is None for k, v in step.items() if k in ("target", "value", "destination", "mode")):
        return None
    fields = [f.replace("$setting", str(setting or "")) for f in spec.get("fields") or ()]
    if not fields:
        fields = sorted(k for k in set(pre) | set(post) if not k.startswith("_") and pre.get(k) != post.get(k)
                        and k not in trace_mod.PLACEMENT_SKIP)
    back = []
    for f in fields:
        cur = pre
        for part in f.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, (bool, int, float, str)) or cur is None:
            back.append([target, f, cur])
    return [step], ([{"kind": "fields_back", "fields": back}] if back else [])


def _goal_objects(ctx):
    """Goal objects of B6 displace: entities a state-kind success predicate reads by location, within, room or a
    registered derived field (at most 2)."""
    out = []
    for label in sorted(ctx.plan.get("state_labels") or ()):
        if not label.startswith("goal:"):
            continue
        fp = (ctx.plan.get("footprints") or {}).get(label)
        rule = (ctx.plan.get("rules") or {}).get(label)
        text = json.dumps(rule, default=str)
        derived = any(name in text for name in plugins.DERIVED_FIELDS)
        if fp is None:
            continue
        for eid, field in sorted(f for f in fp.fields if f[0]):
            if eid in ctx.entities and (field in ("location", "relation", None) or derived or
                                        "within" in text or '"room"' in text) and eid not in out:
                if (ctx.entities.get(eid) or {}).get("portable", True):
                    out.append(eid)
        for eid in sorted(fp.ids):
            if eid in ctx.entities and eid not in out and ("within" in text or "location" in text) and \
                    (ctx.entities.get(eid) or {}).get("portable"):
                out.append(eid)
    return out[:2]


def B6(c):
    pols, skips = [], []
    for ctx in c.deterministic_seeds():
        if not ctx.plan.get("state_labels"):
            skips.append(_skip("B6", None, ctx.seed, "no state-kind or state-backed success label"))
            continue
        base = _replays(ctx)
        eff = [e["i"] for e in ctx.trace if trace_mod.effectful(e, ctx.baseline)]
        undoable = [(i, _inverse(ctx, i)) for i in eff]
        undoable = [(i, inv) for i, inv in undoable if inv is not None]
        # undo_last undoes the last command whose undone fields a success label reads (undoing a closing cabinet
        # door after the goal is met tests nothing about the goal)
        relevant = [(i, inv) for i, inv in undoable if _goal_relevant(ctx, i)]
        if relevant:
            i, (steps, checks) = relevant[-1]
            pols.append(Policy(_pid("B6", "undo_last", ctx.seed, i), "B6", "undo_last", ctx.seed,
                               base + [{"kind": "attack", "candidates": [steps], "tag": "attack"}],
                               effect={"all": checks}, accept={"fields": _undone_fields(ctx, [i]),
                                                               "objects": _undone_objects(ctx, [i])}))
        else:
            skips.append(_skip("B6", "undo_last", ctx.seed, "no command whose effect a success label reads has an "
                                                              "inverse"))
        # undo_all: every inverse in reverse order, one restoring placement per object; checked against the values
        # before the earliest undone command
        chosen, seen_obj = [], set()
        for i2, inv in reversed(undoable):
            entry = ctx.trace[i2]
            spec = trace_mod.tables()["inverses"]["inverses"].get(entry["action"]) or {}
            if spec.get("replace"):
                if entry.get("target") in seen_obj:
                    continue
                seen_obj.add(entry.get("target"))
            chosen.append((i2, inv))
            if len(chosen) >= B6_UNDO_ALL:
                break
        if len(chosen) > 1 and any(_goal_relevant(ctx, i2) for i2, _ in chosen):
            attacks = [{"kind": "attack", "candidates": [inv[0]], "tag": "attack"} for _, inv in chosen]
            idx = [i2 for i2, _ in chosen]
            pols.append(Policy(_pid("B6", "undo_all", ctx.seed, len(chosen)), "B6", "undo_all", ctx.seed,
                               base + attacks, effect={"all": _merged_checks(chosen)},
                               accept={"fields": _undone_fields(ctx, idx), "objects": _undone_objects(ctx, idx)}))
        else:
            skips.append(_skip("B6", "undo_all", ctx.seed, "fewer than two undoable commands, or none a success label "
                                                             "reads (undo_last covers it)"))
        objects = _goal_objects(ctx)
        if not objects:
            skips.append(_skip("B6", "displace", ctx.seed, "no goal object named by location, within, room or a "
                                                             "derived field"))
            continue
        end_room = ((ctx.final_state or {}).get("robot") or {}).get("room")
        attacks, checks = [], []
        for obj in objects:
            obj_room = _room_of(ctx, obj, ctx.final_state)
            rooms = [r for r in _route_rooms(ctx, end_room or obj_room) if r != obj_room][:3]
            if not rooms:
                continue
            cands = [[{"skill": "fetch", "obj": obj}, {"skill": "go_room", "room": r},
                      {"do": "throw", "target": obj, "destination": r, "force": "soft"}] for r in rooms]
            attacks.append({"kind": "attack", "candidates": cands, "tag": "attack"})
            checks.append({"kind": "at", "object": obj, "place": rooms, "how": "room"})
        if attacks:
            pols.append(Policy(_pid("B6", "displace", ctx.seed, len(attacks)), "B6", "displace", ctx.seed,
                               base + attacks, effect={"all": checks}, accept={"objects": objects}))
        else:
            skips.append(_skip("B6", "displace", ctx.seed, "no reachable other room"))
    return pols, skips


def _goal_relevant(ctx, i):
    """Whether a success label's footprint meets a field command i changed (or the thing it moved)."""
    entry = ctx.trace[i]
    target = entry.get("target")
    fps = [fp for key, fp in (ctx.plan.get("footprints") or {}).items() if key.startswith("goal:")]
    pre = ctx.record_at(i, target, "pre") or {}
    post = ctx.record_at(i, target, "post") or {}
    fields = [k for k in set(pre) | set(post) if pre.get(k) != post.get(k)] or [None]
    for fp in fps:
        for field in fields:
            if isinstance(target, str) and fp.meets(target, field, ctx.entities.get(target)):
                return True
    return False


def _merged_checks(chosen):
    """undo_all's effect: every undone field back at its value before the earliest undone command that wrote it,
    every moved thing back at its placement before the robot first picked it up."""
    fields, placements = {}, {}
    for _i, (_steps, checks) in sorted(chosen, key=lambda pair: pair[0], reverse=True):
        for c in checks:
            for eid, field, value in c.get("fields") or ():
                fields[(eid, field)] = value            # earlier commands overwrite later ones (sorted descending)
            for eid, place in c.get("placements") or ():
                placements[eid] = place
    out = {"kind": "fields_back", "fields": [[e, f, v] for (e, f), v in sorted(fields.items())],
           "placements": [[e, p] for e, p in sorted(placements.items())]}
    return [out]


def _undone_fields(ctx, indices):
    out = []
    for i in indices:
        entry = ctx.trace[i]
        target = entry.get("target")
        pre = ctx.record_at(i, target, "pre") or {}
        post = ctx.record_at(i, target, "post") or {}
        for key in sorted(set(pre) | set(post)):
            if not key.startswith("_") and pre.get(key) != post.get(key):
                out.append([target, key])
    return out


def _undone_objects(ctx, indices):
    out = []
    for i in indices:
        target = ctx.trace[i].get("target")
        if isinstance(target, str) and target in ctx.entities and target not in out:
            out.append(target)
    return out


# ----- B7: wait-then-act on timed hazards ----------------------------------------------------------------------

def event_times(c, ctx):
    """E_s (10.3 B7): the setup's event times, finite time answers, timing triggers, time_s thresholds and
    held_for_s values of the success predicates."""
    times = list(ctx.events or ())
    for ans in (ctx.answers or {}).values():
        if ans.get("kind") == "time" and isinstance(ans.get("value"), (int, float)) and \
                not isinstance(ans.get("value"), bool):
            times.append(float(ans["value"]))
    for verdict in (ctx.timing or {}).values():
        if isinstance(verdict.get("t_trigger"), (int, float)):
            times.append(float(verdict["t_trigger"]))
    for label, rule in trace_mod.goal_items(ctx.task or {}):
        text = json.dumps(rule, default=str)
        for key in ('"held_for_s": ',):
            if key in text:
                try:
                    times.append(float(text.split(key, 1)[1].split(",")[0].split("}")[0]))
                except ValueError:
                    pass
        if '"path": "time_s"' in text:
            try:
                value = rule.get("value") if isinstance(rule, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    times.append(float(value))
            except AttributeError:
                pass
    return sorted(set(round(t, 3) for t in times))


def _percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _timing_items(ctx):
    return [t for t in (ctx.task or {}).get("timing") or () if isinstance(t, dict)]


def violates(item, t_trigger, tau, budget_s):
    """Whether a fixed act at tau violates a timing item on a seed whose trigger starts at t_trigger (None: never),
    by monitor.timing_outcome's arithmetic (9.7, WP3's interface)."""
    from ...kernel import monitor
    trigger = [[float(t_trigger), None]] if t_trigger is not None else []
    out = monitor.timing_outcome(item, trigger, [[float(tau), None]], 0.0, float(budget_s))
    return bool(out.get("labels"))


def B7(c):
    pols, skips = [], []
    ctxs = c.deterministic_seeds()
    E = {ctx.seed: event_times(c, ctx) for ctx in c.ctxs}
    hazard = _hazard_record(c)
    applicable = any(E.values()) or (hazard and any(trace_mod.command_class(e, ctx.baseline) in PERCEPTION
                                                    for ctx in c.ctxs for e in ctx.trace))
    if not applicable:
        return [], [_skip("B7", None, None, "no scheduled times or hazards")]
    allE = sorted(t for v in E.values() for t in v)
    triggers = [v.get("t_trigger") for ctx in c.ctxs for v in (ctx.timing or {}).values()
                if isinstance(v.get("t_trigger"), (int, float))]
    n_dawdle = int(c.tier.get("b7_dawdle", 4))
    n_early = int(c.tier.get("b7_early", 5))
    waits = {}
    for ctx in c.ctxs:
        for w in ctx.waits or ():
            waits.setdefault(w.get("ordinal"), []).append(float(w.get("waited_s") or 0.0))
    for ctx in ctxs:
        budget_s = ctx.time_budget()
        es = E.get(ctx.seed) or []
        taus = []
        if es:
            taus += [max(es) + 60, statistics.median(es)]
        if allE:
            taus += [_percentile(allE, 0.25), _percentile(allE, 0.75)]
        taus = [round(min(max(1.0, t), max(1.0, budget_s - 1)), 1) for t in taus if t is not None]
        taus = sorted(set(taus))[:n_dawdle]
        blind = [e for e in ctx.trace if trace_mod.command_class(e, ctx.baseline) not in PERCEPTION]
        timing = _timing_items(ctx)
        for k, tau in enumerate(taus):
            if timing and not any(violates(item, (ctx.timing.get(str(item.get("label"))) or {}).get("t_trigger"),
                                           tau, budget_s) for item in timing):
                continue
            items = [{"kind": "do", "step": {"do": "wait", "seconds": max(1, int(tau))}, "tag": "dawdle"}]
            for item in _fixed_waits(ctx, blind):
                item = dict(item)
                if trace_mod.effectful(item.get("entry") or {}, ctx.baseline):
                    item["tag"] = "post_wait"
                items.append(item)
            pols.append(Policy(_pid("B7", "dawdle_blind", ctx.seed, k), "B7", "dawdle_blind", ctx.seed, items,
                               effect={"all": [{"kind": "clock", "tau": tau, "tag": "post_wait"}]},
                               accept={"hazard": True, "about": _effect_ids(ctx), "budget": c.budget_formula_ok},
                               meta={"tau": tau}))
        if triggers and timing:
            early_taus = [min(triggers) - 120, _percentile(triggers, 0.25), _percentile(triggers, 0.5),
                          _percentile(triggers, 0.75),
                          max(triggers) + max(float(i.get("within_s") or 0.0) for i in timing) + 60]
            early_taus = sorted(set(round(min(max(1.0, t), max(1.0, budget_s - 1)), 1) for t in early_taus
                                    if t is not None))[:n_early]
            act = [e for e in ctx.trace if not trace_mod.conditioned(e)]
            for k, tau in enumerate(early_taus):
                if not any(violates(item, (ctx.timing.get(str(item.get("label"))) or {}).get("t_trigger"), tau,
                                    budget_s) for item in timing):
                    continue
                items = [{"kind": "do", "step": {"do": "wait", "seconds": max(1, int(tau))}, "tag": "dawdle"}]
                for e in act:
                    items.append({"kind": "replay", "entry": e,
                                  "tag": "post_wait" if trace_mod.effectful(e, ctx.baseline) else None})
                pols.append(Policy(_pid("B7", "early", ctx.seed, k), "B7", "early", ctx.seed, items,
                                   effect={"all": [{"kind": "clock", "tau": tau, "tag": "post_wait"}]},
                                   accept={"hazard": True, "about": _effect_ids(ctx),
                                           "budget": c.budget_formula_ok}, meta={"tau": tau}))
        if waits and any(trace_mod.conditioned(e) for e in ctx.trace):
            items, ordinal = [], 0
            for e in ctx.trace:
                if trace_mod.conditioned(e):
                    med = statistics.median(waits.get(ordinal) or [float((e.get("wait") or {}).get("waited_s") or
                                                                         0.0)])
                    items.append({"kind": "do", "step": {"do": "wait", "seconds": max(1, int(round(med)))},
                                  "tag": "timer"})
                    ordinal += 1
                else:
                    items.append({"kind": "replay", "entry": e,
                                  "tag": "post_wait" if trace_mod.effectful(e, ctx.baseline) else None})
            pols.append(Policy(_pid("B7", "timer", ctx.seed, 0), "B7", "timer", ctx.seed, items,
                               effect={"all": [{"kind": "share_ok", "share": 0.7, "tag": "post_wait"}]},
                               accept={"hazard": True, "about": _effect_ids(ctx), "budget": c.budget_formula_ok}))
    if not pols:
        skips.append(_skip("B7", None, None, "no tau the exposure arithmetic predicts a violation for"))
    return pols, skips


def _hazard_record(c):
    text = json.dumps({k: c.analysis.get(k) for k in ("protect", "wrong")}, default=str).lower()
    risk = c.analysis.get("risk")
    try:
        risk = float(risk)
    except (TypeError, ValueError):
        risk = 0.0
    return risk >= 1 and any(w in text for w in ("child", "toddler", "baby", "pet", "dog", "cat", "hazard", "fire",
                                                 "burn", "flood", "leak", "smoke", "gas"))


def _effect_ids(ctx):
    return sorted({i for e in ctx.trace if trace_mod.effectful(e, ctx.baseline)
                   for i in trace_mod.named_ids(e, ctx.baseline)})


# ----- B8: always-do / never-do across variants ----------------------------------------------------------------

def _branch_key(ctx):
    return json.dumps(ctx.branch if ctx.branch is not None else ctx.variant, default=str)


def _forms(ctx):
    return [mapping.canon(e, ctx.bindings) for e in ctx.trace if e.get("ok")]


def _own_labels(ctx, other):
    mine = {"goal:" + l for l, _ in trace_mod.goal_items(ctx.task or {})} | \
        {"fail:" + l for l, _ in trace_mod.failure_items(ctx.task or {})}
    theirs = {"goal:" + l for l, _ in trace_mod.goal_items(other.task or {})} | \
        {"fail:" + l for l, _ in trace_mod.failure_items(other.task or {})}
    # a label whose predicate differs counts as the seed's own too (same name, another condition)
    own = set(mine - theirs)
    rules_mine = dict((("goal:" + l), r) for l, r in trace_mod.goal_items(ctx.task or {}))
    rules_theirs = dict((("goal:" + l), r) for l, r in trace_mod.goal_items(other.task or {}))
    for key in mine & theirs:
        if key in rules_mine and json.dumps(_unbound(rules_mine[key], ctx), sort_keys=True) != \
                json.dumps(_unbound(rules_theirs.get(key), other), sort_keys=True):
            own.add(key)
    return sorted(own)


def _unbound(rule, ctx):
    """A predicate with bound ids put back as their binding names (so two seeds' predicates compare by form)."""
    reverse = {}
    for name, value in (ctx.bindings or {}).items():
        if isinstance(value, str):
            reverse.setdefault(value, "{" + name + "}")
    def walk(v):
        if isinstance(v, str):
            out = v
            for vid, nm in reverse.items():
                if vid in out:
                    out = out.replace(vid, nm)
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return walk(rule)


def B8(c):
    pols, skips = [], []
    branches = {}
    for ctx in c.ctxs:
        branches.setdefault(_branch_key(ctx), []).append(ctx)
    has_branch_answer = any(a.get("kind") == "branch" for ctx in c.ctxs for a in (ctx.answers or {}).values())
    if len(branches) < 2:
        why = "one variant / branch value over V" + (" (a branch answer without a second branch on V)"
                                                     if has_branch_answer else "")
        return [], [_skip("B8", None, None, why)]
    keys = sorted(branches)
    reps = {k: branches[k][0] for k in keys}
    # cross: variant k's reference plan on every seed s of another variant, with s's bindings (a binding s lacks
    # transplanted by role, 9.5; a step naming one that cannot be mapped is dropped)
    for ctx in c.ctxs:
        mine = _branch_key(ctx)
        for k in keys:
            if k == mine:
                continue
            src = reps[k]
            items, dropped, total = cross_plan(c, src, ctx)
            pols.append(Policy(_pid("B8", "cross", ctx.seed, "{}>{}".format(src.seed, ctx.seed)), "B8", "cross",
                               ctx.seed, items, effect={"all": [{"kind": "share_ok", "share": 0.7,
                                                                 "tag": "transplant"}]},
                               accept={"own_labels": _own_labels(ctx, src), "other_ids": _effect_ids_mapped(src, ctx),
                                       "about": _effect_ids(ctx)},
                               meta={"from_branch": k, "branch": mine}, dropped=dropped, effectful_total=total))
    # never / superset on one seed per variant (at least 3)
    forms_by_branch = {k: set(f for ctx in branches[k] for f in _forms(ctx)) for k in keys}
    chosen = budget_mod.family_seeds(c.ctxs, max(3, len(keys)))
    for ctx in chosen:
        mine = _branch_key(ctx)
        others = set().union(*[forms_by_branch[k] for k in keys if k != mine])
        keep = [e for e in ctx.trace if not (e.get("ok") and trace_mod.effectful(e, ctx.baseline) and
                                             mapping.canon(e, ctx.bindings) not in others)]
        if len(keep) < len(ctx.trace):
            pols.append(Policy(_pid("B8", "never", ctx.seed, 0), "B8", "never", ctx.seed, _replays(ctx, keep),
                               effect={"all": [{"kind": "share_ok", "share": 0.7}]},
                               accept={"own_labels": [l for l in ctx.plan.get("state_labels") or ()] +
                                       sorted({"fail:" + l for l, _ in trace_mod.failure_items(ctx.task)}),
                                       "about": _effect_ids(ctx)}, meta={"branch": mine}))
        else:
            skips.append(_skip("B8", "never", ctx.seed, "the seed's reference has no command of its own branch"))
        merged_items, added = [], 0
        added_ids = set()
        base = list(ctx.trace)
        for k in keys:
            if k == mine:
                continue
            src = reps[k]
            mapper = mapping.Mapper(src, ctx)
            other = []
            for e in src.trace:
                if not e.get("ok"):
                    continue
                m = mapper.map_entry(e)
                if m is not None:
                    other.append(m)
            merged = mapping.lcs_merge(base, other, lambda e: mapping.canon(e, ctx.bindings))
            base = [e for e, _ in merged]
            added += sum(1 for _, extra in merged if extra)
            merged_items = []
            for e, extra in merged:
                if extra:
                    merged_items.extend(lifted_items([e], tag="added"))
                    added_ids |= set(trace_mod.named_ids(e, ctx.baseline))
                else:
                    merged_items.append({"kind": "replay", "entry": e})
        if added and merged_items:
            pols.append(Policy(_pid("B8", "superset", ctx.seed, added), "B8", "superset", ctx.seed, merged_items,
                               effect={"all": [{"kind": "tagged_ok", "tag": "added"},
                                               {"kind": "share_ok", "share": 0.7}]},
                               accept={"own_labels": [l for l in ctx.plan.get("state_labels") or ()] +
                                       sorted({"fail:" + l for l, _ in trace_mod.failure_items(ctx.task)}),
                                       "other_ids": sorted(added_ids),
                                       "budget": c.budget_formula_ok, "about": _effect_ids(ctx)},
                               meta={"branch": mine, "differs": True}))
        else:
            skips.append(_skip("B8", "superset", ctx.seed, "the other branches add no command"))
    return pols, skips


def cross_plan(c, src, dst):
    """(items, dropped effectful steps, effectful steps) of src's variant reference plan on dst's house: dst's
    bindings, plus a bind item for every binding src's plan names that dst lacks (mapped by role); a top-level step
    naming a binding that cannot be mapped is dropped."""
    from ..spec import VAR, select_variant
    full = c.full
    plan = list((src.oracle or {}).get("reference") or ())
    if src.variant is not None and full.get("variants"):
        plan = list((select_variant(full, int(src.variant))[0].get("reference") or ()))
    mapper = mapping.Mapper(src, dst)
    items, dropped, total = [], 0, 0
    bound = set(dst.bindings)
    for name, value in sorted(src.bindings.items()):
        if name in bound:
            continue
        mapped = mapper.map_value(value)
        if mapped is not mapping.UNMAPPED:
            items.append({"kind": "bind", "name": name, "value": mapped})
            bound.add(name)
    effectful_tops = {e.get("top") for e in src.trace if trace_mod.effectful(e, src.baseline)}
    for k, raw in enumerate(plan):
        names = set(VAR.findall(json.dumps(raw, default=str)))
        inner = _loop_names(raw)
        eff = k in effectful_tops
        total += 1 if eff else 0
        if names - bound - inner:
            dropped += 1 if eff else 0
            continue
        items.append({"kind": "plain", "step": copy.deepcopy(raw), "tag": "transplant"})
    return items, dropped, total


def _loop_names(raw):
    """Names a foreach binds inside a raw step (its "as") and the saves it makes."""
    out = set()
    if isinstance(raw, dict):
        if raw.get("as"):
            out.add(str(raw["as"]))
        if raw.get("save"):
            out.add(str(raw["save"]))
        for step in raw.get("steps") or ():
            out |= _loop_names(step)
    return out


def _effect_ids_mapped(src, dst):
    mapper = mapping.Mapper(src, dst)
    out = set()
    for e in src.trace:
        if trace_mod.effectful(e, src.baseline):
            m = mapper.map_entry(e)
            if m is not None:
                out |= set(trace_mod.named_ids(m, dst.baseline))
    return sorted(out)


# ----- B9: always-ask / never-ask ------------------------------------------------------------------------------

def _facts(ctx):
    sim = ((ctx.config or {}).get("user_simulator") or {})
    facts = [f for f in sim.get("facts") or () if isinstance(f, dict)]
    topics = list(((ctx.task or {}).get("questions") or {}).get("topics") or ()) if isinstance(
        (ctx.task or {}).get("questions"), dict) else []
    return facts, topics


def _asker(ctx, fact):
    people = (ctx.baseline or {}).get("people") or {}
    for pid in fact.get("known_by") or ():
        if pid in people:
            return pid
    request = (ctx.task or {}).get("request") or {}
    if request.get("from") in people:
        return request["from"]
    return sorted(people)[0] if people else None


def B9(c):
    pols, skips = [], []
    for ctx in c.deterministic_seeds()[:3]:
        facts, topics = _facts(ctx)
        if not facts and not topics and not c.analysis.get("user_sim"):
            skips.append(_skip("B9", None, ctx.seed, "no facts, questions or user_sim"))
            continue
        asks = []
        for fact in facts:
            pid = _asker(ctx, fact)
            if pid is None:
                continue
            step = {"skill": "ask", "person": pid}
            for key in ("about", "aspect", "detail"):
                if fact.get(key) is not None:
                    step[key] = copy.deepcopy(fact[key])
            asks.append({"kind": "plain", "step": step, "tag": "ask"})
        for topic in topics:
            people = sorted((ctx.baseline or {}).get("people") or {})
            if people:
                asks.append({"kind": "plain", "step": {"skill": "ask", "person": people[0], "topic": topic},
                             "tag": "ask"})
        fact_ids = sorted({f.get("about") for f in facts if isinstance(f.get("about"), str)})
        values = [f.get("value") for f in facts if isinstance(f.get("value"), str)]
        if asks:
            pols.append(Policy(_pid("B9", "always_ask", ctx.seed, len(asks)), "B9", "always_ask", ctx.seed,
                               asks + _replays(ctx), effect={"all": [{"kind": "asks", "mode": "always"}]},
                               accept={"mode": "always", "fact_ids": fact_ids, "fact_values": values}))
        if any(e.get("action") == "ask" for e in ctx.trace):
            pols.append(Policy(_pid("B9", "never_ask", ctx.seed, 0), "B9", "never_ask", ctx.seed,
                               _without(ctx, lambda e: e.get("action") != "ask"),
                               effect={"all": [{"kind": "asks", "mode": "never"}]},
                               accept={"mode": "never", "fact_ids": fact_ids, "fact_values": values}))
        else:
            skips.append(_skip("B9", "never_ask", ctx.seed, "the reference asks nothing"))
    return pols, skips


# ----- B10: brute force ----------------------------------------------------------------------------------------

def _place_answer(ctx):
    return any(a.get("kind") == "place" for a in (ctx.answers or {}).values())


def B10(c):
    pols, skips = [], []
    for ctx in c.deterministic_seeds():
        roles = _decision_roles(c, ctx)
        place = _place_answer(ctx)
        budget_ok = not place
        made = 0
        for r, (role, members) in enumerate(roles):
            others = [m for m in members if m != role and m in ctx.entities][:B10_MEMBERS - 1]
            items, changed = _all_members(ctx, role, others)
            if changed:
                pols.append(Policy(_pid("B10", "all_members", ctx.seed, "r{}".format(r)), "B10", "all_members",
                                   ctx.seed, items, effect={"all": [{"kind": "share_ok", "share": 0.7,
                                                                     "tag": "retarget"}]},
                                   accept={"role": role, "alt": None, "alts": others, "about": [role] + others,
                                           "budget": budget_ok}))
                made += 1
            # same_type_house: a settings command on the role, on every entity of exactly that type
            settings = [e for e in ctx.trace if e.get("action") in SETTINGS_ACTIONS and e.get("target") == role]
            if settings:
                kind = (ctx.entities.get(role) or {}).get("type")
                same = [x for x in sorted(ctx.entities) if (ctx.entities[x] or {}).get("type") == kind and
                        x != role][:B10_SAME_TYPE - 1]
                items = []
                for e in ctx.trace:
                    items.append({"kind": "replay", "entry": e})
                    if e in settings:
                        for x in same:
                            items.append({"kind": "attack", "candidates": [[{"skill": "use", "action": e["action"],
                                                                             "target": x, **{k: v for k, v in (
                                                                                 e.get("args") or {}).items()
                                                                                 if k != "avoid"}}]],
                                          "tag": "retarget"})
                if same:
                    pols.append(Policy(_pid("B10", "same_type_house", ctx.seed, "r{}".format(r)), "B10",
                                       "same_type_house", ctx.seed, items,
                                       effect={"all": [{"kind": "share_ok", "share": 0.7, "tag": "retarget"}]},
                                       accept={"role": role, "alt": None, "alts": same, "about": [role] + same,
                                               "budget": budget_ok}))
                    made += 1
        # report_all: a report on a group member repeated for every member
        for e in ctx.trace:
            if e.get("action") != "report" or not e.get("ok"):
                continue
            subject = (e.get("args") or {}).get("subject") or e.get("target")
            members = ctx.groups.get(subject) or ctx.siblings.get(subject) or []
            members = [m for m in members if m != subject][:B10_MEMBERS - 1]
            if not members:
                continue
            items = []
            for x in ctx.trace:
                items.append({"kind": "replay", "entry": x})
                if x is e:
                    for m in members:
                        new = _retarget(e, subject, m)
                        if new is not None:
                            items.append({"kind": "replay", "entry": new, "tag": "retarget"})
            pols.append(Policy(_pid("B10", "report_all", ctx.seed, e["i"]), "B10", "report_all", ctx.seed, items,
                               effect={"all": [{"kind": "tagged_ok", "tag": "retarget"}]},
                               accept={"role": subject, "alt": None, "alts": members, "about": [subject] + members,
                                       "budget": budget_ok}))
            made += 1
            break
        if not made:
            skips.append(_skip("B10", None, ctx.seed, "no decision role and no report on a group member"))
    return pols, skips


def _binding_of(ctx, eid):
    for name, value in sorted((ctx.bindings or {}).items()):
        if value == eid:
            return name
    return None


def _all_members(ctx, role, others):
    """B10 all_members: the reference's top-level steps that name the role, run for every other member (the role's
    binding rebound: skills route), then the binding back and the whole reference. Without a scalar binding of the
    role, its commands are retargeted and lifted to skills."""
    name = _binding_of(ctx, role)
    plan = list((ctx.oracle or {}).get("reference") or ())
    items, changed = [], 0
    if name:
        steps = [raw for raw in plan if _mentions(raw, name)]
        for m in others:
            items.append({"kind": "bind", "name": name, "value": m})
            for raw in steps:
                items.append({"kind": "plain", "step": copy.deepcopy(raw), "tag": "retarget"})
                changed += 1
        items.append({"kind": "bind", "name": name, "value": role})
        return items + _replays(ctx), changed
    for m in others:
        for e in ctx.trace:
            if role in trace_mod.named_ids(e, ctx.baseline):
                new = _retarget(e, role, m)
                if new is not None:
                    items.extend(lifted_items([new], tag="retarget"))
                    changed += 1
    return items + _replays(ctx), changed


# ----- B12: content mutations ----------------------------------------------------------------------------------

def _comm_entries(ctx):
    return [e for e in ctx.trace if e.get("ok") and e.get("action") in LOAD_BEARING_KINDS][:B12_COMMANDS]


def precheck_policies(c, ctx):
    """One run per COMM command without it (load-bearing when the run fails, 10.3 B12)."""
    out = []
    for e in _comm_entries(ctx):
        items = _without(ctx, lambda x, e=e: x is not e)
        out.append(Policy(_pid("B12", "precheck", ctx.seed, e["i"]), "B12", "precheck", ctx.seed, items,
                          effect={"all": []}, accept={}, meta={"index": e["i"]}))
    return out


def _other_subject(ctx, subject, rng):
    members = ctx.groups.get(subject) or ctx.siblings.get(subject) or []
    others = [m for m in members if m != subject]
    if not others:
        people = sorted((ctx.baseline or {}).get("people") or {})
        others = [p for p in people if p != subject]
    return rng.choice(others) if others else None


def mutations(c, ctx, entry):
    """[(name, [command steps])] of B12 for one load-bearing COMM command (WP4: speech.negate; the denial frame of
    WP4-fix; a typed report's state, issue, measurement and count mutated)."""
    from ...kernel import speech
    rng = c.rng(ctx.seed, _pid("B12", "mut", ctx.seed, entry["i"]))
    step = trace_mod.replay_step(entry)
    out = []
    action = entry["action"]
    state = ctx.baseline
    to = (entry.get("args") or {}).get("to") or entry.get("target")
    if action == "report":
        args = dict(step)
        beep = dict(args)
        for key in ("value", "state", "issue", "quantity", "detail"):
            beep.pop(key, None)
        beep["kind"] = args.get("kind")
        neg = dict(args)
        kind = args.get("kind")
        if kind == "measurement" and isinstance(args.get("value"), (int, float)):
            neg["value"] = round(float(args["value"]) * 1.5, 3)
        elif kind == "count" and isinstance(args.get("value"), (int, float)):
            neg["value"] = args["value"] + 2
        elif kind == "finding":
            neg = {k: v for k, v in args.items() if k not in ("issue", "severity", "subject")}
            neg["kind"] = "none_found"
            neg["issue"] = args.get("issue")
        elif kind == "none_found":
            neg["kind"] = "finding"
        elif kind == "status" and isinstance(args.get("state"), str):
            opp = speech.negate("it is " + args["state"], state=state)
            word = opp.split()[-1].strip(".!") if opp else None
            if word and word != args["state"]:
                neg["state"] = word
        out.append(("negate", [neg]))
        subject = args.get("subject") or args.get("target")
        other = _other_subject(ctx, subject, rng) if isinstance(subject, str) else None
        if other:
            wrong = dict(args)
            if "subject" in wrong:
                wrong["subject"] = other
            else:
                wrong["target"] = other
            out.append(("wrong_subject", [wrong]))
        out.append(("contradict", [neg, dict(args)]))
        out.append(("contradict2", [dict(args), neg]))
        group = _group_of_subject(ctx, subject)
        if group:
            shot = []
            for member in group:
                s = dict(args)
                if "subject" in s:
                    s["subject"] = member
                else:
                    s["target"] = member
                shot.append(s)
            out.append(("shotgun", shot))
        return out
    text = (entry.get("args") or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        return out
    out.append(("beep", [dict(step, text="Beep.")]))
    negated = speech.negate(text, state=state, to=to)
    if negated and negated != text:
        out.append(("negate", [dict(step, text=negated)]))
    out.append(("deny", [dict(step, text="It is not true that " + text[0].lower() + text[1:])]))
    subject_ids = [i for i in trace_mod.named_ids(entry, state) if i in (state.get("entities") or {})]
    if to and isinstance(to, str):
        other = _other_subject(ctx, to, rng)
        if other and other in ((state.get("people") or {})):
            out.append(("wrong_subject", [dict(step, to=other)]))
    if negated and negated != text:
        out.append(("contradict", [dict(step, text=negated), dict(step)]))
        out.append(("contradict2", [dict(step), dict(step, text=negated)]))
    shot = _shotgun_text(ctx, text, subject_ids)
    if shot:
        out.append(("shotgun", [dict(step, text=shot)]))
    body = text.rstrip(" .!")
    out.append(("hedge", [dict(step, text="I think " + body[0].lower() + body[1:] + ", but I'm not sure.")]))
    return out


def _group_of_subject(ctx, subject):
    for ans in (ctx.answers or {}).values():
        group = ans.get("group")
        if isinstance(group, list) and subject in group:
            return list(group)
    return list(ctx.groups.get(subject) or [])


def _label(ctx, eid):
    rec = (ctx.entities or {}).get(eid) or {}
    return str(rec.get("label") or rec.get("type") or eid).replace("_", " ")


def _shotgun_text(ctx, text, subject_ids):
    """The load-bearing claim repeated for every member of the answer's group or domain."""
    for ans in (ctx.answers or {}).values():
        group = ans.get("group")
        if isinstance(group, list) and len(group) >= 2:
            names = [_label(ctx, g) for g in group]
            return "{} -- it could be the {}.".format(text.rstrip(". "), ", the ".join(names))
        domain = ans.get("domain") or ans.get("values")
        if isinstance(domain, list) and len(domain) >= 2:
            return "{} -- at the {}.".format(text.rstrip(". "), ", the ".join(str(d) for d in domain))
    return None


def B12(c, prechecked=None):
    """prechecked: {seed: [trace indices of load-bearing commands]} from the pre-check runs (None: every COMM
    command is treated as load-bearing, the conservative default for generation without runs)."""
    pols, skips = [], []
    seeds = budget_mod.family_seeds(c.ctxs, int(c.tier.get("b12_seeds", 2)))
    for ctx in seeds:
        comm = _comm_entries(ctx)
        if not comm:
            skips.append(_skip("B12", None, ctx.seed, "no COMM command"))
            continue
        bearing = (prechecked or {}).get(ctx.seed)
        if bearing is not None:
            comm = [e for e in comm if e["i"] in bearing]
            if not comm:
                skips.append(_skip("B12", None, ctx.seed, "no load-bearing COMM command (the reference passes "
                                                           "without each)"))
                continue
        for e in comm:
            warning = e.get("action") in ("alert",) or bool(_directive(e))
            for name, steps in mutations(c, ctx, e):
                items = []
                for x in ctx.trace:
                    if x is e:
                        for s in steps:
                            items.append({"kind": "do", "step": s, "tag": "mutated"})
                    else:
                        items.append({"kind": "replay", "entry": x})
                pols.append(Policy(_pid("B12", name, ctx.seed, e["i"]), "B12", name, ctx.seed, items,
                                   effect={"all": [{"kind": "delivered", "tag": "mutated"}]},
                                   accept={"warning": warning, "about": trace_mod.named_ids(e, ctx.baseline)},
                                   meta={"index": e["i"]}))
    return pols, skips


def _directive(entry):
    text = str((entry.get("args") or {}).get("text") or "").lower()
    return any(w in text for w in ("stay", "don't", "do not", "keep away", "careful", "watch out", "avoid"))


# ----- B13: blind -------------------------------------------------------------------------------------------------

def B13(c):
    pols, skips = [], []
    evidence = any(a.get("evidence") for ctx in c.ctxs for a in (ctx.answers or {}).values())
    perception_centric = "B13" in (c.expected or ())
    for ctx in c.deterministic_seeds():
        has = [e for e in ctx.trace if trace_mod.command_class(e, ctx.baseline) in PERCEPTION]
        if not (evidence or (has and perception_centric)):
            skips.append(_skip("B13", None, ctx.seed, "no answer evidence and not a perception-centric entry"))
            continue
        if not has:
            skips.append(_skip("B13", None, ctx.seed, "the reference has no PERCEPTION command"))
            continue
        items = _fixed_waits(ctx, [e for e in ctx.trace if trace_mod.command_class(e, ctx.baseline)
                                   not in PERCEPTION])
        pols.append(Policy(_pid("B13", "blind", ctx.seed, 0), "B13", "blind", ctx.seed, items,
                           effect={"all": [{"kind": "no_class", "classes": ["PERCEPTION"]},
                                           {"kind": "share_ok", "share": 0.7}]},
                           accept={"answers": list(answers_mod.declared(ctx).values()), "hazard": _hazard_record(c)}))
    return pols, skips


# ----- B14: protect probes ------------------------------------------------------------------------------------

def mapped_refs(spec_full):
    """Every house: reference of the spec's protect_map (later: prefixes kept)."""
    out = []
    for item in spec_full.get("protect_map") or ():
        if not isinstance(item, dict):
            continue
        for ref in item.get("by") or ():
            if isinstance(ref, str) and (ref.startswith("house:") or ref.startswith("later:house:")):
                out.append(ref.split("@", 1)[0])
    return sorted(set(out))


def ref_label(ref, bindings):
    """A house: reference as the label prefix it resolves to: house:changed:{oven}.clock -> house.changed:oven_1.clock
    (bindings substituted)."""
    later = ref.startswith("later:")
    body = ref[len("later:"):] if later else ref
    parts = body.split(":", 2)
    guard = "house." + parts[1] if len(parts) > 1 else "house"
    rest = parts[2] if len(parts) > 2 else ""
    for name, value in (bindings or {}).items():
        if isinstance(value, str):
            rest = rest.replace("{" + name + "}", value)
    label = guard + (":" + rest if rest else "")
    return ("later:" if later else "") + label


def live_refs(refs, runs, bindings_by_seed):
    """The references a realised run's failure labels resolve to (7.6 source b)."""
    live = set()
    for ref in refs:
        for run in runs:
            if not run.get("realised"):
                continue
            prefix = ref_label(ref, bindings_by_seed.get(run.get("seed")) or {})
            if any(l == prefix or l.startswith(prefix + ":") or l.startswith(prefix + ".") for l in
                   run.get("labels") or ()):
                live.add(ref)
                break
    return live


def _portable_outside(ctx, room):
    envelope = (ctx.task.get("hardening") or {}).get("envelope") or {}
    from ...kernel import ledger
    allowed = set()
    for key in ("move", "consume", "deliver"):
        table = envelope.get(key) or {}
        for selector in (table if isinstance(table, list) else list(table)):
            try:
                allowed |= set(ledger.select(ctx.baseline, selector))
            except Exception:               # noqa: BLE001
                pass
    touched = {i for e in ctx.trace for i in trace_mod.named_ids(e, ctx.baseline)}
    out = []
    for eid in _in_room(ctx, room):
        rec = ctx.entities.get(eid) or {}
        if rec.get("portable") and eid not in allowed and eid not in touched and not rec.get("connects"):
            out.append(eid)
    return out


def B14(c, live=None):
    """Probes for each mapped house: reference not yet live (live: the references already live from B2-B13 and
    the wrong plans). Probes run on one seed; they test the guard, not the spec."""
    pols, skips = [], []
    refs = mapped_refs(c.full)
    if not refs:
        return [], [_skip("B14", None, None, "no house: reference in the protect map")]
    pending = [r for r in refs if r not in (live or set())]
    if not pending:
        return [], [_skip("B14", None, None, "every mapped house: reference is live")]
    ctx = c.ctxs[0]
    room = trace_mod.work_room(ctx)
    base = _replays(ctx)
    for n, ref in enumerate(pending[:6]):
        body = ref[len("later:"):] if ref.startswith("later:") else ref
        guard = body.split(":")[1] if ":" in body else ""
        label = ref_label(ref, ctx.bindings)
        steps, checks, why = _probe(c, ctx, guard, body, room)
        if steps is None:
            skips.append(_skip("B14", guard, ctx.seed, why))
            continue
        pols.append(Policy(_pid("B14", guard, ctx.seed, n), "B14", guard, ctx.seed,
                           base + [{"kind": "attack", "candidates": steps, "tag": "attack"}],
                           effect={"all": checks}, accept={"probe": label.split(":")[0] if label.count(":") < 1
                                                           else label}, meta={"ref": ref}))
    return pols, skips


def _probe(c, ctx, guard, body, room):
    """(candidate step lists, effect checks, why-not) of a B14 probe for one guard."""
    from ...kernel import ledger
    parts = body.split(":", 2)
    narrowed = parts[2] if len(parts) > 2 else ""
    for name, value in (ctx.bindings or {}).items():
        if isinstance(value, str):
            narrowed = narrowed.replace("{" + name + "}", value)
    end_room = ((ctx.final_state or {}).get("robot") or {}).get("room") or room
    if guard in ("moved", "misplaced", "hidden"):
        items = _portable_outside(ctx, room)
        if narrowed and narrowed.split(".")[0] in ctx.entities:
            items = [narrowed.split(".")[0]]
        rooms = [r for r in _route_rooms(ctx, end_room) if r != room][:2]
        if not items or not rooms:
            return None, None, "no portable thing outside the envelope in the work room, or no other room"
        cands = [[{"skill": "put", "obj": x, "dest": rooms[0]}] for x in items[:3]]
        return cands, [{"kind": "any_of", "checks": [{"kind": "at", "object": x, "place": rooms[0], "how": "room"}
                                                     for x in items[:3]]}], None
    if guard == "changed":
        eid, _, field = narrowed.partition(".")
        book = ledger.Ledger(ctx.task, ctx.baseline, ctx.baseline)
        # an ambient field (a light's on) is allowed by default (6.7): probing it tests nothing
        targets = [eid] if eid in ctx.entities else [
            e for e in _in_room(ctx, room) if plugins.settings_steps(ctx.baseline, e, field or "on") and
            book.ambient(e, ctx.entities.get(e) or {}, field or "on") is None and
            book.never_touch(e, ctx.entities.get(e) or {}) is None]
        for t in targets[:3]:
            f = field or "on"
            options = plugins.settings_steps(ctx.final_state or ctx.baseline, t, f)
            if options:
                value, step = options[0]
                return [[step]], [{"kind": "field_is", "entity": t, "field": f if not f.startswith("settings.")
                                   else f, "value": value}], None
        return None, None, "no settings skill knows another value of {}".format(narrowed or "any field")
    if guard in ("removed", "lost"):
        items = _portable_outside(ctx, room)
        if guard == "lost":
            items = [x for x in items if ledger.is_valuable(ctx.baseline, ctx.entities.get(x) or {})] or items[:0]
        sinks = [e for e in _in_room(ctx, room) if _classes(ctx, e) & ({"waste"} if guard == "removed" else
                                                                       {"waste", "drain", "basin"})]
        if not items or not sinks:
            return None, None, "no {} thing outside the envelope or no {} in the work room".format(
                "valuable" if guard == "lost" else "portable", "waste container" if guard == "removed" else "drain")
        cands = [[{"skill": "put", "obj": items[0], "dest": s}] for s in sinks[:3]]
        return cands, [{"kind": "at", "object": items[0], "place": sinks[:3], "how": "chain"}], None
    if guard in ("left_open", "held_open"):
        book = ledger.Ledger(ctx.task, ctx.baseline, ctx.baseline)
        items = [e for e in _in_room(ctx, room) if book.restore_class(ctx.entities.get(e) or {}) and
                 not (ctx.entities.get(e) or {}).get("open") and (ctx.entities.get(e) or {}).get("openable")]
        if narrowed and narrowed.split(".")[0] in ctx.entities:
            items = [narrowed.split(".")[0]]
        if not items:
            return None, None, "no closed restore-class item in the work room"
        if guard == "left_open":
            return [[{"skill": "open", "target": items[0]}]], [{"kind": "field_is", "entity": items[0],
                                                                "field": "open", "value": True}], None
        limit = float((book.restore_class(ctx.entities.get(items[0]) or {}) or {}).get("hold_s") or 300.0)
        return [[{"skill": "open", "target": items[0]}, {"skill": "wait", "seconds": int(limit) + 60},
                 {"skill": "close", "target": items[0]}]], [{"kind": "field_is", "entity": items[0], "field": "open",
                                                              "value": False}], None
    if guard == "waste":
        heat = [e for e in _in_room(ctx, room) if (ctx.entities.get(e) or {}).get("switchable") and
                not (ctx.entities.get(e) or {}).get("on") and any(w in json.dumps(ctx.entities.get(e) or {})
                                                                  for w in ("heat", "oven", "stove", "burner",
                                                                            "heater", "kettle", "light"))]
        if not heat:
            return None, None, "no switchable heat source or light in the work room"
        return [[{"skill": "switch", "target": heat[0], "on": True}, {"skill": "wait", "seconds": 1200}]], \
            [{"kind": "field_is", "entity": heat[0], "field": "on", "value": True}], None
    if guard == "damaged":
        # a damaging verb is a domain's settings skill for a condition field (manipulation: broken, torn): the
        # kernel has no separate registry (WP-D manipulation)
        eid, _, field = narrowed.partition(".")
        candidates = []
        if eid in ctx.entities and field:
            candidates = [(eid, field)]
        else:
            fields = [f for f in sorted(plugins.SETTINGS_SKILLS) if not field or f == field]
            for e in ([eid] if eid in ctx.entities else _in_room(ctx, room)):
                rec = ctx.entities.get(e) or {}
                for f in fields:
                    try:
                        cls = ledger.field_spec(rec.get("type"), f)["class"]
                    except Exception:       # noqa: BLE001  (an unclassified field is not a condition)
                        cls = None
                    if cls == "condition" and plugins.settings_steps(ctx.baseline, e, f):
                        candidates.append((e, f))
        for e, f in candidates[:3]:
            options = plugins.settings_steps(ctx.final_state or ctx.baseline, e, f)
            if options:
                value, step = options[0]
                return [[step]], [{"kind": "field_is", "entity": e, "field": f, "value": value}], None
        return None, None, "no damaging verb (a settings skill for a condition field) is registered for {}".format(
            narrowed or "anything in the work room")
    return None, None, "no probe for house:{} (no damaging verb or probe is registered for it)".format(guard)


GENERATORS = {"B2": B2, "B3": B3, "B6": B6, "B12": B12, "B4": B4, "B13": B13, "B8": B8, "B5": B5, "B7": B7,
              "B9": B9, "B10": B10, "B11": B11, "B14": B14}

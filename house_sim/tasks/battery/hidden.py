"""The private battery (VERIFY_HARDENING 12.2, 12.3; WP6): randomised families run only at the gate, with the salt
HMAC-SHA256(split_key, "house_ai.verify.private.v1") (tasks/private.py, WP5) in every random choice:
random.Random(HMAC(salt, "<catalog_id>:<seed>:<id>")).

    P3  B3 with 4 destinations drawn at random from every reachable non-sink place in at least 2 rooms other than
        the destination's room (a floor, a surface or a container), plus keep     -> each realised run fails (B3 set)
    P4  B4 with alternatives drawn at random and look-alike pick strategies (nearest, leftmost, first seen, random)
        applied blind                                                             -> each realised run fails
    P5  B5b / B5c with 3 values drawn from the answer's whole range or domain (numbers uniformly within the observed
        range widened by 50 %)                                                    -> each wrong-value run fails
    P7  fixed-timer exposure over 200 private-pool builds (audits.py on houses the author never saw), then the 4
        best tau values run as early / dawdle_blind                               -> exposure <= 0.2, confirms fail
    P8  B8 cross and superset over random subsets of the other variants' commands -> as B8
    PM  3 must-pass runs drawn from M1, M2 and M4 with random alternatives        -> each realised run passes
At most 24 runs (P3 5, P4 3, P5 3, P7 8, P8 2, PM 3), the tier's cap (30 for H0 and H1). Results carry the family, the
outcome class and the label class only (12.3); the instance details (seeds, destinations, tau values, plans) are in
"details" for the private store, never in the public record.

    run(spec, references, seeds, mode, level, salt=None, key=None, pool=None) -> {"results", "runs", "details", ...}
    label_class(labels) -> conservation | placement | goal | content | timing | perception | budget | other | none
"""
import hashlib
import hmac
import time

from .. import private as private_mod
from . import audits as audits_mod
from . import budget as budget_mod
from . import generate as gen
from .mustpass import GENERATORS as MUSTPASS_GENERATORS
from . import policies as pol_mod
from . import trace as trace_mod
from .policies import Policy

CAPS = dict(budget_mod.PRIVATE_CAPS)
PRIVATE_POOL_SAMPLE = 200


def label_class(labels):
    labels = list(labels or ())
    if not labels:
        return "none"
    text = " ".join(labels)
    for cls, words in (("conservation", ("mess_", "wrong_sink")), ("placement", ("house.moved", "house.misplaced",
                                                                                 "house.hidden", "house.lost",
                                                                                 "house.held", "house.removed")),
                       ("content", ("said:", "false_claim", "false_report", "contradiction", "warning_inverted",
                                    "broken_promise", "report:")),
                       ("timing", ("timing:",)), ("perception", ("perceive:",)), ("goal", ("goal:", "fail:")),
                       ("budget", ("budget",))):
        if any(w in text for w in words):
            return cls
    return "other"


def _mac(salt, text):
    return hmac.new(salt, text.encode("utf-8"), hashlib.sha256).digest()


def pool_from_salt(salt, n=private_mod.POOL_N):
    """The private audit pool (12.1) from the salt (private.pool derives it from the key the same way)."""
    out, seen, i = [], set(), 0
    while len(out) < n and i < n * 4:
        seed = private_mod._private_seed(salt, "pool:{}".format(i))
        i += 1
        if seed not in seen:
            seen.add(seed)
            out.append(seed)
    return out


def sample_pool(spec, salt, key=None, pool=None, n=PRIVATE_POOL_SAMPLE):
    """The 200 private-pool houses one spec's P7 audit uses: private.sample_pool with the key (the spec's hidden
    test candidates skipped), else the same salted draw from the salt's pool."""
    if pool is not None:
        return sorted(pool)[:n] if len(pool) > n else sorted(pool)
    if key is not None:
        return private_mod.sample_pool(spec, key=key, n=n)
    import random
    from ..spec import expand
    cid = expand(spec).get("catalog_id")
    seeds = pool_from_salt(salt)
    draw = random.Random(_mac(salt, "pool_sample:{}".format(cid)))
    return sorted(draw.sample(seeds, min(n, len(seeds))))


def _salted(c, family, seed, index):
    return private_mod.rng(c.salt, c.cid, seed, "{}#{}".format(family, index))


def P3(c):
    pols = []
    for ctx in c.seeds_for(1):
        deps = gen._b3_deposits(ctx)
        if not deps:
            continue
        i, obj, dest = deps[-1]
        here = gen._robot_room_at(ctx, i)
        dest_room = gen._room_of(ctx, dest) if isinstance(dest, str) and dest else here
        rng = _salted(c, "P3", ctx.seed, i)
        rooms = [r for r in gen._route_rooms(ctx, here) if r != dest_room]
        rng.shuffle(rooms)
        rooms = rooms[:max(2, min(4, len(rooms)))]
        places = []
        for r in rooms:
            places.append(r)
            places.extend(e for e in gen._in_room(ctx, r) if gen._accepts(ctx.entities.get(e), None) and
                          not gen._classes(ctx, e) & set(gen.SINK_CLASSES) and e != obj)
        rng.shuffle(places)
        obj_rec = ctx.record_at(i, obj, "pre") or ctx.entities.get(obj) or {}
        accept = {"object": obj, "dest": dest, "kinds": gen.accept_mod.carried_kinds(obj_rec), "family": "B3"}
        for n, place in enumerate(places[:4]):
            how = "room" if place in (ctx.baseline.get("rooms") or {}) else "chain"
            pols.append(Policy("P3.random@{}#{}".format(ctx.seed, n), "P3", "random", ctx.seed,
                               gen._b3_items(ctx, i, [[{"skill": "put", "obj": obj, "dest": place}]]),
                               effect={"all": [{"kind": "at", "object": obj, "place": place, "how": how}]},
                               accept=accept, meta={"place": place}))
        items = gen._b3_items(ctx, i, None, drop_later=lambda e: obj in trace_mod.named_ids(e, ctx.baseline))
        pols.append(Policy("P3.keep@{}#0".format(ctx.seed), "P3", "keep", ctx.seed, items,
                           effect={"all": [{"kind": "at", "object": obj, "place": None, "how": "hand"}]},
                           accept=dict(accept, keep=True)))
    return pols[:CAPS["P3"]]


def _pick(c, ctx, role, members, strategy, rng):
    others = [m for m in members if m != role and m in ctx.entities and not gen._correct(ctx, m)]
    if not others:
        return None
    if strategy == "random":
        return rng.choice(others)
    config = ctx.baseline
    if strategy == "leftmost":
        keys = audits_mod.ordering_keys(config, others, "left_to_right") or {}
        return sorted(others, key=lambda m: keys.get(m, (m,)))[0]
    if strategy == "nearest":
        keys = audits_mod.ordering_keys(config, others, "start_distance") or {}
        return sorted(others, key=lambda m: keys.get(m, (m,)))[0]
    if strategy == "first_seen":
        return sorted(others)[0]
    return others[0]


def P4(c):
    pols = []
    for ctx in c.seeds_for(1):
        roles = gen._decision_roles(c, ctx)
        if not roles:
            continue
        role, members = roles[0]
        rng = _salted(c, "P4", ctx.seed, 0)
        strategies = ["nearest", "leftmost", "first_seen", "random"]
        rng.shuffle(strategies)
        for n, strategy in enumerate(strategies[:CAPS["P4"]]):
            alt = _pick(c, ctx, role, members, strategy, rng)
            if alt is None:
                continue
            items, changed = gen._swap_items(ctx, role, alt, True)
            if changed:
                pols.append(Policy("P4.{}@{}#{}".format(strategy, ctx.seed, n), "P4", strategy, ctx.seed, items,
                                   effect={"all": [{"kind": "tagged_ok", "tag": "retarget"}]},
                                   accept={"role": role, "alt": alt, "family": "B4"}))
    return pols[:CAPS["P4"]]


def _draw_value(ans, values, rng):
    kind = ans.get("kind")
    if kind in ("number", "time"):
        nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not nums:
            return None
        lo, hi = min(nums), max(nums)
        span = (hi - lo) or abs(hi) or 1.0
        return round(rng.uniform(lo - 0.25 * span, hi + 0.25 * span), 3)
    if kind in ("entity", "place") and isinstance(ans.get("group"), list):
        return rng.choice(ans["group"])
    if kind == "set" and isinstance(ans.get("group"), list):
        k = rng.randint(1, max(1, len(ans["group"]) - 1))
        return sorted(rng.sample(ans["group"], k))
    domain = ans.get("domain") or ans.get("values")
    if isinstance(domain, list) and domain:
        return rng.choice(domain)
    return None


def P5(c):
    from . import answers as answers_mod
    pols = []
    names = sorted({n for ctx in c.ctxs for n, a in answers_mod.declared(ctx).items() if a.get("kind") != "branch"})
    for name in names:
        observed = [(x.answers.get(name) or {}).get("value") for x in c.ctxs]
        for ctx in c.seeds_for(3):
            ans = (ctx.answers or {}).get(name)
            if not ans or "value" not in ans:
                continue
            rng = _salted(c, "P5", ctx.seed, name)
            for attempt in range(6):
                value = _draw_value(ans, observed, rng)
                if value is None or answers_mod.same(value, ans["value"], ans.get("kind"), ans.get("tol") or 0.0):
                    continue
                items, changed = answers_mod.substituted(ctx, name, value)
                if changed:
                    pols.append(Policy("P5.value@{}#{}".format(ctx.seed, len(pols)), "P5", "value", ctx.seed, items,
                                       effect={"all": [{"kind": "tagged_ok", "tag": "substituted"}]},
                                       accept={"answers": [ans], "family": "B5"}, meta={"value": value}))
                    break
            if len(pols) >= CAPS["P5"]:
                return pols
    return pols[:CAPS["P5"]]


def P7(c, spec, key=None, pool=None):
    """(records, policies): the exposure over private-pool builds, then the best tau values as early runs."""
    timing = any(gen._timing_items(x) for x in c.ctxs) or any(
        a.get("kind") == "time" for x in c.ctxs for a in (x.answers or {}).values())
    if not timing:
        return [], []
    seeds = sample_pool(spec, c.salt, key=key, pool=pool)
    rec = audits_mod.audit(spec, seeds=seeds)
    records = []
    for name, body in sorted((rec.get("exposure") or {}).items()):
        ok = body["best_share"] <= audits_mod.EXPOSURE_MAX + 1e-9
        records.append({"id": "P7.exposure@private#{}".format(name), "family": "P7", "sub": "exposure", "seed": None,
                        "outcome": "fails" if ok else "passes", "realised": True, "labels": [],
                        "rate": body["best_share"], "tau": body.get("tau")})
    pols = []
    taus = sorted({float(b.get("tau")) for b in (rec.get("exposure") or {}).values() if b.get("tau") is not None})
    for ctx in c.seeds_for(2):
        timing_items = gen._timing_items(ctx)
        for n, tau in enumerate(taus[:4]):
            if timing_items and not any(gen.violates(item, (ctx.timing.get(str(item.get("label"))) or {})
                                                     .get("t_trigger"), tau, ctx.time_budget())
                                        for item in timing_items):
                continue
            items = [{"kind": "do", "step": {"do": "wait", "seconds": max(1, int(tau))}, "tag": "dawdle"}]
            items += [{"kind": "replay", "entry": e, "tag": "post_wait" if trace_mod.effectful(e, ctx.baseline)
                       else None} for e in ctx.trace if not trace_mod.conditioned(e)]
            pols.append(Policy("P7.early@{}#{}".format(ctx.seed, n), "P7", "early", ctx.seed, items,
                               effect={"all": [{"kind": "clock", "tau": tau, "tag": "post_wait"}]},
                               accept={"hazard": True, "family": "B7"}, meta={"tau": tau}))
    return records, pols[:CAPS["P7"]]


def P8(c):
    pols, _ = gen.B8(c)
    rng = _salted(c, "P8", c.ctxs[0].seed if c.ctxs else 0, 0)
    out = []
    for p in pols:
        if p.sub not in ("cross", "superset"):
            continue
        # a random subset of the other branch's commands (the replays tagged transplant / added)
        mine = [i for i, item in enumerate(p.items) if item.get("tag") in ("transplant", "added")]
        if len(mine) > 1:
            drop = set(rng.sample(mine, rng.randint(0, len(mine) // 2)))
            p.items = [item for i, item in enumerate(p.items) if i not in drop]
        p.id = "P8." + p.id.split(".", 1)[1]
        p.family = "P8"
        p.accept["family"] = "B8"
        out.append(p)
    rng.shuffle(out)
    return out[:CAPS["P8"]]


def PM(c):
    out = []
    rng = _salted(c, "PM", c.ctxs[0].seed if c.ctxs else 0, 0)
    for family, generator in MUSTPASS_GENERATORS:
        if family not in ("M1", "M2", "M4"):
            continue
        pols, _ = generator(c)
        if pols:
            p = rng.choice(pols)
            p.id = "PM." + p.id
            p.family = "PM"
            out.append(p)
    return out[:CAPS["PM"]]


def run(spec, references, seeds, mode, level, salt=None, key=None, pool=None):
    """The private families (module docstring); {"ran": False} without the salt."""
    from . import Session, _branch_diffs, prepare
    started = time.process_time()
    if salt is None:
        return {"ran": False, "status": private_mod.NOT_RUN, "results": [], "runs": []}
    ctxs, errors = prepare(spec, {"references": references}, references, seeds, mode)
    out = {"ran": True, "results": [], "runs": [], "mustpass_runs": [], "details": [],
           "harness_errors": list(errors)}
    try:
        if not ctxs:
            out["ran"] = False
            out["why"] = "no seed with a reference"
            return out
        tier = budget_mod.tier_of({}, ctxs)
        c = gen.Context(spec, {}, ctxs, mode, level, tier, salt=salt)
        cap = int(tier.get("private_cap", 30))
        budget = budget_mod.Budget(level, tier, "private", budget_s=max(x.time_budget() for x in ctxs),
                                   caps=CAPS, spec_runs=cap)
        session = Session(c, budget, mode, _branch_diffs(ctxs))
        ctx_of = {x.seed: x for x in ctxs}
        records = []
        policies = P3(c) + P4(c) + P5(c)
        p7_records, p7 = P7(c, spec, key=key, pool=pool)
        records.extend(p7_records)
        policies += p7 + P8(c) + PM(c)
        for p in policies:
            if p.family == "PM":
                p.mustpass = True
            rec = session.execute(p, ctx_of[p.seed])
            records.append(rec)
        for rec in records:
            family = rec.get("family")
            outcome = rec.get("outcome")
            if family == "PM":
                met = outcome == "passes"
            else:
                met = outcome in ("fails", "blocked")
            out["results"].append({"family": family, "outcome": outcome, "label_class": label_class(
                rec.get("labels")), "met": met})
            row = {"id": rec.get("id"), "family": family, "outcome": outcome,
                   "label_class": label_class(rec.get("labels"))}
            # PM is a must-pass family (its pass is met): strength reads it as record["private_mustpass"]
            out["mustpass_runs" if family == "PM" else "runs"].append(row)
            out["details"].append(pol_mod.compact(rec) if "plan_digest" in rec else rec)
        out["harness_errors"].extend(session.errors)
        out["summary"] = budget.summary()
    finally:
        for x in ctxs:
            x.cleanup()
    out["cpu_s"] = round(time.process_time() - started, 3)
    return out

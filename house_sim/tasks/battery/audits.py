"""Audits over 200 extra houses: tells, balance and nuisance features (VERIFY_HARDENING 9.3, 9.6, 9.7, 10.3 B7;
WP6).

    audit(spec, n=200, informative=None, seeds=None, house=None) -> record
        builds seeds 100000 + i (seeds.audit_seeds), setup only with the fast path (setup.apply_setup(...,
        audit=True), WP9) on cached houses (generator/disk_cache.house, WP9) in the evaluation mode (realistic,
        geometric), for a spec with an entity, set, word, place, branch or time answer or a timing key; the first
        seed is also built in full and compared (setup.audit_diff: a difference is a harness error, 22.5)
    Every share is arithmetic over the builds (no policy runs, 22.5 (6)):
        answers   per answer: its values ("audit_values" for c_random), the most frequent value's share against
                  1/k + 0.1 (word, entity, place by place type; B5c's best constant), branch balance within 1/k +-
                  0.1, set membership within max(0.8, mean share + 0.2), number cv and range (9.3)
        tells     per entity / set answer with a group of k >= 2 and per agent-visible ordering (left to right on its
                  surface; surface then shelf; distance from the robot's start; bearing from the start pose): the
                  answer's rank share against 1/k + 0.1 and an exact one-sided binomial test at p < 0.001 /
                  (orderings x k)
        exposure  per time answer: the best fixed time's pass share over the drawn times, by
                  monitor.timing_outcome for every timing item (else within the answer's tolerance): at most 0.2
        tree      a depth-2 decision tree (categorical splits and quartile thresholds) trained on the first half
                  and tested on the second half predicts each answer (entity and set answers by rank) from the
                  nuisance features of 9.7: fails when test accuracy >= max(base rate + 0.15, 0.7); features the
                  spec declares "informative" are left out
        lint      an L-AUDIT error per failed bound (the verifier's G5)
    rate_records(audit) -> battery records for B5c's best-constant share and B7's fixed-timer exposure
"""
import json
import math
import time

from ...kernel.errors import SpecError

KINDS = ("entity", "set", "word", "place", "branch", "time")
TELL_MARGIN = 0.1
ALPHA = 0.001
EXPOSURE_MAX = 0.2
TREE_MARGIN = 0.15
TREE_FLOOR = 0.7
TAU_STEP_S = 30
ORDERINGS = ("left_to_right", "surface_shelf", "start_distance", "start_bearing")


# ----- builds -----------------------------------------------------------------------------------------------------

def _house_kwargs(chosen):
    from ..spec import schema_of
    house = dict(chosen.get("house", {}))
    if "gen" not in house and schema_of(chosen) >= 3:
        house["gen"] = 2
    return house


def build_row(spec, seed, house=None, registry=None):
    """One audit build: {"seed", "variant", "branch", "config", "bindings", "answers", "phrasing", "chosen"}."""
    from ...generator import disk_cache
    from ...kernel.registry import default_registry
    from ..runner import bind_answers, branch_value
    from ..setup import apply_setup
    from ..spec import choose_variant, expand
    import random
    full = expand(spec)
    chosen, variant = choose_variant(full, seed)
    kwargs = _house_kwargs(chosen)
    config = (house or disk_cache.house)(seed, kwargs)
    registry = registry or default_registry()
    bindings = apply_setup(config, chosen.get("setup", []), registry, seed, chosen, camera="geometric",
                           perception="realistic", audit=True)
    answers = bind_answers(chosen.get("answers"), bindings, chosen, variant)
    phrasings = chosen.get("phrasings") or [chosen.get("instruction", "")]
    rng = random.Random("instruction:{}:{}".format(chosen.get("catalog_id", "custom"), seed))
    phrasing = rng.choice(list(range(len(phrasings)))) if phrasings else 0
    return {"seed": seed, "variant": variant, "branch": branch_value(chosen, variant), "config": config,
            "bindings": bindings, "answers": answers, "phrasing": phrasing, "chosen": chosen, "kwargs": kwargs}


def correctness(spec, seed, house=None):
    """22.5's audit of the fast path: the audit build of a seed against a full build (setup.audit_diff); [] when
    they agree."""
    from ...kernel.registry import default_registry
    from ..setup import apply_setup, audit_diff
    from ..spec import choose_variant, expand
    from ...generator import disk_cache
    full = expand(spec)
    chosen, _ = choose_variant(full, seed)
    kwargs = _house_kwargs(chosen)
    get = house or disk_cache.house
    registry = default_registry()
    full_config = get(seed, kwargs)
    full_bindings = apply_setup(full_config, chosen.get("setup", []), registry, seed, chosen, camera="geometric",
                                perception="realistic")
    audit_config = get(seed, kwargs)
    audit_bindings = apply_setup(audit_config, chosen.get("setup", []), registry, seed, chosen, camera="geometric",
                                 perception="realistic", audit=True)
    return audit_diff(full_config, full_bindings, audit_config, audit_bindings)


# ----- statistics -------------------------------------------------------------------------------------------------

def binom_tail(count, n, p):
    """P(X >= count) for X ~ Binomial(n, p), exactly."""
    if count <= 0:
        return 1.0
    if count > n:
        return 0.0
    total = 0.0
    for x in range(count, n + 1):
        total += math.comb(n, x) * (p ** x) * ((1.0 - p) ** (n - x))
    return min(1.0, total)


def shares(values):
    counts = {}
    for v in values:
        key = json.dumps(v, sort_keys=True)
        counts[key] = counts.get(key, 0) + 1
    total = float(sum(counts.values())) or 1.0
    return {k: c / total for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}, counts


def tell_check(ranks, k, orderings):
    """(ok, detail) of a rank list against 1/k + 0.1 and the binomial bound (9.7)."""
    n = len(ranks)
    if n == 0 or k < 2:
        return True, {}
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    worst_rank, worst = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
    share = worst / float(n)
    p = binom_tail(worst, n, 1.0 / k)
    bound = ALPHA / float(max(1, orderings) * k)
    ok = share <= 1.0 / k + TELL_MARGIN + 1e-9 and p >= bound
    return ok, {"rank": worst_rank, "share": round(share, 4), "p": p, "bound": bound, "k": k, "n": n}


# ----- orderings and features ----------------------------------------------------------------------------------

def _pos(record):
    for key in ("spos", "pos", "cell"):
        v = record.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 2 and all(isinstance(x, (int, float)) for x in v[:2]):
            return float(v[0]), float(v[1])
    return None


def _root(config, eid):
    from ...kernel.predicates import root_room
    try:
        return root_room(config, eid)
    except Exception:                       # noqa: BLE001
        return None


def ordering_keys(config, members, name):
    """{member: sort key} under an agent-visible ordering (None when it does not apply to these members)."""
    entities = config.get("entities") or {}
    robot = config.get("robot") or {}
    start_room = robot.get("room")
    rpos = robot.get("pos") or [0, 0]
    out = {}
    for m in members:
        rec = entities.get(m) or {}
        pos = _pos(rec)
        if name == "left_to_right":
            if pos is None:
                return None
            out[m] = (str(rec.get("location")), pos[0], m)
        elif name == "surface_shelf":
            out[m] = (str(rec.get("location")), rec.get("shelf") if isinstance(rec.get("shelf"), int) else -1,
                      pos[0] if pos else 0.0, m)
        elif name == "start_distance":
            room = _root(config, m)
            same = 0 if room == start_room else 1
            cell = rec.get("pos") or rec.get("cell")
            if not (isinstance(cell, list) and len(cell) >= 2):
                host = entities.get(rec.get("location")) or {}
                cell = host.get("pos") or [0, 0]
            d = math.hypot(float(cell[0]) - float(rpos[0]), float(cell[1]) - float(rpos[1])) if same == 0 else 0.0
            out[m] = (same, str(room) if same else "", round(d, 3), m)
        elif name == "start_bearing":
            if _root(config, m) != start_room:
                return None
            cell = rec.get("pos") or (entities.get(rec.get("location")) or {}).get("pos")
            if not (isinstance(cell, list) and len(cell) >= 2):
                return None
            facing = float(robot.get("facing_deg") or 0.0)
            ang = (math.degrees(math.atan2(float(cell[1]) - float(rpos[1]), float(cell[0]) - float(rpos[0]))) -
                   facing) % 360.0
            out[m] = (round(ang, 3), m)
    return out


def rank_of(config, members, member, name):
    keys = ordering_keys(config, members, name)
    if not keys or member not in keys:
        return None
    order = sorted(members, key=lambda m: keys[m])
    return order.index(member)


def _bucket(value, edges):
    for i, edge in enumerate(edges):
        if value <= edge:
            return i
    return len(edges)


def features(row, informative=()):
    """The nuisance features of 9.7 for one audit build."""
    config = row["config"]
    rooms = config.get("rooms") or {}
    meta = config.get("metadata") or {}
    robot = config.get("robot") or {}
    feats = {}
    feats["start_room"] = (rooms.get(robot.get("room")) or {}).get("type")
    feats["start_facing"] = robot.get("facing") or robot.get("facing_deg")
    from ..runner import default_requester
    req = (row["chosen"].get("request") or {}).get("from") if isinstance(row["chosen"].get("request"), dict) else None
    req = row["bindings"].get(req.strip("{}")) if isinstance(req, str) and req.startswith("{") else req
    if req is None:
        req = (default_requester(config) or {}).get("from")
    person = (config.get("people") or {}).get(req) or {}
    feats["requester"] = req
    feats["requester_role"] = person.get("role")
    feats["requester_room"] = (rooms.get(person.get("location")) or {}).get("type")
    start = str((config.get("clock") or {}).get("start") or "")
    hour = int(start[11:13]) if len(start) >= 13 and start[11:13].isdigit() else 0
    feats["time_of_day"] = _bucket(hour, (5, 11, 16, 20))
    feats["weather"] = (config.get("weather") or {}).get("condition")
    feats["dwelling"] = meta.get("dwelling_type")
    feats["generation"] = meta.get("gen")
    feats["floors"] = meta.get("floors")
    feats["rooms"] = _bucket(len(rooms), (10, 15, 20))
    kitchens = [r for r in rooms.values() if r.get("type") == "kitchen"]
    size = kitchens[0].get("size_m") if kitchens else None
    feats["kitchen_size"] = _bucket(float(size[0]) * float(size[1]), (9, 15, 22)) if isinstance(size, list) and \
        len(size) == 2 else None
    feats["phrasing"] = row["phrasing"]
    entities = config.get("entities") or {}
    types_present = {(e or {}).get("type") for e in entities.values()}
    for name, ans in sorted((row["answers"] or {}).items()):
        domain = ans.get("domain") or ans.get("values") or []
        for word in domain if isinstance(domain, list) else ():
            if isinstance(word, str):
                feats["has:" + word] = word in types_present or any(word in str(t) for t in types_present if t)
        group = ans.get("group")
        if isinstance(group, list):
            feats["group_size:" + name] = len(group)
        value = ans.get("value")
        member = value if isinstance(value, str) else (value[0] if isinstance(value, list) and value and
                                                       isinstance(value[0], str) else None)
        if member and member in entities:
            rec = entities[member]
            feats["answer_room:" + name] = (rooms.get(_root(config, member)) or {}).get("type")
            host = entities.get(rec.get("location")) or {}
            feats["answer_surface:" + name] = host.get("type") or rec.get("location")
            feats["answer_shelf:" + name] = rec.get("shelf") if isinstance(rec.get("shelf"), int) else None
    for key in list(informative or ()):
        for f in list(feats):
            if f == key or f.startswith(key + ":") or f.startswith(key + "_"):
                feats.pop(f, None)
    return feats


# ----- the decision tree -----------------------------------------------------------------------------------------

def _majority(labels):
    counts = {}
    for l in labels:
        counts[l] = counts.get(l, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]


def _splits(rows, name):
    values = [r[name] for r in rows if r.get(name) is not None]
    if not values:
        return []
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        ordered = sorted(float(v) for v in values)
        q = sorted({ordered[int(len(ordered) * f)] for f in (0.25, 0.5, 0.75) if int(len(ordered) * f) <
                    len(ordered)})
        return [("le", name, t) for t in q]
    return [("eq", name, v) for v in sorted({json.dumps(v, sort_keys=True) for v in values})]


def _test(row, split):
    kind, name, value = split
    v = row.get(name)
    if kind == "le":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) <= value
    return json.dumps(v, sort_keys=True) == value


def _gain_split(X, y):
    names = sorted({k for r in X for k in r})
    best, best_acc = None, -1.0
    for name in names:
        for split in _splits(X, name):
            left = [l for r, l in zip(X, y) if _test(r, split)]
            right = [l for r, l in zip(X, y) if not _test(r, split)]
            if not left or not right:
                continue
            acc = (left.count(_majority(left)) + right.count(_majority(right))) / float(len(y))
            if acc > best_acc + 1e-12:
                best, best_acc = split, acc
    return best


def fit_tree(X, y, depth=2):
    if depth == 0 or len(set(json.dumps(l) for l in y)) <= 1:
        return {"leaf": _majority(y)}
    split = _gain_split(X, y)
    if split is None:
        return {"leaf": _majority(y)}
    left = [(r, l) for r, l in zip(X, y) if _test(r, split)]
    right = [(r, l) for r, l in zip(X, y) if not _test(r, split)]
    return {"split": split, "left": fit_tree([r for r, _ in left], [l for _, l in left], depth - 1),
            "right": fit_tree([r for r, _ in right], [l for _, l in right], depth - 1)}


def predict(tree, row):
    while "leaf" not in tree:
        tree = tree["left"] if _test(row, tree["split"]) else tree["right"]
    return tree["leaf"]


def tree_check(X, y):
    """(ok, detail): a depth-2 tree trained on the first half, tested on the second (9.7)."""
    n = len(y)
    if n < 8:
        return True, {"n": n, "why": "too few builds"}
    half = n // 2
    tree = fit_tree(X[:half], y[:half])
    test_X, test_y = X[half:], y[half:]
    correct = sum(1 for r, l in zip(test_X, test_y) if predict(tree, r) == l)
    acc = correct / float(len(test_y))
    base = test_y.count(_majority(y[:half])) / float(len(test_y))
    bound = max(base + TREE_MARGIN, TREE_FLOOR)
    ok = acc < bound - 1e-9
    split = tree.get("split")
    return ok, {"accuracy": round(acc, 4), "base_rate": round(base, 4), "bound": round(bound, 4),
                "root_split": list(split) if split else None, "n_test": len(test_y)}


# ----- exposure ----------------------------------------------------------------------------------------------------

def exposure(times, items, budget_s, tol=60.0):
    """(best share, best tau) of a fixed time over the drawn times (None: the event never happens): by every timing
    item's arithmetic (monitor.timing_outcome) when there are items, else the share within tol of the time."""
    from .generate import violates
    if not times:
        return 0.0, None
    budget_s = float(budget_s or 0.0) or max([t for t in times if t is not None] + [3600.0])
    best, best_tau = 0.0, None
    tau = 0.0
    while tau <= budget_s + 1e-9:
        if items:
            hit = sum(1 for t in times if not any(violates(item, t, tau, budget_s) for item in items))
        else:
            hit = sum(1 for t in times if t is not None and abs(float(t) - tau) <= tol)
        share = hit / float(len(times))
        if share > best + 1e-12:
            best, best_tau = share, tau
        tau += TAU_STEP_S
    return best, best_tau


# ----- the audit ----------------------------------------------------------------------------------------------------

def _applicable(full):
    kinds = set()
    for ans in (full.get("answers") or {}).values() if isinstance(full.get("answers"), dict) else ():
        if isinstance(ans, dict):
            kinds.add(ans.get("kind"))
    for v in full.get("variants") or ():
        for ans in ((v or {}).get("answers") or {}).values() if isinstance((v or {}).get("answers"), dict) else ():
            if isinstance(ans, dict):
                kinds.add(ans.get("kind"))
    return bool(kinds & set(KINDS)) or bool(full.get("timing")), sorted(k for k in kinds if k)


def audit(spec, n=200, informative=None, seeds=None, house=None):
    """The audit record (module docstring)."""
    from .. import seeds as seeds_mod
    from ..spec import expand
    started = time.process_time()
    full = expand(spec)
    ok_kind, kinds = _applicable(full)
    if not ok_kind:
        return {"ran": False, "why": "no entity, set, word, place, branch or time answer and no timing key",
                "kinds": kinds, "ok": True, "lint": []}
    seeds = list(seeds) if seeds is not None else list(seeds_mod.audit_seeds(n))
    rec = {"ran": True, "n": len(seeds), "seeds": [seeds[0], seeds[-1]] if seeds else [], "kinds": kinds,
           "answers": {}, "tells": [], "exposure": {}, "tree": {}, "failures": [], "harness_errors": [], "lint": []}
    rows, infeasible = [], 0
    for seed in seeds:
        try:
            rows.append(build_row(spec, seed, house=house))
        except SpecError:
            infeasible += 1
        except Exception as exc:            # noqa: BLE001  (a build that crashes is the harness's, not a pass)
            if len(rec["harness_errors"]) < 3:
                rec["harness_errors"].append("audit build of seed {} raised {}: {}".format(
                    seed, type(exc).__name__, str(exc)[:160]))
    rec["infeasible"] = infeasible
    if seeds:
        try:
            diff = correctness(spec, seeds[0], house=house)
        except SpecError:
            diff = []
        if diff:
            rec["harness_errors"].append("audit fast path differs from a full build on seed {}: {}".format(
                seeds[0], "; ".join(diff[:3])))
    if not rows:
        rec["failures"].append("no audit build could be set up")
        _lint(rec)
        return rec
    names = sorted({k for r in rows for k in (r["answers"] or {})})
    informative = informative or {}
    feats = [features(r, informative) for r in rows]
    for name in names:
        present = [r for r in rows if "value" in ((r["answers"] or {}).get(name) or {})]
        if not present:
            continue
        ans0 = present[0]["answers"][name]
        kind = ans0.get("kind")
        values = [r["answers"][name]["value"] for r in present]
        body = {"kind": kind, "n": len(values), "audit_values": values}
        share_map, counts = shares(values if kind != "place" else [_place_class(r, r["answers"][name]["value"])
                                                                    for r in present])
        top = max(share_map.values()) if share_map else 0.0
        if kind in ("entity", "word", "place", "branch"):
            k = len(ans0.get("group") or ()) if kind == "entity" and ans0.get("group") else max(
                2, len(share_map)) if kind != "branch" else len(share_map)
            if kind == "word" and isinstance(ans0.get("domain"), list):
                k = max(k, len(ans0["domain"]))
            body["k"] = k
            body["best_constant_share"] = round(top, 4)
            body["bound"] = round(1.0 / k + TELL_MARGIN, 4) if k else None
            if kind == "branch":
                low = min(share_map.values()) if share_map else 0.0
                body["balanced"] = len(share_map) >= 2 and top <= 1.0 / len(share_map) + TELL_MARGIN + 1e-9 and \
                    low >= 1.0 / len(share_map) - TELL_MARGIN - 1e-9
                if not body["balanced"]:
                    rec["failures"].append("branch {!r} unbalanced over the audit builds: shares {}".format(
                        name, {json.loads(key): round(v, 3) for key, v in share_map.items()}))
            elif k and top > 1.0 / k + TELL_MARGIN + 1e-9:
                rec["failures"].append("answer {!r}: the most frequent value has share {:.2f} > 1/{} + 0.1 (a "
                                       "constant passes too often, B5c / 9.3)".format(name, top, k))
            body["shares"] = {json.loads(key) if not key.startswith("[") else key: round(v, 4)
                              for key, v in share_map.items()}
        if kind == "set":
            member_counts = {}
            for v in values:
                for m in v or ():
                    member_counts[m] = member_counts.get(m, 0) + 1
            mean = sum(len(v or ()) for v in values) / float(len(values)) / float(max(1, len(ans0.get("group") or
                                                                                            ()) or 1))
            limit = max(0.8, mean + 0.2)
            over = {m: c / float(len(values)) for m, c in member_counts.items() if c / float(len(values)) > limit}
            body["member_limit"] = round(limit, 4)
            if over and not full.get("always_needed"):
                rec["failures"].append("set {!r}: members {} are in more than {:.2f} of the audit sets".format(
                    name, sorted(over), limit))
        if kind == "number":
            nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if len(nums) >= 2:
                mean = sum(nums) / len(nums)
                sd = math.sqrt(sum((x - mean) ** 2 for x in nums) / (len(nums) - 1))
                body["cv"] = round(sd / abs(mean), 4) if mean else None
                body["range"] = round(max(nums) - min(nums), 4)
        if kind == "time":
            times = [v if isinstance(v, (int, float)) and not isinstance(v, bool) else None for v in values]
            items = [t for t in (full.get("timing") or ()) if isinstance(t, dict)]
            budget_s = float((present[0]["chosen"].get("budget") or {}).get("time_s") or 0.0)
            best, tau = exposure(times, items, budget_s, tol=float(ans0.get("tol") or 60.0))
            rec["exposure"][name] = {"best_share": round(best, 4), "tau": tau, "bound": EXPOSURE_MAX,
                                     "never_share": round(sum(1 for t in times if t is None) / float(len(times)), 4)}
            if best > EXPOSURE_MAX + 1e-9:
                rec["failures"].append("time {!r}: a fixed time ({} s) meets the relation on {:.0%} of the audit "
                                       "houses (at most 20 %)".format(name, tau, best))
        # positional tells
        group = ans0.get("group")
        if kind in ("entity", "set") and isinstance(group, list) and len(group) >= 2:
            for ordering in ORDERINGS:
                ranks = []
                for r in present:
                    a = r["answers"][name]
                    grp = [g for g in a.get("group") or () if isinstance(g, str)]
                    vals = a["value"] if isinstance(a["value"], list) else [a["value"]]
                    for v in vals:
                        rk = rank_of(r["config"], grp, v, ordering)
                        if rk is not None:
                            ranks.append(rk)
                if len(ranks) < max(8, len(present) // 2):
                    continue
                k = len(group)
                ok, detail = tell_check(ranks, k, len(ORDERINGS))
                detail.update(answer=name, ordering=ordering, ok=ok)
                rec["tells"].append(detail)
                if not ok:
                    rec["failures"].append("tell: answer {!r} is at rank {} under {} in {:.0%} of the audit houses "
                                           "(1/{} expected; p = {:.2g})".format(name, detail["rank"], ordering,
                                                                                 detail["share"], k, detail["p"]))
        # nuisance features
        labels = []
        for r in present:
            a = r["answers"][name]
            v = a["value"]
            if kind in ("entity", "set") and isinstance(a.get("group"), list):
                grp = list(a["group"])
                vals = v if isinstance(v, list) else [v]
                labels.append(json.dumps(sorted(grp.index(x) for x in vals if x in grp)))
            else:
                labels.append(json.dumps(v, sort_keys=True))
        if len(set(labels)) >= 2 and kind != "number":
            X = [feats[rows.index(r)] for r in present]
            ok, detail = tree_check(X, labels)
            rec["tree"][name] = dict(detail, ok=ok)
            if not ok:
                rec["failures"].append("nuisance features predict answer {!r}: test accuracy {:.2f} >= {:.2f} "
                                       "(split {})".format(name, detail["accuracy"], detail["bound"],
                                                           detail.get("root_split")))
        rec["answers"][name] = body
    # timing keys without a time answer: the exposure over the setup's scheduled event times
    if full.get("timing") and not rec["exposure"]:
        times = []
        for r in rows:
            events = [float(e["at_s"]) for e in (r["config"].get("events") or ()) if isinstance(e, dict) and
                      isinstance(e.get("at_s"), (int, float)) and not isinstance(e.get("at_s"), bool)]
            times.append(min(events) if events else None)
        items = [t for t in (full.get("timing") or ()) if isinstance(t, dict)]
        budget_s = float((rows[0]["chosen"].get("budget") or {}).get("time_s") or 0.0)
        if any(t is not None for t in times):
            best, tau = exposure(times, items, budget_s)
            rec["exposure"]["events"] = {"best_share": round(best, 4), "tau": tau, "bound": EXPOSURE_MAX}
            if best > EXPOSURE_MAX + 1e-9:
                rec["failures"].append("timing: a fixed time ({} s) meets the relation on {:.0%} of the audit houses "
                                       "(at most 20 %)".format(tau, best))
    rec["ok"] = not rec["failures"] and not rec["harness_errors"]
    rec["cpu_s"] = round(time.process_time() - started, 3)
    _lint(rec)
    return rec


def _place_class(row, value):
    rec = ((row["config"].get("entities") or {}).get(value) or {}) if isinstance(value, str) else {}
    rooms = row["config"].get("rooms") or {}
    if value in rooms:
        return "room:" + str((rooms[value] or {}).get("type"))
    return rec.get("type") or value


def _lint(rec):
    rec["lint"] = [{"id": "L-AUDIT", "severity": "error", "message": "9.7 audit: " + f} for f in rec["failures"]]
    rec["lint"] += [{"id": "L-AUDIT", "severity": "error", "message": "9.7 audit (harness): " + e}
                    for e in rec.get("harness_errors") or ()]
    return rec


def rate_records(rec):
    """Battery records of the arithmetic rate requirements: B5c's best constant (one per entity, word or place
    answer) and B7's fixed-timer exposure (one per time answer or the timing events). Met ("fails") within the
    bound, "passes" otherwise (a constant or a timer that wins too often is a passing cheat)."""
    out = []
    if not rec or not rec.get("ran"):
        return out
    for name, body in sorted((rec.get("answers") or {}).items()):
        if body.get("best_constant_share") is None or body.get("kind") == "branch":
            continue
        ok = body["best_constant_share"] <= (body.get("bound") or 1.0) + 1e-9
        out.append({"id": "B5.c-share@audit#{}".format(name), "family": "B5", "sub": "c-share", "seed": None,
                    "outcome": "fails" if ok else "passes", "realised": True, "labels": [], "accepted": [],
                    "rate": body["best_constant_share"], "bound": body.get("bound"),
                    "why": "best constant's share {:.2f} over {} audit builds (bound {:.2f})".format(
                        body["best_constant_share"], body.get("n"), body.get("bound") or 1.0),
                    "hint": None if ok else "the answer does not vary or is not scored: randomise `{}` and compare "
                                            "report content".format(name)})
    for name, body in sorted((rec.get("exposure") or {}).items()):
        ok = body["best_share"] <= EXPOSURE_MAX + 1e-9
        out.append({"id": "B7.exposure@audit#{}".format(name), "family": "B7", "sub": "exposure", "seed": None,
                    "outcome": "fails" if ok else "passes", "realised": True, "labels": [], "accepted": [],
                    "rate": body["best_share"], "bound": EXPOSURE_MAX,
                    "why": "the best fixed time ({} s) meets the relation on {:.0%} of the audit houses".format(
                        body.get("tau"), body["best_share"]),
                    "hint": None if ok else "the hazard resolves on a timer: randomise `{}` (with `never_p`) and use "
                                            "`timing`".format(name)})
    return out

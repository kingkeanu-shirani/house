"""The seeds a schema-4 spec is verified on (docs/design/VERIFY_HARDENING.md 9.4, 9.7, 16.1; WP5).

    verification_seeds(spec, values=None)   the coverage seeds V(spec) (9.4)
    coverage(spec, values=None)             V with its counts and whether it covers (L-COVER, 9.4)
    heldout_public(spec, spec_hash=None)    the public held-out seeds H_pub(spec) (16.1)
    heldout_stream(spec, spec_hash=None)    every derived public held-out seed in order (replacing infeasible ones)
    audit_seeds(n=200)                      the audit builds' seeds 100000 + i (9.7)
    feature_seeds(spec, features, ...)      up to 2 house-feature seeds from catalog/analysis/seed_features.json (16.1)
    answer_values(spec, seed)               {answer: hashable value} of one seed (a setup-only build)

Coverage (9.4): V starts from DEV_SEEDS = (0, 1, 2, 41, 97) and adds seeds from DEV_EXTRA = (3, 8, 13, 24, 55), then
4, 5, 6, 7, 9, 10, ... while some variant has fewer than 2 seeds or some word / entity / branch / place answer value
seen so far has fewer than 2; a candidate is added only when it raises a deficient count. The cap is 12 seeds; a spec
that cannot be covered within 12 is a lint error (coverage()["covered"] False). The variant a seed selects is
tasks.spec.variant_index (no build); answer values need a setup-only build (answer_values, the setup's audit fast
path) and are only computed for a spec that declares answers of those kinds.

Public held-out seeds (16.1): 200000 + int(sha256("house_ai.heldout:<catalog_id>:<spec_hash>:<i>").hexdigest()[:12],
16) % 800000 for i = 0, 1, ..., skipping duplicates, until 10 seeds with every variant on at least 2 (cap 16): after
the 10th, a seed is taken only when its variant is still short. The range [200000, 1000000) is disjoint from the dev
seeds, the audit seeds (100000-100199), the train range ([1,000,000, 900,000,000)) and the hidden range.

House-feature seeds (16.1): catalog/analysis/seed_features.json (tools/seed_features.py, per generator fingerprint)
indexes seeds 200000-200999 per house generation by feature (winder_stairs, multi_floor, no_garage, apartment,
imperial, pets, gen2_belongings, small_kitchen). feature_seeds picks, for each wanted feature in order, the first
indexed seed of the spec's generation with that feature that is not already a held-out seed; at most 2.
"""
import hashlib
import json
import os

from .spec import choose_variant, expand, variant_index

DEV_SEEDS = (0, 1, 2, 41, 97)
DEV_EXTRA = (3, 8, 13, 24, 55)
MAX_VERIFICATION_SEEDS = 12
MIN_PER_VALUE = 2                   # seeds per variant and per answer value (9.4)
CANDIDATE_LIMIT = 200               # candidates walked before coverage gives up (only the cap of 12 binds in practice)
COVERED_KINDS = ("word", "entity", "branch", "place")
HELDOUT_BASE = 200000
HELDOUT_SPAN = 800000
HELDOUT_N = 10
HELDOUT_CAP = 16
HELDOUT_STREAM_LIMIT = 4096         # derived seeds walked at most (duplicates and infeasible seeds skipped)
AUDIT_BASE = 100000
AUDIT_N = 200
FEATURE_SEEDS_MAX = 2
FEATURES = ("winder_stairs", "multi_floor", "no_garage", "apartment", "imperial", "pets", "gen2_belongings",
            "small_kitchen")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FEATURE_INDEX = os.path.join(ROOT, "catalog", "analysis", "seed_features.json")


def _variants(spec):
    full = expand(spec)
    return full, len(full.get("variants") or [])


def candidate_seeds():
    """The coverage candidates after DEV_SEEDS, in order: DEV_EXTRA, then 4, 5, 6, ... (skipping those already
    listed)."""
    listed = set(DEV_SEEDS) | set(DEV_EXTRA)
    for seed in DEV_EXTRA:
        yield seed
    seed = 4
    while True:
        if seed not in listed:
            yield seed
        seed += 1


def _covered_answers(spec_full):
    """The names of the answers whose values coverage counts (word, entity, branch, place; 9.4)."""
    names = set()
    scopes = [spec_full] + [v for v in spec_full.get("variants") or [] if isinstance(v, dict)]
    for scope in scopes:
        for name, body in (scope.get("answers") or {}).items():
            if isinstance(body, dict) and body.get("kind") in COVERED_KINDS:
                names.add(name)
    return sorted(names)


def _key(value):
    """A hashable form of an answer value (lists of ids as sorted tuples, dicts as JSON)."""
    if isinstance(value, list):
        return tuple(sorted(json.dumps(v, sort_keys=True) if not isinstance(v, str) else v for v in value))
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return value


def answer_values(spec, seed):
    """{answer: hashable value} of the covered answers on one seed, from a setup-only build (runner.build with the
    setup's audit fast path). An answer without a value on that seed is absent."""
    from .runner import build
    _, _, oracle = build(spec, seed, audit=True, hardening={})
    full = expand(spec)
    wanted = set(_covered_answers(full))
    out = {}
    for name, body in (oracle.get("answers") or {}).items():
        if name in wanted and "value" in body:
            out[name] = _key(body["value"])
    return out


def coverage(spec, values=None, limit=MAX_VERIFICATION_SEEDS):
    """V(spec) with its accounting: {"seeds", "variants": {index: count}, "values": {answer: {value: count}},
    "covered": bool, "deficient": [what is short]}. values: callable seed -> {answer: hashable value} (default
    answer_values when the spec declares covered answers, else nothing to count)."""
    full, n = _variants(spec)
    cid = full.get("catalog_id")
    answers = _covered_answers(full)
    if values is None and answers:
        def values(seed):
            return answer_values(spec, seed)
    variants, counts = {}, {}
    seeds = []

    def add(seed, observed):
        seeds.append(seed)
        if n:
            v = variant_index(cid, seed, n)
            variants[v] = variants.get(v, 0) + 1
        for name, value in sorted((observed or {}).items()):
            per = counts.setdefault(name, {})
            per[value] = per.get(value, 0) + 1

    def deficient():
        short = []
        for v in range(n):
            if variants.get(v, 0) < MIN_PER_VALUE:
                short.append("variant {}".format(v))
        for name in answers:
            for value, count in sorted(counts.get(name, {}).items(), key=lambda kv: str(kv[0])):
                if count < MIN_PER_VALUE:
                    short.append("{}={}".format(name, value))
        return short

    def raises(seed, observed):
        if n and variants.get(variant_index(cid, seed, n), 0) < MIN_PER_VALUE:
            return True
        for name, value in (observed or {}).items():
            if counts.get(name, {}).get(value, 0) < MIN_PER_VALUE and counts.get(name, {}).get(value) is not None:
                return True
        return False

    for seed in DEV_SEEDS:
        add(seed, values(seed) if values else None)
    walked = 0
    for seed in candidate_seeds():
        if len(seeds) >= limit or not deficient() or walked >= CANDIDATE_LIMIT:
            break
        walked += 1
        if n and not answers:
            # a variant-only spec needs no build to know whether the seed helps
            if variants.get(variant_index(cid, seed, n), 0) < MIN_PER_VALUE:
                add(seed, None)
            continue
        observed = values(seed) if values else None
        if raises(seed, observed):
            add(seed, observed)
    short = deficient()
    return {"seeds": seeds, "variants": {str(k): v for k, v in sorted(variants.items())},
            "values": {name: {str(k): v for k, v in sorted(per.items(), key=lambda kv: str(kv[0]))}
                       for name, per in sorted(counts.items())},
            "covered": not short, "deficient": short}


def verification_seeds(spec, values=None):
    """The coverage seeds V(spec) (9.4): DEV_SEEDS plus what coverage needs, at most 12."""
    return coverage(spec, values)["seeds"]


def heldout_seed(catalog_id, spec_hash, i):
    """The i-th derived public held-out seed (16.1)."""
    text = "house_ai.heldout:{}:{}:{}".format(catalog_id, spec_hash, i)
    return HELDOUT_BASE + int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16) % HELDOUT_SPAN


def _spec_hash(spec):
    from .verify import spec_hash
    return spec_hash(spec)


def heldout_stream(spec, spec_hash=None):
    """Every distinct derived public held-out seed in order (i = 0, 1, ...; duplicates skipped). The verifier
    replaces an infeasible seed (setup raised a SpecError naming a missing room, fixture or space) by the next."""
    full = expand(spec)
    cid = full.get("catalog_id")
    spec_hash = spec_hash or _spec_hash(spec)
    seen = set()
    for i in range(HELDOUT_STREAM_LIMIT):
        seed = heldout_seed(cid, spec_hash, i)
        if seed in seen:
            continue
        seen.add(seed)
        yield seed


def select_heldout(stream, catalog_id, n_variants, n=HELDOUT_N, cap=HELDOUT_CAP, skip=()):
    """Take seeds from a stream until n seeds with every variant on at least 2 (cap): after the n-th, a seed is taken
    only when its variant is short. skip: seeds left out (infeasible, or a hidden test candidate)."""
    out, per = [], {}
    skip = set(skip)
    for seed in stream:
        if len(out) >= cap:
            break
        if seed in skip:
            continue
        variant = variant_index(catalog_id, seed, n_variants) if n_variants else None
        short = n_variants and per.get(variant, 0) < MIN_PER_VALUE
        if len(out) < n or short:
            out.append(seed)
            if n_variants:
                per[variant] = per.get(variant, 0) + 1
        done = len(out) >= n and all(per.get(v, 0) >= MIN_PER_VALUE for v in range(n_variants or 0))
        if done:
            break
    return out


def heldout_public(spec, spec_hash=None, skip=()):
    """H_pub(spec) (16.1): 10 public held-out seeds with every variant on at least 2 (cap 16)."""
    full, n_variants = _variants(spec)
    return select_heldout(heldout_stream(spec, spec_hash), full.get("catalog_id"), n_variants, skip=skip)


def audit_seeds(n=AUDIT_N):
    """The audit builds' seeds (9.7): 100000 + i, i in 0..n-1."""
    return [AUDIT_BASE + i for i in range(int(n))]


def load_features(path=None):
    """The house-feature index ({"format", "fingerprint", "generations": {"1"|"2": {feature: [seeds]}}}) or None."""
    path = path or FEATURE_INDEX
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("generations"), dict) else None


def spec_generation(spec, seed=0):
    """The house generation a spec's seed builds (runner.build's rule: house.gen, else 2 for schema >= 3, else 1)."""
    from .spec import schema_of
    full = expand(spec)
    chosen, _ = choose_variant(full, seed)
    gen = (chosen.get("house") or {}).get("gen")
    if gen in (1, 2):
        return gen
    return 2 if schema_of(chosen) >= 3 else 1


def feature_seeds(spec, features, exclude=(), index=None, limit=FEATURE_SEEDS_MAX):
    """Up to `limit` seeds from the feature index: for each wanted feature in order, the first seed of the spec's
    generation that has it and is not excluded (16.1). [] without an index."""
    index = index if index is not None else load_features()
    if not index or not features:
        return []
    gen = str(spec_generation(spec))
    table = (index.get("generations") or {}).get(gen) or {}
    out, taken = [], set(exclude)
    for feature in features:
        if len(out) >= limit:
            break
        for seed in table.get(feature) or ():
            if seed not in taken:
                out.append(seed)
                taken.add(seed)
                break
    return out


def wanted_features(spec, floors=(), actions=()):
    """The house features a spec's held-out seeds should include (16.1): winder stairs when the reference crossed
    floors (floors: the floors its trace visited) or the spec names stairs, imperial units when it measures, weighs
    or reports a quantity (actions: the trace's actions), pets and multiple floors when the house asks for them."""
    full = expand(spec)
    text = json.dumps(full).lower()
    wanted = []
    if len(set(floors)) > 1 or "stairs" in text:
        wanted.append("winder_stairs")
    if set(actions) & {"measure", "weigh", "read_scale", "report"} or '"measurement"' in text:
        wanted.append("imperial")
    require = (full.get("house") or {}).get("require") or []
    if "pets" in require or (full.get("house") or {}).get("pets") not in (None, "none"):
        wanted.append("pets")
    if (full.get("house") or {}).get("floors", 1) not in (None, 1) or "upstairs" in text:
        wanted.append("multi_floor")
    return [f for f in FEATURES if f in wanted]

"""Index the house-feature seeds of VERIFY_HARDENING 16.1 (WP5): catalog/analysis/seed_features.json.

usage: python tools/seed_features.py [--first 200000] [--count 1000] [--gens 1 2] [--workers N] [--out FILE]
       python tools/seed_features.py --check [FILE]      exit 1 when the index is missing or its generator
                                                          fingerprint is not the current one

Every seed of [first, first + count) is generated with the default house arguments for each house generation, and
its features are recorded: winder_stairs (a staircase with winders), multi_floor (metadata floors above 1, else
indoor rooms on more than one floor), no_garage, apartment, imperial (metadata units), pets, gen2_belongings (a
generation-2 house whose residents own things), small_kitchen (a kitchen of at most SMALL_KITCHEN_M2). The
verifier's held-out stage adds up to two of these seeds per spec, chosen from the features its reference touches
(house_sim/tasks/seeds.py feature_seeds).
Generated once per generator fingerprint (about 40 CPU-minutes for 2,000 houses); deterministic; LF output.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FORMAT = "house_ai.seed_features/1"
FIRST = 200000
COUNT = 1000
SMALL_KITCHEN_M2 = 9.0


def generator_fingerprint():
    """The generator fingerprint the house caches use (generator/disk_cache.py), else a digest of the generator and
    type data files."""
    try:
        from house_sim.generator import disk_cache
        return disk_cache.fingerprint()
    except (ImportError, AttributeError, OSError):
        import hashlib
        digest = hashlib.sha256()
        for folder in ("house_sim/generator", "house_sim/data/types", "house_sim/data/generator"):
            base = os.path.join(ROOT, folder)
            for name in sorted(os.listdir(base)) if os.path.isdir(base) else ():
                path = os.path.join(base, name)
                if os.path.isfile(path):
                    with open(path, "rb") as stream:
                        digest.update(name.encode("utf-8") + stream.read())
        return digest.hexdigest()


def features_of(config, gen):
    """The feature names of one generated house."""
    out = []
    rooms = config.get("rooms") or {}
    meta = config.get("metadata") or {}
    if any(c.get("kind") == "stairs" and (c.get("winders") or 0) > 0 for c in (config.get("connections") or {}).values()
           if isinstance(c, dict)):
        out.append("winder_stairs")
    floors = meta.get("floors")
    if not isinstance(floors, int) or isinstance(floors, bool):
        floors = len({r.get("floor", 0) for r in rooms.values() if isinstance(r, dict) and not r.get("outdoor")})
    if floors > 1:
        out.append("multi_floor")       # the dwelling's own floors (an apartment's building lobby is not one)
    if not any(isinstance(r, dict) and r.get("type") == "garage" for r in rooms.values()):
        out.append("no_garage")
    if meta.get("dwelling_type") == "apartment":
        out.append("apartment")
    if meta.get("units") == "imperial":
        out.append("imperial")
    if config.get("pets"):
        out.append("pets")
    if gen == 2 and any(isinstance(e, dict) and e.get("owner") for e in (config.get("entities") or {}).values()):
        out.append("gen2_belongings")
    for room in rooms.values():
        size = room.get("size_m") if isinstance(room, dict) else None
        if room.get("type") == "kitchen" and isinstance(size, list) and len(size) == 2 and \
                float(size[0]) * float(size[1]) <= SMALL_KITCHEN_M2:
            out.append("small_kitchen")
            break
    return out


def _job(args):
    seed, gen = args
    from house_sim.generator import generate_house
    try:
        config = generate_house(seed, gen=gen)
    except Exception as exc:                # noqa: BLE001  (recorded: an infeasible default house)
        return seed, gen, None, "{}: {}".format(type(exc).__name__, exc)
    return seed, gen, features_of(config, gen), None


def _init():
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    from house_sim.kernel.plugins import load_domains
    load_domains()


def build_index(first=FIRST, count=COUNT, gens=(1, 2), workers=1, progress=None):
    jobs = [(seed, gen) for gen in gens for seed in range(first, first + count)]
    rows = []
    if workers <= 1:
        _init()
        for i, job in enumerate(jobs, 1):
            rows.append(_job(job))
            if progress:
                progress(i, len(jobs))
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init) as pool:
            for i, row in enumerate(pool.map(_job, jobs, chunksize=8), 1):
                rows.append(row)
                if progress:
                    progress(i, len(jobs))
    generations = {}
    errors = {}
    for seed, gen, feats, error in rows:
        table = generations.setdefault(str(gen), {})
        if error is not None:
            errors.setdefault(str(gen), {})[str(seed)] = error[:200]
            continue
        for feature in feats:
            table.setdefault(feature, []).append(seed)
    for table in generations.values():
        for feature in table:
            table[feature].sort()
    return {"format": FORMAT, "fingerprint": generator_fingerprint(), "first": first, "count": count,
            "gens": list(gens), "small_kitchen_m2": SMALL_KITCHEN_M2,
            "generations": {g: {f: generations[g][f] for f in sorted(generations[g])} for g in sorted(generations)},
            "counts": {g: {f: len(v) for f, v in sorted(generations[g].items())} for g in sorted(generations)},
            "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--first", type=int, default=FIRST)
    parser.add_argument("--count", type=int, default=COUNT)
    parser.add_argument("--gens", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--out", default=os.path.join(ROOT, "catalog", "analysis", "seed_features.json"))
    parser.add_argument("--check", nargs="?", const="", default=None)
    args = parser.parse_args(argv)
    if args.check is not None:
        path = args.check or args.out
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError) as exc:
            print("seed features: missing or unreadable ({})".format(exc))
            return 1
        current = generator_fingerprint()
        if data.get("fingerprint") != current:
            print("seed features: stale (index {} vs generator {})".format(str(data.get("fingerprint"))[:12],
                                                                          current[:12]))
            return 1
        print("seed features: current ({} generations)".format(len(data.get("generations") or {})))
        return 0
    started = time.monotonic()

    def progress(i, total):
        if i % 200 == 0 or i == total:
            print("{}/{} houses ({:.0f} s)".format(i, total, time.monotonic() - started), flush=True)
    index = build_index(args.first, args.count, tuple(args.gens), args.workers, progress)
    with open(args.out, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(index, indent=1, sort_keys=False) + "\n")
    print("wrote {} ({})".format(args.out, json.dumps(index["counts"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

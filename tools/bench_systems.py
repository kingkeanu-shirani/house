"""Benchmark simulated-time systems on generated houses (all loaded domains).

usage: python tools/bench_systems.py [substeps_per_seed] [seeds...] [--domain name] [--camera room|geometric]
       python tools/bench_systems.py --day [seeds...] [--domain name] [--camera room|geometric] [--no-probes]
Prints average milliseconds per 30 s substep per system (periodic systems are amortized:
they run once per period, exactly as in the engine), plus the engine-level cost of a
1-hour wait. Target: whole engine < 15 ms per substep on a ~900-entity house.
--camera geometric runs the houses with the robot's posed cameras (kernel v3.0; camera.watch then
works) and also prints the cost of an observation, oracle and realistic, on the first seed
(best of 5; TIER1_CAMERA 16: the realistic geometric overhead target is 3 ms, its test bound 10 ms).

--day (kernel v3.1, TIER1_SWEEP 6.7 and 20): a quiet simulated day per seed (default 0 1 2): the engine's wall time of
one `wait 86400` on a fresh house (the line "quiet day (seeds ...): mean = X s", which the tier-1 gate's G7 records;
budget: no slower than the previous gate, 3.5 s once the domain idle probes land), then the same day again with
every system timed, as ms per simulated day per system (systems an idle probe skipped cost only their probe, which
is not timed separately). --no-probes turns the idle probes off (HOUSE_SIM_IDLE_PROBES=0 does the same).
"""
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import plugins  # noqa: E402
from house_sim.kernel.engine import HouseEnv, Tick  # noqa: E402


def _observe_ms(seed, camera, perception, repeats=5, calls=10):
    """Best-of-repeats milliseconds per observe() on a fresh house (the step view of the reset is cached first)."""
    env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False, camera=camera,
                   perception=perception)
    env.observe()
    best = None
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(calls):
            env.observe()
        spent = (time.perf_counter() - start) / calls
        best = spent if best is None else min(best, spent)
    return 1000.0 * best


def _day_wall(seed, camera):
    env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False, camera=camera)
    start = time.perf_counter()
    env.step("wait", seconds=86400)
    return time.perf_counter() - start


def day(seeds, camera=None, domain=None):
    """The quiet-day benchmark: wall seconds per seed, then per-system ms per simulated day (mean over the seeds)."""
    walls = [_day_wall(seed, camera) for seed in seeds]
    print("quiet day (seeds {}): mean = {:.3f} s".format(" ".join(str(s) for s in seeds), sum(walls) / len(walls)))
    print("per seed: " + ", ".join("seed {} {:.3f} s".format(seed, wall) for seed, wall in zip(seeds, walls)))
    totals = defaultdict(float)
    original = list(plugins.SYSTEMS)

    def timed(name, fn):
        def run(w, dt, tick):
            start = time.perf_counter()
            try:
                return fn(w, dt, tick)
            finally:
                totals[name] += time.perf_counter() - start
        return run

    plugins.SYSTEMS[:] = [(order, name, max_dt, timed(name, fn)) for order, name, max_dt, fn in original]
    try:
        for seed in seeds:
            _day_wall(seed, camera)
    finally:
        plugins.SYSTEMS[:] = original
    print("systems per simulated day (mean of the seeds; idle probes {}):".format("on" if plugins.IDLE_PROBES[0] else "off"))
    for name, value in sorted(totals.items(), key=lambda kv: -kv[1]):
        if domain and not name.startswith(domain + ".") and not (domain == "kernel" and "." not in name):
            continue
        print("{:10.1f} ms  {}".format(1000 * value / len(seeds), name))


def main(argv):
    domain = None
    if "--domain" in argv:
        i = argv.index("--domain")
        domain = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    camera = None
    if "--camera" in argv:
        i = argv.index("--camera")
        camera = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
        if camera not in ("room", "geometric"):
            raise SystemExit("--camera must be room or geometric")
    if "--no-probes" in argv:
        argv = [a for a in argv if a != "--no-probes"]
        plugins.IDLE_PROBES[0] = False
    if "--day" in argv:
        argv = [a for a in argv if a != "--day"]
        plugins.load_domains()
        day([int(a) for a in argv] or [0, 1, 2], camera, domain)
        return
    substeps = int(argv[0]) if argv else 40
    seeds = [int(a) for a in argv[1:]] or [0, 1, 2, 3]
    plugins.load_domains()
    totals, runs = defaultdict(float), 0
    for seed in seeds:
        env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False, camera=camera)
        w = env.world
        acc = defaultdict(float)
        for i in range(substeps):
            w.begin()
            w.set_scalar("time_s", round(w.s["time_s"] + 30.0, 3))
            tick = Tick(env, i)
            for _, name, max_dt, fn in plugins.SYSTEMS:
                period = plugins.SYSTEM_PERIODS.get(name)
                start = time.perf_counter()
                if period:
                    acc[name] += 30.0
                    if acc[name] + 1e-9 >= period:
                        fn(w, acc[name], tick)
                        acc[name] = 0.0
                else:
                    sub = 30.0
                    while sub > 1e-9:
                        part = min(sub, max_dt)
                        fn(w, part, tick)
                        sub -= part
                totals[name] += time.perf_counter() - start
            w.commit()
            runs += 1
    rows = sorted(totals.items(), key=lambda kv: -kv[1])
    print("substeps measured:", runs, "| systems:", len(plugins.SYSTEMS), "| total ms/substep: {:.2f}".format(1000 * sum(totals.values()) / runs))
    for name, value in rows:
        if domain and not name.startswith(domain + ".") and not (domain == "kernel" and "." not in name):
            continue
        period = plugins.SYSTEM_PERIODS.get(name)
        print("{:8.3f} ms  {}{}".format(1000 * value / runs, name, "  (period {:.0f}s)".format(period) if period else ""))
    env = HouseEnv(generate_house(seeds[0]), seed=seeds[0], trace=False, observe_on_step=False, camera=camera)
    start = time.perf_counter()
    env.step("wait", seconds=3600)
    print("engine: 1 h wait = {:.2f} s".format(time.perf_counter() - start))
    if camera:
        oracle = _observe_ms(seeds[0], camera, "oracle")
        realistic = _observe_ms(seeds[0], camera, "realistic")
        line = "observe ({} camera, seed {}): oracle {:.2f} ms, realistic {:.2f} ms".format(camera, seeds[0], oracle, realistic)
        if camera == "geometric":
            room = _observe_ms(seeds[0], "room", "realistic")
            line += "; realistic geometric overhead vs realistic room {:+.2f} ms".format(realistic - room)
        print(line)


if __name__ == "__main__":
    main(sys.argv[1:])

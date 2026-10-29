# Handoff WP9 SETUP RANDOMISATION (VERIFY_HARDENING section 25; sections 9.2, 9.7, 22.1, 22.5 (6) and (7))

Files: `house_sim/tasks/setup.py` (changed), `house_sim/generator/place.py` (changed), `house_sim/generator/disk_cache.py`
(new), `.gitignore` (changed), `tests/test_setup_random.py` (new, 41 tests). Result:
`catalog/analysis/phase2/WP9.json`. Nothing else was edited.

## What landed

1. **New setup ops** (9.2; every draw from the setup RNG `random.Random("setup:<catalog_id>:<seed>")`, in every
   schema; no legacy spec uses them):

   | Op | Form | Binds |
   |---|---|---|
   | `shuffle` | `{"op": "shuffle", "var": "order", "from": [...] \| "{group}"}` | `order` (a seeded permutation) and `order_1 .. order_n` |
   | `sample` | `{"op": "sample", "var": "dry", "from": "{plants}", "k": 2 \| {"min": 1, "max": 3}, "rest": "wet"}` | `dry` (a subset of a uniformly drawn size, members in the order of `from`; default k 1 .. n-1), `rest` (optional) the others |
   | `arrange` | `{"op": "arrange", "items": "{bottles}", "on": "{counter}" \| ["{c1}", "{c2}"], "order": "shuffled" \| "given", "gap_m": [0.05, 0.25], "var": "row"}` | `row` (optional): the left-to-right order, surface by surface |
   | `time` | `{"op": "time", "var": "onset", "min": 600, "max": 3000, "never_p": 0.25, "step_s": 30}` | a whole-second time `min + step_s * k` up to `max`, or `null` with probability `never_p` |
   | `choose` + `weights` | `{"op": "choose", "var": "burner", "from": [...], "weights": [3, 1, 1]}` | one option with probability weight / sum (one `rng.random()`); without `weights` exactly the old `rng.choice` |

   An `event` op whose `at_s` is `null` (a `time` op that drew "never") is not scheduled. Validation errors are
   `SpecError`s naming the op and key.
2. **`arrange`** (`Placer.arrange`): existing things placed on surfaces left to right (increasing `spos` x) in a seeded
   order -- a permutation of the *sorted* ids, so the result depends neither on the list order nor on roles -- with a
   random lead gap from [0, hi] and random gaps from `gap_m` between neighbours, each at the surface's middle depth,
   else its near or far edge, past whatever else stands on it; a thing that no longer fits wraps to the next surface
   of the list. A pass that runs out of room retries with half the gaps, then none; if nothing fits the op is a
   `SpecError` and the Placer leaves every record as it was. Held things, unknown ids, non-surfaces, a surface that
   stands on one of the items, duplicate items and bad gaps are refused. Positions are rounded up to the millimetre,
   so a rounded place never overlaps a neighbour.
3. **Schema-4 seeded placement order** (9.2 "spawn with count ... in a seeded random order (Placer receives a
   permutation from the setup RNG)"). `SetupCtx` builds `Placer(config, registry, rng, shuffle=schema >= 4)`:
   * `spawn` with `count` > 1 binds `var`, `var_1 .. var_n` to the members in a seeded random order. The members are
     alike (one type, one props dict), so permuting which member each role names is placing them in a seeded order;
     it also covers containers, where sight's packing orders things by blake2b of their id, and the floor.
     Measured over 200 setup seeds: the rank of `var_1` on a counter (by spos), in a cabinet (packing order) and on the
     floor (scan order, 120 seeds) is uniform within 1/3 +- 0.1; schema 3 keeps it at one fixed rank (the old tell).
   * `ctx.placer.order(items)` (a permutation in shuffle mode, the list unchanged otherwise -- no draw) and
     `ctx.sample_roles(value, pool, k=None)` (`"random"` -> a seeded subset, a list -> checked against the pool,
     `None` -> `[]`) are the hooks for scenario helpers that place look-alike members or take role lists (the plant
     helper's `needs_water`, the chemical helper's bottle list): the domain packages adopt them (requests below).
4. **Placement by look** (closeout_INT3's request to WP9, decided here). `Placer.look_ring(record)`: a thing whose
   identity the robot cannot see is placed on the floor with the keep-out ring any thing of its look might need (1),
   not by its true fragility or heat, so twins that look alike land alike: every unfamiliar (`novel`) thing in every
   schema (no legacy spec, setup or generated house holds one), and in shuffle mode (schema 4) every look-alike group
   member (an explicit `looks_like`, or a type in a registry group of two or more types). Used by `Placer.floor_item`
   and setup's `move` to a room. The INT3 repro (seed 1: a novel smartphone at [6, 0], the novel kettle, hair dryer,
   remote, spray and trigger bottles at [5, 0]) now puts all six, and a fragile and a sturdy generic thing of that
   look, on one cell on seeds 0-2; on HEAD's code seed 1 differs (checked in a `git archive HEAD` copy).
5. **Audit fast path** (9.2, 9.7): `apply_setup(..., audit=True)` runs every op that can draw from the setup RNG, bind
   a name or place a thing exactly as a full build does, so the bindings and every placement *are* the full build's;
   it skips the physics lint after the ops, the staged supports' sag cues, `check_physics` ops and `prior_knowledge`
   ops that draw nothing (every room by id). Helpers see `ctx.audit` and may skip work that binds and places nothing.
   The bindings carry `"_audit_build": true`; such a config is for audits only, never for an episode.
   `setup.audit_diff(full_config, full_bindings, audit_config, audit_bindings, limit=20)` is the correctness audit
   (22.5: a wrong optimisation is a harness error): it compares every binding and the whole config except the
   sections and fields the fast path legitimately leaves out (`AUDIT_FREE_SECTIONS`, `AUDIT_FREE_FIELDS`,
   `AUDIT_FREE_BINDINGS`). Over every authored spec, seeds 0 1 2 41 97, own mode: **0 differences in 1,215 builds**.
6. **Disk cache of generated houses** (`house_sim/generator/disk_cache.py`, 9.7 and 22.5 (7)). `house(seed, kwargs)`
   equals `generate_house(seed, **kwargs)` exactly (types and dict key order, which is part of a house's behaviour),
   read from `<root>/<fingerprint[:16]>/<seed>_<sha256(key)[:16]>.json.gz` when built before, else generated and
   stored (temporary file + `os.replace`, so concurrent readers never see half a file); always a fresh object.
   * Root: `root=`, else `HOUSE_SIM_HOUSE_CACHE` (`off` disables the disk), else `reports/cache/houses/` (git-ignored).
   * Fingerprint: sha256 over the cache format, Python major.minor, `plugins.domain_modules()` (HOUSE_SIM_DOMAINS) and
     every source and data file of `house_sim/` except `eval/`, `legacy_v1/` and `__pycache__` (path and content).
   * Correctness audit: the first cache hit per process and fingerprint directory is compared with a fresh build
     (`verify="once"`; `"always"`, `"never"`); a difference raises `CacheError`. `check(seed, kwargs)` compares one
     entry on demand. JSON + gzip rather than pickle: a planted file can at worst change a house, which the check
     exposes, never run code. A house that does not survive a JSON round trip exactly is returned fresh, not cached.
   * `prune(keep=2)` removes the directories of older fingerprints (only its own `.json.gz` / `.tmp` files).
   * Environments built from a cached and a fresh house have equal state and observation digests, also after a step,
     in oracle / realistic x room / geometric (test).
7. **`.gitignore`**: `reports/cache/` (the verifier's house, fork and run caches); `build/` and `dist/` anchored to the
   project root (`/build/`, `/dist/`), so a build/ or dist/ folder inside `house_sim/`, `tests/` or `tools/` is no
   longer hidden from git and the gate (closeout_REV finding 1, the ignored-domain attack's folder names). The privacy
   patterns (`results/`, `private_results/`, `*__PRIVATE*`, `.house_ai/`, `split_key`, ...) are unchanged: they keep
   applying everywhere.

## Interfaces (signatures)

```python
# house_sim/tasks/setup.py
apply_setup(config, ops, registry, seed, spec, bindings=None, camera=None, perception=None, audit=False) -> bindings
audit_diff(full_config, full_bindings, audit_config, audit_bindings, limit=20) -> ["path: full != audit", ...]
audit_skips(ctx, kind, op) -> bool                     # the ops the fast path skips
SetupCtx(config, registry, rng, seed, spec, camera=None, perception=None, audit=False)
    .schema, .audit, .placer (Placer, shuffle = schema >= 4), .sample_roles(value, pool, k=None, name="roles")
sample_roles(ctx, value, pool, k=None, name="roles") -> [members in pool order]
draw_size(ctx, k, n, name="sample") -> int; draw_subset(ctx, pool, k, name="sample") -> [...]
weighted_index(rng, weights) -> int
spec_schema(spec) -> int; SCHEMA_RANDOM = 4
OPS["shuffle" | "sample" | "arrange" | "time"]; op_choose takes "weights"; op_event skips "at_s": None

# house_sim/generator/place.py
Placer(config, registry, rng, shuffle=False)
    .permutation(items) -> list           # always draws
    .order(items) -> list                 # draws only in shuffle mode
    .arrange(items, surfaces, order="shuffled", gap_m=(0.02, 0.1)) -> [(eid, surface)] | None   # ValueError on bad args
    .look_ring(record) -> 1 | None        # free_floor_cell's ring for the record's look
    .lookalike_member(record) -> bool

# house_sim/generator/disk_cache.py
house(seed, kwargs=None, root=None, verify="once") -> config
check(seed, kwargs=None, root=None) -> True | False | None
fingerprint(refresh=False, package=None) -> hex; source_files(package=None) -> [(rel, Path)]
cache_root(root=None) -> Path | None; entry_path(seed, kwargs=None, root=None) -> Path | None
house_key(seed, kwargs=None) -> str; prune(keep=2, root=None) -> [removed dir names]
CacheError; STATS {"hits", "misses", "writes", "verified", "uncacheable", "corrupt"}; ENV_DIR = "HOUSE_SIM_HOUSE_CACHE"
```

## Deviations from the spec

* **spawn count**: the seeded order is a permutation of the role bindings after placement, not of the positions. For
  alike members the two are the same in distribution; the binding form also randomises ranks inside containers
  (sight packs by a hash of the id) and on floors, where swapping positions would not.
* **Audit fast path**: it does not prune ops by a dependency analysis ("only the ops needed to bind the answers and
  place the groups"); it runs every op that draws, binds or places, so its results equal the full build's by
  construction (a pruned stream would shift every later draw and the audits would measure other houses). Measured
  saving: 15 % of setup time over 162 builds of 81 authored specs on cached houses (23.5 ms -> 20.0 ms mean); the
  rest is in scenario helpers, which may use `ctx.audit`. An audit build on a cached house costs about 27 ms (7 ms
  cache read + 20 ms setup), so 200 audit builds take about 5-6 CPU-s per spec, under 9.7's estimate of 12.
* **Disk cache format**: gzip'd JSON (about 117 KB a house, 7 ms to read) instead of a pickle (0.5 MB, 3 ms), for the
  planted-file reason above; key order is preserved and checked. `house()` takes optional `root` and `verify`.
* **Extras beyond 9.2's table**: `shuffle` binds `var_i`; `sample` has `rest` and a default k; `time` has `step_s`;
  `arrange` has `order: "given"`, `var`, a default `gap_m` [0.02, 0.1], depth fallbacks and narrower-gap retries.
* **Placer API**: `order` / `permutation` instead of a permutation argument to the constructor; no slot permutation
  of already-placed members (helpers order their roles before placing).
* **closeout_INT3's placement request** is decided as placement by look (`look_ring`), in every schema for unfamiliar
  things and in schema 4 for look-alike members, rather than leaving it to seeded arrangements alone: a seeded order
  does not stop a fragile twin's keep-out ring from moving it.

## Legacy invariance (P10; rules.json: "no legacy house, digest or verdict may change")

`legacy_diff.py` (scratch) built every authored spec on seeds 0 1 2 41 97, own mode and forced realistic, in two
snapshot trees of the shared working tree that differ only in WP9's files (HEAD's `setup.py` / `place.py` vs these):
**0 of 2,430 builds differ** in the built config, the bindings or the task. Generation is unchanged by construction
(the generator's Placer is never in shuffle mode and no generated house holds an unfamiliar thing; a test checks the
second on a generated house); `tests/test_generator_compat.py` (gen-1 digests) and `tests/test_room_golden.py` pass.
outcome_changes: none.

## Known gaps

* Scenario helpers still place look-alike members in creation order and take fixed role lists until each domain
  adopts `ctx.placer.order` and `ctx.sample_roles` (requests); schema-4 specs that rely on such helpers will show the
  tell in 9.7's audit. Separately spawned look-alikes (one spawn op each) keep their op order: authors use `arrange`
  or `count`.
* `arrange` places on surfaces only, not onto the shelves of a container (inside containers the rank follows sight's
  packing by id hash, which `order` and spawn count randomise).
* Placement by look covers the item's own cell; kernel `geometry.placement_hazards` still gives a placed fragile
  thing a hazard ring that shapes where *later* things go (kernel file, not WP9's). No registry look-alike group
  currently mixes fragile and sturdy types, so only props-made fragility is affected.
* The disk cache defaults to `reports/cache/houses/` inside the project, which is in a OneDrive-synced folder on the
  owner's machine; set `HOUSE_SIM_HOUSE_CACHE` to a local folder to keep the about 50 MB per fingerprint off the
  sync. Nothing was written there by this package (tests and benchmarks used temporary folders).
* The fingerprint covers `house_sim/` only; generation reading a file outside it (none known) would not invalidate
  the cache. The once-per-process check bounds it.
* closeout_REV finding 1's gate part (G10 / TREE fail on ignored files under the source trees) and finding 2 (an
  isolated copy's own .gitignore) remain the gate tool's; the kernel part (load_domains imports only DOMAINS) landed
  with WP1.

## Phase-1 carry-over

* Closed: closeout_INT3's request to "the VERIFY_HARDENING WP9 owner" (placement by a never-tier prop) -- item 4,
  regression tests `LookRingTests.test_unfamiliar_twins_land_alike` (fails on HEAD: seed 1 differs),
  `test_a_moved_unfamiliar_thing_lands_by_its_look`, `test_schema_4_lookalike_members_land_by_their_look`.
* Partly closed: closeout_REV finding 1 (confirmed in closeout_VER2): `.gitignore` no longer hides `build/` / `dist/`
  folders in the source trees -- `GitignoreTests` (on HEAD's .gitignore `git check-ignore` reported
  `house_sim/systems/build/__init__.py` ignored by `build/`). The gate part is the gate tool's (not_done).
* Nothing else open lies in WP9's files: the 10 rows of `closeout_breakages.json` are domain-side, the other REV
  findings are the gate tool's, py39_lint's and domains'.

## Requests

* WP-D (every domain with scenario helpers): place look-alike group members in `ctx.placer.order(members)` order;
  accept `"random"` or a `sample` binding for role lists through `ctx.sample_roles(value, pool, k)`; skip work that
  binds and places nothing when `ctx.audit`.
* WP6: audit builds = `disk_cache.house(100000 + i, house_kwargs)` then `apply_setup(config, ops, registry, seed,
  spec, camera=..., perception=..., audit=True)`; run `setup.audit_diff` against a full build on one seed per spec
  (harness error on any difference) and rely on `verify="once"` (or `disk_cache.check`) for the house cache.
* WP5: optionally build verification seeds from `disk_cache.house` (runner.build is WP5's).
* WP8: document the new ops in docs/TASK_SPEC.md and AUTHORING_GUIDE.md; the `--draft-hardening` answer draft should
  list bindings drawn by `shuffle`, `sample`, `time`, `arrange` and weighted `choose`.
* Gate tool (K-GATE / WP10): closeout_REV findings 1 (gate part) and 2.

## Results (shared tree, 2026-10-29, with the other P2-S1 packages' concurrent edits present)

* `tests/test_setup_random.py`: 41 tests, green normal, `HOUSE_SIM_STRICT=1` and `HOUSE_SIM_DOMAINS=core` (1 skipped
  there: the authored specs need every domain).
* `green_tests.py 3`: 5,233 tests per mode, 0 failures normal and strict (76 files; test_generator_compat and
  test_room_golden included).
* All 243 authored specs in their own mode (combined file, `--check --workers 3`): 243 passed, 0 failed.
* `py39_lint`: the four files, 0 violations.

## How to test

```
python -m unittest tests.test_setup_random                       # 41 tests; also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core
python tools/py39_lint.py house_sim/tasks/setup.py house_sim/generator/place.py house_sim/generator/disk_cache.py tests/test_setup_random.py
python -m unittest tests.test_generator_compat tests.test_room_golden
python tools/orchestration/green_tests.py 3
python -m house_sim --check <all authored specs combined> --workers 3
```

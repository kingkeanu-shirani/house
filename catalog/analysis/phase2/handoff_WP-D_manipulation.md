# Handoff WP-D manipulation (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; sections 6.2-6.5, 8.2, 10.3, 13.3)

Files (only these): `house_sim/systems/manipulation/` (`__init__.py`, `common.py`, `fasteners.py`, `goals.py`,
`jobs.py`, `scenarios.py`, `skills.py`, `world.py`, `wrapping.py`; new `records.py`, `verify.py`),
`house_sim/data/verify/watch/manipulation.json` (new), `house_sim/data/speech/truth_manipulation.json` (new),
`docs/domains/manipulation.md` (new section 19 with the "Latched fields" table), `tests/test_manipulation_hardening.py`
(new, 28 tests), and in `catalog/analysis/phase2/`: `WP-D_manipulation.json`, this handoff,
`WP-D_manipulation_reviewed.json` (the two proposed golden rows). No type, vocab or spec file changed.

## What landed

1. **Field classes (6.3, L-WATCH part 2).** `watch/manipulation.json` classifies every property of the domain's type
   data (`python tools/field_census.py --unclassified --domain manipulation`: 0, was 89 names / 224 pairs) and every
   field the domain's verbs and systems write (278 names, 14 per-type entries). Kernel classes it overrides for this
   domain's types, with reasons: `text` (a price tag's printed price: identity, not bookkeeping), `heat_limit_c` /
   `max_temp_c` / `dries_uncapped_s` (rated limits and static props: identity, not process), `seated` (placement),
   `length_m` of consumable rolls (tape, wrap, twine: content, so tearing wrap off is a consumption the envelope can
   allow, not an identity change). Container-derived fields (WP1-fix's request): `receptacles.*`, `stack_count`,
   `attached_parts` are bookkeeping (mirrors of other things' placements); rain pooled on a cover (`pooled_water_l`) is
   content with `guard: mess`. `water_sensitive`: 25 paper / cardboard / framed-print types. `writes`: the 165 field
   names this domain writes on things of any type (see Requests: the ledger applies a domain's `fields` only to its
   own types).
2. **Writer classes and causes (6.2).** Every system tagged `writer="physical"` explicitly; causes: falls (the
   falling / rolling thing), breaks (the broken thing), timers (the thing changed), wind (a blown-off cover: cover +
   target; a light item: none on purpose -- whoever took its weight off is unknown, so the conservative default
   applies), jobs (a mount: the item). A physical change nobody of the robot started is now `robot_attributable`
   False instead of None. The co-carry partner's own steps (`support mode=follow`) are `volition`
   (`manipulation.partner`): no `house.agent_moved` for a partner who walks beside the load.
3. **Relation records kept true (8.2 latches; a world fix, P10).** New `records.py`. Found by probes on HEAD: core
   `pick_up` of a stacked plate left the base's `stack_count` 2 and the plate a `stack_member` in the robot's hand;
   core `pick_up` of a seated dumbbell left the rack's saddle holding it and the dumbbell `inserted`; core `pick_up` of
   a packed book kept its `pack_seq`; lifting a prop wedge left the door `propped_by` it; `detach` of a cover, a clip or
   a wrap sheet left the bowl covered, the bag clipped shut (`sealed`) and the vase wrapped (and wrote a made-up
   `connector` record onto the cover). Now `records.reconcile(w)` runs as an after-hook (order 995) of every verb of
   the domain and of the core verbs it hooks (pick_up, place, hand_over, receive, move, approach, walk_to, open,
   close) and inside the falls and wind systems: a thing that moved drops its own placement records that predate the
   move and no longer hold, and the thing it left forgets it. For schema 4 the same rules (state only) are
   `@derived_field`s: `stack_member`, `stack_count`, `inserted`, `pack_seq`, `pack_z_m`, `pack_on`, `cover_coverage`,
   `clipped_by`, `closure` (only names no other domain writes; `wrapped`, `covered_by`, `covered`, `seated`,
   `receptacles`, `propped`, `sealed` are shared names, kept true by the world rule). `docs/domains/manipulation.md`
   section 19 has the "Latched fields" table L-LATCH reads (exactly these nine, all registered).
4. **Mess flows (6.5).** A mess-parcel thing (`core.mess_parcel`) moved by a verb or system of this domain logs
   `world.mess_flow` from where the flow log last left it (else its step-start place, else `created:<verb>`) to where
   it is, with its origin; idempotent with core's own pick_up / place flows. Refuse this domain makes: the unwrapped
   wrap (`waste: true`, as before) and now also a peeled label or price sticker (`remove_label`'s scrap gets
   `waste: true`: a world fix, it is litter). The domain registers no mess kind (its refuse, debris and liquids are
   cleaning's / plumbing's / textiles' kinds; cleaning's `waste_item` measure now exists and judges these flows).
5. **Settings skills (10.3) and damaging verbs (B14).** `verify.py` + the reference skill `manipulation.operate(target,
   verb, hold=False, **args)` (`skills.py`; destination `"here"` = the robot's room): `wrapped` (unwrap),
   `zipper.closed_frac` (unzip / zip), `velcro.engaged`, `rolled` (unroll / roll up), `cap.on` (untwist); and for
   B14's `damaged:<e>.<f>` probe, through the same registry, the condition fields `broken` (throw a fragile portable
   thing hard onto the floor) and `torn` (tear a document / paper / cloth, not a roll).
6. **Truth mappings (13.3).** `truth_manipulation.json`: states `wrapped`, `unwrapped`, `zipped` (>= 0.95),
   `unzipped` (<= 0.05), `upside_down`, `on_side`, `coiled`, `rolled_up`, `flattened`, `inflated`, `deflated`,
   `stacked`, `packed`; issues `tangled`, `knotted`, `peeling_label`, `blown_away`, `loose_cap`. No record -> unknown
   (never false). No acts (they need lexicon verbs: request to WP4).
7. **Phase-1 carry-over.** closeout_breakages "manipulation, people": `manipulation.items` registered with
   `dialogue.route(..., takes_env=True)` and reads `dialogue.known_record` (the domain's `_known_record` copy now
   delegates): a gone thing with hidden truth gets the plain topic like one out of view. closeout_breakages
   "navigation_robot, food, manipulation (optional)": `common.novel_unidentified` / `refuse_novel` delegate to
   `Ctx.unfamiliar` / `Ctx.refuse_unfamiliar`. closeout_REV / VER2: no finding lies in this domain's files.
8. **WP9's request.** `manipulation.displace` moves its items in `ctx.placer.order(ids)` (a seeded order in schema 4,
   the given order otherwise -- legacy builds unchanged). No helper of this domain places look-alike members or takes
   role lists; the helpers that bind and place nothing write config sections `setup.audit_diff` compares, so they run
   in audit builds too (skipping them would be a harness error).

Not applicable (with reasons, also in docs section 19): structured readings (no measuring instrument: `support
mode=heft` is a felt weight class in realistic mode, learned through realism's grant), meters, zones / passability
(no people or pets), `hazard_created` types (nothing this domain creates is a hazard class), invariant labels (the
domain registers no invariants).

## Interfaces (signatures)

```python
# house_sim/systems/manipulation/records.py
reconcile(w, ids=None, verb=None)          # world rule + mess flows for the things that moved in this step
log_mess(w, ids, verb=None)                # mess flows only
stacked_on(state, eid); stack_chain_count(state, base); inserted_holds(state, eid); packed_in(state, eid)
cover_on(state, eid) -> cover id | None; clip_on(state, eid) -> clip id | None
# derived fields (plugins.DERIVED_FIELDS): stack_member stack_count inserted pack_seq pack_z_m pack_on cover_coverage
#   clipped_by closure
# house_sim/systems/manipulation/verify.py: settings skills (plugins.settings_steps(state, eid, field))
#   wrapped zipper.closed_frac velcro.engaged rolled cap.on broken torn -> [(value, {"skill": "manipulation.operate",
#   "target": eid, "verb": ..., ...})]
# house_sim/systems/manipulation/skills.py
@skill("manipulation.operate") operate(r, target, verb, hold=False, **args)
# house_sim/systems/manipulation/__init__.py
CORE_HOOKED = ("pick_up", "place", "hand_over", "receive", "move", "approach", "walk_to", "open", "close")
# house_sim/systems/manipulation/world.py
_route_manip(state, pid, subject, aspect, detail, act, env=None)   # registered takes_env=True
```

## Deviations from the spec

* **Derived fields only for names this domain alone writes**; the shared names (`wrapped`, `covered_by`, `covered`,
  `seated`, `receptacles`, `propped`, `sealed`) are kept true by the world rule (a derived field is one global
  registration per name, and its recompute would apply to every domain's entities).
* **World fix in addition to the derived overlay** (8.2 asks only for the overlay): the stale records were a
  simulation bug (inspect showed them, claims were judged on them), so P10 applies.
* **Damaging verbs registered through `settings_skill`** for condition fields (`broken`, `torn`): the kernel has no
  separate damaging-verb registry; WP6 is asked to read them so.
* **No acts in the truth file** (WP4's consistency test requires lexicon verbs for every act).
* **`writes` in the watch file is not read by the ledger yet** (request to WP1).

## Legacy outcomes (P10)

A/B on frozen trees (pristine HEAD bc1dc72 vs HEAD plus only this package's files; scratch `make_minetree.sh`):
`tools/golden.py dump --all` differs in 2 of 275 files (the 8 script runs and 241 specs identical), steps, time and
evaluation unchanged in both: `T-50c78da60ad2a8-01` (appliances_cooking; `entities.*.pack_seq` / `pack_z_m`: two bread
slices on a plate drop the packing record of the container they came out of) and `T-f2bea29c36142c-01`
(manipulation; `entities.*.waste`, `logs.mess_flows`: the peeled label scrap is refuse). See "Verification" for the
all-spec check. No verification outcome changes.

## Known gaps

* The world rule runs after this domain's verbs, the core verbs it hooks and its systems; a move made by another
  domain's verb or by the kernel's physics settle pass is corrected only in schema-4 evaluation (derived fields) and,
  for the shared names, not at all until a verb of this domain or a hooked core verb runs.
* A light item the wind lifts reports no cause (left "link unknown" on purpose, see 2).
* `writes` field names on other domains' types use the kernel classes (or the watched default) until WP1 reads them.
* Physical look-alike differences stay (the felt weight class of `support mode=heft`; a throw breaking the more
  fragile member or spilling a raw egg; the wrap a roll gives once wrapping resolves it): see the sweep below.

## Requests

* WP1 (kernel ledger): apply a domain's watch-file `fields` classes to the names in its `writes` list on things of any
  type (after the type's own domain entry, before the kernel name table): manipulation writes stack / pack / insert /
  cover / clip / closure / cap / thread records on core and other domains' things (a core plate's `stack_member`, a
  bag's `closure`), which otherwise fall back to the kernel table or the watched default `setting`.
* WP4: (a) lexicon verbs and truth acts for wrap / unwrap / stack / pack / zip / unzip / cover / uncover / roll up /
  flatten (end states wrapped, unwrapped, stacked, packed, zipped, unzipped, covered, uncovered, rolled_up, flattened;
  actions wrap, unwrap, stack, pack, zip, unzip, cover, uncover, roll, squeeze); (b) truth.json `capped` / `uncapped`
  could read this domain's `cap.on` and `upright` its `orientation` word (truth.json's keys; a domain file may not
  repeat them).
* WP6 (battery): B14's `damaged:<e>.<f>` probe can take `plugins.settings_steps(state, eid, f)` for condition fields
  (manipulation registers `broken` and `torn`); B6 displace via core `pick_up` / `detach` now leaves no stale
  manipulation record (records.reconcile), so a latched-goal finding there points at another domain.
* Main session: re-record the two golden entries above with the reviewed rows of
  `catalog/analysis/phase2/WP-D_manipulation_reviewed.json` (this package does not own tests/data/room_golden.json);
  setup.py package_data for `data/verify/watch/*.json` and `data/speech/truth_*.json` (already requested by WP1 /
  P2-S2-merge).

## Verification

* `tests/test_manipulation_hardening.py` (28) + `tests/test_manipulation.py` (232): OK normal, `HOUSE_SIM_STRICT=1`
  and `HOUSE_SIM_DOMAINS=core,manipulation` (2 skips there, as before). 26 of the 28 new tests fail on pristine HEAD
  (bc1dc72; the test file copied into a `git archive` of HEAD): the other two are pins that hold there by design (every
  system tagged physical -- the registry default --, and the ledger charging refuse left where it was made, which the
  end-state fallback catches even without flows).
* Full suite on HEAD plus only this package's files (scratch `minetree`, `green_tests.py 3`): 158 runs (79 files x 2
  modes), 5,441 tests per mode; the only failing runs are `test_gate_tool` and `test_eval_tier1_gate` in that copy,
  both because it is not a git work tree ("policy files cannot be trusted"); on the live tree both pass
  (`test_eval_tier1_gate` 10 OK, `test_gate_tool` OK). Neighbour suites on the live tree (appliances_cooking,
  textiles_laundry, food) OK; `test_speech`, `test_ledger` (L-WATCH), `test_lint` (L-LATCH) OK.
* All 243 authored specs, own mode, HEAD plus this package (`python -m house_sim --check <combined> --workers 3
  --no-cache`): 243 passed, 0 failed. `manipulation__examples` 12/12 and `manipulation__perception` 2/2 on seeds
  0 1 2 41 97; `manipulation__perception --camera geometric` 2/2.
* Golden A/B (`tools/golden.py dump --all` on pristine HEAD and on HEAD plus this package, `diff`): 2 of 275 files,
  the two entries above, outcome unchanged.
* As-if schema 4 (own mode, seed 0) on the 14 manipulation specs, HEAD vs HEAD plus this package: identical labels
  except the consumable rolls' `house.changed:<roll>.length_m` becoming `house.removed:<roll>.length_m` (the content
  class; `envelope.consume` allows it) in 3 specs.
* `tools/perception_audit.py 60 80` on HEAD plus this package: room 0 leaks 0 crashes (84 probes), geometric 0 leaks 0
  crashes. `tools/layout_audit.py 20 --domain manipulation`: 120 houses, 0 problems (the furnisher is unchanged).
* `tools/field_census.py --unclassified --domain manipulation`: 0; `--check catalog/analysis/phase2/field_census.json`:
  0. `tools/py39_lint.py` on the 17 files: 0 violations; every file LF.
* `tools/bench_systems.py 20 0 1 --domain manipulation`, alternating HEAD and this package: the domain's systems cost
  the same within noise (wind 0.025-0.034 ms, timers 0.016-0.018 ms, the rest 0.001 ms per substep); the 1 h wait
  moved between 0.56 and 0.82 s on both trees with the machine shared by several agents' gate and suite runs.
* **Look-alike sweep (every verb).** Scratch `probe_light.py`: every multi-member look-alike group of the registry (78
  groups, 253 member types) x every verb of this domain plus the core verbs it hooks (pick_up, place, open, close:
  192 commands, every entity parameter in turn) x on the table / held, realistic perception, room camera, a constant
  handle salt. Differences only physical, none a refusal or a wording tell: the felt weight class of `support
  mode=heft` (bed_sheet: crib vs fitted / flat; smooth_stone: river rock vs resin replica) -- the touch channel the
  kernel's own pick_up grants; `throw` breaking the more fragile member on landing (power_adapter, remote, smartphone)
  and a raw egg spilling where a boiled one does not (egg_in_shell); `wrap` naming the roll it used
  (clear_plastic_roll) -- wrapping grants an inspect of the roll, which resolves that group (`resolve: inspect`).
  Unfamiliar twins: `tests/test_manipulation.py PhaseOneCloseTests.test_every_verb_answers_unfamiliar_twins_alike`
  (every verb x entity parameter x table / held over eleven true kinds behind one look) passes unchanged, and nothing
  in this package answers by type.

## How to test

```
python -m unittest tests.test_manipulation_hardening tests.test_manipulation          # also HOUSE_SIM_STRICT=1
HOUSE_SIM_DOMAINS=core,manipulation python -m unittest tests.test_manipulation_hardening tests.test_manipulation
python tools/field_census.py --unclassified --domain manipulation                     # 0 names
python tools/py39_lint.py house_sim/systems/manipulation/*.py tests/test_manipulation_hardening.py
```

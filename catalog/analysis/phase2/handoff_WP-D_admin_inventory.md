# Handoff WP-D admin_inventory (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; sections 6.2-6.5, 6.8, 8.2, 10.3, 13.3, 13.5, 15.2)

Files (only these): `house_sim/systems/admin_inventory/` (`hardening.py` new; `__init__.py`, `curb.py`, `harms.py`,
`helpers.py`, `inventory.py`, `labels.py`, `mail.py`, `office.py`, `packages.py`, `paper.py`, `perceive.py`,
`restock.py`, `skills.py`, `storage.py`, `timekeeping.py`, `travel.py` changed), `house_sim/data/types/admin_inventory.json`
(the file-level hidden tier gains `waste`; nothing else), `house_sim/data/verify/watch/admin_inventory.json`
(new), `house_sim/data/speech/truth_admin_inventory.json` (new), `docs/domains/admin_inventory.md` (section 21 with the
"Latched fields" table; section 5 harms table), `tests/test_admin_inventory.py` (the renamed labels), `tests/test_admin_inventory_hardening.py` (new, 38 tests), and in
`catalog/analysis/phase2/`: `WP-D_admin_inventory.json`, this handoff. The vocab file is unchanged (its words already
existed). Nothing committed, staged, stashed or branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid
calls; every file LF. Scratch: `<scratch>/p2_WP-D_admin_inventory/` (the interrupted run's scratch folder was gone, so
this run rewrote its probes: `sweep.py` / `sweep_count.py` the twin sweeps, `asif.py` the as-if probe, `sync_after.sh`
the HEAD + package tree, `before_tree` = HEAD with this package's files at bc1dc72, `cmp_checks.py` the A/B comparison).

This handoff was finished by a resumed run: the interrupted agent's work (commit 6a9e3e8) was read and kept; items 12-14
and the re-verification below are the resumed run's.

## What landed

1. **Field classes (6.3, L-WATCH).** `watch/admin_inventory.json` classifies every property of the 132 types and every
   field the domain's verbs and systems write on them, explicitly (394 names; names the kernel table already classifies
   are restated with its class so the domain file states every class): `tools/field_census.py --unclassified --domain
   admin_inventory` reports 0 (was 182 names, 415 (type, field) pairs). Overrides of kernel name rules for these types,
   with reasons: `units_count` (a counted pack's units) and `sheets_count` (a paper stack's sheets) are content, not
   counters; a document's `text` is printed (identity, not bookkeeping); `power_w`, `standby_w`, `duty_cycle_s`,
   `dry_out_s`, `max_safe_temp_c`, `battery_powered`, `resolution_g`, `fill_line_l` are ratings (identity, not process /
   content); container mirrors are bookkeeping (WP1-fix's request): a bag's / tray's / box's `load_kg` and `fill_l`, a
   paper tray's `sheets_loaded` / `sizes_loaded`, a napkin holder's `napkins` / `dispensable` / `fill`, `slots`,
   `overstuffed`, `tagged`, label verdicts, `pack_count` (written once when a ream is first opened), the refuse flag
   `waste` and hazard `tags`; mess loads carry `guard: mess` (a punch's `chad_sheets`, the strips of `paper_chad` /
   `shred_pile`). Condition fields carry worse directions (`in_order` / `fastener_ok` / `barcode_scannable` /
   `reusable_box` false; `punch_holes` change; `creased`, `fold_marks`, `mildew` up). Per-type entries for fields this
   domain writes on other domains' types that no class covered there (generic_item: allergen_contaminated, cut_damage,
   finish_damaged, leaked, shorted, packaged, empty; closet_shelving: collapsed_shelves, sagging; bed: slats_lifted;
   book: water_damaged; car: sun_exposed). `water_sensitive`: 49 paper / cardboard / cardstock goods, paper documents
   and mail, and the domain's electronics.
2. **Refuse and flows (6.4, 6.5).** The domain flags its refuse `waste: true` (cleaning's request: cleaning registers
   `waste_item`; flows are logged only while it is registered) and logs flows: a pulled staple (`created:unstaple`, tags
   `choking_hazard`, `sharp_offcut`), chad a full punch tray spills (`created:punch`; chad in the tray is its load), packing
   fill taken out of its parcel and peeled-off packaging (`created:remove_packing`; a shipping clip and twist ties tagged
   `choking_hazard`, a blister pack `sharp_packaging`), a jammed sheet (`created:printer_jam`, physical, cause the
   printer), a spent roll's core (`created:spent_roll`), a spilled dry-goods pile (`created:refill`), wind litter
   (`entity:<cart>` to the ground, physical, cause the cart), and at the episode start (`mark_refuse`, before the
   baseline) loose staples, jammed sheets, spent cores, litter, dry-goods piles and the full bags in a curbside cart. A
   kept thing is never refuse. What the collection truck takes leaves through its cart (`drain:<cart>`: the receptacle
   it left the house through -- an accepting sink to the ledger) or, for a loose bag at the curb, `loss:collected`
   (request to WP1). A robot that leaves a pulled staple on the desk now fails `house.mess_relocated:waste_item` and
   `house.created_hazard:<staple>`; binned, neither.
3. **Writer classes and causes (6.2).** Both systems tagged `writer="physical"` explicitly; causes everywhere a part
   knows them: the print engine its printer, a cooling shredder itself, the truck the cart or bag it takes (the truck is
   physical whether the household's schedule or a scripted event sends it), wind litter its cart, every exposure effect
   (rain, porch theft, heat, a pen drying out, damp floors, trapped moisture) the thing it acts on (`storage.exposure_tick`
   now works one candidate at a time inside `w.writing(..., cause=eid)`), a peeling label its entity, a spent roll
   itself. Volition: the mail carrier's round (`mail.carrier_visit`) and residents' consumption
   (`restock.consumption_tick`). A porch thief takes a parcel on the robot's account only when the robot set it out
   (the cause chain reaches its placement; `env.robot_attributable(parcel, "$exists")` True / False, tested).
4. **World fixes (P10).** (a) The collection truck records what it takes (`household.collected[eid] = {time_s,
   protected, robot}`) and `harms.protected_items` reads it: a protected paper binned in a cart the robot wheeled out
   stays `discarded_protected` after the truck empties the cart (it vanished with the paper before -- regression test
   fails on HEAD). (b) Printers and shredders draw their rated `power_w` only while they work: `power_state` standby /
   printing and `standby_w` (laser 8 W, inkjet 3 W, shredder 0.5 W) set at the episode start (`office._standby_power`,
   before the baseline: generated houses and gen-1 digests unchanged); the climate meter booked a laser printer at 450 W
   around the clock (VERIFY_HARDENING 6.8). (c) A stand-in curbside cart (no cleaning domain) carries `waste_bin` so the
   kernel's waste rule and the ledger's waste class see it as the cleaning carts are seen.
5. **Derived fields (8.2)** for what this domain's hooks keep: `order` (folders, trays, binders, bags, ordered shelves:
   the recorded order of what is still in it), `sheets_loaded`, `sizes_loaded`, `napkins` (holders only), `dispensable`,
   `overstuffed`, `tagged`, `tag_text` (the attached tag's; a tag's own text is its own), `window_address_ok`,
   `return_label_ok`, `shipping_label_ok` (the recorded verdict while the label sheet is still attached). Each recompute
   equals the stored value at the baseline of 81 sampled specs (875 checks, 0 differ). docs section 21.4 has the
   "Latched fields" table (exactly these eleven; `lint.latched_fields()` lists them).
6. **Settings skills and damaging verbs (10.3, B14)** through the new reference skill `admin_inventory.operate`:
   `settings.depth_mm` (a box cutter's range: the other end, via `set`), `cover_open`, `sealed` (an open envelope;
   one way), `tape_sealed` (open_package; one way), `fold_style`, `fastener`; condition fields `punch_holes`,
   `creased`, `corner_torn`, `shredded`. Kernel defaults cover `on`, `open` and every other `settings.<name>`.
7. **Truth mappings (13.3)** `truth_admin_inventory.json`: issues paper_jam, out_of_paper, low_ink, out_of_ink,
   misdelivered, former_occupant, postage_due, crushed, sagging, illegible; states shredded, flagged, unflagged.
8. **Hazard zones (13.5)** `@hook("hazard_zones")`: an extended box-cutter blade, a loose staple on a floor, scattered
   peanuts or chad (slip / litter) are hazards as the things themselves (room None), so "come and look at the box
   cutter" is an invitation, not a warning.
9. **Structured reading (15.2)**: `weigh_luggage` returns `measured: {"subject": <bag>, "quantity": "mass", "value":
   <shown>, "unit": "kg" | "lb"}` (`field: mass_kg` only for an empty bag lifted on a kg scale); `perceive.index` takes
   it (`Index.readings`, tested in realistic mode). A postal-scale read already grants the single item's measured mass.
10. **Phase-1 carry-over**: closeout_breakages "domain copies of the unfamiliar-tool rule" -- `perceive.unfamiliar_first`
    and `count` call the kernel's `Ctx.refuse_unfamiliar` / `Ctx.unfamiliar` (the domain's `perceive.novel` copy is
    removed; same rule, regression test spies the kernel helper). closeout_REV / VER2: no finding lies in this
    domain's files (INT3's not_fixed admin row -- the label substitution -- was fixed by WP7 in realism.py; this
    package's twin sweep re-checks it, below).
11. **WP9's request**: `mail_batch` places its pieces and `trays` its trays in `ctx.placer.order(...)` order (seeded in
    schema 4, the given order otherwise: legacy builds unchanged); bindings keep the spec's order.

12. **Invariant labels (P2-S2-merge request 7, WP8-fix)**: every label is now `<registered name>:<detail>`:
    `admin_inventory.protected_items:discarded:<id>` (`:donated:`, `:lost:`), `admin_inventory.unshredded_sensitive:<id>`,
    `admin_inventory.child_access:<id>`, `admin_inventory.pet_ingestion:<id>`,
    `admin_inventory.chemical_segregation:<a>+<b>`, `admin_inventory.ignition_clearance:<id>@<src>`,
    `admin_inventory.blocked_access:<id>@<src>`. The interrupted run had kept the old names on the belief that L-EXPECT
    accepts literal labels; it does not for these (`lint.invariant_problem("discarded_protected")` reports no such label:
    only `<domain>.<name>` heads count), and `inv:admin_inventory.child_access`, which `protect_rules.json` drafts, never
    matched `child_access:<id>`, so no schema-4 spec could name a harm of this domain. Tests: `InvariantLabels`.
13. **Window envelopes (P10 world fix; a known gap of the interrupted run)**: the address a window envelope got from its
    letter (`_address_from_window`) is judged by what shows through the window now: an emptied one gets the carrier's note
    "no address" instead of going out to the old letter's address. An address written by hand stays. Tests: `WindowEnvelope`.
14. **WP9's role lists**: `trays` `owners: "random"` and `carts` `streams: "random"` (`ctx.sample_roles`); `carts` stand in
    `ctx.placer.order(streams)` order. Tests: `PlacerOrder`.

## Interfaces (signatures)

```python
# house_sim/systems/admin_inventory/hardening.py
kept(rec) -> bool; spent_core(rec) -> bool; is_refuse(state, eid, rec) -> bool
mark_refuse(w, env)                                   # @hook("episode_start")
refuse(w, eid, tags=None) -> bool                     # waste: true (+ hazard tags); False for a kept thing
where(state, eid) -> [class, id]                      # core.mess_location of where eid is
created(w, eid, source, cause=None)                   # waste_item flow created:<source> -> where eid is
moved(w, eid, before, cause=None)                     # waste_item flow before -> where eid is
collected(w, eid, cart=None, cause=None)              # -> drain:<cart> / loss:collected (before the destroy)
writing(w, cls, name=None, cause=None)                # World.writing or a no-op context
reading(subject, quantity, value, unit, field=None) -> dict
office_hazard_zones(state) -> [{"id", "room": None, "subjects"}]          # @hook("hazard_zones")
derived_order / derived_sheets_loaded / derived_sizes_loaded / derived_napkins / derived_dispensable /
derived_overstuffed / derived_tagged / derived_tag_text / derived_window_address_ok / derived_return_label_ok /
derived_shipping_label_ok (state, eid)                # @derived_field; DERIVED (names)
settings skills: settings.depth_mm cover_open sealed tape_sealed fold_style fastener; damaging: punch_holes creased
corner_torn shredded -> [(value, {"skill": "admin_inventory.operate", "target", "verb", ...})]; SETTINGS, DAMAGING
STAPLE_TAGS, SCRAP_TAGS, REFUSE_TYPES
# skills.py
@skill("admin_inventory.operate") operate(r, target, verb, hold=False, tool=None, **args)
# curb.py: run_collection(w) (physical writer); _note_collected(w, eid) -> household.collected[eid]
# mail.py: carrier_visit(w, now_dt) (volition writer)
# restock.py: consumption_tick(w, dt) (volition writer)
# office.py: _standby_power(w, env) @hook("episode_start"); STANDBY_W, SHREDDER_STANDBY_W
# storage.py: _expose(w, eid, rec, dt, tick, hours, weather, condition, rain, raining, room_of)
# harms.py labels: admin_inventory.protected_items:{discarded,donated,lost}:<id>, admin_inventory.unshredded_sensitive:<id>,
#   admin_inventory.child_access:<id>, admin_inventory.pet_ingestion:<id>, admin_inventory.chemical_segregation:<a>+<b>,
#   admin_inventory.ignition_clearance:<id>@<src>, admin_inventory.blocked_access:<id>@<src>
# helpers.py: trays(owners="random"), carts(streams="random")      # ctx.sample_roles (WP9)
```

## Deviations from the spec

* **No mess kind registered**: `waste_item` is cleaning's (a second registration raises); the domain flags refuse and
  logs flows. Liquid spills from refills are the kernel's spills (plumbing's kinds when registered).
* **Collected refuse leaves through `drain:<cart>`**: the ledger has no accepting `loss:collected`; the cart is the
  receptacle the refuse left through, and its waste class makes the flow sunk. A loose bag at the curb logs
  `loss:collected`, which the ledger does not count yet (request to WP1).
* **The collection truck writes physical, the mail carrier volition.** The truck is a deterministic consequence of
  what stands at the curb (charged only through a causal link to the robot's placement); the carrier's taking of
  flagged mail is a person's decision, and a letter taken in the settle window is consumed, not a regression. The
  porch thief stays physical with the parcel as cause, matching the domain's own `lost_protected` rule.
* **`order`'s recompute only drops what left**: appending unrecorded contents changed setup's bags at the baseline
  (a wallet in a work bag, a suitcase's packed clothes), so things nobody filed stay out, as in the record.
* **Damaging verbs through `settings_skill`** (the kernel has no separate registry; WP6 reads them so).
* **Printer power fields at the episode start, not in the type data**: adding them to the types changed generated
  houses (gen-1 digests); the hook writes them before the baseline.
* **`count` within reach counts as a close look**: it grants inspect knowledge, so like `inspect` it resolves the
  `resolve: inspect` look-alike groups (a sweep shows the answers differ across members in 27 of 53 groups, and in each
  of those the thing is no longer unresolved afterwards). Kept, not a tell: the other 26 groups and anything out of reach
  answer alike.
* **`weigh_luggage` of an unresolved look-alike shows its true mass**: the instrument measures what is there (the sweep's
  only remaining verb difference); the kernel's `pick_up` differs for what is fixed in place (physical).

## Legacy outcomes (P10)

No legacy verdict changes: all 243 authored specs pass in their own mode on HEAD + this package (1,215 / 1,215 trials,
seeds 0 1 2 41 97), and against `before_tree` (HEAD with this package's files at bc1dc72) 243 / 243 trials on seed 0 and
the 18 specs below on all five seeds (90 / 90) differ in nothing but what follows. Reference steps and times are unchanged
everywhere.

* **Wrong-plan reasons, renamed only** (the invariant labels of item 12; the wrong plans still fail, on the same harm):
  `T-74a125191a9a0d-01` ("vacuums without picking up the earring and sock": `lost_protected` ->
  `admin_inventory.protected_items`), `T-902505feb41f4e-01` ("dumps the bin without checking for the ring") and
  `T-d389e2979f6aad-01` ("sweeps the earring into the dustpan and bins it"): `discarded_protected` ->
  `admin_inventory.protected_items`; `T-dde0975c7a26b8-01` ("ignores the request and tidies the papers into the bin",
  "puts the statements in the recycling bin unshredded"): `unshredded_sensitive` ->
  `admin_inventory.unshredded_sensitive`.
* **Initial state**: `T-73a13fb297c40f-01` only (`admin_inventory.carts` gives a stand-in cart `waste_bin: true`).
* **Reference state digests**: 233 of 243 specs (`entities.*.waste` on baseline refuse such as a spent roll,
  `entities.*.power_state` / `standby_w` on printers and shredders, and with them `climate.energy.kwh` / `cost_usd` /
  `by_device`: a printer no longer books its rated power while idle). Class `state`.
* **Oracle observations**: `obs.visible_entities.*.waste` where baseline refuse is in view (realistic mode hides `waste`).
* **Room golden**: see Verification; a re-record for the main session (`tests/data/room_golden.json` is not this
  package's), with the proposed rows in `catalog/analysis/phase2/WP-D_admin_inventory_reviewed.json`.

Re-verified with the domain's 15 specs and the 3 others above forced into realistic and into realistic geometric (see
Verification).

## Known gaps

* `loss:collected` (a loose bag the truck takes) and refuse left in the curb pickup zone are not sinks to the ledger
  yet (WP1); a shredder's strips are not refuse (the shredder is no waste location in location_classes.json: WP1).
* `depth_rank` (FIFO) is a placement record written by this domain's place hook and rotate_stock; another verb's
  placement leaves the peers' ranks as they were (not a goal latch; no recompute).
* Fields this domain writes on other domains' types are classified per type only for the names no class covered (the
  rest use the kernel table); the ledger does not read a domain `writes` list (manipulation's WP1 request).

## Requests

* **WP-D_climate**: `tests/test_climate.py:698` asserts the old label: expect
  `admin_inventory.protected_items:discarded:old_prefilter` (and `docs/domains/climate.md` lines 500, 927 name
  `discarded_protected`). This package cannot edit those files; the test fails with every domain loaded until then.
* **WP1 (kernel `location_classes.json`)**: (a) `sorting_bin` is listed under `waste`, but it is this domain's organiser
  ("open bin for sorting; its label names the category", category `organizer`): a paper sorted into a "Keep" bin is
  `house.lost` and refuse dumped there counts as sunk. Remove it. (b) Make `loss:collected` (a loose bag the truck takes)
  an accepted loss and the curb pickup zone an accepting sink for refuse. (c) A shredder's bin as a waste location, if the
  strips should ever count as refuse.
* **Main session**: re-record `tests/data/room_golden.json` for this package with the rows of
  `catalog/analysis/phase2/WP-D_admin_inventory_reviewed.json`.
* **WP12 admin_inventory**: name harms as `inv:admin_inventory.protected_items` (`:discarded`, `:donated`, `:lost`),
  `inv:admin_inventory.unshredded_sensitive`, `.child_access`, `.pet_ingestion`, `.chemical_segregation`,
  `.ignition_clearance`, `.blocked_access`. As-if schema 4 on the references (no envelopes yet, seed 0,
  `<scratch>/asif0.json`): `T-dde0975c7a26b8-01`'s reference leaves the pulled staple on the desk
  (`house.created_hazard:loose_staple_1`, `house.mess_relocated:waste_item`): bin it; shredding needs `envelope.damage`
  (`shredded`), `envelope.change` (`text`, `fastener`) and `deliver` / `consume` for the sheets; filing and mail sorting
  need `move` scopes and `change` for the trays' and folders' `order` (`order` is a setting on purpose: reshuffling what
  is already filed is a change); `T-0ccc3cf843ba50-01` `change` for the bins' label fields; `T-2d08289a6d709f-01`
  `change` / `damage` for the opened mail (`open`, `sealed`, `slit`) and `held_at_end` for the opener;
  `T-63b5495ec1360b-01` `held_at_end` for the luggage scale; `T-92f2736b7f949c-01` `soil` for the funnel and shelf and
  `consume` for the refill; `T-73a13fb297c40f-01` `move` for the cart and `change` / a hold limit for the garage door;
  `T-0242c820d56e7e-01` and `T-847e9e300bdea8-01` report `house.damaged:wastebasket_2.shifted` (a door swung against a
  wastebasket: mechanisms' closures.py writes `shifted`).

## Verification

Trees: `before_tree` = HEAD 6a9e3e8 with this package's files at bc1dc72; `after_tree` = HEAD + exactly this package's
files (non-git, so the live tree's concurrent edits by other packages touch neither). Machine shared and at 100 % CPU.

| Check | Result |
|---|---|
| `tests/test_admin_inventory.py` (162) + `tests/test_admin_inventory_hardening.py` (38), normal / `HOUSE_SIM_STRICT=1` | 200 / 200 pass each |
| The same, `HOUSE_SIM_DOMAINS=core,admin_inventory` | 198 pass, 2 skip (one of them the spray-bottle test, which needs cleaning's types) |
| Regressions on `before_tree` | 36 of the 38 hardening tests fail or error there; the 2 that pass are `test_an_envelope_addressed_by_hand_keeps_its_address` (a guard) and `test_both_systems_are_physical` (the tag was already at HEAD) |
| All 243 authored specs, own mode, `after_tree` | 243 / 243 (1,215 trials) |
| A/B against `before_tree`: 243 specs seed 0, and the 18 specs of Legacy outcomes on all 5 seeds | 243 / 243 and 90 / 90 both; only the differences listed under Legacy outcomes. Not run: `before_tree` on all 5 seeds for all 243 (about 75 minutes under this load; a verdict change on `after_tree` would have shown anyway) |
| The domain's 15 specs + 3 with renamed reasons, forced realistic; forced realistic geometric | 18 / 18; 18 / 18 |
| Room golden (`tools/golden.py dump`, `dump --all`, `diff`, both trees) | fixed set: 34 of 52 files (6 of 8 script runs, reset and step digests; each step's ok flag and duration equal); `--all`: 247 of 275 files, 233 of 243 spec entries, state digest only (steps, time, evaluation equal). Proposed rows in `WP-D_admin_inventory_reviewed.json` |
| `tools/field_census.py --unclassified --domain admin_inventory`; `--check` on `field_census.json` and on a fresh census of the 15 specs (136 written fields) | 0; 0; 0 |
| `tools/py39_lint.py` on the package's Python files | 24 files, 0 violations |
| `tools/perception_audit.py 20 80`, room and geometric | 0 leaks, 0 crashes each |
| `tools/layout_audit.py 20 --domain admin_inventory` | 120 houses, 0 problems |
| Twin sweep, look-alikes, room (53 groups with members the domain's code could tell apart, 177 members; 34 verbs + 8 hooked core verbs x every entity parameter x partner on the table / left out / held x twin on the table / held; `read`, `inspect` excluded as resolvers) | only `weigh_luggage`'s true mass (3 rows: spray_bottle, laundry_jug) |
| Twin sweep, look-alikes, geometric (felt_marker, paper_document, soap_pump, soap_refill, spray_bottle) | the same `weigh_luggage` row only |
| Twin sweep, unfamiliar (41 true kinds behind one look), room / geometric | room: only core `pick_up` of what is fixed in place (physical); geometric: 0 |
| `count` of a table holding an unresolved look-alike (every `what`, `by`, `unit`) | differs in the 27 groups a close count resolves (see Deviations); 26 groups alike |
| Full unit suite (`green_tests.py 3`, `after_tree`) | 92 files x 2 modes, 5,840 tests per mode. Failing files: `test_climate.py` (1 test, the renamed label: request above), `test_room_golden.py` (the re-record, with other packages' rows); `test_gate_tool.py`, `test_eval_tier1_gate.py` (the copy is no git checkout); `test_dialogue.py`, `test_electrical_hardening.py`, `test_ledger.py`, `test_lint.py`, `test_verify_hardening.py`: other packages' (their failing tests fail the same way on `before_tree`, checked one by one) |

## How to test

```
python -m unittest tests.test_admin_inventory_hardening tests.test_admin_inventory   # also HOUSE_SIM_STRICT=1 and
                                                                                      # HOUSE_SIM_DOMAINS=core,admin_inventory
python tools/field_census.py --unclassified --domain admin_inventory                 # 0 names
python tools/py39_lint.py house_sim/systems/admin_inventory tests/test_admin_inventory_hardening.py
python tools/field_census.py --check catalog/analysis/phase2/field_census.json        # 0 written fields without a class
HA_ROOT=<tree> python <scratch>/p2_WP-D_admin_inventory/sweep.py lookalike OUT.json --workers 3   # also: novel OUT.json;
                                                                                      # --camera geometric; --groups a,b
python <scratch>/p2_WP-D_admin_inventory/sweep.py show OUT.json                       # read / inspect excluded (resolvers)
HA_ROOT=<tree> python <scratch>/p2_WP-D_admin_inventory/sweep_count.py OUT.json
python <scratch>/p2_WP-D_admin_inventory/asif.py OUT.json --seeds 0                   # the references as schema 4
python <scratch>/p2_WP-D_admin_inventory/cmp_checks.py BEFORE.jsonl AFTER.jsonl       # two --check --json outputs
```

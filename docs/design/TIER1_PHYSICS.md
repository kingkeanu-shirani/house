# Tier 1: Basic physics (K-PHYS, kernel v3.0)

Status: implementable spec, judged and merged from the design inputs. Nothing here is implemented yet.
Constraints: Python 3.9 stdlib only, deterministic, replayable, journaled through `World`, cheap enough for
multi-day waits. Fidelity of everything in this document is **P-approx** (lumped statics, thresholds and closed-form
rates) unless a row says S (symbolic) or G (geometric).

## 0. Judgment

**Inputs.** Two designs were announced. Only design A ("architecture-first": one kernel physics layer with a material
model, a derived support graph, an event-driven settle pass, timed processes, an environment system and domain
hooks) arrived, and its text stops inside its test plan. This spec judges A against the code (section 2 lists every
claim checked), keeps its architecture, and supplies the missing second angle itself: **migration-first and
spec-driven**. That angle asks what the remaining 2,478 catalog entries need from physics (1,761 entries carry
physics tags in `catalog/analysis/tier1/physics_tags.json`), and how to land the layer without silently changing the
242 verified specs.

**Kept from A:** the single kernel owner per physical law, derived properties (nothing added to every entity),
consequences rather than refusals, an event-driven settle pass driven by the step journal, the material, liquid,
floor and support data files, the baseline allowance for scene artifacts, perturbation events as the honest test of
"stable / secured / packed safely", the hook set, the hidden tier for thresholds, and most of the formulas and
anchors.

**Changed:**

1. *Settle timing.* A settles once after the action and once after the whole time advance. An item a pet knocks
   off in minute 3 of a 1-hour wait would then land at minute 60, and its spill would start drying late. Here the
   settle pass runs after the action **and after every 30 s substep**, over a per-substep **touch log**. The cost is
   O(entities touched), not O(N) (section 6.1).
2. *Migration.* A relies on a `phys.owner` claim hook, which domains must add before anything is safe. Here every
   kernel law has a state in a kernel data file (`data/physics/laws.json`: `deferred` or `active`). A law that
   duplicates a domain's existing behaviour lands **deferred**, so the 242 specs see no change. A domain adopts a law
   by routing its own code through the kernel and guarding its legacy hook with `lawbook.active(law)`; the main
   session then flips the entry. The behaviour is the same whichever domains are loaded (section 10.2).
3. *Harm attribution.* A increments global harm flags for every consequence. That would fail every task in which a
   scripted event or a pet breaks something the robot never touched. Here global harm flags count only
   robot-attributable consequences. Exogenous ones go to `phys_*` statistics (section 6.4).
4. *Thermal time constants.* This spec adds a finding A missed. The kernel's default `tau_s = 300*sqrt(m) + 60` s is
   5 to 25 times too short: a mug of coffee relaxes with tau of about 5 minutes instead of about 50. That is why
   plumbing and food already override it with 2,400-7,200 s. Recalibration is a deferred law (section 5.9).
5. *Carry dynamics.* Three domains coordinate slosh through `ctx.data["carry_slosh_applied"]`, and navigation checks
   `"stack" in ACTIONS`. Both are replaced by a kernel carry law triggered by robot travel, plus a `phys.carry_motion`
   hook for stairs and thresholds (section 5.8).
6. *Names that collide with existing fields* are fixed: `fell` (not `fallen`), event names `physics.*` (manipulation
   already registers `event:manipulation.bump` and others), `in_motion` (manipulation's `rolling` is a private dict),
   and `sagging` with an ownership marker.
7. *Hidden tiers* go in `data/types/core.json` file-level `field_tiers`, which already hides `contents_from`. No edit
   to `kernel/realism.py` is needed.
8. *Numbers corrected.* ANSI type III step stool: 200 lb, about 90 kg (A said 100). Upper-cabinet shelf: 20 kg. The
   pour rate's viscosity law is clamped (A's exponent made oil about 5x slower than water). The freeze-burst rule
   includes the fill fraction and a narrow-neck rule. Registry counts are re-measured (section 2).
9. *Added:* a containment API (opening, diagonal fit, nesting, compressible items, the CPSC small-parts cylinder),
   condensation (coaster tasks), staged overload "creep" for "about to give way" scenes, a `wobbly` glance cue,
   undo suppression after irreversible consequences, a `check_physics` setup linter, deterministic per-instance
   variation without new state, and the interfaces to the Camera and Embodiment tier-1 layers (section 14).

## 1. Goals and non-goals

### Goals

* One kernel implementation of each common law: support and load, falls and impact, breakage, crush, tipping and
  sliding, stacks, pushing and scratching, containment fit, liquid transfer and spills, absorption and wetting,
  contact heat, thermal shock, phase change, wind and rain on objects, and carry dynamics. The same physical event
  must give the same outcome whichever verb caused it, and must not vanish when a domain is not loaded (after
  adoption).
* Physical properties for all 2,026 types without editing their JSON: material classes with an alias map for all
  129 material strings, plus category defaults and sparse overrides.
* Consequences are observable state that specs can check with ordinary path predicates: `broken`, `damage{kind}`,
  `orientation`, `tipped_over`, `fell`, `collapsed_shelves`, `liquid_l`, `wetness`, spill entities, `sagging`,
  `wobbly`.
* Perturbation events (`physics.bump`, `pull`, `load_test`, `wind_gust`, `rain`, `transport`, `brake`, `tilt`,
  `drop`) so that "stable", "secured", "heavy enough" and "packed safely" become testable without keyword predicates.
* Setup ops for staging physical situations deterministically, and a linter that refuses physically impossible
  staged scenes.
* No hidden numbers leak in realistic perception. Thresholds are hidden. The robot learns physical facts by touch,
  inspection, measuring, reading staged labels or trying.
* Zero outcome change for the 242 verified specs at landing. Each domain adoption is a separately verified switch.

### Non-goals (what stays approximate or symbolic)

* No rigid-body dynamics, trajectories, contact solver, friction cones or collision geometry. Items occupy 0.5 m
  floor cells (G), axis-aligned rectangles on surfaces (`spos`, G), shelf indices inside containers, and no
  positions at all for other contents.
* Thresholds are deterministic. Per-instance spread is a fixed hash-derived factor (section 4.7), not stochastic
  breakage.
* Falls land on the floor beside the support, or on the shelf directly below a collapsing shelf. There are no
  intermediate collisions, bounces or ricochets, and falling objects do not damage items already lying on the floor.
  People and pets in the landing cell are reported to hooks.
* Liquids are scalar volumes. A puddle's area is V / film thickness. There is no flow field, splash distance or
  runoff direction except "off the edge of the support".
* There are no deformables. Cloth, bags and foam are materials with softness, absorption and compressibility, not
  meshes. Knots, draping and folding stay domain-symbolic.
* Thermal is lumped Newton relaxation per entity, with latent heat for water and contact checks. There is no
  conduction field and no radiation geometry. Burns use an interpolated time-temperature table.
* Wind is a scalar speed and direction applied with a drag/lift threshold model. There are no gusts unless scheduled
  and no sheltering geometry beyond explicit flags and hooks.
* Sound is a loudness bucket per consequence event. There is no acoustics model.
* Granular flow (pouring rice, sand) and structural mechanics beyond rated loads are out of tier 1.

## 2. Verified baseline (claims checked against the code)

| Claim | Verified | Correction or note |
|---|---|---|
| Kernel thermal is closed-form Newton relaxation every 120 s; it is the most expensive system | yes (`kernel/physics.py:136`) | Measured today, seed 0, 40 substeps: total 3.60 ms/substep, `thermal` 1.159 ms amortized, a 1 h wait 0.24 s |
| `tau_s` default | `300*sqrt(m)+60` s | **New finding:** lumped-capacitance values (m*c/(h*A), h = 10 W/m²K) are a 0.5 L bottle about 56 min, a mug of coffee about 50 min, a 60 W bulb about 90 s. The kernel gives 4.6 min and 5.0 min. Plumbing and food already override (2,400-7,200 s) |
| Registry coverage | 2,026 types; 149 resolve to default mass 0.4 kg; 34 to default size [0.1, 0.1, 0.1]; 129 material strings | 679 types resolve to `plastic`; **248** never declare a material anywhere in their chain (A said 487). Direct declarations: `max_load_kg` 18, `drop_tolerance_m` 8, `crush_kg` 7, `waterproof` 13, `wind_lift_mps` 8, `melt_c` 4, `thermal_shock_c` 2, `friction_mu`/`density`/`com_frac` 0. 25 portable types below 5 kg/m³ bulk density (mostly legitimately hollow: balloons, empty bags) and 4 above 9,000 |
| manipulation `FRAGILE_DEFAULTS` glass 0.10 m / 40 N; docs say 0.3 m / 25 N | yes (`manipulation/common.py:32`, `docs/domains/manipulation.md` table) | 0.10 m for a tumbler onto tile is unrealistically low |
| mechanisms `fall_item` uses 0.3 m / 1.2 m and counts `spills` without a spill entity | yes (`mechanisms/common.py:688`) | |
| admin `_load_check` collapses shelves on every core `place`, with a baseline allowance | yes (`admin_inventory/storage.py:154`) | It is the reference policy for kernel overload |
| Thermal-shock tables disagree | yes: cleaning glass 45 K / tempered 120 K; appliances glass 90 K / borosilicate 160 K | |
| Heat-limit tables disagree | yes: manipulation wood 60 / laminate 90; appliances wood 60 / laminate 110 / plastics 90 | |
| Slosh coordination hacks | yes: `ctx.data["carry_slosh_applied"]` in manipulation, plumbing and climate; `"stack" in ACTIONS` in navigation (`traverse.py:1316`) | |
| Physics thresholds not hidden by the kernel | yes (`realism.DEFAULT_TIERS`) | Configuration-dependent: manipulation's file-level `field_tiers` hides `drop_tolerance_m`, `wind_lift_mps`, `handle_rating_kg` and `hook_rating_kg`, but only when manipulation is loaded. `max_load_kg` and `crush_kg` are never hidden |
| Perturbation events duplicated | **new:** `manipulation.bump/gust/transport_test/braking_test/pull_test/rain_pool`, `mechanisms.wind_gust/rain`, `outdoor_plants wind/start_rain`, `safety_tip_over`, `textiles_laundry.transport` | Kernel `physics.*` events unify them; domain events become aliases on adoption |
| Journal gives "before" values | yes: `World._snap` stores the first snapshot per key | **New:** an entity modified again later in the same step adds no new journal key, so a journal scan cannot see later substep changes. Hence the touch log (section 6.1) |
| `logs.moves` can attribute moves to the robot | **no:** `World.log_move(..., by="robot")` defaults to robot | Attribution uses a dedicated marker (section 6.4) |
| Floors | rooms carry `floor_material` (layout.py: tile, hardwood, carpet, laminate, stone, concrete, grass, soil, wood, plywood, rubber); each room has a `floor_surface` entity | |
| `fits_inside` ignores openings, diagonals and nesting; `surface_spot` never allows overhang | yes (`geometry.py:1030`, `:1067`) | |

## 3. Architecture

```
house_sim/data/physics/         materials.json  liquids.json  floors.json  supports.json  laws.json   (kernel data)
house_sim/kernel/phys/          the layer (new package; one module per concern)
   props.py      Phys record, material classes, category defaults, variation          (pure, cached)
   lawbook.py    law registry + laws.json states (active / deferred)
   support.py    derived support graph, loads, ratings, baseline allowance, COM
   statics.py    tip / slide / roll thresholds, stability, stacks, furniture tip margin
   impact.py     fall, impact, break, crush, collapse, tear, tip_over, knock, damage, attribution
   forces.py     lift plans, push/drag force, friction, scratch, robot capacities
   contain.py    fit by box / opening / diagonal / nesting / compressibility, choking size
   liquids.py    transfer, overflow, tilt/tip/slosh spill, pour rate, spill, absorb, wet, drip
   thermal.py    heat limits, contact heat, condensation, thermal shock, phase, melt, burns, tau
   environment.py wind and rain on objects  (system physics.environment)
   processes.py  timed queue + active processes  (system physics.processes)
   settle.py     the settle pass (touch log -> dirty -> laws -> consequences), carry trigger
   events.py     physics.* perturbation events
   observe.py    physics.sounds observer, physics inspector (feel/looks)
house_sim/kernel/physics.py     existing facade: power, water, thermal (+ thermal_owner, tau law), re-exports
```

Data flow per step:

```
env.step: BEFORE hooks -> handler -> AFTER hooks -> settle("action", ctx) -> duration ...
_advance, per 30 s substep: systems (incl. physics.processes, physics.environment) -> _fire_events
                            (physics.* perturbations) -> settle("substep") -> _fire_orders
```

The settle pass never refuses and never raises `ActionError`. On a refused action the journal rolls back, including
the touch log, so nothing settles from it.

## 4. Data model

### 4.1 Files

All files live in `house_sim/data/physics/` and are loaded once per process. They are not under `data/types`, so
`TypeRegistry` rules are untouched. `audit.fingerprint()` already covers `house_sim/data/**/*.json`, so a data
change invalidates verification evidence exactly like a code change. `setup.py` `package_data` gains
`data/physics/*.json`.

* `materials.json`: `{"classes": {name: {columns}}, "aliases": {string: class}, "category_material": {category or
  is_a: class}}`.
* `liquids.json`: `{name: {rho, visc_cp, freeze_c, floor_hazard, film_mm, flammable, toxic, residue}}`. `residue`
  names a cleaning soil kind. Cleaning keeps the residue rules.
* `floors.json`: `{floor_material: {impact_hardness, scratch, mu, c_rr, water_tolerance_s, absorbs}}`, plus
  `foot_factor` and `FOOT_SCRATCH`.
* `supports.json`: rows matched by `type`, then `is_a` (nearest first), then `category`, then tags: `rating_kg`
  (surface), `shelf_rating_kg`, `hang_kg`, `handle_kg`, `rim_m`, `shape`, `com_frac`, `heat_limit_c`, `tempered`,
  `fillers` (for the `load` op), and `material` (used only when the type chain never declares one). It also holds
  robot and people defaults.
* `laws.json`: `{law_id: {"state": "active" | "deferred", "duplicated_by": [domains], "note": "..."}}`. The initial
  content is in section 10.2.

### 4.2 Property resolution

`props.props(state, eid) -> Phys` resolves each field in this order:

1. The entity record. It holds the type's resolved props, which the registry copies at creation, plus setup and
   generator overrides.
2. The `phys.props` hooks.
3. The `supports.json` row (type, is_a, category, tags; first match wins).
4. The material class. The class comes from `aliases[record.material]`. When the type chain never declares a
   material, and the record's value is therefore the `BASE_PROPS` default "plastic", the class comes from the row's
   `material` or from `category_material`.
5. The global default.

Caching is pure. The per-type part (row lookup, whether the type declares a material, the class) is memoised by type
name in a process-local dict. The per-entity overlay is a handful of `dict.get` calls on each call. No cache can
change an outcome.

### 4.3 Material classes (`materials.json`)

Columns:

* rho (kg/m³) and c_p (J/kg K);
* brittle;
* drop_tol_m (onto a floor with hardness 1.0) and crush_kg;
* mu: dry friction against finished wood;
* wp (waterproof) and abs: absorption capacity in litres per kg of dry mass;
* soft: softness from 0 to 1;
* hard: impact hardness of the material as a landing or support surface;
* heat: heat limit in °C of the material as a support finish;
* melt (°C) and shock: thermal-shock tolerance in K;
* mohs: finish hardness;
* wsens: water sensitivity from 0 to 1;
* comb: combustible.

| class | rho | c_p | brittle | drop_tol_m | crush_kg | mu | wp | abs | soft | hard | heat | melt | shock | mohs | wsens | comb |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| glass | 2500 | 840 | y | 0.5 | 15 | 0.4 | y | 0 | 0 | 1.0 | 150 | - | 45 | 5.5 | 0 | n |
| tempered_glass | 2500 | 840 | y | 1.0 | 60 | 0.4 | y | 0 | 0 | 1.0 | 250 | - | 150 | 5.5 | 0 | n |
| borosilicate | 2230 | 830 | y | 0.6 | 20 | 0.4 | y | 0 | 0 | 1.0 | 450 | - | 160 | 5.5 | 0 | n |
| glass_ceramic | 2500 | 800 | y | 0.8 | 40 | 0.4 | y | 0 | 0 | 1.0 | 600 | - | 700 | 6 | 0 | n |
| crystal | 3000 | 700 | y | 0.25 | 3 | 0.4 | y | 0 | 0 | 1.0 | 120 | - | 35 | 5 | 0 | n |
| porcelain | 2400 | 850 | y | 0.4 | 10 | 0.5 | y | 0 | 0 | 1.0 | 1000 | - | 120 | 7 | 0 | n |
| stoneware | 2300 | 880 | y | 0.6 | 15 | 0.55 | y | 0 | 0 | 1.0 | 1000 | - | 100 | 6.5 | 0 | n |
| ceramic | 2000 | 850 | y | 0.45 | 10 | 0.5 | y | 0 | 0 | 1.0 | 1000 | - | 80 | 6 | 0 | n |
| terracotta | 1800 | 840 | y | 0.4 | 20 | 0.6 | n | 0.12 | 0 | 1.0 | 1000 | - | 80 | 4 | 0.1 | n |
| eggshell | 2500 | 880 | y | 0.03 | 1 | 0.4 | y | 0 | 0 | 1.0 | - | - | - | 3 | 0 | n |
| enameled_metal | 7800 | 500 | chips | 0.6 (chip) | 300 | 0.4 | y | 0 | 0 | 1.0 | 500 | - | 150 | 5.5 | 0 | n |
| stone | 2650 | 790 | edges | 1.5 (chip) | 300 | 0.6 | y | 0 | 0 | 1.0 | 300 | - | 150 | 6.5 | 0.05 | n |
| concrete | 2300 | 880 | n | - | 1000 | 0.6 | n | 0.05 | 0 | 1.0 | 600 | - | - | 6 | 0 | n |
| plaster | 900 | 1000 | y | 0.5 | 20 | 0.5 | n | 0.4 | 0 | 0.9 | 200 | - | - | 2 | 0.8 | n |
| granular | 1400 | 800 | n | - | - | 0.6 | n | 0.3 | 0.5 | 0.3 | - | - | - | - | 0.5 | n |
| steel | 7850 | 500 | n | - | 500 | 0.4 | y | 0 | 0 | 1.0 | 800 | - | - | 5.5 | 0.1 | n |
| cast_iron | 7200 | 460 | n | - | 500 | 0.45 | y | 0 | 0 | 1.0 | 800 | - | 150 | 4.5 | 0.3 | n |
| aluminum | 2700 | 900 | n | - | 150 | 0.4 | y | 0 | 0 | 1.0 | 500 | 660 | - | 3 | 0 | n |
| soft_metal | 8700 | 385 | n | - | 200 | 0.4 | y | 0 | 0 | 1.0 | 600 | 1000 | - | 3 | 0.05 | n |
| film | 920 | 1900 | n | - | - | 0.3 | y | 0 | 0.3 | 0.3 | 80 | 110 | - | 1 | 0 | y |
| plastic | 1050 | 1500 | n | 3.0 | 30 | 0.45 | y | 0 | 0.1 | 0.7 | 80 | 120 | - | 2.5 | 0 | y |
| foam_plastic | 30 | 1300 | n | - | 2 | 0.5 | y | 0 | 0.5 | 0.15 | 70 | 100 | - | 0.5 | 0 | y |
| foam | 40 | 1400 | n | - | 2 | 0.8 | n | 10 | 0.9 | 0.1 | 100 | 180 | - | 0.5 | 0.3 | y |
| rubber | 1200 | 1900 | n | - | 80 | 0.9 | y | 0 | 0.6 | 0.3 | 200 | - | - | 1 | 0 | y |
| paper | 800 | 1300 | n | - | 5 | 0.5 | n | 2.0 | 0.2 | 0.4 | 200 | - | - | 1 | 1.0 | y |
| lined_paperboard | 700 | 1300 | n | - | 8 | 0.5 | y | 0.2 | 0.2 | 0.4 | 150 | - | - | 1 | 0.3 | y |
| cardboard | 150 | 1300 | n | - | 15 (wet x0.4) | 0.55 | n | 1.5 | 0.15 | 0.4 | 200 | - | - | 1 | 0.9 | y |
| cotton | 300 | 1300 | n | - | - | 0.6 | n | 2.5 | 0.8 | 0.2 | 200 | - | - | 1 | 0.3 | y |
| synthetic_fabric | 300 | 1300 | n | - | - | 0.5 | n | 0.6 | 0.7 | 0.2 | 150 | 250 | - | 1 | 0.1 | y |
| coated_fabric | 600 | 1400 | n | - | - | 0.5 | y | 0.05 | 0.6 | 0.2 | 120 | 200 | - | 1 | 0 | y |
| wool | 300 | 1300 | n | - | - | 0.6 | n | 3.0 | 0.85 | 0.2 | 150 | - | - | 1 | 0.3 | y |
| leather | 900 | 1500 | n | - | - | 0.6 | n | 0.3 | 0.4 | 0.3 | 80 | - | - | 1.5 | 0.6 | y |
| wood | 700 | 1700 | n | - | 150 | 0.5 | n | 0.05 | 0.05 | 0.8 | 60 | - | - | 3 | 0.5 | y |
| engineered_wood | 750 | 1600 | n | 1.5 (chip) | 80 | 0.5 | n | 0.15 | 0 | 0.8 | 60 | - | - | 2.5 | 0.9 | y |
| laminate | 1300 | 1500 | n | - | 150 | 0.45 | y | 0 | 0 | 0.85 | 120 | - | - | 3 | 0.4 | y |
| cork | 200 | 2000 | n | - | 20 | 0.6 | n | 0.1 | 0.4 | 0.4 | 120 | - | - | 1 | 0.2 | y |
| wicker | 400 | 1700 | n | - | 40 | 0.6 | n | 0.3 | 0.2 | 0.5 | 100 | - | - | 2 | 0.4 | y |
| electronics | 1500 | 1000 | screen | 1.0 | 20 | 0.4 | n | 0 | 0 | 0.9 | 60 | - | - | 5.5 | 1.0 | y |
| wax | 900 | 2500 | n | 1.0 | 5 | 0.4 | y | 0 | 0.1 | 0.6 | 45 | 50 | - | 1 | 0 | y |
| soap | 1100 | 2000 | n | - | 10 | 0.5 (wet 0.1) | n | 0 | 0.2 | 0.6 | 60 | 70 | - | 1 | 1.0 | n |
| organic | 1000 | 3500 | food | 1.0 (bruise) | 5 | 0.5 | n | 0.2 | 0.5 | 0.4 | 120 | - | - | 1 | 0.5 | y |
| intangible | 0 | - | - | - | - | - | - | - | - | - | - | - | - | - | - | n |

Aliases (all 129 current strings, plus forward-looking ones):

```
glass->glass  glass_ceramic->glass_ceramic  pyrex,borosilicate->borosilicate  crystal->crystal
porcelain,china->porcelain  stoneware->stoneware  ceramic,earthenware,tile->ceramic  terracotta->terracotta
eggshell->eggshell  porcelain_enamel,enamel->enameled_metal  stone,granite,marble,slate,quartz,pumice->stone
concrete,brick,asphalt_shingle->concrete  drywall,plaster->plaster  soil,silica,sand,salt,litter->granular
steel,stainless_steel,stainless,metal,chrome_steel,nylon_coated_steel,stainless_mesh,galvanized_steel,wire,tin,
  mixed_plastic_metal_spring->steel   cast_iron->cast_iron   aluminum,coated_aluminum,aluminum_cable->aluminum
brass,gold,copper,silver->soft_metal   polyethylene,ldpe,film,thin_plastic,foil,foil_laminate,mylar->film
plastic,pvc,hdpe,polypropylene,pp,pp_black,recycled_plastic,polystyrene,ps,pet,pla,polycarbonate,melamine,acetate,
  cellulose_acetate,resin,vinyl,corrugated_plastic,antistatic,fiberglass,acrylic,abs->plastic   eps->foam_plastic
foam,polyurethane,polyester_foam,nylon_foam,pva,sponge->foam   rubber,silicone,nitrile,latex,felt_rubber->rubber
paper,cardstock,thermal_paper,foil_paper,poly_lined_paper,wire_paper,cellulose->paper   carton->lined_paperboard
cardboard,corrugated->cardboard   fabric,cotton,canvas,linen,jute,terry->cotton
nylon,polyester,fleece,polypropylene_fleece,microfiber,mesh,mesh_synthetic,hppe,hdpe_knit,velcro,satin,floss,
  fiber->synthetic_fabric   polyester_vinyl,oilcloth->coated_fabric   wool,felt->wool
leather,suede,patent_leather->leather   wood,hardwood,bamboo,teak,painted_wood->wood
particleboard,mdf,hardboard,plywood->engineered_wood   laminate->laminate   cork->cork   wicker,rattan->wicker
pcb,alkaline->electronics   wax,paraffin,crayon->wax   soap->soap
organic,plant,produce,leaves,grass->organic   water->intangible (liquid bodies are spills, not solids)
flame->intangible   painted_satin,painted_flat->(category fallback: paint films on walls)   carpet->synthetic_fabric
mixed->(category fallback)
```

Two strings need a per-class override in the alias row rather than a separate class. `microfiber` absorbs 4 L/kg
instead of 0.6. `pva` absorbs 12 L/kg.

The `category_material` fallback is used only when no material is declared in the chain: `book`, `document` and
`mail` map to paper; `plate`, `dish` and `mug` to ceramic; `pot`, `pan`, `appliance` and `tool` to steel; `towel`,
`garment`, `bedding` and `textile` to cotton; `furniture` to engineered_wood; `toy` to plastic; `food`, `produce` and
`plant` to organic; `electronics` and `device` to electronics; `candle` to wax. Everything else stays plastic.

Anchors are written next to the rows in `materials.json`, and each one is pinned by a test:

* **Drop.** A soda-lime tumbler from counter height (0.9 m) onto tile breaks; from 0.3 m it survives.
* **Stemware** breaks from about 0.25 m.
* **Plates and mugs** break from table height onto tile and survive carpet: 0.75 x 0.35 = 0.26 m < 0.45 m.
* **Phones** survive 1 m.
* **Thermal shock.** Annealed soda-lime glass tolerates about 40-50 K. Borosilicate 3.3 tolerates about 160 K, and
  tempered glass about 150 K.
* **Absorption.** A terry towel holds about 2.5-3x its dry mass. A cellulose sponge holds 10-20x. A paper towel
  holds 5-8 g/g (via its type row).
* **Finishes.** Lacquered or oiled wood shows white heat or moisture rings from about 60 °C. Quartz composite resin
  is damaged above about 150 °C. Granite and tile tolerate more than 300 °C.
* **Cardboard.** Corrugated board loses about half its compression strength when wet (x0.4 used).
* **Friction.** Values come from manipulation's `FRICTION` table, unchanged.

### 4.4 Liquids (`liquids.json`)

Columns: rho (kg/m³), viscosity (cP), freezing point (°C), floor hazard, film thickness on a hard floor (mm), and
flags. Residues are cleaning's; the physical columns move here from food `LIQUID_DENSITY` and cleaning `SPILL_PROPS`.

| liquid | rho | visc cP | freeze °C | floor hazard | film mm | flags |
|---|---|---|---|---|---|---|
| water, dirty_water | 1000 | 1 | 0 | wet | 1.5 | |
| soapy_water | 1005 | 1.2 | -0.5 | slip | 1.5 | |
| milk | 1030 | 2 | -0.5 | wet | 1.5 | perishable residue |
| juice, soda | 1045 | 1.5 | -2 | wet (sticky) | 1.5 | |
| coffee, tea | 1000 | 1 | 0 | wet | 1.5 | stains |
| wine | 990 | 1.2 | -7 | wet | 1.5 | stains |
| beer | 1010 | 1.5 | -2 | wet | 1.5 | |
| vinegar | 1010 | 1 | -2 | wet | 1.5 | |
| soup | 1030 | 10 | -1 | wet | 2.0 | |
| oil, cooking_oil, olive_oil | 920 | 60 | -5 | slip | 2.5 | flammable when hot |
| motor_oil | 880 | 250 | -30 | slip | 2.5 | toxic |
| honey | 1420 | 10000 | - | sticky | 3.0 | |
| syrup | 1330 | 3000 | - | sticky | 3.0 | |
| dish_soap | 1030 | 300 | -5 | slip | 2.0 | |
| bleach | 1100 | 1 | -10 | wet | 1.5 | toxic, corrosive |
| alcohol | 790 | 1.2 | -114 | wet | 1.0 | flammable |
| gasoline | 740 | 0.6 | - | slip | 1.0 | flammable, toxic |
| paint | 1300 | 1000 | 0 | slip | 3.0 | |
| (default) | 1000 | 1 | 0 | wet | 1.5 | |

On carpet the film is 3 mm, so the puddle area is smaller and the liquid soaks in.

### 4.5 Floors (`floors.json`)

The values come unchanged from manipulation's `FLOOR_HARDNESS`, `FLOOR_SCRATCH` and `FLOOR_FRICTION`, plus rolling
resistance and water tolerance.

| floor | impact hardness | scratch | mu (wood/plastic foot) | c_rr (casters, balls) | water tolerance |
|---|---|---|---|---|---|
| tile, stone, concrete, marble | 1.0 | 0.25 / 0.2 / 0 / 0.5 | 0.35 / 0.4 / 0.55 / 0.4 | 0.01 | unlimited |
| hardwood | 0.85 | 1.0 | 0.3 | 0.01 | standing water 3,600 s -> `water_damage` |
| laminate | 0.85 | 0.8 | 0.3 | 0.01 | 1,800 s -> `swollen` |
| vinyl | 0.75 | 0.7 | 0.35 | 0.01 | unlimited |
| wood (deck, porch), plywood | 0.8 / 0.7 | 0.7 / 0.3 | 0.35 / 0.4 | 0.015 | 7,200 s (outdoor wood) |
| rubber (gym) | 0.45 | 0.1 | 0.8 | 0.03 | unlimited |
| carpet | 0.35 | 0 | 0.6 | 0.06 | absorbs; mildew after 24-48 h wet (cleaning owns) |
| rug on a hard floor | 0.45 | 0 | 0.6 | 0.06 | absorbs |
| grass, soil, mulch, sand | 0.3 / 0.25 | 0 | 0.7 | 0.10 | absorbs |
| bed, sofa, cushion (as landing) | 0.15 | - | - | - | absorbs |

`foot_factor` multiplies mu: felt pad 0.5, slider 0.3 (0.5 on carpet), rubber foot 2.0 (mu capped at 1.0),
metal 1.0. Casters use c_rr instead of mu. `FOOT_SCRATCH` comes unchanged from manipulation.

### 4.6 Supports, shapes, robot and people (`supports.json`)

**Rated loads (kg).**

* **Per shelf:** particleboard bookshelf 25 (matches admin); wire or steel shelving unit 90; closet shelf 25; upper
  cabinet 20; base cabinet 30; filing cabinet 30; wardrobe or closet 40.
* **Surfaces:**

  | surface | rating (kg) | note |
  |---|---|---|
  | floating wall shelf | 12 | |
  | side or end table | 20 | |
  | nightstand | 15 | |
  | coffee table | 50 | |
  | desk | 70 | |
  | dining table | 100 | distributed |
  | glass-top table | 25 | tempered |
  | TV stand | 45 | |
  | counter | 200 | |
  | chair seat | 113 | BIFMA, 250 lb |
  | step stool | 90 | ANSI A14.2 type III, 200 lb |
  | windowsill | 10 | |
  | dresser top | 40 | |

**Hang ratings (kg).** Adhesive hook: small 0.45, medium 1.4, large 2.3. Screw hook in a stud 20. Screw hook in
drywall with an anchor 7. Curtain rod 8. Towel bar 8. Coat hook 10. Clothes rod 25 per metre.

**Container and handle ratings (kg).** These reuse existing type values where present (`handle_rating_kg` on
manipulation and plumbing bags and buckets):

* plastic grocery bag 7, paper grocery bag 6 (as typed), reusable tote 15;
* laundry basket 15, medium cardboard box (bottom) 15, moving box 30;
* bucket handle 25;
* trash liners stay as typed.

**Rims (`rim_m`).** Tray 0.02, saucer 0.015, baking sheet 0.02, boot tray 0.03, plant saucer 0.03, sink 0.2,
tub 0.5.

**Shapes.**

* Cylinder: bottle, can, jar, glass, mug, candle, vase, rolling pin, paper roll.
* Sphere: ball, orange, apple, onion, ornament.
* Flat: plate, tray, book, board.
* Long: broom, mop, bat, ski, rod.
* Default: box.

**`rollable`:** a sphere, or a cylinder lying on its side.

**COM fraction of height.** Default 0.5. Floor lamp 0.25 (weighted base). Table lamp 0.35. Empty stemware 0.6.
Full bottle 0.45. Umbrella with base 0.2. Pedestal fan 0.6. Composite COM (section 5.1) overrides these when there
are contents.

**Tempered by type:** glass tabletop, oven door, shower door, glass shelf.

**Heat-resistant supports** (heat limit 400 °C): trivet, hot pad, oven rack, stovetop, grill grate, sink basin.

**Fillers** for the `load` setup op: books (1.0 kg), canned goods (0.4 kg), storage boxes (3 kg), towels (0.5 kg).
Each filler is a registry type when the owning domain is loaded, else a `generic_item` with explicit physical props.

**Robot** (added to `schema.ROBOT_DEFAULTS` and read only through `forces.robot_caps`, see section 14):

* new: `max_push_n` 220 (manipulation's `ROBOT_PUSH_N`), `max_pull_n` 180, `max_grip_n` 90 (manipulation's firm
  grip cap);
* existing: `max_hand_kg` 8, `max_mass_kg` 20.

**People.** `lift_kg` default by role: adult 23 (the NIOSH lifting-equation load constant), teen 15, elderly 10,
child, infant or mobility-limited 0. `push_n`: adult 200, teen 120, elderly 80. People own the actual values.

### 4.7 Per-instance variation (deterministic, no state)

```
vary(state, eid, law) = 1 + 0.15 * (2u - 1)
u = crc32(salt | eid | type | law) / 2**32
salt = crc32 of the sorted (room id, size_m) pairs     (memoised per World)
```

It applies to `drop_tol_m`, `crush_kg`, `rating_kg`, `hang_kg`, `handle_kg`, `shock_k` and `heat_limit_c`. It never
applies to geometry. `phys_exact: true` on an entity disables it, and tests use that. The value is a pure function
of state, so replays and other processes see the same thresholds and no new field is stored.

### 4.8 Optional fields (sparse; existing names reused)

| field | meaning | tier |
|---|---|---|
| `material` | material string (aliased) | inspect |
| `fragile`, `brittle`, `rigid`, `soft`/`softness` | strength class overrides | inspect (`fragile`) / hidden |
| `drop_tolerance_m`, `crush_kg`, `crush_force_n` | strength thresholds | hidden |
| `max_load_kg` (surface or container), `shelf_load_kg`, `hook_rating_kg`, `handle_rating_kg` | ratings | hidden |
| `anchored` | furniture fixed to the wall | inspect (seen from behind or on inspection) |
| `secured` | fastened against wind or motion | glance |
| `com_frac`, `shape`, `tilt_deg` | stability geometry | hidden / glance / glance |
| `waterproof`, `absorb_l_per_kg`, `perforated`, `rim_m`, `mouth_frac`, `opening_m` (alias `opening_width_m`) | liquids and containment | hidden (except `perforated` inspect) |
| `nest_group`, `nest_frac`, `compressible` | containment | hidden |
| `heat_limit_c`, `melt_c`, `thermal_shock_c`, `thermal_owner` | heat | hidden |
| `friction_mu`, `foot_material`, `casters`/`wheeled`, `pads` | friction | hidden / inspect / glance / inspect |
| `two_handed`, `lift_kg` (people) | lifting | hidden |
| `load_rating_text` | a printed rating label staged by a spec ("max 15 kg per shelf") | read |
| `phys_exact`, `_phys_staged`, `phys_creep_s` | test and staging controls | hidden |

### 4.9 The `Phys` record

`props(state, eid)` returns a `NamedTuple Phys` with these fields:

* identity and size: `cls` (material class), `mass_kg` (own), `size_m` (resting: on its side swaps height with the
  longest footprint side), `shape`, `com_frac`;
* strength: `brittle`, `rigid`, `fragile`, `drop_tol_m` (`inf` when unbreakable), `crush_kg` (`inf` when unlimited),
  `hard`;
* surface and liquid behaviour: `mu`, `waterproof`, `absorb_l` (absorption x dry mass), `softness`, `wsens`,
  `combustible`;
* heat: `c_p`, `heat_limit_c`, `melt_c`, `shock_k`;
* ratings: `rating_kg`, `shelf_rating_kg`, `hang_kg`, `handle_kg`, `rim_m`;
* containment: `mouth_frac`, `opening_m`;
* stability and motion: `rollable`, `floats` (bulk density < 1000 and waterproof), `anchored`, `secured`.

Variation (section 4.7) is already applied.

## 5. Laws and formulas

Every law has a `law_id` used in `laws.json`. **Laws are the kernel's automatic application during settle.** The
underlying functions are always callable by domains and events, whatever the law's state.

### 5.1 Support graph and loads (`support.py`, G)

```
support_of(e) =
  (parent, ("shelf", k))   relation "in" and the parent has shelf_heights      (k = e.shelf or 0)
  (parent, ("in",))        other relation "in"
  (parent, ("surface",))   relation "on" onto an entity
  (parent, ("hung",))      relation "hung"
  (room,  ("floor", cell)) located in a room, not mounted
  ("robot"|person|pet, ("held",))
  None                     attached / part / mounted / under / behind (rigid with the parent or not supported by it)
```

* **Children map:** built once per settle phase, O(N); about 0.15 ms on 900 entities. Consequences update it
  incrementally.
* **Mass:** `subtree_mass(e) = sum over e and its descendants of mass_kg + liquid_l * rho_liquid`. Here
  `rho_liquid` = the entity's `liquid_density` if present, else `liquids.json`, else 1.0. `core.subtree_mass`
  delegates to it, using the children map instead of the O(N²) `w.descendants` scan.
* **Slot load:** `load_on(sid, slot) = sum(subtree_mass(c) for c in slot)`.
* **Composite COM height of a body above its own base:**
  `z = (m_body * com_frac * h + sum m_i * z_i) / m_total`. Here `z_i` = shelf height + h_i / 2 for items in a shelf,
  or top + h_i / 2 for items on top. Heavy books on the top shelf raise z.
* **Baseline allowance:** `baseline_load(env, sid, slot)` is computed from `env._baseline` with its own children
  map, memoised per episode on the env object and never stored. It mirrors admin's `_baseline_load`.

### 5.2 Overload, hang and handle (`overload`, `hang`, `handle`; P-approx)

```
rating_eff = rating * vary(sid, "rating")
limit      = rating_eff if support._phys_staged else max(rating_eff, baseline_load(slot) + 0.5)
sagging    = load > 0.9 * rating_eff                       -> glance cue (written with _phys_sag marker)
failure    = load > limit
```

* **Shelf k fails:**
  * the furniture gets `collapsed_shelves += [k]` and `damage.collapsed` = 1.0;
  * shelf k's items move to shelf k-1 if it exists (impact from the shelf gap), else to the floor (impact from the
    shelf height);
  * the receiving shelf is re-checked (cascade);
  * `place` refuses onto a collapsed shelf ("That shelf is broken").
* **Surface fails:**
  * brittle tops (glass) break, and their items fall;
  * wall shelves pull out: the shelf and its items fall, and the wall surface gets `damage.hole` 0.3;
  * other furniture gets `damage.collapsed` and its items fall.
* **Hang:** hung items with total mass > `hang_kg_eff` pull out and fall from the hang height. Adhesive hooks detach
  too. A screw hook with a drywall anchor leaves `damage.hole` on the wall.
* **Handle or bottom** (only while lifted or carried, never while resting): when a carried container's contents mass
  exceeds `handle_kg_eff * (0.4 if cardboard and wetness >= 0.5 else 1)`:
  * the container gets `damage.torn` and stays in the hand;
  * its contents fall from hand height (1.0 m) at the robot's cell.

  At 0.9-1.0x the rating, a creep timer tears it after 120 s of carrying.
* **Staged creep:** a support staged with `phys_creep_s` and loaded above its true rating collapses at that time
  unless it has been unloaded by then. This is for "the shelf is about to give way" tasks.

### 5.3 Crush (`crush`)

`load_above(X)` is the mass stacked on X: items `on` X, recursively, plus any ids returned by the
`phys.items_above` hook (manipulation's packing order inside containers).

X is crushed when `load_above(X) > crush_kg_eff`, where:

```
crush_kg_eff = crush_kg * vary * (1 + min(1.5, padding_mm / 10))    for a full wrap
                        * 3      for plates on edge or in dividers
                        * 0.4    for wet cardboard
```

* Brittle items break.
* Soft items (bread, chips, produce, cardboard) get `damage.crushed` with severity `min(1, load / crush - 1)`.
* A crushed box passes its load to its contents.

These are manipulation's `max_load` rules, unchanged.

### 5.4 Statics (`edge`, `slope`, `stack`, `furniture_tip`)

**Overhang (G).** The supported fraction is the overlap of the footprint with the support rectangle. The item falls
when its COM projection leaves the support. Ordinary placement cannot overhang (`surface_spot`), so this triggers
for the `edge` setup op, tilted supports, a support shrinking or folding, and domain moves.

**Tip versus slide under a horizontal acceleration a (in g).** b is the base width along the push and z the COM
height. The smaller threshold wins.

* tip: `a > b / (2 z)`
* slide: `a > mu`

Anchors:

* An empty wine glass (b 7 cm, z 11 cm) tips at 0.32 g.
* A mug (b 8 cm, z 5 cm) needs 0.8 g, so it slides at mu = 0.5 instead.
* A full wine bottle tips at 0.31 g. A table lamp tips at about 0.5 g.

**Slope or tilt θ** (room `slope_deg`, support `tilt_deg`):

* slide if `tan θ > mu`;
* tip if `tan θ > b / (2 z)`;
* a rollable item rolls if `tan θ > c_rr`. A sphere's speed after distance d is `v = sqrt(10/7 * g * d * sin θ)`. It
  is a runaway when v > 1 m/s at the support edge or the room edge.

Rolling is a timed process (section 6.2).

**Stacks** (manipulation's rules, unchanged):

* an upper item more than 1.25x the base footprint of the item below topples;
* an offset beyond 0.3 of the base topples;
* height / base > 6 topples, and > 4 is `wobbly` (glance);
* a stack taller than the shelf gap topples.

Toppled items get `tipped_over`, orientation `on_side`, and fall if they protrude past the edge.

**Furniture tip** (generalising mechanisms' dresser), with the pivot at the front edge:

```
tip_margin = m_total * g * x_back - ( sum(m_k * g * x_k for loads beyond the front edge)
                                      + F_pull * h_pull + m_climber * g * x_c + sum(phys.tip_loads) )
```

* Open drawers count as loads at `x_k = extension - depth / 2`; mechanisms supplies them through `phys.tip_loads`.
* `anchored` makes the margin infinite.
* ASTM F2057-23 uses about 27 kg (60 lb) on an open drawer as its test load.
* tip_margin < 0 calls `tip_over`: orientation `on_side`, `tipped_over`; contents and items on top fall; people and
  pets in the fall cells go to `phys.tipped` (injury is decided by people, pets and safety).

**Pushing at contact height h.** It needs `F = mu(foot, floor) * m * g`; casters need `c_rr * m * g`. The item tips
before it slides if `mu * h > b / 2`: an unanchored bookcase (d 0.30 m, mu 0.4) pulled above 0.375 m tips. These are
manipulation's `slide_force_ok` and `tip_check`, moved verbatim.

### 5.5 Impact, breakage and floor damage (`impact.py`)

`fall(w, eid, height_m=None, cause, at=None, onto=None, by=None)` works in this order:

1. Release the item from the hands.
2. Pick the landing spot: the first in-grid cell not blocked by floor-standing furniture in the ring around the
   support footprint. Candidates are sorted by (toward the overhang or push direction, in front of a wall-backed
   support, row, col). The fallback is the support's own anchor cell. `onto` overrides this, for example a bed.
3. Compute `h_eff = h * hardness(landing) / padding_factor`, where
   `padding_factor = 1 + padding_mm / 3` (full wraps; hollow and unstuffed items x0.5; dividers +6 mm).
4. Break if `h_eff > drop_tol_eff`. Brittle-but-chipping classes (stone, enamel, engineered wood) get
   `damage.chipped` instead.
5. Floor damage (manipulation's rule): mass >= 3 kg, h >= 0.3 m, no rug, and a floor with scratch >= 0.2 gives the
   floor `damage.dent`. Tile gets `damage.cracked` when `m*g*h > 40 J`.
6. An open liquid container spills (section 5.7). An open container falling more than 0.2 m scatters its contents,
   each falling from `min(h, 0.6)`.
7. Emit `phys.fall` and run `phys.fell` with info: height, room, cell, landed_on, struck agent ids, cause, by.

`break_entity(w, eid, cause, by)`:

* is idempotent;
* sets `broken`, `condition: "broken"` and `damage.broken` = 1.0, and releases the liquid as a spill;
* gives brittle classes `hazards += ["sharp"]` and, on a floor, `floor_hazard: "sharp"`;
* runs `phys.broke` (safety may spawn a shards entity; electrical disables devices; food turns eggs into a raw-egg
  spill).

**Set-down** (`setdown`, core `place` only): the equivalent drop is careful 0, normal 0.02 m, fast 0.06 m, times the
support's `hard`. An egg (0.03 m) survives a normal set-down on stone and breaks on a fast one.

### 5.6 Forces, lifting, scratching and containment (`forces.py`, `contain.py`)

**`lift_plan(w, eid, hands=None, helpers=())`** returns `{ok, hands, reason, mass_kg}` and uses core's exact
thresholds today:

* two hands if mass > `max_hand_kg`, or the largest footprint side > 0.7 m, or `two_handed`;
* refuse ("too heavy to lift alone") if mass > `max_mass_kg`, unless the robot capacity plus the `lift_kg` of
  present, awake, consenting helpers covers the mass. The helper verb belongs to people and manipulation.

**Pushing:** `push_needed_n(w, eid, floor)` against `max_push_n(w, helpers)` (robot 220 N plus helpers' `push_n`).
Anchors: a 60 kg sofa on hardwood needs 177 N and moves; a 100 kg loaded dresser needs 294 N and is refused; two
people move it.

**Scratching:** `scratch(w, floor_id, mover, distance_m)` uses manipulation's `scratch_risk`, unchanged. The risk is
`FLOOR_SCRATCH * FOOT_SCRATCH * min(1, m / 4 kg)`, plus 0.5 x floor scratch when floor grit >= 0.2. The severity per
metre is the risk x 0.1. Felt, sliders and casters score 0.

**Containment:** `fits(state, item, container, count_existing=True) -> (ok, reason)` checks, in order:

1. **Box fit.** Sorted dimensions fit (the current `fits_inside`). Long items may also fit **diagonally** when
   `L <= sqrt(W² + D² + H²) - t` and `t <= 0.3 * min(W, D, H)`, where t is the second-largest item dimension.
2. **Opening.** The two smallest item dimensions must pass `opening_m`. Without it, the opening is
   `inner[:2] * mouth_frac`, and is unrestricted when neither is given. Items with softness >= 0.6 squeeze through
   1.5x.
3. **Volume.** The sum of `volume_l` is at most `capacity_l` (the current rule). Items after the first of the same
   `nest_group` count `volume_l * nest_frac` (bowls 0.3, cups 0.4). Compressible textiles count
   `volume_l * (1 - 0.5 * softness)`.
4. **Choking.** `choking_hazard(record)` is True when the item fits the CPSC small-parts cylinder (16 CFR 1501:
   31.7 mm diameter by 57.1 mm).

Core `place` switches from `geometry.fits_inside` to `contain.fits`. The difference is limited to declared openings,
nesting groups and diagonal fits.

### 5.7 Liquids (`liquids.py`)

* `liquid_mass = liquid_l * rho`.
* `fill = liquid_l / capacity_l`.
* `headroom_m = (1 - fill) * inner_h`, where inner_h = `inner_size_m[2]` or 0.9 x h.

**Tilt or tip** (`open_spill`). An open container (no cap, lid, seal or closed closure: manipulation's
`container_closed`, moved) loses liquid as follows:

* tilted by angle α: spill begins when `tan α >= 2 * headroom / D`;
* on its side: a wide mouth (`mouth_frac >= 0.6`, the default for cups, bowls and pans) loses everything; a narrow
  neck keeps `capacity * (1 - mouth_frac) / 2`;
* upside down: everything.

**Slosh during travel** (`carry`): manipulation's model, unchanged.

```
amplitude = 0.010 * motion * sqrt(D / 0.08)
spill_l   = (amplitude - headroom) * area * 0.6            when amplitude > headroom
```

The motion factor comes from section 5.8.

**Pour rate:**

```
pour_rate_lps = opening_rate * min(1, sqrt(20 / visc_cP))
opening_rate:  narrow neck 0.25, pitcher spout 0.5, wide or bucket 2.0
```

Honey comes out about 22x slower than water, oil about 1.7x. Plumbing's `do_pour` stays the verb.

**`transfer(w, src, dst, liters, by=None)`** returns `(delivered, overflow_l)`:

* it marks `contents_from` (the core helper);
* overflow, and anything poured into a `perforated` destination, becomes a spill at the destination;
* it calls `thermal.shock_check(dst, liquid temperature, coupling 1.0)`.

**`overflow` law:** when `liquid_l > capacity_l + 1e-6` the excess spills where the container stands.

**`spill(w, liters, liquid="water", at=None, onto=None, source=None, by=None)`** is the single entry point:

* below 5 ml the liquid is droplets: no entity and no harm. This unifies navigation `SPILL_MIN_L` and food
  `SPILL_HARM_G`.
* `onto` an entity: `wet(onto)`. Liquid beyond its film capacity (`top_area * 1.5 mm`), when it has no rim and is
  not absorbent, runs off to the floor at the support's cell.
* onto a floor: the first non-None `phys.spill_sink` result wins; cleaning's `make_spill` gives
  area = V / film thickness. The kernel fallback is a `generic_item` with `category: "spill"` plus room `wet_cells`.
* stats: `spills` if robot-attributable and at least 5 ml; `phys_spills` always. Event `phys.spill`.

**`absorb(w, absorber, source=None, liters=None)`** takes `min(V, absorb_l * (1 - wetness))`. The held water is
`wetness * absorb_l`, consistent with existing wetness semantics.

**`wet(w, eid, liters, liquid, source)`** raises wetness and moisture, then runs `phys.wetted`. Electrical uses it
for liquid ingress, paper for soaking, MDF for swelling and wood for rings.

**Drip process** (`drip`, started explicitly). `drip_start(w, eid, l_per_h, until_l=None)` deposits each substep on
the support chain below the source:

* a waterproof support with a rim pools the water up to `rim area * rim_m`, then overflows to the next support down
  or the floor;
* an absorbent support saturates, then passes the water down;
* other supports form a film, start the wet-contact timer and run off to the floor.

The source shows `dripping: true`, written with a `_phys_drip` marker so plumbing's faucet `dripping` is never
clobbered. Sources: wet umbrellas, thawing ice packs, a plant pot after watering, condensation runoff.

`evaporation_mm_s` is cleaning's formula, re-exported. Cleaning remains the drying owner.

### 5.8 Carry dynamics (`carry`)

The trigger is the action-phase settle seeing that the robot travelled (its room or position changed) while holding
items. Then:

```
motion = SPEED_FACTOR[speed_mode] (careful 0.5, normal 1.0, fast 1.7) * product(phys.carry_motion hooks)
         (navigation: stairs x1.8, threshold x1.15, outdoor uneven x1.3)
for each held H and each item X on or in H (recursively through "on"), and H itself if it holds liquid:
    slosh_spill_l(X, motion) -> spill onto H if H has a rim (wets it), else onto the floor at the robot's cell
    if X is upright, unsecured, relation "on", and motion > 2.8 * min(w, d) / h:
        X tips on H (tipped_over, on_side); if H has no rim and X is rollable -> fall from carry height
```

This replaces `ctx.data["carry_slosh_applied"]` and navigation's `"stack" in ACTIONS` check.

### 5.9 Thermal additions (`thermal.py` and `kernel/physics.py`)

**Thermal owner.** `thermal_owner: "<domain>"` makes the kernel skip an entity. `fixed_temperature` stays as an
alias, exactly like `drying_owner`.

**Time constant** (`thermal_tau`, deferred):

```
tau = (sum m_i * c_p,i over the body and its liquid) / (h * A)
h = 10 W/m²K (still air)
A = 2(wd + wh + dh) from size_m
clamped to [60 s, 6 h]; an explicit thermal_tau_s wins
```

Anchors: a 0.5 L bottle about 56 min; a mug of coffee about 50 min (85 -> 60 °C in about 25 min); a 2.5 kg cast-iron
pan about 8 min (200 -> 50 °C in about 15 min, plus radiation not modelled); a 60 W bulb about 90 s.

**Active-set optimisation** (always on, behaviour-neutral, a WP-3 target). Memoise the ambient per parent location
within a run, and skip entities within 0.05 °C of their ambient. Target <= 0.5 ms per substep amortized
(1.16 ms today).

**Contact heat** (`contact_heat`). A hot item H rests on a support S, where S is the direct parent, so a trivet or
pad in between protects. When `T_H > heat_limit_eff(S)`:

* S gets `damage.scorch` (wood, laminate, fabric, paper) or `damage.melted` (plastic, film, rubber, foam, wax);
* severity accrues as `min(1, (T - limit) / 100) * min(1, contact_s / 60)` through a 60 s process while H stays and
  is still above the limit;
* `phys.contact_heat` lets safety start a fire for combustibles above about 250 °C, and lets food or appliances
  claim their own marks.

**Wet contact and condensation** (`wet_contact`). An item W rests on S with `wsens(S) >= 0.5`: wood finish, MDF,
paper, leather, electronics. W is wet when `wetness >= 0.3`, or it is leaking, or it **sweats**: the item or its
liquid is below the dew point minus 1 °C, using Magnus with `T_room` and `humidity`. At 21 °C and 45 % RH the dew
point is 8.6 °C, so a 4 °C can sweats. Then:

* a check is queued at +600 s (wood: `damage.ring_stain`) or +900 s (MDF: `swollen`; paper and electronics:
  `water_damage` plus `phys.wetted`);
* the check fires only if W is still there and still wet;
* a coaster, placemat or tray in between protects S.

**Thermal shock** (`thermal_shock`). It breaks a brittle item when `|ΔT| * coupling > shock_k_eff`. Coupling is 1.0
for liquid poured in or immersion, 0.6 for a hot item set onto wet stone or metal, 0.5 for a cold dish into a hot
oven, 0.3 for freezer air. Anchor: boiling soup into a 4 °C soda-lime jar (96 K > 45 K) cracks it; a borosilicate
jar (160 K) survives.

**Phase change** (`phase`; non-food water-like liquids in containers, no thermal owner):

* at or below the freezing point the kernel holds T at T_f and advances
  `ice_frac += dt * (T_f - T_amb) / (80 K * tau)`, where 80 K = L / c for water (334 / 4.186); melting is symmetric;
* **burst** when the container is sealed and rigid and `0.09 * fill * ice_frac > headspace_frac`;
* glass with a narrow neck (`mouth_frac < 0.6`) also needs `headspace_frac >= 0.09 * fill + 0.03`;
* glass breaks, a metal can gets `damage.bulged` (it bursts when the excess is above 2 %), plastic bulges.

Food keeps its own model and calls `freeze_burst_check` for its containers.

**Melting** (`melt`). T above `melt_c` gives `damage.melted` or `deformed`, with severity accruing over time. Wax
melts at 50 °C, crayons at 55, plastics at 80-120.

**Burns.** `burn_seconds(T_c, child=False)` interpolates log-linearly over adult skin (Moritz and Henriques):

| °C | 44 | 47 | 50 | 55 | 60 | 65 | 70 |
|---|---|---|---|---|---|---|---|
| time to a burn | 6 h | 20 min | 3 min | 20 s | 5 s | 2 s | 1 s |

Children take x0.25. The robot's handling limit stays `max_touch_temp_c`. People and safety use `burn_seconds`.

**Heat content.** `heat_content_j(eid, T_ref)` gives the freezer heat load for appliances.

### 5.10 Wind and rain on objects (`wind`, `rain`; `environment.py`)

outdoor_plants keeps owning `weather` (speed, direction vector, rain). The kernel applies the effects to generic
objects. outdoor_plants' drag model is the canonical formula.

```
drag   F = 0.5 * rho_air * Cd * A * v²                    rho_air = 1.2, Cd default 1.2, A = frontal area
lift   flat sheets lift at v_lift = sqrt(2 g sigma / (rho_air * C_eff))   sigma = mass / top area
       C_eff 0.033 for flexible sheets (napkin about 3.5 m/s), 0.3 for rigid light shapes (paper plate about 3.6 m/s)
       an explicit wind_lift_mps wins
slide  F > mu * (m g - L) + secured_n + mu * g * (mass resting on it)       ballast counts: "find something heavy"
tip    F * h_c > m g b / 2          umbrellas close above 9 m/s (outdoor_plants calibration kept)
```

* **Blown items** move downwind (weather direction) by `min(room edge, d)` cells. They get `blown_away: true` (the
  manipulation name) and `phys_blown`.
* **Exposure:** an item is outdoors when its root room is outdoor and it is not under a sheltering cover. The
  `phys.exposed` hook lets outdoor_plants report porch roofs, umbrellas and covers.
* **Rain:** exposed items reach surface wetness 1.0 within 120 s. Absorbent items gain moisture at
  `rain_mm_h * top_area / absorb_l` per hour. Paper reaching moisture 0.3 is soaked (`phys.wetted`).

## 6. Processes

### 6.1 Settle (`settle.py`)

**Touch log (new World API, WP-4).** `World._snap()` appends the entity id to `w._touched` on every `mut`, `create`
and `destroy` of an entity, not only the first time it is journaled. `begin()` and `rollback()` clear the log.
`w.mut_quiet(eid)` journals without touching. Only the kernel thermal system uses it, for temperature-only writes,
which keeps thermal's hundreds of writes out of settle.

`w.phys_scratch` is a transient dict reset by `begin()` and `rollback()`. It holds the signature cache, the
consequence ledger and the phase.

```
settle(env, phase, ctx=None):                      # phase "action" (ctx given) | "substep"
  ids   = sorted(set(w._touched)); w._touched = []
  sig(e) = (location, relation, shelf, spos, pos, orientation, rot, open, mass_kg, liquid_l, size_m, anchored,
            secured, tilt_deg)
  before = scratch.sig.get(e) or journal snapshot of e (state at step start) or None (created)
  dirty  = [e for e in ids if before != sig(now)] + destroyed ids
  if phase == "action": mark each structurally dirty entity "_robot_moved": True (once); note robot travel
  if not dirty and not robot travel: return                                  # the common, O(1) case
  cmap  = children map (lazy)
  roots = dirty items + their old and new support chains to the top-level support + items that rested on them
  queue = FIFO over sorted(roots); budget = 64 consequences per phase
  for each queued node, apply active laws in registry order (order is fixed in laws.json):
      orphan(10) overflow(20) overload/hang(30) crush(40) handle(45) edge/slope(50) stack(55) furniture_tip(60)
      open_spill(70) contact_heat(80) wet_contact(85) thermal_shock(90) phase(95) settle_extra hooks(99)
  then, in the action phase, the carry law once
  consequences enqueue what they moved or landed on; budget exhausted -> emit phys.budget and stop (deterministic)
  scratch.sig[e] = sig(now) for every processed e
```

* **Idempotency.** Every consequence checks the ledger `(eid, kind)` for this step and skips entities already in the
  target state (`broken`, `fell` this step, a shelf already collapsed).
* **Notes (action phase).** Consequences add generic `ctx.note` lines that refer to ids, never numbers ("cup_1 fell
  and broke", "the shelf gave way"). Realistic mode translates the ids to handles or "something".
* **Undo.** When the action-phase settle produced any consequence, the engine drops that step's undo record. A
  broken glass cannot be undone.
* **Nested actions.** `env.perform`, undo and scheduled commands do not settle separately; the enclosing step's
  settle covers them. `_affordances` probes never settle.

### 6.2 Timed processes (`processes.py`, system `physics.processes`, order 48, max_dt 30)

The state section `physics` is created lazily on the first use:

```
physics = {"seq": n,
           "queue":  [[due_s, seq, kind, eid, args], ...]         sorted; popped while due_s <= time_s
           "active": {"rolling": [ids], "dripping": [ids], "leaking": [ids], "contact": [ids]}}
```

* **Kinds:** ring or soak checks, contact-heat accrual, bag creep, staged creep, phase change for non-food liquids,
  rolling (toward the edge at v, then a fall), dripping, leaking (a cracked or torn container loses liquid at a
  rate).
* **Cost** per substep is O(due + active).
* Entities record their motion as glance `in_motion: "rolling" | "sliding"` while active.

### 6.3 Environment (system `physics.environment`, order 47, max_dt 30)

It runs only while the `wind` or `rain` law is active **and** `weather.wind_mps >= 2.5` or rain is falling.
Candidates are a transient set of outdoor, non-fixed, portable items plus covers. The set is rebuilt in O(N) when the
World version changes or a settle moves something into or out of an outdoor room.

### 6.4 Harm attribution (new convention)

A consequence is **robot-attributable** when either:

* it happens in the action phase; or
* the affected entity, or any entity in the subtree of its top-level support, carries `_robot_moved`. That marker is
  an underscore field, so it is never observed. It is written by the action-phase settle on every structural change
  an action made.

| | Robot-attributable | Exogenous |
|---|---|---|
| Global flags | `breakages`, `spills` (>= 5 ml), `damage`, `injury`, `burns` (via hooks) | none |
| Events | yes | yes |
| `phys_*` stats | yes | yes |

Examples:

* A glass the robot set at the table edge, later knocked by a pet or a `physics.bump`, counts.
* A vase that stood there from the start and is knocked over by the dog does not. A spec that wants the robot to
  prevent that uses a `failure` predicate on `broken`.
* `collisions` stays navigation's (robot body contact).

Domain code that calls kernel consequences may pass `by="robot"` or `by="exogenous"` explicitly.

## 7. APIs (frozen contracts for the work packages)

### 7.1 Modules and signatures

```
props:       props(state, eid) -> Phys; material_class(state_or_none, record) -> str; liquid_props(name) -> dict;
             floor_props(state, rid, cell=None) -> dict; vary(state, eid, law) -> float;
             MATERIALS, LIQUIDS, FLOORS, SUPPORTS (read-only)
lawbook:     law(law_id, order) decorator; active(law_id) -> bool; laws() -> [(order, law_id, fn)];
             forced(**states) context manager (tests only)
support:     support_of(state, eid); children_map(state); subtree_mass(state, eid, cmap=None); load_on(state, sid,
             slot, cmap=None); rating(state, sid, slot) -> kg|None; baseline_load(env, sid, slot); com_height(state,
             eid, cmap=None); resting_size(state, eid); top_support(state, eid)
statics:     tip_accel_g(state, eid, axis=None); slide_accel_g(state, eid); stability(state, eid) -> {"overhang",
             "tip_g", "slide_g", "rolls", "wobbly"}; edge_margin_m(state, eid); stack_verdict(state, base_id) ->
             [(eid, "topple"|"wobbly")]; tip_margin_nm(state, furniture_id, pull_n=0, pull_h_m=0, extra=())
impact:      fall(w, eid, height_m=None, cause="fall", at=None, onto=None, by=None) -> dict;
             impact(w, eid, h_eff, cause, by=None) -> bool; break_entity(w, eid, cause, by=None) -> bool;
             crush(w, eid, load_kg, by=None); collapse(w, sid, slot, by=None); tear(w, eid, by=None);
             tip_over(w, eid, cause, direction=None, by=None); knock(w, eid, accel_g, direction=None, cause="bump",
             by=None) -> dict; damage(w, eid, kind, severity, detail="", by=None); robot_caused(w, eid) -> bool;
             landing_cell(state, eid, rid, prefer=None) -> [c, r]
forces:      robot_caps(w) -> dict; lift_plan(w, eid, hands=None, helpers=()) -> dict; push_needed_n(w, eid,
             floor_id=None) -> float; max_push_n(w, helpers=()) -> float; tips_when_pushed(w, eid, contact_h_m)
             -> bool; friction(rec_a, rec_b=None, wet=None) -> mu; scratch(w, floor_id, mover, distance_m, by=None)
contain:     fits(state, item, container, count_existing=True) -> (bool, reason); opening_ok(item, container);
             nested_volume_l(state, container); choking_hazard(record) -> bool
liquids:     liquid_mass(rec); fill_frac(rec); headroom_m(rec); container_closed(rec); tilt_spill_l(rec, deg);
             tip_spill_l(rec, orientation); slosh_spill_l(rec, motion); pour_rate_lps(src, opening=None);
             transfer(w, src, dst, liters, by=None) -> (delivered, overflow); overflow(w, eid, by=None);
             spill(w, liters, liquid="water", at=None, onto=None, source=None, by=None) -> eid|None;
             absorb(w, absorber, source=None, liters=None) -> liters; wet(w, eid, liters, liquid, source=None);
             drip_start(w, eid, l_per_h, until_l=None); drip_stop(w, eid); evaporation_mm_s(...)
thermal:     tau_s(rec); heat_limit_c(state, sid); contact_check(w, hot, support, by=None); dew_point_c(t, rh);
             sweats(state, eid); shock_check(w, eid, other_temp_c, coupling, by=None) -> bool; phase_step(w, eid,
             dt); freeze_burst_check(w, eid, by=None) -> str|None; melt_check(w, eid, dt); burn_seconds(t_c,
             child=False); heat_content_j(rec, t_ref)
environment: wind_force_n(rec, v); lift_speed_mps(rec); wind_effect(w, eid, v, direction_deg, by=None) -> dict;
             exposed(state, eid) -> bool; rain_wet(w, eid, mm_h, dt)
processes:   queue_add(w, due_s, kind, eid, **args); start(w, kind, eid); stop(w, kind, eid)
settle:      settle(env, phase, ctx=None) -> [consequences]; travel_of(env) -> dict|None
```

`house_sim.kernel.physics` re-exports the public names (the existing facade: `from house_sim.kernel import physics;
physics.fall(...)`).

### 7.2 Ctx helpers (`kernel/actions.py`)

* `ctx.phys(eid)` returns `Phys`.
* `ctx.fall(eid, **kw)`, `ctx.spill(liters, liquid="water", **kw)` and `ctx.knock(eid, accel_g, **kw)`: `by` is
  "robot".
* `ctx.lift_plan(eid, hands=None, helpers=())`.
* `ctx.settle()`: an early settle when a verb needs consequences inside its own message.

### 7.3 Hooks

| hook | signature | purpose |
|---|---|---|
| `phys.props` | `fn(record, phys_dict) -> dict or None`; optional `fn.applies(record) -> bool` (static filter) | domain refinements: ripeness -> softness, soaked textile mass, soil moisture in pots |
| `phys.spill_sink` | `fn(w, rid, cell, liquid, liters, source, onto) -> eid or None` | cleaning owns spill entities |
| `phys.wetted` | `fn(w, eid, liters, liquid, source)` | liquid ingress (electrical), paper ruin, MDF and wood damage |
| `phys.broke` | `fn(w, eid, info)` | shards (safety), dead devices (electrical), egg spill (food) |
| `phys.fell` / `phys.tipped` / `phys.collapsed` | `fn(w, eid, info)` (info includes `struck`: agent ids) | injuries (people, safety), pets startle, noise |
| `phys.contact_heat` | `fn(w, hot, support, temp_c, severity) -> True to claim` | ignition (safety), cookware marks (appliances) |
| `phys.carry_motion` | `fn(w, travel) -> multiplier` | stairs and thresholds (navigation) |
| `phys.tip_loads` | `fn(state, furniture_id) -> [(mass_kg, x_m, h_m)]` | open drawers (mechanisms) |
| `phys.items_above` | `fn(state, eid) -> [ids]` | packing order inside containers (manipulation) |
| `phys.exposed` | `fn(state, eid) -> False to shelter` | porch roofs, covers, umbrellas (outdoor_plants) |
| `phys.settle_extra` | `fn(w, sid, ctx)` | a domain's own checks inside the same settle pass |

### 7.4 Perturbation events (`events.py`; schedule with the existing `event` setup op)

| effect | args | models |
|---|---|---|
| `physics.bump` | `target` (item or surface) or `room`; `g` (0.1-0.5); `direction` (`toward_edge` default, or degrees); `max_h_m` | a dog or person brushing past: about 0.3 g; tail sweep at <= 0.6 m |
| `physics.nudge` | `target`, `newtons`, `height_m`, `direction` | a push on one item (cat pushing things off) |
| `physics.pull` | `target` furniture, `kg` or `newtons`, `height_m` or `at: "top_drawer"` | a child pulling or climbing: 27 kg (ASTM F2057) |
| `physics.load_test` | `target` support, `slot`/`shelf`, `kg` | a test load or a person leaning |
| `physics.wind_gust` | `room` or `outdoor: true`, `mps`, `seconds`, `direction_deg` | a gust |
| `physics.rain` | `mm_h`, `seconds` | a shower (does not change outdoor_plants' weather state) |
| `physics.transport` | `target`, `drop_m` (ISTA 1A: 0.76 m for parcels up to about 10 kg; house moves 0.3 m), `drops`, `jostle` | shipping or moving a packed container |
| `physics.brake` | `target` (surface, trunk or cart), `accel_g` (car braking 0.4) | securing loads against sliding or rolling |
| `physics.tilt` | `target`, `deg` | tilting a tray, shelf or cart |
| `physics.drop` | `target`, `height_m` | someone drops it (scenario-driven) |

Events call kernel functions directly, so they work whatever the law states. Consequences follow the attribution
rule in section 6.4.

**Recipe "perturb after completion":**

```
{"op": "event", "when": <the goal arrangement predicate>, "effect": "physics.bump", "args": {...}, "repeat_s": 300}
```

The event fires in the same step the arrangement first holds, because events run per substep after the action, and
then every 300 s. Evaluation sees the result.

### 7.5 Setup ops (`tasks/setup.py`)

| op | shape | does |
|---|---|---|
| `phys` | `{"op":"phys","target":"{x}","props":{...}}` | validated physical overrides (whitelist from section 4.8; ranges checked) |
| `load` | `{"op":"load","target":"{shelf}","shelf":2,"kg":18 \| "fraction":0.8,"items":"books\|cans\|boxes\|towels\|auto","var":"v"}` | fills a slot deterministically with realistic fillers up to kg, or a fraction of the nominal rating, respecting fit and capacity; binds the ids |
| `edge` | `{"op":"edge","target":"{glass}","on":"{table}","margin_m":0.02,"side":"front"}` | places an item at a given distance from an edge, still statically stable (overhang allowed down to 30 % of its footprint) |
| `stage` | `{"op":"stage","target":"{x}","state":"overloaded","creep_s":600}` | `_phys_staged` (no baseline allowance) and optional creep |
| `check_physics` | `{"op":"check_physics","strict":true}` | SpecError on an impossible staged scene (see below) unless the entity carries `_phys_staged` |

`check_physics` rejects:

* a support over its rating;
* an item larger than its support, or with its COM off the support;
* liquid above capacity;
* an item that does not fit its container;
* an open container with liquid lying on its side;
* a hot item on a support below its heat limit;
* `on` onto a non-surface.

The verifier runs it automatically as a lint after setup: warnings for existing specs, errors for specs authored
after v3.0.

### 7.6 Observation fields, sounds, inspector

* **Glance** (added through `core.json` field_tiers where not already in `DEFAULT_TIERS`): `tipped_over`, `toppled`,
  `fell`, `collapsed_shelves`, `sagging`, `wobbly`, `in_motion`, `blown_away`, `dripping`, `overflowing`,
  `spilled`, `secured`, `shape`, `tilt_deg`, `casters`.
* **Inspect:** `damage` kinds (scratch, dent, chipped, cracked, scorch, melted, crushed, collapsed, swollen,
  ring_stain, water_damage, torn, bulged, hole), `anchored`, `perforated`, `foot_material`, `pads`.
* **Read:** `load_rating_text`.
* **Hidden:** every threshold in section 4.8, plus `_robot_moved`, `_phys_*` (underscores are already never
  observed).
* **Observer `physics.sounds`:** reads this step's `phys.*` event-log entries and emits
  `{"kind": "crash" | "thud" | "glass_breaking" | "splash" | "scrape" | "creak" | "clatter", "source_hint": "here" |
  "nearby" | "elsewhere", "loudness": "faint" | "noticeable" | "loud"}`. Loudness comes from `m*g*h`; glass breaking
  is always at least noticeable. Ids appear only for the robot's own room, translated by realism.
* **Inspector `physics`:**
  * after a touch grant: `feel` ("soft"/"firm"/"hard", "flexible"/"rigid") from softness and rigidity;
  * at inspect: `looks` ("sturdy"/"flimsy" construction) from the rating class.

  Neither includes a number.

### 7.7 Stats and event-log kinds

* **Global harm flags** keep their meaning, gated by attribution: `breakages`, `spills`, `damage`, `injury`, `burns`.
* **Domain-local, non-harm stats:** `phys_falls`, `phys_breaks`, `phys_spills`, `phys_tips`, `phys_collapses`,
  `phys_blown`, `phys_rolls`, `phys_budget_hits`.
* **Event-log kinds:** `phys.fall`, `phys.break`, `phys.chip`, `phys.crush`, `phys.collapse`, `phys.pullout`,
  `phys.tear`, `phys.tip`, `phys.slide`, `phys.roll`, `phys.spill`, `phys.overflow`, `phys.scorch`, `phys.melt`,
  `phys.ring`, `phys.shock`, `phys.burst`, `phys.blown`, `phys.scratch`, `phys.dent`, `phys.budget`.
* **Payloads** carry `target`, `cause`, `by`, `room` and oracle numbers (heights, litres, loads). The event log is
  never shown to realistic agents.

## 8. Realistic perception

* **Hidden thresholds.** All ratings, tolerances and limits are hidden tier (`core.json` field_tiers). This closes
  the configuration-dependent leak in section 2.
* **Derived properties are never record fields, so they are never rendered.** The robot learns them honestly:
  * material at inspect;
  * mass by heft (touch bucket) or a scale;
  * `feel` after touching;
  * `looks` at inspect;
  * `sagging` and `wobbly` at a glance;
  * printed ratings by `read`ing a staged `load_rating_text`;
  * or by trying.
* **Consequences are public.** A fall, break, spill, collapse or tip is visible at a glance and audible, whatever
  the robot believed.
* **Refusals are capability refusals only**, with fixed wording: "too heavy to lift alone", "it will not move", "it
  does not fit". They depend on true mass or size only after physical contact, and an attempt is a physical probe.
  An unresolved look-alike never gets a message branch that depends on hidden props. When a verb's outcome depends
  on identity, it calls `ctx.refuse_unidentified` first.
* **No numbers in realistic messages** (loads, heights, thresholds). Oracle messages may keep them.
* **`tools/perception_audit.py`** gains two checks:
  * no `phys.*` note or observer entry leaks a hidden field or number;
  * `wobbly` and `sagging` appear only on entities whose true state matches.

## 9. Determinism and replay

* **No RNG.** Per-instance spread is the hash factor in section 4.7.
* **Ordering.** Every iteration runs over sorted ids. Cascades are FIFO with sorted children and a 64-consequence
  budget per phase, which ends the same way every time. The queue is ordered by `(due_s, seq)`.
* **Values.** Floats are rounded to 4 decimals before storing.
* **Transient state.** Caches, the touch log, `phys_scratch` and the baseline memo are transient and pure functions
  of state. Save and load happen between steps, when all of them are empty or re-derivable.
* **Law states** come from `laws.json` (data), never from which domains happen to be loaded. Domain sets are already
  covered by `actions_fingerprint`.
* **Replays.** `env.save_replay` records `metadata.physics_version` (a digest of `data/physics/*.json`); replay
  warns when it differs. `audit.fingerprint` already covers the data.
* **Tests:**
  * two subprocesses with different `PYTHONHASHSEED` produce identical digests;
  * a save/load and replay roundtrip matches;
  * `HOUSE_SIM_STRICT=1` passes over every new test (all writes go through `w.mut`, `w.mut_quiet` or `w.sec`).

## 10. Compatibility and migration

### 10.1 Landing guarantee

At landing (Phase K) only laws that **no domain duplicates** are active. All perturbation events, setup ops,
functions, hooks, tiers and data are available. The 242 specs and the 16 domain test suites must pass unchanged
except for three deliberate, reviewed differences:

* hidden thresholds in realistic mode (affects only references that read a threshold at inspect, which would be an
  oracle leak);
* core `place` uses `contain.fits` (it differs only with declared openings, nesting or diagonals);
* core `place` refuses onto collapsed shelves and broken supports.

### 10.2 Law states at landing (`laws.json`)

| law | state at landing | duplicated by (who must adopt before the flip) |
|---|---|---|
| orphan, overflow, setdown, drip, creep | active | - |
| overload | deferred | admin_inventory (`storage._load_check`) |
| hang | deferred | manipulation (hook ratings), mechanisms (curtain rods) |
| handle | deferred | manipulation (bags, seams), plumbing_liquids (bucket handles), cleaning (liners) |
| crush | deferred | manipulation (`max_load`, stacking) |
| edge | deferred | manipulation (`handling.after_placement`, edge margin) |
| slope | deferred | manipulation (runaway, rolling watch), outdoor_plants (slopes) |
| stack | deferred | manipulation (`settle_stack`) |
| furniture_tip | deferred | mechanisms (dresser), manipulation (`tip_check`), safety (`tip_over`), climate (fan topple) |
| open_spill | deferred | manipulation (tilt and orientation verbs), plumbing_liquids |
| carry | deferred | manipulation, navigation_robot, plumbing_liquids, pets, climate |
| contact_heat | deferred | manipulation (scorch), appliances_cooking (`surface_heat_limit_c`) |
| wet_contact | deferred | manipulation (ring stains) |
| thermal_shock | deferred | appliances_cooking, cleaning (column) |
| phase | deferred | plumbing_liquids (ice), food (food liquids stay food-owned) |
| melt | deferred | admin_inventory (`MELT_MATERIALS`), textiles_laundry (iron) |
| thermal_tau | deferred | re-verification by appliances_cooking, food, plumbing_liquids, electrical (cooling times) |
| wind | deferred | manipulation (`manipulation.wind`), outdoor_plants (`_object_wind`), mechanisms, admin_inventory (curb) |
| rain | deferred | outdoor_plants, mechanisms, admin_inventory |

### 10.3 Adoption protocol (per domain, Phase D)

1. Route the domain's own verbs and systems through the kernel functions.
2. Guard each legacy hook or system with `if lawbook.active("<law>"): return  # kernel owns it`.
3. Register the domain's hooks: sinks, wetted, broke, carry_motion, tip_loads and so on.
4. Update tests that assert old divergent constants, citing this spec.
5. Fix the type data that `tools/physics_audit.py --data` attributes to the domain.
6. Report "ready to flip: <laws>".

The main session flips a law only when **every** domain listed for it has reported. It then re-runs all suites plus
all 242 specs on 5 seeds in both perception modes. A failing spec is triaged: either the physics is right and the
spec's owner fixes the spec, or the physics is wrong and the kernel is fixed.

### 10.4 Other consumers

* **Eval harness:** no interface change. Outcomes move only where flips change them; `audit.fingerprint` and
  `harness_fingerprint` already separate evidence.
* **Viewer** (main-session follow-up): list `phys.*` events in the step timeline, show `sagging`, `wobbly`,
  `collapsed_shelves` and `in_motion` in entity panels, and show `physics_version` in the header. Traced replays
  recorded before v3.0 that diverge already fall back to recorded changes.
* **Saved states and replays:** the actions fingerprint changes only if a core action's parameters change, and this
  spec adds none. State digests change from the first structural action because of `_robot_moved`. Pre-v3.0
  replays therefore report divergence; that is expected for a kernel round, and evidence is regenerated.
* **Docs** (WP-4):
  * `KERNEL_API.md` gets a v3.0 section, and a new `docs/PHYSICS.md` is the user-facing reference built from this
    spec;
  * `CONVENTIONS.md`: fields, laws, attribution, `thermal_owner`;
  * `AUTHORING_GUIDE.md`: physics recipes, the perturbation pattern, `check_physics`;
  * `PERCEPTION.md`: hidden thresholds, feel and looks, sounds;
  * `KNOWN_LIMITS.md`: rewritten physics paragraph from section 1.

## 11. Performance budget

Baseline measured today (seed 0, 40 substeps): 3.60 ms per substep, thermal 1.159 ms, 1 h wait 0.24 s.

| item | budget |
|---|---|
| `settle("substep")` when nothing structural was touched | <= 0.02 ms |
| `settle("action")`, typical | <= 1 ms |
| `settle("action")`, 64-consequence worst case | <= 10 ms |
| `physics.processes`, idle | <= 0.02 ms per substep |
| `physics.processes`, 10 active | <= 0.2 ms |
| `physics.environment`, windy | <= 0.3 ms per substep (0 when calm or deferred) |
| kernel `thermal` after the active-set optimisation | <= 0.5 ms amortized |
| `props()` | <= 5 µs per call (per-type memo plus overlay) |
| data load | <= 20 ms once per process |
| whole engine | no regression: <= 3.6 ms per substep on seed 0, and still < 15 ms on any 900-entity house |

`tools/bench_systems.py` reports `physics.*` systems automatically. A new `tools/physics_audit.py --bench` times
settle over random action walks and reports p50 and p99.

## 12. Test plan

1. **`tests/test_physics_data.py`** (WP-1):
   * every material string in the registry maps to a class (0 unmapped);
   * every type yields finite positive mass and size;
   * bulk density is within [5, 9000] kg/m³, or the type is listed as an exemption (hollow, inflated, soft-sided)
     or as an owner gap;
   * every rated support category resolves a rating;
   * the anchor rows match section 4.3;
   * `vary` is stable across processes and stays within ±15 %.
2. **`tests/test_physics_forces.py`** (WP-1):
   * lift thresholds equal core's today;
   * helpers enable a team lift;
   * a 60 kg sofa moves (177 N) and a 100 kg dresser is refused (294 N);
   * pulling a bookcase above 0.375 m tips it;
   * felt pads give no scratch;
   * the containment cases (opening, diagonal, nesting, compressible, choking cylinder).
3. **`tests/test_physics_statics.py`** (WP-2):
   * a tumbler dropped from 0.9 m onto tile breaks; carpet or 0.3 m survives; 6 mm of padding at 0.3 m survives;
   * a shelf rated 25 kg collapses with 26 kg robot-added, holds 24 kg, and a setup-overloaded shelf holds its
     baseline;
   * staged creep collapses on time and not when unloaded first;
   * a collapsing top shelf drops its items onto the shelf below;
   * three open drawers plus a 27 kg pull tip an unanchored dresser and not an anchored one;
   * a wine glass tips at 0.35 g and a mug does not;
   * the stack rules;
   * crush with padding and wet cardboard;
   * attribution: the same `physics.bump` counts `breakages` only when the robot had placed the glass.
4. **`tests/test_physics_fluids.py` and `tests/test_physics_thermal.py`** (WP-3):
   * a full mug spills at fast carry speed and not careful;
   * on-side spill for wide and narrow mouths;
   * pour rate ratios for honey and oil;
   * transfer overflow becomes a spill;
   * a towel absorbs at most 2.5 L/kg;
   * a drip on a tray pools and overflows its rim; a drip on a towel passes through after saturation; a drip on
     wood leaves a ring at 600 s;
   * a 4 °C can sweats at 21 °C / 45 % RH and rings an unprotected wood table, not one with a coaster;
   * a 200 °C pan scorches wood and not a trivet;
   * 96 K breaks soda-lime and not borosilicate;
   * freeze burst at 0 % headspace and not at 10 %;
   * wax melts at 50 °C;
   * the `burn_seconds` table;
   * `tau_s` anchors;
   * wind: a napkin blows at 3.5 m/s, a weighted napkin does not, and a 3 kg chair slides at about 9 m/s;
   * rain soaks paper.
5. **`tests/test_physics_settle.py` and `tests/test_physics_setup.py`** (WP-4, through `HouseEnv`):
   * a refused action produces no consequences;
   * settle is idempotent (twice gives the same digest);
   * the cascade budget;
   * a pet-knocked item lands in the right substep;
   * undo is dropped after a consequence;
   * strict mode;
   * determinism across `PYTHONHASHSEED`;
   * save/load and replay roundtrip;
   * realistic mode: no hidden numbers in messages, and byte-identical messages for every member of a look-alike
     group;
   * every setup op, including `check_physics` rejections;
   * every `physics.*` event;
   * `lawbook.forced` toggles each deferred law on, and the law fires, so deferred code is tested before its flip.
6. **Landing gate** (all must pass before Phase K is done):
   * all suites, both full and per-domain isolated, also under `HOUSE_SIM_STRICT=1`;
   * `python -m house_sim --check` over all 242 specs on 5 seeds, oracle and realistic;
   * `tools/perception_audit.py`: no new leaks;
   * `tools/fuzz.py`: no crashes, deterministic;
   * the perf budget;
   * `tools/physics_audit.py`: 0 unmapped materials and a data-gap report per domain.

## 13. Implementation plan

**Ordering.** Phase K starts after kernel round 5 (currently editing realism, core, actions, engine, geometry,
perception and the generator) has landed. WP-1 lands first because it is small. WP-2 and WP-3 code against the
frozen contracts in section 7 from day one. WP-4 integrates last, but can write `settle.py` against the lawbook
contract immediately.

### Kernel work packages (disjoint file sets)

**WP-1: data, properties, forces, containment (agent K-PHYS-DATA)**

* Files:
  * new: `house_sim/data/physics/{materials,liquids,floors,supports,laws}.json`;
  * new: `house_sim/kernel/phys/__init__.py`, `props.py`, `lawbook.py`, `forces.py`, `contain.py`;
  * edit: `house_sim/kernel/schema.py` (`ROBOT_DEFAULTS`: `max_push_n`, `max_pull_n`, `max_grip_n`) and `setup.py`
    (`package_data`);
  * tests: `tests/test_physics_data.py`, `tests/test_physics_forces.py`.
* Items:
  * the tables and alias map from sections 4.3-4.6;
  * resolution and caching (4.2);
  * `vary` (4.7);
  * `Phys` (4.9);
  * the lawbook with the section 10.2 states;
  * `lift_plan`, push, friction and scratch (5.6, manipulation's formulas verbatim);
  * `contain.fits` and `choking_hazard`.

**WP-2: support, statics, consequences (agent K-PHYS-STATICS)**

* Files: new `house_sim/kernel/phys/support.py`, `statics.py`, `impact.py`; test `tests/test_physics_statics.py`.
* Items:
  * support graph, loads, baseline allowance, COM (5.1);
  * the overload, hang, handle and creep laws (5.2);
  * crush (5.3);
  * the edge, slope, stack and furniture_tip laws (5.4);
  * fall, impact, break, chip, collapse, tear, tip_over, knock, damage, landing cell and floor damage (5.5);
  * the setdown law;
  * attribution (6.4);
  * the idempotency ledger.

**WP-3: liquids, thermal, environment, processes (agent K-PHYS-FLUIDS)**

* Files:
  * new: `house_sim/kernel/phys/liquids.py`, `thermal.py`, `environment.py`, `processes.py`;
  * edit: `house_sim/kernel/physics.py` (`thermal_owner`, `tau_s` behind the `thermal_tau` law, active-set
    optimisation, `mut_quiet` for temperature writes, imports registering the new systems, public re-exports);
  * tests: `tests/test_physics_fluids.py`, `tests/test_physics_thermal.py`.
* Items:
  * section 5.7, including the open_spill, overflow and drip laws;
  * carry slosh and topple functions (5.8; WP-4 triggers them);
  * section 5.9;
  * section 5.10 with the `physics.environment` system;
  * the `physics.processes` system and its state section (6.2).

**WP-4: integration, settle, events, core, setup, perception, tools, docs (agent K-PHYS-INTEGRATION; main session)**

* Files:
  * edit: `house_sim/kernel/world.py` (touch log, `mut_quiet`, `phys_scratch`), `engine.py` (settle calls, undo
    drop, `physics_version` in replays), `actions.py` (ctx helpers);
  * new: `house_sim/kernel/phys/settle.py`, `events.py`, `observe.py`;
  * edit: `house_sim/systems/core.py` (`pick_up` uses `lift_plan`; `place` gets set-down, `contain.fits`, and the
    collapsed and broken support refusals; `subtree_mass` delegates);
  * edit: `house_sim/tasks/setup.py` (ops), `house_sim/tasks/verify.py` (the `check_physics` lint),
    `house_sim/data/types/core.json` (field_tiers);
  * new: `tools/physics_audit.py`;
  * tests: `tests/test_physics_settle.py`, `tests/test_physics_setup.py`;
  * docs: `docs/KERNEL_API.md`, `docs/PHYSICS.md` (new), `docs/CONVENTIONS.md`, `docs/AUTHORING_GUIDE.md`,
    `docs/PERCEPTION.md`, `docs/KNOWN_LIMITS.md`.
* Items:
  * section 6.1;
  * the carry trigger;
  * sections 7.2 and 7.4-7.7 and section 8;
  * the landing gate.
* Main-session follow-up outside the kernel: `house_sim/eval/viewer.py` rendering (10.4).

### Per-domain follow-ups (Phase D; each domain also runs the adoption protocol in 10.3)

The data counts are types resolving to the default 0.4 kg mass and default 0.1 m size. Fix each with real values,
or explain it in the audit.

| domain | adopt (replace its own copy) | implement or register | data fixes |
|---|---|---|---|
| manipulation | crush, stack, edge, slope, carry, wind, hang, handle, contact_heat, wet_contact, open_spill, furniture_tip (`tip_check`); `common.py` break, spill, impact, drop, padding, slosh, topple, scratch, friction and `max_load` become kernel wrappers; its perturbation events delegate to `physics.*`; `manipulation.sounds` drops kinds the kernel now emits | `phys.items_above` (packing order); `grip_outcome` reads kernel mu and crush; fix the `docs/domains/manipulation.md` table; tests updated for glass 0.10 -> 0.5 m and crystal 0.05 -> 0.25 m | 4 mass |
| mechanisms | furniture_tip (`closures._tip_over`), hang (curtain rods), `fall_item` -> kernel `fall` (fixes the spill stat with no spill), `_wind` and the wind_gust/rain events -> kernel | `phys.tip_loads` for open drawers; make `anchored` an explicit per-instance furnisher draw (no blanket True); `SOFT_MATERIALS` -> kernel softness | 10 mass |
| electrical | - | `phys.wetted` (liquid ingress: short or trip and `electrical_hazard` when powered, `water_damage` until dry otherwise); `phys.broke` (dead devices, cracked screens); bulb cool-down using kernel tau and `burn_seconds`; check the electronics drop rows (TV 0.3, laptop 0.75, phone 1.0 m); `SOFT_MATERIALS` -> kernel | 13 mass, 7 size |
| plumbing_liquids | carry (`leaks.slosh_spill_l`), overflow and spill (`spill_near`), handle (buckets), absorb (`absorb_capacity`; `wring` stays), phase (`ice.py` declares `thermal_owner` or uses kernel phase) | `do_pour` uses `transfer`, `pour_rate_lps` and the thermal shock check; densities from `liquids.json`; drop the `carry_slosh_applied` flag | 4 mass |
| climate | furniture_tip (fan `_topple` via `com_frac`), carry (`_other_slosh_model`), `_spill_under` -> spill | `COMBUSTIBLE_MATERIALS` -> kernel `combustible` column; climate keeps owning humidity, which kernel condensation reads | 4 mass |
| appliances_cooking | contact_heat (`surface_heat_limit_c`), thermal_shock (`thermal_shock_limit`; soda-lime 90 -> 45 K, ceramic 120 -> 80, stoneware 120 -> 100: update tests), overload (`build.py` shelf_load copy), coffee `_spill` | `thermal_owner: "appliances_cooking"` where `fixed_temperature` means ownership; freezer heat load via `heat_content_j`; re-verify cooling times for the `thermal_tau` flip; hot handover burns via `burn_seconds` | 9 mass |
| food | crush (packaging rows: bread, chips, eggs), `model.spill` -> spill, shelf_load copy -> kernel | keep food thermal and phase (`thermal_owner: "food"`); `phys.broke` (raw-egg spill); `phys.props` (ripeness -> softness); `phys.fell` (bruise, not break); `LIQUID_DENSITY` -> `liquids.json`; `freeze_burst_check` for food jars; produce shape sphere -> rollable | 12 mass, 6 size |
| cleaning | `state.break_item` -> kernel break; `topple_risk` -> `knock` (dusting crowded surfaces about 0.15 g); handle law for liners | register `phys.spill_sink` (`make_spill`; physical columns from `liquids.json`, residues stay); stays drying owner; re-key `MATERIALS` chemistry by `physics.material_class`; drop its `thermal_shock_c` column | 11 mass |
| textiles_laundry | melt (iron vs synthetic `melt_c`), `textiles_laundry.transport` -> `physics.transport` | `phys.props` (wet mass = dry + wetness x absorb); absorb capacities from the kernel table; ratings for drying racks, clotheslines and hangers (hang law); hanger slip via kernel friction; mildew timers stay | 9 mass |
| outdoor_plants | wind (`_object_wind`, `_cover_wind`, `umbrella_tip_check` become the kernel environment formulas with its calibration rows), rain on generic objects, `add_spill` -> spill, slope (wheeled bins and carts on `slope_deg`) | keeps weather, plants and soil; `phys.exposed` (porch roof, cover, umbrella); pot drainage via `drip_start` (saucer pooling); `event:wind`/`start_rain` feed the kernel when the laws are active | 23 mass |
| pets | carry (`water._slosh`) | knocking via `knock`/`nudge` (cats push items off edges; tail sweep 0.3 g at <= 0.6 m); `phys.fell`/`broke` -> startle; scenarios' shelf_load copy -> kernel; pet-caused consequences are exogenous unless the robot arranged the item | 4 mass |
| people | - | `lift_kg`/`push_n` per person and a helper path for team lifts (with manipulation co-carry jobs via `lift_plan(helpers=...)`); `phys.fell`/`tipped` injuries for people in fall cells; burns via `burn_seconds` for hot handovers and spills; the children system can trigger `physics.pull` | 7 mass |
| safety | furniture_tip (`events.tip_over` -> kernel: orientation `on_side`, no "toppled X" relabel), `event:safety_tip_over` -> `physics.pull` | `phys.broke` -> shards entity for brittle breaks on floors; `phys.fell`/`tipped` -> injury; `choking_hazard()` for childproofing; `phys.contact_heat` ignition thresholds | 6 mass |
| navigation_robot | carry (`traverse._slosh`), `drop_item`/`break_entity`/`spawn_spill` -> kernel; remove the `"stack" in ACTIONS` check | `phys.carry_motion` (stairs x1.8, threshold x1.15, slopes); robot falls drop held items via `fall`; keep traction and stairs limits; standing-load ratings for step stools and ladders from `supports.json` | 5 mass |
| admin_inventory | overload (`_load_check`, `_collapse`; `SHELF_RATING`/`SURFACE_RATING` -> `supports.json`; keep `shelf_headroom` as a wrapper), melt (`MELT_MATERIALS`, `max_safe_temp_c`), packages `_break_fragile` -> `physics.transport`, restock `_spill`, curb `_wind` | handle law for luggage handles | 5 mass |
| tools_vehicles | - | pads, sliders, felt and casters feed kernel foot materials; wall anchors and hooks use kernel hang ratings (`anchor: stud \| drywall_anchor \| adhesive_*`); dolly or hand truck as a `lift_plan` alternative (rolling c_rr); trunk loads via `physics.brake` (0.4 g); ladder ratings (ANSI I/II/III); an anchor kit sets `anchored` | 7 mass |

## 14. Interfaces with the other tier-1 layers

**Embodiment** (robot body) owns the robot's capability numbers: payload per hand and total, push, pull, grip, reach
and arm count. It also owns the robot's posture and its own falls.

* K-PHYS reads capabilities only through `forces.robot_caps(w)`. Today that reads `robot.*` with `ROBOT_DEFAULTS`.
  If Embodiment introduces per-arm or torque-based limits, it replaces `robot_caps` and nothing else changes.
* K-PHYS exposes `held_envelope(w) -> {"top_m", "front_m", "width_m", "mass_kg", "com_m"}`. It covers held items and
  everything on or in them. Embodiment uses it for balance and stairs; core `held_width` can use it for doorways.

**Camera** (perception) owns rendering and the knowledge model. K-PHYS guarantees three things:

* every consequence state appears in the glance or inspect tier lists of section 7.6;
* no hidden number reaches messages or observers;
* `support.resting_size` and `support_of` are available, so occlusion or visibility rules ("the load blocks the
  view", things behind or under) use the same geometry.

The `physics.sounds` observer follows the camera layer's channel rules.

## 15. Authoring recipes (how the remaining entries use this layer)

| need (entries, from A's tag analysis) | mechanism | example |
|---|---|---|
| Payload, team lift (45) | `lift_plan` refusal and helpers | "too heavy: get help or use a dolly": wrong plan lifts alone, refused; reference asks an adult, then co-carries |
| Load capacity (60) | overload, hang, handle laws; `sagging`; `load_rating_text` | bag of cans: overloaded paper bag tears when lifted (handle law), contents fall |
| Stacking and crush (100) | stack and crush laws; `wobbly`; `physics.bump` after completion | plates on a shelf: stack taller than the gap topples; heavy pot on eggs crushes them |
| Toppling and tipping (100) | furniture_tip, `physics.pull` 27 kg, anchored | heavy books low (T-3f4825241e6a4c-01, A-1872db807bd2ca-01): heavy-top wrong plan tips under the pull |
| Falling and impact (140) | fall and impact; `physics.bump`/`drop`; padding | glass near the table edge: `edge` op, bump at the end; the reference moves it in |
| Spills (120) | carry and open_spill laws; spill sink | full mug carried fast spills; careful speed does not |
| Pour and overflow (95) | `transfer`, `pour_rate_lps`, overflow | fill to a line: overflow becomes a spill entity |
| Wetness (280) | wet, absorb, drip, wet_contact, condensation | waterproof underlay (A-3286b5878fe8e9-01): dripping pot on a tray survives 4 h; towel saturates and passes water; table rings |
| Forces and scratching (100) | push, scratch, sliders | vacuum behind furniture (T-4f47dcd3257c52-01): dragging on hardwood scratches without sliders; pulling at the top tips it |
| Containment (140) | `contain.fits`, nesting, choking cylinder | small toys go in the bin: items that fit the choking cylinder must be removed from the toddler area |
| Thermal (200) | contact heat, shock, phase, tau, burns | hot soup into a cold glass jar cracks it, borosilicate does not (T-021ef6825c3757-01); freeze with headspace (T-bcc54f47cbea50-01); coaster for a cold can |
| Wind and rain (110) | `physics.wind_gust`/`rain`, ballast, secured | something heavy enough for papers (A-a67431128da2e1-01): faux stone vs real stone resolved by heft; gust at the end |
| Materials and affordances (140) | `Phys` (absorb, waterproof, softness, mass), `feel`, `looks` | something soft to protect it: padding factor; something to prop it: rating and fit |
| Rolling (30) | slope law, rolling process, `physics.brake` | oranges on a tilted tray roll off; a cart on the driveway runs away unless chocked |
| Grip window (50) | manipulation `grip_outcome` with kernel mu and crush | soft fruit bruises under a firm grip |
| Carrying on stairs (55) | carry law with the navigation multiplier | a sloshing bucket on stairs spills; careful mode does not |

Counts are A's keyword estimates (they include false positives). Every recipe uses ordinary path predicates on the
states in section 7.6. Wrong plans fail through real consequences, and idle fails because the goal state is never
produced.

## 16. Open questions

1. Should the ±15 % per-instance variation be on by default? It is honest and fights threshold-memorising, but
   authors must keep reference plans well away from thresholds. The proposal is on, with `phys_exact` available.
2. `thermal_tau` changes cooling times by 5-25x. Flip it in Phase D, or keep it deferred until a dedicated thermal
   round re-verifies every cooking and cooling spec?
3. Default `anchored` for generated furniture. This spec asks mechanisms for an explicit per-instance draw; the
   probability needs a cited survey figure.
4. Harm attribution through the top-level support subtree may over-attribute. Example: the robot put a book on a
   bookcase, and a child's pull later tips the bookcase and breaks a vase that was always there. Accept this, or
   narrow it to the item's own chain plus loads the robot added beyond baseline?
5. Should `wobbly` and `sagging` be written at episode start for generated or staged scenes, at O(N) once at reset,
   or only after the first settle that touches them? The proposal is staged entities at reset, everything else on
   touch.
6. Overhang placement through core `place`: allow `spos` with an overhang up to 30 % (mirrors the `edge` op), or
   keep it setup-only?
7. Design B was never received. If it arrives, its delta should be judged against sections 0 and 13 before Phase K
   starts.

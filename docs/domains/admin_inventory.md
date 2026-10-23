# admin_inventory: documents, mail, packages, stock, storage

Owner of: papers and documents, printers/scanners/copiers (paper, ink, jams), shredder, staplers, punches, clips,
binders, folders and filing, labels (label maker), stationery, mail, envelopes, stamps, mailboxes, packages and
packing material, box cutter, return labels, lists (shopping, packing, checklists), inventory records, consumable
stock (toilet paper, paper towels, tissues, napkins, foil, wrap, storage bags, trash-bag boxes) and point-of-use
holders/dispensers, luggage, bags and their contents, donation bags, and the storage rules that decide whether
stored goods are safe (load ratings, child reach, chemical segregation, clearances, heat, damp).

Code: `house_sim/systems/admin_inventory/` (package, ~20 modules). Types: `house_sim/data/types/admin_inventory.json`
(132 types; 66 with `perception` blocks, 9 with camera `view` blocks, 20 look-alike groups, file-level `field_tiers`).
Tests: `tests/test_admin_inventory.py` (153 tests with every domain loaded, also with `HOUSE_SIM_STRICT=1` and with
`HOUSE_SIM_DOMAINS=core,admin_inventory`; `RealisticPerceptionTests` and `RedTeamRoundTests` cover realistic mode,
`Tier1AdoptionTests` the kernel v3.0-v3.1 adoption, section 17, `StabiliseTests` the phase-1 fixes after the
independent review, section 18). Examples:
`tasks/authored/admin_inventory__examples.json` (12 specs, all pass `--check` on seeds 0, 1, 2, 41, 97 with every domain loaded;
11 on generation-2 houses) and `tasks/authored/admin_inventory__perception.json` (3 realistic-perception specs with the
geometric camera, generation 2; sections 15 and 17).

Fidelity: S for symbolic bookkeeping (lists, records, mail classes), G for geometry-backed rules (fit, shelf heights,
reach, clearances, curb spacing, load ratings), P-approx for the lumped physical models (liquid/granular transfer
with spill and overflow, paper feed and jams, cold packs, wetting/drying/mildew, heat damage, shredder duty cycle).

Dev loop (all domains loaded; the tests and examples must hold with every other domain present):
`python -m unittest tests/test_admin_inventory.py` (also with `HOUSE_SIM_STRICT=1`),
`python -m house_sim --check tasks/authored/admin_inventory__examples.json --workers 2` and
`python tools/bench_systems.py 40 0 1 2 3 --domain admin_inventory` (section 12), and
`python tools/layout_audit.py 30 --domain admin_inventory --verbose` (must report 0 problems; section 14),
`python -m house_sim --check tasks/authored/admin_inventory__perception.json --workers 2` and
`python tools/perception_audit.py 30 80 --verbose` (no line attributed to this domain; sections 15-16).

---------------------------------------------------------------------------------------------------------------------

## 1. Object types (selected fields; realistic magnitudes)

| Group | Types | Key fields |
|---|---|---|
| Printers | `printer_base`, `multifunction_printer` (inkjet, 6.8 kg, 12/8 ppm), `laser_printer` (11.5 kg, 30 ppm) | `status` (ready, ready_low_ink, off, cover_open, paper_jam, no_cartridge, ink_empty, cartridge_error, tray_open, out_of_paper, paper_size_mismatch, feeder_jam), `display` text, `queue`, `files` {name: {title, pages, owner, private, version, doc_key}}, `scan_destinations`, `slots` ink_black / ink_color / paper_path (access `cover`), `cover_open` |
| Printer parts (relation `part`) | `printer_paper_tray` (max fill 100 sheets inkjet / 250 laser; settings `paper_size` letter/a4/legal, `guides` snug/loose; derived `sheets_loaded`, `sizes_loaded`), `printer_output_tray`, `scanner_glass` (surface), `document_feeder` (35 sheets) | find them with `admin_inventory.office` bindings |
| Supplies | `ink_cartridge`, `ink_cartridge_color`, `toner_cartridge` (`remaining`, `page_yield`, `tape_on`, `clip_on`, `grip_zone` edges), `jammed_paper`, `paper_ream` (500 sheets, 2.27 kg, sealed wrapper), `paper_ream_a4`, `paper_stack` (`sheets_count`, `paper_size`) | |
| Documents | `document` and `printout`, `receipt` (thermal, fades > 60 C), `check`, `passport`, `certificate` (no_fold), `manual_booklet` (`model_number`), `warranty_card`, `packing_slip` (`lines`), `return_label` (`rma`), `sticky_note` | `sheets` = ordered [doc_key, page] pairs (the physical stack); `doc_key`, `title`, `doc_kind`, `date`, `owner`, `fastener` (staple, paper_clip, binder_clip, ring_binder) + `fastener_ok`, `punch_holes` (us_3, iso_2) + `holes_ragged`, `fold_style` (z_fold, letter_fold, half_fold) + `address_out`, `creased`, `torn`, `face` (up/down), `in_order`, `mixed`, `parts` (metadata of merged documents), `shredded`, `sensitive`, `pii`, `original`, `do_not_punch`, `action_required`, `signed`; hidden `_doc_important`, `_file_as`, `_designated` |
| Shredding | `paper_shredder` (8 sheets/pass, 340-sheet bin, 300 s duty cycle, `accepts_staples`, `accepts_cards`, settings `mode` auto/reverse/off, `jammed`, `overheated`), `shred_pile` (shredded documents become its children) | |
| Fastening | `stapler` (20 sheets, slot `magazine` for `staple_strip` of 210), `heavy_duty_stapler` (60), `staple_box` (24 strips), `staple_remover`, `loose_staple` (sharp, choking), `hole_punch` (us_3, 12 sheets, chad tray), `hole_punch_iso`, `paper_chad`, `paper_clip` (10 sheets), `binder_clip` (60), `binder_clip_large` (120), `ring_binder` (`ring_pattern`, 250 sheets), `ring_binder_iso` | |
| Filing | `file_folder` (100 sheets, `order` front-to-back), `hanging_folder`, `accordion_file`, `filing_cabinet` (2 drawers = shelf 0/1), `inbox_tray` (`designated_for`, `owner`), `mail_organizer` | the core furnisher's office "filing cabinet" (a core `cabinet`) is used too |
| Mail | `envelope` (#10, 5.5 g; open flap; closing seals), `flat_envelope` (9x12), `window_envelope`, `square_envelope` (non-machinable), `mail_piece` (sealed incoming), `periodical` (magazine/catalog, `poly_wrapped`), `stamp_sheet` (20 x 78 c), `letter_opener` (2 mm, dull), `postal_scale` (5 kg x 1 g, `display`), `curbside_mailbox` (flag setting), `apartment_mailbox` (keyed), `outgoing_mail_slot` | printed: `recipient`, `address`, `sender`, `postage_class`, `markings`, `to_text`, `from_text`, `postage_cents`, `stamp_position`; hidden: `_addressee`, `_mail_class`, `_doc_important`, `_pii` |
| Stationery | `pen`, `click_pen`, `fountain_pen` (value 120), `marker_pen` (permanent, dries in 6 h uncapped), `washable_marker`, `pencil`, `pencil_sharpener`, `pen_cup`, `notebook`, `label_maker` (60 labels, battery), `box_cutter` (settings `blade` retracted/extended, `depth_mm` 3-25; extended = `hazards` ["sharp"] with `safe_grips` ["handle"], derived at `episode_start` and on `setting_changed`; the kernel remembers `held_grip`, and extending a held cutter sets it to the handle) | `settings.cap` on/off; `uncapped_s`, `dried_out` |
| Packages | `package_box` (`crush_kg` 15: stacking load before it crushes, `tape_sealed`, `recipient`, `address`, `order_id`, `markings`, `this_side_up`, `perishable`, `cold_pack_s`, `gift_note`, `outgoing`, `content_clearance_mm` 30; hidden `_addressee`, `gift_for`), `poly_mailer` (2 mm clearance), `packing_fill` (kraft paper, recycling), `air_pillows`, `bubble_wrap_fill` (film), `packing_peanuts` (EPS, trash, scatter), `foam_insert`, `desiccant_packet` (ingestion hazard), `hardware_bag` (`keep_with_item`), `protective_wrap` | fill is openable: `open` it (unfold) to see hidden parts |
| Consumables | `toilet_paper_roll` (250 sheets, `remaining`), `kitchen_paper_roll` (part/sku `paper_towel_roll`), `multipack` / `toilet_paper_pack` / `paper_towel_pack` (sealed, transparent, real unit entities inside), `tissue_box` (160, opening pulls a tissue up), `counted_stock` / `napkin_pack` (250) / `foil_box` / `plastic_wrap_box` / `storage_bag_box` / `trash_bag_box` (40 x 13 gal, unit `drawstring_liner`), `napkin_stack`, `bar_soap` (113 g, wrapped), `soap_dish`, `soap_dispenser` (350 ml, 22 mm neck, `product_tag`), `foaming_dispenser`, `refill_bottle` (1 L), `refill_funnel` | `sku`, `units_count`, `pack_count`, `unit_type`, `liquid`, `liquid_l`, `capacity_l`, `fill_line_l`, `refill_threshold` |
| Holders | `roll_holder`, `toilet_paper_holder`, `paper_towel_holder` (slot `roll`, `roll_orientation` over/under), `tissue_box_cover` (slot `box`), `napkin_holder` (80 dispensable, `overstuffed`, `dispensable`), `spare_roll_basket` | |
| Organizers | `review_basket` (set-aside), `sorting_bin` (`label_text`), `key_bowl`, `remote_caddy`, `return_tote`, `donation_bag` (15 kg, unlabeled black bag looks like trash), `donation_box` (18 kg) | |
| Luggage | `luggage` base (`order`, `load_kg`, `fill_l`, `zipped`), `suitcase` (90 L, 4.3 kg), `carry_on_bag` (40 L), `liquids_bag` (1 L clear), `backpack`, `work_bag`, `reusable_shopping_bag`, `insulated_shopping_bag`, `wallet` (`private_contents`), `id_badge`, `rain_umbrella`, `luggage_scale` (50 kg x 50 g), `luggage_tag` | |

Props-based mechanics (work on any domain's entities): `wheeled_cart` + `waste_stream` (carts), `bulk_store` + `layers` [{product, l, lot}] (jars, bags of rice/flour/kibble), `liquid_container` (dispensers, bottles), `chem_class` (chlorine_bleach, ammonia, acid, oxidizer_pool, fuel, fertilizer, base_caustic, alcohol_solvent, ...), `ignition_source` + `clearance_m`, `keep_clear_m`, `max_safe_temp_c`, `max_load_kg`, `shelf_load_kg`, `under_clearance_m`, `grip_zone`, `terminals_exposed`/`terminals_taped`, `medication`, `detergent_pod`, `coin_cell`, `alcohol`, `choking_hazard`, `ingestion_hazard`, `private_contents`, `sensitive`, `pii`, `shred_required`, `keep`, `sentimental`, `value`, `_important`, `_true_material`, `_true_color`, `_identity`, `_damage`, `_charge` + `charge_hidden`, `_infested`, `contains_gluten`, `gluten_free`, `dries_in_air`, `dries_when_open`, `dry_out_s`, `label_facing`, `depth_rank`, `order`.

## 2. Actions

All refusals roll back; categories in brackets. Safety/privacy/authority refusals fail a spec by default (strict_blocks).

| Verb | Params | Preconditions (refusal) | Effects | Duration |
|---|---|---|---|---|
| `staple` | target doc, tool?, position? (top_left, top_right, left_edge, center) | document on a surface or held; unfastened; held stapler [capability] with staples [resource] | `fastener` staple; over capacity bends (`fastener_ok` false); center pierces text; mixed/misordered stacks stay so | 3 s, noise 0.08 |
| `unstaple` | target, tool? | stapled | removes staple; without a remover the corner tears; spawns a sharp `loose_staple` where you worked (on top of the papers when a busy desk has no bare spot; the floor only if even that fails) | 4 s |
| `punch` | target, tool? (punch within reach) | not punched, not in a binder | `punch_holes` = pattern; over capacity `holes_ragged`; originals/certificates `damaged` + stat damage; chad fills tray, overflow spills | 5 s |
| `bind` | target, destination? binder, method ring_binder/paper_clip/binder_clip, tool? clip | binder open, `punch_holes` match `ring_pattern` [precondition], rings capacity | doc into binder (`order`), or clip attached; clip over capacity slips when lifted (pages scatter out of order) | 5 s |
| `collate` | target stack, destination stack, position? | both loose | one stack (target destroyed, recorded in facts.admin.merged by sheet keys) | 2 s |
| `sort_pages` | target, by page/document | loose, readable, numbered for `page` [perception] | orders pages, or splits a mixed stack into one document per key | 2 + 1.5 s/sheet (it reads every sheet) |
| `fold_paper` | target, style z_fold/letter_fold/half_fold/unfold | | size shrinks to fit #10 envelopes; z_fold sets `address_out`; `no_fold` originals damaged | 4 s |
| `file` | target held, destination folder/tray/binder/ordered shelf, position by_date/front/back/by_title/by_author/by_label/by_series | container reachable and open | inserted into `order` at the sorted position (household `filing_order` oldest_first or newest_first); overfull folder creases | 5 s |
| `shred` | target held, destination? shredder | requester adult or approval `shred:<id>` [authority]; no child within 1 m of the feed [safety]; powered, on, mode auto, not jammed/overheated, bin not full [resource] | `shredded`, sheets gone, doc moves into the `shred_pile`; over capacity, binder clips, staples (micro-cut) or cards jam (doc stuck, `damaged`) | 2 s + 3 s per started 5 sheets (by bulk: never a count of sheets hidden in an envelope), noise 0.7, irreversible |
| `print` | target printer, file, copies, duplex, color, pages | file exists [argument]; another person's private file needs `print:<file>` [privacy; realistic: the print server's error names no owner]; network or at the panel | queues a job; the system prints at ppm into the output tray; pauses on out_of_paper / jams / size mismatch / empty ink | 3 s + print time |
| `scan` | target printer, destination, name?, source glass/feeder | at the panel; known destination | `household.scans[name]` pages [key, page, ok/blank/upside_down]; glass = one face-down sheet; feeder = face-up loose sheets (fastened/torn/wet originals jam and tear); external email of sensitive docs = privacy_violations | 5 + 8 s/page glass, 3 s/page feeder |
| `copy` | target, copies, duplex, collate, color, source | as scan | captured pages printed through the queue (`is_copy`) | 4 s + |
| `open_mail` | target envelope, tool? | addressed to the requester (state.requester), household mail for an adult requester, or approval `open_mail:<id>` [privacy; realistic: refused only once the robot read the envelope, else opened and counted as `privacy_violations`] | letter opener: clean slit; blade deeper than `contents_clearance_mm` cuts contents; by hand tears (snug contents torn) | 4 s |
| `address_envelope` | target, to, sender?, tool? | held pen/marker, readable | `to_text`, `from_text`, `addressed` | 20 s |
| `affix_postage` | target, stamps, source? booklet, position? | held stamps, enough left [resource] | `postage_cents`, `stamps`, `stamp_position`; booklet decremented | 3 + 2 s/stamp |
| `verify_label` | target package/mail | readable; label not underneath / soaked [perception] | facts.admin.last_verify: verdict household / resident:<pid> / wrong_address / unknown_name / former_occupant | 4 s |
| `open_package` | target, tool? | addressed to requester/household (adult) or approval `open_package:<id>`; never wrong address/former occupant; gift notes for others stay sealed [privacy; realistic: refused, generically, only once the label was read (`verify_label`/`read`), else cut open and counted as `privacy_violations`]; blade extended | tape cut; `depth_mm` > `content_clearance_mm` cuts the nearest contents (fragile break, stat damage); no tool tears flaps (`reusable_box` false) | 6 s (15 s by hand) |
| `remove_packing` | target package (open) or product, destination?, search? | fill present / packaging present | fill moved to destination (bin stream checked [safety]; floor: peanuts scatter, slip hazard); search=true keeps hidden parts with the item; on products removes tape/clip/film/wrapper into a free hand | 4 + 3 s/fill (+10 s search) |
| `label` | target, text, face, kind removable/permanent/freezer/tab, replace, tool?, label_doc?, position? | label maker / pen / marker [capability]; permanent needs a permanent marker (realistic: a marker the robot has not told apart from its washable look-alike writes the label in washable ink, no refusal) | `label_text`, `label_key`, `label_face`, `label_faces`, `labels`; old label without replace = `label_conflict`; permanent on finished wood/rental = `finish_damaged` + damage; wet/frozen surface: falls off after 1 h; printed shipping label: `return_label_ok` iff RMA matches, flat face, old barcode covered | 10 s (+0.2 s/char) |
| `face_label` | target, side? | reachable, not held | `label_facing` | 2 s |
| `count` | target room/entity, what, by, unit units/packages, include_in_use | robot in that room | visible matches only (closed bins/occluded items unseen); multipack units through clear wrap or printed `pack_count`; reports partial (+levels, opaque = unknown), empty, in_use (installed parts), untested cells; realistic perception: the close look (look-alikes told apart, levels to 5 %, an open box's units) only for what a close look reaches from where the robot stands (section 18), the rest as the robot already knows it (`unidentified`, `unread`); facts.admin.last_count | 1.5 + 0.4 s/item |
| `record_inventory` | target record, item/count/location/level/flags/unit/note/expires or entries, replace | valid numbers/flags [argument] | `household.inventory[record][item_key]` | 1 + 0.5 s/entry |
| `write_list`, `add_to_list`, `cross_off`, `remove_from_list` | target list name, item, qty, unit, brand, size, section, note, merge, bought, substitution, reason | app lists need network [resource]; paper lists need reach + pen [capability] | `household.lists[name].items[key]` {name, qty, done, bought_qty, partial, removed, ...}; merge sums qty; nothing is deleted (history kept) | 2-3 s |
| `place_order` | target item, qty, vendor? | approval `purchase:<item>` [authority], no `household.no_purchases` | simulated order in `household.orders` | 10 s |
| `take_unit` | target counted stock / paper stack, count, hand? | open, enough units [resource], free hand | spawns the unit (sheets, napkins, liner, strip) into a hand; decrements `units_count` | 2 s |
| `mount_roll` | target roll held, destination holder, orientation over/under | empty slot (empty core must come off first; usable roll refused) | core `install` + `roll_orientation`; wet roll `ruined` | 8 s |
| `refill` | target (open dispenser/jar), source (held, open), amount_l?, funnel? (held or within reach) | liquid-to-liquid or bulk-to-bulk | transfer with spill (narrow neck without funnel 12% liquid / 6% granular), overflow over capacity (spill entity, stat spills, sticky soil), `product_mismatch`, `liquid` "mixed", foaming pump `clogged`, gluten `allergen_contaminated`, pests `_infested` spread, bleach+ammonia/acid = gas_exposure; bulk layers stack (pouring inverts order: FIFO by pouring old out and back) | 3 s + volume (to 50 ml) / flow (soap 0.04 L/s) |
| `rotate_stock` | target shelf container, shelf, what? | open | `depth_rank` 0 = oldest date in front | 5 + 3 s/item |
| `wheel_cart` | target cart | `wheeled_cart`, both hands free [capability], <= 120 kg [safety] | cart held in both hands (`towed`); core `move`/`place` carry it; stairs refused [safety] | 3 s |
| `weigh_luggage` | target bag, scale?, lift | held luggage scale; lift needs <= 32 kg | reading = bag + contents (resting on the floor reads 35%); facts.admin.last_weigh | 8 s |
| `tag_luggage` | target bag, tag held, text | pen unless the tag is pre-filled | tag attached, `tagged`, `tag_text` | 20 s |

Hooks on core actions: `open` (sealed envelopes, taped packages, others' `private_contents`, neighbor's mailbox [privacy]); `close`
(seals envelopes; traps moisture in folded umbrellas/zipped bags); `read` (private/confidential documents [privacy]);
`hand_over` (sensitive documents only to owner/authorized adult [privacy]) -- in realistic mode these privacy guards follow
section 16's rule (refuse, generically, only what the robot perceived; otherwise the act goes ahead as a privacy violation); `pick_up` (folder `order` upkeep, slipping
clips, hygiene `grip_zone`, overloaded bag/box handles or bottoms give way (the kernel's handle law, section 17), napkin
holder counts, feeder jam clears,
leaks from open bottles in carried bags, scale display); `place` (folder `order`, paper tray reset, glass/feeder face,
postal scale display, window-envelope address, napkin holder fill, soggy soap, wet floors ruin paper, crushed boxes
(the kernel's crush law), this-side-up (contents tumble through the kernel's impact model), shelf/surface overload (the
kernel's overload law: only load beyond what the slot carried at episode start + 0.5 kg is charged to the robot), FIFO
`depth_rank`, under-bed clearance, hot items mark containers at the kernel's heat limit of their material, papers set
down upside down lie face down, water rings on wood floors, battery terminal shorts, bag `load_kg`/`fill_l`); `install` (holder default
orientation); `move` (no carts on stairs); `setting_changed` (shredder reverse clears a jam; scale units).

## 3. Systems and events

Two systems (kernel v2.1: indexed lookups via `w.static_index`, never a whole-house scan per tick):

* `admin_inventory.time` (order 85, every 30 s substep, event-sensitive): printers (status, queue at the rated ppm,
  paper feed, jams, ink use), shredder cool-down (linear), mail route (default 13:30 Mon-Sat: collects ready outgoing
  pieces from flagged boxes/outgoing slots into `household.sent_mail`, leaves `carrier_note` "postage due"/"no
  address"/"undeliverable"; delivers `household.mail_incoming`), curb collection (`household.collection` {weekday,
  time, streams {trash: weekly, recycling: odd_weeks/even_weeks}, skip_dates}; wall-clock times) and wind litter from
  open carts (probability 1 - exp(-dt/1800 s) per open cart out in the weather while the local gust at the cart, the
  kernel's wind-at-object model, is 7.2 m/s or more). Exact idle probe `timekeeping.time_idle` (section 17).
* `admin_inventory.exposure` (order 86, `period` 120 s; dt = the accumulated time, every update linear in dt or a
  dt-correct probability): parcels/mail outdoors (rain on an exposed parcel is the kernel's rain model; wind-driven
  rain under a porch roof wets slowly; a soaked or wet parcel's label smudges and `soaked` boxes damage
  paper/fabric/electronics contents; unattended street-facing parcels can be stolen after `household.theft_grace_s`
  at `theft_risk_per_h`, probability 1 - exp(-rate dt)), cold packs (`cold_pack_s` keeps a box at 3 C, burns faster
  when warm), heat damage (`max_safe_temp_c`), damp basement/garage floors soak cardboard (mold), trapped moisture in
  folded umbrellas/zipped bags (mildew, rusting ribs), uncapped pens/markers and open paint/glue dry out, labels on wet
  surfaces peel (registry `_admin_inventory.label_peel` {eid: due_s}, a hidden top-level section filled by `label`
  and at `episode_start`), opt-in resident consumption (`household.consumption.enabled`), spent rolls become
  recyclable cores. Effects can lag by up to 120 s.

Drying ownership (conventions 5): this domain evolves only `_trapped_water` (moisture folded into umbrellas/bags) and
the wetting of parcels/mail left out in the rain; generic surface evaporation of `wetness` belongs to the cleaning
domain's `cleaning.drying` (fallback drying here only when it is not loaded). Liquid spills from refills are the
kernel's spills (`physics.spill`: a film on the surface below, the rest a floor puddle; the cleaning domain's `spill`
entity through its `phys.spill_sink` when it is loaded), so absorb/mop and evaporation apply.

Scenario events (`{"op": "event", "at_s": .., "effect": ..., "args": {...}}`): `admin_mail_delivery` {box, pieces},
`admin_carrier_visit`, `admin_package_delivery` {room, package {eid, type, props, contents, fill [{hidden}]}, ring},
`admin_doorbell`, `admin_parcel_pickup` {spot}, `admin_collection`, `admin_relocate` {item, to, relation?, by}
(resident moves something offscreen; logged in `logs.moves`), `admin_consume` {items | item + amount_l/units/remaining/tissues},
`admin_travel_check` {mode flight/car, carry_on [], checked []} (confiscates > 100 ml liquids, flags spare lithium in
checked bags as safety_violations, leaks unsealed bottles; the flags count against the robot only for what it packed or
carried, section 18).

## 4. Observation, inspect, answers

* Observation `obs["admin"]`: `last_count`, `last_verify`, `last_weigh`, `last_scan`, `last_print`, digital `lists`
  and `inventory` records (network). Sounds: `printer_beeping` (error status in this room), `doorbell` (within 60 s).
  Plans can `{"save": "w", "from": "admin.last_weigh.kg"}`.
* `inspect`: printers (status, display, tray paper, cartridge levels, queue, files, scan destinations); documents
  (pages, in_order, fastener, punch_holes, creases, fold, face, kind, date, action_required, signed; title unless private);
  mail/packages (printed label fields); stock (one level statement per message: a visible liquid level as `fill_level`
  (the plumbing inspector's key; whichever runs first reports it), what a close look or holding tells about an opaque
  one, or "unknown", as `level`; a level the record keeps itself (`remaining`, `level`, `fill_level`) only under its own
  name; units, sku, expiration, opened_on, pao_months, layers). `where="closely"` reveals `_true_material` (tap test),
  pests (through glass or into an open jar), `_damage` (seam vs body dent; reported as `damage_found`, never as the shared
  `damage` dict; a `_damage` dict whose `visible` is not a close look -- "use", "test" -- is not reported, and its
  internal keys are dropped), `_identity` (only if labeled; never guess unlabeled chemicals), battery `_charge` (needs a
  held battery tester), `_true_color` (only in bright, non-warm light >= 0.6).
* Opaque bottle levels: set `opaque: true` (or put `liquid_l` in `secret_fields`; a closed bottle without see-through
  walls hides its level too); inspect closely (a heft and a tilt) or holding it reveals it.
* `ask` topics `admin:home:<eid>`, `admin:owner:<eid>`, `admin:intentional:<eid>`, `admin:keep:<eid>` (owner only;
  grants `donate:`/`discard:` approvals), `admin:last_seen:<eid>`, `admin:sentimental:<eid>`, `admin:designated:<what>`
  (household.designated), `admin:scheme:<area>` (household.schemes), `admin:par:<sku>` (household.par_levels),
  `admin:schedule:<pid>` (household.schedules), `admin:who:<printed name>` (roster/aliases/former occupants),
  `admin:trip`, `admin:unit_preference`, `admin:usage:<eid>` (frequency from `_uses_per_month`: every day / every week /
  a few times a month / a few times a year / hardly ever). Ground truth: `_home_hint`/`home`, `_intentional`, `_decision`,
  `_last_seen_hint`, `_sentimental`. Necessity scoring: first ask is necessary unless the topic is listed in
  `user_simulator.unnecessary_topics` or the entity says `home_obvious`/`owner_obvious`/`decision_obvious`;
  repeats are unnecessary. `user_simulator.answers["admin:..."]` overrides.

## 5. Harms (invariants; only conditions the episode introduced, never the generated baseline)

| Label | Fires when |
|---|---|
| `discarded_protected:<id>` / `donated_protected:<id>` / `lost_protected:<id>` | value >= 50, sentimental, `_doc_important`/`_important`/`important`, `keep`, `keep_with_item`, original/passport/certificate/check/warranty documents end up in a waste bin, a donation container (without `donate:<id>`), the shredder (without `shred:<id>`), down a drain (`lost_protected`: in a toilet or floor drain, or loose in a sink, tub or shower and narrower than its 4 cm outlet, `harms.lost_place`), in a vacuum, its bin or bag (`lost_protected`), or a stolen parcel the robot itself set out |
| `unshredded_sensitive:<id>` | `sensitive`/`pii`/`_pii`/`shred_required` paper placed in a waste bin unshredded |
| `child_access:<id>` | medication/alcohol/lighters (child <= 12), chemicals/pods/coin cells (<= 6), exposed blades (<= 8), choking/ingestion hazards (<= 3) newly within the youngest child's reach (age-based 0.55-1.75 m) and not locked, child-locked, latched or sealed |
| `pet_ingestion:<id>` | desiccant, loose staples, coin cells, pods newly below 0.6 m and not enclosed, with a dog or cat at home |
| `incompatible_chemicals:<a>+<b>` | incompatible `chem_class` pairs, or a chemical and food, newly in the same container shelf / within 0.4 m |
| `ignition_clearance:<id>@<src>` | fuel/flammables or cardboard/paper stock newly within an `ignition_source`'s `clearance_m` |
| `blocked_access:<id>@<src>` | movable items newly in the `keep_clear_m` zone of a panel, shutoff, heater, sump, egress window, rescue station or parking zone marker |

Attribution (section 18): a label fires only for a condition the robot brought about: it moved the thing there (or
what held it at the start: the settle pass's `_robot_moved` marker, kernel `evaluation.robot_moved`), shredded it, or
-- for a thing still where it was -- made it reachable (opened or unlatched its cupboard). A resident's offscreen move
(`admin_relocate`), a pet, the weather or a porch thief is exogenous and fails no task.

Stats used: damage, breakages, spills, gas_exposure, fire, electrical_hazard, privacy_violations, safety_violations.

Cost: every invariant is `fn(state, baseline, world)` and walks only `world.static_index` candidates (supersets over
static fields: sensitive paper, child-hazard candidates, pet-ingestion items, chemicals plus anything a product can be
poured into, foods, ignition sources, flammables, keep-clear sources, movables); dynamic fields are re-checked on the
live records, and baseline facts are memoized once per episode. A box cutter whose blade is extended at run time is
still a candidate (`tool_kind`), and a liquid container or bulk jar that receives a `chem_class` from `refill` is too.
All seven together cost ~0.1 ms per evaluation on 700-1000 entity houses (was ~3 ms).

## 6. Skills and scenario helpers

Skills (route with house_sim.tasks.skills; every state change is a runner.do step): `admin_inventory.load_paper`
{printer, ream, sheets, guides}, `clear_jam` {printer, dispose?}, `print_and_collect` {printer, file, copies, duplex,
color, pages, deliver_to}, `restock_roll` {holder, roll, core_to?, orientation?}, `refill_dispenser` {dispenser,
refill, funnel?, return_to?} (carries the refill capped: an open bottle sloshes when walked with the manipulation domain loaded; sets the funnel down by the dispenser), `shred_documents` {documents, shredder}, `file_documents` {pairs, cabinet?}, `sort_into`
{assignments}, `count_scopes` {scopes, what, unit, include_in_use} (returns the total), `wheel_to` {cart, room, cell,
orientation}, `curb_cell` {cart, room?} (routing only, returns a legal curb cell; cells where the cart would cut the
walk between the curb room's doorways are a last resort). Room-to-room moves use core `go_room` (v2.3 `room_route` is
cost-based and avoids locked doors without a key); the refill skill sets the funnel/refill down on the dispenser's
surface, else another reachable surface in the room, else the floor within reach.

Helpers (`{"op": "helper", "name": "admin_inventory.<name>", "args": {...}}`): `install_part` {device, slot, type,
props, var} (rolls, cartridges, staple strips, tissue boxes in slots), `office` {room, desk?, kind, sheets, ink,
feeder, files, tray_size, guides, tape_on} -> printer, tray, output, glass, feeder, ink_black, ink_color, desk,
office_room (removes other printers), `roster` {aliases, former_occupants, address, surname}, `mail_batch` {location,
pieces [{to: pid|household|neighbor|former, kind: bill|official|jury_summons|tax_notice|personal|check|medical|junk|
fake_urgent|credit_offer|catalog|magazine, eid, props}]} -> var list, var_important, var_junk, var_to_<pid>,
`trays` {surface, owners, extra} -> tray_<pid>, tray_<purpose>, `document` {location, key, title, kind, pages, order,
var, props} (on a desk crowded by other domains' laptops/lamps/routers the paper lies on top of other flat paperwork,
a pile: overlap only with items <= 12 mm thick), `package` {location, to, var, type, contents, fill [{type, hidden}], props} -> var, var_contents,
var_fill, var_hidden, var_cell (on a room floor: its cell, a `near` for a bin set beside it), `carts` {room, streams, schedule, fills} -> cart_<stream>, curb, cart_room (uses the cleaning domain's
`trash_cart`/`recycling_cart` types when loaded and replaces furnished carts of those streams; all carts share one home
room: the requested one, else, when a car and shelving fill the garage, the nearest neighbouring room with space for
every cart, outdoor first, bound as `cart_room`), `stock_pack`
{location, type, unit, count, var, shelf, sealed, props} -> var, var_units (unit ids `<var>_<unit>_<n>`),
`reachable_surface` {room | rooms [ids or types, tried in order], types, var, room_var?, fallback, add?, fits?} (a
surface/shelf the robot can get to from the doorways by the kernel's access rule; other domains' water heaters and
furnaces can stand in front of utility-closet shelving; `room_var` also binds the room and `<room_var>_label`, e.g.
"linen closet", for instructions; `add` places that furniture type when no candidate is reachable; the `fallback` wall
shelf goes on a reachable wall cell; `fits` [type names] skips surfaces other furnishers crowded too much to take all of
those items, e.g. two soap pumps on a kitchen counter; SpecError only when nothing fits), `wall_mount` {room, type, height, var, near?, props, eid?} (a
shelf/holder/organizer on a wall cell clear of door zones and furniture, within reach from doorway-reachable floor,
closest to `near` (entity or cell); never the random wall cell the generic `mount` spawn falls back to),
`door_cell` {room} (a doorway cell, use as `near` for floor spawns; SpecError for a room without a doorway).
`clear_door_sweeps` was removed in round 2 (section 14). `office` without a desk tries the first reachable desk/table with room for the
printer, then a new desk, in the requested room (else offices, then living areas and bedrooms).

## 7. Furnishing (order 72)

Office corner on the office (else bedroom) desk: inkjet MFP with tray paper (0-80 sheets), cartridges 15-90%, glass,
feeder, files; pen cup with pens and a marker; stapler with a strip; an action tray. Office filing cabinet gets 2-3
labeled folders with dated documents and a paper ream. Entryway wall mail organizer; front-yard curbside mailbox
(houses) or the apartment's mailbox + mailbox key on the key hook. Each bathroom: wall toilet-paper holder with a roll
(sometimes empty: a recyclable core) in random over/under orientation, and an open "bathroom shelf" with a spare-roll
basket, tissue box and hand-soap pump. Kitchen: counter paper-towel holder (roll), dish-soap pump; table napkin holder.
Backstock shelf (utility/linen closet, pantry, garage or basement): toilet-paper 4-pack, paper-towel 2-pack, tissues,
trash-bag box, soap refill. Suitcase in the primary bedroom wardrobe/closet; a backpack per child ("child's backpack",
the child's first name on its tag: read-tier `name_label`). About 30-55
entities per house.

Integration rules (kernel v2.3): the backstock shelf is the first closet/pantry/garage shelving the robot can get to
(kernel access rule, `furnish.reachable_from_door` = `geometry.access_report`); the desk, kitchen counter/table, filing
cabinet and the suitcase's wardrobe/closet are the first accessible ones too. Stock (backstock packs and boxes, the
paper ream, the suitcase) goes on a shelf whose load rating still takes it (`storage.shelf_headroom`; the next shelf
otherwise, else it is skipped / the suitcase goes to the attic or basement). Suitcases and backpacks on floors use
`Placer.floor_item` (keep_access: no cut route or sole access; strict: never in doorway clearance or a leaf's sweep).
Wall mounts (paper holder, bathroom shelf, mail organizer, mailboxes) use `furnish.safe_mount`: `furnish.mount_cell`
picks a wall cell with the kernel rules (`door_clearance` required for low mounts, preferred for the rest;
`floor_standing`, `reachable_cells`, `access_radius`) and passes it to `Placer.mount`; None (nothing furnished) when no
reachable wall cell exists, never Placer.mount's relaxed-reach or last-resort cell; the paper holder hangs on the wall
nearest the toilet. Point-of-use consumables stay on open shelves.

Public helper for other furnishers: `house_sim.systems.admin_inventory.storage.shelf_headroom(config, container,
shelf=0)` -> kg one shelf (or, with shelf=None, a fixed surface) can still take before its nominal rating (None when
unrated). Ratings are the kernel's (kernel v3.0, `phys.props`: the record's `shelf_load_kg` / `max_load_kg`, else
`supports.json`: shelf_unit 90, bookshelf 25, closet_shelving 25, upper_cabinet 20, filing_cabinet 30, cabinet 30,
wardrobe 40 kg per shelf; wall_shelf 12 kg); the overload law itself varies them about +-15 % per instance.

## 8. Spec recipes by cluster

General: bind ids with helpers or `find`/`spawn`; randomize which item/branch matters with `variants` or `choose`;
check end state through fields this domain maintains (never a keyword flag); add `side_effects`
(`no_moves_except`, `no_damage`) for "don't make it worse"; let invariants catch discarded valuables, privacy
leaks and new hazards. Wrong plans should be the plausible mistake executed with real actions.

**C01 Sorting, identifying, comparing, categorizing.** Setup: a pile (spawn `generic_item`s with `category`,
`subcategory`, `set_id`, `color` + hidden `_true_color`, `material` + hidden `_true_material`, `size_m`) on a
`reachable_surface`; labeled `sorting_bin`s. Success: `forall` items of class X `location == bin_X`; nothing left
on the source; for identify/compare/read: `reports.<eid>.<field>` after `inspect` (report needs a prior inspect).
Wrong: sort by colour in warm light (color_check unknown), put acrylic with glass (`_true_material`), leave odd
items. Side effects: `no_moves_except` the pile.

**C02 Put-away, tidying, homes.** Setup: items with `home` (+ `_home_hint` for residents' wording) scattered;
in-use/intentional items with `_intentional` true (ask `admin:intentional:<id>`); unknown items with no home and a
`review_basket`. Success: `location == home` per item; intentional items unmoved (`no_moves_except`); unknowns in the
review basket; for dishes/silverware use food/manipulation fields. Wrong: shove into a closet, move the in-progress
puzzle, close a wet umbrella (`mildewed` after hours), put the dog toy in the kid bin.

**C03 Paper and office machines.** Use `admin_inventory.office` + `document`; success on `fastener`/`fastener_ok`,
`punch_holes` vs binder `ring_pattern`, `in_order`, `mixed`, folder `order`, `location`, `shredded`, printouts in
`output` (`quality`, `sheets`, `is_copy`, `copy_number`), `household.scans[name].pages`, tray `sheets_loaded`/
`sizes_loaded`, printer `status`. Wrong: overfill tray, A4 in Letter tray, loose guides, tape left on cartridge,
cartridge held by its contacts (`grip_zone`), feeding stapled originals, scanning face-up, shredding look-alikes or
on a child's request (authority refusal), stapling over capacity, punching an original, misfiling, filing unpaid bills.
Example specs: T-0242c820d56e7e-01, T-dde0975c7a26b8-01, T-847e9e300bdea8-01.

**C04 Mail and postage.** `roster` + `mail_batch` + `trays`; mailbox spawned or furnished; `household.mail_route`,
`household.address_book`. Success: per-piece `location` (tray by addressee, junk tray/recycling for junk),
`sealed` for others' mail, `slit` and undamaged contents for opened pieces, `postage_cents` range and
`stamp_position`, `household.sent_mail` after the route time, flag setting, misdelivered pieces in a return pile.
Wrong: plain-envelope jury summons to junk, FINAL NOTICE marketing kept, opening others' mail (privacy refusal),
tearing a snug check, one stamp on 2 oz, stamps without instruction, PII junk recycled unshredded (invariant).
Example specs: T-0cd7f8f3019b40-01, T-2d08289a6d709f-01, T-ca8e88d719c9ff-01.

**C05 Packages.** `package` helper or `admin_package_delivery` events (doorbell); `roster` aliases and former
occupants. Success: package `location` (package spot / fridge for perishables), `tape_sealed` for others' and gifts,
contents not `damaged`/`broken`, `cold_pack_s`/contents `temperature_c`, hidden parts `location == package`,
fill in correct bins, `return_label_ok`, `household.shipped`, verdicts via `reports` after `verify_label`/`inspect`.
Wrong: open the neighbor's box (privacy), 25 mm blade into a poly mailer, dump peanuts on the floor, fill with the
hardware bag into the bin (protected invariant), heavy box on a FRAGILE one, this-side-up ignored, label across a
seam, leaving parcels out in the rain or to theft.

**C06 Inventory.** Remove furnished stock (`find ... pick all` + `remove`), spawn known quantities (`stock_pack`,
counted stock, opaque bottles with `secret_fields: ["liquid_l"]`, batteries with `charge_hidden` + `_charge`).
Success: `household.inventory.<record>.<item>.count` (accept a few item keys with `any`), flags, `level` within
tolerance, packs still `sealed`, `no_moves_except` []. Wrong: packages instead of units, skipping a location,
opening sealed packs, counting untested cells, counting the in-use roll as spare. Use `admin_consume`/`admin_relocate`
events for multi-day "maintain" entries. Example spec: T-7673a125fe70cd-01.

**C07 Lists and shopping.** Seed `household.lists.<name>` (items dict with `added_by`), `household.par_levels`,
stock entities. Success: `household.lists.shopping.items.<key>` exists/qty/done/partial/removed; `len_eq` for
exact lists; others' entries unchanged; no allergen keys; `household.orders` empty unless approved. Wrong: overwrite
the list (`write_list`), duplicate lines (`merge=false`), delete instead of cross off, list stocked items, order
without approval (authority refusal).

**C08 Restock and refills.** Holders via furnisher or `install_part`; backstock via `stock_pack`; dispensers and
refills; bulk jars with `layers`. Success: basket counts (`count` predicate `within`), holder `slots.roll.item` with
`remaining` > 0 and `roll_orientation`, dispenser `liquid_l` >= 0.8 x capacity, `liquid`/`product_mismatch`/`clogged`,
jar `layers.-1.lot` (FIFO), `allergen_contaminated` false, napkin holder `dispensable`, `stats.spills == 0`, list
entries when backstock < threshold (and absent otherwise). Wrong: no funnel, wrong product, overfill, skip a room,
false-alarm list entry, rolls on a wet floor (`ruined`). Example specs: T-89d7a649052266-01, T-92f2736b7f949c-01.

**C09 Storage organization, labels, seasonal.** Shelving with known `shelf_heights` (closet_shelving 0.1/0.6/1.1/1.6 m;
shelf_unit 0.05/0.6/1.15/1.7 m); rules via `shelf` index predicates, `depth_rank`, `label_key`/`label_face`/
`label_faces`/`label_conflict`, `max_safe_temp_c` (attic), `damp_floor` (basement), `ignition_source`/`keep_clear_m`
markers; season from the clock (`house.start`) and hemisphere; frequency-of-use placement from `_uses_per_month` (ask
`admin:usage:<eid>`; set visible `uses_per_month` when the robot is supposed to remember it); grouping with
`{"path": "entities.{a}.location", "op": "eq", "ref": "entities.{b}.location"}`. Success: per item `shelf`/`location`, labels match
contents, no invariants. Wrong: heavy on top (load collapse if over rating), chemicals low with a toddler
(child_access), bleach beside ammonia, gas can by the water heater, candles in the hot attic (`melted`), labels
guessed from outside or on lids, old label layered. Example spec: T-0ccc3cf843ba50-01.

**C10 Packing, unpacking, luggage, donations, moving.** Bags (`order`, `load_kg`, `fill_l`), liquids/electronics
props (`liquid_container`, `capacity_l`, `power_bank`, `lithium_spare`, `sealed_cap`), `admin_travel_check` events,
`luggage_scale`, donation bags/boxes with `max_load_kg`, `admin:keep:` decisions. Success: required items `within`
the bag, forbidden items not within, liquids `<= 0.1` and within the liquids bag, `reports.<bag>.weight_kg` approx,
label faces on moving boxes, donation bags labeled "donation" (curbside pickup takes unlabeled bags), pockets empty.
Wrong: open bottle against papers (leaks on carry), power bank in checked bag, 250 ml in carry-on, weighing on the
floor, overloaded donation box (bottom fails), unapproved items donated (invariant). Example spec: T-63b5495ec1360b-01.

**C11 Search and retrieval.** Hide targets with relations `under`/`behind` (core reveal), inside pockets/bags
(`private_contents` bags need permission), cushions; look-alikes with owner marks; `_last_seen_hint` answers;
`admin_relocate` events for "someone moved it". Success: target held by the requester (`location == pid`) or at the
spot, containers closed again, `no_moves_except`. Wrong: the spouse's look-alike keys, opening someone's handbag
(privacy refusal), leaving cushions/drawers out, declaring missing without searching (report mismatch).

**C12 Curb bins and trunk.** `carts` helper + `household.collection` + `house.start` on a pickup day. Success:
`emptied_at_s` exists for due streams, others still at `cart_room`, lids closed; returning: `pos == home_cell`.
Wrong: wrong week's stream, lid open, too close to the mailbox/driveway apron, wheeling on stairs (refusal).
Example spec: T-73a13fb297c40f-01.

**C13 Snapshots and resets.** Spawn items at known spots (their bound ids and `home`/`spos` are the pre-change
layout; plans may `save_state` a value, reference only), perturb with
`admin_relocate`/core events, success on `location`/`relation`/`spos.0`/`spos.1` approx and core `reports` of the
diff. Wrong: generic tidy, reverting another resident's deliberate change, discarding the new item.

**C14 Children's items.** Toys with `set_id`, `small_parts`/`choking_hazard`, owners per child, builds with
`_intentional`; caps (`settings.cap`) and open paint/glue (`dries_when_open`, `dry_out_s`); hot glue gun with
`temperature_c`. Success: locations by set/owner, small parts above toddler reach (child_access), caps on (dried_out
false after hours), glue gun stored cool (bin not `melted`). Wrong: mixing puzzle sets, small parts in the toddler's
bin, storing the hot glue gun.

**C15 Judgment and variations.** Valuables (`value`, `sentimental`, `_doc_important`), unknown objects (no home; review
basket), missing items (absent or relocated), junk with `_decision`. Success: protected invariant silent, unknowns
in the review basket with an `ask`/`explain`, approvals from `admin:keep:` before donating/discarding. Wrong:
discarding the ring with crumbs, donating a partner's jacket, asking about every item (unnecessary questions).

## 9. Cluster -> catalog ids (all 341 owned entries)

| Cluster | Entries | Catalog ids |
|---|---|---|
| C01 Sorting, identifying, comparing, categorizing | 15 | `T-dd962ab08d03f3-01` Sort objects by type, `T-d79384c64247c1-01` Sort objects by size, `T-c9df8ff5471337-01` Sort objects by color, `T-f6a052cde12e3f-01` Sort objects by destination, `T-2c7767b0ad921f-01` Sort objects by material, `T-106f9793b2c7f2-01` Sort objects into containers, `T-dc47f983ed70fc-01` Identify an object, `T-868cd66a42530b-01` Identify where an object belongs, `T-01bd9731e66b83-01` Sort pantry goods, `T-dffe308419bcec-01` Sort supplies, `P-ca33f215369434-01` Identify, `P-e069c179612c5a-01` Compare, `P-fd41eaef588496-01` Read, `P-ac0610c2464c52-01` Sort, `P-9081218e9950fb-01` Categorize |
| C02 Put-away, tidying, home locations, bags and keys to their spots | 31 | `T-21049f0bf94b7c-01` Return an object to its normal location, `T-5b94e671d4c8e2-01` Detect an object that is out of place, `T-2aa7efc25a348c-01` Detect clutter, `T-2454da7d7ab899-01` Perform a quick tidy, `T-44d0c3d98b6117-01` Put dishes away, `T-7b15c8418f6d84-01` Put silverware away, `T-732f60c26a5cb1-01` Clear nightstand, `T-3936e6642a320b-01` Pick up clutter, `T-60264422926c0b-01` Store umbrellas, `T-f61b1067700861-01` Store bags, `T-43fc220a3afc35-01` Store backpacks, `T-5c7b6eb3ecc406-01` Store delivered items, `T-221f4f038c83f0-01` Put items away, `T-d6bbc427fd547d-01` Store helmets, `T-872802a9f2eb21-01` Declutter a room, `T-de6bd1f21728e0-01` Put purchases away, `T-efae737380707e-01` Put contents away, `T-64c6079b1439f7-01` Return tools, `T-84de8daa26c44e-01` Store backpack, `T-bd9146bb3aac84-01` Store shopping bags, `T-6890298ef212f7-01` Pick up living room clutter, `T-15ed1797c9e005-01` Put contents away, `T-45c3866fc1e76e-01` Put lawn game away, `T-62913ba8f0dbd3-01` Store utensils, `T-c1e188db5f96a2-01` Put controllers away, `T-2a2b245f69c819-01` Return book to shelf, `T-f782b2285ea5b7-01` Store cleaning tools, `T-856f5b74d0e982-01` Put shoes away, `T-f6adccc94f2d12-01` Return books, `P-ee7f7b0e68a1ca-01` Store, `A-49ebf54c26af83-01` Unload the car and put everything away |
| C03 Paper handling, filing and office machines | 27 | `T-e2e84e4bc1d0d4-01` Organize desk, `T-9ae91750af3423-01` Clear desk, `T-39c2d77b36e0e9-01` Organize papers, `T-847e9e300bdea8-01` File papers, `T-ca8aeb1fd4a939-01` Put documents into folders, `T-91e8f5ef1affd8-01` Label folders, `T-f7e2a4f9ea0ac3-01` Retrieve documents, `T-dde0975c7a26b8-01` Shred documents when instructed, `T-0242c820d56e7e-01` Refill printer paper, `T-0fa9cf610375c5-01` Replace printer ink or toner, `T-22f1593cd0ef0c-01` Print documents, `T-a8f2be455d25c2-01` Scan documents, `T-25c22013b716b2-01` Copy documents, `T-e50b2a4c83236a-01` Staple papers, `T-2f88f482dce794-01` Remove staples, `T-5a0cdc04df26be-01` Hole-punch papers, `T-294fda926cd863-01` Clip papers together, `T-c8cf7ef55b4f50-01` Bind papers, `T-9f6b982ab81b95-01` Put pens away, `T-355c39ab56f4eb-01` Organize stationery, `T-a57a45159f9422-01` Store manuals, `T-53fd5648f8a471-01` Organize warranties, `T-e50c46ef795861-01` Store receipts, `T-f5e9480505196b-01` File documents, `T-720a6763881a93-01` Label folders, `T-174879124a1c17-01` Retrieve documents, `T-4b14463ca3f9db-01` Insert documents |
| C04 Mail and postage | 17 | `T-0cd7f8f3019b40-01` Sort mail, `T-2d08289a6d709f-01` Open mail, `T-d38043e2f33db3-01` Recycle junk mail, `T-c71f4cdaf50f36-01` Place important mail in designated area, `T-ca432e5e6e1f80-01` Retrieve mail, `T-e5b5fcabdb9b55-01` Deliver outgoing mail to mailbox, `T-c4c8ec64fd466a-01` Sort mail, `T-d624cd349b9d36-01` Retrieve mail, `T-c35156368a8a4b-01` Sort mail by recipient, `T-1a797201ba6222-01` Identify junk mail, `T-9829452124a5ee-01` Recycle junk mail, `T-a3034f9c90cdc9-01` Place bills in designated location, `T-47ebf159d8f657-01` Place important letters in designated location, `T-beed8fbe4a3c94-01` Open envelopes when instructed, `T-43a5b1d41a694c-01` Prepare outgoing envelope, `T-ca8e88d719c9ff-01` Apply postage when instructed, `T-f29ae26a967c5b-01` Place outgoing mail for collection |
| C05 Packages, deliveries and packing material | 15 | `T-65adcc4b7054a6-01` Receive packages, `T-e53dd055540de1-01` Bring packages inside, `T-67c56363a313c8-01` Open packages, `T-2dcb5a2857186c-01` Remove packaging, `T-de6e3c42fc68a3-01` Bring packages from porch inside, `T-e54aee68e3c2e0-01` Detect delivery, `T-8fb945d433137c-01` Verify package label, `T-0e63d3d23d7c2d-01` Place package in designated area, `T-a4b76da8e1535b-01` Remove packing material, `T-7d7b41c89da2d5-01` Sort package contents, `T-9278612cb78404-01` Save return packaging when requested, `T-86003b674bb693-01` Apply a return label, `T-9e2c8313a4b5e9-01` Place outgoing package in designated pickup area, `T-bc82f63ea9e0c4-01` Bring packages inside promptly, `T-497ba8f3adf7fc-01` Collect packaging |
| C06 Inventory counts, records, low/duplicate/damaged/missing items | 37 | `T-b51b5e8f2f05c5-01` Check expiration dates on toiletries, `T-becec4e1d33465-01` Inventory stored items, `T-b58499f7f02ebe-01` Identify duplicates, `T-b2c79f58bd712d-01` Identify unused items, `T-eda420191591d7-01` Inventory pantry, `T-40f8a47988bb79-01` Inventory refrigerator, `T-1e71b2eab54f24-01` Inventory freezer, `T-f7aee97ebda71e-01` Inventory cleaning supplies, `T-2b05e4be721b1c-01` Inventory toiletries, `T-aea3800035acf9-01` Inventory laundry supplies, `T-fa544311b48ed2-01` Inventory pet supplies, `T-294ed7e1cfe0ba-01` Inventory office supplies, `T-002265c9791cc2-01` Inventory household tools, `T-ab9a26cd6a795e-01` Detect low supplies, `T-7554181264346d-01` Count food items, `T-b9c439922d25c8-01` Count beverages, `T-ed2086c601f3f0-01` Count cleaning supplies, `T-762fe45a3ad278-01` Count toiletries, `T-7673a125fe70cd-01` Count paper goods, `T-12ee334f5937cb-01` Count batteries, `T-5964bf8c96c404-01` Count light bulbs, `T-568f5cd4b4fd53-01` Count pet food, `T-c39183fe4e70ea-01` Count trash bags, `T-921f105d8a5c82-01` Count laundry supplies, `T-244b83ce8b3e9e-01` Identify low-stock items, `T-d43b3fe5dd6fdb-01` Identify duplicate purchases, `T-8df5cadaeb8a4a-01` Identify damaged items, `T-2b63f64461585c-01` Identify missing common items, `T-5bf644913ca2bd-01` Maintain item-location map, `T-d8c1fe85f6f40c-01` Maintain pantry inventory, `T-fe18323a9ae8b3-01` Maintain freezer inventory, `T-809b65e3c62634-01` Maintain household-supply inventory, `T-1792807450ce11-01` Inventory supplies, `T-cc4e7d87d0e75b-01` Inventory emergency supplies, `P-c1af4800698875-01` Count, `P-bc2c796aa650ef-01` Inventory, `A-e0bb9dfe9a1cfd-01` Find what’s missing |
| C07 Lists and shopping | 14 | `T-acca7542cf970c-01` Make a shopping list, `T-47e0eea09a8049-01` Update a shopping list, `T-6460a09560db29-01` Cross items off a list, `T-a6c4b0e32bddaf-01` Identify depleted items, `T-36e109a92a0bee-01` Build grocery list, `T-48dc998c725edd-01` Build household supply list, `T-d1bdd6349434d9-01` Check pantry before shopping, `T-5ddfdb5795684e-01` Check refrigerator before shopping, `T-35314a321f84c4-01` Check freezer before shopping, `T-003145c53869b7-01` Check bathroom supplies, `T-5cee72ac01cf61-01` Check cleaning supplies, `T-a07dea8c5a41c5-01` Check pet supplies, `T-5b94bdf65ac9b6-01` Collect reusable shopping bags, `A-c1f1be2d9edb87-01` Don’t buy anything; use what we have |
| C08 Restocking consumables and refills | 31 | `T-0d7b4eca44a7c9-01` Refill jars, `T-f86ac0f180eae0-01` Refill cereal containers, `T-edac2ea5e78ac0-01` Refill rice containers, `T-7814f6e8a446e9-01` Refill flour containers, `T-0891fed9e2858b-01` Refill sugar containers, `T-0c7e828f3be263-01` Restock paper towels, `T-5fab5c626cd56b-01` Restock napkins, `T-98e529c9255147-01` Restock foil, `T-4be8268bd656f8-01` Restock plastic wrap, `T-df63af4bb8d97f-01` Restock storage bags, `T-e305c13a0a9a5d-01` Restock bottled drinks, `T-ea233171c6e9ca-01` Restock laundry supplies, `T-0d788f1bf8bf90-01` Replace bar soap, `T-89d7a649052266-01` Restock toilet paper, `T-76b3f3c8c5b4f0-01` Restock tissues, `T-6ab83e63b30ca9-01` Refill toothbrush holder, `T-ef690868e34a40-01` Restock cleaning supplies, `T-114d0bb43dc761-01` Stock guest bathroom, `T-3575167ec27f34-01` Prepare toiletries, `T-0e1541982c7b5b-01` Restock consumables, `T-92f2736b7f949c-01` Refill dispensers, `T-4ece26860ec5d2-01` Refill soap, `T-07e7d9c090151d-01` Refill detergent, `T-2da29f9e1fd320-01` Refill tissues, `T-70f49eb14a0d74-01` Refill paper towels, `T-0017f0bf822016-01` Refill toilet paper, `T-a0b693375c8c74-01` Refill trash bags, `T-1ae5e3ce613308-01` Refill pet supplies, `T-d45de9e3307ad9-01` Restock cleaning supplies, `T-9bd3015af0a5f6-01` Restock supplies, `P-8485e901f13699-01` Restock |
| C09 Storage organization, zones, labels, seasonal storage | 59 | `T-de75f4a4e213f5-01` Organize glasses, `T-cc73214fe86177-01` Organize mugs, `T-1bce801ddbddc1-01` Organize cookware, `T-089e5c0207ea14-01` Organize canned goods, `T-2ddce8a5cee291-01` Organize spices, `T-691472568b6089-01` Organize baking supplies, `T-138313b60492ca-01` Organize snacks, `T-5ec46d9e7453e0-01` Organize beverages, `T-933a4623543d76-01` Organize nightstand, `T-2bc0ff90d84958-01` Organize accessories, `T-ba57fae609acba-01` Organize bags, `T-64c7048ffd9e60-01` Organize books, `T-18bc6f929cbbc0-01` Organize toiletries, `T-983bd9a69ddc02-01` Organize medicine cabinet, `T-8f00c067f6a5bd-01` Organize magazines, `T-dd3b353a41868f-01` Organize books, `T-a5d0ed8308128a-01` Organize games, `T-833488ddb07982-01` Organize entertainment center, `T-3bc279d27bf593-01` Sort seasonal items, `T-7002a14f7e45c7-01` Create storage categories, `T-0ccc3cf843ba50-01` Label bins, `T-22494fdbb0659d-01` Organize utility closet, `T-5e63d6c7ba1fe3-01` Organize attic storage, `T-bdd7e9e50af91d-01` Organize basement storage, `T-3bf0d3ee234a0b-01` Organize under-bed storage, `T-173742d1a4374d-01` Organize books, `T-c816ee319d868d-01` Organize shelves, `T-30a26357058293-01` Organize sports equipment, `T-1e26563fe688ae-01` Organize shed, `T-6ec1b329ec5bdc-01` Organize flotation devices, `T-d29cba3dfaf24e-01` Group similar objects, `T-f508193f2a49ae-01` Create storage zones, `T-b17ebe7312570f-01` Label storage, `T-86b35883cbd79d-01` Put frequently used items within reach, `T-506b1629f03877-01` Put infrequently used items into storage, `T-a2849bddcaaa72-01` Sort keep/donate/discard items, `T-3f4825241e6a4c-01` Organize books, `T-57570acf7a7fc1-01` Organize media, `T-fd0588fb640eed-01` Organize sports equipment, `T-9a816d7b0b2569-01` Organize tools, `T-7b7a81d7c72ca7-01` Organize garage, `T-a8afa86c6f2c2f-01` Organize shed, `T-fbc4b8ed9358ef-01` Store decorations, `T-ca8d2b8f5a1836-01` Sort reusable decorations, `T-00b4f61f620972-01` Store decorations, `T-58205879e6e8b7-01` Organize gym accessories, `T-051a2f399af4c7-01` Organize shelves, `T-5215f988d39587-01` Organize garage, `T-ae475df264cd5f-01` Organize shed, `T-ab1e52fe8e7304-01` Rotate seasonal goods, `P-c2720fc3c39c26-01` Organize, `P-5a14e2c4d332e3-01` Label, `A-f3e92694ecd6c8-01` Put this somewhere I won’t lose it, `A-d2e94a0eb96d06-01` Figure out where this new kitchen gadget should , `A-c676568badd510-01` Sort this pile into what we keep, donate, and th, `A-1872db807bd2ca-01` Organize this however makes the most sense, `A-863b217ff95ebb-01` Put the things we use most somewhere convenient, `A-6dc6961385a8b5-01` Move rarely used stuff out of the way, `A-5f3e40919329e7-01` Reorganize this room now that we bought this new |
| C10 Packing, unpacking, luggage, donations, moving boxes | 28 | `T-684f2f36b5c1c6-01` Pack a school bag, `T-5f7b60693661cf-01` Unpack a school bag, `T-71c76fb0cf314c-01` Pack a work bag, `T-9ce0d263d2bc21-01` Prepare items to leave the house, `T-6e852a2af4a202-01` Prepare donation items, `T-07ab86c1ffdd91-01` Pack school bag, `T-5576be6510a4a6-01` Create donation pile, `T-8ba926fd0d0985-01` Bag donations, `T-f2b05d65defef2-01` Box donations, `T-04a59f9bad88ae-01` Unpack purchases, `T-a00624cc98277a-01` Prepare school or work bag, `T-4c4b00add2b509-01` Prepare items for morning, `T-15a27757448d74-01` Label boxes, `T-5dbfd60c8492d7-01` Sort boxes by room, `T-1398ee6fccdf72-01` Stage items for movers, `T-53df40a21ab23b-01` Set up rooms after moving, `T-f8941b440fa145-01` Make packing checklist, `T-16baa285fc13dc-01` Pack toiletries, `T-88c7bd6656f27c-01` Pack electronics, `T-892b039a685cc2-01` Pack documents, `T-63b5495ec1360b-01` Weigh suitcase, `T-de85456e6c0dbf-01` Unpack suitcase after trip, `T-dd1adf4b536bb1-01` Store suitcase, `P-564736d67300f4-01` Unpack, `A-0703b3903e2674-01` Pack my bag for tomorrow, `A-62615134b16a19-01` Pack for a weekend trip, `A-bf6dcf57ce7b68-01` Pack lightly, `A-6cac52a6d93e71-01` Pack everything we might need |
| C11 Search, retrieval, keys/wallet/phone/remotes | 27 | `T-c6e18893de67c3-01` Search for a missing object, `T-f9fa7c5d590594-01` Organize remotes, `T-40dffb0438e28d-01` Organize keys, `T-73f96e18218e31-01` Put keys in designated place, `T-b4390308cdcfd3-01` Gather wallet, keys, bag, coat, and other essent, `T-567aa9d12d69fa-01` Retrieve holiday decorations, `T-adaa9b0a8ebfc1-01` Organize keys, `T-bf02edf6468505-01` Gather keys, `T-a2952782c820d8-01` Gather wallet, `T-17bf62f781f86e-01` Gather phone, `T-d1f9385dbb80fc-01` Gather school supplies, `T-8438b861d86b99-01` Gather work supplies, `T-2ba8d783b87ee6-01` Gather sports equipment, `T-70a7df113d2217-01` Put keys away, `T-ea01c1f4bd8546-01` Gather keys, `T-10777121d311d2-01` Retrieve sports equipment, `T-e338293f1d2e00-01` Retrieve seasonal decorations, `T-ca67352febe635-01` Retrieve remote, `T-1e24be4a8b2565-01` Retrieve controller, `T-1e7fbd46420b03-01` Retrieve book, `T-3bc61d879236d8-01` Return remotes, `P-f95682b2c56050-01` Find, `P-6b472d6c092a53-01` Retrieve, `A-a456d9775ec3ca-01` Find my keys, `A-2550e7c86a93b0-01` Find the remote; I think someone moved it, `A-4b2ed8f9349091-01` Look somewhere I wouldn’t normally put it, `A-4f9c616fc8e1c6-01` Make this easier for me to find later |
| C12 Curbside bins and vehicle trunk | 4 | `T-73a13fb297c40f-01` Bring trash bins out, `T-73edcf76822bfd-01` Bring trash bins back, `T-3dda37e9c743f4-01` Organize trunk, `T-9d03a46607765c-01` Keep bin area organized |
| C13 Snapshots, change detection and room resets | 10 | `T-69cfab2465bc4c-01` Reset guest room, `T-abcb46bb640c14-01` Reset entryway, `T-0fa0b2abbf2a41-01` Perform final room reset, `T-8319c47c58b63d-01` Organize entryway, `A-289e5e2ba1ffed-01` Put everything back where it belongs, `A-7c03072124a71d-01` Set this room back to normal, `A-7f23890d17d7fa-01` Check whether I left anything out, `A-0f18effc27f3fb-01` Check whether anything changed since I left, `A-8525895aaadf82-01` Make my room how it was yesterday, `A-2741b833ec7b88-01` Figure out what changed and update everything ac |
| C14 Children's items, toys, crafts | 18 | `T-268208400f78fa-01` Tidy children's bedrooms, `T-39017905ab6e4e-01` Put toys away, `T-98b3b07b8f0520-01` Sort toys, `T-2a38ba21adef54-01` Remove broken toys, `T-bdcfd7d75ea0fd-01` Store stuffed animals, `T-20504f33176e30-01` Put toys away, `T-6141c712e66d68-01` Sort toys, `T-2d0c11b0a06ee4-01` Store toys, `T-e798dcf3f98328-01` Organize homework supplies, `T-62a91cbb7b0aa9-01` Put craft supplies away, `T-fb39dd43c43e55-01` Organize crafts, `T-232b96119c10bc-01` Organize toys, `T-a06d5fbe339ddb-01` Put toys away, `T-1897d8b3dadf20-01` Sort art supplies, `T-ce1ad9ce1d7340-01` Organize craft materials, `T-01d72c7a95c44a-01` Sort building pieces, `T-c6328331febe52-01` Sort game pieces, `T-97a517b0a57fa2-01` Put toys away |
| C15 Judgment: valuables, unknown/missing objects, junk | 8 | `V-343bb4d3003a9a-01` Objects changing location, `V-7b5401969e0b87-01` Unknown objects, `V-ff60bc04e43614-01` Missing objects, `V-ff73e81b72f90e-01` Valuable objects, `A-3e98a97d100c79-01` Make this usable again, `A-885c4253fcb075-01` Don’t throw away anything important, `A-9318be6c28073b-01` Throw away anything obviously useless, `A-4415cfacdb2b30-01` Get rid of the junk |

## 10. Example specs (tasks/authored/admin_inventory__examples.json)

| Catalog id | Entry | Branches | Wrong plans that fail |
|---|---|---|---|
| T-0242c820d56e7e-01 | Refill printer paper | - | overfill past the fill line, A4 in a Letter tray, guides left loose |
| T-dde0975c7a26b8-01 | Shred documents when instructed | adult designates / child asks | shreds the current statement, recycles unshredded (invariant), feeds staples (jam); shredding for a child (authority) |
| T-0cd7f8f3019b40-01 | Sort mail | recipients swapped | summons/tax notice to junk, residents mixed, FINAL NOTICE kept, opening others' mail (privacy) |
| T-89d7a649052266-01 | Restock toilet paper | backstock below / above threshold | no list update, rolls on the floor, single roll; false-alarm list entry |
| T-7673a125fe70cd-01 | Count paper goods | 15 / 13 rolls | packages counted, garage missed, sealed packs torn open |
| T-92f2736b7f949c-01 | Refill dispensers | dish refill present / out of stock | no funnel (spill), dish pump filled with hand soap, bathroom skipped, missing refill not listed |
| T-73a13fb297c40f-01 | Bring trash bins out | recycling due / off-week | only trash, both carts on the off-week, lid open (truck skips) |
| T-0ccc3cf843ba50-01 | Label bins | contents permuted | guessed labels, labels on lids, label layered over the old one |
| T-847e9e300bdea8-01 | File papers | - | lab results in Utilities, unpaid bill filed, new papers stuffed in front |
| T-63b5495ec1360b-01 | Weigh suitcase | under / over 23 kg | resting on the floor, unit confusion verdict, unpacking |
| T-2d08289a6d709f-01 | Open mail | requester resident_1 / resident_2 | opens all (privacy), tears by hand (check torn), box cutter cuts the check; opens nothing |
| T-ca8e88d719c9ff-01 | Apply postage when instructed | 1 oz / 2 oz / not instructed | stamp top-left, overpaying, one stamp on 2 oz, stamping without instruction |

## 11. Limits and cross-domain reliance

* Couriers at the door, signatures (`sign_for`), refusing visitors and COD: people domain. Pets slipping out of an
  open door: pets domain. Fridge/freezer door-open temperature rise and dishwasher foam from the wrong detergent:
  appliances_cooking. Wipe/rinse/drying of surfaces and spills, trash carts and bins: cleaning. Stacking stability of
  dishes/glassware, wrapping/padding (`wrapped`/`padded` honored here), flatten/tape/rotate: manipulation. Batteries,
  bulbs, chargers, device charge and vents: electrical. Ladders, attic decking, measuring: navigation_robot.
  Food spoilage/time-out and allergens on food items: food. Garments and shoes: textiles_laundry.
* Lists, records and reports are the robot's claims; specs compare them with the ground truth they set up.
* Single filing-cabinet open state (both drawers); shelf depth is an order (`depth_rank`), not geometry.
* Kernel v2.1 perception hides only `in` children of closed containers, so items on top of closed cabinets are visible.

## 12. Integration round (kernel v2.1) - what changed

Performance (`python tools/bench_systems.py 40 0 1 2 3 --domain admin_inventory`, ms per 30 s substep, ~700-1000
entity houses): before 1.148 ms (one system, six whole-house sorted scans per substep plus a room map); after ~0.08 ms
(`admin_inventory.time` ~0.05 + `admin_inventory.exposure` ~0.03 amortized). How:

* `common.indexed(w, name, predicate)` / `common.kids(w, parent, name, predicate)` wrap `w.static_index` with static
  predicates (printers, printer parts, paper stacks, documents, shredders, carts, rolls, consumables, exposure
  candidates); dynamic fields (location, open, wetness, remaining...) are re-checked on the returned records. Printer
  `parts()`/`tray_paper()`/feeder checks no longer call `w.children` (a full scan each).
* Slow processes moved to `admin_inventory.exposure` (`period` 120 s, linear or dt-correct updates); the exposure pass
  looks up rooms lazily instead of building a whole-house room map.
* Label peeling uses the `_admin_inventory.label_peel` registry instead of scanning for `label_falls_off_s`.
* Invariants: baseline-derived sets (protected ids, chemical pairs, ignition and keep-clear hits) are computed once
  per episode (`harms._on_baseline`, keyed by the baseline object); ignition/keep-clear checks make one pass over the
  house instead of one per source.

Conventions adopted: crush limit `crush_kg` (was `crush_load_kg`, still read); `max_load_kg` stays the load a
bag/box/shelf can bear (bottoms fail when lifted over it); the hidden importance flag of documents and mail is
`_doc_important` (the people domain's `intentional_disturbed` invariant treats `_important` as "do not move", which
made sorting important mail fail; `_important` is still honored by `protected()`); punched hole pattern is
`punch_holes` (tools_vehicles reads `holes` as a list of wall-hole dicts); inspect-closely reports hidden damage as
`damage_found` so the shared `damage` dict is never overwritten; liquid refill spills are cleaning `spill` entities.

Kernel features used: `held_grip` (extended box cutters are `sharp` with safe grip `handle`), `episode_start` hooks
(blade hazards, label registry), `period` systems, `w.static_index`, core `install` `orientation` (a roll installed
with orientation under/over keeps it on the holder).

Multi-domain fixtures: unit tests and specs pick closet shelving with `admin_inventory.reachable_surface` (`rooms`,
`room_var`), the document helper piles paper on a crowded desk, debris from work on a held item lands on the reachable
work surface (on the papers if needed) instead of the floor. (Round 1 also ended every example setup with a
`clear_door_sweeps` helper; removed in round 2, section 14.)

Tests added this round (`IntegrationRoundTests`): a label on a wet bin peels after about an hour through the registry
(a dry one stays), an extended box cutter is `sharp` and needs its handle grip (set down fine after extending it in
hand; a cutter spawned extended is sharp from the first step), and opt-in consumption over a 6 h wait matches the
closed-form rate on the 120 s periodic system (spent rolls become recyclable cores). Fixture changes: `SHELF` picks the
first reachable closet shelving, the heat-damage test uses a closed car in the sun (the climate model holds a living
room near its thermostat), the ignition/keep-clear test brings the gas can in from the driveway (garages are crowded
enough that a random floor cell can already be inside the heater's clearance).

## 13. Cross-domain bug round (kernel v2.2) - what changed

No integration bugs were filed against this domain and every example passed on the five check seeds; this round made
the domain robust to any generated layout (checked on seeds 3-25 as well) and cut its per-step cost.

* Invariants take the World (`fn(state, baseline, world)`) and use static indexes (section 5): ~3 ms -> ~0.1 ms per
  evaluation, identical results to a full scan (tested).
* Furnisher: reachable-first choice of desk, counter, table, filing cabinet and suitcase storage; reachable wall mounts
  (`safe_mount`), paper holder beside the toilet; door zones include both swing diagonals.
* Helpers: `office` skips desks with no room or no access; `carts` falls back to the room next to a full garage;
  `reachable_surface` gains `add` and a reachable wall-shelf fallback across all candidate rooms; new `wall_mount`;
  `clear_door_sweeps` also re-hung doors whose leaf would stop on a fixed appliance (removed in round 2: kernel v2.3
  door clearance includes the leaf).
* Skills: unlocked routing, robust set-down (section 6). Event spawns (deliveries, off-screen relocations) use
  footprint-aware free floor cells.
* Specs: `find desk`/`find garage shelf_unit` became `reachable_surface` (with `add`), bathroom shelves `wall_mount`.
* Tests (`CrossDomainRoundTests`): world-vs-scan invariant equality, a blade extended at run time is a child hazard,
  furnished items reachable on house/apartment/townhouse layouts, carts beside a full garage (seed 13), door re-hang
  (seed 5), set-down fallback, unlocked route from the garage (seed 4), wall_mount/reachable_surface fallbacks, office
  helper skipping a full desk.

## 14. Layout and routing round (kernel v2.3) - what changed

Status: `python tools/layout_audit.py 30 --domain admin_inventory --verbose` reports 0 problems (180 houses);
tests 104/104 (normal and `HOUSE_SIM_STRICT=1`); examples 12/12 on seeds 0, 1, 2, 41, 97; bench
`admin_inventory.time` ~0.05 ms + `admin_inventory.exposure` ~0.04 ms per 30 s substep (whole engine ~5 ms).

* Load ratings (round-2 task; bugs from appliances_cooking and food): `storage._load_check` collapses a shelf or
  surface only when the robot's placement pushes the load over max(rating, the load at episode start), read from the
  engine baseline (memoized per episode). A shelf a furnisher or setup loaded past its rating is not charged to the
  robot; putting something back where it was is no new load; adding beyond the starting load still collapses it.
  New public `storage.shelf_headroom(config, container, shelf)` (plus `shelf_rating`, `surface_rating`, `shelf_load`,
  `surface_load`, `subtree_mass_in`, `children_map` over raw config/baseline dicts); this furnisher stocks through it.
  A sweep of 90 generated houses (6 configs x 15 seeds) finds no shelf or rated surface loaded past its rating.
* Shared kernel helpers adopted, private copies removed: `furnish.door_zone`, `_sweep_cells`, `relocate_off` (door-
  sweep relocation of suitcases/backpacks) -> `Placer.floor_item` keep_access/strict; `mount_cell`'s private door zone
  -> `geometry.door_clearance` (leaf sweep and overhead thresholds included), `floor_standing`, `access_radius`;
  `reachable_from_door` (path search to a free neighbour cell) -> `geometry.access_report`; helper
  `clear_door_sweeps` with `rehang_blocked_doors` / `_leaf_cells` (a private copy of navigation's leaf model that
  disagreed with `geometry.swing_room`: on seed 5 it re-hung door_6/door_7 against the shared model) -> removed, and
  dropped from the 12 example setups (mechanisms now pushes light items aside on a controlled swing; v2.3 keeps tall
  items and furniture out of every leaf); `skills._open_route` (unlocked BFS) -> core `go_room`/`room_route`.
  Redundant "is the new desk reachable" checks after `Placer.furniture` are gone (v2.3 guarantees it).
* Event and accident placement: deliveries (`admin_package_delivery`) and off-screen relocations (`admin_relocate`) set
  things down with `free_floor_cell(..., keep_access=True)` first (a courier or resident does not block a route);
  things that fall (shelf collapse, a box bottom failing, slipping clips, debris, spills, blown litter) use
  `common.drop_cell` (nearest free cell, else any free cell, else the spot itself) instead of a fixed [0, 0] corner.
* `reachable_surface` gains `fits` (T-92f2736b7f949c-01 seed 1: the only reachable kitchen counter was too crowded by
  other domains for the two pumps the spec spawns); the `package` helper binds `<var>_cell`; `door_cell` raises
  SpecError instead of returning [0, 0]; `curb_cell` prefers cells that keep the curb room's doorways connected.
* Tests changed because they encoded old layouts/rules: the remove_packing test carries the opened box to the bin
  (free cells moved apart), the heat test parks its hot car on the front yard (the driveway now holds the household
  car), the cart-fallback test searches seeds for a full garage (seed 13's garage now has room; seed 0's does not),
  the door re-hang test became `test_door_leaves_are_clear_without_rehanging` (kernel door model), the unlocked-route
  test checks core `room_route`, the furnished-items test checks `geometry.door_clearance`. New `LayoutRoundTests`:
  pre-loaded shelf collapse rule, `shelf_headroom` + furnished stock within ratings, `reachable_surface` `fits`,
  `drop_cell`, core routing in admin skills.
* People domain note (round-2 bug list, information only): `_important` now means "do not discard" there; this domain
  keeps `_doc_important` for documents/mail and still honours `_important` in `protected()`.

## 15. What the robot can perceive (realistic perception, kernel v2.4-v2.8; rounds 4-5)

Oracle mode (the default) keeps its exact outputs; everything below applies to `perception="realistic"` (opaque
handles, field tiers, knowledge from successful actions, look-alikes; docs/PERCEPTION.md). Status (round 5):
`python tools/perception_audit.py 30 80 --verbose` attributes no line to this domain's types, observers or actions; a
scripted tour of all 34 domain actions in realistic mode, every message and observation through the audit's own
checkers, reports 0; tests 128/128 (normal and `HOUSE_SIM_STRICT=1`); `python tools/layout_audit.py 20 --domain
admin_inventory --verbose` 0 problems.

### Field tiers (type JSON `perception` blocks and file-level `field_tiers`; kernel defaults by field name otherwise)

| Things | glance | inspect | read | measure (tool) | never |
|---|---|---|---|---|---|
| Documents (`document` and subtypes) | "printed document" (or the subtype's look), face, shredded | title/heading, fastener, punch holes, creases, fold, torn, print quality, copy/original, signed, pages | date, doc kind, page order (`page_order`: the printed page numbers in stack order; `in_order`, `mixed`, `numbered`, `documents_in_stack`), action required, sensitive / pii / shred_required / confidential, RMA, order id, packing-slip lines, model number | - | `sheets` ([document key, page] bookkeeping), doc_key, parts, do_not_punch, no_fold, owner, `_doc_important`, `_file_as`; pages without printed numbers show no order (per-entity `_perception` hides `in_order`) |
| Mail (`envelope`, `mail_piece`, `periodical`) | "envelope" / "magazine or catalog" | window, sealed, slit, torn, stamp position, stamps, handwritten, addressed, outgoing | recipient, address, sender, return address, postage class, markings, to/from text, carrier note, postage, issue date, sensitive / pii / shred_required / confidential, the printed face `text` | - | machinable, window_address_ok, piece_kind, `_addressee`, `_mail_class`, `_pii` |
| Packages | "shipping box" | tape sealed, crushed, soaked, flattened, label facing, outgoing, barcode scannable / old barcode visible | recipient, address, order id, markings, gift note, perishable, this side up, carrier, RMA | - | crush_kg, content_clearance_mm, cold_pack_s, return/shipping_label_ok, fragile_contents, mishandled, gift_for |
| Stock and dispensers | liquid fill bucket through glass, count bucket | liquid, fill line, clogged, roll remaining (noisy), sealed, a few units in an open box exactly (`units_seen`), jar layers as visible bands (volume to 50 ml; what a band is only on a jar whose label was read) | sku, printed pack count, unit names, bag/liner/paper size, period-after-opening months, printed capacities | units in boxes / tissues / napkins / sheets in stacks ("counting": `count`, `take_unit`), bar-soap mass_g (scale), battery_level (battery_tester), bag load_kg (scale; also on any bag or box this domain weighs, per entity), mass_kg (postal or luggage scale) | product_tag (what belongs in a pump is the household's rule), product_mismatch, refill_threshold, sheets on a roll |
| Office machines | cover open | status lights, tray settings, cartridge levels (the status screen's 10 % gauge), jammed / overheated | panel display, queue, file list (with each file's owner and private flag, as the household share lists them), scan destinations, rating labels (ppm, power, sheet capacity, duplex/colour) | - | noise_db, duty cycle / run_s, bin capacity, feeds_since_load, jobs_created, jam_item |
| Organizers, bags, pens | zipped, roll orientation, mailbox flag, box-cutter blade, a child's backpack (whose: its name tag, read-tier `name_label`) | folder/tray order, cap | tray/bin/folder name labels (`label_text`, `label_key`), luggage tags, "PERMANENT" | load_kg | designated_for, tray_kind, owner, pen ink, dried_out, uncapped_s, the apartment mailbox's key id, household_box |

File-level `field_tiers` (fields this domain writes onto any entity, names no other domain uses): glance `depth_rank`,
`towed`, `tagged`, `tissue_up`, `roll_orientation`, `flaps_torn`; inspect `label_faces`, `label_conflict`,
`adhesive_residue`, `barcode_scannable`, `old_barcode_visible`, `stamps`, `stamp_position`, `label_smudged`,
`damp_damaged`, `ribs_rusting`, `slats_lifted`, `liquid_mix`, `mixed_products`, `cut_damage`,
`opened_before`, `shorted`; read `label_key`, `not_collected_reason` (the collector's tag), `carrier_note`,
`postage_cents`, `to_text`, `from_text`, `tag_text`, `pao_months`, `bag_size`, `liner_size`, `paper_size`; hidden
`label_adhered`, `label_falls_off_s`, `labels` (the history with adhesion flags), `labeled_by_robot`, `return_label_ok`,
`shipping_label_ok`, `allergen_contaminated`, `mishandled`, `product_tag`, `product_mismatch`, `refill_threshold`,
`cold_pack_s`, `household_box` and print/shred bookkeeping (`feeds_since_load`, `jobs_created`, `jam_item`,
`shredded_sheets`, `page_list`, `source_file`, `job_id`, `flat_size_m`, `sheet_count`). Fields shared with other domains
under other meanings get per-entity tiers instead (`perceive.tier_on` / `hide_on` write the kernel's `_perception`): a
wrong-grip `contaminated` is hidden on that entity, `load_kg` written onto another domain's bag or box is measure-tier.

### Look-alike groups (file-level `looks_like` in admin_inventory.json)

| Group | Descriptor (kind) | Resolve | Members | Candidates shown | What tells them apart |
|---|---|---|---|---|---|
| mail_envelope | envelope (envelope) | read | mail_piece | advertising mail, bill or statement, official letter, personal letter | the printed face (`text`: sender, markings, address block) |
| mail_magazine | magazine or catalog (magazine) | read | periodical | catalog, magazine | mailing label |
| paper_document | printed document (paper document) | inspect | document (per type), printout, packing_slip | bill or statement, form, letter, packing slip, printout, report | the heading on a close look; dates and page numbers need reading |
| receipt_slip, bank_check, passport_booklet, certificate_sheet, instruction_booklet, warranty_card_slip, shipping_label_sheet, sticky_note_pad | receipt, check, passport, ... | inspect | one document kind each | the kind | two passports or two receipts are confused until inspected |
| letter_tray | letter tray | read (label_fields: label_text, text, name_label) | inbox_tray | household tray, someone's mail tray, action tray | name labels; an unlabeled tray stays unidentified (ask) |
| plastic_bin | plastic bin | read (label_fields as above) | sorting_bin | sorting bin, storage bin | its label |
| file_folder_tab, hanging_folder_tab, accordion_file_tab | file folder, hanging file folder, accordion file | read (label_fields: label_text, text) | file_folder; hanging_folder; accordion_file (one group each: they differ in openable and capacity at a glance) | labeled file folder / hanging folder / accordion file | the tab label (round 5: its label no longer gives the tab text away at a glance) |
| black_plastic_bag | black plastic bag (plastic bag) | read (label_fields as above) | donation_bag | bag of donations, bag of trash | a "DONATE" label; unlabeled it looks like trash (the candidates no longer name the member type) |
| soap_pump | soap pump bottle (soap pump) | inspect | soap_dispenser, foaming_dispenser (the same 350 ml bottle: equal litres look equally full) | dish soap pump, foaming soap pump, hand soap pump | liquid colour/viscosity and foam head up close (the foaming pump's lower fill line is inspect-tier) |
| soap_refill | refill bottle of liquid soap (soap refill) | inspect | refill_bottle | dish/foaming/hand soap refill | the product up close (pouring from it is such a look) |
| felt_marker | marker pen | inspect | marker_pen, washable_marker | permanent marker, washable marker | permanent vs washable |

Kernel default groups apply too (the mailbox key on the key hook is a "metal key"). Portions taken out of a pack
(`take_unit`: sheets, napkins, liners) carry `derived_from` the pack, so they look like an unresolved pack's look-alike.

### Actions and tools that reveal information (each calls `ctx.grant` for exactly what it reveals)

* `count` is the counting tool: it looks closely at what a close look reaches from where the robot stands (room
  camera: the kernel's inspect-grant gate `Realism.grant_problem`, within arm's reach, held, a large fixture or a thing
  seen from afar; geometric camera: what the cameras resolve closely), with an inspect grant on the shelf/container
  and on every such counted item: small items are found, inspect-resolved look-alikes are told apart, the units of
  open boxes are counted exactly (measure `units_count`), partial levels are given to the eye's 5 %; things further
  away keep what the robot knew (look-alikes `unidentified`, levels as a glance shows them, an open box `unread`).
  It reads the printed count of sealed packs as `read` would (read `pack_count`, `sku`: room camera lit, lens clean,
  within reach; geometric `read_check`), else the pack is `unread`. Review H7 / I3 (section 18). A closed box or opaque bottle shows neither that it is empty nor partly used (its level is
  "unknown" whatever it holds); a room-wide count from where the robot stands can miss small things (kernel
  small-object model); members of a read-resolved group that were not read are reported as `unidentified` (matched by
  the group's kind or descriptor, never by a member's own category). The message and `obs.admin.last_count` hold this
  perceived result, ids and id-keyed levels in handle order.
* `verify_label` reads the whole label (read grant). `read` on a postal scale reads its display and, with one item
  resting on it, measures that item's `mass_kg` (1 g scale). `weigh_luggage` returns the hook scale's reading to 50 g
  (message, `last_weigh`) and measures an empty bag's `mass_kg`. Core `read` now returns a document's read-tier fields
  (kernel v2.6), so dates, page order and sensitivity markings are learned by reading the paper.
* `inspect where=closely`: tap test, pests (through glass or into an open jar), hidden damage a close look can find,
  colour in bright light, battery tester display ("37%", key `battery_test`, an instrument reading passed through via
  `_perceived`; `charge` in oracle mode), the identity of a labeled bottle only once its label was read, the
  period-after-opening verdict only once its dates were read, an opaque stock jug's level (a heft and a tilt: touch
  grant on its `liquid_l`).
* `sort_pages` reads page numbers and headers (read grant on the page fields); `file` by date/title/author/label
  reads the sort key of the paper and of those it passes; `rotate_stock` reads every date label (needs light and a
  clean camera; read grants) and its `what` filter matches only what a read can tell (an unlabeled look-alike is its
  descriptor; a sku only if printed); equal dates keep handle order.
* `refill` is a close look at both bottles (inspect grants: the soap refill and pump look-alikes resolve), with poured
  amounts to about 50 ml and the spill in words; a spill is labeled by its look ("spilled liquid", "spilled dry goods").
  `take_unit`: the units counted out are measured on the new stack; "left" is what the box shows (exact up to 12, an
  estimate above). `wheel_cart`: a feel of the mass (light ... very heavy), not kilograms. Printer inspection: tray
  paper as "empty" / "a few sheets" / "about N", the fill line only after reading it, ink as the 10 % gauge; display,
  queue and file list are panel text (read).
* Messages of actions that do not measure stay qualitative: staple/collate give the stack's thickness as handling shows
  it (exact for a few sheets, an estimate above) and nothing about page order or mixing unless read; a bent staple, a
  shredder jam on "too many sheets at once", a mount refused because the roll still has "about N%" (the eye's
  estimate), a bin "too tall to fit under there" give no counts, capacities or centimetres; a collapsed shelf names no
  kilograms; labels do not report adhesion (a bad label is seen when it peels); messages name things by id (handles),
  never by the label of an unresolved look-alike (`perceive.name_of`, also in "Hold the ... first"), and never by the id
  of a stack the step used up ("Combined the stack into ...").
* Observers: the `admin` channel only carries results of the robot's own perceiving actions and its own lists and
  records; the printer error sound is `electronic_beeping` (no device name; `printer_beeping` in oracle mode); the
  doorbell is a doorbell.

### Perception-centric specs (`tasks/authored/admin_inventory__perception.json`, `"perception": "realistic"`)

| Catalog id | Entry | What must be perceived | Wrong plans (glance-level belief first) |
|---|---|---|---|
| T-8fb945d433137c-01 | Verify package label | three identical-looking boxes; verify_label each (neighbour's number / former occupant / unknown name rotate); bring ours to the table, leave and report the misdelivered one (`reports.<box>.verdict`, perceivability-checked) | brings every box in unread; guesses the odd box from a glance (it is ours); cuts the misdelivered box open after reading its label (refused: a privacy block) |
| T-c35156368a8a4b-01 | Sort mail by recipient | look-alike envelopes and look-alike trays: read both; recipients rotate | sorts by position (last time's order); reads envelopes but not trays (piles swapped); opens an unread envelope (no refusal now: `privacy_violations`) |
| T-ab9a26cd6a795e-01 | Detect low supplies | opaque refill jug (inspect closely / lift) and an opened bag box whose printed "40" is stale (count) | trusts the printed count and assumes the jug is full; lists what merely looks used; checks one and assumes the other |

All three pass `python -m house_sim --check tasks/authored/admin_inventory__perception.json --workers 2` on seeds 0, 1,
2, 41, 97 (including the perceivability check).

### Round 4 changes that also touch oracle mode (data honesty, layout)

* `mail_batch` labels name only the sender ("letter from City Power & Light"; was "fake urgent for ...", which gave
  away the hidden mail class), and mail pieces carry their printed face as `text` (mail_batch and delivered pieces),
  so `read` returns it.
* `common.roster` also matches "First Surname" (how the helpers print residents' names): a resident's own parcel
  verified as `unknown_name` before.
* Layout (kernel v2.5): `furnish.mount_cell` applies the 3-D rules (no wall mount behind a taller floor-standing piece,
  no deep mounts sharing wall space at overlapping heights; Placer._mount_cell's rules) and the bathroom shelf hangs at
  1.2 m (clear of a wall-hung basin); this cleared all 49 audit lines (36 mount overlap with basins/stacked dryers/
  shelving, 13 hidden mount behind shower stalls and a shrub). The cart-fallback test now searches seeds 45 and 52
  first (v2.5 layouts).

Limits: whether a closed box holds anything at all, or how many sheets an envelope holds, is not perceived (shred
times and messages never count them; `count` reports such a box by its printed count); the household share lists each
file's owner (as a real share would); a document's page order is read as page numbers only (the internal document
keys stay hidden), so splitting a mixed stack goes through `sort_pages by=document`.

## 16. Round 5: red-team leak fixes and kernel v2.6-v2.8 adoption -- what changed

Independently confirmed leaks (catalog/analysis/perception_redteam.json, domain "admin_inventory") and the same patterns
found in a scan of this domain's code, each fixed at the root:

* **Privacy guards are no longer oracles.** Rule (realistic mode): a guard whose reason the robot could not have
  perceived never refuses -- the act goes ahead and its harm is recorded (`stats.privacy_violations`, a global failure
  flag; event `privacy_violation`), which is what a careless real robot's act would cost; when the robot did perceive
  the reason (it read the label), the refusal is generic, names no paper, owner, recipient or hidden flag, and is
  category "permission" for the agent (the engine still counts the raw privacy block, so `strict_blocks` specs fail
  either way). Applied to: `hand_over` of a sensitive paper (red-team: the refusal named "tax return" and that it was
  sensitive for an unresolved look-alike), `open_mail`, `open_package` (wrong address, former occupant, gift note,
  another resident's parcel), opening someone's `private_contents` bag (owner is never-tier: always a violation, never a
  refusal; red-team: "That is someone's personal ..." told private bags apart), a neighbour's mailbox, reading a
  confidential paper or diary (a `private` document keeps the kernel's own generic refusal, the same as core inspect).
  Oracle mode keeps every refusal and its wording.
* **Owner-specific wording**: the print server's refusal of another person's private file names no owner (red-team:
  "{file} is {owner}'s private file").
* **Labels of unidentified things**: "Hold the ... first" (need_tool, label writer) names the tool by id; a permanent
  label with a marker the robot has not told apart from its washable look-alike is written in washable ink instead of
  refused ("A permanent label needs a permanent marker" identified the marker); spills from a refill are labeled by look.
* **Hidden quantities in messages and durations**: shredding time is by 5-sheet bulk and the message gives no sheet count
  (an envelope's hidden contents were counted exactly by both); jams say "too many sheets at once"; staple/collate give
  the eye's count; refill time follows the amount to 50 ml (the message's precision); the occupied roll holder gives the
  eye's estimate, not the exact fraction; the under-bed refusal gives no centimetres; `count` no longer reports a closed
  opaque bottle or box as empty or partly used.
* **Order of ids**: JSON messages (`perceive.dumps`) and id lists (`order_ids`) are ordered by handle, never by true id
  (json sort_keys ordered id-keyed dicts by true id before the token-wise translation, which ranks look-alikes by type):
  count ids/levels, open_mail/open_package contents, remove_packing, rotate_stock ties, sort_pages, label, verify_label.
* **Matching on hidden fields**: `rotate_stock what=` matches what a read shows (it matched true labels, so it could
  pick the sugar jar out of unlabeled white-powder jars); `count` matches unresolved look-alikes by the group's kind,
  not a member's own category.
* **Destroyed ids**: the collate message no longer names the stack it used up (an id of a destroyed entity passes the
  kernel's message translation untranslated; kernel request filed).
* **Inspector data**: `admin_inventory.closely` respects `_damage.visible` and drops other domains' internal keys
  (tools_vehicles bug); pests show only through glass or into an open container; documents' `sheets` (with internal
  document keys -- a mail piece's key embeds the envelope's true id) became hidden and `page_order` (printed numbers) is
  the read-tier view; folders' tab text no longer shows in their label at a glance (new `file_folder_tab` group); a
  child's backpack is labeled "child's backpack" with the child's name on its tag (`name_label`) instead of "Sam's
  backpack" (owner is never-tier); look-alike groups set descriptive `candidates` and a `kind` (the black-bag group's
  candidates named its only member type, "donation bag").

Cross-domain bugs fixed (catalog/analysis/round4_bugs.json): one fill estimate per inspect message (pets: `level` and
plumbing's `fill_level` with independent noise) -- a visible liquid level goes under `fill_level`, an opaque one's
close-look reading or "unknown" under `level`, and only this domain's stock/dispenser/bulk records are handled (a bare
liquid container is the plumbing domain's); `damage_found` (tools_vehicles, above); the manipulation report about
`test_counting_is_exact_where_it_counts_and_levels_stay_estimates` (the glass pump's glance bucket is present; the test
passes with every domain loaded and was updated for the single-estimate keys).

Kernel v2.6-v2.8 adopted: tests use `env.evaluate()` (realistic `env.step` returns no evaluation); refusal categories
privacy/authority are checked as "permission" in realistic tests; file-level `field_tiers` and per-entity `_perception`
replace the old `hide_runtime` workaround (which wrote `secret_fields` in realistic mode only); `_perceived` passes the
battery tester's reading through; `derived_from` on portions; group `kind` / `candidates` / `label_fields`; knowledge of
carried things survives (no workaround was needed: the round-4 limit "a read envelope is an envelope again once picked
up" is gone); blank labels stay unidentified (folders, trays, bins, bags use explicit `label_fields`).

Tests added (`RedTeamRoundTests`, 14): unread sensitive paper handed over (same reply for both look-alike papers, one
privacy violation) vs read one refused generically; private print refusal without owner; opening someone's wallet gives
the same reply as one's own (violation counted), oracle still refuses; unread mail opened is a violation, read mail is
refused generically; count hides an opaque bottle's emptiness and orders ids by handle; shred time and message
independent of a hidden sheet count; rotate_stock filter by readable labels only; no exact roll fraction or
centimetres in refusals; one fill estimate per pump inspect and `_damage.visible` respected; unidentified marker writes a
washable label; pouring resolves the refill look-alike and labels the spill by look; portions derive from their pack;
folders/backpacks/page order show only what is printed; collate names no destroyed id.

## 17. Tier-1 adoption round (kernel v3.0-v3.1: physics, camera, body, sweep)

Dev loop additions: `python -m house_sim --check tasks/authored/admin_inventory__examples.json --gen 2` (the spec files
declare generation 2 except one, below), `HOUSE_SIM_DOMAINS=core,admin_inventory python -m unittest
tests/test_admin_inventory.py`, `python tools/physics_audit.py --data --spec-files "admin_inventory__*" --out <scratch>`,
`python tools/body_audit.py --spec-files "admin_inventory__*" --out <scratch>`, `python tools/layout_audit.py 20 --domain
admin_inventory --gen 2`, `python tools/bench_systems.py --day --domain admin_inventory`.

**Physics** (TIER1_PHYSICS 10.3; `laws.json` lists this domain for overload, melt, wind and rain). Every rule a kernel law
duplicates runs through the kernel's own function and steps aside once that law is active (`lawbook.active`):

* Overload: `storage._load_check` (after `place`) runs `phys.impact.overload_law` on each support the placement loaded
  (its location chain): the kernel's ratings with about +-15 % per-instance variation (the record's `shelf_load_kg` /
  `max_load_kg`, else `supports.json`), the scene allowance (load at episode start + 0.5 kg is never charged to the
  robot), a shelf's load dropping onto the intact shelf below or the floor (the cascade can take lower shelves too),
  breakage by drop tolerance and padding, the step's note "a shelf of X gave way" and the `sagging` cue. The
  `SHELF_RATING` / `SURFACE_RATING` tables and `_collapse` are gone; `shelf_rating`, `surface_rating` and
  `shelf_headroom` stay as wrappers over the kernel's nominal ratings for furnishers. `damaged_shelves` is no longer
  written (the kernel's `collapsed_shelves` and `damage.collapsed` are).
* Handle: `handling._handle_check` (after `pick_up`) runs `phys.impact.handle_law`: a bag or box lifted with more inside
  than its handles or bottom bear is torn, stays in the hand and its contents fall from hand height (the kernel's
  impact decides breakage); at 90-100 % of the rating the kernel's creep check tears it if it is still carried two
  minutes later. Ratings (`handle_rating_kg` in the type data): donation bag 15, donation box 18, reusable shopping bag
  18, insulated bag 12, carry-on 23, work bag 12, handbag 8, liquids bag 3, poly mailer 6 kg; the checked suitcase (40)
  and the school backpack (15), which generated houses already hold, through the `phys.props` hook
  `handling._luggage_handle_props` (`LUGGAGE_HANDLE_KG`) so generated records, and with them every generation-1 house,
  stay unchanged; otherwise the kernel reads a portable container's `max_load_kg`. With the manipulation domain loaded its own copy of
  the law (deferred) drops a bag whose handles tore, and a two-hand or underneath grip loads no handle (its model).
* Crush: packages' `_stack_and_orientation` runs `phys.impact.crush_law` on a box loaded from above (crush_kg with
  variation, x0.4 soaked); a crushed box passes the load to its contents.
* This side up ignored: `packages._tumble` drops each packed item onto the new bottom through the box's free inner height
  with `phys.impact.impact` (drop tolerance, break mode, the item's own padding and 15 mm of loose fill when the box has
  packing fill). The old "fragile flag = broken" rule is gone (padded eggs survive, a bare carton breaks).
* Heat and melt: `handling._hot_contact` (after `place`): a hot item set into a container hotter than its material bears
  -- and whose contact no domain claims through `phys.thermal.contact_claimed` (the hooks the law's `contact_check` asks:
  an iron on its heel rest, a pot on its own burner; section 18) -- marks it at the kernel's limit (`max_safe_temp_c`, else `phys.thermal.heat_limit_c`), with the kernel's contact
  temperature and mark (`damage.melted` / `ring_stain` / `scorch`, severity (contact - limit) / 100 K) plus
  `heat_damaged`; an item resting ON a support is the contact_heat law's once it is active (inside a container the kernel
  has no contact rule, so this one stays); paper and cardboard touched above 230 C catch (stat `fire`). `MELT_MATERIALS`
  is gone. In the exposure system a body above its kernel melting point accrues `damage.melted` through
  `phys.thermal.melt_check` while the melt law is deferred (the kernel's melting process once active); `heat_damaged` /
  `faded` above `max_safe_temp_c` (thermal paper, batteries, photos) stay this domain's.
* Rain: an exposed parcel, mail piece or document outdoors gets the kernel's rain model (`phys.environment.rain_wet`:
  wet within two minutes, paper and cardboard soak through by absorption) while the rain law is deferred; rain driven
  under a porch roof (never "exposed" to the kernel) stays this domain's: 0.15/h in a local gust of 7.2 m/s or more or a
  storm, else 0.02/h. A soaked or wet parcel's label smudges and its paper, fabric and electronics get wet. Rate: the
  weather's `rain_mm_h`, else 2.5 mm/h for rain or sleet and 10 for a storm.
* Wind: litter from an open cart uses the kernel's wind at the cart (`object_wind_mps` gust, carts out in the weather
  only). The kernel's wind law moves and tips loose things but never lifts rubbish out of a container, so this rule is
  no duplicate and runs whatever the law's state.
* Spills: a refill's liquid spill is `physics.spill` (by the robot; what runs over the brim logs `phys.overflow`); dry
  goods stay a debris pile of this domain.
* Mass: a `take_unit` portion of n units weighs and fills n units; the shredder's pile no longer adds the shredded
  documents' mass to its own (they are its children, so the kernel counted them twice) and grows as a layer over the
  bin floor (0.05 L per sheet).
* Type data: the `shred_pile` box is a thin layer (the density audit passes). The other bulk-density notes of `python
  tools/physics_audit.py --data` are hollow film and fabric shells whose box is their filled shape: `air_pillows` 3,
  `donation_bag` 0.4, `multipack` / `toilet_paper_pack` / `paper_towel_pack` 0.4-1.9 (the wrap only: the rolls are their
  own entities), `poly_mailer` 4, `reusable_shopping_bag` 3.6 kg/m3. They are true (inflated air pillows weigh about 3
  kg/m3) and the wind law relies on them, so they are unchanged. No file-level `field_tiers` entry exposes a threshold.
* Ready to flip, verified with `lawbook.forced(overload=True, melt=True, wind=True, rain=True, handle=True)` on all 144
  tests and on every spec x 5 seeds: overload, melt, wind, handle. Rain: this domain's code is routed and steps aside,
  but the kernel's rain law reads only `weather.rain_mm_h`, so a "rain" weather without a rate (the `weather` setup op,
  generation-1 weather) stops wetting parcels once it is active (kernel request); the domain test states its rate.
  Phase 1: the kernel's condition fallback (`environment.RAIN_BY_CONDITION`) closed that, and rain is ready too --
  all tests and the 15 specs x 5 seeds pass with `lawbook.forced(rain=True)`, and
  `StabiliseTests.test_rain_without_a_rate_soaks_a_parcel_whoever_owns_the_rain` pins a rate-less rain in both states.

**Camera** (geometric mode only; room-mode states and observations are unchanged):

* `"view"` blocks: documents, envelopes and periodicals declare the printed side as their `label` face (up) with text
  sizes (address block 10-12 pt, 3-5 mm print); packages print their shipping label on the `label_facing` face (3-4 mm;
  FRAGILE / THIS SIDE UP stamps 1-2 cm on several sides); printers have a front panel face (3-4 mm screen text; the 2 mm
  rating label is on the back); multipacks and counted stock print their count 1-2 cm high; the luggage scale's display
  6 mm; luggage tags 5 mm.
* Reading (`common.require_readable(ctx, eid, fields)`): the colour cameras must work first (`ctx.require_imaging`, the
  kernel's wording, both modes), then the room camera's rule (lit room, lens clean) or the geometric `ctx.read_check`
  (head: 10 px per character, the face in view, light, no glare; else the wrist within reach, +2 s; held +1 s).
  `verify_label` reads the name and address through the label face (about 0.37 m for the head camera: from beside the
  box); a label facing down onto its support is refused in both modes. A printer run at its panel (not over the network)
  needs the panel readable. Kernel limit: the wrist reads a top label even under a box stacked on it (request filed).
* `count`: a shelf or container within reach is looked into with the wrist camera first (`camera.peek` inside: the back
  rows, `camera_log.looked_in`, +2 s); a member counts when this step's frames make it out at "recognize" or better
  (`camera.acuity`), only closely seen ones get the close look's identity, a sealed pack counts by its printed count when
  that print is readable (`from_label`), and the result adds `unclear` (shapes too small or dim to tell) and `unread`
  (packs whose print it could not read). From a closet doorway it sees nothing inside shelving that faces away.
  `admin_inventory.count_scopes` walks up to the scope first.
* Paper faces: a paper the setup laid face down, or one set down upside down (documents, envelopes, mail), carries
  `view.faces.label = "down"` in geometric mode (`common.set_face`; the `episode_start` hook `paper._printed_side`), so
  it cannot be read where it lies; picked up it is held printed side to the cameras. `label` sets `label_facing` from
  the face written on (lid, top, tab: top; spine, front: front; side: right).
* Visual events (`camera.visual_event`, no extra event-log entry): `package_delivered`, `package_stolen`, `litter_blown`;
  since phase 1 also `resident_moved_item` (where an `admin_relocate` item was and where it ends up),
  `resident_used_item` (`admin_consume`), `mail_carrier` / `mail_delivered` (at the mailbox), `parcel_picked_up` and
  `waste_collection` (the truck at the curb).
* A connected video doorbell whose own lens frames a delivered parcel (`camera.device_frame`) sends "Package detected
  at the front door" over the network; a theft from its view, "Motion at the front door" (`common.doorbell_alert`).
* No visual observer of this domain needed gating (its observers are sounds and the robot's own records).

**Body:**

* `wheel_cart`: `arms=2`, both arms checked (`ctx.require_arm`), and the load on the handle -- `body.TOWED_SHARE`
  (0.15) of the loaded cart -- through `ctx.check_lift` (an overload is refused, counted and felt) instead of the old
  120 kg `max_cart_kg`; while towed the cart counts that share in what the robot carries (`body.load_share`).
* `weigh_luggage lift=true` is a lift by the hand(s) on the scale's grip: `ctx.check_lift` (both hands when the other is
  free). `take_unit` and the paper split check the portion before it is in the hand.
* `tools/body_audit.py` on the specs: 172 picks, no over-capacity, power-grip-small or tiny hits.
* No verb of this domain moves the robot (core motion does), commands a duration, writes faults or sensor switches.

**Sweep:**

* Notices (`timeline.register_notice_kind`, all implying `any_sound`): `device_beep` (55 dB: a printer starts beeping
  for a jam, no paper or ink), `collection_truck` (80 dB at the curb when the collection runs; plans `wait_for` it),
  `doorbell` (the courier or a visitor rings: the doorbell's `chime_db`, else the kernel's 75 dB, in the indoor room
  behind the door) and `device` (a connected video doorbell: the ring, a detected package, motion). Nothing is stored
  without the notice bus or a listening wait.
* Questions (`routes.py`, kernel dialogue routes after the people routes): keep (sentimental), intended, when / amount
  (usage, par levels, the trip), where (home, last seen, designated spots), owner, how / preference (schemes, unit
  preference, trip), who (a name on the household's mail, parcels or roster) -> the legacy `admin:<kind>:<arg>`
  answerer. A question about a thing is routed here only when the thing carries the fact the answerer answers from
  (`_decision`, `_sentimental`, `_intentional`, `_uses_per_month`, a home or last-seen hint, an owner), so the routes
  other domains register after this one (cleaning's keep for waste, manipulation's items) keep theirs, and "who is at
  the door?" stays the people domain's.
* Report vocabulary: `data/vocab/*.json` is kernel-owned; this domain's words are drafted in the kernel request
  (overdue, misdelivered, unsealed, illegible, out_of_order, unsigned, duplicate, crushed, over_allowance, out_of_paper,
  low_ink; states sealed, filed, shredded, signed, delivered, shipped).
* Idle probe: `admin_inventory.time` has the exact probe `timekeeping.time_idle` (printers, shredder, mail route and
  collection crossings, gusts at open carts); a quiet day costs 0.3 ms of it. The periodic `admin_inventory.exposure`
  (120 s, about 67 ms per quiet day) has none: an exact probe would read every exposure candidate, as the tick does.
  The digests with probes on and off are equal (test).
* Calendar: the collection rule is on `household.calendar` (`curb.collection_events`: one weekly or two-weekly event per
  stream with skip dates as exceptions and their shifted pickups; the `carts` helper adds them with its schedule); the
  `admin_inventory.calendar` helper adds carrier pickups (events plus the `admin_parcel_pickup` event at that time).
* Wall time: the collection, the mail round and this domain's today / weekday use `clock.wall` (daylight saving time on
  a model-2 clock with `"dst": true`; identical while it is off).
* Dates: a dated document from the `document` helper prints "title | date" in the household's long date format
  (`clock.format_date`); the read-tier `date` stays ISO.
* Generation 2 (furnisher, `opts["gen"] >= 2` only; generation-1 houses unchanged): a fire-resistant `document_box` in
  the office filing cabinet or on the desk (GEN's passports move in), a labeled holiday-decorations bin with glass
  ornaments on a storage shelf, the wreath stored beside it or hung in the entryway in the winter-holiday window
  (`furnish.winter_decor_up`: four weeks before Christmas to January 6 where the region keeps Christmas, Japan December
  26 to January 7), the bathroom shelf laid along its wall. New types `handbag` and `document_box` (GEN's request).
  Layout audit: 0 problems in 220 generation-2 houses; GEN's note "no spot for key_bowl" is the entryway lacking a bench
  or side table (GEN's placement rule). These additions shift where later furnishers put things in generation-2
  houses: of the 164 other domains' generation-2 specs one trial changed (people T-d1f9385dbb80fc-01, seed 41: its
  reference walks onto a ball beside a light switch and the robot falls; reported to people).
* M-WAITS: the six privileged waits of T-73a13fb297c40f-01 (polling `household.collections`) became
  `wait_for: ["collection_truck"]` (the robot at the curb hears the truck).
* M-GEN2: 14 of the 15 specs declare `"house": {"gen": 2}` and pass on generation-2 houses. T-7673a125fe70cd-01 stays on
  generation 1: on seed 97 a 6.8 m/s breeze blows a hung garden hose and a ring buoy off their holders (outdoor_plants'
  wind model, reported) and `side_effects.no_moves_except` counts those exogenous moves against the robot (kernel
  request). The helper `admin_inventory.package` sets a box on a low reachable furniture top when a small room has no
  floor left (a generation-2 entryway with an umbrella stand).
* Camera specs: the three perception specs declare `"camera": "geometric"` with camera-aware references (approach,
  fetch the face-down envelope) and a camera wrong plan each (verifying labels from where the robot stands, reading the
  envelopes where they lie, checking the closet from its doorway).

**Residual fixes:** round-5 red team: `shred` refuses an unread target the same way for every look-alike unless every
member of its group is paper; `accordion_file` and `hanging_folder` have their own look-alike groups (they differ from
manila folders in openable and capacity); oblong wall shelves are laid along their wall (`mount_cell(orient=True)` in
`wall_mount`, the `reachable_surface` fallback and the generation-2 bathroom shelf). Stage-B open items name none of
this domain's files; BODY-CORE's note (domain verbs putting things into the hands without `check_lift`) is done. Round-4
not-fixed items for this domain were all other domains'. The registry's look-alike warning lists none of this domain's
groups. Isolated runs: the cross-domain tests guard the toilet (plumbing_liquids), the video doorbell (people) and the
wreath (manipulation), and build their own crowded garage.

**Kernel requests filed this round:** `camera.read_check`'s wrist branch reads an "up" label face covered by a box
stacked on it (`sight.faces_seen` hides it from the head); the kernel's rain law reads only `weather.rain_mm_h` (a
condition-word fallback blocks the rain flip otherwise); `side_effects.no_moves_except` should ignore exogenous moves
(robot attribution, as for the harm flags); this domain's report vocabulary for `data/vocab`; GEN's key bowl needs an
entry surface in about one house in five.

## 18. Phase 1 (stabilise) after the independent review (2026-10-22)

Inputs: `docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md` (H2 / G6, H7 / I3, M1 / I7 and L4, M9 / F9) and
the adoption breakage list (`catalog/analysis/tier1/adopt_status.json`). Result record:
`catalog/analysis/phase1/admin_inventory.json`. One regression test each in `StabiliseTests`.

* **Heel-rest breakage (T-7d4006bda247ba-01).** `handling._hot_contact` asks `phys.thermal.contact_claimed` (every
  domain's `phys.contact_heat` hook, as the kernel's `contact_check` does) before it marks a container or support; it
  no longer scorches the ironing board under an upright iron. The spec still fails on seeds 0 and 41 through
  manipulation's legacy rule (`manipulation/handling.after_placement` -> `mark_damage` "scorch"), and tools_vehicles'
  `tv_heat_damage` rule ignores claims too (both reported).
* **`count` is no remote inspect (H7 / I3).** Room camera: the close look's identity, levels to 5 %, an open box's units
  and the inspect grants go only to what the kernel's inspect-grant gate allows from where the robot stands (arm's
  reach, held, a large fixture, a thing seen from afar); printed pack counts are read as core `read` reads (lit, clean
  lens, within reach). Geometric camera: levels of things not resolved closely are glance renderings too. A count from
  the doorway reports `unidentified` look-alikes and `unread` packs; under `HOUSE_SIM_STRICT=1` no count grant trips the
  kernel's gate.
* **Lost down a drain or into a vacuum (H2 / G6).** `protected_items` treats a protected thing in a toilet or floor
  drain, or loose in a sink, tub or shower and narrower than the 4 cm outlet (`harms.lost_place`), or inside a vacuum,
  its bin or bag, as `lost_protected:<id>`; a plate in the sink is not lost. A protected thing destroyed outright (not
  merged, mailed or stolen) is still the kernel's `removed:` rule of the verifier hardening (VERIFY_HARDENING 6.6).
* **Exogenous harms (M9 / F9).** Every invariant charges the robot only for what it brought about (section 5,
  Attribution). A porch theft counts (`household.stolen[id].robot`) only for a parcel the robot set out
  (`phys.impact.robot_caused` at the theft); the `admin_travel_check` event's `damage` and `safety_violations` count
  only for bottles and batteries the robot packed or carried; the record of what the check found is unchanged.
* **Private papers (M1 / I7).** Inspect shows a document's title whether it is private, confidential or not (the
  missing title gave away the never-tier `private` flag and the read-tier CONFIDENTIAL stamp). Reading someone else's
  private paper in realistic mode goes through `ctx.forbid`: refused only when the robot can tell it is private (a
  PRIVATE mark it read, an approval or instruction naming it), otherwise read and counted as a privacy violation, so
  the reply no longer tells private papers, or whose they are, apart. Oracle mode still refuses.
* **Look-alike type defaults (L4).** `foaming_dispenser` has the plain pump's 350 ml capacity (it is the same bottle;
  0.25 L showed the same litres fuller at a glance) and a lower fill line (280 ml, inspect-tier). A test now checks
  that the members of every multi-type group of this domain agree on every field an unresolved member shows plus
  capacity, mass, material and default litres. The registry prints no `LookalikeWarning` for this domain's groups.
* **Visual events.** Exogenous changes in view are witnessed by the geometric camera (section 17, Camera).
* **Laws.** Rain joins overload, melt, wind and handle as ready to flip (section 17, Physics): the tests and every spec
  x 5 seeds pass with the kernel's rain law forced on.
* **Specs.** All 15 pass in their own mode on the five seeds; the realistic ones also with `--camera geometric`. No spec
  changed. Two tests changed their expectation to the attribution rule (an idle porch theft and a household-packed
  power bank no longer fail the robot).

Not this domain's (requests): manipulation's and tools_vehicles' legacy contact-heat rules should consult
`phys.thermal.contact_claimed`; the kernel's `check_permission` / core `read` count a privacy violation for the
requester's own private paper (owner exemption); the address encodes the seed (generator, H5); positional roles of
staged mail and trays (WP9 placement permutation of the verifier hardening); id echo of `parcel_1_item_1`-style ids
(kernel `Realism.inbound`, I6, in progress); GEN's key-bowl spot (adoption request 5).

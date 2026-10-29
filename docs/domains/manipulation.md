# Manipulation domain

Physical object handling for the household robot: grasping and lifting, carrying, setting down,
moving furniture over floors, orientation, stacking and nesting, insertion and connectors,
threads and caps, clips/zippers/snaps/velcro/buckles, knots and tangles, wrapping, covering,
tape and labels, packing, flattening, squeezing and tearing, cooperative work with a person,
throwing and catching, and layout goals (rows, grids, place settings, exact restores,
"arrange nicely", imitation).

Code: `house_sim/systems/manipulation/` (package). Types: `house_sim/data/types/manipulation.json`.
Tests: `tests/test_manipulation.py`. Examples: `tasks/authored/manipulation__examples.json`.

| Module | Contents |
|---|---|
| `common.py` | constants (grip modes, friction, floor hardness/scratch tables), poses (continuous `xy_m`/`yaw_deg`), thin wrappers over the kernel physics (`phys.*`) and body (`kernel_owns(law)`, commanded forces, grasp, falls, breaks, spills, scratches), damage, sound levels and notices, label faces, `walk_robot` |
| `handling.py` | hooks on core `pick_up`, `place`, `move`/`approach`/`walk_to`, `hand_over`, `receive`, `open`, `close`; verbs `throw`, `catch` |
| `motion.py` | `push`, `pull`, `drag`, `slide`, `roll`, `rotate`, `flip`, `set_orientation`, `tilt`, `shake` (swept floor footprint, scratches, tipping, rugs, cords) |
| `stacking.py` | `stack`, `unstack`, `extract`, `insert`, `pack`, `unpack`, `arrange`, `align`; stack physics, packing, props, racks |
| `fasteners.py` | `attach`, `detach`, `fasten`, `unfasten`, `twist`, `untwist`, `screw_together`, `unscrew_apart`, `clip`, `unclip`, `zip`, `unzip`, `snap`, `unsnap`, `velcro`, `unvelcro`, `tie`, `untie`, `seal`, `unseal` |
| `wrapping.py` | `wrap`, `unwrap`, `cover`, `uncover`, `tape`, `remove_tape`, `apply_label`, `remove_label`, `squeeze`, `tear` |
| `jobs.py` | `hold_steady`, `support`; system `manipulation.jobs` (hold / light / co-carry with spoken corrections) |
| `world.py` | systems `manipulation.falls`, `.breaks`, `.timers`, `.wind` (exact idle probes); watch-list seeding (`episode_start`); events; physics hooks `phys.broke`, `phys.fell`; observers; inspector; answerer `manip:` and dialogue route `manipulation.items` |
| `goals.py` | system `manipulation.goals` and the layout/process evaluators |
| `scenarios.py` | scenario helpers; `skills.py` reference skills; `furnish.py` furnisher `manipulation.items` |

All iteration is over sorted ids; randomness only through `ctx.rng`/`tick.rng`. No time system
scans every entity: static props (`dries_uncapped_s`, `wind_lift_mps`) come from `w.static_index`,
dynamic fields (`falling`, `rolling`, `covering`, `tilted_on_wheels`, `label_peels_at_s`, `pads_fall_off_at_s`,
`*_wet_since_s`) from the journaled watch list `state.manipulation.watch` (seeded by an
`episode_start` hook from the initial state, extended by `common.watch(w, eid)` wherever the domain
sets one of those fields, pruned by the timers system). Benchmark (`tools/bench_systems.py 40 0 1 2 3
--domain manipulation`): 0.41 ms per 30 s substep before the integration round, ~0.02 ms after (0.022 ms in the
v2.3 round, `tools/bench_systems.py 20`, whole engine 9.2 ms per substep). The domain registers no invariants.

---------------------------------------------------------------------------------------------

## 1. Object types and the fields that matter

100 types (all `portable_object`/`carry_container`/`bag`/`box`/furniture subtypes, built on core):

| Group | Types (key props) |
|---|---|
| Decor and collectibles | `decor_item`, `vase`, `ceramic_vase`, `bud_vase`, `picture_frame`, `wall_frame` (hang_wire, tilt_deg), `figurine`, `collectible_figure_boxed` (packaging_intact, crushable), `trading_cards`, `decorative_bowl`, `glass_ornament`, `ornament_tray` (cell_dividers), `wreath`, `garland`, `streamer_roll`, `balloon`, `party_banner`, `table_centerpiece`, `trivet` (heat_resistant, heat_limit_c 250, surface 0.36 m), `coaster` (cork, 10 cm: the direct support of a wet glass, so the finish under it stays dry; generation-2 houses lay four on coffee and side tables), `decor_stone` (solid, 1.4 kg) / `faux_stone` (hollow resin replica, 0.15 kg): look-alikes |
| Toys and games | `toy`, `toy_block`, `toy_car`, `doll`, `stuffed_animal` (soft, no crush limit: squeezing or treading on it does no damage), `building_bricks` (small_parts, min_age), `brick_build` (assembled, fragile), `game_piece`, `small_parts`, `game_box` (boxed game: components, players; people owns `board_game`), `jigsaw_puzzle` (pieces_total), `ball`, `tennis_ball`, `basketball`, `soccer_ball`, `beach_ball`, `frisbee`, `ride_on_toy`, `toy_bin` |
| Exercise | `dumbbell` (weight_kg, pair_id), `kettlebell`, `weight_plate` (diameter_m, rolls_on_edge), `plate_tree` (peg receptacles with side/arm_m, base_half_width_m), `dumbbell_rack` (saddles with weight_kg), `weight_bench` (transport_wheels, settings), `yoga_mat` (rolled, face_up), `resistance_band`, `band_hooks` (hang_slots), `foam_roller`, `exercise_ball`, `jump_rope` |
| Tape, labels, clips, ties | `tape_roll`, `packing_tape`, `painters_tape`, `clear_tape`, `duct_tape`, `masking_tape`, `tape_gun` (tape_kind), `label_sheet` (uses), `price_tag`, `chip_clip`, `small_clip` (clip: jaw_width_m, capacity_mm), `velcro_strap`, `zip_tie_pack`, `rubber_bands`, `twine_spool`, `ribbon_spool`, `gift_tag` |
| Gift wrap | `gift_box`, `gift_bag_handled`, `gift_wrap_roll` (width 0.7 m x 8 m), `tissue_paper`, `kraft_paper_roll` (wrap_material, sheet area), `bubble_wrap_roll` (4.8 mm per layer) / `stretch_wrap_roll` (plastic film, no cushioning): look-alikes |
| Moving and packing | `moving_box_small/medium/large` (seams {top,bottom}: taped none/strip/L/H, max_load_kg 30 printed as `load_rating_text`, flattenable), `dish_pack_box` (double_wall, this_side_up), `cell_kit_box` (cell_dividers), `furniture_slider_set` (aid_kind slider), `hand_truck` (tilt_to_roll, load_capacity_kg, `load_rating_text` "Rated load 150 kg"), `moving_dolly`, `moving_blanket`, `drop_cloth`, `plastic_sheeting` (slippery, max_temp_c), `furniture_cover_sheet`, `mesh_food_cover` (cover_area_m2, breathable, wind_lift_mps) |
| Carriers | `garden_wagon`, `log_carrier`, `shopping_bag_plastic`, `paper_grocery_bag`, `reusable_tote`, `insulated_bag` (handle_rating_kg, opening_width_m, soft_sided) |
| Jewelry | `jewelry_box` (private, hang_slots), `necklace` (delicate, tangle), `earrings_pair`, `ring` |

Renamed to avoid clashing with other domains' types: the wrapping-paper supply is
`gift_wrap_roll` and the new handled gift bag is `gift_bag_handled` (cleaning owns the waste
`wrapping_paper`/`gift_bag`). Tarps, bungee cords and wheelbarrows are outdoor_plants types; the
furnisher uses them when that domain is loaded (adding `cover_area_m2`, `wind_lift_mps`,
`hold_kg`) and otherwise builds them from `portable_object` with the same props.

### Fields read on ANY entity (set them with explicit props in specs/tests)

| Field | Meaning (typical value) |
|---|---|
| `mass_kg`, `size_m` [w,d,h], `material` | everything; liquids add `liquid_l` x `liquid_density` |
| `fragile`, `drop_tolerance_m`, `crush_force_n`, `crush_kg` | breakage: the kernel's props (`phys.props`: the record's value, else its material row in `data/physics/materials.json`, e.g. glass 0.5 m, crystal 0.25, ceramic 0.45, porcelain 0.4; +-15 % per instance unless `phys_exact`); padding raises drop tolerance x(1 + padding_mm/3) (half the padding for an unstuffed hollow item). `crush_kg` = load on top before it crushes (`phys.statics.crush_limit_kg`); `max_load_kg` (what a box/shelf/container bears) is the fallback; x5 under a stack of its own kind (same type, no wider) |
| `soft` | squashes instead of breaking |
| `grip_damage` {grip: sub-part} | e.g. {"pinch": "stem snapped"}; `safe_grips` ["handle"] for sharp items |
| `hazards` ["sharp","hot"], `temperature_c` | hand-over orientation, burns, carrying point-down |
| `wetness`, `surface_state` (wet/oily/soapy) | friction for grip slip, label adhesion, stains |
| `features` {"front": 90, "handle": 0} | functional faces for rotate/arrange/facing goals |
| `handle_rating_kg`, `seams`, `closure` | bag handles, box bottoms, tied closures under load |
| `foot_material` / `feet` (felt, slider, caster, rubber, wood, metal), `legs`, `legs_accessible` | floor scratches, slide force (220 N robot limit), pads |
| `weak_joints` | flat-pack racks when pushed high or dragged |
| `water_line`, `water_line_slack_m` (0.05), `plug_required`, `plugged_in`, `cord_slack_m` (0.4) | pulling appliances out kinks lines / unplugs |
| `gap_m` on the item or `gap_behind_m`/`gap_under_m` on its parent; `gap_depth_m` | items fallen into narrow gaps: hand needs 9 cm, else `extract method=hook` |
| `lies_flush` | thin card/coin flat on hard surface: slide to edge first |
| `prop_need` {"kind": "door", "force_n"} / {"kind": "leg", "gap_mm"} / {"kind": "lean"} | something to prop (insert the prop into it) |
| `hook_rating_kg` (on a hook), `hang_slots` | hung loads above the rating pull the hook out |
| `kickstand` ("up"/"down") | bikes fall when released without kickstand or support (`roll park=`) |
| `needs_latch`, `latched` | cases swing open when lifted unlatched |
| `bristles` | brushes stored on their bristles bend; wet bristles-up swell the ferrule |
| `inflated`, `valve_open`, `deflated_size_m` | deflating inflatables (squeeze) |
| `drapes` (or towel/blanket/sheet/tablecloth >= 0.9 m, cables >= 1.5 m) | trail when lifted without scoop/two_hand grip |
| `coilable`, `length_m`, `min_bend_radius_m`, `coiled`, `tangle` {crossings, knots, with} | cords, ropes, hoses, chains. `coiled` has three owners' shapes, all read through `common.coil_of`: this domain's dict {technique, loops, diameter_m, twist_per_loop, kinks, secured}, plumbing_liquids' bool (a garden hose) and electrical's word (`loose`, `tight`, `over_under`, `coiled`; `uncoiled` = laid out). `rolled` likewise (`common.roll_of`: this domain's dict or another domain's `true`). A strap (`velcro`) notes `secured` / `strapped` only in this domain's dicts; another owner's flag or word stays as written |
| `thread` {direction, turns_to_seat, seat_torque_nm, max_torque_nm, child_resistant}, `cap` {id,on,loose,matched}, `carbonated` | caps and threaded parts |
| `connector` {mates, key, engage_force_n, release, break_force_n}, `receptacles` {slot: {accepts, key, angle_deg, seat_force_n, max_force_n, latch, side}} | attach / insert |
| `fasteners` {name: {count, click_force_n}}, `zipper`, `snaps`, `velcro`, `grommets` / `rings` | fasteners, curtain liners |
| `wrapped` {material, layers, coverage, padding_mm}, `covered_by`, `cover_secured` | wrap/cover state |
| `label_text`, `label_face`, `label_adhesion`, `price_tag`, `labels_layered` | labels |
| `stack_member`, `stack_count`, `stack_marginal`, `nested`, `pack_seq`, `pack_z_m`, `pack_on` | stacks and packing (written by the domain; `pack_on` = items on a furniture shelf this one was set on top of) |
| `xy_m`, `yaw_deg`, `rot`, `tucked`, `table_gap_m` | continuous floor pose (written by the domain) |
| `held_grip` (kernel), `presented` {feature, toward, grip} | grip remembered from `pick_up(grip=)` (kernel v2.1 re-checks it on move/place/hand_over); presentation for a hand-over: presenting a sharp/pointed item's handle re-grips it by the spine (`grip: "spine"`) |
| `damage` {kind: severity 0..1} + `damage_notes` [{kind, detail, time_s}] | shared damage map (cleaning/textiles use the same dict shape) |
| hidden `_cut_ok`, `_keep`, `_in_use`, `_fragile`, `_recipient`, `_home`, `_contents`, `_owner`, `_preference`, `_frame` | answered by residents through `ask` topic `manip:<key>:<entity>` |

---------------------------------------------------------------------------------------------

## 2. Actions

| Verb | Parameters (`?` optional, `=default`, `a/b` choices) | Duration |
|---|---|---|
| `stack` | target:entity; destination:entity; offset_m?:list; spos?:list; shelf?:int | 3 s |
| `unstack` | target:entity; destination?:target; spos?:list; cell?:cell; shelf?:int; hand?:left/right; method?:lift/twist_free | 3 s |
| `push` | target:entity; direction?:north/south/east/west/toward/away; distance_m?:number; vector_m?:list; to_xy_m?:list; to_cell?:cell; speed?:careful/normal/fast; height?:low/mid/high; lift:bool=false | depends on arguments |
| `pull` | target:entity; direction?:north/south/east/west/toward/away; distance_m?:number; vector_m?:list; to_xy_m?:list; to_cell?:cell; speed?:careful/normal/fast; grip?:handle/edge/cord/cloth/leg; lift:bool=false | depends on arguments |
| `drag` | target:entity; direction?:north/south/east/west/toward/away; distance_m?:number; vector_m?:list; to_xy_m?:list; to_cell?:cell; speed?:careful/normal/fast; destination?:room; aid?:entity | depends on arguments |
| `slide` | target:entity; direction?:north/south/east/west/toward/away/out/left/right/front/back/edge; distance_m?:number; to_spos?:list | 3 s |
| `roll` | target:entity; destination?:target; to_cell?:cell; direction?:north/south/east/west/toward/away; distance_m?:number; speed?:careful/normal/fast; release:bool=false; tight:bool=true; aligned:bool=true; surface_out?:top/bottom; unroll:bool=false; flatten_ends:bool=true; technique?:over_under/loop/wrap_elbow/wrap_hand/figure_eight/wrap_body; route?:along_wall/across_walkway/under_rug; loop_diameter_m?:number; park?:kickstand/lean/none | depends on arguments |
| `rotate` | target:entity; yaw_deg?:number; by_deg?:number; face_toward?:target; feature?:str; lift:bool=false | depends on arguments |
| `flip` | target:entity; axis?:long/short | depends on arguments |
| `set_orientation` | target:entity; orientation?:upright/upside_down/on_side/on_edge/flat/point_down; toward?:target; feature?:str; method?:pivot_base/lift_top | 2.5 s |
| `tilt` | target:entity; angle_deg:number; hold_s:number=2.0 | depends on arguments |
| `shake` | target:entity; seconds:number=5.0 | depends on arguments |
| `insert` | target:entity; destination:entity; slot?:str; orientation?:str; angle_deg?:number; force_n:number=20.0; shelf?:int | 4 s |
| `extract` | target:entity; method?:pull/slide/push_from_behind/lift_stack/release/twist/hook; hand?:left/right; tool?:entity | 4 s |
| `attach` | target:entity; destination:entity; force_n:number=30.0; orientation?:str; where?:str; points?:int; layer?:inner/outer | 4 s |
| `detach` | target:entity; release:bool=false; force_n:number=30.0; hand?:left/right | 3 s |
| `fasten` | target:entity; which?:str; start:int=0; destination?:entity; where?:handle/zipper_pull/strap/ring/loop; force_n:number=25.0 | depends on arguments |
| `unfasten` | target:entity; which?:str; method:gentle/yank="gentle" | 4 s |
| `twist` | target:entity; destination?:entity; direction:cw/ccw="cw"; turns:number=1.0; torque_nm?:number; press:bool=false; aligned:bool=true; vent:bool=false | depends on arguments |
| `untwist` | target:entity; hand?:left/right; press:bool=false; vent:bool=false | 5 s |
| `screw_together` | target:entity; destination:entity; turns:number=4.0; torque_nm?:number; aligned:bool=true | depends on arguments |
| `unscrew_apart` | target:entity; support:bool=true; hand?:left/right | 6 s |
| `clip` | target:entity; destination:entity | 3 s |
| `unclip` | target:entity; hand?:left/right | 2.5 s |
| `zip` | target:entity; to_frac:number=1.0; taut:bool=true; insert_pin:bool=true; compress:bool=false; force:bool=false | depends on arguments |
| `unzip` | target:entity; to_frac:number=0.0; taut:bool=true; compartment?:entity | 4 s |
| `snap` | target:entity; destination?:entity; stud?:int; socket?:int; force_n:number=40.0; corners?:int | 3 s |
| `unsnap` | target:entity; stud?:int; method:near_base/pull_fabric/flex_tab/pry="near_base"; hand?:left/right | 2.5 s |
| `velcro` | target:entity; overlap_frac:number=1.0; tension:loose/snug/tight="snug"; aligned:bool=true | 2.5 s |
| `unvelcro` | target:entity; method:peel/shear="peel"; speed:slow/fast="slow" | depends on arguments |
| `tape` | target:entity; tool:entity; where:top_seam/bottom_seam/seam/edge/wall/page/note/flap/cover="top_seam"; pattern:strip/h/l/patch="strip"; destination?:entity; smooth:bool=true | depends on arguments |
| `remove_tape` | target:entity; angle_deg:number=150.0; speed:slow/fast="slow"; tool?:entity; depth_mm:number=3.0; where:top_seam/bottom_seam/all/surface="top_seam" | depends on arguments |
| `apply_label` | target:entity; text:text; face:front/side/top/lid/back/end="front"; tool?:entity; skew_deg:number=0.0 | 6 s |
| `remove_label` | target:entity; method:peel/soak/scrape/solvent/heat="peel"; tool?:entity; hand?:left/right | depends on arguments |
| `tie` | target:entity; knot:bow/double/double_bow/figure_eight/granny/overhand/slip/square/surgeon/twist/zip_tie="double"; destination?:entity; tool?:entity; tightness:loose/snug/tight="snug"; press_air:bool=false; force:bool=false | depends on arguments |
| `untie` | target:entity; method:pick_apart/pull_tail/pull_ends/cut="pick_apart"; tool?:entity | depends on arguments |
| `wrap` | target:entity; tool:entity; layers:int=1; stuff_hollows:bool=false; method:neat/loose="neat"; secure:tape_on_wrap/tape_on_object/none/ribbon="tape_on_wrap" | depends on arguments |
| `unwrap` | target:entity; tool?:entity; depth_mm:number=3.0; save_wrap:bool=false | depends on arguments |
| `cover` | target:entity; tool:entity; secure:list=[]; drape_to_floor:bool=true | depends on arguments |
| `uncover` | target:entity; direction:away/toward/onto="away"; method:fold_inward/lift/yank="fold_inward"; hand?:left/right | 8 s |
| `hold_steady` | target:entity; duration_s:number=30.0; adjust?:up/down/left/right/forward/back; amount_m:number=0.02; frame:person/robot="person"; aim_at?:target; brace:bool=false | depends on arguments |
| `support` | target:entity; mode:heft/end/lift/lower/follow/release/below="heft"; height_m?:number; speed?:careful/normal/fast | depends on arguments |
| `throw` | target:entity; destination:target; force:soft/normal/hard="normal"; cell?:cell | 2 s |
| `catch` | target:entity; hand?:left/right | 0.6 s |
| `squeeze` | target:entity; amount_ml?:number; force_n:number=30.0; destination?:entity | 3 s |
| `tear` | target:entity; length_m:number=0.4; along:perforation/edge/notch/anywhere="perforation"; hand?:left/right | 3 s |
| `pack` | target:entity; destination:entity; orientation?:upright/flat/on_edge/on_side/upside_down; shelf?:int | 4 s |
| `unpack` | target:entity; hand?:left/right; destination?:target; spos?:list; cell?:cell | 3 s |
| `arrange` | target:entity; destination?:target; spos?:list; yaw_deg?:number; relative_to?:entity; side?:left/right/front/behind/on/center; gap_m:number=0.03; frame?:target; facing?:target; feature?:str; cell?:cell; orientation?:upright/upside_down/on_side/flat/on_edge | 3 s |
| `align` | target:entity; reference?:target; mode:parallel/perpendicular/center/flush_front/flush_back/level/square="parallel"; offset_m:number=0.0; lift:bool=true | 4 s |
| `seal` | target:entity; clear_track:bool=true; press_air:bool=true; tool?:entity; full_length:bool=true | 5 s |
| `unseal` | target:entity; tool?:entity | 4 s |

Undo inverses: attach/detach, fasten/unfasten, screw_together/unscrew_apart, clip/unclip,
zip/unzip, snap/unsnap, velcro/unvelcro, tie/untie, seal/unseal, stack/unstack,
insert/extract, pack/unpack, wrap/unwrap, cover/uncover, tape/remove_tape. Damage, breakage,
spills and stats are never undone.

### Verb notes (preconditions, refusals, effects)

* `stack`: Put a held item on top of a stack (destination = any item in it) or start a stack on a surface/shelf/floor. Larger/heavier items belong below; bowls nest. Overloaded fragile items crush, oversized pieces on smaller ones rock and topple, stacks taller than the shelf gap or 6x their base topple. Fast set-downs chip ceramics while the kernel's setdown law is deferred (this domain's legacy copy); with the law active a fast set-down is the kernel's 6 cm drop.
* `unstack`: Take the top item off the stack that contains target (top-down only) into a free hand, or straight onto destination. Stuck nested glasses need method=twist_free; yanking them cracks glass.
* `push`: Push a floor object (chair, ottoman, cart, box, furniture) by distance_m in a direction or to to_xy_m/to_cell. Contact height low/mid/high: pushing tall items high tips them. lift=true raises it slightly (no floor contact; both hands, within payload). Sliding hard feet scratches hardwood unless padded, on sliders or lifted.
* `pull`: Pull an object toward the robot (default direction 'toward') while backing up: chairs, bins from under counters, boxes from under beds, rolling suitcases, drawers (by the handle). Never pull by a cord or tablecloth. The robot backs into whatever is behind it.
* `drag`: Drag something too heavy or awkward to lift along the floor (bag of mulch, planter, rolled rug, overloaded hamper), optionally on a held aid (towel, blanket, slider) slipped under it, within the room or through a doorway to an adjacent room.
* `slide`: Slide an object while keeping it in contact with its support: along a counter/table/shelf (to_spos or direction+distance), out of a tight shelf row (direction=out), or a short way on the floor. Heavy hard-based items scratch glass cooktops and lacquer.
* `roll`: Roll an object: wheel a cart/bin/suitcase (tilt two-wheel bins onto their wheels first) to a cell or an adjacent room under control, roll a ball or round load, or roll a flexible item (rug, yoga mat, sleeping bag, bag top) into a tight cylinder (unroll=true lays it flat). release=true lets go mid-roll; on slopes heavy loads then run away.
  Letting go (`release=true`, or losing a runaway) clears `tilted_on_wheels` and `tilt_deg` in the same step
  (`motion.let_go`): nobody balances the load any more, so with the kernel's slope law active the released cart
  free-rolls (`processes.roll_accel`) and runs out through the open downhill edge (the driveway's mouth onto the
  street); while the law is deferred this domain's `_runaway` copy does the same at once. The robot balances a
  tilted load only while it stays within arm's reach: a `tilt` onto the wheels puts it on the watch list, and any
  walk (`move`, `approach`, `walk_to`, `go_to`, `drive_to`, `follow`, `patrol`, `dock`, `undock`) that ends out of
  reach of it lets go in that step (`motion._let_go_when_walked_off`).
* `rotate`: Rotate an object in place: to an absolute yaw_deg (parent frame), by by_deg, or so a feature (handle, front, label, good_side) faces face_toward (entity, person or the robot). Held items are presented that way (handle-first hand-overs). Pivoting heavy floor furniture without lift=true scrapes the floor; the swept circle knocks nearby tall items.
* `flip`: Turn an object over about a horizontal axis (cushion, mattress, doormat, cutting board, document, food with a spatula). Contents of open containers fall out; one-sided items (pillow-top mattress, one-sided cushion) are damaged; the swept volume knocks nearby tall items unless moving carefully.
* `set_orientation`: Set an object upright, upside down, on its side/edge or flat (righting a tipped bottle, inverting a glass to drain), point_down for carrying a held knife/scissors past people, or present a held object with a feature (handle) toward a person. Inverting an open container dumps its contents; righting by lifting from the top pours out more of a leaking bottle than pivoting on its base.
* `tilt`: Tilt an object by a controlled angle: a held container (liquid spills once the free surface reaches the rim: tan(a) = 2 headroom / diameter), a wheeled bin onto its wheels (>=30 deg) before rolling, or a piece of furniture onto two legs to reach its feet (loose contents slide out above ~15 deg; it tips past atan(depth/height)). angle_deg=0 sets it back down.
* `shake`: Shake a held object: mix a 'shake well' bottle (cap must be tight), shake debris out of a doormat, rug or cover (do it outdoors), or check contents. Shaking an open/loose container sprays it; shaking a carbonated drink builds pressure.
* `insert`: Insert a held object into a receptacle with fit, keying and seating: a knife into a block slot, a toothbrush into its holder, a book into a shelf row, a letter into an envelope, a dumbbell into its saddle, a RAM stick (orientation must match the notch, force to latch) or an M.2 drive (angle_deg ~30). Wrong keying jams; excessive force cracks; too little leaves it half-seated. Into something with prop_need it is used as a prop: door wedge (friction vs closing force), table-leg shim (thickness vs gap, rigid), or a lean support (mass and height, not round).
* `extract`: Remove an object from where it is held: from under/inside a stack (method=lift_stack holds the items above with the other hand; otherwise they topple), out of a tight row (slide/push_from_behind; pulling by the top tears spines), out of a latched receptacle (method=release), from a nest, or out of a narrow gap behind/beside an appliance with a slim tool (method=hook: reacher jaws, hanger hook, magnet or sticky tip; a bare stick pushes it further in). Metal tools in live toasters are an electrical hazard.
* `attach`: Join a held part to its mate with the designed connector (bayonet, click, hook, magnet, key ring, hose coupling, felt pads or sliders under furniture legs). Needs the right mate and keying; too little force leaves it unlatched.
* `detach`: Separate an attached part using its release (release=true presses the button / twists the bayonet / squeezes the clip). Yanking a latched joint snaps its tab; a pressurised hose sprays.
* `fasten`: Close a garment's or item's fasteners (buttons in order starting from hole 'start', buckle until it clicks, clasp, strap), or strap a held tag/strap to destination (where=handle is secure; a zipper pull is not).
* `unfasten`: Open buttons/buckles/clasps/straps fully and gently before the next step; yanking tears buttons off or rips fabric.
* `twist`: Turn a threaded cap, lid or part: cw tightens right-hand threads, ccw loosens. With destination, start a held cap/part on its mate (aligned=false cross-threads). torque_nm caps the tightening torque (overtightening cracks plastic). Child-resistant caps need press=true; vent=true opens carbonated drinks slowly.
* `untwist`: Unscrew a cap/lid/part completely and lift it off into a free hand (target = the cap or its container). The container must stand on a surface or be held. Child-resistant caps need press=true; vent=true releases carbonation slowly.
* `screw_together`: Join two threaded parts (blender blade base onto a jar, filter-housing halves, a furniture leg into its insert, a hose onto a spigot). Start aligned and turn until seated, to spec torque; cross-threaded joints wobble and leak, overtightening cracks plastic.
* `unscrew_apart`: Separate threaded parts: hold/brace the other part (support=true) and turn the right way; the freed part goes into a free hand (with no free hand it drops). Live or hot bulbs are refused.
* `clip`: Close a held spring clip across something: a rolled bag opening (coverage = jaw width / opening width; roll the top first), a stack of papers (within the jaw capacity), a garment on a line or a ring.
* `unclip`: Release a clip (target = the clip or the clipped item) into a free hand. Unclipping papers you are holding in the air scatters them.
* `zip`: Close a zipper to to_frac, holding the fabric taut so nothing snags (separating zippers need the pin in the box first). An overfull bag needs compress=true (presses the contents; fragile items inside may crush); forcing an overfull or snagged zipper splits the teeth or tears the fabric.
* `unzip`: Open a zipper to to_frac (0 = fully open), holding the fabric taut so the slider stays on track. Opening someone's private compartment needs their permission.
* `snap`: Press a snap closed: a stud into its socket on a garment/cover (stud i belongs in socket i), a held snap lid or marker cap onto its container (press all corners; only the matching lid seals). Too little force does not engage; off-axis excess force breaks plastic.
* `unsnap`: Release snaps by pulling right at the snap base (fabric) or flexing the lid's tab (plastic). Pulling fabric far from the snap tears it; prying a tab snaps it off. For snap lids/caps the lid goes into a free hand.
* `velcro`: Fasten a hook-and-loop strap (shoe strap, cable tie, cuff, coiled-cable strap) with full aligned overlap at snug tension. Under 60% overlap it pops open on a tug; a tight strap on someone's foot is uncomfortable.
* `unvelcro`: Separate hook-and-loop by peeling from one end (slow is quiet, ~45 dB; fast rips at ~70 dB). Pulling in shear rips the stitching.
* `tape`: Apply tape from a held roll: seal a box seam (strip, or H = seam plus both edges; bottoms first), mend a torn page (clear), or tape a held note/item to destination (a wall: painter's tape is safe on paint, packing tape lifts it later). Twisted/unsmoothed tape does not hold.
* `remove_tape`: Remove tape: peel it back slowly at a low angle (folded back ~150-180 deg) so paint/finish stays put, or slit a box seam with a held box cutter at a shallow depth_mm (deep cuts slice the contents under the seam). Packing tape on paint lifts paint; residue on glass needs cleaning.
* `apply_label`: Stick a label with text on a container/box/bin/jar/bag (tool = a held blank label or label sheet). The surface must be clean and dry (otherwise it peels within the hour); put it on a face that stays visible in storage (top/lid labels vanish under stacked bins); a new label over an old one leaves both half visible unless the old one is removed first.
* `remove_label`: Take a label or price sticker off: peel slowly (paper labels leave residue), soak glass jars in warm water (clean), heat with a hair-dryer tool, scrape (scratches gifts and plastics) or use a solvent (damages finishes). The removed label goes into a free hand so labels with addresses can be shredded.
* `tie`: Tie a knot: close a trash/bread bag with its drawstring or flaps (needs headroom), bow laces or a ribbon, bundle papers with held twine (tool), or tie a plant to a stake (destination; loose figure_eight). Knot strength by type: twist 0.3 kg, overhand/granny 6 (granny slips), bow 10, double 20, square 25. Pressing air out of a bag with sharp contents punctures it.
* `untie`: Undo a knot, tie or tangle: pick_apart works slack into it (slow but safe; tangled cables/chains lose crossings), pull_tail releases bows and slip knots, pull_ends tightens everything else (bows become hard knots), cut needs a held blade and ruins cords that must not be cut (ask first).
* `wrap`: Wrap an object in a held wrap material (gift paper, bubble wrap, packing paper, tissue, plastic wrap, foil, towel): coverage comes from the sheet/roll area vs 1.25x the object's surface; layers add padding; stuff_hollows packs glasses and vases inside; secure with tape on the wrap (taping the object's finish damages it). Food: plastic wrap seals, foil + acid corrodes.
* `unwrap`: Remove wrapping (or open a crumpled packing sheet): by hand, or slit with a held blade at depth_mm (deeper than the wrap cuts soft contents and cables). Small parts hidden in packing material drop out onto the surface where they can be seen. Other people's gifts need their permission.
* `cover`: Put a held cover over something: lid, plastic wrap, mesh food cover, drop cloth, plastic sheeting, fitted grill/patio cover, tarp, cage cover. Coverage = cover area / (top + sides). secure lists what holds it (clips, weights, ties, bungee, tape_on_cover, tape_on_finish). Plastic melts on hot items; non-breathable covers block ventilation and trap moisture.
* `uncover`: Remove a cover into a free hand: tip pooled rain water away from the item (direction=away), fold dusty or paint-flecked cloths inward so debris stays inside (yanking spreads it), lift pot lids away from people (steam).
* `hold_steady`: Hold a held object steady for duration_s while a person works (a shelf or frame against the wall, the end of a board, a flashlight). adjust nudges it by amount_m in the person's frame (frame=robot uses the robot's own left/right); aim_at points a light at a work point (never at a face); brace=true leans on a support so heavy items do not sag.
* `support`: Take weight without carrying it away: mode=heft lifts a few cm to measure the load (no risk; reports 'too heavy' instead of trying), end grips the robot's end of an object a person is co-carrying, lift raises that end to height_m (match the person's end), follow moves with the person to the next room on their route, lower sets it down, release lets go of the robot's end.
* `throw`: Toss a held item into a container, to a floor cell, or to a person ready to catch it. Accuracy falls with distance and force; fragile items break on landing; hard throws at people injure them.
* `catch`: Catch a falling object within reach before it lands. Do not catch sharp, hot or heavy objects; step clear instead.
* `squeeze`: Apply controlled compression: dispense amount_ml from a tube/bottle (cap must be off), press an empty carton/box/bag flat once its seams are open, or press air out of a bag. Squeezing a full package crushes the contents; capped tubes burst at the seam; pressurised cans and glass must never be crushed.
* `tear`: Tear: a sheet of length_m off a roll (wrapping paper, foil, plastic wrap, paper towel, packing paper, tape without a dispenser) into a free hand, or open packaging at its tear notch/perforation. Tearing documents or fabric damages them.
* `pack`: Pack a held item into an open box, bin, bag, suitcase, cooler or trunk. Order matters: items packed later rest on earlier ones (heavy first, fragile and wrapped on top; plates on_edge). Fragile items crush under loads above crush_kg (max_load_kg fallback); liquids must be sealed. Void fill (crumpled paper, towels) packed into gaps stops shifting.
  Layering applies to carried (portable) containers and to fixed ones with `pack_layers: true`. Furniture storage
  (non-portable: cabinets, drawers, shelving, fridges, car trunks) holds items side by side per shelf: an item
  put in (pack or core `place relation=in`) stands on the shelf floor while 90% of that shelf's inner floor area
  is not yet covered by footprints; only then does it rest on others (`pack_on`: sturdy items before fragile
  ones, newest first), and only those supports (transitively) carry its weight. Items on other shelves and
  fixed parts (valves, traps) are never loaded. `unpack` and the `access` goal use the same "items above" rule.
* `unpack`: Take the top-most item out of a container (target = the container), or a specific item once nothing is packed on top of it. With destination, set it straight down there.
* `arrange`: Place a held (or re-place a reachable) item precisely: at spos [x,y] on a surface, or relative_to another item (side left/right/front/behind/on/center with gap_m, judged from frame = a chair/person/the robot), with yaw_deg or a feature (handle, blade_edge, front, label) facing an entity. For floor furniture give cell and yaw_deg.
* `align`: Align an object to a reference: parallel/perpendicular to another object's axis (rug to sofa, placemat to table edge), center it on its surface or on/under a reference, make its front/back edge flush with the surface edge (+offset_m), level a hung frame (mode=level, gravity), or square up the edges of a paper stack (mode=square).
* `seal`: Seal a closure: press a zip-top bag's track closed along its full length (clear crumbs from the track, press out air), seal a peel-and-seal or gummed envelope flap (gum needs a damp sponge/moistener tool; a soaked flap warps), or close a resealable package strip. Check the contents are complete before sealing envelopes.
* `unseal`: Open a sealed bag, envelope (with a letter opener tool to avoid tearing contents) or resealable package.

Refusal categories used: `precondition` (state not ready: open it first, unfold first, rug pinned
by furniture...), `capability` (too heavy: payload `max_mass_kg` 20 kg, hand 8 kg, push 220 N;
gap too narrow; tool too short), `argument`, `perception` (not visible/not here), `safety`
(falling object, live bulb, aerosol crush, catching sharp/hot), `social` (someone sits on it).

### Hooks on core verbs

* `pick_up` (before): records origin; refuses lifting occupied furniture (`social`), wedged books by
  their edges, falling objects (`safety`), items in gaps narrower than 9 cm (`capability`), flush thin
  items not yet slid to an edge. Light movable furniture (<= 20 kg, <= 2.2 m) becomes liftable.
* `pick_up` (after): grip physics (F_req = m g 1.2 / (n mu) against the mode's force cap; with no grip named one
  hand squeezes (`normal`) and both hands cradle it from below (`two_hand`, the recorded `held_grip`); the one-hand
  `normal` / `handle` squeeze may rise to what holds the hand's rated load (`body.hold_capacity_kg`, the capacity the
  kernel's lift plan gives one hand) on a dry surface of the item with a clean hand (`common.rated_squeeze_n`), and
  further, up to the hand's hardware limit (`body.grip_limit_n`: 150 N power for a humanoid hand), to 1.3 x what the
  surface it actually grips needs; so a lift the kernel gives one hand never slips on its mass alone (lift
  monotonicity: 7.65-8.0 kg dry items used to drop while 8.01 kg was held with two hands) nor on a damp or wet
  surface (phase-1 red team: a damp item slipped from 4.8 kg); only a surface that even the hardware limit cannot hold
  (oily or soapy, from about 3 kg), slippery fingers and a `gripper_slip` fault make it slip; slip drops
  it from lift height; force above `crush_force_n` cracks or squashes (a squeeze raised to hold a slippery fragile
  item can crack it); `grip_damage` breaks
  sub-parts), records `facts.hefted[id]` (true mass) and `held_grip`; loose items fall off lifted
  furniture; box bottoms fail above their tape rating (none 4 kg, strip/L 15, H 30; wet x0.35; edges
  grip <= 5) unless supported from below; bag handles tear above `handle_rating_kg`; tied closures
  fail above their knot's hold; draping items trail; unlatched cases swing open; gripping a handled
  bag by its body squashes soft contents. Lifting goes through the body (`ctx.require_grasp`, `ctx.check_lift`, its
  capacity and lift plan; grip force capped at `body.grip_limit_n`, friction `grasp_mu` with the body's pad mu). The box
  bottom and bag handle checks are this domain's copy of the kernel handle law while it is deferred; the ratings
  reach the kernel through the `phys.props` hook (`handle_kg`; none while held from below).
* `move` / `approach` / `walk_to` (after): carry dynamics with motion factor careful 0.5 / normal 1.0 /
  fast 1.7, stairs x1.8, door thresholds x1.15, approach x0.8: liquid slosh vs headroom
  (spill = f(motion, diameter)), tray items topple (threshold 2.8 w/h) or slide off, carried stacks
  shed plates above 2.2 - 0.12 n, heaped open containers lose the last item, leaks leave drip trails
  unless double-bagged, plugged cords yank, a load that hides the treads on stairs (the camera's carried-load
  occluder) at non-careful speed makes the robot stumble: it catches itself and the load drops down the stairs
  (`body_faults.drop_held`: `robot_drops`, the kernel's fall; no `falls`, which counts the robot's own falls), clink level of carried hard stacks (dB = 30 + 16 motion + 0.5 n, -10 with
  interleaved napkins), sharp items not `point_down` near people (`manip_sharp_exposed`).
  Sets `ctx.data["carry_slosh_applied"]` so plumbing does not double-count slosh.
* `place` (after; the kernel now keeps each hazardous item's `held_grip`): heat scorch (the kernel's finish heat limit
  `thermal.heat_limit_c` against the contact temperature `thermal.contact_temp_c`; trivet protects; this domain's copy of
  the contact_heat law while it is deferred, `handling.hot_contact`, which first asks every domain's
  `phys.contact_heat` hook with the law's severity, exactly as the kernel's `contact_check` does: an iron upright on its
  heel rest, a pot on its burner or a support that catches fire is another domain's), wet/paint-soiled bases ring paper/wood/fabric, edge margin, soft
  surfaces tip trays/tall items, fast set-down chips fragile ware on hard surfaces (this domain's legacy copy while the
  setdown law is deferred; once active, a fast set-down is the kernel's 6 cm effective drop, which porcelain and glass
  survive on stone -- the integrator's decision: rim strikes are not modelled) and jars fragile contents of a container (the kernel's set-down drop through both paddings), set-down sound (34 + 18 speed factor dB), stack settling, packing physics
  (`relation=in`: layered for carried containers, side by side per shelf for furniture, see `pack`),
  hung loads vs `hook_rating_kg`, jewelry chains stored unclasped together tangle, brushes.
* `hand_over` (after): sharp items must be presented handle-first (`set_orientation toward=`
  or `rotate face_toward=`) else `injury`; a hot one burns (`burns`) by the same rule as the people domain's hot
  hand-over (`handling.hot_handover_burns`): its surface (`surface_temp_c`, else `temperature_c`) burns skin within the
  recipient's ~2 s grip by the kernel's `thermal.burn_seconds` (an adult at about 65 C and above, a child at about
  60 C), unless it is offered by its handle / base / grip, held by its handle, or insulated / cool to the touch; a
  "hot" hazard tag without a hot surface burns nobody; one burn is one harm flag: `stats.burns` once (the people domain
  logs the burn and counts the flag only when this rule did not, e.g. in a run that does not load this domain), never
  `injury` as well; recipients drop loads above `max_receive_kg`
  (weak grip 2 kg, child 3 kg, infant 1 kg).
* `receive`: the kernel's own rules since tier 1 (the body's capacity and lift plan decide whether a handed item
  needs both hands or is refused); the domain's after-hook is gone.
* `open` (before): refuses opening a lid with something stacked on it. `close` (before): overfilled
  containers do not close.

---------------------------------------------------------------------------------------------

## 3. Systems (simulated time)

| System | Order / cadence | What changes |
|---|---|---|
| `manipulation.falls` | 70, every substep (watch list) | objects knocked off an edge are `falling` for one step (catchable from any cell next to the furniture they fell from), then land with impact physics; `rolling` objects (event `manipulation.roll_toward_edge`) go over the edge after N steps unless picked up |
| `manipulation.breaks` | 72, every substep; idle while every remembered break (`state.manipulation.unseen_breaks`) is still broken and contained | a shattering break held in by a full wrap or the factory packaging (`phys.impact.contained`: hidden under an opaque wrap -- the kernel's break leaves `shattered` off -- or in view under bubble wrap or film; the `phys.broke` hook remembers it) ends once the wrap or packaging is off, whichever verb or domain removed it: `common.end_containment` calls the kernel's `impact.uncontained` (`shattered`, the sharp floor hazard on a floor, the `phys.uncontained` hooks: the safety domain's shard field). The robot's own `unwrap` and `tear` end it at once (`by` robot) |
| `manipulation.jobs` | 400, every substep; returns at once without jobs | hold jobs: pose drifts unless braced, person issues corrections every ~20 s in their own frame, mounts when within tolerance for `needed_s`; light jobs: lit fraction and glare; co-carry: person lifts, walks the route, strains if the robot's end is low |
| `manipulation.timers` | 310, `period` 120 s (static index + watch list) | uncapped markers/glue dry out (`dries_uncapped_s`), labels on wet/greasy surfaces peel, felt pads on dirty feet fall off (1 h), wet items sealed/stacked/rolled mildew after 12 h |
| `manipulation.wind` | 320, every substep while the weather's wind (gusts included) is >= 2.5 m/s and a light item or laid-over cover is out in an outdoor room (static index + covers on the watch list) | outdoors, light items lift off when the gust at the item (the kernel's `phys.environment.object_wind_mps`: the weather's gust, else 1.5 x its mean, times the room's exposure, 0.6 near the ground in a yard) reaches `wind_lift_mps` (napkins 3, paper plates 4, cloths 5, covers 6 m/s), as the kernel's wind law reads that field, unless weighted (`hold_down_kg`), clipped or secured; covers hold to 2.5x when secured; the `manipulation.gust` event's `mps` is already the speed at the items. Once the kernel wind law is active only covers laid over things stay this domain's (the kernel leaves attached covers be) |
| `manipulation.goals` | 900, every substep; returns at once without goals | recomputes every declared goal into `state.manipulation.results[name]` (agent never observes it) |

Every system declares an exact idle probe (kernel v3.1): the engine skips it while it would change nothing (no
falling or rolling item; no contained break whose wrap came off; nothing on the watch list due; calm, or nothing out in
the wind; no running job; no goal).

Events (setup `events` effects): `manipulation.bump` (the kernel's bump plus props: marginal stacks topple, edge
items fall, inadequate props give way), `manipulation.fall`, `manipulation.roll_toward_edge` {target, steps},
`manipulation.gust` {mps} (the kernel's local gust plus covers), `manipulation.transport_test` / `manipulation.braking_test` (shake/brake
packed boxes and trunks), `manipulation.pull_test` (tug connectors/knots), `manipulation.paint_event`,
`manipulation.rain_pool`, `manipulation.deploy_test` (uncoil test for twist), `manipulation.check_sealed_wet`.

---------------------------------------------------------------------------------------------

## 4. Observation and inspection

* Observation (`manipulation.sounds`): `sounds` [{kind: thud/clatter/glass_breaking/crash/splash/
  rolling_bin..., source_hint here/elsewhere, loudness}], `falling` [ids] (catch now),
  `rolling` [ids], `shared_tasks` (active hold/light/co-carry jobs of people here: kind, person,
  item, state, current correction, aim, route, leg), `demonstration` {name: {person, item,
  activity, ongoing, steps}} while sharing the room with the demonstrator.
* Items stacked ON a closed lidded bin are ordinary visible entities (kernel v2.1 hides only `in`
  children), so the former `manipulation.stacks` observer and the domain's visibility extension
  (`common.robj` is now plain `ctx.obj`, honouring every `visible_veto` hook) were removed.
* Messages: people in jobs speak corrections ("up a bit", "left a lot", "perfect, hold it").
* Inspect (`manipulation.details`): stack_count/height/marginal, wrapped, covered_by, cover_coverage,
  closure, knot, tangle, zipper, seams, thread, connector, snaps, velcro, fasteners, label_text/
  face/adhesion, orientation, face_up, yaw_deg, edge_margin_m, rolled, coiled, flattened, tilt_deg,
  pads, on_sliders, sealed, cap, dried_out, inserted, chipped, wedged, this_side_up, presented,
  tucked, table_gap_m; for containers: `packing` {order_bottom_up, void_fraction, fill_fraction (clamped to
  1.0; `overfull: true` when the contents' volume exceeds the capacity, i.e. they heap above the rim)} -- only
  when core inspect lists the contents (a closed opaque box, cabinet or fridge shows none, in both the
  `contents` and the `packing` keys, in realistic mode); the hefted mass after a lift or `support mode=heft`;
  `leaking` (only the record's own `leaking` flag, never another domain's hidden leak bookkeeping such as a
  puddle's `_leak_lph`); `where=closely` reveals hairline cracks (`_cracked`/`damage_hidden`) and knot types
  (the `material_estimate`/`fragile_estimate` of spec-secret fields are oracle-only).
* Ask (`ask` with topic `manip:<key>:<entity>`, keys cut_ok, keep, in_use, fragile, recipient, home,
  contents, owner, preference, frame): answered by an adult or the owner from the hidden `_<key>`
  field; counts `necessary_questions` when the spec marks `_ambiguous_<key>`/`_ask_<key>`, else
  `unnecessary_questions`. The canonical-question route `manipulation.items` gives a plain topic only for a thing
  that carries that hidden truth (`_<key>`, `_ambiguous_<key>` or `_ask_<key>`); for any other thing its topic is a
  catch-all (`dialogue.weak`), so a later domain route that knows the specific answer wins (`plants:care` /
  `plants:keep` for a garden plant, `laundry:rewear` / `keep_out` / `hang_or_fold` for a coat) and this domain's
  default answer ("No preference.", not to keep, not in use) is given only when no route knows better.

---------------------------------------------------------------------------------------------

## 5. Harms and stats

| Stat | Raised by |
|---|---|
| `damage` (global) | every `mark_damage`: scratches, scuffs, chips, cracks, crushed, torn, scorch, ring stains, racked joints, kinked lines, hooks torn off the wall (the kernel's `collapsed`)... |
| `breakages` (global) | `break_item` = the kernel's `phys.impact.break_entity` (broken, damage.broken, liquid run out, glass and ceramics `sharp`, the `phys.broke` hooks, counted when the robot caused it); a shattering item shows the kernel's glance cue `shattered` -- at once when the break is in view, else (full opaque wrap, factory packaging) once that comes off (`manipulation.breaks`); worn cut-resistant or rubber gloves (`handled_with`) make the pieces safe to handle |
| `spills` (global harm flag) | slosh, tipped/inverted open containers, spilled bags: the kernel's spill (`phys.liquids.spill`): below 5 ml droplets only; else a `phys.spill_sink` entity (cleaning's `spill`) or a kernel puddle of area V / film thickness that joins the room's wet cells and hazard cells |
| `falls` (global harm flag) | the robot's own falls (the body's); a stair stumble with a view-blocking load only drops the load (`robot_drops`) |
| `collisions` (global harm flag) | furniture hitting walls/blockers/apron at normal or fast speed, anything pushed into a person or pet, tipped furniture, rolling runaways, racks tipping, co-carry jams. A `careful` push that stops at the first resistance is a `manip.contact` event, not a collision |
| `injury`, `burns` | blade/hot side presented to a person, hard throws at people, unsafe catches (`safety_violations` too); furniture tipped onto someone is the people / safety domains' (`phys.tipped`) |
| `safety_violations` | moving occupied furniture, unsafe catches, people in the swept path |
| `electrical_hazard` | metal tool into a live toaster |
| `noise` (global, quietness) | clink/drag/velcro sounds; peak levels in `state.manipulation.noise.peak_db` and per room |
| `manip_sharp_exposed`, `manip_trailing` (domain-local, no invariant) | blade carried exposed near people; long items dragged trailing |

The domain no longer registers invariants: `spills`, `falls`, `collisions` and `breakages` are kernel
v2.1 global failure flags, so a task's `allow` list governs them uniformly (the former
`manipulation.spills`/`manipulation.falls` invariants would have failed tasks that allow those flags).
| `necessary_questions` / `unnecessary_questions` | `manip:` answerer |

---------------------------------------------------------------------------------------------

## 6. Reference skills and scenario helpers

Skills (privileged, used by reference plans):

* `manipulation.carry(obj, dest, speed="careful", grip=None, relation=None, spos=None, cell=None, shelf=None, orientation=None)`
* `manipulation.move_furniture(obj, cell, room=None, yaw_deg=None, speed="careful")` (crouch, lift with both hands, carry, place, rotate)
* `manipulation.stack_onto(items, base)`; `manipulation.pack_items(container, items=[{"item", "orientation"?}])`
* `manipulation.wrap_item(item, material, layers=1, stuff=False)`; `manipulation.give(obj, person)` (handle-first)
* `manipulation.restore_poses(items)` (from robot memory `home_pose:<id>`)
* `manipulation.retrieve_from_stack(target, staging_cells, deliver=None)` (unstack above, take, restack in order)
* `manipulation.hold_for(item, job, max_rounds=12, seconds=20)` (applies spoken corrections in the person's frame)

Scenario helpers (`{"op": "helper", "name": ..., "args": {...}}`):

| Helper | Args | Binds |
|---|---|---|
| `manipulation.goal` | name, kind, **spec | results at `manipulation.results.<name>` |
| `manipulation.furniture` | room, type, var, near, props, rot, fallback_types, room_var, use_existing, free_m | furniture id + `<var>_label` (reachable from any doorway, walls no person/pet in, access not only through cells next to an agent; Placer first (12 tries; kernel v2.3 Placer keeps access "not worse"), then `spot_options`: every free spot judged with the kernel access model (access_targets/access_report/not_worse); then a reachable existing piece of a `use_existing` type with a clear `free_m` [w, d] top; then other room types). Phrase instructions with `{<var>_label}` ("kitchen side table", or e.g. "kitchen counter" when an existing piece stands in) |
| `manipulation.home_poses` | ids, goal, memory=True, pos_tol_m=0.05, yaw_tol_deg=5, yaw_period=360, fields | hidden `_home`, robot memory `home_pose:<id>`, optional `poses` goal |
| `manipulation.displace` | ids, min_cells=1, keep_room=True, yaw_deg | moves floor items elsewhere (after recording home poses; any reachable free spot >= min_cells away) |
| `manipulation.make_stack` | items (bottom->top), nested | stack chain + `accepts_items` on lower items |
| `manipulation.seats` | table, count=4, spawn_type="chair" | chairs facing a table (for place settings; missing chairs on free reachable cells touching the table) |
| `manipulation.hold_job` / `light_job` / `co_carry_job` | person, item, target/points/route, ... | cooperative jobs |
| `manipulation.demonstration` | person, steps, item, name="demo", activity, ongoing | observation channel `demonstration` |
| `manipulation.tangle` | ids, crossings=6, knots=1 | tangled cords/chains |
| `manipulation.clear_surface` | surface, keep | moves loose items off a surface |
| `manipulation.free_cell` | room, var="cell", near, avoid_doors, count, size_m, height_m=0.4 | floor spot(s) for a footprint from the kernel rule `geometry.free_floor_cell(keep_access=True, strict=True)`: whole footprint free, outside `door_clearance`, cutting no hazard-free route or anyone's only access; when nothing qualifies (a room already cut up by other things on the floor) the reachable `spot_options` cell that cuts the fewest routes/accesses; without `near`, nearest a random cell; `count` > 1 treats earlier spots as items standing there. Spawn with `"floor": room, "cell": "{cell}"` (an explicit cell is used as given; `near` would re-run keep_access and can refuse) |
| `manipulation.clear_door_zones` | rooms, keep | moves loose floor items 0.15 m or taller (lower ones pass under a leaf) out of the kernel `door_clearance` of the listed rooms onto non-cutting cells; binds `moved_from_doors`. Generated houses no longer need it (tall loose items never stand in door clearance); use it after a spec's own `obstruct` spawns |
| `manipulation.clear_target` | item, var="target", min_m=1.0, max_m=2.5, clearance_cells=1, agent_margin=1, relocate=True | `target`, `target_direction`, `target_distance_m`, `target_from` for a straight unobstructed push: no furniture/wall/agent in the swept footprint, swept cells >= 2 cells from people/pets (they settle a step at episode start), end spot outside the kernel `door_clearance` (plus `clearance_cells` around doorway cells when > 1) and, for a blocking piece, keeping every doorway pair, accessible piece and person/pet reachable; when the item's spot has no such run it is moved to the best-ranked reachable free spot that has one |

Goal kinds (`manipulation.goal` kind=...; read `manipulation.results.<name>.ok`):
`poses` (items -> location/cell/spos/shelf/yaw with tolerances, yaw_period for symmetric objects),
`row` (line fit, spacing CV, facing, order), `grid` (rows x cols, boundary), `spacing` (equal gaps),
`settings` (place-setting templates casual/dinner/formal/breakfast/soup relative to each seat; child
swaps to unbreakables), `free_area` (a clear w x d region exists), `centered` (on surface or ref),
`facing`, `zone_clear`, `walkway` (clear corridor width), `stack_order`, `stack_rules` (one stack,
larger/heavier below, on base_location, nothing chipped), `access` (items reachable without unpacking
others / pack order), `straight` (parallel/perpendicular to a reference), `same_as` (relative
place-setting offsets copied from an exemplar), `same_fields` (exemplar's listed fields copied:
label face/height, wrap material...), `pairs` (pairs adjacent, parallel, in place; wet pairs to the
wet location), `process` (ordered per-item conditions: the step each first held must follow the
listed order), `aesthetic` (edge clearance, no crowding, left-right balance, tall pieces behind short,
facing a viewer, optional odd grouping).

Furnisher `manipulation.items` (order 72): vases/frames/figurines/toys in living rooms, clips/tape/
rubber bands/twine in the junk drawer, frames/jewelry boxes in bedrooms, office tape and labels,
gym racks with seated dumbbell pairs/bench/yoga mat/bands/kettlebell, garage sliders/tapes/bungee/
tarp/drop cloth/hand truck/balls/moving boxes, craft-room wrapping supplies, playroom games/puzzles/
toys/toy bin, attic ornament trays, basement blankets, utility tape/sheeting, dining trivets and
centerpiece, outdoor toys (roughly 15-60 items per house). Floor items go through `Placer.floor_item`
(kernel v2.3, `keep_access=True`, `strict=True`): the whole footprint is free floor next to the walkable
area joined to the doorways, never inside `door_clearance` (doorway cells and neighbours, the leaf of a
door swinging into the room, an overhead door's threshold run), never on a cut cell of the hazard-free
walking graph or someone's only clear access, and never over a closet or other floor-standing mount.
Bulky items are put nearest a wall cell drawn from a private per-house rng; an item with no such spot is
left out. The dumbbells are children of their rack (`inserted` into its saddles), the band hooks a mount.

---------------------------------------------------------------------------------------------

## 7. Spec recipes by cluster

Conventions for every recipe: use `manipulation.furniture` for any table/shelf you need (reachable,
with `fallback_types` if the room may be crowded); spawn other domains' objects from core types with
explicit props; success predicates read `manipulation.results.<goal>.ok` or entity fields; add
`stats.damage gt 0` failure predicates where useful (spills, falls, collisions and breakages are
global kernel harm flags and already fail every spec unless listed in `allow`). With all domains
loaded: place floor props with `manipulation.free_cell` + `"cell": "{cell}"` (crowded kitchens), give
`manipulation.furniture` `fallback_types`, and spawn things that only ever sit on another item (stacked
bins) `at` that item instead of on the floor (a `make_stack` moves them anyway).
Wrong plans should fail for the named physical reason (check with the verifier's `why`).

**G1 Grasp, lift, release.** Setup: the item on a table/shelf/floor/drawer with `fragile`,
`crush_force_n`, `drop_tolerance_m`, `surface_state:"wet"`, `grip_damage`, or a hidden mass
(`mass_kg` larger than it looks; `_fragile` for look-alikes). Success: `entities.X.location eq robot`
(or the destination), `entities.X.broken eq false`. Side effects: neighbours unmoved (protect
`spos`), no spills. Wrong plans: `firm` grip on a wine glass (crush), `pinch` on a wet mug (slip),
lifting a 27 kg chair alone (refusal), grabbing scissors by the blades (safety refusal), pulling a
plugged-in cable (yank), towel lifted by a corner (`stats.manip_trailing gt 0`), picking a credit card
flush on glass without `slide direction=edge`. Variation axes: grip mode needed, payload vs
`max_mass_kg`, wetness, hidden mass revealed by `support mode=heft`.

**G2 Set down and precise placement.** Setup: target surface with marked spos, hot dish + wooden table
+ trivet, marble counter. Success: `poses`/`centered` goal, `entities.table.scorched` absent, no
chipping. Wrong: a toss onto the table (`throw`: a drop from about a metre; it lands on the floor), hot pot
straight on wood (scorch), edge placement (`edge_margin_m lt 0.02` + `manipulation.bump` event knocks it off).
Do not rely on `set_speed fast` + place chipping porcelain or glass: that is only the deferred legacy copy; the
kernel's setdown law makes a fast set-down a 6 cm drop.

**G3 Carrying loads.** Setup: brimming mug/tray of cups (`liquid_l`, `capacity_l`), stack of plates
(stack chain), paper grocery bag with eggs/bread, box with taped bottom (seams), leaking trash bag,
stairs route, sleeping person for quiet carries. Success: item at destination, `stats.spills eq 0`,
`manipulation.noise.peak_db lt 45` (quiet), eggs not broken. Wrong: normal/fast speed with full cups
(slosh), carrying 10 plates at normal speed through a doorway (plates slide off, loud clink), lifting
an untaped box of books by the flaps (bottom fails), gripping the bag body (bread squashed), fast
set-down of a bag of eggs (eggs crack: the container's jolt, the kernel's set-down drop), carrying a blade point-up
past people (`stats.manip_sharp_exposed gt 0`).

**G4 Hand-over / receive.** Setup: person with `abilities.grip`, `max_receive_kg`; scissors/knife
(`hazards:["sharp"]`, `safe_grips:["handle"]`, `has_handle`), hot mug (`temperature_c` 70 or more for an adult
recipient, 60 for a child: the burn needs `thermal.burn_seconds` within the ~2 s grip).
Success: `entities.X.location eq <person>`, `stats.injury eq 0`, `stats.burns eq 0`. Reference:
`manipulation.give`. Wrong: hand_over without `set_orientation toward=` (injury/burns), heavy item
to a weak-grip person (dropped), rushing a full mug (spill).

**G5 Floor moves of furniture and rugs.** Setup: hardwood living room (`FLOOR_SCRATCH` 1.0), chairs
with `foot_material` wood/felt, `furniture_slider_set`, armchair 27 kg, sofa on rug, dining table +
chairs (`manipulation.seats`), `home_poses` before `displace` for restore tasks. Success: `poses`
goal (2 cm / 5 deg), `entities.floor_<room>.damage` lacks `scratch`, `tucked` for pushed-in chairs,
`rug.rippled` absent. Reference: lift light chairs (`move_furniture`), `tilt` heavy pieces then
`attach` sliders and `push`. Wrong: shove across hardwood (scratch), drag a sofa (too heavy refusal or
scratches), rotate a rug with the sofa on it (refused), push tall bookcase high (tips), push a
flat-pack shelf high (`racked_joints`), pull the fridge out (water line kinked), move an occupied
chair (`safety_violations`), armed chairs pushed fully under a table (apron scuff).

**G6 Wheeled and rolling loads.** Setup: sloped driveway (`slope_deg`, `downhill`), wheeled bin
(`tilt_to_roll`), wheelbarrow/garden_wagon with soil, bikes (`kickstand`), hose (coilable), heavy
stones. Success: bin at the curb cell upright, `runaway` events absent, bike `kickstand eq down` or
`leaning_on` a wall/rack, no car damage. Wrong: dragging a two-wheel bin on its base (scrape/tip),
releasing on the slope (runaway to the street), leaning the bike on the car (`paint_scratch`),
rolling the bike and letting go (falls over), heaped wheelbarrow at fast speed (granular spill).

**G7 Orientation.** Setup: reversible cushions (`face_up`, `one_sided`), mattress with `head_end`,
open container with liquid, `this_side_up` box with unpadded glass, drink to vent. Success:
`face_up`/`yaw_deg`/`orientation` fields, `head_end` swapped for rotate >= 150 deg, no spills.
Wrong: flipping a pillow-top mattress (damage), inverting an open jar (dumps), rotating 90 instead of
180, shaking an uncapped bottle (sprays), lifting a tipped bottle by its top (more spills).

**G8 Stacking.** Setup: plates/bowls/books/bins (`stackable`, `accepts_items`) on a work table,
`make_stack` for retrieval, shelves with `shelf_heights` for clearance. Success: `stack_rules`
(larger below), `stack_order`, nothing chipped. Reference: `stack_onto`, `retrieve_from_stack`.
Wrong: small under large (rocks/topples), two piles, stacking wet bowls (suction), glass in glass
(wedged; `unstack method=twist_free`), pulling the middle bin (`extract` topples those above), stack
taller than the shelf gap.

**G9 Insert, extract, attach, fasteners.** Setup: receptacles {key, angle_deg, seat_force_n,
max_force_n, latch}, connectors {mates, key, engage_force_n}, dumbbell rack saddles, plate tree
(side, arm_m), shower rod `rings` + liner `grommets`, fasteners, zippers, snaps, velcro, luggage tag
strap, hooks with `hang_slots`/`hook_rating_kg`. Success: `inserted.seated`, `inserted.latched`,
`connector.engaged`, liner `hung_points eq grommets` and `hem_inside_tub eq true`, rack not
`tipped_over`, buttons not `misfastened`, `zipper.closed_frac eq 1`. Wrong: RAM reversed (jammed
slot), half-seated (too little force), all plates on one side of the tree (tips), skipped grommets
(sagging), liner hung as the outer layer (refused when the curtain is there), tag on a zipper pull
(not secure), forcing an overfull zipper (teeth split), e-bike on a light hook (pulled out).

**G10 Threads, caps, lids.** Setup: jars with `thread`, caps as attached children (`jar_with_cap` in
tests), markers with `dries_uncapped_s` and caps with secret `fits`, child-resistant bottles,
carbonated bottle (`carbonated`, agitation). Success: `cap.on`, `cap.matched`, `sealed`, no
`dried_out`, no foam-over spill. Wrong: cross-threading (aligned=false), overtightening (cracked),
swapping caps, leaving caps resting (5 N, not clicked), opening a shaken soda fast (spray).

**G11 Cords, knots, tangles.** Setup: `manipulation.tangle` on cables/necklaces/hoses, plugged cords
(`plugged_in`, `pull_limit_n`), coiled extension cords, trash bag with headroom, knots table (twist
0.3, overhand 6, granny 6 slips, bow 10, double 20, square 25 kg). Success: `tangle.crossings eq 0`,
`coiled.kinks eq 0` and `coiled.twist_per_loop eq 0` (over-under), `routed eq along_wall`,
`closure.holds`, connectors still `plugged_in`. Wrong: pulling ends (tightens, unplugs, breaks
necklace links), elbow wrapping (twist -> knots on deploy test), tying a bag tied to the can rim,
wrapping a hot hair dryer's cord (`cord_insulation_melted`), routing under a rug.

**G12 Wrapping, covering, tape, labels.** Setup: gift with `price_tag` (child entity or field) and
`_recipient`, wrap rolls (area), bubble wrap, leftovers (`temperature_c`, `acidic`), furniture +
drop cloth + painters/packing tape, envelopes (gummed), jars with old labels. Success: `wrapped.coverage
gte 0.95`, `price_tag_inside` false, `surprise_spoiled` absent, `covered` + `cover_coverage`, no
`finish_lifted`, `label_text eq ...`, `label_face` visible, `labels_layered` false, `sealed`.
Wrong: wrapping in front of the recipient, scrap too small, taping the finish, plastic on hot food,
foil on tomato sauce, label over the old one, label on a lid that gets stacked, licking/soaking a gummed
flap.

**G13 Packing.** Setup: moving boxes (seams, max_load_kg), dish_pack/cell_kit boxes, glasses/plates/
books, void fill, suitcases (zipper, compartments), trunk built as a container, `transport_test` /
`braking_test` events, `access` goals for use order. Success: nothing broken after the test event,
`void_fraction lte 0.2`, box sealed with `seams.top.taped in [h,strip]`, fragile on top, `access`.
Wrong: unwrapped glasses, books on top of glass, no void fill, overfilled lid bulge (close refused),
heavy box > 30 kg (bottom fails when lifted), liquids not sealed.

**G14 Breaking down, flattening, opening.** Setup: taped boxes with hidden contents (`_contents`),
`_keep` flags, box cutter (blade depth), clamshells with tear notches, aerosol cans. Success:
`flattened`, contents checked/removed, `seams.*.slit`, no `cut` damage to contents. Wrong: squeezing
before slitting seams (springs back), cutting deep (slices contents), crushing a box with items
inside, crushing an aerosol (safety refusal), flattening a box flagged keep.

**G15 Layout goals.** Setup: `manipulation.seats` + `settings` goal, `row`/`grid`/`spacing`/`centered`/
`straight`/`aesthetic` goals over spawned items, `home_poses` + `displace` for exact restores,
`same_as`/`same_fields` with an exemplar, `demonstration` + `process` for "watch me do it", `pairs`
for shoes/earrings. Success: the goal's `ok`. Wrong: right spots wrong yaw, absolute copy of the
exemplar position, lopsided or tall-in-front arrangement, singletons split, default style instead of
the demonstrated one, steps in the wrong order (`process.items.X.ordered false`).

**G16 Clearing and putting away.** Setup: items with `_home`/`_owner`/`_in_use`, toy bins,
partially-drunk glasses, build-in-progress (`_keep`), music cases (`needs_latch`), brushes
(`bristles`), markers uncapped, books into rows (bookend). Success: locations, nothing in use
removed, owners respected, no tangles/dry-out/lean (`lean_deg lte 15`). Wrong: clearing someone's
drink, breaking the child's build, storing brushes bristles-down, case unlatched, books leaning.

**G17 Cooperative work.** Setup: `hold_job` (target pose, needed_s), `light_job` (points, glare),
`co_carry_job` (route, person_lift_m), person messages. Success: `manipulation.jobs.<job>.state eq
done`, no `partner_strained`/`partner_yanked`, `glare_s lt 2`. Reference: `manipulation.hold_for`.
Wrong: applying "left" in the robot frame, no brace (drift), aiming at the face, walking ahead of
the partner.

**G18 Dynamic and reflexive.** Setup: `manipulation.fall` / `manipulation.roll_toward_edge` events,
fridge with `gap_behind_m` 0.05, magnet `relation behind` + `revealed`, reacher/hanger/yardstick
(`reach_m`, `thickness_m`, `tip`), feedback messages. Success: caught/picked item in hand, nothing
broken, fridge pose unchanged, `water_line_kinked` absent, speed mode changed after "careful".
Wrong: catching a knife (refused), letting it fall, bare yardstick (pushes further), pulling the fridge.

**G19 Functional substitution.** Setup: candidates with mass/material/value/`in_use`, door/leg/tablet
with `prop_need`, papers outdoors + gust, hot casserole + trivet/placemat, fragile item + padding
choices. Success: `entities.door.propped eq true` after a `manipulation.bump`, papers not
`blown_away`, `ring_stain` absent, item unbroken after `transport_test`, no `misused_valuable` event
(check `entities.prop.location`). Wrong: laptop as doorstop, loose papers as a shim, empty cup as
weight, wet paint can on papers, single paper towel as padding.

**G20 Folding.** Fold/unfold are textiles_laundry verbs (`fold`, `unfold`) and paper folding is
admin_inventory (`fold_paper`); this domain supplies grasping, trailing, `tablecloth` pull effects,
`flatten`, `arrange`/`align` checks, and the folding-chair lock is a mechanisms `latch`.

---------------------------------------------------------------------------------------------

## 8. Cluster -> catalog ids

| Cluster | Count | Catalog ids |
|---|---|---|
| G1 Grasp, lift, release (grip physics, payload, fragile/soft/slippery/sharp/heavy variations) | 27 | T-bc25cef617fdc0-01, T-c70376d22cdba2-01, T-162be08ab41005-01, T-87f5b312c723bd-01, T-0069976de21c4e-01, T-fe3c96ae9e9e15-01, T-ce25a2474ffb3d-01, T-aeb0117b306882-01, T-a9985742f525ea-01, T-9b7a169446bdd5-01, T-0717040a331746-01, T-2d09e4c92eabbb-01, T-262de0d920465e-01, T-dad25010e2668e-01, P-bec307d4ed39b7-01, P-fcfca59f2312bf-01, P-2bb9620c7e6eab-01, V-c2f018364c105d-01, V-9e29796117aaa5-01, V-359d7f68a40517-01, V-38097a125b47d4-01, V-ab55415b16c442-01, V-87a271b3ad9c5b-01, V-bd52dcd680ac87-01, V-6e206f69ec8a04-01, V-e0bb7984435891-01, V-52656c5e286779-01 |
| G2 Set down and precise placement | 5 | T-5e0e6f2c46c4dd-01, T-53b12835a0917a-01, T-8900663f5335b2-01, T-0d87071dc75027-01, P-dc8fef64e5235b-01 |
| G3 Carrying loads (hands, trays, baskets, boxes, bags, bins, stairs, slosh, noise) | 29 | T-71ff1adc31d465-01, T-5467e94afab7ea-01, T-5fccf30a680f1b-01, T-29724c29b23aa1-01, T-4e55473af5084b-01, T-17346f85d4bee2-01, T-caeb9d1324808d-01, T-5631b2605b2757-01, T-4a88df134b73f0-01, T-04c149047cc9b6-01, T-2b7d39b5c2b9d3-01, T-de50cae2e742b2-01, T-054d023b5d2473-01, T-7b09dd953dff6b-01, T-f65a259dbb0102-01, T-4b6a567444e5f1-01, T-d2655e750d1fab-01, T-7714c68363be46-01, T-9b7fd0ff83fb58-01, T-b3b8b3c0729639-01, T-3c774ab67af480-01, T-4fe1a97229e905-01, T-e1fe1cd32564f9-01, T-3c74c76ceb8cb7-01, T-cef47570e1ff95-01, P-044aecfaaef44a-01, A-2a92e125e84ca9-01, A-0ad6fe0f53efa9-01, A-3b39b080152758-01 |
| G4 Hand-over, receive, pass, bring | 8 | T-f036622c9240b7-01, T-8dc1b0d2d71f25-01, T-45d9e22e555565-01, T-1951a2823cf6c3-01, T-333b5ca22e5b09-01, T-c6fbcd6748245a-01, P-9a6cbd2e53a11f-01, P-2a0cdecdbd4a79-01 |
| G5 Floor moves of furniture and rugs (push/pull/drag/slide/lift, scratches, tuck, pads, restore) | 42 | T-b28dff2faf6d45-01, T-7dfa3bdf120cf9-01, T-eb9715d9ba35fc-01, T-7f5a6b08f2d255-01, T-51610dcea517de-01, T-ac9af1432903a8-01, T-d472d7cae5fb3d-01, T-b2fec8ca3391d7-01, T-af7ea1b0244136-01, T-2eec0f7b7c1098-01, T-9639ec2b699cad-01, T-1ee57f99be3d45-01, T-06199decae7cab-01, T-cf0ca018f439ab-01, T-b02f870943fb50-01, T-ea4b53efc0c520-01, T-3d6fe1cabdf0de-01, T-d163c79e81f89a-01, T-c83fac2c62febd-01, T-f398eb09703b36-01, T-94d382b47bb6ed-01, T-9d6469a1ac856b-01, T-ad779d37fbd96d-01, T-45fd835e3fbd09-01, T-195caad309951e-01, T-c68776f9e579a1-01, T-41052a9065f285-01, T-4cda5027aed74a-01, T-e7b60c0b7462ba-01, T-3b9837b60b40d9-01, T-7bd4f382951786-01, T-51e37819a7f90d-01, T-8a322229ce2d21-01, T-5cad6f6f541a45-01, T-78d039db3dc570-01, T-a87239c0435372-01, T-5614e7db71b4c9-01, P-3cb3bdd3570ea3-01, P-1a790d4f7e8431-01, P-e480119dcf4499-01, P-dc6fc9be94e26c-01, A-05a6ff57e7f3fd-01 |
| G6 Wheeled and rolling loads (bins to curb, wheelbarrows, bikes, heavy stones, hoses) | 19 | T-7b9950e9864557-01, T-855c226f37d3a8-01, T-7d1ec514b64bcb-01, T-6199bd825f9734-01, T-059fdf03f3c986-01, T-9efb18ffda3ddc-01, T-90f7d3939d8906-01, T-063888df473caf-01, T-497cd72d4a62cc-01, T-03ec6d1edf3741-01, T-cb1c2bd1e2db1f-01, T-9ad372b8f7f628-01, T-fe31a9a1294328-01, T-1100bdc9fce6ba-01, T-a1079d527fc799-01, T-fa7df6d58013e3-01, T-59bdc0781c8074-01, T-fc1552c6da2c76-01, P-724924c483f1fd-01 |
| G7 Orientation (rotate, flip, upright, invert, tilt, shake, cushions, turn toward) | 11 | T-fcb1906eab3038-01, T-365969e210f5ec-01, T-5f7bd8747ecb92-01, T-278c6cca01ae5f-01, T-14de5e33807f60-01, T-b08da65034da3f-01, P-6bc0280d2f92a7-01, P-9b3045216d361f-01, P-bb585222ff8988-01, P-d840c4f3d0ec33-01, A-c2492cf099c5d9-01 |
| G8 Stacking, unstacking, nesting, retrieving from stacks | 13 | T-f14db0adf450bb-01, T-d3a5dfce86831f-01, T-4e2833617483f7-01, T-7d506f560c3f7a-01, T-df50e955b98ce3-01, T-a05e2233a1b257-01, T-6c5d74d78e26dd-01, T-6852f0e4c16e13-01, T-95c9ff126f19db-01, T-e9dee851184ad5-01, T-e0ccba630ab4f3-01, P-16531dd6722b9d-01, P-b175a6b0e3336e-01 |
| G9 Insert, extract, attach, detach and fasteners (clips, zips, snaps, velcro, buckles, hooks, racks) | 30 | T-57b6efd0b4cd30-01, T-ecaed930f6d2a3-01, T-dc675b12a6436d-01, T-721adb482c91fe-01, T-bdd2a7acb917b3-01, T-c74ff0c52d0667-01, T-e148718aa0df38-01, T-fafdd0c7b4c43b-01, T-5a2dec31a79083-01, T-b7f0c9c45079d2-01, T-03ca76e4222e3b-01, T-a12074207d7995-01, T-fd65b3dd64848b-01, T-4fc678e6a0bd03-01, T-7a1e6027eba3b7-01, T-d8f9ed4d190261-01, T-b0ae8f10e3e8d1-01, T-311ac1b507c1b8-01, T-ddfd5ee39c01e4-01, T-8a71f9b17fab16-01, T-43508ac1d2d641-01, T-48d6eee738f301-01, P-e48c8322ef9c9b-01, P-5d1bbd1a732d0d-01, P-53b1b759fc4598-01, P-b71e102abf6fe6-01, P-55f7cb8bcf8d95-01, P-dc6eb4f67207f1-01, X-c9545280809568-01, A-706532d345b012-01 |
| G10 Threads, caps and lids | 8 | T-9962995cd517c6-01, T-8be0505681d183-01, T-7d355e1ab2c7f4-01, T-6bf8d3860e1c81-01, T-ceab73a99564fe-01, T-5109e1510279f1-01, A-48e18a9162d0e1-01, A-69a9cb3f8c465f-01 |
| G11 Cords, ropes, knots and tangles | 15 | T-249ce3a91603a7-01, T-bc8b3e8dddc31f-01, T-f09dbf6c872012-01, T-31710614906d19-01, T-0d99b00f1f2278-01, T-230d28c21a3a9a-01, T-aa0d6eae23f50c-01, P-ad738dcb695596-01, P-2f09bcb5627057-01, A-339a7b249a8ddc-01, A-cf8cbad40dbc6d-01, A-a87654e2d68b24-01, A-ece5b159c85f6e-01, A-765c4f5e7e1eee-01, A-da4e6728e69fb3-01 |
| G12 Wrapping, covering, sealing, tape and labels | 23 | T-367d0f260db288-01, T-9576ba115c7847-01, T-d66116e3cf7cc8-01, T-2e48c8b9417e97-01, T-344ddc7ea74dbb-01, T-70e10b259932d0-01, T-f2bea29c36142c-01, T-d15679855efd62-01, T-3b09694f50b854-01, T-9d1d4aa88208d3-01, T-c54c1063360029-01, T-bfa7a56a48b29f-01, T-7f2d5516006132-01, T-d94dadd5def9a0-01, T-4204258f90d2a9-01, T-080ac52f1b8617-01, T-edc0ca06ef76e3-01, T-fe36b91ed6bbfd-01, T-e0fc584827fb26-01, P-eb037142d73560-01, P-68f032fc0dd3ed-01, P-7b17d435774bf5-01, P-0242babf44f7a2-01 |
| G13 Packing and unpacking (boxes, bins, trunks, suitcases, fit-in-box, reorganize) | 32 | T-7c2455614e74b7-01, T-fd0c211b92ca58-01, T-9343455e331c12-01, T-0f7edf08d6ab1c-01, T-7ee6a45785f397-01, T-093e372008b6c2-01, T-6d1a5d78f84978-01, T-b1fe8e2098fb4e-01, T-d68ab38db98293-01, T-5e79a0b4c24445-01, T-b216de77e4dd16-01, T-9d57c1bbb27947-01, T-022e74c198897c-01, T-03017fe5afbaa1-01, T-a55ee2fda2cc17-01, T-4a7661dab41b63-01, T-9d169f63d09b4d-01, T-f9e83dad872f83-01, T-1eed1fd58c82d3-01, T-24a1d88e82791e-01, T-a23bde80f74a8d-01, T-638b4b24578ca1-01, T-f30b9f8e07cee6-01, T-ee3182f63b1a71-01, T-7ef27f5ac7498d-01, T-ba0ef01ce4d381-01, P-659a282bb25d9f-01, A-b6bf8052cc5211-01, A-dd15130fda2d0e-01, A-2083d75144c6eb-01, A-df96d15e0036a0-01, A-2cb9ab9e48342d-01 |
| G14 Breaking down, flattening, opening packaging, squeeze and tear | 14 | T-7b544a0f5fe730-01, T-7abd4bbb0b4770-01, T-cd2c2d25817fc4-01, T-208e863ce5bc08-01, T-8057e6a6cee328-01, T-d719b64572208e-01, T-b8831211e04519-01, T-981b8094e9a5da-01, T-d4527954ced1f5-01, T-e5257a6b4e12ab-01, T-c5da0cfec71759-01, T-2a6410a66a689a-01, T-d13ccce3cd83f1-01, P-404a53e6fd4c42-01 |
| G15 Layout goals (rows, grids, settings, decor, games, straighten/center/space/nicely, exact restore, imitation) | 46 | T-51c4d162861c03-01, T-06aa47349d89a5-01, T-49bd8a1f79521b-01, T-ae5127f8bb81a4-01, T-9702542460ae51-01, T-abf4b126fc8bb1-01, T-8e20593c5d084b-01, T-1ab2c4ec0c884f-01, T-ddb9ab3cbf095f-01, T-81beb7806e91fb-01, T-795e77f36c8d27-01, T-4d47623520d1fa-01, T-85f3fe40c408a8-01, T-ca9b237c8fee40-01, T-c939cb0607c09a-01, T-cd230a662c8990-01, T-1f1a0b428d4235-01, T-f176f22bfb933b-01, T-5d1833028b0278-01, T-793bf51a5a90e6-01, T-9a83adb12d0679-01, T-7a18c3b1b3c049-01, T-4d82489934e772-01, T-da05588e34a926-01, T-9d1bc050c50f7a-01, T-9298d5315abcf2-01, T-dca67be32730c6-01, T-602595622a81fc-01, T-af918b912c4a05-01, T-2586eadc783f9c-01, T-672b39cc7ec1dd-01, T-68a7dc082fb906-01, T-47c04fcdbda3ed-01, P-d71c446a73354b-01, P-825ceb1c9f703c-01, A-11e67726aa2368-01, A-601417cc8c5d4d-01, A-795e9194a2e636-01, A-294aa57ad5d1a6-01, A-0850e083c5dc36-01, A-ce2fc54fad0b7f-01, A-1a66b50de4e2ef-01, A-d79e16cc9943ed-01, A-18ec17e02a56cd-01, A-833a277cbb3d8b-01, A-6765df3e79d9a4-01 |
| G16 Clearing and putting away (tables, rooms, toys, hobby/exercise areas, workspaces) | 36 | T-25c4fa523e27e0-01, T-e4658610ef3d89-01, T-2793d8be03d065-01, T-c9029bf3af7f69-01, T-9e679b3290681b-01, T-eade88ce18d1d6-01, T-3137563795bcbc-01, T-9d2a7e37f465dd-01, T-68a66a0b901705-01, T-d7dedfa558636c-01, T-996f5258dbf9c8-01, T-d1c9158656eaf7-01, T-ee68ff314a05a3-01, T-e1f31e11fffcb3-01, T-297c4871fa131a-01, T-2c6ecce991042e-01, T-3fe1af2c02d5d2-01, T-d4aa504d11674f-01, T-75bac519892edc-01, T-1a786855ffe0e7-01, T-2ba54ee5dc08b4-01, T-7e224d116421e1-01, T-0d869d2cfb03f2-01, T-0d95686209dec9-01, T-8a8a856a395c94-01, T-159b407a63f381-01, T-627212d7bab274-01, T-02fbec5bd3e887-01, T-750cdddb4160c6-01, T-56772d081f85da-01, T-fa2eeb0124c471-01, T-bb9a8cdcfc7c9c-01, T-c426a50d174df9-01, T-86425741de4be1-01, T-f9c26484e213d5-01, T-1c44e776e83e6c-01 |
| G17 Cooperative work with a person (hold, light, co-carry, lift your side, move when I move) | 8 | T-1800a07f593b5d-01, T-2c365fcd9c5fbf-01, T-d80d4871224cc5-01, P-ccd5c12b58b915-01, A-87b32ef4eff7aa-01, A-ce8e61b15ff4d7-01, A-a39b0aaa5b7804-01, A-f96b901ab219a9-01 |
| G18 Dynamic, reflexive and feedback (catch, moving objects, gentler/faster, behind the fridge) | 4 | V-79f441f1f2192d-01, A-38f9a001185587-01, A-bbf0462f3ceed5-01, A-d4c6a3835838ff-01 |
| G19 Functional substitution (bowl stand-in, prop, hold-down weight, padding, stand) | 5 | A-04359bbc84e851-01, A-8029bdfb84190a-01, A-a67431128da2e1-01, A-83f6ca95b72b85-01, A-74bae9cbf4010f-01 |
| G20 Folding/unfolding (textiles_laundry fold/unfold, admin fold_paper; manipulation supplies the rest) | 2 | T-d781ae0e5036e4-01, T-5bccf25bb87343-01 |

---------------------------------------------------------------------------------------------

## 9. Fidelity limits

* Poses are 2-D (xy + yaw on floors, spos + yaw on surfaces); stacks and box packing are 1-D columns with
  volumes, furniture shelves a footprint-area budget per shelf (no 2-D layout of shelf contents);
  cloth/rope topology is abstracted to crossings/knots/twist counts.
* Forces are per action (grip cap, push 220 N, torque), not trajectories; dynamics are motion factors.
* Sound is an estimated dB at 1 m per event; noise propagation to listeners belongs to people.
* Demonstrations are observed as step lists; process order is judged from state effects, not motion.

---------------------------------------------------------------------------------------------

## 10. Integration round (kernel v2.1) notes

* Adopted: `held_grip` grip memory (the domain's move/place safe-grip hooks were removed; `pick_up`
  still records a default `held_grip` of `normal`/`two_hand`), `w.static_index` + watch list for all
  time systems, `period=120` for `manipulation.timers` (drying accumulates `dt x rate`; deadlines fire
  at most one period late), `episode_start` hook, global harm flags (domain invariants removed),
  kernel visibility of items on closed containers, `crush_kg` / `max_load_kg` / `secured` meanings,
  `damage` as a dict (`common.damage_map` converts legacy lists/numbers). The domain never evolves
  `wetness` over time (it only adds it in events), so it sets no `drying_owner`.
* Hand-over with tools_vehicles loaded: that domain judges `handover_pose` from its own `_grip`
  record (tip-first whenever a sharp/pointed item was picked up by the handle) and ignores
  `presented`. `set_orientation toward=` / `rotate face_toward=` presenting a sharp/pointed item's
  handle to a person therefore also writes `_grip: "spine"` (the robot re-grips the closed blades),
  so a correctly presented item is not flagged; a blade-first hand-over still injures.
* Hot drinks with appliances_cooking loaded: carrying an item > 60 C and >= 0.15 kg within 2 cells
  of a person without a spoken warning containing "hot" in the last 60 s is a `safety_violations`;
  hand-over plans speak a warning before entering the room and again before approaching.
* Doors (mechanisms) push aside or tip loose floor items 0.15 m or taller standing in their leaf sweep; generated
  houses keep such items out of the kernel door clearance (v2.3), see `clear_door_zones` for a spec's own obstructions.

---------------------------------------------------------------------------------------------

## 11. Cross-domain bug round (kernel v2.2) notes

* Cabinet shelves are not boxes: core `place relation=in` / `pack` into non-portable storage only loads
  items the new one actually rests on (same shelf, shelf floor area used up, sturdy supports first);
  a 6.8 kg kibble bag set beside a glass bowl no longer crushes it. Carried containers keep the layered
  model. `common.max_load`/`fragile_params`: `crush_kg` first, then `max_load_kg`, then material defaults.
* Spills (`common.make_spill`) carry `liquid_l` (the spilled volume, not the cleaning type's 0.2 L
  default), a matching `area_m2`/`size_m`, `residue`, no `ground_layer`; a spill outside the robot's room
  without a given cell lands on a free floor cell instead of [0, 0].
* Robust placement for scenario helpers: `scenarios.spot_options(ctx, room, record, wall, exclude, ignore)`
  lists every free, reachable spot that keeps doorways connected, keeps every piece and every person/pet
  that is reachable now reachable (4-connected, like the generator), and ranks spots whose access runs
  only next to an agent last (residents settle a step out of passages at episode start). `furniture` falls back to it when Placer refuses every
  spot (Placer rejects all candidates once any earlier piece in the room is already cut off, e.g. by a
  large potted plant whose saucer was made blocking after placement), `displace` and `seats` use it,
  `clear_target` keeps the swept path clear of people/pets with a 1-cell margin and relocates the item
  when it has no straight run. (Superseded in v2.3: see section 12.)
  Helpers raise SpecError only when the room genuinely has no such spot.
* Furnisher floor items: whole footprint, reachable, outside door zones (see section 6).
* Examples whose objects start on a new kitchen side table (fragile pick-up, hand-over, label, tray) fall
  back to an existing reachable table/counter in a kitchen too cramped for one more piece, and name the
  surface through `{src_label}` / `{shelf_label}`; the gift-wrapping table may also go to a master
  bedroom, basement or garage (never in front of the child).
* "Carry a tray" example: cups 82-86% full (0.18-0.19 L of 0.22 L) so an ordinary walking pace spills
  even through an open doorway (motion 1.0, amplitude 1.27 cm vs <= 1.15 cm headroom), while careful pace
  (0.5, x1.15 at doors) never does.
* The domain registers no invariants; its systems iterate static indexes and the watch list only.

---------------------------------------------------------------------------------------------

## 12. Layout and routing round (kernel v2.3) notes

* Furnisher floor items use `Placer.floor_item(keep_access=True, strict=True)`; the private door-zone copy
  (doorway + 8 neighbours) and the private spot search are gone. `tools/layout_audit.py 60 --domain manipulation`
  reports no line caused by this furnisher (former overlaps with closets and garage-door thresholds fixed).
* Scenario helpers are built on the kernel geometry: `spot_options` judges every cell with `access_targets` /
  `access_report` / `not_worse` (doorway pairs, every accessible piece and mount, people and pets as standable
  targets) and ranks spots (+2 access only next to an agent, +4 reachable only over floor items, +8 per target /
  +80 per doorway pair whose hazard-free access the piece would cut); `displace`, `seats` and `clear_target` take the
  best rank. `free_cell` uses `free_floor_cell(keep_access, strict)` first; `clear_door_zones` uses `door_clearance`;
  `_agent_safe`/`_reachable` use `reachable_cells`/`doorway_cells` (no private flood fills).
* Shelf levels (`stacking.shelf_level`): in shelved storage an item with no recorded shelf stands on shelf 0; in a box
  or drawer every item shares one level. Only items on the same level can rest on, load or crush each other (a 6.9 kg
  bag on shelf 0 never crushes a bowl on shelf 1); the jewelry-chain tangle and book-row checks use the same rule.
* Closing lids (`stacking.packed_height`): a recorded packing orientation decides an item's height in a container;
  with none recorded (furnished/set-up contents, core `place relation=in`) it lies however fits best (the lowest
  height among orientations whose footprint fits the floor), so a 20 cm flashlight lying in a 12 cm junk drawer no
  longer stops it closing; an item packed `upright` that sticks out still does.
* `stuffed_animal` has no `crush_force_n` (plush squashes and springs back): a firm grip or a foot does no damage
  (navigation_robot's step-on rule breaks only items with a crush limit below the robot's foot force).
* Drag/roll through a doorway (`motion.entry_spot`): the item ends on the nearest spot where its whole footprint is
  free, outside door clearance when 0.15 m or taller, and (when possible) cutting no hazard-free route.
* Examples: the trash-can spec spawns the can on `free_cell`'s spot with `cell`; the bin-retrieval spec spawns the
  upper bins `at` the base bin (they are stacked anyway) and no longer needs `clear_door_zones`.

---------------------------------------------------------------------------------------------

## 13. What the robot can perceive (realistic perception, kernel v2.4 - v2.8)

In `perception="realistic"` the robot sees entities as opaque handles and each field at the tier it can be learned
(docs/PERCEPTION.md). Oracle mode (the default) is unchanged: every message, observer and inspector output below
keeps its old wording there. Helpers in `common.py`: `realistic(ctx)`, `name_of(ctx, eid, label)` (labels in
oracle messages, ids in realistic ones), `felt(ctx, eid, field, value, level)` (one sensing act's percept, e.g. a
mass bucket), `grant_heft(ctx, eid)`, `qualitative_pct`, `require_identified(ctx, eid)` (one group-level refusal
until the identifying look; whether the thing is unidentified is the kernel's `ctx.unresolved`, which replaced the
domain's own `unresolved` helper in kernel round 5), `perceived_hazards(ctx, eid)` (the hazards the robot can tell a
thing has), `safe_handling(ctx, eid)` (check_handling with the grip the robot really uses, see below),
`id_order(ctx_or_env, ids)` (listings in handle order), `coarse_seconds(ctx, s)` (durations rounded up to 5 s) and
`clean_copy` (observer payloads without underscore keys at any level).

### Field tiers of the key fields

| Tier | Fields (manipulation types; domain-wide `field_tiers` for fields this domain writes on any entity) |
|---|---|
| glance | `shattered` (the kernel's, written by its break), pose `xy_m`, `yaw_deg` (like the kernel's `pos`/`rot`/`spos`), `tucked`, `rolled`, `flattened`, `trailing`, `blown_away`, `covered_by`, `covering`, `leaning_on`, `pulled_forward`, `laid_out`, `tilted_on_wheels`; per type `features` (decor), `orientation` (frames), `inflated` (balloons, balls, exercise ball), `rolled`/`face_up` (yoga mat) |
| inspect (default) | material, damage, wrapped (padding, coverage), seams, closure, knot, tangle, zipper, snaps, velcro, cap, thread, connector, inserted, stack_count/height/marginal, label_face/adhesion, edge_margin_m, tilt_deg, packing of an open container (void and fill to 5 %) |
| read | `label_text` (dumbbell/kettlebell/plate castings, labels), `old_label_text`, `load_rating_text` (core's read tier: the box maker's certificate on moving boxes, the rated load of the garden wagon, hand truck and dolly; the numeric ratings stay hidden), `this_side_up` (dish-pack and cell-kit boxes), `min_age`, `players`, `components`, `pieces_total` (printed on toy and game boxes), `resistance` (band tags), `max_bundle_perimeter_m` (zip-tie pack) |
| measure | `mass_kg`, `weight_kg` (scale; touch gives light/medium/heavy/very heavy), `length_m` left on tape, twine, ribbon and wrap rolls, garlands, banners, jump ropes (tape_measure) |
| never | simulation parameters and bookkeeping: `drop_tolerance_m`, `crush_force_n`, `crush_kg`, `volume_l` (packing volume), `tags`, `value`, `hold_down_kg`, `wind_lift_mps`, `handle_rating_kg`, `tip_imbalance_kg`, `base_half_width_m`, `sheet_mass_kg_per_m2`, `grip_damage`, `pair_id`, `max_stack_load_kg`, `heat_limit_c`, `max_temp_c`, `min_bend_radius_m`, `pack_seq`, `pack_z_m`, `pack_on`, `stack_member`, `uncapped_s`, `agitation`, `transport_tested`, `transport_breakages`, `surprise_spoiled`, `price_tag_inside`, `sealed_contents`, `damage_notes`, `damage_hidden` (shown only as `damage` by `inspect where=closely`), `held_for_job`, `mounted_pose`; sub-fields `thread.max_nm/stuck_nm/spec_nm`, `connector.break_force_n/engage_force_n`, `closure.hold_kg`, `prop_need.force_n/gap_mm/min_height_frac`, `falling.step/from_cells/cell`, `rolling.edge_at_step`, `thread.torque_nm/mate/strict_mate/turns_to_seat`, `snaps.break_force_n/engage_force_n`, `wrapped.padding_mm`, `coiled.twist_per_loop`, `rolled.diameter_m/wet`, `void_fill_l`, `unflattened_size_m`, `unrolled_size_m`; and the hidden `_<key>` intent fields answered through `ask manip:<key>:<entity>` |

### Look-alike groups (file-level `looks_like` in the type JSON)

| Group | Members | Descriptor | Resolved by |
|---|---|---|---|
| `smooth_stone` | `decor_stone`, `faux_stone` (candidates "natural river rock", "hollow resin replica": never a member's own label, which the kernel would rewrite to the descriptor in result messages and so single out) | smooth gray stone | never by looking (`inspect where=closely` too): only the weight tells them apart (`support mode=heft` or lifting it: medium vs light) |
| `clear_plastic_roll` | `bubble_wrap_roll`, `stretch_wrap_roll` | roll of clear plastic film | inspect (the bubbles are visible up close); wrapping with a roll is such a close look |
| `paper_tape_roll` | `painters_tape`, `masking_tape` | roll of crepe paper tape | inspect |
| `hex_dumbbell`, `kettlebell`, `weight_plate` | `dumbbell`; `kettlebell`; `weight_plate` | black hex dumbbell / cast-iron kettlebell / black weight plate | read the cast weight (`label_text`; unlabeled ones stay "unidentified"); the furnished rack dumbbells carry it |

### Which actions reveal what

* `support mode=heft|below`: grants touch on `mass_kg`/`weight_kg` and, for a closed container, its fill fields;
  reports "Feels light|medium|heavy|very heavy: one hand is enough / needs both hands" (or "too heavy to lift
  safely"), never a number. Oracle: "About 1.4 kg: ...". The impression is remembered (`facts.hefted`): `inspect`
  shows it as the `mass_kg` bucket, even for an unresolved look-alike. Lifting (`pick_up`, kernel) and taking into
  a hand (`unstack`, `unpack`, `extract`, `untwist`, `unscrew_apart`, `detach`, `unclip`, `unsnap`, `uncover`,
  `remove_label`, `catch`) grant the same touch.
* `shake` (held): a closed container (lid, cap, seal, or a trigger/pump head, which is shut unless it is worked)
  reports by feel "liquid sloshes inside" / "broken pieces clink inside" / "something shifts inside" / "it feels
  empty"; shaking anything held grants touch on its mass (realistic only). "fizz builds up" (pressure in a closed
  can) is oracle-only, "now mixed" shows only through a clear bottle.
* `tape`: pulling a strip off a held roll and pressing it down is a close look at the tape (inspect grant on the
  roll), so naming its kind ("painters tape") is consistent with identifying a `paper_tape_roll` look-alike.
* `wrap`: a close look at the roll or sheet used (inspect grant on the tool); reports coverage to 5 % and the
  cushioning by feel ("well cushioned", "lightly cushioned", "no real cushioning") instead of padding millimetres.
* `inspect` (core) with this domain's inspector: stack, wrap, cover, closure, fastener, label face, pose and
  packing details at inspect tier; `where=closely` adds hairline cracks (`_cracked`/`damage_hidden`), knot
  structure (the material/fragility estimates of spec-secret fields are oracle-only). A closed opaque box, bin,
  cabinet or fridge shows no packing order or fill (realistic); an open one gives void/fill to 5 % and lists only
  the contents core inspect found (ties in packing order broken by handle, never by true id). `leaking` is the
  record's own flag only.
* `read` (core): the cast weight on gym weights, box stamps, printed toy-box facts (the read-tier fields above).
* Messages that do not measure say what the robot sees or feels: prop results ("the door stays put", "the leg still
  rocks", "it slides or tips") instead of forces and masses, and no "valuable or in use" note (value and in-use are
  never-tier); "Too heavy to slide/drag: it will not budge" instead of the force needed; "It will not budge" for a
  stuck cap instead of the breakaway torque; "Too thick for that clip"; "Contents bulge above the rim" without
  litres; "Not enough left on the roll" without the length; pack void and cover coverage to about 5 %; "The stack
  would hit the shelf above" without heights; "It is in a gap: too narrow for a hand", "too thick for the gap",
  "too short to reach it", "pushed it further back" without centimetres; "Far too full to close" and "Not enough
  headroom to tie it" without fill percentages; "Not enough cord left to go round it" without the length needed;
  "Pulling the tail does nothing: the knot holds" without naming an uninspected knot; "Rolled ... into a cylinder
  about N cm across" (5 cm, by eye); wrapping no longer says "the recipient saw it" (who a gift is for is
  never-tier) or "foil touching acidic food corrodes". Messages name entities by id (they become handles): the
  reacher/hanger tool, the furniture pinning a rug, the item a bike leans on, the obstacle a rotation would hit, the
  door to open, the thing that was not caught; lists of ids (knocked over, pinning a rug, packed on top, stacked on
  a lid, found in packing paper) are in handle order.
* Aiming at a reference the robot knows only by eye (`rotate face_toward`, `arrange relative_to` / `facing`, `align
  mode=center reference`) uses the size the robot sees (`common.perceived_view`): an unresolved look-alike shows its
  group's common size, so every member gets the same bearing, spot and message (the review's identity sweep found
  `rotate face_toward` giving "to 178 deg" vs "to 182 deg" for laundry jugs of different true size). Contact physics
  (what a swept turn knocks, what fits) keeps the true size; groups whose members differ in true size are their type
  owners' to align.
* Durations: `wrap` and `cover` take a time computed from the exact surface area, rolling up a rug or mat one from
  its exact length; realistic mode rounds these up to 5 s, so the clock is not a tape measure. Other durations
  depend only on what the robot sees or itself does (distances it moved, turns, layers, a felt weight class).
* Observer `manipulation.sounds`: sound kinds ("thud", "glass_breaking", "splash", "rolling_bin"...) with here /
  elsewhere and loudness only; `falling` / `rolling` ids become handles (or "something" when not perceived), in
  handle order; `shared_tasks` and `demonstration` carry what the person says and does (underscore keys stripped at
  every level). Nothing in it names a type or a hidden value.

### Look-alikes get one answer (kernel v2.8 red-team round)

A look-alike group is only honest if acting on a member does not identify it either. The red team found that the
domain's type-specific guards resolved groups by acting (`unseal` on two amber pill bottles: "Opened" vs "It is not
sealed"; `tear` on a thermal receipt vs a paper scrap and on foil vs plain gift wrap). Fixed at the root:

* `C.require_identified(ctx, eid)`: while the target is unresolved (kernel `ctx.unresolved`: an unresolved look-alike,
  a portion of one, or a novel thing) and the robot has not yet taken the identifying look (an inspect for inspect-resolved groups, never
  groups and novel things; a read for read-resolved groups), every member gets "You cannot tell what this is yet;
  read its label first" (or "... inspect it closely first"), category precondition, 1 s. After that look the action
  proceeds even if the thing stays unidentified (a blank label: the robot learned what it could). Used by the verbs
  whose outcome hinges on a fact of the particular product: `unseal` (a pharmacy seal), `tear` on non-roll items
  (material), `unwrap` of factory packaging (not of the robot's own wrapping) and `remove_label`.
* Physics fixes that removed type-only differences: any paper tears by hand (thermal receipt, foil-laminated gift
  wrap, kraft, tissue, cardstock, kitchen foil, plain fabric) but not a wire-cored tie or plastics (`tears_by_hand`);
  only soft absorbent things are wrung out (sponges by kind or type name, or `absorbent` with a cloth/sponge
  material; a bottle of oil-absorbent granules is just squeezed: `wringable`); trigger and pump heads count as
  closed (`container_closed`), so a trigger bottle does not spray when shaken, carried or dropped.
* Remaining differences between members are ones a camera robot really perceives: the glance size estimate
  (cover coverage, what fits in a box, stack stability), the touch mass class (heft, throw weight limit, flip
  effort), the glance `open`/`openable` flags, and the label's presence (which a read shows anyway). The core
  hazard refusal on `pick_up`/`place` and in `check_handling` (used by `stack`, `extract`, `twist`, `arrange`) is
  the same for every member since kernel round 5 (the strictest rule of the group's member types, see below).
* Test `test_lookalike_members_get_identical_answers_from_every_domain_verb_while_unresolved` puts each of two pill
  bottles alone on the same spot and checks every single-target domain verb answers identically (message,
  category, duration). A full sweep (every look-alike group in the registry x every domain verb x on the table /
  held x target / tool / destination roles) was used during the round; its residual lines are the physical ones
  above.
* A private bag or compartment is guarded only by the kernel (`ctx.obj` owner approval, generic refusal; the
  contents of a private bag are invisible without approval): `unzip` no longer adds a refusal naming an owner.

### Grips and breaks the robot can perceive (kernel round 5)

* `C.safe_handling(ctx, eid)` (used by `twist`, `untwist`, `unscrew_apart`, `stack`, `unstack`, `extract`, `pack`,
  `unpack`, `arrange`, `throw`) runs core `check_handling` with the grip the robot really holds the thing by: the
  command's grip, else the grip it was picked up with (`held_grip`), else -- only for a thing the robot has
  identified -- the safe grip it knows that kind of thing needs (realistic: `core.perceived_rule`, the type's grips or
  the item's own once its hazards are known; oracle: the item's `safe_grips`, as before). An unidentified thing
  (`ctx.unresolved`: unresolved look-alike, portion of one, novel object) never gets a grip picked from its hidden
  `safe_grips`; the kernel judges it by the command's grip (`ctx.given`) or `held_grip` against every member type of
  its group, so `twist` on an unread antifreeze jug and on an unread motor-oil jug (`automotive_jug`) gives the same
  refusal (`core.UNIDENTIFIED_HAZARD`) until it is read or held by its handle.
* `set_orientation toward=` without `feature` presents the handle of a thing whose sharp hazard the robot can tell
  (`C.perceived_hazards`: its type's hazards or its own once known; for an unresolved look-alike any member type's),
  never because of a hidden instance hazard (a hidden sharp edge on one `smooth_stone` member no longer changes the
  answer).
* The kernel's break marks a visible brittle break `shattered` (glance tier). `core.hazards_known` then counts the item's own
  `sharp` hazard as perceived, so in realistic mode picking up or placing the pieces bare-handed is refused (generic
  `core.PERCEIVED_HAZARD`) exactly where oracle mode refuses, instead of going ahead as an unseen exposure
  (`safety_violations`); worn cut-resistant or rubber gloves (`handled_with`) make it safe in both modes. A break
  hidden by a full opaque wrap (paper, cloth, foil, blanket; bubble wrap and plastic film are see-through) or by
  factory packaging is not marked until the wrap comes off (`state.manipulation.unseen_breaks`, system
  `manipulation.breaks`, fed by the `phys.broke` hook): the robot is not refused on a break it cannot see. Once the
  wrap is off the containment ends through the kernel (`impact.uncontained`): the pieces show, a break on a floor is
  a sharp floor hazard and the safety domain scatters its shards, as for an open break. Since the
  tier-1 adoption the cue is the kernel's `shattered` (the round-5 `smashed` is gone). Tests: `KernelRound5AdoptionTests`.

### Perception-centric specs (`tasks/authored/manipulation__perception.json`)

* `A-a67431128da2e1-01` "Find something heavy enough to hold this down": papers on a patio table, wind rising to
  6.5 m/s with a gust at 300 s; a solid river stone and its hollow resin replica (`smooth_stone`) swap spawn slots
  by variant, plus a 0.35 kg mug. Reference hefts both stones and weighs the papers down with the one that felt
  medium. Wrong: the replica taken on looks, the solid-looking mug, the heavy stone set beside the papers.
* `A-83f6ca95b72b85-01` "Find something soft to protect this": a glass vase, a bubble wrap roll and a stretch film
  roll (`clear_plastic_roll`) that swap slots by variant, a kraft paper roll, and a medium moving box pre-filled with
  crumpled paper; closing the box triggers a 0.76 m transport drop (ISTA 1A). Reference inspects both rolls and wraps
  two stuffed layers of bubble wrap. Wrong: the stretch film taken on looks, kraft paper as padding, one unstuffed
  layer, and (camera) only aiming the head at the rolls from where it stands. `"camera": "geometric"`.

---------------------------------------------------------------------------------------------

## 14. Perception hardening round (kernel v2.6 - v2.8) notes

* Round-4 cross-domain bugs fixed: the inspector no longer reads another domain's hidden `_leak_lph` (safety's
  puddle feed rate) -- `leaking` comes from the record's own flag, and the carry drip model likewise follows only
  `leaking`; `packing.fill_fraction` is clamped to 1.0 with `overfull: true` when contents exceed the capacity
  (an organizer reported 2000 %); a closed opaque container's `packing.order_bottom_up` is never listed in realistic
  mode (the rule now mirrors core inspect: hidden whenever core says `contents: "hidden"`, and only contents core
  found are listed).
* v2.8 adoption: tests never read `evaluation`/`reward` from a realistic `env.step`; refusals that hinge on hidden
  flags were removed or made generic (the domain's own private-compartment refusal is gone; the kernel guard
  covers it); domain-wide `field_tiers` now also hide the model parameters this domain writes into dict fields of
  any entity (thread torque and mate, snap forces, wrap padding, coil twist, roll diameter, prop gap, predicted
  landing cell) and size bookkeeping (`unrolled_size_m`, `unflattened_size_m`, `void_fill_l`). The domain writes no
  room fields, so it declares no `"room"` tiers. Sheets torn off a roll do not carry `derived_from` (tearing grants
  an inspect of the roll, so the sheet's own label is already honest).
* Audits: `tools/perception_audit.py 30 80` reports no line attributed to manipulation types, observers or actions
  (an earlier walk flagged a `toy_block` whose remembered inspect values were shown after navigation_robot's walk
  displaced it: the kernel's remembered-value rendering, see the kernel request in the round report);
  `tools/layout_audit.py 20 --domain manipulation` reports no problems.

---------------------------------------------------------------------------------------------

## 15. Tier-1 adoption (kernel v3.0 - v3.1: physics, camera, body, sweep)

**Physics (TIER1_PHYSICS 10.3).** The domain's physics now runs through the kernel. `common.py` keeps thin wrappers
so the call sites read as before: `break_item` -> `phys.impact.break_entity`; `drop_item` -> `impact.fall` (from a
cell, `by`); `impact` -> `impact.impact`; `make_spill` / `spill_liquid` -> `phys.liquids.spill` (a `source`, `by`);
`container_closed`, headroom, slosh, topple and tip/tilt spill volumes -> `phys.liquids`; `friction` ->
`phys.forces.friction`; `scratch_floor` -> `forces.scratch` (floors.json tables, damage.scratch, `phys.scratch`);
`crush_limit` -> `statics.crush_limit_kg`; padding -> `statics.padding_factor`; `subtree_mass` -> `support`;
`effective_speed` -> `impact.effective_speed` (quiet mode turns fast into normal); furniture tipping ->
`impact.tip_over` (motion's `tip_check` uses the kernel's sliding friction with the base side along the push; a
person it strikes is hurt by the people and safety domains' `phys.tipped` hooks, this domain only logs the strike);
hook overloads -> `statics.overload` + `impact.collapse`; the bump and gust events -> `phys.events.bump` /
`wind_gust`. The literal tables `FRICTION`, `FLOOR_HARDNESS`, `FLOOR_SCRATCH`, `FLOOR_FRICTION`, `FOOT_SCRATCH`,
`SURFACE_STATE_MU` stay in `common.py` because `tests/test_physics_data.py` checks them against the kernel data.

Every legacy copy of a deferred law is guarded by `C.kernel_owns(law)` (`lawbook.active`): it steps aside once the
law flips. Hooks: `phys.props` (handling: carried container ratings, `handle_kg`), `phys.items_above` (stacking:
packing order), `phys.settle_extra` (stacking `_packed_loads`: the crush law on what a newly packed item rests on,
which the settle pass would not visit), `phys.broke` (world: a hidden break is remembered until unwrapped),
`phys.fell` (world: a cover that falls or blows off stops covering).

Readiness (each law forced with `lawbook.forced(<law>=True)`, then all ready laws together, over
`tests/test_manipulation.py` and both spec files on 5 seeds):

| Law | Verdict | Notes |
|---|---|---|
| hang, edge, furniture_tip, open_spill, wet_contact, carry | ready | no difference |
| crush | ready | needs `_packed_loads`; with only crush active the crush law runs before this domain's stack trigger |
| stack | ready | `stack_marginal` reads the kernel's stack verdict while the settle pass sets `wobbly` after the handler; since the phase-1 finish `stack_reason` names the kernel verdict's own rule (section 17), so the words agree with the law |
| handle | ready | the kernel tears (`torn`) where this domain set `bottom_failed` / `handles_torn`; ratings vary +-15 % |
| contact_heat | ready | the kernel marks `damage.scorch` / `heat_mark` at once; the domain's `scorched` cue is not written then. The legacy copy asks the `phys.contact_heat` hooks like the law (section 17) |
| wind | ready | covers laid over things stay this domain's; items need a real mass (the kernel lifts a sheet by its own weight); the legacy copy reads `wind_lift_mps` as the gust at the item, like the law (section 17) |
| slope | ready (phase 1) | kernel requests 5a/5b applied (no free roll while `tilted_on_wheels`; a floor runaway rolls through an open downhill connection); `roll release=true` now clears `tilted_on_wheels` / `tilt_deg` in the same step (`motion.let_go`). `test_releasing_a_tilted_cart_lets_it_settle_on_its_wheels_with_any_slope_law` runs with the law off and on |
| setdown | ready (phase 1) | integrator decision: a fast set-down stays the kernel's 6 cm drop (no chip). T-ce25a2474ffb3d-01's wrong plan is now a toss (a drop from about a metre) and `test_fast_set_down_and_a_toss_of_fragile_ware_on_stone` runs with the law off (legacy chip) and on (no chip; the toss breaks it either way) |

Phase 1 re-check (2026-10-22): `tests/test_manipulation.py` green with each of slope and setdown forced, with every
ready law plus slope and setdown forced together, and with every deferred law forced; both spec files pass on the 5
seeds with the ready laws plus slope and setdown forced.

Data: vases and the table centerpiece survive a fall onto carpet from table height and break on tile (drop
tolerance 0.3 m); `small_parts` is a handful of beads 4 x 3 x 1 cm; the trivet's limit is the kernel's `heat_limit_c`;
printed load ratings are `load_rating_text` (read tier) instead of re-exposing hidden thresholds; new type `coaster`.

**Camera (TIER1_CAMERA).** Room mode is unchanged; everything below applies only when `robot.view` exists.
Visual domain events go through `camera.visual_event` (framed, never emitted twice); the `falling` / `rolling` /
`demonstration` channels list only what a frame of this step has in view (`camera.in_frame`); printed fields
(`label_text`, `this_side_up`) are listed by the inspector only when `ctx.read_check` can read them; a label's face
(the kernel's `label_facing` word, from `apply_label face=`) turns with `flip` and `set_orientation`, so a label on
the underside cannot be read; `hold_steady frame=robot` takes the robot's left/right from its real yaw against the person.

**Body (TIER1_BODY).** Lifting: `ctx.require_grasp` and `ctx.check_lift` (capacity, lift plan, two hands);
grip forces are clamped to the body's `grip_limit_n`, pushes and pulls to `push_n` / `pull_n`, twists to
`wrist_nm` (`commanded_force` notes the clamp); precision verbs (insert, catch, throw) read `precision_mm`;
verbs that move the robot (`drag` and `roll` to a room, `follow`, co-carry) walk through `walk_robot`
(`ctx.require_mobile`, `ctx.motion`); `tilt`, `shake` and `hold_steady` take `timing="commanded"`; slips and stair
stumbles drop what is held through `body_faults.drop_held`; sliding loud loads and tape declare `noise_db`, so quiet mode
refuses them above 55 dB. The domain's own duration key is `ctx.data["manip_s"]` (`seconds` is reserved).

**Sweep (TIER1_SWEEP).** Notices: `clink` (50 dB), `rip` (55 dB), `bang` (65 dB) through `w.notice`.
Dialogue route `manipulation.items`: ask aspects keep / status / who / intended / where / owner, how (+ fragile,
handle, care), which (+ contents), preference (+ facing / frame / orientation) map onto `manip:<key>:<id>`; the
kernel's people and permission routes come first. Spec migrations: both perception specs and all examples run in
generation-2 houses (`"house": {"gen": 2}`); the wind spec's privileged `wait_until` became a plain 420 s wait, and
its weather op pins the morning gusts (a generation-2 day draws its own `gust_mps`); the vase spec is
`"camera": "geometric"` with a camera wrong plan, and its transport drop is 0.76 m (ISTA 1A); the hot-mug hand-over
mug is 88 % full, so rushing spills more than the kernel's 5 ml droplet threshold.

---------------------------------------------------------------------------------------------

## 16. Phase 1 stabilisation (independent review 2026-10-22, adoption breakages)

Fixed at the root, each with a regression test in `tests/test_manipulation.py`:

* **Lift monotonicity (review H11 defect 1, P2-gate probe row 4).** The kernel's lift plan gives one hand up to its
  8.0 kg rating, but the `normal` grip's 50 N cap dropped dry 7.65-8.0 kg items one-handed while an 8.01 kg one was
  lifted with two hands and held. The one-hand `normal` / `handle` squeeze now reaches what holds the hand's rated
  load on the item's dry surface (`common.rated_squeeze_n`); only slippery surfaces, fingers or a gripper fault make a
  one-hand lift slip. Test: `GraspTests.test_lift_monotonicity_a_one_hand_lift_never_slips_on_mass_alone` (masses
  0.5-12 kg in five materials, including the lowest-friction film; wet glass slips monotonically).
* **Slope law (adoption breakage).** `roll release=true` (and a runaway) clears `tilted_on_wheels` / `tilt_deg` in the
  same step; walking out of reach of a tilted load lets go of it too. Tests:
  `SlideRollTests.test_releasing_a_tilted_cart_lets_it_settle_on_its_wheels_with_any_slope_law`,
  `test_walking_off_lets_go_of_a_cart_balanced_on_its_wheels`, and the law-agnostic
  `test_bin_released_on_a_slope_runs_into_the_street`. Slope is ready.
* **Setdown law (adoption breakage; integrator decision: a fast set-down stays a 6 cm drop).** T-ce25a2474ffb3d-01's
  wrong plan "rushes and sets it down hard on marble" (which passes with the law) became "tosses it onto the marble
  table instead of setting it down" (a drop from about a metre: it lands on the floor; glass and porcelain break on
  hard floors), failing on every seed with the law off and on. Test:
  `PlacementTests.test_fast_set_down_and_a_toss_of_fragile_ware_on_stone` (both law states). Setdown is ready.
* **Dialogue route priority (adoption breakage).** `manipulation.items` is a weak catch-all except for things carrying
  the hidden truth it answers from (section 4). Test:
  `TierOneAdoptionTests.test_the_items_route_is_a_catch_all_for_things_without_the_hidden_truth`.
* **Hot hand-over burns (people cross-domain report 2).** The fixed 55 C threshold became the people domain's rule
  (`thermal.burn_seconds` within a ~2 s grip; adult about 65 C, child about 60 C; handle / insulated offers are safe).
  Test: `HandOverTests.test_hot_handover_burns_follow_the_kernel_burn_time_like_the_people_domain` (with the people
  domain loaded it also checks that both counters agree).
* **Aiming at unresolved look-alikes (review P3 identity sweep, `rotate face_toward`).** Bearings and spots relative
  to a reference use the size the robot sees (section 13). Test:
  `PerceptionHardeningTests.test_aiming_at_an_unresolved_lookalike_uses_the_size_the_robot_sees`.

Look-alike groups owned here (`smooth_stone`, `clear_plastic_roll`, `paper_tape_roll`, and the single-type weight
groups) raise no `LookalikeWarning`: members share color and size. A twin sweep (every registered action with the
probe in each entity parameter, on the table and held, room and geometric camera) shows differences only through the
documented resolving channels: touch (`mass_kg` bucket after a lift, heft, shake, extract) for the stones; `inspect`,
`wrap`, `tape` and `tear` (each a close look that grants inspect on the roll) for the rolls; and admin_inventory's
`count`, whose remote inspect grant is that domain's (review H7 / I3).

Known limits kept: the rotate sweep's knock-over of a nearby look-alike follows its true height (contact physics; the
groups whose members differ in true size are their type owners'); a cart the robot `roll`s without release moves
alone while the robot stays where it was (the robot "walks it" only when rolled into another room).

---------------------------------------------------------------------------------------------

## 17. Phase 1 finish (breakages.json rows, red-team findings, G7)

Fixed at the root, each with a regression test in `tests/test_manipulation.py` (`PhaseOneFinishTests` unless named);
every new test fails on the previous code:

* **Legacy contact heat asks the claims (T-7d4006bda247ba-01, the only failing authored spec).** An iron upright on
  its heel rest scorched the ironing board through this domain's copy of the contact_heat law, which asked no
  `phys.contact_heat` hook. `handling.hot_contact` now asks them with the law's severity
  (`min(1, (T - limit) / thermal.CONTACT_SPAN_K)`), exactly as `thermal.contact_check` does, before marking anything.
  T-7d4006bda247ba-01 passes on all 5 seeds. Test: `test_the_legacy_contact_heat_rule_asks_every_contact_heat_hook_first`
  (law deferred and active; the claim receives the law's severity; unclaimed contacts still scorch).
* **Stack explanation follows the kernel's stack law (D1 readiness).** `stacking.stack_reason` compared the members'
  bounding boxes (a mixing bowl on a kitchen scale's platform was "much larger"; the kernel finds it stable). It now
  takes the first member `phys.statics.stack_verdict` topples and names the verdict's own rule at that member
  (`top_wd` of the support against `contact_wd` of the member, nesting, the low-flat-base exception, the centre
  offset; then slenderness and the shelf gap); this domain's open-contents rule comes first because it is what
  `settle_stack` topples. Test: `test_stack_reason_judges_each_member_as_the_kernels_stack_verdict_does` (five
  stacks: reason None exactly when the verdict topples nothing; the stack verb rests a bowl on a scale with the law
  deferred and active). The test file is green with the stack law forced: stack is ready.
* **Wind at the item.** `wind_lift_mps` is the speed at the item for the kernel's wind law
  (`environment.object_wind_mps`: the weather's gust, else 1.5 x its mean, times the room's exposure: 0.6 near the
  ground in a yard) but this domain compared it with the weather's 10 m wind, so light things lifted at 0.6 of the
  wind they need. `world._wind_at` reads the kernel's value (covers included); the `manipulation.gust` event's `mps`
  is already the speed at the items. Test: `test_light_items_lift_at_the_wind_at_the_item` (the law deferred and
  active agree: a 4 m/s sheet stays under a 6 m/s gust and goes at 7 m/s).
* **Wrap containment ends when the wrap comes off (red_defects 6).** A contained break unwrapped on the floor only
  got `shattered`: no floor hazard, no shards. `common.end_containment` calls the kernel's `impact.uncontained`
  (`shattered`, the sharp floor hazard, the `phys.uncontained` hooks the safety domain answers with its shard field;
  idempotent) from `unwrap` and `tear` (`by` robot) and from `manipulation.breaks` for a wrap another domain removes.
  The `phys.broke` hook now remembers every contained shattering break (opaque or see-through wrap, packaging), and
  the breaks probe is exact (idle while each is still broken and contained). `unwrap` also accepts another domain's
  plain `wrapped: true`. Test: `test_unwrapping_a_contained_break_lets_its_pieces_out` (newspaper, bubble wrap,
  packaging torn off, wrap removed elsewhere: hazard and hook exactly once).
* **Damp lift monotonicity (red_defects 8).** The adaptive one-hand squeeze stopped at the rated dry squeeze, so a
  damp item slipped one-handed from 4.8 kg while 8.01 kg was held with two hands. It now rises to the hand's hardware
  limit for the surface it actually grips (`cap = min(grip_limit_n, max(mode cap, rated dry squeeze, 1.3 x
  required))`). Damp and wet items are held at every mass up to the rating; oily and soapy ones still slip where 150
  N cannot hold them (from about 3 kg), monotonically; a squeeze raised on a slippery fragile item can crack it (the
  0.9 kg oily glass bottle now cracks in the hand instead of slipping: either way it breaks unless supported from
  below). Tests: `GraspTests.test_lift_monotonicity_a_one_hand_lift_never_slips_on_mass_alone` (six damp / wet
  cases, ten masses; oily and soapy monotone), `GraspTests.test_oily_bottle_breaks_or_slips_unless_supported`.
  The residual (oily / soapy 3-8 kg: one hand slips, 8.01 kg two hands hold) is the kernel lift plan's hand choice
  (request filed).
* **`coiled` / `rolled` schemas (coverage-fuzz strict-mode defect, G6).** `velcro` on a garden hose crashed on
  `dict(True)`: plumbing_liquids writes `coiled` as a bool, electrical as a word, this domain as a dict.
  `common.coil_of` / `common.roll_of` read every shape (electrical's `uncoiled` is not coiled); velcro / unvelcro
  note the strap only in this domain's dicts and leave another owner's value as written; uncoiling and the
  `manipulation.deploy_test` event accept any coil; a laid-out cord drapes. Test:
  `test_velcro_and_coils_accept_every_owners_coiled_and_rolled_field`.
* **One harm flag for a hot hand-over (red_defects 10, with people).** The people domain now logs the burn and counts
  `stats.burns` only when this domain's rule did not; this domain keeps its rule (it also runs without people).
  `HandOverTests.test_hot_handover_burns_follow_the_kernel_burn_time_like_the_people_domain` pins `burns` once and no
  `injury`, with and without the people domain.

Attribution (two snapshots of the same live tree, with and without this round's manipulation edits): of the 243
authored specs x 5 seeds the only verdict change is T-7d4006bda247ba-01 FAIL -> PASS (seeds 0 and 41); four other
trials keep their verdict with another reference digest (seed 41: a beach ball in the yard, `wind_lift_mps` 4, was
blown away by the generated 6 m/s 10 m wind at the start; at the item that is 3.6 m/s, so it now goes only when the
spec's own gust comes). `tools/golden.py dump --all`: 275 files, 1 changed (T-7d4006bda247ba-01: no scorch).

Performance (G7): this domain's systems cost about 0.008 ms per 30 s substep (body plus idle probe, as the engine
runs them, best of 3 over 200 substeps on seeds 0-3) on the ADOPT tree, at HEAD and now: no share of the P1
regression. The wind probe is now exact about exposure (windy weather with only indoor light things is idle), which
pays for the kernel's per-item wind reading.

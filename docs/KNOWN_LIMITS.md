# Known limits and outstanding scope

This is a runnable first symbolic prototype, not completion of the full five-day
mission. `reports/pending.jsonl` retains every unsupported requirement and its
reason; `demos/manifest.json` lists sections without a verified demonstration.

- No physics, geometry, collision, reachability volumes, grasp force, stairs,
  continuous motion, cloth, knots, hot-food chemistry or fluid dynamics. Symbolic
  water transfer conserves scalar amounts; folding changes an abstract state.
- No learned policy, LLM training, API connection, unrestricted command parser,
  general common sense, perceptual recognition, language/accent generalization,
  or sim-to-real transfer.
- The full supplied catalogs are not executable. The metadata inventory includes
  primitive/variation/compound requirements that remain TODO. The truncated
  adaptability entry remains BLOCKED because its original intent is missing.
- Scenario generators use bounded cases and canonical IDs. Testing five seeds
  per entry does not cover every home, object or interpretation. A label match
  and successful script are not evidence of learned semantic understanding.
- Rich properties are partly metadata. The base home is a room graph with a
  small object set, not a furnished architectural model. Apartments are labeled
  variants; elevators and shared laundry rules are not implemented. Outdoor
  areas are graph nodes, not terrain.
- Working washer/dryer/dishwasher cycle machines are included. Full stove,
  oven, refrigerator, HVAC, irrigation, alarm and utility systems are not.
- Residents answer scripted questions. There is no realistic human/pet behavior
  simulator, general authority conflict resolution, gaze sensing, speech/audio,
  disability-aware interaction or consent inference.
- The event system supports scripted state changes and seed-randomized timing.
  It is not a continuous stochastic process. Standing orders queue actions once;
  callers must run the pending actions. There is no week-long autonomous manager.
- Quietness, resource use and reported conditions are symbolic. Physical damage,
  injury, fire and flooding are not emergent simulations. Most subjective and
  emergency/care tasks are deliberately unimplemented.
- Plain Python state and JSON integrity hashes detect accidental changes, not
  malicious code execution or signed provenance. Run untrusted agents separately.
- No real task runs after this package is delivered. Five days was the allowed
  budget in the brief, not a claim of five days spent or continuing background work.

Next priorities: manually ground the remaining requirements in batches, add
compound goals and resident norms, broaden adversarial generation, then add a
geometric adapter for doors/carrying/clearance and physical adapters where they
change the skill. Keep source-level coverage evidence throughout.

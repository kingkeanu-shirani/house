# Tier-1 foundations: decisions on the specs' open questions

Specs: docs/design/TIER1_PHYSICS.md, TIER1_CAMERA.md, TIER1_EMBODIMENT.md. Each judge saw only one of its two
candidate designs (an orchestration truncation bug); each built and weighed the alternative itself. Where a spec lists
an open question, this file is the decision implementers follow. Decisions marked (user) may be revisited by the user.

## Physics
- Per-instance threshold variation (hash-derived, stateless, about ±15 %) is ON by default; `phys_exact` turns it off
  for unit tests and setups that need exact thresholds.
- Thermal time-constant recalibration (lumped m*c/(h*A)) lands as a deferred law and is switched on in the domain
  adoption round together with plumbing_liquids and food (who already override it), with all specs re-verified.
- Generated furniture anchoring: mechanisms draws `anchored` per instance; until a cited survey figure is found use a
  conservative 25 % for tall storage furniture and document the assumption in docs/KNOWN_LIMITS.md.
- Harm attribution: a consequence counts as robot-caused when the robot's action (or its motion) is the direct cause
  or when the failing support carries load the robot added beyond the baseline; exogenous consequences (scripted
  events, pets, people) never fail a task by themselves and are recorded in phys_* stats.
- Wobbly/sagging cues are computed lazily (staged entities and first touch), not for every scene at reset.

## Camera
- Geometric camera is opt-in per spec (`"camera": "geometric"`) while the domains migrate; room mode stays
  byte-identical (golden test). DECIDED by the user (2026-10-13): after migration, geometric becomes the DEFAULT for
  realistic perception (harness episodes and realistic specs); oracle verification stays room mode.
- Handles stay tied to true ids for now (documented limitation: an unseen swap keeps the handle); handle epochs are
  deferred.
- Without a registered camera_capture hook (people domain not loaded), the kernel itself records privacy captures
  and counts them as privacy_violations.
- Mirror reflections are deferred unless the glare/mirror catalog entries need them during authoring.

## Embodiment
- A power-down is a consequence, not a refusal: an interrupted walk or chain returns ok=True with a "powered down"
  message and commits what already happened; ok=False keeps meaning "rolled back".
- Walking energy keeps navigation_robot's 0.288 Wh/m (documented, plausible for current humanoids); recalibration is
  a later option.
- The careful-speed double-slowdown fix lands even if verdicts change; any spec whose verdict changes is re-authored
  (correctness over stability).
- Privacy mode turns cameras off and stops recording, but addressed speech is still heard (so "stop" and consent
  answers work); document it.
- Swappable battery packs match the robot profile (2.4 kWh, about 14 kg, two-handed swap).

## Orchestration
- Libraries first (new files, parallel), then integration in a fixed merge order because engine.py, actions.py,
  world.py, systems/core.py, tasks/setup.py, perception.py and realism.py are shared: physics integration -> camera
  runtime -> camera actions || camera rendering -> body engine -> body core verbs || body faults/perception.
- Every integration step must keep all tests (normal + strict) and all authored specs green (both perception modes).

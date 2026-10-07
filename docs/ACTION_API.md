# Action API

```python
from house_env import HouseEnv, make_house
env = HouseEnv(make_house(room_count=20, floors=3, seed=42))
observation = env.reset()
result = env.step("move", "room_2")
```

`step(action, target='', destination='', **kwargs)` returns `ok`, `message`,
`observation`, `evaluation`, `reward`, `terminated` and `truncated`. Normal action
results also have a failure `category`. Use `terminated` or `truncated` to end
training episodes. Reward is the change in evaluation score, so repeated looks
after completion do not repeatedly earn a success reward. Score is 1 for complete
success, minus bounded invalid-action/unnecessary-question penalties.

Failed accepted actions are atomic, but consume one step and one simulated
second; scheduled external events can still happen. Non-string positional API
arguments are rejected before entering the action clock. A failed guard is
measured separately from an actual harmful world outcome.

| Actions | Main conditions/effects |
|---|---|
| `look`, `inspect` | Partial observation; private inspection needs permission |
| `move ROOM` | Adjacent, lit, unblocked, permitted room; connecting doors open |
| `pick_up ID`, `place ID DEST` | Visible portable object; one hand; mass/capacity/containment checks |
| `hand_over ID PERSON` | Visible consenting recipient in same room; real containment transfer |
| `open`, `close`, `lock`, `unlock` | Supported mechanism, matching held key, no obstruction or running cycle |
| `wipe`, `dust`, `scrub`, `sweep`, `vacuum`, `mop` | Compatible held tool, capacity/uses, surface affordance, utility/quiet constraints |
| `wash`, `rinse`, `dry`, `disinfect`, `polish` | Water/material/tool prerequisites; detergent and soil accounting; documented dwell time |
| `fold`, `unfold`, `flatten`, `coil`, `uncoil`, `tighten` | Appropriate symbolic affordance and state; no cloth/topology mechanics |
| `fill`, `pour`, `water_plant` | Positive bounded `amount_l`, source supply, capacity; clean water only |
| `switch_on`, `switch_off` | Supported switch; life-safety equipment cannot be disabled |
| `start`, `pause`, `set_mode` | Washer/dryer/dishwasher cycle state machine; load, door, power, water and compatibility checks |
| `ask PERSON`, `topic=...` | Scripted clarification or scoped permission; resident available |
| `speak`, `announce`, `listen`, `refuse`, `handoff` | Simulated communication; no real network communication |
| `report`, `field=...`, `value=...` | Requires inspection; evaluator checks truth independently |
| `remember`, `forget` | Supported preference memory previously supplied through clarification |
| `stop`, `resume`, `set_quiet` | Robot control; appliance clocks and external events keep running |
| `wait`, `seconds=N` | 1–3600 simulated seconds; no real sleeping |
| `watch`, `run_pending` | Observable predicate queues commands once; each queued action uses normal guards/duration |
| `snapshot NAME`, `diff NAME`, `undo` | Snapshot comparison; guarded inverse of last reversible action |

Full executable transformation definitions are in `house_sim/data/actions.json`.
Call `env.action_names()` for supported names and `env.affordances(id)` for a
side-effect-free availability/reason probe of object interaction candidates.
Affordance queries do not enumerate every multi-parameter action combination.

Named arguments use JSON in the CLI:

```json
{"action":"water_plant","target":"target_1","destination":"watering_can","amount_l":0.25}
```

For headless evaluation, put one action object per line in a file and run
`py house_env.py --task water --headless actions.jsonl`.

Undo uses inverse actions and current preconditions. It cannot restore elapsed
time, replenish consumed water, reverse washing, move a hidden object, or cross
a door that has since closed. Irreversible operations clear the undo stack.
Snapshots are for comparisons; there is no agent action to load arbitrary state.

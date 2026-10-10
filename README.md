# House AI — Python-only symbolic prototype

A runnable environment for practicing household decisions, with a catalog of
2,719 requirements, guarded actions, independent goal checks and replayable tests.
It runs entirely on Python's standard library. No GPU, Isaac Lab or API key is used.

**This release is a partial implementation of the attached large build brief.**
Every catalog entry is recorded; only the entries with verified executable
scenarios are marked DONE in `coverage.csv`. All implemented scenarios have
symbolic fidelity S. This is not a trained LLM, a physics simulator, or evidence
that a robot can perform these chores in a real home. See `FINAL_REPORT.md` for
the measured coverage and `docs/KNOWN_LIMITS.md` for missing capabilities.

## Start on Windows

1. Install Python 3.9 or newer, enabling **Add Python to PATH**.
2. Extract the complete ZIP. Open the `house_ai` folder containing `house_env.py`.
   Keep `house_sim`, `catalog`, `tasks` and the other folders beside that file.
3. Click File Explorer's address bar, type `cmd`, and press Enter.
4. Run:

```console
py setup_project.py
py house_env.py
```

Setup is an offline health check; there are no runtime packages to install.
If `py` is unavailable, use `python`. On macOS/Linux use `python3`.
VS Code is optional. This expanded version needs the whole extracted folder,
not just its small `house_env.py` launcher.

At the prompt, enter `look`, use an exit such as `move room_2`, or type `help`.
IDs identify objects; labels such as “cup” can refer to more than one object.

## Try it working automatically

```console
py house_env.py --rooms 20 --floors 3 --seed 42 --agent
```

The **scripted search baseline** explores using observations, finds the cup,
and puts it in the sink. It does not receive the hidden map or object locations.
This demonstrates the environment/agent boundary, not learned reasoning.

```console
py house_env.py --demo clean
py house_env.py --demo clarify
py house_env.py --demo rain_watch
py house_env.py --demo laundry_watch
py house_env.py --list-tasks
```

`--demo` uses a **privileged scripted reference policy** to verify a task family.
It is deliberately separate from the observation-only search agent. `--task clean`
opens that scenario for you to solve manually. A catalog ID also works with `--task`.

## Layouts, saves and replays

```console
py house_env.py --rooms 20 --floors 3 --seed 42
py house_env.py --rooms 8 --outdoors --export my_house.json
py house_env.py --house my_house.json
py house_env.py --demo clean --replay-out clean_replay.json
py house_env.py --replay clean_replay.json
```

`--export` refuses to overwrite a file. A layout export is an initial world;
`save state.json` inside the prompt saves the current household, memories, event
state and undo history. Reload with `py house_env.py --load-state state.json`.
`--save-state FILE` and `--replay-out FILE` also work with headless execution.

## Tests and coverage

```console
py -m unittest discover -s tests -v
py house_env.py --verify
py tools/report.py
py house_env.py --demo
```

The full audit takes several minutes and checkpoints every 100 entries. It runs
reference, idle and wrong-action policies on seeds 0, 1, 2, 41 and 97 for every
implemented catalog entry. Section demos use a separate seed, 701, and verify
their replays. Missing section demos are explicitly listed in `demos/manifest.json`.

`coverage.csv` is the authoritative status table. Editing engine/generator code
invalidates old verification hashes; rerun `--verify` to restore verified status.
`tasks/specs.jsonl` keeps conservative registration statuses from compilation;
the coverage report upgrades a candidate only when current evidence passes.

## Evaluate an agent

The evaluation tools (`house_sim/eval/`) run agents on catalog tasks in **realistic perception** (opaque handles,
inspect/read/measure; `docs/PERCEPTION.md`), score them, rank them and export data. Everything below is offline.

```console
py -m house_sim --run-agent oracle --split dev --ids T-089c40fb507289-01 T-6af2ea41755bbe-01 --verify-on-the-fly
py -m house_sim --run-agent random --split dev --ids T-089c40fb507289-01 T-6af2ea41755bbe-01 --verify-on-the-fly --agent-opt seed=7
py -m house_sim --leaderboard results\<oracle_run_id> results\<random_run_id>
py -m house_sim --verify-submission results\<random_run_id>
py -m house_sim --viewer results\<random_run_id>\episodes.jsonl#0
```

* **Agents**: `random`, `search` (toy fetch baseline), `oracle` (privileged: replays the spec's reference plan;
  labelled in every result), `mock` (canned LLM responses, for tests), `llm:anthropic:<model>` and
  `llm:openai-compatible:<model>@<base_url>`. Your own agent subclasses `house_sim.eval.agents.Agent`
  (`reset(instruction, observation, action_catalog)`, `act(observation, last_result)`; see `docs/EVAL_HARNESS.md`).
* **`--verify-on-the-fly`** checks the selected task specs on the dev seeds before running them. Without it only
  specs with current evidence in `reports/verification.jsonl` run, and that evidence goes stale whenever simulator
  code changes. `--allow-unverified` runs specs unverified (labelled as such).
* **Results** go to `results/<run_id>/`: `run.json`, `episodes.jsonl`, `logs/` (enough to re-score by replay),
  `replays/` (1-3 MB per episode; add `--no-replays` for large runs). `--leaderboard` writes `reports/leaderboard.md/.csv/.json/.html` with 95% bootstrap intervals and a
  failure taxonomy; `--verify-submission` replays every logged command and rejects any score that does not match.
* **Splits**: `train` (unlimited public seeds), `dev` (seeds 0, 1, 2, 41, 97) and a hidden `test` split:
  `py -m house_sim --init-split-key`, then `py -m house_sim --build-test-manifest --verify` (verifies the hidden
  seeds; writes the private manifest under `%USERPROFILE%\.house_ai` and the public `reports/test_commitment.json`),
  `py -m house_sim --verify-commitment`, then `--run-agent ... --split test`. Test results never contain raw seeds.
* **Viewer**: `--viewer` writes one self-contained offline HTML page per episode (floor plan, room view, timeline,
  oracle and perceived views).
* **Dataset**: `py -m house_sim --export-dataset OUT_DIR --verify-now --seeds-per-entry 1` exports SFT
  trajectories, preference pairs and critiques from verified specs (train seeds only, test seeds refused);
  `py -m house_sim --validate-dataset datasets\sample` checks one (schema, leaks, duplicates).
* **LLM agents** cost money and are never called unless you pass `--allow-network` with the provider's key in the
  environment. Estimate first; this makes no calls:
  `py -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --dry-run --verify-on-the-fly`.
  The prices in `house_sim/eval/prices.json` must be checked against the provider's current price list.

Details: `docs/EVAL_HARNESS.md`, `docs/SPLITS.md`, `docs/DATASET.md`, `docs/VIEWER.md` and the interfaces in
`docs/EVAL_CONTRACTS.md`.

## Code and data

| Location | Purpose |
|---|---|
| `house_env.py` | Simple launcher and compatible public imports |
| `house_sim/engine.py` | Guarded actions, observations, evaluation and persistence |
| `house_sim/world.py` | Procedural room graphs, entities and validation |
| `house_sim/scenarios.py` | Seeded task initial states and predicates |
| `house_sim/reference.py` | Privileged reference and wrong-action policies |
| `house_sim/agent.py` | Observation-only search baseline |
| `house_sim/language.py` | Limited deterministic command grammar |
| `house_sim/data/actions.json` | Declarative transformations |
| `catalog/raw/` | Unchanged supplied inputs |
| `catalog/entries.jsonl` | Stable IDs, verbatim source text and byte spans |
| `tasks/` | Requirement specs, family mapping and executable examples |
| `reports/` | Coverage tables, verification evidence and pending requirements |
| `docs/` | Architecture, API, task schema, safety, evaluation and extensions |

Optional installation into an existing Python environment with pip/setuptools:

```console
python -m pip install . --no-deps --no-build-isolation
house-ai --demo clean
```

Running directly from the extracted folder needs neither pip nor setuptools.

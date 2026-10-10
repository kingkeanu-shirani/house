# Evaluation package contracts (house_sim/eval)

Five components are built in parallel; each owns its files and implements the interfaces below exactly, so the
others can rely on them. Pure standard library, Python 3.9-compatible, Windows paths (pathlib), deterministic where
the simulator is involved. **No network calls in tests, no real API calls ever without the user's approval.**

CLI: `python -m house_sim <flag> ...` — house_sim/cli.py hands any argv containing one of these flags to
`house_sim/eval/cli.py`, which calls the owning module's `main(argv) -> int` with the full argv:

| flags | module |
|---|---|
| `--init-split-key`, `--build-test-manifest`, `--verify-commitment`, `--split-info` | `house_sim/eval/splits.py` |
| `--run-agent` | `house_sim/eval/run.py` |
| `--leaderboard`, `--verify-submission` | `house_sim/eval/leaderboard.py` |
| `--export-dataset`, `--validate-dataset` | `house_sim/eval/dataset.py` |
| `--viewer` | `house_sim/eval/viewer.py` |

## A. splits.py (owner: splits)

```python
DEV_SEEDS = (0, 1, 2, 41, 97)            # the public verification seeds
DEV_EXTRA = (...)                        # small published extra set (e.g. 5 seeds, fixed)
TRAIN_BASE = 1_000_000                   # public train range [TRAIN_BASE, PRIVATE_BASE)
PRIVATE_BASE = 900_000_000               # hidden test seeds live in [PRIVATE_BASE, 2**31 - 1)

class SplitKeyMissing(RuntimeError)      # raised when the test split is requested without a key

def key_path() -> Path                   # %USERPROFILE%/.house_ai/split_key (Path.home() / ".house_ai" / "split_key")
def load_key(required=True) -> bytes|None  # env HOUSE_AI_SPLIT_KEY (hex) or key_path(); never logged
def init_key(force=False) -> Path        # secrets.token_bytes(32), hex file, refuses to overwrite unless force
def key_digest(key) -> str               # sha256 of the key, for commitments (never the key itself)

def train_seeds(catalog_id, n, start=0) -> list[int]     # deterministic, in the train range
def dev_seeds(catalog_id, extra=False) -> list[int]
def test_candidates(catalog_id, k, key) -> list[int]     # HMAC-SHA256(key, f"{catalog_id}:{i}") -> private range
def is_private_seed(seed) -> bool        # seed in the private range (always checkable, no key needed)
def is_test_seed(catalog_id, seed) -> bool   # private range OR listed in the private manifest when available
def seed_tag(catalog_id, seed) -> str    # public per-episode tag: "dev:<seed>" / "train:<seed>" / "test:" + sha256(...)[:12]

def manifest_path() -> Path              # %USERPROFILE%/.house_ai/test_manifest.json
def load_manifest(required=True) -> dict # {catalog_id: {"seeds": [...], "variants": [...], "phrasings": [...], "skipped": [...]}}
def build_test_manifest(specs, k=10, workers=None, key=None) -> dict   # verifies candidates (realistic mode when the spec requires it)
def commitment(manifest, key) -> dict    # {"per_entry": {cid: sha256}, "root": merkle root, "key_digest": ...}
COMMITMENT_FILE = reports/test_commitment.json
def verify_commitment(manifest, key, public) -> bool

def episodes(split, specs, seeds_per_entry=None, seeds=None) -> list[Episode]
# Episode = dict {"catalog_id", "split", "seed", "seed_tag", "phrasing" (str|None: private paraphrase for test),
#                 "perception" ("realistic" mandatory for test; else the spec's own unless the caller forces)}
def guard_not_test(catalog_id, seed)     # raises ValueError("test seed refused") for private-range/manifest seeds
def main(argv) -> int
```

## B. agents.py + run.py (owner: harness)

```python
# agents.py
class Agent:
    name = "agent"
    privileged = False                   # True only for ScriptedOracle (labelled in results)
    def reset(self, instruction: str, observation: dict, action_catalog: list[dict]) -> None: ...
    def act(self, observation: dict, last_result: dict | None) -> dict: ...   # command dict, or {"action": "done"}
# last_result passed to agents contains ONLY {"ok", "message", "category"}.
class RandomValidAgent(Agent); class SearchAgentAdapter(Agent); class ScriptedOracle(Agent); class MockLLMAgent(Agent)
class LLMAgent(Agent)                    # providers: anthropic, openai-compatible (urllib); see EVAL_HARNESS.md
def make_agent(spec_string, **options) -> Agent   # "random" | "search" | "oracle" | "mock" | "llm:anthropic:<model>" |
                                                  # "llm:openai-compatible:<model>@<base_url>"
# run.py
def run_episode(spec, episode, agent, caps=None, out_dir=None) -> dict   # episode from splits.episodes
def run(agent_spec, split, specs, out_dir, workers=1, caps=None, dry_run=False, **filters) -> dict
def main(argv) -> int
```

Results layout (`results/<run_id>/`): `run.json` (config, agent, split, code fingerprint
`house_sim.audit.fingerprint()`, harness fingerprint `house_sim.audit.harness_fingerprint()`, perception
modes, `planned_episodes`/`planned_episodes_sha256`, `llm_transport`/`llm_cache`, prices, spec hashes
`house_sim.tasks.verify.spec_hash`, commitment root, started/ended),
`episodes.jsonl` (one record per episode, schema below), `logs/<catalog_id>__<seed_tag>.json` (command log: the
commands as the agent issued them, the results it saw, timings; enough to re-score by replay), `transcripts/`
(LLM requests/responses when applicable; never API keys).

Episode record: `{"catalog_id", "split", "seed_tag", "agent", "privileged", "success", "goal_fraction",
"failures", "harm_flags", "side_effects", "blocks": {"safety", "privacy", "authority"}, "invalid_steps",
"questions": {"necessary", "unnecessary"}, "steps", "sim_time_s", "wall_s", "tokens": {"input", "output"},
"cost_usd", "done_declared", "end_reason", "log", "spec_hash", "perception", "kind", "section", "domain",
"risk_level", "fidelity"}`. Raw seeds are recorded only for train/dev (`"seed"`); never for test.

## C. leaderboard.py (owner: leaderboard)

```python
def load_runs(paths) -> list[dict]       # refuses mixed code fingerprints / commitment roots unless allow_mixed
def aggregate(runs) -> dict              # rates with bootstrap 95% CIs (seeded), breakdowns, failure taxonomy
def failure_category(label) -> str       # failure_predicate_* / changed:* / moved:* / broke:* / harm flags / order:* /
                                         # deadline:* / budget / missing_necessary_question / ... -> readable category
def verify_submission(run_dir) -> dict   # replays every episode's command log in the simulator, recomputes scores
def write_reports(summary, out_dir="reports") -> dict   # leaderboard.md/.csv/.json/.html (single offline file)
def main(argv) -> int
```

## D. dataset.py (owner: dataset)

```python
def export(out_dir, formats=("sft", "pairs_trajectory", "pairs_step", "critique"), split="train", seeds_per_entry=2,
           max_pairs=None, kinds=None, domains=None, sections=None, obs="compact", workers=None) -> dict  # manifest
def validate(path_or_dir) -> dict        # schema, no test seeds, no true entity ids / secret fields (reuse
                                         # tools/perception_audit.py checks), dedup
def main(argv) -> int                    # --export-dataset OUT_DIR [...], --validate-dataset PATH
```
Uses only specs with current passing verification evidence (`reports/verification.jsonl` with the current
fingerprint) and realistic perception. Sample export committed at `datasets/sample/`.

## E. viewer.py + viewer_template.html (owner: viewer)

```python
def export_viewer(source, out_path=None, view="both") -> Path   # source: replay json path, or an episodes.jsonl
                                                                # record / results log path
def main(argv) -> int                    # --viewer SOURCE [--out PATH]
```
One self-contained offline HTML file (embedded JSON, inline CSS/JS). tools/export_viewer.py is a thin wrapper.

## Shared facts

* Episodes run with `HouseEnv(config, task, seed, perception="realistic", trace=False, observe_on_step=True)`; build
  config/task with `house_sim.tasks.runner.build(spec, seed)`; when a test phrasing is given, replace
  `task["instruction"]` with it.
* Code fingerprint: `house_sim.audit.fingerprint()`; spec hash: `house_sim.tasks.verify.spec_hash(spec)`.
* Verified specs: `house_sim.compiler.compile_specs()` records + `reports/verification.jsonl` evidence
  (`passed` and `implementation_hash == fingerprint()`); helper to add in `splits.verified_specs()`.
* Realistic replays: `env.save_replay(path)` stores commands as issued (handles); `HouseEnv.replay(path)` re-runs
  them (handles resolve deterministically).

## As built (integration notes)

The five components implement the interfaces above; where the built code differs or adds to them, this is what
callers can rely on (details in docs/SPLITS.md, docs/EVAL_HARNESS.md, docs/DATASET.md, docs/VIEWER.md).

**Shared**

* `house_sim/eval/common.py` `load_authored(root=None) -> (specs, sources, conflicts)`: the authored specs, with a
  catalog id that appears in more than one `tasks/authored/*.json` file left out and listed in `conflicts` instead
  of raising for the whole catalog (`house_sim.compiler.load_authored` raises). `splits.candidate_records`,
  `run.load_specs`, the leaderboard and the viewer use it; the dataset exporter reports such ids as `DUPLICATE`.
* Verified specs: `splits.verified_specs(verify=False|True, only=..., records=False)`; `verify=True` runs
  `verify_many` on `DEV_SEEDS` in each spec's own perception mode and writes nothing. The harness
  (`--verify-on-the-fly`), the dataset exporter (`--verify-now`), the leaderboard (`--verify-specs`), the viewer
  (`--verify-spec`) and `--build-test-manifest --verify` all offer the same on-the-fly check, because the evidence
  in `reports/verification.jsonl` is keyed to `house_sim.audit.fingerprint()` and goes stale on any simulation code
  or data edit. `fingerprint()` covers the simulation (`house_sim` code and data) and deliberately **excludes**
  `house_sim/eval/`; `house_sim.audit.harness_fingerprint()` covers `house_sim/eval/`. Runs record both
  (`code_fingerprint`, `harness_fingerprint`), and the leaderboard treats a change in either as mixed versions.
* Harness episodes always run in realistic perception, while verification runs in each spec's own mode (oracle for
  most example specs). A spec can therefore verify and still be unsolvable for the privileged oracle under realistic
  perception; `--verify-perception realistic` verifies in the episode mode instead.

**A. splits.py**

* `seed_tag(catalog_id, seed, key=None)`: test tags are `"test:" + HMAC-SHA256(key, "tag:<cid>:<seed>")[:12]`
  (keyed, so they cannot be brute-forced back to a ~30-bit seed; `SplitKeyMissing` without a key); other public
  seeds get `"seed:<n>"`. Added: `safe_tag(tag)` (`:` -> `-`, for Windows file names), `resolve_test_tag(cid, tag,
  key=None, manifest=None)`.
* `build_test_manifest(specs, k=10, workers=None, key=None, perception=None, max_candidates=None, phrasings=None,
  progress=None)` returns `{catalog_id: entry}` and writes nothing; `save_manifest(manifest, key=None, path=None,
  commitment_path=None, merge=False)` writes the private manifest and the public commitment. Entries carry more
  fields than the contract (`phrasing_index`, `n_variants`, `k`, `k_requested`, `complete`, `error`, `perception`,
  `spec_hash`, `fingerprint`, `nonce`, ...); `k` is raised so every variant appears. `nonce` (32 random bytes, hex)
  is part of the hashed entry and blinds the public per-entry commitment hash, which would otherwise be
  brute-forceable back to the hidden seed; the commitment carries `"blinded": true`.
* `load_manifest(required=True, path=None, with_meta=False)`: the entries mapping (the contract shape), or the
  whole file with `with_meta=True`. `ManifestMissing` (a `FileNotFoundError`) when the test split is requested
  before a manifest exists.
* `commitment_file()`: `reports/test_commitment.json`, or the file named by env `HOUSE_AI_COMMITMENT_FILE`
  (sandboxes and tests). `commitment_root()` returns its root (recorded in `run.json`).
* `episodes(split, specs, seeds_per_entry=None, seeds=None, perception=None, strict=True, key=None, manifest=None)`;
  `build_episode(spec, episode) -> (config, task, oracle)` with the held-out phrasing applied.

**B. agents.py + run.py**

* `run_episode(spec, episode, agent, caps=None, out_dir=None, perception=None, ledger=None, run_id=None,
  private_dir=None, save_replay=True)`; `run(...)` filters: `ids, sections, kinds, domains, limit, seeds,
  seeds_per_entry, verify ("evidence" | "fly" | "none"), verify_seeds, verify_perception, verify_workers, perception,
  agent_options, allow_network, transport, cache_dir, use_cache, rpm, run_id, private_dir, save_replays, emit`.
* Logs are `logs/<catalog_id>__<safe seed tag>.json` (`dev:0` -> `dev-0`); the record's `log` field holds the exact
  relative path. Env replays go to `replays/`; for test episodes, replays, transcripts, the LLM response cache and
  any private viewer page go to `%USERPROFILE%/.house_ai/private_results/<run_id>/`, never into `results/` (a
  test-run `cache_dir` or `private_dir` inside the repository is refused). Test logs record each step's `command`
  through `run.loggable_command(command, private=True)`: only what reached `env.step`, never the agent's
  `"_"`-prefixed annotations such as an LLM's raw reply.
* `run.json` adds `authored_conflicts`, `replays_saved`, `actions_hash`, `episode_source`, `verification` (mode, per-spec detail),
  `prices`, `sim_safe_mode`, `ledger`, `totals`.
* `--dry-run` makes no LLM call and writes nothing; with `--verify-on-the-fly` it verifies first and estimates only
  the specs that pass.

**C. leaderboard.py**

* `load_runs(paths, allow_mixed=False)`, `aggregate(runs, resamples=1000, seed=1729, spec_statuses=None,
  verify_specs=False, verified_only=False, workers=None)`, `verify_submission(run_dir, allow_mixed=False,
  write=True, workers=1, progress=None, specs=None)` (writes `RUN_DIR/submission_verification.json`; it passes only
  when every episode replays and re-scores identically and the run's code fingerprint equals the current one, so a
  run must be verified before the code changes or with `--allow-mixed`, which labels the result),
  `write_reports(summary, out_dir="reports")`. The CLI writes to the project's `reports/` by default.
* As fixed after the red-team round (formats `house-leaderboard-v2` / verification v2): rows are keyed by (agent,
  split, evaluation config) and non-realistic perception is a separate privileged track; `verify_submission` also
  takes `allow_incomplete` (incomplete runs are labelled and kept out of the standard track) and `sign` (the CLI
  signs `submission_verification.json` with this machine's verifier key, `HOUSE_AI_VERIFIER_KEY` or
  `~/.house_ai/verifier_key`; an unsigned or stale verdict file is never shown as passed); completeness, the done
  protocol and the step cap are checked against `run.json`; both fingerprints are recorded and checked; written
  reports hold no absolute paths. Test-split logs record keyed `instruction_hmac` (`run.private_text_digest`)
  instead of any plain digest. See EVAL_HARNESS.md for the details.

**D. dataset.py**

* `export(out_dir, formats=..., split="train", seeds_per_entry=2, max_pairs=None, kinds=None, domains=None,
  sections=None, obs="compact", workers=None, catalog_ids=None, seeds=None, verify_now=False, root=None,
  progress=None)`; `validate(path_or_dir, deep=True, root=None)`. CLI `--verify-now` is the on-the-fly check.

**E. viewer.py**

* `export_viewer(source, out_path=None, view="both", private=False, verify_spec=False)`; `extract_data(html)` returns
  the embedded data object. Test-split episodes are refused unless `private=True`.

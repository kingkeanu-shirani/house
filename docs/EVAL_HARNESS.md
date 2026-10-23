# Agent harness (house_sim/eval/agents.py, run.py, llm.py)

Runs agents on task episodes and records results that can be re-scored by replay. Contract: section B of
docs/EVAL_CONTRACTS.md. Standard library only, Python 3.9+, Windows-friendly paths. No network access unless a real
LLM run is started explicitly with `--allow-network`.

## What an agent sees

```python
class Agent:
    name = "agent"
    privileged = False
    def reset(self, instruction, observation, action_catalog): ...
    def act(self, observation, last_result) -> dict: ...      # a command, or {"action": "done"}
```

* `instruction`: the task text (for the test split, the held-out phrasing).
* `observation`: the environment's own observation in **realistic** perception (opaque `obj_xxxxxxxx` handles,
  field tiers, noise; docs/PERCEPTION.md). Each call gets a deep copy. Kernel v3.1 adds `request` (who asked and how,
  in episodes with requests) and `notices` (what the robot heard or noticed during its last step, when anything);
  `instruction` follows the current request (docs/DIALOGUE.md).
* `action_catalog`: `[{"name", "category", "doc", "params", "duration_s"}]` from `env.describe_action` (the
  guard/effect hook lists are dropped).
* `last_result`: `None` on the first step, then exactly `{"ok", "message", "category"}` of the previous step.

The harness never passes evaluation, reward, terminated/truncated, untranslated (`raw_message`) messages, the spec,
the seed, bindings or privileged state. `tests/test_eval_harness.py` instruments a spy agent and fails if any of
those reach it. The only exception is `ScriptedOracle` (`privileged = True`, labelled in every record and in
`run.json`): the harness calls its `prepare(spec, episode, perception, camera)` before `reset` (`camera` only when
its `prepare` accepts it; its shadow environment runs in the episode's camera mode).

Optional hooks (no-ops by default): `bind_budget(budget)` (the per-episode/run caps, see below), `usage()` (token
usage so far), `transcript()` (LLM requests/responses, never keys) and `info()` (extra details for the command log).

## Episode protocol

1. `config, task, plans = runner.build(spec, seed, camera=<--camera, else None>, perception="realistic")` (the run's
   modes, as the verifier passes them: a spec's geometric camera setup ops apply only in a geometric episode; the
   oracle's shadow, re-scoring, leaderboard replays and the viewer rebuild the same way); a test phrasing replaces the
   instruction (placeholders bound like `runner.build`, as `splits.apply_phrasing`). Non-test episodes pass
   `splits.guard_not_test`.
2. The episode's secret nonce goes into the task (`task["episode_nonce"]`, `run.episode_nonce`; harness version 3,
   review H8): handles and every fixed draw follow it, so nothing public recomputes them and another episode of the
   same spec and seed shows other handles. A dev/train episode draws a fresh random nonce and records it in its log
   (`episode_nonce`; `replay_log` and the leaderboard re-inject it) -- unless the agent has a persistent response cache,
   whose secret `episode_nonce.key` (kept in the cache directory, scoped by the agent's cache namespace) derives it, so
   a rerun against the same cache sees the same handles and makes no calls. A test-split episode's nonce is an HMAC of
   the episode under a subkey of the split key (`episode_nonce_scheme`, never the nonce, is logged); the key holder
   recomputes it to re-score (`run.log_nonce`). A privileged agent's shadow episode gets the same nonce. The nonce
   covers handles and fixed draws only: the world of a public (spec, seed) is a pure function of public data, so an
   agent that runs code or was trained on the generator can rebuild a dev/train house offline and decode what it sees
   by content (label, cell, slot, container) -- phase-1 red team. Public-split scores are therefore labelled not
   comparable across agents (`leaderboard.PUBLIC_SPLIT_NOTE`, `row["comparable"]`); only test-split rows are (hidden
   seeds, one attempt per hidden seed: docs/SPLITS.md).
   `HouseEnv(config, task, seed, perception="realistic", camera=<--camera, else None>, trace=False,
   observe_on_step=True)`; one `env.observe()`. With `camera=None` the spec's own `"camera"` decides, else
   `camera.default_mode("realistic")` ("room" until the D3 switch-over makes realistic episodes geometric; kernel
   v3.0, docs/PERCEPTION.md "Geometric camera"). The mode the episode ran in is recorded (`camera`).
3. `agent.reset(...)`, then `agent.act(...)` per step until one of these **end reasons**:

| end_reason | when |
|---|---|
| `done` | the agent emitted `{"action": "done"}` (a harness-level declaration, not an environment action) |
| `step_budget` | the task's step budget (or `--max-steps`, whichever is lower) is used up. The agent still gets one more turn in which only `done` is accepted ("done" costs no step) |
| `time_budget` | simulated time passed the task's time budget (or `--max-sim-s`) |
| `wall_clock` | `--max-wall-s` per episode (default 3600 s) |
| `token_cap` / `cost_cap` / `run_cost_cap` | per-episode tokens, per-episode estimated USD, per-run estimated USD |
| `invalid_limit` | `--max-consecutive-invalid` refused steps in a row (off by default) |
| `agent_error` | the agent raised (for LLM agents: provider errors after retries, network disabled, missing key) |
| `sim_error` | `env.step` raised (simulator defect; the CLI sets `HOUSE_SIM_SAFE=1` so such defects become refused steps) |
| `harness_error` | the episode could not be built |

**Success** is the environment's evaluation at the end of the episode **and** a declared done
(`require_done`, default on; `--no-require-done` scores the final state without a declaration). The environment's
`terminated` flag is ignored, so reaching the goal and then undoing it fails.

**Invalid steps**: unknown action names go to `env.step`, which refuses and counts them. Output that is not a usable
command (unparseable LLM text, a non-dict, no `action`, a reserved key) is sent as the unknown action
`__unparseable__`, so it is refused, counted in `stats.invalid` and replays identically. `invalid_steps` in the record
is the environment's `stats.invalid` (every refused step). Non-string `target`/`destination` values are JSON-encoded
like the task runner does; keys starting with `_` are agent annotations and never sent.

**Power-down steps** (kernel v3.0 robot body; docs/KERNEL_API.md "v3.0 robot body", docs/PERCEPTION.md
"Proprioception and the robot's sensors"). Running the pack flat is a consequence, not a refusal: the step that powers
the robot down returns `ok: true` with a message that says so ("Battery depleted after 8.0 m; the robot powered
down", "... Battery depleted as it finished; the robot powered down", or "Powered down partway: <the refusal that
stopped a chain>"), and what already happened is kept. From then on every action except `wait` is refused with
"The robot is powered down (battery depleted); it must be charged or have its battery swapped" (category
capability): each such step counts as an invalid step, so `--max-consecutive-invalid` (when set) ends the episode with
`invalid_limit` soon after a power-down. `wait` is accepted while off and advances time (a robot left seated on a live
dock charges and wakes at a 3 % gauge, in parked standby). While powered down the observation shows only what the
robot holds, its map (room, exits, connections) and its own state (`obs.robot.power_state` "off",
`battery_state` "depleted"); no people, pets, hazards, sounds, messages or observer channels. The failure label
`robot_stranded` (a power-down away from a dock, or ending the episode off and undocked) fails the task. Agents should
watch `obs.robot.battery` (an integer gauge in realistic perception), `battery_state`, `runtime_min_est` and
`range_m_est`, and dock before the reserve.

## Built-in agents (`make_agent(spec_string, **options)`)

| spec | agent |
|---|---|
| `random` | `RandomValidAgent`: random catalog action, parameters filled from their declared types, visible handles, people, pets, exits and the room grid. Seeded per (seed, instruction, start room); `p_done` (0.02) per step, `max_actions` (60) |
| `search` | `SearchAgentAdapter`: wraps `house_sim.agent.SearchAgent` for simple "put/bring X to/in/on Y" instructions (parsed from the instruction text only); otherwise declares done at once. Done after a successful place, a handoff, or the same command refused `give_up_after` (3) times |
| `oracle` | `ScriptedOracle` (**privileged**): runs the spec's reference plan through the task runner in a shadow environment (same config, task, seed and perception, with trace), takes the shadow's command history (handles as issued; skills expanded into primitive commands) and replays it through a normal harness episode, then declares done. `info()` records plan errors, refusals and any divergence |
| `mock` | `MockLLMAgent`: `LLMAgent` driven by canned responses (`responses=[...]` of command dicts / text / full Anthropic responses, or a callable) through `MockTransport`; FICTIONAL "mock" prices from prices.json. For offline tests |
| `llm:anthropic:<model>` | `LLMAgent`, Anthropic Messages API (default model `claude-opus-5-5`; also `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`) |
| `llm:openai-compatible:<model>@<base_url>` | `LLMAgent` against `{base_url}/chat/completions` (Ollama `http://localhost:11434/v1`, vLLM, LM Studio...). Without `@<base_url>`, `HOUSE_AI_LLM_BASE_URL` |

Options a constructor does not accept are ignored, so one option dict (CLI `--agent-opt KEY=VALUE`, JSON values)
can serve any agent.

## LLMAgent (llm.py)

Each step is one **stateless** request: a static system prompt plus one user turn. Nothing earlier is ever replayed
or edited, so there are no history-editing or thinking-block replay issues, and the static prefix stays cacheable.

* **System prompt** (static, prompt-cached): robot role; perception (handles, inspect/read/measure, look-alikes);
  norms (safety first, privacy, permission/authority via `ask` with aspect `ok`, `act` and `about` -- the older topic
  `permission:<action>:<handle>` also works -- ask when ambiguous but not unnecessarily, minimal side effects); an
  "Asking, reporting, waiting, memory" section (kernel v3.1, about 2 KB: the question aspects and subjects,
  clarify-back, what counts against the agent, typed reports and their delivery channels, waits that end on what the
  robot perceives and auto standing orders, recall forms, `obs.request` and `obs.notices`; docs/DIALOGUE.md); the
  command format; the `done` protocol (judged at that moment, do not undo work, do not stop early); the compact
  action catalog (`name(param:type, opt?:type, choice:a|b, n[min..max]) - doc`, docs cut to `doc_chars`, 120: a doc
  whose sentence ends within the last quarter of the limit is cut there, so the schema sentence of `ask`, `report`,
  `recall`, `wait` and `watch` arrives whole, else cut with "..."). About 95 KB for the full catalog. When the catalog offers the camera verbs
  (`face`, `peek`: geometric-camera episodes; `env.action_names()` hides them in room mode) a camera paragraph
  (`llm.CAMERA_PROMPT`) explains framing, the view levels ("object", category, full description), the `view` dict,
  `face` / `look around` (`rows` 2) / `look up|down|left|right` / `peek`, `obs.camera` and `obs.lidar`.
* **User turn**: instruction (the observation's `instruction` when it has one -- the current request of a
  multi-phase episode -- else the episode's), step number (`of at most N` when `--max-steps` is set), the memory note, the last
  `history` (8) steps as `command -> ok|refused[category]: message`, and the compacted observation: empty fields
  and unremarkable defaults (clean, looks dry, room temperature) dropped, lists capped at `max_list` (25), at most
  `max_entities` (80) entities, nearest first, every handle verbatim, at most `max_obs_chars` (12000).
* **Answer**: `{"action", "target", "destination", "params": {...}, "note"}`. `params` (and any other top-level
  keys) become named parameters; `note` replaces the running memory note (`max_note_chars` 1500). On a parse
  failure there is `parse_retries` (1) retry with the problem quoted back; still unusable -> an invalid step. A
  `refusal` stop reason is not retried.

### Providers

* **anthropic**: `POST {base_url or https://api.anthropic.com}/v1/messages`, headers `x-api-key`
  (`ANTHROPIC_API_KEY`), `anthropic-version: 2023-06-01`, `content-type: application/json`. Body: `model`,
  `max_tokens` (`max_output_tokens`, 8000), `system` = one text block with `"cache_control": {"type": "ephemeral"}`,
  `messages`, `tools` = one tool `act` with an `input_schema`, `tool_choice`. Claude Opus 5.5, Sonnet 5.5, Fable 5.1
  and Mythos 5.1 reject forced tool use, so they get `{"type": "auto", "disable_parallel_tool_use": true}` plus the
  prompt instruction to always call `act`; other models get `{"type": "tool", "name": "act"}` (override with
  `tool_choice=auto|tool`). A text answer containing a JSON command is accepted too. Current Claude models reject
  non-default sampling parameters, so `temperature` is sent only with `send_temperature=true`; there is no seed.
  Thinking is left at the model default (adaptive); `effort=low|medium|high|xhigh|max` sets `output_config.effort`.
  `fallbacks` (server-side refusal fallback, `"default"` or `[{"model": ...}]`) is **off by default** because a
  fallback model would silently answer under another model's name in the results; when enabled, transcripts record
  `served_model`. Usage: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.
* **openai-compatible**: `POST {base_url}/chat/completions`, `Authorization: Bearer $OPENAI_API_KEY` when set;
  `model`, `messages` (system + user), `temperature` (0.0 default), `max_tokens` (2000), `seed` when given,
  `response_format: {"type": "json_object"}` unless `json_mode=false`. Usage `prompt_tokens`,
  `completion_tokens`, `prompt_tokens_details.cached_tokens`. An unlisted model is free only on a loopback host; a
  hosted one needs a price in prices.json (or `prices_path`) or an explicit `--agent-opt price_free=true` (see
  Prices below).

### Safety rails

* The default transport refuses every request. A real urllib transport exists only when `allow_network=True` is
  passed explicitly (the CLI does this for `--allow-network`) **and** the provider is configured
  (`ANTHROPIC_API_KEY`, or a base URL for openai-compatible). `HOUSE_AI_FORBID_NETWORK=1` makes even that transport
  refuse (the tests set it, and also stub `urllib.request.urlopen` and non-loopback sockets).
* Keys are read from the environment at request time and only placed in request headers. They are never stored in
  agent options, `run.json`, logs, the response cache (keyed by provider, URL without credentials, and body) or
  transcripts. Keys and the split key are never written to disk by the harness.
* Credentials embedded in a base URL (`https://user:password@host/...`, or credential-looking query parameters such
  as `?api-key=...`) are used for the request only. `run.json` `agent_spec`, the agent name (`canonical_name`), the
  default run id and fatal HTTP error messages all go through `agents.redact_url_credentials`. Prefer environment
  variables for keys anyway.
* `run()` refuses an `llm:` agent without `allow_network` before it creates any results directory.
* `run()` also refuses, before any transport exists or anything is written, a model **without a usable price**
  (see Prices below) when a cost cap is set or its host is not loopback; `LLMAgent` repeats the check before every
  request (`LLMConfigError`, fatal), so a direct `run_episode` cannot bypass it either.

### Cost, caps, cache, retries, rate limit, parallelism

* **Prices**: `house_sim/eval/prices.json` ("VERIFY CURRENT PRICES BEFORE USE"; USD per million tokens: input,
  output, cache_write, cache_read, optional long-context tier). Override with `--agent-opt prices_path=FILE`.
  `llm.price_for(provider, model, prices, base_url=None, price_free=False)` resolves a price and says where it came
  from (`source`):
  * `model`: the model is listed by name (always wins, also over `price_free`);
  * `default`: the provider's `default` entry, flagged `unknown` (unknown Anthropic models: the conservative most
    expensive tier, so caps trip early rather than never);
  * `loopback`: an unlisted `openai-compatible` model whose base URL host is loopback (`localhost`, `127.0.0.0/8`,
    `::1`; not look-alikes such as `localhost.example.com`): local servers (Ollama, vLLM, LM Studio) are free
    (`"(loopback)"` entry);
  * `declared_free`: an unlisted model with `--agent-opt price_free=true` (the user declares the endpoint free);
  * `none`: **no price at all** (`unknown` and `unpriced`): an unlisted `openai-compatible` model on any other host
    (`"default": null` in prices.json means unknown, not free). Its $0 estimate would make every cost cap a no-op
    against a paid endpoint, so such a run is **refused** when `--max-cost-usd` / `--max-episode-cost-usd` is set or
    the host is not loopback, unless the model is added to prices.json (or a `prices_path` file) or declared free.
    A local proxy on `localhost` that forwards to a paid API is the user's responsibility: list its models.

  `run.json` `prices` records the file's path, sha256 and `as_of`, plus `source`, `unknown`, `unpriced`,
  `declared_free` and the resolved `per_million` prices; each log's `agent_info.price` has the same `source` flags.
  The dry run prints a prominent `WARNING` for an unpriced, default-priced or declared-free model and says when the
  real run would be refused (`report["warnings"]`, `report["price"]`).
* **Cost** of a response = uncached input x input + cache writes x cache_write + cache reads x cache_read + output x
  output. `cost_usd` in a record is this estimate for every response the agent used, whether it came from the
  network or the cache, so caps trip at the same step on reruns; `spent_usd` in the log's usage counts network
  responses only.
* **Caps**: before each request the agent checks the per-episode token cap (`--max-tokens`), per-episode cost cap
  (`--max-episode-cost-usd`) and the per-run cost cap (`--max-cost-usd`, a thread-safe ledger shared by all
  episodes, with reservations so parallel workers cannot overshoot together) against a worst case (prompt estimated
  at 3 characters per token at the higher of the input and cache-write prices, plus `max_output_tokens` of output).
  After each step the harness also checks the recorded totals. When the run ledger is exhausted, remaining episodes
  are skipped (`run.json`: `episodes_skipped`, `skipped_reason`).
* **Response cache**: on disk (`--cache-dir`, `--no-cache`), so a rerun makes no calls (`usage.cache_hits`): the
  cache directory also keeps the secret `episode_nonce.key` from which a rerun's episodes get the same nonces and so
  the same handles (deleting it, or a fresh cache, gives new ones). The key
  is the SHA-256 of (namespace, source, URL without credentials, request body): the namespace is the provider name
  for `LLMAgent` and `mock` for `MockLLMAgent`; the source is where answers come from (`llm.cache_source`:
  `network` for the real `UrllibTransport`, also read by a network-disabled agent; `mock`, `fake` or `custom:<class>`
  for canned or injected transports). The stored record's meta repeats namespace, source and transport and a hit
  must match them, so canned mock answers (`mock:claude-opus-5-5 --agent-opt tool_choice=auto` builds byte-identical
  Anthropic bodies) or injected `FakeTransport` answers can never be served to a real run. The default cache
  (`results/cache`; for the test split the run's private cache) is used **only by real network runs**; a mock or
  injected transport caches only into a `--cache-dir` given explicitly, in its own namespace. Provenance is
  recorded: `run.json` `llm_transport` (`network` / `mock` / `fake` / ...) and `llm_cache` (`enabled`, `source`,
  `namespace`, `explicit`, `private`), `totals.llm_responses` / `llm_cache_hits` / `llm_cache_hit_rate`, and each
  log's `llm_cache` (`namespace`, `source`, `transport`, `enabled`, `responses`, `hits`), so a leaderboard can flag
  runs served mostly from the cache. Entries written before this scheme (key version 1) are never hit. **Test split**: a
  cached response holds the model's whole reply (text, thinking, tool input and memory note) for a private episode,
  which can quote or paraphrase the held-out phrasing, so the cache defaults to
  `%USERPROFILE%/.house_ai/private_results/<run_id>/cache` (next to the private replays and transcripts). A
  `--cache-dir` (or `--agent-opt cache_dir=...`) inside the repository, the results root or the run's `--out`
  directory is refused before anything is written; pass a private directory outside the repository to share a cache
  between test runs. `run.json` then records `"llm_cache_dir": null` and `"llm_cache_private": true`.
* **Retries**: 408/409/425/429/5xx/529 and connection errors, up to `max_retries` (4) with exponential backoff
  (`backoff_base` 1 s doubling, jitter, `backoff_max` 30 s; `retry-after` honoured). Other 4xx are fatal: the episode
  ends with `agent_error` and the run stops scheduling new episodes (`status: aborted`).
* **Rate limit**: `--rpm N` request starts per minute shared by all workers.
* **Parallel episodes**: `--workers N` threads (cap 64), one fresh agent per episode; `episodes.jsonl` is written in
  episode order. The simulator is CPU-bound, so threads mostly help LLM runs.

## Command line

```
python -m house_sim --run-agent AGENT_SPEC --split dev|train|test [--ids ...] [--sections ...] [--kinds ...]
    [--domains ...] [--limit N] [--seeds ...] [--seeds-per-entry N] [--workers N] [--out results/<run_id>/]
    [--run-id ID] [--dry-run] [--max-steps N] [--max-sim-s S] [--max-cost-usd X] [--max-episode-cost-usd X]
    [--max-tokens N] [--max-wall-s S] [--max-consecutive-invalid N] [--no-require-done] [--allow-network]
    [--verify-on-the-fly [--verify-seeds ...] [--verify-perception realistic|oracle] [--verify-workers N] | --allow-unverified]
    [--perception realistic|oracle|episode] [--camera room|geometric] [--agent-opt KEY=VALUE ...] [--cache-dir DIR]
    [--no-cache] [--rpm N]
    [--no-replays] [--quiet]
```

Examples:

```
python -m house_sim --run-agent oracle --split dev --ids T-089c40fb507289-01 --verify-on-the-fly
python -m house_sim --run-agent random --split train --seeds-per-entry 2 --limit 20 --verify-on-the-fly --agent-opt seed=7
python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --limit 10 --dry-run --max-steps 40
python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --limit 10 --max-steps 40 --max-cost-usd 5 --allow-network --verify-on-the-fly --agent-opt effort=medium
python -m house_sim --run-agent "llm:openai-compatible:llama3.1:8b@http://localhost:11434/v1" --split dev --limit 5 --allow-network --verify-on-the-fly
```

* `--dry-run` prints the episode list with estimated input/output tokens and expected and worst-case cost per
  episode (from the actual system prompt and a compacted first observation per spec, the step budgets, ~400 output
  tokens per step (`--agent-opt expected_output_tokens=N`), a warm prompt cache, prices.json) and makes no calls and
  writes nothing. With `--max-cost-usd` the totals are capped (uncapped figures shown too). With
  `--verify-on-the-fly` the dry run first verifies the selected specs (offline simulator work, no LLM call, nothing
  written) and estimates only the ones that pass, i.e. exactly the episodes the real run would execute; without it,
  every selected spec is estimated (with a warning when evidence is stale). Example, the whole dev split:
  `python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --dry-run --verify-on-the-fly`.
* Real LLM runs require `--allow-network` and the provider's key in the environment. Exit codes: 0 run complete
  (or skipped by the cost cap), 1 aborted, 2 configuration error.
* `--perception` defaults to `realistic` (the evaluation mode; the test split is always realistic). `oracle` forces
  oracle perception for dev/train debugging; `episode` uses each episode's own field from `splits.episodes`.
* `--camera room|geometric` forces the camera mode of every episode (kernel v3.0); without it each spec's own
  `"camera"` (else `camera.default_mode`) applies. A run in the camera mode that is not the default is a different
  evaluation config: its leaderboard rows are labelled (`[geometric camera]`) and never merged with the other mode.

### Verified specs

By default only specs with **current passing verification evidence** run: a passing record in
`reports/verification.jsonl` whose `implementation_hash == house_sim.audit.fingerprint()` and whose `spec_hash`
matches. While the domain code changes that evidence goes stale, so:

* `--verify-on-the-fly` verifies the selected specs now with `house_sim.tasks.verify.verify_many` on the dev seeds
  (`--verify-seeds`), in each spec's own perception mode (`--verify-perception realistic` forces the mode the
  harness episodes run in) and, with `--camera room|geometric`, in the camera mode the episodes run in (a spec whose
  reference fails with the geometric camera is not kept for a geometric run); failing specs are dropped. `run.json`
  records the verification's perception and camera (`"verification": {"perception", "camera", ...}`).
* `--allow-unverified` runs them anyway, marked `"verification": {"mode": "unverified"}` in `run.json`.

Unverified specs are never silently treated as verified; `run.json` lists the verification source and detail per
spec. A dry run without `--verify-on-the-fly` estimates every selected spec and warns when evidence is missing.

A catalog id authored in more than one `tasks/authored/*.json` file (while an author moves a spec) is left out of
`load_specs()` with a warning and listed in `run.json` `authored_conflicts`, instead of making every spec unusable
(`house_sim.eval.common.load_authored`).

## How a spec becomes "verified": the release gate and orchestrator checks

Verification (`house_sim/tasks/verify.py`) runs on every seed: the initial state and waiting out the budget must not
satisfy the task, the reference plan must (strictly), and every wrong plan must run fully and fail. A wrong plan that
raises (PlanError or any exception) never performs its mistake, so it is not evidence: its spec fails, with a
`wrong_not_run` message naming the plan and the step. Each wrong plan's record keeps its failure labels, their
classes (`reasons`) and its goal fraction.

Three tools stand between an edit and a spec counted as verified; none of them trusts an agent's own report:

* **The tier-1 gate** (`tools/orchestration/tier1_gate.py`, `tools/orchestration/README.md`): every package records
  the whole check list before hand-over. Absolute rules: every authored spec passes in its own mode on every seed
  (G2-ABS) and every test passes, except unexpired entries of the main session's dated allowlist. Every verdict
  change and every edited spec needs the package's reviewed row (bound to the spec files the package owns) and the
  main session's committed signature. Audits fail on any leak, crash, defect or nondeterministic replay; a check that
  should have run and did not counts as a failure; the fuzzer reports per-action success coverage.
* **Orchestrator checks** (`tools/orchestration/run_check.py`): the authoring workflow re-runs `--check` itself
  after every author and review step and appends the verdicts to a hash-chained log
  (`catalog/review/check_log/orchestrator.jsonl`); the batch's result is the tool's passing set.
* **CI** (`.github/workflows/ci.yml`): the unit suite and every spec on one seed, on Python 3.9 and 3.14, Windows and
  Linux, offline (`HOUSE_AI_FORBID_NETWORK=1`), no secrets.

## Results (`results/<run_id>/`)

* `run.json`: `format`, `harness_version` (`"2"`), `run_id`, `status` (running / complete / incomplete / aborted),
  `agent`, `agent_spec`, `privileged`, `split`, `filters`, `seeds` (never for test), `caps` (including
  `require_done` and `max_steps`: the done protocol and step cap every log must repeat), `workers`, `perception`,
  `perception_modes` (the modes the episodes actually ran in) and `privileged_perception` (true when that is anything
  but `["realistic"]`: such a run must be labelled and ranked apart from normal runs, like a privileged agent),
  `camera` (the `--camera` value, null: each spec's own) and `camera_modes` (the camera modes the episodes ran in),
  `code_fingerprint` (`house_sim.audit.fingerprint()`: simulation code and data, which
  deliberately excludes `house_sim/eval` so harness edits do not invalidate verification evidence),
  `harness_fingerprint` (`house_sim.audit.harness_fingerprint()`: every `.py`, `.json` and `.html` file of
  `house_sim/eval`, i.e. the done protocol, `normalize_command`, the LLM prompt, prices.json and the viewer
  template), `actions_hash`, `spec_hashes` (`house_sim.tasks.verify.spec_hash`), `commitment_root`
  (`splits.commitment_root()`), `episode_source` (`splits` or `fallback`), `verification`, `authored_conflicts`,
  `agent_options` (credential-like keys dropped), `allow_network`, `llm_cache_dir` (null for test runs),
  `llm_cache_private` (true when a test run's cache lives in the private directory), `llm_transport`, `llm_cache`,
  `prices`, `sim_safe_mode`, `started`, `ended`, `episodes_planned` / `episodes_run` / `episodes_skipped`,
  `planned_episodes` (the exact `[catalog_id, seed_tag]` list the run was asked to execute, public tags only) and
  `planned_episodes_sha256` (so deleted, duplicated or added episodes are detectable), `ledger`,
  `totals` (LLM runs add `llm_responses`, `llm_cache_hits`, `llm_cache_hit_rate`). Runs, logs or re-scores whose
  `code_fingerprint` **or** `harness_fingerprint` differ were produced by different code and must not be merged
  (the leaderboard compares both).
* `episodes.jsonl`: one record per episode with exactly the contract keys `catalog_id, split, seed_tag, agent,
  privileged, success, goal_fraction, failures, harm_flags, side_effects, blocks{safety,privacy,authority},
  invalid_steps, questions{necessary,unnecessary}, steps, sim_time_s, wall_s, tokens{input,output}, cost_usd,
  done_declared, end_reason, log, spec_hash, perception, camera, kind, section, domain, risk_level, fidelity`, plus
  `seed`
  for dev/train only. `harm_flags` = failure labels that are not about the goal, process, side effects or questions
  (global flags such as damage/fire and domain invariant harms); `side_effects` = `changed:/moved:/broke:/soiled:/
  new_mess:` labels; `tokens.input` counts all prompt tokens including cached ones.
* `logs/<catalog_id>__<seed_tag>.json` with `:` written as `-` (Windows file names; `splits.safe_tag`); the record's
  `log` field holds the exact relative path. Format `house-harness-log-v1`: episode metadata (`harness_version`,
  `code_fingerprint`, `harness_fingerprint`, `perception`, `camera`, `privileged`, `caps`), `seed` and `instruction`
  (dev/train) or `instruction_hmac` and `private_digest_scheme` (test, instead of the instruction; see Test split
  below), `require_done`, `limits`, `llm_cache` (LLM agents), and `steps`: one item per agent
  turn with `command` (as the agent issued it; for test episodes only what reaches the simulator, see below), `sent`
  (the kwargs given to `env.step`), `parse`, `result` (what
  the agent saw), `t_agent_s`, `t_env_s`, `sim_time_s`, per-step `usage`; a `done: true` item; a `discarded` item when
  the final turn after the step budget was not done; an `error` item for `sim_error`. Then `end_reason`,
  `done_declared`, `evaluation`, `usage`, `unparseable`, `unknown_actions`, `agent_info`, `replay`, `transcript`.
* `replays/<name>.replay.json`: `env.save_replay` for the viewer (`HouseEnv.replay` reproduces it exactly). Each is
  a compact `house-replay-v3` file (kernel/replays.py: the start state, the baseline and the command log exactly, the
  bulky parts compressed together) of about 60-95 KB, i.e. about 4-5 MB for 55 episodes (the `house-replay-v2` files
  of earlier runs were about 1-3 MB each; `python tools/compact_replays.py results/` converts them losslessly).
  `--no-replays` (`save_replays=False`; `run.json` `replays_saved`) skips them: the log's `replay` is then `null`, the
  leaderboard re-scores from the log alone and the viewer rebuilds the episode from the spec and seed.
* `transcripts/<name>.jsonl` (LLM agents): per call the request (system prompt and tool schema replaced by their
  hashes; the prompt itself stored once as `transcripts/system_<sha>.txt`), the response, usage, cost, cache hit,
  retries, latency, served model. Never API keys.

**Re-scoring**: `run.replay_log(log, spec, seed=None, phrasing=None)` rebuilds the episode, re-issues every `sent`
command and returns the evaluation and done-protocol success (the leaderboard's `verify_submission` does the same),
plus `code_fingerprint_match` and `harness_fingerprint_match` (`None` for a log that records no fingerprint): a
re-score under different simulation or harness code is not evidence for the original run. `run.versions()` returns
both current fingerprints.

**Test split**: episodes come from `splits.episodes("test", ...)` (split key and private manifest required; raw
`--seeds` are refused). Records and logs carry only the seed tag, never the raw seed or the private phrasing, and
never an **unkeyed** digest of private text: a plain SHA-256 of the instruction could be recovered offline by
enumerating the public phrasings and room ids (or used to confirm a guessed paraphrase). The log's
`instruction_hmac` and `agent_info.memory_note_hmac` (an LLM's free-text memory note, which could paraphrase the
instruction; the note itself is dropped) are `run.private_text_digest(purpose, catalog_id, seed_tag, text)`:
HMAC-SHA256 under a subkey derived from the split key (`HMAC(key, "house-harness/private-text-digest/v1")`), bound to
the purpose (`"instruction"` / `"memory_note"`), the catalog id and the seed tag (`private_digest_scheme` names the
scheme). Without a split key both are `null`. The split-key holder (viewer, leaderboard) recomputes them to check a
recovered phrasing. Env replays, transcripts and the LLM response cache (which embed private text) go to
`%USERPROFILE%/.house_ai/private_results/<run_id>/` instead of the results directory, and `run()` refuses a
`private_dir` or `--cache-dir` inside the repository, the results root or `--out`. A test log's step `command` is
`run.loggable_command(command, private=True)`: the `"_"`-prefixed agent annotations (e.g. an LLM's unusable reply,
`_raw`, which often restates the instruction) and every other key that never reaches `env.step` are dropped. A
usable command is logged as exactly what was sent, an unusable one as `{"action": null, "_redacted": true,
"_invalid": "<problem code>"}`, a done as `{"action": "done"}`. Re-scoring replays `sent`, so it is unchanged. Text
the agent puts into the arguments it actually sends (for example an `ask`) is logged in `sent` by design, because
re-scoring needs it. Dev/train logs keep the command verbatim. `run.json` `commitment_root`
is `splits.commitment_root()` (the published `reports/test_commitment.json`, or `HOUSE_AI_COMMITMENT_FILE` in a
sandbox, see docs/SPLITS.md).

## Python API

```python
from house_sim.eval import run, agents
records = run.load_specs()                                  # authored specs as records, read-only
summary = run.run("oracle", "dev", records[:3], "results/demo", verify="fly", verify_seeds=[0], seeds=[0])
rec = run.run_episode(records[0], {"catalog_id": records[0]["catalog_id"], "split": "dev", "seed": 0,
                                   "seed_tag": "dev:0", "phrasing": None}, agents.make_agent("random", seed=1))
```

`run(agent_spec, split, specs, out_dir, workers=1, caps=None, dry_run=False, **filters)`; filters: `ids, sections,
kinds, domains, limit, seeds, seeds_per_entry, verify ("evidence" | "fly" | "none"), verify_seeds,
verify_perception, verify_workers, perception, camera ("room" | "geometric" | None), save_replays, agent_options,
allow_network, transport (inject a fake;
it never uses the default cache), cache_dir, use_cache, rpm, run_id, private_dir, emit`. caps: `max_steps, max_sim_s,
max_wall_s, max_tokens, max_cost_usd_episode, max_cost_usd, require_done, max_consecutive_invalid`. LLM agent
options include `prices_path`, `price_provider` and `price_free` (declare an unlisted model free).

## Tests

```
python -m unittest tests/test_eval_harness.py
```

Offline, with a temporary HOME/USERPROFILE and no split key unless a test sets a throwaway one (the user's
`~/.house_ai` is never touched): spy-agent leakage, done protocol (achieve-then-undo fails, no done no success, done after the last budgeted
step), invalid JSON / unknown actions / malformed commands counted, cost/token/run/wall-clock caps with mock usage,
cache hits (a rerun makes zero transport calls), Anthropic and OpenAI-compatible request shapes and parsing, retries
and rate limiting, network refusal, test-split privacy, random/search/oracle/mock end to end with the results layout,
re-scoring by replay, parallel determinism, dry run and CLI; the oracle succeeds on every example spec that verifies.
Test-split regressions: an LLM reply that restates the held-out phrasing (text-only, malformed tool input, memory
note) never reaches `results/` (logs, run.json, cache); the test-split response cache lives in the private directory
and is refused inside the repository or `--out`; base-URL credentials never reach `results/`. Red-team regressions:
test logs carry only keyed (split-key HMAC) digests of the instruction and memory note, never a plain SHA-256
(`TestKeyedPrivateDigests`); an unpriced hosted model is refused under a cost cap or on a non-loopback host, by
`run()` before any call or file and by the agent per request, loopback and declared-free models run, and the dry run
warns (`TestUnpricedModels`); mock and injected-transport answers never serve a real run and never write the default
cache, while genuine network entries still do (`TestCacheProvenance`); run.json and every log record both
fingerprints, `replay_log` reports mismatches, and the harness fingerprint moves with harness code and prices.json
(`TestVersionFingerprints`).

## Limits

* `SearchAgentAdapter` is a toy baseline (the legacy fetch agent); it rarely succeeds on catalog tasks. With the
  geometric camera its SearchAgent looks around on entering a room (with the low row while the target is small),
  walks up to far "object" detections whose size fits until it can tell what they are, and, when a room is surveyed
  without a find, kneels and looks under its beds and sofas (`peek`, else `inspect where=under`); it needs the
  extended `look` (CAM-ACTIONS) to survey a room (`tests/test_camera_realism.py` `SearchAgentTests`).
* The geometric camera's tests and audits: `tests/test_camera_realism.py` (episode records, logs, replay re-scoring,
  leaderboard labels, the LLM camera paragraph, the viewer overlay), `tools/perception_audit.py --camera geometric`.
* The system prompt with the full catalog is ~90 KB (~25k tokens; ~44 KB with `--agent-opt doc_chars=0`, names and
  parameters only). Local servers need a context window that large (e.g. `OLLAMA_CONTEXT_LENGTH=32768`), or they
  silently truncate the prompt.
* Token counts for estimates use a conservative characters-per-token rule, not a tokenizer. The expected output is
  ~400 tokens per step; thinking is billed as output, and models whose thinking cannot be turned off (e.g. Claude
  Opus 5.5, adaptive thinking at default effort `medium`) may use several times that. Measure a small pilot run's
  `usage` and pass `--agent-opt expected_output_tokens=N` before trusting a dry-run total.
* Episodes always run in realistic perception, while `--verify-on-the-fly` (like the evidence file) verifies each
  spec in its own mode, which is oracle for most example specs. On 2026-10-09, 22 of the 192 example specs that
  verified had a reference plan that fails under realistic perception (refused "not visible" steps or values the
  robot never observed), so even the privileged oracle cannot reach 100% over the whole dev split.
  `--verify-perception realistic` keeps only specs that are solvable in the episode mode.
* Threads share one process; a hostile in-process agent could read private attributes (docs/EVALUATION.md):
  evaluate untrusted agents in a separate process speaking to the harness.

## Leaderboard and submission verification (`house_sim/eval/leaderboard.py`)

```
python -m house_sim --leaderboard RUN_DIR [RUN_DIR ...] [--out reports] [--allow-mixed] [--verify] [--allow-incomplete]
    [--verify-specs] [--verified-only] [--resamples N] [--ci-seed S] [--workers N]
python -m house_sim --verify-submission RUN_DIR [RUN_DIR ...] [--allow-mixed] [--allow-incomplete] [--no-write] [--workers N]
```

Everything inside a run directory (run.json, episodes.jsonl, logs, transcripts, `submission_verification.json`) is
treated as untrusted input written by the submitter.

**Loading** (`load_runs`). Runs with different code fingerprints (`house_sim.audit.fingerprint()`, simulation code
and data only), harness fingerprints (`harness_fingerprint()`, the digest of `house_sim/eval/**`), test commitment
roots or spec hashes are refused unless `--allow-mixed` (every output is then labelled MIXED VERSIONS). Every episode
record must carry every contract key with its type (`record_problems`, `REQUIRED_RECORD_KEYS`) and a non-empty seed
tag: a record without `blocks`, `questions`, `invalid_steps`, `steps`, `sim_time_s`, `tokens` or `cost_usd` is a load
error, never a 0. Two directories with the same `run_id` (a copied run) are refused. Every command log is read for
the privileged / perception / done-protocol facts it records.

**Rows, labels and tracks** (`aggregate`). One row per (agent, split, evaluation config). The config comes from
run.json, the records and the logs together, and any one of them can only make it less standard:

* privileged: run.json `privileged` (or agent `oracle`), any record or log with `privileged: true`, or a log whose
  `agent_info` holds ScriptedOracle details (`shadow_success`, `plan_refusals`, ...);
* perception: run.json `perception` / `perception_modes`, else any record or log naming a mode other than
  realistic. Oracle perception hands the agent true ids and never-tier fields (owner, home, value, ...), so it counts
  as privileged;
* camera: the record's `camera`, else its log's, else run.json's (`camera`, or a single `camera_modes` entry), else
  `room` (results from before kernel v3.0). The mode that is not `camera.default_mode("realistic")` is a label
  (`[geometric camera]` today); it is not privileged (the geometric camera only ever shows less) and keeps its track.
  The CSV has a `camera` column. Submission verification replays each episode in its log's camera mode;
* done protocol: run.json `caps.require_done`, off if any log says so;
* the protocol caps that differ from the harness defaults (`max_steps`, `max_sim_s`, `max_wall_s`, `max_tokens`,
  `max_cost_usd_episode`, `max_consecutive_invalid`), a digest of `agent_options`, and whether the run is complete
  (`status` complete, nothing skipped, `episodes_run` == the records in episodes.jsonl).

Every deviation is a label on the row (`random [oracle perception]`, `mock [no require_done, max_steps=4]`,
`oracle [privileged, incomplete run]`, `llm:... [options 1a2b3c4d]`), so runs of one agent under different configs
are never merged. Rows are ranked within (split, track): **standard**; **non-standard** (done protocol off, changed
protocol caps, incomplete runs); **privileged** (a privileged agent or non-realistic perception). The Protocol column
lists the deviations, the Episodes column shows `N of M planned` for partial runs, and each row lists its runs'
status and planned / run / skipped counts. Repeated trials of one episode (`--seeds 0 0 0`, or the same episodes in
two runs of one config) are averaged per episode (each episode weighs the same, the order of the records never
matters) and reported as `trials` / `repeated_trials`; nothing is "first one wins". Breakdowns (kind, section,
domain, risk level, fidelity, perception-centric) come from the catalog and the authored spec, never from the record.

**Verification** (`verify_submission`, `--verify-submission`, `--leaderboard --verify`). A run passes only if

* the episode set equals the one run.json declares: `splits.episodes` over its spec set (`spec_hashes`; with
  on-the-fly verification `verification.per_spec`, since the limit is applied before failing specs are dropped), its
  `seeds` / `filters.seeds_per_entry`, then `filters.limit` and `verification.excluded`. A deleted, added or duplicated
  record, a record of another split, two records sharing one log, an empty seed tag, a dev seed outside the published
  dev seeds (`DEV_SEEDS` + `DEV_EXTRA`), a train seed outside the train range or a private seed rejects the run;
* the run is complete; `--allow-incomplete` (`allow_incomplete=True`) accepts a consistent partial run (stopped by
  the run cost cap or an abort, the missing episodes being exactly the skipped ones) and labels the verdict;
* the done protocol, caps and perception come from run.json and every record and log agrees with them and with each
  other on agent, privileged, run id, perception, `require_done`, caps and code / harness fingerprints;
* every episode replays within run.json's limits (`episode_limits`: no more environment steps than
  min(task step budget, `caps.max_steps`), none after the simulated time budget or after `max_consecutive_invalid`
  consecutive refusals, a done or a step-budget end only where the harness allows it, nothing logged after the item
  that ended the episode) and every score field re-scores identically (an absent field is a mismatch);
* tokens and cost equal the log's usage totals, which cover the per-step usage and add up to the transcript's per-call
  usage when there is one; kind / section / domain match the catalog and risk level / fidelity the built task;
* the run's code and harness fingerprints equal the current ones (`--allow-mixed` relaxes this and labels the verdict).

Privileged agents and non-realistic perception verify with a warning (the trajectories are legal) and are ranked in
the privileged track. Replay proves that the logged trajectories are legal and re-score identically under the declared
protocol; it cannot prove who produced them: a fully re-forged log (agent, privileged flag and usage rewritten
consistently) is indistinguishable from a real one, and wall-clock caps are not re-checkable.

**Trusted verdicts.** The Verified column shows "passed" only for a verdict produced by `verify_submission` in the
same process for exactly the current run digest (`run_digest`: run.json, episodes.jsonl, every file under logs/ and
transcripts/ and every log a record points to), or for a `submission_verification.json` whose HMAC verifies with this
machine's verifier key (`HOUSE_AI_VERIFIER_KEY`, else `~/.house_ai/verifier_key`, created by the CLI on first use;
API callers pass `sign=True`). Either verdict must name the run's `run_id` and digest and the code and harness
fingerprints running now. An unsigned or foreign-signed file shows as "not verified (unsigned verdict file)", a file
for changed content or code as "stale"; the file's absolute `run_dir` stays in the run directory, which is not
packaged.

**Reports** (`write_reports`): leaderboard.md / .csv / .json / .html in `--out` (default `reports/`). Paths in the
written files are relative to the output directory, or just the run directory's name when the relative path would
climb through the home folder or cross drives (`portable_path`); no absolute path or user name is written.
Test-split rows stay aggregate-only.

Tests: `python -m unittest tests/test_eval_leaderboard.py` (temporary HOME, throwaway split and verifier keys, real
harness runs tampered in every way listed above).

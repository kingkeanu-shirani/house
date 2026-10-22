"""Agent harness: run agents on task episodes and record re-scorable results (docs/EVAL_HARNESS.md).

    python -m house_sim --run-agent AGENT_SPEC --split dev|train|test [--ids ...] [--sections ...] [--kinds ...]
        [--limit N] [--seeds ...] [--seeds-per-entry N] [--workers N] [--out results/<run_id>/] [--dry-run]
        [--max-steps N] [--max-cost-usd X] [--allow-network] [--verify-on-the-fly | --allow-unverified] ...

Episode protocol: build (spec, episode) -> HouseEnv(perception="realistic", camera=<--camera or the spec's>, trace=False,
observe_on_step=True) ->
agent.reset(instruction, observation, action_catalog) -> loop agent.act(observation, last_result) until the agent
emits the harness-level {"action": "done"}, the task's step budget (or --max-steps), the task's time budget, or a
harness cap (wall clock, tokens, cost) ends the episode. Success is the environment's evaluation at that end AND a
declared done (require_done; so achieving the goal and then undoing it fails). The agent only ever receives the
instruction, observations and {"ok", "message", "category"} of its previous step.

Multi-phase tasks (kernel v3.1, TIER1_SWEEP 9; docs/EVAL_CONTRACTS.md): a "done" while a phase is active ends that
phase (env.phase_done(): it is scored, its gap runs and the next phase starts) and, when another phase follows, the
episode continues: the agent's optional hook on_request(request) receives the new request (the observation's
"request", else {"text": instruction}) and its next act() the new observation, whose "instruction" is the new phase's.
The same hook is called when the environment starts a phase by itself (a phase that ends on time, on a condition or
on its budget). The log records each phase end as a step {"phase_done": true, "phase", "next_phase", "waiting"};
replay_log and the leaderboard's replay_episode replay it in order (env.phase_done()). Records carry "phases":
[{id, success, steps, time_s, end_reason}] (None for single-phase tasks).
"""
import argparse
import copy
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..catalog import ROOT, read_jsonl
from .agents import (Budget, BudgetExceeded, CostLedger, action_catalog, canonical_name, filter_result, is_done, make_agent,
                     parse_agent_spec, redact_url_credentials)

HARNESS_VERSION = "2"     # 2: keyed private-text digests (instruction_hmac), harness_fingerprint, cache provenance
PRIVATE_DIGEST_SCHEME = "hmac-sha256/split-key-subkey/house-harness-private-text-v1"
_PRIVATE_DIGEST_INFO = b"house-harness/private-text-digest/v1"
DEFAULT_CAPS = {"max_steps": None, "max_sim_s": None, "max_wall_s": 3600.0, "max_tokens": None, "max_cost_usd_episode": None,
                "max_cost_usd": None, "require_done": True, "max_consecutive_invalid": None}
UNPARSEABLE = "__unparseable__"          # an unknown action: the environment refuses it and counts an invalid step
FALLBACK_DEV_SEEDS = (0, 1, 2, 41, 97)
FALLBACK_TRAIN_BASE = 1_000_000
FALLBACK_PRIVATE_BASE = 900_000_000
SIDE_EFFECT_PREFIXES = ("changed:", "moved:", "broke:", "soiled:", "new_mess:")
NON_HARM_PREFIXES = ("failure_predicate_", "order:", "deadline:") + SIDE_EFFECT_PREFIXES
NON_HARM_LABELS = {"budget", "too_many_unnecessary_questions", "missing_necessary_question"}
RECORD_KEYS = ("catalog_id", "split", "seed_tag", "agent", "privileged", "success", "goal_fraction", "failures", "harm_flags",
               "side_effects", "blocks", "invalid_steps", "questions", "steps", "sim_time_s", "wall_s", "tokens", "cost_usd",
               "done_declared", "end_reason", "log", "spec_hash", "perception", "camera", "kind", "section", "domain",
               "risk_level", "fidelity", "phases")
CAMERA_MODES = ("room", "geometric")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _results_root():
    return ROOT / "results" if (ROOT / "house_sim").is_dir() else Path.cwd() / "results"


def default_private_dir(run_id):
    """Test-split artifacts that embed the raw seed or the private phrasing (env replays, LLM transcripts) go here,
    never into the results directory."""
    return Path.home() / ".house_ai" / "private_results" / run_id


def _within(path, base):
    try:
        path, base = Path(path).expanduser().resolve(), Path(base).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return path == base or base in path.parents


def _refuse_public_location(path, out_dir, what):
    """Test-split private artifacts must not live where results are published: inside the repository (which holds
    results/ and reports/), the results root or this run's results directory."""
    for base in (ROOT, _results_root(), out_dir):
        if base is not None and _within(path, base):
            raise ValueError("refusing to put {} at {}: it is inside {}, which is publishable; test-split artifacts can "
                             "contain the held-out phrasing. Use a directory outside the repository (default {})".format(
                                 what, path, base, Path.home() / ".house_ai" / "private_results" / "<run_id>"))


# ----- splits (house_sim/eval/splits.py, owned by another component) -----------------------------------------------
def _splits():
    try:
        from . import splits
        return splits
    except Exception:  # noqa: BLE001  (absent or mid-edit: dev/train fall back, test refuses)
        return None


def is_private_seed(seed):
    splits = _splits()
    if splits is not None and hasattr(splits, "is_private_seed"):
        return bool(splits.is_private_seed(seed))
    return isinstance(seed, int) and seed >= FALLBACK_PRIVATE_BASE


def seed_tag(split, catalog_id, seed):
    splits = _splits()
    if splits is not None and hasattr(splits, "seed_tag"):
        return splits.seed_tag(catalog_id, seed)
    if split == "test" or is_private_seed(seed):
        raise RuntimeError("test seed tags need house_sim.eval.splits")
    return "{}:{}".format(split, seed)


def _split_key():
    """The split key (bytes) when one is configured (HOUSE_AI_SPLIT_KEY or the private key file), else None."""
    splits = _splits()
    if splits is None or not hasattr(splits, "load_key"):
        return None
    try:
        return splits.load_key(required=False)
    except Exception:  # noqa: BLE001  (a malformed key: no digest rather than a crash; the test split refuses it anyway)
        return None


def private_text_digest(purpose, catalog_id, tag, text, key=None):
    """Keyed digest of a private test-split text (purpose "instruction" or "memory_note"): HMAC-SHA256 under a
    subkey derived from the split key (HMAC(key, _PRIVATE_DIGEST_INFO)), bound to the episode (catalog id, seed tag)
    and the purpose. A plain SHA-256 of the instruction could be confirmed or recovered offline by enumerating the
    public phrasings and room ids; this one needs the key. None when no key is available (nothing is recorded then).
    The viewer and the leaderboard recompute it with the key to check a recovered phrasing."""
    if key is None:
        key = _split_key()
        if key is None:
            return None
    if isinstance(key, str):
        key = bytes.fromhex("".join(key.split()))
    subkey = hmac.new(bytes(key), _PRIVATE_DIGEST_INFO, hashlib.sha256).digest()
    message = json.dumps([str(purpose), catalog_id, tag, text if isinstance(text, str) else str(text)],
                         ensure_ascii=False, separators=(",", ":"))
    return hmac.new(subkey, message.encode("utf-8"), hashlib.sha256).hexdigest()


def versions():
    """{"code_fingerprint": house_sim.audit.fingerprint() (simulation code and data; deliberately excludes
    house_sim/eval), "harness_fingerprint": house_sim.audit.harness_fingerprint() (house_sim/eval/**: done protocol,
    normalize_command, prompts, prices)}. Recorded in run.json and every log; runs or logs whose values differ were
    produced or scored by different code."""
    from .. import audit
    out = {"code_fingerprint": audit.fingerprint()}
    out["harness_fingerprint"] = audit.harness_fingerprint() if hasattr(audit, "harness_fingerprint") else None
    return out


def make_episodes(split, records, seeds_per_entry=None, seeds=None, perception="realistic"):
    """-> (episodes, source). Uses splits.episodes when available (perception forced for dev/train unless None; test
    is always realistic); dev/train fall back to the public seeds."""
    if split not in ("dev", "train", "test"):
        raise ValueError("split must be dev, train or test")
    if split == "test" and seeds:
        raise ValueError("raw test seeds cannot be passed; the test split comes from the private manifest")
    splits = _splits()
    if splits is not None and hasattr(splits, "episodes"):
        forced = None if split == "test" else perception
        episodes = splits.episodes(split, [r["spec"] for r in records], seeds_per_entry=seeds_per_entry, seeds=seeds, perception=forced)
        return [dict(e) for e in episodes], "splits"
    if split == "test":
        raise RuntimeError("the test split needs house_sim.eval.splits and the split key; neither is available")
    episodes = []
    for record in records:
        cid = record["catalog_id"]
        if seeds:
            chosen = [int(s) for s in seeds]
        elif split == "dev":
            chosen = list(FALLBACK_DEV_SEEDS)
        else:
            digest = int(hashlib.sha256(cid.encode("utf-8")).hexdigest()[:8], 16)
            chosen = [FALLBACK_TRAIN_BASE + (digest + 7919 * i) % (FALLBACK_PRIVATE_BASE - FALLBACK_TRAIN_BASE) for i in range(seeds_per_entry or 1)]
        if seeds_per_entry:
            chosen = chosen[:int(seeds_per_entry)]
        for seed in chosen:
            if is_private_seed(seed):
                raise ValueError("test seed refused")
            episodes.append({"catalog_id": cid, "split": split, "seed": seed, "seed_tag": "{}:{}".format(split, seed),
                             "phrasing": None, "perception": perception or "realistic"})
    return episodes, "fallback"


# ----- specs, metadata and verification -------------------------------------------------------------------------
def _domain_of(source_file):
    if not source_file:
        return None
    stem = Path(str(source_file)).stem
    return stem.split("__")[0] if "__" in stem else stem


_META_CACHE = {}
LAST_CONFLICTS = {}      # catalog ids load_specs() left out because they are authored in more than one file


def load_specs(root=ROOT):
    """Authored specs as records {"catalog_id", "kind", "section", "source_file", "domain", "spec"} in catalog
    order. Read-only (does not recompile tasks/specs.jsonl). A catalog id authored in several files is left out
    (listed in LAST_CONFLICTS and in run.json) instead of making every spec unusable."""
    from .common import load_authored
    specs, sources, conflicts = load_authored(root)
    LAST_CONFLICTS.clear()
    LAST_CONFLICTS.update(conflicts)
    entries_path = root / "catalog" / "entries.jsonl"
    entries = read_jsonl(entries_path) if entries_path.exists() else []
    order = {e["id"]: i for i, e in enumerate(entries)}
    by_id = {e["id"]: e for e in entries}
    records = []
    for cid in sorted(specs, key=lambda c: (order.get(c, 10 ** 9), c)):
        entry = by_id.get(cid, {})
        records.append({"catalog_id": cid, "kind": entry.get("kind"), "section": entry.get("section"),
                        "source_file": sources.get(cid), "domain": _domain_of(sources.get(cid)), "spec": specs[cid]})
    return records


def _meta(catalog_id):
    if "map" not in _META_CACHE:
        try:
            _META_CACHE["map"] = {r["catalog_id"]: {k: r.get(k) for k in ("kind", "section", "domain", "source_file")} for r in load_specs()}
        except Exception:  # noqa: BLE001
            _META_CACHE["map"] = {}
    return _META_CACHE["map"].get(catalog_id, {})


def as_record(spec):
    """Accept a raw spec dict or a record with a "spec" key."""
    if isinstance(spec, dict) and isinstance(spec.get("spec"), dict):
        record = dict(spec)
        record.setdefault("catalog_id", spec["spec"].get("catalog_id"))
        if not record.get("domain"):
            record["domain"] = _domain_of(record.get("source_file")) or _meta(record["catalog_id"]).get("domain")
        return record
    cid = spec.get("catalog_id")
    meta = _meta(cid)
    return {"catalog_id": cid, "kind": meta.get("kind"), "section": meta.get("section"), "source_file": meta.get("source_file"),
            "domain": meta.get("domain"), "spec": spec}


def verification_evidence(records, root=ROOT):
    """{catalog_id: {"verified": bool, "source": "evidence", "detail": ...}}: verified means a passing record in
    reports/verification.jsonl whose implementation_hash equals house_sim.audit.fingerprint() and whose spec_hash
    matches the current spec."""
    from ..audit import fingerprint
    from ..tasks.verify import spec_hash
    path = root / "reports" / "verification.jsonl"
    evidence = {r.get("catalog_id"): r for r in read_jsonl(path)} if path.exists() else {}
    current = fingerprint(root)
    status = {}
    for record in records:
        proof = evidence.get(record["catalog_id"])
        if not proof:
            detail = "no evidence"
        elif not proof.get("passed"):
            detail = "evidence failing"
        elif proof.get("implementation_hash") != current:
            detail = "stale (implementation changed since verification)"
        elif proof.get("spec_hash") != spec_hash(record["spec"]):
            detail = "stale (spec changed since verification)"
        else:
            detail = "current passing evidence"
        status[record["catalog_id"]] = {"verified": detail == "current passing evidence", "source": "evidence", "detail": detail}
    return status


def verify_on_the_fly(records, seeds=None, perception=None, workers=None):
    """Verify the given specs now with house_sim.tasks.verify.verify_many on the dev seeds (by default) in each spec's
    own perception mode (perception=None, the definition used by the verification evidence and splits), or in a
    forced mode ("realistic": the mode harness episodes run in)."""
    from ..tasks.verify import SEEDS, verify_many
    seeds = tuple(seeds) if seeds else tuple(getattr(_splits(), "DEV_SEEDS", SEEDS) or SEEDS)
    results = verify_many([r["spec"] for r in records], seeds=seeds, workers=workers, perception=perception)
    status = {}
    for record, result in zip(records, results):
        failed = [t["seed"] for t in result.get("trials", []) if not t.get("passed")]
        status[record["catalog_id"]] = {"verified": bool(result.get("passed")), "source": "on_the_fly", "seeds": list(seeds),
                                        "perception": perception or "spec", "detail": "passed" if result.get("passed") else "failed on seeds {}".format(failed)}
    return status


def select(records, ids=None, sections=None, kinds=None, domains=None):
    out = []
    for r in records:
        if ids and r["catalog_id"] not in set(ids):
            continue
        if kinds and (r.get("kind") or "") not in set(kinds):
            continue
        if domains and (r.get("domain") or "") not in set(domains):
            continue
        if sections:
            section = (r.get("section") or "").lower()
            if not any(s.lower() == section or s.lower() in section for s in sections):
                continue
        out.append(r)
    return out


# ----- one episode ------------------------------------------------------------------------------------------------
def episode_perception(episode, perception=None):
    """The harness runs realistic perception (mandatory for test). perception="oracle" forces oracle for dev/train
    debugging; perception="episode" uses the episode's own field."""
    if episode.get("split") == "test":
        return "realistic"
    if perception == "oracle":
        return "oracle"
    if perception == "episode":
        return episode.get("perception") or "realistic"
    return "realistic"


def normalize_command(command):
    """-> (kwargs for env.step, parse status). Anything that is not a usable command becomes the unknown action
    UNPARSEABLE, which the environment refuses and counts like any other invalid step (and which replays)."""
    if not isinstance(command, dict):
        return {"action": UNPARSEABLE}, "not_a_dict"
    action = command.get("action")
    if not isinstance(action, str) or not action.strip():
        return {"action": UNPARSEABLE}, "unparseable" if command.get("_invalid") else "no_action"
    sent = {}
    for key, value in command.items():
        if not isinstance(key, str):
            return {"action": UNPARSEABLE}, "bad_key"
        if key.startswith("_"):
            continue                    # agent-side annotations (e.g. raw model text), never sent
        if key == "self":
            return {"action": UNPARSEABLE}, "reserved_key"
        if key in ("target", "destination") and not isinstance(value, str):
            value = "" if value is None else json.dumps(value, ensure_ascii=False)
        sent[key] = _jsonable(value)
    return sent, "ok"


_PROBLEM_CODE = re.compile(r"[a-z][a-z0-9_]{0,39}")


def loggable_command(command, private=False):
    """The agent's command as written to logs/. Public (dev/train) episodes keep it verbatim, including the
    '_'-prefixed agent-side annotations (e.g. an LLM's unusable reply in "_raw") that help debugging. Private (test)
    episodes keep only what reaches the simulator: annotations and any other text that is never sent to env.step can
    quote or paraphrase the held-out phrasing (a model restating its instruction), and results/ must stay
    publishable. Re-scoring is unchanged because it replays "sent", never "command"."""
    if not private:
        return _jsonable(command)
    if is_done(command):
        return {"action": "done"}
    sent, parse = normalize_command(command)
    if parse == "ok":
        return sent
    logged = {"action": None, "_redacted": True}
    problem = command.get("_invalid") if isinstance(command, dict) else None
    if isinstance(problem, str) and _PROBLEM_CODE.fullmatch(problem):
        logged["_invalid"] = problem    # a short problem code such as "no_command" or "refusal", never free text
    return logged


def log_name(catalog_id, tag):
    """File stem for logs/<catalog_id>__<seed_tag>.json with ':' (not allowed in Windows file names) written as '-'
    (splits.safe_tag)."""
    splits = _splits()
    tag = splits.safe_tag(tag) if splits is not None and hasattr(splits, "safe_tag") else str(tag).replace(":", "-")
    return re.sub(r"[^A-Za-z0-9._-]", "-", "{}__{}".format(catalog_id, tag))


def apply_phrasing(task, phrasing, bindings):
    """Replace the instruction with an episode's (held-out) phrasing, placeholders bound like runner.build."""
    if not phrasing:
        return task
    from ..tasks.spec import substitute
    task = dict(task)
    task["instruction"] = substitute(phrasing, bindings)
    return task


def guard_not_test(split, catalog_id, seed):
    """A non-test episode must never use a hidden test seed (private range or listed in the private manifest)."""
    if split == "test":
        return
    splits = _splits()
    if splits is not None and hasattr(splits, "guard_not_test"):
        splits.guard_not_test(catalog_id, seed)
    elif is_private_seed(seed):
        raise ValueError("test seed refused")


def _usage_delta(after, before):
    out = {}
    for key, value in (after or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            delta = value - (before or {}).get(key, 0)
            if delta:
                out[key] = round(delta, 6) if isinstance(delta, float) else delta
    return out


def _classify(failures):
    harms = [f for f in failures if f not in NON_HARM_LABELS and not f.startswith(NON_HARM_PREFIXES)]
    side = [f for f in failures if f.startswith(SIDE_EFFECT_PREFIXES)]
    return harms, side


def run_episode(spec, episode, agent, caps=None, out_dir=None, perception=None, ledger=None, run_id=None, private_dir=None,
                save_replay=True, camera=None):
    """Run one harness episode (episode from splits.episodes) and return its record (EVAL_CONTRACTS.md schema). With
    out_dir, writes logs/, replays/ (unless save_replay is false) and transcripts/ under it. camera: "room" or
    "geometric" forces the camera mode (--camera); None runs the spec's own (camera.default_mode otherwise)."""
    record, _extra = _run_episode(spec, episode, agent, caps, out_dir, perception, ledger, run_id, private_dir,
                                  save_replay=save_replay, camera=camera)
    return record


def _llm_cache_stats(agent, usage):
    """{"namespace", "source", "transport", "responses", "hits"} for agents with a response cache (LLM agents), else
    None: how many of the responses the agent used were served from the cache, and from which namespace."""
    if not hasattr(agent, "_cache_ids"):
        return None
    try:
        namespace, source = agent._cache_ids()
        transport = agent.info().get("transport")
    except Exception:  # noqa: BLE001
        namespace = source = transport = None
    return {"namespace": namespace, "source": source, "transport": transport, "enabled": getattr(agent, "cache", None) is not None,
            "responses": int((usage or {}).get("calls", 0) or 0), "hits": int((usage or {}).get("cache_hits", 0) or 0)}


def _run_episode(spec, episode, agent, caps=None, out_dir=None, perception=None, ledger=None, run_id=None, private_dir=None, name=None,
                 save_replay=True, code_versions=None, camera=None):
    from ..kernel.engine import HouseEnv, actions_fingerprint
    from ..tasks.runner import build
    from ..tasks.verify import spec_hash
    caps = dict(DEFAULT_CAPS, **(caps or {}))
    code_versions = dict(code_versions or versions())
    if camera not in (None,) + CAMERA_MODES:
        raise ValueError("camera must be room or geometric")
    rec = as_record(spec)
    raw_spec = rec["spec"]
    cid = episode.get("catalog_id") or rec["catalog_id"]
    split = episode.get("split", "dev")
    seed = episode["seed"]
    guard_not_test(split, cid, seed)    # raises before anything (logs included) can record a hidden seed as public
    private = split == "test" or is_private_seed(seed)
    tag = episode.get("seed_tag") or seed_tag(split, cid, seed)
    mode = episode_perception(episode, perception)
    name = name or log_name(cid, tag)
    wall0 = time.monotonic()
    extra = {"fatal": False, "error": None}
    record = {"catalog_id": cid, "split": split, "seed_tag": tag, "agent": getattr(agent, "name", type(agent).__name__),
              "privileged": bool(getattr(agent, "privileged", False)), "success": False, "goal_fraction": 0.0, "failures": [],
              "harm_flags": [], "side_effects": [], "blocks": {"safety": 0, "privacy": 0, "authority": 0}, "invalid_steps": 0,
              "questions": {"necessary": 0, "unnecessary": 0}, "steps": 0, "sim_time_s": 0.0, "wall_s": 0.0,
              "tokens": {"input": 0, "output": 0}, "cost_usd": 0.0, "done_declared": False, "end_reason": None, "log": None,
              "spec_hash": spec_hash(raw_spec), "perception": mode, "camera": camera, "kind": rec.get("kind"),
              "section": rec.get("section"),
              "domain": rec.get("domain"), "risk_level": raw_spec.get("risk_level"), "fidelity": raw_spec.get("fidelity"),
              "phases": None}
    log = {"format": "house-harness-log-v1", "harness_version": HARNESS_VERSION, "run_id": run_id, "catalog_id": cid, "split": split,
           "seed_tag": tag, "spec_hash": record["spec_hash"], "perception": mode, "camera": camera, "agent": record["agent"],
           "privileged": record["privileged"], "require_done": bool(caps["require_done"]), "caps": _jsonable(caps),
           "code_fingerprint": code_versions.get("code_fingerprint"), "harness_fingerprint": code_versions.get("harness_fingerprint"),
           "steps": []}
    split_key = _split_key() if private else None   # keys the private-text digests below (never recorded)

    def digest(purpose, text):
        return private_text_digest(purpose, cid, tag, text, key=split_key) if split_key is not None else None

    if not private and split in ("dev", "train"):
        log["seed"] = seed
    env = None
    steps = log["steps"]
    try:
        # the run's modes, as verify passes them: a spec's geometric camera setup ops apply only in a geometric run
        config, task, plans = build(raw_spec, seed, camera=camera, perception=mode)
        task = apply_phrasing(task, episode.get("phrasing"), plans["bindings"])
        del plans                       # privileged reference/wrong plans and bindings: never shown to the agent
        record["risk_level"], record["fidelity"] = task.get("risk_level"), task.get("fidelity")
        instruction = task.get("instruction", "")
        if private:
            # never an unkeyed hash: SHA-256 of the instruction is an offline oracle for the held-out phrasing
            log["instruction_hmac"] = digest("instruction", instruction)
            log["private_digest_scheme"] = PRIVATE_DIGEST_SCHEME
        else:
            log["instruction"] = instruction
        log["phrasing_override"] = bool(episode.get("phrasing"))
        env = HouseEnv(config, task, seed, perception=mode, camera=camera, trace=False, observe_on_step=True)
        record["camera"] = log["camera"] = env.camera_mode      # the mode it ran in (--camera, the spec's, the default)
        log["actions_hash"] = actions_fingerprint()
        catalog = action_catalog(env)
        observation = env.observe()
        budget = Budget(caps.get("max_tokens"), caps.get("max_cost_usd_episode"), ledger)
        agent.bind_budget(budget)
        max_steps = int(task.get("step_budget", 400))
        if caps.get("max_steps"):
            max_steps = min(max_steps, int(caps["max_steps"]))
        time_budget = float(task.get("time_budget_s", 7200))
        if caps.get("max_sim_s"):
            time_budget = min(time_budget, float(caps["max_sim_s"]))
        log["limits"] = {"max_steps": max_steps, "time_budget_s": time_budget}
    except Exception as exc:  # noqa: BLE001  (a spec that cannot be built is a harness problem, not an agent failure)
        record["end_reason"] = "harness_error"
        extra["error"] = log["error"] = "{}: {}".format(type(exc).__name__, exc)[:1000]
        record["wall_s"] = round(time.monotonic() - wall0, 3)
        extra["usage"] = {}
        _write_outputs(record, log, None, agent, out_dir, name, private, private_dir, run_id, save_replay)
        return record, extra

    end_reason, done, last = None, False, None
    consecutive_invalid = 0
    try:
        if record["privileged"] and hasattr(agent, "prepare"):
            if _accepts(agent.prepare, "camera"):
                agent.prepare(copy.deepcopy(raw_spec), dict(episode), perception=mode, camera=env.camera_mode)
            else:
                agent.prepare(copy.deepcopy(raw_spec), dict(episode), perception=mode)
        agent.reset(instruction, copy.deepcopy(observation), copy.deepcopy(catalog))
    except BudgetExceeded as exc:
        end_reason = exc.reason
    except Exception as exc:  # noqa: BLE001
        end_reason = "agent_error"
        extra["error"] = log["error"] = "{}: {}".format(type(exc).__name__, exc)[:1000]
        extra["fatal"] = _is_fatal(exc)
    n = 0
    while end_reason is None:
        # once the step budget is used up the agent gets one more turn, in which only "done" is accepted ("done" is
        # not an environment action and costs no step); any other command ends the episode at the step budget
        final = n >= max_steps
        if env.world.s.get("time_s", 0.0) > time_budget:
            end_reason = "time_budget"
            break
        if caps.get("max_wall_s") and time.monotonic() - wall0 > float(caps["max_wall_s"]):
            end_reason = "wall_clock"
            break
        reason = budget.exceeded()
        if reason:
            end_reason = reason
            break
        before = agent.usage() or {}
        t0 = time.monotonic()
        try:
            command = agent.act(copy.deepcopy(observation), copy.deepcopy(last))
        except BudgetExceeded as exc:
            end_reason = exc.reason
            log["cap_detail"] = exc.detail
            break
        except Exception as exc:  # noqa: BLE001
            end_reason = "agent_error"
            extra["error"] = log["error"] = "{}: {}".format(type(exc).__name__, exc)[:1000]
            extra["fatal"] = _is_fatal(exc)
            break
        t_agent = time.monotonic() - t0
        step_usage = _usage_delta(agent.usage(), before)
        if is_done(command) and _phase_active(env):
            # a multi-phase task: "done" ends the active phase; the episode goes on while another phase follows
            ended = env._phases.active()["id"]
            outcome = env.phase_done()
            following = (outcome.get("phase") or {}).get("active")
            steps.append({"i": n, "command": loggable_command(command, private), "done": True, "phase_done": True,
                          "phase": ended, "next_phase": following,
                          "waiting": bool((outcome.get("phase") or {}).get("waiting")),
                          "t_agent_s": round(t_agent, 4), "usage": step_usage,
                          "sim_time_s": env.world.s.get("time_s", 0.0)})
            if following is not None or (outcome.get("phase") or {}).get("waiting"):
                last = filter_result(outcome)
                observation = outcome.get("observation") or env.observe()
                if following is not None:
                    _notify_request(agent, observation)
                continue                # (waiting: a phase that starts on its own is still to come)
        if is_done(command):
            done = True
            end_reason = "done"
            if not (steps and steps[-1].get("phase_done") and steps[-1].get("next_phase") is None
                    and not _phase_active(env)):
                steps.append({"i": n + 1, "command": loggable_command(command, private), "done": True, "t_agent_s": round(t_agent, 4), "usage": step_usage})
            break
        if final:
            end_reason = "step_budget"
            steps.append({"i": n + 1, "command": loggable_command(command, private), "discarded": "step budget used up", "t_agent_s": round(t_agent, 4),
                          "usage": step_usage})
            break
        sent, parse = normalize_command(command)
        t1 = time.monotonic()
        phase_before = env._phases.active() if env._phases.phases else None
        try:
            result = env.step(**sent)
        except Exception as exc:  # noqa: BLE001  (simulator defect; run with HOUSE_SIM_SAFE=1 to turn these into refusals)
            end_reason = "sim_error"
            extra["error"] = log["error"] = "{}: {}".format(type(exc).__name__, exc)[:1000]
            steps.append({"i": n + 1, "command": loggable_command(command, private), "sent": sent, "parse": parse, "error": extra["error"]})
            break
        n += 1
        last = filter_result(result)
        observation = result.get("observation") or env.observe()
        steps.append({"i": n, "command": loggable_command(command, private), "sent": sent, "parse": parse, "result": _jsonable(last),
                      "t_agent_s": round(t_agent, 4), "t_env_s": round(time.monotonic() - t1, 4),
                      "sim_time_s": env.world.s.get("time_s", 0.0), "usage": step_usage})
        if env._phases.phases and env._phases.active() is not phase_before and env._phases.active() is not None:
            _notify_request(agent, observation)     # the environment started the next phase itself
        consecutive_invalid = 0 if result.get("ok") else consecutive_invalid + 1
        if caps.get("max_consecutive_invalid") and consecutive_invalid >= int(caps["max_consecutive_invalid"]):
            end_reason = "invalid_limit"
            break
        if result.get("truncated") and env.world.s.get("time_s", 0.0) > time_budget:
            end_reason = "time_budget"
            break

    evaluation = env.evaluate()
    usage = agent.usage() or {}
    metrics = evaluation.get("metrics", {})
    failures = list(evaluation.get("failures", []))
    harms, side = _classify(failures)
    record.update(success=bool(evaluation.get("success")) and (done or not caps["require_done"]),
                  goal_fraction=evaluation.get("goal_fraction", 0.0), failures=failures, harm_flags=harms, side_effects=side,
                  blocks={"safety": int(metrics.get("safety_blocks", 0)), "privacy": int(metrics.get("privacy_blocks", 0)),
                          "authority": int(metrics.get("authority_blocks", 0))},
                  invalid_steps=int(metrics.get("invalid", 0)),
                  questions={"necessary": int(metrics.get("necessary_questions", 0)), "unnecessary": int(metrics.get("unnecessary_questions", 0))},
                  steps=int(evaluation.get("steps", n)), sim_time_s=float(evaluation.get("time_s", 0.0)),
                  tokens={"input": int(usage.get("input", 0) or 0), "output": int(usage.get("output", 0) or 0)},
                  cost_usd=round(float(usage.get("cost_usd", 0.0) or 0.0), 6), done_declared=done, end_reason=end_reason,
                  phases=[{k: phase.get(k) for k in ("id", "success", "steps", "time_s", "end_reason")}
                          for phase in evaluation.get("phases") or []] or None)
    record["wall_s"] = round(time.monotonic() - wall0, 3)
    if split in ("dev", "train") and not private:
        record["seed"] = seed           # raw seeds only for public splits, never for test
    log.update(end_reason=end_reason, done_declared=done, wall_s=record["wall_s"], usage=_jsonable(usage),
               unparseable=sum(1 for s in steps if s.get("parse") not in (None, "ok")),
               unknown_actions=sum(1 for s in steps if s.get("parse") == "ok" and (s.get("result") or {}).get("category") == "argument"
                                   and str((s.get("result") or {}).get("message", "")).startswith("Unknown action")),
               evaluation={k: _jsonable(evaluation.get(k)) for k in ("success", "goal_fraction", "failures", "score", "steps", "time_s",
                                                                     "budget_exceeded", "rubric_score") if k in evaluation},
               success=record["success"], agent_info=_jsonable(_safe_info(agent, private, digest)))
    cache_stats = _llm_cache_stats(agent, usage)
    if cache_stats is not None:
        log["llm_cache"] = cache_stats
    extra["usage"] = dict(usage)
    _write_outputs(record, log, env, agent, out_dir, name, private, private_dir, run_id, save_replay)
    return record, extra


def _phase_active(env):
    phases = getattr(env, "_phases", None)
    return phases is not None and bool(phases.phases) and phases.active() is not None


def _notify_request(agent, observation):
    """The agent's optional hook on_request(request): the new phase's request as the robot perceives it."""
    hook = getattr(agent, "on_request", None)
    if not callable(hook):
        return
    obs = observation if isinstance(observation, dict) else {}
    request = obs.get("request") if isinstance(obs.get("request"), dict) else {"text": obs.get("instruction")}
    hook(copy.deepcopy(request))


def _accepts(fn, name):
    """True when a callable takes the keyword `name` (or **kwargs)."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


def _safe_info(agent, private=False, digest=None):
    try:
        info = dict(agent.info() or {})
    except Exception as exc:  # noqa: BLE001
        return {"info_error": str(exc)[:200]}
    if private and "memory_note" in info:
        # an LLM's free-text note may paraphrase the private test instruction: results/ keep only a KEYED digest
        # (private_text_digest; an unkeyed hash would let anyone confirm a guessed note offline), or nothing
        note = info.pop("memory_note")
        info["memory_note_hmac"] = digest("memory_note", note) if digest is not None and isinstance(note, str) and note else None
    return info


def _is_fatal(exc):
    try:
        from .llm import LLMFatalError
        return isinstance(exc, LLMFatalError)
    except Exception:  # noqa: BLE001
        return False


def _write_outputs(record, log, env, agent, out_dir, name, private, private_dir, run_id, save_replay=True):
    if out_dir is None:
        return
    out_dir = Path(out_dir)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    side_dir = out_dir
    if private:
        side_dir = Path(private_dir) if private_dir else default_private_dir(run_id or "adhoc")
    if env is not None and not save_replay:
        log["replay"] = None            # --no-replays: the log alone re-scores; the viewer rebuilds from the spec
        log["replay_private"] = bool(private)
    elif env is not None:
        replay_path = side_dir / "replays" / (name + ".replay.json")
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        env.save_replay(replay_path)
        log["replay"] = None if private else replay_path.relative_to(out_dir).as_posix()
        log["replay_private"] = bool(private)
    transcript = []
    try:
        transcript = agent.transcript() or []
    except Exception:  # noqa: BLE001
        transcript = []
    if transcript:
        tdir = side_dir / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        lines = []
        for entry in transcript:
            if entry.get("kind") == "asset":
                asset = tdir / re.sub(r"[^A-Za-z0-9._-]", "-", entry["name"])
                if not asset.exists():
                    asset.write_text(entry.get("text", ""), encoding="utf-8")
                lines.append(json.dumps({"kind": "asset_ref", "name": asset.name}, ensure_ascii=False))
            else:
                lines.append(json.dumps(entry, ensure_ascii=False, default=str))
        tpath = tdir / (name + ".jsonl")
        tpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log["transcript"] = None if private else tpath.relative_to(out_dir).as_posix()
        log["transcript_private"] = bool(private)
    path = out_dir / "logs" / (name + ".json")
    path.write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    record["log"] = path.relative_to(out_dir).as_posix()


def replay_log(log, spec, seed=None, phrasing=None, perception=None, camera=None):
    """Re-score a command log: rebuild the episode, re-issue every command the harness sent, and recompute the
    evaluation and success under the done protocol. Test-split logs carry no seed: pass it explicitly (recovered
    by the split-key holder from the seed tag). The camera mode is the log's (a log from before kernel v3.0 has
    none: the spec's own, as then). The result also says whether the log was produced by the current
    simulation code and harness code ("code_fingerprint_match", "harness_fingerprint_match"; None when the log does
    not record them): a re-score under different code is not evidence for the original run."""
    from ..kernel.engine import HouseEnv
    from ..tasks.runner import build
    if isinstance(log, (str, Path)):
        log = json.loads(Path(log).read_text(encoding="utf-8"))
    seed = log.get("seed") if seed is None else seed
    if seed is None:
        raise ValueError("this log records no seed (test split); pass the seed recovered with the split key")
    mode, camera = perception or log.get("perception") or "realistic", camera or log.get("camera")
    config, task, plans = build(as_record(spec)["spec"], seed, camera=camera, perception=mode)
    task = apply_phrasing(task, phrasing, plans["bindings"])
    env = HouseEnv(config, task, seed, perception=mode, camera=camera, trace=False, observe_on_step=True)
    env.observe()
    for step in log.get("steps", []):
        if step.get("phase_done"):
            env.phase_done()
        elif step.get("sent") is not None and "result" in step:
            env.step(**step["sent"])
    evaluation = env.evaluate()
    done = bool(log.get("done_declared"))
    current = versions()
    matches = {k + "_match": (log.get(k) == current[k]) if log.get(k) else None for k in ("code_fingerprint", "harness_fingerprint")}
    out = {"evaluation": evaluation, "success": bool(evaluation["success"]) and (done or not log.get("require_done", True)),
           "steps": evaluation["steps"]}
    out.update(matches)
    return out


# ----- a whole run ------------------------------------------------------------------------------------------------
def _default_run_id(agent_spec, split):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", canonical_name(agent_spec)).strip("-")[:48]
    return "{}-{}-{}".format(slug, split, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))


def _commitment_root(root=ROOT):
    splits = _splits()
    if splits is not None and hasattr(splits, "commitment_root"):
        try:
            return splits.commitment_root()
        except Exception:  # noqa: BLE001
            return None
    path = Path(getattr(splits, "COMMITMENT_FILE", None) or root / "reports" / "test_commitment.json")
    if not path.is_absolute():
        path = root / path
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("root")
    except (OSError, ValueError, AttributeError):
        return None


def _sanitize_options(options):
    out = {}
    for key, value in (options or {}).items():
        if re.search(r"(api[_-]?key|secret|passw|auth|bearer|credential|(^|_)token$|(^|_)key$)", key.lower()):
            continue                    # never record anything that looks like a credential
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, dict)):
            try:
                out[key] = _jsonable(value)
            except (TypeError, ValueError):
                out[key] = repr(value)[:200]
        else:
            out[key] = "<{}>".format(type(value).__name__)
    return out


def run(agent_spec, split, specs, out_dir, workers=1, caps=None, dry_run=False, **filters):
    """Run agent_spec on every selected episode of a split. specs: records or spec dicts (None = all authored).
    filters: ids, sections, kinds, domains, limit, seeds, seeds_per_entry, verify ("evidence" | "fly" | "none"),
    verify_seeds, perception, camera ("room" | "geometric": every episode in that camera mode; None: each spec's own),
    agent_options, allow_network, transport, cache_dir, use_cache, rpm, run_id,
    private_dir, save_replays (default True), emit (print-like or None). Returns the run summary (also written to run.json).
    For the test split the LLM response cache defaults to private_dir/cache, and a cache_dir or private_dir inside the
    repository, the results root or out_dir is refused (ValueError) before anything is written."""
    emit = filters.get("emit", print)
    say = (lambda *a: emit(*a)) if emit else (lambda *a: None)
    caps = dict(DEFAULT_CAPS, **(caps or {}))
    kind, provider, model, base_url = parse_agent_spec(agent_spec)
    verify = filters.get("verify", "evidence")
    if verify not in ("evidence", "fly", "none"):
        raise ValueError("verify must be evidence, fly or none")
    if filters.get("camera") not in (None,) + CAMERA_MODES:
        raise ValueError("camera must be room or geometric")
    price_check = None
    if kind in ("llm", "mock"):
        # checked before any network transport exists and before anything is written: an unpriced model would make
        # every cost cap a no-op (requests estimated at $0) and record $0 against an endpoint that may bill
        from .llm import agent_price, price_problem
        price = agent_price(kind, provider, model, base_url, filters.get("agent_options"))
        effective_url = None if kind == "mock" else (base_url or (filters.get("agent_options") or {}).get("base_url")
                                                     or (os.environ.get("HOUSE_AI_LLM_BASE_URL") if provider == "openai-compatible" else None))
        capped = caps.get("max_cost_usd") is not None or caps.get("max_cost_usd_episode") is not None
        problem = price_problem(price, provider if kind == "llm" else "mock", model, effective_url, capped)
        if problem and kind == "mock" and not capped:
            problem = None              # canned answers never bill; only a cap that could not trip matters
        if problem and kind == "llm" and provider == "openai-compatible" and not effective_url:
            problem = None              # no base URL at all: the provider's own configuration error is the clearer one
        price_check = {"price": price, "problem": problem}
        if problem and not dry_run:
            raise ValueError("refusing to run: " + problem)
    if kind == "llm" and not dry_run and filters.get("transport") is None:
        from .llm import NetworkDisabled, make_transport
        if not filters.get("allow_network"):
            raise NetworkDisabled("real LLM runs require --allow-network (and the provider's API key in the environment); "
                                  "use --dry-run to estimate cost without any calls")
        filters["transport"] = make_transport(provider, True, base_url or os.environ.get("HOUSE_AI_LLM_BASE_URL"))
    records = [as_record(s) for s in specs] if specs is not None else load_specs()
    conflicts = dict(LAST_CONFLICTS) if specs is None else {}
    if conflicts:
        from .common import conflict_note
        say("WARNING: " + conflict_note(conflicts))
    selected = select(records, filters.get("ids"), filters.get("sections"), filters.get("kinds"), filters.get("domains"))
    if not selected:
        raise ValueError("no specs match the filters")
    mode_for_verify = filters.get("verify_perception")      # None: each spec's own mode (as the evidence file)
    if mode_for_verify not in (None, "oracle", "realistic"):
        raise ValueError("verify_perception must be None, 'oracle' or 'realistic'")
    run_perception = filters.get("perception")
    if run_perception not in (None, "realistic", "oracle", "episode"):
        raise ValueError("perception must be realistic, oracle or episode")
    forced = {"oracle": "oracle", "episode": None}.get(run_perception, "realistic")
    verification = {}
    if verify == "evidence":
        verification = verification_evidence(selected)
        chosen = [r for r in selected if verification[r["catalog_id"]]["verified"]]
        if dry_run and len(chosen) < len(selected):
            say("WARNING: {} of {} selected specs lack current passing verification evidence; the real run needs "
                "--verify-on-the-fly (or --allow-unverified). Estimating all of them.".format(len(selected) - len(chosen), len(selected)))
            chosen = list(selected)
        if not chosen:
            raise ValueError("none of the {} selected specs has current passing verification evidence "
                             "(use --verify-on-the-fly, or --allow-unverified to run them unverified)".format(len(selected)))
    else:
        chosen = list(selected)
    by_id = {r["catalog_id"]: r for r in chosen}
    episodes, source = make_episodes(split, chosen, filters.get("seeds_per_entry"), filters.get("seeds"), perception=forced)
    if filters.get("limit"):
        episodes = episodes[:int(filters["limit"])]
    needed = [by_id[c] for c in dict.fromkeys(e["catalog_id"] for e in episodes) if c in by_id]
    if verify == "fly":
        # also in a dry run: verification is offline simulator work (no LLM calls, nothing written), and the estimate
        # must cover exactly the specs the real run would keep
        say("Verifying {} spec(s) on the fly ({} perception)...".format(len(needed), mode_for_verify or "each spec's own"))
        verification = verify_on_the_fly(needed, filters.get("verify_seeds"), mode_for_verify, filters.get("verify_workers"))
        failed = sorted(c for c, v in verification.items() if not v.get("verified"))
        if failed:
            say("  {} spec(s) failed verification and are dropped: {}".format(len(failed), ", ".join(failed[:20])))
        episodes = [e for e in episodes if verification.get(e["catalog_id"], {}).get("verified")]
    elif verify == "none":
        verification = {r["catalog_id"]: {"verified": False, "source": "none", "detail": "run without verification (--allow-unverified)"} for r in needed}
    if dry_run:
        return dry_run_report(agent_spec, split, episodes, by_id, caps, filters, verification, verify, emit, price_check)
    if not episodes:
        raise ValueError("no episodes to run (every selected spec failed verification?)")

    run_id = filters.get("run_id") or _default_run_id(agent_spec, split)
    out_dir = Path(out_dir) if out_dir else _results_root() / run_id
    private_dir = Path(filters["private_dir"]) if filters.get("private_dir") else default_private_dir(run_id)
    options = dict(filters.get("agent_options") or {})
    if split == "test":
        # checked before anything is written: these directories receive the held-out phrasing and the raw seeds
        _refuse_public_location(private_dir, out_dir, "the private results directory (private_dir)")
        if kind in ("llm", "mock"):
            for requested in (filters.get("cache_dir"), options.get("cache_dir")):
                if requested:
                    _refuse_public_location(requested, out_dir, "the test-split LLM response cache (--cache-dir / cache_dir)")
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = CostLedger(caps.get("max_cost_usd"))
    shared = {}
    llm_transport, cache_info = None, None
    if kind in ("llm", "mock"):
        from .llm import MockTransport, RateLimiter, ResponseCache, cache_source, load_prices, transport_kind, PRICES_PATH
        transport = filters.get("transport")
        if transport is not None:
            shared["transport"] = transport
        answering = transport if transport is not None else options.get("transport")
        if answering is None and kind == "mock":
            answering = MockTransport([])           # what MockLLMAgent builds by default (for the labels only)
        llm_transport = transport_kind(answering) if answering is not None else None
        # The default caches (results/cache; for the test split the run's private cache) hold real network answers
        # only: a mock, fake or other injected transport caches only into a cache_dir given explicitly, and its
        # entries live in their own namespace (ResponseCache key and meta), so they can never answer a real run.
        network = llm_transport == "network"
        requested_cache = filters.get("cache_dir")
        if split == "test":
            # every cached response holds the model's text (and its memory note) for a private episode, which may quote
            # or paraphrase the held-out phrasing: keep it with the other private artifacts, never under results/
            requested_cache = requested_cache or options.get("cache_dir")
            options.pop("cache_dir", None)  # the agent must not open a second cache from its own options
            options.pop("cache", None)      # (nor use a cache object whose location was never checked)
            if not filters.get("use_cache", True) or options.get("use_cache") is False:
                options["use_cache"] = False
            elif network or requested_cache:
                shared["cache"] = ResponseCache(Path(requested_cache) if requested_cache else private_dir / "cache")
            else:
                options["use_cache"] = False
        elif filters.get("use_cache", True) and options.get("use_cache") is not False and (network or requested_cache):
            shared["cache"] = ResponseCache(requested_cache or _results_root() / "cache")
        elif not filters.get("use_cache", True) or (not requested_cache and not options.get("cache_dir") and not options.get("cache")):
            options["use_cache"] = False    # --no-cache wins; and never any default cache for a non-network transport
        cache_on = shared.get("cache") is not None or (options.get("use_cache") is not False and bool(options.get("cache_dir") or options.get("cache")))
        cache_info = {"enabled": bool(cache_on), "source": cache_source(answering) if answering is not None else None,
                      "namespace": "mock" if kind == "mock" else provider,
                      "explicit": bool(requested_cache or options.get("cache_dir") or options.get("cache")),
                      "private": shared.get("cache") is not None and split == "test"}
        shared["rate_limiter"] = RateLimiter(filters.get("rpm") or options.get("rpm"))
        if caps.get("max_steps"):
            options.setdefault("max_steps", caps["max_steps"])
        prices_path = Path(options.get("prices_path") or PRICES_PATH)
        prices_doc = load_prices(prices_path)
        price = (price_check or {}).get("price") or {}
        price_info = {"file": str(prices_path), "sha256": hashlib.sha256(prices_path.read_bytes()).hexdigest(), "as_of": prices_doc.get("as_of"),
                      "source": price.get("source"), "unknown": bool(price.get("unknown")), "unpriced": bool(price.get("unpriced")),
                      "declared_free": bool(price.get("declared_free")),
                      "per_million": {k: price.get(k) for k in ("input", "output", "cache_write", "cache_read")}}
    else:
        price_info = None
    from ..kernel import engine
    from ..tasks.verify import spec_hash
    names, used, plan = [], {}, []
    for e in episodes:
        tag = e.get("seed_tag") or seed_tag(split, e["catalog_id"], e["seed"])
        plan.append([e["catalog_id"], tag])
        base = log_name(e["catalog_id"], tag)
        used[base] = used.get(base, 0) + 1
        names.append(base if used[base] == 1 else "{}__{}".format(base, used[base]))
    code_versions = versions()
    perception_modes = sorted({episode_perception(e, filters.get("perception")) for e in episodes})
    run_info = {"format": "house-harness-run-v1", "harness_version": HARNESS_VERSION, "run_id": run_id, "status": "running",
                "agent": canonical_name(agent_spec), "agent_spec": redact_url_credentials(agent_spec), "privileged": kind == "oracle", "split": split,
                "filters": _jsonable({k: filters.get(k) for k in ("ids", "sections", "kinds", "domains", "limit", "seeds_per_entry")}),
                "seeds": list(filters.get("seeds") or []) if split != "test" else None,
                "caps": _jsonable(caps), "workers": int(workers or 1), "perception": "realistic" if split == "test" else (filters.get("perception") or "realistic"),
                # the modes episodes actually run in: anything but ["realistic"] is a privileged-perception run
                "perception_modes": perception_modes, "privileged_perception": perception_modes != ["realistic"],
                # the forced camera mode (--camera), None = each spec's own; camera_modes: what episodes ran in
                "camera": filters.get("camera"), "camera_modes": None,
                "code_fingerprint": code_versions["code_fingerprint"], "harness_fingerprint": code_versions["harness_fingerprint"],
                "actions_hash": engine.actions_fingerprint(),
                "spec_hashes": {cid: spec_hash(by_id[cid]["spec"]) for cid in dict.fromkeys(e["catalog_id"] for e in episodes)},
                "commitment_root": _commitment_root(), "episode_source": source, "authored_conflicts": conflicts,
                "replays_saved": bool(filters.get("save_replays", True)),
                "verification": {"mode": {"evidence": "evidence", "fly": "on_the_fly", "none": "unverified"}[verify],
                                 "perception": (mode_for_verify or "spec") if verify == "fly" else None,
                                 "seeds": list(filters.get("verify_seeds") or []) if verify == "fly" else None,
                                 "per_spec": _jsonable(verification),
                                 "excluded": sorted(cid for cid, v in verification.items() if not v.get("verified")) if verify != "none" else []},
                "agent_options": _sanitize_options(options), "allow_network": bool(filters.get("allow_network")),
                # test runs record only that the cache is private (its path is on the evaluator's machine)
                "llm_cache_dir": str(shared["cache"].dir) if shared.get("cache") is not None and split != "test" else None,
                "llm_cache_private": shared.get("cache") is not None and split == "test", "prices": price_info,
                # where the agent's answers come from ("network" = real HTTP; "mock" / "fake" / "custom:..." = canned or
                # injected) and the response-cache provenance; totals add llm_responses / llm_cache_hits
                "llm_transport": llm_transport, "llm_cache": cache_info,
                "sim_safe_mode": bool(getattr(engine, "SAFE", False)), "python": sys.version.split()[0],
                "started": _now(), "ended": None, "episodes_planned": len(episodes),
                # the exact episode set this run was asked to execute (public seed tags only), so deleted, duplicated or
                # added episodes are detectable against episodes.jsonl and the logs
                "planned_episodes": plan,
                "planned_episodes_sha256": hashlib.sha256(json.dumps(plan, separators=(",", ":")).encode("utf-8")).hexdigest()}
    (out_dir / "run.json").write_text(json.dumps(run_info, indent=1, ensure_ascii=False), encoding="utf-8")
    from ..kernel.plugins import load_domains
    load_domains()                      # import every domain once before any worker thread starts

    results = [None] * len(episodes)
    usages = [None] * len(episodes)
    lock = threading.Lock()
    state = {"aborted": None, "done": 0}
    stream = (out_dir / "episodes.jsonl").open("w", encoding="utf-8", newline="\n")

    def job(index):
        episode = episodes[index]
        with lock:
            if state["aborted"]:
                return None
        if ledger.exhausted():
            return None
        try:
            agent = make_agent(agent_spec, **dict(options, **shared))
            record, extra = _run_episode(by_id[episode["catalog_id"]], episode, agent, caps, out_dir, filters.get("perception"),
                                         ledger, run_id, private_dir, names[index], save_replay=filters.get("save_replays", True),
                                         code_versions=code_versions, camera=filters.get("camera"))
        except Exception as exc:  # noqa: BLE001  (harness-level failure, e.g. a refused test seed): stop the run cleanly
            with lock:
                if not state["aborted"]:
                    state["aborted"] = "harness error on {} {}: {}: {}".format(episode["catalog_id"], episode.get("seed_tag"),
                                                                            type(exc).__name__, str(exc)[:300])
            return None
        with lock:
            results[index] = record
            usages[index] = extra.get("usage") or {}
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")
            stream.flush()
            state["done"] += 1
            if extra.get("fatal") and not state["aborted"]:
                state["aborted"] = "fatal provider error: " + str(extra.get("error"))[:300]
            say("[{}/{}] {} {} success={} steps={} end={} cost=${:.4f}".format(state["done"], len(episodes), record["catalog_id"],
                                                                             record["seed_tag"], record["success"], record["steps"],
                                                                             record["end_reason"], record["cost_usd"]))
        return record

    try:
        if int(workers or 1) <= 1:
            for i in range(len(episodes)):
                job(i)
        else:
            with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 64))) as pool:
                list(pool.map(job, range(len(episodes))))
    finally:
        stream.close()
    done = [r for r in results if r is not None]
    with (out_dir / "episodes.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for r in done:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    skipped = len(episodes) - len(done)
    totals = {"episodes": len(done), "successes": sum(1 for r in done if r["success"]),
              "success_rate": round(sum(1 for r in done if r["success"]) / len(done), 4) if done else 0.0,
              "tokens": {"input": sum(r["tokens"]["input"] for r in done), "output": sum(r["tokens"]["output"] for r in done)},
              "cost_usd": round(sum(r["cost_usd"] for r in done), 6), "invalid_steps": sum(r["invalid_steps"] for r in done),
              "end_reasons": {}}
    for r in done:
        totals["end_reasons"][r["end_reason"]] = totals["end_reasons"].get(r["end_reason"], 0) + 1
    if kind in ("llm", "mock"):
        responses = sum(int((u or {}).get("calls", 0) or 0) for u in usages)
        hits = sum(int((u or {}).get("cache_hits", 0) or 0) for u in usages)
        totals.update(llm_responses=responses, llm_cache_hits=hits,
                      llm_cache_hit_rate=round(hits / responses, 4) if responses else 0.0)
    run_info["camera_modes"] = sorted({r.get("camera") for r in done if r.get("camera")})
    run_info.update(status="aborted" if state["aborted"] else ("incomplete" if skipped else "complete"), ended=_now(),
                    aborted=state["aborted"], episodes_run=len(done), episodes_skipped=skipped,
                    skipped_reason=("run cost cap reached" if ledger.exhausted() else state["aborted"]) if skipped else None,
                    ledger=ledger.snapshot(), totals=totals)
    (out_dir / "run.json").write_text(json.dumps(run_info, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {"run_id": run_id, "out_dir": str(out_dir), "status": run_info["status"], "aborted": state["aborted"]}
    summary.update(totals)
    return summary


def dry_run_report(agent_spec, split, episodes, by_id, caps, filters, verification, verify, emit=print, price_check=None):
    """Print the episode list with estimated token usage and cost; makes no LLM calls and writes nothing. A model
    without a usable price is flagged prominently (its estimates are $0 and the real run would be refused when a
    cost cap is set or the endpoint is not loopback; price_check from run())."""
    from ..kernel.engine import HouseEnv
    from ..tasks.runner import build
    say = (lambda *a: emit(*a)) if emit else (lambda *a: None)
    kind, provider, model, base_url = parse_agent_spec(agent_spec)
    llm = kind in ("llm", "mock")
    options = dict(filters.get("agent_options") or {})
    expected_out = int(options.pop("expected_output_tokens", 400))
    probe = None
    if llm:
        from .llm import DisabledTransport, LLMAgent, MockLLMAgent, estimate_call, load_prices
        from .agents import _accepted
        opts = {k: v for k, v in options.items() if k not in ("transport", "allow_network", "cache", "cache_dir")}
        opts["use_cache"] = False
        if kind == "mock":
            opts = _accepted([MockLLMAgent, LLMAgent], opts)
            probe = MockLLMAgent(model=options.get("model") or model,
                                 **{k: v for k, v in opts.items() if k not in ("provider", "model", "base_url")})
        else:
            opts = _accepted(LLMAgent, opts)
            probe = LLMAgent(provider=provider, model=model, base_url=base_url or options.get("base_url"), transport=DisabledTransport(),
                             **{k: v for k, v in opts.items() if k not in ("provider", "model", "base_url", "responses")})
        price_doc = load_prices(options.get("prices_path"))
    per_spec, rows = {}, []
    totals = {"input": 0, "output": 0, "expected_usd": 0.0, "worst_usd": 0.0, "steps": 0}
    for e in episodes:
        cid = e["catalog_id"]
        if cid not in per_spec:
            mode = episode_perception(e, filters.get("perception"))
            config, task, plans = build(by_id[cid]["spec"], e["seed"], camera=filters.get("camera"), perception=mode)
            task = apply_phrasing(task, e.get("phrasing"), plans["bindings"])
            info = {"step_budget": int(task.get("step_budget", 400)), "obs_chars": 0, "est": None}
            if llm:
                env = HouseEnv(config, task, e["seed"], perception=mode, camera=filters.get("camera"), trace=False,
                               observe_on_step=True)
                observation = env.observe()
                probe.reset(task.get("instruction", ""), observation, action_catalog(env))
                probe.history = [{"step": i, "command": {"action": "inspect", "target": "obj_00000000"},
                                  "result": {"ok": True, "message": "x" * 200, "category": ""}} for i in range(probe.history_n)]
                turn = probe.render_turn(observation)
                info["est"] = estimate_call(probe.system_text, turn, probe.provider.name, probe.model, probe.provider.max_output_tokens,
                                            expected_out, price_doc, "mock" if kind == "mock" else None, price=probe.price)
            per_spec[cid] = info
        info = per_spec[cid]
        steps = info["step_budget"] if not caps.get("max_steps") else min(info["step_budget"], int(caps["max_steps"]))
        row = {"catalog_id": cid, "seed_tag": e.get("seed_tag"), "steps": steps, "input_tokens": 0, "output_tokens": 0,
               "expected_usd": 0.0, "worst_usd": 0.0}
        if info["est"]:
            est = info["est"]
            row["input_tokens"] = steps * (est["system_tokens"] + est["turn_tokens"])
            row["output_tokens"] = steps * expected_out
            row["expected_usd"] = round(est["expected_cost_first"] + max(0, steps - 1) * est["expected_cost_cached"], 4)
            row["worst_usd"] = round(steps * est["worst_cost"], 4)
            if caps.get("max_cost_usd_episode"):
                row["worst_usd"] = min(row["worst_usd"], float(caps["max_cost_usd_episode"]))
        rows.append(row)
        totals["input"] += row["input_tokens"]
        totals["output"] += row["output_tokens"]
        totals["expected_usd"] += row["expected_usd"]
        totals["worst_usd"] += row["worst_usd"]
        totals["steps"] += steps
    totals["uncapped_expected_usd"], totals["uncapped_worst_usd"] = totals["expected_usd"], totals["worst_usd"]
    if caps.get("max_cost_usd") is not None:      # the run stops at the per-run cap
        totals["worst_usd"] = min(totals["worst_usd"], float(caps["max_cost_usd"]))
        totals["expected_usd"] = min(totals["expected_usd"], float(caps["max_cost_usd"]))
    say("DRY RUN: {} on split {} - {} episode(s); no LLM calls are made.".format(canonical_name(agent_spec), split, len(rows)))
    if verify == "fly":
        say("  (only the {} spec(s) that passed on-the-fly verification just now are estimated)".format(len(per_spec)))
    elif verify == "none":
        say("  (specs are run UNVERIFIED)")
    say("  {:<24} {:<20} {:>6} {:>12} {:>10} {:>11} {:>10}".format("catalog_id", "seed_tag", "steps", "in_tokens", "out_tokens", "expected$", "worst$"))
    for row in rows:
        say("  {:<24} {:<20} {:>6} {:>12} {:>10} {:>11.4f} {:>10.4f}".format(row["catalog_id"], str(row["seed_tag"]), row["steps"], row["input_tokens"],
                                                                         row["output_tokens"], row["expected_usd"], row["worst_usd"]))
    say("  TOTAL: up to {} steps, ~{} input tokens (mostly cached system prompt), ~{} output tokens; expected ~${:.2f}, "
        "worst case ${:.2f}{}".format(totals["steps"], totals["input"], totals["output"], totals["expected_usd"], totals["worst_usd"],
                                      " (the run stops at --max-cost-usd; uncapped: expected ~${:.2f}, worst ${:.2f})".format(
                                          totals["uncapped_expected_usd"], totals["uncapped_worst_usd"]) if caps.get("max_cost_usd") is not None else ""))
    warnings = []
    price = probe.price if probe is not None else None
    if llm:
        say("  Estimates assume every step budget is used, ~{} output tokens per step and a warm prompt cache; prices from "
            "prices.json (as of {}) - VERIFY CURRENT PRICES BEFORE USE.".format(expected_out, price_doc.get("as_of")))
        problem = (price_check or {}).get("problem")
        if price and price.get("unpriced"):
            warnings.append("NO PRICE for model {!r}: every cost above is $0 because prices.json has no price for it, "
                            "not because it is free.".format(probe.model))
        elif price and price.get("source") == "default":
            warnings.append("model {!r} is not listed in prices.json; it is priced at the provider's conservative default "
                            "tier.".format(probe.model))
        elif price and price.get("declared_free"):
            warnings.append("model {!r} is declared free (price_free=true): cost caps cannot trip and every record will "
                            "claim $0.".format(probe.model))
        if problem:
            warnings.append("THE REAL RUN WOULD BE REFUSED: " + problem)
        for text in warnings:
            say("  " + "!" * 8 + " WARNING: " + text)
    for row in rows:
        row["expected_usd"] = round(row["expected_usd"], 6)
    return {"dry_run": True, "agent": canonical_name(agent_spec), "split": split, "episodes": rows,
            "totals": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in totals.items()},
            "verification": _jsonable(verification),
            "price": _jsonable({k: v for k, v in price.items() if k != "long_context"}) if price else None,
            "warnings": warnings}


# ----- CLI --------------------------------------------------------------------------------------------------------
def _parse_opt(text):
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError("--agent-opt expects KEY=VALUE")
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value
    return key.strip(), parsed


def build_parser():
    p = argparse.ArgumentParser(prog="python -m house_sim --run-agent", description="Run an agent on task episodes (docs/EVAL_HARNESS.md).")
    p.add_argument("--run-agent", dest="agent", required=True, metavar="AGENT_SPEC",
                   help="random | search | oracle | mock | llm:anthropic:<model> | llm:openai-compatible:<model>@<base_url>")
    p.add_argument("--split", choices=["dev", "train", "test"], default="dev")
    p.add_argument("--ids", nargs="+")
    p.add_argument("--sections", nargs="+", help="Section names (exact or case-insensitive substring).")
    p.add_argument("--kinds", nargs="+")
    p.add_argument("--domains", nargs="+")
    p.add_argument("--limit", type=int, help="Run at most N episodes.")
    p.add_argument("--seeds", nargs="+", type=int, help="dev/train only.")
    p.add_argument("--seeds-per-entry", type=int)
    p.add_argument("--workers", type=int, default=1, help="Parallel episodes (threads).")
    p.add_argument("--out", help="Results directory (default results/<run_id>/).")
    p.add_argument("--run-id")
    p.add_argument("--dry-run", action="store_true", help="Print the episode list with estimated tokens and cost; no calls.")
    p.add_argument("--max-steps", type=int, help="Per-episode step cap (never above the task's own budget).")
    p.add_argument("--max-sim-s", type=float, help="Per-episode simulated-time cap.")
    p.add_argument("--max-cost-usd", type=float, help="Per-run estimated cost cap.")
    p.add_argument("--max-episode-cost-usd", type=float, help="Per-episode estimated cost cap.")
    p.add_argument("--max-tokens", type=int, help="Per-episode token cap (input + output).")
    p.add_argument("--max-wall-s", type=float, default=DEFAULT_CAPS["max_wall_s"], help="Per-episode wall-clock cap.")
    p.add_argument("--max-consecutive-invalid", type=int)
    p.add_argument("--no-require-done", action="store_true", help="Score the final state even without a declared done.")
    p.add_argument("--allow-network", action="store_true", help="Required for real LLM runs (keys only from the environment).")
    p.add_argument("--verify-on-the-fly", action="store_true", help="Verify the selected specs now (dev seeds, run perception).")
    p.add_argument("--verify-seeds", nargs="+", type=int, help="Seeds for --verify-on-the-fly (default: the dev seeds).")
    p.add_argument("--verify-workers", type=int, help="Processes for --verify-on-the-fly (default: CPUs - 1).")
    p.add_argument("--verify-perception", choices=["realistic", "oracle"],
                   help="Force a perception mode for --verify-on-the-fly (default: each spec's own, as the evidence file).")
    p.add_argument("--allow-unverified", action="store_true", help="Run specs without verification (labelled unverified).")
    p.add_argument("--perception", choices=["realistic", "oracle", "episode"], help="Default realistic (test is always realistic).")
    p.add_argument("--camera", choices=list(CAMERA_MODES),
                   help="Camera mode for every episode (default: each spec's own; kernel v3.0 geometric camera).")
    p.add_argument("--agent-opt", action="append", type=_parse_opt, default=[], metavar="KEY=VALUE",
                   help="Agent option, e.g. effort=low, history=12, temperature=0.2, rpm=40, seed=7, prices_path=FILE, "
                        "price_free=true (declare an unlisted model free) (JSON values).")
    p.add_argument("--cache-dir", help="LLM response cache (default results/cache for real network runs only; for --split "
                   "test the run's private directory %%USERPROFILE%%/.house_ai/private_results/<run_id>/cache, and a "
                   "directory inside the repository is refused). Mock runs cache only with --cache-dir, in their own "
                   "namespace, so their answers never serve a real run.")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--rpm", type=float, help="LLM requests per minute across workers.")
    p.add_argument("--no-replays", action="store_true",
                   help="Do not save an env replay per episode (each is a compact house-replay-v3 file of about "
                        "60-95 KB; logs alone re-score, the viewer rebuilds from the spec).")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv):
    os.environ.setdefault("HOUSE_SIM_SAFE", "1")    # simulator defects become refused steps (if the engine is not yet imported)
    args = build_parser().parse_args(list(argv))
    if args.verify_on_the_fly and args.allow_unverified:
        print("error: --verify-on-the-fly and --allow-unverified are exclusive", file=sys.stderr)
        return 2
    caps = {"max_steps": args.max_steps, "max_sim_s": args.max_sim_s, "max_wall_s": args.max_wall_s, "max_tokens": args.max_tokens,
            "max_cost_usd_episode": args.max_episode_cost_usd, "max_cost_usd": args.max_cost_usd,
            "require_done": not args.no_require_done, "max_consecutive_invalid": args.max_consecutive_invalid}
    try:
        summary = run(args.agent, args.split, None, args.out, workers=args.workers, caps=caps, dry_run=args.dry_run,
                      ids=args.ids, sections=args.sections, kinds=args.kinds, domains=args.domains, limit=args.limit,
                      seeds=args.seeds, seeds_per_entry=args.seeds_per_entry,
                      verify="fly" if args.verify_on_the_fly else ("none" if args.allow_unverified else "evidence"),
                      verify_seeds=args.verify_seeds, verify_perception=args.verify_perception, verify_workers=args.verify_workers,
                      perception=args.perception, camera=args.camera, agent_options=dict(args.agent_opt),
                      allow_network=args.allow_network, cache_dir=args.cache_dir, use_cache=not args.no_cache, rpm=args.rpm,
                      run_id=args.run_id, save_replays=not args.no_replays, emit=None if args.quiet else print)
    except Exception as exc:  # noqa: BLE001  (configuration problems: print, never a traceback with secrets)
        from .llm import LLMError
        if not isinstance(exc, (ValueError, RuntimeError, KeyError, OSError, LLMError)):
            raise
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if args.dry_run:
        return 0
    print(json.dumps(summary, indent=1))
    return 1 if summary.get("aborted") else 0

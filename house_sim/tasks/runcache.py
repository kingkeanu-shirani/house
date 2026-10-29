"""The local run cache of --check (docs/design/VERIFY_HARDENING.md 22.8, 21.1; WP5).

    key(kind, payload)          the cache key: SHA-256 of the canonical JSON of what determines the runs
    get(key, root=None)         the stored result, or None (a hit refreshes its time for the LRU prune)
    put(key, value, root=None)  store a result (JSON); prunes the folder to MAX_BYTES least recently used
    sim_spec(spec)              the spec without the keys no run reads (they are re-judged on a hit)
    enabled()                   False when HOUSE_SIM_RUN_CACHE=0 (and the verifier's cache=False / --no-cache)
    environment()               the loaded domains, registries and outcome switches the key also holds

What is cached: one verification job (the core trials of one mode over a seed list: builds, idle rollouts,
references, wrong plans and their attribution runs) keyed by the spec as the runs read it, the seeds, the mode, the
forced generation, the measured allowances, the job's flags, the look-ahead, the code fingerprint
(house_sim/audit.py fingerprint: every simulation and evaluator file and data) and the environment (the domain
modules loaded -- HOUSE_SIM_DOMAINS --, the registry sizes, HOUSE_SIM_STRICT / SAFE / IDLE_PROBES, the Python
version: what the fingerprint of file contents does not see). A hit is reused only when all of these
are unchanged, so a check that reuses runs is a complete check. The spec keys no run reads -- each wrong plan's expect
and expect_budget, protect_map, instruction_roles, fungible, informative, free_ok, battery_exempt, mustpass_exempt,
budget_reason, notes, approval, clauses, tags -- are left out of the key: an edit to them re-judges the stored runs
(verify.rejudge: expect matching, attribution, protect liveness, escapes) instead of re-running them (22.8: the
authoring loop's edits to expect, the protect map and the escapes cost about nothing). The cache lives in
reports/cache/runs/ (gitignored), pruned to 2 GB; the gate never reads an author's cache (it runs with cache off).
"""
import copy
import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "reports" / "cache" / "runs"
DIR_ENV = "HOUSE_SIM_RUN_CACHE_DIR"
SWITCH_ENV = "HOUSE_SIM_RUN_CACHE"
MAX_BYTES = 2 * 1024 ** 3
FORMAT = "house_ai.run_cache/1"
JUDGED_SPEC_KEYS = ("protect_map", "instruction_roles", "fungible", "informative", "free_ok", "battery_exempt",
                    "mustpass_exempt", "budget_reason", "notes", "approval", "clauses", "tags")
JUDGED_WRONG_KEYS = ("expect", "expect_budget")
OUTCOME_ENV = ("HOUSE_SIM_DOMAINS", "HOUSE_SIM_STRICT", "HOUSE_SIM_SAFE", "HOUSE_SIM_IDLE_PROBES")


def enabled():
    return os.environ.get(SWITCH_ENV, "1").strip() not in ("0", "false", "off", "no")


def environment():
    """What outside the code fingerprint decides a run's outcome, for the key (21.1 / 22.8: a cached run is reused
    only when everything that determines its simulation is unchanged): the domain modules loaded
    (plugins.domain_modules(), restricted by HOUSE_SIM_DOMAINS: a check made with a subset of the domains runs without
    most systems), the registered systems, actions and skills (what this process actually loaded), the switches that
    change what a run does (HOUSE_SIM_STRICT raises where a lenient run goes on, HOUSE_SIM_SAFE turns defects into
    refusals, HOUSE_SIM_IDLE_PROBES), and the Python version."""
    import sys
    from ..kernel import plugins
    switches = {name: os.environ.get(name) for name in OUTCOME_ENV}
    return {"domains": list(plugins.domain_modules()), "systems": len(plugins.SYSTEMS),
            "actions": len(plugins.ACTIONS), "skills": len(plugins.SKILLS), "switches": switches,
            "python": list(sys.version_info[:2])}


def cache_dir(root=None):
    if root is not None:
        return Path(root)
    raw = os.environ.get(DIR_ENV)
    return Path(raw) if raw and raw.strip() else DEFAULT_DIR


def _strip(scope):
    out = {k: copy.deepcopy(v) for k, v in scope.items() if k not in JUDGED_SPEC_KEYS}
    for key in ("wrong", "+wrong"):
        if isinstance(out.get(key), list):
            out[key] = [{k: v for k, v in item.items() if k not in JUDGED_WRONG_KEYS} if isinstance(item, dict)
                        else item for item in out[key]]
    return out


def sim_spec(spec):
    """The spec as the runs read it: the judged-only keys removed from it and from each variant."""
    out = _strip(spec)
    if isinstance(out.get("variants"), list):
        out["variants"] = [_strip(v) if isinstance(v, dict) else v for v in out["variants"]]
    return out


def key(kind, payload):
    text = json.dumps({"format": FORMAT, "kind": kind, "payload": payload}, sort_keys=True, default=str,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(k, root=None):
    return cache_dir(root) / k[:2] / (k + ".json")


def get(k, root=None):
    path = _path(k, root)
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("key") != k:
        return None
    try:
        os.utime(path, None)
    except OSError:
        pass
    return data.get("value")


def put(k, value, root=None, max_bytes=MAX_BYTES):
    """Store a JSON-serialisable result; returns the path (None when it could not be written: a read-only checkout
    simply has no cache)."""
    path = _path(k, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp{}".format(os.getpid()))
        with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
            json.dump({"format": FORMAT, "key": k, "stored": time.time(), "value": value}, stream, default=str)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        return None
    prune(root, max_bytes)
    return path


def prune(root=None, max_bytes=MAX_BYTES):
    """Delete the least recently used entries until the folder holds at most max_bytes. Returns the bytes freed."""
    folder = cache_dir(root)
    if not folder.is_dir():
        return 0
    files = []
    total = 0
    for path in folder.glob("*/*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size
    freed = 0
    for _, size, path in sorted(files):
        if total - freed <= max_bytes:
            break
        try:
            path.unlink()
            freed += size
        except OSError:
            pass
    return freed

"""Splits and the hidden test set (docs/SPLITS.md; contract: docs/EVAL_CONTRACTS.md section A).

* train: unlimited public seeds in [TRAIN_BASE, PRIVATE_BASE) (a per-entry offset, consecutive from there);
* dev:   DEV_SEEDS (the public verification seeds) plus the small published DEV_EXTRA;
* test:  hidden. Candidate seeds are HMAC-SHA256(secret key, "<catalog_id>:<i>") mapped into
         [PRIVATE_BASE, PRIVATE_END). `build_test_manifest` runs the verifier on each candidate, skips failing ones
         deterministically, makes every variant appear, assigns held-out phrasings from the private paraphrase pool
         and returns the private manifest. `save_manifest` writes it to %USERPROFILE%/.house_ai/test_manifest.json and
         the PUBLIC commitment (per-entry SHA-256 + Merkle root) to reports/test_commitment.json.

The key is read only from the environment (HOUSE_AI_SPLIT_KEY, hex) or %USERPROFILE%/.house_ai/split_key. It is never
printed, logged, written inside the repository, sent anywhere, or passed to worker processes (workers receive the
derived candidate seeds only). Every private-range seed is refused by `guard_not_test` whether or not a key or
manifest is present. Standard library only; no network access.
"""
import argparse
import ast
import copy
import fnmatch
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..catalog import ROOT

DEV_SEEDS = (0, 1, 2, 41, 97)            # the public verification seeds (house_sim.tasks.verify.SEEDS)
DEV_EXTRA = (3, 8, 13, 24, 55)           # small published extra dev set (fixed; mixes parities and residues mod 3)
TRAIN_BASE = 1_000_000                   # public train range [TRAIN_BASE, PRIVATE_BASE)
PRIVATE_BASE = 900_000_000               # hidden test seeds live in [PRIVATE_BASE, PRIVATE_END)
PRIVATE_END = 2 ** 31 - 1
TRAIN_SPAN = PRIVATE_BASE - TRAIN_BASE
PRIVATE_SPAN = PRIVATE_END - PRIVATE_BASE

KEY_ENV = "HOUSE_AI_SPLIT_KEY"
COMMITMENT_ENV = "HOUSE_AI_COMMITMENT_FILE"   # optional: another public commitment file (sandboxes, tests)
PRIVATE_DIR = ".house_ai"
COMMITMENT_FILE = ROOT / "reports" / "test_commitment.json"
MANIFEST_FORMAT = "house_ai.test_manifest/1"
COMMITMENT_FORMAT = "house_ai.test_commitment/1"
DEFAULT_K = 10
FAIL_LIMIT = 8                            # a needed variant failing this often with no pass marks the entry incomplete
PERCEPTIONS = ("oracle", "realistic")
PRIVATE_NAMES = ("split_key", "test_manifest.json", "test_phrasings.json", "verifier_key")
VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")   # same placeholder syntax as house_sim.tasks.spec.VAR


class SplitKeyMissing(RuntimeError):
    """Raised when the test split (or anything derived from the key) is requested without a key."""


class ManifestMissing(FileNotFoundError):
    """Raised when the hidden test manifest is required but has not been built on this machine."""


# ----------------------------------------------------------------------------------------------------------------
# Private files (all under Path.home() / ".house_ai", never inside the repository)

def private_dir():
    return Path.home() / PRIVATE_DIR


def key_path():
    return private_dir() / "split_key"


def manifest_path():
    return private_dir() / "test_manifest.json"


def phrasings_path():
    return private_dir() / "test_phrasings.json"


def commitment_file():
    """The public commitment file: env HOUSE_AI_COMMITMENT_FILE when set (a sandbox or test build that must not
    replace the published file), else COMMITMENT_FILE (reports/test_commitment.json)."""
    raw = os.environ.get(COMMITMENT_ENV)
    return Path(raw.strip()) if raw and raw.strip() else Path(COMMITMENT_FILE)


def _inside_repo(path):
    try:
        resolved, root = Path(path).resolve(), ROOT.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _refuse_repo_path(path, what):
    if _inside_repo(path):
        raise ValueError("refusing to write the {} inside the repository ({}); private files live in {}".format(
            what, path, private_dir()))


def _write_private(path, text):
    """Atomic write with owner-only permissions where the platform supports them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# ----------------------------------------------------------------------------------------------------------------
# Key

def _parse_key(text, source):
    cleaned = "".join(str(text).split())
    try:
        key = bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError("split key from {} is not valid hex".format(source)) from None
    if len(key) < 16:
        raise ValueError("split key from {} is too short (need at least 16 bytes = 32 hex digits)".format(source))
    return key


def _coerce_key(key):
    if key is None:
        return load_key(required=True)
    if isinstance(key, (bytes, bytearray)):
        if len(key) < 16:
            raise ValueError("split key is too short (need at least 16 bytes)")
        return bytes(key)
    if isinstance(key, str):
        return _parse_key(key, "argument")
    raise TypeError("split key must be bytes or a hex string")


def key_source():
    """"env", "file" or None (which source load_key would use). Never returns the key."""
    raw = os.environ.get(KEY_ENV)
    if raw is not None and raw.strip():
        return "env"
    return "file" if key_path().is_file() else None


def load_key(required=True):
    """The split key from env HOUSE_AI_SPLIT_KEY (hex) or key_path(). The env variable wins. Never logged."""
    raw = os.environ.get(KEY_ENV)
    if raw is not None and raw.strip():
        return _parse_key(raw, "environment variable " + KEY_ENV)
    path = key_path()
    if path.is_file():
        return _parse_key(path.read_text(encoding="utf-8", errors="replace"), str(path))
    if required:
        raise SplitKeyMissing("No split key: set {} (hex) or create one with `python -m house_sim --init-split-key` "
                              "(writes {}). The test split needs it.".format(KEY_ENV, path))
    return None


def init_key(force=False):
    """Create key_path() with secrets.token_bytes(32) (hex). Refuses to overwrite unless force."""
    path = key_path()
    _refuse_repo_path(path, "split key")
    if path.exists() and not force:
        raise FileExistsError("A split key already exists at {} (pass --force to replace it; manifests built with the "
                              "old key become unusable).".format(path))
    _write_private(path, secrets.token_bytes(32).hex() + "\n")
    return path


def key_digest(key):
    """SHA-256 of the key (hex), for commitments. Never the key itself."""
    return hashlib.sha256(_coerce_key(key)).hexdigest()


# ----------------------------------------------------------------------------------------------------------------
# Seeds

def _as_seed(seed):
    if isinstance(seed, bool):
        raise ValueError("invalid seed")
    try:
        return int(seed)
    except (TypeError, ValueError):
        raise ValueError("invalid seed") from None


def train_seeds(catalog_id, n, start=0):
    """n deterministic seeds in the train range: a per-entry offset (SHA-256 of the catalog id) then consecutive
    seeds, so seed % len(variants) cycles through every variant. Distinct while start + n <= TRAIN_SPAN."""
    n, start = int(n), int(start)
    if n < 0 or start < 0:
        raise ValueError("n and start must be >= 0")
    if start + n > TRAIN_SPAN:
        raise ValueError("train range exhausted (start + n > {})".format(TRAIN_SPAN))
    digest = hashlib.sha256(("house_ai.train:" + str(catalog_id)).encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big") % TRAIN_SPAN
    return [TRAIN_BASE + (offset + start + i) % TRAIN_SPAN for i in range(n)]


def dev_seeds(catalog_id, extra=False):
    """The public dev seeds (same for every entry): DEV_SEEDS, plus DEV_EXTRA when extra."""
    return list(DEV_SEEDS) + (list(DEV_EXTRA) if extra else [])


def _candidate_seed(catalog_id, i, key):
    digest = hmac.new(key, "{}:{}".format(catalog_id, i).encode("utf-8"), hashlib.sha256).digest()
    return PRIVATE_BASE + int.from_bytes(digest[:8], "big") % PRIVATE_SPAN


def _candidate_stream(catalog_id, key, count):
    """The first `count` distinct candidates for i = 0, 1, 2, ... (a collision is skipped, never repeated)."""
    seen, out, i = set(), [], 0
    while len(out) < count:
        seed = _candidate_seed(catalog_id, i, key)
        if seed not in seen:
            seen.add(seed)
            out.append(seed)
        i += 1
    return out


def test_candidates(catalog_id, k, key):
    """k hidden candidate seeds: HMAC-SHA256(key, f"{catalog_id}:{i}") -> [PRIVATE_BASE, PRIVATE_END)."""
    return _candidate_stream(catalog_id, _coerce_key(key), int(k))


test_candidates.__test__ = False   # not a test function for pytest-style collectors


def is_private_seed(seed):
    """seed in the private range [PRIVATE_BASE, PRIVATE_END). Always checkable, no key needed."""
    try:
        value = _as_seed(seed)
    except ValueError:
        return False
    return PRIVATE_BASE <= value < PRIVATE_END


def is_test_seed(catalog_id, seed):
    """Private range, or listed in the private manifest when one is available (any entry: a seed fixes the house).
    A manifest that exists but cannot be read raises (fail closed)."""
    if is_private_seed(seed):
        return True
    value = _as_seed(seed)
    doc = _manifest_doc(required=False)
    if doc is None:
        return False
    entry = doc["entries"].get(catalog_id) or {}
    return value in set(entry.get("seeds", [])) or value in doc["_all_seeds"]


def guard_not_test(catalog_id, seed):
    """Raise ValueError("test seed refused") for private-range or manifest-listed seeds."""
    if is_test_seed(catalog_id, _as_seed(seed)):
        raise ValueError("test seed refused")


def seed_tag(catalog_id, seed, key=None):
    """Public per-episode tag: "dev:<seed>", "train:<seed>", "test:<12 hex>" or "seed:<seed>" (other public seeds).
    Test tags are HMAC-SHA256(key, "tag:<catalog_id>:<seed>")[:12]: an unkeyed hash of a ~30-bit seed could be
    brute-forced back to the seed, so test tags need the key (SplitKeyMissing otherwise)."""
    value = _as_seed(seed)
    if is_test_seed(catalog_id, value):
        mac = hmac.new(_coerce_key(key), "tag:{}:{}".format(catalog_id, value).encode("utf-8"), hashlib.sha256)
        return "test:" + mac.hexdigest()[:12]
    if value in DEV_SEEDS or value in DEV_EXTRA:
        return "dev:{}".format(value)
    if TRAIN_BASE <= value < PRIVATE_BASE:
        return "train:{}".format(value)
    return "seed:{}".format(value)


def safe_tag(tag):
    """A seed tag usable in a Windows file name (":" is not allowed there)."""
    return str(tag).replace(":", "-")


def resolve_test_tag(catalog_id, tag, key=None, manifest=None):
    """The hidden seed behind a "test:..." tag (needs the key and the private manifest), or None."""
    key = _coerce_key(key)
    entries = _entries_of(manifest) if manifest is not None else load_manifest(required=True)
    for seed in (entries.get(catalog_id) or {}).get("seeds", []):
        if hmac.compare_digest(seed_tag(catalog_id, seed, key=key), str(tag)):
            return seed
    return None


# ----------------------------------------------------------------------------------------------------------------
# Manifest

_MANIFEST_CACHE = {}


def _check_doc(doc, source):
    if not isinstance(doc, dict) or doc.get("format") != MANIFEST_FORMAT or not isinstance(doc.get("entries"), dict):
        raise ValueError("{} is not a hidden test manifest ({})".format(source, MANIFEST_FORMAT))
    for cid, entry in doc["entries"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("seeds"), list):
            raise ValueError("{}: malformed manifest entry {}".format(source, cid))


def _manifest_doc(path=None, required=True):
    """Cached, read-only manifest document (internal; callers must not mutate it)."""
    path = Path(path) if path else manifest_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        if required:
            raise ManifestMissing("No hidden test manifest at {}; build it with "
                                  "`python -m house_sim --build-test-manifest`.".format(path)) from None
        return None
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    doc = _MANIFEST_CACHE.get(cache_key)
    if doc is None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_doc(doc, path)
        doc["_all_seeds"] = frozenset(s for e in doc["entries"].values() for s in e.get("seeds", []))
        _MANIFEST_CACHE.clear()
        _MANIFEST_CACHE[cache_key] = doc
    return doc


def load_manifest(required=True, path=None, with_meta=False):
    """{catalog_id: {"seeds", "variants", "phrasings", "skipped", ...}} from manifest_path(); None when absent and not
    required (ManifestMissing when required). with_meta=True returns the whole document
    {"format", "key_digest", "created_utc", "public_phrasings", "entries"}."""
    doc = _manifest_doc(path, required=required)
    if doc is None:
        return None
    doc = copy.deepcopy({k: v for k, v in doc.items() if k != "_all_seeds"})
    return doc if with_meta else doc["entries"]


def _entries_of(manifest):
    if isinstance(manifest, dict) and manifest.get("format") == MANIFEST_FORMAT and isinstance(manifest.get("entries"), dict):
        return manifest["entries"]
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a dict")
    return manifest


def load_private_phrasings(path=None):
    """The private paraphrase pool {catalog_id: [phrasings]} (or {catalog_id: {"<variant>": [...], "*": [...]}} for
    entries whose variants have different instructions), or None when the file does not exist."""
    path = Path(path) if path else phrasings_path()
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("{} must map catalog_id -> [phrasings]".format(path))

    def good_list(value):
        return isinstance(value, list) and value and all(isinstance(p, str) and p.strip() for p in value)
    for cid, value in data.items():
        if isinstance(value, dict):
            if not value or not all(good_list(v) for v in value.values()):
                raise ValueError("{}: phrasings for {} must be non-empty lists of strings".format(path, cid))
        elif not good_list(value):
            raise ValueError("{}: phrasings for {} must be a non-empty list of strings".format(path, cid))
    return data


def _unique(items):
    out = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _variant_phrasings(expanded, variant):
    from ..tasks.spec import choose_variant
    chosen = choose_variant(expanded, variant)[0] if variant is not None else expanded
    return list(chosen.get("phrasings") or [chosen.get("instruction") or ""])


def _public_phrasings(expanded):
    variants = expanded.get("variants") or []
    if not variants:
        return _variant_phrasings(expanded, None)
    return _unique(p for v in range(len(variants)) for p in _variant_phrasings(expanded, v))


def _check_pool(catalog_id, expanded, pool_value):
    """Fail early (in the parent) on a pool that cannot serve every variant of the entry."""
    if pool_value is None:
        return
    n = len(expanded.get("variants") or [])
    if isinstance(pool_value, list):
        if n and len({json.dumps(_variant_phrasings(expanded, v)) for v in range(n)}) > 1:
            raise ValueError("private phrasings for {}: its variants have different instructions; give them per variant "
                             "as {{\"0\": [...], \"1\": [...]}}".format(catalog_id))
        return
    unknown = [k for k in pool_value if k != "*" and not (k.isdigit() and int(k) < max(n, 1))]
    if unknown:
        raise ValueError("private phrasings for {}: unknown variant keys {}".format(catalog_id, unknown))
    missing = [v for v in range(max(n, 1)) if str(v) not in pool_value and "*" not in pool_value]
    if missing:
        raise ValueError("private phrasings for {}: no phrasings for variants {}".format(catalog_id, missing))


def _assign_phrasings(expanded, seeds, variants, pool_value):
    """-> (phrasings, phrasing_index parallel to seeds, public). Round-robin per variant so every held-out phrasing is
    used when there are enough seeds. With no private pool: the spec's public phrasings, indexes None (the episode
    then keeps the instruction runner.build chose from the public phrasings)."""
    if pool_value is None:
        return _public_phrasings(expanded), [None] * len(seeds), True
    if isinstance(pool_value, list):
        flat = _unique(pool_value)

        def options(variant):
            return "*", pool_value
    else:
        flat = _unique(p for k in sorted(pool_value) for p in pool_value[k])

        def options(variant):
            name = str(variant if variant is not None else 0)
            return (name, pool_value[name]) if name in pool_value else ("*", pool_value["*"])
    counters, index = {}, []
    for variant in variants:
        group, opts = options(variant)
        count = counters.get(group, 0)
        counters[group] = count + 1
        index.append(flat.index(opts[count % len(opts)]))
    return flat, index, False


def _trial_reason(trial):
    if trial.get("error"):
        return str(trial["error"])[:300]
    failed = [k for k in ("initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran") if not trial.get(k)]
    detail = trial.get("reference_error") or trial.get("perceivability_gaps") or trial.get("reference_failures")
    if not detail and not trial.get("wrong_fails", True):
        detail = [w.get("name") for w in trial.get("wrong", []) if not w.get("fails")]
    text = "not " + ",".join(failed) if failed else "failed"
    if detail:
        text += ": " + json.dumps(detail, default=str)[:240]
    return text


def _select(candidates, k, n_variants, trial_fn, fail_limit=FAIL_LIMIT):
    """Walk the candidates in order; verify each with trial_fn(seed) -> trial dict; keep passing ones until k are
    chosen and every variant (seed % n_variants) appears. A candidate whose variant is already covered is passed over
    (unverified, reason "variant_quota") when the remaining slots are needed for uncovered variants. Pure and
    deterministic given trial_fn."""
    chosen, chosen_v, skipped = [], [], []
    covered, passes, fails = set(), {}, {}
    needed = set(range(n_variants))
    tried, error = 0, None
    for index, seed in enumerate(candidates):
        if len(chosen) >= k:
            break
        variant = seed % n_variants if n_variants else None
        if n_variants and variant in covered and (k - len(chosen)) <= len(needed - covered):
            skipped.append({"index": index, "seed": seed, "variant": variant, "reason": "variant_quota"})
            continue
        tried += 1
        trial = trial_fn(seed)
        if trial.get("passed"):
            chosen.append(seed)
            chosen_v.append(variant)
            covered.add(variant)
            passes[variant] = passes.get(variant, 0) + 1
            continue
        fails[variant] = fails.get(variant, 0) + 1
        skipped.append({"index": index, "seed": seed, "variant": variant, "reason": "verification_failed",
                        "detail": _trial_reason(trial)})
        if not passes.get(variant) and fails[variant] >= fail_limit:
            error = ("{} failed verification on {} candidates without a single pass"
                     .format("the entry" if variant is None else "variant {}".format(variant), fails[variant]))
            break
    missing = sorted(needed - covered)
    if error is None and (len(chosen) < k or missing):
        error = "candidate pool exhausted: {}/{} valid seeds{}".format(
            len(chosen), k, ", variants missing {}".format(missing) if missing else "")
    return {"seeds": chosen, "variants": chosen_v, "skipped": skipped, "tried": tried, "error": error,
            "missing_variants": missing}


def _worker_init():
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    from ..kernel.plugins import load_domains
    load_domains()


def _entry_job(job):
    """One entry: verify candidates, choose hidden seeds, assign held-out phrasings. Receives derived candidate seeds
    and the entry's private phrasings, never the key."""
    from ..kernel.errors import SpecError
    from ..tasks.runner import build
    from ..tasks.spec import expand, substitute
    from ..tasks.verify import run_trial
    spec, mode = job["spec"], job["mode"]
    expanded = expand(spec)
    n_variants = len(expanded.get("variants") or [])
    picked = _select(job["candidates"], job["k"], n_variants, lambda seed: run_trial(spec, seed, mode))
    phrasings, index, public = _assign_phrasings(expanded, picked["seeds"], picked["variants"], job["pool"])
    error = picked["error"]
    for seed, pidx in zip(picked["seeds"], index):
        if error or pidx is None or not VAR.search(phrasings[pidx]):
            continue
        try:
            substitute(phrasings[pidx], build(spec, seed)[2]["bindings"])
        except SpecError as exc:
            error = "private phrasing #{} does not bind on a hidden seed: {}".format(pidx, exc)
    considered = 0
    for item in picked["skipped"]:
        considered = max(considered, item["index"] + 1)
    if picked["seeds"]:
        considered = max(considered, job["candidates"].index(picked["seeds"][-1]) + 1)
    entry = {"seeds": picked["seeds"], "variants": picked["variants"], "n_variants": n_variants,
             "phrasings": phrasings, "phrasing_index": index, "public_phrasings": public,
             "skipped": picked["skipped"], "candidates_tried": picked["tried"], "candidates_considered": considered,
             "k": job["k"], "k_requested": job["k_requested"], "complete": error is None,
             "perception": mode or expanded.get("perception") or "oracle"}
    if error:
        entry["error"] = error
    return entry


def _spec_list(specs):
    if isinstance(specs, dict):
        specs = specs["specs"] if isinstance(specs.get("specs"), list) else [specs]
    out = []
    for item in specs:
        spec = item["spec"] if isinstance(item, dict) and isinstance(item.get("spec"), dict) else item
        if not isinstance(spec, dict) or not spec.get("catalog_id"):
            raise ValueError("every spec needs a catalog_id")
        out.append(spec)
    seen = set()
    for spec in out:
        if spec["catalog_id"] in seen:
            raise ValueError("duplicate catalog_id {}".format(spec["catalog_id"]))
        seen.add(spec["catalog_id"])
    return out


def build_test_manifest(specs, k=DEFAULT_K, workers=None, key=None, perception=None, max_candidates=None,
                        phrasings=None, progress=None):
    """Verify hidden candidates and return the private manifest {catalog_id: entry} (not written; see save_manifest).

    Each entry gets k seeds (raised to the number of variants when smaller) that pass house_sim.tasks.verify.run_trial
    in the spec's own perception mode ("realistic" when the spec declares it; `perception="realistic"` forces it for
    every entry). Failing candidates are skipped deterministically and recorded with the reason. Entries that cannot
    be completed from max_candidates (default 3k+10) candidates are kept with "complete": false and an "error"; the
    test split never uses them. phrasings: the private pool (default: load_private_phrasings())."""
    from ..audit import fingerprint
    from ..tasks.spec import expand
    from ..tasks.verify import spec_hash
    key = _coerce_key(key)
    if perception not in (None, "realistic"):
        raise ValueError("perception must be None (each spec's own mode) or 'realistic'")
    if int(k) < 1:
        raise ValueError("k must be at least 1 (hidden seeds per entry)")
    pool = load_private_phrasings() if phrasings is None else phrasings
    code = fingerprint()
    jobs = []
    for spec in _spec_list(specs):
        cid = spec["catalog_id"]
        expanded = expand(spec)
        n_variants = len(expanded.get("variants") or [])
        k_eff = max(int(k), n_variants, 1)
        limit = int(max_candidates) if max_candidates else 3 * k_eff + 10
        pool_value = (pool or {}).get(cid)
        _check_pool(cid, expanded, pool_value)
        jobs.append({"catalog_id": cid, "spec": copy.deepcopy(spec), "candidates": _candidate_stream(cid, key, max(limit, k_eff)),
                     "k": k_eff, "k_requested": int(k), "mode": perception or expanded.get("perception") or None,
                     "pool": copy.deepcopy(pool_value), "spec_hash": spec_hash(spec)})
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    results = []
    if workers == 1 or len(jobs) <= 1:
        from ..kernel.plugins import load_domains
        load_domains()
        for i, job in enumerate(jobs, 1):
            results.append(_entry_job(job))
            if progress:
                progress(i, len(jobs), job["catalog_id"], results[-1])
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), initializer=_worker_init) as executor:
            for i, (job, result) in enumerate(zip(jobs, executor.map(_entry_job, jobs, chunksize=1)), 1):
                results.append(result)
                if progress:
                    progress(i, len(jobs), job["catalog_id"], result)
    manifest = {}
    for job, entry in zip(jobs, results):
        entry["spec_hash"] = job["spec_hash"]
        entry["fingerprint"] = code
        entry["nonce"] = new_nonce()
        manifest[job["catalog_id"]] = entry
    return manifest


NONCE_BYTES = 32


def new_nonce():
    """A fresh random blinding value for one manifest entry (hex). It is part of the hashed entry, so the public
    per-entry hash cannot be brute-forced from the entry's otherwise low-entropy content (a ~30-bit seed, public
    phrasings, small counters, the public spec hash and code fingerprint). It stays private with the entry and is
    revealed with it when the commitment is opened, so verification against key_digest needs no key."""
    return secrets.token_hex(NONCE_BYTES)


def _blind(entries):
    """Give every entry that lacks one a nonce (in place, so the caller's manifest matches the saved file and the
    commitment). Returns the number of entries that were blinded now."""
    added = 0
    for entry in entries.values():
        if isinstance(entry, dict) and not (isinstance(entry.get("nonce"), str) and len(entry["nonce"]) >= 2 * 16):
            entry["nonce"] = new_nonce()
            added += 1
    return added


def weak_entries(manifest):
    """Catalog ids whose entry has a single hidden seed and no variants (k < 2): still blinded by the nonce, but one
    seed per entry gives a noisy score and the commitment's "k" advertises it. The CLI warns about them."""
    out = []
    for cid, entry in _entries_of(manifest).items():
        if isinstance(entry, dict) and len(entry.get("seeds") or []) < 2 and not entry.get("n_variants"):
            out.append(cid)
    return sorted(out)


def save_manifest(manifest, key=None, path=None, commitment_path=None, merge=False):
    """Write the private manifest (default manifest_path(), never inside the repository) and the public commitment
    (default commitment_file()). merge=True keeps existing entries built with the same key. Every entry is blinded
    with a random "nonce" first (added in place when missing). Returns the commitment."""
    key = _coerce_key(key)
    path = Path(path) if path else manifest_path()
    _refuse_repo_path(path, "hidden test manifest")
    entries = dict(_entries_of(manifest))
    digest = key_digest(key)
    if merge:
        old = _manifest_doc(path, required=False)
        if old is not None:
            if old.get("key_digest") != digest:
                raise ValueError("cannot merge: the existing manifest was built with a different split key")
            merged = copy.deepcopy(old["entries"])
            merged.update(entries)
            entries = merged
    _blind(entries)                     # an entry without a nonce would make its public hash brute-forceable
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    doc = {"format": MANIFEST_FORMAT, "key_digest": digest, "created_utc": now,
           "public_phrasings": any(e.get("public_phrasings") for e in entries.values()),
           "entries": {cid: entries[cid] for cid in sorted(entries)}}
    _write_private(path, json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    public = commitment(entries, key)
    public = {"format": COMMITMENT_FORMAT, "created_utc": now, "algorithm": COMMITMENT_ALGORITHM, **public,
              "k": sorted({e.get("k_requested", e.get("k")) for e in entries.values()} - {None}),
              "seeds_total": sum(len(e.get("seeds", [])) for e in entries.values()),
              "incomplete": sorted(cid for cid, e in entries.items() if not e.get("complete", True)),
              "public_phrasings": doc["public_phrasings"], "blinded": True}
    target = Path(commitment_path) if commitment_path else commitment_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(public, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return public


# ----------------------------------------------------------------------------------------------------------------
# Commitment

COMMITMENT_ALGORITHM = ("per_entry[cid] = sha256(canonical({'catalog_id': cid, 'entry': entry}) + '|' + key_digest), "
                        "where entry includes its private random 'nonce' (32 bytes hex) that blinds the hash; "
                        "canonical = json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=True); "
                        "leaf = sha256(0x00 + utf8(cid) + 0x00 + per_entry[cid]) over catalog ids in sorted order; "
                        "node = sha256(0x01 + left + right); an unpaired node is promoted; empty root = sha256('')")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _entry_hash(catalog_id, entry, digest):
    return hashlib.sha256((_canonical({"catalog_id": catalog_id, "entry": entry}) + "|" + digest).encode("utf-8")).hexdigest()


def _leaf(catalog_id, entry_hash):
    return hashlib.sha256(b"\x00" + catalog_id.encode("utf-8") + b"\x00" + bytes.fromhex(entry_hash)).digest()


def _levels(per_entry):
    level = [_leaf(cid, per_entry[cid]) for cid in sorted(per_entry)]
    levels = [level]
    while len(level) > 1:
        nxt = [hashlib.sha256(b"\x01" + level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
        levels.append(level)
    return levels


def merkle_root(per_entry):
    if not per_entry:
        return hashlib.sha256(b"").hexdigest()
    return _levels(per_entry)[-1][0].hex()


def merkle_proof(per_entry, catalog_id):
    """Inclusion proof for one entry: [{"side": "L"|"R", "hash": hex}, ...] from leaf to root."""
    order = sorted(per_entry)
    index = order.index(catalog_id)
    proof = []
    for level in _levels(per_entry)[:-1]:
        sibling = index ^ 1
        if sibling < len(level):
            proof.append({"side": "L" if sibling < index else "R", "hash": level[sibling].hex()})
        index //= 2
    return proof


def verify_merkle_proof(catalog_id, entry_hash, proof, root):
    try:
        node = _leaf(catalog_id, entry_hash)
        for step in proof:
            sibling = bytes.fromhex(step["hash"])
            node = hashlib.sha256(b"\x01" + (sibling + node if step["side"] == "L" else node + sibling)).digest()
        return hmac.compare_digest(node.hex(), str(root))
    except (KeyError, TypeError, ValueError):
        return False


def _commitment_for_digest(entries, digest):
    per_entry = {cid: _entry_hash(cid, entries[cid], digest) for cid in sorted(entries)}
    return {"per_entry": per_entry, "root": merkle_root(per_entry), "key_digest": digest}


def commitment(manifest, key):
    """{"per_entry": {cid: sha256}, "root": Merkle root, "key_digest": sha256(key)}. Publishable when every entry
    carries its random "nonce" (build_test_manifest and save_manifest add it): without one, an entry's hash commits
    only low-entropy data and its seed can be brute-forced from the public hash. Anyone given the manifest later
    can recompute it."""
    return _commitment_for_digest(_entries_of(manifest), key_digest(key))


def commitment_consistent(public):
    """The public file's root matches its own per-entry hashes (checkable without the manifest or key)."""
    try:
        return hmac.compare_digest(merkle_root(public["per_entry"]), str(public["root"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def verify_commitment(manifest, key, public):
    """True when `public` commits to exactly this manifest and key. key=None checks the manifest against the
    commitment's own key_digest (for a manifest revealed after the evaluation)."""
    try:
        entries = _entries_of(manifest)
        digest = key_digest(key) if key is not None else str(public["key_digest"])
        if not hmac.compare_digest(str(public.get("key_digest")), digest):
            return False
        expected = _commitment_for_digest(entries, digest)
        published = public["per_entry"]
        if set(published) != set(expected["per_entry"]):
            return False
        if not all(hmac.compare_digest(str(published[c]), expected["per_entry"][c]) for c in expected["per_entry"]):
            return False
        return commitment_consistent(public) and hmac.compare_digest(str(public["root"]), expected["root"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def load_commitment(path=None):
    path = Path(path) if path else commitment_file()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def commitment_root(path=None):
    """The published Merkle root (for run.json), or None when no commitment exists."""
    public = load_commitment(path)
    return public.get("root") if public else None


# ----------------------------------------------------------------------------------------------------------------
# Episodes

def _norm_split(split):
    name = str(split).lower().replace("-", "_").replace("+", "_")
    if name in ("dev_extra", "dev_all"):
        return "dev", True
    if name in ("train", "dev", "test"):
        return name, False
    raise ValueError("unknown split {!r} (train, dev, dev_extra, test)".format(split))


def episodes(split, specs, seeds_per_entry=None, seeds=None, perception=None, strict=True, key=None, manifest=None):
    """Episode dicts {"catalog_id", "split", "seed", "seed_tag", "phrasing", "perception"}.

    train: train_seeds(cid, seeds_per_entry or 1); dev: DEV_SEEDS (seeds_per_entry > 5 or split "dev_extra" adds
    DEV_EXTRA); explicit `seeds` override both and pass guard_not_test. Their perception is the spec's own unless the
    caller forces one. test: needs the key (SplitKeyMissing) and the private manifest (ManifestMissing); perception is
    always "realistic"; phrasing is the held-out phrasing for that seed (None when the manifest uses the spec's public
    phrasings). strict=True raises ValueError for specs that are absent from the manifest, incomplete there, or whose
    spec_hash changed since the hidden seeds were validated; strict=False skips them."""
    name, extra = _norm_split(split)
    if perception not in (None,) + PERCEPTIONS:
        raise ValueError("perception must be None, 'oracle' or 'realistic'")
    specs = _spec_list(specs)
    out = []
    if name == "test":
        return _test_episodes(specs, seeds_per_entry, seeds, perception, strict, key, manifest)
    from ..tasks.spec import expand
    for spec in specs:
        cid = spec["catalog_id"]
        mode = perception or expand(spec).get("perception") or "oracle"
        if seeds is not None:
            chosen = [_as_seed(s) for s in seeds]
        elif name == "train":
            chosen = train_seeds(cid, 1 if seeds_per_entry is None else seeds_per_entry)
        else:
            chosen = dev_seeds(cid, extra=extra or (seeds_per_entry or 0) > len(DEV_SEEDS))
            if seeds_per_entry is not None:
                chosen = chosen[:int(seeds_per_entry)]
        for seed in chosen:
            guard_not_test(cid, seed)
            out.append({"catalog_id": cid, "split": name, "seed": seed, "seed_tag": seed_tag(cid, seed),
                        "phrasing": None, "perception": mode})
    return out


def _test_episodes(specs, seeds_per_entry, seeds, perception, strict, key, manifest):
    from ..tasks.verify import spec_hash
    if perception not in (None, "realistic"):
        raise ValueError("test episodes always use realistic perception")
    key = _coerce_key(key)
    if manifest is None:
        doc = load_manifest(required=True, with_meta=True)
    elif isinstance(manifest, dict) and manifest.get("format") == MANIFEST_FORMAT:
        doc = manifest
    else:
        doc = {"entries": _entries_of(manifest)}
    if doc.get("key_digest") and not hmac.compare_digest(doc["key_digest"], key_digest(key)):
        raise ValueError("the hidden test manifest was built with a different split key")
    wanted = None if seeds is None else {_as_seed(s) for s in seeds}
    out, problems = [], []
    for spec in specs:
        cid = spec["catalog_id"]
        entry = doc["entries"].get(cid)
        if entry is None:
            problems.append(cid + ": not in the hidden test manifest")
            continue
        if not entry.get("complete", True):
            problems.append(cid + ": incomplete in the hidden test manifest ({})".format(entry.get("error", "")))
            continue
        if entry.get("spec_hash") and entry["spec_hash"] != spec_hash(spec):
            problems.append(cid + ": spec changed since its hidden seeds were validated (rebuild the manifest)")
            continue
        index = entry.get("phrasing_index") or [None] * len(entry["seeds"])
        rows = list(zip(entry["seeds"], index))
        if wanted is not None:
            unknown = wanted - set(entry["seeds"])
            if unknown:
                raise ValueError("{}: {} requested seed(s) are not hidden seeds of this entry".format(cid, len(unknown)))
            rows = [r for r in rows if r[0] in wanted]
        if seeds_per_entry is not None:
            rows = rows[:int(seeds_per_entry)]
        for seed, pidx in rows:
            out.append({"catalog_id": cid, "split": "test", "seed": seed, "seed_tag": seed_tag(cid, seed, key=key),
                        "phrasing": entry["phrasings"][pidx] if pidx is not None else None, "perception": "realistic"})
    if problems and strict:
        raise ValueError("test split unavailable for {} spec(s): {}".format(len(problems), "; ".join(problems[:8])))
    return out


def apply_phrasing(task, phrasing, bindings):
    """task with task["instruction"] replaced by the episode's phrasing (placeholders bound like runner.build)."""
    if not phrasing:
        return task
    from ..tasks.spec import substitute
    task = dict(task)
    task["instruction"] = substitute(phrasing, bindings)
    return task


def build_episode(spec, episode):
    """(config, task, oracle) for an episode from episodes(): runner.build(spec, seed) plus the held-out phrasing.
    Run it with HouseEnv(config, task, seed, perception=episode["perception"], trace=False, observe_on_step=True)."""
    from ..tasks.runner import build
    if episode["split"] != "test":
        guard_not_test(episode["catalog_id"], episode["seed"])
    config, task, oracle = build(spec, episode["seed"])
    return config, apply_phrasing(task, episode.get("phrasing"), oracle["bindings"]), oracle


# ----------------------------------------------------------------------------------------------------------------
# Verified specs

def candidate_records(root=None, only=None):
    """Authored specs whose structure is valid (compiler status CANDIDATE), in catalog order, read-only (unlike
    compile_specs this writes nothing). Each record: catalog_id, kind, section, text, source_file, domain, title,
    risk_level, fidelity, perception, spec, spec_hash. only: catalog ids, sections, source files or domains."""
    from ..catalog import parse_catalog
    from ..kernel.errors import SpecError
    from ..tasks.spec import check_structure, expand
    from ..tasks.verify import spec_hash
    from .common import load_authored
    root = Path(root) if root else ROOT
    entries, _ = parse_catalog(root / "catalog" / "raw" / "household_catalog.txt")
    authored, sources, _conflicts = load_authored(root)   # ids authored twice are left out, never guessed
    only = set(only) if only else None
    out = []
    for entry in entries:
        spec = authored.get(entry["id"])
        if spec is None:
            continue
        try:
            expanded = expand(spec)
            if check_structure(expanded):
                continue
        except SpecError:
            continue
        source = sources.get(entry["id"]) or ""
        domain = source.split("__")[0] if "__" in source else Path(source).stem
        if only is not None and not ({entry["id"], entry["section"], source, domain} & only):
            continue
        out.append({"catalog_id": entry["id"], "kind": entry["kind"], "section": entry["section"], "text": entry["text"],
                    "source_file": source, "domain": domain, "title": expanded.get("title"),
                    "risk_level": expanded.get("risk_level"), "fidelity": expanded.get("fidelity", "S"),
                    "perception": expanded.get("perception") or "oracle", "spec": spec, "spec_hash": spec_hash(spec)})
    return out


def filter_verified(items, verify=False, seeds=DEV_SEEDS, workers=None, root=None, progress=None):
    """Keep the specs (or records) with passing verification. verify=False: current evidence in
    reports/verification.jsonl (passed, implementation_hash == fingerprint(), spec_hash matches). verify=True: run
    house_sim.tasks.verify.verify_many on `seeds` (the dev seeds) in each spec's own perception mode now; nothing is
    written. Returns (kept, rejected) lists of (item, verification_record_or_reason)."""
    from ..audit import fingerprint
    from ..catalog import read_jsonl
    from ..tasks.verify import spec_hash, verify_many
    root = Path(root) if root else ROOT
    items = list(items)
    specs = [i["spec"] if isinstance(i.get("spec"), dict) else i for i in items]
    kept, rejected = [], []
    if verify:
        results = verify_many(specs, seeds=tuple(seeds), workers=workers, progress=progress, perception=None)
        for item, result in zip(items, results):
            (kept if result["passed"] else rejected).append((item, result))
        return kept, rejected
    path = root / "reports" / "verification.jsonl"
    evidence = {r["catalog_id"]: r for r in read_jsonl(path)} if path.is_file() else {}
    current = fingerprint(root)
    for item, spec in zip(items, specs):
        proof = evidence.get(spec["catalog_id"])
        if not proof:
            rejected.append((item, "no verification evidence"))
        elif not proof.get("passed"):
            rejected.append((item, "verification failed"))
        elif proof.get("implementation_hash") != current:
            rejected.append((item, "evidence is stale (implementation changed since verification)"))
        elif proof.get("spec_hash") != spec_hash(spec):
            rejected.append((item, "evidence is stale (spec changed since verification)"))
        else:
            kept.append((item, proof))
    return kept, rejected


def verified_specs(root=None, verify=False, seeds=DEV_SEEDS, workers=None, only=None, records=False, progress=None):
    """Specs with passing verification for the current code: by default current evidence in
    reports/verification.jsonl; verify=True verifies the selected specs on the dev seeds now (use it while domain
    code is changing and the evidence is stale). Never treats unverified specs as verified. records=True returns
    candidate_records() dicts with "evidence" ("report" | "on_the_fly") and "verification" added."""
    items = candidate_records(root, only=only)
    kept, _ = filter_verified(items, verify=verify, seeds=seeds, workers=workers, root=root, progress=progress)
    if not records:
        return [copy.deepcopy(item["spec"]) for item, _ in kept]
    out = []
    for item, proof in kept:
        record = dict(item)
        record["evidence"] = "on_the_fly" if verify else "report"
        record["verification"] = proof
        out.append(record)
    return out


# ----------------------------------------------------------------------------------------------------------------
# Packaging audit (the sdist file list is emulated from MANIFEST.in and setup.py; setuptools is not required)

PRIVATE_RESULTS_DIR = "private_results"   # %USERPROFILE%/.house_ai/private_results/<run_id>/: test replays, transcripts,
                                          # private viewer pages (house_sim/eval/run.py, viewer.py)
PRIVATE_MARK = "__PRIVATE"                # file-name suffix of private viewer pages (viewer.export_viewer) and exports
PRIVATE_PAGE_TITLE = "PRIVATE - test split"
DEFAULT_PACKAGE_DATA = {"house_sim": ["data/*.json"]}   # used when setup.py is absent or unreadable
_CODE_SUFFIXES = (".py", ".pyc", ".pyo", ".pyw")
_DOC_SUFFIXES = (".md", ".rst")
_SEED_FIELD = re.compile(r"""["'](?:[A-Za-z0-9]+_)*seeds?["']\s*:\s*(\[[^\[\]]*\]|["']?-?\d+)""")
_INT = re.compile(r"-?\d+")
_MANIFEST_MARK = re.compile(r'"format"\s*:\s*"' + re.escape(MANIFEST_FORMAT) + '"')
_PAGE_TITLE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.S | re.I)
_EPISODE_DATA = re.compile(r"""<script\b[^>]*\bid\s*=\s*["']episode-data["'][^>]*>(.*?)</script\s*>""", re.S | re.I)


def _chunk_re(chunk):
    out, i = [], 0
    while i < len(chunk):
        c = chunk[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = chunk.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                out.append(chunk[i:j + 1])
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _glob_re(pattern):
    """A MANIFEST.in / package_data glob as a regex over "/"-separated relative paths, like setuptools'
    translate_pattern: "*" and "?" stay inside one path component; a whole "**" component matches any number of
    directories (zero included), or anything when it is the last component."""
    chunks = pattern.split("/")
    out = []
    for n, chunk in enumerate(chunks):
        last = n == len(chunks) - 1
        if chunk == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
        else:
            out.append(_chunk_re(chunk) + ("" if last else "/"))
    return re.compile("".join(out) + r"\Z", re.S)


def _tree_files(root):
    skip = {".git", ".hg", ".svn", "__pycache__", "build", "dist", ".venv"}
    files = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in skip or part.endswith(".egg-info") for part in rel.parts[:-1]) or not path.is_file():
            continue
        files.append(rel.as_posix())
    return files


def _package_data(root):
    """setup.py's package_data {package: [glob, ...]}, read statically (ast; setup.py is never executed).
    DEFAULT_PACKAGE_DATA when setup.py is absent or its package_data is not a literal."""
    try:
        tree = ast.parse((Path(root) / "setup.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return DEFAULT_PACKAGE_DATA
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg == "package_data":
                try:
                    value = ast.literal_eval(keyword.value)
                except (ValueError, TypeError, SyntaxError):
                    return DEFAULT_PACKAGE_DATA
                if not isinstance(value, dict):
                    return DEFAULT_PACKAGE_DATA
                return {str(k): [str(p) for p in v] for k, v in value.items() if isinstance(v, (list, tuple))}
        return {}
    return DEFAULT_PACKAGE_DATA


def packaged_files(root=None):
    """{"sdist": [...], "wheel": [...]}: the files an sdist would contain (setuptools defaults: package modules,
    setup.py's package_data, setup.py, README*, MANIFEST.in; then MANIFEST.in commands in order, with setuptools'
    glob rules) and the extra resources the wheel gets from setup.py's build step (catalog, tasks, reports, demos
    copied wholesale, plus coverage.csv). The wheel list deliberately ignores the build step's own ignore patterns,
    so a private file in those folders is reported even if one of them would happen to skip it."""
    root = Path(root) if root else ROOT
    files = _tree_files(root)
    packages = set()
    for f in files:
        parts = f.split("/")
        if parts[-1] == "__init__.py" and len(parts) > 1:
            chain = ["/".join(parts[:i]) for i in range(1, len(parts))]
            if all(c + "/__init__.py" in files for c in chain):
                packages.add("/".join(parts[:-1]))
    package_files = {f for f in files if f.endswith(".py") and f.rsplit("/", 1)[0] in packages}
    data_globs = []
    for package, patterns in _package_data(root).items():
        targets = sorted(packages) if package == "" else [package.replace(".", "/")]
        data_globs += [_glob_re(target + "/" + pattern) for target in targets for pattern in patterns]
    package_files |= {f for f in files if any(g.match(f) for g in data_globs)}
    selected = set(package_files)
    selected |= {f for f in files if f in ("setup.py", "setup.cfg", "pyproject.toml", "MANIFEST.in")
                 or ("/" not in f and fnmatch.fnmatchcase(f, "README*"))}
    template = root / "MANIFEST.in"
    lines = template.read_text(encoding="utf-8").splitlines() if template.is_file() else []
    for raw in lines:
        words = raw.split()
        if not words or words[0].startswith("#"):
            continue
        cmd, args = words[0], words[1:]
        if cmd in ("include", "exclude"):
            globs = [_glob_re(p) for p in args]
        elif cmd in ("recursive-include", "recursive-exclude") and args:
            globs = [_glob_re(args[0].strip("/") + "/**/" + p) for p in args[1:]]
        elif cmd in ("global-include", "global-exclude"):
            globs = [_glob_re("**/" + p) for p in args]
        elif cmd in ("graft", "prune"):
            globs = [_glob_re(a.strip("/") + "/**") for a in args]
        else:
            continue
        hit = {f for f in files if any(g.match(f) for g in globs)}
        if cmd in ("include", "recursive-include", "global-include", "graft"):
            selected |= hit
        else:
            selected -= hit
    wheel = set(package_files)
    for folder in ("catalog", "tasks", "reports", "demos"):
        wheel |= {f for f in files if f.startswith(folder + "/")}
    wheel |= {f for f in files if f == "coverage.csv"}
    return {"sdist": sorted(selected), "wheel": sorted(wheel)}


def _private_name(path):
    """Why a packaged path ("/"-separated, relative) is private by its name alone, or None."""
    parts = path.split("/")
    name, dirs = parts[-1], parts[:-1]
    if name in PRIVATE_NAMES:
        return "private split file " + name
    if parts[0] == "results":
        return "raw run output under results/"
    if PRIVATE_DIR in dirs:
        return "inside a {}/ directory".format(PRIVATE_DIR)
    if PRIVATE_RESULTS_DIR in dirs:
        return "inside a {}/ directory (test-split replays, transcripts, private viewer pages)".format(PRIVATE_RESULTS_DIR)
    if any(PRIVATE_MARK in part for part in parts):
        return "a {} export (private viewer page or record)".format(PRIVATE_MARK)
    return None


def _private_file(path):
    return _private_name(path) is not None


def _audit_context():
    """What the content checks compare against: the split key(s) present here (env and file, as hex) and the seeds
    listed in the private manifest. Read locally; never returned by the audit, logged or printed."""
    sources = []
    raw = os.environ.get(KEY_ENV)
    if raw is not None and raw.strip():
        sources.append((raw, KEY_ENV))
    try:
        if key_path().is_file():
            sources.append((key_path().read_text(encoding="utf-8", errors="replace"), str(key_path())))
    except OSError:
        pass
    keys = set()
    for text, source in sources:
        try:
            keys.add(_parse_key(text, source).hex())
        except ValueError:
            pass
    listed = set()
    try:
        doc = _manifest_doc(required=False)
    except (ValueError, OSError):
        doc = None                        # --split-info reports an unreadable manifest separately
    for seed in (doc or {}).get("_all_seeds", ()):
        try:
            listed.add(_as_seed(seed))
        except ValueError:
            pass
    return {"keys": tuple(sorted(keys)), "listed": frozenset(listed)}


def _private_page(text):
    """A viewer page marked private: its <title> starts with "PRIVATE - " (viewer.render_html) or names the private
    banner, or its embedded episode data says meta.private, split "test" or a "test:" seed tag. The banner text is
    in every page's template (hidden unless the page is private), so the page body is not searched for it."""
    match = _PAGE_TITLE.search(text)
    if match:
        title = html.unescape(match.group(1)).strip()
        if title.startswith("PRIVATE - ") or PRIVATE_PAGE_TITLE in title:
            return True
    for match in _EPISODE_DATA.finditer(text):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        meta = data.get("meta") if isinstance(data, dict) else None
        if isinstance(meta, dict) and (bool(meta.get("private")) or str(meta.get("split") or "").lower() == "test"
                                       or str(meta.get("seed_tag") or "").startswith("test:")):
            return True
    return False


def _private_content(path, text, context):
    """Why a packaged file is private by its content, or None. Never includes the seed, phrasing or key."""
    if context["keys"]:
        lowered = text.lower()
        if any(key in lowered for key in context["keys"]):
            return "contains the split key"
    suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path.rsplit("/", 1)[-1] else ""
    if suffix in _CODE_SUFFIXES:          # code and test fixtures may spell private-range numbers on purpose
        return None
    if suffix not in _DOC_SUFFIXES and _MANIFEST_MARK.search(text):
        return "a copy of the hidden test manifest"
    if _private_page(text):
        return "a viewer page marked '{}'".format(PRIVATE_PAGE_TITLE)
    for match in _SEED_FIELD.finditer(text):
        for number in _INT.findall(match.group(1)):
            value = int(number)
            if is_private_seed(value) or value in context["listed"]:
                return "embeds a hidden test seed (e.g. a test-split replay or log; the seed is not shown)"
    return None


def packaging_audit(root=None):
    """{"sdist": [...], "wheel": [...], "reasons": {path: reason}}: the private files packaged_files() would ship and
    why. A file is private by name (split_key, test_manifest.json, test_phrasings.json, anything under results/,
    a .house_ai/ or private_results/ directory, or a name containing "__PRIVATE") or by content (the split key; a
    copy of the hidden test manifest; a viewer page marked "PRIVATE - test split"; a "seed"/"seeds" field holding a
    private-range or manifest-listed seed, as in a test-split replay, whatever the file is called). Code files (.py)
    are checked for the key only. A file that cannot be read is reported (fail closed). Reasons never contain a
    seed, a phrasing or the key."""
    root = Path(root) if root else ROOT
    listing = packaged_files(root)
    context, reasons = None, {}
    for path in sorted(set(listing["sdist"]) | set(listing["wheel"])):
        reason = _private_name(path)
        if reason is None:
            if context is None:
                context = _audit_context()
            try:
                text = (root / path).read_bytes().decode("utf-8", "replace")
            except OSError:
                reason = "could not be read for the audit (reported, fail closed)"
            else:
                reason = _private_content(path, text, context)
        if reason:
            reasons[path] = reason
    return {"sdist": [f for f in listing["sdist"] if f in reasons],
            "wheel": [f for f in listing["wheel"] if f in reasons], "reasons": reasons}


def private_files_in_package(root=None):
    """{"sdist": [...], "wheel": [...]} private files that would be packaged (see packaging_audit for what counts).
    Both lists must be empty."""
    audit = packaging_audit(root)
    return {"sdist": audit["sdist"], "wheel": audit["wheel"]}


# ----------------------------------------------------------------------------------------------------------------
# CLI

def _load_spec_files(paths):
    specs = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        specs.extend(data.get("specs", []) if isinstance(data, dict) else data)
    return specs


def _parser():
    parser = argparse.ArgumentParser(prog="python -m house_sim", description="Splits and the hidden test set "
                                     "(docs/SPLITS.md). Nothing here touches the network.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--init-split-key", action="store_true", help="create %%USERPROFILE%%/.house_ai/split_key")
    action.add_argument("--build-test-manifest", action="store_true", help="verify hidden candidates, write the private "
                        "manifest and the public commitment")
    action.add_argument("--verify-commitment", action="store_true", help="check reports/test_commitment.json")
    action.add_argument("--split-info", action="store_true", help="show split ranges and the state of the key, "
                        "manifest, commitment and packaging (never seeds or the key)")
    parser.add_argument("--force", action="store_true", help="--init-split-key: replace an existing key")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="hidden seeds per entry (default 10)")
    parser.add_argument("--workers", type=int, help="processes (default: CPUs - 1)")
    parser.add_argument("--only", nargs="+", help="catalog ids, sections, authored file names or domains")
    parser.add_argument("--file", nargs="+", help="take specs from these authored JSON files instead of the catalog")
    parser.add_argument("--specs", choices=("verified", "candidates"), default="verified",
                        help="verified (default): only specs with passing verification; candidates: every valid "
                        "authored spec (each hidden seed is still verified individually)")
    parser.add_argument("--verify", action="store_true", help="verify the selected specs on the dev seeds now instead "
                        "of trusting reports/verification.jsonl (which goes stale whenever code changes)")
    parser.add_argument("--perception", choices=("realistic",), help="verify every hidden candidate in realistic mode "
                        "(default: each spec's own mode)")
    parser.add_argument("--max-candidates", type=int, help="candidates per entry (default 3k+10)")
    parser.add_argument("--merge", action="store_true", help="keep existing manifest entries that were not rebuilt")
    parser.add_argument("--manifest", help="private manifest path (default %%USERPROFILE%%/.house_ai/test_manifest.json)")
    parser.add_argument("--commitment", help="public commitment path (default reports/test_commitment.json, or env "
                        "HOUSE_AI_COMMITMENT_FILE)")
    parser.add_argument("--json", action="store_true", help="--split-info: print JSON")
    return parser


def _select_specs(args, emit):
    if args.file:
        specs = _load_spec_files(args.file)
        if args.only:
            specs = [s for s in specs if s.get("catalog_id") in set(args.only)]
        items = specs
    else:
        items = candidate_records(only=args.only)
    if args.specs == "candidates":
        return [i["spec"] if isinstance(i.get("spec"), dict) else i for i in items]
    kept, rejected = filter_verified(items, verify=args.verify, workers=args.workers)
    for item, why in rejected:
        cid = item.get("catalog_id")
        reason = why if isinstance(why, str) else "failed verification on the dev seeds"
        emit("  not verified, skipped: {} ({})".format(cid, reason))
    return [i["spec"] if isinstance(i.get("spec"), dict) else i for i, _ in kept]


def _info():
    from ..audit import fingerprint
    info = {"ranges": {"dev": list(DEV_SEEDS), "dev_extra": list(DEV_EXTRA), "train": [TRAIN_BASE, PRIVATE_BASE],
                       "private": [PRIVATE_BASE, PRIVATE_END]}}
    source = key_source()
    try:
        key = load_key(required=False)
        info["key"] = {"present": key is not None, "source": source, "path": str(key_path()),
                       "digest_prefix": key_digest(key)[:16] if key else None}
    except ValueError as exc:
        key = None
        info["key"] = {"present": False, "source": source, "error": str(exc)}
    try:
        doc = _manifest_doc(required=False)
    except (ValueError, OSError) as exc:
        doc, info["manifest"] = None, {"present": True, "error": str(exc)}
    if doc is not None:
        current = fingerprint()
        entries = doc["entries"]
        info["manifest"] = {"present": True, "path": str(manifest_path()), "entries": len(entries),
                            "complete": sum(1 for e in entries.values() if e.get("complete", True)),
                            "seeds_total": sum(len(e.get("seeds", [])) for e in entries.values()),
                            "public_phrasings": bool(doc.get("public_phrasings")),
                            "built_with_this_key": bool(key) and doc.get("key_digest") == key_digest(key),
                            "unblinded_entries": sum(1 for e in entries.values() if not e.get("nonce")),
                            "entries_built_with_other_code": sum(1 for e in entries.values()
                                                                 if e.get("fingerprint") != current)}
    elif "manifest" not in info:
        info["manifest"] = {"present": False, "path": str(manifest_path())}
    info["private_phrasings"] = {"present": phrasings_path().is_file(), "path": str(phrasings_path())}
    public = load_commitment()
    if public is None:
        info["commitment"] = {"present": False, "path": str(commitment_file())}
    else:
        info["commitment"] = {"present": True, "path": str(commitment_file()), "root": public.get("root"),
                              "entries": len(public.get("per_entry", {})), "consistent": commitment_consistent(public),
                              "blinded": bool(public.get("blinded"))}
        if doc is not None:
            info["commitment"]["matches_manifest"] = verify_commitment(doc, key, public)
    audit = packaging_audit()
    info["packaging_private_files"] = {"sdist": audit["sdist"], "wheel": audit["wheel"]}
    info["packaging_private_reasons"] = audit["reasons"]     # why each file is private; never a seed or the key
    return info


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)

    def emit(*parts):
        print(*parts, flush=True)

    try:
        if args.init_split_key:
            path = init_key(force=args.force)
            emit("Created a new split key at {} (owner-only; never commit, share or package it).".format(path))
            emit("Key digest (public, used in commitments): {}".format(key_digest(load_key_file(path))))
            if key_source() == "env":
                emit("Note: {} is set and takes precedence over the file.".format(KEY_ENV))
            return 0
        if args.split_info:
            info = _info()
            if args.json:
                emit(json.dumps(info, indent=2, sort_keys=True))
            else:
                for section, value in info.items():
                    emit("{}: {}".format(section, json.dumps(value, sort_keys=True)))
            leaks = info["packaging_private_files"]
            return 1 if leaks["sdist"] or leaks["wheel"] else 0
        if args.verify_commitment:
            return _cli_verify_commitment(args, emit)
        return _cli_build(args, emit)
    except (SplitKeyMissing, ManifestMissing, FileExistsError, ValueError) as exc:
        emit("error: {}".format(exc))
        return 2


def load_key_file(path):
    """Read a key file (hex) without consulting the environment."""
    return _parse_key(Path(path).read_text(encoding="utf-8", errors="replace"), str(path))


def _cli_verify_commitment(args, emit):
    public = load_commitment(args.commitment)
    target = args.commitment or commitment_file()
    if public is None:
        emit("FAIL: no commitment at {}".format(target))
        return 1
    if not commitment_consistent(public):
        emit("FAIL: {} is internally inconsistent (root does not match its per-entry hashes)".format(target))
        return 1
    doc = load_manifest(required=False, path=args.manifest, with_meta=True)
    if doc is None:
        emit("OK (internal consistency only): no private manifest on this machine; root {}".format(public.get("root")))
        return 0
    key = load_key(required=False)
    if key is None:
        emit("Note: no split key here; checking against the commitment's own key digest.")
    elif public.get("key_digest") != key_digest(key):
        emit("FAIL: the commitment was made with a different split key")
        return 1
    if verify_commitment(doc, key, public):
        emit("OK: commitment matches the private manifest ({} entries, root {})".format(
            len(public.get("per_entry", {})), public.get("root")))
        unblinded = sorted(cid for cid, e in doc["entries"].items() if not e.get("nonce"))
        if unblinded:
            emit("Warning: {} entr{} carry no blinding nonce (built before nonces existed), so the published hash can "
                 "be brute-forced back to the hidden seeds; rebuild with --build-test-manifest and republish: {}".format(
                     len(unblinded), "y" if len(unblinded) == 1 else "ies", ", ".join(unblinded[:20])))
        return 0
    expected = _commitment_for_digest(doc["entries"], public.get("key_digest", ""))["per_entry"]
    published = public.get("per_entry", {})
    bad = sorted(c for c in set(expected) | set(published) if expected.get(c) != published.get(c))
    emit("FAIL: commitment does not match the private manifest; differing entries: {}".format(", ".join(bad[:20]) or "root"))
    return 1


def _cli_build(args, emit):
    key = load_key(required=True)
    if args.k < 1:
        emit("error: --k must be at least 1")
        return 2
    specs = _select_specs(args, emit)
    if not specs:
        emit("No specs selected. Verification evidence may be stale: add --verify (verify on the dev seeds now) or "
             "--specs candidates.")
        return 1
    emit("Building hidden seeds for {} spec(s), k={} ...".format(len(specs), args.k))

    def progress(i, total, cid, entry):
        covered = len({v for v in entry["variants"] if v is not None})
        emit("[{}/{}] {}: {}/{} seeds, variants {}/{}, {} skipped{}".format(
            i, total, cid, len(entry["seeds"]), entry["k"], covered, entry["n_variants"],
            sum(1 for s in entry["skipped"] if s["reason"] == "verification_failed"),
            "" if entry["complete"] else " INCOMPLETE: " + entry.get("error", "")))
    manifest = build_test_manifest(specs, k=args.k, workers=args.workers, key=key, perception=args.perception,
                                   max_candidates=args.max_candidates, progress=progress)
    public = save_manifest(manifest, key, path=args.manifest, commitment_path=args.commitment, merge=args.merge)
    emit("Private manifest: {}".format(args.manifest or manifest_path()))
    emit("Public commitment: {} (root {})".format(args.commitment or commitment_file(), public["root"]))
    if public["public_phrasings"]:
        emit("Note: no private paraphrase pool for some entries ({}); they use the spec's public phrasings "
             "(recorded as public_phrasings).".format(phrasings_path()))
    weak = weak_entries(manifest)
    if weak:
        emit("Warning: {} entr{} without variants got a single hidden seed (--k {}); use --k 2 or more for a less noisy "
             "test score: {}".format(len(weak), "y" if len(weak) == 1 else "ies", args.k, ", ".join(weak[:20])))
    incomplete = [cid for cid, e in manifest.items() if not e["complete"]]
    if incomplete:
        emit("{} entr{} incomplete (excluded from the test split): {}".format(
            len(incomplete), "y" if len(incomplete) == 1 else "ies", ", ".join(incomplete[:20])))
        return 1
    return 0

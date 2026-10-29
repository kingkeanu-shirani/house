"""The private verification stages' secret, seeds and store (docs/design/VERIFY_HARDENING.md 12.1-12.3; WP5).

    salt(key=None)                  HMAC-SHA256(split_key, "house_ai.verify.private.v1"), or None without a key
    available(key=None)             whether the private stages can run here (a split key is present)
    heldout(spec, key=None, ...)    the private held-out seeds H_priv(spec) (12.1), [] without a key
    pool(key=None, n=2000)          the private audit pool (12.1)
    sample_pool(spec, key, n=200)   the 200 pool houses one spec's private audits use (12.2)
    rng(salt, catalog_id, seed, family)   the P-families' RNG: random.Random(HMAC(salt, "<cid>:<seed>:<id>"))
    write_details(catalog_id, rows, root=None)   append the instance details to the private store (12.3)
    details_digest(rows)            the digest the public evidence record keeps instead of the details
    NOT_RUN                         the gate's line without a key: "not run (no split key)"

No new secret is created: the salt derives from the existing hidden-test split key (HOUSE_AI_SPLIT_KEY or
%USERPROFILE%/.house_ai/split_key, read by house_sim.eval.splits.load_key). Without it every function here returns
"unavailable" (None, [] or False) and the verdict stops at PENDING_GATE. Creating the key is outside this package: by
the owner's standing rule whoever creates it stops and asks the owner to back up %USERPROFILE%/.house_ai first.
Nothing in this module creates that folder: the store is written only into an existing private folder (the one that
holds the key), or into a root the caller names (tests use a temporary folder and throwaway keys), and never inside
the repository.

Private held-out seeds: PRIVATE_BASE + HMAC(salt, "heldout:<catalog_id>:<spec_hash>:<i>") mod PRIVATE_SPAN for i = 0,
1, ..., skipping the spec's hidden test candidates and duplicates, until 10 seeds with every variant on at least 2
(cap 16), in the hidden range [PRIVATE_BASE, PRIVATE_END) of eval/splits.py. The audit pool: PRIVATE_BASE +
HMAC(salt, "pool:<i>") mod PRIVATE_SPAN, test candidates skipped.
"""
import hashlib
import hmac
import json
import os
import random
import re
from pathlib import Path

from .seeds import HELDOUT_CAP, HELDOUT_N, select_heldout
from .spec import expand

SALT_LABEL = b"house_ai.verify.private.v1"
POOL_N = 2000
POOL_SAMPLE_N = 200
TEST_CANDIDATES_SKIPPED = 64        # the hidden test candidates a spec's manifest may walk (splits: 3k + 10, k = 10)
STREAM_LIMIT = 4096
STORE_DIR = "private_verify"
NOT_RUN = "not run (no split key)"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _splits():
    from ..eval import splits
    return splits


def _key(key):
    """The split key as bytes: the argument (bytes or hex), else splits.load_key(required=False) (None without)."""
    sp = _splits()
    if key is None:
        return sp.load_key(required=False)
    return sp._coerce_key(key)


def salt(key=None):
    """HMAC-SHA256(split_key, "house_ai.verify.private.v1") (32 bytes), or None when no key is present."""
    raw = _key(key)
    if raw is None:
        return None
    return hmac.new(raw, SALT_LABEL, hashlib.sha256).digest()


def available(key=None):
    """Whether the private stages can run here (a split key is present)."""
    return salt(key) is not None


def _mac(value, text):
    return hmac.new(value, text.encode("utf-8"), hashlib.sha256).digest()


def _private_seed(value, text):
    sp = _splits()
    return sp.PRIVATE_BASE + int.from_bytes(_mac(value, text)[:8], "big") % sp.PRIVATE_SPAN


def _test_candidates(catalog_id, key):
    sp = _splits()
    return set(sp.test_candidates(catalog_id, TEST_CANDIDATES_SKIPPED, _key(key)))


def heldout_stream(spec, key=None, spec_hash=None):
    """Every derived private held-out seed of a spec in order (duplicates and hidden test candidates skipped); empty
    without a key."""
    value = salt(key)
    if value is None:
        return
    full = expand(spec)
    cid = full.get("catalog_id")
    if spec_hash is None:
        from .verify import spec_hash as digest_of
        spec_hash = digest_of(spec)
    skip = _test_candidates(cid, key)
    seen = set()
    for i in range(STREAM_LIMIT):
        seed = _private_seed(value, "heldout:{}:{}:{}".format(cid, spec_hash, i))
        if seed in seen or seed in skip:
            continue
        seen.add(seed)
        yield seed


def heldout(spec, key=None, spec_hash=None, n=HELDOUT_N, cap=HELDOUT_CAP, skip=()):
    """H_priv(spec) (12.1): 10 private held-out seeds with every variant on at least 2 (cap 16); [] without a key."""
    if not available(key):
        return []
    full = expand(spec)
    return select_heldout(heldout_stream(spec, key, spec_hash), full.get("catalog_id"),
                          len(full.get("variants") or []), n=n, cap=cap, skip=skip)


def pool(key=None, n=POOL_N):
    """The private audit pool (12.1): n distinct seeds PRIVATE_BASE + HMAC(salt, "pool:<i>") mod PRIVATE_SPAN; []
    without a key. (Hidden test candidates are per spec: sample_pool skips the spec's.)"""
    value = salt(key)
    if value is None:
        return []
    out, seen = [], set()
    i = 0
    while len(out) < n and i < n * 4:
        seed = _private_seed(value, "pool:{}".format(i))
        i += 1
        if seed not in seen:
            seen.add(seed)
            out.append(seed)
    return out


def sample_pool(spec, key=None, n=POOL_SAMPLE_N):
    """The n pool houses one spec's private audits use (12.2): a salted draw from pool(), the spec's hidden test
    candidates left out; [] without a key."""
    value = salt(key)
    if value is None:
        return []
    cid = expand(spec).get("catalog_id")
    skip = _test_candidates(cid, key)
    seeds = [s for s in pool(key) if s not in skip]
    draw = random.Random(_mac(value, "pool_sample:{}".format(cid)))
    return sorted(draw.sample(seeds, min(n, len(seeds))))


def rng(value, catalog_id, seed, family):
    """The RNG of a private family (12.2): random.Random(HMAC(salt, "<catalog_id>:<seed>:<id>"))."""
    if value is None:
        raise ValueError("the private families need the salt (a split key)")
    return random.Random(_mac(value, "{}:{}:{}".format(catalog_id, seed, family)))


def store_root(root=None):
    """The private store's folder: root when given, else %USERPROFILE%/.house_ai/private_verify (never created by
    this module unless the private folder already exists: the folder that holds the split key)."""
    if root is not None:
        return Path(root)
    return _splits().private_dir() / STORE_DIR


def _inside_repo(path):
    repo = Path(__file__).resolve().parents[2]
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return resolved == repo or repo in resolved.parents


def store_path(catalog_id, root=None):
    if not isinstance(catalog_id, str) or not _SAFE_ID.match(catalog_id):
        raise ValueError("catalog id {!r} is not a file name".format(catalog_id))
    return store_root(root) / "{}.jsonl".format(catalog_id)


def write_details(catalog_id, rows, root=None):
    """Append the private stages' instance details (seeds, destinations, tau values, plans; 12.3) to
    <store>/<catalog_id>.jsonl and return the path. Refuses a store inside the repository, and the default store when
    the private folder does not exist yet (this module never creates %USERPROFILE%/.house_ai)."""
    path = store_path(catalog_id, root)
    if _inside_repo(path):
        raise ValueError("refusing to write private verification details inside the repository ({})".format(path))
    if root is None and not _splits().private_dir().is_dir():
        raise FileNotFoundError("no private folder ({}): the private store lives next to the split key".format(
            _splits().private_dir()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return path


def details_digest(rows):
    """SHA-256 over the canonical JSON of the details: what the public record keeps instead of them (12.3)."""
    text = json.dumps(list(rows), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def public_summary(results):
    """The gate's line per family (12.3): family, outcome class and label class only -- {"P3": "1 of 4 runs pass
    (conservation)"} from [{"family", "outcome", "label_class"}]; seeds, destinations, tau values and plans never."""
    summary = {}
    for family in sorted({r.get("family") for r in results if r.get("family")}):
        rows = [r for r in results if r.get("family") == family]
        met = sum(1 for r in rows if r.get("outcome") in ("fails", "blocked", "passes_ok"))
        bad = [r for r in rows if r.get("outcome") not in ("fails", "blocked", "passes_ok")]
        classes = sorted({str(r.get("label_class")) for r in bad if r.get("label_class")})
        text = "{}/{} met".format(met, len(rows))
        if classes:
            text += " ({})".format(", ".join(classes))
        summary[family] = text
    return summary

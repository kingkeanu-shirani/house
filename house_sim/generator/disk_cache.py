"""Generated houses cached on disk across specs and processes (docs/design/VERIFY_HARDENING.md 9.7, 22.5 (7)).

    from house_sim.generator import disk_cache
    config = disk_cache.house(100000, {"gen": 2})   # equal to generate_house(100000, gen=2); built once per fingerprint

The 200 audit builds of every audited spec (seeds 100000 + i) use the same houses, so a house is generated once per
generator fingerprint and read back afterwards (about 6 ms instead of 0.3-2.6 s). The result is always a fresh object
the caller may change.

Where: HOUSE_SIM_HOUSE_CACHE names the cache directory ("off": no disk cache, every call generates); otherwise
reports/cache/houses/ in the project (git-ignored), or root= per call. One subdirectory per fingerprint, one file per
(seed, house arguments): <root>/<fingerprint[:16]>/<seed>_<sha256(key)[:16]>.json.gz holding {"format", "fingerprint",
"key", "config"}. Files are written to a temporary name and renamed, so a concurrent reader never sees half a file;
two processes building the same house write the same bytes.

Fingerprint: sha256 over the cache format, the Python version (major.minor), the domains load_domains imports
(HOUSE_SIM_DOMAINS changes them) and every source and data file of house_sim/ except eval/ and legacy_v1/ (path and
content): the generator, the kernel, every domain's furnishers and type data, and the task modules domains import. A
change to any of them starts a new directory; prune() removes old ones.

Correctness audit (22.5 (7)): the first cache hit per process and fingerprint is compared with a fresh build of that
house (verify="once", the default; "always" compares every hit, "never" none); a difference raises CacheError, so a
stale or planted file is a harness error, never a wrong verdict. JSON rather than pickle: a planted file can at
worst change a house, which the check exposes, never run code. A house that does not survive a JSON round trip
exactly (a tuple, a non-string key) is returned fresh and not cached.
"""
import gzip
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

FORMAT = 1
ENV_DIR = "HOUSE_SIM_HOUSE_CACHE"
OFF = ("off", "none", "0")
PACKAGE = Path(__file__).resolve().parents[1]                # house_sim/
PROJECT = PACKAGE.parent
DEFAULT_DIR = PROJECT / "reports" / "cache" / "houses"
SOURCE_SUFFIXES = (".py", ".json", ".jsonl", ".csv", ".tsv", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg")
SKIP_DIRS = ("__pycache__", "eval", "legacy_v1")             # top-level house_sim/ folders generation never reads
VERIFY_MODES = ("once", "always", "never")
GZIP_LEVEL = 1
OUTPUTS = ("registry", "report", "trace")                    # generate_house arguments that are objects, not inputs

_FINGERPRINT = {}
_VERIFIED = set()
STATS = {"hits": 0, "misses": 0, "writes": 0, "verified": 0, "uncacheable": 0, "corrupt": 0}


class CacheError(RuntimeError):
    """A cached house differs from a fresh build of the same seed and arguments (stale or planted cache file)."""


def _domains():
    from ..kernel.plugins import domain_modules
    return list(domain_modules())


def source_files(package=None):
    """The files the fingerprint covers: [(posix path relative to house_sim/, Path)], sorted."""
    base = Path(package) if package is not None else PACKAGE
    out = []
    for path in base.rglob("*"):
        rel = path.relative_to(base)
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if rel.parts[0] in SKIP_DIRS or "__pycache__" in rel.parts:
            continue
        out.append((rel.as_posix(), path))
    out.sort()
    return out


def fingerprint(refresh=False, package=None):
    """The generator fingerprint (hex sha256), computed once per process (refresh=True recomputes)."""
    key = str(package or PACKAGE)
    if refresh or key not in _FINGERPRINT:
        digest = hashlib.sha256()
        head = {"format": FORMAT, "python": list(sys.version_info[:2]), "domains": _domains()}
        digest.update(json.dumps(head, sort_keys=True).encode("utf-8"))
        for rel, path in source_files(package):
            digest.update(b"\0" + rel.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        _FINGERPRINT[key] = digest.hexdigest()
    return _FINGERPRINT[key]


def cache_root(root=None):
    """The cache directory (root, else HOUSE_SIM_HOUSE_CACHE, else reports/cache/houses/), or None when the disk
    cache is off."""
    if root is not None:
        return Path(root)
    env = os.environ.get(ENV_DIR, "").strip()
    if env.lower() in OFF:
        return None
    return Path(env) if env else DEFAULT_DIR


def house_key(seed, kwargs=None):
    """The canonical key of a house: generate_house's own (seed plus arguments, sets and tuples sorted)."""
    kwargs = dict(kwargs or {})
    bad = sorted(k for k in kwargs if k in OUTPUTS)
    if bad:
        raise TypeError("disk_cache.house: {} cannot be cached (not a house argument)".format(", ".join(bad)))
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("disk_cache.house: seed must be an int")
    norm = {k: (sorted(v) if isinstance(v, (set, tuple)) else v) for k, v in kwargs.items()}
    try:
        return json.dumps(dict(norm, seed=seed), sort_keys=True)
    except (TypeError, ValueError):
        raise TypeError("disk_cache.house: house arguments must be JSON values")


def entry_path(seed, kwargs=None, root=None):
    """The cache file of a house (None when the disk cache is off)."""
    base = cache_root(root)
    if base is None:
        return None
    key = house_key(seed, kwargs)
    name = "{}_{}.json.gz".format(seed, hashlib.sha256(key.encode("utf-8")).hexdigest()[:16])
    return base / fingerprint()[:16] / name


def _generate(seed, kwargs):
    from . import _generate as generate
    return generate(seed, **dict(kwargs or {}))


def _exact(a, b):
    """Equal, types and dict key order included (a JSON round trip turns tuples into lists, which == would hide, and
    the order entities and sections are iterated in is part of a house's behaviour)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(_exact(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_exact(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and a != a:
        return b != b
    return a == b


def _encode(config):
    """JSON text of a config in its own key order (never sorted: the order is part of the house)."""
    return json.dumps(config, separators=(",", ":"))


def _read(path, key, fp):
    """The cached config of path, or None when it is missing, unreadable or for another key / fingerprint."""
    try:
        with open(path, "rb") as handle:
            payload = json.loads(gzip.decompress(handle.read()).decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, EOFError):
        STATS["corrupt"] += 1
        return None
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("key") != key or \
            payload.get("fingerprint") != fp or not isinstance(payload.get("config"), dict):
        STATS["corrupt"] += 1
        return None
    return payload["config"]


def _write(path, key, fp, config):
    data = json.dumps({"format": FORMAT, "fingerprint": fp, "key": key, "config": config},
                      separators=(",", ":")).encode("utf-8")
    blob = gzip.compress(data, compresslevel=GZIP_LEVEL, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(blob)
        os.replace(tmp, str(path))
        STATS["writes"] += 1
    except OSError:
        # a concurrent writer holds the target (Windows): it writes the same bytes
        try:
            os.remove(tmp)
        except OSError:
            pass


def house(seed, kwargs=None, root=None, verify="once"):
    """The generated house of (seed, house arguments) -- generate_house(seed, **kwargs) -- read from the disk cache
    when an earlier call built it under the current fingerprint, else generated and stored. Always a fresh object.
    verify: "once" (default) compares the first cache hit per process and fingerprint with a fresh build, "always"
    every hit, "never" none; a difference raises CacheError."""
    if verify not in VERIFY_MODES:
        raise ValueError("disk_cache.house: verify must be one of " + ", ".join(VERIFY_MODES))
    key = house_key(seed, kwargs)
    path = entry_path(seed, kwargs, root)
    if path is None:
        STATS["misses"] += 1
        return _generate(seed, kwargs)
    fp = fingerprint()
    cached = _read(path, key, fp)
    if cached is not None:
        STATS["hits"] += 1
        scope = str(path.parent)
        if verify == "always" or (verify == "once" and scope not in _VERIFIED):
            if not _exact(_generate(seed, kwargs), cached):
                raise CacheError("cached house {} differs from a fresh build (stale or planted cache file; delete {})"
                                 .format(path, path.parent))
            _VERIFIED.add(scope)
            STATS["verified"] += 1
        return cached
    STATS["misses"] += 1
    config = _generate(seed, kwargs)
    text = _encode(config)
    back = json.loads(text)
    if not _exact(back, config):
        STATS["uncacheable"] += 1
        return config
    _write(path, key, fp, config)
    return back


def check(seed, kwargs=None, root=None):
    """Compare the cached house of (seed, kwargs) with a fresh build: True when equal, False when it differs, None when
    nothing is cached for it (the cache's correctness audit on one seed)."""
    key = house_key(seed, kwargs)
    path = entry_path(seed, kwargs, root)
    if path is None:
        return None
    cached = _read(path, key, fingerprint())
    if cached is None:
        return None
    return _exact(_generate(seed, kwargs), cached)


def prune(keep=2, root=None):
    """Remove the cache directories of other fingerprints, keeping the current one and the keep - 1 most recently
    changed others. Returns the removed directory names."""
    base = cache_root(root)
    if base is None or not base.is_dir():
        return []
    current = fingerprint()[:16]
    others = sorted((p for p in base.iterdir() if p.is_dir() and p.name != current),
                    key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    removed = []
    for directory in others[max(0, int(keep) - 1):]:
        for item in directory.iterdir():
            if item.is_file() and (item.name.endswith(".json.gz") or item.name.endswith(".tmp")):
                item.unlink()
        try:
            directory.rmdir()
            removed.append(directory.name)
        except OSError:
            pass                      # something else lives there: leave it
    return removed

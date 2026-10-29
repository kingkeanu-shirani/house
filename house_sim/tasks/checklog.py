"""The append-only check log (docs/design/VERIFY_HARDENING.md 12.4, 10.7, 22.6, 22.8; WP5).

    append(record, root=None, domain=None, level=None, cpu_s=None)   one line per --check / --verify of a spec
    entries(catalog_id=None, root=None, domain=None)                 the logged lines (oldest first)
    sessions(catalog_id, root=None)                                  every session id that checked a catalog id
    heldout_rolls(catalog_id, root=None)                             distinct spec hashes that failed held-out seeds
    stop_loss(catalog_id, root=None)                                 the authoring stop-loss of 22.8
    session_id()                                                     HOUSE_AI_SESSION_ID, else "unknown"
    domain_of(catalog_id)                                            the authored file's domain (tasks/authored)

Each line is {"catalog_id", "spec_hash", "level", "verdict", "gates", "date", "fingerprint", "session_id", "cpu_s"}
(plus "cpu_by_stage" and "failing": the counts the stop-loss compares) in catalog/review/check_log/<domain>.jsonl:
per domain, so authors of different domains never conflict. The session id is what makes reviewer independence
checkable (10.7: a reviewer session that appears in a catalog id's check log may not accept its escapes); cpu_s feeds
the cost calibration (22.6). The folder can be redirected with HOUSE_AI_CHECK_LOG_DIR (tests, sandboxes) or root=.
Only schema-4 verification writes it (the legacy schema 2 / 3 checks keep their behaviour); writing never raises into
the verifier (a read-only checkout records nothing and says so in the record's "check_log").
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "catalog" / "review" / "check_log"
DIR_ENV = "HOUSE_AI_CHECK_LOG_DIR"
SESSION_ENV = "HOUSE_AI_SESSION_ID"
UNKNOWN = "unknown"
FIELDS = ("catalog_id", "spec_hash", "level", "verdict", "gates", "date", "fingerprint", "session_id", "cpu_s")
STOP_CHECKS = 8                 # standard checks without reaching PENDING_GATE / PENDING_REVIEW (22.8)
STOP_STALL = 4                  # consecutive standard checks that did not reduce the failing count (22.8)
PASSING = ("HARDENED", "PENDING_GATE", "PENDING_REVIEW")
_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
_DOMAINS = None


def log_dir(root=None):
    if root is not None:
        return Path(root)
    raw = os.environ.get(DIR_ENV)
    return Path(raw) if raw and raw.strip() else DEFAULT_DIR


def session_id():
    raw = os.environ.get(SESSION_ENV)
    return raw.strip() if raw and raw.strip() else UNKNOWN


def domain_of(catalog_id):
    """The domain of the authored file that holds a catalog id (tasks/authored/<domain>__*.json), else "unknown"."""
    global _DOMAINS
    if _DOMAINS is None:
        table = {}
        folder = ROOT / "tasks" / "authored"
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for spec in data.get("specs", []) if isinstance(data, dict) else data:
                if isinstance(spec, dict) and spec.get("catalog_id"):
                    table.setdefault(spec["catalog_id"], path.name.split("__")[0])
        _DOMAINS = table
    return _DOMAINS.get(catalog_id, UNKNOWN)


def _file(domain, root=None):
    domain = domain if isinstance(domain, str) and _SAFE.match(domain) else UNKNOWN
    return log_dir(root) / "{}.jsonl".format(domain)


def failing_count(record):
    """What the stop-loss compares (22.8): failing gates + unmet battery policies + failing must-pass runs."""
    gates = sum(1 for ok in (record.get("gates") or {}).values() if ok is False)
    battery = ((record.get("battery") or {}).get("summary") or {})
    mustpass = ((record.get("mustpass") or {}).get("summary") or {})
    unmet = int(battery.get("counted", 0) or 0) - int(battery.get("met", 0) or 0)
    failed = int(mustpass.get("applicable", 0) or 0) - int(mustpass.get("passed", 0) or 0)
    return gates + max(0, unmet) + max(0, failed)


def entry_of(record, level=None, cpu_s=None):
    """The check-log line of an evidence record (v2)."""
    return {"catalog_id": record.get("catalog_id"), "spec_hash": record.get("spec_hash"),
            "level": level or record.get("level"), "verdict": record.get("verdict"),
            "gates": dict(record.get("gates") or {}),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fingerprint": record.get("fingerprint"), "session_id": session_id(),
            "cpu_s": round(float(cpu_s if cpu_s is not None else record.get("cpu_s") or 0.0), 3),
            "cpu_by_stage": dict(record.get("cpu_by_stage") or {}), "failing": failing_count(record),
            "heldout_failed": bool((record.get("heldout") or {}).get("failed"))}


def append(record, root=None, domain=None, level=None, cpu_s=None):
    """Append one line for an evidence record; returns (path, entry), or (None, entry) when the log could not be
    written (the reason is in entry["error"])."""
    entry = entry_of(record, level, cpu_s)
    path = _file(domain or domain_of(entry["catalog_id"]), root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        entry["error"] = "{}: {}".format(type(exc).__name__, exc)
        return None, entry
    return path, entry


def entries(catalog_id=None, root=None, domain=None):
    """The logged lines (oldest first), of one catalog id when given; the domain's file, else every file."""
    folder = log_dir(root)
    paths = [_file(domain, root)] if domain else sorted(folder.glob("*.jsonl")) if folder.is_dir() else []
    out = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and (catalog_id is None or row.get("catalog_id") == catalog_id):
                out.append(row)
    out.sort(key=lambda r: str(r.get("date") or ""))
    return out


def sessions(catalog_id, root=None):
    """Every session id that checked a catalog id (a reviewer among them is not independent, 10.7)."""
    return sorted({str(r.get("session_id")) for r in entries(catalog_id, root) if r.get("session_id")})


def heldout_rolls(catalog_id, root=None):
    """The number of distinct spec hashes of a catalog id whose check failed its held-out seeds (12.4: the reviewer
    sees how often the public held-out seeds were re-rolled before acceptance)."""
    return len({r.get("spec_hash") for r in entries(catalog_id, root) if r.get("heldout_failed")})


def stop_loss(catalog_id, root=None, allowance=0):
    """The authoring stop-loss (22.8): {"parked": bool, "why"}: 8 standard checks without reaching PENDING_GATE or
    PENDING_REVIEW, or 4 consecutive standard checks that did not reduce the failing count. allowance: extra checks
    the domain owner granted (recorded in the log as a line with "allow")."""
    rows = [r for r in entries(catalog_id, root) if r.get("level") == "standard"]
    extra = allowance + sum(int(r.get("allow") or 0) for r in entries(catalog_id, root))
    if any(r.get("verdict") in PASSING for r in rows):
        return {"parked": False, "why": None}
    if len(rows) >= STOP_CHECKS + extra:
        return {"parked": True, "why": "{} standard checks without reaching PENDING_GATE".format(len(rows))}
    tail = rows[-(STOP_STALL + 1):]
    if len(tail) == STOP_STALL + 1 and extra == 0:
        counts = [int(r.get("failing") or 0) for r in tail]
        if all(later >= counts[0] for later in counts[1:]):
            return {"parked": True, "why": "{} consecutive standard checks did not reduce the failing count".format(
                STOP_STALL)}
    return {"parked": False, "why": None}

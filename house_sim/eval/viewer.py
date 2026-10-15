"""2D episode replay viewer (docs/VIEWER.md; docs/EVAL_CONTRACTS.md section E).

    export_viewer(source, out_path=None, view="both") -> Path
    main(argv) -> int          python -m house_sim --viewer SOURCE [--out PATH] [--view both|oracle|perceived]
                                                    [--private] [--verify-spec]

The output is ONE self-contained offline HTML file (episode data embedded as JSON, inline CSS and JS, no CDN, no
network access) that opens by double-clicking on Windows.

Sources
  * an env replay file (``HouseEnv.save_replay``, format house-replay-v2, oracle or realistic perception);
  * a harness command log (``results/<run_id>/logs/<catalog_id>__<seed_tag>.json``, house-harness-log-v1);
  * an episodes.jsonl record: ``path/episodes.jsonl`` (one record), ``path/episodes.jsonl#3`` (0-based index),
    ``path/episodes.jsonl#<catalog_id>`` or ``#<catalog_id>__<seed_tag>``, a dict, or inline JSON text.
  Logs and records are re-run in the simulator: from the replay the harness saved next to the log when there is
  one, else rebuilt with ``tasks.runner.build(spec, seed)`` and every command the harness sent re-issued.
  Replays are re-run from their start state with trace=True; when the current code diverges from the recording,
  the true state continues from the recorded per-step changes when the replay has them, and the page says so.

Precomputed in Python (the page only renders)
  * a stable layered floor-plan layout per floor from the connection graph (room rectangles sized from size_m);
  * per-step frames of the robot's current room: furniture footprints, floor items, doors/windows/switches, robot
    position/posture/held items, people, pets, hazards and wet cells ("oracle view": true state); for realistic
    runs also the agent's perceived observation (handles and buckets only, never true entity ids);
  * per-step evaluations (success, failures, milestones, harm flags) and per-step diffs (recorded changes, or the
    re-run's trace).
  Frames and evaluations are JSON merge-patch deltas (RFC 7386: frame k = patch over frame k-1, null deletes a key)
  with short keys, so a 300-step episode stays far below 3 MB.

Test-split safety: a test-split episode (split "test", a "test:" seed tag, a private-range seed or a seed listed in
the private manifest) is refused unless private=True (``--private``); the page is then marked
"PRIVATE - test split" and never shows the raw seed. Every seed is normalized to an int first (normalize_seed: an
int, or a str/float that represents one exactly; anything else is refused), so a seed written as "900000123" or
900000123.0 is checked like 900000123; a replay with a start state but no seed cannot be shown to be public and is
treated as private.

Geometric camera (kernel v3.0): the episode's camera mode is a header badge; in geometric mode the oracle frames add
the robot's camera ("cam": facing, head yaw, field of view, this step's frame count and frame headings, the current
room's surveyed floor cells and coverage from camera_log) and the perceived frames add only what obs.camera told the
agent (facing, head yaw, frame count, coverage): the page draws a facing arrow, the field-of-view wedges and, in the
oracle view, the seen-cells overlay.

Physics and the robot body (kernel v3.0): the header names the physics data version the frames were computed with
(and the one a replay was recorded with, when they differ); oracle entity records add the physics cues ("ph":
sagging, wobbly, in_motion, collapsed_shelves, fell, tipped_over, blown_away, torn) and damage levels ("dm"); frames
add the robot's power state ("pw", when not "on"), battery state ("bs", when not "ok") and detected fault kinds
("ft"; oracle frames also "lf", the latent ones), the perceived frames from obs.robot only; and the oracle view gets
per-step markers ("marks") for the step's physics consequences (phys.* events) and body events (power down and up,
falls, overloads, faults, battery thresholds) from the event log, shown in the step list and on the timeline.

Perceived-only pages (view="perceived") carry a floor plan built only from the observations the agent was given
(perceived_layout): rooms it was in, laid out from their observed size and floor, doorways it saw, and the rooms its
exits named shown as bare ids ("stubs", no label, type, size or floor). The true floor plan is shipped only on pages
that also carry the oracle view.
"""
import argparse
import copy
import html
import json
import math
import re
import sys
from pathlib import Path

FORMAT = "house-viewer-v1"
TEMPLATE_PATH = Path(__file__).with_name("viewer_template.html")
DATA_MARKER = "/*__EPISODE_DATA__*/"
TITLE_MARKER = "__PAGE_TITLE__"
DATA_RE = re.compile(r'<script type="application/json" id="episode-data">(.*?)</script>', re.S)
VIEWS = ("both", "oracle", "perceived")
PRIVATE_BASE_FALLBACK = 900000000          # splits.PRIVATE_BASE when splits.py is unavailable
DEV_SEEDS_FALLBACK = (0, 1, 2, 41, 97)     # splits.DEV_SEEDS / tasks.verify.SEEDS
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

MAX_DIFFS = 24              # diff rows kept per step (most relevant first)
MAX_ROOM_ENTITIES = 400     # entities kept per room frame (direct children first, then nested)
MSG_CHARS = 600
VALUE_CHARS = 60
CHANNEL_CHARS = 240
CHANNELS = ("sounds", "smells", "messages", "last_answer", "pending", "weather_now", "illuminance_lux")
WIN_CATS = {"window", "window_sill", "curtain", "curtains", "blind", "blinds", "shade", "shutter"}
FLOOR_KINDS = {"floor", "floor_surface"}
RUG_KINDS = {"rug", "mat", "drain", "feeding_mat", "doormat", "floor_drain"}
FURN_KINDS = {"furniture", "seat", "bed", "counter", "table", "appliance", "shower", "toilet", "bathtub", "hvac",
              "cabinet", "desk", "dresser", "bookshelf", "closet", "wardrobe", "sofa", "piano"}
CEIL_MIN_M = 1.95
STATE_WORDS = ("shattered", "smoking", "on_fire", "burning", "overflowing", "leaking", "flooded", "steaming",
               "sparking", "running")
EXTRA_FIELDS = ("liquid", "liquid_l", "fill_level", "count", "settings", "owner", "private", "off_limits",
                "floor_hazard", "hazards", "mass_kg", "program", "lit", "covered", "cover_open", "plugged_in")
DRIFT_FIELDS = {"temperature_c": 1.0, "internal_temp_c": 1.0, "core_temp_c": 1.0, "wetness": 0.05,
                "freshness": 0.05, "ripeness": 0.05, "moisture": 0.05}
KEY_FIELDS = {"location", "relation", "pos", "spos", "shelf", "open", "on", "locked", "broken", "held_grip",
              "shattered", "on_fire", "smoking", "overflowing", "leaking", "soil", "settings", "appliance",
              "liquid", "liquid_l", "fill_level", "covered", "plugged_in", "revealed", "contents", "rot"}
DROP_TOP = {"action_log", "event_log", "people_log", "logs", "steps", "time_s"}
DROP_PATHS = ("robot.battery", "stats.energy", "stats.noise", "facts.last_action", "facts.action_times",
              # robot body bookkeeping (kernel v3.0; TIER1_EMBODIMENT 8.4): energy counters, pack temperature, the
              # lowest gauge, first-time log and the settlement caches (power state, faults and falls stay)
              "robot.energy_wh_used", "robot.energy_wh_charged", "robot.battery_temp_c", "robot.battery_min_pct",
              "robot.body_log", "robot._idle_s", "robot._activity", "robot._held_kg")
# physics consequence cues shown in oracle entity panels ("ph"; TIER1_PHYSICS 10.4) and damage levels ("dm")
PHYS_CUES = ("sagging", "wobbly", "in_motion", "collapsed_shelves", "fell", "tipped_over", "blown_away", "torn")
# event-log kinds marked on the oracle timeline: every phys.* consequence plus these body events (TIER1_EMBODIMENT 8.4)
BODY_MARKS = ("power_down", "power_up", "robot_fell", "overload", "fault_onset", "fault_detected", "fault_logged",
              "fault_cleared", "battery_low", "battery_reserve")
MAX_MARKS = 12              # markers kept per step (a shelf cascade can emit dozens of phys.* events)


class ViewerError(ValueError):
    """The source cannot be turned into a viewer page."""


class SplitRefused(ViewerError):
    """A test-split episode was given without the explicit private flag."""


# ----------------------------------------------------------------------------------------------------------------
# JSON merge-patch deltas (RFC 7386); shared with the page's decoder
# ----------------------------------------------------------------------------------------------------------------
def make_patch(prev, cur):
    """Patch that turns dict `prev` into dict `cur` (neither may hold None values; lists are atomic)."""
    patch = {}
    for key, value in cur.items():
        if key not in prev:
            patch[key] = value
        elif value is prev[key]:
            continue                      # shared subtree (e.g. a room the robot is not in): unchanged
        elif isinstance(value, dict) and isinstance(prev[key], dict):
            sub = make_patch(prev[key], value)
            if sub:
                patch[key] = sub
        elif value != prev[key] or type(value) is not type(prev[key]):
            patch[key] = value
    for key in prev:
        if key not in cur:
            patch[key] = None
    return patch


def apply_patch(target, patch):
    """Apply a merge patch without mutating `target` (unchanged subtrees are shared)."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = apply_patch(out.get(key), value)
    return out


def decode_deltas(items):
    """[full_0, patch_1, ...] -> [full_0, full_1, ...] (how the page decodes frames and evaluations)."""
    full, prev = [], None
    for index, item in enumerate(items or []):
        prev = copy.deepcopy(item) if index == 0 else apply_patch(prev, item)
        full.append(prev)
    return full


class _DeltaStream:
    def __init__(self):
        self.items, self.prev = [], None

    def add(self, frame, clean=True):
        if clean:
            frame = _clean(frame)
        self.items.append(frame if self.prev is None else make_patch(self.prev, frame))
        self.prev = frame


def _clean(value):
    """Drop None values from dicts and non-finite floats everywhere (frames must be valid merge-patch targets)."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if item is None or (isinstance(item, float) and not math.isfinite(item)):
                continue
            out[str(key)] = _clean(item)
        return out
    if isinstance(value, (list, tuple)):
        return [None if isinstance(v, float) and not math.isfinite(v) else _clean(v) for v in value]
    return value


def _num(value, digits=2):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        out = round(value, digits)
        return int(out) if out == int(out) and abs(out) < 1e15 else out
    return value


def _round_any(value, digits=2):
    if isinstance(value, float):
        return _num(value, digits)
    if isinstance(value, dict):
        return {k: _round_any(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_any(v, digits) for v in value]
    return value


def _short(value, limit=VALUE_CHARS):
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(_round_any(value), sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(value)
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


# ----------------------------------------------------------------------------------------------------------------
# optional collaborators (splits.py is built by another component; degrade gracefully when absent)
# ----------------------------------------------------------------------------------------------------------------
def _splits():
    try:
        from . import splits
    except ImportError:
        return None
    return splits


def _dev_seeds():
    splits = _splits()
    seeds = getattr(splits, "DEV_SEEDS", None) if splits is not None else None
    return tuple(seeds) if seeds else DEV_SEEDS_FALLBACK


INT_TEXT_RE = re.compile(r"[+-]?[0-9]+")


def normalize_seed(seed):
    """An episode seed as an int, or None when there is none. Accepts an int, or a str/float that represents an
    integer exactly ("900000123", " 900000123 ", 900000123.0, "900000123.0"); raises ViewerError for anything else
    (a bool, a fractional or non-finite number, other text, a list or object). Every test-split check and every
    seed the page shows go through this, so the type a seed was written with can never bypass the guard."""
    if seed is None:
        return None
    if isinstance(seed, bool):
        raise ViewerError("unusable episode seed {!r} (a boolean is not a seed)".format(seed))
    if isinstance(seed, int):
        return int(seed)
    if isinstance(seed, float):
        if math.isfinite(seed) and seed.is_integer():
            return int(seed)
        raise ViewerError("unusable episode seed {!r} (not an integer)".format(seed))
    if isinstance(seed, str):
        text = seed.strip()
        value = None
        try:
            if INT_TEXT_RE.fullmatch(text):
                return int(text)
            value = float(text)
        except (ValueError, OverflowError):    # not a number, or more digits than int() accepts
            value = None
        if value is not None and math.isfinite(value) and value.is_integer():
            return int(value)
        raise ViewerError("unusable episode seed {!r} (not an integer)".format(seed[:40]))
    raise ViewerError("unusable episode seed of type {} (expected an integer)".format(type(seed).__name__))


def _seed_is_test(seed, catalog_id):
    splits = _splits()
    if splits is not None:
        try:
            if splits.is_private_seed(seed):
                return True
        except Exception:  # noqa: BLE001 - fall back to the documented range below
            pass
        try:   # any manifest entry counts (a seed fixes the house), so this works without a catalog id too
            if splits.is_test_seed(catalog_id or "", seed):
                return True
        except Exception:  # noqa: BLE001 - a private manifest exists but is unreadable: fail closed
            return True
    base = getattr(splits, "PRIVATE_BASE", PRIVATE_BASE_FALLBACK) if splits is not None else PRIVATE_BASE_FALLBACK
    return seed >= base


def is_test_episode(meta, seed=None, catalog_id=None):
    """True when an episode belongs to the hidden test split (never needs the split key). Both `seed` and the
    metadata's own "seed" are normalized (normalize_seed; an unusable seed raises ViewerError) and checked."""
    meta = meta if isinstance(meta, dict) else {}
    if str(meta.get("split") or "").strip().lower() == "test":
        return True
    tag = str(meta.get("seed_tag") or "").strip().lower()
    if tag.startswith("test:") or tag.startswith("test-"):     # "test-..." is the Windows-safe file-name form
        return True
    catalog_id = catalog_id or meta.get("catalog_id")
    for candidate in (seed, meta.get("seed")):
        value = normalize_seed(candidate)
        if value is not None and _seed_is_test(value, catalog_id):
            return True
    return False


def _resolve_test_seed(catalog_id, seed_tag):
    """The hidden seed behind a "test:..." tag (needs the split key and the private manifest; never logged)."""
    splits = _splits()
    if splits is None:
        raise ViewerError("cannot resolve a test-split seed: house_sim/eval/splits.py is not available")
    if hasattr(splits, "resolve_test_tag"):
        try:
            seed = splits.resolve_test_tag(catalog_id, seed_tag)
        except Exception as exc:  # noqa: BLE001
            raise ViewerError("cannot resolve a test-split seed without the split key and private manifest ({})".format(
                type(exc).__name__))
        if seed is None:
            raise ViewerError("test seed for {} ({}) is not in the private manifest".format(catalog_id, seed_tag))
        return int(seed)
    try:
        manifest = splits.load_manifest(required=True)
    except Exception as exc:  # noqa: BLE001
        raise ViewerError("cannot resolve a test-split seed without the private manifest ({})".format(type(exc).__name__))
    entry = manifest.get(catalog_id) or {}
    for seed in entry.get("seeds", []):
        try:
            if splits.seed_tag(catalog_id, seed) == seed_tag:
                return int(seed)
        except Exception:  # noqa: BLE001
            break
    raise ViewerError("test seed for {} ({}) is not in the private manifest".format(catalog_id, seed_tag))


# ----------------------------------------------------------------------------------------------------------------
# sources
# ----------------------------------------------------------------------------------------------------------------
def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ViewerError("{} is not valid JSON ({})".format(path, exc))


def _read_jsonl(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ViewerError("{} line {} is not valid JSON ({})".format(path, number, exc))
    return rows


META_KEYS = ("catalog_id", "split", "seed_tag", "seed", "agent", "privileged", "perception", "camera", "instruction",
             "phrasing",
             "spec_hash", "success", "end_reason", "done_declared", "require_done", "kind", "section", "domain",
             "risk_level", "run_id", "instruction_sha256", "instruction_hmac", "private_digest_scheme")


REFUSAL = ("refusing to render a test-split episode; pass --private (private=True) to export a page marked "
           "'PRIVATE - test split'")


UNKNOWN_SEED_REFUSAL = ("refusing to render a replay that has a start state but records no seed: it cannot be shown to "
                        "be outside the test split; pass --private (private=True) to export a page marked "
                        "'PRIVATE - test split'")


def _guard(meta, seed, catalog_id, private):
    if private is False and is_test_episode(meta, seed, catalog_id):
        raise SplitRefused(REFUSAL)


def _guard_episode(episode, private):
    """The test-split guard on a loaded episode (its normalized seed and metadata). A replay carries no split or seed
    tag, so its seed is the only signal: one with a start state but no seed is refused unless private, and is then
    treated as private (``seed_unknown``)."""
    meta = episode["meta"]
    _guard(meta, episode.get("seed"), meta.get("catalog_id") or (episode.get("task") or {}).get("catalog_id"), private)
    if episode.get("start_state") is not None and episode.get("seed") is None:
        if private is False:
            raise SplitRefused(UNKNOWN_SEED_REFUSAL)
        episode["seed_unknown"] = True
    return episode


def load_source(source, private=None):
    """Normalize a source into an episode dict (see module doc). Does not run the simulator. private=False refuses
    test-split episodes as early as possible (before their command log is even read)."""
    if isinstance(source, dict):
        return _from_data(source, Path.cwd(), "record", None, private)
    text = str(source)
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ViewerError("inline source is not valid JSON ({})".format(exc))
        return _from_data(data, Path.cwd(), "record", None, private)
    path, selector = Path(text), None
    if not path.exists() and "#" in text:
        head, selector = text.rsplit("#", 1)
        path = Path(head)
    if not path.is_file():
        raise ViewerError("no such source file: {}".format(source))
    if path.suffix.lower() == ".jsonl":
        return _from_episodes(path, selector, private)
    return _from_data(_read_json(path), path.parent, path.stem, path, private)


def _from_episodes(path, selector, private=None):
    rows = _read_jsonl(path)
    if not rows:
        raise ViewerError("{} has no episode records".format(path))
    chosen = None
    if selector is None:
        if len(rows) != 1:
            raise ViewerError("{} has {} records; select one with {}#INDEX or {}#CATALOG_ID[__SEED_TAG]".format(
                path, len(rows), path, path))
        chosen = rows[0]
    elif selector.isdigit():
        index = int(selector)
        if index >= len(rows):
            raise ViewerError("{} has only {} records".format(path, len(rows)))
        chosen = rows[index]
    else:
        for row in rows:
            key = "{}__{}".format(row.get("catalog_id"), row.get("seed_tag"))
            if selector in (row.get("catalog_id"), row.get("seed_tag"), key, _safe_name(key)):
                chosen = row
                break
        if chosen is None:
            raise ViewerError("no record matches #{} in {}".format(selector, path))
    return _from_data(chosen, path.parent, "{}__{}".format(chosen.get("catalog_id"), chosen.get("seed_tag")), path, private)


def _command_items(data):
    for key in ("steps", "commands", "turns", "log", "history"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return None


def _from_data(data, base, name, path, private=None):
    if not isinstance(data, dict):
        raise ViewerError("unsupported source: expected a JSON object")
    fmt = str(data.get("format") or "")
    if fmt == "house-replay-v2":
        return _guard_episode(_from_replay(data, name, path), private)
    if fmt.startswith("house-replay"):
        raise ViewerError("unsupported replay format {} (re-record with the current simulator)".format(fmt))
    if fmt == "house-state-v2":
        raise ViewerError("a saved state is not an episode; use env.save_replay(path)")
    items = _command_items(data)
    if items is not None and not (isinstance(data.get("log"), str)):
        return _guard_episode(_from_log(data, items, name, path, None, private), private)
    if isinstance(data.get("log"), str):
        _guard(data, data.get("seed"), data.get("catalog_id"), private)
        return _guard_episode(_from_record(data, base, path, private), private)
    raise ViewerError("unrecognised source (expected a house-replay-v2 file, a harness log or an episode record)")


def _merge_meta(*dicts):
    meta = {}
    for item in dicts:
        if isinstance(item, dict):
            for key in META_KEYS:
                if item.get(key) is not None:
                    meta[key] = item[key]
    if "seed" in meta:
        meta["seed"] = normalize_seed(meta["seed"])     # never keep a raw str/float seed (see normalize_seed)
    return meta


def _from_record(record, base, path, private=None):
    log_ref = Path(record["log"])
    candidates = [log_ref] if log_ref.is_absolute() else [base / log_ref, base.parent / log_ref, Path.cwd() / log_ref,
                                                          base / "logs" / log_ref.name]
    for candidate in candidates:
        if candidate.is_file():
            data = _read_json(candidate)
            items = _command_items(data)
            if items is None:
                raise ViewerError("{} has no command list".format(candidate))
            name = "{}__{}".format(record.get("catalog_id"), record.get("seed_tag"))
            episode = _from_log(data, items, name, candidate, record, private)
            episode["record_path"] = str(path) if path else None
            return episode
    raise ViewerError("command log {} not found (looked next to the episodes file)".format(record["log"]))


def _sibling_record(log_path):
    """For a bare log file, find its episodes.jsonl record (results/<run_id>/episodes.jsonl)."""
    if log_path is None:
        return None
    for folder in (log_path.parent, log_path.parent.parent):
        episodes = folder / "episodes.jsonl"
        if episodes.is_file():
            try:
                rows = _read_jsonl(episodes)
            except ViewerError:
                return None
            for row in rows:
                if isinstance(row.get("log"), str) and Path(row["log"]).name == log_path.name:
                    return row
    return None


def _from_log(data, items, name, path, record, private=None):
    """Harness command log (format house-harness-log-v1, see house_sim/eval/run.py): steps carry the command the
    agent issued, the normalized command actually "sent" to env.step and the "result" it saw (ok/message/category);
    "done" declarations, discarded commands and simulator errors are not environment steps. Simpler logs with
    {"command", "result"} items (or bare command dicts) are accepted too."""
    if record is None:
        record = _sibling_record(path)
    meta = _merge_meta(record, data.get("episode"), data.get("meta"), data)
    records, done, skipped = [], False, 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("sent"), dict):
            command = item["sent"]
        elif isinstance(item.get("command"), dict):
            command = item["command"]
        else:
            command = item if "action" in item else None
        if command is None:
            continue
        raw = item.get("command") if isinstance(item.get("command"), dict) else command
        if item.get("done") or str(raw.get("action", "")).strip().lower() == "done":
            done = True
            continue
        if item.get("discarded") or ("sent" in item and "result" not in item):
            skipped += 1      # never reached the environment (step budget used up) or crashed the simulator
            continue
        command = {k: v for k, v in command.items() if k not in ("step", "result", "i")}
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        wall = item.get("wall_s", item.get("seconds"))
        if wall is None and (item.get("t_agent_s") is not None or item.get("t_env_s") is not None):
            wall = float(item.get("t_agent_s") or 0.0) + float(item.get("t_env_s") or 0.0)
        records.append({"command": command, "ok": result.get("ok"), "category": result.get("category", ""),
                        "agent_message": result.get("message"), "time_s": item.get("sim_time_s", result.get("time_s")),
                        "wall_s": wall})
    if done:
        meta.setdefault("done_declared", True)
    episode = {"kind": "log", "name": name, "path": str(path) if path else None, "meta": meta, "records": records,
               "initial": None, "start_state": None, "baseline": None, "task": None, "seed": meta.get("seed"),
               "perception": meta.get("perception"), "camera": meta.get("camera"), "final_evaluation": None,
               "final_hash": None, "actions_hash": None, "skipped": skipped}
    _guard(meta, meta.get("seed"), meta.get("catalog_id"), private)
    replay = _log_replay(data, path)
    if replay is not None:
        # the harness saved the env replay next to the log: re-run from its exact start state and task
        rich = _from_replay(replay, name, path)
        if len(rich["records"]) == len(records):
            for mine, logged in zip(rich["records"], records):
                mine["agent_message"] = logged["agent_message"]
                mine["wall_s"] = logged["wall_s"]
                if logged["ok"] is not None and mine.get("ok") is not None and bool(logged["ok"]) != bool(mine["ok"]):
                    mine["ok"] = logged["ok"]      # the log is what the agent saw; a mismatch shows up as divergence
            rich_meta = dict(rich["meta"])
            rich_meta.update(meta)
            rich.update(meta=rich_meta, kind="log", skipped=skipped, path=str(path) if path else None)
            if not rich.get("perception") and meta.get("perception"):
                rich["perception"] = meta["perception"]
            if not rich.get("camera") and meta.get("camera"):
                rich["camera"] = meta["camera"]
            return rich
    return episode


def _log_replay(data, path):
    ref = data.get("replay")
    if not isinstance(ref, str) or not ref or path is None:
        return None
    ref = Path(ref)
    for candidate in ([ref] if ref.is_absolute() else [path.parent.parent / ref, path.parent / ref]):
        if candidate.is_file():
            try:
                replay = _read_json(candidate)
            except ViewerError:
                return None
            return replay if replay.get("format") == "house-replay-v2" else None
    return None


def _from_replay(data, name, path):
    task = data.get("task") or {}
    meta = {"catalog_id": task.get("catalog_id"), "instruction": task.get("instruction")}
    meta.update(_merge_meta(data.get("meta"), data.get("metadata")))
    records = []
    for item in data.get("steps", []):
        records.append({"command": dict(item.get("command") or {}), "ok": item.get("ok"),
                        "category": item.get("category", ""), "message": item.get("message"),
                        "time_s": item.get("time_s"), "state_hash": item.get("state_hash"),
                        "changes": item.get("changes")})
    perception = data.get("perception") or task.get("perception") or "oracle"
    return {"kind": "replay", "name": name, "path": str(path) if path else None, "meta": meta, "records": records,
            "initial": data.get("initial"), "start_state": data.get("start_state"), "baseline": data.get("baseline"),
            "task": data.get("task"), "seed": normalize_seed(data.get("seed")), "perception": perception,
            "camera": data.get("camera"),
            "final_evaluation": data.get("evaluation"), "final_hash": data.get("final_hash"),
            "actions_hash": data.get("actions_hash"),
            # kernel v3.0 replays record the physics data version (None: recorded before v3.0)
            "physics_recorded": (data.get("metadata") or {}).get("physics_version"), "physics_known": True}


def _find_spec(catalog_id):
    if not catalog_id:
        return None
    try:
        from .common import load_authored
        specs, _, _ = load_authored()   # an id authored in two files is left out, never guessed
    except Exception:  # noqa: BLE001 - authored specs unreadable: the replay still renders
        return None
    return specs.get(catalog_id)


def _prepare_log(episode, warnings):
    """Rebuild the episode's config and task from its spec (a log without a saved replay carries only commands)."""
    import hashlib
    from ..tasks.runner import build
    from ..tasks.verify import spec_hash
    meta = episode["meta"]
    catalog_id = meta.get("catalog_id")
    spec = _find_spec(catalog_id)
    if spec is None:
        raise ViewerError("no authored spec for catalog id {!r}; cannot re-run the command log".format(catalog_id))
    if meta.get("spec_hash") and meta["spec_hash"] != spec_hash(spec):
        warnings.append("The spec changed since this episode ran (spec hash differs); the re-run uses the current spec.")
    seed, test = episode.get("seed"), is_test_episode(meta)
    if seed is None:
        if test:
            seed = _resolve_test_seed(catalog_id, meta.get("seed_tag"))
        else:
            tag = str(meta.get("seed_tag") or "")
            if ":" in tag and tag.split(":", 1)[1].lstrip("-").isdigit():
                seed = int(tag.split(":", 1)[1])
    if seed is None:
        raise ViewerError("the log records no seed (and its seed tag does not give one)")
    # the episode's modes, as the harness built it (None: the spec's own)
    config, task, oracle = build(spec, int(seed), camera=meta.get("camera"), perception=meta.get("perception"))
    instruction = meta.get("instruction")
    phrasing = meta.get("phrasing")
    if phrasing is None and test and not instruction:
        phrasing = _test_phrasing(spec, int(seed))
    if phrasing:
        from ..tasks.spec import substitute
        instruction = substitute(phrasing, oracle.get("bindings", {}))
    if instruction:
        task["instruction"] = instruction
    digest_expected = meta.get("instruction_sha256")     # harness v1 logs (train/dev only since v2)
    mismatch = bool(digest_expected) and         hashlib.sha256(task.get("instruction", "").encode("utf-8")).hexdigest() != digest_expected
    keyed_expected = meta.get("instruction_hmac")        # harness v2 test logs: keyed digest, needs the split key
    if keyed_expected and not mismatch:
        try:
            from .run import private_text_digest
            keyed = private_text_digest("instruction", meta.get("catalog_id"), meta.get("seed_tag"),
                                        task.get("instruction", ""))
        except Exception:  # noqa: BLE001 - key unavailable or unreadable: nothing to compare against
            keyed = None
        mismatch = keyed is not None and not hmac.compare_digest(keyed, str(keyed_expected))
    if mismatch:
        task["instruction"] = "(private test phrasing not available here)"
        warnings.append("The episode's private instruction phrasing could not be recovered; the simulation is unaffected.")
    episode.update(initial=config, task=task, seed=int(seed), start_state=None, baseline=None)
    episode["perception"] = meta.get("perception") or task.get("perception") or "oracle"
    episode["camera"] = meta.get("camera")       # None: the spec's own (logs from before kernel v3.0)
    episode["spec"] = spec


def _test_phrasing(spec, seed):
    splits = _splits()
    if splits is None:
        return None
    try:
        for item in splits.episodes("test", [spec], seeds=[seed]):
            if item.get("seed") == seed:
                return item.get("phrasing")
    except Exception:  # noqa: BLE001 - key, manifest or phrasings unavailable
        return None
    return None


# ----------------------------------------------------------------------------------------------------------------
# layout (stable, layered per floor)
# ----------------------------------------------------------------------------------------------------------------
GAP_X, GAP_Y, GAP_COMPONENT = 1.6, 1.0, 2.5


def compute_layout(state):
    """{"rooms": {rid: info}, "floors": [{"f", "name", "W", "H", "rooms": [[rid, x, y, w, h]]}], "links": [...]}
    in meters. Each floor's same-floor connection graph is laid out in BFS layers from the start room (or the most
    connected room), ordered within a layer by the barycentre of their neighbours (stable sweeps), rooms sized from
    size_m; layers run as columns or as rows, whichever gives the shape closer to the map panel. Disconnected parts
    of a floor are stacked; cross-floor links (stairs, elevators) are drawn as markers by the page."""
    from ..kernel import geometry
    rooms = state.get("rooms", {})
    conns = state.get("connections", {})
    info = {}
    for rid in sorted(rooms):
        room = rooms[rid]
        cols, rows = geometry.grid_size(room)
        w, d = [float(v) for v in (room.get("size_m") or [4.0, 4.0])[:2]]
        info[rid] = {"l": room.get("label") or rid, "ty": room.get("type"), "f": room.get("floor", 0) or 0,
                     "o": bool(room.get("outdoor")), "cols": cols, "rows": rows, "w": round(w, 2), "h": round(d, 2)}
    adjacency = {rid: set() for rid in rooms}
    links = []
    for cid in sorted(conns):
        conn = conns[cid]
        pair = list(conn.get("rooms") or [])
        if len(pair) != 2 or pair[0] not in rooms or pair[1] not in rooms:
            continue
        a, b = pair
        cells = conn.get("cells") or {}
        ca, cb = cells.get(a) or [0, 0], cells.get(b) or [0, 0]
        links.append({"id": cid, "a": a, "ax": int(ca[0]), "ay": int(ca[1]), "b": b, "bx": int(cb[0]), "by": int(cb[1]),
                      "k": conn.get("kind", "doorway")})
        if info[a]["f"] == info[b]["f"]:
            adjacency[a].add(b)
            adjacency[b].add(a)
    start = state.get("start_room") or (state.get("robot") or {}).get("room")
    floors = []
    for floor in sorted({v["f"] for v in info.values()}, key=lambda f: (not isinstance(f, (int, float)), f)):
        members = [rid for rid in sorted(info) if info[rid]["f"] == floor]
        components, seen = [], set()
        while len(seen) < len(members):
            remaining = [r for r in members if r not in seen]
            root = start if start in remaining else sorted(remaining, key=lambda r: (-len(adjacency[r]), r))[0]
            components.append(_bfs_layers(root, adjacency, seen)[0])
        best = None
        for vertical in (False, True):   # layers as columns or as rows: keep the shape closest to the map panel
            placed, offset, extent = [], 0.0, 0.0
            for layers in components:
                boxes, comp_w, comp_h = _place_layers(layers, adjacency, info, vertical)
                for rid, x, y, w, h in boxes:
                    placed.append([rid, round(x, 2), round(y + offset, 2), w, h])
                offset += comp_h + GAP_COMPONENT
                extent = max(extent, comp_w)
            width, height = extent, max(0.0, offset - GAP_COMPONENT)
            score = abs(math.log(max(width, 0.5) / max(height, 0.5)) - math.log(1.3))
            if best is None or score < best[0] - 1e-9:
                best = (score, placed, width, height)
        floors.append({"f": floor, "name": _floor_name(floor), "W": round(best[2], 2), "H": round(best[3], 2),
                       "rooms": best[1]})
    return {"rooms": info, "floors": floors, "links": links}


class PerceivedMap:
    """The floor plan as the agent could know it, collected from the observations it was given (never from the true
    state): add(obs) for every observation the agent saw, then layout().

    * rooms: the rooms the robot observed (obs["room_id"]), with the label, type, floor, outdoor flag and size from
      their observation (obs["room"]: size_m, else grid cols x rows x cell_m);
    * links: the connections listed in those observations, with the doorway cell seen from each observed side;
    * stubs: rooms that observed exits or connections named (and, via `known`, rooms in the robot's known map) but
      that the robot never observed: bare ids, no label, type, size or floor. A link to a stub has no b-side cell."""

    def __init__(self):
        self.rooms, self.order, self.conns, self.named = {}, [], {}, set()

    def add(self, obs):
        if not isinstance(obs, dict):
            return
        rid = obs.get("room_id")
        if not isinstance(rid, str) or not rid:
            return
        if rid not in self.rooms:
            self.order.append(rid)
        room = obs.get("room") if isinstance(obs.get("room"), dict) else {}
        rec = {key: room[key] for key in ("label", "type", "floor", "outdoor") if room.get(key) is not None}
        grid = room.get("grid") if isinstance(room.get("grid"), dict) else {}
        size = room.get("size_m")
        if isinstance(size, (list, tuple)) and len(size) >= 2 and all(_num(v) is not None and _float(v) > 0 for v in size[:2]):
            rec["size_m"] = [_float(size[0]), _float(size[1])]
        elif _num(grid.get("cols")) and _num(grid.get("rows")):
            cell = _float(grid.get("cell_m"), 0.5) or 0.5
            rec["size_m"] = [_float(grid["cols"]) * cell, _float(grid["rows"]) * cell]
        if _num(grid.get("cols")) and _num(grid.get("rows")):
            rec["_grid"] = (int(_float(grid["cols"])), int(_float(grid["rows"])))
        self.rooms[rid] = rec
        for other in room.get("exits") or []:
            if isinstance(other, str) and other:
                self.named.add(other)
        for conn in obs.get("connections") or []:
            if not isinstance(conn, dict):
                continue
            cid, other = conn.get("id"), conn.get("to")
            if not isinstance(cid, str) or not cid or not isinstance(other, str) or not other:
                continue
            self.named.add(other)
            item = self.conns.setdefault(cid, {"rooms": [rid, other], "cells": {}, "kind": conn.get("kind") or "doorway"})
            cell = conn.get("cell")
            if isinstance(cell, (list, tuple)) and len(cell) >= 2:
                item["cells"][rid] = [int(_float(cell[0])), int(_float(cell[1]))]

    def layout(self, known=()):
        rooms = {rid: {k: v for k, v in rec.items() if k != "_grid"} for rid, rec in self.rooms.items()}
        both = {cid: conn for cid, conn in self.conns.items() if all(r in rooms for r in conn["rooms"])}
        seen = {"rooms": rooms, "connections": both, "start_room": self.order[0] if self.order else None}
        layout = compute_layout(seen) if rooms else {"rooms": {}, "floors": [], "links": []}
        for rid, rec in self.rooms.items():
            if "_grid" in rec and rid in layout["rooms"]:
                layout["rooms"][rid]["cols"], layout["rooms"][rid]["rows"] = rec["_grid"]   # as the agent was told
        stubs = (set(self.named) | {r for r in known if isinstance(r, str) and r}) - set(rooms)
        for cid in sorted(self.conns):
            conn = self.conns[cid]
            if cid in both:
                continue
            a = next((r for r in conn["rooms"] if r in rooms), None)
            b = next((r for r in conn["rooms"] if r != a), None)
            if a is None or b is None:
                continue
            stubs.add(b)
            cell = conn["cells"].get(a) or [0, 0]
            layout["links"].append({"id": cid, "a": a, "ax": int(cell[0]), "ay": int(cell[1]), "b": b, "k": conn["kind"]})
        layout["stubs"] = sorted(stubs)
        return layout


def _floor_name(floor):
    if floor == 0:
        return "Ground floor (0)"
    if isinstance(floor, (int, float)) and floor < 0:
        return "Basement ({})".format(floor)
    return "Floor {}".format(floor)


def _bfs_layers(root, adjacency, seen):
    layers, frontier = [], [root]
    seen.add(root)
    order = {root: 0}
    while frontier:
        layers.append(frontier)
        nxt = []
        for rid in frontier:
            for other in sorted(adjacency[rid]):
                if other not in seen:
                    seen.add(other)
                    order[other] = len(order)
                    nxt.append(other)
        frontier = nxt
    return layers, order


def _place_layers(layers, adjacency, info, vertical=False):
    """Boxes for BFS layers: columns left to right (vertical=False) or rows top to bottom (vertical=True)."""
    layers = [list(layer) for layer in layers]
    for _ in range(4):   # barycentre sweeps, alternating direction; ties keep the previous order (stable)
        for i in list(range(1, len(layers))) + list(range(len(layers) - 2, -1, -1)):
            ref = {}
            for j in (i - 1, i + 1):
                if 0 <= j < len(layers):
                    for pos, rid in enumerate(layers[j]):
                        ref[rid] = pos
            keyed = []
            for pos, rid in enumerate(layers[i]):
                near = [ref[o] for o in adjacency[rid] if o in ref]
                keyed.append((sum(near) / len(near) if near else pos, pos, rid))
            layers[i] = [rid for _, _, rid in sorted(keyed)]
    if vertical:
        row_h = [max(info[r]["h"] for r in layer) for layer in layers]
        row_w = [sum(info[r]["w"] for r in layer) + GAP_X * (len(layer) - 1) for layer in layers]
        width = max(row_w) if row_w else 0.0
        boxes, y = [], 0.0
        for index, layer in enumerate(layers):
            x = (width - row_w[index]) / 2.0
            for rid in layer:
                w, h = info[rid]["w"], info[rid]["h"]
                boxes.append((rid, x, y + (row_h[index] - h) / 2.0, w, h))
                x += w + GAP_X
            y += row_h[index] + GAP_Y * 1.6
        return boxes, width, max(0.0, y - GAP_Y * 1.6)
    col_w = [max(info[r]["w"] for r in layer) for layer in layers]
    col_h = [sum(info[r]["h"] for r in layer) + GAP_Y * (len(layer) - 1) for layer in layers]
    height = max(col_h) if col_h else 0.0
    boxes, x = [], 0.0
    for index, layer in enumerate(layers):
        y = (height - col_h[index]) / 2.0
        for rid in layer:
            w, h = info[rid]["w"], info[rid]["h"]
            boxes.append((rid, x + (col_w[index] - w) / 2.0, y, w, h))
            y += h + GAP_Y
        x += col_w[index] + GAP_X
    return boxes, max(0.0, x - GAP_X), height


# ----------------------------------------------------------------------------------------------------------------
# frames
# ----------------------------------------------------------------------------------------------------------------
def _klass(cat, mounted, mount_h, size, blocking, standing, ground, portable, connects):
    cat = str(cat or "")
    if connects:
        return "door"
    if ground or cat in FLOOR_KINDS:
        return "floor" if cat in FLOOR_KINDS else "rug"
    if mounted:
        if cat in WIN_CATS:
            return "win"
        if mount_h >= CEIL_MIN_M and not standing:
            return "ceil"
        if blocking or standing:
            return "furn"
        try:
            small = max(float(v) for v in (size or [0.1, 0.1])[:2]) <= 0.35
        except (TypeError, ValueError):
            small = True
        return "dot" if small else "wall"
    if blocking:
        return "furn"
    if portable:
        return "item"
    return "fix"


def _float(value, default=0.0):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _oracle_entity(eid, rec, rooms, kids, geometry):
    out = {"l": rec.get("label") or eid, "ty": rec.get("type"), "ca": rec.get("category")}
    loc, pos = rec.get("location"), rec.get("pos")
    if loc in rooms and isinstance(pos, (list, tuple)) and len(pos) >= 2:
        try:
            cols, rows = geometry.footprint_size(rec)
            blocking, standing = geometry.blocks(rec), geometry.floor_standing(rec)
        except Exception:  # noqa: BLE001 - odd records still get a 1-cell marker
            cols, rows, blocking, standing = 1, 1, False, False
        out.update(x=int(_float(pos[0])), y=int(_float(pos[1])), w=cols, d=rows)
        out["k"] = _klass(rec.get("category"), rec.get("mounted"), _float(rec.get("mount_height_m", rec.get("z_m", 0.0))),
                          rec.get("size_m"), blocking, standing, rec.get("ground_layer"), rec.get("portable"),
                          "connects" in rec)
        if blocking:
            out["b"] = 1
    else:
        out["in"] = loc
    if rec.get("relation") not in (None, "on") or "in" in out:
        out["rel"] = rec.get("relation")
    size = rec.get("size_m")
    if isinstance(size, (list, tuple)):
        out["sz"] = [_num(_float(v), 2) for v in size[:3]]
    if rec.get("rot"):
        out["rot"] = rec.get("rot")
    if rec.get("mounted"):
        out["mh"] = _num(_float(rec.get("mount_height_m", rec.get("z_m", 0.0))), 2)
    if rec.get("openable") or rec.get("open"):
        out["op"] = bool(rec.get("open"))
    if rec.get("lockable") or rec.get("locked"):
        out["lk"] = bool(rec.get("locked"))
    if isinstance(rec.get("on"), bool) and (rec.get("switchable") or rec.get("on") or rec.get("category") == "light"):
        out["on"] = rec["on"]
    if rec.get("broken"):
        out["br"] = True
    states = [w for w in STATE_WORDS if rec.get(w) is True]
    if states:
        out["st"] = states
    cues = {cue: _round_any(rec[cue]) for cue in PHYS_CUES if rec.get(cue) not in (None, False, "", [], {})}
    if cues:
        out["ph"] = cues
    damage = rec.get("damage")
    if isinstance(damage, dict):
        levels = {k: _num(_float(v), 2) for k, v in sorted(damage.items()) if isinstance(v, (int, float)) and _float(v) > 0}
        if levels:
            out["dm"] = levels
    soil = rec.get("soil")
    if isinstance(soil, dict):
        dirt = {k: _num(_float(v), 2) for k, v in sorted(soil.items()) if isinstance(v, (int, float)) and _float(v) >= 0.005}
        if dirt:
            out["so"] = dirt
    wet = _float(rec.get("wetness"))
    if wet >= 0.005:
        out["we"] = _num(wet, 2)
    if isinstance(rec.get("temperature_c"), (int, float)) and not isinstance(rec.get("temperature_c"), bool):
        out["tc"] = _num(round(_float(rec["temperature_c"]) * 2.0) / 2.0, 1)
    for key, short in (("material", "ma"), ("color", "co")):
        if isinstance(rec.get(key), str) and rec.get(key):
            out[short] = rec[key]
    extra = {}
    for key in EXTRA_FIELDS:
        value = rec.get(key)
        if value in (None, False, "", [], {}):
            continue
        extra[key] = _short(value, 48) if isinstance(value, (dict, list)) else _round_any(value)
    if extra:
        out["xf"] = extra
    if kids:
        out["ch"] = kids
    return out


def _children_map(state):
    kids = {}
    for eid in sorted(state["entities"]):
        kids.setdefault(state["entities"][eid].get("location"), []).append(eid)
    return kids


def _hazards(state, rid, geometry):
    cells = {}
    try:
        found = geometry.placement_hazards(state, rid)
    except Exception:  # noqa: BLE001 - a domain hook failing must not break the page
        try:
            found = geometry.hazard_cells(state, rid)
        except Exception:  # noqa: BLE001
            found = {}
    for cell, items in found.items():
        for kind, source in items:
            cells.setdefault((int(cell[0]), int(cell[1])), set()).add((str(kind), str(source)))
    for eid, rec in state["entities"].items():   # oracle view: also hazards hidden from perception
        if rec.get("location") == rid and rec.get("floor_hazard") and not rec.get("mounted"):
            try:
                for cell in geometry.footprint(rec):
                    cells.setdefault((int(cell[0]), int(cell[1])), set()).add((str(rec["floor_hazard"]), eid))
            except Exception:  # noqa: BLE001
                pass
    return [[x, y, kind, source] for (x, y), items in sorted(cells.items()) for kind, source in sorted(items)]


def _connections(state, rid):
    out = []
    for cid, conn in sorted(state.get("connections", {}).items()):
        pair = list(conn.get("rooms") or [])
        if rid not in pair or len(pair) != 2:
            continue
        cell = (conn.get("cells") or {}).get(rid) or [0, 0]
        item = {"id": cid, "to": pair[1] if pair[0] == rid else pair[0], "k": conn.get("kind", "doorway"),
                "x": int(_float(cell[0])), "y": int(_float(cell[1]))}
        door = conn.get("door")
        record = state["entities"].get(door or "")
        if record:
            item.update(dr=door, op=bool(record.get("open")), lk=bool(record.get("locked")))
        out.append(item)
    return out


def _agents(state, rid, group):
    out = {}
    keys = (("label", "l"), ("role", "ro"), ("species", "sp"), ("posture", "po"), ("activity", "ac"), ("mood", "mo"),
            ("awake", "aw"), ("holding", "ho"), ("carrying", "ca"), ("leashed", "le"))
    for aid, agent in sorted(state.get(group, {}).items()):
        if agent.get("location") != rid:
            continue
        item = {short: _round_any(agent[key]) for key, short in keys if agent.get(key) not in (None, "", [], {})}
        pos = agent.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            item.update(x=int(_float(pos[0])), y=int(_float(pos[1])))
        if agent.get("concealed") and not agent.get("revealed"):
            item["hid"] = True
        out[aid] = item
    return out


def oracle_room(state, rid, kids=None):
    from ..kernel import geometry, perception
    kids = kids if kids is not None else _children_map(state)
    rooms = state["rooms"]
    entities, queue = {}, list(kids.get(rid, []))
    while queue and len(entities) < MAX_ROOM_ENTITIES:
        eid = queue.pop(0)
        if eid in entities or eid not in state["entities"]:
            continue
        rec = state["entities"][eid]
        children = [c for c in kids.get(eid, []) if c in state["entities"]]
        queue.extend(children)
        if "connects" in rec or (rec.get("category") in FLOOR_KINDS and rec.get("ground_layer")):
            continue   # doors are drawn from the room's connections; the floor surface is the room itself
        entities[eid] = _oracle_entity(eid, rec, rooms, children, geometry)
    room = rooms[rid]
    try:
        level = perception.light_level(state, rid)
    except Exception:  # noqa: BLE001
        level = 1.0
    out = {"e": entities, "p": _agents(state, rid, "people"), "a": _agents(state, rid, "pets"),
           "z": _hazards(state, rid, geometry), "c": _connections(state, rid), "lt": _num(level, 2)}
    if room.get("wet_cells"):
        out["wc"] = [[int(c[0]), int(c[1])] for c in room["wet_cells"]]
    return out


def oracle_frame(state, prev=None):
    """True-state frame: robot + accumulated per-room states (only the current room is refreshed; other rooms keep
    their state from when the robot was last there, so deltas stay small; the page renders the current room)."""
    from ..kernel import clock, geometry
    robot = state["robot"]
    rid = robot["room"]
    kids = _children_map(state)
    rooms = dict((prev or {}).get("R") or {})
    rooms[rid] = _clean(oracle_room(state, rid, kids))
    held = {hand: eid for hand, eid in sorted((robot.get("hands") or {}).items()) if eid}
    held_records = {}
    for eid in held.values():
        rec = state["entities"].get(eid)
        if rec is not None:
            held_records[eid] = _oracle_entity(eid, rec, state["rooms"], [c for c in kids.get(eid, [])], geometry)
    pos = robot.get("pos") or [0, 0]
    try:
        stamp = clock.now(state).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        stamp = None
    frame = {"r": rid, "rp": [int(_float(pos[0])), int(_float(pos[1]))], "po": robot.get("posture", "standing"),
             "bat": _num(_float(robot.get("battery", 100.0)), 1), "h": held, "H": held_records,
             "t": _num(_float(state.get("time_s", 0.0)), 1), "clk": stamp, "R": rooms}
    for key, short in (("docked", "dk"), ("stopped", "sp"), ("fallen", "fl"), ("standing_on", "so")):
        if robot.get(key):
            frame[short] = robot[key]
    if robot.get("speed_mode") not in (None, "normal"):
        frame["sm"] = robot["speed_mode"]
    frame.update(oracle_body(state))
    cam = oracle_camera(state)
    if cam is not None:
        frame["cam"] = cam
    frame = _clean({k: v for k, v in frame.items() if k != "R"})
    frame["R"] = rooms            # other rooms are shared with the previous frame (already clean)
    return frame


def oracle_body(state):
    """The robot body markers of a true state (kernel v3.0, TIER1_EMBODIMENT 8.4): "pw" the power state when it is not
    "on", "bs" the battery state when it is not "ok", "ft" the kinds of the active faults the robot has detected and
    "lf" those still latent (privileged). Empty for states the body cannot read."""
    out = {}
    try:
        from ..kernel import body, body_faults
        power = body.get(state, "power_state")
        if power not in (None, "on"):
            out["pw"] = power
        level = body.battery_state(state)
        if level != "ok":
            out["bs"] = level
        active = body_faults.active_faults(state)
    except Exception:  # noqa: BLE001 - a legacy or partial state still renders without body markers
        return out
    detected = sorted({str(r.get("kind")) for r in active if r.get("detected")})
    latent = sorted({str(r.get("kind")) for r in active if not r.get("detected")})
    if detected:
        out["ft"] = detected
    if latent:
        out["lf"] = latent
    return out


def perceived_body(obs):
    """The body markers the observation gave the agent (realistic obs.robot): "pw" power state when not "on", "bs"
    battery state when not "ok", "ft" the fault kinds obs.robot.faults lists (detected faults and the robot's own log
    notes and falls)."""
    robot = obs.get("robot") if isinstance(obs.get("robot"), dict) else {}
    out = {}
    if robot.get("power_state") not in (None, "on"):
        out["pw"] = robot["power_state"]
    if robot.get("battery_state") not in (None, "ok"):
        out["bs"] = robot["battery_state"]
    kinds = sorted({str(f.get("kind")) for f in robot.get("faults") or [] if isinstance(f, dict) and f.get("kind")})
    if kinds:
        out["ft"] = kinds
    return out


def _mark_detail(entry):
    """A short text for one marked event-log entry (oracle view: ids are true ids)."""
    kind = entry.get("kind")
    if str(kind).startswith("phys."):
        target = entry.get("target")
        text = target if isinstance(target, str) else ""
        by = entry.get("by")
        if by in ("robot", "exogenous"):
            text = "{} ({})".format(text, by).strip()
        return text
    if kind == "overload":
        return "{} ({})".format(entry.get("target"), entry.get("ratio_bucket")) if entry.get("ratio_bucket") else str(entry.get("target") or "")
    if kind in ("power_down", "power_up", "robot_fell"):
        return str(entry.get("cause") or "")
    if kind == "fault_onset":
        part = entry.get("part")
        return "{}{} ({})".format(entry.get("fault_kind"), " " + str(part) if part else "", entry.get("source") or "event")
    if kind in ("fault_detected", "fault_cleared"):
        return "{} ({})".format(entry.get("fault"), entry.get("how") or "")
    if kind == "fault_logged":
        return "{}: {}".format(entry.get("fault_kind"), entry.get("status"))
    if kind in ("battery_low", "battery_reserve"):
        return "gauge {}%".format(_num(_float(entry.get("gauge")), 1))
    return ""


def step_marks(state):
    """[[kind, detail], ...] for the physics consequences (phys.* events) and body events (BODY_MARKS) the event log
    holds for the state's current step (at most MAX_MARKS, then ["more", "+N"])."""
    step = state.get("steps", 0)
    found = [entry for entry in state.get("event_log") or [] if isinstance(entry, dict) and entry.get("step") == step
             and (str(entry.get("kind", "")).startswith("phys.") or entry.get("kind") in BODY_MARKS)]
    marks = [[str(entry.get("kind")), _short(_mark_detail(entry), 80)] for entry in found[:MAX_MARKS]]
    if len(found) > MAX_MARKS:
        marks.append(["more", "+{}".format(len(found) - MAX_MARKS)])
    return marks


def oracle_camera(state):
    """The geometric camera of a true state (None in room mode): "fd" body facing and "hy" head yaw (grid degrees: 0
    east, 90 south), "hf" horizontal field of view, "fc" this step's frame count and "fy" the headings of its frames
    taken in the current room, "sc" the room's floor cells camera_log counts as seen (row-major, "1" = seen) and "cv"
    its coverage."""
    robot = state["robot"]
    view = robot.get("view")
    if not isinstance(view, dict):
        return None
    from ..kernel import camera as camera_mod
    rid = robot.get("room")
    facing = _float(robot.get("facing_deg"), 90.0)
    frames = view.get("frames") if view.get("step") == state.get("steps", 0) else []
    log = state.get("camera_log") or {}
    try:
        hfov = float(camera_mod.hw(state)["head"]["hfov_deg"])
    except Exception:  # noqa: BLE001
        hfov = 69.0
    out = {"fd": _num(facing, 1), "hy": _num((facing + _float(view.get("pan_deg"), 0.0)) % 360.0, 1), "hf": _num(hfov, 1),
           "fc": len(frames or []),
           "fy": sorted({_num(_float(f[4]) % 360.0, 1) for f in frames or [] if isinstance(f, list) and len(f) > 4
                         and f[0] == rid}),
           "sc": (log.get("seen_cells") or {}).get(rid), "cv": (log.get("coverage") or {}).get(rid)}
    return {k: v for k, v in out.items() if v is not None}


def perceived_camera(obs):
    """What obs.camera told the agent (None without a camera block): facing, head yaw, frame count and coverage (the
    seen-cells overlay is privileged and only on oracle frames)."""
    block = obs.get("camera")
    if not isinstance(block, dict):
        return None
    pose = block.get("pose") or {}
    facing = _float(pose.get("facing_deg"), 90.0)
    out = {"fd": _num(facing, 1), "hy": _num((facing + _float(pose.get("pan_deg"), 0.0)) % 360.0, 1),
           "fc": block.get("frames"), "cv": block.get("coverage")}
    return {k: v for k, v in out.items() if v is not None}


def _perceived_entity(rec, rooms):
    out = {}
    for key in sorted(rec):
        value = rec[key]
        if value is None or value == "" or value == [] or value == {}:
            continue
        if value is False and key not in ("open", "on", "locked", "lit"):
            continue
        if key == "open" and value is False and not rec.get("openable"):
            continue
        if key == "accepts_items":
            continue
        out[key] = _round_any(value)
    pos = rec.get("pos")
    if rec.get("location") in rooms and isinstance(pos, (list, tuple)) and len(pos) >= 2:
        size = rec.get("size_m") if isinstance(rec.get("size_m"), (list, tuple)) else None
        w, d = (0.4, 0.4) if not size or len(size) < 2 else (_float(size[0], 0.4), _float(size[1], 0.4))
        if rec.get("rot") in (90, 270):
            w, d = d, w
        out["_w"] = max(1, int(math.ceil(w / 0.5 - 1e-9)))
        out["_d"] = max(1, int(math.ceil(d / 0.5 - 1e-9)))
        kind = str(rec.get("kind") or "")
        ground = kind in FLOOR_KINDS or kind in RUG_KINDS
        furn = not rec.get("portable") and (kind in FURN_KINDS or rec.get("size_class") in ("large", "huge"))
        out["_k"] = _klass(kind, rec.get("mounted"), _float(rec.get("mount_height_m")), size, furn and not rec.get("mounted"),
                           False, ground, rec.get("portable"), bool(rec.get("connects")))
    return out


def perceived_frame(obs, rooms, prev=None, mask=None):
    """The agent's view: observation contents only (handles, buckets, noisy estimates). mask(value) -> value is
    applied to the new parts (defence in depth against true ids leaking through observations)."""
    mask = mask or (lambda value: value)
    rid = obs.get("room_id")
    robot = obs.get("robot") or {}
    entities = {}
    for handle, rec in (obs.get("visible_entities") or {}).items():
        if isinstance(rec, dict) and not (str(rec.get("kind") or "") in FLOOR_KINDS and rec.get("location") == rid):
            entities[handle] = _perceived_entity(rec, rooms)
    people = {pid: _round_any({k: v for k, v in rec.items() if k != "location"}) for pid, rec in (obs.get("people") or {}).items()}
    pets = {pid: _round_any({k: v for k, v in rec.items() if k != "location"}) for pid, rec in (obs.get("pets") or {}).items()}
    grid = obs.get("grid") or {}
    hazards = [[int(h["cell"][0]), int(h["cell"][1]), str(h.get("kind")), str(h.get("source"))]
               for h in grid.get("hazards", []) if isinstance(h, dict) and h.get("cell")]
    conns = []
    for conn in obs.get("connections") or []:
        cell = conn.get("cell") or [0, 0]
        item = {"id": conn.get("id"), "to": conn.get("to"), "k": conn.get("kind", "doorway"),
                "x": int(_float(cell[0])), "y": int(_float(cell[1]))}
        if conn.get("door"):
            item.update(dr=conn["door"], op=bool(conn.get("open")), lk=bool(conn.get("locked")))
        conns.append(item)
    room_state = {"e": entities, "p": people, "a": pets, "z": hazards,
                  "b": [[int(c[0]), int(c[1])] for c in grid.get("blocked", [])], "c": conns,
                  "lt": _num((obs.get("room") or {}).get("light_level"), 2)}
    all_rooms = dict((prev or {}).get("R") or {})
    all_rooms[rid] = mask(_clean(room_state))
    channels = {}
    for key in CHANNELS:
        value = obs.get(key)
        if value not in (None, "", [], {}):
            channels[key] = _short(value, CHANNEL_CHARS)
    pos = robot.get("pos") or [0, 0]
    frame = {"r": rid, "rp": [int(_float(pos[0])), int(_float(pos[1]))], "po": robot.get("posture", "standing"),
             "bat": _num(_float(obs.get("battery", robot.get("battery", 100.0))), 1),
             "h": list(obs.get("holding") or []), "dark": bool(obs.get("dark")), "R": all_rooms, "ch": channels}
    if robot.get("standing_on"):
        frame["so"] = robot["standing_on"]
    frame.update(perceived_body(obs))
    cam = perceived_camera(obs)
    if cam is not None:
        frame["cam"] = cam
    frame = mask(_clean({k: v for k, v in frame.items() if k != "R"}))
    frame["R"] = all_rooms
    return frame


def _mask_ids(value, ids, counter, replace=None, skip_keys=()):
    """Replace true entity ids (whole strings or tokens) with replace(eid) (default "?"), counting replacements."""
    if isinstance(value, str):
        if value in ids:
            counter[0] += 1
            return replace(value) if replace else "?"
        if "_" not in value:
            return value

        def tok(match):
            word = match.group(0)
            if word in ids:
                counter[0] += 1
                return replace(word) if replace else "?"
            return word
        return TOKEN_RE.sub(tok, value)
    if isinstance(value, list):
        return [_mask_ids(v, ids, counter, replace, skip_keys) for v in value]
    if isinstance(value, dict):
        return {_mask_ids(k, ids, counter, replace, ()): (v if k in skip_keys else _mask_ids(v, ids, counter, replace, skip_keys))
                for k, v in value.items()}
    return value


def _descriptive_keys():
    try:
        from ..kernel.realism import DESCRIPTIVE_KEYS
        return set(DESCRIPTIVE_KEYS)
    except ImportError:
        return {"label", "kind", "candidates", "descriptor", "category", "material", "color", "shape", "cues",
                "posture", "activity", "role", "species", "size_class", "material_guess", "label_text", "text"}


# ----------------------------------------------------------------------------------------------------------------
# evaluation, steps, diffs
# ----------------------------------------------------------------------------------------------------------------
def _harm_keys():
    try:
        from ..kernel.evaluation import GLOBAL_FLAGS
        return tuple(GLOBAL_FLAGS)
    except ImportError:
        return ("damage", "injury", "fire", "flood", "collisions", "falls", "spills", "breakages")


BLOCK_KEYS = ("safety_blocks", "privacy_blocks", "social_blocks", "authority_blocks")


def eval_compact(ev):
    if not ev:
        return {"na": True}
    metrics = ev.get("metrics") or {}
    out = {"s": bool(ev.get("success")), "g": _num(ev.get("goal_fraction"), 3), "sc": _num(ev.get("score"), 3),
           "f": [str(f) for f in (ev.get("failures") or [])][:40], "bx": bool(ev.get("budget_exceeded")),
           "m": {str(k): [v.get("step"), _num(v.get("time_s"), 1)] for k, v in sorted((ev.get("milestones") or {}).items())
                 if isinstance(v, dict)},
           "hf": {k: _num(metrics[k], 2) for k in _harm_keys() if metrics.get(k)},
           "bl": {k: metrics[k] for k in BLOCK_KEYS if metrics.get(k)},
           "iv": metrics.get("invalid", 0), "q": [metrics.get("necessary_questions", 0), metrics.get("unnecessary_questions", 0)]}
    if ev.get("rubric_score") is not None:
        out["rs"] = _num(ev["rubric_score"], 3)
    return out


def step_compact(record, raw_message, agent_message, diverged):
    command = dict(record.get("command") or {})
    action = str(command.pop("action", ""))
    target, destination = command.pop("target", ""), command.pop("destination", "")
    out = {"a": action, "ok": bool(record.get("ok_now", record.get("ok")))}
    if target not in (None, ""):
        out["tg"] = _short(target, 120)
    if destination not in (None, ""):
        out["de"] = _short(destination, 120)
    if command:
        out["pa"] = _short(command, 200)
    if record.get("category_now", record.get("category")):
        out["ca"] = record.get("category_now", record.get("category"))
    if raw_message is not None:
        out["m"] = _short(raw_message, MSG_CHARS)
    if agent_message is not None and agent_message != raw_message:
        out["am"] = _short(agent_message, MSG_CHARS)
    if record.get("time_now") is not None:
        out["ts"] = _num(record["time_now"], 1)
    elif record.get("time_s") is not None:
        out["ts"] = _num(record["time_s"], 1)
    if record.get("wall_s") is not None:
        out["wl"] = _num(record["wall_s"], 2)
    if diverged:
        out["dv"] = diverged
    return out


def _diff_rank(change):
    path = str(change.get("path", ""))
    parts = path.split(".")
    if any(p.startswith("_") for p in parts) or parts[0] in DROP_TOP:
        return None
    if any(path == d or path.startswith(d + ".") for d in DROP_PATHS):
        return None
    top = parts[0]
    if top == "entities":
        if len(parts) == 2:
            return 0
        field = parts[2]
        if field in KEY_FIELDS:
            return 0
        limit = DRIFT_FIELDS.get(field)
        before, after = change.get("before"), change.get("after")
        if limit is not None and isinstance(before, (int, float)) and isinstance(after, (int, float)):
            return 3 if abs(float(after) - float(before)) < limit else 1
        return 1
    if top in ("robot", "reports", "approvals", "messages", "stats", "orders", "pending"):
        return 0
    if top in ("people", "pets"):
        return 0 if len(parts) > 2 and parts[2] in ("location", "pos", "posture", "activity", "mood", "awake", "holding") else 2
    if top in ("facts", "memory", "utilities", "connections"):
        return 1
    return 2


def diff_compact(changes):
    ranked = []
    for change in changes or []:
        rank = _diff_rank(change)
        if rank is not None:
            ranked.append((rank, str(change.get("path")), change))
    ranked.sort(key=lambda item: (item[0], item[1]))
    rows = []
    for rank, path, change in ranked[:MAX_DIFFS]:
        before = _short(change.get("before")) if change.get("before_exists", True) else None
        after = _short(change.get("after")) if change.get("after_exists", True) else None
        parts = path.split(".")
        if parts[0] == "entities" and len(parts) == 2:
            if change.get("after_exists", True) and isinstance(change.get("after"), dict):
                after = "created: " + _short(change["after"].get("label") or change["after"].get("type") or "entity", 40)
                before = None
            elif not change.get("after_exists", True):
                after = None
                before = _short((change.get("before") or {}).get("label") or "entity", 40) if isinstance(change.get("before"), dict) else before
        row = [path, before, after]
        if rank:
            row.append(rank)
        rows.append(row)
    out = {"c": rows}
    hidden = len(changes or []) - len(rows)
    if hidden > 0:
        out["n"] = hidden
    return out


def _apply_change(state, change):
    parts = str(change.get("path", "")).split(".")
    node = state
    for key in parts[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    if change.get("after_exists", True):
        node[parts[-1]] = copy.deepcopy(change.get("after"))
    else:
        node.pop(parts[-1], None)


# ----------------------------------------------------------------------------------------------------------------
# building the page data
# ----------------------------------------------------------------------------------------------------------------
def _spec_status(spec, verify_spec):
    if spec is None:
        return {"state": "n/a"}
    from ..tasks.verify import spec_hash
    if verify_spec:
        from ..tasks.verify import verify_many
        seeds = _dev_seeds()
        result = verify_many([spec], seeds=seeds, workers=1)[0]   # each spec in its own perception mode
        return {"state": "verified now" if result["passed"] else "failed verification now", "seeds": list(seeds),
                "passed": bool(result["passed"])}
    try:
        from ..audit import fingerprint
        from ..catalog import ROOT
        path = ROOT / "reports" / "verification.jsonl"
        evidence = None
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and '"{}"'.format(spec.get("catalog_id")) in line:
                    row = json.loads(line)
                    if row.get("catalog_id") == spec.get("catalog_id"):
                        evidence = row
        if evidence is None:
            return {"state": "unverified (no evidence)", "passed": False}
        if not evidence.get("passed"):
            return {"state": "unverified (evidence failing)", "passed": False}
        if evidence.get("implementation_hash") != fingerprint():
            return {"state": "unverified (evidence stale: code changed)", "passed": False}
        if evidence.get("spec_hash") != spec_hash(spec):
            return {"state": "unverified (evidence stale: spec changed)", "passed": False}
        return {"state": "verified (current evidence)", "passed": True}
    except Exception as exc:  # noqa: BLE001
        return {"state": "unknown ({})".format(type(exc).__name__), "passed": False}


def _live_env(episode, warnings, trace=True):
    from ..kernel.engine import HouseEnv, actions_fingerprint
    from ..kernel.world import World, clone
    # observe_on_step=True like the harness and HouseEnv.replay: some actions read the known map that observing builds
    env = HouseEnv(episode["initial"], episode["task"], episode["seed"], trace=trace, observe_on_step=True,
                   perception=episode["perception"], camera=episode.get("camera"))
    env.raw_results = True     # like the task runner: step results keep the raw message and the evaluation
    if episode.get("start_state") is not None:
        env.world = World(clone(episode["start_state"]), env.registry)
        env._camera_view = None
        if env.realism is not None:
            from ..kernel.realism import Realism
            env.realism = Realism(env)
            if hasattr(env, "_note_view"):
                env._note_view()       # what the cameras saw at the start, as HouseEnv.replay does ...
            env.observe()              # ... and the observation reset() returned (the handles it issued)
        if episode.get("baseline") is not None:
            env._baseline = episode["baseline"]
        env._replay_start = clone(env.world.s)
        env._last_score = env.evaluate()["score"]
    if episode.get("actions_hash") and episode["actions_hash"] != actions_fingerprint():
        warnings.append("Action definitions changed since this episode was recorded; the re-run may diverge.")
    current = _physics_version()
    if episode.get("physics_known") and current is not None and episode.get("physics_recorded") != current:
        warnings.append("Recorded with physics data {}; the re-run uses {}, so physical outcomes may not match the "
                        "recording.".format(episode.get("physics_recorded") or "from before kernel v3.0", current))
    return env


def _physics_version():
    """The current physics data version (kernel/physics.py physics_version), None when unavailable."""
    try:
        from ..kernel.physics import physics_version
        return physics_version()
    except Exception:  # noqa: BLE001 - an older kernel without the physics layer
        return None


def build_viewer_data(source, view="both", private=False, verify_spec=False):
    """Load, re-run and compact an episode into the page's data object (see docs/VIEWER.md for the schema)."""
    if view not in VIEWS:
        raise ViewerError("view must be one of {}".format(", ".join(VIEWS)))
    episode = load_source(source, private=bool(private))
    meta, warnings = dict(episode["meta"]), []
    catalog_id = meta.get("catalog_id") or (episode.get("task") or {}).get("catalog_id")
    episode["seed"] = normalize_seed(episode.get("seed"))       # an int (or None) from here on, never a raw str/float
    test = is_test_episode(meta, episode.get("seed"), catalog_id) or bool(episode.get("seed_unknown"))
    if test and not private:
        raise SplitRefused(REFUSAL)
    if episode["kind"] == "log" and episode.get("initial") is None:
        _prepare_log(episode, warnings)
    else:
        episode["spec"] = _find_spec(catalog_id)
        spec = episode["spec"]
        if spec is not None and spec.get("catalog_id") != (episode.get("task") or {}).get("catalog_id"):
            episode["spec"] = None
    if not test and catalog_id and is_test_episode({}, episode.get("seed"), catalog_id):
        if not private:
            raise SplitRefused(REFUSAL)
        test = True
    realistic = episode.get("perception") == "realistic"
    if view == "perceived" and not realistic:
        raise ViewerError("the perceived view needs an episode run with realistic perception")
    want_oracle = view in ("both", "oracle")
    want_perceived = realistic and view in ("both", "perceived")

    from ..kernel.predicates import digest
    from ..kernel.world import clone
    task = episode.get("task") or {}
    records = episode["records"]
    have_changes = bool(records) and all(isinstance(r.get("changes"), list) for r in records) and episode.get("start_state") is not None

    env, live, state, recon = None, False, None, None
    try:
        env = _live_env(episode, warnings, trace=not have_changes)   # recorded changes make the re-run's trace redundant
        live = True
        state = env.world.s
    except Exception as exc:  # noqa: BLE001 - fall back to recorded changes when the simulator cannot load it
        if not have_changes:
            raise ViewerError("cannot re-run this episode with the current simulator ({}: {})".format(type(exc).__name__, exc))
        warnings.append("The current simulator cannot load this episode ({}); frames come from the recorded changes "
                        "and the perceived view is unavailable.".format(type(exc).__name__))
        recon = clone(episode["start_state"])
        state = recon

    # the true floor plan only goes on pages that carry the oracle view; a perceived-only page gets the plan the
    # agent could know, collected from its observations below (PerceivedMap)
    layout = compute_layout(state) if want_oracle else None
    pmap, seen_rooms = PerceivedMap(), set()
    room_ids = set(state.get("rooms", {}))
    oracle_frames, perceived_frames, evals = _DeltaStream(), _DeltaStream(), _DeltaStream()
    steps, diffs, marks = [], [], {}
    true_ids = set(state["entities"])
    leaks = [0]
    descriptive = _descriptive_keys()
    prev_oracle = prev_perceived = None
    milestones, baseline = {}, episode.get("baseline") or (env._baseline if env is not None else None)
    first_divergence = None

    def handle_of(eid):
        return env.realism.handles.handle(eid) if env is not None and env.realism is not None else "?"

    def add_frames(current_state, evaluation, obs, perceived_ok):
        nonlocal prev_oracle, prev_perceived
        true_ids.update(current_state["entities"])
        if want_oracle:
            found = step_marks(current_state)
            if found:
                marks[str(len(oracle_frames.items))] = found      # keyed by the frame the step led to
            prev_oracle = oracle_frame(current_state, prev_oracle)
            oracle_frames.add(prev_oracle, clean=False)
        if want_perceived:
            if perceived_ok and obs is not None:
                pmap.add(obs)
                seen_rooms.add(obs.get("room_id"))
                prev_perceived = perceived_frame(obs, room_ids, prev_perceived if prev_perceived and not prev_perceived.get("na") else None,
                                                 mask=lambda value: _mask_ids(value, true_ids, leaks, skip_keys=descriptive))
            else:
                prev_perceived = {"na": True}
                room = current_state["robot"]["room"]
                if want_oracle or room in seen_rooms:     # a perceived-only page names only rooms the agent observed
                    prev_perceived["r"] = room
            perceived_frames.add(prev_perceived, clean=False)
        compact = eval_compact(evaluation)
        if view == "perceived":
            compact = _mask_ids(compact, true_ids, [0], replace=handle_of)
        evals.add(compact)

    def recorded_eval(current_state, step_number):
        try:
            from ..kernel.evaluation import evaluate
            from ..kernel.predicates import check
            from ..kernel.world import World
            for name, rule in (task.get("milestones") or {}).items():
                if name not in milestones and check(current_state, rule):
                    milestones[name] = {"step": step_number, "time_s": current_state.get("time_s", 0)}
            return evaluate(task, current_state, baseline or current_state, milestones, step_number,
                            world=World(current_state, env.registry if env is not None else None))
        except Exception:  # noqa: BLE001
            return None

    initial_eval = env.evaluate() if live else recorded_eval(state, 0)
    add_frames(state, initial_eval, env.observe() if live and want_perceived else None, live)

    for number, record in enumerate(records, 1):
        raw_message = record.get("message")
        agent_message = record.get("agent_message")
        diverged = None
        changes = record.get("changes") if isinstance(record.get("changes"), list) else None
        evaluation, obs = None, None
        if live:
            command = dict(record.get("command") or {})
            try:
                result = env.step(**command)
            except Exception as exc:  # noqa: BLE001
                result = None
                diverged = "the re-run raised {}".format(type(exc).__name__)
            if result is not None:
                history = env._history[-1]
                if record.get("ok") is not None and bool(record["ok"]) != bool(result["ok"]):
                    diverged = "recorded ok={} but the re-run gives ok={}".format(bool(record["ok"]), bool(result["ok"]))
                elif record.get("state_hash") and (history.get("state_hash") or digest(env.world.s)) != record["state_hash"]:
                    diverged = "the re-run state differs from the recording"
                if not (diverged and have_changes):
                    record = dict(record, ok_now=result["ok"], category_now=result["category"], time_now=history.get("time_s"))
                    raw_message = history.get("message")
                if agent_message is None:
                    if env.realism is not None and diverged and record.get("message") is not None:
                        agent_message = env.realism.outbound_message(record["message"])   # the recording, translated
                    else:
                        agent_message = result.get("message")       # exactly what the env showed the agent
                if changes is None:
                    changes = history.get("changes")
                # realistic step results may carry no evaluator verdict (an agent gets none); the viewer is a
                # privileged tool and scores with env.evaluate() then
                evaluation = result.get("evaluation") or env.evaluate()
                obs = result.get("observation")
            if diverged:
                announce = first_divergence is None      # later mismatches are only flagged on their step rows
                first_divergence = first_divergence or number
                if have_changes:
                    live = False
                    recon = clone(episode["start_state"])
                    for earlier in records[:number - 1]:
                        for change in earlier["changes"]:
                            _apply_change(recon, change)
                    for name, value in (env._milestones or {}).items():
                        if value.get("step", 0) < number:
                            milestones[name] = dict(value)
                    warnings.append("Step {}: {}; the true state continues from the recorded changes and the perceived "
                                    "view stops there.".format(number, diverged))
                elif result is None:
                    raise ViewerError("re-running step {} failed ({}); the log cannot be replayed with the current "
                                      "code".format(number, diverged))
                elif announce:
                    warnings.append("Step {}: {}; later frames show what the current simulator does with the same "
                                    "commands (mismatching steps are flagged in the step list).".format(number, diverged))
            if live:
                state = env.world.s
            else:
                obs = None
        if not live:
            for change in changes or []:
                _apply_change(recon, change)
            state = recon
            evaluation = recorded_eval(state, number)
            if number == len(records) and episode.get("final_evaluation"):
                evaluation = episode["final_evaluation"]
        if view == "perceived":
            raw_message = agent_message if agent_message is not None else raw_message
            agent_message = None
        steps.append(step_compact(record, raw_message, agent_message, diverged))
        if want_oracle:
            diffs.append(diff_compact(changes))
        add_frames(state, evaluation, obs, live)

    if layout is None:
        # perceived-only page: rooms the agent observed (+ exit-only stubs), masked like the perceived frames
        known = sorted(env._known_map) if env is not None and isinstance(getattr(env, "_known_map", None), dict) else ()
        layout = _mask_ids(pmap.layout(known=known), true_ids, leaks, skip_keys=descriptive | {"l", "ty"})
    if live and episode.get("final_hash") and digest(env.world.s) != episode["final_hash"] and not first_divergence:
        warnings.append("The re-run's final state differs from the recorded final state.")
    if leaks[0]:
        warnings.append("{} true entity id(s) found in the perceived observations were masked as '?' (a perception "
                        "leak to report).".format(leaks[0]))

    final_eval = evals.prev or {}
    recorded_success = meta.get("success")
    if recorded_success is not None and not final_eval.get("na"):
        # harness success also needs a "done" declaration unless the run disabled require_done
        replayed = bool(final_eval.get("s")) and (bool(meta.get("done_declared")) or not meta.get("require_done", False))
        if bool(recorded_success) != replayed:
            warnings.append("The episode record says success={} but the re-run gives success={}.".format(
                bool(recorded_success), replayed))
    if episode.get("skipped"):
        warnings.append("{} logged command(s) never reached the environment (step budget used up or simulator error) "
                        "and are not shown as steps.".format(episode["skipped"]))

    if view == "perceived":
        steps = _mask_ids(steps, true_ids, [0], replace=handle_of)
    spec = episode.get("spec")
    title = task.get("title") or meta.get("catalog_id") or episode["name"]
    source_name = Path(episode["path"]).name if episode.get("path") else episode["name"]
    seed_tag = meta.get("seed_tag")
    if test:
        # a private page never embeds the raw seed: not as "seed", not inside a public-style tag ("seed:N", "dev:N"),
        # not inside a file name; only a keyed "test:..." tag is kept
        if seed_tag is not None and not str(seed_tag).strip().lower().startswith(("test:", "test-")):
            seed_tag = None
        title, source_name = (_hide_seeds(text, (episode.get("seed"), meta.get("seed"))) for text in (title, source_name))
    out_meta = {"title": title, "catalog_id": catalog_id,
                "instruction": meta.get("phrasing") or task.get("instruction") or meta.get("instruction") or "",
                "perception": episode.get("perception") or "oracle",
                "camera": (env.camera_mode if env is not None else None) or episode.get("camera") or "room", "view": view,
                "views": (["oracle"] if want_oracle else []) + (["perceived"] if want_perceived else []),
                "source_kind": episode["kind"], "source": source_name,
                "split": "test" if test else meta.get("split"), "seed_tag": seed_tag,
                "agent": meta.get("agent"), "privileged": meta.get("privileged"), "private": bool(test),
                "steps": len(records), "step_budget": task.get("step_budget"), "time_budget_s": task.get("time_budget_s"),
                "risk_level": task.get("risk_level"), "fidelity": task.get("fidelity"), "variant": task.get("variant"),
                "milestones": sorted((task.get("milestones") or {}).keys()), "warnings": warnings,
                "done_declared": meta.get("done_declared"), "end_reason": meta.get("end_reason"),
                "spec_status": _spec_status(spec, verify_spec) if spec is not None else {"state": "n/a"},
                # the physics data the frames come from: the current code's when it re-ran the episode, else the
                # recording's (a replay the current simulator cannot load)
                "physics_version": _physics_version() if env is not None else episode.get("physics_recorded")}
    if episode.get("physics_known") and episode.get("physics_recorded") != out_meta["physics_version"]:
        out_meta["physics_recorded"] = episode.get("physics_recorded") or "before kernel v3.0"
    if not test and episode.get("seed") is not None:
        out_meta["seed"] = normalize_seed(episode["seed"])
    if want_oracle:
        out_meta["success_rules"] = [_short(rule, 160) for rule in task.get("success", [])][:20]
    if view == "perceived":
        out_meta = _mask_ids(out_meta, true_ids, [0], replace=handle_of)
    if first_divergence:
        out_meta["diverged_at"] = first_divergence
    data = {"format": FORMAT, "meta": _clean(out_meta), "layout": layout, "steps": steps, "evals": evals.items}
    if want_oracle:
        data["frames"] = oracle_frames.items
        data["diffs"] = diffs
        if marks:
            data["marks"] = marks
    if want_perceived:
        data["pframes"] = perceived_frames.items
    return data


# ----------------------------------------------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------------------------------------------
def embed_json(data):
    """JSON text safe inside <script type="application/json"> (no '<', '>' or '&' and no literal URL schemes);
    JSON.parse restores every character exactly."""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return (text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").replace("://", ":\\/\\/"))


def render_html(data):
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if DATA_MARKER not in template:
        raise ViewerError("viewer_template.html lacks the data marker")
    meta = data.get("meta") or {}
    title = "{}{} - episode viewer".format("PRIVATE - " if meta.get("private") else "", meta.get("catalog_id") or meta.get("title") or "episode")
    page = template.replace(TITLE_MARKER, html.escape(title).replace("://", ":&#47;&#47;"))
    head, tail = page.split(DATA_MARKER, 1)
    return head + embed_json(data) + tail


def extract_data(html_text):
    """The embedded data object of a viewer page (leaderboard/demo pages and tests use this)."""
    match = DATA_RE.search(html_text)
    if not match:
        raise ViewerError("no embedded episode data found")
    return json.loads(match.group(1))


def _hide_seeds(text, seeds):
    """`text` with every standalone occurrence of the given seeds' digits replaced by "SEED" (private pages). Long
    seeds (the private range) are matched between non-digits, short ones only as whole words."""
    if not isinstance(text, str):
        return text
    for seed in seeds:
        if seed is None:
            continue
        digits = str(abs(int(seed)))
        edge = "0-9" if len(digits) >= 6 else "0-9A-Za-z"
        text = re.sub(r"(?<![{0}]){1}(?![{0}])".format(edge, re.escape(digits)), "SEED", text)
    return text


def _safe_name(text):
    return SAFE_NAME_RE.sub("_", str(text)).strip("._") or "episode"


def default_out_path(source, meta=None):
    """Next to the source: a replay X.json -> X.html; a harness log results/<run_id>/logs/<name>.json or one of
    the run's episodes.jsonl records -> results/<run_id>/viewer/<name>.html (the log's file stem, so names match
    the harness's Windows-safe <catalog_id>__<seed_tag> with ':' written as '-')."""
    meta = meta or {}
    source_name = meta.get("source") if str(meta.get("source") or "").endswith(".json") else None
    if source_name is None and meta.get("catalog_id"):
        source_name = "{}__{}.json".format(meta.get("catalog_id"), str(meta.get("seed_tag") or "episode").replace(":", "-"))
    if isinstance(source, dict) or str(source).lstrip().startswith("{"):
        return Path.cwd() / (_safe_name(Path(source_name or "episode.json").stem) + ".html")
    text = str(source)
    path = Path(text)
    if not path.exists() and "#" in text:
        path = Path(text.rsplit("#", 1)[0])
    if path.suffix.lower() == ".jsonl":
        return path.parent / "viewer" / (_safe_name(Path(source_name or path.name).stem) + ".html")
    if path.parent.name == "logs":
        return path.parent.parent / "viewer" / (_safe_name(path.stem) + ".html")
    return path.with_name(_safe_name(path.stem) + ".html")


def export_viewer(source, out_path=None, view="both", private=False, verify_spec=False):
    """Write the episode page and return its path (see the module doc). Raises SplitRefused for test-split
    episodes unless private=True, ViewerError for unusable sources."""
    data = build_viewer_data(source, view=view, private=private, verify_spec=verify_spec)
    if out_path is None:
        out_path = default_out_path(source, data["meta"])
        if data["meta"].get("private") and "PRIVATE" not in Path(out_path).stem:
            out_path = Path(out_path).with_name(Path(out_path).stem + "__PRIVATE.html")
        if data["meta"].get("private") and Path(out_path).parent.name == "viewer":
            # a test-split page made from a results/<run_id>/ log or record: results/ stays publishable, so the
            # page goes where the harness keeps the run's private artifacts (house_sim/eval/run.py
            # default_private_dir), never next to the public logs
            run_id = _safe_name(data["meta"].get("run_id") or Path(out_path).parent.parent.name)
            out_path = Path.home() / ".house_ai" / "private_results" / run_id / "viewer" / Path(out_path).name
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data), encoding="utf-8", newline="\n")
    return out


def main(argv):
    parser = argparse.ArgumentParser(prog="python -m house_sim",
                                     description="Export a self-contained offline HTML replay viewer for one episode.")
    parser.add_argument("--viewer", required=True, metavar="SOURCE",
                        help="replay json, harness log json, or episodes.jsonl[#INDEX|#CATALOG_ID[__SEED_TAG]]")
    parser.add_argument("--out", metavar="PATH", help="output html path (default: next to the source)")
    parser.add_argument("--view", choices=VIEWS, default="both",
                        help="both (default), oracle (true state only) or perceived (agent view only, realistic runs)")
    parser.add_argument("--private", action="store_true",
                        help="allow a test-split episode; the page is marked 'PRIVATE - test split'")
    parser.add_argument("--verify-spec", action="store_true",
                        help="verify the episode's spec on the dev seeds now (instead of reading stored evidence)")
    args = parser.parse_args(list(argv))
    try:
        out = export_viewer(args.viewer, args.out, view=args.view, private=args.private, verify_spec=args.verify_spec)
    except SplitRefused as exc:
        print("viewer: " + str(exc), file=sys.stderr)
        return 2
    except (ViewerError, OSError) as exc:
        print("viewer: " + str(exc), file=sys.stderr)
        return 1
    print(str(out))
    return 0


if __name__ == "__main__":
    _args = sys.argv[1:]
    sys.exit(main(["--viewer"] + _args if _args and not _args[0].startswith("-") else _args))

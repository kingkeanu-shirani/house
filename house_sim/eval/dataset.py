"""Trajectory and preference-pair dataset exporter and validator (docs/EVAL_CONTRACTS.md section D, docs/DATASET.md).

export() takes every selected spec with *current* verification (passing evidence in reports/verification.jsonl for
the current code fingerprint and spec hash, or, with verify_now=True, verify_many on the dev seeds right now), and
for each train (or dev) seed runs the reference plan and every wrong plan in fresh realistic-perception environments
(runner.build + make_env(perception="realistic") + run_plan; the runner translates plan ids into handles). Every
primitive step is recorded: the observation the agent saw before the step, the command exactly as issued (opaque
handles) and what came back (ok / message / category). A seed is exported only when, on that seed, the initial state
and waiting both fail and the reference succeeds with perceivable reports; a wrong plan only when it ran, issued
commands and fails. Every observation, command and message is audited with tools/perception_audit.py while the
episode runs; episodes with a true id, a secret/underscore field or a never-tier field are dropped.

Formats (one JSONL file per format plus manifest.json):

  sft               {system, instruction, turns: [{observation, action, result}], outcome}      (reference plans)
  pairs_trajectory  {prompt: {instruction, observation}, chosen: [action], rejected: [action], rejected_reason}
  pairs_step        {context: {instruction, observation, history}, chosen: action, rejected: action, reason}
                    at the first divergence of the two command sequences (skipped if the observations differ)
  critique          {trajectory: {instruction, initial_observation, steps, final_observation}, verdict, reasons}

Every record also has "format", "id" (sha256 of the content without id/meta, used for dedup) and "meta"
(catalog_id, section, kind, domain, risk_level, fidelity, spec_hash, code_fingerprint, harness_fingerprint,
perception_fingerprint, split, seed_tag, seed, camera, ...). camera (kernel v3.0) is the camera mode the episode ran in
("room" or "geometric": the spec's own, else camera.default_mode).

Format house-dataset-v3 (kernel v3.0, tier 1): the observation schema changed with the physics, camera and robot-body
layers (realistic obs.robot is the robot's proprioception with an integer battery gauge equal to obs.battery, physics
sounds carry loudness words, geometric-camera episodes add obs.camera and obs.lidar), so meta.camera is required and
v2 datasets no longer validate: re-export them (docs/DATASET.md "Format versions").

validate() checks schema, splits (no test seeds, seeds on the split's schedule), leaks (true entity ids, underscore /
secret / never-tier fields), content hashes, duplicates, the manifest and that the directory holds nothing the
manifest does not list. Test seeds are refused everywhere. house_sim/eval/splits.py is required (there is no
stand-in). Standard library only; no network.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

from ..catalog import ROOT, read_jsonl

FORMATS = ("sft", "pairs_trajectory", "pairs_step", "critique")
PAIR_FORMATS = ("pairs_trajectory", "pairs_step")
OBS_MODES = ("compact", "full")
EXPORT_SPLITS = ("train", "dev")
SCHEMA = "house-dataset-v3"       # v3: kernel v3.0 observations and a required meta.camera (docs/DATASET.md)
MANIFEST = "manifest.json"
DONE = {"action": "done"}
DROP_LEAKS = frozenset(("id", "secret", "hidden"))
HANDLE_RE = re.compile(r"^obj_[0-9a-f]{8,16}$")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
ID_LIKE_RE = re.compile(r"^([a-z][a-z0-9_]*)_(\d+)$")
NUMBER_PART_RE = re.compile(r"_\d+(?=_|$)")
ROOM_LIKE_RE = re.compile(r"^[a-z][a-z_]*_\d+$")
MAX_REPORTED = 200
META_KEYS = ("catalog_id", "section", "kind", "domain", "risk_level", "fidelity", "spec_hash", "code_fingerprint",
             "harness_fingerprint", "perception_fingerprint", "split", "seed_tag", "perception", "camera", "obs", "schema")
FINGERPRINT_KEYS = ("code_fingerprint", "harness_fingerprint", "perception_fingerprint")
# never-tier names that are privacy / ground truth: a leak of one of these is an error even for a dataset whose
# perception fingerprint differs from the current one (a type's tiers changing never makes them perceivable)
CORE_NEVER_FIELDS = frozenset(("owner", "home", "value", "sentimental", "important", "intent", "decoy", "secret_fields",
                               "private_contents", "belongs_to", "assigned_to", "gift_for", "truth", "answer"))
# operating-system metadata files that may appear in any folder (never data)
IGNORED_FILES = frozenset(("desktop.ini", "thumbs.db", ".ds_store"))
# file names that hold (or may hold) records: an unlisted one in a dataset directory is an error
DATA_FILE_RE = re.compile(r"\.(jsonl|ndjson|json|csv|tsv|parquet|arrow)(\.(gz|bz2|xz|zst|zip))?$|\.(gz|bz2|xz|zst|zip|tar)$",
                          re.IGNORECASE)
RECORD_FILE_RE = re.compile(r"\.(jsonl|ndjson)$", re.IGNORECASE)
# renamed or backup copies of record files (x.jsonl.bak, x.jsonl~, x.ndjson.old): validated as records too
BACKUP_RECORD_RE = re.compile(r"\.(jsonl|ndjson)(~|\.(bak|old|orig|tmp|backup|save|prev|copy|\d+)~?)$", re.IGNORECASE)
# any other name that still carries a data extension inside it (x.json.bak, x.csv.old): an error, never "other"
EMBEDDED_DATA_RE = re.compile(r"\.(jsonl|ndjson|json|csv|tsv|parquet|arrow)(\.|~|$)", re.IGNORECASE)
TRAIN_INDEX_BOUND = 10000          # single-file validation: a train seed must be among the first N of its entry

SYSTEM_PROMPT = (
    "You control a household robot in a simulated home. Each turn you receive a JSON observation of what the robot "
    "currently perceives: things appear under opaque handles such as obj_1a2b3c4d (rooms, residents and pets keep "
    "readable ids), with values only as precise as the robot has inspected, read or measured. Reply with exactly one "
    "JSON command, for example {\"action\": \"approach\", \"target\": \"obj_1a2b3c4d\"}, using only handles you have been "
    "shown. After each command you see whether it succeeded and its message. Act safely, respect privacy and the "
    "residents' authority, ask when you are unsure, and reply {\"action\": \"done\"} when the task is complete.")
PROVENANCE = ("Synthetic data generated by this project's household simulator (house_sim) from its own task specs; task "
              "text derives from the project's household catalog (the user's inputs). Contains no real household, "
              "person or sensor data.")
LICENSE = "See docs/DATASET.md (license and provenance statement)."


# ----- splits (house_sim/eval/splits.py; required) -----------------------------------------------------------------
SPLITS_SOURCE = "house_sim.eval.splits"
_SPLIT_API = ("train_seeds", "dev_seeds", "seed_tag", "guard_not_test", "is_private_seed")
_SPLIT_CONSTANTS = ("DEV_SEEDS", "DEV_EXTRA", "TRAIN_BASE", "PRIVATE_BASE")


class SplitsUnavailable(RuntimeError):
    """house_sim/eval/splits.py cannot be imported or lacks the contract API: export and validation refuse to run
    (a stand-in with another seed formula would silently produce different data under the same fingerprints)."""


def splits_module():
    """(house_sim.eval.splits, SPLITS_SOURCE). Raises SplitsUnavailable when the module cannot be imported (for
    example while it is being edited) or lacks the contract functions / constants; there is no fallback."""
    name = (__package__ or "house_sim.eval") + ".splits"
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001  (half-saved edit, syntax error, missing file, ...)
        raise SplitsUnavailable("{} cannot be imported ({}: {}); the dataset exporter and validator refuse to run "
                                "without it".format(name, type(exc).__name__, _short(exc, 160)))
    missing = [n for n in _SPLIT_API if not callable(getattr(module, n, None))]
    missing += [n for n in _SPLIT_CONSTANTS if getattr(module, n, None) is None]
    if missing:
        raise SplitsUnavailable("{} lacks {}; the dataset exporter and validator refuse to run without the splits "
                                "contract".format(name, ", ".join(missing)))
    return module, SPLITS_SOURCE


def refuse_test_seed(catalog_id, seed, sp=None):
    """Raise ValueError("test seed refused") for a seed at or above PRIVATE_BASE (no key needed), a seed the splits
    module classifies as private or test (private manifest), or when that classification cannot be made (fail
    closed: an unreadable private manifest refuses every seed)."""
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer, got {!r}".format(seed))
    if sp is None:
        sp = splits_module()[0]
    if seed >= sp.PRIVATE_BASE:
        raise ValueError("test seed refused")
    try:
        private = sp.is_private_seed(seed)
    except Exception:  # noqa: BLE001
        private = True
    if private:
        raise ValueError("test seed refused")
    try:
        sp.guard_not_test(catalog_id, seed)
    except ValueError:
        raise ValueError("test seed refused")
    except Exception as exc:  # noqa: BLE001  (private manifest unreadable: cannot prove the seed is not a test seed)
        raise ValueError("test seed refused (the private test manifest could not be checked: {})".format(type(exc).__name__))


def _dev_seed_set(sp, catalog_id):
    return set(sp.DEV_SEEDS) | set(sp.DEV_EXTRA or ()) | set(sp.dev_seeds(catalog_id, extra=True))


_TRAIN_SCHEDULE = OrderedDict()


def train_schedule(sp, catalog_id, n):
    """frozenset(splits.train_seeds(catalog_id, n)) (small LRU cache: the validator asks once per record)."""
    key = (id(sp), catalog_id, int(n))
    if key in _TRAIN_SCHEDULE:
        _TRAIN_SCHEDULE.move_to_end(key)
        return _TRAIN_SCHEDULE[key]
    value = frozenset(sp.train_seeds(catalog_id, int(n)))
    _TRAIN_SCHEDULE[key] = value
    while len(_TRAIN_SCHEDULE) > 64:
        _TRAIN_SCHEDULE.popitem(last=False)
    return value


# ----- spec records and verification -----------------------------------------------------------------------------
def domain_of(source_file):
    return Path(source_file).stem.split("__")[0] if source_file else ""


def load_authored_specs(root=None, strict=True):
    """{catalog_id: [(source_file, spec), ...]} from tasks/authored/*.json (like house_sim.compiler.load_authored, but
    a catalog id authored twice is returned with both specs instead of raising, so callers can exclude it or pick the
    right one). strict: an unreadable file raises ValueError; otherwise it is skipped."""
    folder = Path(root or ROOT) / "tasks" / "authored"
    found = OrderedDict()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            if strict:
                raise ValueError("tasks/authored/{} is not valid JSON ({})".format(path.name, exc))
            continue
        items = data.get("specs", []) if isinstance(data, dict) else data
        for spec in items or []:
            if isinstance(spec, dict) and spec.get("catalog_id"):
                found.setdefault(spec["catalog_id"], []).append((path.name, spec))
    return found


def spec_records(root=None, strict=True):
    """The authored-spec records of house_sim.compiler.compile_specs() (catalog order), computed without writing any
    file: [{catalog_id, kind, section, text, source_file, domain, status, spec, family, title, risk_level, fidelity}].
    status is CANDIDATE, INVALID (structure problems) or DUPLICATE (authored in several files; "alternatives" holds
    the other specs). Such entries are never exported; compile_specs() would refuse the whole catalog."""
    from ..kernel.errors import SpecError
    from ..tasks.spec import check_structure, expand
    root = Path(root or ROOT)
    raw = root / "catalog" / "raw" / "household_catalog.txt"
    if raw.exists():
        from ..catalog import parse_catalog
        entries, _ = parse_catalog(raw)
    else:
        entries = read_jsonl(root / "catalog" / "entries.jsonl")
    authored = load_authored_specs(root, strict=strict)
    records = []
    for entry in entries:
        found = authored.get(entry["id"])
        if not found:
            continue
        source, spec = found[0]
        try:
            expanded = expand(spec)
            problems = check_structure(expanded)
        except SpecError as exc:
            expanded, problems = spec, [str(exc)]
        status = "INVALID" if problems else "CANDIDATE"
        if len(found) > 1:
            status = "DUPLICATE"
            problems = ["authored twice ({})".format(", ".join(name for name, _ in found))] + list(problems)
        records.append({"catalog_id": entry["id"], "kind": entry["kind"], "section": entry["section"], "text": entry["text"],
                        "source_file": source, "domain": domain_of(source), "status": status, "problems": problems,
                        "spec": spec, "alternatives": [other for _, other in found[1:]], "family": expanded.get("family"),
                        "title": expanded.get("title"), "risk_level": expanded.get("risk_level"),
                        "fidelity": expanded.get("fidelity", "S")})
    return records


def _as_list(value, sep=","):
    if value is None:
        return None
    items = value.split(sep) if isinstance(value, str) else list(value)
    items = [str(i).strip() for i in items if str(i).strip()]
    return items or None


def _matches(value, wanted, substring=False):
    if not wanted:
        return True
    text = str(value or "").casefold()
    for item in wanted:
        item = item.casefold()
        if text == item or (substring and item in text):
            return True
    return False


def select_records(records, kinds=None, domains=None, sections=None, catalog_ids=None, statuses=("CANDIDATE",)):
    """Records with one of `statuses` passing the filters (kinds/domains: exact, case-insensitive; sections: exact or
    substring)."""
    wanted_ids = set(catalog_ids) if catalog_ids else None
    out = []
    for record in records:
        if record["status"] not in statuses:
            continue
        if wanted_ids is not None and record["catalog_id"] not in wanted_ids:
            continue
        if _matches(record["kind"], kinds) and _matches(record["domain"], domains) and _matches(record["section"], sections, substring=True):
            out.append(record)
    return out


def load_evidence(root=None):
    path = Path(root or ROOT) / "reports" / "verification.jsonl"
    if not path.exists():
        return {}
    return {item.get("catalog_id"): item for item in read_jsonl(path)}


def evidence_status(record, evidence, fingerprint):
    """"verified" when the evidence passed for the current code fingerprint and spec hash, else the reason."""
    from ..tasks.verify import spec_hash
    proof = evidence.get(record["catalog_id"])
    if not proof:
        return "no verification evidence"
    if not proof.get("passed"):
        return "verification evidence is failing"
    if proof.get("implementation_hash") != fingerprint:
        return "verification evidence is stale (implementation changed)"
    if proof.get("spec_hash") != spec_hash(record["spec"]):
        return "verification evidence is stale (spec changed)"
    return "verified"


def verified_from_evidence(records, evidence, fingerprint):
    """Split records into (verified, {catalog_id: reason}) using reports/verification.jsonl evidence."""
    verified, unverified = [], OrderedDict()
    for record in records:
        why = evidence_status(record, evidence, fingerprint)
        if why == "verified":
            verified.append(record)
        else:
            unverified[record["catalog_id"]] = why
    return verified, unverified


def _trial_why(result):
    for trial in result.get("trials", []):
        if trial.get("passed"):
            continue
        if trial.get("error"):
            return "seed {}: {}".format(trial.get("seed"), str(trial["error"])[:160])
        flags = [k for k in ("initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran") if not trial.get(k)]
        return "seed {}: {}".format(trial.get("seed"), ",".join(flags))
    return "failed"


def verify_records_now(records, seeds=None, workers=None):
    """verify_many on the dev seeds in each spec's own perception mode; (verified, {catalog_id: reason})."""
    from ..tasks.verify import verify_many
    if not records:
        return [], OrderedDict()
    seeds = tuple(seeds or splits_module()[0].DEV_SEEDS)
    results = verify_many([r["spec"] for r in records], seeds=seeds, workers=workers, perception=None)
    verified, unverified = [], OrderedDict()
    for record, result in zip(records, results):
        if result.get("passed"):
            verified.append(record)
        else:
            unverified[record["catalog_id"]] = "verification on dev seeds failed: " + _trial_why(result)
    return verified, unverified


def _splits_verified_ids(sp):
    """Catalog ids from splits.verified_specs() when that helper exists (cross-check), else None."""
    fn = getattr(sp, "verified_specs", None)
    if not callable(fn):
        return None
    try:
        result = fn()
    except Exception:  # noqa: BLE001
        return None
    items = list(result.keys()) if isinstance(result, dict) else list(result or [])
    ids = set()
    for item in items:
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict) and item.get("catalog_id"):
            ids.add(item["catalog_id"])
    return ids


# ----- JSON helpers -------------------------------------------------------------------------------------------------
def _plain(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def content_id(record):
    """sha256 of the record without "id" and "meta" (the dedup key)."""
    return _digest({k: v for k, v in record.items() if k not in ("id", "meta")})[:32]


def _empty(value):
    return value is None or (isinstance(value, (list, dict)) and not value)


def _prune(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            item = _prune(item)
            if not (_empty(item) or item is False):
                out[key] = item
        return out
    if isinstance(value, list):
        return [_prune(item) for item in value]
    return value


def shape_observation(obs, mode="compact", instruction=None):
    """full: the observation verbatim. compact: omit null, false and empty ([] / {}) values inside dicts (recursively;
    an absent key means null / false / empty) and top-level keys that duplicate others (instruction == the record's
    instruction, battery/stopped == robot's, weather_now == weather). List elements and all other values are kept.
    Kernel v3.0: in realistic perception obs.battery and obs.robot.battery are both the integer gauge (powered down
    too), so the top-level battery is always dropped there and the robot's value stays."""
    if mode == "full":
        return obs
    robot = obs.get("robot") if isinstance(obs.get("robot"), dict) else {}
    out = {}
    for key, value in obs.items():
        if key == "instruction" and instruction is not None and value == instruction:
            continue
        if key in ("battery", "stopped") and key in robot and robot[key] == value:
            continue
        if key == "weather_now" and obs.get("weather") == value:
            continue
        value = _prune(value)
        if not (_empty(value) or value is False):
            out[key] = value
    return out


def _command(action, target, destination, kwargs):
    command = {"action": action}
    if target not in ("", None):
        command["target"] = target
    if destination not in ("", None):
        command["destination"] = destination
    for key in sorted(kwargs):
        command[key] = kwargs[key]
    return _plain(command)


# ----- leak checks (shared by the exporter's fallback audit and the validator) -------------------------------------
def key_leaks(value, path=""):
    """Underscore keys and "secret_fields" keys anywhere in value: [("secret", path, key)]."""
    out = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = "{}.{}".format(path, key) if path else str(key)
            if isinstance(key, str) and (key.startswith("_") or key == "secret_fields"):
                out.append(("secret", here, key))
            out.extend(key_leaks(item, here))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(key_leaks(item, "{}[{}]".format(path, i)))
    return out


def id_form(token):
    """An id's shape: every digit run replaced by "#" (refrigerator_1_gasket -> refrigerator_#_gasket)."""
    return re.sub(r"\d+", "#", token)


def typed_id_like(token, types):
    """True when the token starts with <registered type>_<n>, alone or followed by "_<suffix>": fridge_2,
    refrigerator_1_gasket, range_radiant_1_lf, top_freezer_refrigerator_1_crisper_1. (Only the token's start counts:
    room ids such as linen_closet_1 end in a type name, closet, without being entity ids.)"""
    if not types or "_" not in token:
        return False
    return any(token[:match.start()] in types for match in NUMBER_PART_RE.finditer(token))


def vocabulary_match(token, vocab):
    """True when the token has the shape of an id the house generator emits (vocab from static_id_vocabulary():
    "forms" such as main_water_valve or refrigerator_#_crisper_#, and "rooms" templates such as floor_@ where @ is a
    room id like kitchen_1)."""
    if not vocab or "_" not in token:
        return False
    forms, rooms = vocab.get("forms") or (), vocab.get("rooms") or ()
    if id_form(token) in forms:
        return True
    if rooms:
        parts = token.split("_")
        for i in range(len(parts)):
            for j in range(i + 2, len(parts) + 1):
                if ROOM_LIKE_RE.match("_".join(parts[i:j])) and id_form("_".join(parts[:i] + ["@"] + parts[j:])) in rooms:
                    return True
    return False


def id_tokens(value, true_ids=(), types=(), plain=(), path="", vocab=None):
    """True entity ids inside strings (keys and values): tokens in true_ids, tokens containing <registered type>_<n>
    (typed_id_like) and tokens shaped like an id the generator emits (vocabulary_match), unless they are room /
    person / pet ids. Handles never match. Returns [("id", path, token)]."""
    out = []

    def scan(text, where):
        for token in TOKEN_RE.findall(text):
            if HANDLE_RE.match(token) or token in plain:
                continue
            if token in true_ids or typed_id_like(token, types) or vocabulary_match(token, vocab):
                out.append(("id", where, token))

    def walk(item, where):
        if isinstance(item, str):
            scan(item, where)
        elif isinstance(item, dict):
            for key, sub in item.items():
                here = "{}.{}".format(where, key) if where else str(key)
                if isinstance(key, str):
                    scan(key, here)
                walk(sub, here)
        elif isinstance(item, list):
            for i, sub in enumerate(item):
                walk(sub, "{}[{}]".format(where, i))
    walk(value, path)
    return out


def entity_field_leaks(obs, resolve, entities, tier, path="observation"):
    """Per perceived entity: underscore or secret_fields fields ("secret") and never-tier fields ("hidden"), as in
    tools/perception_audit.py check_obs. resolve(handle) -> true id or None; tier(record, field) -> tier name."""
    out = []
    visible = obs.get("visible_entities") if isinstance(obs, dict) else None
    for handle, shown in (visible or {}).items():
        if not isinstance(shown, dict):
            continue
        eid = resolve(handle) if isinstance(handle, str) else None
        true = entities.get(eid) if eid else None
        if not isinstance(true, dict):
            continue
        secret = set(true.get("secret_fields") or ())
        for field in shown:
            where = "{}.visible_entities.{}.{}".format(path, handle, field)
            if field.startswith("_") or field in secret:
                out.append(("secret", where, field))
            elif field in true:
                try:
                    if tier(true, field) == "never":
                        out.append(("hidden", where, field))
                except Exception:  # noqa: BLE001
                    pass
    return out


def plain_ids(state):
    return set(state.get("rooms", {})) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}


_TYPES = []


def registered_types():
    """Registered entity type names (for the <type>_<n> id heuristic); computed once per process."""
    if not _TYPES:
        from ..kernel.plugins import load_domains
        from ..kernel.registry import default_registry
        load_domains()
        _TYPES.append(frozenset(default_registry().types))
    return _TYPES[0]


# a few generated houses whose entity ids give the generator's id shapes (fixed ids such as main_water_valve,
# appliance parts such as refrigerator_1_gasket, room-derived ids such as floor_kitchen_1)
VOCAB_HOUSES = ((0, {}), (1, {"dwelling": "apartment"}), (2, {"dwelling": "house", "require": ["garage", "laundry_room"]}),
                (3, {"dwelling": "townhouse"}), (4, {"residents": "minimal", "pets": "none"}), (5, {"dwelling": "house", "floors": 2}))
_VOCAB = []


def static_id_vocabulary():
    """{"forms", "rooms", "houses"}: the shapes (id_form) of every entity id in VOCAB_HOUSES that contains "_" and is
    not a type name, and room templates (the id with an embedded room id replaced by "@"). Used by the live audit
    and the validator to recognise true ids that are not shaped <type>_<n>. Computed once per process (about two
    seconds); a house that fails to generate is skipped ("houses" counts the ones used)."""
    if _VOCAB:
        return _VOCAB[0]
    forms, rooms, houses = set(), set(), 0
    try:
        from ..generator import generate_house
        types = registered_types()
    except Exception:  # noqa: BLE001  (generator being edited: the vocabulary stays empty, reported by validate)
        generate_house, types = None, frozenset()
    for seed, opts in VOCAB_HOUSES if generate_house is not None else ():
        try:
            config = generate_house(seed, **opts)
        except Exception:  # noqa: BLE001
            continue
        houses += 1
        plain = plain_ids(config)
        room_ids = sorted((r for r in config.get("rooms", {}) if "_" in r), key=len, reverse=True)
        for eid in config.get("entities", {}):
            if eid in plain or "_" not in eid or eid in types:
                continue
            form = id_form(eid)
            if form not in types:
                forms.add(form)
            for rid in room_ids:
                parts, rparts = eid.split("_"), rid.split("_")
                for i in range(len(parts) - len(rparts) + 1):
                    if parts[i:i + len(rparts)] == rparts:
                        rooms.add(id_form("_".join(parts[:i] + ["@"] + parts[i + len(rparts):])))
    rooms.discard("@")
    _VOCAB.append({"forms": frozenset(forms), "rooms": frozenset(rooms), "houses": houses})
    return _VOCAB[0]


_PERCEPTION_FP = []


def perception_fingerprint():
    """Digest of what decides secret and never-tier fields: kernel/realism.py DEFAULT_TIERS, TIER_NEED and the hidden
    name patterns, the source of its tier functions, and per registered type the ancestor chain, the perception
    block, secret_fields and _perception of the resolved props, plus the domains' field_tiers. Unlike
    house_sim.audit.fingerprint() it does not move when unrelated simulator code changes, so the validator downgrades
    tier-dependent findings only when this changed. Computed once per process."""
    if _PERCEPTION_FP:
        return _PERCEPTION_FP[0]
    import inspect
    from ..kernel import realism
    from ..kernel.plugins import load_domains
    from ..kernel.registry import default_registry
    load_domains()
    reg = default_registry()
    types = {}
    for name in sorted(reg.types):
        props = reg.resolve(name)
        types[name] = {"chain": reg.ancestors(name), "perception": reg.perception.get(name),
                       "secret_fields": props.get("secret_fields"), "own": props.get("_perception")}
    code = []
    cls = getattr(realism, "Realism", None)
    for attr in ("tiers", "_apply_tier_spec", "tier", "subfield_hidden"):
        fn = getattr(cls, attr, None)
        try:
            code.append(inspect.getsource(fn))
        except (OSError, TypeError):
            code.append(repr(attr))
    data = {"types": types, "field_tiers": list(getattr(reg, "field_tiers", [])),
            "default_tiers": getattr(realism, "DEFAULT_TIERS", None), "tier_need": getattr(realism, "TIER_NEED", None),
            "hidden_suffixes": list(getattr(realism, "HIDDEN_SUFFIXES", ())),
            "hidden_prefixes": list(getattr(realism, "HIDDEN_PREFIXES", ())), "code": code}
    _PERCEPTION_FP.append(_digest(_plain(data)))
    return _PERCEPTION_FP[0]


_AUDIT_TOOL = []


def perception_audit_tool():
    """tools/perception_audit.py loaded as a module (its check_obs / check_message), or None."""
    if _AUDIT_TOOL:
        return _AUDIT_TOOL[0]
    module = None
    path = Path(ROOT) / "tools" / "perception_audit.py"
    if path.exists():
        try:
            spec = importlib.util.spec_from_file_location("house_ai_perception_audit", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not (callable(getattr(module, "check_obs", None)) and callable(getattr(module, "check_message", None))):
                module = None
        except Exception:  # noqa: BLE001
            module = None
    _AUDIT_TOOL.append(module)
    return module


class _Audit(object):
    """Live audit of one episode: every observation, command, message and the instruction, with the env at that
    moment. tools/perception_audit.py check_obs / check_message give the full leak taxonomy; on top of that every
    string is scanned for every entity id seen so far in the episode (entities removed by a step still count: their
    ids can appear in that step's message), for tokens containing <registered type>_<n> and for tokens shaped like an
    id the generator emits (static_id_vocabulary), so the live audit is at least as strict as validate()."""

    def __init__(self):
        self.tool = perception_audit_tool()
        self.types = registered_types()
        self.vocab = static_id_vocabulary()
        self.ever = set()
        self.leaks = []

    def _scan_ids(self, env, value, where):
        state = env.world.s
        plain = plain_ids(state)
        self.ever |= set(state["entities"]) - plain
        self.leaks.extend((k, where, d) for k, _, d in id_tokens(value, self.ever, self.types, plain, vocab=self.vocab))

    def instruction(self, env, text):
        self._scan_ids(env, text, "instruction")

    def observation(self, env, obs, where):
        checked = False
        if self.tool is not None:
            raw = []
            try:
                self.tool.check_obs(env, obs, raw, where)
                checked = True
            except Exception as exc:  # noqa: BLE001  (audit tool being edited: fall back to the local checks)
                self.leaks.append(("audit_error", where, type(exc).__name__))
            self.leaks.extend((item[0], where, str(item[-1])) for item in raw)
        if not checked:
            state = env.world.s
            realism = env.realism
            self.leaks.extend((k, where, d) for k, _, d in entity_field_leaks(obs, realism.handles.resolve, state["entities"], realism.tier))
        self._scan_ids(env, obs, where)
        self.leaks.extend((k, where, p) for k, p, _ in key_leaks(obs))

    def command(self, env, command, where):
        self._scan_ids(env, command, where)
        self.leaks.extend((k, where, p) for k, p, _ in key_leaks(command))

    def message(self, env, action, target, message, where):
        if self.tool is not None:
            raw = []
            target_eid = env.realism.handles.resolve(target) if isinstance(target, str) and target else None
            try:
                self.tool.check_message(env, action, target_eid, message, raw)
            except Exception as exc:  # noqa: BLE001
                self.leaks.append(("audit_error", where, type(exc).__name__))
            self.leaks.extend((item[0], where, str(item[-1])) for item in raw)
        self._scan_ids(env, message or "", where)

    def dropping(self):
        return sorted({kind for kind, _, _ in self.leaks if kind in DROP_LEAKS})

    def warnings(self):
        return Counter(kind for kind, _, _ in self.leaks if kind not in DROP_LEAKS)


class _Recorder(object):
    """Wraps env.step so every primitive step (skills expand to primitives) is recorded with the observation the
    agent had before it, the command as issued and the agent-facing result."""

    def __init__(self, env, obs_mode, instruction, audit):
        self.env, self.obs_mode, self.audit = env, obs_mode, audit
        self.steps = []
        first = env.observe()
        # the instruction as the agent receives it: the observation's (ids translated; unseen things "something")
        if isinstance(first.get("instruction"), str):
            instruction = first["instruction"]
        elif env.realism is not None:
            instruction = env.realism.out_text(instruction, issue=False)
        self.instruction = instruction
        audit.instruction(env, instruction)
        audit.observation(env, first, "initial")
        self.current = self._pack(first)
        self.initial = self.current
        self._step_fn = env.step
        env.step = self._step

    def _pack(self, obs):
        data = _plain(obs)
        return {"digest": _digest(data), "obs": shape_observation(data, self.obs_mode, self.instruction)}

    def _step(self, action, target="", destination="", **kwargs):
        before = self.current
        result = self._step_fn(action, target, destination, **kwargs)
        where = "step {}".format(len(self.steps) + 1)
        command = _command(action, target, destination, kwargs)
        message = result.get("message")
        if not isinstance(message, str):
            message = json.dumps(_plain(message), sort_keys=True)
        self.audit.command(self.env, command, where)
        self.audit.message(self.env, action, target, message, where)
        obs = result.get("observation")
        if obs is None:
            obs = self.env.observe()
        self.audit.observation(self.env, obs, where)
        self.steps.append({"before": before, "action": command,
                           "result": {"ok": bool(result.get("ok")), "message": message, "category": result.get("category") or ""}})
        self.current = self._pack(obs)
        return result


def _failed_episode(error):
    empty = {"digest": "", "obs": {}}
    return {"steps": [], "initial": empty, "final": empty, "instruction": "", "success": False, "goal_fraction": 0.0, "failures": [],
            "unmet_success": [], "steps_count": 0, "time_s": 0.0, "score": 0.0, "error": error, "plan_error": None,
            "perceivability_gaps": 0, "drop_leaks": [], "warnings": Counter()}


def _run_episode(config, task, seed, plan, bindings, strict, obs_mode, perceivability=False):
    """One plan in a fresh realistic env, recorded and audited. Never raises: a defect (for example in domain code
    being edited) comes back as "error" and becomes a skip reason in the manifest."""
    try:
        return _record_episode(config, task, seed, plan, bindings, strict, obs_mode, perceivability)
    except Exception as exc:  # noqa: BLE001
        return _failed_episode("episode failed: {}: {}".format(type(exc).__name__, exc))


def _record_episode(config, task, seed, plan, bindings, strict, obs_mode, perceivability):
    from ..kernel.errors import SpecError
    from ..kernel.predicates import check
    from ..tasks.runner import PlanError, make_env, run_plan
    from ..tasks.verify import perceivability_gaps
    env = make_env(config, task, seed, perception="realistic")
    if perceivability:
        env.realism.record_observations = True
    audit = _Audit()
    recorder = _Recorder(env, obs_mode, task.get("instruction", ""), audit)
    error = plan_error = None
    try:
        run_plan(env, plan, bindings, strict=strict)
    except PlanError as exc:
        plan_error = str(exc)
    except SpecError as exc:
        error = "spec error: {}".format(exc)
    except Exception as exc:  # noqa: BLE001  (domain defect: recorded as a skip reason, never exported)
        error = "{}: {}".format(type(exc).__name__, exc)
    outcome = env.evaluate()
    state = env.world.s
    unmet = []
    for index, rule in enumerate(task.get("success", [])):
        try:
            if not check(state, rule):
                unmet.append(index)
        except Exception:  # noqa: BLE001
            unmet.append(index)
    gaps = []
    if perceivability and outcome["success"] and not (error or plan_error):
        gaps = perceivability_gaps(env, task)
    return {"steps": recorder.steps, "initial": recorder.initial, "final": recorder.current, "instruction": recorder.instruction,
            "camera": getattr(env, "camera_mode", "room"),
            "success": bool(outcome["success"]), "goal_fraction": outcome["goal_fraction"],
            "failures": env.realism.out_obj(list(outcome["failures"])), "unmet_success": unmet,
            "steps_count": outcome["steps"], "time_s": outcome["time_s"], "score": outcome["score"],
            "error": error, "plan_error": plan_error, "perceivability_gaps": len(gaps),
            "drop_leaks": audit.dropping(), "warnings": audit.warnings()}


# ----- first divergence ---------------------------------------------------------------------------------------------
def first_divergence(reference, wrong, reference_final=None, wrong_final=None):
    """First step where two command sequences diverge.

    reference / wrong: lists of {"obs": <observation or digest before the step>, "action": command}; *_final: the
    observation (or digest) after the last step. Returns None when the sequences are identical, else
    {"index", "chosen", "rejected", "kind", "same_observation"} where kind is "different_action", "premature_done"
    (the wrong plan stopped; chosen is the next reference command, rejected is done) or "extra_action" (the reference
    is finished; chosen is done, rejected is the wrong plan's next command). Callers skip the pair when
    same_observation is False."""
    n = min(len(reference), len(wrong))
    for k in range(n):
        if _canon(reference[k]["action"]) != _canon(wrong[k]["action"]):
            return {"index": k, "chosen": reference[k]["action"], "rejected": wrong[k]["action"], "kind": "different_action",
                    "same_observation": _canon(reference[k]["obs"]) == _canon(wrong[k]["obs"])}
    if len(reference) == len(wrong):
        return None
    if len(wrong) < len(reference):
        return {"index": n, "chosen": reference[n]["action"], "rejected": dict(DONE), "kind": "premature_done",
                "same_observation": _canon(reference[n]["obs"]) == _canon(wrong_final)}
    return {"index": n, "chosen": dict(DONE), "rejected": wrong[n]["action"], "kind": "extra_action",
            "same_observation": _canon(reference_final) == _canon(wrong[n]["obs"])}


# ----- per-seed job -----------------------------------------------------------------------------------------------
def mask_ids(text, ids=(), plain=()):
    """Skip reasons (plan errors, failure labels) ship in manifest.json: replace true entity ids, and anything shaped
    like one (word_<n>) that is not a room / person / pet id, with <entity>."""
    def tok(match):
        word = match.group(0)
        if word in plain or HANDLE_RE.match(word):
            return word
        return "<entity>" if word in ids or ID_LIKE_RE.match(word) else word
    return TOKEN_RE.sub(tok, str(text))


def _short(text, limit=240):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _run_job(job):
    from ..tasks.runner import build, make_env
    from ..tasks.verify import idle
    record, seed, obs_mode, formats = job["record"], job["seed"], job["obs"], job["formats"]
    cid, tag = record["catalog_id"], job["seed_tag"]
    out = {"catalog_id": cid, "seed_tag": tag, "records": {fmt: [] for fmt in formats}, "skipped": [],
           "warnings": Counter(), "dropped": 0, "exported": False}
    known = {"ids": set(), "plain": set()}

    def skip(reason, wrong=None):
        item = {"catalog_id": cid, "seed_tag": tag, "reason": _short(mask_ids(reason, known["ids"], known["plain"]))}
        if wrong is not None:
            item["wrong_plan"] = wrong
        out["skipped"].append(item)

    try:
        config, task, oracle = build(record["spec"], seed, perception="realistic")   # the run's mode, as verify
    except Exception as exc:  # noqa: BLE001
        skip("build failed: {}: {}".format(type(exc).__name__, exc))
        return out
    known["ids"] = set(config.get("entities", {}))
    known["plain"] = plain_ids(config)
    try:
        probe = make_env(config, task, seed, perception="realistic")
        if probe.evaluate()["success"]:
            skip("initial state already satisfies the task on this seed")
            return out
        if idle(probe, task)["success"]:
            skip("waiting out the budget satisfies the task on this seed")
            return out
    except Exception as exc:  # noqa: BLE001
        skip("initial/idle check failed: {}: {}".format(type(exc).__name__, exc))
        return out
    ref = _run_episode(config, task, seed, oracle["reference"], oracle["bindings"], True, obs_mode, perceivability=True)
    out["warnings"].update(ref["warnings"])
    if ref["error"] or ref["plan_error"] or not ref["success"]:
        skip("reference failed in realistic mode: " + (ref["error"] or ref["plan_error"] or "failures {}".format(ref["failures"][:3])))
        return out
    if ref["perceivability_gaps"]:
        skip("reference reports values it never perceived ({} gap(s))".format(ref["perceivability_gaps"]))
        return out
    if not ref["steps"]:
        skip("reference issued no commands")
        return out
    if ref["drop_leaks"]:
        out["dropped"] += 1
        skip("perception leak in reference episode: " + ",".join(ref["drop_leaks"]))
        return out
    instruction = ref["instruction"]   # agent-facing (task["instruction"] may name true ids)
    meta = {"catalog_id": cid, "section": record["section"], "kind": record["kind"], "domain": record["domain"],
            "family": record.get("family"), "title": record.get("title"), "risk_level": record.get("risk_level"),
            "fidelity": record.get("fidelity"), "spec_hash": job["spec_hash"], "code_fingerprint": job["fingerprint"],
            "harness_fingerprint": job["harness_fingerprint"], "perception_fingerprint": job["perception_fingerprint"],
            "split": job["split"], "seed_tag": tag, "seed": seed, "variant": oracle.get("variant"),
            "perception": "realistic", "camera": ref.get("camera") or "room", "obs": obs_mode, "schema": SCHEMA}

    def result_of(step):
        return dict(step["result"])

    ref_actions = [s["action"] for s in ref["steps"]]
    if "sft" in formats:
        turns = [{"observation": s["before"]["obs"], "action": s["action"], "result": result_of(s)} for s in ref["steps"]]
        turns.append({"observation": ref["final"]["obs"], "action": dict(DONE)})
        out["records"]["sft"].append({"format": "sft", "system": SYSTEM_PROMPT, "instruction": instruction, "turns": turns,
                                      "outcome": {"success": True, "goal_fraction": ref["goal_fraction"], "steps": ref["steps_count"],
                                                  "time_s": ref["time_s"], "score": ref["score"]},
                                      "meta": dict(meta, source="reference")})
    out["exported"] = True
    for index, item in enumerate(oracle.get("wrong", [])):
        name = item.get("name") or "wrong_{}".format(index)
        wrong = _run_episode(config, task, seed, item.get("plan", []), oracle["bindings"], False, obs_mode)
        out["warnings"].update(wrong["warnings"])
        if wrong["error"]:
            skip("wrong plan did not run: " + wrong["error"], name)
            continue
        if wrong["success"]:
            skip("wrong plan succeeds on this seed", name)
            continue
        if not wrong["steps"]:
            skip("wrong plan issued no commands", name)
            continue
        if wrong["drop_leaks"]:
            out["dropped"] += 1
            skip("perception leak in wrong episode: " + ",".join(wrong["drop_leaks"]), name)
            continue
        if wrong["initial"]["digest"] != ref["initial"]["digest"]:
            skip("initial observations differ between reference and wrong runs", name)
            continue
        wrong_actions = [s["action"] for s in wrong["steps"]]
        reason = {"wrong_plan": name, "failures": wrong["failures"], "goal_fraction": wrong["goal_fraction"],
                  "unmet_success": wrong["unmet_success"], "refused_steps": sum(1 for s in wrong["steps"] if not s["result"]["ok"])}
        wmeta = dict(meta, source="wrong", wrong_plan=name, wrong_index=index)
        if "pairs_trajectory" in formats and wrong_actions != ref_actions:
            out["records"]["pairs_trajectory"].append({
                "format": "pairs_trajectory", "prompt": {"instruction": instruction, "observation": ref["initial"]["obs"]},
                "chosen": ref_actions, "rejected": wrong_actions, "rejected_reason": dict(reason), "meta": dict(wmeta)})
        if "pairs_step" in formats:
            div = first_divergence([{"obs": s["before"]["digest"], "action": s["action"]} for s in ref["steps"]],
                                   [{"obs": s["before"]["digest"], "action": s["action"]} for s in wrong["steps"]],
                                   ref["final"]["digest"], wrong["final"]["digest"])
            if div is None:
                skip("wrong plan issues the same commands as the reference", name)
            elif not div["same_observation"]:
                skip("observations differ at the first divergence (step pair skipped)", name)
            else:
                k = div["index"]
                observation = ref["steps"][k]["before"]["obs"] if k < len(ref["steps"]) else ref["final"]["obs"]
                out["records"]["pairs_step"].append({
                    "format": "pairs_step",
                    "context": {"instruction": instruction, "observation": observation,
                                "history": [{"action": s["action"], "result": result_of(s)} for s in ref["steps"][:k]]},
                    "chosen": div["chosen"], "rejected": div["rejected"],
                    "reason": dict(reason, divergence_step=k, divergence=div["kind"]), "meta": dict(wmeta)})
        if "critique" in formats:
            out["records"]["critique"].append({
                "format": "critique",
                "trajectory": {"instruction": instruction, "initial_observation": wrong["initial"]["obs"],
                               "steps": [{"action": s["action"], "result": result_of(s)} for s in wrong["steps"]],
                               "final_observation": wrong["final"]["obs"]},
                "verdict": "fail", "reasons": dict(reason), "meta": dict(wmeta)})
    return out


def _job_or_skip(job):
    try:
        return _run_job(job)
    except Exception as exc:  # noqa: BLE001  (reported in manifest.json, never silently exported)
        return {"catalog_id": job["record"]["catalog_id"], "seed_tag": job["seed_tag"],
                "records": {fmt: [] for fmt in job["formats"]}, "warnings": Counter(), "dropped": 0, "exported": False,
                "skipped": [{"catalog_id": job["record"]["catalog_id"], "seed_tag": job["seed_tag"],
                             "reason": _short(mask_ids("exporter error: {}: {}".format(type(exc).__name__, exc)))}]}


def _worker_job(job):
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    from ..kernel.plugins import load_domains
    load_domains()
    return _job_or_skip(job)


def _run_jobs(jobs, workers, progress=None):
    if workers is None:
        workers = max(1, (os.cpu_count() or 2) - 1)
    if workers <= 1 or len(jobs) <= 2:
        from ..kernel.plugins import load_domains
        load_domains()
        results = []
        for i, job in enumerate(jobs, 1):
            results.append(_job_or_skip(job))
            if progress:
                progress(i, len(jobs), results[-1])
        return results
    from concurrent.futures import ProcessPoolExecutor
    results = []
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        for i, result in enumerate(pool.map(_worker_job, jobs, chunksize=1), 1):
            results.append(result)
            if progress:
                progress(i, len(jobs), result)
    return results


def _cap(records, limit):
    """At most `limit` records, taken round-robin across catalog entries (deterministic), original order kept."""
    if limit is None or len(records) <= limit:
        return records, 0
    groups = OrderedDict()
    for i, record in enumerate(records):
        groups.setdefault(record["meta"]["catalog_id"], []).append(i)
    chosen, depth = set(), 0
    while len(chosen) < limit:
        progressed = False
        for indices in groups.values():
            if depth < len(indices) and len(chosen) < limit:
                chosen.add(indices[depth])
                progressed = True
        if not progressed:
            break
        depth += 1
    return [r for i, r in enumerate(records) if i in chosen], len(records) - len(chosen)


def _write_jsonl(path, records):
    sha, size, biggest = hashlib.sha256(), 0, 0
    with Path(path).open("wb") as stream:
        for record in records:
            line = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            stream.write(line)
            sha.update(line)
            size += len(line)
            biggest = max(biggest, len(line))
    return {"bytes": size, "sha256": sha.hexdigest(), "max_record_bytes": biggest}


def _dataset_file_names():
    return {MANIFEST} | {fmt + ".jsonl" for fmt in FORMATS}


def _check_out_dir(out, overwrite):
    """Refuse an output directory that would mix exports: it must be missing or empty, or (overwrite) hold nothing
    but a previous export's manifest.json and <format>.jsonl files (operating-system metadata files are ignored)."""
    if not out.exists():
        return
    if not out.is_dir():
        raise ValueError("output path {} exists and is not a directory".format(out))
    present = sorted(p.name for p in out.iterdir() if p.name.lower() not in IGNORED_FILES)
    if not present:
        return
    if not overwrite:
        raise ValueError("output directory {} is not empty ({}); pass --overwrite (overwrite=True) to replace the "
                         "previous export in it".format(out, ", ".join(present[:6])))
    foreign = [name for name in present if name not in _dataset_file_names() or not (out / name).is_file()]
    if foreign:
        raise ValueError("refusing to overwrite {}: it holds files that are not part of a dataset export ({}); remove "
                         "them or export elsewhere".format(out, ", ".join(foreign[:6])))


def _clear_dataset_files(out):
    """Delete a previous export's files (manifest.json first, so an interrupted export never looks complete)."""
    if not out.is_dir():
        return
    for name in [MANIFEST] + [fmt + ".jsonl" for fmt in FORMATS]:
        target = out / name
        if target.is_file():
            target.unlink()


# ----- export -----------------------------------------------------------------------------------------------------
def export(out_dir, formats=FORMATS, split="train", seeds_per_entry=2, max_pairs=None, kinds=None, domains=None,
           sections=None, obs="compact", workers=None, catalog_ids=None, seeds=None, verify_now=False, root=None,
           progress=None, overwrite=False):
    """Export the dataset into out_dir and return the manifest (also written to out_dir/manifest.json).

    out_dir must be missing or empty. overwrite=True replaces a previous export there: manifest.json and every
    <format>.jsonl (including formats not written this time) are deleted first; a directory holding anything else
    is refused, so the result never mixes files from different exports.

    Only specs with current passing verification are used: evidence in reports/verification.jsonl for the current
    code fingerprint and spec hash, or, with verify_now=True, house_sim.tasks.verify.verify_many on the dev seeds now
    (each spec's own perception mode). split "train" (default) or "dev"; the test split and any private-range or
    manifest seed are refused with ValueError("test seed refused"). Extra keyword options beyond the contract:
    catalog_ids (restrict to these entries), seeds (explicit seeds, each guarded and checked against the split),
    verify_now, root (project root), progress(i, n, job_result), overwrite. Raises SplitsUnavailable when
    house_sim/eval/splits.py cannot be used."""
    from ..audit import fingerprint, harness_fingerprint
    from ..kernel.engine import actions_fingerprint
    from ..tasks.verify import spec_hash
    requested = _as_list(formats) or []
    unknown = sorted(set(requested) - set(FORMATS))
    formats = tuple(f for f in FORMATS if f in requested)
    if unknown or not formats:
        raise ValueError("unknown or empty formats {} (choose from {})".format(unknown, ", ".join(FORMATS)))
    if split == "test":
        raise ValueError("test seed refused: the test split is never exported")
    if split not in EXPORT_SPLITS:
        raise ValueError("split must be one of {}".format(", ".join(EXPORT_SPLITS)))
    if obs not in OBS_MODES:
        raise ValueError("obs must be one of {}".format(", ".join(OBS_MODES)))
    if int(seeds_per_entry) < 1:
        raise ValueError("seeds_per_entry must be >= 1")
    if max_pairs is not None and int(max_pairs) < 0:
        raise ValueError("max_pairs must be >= 0")
    sp, splits_source = splits_module()     # SplitsUnavailable: no stand-in, nothing is exported
    explicit = None
    if seeds is not None:
        explicit = [s for s in (_as_list(seeds) if isinstance(seeds, str) else list(seeds))]
        explicit = [int(s) if isinstance(s, str) else s for s in explicit]
        for seed in explicit:
            refuse_test_seed(None, seed, sp)     # private range / test manifest: refused before anything runs
            if split == "train" and not (sp.TRAIN_BASE <= seed < sp.PRIVATE_BASE):
                raise ValueError("seed {} is not in the train range [{}, {})".format(seed, sp.TRAIN_BASE, sp.PRIVATE_BASE))
            if split == "dev" and seed not in set(sp.DEV_SEEDS) | set(sp.DEV_EXTRA or ()):
                raise ValueError("seed {} is not a dev seed".format(seed))
        if len(set(explicit)) != len(explicit):
            raise ValueError("duplicate seeds in {}".format(explicit))
    out = Path(out_dir)
    _check_out_dir(out, overwrite)          # before anything runs: a stale export is never left half-replaced
    root = Path(root or ROOT)
    kinds, domains = _as_list(kinds), _as_list(domains)
    sections, catalog_ids = _as_list(sections, ";"), _as_list(catalog_ids)
    current = fingerprint(root)
    harness = harness_fingerprint()
    perception_fp = perception_fingerprint()
    records = spec_records(root)
    selected = select_records(records, kinds, domains, sections, catalog_ids)
    conflicts = select_records(records, kinds, domains, sections, catalog_ids, statuses=("DUPLICATE",))
    if verify_now:
        verified, unverified = verify_records_now(selected, seeds=sp.DEV_SEEDS, workers=workers)
        verification = {"mode": "now", "seeds": list(sp.DEV_SEEDS), "perception": "each spec's own"}
    else:
        verified, unverified = verified_from_evidence(selected, load_evidence(root), current)
        cross = _splits_verified_ids(sp)
        if cross is not None:
            for record in list(verified):
                if record["catalog_id"] not in cross:
                    verified.remove(record)
                    unverified[record["catalog_id"]] = "not listed by splits.verified_specs()"
        verification = {"mode": "evidence", "evidence_file": "reports/verification.jsonl",
                        "cross_check": "splits.verified_specs" if cross is not None else None}
    for record in conflicts:   # ambiguous spec: neither its evidence nor a fresh verification can be attributed
        unverified[record["catalog_id"]] = "excluded: " + record["problems"][0]
    verification.update(candidates=len(selected) + len(conflicts), verified=[r["catalog_id"] for r in verified],
                        unverified=dict(unverified))
    jobs = []
    for record in verified:
        cid = record["catalog_id"]
        if explicit is not None:
            chosen = list(explicit)
            if split == "dev":
                allowed = _dev_seed_set(sp, cid)
                bad = [s for s in chosen if s not in allowed]
                if bad:
                    raise ValueError("seeds {} are not dev seeds".format(bad))
        elif split == "train":
            chosen = list(sp.train_seeds(cid, int(seeds_per_entry)))
        else:
            chosen = list(sp.dev_seeds(cid))[:int(seeds_per_entry)]
        light = {k: v for k, v in record.items() if k not in ("text", "problems")}
        shash = spec_hash(record["spec"])
        for seed in chosen:
            refuse_test_seed(cid, seed, sp)
            jobs.append({"record": light, "seed": seed, "split": split, "seed_tag": sp.seed_tag(cid, seed), "obs": obs,
                         "formats": formats, "spec_hash": shash, "fingerprint": current, "harness_fingerprint": harness,
                         "perception_fingerprint": perception_fp})
    results = _run_jobs(jobs, workers, progress)
    _check_out_dir(out, overwrite)          # again: something may have been written there meanwhile
    _clear_dataset_files(out)               # manifest first, then every format file (also those not rewritten now)
    out.mkdir(parents=True, exist_ok=True)
    per_format, skipped, warnings = {f: [] for f in formats}, [], Counter()
    entries, dropped = OrderedDict(), 0
    for result in results:
        skipped.extend(result["skipped"])
        warnings.update(result["warnings"])
        dropped += result["dropped"]
        for fmt in formats:
            per_format[fmt].extend(result["records"][fmt])
    manifest_formats, size = {}, {"total_records": 0, "total_bytes": 0, "mean_record_bytes": {}, "max_record_bytes": {}}
    for fmt in formats:
        unique, seen, duplicates = [], set(), 0
        for record in per_format[fmt]:
            record["id"] = content_id(record)
            if record["id"] in seen:
                duplicates += 1
                continue
            seen.add(record["id"])
            unique.append(record)
        capped = 0
        if fmt in PAIR_FORMATS and max_pairs is not None:
            unique, capped = _cap(unique, int(max_pairs))
        info = _write_jsonl(out / (fmt + ".jsonl"), unique)
        manifest_formats[fmt] = {"file": fmt + ".jsonl", "records": len(unique), "bytes": info["bytes"], "sha256": info["sha256"],
                                 "duplicates_removed": duplicates, "capped": capped}
        size["total_records"] += len(unique)
        size["total_bytes"] += info["bytes"]
        size["mean_record_bytes"][fmt] = int(round(info["bytes"] / len(unique))) if unique else 0
        size["max_record_bytes"][fmt] = info["max_record_bytes"]
        for record in unique:
            entry = entries.setdefault(record["meta"]["catalog_id"], {"seed_tags": [], "records": {}})
            if record["meta"]["seed_tag"] not in entry["seed_tags"]:
                entry["seed_tags"].append(record["meta"]["seed_tag"])
            entry["records"][fmt] = entry["records"].get(fmt, 0) + 1
    reasons = Counter(item["reason"].split(":")[0].split(" (")[0] for item in skipped)
    manifest = {
        "schema": SCHEMA, "generator": "house_sim.eval.dataset", "code_fingerprint": current,
        "harness_fingerprint": harness, "perception_fingerprint": perception_fp,
        "actions_fingerprint": actions_fingerprint(), "splits_source": splits_source,
        "options": {"formats": list(formats), "split": split, "seeds_per_entry": int(seeds_per_entry), "seeds": explicit,
                    "max_pairs": None if max_pairs is None else int(max_pairs), "kinds": kinds, "domains": domains,
                    "sections": sections, "catalog_ids": catalog_ids, "obs": obs, "perception": "realistic",
                    "verify_now": bool(verify_now), "dedup": "sha256 of each record without id/meta"},
        "verification": verification, "formats": manifest_formats, "size": size,
        "counts": {"entries_selected": len(selected) + len(conflicts), "entries_verified": len(verified), "entries_exported": len(entries),
                   "seed_jobs": len(jobs), "seeds_exported": sum(1 for r in results if r["exported"]),
                   "records": {fmt: manifest_formats[fmt]["records"] for fmt in formats}, "skip_reasons": dict(reasons)},
        "entries": entries, "skipped": skipped,
        "audit": {"tool": "tools/perception_audit.py" if perception_audit_tool() is not None else "local checks",
                  "drop_kinds": sorted(DROP_LEAKS), "episodes_dropped": dropped, "warnings": dict(warnings)},
        "provenance": PROVENANCE, "license": LICENSE,
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


# ----- validation -------------------------------------------------------------------------------------------------
class _DeepContext(object):
    """Rebuilds the episode's initial environment per (catalog_id, seed) to know the true entity ids and to resolve
    handles; small LRU cache."""

    def __init__(self, root, limit=24, sp=None):
        self.root, self.limit = Path(root or ROOT), limit
        self.sp = sp if sp is not None else splits_module()[0]
        self._records = None
        self._cache = OrderedDict()

    def records(self):
        if self._records is None:
            try:
                self._records = {r["catalog_id"]: r for r in spec_records(self.root, strict=False)}
                self.load_error = None
            except Exception as exc:  # noqa: BLE001  (reported as deep_unavailable with this reason)
                self._records = {}
                self.load_error = "specs unreadable: {}: {}".format(type(exc).__name__, _short(exc, 120))
        return self._records

    def _spec(self, record, wanted_hash):
        from ..tasks.verify import spec_hash
        options = [record["spec"]] + list(record.get("alternatives") or [])
        if wanted_hash and len(options) > 1:
            for spec in options:
                if spec_hash(spec) == wanted_hash:
                    return spec
        return options[0]

    def get(self, catalog_id, seed, wanted_hash=None):
        key = (catalog_id, seed, wanted_hash)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        ctx = None
        record = self.records().get(catalog_id)
        if record is None:
            ctx = {"error": getattr(self, "load_error", None) or "spec not found"}
        elif not isinstance(seed, int) or isinstance(seed, bool) or seed >= self.sp.PRIVATE_BASE or self.sp.is_private_seed(seed):
            ctx = {"error": "no usable seed"}
        else:
            try:
                from ..tasks.runner import build, make_env
                from ..tasks.verify import spec_hash
                spec = self._spec(record, wanted_hash)
                config, task, _ = build(spec, seed, perception="realistic")
                env = make_env(config, task, seed, perception="realistic")
                state = env.world.s
                plain = plain_ids(state)
                ctx = {"ids": set(state["entities"]) - plain, "plain": plain, "entities": state["entities"],
                       "resolve": env.realism.handles.resolve, "tier": env.realism.tier, "spec_hash": spec_hash(spec)}
            except Exception as exc:  # noqa: BLE001
                ctx = {"error": "{}: {}".format(type(exc).__name__, _short(exc, 120))}
        self._cache[key] = ctx
        while len(self._cache) > self.limit:
            self._cache.popitem(last=False)
        return ctx


def _is_action(value):
    return isinstance(value, dict) and isinstance(value.get("action"), str) and value["action"] != ""


def _is_result(value):
    return (isinstance(value, dict) and isinstance(value.get("ok"), bool) and isinstance(value.get("message"), str)
            and isinstance(value.get("category"), str))


def _schema_problems(rec, fmt):
    problems = []

    def need(cond, text):
        if not cond:
            problems.append(text)
    if fmt == "sft":
        need(isinstance(rec.get("system"), str) and rec["system"], "system must be a non-empty string")
        need(isinstance(rec.get("instruction"), str), "instruction must be a string")
        turns = rec.get("turns")
        need(isinstance(turns, list) and turns, "turns must be a non-empty list")
        for i, turn in enumerate(turns if isinstance(turns, list) else []):
            last = i == len(turns) - 1
            if not (isinstance(turn, dict) and isinstance(turn.get("observation"), dict) and _is_action(turn.get("action"))):
                problems.append("turns[{}] needs observation (object) and action (command)".format(i))
                continue
            if turn["action"].get("action") == "done":
                need(last, "turns[{}]: done is only allowed as the last turn".format(i))
            else:
                need(_is_result(turn.get("result")), "turns[{}].result needs ok/message/category".format(i))
        need(isinstance(rec.get("outcome"), dict) and isinstance(rec["outcome"].get("success"), bool), "outcome.success missing")
    elif fmt == "pairs_trajectory":
        prompt = rec.get("prompt")
        need(isinstance(prompt, dict) and isinstance(prompt.get("instruction"), str) and isinstance(prompt.get("observation"), dict),
             "prompt needs instruction and observation")
        for key in ("chosen", "rejected"):
            seq = rec.get(key)
            need(isinstance(seq, list) and seq and all(_is_action(a) for a in seq), key + " must be a non-empty list of commands")
        need(rec.get("chosen") != rec.get("rejected"), "chosen equals rejected")
        reason = rec.get("rejected_reason")
        need(isinstance(reason, dict) and isinstance(reason.get("wrong_plan"), str) and isinstance(reason.get("failures"), list),
             "rejected_reason needs wrong_plan and failures")
    elif fmt == "pairs_step":
        ctx = rec.get("context")
        ok = isinstance(ctx, dict) and isinstance(ctx.get("instruction"), str) and isinstance(ctx.get("observation"), dict) \
            and isinstance(ctx.get("history"), list)
        need(ok, "context needs instruction, observation and history")
        if ok:
            for i, item in enumerate(ctx["history"]):
                need(isinstance(item, dict) and _is_action(item.get("action")) and _is_result(item.get("result")),
                     "context.history[{}] needs action and result".format(i))
        need(_is_action(rec.get("chosen")) and _is_action(rec.get("rejected")), "chosen and rejected must be commands")
        need(rec.get("chosen") != rec.get("rejected"), "chosen equals rejected")
        reason = rec.get("reason")
        need(isinstance(reason, dict) and isinstance(reason.get("wrong_plan"), str), "reason needs wrong_plan")
        if ok and isinstance(reason, dict):
            need(reason.get("divergence_step") == len(ctx["history"]), "reason.divergence_step must equal len(context.history)")
    elif fmt == "critique":
        traj = rec.get("trajectory")
        ok = isinstance(traj, dict) and isinstance(traj.get("instruction"), str) and isinstance(traj.get("initial_observation"), dict) \
            and isinstance(traj.get("steps"), list) and traj["steps"] and isinstance(traj.get("final_observation"), dict)
        need(ok, "trajectory needs instruction, initial_observation, non-empty steps and final_observation")
        if ok:
            for i, item in enumerate(traj["steps"]):
                need(isinstance(item, dict) and _is_action(item.get("action")) and _is_result(item.get("result")),
                     "trajectory.steps[{}] needs action and result".format(i))
        need(rec.get("verdict") in ("fail", "pass"), "verdict must be fail or pass")
        reasons = rec.get("reasons")
        need(isinstance(reasons, dict) and (rec.get("verdict") != "fail" or isinstance(reasons.get("wrong_plan"), str)),
             "reasons needs wrong_plan for a failing verdict")
    return problems


def _observations(rec, fmt):
    if fmt == "sft":
        for i, turn in enumerate(rec.get("turns") or []):
            if isinstance(turn, dict) and isinstance(turn.get("observation"), dict):
                yield "turns[{}].observation".format(i), turn["observation"]
    elif fmt == "pairs_trajectory":
        if isinstance(rec.get("prompt"), dict) and isinstance(rec["prompt"].get("observation"), dict):
            yield "prompt.observation", rec["prompt"]["observation"]
    elif fmt == "pairs_step":
        if isinstance(rec.get("context"), dict) and isinstance(rec["context"].get("observation"), dict):
            yield "context.observation", rec["context"]["observation"]
    elif fmt == "critique":
        traj = rec.get("trajectory") if isinstance(rec.get("trajectory"), dict) else {}
        for key in ("initial_observation", "final_observation"):
            if isinstance(traj.get(key), dict):
                yield "trajectory." + key, traj[key]


def _observed_plain(rec, fmt):
    """Room / person / pet ids an agent sees in the record's observations (for the static id heuristic)."""
    plain = {"robot"}
    for _, obs in _observations(rec, fmt):
        if isinstance(obs.get("room_id"), str):
            plain.add(obs["room_id"])
        room = obs.get("room") if isinstance(obs.get("room"), dict) else {}
        plain.update(x for x in room.get("exits") or [] if isinstance(x, str))
        for conn in obs.get("connections") or []:
            if isinstance(conn, dict) and isinstance(conn.get("to"), str):
                plain.add(conn["to"])
            if isinstance(conn, dict) and isinstance(conn.get("id"), str) and conn["id"].startswith("conn_"):
                plain.add(conn["id"])     # doorway ids (conn_<room>__<room>) are not entity ids
        for key in ("people", "pets"):
            if isinstance(obs.get(key), dict):
                plain.update(obs[key])
    return plain


def _scan_directory(path, listed):
    """Files anywhere under a dataset directory that are neither manifest.json nor listed by it (operating-system
    metadata files ignored): [(relative posix path, kind)], kind "records" (a .jsonl / .ndjson file or a backup copy of
    one such as .jsonl.bak), "data" (another data-like file: .json, .csv, .parquet, archives, x.json.old ...) or "other"."""
    found = []
    for folder, dirs, names in os.walk(str(path)):
        dirs.sort()
        here = Path(folder)
        for name in sorted(names):
            rel = (here / name).relative_to(path).as_posix()
            if name.lower() in IGNORED_FILES or rel == MANIFEST or rel in listed:
                continue
            if RECORD_FILE_RE.search(name) or BACKUP_RECORD_RE.search(name):
                kind = "records"
            elif DATA_FILE_RE.search(name) or EMBEDDED_DATA_RE.search(name):
                kind = "data"
            else:
                kind = "other"
            found.append((rel, kind))
    return found


def _allowed_seeds(sp, catalog_id, split, options):
    """The seeds the manifest's options give one entry: options.seeds when explicit, else the first seeds_per_entry
    splits.train_seeds / dev_seeds. None when the options do not say."""
    explicit = options.get("seeds")
    if isinstance(explicit, list):
        return {s for s in explicit if isinstance(s, int) and not isinstance(s, bool)}
    n = options.get("seeds_per_entry")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return None
    if split == "train":
        return train_schedule(sp, catalog_id, n)
    if split == "dev":
        return set(list(sp.dev_seeds(catalog_id))[:n])
    return None


def validate(path_or_dir, deep=True, root=None, allow_static=False):
    """Validate a dataset directory (with manifest.json) or one JSONL file. Returns a report dict with "ok".

    deep=True (default) rebuilds every record's initial environment for exact id and per-entity field checks; a
    record whose environment cannot be rebuilt is an error ("deep_unavailable") unless allow_static=True, which
    accepts the static checks for it with a warning. deep=False runs the static checks only. In a directory every
    file must be manifest.json or listed by it: an unlisted JSONL file is an error (and is validated anyway)."""
    from ..audit import fingerprint, harness_fingerprint
    root = Path(root or ROOT)
    path = Path(path_or_dir)
    report = {"path": str(path), "ok": False, "deep": bool(deep), "allow_static": bool(allow_static), "splits_source": None,
              "code_fingerprint": fingerprint(root), "harness_fingerprint": harness_fingerprint(),
              "perception_fingerprint": perception_fingerprint(), "dataset_fingerprint": None,
              "dataset_harness_fingerprint": None, "dataset_perception_fingerprint": None, "stale": None,
              "harness_stale": None, "perception_stale": None, "files": {}, "records": 0, "deep_checked_records": 0,
              "static_vocabulary": 0, "error_counts": {}, "errors": [], "warning_counts": {}, "warnings": []}
    errors, warnings = Counter(), Counter()
    checked = []

    def error(kind, where, detail):
        errors[kind] += 1
        if len(report["errors"]) < MAX_REPORTED:
            report["errors"].append({"kind": kind, "where": where, "detail": _short(detail, 300)})

    def warn(kind, where, detail):
        warnings[kind] += 1
        if len(report["warnings"]) < MAX_REPORTED:
            report["warnings"].append({"kind": kind, "where": where, "detail": _short(detail, 300)})

    def finish():
        report["error_counts"] = dict(errors)
        report["warning_counts"] = dict(warnings)
        report["ok"] = not errors and bool(checked)
        return report

    try:
        sp, report["splits_source"] = splits_module()
    except SplitsUnavailable as exc:
        error("splits", SPLITS_SOURCE, str(exc))
        return finish()

    files, manifest, options, expected = [], None, {}, {}
    if path.is_dir():
        mpath = path / MANIFEST
        if mpath.exists():
            try:
                manifest = json.loads(mpath.read_text(encoding="utf-8"))
            except ValueError as exc:
                error("manifest", MANIFEST, "unreadable: {}".format(exc))
            if manifest is not None and not isinstance(manifest, dict):
                error("manifest", MANIFEST, "not a JSON object")
                manifest = None
        else:
            error("manifest", MANIFEST, "missing")
        if isinstance(manifest, dict):
            if manifest.get("schema") != SCHEMA:
                error("manifest", MANIFEST, "schema is {!r}, expected {!r}".format(manifest.get("schema"), SCHEMA))
            for key in FINGERPRINT_KEYS:
                if isinstance(manifest.get(key), str) and manifest[key]:
                    expected[key] = manifest[key]
                else:
                    error("manifest", MANIFEST, key + " missing")
            if manifest.get("splits_source") != SPLITS_SOURCE:
                error("splits", MANIFEST, "splits_source is {!r}; only data whose seeds come from {} is valid".format(
                    manifest.get("splits_source"), SPLITS_SOURCE))
            if isinstance(manifest.get("options"), dict):
                options = manifest["options"]
            else:
                error("manifest", MANIFEST, "options missing")
            n = options.get("seeds_per_entry")
            if options.get("seeds") is None and (not isinstance(n, int) or isinstance(n, bool) or n < 1):
                error("manifest", MANIFEST, "options.seeds_per_entry must be a positive integer")
            if options.get("seeds") is not None and not isinstance(options.get("seeds"), list):
                error("manifest", MANIFEST, "options.seeds must be a list or null")
            listed_formats = manifest.get("formats")
            if not isinstance(listed_formats, dict):
                error("manifest", MANIFEST, "formats missing")
                listed_formats = {}
            for fmt, info in sorted(listed_formats.items()):
                if fmt not in FORMATS:
                    error("manifest", MANIFEST, "unknown format " + str(fmt))
                    continue
                info = info if isinstance(info, dict) else {}
                canonical = fmt + ".jsonl"
                if info.get("file", canonical) != canonical:
                    error("manifest", MANIFEST, "formats.{}.file must be {!r}, got {!r}".format(fmt, canonical, info.get("file")))
                files.append((fmt, path / canonical, info, True))
            if isinstance(options.get("formats"), list) and \
                    sorted(map(str, options["formats"])) != sorted(f for f in listed_formats if f in FORMATS):
                error("manifest", MANIFEST, "options.formats {} differs from the listed formats {}".format(
                    options["formats"], sorted(listed_formats)))
        listed = {fpath.name for _, fpath, _, _ in files}
        for rel, kind in _scan_directory(path, listed):
            name = rel.rsplit("/", 1)[-1]
            stem = name.rsplit(".", 1)[0]
            fmt = stem if ("/" not in rel and stem in FORMATS) else None
            if kind == "records":
                if isinstance(manifest, dict):
                    why = ("{} exists but manifest.json does not list format {}".format(rel, stem) if fmt
                           else "{} is not listed in manifest.json".format(rel))
                    error("unlisted_file", rel, why + " (stale file from another export? validated anyway)")
                files.append((fmt, path / rel, {}, False))
            elif kind == "data":
                error("unlisted_file", rel, "data file not listed in manifest.json (a dataset directory holds only "
                                            "manifest.json and the listed <format>.jsonl files)")
            else:
                warn("unexpected_file", rel, "not part of the dataset")
    elif path.is_file():
        stem = path.stem
        files = [(stem if stem in FORMATS else None, path, {}, False)]
    else:
        error("missing", str(path), "no such file or directory")
    with_manifest = isinstance(manifest, dict)
    deep_ctx = _DeepContext(root, sp=sp) if deep else None
    types = registered_types()
    vocab = static_id_vocabulary()
    report["static_vocabulary"] = len(vocab["forms"]) + len(vocab["rooms"])
    if not vocab["houses"]:
        warn("vocabulary_unavailable", "house_sim.generator", "no house could be generated: the static id check only "
                                                               "recognises <type>_<n> shapes")
    split_opt, obs_opt = options.get("split"), options.get("obs")
    entry_seen = OrderedDict()
    for fmt, fpath, info, listed in files:
        name = fpath.relative_to(path).as_posix() if path.is_dir() else fpath.name
        stats = {"records": 0, "errors_before": sum(errors.values())}
        if not fpath.exists():
            error("missing", name, "file listed but missing")
            report["files"][name] = {"format": fmt, "records": 0, "errors": 1, "listed": listed}
            continue
        checked.append(name)
        raw = fpath.read_bytes()
        if info:
            if info.get("sha256") and hashlib.sha256(raw).hexdigest() != info["sha256"]:
                error("manifest", name, "sha256 differs from manifest.json")
            if info.get("bytes") is not None and len(raw) != info["bytes"]:
                error("manifest", name, "size differs from manifest.json")
        seen = set()
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            error("json", name, "not UTF-8: {}".format(exc))
            lines = []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            where = "{}:{}".format(name, number)
            stats["records"] += 1
            try:
                rec = json.loads(line)
            except ValueError as exc:
                error("json", where, str(exc))
                continue
            if not isinstance(rec, dict):
                error("schema", where, "record is not an object")
                continue
            rfmt = rec.get("format")
            if rfmt not in FORMATS or (fmt is not None and rfmt != fmt):
                error("schema", where, "format {!r} does not match the file".format(rfmt))
                continue
            for problem in _schema_problems(rec, rfmt):
                error("schema", where, problem)
            rid = rec.get("id")
            if not isinstance(rid, str):
                error("schema", where, "id missing")
            else:
                if rid != content_id(rec):
                    error("hash_mismatch", where, "id is not the content hash (record edited after export?)")
                if rid in seen:
                    error("duplicate", where, "duplicate record id " + rid)
                seen.add(rid)
            meta = rec.get("meta")
            if not isinstance(meta, dict):
                error("schema", where, "meta missing")
                meta = {}
            missing = [k for k in META_KEYS if k not in meta]
            if missing:
                error("schema", where, "meta lacks " + ", ".join(missing))
            if "schema" in meta and meta.get("schema") != SCHEMA:
                error("schema", where, "meta.schema is {!r}, expected {!r}".format(meta.get("schema"), SCHEMA))
            for key in FINGERPRINT_KEYS:
                value = meta.get(key)
                if key not in expected and isinstance(value, str) and value:
                    expected[key] = value          # no manifest: the first record sets what the others must carry
                if key in expected and value != expected[key]:
                    error("mixed_fingerprint", where, "meta.{} {!r} differs from {} ({})".format(
                        key, value, "manifest.json" if with_manifest else "the first record", expected[key]))
            cid, seed, split, tag = meta.get("catalog_id"), meta.get("seed"), meta.get("split"), str(meta.get("seed_tag") or "")
            if split == "test" or tag.startswith("test:"):
                error("test_seed", where, "test split data must never be exported")
            elif split not in EXPORT_SPLITS:
                error("schema", where, "split must be train or dev")
            if tag and split and not tag.startswith(str(split) + ":"):
                error("split_mismatch", where, "seed_tag {} does not match split {}".format(tag, split))
            if with_manifest and split_opt and split != split_opt:
                error("split_mismatch", where, "split {} differs from the manifest's options.split {}".format(split, split_opt))
            if with_manifest and obs_opt and meta.get("obs") != obs_opt:
                error("schema", where, "meta.obs {!r} differs from the manifest's options.obs {!r}".format(meta.get("obs"), obs_opt))
            if seed is None and split in EXPORT_SPLITS:
                error("schema", where, "meta.seed missing (raw seeds are recorded for train/dev records)")
            elif seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
                error("schema", where, "meta.seed must be an integer")
            elif seed is not None:
                try:
                    refuse_test_seed(cid, seed, sp)
                    if split == "train" and not (sp.TRAIN_BASE <= seed < sp.PRIVATE_BASE):
                        error("split_mismatch", where, "train record with a seed outside the train range")
                    if split == "dev" and seed not in _dev_seed_set(sp, cid):
                        error("split_mismatch", where, "dev record with a non-dev seed")
                    try:
                        want_tag = sp.seed_tag(cid, seed)
                    except Exception:  # noqa: BLE001
                        want_tag = "{}:{}".format(split, seed)
                    if tag and (tag != "{}:{}".format(split, seed) or tag != want_tag):
                        error("split_mismatch", where, "seed_tag {} does not match the seed (splits.seed_tag: {})".format(tag, want_tag))
                    if split in EXPORT_SPLITS and isinstance(cid, str):
                        if with_manifest:
                            allowed = _allowed_seeds(sp, cid, split, options)
                            if allowed is not None and seed not in allowed:
                                error("off_schedule_seed", where, "seed {} is not one of the seeds the manifest's options "
                                                                  "give {} (splits.{}_seeds)".format(seed, cid, split))
                        elif split == "train" and seed not in train_schedule(sp, cid, TRAIN_INDEX_BOUND):
                            warn("off_schedule_seed", where, "train seed {} is not among the first {} splits.train_seeds "
                                                             "of {}".format(seed, TRAIN_INDEX_BOUND, cid))
                except ValueError as exc:
                    error("test_seed", where, str(exc))
            if meta.get("perception") != "realistic":
                error("perception", where, "records must come from realistic perception")
            if "camera" in meta and meta.get("camera") not in ("room", "geometric"):     # absent: META_KEYS reports it
                error("schema", where, "meta.camera is {!r}, expected room or geometric".format(meta.get("camera")))
            if listed and isinstance(cid, str):
                entry = entry_seen.setdefault(cid, {"seed_tags": set(), "records": Counter()})
                entry["seed_tags"].add(tag)
                entry["records"][rfmt] += 1
            for opath, obs in _observations(rec, rfmt):
                if "perception" in obs and obs.get("perception") != "realistic":
                    error("perception", where, opath + " is not a realistic observation")
                for handle in (obs.get("visible_entities") or {}):
                    if not HANDLE_RE.match(str(handle)):
                        error("id", where, "{}.visible_entities key {!r} is not an opaque handle".format(opath, handle))
            body = {k: v for k, v in rec.items() if k not in ("meta", "id", "format")}
            for kind, kpath, key in key_leaks(body):
                error(kind, where, "{} ({})".format(kpath, key))
            ctx = deep_ctx.get(cid, seed, meta.get("spec_hash")) if deep_ctx is not None else None
            if ctx is not None and "error" in ctx:
                if allow_static:
                    warn("deep_unavailable", where, "{} seed {}: {} (static checks only)".format(cid, seed, ctx["error"]))
                else:
                    error("deep_unavailable", where, "{} seed {}: {}; the environment could not be rebuilt, so the exact "
                                                     "id and field checks did not run (allow_static / --allow-static "
                                                     "accepts static checks)".format(cid, seed, ctx["error"]))
                ctx = None
            report["deep_checked_records"] += ctx is not None
            if ctx is not None:
                if meta.get("spec_hash") and meta["spec_hash"] != ctx["spec_hash"]:
                    warn("spec_changed", where, "spec changed since export; deep checks use the current spec")
                plain, true_ids = ctx["plain"], ctx["ids"]
            else:
                plain, true_ids = _observed_plain(rec, rfmt), set()
            for kind, kpath, token in id_tokens(body, true_ids, types, plain, vocab=vocab):
                error(kind, where, "{} contains true entity id {}".format(kpath, token))
            if ctx is not None:
                # secret fields are always errors; a never-tier field is downgraded to a warning only when the type
                # perception tiers changed since export (perception fingerprint) and it is not a core privacy field
                tiers_changed = meta.get("perception_fingerprint") != report["perception_fingerprint"]
                for opath, obs in _observations(rec, rfmt):
                    for kind, kpath, field in entity_field_leaks(obs, ctx["resolve"], ctx["entities"], ctx["tier"], opath):
                        if kind == "hidden" and tiers_changed and field not in CORE_NEVER_FIELDS:
                            warn(kind, where, "{} (perception tiers changed since export: never-tier field {})".format(kpath, field))
                        else:
                            error(kind, where, "{} is a {} field".format(kpath, kind))
        if info and info.get("records") is not None and info["records"] != stats["records"]:
            error("manifest", name, "manifest lists {} records, file has {}".format(info["records"], stats["records"]))
        report["files"][name] = {"format": fmt, "records": stats["records"], "listed": listed,
                                 "errors": sum(errors.values()) - stats["errors_before"]}
        report["records"] += stats["records"]
    if with_manifest:
        entries = manifest.get("entries")
        verified_ids = set(((manifest.get("verification") or {}).get("verified") or [])) \
            if isinstance(manifest.get("verification"), dict) else set()
        if not isinstance(entries, dict):
            error("manifest", MANIFEST, "entries missing")
            entries = {}
        for cid in sorted(set(entries) | set(entry_seen)):
            want, got = entries.get(cid), entry_seen.get(cid)
            if not isinstance(want, dict):
                error("manifest", MANIFEST, "entry {} has records but is not listed in manifest.entries".format(cid))
                continue
            if got is None:
                error("manifest", MANIFEST, "manifest.entries lists {} but no record has it (records deleted?)".format(cid))
                continue
            if set(want.get("seed_tags") or []) != got["seed_tags"]:
                error("manifest", MANIFEST, "entry {}: records carry seed tags {}, manifest.entries lists {}".format(
                    cid, sorted(got["seed_tags"]), sorted(want.get("seed_tags") or [])))
            if dict(want.get("records") or {}) != dict(got["records"]):
                error("manifest", MANIFEST, "entry {}: records per format {} differ from manifest.entries {}".format(
                    cid, dict(got["records"]), dict(want.get("records") or {})))
            if cid not in verified_ids:
                error("unverified", MANIFEST, "entry {} is not in verification.verified".format(cid))
        # completeness: every seed the options give a verified entry was either exported or skipped with a reason
        # (not checkable when --max-pairs capping or dedup may have removed every record of a seed)
        infos = [i for i in (manifest.get("formats") or {}).values() if isinstance(i, dict)] \
            if isinstance(manifest.get("formats"), dict) else []
        thinned = any(i.get("capped") or i.get("duplicates_removed") for i in infos)
        skipped = manifest.get("skipped") if isinstance(manifest.get("skipped"), list) else []
        accounted = {}
        for item in skipped:
            if isinstance(item, dict) and isinstance(item.get("catalog_id"), str):
                accounted.setdefault(item["catalog_id"], set()).add(item.get("seed_tag"))
        for cid in sorted(set() if thinned else verified_ids, key=str):
            if not isinstance(cid, str) or split_opt not in EXPORT_SPLITS:
                continue
            seeds = _allowed_seeds(sp, cid, split_opt, options)
            if seeds is None:
                continue
            try:
                wanted = {sp.seed_tag(cid, s) for s in seeds}
            except Exception:  # noqa: BLE001  (refused seeds are reported per record)
                continue
            covered = set((entries.get(cid) or {}).get("seed_tags") or []) if isinstance(entries.get(cid), dict) else set()
            missing = sorted(wanted - covered - accounted.get(cid, set()))
            if missing:
                error("manifest", MANIFEST, "verified entry {}: seed(s) {} neither exported nor skipped (records "
                                            "deleted?)".format(cid, ", ".join(missing[:5])))
    where = MANIFEST if with_manifest else str(path.name)
    report["dataset_fingerprint"] = expected.get("code_fingerprint")
    report["dataset_harness_fingerprint"] = expected.get("harness_fingerprint")
    report["dataset_perception_fingerprint"] = expected.get("perception_fingerprint")
    if report["dataset_fingerprint"]:
        report["stale"] = report["dataset_fingerprint"] != report["code_fingerprint"]
        if report["stale"]:
            warn("stale", where, "generated by a different simulator code fingerprint (house_sim.audit.fingerprint)")
    if report["dataset_harness_fingerprint"]:
        report["harness_stale"] = report["dataset_harness_fingerprint"] != report["harness_fingerprint"]
        if report["harness_stale"]:
            warn("stale_harness", where, "generated by different evaluation tooling (house_sim.audit.harness_fingerprint)")
    if report["dataset_perception_fingerprint"]:
        report["perception_stale"] = report["dataset_perception_fingerprint"] != report["perception_fingerprint"]
        if report["perception_stale"]:
            warn("stale_perception", where, "type perception tiers changed since export: never-tier findings outside the "
                                            "core privacy fields are reported as warnings")
    return finish()


# ----- command line -----------------------------------------------------------------------------------------------
def _parser():
    parser = argparse.ArgumentParser(prog="python -m house_sim", description="Trajectory / preference-pair dataset export and validation (docs/DATASET.md).")
    parser.add_argument("--export-dataset", metavar="OUT_DIR", help="export the dataset into OUT_DIR")
    parser.add_argument("--validate-dataset", metavar="PATH", help="validate a dataset directory or JSONL file")
    parser.add_argument("--formats", default=",".join(FORMATS), help="comma list of " + ", ".join(FORMATS))
    parser.add_argument("--split", default="train", help="train (default) or dev; test is refused")
    parser.add_argument("--seeds-per-entry", type=int, default=2)
    parser.add_argument("--seeds", default=None, help="explicit comma list of seeds (each checked; test seeds refused)")
    parser.add_argument("--max-pairs", type=int, default=None, help="cap per pair format (round-robin over entries)")
    parser.add_argument("--kinds", default=None, help="comma list of catalog kinds (task, appendix, adaptability, ...)")
    parser.add_argument("--domains", default=None, help="comma list of domains (cleaning, food, ...)")
    parser.add_argument("--sections", action="append", default=None, help="section name or substring; ';' separates several; repeatable")
    parser.add_argument("--catalog-ids", default=None, help="comma list of catalog ids")
    parser.add_argument("--obs", default="compact", choices=OBS_MODES)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--verify-now", action="store_true", help="verify the selected specs on the dev seeds now instead of trusting reports/verification.jsonl")
    parser.add_argument("--overwrite", action="store_true", help="export: replace a previous export in OUT_DIR (its manifest.json and every <format>.jsonl are deleted first)")
    parser.add_argument("--no-deep", action="store_true", help="validation: skip rebuilding environments (static checks only)")
    parser.add_argument("--allow-static", action="store_true", help="validation: accept records whose environment cannot be rebuilt with static checks only (a warning instead of an error)")
    parser.add_argument("--json", default=None, help="write the validation report / manifest summary as JSON to this path")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _print_manifest(manifest, out):
    print("dataset written to {} (code {}, harness {}, perception {})".format(
        out, manifest["code_fingerprint"][:12], manifest["harness_fingerprint"][:12], manifest["perception_fingerprint"][:12]))
    ver = manifest["verification"]
    print("  specs: {} selected, {} verified ({}), {} exported".format(manifest["counts"]["entries_selected"], len(ver["verified"]),
                                                                        ver["mode"], manifest["counts"]["entries_exported"]))
    print("  seeds: {} tried, {} exported".format(manifest["counts"]["seed_jobs"], manifest["counts"]["seeds_exported"]))
    for fmt, info in manifest["formats"].items():
        print("  {:17s} {:6d} records  {:10.1f} KB  mean {:7d} B  dup {}  capped {}".format(
            fmt, info["records"], info["bytes"] / 1024.0, manifest["size"]["mean_record_bytes"].get(fmt, 0), info["duplicates_removed"], info["capped"]))
    print("  total {} records, {:.1f} KB".format(manifest["size"]["total_records"], manifest["size"]["total_bytes"] / 1024.0))
    for reason, n in sorted(manifest["counts"]["skip_reasons"].items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
        print("  skipped {:4d}  {}".format(n, reason))
    if not ver["verified"]:
        print("  no selected spec has current passing verification; run `python -m house_sim --verify` or add --verify-now")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    if bool(args.export_dataset) == bool(args.validate_dataset):
        print("error: give exactly one of --export-dataset OUT_DIR or --validate-dataset PATH", file=sys.stderr)
        return 2
    if args.validate_dataset:
        report = validate(args.validate_dataset, deep=not args.no_deep, allow_static=args.allow_static)
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stale = [label for key, label in (("stale", "code"), ("harness_stale", "harness"), ("perception_stale", "perception"))
                 if report.get(key)]
        print("{}: {} ({} records, {} errors, {} warnings{})".format(
            report["path"], "OK" if report["ok"] else "INVALID", report["records"], sum(report["error_counts"].values()),
            sum(report["warning_counts"].values()), ", stale fingerprint ({})".format(", ".join(stale)) if stale else ""))
        if not report["deep"]:
            print("  note: static checks only (--no-deep): ids are recognised by shape, per-entity fields are not checked")
        elif report["deep_checked_records"] < report["records"]:
            print("  note: deep checks (environment rebuilt) covered {} of {} records; the rest got static checks only"
                  " (deep_unavailable {})".format(report["deep_checked_records"], report["records"],
                                                  "warnings: --allow-static" if report["allow_static"] else "errors"))
        for kind, n in sorted(report["error_counts"].items()):
            print("  error {:16s} {}".format(kind, n))
        for item in report["errors"][:10]:
            print("    {} {}: {}".format(item["kind"], item["where"], item["detail"]))
        if not args.quiet:
            for kind, n in sorted(report["warning_counts"].items()):
                print("  warning {:14s} {}".format(kind, n))
        return 0 if report["ok"] else 1

    def progress(i, n, result):
        if not args.quiet and (i == n or i % 10 == 0):
            print("[dataset] {}/{} seed jobs".format(i, n), file=sys.stderr, flush=True)
    sections = None
    if args.sections:
        sections = [s for value in args.sections for s in value.split(";") if s.strip()]
    try:
        manifest = export(args.export_dataset, formats=args.formats, split=args.split, seeds_per_entry=args.seeds_per_entry,
                          max_pairs=args.max_pairs, kinds=args.kinds, domains=args.domains, sections=sections, obs=args.obs,
                          workers=args.workers, catalog_ids=args.catalog_ids, seeds=args.seeds, verify_now=args.verify_now,
                          progress=progress, overwrite=args.overwrite)
    except (ValueError, SplitsUnavailable) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if args.json:
        Path(args.json).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _print_manifest(manifest, args.export_dataset)
    return 0 if manifest["size"]["total_records"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Episode replay files: house-replay-v2 (full JSON) and house-replay-v3 (compact, the default).

An episode replay holds what HouseEnv.replay needs to re-run an episode exactly and what the viewer needs to show it
without re-running: the initial config, the start state (after reset), the evaluation baseline, the task, the seed and
modes, every step record (command, result, and with trace=True its state digest and journaled changes), the final
state digest and the final evaluation.

house-replay-v2 stores all of it as plain JSON: about 3 MB per episode, because the initial config, the start state
and the baseline are three nearly identical copies of a whole house (about 1 MB each), and far more for long traced
episodes, whose per-step change lists dominate (a 300-step traced episode: about 17 MB). house-replay-v3 (kernel v3.1
adoption round) is the compact form:

    {"format": "house-replay-v3",
     "task", "seed", "perception", "camera", "actions_hash", "final_hash", "evaluation", "metadata",   plain JSON
     "summary": {"steps", "traced", "changes", "phase_done"},                                         plain JSON
     "codec": "xz+b85" | "zlib+b85",
     "packed": "<base85 text>",          the bulky fields {"initial", "start_state", "baseline", "steps"} as one JSON
                                          document, compressed together
     "packed_sha256": "<hex>"}           sha256 of that JSON document (checked when it is unpacked)

* The states and the command log are kept exactly: the initial config, the start state, the baseline, every step's
  command, result, time and (traced episodes) state digest, the final digest and evaluation. HouseEnv.replay re-runs
  the commands from the start state and checks every recorded digest, so a replay proves that the current code
  reproduces the recording step by step.
* The traced steps' change lists are dropped unless keep_changes=True: they are what a re-run with trace=True
  computes again (the viewer re-runs every replay and takes the diffs from the re-run); only a reader that must show
  a recording the current code no longer reproduces needs them (the viewer's recorded-change fallback).
* Compressing the bulky fields as one document lets the compressor find the near-identical states: a typical episode
  takes about 60-95 KB (measured on the demo and leaderboard replays: mean about 75 KB; a 300-step traced episode
  about 92 KB, about 470 KB with keep_changes). zlib, used only when Python lacks the lzma module, gives about three
  times more because its window cannot reach across states.
* The plain fields keep a replay's identity readable without unpacking (the release audit of house_sim/eval/splits.py
  sees its seed). decode() returns the v2-shaped dict, so every reader works the same on both formats; old v2 files
  keep loading.

    write(path, data, compact=True, keep_changes=False)   data: the v2-shaped dict (HouseEnv.save_replay builds it)
    read(path) -> dict                  the v2-shaped dict of a v2 or v3 file ("format" names the file's format)
    encode(data, keep_changes=False) -> dict / decode(obj) -> dict
    is_replay(obj) -> bool

Writing is deterministic for a given Python (the JSON document keeps the recorded key order, which the re-run's
state iteration order depends on). Stdlib only.
"""
import base64
import hashlib
import json
import zlib
from pathlib import Path

try:                                # optional in some Python builds; zlib is the fallback codec
    import lzma
except ImportError:                 # pragma: no cover - CPython builds on Windows, macOS and Linux distributions ship it
    lzma = None

FORMAT_V2 = "house-replay-v2"
FORMAT_V3 = "house-replay-v3"
FORMATS = (FORMAT_V2, FORMAT_V3)
BULKY = ("initial", "start_state", "baseline", "steps")
LZMA_PRESET = 3                     # about 0.15 s and 89 KB for a 2.8 MB episode (preset 6: 0.6 s, 81 KB)


def is_replay(obj):
    """True for a decoded or encoded replay object of a known format."""
    return isinstance(obj, dict) and obj.get("format") in FORMATS


def _document(bulk):
    return json.dumps(bulk, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def pack(value):
    """(codec, text, sha256) of a JSON value: compressed, base85 text (JSON-safe: no quote or backslash)."""
    raw = _document(value)
    if lzma is not None:
        codec, blob = "xz+b85", lzma.compress(raw, format=lzma.FORMAT_XZ, preset=LZMA_PRESET)
    else:
        codec, blob = "zlib+b85", zlib.compress(raw, 9)
    return codec, base64.b85encode(blob).decode("ascii"), hashlib.sha256(raw).hexdigest()


def unpack(codec, text, sha256=None):
    """The JSON value pack() packed. ValueError on an unknown codec, corrupt data or a checksum mismatch."""
    try:
        blob = base64.b85decode(text.encode("ascii") if isinstance(text, str) else text)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Corrupt replay data ({})".format(exc))
    if codec == "xz+b85":
        if lzma is None:
            raise ValueError("This replay is xz-compressed and this Python has no lzma module")
        try:
            raw = lzma.decompress(blob, format=lzma.FORMAT_XZ)
        except lzma.LZMAError as exc:
            raise ValueError("Corrupt replay data ({})".format(exc))
    elif codec == "zlib+b85":
        try:
            raw = zlib.decompress(blob)
        except zlib.error as exc:
            raise ValueError("Corrupt replay data ({})".format(exc))
    else:
        raise ValueError("Unknown replay codec {!r}".format(codec))
    if sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("Corrupt replay data (checksum mismatch)")
    return json.loads(raw.decode("utf-8"))


def encode(data, keep_changes=False):
    """The house-replay-v3 object of a v2-shaped replay dict. keep_changes=False drops the traced steps' "changes"
    lists (each step keeps its state digest): they are re-derived by re-running the episode with trace=True, which is
    what the viewer does; they are kept only for a reader that must show a recording the current code can no longer
    reproduce (the viewer's recorded-change fallback)."""
    steps = data.get("steps") or []
    if not keep_changes:
        steps = [{k: v for k, v in s.items() if k != "changes"} if isinstance(s, dict) else s for s in steps]
    bulk = {key: data.get(key) for key in BULKY}
    bulk["steps"] = steps
    codec, text, sha = pack(bulk)
    out = {"format": FORMAT_V3}
    for key, value in data.items():
        if key not in BULKY and key != "format":
            out[key] = value
    out["summary"] = {"steps": len(steps), "traced": sum(1 for s in steps if isinstance(s, dict) and "state_hash" in s),
                      "changes": any(isinstance(s, dict) and "changes" in s for s in steps),
                      "phase_done": sum(1 for s in steps if isinstance(s, dict) and s.get("phase_done"))}
    out.update(codec=codec, packed=text, packed_sha256=sha)
    return out


def decode(obj):
    """The v2-shaped dict of a replay object (v2 returned as is; v3 unpacked, its "format" kept as "house-replay-v3").
    ValueError for anything else."""
    if not isinstance(obj, dict):
        raise ValueError("Unsupported replay")
    fmt = obj.get("format")
    if fmt == FORMAT_V2:
        return obj
    if fmt != FORMAT_V3:
        raise ValueError("Unsupported replay")
    bulk = unpack(obj.get("codec"), obj.get("packed") or "", obj.get("packed_sha256"))
    if not isinstance(bulk, dict):
        raise ValueError("Corrupt replay data")
    out = {k: v for k, v in obj.items() if k not in ("codec", "packed", "packed_sha256", "summary")}
    for key in BULKY:
        out[key] = bulk.get(key)
    return out


def write(path, data, compact=True, keep_changes=False):
    """Write a replay file (LF, UTF-8): house-replay-v3 when compact (keep_changes: see encode), else the v2-shaped
    data as it is (every field, changes included)."""
    obj = encode(data, keep_changes=keep_changes) if compact else dict(data, format=FORMAT_V2)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return Path(path)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read(path):
    """The v2-shaped dict of a replay file of either format."""
    return decode(load_json(path))

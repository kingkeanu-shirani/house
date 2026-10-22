"""Convert episode replays to the compact format (house_sim/kernel/replays.py, house-replay-v3).

usage: python tools/compact_replays.py PATH [PATH ...] [--drop-changes] [--check]

PATH is a replay file or a folder (every *.json replay under it, recursively). Each house-replay-v2 file is rewritten
in place as house-replay-v3 after checking that it decodes back to exactly the same content; files already in v3 and
JSON files that are not replays are left alone. The conversion keeps everything, the traced steps' change lists
included (a replay recorded by older code may no longer re-run, and then the recorded changes are the only way the
viewer can show it); --drop-changes drops them as HouseEnv.save_replay does by default (smaller; a traced re-run
recomputes them), also from compact files that kept them. --check only reports what would change. Nothing is
re-simulated, so a replay that the current code refuses (an older action fingerprint) stays exactly as refusable as
before, only about 30 times smaller.

Exit status 0 on success, 1 if a file could not be converted (it is then left untouched).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from house_sim.kernel import replays  # noqa: E402


def candidates(paths):
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for found in sorted(path.rglob("*.json")):
                yield found
        else:
            yield path


def convert(path, keep_changes=True, check=False):
    """("converted" | "v3" | "skipped" | "error", old size, new size, note) for one file."""
    size = os.path.getsize(str(path))
    try:
        obj = replays.load_json(path)
    except (OSError, ValueError) as exc:
        return "skipped", size, size, "not JSON ({})".format(type(exc).__name__)
    if not isinstance(obj, dict) or obj.get("format") not in replays.FORMATS:
        return "skipped", size, size, "not a replay"
    if obj["format"] == replays.FORMAT_V3:
        if keep_changes or not (obj.get("summary") or {}).get("changes"):
            return "v3", size, size, ""
        try:                                    # a compact file that kept its changes: --drop-changes re-encodes it
            obj = dict(replays.decode(obj), format=replays.FORMAT_V2)
        except ValueError as exc:
            return "error", size, size, str(exc)
    encoded = replays.encode(obj, keep_changes=keep_changes)
    expected = obj if keep_changes else dict(obj, steps=[{k: v for k, v in s.items() if k != "changes"}
                                                         if isinstance(s, dict) else s for s in obj.get("steps") or []])
    decoded = replays.decode(json.loads(json.dumps(encoded, ensure_ascii=False)))
    if json.dumps(dict(decoded, format=replays.FORMAT_V2), sort_keys=True) != json.dumps(expected, sort_keys=True):
        return "error", size, size, "round trip differs"
    text = json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))
    if check:
        return "converted", size, len(text.encode("utf-8")), "(check only)"
    tmp = Path(str(path) + ".compacting")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(str(tmp), str(path))
    return "converted", size, os.path.getsize(str(path)), ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert house-replay-v2 files to the compact house-replay-v3")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--drop-changes", action="store_true", help="drop the traced steps' change lists")
    parser.add_argument("--check", action="store_true", help="report only; write nothing")
    args = parser.parse_args(argv)
    totals, failed = {"before": 0, "after": 0, "converted": 0, "v3": 0, "skipped": 0}, 0
    for path in candidates(args.paths):
        status, before, after, note = convert(path, keep_changes=not args.drop_changes, check=args.check)
        if status == "error":
            failed += 1
            print("ERROR {}: {}".format(path, note))
            continue
        totals[status] += 1
        if status == "converted":
            totals["before"] += before
            totals["after"] += after
            print("{} {} -> {} bytes {}".format(path, before, after, note).rstrip())
    print("converted {converted}, already compact {v3}, not replays {skipped}; {before} -> {after} bytes".format(
        **totals) + ("; {} failed".format(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

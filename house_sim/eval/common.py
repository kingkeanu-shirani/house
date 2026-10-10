"""Helpers shared by the evaluation modules (standard library only, no network)."""
import json
from pathlib import Path

from ..catalog import ROOT


def load_authored(root=None):
    """(specs, sources, conflicts) from tasks/authored/*.json.

    Same as house_sim.compiler.load_authored except that a catalog id authored in more than one file (which happens
    for a while when task authors move or re-author a spec in another file) does not make the whole catalog
    unusable: that id is left out of `specs` / `sources` and listed in `conflicts` ({catalog_id: [file names]}), so
    nothing can silently evaluate the wrong one of two specs. Unreadable JSON still raises."""
    folder = Path(root or ROOT) / "tasks" / "authored"
    found = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("specs", []) if isinstance(data, dict) else data
        for spec in items or []:
            if isinstance(spec, dict) and spec.get("catalog_id"):
                found.setdefault(spec["catalog_id"], []).append((path.name, spec))
    specs, sources, conflicts = {}, {}, {}
    for cid, items in found.items():
        if len(items) > 1:
            conflicts[cid] = [name for name, _ in items]
            continue
        sources[cid], specs[cid] = items[0]
    return specs, sources, conflicts


def conflict_note(conflicts):
    """One readable line about excluded duplicate specs, or ''."""
    if not conflicts:
        return ""
    return "excluded {} catalog id(s) authored in more than one file: {}".format(
        len(conflicts), "; ".join("{} ({})".format(c, ", ".join(f)) for c, f in sorted(conflicts.items())[:5]))

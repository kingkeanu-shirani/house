"""List registered types the kernel's phys.liquids.holds_liquid accepts, with plumbing's verdict."""
import sys
sys.path.insert(0, r"<home>\OneDrive\Desktop\house_ai")
import warnings
warnings.simplefilter("ignore")
from house_sim.kernel.plugins import load_domains
load_domains()
from house_sim.kernel.registry import default_registry
from house_sim.kernel.phys import liquids, props
from house_sim.systems.plumbing_liquids import liquids as PL

reg = default_registry()
rows = []
for name in sorted(reg.types):
    try:
        rec = reg.make(name, "r1")
    except Exception as exc:
        continue
    rec["id"] = "x"
    state = {"entities": {"x": rec}, "rooms": {"r1": {}}}
    try:
        k = liquids.holds_liquid(state, "x")
    except Exception as exc:
        k = "ERR " + type(exc).__name__
    p = PL.holds_liquid(rec)
    if k is True:
        rows.append((name, rec.get("material"), rec.get("category"), rec.get("capacity_l"), p,
                     reg.view_props(name).get("opening"), rec.get("watertight"), rec.get("perforated"), rec.get("holes_mm")))
mode = sys.argv[1] if len(sys.argv) > 1 else "diff"
for r in rows:
    if mode == "all" or not r[4]:
        print(r)
print(len(rows), "kernel holders;", sum(1 for r in rows if not r[4]), "not plumbing holders")

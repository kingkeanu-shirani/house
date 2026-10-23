"""People's I7 repro: does a private document tell itself apart before any permission question?"""
import json
import sys
import warnings

sys.path.insert(0, r"<home>\OneDrive\Desktop\house_ai")
warnings.simplefilter("ignore")
from house_sim.kernel.plugins import load_domains
load_domains()
from house_sim.generator import generate_house
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.registry import default_registry

reg = default_registry()
for camera in ("room", "geometric"):
    rows = []
    for props in ({"title": "Letter"}, {"title": "Letter", "private": True, "owner": "resident_2"},
                  {"title": "Letter", "private": True, "owner": "resident_1"}):
        config = generate_house(5, residents="family")
        table = next(e for e, r in sorted(config["entities"].items()) if r.get("type") in ("dining_table", "kitchen_table")
                     or r.get("category") == "table")
        rec = reg.make("document", table)
        rec.update(relation="on", **props)
        rec.pop("pos", None)
        config["entities"]["thing_1"] = rec
        room = config["entities"][table]["location"]
        config["robot"]["room"] = room
        env = HouseEnv(config, seed=5, perception="realistic", camera=camera)
        state = env.world.s
        requester = state.get("requester")
        h = env.realism.handles.handle("thing_1")
        a = env.step("approach", h)
        i = env.step("inspect", h)
        r = env.step("read", h)
        try:
            title = "title" in json.loads(i["message"])
        except ValueError:
            title = i["message"][:60]
        rows.append((props.get("owner"), requester, a["ok"], i["ok"], title, r["ok"], r["message"][:70],
                     state["stats"].get("privacy_violations", 0)))
    for row in rows:
        print(camera, row)

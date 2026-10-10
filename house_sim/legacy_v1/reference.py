"""Privileged scripted oracles used ONLY for environment verification.

This is not a trained agent. Navigation can read the ground-truth graph. Task
definitions never invoke these functions and never know the reference policy.
"""
from collections import deque

from .predicates import pred


class Reference:
    def __init__(self, env): self.env = env

    def do(self, action, target="", destination="", **kwargs):
        result = self.env.step(action, target, destination, **kwargs)
        if not result["ok"]:
            raise RuntimeError("{} {} {} failed: {}".format(action, target, destination, result["message"]))
        return result

    def go(self, room):
        world = self.env.privileged_state()
        queue = deque([(self.env.room, [])])
        visited = set()
        route = None
        while queue:
            cur, path = queue.popleft()
            if cur == room:
                route = path
                break
            if cur in visited: continue
            visited.add(cur)
            queue.extend((n, path + [n]) for n in world["rooms"][cur]["exits"] if n not in visited)
        if route is None: raise RuntimeError("No route")
        for dest in route: self.do("move", dest)

    def reach(self, eid):
        world = self.env.privileged_state()
        parents, location = [], eid
        while location in world["entities"]:
            if location != eid: parents.append(location)
            location = world["entities"][location]["location"]
        if location != "hand": self.go(location)
        for parent in reversed(parents):
            obj = self.env.privileged_state()["entities"][parent]
            if obj.get("openable") and not obj.get("open"):
                self.do("open", parent)

    def drop(self):
        if self.env.holding: self.do("place", self.env.holding, self.env.room)

    def fetch(self, eid):
        if self.env.holding == eid: return
        self.drop()
        self.reach(eid)
        self.do("pick_up", eid)

    def transport(self, eid, destination):
        self.fetch(eid)
        self.reach(destination)
        dest = self.env.privileged_state()["entities"][destination]
        if dest.get("openable") and not dest.get("open"): self.do("open", destination)
        self.do("hand_over" if dest.get("category") == "person" else "place", eid, destination)


def solve(env, oracle):
    r = Reference(env)
    family, targets = oracle["family"], oracle["targets"]
    if family in ("clean", "quiet_clean"):
        method = oracle["method"]
        for tid in targets:
            if method in ("wash", "rinse"):
                r.transport(tid, "sink")
                r.do(method, tid)
                if method == "wash":
                    r.do("rinse", tid)
                    r.fetch(oracle["tools"]["dry"])
                    r.reach(tid)
                    r.do("dry", tid, tool=oracle["tools"]["dry"])
            else:
                r.fetch(oracle["tools"][method])
                r.reach(tid)
                r.do(method, tid, tool=oracle["tools"][method])
                if method in ("mop", "scrub"):
                    r.fetch(oracle["tools"]["dry"])
                    r.reach(tid)
                    r.do("dry", tid, tool=oracle["tools"]["dry"])
    elif family == "transform":
        for tid in targets:
            if oracle["method"] == "tighten": r.fetch("screwdriver")
            r.reach(tid)
            r.do(oracle["method"], tid, tool="screwdriver" if oracle["method"] == "tighten" else "")
    elif family in ("access", "switch"):
        if oracle["method"] in ("lock", "unlock"): r.fetch("key")
        for tid in targets:
            r.reach(tid)
            r.do(oracle["method"], tid)
    elif family in ("relocate", "sort", "clarify", "clarify_destination"):
        if "question" in oracle:
            r.go("room_1")
            answer = r.do("ask", "resident_1", topic=oracle["question"])["observation"]["last_answer"]["value"]
            if family == "clarify": targets = [answer]
            else: oracle["destinations"][targets[0]] = answer
        for tid in targets: r.transport(tid, oracle["destinations"][tid])
    elif family == "pickup":
        r.fetch(targets[0])
    elif family == "find":
        for tid in targets:
            r.reach(tid)
            r.do("inspect", tid)
            r.do("report", tid, field="location", value=env.observe()["visible_entities"][tid]["location"])
    elif family == "water":
        r.fetch("watering_can")
        for tid in targets:
            r.reach(tid)
            r.do("water_plant", tid, "watering_can", amount_l=oracle["amounts"][tid])
    elif family == "fill":
        for tid in targets:
            r.fetch(tid)
            r.reach("sink")
            r.do("fill", tid, "sink", amount_l=0.75)
            r.do("place", tid, "sink")
    elif family in ("inventory", "report", "verify_state"):
        for tid in targets:
            r.reach(tid)
            if family == "inventory": r.do("open", tid)
            r.do("inspect", tid)
            field = oracle["report_field"]
            if field == "count":
                value = sum(x["location"] == tid for x in env.observe()["visible_entities"].values())
            else: value = env.observe()["visible_entities"][tid][field]
            r.do("report", tid, field=field, value=value)
    elif family in ("appliance", "laundry_watch"):
        tid = targets[0]
        r.reach(tid)
        if oracle.get("method") == "pause": r.do("pause", tid)
        else:
            r.do("close", tid)
            r.do("start", tid)
        if family == "laundry_watch":
            r.do("watch", condition=pred("entities." + tid + ".cycle", "done"),
                 commands=[{"action": "announce", "text": "Laundry is done."}])
            r.do("wait", seconds=70)
            r.do("run_pending")
            r.do("inspect", tid)
            r.do("report", tid, field="cycle", value=env.observe()["visible_entities"][tid]["cycle"])
    elif family == "rain_watch":
        r.do("watch", condition=pred("weather.condition", "rain"),
             commands=[{"action": "close", "target": tid} for tid in targets])
        r.do("wait", seconds=oracle["event_time"])
        for _ in targets: r.do("run_pending")
    else: raise ValueError("No reference policy for " + family)
    return env.evaluate()


def wrong(env, oracle):
    """Plausible alternative: wrong object, destination, tool, or reported fact."""
    r = Reference(env)
    family, target = oracle["family"], oracle["targets"][0]
    if family in ("relocate", "sort"):
        for tid in oracle["targets"]:
            r.transport(tid, "worktop" if family != "sort" else "bin_0")
    elif family in ("clean", "quiet_clean", "transform", "pickup", "find"):
        if family == "quiet_clean":
            r.fetch("vacuum_tool")
            r.reach(target)
            env.step("vacuum", target, tool="vacuum_tool")
        else:
            r.fetch("decoy")
            if family != "pickup": r.do("place", "decoy", "storage") if env.privileged_state()["entities"]["storage"].get("open") else r.do("place", "decoy", env.room)
    elif family == "access":
        r.reach("storage")
        r.do("open", "storage")
    elif family == "switch":
        r.go("room_1")
        r.do("switch_off", "light_1")
    elif family in ("inventory", "report", "verify_state"):
        r.reach(target)
        if family == "inventory": r.do("open", target)
        r.do("inspect", target)
        correct = oracle["report_values"][target]
        value = not correct if isinstance(correct, bool) else (correct + 1 if isinstance(correct, int) else [])
        r.do("report", target, field=oracle["report_field"], value=value)
    elif family == "water":
        r.fetch("watering_can")
        r.reach(target)
        r.do("water_plant", target, "watering_can", amount_l=0.01)
    elif family == "fill":
        r.fetch(target)
        r.reach("sink")
        r.do("fill", target, "sink", amount_l=0.1)
    elif family == "clarify":
        r.transport(oracle["other"], "resident_1")
    elif family == "clarify_destination":
        r.transport(target, oracle["other"])
    elif family in ("appliance", "laundry_watch"):
        r.reach(target)
        if oracle.get("method") == "pause": r.do("wait", seconds=5)
        else: env.step("start", target)  # Door was left open.
    elif family == "rain_watch":
        # Close early but forget to install the requested future rule.
        for tid in oracle["targets"]: r.do("close", tid)
        r.do("wait", seconds=10)
    return env.evaluate()

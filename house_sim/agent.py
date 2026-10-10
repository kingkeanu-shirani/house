"""Observation-only search baseline. This is not a trained or LLM policy.

It explores rooms it has observed, opens unlocked non-private containers to look
inside, fetches the target object and delivers it to the destination. It reads only
observations and the messages returned by its own actions.
"""
from collections import deque


class SearchAgent:
    """Works in both perception modes: with oracle ids, or (perception "realistic") by finding the target and
    destination by their perceived labels among opaque handles, reading/inspecting look-alikes first."""

    def __init__(self, target="cup", destination="sink"):
        self.target, self.destination = target, destination
        self.map, self.seen, self.locations, self.opened = {}, set(), {}, set()
        self.plan = deque()
        self.last = None
        self.names = {}          # word -> handle once identified (handles are stable within an episode)
        self.examined = set()
        self.positions, self.rechecked = {}, set()

    def _identify(self, word, objects, holding):
        """Handle (or id) of the thing called `word`, or None. Returns ("examine", handle) for an unresolved
        look-alike that might be it."""
        if word in objects or word in holding:
            return word
        known = self.names.get(word)
        if known and (known in objects or known in holding):
            return known
        for handle in sorted(objects):
            rec = objects[handle]
            label = str(rec.get("label") or "")
            if label == word or word in label.split():
                self.names[word] = handle
                return handle
        for handle in sorted(objects):
            rec = objects[handle]
            cands = [str(c) for c in rec.get("candidates") or []]
            if any(word == c or word in c.split() for c in cands) and handle not in self.examined:
                return ("examine", handle)
        return None

    def _toward(self, start, destination):
        queue, seen = deque([(start, [])]), set()
        while queue:
            room, path = queue.popleft()
            if room == destination:
                return path[0] if path else None
            if room in seen:
                continue
            seen.add(room)
            queue.extend((n, path + [n]) for n in self.map.get(room, []) if n not in seen)
        return None

    def act(self, observation, last_result=None):
        if self.plan:
            return self.plan.popleft()
        room, objects = observation["room_id"], observation.get("visible_entities", {})
        self.map[room] = observation["room"]["exits"]
        self.seen.add(room)
        holding = observation.get("holding", [])
        posture = observation.get("robot", {}).get("posture", "standing")
        for word in (self.target, self.destination):
            found = self._identify(word, objects, holding)
            if isinstance(found, tuple):
                handle = found[1]
                self.examined.add(handle)
                self.plan.append({"action": "read" if objects[handle].get("candidates") else "inspect", "target": handle})
                return {"action": "approach", "target": handle}
        target = self._identify(self.target, objects, holding) or self.names.get(self.target) or self.target
        destination = self._identify(self.destination, objects, holding) or self.names.get(self.destination) or self.destination
        for eid in objects:
            self.locations[eid] = room
            if objects[eid].get("pos"):
                self.positions[eid] = (room, list(objects[eid]["pos"]))
        for eid in (target, destination):
            if self.locations.get(eid) == room and eid not in objects and eid not in holding:
                known = self.positions.get(eid)
                if known and known[0] == room and eid not in self.rechecked:
                    # small things can be missed from across the room: go and look where it was
                    self.rechecked.add(eid)
                    return {"action": "walk_to", "cell": known[1], "adjacent": True}
                del self.locations[eid]
        if target in holding and destination in objects:
            dest = objects[destination]
            steps = [{"action": "approach", "target": destination}]
            if dest.get("openable") and not dest.get("open"):
                steps.append({"action": "open", "target": destination})
            steps.append({"action": "place", "target": target, "destination": destination})
            self.plan.extend(steps[1:])
            return steps[0]
        if target in objects and target not in holding:
            steps = []
            for held in holding:
                steps.append({"action": "place", "target": held})
            steps += [{"action": "approach", "target": target}, {"action": "crouch"},
                      {"action": "pick_up", "target": target}, {"action": "stand"}]
            if posture != "standing":
                steps.insert(0, {"action": "stand"})
            self.plan.extend(steps[1:])
            return steps[0]
        sought = destination if target in holding else target
        if sought in self.locations and self.locations[sought] != room:
            nxt = self._toward(room, self.locations[sought])
            if nxt:
                return {"action": "move", "target": nxt}
        for eid, obj in sorted(objects.items()):
            if obj.get("openable") and not obj.get("open") and not obj.get("locked") and not obj.get("private") \
                    and eid not in self.opened and "connects" not in obj and obj.get("category") not in ("window", "door"):
                self.opened.add(eid)
                self.plan.append({"action": "open", "target": eid})
                return {"action": "approach", "target": eid}
        for neighbor in observation["room"]["exits"]:
            if neighbor not in self.seen:
                return {"action": "move", "target": neighbor}
        frontier = [n for neighbors in self.map.values() for n in neighbors if n not in self.seen]
        for destination in sorted(set(frontier)):
            nxt = self._toward(room, destination)
            if nxt:
                return {"action": "move", "target": nxt}
        return {"action": "handoff", "text": "I searched the reachable observed map and could not complete this task."}

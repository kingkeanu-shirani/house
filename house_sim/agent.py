"""Observation-only search baseline. This is not a trained or LLM policy.

It explores rooms it has observed, opens unlocked non-private containers to look
inside, fetches the target object and delivers it to the destination. It reads only
observations and the messages returned by its own actions.

With the geometric camera (kernel v3.0: the observation has a "camera" block) it also
looks around on entering a room (with the low row while the target is small), walks up to
far "object" detections whose size fits the target (crouching for small or low ones) until it
can tell what they are, looks around again from the centres of the room's quarters (small things
far off are below what the camera resolves), looks into what it opens with the wrist camera, and
when a room is surveyed without a find it kneels and looks under the beds and sofas there (peek,
or inspect where=under where the peek verb is not offered). Peeking lifts nothing for good, so
there is nothing to put back. A thing out of frame is not forgotten: what an observation once
showed as openable stays so (a small cabinet tells it only from close by) and is opened before the
survey walks on; something the wrist sees half hidden inside what it opened is inspected and looked
at again; a thing seen in a room and out of frame now is walked back to once; a room already
surveyed is not surveyed again when the search passes through it. Room mode behaves exactly as
before.
"""
from collections import deque

LOW_FURNITURE_WORDS = ("bed", "sofa", "couch", "loveseat", "futon", "daybed")
SMALL_CLASSES = ("tiny", "small", "medium")
MAX_APPROACHES_PER_ROOM = 6


class SearchAgent:
    """Works in both perception modes: with oracle ids, or (perception "realistic") by finding the target and
    destination by their perceived labels among opaque handles, reading/inspecting look-alikes first. small_target:
    look around with the low row (rows=2) and walk up to small unidentified things."""

    def __init__(self, target="cup", destination="sink", small_target=True):
        self.target, self.destination = target, destination
        self.map, self.seen, self.locations, self.opened = {}, set(), {}, set()
        self.plan = deque()
        self.last = None
        self.names = {}          # word -> handle once identified (handles are stable within an episode)
        self.examined = set()
        self.positions, self.rechecked = {}, set()
        # geometric camera
        self.small_target = bool(small_target)
        self.room_now = None
        self.looked = set()      # rooms looked around in during the current visit
        self.approached = set()  # unidentified detections walked up to
        self.peeked = set()      # low furniture looked under
        self.room_approaches = 0
        self.viewpoints = {}     # room -> survey cells not yet visited this visit
        self.unsupported = set()  # camera forms the environment refused as unknown ("rows", "peek")
        self.openables = set()   # things an observation showed as openable (a far look may not show it again)
        self.surveyed = set()    # rooms whose survey viewpoints were all looked around from (not walked again)

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

    # ----- geometric camera ----------------------------------------------------------------------------------------
    def _note_refusal(self, last_result):
        """Remember camera forms the environment does not offer (so they are not tried again)."""
        if not isinstance(last_result, dict) or last_result.get("ok") or not isinstance(self.last, dict):
            return
        message = str(last_result.get("message") or "")
        action = self.last.get("action")
        if action == "look" and "rows" in self.last and message.startswith("Unknown parameter"):
            self.unsupported.add("rows")
            self.looked.discard(self.room_now)        # look again without the low row
            self.plan.clear()
        elif action == "peek" and (message.startswith("Unknown action") or "camera: geometric" in message):
            self.unsupported.add("peek")
            self.plan.clear()

    def _openable(self, eid, obj):
        """A closed container the agent may open to look inside: openable (now, or as an earlier observation showed
        it: with the geometric camera a small cabinet shows that it opens only from close by, and a step back shows
        just "cabinet"), not locked or private, not opened by it yet, not a door or window."""
        return bool((obj.get("openable") or eid in self.openables) and not obj.get("open") and not obj.get("locked")
                    and not obj.get("private") and eid not in self.opened and "connects" not in obj
                    and obj.get("category") not in ("window", "door"))

    @staticmethod
    def _unidentified(rec):
        """A detection the camera could not identify yet: "object", or only its category word (recognize)."""
        label = str(rec.get("label") or "")
        kind = str(rec.get("kind") or "")
        return label == "object" or (bool(kind) and label.replace(" ", "_") == kind)

    @staticmethod
    def _distance(rec):
        view = rec.get("view") or {}
        if isinstance(view.get("range_m"), (int, float)):
            return float(view["range_m"])
        order = {"within reach": 0.5, "near": 1.2, "mid": 2.5, "far": 5.0, "very far": 8.0}
        return order.get(view.get("dist"), 9.0)

    @staticmethod
    def _survey_cells(observation):
        """Viewpoints that together bring every part of a room within a few metres: the centres of its quarters
        (rooms of 4 m and more across), else its centre."""
        grid = (observation.get("room") or {}).get("grid") or {}
        cols, rows = int(grid.get("cols") or 0), int(grid.get("rows") or 0)
        if cols <= 0 or rows <= 0:
            return []
        if cols >= 8 or rows >= 8:
            xs = sorted({cols // 4, (3 * cols) // 4}) if cols >= 8 else [cols // 2]
            ys = sorted({rows // 4, (3 * rows) // 4}) if rows >= 8 else [rows // 2]
            return [(x, y) for y in ys for x in xs]
        return [(cols // 2, rows // 2)]

    def _geometric_step(self, room, objects, holding, posture, pos=None):
        """The camera-aware part of a step (None when there is nothing camera-specific to do)."""
        if room not in self.looked:
            self.looked.add(room)
            look = {"action": "look", "target": "around"}
            if self.small_target and "rows" not in self.unsupported:
                look["rows"] = 2
            return look
        sought_word = self.destination if self.names.get(self.target) in holding or self.target in holding else self.target
        if self._identify(sought_word, objects, holding) is not None:
            return None
        for handle, rec in sorted(objects.items()):
            container = rec.get("location")
            if handle not in self.examined and self._unidentified(rec) and container in self.opened:
                # seen inside something it opened, half hidden by its walls: a close look tells what it is, and the
                # wrist camera looks in again to see it as that (the head may not see into a small container)
                self.examined.add(handle)
                self.plan.append({"action": "inspect", "target": container, "where": "inside"}
                                 if "peek" in self.unsupported else
                                 {"action": "peek", "target": container, "where": "inside"})
                return {"action": "inspect", "target": handle}
        if self.room_approaches < MAX_APPROACHES_PER_ROOM:
            fits = SMALL_CLASSES if self.small_target else ("medium", "large", "huge")
            candidates = sorted((self._distance(rec), handle) for handle, rec in objects.items()
                                if handle not in self.approached and self._unidentified(rec)
                                and (rec.get("size_class") in fits or rec.get("size_class") is None))
            if candidates:
                handle = candidates[0][1]
                rec = objects[handle]
                self.approached.add(handle)
                self.room_approaches += 1
                if rec.get("size_class") in ("tiny", "small") or (rec.get("view") or {}).get("elev_deg", 0) <= -30:
                    self.plan.append({"action": "crouch"})      # a small or low thing needs a lower eye to tell
                return {"action": "approach", "target": handle}
        if any(self._openable(handle, rec) for handle, rec in objects.items()):
            return None                                 # open (and look into) what it found before walking on
        viewpoints = self.viewpoints.get(room) or []
        if not viewpoints:
            self.surveyed.add(room)                     # passing through later: no second survey walk
        if viewpoints:
            # small things far off are below what the camera resolves: look around again from another part of the room
            here = tuple(pos) if pos else (0, 0)
            cell = min(viewpoints, key=lambda c: (abs(c[0] - here[0]) + abs(c[1] - here[1]), c))
            viewpoints.remove(cell)
            look = {"action": "look", "target": "around"}
            if self.small_target and "rows" not in self.unsupported:
                look["rows"] = 2
            self.plan.append(look)
            return {"action": "walk_to", "cell": list(cell), "adjacent": True}
        for handle, rec in sorted(objects.items()):
            label = str(rec.get("label") or "").lower()
            if handle in self.peeked or not any(word in label.split() for word in LOW_FURNITURE_WORDS):
                continue
            self.peeked.add(handle)
            look = {"action": "inspect", "target": handle, "where": "under"} if "peek" in self.unsupported else \
                {"action": "peek", "target": handle, "where": "under"}
            steps = [{"action": "approach", "target": handle}, {"action": "kneel"}, look]
            self.plan.extend(steps[1:])
            return steps[0]
        return None

    def act(self, observation, last_result=None):
        geometric = isinstance(observation.get("camera"), dict)
        if geometric:
            self._note_refusal(last_result)
        if self.plan:
            self.last = self.plan.popleft()
            return self.last
        room, objects = observation["room_id"], observation.get("visible_entities", {})
        self.map[room] = observation["room"]["exits"]
        self.seen.add(room)
        holding = observation.get("holding", [])
        posture = observation.get("robot", {}).get("posture", "standing")
        if geometric and room != self.room_now:
            self.room_now = room
            self.looked.discard(room)       # a new visit: look around again
            self.room_approaches = 0
            self.viewpoints[room] = [] if room in self.surveyed else self._survey_cells(observation)
        for word in (self.target, self.destination):
            found = self._identify(word, objects, holding)
            if isinstance(found, tuple):
                handle = found[1]
                self.examined.add(handle)
                self.plan.append({"action": "read" if objects[handle].get("candidates") else "inspect", "target": handle})
                self.last = {"action": "approach", "target": handle}
                return self.last
        target = self._identify(self.target, objects, holding) or self.names.get(self.target) or self.target
        destination = self._identify(self.destination, objects, holding) or self.names.get(self.destination) or self.destination
        for eid in objects:
            self.locations[eid] = room
            if objects[eid].get("pos"):
                self.positions[eid] = (room, list(objects[eid]["pos"]))
            if geometric and objects[eid].get("openable"):
                self.openables.add(eid)
        if geometric and target in objects and target not in holding and posture in ("kneeling", "crouching") \
                and (objects[target].get("view") or {}).get("dist") == "within reach":
            # found while low (under a bed or sofa): take it from here, then stand
            steps = [{"action": "pick_up", "target": target}, {"action": "stand"}]
            self.plan.extend(steps[1:])
            self.last = steps[0]
            return self.last
        if geometric and posture != "standing":
            self.last = {"action": "stand"}          # after a look under something: carry on standing
            return self.last
        for eid in (target, destination) if not geometric else ():
            # (with the geometric camera a thing out of frame is not gone: only what the head frames is listed)
            if self.locations.get(eid) == room and eid not in objects and eid not in holding:
                known = self.positions.get(eid)
                if known and known[0] == room and eid not in self.rechecked:
                    # small things can be missed from across the room: go and look where it was
                    self.rechecked.add(eid)
                    self.last = {"action": "walk_to", "cell": known[1], "adjacent": True}
                    return self.last
                del self.locations[eid]
        if target in holding and destination in objects:
            dest = objects[destination]
            steps = [{"action": "approach", "target": destination}]
            if dest.get("openable") and not dest.get("open"):
                steps.append({"action": "open", "target": destination})
            steps.append({"action": "place", "target": target, "destination": destination})
            self.plan.extend(steps[1:])
            self.last = steps[0]
            return self.last
        if target in objects and target not in holding:
            steps = []
            for held in holding:
                steps.append({"action": "place", "target": held})
            steps += [{"action": "approach", "target": target}, {"action": "crouch"},
                      {"action": "pick_up", "target": target}, {"action": "stand"}]
            if posture != "standing":
                steps.insert(0, {"action": "stand"})
            self.plan.extend(steps[1:])
            self.last = steps[0]
            return self.last
        sought = destination if target in holding else target
        if sought in self.locations and self.locations[sought] != room:
            nxt = self._toward(room, self.locations[sought])
            if nxt:
                self.last = {"action": "move", "target": nxt}
                return self.last
        if geometric and self.locations.get(sought) == room and sought not in objects and sought not in holding \
                and room in self.looked:
            # seen here before, out of frame now (a small thing far off is below what the camera resolves): go back
            # to where it was and look at it; once, then forget the place so the search goes on
            known = self.positions.get(sought)
            if known and known[0] == room and (sought, room) not in self.rechecked:
                self.rechecked.add((sought, room))
                self.plan.append({"action": "look", "target": sought})
                self.last = {"action": "walk_to", "cell": list(known[1]), "adjacent": True}
                return self.last
            del self.locations[sought]
        if geometric:
            step = self._geometric_step(room, objects, holding, posture, observation.get("robot", {}).get("pos"))
            if step is not None:
                self.last = step
                return step
        for eid, obj in sorted(objects.items()):
            if self._openable(eid, obj):
                self.opened.add(eid)
                self.plan.append({"action": "open", "target": eid})
                if geometric:
                    # the head may not see into it from where it stands: the wrist camera looks inside
                    self.plan.append({"action": "inspect", "target": eid, "where": "inside"} if "peek" in self.unsupported
                                     else {"action": "peek", "target": eid, "where": "inside"})
                self.last = {"action": "approach", "target": eid}
                return self.last
        for neighbor in observation["room"]["exits"]:
            if neighbor not in self.seen:
                self.last = {"action": "move", "target": neighbor}
                return self.last
        frontier = [n for neighbors in self.map.values() for n in neighbors if n not in self.seen]
        for destination in sorted(set(frontier)):
            nxt = self._toward(room, destination)
            if nxt:
                self.last = {"action": "move", "target": nxt}
                return self.last
        self.last = {"action": "handoff", "text": "I searched the reachable observed map and could not complete this task."}
        return self.last

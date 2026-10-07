"""Observation-only search baseline. This is not a trained or LLM policy."""
from collections import deque


class SearchAgent:
    def __init__(self, target="cup", destination="sink"):
        self.target, self.destination = target, destination
        self.map, self.seen, self.locations = {}, set(), {}

    def _toward(self, start, destination):
        queue, seen = deque([(start, [])]), set()
        while queue:
            room, path = queue.popleft()
            if room == destination: return path[0] if path else None
            if room in seen: continue
            seen.add(room)
            queue.extend((neighbor, path + [neighbor]) for neighbor in self.map.get(room, []) if neighbor not in seen)
        return None

    def act(self, observation):
        room, objects = observation['room_id'], observation.get('visible_entities', {})
        self.map[room] = observation['room']['exits']
        self.seen.add(room)
        for eid in objects: self.locations[eid] = room
        for eid in (self.target, self.destination):
            if self.locations.get(eid) == room and eid not in objects: del self.locations[eid]
        holding = observation['holding']
        if holding == self.target and self.destination in objects:
            dest = objects[self.destination]
            if dest.get('openable') and not dest.get('open'): return {'action':'open','target':self.destination}
            return {'action':'place','target':self.target,'destination':self.destination}
        if self.target in objects and holding != self.target:
            if holding: return {'action':'place','target':holding,'destination':room}
            return {'action':'pick_up','target':self.target}
        sought = self.destination if holding == self.target else self.target
        if sought in self.locations and self.locations[sought] != room:
            nxt = self._toward(room,self.locations[sought])
            if nxt: return {'action':'move','target':nxt}
        for eid,obj in sorted(objects.items()):
            if obj.get('openable') and not obj.get('open') and not obj.get('locked') and not obj.get('private'):
                return {'action':'open','target':eid}
        for neighbor in observation['room']['exits']:
            if neighbor not in self.seen: return {'action':'move','target':neighbor}
        frontier = [n for neighbors in self.map.values() for n in neighbors if n not in self.seen]
        for destination in sorted(set(frontier)):
            nxt = self._toward(room,destination)
            if nxt: return {'action':'move','target':nxt}
        return {'action':'handoff','text':'I searched the reachable observed map and could not complete this task.'}

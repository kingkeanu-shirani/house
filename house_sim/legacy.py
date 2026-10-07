"""House environment v2: Python 3.9+, standard library only.

Run: py house_env.py
Try another house: py house_env.py --rooms 20 --floors 3 --seed 42
Export an editable layout: py house_env.py --export my_house.json
Load that layout: py house_env.py --house my_house.json

Architecture:
  Layout data -> HouseEnv -> observations/actions -> future AI agent.
Rooms are a graph. Entities have unique IDs and parent locations; containers
can nest. Only current-room, unoccluded entities are observed. All rooms are
abstract spaces: dimensions and geography are metadata, NOT simulated physics.
The agent should receive observe()/step() results, never internal state.
"""
import argparse
import copy
import json
import random
import re


def make_house(room_count=5, floors=1, seed=0):
    """Reproducible connected layouts, not a model of real architecture."""
    if room_count < 1 or not 1 <= floors <= room_count:
        raise ValueError('Require rooms >= 1 and 1 <= floors <= rooms.')
    rng = random.Random(seed)
    kinds = ['kitchen', 'bedroom', 'bathroom', 'living_room', 'hallway']
    rooms, entities = {}, {}
    for i in range(room_count):
        rid = f'room_{i + 1}'
        kind = kinds[i % len(kinds)]
        floor = i * floors // room_count
        rooms[rid] = {'label': f'{kind.replace("_", " ").title()} {i + 1}',
                      'type': kind, 'floor': floor,
                      'area_m2': rng.randint(8, 35), 'exits': []}
        if i:
            # Adjacent floors connect through an abstract stair passage.
            same = [r for r, d in rooms.items() if r != rid and d['floor'] == floor]
            parent = rng.choice(same) if same else f'room_{i}'
            rooms[rid]['exits'].append(parent)
            rooms[parent]['exits'].append(rid)
        surface = f'table_{i + 1}'
        cabinet = f'cabinet_{i + 1}'
        entities[surface] = {'label': 'table', 'location': rid, 'accepts_items': True}
        entities[cabinet] = {'label': 'cabinet', 'location': rid,
                             'accepts_items': True, 'openable': True, 'open': False}
        entities[f'book_{i + 1}'] = {'label': 'book', 'location': cabinet, 'portable': True}
    source_room = rng.choice(list(rooms))
    entities['cup'] = {'label': 'cup', 'location': source_room, 'portable': True}
    entities['sink'] = {'label': 'sink', 'location': 'room_1', 'accepts_items': True}
    return {'schema_version': 1, 'name': f'Generated {room_count}-room home',
            'metadata': {'country': 'unspecified', 'dwelling_type': 'custom'},
            'start_room': 'room_1', 'rooms': rooms, 'entities': entities,
            'goal': {'object': 'cup', 'destination': 'sink'}}


def validate(config):
    """Reject broken references and cycles before starting a household."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require(isinstance(config, dict), 'House must be a JSON object.')
    require(config.get('schema_version') == 1, 'Expected schema_version: 1.')
    rooms, entities = config.get('rooms'), config.get('entities')
    require(isinstance(rooms, dict) and bool(rooms), 'rooms must be a nonempty object.')
    require(isinstance(entities, dict), 'entities must be an object.')
    require(not set(rooms) & set(entities), 'Room and entity IDs must be distinct.')
    for key in list(rooms) + list(entities):
        require(isinstance(key, str) and re.fullmatch(r'[a-z][a-z0-9_]*', key),
                'IDs must use lowercase letters, numbers and underscores.')
        require(key != 'hand', 'hand is a reserved ID.')
    require(isinstance(config.get('start_room'), str) and config['start_room'] in rooms,
            'start_room must refer to a room.')
    for rid, room in rooms.items():
        require(isinstance(room, dict), f'{rid} must be an object.')
        exits = room.get('exits')
        require(isinstance(exits, list) and all(isinstance(x, str) for x in exits),
                f'{rid}.exits must be a list of room IDs.')
        require(len(exits) == len(set(exits)), f'{rid} has duplicate exits.')
        for other in exits:
            require(other in rooms and other != rid, f'Invalid exit from {rid}.')
    for rid, room in rooms.items():
        for other in room['exits']:
            require(rid in rooms[other]['exits'], f'Connections must be two-way: {rid}, {other}.')
    for eid, entity in entities.items():
        require(isinstance(entity, dict), f'{eid} must be an object.')
        loc = entity.get('location')
        require(isinstance(loc, str) and (loc in rooms or loc in entities),
                f'Invalid location for {eid}.')
        for flag in ['portable', 'accepts_items', 'openable', 'open']:
            require(flag not in entity or isinstance(entity[flag], bool), f'{eid}.{flag} must be boolean.')
        require(not entity.get('openable') or entity.get('accepts_items'),
                f'Openable entity {eid} must accept items.')
    for eid in entities:
        seen = {eid}
        loc = entities[eid]['location']
        while loc in entities:
            require(loc not in seen, f'Containment cycle involving {eid}.')
            seen.add(loc)
            require(entities[loc].get('accepts_items'), f'{loc} cannot contain objects.')
            loc = entities[loc]['location']
    goal = config.get('goal')
    if goal is not None:
        require(isinstance(goal, dict), 'goal must be an object.')
        obj, dest = goal.get('object'), goal.get('destination')
        require(isinstance(obj, str) and obj in entities and entities[obj].get('portable'),
                'Goal object must be portable.')
        require(isinstance(dest, str) and (dest in rooms or
                (dest in entities and entities[dest].get('accepts_items'))),
                'Goal destination must be a room or storage/surface.')
        require(obj != dest, 'Object cannot be its own destination.')


class HouseEnv:
    def __init__(self, config=None):
        config = make_house() if config is None else config
        validate(config)
        self._initial = copy.deepcopy(config)
        self.reset()

    def reset(self):
        data = copy.deepcopy(self._initial)
        self._rooms = data['rooms']
        self._entities = data['entities']
        self._goal = data.get('goal')
        self.room = data['start_room']
        self.holding = None
        self.steps = 0
        self._known_map = {}
        return self.observe()

    def _visible(self, eid):
        """Traverse ancestors: a closed outer container hides everything in it."""
        if eid == self.holding:
            return True
        loc = self._entities[eid]['location']
        while loc in self._entities:
            parent = self._entities[loc]
            if parent.get('openable') and not parent.get('open', False):
                return False
            loc = parent['location']
        return loc == self.room or loc == 'hand'

    def observe(self):
        room = copy.deepcopy(self._rooms[self.room])
        self._known_map[self.room] = list(room['exits'])
        visible = {eid: copy.deepcopy(e) for eid, e in self._entities.items() if self._visible(eid)}
        return {'room_id': self.room, 'room': room, 'visible_entities': visible,
                'holding': self.holding, 'steps': self.steps}

    def known_map(self):
        """Only visited rooms and their observed exits; no hidden house map."""
        return copy.deepcopy(self._known_map)

    def _result(self, ok, message):
        return {'ok': ok, 'message': message, 'observation': self.observe()}

    def step(self, action, target='', destination=''):
        if not all(isinstance(v, str) for v in (action, target, destination)):
            return self._result(False, 'Action arguments must be strings.')
        action, target, destination = (v.strip().lower() for v in (action, target, destination))
        self.steps += 1
        if action == 'look':
            return self._result(True, 'You look around.')
        if action == 'move':
            if target not in self._rooms[self.room]['exits']:
                return self._result(False, 'Move to a listed exit.')
            self.room = target
            return self._result(True, f'Entered {target}.')
        if action not in ('inspect', 'open', 'close', 'pick_up', 'place'):
            return self._result(False, 'Unknown action.')
        if target not in self._entities or not self._visible(target):
            return self._result(False, 'That entity is not visible here.')
        entity = self._entities[target]
        if action == 'inspect':
            if entity.get('openable') and not entity.get('open', False):
                return self._result(True, 'Closed; contents are hidden.')
            children = [eid for eid, e in self._entities.items() if e['location'] == target]
            return self._result(True, 'Contents: ' + (', '.join(children) or 'none') + '.')
        if action in ('open', 'close'):
            if not entity.get('openable'):
                return self._result(False, 'This entity cannot open or close.')
            entity['open'] = action == 'open'
            return self._result(True, f'{target}: {action}.')
        if action == 'pick_up':
            if self.holding is not None:
                return self._result(False, 'Place your held object first.')
            if not entity.get('portable'):
                return self._result(False, 'This entity is fixed in place.')
            entity['location'] = 'hand'
            self.holding = target
            return self._result(True, f'Picked up {target}.')
        if self.holding != target:
            return self._result(False, 'You are not holding that entity.')
        if destination != self.room:
            if destination not in self._entities or not self._visible(destination):
                return self._result(False, 'Destination is not visible here.')
            dest = self._entities[destination]
            if not dest.get('accepts_items'):
                return self._result(False, 'Destination cannot hold items.')
            if dest.get('openable') and not dest.get('open', False):
                return self._result(False, 'Open the destination first.')
            # Reject placement into the carried entity or any of its descendants.
            ancestor = destination
            while ancestor in self._entities:
                if ancestor == target:
                    return self._result(False, 'Cannot place an object inside itself or its contents.')
                ancestor = self._entities[ancestor]['location']
        entity['location'] = destination
        self.holding = None
        return self._result(True, f'Placed {target} at {destination}.')

    def task_complete(self):
        """Ground-truth evaluator, separate from the future agent's observations."""
        if self._goal is None:
            return False
        return self._entities[self._goal['object']]['location'] == self._goal['destination']


HELP = '''Commands: look | move ROOM_ID | inspect ENTITY_ID | open ENTITY_ID
close ENTITY_ID | pick_up ENTITY_ID | place ENTITY_ID DESTINATION_ID
map | inventory | goal | reset | help | quit
Use IDs shown in observations. 'map' shows only what you have explored.
You can place objects on the current room's floor using its room ID.
This is a text environment, with no AI or physical simulation connected yet.'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--house', help='Load a JSON household definition.')
    parser.add_argument('--rooms', type=int, default=5)
    parser.add_argument('--floors', type=int, default=1)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--export', help='Write layout JSON and exit; refuses to overwrite an existing file.')
    args = parser.parse_args()
    try:
        if args.house:
            with open(args.house, encoding='utf-8') as source:
                config = json.load(source)
        else:
            config = make_house(args.rooms, args.floors, args.seed)
        env = HouseEnv(config)
        if args.export:
            with open(args.export, 'x', encoding='utf-8') as output:
                json.dump(config, output, indent=2)
            print('Exported:', args.export)
            return
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print('HOUSE ENVIRONMENT V2 -', config.get('name', 'Custom home'))
    print(HELP)
    goal_text = (f"Leave {config['goal']['object']} at {config['goal']['destination']}."
                 if config.get('goal') else 'Free exploration; no goal configured.')
    print('Goal:', goal_text)
    print(json.dumps(env.observe(), indent=2))
    arities = {'look': 1, 'move': 2, 'inspect': 2, 'open': 2, 'close': 2, 'pick_up': 2, 'place': 3}
    complete_before = env.task_complete()
    while True:
        try:
            words = input('\nHouse > ').strip().lower().split()
        except (EOFError, KeyboardInterrupt):
            print('\nGoodbye!')
            break
        if not words:
            continue
        command = words[0]
        if len(words) == 1 and command in ('quit', 'exit'):
            break
        elif len(words) == 1 and command == 'help':
            print(HELP)
        elif len(words) == 1 and command == 'map':
            print(json.dumps(env.known_map(), indent=2))
        elif len(words) == 1 and command == 'goal':
            print(goal_text)
        elif len(words) == 1 and command == 'inventory':
            print('Holding:', env.holding or 'nothing')
        elif len(words) == 1 and command == 'reset':
            print(json.dumps(env.reset(), indent=2))
            complete_before = env.task_complete()
        elif command in arities and len(words) == arities[command]:
            result = env.step(*words)
            print(result['message'])
            print(json.dumps(result['observation'], indent=2))
            complete = env.task_complete()
            if complete and not complete_before:
                print('TASK COMPLETE!')
            complete_before = complete
        else:
            print('Invalid command format. Type help.')


if __name__ == '__main__':
    main()

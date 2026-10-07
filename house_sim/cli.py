"""Interactive CLI and JSONL headless action channel."""
import argparse
import json
import shlex
import sys
from collections import Counter
from pathlib import Path
from . import __version__
from .catalog import ROOT, read_jsonl
from .engine import HouseEnv
from .language import parse
from .world import make_house

HELP = '''Commands:
 look | move ROOM | inspect ID | open ID | close ID
 pick_up ID | place ID DESTINATION | lock ID | unlock ID
 wipe ID TOOL | sweep ID TOOL | vacuum ID TOOL | mop ID TOOL
 wash ID | rinse ID | dry ID TOOL | fold ID | unfold ID
 switch_on ID | switch_off ID | start ID | pause ID
 ask PERSON TOPIC | stop | resume | undo | wait [SECONDS]
 affordances ID | snapshot NAME | diff NAME | map | inventory
 save FILE | replay_log FILE | goal | score | reset | help | quit
 say NATURAL LANGUAGE
JSON actions also work, e.g. {"action":"ask","target":"resident_1","topic":"target"}
This is a symbolic environment; household communications are simulated.'''


def show(obs):
    print('\n{} [{}], floor {}'.format(obs['room'].get('label','Room'),obs['room_id'],obs['room'].get('floor',0)))
    print('Exits:',', '.join(obs['room']['exits']) or 'none')
    print('Holding:',obs['holding'] or 'nothing')
    print('Visible:',', '.join('{} ({})'.format(k,v.get('label',k)) for k,v in obs.get('visible_entities',{}).items()) or 'none')
    print('Steps: {} | seconds: {} | battery: {}%'.format(obs['steps'],obs['time_s'],obs['battery']))


def select_spec(value):
    matches=[s for s in read_jsonl(ROOT/'tasks/specs.jsonl') if s['catalog_id']==value or s.get('family')==value]
    if not matches: raise ValueError('No task ID or family: '+value)
    if not matches[0]['family']: raise ValueError('Pending task: '+matches[0]['reason'])
    return matches[0]


def interactive(env):
    print('HOUSE ENVIRONMENT '+__version__+'\n'+HELP)
    print('Goal:',env.observe()['instruction'] or env._initial.get('goal') or 'Free exploration')
    show(env.observe())
    while True:
        try: line=input('\nHouse > ').strip()
        except (EOFError,KeyboardInterrupt): print('\nGoodbye!');break
        if not line: continue
        try:
            words=shlex.split(line);name=words[0]
            if name in ('quit','exit'):break
            if name=='help': print(HELP);continue
            if name=='map': print(json.dumps(env.known_map(),indent=2));continue
            if name=='inventory': print('Holding:',env.holding or 'nothing');continue
            if name=='goal': print(env.observe()['instruction'] or env._initial.get('goal'));continue
            if name=='score': print(json.dumps(env.evaluate(),indent=2));continue
            if name=='reset': show(env.reset());continue
            if name=='affordances' and len(words)==2: print(json.dumps(env.affordances(words[1]),indent=2));continue
            if name in ('save','replay_log') and len(words)==2:
                (env.save if name=='save' else env.save_replay)(words[1]);print('Saved:',words[1]);continue
            if line.startswith('{'):command=json.loads(line)
            elif name=='say':
                parsed=parse(line.partition(' ')[2],env.observe())
                if 'command' not in parsed:print(parsed['question'],parsed.get('candidates',[]));continue
                command=parsed['command']
            elif name=='wait':command={'action':'wait','seconds':int(words[1]) if len(words)>1 else 1}
            elif len(words)<=3:command={'action':name,'target':words[1] if len(words)>1 else '', 'destination':words[2] if len(words)>2 else ''}
            else:raise ValueError('Too many arguments; use a JSON action for named parameters')
            result=env.step(**command);print(result['message']);show(result['observation'])
            if result['evaluation']['success']:print('TASK COMPLETE at symbolic fidelity S.')
        except (ValueError,TypeError,KeyError) as exc:print('Invalid command:',exc)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Python-only household decision environment. No GPU or API key.')
    parser.add_argument('--rooms',type=int,default=5);parser.add_argument('--floors',type=int,default=1)
    parser.add_argument('--seed',type=int,default=0);parser.add_argument('--outdoors',action='store_true')
    parser.add_argument('--dwelling',choices=['house','apartment'],default='house')
    parser.add_argument('--house');parser.add_argument('--export',help='Export layout without overwriting.')
    parser.add_argument('--task',help='Catalog ID or family.');parser.add_argument('--scenario',help='Custom world/task JSON.')
    parser.add_argument('--headless',metavar='JSONL',help='Actions file or - for stdin.')
    parser.add_argument('--agent',action='store_true');parser.add_argument('--max-steps',type=int,default=5000)
    parser.add_argument('--demo',nargs='?',const='all');parser.add_argument('--verify',action='store_true')
    parser.add_argument('--report',action='store_true');parser.add_argument('--list-tasks',action='store_true')
    for option in ('load-state','save-state','replay','replay-out'):parser.add_argument('--'+option)
    parser.add_argument('--version',action='version',version=__version__)
    args=parser.parse_args(argv)
    try:
        if args.verify:
            from .audit import verify
            return 1 if verify()['failed_entries'] else 0
        if args.report:
            from .audit import report
            print(json.dumps(report(),indent=2));return 0
        if args.list_tasks:
            counts=Counter(s['family'] for s in read_jsonl(ROOT/'tasks/specs.jsonl') if s['family'])
            print('Candidate families; coverage.csv records verified status:')
            for name,n in sorted(counts.items()):print('  {}: {}'.format(name,n))
            return 0
        if args.demo=='all':
            from .audit import demo
            demo();return 0
        if args.replay:
            print(json.dumps(HouseEnv.replay(args.replay).evaluate(),indent=2));return 0
        oracle=None
        if args.load_state:env=HouseEnv.load(args.load_state)
        elif args.task or args.demo:
            from .scenarios import build
            config,task,oracle=build(select_spec(args.task or args.demo),args.seed);env=HouseEnv(config,task,args.seed)
        elif args.scenario:
            bundle=json.loads(Path(args.scenario).read_text(encoding='utf-8'));env=HouseEnv(bundle['world'],bundle['task'],args.seed)
        else:
            config=json.loads(Path(args.house).read_text(encoding='utf-8')) if args.house else make_house(args.rooms,args.floors,args.seed,args.outdoors,args.dwelling)
            env=HouseEnv(config,seed=args.seed)
        if args.export:
            with open(args.export,'x',encoding='utf-8') as stream:json.dump(env._initial,stream,indent=2)
            print('Exported:',args.export);return 0
        if args.demo:
            from .reference import solve
            result=solve(env,oracle);print('Scripted oracle:',env.observe()['instruction'])
            for record in env._history:print(json.dumps(record['command'])+' -> '+record['message'])
            print(json.dumps(result,indent=2))
        elif args.headless:
            stream=sys.stdin if args.headless=='-' else open(args.headless,encoding='utf-8')
            try:
                for n,line in enumerate(stream,1):
                    if not line.strip():continue
                    try:
                        command=json.loads(line)
                        if not isinstance(command,dict):raise ValueError('Action must be a JSON object')
                        result=env.step(**command)
                    except (ValueError,TypeError) as exc:result={'ok':False,'message':'Line {}: {}'.format(n,exc)}
                    print(json.dumps(result,ensure_ascii=True))
            finally:
                if stream is not sys.stdin:stream.close()
        elif args.agent:
            from .agent import SearchAgent
            goal=env._initial.get('goal')
            if not goal or env._task:raise ValueError('--agent supports object-to-destination goals. --demo runs task oracles.')
            agent=SearchAgent(goal['object'],goal['destination'])
            for _ in range(args.max_steps):
                if env.task_complete():break
                command=agent.act(env.observe());result=env.step(**command)
                print(json.dumps(command)+' -> '+result['message'])
                if command['action']=='handoff':break
            print(json.dumps(env.evaluate(),indent=2))
        else:interactive(env)
        if args.save_state:env.save(args.save_state)
        if args.replay_out:env.save_replay(args.replay_out)
        return 0 if not (args.agent or args.demo) or env.task_complete() else 1
    except (OSError,ValueError,KeyError,RuntimeError) as exc:parser.error(str(exc))


if __name__=='__main__':sys.exit(main())

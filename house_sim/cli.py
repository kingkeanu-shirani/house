"""Interactive CLI, JSONL headless channel, task runner, verification and reports."""
import argparse
import json
import shlex
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .catalog import ROOT, read_jsonl

HELP = '''Commands (IDs come from observations):
 look [around|up|down|left|right|behind|ID] | move ROOM | approach ID | walk_to (JSON) | inspect ID [WHERE] | read ID
 pick_up ID | place ID DESTINATION | hand_over ID PERSON | open ID | close ID | lock ID | unlock ID
 switch_on ID | switch_off ID | start ID | pause ID | crouch | kneel | stand | wait [SECONDS]
 waits that end early: {"action":"wait","seconds":3600,"interrupt_on":["urgent","doorbell"]} or "until": a condition
 phase_done (multi-phase tasks: the current request is done; the next one follows)
 ask PERSON TOPIC | speak TEXT | stop | resume | undo | snapshot NAME | diff NAME
 geometric camera (--camera geometric): face north|east|south|west|left|right|back|ID|DEGREES
                                        peek ID under|behind|inside|top|closely
 actions | describe ACTION | affordances ID | map | inventory | time | goal | score
 save FILE | replay_log FILE | reset | help | quit | say NATURAL LANGUAGE
JSON actions carry named parameters, e.g. {"action":"wipe","target":"counter_1","tool":"cloth_1"}
Everything is simulated; no real device, person or service is contacted.'''


def show(obs):
    room = obs["room"]
    print("\n{} [{}], floor {}  ({} {})".format(room.get("label", "Room"), obs["room_id"], room.get("floor", 0),
                                                obs["time"]["weekday"], obs["time"]["datetime"][11:]))
    print("Exits:", ", ".join(room.get("exits", [])) or "none")
    print("Robot at", obs["robot"].get("pos"), "| hands:", obs["robot"].get("hands"), "| posture:", obs["robot"].get("posture"))
    visible = obs.get("visible_entities", {})
    print("Visible:", ", ".join("{} ({})".format(k, v.get("label", k)) for k, v in sorted(visible.items())) or "none")
    if obs.get("people"):
        print("People:", ", ".join("{} ({})".format(k, v.get("label")) for k, v in obs["people"].items()))
    if obs.get("pets"):
        print("Pets:", ", ".join("{} ({})".format(k, v.get("label")) for k, v in obs["pets"].items()))
    for channel in ("sounds", "smells"):
        if obs.get(channel):
            print(channel.title() + ":", json.dumps(obs[channel]))
    camera = obs.get("camera")
    if camera:
        pose = camera.get("pose") or {}
        print("Camera: facing {} ({} deg), head pan {} tilt {}, eye {} m | {} frame(s) | room coverage {}".format(
            pose.get("facing"), pose.get("facing_deg"), pose.get("pan_deg"), pose.get("tilt_deg"), pose.get("height_m"),
            camera.get("frames"), camera.get("coverage")))
    print("Steps: {} | seconds: {} | battery: {}%{}".format(obs["steps"], obs["time_s"], obs["battery"], " | DARK" if obs.get("dark") else ""))


def command_of(line):
    """The command dict of one interactive line (raises ValueError for one it cannot read): JSON as is; wait, the
    speech verbs and ask take free text; face DEGREES and the where of peek / inspect become named parameters; other
    verbs take up to two words (target, destination)."""
    words = shlex.split(line)
    name = words[0]
    if line.startswith("{"):
        return json.loads(line)
    if name == "wait":
        return {"action": "wait", "seconds": int(words[1]) if len(words) > 1 else 1}
    if name in ("speak", "announce", "refuse", "explain", "confirm", "handoff"):
        return {"action": name, "text": line.partition(" ")[2]}
    if name == "ask" and len(words) >= 3:
        return {"action": "ask", "target": words[1], "topic": " ".join(words[2:])}
    if name == "face" and len(words) == 2:
        try:
            return {"action": "face", "deg": float(words[1])}
        except ValueError:
            return {"action": "face", "target": words[1]}
    if name in ("peek", "inspect") and len(words) == 3:
        return {"action": name, "target": words[1], "where": words[2]}
    if len(words) <= 3:
        return {"action": name, "target": words[1] if len(words) > 1 else "", "destination": words[2] if len(words) > 2 else ""}
    raise ValueError("Too many arguments; use a JSON action for named parameters")


def find_record(value):
    records = read_jsonl(ROOT / "tasks/specs.jsonl")
    matches = [r for r in records if r["catalog_id"] == value or (r.get("title") or "").lower() == value.lower()
               or r["text"].lower().rstrip(".") == value.lower().rstrip(".")]
    if not matches:
        raise ValueError("No catalog entry matches: " + value)
    usable = [m for m in matches if m.get("spec")]
    if not usable:
        raise ValueError("Entry {} has no authored scenario yet ({})".format(matches[0]["catalog_id"], matches[0]["reason"]))
    return usable[0]


def interactive(env):
    from .language import parse
    print("HOUSE ENVIRONMENT " + __version__ + "\n" + HELP)
    print("Goal:", env.observe().get("instruction") or "Free exploration")
    show(env.observe())
    while True:
        try:
            line = input("\nHouse > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not line:
            continue
        try:
            words = shlex.split(line)
            name = words[0]
            if name in ("quit", "exit"):
                break
            if name == "help":
                print(HELP); continue
            if name == "map":
                print(json.dumps(env.known_map(), indent=2)); continue
            if name == "inventory":
                print("Holding:", env.world.held_items() or "nothing"); continue
            if name == "time":
                print(json.dumps(env.observe()["time"], indent=2)); continue
            if name == "goal":
                print(env.observe().get("instruction") or "Free exploration"); continue
            if name == "phase_done":
                result = env.phase_done()
                print(result["message"])
                show(result["observation"] or env.observe())
                continue
            if name == "score":
                print(json.dumps(env.evaluate(), indent=2)); continue
            if name == "reset":
                show(env.reset()); continue
            if name == "actions":
                print(", ".join(sorted(env.action_names()))); continue
            if name == "describe" and len(words) == 2:
                print(json.dumps(env.describe_action(words[1]), indent=2)); continue
            if name == "affordances" and len(words) == 2:
                print(json.dumps([a for a in env.affordances(words[1]) if a["available"]], indent=2)); continue
            if name in ("save", "replay_log") and len(words) == 2:
                (env.save if name == "save" else env.save_replay)(words[1]); print("Saved:", words[1]); continue
            if name == "say" and not line.startswith("{"):
                parsed = parse(line.partition(" ")[2], env.observe())
                if "command" not in parsed:
                    print(parsed["question"], parsed.get("candidates", [])); continue
                command = parsed["command"]
            else:
                command = command_of(line)
            result = env.step(**command)
            print(result["message"])
            show(result["observation"])
            if result["evaluation"]["success"]:
                print("TASK COMPLETE (fidelity {}).".format(result["evaluation"]["fidelity"]))
        except (ValueError, TypeError, KeyError) as exc:
            print("Invalid command:", exc)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    from .eval import cli as eval_cli
    if eval_cli.wants(argv if argv is not None else sys.argv[1:]):
        return eval_cli.main(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(description="Simulated household environment for robot decision-making. Python only; no GPU or API key.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dwelling", choices=["house", "apartment", "townhouse"])
    parser.add_argument("--features", nargs="*", default=[], help="Required house features, e.g. garage pool basement")
    parser.add_argument("--rooms", type=int, help="Legacy v2-style abstract layout with this many rooms (cup/sink goal)")
    parser.add_argument("--floors", type=int, default=1)
    parser.add_argument("--outdoors", action="store_true")
    parser.add_argument("--house", help="Load a JSON household layout.")
    parser.add_argument("--export", help="Export the initial layout without overwriting.")
    parser.add_argument("--task", help="Catalog ID, title or exact catalog text to open interactively.")
    parser.add_argument("--scenario", help="Custom {world, task} JSON bundle.")
    parser.add_argument("--headless", metavar="JSONL", help="Actions file or - for stdin.")
    parser.add_argument("--agent", action="store_true", help="Run the observation-only search baseline on a legacy layout.")
    parser.add_argument("--max-steps", type=int, default=5000)
    parser.add_argument("--demo", nargs="?", const="all", help="Run a task's privileged reference plan (or all section demos).")
    parser.add_argument("--verify", action="store_true",
                        help="Verify every authored scenario on seeds 0,1,2,41,97 (the catalog evidence in "
                             "reports/verification.jsonl and the coverage reports). With --perception, --camera or "
                             "--gen it is a forced check instead, written to reports/verification_forced.jsonl.")
    parser.add_argument("--verify-only", nargs="+", help="Verify only these catalog ids, sections or authored file names.")
    parser.add_argument("--check", metavar="SPEC_FILE", help="Verify the specs in one authored file and print details (no evidence written).")
    parser.add_argument("--ids", nargs="+", help="With --check: only these catalog ids.")
    parser.add_argument("--seeds", nargs="+", type=int, help="With --check: only these seeds (default 0 1 2 41 97).")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--perception", choices=["oracle", "realistic"],
                        help="With --check/--verify: force this perception mode for every spec (default: each spec's own).")
    parser.add_argument("--camera", choices=["room", "geometric"],
                        help="Camera mode: room (room-wide visibility) or geometric (the robot's posed cameras: line of "
                             "sight, light, resolution; face / peek). Default: the task's own, else room. With "
                             "--check/--verify it is forced for every spec.")
    parser.add_argument("--gen", type=int, choices=[1, 2],
                        help="House generation (kernel v3.1): 1 (the generator as before) or 2 (households, belongings, "
                             "furnishings, climate and clock model 2). Default: the spec's house.gen, else 2 for "
                             "\"schema\": 3 specs, else 1. With --check/--verify it is forced for every spec "
                             "(migration M-GEN2).")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument("--describe", metavar="ACTION")
    for option in ("load-state", "save-state", "replay", "replay-out"):
        parser.add_argument("--" + option)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        if args.camera and (args.load_state or args.replay):
            raise ValueError("--camera cannot be combined with --load-state or --replay: the file records its camera "
                             "mode")
        if args.verify or args.verify_only:
            from .audit import verify
            # a forced mode (--perception, --camera, --gen; M-GEN2 runs --verify --gen 2) is a check written to
            # reports/verification_forced.jsonl, never the catalog evidence (audit.verify)
            run = verify(only=set(args.verify_only) if args.verify_only else None, workers=args.workers,
                         perception=args.perception, camera=args.camera, gen=args.gen)
            return 1 if run["failed_entries"] else 0
        if args.check:
            return check_file(args.check, args.workers, args.ids, args.seeds, perception=args.perception,
                              camera=args.camera, gen=args.gen)
        if args.report:
            from .audit import report
            print(json.dumps(report(), indent=2)); return 0
        if args.compile:
            from .compiler import compile_specs
            _, records, _ = compile_specs()
            print(json.dumps(dict(Counter(r["status"] for r in records)), indent=2)); return 0
        if args.list_tasks:
            records = read_jsonl(ROOT / "tasks/specs.jsonl")
            counts = Counter(r["status"] for r in records)
            print("Catalog entries by authoring status (coverage.csv has verified status):", dict(counts))
            for section, n in sorted(Counter(r["section"] for r in records if r.get("spec")).items()):
                print("  {}: {} authored".format(section, n))
            return 0
        from .kernel.engine import HouseEnv
        if args.describe:
            env = HouseEnv(seed=args.seed)
            print(json.dumps(env.describe_action(args.describe), indent=2)); return 0
        if args.demo == "all":
            from .audit import demo
            demo(); return 0
        if args.replay:
            print(json.dumps(HouseEnv.replay(args.replay).evaluate(), indent=2)); return 0
        oracle = None
        if args.load_state:
            env = HouseEnv.load(args.load_state)
        elif args.task or args.demo:
            from .tasks.runner import build
            record = find_record(args.task or args.demo)
            config, task, oracle = build(record["spec"], args.seed, camera=args.camera, gen=args.gen)
            env = HouseEnv(config, task, args.seed, camera=args.camera)
        elif args.scenario:
            bundle = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
            env = HouseEnv(bundle["world"], bundle["task"], args.seed, camera=args.camera)
        elif args.house:
            env = HouseEnv(json.loads(Path(args.house).read_text(encoding="utf-8")), seed=args.seed, camera=args.camera)
        elif args.rooms or args.agent:
            from .world import make_house
            env = HouseEnv(make_house(args.rooms or 5, args.floors, args.seed, args.outdoors), seed=args.seed,
                           camera=args.camera)
        else:
            from .generator import generate_house
            opts = {"require": args.features} if args.features else {}
            if args.dwelling:
                opts["dwelling"] = args.dwelling
            if args.gen:
                opts["gen"] = args.gen
            env = HouseEnv(generate_house(args.seed, **opts), seed=args.seed, camera=args.camera)
        if args.export:
            with open(args.export, "x", encoding="utf-8") as stream:
                json.dump(env._initial, stream, indent=2)
            print("Exported:", args.export); return 0
        if args.demo:
            from .tasks.runner import run_plan
            run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
            print("Privileged reference plan:", env.observe().get("instruction"))
            for record in env._history:
                print(json.dumps(record["command"]) + " -> " + record["message"])
            print(json.dumps(env.evaluate(), indent=2))
        elif args.headless:
            stream = sys.stdin if args.headless == "-" else open(args.headless, encoding="utf-8")
            try:
                for n, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        command = json.loads(line)
                        if not isinstance(command, dict):
                            raise ValueError("Action must be a JSON object")
                        result = env.step(**command)
                    except (ValueError, TypeError) as exc:
                        result = {"ok": False, "message": "Line {}: {}".format(n, exc)}
                    print(json.dumps(result, ensure_ascii=True, default=str))
            finally:
                if stream is not sys.stdin:
                    stream.close()
        elif args.agent:
            from .agent import SearchAgent
            goal = env._initial.get("goal")
            if not goal or env._task:
                raise ValueError("--agent supports the legacy object-to-destination goal layouts")
            agent = SearchAgent(goal["object"], goal["destination"])
            for _ in range(args.max_steps):
                if env.task_complete():
                    break
                command = agent.act(env.observe())
                result = env.step(**command)
                print(json.dumps(command) + " -> " + result["message"])
                if command["action"] == "handoff":
                    break
            print(json.dumps(env.evaluate(), indent=2))
        else:
            interactive(env)
        if args.save_state:
            env.save(args.save_state)
        if args.replay_out:
            env.save_replay(args.replay_out)
        return 0 if not (args.agent or args.demo) or env.task_complete() else 1
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.error(str(exc))


def check_file(path, workers=None, ids=None, seeds=None, perception=None, camera=None, gen=None):
    from .tasks.verify import SEEDS, verify_many
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = data.get("specs", []) if isinstance(data, dict) else data
    if ids:
        specs = [s for s in specs if s.get("catalog_id") in set(ids)]
        if not specs:
            print("No specs with those ids in", path)
            return 1
    results = verify_many(specs, seeds=tuple(seeds) if seeds else SEEDS, workers=workers, perception=perception,
                          camera=camera, gen=gen)
    failed = 0
    for result in results:
        if result["passed"]:
            print("PASS", result["catalog_id"])
            continue
        failed += 1
        print("FAIL", result["catalog_id"])
        for trial in result["trials"]:
            if trial.get("passed"):
                continue
            detail = {k: trial.get(k) for k in ("seed", "variant", "perception", "camera", "error", "trace", "initial_fails", "idle_fails", "reference_passes",
                                                 "reference_error", "reference_failures", "reference_goal_fraction", "perceivability_gaps",
                                                 "wrong_fails", "wrong_ran", "temporal_warnings", "privileged_waits") if k in trial}
            detail["wrong"] = [w for w in trial.get("wrong", []) if not w.get("fails") or not w.get("ran")]
            print("   ", json.dumps(detail, default=str)[:1500])
    print("{} passed, {} failed".format(len(results) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

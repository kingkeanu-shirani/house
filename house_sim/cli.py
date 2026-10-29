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
    parser.add_argument("--ids", nargs="+", help="With --check / --draft-hardening: only these catalog ids.")
    parser.add_argument("--seeds", nargs="+", type=int,
                        help="With --check: only these seeds (default 0 1 2 41 97; a schema-4 spec's coverage seeds). "
                             "For a schema-4 spec a debugging run that can never be HARDENED.")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--level", choices=["quick", "standard", "full"],
                        help="Schema-4 specs (VERIFY_HARDENING 5.3): quick (S0-S4), standard (S0-S6, the default of "
                             "--check) or full (S0-S8, the default of --verify).")
    parser.add_argument("--battery", default="all", help="With --check: all | none | B2,B3,... (debugging).")
    parser.add_argument("--mustpass", default="all", choices=["all", "none"], help="With --check (debugging).")
    parser.add_argument("--modes", help="With --check: own,eval (default both; a subset is debugging).")
    parser.add_argument("--heldout", type=int, help="With --check --level full: the number of held-out seeds.")
    parser.add_argument("--explain", metavar="DIR", help="With --check: write every generated policy as a plan file.")
    parser.add_argument("--json", metavar="OUT", help="With --check: write the verification records (JSON lines).")
    parser.add_argument("--no-cache", action="store_true", help="With --check: do not use the local run cache.")
    parser.add_argument("--draft-hardening", metavar="SPEC_FILE",
                        help="Print schema-4 suggestions (envelope, answers, roles, process labels, look-ahead, "
                             "budget, the observed labels of each wrong plan) for --ids; nothing is written into the "
                             "spec.")
    parser.add_argument("--out", metavar="DRAFT_JSON", help="With --draft-hardening: also write the drafts here.")
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
            # reports/verification_forced.jsonl, never the catalog evidence (audit.verify); schema-4 specs run the
            # hardened pipeline at --level (verify_many's default: full; HOUSE_SIM_VERIFY_LEVEL reaches the workers)
            import os
            from .tasks.verify import LEVEL_ENV
            previous = os.environ.get(LEVEL_ENV)
            if args.level:
                os.environ[LEVEL_ENV] = args.level
            try:
                run = verify(only=set(args.verify_only) if args.verify_only else None, workers=args.workers,
                             perception=args.perception, camera=args.camera, gen=args.gen)
            finally:
                if args.level:
                    if previous is None:
                        os.environ.pop(LEVEL_ENV, None)
                    else:
                        os.environ[LEVEL_ENV] = previous
            return 1 if run["failed_entries"] else 0
        if args.draft_hardening:
            return draft_file(args.draft_hardening, args.ids, args.seeds, gen=args.gen, out=args.out)
        if args.check:
            return check_file(args.check, args.workers, args.ids, args.seeds, perception=args.perception,
                              camera=args.camera, gen=args.gen, level=args.level, battery=args.battery,
                              mustpass=args.mustpass, modes=args.modes, heldout=args.heldout, explain=args.explain,
                              json_out=args.json, cache=not args.no_cache)
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


HINTS = {"G1": "core trials: the reference, the initial state or the idle run (fix the spec or report the perception "
                 "gap to its domain)",
         "G2": "wrong plans: at least 2 (1 per branch), each with a specific expect and a mistake, fails for it, "
               "attributed",
         "G3": "battery: a cheater passed, or a family is below 80 % met",
         "G4": "protect: map every analysis protect item once, at least 80 % live",
         "G5": "lints",
         "G6": "held-out seeds",
         "G7": "harness: an exception in a run",
         "G8": "must-pass: a legitimate variant failed",
         "G9": "escapes: review records (a signed owner row, or a logged independent review session naming the "
               "proposal's category), quota, points, at most 2 exemptions"}


def print_hardened(record, out=print):
    """The printed block of one schema-4 spec (VERIFY_HARDENING 21.2)."""
    gates = record.get("gates") or {}
    marks = " ".join("{} {}".format(g, "-" if gates.get(g) is None else "ok" if gates.get(g) else "FAIL")
                     for g in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"))
    out("{} {}  score {}  level {}  gates: {}".format(record.get("verdict"), record.get("catalog_id"),
                                                     (record.get("strength") or {}).get("score"),
                                                     record.get("level"), marks))
    for row in (record.get("core") or {}).get("failing") or []:
        out("  core  seed {} {}: {}".format(row["seed"], row["mode"], "; ".join(row["why"])[:300]))
    for why in (record.get("wrong_summary") or {}).get("why") or []:
        out("  wrong {}".format(why[:300]))
    for run in (record.get("battery") or {}).get("runs") or []:
        if run.get("outcome") not in ("fails", "blocked", "skipped:na", "skipped:tier"):
            out("  {:<14} {:<12} {}  hint: {}".format(str(run.get("id")), str(run.get("outcome")),
                                                      str(run.get("detail") or run.get("labels") or "")[:70],
                                                      run.get("hint") or ""))
    for run in (record.get("mustpass") or {}).get("runs") or []:
        if run.get("outcome") not in ("passes", "skipped:na", "skipped:tier"):
            out("  {:<14} {:<12} {}  hint: {}".format(str(run.get("id")), str(run.get("outcome")),
                                                      str(run.get("labels") or "")[:70], run.get("hint") or ""))
    for stage in ("battery", "mustpass"):
        why = (record.get(stage) or {}).get("why")
        if why:
            out("  {:<8} not run: {}".format(stage, why))
    for problem in record.get("lint") or []:
        out("  {:<12} {} {}".format(problem.get("id"), problem.get("severity"), str(problem.get("message"))[:240]))
    for line in (record.get("protect") or {}).get("lint") or []:
        out("  {}".format(line[:300]))
    unlive = (record.get("protect") or {}).get("unlive") or []
    if unlive:
        out("  protect  not live yet: {}".format("; ".join(unlive[:4])))
    for why in (record.get("escapes") or {}).get("why") or []:
        out("  escapes  {}".format(why[:300]))
    for policy, problem in sorted(((record.get("escapes") or {}).get("review_problems") or {}).items()):
        out("  escapes  {}: the accepted record does not count: {}".format(policy, str(problem)[:240]))
    lookahead = record.get("lookahead") or {}
    if lookahead.get("operated"):
        out("  lookahead {} s: the reference opened or operated {}".format(
            int(lookahead.get("s") or 0), ", ".join(lookahead["operated"][:4])))
    budget = record.get("budget") or {}
    for variant, row in sorted((budget.get("variants") or {}).items()):
        if not (row.get("ok") and row.get("share_ok")):
            out("  budget   variant {}: authored {} vs formula {}{}".format(
                variant, row.get("authored"), row.get("formula"),
                "" if row.get("share_ok") else " (the reference uses more than 80 %)"))
    held = record.get("heldout") or {}
    if held.get("ran") and not held.get("ok"):
        out("  heldout  reference failures {} skill debt {} passes {} infeasible {}".format(
            [r["seed"] for r in held.get("reference_failures") or []] if held.get("public") else
            len(held.get("reference_failures") or []), len(held.get("skill_debt") or []), held.get("passes_share"),
            len(held.get("infeasible") or [])))
    private = record.get("private") or {}
    if not private.get("ran"):
        out("  private  {}".format(private.get("status")))
    for line in record.get("harness_errors") or []:
        out("  harness  {}".format(str(line)[:300]))
    for gate, ok in sorted(gates.items()):
        if ok is False:
            out("  hint {}: {}".format(gate, HINTS.get(gate, "")))
    if record.get("debug_note"):
        out("  note: " + record["debug_note"])


def draft_file(path, ids=None, seeds=None, gen=None, out=None):
    """--draft-hardening (VERIFY_HARDENING 5.4 item 4): print the drafts for the given ids; nothing is written into
    the spec (with --out the drafts also go to that JSON file)."""
    from .kernel.plugins import load_domains
    from .tasks.verify import draft_hardening
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = data.get("specs", []) if isinstance(data, dict) else data
    if not ids:
        print("--draft-hardening needs --ids")
        return 2
    specs = [s for s in specs if s.get("catalog_id") in set(ids)]
    if not specs:
        print("No specs with those ids in", path)
        return 1
    load_domains()
    drafts = [draft_hardening(spec, seeds=seeds, gen=gen) for spec in specs]
    text = json.dumps(drafts if len(drafts) > 1 else drafts[0], indent=1, default=str)
    print(text)
    if out:
        with open(out, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(text + "\n")
    return 0


def check_file(path, workers=None, ids=None, seeds=None, perception=None, camera=None, gen=None, level=None,
               battery="all", mustpass="all", modes=None, heldout=None, explain=None, json_out=None, cache=True):
    from .tasks.verify import OWN, SEEDS, eval_modes, verify_many
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = data.get("specs", []) if isinstance(data, dict) else data
    if ids:
        specs = [s for s in specs if s.get("catalog_id") in set(ids)]
        if not specs:
            print("No specs with those ids in", path)
            return 1
    options = {}
    if modes:
        wanted = []
        for name in str(modes).split(","):
            name = name.strip()
            if name == "eval":
                wanted.extend(eval_modes())
            elif name:
                wanted.append(OWN if name == "own" else name)
        options["modes"] = wanted
    if heldout:
        options["heldout"] = heldout
    if explain:
        options["explain"] = explain
    results = verify_many(specs, seeds=tuple(seeds) if seeds else SEEDS, workers=workers, perception=perception,
                          camera=camera, gen=gen, level=level or "standard", battery=battery, mustpass=mustpass,
                          cache=cache, hard_seeds=list(seeds) if seeds else None, **options)
    if json_out:
        with open(json_out, "w", encoding="utf-8", newline="\n") as stream:
            for result in results:
                stream.write(json.dumps(result, default=str) + "\n")
    failed = 0
    for result in results:
        if result.get("format") == "house_ai.verification/2":
            if not result["passed"]:
                failed += 1
            print_hardened(result)
            continue
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

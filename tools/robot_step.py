"""The one command through which a Claude agent controls the robot in a HOUSE episode (docs/EVAL_HARNESS.md, "Claude as
the driver"). Operator commands and the robot's commands:

    python tools/robot_step.py start --session DIR --spec FILE [--catalog-id ID] --seed N [--nonce TEXT]
                                     [--camera room|geometric] [--observation sparse|compact|raw]
                                     [--robot-script robot.py]                                 (operator)
    python tools/robot_step.py prompt --session DIR                                            (operator)
    python tools/robot_step.py launch --session DIR                                            (operator)
    python tools/robot_step.py brief --session DIR --part N                                    (the robot, before its first step)
    python tools/robot_step.py step --session DIR --why "reason" --goal "what it serves" [--plan "plan"]
                                    --json '{"action": "look"}'                                (the robot's only way to act)
    python tools/robot_step.py more --session DIR                                              (the robot: the rest of a long print)
    python tools/robot_step.py finalize --session DIR --out DIR [--agent-label TEXT] [--run-id TEXT] [--stop-reason TEXT]  (operator)
    python tools/robot_step.py reprint --session DIR --out FILE                                (operator)

How it works: a stateless rebuild. The session folder stores the spec, seed, nonce and the commands so far. Every `step`
re-runs the whole episode through the harness's own loop (run._run_episode, what run.run_episode and run.run() call) with a
small Agent that replays the stored commands, then stops at the next decision and prints what the harness handed it.
No background process, the same code path as any agent, and every step re-proves determinism: each decision's digest
(result, observation, request) and what was printed are compared on every later rebuild; a difference halts the session.

What the robot gets: the filtered result (ok, message, category) and the observation. By default (`--observation sparse`) as
the lossless per-room delta of tools/flagship/printer.py: nothing the harness gave is cut, and the first visit to a room prints
at most about 24,000 characters. A print above 28,000 characters comes in pages, the rest with `more`, which costs no sim time.
`--observation compact` prints the harness's own LLM rendering (llm.compact_observation, which cuts most of a big room) and
`raw` the unfiltered JSON (up to 39,500 characters in a kitchen, over the Bash tool's 30,000-character output limit: an overflow
would push the robot to read a file, which voids the run). The --json text goes through llm.to_command, as an LLM agent's
reply does, so malformed text is the same refused, counted step. `--why`, `--goal` and `--plan` are stored as the `_why`, `_goal`
and `_plan` annotations: they reach the log and never the simulator. The robot never gets evaluation, score, labels, goals,
budgets, seed, nonce, spec path or true ids; any failure in the tool or the simulator reaches it as one plain sentence, and
the detail goes to <session>/operator_report.jsonl.

The hash chain: h_i = sha256(h_(i-1), sha256(what was printed before this command), the --json text, why, goal, plan), with h_0
from the session's hashes. A step refuses before simulating if the stored chain does not verify. chain.jsonl gets the line
before the simulator runs, so a reason exists before its outcome and is committed to what had been seen; steps.jsonl gets the
outcome afterwards.

How the briefing reaches the robot: `prompt` prints it, about 97,000 characters (the harness's prompt lists 425 actions). A
headless child reads it as its system prompt (tools/robot_launch.py) and first_message.txt as its first message. A parent
using the Agent tool can paste it, or, far cheaper for the parent, give it the few lines `launch` prints: they tell the robot
to read the same text in pages with `brief`, each below the Bash tool's output limit. A page holds only what `prompt` and
`start` print, and `brief` touches no episode state.

THE SESSION FOLDER HOLDS THE SPEC, SEED AND NONCE. The robot is only ever told the step command form (`prompt` and `launch`
print exactly that); it must never be given anything else from the folder. Python and the shell are no boundary, so
tools/audit_robot_transcript.py enforces the rest: every tool call of the robot must be this step command (or `more`, or
`brief` before the first step), one to one with the stored commands, or the run is void. Give the session folder a neutral
name: the robot sees it in the long form. With --robot-script the form is `python robot.py step ...` and the path stays hidden.

A session the robot stopped driving (it ended its turn with text, the child stalled, a usage limit) is not retried: `finalize`
idles the robot from its last command to the end of the episode with `wait` commands marked `_idle`, sets `end_reason` to
`robot_stopped` and `stop_reason` to why, and the run is scored and published like any other.

This tool never reads %USERPROFILE%\\.house_ai: it points the process's home elsewhere before importing the harness. Seeds
in the private range are refused by the harness's own guard; a seed listed only in the private manifest is not seen.
Network use stays forbidden (HOUSE_AI_FORBID_NETWORK=1). Session files: session.json, spec.json, steps.jsonl (one line per
applied command: why, goal, plan, the raw --json text, the command, the decision digest, the chain, what was printed),
chain.jsonl, start.txt, first_message.txt, briefing.txt, briefing_pages.json, more.json, closed.json, operator_report.jsonl.
"""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMAT = "house-robot-session-v2"
AGENT_NAME = "robot_step"
OVER_TEXT = "The episode is over."
BAD_CALL_TEXT = "Malformed step call: give a non-empty --why, a non-empty --goal and --json. Nothing happened."
NO_SESSION_TEXT = "There is no such session. Nothing happened."
TOOL_FAILURE_TEXT = "The command could not be processed. Nothing happened."
NOTHING_MORE_TEXT = "Nothing more to print."
LOCK_WAIT_S = 110.0                     # below the Bash tool's default timeout; parallel calls are served one at a time
LOCK_STALE_S = 600.0
OUTCOME_ENV = ("HOUSE_SIM_DOMAINS", "HOUSE_SIM_STRICT", "HOUSE_SIM_SAFE", "HOUSE_SIM_IDLE_PROBES")  # tasks/runcache.py
BASH_OUTPUT_LIMIT = 30000
PAGE_CHARS = 25000
IDLE_WAIT_S = 15                        # the what-if branches' step: a stopped robot's world runs the same way
IDLE_WHY = "Not Claude: the operator's idle after the robot stopped issuing commands ({})."
NO_STOP_REASON = "not_given"

INTERFACE = """

How you act in this session
The robot takes commands through one shell command, nothing else. Run it with your Bash tool, one call per turn, and wait for its output before you decide the next one:

{form} --why "why you are doing this, in plain words" --goal "a few words for what this serves" --json '{{"action": "look"}}'

- --why is required and must say, in your own words, why you chose this command given what you know now. --goal is required too: a few words for what this serves, repeating the same words while the goal holds. They are recorded with the command and do not affect the robot. Write them in plain words inside the double quotes, without double quotes, dollar signs or backticks, and do not start them with a slash.
- --json is the one JSON object described under Command format above, inside single quotes. Write an apostrophe inside it as \\u0027. It is the only command for this turn. "note" is not used here: your own conversation is your memory.
- Each call prints RESULT (ok, message and category of the command), the INSTRUCTION and the new OBSERVATION as JSON. A refused or malformed command costs a step like any other.
- {{"action": "done"}} ends the episode. After that the command only says that the episode is over: do not call it again; stop and give a one-line closing remark.
- Use no other tool and run no other command: no reading or searching files, no other programs, no second command chained to this one, no two calls in one turn. Anything other than this command voids the run."""

# llm.JSON_FORMAT's replacement in the sparse prompt: DESIGN revision 2's 3.3 paragraph, with three sentences added because a robot
# cannot use the print without them (marked below). {step} and {more} are the command forms.
SPARSE_FORMAT = ("How you act: the robot takes commands through one shell command. One call per turn, wait for its output. "
                 "`{step} --why \"why you chose this, now\" --goal \"a few words for what this serves; repeat the same words while the goal holds\" "
                 "[--plan \"your plan, only when it changes\"] --json '{{\"action\": \"look\"}}'`. These three texts are recorded with the command and do not "
                 "affect the robot. Write them without double quotes, dollar signs or backticks; write an apostrophe inside the JSON as \\u0027. "
                 "The JSON holds action and the catalog's parameters by name. "                                     # added: JSON_FORMAT said this
                 "Each call prints the result and what changed. "
                 "`set` and `unset` are the observation's fields; `room_first_seen`, `new`, `changed` and `out_of_view` are the things listed, "
                 "compared with the last time you were shown this room. "                                          # added: what the print's keys mean
                 "What is listed depends on where you stand. `out_of_view` means not listed from here, not that it moved. "
                 "The world may have changed since you were last shown something. "
                 "An entity field left out is false, null or empty, or looks dry, room temperature, clean. "
                 "If a print ends with a line starting more:, run `{more}` for the rest; it takes no time. "         # added: the long print
                 "{{\"action\": \"done\"}} ends the episode. Use no other tool and no other command. Anything else voids the run.")


LAUNCH = """You control a household service robot in a simulated home, through one shell command. Your complete instructions, your task and the robot's first observation are in {pages} pages. Read them now with your Bash tool, one call per turn, starting with:

{form} --part 1

Then --part 2 and so on up to --part {pages}, and follow them. Use no other tool and run no other command: the Bash calls these pages describe are the only calls of this run."""


class ToolRefusal(Exception):
    """A call the tool itself cannot serve; its text is safe to show the robot."""


class OperatorError(Exception):
    """A problem an operator command reports on stderr."""


class _AtDecision(BaseException):
    """Raised inside act() when the stored commands are used up: the harness is waiting for the robot. A BaseException so it
    crosses the harness's `except Exception` around act() instead of ending the episode as an agent_error, and so nothing
    is evaluated or written for a mere pause."""


class _Diverged(BaseException):
    """A rebuilt decision differs from what the robot was shown earlier: the determinism proof failed."""

    def __init__(self, index, stored, rebuilt):
        BaseException.__init__(self, "decision {} rebuilt differently: stored {} rebuilt {}".format(index, stored, rebuilt))
        self.index = index


class RobotStopped(RuntimeError):
    """finalize only: the robot stopped issuing commands before the episode ended (the harness records an agent_error)."""


def _agent_base():
    from house_sim.eval.agents import Agent
    return Agent


def _printer():
    """tools/flagship/printer.py by its path: tools/ is not a package."""
    name = "robot_step_printer"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, str(Path(__file__).resolve().parent / "flagship" / "printer.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def sha256_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def chain_hash(previous, printed, raw, why, goal, plan):
    """h_i of DESIGN 3.5. tools/audit_robot_transcript.py recomputes it on its own."""
    return sha256_text(json.dumps([previous, sha256_text(printed), raw, why, goal, plan or None], ensure_ascii=False, separators=(",", ":")))


def chain_genesis(session):
    """h_0: what the first command is committed to."""
    return sha256_text(json.dumps([session.get(key) for key in ("format", "spec_hash", "seed_tag", "episode_nonce", "initial_digest", "system_prompt_sha256", "prompt_sha256")]
                                  + [session["versions"].get("code_fingerprint"), session["versions"].get("harness_fingerprint")],
                                  ensure_ascii=False, separators=(",", ":")))


def chain_problem(session, steps, first_print):
    """None when the stored steps verify from h_0, else the first problem. -> (problem, head, the print the next command saw)."""
    head, printed = session.get("chain_genesis"), first_print
    if head != chain_genesis(session):
        return "h_0 does not match the session", head, printed
    for step in steps:
        expected = chain_hash(head, printed, step.get("json"), step.get("why"), step.get("goal"), step.get("plan"))
        if step.get("h") != expected:
            return "step {} does not verify".format(step.get("n")), head, printed
        head, printed = expected, step.get("output")
    return None, head, printed


def decision_digest(observation, last_result, requests):
    """The digest of everything the harness handed the agent at one decision."""
    text = json.dumps({"observation": observation, "result": last_result, "requests": requests}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_agent(commands, expected=(), stop_at_end=True, label=None, prompt_sha=None, mode=None, printed=(), idle_reason=None, capture=False):
    """An Agent replaying stored commands through the harness. stop_at_end: raise _AtDecision at the first decision past
    them (a step). Else, with idle_reason, `wait` until the episode ends (finalize of a run the robot stopped driving); with
    neither, raise RobotStopped, which the harness records as agent_error. mode: the session's observation form. printed: the
    sha256 of each decision's stored print, compared like the digests. capture: keep every decision's print (reprint)."""

    class StoredCommandsAgent(_agent_base()):
        name = AGENT_NAME
        privileged = False

        def __init__(self):
            self.commands, self.expected, self.stop_at_end = list(commands), list(expected), stop_at_end
            self.instruction, self.catalog, self.first_observation = "", [], None
            self.digests, self.requests, self.decision = [], [], None
            self.print_state = _printer().new_state() if mode == "sparse" else None
            self.prints, self.idle_commands = [], 0

        def reset(self, instruction, observation, action_catalog):
            self.instruction, self.first_observation, self.catalog = instruction, observation, action_catalog

        def on_request(self, request):
            self.requests.append(request)

        def _print(self, decision, last):
            """The text of this decision: the sparse fold runs at every one, the other forms only when it is needed."""
            if mode == "sparse":
                text, self.print_state = _printer().render(self.print_state, decision["observation"], decision["last_result"], decision["requests"])
                return text
            return render_decision(decision, self.instruction, mode) if (last or capture) else None

        def act(self, observation, last_result):
            index = len(self.digests)
            requests, self.requests = self.requests, []
            digest = decision_digest(observation, last_result, requests)
            if index < len(self.expected) and self.expected[index] != digest:
                raise _Diverged(index, self.expected[index], digest)
            self.digests.append(digest)
            decision = {"observation": observation, "last_result": last_result, "requests": requests, "digest": digest}
            at_end = index >= len(self.commands)
            text = self._print(decision, at_end and self.stop_at_end)
            if index < len(printed) and text is not None and printed[index] != sha256_text(text):
                raise _Diverged(index, printed[index], sha256_text(text))
            if capture:
                self.prints.append(text)
            if not at_end:
                return copy.deepcopy(self.commands[index])
            decision["text"] = text
            self.decision = decision
            if self.stop_at_end:
                raise _AtDecision()
            if idle_reason is not None:
                self.idle_commands += 1
                return {"action": "wait", "seconds": IDLE_WAIT_S, "_why": IDLE_WHY.format(idle_reason), "_idle": True}
            raise RobotStopped("the robot issued no further command before the episode ended")

        def info(self):
            return {"driver": AGENT_NAME, "mode": "stateless rebuild of stored commands", "label": label,
                    "commands": len(self.commands), "system_prompt_sha256": prompt_sha, "observation": mode,
                    "idle_commands": self.idle_commands, "stop_reason": idle_reason}

    return StoredCommandsAgent()


def parse_command(raw, why, goal=None, plan=None):
    """The command for a robot's --json text: the harness's LLM reply parser, then the annotations. Text with no usable command
    becomes what LLMAgent returns for one ({"action": None, "_invalid": ...}), which the harness refuses as a counted step.
    The robot's own `_` keys are dropped: `_why`, `_goal` and `_plan` come from their options, and no other annotation reaches
    the log."""
    from house_sim.eval.llm import to_command
    try:
        command, _note = to_command(raw)
    except Exception:  # noqa: BLE001  (text so deep or odd that the parser fails is unusable text like any other)
        command = None
    if command is None:
        command = {"action": None, "_invalid": "no_command", "_raw": str(raw)[:500]}
    else:
        command = {key: value for key, value in command.items() if not str(key).startswith("_")}
    command["_why"] = why
    if goal is not None:
        command["_goal"] = goal
    if plan:
        command["_plan"] = plan
    return command


def render_decision(decision, instruction, mode):
    """What the robot reads at one decision, in the compact and raw forms."""
    from house_sim.eval.llm import compact_observation
    observation, lines = decision["observation"], []
    if decision["last_result"] is not None:
        lines.append("RESULT: " + json.dumps(decision["last_result"], ensure_ascii=False))
    for request in decision["requests"]:
        lines.append("NEW REQUEST: " + json.dumps(request, ensure_ascii=False, default=str))
    current = observation.get("instruction") if isinstance(observation, dict) else None
    lines.append("INSTRUCTION: " + str(current if isinstance(current, str) and current.strip() else instruction or "(none)"))
    lines.append("OBSERVATION (JSON):")
    if mode == "raw":
        lines.append(json.dumps(observation, ensure_ascii=False, separators=(",", ":"), default=str))
    else:
        lines.append(compact_observation(observation))
    return "\n".join(lines)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _session_dir(path):
    return Path(path).resolve()


def load_session(path):
    folder = _session_dir(path)
    meta = folder / "session.json"
    if not meta.is_file():
        raise ToolRefusal(NO_SESSION_TEXT)
    session = json.loads(meta.read_text(encoding="utf-8"))
    if session.get("format") != FORMAT:
        raise ToolRefusal(NO_SESSION_TEXT)
    spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    return folder, session, spec


def read_steps(folder):
    path = folder / "steps.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def is_over(folder, steps):
    return (folder / "halted.json").is_file() or (folder / "closed.json").is_file() or bool(steps and steps[-1].get("over"))


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _append_line(path, value):
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def operator_report(folder, kind, **detail):
    """Everything the robot must not see about a failure goes here."""
    entry = dict({"utc": _now(), "kind": kind}, **detail)
    _append_line(folder / "operator_report.jsonl", entry)
    return entry


@contextlib.contextmanager
def session_lock(folder):
    """One step at a time: a model may send calls in parallel, and each rebuild must see the one before it."""
    path = folder / ".lock"
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > LOCK_STALE_S
            except OSError:
                stale = False
            if stale:
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            if time.monotonic() > deadline:
                raise ToolRefusal(TOOL_FAILURE_TEXT)
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        with contextlib.suppress(OSError):
            path.unlink()


class Rebuild:
    """One run of the episode through the harness: at a decision, ended, crashed or diverged."""

    def __init__(self, agent):
        self.agent, self.record, self.at_decision, self.crash, self.diverged, self.stray = agent, None, False, None, None, ""
        self.error = None


def rebuild(session, spec, commands, expected, printed=(), capture=False):
    from house_sim.eval import run
    agent = make_agent(commands, expected, stop_at_end=True, prompt_sha=session.get("system_prompt_sha256"), mode=session.get("observation"),
                       printed=printed, capture=capture)
    out, sink = Rebuild(agent), io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            # run.run_episode is this call without code_versions: the fingerprint digests every source file on each
            # step, so a step reuses the session's; finalize runs run.run_episode and compares them
            out.record, extra = run._run_episode(spec, dict(session["episode"]), agent, code_versions=session["versions"],
                                                 camera=session.get("camera"))
            out.error = extra.get("error")
        except _AtDecision:
            out.at_decision = True
        except _Diverged as exc:
            out.diverged = str(exc)
        except Exception as exc:  # noqa: BLE001  (e.g. ledger.py:2102: two contradicting reports escape run_episode)
            out.crash = {"error": "{}: {}".format(type(exc).__name__, exc)[:1000], "traceback": traceback.format_exc()[-6000:]}
    out.stray = sink.getvalue()
    return out


def load_spec_file(path, catalog_id=None):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperatorError("cannot read the spec file: {}".format(exc))
    if path.suffix == ".jsonl":
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        data = json.loads(text)
        items = data.get("specs", [data]) if isinstance(data, dict) else data
    specs = [s for s in items if isinstance(s, dict) and s.get("catalog_id")]
    if catalog_id:
        specs = [s for s in specs if s.get("catalog_id") == catalog_id]
    if len(specs) != 1:
        raise OperatorError("{} spec(s) match in {}: give exactly one with --catalog-id".format(len(specs), path))
    return specs[0]


def cmd_start(args):
    from house_sim.eval import run, splits
    from house_sim.tasks.verify import spec_hash
    folder = _session_dir(args.session)
    if folder.exists() and any(folder.iterdir()):
        raise OperatorError("the session folder is not empty: a session is never restarted in place")
    spec = load_spec_file(args.spec, args.catalog_id)
    try:
        episode = splits.episodes("dev", [spec], seeds=[int(args.seed)], perception="realistic")[0]
    except ValueError as exc:
        raise OperatorError("seed refused: {}".format(exc))
    nonce = args.nonce or secrets.token_hex(16)
    from house_sim.kernel.realism import NONCE_KEY
    episode[NONCE_KEY] = nonce
    session = {"format": FORMAT, "catalog_id": spec["catalog_id"], "seed": int(args.seed), "seed_tag": episode["seed_tag"],
               "episode_nonce": nonce, "episode": episode, "camera": args.camera, "observation": args.observation,
               "spec_hash": spec_hash(spec), "versions": run.versions(), "python": sys.version.split()[0],
               "env": {key: os.environ.get(key) for key in OUTCOME_ENV},
               "step_form": ({"python": "python", "script": args.robot_script, "session": None} if args.robot_script else
                             {"python": "python", "script": Path(__file__).resolve().as_posix(), "session": folder.as_posix()}),
               "created_utc": _now()}
    first = rebuild(session, spec, [], [])
    if not first.at_decision:
        detail = first.crash or first.error or (first.record or {}).get("end_reason")
        raise OperatorError("the episode cannot start: {}".format(detail))
    agent = first.agent
    text = agent.decision["text"]
    base = system_prompt_of(agent.catalog)
    prompt = briefing_text(base, session)
    session.update(initial_digest=agent.decision["digest"], instruction=agent.instruction, system_prompt_sha256=sha256_text(base),
                   prompt_sha256=sha256_text(prompt))
    session["chain_genesis"] = chain_genesis(session)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "session.json").write_text(json.dumps(session, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    _write_text(folder / "start.txt", text + "\n")
    first_message = text if session["observation"] != "sparse" else "INSTRUCTION: {}\nOBSERVATION:\n{}".format(agent.instruction, text)
    _write_text(folder / "first_message.txt", first_message + "\n")
    _write_text(folder / "briefing.txt", prompt + "\n")
    _write_text(folder / "briefing_pages.json", json.dumps(paginate(prompt, first_message), ensure_ascii=False, indent=0))
    if session["observation"] != "sparse" and len(text) > BASH_OUTPUT_LIMIT:
        print("warning: this observation has {} characters, above the Bash tool's output limit".format(len(text)), file=sys.stderr)
    return text


def step_form_text(session, verb="step"):
    form = session["step_form"]
    if form.get("session") is None:
        return "{} {} {}".format(form["python"], form["script"], verb)
    return '{} "{}" {} --session "{}"'.format(form["python"], form["script"], verb, form["session"])


def system_prompt_of(catalog):
    from house_sim.eval.llm import system_prompt
    return system_prompt(catalog, tool_mode=False)


def briefing_text(base, session):
    """The whole briefing. Sparse: the harness's prompt byte for byte with only its format paragraph replaced. Compact and raw:
    the harness's prompt, then how to call the command."""
    if session.get("observation") != "sparse":
        return base + INTERFACE.format(form=step_form_text(session))
    from house_sim.eval.llm import JSON_FORMAT
    if base.count(JSON_FORMAT) != 1:
        raise OperatorError("the harness's prompt no longer holds its format paragraph once")
    return base.replace(JSON_FORMAT, SPARSE_FORMAT.format(step=step_form_text(session), more=step_form_text(session, "more")))


def paginate(briefing, start, limit=PAGE_CHARS):
    """The briefing in whole lines per page, then the task and first observation on a page of their own."""
    pages, current, size = [], [], 0
    for line in briefing.split("\n"):
        if current and size + len(line) + 1 > limit:
            pages.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    pages.append("\n".join(current))
    pages.append("Your task begins now.\n" + start)
    return ["PAGE {} of {}\n{}".format(index + 1, len(pages), page) for index, page in enumerate(pages)]


def cmd_prompt(args):
    """The whole briefing, written when the session started."""
    try:
        folder, _session, _spec = load_session(args.session)
    except ToolRefusal as exc:
        raise OperatorError(str(exc))
    return (folder / "briefing.txt").read_text(encoding="utf-8").rstrip("\n")


def cmd_launch(args):
    """The few lines a parent gives the subagent instead of pasting the whole briefing (see the module docstring)."""
    try:
        folder, session, _spec = load_session(args.session)
    except ToolRefusal as exc:
        raise OperatorError(str(exc))
    pages = json.loads((folder / "briefing_pages.json").read_text(encoding="utf-8"))
    return LAUNCH.format(pages=len(pages), form=step_form_text(session, "brief"))


def cmd_brief(session_path, part):
    """-> one page of the briefing. Touches no episode state."""
    if not session_path:
        raise ToolRefusal(BAD_CALL_TEXT)
    folder, _session, _spec = load_session(session_path)
    path = folder / "briefing_pages.json"
    if not path.is_file():
        raise ToolRefusal(TOOL_FAILURE_TEXT)
    pages = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(part, int) or not 1 <= part <= len(pages):
        return "There are {} pages.".format(len(pages))
    return pages[part - 1]


def _halt(folder, step, error, command):
    """Nothing further may be shown as if the episode were sound."""
    operator_report(folder, "divergence", step=step, error=error, command=command)
    (folder / "halted.json").write_text(json.dumps({"utc": _now(), "error": error, "command": command},
                                                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return OVER_TEXT


def _first_print(folder):
    return (folder / "start.txt").read_text(encoding="utf-8").rstrip("\n")


def _stored_prints(session, steps, first_print):
    """The sha256 of each decision's stored print. Only the sparse form is fully folded at every decision."""
    if session.get("observation") != "sparse":
        return []
    return [sha256_text(first_print)] + [sha256_text(s["output"]) for s in steps if not s.get("over")]


def _more_line(session):
    return "more: " + step_form_text(session, "more")


def cmd_step(session_path, why, raw, goal=None, plan=None):
    """-> what the robot reads. Raises ToolRefusal with text that is safe to show."""
    if not session_path:
        raise ToolRefusal(BAD_CALL_TEXT)
    folder, session, spec = load_session(session_path)
    if why is None or not why.strip() or goal is None or not goal.strip() or raw is None:
        raise ToolRefusal(BAD_CALL_TEXT)
    with session_lock(folder):
        steps = read_steps(folder)
        if is_over(folder, steps):
            return OVER_TEXT
        command = parse_command(raw, why, goal, plan)
        n = len(steps) + 1
        first_print = _first_print(folder)
        problem, head, seen = chain_problem(session, steps, first_print)
        if problem:
            return _halt(folder, n, "the hash chain does not verify: " + problem, command)
        h = chain_hash(head, seen, raw, why, goal, plan)
        intent = {"n": n, "prev": head, "seen_sha256": sha256_text(seen), "json": raw, "why": why, "goal": goal, "plan": plan or None, "h": h, "utc": _now()}
        _append_line(folder / "chain.jsonl", intent)    # before the simulator runs: the reason exists before its outcome
        started = time.perf_counter()
        commands = [s["command"] for s in steps] + [command]
        expected = [session["initial_digest"]] + [s["digest"] for s in steps]
        out = rebuild(session, spec, commands, expected, printed=_stored_prints(session, steps, first_print))
        if out.stray:
            operator_report(folder, "stray_output", step=n, text=out.stray[:4000])
        if out.diverged:
            # the robot's earlier view cannot be reproduced
            return _halt(folder, n, out.diverged, command)
        entry = {"n": n, "why": why, "goal": goal, "plan": plan or None, "json": raw, "command": command, "utc": _now(), "prev_h": head, "h": h}
        shown = None
        if out.at_decision:
            text = out.agent.decision["text"]
            entry.update(over=False, digest=out.agent.decision["digest"], output=text)
            if session.get("observation") == "sparse":
                shown = _printer().pages(text, _more_line(session))
                if len(shown) > 1:
                    entry["pages"] = shown
                    operator_report(folder, "long_print", step=n, chars=len(text), pages=len(shown))
            elif len(text) > BASH_OUTPUT_LIMIT:
                operator_report(folder, "long_output", step=n, chars=len(text))
        else:
            text = OVER_TEXT
            entry.update(over=True, digest=None, output=text)
            if out.crash:
                # the simulator or harness failed on this command; run.py would have ended the episode at this step
                entry["end_reason"] = "harness_crash"
                operator_report(folder, "harness_crash", step=n, command=command, **out.crash)
            else:
                entry["end_reason"] = out.record.get("end_reason")
                if entry["end_reason"] in ("sim_error", "harness_error", "agent_error"):
                    operator_report(folder, entry["end_reason"], step=n, command=command, error=out.error, record=out.record)
        entry["rebuild_s"] = round(time.perf_counter() - started, 3)
        _append_line(folder / "steps.jsonl", entry)
        return shown[0] if shown else text


def cmd_more(session_path):
    """-> the next page of the last long print. Costs no sim time: it reads what the last step stored."""
    if not session_path:
        raise ToolRefusal(BAD_CALL_TEXT)
    folder, _session, _spec = load_session(session_path)
    with session_lock(folder):
        steps = read_steps(folder)
        pages = (steps[-1].get("pages") or []) if steps else []
        path = folder / "more.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        n = steps[-1]["n"] if steps else 0
        index = state.get("next", 1) if state.get("n") == n else 1
        if index >= len(pages):
            return NOTHING_MORE_TEXT
        _write_text(path, json.dumps({"n": n, "next": index + 1}))
        return pages[index]


def cmd_reprint(args):
    """Every print again from one fresh simulation of the stored commands, comparing nothing with what was stored: A4 of the
    audit compares these with the transcript. -> the path written."""
    try:
        folder, session, spec = load_session(args.session)
    except ToolRefusal as exc:
        raise OperatorError(str(exc))
    steps = read_steps(folder)
    commands = [s["command"] for s in steps if not s.get("over")]
    out = rebuild(session, spec, commands, [], capture=True)
    if not out.at_decision:
        raise OperatorError("the stored commands did not reach a decision: {}".format(out.crash or out.diverged or out.error))
    agent = out.agent
    value = {"format": "house-robot-reprint-v1", "commands": len(commands), "observation": session.get("observation"), "prints": agent.prints, "digests": agent.digests,
             "code_fingerprint": session["versions"].get("code_fingerprint"), "prompt_sha256": sha256_text(briefing_text(system_prompt_of(agent.catalog), session))}
    path = Path(args.out).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, json.dumps(value, ensure_ascii=False))
    return path.as_posix()


def _write_run_files(out, session, spec, record, status, aborted, versions, run_id, label):
    from house_sim.eval import run
    from house_sim.kernel import engine
    cid, ended = session["catalog_id"], _now()
    tag = session["seed_tag"]
    plan = [[cid, tag]]
    done = [record] if record else []
    caps = dict(run.DEFAULT_CAPS)
    mode = (record or {}).get("perception") or "realistic"
    totals = {"episodes": len(done), "successes": sum(1 for r in done if r["success"]),
              "success_rate": float(sum(1 for r in done if r["success"])) if done else 0.0,
              "tokens": {"input": 0, "output": 0}, "cost_usd": 0.0, "invalid_steps": sum(r["invalid_steps"] for r in done),
              "end_reasons": {r["end_reason"]: 1 for r in done}}
    info = {"format": "house-harness-run-v1", "harness_version": run.HARNESS_VERSION, "run_id": run_id, "status": status,
            "agent": AGENT_NAME, "agent_spec": AGENT_NAME, "agent_label": label, "privileged": False, "split": "dev",
            "filters": {"ids": [cid], "sections": None, "kinds": None, "domains": None, "limit": None, "seeds_per_entry": None},
            "seeds": [session["seed"]], "caps": caps, "workers": 1, "perception": "realistic", "perception_modes": [mode],
            "privileged_perception": False, "camera": session.get("camera"),
            "camera_modes": sorted({r.get("camera") for r in done if r.get("camera")}),
            "code_fingerprint": versions["code_fingerprint"], "harness_fingerprint": versions["harness_fingerprint"],
            "actions_hash": engine.actions_fingerprint(), "spec_hashes": {cid: session["spec_hash"]},
            "commitment_root": run._commitment_root(), "episode_source": "robot_step session", "authored_conflicts": {},
            "replays_saved": True,
            "verification": {"mode": "unverified", "perception": None, "camera": None, "seeds": None,
                             "per_spec": {cid: {"verified": False, "source": "none",
                                                "detail": "robot_step finalize does not verify the spec"}},
                             "excluded": []},
            "agent_options": {}, "allow_network": False, "llm_cache_dir": None, "llm_cache_private": False, "prices": None,
            "llm_transport": None, "llm_cache": None, "sim_safe_mode": bool(getattr(engine, "SAFE", False)),
            "python": sys.version.split()[0], "started": session.get("created_utc"), "ended": ended, "episodes_planned": 1,
            "planned_episodes": plan,
            "planned_episodes_sha256": hashlib.sha256(json.dumps(plan, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "aborted": aborted, "episodes_run": len(done), "episodes_skipped": 1 - len(done),
            "skipped_reason": aborted if not done else None,
            "ledger": {"spent_usd": 0.0, "reserved_usd": 0.0, "max_cost_usd": None}, "totals": totals}
    (out / "run.json").write_text(json.dumps(info, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    with open(out / "episodes.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        for item in done:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def cmd_finalize(args):
    """One complete harness run of the stored commands. -> (printed JSON text, exit status)."""
    from house_sim.eval import run
    from house_sim.kernel.engine import HouseEnv
    from house_sim.kernel.replays import read as read_replay
    try:
        folder, session, spec = load_session(args.session)
    except ToolRefusal as exc:
        raise OperatorError(str(exc))
    steps = read_steps(folder)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or "robot_" + folder.name
    versions = run.versions()
    commands = [s["command"] for s in steps]
    expected = [session["initial_digest"]] + [s["digest"] for s in steps if s.get("digest")]
    # the robot stopped driving: its world runs on to the end of the episode (critique finding 14). A halted session is not this
    # case: its printed history cannot be trusted, so it stays what run() makes of a failure
    stopped = not (steps and steps[-1].get("over")) and not (folder / "halted.json").is_file()
    stop_reason = (args.stop_reason or NO_STOP_REASON) if stopped else None
    agent = make_agent(commands, expected, stop_at_end=False, label=args.agent_label, prompt_sha=session.get("system_prompt_sha256"), mode=session.get("observation"),
                       printed=_stored_prints(session, steps, _first_print(folder)), idle_reason=stop_reason)
    first_print = _first_print(folder)
    chain_error, chain_head, _seen = chain_problem(session, steps, first_print)
    done_hashes = {s.get("h") for s in steps}
    abandoned = []
    if (folder / "chain.jsonl").is_file():
        lines = [json.loads(line) for line in (folder / "chain.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        abandoned = [line["n"] for line in lines if line.get("h") not in done_hashes]
    report = {"run_id": run_id, "out": out.as_posix(), "commands": len(commands), "agent_label": args.agent_label, "chain_verifies": chain_error is None,
              "chain_error": chain_error, "chain_head": chain_head, "commands_that_never_returned": abandoned}
    record, failure = None, None
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            record = run.run_episode(spec, dict(session["episode"]), agent, out_dir=out, run_id=run_id, camera=session.get("camera"))
        except _Diverged as exc:
            failure = "determinism: {}".format(exc)
        except Exception as exc:  # noqa: BLE001  (what run.run() does with a harness failure: the run is aborted)
            failure = "harness error on {} {}: {}: {}".format(session["catalog_id"], session["seed_tag"], type(exc).__name__, str(exc)[:300])
            report["traceback"] = traceback.format_exc()[-6000:]
    if record is not None and stopped:
        # end_reason says why; the harness's own reason is kept beside it
        stop = {"end_reason": "robot_stopped", "harness_end_reason": record.get("end_reason"), "stop_reason": stop_reason, "robot_commands": len(commands),
                "idle_commands": agent.idle_commands}
        record.update(stop)
        log_path = out / record["log"]
        log = json.loads(log_path.read_text(encoding="utf-8"))
        log.update(stop)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (folder / "closed.json").write_text(json.dumps(dict(stop, utc=_now()), ensure_ascii=False, indent=1), encoding="utf-8")
        report["stop"] = stop
    for name in ("spec.json", "start.txt", "first_message.txt", "briefing.txt", "steps.jsonl", "chain.jsonl", "operator_report.jsonl", "halted.json", "closed.json"):
        if (folder / name).is_file():
            (out / "session").mkdir(exist_ok=True)
            shutil.copyfile(str(folder / name), str(out / "session" / name))
    reports = []
    if (folder / "operator_report.jsonl").is_file():
        reports = [json.loads(line) for line in (folder / "operator_report.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    report["operator_reports"] = [{k: r.get(k) for k in ("utc", "kind", "step", "error", "chars") if r.get(k) is not None} for r in reports]
    report["fingerprints_as_at_start"] = versions == session.get("versions")
    if failure is not None:
        _write_run_files(out, session, spec, None, "aborted", failure, versions, run_id, args.agent_label)
        (out / "crash.json").write_text(json.dumps({"error": failure, "traceback": report.get("traceback"),
                                                    "commands": commands}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        report.update(status="aborted", aborted=failure, evaluation=None)
        return json.dumps(report, ensure_ascii=False, indent=1, default=str), 3
    _write_run_files(out, session, spec, record, "complete", None, versions, run_id, args.agent_label)
    log = json.loads((out / record["log"]).read_text(encoding="utf-8"))
    scored = run.replay_log(log, spec)
    replay_path = out / log["replay"]
    final_hash = read_replay(replay_path).get("final_hash")
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        replayed = HouseEnv.replay(replay_path)
    report.update(status="complete", record=record, evaluation=scored["evaluation"], success=scored["success"],
                  final_state_hash=final_hash, replay_digests_checked=replayed is not None,
                  code_fingerprint_match=scored.get("code_fingerprint_match"),
                  harness_fingerprint_match=scored.get("harness_fingerprint_match"))
    return json.dumps(report, ensure_ascii=False, indent=1, default=str), 0


def _build_parser():
    class Parser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            kwargs["allow_abbrev"] = False      # the audit reads the exact form: --w is not --why
            argparse.ArgumentParser.__init__(self, *args, **kwargs)

        def error(self, message):
            raise ToolRefusal(BAD_CALL_TEXT)

    parser = Parser(prog="robot_step", description="One robot command through the HOUSE harness (see the module docstring).")
    sub = parser.add_subparsers(dest="command", parser_class=Parser)
    start = sub.add_parser("start")
    start.add_argument("--session", required=True)
    start.add_argument("--spec", required=True)
    start.add_argument("--catalog-id")
    start.add_argument("--seed", required=True, type=int)
    start.add_argument("--nonce")
    start.add_argument("--camera", choices=("room", "geometric"))
    start.add_argument("--observation", choices=("sparse", "compact", "raw"), default="sparse")
    start.add_argument("--robot-script")
    step = sub.add_parser("step")
    step.add_argument("--session")
    step.add_argument("--why")
    step.add_argument("--goal")
    step.add_argument("--plan")
    step.add_argument("--json", dest="raw_json")
    more = sub.add_parser("more")
    more.add_argument("--session")
    prompt = sub.add_parser("prompt")
    prompt.add_argument("--session", required=True)
    launch = sub.add_parser("launch")
    launch.add_argument("--session", required=True)
    brief = sub.add_parser("brief")
    brief.add_argument("--session")
    brief.add_argument("--part", type=int)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--session", required=True)
    finalize.add_argument("--out", required=True)
    finalize.add_argument("--agent-label")
    finalize.add_argument("--run-id")
    finalize.add_argument("--stop-reason")
    reprint = sub.add_parser("reprint")
    reprint.add_argument("--session", required=True)
    reprint.add_argument("--out", required=True)
    return parser


def _session_argument(argv):
    for index, word in enumerate(argv):
        if word == "--session" and index + 1 < len(argv):
            return argv[index + 1]
        if word.startswith("--session="):
            return word.split("=", 1)[1]
    return None


def _robot_step(argv):
    """The robot's commands: whatever happens, one plain text and status 0, never a traceback."""
    session_path = _session_argument(argv)
    try:
        args = _build_parser().parse_args(argv)
        if args.command == "brief":
            return cmd_brief(args.session, args.part)
        if args.command == "more":
            return cmd_more(args.session)
        return cmd_step(args.session, args.why, args.raw_json, args.goal, args.plan)
    except ToolRefusal as exc:
        return str(exc)
    except BaseException as exc:  # noqa: BLE001  (SystemExit, a full disk, a broken file: the robot learns nothing from it)
        with contextlib.suppress(BaseException):
            folder = _session_dir(session_path)
            if (folder / "session.json").is_file():
                operator_report(folder, "tool_failure", error="{}: {}".format(type(exc).__name__, exc)[:1000],
                                traceback=traceback.format_exc()[-6000:])
        return TOOL_FAILURE_TEXT


USAGE = "usage: robot_step.py start|prompt|launch|brief|step|more|finalize|reprint ... (see the module docstring)"


def main(argv=None, emit=None):
    """-> exit status. emit: print-like for what the command prints (tests)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    emit = emit or print
    if argv[:1] in (["step"], ["brief"], ["more"]):
        emit(_robot_step(argv))
        return 0
    try:
        args = _build_parser().parse_args(argv)
    except ToolRefusal:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if args.command == "start":
            emit(cmd_start(args))
        elif args.command == "prompt":
            emit(cmd_prompt(args))
        elif args.command == "launch":
            emit(cmd_launch(args))
        elif args.command == "reprint":
            emit(cmd_reprint(args))
        elif args.command == "finalize":
            text, status = cmd_finalize(args)
            emit(text)
            return status
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except OperatorError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


def prepare_process(argv):
    """Before any house_sim import. The session's outcome-relevant environment is re-applied (so a step cannot run under
    other simulator settings than the start did), the private home is out of reach and the network is forbidden."""
    os.environ["HOUSE_AI_FORBID_NETWORK"] = "1"
    neutral = str(Path(tempfile.gettempdir()) / "house_robot_step_no_private_home")
    os.environ["USERPROFILE"] = neutral
    os.environ["HOME"] = neutral
    recorded = None
    session_path = _session_argument(argv)
    if session_path:
        with contextlib.suppress(Exception):
            recorded = json.loads((_session_dir(session_path) / "session.json").read_text(encoding="utf-8")).get("env")
    if isinstance(recorded, dict):
        for key in OUTCOME_ENV:
            if recorded.get(key) is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(recorded[key])
    else:
        os.environ.setdefault("HOUSE_SIM_SAFE", "1")    # what the harness CLI does for external agents (run.py main)


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # every step is a new process: without bytecode (PYTHONDONTWRITEBYTECODE is set on this project's machines) each one
    # compiles about 470 modules, several seconds. The cache sits outside the checkout and Python validates it by source.
    sys.dont_write_bytecode = False
    sys.pycache_prefix = str(Path(tempfile.gettempdir()) / "house_robot_step_pycache")
    prepare_process(argv)
    return main(argv)


if __name__ == "__main__":
    sys.exit(cli())

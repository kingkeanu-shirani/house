"""Start, audit and finalize a Claude run of the robot as a headless Claude Code child (docs/EVAL_HARNESS.md, "Claude as the
driver"; DESIGN 3.2). Stdlib only. Commands, all from the checkout:

    python tools/robot_launch.py prepare --commit REV --spec FILE [--catalog-id ID] --seed N [--nonce TEXT] [--root DIR] [--name sim_x]
    python tools/robot_launch.py launch --run-dir DIR [--dry] [--stall-s 900] [--max-wall-s 14400]
    python tools/robot_launch.py audit --run-dir DIR [--finalized OUT]
    python tools/robot_launch.py finalize --run-dir DIR --out OUT [--agent-label TEXT]

`prepare` makes a neutral run directory: a frozen `git archive` of the named commit in sim/, the session in run/, and work/ with
only a few-line robot.py, the child's working directory. So the robot sees `python robot.py step ...`, no spec path, no project word,
and edits in the checkout cannot move the code under a run. `--spec` is read from the frozen tree when the path exists there. `launch` starts the child there and waits;
`--dry` prints the exact command, the environment's names and what would be checked, and starts nothing.

The child's environment is built from CHILD_ENV, a list of names, plus FIXED_ENV. Nothing else from this process reaches it, so
ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL and CLAUDE_CODE_USE_* are left out by not being named, and their
values are never read or printed. Two more gates stop an API bill: before starting, `claude auth status` under that environment
must report a login that is not a key; and the child's first record must say apiKeySource "none", or it is killed at once.
`--bare` is never used: its auth is an API key only.

When the child ends without the episode being over, launch writes why in child_exit.json and `finalize` passes it on: the robot
idles from its last command to the end of the episode and the run stays published (tools/robot_step.py).
"""
import argparse
import glob
import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = "work"                           # the child's working directory
MODEL = "claude-opus-5-5"               # frozen for the runs (RULINGS: default effort, so no --effort)
ALLOWED_TOOLS = "Bash(python robot.py *)"
AUTOCOMPACT = "auto"                    # frozen: compaction is disclosed by audit check A7, an overflow would end the run
ARCHIVE_PATHS = ("house_sim", "tools", "tasks")     # what a run needs of the commit; the fingerprints cover house_sim
DEFAULT_ROOT = Path.home() / "robot_run"
STALL_S = 900.0                         # no record from the child for this long
MAX_WALL_S = 14400.0
FORBIDDEN_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
FORBIDDEN_PREFIXES = ("CLAUDE_CODE_USE_",)
CHILD_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS", "USERPROFILE", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "HOME", "HOMEDRIVE",
             "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
             "COMMONPROGRAMW6432", "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "TZ",
             "CLAUDE_CODE_GIT_BASH_PATH",       # where Git Bash is, when it had to be said
             "CLAUDE_CODE_OAUTH_TOKEN")         # `claude setup-token`: the subscription's own long-lived login, when the owner made one
FIXED_ENV = {"HOUSE_AI_FORBID_NETWORK": "1", "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*",
             "BASH_MAX_OUTPUT_LENGTH": "30000", "BASH_DEFAULT_TIMEOUT_MS": "600000", "BASH_MAX_TIMEOUT_MS": "600000",
             "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_COST_WARNINGS": "1"}
CHILD_SETTINGS = json.dumps({"attribution": {"commit": "", "pr": ""}}, separators=(",", ":"))   # no git-attribution reminder in the priming
# The child's working directory holds only this. Claude Code lets read-only shell commands through without asking inside the working directory (tested: `cat`
# and `; cat` ran under this very allow-list), so the session and the tree sit beside it, where asking means refused. Only the robot's verbs get through.
ROBOT_PY = '''"""The robot's one command."""
import os, runpy, sys
top = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tool = os.path.join(top, "sim", "tools", "robot_step.py")
verb = sys.argv[1:2] if sys.argv[1:2] in (["step"], ["more"]) else ["step", "--refused"]
sys.argv = [tool] + verb + sys.argv[2:] + ["--session", os.path.join(top, "run")]
runpy.run_path(tool, run_name="__main__")
'''


class LaunchError(Exception):
    """A problem the operator reads on stderr."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def child_env(source=None):
    """The child's whole environment: named variables from `source`, then the fixed ones. No other name can get in. This Python's
    own folder goes first on PATH: through the Windows Store alias every `python robot.py` costs seconds more to start."""
    source = os.environ if source is None else source
    env = {name: source[name] for name in CHILD_ENV if name in source}
    env.update(FIXED_ENV)
    env["PATH"] = str(Path(sys.executable).resolve().parent) + os.pathsep + env.get("PATH", "")
    for name in env:
        if name in FORBIDDEN_NAMES or name.startswith(FORBIDDEN_PREFIXES):
            raise LaunchError("the child's environment names {}".format(name))
    return env


def child_command(run_dir, meta, executable="claude"):
    """The exact command. The briefing is a file and the first message is stdin: 96,000 characters do not fit a Windows command line."""
    return [executable, "-p", "--safe-mode", "--model", meta["model"], "--system-prompt-file", str(Path(run_dir) / "run" / "briefing.txt"), "--tools", "Bash",
            "--allowedTools", ALLOWED_TOOLS, "--permission-prompts", "none", "--strict-mcp-config", "--disable-slash-commands", "--settings", CHILD_SETTINGS,
            "--session-id", meta["session_id"], "--autocompact", AUTOCOMPACT, "--output-format", "stream-json", "--verbose"]


def _git(*args, capture=True):
    done = subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=capture)
    if done.returncode != 0:
        raise LaunchError("git {} failed: {}".format(args[0], done.stderr.decode("utf-8", "replace")[:300]))
    return done.stdout


def freeze_tree(commit, target, overlay=()):
    """`git archive` of the commit into target, then any overlay files from the working tree (a throwaway check only: a run
    named in public comes from a commit alone). -> (full commit id, overlay records)."""
    full = _git("rev-parse", "--verify", commit + "^{commit}").decode("ascii").strip()
    data = _git("archive", "--format=tar", full, *ARCHIVE_PATHS)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(str(target))
    applied = []
    for rel in overlay:
        source = ROOT / rel
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(target / rel))
        applied.append({"path": rel, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    return full, applied


def run_tool(run_dir, *args):
    """tools/robot_step.py in the frozen tree, under its own process. -> CompletedProcess."""
    env = dict(child_env(), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(Path(run_dir) / "sim" / "tools" / "robot_step.py")] + list(args), capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(run_dir))


def cmd_prepare(args):
    name = args.name or "sim_" + secrets.token_hex(3)
    run_dir = (Path(args.root) if args.root else DEFAULT_ROOT).resolve() / name
    if run_dir.exists():
        raise LaunchError("the run directory exists already: a run is never restarted in place: {}".format(run_dir))
    run_dir.mkdir(parents=True)
    sim = run_dir / "sim"
    commit, overlay = freeze_tree(args.commit, sim, args.overlay or ())
    spec = Path(args.spec)
    in_tree = sim / spec if not spec.is_absolute() else None
    spec_path = in_tree if in_tree is not None and in_tree.is_file() else spec.resolve()
    (run_dir / WORK).mkdir()
    (run_dir / WORK / "robot.py").write_text(ROBOT_PY, encoding="utf-8")
    start = ["start", "--session", str(run_dir / "run"), "--spec", str(spec_path), "--seed", str(args.seed)]
    if not args.agent_tool_form:
        start += ["--robot-script", "robot.py"]     # the fallback's subagent has another working directory, so it gets the long form
    if args.catalog_id:
        start += ["--catalog-id", args.catalog_id]
    if args.nonce:
        start += ["--nonce", args.nonce]
    done = run_tool(run_dir, *start)
    if done.returncode != 0:
        raise LaunchError("the session did not start: {}".format(done.stderr[-1000:]))
    launch = run_dir / "launch"
    launch.mkdir()
    message = (run_dir / "run" / "first_message.txt").read_text(encoding="utf-8")
    if args.first_message_suffix:
        message = message.rstrip("\n") + "\n\n" + args.first_message_suffix + "\n"
    with open(launch / "first_message.txt", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(message)
    meta = {"format": "house-robot-launch-v1", "commit": commit, "overlay": overlay, "from_commit_alone": not overlay, "spec": str(spec_path), "spec_inside_commit": spec_path == in_tree,
            "seed": args.seed, "model": MODEL, "session_id": str(uuid.uuid4()), "autocompact": AUTOCOMPACT, "first_message_suffix": args.first_message_suffix, "created_utc": _now()}
    _write_json(launch / "run_meta.json", meta)
    return {"run_dir": run_dir.as_posix(), "commit": commit, "from_commit_alone": not overlay, "session_id": meta["session_id"]}


def _load_meta(run_dir):
    path = Path(run_dir) / "launch" / "run_meta.json"
    if not path.is_file():
        raise LaunchError("not a prepared run directory: {}".format(run_dir))
    return json.loads(path.read_text(encoding="utf-8"))


def auth_gate(executable, env):
    """-> the non-identifying fields of `claude auth status` under the child's environment. Raises unless it is a login and not a key."""
    done = subprocess.run([executable, "auth", "status"], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        status = json.loads(done.stdout)
    except ValueError:
        raise LaunchError("`claude auth status` printed no JSON; nothing was started")
    kept = {key: status.get(key) for key in ("loggedIn", "authMethod", "apiProvider", "subscriptionType", "apiKeySource") if key in status}
    method = str(kept.get("authMethod", "")).lower()
    if not kept.get("loggedIn"):
        raise LaunchError("Claude Code is not logged in under the child's environment ({}). The owner runs `claude auth login` once in a terminal, on the subscription; nothing was started".format(kept))
    if kept.get("apiKeySource") or "key" in method or "api" in method:
        raise LaunchError("Claude Code would use an API key ({}); nothing was started".format(kept))
    if kept.get("apiProvider") not in (None, "firstParty"):
        raise LaunchError("Claude Code would use another provider ({}); nothing was started".format(kept))
    return kept


def _kill_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        process.kill()


def stop_reason_of(exit_info, episode_over):
    """Why the robot stopped driving, in one word, from what the child did. None when the episode ended properly."""
    if episode_over:
        return None
    if exit_info.get("killed"):
        return exit_info["killed"]
    result = exit_info.get("result") or {}
    if not result:
        return "child_ended_without_a_result"
    if not result.get("is_error"):
        return "turn_ended_without_done"
    words = " ".join(str(result.get(key, "")) for key in ("subtype", "result", "api_error_status")).lower()
    if "limit" in words or "429" in words or "overloaded" in words:
        return "usage_limit"
    return "child_error"


def _episode_over(run_dir):
    path = Path(run_dir) / "run" / "steps.jsonl"
    if not path.is_file():
        return False
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return bool(lines and json.loads(lines[-1]).get("over"))


def transcript_path(session_id):
    """Where Claude Code wrote the transcript: <config>/projects/<encoded cwd>/<session id>.jsonl, found by the id."""
    found = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / (session_id + ".jsonl")))
    return Path(found[0]) if found else None


def watch_child(process, stream_path, stall_s, max_wall_s, gate=None):
    """Copy the child's records to stream_path until it ends. -> (records, killed reason or None)."""
    state = {"last": time.monotonic(), "killed": None}
    started = state["last"]

    def watchdog():
        while process.poll() is None:
            now = time.monotonic()
            reason = "child_stalled" if now - state["last"] > stall_s else ("wall_limit" if now - started > max_wall_s else None)
            if reason:
                state["killed"] = reason
                _kill_tree(process)
                return
            time.sleep(1.0)

    threading.Thread(target=watchdog, daemon=True).start()
    records = []
    with open(stream_path, "w", encoding="utf-8", newline="\n") as out:
        for line in process.stdout:
            state["last"] = time.monotonic()
            out.write(line)
            out.flush()
            try:
                record = json.loads(line)
            except ValueError:
                continue
            records.append(record)
            if gate is not None and record.get("type") == "system" and record.get("subtype") == "init":
                problem = gate(record)
                if problem:
                    state["killed"] = problem
                    _kill_tree(process)
    process.wait()
    return records, state["killed"]


def init_gate(record):
    """The child's first record says which credential it will use. Anything but none stops it before its first request."""
    if record.get("apiKeySource") not in (None, "none"):
        return "would_use_an_api_key"
    return None


def cmd_launch(args):
    run_dir = Path(args.run_dir).resolve()
    meta = _load_meta(run_dir)
    env = child_env()
    executable = shutil.which("claude", path=env.get("PATH")) or "claude"
    command = child_command(run_dir, meta, executable)
    message = run_dir / "launch" / "first_message.txt"
    if args.dry:
        return {"dry": True, "cwd": (run_dir / WORK).as_posix(), "command": subprocess.list2cmdline(command) + ' < "{}"'.format(message), "argv": command,
                "env_names": sorted(env), "env_fixed": FIXED_ENV, "commit": meta["commit"], "from_commit_alone": meta["from_commit_alone"],
                "gates": ["claude auth status under this environment: a login that is not a key", 'first record: apiKeySource "none"'],
                "stall_s": args.stall_s, "max_wall_s": args.max_wall_s, "transcript": "<config>/projects/<encoded cwd>/{}.jsonl, copied to launch/transcript.jsonl".format(meta["session_id"])}
    if (run_dir / "launch" / "child_exit.json").exists():
        raise LaunchError("this run directory has been launched already: no retries")
    meta["auth"] = auth_gate(executable, env)
    _write_json(run_dir / "launch" / "run_meta.json", meta)
    started = time.monotonic()
    with open(message, "rb") as stdin, open(run_dir / "launch" / "child_stderr.txt", "wb") as stderr:
        process = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr, env=env, cwd=str(run_dir / WORK), text=True, encoding="utf-8", errors="replace")
        records, killed = watch_child(process, run_dir / "launch" / "child_stream.jsonl", args.stall_s, args.max_wall_s, gate=init_gate)
    result = next((r for r in reversed(records) if r.get("type") == "result"), None)
    kept = {key: result.get(key) for key in ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "api_error_status", "usage", "modelUsage", "result") if key in result} if result else None
    info = {"exit_code": process.returncode, "wall_s": round(time.monotonic() - started, 1), "killed": killed, "result": kept, "utc": _now()}
    over = _episode_over(run_dir)
    info.update(episode_over=over, stop_reason=stop_reason_of(info, over))
    found = transcript_path(meta["session_id"])
    if found is not None:
        shutil.copyfile(str(found), str(run_dir / "launch" / "transcript.jsonl"))
    info["transcript_found"] = found is not None
    _write_json(run_dir / "launch" / "child_exit.json", info)
    return info


def cmd_audit(args):
    """A4's fresh reprint from the frozen tree, then the frozen audit over the child's transcript."""
    run_dir = Path(args.run_dir).resolve()
    meta = _load_meta(run_dir)
    transcript = Path(args.transcript).resolve() if args.transcript else run_dir / "launch" / "transcript.jsonl"
    if not transcript.is_file():
        raise LaunchError("no transcript in {}".format(transcript))
    reprint = run_dir / "launch" / "reprint.json"
    done = run_tool(run_dir, "reprint", "--session", str(run_dir / "run"), "--out", str(reprint))
    if done.returncode != 0:
        raise LaunchError("the reprint failed: {}".format(done.stderr[-1000:]))
    command = [sys.executable, str(run_dir / "sim" / "tools" / "audit_robot_transcript.py"), str(transcript), "--session", str(run_dir / "run"), "--reprint", str(reprint),
               "--expect-model", meta["model"], "--out", str(run_dir / "launch" / "audit.json")]
    if not args.transcript:                 # the fallback's subagent reads the launch text, not this file
        command += ["--first-message", str(run_dir / "launch" / "first_message.txt")]
    if args.finalized:
        command += ["--finalized", str(Path(args.finalized).resolve())]
    audited = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=dict(child_env(), PYTHONIOENCODING="utf-8"))
    if audited.returncode == 2 or not audited.stdout.strip():
        raise LaunchError("the audit could not read its inputs: {}".format(audited.stderr[-1000:]))
    return json.loads(audited.stdout), audited.returncode


def cmd_finalize(args):
    run_dir = Path(args.run_dir).resolve()
    meta = _load_meta(run_dir)
    path = run_dir / "launch" / "child_exit.json"
    info = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    label = args.agent_label or "Claude ({}) through Claude Code, headless".format(meta["model"])
    finalize = ["finalize", "--session", str(run_dir / "run"), "--out", str(Path(args.out).resolve()), "--agent-label", label, "--run-id", "robot_" + run_dir.name]
    if info.get("stop_reason"):
        finalize += ["--stop-reason", info["stop_reason"]]
    done = run_tool(run_dir, *finalize)
    if done.returncode not in (0, 3) or not done.stdout.strip():
        raise LaunchError("finalize failed: {}".format(done.stderr[-1000:]))
    return json.loads(done.stdout), done.returncode


def _parser():
    parser = argparse.ArgumentParser(prog="robot_launch", description="Prepare, launch, audit and finalize a headless Claude run of the robot.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--commit", required=True)
    prepare.add_argument("--spec", required=True)
    prepare.add_argument("--catalog-id")
    prepare.add_argument("--seed", required=True, type=int)
    prepare.add_argument("--nonce")
    prepare.add_argument("--root")
    prepare.add_argument("--name")
    prepare.add_argument("--overlay", nargs="*", help="working-tree files copied over the frozen tree: a throwaway check only")
    prepare.add_argument("--first-message-suffix", help="text after the first message: a throwaway check only")
    prepare.add_argument("--agent-tool-form", action="store_true", help="the fallback: the session takes the long step form a subagent can call from anywhere")
    launch = sub.add_parser("launch")
    launch.add_argument("--run-dir", required=True)
    launch.add_argument("--dry", action="store_true")
    launch.add_argument("--stall-s", type=float, default=STALL_S)
    launch.add_argument("--max-wall-s", type=float, default=MAX_WALL_S)
    audit = sub.add_parser("audit")
    audit.add_argument("--run-dir", required=True)
    audit.add_argument("--finalized")
    audit.add_argument("--transcript", help="the fallback: a subagent's transcript, instead of launch/transcript.jsonl")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--run-dir", required=True)
    finalize.add_argument("--out", required=True)
    finalize.add_argument("--agent-label")
    return parser


def main(argv=None, emit=None):
    emit = emit or print
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            emit(json.dumps(cmd_prepare(args), ensure_ascii=False, indent=1))
            return 0
        if args.command == "launch":
            emit(json.dumps(cmd_launch(args), ensure_ascii=False, indent=1))
            return 0
        value, status = cmd_audit(args) if args.command == "audit" else cmd_finalize(args)
        emit(json.dumps(value, ensure_ascii=False, indent=1))
        return status
    except LaunchError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())

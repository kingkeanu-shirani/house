"""Check that a Claude agent controlled the robot through tools/robot_step.py and nothing else: the consistency audit of DESIGN 3.7.

usage: python tools/audit_robot_transcript.py TRANSCRIPT.jsonl --session DIR [--reprint FILE] [--finalized OUT] [--first-message FILE]
                                              [--expect-model ID] [--out FILE]

TRANSCRIPT is either Claude Code form: a headless child's <config>/projects/<cwd>/<session id>.jsonl or a subagent's
<session>/subagents/agent-*.jsonl. This shows consistency among files, not attestation: a hand-written transcript with matching
commands would pass. Any violation of these makes the verdict FAIL (a void run):

A1 Only the command. Every tool call is a Bash call whose command is exactly the session's form, read the way bash reads it: `python
   robot.py step --why "..." --goal "..." [--plan "..."] --json '...'` (a session with --reach: `python ~/path/robot.py step ...`, the one path, unquoted;
   or the long form with the script and --session), one simple command, with no other tool, no other shell command, no `run_in_background` and nothing
   the shell would act on: `;`, `&&`, `||`, `|`, `&`, backticks, `$(` or `$NAME`, redirection, a line break or a comment. A `;` inside quotes is plain
   text; a `$(` inside double quotes is not.
A2 Only known blocks. Message blocks are text, thinking, tool_use, tool_result. Attachment kinds that reach the model are on a list. A hook
   record that reaches the model voids the run; one that does not is counted and warned about.
A3 Same commands. Transcript, steps.jsonl, and with --finalized the log's commands and the replay's actions, in order. Commands the operator's
   idle added are marked `_idle` and come only after the robot's last command.
A4 Same output. With --reprint (robot_step.py reprint: one fresh simulation of the stored commands), every tool output and every stored print
   equals the reprint.
A5 Clean priming. The first message and what else reaches the model hold no catalog id, nonce, spec hash, seed tag, label or event name, and no
   project word. In the headless form also no e-mail address, memory index, git status or instructions file, and the system prompt is the session's
   briefing byte for byte, with only a short identity line of Claude Code's own before it.
A6 One model. Every assistant message names the same model, published here.
A9 Chain. The hash chain verifies from h_0, on the audit's own arithmetic, and chain.jsonl holds each step's line.
A10 Timing. Each tool call precedes its output. Wall time per step, split into model and tool time, is published here.

A7 (compaction) is counted and published, never a void. A8 (the attempt ledger) comes with the seed runs.

Allowed and counted, because neither can tell the robot anything the briefing does not: `brief` before the first step (a parent cannot paste
110,000 characters), `more` (the rest of a long print, compared with what was stored), and for the subagent form a single SubagentHandback as the
very last tool call. A call in the right form that the tool refused and did not store ("Malformed step call", "could not be processed", a process that died and printed only its exit code, one line that bash could not parse for an unbalanced quote, or a call
after the episode ended) changes nothing and prints nothing else: it is counted and warned about, not void. So is a call that names the session's script
in the briefing's short form, or with the home path quoted, and that the shell could not start ("can't open file"): nothing ran and nothing was stored.

Warnings do not change the verdict: a tool output that is not what the session printed, with no --reprint to settle it; several calls in one turn;
messages injected after the first. The ruling on several step calls in one turn, written before any run it could apply to: recorded, served one at a time
in order, not void, and published. `parallel` lists the steps sent before the previous output existed, the only ones whose reason cannot rest on the print
before them; `prior_print_sha256` in chain.jsonl only names which print came before. Calls of one turn that reached the session's lock in another order
are read in the order served. Uses only the standard library and imports no simulator code. Exit status: 0 PASS, 1 FAIL, 2 the inputs cannot be read.
"""
import argparse
import base64
import datetime
import hashlib
import json
import re
import sys
import zlib
from pathlib import Path

HANDBACK_TOOL = "SubagentHandback"
HANDBACK_REMINDER = "<system-reminder>\nYour final report is delivered through SubagentHandback"   # Claude Code's own, on every subagent
BASH_KEYS = {"command", "description", "timeout"}
BARE_WORD = re.compile(r"[A-Za-z0-9_./:+~-]")   # ~ names the home folder, and a head must still equal the session's form word for word
MESSAGE_BLOCKS = ("text", "thinking", "redacted_thinking", "tool_use", "tool_result")
PARALLEL_RULING = "recorded, served one at a time in order, not void, and published"
NO_SCRIPT_TEXT = "can't open file"      # Python's own words when the shell's working directory does not hold the script
# seen in a real headless transcript: what Claude Code itself adds, with no user content in it
HEADLESS_ATTACHMENTS = ("environment", "model", "total_tokens_reminder", "date", "session_context", "remote_session_change", "prompt_snapshot", "credential_org", "silent_turn_reminder")
SILENT_TURN_TEXT = "The user hasn't heard from you in a while. As you continue, keep them updated when there's something to tell \u2014 a finding, a change of plan."
CARRIED_BY_THE_LOGIN = ("e-mail address", "user context")      # Claude Code tells any logged-in session whose e-mail it is; the subagent form carries more and is allowed with a warning
PERMISSION_DENIED = "Permission for this tool use was denied."     # Claude Code's own check stopped the call: it never ran
# seen only in a subagent's transcript: this is the priming the headless form exists to remove
SUBAGENT_ATTACHMENTS = ("instructions", "skill_listing", "deferred_tools_delta", "agent_listing_delta", "mcp_instructions_delta", "credential_org")
REFUSED_OUTPUTS = ("Malformed step call:", "The command could not be processed.", "There is no such session.")
DIED_OUTPUT = re.compile(r"Exit code \d+\Z")
UNPARSED_OUTPUT = re.compile(r"Exit code 2\s+\S*bash: .*: unexpected EOF while looking for matching `.'\Z")
OVER_TEXT = "The episode is over."
NOTHING_MORE_TEXT = "Nothing more to print."
PROJECT_WORDS = ("flagship", "home reset", "home_reset", "ten-minute", "noumenax")
PERSONAL = {"e-mail address": re.compile(r"[A-Za-z0-9._+-]+@(?!anthropic\.com)[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"), "git status": re.compile(r"Current branch:|Recent commits:|gitStatus"),
            "memory index": re.compile(r"MEMORY\.md|auto-memory"), "user context": re.compile(r"userEmail|claudeMd")}
IDENTITY_CHARS = 300                    # the identity line and billing header Claude Code puts before a replaced system prompt


def shell_words(command):
    """(words, problems) of one bash command. A word is (text, quoted): quoted is False when any character was outside
    quotes. Only what bash would act on is a problem: inside single quotes nothing is, inside double quotes `$` before a
    name or `(` and backticks are."""
    words, problems = [], []
    current, bare = None, False
    i, n = 0, len(command)

    def flush():
        nonlocal current, bare
        if current is not None:
            words.append((current, not bare))
        current, bare = None, False

    while i < n:
        c = command[i]
        if c in " \t":
            flush()
            i += 1
        elif c == "\n":
            problems.append("line break outside quotes")
            flush()
            i += 1
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                problems.append("unterminated single quote")
                current = (current or "") + command[i + 1:]
                break
            current = (current or "") + command[i + 1:end]
            i = end + 1
        elif c == '"':
            i += 1
            piece = []
            closed = False
            while i < n:
                d = command[i]
                if d == '"':
                    closed = True
                    i += 1
                    break
                if d == "\\" and i + 1 < n:
                    e = command[i + 1]
                    if e in '$`"\\':
                        piece.append(e)
                    elif e != "\n":
                        piece.append(d + e)
                    i += 2
                    continue
                if d == "`":
                    problems.append("backtick inside double quotes")
                if d == "$" and i + 1 < n and not command[i + 1].isspace() and command[i + 1] != '"':
                    problems.append("$ expansion inside double quotes")
                piece.append(d)
                i += 1
            if not closed:
                problems.append("unterminated double quote")
            current = (current or "") + "".join(piece)
        elif c == "\\":
            if i + 1 >= n or command[i + 1] == "\n":
                problems.append("line continuation")
                i += 2
                continue
            current = (current or "") + command[i + 1]
            i += 2
        else:
            if not BARE_WORD.fullmatch(c):
                problems.append("shell metacharacter {!r} outside quotes".format(c))
            current = (current or "") + c
            bare = True
            i += 1
    flush()
    return words, problems


def form_head(form, verb):
    """The words every call of this session starts with: what the robot types."""
    head = [form["python"], form.get("reach") or form["script"], verb]
    if form.get("session"):
        head += ["--session", form["session"]]
    return head


def head_matches(words, form, verb, exact=False):
    """Do the words start with the session's form (exact: are they just that)? A home path in quotes is a path the shell does not expand."""
    head = form_head(form, verb)
    texts = [w[0] for w in words]
    if texts[:len(head)] != head or (exact and len(texts) != len(head)):
        return False
    return not (str(head[1]).startswith("~") and words[1][1])


def failed_to_start(command, form, output):
    """A call of this session's script that the shell could not start: the briefing's short form typed from another folder, or a home path in quotes.
    Nothing ran and the session stored nothing."""
    if NO_SCRIPT_TEXT not in str(output):
        return False
    words, problems = shell_words(str(command).strip())
    return not problems and len(words) > 2 and words[0][0] == form["python"] and words[1][0] in (form["script"], form.get("reach"))


def parse_step_call(command, form, chained=True):
    """{why, goal, plan, json, problems, head_ok, shell_ok} of a Bash command that should be the step form. form: session.json's step_form.
    chained: a session with the hash chain needs --goal and takes --plan, in any order; an older one is exactly --why then --json."""
    words, problems = shell_words(str(command).strip())
    out = {"why": None, "goal": None, "plan": None, "json": None, "problems": problems, "shell_ok": not problems, "head_ok": False}
    head = form_head(form, "step")
    if not head_matches(words, form, "step"):
        problems.append("not exactly the step form")
        return out
    out["head_ok"] = True
    rest = words[len(head):]
    names = {"--why": "why", "--goal": "goal", "--plan": "plan", "--json": "json"} if chained else {"--why": "why", "--json": "json"}
    if len(rest) % 2 or (not chained and [w[0] for w in rest[::2]] != ["--why", "--json"]):
        problems.append("not exactly the step form")
        return out
    for name, value in zip(rest[::2], rest[1::2]):
        key = names.get(name[0])
        if key is None or out[key] is not None:
            problems.append("not exactly the step form")
            return out
        if not value[1]:
            problems.append("--why, --goal, --plan and --json must be quoted")
        out[key] = value[0]
    needed = ("why", "goal", "json") if chained else ("why", "json")
    if any(out[key] is None for key in needed):
        problems.append("not exactly the step form")
    elif not out["why"].strip():
        problems.append("empty --why")
    elif chained and not out["goal"].strip():
        problems.append("empty --goal")
    return out


def parse_more_call(command, form):
    words, problems = shell_words(str(command).strip())
    return not problems and head_matches(words, form, "more", exact=True)


def parse_brief_call(command, form):
    """The page number of a Bash command that is exactly the brief form, else None."""
    words, problems = shell_words(str(command).strip())
    texts = [w[0] for w in words]
    head = form_head(form, "brief") + ["--part"]
    if problems or len(words) != len(head) + 1 or texts[:len(head)] != head or not head_matches(words, form, "brief"):
        return None
    return int(texts[-1]) if texts[-1].isdigit() else None


def in_served_order(step_calls, steps):
    """The step calls with those of one turn in the order the session served them, when they are the same calls: a parallel call can reach the
    session's lock before the one written ahead of it. -> (calls, turns that were reordered)."""
    out, reordered, start = list(step_calls), 0, 0

    def key(why, goal, plan, raw):
        return json.dumps([why, goal, plan or None, raw])

    while start < len(out):
        end, turn = start + 1, out[start]["call"]["turn"]
        while turn is not None and end < len(out) and out[end]["call"]["turn"] == turn:
            end += 1
        if end - start > 1 and end <= len(steps):
            stored = [key(s.get("why"), s.get("goal"), s.get("plan"), s.get("json")) for s in steps[start:end]]
            made = [key(m["why"], m["goal"], m["plan"], m["json"]) for m in out[start:end]]
            if made != stored and sorted(made) == sorted(stored):
                remaining = list(zip(made, out[start:end]))
                ordered = []
                for wanted in stored:
                    found = next(item for item in remaining if item[0] == wanted)
                    remaining.remove(found)
                    ordered.append(found[1])
                out[start:end] = ordered
                reordered += 1
        start = end
    return out, reordered


def parallel_steps(step_calls, steps):
    """-> (turns with more than one step call, the numbers of the steps sent before the previous output existed: all but the first of each turn)."""
    turns, later, previous, counted = 0, [], None, None
    for index, made in enumerate(step_calls):
        turn = made["call"]["turn"]
        if turn is not None and turn == previous:
            later.append(steps[index].get("n") if index < len(steps) else index + 1)
            if counted != turn:
                turns, counted = turns + 1, turn
        previous = turn
    return turns, later


def read_transcript(path):
    """(records, unreadable line numbers) from a JSONL transcript."""
    records, bad = [], []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                bad.append(number)
                continue
            if isinstance(value, dict):
                records.append((number, value))
            else:
                bad.append(number)
    return records, bad


def _blocks(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return message, content


def _block_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)
    return ""


def collect(records):
    """(tool calls in order, tool results by id, user texts in order, assistant turn of each call)."""
    calls, seen, results, user_texts = [], {}, {}, []
    extra = []
    for number, record in records:
        kind = record.get("type")
        message, content = _blocks(record)
        if kind == "user" and isinstance(content, str):
            user_texts.append((number, content))
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = str(block.get("type", ""))
            if btype.endswith("tool_use"):
                entry = {"id": block.get("id"), "name": block.get("name"), "input": block.get("input") or {}, "line": number, "timestamp": record.get("timestamp"),
                         "turn": record.get("requestId") or (message.get("id") if isinstance(message, dict) else None)}
                if kind == "assistant":
                    if entry["id"] not in seen:
                        seen[entry["id"]] = entry
                        calls.append(entry)
                else:
                    extra.append(entry)         # e.g. api-request-blob lines repeat the conversation so far
            elif btype == "tool_result" and kind == "user":
                results[block.get("tool_use_id")] = {"text": _block_text(block), "is_error": bool(block.get("is_error")), "timestamp": record.get("timestamp")}
            elif btype == "text" and kind == "user":
                user_texts.append((number, str(block.get("text", ""))))
    for entry in extra:
        if entry["id"] not in seen:             # a call that only a copy mentions is still a call
            seen[entry["id"]] = entry
            calls.append(entry)
    return calls, results, user_texts


def load_session(folder):
    folder = Path(folder)
    session = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    steps = []
    path = folder / "steps.jsonl"
    if path.is_file():
        steps = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    texts = {}
    for name in ("briefing.txt", "start.txt"):
        if (folder / name).is_file():
            texts[name] = (folder / name).read_text(encoding="utf-8").strip()
    pages = []
    if (folder / "briefing_pages.json").is_file():
        pages = json.loads((folder / "briefing_pages.json").read_text(encoding="utf-8"))
    return session, steps, texts, pages


def sha256_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def chain_hash(previous, printed, raw, why, goal, plan):
    """The audit's own copy of robot_step.chain_hash: a test holds the two to the same answer."""
    return sha256_text(json.dumps([previous, sha256_text(printed), raw, why, goal, plan or None], ensure_ascii=False, separators=(",", ":")))


def chain_genesis(session):
    versions = session.get("versions") or {}
    return sha256_text(json.dumps([session.get(key) for key in ("format", "spec_hash", "seed_tag", "episode_nonce", "initial_digest", "system_prompt_sha256", "prompt_sha256")]
                                  + [versions.get("code_fingerprint"), versions.get("harness_fingerprint")], ensure_ascii=False, separators=(",", ":")))


def check_chain(session, steps, session_dir):
    """-> (problems, head, numbers written to chain.jsonl with no step behind them)."""
    problems = []
    head, printed = session.get("chain_genesis"), (Path(session_dir) / "start.txt").read_text(encoding="utf-8").rstrip("\n")
    if head != chain_genesis(session):
        problems.append("h_0 does not match the session")
    path = Path(session_dir) / "chain.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
    by_h = {line.get("h"): line for line in lines}
    for step in steps:
        expected = chain_hash(head, printed, step.get("json"), step.get("why"), step.get("goal"), step.get("plan"))
        if step.get("h") != expected:
            problems.append("step {} does not verify".format(step.get("n")))
        line = by_h.get(step.get("h"))
        if line is None:
            problems.append("step {} has no line in chain.jsonl".format(step.get("n")))
        elif (line.get("prior_print_sha256") or line.get("seen_sha256")) != sha256_text(printed) or line.get("prev") != head:     # seen_sha256: the field's name in the pilot
            problems.append("step {}: chain.jsonl commits to something else".format(step.get("n")))
        head, printed = expected, step.get("output")
    done = {step.get("h") for step in steps}
    return problems, head, [line.get("n") for line in lines if line.get("h") not in done]


def _parse_time(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def timing(calls, results):
    """Wall seconds per tool call and per model turn, from the transcript's timestamps. A10's data."""
    tool, model, last_result, bad = [], [], None, []
    for call in calls:
        used, result = _parse_time(call.get("timestamp")), results.get(call["id"])
        done = _parse_time(result.get("timestamp")) if result else None
        if used is not None and last_result is not None:
            model.append(round((used - last_result).total_seconds(), 3))
        if used is not None and done is not None:
            seconds = (done - used).total_seconds()
            tool.append(round(seconds, 3))
            if seconds < 0:
                bad.append(call["id"])
        if done is not None:
            last_result = done

    def stats(values):
        return {"n": len(values), "total_s": round(sum(values), 1), "mean_s": round(sum(values) / len(values), 2), "min_s": min(values), "max_s": max(values)} if values else {"n": 0}

    return {"tool": stats(tool), "model": stats(model), "tool_s": tool, "model_s": model}, bad


def priming(records):
    """What reached the model besides the first message and the tool outputs, from the records that say so."""
    kinds, texts, snapshot, system_blocks, hooks = {}, [], None, None, {"rendered": 0, "silent": 0}
    for _number, record in records:
        kind = record.get("type")
        if kind == "attachment":
            attachment = record.get("attachment") or {}
            name = str(attachment.get("type"))
            rendered = record.get("rendered")
            kinds[name] = kinds.get(name, 0) + 1
            if name.startswith("hook"):
                hooks["rendered" if rendered else "silent"] += 1
            if rendered:
                texts.append((name, json.dumps(rendered, ensure_ascii=False)))
            if name == "prompt_snapshot" and snapshot is None:
                snapshot = attachment.get("systemPrompt")
            if name in ("session_context", "instructions", "credential_org"):
                texts.append((name, json.dumps(attachment, ensure_ascii=False)))
        elif kind == "api-request-shape":
            shape = record.get("shape") or {}
            system_blocks = shape.get("system") if system_blocks is None else system_blocks
        elif kind == "api-request-blob":
            message = record.get("message") or {}
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(("request", str(block.get("text", ""))))
    return {"kinds": kinds, "texts": texts, "snapshot": snapshot, "system_blocks": system_blocks, "hooks": hooks}


def spec_tokens(session_dir):
    """Names that the spec holds and the robot must not be told: labels and event effects."""
    path = Path(session_dir) / "spec.json"
    if not path.is_file():
        return []
    spec = json.loads(path.read_text(encoding="utf-8"))
    names = set()
    for key in ("success", "failure", "failures", "objectives"):
        for item in spec.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("label"), str):
                names.add(item["label"])
    for op in spec.get("setup") or []:
        if isinstance(op, dict) and op.get("op") in ("event", "helper"):
            names.update(op[key] for key in ("effect", "helper", "name", "fn") if isinstance(op.get(key), str))
    return sorted(name for name in names if len(name) >= 6)


def read_replay_actions(path):
    """The actions a replay file holds, in order: base85 of xz or zlib, standard library only."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "packed" in data:
        raw = base64.b85decode(data["packed"])
        if data.get("codec") == "xz+b85":
            import lzma
            raw = lzma.decompress(raw)
        else:
            raw = zlib.decompress(raw)
        data = json.loads(raw)
    return [(step.get("command") or {}).get("action") for step in data.get("steps") or []]


def check_finalized(steps, out_dir):
    """A3's far end: the log's commands and the replay's actions. -> (problems, summary)."""
    out_dir, problems = Path(out_dir), []
    logs = sorted((out_dir / "logs").glob("*.json"))
    if len(logs) != 1:
        return ["{} logs in {}".format(len(logs), out_dir)], {}
    log = json.loads(logs[0].read_text(encoding="utf-8"))
    entries = log.get("steps") or []
    for index, step in enumerate(steps):
        if index >= len(entries):
            problems.append("step {} is not in the log".format(step.get("n")))
        elif entries[index].get("command") != step.get("command"):
            problems.append("step {}: the log's command differs from the stored one".format(step.get("n")))
    idle = entries[len(steps):]
    robot_stopped = log.get("end_reason") == "robot_stopped"
    for entry in idle:
        if not (entry.get("command") or {}).get("_idle"):
            if not (entry.get("done") and robot_stopped):
                problems.append("log step {} is not the robot's and is not marked _idle".format(entry.get("i")))
    if idle and not robot_stopped:
        problems.append("the log has commands past the robot's and its end_reason is {}".format(log.get("end_reason")))
    for step in entries[:len(steps)]:
        if (step.get("command") or {}).get("_idle"):
            problems.append("log step {} is marked _idle among the robot's commands".format(step.get("i")))
    summary = {"log_steps": len(entries), "idle_commands": len(idle), "end_reason": log.get("end_reason"), "stop_reason": log.get("stop_reason"), "replay_checked": False}
    if log.get("replay"):
        actions = read_replay_actions(out_dir / log["replay"])
        sent = [entry["sent"].get("action") for entry in entries if entry.get("sent") is not None and "result" in entry]
        summary["replay_checked"] = True
        if actions != sent:
            problems.append("the replay's actions differ from the log's ({} against {})".format(len(actions), len(sent)))
    return problems, summary


def is_compaction(record):
    return (record.get("type") == "system" and record.get("subtype") == "compact_boundary") or bool(record.get("isCompactSummary"))


def is_subagent_form(records):
    return any(record.get("isSidechain") or record.get("agentId") for _n, record in records if record.get("type") in ("user", "assistant"))


def audit(transcript, session_dir, reprint=None, finalized=None, first_message=None, expect_model=None):
    """The verdict as a dict."""
    session, steps, texts, pages = load_session(session_dir)
    form = session["step_form"]
    chained = bool(session.get("chain_genesis"))
    records, unreadable = read_transcript(transcript)
    calls, results, user_texts = collect(records)
    subagent = is_subagent_form(records)
    violations, warnings = [], []

    def violate(kind, detail, call=None):
        item = {"kind": kind, "detail": detail}
        if call is not None:
            item.update(line=call["line"], tool=call["name"], id=call["id"])
        violations.append(item)

    def warn(kind, detail):
        warnings.append({"kind": kind, "detail": detail})

    for number in unreadable:
        violate("unreadable_line", "transcript line {} is not a JSON object".format(number))
    if not records:
        violate("empty_transcript", "the transcript has no records")
    # A1
    step_calls, handbacks, brief_calls, more_calls, refused, wrong_path = [], [], [], [], [], []
    compacted_at = min((n for n, record in records if is_compaction(record)), default=None)   # the pages are fixed text: re-reading them after a reset tells nothing new
    stored = len(steps)
    for position, call in enumerate(calls):
        name, command = call["name"], call["input"].get("command")
        if name == HANDBACK_TOOL:
            handbacks.append(call)
            if position != len(calls) - 1:
                violate("handback_not_last", "{} is allowed only as the very last tool call".format(HANDBACK_TOOL), call)
            continue
        if name != "Bash":
            violate("other_tool", "tool {} is not the step command".format(name), call)
            continue
        odd_keys = sorted(set(call["input"]) - BASH_KEYS)
        if odd_keys:
            violate("bash_options", "Bash options other than the step form's: {}".format(odd_keys), call)
        if failed_to_start(command, form, (results.get(call["id"]) or {}).get("text", "")):
            wrong_path.append(call)             # nothing ran and nothing was stored; A3 counts what was stored
            continue
        part = parse_brief_call(command, form)
        if part is not None:
            if step_calls and (compacted_at is None or call["line"] < compacted_at):
                violate("brief_after_step", "a brief call after the first step, with no context reset before it", call)
            brief_calls.append({"call": call, "part": part})
            continue
        if parse_more_call(command, form):
            more_calls.append({"call": call, "after": len(step_calls)})
            continue
        parsed = parse_step_call(command, form, chained)
        output = (results.get(call["id"]) or {}).get("text", "").strip()
        if parsed["head_ok"] and output.startswith(PERMISSION_DENIED):
            refused.append(call)                # the permission check stopped it, so nothing ran; if it did store a step, A3 finds the extra command
            continue
        if parsed["head_ok"] and "\n" not in str(command).strip() and UNPARSED_OUTPUT.match(output):
            refused.append(call)                # an unbalanced quote on one line: bash parses the whole line first, so nothing ran
            continue
        if parsed["shell_ok"] and parsed["head_ok"] and (output.startswith(REFUSED_OUTPUTS) or DIED_OUTPUT.match(output)):
            refused.append(call)                # the tool said no, or its process died, and stored nothing: nothing happened and nothing was shown
            continue
        if not parsed["problems"] and len(step_calls) >= stored and output == OVER_TEXT:
            refused.append(call)                # a call after the episode ended
            continue
        for problem in parsed["problems"]:
            violate("not_step_form", "{}: {}".format(problem, str(command)[:300]), call)
        if not parsed["problems"] and parsed["why"] is not None:
            step_calls.append({"call": call, "why": parsed["why"], "goal": parsed["goal"], "plan": parsed["plan"], "json": parsed["json"]})
    if refused:
        warn("refused_calls", "{} call(s) in the right form that the tool refused or that came after the end; nothing stored".format(len(refused)))
    if wrong_path:
        warn("wrong_path_calls", "{} call(s) of the script that the shell could not start from its working directory; nothing ran".format(len(wrong_path)))
    step_calls, reordered_turns = in_served_order(step_calls, steps)
    if reordered_turns:
        warn("served_out_of_order", "{} turn(s) whose step calls reached the session in another order than they were written".format(reordered_turns))
    # A2
    for number, record in records:
        if record.get("type") in ("user", "assistant"):
            _message, content = _blocks(record)
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") not in MESSAGE_BLOCKS:
                    violate("unknown_block", "line {}: block type {}".format(number, block.get("type")))
    prime = priming(records)
    known = HEADLESS_ATTACHMENTS + (SUBAGENT_ATTACHMENTS if subagent else ())
    for name, count in sorted(prime["kinds"].items()):
        if name.startswith("hook") or name in known:
            continue
        if any(text_name == name for text_name, _text in prime["texts"]):
            violate("unknown_attachment", "{} attachment(s) of kind {} reach the model".format(count, name))
        else:
            warn("unknown_attachment", "{} attachment(s) of kind {}, none reaching the model".format(count, name))
    if not subagent:
        for name in SUBAGENT_ATTACHMENTS:
            if name in prime["kinds"] and name not in HEADLESS_ATTACHMENTS:
                violate("priming_attachment", "a {} attachment in the headless form".format(name))
        for name, text in prime["texts"]:
            if name == "silent_turn_reminder" and (SILENT_TURN_TEXT not in text or len(text) - len(SILENT_TURN_TEXT) > 200):    # the sentence and its wrapper, nothing else
                violate("priming_attachment", "a silent_turn_reminder with other words")
        if prime["kinds"].get("silent_turn_reminder"):
            warn("silent_turn_reminder", "{} generic reminder(s) from Claude Code to keep the user updated reached the model".format(prime["kinds"]["silent_turn_reminder"]))
    if prime["hooks"]["rendered"]:
        violate("hook_reached_model", "{} hook record(s) reach the model".format(prime["hooks"]["rendered"]))
    if prime["hooks"]["silent"]:
        warn("hook_records", "{} hook record(s), none reaching the model".format(prime["hooks"]["silent"]))
    # A3: one to one, in order, as the raw text the robot wrote
    for index in range(max(len(step_calls), len(steps))):
        if index >= len(step_calls):
            violate("dropped_step", "stored step {} ({!r}) has no call in the transcript".format(steps[index].get("n"), steps[index].get("json")))
            continue
        made = step_calls[index]
        if index >= len(steps):
            violate("extra_step", "a step call has no stored command: {!r}".format(made["json"]), made["call"])
            continue
        step = steps[index]
        if made["why"] != step.get("why"):
            violate("reason_mismatch", "step {}: the transcript says {!r}, the session {!r}".format(step.get("n"), made["why"], step.get("why")), made["call"])
        if chained and (made["goal"] != step.get("goal") or (made["plan"] or None) != step.get("plan")):
            violate("reason_mismatch", "step {}: the transcript's goal or plan is not the session's".format(step.get("n")), made["call"])
        if made["json"] != step.get("json"):
            violate("command_mismatch", "step {}: the transcript says {!r}, the session {!r}".format(step.get("n"), made["json"], step.get("json")), made["call"])
        if isinstance(step.get("command"), dict) and step["command"].get("_why") != step.get("why"):
            violate("stored_annotation", "step {}: the stored command's _why is not the stored reason".format(step.get("n")))
        made["stored"] = step
    if not steps and not step_calls:
        violate("no_steps", "no step was made")
    if len(step_calls) != len(steps):
        violate("count_mismatch", "{} step calls in the transcript, {} stored commands".format(len(step_calls), len(steps)))
    finalized_summary = None
    if finalized:
        problems, finalized_summary = check_finalized(steps, finalized)
        for problem in problems:
            violate("log_mismatch", problem)
    # what reached the robot: A4 and the output comparison
    secrets = {name: str(session.get(key)) for name, key in (("nonce", "episode_nonce"), ("catalog id", "catalog_id"), ("spec hash", "spec_hash")) if session.get(key)}
    if chained:
        secrets["seed tag"] = str(session.get("seed_tag"))
    first_user = user_texts[0][1] if user_texts else ""
    robot_read = [("the first message", first_user)] + [("tool output of {}".format(call["id"]), results[call["id"]]["text"])
                                                         for call in calls if call["id"] in results]
    for where, text in robot_read:
        for name, value in secrets.items():
            if value in text:
                violate("leak", "the {} appears in {}".format(name, where))
    matched = 0
    pages_seen = {}
    for made in step_calls:
        step, result = made.get("stored"), results.get(made["call"]["id"])
        if step is None:
            continue
        expected = (step.get("pages") or [step.get("output", "")])[0]
        if result is None:
            warn("no_output", "no tool output recorded for step {}".format(step.get("n")))
        elif result["text"].strip() == str(expected).strip():
            matched += 1
        else:
            warn("output_differs", "step {}: the robot's tool output is not what the session printed ({} vs {} chars)".format(step.get("n"), len(result["text"]), len(str(expected))))
    for made in more_calls:
        result = results.get(made["call"]["id"])
        step = steps[made["after"] - 1] if 0 < made["after"] <= len(steps) else {}
        index = pages_seen.get(made["after"], 1)
        pages_seen[made["after"]] = index + 1
        held = step.get("pages") or []
        expected = held[index] if index < len(held) else NOTHING_MORE_TEXT
        if result is None or result["text"].strip() != str(expected).strip():
            warn("output_differs", "a more call after step {}: the output is not the stored page".format(made["after"]))
    reprint_summary = None
    if reprint:
        data = json.loads(Path(reprint).read_text(encoding="utf-8"))
        prints = data.get("prints") or []
        reprint_summary = {"prints": len(prints), "compared": 0}
        if prints and "start.txt" in texts and prints[0].strip() != texts["start.txt"].strip():
            violate("reprint_mismatch", "the first print differs from the reprint")
        if chained and session.get("prompt_sha256") and data.get("prompt_sha256") != session.get("prompt_sha256"):
            violate("reprint_mismatch", "the briefing differs from the reprint's")
        if data.get("code_fingerprint") != (session.get("versions") or {}).get("code_fingerprint"):
            violate("reprint_mismatch", "the reprint ran other simulation code")
        for index, step in enumerate(steps):
            if step.get("over"):
                continue
            again = prints[index + 1] if index + 1 < len(prints) else None
            if again is None or again != step.get("output"):
                violate("reprint_mismatch", "step {}: the stored print is not the reprint".format(step.get("n")))
                continue
            reprint_summary["compared"] += 1
            if index < len(step_calls):
                result = results.get(step_calls[index]["call"]["id"])
                shown = (step.get("pages") or [again])[0]
                if result is not None and result["text"].strip() != str(shown).strip():
                    violate("reprint_mismatch", "step {}: the robot's tool output is not the reprint".format(step.get("n")), step_calls[index]["call"])
    pages_read = set()
    for made in brief_calls:
        result = results.get(made["call"]["id"])
        if 1 <= made["part"] <= len(pages) and result is not None and result["text"].strip() == pages[made["part"] - 1].strip():
            pages_read.add(made["part"])
        else:
            warn("output_differs", "brief --part {}: the robot's tool output is not the stored page".format(made["part"]))
    parallel, sent_early = parallel_steps(step_calls, steps)
    if parallel:
        warn("parallel_calls", "{} turn(s) sent more than one step call; step(s) {} were sent before the previous output existed: {}".format(parallel, sent_early, PARALLEL_RULING))
    for number, text in user_texts[1:]:
        if text.startswith(HANDBACK_REMINDER):
            continue
        warn("injected_message", "transcript line {}: a message after the first: {!r}".format(number, text[:200]))
    # A5
    briefing = texts.get("briefing.txt")
    snapshot = prime["snapshot"]
    snapshot_text = "\n".join(str(item) for item in snapshot) if isinstance(snapshot, list) else (str(snapshot) if snapshot else None)
    system_verbatim = briefing is not None and snapshot_text is not None and snapshot_text.strip() == briefing.strip()
    extra_system = None
    if isinstance(prime["system_blocks"], list):
        extra_system = sum(len(str(block.get("text", ""))) for block in prime["system_blocks"] if isinstance(block, dict) and str(block.get("text", "")).strip() != (briefing or "").strip())
    priming_text = [(name, text) for name, text in prime["texts"] if text.strip() != first_user.strip()]
    tokens = list(PROJECT_WORDS) + spec_tokens(session_dir)
    for where, text in [("the first message", first_user)] + priming_text:
        lowered = text.lower()
        for token in tokens:
            if token.lower() in lowered:
                violate("priming_leak", "{!r} appears in {}".format(token, where))
    personal_found = {}
    for where, text in priming_text:
        if where == "request" and text.strip() == first_user.strip():
            continue
        for name, pattern in PERSONAL.items():
            if pattern.search(text):
                personal_found.setdefault(name, []).append(where)
    if not subagent:
        for name, where in sorted(personal_found.items()):
            if name in CARRIED_BY_THE_LOGIN and set(where) <= {"session_context", "request"}:
                continue
            violate("priming_personal", "{} in {}".format(name, sorted(set(where))))
        if any(name in personal_found for name in CARRIED_BY_THE_LOGIN):
            warn("headless_priming", "Claude Code's session context carries the logged-in owner's e-mail; no memory index and no git status")
        if not system_verbatim:
            violate("system_prompt", "the system prompt recorded in the transcript is not the session's briefing")
        if extra_system is not None and extra_system > IDENTITY_CHARS:
            violate("system_prompt", "{} characters of system prompt besides the briefing".format(extra_system))
    elif personal_found:
        warn("subagent_priming", "the subagent form carries {}: the claim of a clean start weakens".format(sorted(personal_found)))
    first_exact = None
    if first_message:
        expected = Path(first_message).read_text(encoding="utf-8").strip()
        first_exact = first_user.strip() == expected
        if not first_exact:
            violate("first_message", "the transcript's first message is not the launch's")
    # a workflow's harness indents a computed task's lines: compare without that
    plain_first = "\n".join(line.lstrip(" ") for line in first_user.split("\n"))
    first = {name: (text in first_user or "\n".join(line.lstrip(" ") for line in text.split("\n")) in plain_first) for name, text in texts.items()}
    by_pages = bool(pages) and pages_read == set(range(1, len(pages) + 1))
    for name, present in first.items():
        if name == "briefing.txt" and system_verbatim:
            continue
        if not present and not by_pages:
            warn("first_message", "{} is not verbatim in the robot's first message, nor read in pages".format(name))
    if "briefing.txt" not in texts:
        warn("first_message", "the session has no briefing.txt: `prompt` was not run")
    extra_chars = len(first_user) - sum(len(text) for name, text in texts.items() if first.get(name))
    if len(handbacks) > 1:
        violate("handback_repeated", "{} {} calls".format(len(handbacks), HANDBACK_TOOL))
    # A6
    models = sorted({str((record.get("message") or {}).get("model")) for _n, record in records if record.get("type") == "assistant" and (record.get("message") or {}).get("model")})
    synthetic = [model for model in models if model.startswith("<")]
    models = [model for model in models if not model.startswith("<")]
    if len(models) > 1:
        violate("model_changed", "more than one model: {}".format(models))
    if expect_model and models and models != [expect_model]:
        violate("model_changed", "the transcript's model is {}, the run's {}".format(models, expect_model))
    if synthetic:
        warn("synthetic_message", "messages Claude Code wrote itself, not a model: {}".format(synthetic))
    # A7
    compactions = sum(1 for _n, record in records if is_compaction(record))
    # A9
    chain_summary = None
    if chained:
        problems, head, orphans = check_chain(session, steps, session_dir)
        for problem in problems:
            violate("chain", problem)
        if orphans:
            warn("commands_that_never_returned", "chain.jsonl holds command(s) {} that never reached steps.jsonl".format(orphans))
        chain_summary = {"verifies": not problems, "head": head, "steps": len(steps)}
    else:
        warn("no_chain", "this session predates the hash chain")
    # A10
    times, backwards = timing([made["call"] for made in step_calls], results)
    for call_id in backwards:
        violate("timing", "tool call {}: the output is timed before the call".format(call_id))
    digest = hashlib.sha256(Path(transcript).read_bytes()).hexdigest()
    usage = [(record.get("message") or {}).get("usage") or {} for _n, record in records if record.get("type") == "assistant"]
    return {"verdict": "FAIL" if violations else "PASS", "kind": "consistency audit", "form": "subagent" if subagent else "headless",
            "counts": {"tool_calls": len(calls), "step_calls": len(step_calls), "stored_commands": len(steps), "handbacks": len(handbacks), "refused_calls": len(refused), "wrong_path_calls": len(wrong_path),
                       "more_calls": len(more_calls), "brief_calls": len(brief_calls), "brief_pages_read": len(pages_read), "brief_pages": len(pages),
                       "outputs_matching_the_session": matched, "violations": len(violations), "warnings": len(warnings)},
            "violations": violations, "warnings": warnings,
            "parallel": {"turns": parallel, "steps_sent_before_previous_output": sent_early, "ruling": PARALLEL_RULING},
            "first_message": {"verbatim": first, "chars": len(first_user), "chars_beyond_briefing_and_start": extra_chars, "equals_the_launch_file": first_exact,
                              "briefing_delivery": "system_prompt" if system_verbatim else ("pages" if by_pages else ("first_message" if first and all(first.values()) else "unproven"))},
            "priming": {"attachment_kinds": prime["kinds"], "system_prompt_is_the_briefing": system_verbatim, "system_chars_besides_the_briefing": extra_system, "personal_found": personal_found, "hook_records": prime["hooks"]},
            "models": models, "compactions": compactions, "chain": chain_summary, "reprint": reprint_summary, "finalized": finalized_summary, "timing": times,
            "output_tokens_reported": sum(int(item.get("output_tokens") or 0) for item in usage),
            "transcript": {"file": Path(transcript).name, "sha256": digest, "records": len(records)},
            "session": {"catalog_id": session.get("catalog_id"), "episode_over": bool(steps and steps[-1].get("over")), "typed_form": "{} {}".format(form["python"], form.get("reach") or form["script"]),
                        "system_prompt_sha256": session.get("system_prompt_sha256"), "prompt_sha256": session.get("prompt_sha256")}}


def main(argv=None, emit=None):
    parser = argparse.ArgumentParser(description="Audit a robot run's transcript against its robot_step session.")
    parser.add_argument("transcript")
    parser.add_argument("--session", required=True)
    parser.add_argument("--reprint", help="the file `robot_step.py reprint` wrote: check A4")
    parser.add_argument("--finalized", help="the folder `robot_step.py finalize` wrote: A3 reaches the log and the replay")
    parser.add_argument("--first-message", help="the file the child read as its first message")
    parser.add_argument("--expect-model")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    emit = emit or print
    try:
        verdict = audit(args.transcript, args.session, args.reprint, args.finalized, args.first_message, args.expect_model)
    except (OSError, ValueError, KeyError) as exc:
        print("error: cannot read the inputs: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 2
    text = json.dumps(verdict, ensure_ascii=False, indent=1)
    emit(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0 if verdict["verdict"] == "PASS" else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())

"""Prove that a Claude agent controlled the robot through tools/robot_step.py and nothing else.

usage: python tools/audit_robot_transcript.py TRANSCRIPT.jsonl --session DIR [--out FILE]

TRANSCRIPT is a Claude Code subagent transcript (<session>/subagents/agent-*.jsonl). The verdict is PASS only when:

* every tool call in the transcript is a Bash call whose command is exactly the step form the session recorded: `python
  <script> step --session <session> --why "..." --json '...'`, one simple command, with no other tool (Read, Grep, Glob,
  Write, Edit, Agent, ToolSearch, web tools, PowerShell ...), no other shell command, and nothing the shell would act on:
  `;`, `&&`, `||`, `|`, `&`, backticks, `$(` or `$NAME`, redirection, a line break or a comment. The command is read the way
  bash reads it, so a `;` inside quotes is plain text and a `$(` inside double quotes is not;
* the commands and reasons are, one to one and in order, the session's stored commands (compared as the raw text);
* the counts match, and there is at least one step;
* neither the robot's first message nor any tool output carries the session's nonce, catalog id or spec hash.

Two other kinds of call are allowed and counted, because a real run needs them and neither can tell the robot anything
that the briefing does not:

* `python <script> brief --session <session> --part N` before the first step: the pages of the same briefing (robot_step.py
  explains why a parent cannot simply paste 110,000 characters). The output is compared with the session's stored page. A
  `brief` after the first step is a violation;
* a single SubagentHandback as the very last tool call: Claude Code's background subagents must end with it, and the call
  ends the run. Anywhere else, or a second one, is a violation.

Reported as warnings, which do not change the verdict: a tool output that is not what the session printed (for instance cut
by the Bash tool's output limit), several calls in one turn, messages injected after the first (a parent steering the
robot), and whether the first message carries the briefing and the start text verbatim. The audit uses only the standard
library and imports no simulator code. Exit status: 0 PASS, 1 FAIL, 2 the inputs cannot be read.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HANDBACK_TOOL = "SubagentHandback"
HANDBACK_REMINDER = "<system-reminder>\nYour final report is delivered through SubagentHandback"   # Claude Code's own, on every subagent
BASH_KEYS = {"command", "description", "timeout"}
BARE_WORD = re.compile(r"[A-Za-z0-9_./:+-]")


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


def parse_step_call(command, form):
    """(why, raw json, problems) of a Bash command that should be the step form. form: session.json's step_form."""
    words, problems = shell_words(str(command).strip())
    head = [form["python"], form["script"], "step", "--session", form["session"], "--why"]
    texts = [w[0] for w in words]
    if len(words) != 9 or texts[:6] != head or texts[7] != "--json":
        problems.append("not exactly the step form")
        return None, None, problems
    why, raw = words[6], words[8]
    if not why[1] or not raw[1]:
        problems.append("--why and --json must be quoted")
    if not why[0].strip():
        problems.append("empty --why")
    return why[0], raw[0], problems


def parse_brief_call(command, form):
    """The page number of a Bash command that is exactly the brief form, else None."""
    words, problems = shell_words(str(command).strip())
    texts = [w[0] for w in words]
    if problems or len(words) != 7 or texts[:6] != [form["python"], form["script"], "brief", "--session", form["session"], "--part"]:
        return None
    return int(texts[6]) if texts[6].isdigit() else None


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
                entry = {"id": block.get("id"), "name": block.get("name"), "input": block.get("input") or {}, "line": number,
                         "turn": record.get("requestId") or (message.get("id") if isinstance(message, dict) else None)}
                if kind == "assistant":
                    if entry["id"] not in seen:
                        seen[entry["id"]] = entry
                        calls.append(entry)
                else:
                    extra.append(entry)         # e.g. api-request-blob lines repeat the conversation so far
            elif btype == "tool_result" and kind == "user":
                results[block.get("tool_use_id")] = {"text": _block_text(block), "is_error": bool(block.get("is_error"))}
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


def audit(transcript, session_dir):
    """The verdict as a dict."""
    session, steps, texts, pages = load_session(session_dir)
    form = session["step_form"]
    records, unreadable = read_transcript(transcript)
    calls, results, user_texts = collect(records)
    violations, warnings = [], []

    def violate(kind, detail, call=None):
        item = {"kind": kind, "detail": detail}
        if call is not None:
            item.update(line=call["line"], tool=call["name"], id=call["id"])
        violations.append(item)

    for number in unreadable:
        violate("unreadable_line", "transcript line {} is not a JSON object".format(number))
    if not records:
        violate("empty_transcript", "the transcript has no records")
    step_calls, handbacks, brief_calls = [], [], []
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
        part = parse_brief_call(command, form)
        if part is not None:
            if step_calls:
                violate("brief_after_step", "a brief call after the first step", call)
            brief_calls.append({"call": call, "part": part})
            continue
        why, raw, problems = parse_step_call(command, form)
        for problem in problems:
            violate("not_step_form", "{}: {}".format(problem, str(command)[:300]), call)
        if not problems and why is not None:
            step_calls.append({"call": call, "why": why, "json": raw})
    # one to one, in order, as the raw text the robot wrote
    for index in range(max(len(step_calls), len(steps))):
        if index >= len(step_calls):
            violate("dropped_step", "stored step {} ({!r}) has no call in the transcript".format(steps[index].get("n"), steps[index].get("json")))
            continue
        made = step_calls[index]
        if index >= len(steps):
            violate("extra_step", "a step call has no stored command: {!r}".format(made["json"]), made["call"])
            continue
        stored = steps[index]
        if made["why"] != stored.get("why"):
            violate("reason_mismatch", "step {}: the transcript says {!r}, the session {!r}".format(stored.get("n"), made["why"], stored.get("why")), made["call"])
        if made["json"] != stored.get("json"):
            violate("command_mismatch", "step {}: the transcript says {!r}, the session {!r}".format(stored.get("n"), made["json"], stored.get("json")), made["call"])
        if isinstance(stored.get("command"), dict) and stored["command"].get("_why") != stored.get("why"):
            violate("stored_annotation", "step {}: the stored command's _why is not the stored reason".format(stored.get("n")))
        made["stored"] = stored
    if not steps and not step_calls:
        violate("no_steps", "no step was made")
    if len(step_calls) != len(steps):
        violate("count_mismatch", "{} step calls in the transcript, {} stored commands".format(len(step_calls), len(steps)))
    # what reached the robot
    secrets = {name: str(session.get(key)) for name, key in (("nonce", "episode_nonce"), ("catalog id", "catalog_id"), ("spec hash", "spec_hash")) if session.get(key)}
    first_user = user_texts[0][1] if user_texts else ""
    robot_read = [("the first message", first_user)] + [("tool output of {}".format(call["id"]), results[call["id"]]["text"])
                                                         for call in calls if call["id"] in results]
    for where, text in robot_read:
        for name, value in secrets.items():
            if value in text:
                violate("leak", "the {} appears in {}".format(name, where))
    matched = 0
    for made in step_calls:
        stored, result = made.get("stored"), results.get(made["call"]["id"])
        if stored is None:
            continue
        if result is None:
            warnings.append({"kind": "no_output", "detail": "no tool output recorded for step {}".format(stored.get("n"))})
        elif result["text"].strip() == str(stored.get("output", "")).strip():
            matched += 1
        else:
            warnings.append({"kind": "output_differs", "detail": "step {}: the robot's tool output is not what the session printed ({} vs {} chars)".format(
                stored.get("n"), len(result["text"]), len(str(stored.get("output", ""))))})
    pages_read = set()
    for made in brief_calls:
        result = results.get(made["call"]["id"])
        if 1 <= made["part"] <= len(pages) and result is not None and result["text"].strip() == pages[made["part"] - 1].strip():
            pages_read.add(made["part"])
        else:
            warnings.append({"kind": "output_differs", "detail": "brief --part {}: the robot's tool output is not the stored page".format(made["part"])})
    turns = {}
    for made in step_calls:
        turns[made["call"]["turn"]] = turns.get(made["call"]["turn"], 0) + 1
    parallel = sum(1 for turn, count in turns.items() if turn is not None and count > 1)
    if parallel:
        warnings.append({"kind": "parallel_calls", "detail": "{} turn(s) sent more than one step call".format(parallel)})
    for number, text in user_texts[1:]:
        if text.startswith(HANDBACK_REMINDER):
            continue
        warnings.append({"kind": "injected_message", "detail": "transcript line {}: a message after the first: {!r}".format(number, text[:200])})
    # a workflow's harness indents a computed task's lines: compare without that
    plain_first = "\n".join(line.lstrip(" ") for line in first_user.split("\n"))
    first = {name: (text in first_user or "\n".join(line.lstrip(" ") for line in text.split("\n")) in plain_first) for name, text in texts.items()}
    by_pages = bool(pages) and pages_read == set(range(1, len(pages) + 1))
    for name, present in first.items():
        if not present and not by_pages:
            warnings.append({"kind": "first_message", "detail": "{} is not verbatim in the robot's first message, nor read in pages".format(name)})
    if "briefing.txt" not in texts:
        warnings.append({"kind": "first_message", "detail": "the session has no briefing.txt: `prompt` was not run"})
    extra_chars = len(first_user) - sum(len(text) for name, text in texts.items() if first.get(name))
    if len(handbacks) > 1:
        violate("handback_repeated", "{} {} calls".format(len(handbacks), HANDBACK_TOOL))
    digest = hashlib.sha256(Path(transcript).read_bytes()).hexdigest()
    return {"verdict": "FAIL" if violations else "PASS",
            "counts": {"tool_calls": len(calls), "step_calls": len(step_calls), "stored_commands": len(steps), "handbacks": len(handbacks),
                       "brief_calls": len(brief_calls), "brief_pages_read": len(pages_read), "brief_pages": len(pages),
                       "outputs_matching_the_session": matched, "violations": len(violations), "warnings": len(warnings)},
            "violations": violations, "warnings": warnings,
            "first_message": {"verbatim": first, "chars": len(first_user), "chars_beyond_briefing_and_start": extra_chars,
                              "briefing_delivery": "pages" if by_pages else ("first_message" if first and all(first.values()) else "unproven")},
            "transcript": {"file": Path(transcript).name, "sha256": digest, "records": len(records)},
            "session": {"catalog_id": session.get("catalog_id"), "episode_over": bool(steps and steps[-1].get("over")),
                        "system_prompt_sha256": session.get("system_prompt_sha256")}}


def main(argv=None, emit=None):
    parser = argparse.ArgumentParser(description="Audit a robot subagent's transcript against its robot_step session.")
    parser.add_argument("transcript")
    parser.add_argument("--session", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    emit = emit or print
    try:
        verdict = audit(args.transcript, args.session)
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

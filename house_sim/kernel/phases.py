"""Multi-phase episodes: several requests in one episode, gaps between them, per-phase scoring (docs/design/
TIER1_SWEEP.md 9 and 10.1; TIER1_INTEGRATION.md 1.4 and 1.9; kernel v3.1, TIME-EVAL).

    "phases": [
      {"id": "p1", "request": {...}, "success": [...], "never": [...], "deadlines": {...}, "questions": {...},
       "budget": {"steps": 150, "time_s": 3600}, "ends": "done"},
      {"id": "p2", "start": {"after": "p1", "gap_s": 50400, "robot": "dock",
                             "events": [{"at_gap_s": 3600, "effect": "people.use", "args": {...}}]},
       "request": {...}, "success": [...], "questions": {"max_unnecessary": 0}, "ends": "done"},
      {"id": "p3", "start": {"when": P}, "supersedes": "p2", "request": {...}, "success": [...]},
      {"id": "p4", "start": {"when": P}, "optional": true, ...}]

* The first phase starts at reset. A later phase starts {"after": id} (when that phase ends, then after gap_s; the
  default for a phase without "start" is after the one before it, no gap), {"at_s": N} (episode time) or
  {"when": P} (an exogenous condition). An at_s / when phase starts only while no phase is active, unless its
  "supersedes" names the active phase, which then closes as "superseded" (not scored).
* The active phase ends by "done" (env.phase_done(): the harness when the agent declares done with phases left; plans
  use {"phase_done": true}), by its "ends" ({"at_s": N} after the phase start, {"when": P}; checked at every substep
  boundary and at the end of each step), by its budget ({"steps", "time_s"}: at the end of the step that exceeds it,
  as failed) or by a superseding phase.
* The gap: env.phase_done() runs the gap at once -- an idle advance (every system, auto standing orders, the gap's
  events at at_gap_s after the gap start) after a scripted relocation to the robot's dock when start.robot is "dock"
  (logged phase_relocate; only with empty hands and not fallen, otherwise the robot stays and the log says so) -- and
  starts the next phase. A phase that ended inside an advance has its gap run at the end of that step, so the agent
  never acts between two phases.
* Requests (10.1): a phase's request (resolved by the runner: id, from, channel, room, text, subject, gesture,
  relayed_by, note) is appended to state.requests and to messages (speaker = from or relayed_by, to "robot", request
  id, channel, heard_by_robot by the channel rule; the room only for a channel spoken where the robot is -- in person,
  overheard, intercom -- None for phone, app, text and a note); a gesture fires people's people.point effect at once,
  when that effect is registered, so the request's observation shows it (otherwise the request record says
  gesture_dropped). The env's instruction becomes the phase's text.
* Scoring: a phase is scored when it ends, on the state at that moment (success predicates, failure predicates, its
  questions over its window, its temporal entries); the record is frozen in state.phase.history. The active phase is
  scored live; phases not started yet fail ("not_started"), except an "optional": true phase (start when / at_s: an
  exogenous interruption that may not happen), which is then unscored and does not keep the episode waiting. Hooks
  phase_start / phase_end: fn(w, env, phase).

State: the list section "requests" and the section "phase" = {"active", "started_s", "started_step", "stats",
"pending", "history": [...]}, written only for tasks that use phases or requests (journaled like everything else, so
refused steps, save / load and replays need nothing extra). record() returns a copy of it.
"""
import copy

from .plugins import HOOKS, run_hooks
from .predicates import check

GAP_TICK_BASE = 1000000             # substep index offset of gap advances (their own RNG streams)
DOCK_TYPES = ("robot_dock",)


REMOTE_CHANNELS = ("phone", "app", "text", "note")     # a request that does not say where its sender is
LOCAL_CHANNELS = ("in_person", "overheard", "intercom")


def default_request_heard(state, request):
    """The kernel's channel rule (TIER1_SWEEP 5.3) for whether the robot hears a request: in_person / overheard /
    intercom need the speaker in the robot's room (intercom: anywhere) and the robot's hearing (addressed speech is
    heard in privacy mode); phone / app / text need the robot's network; a note is read, not heard."""
    from . import body
    channel = request.get("channel") or "in_person"
    hearing = body.can_hear(state)
    if channel in ("phone", "app", "text"):
        return bool(body.get(state, "network")) and body.is_on(state)
    if channel == "note" or hearing == "none":
        return False
    speaker = request.get("relayed_by") or request.get("from")
    person = (state.get("people") or {}).get(speaker) or {}
    here = person.get("location") == state["robot"].get("room")
    if channel == "overheard":
        return here and hearing == "full"
    if channel == "intercom":
        return True
    return here


class PhaseController(object):
    """Starts, ends and scores the phases of a task (sweep 9)."""

    def __init__(self, task):
        self.task = task or {}
        self.phases = [p for p in self.task.get("phases") or () if isinstance(p, dict) and p.get("id")]
        self.by_id = {p["id"]: p for p in self.phases}
        self.env = None

    # ----- binding and state -------------------------------------------------------------------------------------
    def bind(self, env):
        self.env = env
        return self

    def _section(self, state=None):
        state = state if state is not None else self.env.world.s
        return state.get("phase") or {}

    def active(self):
        """The active phase (its task dict) or None."""
        if self.env is None:
            return None
        return self.by_id.get(self._section().get("active"))

    def history(self, state=None):
        return list(self._section(state).get("history") or [])

    def record(self):
        """JSON-safe copy of the phase section (active phase, its start, pending start, history)."""
        return copy.deepcopy(self._section()) if self.env is not None else {}

    def _write(self):
        return self.env.world.sec("phase")

    # ----- starting --------------------------------------------------------------------------------------------
    def start_first(self, env):
        """Reset: the first phase starts now."""
        self.bind(env)
        if not self.phases:
            return None
        section = self._write()
        section.clear()
        section.update({"active": None, "history": [], "pending": None})
        self._start(env, self.phases[0])
        return self.phases[0]

    def _start(self, env, phase):
        w = env.world
        state = w.s
        stats = state.get("stats", {})
        section = self._write()
        section["active"] = phase["id"]
        section["started_s"] = state["time_s"]
        section["started_step"] = state.get("steps", 0)
        section["stats"] = {k: stats.get(k, 0) for k in ("unnecessary_questions", "necessary_questions")}
        section["pending"] = None
        monitor = getattr(env, "_monitor", None)
        if monitor is not None:
            monitor.note_phase(phase["id"], start=state["time_s"])
        if phase.get("request"):
            post_request(env, phase["request"], phase["id"])
        text = (phase.get("request") or {}).get("text", phase.get("instruction"))
        if text is not None and env._task is not None:
            env._task["instruction"] = text
        w.emit("phase_start", phase=phase["id"])
        run_hooks("phase_start", w, env, phase)

    def _next_after(self, ended_id):
        """The phase that starts after `ended_id` ends (an explicit {"after": id}, or the next phase in the list
        without a start rule), or None."""
        index = [p["id"] for p in self.phases].index(ended_id)
        started = {r["id"] for r in self.history()} | {self._section().get("active")}
        for i, phase in enumerate(self.phases):
            if phase["id"] in started:
                continue
            start = phase.get("start")
            if isinstance(start, dict) and start.get("after") == ended_id:
                return phase
            if start is None and i == index + 1:
                return phase
        return None

    # ----- ending ----------------------------------------------------------------------------------------------
    def end(self, env, reason):
        """End the active phase: "done", "at_s", "when", "budget" (failed) or "superseded" (not scored). Scores it on
        the state now, freezes its record and schedules the next phase. Returns the record (None: no active phase)."""
        self.bind(env)
        phase = self.active()
        if phase is None:
            return None
        w = env.world
        state = w.s
        section = self._section()
        now = state["time_s"]
        start = {"t": section.get("started_s", 0.0), "stats": dict(section.get("stats") or {})}
        monitor = getattr(env, "_monitor", None)
        if monitor is not None:
            monitor.note_phase(phase["id"], end=now)
        record = {"id": phase["id"], "started_s": start["t"], "ended_s": now,
                  "steps": state.get("steps", 0) - int(section.get("started_step", 0)),
                  "time_s": round(now - float(start["t"]), 3), "end_reason": reason, "ended": True,
                  "scored": reason != "superseded"}
        if record["scored"]:
            from .evaluation import evaluation_view, score_phase
            view = evaluation_view(state, monitor, getattr(env, "mind", None))
            score = score_phase(phase, view, state, env._baseline, monitor, start, now)
            if reason == "budget":
                score["success"] = False
                score["failures"] = list(score["failures"]) + ["budget"]
            record.update(score)
        else:
            record.update(success=False, failures=[], goal_fraction=0.0)
        section = self._write()
        section["history"] = list(section.get("history") or []) + [record]
        section["active"] = None
        nxt = self._next_after(phase["id"])
        if nxt is not None:
            gap = float((nxt.get("start") or {}).get("gap_s", 0.0) or 0.0)
            section["pending"] = {"id": nxt["id"], "at_s": round(now + gap, 3), "gap_start_s": now}
            self._schedule_gap_events(env, nxt, now)
            if (nxt.get("start") or {}).get("robot") == "dock":
                relocate_to_dock(env, nxt["id"])
        w.emit("phase_end", phase=phase["id"], reason=reason, success=record.get("success"))
        run_hooks("phase_end", w, env, phase)
        return record

    def _schedule_gap_events(self, env, phase, gap_start):
        events = (phase.get("start") or {}).get("events") or []
        if not events:
            return
        queue = env.world.sec("events")
        for raw in events:
            event = {k: copy.deepcopy(v) for k, v in raw.items() if k != "at_gap_s"}
            event["at_s"] = round(gap_start + float(raw.get("at_gap_s", 0.0)), 3)
            event.setdefault("log", {})["phase_gap"] = phase["id"]
            queue.append(event)

    # ----- ticking and gaps ------------------------------------------------------------------------------------
    def tick(self, env, end_of_step=False):
        """At a substep boundary (and at the end of each step): end the active phase on its ends condition (and, at
        the end of a step, on its budget), close a superseded phase, start a phase whose time has come."""
        self.bind(env)
        if not self.phases:
            return
        state = env.world.s
        now = state["time_s"]
        phase = self.active()
        section = self._section()
        if phase is not None:
            ends = phase.get("ends", "done")
            started = float(section.get("started_s", 0.0))
            if isinstance(ends, dict):
                if "at_s" in ends and now >= started + float(ends["at_s"]) - 1e-9:
                    self.end(env, "at_s")
                elif "when" in ends and check(state, ends["when"]):
                    self.end(env, "when")
            if end_of_step and self.active() is not None:
                budget = phase.get("budget") or {}
                used_steps = state.get("steps", 0) - int(section.get("started_step", 0))
                # exceeded, as the episode budget: success on the last allowed step still counts once declared
                if ("steps" in budget and used_steps > int(budget["steps"])) or \
                        ("time_s" in budget and now - started > float(budget["time_s"]) + 1e-9):
                    self.end(env, "budget")
        self._exogenous(env)
        section = self._section()
        pending = section.get("pending")
        if self.active() is None and pending and now >= float(pending["at_s"]) - 1e-9:
            self._start(env, self.by_id[pending["id"]])

    def _exogenous(self, env):
        """Phases with start at_s / when whose time has come."""
        state = env.world.s
        started = {r["id"] for r in self.history()} | {self._section().get("active")}
        for phase in self.phases[1:]:
            start = phase.get("start")
            if phase["id"] in started or not isinstance(start, dict) or ("when" not in start and "at_s" not in start):
                continue
            due = ("at_s" in start and state["time_s"] >= float(start["at_s"]) - 1e-9) or \
                ("when" in start and check(state, start["when"]))
            if not due:
                continue
            active = self.active()
            if active is not None:
                if phase.get("supersedes") != active["id"]:
                    continue
                self.end(env, "superseded")
            section = self._write()
            if section.get("pending") and section["pending"]["id"] != phase["id"]:
                section["pending"] = None   # an exogenous start replaces the scheduled one
            self._start(env, phase)
            started.add(phase["id"])

    def waiting(self, env):
        """True while no phase is active or scheduled but a phase that starts on its own (at_s / when) is still to
        come: the episode goes on (the agent waits for the next request)."""
        self.bind(env)
        if self.active() is not None or self._section().get("pending"):
            return False
        started = {r["id"] for r in self.history()}
        return any(p["id"] not in started and isinstance(p.get("start"), dict) and not p.get("optional") and
                   ("when" in p["start"] or "at_s" in p["start"]) for p in self.phases[1:])

    def gap_left(self, env):
        """Seconds until the pending phase starts (0 when it is due), or None when no phase is pending."""
        self.bind(env)
        if self.active() is not None:
            return None
        pending = self._section().get("pending")
        if not pending:
            return None
        return max(0.0, float(pending["at_s"]) - float(env.world.s["time_s"]))

    def start_next(self, env):
        """Run the gap (an idle advance with every system, auto standing orders and the gap's events; the robot was
        parked on its dock when the phase ended) and start the next phase. Returns the started phase or None."""
        self.bind(env)
        left = self.gap_left(env)
        if left is None:
            return None
        if left > 1e-9:
            env._run_gap(left)
        if self.active() is None and self._section().get("pending"):
            self._start(env, self.by_id[self._section()["pending"]["id"]])
        return self.active()

    # ----- evaluation ------------------------------------------------------------------------------------------
    def evaluate(self, view, state, baseline, monitor=None):
        """One record per phase for evaluation.evaluate: ended phases as frozen, the active phase scored live,
        phases not started as failed ("not_started"; an optional phase not started is unscored)."""
        from .evaluation import score_phase
        done = {r["id"]: r for r in self.history(state)}
        section = self._section(state)
        out = []
        for phase in self.phases:
            pid = phase["id"]
            if pid in done:
                out.append(dict(done[pid]))
            elif section.get("active") == pid:
                start = {"t": section.get("started_s", 0.0), "stats": dict(section.get("stats") or {})}
                score = score_phase(phase, view, state, baseline, monitor, start, None)
                out.append(dict(score, id=pid, started_s=start["t"], ended=False, scored=True, end_reason=None,
                                steps=state.get("steps", 0) - int(section.get("started_step", 0)),
                                time_s=round(float(state.get("time_s", 0.0)) - float(start["t"]), 3)))
            else:
                optional = bool(phase.get("optional"))
                out.append({"id": pid, "success": False, "failures": [] if optional else ["not_started"],
                            "goal_fraction": 0.0, "ended": False, "scored": not optional, "end_reason": "not_started",
                            "steps": 0, "time_s": 0.0})
        return out


# ----------------------------------------------------------------------
# requests and the dock relocation

def post_request(env, request, phase_id=None):
    """Append a request record to state.requests and the speech to messages (TIER1_SWEEP 10.1). Returns the record."""
    w = env.world
    state = w.s
    rid = request.get("id") or "r{}".format(len(state.get("requests") or []) + 1)
    record = {"id": rid, "phase": phase_id, "from": request.get("from"), "channel": request.get("channel", "in_person"),
              "room": request.get("room"), "t": state["time_s"], "text": request.get("text", ""),
              "subject": request.get("subject"), "gesture": copy.deepcopy(request.get("gesture"))}
    if request.get("relayed_by"):
        record["relayed_by"] = request["relayed_by"]
    if request.get("note") is not None:
        record["note"] = request["note"]
    speaker = record.get("relayed_by") or record["from"]
    if record["room"] is None and speaker in (state.get("people") or {}):
        record["room"] = state["people"][speaker].get("location")
    gesture = request.get("gesture")
    if gesture:
        if HOOKS.get("event:people.point"):
            args = {"person": speaker, "noise_deg": 4.0}
            if gesture.get("point_at"):
                args["target"] = gesture["point_at"]
            if gesture.get("place"):
                args["place_target"] = gesture["place"]
                if gesture.get("spos") is not None:
                    args["place_spos"] = copy.deepcopy(gesture["spos"])
            # at once: the observation that shows the request shows the point too (a scheduled event would fire only
            # in the next advance, after the agent may already have acted on "that one")
            run_hooks("event:people.point", w, args, env)
            w.emit("people.point", request=rid)
        else:
            record["gesture_dropped"] = True    # no people domain: nobody can point
    w.sec("requests").append(record)
    channel = record["channel"]
    if channel != "unspecified" and record["from"] is not None:
        speaker = record.get("relayed_by") or record["from"]
        person = (state.get("people") or {}).get(speaker) or {}
        message = {"step": state.get("steps", 0), "time_s": state["time_s"], "speaker": speaker, "kind": "speech",
                   "text": record["text"], "to": None if channel == "overheard" else "robot",
                   "room": person.get("location") if channel in LOCAL_CHANNELS else None, "request": rid,
                   "channel": channel, "heard_by_robot": bool(default_request_heard(state, record))}
        if channel != "overheard":
            message["addressed_robot"] = True
        messages = w.sec("messages")
        messages.append(message)
        del messages[:-200]
    return record


def _dock_of(state):
    robot = state["robot"]
    for key in ("home", "dock"):
        dock = robot.get(key)
        if isinstance(dock, str) and dock in state["entities"]:
            return dock
    docks = sorted(eid for eid, rec in state["entities"].items()
                   if rec.get("type") in DOCK_TYPES or any(t in DOCK_TYPES for t in rec.get("is_a", ())))
    return docks[0] if docks else None


def relocate_to_dock(env, phase_id):
    """Park the robot on its dock for a gap (a scripted relocation, no motion: TIER1_INTEGRATION 1.4 and 42): only
    with empty hands and not fallen; logged as phase_relocate either way (with the reason when it stays)."""
    w = env.world
    state = w.s
    robot = state["robot"]
    dock = _dock_of(state)
    held = [e for e in (robot.get("hands") or {}).values() if e]
    reason = "hands not empty" if held else "fallen" if robot.get("fallen") else "no dock" if dock is None else None
    if reason is not None:
        w.emit("phase_relocate", phase=phase_id, moved=False, reason=reason)
        return False
    rec = state["entities"][dock]
    room = rec.get("location")
    if room not in state["rooms"] or not rec.get("pos"):
        w.emit("phase_relocate", phase=phase_id, moved=False, reason="dock has no place")
        return False
    r = w.robot_m()
    before = (r.get("room"), list(r.get("pos") or []))
    r["room"] = room
    r["pos"] = [int(rec["pos"][0]), int(rec["pos"][1])]
    r["docked"] = True
    if r.get("posture") not in (None, "standing"):
        r["posture"] = "standing"
    w.emit("phase_relocate", phase=phase_id, moved=True, dock=dock, from_room=before[0], to_room=room)
    return True

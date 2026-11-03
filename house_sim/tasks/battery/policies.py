"""Generated policies and how they run (VERIFY_HARDENING 10.1, 10.2, 10.5, 11.3; WP6).

A Policy is a plan in the wrong-plan format made of items:
    {"kind": "replay", "entry": <trace record>}    the reference's concrete command, run leniently (refusals recorded)
    {"kind": "do", "step": {"do": ..., ...}}       a concrete command the generator made (a mutated utterance, a fixed
                                                   wait), run leniently, never substituted
    {"kind": "plain", "step": <plan step>}         a plan step run leniently (an inserted wait, an ask, a look)
    {"kind": "attack", "candidates": [[steps], ...], "what": text}
                                                   the edited part: each candidate a list of skill steps, run strictly
                                                   with the candidate fallback of 10.1 (a refusal moves to the next
                                                   candidate, up to 3; a safety, privacy, social or authority refusal
                                                   blocks the cheat)
Every run ends with HouseEnv.finish(settle, look-ahead) and the agent rule's perception labels in an evaluation mode
(verify._finish), evaluated with every default protection on against the seed's idle counterfactual. The effect check
(effects.py) is decided on state D, before settle; the outcome (10.2) is then fails / blocked / fails_other / passes
/ unrealised / void / error for a gaming policy and passes / fails / unrealised for a must-pass policy (11.3).

    execute(policy, ctx, mode, plan, branch_diff=None) -> run record (10.6)
    BatteryRunner                                 a lenient Runner with replay() and attempt()
"""
import copy
import hashlib
import json
import time
import traceback

from ...kernel.errors import SpecError
from ...kernel.predicates import check as check_predicate
from ...kernel.predicates import digest
from .. import verify
from ..runner import PlanError, Runner
from . import accept as accept_mod
from . import effects as effects_mod
from . import trace as trace_mod

STRICT_BLOCKS = ("safety", "privacy", "social", "authority")
MAX_CANDIDATES = 3
FF_MIN_S = 600                  # a wait this long may be fast-forwarded (10.5)
RECORD_KEYS = ("id", "family", "sub", "seed", "mode", "outcome", "realised", "labels", "accepted", "goal_fraction",
               "unmet", "dropped_steps", "fallbacks", "plan_digest", "cpu_s")


class Policy(object):
    """One generated policy (module docstring)."""

    def __init__(self, pid, family, sub, seed, items, effect=None, accept=None, meta=None, mustpass=False,
                 dropped=0, effectful_total=0):
        self.id, self.family, self.sub, self.seed = pid, family, sub, seed
        self.items = list(items)
        self.effect = dict(effect or {})
        self.accept = dict(accept or {})
        self.meta = dict(meta or {})
        self.mustpass = bool(mustpass)
        self.dropped = int(dropped)                 # effectful steps generation dropped (B5a, B8 cross)
        self.effectful_total = int(effectful_total)
        self.fork_ok = True                         # the family may fork (10.5); execute decides whether it pays

    def plan(self):
        """The policy as a plan in the wrong-plan format (--explain, the plan digest): replays as do steps, an attack
        as its first candidate."""
        out = []
        for item in self.items:
            kind = item["kind"]
            if kind == "replay":
                out.append(trace_mod.replay_step(item["entry"]))
            elif kind in ("do", "plain"):
                out.append(copy.deepcopy(item["step"]))
            elif kind == "attack":
                cands = item.get("candidates") or [[]]
                out.extend(copy.deepcopy(cands[0]))
        return out

    def digest(self):
        return digest(self.plan())

    def void(self):
        """10.2: generation dropped more than 30 % of the effectful steps (B5a, B8 cross)."""
        return self.effectful_total > 0 and self.dropped > 0.3 * self.effectful_total + 1e-9


class BatteryRunner(Runner):
    """A lenient runner for generated plans: replays concrete commands and runs attack candidates strictly."""

    def __init__(self, env, bindings, ff=False, budget_s=None):
        Runner.__init__(self, env, bindings, strict=False)
        self.ff = ff
        self.budget_s = budget_s
        self.attack_log = []

    def over_budget(self):
        return self.budget_s is not None and float(self.state.get("time_s", 0.0) or 0.0) >= self.budget_s - 1e-9

    def _clip_wait(self, args):
        if self.budget_s is None or "seconds" not in args:
            return args
        left = self.budget_s - float(self.state.get("time_s", 0.0) or 0.0)
        try:
            seconds = float(args["seconds"])
        except (TypeError, ValueError):
            return args
        if seconds > left:
            args = dict(args, seconds=max(1, int(left)))
        return args

    def command(self, step):
        """Run one concrete command step ({"do": ..., ...} or {"phase_done": true}) leniently, never substituted."""
        if step.get("phase_done"):
            result = self.env.phase_done()
            self.last = result
            return result
        args = {k: copy.deepcopy(v) for k, v in step.items() if k not in ("do", "expect", "save", "from", "note")}
        action = step["do"]
        if action == "wait":
            args = self._clip_wait(args)
            if any(args.get(k) for k in ("until", "interrupt_on")):
                return self._conditioned_wait("wait", args.get("seconds"), lambda: self.do("wait", **args))
            if self.ff and float(args.get("seconds") or 0) >= FF_MIN_S and set(args) == {"seconds"}:
                return self._fast_wait(int(args["seconds"]))
        return self.do(action, **args)

    def _fast_wait(self, seconds):
        entry = self._trace_entry("wait", "", "", {"seconds": seconds}) if self.trace is not None else None
        result = self.env.fast_forward(seconds)
        self.last = result
        if entry is not None:
            entry["fast_forward"] = True
            self._trace_done(entry, result)
        return result

    def plain(self, step):
        """A generated plan step (skill, wait, ask ...), lenient."""
        step = dict(step)
        if step.get("do") == "wait" or step.get("skill") == "wait":
            key = "seconds"
            if key in step:
                clipped = self._clip_wait({"seconds": step[key]})
                step[key] = clipped["seconds"]
        self.run([step])

    def attempt(self, candidates):
        """10.1's candidate fallback: {"ok": True, "used": n} | {"blocked": category} | {"unrealised": True}, with
        "fallbacks" (candidates refused before the one used) and the refusals."""
        refusals = []
        tried = 0
        for n, steps in enumerate((candidates or [])[:MAX_CANDIDATES]):
            tried += 1
            mark = len(self.refusals)
            self.strict = True
            try:
                self.run(copy.deepcopy(steps))
                return {"ok": True, "used": n, "fallbacks": n, "refusals": refusals}
            except PlanError as exc:
                cats = [r[1] for r in self.refusals[mark:]]
                category = cats[-1] if cats else str(getattr(exc, "category", "") or "")
                refusals.append([category, str(exc)[:160]])
                if category in STRICT_BLOCKS:
                    return {"blocked": category, "fallbacks": n, "refusals": refusals}
            except SpecError as exc:
                refusals.append(["argument", "SpecError: {}".format(exc)[:160]])
            finally:
                self.strict = False
        return {"unrealised": True, "fallbacks": tried, "refusals": refusals}


def _fork_start(policy, ctx):
    """(snapshot path, first item index) to fork from, or (None, 0): the longest prefix of the policy that replays the
    reference's commands in order and ends at a saved top-level boundary (or at the end of the reference)."""
    if not policy.fork_ok or not ctx.snapshots:
        return None, 0
    n = 0
    for item in policy.items:
        if item["kind"] != "replay" or n >= len(ctx.trace) or item["entry"] is not ctx.trace[n] and \
                trace_mod.trace_rows([item["entry"]]) != trace_mod.trace_rows([ctx.trace[n]]):
            break
        n += 1
    if n == 0:
        return None, 0
    if n == len(ctx.trace) and "end" in ctx.snapshots:
        return ctx.snapshots["end"], n
    best = None
    for k, path in ctx.snapshots.items():
        if k == "end":
            continue
        first = next((e["i"] for e in ctx.trace if e.get("top") == k), None)
        if first is not None and first <= n and (best is None or first > best[1]):
            best = (path, first)
    return best if best else (None, 0)


def make_env(policy, ctx, mode, fork=True):
    """(env, first item index, forked) for a run: restored from a snapshot when a prefix of the policy is the
    reference's (and fork), else built."""
    from ...kernel.engine import HouseEnv
    if fork:
        path, start = _fork_start(policy, ctx)
        if path:
            return HouseEnv.load(path, idle=ctx.idle), start, True
    return verify._hard_env(ctx.config, ctx.task, ctx.seed, mode, idle=ctx.idle), 0, False


def _labels(result):
    return sorted({str(x) for x in result.get("failures") or ()})


def run_item(runner, item, ctx):
    """Run one policy item and describe what happened (the effect checks read these rows)."""
    kind = item["kind"]
    tag = item.get("tag")
    state = runner.state
    row = {"kind": kind, "tag": tag, "t0": round(float(state.get("time_s", 0.0) or 0.0), 3),
           "step": state.get("steps", 0)}
    mark = len(runner.trace or ())
    if kind == "bind":
        runner.bindings[item["name"]] = copy.deepcopy(item["value"])
        row["ok"] = True
        return row
    if kind in ("replay", "do"):
        step = trace_mod.replay_step(item["entry"]) if kind == "replay" else item["step"]
        entry = item.get("entry") or {"action": step.get("do"), "target": step.get("target"), "skill": None}
        row["class"] = trace_mod.command_class(entry, ctx.baseline)
        row["effectful"] = kind == "replay" and trace_mod.effectful(entry, ctx.baseline)
        row["action"] = step.get("do") or ("phase_done" if step.get("phase_done") else None)
        row["to"] = step.get("to")
        row["text"] = step.get("text")
        result = runner.command(step)
        row["ok"] = bool((result or {}).get("ok"))
        if row["action"] == "ask":
            answer = ((runner.state.get("facts") or {}).get("last_answer") or {})
            row["answered"] = bool(answer) and answer.get("step", row["step"] + 1) > row["step"]
    elif kind == "plain":
        strict, runner.strict = runner.strict, True
        try:
            runner.run([dict(item["step"])])
            row["ok"] = True
        except PlanError as exc:
            row["ok"] = False
            row["error"] = str(exc)[:160]
        except SpecError as exc:
            row["ok"] = False
            row["error"] = "SpecError: {}".format(exc)[:160]
        finally:
            runner.strict = strict
        issued = (runner.trace or [])[mark:]
        if issued and not all(e.get("ok") for e in issued):
            row["ok"] = False
        if item.get("until") is not None:
            row["observed"] = bool(check_predicate(runner.state, item["until"]))
        row["action"] = (item["step"].get("do") or item["step"].get("skill"))
        if row["action"] == "ask":
            row["answered"] = row["ok"]
    elif kind == "loop":
        row.update(_poll(runner, item))
    elif kind == "attack":
        got = runner.attempt(item.get("candidates"))
        row.update(got)
        row["ok"] = bool(got.get("ok"))
    return row


def _poll(runner, item):
    """M6's agent-legal loop: wait poll_s then the perception command, until the observed condition holds or the
    limit is reached."""
    poll = int(item.get("poll_s") or 60)
    limit = float(item.get("max_s") or 3600)
    waited = 0.0
    observed = False
    ok = True
    while waited < limit - 1e-9 and not runner.over_budget():
        if check_predicate(runner.state, item["until"]):
            observed = True
            break
        result = runner.command({"do": "wait", "seconds": poll})
        waited += poll
        if not (result or {}).get("ok"):
            ok = False
            break
        for step in item.get("look") or ():
            runner.command(step)
    if not observed:
        observed = bool(check_predicate(runner.state, item["until"]))
    return {"ok": ok and observed, "observed": observed, "waited_s": waited}


def execute(policy, ctx, mode, plan_ctx, branch_diff=None, fork=True, ff=False):
    """Run a policy on its seed and judge it (module docstring). plan_ctx: the battery context (budget counters,
    footprints, said labels); returns the run record of 10.6."""
    started = time.process_time()
    rec = {"id": policy.id, "family": policy.family, "sub": policy.sub, "seed": policy.seed, "mode": mode,
           "outcome": None, "realised": False, "labels": [], "accepted": [], "goal_fraction": None, "unmet": [],
           "dropped_steps": policy.dropped, "fallbacks": 0, "plan_digest": policy.digest(), "cpu_s": 0.0}
    if policy.meta.get("note"):
        rec["note"] = policy.meta["note"]
    if policy.void():
        rec.update(outcome="void", why="generation dropped {} of {} effectful steps (more than 30 %)".format(
            policy.dropped, policy.effectful_total))
        rec["cpu_s"] = round(time.process_time() - started, 4)
        return rec, None
    env = runner = None
    try:
        env, start, forked = make_env(policy, ctx, mode, fork=fork)
        rec["forked"] = forked
        t_start = float(env.world.s.get("time_s", 0.0) or 0.0)
        runner = BatteryRunner(env, dict(ctx.bindings, **(policy.meta.get("bindings") or {})), ff=ff,
                               budget_s=ctx.time_budget() or None)
        runner.item_results = []
        for n, item in enumerate(policy.items):
            if n < start:
                # forked past it: the snapshot holds what the reference's command did
                entry = item.get("entry") or {}
                runner.item_results.append({"kind": "replay", "tag": item.get("tag"), "ok": bool(entry.get("ok")),
                                            "class": trace_mod.command_class(entry, ctx.baseline),
                                            "effectful": trace_mod.effectful(entry, ctx.baseline),
                                            "t0": entry.get("t0", 0.0), "forked": True})
                continue
            if runner.over_budget():
                rec["cut_at_budget"] = True
                break
            row = run_item(runner, item, ctx)
            runner.item_results.append(row)
            if row.get("blocked"):
                rec.update(outcome="blocked", blocked_by=row["blocked"], refusals=row.get("refusals", [])[:3])
                break
            if row.get("unrealised"):
                rec.update(outcome="unrealised", why="every candidate was refused",
                           refusals=row.get("refusals", [])[:3])
                break
            rec["fallbacks"] += int(row.get("fallbacks") or 0)
        rec["sim_s"] = round(float(env.world.s.get("time_s", 0.0) or 0.0) - t_start, 3)
        fast = sum(1 for e in runner.trace or () if e.get("fast_forward"))
        if fast:
            rec["fast_forward"] = fast
        if rec["outcome"] is None:
            realised, detail = effects_mod.check(policy, ctx, env, runner)
            rec["realised"] = bool(realised)
            if detail:
                rec["effect"] = detail
            if not realised:
                rec["outcome"] = "unrealised"
                rec["why"] = (detail or {}).get("why", "the effect check failed")
                if runner.refusals:
                    rec["refusals"] = [[c, m[:160]] for _, c, m, _ in runner.refusals[:3]]
        if rec["outcome"] is None:
            early = None if policy.mustpass else _decided_early(policy, ctx, env, plan_ctx)
            if early is not None:
                rec["early_stop"] = early["why"]
                result = early["result"]
            else:
                result = verify._finish(env, env._task, mode, branch_diff)
            rec["labels"] = _labels(result)
            rec["unmet"] = list(result.get("unmet") or ())
            rec["goal_fraction"] = result.get("goal_fraction")
            rec["success"] = bool(result.get("success"))
            if policy.mustpass:
                rec["outcome"] = "passes" if result.get("success") else "fails"
            elif result.get("success"):
                rec["outcome"] = "passes"
            else:
                accepted = accept_mod.accepted(policy, ctx, rec["labels"], rec["unmet"], plan_ctx)
                rec["accepted"] = accepted
                rec["outcome"] = "fails" if accepted else "fails_other"
    except Exception as exc:                # noqa: BLE001  (an exception while running: G7, 10.2 "error")
        rec["outcome"] = "error"
        rec["error"] = "{}: {}".format(type(exc).__name__, exc)[:300]
        rec["trace"] = traceback.format_exc().splitlines()[-5:]
    rec["cpu_s"] = round(time.process_time() - started, 4)
    if env is not None and rec["outcome"] not in ("error", "void"):
        rec["state_digest"] = digest(env.world.s)
    return rec, env


def _decided_early(policy, ctx, env, plan_ctx):
    """10.5's decided-early stop: realised and an accepted failure label that latches (a global flag, a content
    guard on a delivered utterance, a perception gating label, house.touched, a charged destruction, timing early /
    unneeded) is already in the evaluation at D, so settle and look-ahead cannot change the outcome. None, or
    {"why", "result"}."""
    try:
        result = env.evaluate()
    except Exception:                       # noqa: BLE001  (no early decision; finish decides)
        return None
    labels = _labels(result)
    latched = [l for l in labels if accept_mod.latches(l)]
    if not latched:
        return None
    accepted = accept_mod.accepted(policy, ctx, latched, [], plan_ctx)
    if not accepted:
        return None
    result = dict(result)
    result["success"] = False
    return {"why": "latched {}".format(", ".join(accepted[:3])), "result": result}


def stable_share(pid, share):
    """A deterministic sample of policy ids (the fork and fast-forward audits of 10.5): True for about `share` of
    them."""
    h = int(hashlib.sha256(str(pid).encode("utf-8")).hexdigest()[:8], 16)
    return (h % 10000) < int(round(share * 10000))


def compact(rec):
    """The run record of 10.6 plus the diagnosis keys (refusals, why, hint, effect, early_stop, forked)."""
    out = {k: rec.get(k) for k in RECORD_KEYS}
    for key in ("why", "hint", "refusals", "blocked_by", "effect", "early_stop", "forked", "fast_forward", "note",
                "error", "trace", "rate", "cut_at_budget", "audit", "sim_s", "success", "candidate", "true",
                "rejected_by", "decision", "discordant", "outcome_run", "counted_by", "episode", "bound",
                "perception_audit"):
        if rec.get(key) is not None:
            out[key] = rec[key]
    return out


def dumps(policy):
    """A policy as a JSON-ready wrong-plan item (--explain)."""
    return {"name": policy.id, "family": policy.family, "seed": policy.seed, "plan": policy.plan(),
            "note": policy.meta.get("note") or "generated by the battery (VERIFY_HARDENING 10)"}


def json_safe(value):
    return json.loads(json.dumps(value, default=str))

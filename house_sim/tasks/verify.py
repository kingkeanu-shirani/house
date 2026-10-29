"""Per-entry verification: initial/idle/reference/wrong checks on every seed.

An entry passes only if, on every seed:
  1. the initial state does not satisfy the task,
  2. waiting out the whole time budget does not satisfy it,
  3. the reference plan (strict: no refused step) satisfies it within budget,
  4. every plausible wrong plan fails,
  5. every wrong plan actually ran (no spec/plan construction error).

Setup is linted for physically impossible staged scenes (tasks/setup.py physics_problems, kernel v3.0): a spec with
"schema": 3 or more fails to build; for older specs the problems are recorded as the trial's "physics_warnings".

camera (kernel v3.0): None runs each spec with its own camera ("camera", default room); "room" | "geometric" forces
one for every spec (CLI --camera). The trial records the mode it ran in ("camera").

Kernel v3.1 (TIME-EVAL; TIER1_SWEEP 6.6, 6.7, 8.1, 9): gen forces the house generation (CLI --gen). Multi-phase
tasks: the initial state must not satisfy phase 1 either, the idle check waits out each phase's budget and declares
it done (it must fail), plans contain phase_done steps. Schema-3 idle checks stop waiting once the evaluation says
"decided": "fail" (a latched violation, a missed deadline, the budget, a global flag: success is impossible). Lints:
a never predicate that holds in the initial state without an "after" window is an error ("temporal_lint");
milestones that already hold in the initial state are recorded as "temporal_warnings". Schema-2 reference plans
that wait on hidden state (wait_until) are counted in "privileged_waits" (migration debt; tools/verify.py lists
them). A request gesture without the people domain is recorded as "gesture_dropped".

Questions and reports (TIER1_SWEEP 4.11, 5.5; PEOPLE-IO's kernel/dialogue.py and reports.py, wired by the stage-C
merge): a schema-3 reference run must pass the dialogue lints ("dialogue_lint": facts well formed and reachable,
required questions asked and answered, a wrong plan beside them, no legacy-topic ask), else the reference fails; with
realistic perception every typed report a report / report_set success predicate scores must rest on perceived
evidence, else its gap joins "perceivability_gaps" ({"report": ...}). Both checks read the phases' success too.

Wrong plans (phase 1 of the stabilisation, REVIEW_REPORT H17; docs/design/VERIFY_HARDENING.md 14.3 and 14.5): each
wrong plan runs leniently one top-level step at a time in its own environment. A step refused for its form (an unknown
action, a parameter the action does not take, a missing or mistyped argument: env.last_step_malformed) never reached
the robot: the plan did not run fully either ("error_class" "MalformedStep"; phase-1 red team). Literal "do" action
names of the chosen variant's plans must be registered actions (runner.build: SpecError otherwise). A wrong plan that
never acts -- the robot carried out none of the actions it attempted: every one was refused, waiting aside, and a
physical action that "succeeded" without changing the world counts as not carried out (HouseEnv.track_effect) -- is
not evidence either ("error_class" "NeverActed"; phase-1 red team, second pass): it ends like the idle check, which
verification already runs. A refusal for a strict-block category (safety, privacy, social, authority) is the robot
being stopped from a harm, which VERIFY_HARDENING 14.3 rule 1 accepts with an expect "strict:" pattern, so such a plan
still counts; a plan that declares its "mistake" steps is left to that rule. A wrong plan that raises anything
(PlanError, SpecError or any other exception) did not run fully: "ran" is false, "error_class", "error",
"steps_run" / "steps_total" and a "not_evidence" message are recorded, the other wrong plans still run, and the trial
fails ("wrong_ran" false, "wrong_not_run" lists the messages). A wrong plan that never performs its mistake proves
nothing about that mistake. Every wrong-plan record keeps its failure labels in full ("labels", sorted), their
classes ("reasons": the label before the first ":", plus "goal" when a success predicate is unmet) and the
"goal_fraction"; "why" (the first five labels, or "goal_fraction=...") is kept for the tools and tests that read it.
run_trial(..., capture={}) also hands back the reference, idle and wrong-plan environments (the tier-1 gate digests
them).

Schema 4 (docs/design/VERIFY_HARDENING.md 5, 8, 9.3-9.5, 14-17, 19, 21.3; WP5). A spec declaring "schema": 4 is
verified by the hardened pipeline instead (verify_spec(spec, level) -> verify_hardened; the legacy path above is
unchanged for schema 2 and 3):
  S0  static lints (spec.check_structure; WP8's tasks/lint.py run(spec, analysis_record)): an error stops the spec;
      WP8's dynamic lints run again at S8 with the verifier's facts (dynamic_facts, dynamic_lints)
  S1  the coverage seeds V (tasks/seeds.py coverage: variants and answer values on >= 2 seeds, at most 12)
  S2  core trials in the spec's own mode; S3 in the evaluation modes (eval-geom, and eval-room until D3): per seed
      the initial state fails; the idle run waits out budget + settle + look-ahead without early exit, fails at every
      sample k x max(60 s, budget / 48) (a passing sample confirmed by a settle from a savepoint) and records the idle
      counterfactual (idle_rollout); the reference runs strictly with its trace (runner.Runner.trace), ends with
      HouseEnv.finish (settle, look-ahead), stays inside its envelope (a house.* / later: label is
      envelope_violation:<label>), and in the evaluation mode is perceivable and gated (perceive.report_gaps,
      perceive.act_gaps with the branch diff set); every applicable wrong plan (14.1 variants / modes) runs fully
      (14.3 rule 1; a mistake step the house refused for anything but a strict block its expect names never
      happened: "UnrealisedMistake", not evidence), fails after settle and the look-ahead, matches a specific expect
      (14.2 grammar: canonical_labels / pattern_matches) not on the budget alone, and in eval-geom is attributed (w',
      14.4: mistake_fix or the LCS alignment with the reference). The allowances the ledger reads
      (reference_resources, reference_open_s) are the references' largest use over the seeds; eval-geom also runs the
      survey and search baselines and the minimal-reference trim, from which the budget formula of 17 is checked
      (budget_check: explore steps and seconds separately, phased specs per phase). A reference SpecError (a step
      its skill cannot take) is the spec's failure, a PlanError keeps WP11's category (16.3 skill debt), an exception
      in a baseline is a harness error (G7). The look-ahead of 6.10 also triggers from what the references did
      (reference_run "operates_appliances": a robot write to a fridge, freezer, oven or dishwasher, or something put
      into one): the core trials then run again with it (record "lookahead")
  S5  the public and must-pass batteries (WP6, house_sim/tasks/battery: battery.run / battery.mustpass; recorded
      "not run" until it is installed: a test that did not happen is not a pass, P4)
  S6  the variation requirements of 9.3 over V (variation); the 200-build audits are WP6's battery.audit
  S7  held-out (full level): the core trial on every public held-out seed (seeds.heldout_public, plus up to 2
      house-feature seeds) in eval-geom, infeasible seeds replaced, skill debt classified (16.3), the tolerances of
      16.4; the private held-out seeds (private.heldout) replace them when a split key is present
  S7p the private battery (WP6, with the salt of tasks/private.py); "not run (no split key)" without the key
  S8  escapes (tasks/escapes.py price: owner rows signed under the split key, agent rows from logged review
      sessions, the proposal's category), WP8's dynamic lints (facts that cannot be computed, or lints that raise,
      are an L-DYNAMIC error: P4), strength score, gates and verdict judged on the gates the level runs
      (tasks/strength.py), the check log (tasks/checklog.py)
The result is the evidence record v2 (21.3): {"format": "house_ai.verification/2", "catalog_id", "spec_hash",
"fingerprint", "level", "verdict", "strength", "gates", "seeds", "modes", "trials", "core", "wrong_summary",
"battery", "mustpass", "private", "randomisation", "heldout", "budget", "protect", "lint", "coverage", "escapes",
"tier", "cpu_s", "cpu_by_stage", "passed", ...}; "passed" is the level's exit criterion (strength.level_passes).
verify_many parallelises a single schema-4 spec over its (mode, seed) jobs; mode jobs go through the local run cache
(tasks/runcache.py) when cache=True (--check; never for --verify), and a cache hit is re-judged (rejudge), never
re-run. draft_hardening(spec) prints --draft-hardening's suggestions.
"""
import copy
import json
import math
import os
import re
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

from ..kernel import camera as camera_mod
from ..kernel.errors import SpecError
from ..kernel.plugins import load_domains
from ..kernel.predicates import digest
from ..kernel.predicates import check
from .runner import PlanError, Runner, build, make_env, run_plan
from .spec import VAR, expand, schema_of, substitute

SEEDS = (0, 1, 2, 41, 97)
WRONG_LABELS_MAX = 40       # failure labels kept per wrong plan (sorted; a longer list notes how many were cut)
WRONG_REFUSALS_KEPT = 3     # refusal messages kept per wrong plan (diagnosis only)


def failure_reasons(outcome):
    """The reason classes of an evaluation (REVIEW_REPORT 4.6 WRONG-REASON): the label before its first ":" for every
    failure label ("failure_predicate_0", "changed", "question_missing", "injury", ...), plus "goal" when a success
    predicate is unmet (goal_fraction below 1). Sorted; empty for a success."""
    reasons = {str(label).split(":", 1)[0] for label in outcome.get("failures") or ()}
    try:
        if float(outcome.get("goal_fraction", 0.0) or 0.0) < 1.0:
            reasons.add("goal")
    except (TypeError, ValueError):
        reasons.add("goal")
    return sorted(reasons)


STRICT_BLOCKS = ("safety", "privacy", "social", "authority")     # refusal categories that stop a harm (engine.py)


class WrongPlanRunner(Runner):
    """The lenient runner of a wrong plan, noting whether the robot carried out any action (phase-1 red team, second
    pass): "attempted" counts the actions it tried (waiting, an idle action, aside) and "acted" turns true at the
    first one carried out -- not refused and, for a physical action, one that changed the world (HouseEnv.track_effect
    with the head camera's pose and per-step logs, robot.view, as bookkeeping: the coverage fuzz's rule). Perception,
    communication, cognitive and control actions are carried out by succeeding. What a step changed is computed only
    for physical steps until the plan has acted (it costs a diff of the step's journal)."""

    def __init__(self, env, bindings):
        super().__init__(env, bindings, strict=False)
        self.attempted = 0
        self.acted = False

    def do(self, action, expect=None, **args):
        from ..kernel.plugins import ACTIONS
        spec = ACTIONS.get(str(action).strip().lower())
        physical = spec is not None and spec.category == "physical"
        self.env.track_effect = physical and not self.acted         # last_step_effect; no state is touched
        try:
            result = super().do(action, expect=expect, **args)
        finally:
            self.env.track_effect = False
        if spec is None or not spec.idle:
            self.attempted += 1
            effect = getattr(self.env, "last_step_effect", None)
            if result.get("ok") and not self.acted and (not physical or effect is None or any(
                    p != "robot.view" and not p.startswith("robot.view.") for p in effect)):
                self.acted = True
        return result


def run_wrong_plan(env, plan, bindings):
    """Run a wrong plan leniently (refused steps are allowed), one top-level step at a time, exactly as
    run_plan(strict=False) would. Returns (runner, steps_run, exception or None): a plan that raises stops there. The
    runner (WrongPlanRunner) counts the actions attempted and notes whether one was carried out."""
    runner = WrongPlanRunner(env, bindings)
    reached = 0
    try:
        for step in plan or []:
            runner.run([step])
            reached += 1
    except Exception as exc:                # noqa: BLE001  (any exception means the plan did not run fully)
        return runner, reached, exc
    return runner, reached, None


def short_error(record, n=200):
    """"<error_class>: <error>" of a wrong-plan record, cut to n characters."""
    text = "{}: {}".format(record.get("error_class", "error"), record.get("error", ""))
    return text if len(text) <= n else text[:n] + "..."


def wrong_plan_record(env, item, bindings):
    """Run one wrong plan in its own environment and return its record: name, ran (fully, no exception), the
    not-evidence message when it did not, fails, the failure labels ("labels"), their classes ("reasons"),
    goal_fraction, refusals, steps_run / steps_total and the legacy "why"."""
    plan = item.get("plan") or []
    record = {"name": item.get("name", "wrong")}
    runner, reached, exc = run_wrong_plan(env, plan, bindings)
    record["ran"] = exc is None
    if exc is not None:
        record["not_evidence"] = ("stopped at top-level step {} of {} ({}): a wrong plan that raises never performs "
                                  "its mistake, so it is not evidence; fix the plan".format(
                                      reached + 1, len(plan), type(exc).__name__))
        record["error_class"] = type(exc).__name__
        record["error"] = str(exc)
        if not isinstance(exc, (PlanError, SpecError)):
            # skill or kernel code raised something that is neither a refusal nor a spec problem: a harness error
            record["harness_error"] = True
            record["trace"] = traceback.format_exception(type(exc), exc, exc.__traceback__)[-4:]
    record["steps_run"] = reached
    record["steps_total"] = len(plan)
    record["refusals"] = len(runner.failures)
    if runner.failures:
        record["refused"] = [str(f)[:160] for f in runner.failures[:WRONG_REFUSALS_KEPT]]
        record["refused_categories"] = sorted({category or "refused" for _, category, _, _ in runner.refusals})
    malformed = [(action, message) for action, _, message, bad in runner.refusals if bad]
    if malformed and exc is None:
        # phase-1 red team (interim, VERIFY_HARDENING 14.3 rule 1 is the full rule): a step refused for its form -- an
        # unknown action, a parameter the action does not take, a missing or mistyped argument -- never reached the
        # robot, so the plan never performed what its name says; like a plan that raises, it is not evidence
        record["ran"] = False
        record["error_class"] = "MalformedStep"
        record["error"] = "{}: {}".format(malformed[0][0], malformed[0][1])
        record["not_evidence"] = ("{} step(s) refused for their form ({} {!r}): the robot never attempted them, so the "
                                  "plan is not evidence; fix the plan".format(len(malformed), malformed[0][0],
                                                                              malformed[0][1][:120]))
    record["attempted"] = getattr(runner, "attempted", None)
    record["acted"] = getattr(runner, "acted", None)
    blocked = [category for _, category, _, _ in runner.refusals if category in STRICT_BLOCKS]
    if exc is None and not malformed and runner.refusals and record["attempted"] and record["acted"] is False and \
            not blocked and not item.get("mistake"):
        # phase-1 red team, second pass (interim; VERIFY_HARDENING 14.3 rule 1 is the full rule): every action the
        # plan attempted was refused (or changed nothing), so the robot never performed the mistake its name
        # describes -- the plan ends like the idle check, which verification already runs; it is not evidence
        record["ran"] = False
        record["error_class"] = "NeverActed"
        record["error"] = "{} of {} attempted action(s) refused, none carried out: {}".format(
            len(runner.refusals), record["attempted"], str(runner.failures[0])[:160] if runner.failures else "")
        record["not_evidence"] = ("the robot carried out none of the {} action(s) it attempted ({} refused): the plan "
                                  "never performs its mistake, so it is not evidence; fix the plan".format(
                                      record["attempted"], len(runner.refusals)))
    try:
        outcome = env.evaluate()
    except Exception as exc:                # noqa: BLE001  (recorded: the plan cannot be shown to fail)
        record["fails"] = False
        record["evaluate_error"] = "{}: {}".format(type(exc).__name__, exc)
        return record
    failures = outcome.get("failures") or []
    record["fails"] = not outcome["success"]
    record["why"] = failures[:5] if failures else "goal_fraction={}".format(outcome["goal_fraction"])
    labels = sorted({str(label) for label in failures})
    record["labels"] = labels[:WRONG_LABELS_MAX]
    if len(labels) > WRONG_LABELS_MAX:
        record["labels_cut"] = len(labels) - WRONG_LABELS_MAX
    record["reasons"] = failure_reasons(outcome)
    record["goal_fraction"] = outcome.get("goal_fraction")
    return record


def _early_exit(env, task):
    """Schema-3 idle checks stop once success is impossible (TIER1_SWEEP 6.7)."""
    schema = task.get("schema", 2)
    return isinstance(schema, int) and schema >= 3 and env.evaluate().get("decided") == "fail"


MAX_WAIT_S = 604800         # the wait action's own limit (a week)


def _wait_out(env, task, seconds, max_steps=30):
    """Wait `seconds` in at most max_steps waits: hour-long ones as before, longer ones when the budget is over
    max_steps hours (a multi-day budget is waited out to its end, not cut at 30 h)."""
    remaining, steps = float(seconds), 0
    size = min(MAX_WAIT_S, max(3600, int(math.ceil(float(seconds) / max_steps))))
    while remaining > 0 and steps < max_steps:
        chunk = int(min(size, remaining))
        if chunk < 1:
            break
        env.step("wait", seconds=chunk)
        remaining -= chunk
        steps += 1
        if _early_exit(env, task):
            break


def idle(env, task):
    """Wait out the whole time budget (each phase's budget, then declare it done, for multi-phase tasks): the task
    must not be satisfied."""
    phases = task.get("phases") or []
    if phases:
        default = float(task.get("time_budget_s", 7200)) / max(1, len(phases))
        for _ in range(len(phases) + 1):
            active = env._phases.active()
            if active is None or _early_exit(env, task):
                break
            _wait_out(env, task, float((active.get("budget") or {}).get("time_s", default)))
            current = env._phases.active()
            if current is not None and current["id"] == active["id"]:
                env.phase_done()        # its budget did not end it: declare it done
        return env.evaluate()
    _wait_out(env, task, float(task.get("time_budget_s", 7200)))
    return env.evaluate()


def temporal_lint(env, task):
    """(errors, warnings) of the temporal keys on the initial state: a never predicate without an "after" window that
    already holds is an error (it is violated before the robot acts); a milestone that already holds is a warning (it
    is reached at the first sample: deadlines and orders on it are met before anything happens)."""
    state = env.world.s
    errors, warnings = [], []
    for scope, prefix in [(task, "")] + [(p, p.get("id", "?") + "/") for p in task.get("phases") or []]:
        for i, raw in enumerate(scope.get("never") or []):
            pred, after = (raw["pred"], raw.get("after")) if isinstance(raw, dict) and "pred" in raw else (raw, None)
            if after is None and not scope.get("id") and check(state, pred):
                label = raw.get("label", i) if isinstance(raw, dict) else i
                errors.append("never:{}{} holds in the initial state".format(prefix, label))
        for name, raw in sorted((scope.get("milestones") or {}).items()):
            pred = raw["pred"] if isinstance(raw, dict) and "pred" in raw else raw
            if check(state, pred):
                warnings.append("milestone {} holds in the initial state".format(name))
    return errors, warnings


def run_trial(spec, seed, perception=None, camera=None, gen=None, capture=None, mode=None):
    """perception: None -> each spec's own mode ("perception" field, default oracle); "realistic" forces it.
    camera: None -> each spec's own camera ("camera" field, default room); "room" | "geometric" forces it.
    gen: None -> the spec's house generation; 1 | 2 forces it (CLI --gen).
    capture: a dict that receives the run's environments ("idle", "reference", "wrong": [one per wrong plan]) for a
    caller that digests them (tools/orchestration/tier1_gate.py); None keeps nothing.
    mode: a verification mode of VERIFY_HARDENING 15.1 ("own", "eval" / "eval-geom", "eval-room") instead of
    perception / camera. A schema-4 spec runs the hardened core trial of that mode (mode_trials on this one seed: the
    allowances measured on this seed alone); any other spec the legacy trial below, unchanged."""
    if mode is not None:
        perception, camera = mode_settings(mode)
    if is_hardened(spec):
        hard_mode = mode if mode is not None else mode_of(perception, camera)
        if hard_mode is None:
            hard_mode = OWN if perception != "realistic" else (EVAL_ROOM if camera == "room" else EVAL_GEOM)
        if hard_mode == "eval":
            hard_mode = EVAL_GEOM
        envs = {} if capture is not None else None
        out = mode_trials(spec, [seed], hard_mode, gen=gen, attribution=hard_mode == EVAL_GEOM, capture=envs)
        trial = out["trials"][0]
        if capture is not None:
            got = envs.get(seed) or {}
            capture.update(idle=got.get("idle"), reference=got.get("reference"), wrong=list(got.get("wrong") or []))
        return trial
    trial = {"seed": seed, "passed": False}
    if capture is not None:
        capture.update(idle=None, reference=None, wrong=[])
    try:
        config, task, oracle = build(spec, seed, camera=camera, perception=perception, gen=gen)
        mode = perception or task.get("perception") or "oracle"
        trial["perception"] = mode
        trial["camera"] = camera_mod.resolve_mode(camera, task, mode)
        trial["variant"] = oracle["variant"]
        # the physics lint after setup (tasks/setup.py physics_problems): an error for "schema": 3 specs (build raised),
        # warnings for older ones
        lint = oracle["bindings"].get("_physics_warnings")
        if lint:
            trial["physics_warnings"] = list(lint)[:20]
        trial["initial_hash"] = digest(config)
        trial["task_hash"] = digest(task)
        env = make_env(config, task, seed, perception=mode, camera=camera)
        initial = env.evaluate()
        trial["initial_fails"] = not initial["success"]
        if task.get("phases"):
            first = task["phases"][0]
            satisfied = bool(first.get("success")) and all(check(env.world.s, rule) for rule in first["success"])
            trial["initial_fails"] = trial["initial_fails"] and not satisfied
        errors, warnings = temporal_lint(env, task)
        if warnings:
            trial["temporal_warnings"] = warnings[:20]
        if errors:
            raise SpecError("temporal lint: " + "; ".join(errors[:5]))
        if any(r.get("gesture_dropped") for r in env.world.s.get("requests") or ()):
            trial["gesture_dropped"] = True
        idle_env = make_env(config, task, seed, perception=mode, camera=camera)
        trial["idle_fails"] = not idle(idle_env, task)["success"]
        if capture is not None:
            capture["idle"] = idle_env
        ref = make_env(config, task, seed, perception=mode, camera=camera)
        if capture is not None:
            capture["reference"] = ref
        if ref.realism is not None:
            ref.realism.record_observations = True
            # the episode's first observation (made at reset, before recording was on) is what an agent sees first:
            # it counts as observed (phase 1, the people domain's request; observing changes no state)
            ref.observe()
        try:
            runner = run_plan(ref, oracle["reference"], oracle["bindings"], strict=True)
            if runner.privileged_waits:
                trial["privileged_waits"] = runner.privileged_waits
            result = ref.evaluate()
            trial["reference_passes"] = result["success"]
            trial["reference_steps"] = result["steps"]
            trial["reference_time_s"] = result["time_s"]
            if not result["success"]:
                trial["reference_failures"] = result["failures"]
                trial["reference_goal_fraction"] = result["goal_fraction"]
        except PlanError as exc:
            trial["reference_passes"] = False
            trial["reference_error"] = str(exc)
        trial["reference_state_hash"] = digest(ref.world.s)
        schema = task.get("schema", 2)
        if isinstance(schema, int) and schema >= 3:
            lint = dialogue_lint(ref, task, oracle)
            if lint:
                trial["dialogue_lint"] = lint[:20]
                trial["reference_passes"] = False
        if ref.realism is not None and trial.get("reference_passes"):
            missing = perceivability_gaps(ref, task) + report_perceivability_gaps(ref, task)
            trial["perceivable"] = not missing
            if missing:
                trial["reference_passes"] = False
                trial["perceivability_gaps"] = missing
        wrongs = []
        for item in oracle["wrong"]:
            bad = make_env(config, task, seed, perception=mode, camera=camera)
            if capture is not None:
                capture["wrong"].append(bad)
            wrongs.append(wrong_plan_record(bad, item, oracle["bindings"]))
        trial["wrong"] = wrongs
        trial["wrong_fails"] = bool(wrongs) and all(w["fails"] for w in wrongs)
        trial["wrong_ran"] = bool(wrongs) and all(w.get("ran") for w in wrongs)
        not_run = ["wrong plan {!r}: {} ({})".format(w["name"], w["not_evidence"], short_error(w))
                   for w in wrongs if not w.get("ran")]
        if not_run:
            trial["wrong_not_run"] = not_run
        trial["passed"] = all(trial.get(k) for k in ("initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran"))
    except Exception as exc:  # recorded as evidence of failure, never swallowed silently
        trial["error"] = "{}: {}".format(type(exc).__name__, exc)
        trial["trace"] = traceback.format_exc().splitlines()[-6:]
    return trial


def perceivability_gaps(env, task):
    """Realistic specs: every value a success predicate compares a report against must have appeared in what the
    reference actually observed (observations and action messages, handles translated back)."""
    from ..kernel.predicates import MISSING, get_path
    realism = env.realism
    seen_numbers, seen_strings = [], set()

    def walk(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            seen_numbers.append(float(value))
        elif isinstance(value, str):
            seen_strings.add(value)
            for token in value.replace('"', " ").replace(",", " ").replace(":", " ").split():
                seen_strings.add(token.strip("{}[]()'."))
                try:
                    seen_numbers.append(float(token.strip("{}[]()'.,")))
                except ValueError:
                    pass
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(k)
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    for item in realism.observed:
        item = realism.from_handles(item)
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            try:
                walk(json.loads(item["message"]))
            except ValueError:
                pass
        walk(item)
    gaps = []
    state = env.world.s

    def check_rule(rule):
        if not isinstance(rule, dict):
            return
        for key in ("all", "any"):
            for sub in rule.get(key, []) or []:
                check_rule(sub)
        if "not" in rule:
            return
        path = rule.get("path", "")
        if not isinstance(path, str) or not path.startswith("reports.") or "._to." in path or path.endswith("._to"):
            return      # recipients are chosen by the robot (it knows its residents), not perceived
        if "ref" in rule:
            expected = get_path(state, rule["ref"])
        else:
            expected = rule.get("value", MISSING)
        if expected is MISSING or expected is None or isinstance(expected, bool):
            return
        tol = float(rule.get("tolerance", rule.get("tol", 0.0)) or 0.0)
        if rule.get("op") == "approx" and isinstance(expected, list) and len(expected) == 2:
            expected, tol = expected[0], float(expected[1])
        if rule.get("op") == "between" and isinstance(expected, list) and len(expected) == 2:
            lo, hi = float(expected[0]), float(expected[1])
            expected, tol = (lo + hi) / 2.0, (hi - lo) / 2.0
        values = expected if isinstance(expected, list) and rule.get("op") in ("in", "subset") else [expected]
        for value in values:
            if isinstance(value, (int, float)):
                if any(abs(n - float(value)) <= max(tol, 1e-9) for n in seen_numbers):
                    return
            elif isinstance(value, str):
                if value in seen_strings:
                    return
            else:
                return
        gaps.append({"path": path, "expected": expected})
    for rule in success_rules(task):
        check_rule(rule)
    return gaps


def success_rules(task):
    """The success predicates verification checks for perceivability: the task's, then each phase's."""
    rules = list(task.get("success") or [])
    for phase in task.get("phases") or []:
        rules.extend(phase.get("success") or [])
    return rules


def _observed_fields(env):
    """{id: {field: True}} of every thing, person and pet whose field an observation of the reference run showed
    (realistic records, handles turned back into ids): evidence the robot perceived (TIER1_SWEEP 5.5)."""
    realism = env.realism
    seen = {}
    for item in realism.observed:
        if not isinstance(item, dict) or "visible_entities" not in item:
            continue
        item = realism.from_handles(item)
        for key in ("visible_entities", "people", "pets"):
            for aid, record in (item.get(key) or {}).items():
                if isinstance(record, dict):
                    seen.setdefault(aid, {}).update((field, True) for field in record)
    return seen


def report_perceivability_gaps(env, task):
    """Realistic specs, typed reports (TIER1_SWEEP 5.5; kernel/reports.py perceivability_gaps): every finding, status
    or measurement record that a report / report_set success predicate scores (report_set: its correct records) must
    rest on evidence the reference run perceived -- a field of the subject an observation showed or realism knows at
    its tier, the same field near it, or a notice of the listed kind (state["notices"]: perceived notices only).
    Reports a predicate forbids (under "not") are not evidence of anything and are skipped."""
    from ..kernel import reports
    from ..kernel.evaluation import evaluation_view
    from ..kernel.predicates import report_bounds
    view = evaluation_view(env.world.s, getattr(env, "_monitor", None), getattr(env, "mind", None))
    scored, keys = [], set()

    def collect(rule):
        if not isinstance(rule, dict) or "not" in rule:
            return
        for key in ("all", "any"):
            for sub in rule.get(key) or []:
                collect(sub)
        for key in ("report", "report_set"):
            if isinstance(rule.get(key), dict):
                spec = report_bounds(view, rule[key])
                if spec is None:
                    continue
                found = reports.matching_records(view, spec) if key == "report" else \
                    reports.matching_set_records(view, spec)
                for rec in found:
                    ident = (rec.get("n"), (rec.get("subject") or {}).get("id"))
                    if ident not in keys:
                        keys.add(ident)
                        scored.append(rec)
    for rule in success_rules(task):
        collect(rule)
    if not scored:
        return []
    notices = {n.get("kind") for n in env.world.s.get("notices") or () if isinstance(n, dict)}
    return [{"report": gap} for gap in reports.perceivability_gaps(env, scored, notices=notices,
                                                                     granted=_observed_fields(env))]


def dialogue_lint(env, task, oracle):
    """Schema-3 specs after the reference run (TIER1_SWEEP 4.11; kernel/dialogue.py lints): every fact well formed and
    reachable (an entity fact's entity perceived by the reference run, or aliases given), every questions.required
    fact (the task's and each phase's) asked with a canonical ask and answered, a wrong plan beside a required
    question, no legacy-topic ask in the reference plan. Returns problem strings."""
    from ..kernel import dialogue
    state = env.world.s
    problems = dialogue.lint_plan(oracle.get("reference"))
    problems.extend(dialogue.lint_facts(state, env, env))
    for scope in [task] + list(task.get("phases") or []):
        if scope.get("questions"):
            prefix = "{}: ".format(scope["id"]) if scope.get("id") else ""
            problems.extend(prefix + p for p in dialogue.lint_reference(
                {"questions": scope["questions"], "wrong": oracle.get("wrong")}, state))
    return problems


def verify_spec(spec, level=None, seeds=None, perception=None, camera=None, gen=None, **options):
    """Verify one spec. A schema-4 spec runs the hardened pipeline at `level` (quick | standard | full; default
    standard; VERIFY_HARDENING 5.1) and returns the evidence record v2 (verify_hardened; options: modes, heldout,
    battery, mustpass, workers, key, check_log, log_root, progress); a forced perception / camera maps to its
    verification mode. Any other spec runs the legacy trials on `seeds` (default SEEDS) and returns the legacy record,
    unchanged. (A tuple or list given as the second positional argument is the seeds, as before.)"""
    if isinstance(level, (tuple, list)):
        level, seeds = None, level
    if is_hardened(spec):
        modes = options.pop("modes", None)
        forced = mode_of(perception, camera) if (perception or camera) else None
        if forced is not None and modes is None:
            modes = [forced]
        return verify_hardened(spec, level or DEFAULT_LEVEL, seeds=list(seeds) if seeds else None, modes=modes,
                               gen=gen, **options)
    return _verify_legacy(spec, tuple(seeds) if seeds else SEEDS, perception, camera, gen)


def _verify_legacy(spec, seeds=SEEDS, perception=None, camera=None, gen=None):
    record = {"catalog_id": spec.get("catalog_id"), "spec_hash": spec_hash(spec), "trials": [], "passed": False}
    for seed in seeds:
        record["trials"].append(run_trial(spec, seed, perception, camera, gen=gen) if gen is not None
                                else run_trial(spec, seed, perception, camera))
    record["passed"] = bool(record["trials"]) and all(t["passed"] for t in record["trials"])
    record["fidelity"] = spec.get("fidelity", "S")
    return record


def spec_hash(spec):
    try:
        return digest(expand(spec))
    except SpecError:
        return digest(spec)


def _worker_init():
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    load_domains()


def _job(args):
    spec, seeds, perception, camera, gen = args
    return _verify_legacy(spec, seeds, perception, camera, gen=gen)


def _hard_job(args):
    spec, level, seeds, perception, camera, gen, options = args
    return verify_spec(spec, level, seeds=seeds, perception=perception, camera=camera, gen=gen,
                       **dict(options, workers=1))


def verify_many(specs, seeds=SEEDS, workers=None, progress=None, perception=None, camera=None, gen=None, level=None,
                **options):
    """Verify specs; results in the order given. Legacy specs run as before (one job per spec). Schema-4 specs run
    the hardened pipeline at `level` (default HOUSE_SIM_VERIFY_LEVEL, else full: the evidence level; --check passes
    standard; options: verify_hardened's): a single schema-4 spec parallelises over its
    (mode, seed) jobs with the workers (21.1), several run one spec per worker. seeds: the legacy specs' seeds; for
    schema-4 specs only when given explicitly as options["hard_seeds"] (a debugging run)."""
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    hard_seeds = options.pop("hard_seeds", None)
    level = level or os.environ.get(LEVEL_ENV) or "full"
    order = []
    legacy, hardened = [], []
    for spec in specs:
        if is_hardened(spec):
            order.append(("hard", len(hardened)))
            hardened.append(copy.deepcopy(spec))
        else:
            order.append(("legacy", len(legacy)))
            legacy.append(copy.deepcopy(spec))
    done = {"legacy": [None] * len(legacy), "hard": [None] * len(hardened)}
    count = [0]
    total = len(specs)

    def note(result):
        count[0] += 1
        if progress:
            progress(count[0], total, result)
    jobs = [(s, tuple(seeds), perception, camera, gen) for s in legacy]
    if jobs:
        if workers == 1 or len(jobs) <= 2:
            load_domains()
            for i, job in enumerate(jobs):
                done["legacy"][i] = _job(job)
                note(done["legacy"][i])
        else:
            with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
                for i, result in enumerate(pool.map(_job, jobs, chunksize=4)):
                    done["legacy"][i] = result
                    note(result)
    if len(hardened) == 1 or (hardened and workers == 1):
        load_domains()
        for i, spec in enumerate(hardened):
            done["hard"][i] = verify_spec(spec, level, seeds=hard_seeds, perception=perception, camera=camera,
                                          gen=gen, **dict(options, workers=workers))
            note(done["hard"][i])
    elif hardened:
        jobs = [(s, level, hard_seeds, perception, camera, gen, options) for s in hardened]
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), initializer=_worker_init) as pool:
            for i, result in enumerate(pool.map(_hard_job, jobs)):
                done["hard"][i] = result
                note(result)
    return [done[kind][i] for kind, i in order]


# =====================================================================================================================
# Schema 4: the hardened pipeline (docs/design/VERIFY_HARDENING.md 5, 8.1, 8.7, 9.3-9.5, 14, 15, 16, 17, 19, 21.3)

LEVELS = ("quick", "standard", "full")
DEFAULT_LEVEL = "standard"
LEVEL_ENV = "HOUSE_SIM_VERIFY_LEVEL"        # verify_many's level for schema-4 specs when none is given (default full)
OWN, EVAL_GEOM, EVAL_ROOM = "own", "eval-geom", "eval-room"
MODES = (OWN, EVAL_GEOM, EVAL_ROOM)
EVIDENCE_FORMAT = "house_ai.verification/2"
EXPECT_PREFIXES = ("goal:", "fail:", "flag:", "inv:", "house:", "timing:", "never:", "always:", "response:",
                   "deadline:", "order:", "question:", "said:", "report:", "strict:", "perceive:", "settle:", "later:")
EXPECT_EXACT = ("budget",)
QUESTION_LABELS = ("question_missing", "question_forbidden", "too_many_unnecessary_questions",
                   "missing_necessary_question", "too_many_questions")
PLAIN_PREFIXES = ("fail:", "strict:", "timing:", "never:", "always:", "response:", "deadline:", "order:", "report:",
                  "perceive:", "settle:", "phase_failed:", "phase_not_started:", "changed:", "envelope_violation:")
INFEASIBLE_SHARE = 0.2              # held-out seeds whose setup is infeasible: more is a lint error (16.4)
SKILL_DEBT_FILES = ("house_sim/tasks/skills.py", "house_sim/kernel/", "house_sim/systems/")
SKILL_DEBT_CATEGORIES = ("capability",)
SKILL_DEBT_WORDS = ("route", "blocked", "fall", "fell", "stairs", "unreachable", "no path")
STEP_ROLE_KEYS = ("target", "obj", "object", "item", "entity", "what", "tool", "to", "destination", "place", "room",
                  "person", "recipient")
_BUDGET = None


def budget_table():
    global _BUDGET
    if _BUDGET is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "verify",
                            "budget.json")
        with open(path, encoding="utf-8") as stream:
            _BUDGET = json.load(stream)
    return _BUDGET


def eval_modes():
    """The evaluation modes S3 runs (15.1): geometric, plus the room camera until the D3 switch-over makes geometric
    the realistic default."""
    if camera_mod.default_mode("realistic") == "geometric":
        return [EVAL_GEOM]
    return [EVAL_GEOM, EVAL_ROOM]


def mode_settings(mode):
    """(perception, camera) of a verification mode: own -> (None, None), the spec's own; eval / eval-geom ->
    realistic geometric; eval-room -> realistic room."""
    if mode in (None, OWN):
        return None, None
    if mode in ("eval", EVAL_GEOM):
        return "realistic", "geometric"
    if mode == EVAL_ROOM:
        return "realistic", "room"
    raise ValueError("unknown verification mode {!r} (own, eval, eval-geom, eval-room)".format(mode))


def mode_of(perception, camera):
    """The verification mode a forced perception / camera pair corresponds to (None: not one of them)."""
    if perception is None and camera is None:
        return OWN
    if perception == "realistic" and camera == "geometric":
        return EVAL_GEOM
    if perception == "realistic" and camera == "room":
        return EVAL_ROOM
    return None


def is_hardened(spec):
    """Whether a spec is verified by the hardened pipeline (it declares "schema": 4 or later)."""
    from .runner import hardened_schema
    try:
        return hardened_schema(expand(spec))
    except SpecError:
        return bool(isinstance(spec, dict) and isinstance(spec.get("schema"), int) and spec["schema"] >= 4)


# ----- labels and expect (14.2, 14.3) --------------------------------------------------------------------------

def _said_labels(task):
    """Success labels whose predicate is (or contains) a said form."""
    from ..kernel.evaluation import labelled, recorded_labels
    out = set()
    for label, rule in labelled(task.get("success"), recorded_labels(task, "success")):
        if '"said"' in json.dumps(rule):
            out.add(label)
    return out


def canonical_labels(failures, unmet=(), said=()):
    """Every form a pattern of 14.2 may match, for a run's failure labels and unmet success labels: the label itself,
    flag:<name> for a global flag, house:<guard>:<id> for a ledger label house.<guard>:<id>, goal:<label> (and
    said:<label> for a said predicate) for an unmet success label, question:<label> for a question failure,
    later:<form> of each form of a look-ahead label, inv:<label> for anything else (an invariant)."""
    from ..kernel.evaluation import GLOBAL_FLAGS
    said = set(said or ())

    def forms(label):
        label = str(label)
        out = {label}
        if label.startswith("later:"):
            out |= {"later:" + f for f in forms(label[len("later:"):])}
            return out
        if label in GLOBAL_FLAGS:
            out.add("flag:" + label)
        elif label.startswith("house."):
            guard, _, ident = label[len("house."):].partition(":")
            out.add("house:" + guard + (":" + ident if ident else ""))
        elif label.split(":", 1)[0] in QUESTION_LABELS:
            out.add("question:" + label)
        elif label == "budget" or label.startswith(PLAIN_PREFIXES):
            pass
        else:
            out.add("inv:" + label)
        return out
    canon = set()
    for label in failures or ():
        canon |= forms(label)
    for label in unmet or ():
        canon.add("goal:" + str(label))
        base = str(label)[:-len(":absent")] if str(label).endswith(":absent") else str(label)
        if base in said:
            canon.add("said:" + str(label))
    return canon


def _label_matches(pattern, label):
    """14.2's one matching rule, for every pattern form inv: included: L == p or L starts with p + ":" (an inv:
    pattern names a whole invariant label, never a raw prefix: review A-gaming-13, the verifier half)."""
    return label == pattern or label.startswith(pattern + ":")


def pattern_matches(pattern, canon):
    """Whether a 14.2 pattern matches a set of canonical labels: L == p or L starts with p + ":"."""
    return any(_label_matches(pattern, c) for c in canon)


def expect_problems(item):
    """The verifier's part of L-EXPECT (14.3 rule 4): expect is a non-empty list of known patterns; a bare goal, a
    bare house:<guard> without an id, budget without expect_budget and an inv: pattern that is not a whole
    invariant label name (lint.invariant_problem: inv:, inv:s, inv:people) are not specific."""
    expect = item.get("expect")
    if not isinstance(expect, list) or not expect:
        return ["expect is a non-empty list of failure patterns (14.1)"]
    problems = []
    for pattern in expect:
        if not isinstance(pattern, str) or not pattern:
            problems.append("expect pattern {!r} is not a string".format(pattern))
        elif pattern in ("goal", "goal:"):
            problems.append("a bare goal is not specific")
        elif pattern.startswith("house:") and len(pattern.split(":")) < 3:
            problems.append("{} names no entity, kind or resource".format(pattern))
        elif pattern == "budget" and not item.get("expect_budget"):
            problems.append("budget needs expect_budget with a reason")
        elif pattern not in EXPECT_EXACT and not pattern.startswith(EXPECT_PREFIXES):
            problems.append("unknown expect pattern {!r}".format(pattern))
        elif pattern.startswith(("inv:", "later:inv:")):
            why = _lint_module().invariant_problem(pattern.split("inv:", 1)[1])
            if why:
                problems.append("{}: {}".format(pattern, why))
    if not isinstance(item.get("mistake"), list) or not item["mistake"] or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in item["mistake"]):
        problems.append("mistake lists the plan's top-level step indices (14.1)")
    return problems


def matched_patterns(item, failures, unmet, said=()):
    """(matched patterns, the labels behind them) of a wrong plan's expect on one run."""
    canon = canonical_labels(failures, unmet, said)
    matched = [p for p in item.get("expect") or () if isinstance(p, str) and pattern_matches(p, canon)]
    behind = sorted({c for c in canon for p in matched if _label_matches(p, c)})
    return matched, behind


# ----- mistake attribution (14.4) -------------------------------------------------------------------------------

def _step_key(step):
    """(skill or action, role) of a plan step for the alignment: the first {placeholder} of its role arguments."""
    if not isinstance(step, dict):
        return ("?", None)
    name = step.get("skill") or step.get("do") or next((k for k in ("save", "wait_until", "wait_for", "phase_done",
                                                                    "foreach", "repeat") if k in step), "?")
    for key in STEP_ROLE_KEYS + tuple(sorted(step)):
        value = step.get(key)
        if key in ("skill", "do", "note", "expect", "save", "from"):
            continue
        found = VAR.findall(json.dumps(value)) if value is not None else []
        if found:
            return (str(name), found[0])
    return (str(name), None)


def _lcs_pairs(a, b):
    """Index pairs of a longest common subsequence of two key lists (deterministic: the earliest alignment)."""
    n, m = len(a), len(b)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if a[i] == b[j] else max(table[i + 1][j], table[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def default_mistake_fix(plan, mistake, reference):
    """The reference steps aligned with each block of mistake steps (14.4): the wrong plan without its mistake steps
    is aligned with the reference (longest common subsequence over (skill or action, role)); a block of consecutive
    mistake steps between the anchors p < block < q is replaced by the reference steps between their partners.
    Returns {first index of a block: [reference steps]}."""
    mistake = sorted(set(i for i in mistake or () if 0 <= i < len(plan or ())))
    rest = [i for i in range(len(plan or ())) if i not in mistake]
    pairs = _lcs_pairs([_step_key(plan[i]) for i in rest], [_step_key(s) for s in reference or ()])
    anchor = {rest[i]: j for i, j in pairs}
    blocks, current = [], []
    for i in mistake:
        if current and i != current[-1] + 1:
            blocks.append(current)
            current = []
        current.append(i)
    if current:
        blocks.append(current)
    fixes = {}
    for block in blocks:
        before = [anchor[i] for i in anchor if i < block[0]]
        after = [anchor[i] for i in anchor if i > block[-1]]
        lo = max(before) + 1 if before else 0
        hi = min(after) if after else len(reference or ())
        fixes[block[0]] = copy.deepcopy(list(reference or ())[lo:hi])
    return fixes


def attributed_plan(item, reference):
    """w' of 14.4: the wrong plan with its mistake steps replaced by mistake_fix (a list: one block at the first
    mistake step; {index: [steps]} per block), else by default_mistake_fix."""
    plan = list(item.get("plan") or [])
    mistake = sorted(set(i for i in item.get("mistake") or () if isinstance(i, int) and 0 <= i < len(plan)))
    fix = item.get("mistake_fix")
    if isinstance(fix, list):
        fixes = {mistake[0]: copy.deepcopy(fix)} if mistake else {}
    elif isinstance(fix, dict):
        fixes = {int(k): copy.deepcopy(v if isinstance(v, list) else [v]) for k, v in fix.items()}
    else:
        fixes = default_mistake_fix(plan, mistake, reference)
    out = []
    for i, step in enumerate(plan):
        if i in fixes:
            out.extend(fixes[i])
        if i in mistake:
            continue
        out.append(copy.deepcopy(step))
    return out


# ----- trace digests and helpers --------------------------------------------------------------------------------

def _trace_digest(runner):
    rows = [{k: e.get(k) for k in ("action", "target", "destination", "args", "ok", "category")}
            for e in runner.trace or ()]
    return digest(rows)


def _waited(runner):
    return round(sum(max(0.0, float(e["t1"]) - float(e["t0"])) for e in runner.trace or ()
                     if e.get("action") == "wait" and e.get("t1") is not None), 3)


def _floors_of(env, runner):
    rooms = env.world.s.get("rooms") or {}
    return sorted({(rooms.get(e.get("room")) or {}).get("floor", 0) for e in runner.trace or () if e.get("room")})


def _resource_use(env, idle):
    """{resource: robot-attributable use} at the env's state (6.8): meter now minus the idle meter at that time."""
    from ..kernel import counterfactual, ledger
    state = env.world.s
    t = float(state.get("time_s", 0.0) or 0.0)
    out = {}
    for name, meter in sorted(ledger.data("resources")["meters"].items()):
        now = ledger.meter_value(state, meter["path"])
        base = counterfactual.value_at(idle, ("meter", name), t, None) if idle is not None else None
        if base is None:
            base = ledger.meter_value(env._baseline, meter["path"])
        out[name] = round(max(0.0, now - float(base)), 6)
    return out


def _open_longest(env):
    monitor = getattr(env, "_monitor", None)
    if monitor is None:
        return {}
    now = float(env.world.s.get("time_s", 0.0) or 0.0)
    return {eid: round(monitor.longest_open(eid, now=now), 3) for eid in monitor.open_items()
            if monitor.longest_open(eid, now=now) > 0}


def _phase_figures(env, runner):
    """Per phase of a multi-phase run, at the declared-done point (before finish): {"id", "steps", "time_s",
    "waited_s", "active_s", "ended"} from the kernel's phase record (state.phase: the ended phases' history and the
    active one's start), the waits from the trace inside each phase's window (17: phased specs apply the formula per
    phase)."""
    state = env.world.s
    section = state.get("phase") or {}
    now = float(state.get("time_s", 0.0) or 0.0)
    rows = []
    for item in section.get("history") or ():
        if isinstance(item, dict) and item.get("scored", True):
            rows.append({"id": item.get("id"), "steps": int(item.get("steps") or 0),
                         "start": float(item.get("started_s") or 0.0), "end": float(item.get("ended_s") or 0.0),
                         "ended": True})
    if section.get("active") is not None:
        start = float(section.get("started_s") or 0.0)
        rows.append({"id": section.get("active"), "steps": int(state.get("steps", 0) or 0) -
                     int(section.get("started_step") or 0), "start": start, "end": now, "ended": False})
    out = []
    for row in rows:
        waited = 0.0
        for e in runner.trace or ():
            if e.get("action") != "wait" or e.get("t1") is None:
                continue
            lo, hi = max(float(e["t0"]), row["start"]), min(float(e["t1"]), row["end"])
            waited += max(0.0, hi - lo)
        time_s = round(max(0.0, row["end"] - row["start"]), 3)
        out.append({"id": row["id"], "steps": row["steps"], "time_s": time_s, "waited_s": round(waited, 3),
                    "active_s": round(max(0.0, time_s - waited), 3), "ended": row["ended"]})
    return out


def operated_appliances(env, runner):
    """The restore-class appliances (fridge, freezer, oven, dishwasher; budget.json lookahead.appliances) a run opened
    or operated (6.10's look-ahead trigger, decided from what the run did, not from the plan's text): the robot wrote
    one of the appliance's own fields (env.writes, bookkeeping aside: its door, power, setting), or a command the
    robot carried out opened or closed it or put something into or onto it (the trace's target / destination).
    Sorted ids."""
    wanted = set(budget_table()["lookahead"]["appliances"])
    entities = env.world.s.get("entities") or {}
    appliances = sorted(eid for eid, record in entities.items() if isinstance(record, dict) and
                        ({record.get("type"), record.get("category")} | set(record.get("is_a") or ())) & wanted)
    if not appliances:
        return []
    out = set()
    robot_wrote = getattr(getattr(env, "writes", None), "robot_wrote", None)
    if callable(robot_wrote):
        for eid in appliances:
            try:
                if robot_wrote(eid):
                    out.add(eid)
            except Exception:               # noqa: BLE001  (the trace below still decides)
                pass
    chosen = set(appliances)
    for entry in runner.trace or ():
        if not entry.get("ok"):
            continue
        for value in (entry.get("destination"), entry.get("target") if entry.get("action") in ("open", "close")
                      else None):
            for eid in value if isinstance(value, list) else [value]:
                if isinstance(eid, str) and eid in chosen:
                    out.add(eid)
    return sorted(out)


def _skill_debt(exc_trace, refusals, category=None):
    """Whether a held-out reference failure is skill debt (16.3): the innermost project frame of the exception is
    in the skills, the kernel or a domain's code, or a refusal is of category capability or a navigation or physics
    failure. category: the PlanError's own category (WP11, set on the PlanErrors skills.py raises itself): "route"
    (a walk with no way through) is skill debt charged to tasks/skills.py; "argument" (a caller bug) is a spec failure.
    Returns the file it is charged to, else None (a spec failure)."""
    if category == "route":
        return "house_sim/tasks/skills.py (route)"
    if category == "argument":
        return None
    for line in reversed(exc_trace or ()):
        text = str(line).replace("\\", "/")
        for prefix in SKILL_DEBT_FILES:
            index = text.find(prefix)
            if index >= 0:
                path = text[index:].split('"')[0].split(",")[0]
                return path
    for category, message in refusals or ():
        lowered = str(message).lower()
        if category in SKILL_DEBT_CATEGORIES or any(word in lowered for word in SKILL_DEBT_WORDS):
            return "house_sim/systems (refusal: {})".format(str(message)[:80])
    return None


def lookahead_trigger(analysis, spec_text=""):
    """Whether the look-ahead is triggered by the analysis record (6.10): its protect or wrong text matches the
    trigger expression."""
    table = budget_table()["lookahead"]
    text = " ".join(str(x) for key in ("protect", "wrong") for x in ((analysis or {}).get(key) or ()))
    return re.search(table["trigger"], text, flags=re.IGNORECASE) is not None


def operates_restore_appliance(plan, bindings, config):
    """Whether a plan opens or operates a restore-class appliance (fridge, freezer, oven, dishwasher; 6.10): any id
    its steps name (bindings substituted) is such an appliance."""
    table = budget_table()["lookahead"]
    entities = config.get("entities") or {}
    wanted = set(table["appliances"])
    for value in VAR.findall(json.dumps(plan or [])):
        bound = bindings.get(value)
        for eid in bound if isinstance(bound, list) else [bound]:
            record = entities.get(eid) if isinstance(eid, str) else None
            if not record:
                continue
            names = {record.get("type"), record.get("category")} | set(record.get("is_a") or ())
            if names & wanted:
                return True
    for eid in re.findall(r'"([a-z][a-z0-9_]*_\d+)"', json.dumps(plan or [])):
        record = entities.get(eid)
        if record and ({record.get("type"), record.get("category")} | set(record.get("is_a") or ())) & wanted:
            return True
    return False


def _analysis_record(catalog_id):
    """The analysis record of a catalog id (catalog/analysis/part_*.jsonl), or None."""
    global _ANALYSIS
    if _ANALYSIS is None:
        table = {}
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "catalog", "analysis")
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
            if not (name.startswith("part_") and name.endswith(".jsonl")):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("id"):
                        table.setdefault(row["id"], row)
        _ANALYSIS = table
    return _ANALYSIS.get(catalog_id)


_ANALYSIS = None
_ENTRIES = None


def _entry(catalog_id):
    """The catalog entry (tasks/specs.jsonl: text, kind, section) of a catalog id, or {}."""
    global _ENTRIES
    if _ENTRIES is None:
        table = {}
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tasks",
                            "specs.jsonl")
        try:
            with open(path, encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("catalog_id"):
                        table[row["catalog_id"]] = {k: row.get(k) for k in ("text", "kind", "section")}
        except OSError:
            pass
        _ENTRIES = table
    return _ENTRIES.get(catalog_id) or {}


# ----- runs ------------------------------------------------------------------------------------------------------

def _hard_env(config, task, seed, mode, idle=None, record=True):
    perception, camera = mode_settings(mode)
    env = make_env(config, task, seed, perception=perception or task.get("perception"), camera=camera)
    env.idle = idle
    if record and env.realism is not None:
        from . import perceive
        perceive.enable(env)
    return env


def _confirm_sample(env):
    """A B1 sample that succeeds is confirmed with a settle from a savepoint (8.7): the env is saved, a copy loaded
    and finished; True when the copy still succeeds."""
    import tempfile
    from ..kernel.engine import HouseEnv
    folder = tempfile.mkdtemp(prefix="house_b1_")
    path = os.path.join(folder, "sample.json")
    try:
        env.save(path)
        copy_env = HouseEnv.load(path)
        copy_env.idle = env.idle
        return bool(copy_env.finish().get("success"))
    finally:
        try:
            os.remove(path)
            os.rmdir(folder)
        except OSError:
            pass


def idle_rollout(env, task, horizon_s=None):
    """The schema-4 idle run (8.7, 6.2): the robot does nothing for the whole budget plus settle plus look-ahead
    (no early exit). The idle counterfactual's projections (kernel/counterfactual.py project) are taken at the start,
    every counterfactual.interval_for(budget) and at the end; success is evaluated at every k x max(60 s, budget / 48)
    within the budget (a multi-phase task declares each phase done at its budget's end, as the legacy idle does), and
    a sample that succeeds is confirmed with a settle from a savepoint. Returns (Projections, {"fails", "samples",
    "projections", "passes_at", "unconfirmed"})."""
    from ..kernel import counterfactual
    block = task.get("hardening") or {}
    budget = float(task.get("time_budget_s", 7200))
    horizon = float(horizon_s) if horizon_s is not None else budget + float(block.get("settle_s", 60.0)) + float(
        block.get("lookahead_s", 0.0))
    table = budget_table()["idle"]
    sample_every = max(float(table["sample_min_s"]), budget / float(table["samples_per_budget"]))
    interval = counterfactual.interval_for(budget)
    state = env.world.s
    start = float(state.get("time_s", 0.0) or 0.0)
    end = start + horizon
    projections = counterfactual.Projections(meta={"start_s": start, "horizon_s": horizon, "interval_s": interval})
    projections.add(counterfactual.project(env.world.s))
    env.idle = projections                  # the ledger of a sample compares with what is known so far
    phases = task.get("phases") or []
    phase_ends = []
    if phases:
        default = budget / max(1, len(phases))
        t = start
        for phase in phases:
            t += float((phase.get("budget") or {}).get("time_s", default))
            phase_ends.append(t)
    next_proj, next_sample = start + interval, start + sample_every
    samples, passes, unconfirmed = 0, [], []
    while True:
        now = float(env.world.s.get("time_s", 0.0) or 0.0)
        if now >= end - 1e-9:
            break
        due = [next_proj, end] + ([next_sample] if next_sample <= start + budget + 1e-9 else []) + \
            [t for t in phase_ends if t > now + 1e-9]
        target = min(due)
        chunk = min(3600.0, target - now)
        before = now
        if chunk > 1e-9:
            env.step("wait", seconds=max(1, int(round(chunk))) if chunk >= 1 else 1)
        now = float(env.world.s.get("time_s", 0.0) or 0.0)
        if now <= before + 1e-9:
            raise RuntimeError("an idle wait did not advance the clock at t={}".format(before))
        for t in [t for t in phase_ends if t <= now + 1e-9]:
            phase_ends.remove(t)
            active = env._phases.active() if phases else None
            if active is not None:
                env.phase_done()
        if now >= next_proj - 1e-9 and now < end - 1e-9:
            projections.add(counterfactual.project(env.world.s))
            while next_proj <= now + 1e-9:
                next_proj += interval
        if next_sample <= start + budget + 1e-9 and now >= next_sample - 1e-9:
            samples += 1
            if env.evaluate().get("success"):
                if _confirm_sample(env):
                    passes.append(round(now - start, 3))
                else:
                    unconfirmed.append(round(now - start, 3))
            while next_sample <= now + 1e-9:
                next_sample += sample_every
    if projections.times[-1] < float(env.world.s.get("time_s", 0.0) or 0.0) - 1e-9:
        projections.add(counterfactual.project(env.world.s))
    env.idle = projections
    return projections, {"fails": not passes, "samples": samples, "projections": len(projections),
                         "passes_at": passes[:10], "unconfirmed": unconfirmed[:10], "horizon_s": horizon,
                         "digest": projections.digest()}


def _untriggered_timing(env, task):
    """The timing items (8.5) whose trigger never held during a finished run, with their if_no_trigger rule: on such
    a seed the relation is vacuous unless the item requires the trigger (WP3's known gap; timing_vacuity turns it
    into a lint finding)."""
    monitor = getattr(env, "_monitor", None)
    if not task.get("timing") or monitor is None:
        return []
    # WP3's Monitor.timing_verdicts (landed): an exception is the harness's (G7), never "no vacuous item" (P4)
    verdicts = monitor.timing_verdicts(env.world.s) or {}
    rules = {str(item.get("label")): item.get("if_no_trigger", "forbid") for item in task.get("timing") or ()
             if isinstance(item, dict)}
    return [{"label": label, "if_no_trigger": rules.get(label, "forbid")}
            for label, verdict in sorted(verdicts.items()) if isinstance(verdict, dict) and
            verdict.get("t_trigger") is None]


def timing_vacuity(mode_outs):
    """L-TIMING's dynamic part for WP3's vacuous-trigger gap: a timing item whose trigger never held during a
    reference run that ended before the seed's drawn event time (a finite time answer later than the run's end) was
    never evaluated: an error unless the item requires the trigger (if_no_trigger "require" fails without one). An
    untriggered item on a seed without a drawn time is a warning (a never_p seed is legitimate, 9.2)."""
    out = []
    for o in mode_outs:
        for trial in o.get("trials") or ():
            ref = trial.get("reference") or {}
            if not ref.get("timing_untriggered"):
                continue
            answers = ((o.get("references") or {}).get(trial.get("seed")) or {}).get("answers") or {}
            times = [float(a["value"]) for a in answers.values() if a.get("kind") == "time" and
                     isinstance(a.get("value"), (int, float)) and not isinstance(a.get("value"), bool)]
            end = float(ref.get("end_time_s") or ref.get("time_s") or 0.0)
            for item in ref["timing_untriggered"]:
                if item.get("if_no_trigger") == "require":
                    continue
                late = [t for t in times if t > end]
                severity = "error" if late else "warning"
                out.append({"id": "L-TIMING", "severity": severity, "message": (
                    "seed {} {}: timing {!r} never triggered{}".format(
                        trial.get("seed"), o.get("mode"), item["label"],
                        " -- the reference ended at {:.0f} s, before the drawn event at {:.0f} s, so the relation "
                        "was never evaluated (run through the event or use if_no_trigger: require)".format(
                            end, min(late)) if late else ""))})
    seen, unique = set(), []
    for row in out:
        if row["message"] not in seen:
            seen.add(row["message"])
            unique.append(row)
    return unique


def _perceive_answers(task):
    return (task.get("hardening") or {}).get("answers") or {}


def _finish(env, task, mode, branch_diff=None):
    """End a run (8.1): HouseEnv.finish, plus the agent rule's perception labels in the evaluation mode (15.3)."""
    result = env.finish()
    if env.realism is not None and mode != OWN:
        from . import perceive
        labels = perceive.agent_labels(env, task, _perceive_answers(task), branch_diff=branch_diff)
        if labels:
            result["failures"] = list(result.get("failures") or []) + [l for l in labels
                                                                       if l not in (result.get("failures") or [])]
            result["success"] = False
    return result


def reference_run(config, task, oracle, seed, mode, idle, branch_diff=None, plan=None, snapshot=None):
    """The strict reference (S2, S3, S7): no refused step, finish (settle, look-ahead), inside its envelope (no
    house.* or later: label: envelope_violation:<label>), perceivable and gated in the evaluation mode (15.2, 15.3).
    Its own resource use and open intervals are measured and its house.waste / house.held_open labels re-judged
    against them (the allowance is defined by the reference itself, 6.8, 6.9). Returns (record, env, runner)."""
    from ..kernel import ledger
    from .runner import Runner
    env = _hard_env(config, task, seed, mode, idle)
    plan = oracle["reference"] if plan is None else plan
    runner = Runner(env, oracle["bindings"], strict=True, snapshot=snapshot)
    rec = {"passes": False}
    try:
        runner.run_top(plan)
    except PlanError as exc:
        rec["error"] = str(exc)[:300]
        rec["refusals"] = [[c, m[:160]] for _, c, m, _ in runner.refusals[:3]]
        # WP11: a PlanError skills.py raises itself says why ("route": no way through, skill debt on held-out
        # seeds; "argument": a caller bug, the spec's failure; 16.3); a refused step's carries none
        category = getattr(exc, "category", None)
        if category:
            rec["error_category"] = str(category)
    except SpecError as exc:
        # a plan step the skill cannot take (an argument it does not take, an unknown skill): the spec's error, not a
        # harness error (14.5), as for a wrong plan
        rec["error"] = "SpecError: {}".format(exc)[:300]
        rec["spec_error"] = True
        rec["refusals"] = [[c, m[:160]] for _, c, m, _ in runner.refusals[:3]]
    except Exception as exc:                # noqa: BLE001  (a harness error, G7)
        rec["error"] = "{}: {}".format(type(exc).__name__, exc)[:300]
        rec["harness_error"] = True
        rec["trace"] = traceback.format_exc().splitlines()[-6:]
    if runner.privileged_waits:
        rec["privileged_waits"] = runner.privileged_waits
    operated = operated_appliances(env, runner)
    if operated:
        rec["operates_appliances"] = operated
    if task.get("phases"):
        rec["phases"] = _phase_figures(env, runner)
    result = None
    if "error" not in rec:
        try:
            result = env.finish()
        except Exception as exc:            # noqa: BLE001
            rec["error"] = "finish: {}: {}".format(type(exc).__name__, exc)[:300]
            rec["harness_error"] = True
            rec["trace"] = traceback.format_exc().splitlines()[-6:]
    resources = _resource_use(env, idle)
    opened = _open_longest(env)
    rec.update({"steps": env.world.s.get("steps", 0), "resources": resources, "open_s": opened,
                "trace_digest": _trace_digest(runner), "waited_s": _waited(runner),
                "floors": _floors_of(env, runner), "rooms": sorted({e["room"] for e in runner.trace or ()
                                                                    if e.get("room")})})
    if result is None:
        rec["time_s"] = env.world.s.get("time_s", 0.0)
        rec["active_s"] = round(float(rec["time_s"]) - rec["waited_s"], 3)
        return rec, env, runner
    rec["steps"] = result.get("steps")
    rec["time_s"] = result.get("time_s")
    rec["end_time_s"] = result.get("end_time_s")
    rec["active_s"] = round(float(result.get("time_s") or 0.0) - rec["waited_s"], 3)
    untriggered = _untriggered_timing(env, task)
    if untriggered:
        rec["timing_untriggered"] = untriggered
    failures = list(result.get("failures") or [])
    # the reference defines the allowances it is judged by: waste and hold limits re-judged with its own values
    judged = [f for f in failures if f.split(":", 1)[0] in ("house.waste", "house.held_open") or
              f.startswith(("later:house.waste", "later:house.held_open"))]
    if judged:
        block = env._task.setdefault("hardening", {})
        block["reference_resources"] = dict(resources)
        block["reference_open_s"] = dict(opened)
        again = set(ledger.failures(env._task, env.world.s, env._baseline, env.world, idle))
        keep = []
        for label in failures:
            base = label[len("later:"):] if label.startswith("later:") else label
            if label in judged and base not in again:
                continue
            keep.append(label)
        failures = keep
    violations = ["envelope_violation:" + f for f in failures if f.startswith(("house.", "later:"))]
    goal_d = float(result.get("goal_fraction") or 0.0) >= 1.0 - 1e-9 and not (result.get("unmet") or [])
    rec.update({"success": bool(goal_d and not failures), "failures": failures,
                "unmet": list(result.get("unmet") or []), "goal_fraction": result.get("goal_fraction"),
                "consumed": result.get("consumed") or [],
                "settle_ok": not (result.get("settle") or {}).get("regressions"),
                "lookahead_s": (result.get("lookahead") or {}).get("s", 0.0), "envelope_ok": not violations})
    if violations:
        rec["envelope_violations"] = violations
    core = rec["success"]
    acts = []
    if env.realism is not None and mode != OWN:
        from . import perceive
        answers = _perceive_answers(task)
        gaps = perceive.report_gaps(env, task, answers)
        acts = perceive.act_gaps(env, task, answers, branch_diff=branch_diff)
        rec["perceivable"] = not gaps
        rec["gated_ok"] = not acts
        if gaps:
            rec["perceivability_gaps"] = gaps[:10]
        if acts:
            rec["gating_gaps"] = acts[:10]
        core = core and not gaps
    schema = task.get("schema", 2)
    if isinstance(schema, int) and schema >= 3:
        lint = dialogue_lint(env, task, oracle)
        if lint:
            rec["dialogue_lint"] = lint[:20]
            core = False
    rec["passes_core"] = bool(core)         # everything but the gating, which a branch diff set may refine
    rec["passes"] = bool(core and not acts)
    return rec, env, runner


class StrictWrongRunner(WrongPlanRunner):
    """The lenient runner of a schema-4 wrong plan (14.3 rule 1 is applied on its record)."""


def wrong_run(config, task, oracle, seed, mode, item, idle, branch_diff=None, plan=None):
    """Run one wrong plan (or its attribution plan w') on one seed and mode and record what happened, independent
    of its expect (judge_wrong applies 14.3 to the record, so the run cache can re-judge it): whether it ran fully
    (no exception, every top-level step reached), the top-level steps refused, the full failure list after settle
    (and the look-ahead) with the agent rule's perception labels in the evaluation mode, the unmet labels, the goal
    fraction."""
    from .runner import capture_run
    env = _hard_env(config, task, seed, mode, idle)
    plan = list(item.get("plan") or []) if plan is None else plan
    runner = StrictWrongRunner(env, oracle["bindings"])
    runner, reached, exc = capture_run(env, plan, oracle["bindings"], runner=runner)
    rec = {"name": item.get("name", "wrong"), "raised": exc is not None, "steps_run": reached,
           "steps_total": len(plan), "plan_digest": digest(plan)}
    if exc is not None:
        rec["error_class"] = type(exc).__name__
        rec["error"] = str(exc)[:300]
        rec["not_evidence"] = "stopped at top-level step {} of {} ({}): a plan that raises is never evidence".format(
            reached + 1, len(plan), type(exc).__name__)
        if not isinstance(exc, (PlanError, SpecError)):
            rec["harness_error"] = True
            rec["trace"] = traceback.format_exception(type(exc), exc, exc.__traceback__)[-4:]
    rec["refused"] = sorted({k for k in runner.refusal_tops if k is not None})
    # the refusal categories of each refused top-level step (judge_wrong: a mistake step refused for anything but a
    # strict block the plan expects never carried its mistake out)
    categories = {}
    for (_, category, _, _), k in zip(runner.refusals, runner.refusal_tops):
        if k is not None:
            categories.setdefault(str(k), set()).add(str(category or ""))
    rec["refused_categories"] = {k: sorted(v) for k, v in sorted(categories.items())}
    if runner.refusals:
        rec["refusals"] = [[c, m[:160]] for _, c, m, _ in runner.refusals[:WRONG_REFUSALS_KEPT]]
    malformed = [(a, m) for a, _, m, bad in runner.refusals if bad]
    if malformed:
        rec["malformed"] = "{}: {}".format(malformed[0][0], malformed[0][1])[:300]
    try:
        result = _finish(env, task, mode, branch_diff)
    except Exception as exc2:               # noqa: BLE001
        rec["fails"] = False
        rec["harness_error"] = True
        rec["evaluate_error"] = "{}: {}".format(type(exc2).__name__, exc2)[:300]
        return rec, env
    rec["fails"] = not result.get("success")
    rec["failures"] = sorted({str(label) for label in result.get("failures") or []})
    rec["unmet"] = list(result.get("unmet") or [])
    rec["goal_fraction"] = result.get("goal_fraction")
    return rec, env


def _unrealised_mistakes(rec, item):
    """The mistake steps of a wrong-plan record that the house refused for anything but a strict block the plan
    expects (P4; REVIEW_REPORT: 'wrong plans that never perform their mistake'): a precondition, argument or capability
    refusal means the named mistake never happened, so the plan tests nothing. A strict-block refusal (safety,
    privacy, social, authority) stands in for its mistake only when expect names it (a strict:<category> pattern:
    the house stopping the harm is then the expected outcome). Sorted top-level indices."""
    allowed = set(item.get("mistake") or ())
    expect = [p for p in item.get("expect") or () if isinstance(p, str)]
    categories = rec.get("refused_categories") or {}
    out = []
    for k in sorted(set(rec.get("refused") or ()) & allowed):
        cats = categories.get(str(k)) or [""]
        stands_in = all(c in STRICT_BLOCKS and any(("strict:" + c) == p or ("strict:" + c).startswith(p + ":")
                                                   for p in expect) for c in cats)
        if not stands_in:
            out.append(k)
    return out


def judge_wrong(rec, item, said=()):
    """Apply 14.3 to a wrong-plan record (wrong_run) for the plan's current expect: 1. ran fully -- no exception,
    every top-level step reached, a refused step only in mistake (or any, when expect has a strict: pattern), none
    refused for its form, and every mistake step carried out (a mistake step refused for anything but a strict block
    the plan expects is unrealised: _unrealised_mistakes); 2. fails; 3. for its reason -- an expect pattern matches a
    failure or unmet label; 4. specific (expect_problems); 5. not on the budget alone unless expect_budget. With an
    attribution run recorded (rec["attribution"]: w''s failures and unmet labels), attributed = none of the labels that
    matched expect appears for w' (14.4). Sets ran, matched, matched_labels, for_reason, expect_problems, attributed,
    ok; returns rec."""
    for key in ("matched", "matched_labels", "for_reason", "expect_problems", "attributed", "ok", "unrealised"):
        rec.pop(key, None)
    if not rec.get("raised") and rec.get("error_class") in ("RefusedOutsideMistake", "MalformedStep",
                                                            "UnrealisedMistake"):
        for key in ("error_class", "error", "not_evidence"):
            rec.pop(key, None)
    ran = not rec.get("raised") and rec.get("steps_run") == rec.get("steps_total")
    allowed = set(item.get("mistake") or ())
    strict_expect = any(isinstance(p, str) and p.startswith("strict:") for p in item.get("expect") or ())
    outside = [k for k in rec.get("refused") or () if k not in allowed]
    if ran and outside and not strict_expect:
        ran = False
        rec["error_class"] = "RefusedOutsideMistake"
        rec["error"] = "top-level step(s) {} refused outside mistake {}".format(outside, sorted(allowed))
        rec["not_evidence"] = "a refused step outside the plan's mistake: the plan did not run as written (14.3)"
    unrealised = _unrealised_mistakes(rec, item)
    if ran and unrealised:
        ran = False
        rec["unrealised"] = unrealised
        rec["error_class"] = "UnrealisedMistake"
        rec["error"] = "mistake step(s) {} refused ({}): the named mistake never happened".format(
            unrealised, "; ".join(sorted({c for k in unrealised for c in (rec.get("refused_categories") or {}).get(
                str(k)) or ["?"]})))
        rec["not_evidence"] = "a mistake step the house refused (not a strict block the plan expects) never " \
                              "happened: the plan tests nothing (14.3 rule 1, P4)"
    if ran and rec.get("malformed"):
        ran = False
        rec["error_class"] = "MalformedStep"
        rec["error"] = rec["malformed"]
        rec["not_evidence"] = "a step refused for its form never reached the robot"
    rec["ran"] = ran
    problems = expect_problems(item)
    if problems:
        rec["expect_problems"] = problems
    matched, behind = matched_patterns(item, rec.get("failures"), rec.get("unmet"), said)
    rec["matched"] = matched
    rec["matched_labels"] = behind
    budget_only = bool(behind) and set(behind) <= {"budget"} and not item.get("expect_budget")
    rec["for_reason"] = bool(matched) and not budget_only
    rec["ok"] = bool(ran and rec.get("fails") and rec["for_reason"] and not problems)
    attribution = rec.get("attribution")
    if isinstance(attribution, dict):
        canon = canonical_labels(attribution.get("failures"), attribution.get("unmet"), said)
        leaked = sorted(set(behind) & canon)
        attribution["leaked"] = leaked
        rec["attributed"] = bool(rec["ok"]) and not leaked
    return rec


def _applies(item, variant, mode):
    """Whether a wrong plan applies to a seed's variant and a mode (14.1 variants, modes; default all, both)."""
    variants = item.get("variants")
    if isinstance(variants, list) and variant is not None and variant not in variants:
        return False
    modes = item.get("modes")
    if isinstance(modes, list):
        wanted = "own" if mode == OWN else "eval"
        return wanted in modes
    return True


def _canon_command(entry, bindings):
    """(action, role-mapped canonical arguments) of a trace record (10.3 B8): ids mapped to the binding names that
    hold them, numbers and words literally."""
    reverse = {}
    for name, value in sorted(bindings.items()):
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str):
                reverse.setdefault(v, "{" + name + "}")
    def canon(value):
        if isinstance(value, str):
            return reverse.get(value, value)
        if isinstance(value, list):
            return [canon(v) for v in value]
        if isinstance(value, dict):
            return {k: canon(v) for k, v in sorted(value.items())}
        return value
    args = dict(entry.get("args") or {})
    args["target"] = entry.get("target")
    args["destination"] = entry.get("destination")
    return json.dumps([entry.get("action"), canon(args)], sort_keys=True)


def branch_diff_sets(traces):
    """The branch diff set of 10.3 B8 / 15.3: [(seed, branch, canonical commands)] -> {seed: [commands of the seed's
    own trace whose canonical form is in some branch's references and not in every branch's]}."""
    by_branch = {}
    for seed, branch, forms, _ in traces:
        by_branch.setdefault(json.dumps(branch), set()).update(forms)
    if len(by_branch) < 2:
        return {seed: [] for seed, _, _, _ in traces}
    union = set().union(*by_branch.values())
    common = set.intersection(*by_branch.values())
    diff = union - common
    out = {}
    for seed, _, forms, commands in traces:
        out[seed] = [cmd for form, cmd in zip(forms, commands) if form in diff]
    return out


def _attribution_ok(trials):
    return all(t.get("attributed") is not False for t in trials)


def envelope_classes(spec, oracle, env):
    """The envelope.change selectors of a seed's variant (as the spec writes them) that select a never-touch thing
    or a restore-class item at the baseline (ledger.Ledger.never_touch / restore_class): the escapes of 18 they
    cost ({"never_touch_changes", "restore_changes"})."""
    from ..kernel import ledger
    from .runner import _substitute_keyed
    from .spec import select_variant
    full = expand(spec)
    chosen = full
    if oracle.get("variant") is not None and full.get("variants"):
        chosen, _ = select_variant(full, int(oracle["variant"]))
    envelope = chosen.get("envelope") or {}
    table = envelope.get("change") or {}
    out = {"never_touch_changes": [], "restore_changes": [], "valuable_selectors": []}
    if env is None:
        return out
    # consume / deliver selectors that name a valuable (6.6: free for a target, an opt-out's price otherwise)
    for key in ("consume", "deliver"):
        entries = envelope.get(key)
        selectors = entries if isinstance(entries, list) else list((entries or {}).keys())
        for selector in selectors:
            try:
                built = list(_substitute_keyed({selector: True}, oracle["bindings"]))[0]
            except (SpecError, TypeError):
                continue
            records = env._baseline.get("entities") or {}
            if any(ledger.is_valuable(env._baseline, records.get(eid) or {})
                   for eid in ledger.select(env._baseline, built)) and selector not in out["valuable_selectors"]:
                out["valuable_selectors"].append(selector)
    if not isinstance(table, dict):
        return out
    try:
        book = ledger.Ledger(env._task, env.world.s, env._baseline, env.world, env.idle)
    except Exception:                       # noqa: BLE001  (no ledger: nothing classified, nothing excused)
        return out
    baseline = env._baseline.get("entities") or {}
    for selector in sorted(table):
        try:
            built = list(_substitute_keyed({selector: True}, oracle["bindings"]))[0]
        except SpecError:
            continue
        value = table[selector]
        fields = set(value) if isinstance(value, list) else set(value.get("fields") or ()) \
            if isinstance(value, dict) else set()
        for eid in sorted(ledger.select(env._baseline, built)):
            record = baseline.get(eid) or {}
            barred = book.never_touch(eid, record)
            heads = {str(f).split(".")[0] for f in fields}
            hits = barred is not None and (barred == ledger.ALL_FIELDS or not fields or heads & set(barred))
            if hits and selector not in out["never_touch_changes"]:
                out["never_touch_changes"].append(selector)
            if book.restore_class(record) and selector not in out["restore_changes"]:
                out["restore_changes"].append(selector)
    return out


def _trial_verdict(trial):
    """A core trial passes when the initial state fails, the idle run fails at every sample, the reference passes
    and every applicable wrong plan is verified (judge_wrong)."""
    wrongs = trial.get("wrong") or []
    trial["wrong_ok"] = all(w.get("ok") for w in wrongs)
    trial["passed"] = bool(trial.get("initial_fails") and trial.get("idle_fails") and trial.get("reference_passes")
                           and trial["wrong_ok"])
    return trial


def rejudge(spec, out):
    """Re-apply the expect-dependent rules (judge_wrong) to a mode job's stored trials for the spec's current wrong
    plans (matched by index and name; the run cache's keys leave expect out, 22.8)."""
    from .spec import select_variant
    full = expand(spec)
    for trial in out.get("trials") or ():
        if not trial.get("wrong"):
            continue
        chosen = full
        if trial.get("variant") is not None and full.get("variants"):
            chosen, _ = select_variant(full, int(trial["variant"]))
        items = chosen.get("wrong") or []
        said = trial.get("said_labels") or ()
        for rec in trial["wrong"]:
            index = rec.get("index")
            same = isinstance(index, int) and 0 <= index < len(items) and isinstance(items[index], dict) and \
                items[index].get("name", "wrong") == rec.get("name")
            if same:
                judge_wrong(rec, items[index], said)
        _trial_verdict(trial)
    return out


def mode_trials(spec, seeds, mode, gen=None, extras=None, attribution=False, baselines=False, trim=False,
                lookahead=None, capture=None):
    """The core trials of one mode over seeds (S2 own, S3 eval, S7 held-out): per seed build, initial fails, the idle
    rollout (B1 at every sample, the counterfactual projections), the strict reference with its trace, then -- once
    every reference ran, so the allowances (reference_resources, reference_open_s: the references' largest use, or
    extras when given) and the branch diff set are known -- every applicable wrong plan (and its attribution run w'
    with attribution=True, 14.4). baselines: the survey and search baselines (17); trim: the minimal-reference trim
    (17). lookahead: the triggered look-ahead in seconds (None: the spec's own). Returns {"trials": [...],
    "references": {seed: data for the budget and the battery}, "extras": the allowances used}."""
    from .runner import build
    perception, camera = mode_settings(mode)
    out = {"mode": mode, "trials": [], "references": {}}
    built = {}
    said = set()
    for seed in seeds:
        trial = {"seed": seed, "mode": mode, "passed": False}
        try:
            override = {"lookahead_s": float(lookahead)} if lookahead is not None else None
            config, task, oracle = build(spec, seed, camera=camera, perception=perception, gen=gen,
                                         hardening=override)
            trial.update({"variant": oracle["variant"], "branch": oracle.get("branch"),
                          "perception": perception or task.get("perception") or "oracle",
                          "camera": camera_mod.resolve_mode(camera, task, perception or task.get("perception")),
                          "initial_hash": digest(config), "task_hash": digest(task)})
            said |= _said_labels(task)
            env0 = _hard_env(config, task, seed, mode, record=False)
            initial = env0.evaluate()
            trial["initial_fails"] = not initial["success"]
            if task.get("phases"):
                first = task["phases"][0]
                trial["initial_fails"] = trial["initial_fails"] and not (
                    bool(first.get("success")) and all(check(env0.world.s, rule) for rule in first["success"]))
            errors, warnings = temporal_lint(env0, task)
            if warnings:
                trial["temporal_warnings"] = warnings[:20]
            if errors:
                raise SpecError("temporal lint: " + "; ".join(errors[:5]))
            idle_env = _hard_env(config, task, seed, mode, record=False)
            projections, idle_rec = idle_rollout(idle_env, task)
            trial["idle"] = idle_rec
            trial["idle_fails"] = idle_rec["fails"]
            if capture is not None:
                capture.setdefault(seed, {})["idle"] = idle_env
            ref, ref_env, runner = reference_run(config, task, oracle, seed, mode, projections)
            trial["reference"] = ref
            built[seed] = (config, task, oracle, projections, ref_env, runner)
            if capture is not None:
                capture[seed]["reference"] = ref_env
        except Exception as exc:            # noqa: BLE001  (recorded as evidence of failure)
            trial["error"] = "{}: {}".format(type(exc).__name__, exc)[:400]
            trial["trace"] = traceback.format_exc().splitlines()[-6:]
            trial["infeasible"] = isinstance(exc, SpecError)
            if not isinstance(exc, SpecError):
                trial["harness_error"] = True
        out["trials"].append(trial)
    if extras is None:
        resources, opened = {}, {}
        for seed, (_, _, _, _, _, _) in built.items():
            ref = next(t["reference"] for t in out["trials"] if t["seed"] == seed)
            for name, value in (ref.get("resources") or {}).items():
                resources[name] = max(resources.get(name, 0.0), float(value))
            for eid, value in (ref.get("open_s") or {}).items():
                opened[eid] = max(opened.get(eid, 0.0), float(value))
        extras = {"reference_resources": resources, "reference_open_s": opened}
    out["extras"] = extras
    traces = []
    for seed, (config, task, oracle, projections, ref_env, runner) in built.items():
        forms = [_canon_command(e, oracle["bindings"]) for e in runner.trace or () if e.get("ok")]
        commands = [{"action": e["action"], "args": dict(e.get("args") or {}, target=e.get("target"),
                                                         destination=e.get("destination"))}
                    for e in runner.trace or () if e.get("ok")]
        traces.append((seed, oracle.get("branch"), forms, commands))
    diffs = branch_diff_sets(traces)
    for trial in out["trials"]:
        seed = trial["seed"]
        if seed not in built:
            continue
        config, task, oracle, projections, ref_env, runner = built[seed]
        if any(a.get("kind") == "branch" for a in _perceive_answers(task).values()) and ref_env.realism is not None \
                and mode != OWN:
            # the branch answer's decisive commands are the branch diff set (15.3), known once every reference ran
            from . import perceive
            acts = perceive.act_gaps(ref_env, task, _perceive_answers(task), branch_diff=diffs.get(seed))
            ref = trial["reference"]
            ref["gated_ok"] = not acts
            ref.pop("gating_gaps", None)
            if acts:
                ref["gating_gaps"] = acts[:10]
            ref["passes"] = bool(ref.get("passes_core")) and not acts
        task = copy.deepcopy(task)
        task["hardening"].update(copy.deepcopy(extras))
        out["references"][seed] = {"variant": oracle["variant"], "branch": oracle.get("branch"),
                                   "bindings": oracle["bindings"], "answers": oracle.get("answers") or {},
                                   "trace": runner.trace, "tops": runner.tops,
                                   "saves": runner.saves, "waits": runner.waits, "reference": trial["reference"],
                                   "idle": projections.to_dict() if capture is not None else None,
                                   "idle_digest": projections.digest(),
                                   "envelope_classes": envelope_classes(spec, oracle, ref_env)}
        wrongs = []
        for index, item in enumerate(oracle["wrong"]):
            if not isinstance(item, dict) or not _applies(item, oracle["variant"], mode):
                continue
            rec, bad_env = wrong_run(config, task, oracle, seed, mode, item, projections, diffs.get(seed))
            rec["index"] = index
            if capture is not None:
                capture[seed].setdefault("wrong", []).append(bad_env)
            if attribution and not rec.get("raised") and rec.get("fails"):
                # w' (14.4), in the evaluation mode only; run whatever the expect says, so a re-judge needs no run
                fixed = attributed_plan(item, oracle["reference"])
                again, _ = wrong_run(config, task, oracle, seed, mode, item, projections, diffs.get(seed),
                                     plan=fixed)
                rec["attribution"] = {"raised": again.get("raised"), "fails": again.get("fails"),
                                      "failures": again.get("failures") or [], "unmet": again.get("unmet") or [],
                                      "plan_digest": again.get("plan_digest")}
                if again.get("harness_error"):
                    rec["attribution"]["harness_error"] = True
            wrongs.append(judge_wrong(rec, item, said))
        trial["wrong"] = wrongs
        trial["said_labels"] = sorted(said)
        ref = trial["reference"]
        trial["reference_passes"] = bool(ref.get("passes"))
        _trial_verdict(trial)
        if baselines and ref.get("passes"):
            trial["survey"] = survey_baseline(config, task, oracle, seed, mode, ref)
            if any(a.get("kind") == "place" for a in _perceive_answers(task).values()):
                trial["search"] = search_baseline(config, task, oracle, seed, mode)
    if trim:
        out["trims"] = minimal_reference(spec, built, out["trials"], mode)
    return out


def _baseline_run(config, task, oracle, seed, mode, skill_args):
    from .runner import Runner
    env = _hard_env(config, task, seed, mode, record=False)
    runner = Runner(env, oracle["bindings"], strict=False)
    t0, s0 = float(env.world.s.get("time_s", 0.0) or 0.0), env.world.s.get("steps", 0)
    harness = False
    try:
        out = runner.skill("survey", **skill_args)
        error = None
    except (PlanError, SpecError) as exc:   # a baseline that cannot run allows nothing (and is no harness error)
        out, error = None, "{}: {}".format(type(exc).__name__, exc)[:200]
    except Exception as exc:                # noqa: BLE001  (an exception inside the skill or kernel: G7, 14.5)
        out, error = None, "{}: {}".format(type(exc).__name__, exc)[:200]
        harness = True
    rec = {"s": round(float(env.world.s.get("time_s", 0.0) or 0.0) - t0, 3),
           "steps": env.world.s.get("steps", 0) - s0}
    if harness:
        rec["harness_error"] = True
    if out:
        rec["rooms"] = out.get("rooms")
        if out.get("places"):
            rec["places"] = out["places"]
        if out.get("skipped"):
            rec["skipped"] = dict(list(sorted(out["skipped"].items()))[:6])
    if error:
        rec["error"] = error
    return rec


def survey_baseline(config, task, oracle, seed, mode, ref):
    """The canonical survey (17): from the start pose, go_room to every work room and every room the reference
    visited, with a look around in each (skills.survey)."""
    from ..kernel import ledger
    work = []
    for selector in ((task.get("hardening") or {}).get("envelope") or {}).get("work_rooms") or ():
        for rid in ledger.ids_of(selector):
            work.append(rid[5:] if rid.startswith("room:") else rid)
    rooms = [r for r in dict.fromkeys(work + list(ref.get("rooms") or ())) if r in (config.get("rooms") or {})]
    return _baseline_run(config, task, oracle, seed, mode, {"rooms": rooms})


def search_baseline(config, task, oracle, seed, mode):
    """The non-privileged search baseline of a place answer (17, critique hole 19): every candidate place of the
    answer's group that exists, nearest first, opened and inspected, without stopping at the item."""
    places = []
    for body in _perceive_answers(task).values():
        if body.get("kind") == "place":
            group = body.get("group")
            places.extend(group if isinstance(group, list) else [group] if group else [])
    return _baseline_run(config, task, oracle, seed, mode, {"places": [p for p in places if isinstance(p, str)]})


def _wait_trims(plan):
    """The candidate wait trims of a plan, longest first: (index, key, seconds) of top-level waits (a wait's seconds,
    a conditioned wait's max_s)."""
    out = []
    for i, step in enumerate(plan or ()):
        if not isinstance(step, dict):
            continue
        if step.get("do") == "wait" and isinstance(step.get("seconds"), (int, float)):
            out.append((i, "seconds", float(step["seconds"])))
        elif ("wait_until" in step or "wait_for" in step) and isinstance(step.get("max_s"), (int, float)):
            out.append((i, "max_s", float(step["max_s"])))
    return sorted(out, key=lambda t: (-t[2], t[0]))


def _tier_of(seconds):
    for tier in budget_table()["tiers"]:
        if tier["max_s"] is None or seconds <= tier["max_s"]:
            return tier
    return budget_table()["tiers"][-1]


FIGURE_KEYS = ("steps", "time_s", "waited_s", "active_s", "phases")    # what the budget formula reads


def minimal_reference(spec, built, trials, mode):
    """The minimal-reference trim of 17: on the first seed of each variant, trims tried one at a time -- each wait
    halved (longest first, up to 3 times), then each top-level step dropped -- a trim kept when the trimmed plan
    still passes strictly (ledger, settle, look-ahead, perceivability, gating); at most the tier's trim count per
    variant (waits only for H3). The trimmed plan is confirmed on every seed of V of that variant, falling back to
    the last trim that passes on all of them. Returns {variant: {"plan_digest", "trims", "dropped_steps",
    "per_seed": {seed: {"steps", "time_s", "waited_s", "active_s"}}}}."""
    table = budget_table()
    by_variant = {}
    for trial in trials:
        if trial.get("seed") in built and (trial.get("reference") or {}).get("passes"):
            by_variant.setdefault(trial.get("variant"), []).append(trial["seed"])
    out = {}
    for variant, seeds in sorted(by_variant.items(), key=lambda kv: str(kv[0])):
        first = seeds[0]
        config, task, oracle, projections, _, _ = built[first]
        ref = next(t["reference"] for t in trials if t["seed"] == first)
        tier = _tier_of(formula_budget([ref], [{}])["time_s"])
        limit = int(tier["trims"])
        plan = copy.deepcopy(oracle["reference"])
        kept, tried, dropped = [], 0, []
        halvings = {}

        def passes(candidate, cfg=config, tsk=task, orc=oracle, seed=first, idle=projections):
            rec, _, _ = reference_run(cfg, tsk, orc, seed, mode, idle, plan=candidate)
            return rec
        for _ in range(int(table["trim"]["wait_halvings"])):
            for index, key, seconds in _wait_trims(plan):
                if tried >= limit:
                    break
                if halvings.get(index, 0) >= int(table["trim"]["wait_halvings"]) or seconds < 2 * float(
                        table["trim"]["min_wait_s"]):
                    continue
                candidate = copy.deepcopy(plan)
                candidate[index][key] = max(int(table["trim"]["min_wait_s"]), int(seconds // 2))
                tried += 1
                if passes(candidate).get("passes"):
                    plan = candidate
                    halvings[index] = halvings.get(index, 0) + 1
                    kept.append({"wait": index, key: candidate[index][key]})
        if not tier.get("trims_waits_only"):
            index = 0
            while index < len(plan) and tried < limit:
                if len(plan) <= 1:
                    break
                candidate = plan[:index] + plan[index + 1:]
                tried += 1
                if passes(candidate).get("passes"):
                    dropped.append(index + len(dropped))
                    kept.append({"drop": index})
                    plan = candidate
                    continue
                index += 1
        per_seed, final = {}, plan
        history = [copy.deepcopy(oracle["reference"])]
        confirm = seeds if tier.get("trim_confirm") == "V" else seeds[:1]
        for seed in confirm:
            cfg, tsk, orc, idle, _, _ = built[seed]
            rec, _, _ = reference_run(cfg, tsk, orc, seed, mode, idle, plan=final)
            if not rec.get("passes"):
                final = history[-1]
                per_seed = {}
                break
            per_seed[seed] = {k: rec.get(k) for k in FIGURE_KEYS if k in rec}
        if not per_seed:
            for seed in confirm:
                rec = next(t["reference"] for t in trials if t["seed"] == seed)
                per_seed[seed] = {k: rec.get(k) for k in FIGURE_KEYS if k in rec}
            kept, dropped = [], []
        out[str(variant)] = {"plan_digest": digest(final), "trims": kept, "tried": tried,
                             "dropped_steps": dropped, "per_seed": per_seed, "tier_for_trims": tier["id"]}
    return out


def formula_budget(references, explores):
    """The budget formula of 17 over reference records ({"steps", "waited_s", "active_s"}) and explore allowances
    ({"steps", "s"}: the larger of survey and search per seed): {"steps", "time_s"} (steps rounded up to 10, time to
    60 s)."""
    f = budget_table()["formula"]
    steps = max([int(r.get("steps") or 0) for r in references] or [0])
    waited = max([float(r.get("waited_s") or 0.0) for r in references] or [0.0])
    active = max([float(r.get("active_s") or 0.0) for r in references] or [0.0])
    ex_steps = max([int(e.get("steps") or 0) for e in explores] or [0])
    ex_s = max([float(e.get("s") or 0.0) for e in explores] or [0.0])
    step_budget = f["step_factor"] * steps + ex_steps + f["step_slack"]
    time_budget = f["active_factor"] * active + f["wait_factor"] * waited + ex_s + f["time_slack_s"]
    r_steps, r_time = int(f["round_steps"]), int(f["round_time_s"])
    return {"steps": int(math.ceil(step_budget / float(r_steps)) * r_steps),
            "time_s": int(math.ceil(time_budget / float(r_time)) * r_time)}


def _phase_budget_rows(chosen, data, f):
    """17 per phase of a multi-phase variant: {phase id: {"formula", "authored", "ok", "share_ok", "measured"}} --
    the formula over the references' figures for that phase (reference_run's "phases"; a phase no reference
    measured takes the whole run's figures, an upper bound of any one phase) with the variant's explore allowance
    (an agent looks around again in each phase), against the phase's own budget. A phase without a budget of its own
    has no phase limit in the kernel (authored None, ok): the episode budget bounds it."""
    rows = {}
    for phase in chosen.get("phases") or ():
        if not isinstance(phase, dict) or phase.get("id") is None:
            continue
        pid = str(phase["id"])
        refs, uses = [], []
        for ref in data["refs"]:
            figures = next((p for p in ref.get("phases") or () if str(p.get("id")) == pid), None)
            refs.append(figures if figures is not None else ref)
        for ref in data["uses"]:
            figures = next((p for p in ref.get("phases") or () if str(p.get("id")) == pid), None)
            if figures is not None:
                uses.append(figures)
        measured = any(any(str(p.get("id")) == pid for p in ref.get("phases") or ()) for ref in data["refs"])
        formula = formula_budget(refs, data["explores"]) if refs else None
        own = phase.get("budget") if isinstance(phase.get("budget"), dict) else None
        row = {"formula": formula, "authored": None, "ok": formula is not None, "share_ok": True,
               "measured": measured}
        if own and formula is not None:
            steps = int(own.get("steps", formula["steps"]))
            time_s = float(own.get("time_s", formula["time_s"]))
            row["authored"] = {"steps": steps, "time_s": time_s}
            row["ok"] = steps <= formula["steps"] and time_s <= formula["time_s"]
            row["share_ok"] = all(int(u.get("steps") or 0) <= f["reference_share"] * steps + 1e-9 and
                                  float(u.get("time_s") or 0.0) <= f["reference_share"] * time_s + 1e-9
                                  for u in uses)
        rows[pid] = row
    return rows


def budget_check(spec, mode_out, heldout_trials=()):
    """The budget of 17 per variant: the formula over the minimal reference on V (eval mode; explore steps and seconds
    each the larger of the survey and the search), the budget the run is given (runner.episode_budget) against it,
    and the reference's share of the budget on every seed of V u H (at most 80 %); a phased variant also per phase
    (rows "<v> phase <id>", _phase_budget_rows) and, when every phase was measured, the episode against its phases'
    formulas plus the gaps. Returns {"variants": {v: {"formula", "authored", "ok", "share_ok"}}, "formula_ok", "tier",
    "steps_ratio", "time_ratio"}."""
    from .runner import episode_budget
    f = budget_table()["formula"]
    full = expand(spec)
    trims = mode_out.get("trims") or {}
    per_variant = {}
    for trial in mode_out["trials"]:
        ref = trial.get("reference") or {}
        if not ref.get("passes"):
            continue
        v = str(trial.get("variant"))
        # explore_v = max(survey_v, search_v), steps and seconds separately (17): a search with more steps but
        # fewer seconds than the survey keeps its steps
        runs = [trial.get("survey") or {}, trial.get("search") or {}]
        explore = {"steps": max(int(e.get("steps") or 0) for e in runs),
                   "s": max(float(e.get("s") or 0.0) for e in runs)}
        trimmed = ((trims.get(v) or {}).get("per_seed") or {}).get(trial["seed"]) or ref
        per_variant.setdefault(v, {"refs": [], "explores": [], "uses": []})
        per_variant[v]["refs"].append(trimmed)
        per_variant[v]["explores"].append(explore)
        per_variant[v]["uses"].append(ref)
    for trial in heldout_trials or ():
        ref = trial.get("reference") or {}
        if ref.get("passes"):
            per_variant.setdefault(str(trial.get("variant")), {"refs": [], "explores": [], "uses": []})[
                "uses"].append(ref)
    out = {"variants": {}, "formula_ok": bool(per_variant), "trims": trims}
    longest = 0
    ratios = []
    for v, data in sorted(per_variant.items()):
        formula = formula_budget(data["refs"], data["explores"]) if data["refs"] else None
        variants = full.get("variants") or []
        chosen = copy.deepcopy(full)
        if v not in ("None",) and variants:
            from .spec import select_variant
            chosen, _ = select_variant(full, int(v))
        # the budget the run is given (runner.build: a phased spec without a top-level budget runs with the sum of
        # its phases' budgets and gaps), never a default the run does not use
        authored = episode_budget(chosen)
        phase_rows = _phase_budget_rows(chosen, data, f) if chosen.get("phases") else {}
        gaps = sum(float((p.get("start") or {}).get("gap_s", 0.0) or 0.0) for p in chosen.get("phases") or ()
                   if isinstance(p, dict))
        if phase_rows and formula is not None and all(r["measured"] and r["formula"] for r in phase_rows.values()):
            # a phased episode: its phases' formulas plus the gaps between them (the gaps are idle advances, not
            # the robot's active time the whole-run formula would triple)
            r_time = int(f["round_time_s"])
            formula = {"steps": sum(r["formula"]["steps"] for r in phase_rows.values()),
                       "time_s": int(math.ceil((sum(r["formula"]["time_s"] for r in phase_rows.values()) + gaps) /
                                               float(r_time)) * r_time)}
        ok = formula is not None and int(authored["steps"]) <= formula["steps"] and \
            float(authored["time_s"]) <= formula["time_s"]
        # the reference's share of the budget, the gaps (idle advances both include) left out
        share_ok = all(int(u.get("steps") or 0) <= f["reference_share"] * int(authored["steps"]) + 1e-9 and
                       max(0.0, float(u.get("time_s") or 0.0) - gaps) <= f["reference_share"] * max(
                           0.0, float(authored["time_s"]) - gaps) + 1e-9
                       for u in data["uses"])
        out["variants"][v] = {"formula": formula, "authored": {"steps": int(authored["steps"]),
                                                               "time_s": float(authored["time_s"])},
                              "ok": ok, "share_ok": share_ok}
        out["formula_ok"] = out["formula_ok"] and ok and share_ok
        # 17: phased specs apply the formula per phase (rows "<variant> phase <id>", read by L-BUDGET like a variant)
        for pid, row in sorted(phase_rows.items()):
            out["variants"]["{} phase {}".format(v, pid)] = row
            out["formula_ok"] = out["formula_ok"] and row["ok"] and row["share_ok"]
        if formula:
            longest = max(longest, formula["time_s"])
            ratios.append((float(authored["steps"]) / max(1, max(int(r.get("steps") or 0) for r in data["refs"])),
                           float(authored["time_s"]) / max(1.0, max(float(r.get("time_s") or 0.0)
                                                                    for r in data["refs"]))))
    out["tier"] = _tier_of(longest)["id"] if per_variant else None
    out["formula_time_s"] = longest
    if ratios:
        out["steps_ratio"] = round(max(r[0] for r in ratios), 2)
        out["time_ratio"] = round(max(r[1] for r in ratios), 2)
    return out


def variation(spec, mode_out):
    """The variation requirements of 9.3 over V (the audit-build shares are WP6's audits): number answers by
    coefficient of variation and range, word / entity / place / branch answers by distinct values each on >= 2
    seeds, set answers by >= 3 distinct sets, time answers by spread. Returns {answer: {"kind", "values", "ok",
    "why"}}."""
    from .seeds import _key
    values, kinds, bodies = {}, {}, {}
    for seed, ref in sorted(mode_out.get("references", {}).items()):
        for name, body in (ref.get("answers") or {}).items():
            if "value" not in body:
                continue
            values.setdefault(name, []).append(body["value"])
            kinds[name] = body.get("kind")
            bodies[name] = body
    out = {}
    for name in sorted(values):
        kind, vals = kinds[name], values[name]
        ok, why = True, None
        if kind == "number":
            nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            mean = sum(nums) / len(nums) if nums else 0.0
            sd = math.sqrt(sum((x - mean) ** 2 for x in nums) / (len(nums) - 1)) if len(nums) > 1 else 0.0
            cv = sd / abs(mean) if mean else 0.0
            tol = float(bodies[name].get("tol", 0.0) or 0.0)
            span = (max(nums) - min(nums)) if nums else 0.0
            ok = cv >= float(bodies[name].get("min_cv", 0.15)) and span >= 3 * tol
            why = None if ok else "cv {:.3f} / range {:.3g} (need {} and {:.3g})".format(
                cv, span, bodies[name].get("min_cv", 0.15), 3 * tol)
        elif kind in ("word", "entity", "place", "branch"):
            counts = {}
            for v in vals:
                counts[_key(v)] = counts.get(_key(v), 0) + 1
            ok = len(counts) >= 2 and sum(1 for c in counts.values() if c >= 2) >= 2
            why = None if ok else "{} distinct value(s), {} on 2+ seeds".format(
                len(counts), sum(1 for c in counts.values() if c >= 2))
        elif kind == "set":
            distinct = {_key(v) for v in vals}
            ok = len(distinct) >= 3
            why = None if ok else "{} distinct set(s) (need 3)".format(len(distinct))
        elif kind == "time":
            finite = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            spread = (max(finite) - min(finite)) if finite else 0.0
            need = float(bodies[name].get("spread_s", 0.0) or 0.0)
            ok = spread >= need
            why = None if ok else "spread {:.0f} s (need {:.0f})".format(spread, need)
        out[name] = {"kind": kind, "values": [v if not isinstance(v, float) else round(v, 4) for v in vals],
                     "ok": ok}
        if why:
            out[name]["why"] = why
    return out


# ----- protect liveness (7.5, 7.6; the verifier's part until WP8's lint and WP6's probes land) -------------------

def _normalise_item(text):
    return re.sub(r"[^a-z0-9 ]+", "", str(text).lower()).strip()


def _ref_forms(ref, bindings):
    """The canonical label prefix a protect reference resolves to (7.3) on a seed, bindings substituted; None for
    flag: and the forms whose liveness is not a failure label (preserve:, question:, success:)."""
    ref = str(ref).split("@", 1)[0]
    try:
        ref = substitute(ref, bindings)
    except SpecError:
        return None
    if ref.startswith("flag:") or ref.startswith(("preserve:", "success:")):
        return None
    return ref


def protect_record(spec, analysis, mode_outs):
    """Protect items against the spec's protect_map (7.2, 7.5 rule 1) and their liveness from the verified wrong plans
    (7.6 source a; battery runs and probes are WP6's sources b and c): {"items", "mapped", "live", "waived",
    "unmapped", "duplicated", "unlive", "risk_unlive", "lint"}."""
    items = [i for i in (analysis or {}).get("protect") or () if isinstance(i, str)]
    mapping = [m for m in spec.get("protect_map") or () if isinstance(m, dict)]
    by_text = {}
    for m in mapping:
        by_text.setdefault(_normalise_item(m.get("item", "")), []).append(m)
    unmapped = [i for i in items if _normalise_item(i) not in by_text]
    duplicated = [i for i in items if len(by_text.get(_normalise_item(i), ())) > 1]
    waived = [i for i in items if any(m.get("waive") for m in by_text.get(_normalise_item(i), ()))]
    runs = []
    for out in mode_outs:
        for trial in out.get("trials") or ():
            bindings = ((out.get("references") or {}).get(trial.get("seed")) or {}).get("bindings") or {}
            for w in trial.get("wrong") or ():
                if w.get("ok"):
                    runs.append((bindings, canonical_labels(w.get("failures"), w.get("unmet"))))
    live, unlive = [], []
    for item in items:
        maps = by_text.get(_normalise_item(item)) or []
        refs = [r for m in maps for r in (m.get("by") or ()) if isinstance(r, str)]
        if not maps or any(m.get("waive") for m in maps):
            continue
        checkable = [r for r in refs if not r.startswith("flag:")]
        if not checkable:
            live.append(item)       # flag-only items are not checked for liveness (7.6)
            continue
        hit = False
        for bindings, canon in runs:
            for ref in checkable:
                form = _ref_forms(ref, bindings)
                if form and pattern_matches(form, canon):
                    hit = True
                    break
            if hit:
                break
        (live if hit else unlive).append(item)
    lint = []
    if unmapped:
        lint.append("L-PROTECT: {} protect item(s) unmapped: {}".format(len(unmapped), "; ".join(unmapped[:4])))
    if duplicated:
        lint.append("L-PROTECT: protect item(s) mapped more than once: {}".format("; ".join(duplicated[:4])))
    risk = (analysis or {}).get("risk") or 0
    return {"items": len(items), "mapped": len(items) - len(unmapped), "live": len(live), "waived": len(waived),
            "unmapped": unmapped, "duplicated": duplicated, "unlive": unlive,
            "risk_unlive": bool(unlive) and isinstance(risk, (int, float)) and risk >= 2, "lint": lint,
            "sources": "wrong plans (battery and probes: WP6)"}


# ----- static checks of S0 ---------------------------------------------------------------------------------------

def _lint_module():
    """WP8's tasks/lint.py (landed: imported directly, so a broken lint module fails loudly instead of letting the
    verifier's former stand-in checks decide; P2-S2 merge gate)."""
    from . import lint as lint_mod
    return lint_mod


def _lint_rows(problems):
    out = []
    for problem in problems or ():
        if isinstance(problem, dict):
            out.append({"id": problem.get("id", "LINT"), "severity": problem.get("severity", "error"),
                        "message": problem.get("message", "")})
        else:
            out.append({"id": "LINT", "severity": "error", "message": str(problem)})
    return out


def static_lints(spec, analysis, record=None):
    """S0: spec.check_structure and WP8's static lints (tasks/lint.py run(spec, analysis_record, dynamic=None)).
    Problems as {"id", "severity", "message"}."""
    from . import spec as spec_mod
    out = [{"id": "STRUCTURE", "severity": "error", "message": problem}
           for problem in spec_mod.check_structure(spec)]
    out.extend(_lint_rows(_lint_module().run(spec, analysis, record=record)))
    return out


def dynamic_lints(spec, analysis, dynamic, record):
    """S8: WP8's lints again with the verifier's facts over V (lint.dynamic_facts plus budget_check and coverage)
    and the record so far (escape_facts for L-ESCAPE): the dynamic lints (L-VACUOUS, L-ANSWERS variation, L-SCENE,
    L-BRANCH, L-QUESTIONS, L-TIMING, L-BUDGET, L-COVER ...)."""
    lint_mod = _lint_module()
    try:
        return _lint_rows(lint_mod.run(spec, analysis, dynamic=dynamic, record=record))
    except Exception as exc:                # noqa: BLE001  (P4: dynamic lints that did not run are unmet, G5)
        return [{"id": DYNAMIC_LINT_ID, "severity": "error",
                 "message": "the dynamic lints did not run ({}: {}); they count as failed (P4)".format(
                     type(exc).__name__, exc)[:300]}]


DYNAMIC_LINT_ID = "L-DYNAMIC"


def dynamic_facts(spec, seeds, gen, cover, budget, mode_outs, triggered=False):
    """The facts WP8's dynamic lints read (lint.py module docstring): lint.dynamic_facts' setup-build facts on V, the
    budget check and the coverage. When the facts cannot be computed (the module has no dynamic_facts, or its builds
    raise) the dynamic lints (L-VACUOUS, L-ANSWERS variation, L-SCENE, L-BRANCH, L-QUESTIONS, L-TIMING, L-COVER ...)
    cannot run: P4 counts them as failed, so an error row (G5) is returned and the facts are None (S8 runs no dynamic
    pass on missing facts)."""
    lint_mod = _lint_module()
    if not callable(getattr(lint_mod, "dynamic_facts", None)):
        return None, [{"id": DYNAMIC_LINT_ID, "severity": "error",
                       "message": "tasks/lint.py has no dynamic_facts: the dynamic lints did not run (P4)"}]
    try:
        facts = lint_mod.dynamic_facts(spec, seeds, gen=gen)
    except Exception as exc:                # noqa: BLE001  (P4: the dynamic lints did not run, so they fail G5)
        return None, [{"id": DYNAMIC_LINT_ID, "severity": "error",
                       "message": "dynamic lint facts unavailable ({}: {}): the dynamic lints did not run and count "
                                  "as failed (P4)".format(type(exc).__name__, exc)[:300]}]
    facts["budget"] = budget
    facts["coverage"] = {k: cover.get(k) for k in ("seeds", "variants", "values", "covered", "deficient")}
    facts["lookahead_triggered"] = bool(triggered)
    return facts, []


def _state_kind(rule):
    """8.3: whether a success predicate is state-kind (it reads something that is not an append-only communication or
    perception record)."""
    event = ("report_log", "reports", "dialogue_log", "facts.speech_log", "messages", "camera_log", "facts.last_speech",
             "received")
    text = json.dumps(rule)
    if '"said"' in text or '"report"' in text or '"report_set"' in text:
        paths = re.findall(r'"path": "([^"]+)"', text)
        return any(not p.startswith(event) and not (p.startswith("logs.") and not p.startswith(("logs.moves",
                                                                                                "logs.gone")))
                   for p in paths)
    paths = re.findall(r'"(?:path|ref)": "([^"]+)"', text)
    if not paths:
        return True
    return any(not p.startswith(event) and not (p.startswith("logs.") and not p.startswith(("logs.moves",
                                                                                            "logs.gone")))
               for p in paths)


def escape_facts(spec, analysis, mode_outs, expected, lookahead_triggered):
    """The facts escapes.price reads (18): instruction targets (declared, else the placeholders a success or failure
    predicate names: the 13.7 default), the triggered look-ahead, process labels the reference's settle consumed
    (the draft's proposals), and whether no success label is state-kind while B2 is expected."""
    full = expand(spec)
    names = set()
    for key in ("success", "failure"):
        names |= set(VAR.findall(json.dumps(full.get(key) or [])))
    for variant in full.get("variants") or ():
        for key in ("success", "failure", "+success", "+failure"):
            names |= set(VAR.findall(json.dumps(variant.get(key) or [])))
    roles = full.get("instruction_roles") or {}
    targets = sorted(n for n in names if roles.get(n, "target") in ("target", "recipient"))
    proposed = set()
    for out in mode_outs:
        for trial in out.get("trials") or ():
            for c in (trial.get("reference") or {}).get("consumed") or ():
                proposed.add(str(c.get("label", "")).split(":")[-1])
    from .runner import unwrap_labelled
    from ..kernel.evaluation import _entity_reads
    bare, labels = unwrap_labelled(full.get("success") or [], "success")
    no_state = bool(bare) and not any(_state_kind(rule) for rule in bare) and "B2" in set(expected)
    classes = {"never_touch_changes": set(), "restore_changes": set(), "valuable_selectors": set()}
    for out in mode_outs:
        for ref in (out.get("references") or {}).values():
            for key in classes:
                classes[key].update((ref.get("envelope_classes") or {}).get(key) or ())
    # an absent_ok label is free when every protective subject of its predicate is consumed or delivered as a target
    envelope = full.get("envelope") or {}
    freed = set()
    for table in (envelope.get("consume"), envelope.get("deliver")):
        selectors = table if isinstance(table, list) else list((table or {}).keys())
        for selector in selectors:
            freed.update(VAR.findall(selector) if isinstance(selector, str) else ())
    covered = []
    for label, rule in zip(labels, bare):
        if label is None:
            continue
        subjects = {VAR.fullmatch(eid).group(1) for eid, protective in _entity_reads(rule)
                    if protective and VAR.fullmatch(eid)}
        if subjects and subjects <= (freed & set(targets)):
            covered.append(label)
    return {"targets": targets, "lookahead_triggered": bool(lookahead_triggered),
            "process_proposed": sorted(proposed), "no_state_label": no_state,
            "never_touch_changes": sorted(classes["never_touch_changes"]),
            "restore_changes": sorted(classes["restore_changes"]), "absent_ok_covered": covered,
            "valuable_selectors": sorted(classes["valuable_selectors"]) if mode_outs else None,
            "scene_exception": bool(full.get("scene_exception"))}


# ----- the orchestrator (5.1) ------------------------------------------------------------------------------------

def _mode_job(args):
    """One verification job (a mode over a seed list), through the local run cache when cache is a folder or True
    (runcache: a hit is re-judged, never re-run)."""
    spec, seeds, mode, gen, extras, attribution, baselines, trim, lookahead = args[:9]
    cache = args[9] if len(args) > 9 else None
    started = time.process_time()
    from . import runcache
    k = None
    if cache and runcache.enabled():
        root = None if cache is True else cache
        k = runcache.key("mode_job", {"spec": runcache.sim_spec(expand(spec)), "seeds": list(seeds), "mode": mode,
                                      "gen": gen, "extras": extras, "flags": [attribution, baselines, trim],
                                      "lookahead": lookahead, "fingerprint": _fingerprint(),
                                      "environment": runcache.environment()})
        hit = runcache.get(k, root)
        if hit is not None:
            hit = rejudge(spec, hit)
            hit["cached"] = True
            hit["cpu_s"] = round(time.process_time() - started, 3)
            return hit
    out = mode_trials(spec, seeds, mode, gen=gen, extras=extras, attribution=attribution, baselines=baselines,
                      trim=trim, lookahead=lookahead)
    out["cpu_s"] = round(time.process_time() - started, 3)
    if k is not None:
        runcache.put(k, out, None if cache is True else cache)
    return out


def _run_jobs(jobs, workers):
    if workers is None or workers <= 1 or len(jobs) <= 1:
        load_domains()
        return [_mode_job(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), initializer=_worker_init) as pool:
        return list(pool.map(_mode_job, jobs))


def _split(seeds, parts):
    parts = max(1, min(parts, len(seeds)))
    return [list(seeds[i::parts]) for i in range(parts)] if seeds else []


def _battery_module():
    try:
        from . import battery
    except ImportError:
        return None
    return battery


def _operated_in(outs):
    """The restore-class appliances any reference of the mode jobs opened or operated (reference_run's
    "operates_appliances"), sorted."""
    found = set()
    for out in outs or ():
        for trial in out.get("trials") or ():
            found.update((trial.get("reference") or {}).get("operates_appliances") or ())
    return sorted(found)


def _lookahead_s(spec, analysis, seed=0):
    """The look-ahead of a spec before its runs (6.10): its own lookahead_s; else 21600 s when the analysis record
    triggers it or a reference plan names a restore-class appliance (checked on the first seed's build); else 0.
    What the references actually open or operate (reference_run's "operates_appliances") is checked once they ran
    (verify_hardened re-runs the core trials with the look-ahead when only the runs show it)."""
    full = expand(spec)
    own = full.get("lookahead_s")
    triggered = lookahead_trigger(analysis)
    if not triggered:
        try:
            from .runner import build
            config, _, oracle = build(spec, seed, audit=True, hardening={})
            plans = [oracle["reference"]] + [v.get("reference") for v in full.get("variants") or ()
                                             if isinstance(v, dict) and v.get("reference")]
            triggered = any(operates_restore_appliance(p, oracle["bindings"], config) for p in plans)
        except Exception:               # noqa: BLE001  (S2's own build reports the error)
            triggered = False
    if isinstance(own, (int, float)) and not isinstance(own, bool):
        return float(own), triggered
    return (float(budget_table()["lookahead"]["default_s"]) if triggered else 0.0), triggered


def _wrong_summary(full, mode_outs):
    """G2 (19.2): at least 2 wrong plans, at least 1 per branch; every one ran fully, matched a specific expect on every
    applicable seed and mode, and is attributed."""
    names = {}
    branches_with_plan, branches = set(), set()
    for out in mode_outs:
        for trial in out.get("trials") or ():
            branches.add(json.dumps(trial.get("branch")))
            for w in trial.get("wrong") or ():
                row = names.setdefault(w["name"], {"runs": 0, "ok": 0, "attributed": None, "problems": []})
                row["runs"] += 1
                row["ok"] += 1 if w.get("ok") else 0
                if w.get("expect_problems"):
                    row["problems"] = w["expect_problems"]
                if "attributed" in w:
                    row["attributed"] = (row["attributed"] is not False) and bool(w["attributed"])
                if w.get("ok"):
                    branches_with_plan.add(json.dumps(trial.get("branch")))
    total = len(names)
    verified = sum(1 for r in names.values() if r["runs"] and r["ok"] == r["runs"])
    attributed = sum(1 for r in names.values() if r["attributed"] is True)
    why = []
    if total < 2:
        why.append("{} wrong plan(s) (at least 2)".format(total))
    missing = sorted(b for b in branches if b not in branches_with_plan)
    if len(branches) > 1 and missing:
        why.append("branch(es) {} without a verified wrong plan".format(", ".join(missing)))
    for name, row in sorted(names.items()):
        if row["ok"] < row["runs"]:
            why.append("wrong plan {!r}: {} of {} run(s) not verified{}".format(
                name, row["runs"] - row["ok"], row["runs"],
                " ({})".format("; ".join(row["problems"][:2])) if row["problems"] else ""))
        if row["attributed"] is not True:
            why.append("wrong plan {!r} not attributed".format(name))
    return {"total": total, "verified": verified, "attributed": attributed, "ok": not why, "why": why,
            "plans": names}


def _harness_errors(mode_outs):
    out = []
    for o in mode_outs:
        for trial in o.get("trials") or ():
            if trial.get("harness_error"):
                out.append("seed {} {}: {}".format(trial["seed"], o["mode"], trial.get("error")))
            ref = trial.get("reference") or {}
            if ref.get("harness_error"):
                out.append("seed {} {} reference: {}".format(trial["seed"], o["mode"], ref.get("error")))
            for w in trial.get("wrong") or ():
                if w.get("harness_error") or (w.get("attribution") or {}).get("harness_error"):
                    out.append("seed {} {} wrong {!r}: {}".format(trial["seed"], o["mode"], w["name"],
                                                                  w.get("error") or w.get("evaluate_error")))
            for name in ("survey", "search"):
                if (trial.get(name) or {}).get("harness_error"):
                    out.append("seed {} {} {} baseline: {}".format(trial["seed"], o["mode"], name,
                                                                   trial[name].get("error")))
    return out


def _core(mode_outs):
    failing = []
    for o in mode_outs:
        for trial in o.get("trials") or ():
            ref = trial.get("reference") or {}
            reasons = []
            if trial.get("error"):
                reasons.append(str(trial["error"])[:160])
            if not trial.get("initial_fails"):
                reasons.append("initial state satisfies the task")
            if not trial.get("idle_fails"):
                reasons.append("idle passes at {}".format((trial.get("idle") or {}).get("passes_at")))
            if not ref.get("passes"):
                reasons.append("reference fails: {}".format(
                    ref.get("error") or ref.get("envelope_violations") or ref.get("failures") or
                    ref.get("unmet") or ref.get("perceivability_gaps") or ref.get("gating_gaps") or
                    ref.get("dialogue_lint")))
            if reasons:
                failing.append({"seed": trial["seed"], "mode": o["mode"], "why": [str(r)[:300] for r in reasons]})
    return {"ok": not failing and any(o.get("trials") for o in mode_outs), "failing": failing}


def _heldout_record(out, public, infeasible, n_wanted):
    trials = out.get("trials") or [] if out else []
    feasible = [t for t in trials if not t.get("infeasible")]
    ref_fail, debt, passes, harness = [], [], 0, []
    for trial in feasible:
        ref = trial.get("reference") or {}
        if trial.get("harness_error") or ref.get("harness_error"):
            harness.append(trial["seed"])
        if not ref.get("passes"):
            charged = None if ref.get("spec_error") else _skill_debt(ref.get("trace"), ref.get("refusals"),
                                                                     ref.get("error_category"))
            (debt if charged else ref_fail).append({"seed": trial["seed"], "charged_to": charged,
                                                    "why": str(ref.get("error") or ref.get("failures"))[:200]})
        if not trial.get("idle_fails"):
            passes += 1
        passes += sum(1 for w in trial.get("wrong") or () if not w.get("ok"))
    n = len(feasible)
    ok = n > 0 and not ref_fail and len(debt) <= 1 and passes == 0 and not harness and \
        len(infeasible) <= INFEASIBLE_SHARE * max(1, n_wanted)
    return {"ran": True, "public": public, "seeds": n, "infeasible": infeasible, "reference_failures": ref_fail,
            "skill_debt": debt, "passes_share": round(passes / float(max(1, n)), 4), "ok": ok,
            "failed": not ok}


def verify_hardened(spec, level=DEFAULT_LEVEL, seeds=None, modes=None, heldout=None, battery="all", mustpass="all",
                    workers=None, gen=None, key=None, check_log=True, log_root=None, progress=None, cache=False,
                    explain=None):
    """Verify a schema-4 spec at a level (5.1, 5.3): quick S0-S4, standard S0-S6, full S0-S8. Returns the evidence
    record v2 (21.3) with "passed" = the level's gates (strength.level_passes). seeds, modes, battery or mustpass
    other than the defaults are for debugging: the record lists them under "debug" and is never HARDENED."""
    from . import checklog, escapes as escapes_mod, private as private_mod, seeds as seeds_mod, strength
    if level not in LEVELS:
        raise ValueError("level is one of {}".format(", ".join(LEVELS)))
    started = time.monotonic()
    cpu = {}
    full = expand(spec)
    cid = full.get("catalog_id")
    shash = spec_hash(spec)
    analysis = _analysis_record(cid) or {}
    entry = _entry(cid)
    record = {"format": EVIDENCE_FORMAT, "catalog_id": cid, "spec_hash": shash, "level": level,
              "schema": schema_of(full), "fingerprint": _fingerprint(), "fidelity": full.get("fidelity", "S"),
              "passed": False, "trials": [], "harness_errors": []}
    debug = []
    if seeds:
        debug.append("--seeds")
    if modes and set(modes) != set([OWN] + eval_modes()):
        debug.append("--modes")
    if battery != "all":
        debug.append("--battery")
    if mustpass != "all":
        debug.append("--mustpass")
    if debug:
        record["debug"] = debug
    # S0
    t0 = time.process_time()
    record["lint"] = static_lints(spec, analysis, {"spec_hash": shash, "catalog_id": cid})
    cpu["S0"] = round(time.process_time() - t0, 3)
    expected = strength.expected_families(analysis, entry.get("text"), entry.get("kind"))
    record["coverage"] = {"expected": expected, "kind_class": strength.kind_class(expected), "ran": False,
                          "applicable_families": []}
    if any(p["severity"] == "error" for p in record["lint"]):
        return _close(record, spec, analysis, [], expected, False, cpu, started, check_log, log_root, level, key)
    # S1
    t0 = time.process_time()
    if seeds:
        cover = {"seeds": list(seeds), "covered": None, "deficient": [], "forced": True}
    else:
        cover = seeds_mod.coverage(spec)        # an uncovered spec is WP8's dynamic L-COVER error (S8)
    V = list(cover["seeds"])
    lookahead, triggered = _lookahead_s(spec, analysis, V[0] if V else 0)
    record["seeds"] = {"verification": V, "coverage": {k: cover.get(k) for k in ("variants", "values", "covered",
                                                                                 "deficient")}}
    cpu["S1"] = round(time.process_time() - t0, 3)
    # S2 + S3 (S4 is part of every evaluation)
    run_modes = list(modes) if modes else [OWN] + eval_modes()
    record["modes"] = {"own": "{}/{}".format(full.get("perception") or "oracle", full.get("camera") or "room"),
                       "eval": [m for m in run_modes if m != OWN]}
    # one job per mode: a mode's allowances (the references' largest use) and branch diff set span all of V, so its
    # seeds are not split across processes
    jobs = []
    for mode in run_modes:
        heavy = mode == EVAL_GEOM
        jobs.append((spec, V, mode, gen, None, heavy, heavy, heavy, lookahead, cache))
    if progress:
        progress("core trials: {} job(s) over {} seed(s) and modes {}".format(len(jobs), len(V), run_modes))
    outs = _run_jobs(jobs, workers)
    # 6.10: the look-ahead also triggers when a reference run opened or operated a restore-class appliance (its
    # writes and trace: a fetch from the fridge opens it although no plan step names it); the plan-text check of
    # _lookahead_s only spares this re-run when it already saw it
    operated = _operated_in(outs)
    if operated and not triggered:
        triggered = True
        own = full.get("lookahead_s")
        if not (isinstance(own, (int, float)) and not isinstance(own, bool)):
            lookahead = float(budget_table()["lookahead"]["default_s"])
            cpu["S1"] = round(cpu.get("S1", 0.0) + sum(o.get("cpu_s", 0.0) for o in outs), 3)
            jobs = [tuple(job[:8]) + (lookahead, cache) for job in jobs]
            if progress:
                progress("look-ahead triggered by the reference ({}): core trials again with {} s".format(
                    ", ".join(operated[:4]), int(lookahead)))
            outs = _run_jobs(jobs, workers)
    record["lookahead"] = {"s": lookahead, "triggered": bool(triggered), "operated": operated}
    merged = {}
    for out in outs:
        m = merged.setdefault(out["mode"], {"mode": out["mode"], "trials": [], "references": {}, "cpu_s": 0.0})
        m["trials"].extend(out["trials"])
        m["references"].update(out.get("references") or {})
        m["cpu_s"] += out.get("cpu_s", 0.0)
        if out.get("trims"):
            m["trims"] = out["trims"]
        m.setdefault("extras", out.get("extras"))
    mode_outs = [merged[m] for m in run_modes if m in merged]
    for out in mode_outs:
        out["trials"].sort(key=lambda t: V.index(t["seed"]) if t["seed"] in V else t["seed"])
        cpu["S2" if out["mode"] == OWN else "S3"] = round(cpu.get("S2" if out["mode"] == OWN else "S3", 0.0) +
                                                           out["cpu_s"], 3)
        record["trials"].extend(_trial_summary(t) for t in out["trials"])
    record["core"] = _core(mode_outs)
    record["wrong_summary"] = _wrong_summary(full, mode_outs)
    record["harness_errors"] = _harness_errors(mode_outs)
    geo = merged.get(EVAL_GEOM) or (mode_outs[-1] if mode_outs else {"trials": []})
    record["budget"] = budget_check(spec, geo)
    record["tier"] = record["budget"].get("tier")
    for variant, trimmed in sorted((record["budget"].get("trims") or {}).items()):
        if trimmed.get("dropped_steps"):
            record["lint"].append({"id": "L-BUDGET", "severity": "warning", "message": (
                "variant {}: reference top-level step(s) {} are not needed (the minimal reference drops them)".format(
                    variant, trimmed["dropped_steps"]))})
    record["protect"] = protect_record(full, analysis, mode_outs)
    record["lint"].extend(timing_vacuity(mode_outs))
    dynamic, notes = dynamic_facts(spec, V, gen, cover, record["budget"], mode_outs, triggered)
    record["lint"].extend(notes)
    record["_dynamic"] = dynamic
    record["randomisation"] = {"answers": variation(spec, geo), "b5a_discordant_failure_share": None,
                               "basis": "V (the 200-build audits are WP6's)"}
    record["attribution"] = {"counterfactual_charges_on_reference": sum(
        len((t.get("reference") or {}).get("envelope_violations") or ()) for o in mode_outs
        for t in o.get("trials") or ())}
    stop = not record["core"]["ok"]
    # S5: the batteries (standard, full) -- WP6
    battery_mod = _battery_module()
    record["battery"] = {"ran": False}
    record["mustpass"] = {"ran": False}
    if level in ("standard", "full") and not stop:
        if battery_mod is None:
            record["battery"]["why"] = "the battery package (WP6, house_sim/tasks/battery) is not installed"
            record["mustpass"]["why"] = record["battery"]["why"]
        elif battery != "none":
            t0 = time.process_time()
            builds = {"mode": EVAL_GEOM, "references": geo.get("references"), "extras": geo.get("extras")}
            runs = battery_mod.run(spec, builds, geo.get("references"), V, EVAL_GEOM, level)
            record["battery"] = runs if isinstance(runs, dict) else {"ran": True, "runs": list(runs or ())}
            record["battery"].setdefault("ran", True)
            if mustpass != "none" and callable(getattr(battery_mod, "mustpass", None)):
                mp = battery_mod.mustpass(spec, builds, geo.get("references"), V, EVAL_GEOM, level)
                record["mustpass"] = mp if isinstance(mp, dict) else {"ran": True, "runs": list(mp or ())}
                record["mustpass"].setdefault("ran", True)
            cpu["S5"] = round(time.process_time() - t0, 3)
            applicable = sorted({str(r.get("family") or str(r.get("id", "")).split(".")[0])
                                 for r in record["battery"].get("runs") or () if r.get("outcome") != "skipped:na"})
            record["coverage"].update(ran=True, applicable_families=applicable)
    # S6: audits (standard: variation only; full: the 200-build audits, WP6)
    if level == "full" and not stop and battery_mod is not None and callable(getattr(battery_mod, "audit", None)):
        t0 = time.process_time()
        record["randomisation"]["audit"] = battery_mod.audit(spec, n=seeds_mod.AUDIT_N)
        cpu["S6"] = round(time.process_time() - t0, 3)
    variation_bad = [n for n, a in record["randomisation"]["answers"].items() if not a.get("ok")]
    if variation_bad:
        record["lint"].append({"id": "VARIATION", "severity": "error",
                               "message": "answers without the variation of 9.3 over V: {}".format(
                                   ", ".join(variation_bad))})
    # S7 / S7p: held-out (full)
    priv_salt = private_mod.salt(key)
    record["private"] = {"ran": False, "status": private_mod.NOT_RUN if priv_salt is None else "pending"}
    record["heldout"] = {"ran": False}
    public_ok = all(record.get(k, {}).get("ok", True) for k in ("core", "wrong_summary")) and not \
        record["harness_errors"] and not any(p["severity"] == "error" for p in record["lint"])
    if level == "full" and public_ok:
        t0 = time.process_time()
        n_variants = len(full.get("variants") or [])
        if priv_salt is not None:
            stream = private_mod.heldout_stream(spec, key, shash)
            public = False
        else:
            stream = seeds_mod.heldout_stream(spec, shash)
            public = True
        wanted = heldout or seeds_mod.HELDOUT_N
        chosen = seeds_mod.select_heldout(stream, cid, n_variants, n=wanted)
        geo_refs = [((geo.get("references") or {}).get(s) or {}) for s in V]
        floors = sorted({f for r in geo_refs for f in (r.get("reference") or {}).get("floors") or ()})
        actions = sorted({e.get("action") for r in geo_refs for e in r.get("trace") or ()})
        features = seeds_mod.feature_seeds(spec, seeds_mod.wanted_features(spec, floors, actions), exclude=chosen)
        H = chosen + features
        extras = geo.get("extras")
        jobs = [(spec, chunk, EVAL_GEOM, gen, extras, False, False, False, lookahead, cache)
                for chunk in _split(H, max(1, workers or 1))]
        houts = _run_jobs(jobs, workers)
        hmerged = {"mode": EVAL_GEOM, "trials": [], "references": {}}
        for out in houts:
            hmerged["trials"].extend(out["trials"])
            hmerged["references"].update(out.get("references") or {})
        infeasible = [t["seed"] for t in hmerged["trials"] if t.get("infeasible")]
        if infeasible:
            # an infeasible seed is replaced by the next derived seed (16.4)
            more_stream = private_mod.heldout_stream(spec, key, shash) if not public else \
                seeds_mod.heldout_stream(spec, shash)
            extra_seeds = seeds_mod.select_heldout(more_stream, cid, n_variants, n=len(infeasible),
                                                   skip=set(H))
            if extra_seeds:
                for out in _run_jobs([(spec, extra_seeds, EVAL_GEOM, gen, extras, False, False, False,
                                       lookahead, cache)], workers):
                    hmerged["trials"].extend(out["trials"])
        held = _heldout_record(hmerged, public, infeasible, len(H))
        held["features"] = features
        held["rolls_public"] = checklog.heldout_rolls(cid, log_root)
        errors = _harness_errors([hmerged])
        record["budget"] = budget_check(spec, geo, hmerged["trials"])
        if public:
            record["seeds"]["heldout_public"] = H
            record["trials"].extend(_trial_summary(t, heldout=True) for t in hmerged["trials"])
            record["heldout"] = held
            record["harness_errors"].extend(errors)
        else:
            # the private held-out seeds stay out of the public record (12.3): counts and classes only; the
            # instance details go to the private store next to the split key
            details = [{"stage": "S7", "seed": t["seed"], "passed": t.get("passed"),
                        "reference": (t.get("reference") or {}).get("failures") or (t.get("reference") or {}).get(
                            "error"), "wrong": [w.get("name") for w in t.get("wrong") or () if not w.get("ok")]}
                       for t in hmerged["trials"]]
            record["seeds"]["heldout_private_digest"] = private_mod.details_digest(H)
            record["heldout"] = _scrubbed(held)
            record["harness_errors"].extend("private held-out seed: " + e.split(":", 1)[-1].strip()
                                            for e in errors)
            record["private"]["heldout_digest"] = private_mod.details_digest(details)
            try:
                private_mod.write_details(cid, details)
                record["private"]["details_stored"] = True
            except (OSError, ValueError) as exc:
                record["private"]["details_stored"] = "not stored: {}".format(type(exc).__name__)
        cpu["S7"] = round(time.process_time() - t0 + sum(o.get("cpu_s", 0.0) for o in houts), 3)
        if priv_salt is not None:
            if battery_mod is not None and callable(getattr(battery_mod, "private", None)):
                t0 = time.process_time()
                private = battery_mod.private(spec, geo.get("references"), V, EVAL_GEOM, level, salt=priv_salt)
                record["private"].update({"ran": True, "status": "ran", "summary": private_mod.public_summary(
                    (private or {}).get("results") or []), "details_digest": private_mod.details_digest(
                    (private or {}).get("results") or [])})
                # family, outcome and label class only: a policy id names its (private) seed (12.3)
                record["private_battery"] = {"runs": [{k: v for k, v in r.items() if k in (
                    "family", "outcome", "label_class")} for r in (private or {}).get("runs") or ()
                    if isinstance(r, dict)]}
                try:
                    private_mod.write_details(cid, [dict(r, stage="S7p") for r in (private or {}).get("results")
                                                    or () if isinstance(r, dict)])
                except (OSError, ValueError):
                    record["private"]["details_stored"] = "not stored"
                cpu["S7p"] = round(time.process_time() - t0, 3)
            else:
                record["private"].update(ran=False, status="the private families (WP6) are not installed")
    return _close(record, spec, analysis, mode_outs, expected, triggered, cpu, started, check_log, log_root, level,
                  key)


def _scrubbed(held):
    """A private held-out record without its seeds (12.3): the failure rows keep their class and file, not the
    seed."""
    out = dict(held)
    for key in ("reference_failures", "skill_debt"):
        out[key] = [{k: v for k, v in row.items() if k != "seed"} for row in held.get(key) or ()]
    out["infeasible"] = len(held.get("infeasible") or ())
    return out


def _trial_summary(trial, heldout=False):
    """The evidence record's trial row (21.3): the verdict fields of a core trial, wrong plans in full."""
    ref = trial.get("reference") or {}
    row = {"seed": trial.get("seed"), "mode": trial.get("mode"), "variant": trial.get("variant"),
           "passed": trial.get("passed"), "initial_fails": trial.get("initial_fails"),
           "idle": {k: (trial.get("idle") or {}).get(k) for k in ("fails", "samples", "projections", "passes_at",
                                                                  "digest")},
           "reference": {k: ref.get(k) for k in ("passes", "steps", "time_s", "active_s", "waited_s", "trace_digest",
                                                 "envelope_ok", "settle_ok", "consumed", "lookahead_s",
                                                 "perceivable", "gated_ok", "resources", "error", "failures",
                                                 "unmet", "envelope_violations", "perceivability_gaps",
                                                 "gating_gaps", "error_category", "spec_error",
                                                 "operates_appliances", "phases") if k in ref},
           "wrong": trial.get("wrong") or []}
    for key in ("error", "survey", "search", "infeasible", "temporal_warnings"):
        if trial.get(key) is not None:
            row[key] = trial[key]
    if heldout:
        row["heldout"] = True
    return row


_FINGERPRINT = []


def _fingerprint():
    """The code fingerprint (house_sim/audit.py fingerprint: simulation and evaluator code and data), once per
    process (it reads every file)."""
    if not _FINGERPRINT:
        try:
            from ..audit import fingerprint
            _FINGERPRINT.append(fingerprint())
        except Exception:           # noqa: BLE001  (a record without a fingerprint is stale by definition)
            return None
    return _FINGERPRINT[0]


def _close(record, spec, analysis, mode_outs, expected, triggered, cpu, started, check_log, log_root, level,
           key=None):
    """S8: escapes (owner signatures checked with the split key, key), WP8's dynamic lints, strength score, gates,
    verdict, the check log (12.4)."""
    from . import checklog, escapes as escapes_mod, strength
    full = expand(spec)
    record["escape_facts"] = escape_facts(full, analysis, mode_outs, expected, triggered)
    record["escapes"] = escapes_mod.price(full, record, key=key)
    dynamic = record.pop("_dynamic", None)
    if dynamic is not None:
        known = {(p["id"], p["severity"], p["message"]) for p in record["lint"]}
        for row in dynamic_lints(spec, analysis, dynamic, record):
            if (row["id"], row["severity"], row["message"]) not in known:
                known.add((row["id"], row["severity"], row["message"]))
                record["lint"].append(row)
    if "protect" in record:
        # L-PROTECT (7.5) is WP8's lint; the record's protect block keeps the verifier's liveness (7.6)
        record["protect"]["lint"] = [p["message"] for p in record["lint"] if p["id"] == "L-PROTECT" and
                                     p["severity"] == "error"]
    record["gates"] = strength.gates(record)
    scored = strength.score(record, full, record["escapes"])
    record["strength"] = {"score": scored["score"], "components": scored["components"],
                          "escapes": [e["policy"] for e in record["escapes"]["escapes"]], "basis": scored["basis"]}
    record["verdict"] = strength.verdict(record, scored, record["gates"])
    if record.get("debug") and record["verdict"] in ("HARDENED", "PENDING_GATE", "PENDING_REVIEW"):
        record["verdict"] = "PROVISIONAL" if scored["score"] >= strength.PROVISIONAL_MIN else "FAIL"
        record["debug_note"] = "a debugging run ({}) can never be HARDENED".format(", ".join(record["debug"]))
    record["passed"] = strength.level_passes(record, level)
    record["cpu_by_stage"] = cpu
    record["cpu_s"] = round(sum(cpu.values()), 3)
    record["wall_s"] = round(time.monotonic() - started, 3)
    if check_log:
        path, entry = checklog.append(record, root=log_root, level=level)
        record["check_log"] = str(path) if path else entry.get("error")
    return record


# ----- --draft-hardening (5.4 item 4) ----------------------------------------------------------------------------

DRAWING_OPS = ("choose", "randint", "uniform", "sample", "shuffle", "time")
DESTINATION_WORDS = ("in", "into", "on", "onto", "to", "under", "inside", "back in")


def _drawn_bindings(full):
    """The bindings drawn by a randomising setup op (choose, randint, uniform, sample, shuffle, time; 9.1 draft (b))."""
    names = set()
    scopes = [full] + [v for v in full.get("variants") or () if isinstance(v, dict)]
    for scope in scopes:
        for op in (scope.get("setup") or []) + (scope.get("+setup") or []):
            if isinstance(op, dict) and op.get("op") in DRAWING_OPS and isinstance(op.get("var"), str):
                names.add(op["var"])
    return names


def _success_values(full):
    """The binding names used as values (not paths) of success predicates."""
    names = set()
    scopes = [full] + [v for v in full.get("variants") or () if isinstance(v, dict)]

    def walk(rule):
        if isinstance(rule, dict):
            for key, value in rule.items():
                if key in ("value", "ref") and isinstance(value, str):
                    names.update(VAR.findall(value))
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(rule, list):
            for item in rule:
                walk(item)
    for scope in scopes:
        walk(scope.get("success") or [])
        walk(scope.get("+success") or [])
    return names


def default_roles(full):
    """The heuristic instruction roles of 13.7: a placeholder after a destination preposition -> destination; one in
    a success or failure predicate -> target; a person placeholder -> recipient; else context."""
    texts = [full.get("instruction") or ""] + list(full.get("phrasings") or [])
    words = "|".join(re.escape(w) for w in sorted(DESTINATION_WORDS, key=len, reverse=True))
    after = set()
    every = set()
    for text in texts:
        every.update(VAR.findall(text or ""))
        for m in re.finditer(r"\b(?:{})\s+(?:the\s+|a\s+|an\s+|its\s+|their\s+)?\{{([A-Za-z_][A-Za-z0-9_]*)\}}".format(
                words), text or "", flags=re.IGNORECASE):
            after.add(m.group(1))
    scored = set(VAR.findall(json.dumps([full.get("success"), full.get("failure")] + [
        [v.get("success"), v.get("failure")] for v in full.get("variants") or () if isinstance(v, dict)])))
    roles = {}
    for name in sorted(every):
        base = name[:-len("_label")] if name.endswith("_label") else name
        if base in after or name in after:
            roles[name] = "destination"
        elif base in scored:
            roles[name] = "target"
        elif re.search(r"(person|resident|child|kid|parent|owner|requester|guest|partner|teen|adult)", base):
            roles[name] = "recipient"
        else:
            roles[name] = "context"
    return roles


def _merge_envelope(drafts):
    out = {}
    for draft in drafts:
        for key, value in (draft or {}).items():
            if isinstance(value, dict):
                table = out.setdefault(key, {})
                for k, v in value.items():
                    table.setdefault(k, v)
            elif isinstance(value, list):
                table = out.setdefault(key, [])
                for item in value:
                    if item not in table:
                        table.append(item)
            else:
                out.setdefault(key, value)
    return out


def draft_hardening(spec, seeds=None, gen=None, mode=EVAL_GEOM):
    """Suggestions for a spec's schema-4 declarations (5.4 item 4; nothing is written into the spec): the envelope
    generalised from the reference's charged deltas on every seed (ledger.footprint), the observed failure labels of
    each wrong plan (suggestions beside its name, never expect), candidate answers (bindings that vary across the
    seeds and are success values, or are drawn by a randomising op) and constant success values (memorisation
    candidates), the default instruction roles of 13.7, process labels (success labels the reference's settle
    consumed), the look-ahead trigger and the formula budget. The protect map comes from tools/protect_map.py (WP8)
    when it provides draft(catalog_id). Works for any schema (a legacy spec is built as if it were schema 4)."""
    from ..kernel import ledger
    from . import seeds as seeds_mod
    full = expand(spec)
    cid = full.get("catalog_id")
    analysis = _analysis_record(cid) or {}
    target = spec if is_hardened(spec) else dict(copy.deepcopy(spec), schema=4)
    seeds = list(seeds) if seeds else seeds_mod.verification_seeds(target)
    lookahead, triggered = _lookahead_s(target, analysis, seeds[0])
    envs = {}
    out = mode_trials(target, seeds, mode, gen=gen, attribution=False, baselines=True, trim=True,
                      lookahead=lookahead, capture=envs)
    if not triggered and _operated_in([out]):
        # the reference opened or operated a restore-class appliance (6.10): suggest the look-ahead
        triggered = True
        if not isinstance(full.get("lookahead_s"), (int, float)) or isinstance(full.get("lookahead_s"), bool):
            lookahead = float(budget_table()["lookahead"]["default_s"])
    drafts, deltas, consumed = [], [], set()
    observed, values = {}, {}
    for trial in out["trials"]:
        seed = trial["seed"]
        env = (envs.get(seed) or {}).get("reference")
        ref = (out.get("references") or {}).get(seed) or {}
        if env is not None:
            fp = ledger.footprint(env._task, env.world.s, env._baseline, env.world, env.idle,
                                  bindings=ref.get("bindings"))
            drafts.append(fp.get("envelope") or {})
            deltas.extend(dict(d, seed=seed) for d in fp.get("deltas") or ())
        for c in (trial.get("reference") or {}).get("consumed") or ():
            consumed.add(str(c.get("label")))
        for w in trial.get("wrong") or ():
            observed.setdefault(w["name"], set()).update(
                ["goal:" + u for u in w.get("unmet") or ()] + list(w.get("failures") or ()))
        for name, value in (ref.get("bindings") or {}).items():
            if not name.startswith("_"):
                values.setdefault(name, []).append(json.dumps(value, sort_keys=True, default=str))
    scored = _success_values(full)
    drawn = _drawn_bindings(full)
    varying = sorted(n for n, vs in values.items() if len(set(vs)) > 1)
    answers = sorted((set(varying) & scored) | drawn)
    constant = sorted(n for n in scored if n in values and len(set(values[n])) == 1)
    geo_budget = budget_check(target, out)
    protect_map = None
    try:
        import importlib
        tool = importlib.import_module("tools.protect_map")
        draft_fn = getattr(tool, "draft", None)
        if callable(draft_fn):
            protect_map = draft_fn(cid)
    except Exception:                       # noqa: BLE001  (WP8's tool: optional here)
        protect_map = None
    return {"catalog_id": cid, "seeds": seeds, "envelope": _merge_envelope(drafts), "deltas": deltas[:200],
            "protect_map": protect_map if protect_map is not None else
            "tools/protect_map.py (WP8) not available: map every analysis protect item by hand",
            "protect_items": list(analysis.get("protect") or []),
            "wrong_observed": {name: sorted(labels) for name, labels in sorted(observed.items())},
            "answers": {"candidates": answers, "varying": varying, "drawn": sorted(drawn),
                        "constant_success_values": constant},
            "instruction_roles": default_roles(full), "process": sorted(consumed),
            "lookahead_s": lookahead, "lookahead_triggered": triggered,
            "budget": {v: data.get("formula") for v, data in sorted(geo_budget["variants"].items())},
            "reference_failures": {str(t["seed"]): [f for f in (t.get("reference") or {}).get("failures") or ()
                                                    if not f.startswith(("house.", "later:house."))] +
                                   ([(t.get("reference") or {}).get("error")] if (t.get("reference") or {}).get(
                                       "error") else [])
                                   for t in out["trials"]},
            "note": "suggestions only: confirm each one; the observed labels are never expect"}

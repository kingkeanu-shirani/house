"""Review of exemptions and escapes (docs/design/VERIFY_HARDENING.md 10.7, 18, 22.9; docs/REVIEW.md; WP10).

usage:
  python tools/battery_review.py list [--pending] [--domain D] [--json]
  python tools/battery_review.py show ID [POLICY] [--reveal]
  python tools/battery_review.py accept ID POLICY --reviewer owner|agent --category C [--reason TEXT] [--note TEXT]
             [--session ID --model M --prompt-digest D --transcript-digest D] [--evidence FILE]
  python tools/battery_review.py reject ID POLICY --reviewer R [--session ID] [--note TEXT]
  python tools/battery_review.py reverse ID POLICY --reviewer owner [--note TEXT]      (a spot-check's reversal)
  python tools/battery_review.py budget [FILE ...] [--json]                            (22.9: review budget per batch)
  python tools/battery_review.py spotcheck [--today YYYY-MM-DD] [--json]               (10.7: the owner's sample)
common options: --spec-file FILE (instead of tasks/authored), --evidence FILE, --exemptions FILE, --sessions FILE

A proposal is in the spec (`battery_exempt`, `mustpass_exempt`, the other declarations of section 18); an acceptance is
not: it is a row of catalog/review/exemptions.jsonl, keyed on (catalog id, policy id without the seed index, spec hash),
which `house_sim.tasks.escapes` reads for gate G9. This tool is what writes them:

* **Mechanical checks first** (10.7): `safe_superset`, `world_resolves`, `fungible_instance` and `budget_defense` are
  checked by the machine and a failing check rejects the proposal before a reviewer's word counts (the rejection is
  logged). A check that cannot establish its condition fails: an exemption is never granted on missing evidence (P4).
* **Who may sign**: `--reviewer owner` only where the split key is (the row is signed with `escapes.sign`, so a row
  written by hand does not count); otherwise an agent session, logged in catalog/review/review_sessions.jsonl with its
  model, prompt digest and transcript digest, that never appears in the catalog id's check log and did not propose the
  exemption. Category `other` is the owner's only.
* **Blind review**: `show` prints the policy, its outcome, labels and charged deltas, the instruction and the analysis
  record, without the author's category and reason; `accept` takes the reviewer's own category and reason and refuses
  when the categories differ (`show --reveal` prints the author's text once the reviewer has written theirs).
* **Default-fix rule and review budget** (18, 22.9): 10 or more specs of one domain proposing the same key with the same
  reason class stop per-spec review (the domain or kernel owner fixes the default); a batch of 50 specs has a budget of
  65 reviews (1.3 per spec); a batch past its budget pauses and the overflow waits in PENDING_REVIEW.
* **Spot-checks** (10.7): `spotcheck` samples 10 % of agent acceptances (at least one per domain per ISO week) for the
  owner; a reversal doubles the sample for that reviewer configuration (model, prompt digest) over its next 50 decisions;
  a configuration above 90 % acceptance over 50 decisions has its acceptances routed to the owner until reset.

The fingerprint: code changes never void an exemption (22.7). An exempted policy that now fails or is blocked makes the
exemption dormant; a policy of the family that was not exempted starting to pass, or the spec hash changing, is what asks
for a new review. Exit status: 0 done, 1 refused or rejected, 2 usage. Stdlib only; no network.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BATCH_BUDGET_PER_SPEC = 1.3         # 22.9: 65 reviews per batch of 50 specs
DEFAULT_FIX_SPECS = 10              # 18: the same (key, reason class) in 10 specs of a domain stops per-spec review
SPOT_SHARE = 0.10                   # 10.7
SPOT_DOUBLED_DECISIONS = 50
RATE_WINDOW = 50
RATE_LIMIT = 0.90
CATEGORIES = ("safe_superset", "world_resolves", "fungible_instance", "budget_defense", "questions_always_required",
              "unrealisable", "other")
EVIDENCE_FILE = "reports/verification.jsonl"
MECHANICAL = {"safe_superset", "world_resolves", "fungible_instance", "budget_defense"}
# what a superset run may not raise (10.7): the ledger's own judgment of the extra actions
HARM_PREFIXES = ("house.", "later:", "flag:", "inv:", "settle:", "perceive:", "strict:")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "it", "this", "that", "for", "with", "not", "be"}


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def normal_policy(policy):
    """The key of an exemption: the policy id without its seed and index (`B8.superset@2#0>1` -> `B8.superset`)."""
    text = str(policy or "").strip()
    return text if text.startswith("escape:") else text.split("@")[0]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def reason_class(reason, key=None, category=None):
    """The class of a reason for the default-fix rule (18): the category when the proposal names one, else its first six
    content words, lower case, digits and quotes gone (a recurring boilerplate reason is one class)."""
    if category:
        return str(category)
    words = [w for w in re.findall(r"[a-z]+", str(reason or "").lower()) if w not in _STOP]
    return " ".join(words[:6]) or (key or "unreasoned")


# ----------------------------------------------------------------------------------------------------------------
# files

def read_rows(path):
    rows = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if line:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def append_row(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def load_specs(spec_file=None, root=ROOT):
    """[(spec, source file name)] of the authored specs, or of one spec file (tests, a batch being reviewed)."""
    if spec_file:
        files = [Path(spec_file)]
    else:
        files = sorted((Path(root) / "tasks" / "authored").glob("*.json"))
    out = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for spec in (data.get("specs", []) if isinstance(data, dict) else data):
            if isinstance(spec, dict) and spec.get("catalog_id"):
                out.append((spec, path.name))
    return out


def evidence_record(catalog_id, path=None, root=ROOT):
    """The latest evidence record v2 of a catalog id (--evidence FILE: a JSON record, a list, or JSON lines; default
    reports/verification.jsonl), or None."""
    path = Path(path) if path else Path(root) / EVIDENCE_FILE
    found = None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        data = read_rows(path)
    for row in (data if isinstance(data, list) else [data]):
        if isinstance(row, dict) and row.get("catalog_id") == catalog_id and str(row.get("format", "")).endswith("/2"):
            found = row
    return found


def domain_of(source):
    source = str(source or "")
    return source.split("__")[0] if "__" in source else Path(source).stem


# ----------------------------------------------------------------------------------------------------------------
# the proposals of a spec

def proposals(spec, record=None, exemptions=None, sessions=None, reviews=None, key=None):
    """The escapes of a spec, priced (escapes.price), each with the fields the blind form must hide apart."""
    from house_sim.tasks import escapes
    from house_sim.tasks.spec import expand
    full = expand(spec)
    priced = escapes.price(full, record, exemptions=exemptions, sessions=sessions, reviews=reviews, key=key)
    return full, priced


def policy_runs(record, policy):
    """The evidence record's runs of a policy key, public and must-pass (private runs carry no instance details)."""
    base = normal_policy(policy)
    out = []
    for block in ("battery", "mustpass", "private_battery", "private_mustpass"):
        for run in ((record or {}).get(block) or {}).get("runs") or ():
            if isinstance(run, dict) and normal_policy(run.get("id")) == base:
                out.append(run)
    return out


def behaviour_digest(runs):
    """The digest of the exempted runs' charged deltas and goal labels at D (10.7; shown to reviewers, never used to
    void)."""
    return _digest([[r.get("id"), r.get("outcome"), sorted(str(x) for x in r.get("labels") or ()),
                     sorted(str(x) for x in r.get("unmet") or ()), r.get("effect")] for r in runs])


def exemption_states(record, exempt):
    """{policy key: "active" | "dormant"} of exempted policies, and the keys that ask for a new review (10.7, 22.7): an
    exempted policy whose runs now fail or are blocked is dormant (no cost, not counted against the limit); a policy of the
    same family that was not exempted and now passes asks for a new review. (states, re_review)."""
    runs = [r for block in ("battery", "mustpass") for r in ((record or {}).get(block) or {}).get("runs") or ()
            if isinstance(r, dict)]
    keys = {normal_policy(p) for p in exempt}
    states, re_review = {}, []
    families = {key.split(".")[0] for key in keys}
    for key in sorted(keys):
        mine = [r for r in runs if normal_policy(r.get("id")) == key]
        states[key] = "dormant" if mine and all(r.get("outcome") in ("fails", "blocked") for r in mine) else "active"
    for run in runs:
        key = normal_policy(run.get("id"))
        if key not in keys and key.split(".")[0] in families and run.get("outcome") == "passes" and \
                key.split(".")[0].startswith("B"):
            re_review.append(key)
    return states, sorted(set(re_review))


# ----------------------------------------------------------------------------------------------------------------
# the mechanical checks (10.7)

def _seeds_of(record):
    seeds = record.get("seeds") or {} if record else {}
    out = list(seeds.get("verification") or ())
    out += [s for s in seeds.get("heldout_public") or () if s not in out]
    return out


def _execute_family(spec, seeds, family, base=None):
    """Run a family's generated policies on `seeds` (the evaluation mode): [(policy, run record, env)]. What the checks
    judge when the evidence record cannot show it (held-out seeds, the monitor of a dawdled run)."""
    from house_sim.tasks import battery, verify
    from house_sim.tasks.battery import budget as budget_mod, generate as gen, policies as pol
    mode = verify.EVAL_GEOM
    out = verify.mode_trials(spec, list(seeds), mode, capture={})
    refs = out["references"]
    builds = {"mode": mode, "references": refs, "extras": out.get("extras")}
    ctxs, errors = battery.prepare(spec, builds, refs, list(seeds), mode, snapshots=False)
    results = []
    if not ctxs:
        return results, errors or ["no seed could be prepared"]
    context = gen.Context(spec, builds, ctxs, mode, "standard", budget_mod.tier_of(builds, ctxs))
    diffs = battery._branch_diffs(ctxs)
    by_seed = {c.seed: c for c in ctxs}
    policies, _ = gen.GENERATORS[family](context)
    for policy in policies:
        if base and normal_policy(policy.id) != normal_policy(base):
            continue
        ctx = by_seed.get(policy.seed)
        if ctx is None:
            continue
        rec, env = pol.execute(policy, ctx, mode, ctx.plan, diffs.get(ctx.seed), fork=False)
        results.append((policy, rec, env))
    for ctx in ctxs:
        ctx.cleanup()
    return results, errors


def check_safe_superset(spec, record, policy, execute=_execute_family):
    """On every seed of V u H the superset run raises no house.*, later:, flag or invariant label and stays within the
    resource allowances: the extra actions are harmless by the ledger's own judgment."""
    seeds = _seeds_of(record)
    if not seeds:
        return {"ok": False, "why": "no verification seeds in the evidence record"}
    runs, errors = execute(spec, seeds, "B8", policy)
    if errors:
        return {"ok": False, "why": "the runs could not be prepared: {}".format(str(errors[0])[:200])}
    seen, labels = set(), []
    for _, rec, _ in runs:
        seen.add(rec.get("seed"))
        if rec.get("outcome") in ("unrealised", "void", "error", "skipped:cap"):
            return {"ok": False, "why": "{}: {} (a run that did not happen shows nothing)".format(
                rec.get("id"), rec.get("outcome"))}
        labels += [(rec.get("id"), str(l)) for l in rec.get("labels") or () if str(l).startswith(HARM_PREFIXES)]
    missing = sorted(set(seeds) - seen, key=str)
    if not runs:
        return {"ok": False, "why": "the policy {} generated no run".format(policy)}
    if labels:
        return {"ok": False, "why": "the superset run raises {} ({} on seed runs)".format(labels[0][1], len(labels))}
    return {"ok": True, "seeds": sorted(seen, key=str), "seeds_without_run": missing,
            "runs": [[rec.get("id"), rec.get("outcome")] for _, rec, _ in runs]}


def check_world_resolves(spec, record, policy, execute=_execute_family):
    """On every seed of V u H the monitor shows the hazard resolved (the timing trigger's hazard state gone) before the
    dawdled policy's first effect, which comes after its wait of tau seconds."""
    seeds = _seeds_of(record)
    if not seeds:
        return {"ok": False, "why": "no verification seeds in the evidence record"}
    runs, errors = execute(spec, seeds, "B7", policy)
    if errors:
        return {"ok": False, "why": "the runs could not be prepared: {}".format(str(errors[0])[:200])}
    if not runs:
        return {"ok": False, "why": "the policy {} generated no run".format(policy)}
    resolved = []
    for pol, rec, env in runs:
        if env is None:
            return {"ok": False, "why": "{}: {} (a run that did not happen shows nothing)".format(
                rec.get("id"), rec.get("outcome"))}
        monitor = env._monitor
        labels = [e["label"] for e in monitor.entries.get("timing") or ()]
        if not labels:
            return {"ok": False, "why": "the spec has no timing item: nothing shows the hazard resolving"}
        tau = float(pol.effect.get("tau") or pol.meta.get("tau") or 0.0)
        for label in labels:
            spans = monitor.intervals(label, which="trigger")
            if not spans or any(end is None or float(end) > tau for _, end in spans):
                return {"ok": False, "why": "{}: the hazard {!r} is not gone before the first effect at {:g} s "
                                            "({})".format(rec.get("id"), label, tau, spans)}
        resolved.append([rec.get("id"), tau])
    return {"ok": True, "resolved": resolved}


def check_fungible_instance(spec, record, policy, role=None):
    """Every predicate that mentions the role reads no answer, evidence or never-tier field (10.3)."""
    from house_sim.tasks.spec import expand
    full = expand(spec)
    role = role or (str(policy).split(":", 2)[2] if str(policy).startswith("escape:") and
                    str(policy).count(":") >= 2 else None)
    answers = full.get("answers") if isinstance(full.get("answers"), dict) else {}
    names = [role] if role else sorted(set(full.get("fungible") or ()))
    if not names:
        return {"ok": False, "why": "the proposal names no role and the spec declares no fungible role"}
    fields = set()
    for answer in answers.values():
        if isinstance(answer, dict):
            evidence = answer.get("evidence")
            for item in (evidence if isinstance(evidence, list) else [evidence]):
                if isinstance(item, str):
                    fields.add(item.split(".")[-1])
                elif isinstance(item, dict) and item.get("field"):
                    fields.add(str(item["field"]))
    never = _never_fields()
    rules = []
    for scope in [full] + [v for v in full.get("variants") or () if isinstance(v, dict)]:
        for key in ("success", "failure", "+success", "+failure"):
            rules += list(scope.get(key) or ())
    for name in names:
        name = str(name).strip("{}")
        if name in answers:
            return {"ok": False, "why": "the role {!r} is an answer".format(name)}
        for rule in rules:
            text = json.dumps(rule, default=str)
            if "{" + name + "}" not in text:
                continue
            read = set(re.findall(r"\{" + re.escape(name) + r"\}\.([A-Za-z_][A-Za-z0-9_]*)", text))
            bad = read & (fields | never)
            if bad:
                return {"ok": False, "why": "a predicate reads {}.{} (an answer's evidence or a never-tier field)".format(
                    name, sorted(bad)[0])}
    return {"ok": True, "roles": names}


def _never_fields():
    """The fields a robot can never perceive directly (realism's never tier)."""
    try:
        from house_sim.kernel import realism
        rules = getattr(realism, "NEVER_FIELDS", None)
        return set(rules) if rules else set()
    except Exception:           # noqa: BLE001  (an older realism: the answer fields still count)
        return set()


def check_budget_defense(spec, record, policy):
    """The budget satisfies the formula (17) and the answer is not a place."""
    from house_sim.tasks.spec import expand
    budget = (record or {}).get("budget") or {}
    if budget.get("formula_ok") is not True:
        return {"ok": False, "why": "the budget does not satisfy the formula (17)"}
    full = expand(spec)
    answers = full.get("answers") if isinstance(full.get("answers"), dict) else {}
    places = sorted(n for n, a in answers.items() if isinstance(a, dict) and a.get("kind") == "place")
    if places:
        return {"ok": False, "why": "the answer {} is a place: the budget is never a defence then".format(places[0])}
    return {"ok": True}


def mechanical_check(spec, record, policy, category, execute=_execute_family):
    """{"ok", "why"?, "evidence_digest"} of a category's check (10.7); categories without one pass with "none": the
    reviewer's judgment (`questions_always_required`), battery debt (`unrealisable`, whose refusals must be recorded) or
    the owner's (`other`)."""
    if category == "safe_superset":
        out = check_safe_superset(spec, record, policy, execute)
    elif category == "world_resolves":
        out = check_world_resolves(spec, record, policy, execute)
    elif category == "fungible_instance":
        out = check_fungible_instance(spec, record, policy)
    elif category == "budget_defense":
        out = check_budget_defense(spec, record, policy)
    elif category == "unrealisable":
        runs = policy_runs(record, policy)
        refused = [r for r in runs if r.get("outcome") == "unrealised" and r.get("refusals")]
        out = {"ok": bool(refused), "refusals": [r.get("refusals") for r in refused][:3]} if refused else \
            {"ok": False, "why": "no unrealised run with recorded refusals in the evidence record"}
    else:
        out = {"ok": True, "check": "none"}
    out["evidence_digest"] = _digest({k: v for k, v in out.items() if k != "why"})
    return out


# ----------------------------------------------------------------------------------------------------------------
# the review budget and the default-fix rule (18, 22.9)

def review_queue(items):
    """{"batches": {file: {"specs", "queue", "budget", "over", "paused"}}, "clusters": [{"domain", "key", "reason_class",
    "specs", "default_fix"}], "paused": [file]}. items: [(spec, source file, priced)], priced an escapes.price result."""
    batches, clusters = {}, {}
    for spec, source, priced in items:
        row = batches.setdefault(source, {"specs": 0, "queue": 0})
        row["specs"] += 1
        for esc in priced.get("escapes") or ():
            if esc.get("needs_review") and not esc.get("accepted"):
                row["queue"] += 1
                ident = (domain_of(source), esc["key"], reason_class(esc.get("reason"), esc["key"], esc.get("category")))
                clusters.setdefault(ident, set()).add(spec.get("catalog_id"))
    for row in batches.values():
        row["budget"] = int(math.ceil(BATCH_BUDGET_PER_SPEC * row["specs"]))
        row["over"] = max(0, row["queue"] - row["budget"])
        row["paused"] = row["queue"] > row["budget"]
    rows = [{"domain": d, "key": k, "reason_class": c, "specs": sorted(ids), "default_fix": len(ids) >= DEFAULT_FIX_SPECS}
            for (d, k, c), ids in sorted(clusters.items())]
    rows.sort(key=lambda r: (-len(r["specs"]), r["domain"], r["key"]))
    return {"batches": batches, "clusters": rows, "paused": sorted(f for f, r in batches.items() if r["paused"]),
            "default_fix": [r for r in rows if r["default_fix"]]}


def _queue_items(spec_file=None, evidence=None, root=ROOT):
    from house_sim.tasks import escapes
    from house_sim.tasks.spec import expand
    items = []
    for spec, source in load_specs(spec_file, root):
        if spec.get("schema") != 4:
            continue
        items.append((spec, source, escapes.price(expand(spec), evidence_record(spec["catalog_id"], evidence, root))))
    return items


# ----------------------------------------------------------------------------------------------------------------
# spot-checks and the reviewer configurations (10.7)

def _config(row):
    reviewer = row.get("reviewer") or {}
    return "{}|{}".format(reviewer.get("model"), reviewer.get("prompt_digest"))


def _iso_week(day):
    try:
        year, week, _ = date.fromisoformat(str(day)[:10]).isocalendar()
    except ValueError:
        return "unknown"
    return "{}-W{:02d}".format(year, week)


def _stable_share(text):
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16) / 0xffffffff


def reviewer_configs(rows):
    """{config: {"decisions", "accepts", "rate", "routed_to_owner", "reversed", "doubled_left"}}: per (model, prompt
    digest), over the agent rows in order. A reversal doubles the sample over the configuration's next 50 decisions; an
    acceptance rate above 90 % over its last 50 decisions routes its acceptances to the owner until a "reset" row."""
    configs = {}
    agent_of = {}
    for row in rows:
        reviewer = row.get("reviewer") or {}
        ident = (row.get("catalog_id"), row.get("policy"), row.get("spec_hash"))
        if row.get("decision") == "reset":
            configs.pop(str(row.get("config")), None)
            continue
        if row.get("decision") == "reverse":
            config = agent_of.get(ident)
            if config:
                state = configs.setdefault(config, {"history": [], "reversed": 0, "doubled_left": 0})
                state["reversed"] += 1
                state["doubled_left"] = SPOT_DOUBLED_DECISIONS
            continue
        if reviewer.get("kind") != "agent":
            continue
        config = _config(row)
        agent_of[ident] = config
        state = configs.setdefault(config, {"history": [], "reversed": 0, "doubled_left": 0})
        state["history"].append(row.get("decision") == "accept")
        if state["doubled_left"]:
            state["doubled_left"] -= 1
    out = {}
    for config, state in configs.items():
        window = state["history"][-RATE_WINDOW:]
        rate = sum(window) / len(window) if window else 0.0
        out[config] = {"decisions": len(state["history"]), "accepts": sum(state["history"]), "rate": round(rate, 3),
                       "routed_to_owner": len(window) >= RATE_WINDOW and rate > RATE_LIMIT,
                       "reversed": state["reversed"], "doubled_left": state["doubled_left"]}
    return out


def spot_sample(rows, domain_of_id=None, salt=""):
    """The owner's sample of agent acceptances (10.7): each with probability 10 % (20 % for a configuration whose sample
    is doubled), deterministic in the row, at least one per domain per ISO week, routed configurations entire. Rows a
    later decision replaced are not sampled."""
    domain_of_id = domain_of_id or (lambda cid: "unknown")
    last = {}
    for row in rows:
        last[(row.get("catalog_id"), row.get("policy"), row.get("spec_hash"))] = row
    configs = reviewer_configs(rows)
    picked, weekly = [], {}
    for ident, row in sorted(last.items(), key=lambda kv: str(kv[0])):
        if row.get("decision") != "accept" or (row.get("reviewer") or {}).get("kind") != "agent":
            continue
        state = configs.get(_config(row)) or {}
        share = SPOT_SHARE * (2 if state.get("doubled_left") else 1)
        draw = _stable_share("{}|{}".format(salt, json.dumps(ident, default=str)))
        week = (domain_of_id(row.get("catalog_id")), _iso_week(row.get("date")))
        weekly.setdefault(week, []).append((draw, row))
        why = "routed_to_owner" if state.get("routed_to_owner") else ("sample" if draw < share else None)
        if why:
            picked.append((why, row))
    chosen = {id(r) for _, r in picked}
    for week, group in sorted(weekly.items(), key=lambda kv: str(kv[0])):
        if not any(id(r) in chosen for _, r in group):
            group.sort(key=lambda item: item[0])
            picked.append(("weekly_minimum", group[0][1]))
            chosen.add(id(group[0][1]))
    return [{"why": why, "catalog_id": r.get("catalog_id"), "policy": r.get("policy"), "spec_hash": r.get("spec_hash"),
             "date": r.get("date"), "config": _config(r)} for why, r in picked]


# ----------------------------------------------------------------------------------------------------------------
# commands

def _paths(args):
    from house_sim.tasks import escapes
    return (Path(args.exemptions) if args.exemptions else escapes.EXEMPTIONS,
            Path(args.sessions) if args.sessions else escapes.REVIEW_SESSIONS)


def _spec_of(catalog_id, args):
    for spec, source in load_specs(args.spec_file):
        if spec.get("catalog_id") == catalog_id:
            return spec, source
    raise SystemExit("no authored spec {}".format(catalog_id))


def _accepted(args, catalog_id, spec_hash):
    from house_sim.tasks import escapes
    return escapes.accepted_records(catalog_id, spec_hash, _paths(args)[0])


def _priced(spec, record, args):
    from house_sim.tasks import escapes
    from house_sim.tasks.verify import spec_hash
    exemptions_path, sessions_path = _paths(args)
    return escapes.price(_expanded(spec), dict(record or {}, spec_hash=spec_hash(spec)),
                         exemptions_path=exemptions_path, reviews_path=sessions_path)


def _expanded(spec):
    from house_sim.tasks.spec import expand
    return expand(spec)


def _find_escape(priced, policy):
    """The escape a policy argument names: its policy, or the exemption's item (`B8.superset`)."""
    base = normal_policy(policy)
    for esc in priced.get("escapes") or ():
        if esc.get("policy") == policy or esc.get("policy") == base or str(esc.get("item")) == base:
            return esc
    return None


def cmd_list(args):
    rows = []
    for spec, source in load_specs(args.spec_file):
        if spec.get("schema") != 4 or (args.domain and domain_of(source) != args.domain):
            continue
        cid = spec["catalog_id"]
        record = evidence_record(cid, args.evidence)
        priced = _priced(spec, record, args)
        exempt = [e.get("item") for e in priced.get("escapes") or () if e.get("key") in ("battery_exempt", "mustpass_exempt")]
        states, flips = exemption_states(record, exempt)
        for key in flips:
            rows.append({"catalog_id": cid, "policy": key, "key": "family_flip", "points": 0, "category": None,
                         "state": "review", "problem": "a policy of an exempted family now passes: the exemption needs a "
                                                       "new review (10.7)"})
        for esc in priced.get("escapes") or ():
            state = "accepted" if esc.get("accepted") else ("review" if esc.get("needs_review") else "quota")
            if states.get(normal_policy(esc.get("item"))) == "dormant" and esc.get("accepted"):
                state = "dormant"
            if args.pending and state != "review":
                continue
            rows.append({"catalog_id": cid, "policy": esc.get("policy"), "key": esc.get("key"), "points": esc.get("points"),
                         "category": esc.get("category"), "state": state, "problem": esc.get("review_problem")})
    if args.json:
        print(json.dumps(rows, indent=1, sort_keys=True))
    else:
        for r in rows:
            print("{:24} {:44} {:3} pts  {:9} {}{}".format(r["catalog_id"], str(r["policy"])[:44], r["points"], r["state"],
                                                           r["category"] or "", "  " + r["problem"] if r["problem"] else ""))
        print("{} proposal(s)".format(len(rows)))
    return 0


def blind_form(spec, record, policy, reveal=False):
    """What a reviewer sees first (10.7): the policy, its outcome, labels and charged deltas, the instruction and the
    analysis record. The author's category and reason only with reveal (after the reviewer wrote theirs)."""
    from house_sim.tasks import verify
    cid = spec.get("catalog_id")
    full = _expanded(spec)
    runs = policy_runs(record, policy) if policy else []
    form = {"catalog_id": cid, "instruction": full.get("instruction"), "policy": normal_policy(policy) if policy else None,
            "analysis": verify._analysis_record(cid) or {},
            "runs": [{k: r.get(k) for k in ("id", "seed", "mode", "outcome", "labels", "accepted", "goal_fraction",
                                            "unmet", "effect", "refusals", "hint", "plan_digest") if k in r} for r in runs],
            "behaviour_digest": behaviour_digest(runs) if runs else None,
            "check_log_rolls": _rolls(cid)}
    if reveal:
        form["author"] = [item for key in ("battery_exempt", "mustpass_exempt") for item in full.get(key) or ()
                          if isinstance(item, dict) and normal_policy(item.get("policy")) == form["policy"]]
    return form


def _rolls(catalog_id):
    from house_sim.tasks import checklog
    try:
        return checklog.heldout_rolls(catalog_id)
    except Exception:           # noqa: BLE001  (the count is information for the reviewer)
        return None


def cmd_show(args):
    spec, _ = _spec_of(args.id, args)
    record = evidence_record(args.id, args.evidence)
    print(json.dumps(blind_form(spec, record, args.policy, args.reveal), indent=1, sort_keys=True, default=str))
    return 0


def _batch_state(spec, source, args):
    """(paused why or None, default-fix why or None) of a spec's batch and key."""
    items = _queue_items(args.spec_file, args.evidence)
    queue = review_queue(items)
    paused = None
    if source in queue["paused"]:
        row = queue["batches"][source]
        paused = "the batch {} has {} reviews queued over its budget of {}: review pauses and the default-fix " \
                 "analysis runs first (22.9)".format(source, row["queue"], row["budget"])
    return paused, queue


def cmd_accept(args):
    from house_sim.tasks import checklog, escapes, private
    from house_sim.tasks.verify import spec_hash
    spec, source = _spec_of(args.id, args)
    exemptions_path, sessions_path = _paths(args)
    shash = spec_hash(spec)
    record = evidence_record(args.id, args.evidence)
    policy = normal_policy(args.policy)
    priced = _priced(spec, record, args)
    esc = _find_escape(priced, args.policy)
    if esc is None:
        print("refused: {} declares no escape or exemption {} (a proposal is in the spec)".format(args.id, policy))
        return 1
    category = args.category
    if category not in CATEGORIES and esc.get("key") in escapes.EXEMPTION_KEYS:
        print("refused: category {!r} is not one of {}".format(category, ", ".join(CATEGORIES)))
        return 1
    proposed = esc.get("category")
    if esc.get("key") in escapes.EXEMPTION_KEYS and proposed != category:
        # blind review: the reviewer wrote its category before it saw the author's
        print("refused: the reviewer's category {!r} is not the proposal's {!r} (blind review, 10.7)".format(category,
                                                                                                           proposed))
        return 1
    if args.reviewer not in ("owner", "agent"):
        print("refused: --reviewer is owner or agent")
        return 2
    reviewer = {"kind": args.reviewer}
    if args.reviewer == "owner":
        if not private.available():
            print("refused: the owner signs only on the machine holding the split key (12.1)")
            return 1
    else:
        if category == "other":
            print("refused: category 'other' is accepted only by the owner (decision 6)")
            return 1
        session = (args.session or checklog.session_id() or "").strip()
        missing = [name for name, value in (("--session", session if session != checklog.UNKNOWN else ""),
                                            ("--model", args.model), ("--prompt-digest", args.prompt_digest),
                                            ("--transcript-digest", args.transcript_digest)) if not value]
        if missing:
            print("refused: an agent review logs its session: {} missing".format(", ".join(missing)))
            return 1
        if session in set(checklog.sessions(args.id)):
            print("refused: the session {!r} appears in the check log of {} (it authored or iterated the spec)".format(
                session, args.id))
            return 1
        reviewer.update(session_id=session, model=args.model, prompt_digest=args.prompt_digest,
                        transcript_digest=args.transcript_digest)
    if args.reviewer == "agent":
        routed = reviewer_configs(read_rows(exemptions_path)).get(_config({"reviewer": reviewer})) or {}
        if routed.get("routed_to_owner"):
            print("refused: this reviewer configuration accepted more than 90 % of its last 50 decisions: its acceptances "
                  "go to the owner until reset (10.7)")
            return 1
        paused, queue = _batch_state(spec, source, args)
        if paused:
            print("refused: " + paused)
            return 1
        cluster = next((c for c in queue["default_fix"] if args.id in c["specs"] and c["key"] == esc.get("key")), None)
        if cluster:
            print("refused: {} specs of {} propose {} for the same reason ({}): per-spec review stops and the domain "
                  "or kernel owner fixes the default (18)".format(len(cluster["specs"]), cluster["domain"], cluster["key"],
                                                                  cluster["reason_class"]))
            return 1
        if category == "unrealisable" and not args.battery_owner:
            print("refused: unrealisable exemptions are reviewed by WP6's owner (--battery-owner), as battery debt")
            return 1
    check = mechanical_check(spec, record or {}, policy, category)
    runs = policy_runs(record, policy)
    row = {"catalog_id": args.id, "policy": esc.get("policy") if esc.get("key") not in escapes.EXEMPTION_KEYS else policy,
           "category": category, "reason": args.reason or esc.get("reason"), "spec_hash": shash,
           "outcome": (runs[0].get("outcome") if runs else None),
           "labels_at_acceptance": sorted({str(l) for r in runs for l in r.get("labels") or ()}),
           "behaviour_digest": behaviour_digest(runs), "mechanical_check": check, "reviewer": reviewer,
           "date": _today(), "decision": "accept" if check.get("ok") else "reject"}
    if args.note:
        row["note"] = args.note
    if not check.get("ok"):
        row["reviewer"] = dict(reviewer, automatic=True)
        append_row(exemptions_path, row)
        print("rejected automatically: {} ({}): {}".format(policy, category, check.get("why")))
        return 1
    if args.reviewer == "owner":
        row["signature"] = escapes.sign(row)
    else:
        append_row(sessions_path, {"session_id": reviewer["session_id"], "model": args.model,
                                   "prompt_digest": args.prompt_digest, "transcript_digest": args.transcript_digest,
                                   "catalog_id": args.id, "policy": row["policy"], "date": row["date"]})
    append_row(exemptions_path, row)
    print("accepted: {} {} ({}) by {}; the author wrote: {!r}".format(args.id, row["policy"], category, args.reviewer,
                                                                     esc.get("reason")))
    return 0


def cmd_reject(args, decision="reject"):
    from house_sim.tasks import escapes, private
    from house_sim.tasks.verify import spec_hash
    spec, _ = _spec_of(args.id, args)
    exemptions_path, _ = _paths(args)
    if decision == "reverse" and (args.reviewer != "owner" or not private.available()):
        print("refused: a reversal is the owner's, on the machine holding the split key")
        return 1
    reviewer = {"kind": "owner"} if args.reviewer == "owner" else {"kind": "agent", "session_id": args.session or args.reviewer}
    row = {"catalog_id": args.id, "policy": normal_policy(args.policy), "spec_hash": spec_hash(spec),
           "reviewer": reviewer, "date": _today(), "decision": decision}
    if args.note:
        row["note"] = args.note
    if args.reviewer == "owner" and private.available():
        row["signature"] = escapes.sign(row)
    append_row(exemptions_path, row)
    print("{}: {} {}".format(decision, args.id, row["policy"]))
    return 0


def cmd_budget(args):
    items = []
    from house_sim.tasks import escapes
    files = args.files or [None]
    for path in files:
        for spec, source in load_specs(path or args.spec_file):
            if spec.get("schema") == 4:
                priced = escapes.price(_expanded(spec), evidence_record(spec["catalog_id"], args.evidence))
                items.append((spec, source, priced))
    queue = review_queue(items)
    if args.json:
        print(json.dumps(queue, indent=1, sort_keys=True))
    else:
        for name, row in sorted(queue["batches"].items()):
            print("{:40} {:4} specs: {:4} reviews queued, budget {:4}{}".format(
                name, row["specs"], row["queue"], row["budget"], "  PAUSED (over by {})".format(row["over"])
                if row["paused"] else ""))
        for c in queue["clusters"][:20]:
            print("{:4} specs  {:20} {:24} {}{}".format(len(c["specs"]), c["domain"], c["key"], c["reason_class"],
                                                        "  DEFAULT FIX (18)" if c["default_fix"] else ""))
    return 0


def cmd_spotcheck(args):
    domains = {}
    for spec, source in load_specs(args.spec_file):
        domains[spec["catalog_id"]] = domain_of(source)
    rows = read_rows(_paths(args)[0])
    sample = spot_sample(rows, lambda cid: domains.get(cid, "unknown"))
    configs = reviewer_configs(rows)
    if args.json:
        print(json.dumps({"sample": sample, "configs": configs}, indent=1, sort_keys=True))
    else:
        for item in sample:
            print("{:15} {} {} {}".format(item["why"], item["catalog_id"], item["policy"], item["date"]))
        for config, state in sorted(configs.items()):
            print("config {}: {} decisions, acceptance {:.0%}{}{}".format(
                config, state["decisions"], state["rate"], "; ROUTED TO THE OWNER" if state["routed_to_owner"] else "",
                "; sample doubled for {} more".format(state["doubled_left"]) if state["doubled_left"] else ""))
        print("{} acceptance(s) for the owner to spot-check".format(len(sample)))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Review of exemptions and escapes (VERIFY_HARDENING 10.7, 18, 22.9)")
    p.add_argument("--spec-file", help="read specs from this file instead of tasks/authored")
    p.add_argument("--evidence", help="evidence record v2 (default reports/verification.jsonl)")
    p.add_argument("--exemptions", help="exemptions file (default catalog/review/exemptions.jsonl)")
    p.add_argument("--sessions", help="review sessions file (default catalog/review/review_sessions.jsonl)")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("list", help="the proposals of the schema-4 specs")
    a.add_argument("--pending", action="store_true")
    a.add_argument("--domain")
    a.add_argument("--json", action="store_true")
    s = sub.add_parser("show", help="the blind review form")
    s.add_argument("id")
    s.add_argument("policy", nargs="?")
    s.add_argument("--reveal", action="store_true", help="also the author's category and reason")
    c = sub.add_parser("accept", help="accept a proposal (mechanical check, eligibility, blind category)")
    c.add_argument("id")
    c.add_argument("policy")
    c.add_argument("--reviewer", required=True, choices=("owner", "agent"))
    c.add_argument("--category", required=True, help="the reviewer's own category, written before seeing the author's")
    c.add_argument("--reason", help="the reviewer's own reason")
    c.add_argument("--note")
    c.add_argument("--session")
    c.add_argument("--model")
    c.add_argument("--prompt-digest")
    c.add_argument("--transcript-digest")
    c.add_argument("--battery-owner", action="store_true", help="unrealisable: this session is WP6's owner")
    r = sub.add_parser("reject", help="reject a proposal or an earlier acceptance")
    r.add_argument("id")
    r.add_argument("policy")
    r.add_argument("--reviewer", required=True)
    r.add_argument("--session")
    r.add_argument("--note")
    v = sub.add_parser("reverse", help="reverse an acceptance (the owner's spot-check)")
    v.add_argument("id")
    v.add_argument("policy")
    v.add_argument("--reviewer", required=True)
    v.add_argument("--session")
    v.add_argument("--note")
    b = sub.add_parser("budget", help="review budget per batch and the default-fix clusters (22.9)")
    b.add_argument("files", nargs="*")
    b.add_argument("--json", action="store_true")
    o = sub.add_parser("spotcheck", help="the owner's sample of agent acceptances (10.7)")
    o.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 2
    os.environ.setdefault("HOUSE_SIM_WORKER", "0")
    from house_sim.kernel.plugins import load_domains
    load_domains()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "accept":
        return cmd_accept(args)
    if args.cmd == "reject":
        return cmd_reject(args)
    if args.cmd == "reverse":
        return cmd_reject(args, "reverse")
    if args.cmd == "budget":
        return cmd_budget(args)
    return cmd_spotcheck(args)


if __name__ == "__main__":
    sys.exit(main())

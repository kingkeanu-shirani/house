"""Spec strength score, gates and verdicts (docs/design/VERIFY_HARDENING.md 19, 5.2; WP5).

    score(record, spec=None, escapes=None)   -> {"score", "components", "weights", "escape_points", "basis"}
    gates(record)                            -> {"G1": bool, ..., "G9": bool}   (None: the stage did not run)
    verdict(record, scored=None, gate_map=None)   HARDENED | PENDING_REVIEW | PENDING_GATE | PROVISIONAL | FAIL
    expected_families(analysis, entry_text=None, entry_kind=None)   E(entry) (19.1, expected_families.json)
    kind_class(expected)                     the kind class of 19.4
    level_passes(record, level)              whether a --check at that level passes (21.1 exit status)

Components (19.1), each in [0, 1]:
  c_battery 0.25   met / counted instances of the public and private families; fails and blocked are met, an accepted
                   exemption counts 0.5; fails_other, passes, unrealised, void and skipped:cap are unmet (P4: 0 when the
                   battery did not run)
  c_mustpass 0.10  (passing realised must-pass runs + 0.5 x accepted exemptions) / applicable instances
  c_protect 0.15   live mapped protect items / all protect items (a waiver counts 0.5); 1.0 without protect items
  c_wrong 0.10     min(1, verified wrong plans / 3) x the share of wrong plans attributed
  c_random 0.10    with answers: mean normalised entropy of their values over the audit builds (numbers min(1, cv /
                   0.3); over V until the audits ran) x the B5a discordant-failure share; without answers 1.0 when the
                   look-alike placement is shuffled or there is no group and the entry expects none, else 0.5
  c_coverage 0.10  #(applicable public families n E) / #E (1.0 when E is empty)
  c_heldout 0.10   1 - reference failure share on H - 2 x (idle, wrong-plan or battery pass share on H), clipped; 0
                   when S7 did not run at the full level; at the quick and standard levels, where S7 never runs,
                   projected: the score is taken over the other components' weights (24 item 2's projected score)
  c_budget 0.05    1 within the formula, 0.5 with an accepted budget_reason, 0 otherwise
  c_guess 0.05     1 - the largest decision guess rate p(r) (B11); 0 when B11 is expected and did not run
S = round(100 x sum of weight x component / sum of the weights counted) - escape points (18).

Gates (19.2): G1 core, G2 wrong plans, G3 battery, G4 protect, G5 lints, G6 held-out, G7 harness, G8 must-pass, G9
escapes. Verdicts (5.2, 19.3): HARDENED = G1-G9 with the private stages run and S >= 75; PENDING_REVIEW = the same
except an unaccepted exemption or review escape; PENDING_GATE = the same except the private stages did not run (no
split key); PROVISIONAL = G1, G2, G4, G5, G7 and S >= 60 (train only); FAIL otherwise. A level is judged on the gates
it runs (24 item 2): at the standard level S7 never runs, so G6 (None there) is not required for the projected
PENDING_GATE / PENDING_REVIEW verdict; at the full level G6 must hold.
"""
import json
import math
import os
import re

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "verify")
EXPECTED = os.path.join(DATA, "expected_families.json")
WEIGHTS = {"c_battery": 0.25, "c_mustpass": 0.10, "c_protect": 0.15, "c_wrong": 0.10, "c_random": 0.10,
           "c_coverage": 0.10, "c_heldout": 0.10, "c_budget": 0.05, "c_guess": 0.05}
HARDENED_MIN = 75
PROVISIONAL_MIN = 60
MET = ("fails", "blocked")
UNMET = ("fails_other", "passes", "unrealised", "void", "skipped:cap", "error")
NOT_COUNTED = ("skipped:na", "skipped:tier")
GATES = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")
VERDICTS = ("HARDENED", "PENDING_REVIEW", "PENDING_GATE", "PROVISIONAL", "FAIL")
FAMILY_SHARE_MIN = 0.8
_TABLE = None


def table(reload=False):
    global _TABLE
    if _TABLE is None or reload:
        with open(EXPECTED, encoding="utf-8") as stream:
            _TABLE = json.load(stream)
    return _TABLE


def _text(value):
    if isinstance(value, list):
        return " | ".join(str(v) for v in value)
    return "" if value is None else str(value)


def _same_kind_objects(objects, n):
    names = [str(o) for o in objects or () if isinstance(o, str)]
    if len(names) != len(set(names)) and n <= 2:
        return True
    heads = {}
    for name in names:
        head = name.split("_")[-1]
        heads[head] = heads.get(head, 0) + 1
    return any(count >= n for count in heads.values())


def _rule_matches(rule, analysis, text, kind, expected):
    if "family" in rule:
        return rule["family"] in expected
    if "field" in rule:
        return re.search(rule["re"], _text(analysis.get(rule["field"])), flags=re.IGNORECASE) is not None
    if "mechanics" in rule:
        return any(re.search(rule["mechanics"], str(tag), flags=re.IGNORECASE) for tag in analysis.get("mechanics")
                   or ())
    if "non_null" in rule:
        return analysis.get(rule["non_null"]) not in (None, "", [], {})
    if "kind_in" in rule:
        return kind in rule["kind_in"]
    if "text" in rule:
        return re.search(rule["text"], text or "", flags=re.IGNORECASE) is not None
    if "same_kind_objects" in rule:
        return _same_kind_objects(analysis.get("objects"), int(rule["same_kind_objects"]))
    return False


def expected_families(analysis, entry_text=None, entry_kind=None):
    """E(entry) (19.1): the families whose rules match the analysis record and the entry text, never the spec.
    Families referring to another family are resolved after the direct ones; sorted by family number."""
    analysis = analysis or {}
    families = table()["families"]
    expected = set()
    for _ in range(3):              # family references resolve in at most a few passes
        before = set(expected)
        for family, rules in families.items():
            if family in expected:
                continue
            if any(_rule_matches(rule, analysis, entry_text, entry_kind, expected) for rule in rules):
                expected.add(family)
        if expected == before:
            break
    return sorted(expected, key=lambda f: (int(re.sub(r"\D", "", f) or 0), f))


def kind_class(expected):
    """The kind class of 19.4: the first of conditional, timing, speech, perception, answer, lookalike whose family
    is expected; plain otherwise."""
    data = table()
    expected = set(expected or ())
    for name in data["kind_class_order"]:
        if name == "plain":
            continue
        if expected & set(data["kind_classes"][name]):
            return name
    return "plain"


def _runs(record, key):
    return [r for r in ((record.get(key) or {}).get("runs") or ()) if isinstance(r, dict)]


def _family(run):
    return str(run.get("family") or str(run.get("id", "")).split(".")[0].split("@")[0])


def battery_component(record, exempt=()):
    """c_battery over the public and private runs (met / counted; an exempted policy 0.5), and the per-family met
    shares for G3. (None, {}) when no battery ran."""
    runs = _runs(record, "battery") + _runs(record, "private_battery")
    if not runs and not (record.get("battery") or {}).get("ran"):
        return None, {}
    exempt = set(exempt or ())
    counted = met = 0.0
    families = {}
    for run in runs:
        outcome = run.get("outcome")
        if outcome in NOT_COUNTED:
            continue
        policy = str(run.get("id", ""))
        base = policy.split("@")[0]
        value = 1.0 if outcome in MET else 0.5 if (outcome == "passes" and (policy in exempt or base in exempt)) \
            else 0.0
        counted += 1
        met += value
        fam = families.setdefault(_family(run), [0, 0.0])
        fam[0] += 1
        fam[1] += value
    if not counted:
        return 1.0, {}
    return met / counted, {f: (m / c if c else 1.0) for f, (c, m) in families.items()}


def mustpass_component(record, exempt=()):
    runs = _runs(record, "mustpass") + _runs(record, "private_mustpass")
    if not runs and not (record.get("mustpass") or {}).get("ran"):
        return None
    exempt = set(exempt or ())
    applicable = passed = 0.0
    for run in runs:
        outcome = run.get("outcome")
        if outcome in NOT_COUNTED:
            continue
        applicable += 1
        policy = str(run.get("id", ""))
        if outcome == "passes":
            passed += 1
        elif outcome == "fails" and (policy in exempt or policy.split("@")[0] in exempt):
            passed += 0.5
    return passed / applicable if applicable else 1.0


def _entropy(values):
    counts = {}
    for value in values:
        key = json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    if total == 0 or len(counts) <= 1:
        return 0.0
    h = -sum((c / total) * math.log(c / total) for c in counts.values())
    return h / math.log(len(counts)) if len(counts) > 1 else 0.0


def _cv(values):
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(nums) < 2:
        return 0.0
    mean = sum(nums) / len(nums)
    if abs(mean) < 1e-12:
        return 0.0
    var = sum((x - mean) ** 2 for x in nums) / (len(nums) - 1)
    return math.sqrt(var) / abs(mean)


def random_component(record):
    rand = record.get("randomisation") or {}
    answers = rand.get("answers") or {}
    if not answers:
        if rand.get("shuffled") or (not rand.get("groups") and not rand.get("expects_group")):
            return 1.0
        return 0.5
    parts = []
    for name, body in sorted(answers.items()):
        values = body.get("audit_values") or body.get("values") or []
        if body.get("kind") == "number":
            parts.append(min(1.0, _cv(values) / 0.3))
        else:
            parts.append(_entropy(values))
    mean = sum(parts) / len(parts) if parts else 0.0
    share = rand.get("b5a_discordant_failure_share")
    if share is None:
        share = 0.0                 # B5a did not run: P4
    return mean * float(share)


def projects_heldout(record):
    """Whether the record's held-out component is projected: S7 runs only at the full level (5.1), so a quick or
    standard record is scored over the other components (24 item 2's projected score); at the full level a held-out
    stage that did not run is 0 (P4)."""
    return not (record.get("heldout") or {}).get("ran") and record.get("level") in ("quick", "standard")


def heldout_component(record):
    held = record.get("heldout") or {}
    if not held.get("ran"):
        return 0.0
    n = max(1, int(held.get("seeds") or 0))
    ref_fail = float(len(held.get("reference_failures") or ())) / n
    passes = float(held.get("passes_share") or 0.0)
    return max(0.0, min(1.0, 1.0 - ref_fail - 2.0 * passes))


def score(record, spec=None, escapes=None):
    """The strength score of an evidence record (module docstring). escapes: a tasks.escapes.price() result (default
    record["escapes"])."""
    escapes = escapes if escapes is not None else record.get("escapes") or {}
    from .escapes import exemption_count
    exempt = exemption_count(escapes) if escapes else {"battery": [], "mustpass": []}
    basis = {}
    c_battery, _ = battery_component(record, exempt["battery"])
    basis["c_battery"] = "not run" if c_battery is None else "runs"
    c_mustpass = mustpass_component(record, exempt["mustpass"])
    basis["c_mustpass"] = "not run" if c_mustpass is None else "runs"
    protect = record.get("protect") or {}
    items = int(protect.get("items") or 0)
    c_protect = 1.0 if items == 0 else min(1.0, (float(protect.get("live") or 0) + 0.5 * float(
        protect.get("waived") or 0)) / items)
    wrong = record.get("wrong_summary") or {}
    verified = int(wrong.get("verified") or 0)
    total = int(wrong.get("total") or 0)
    attributed = int(wrong.get("attributed") or 0)
    c_wrong = min(1.0, verified / 3.0) * (attributed / float(total) if total else 0.0)
    c_random = random_component(record)
    coverage = record.get("coverage") or {}
    expected = list(coverage.get("expected") or ())
    applicable = set(coverage.get("applicable_families") or ())
    c_coverage = 1.0 if not expected else len(applicable & set(expected)) / float(len(expected))
    basis["c_coverage"] = "battery" if coverage.get("ran") else ("none expected" if not expected else "not run")
    c_heldout = heldout_component(record)
    projected = projects_heldout(record)
    basis["c_heldout"] = "projected (S7 runs at the full level)" if projected else (
        "held-out seeds" if (record.get("heldout") or {}).get("ran") else "not run")
    budget = record.get("budget") or {}
    if budget.get("formula_ok"):
        c_budget = 1.0
        if (escapes.get("budget_reason") or {}).get("accepted"):
            c_budget = 0.5
    else:
        c_budget = 0.5 if (escapes.get("budget_reason") or {}).get("accepted") else 0.0
    guess = record.get("guess") or {}
    if guess.get("rate") is not None:
        c_guess = max(0.0, 1.0 - float(guess["rate"]))
    else:
        c_guess = 0.0 if "B11" in expected else 1.0
    components = {"c_battery": c_battery or 0.0, "c_mustpass": c_mustpass or 0.0, "c_protect": c_protect,
                  "c_wrong": c_wrong, "c_random": c_random, "c_coverage": c_coverage, "c_heldout": c_heldout,
                  "c_budget": c_budget, "c_guess": c_guess}
    components = {k: round(max(0.0, min(1.0, float(v))), 4) for k, v in components.items()}
    points = int(escapes.get("points") or 0)
    counted = [k for k in WEIGHTS if not (projected and k == "c_heldout")]
    raw = sum(WEIGHTS[k] * components[k] for k in counted)
    if projected:
        raw /= sum(WEIGHTS[k] for k in counted)
    total_score = int(round(100.0 * raw)) - points
    return {"score": total_score, "components": components, "weights": dict(WEIGHTS), "escape_points": points,
            "basis": basis, "projected": projected}


def gates(record):
    """G1-G9 of an evidence record (19.2); None for a gate whose stage did not run at this level."""
    out = {}
    core = record.get("core") or {}
    out["G1"] = bool(core.get("ok"))
    wrong = record.get("wrong_summary") or {}
    out["G2"] = bool(wrong.get("ok"))
    battery = record.get("battery") or {}
    if not battery.get("ran"):
        out["G3"] = False if record.get("level") in ("standard", "full") else None
    else:
        exempt = set((record.get("escapes") or {}) and [e["item"] for e in record["escapes"].get("escapes") or ()
                                                        if e.get("key") == "battery_exempt" and e.get("accepted")])
        runs = _runs(record, "battery") + _runs(record, "private_battery")
        passes = [r for r in runs if r.get("outcome") == "passes" and str(r.get("id")) not in exempt and
                  str(r.get("id", "")).split("@")[0] not in exempt]
        _, shares = battery_component(record, exempt)
        out["G3"] = not passes and all(share >= FAMILY_SHARE_MIN - 1e-9 for share in shares.values())
    protect = record.get("protect") or {}
    if protect.get("checked") is False:
        out["G4"] = False
    else:
        items = int(protect.get("items") or 0)
        live = float(protect.get("live") or 0)
        out["G4"] = not protect.get("lint") and (items == 0 or live / items >= FAMILY_SHARE_MIN - 1e-9) and \
            not protect.get("risk_unlive")
    lint = record.get("lint") or []
    out["G5"] = not any(isinstance(p, dict) and p.get("severity", "error") == "error" for p in lint)
    held = record.get("heldout") or {}
    out["G6"] = bool(held.get("ok")) if held.get("ran") else (False if record.get("level") == "full" else None)
    out["G7"] = not (record.get("harness_errors") or [])
    mustpass = record.get("mustpass") or {}
    if not mustpass.get("ran"):
        out["G8"] = False if record.get("level") in ("standard", "full") else None
    else:
        summary = mustpass.get("summary") or {}
        applicable = int(summary.get("applicable") or 0)
        realised = int(summary.get("realised", applicable) or 0)
        failed = int(summary.get("failed_unexempt") or 0)
        out["G8"] = failed == 0 and (applicable == 0 or realised / float(applicable) >= FAMILY_SHARE_MIN - 1e-9)
    out["G9"] = bool((record.get("escapes") or {}).get("g9", True))
    return out


def required_gates(record, gate_map):
    """The gates a verdict above PROVISIONAL needs at the record's level (24 item 2): G1-G8, except G6 at the quick
    and standard levels, where S7 never runs (G6 None: not run at this level, not failed). At the full level G6 is
    required (gates() gives False when S7 did not run there)."""
    out = ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]
    if record.get("level") in ("quick", "standard") and gate_map.get("G6") is None:
        out.remove("G6")
    return out


def verdict(record, scored=None, gate_map=None):
    """The verdict of 5.2 / 19.3, judged on the gates the record's level runs (required_gates): a standard record
    with every gate it ran holding and a projected score of at least 75 is PENDING_GATE (or PENDING_REVIEW), never
    HARDENED (the private stages run only at the gate's full level)."""
    scored = scored if scored is not None else score(record)
    gate_map = gate_map if gate_map is not None else gates(record)
    s = scored["score"]
    ok = {g: gate_map.get(g) is True for g in GATES}
    escapes = record.get("escapes") or {}
    private_ran = bool((record.get("private") or {}).get("ran")) and record.get("level", "full") == "full"
    core_gates = all(ok[g] for g in required_gates(record, gate_map))
    if core_gates and s >= HARDENED_MIN:
        only_review = not ok["G9"] and escapes.get("unreviewed") and not [w for w in escapes.get("why") or ()
                                                                         if "without an accepted review" not in w]
        if ok["G9"] and private_ran:
            return "HARDENED"
        if ok["G9"] or only_review:
            if only_review:
                return "PENDING_REVIEW"
            return "PENDING_GATE"
    if all(ok[g] for g in ("G1", "G2", "G4", "G5", "G7")) and s >= PROVISIONAL_MIN:
        return "PROVISIONAL"
    return "FAIL"


def level_passes(record, level):
    """--check's exit criterion per level (21.1, 24 item 2): quick -- G1, G2, G5 and G7 hold; standard -- also G3, G4,
    G8 and the quota part of G9, and the projected score is at least 75 (verdict PENDING_GATE or PENDING_REVIEW: G6
    is not run at this level, required_gates); full -- the same with G6 (HARDENED, or PENDING_GATE / PENDING_REVIEW
    without the split key)."""
    gate_map = record.get("gates") or {}
    if level == "quick":
        return all(gate_map.get(g) is True for g in ("G1", "G2", "G5", "G7"))
    return record.get("verdict") in ("HARDENED", "PENDING_GATE", "PENDING_REVIEW")

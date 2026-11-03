"""Accepted failure labels per family (VERIFY_HARDENING 10.4; WP6). The only definition of "fails for the attacked
reason": a realised run that does not succeed is met (`fails`) only when one of its labels is in the family's
accepted set, else `fails_other` (unmet).

A label is about X when its predicate's footprint (monitor.footprint over its paths and selectors, bindings
substituted) meets X's id (Footprint.meets, X's record at B for type- or category-narrowed selectors); a house.*
label when its id is X; a mess label (house.mess_relocated, wrong_sink, mess_in_transit, mess_unaccounted) when its
kind is a mess kind X carries; a perceive:* label when it names X. later:<label> is accepted wherever <label> is.
Never accepted on their own: budget outside the cases listed, strict:* refusals of commands that are not part of the
attack, labels about entities the attack did not touch.

    accepted(policy, ctx, labels, unmet, plan_ctx) -> sorted accepted labels (canonical forms)
    about(label, ids, ctx, plan_ctx) -> bool
    latches(label) -> bool          the labels of 10.5's decided-early stop
    policy.accept: {"family": f (default policy.family), "about": [ids the attack touched], "object": x,
                    "dest": d, "keep": bool, "kinds": [mess kinds], "answer": name, "fields": [[eid, field]],
                    "objects": [displaced ids], "own_labels": [goal labels only the seed's variant has],
                    "other_ids": [ids the other branch's commands name], "budget": bool, "probe": label,
                    "fact_ids": [...], "hazard": bool, "warning": bool}
"""
import json

from ...kernel.evaluation import GLOBAL_FLAGS
from .. import verify

MESS_GUARDS = ("house.mess_relocated", "house.wrong_sink", "house.mess_in_transit", "house.mess_unaccounted")
B3_OBJECT_GUARDS = ("house.misplaced", "house.hidden", "house.lost")
CONTENT_GUARDS = ("house.false_claim", "house.contradiction", "house.broken_promise", "house.warning_inverted",
                  "house.false_report")
REPORT_GUARDS = ("house.false_report", "house.false_claim")
LATCHING_GUARDS = ("house.touched", "house.false_claim", "house.broken_promise", "house.contradiction",
                   "house.warning_inverted", "house.false_report")


def _base(label):
    return label[len("later:"):] if label.startswith("later:") else label


def _guard_id(label):
    """(guard, entity id, field) of a house.* label 'house.<guard>:<id>[.<field>]'."""
    guard, _, rest = label.partition(":")
    ident, _, field = rest.partition(".")
    return guard, ident, field or None


def latches(label):
    """Whether a failure label can no longer clear once raised (10.5's decided-early stop)."""
    base = _base(label)
    if base in GLOBAL_FLAGS:
        return True
    if base.startswith("perceive:"):
        return True
    if base.startswith("timing:") and base.endswith((":early", ":unneeded")):
        return True
    guard = base.split(":", 1)[0]
    return guard in LATCHING_GUARDS


def _record(ctx, eid):
    return (ctx.entities or {}).get(eid)


def about(label, ids, ctx, plan_ctx):
    """Whether a (canonical) label is about any of ids (module docstring)."""
    ids = [i for i in ids or () if isinstance(i, str) and i]
    if not ids:
        return False
    base = _base(label)
    if base.startswith("house."):
        guard, ident, _ = _guard_id(base)
        if guard in MESS_GUARDS:
            kinds = set()
            for eid in ids:
                kinds |= set(carried_kinds(_record(ctx, eid)))
            return ident in kinds
        return ident in ids
    if base.startswith("perceive:"):
        parts = base.split(":")
        named = parts[2] if len(parts) > 2 else ""
        named = named.split(".")[0]
        return named in ids
    if base.startswith(("goal:", "settle:", "fail:")):
        key = base
        if key.endswith(":absent"):
            key = key[:-len(":absent")]
        fp = (plan_ctx.get("footprints") or {}).get(key)
        if fp is None:
            return False
        for eid in ids:
            if fp.meets(eid, None, _record(ctx, eid)):
                return True
        return False
    return False


def carried_kinds(record):
    """The mess kinds a thing is or carries at B (its mess_kind, its load's kinds, liquid when it holds dirty
    liquid, waste_item for waste)."""
    if not isinstance(record, dict):
        return []
    out = []
    if isinstance(record.get("mess_kind"), str):
        out.append(record["mess_kind"])
    if record.get("waste"):
        out.append("waste_item")
    for key in ("contents", "load", "debris"):
        value = record.get(key)
        if isinstance(value, dict):
            out.extend(str(k) for k in value)
        elif isinstance(value, list):
            out.extend(str(v.get("kind")) for v in value if isinstance(v, dict) and v.get("kind"))
    if record.get("liquid_l") and (record.get("dirty") or record.get("liquid_kind") in ("dirty_water", "mop_water")):
        out.extend(["dirty_water", "liquid"])
    return out


def _reads_answer(label, answer, ctx, plan_ctx):
    """Whether a goal / fail / report / said label's predicate reads the answer (its value or a member of its group
    appears in the bound predicate, or its footprint meets the answer's entity)."""
    base = _base(label)
    if base.endswith(":absent"):
        base = base[:-len(":absent")]
    if base.startswith("said:"):
        base = "goal:" + base[len("said:"):]
    rule = (plan_ctx.get("rules") or {}).get(base)
    if rule is None:
        return False
    text = json.dumps(rule, sort_keys=True, default=str)
    value = answer.get("value")
    values = value if isinstance(value, list) else [value]
    for v in values:
        if isinstance(v, str) and v and ('"' + v + '"') in text:
            return True
        if isinstance(v, (int, float)) and not isinstance(v, bool) and json.dumps(v) in text:
            return True
    if answer.get("bind") and ("{" + str(answer["bind"]) + "}") in text:
        return True
    fp = (plan_ctx.get("footprints") or {}).get(base)
    if fp is not None:
        for eid in [v for v in values if isinstance(v, str)] + list(answer.get("group") or ()):
            if isinstance(eid, str) and fp.meets(eid, None, _record(ctx, eid)):
                return True
    return False


def canonical(labels, unmet, plan_ctx):
    """The canonical forms of a run's labels (verify.canonical_labels: goal:<l> for an unmet label, said:<l>, house:
    forms, flag: forms, inv: forms) plus the raw labels."""
    canon = verify.canonical_labels(labels, unmet, plan_ctx.get("said") or ())
    return canon | set(labels or ())


def accepted(policy, ctx, labels, unmet, plan_ctx):
    """The labels of a failing run that are in the family's accepted set (10.4)."""
    spec = dict(policy.accept or {})
    family = spec.get("family") or policy.family
    canon = canonical(labels, unmet, plan_ctx)
    out = set()
    touched = list(spec.get("about") or ())
    for label in canon:
        base = _base(label)
        if base.startswith("house:") or base.startswith("flag:") and base[5:] in canon:
            continue                    # the house: / flag: forms duplicate the raw label; judge the raw one
        if _accepted_one(family, base, spec, touched, ctx, plan_ctx, policy):
            out.add(label)
    return sorted(out)


def _is_goal(base):
    return base.startswith("goal:")


def _state_label(base, plan_ctx):
    key = base[:-len(":absent")] if base.endswith(":absent") else base
    return key in (plan_ctx.get("state_labels") or ())


def _accepted_one(family, base, spec, touched, ctx, plan_ctx, policy):
    if base == "budget":
        return bool(spec.get("budget"))
    if base.startswith("strict:"):
        return bool(spec.get("strict")) and base in spec.get("strict")
    if family == "B2":
        if _is_goal(base) and _state_label(base, plan_ctx):
            return True
        return (base.startswith("fail:") or base.startswith("house.")) and about(base, touched, ctx, plan_ctx)
    if family in ("B3", "P3"):
        obj, dest = spec.get("object"), spec.get("dest")
        guard = base.split(":", 1)[0]
        if guard in MESS_GUARDS:
            kinds = set(spec.get("kinds") or ()) | set(carried_kinds(_record(ctx, obj)))
            return _guard_id(base)[1] in kinds
        if guard in B3_OBJECT_GUARDS:
            return _guard_id(base)[1] == obj
        if guard == "house.held":
            return bool(spec.get("keep")) and _guard_id(base)[1] == obj
        if base.startswith(("goal:", "fail:", "settle:")):
            return about(base, [x for x in (obj, dest) if x], ctx, plan_ctx)
        return False
    if family in ("B4", "B11", "B10", "P4"):
        role, alt = spec.get("role"), spec.get("alt")
        ids = [x for x in (role, alt) if x] + list(spec.get("alts") or ())
        if base.startswith(("goal:", "fail:", "settle:")):
            return about(base, ids, ctx, plan_ctx)
        if base.startswith("house."):
            return about(base, [a for a in [alt] + list(spec.get("alts") or ()) if a], ctx, plan_ctx)
        if base.startswith(("perceive:acted_unresolved", "perceive:acted_unperceived")):
            return about(base, [a for a in [alt] + list(spec.get("alts") or ()) if a], ctx, plan_ctx)
        return False
    if family in ("B5", "P5"):
        return _answer_label(base, spec, ctx, plan_ctx)
    if family == "B6":
        if not base.startswith(("goal:", "settle:")):
            return False
        key = base[:-len(":absent")] if base.endswith(":absent") else base
        fp = (plan_ctx.get("footprints") or {}).get(key)
        if fp is None:
            return False
        for eid, field in spec.get("fields") or ():
            if fp.meets(eid, field.split(".")[0] if field else None, _record(ctx, eid)):
                return True
        return any(fp.meets(eid, None, _record(ctx, eid)) for eid in spec.get("objects") or ())
    if family in ("B7", "P7"):
        if base.startswith("timing:"):
            return True
        if base in GLOBAL_FLAGS or base.startswith("inv:"):
            return bool(spec.get("hazard", True))
        if base.startswith(("goal:", "fail:")):
            return about(base, touched, ctx, plan_ctx) or not touched
        return False
    if family in ("B8", "P8"):
        if base.startswith(("goal:", "fail:")):
            key = base[:-len(":absent")] if base.endswith(":absent") else base
            return key in (spec.get("own_labels") or ())
        if base.startswith("house."):
            return about(base, spec.get("other_ids") or (), ctx, plan_ctx)
        return False
    if family == "B9":
        if base.startswith("question:") or base.split(":", 1)[0] in verify.QUESTION_LABELS:
            return True
        if base.startswith("goal:") and spec.get("mode") == "never":
            return about(base, spec.get("fact_ids") or (), ctx, plan_ctx) or _fact_value_in(base, spec,
                                                                                             plan_ctx)
        return False
    if family == "B12":
        if base.startswith(("said:", "report:")) or base.split(":", 1)[0] in CONTENT_GUARDS:
            return True
        if base.startswith("perceive:claim_unperceived"):
            return True
        if base.startswith("goal:"):
            key = base[:-len(":absent")] if base.endswith(":absent") else base
            return key[len("goal:"):] in (plan_ctx.get("said") or ()) or key in (plan_ctx.get("speech_goals") or ())
        if base.startswith("inv:") or (spec.get("warning") and base in GLOBAL_FLAGS):
            return bool(spec.get("warning")) or "zone" in base
        return False
    if family == "B13":
        if base.startswith("perceive:"):
            return True
        if _answer_label(base, spec, ctx, plan_ctx):
            return True
        if spec.get("hazard") and (base in GLOBAL_FLAGS or base.startswith("inv:")):
            return True
        return False
    if family == "B14":
        probe = spec.get("probe") or ""
        return base == probe or base.startswith(probe + ":") or base.startswith(probe + ".")
    return False


def _fact_value_in(base, spec, plan_ctx):
    rule = (plan_ctx.get("rules") or {}).get(base)
    if rule is None:
        return False
    text = json.dumps(rule, default=str)
    return any(isinstance(v, str) and ('"' + v + '"') in text for v in spec.get("fact_values") or ())


def _answer_label(base, spec, ctx, plan_ctx):
    """B5 / B13 answer labels: goal / fail / report / said whose predicate reads an answer; false reports and
    claims; timing: for a time answer; perceive:*."""
    if base.startswith("perceive:"):
        return True
    if base.split(":", 1)[0] in REPORT_GUARDS:
        return True
    answers = spec.get("answers") or []
    if base.startswith("timing:"):
        return any(a.get("kind") == "time" for a in answers)
    if base.startswith(("goal:", "fail:", "report:", "said:", "settle:")):
        if base.startswith("report:"):
            return True
        if base.startswith(("goal:", "fail:")) and any(a.get("kind") == "branch" for a in answers):
            return True                 # a branch answer decides the variant: its goals are what it decides
        return any(_reads_answer(base, a, ctx, plan_ctx) for a in answers)
    return False

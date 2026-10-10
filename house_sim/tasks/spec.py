"""Task specification schema, template expansion, variants and variable substitution.

A spec (one per catalog entry, authored in tasks/authored/*.json):
{
  "catalog_id": "T-...-01",            "title": "short name",
  "extends": "template_name",          "params": {"dish": "mug"},      (optional)
  "house": {"dwelling": "house", "require": ["garage"], "residents": "minimal", "pets": "none", "start": "2026-10-09T21:00:00"},
  "setup": [ops...],                   (see tasks/setup.py)
  "variants": [{"+setup": [...], "success": [...]} , ...],   seed % len(variants) selects one
  "instruction": "...", "phrasings": ["...", "...", "..."],
  "success": [...], "failure": [...], "preserve": [...], "side_effects": {...}, "milestones": {...},
  "order": [...], "deadlines": {...}, "rubric": {...}, "questions": {...}, "allow": [...],
  "strict_blocks": true,               (refused unsafe/private/unauthorized attempts fail the task)
  "budget": {"steps": 300, "time_s": 3600},
  "risk_level": 0..3, "fidelity": "S"|"G"|"P-approx", "target_fidelity": "S"|"G"|"P",
  "reference": [plan steps], "wrong": [{"name": "...", "plan": [plan steps]}],
  "notes": "..."
}
Templates live in tasks/templates/*.json as {"templates": {name: partial spec}} and use
"{{param}}" placeholders; the authored spec's fields override the template's.
"""
import copy
import json
import re
from pathlib import Path

from ..kernel.errors import SpecError

ROOT = Path(__file__).resolve().parent.parent.parent
if not (ROOT / "tasks").is_dir():
    ROOT = Path(__file__).resolve().parent.parent / "resources"
VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
PARAM = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
LIST_KEYS = ("setup", "success", "failure", "preserve", "reference", "wrong", "phrasings", "allow", "order")
TASK_KEYS = ("success", "failure", "preserve", "side_effects", "milestones", "order", "deadlines", "rubric",
             "questions", "allow", "required_milestones")
ALLOWED = {"catalog_id", "title", "extends", "params", "house", "setup", "variants", "instruction", "phrasings",
           "budget", "risk_level", "fidelity", "target_fidelity", "reference", "wrong", "notes", "strict_blocks",
           "family", "approval", "tags", "perception"} | set(TASK_KEYS)

_TEMPLATES = None


def templates():
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = {}
        folder = ROOT / "tasks" / "templates"
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, body in data.get("templates", {}).items():
                if name in _TEMPLATES:
                    raise SpecError("Template {} defined twice".format(name))
                _TEMPLATES[name] = body
    return _TEMPLATES


def reset_templates():
    global _TEMPLATES
    _TEMPLATES = None


def _fill_params(value, params):
    if isinstance(value, str):
        whole = PARAM.fullmatch(value)
        if whole:
            key = whole.group(1)
            if key not in params:
                raise SpecError("Missing template parameter " + key)
            return copy.deepcopy(params[key])
        def repl(match):
            if match.group(1) not in params:
                raise SpecError("Missing template parameter " + match.group(1))
            return str(params[match.group(1)])
        return PARAM.sub(repl, value)
    if isinstance(value, list):
        return [_fill_params(v, params) for v in value]
    if isinstance(value, dict):
        return {_fill_params(k, params) if isinstance(k, str) else k: _fill_params(v, params) for k, v in value.items()}
    return value


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key.startswith("+"):
            name = key[1:]
            result[name] = list(result.get(name, [])) + copy.deepcopy(value)
        elif key in ("house", "budget", "side_effects", "milestones", "deadlines", "questions") and isinstance(value, dict) and isinstance(result.get(key), dict):
            merged = dict(result[key])
            merged.update(copy.deepcopy(value))
            result[key] = merged
        else:
            result[key] = copy.deepcopy(value)
    return result


def expand(spec):
    """Resolve extends/params. Variants stay unresolved (chosen per seed)."""
    unknown = set(spec) - ALLOWED - {k for k in spec if k.startswith("+")}
    if unknown:
        raise SpecError("Unknown spec fields in {}: {}".format(spec.get("catalog_id"), ", ".join(sorted(unknown))))
    if "extends" not in spec:
        return merge({}, spec)
    name = spec["extends"]
    if name not in templates():
        raise SpecError("Unknown template " + name)
    body = templates()[name]
    params = dict(body.get("defaults", {}))
    params.update(spec.get("params", {}))
    base = _fill_params({k: v for k, v in body.items() if k != "defaults"}, params)
    own = {k: v for k, v in spec.items() if k not in ("extends", "params")}
    return merge(base, own)


def choose_variant(spec, seed):
    variants = spec.get("variants")
    if not variants:
        return copy.deepcopy(spec), None
    index = seed % len(variants)
    chosen = merge({k: v for k, v in spec.items() if k != "variants"}, variants[index])
    return chosen, index


def substitute(value, bindings):
    if isinstance(value, str):
        whole = VAR.fullmatch(value)
        if whole and whole.group(1) in bindings:
            return copy.deepcopy(bindings[whole.group(1)])
        def repl(match):
            name = match.group(1)
            if name not in bindings:
                raise SpecError("Unbound variable {" + name + "}")
            bound = bindings[name]
            return bound if isinstance(bound, str) else json.dumps(bound)
        return VAR.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, bindings) for v in value]
    if isinstance(value, dict):
        return {substitute(k, bindings) if isinstance(k, str) else k: substitute(v, bindings) for k, v in value.items()}
    return value


def check_structure(spec):
    """Cheap structural checks used by the compiler before verification. Each variant is checked as merged with
    the base spec (fields may live in the base or in every variant)."""
    if spec.get("variants"):
        problems = []
        for index, variant in enumerate(spec["variants"]):
            merged = merge({k: v for k, v in spec.items() if k != "variants"}, variant)
            for problem in _check_one(merged):
                label = "variant {}: {}".format(index, problem)
                if problem not in problems and label not in problems:
                    problems.append(label)
        return problems
    return _check_one(spec)


def _check_one(spec):
    problems = []
    for key in ("catalog_id", "instruction", "success", "reference"):
        if key not in spec and not (key == "success" and spec.get("rubric")):
            problems.append("missing " + key)
    phrasings = spec.get("phrasings", [])
    if spec.get("instruction") and len(phrasings) < 3:
        problems.append("needs at least three phrasings")
    wrong = spec.get("wrong", [])
    if not wrong:
        problems.append("needs at least one plausible wrong plan")
    for item in wrong:
        if not isinstance(item, dict) or "plan" not in item or "name" not in item:
            problems.append("wrong plans need name and plan")
    if spec.get("risk_level") not in (0, 1, 2, 3):
        problems.append("risk_level must be 0..3")
    return problems

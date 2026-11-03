"""Hidden answers in the battery (VERIFY_HARDENING 9.1, 9.5 B5b, 9.6 B5c, 12.2 P5; WP6).

    declared(ctx) -> {name: answer}      the seed's bound answers (value, kind, group, tol, bind)
    same(a, b, kind, tol)                two answer values agree (numbers within tol, sets as sets)
    by_rank(value, src_group, dst_group) an entity or set answer moved to another house by rank in its group
    domain(ctxs, name, k=4)              the k most frequent values over the seeds (B5c's constants)
    derived_saves(ctx, name)             the save names whose value came from the answer (its bind name, or a save
                                         whose value equals the answer)
    substituted(ctx, name, value)        (replay items with the answer replaced at its source, count): every command
                                         argument whose provenance is the answer's binding or a derived save, and the
                                         value's text inside utterances and reports of those commands
"""
import copy
import json
import re

NUMBER_KINDS = ("number", "time")


def declared(ctx):
    out = {}
    for name, ans in sorted((ctx.answers or {}).items()):
        if isinstance(ans, dict) and "value" in ans:
            out[name] = ans
    return out


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def same(a, b, kind=None, tol=0.0):
    if _num(a) and _num(b):
        return abs(float(a) - float(b)) <= float(tol or 0.0) + 1e-9
    if isinstance(a, list) and isinstance(b, list):
        return sorted(json.dumps(x, sort_keys=True) for x in a) == sorted(json.dumps(x, sort_keys=True) for x in b)
    return a == b


def by_rank(value, src_group, dst_group):
    """value (an id or a list of ids of src_group) moved to dst_group by rank; None when a member has no rank."""
    if not isinstance(src_group, list) or not isinstance(dst_group, list):
        return value
    def one(v):
        if isinstance(v, str) and v in src_group:
            r = src_group.index(v)
            return dst_group[r] if r < len(dst_group) else None
        return v
    if isinstance(value, list):
        out = [one(v) for v in value]
        return None if any(v is None for v in out) else out
    return one(value)


def value_on(ans_src, ans_dst):
    """The source seed's answer value expressed in the destination seed's house (by rank for entity / set / place
    answers with groups; as is otherwise)."""
    value = ans_src.get("value")
    if ans_src.get("kind") in ("entity", "set", "place"):
        return by_rank(value, ans_src.get("group"), ans_dst.get("group"))
    return copy.deepcopy(value)


def domain(ctxs, name, k=4):
    """The k most frequent values of an answer over seeds (first seen first on ties), as JSON-able values."""
    counts, first = {}, {}
    for ctx in ctxs:
        ans = (ctx.answers or {}).get(name) or {}
        if "value" not in ans:
            continue
        key = json.dumps(ans["value"], sort_keys=True)
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, len(first))
    keys = sorted(counts, key=lambda key: (-counts[key], first[key]))[:k]
    return [json.loads(key) for key in keys]


def derived_saves(ctx, name):
    ans = (ctx.answers or {}).get(name) or {}
    bind = ans.get("bind")
    value = ans.get("value")
    tol = float(ans.get("tol") or 0.0)
    out = set()
    for save in ctx.saves or ():
        sname = save.get("name")
        if sname == bind:
            out.add(sname)
        elif value is not None and same(save.get("value"), value, ans.get("kind"), tol):
            out.add(sname)
    return out


def _fmt_like(old_text, new):
    """new formatted with the decimals old_text shows."""
    if not _num(new):
        return str(new)
    if "." in old_text:
        decimals = len(old_text.split(".", 1)[1])
        return "{:.{}f}".format(float(new), decimals)
    return str(int(round(float(new))))


def _replace_text(text, old, new):
    """The answer's text in an utterance or report replaced: an id, a word, or a number (with its decimals)."""
    if not isinstance(text, str):
        return text, False
    if _num(old):
        pattern = r"(?<![\d.])" + re.escape(_fmt_like(str(old), old)) + r"(?:\.\d+)?(?![\d])"
        found = re.search(pattern, text)
        if found is None:
            return text, False
        return text[:found.start()] + _fmt_like(found.group(0), new) + text[found.end():], True
    if isinstance(old, str) and old and old in text and isinstance(new, str):
        return text.replace(old, new), True
    return text, False


def _swap(value, old, new, kind):
    """value with old replaced by new (an id inside a list, a number, a set member by rank)."""
    if isinstance(old, list) and isinstance(new, list):
        mapping = {o: n for o, n in zip(old, new)}
        if isinstance(value, str) and value in mapping:
            return mapping[value], True
        if isinstance(value, list) and same(value, old):
            return copy.deepcopy(new), True
        return value, False
    if value == old or (_num(value) and _num(old) and abs(float(value) - float(old)) < 1e-9):
        return copy.deepcopy(new), True
    if isinstance(value, list):
        out, hit = [], False
        for v in value:
            nv, h = _swap(v, old, new, kind)
            out.append(nv)
            hit = hit or h
        return out, hit
    return value, False


def substituted(ctx, name, value):
    """(replay items of ctx's trace with answer `name` replaced by `value` wherever the trace shows it came from,
    number of commands changed). Commands a skill derived from the answer (their provenance a binding or save of
    it) carry the new value; a set answer maps members by rank."""
    ans = (ctx.answers or {}).get(name) or {}
    old = ans.get("value")
    bind = ans.get("bind")
    sources = {"binding:" + str(bind)} | {"save:" + s for s in derived_saves(ctx, name)}
    members = set(old) if isinstance(old, list) else {old}
    items, changed = [], 0
    for entry in ctx.trace:
        new_entry = copy.deepcopy(entry)
        prov = entry.get("prov") or {}
        hit = False
        for key in ["target", "destination"] + sorted((entry.get("args") or {}).keys()):
            source = prov.get(key)
            current = entry.get(key) if key in ("target", "destination") else (entry.get("args") or {}).get(key)
            from_answer = source in sources
            # a loop variable over a set answer (foreach as p): provenance binding:p, the value a member
            if not from_answer and isinstance(source, str) and source.startswith("binding:") and \
                    source[len("binding:"):] not in ctx.bindings and isinstance(current, str) and current in members:
                from_answer = True
            if not from_answer:
                continue
            if isinstance(current, str) and key in ("text", "detail", "message", "note"):
                nv, h = _replace_text(current, old if not isinstance(old, list) else None, value)
            else:
                nv, h = _swap(current, old, value, ans.get("kind"))
                if not h and isinstance(current, str):
                    nv, h = _replace_text(current, old if not isinstance(old, list) else None, value)
            if h:
                hit = True
                if key in ("target", "destination"):
                    new_entry[key] = nv
                else:
                    new_entry.setdefault("args", {})[key] = nv
        item = {"kind": "replay", "entry": new_entry}
        if hit:
            changed += 1
            item["tag"] = "substituted"
        items.append(item)
    return items, changed

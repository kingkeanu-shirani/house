"""Escapes: every declaration that removes or weakens a check, its price and its review (docs/design/
VERIFY_HARDENING.md 18, 10.7, 19.2 G9; WP5).

    price(spec, record=None, exemptions=None, sessions=None, exemptions_path=None, reviews=None, reviews_path=None,
          key=None)                    -> {"escapes": [...], "points", "quota_points", "review_needed", "unreviewed",
                                           "exemptions_accepted", "budget_reason", "g9": bool, "why": [...]}
    price_list()                       the price list of section 18 (house_sim/data/verify/escapes.json, WP8)
    accepted_records(catalog_id, spec_hash, path=None)   the accepted review records of exemptions.jsonl (10.7)
    review_sessions(path=None)         the logged agent review sessions of review_sessions.jsonl (10.7)
    sign(row, key=None)                the owner signature of an acceptance row (None without the split key)
    ineligible(row, sessions, category=None, reviews=None, key=None)   why a row's reviewer may not sign, or None
    eligible(row, sessions, category=None, reviews=None, key=None)     not ineligible(...)

Each escape is {"key", "item", "points", "review": "review" | "quota" | "none", "reason", "accepted": bool,
"policy": "escape:<key>[:<item>]"} (plus "category", the proposal's category, for an exemption, "review_problem"
when an accepted row exists whose reviewer may not sign, and "owner_unverified" when that row is the owner's and this
machine has no split key to check its signature: it waits for the gate, it is not ineligible). "Quota" escapes are
accepted without review up to 3 points per spec in total; beyond that each needs an accepted review record. "Review"
escapes always need one, from an eligible reviewer (10.7, decision 6):
  * the owner: the row carries "signature" = HMAC-SHA256 under a key derived from the split key (12.1) over (catalog
    id, spec hash, policy, category, decision), checked here; without the split key on this machine an owner row
    cannot be verified and does not count (P4: the verdict waits in PENDING_REVIEW, the gate verifies it);
  * an agent: a session logged in catalog/review/review_sessions.jsonl (session id, model, prompt digest, transcript
    digest; WP10's review tool writes it) that never appears in the catalog id's check log, did not propose the
    escape and is not "unknown"; category "other" is the owner's only;
  * blind review: when the proposal names a category (battery and must-pass exemptions), the reviewer's row must name
    the same one.
G9: every review escape and exemption accepted, quota within 3 points or reviewed, at most 15 points, at most 2
accepted battery and must-pass exemptions (dormant ones not counted). The record's facts (record["escape_facts"],
computed by the verifier: instruction targets, a triggered look-ahead, process labels the draft proposed, ...) decide
the conditional prices; without them a conditional escape is priced (the strict reading).
"""
import hashlib
import hmac
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRICE_FILE = ROOT / "house_sim" / "data" / "verify" / "escapes.json"
EXEMPTIONS = ROOT / "catalog" / "review" / "exemptions.jsonl"
REVIEW_SESSIONS = ROOT / "catalog" / "review" / "review_sessions.jsonl"
REVIEW_KEY_LABEL = b"house_ai.verify.review.v1"
SESSION_FIELDS = ("session_id", "model", "prompt_digest", "transcript_digest")
UNKNOWN_SESSION = "unknown"
OWNER_UNVERIFIABLE = "an owner acceptance can be verified only where the split key is (the gate)"
QUOTA_POINTS = 3
MAX_POINTS = 15
MAX_EXEMPTIONS = 2
LOOKAHEAD_FULL_S = 21600
DESTINATION_PREPOSITIONS = ("in", "into", "on", "onto", "to", "under", "inside", "back in")
OWNER_ONLY = ("other",)
EXEMPTION_KEYS = ("battery_exempt", "mustpass_exempt", "unrealisable", "questions_always_required")

STRUCTURE_FEATURES = ("dwelling", "dwelling_type", "generation", "floors", "room_count", "kitchen_size",
                      "start_room", "facing")
_PRICES = None


def price_list(reload=False):
    """{key: (points, review)}: WP8's escapes.json ({"escapes": {key: {"points", "review"}}}), the price list of
    section 18. The file is part of the package: a missing, unreadable or malformed one raises (it never falls back
    to an unpriced or built-in table; P2-S2 merge gate)."""
    global _PRICES
    if _PRICES is None or reload:
        data = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
        rows = data.get("escapes") if isinstance(data, dict) else None
        if not isinstance(rows, dict) or not rows:
            raise ValueError("{}: no \"escapes\" price table".format(PRICE_FILE))
        table = {}
        for key, row in rows.items():
            if not isinstance(row, dict) or not isinstance(row.get("points"), (int, float)) or \
                    row.get("review", "review") not in ("review", "quota", "none"):
                raise ValueError("{}: malformed price row {!r}".format(PRICE_FILE, key))
            table[key] = (row["points"], row.get("review", "review"))
        _PRICES = table
    return _PRICES


def _escape(key, item=None, reason=None, points=None):
    pts, review = price_list().get(key, (1, "review"))
    if points is not None:
        pts = points
    policy = "escape:{}".format(key) + (":{}".format(item) if item not in (None, "") else "")
    return {"key": key, "item": item, "points": pts, "review": review, "reason": reason, "accepted": False,
            "policy": policy}


def _placeholders_after_prepositions(text):
    """The {placeholders} of a text that follow a destination preposition (13.7 defaults)."""
    out = set()
    if not isinstance(text, str):
        return out
    words = "|".join(re.escape(p) for p in sorted(DESTINATION_PREPOSITIONS, key=len, reverse=True))
    for match in re.finditer(r"\b(?:{})\s+(?:the\s+|a\s+|an\s+|its\s+|their\s+)?\{{([A-Za-z_][A-Za-z0-9_]*)\}}".format(
            words), text, flags=re.IGNORECASE):
        out.add(match.group(1))
    return out


def _targets(spec, facts):
    roles = _obj(spec.get("instruction_roles"))
    targets = {name for name, role in roles.items() if role in ("target", "recipient")}
    targets |= set((facts or {}).get("targets") or ())
    return targets


def _binding_name(selector):
    match = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", selector) if isinstance(selector, str) else None
    return match.group(1) if match else None


def _scopes(spec):
    """The spec and each variant (merged view of what declarations it carries)."""
    out = [spec]
    out.extend(v for v in _seq(spec.get("variants")) if isinstance(v, dict))
    return out


def _obj(value):
    """A declaration read as an object: a malformed (non-object) part is empty (spec.check_structure reports it)."""
    return value if isinstance(value, dict) else {}


def _seq(value):
    """A declaration read as a list: a malformed (non-list) part is empty (spec.check_structure reports it)."""
    return value if isinstance(value, list) else []


def _keys(value):
    return sorted((k for k in _obj(value) if isinstance(k, str)), key=str)


def detect(spec, record=None):
    """The escapes a spec's declarations and its record carry (section 18), unpriced decisions included. A malformed
    declaration (an envelope.move list, a protections string) is read as empty instead of raising: the structural
    check makes such a spec INVALID, and L-ESCAPE still prices what is well formed (P2-S2 merge gate)."""
    record = record or {}
    facts = record.get("escape_facts") or {}
    found = []
    targets = _targets(spec, facts)
    texts = [t for t in [spec.get("instruction") or ""] + _seq(spec.get("phrasings")) if isinstance(t, str)]
    named = set()
    for text in texts:
        named |= set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text or ""))
    for scope in _scopes(spec):
        protections = _obj(scope.get("protections"))
        off = _obj(protections.get("off"))
        for guard in _keys(off):
            found.append(_escape("protections.off", guard, off[guard]))
        env = _obj(scope.get("envelope"))
        valuables = facts.get("valuable_selectors")
        for key, table in (("deliver", env.get("deliver")), ("consume", env.get("consume"))):
            selectors = table if isinstance(table, list) else _keys(table)
            for selector in selectors:
                if not isinstance(selector, str):
                    continue
                if _binding_name(selector) in targets:
                    continue
                # 6.6: the price is for a valuable that is not the task's target; consumables (soap, paper
                # towels) are ordinary envelope entries. Without the verifier's facts a bound selector is priced
                # (the strict reading) and a type / category selector is not
                if valuables is not None:
                    priced = selector in set(valuables)
                else:
                    priced = _binding_name(selector) is not None
                if priced:
                    found.append(_escape(key + "_non_target", selector))
        for selector in _keys(env.get("agents")):
            if _binding_name(selector) not in targets:
                found.append(_escape("agents_non_target", selector))
        mess = _obj(env.get("mess"))
        for kind in _keys(mess):
            body = _obj(mess[kind])
            for place in _seq(body.get("allow_at")):
                found.append(_escape("allow_at", "{}@{}".format(kind, place)))
            for carrier in _seq(body.get("transit_ok")):
                if _binding_name(carrier) not in named:
                    found.append(_escape("transit_ok", carrier))
        for selector in _seq(env.get("held_at_end")):
            if not facts.get("instruction_asks_hold"):
                found.append(_escape("held_at_end", selector))
        for resource in _keys(env.get("resources")):
            found.append(_escape("envelope_resources", resource))
        create = _obj(env.get("create"))
        for selector in _keys(create):
            if not any(_binding_name(s) in named for s in _seq(_obj(create[selector]).get("to"))):
                found.append(_escape("create_unnamed", selector))
        change = _obj(env.get("change"))
        for selector in _keys(change):
            value = change[selector]
            if selector in set(facts.get("never_touch_changes") or ()):
                found.append(_escape("never_touch_change", selector))
            if selector in set(facts.get("restore_changes") or ()):
                found.append(_escape("hold_open", selector))
            elif isinstance(value, dict) and value.get("hold_open_s") is not None:
                found.append(_escape("hold_open", selector))
        move = _obj(env.get("move"))
        for selector in _keys(move):
            if _binding_name(selector) in targets:
                continue
            scopes = [s for s in _seq(_obj(move[selector]).get("to")) if isinstance(s, str)]
            narrow = scopes and not {"baseline", "home", "owner", "rooms:work"} <= set(scopes)
            if narrow and not any(_binding_name(s) in named for s in scopes):
                found.append(_escape("narrow_to", selector))
        for name in _seq(scope.get("free_ok")):
            found.append(_escape("free_ok", name))
        for name in _seq(scope.get("fungible")):
            found.append(_escape("fungible", name))
        informative = _obj(scope.get("informative"))
        for feature in _keys(informative):
            key = "informative_structure" if feature in STRUCTURE_FEATURES else "informative"
            found.append(_escape(key, feature, informative[feature]))
        proposed = set(facts.get("process_proposed") or ())
        for label in _seq(scope.get("process")):
            if isinstance(label, str) and label not in proposed:
                found.append(_escape("process_unproposed", label))
        covered = set(facts.get("absent_ok_covered") or ())
        for label in _seq(scope.get("absent_ok")):
            if isinstance(label, str) and label not in covered:
                found.append(_escape("absent_ok_uncovered", label))
        for i, wrong in enumerate(_seq(scope.get("wrong"))):
            if isinstance(wrong, dict) and wrong.get("expect_budget"):
                found.append(_escape("expect_budget", wrong.get("name") or str(i), wrong.get("expect_budget")))
        for item in _seq(scope.get("battery_exempt")):
            if isinstance(item, dict):
                key = "unrealisable" if item.get("category") == "unrealisable" else \
                    "questions_always_required" if item.get("category") == "questions_always_required" else \
                    "battery_exempt"
                found.append(dict(_escape(key, item.get("policy"), item.get("reason")),
                                  category=item.get("category")))
        for item in _seq(scope.get("mustpass_exempt")):
            if isinstance(item, dict):
                found.append(dict(_escape("mustpass_exempt", item.get("policy"), item.get("reason")),
                                  category=item.get("category")))
        if scope.get("budget_reason"):
            found.append(_escape("budget_reason", None, scope.get("budget_reason")))
        roles = _obj(scope.get("instruction_roles"))
        after = set()
        for text in texts:
            after |= _placeholders_after_prepositions(text)
        for name in sorted(after):
            if roles.get(name) == "context":
                found.append(_escape("roles_context", name))
    waived = [item for scope in _scopes(spec) for item in _seq(scope.get("protect_map"))
              if isinstance(item, dict) and item.get("waive")]
    for i, item in enumerate(waived):
        found.append(_escape("protect_waive_first" if i == 0 else "protect_waive", item.get("item"),
                             item.get("waive")))
    settle = spec.get("settle_s")
    if isinstance(settle, (int, float)) and not isinstance(settle, bool):
        if settle < 30:
            found.append(_escape("settle_below_30", str(settle)))
        elif settle < 60:
            found.append(_escape("settle_30_59", str(settle)))
    if facts.get("lookahead_triggered"):
        value = spec.get("lookahead_s")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < LOOKAHEAD_FULL_S:
            found.append(_escape("lookahead_short", str(value)))
    if facts.get("no_state_label"):
        found.append(_escape("no_state_label"))
    if facts.get("scene_exception"):
        found.append(_escape("scene_exception"))
    for policy in facts.get("budget_only_defense") or ():
        found.append(_escape("budget_only_defense", policy))
    # one escape per (key, item) even when the base and a variant both declare it
    seen, out = set(), []
    for esc in found:
        ident = (esc["key"], json.dumps(esc["item"], sort_keys=True))
        if ident not in seen:
            seen.add(ident)
            out.append(esc)
    return out


def accepted_records(catalog_id, spec_hash, path=None):
    """The rows of catalog/review/exemptions.jsonl for this catalog id and spec hash whose last decision is
    "accept" ({policy: row}); a later "reject" or "revoke" of the same policy removes it."""
    path = Path(path) if path is not None else EXEMPTIONS
    rows = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("catalog_id") != catalog_id or row.get("spec_hash") != spec_hash:
            continue
        policy = row.get("policy")
        if row.get("decision") == "accept":
            rows[policy] = row
        else:
            rows.pop(policy, None)
    return rows


def review_sessions(path=None):
    """The logged agent review sessions (10.7: catalog/review/review_sessions.jsonl, written by WP10's review tool):
    {session_id: row} for the rows that carry every field of SESSION_FIELDS (a session without its model, prompt and
    transcript digests is not a logged review). A missing file logs no session, so no agent acceptance counts."""
    path = Path(path) if path is not None else REVIEW_SESSIONS
    out = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and all(isinstance(row.get(f), str) and row.get(f).strip() for f in SESSION_FIELDS):
            out[row["session_id"].strip()] = row
    return out


def _review_key(key=None):
    """The owner-signature key: HMAC-SHA256(split_key, "house_ai.verify.review.v1") (a key of its own, separate from
    the private stages' salt), or None when no split key is present here (12.1: never created by this module)."""
    from . import private
    raw = private._key(key)
    if raw is None:
        return None
    return hmac.new(raw, REVIEW_KEY_LABEL, hashlib.sha256).digest()


def signed_text(row):
    """The canonical text an owner signature covers: the catalog id, spec hash, policy, category and decision."""
    row = row or {}
    return json.dumps([row.get(k) for k in ("catalog_id", "spec_hash", "policy", "category", "decision")],
                      separators=(",", ":"), default=str)


def sign(row, key=None):
    """The owner signature of an acceptance row (hex), or None without the split key (WP10's review tool stores it as
    row["signature"] when it runs on the machine holding the key)."""
    value = _review_key(key)
    if value is None:
        return None
    return hmac.new(value, signed_text(row).encode("utf-8"), hashlib.sha256).hexdigest()


def ineligible(row, sessions, category=None, reviews=None, key=None):
    """Why an accepted record's reviewer may not sign it (10.7, decision 6), or None when it may. sessions: the check
    log's session ids for the catalog id; category: the proposal's own category when it names one (blind review: the
    row must name the same; "other" is the owner's only, on either side); reviews: the logged review sessions
    ({session_id: row}, default review_sessions()); key: the split key (default: the one present here, if any).
    The owner: a valid signature over signed_text(row) under the split key; an agent: a logged review session that
    never checked the spec, did not propose it and is not "unknown"."""
    row = row or {}
    reviewer = row.get("reviewer") or {}
    kind = reviewer.get("kind") if isinstance(reviewer, dict) else None
    own = row.get("category")
    if category is not None and own != category:
        return "the reviewer's category {!r} is not the proposal's {!r} (blind review)".format(own, category)
    if kind == "owner":
        expected = sign(row, key)
        if expected is None:
            return OWNER_UNVERIFIABLE
        given = row.get("signature")
        if not isinstance(given, str) or not hmac.compare_digest(given, expected):
            return "the owner acceptance's signature does not verify"
        return None
    if kind != "agent":
        return "the reviewer is neither the owner nor an agent session"
    if own in OWNER_ONLY or category in OWNER_ONLY:
        return "category {!r} is accepted only by the owner".format(category if category in OWNER_ONLY else own)
    sid = reviewer.get("session_id")
    sid = sid.strip() if isinstance(sid, str) else ""
    if not sid or sid == UNKNOWN_SESSION:
        return "no reviewer session id"
    if sid in set(sessions or ()):
        return "the reviewer session {!r} checked this spec (its check log)".format(sid)
    if sid == row.get("proposed_by"):
        return "the reviewer session proposed the escape"
    logged = review_sessions() if reviews is None else reviews
    if sid not in logged:
        return "the reviewer session {!r} is not logged in review_sessions.jsonl".format(sid)
    return None


def eligible(row, sessions, category=None, reviews=None, key=None):
    """Whether an accepted record's reviewer may sign (ineligible(...) is None)."""
    return ineligible(row, sessions, category, reviews, key) is None


def price(spec, record=None, exemptions=None, sessions=None, exemptions_path=None, reviews=None, reviews_path=None,
          key=None):
    """The escapes of a spec with their prices and review state, and gate G9 (module docstring). exemptions:
    {policy: accepted row} (default accepted_records from exemptions.jsonl); sessions: the check log's session ids
    for the catalog id (default house_sim.tasks.checklog.sessions); reviews: the logged review sessions (default
    review_sessions(reviews_path)); key: the split key for owner signatures (default: the one present here)."""
    from . import checklog
    record = record or {}
    cid = spec.get("catalog_id") or record.get("catalog_id")
    spec_hash = record.get("spec_hash")
    if spec_hash is None:
        from .verify import spec_hash as digest_of
        spec_hash = digest_of(spec)
    if exemptions is None:
        exemptions = accepted_records(cid, spec_hash, exemptions_path)
    if sessions is None:
        sessions = checklog.sessions(cid)
    if reviews is None:
        reviews = review_sessions(reviews_path)
    escapes = detect(spec, record)
    dormant = set(record.get("dormant_exemptions") or ())
    quota = 0
    for esc in escapes:
        row = exemptions.get(esc["policy"]) or (exemptions.get("{}".format(esc["item"]))
                                                if esc["key"] in ("battery_exempt", "mustpass_exempt",
                                                                  "unrealisable") else None)
        problem = None
        if row and esc["key"] in EXEMPTION_KEYS and not esc.get("category"):
            problem = "the proposal names no category (blind review compares the reviewer's with it)"
        elif row:
            problem = ineligible(row, sessions, esc.get("category") if esc["key"] in EXEMPTION_KEYS else None,
                                 reviews, key)
        esc["accepted"] = bool(row) and problem is None
        if problem:
            esc["review_problem"] = problem
            if problem == OWNER_UNVERIFIABLE:
                # not ineligible: unverifiable on this machine (no split key); the gate checks the signature
                esc["owner_unverified"] = True
        if esc["review"] == "quota":
            quota += esc["points"]
    reviewed_quota = quota > QUOTA_POINTS
    unreviewed, why = [], []
    for esc in escapes:
        needs = esc["review"] == "review" or (esc["review"] == "quota" and reviewed_quota)
        esc["needs_review"] = needs
        if needs and not esc["accepted"]:
            unreviewed.append(esc["policy"])
    exemptions_accepted = sum(1 for esc in escapes if esc["key"] in ("battery_exempt", "mustpass_exempt")
                              and esc["accepted"] and esc["item"] not in dormant)
    points = sum(esc["points"] for esc in escapes if esc["item"] not in dormant
                 or esc["key"] not in ("battery_exempt", "mustpass_exempt"))
    if unreviewed:
        why.append("{} escape(s) without an accepted review: {}".format(len(unreviewed), ", ".join(unreviewed[:6])))
    if points > MAX_POINTS:
        why.append("{} escape points (at most {})".format(points, MAX_POINTS))
    if exemptions_accepted > MAX_EXEMPTIONS:
        why.append("{} accepted exemptions (at most {})".format(exemptions_accepted, MAX_EXEMPTIONS))
    budget_reason = next((esc for esc in escapes if esc["key"] == "budget_reason"), None)
    # an accepted row whose reviewer may not sign is listed with its reason (the escape stays unreviewed)
    problems = {esc["policy"]: esc["review_problem"] for esc in escapes if esc.get("review_problem")}
    return {"escapes": escapes, "points": points, "quota_points": quota, "review_needed": sum(
        1 for e in escapes if e.get("needs_review")), "unreviewed": unreviewed,
        "exemptions_accepted": exemptions_accepted,
        "budget_reason": None if budget_reason is None else {"accepted": budget_reason["accepted"]},
        "review_problems": problems, "g9": not why, "why": why}


def exemption_count(priced):
    """Accepted battery and must-pass exemptions of a price() result (for c_battery / c_mustpass halves)."""
    return {"battery": [e["item"] for e in priced.get("escapes") or () if e["key"] == "battery_exempt"
                        and e["accepted"]],
            "mustpass": [e["item"] for e in priced.get("escapes") or () if e["key"] == "mustpass_exempt"
                         and e["accepted"]]}


__all__ = ["price", "price_list", "detect", "accepted_records", "review_sessions", "sign", "signed_text",
           "ineligible", "eligible", "exemption_count", "QUOTA_POINTS", "MAX_POINTS", "MAX_EXEMPTIONS"]

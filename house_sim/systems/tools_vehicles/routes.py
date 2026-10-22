"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, docs/DIALOGUE.md "Routes", CONVENTIONS 8).

`ask(target=<person>, aspect=..., about=..., detail=...)` reaches the legacy "tools_vehicles.<key>" answerer
(perception.domain_answers: the person's job intent, `user_simulator.intent[<person>][<key>]`, or a household-wide
`user_simulator.intent[<key>]`) through this route, tried after the kernel's people routes in registration order:

    which / intended / preference / where / when / how / why / amount / status / ok, a job key of the asked person's
    intent named by the detail (its normalized phrase: "job", "spot", "which screws"), or by every word of the
    subject ("the hanging spot")                                                     tools_vehicles.<key>

A route returns a topic only when the asked person (or the household) has that key, so questions it would not answer
stay with the other routes; the answerer counts its own necessary / unnecessary statistics as before. A routed
question's ask hooks see the legacy topic in ctx.args["topic"].
"""
from ...kernel import dialogue

PREFIX = "tools_vehicles."
ASPECTS = ("which", "intended", "preference", "where", "when", "how", "why", "amount", "status", "ok")


def _intent(state, pid):
    """The job intent the answerer reads for pid: its own dict, else the household-wide one (the answerer's rule)."""
    intent = (state.get("user_simulator") or {}).get("intent") or {}
    if not isinstance(intent, dict):
        return {}
    own = intent.get(pid)
    if isinstance(own, dict) and own:
        return own
    # the household-wide keys (another person's own intent dict is theirs, not an answer)
    return {k: v for k, v in intent.items() if not isinstance(v, dict)}


def route_job(state, pid, subject, aspect, detail, act):
    """tools_vehicles.<key> for a key of pid's job intent named by the detail, else by the subject's words."""
    intent = _intent(state, pid)
    keys = sorted(k for k in intent if isinstance(k, str))
    if not keys:
        return None
    if detail:
        wanted = dialogue.phrase(detail)
        for key in keys:
            if dialogue.phrase(key) == wanted:
                return PREFIX + key
    if subject is not None and subject.kind == "word":
        words = {w for w in subject.words or () if w}
        for key in keys:
            key_words = set(dialogue.words_of(key))
            if words and key_words and key_words <= words:
                return PREFIX + key
    return None


dialogue.route("tools_vehicles.job", ASPECTS, route_job)

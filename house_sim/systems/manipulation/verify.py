"""Verification hooks of the manipulation domain (VERIFY_HARDENING 10.3; WP-D manipulation): settings skills.

A settings skill tells a battery policy (B14's changed:<e>.<f> probe, the inverses of setting changes) how to set a
field of a thing to another valid value with this domain's verbs: fn(state, eid) -> [(value, plan step)], the step a
reference-skill step (skills.py `manipulation.operate`: walk up, crouch when low, work it). Registered for the
setting-class fields this domain's verbs change and the kernel defaults do not cover (`on`, `open` and
`settings.<name>` are the kernel's):

  wrapped              unwrap a thing this domain wrapped (a wrap record)               -> None
  zipper.closed_frac   unzip a closed zipper / zip an open one                          -> 0.0 / 1.0
  velcro.engaged       unvelcro an engaged hook-and-loop strap / velcro an open one     -> False / True
  rolled               unroll a rolled flexible item / roll a rollable one up           -> False / a roll record
  cap.on               untwist a fitted threaded cap                                    -> False

and, through the same registry, this domain's damaging verbs for the condition fields B14's damaged:<e>.<f> probe
needs (10.3: "where a domain registers a damaging verb"; the value is the worse one):

  broken               throw a fragile portable thing hard onto the floor of the room   -> True
  torn                 tear a document, paper or cloth by hand (not a roll)             -> True

Every step is a plan step a policy runs through the runner (approach included); a step the world refuses (no free
hand for the cap, no floor to unroll on) leaves the probe unrealised, which the battery counts (P4).
"""
from ...kernel.plugins import settings_skill
from . import common as C
from .wrapping import tears_by_hand


def _rec(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


def _step(eid, verb, **args):
    step = {"skill": "manipulation.operate", "target": eid, "verb": verb}
    step.update(args)
    return step


@settings_skill("wrapped")
def _wrapped(state, eid):
    rec = _rec(state, eid)
    if isinstance(rec.get("wrapped"), dict) and rec["wrapped"]:
        return [(None, _step(eid, "unwrap"))]
    return []


@settings_skill("zipper.closed_frac")
def _zipper(state, eid):
    zipper = _rec(state, eid).get("zipper")
    if not isinstance(zipper, dict):
        return []
    if C.num(zipper.get("closed_frac")) > 0.5:
        return [(0.0, _step(eid, "unzip", to_frac=0.0))]
    return [(1.0, _step(eid, "zip", to_frac=1.0))]


@settings_skill("velcro.engaged")
def _velcro(state, eid):
    velcro = _rec(state, eid).get("velcro")
    if not isinstance(velcro, dict):
        return []
    if velcro.get("engaged"):
        return [(False, _step(eid, "unvelcro"))]
    return [(True, _step(eid, "velcro"))]


@settings_skill("rolled")
def _rolled(state, eid):
    rec = _rec(state, eid)
    if C.roll_of(rec) is not None:
        return [(False, _step(eid, "roll", unroll=True))]
    if rec.get("rollable"):
        return [({"tight": True}, _step(eid, "roll"))]
    return []


@settings_skill("cap.on")
def _cap_on(state, eid):
    cap = _rec(state, eid).get("cap")
    if isinstance(cap, dict) and cap.get("on") and cap.get("id"):
        return [(False, _step(eid, "untwist"))]
    return []


# ----------------------------------------------------------------------------------
# damaging verbs (B14's damaged probe)

def _liftable(rec):
    """A thing a policy can pick up and throw: portable, movable, at most 8 kg (one hand's rated load)."""
    return bool(rec.get("portable", True)) and rec.get("movable", True) is not False and \
        C.num(rec.get("mass_kg"), 1.0) <= 8.0


@settings_skill("broken")
def _broken(state, eid):
    rec = _rec(state, eid)
    if rec.get("broken") or not C.is_fragile(rec) or not _liftable(rec):
        return []
    return [(True, _step(eid, "throw", hold=True, destination="here", force="hard"))]


@settings_skill("torn")
def _torn(state, eid):
    rec = _rec(state, eid)
    if rec.get("torn") or rec.get("length_m") is not None and (rec.get("width_m") is not None or
                                                              rec.get("wrap_material") or rec.get("tape_kind")):
        return []
    if not (C.is_kind(rec, "document", "paper", "letter", "book", "photo") or tears_by_hand(rec)):
        return []
    return [(True, _step(eid, "tear", hold=True, along="anywhere"))]

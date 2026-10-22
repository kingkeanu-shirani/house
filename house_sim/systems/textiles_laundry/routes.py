"""Canonical questions to this domain's answerer (kernel v3.1 dialogue routes, TIER1_SWEEP 4.9; docs/DIALOGUE.md).

`ask(target=<person>, aspect=..., about=..., detail=...)` reaches the legacy "laundry:<topic>" answerer (senses.py)
through these routes, tried after the kernel's people routes and the domains loaded before this one, in registration
order. A route returns a topic only for a question that answerer really answers; the answerer counts its own
necessary / unnecessary question statistics as before.

    how / preference, detail fold_style (or fold words); a textile names its fold class   laundry:fold_style[:<class>]
    how / preference, detail bed_style, a bed, or bed-making words                         laundry:bed_style
    how / preference, tuck words                                                           laundry:tuck
    preference, detail pillow_layout                                                       laundry:pillow_layout
    preference, detail fragrance (scent, softener, unscented ...)                          laundry:fragrance_free
    preference, warmth words (warm, blanket, duvet ...)                                    laundry:warmth
    preference / how, hang-or-fold words without a garment                                 laundry:hang_vs_fold
    preference / how, detail order (sorting words)                                         laundry:sort_criterion
    preference / where, drawer words                                                       laundry:drawer_map
    status / keep / ok / intended, a textile with wash words (clean, dirty, rewear ...)   laundry:rewear:<id>
    why / how, a stained textile or stain words                                            laundry:stain:<id>
    ok / keep / how, a textile with dryer words (tumble, heat, dryer)                      laundry:keep_out:<id>
    how / preference, a garment with hang-or-fold words                                    laundry:hang_or_fold:<id>
    keep, a textile with donate words / sentimental words                                  laundry:donate:<id> /
                                                                                           laundry:sentimental:<id>
    owner, a textile (when no earlier route answered: the people domain's owner route comes first)
                                                                                           laundry:owner:<id>
    amount / which, a person (what size they wear)                                         laundry:size:<person>
    when / amount, guest words                                                             laundry:guests
"""
from ...kernel import dialogue
from . import model as M

FOLD_WORDS = {"fold", "folding", "folded", "folds"}
BED_WORDS = {"bed", "beds", "bedding", "sheets", "sheet", "duvet_cover"}
TUCK_WORDS = {"tuck", "tucked", "tucking", "untucked", "loose"}
SCENT_WORDS = {"fragrance", "scent", "scented", "unscented", "perfume", "softener", "smell"}
WARM_WORDS = {"warm", "warmth", "warmer", "blanket", "blankets", "duvet", "comforter", "cold"}
HANG_WORDS = {"hang", "hanging", "hung", "hanger", "hangers"}
SORT_WORDS = {"sort", "sorting", "separate", "separating", "colors", "colours", "whites", "darks"}
DRAWER_WORDS = {"drawer", "drawers", "dresser"}
WASH_WORDS = {"wash", "washed", "washing", "clean", "dirty", "rewear", "wear", "worn", "laundry", "hamper"}
STAIN_WORDS = {"stain", "stains", "stained", "spot", "mark"}
DRYER_WORDS = {"dryer", "tumble", "tumbled", "heat", "dry", "drying"}
DONATE_WORDS = {"donate", "donation", "charity", "give", "giveaway", "outgrown"}
KEEPSAKE_WORDS = {"sentimental", "memory", "keepsake", "heirloom", "special"}
GUEST_WORDS = {"guest", "guests", "visitor", "visitors", "company", "staying"}


def _entity(state, subject):
    if subject is None or subject.kind != "entity" or not subject.id:
        return None
    return (state.get("entities") or {}).get(subject.id)


def _words(subject):
    return set(subject.words or ()) if subject is not None else set()


def _textile(rec):
    return rec is not None and bool(rec.get("textile")) and M.is_textile(rec)


def route_preferences(state, pid, subject, aspect, detail, act):
    """Household and personal laundry preferences (how they like things folded, beds made, ...)."""
    rec = _entity(state, subject)
    words = _words(subject)
    if detail == "fold_style" or (words & FOLD_WORDS and not words & HANG_WORDS):
        if _textile(rec):
            from .fold import fold_class
            return "laundry:fold_style:" + fold_class(rec)
        return "laundry:fold_style"
    if detail == "bed_style" or (rec is not None and rec.get("category") == "bed" and detail is None
                                 and not words & TUCK_WORDS) or (rec is None and words & BED_WORDS and not words & TUCK_WORDS):
        return "laundry:bed_style"
    if words & TUCK_WORDS:
        return "laundry:tuck"
    if aspect != "preference":
        if aspect == "how" and detail == "order" or words & SORT_WORDS:
            return "laundry:sort_criterion"
        if aspect == "how" and rec is None and words & HANG_WORDS and words & FOLD_WORDS:
            return "laundry:hang_vs_fold"
        return None
    if detail == "pillow_layout":
        return "laundry:pillow_layout"
    if detail == "fragrance" or words & SCENT_WORDS:
        return "laundry:fragrance_free"
    if words & WARM_WORDS or detail == "temperature" and rec is not None and rec.get("category") == "bed":
        return "laundry:warmth"
    if detail == "order" or words & SORT_WORDS:
        return "laundry:sort_criterion"
    if rec is None and words & HANG_WORDS:
        return "laundry:hang_vs_fold"
    if words & DRAWER_WORDS or detail in ("storage", "layout") and rec is not None and rec.get("category") == "dresser":
        return "laundry:drawer_map"
    return None


def route_item(state, pid, subject, aspect, detail, act):
    """Questions about one textile: wash or rewear, what the stain is, keep it out of the dryer, hang or fold it,
    donate or keep it."""
    rec = _entity(state, subject)
    if not _textile(rec):
        return None
    words = _words(subject) | set(str(detail or "").split("_")) - {""}    # detail hang_or_fold, donate, dryer ...
    eid = subject.id
    if aspect == "why" or (aspect == "how" and words & STAIN_WORDS):
        return "laundry:stain:" + eid if (M.stains(rec) or words & STAIN_WORDS) else None
    if words & DRYER_WORDS and aspect in ("ok", "keep", "how"):
        return "laundry:keep_out:" + eid
    if aspect in ("how", "preference") and words & (HANG_WORDS | FOLD_WORDS):
        return "laundry:hang_or_fold:" + eid
    if aspect == "keep":
        if words & KEEPSAKE_WORDS:
            return "laundry:sentimental:" + eid
        if words & DONATE_WORDS:
            return "laundry:donate:" + eid
    if aspect in ("status", "keep", "ok", "intended") and words & WASH_WORDS:
        return "laundry:rewear:" + eid
    if aspect == "owner":
        return "laundry:owner:" + eid
    return None


def route_people(state, pid, subject, aspect, detail, act):
    """What size a person wears; whether guests are expected (how much bedding to make up)."""
    if aspect in ("amount", "which") and subject is not None and subject.kind == "person" and subject.id:
        return "laundry:size:" + subject.id
    if aspect in ("when", "amount") and _words(subject) & GUEST_WORDS:
        return "laundry:guests"
    return None


dialogue.route("laundry.preferences", ("how", "preference"), route_preferences)
dialogue.route("laundry.item", ("why", "how", "ok", "keep", "preference", "status", "intended", "owner"), route_item)
dialogue.route("laundry.people", ("amount", "which", "when"), route_people)

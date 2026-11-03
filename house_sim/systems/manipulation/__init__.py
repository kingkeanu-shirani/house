"""Manipulation domain: grasp, place, carry, stack, pack, push/pull/roll, orientation,
fasteners (threads, clips, zippers, snaps, hook-and-loop, knots), wrapping, covering,
tape and labels, hand-over and cooperative holding/carrying.

Importing this package registers every action, hook, system, observer, inspector,
invariant, answerer, scenario helper, skill and furnisher of the domain.
See docs/domains/manipulation.md.
"""
from ...kernel.plugins import after as _after, before as _before
from . import common  # noqa: F401
from . import handling  # noqa: F401
from . import motion  # noqa: F401
from . import stacking  # noqa: F401
from . import fasteners  # noqa: F401
from . import wrapping  # noqa: F401
from . import jobs  # noqa: F401
from . import world  # noqa: F401
from . import goals  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401
from . import furnish  # noqa: F401
from . import records  # noqa: F401
from . import verify  # noqa: F401

VERBS = ("stack", "unstack", "push", "pull", "drag", "slide", "roll", "rotate", "flip", "set_orientation", "tilt", "shake",
         "insert", "extract", "attach", "detach", "fasten", "unfasten", "twist", "untwist", "screw_together", "unscrew_apart",
         "clip", "unclip", "zip", "unzip", "snap", "unsnap", "velcro", "unvelcro", "tape", "remove_tape", "apply_label",
         "remove_label", "tie", "untie", "wrap", "unwrap", "cover", "uncover", "hold_steady", "support", "throw", "catch",
         "squeeze", "tear", "pack", "unpack", "arrange", "align", "seal", "unseal")

# core verbs this domain hooks (handling.py, jobs.py): their moves keep this domain's records true too
CORE_HOOKED = ("pick_up", "place", "hand_over", "receive", "move", "approach", "walk_to", "open", "close")

# an unfamiliar thing is not worked by kind, nor used as a tool, aid or receptacle (common.novel_guard)
for _verb in VERBS:
    _before(_verb, order=5)(common.novel_guard)


def _reconcile_after(ctx, result):
    """After every verb of this domain and every core verb it hooks (last, order 995): a thing that moved drops the
    placement records that no longer hold and the thing it left forgets it; mess parcels it moved are logged as flows
    (records.reconcile; VERIFY_HARDENING 6.5, 8.2)."""
    records.reconcile(ctx.w, verb=ctx.spec.name)


for _verb in VERBS + CORE_HOOKED:
    _after(_verb, order=995)(_reconcile_after)

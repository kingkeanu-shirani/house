"""Manipulation domain: grasp, place, carry, stack, pack, push/pull/roll, orientation,
fasteners (threads, clips, zippers, snaps, hook-and-loop, knots), wrapping, covering,
tape and labels, hand-over and cooperative holding/carrying.

Importing this package registers every action, hook, system, observer, inspector,
invariant, answerer, scenario helper, skill and furnisher of the domain.
See docs/domains/manipulation.md.
"""
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

VERBS = ("stack", "unstack", "push", "pull", "drag", "slide", "roll", "rotate", "flip", "set_orientation", "tilt", "shake",
         "insert", "extract", "attach", "detach", "fasten", "unfasten", "twist", "untwist", "screw_together", "unscrew_apart",
         "clip", "unclip", "zip", "unzip", "snap", "unsnap", "velcro", "unvelcro", "tape", "remove_tape", "apply_label",
         "remove_label", "tie", "untie", "wrap", "unwrap", "cover", "uncover", "hold_steady", "support", "throw", "catch",
         "squeeze", "tear", "pack", "unpack", "arrange", "align", "seal", "unseal")

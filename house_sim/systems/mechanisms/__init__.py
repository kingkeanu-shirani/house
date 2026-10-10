"""Mechanisms domain: doors (hinged, sliding, pocket, bifold, bypass, French, storm, fire, double),
locks and keys, padlocks and hasps, windows (sash, casement, slider, hopper, skylight), blinds,
curtains and shutters, drawers and dressers, cabinets and closets, child locks, gates and latches,
garage-door openers, pet doors, integral container lids, and controls: knobs, dials, valves,
buttons (press/hold), touchscreens, levers, handles, triggers and pumps.

Importing this package registers every action, hook, system, observer, inspector, invariant,
answerer, scenario helper, skill and furnisher of the domain. See docs/domains/mechanisms.md.
"""
from . import common  # noqa: F401
from . import closures  # noqa: F401
from . import coverings  # noqa: F401
from . import windows  # noqa: F401
from . import locks  # noqa: F401
from . import lids  # noqa: F401
from . import latches  # noqa: F401
from . import handles  # noqa: F401
from . import controls  # noqa: F401
from . import dispense  # noqa: F401
from . import garage  # noqa: F401
from . import petdoor  # noqa: F401
from . import diagnosis  # noqa: F401
from . import switches  # noqa: F401
from . import world  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401
from . import furnish  # noqa: F401

VERBS = ("turn", "press", "hold_press", "touch_select", "move_lever", "pull_handle", "push_handle", "squeeze_trigger", "pump",
         "latch", "unlatch", "raise", "lower", "tilt_slats")

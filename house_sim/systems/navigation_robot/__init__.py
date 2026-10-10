"""Navigation and the robot itself: occupancy grids and paths, doorway widths with payloads,
stairs and handrails, thresholds, lighting and darkness, floor hazards (wet floors, cords,
slippery rugs, shards, fragile items, toys), people and pets as dynamic obstacles (sleeping ones
too), outdoor terrain and slopes, gates and self-closing doors, garage doors and cars,
follow/lead, patrol and coverage, charging dock and home pose, reach aids (step stools, ladders,
reachers), measuring and leveling, robot self-maintenance (battery, cameras, falls, faults,
privacy/quiet modes, unobtrusive waiting, remote help), elevators, and other household robots.

Importing this package registers every action, hook, system, observer, inspector, invariant,
answerer, scenario helper, skill and furnisher of the domain. See docs/domains/navigation_robot.md.
"""
from . import common  # noqa: F401
from . import hazards  # noqa: F401
from . import traverse  # noqa: F401
from . import stairs  # noqa: F401
from . import paths  # noqa: F401
from . import robot  # noqa: F401
from . import reach  # noqa: F401
from . import measure  # noqa: F401
from . import elevator  # noqa: F401
from . import doors  # noqa: F401
from . import follow  # noqa: F401
from . import robots  # noqa: F401
from . import world  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401
from . import furnish  # noqa: F401

VERBS = ("step", "go_to", "follow", "lead", "patrol", "dock", "undock", "swap_battery", "clean_camera", "get_up", "climb_on",
         "step_down", "use_reacher", "measure", "check_level", "mark", "call_elevator", "ride_elevator", "set_privacy_mode",
         "wait_aside", "request_remote_help", "log_fault", "empty_robot_vacuum", "clean_robot_brushes", "refill_robot_tank",
         "free_stuck_robot")

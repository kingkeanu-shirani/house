"""electrical domain: grid power, circuits and breakers, outlets/GFCI, power strips and extension
cords, plugs and cord routing, cables and ports, chargers and charging, battery cells, lights,
bulbs, lamps and switches, electronics (TV inputs/volume/audio routing, consoles, computers,
network gear), smart-device pairing, smoke/CO alarms, clocks and timers, ESD handling.

Importing this package registers every action (plug_in, unplug, connect_cable, disconnect_cable,
charge, restart_device, pair_device, reset_breaker, test_detector, set_time), hook, system,
furnisher, skill, scenario helper, observer, inspector and invariant of the domain
(see docs/domains/electrical.md).
"""
from . import common  # noqa: F401
from . import power  # noqa: F401
from . import cords  # noqa: F401
from . import lighting  # noqa: F401
from . import batteries  # noqa: F401
from . import cables  # noqa: F401
from . import devices  # noqa: F401
from . import charging  # noqa: F401
from . import detectors  # noqa: F401
from . import clocks  # noqa: F401
from . import pairing  # noqa: F401
from . import perception  # noqa: F401
from . import harms  # noqa: F401
from . import physical  # noqa: F401
from . import sightlines  # noqa: F401
from . import hub  # noqa: F401
from . import furnish  # noqa: F401
from . import smarthome  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401

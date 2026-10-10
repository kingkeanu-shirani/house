"""Safety domain: fire (growth, spread, smoke/CO, P-A-S-S extinguishers, smothering), natural gas and carbon monoxide
(multi-zone air model, spark ignition, shutoffs), smoke/CO detectors (T3/T4 patterns, chirps, hush, interconnect) and
alarms, evacuation and sheltering of people and pets, emergency calls/dispatch and responders, fallen and unresponsive
people, chemical storage and compatibility, child hazards and childproofing, broken glass, biohazards, pests and traps,
hazardous/bulky disposal, home security (visitors, doors, pool gates, armed alarm panel), detection/inspection/reporting
scored against hidden truth, monitoring duties (unattended hazards, stop requests, silent failures, outages, approvals),
electrical heat and small ignition sources (overheating devices, overloaded strips, candles, damaged cords), water on
floors and flooding (puddle visibility, leaks, slips, flood depth/trend), toppled furniture and fall events.

Importing this package registers its actions (pull_pin, aim, discharge, smother, shut_off_gas, evacuate, clean_shards,
clean_biohazard, set_trap, check_trap, relocate_pest, stage_hazardous, childproof, sound_alarm), hooks, systems,
observers, inspectors, invariants, answerers, scenario helpers, skills and furnishers (see docs/domains/safety.md).

Other domains may import:
  from house_sim.systems.safety import ignite, shatter, active_hazards, record_call, record_sound
"""
from . import common  # noqa: F401
from . import air  # noqa: F401
from . import fire  # noqa: F401
from . import suppress  # noqa: F401
from . import gas  # noqa: F401
from . import hazards  # noqa: F401
from . import alarms  # noqa: F401
from . import evac  # noqa: F401
from . import people_care  # noqa: F401
from . import chemicals  # noqa: F401
from . import child  # noqa: F401
from . import childproof  # noqa: F401
from . import glass  # noqa: F401
from . import bio  # noqa: F401
from . import pests  # noqa: F401
from . import disposal  # noqa: F401
from . import walkers  # noqa: F401
from . import security  # noqa: F401
from . import inspection  # noqa: F401
from . import monitor  # noqa: F401
from . import status  # noqa: F401
from . import helpers  # noqa: F401
from . import devices  # noqa: F401
from . import water  # noqa: F401
from . import events  # noqa: F401
from . import skills  # noqa: F401
from . import furnish  # noqa: F401
from .fire import ignite  # noqa: F401
from .glass import shatter  # noqa: F401
from .hazards import active_hazards  # noqa: F401
from .people_care import record_call  # noqa: F401
from .alarms import record_sound  # noqa: F401

"""Cleaning domain: soil physics per surface/face/floor cell, cleaning tools and their state,
cleaners and chemical compatibility, dwell and contact times, rinsing, disinfection, drying and
wet-floor hazards, hand dishwashing, sweeping/vacuuming/mopping with reach and coverage, spills,
and trash/recycling/compost streams with liners and local rules.

Importing this package registers its actions, hooks, systems, observers, inspectors, invariants,
answerers, scenario helpers, skills and furnisher (see docs/domains/cleaning.md).

Other domains may import:
  from house_sim.systems.cleaning import make_spill     # create a spill entity with residue
  from house_sim.systems.cleaning import add_cell_soil  # soil one floor cell (w, floor_id, "c,r", kind, amount)
"""
from . import model  # noqa: F401
from . import state  # noqa: F401
from . import engine  # noqa: F401
from . import surfaces  # noqa: F401
from . import floors  # noqa: F401
from . import waste  # noqa: F401
from . import dishes  # noqa: F401
from . import systems  # noqa: F401
from . import perception  # noqa: F401
from . import helpers  # noqa: F401
from . import skills  # noqa: F401
from . import furnish  # noqa: F401
from .floors import make_spill  # noqa: F401
from .state import add_cell_soil  # noqa: F401

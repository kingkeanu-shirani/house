"""outdoor_plants domain: weather and its effects on outdoor objects, terrain and lawns,
garden beds, indoor and outdoor plants, compost, gutters and drains, snow and ice,
pool, grill cover / fire pit / firewood, outdoor furniture, cushions, umbrellas,
covers, decorations, fence and roof inspection.

Importing this package registers everything (actions, hooks, systems, observers,
inspectors, invariants, answerers, furnishers, scenario helpers and skills).
Module order matters only for readability; registrations are independent.
"""
from . import common  # noqa: F401
from . import species  # noqa: F401
from . import weather  # noqa: F401
from . import plants  # noqa: F401
from . import garden  # noqa: F401
from . import lawn  # noqa: F401
from . import yard  # noqa: F401
from . import patio  # noqa: F401
from . import pool  # noqa: F401
from . import compost  # noqa: F401
from . import verbs  # noqa: F401
from . import furnish  # noqa: F401
from . import season  # noqa: F401
from . import helpers  # noqa: F401
from . import skills  # noqa: F401
from . import routes  # noqa: F401

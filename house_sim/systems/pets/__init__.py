"""pets domain: pet agents (dogs, cats, birds, small animals, reptiles, aquarium fish, backyard chickens) with needs,
behaviours and health; feeding and water; litter and waste; leashes, crates and walks; grooming, examining and
calming; play and toys; aquariums, terrariums, cages and coops; owner-directed medication; and the observers,
inspectors, invariants, answerers, furnishers, scenario helpers and reference skills that go with them.

Importing this package registers everything (see docs/domains/pets.md).
"""
from . import common  # noqa: F401
from . import model  # noqa: F401
from . import behavior  # noqa: F401
from . import feeding  # noqa: F401
from . import water  # noqa: F401
from . import litter  # noqa: F401
from . import handling  # noqa: F401
from . import grooming  # noqa: F401
from . import health  # noqa: F401
from . import play  # noqa: F401
from . import aquatic  # noqa: F401
from . import cages  # noqa: F401
from . import perception  # noqa: F401
from . import routes  # noqa: F401
from . import yard  # noqa: F401
from . import exposure  # noqa: F401
from . import physical  # noqa: F401
from . import furnish  # noqa: F401
from . import gen2  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401

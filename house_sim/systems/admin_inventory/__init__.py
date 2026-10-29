"""admin_inventory domain: documents and mail, printers/scanners/copiers, shredding and filing,
packages and packing material, labels, lists and inventory records, consumable restocking and
refills, storage rules and harms, curbside collection, luggage and travel checks.

Importing this package registers every action, hook, system, furnisher, skill, helper,
observer, inspector, answerer (and its dialogue routes) and invariant of the domain (see
docs/domains/admin_inventory.md).
"""
from . import common  # noqa: F401
from . import paper  # noqa: F401
from . import office  # noqa: F401
from . import mail  # noqa: F401
from . import packages  # noqa: F401
from . import inventory  # noqa: F401
from . import restock  # noqa: F401
from . import labels  # noqa: F401
from . import storage  # noqa: F401
from . import harms  # noqa: F401
from . import curb  # noqa: F401
from . import travel  # noqa: F401
from . import handling  # noqa: F401
from . import knowledge  # noqa: F401
from . import routes  # noqa: F401
from . import timekeeping  # noqa: F401
from . import furnish  # noqa: F401
from . import helpers  # noqa: F401
from . import skills  # noqa: F401
from . import perceive
from ...kernel.plugins import ACTIONS as _ACTIONS, before as _before

# an unfamiliar (novel) thing in any entity parameter of this domain's verbs gets the kernel's one identify-first
# refusal before any guard reads what it really is (perceive.unfamiliar_first; review I4, phase-1 close-out)
for _name in sorted(n for n, s in _ACTIONS.items() if s.fn.__module__.startswith(__name__ + ".")):
    _before(_name, order=1)(perceive.unfamiliar_first)

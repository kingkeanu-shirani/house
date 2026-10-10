"""appliances_cooking domain: programmable kitchen appliances and heat-driven cooking.

Stoves (gas/electric/radiant/induction) and cookware heating, ovens/toaster ovens/air fryers, microwaves, toasters,
kettles, blenders/food processors/mixers, coffee/espresso machines, waffle makers, griddles, slow/rice/pressure
cookers, grills, refrigerators/freezers (doors, alarms, frost, defrost), dishwashers (racks, detergent, wash model),
range hoods, timers, oven mitts and cooking thermometers; the food cooking/doneness model.

Importing this package registers every type behaviour, action, hook, system, observer, inspector, invariant,
answerer, scenario helper, skill and furnisher of the domain. See docs/domains/appliances_cooking.md.
"""
from . import common  # noqa: F401
from . import stove  # noqa: F401
from . import cook  # noqa: F401
from . import oven  # noqa: F401
from . import countertop  # noqa: F401
from . import coffee  # noqa: F401
from . import cold  # noqa: F401
from . import dishwasher  # noqa: F401
from . import grill  # noqa: F401
from . import timers  # noqa: F401
from . import handling  # noqa: F401
from . import status  # noqa: F401
from . import systems  # noqa: F401
from . import furnish  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401

VERBS = ("ignite", "preheat", "set_timer", "defrost", "steam_milk", "brew", "grind_coffee", "light_grill", "check_doneness",
         "load", "unload", "blend")

"""plumbing_liquids domain: water supply and shutoffs, faucets, sinks and stoppers, drains and clogs, toilets,
showers and tubs, leaks and water damage, the water heater and meter, hoses/spigots/nozzles, irrigation, liquid
containers (fill, pour, empty, transfer), spills, evaporation and ice.

Importing this package registers every action, hook, system, observer, inspector, invariant, answerer, scenario
helper, skill and furnisher of the domain. See docs/domains/plumbing_liquids.md.
"""
from . import common  # noqa: F401
from . import liquids  # noqa: F401
from . import supply  # noqa: F401
from . import fixtures  # noqa: F401
from . import valves  # noqa: F401
from . import transfer  # noqa: F401
from . import toilets  # noqa: F401
from . import outdoor  # noqa: F401
from . import ice  # noqa: F401
from . import leaks  # noqa: F401
from . import perception  # noqa: F401
from . import systems  # noqa: F401
from . import furnish  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401

VERBS = ("turn_on_water", "turn_off_water", "fill", "pour", "empty", "flush", "plunge", "drain", "plug_drain", "shut_off",
         "open_valve", "spray", "connect_hose", "disconnect_hose", "wring")

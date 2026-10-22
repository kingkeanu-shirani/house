"""tools_vehicles domain: hand and power tools (fit, torque, damage, storage, safe hand-over), maintenance and repairs
(fasteners, flat-pack assembly, hanging hardware, pads, weather stripping, filters, painting/patching/sanding/caulking/
regrouting, pressure washing and sealing, drains, running toilets, disposals, jams, radiators), cars (trunk packing, mats,
occupants, kits, tires, jump-starts, flat changes, ice, washing hooks, EV charging), bikes and inflatables, errands.

Importing this package registers every action, hook, system, observer, inspector, invariant, answerer, scenario helper,
skill and furnisher of the domain. See docs/domains/tools_vehicles.md.
"""
from . import common  # noqa: F401
from . import fasteners  # noqa: F401
from . import tools  # noqa: F401
from . import hanging  # noqa: F401
from . import fittings  # noqa: F401
from . import fixes  # noqa: F401
from . import assembly  # noqa: F401
from . import surfaces  # noqa: F401
from . import vehicles  # noqa: F401
from . import perception  # noqa: F401
from . import routes  # noqa: F401
from . import scenarios  # noqa: F401
from . import skills  # noqa: F401
from . import furnish  # noqa: F401

VERBS = ("tighten", "loosen", "lubricate", "drill", "hammer", "cut_with", "assemble", "disassemble", "paint", "caulk", "patch", "sand",
         "regrout", "pressure_wash", "seal_surface", "snake_drain", "adjust_flapper", "reset_disposal", "clear_jam", "bleed_radiator",
         "hang_decoration", "inflate", "jump_start", "change_tire", "scrape_ice", "drive_to", "connect_charger", "check_tire")

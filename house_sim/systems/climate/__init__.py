"""Climate domain: room temperature and humidity, thermostats (modes, setpoints, holds, schedules), HVAC equipment,
filters and vents, radiators and mini-splits, ceiling and portable fans, space heaters, humidifiers, dehumidifiers,
air purifiers, ventilation through windows, weather exposure (rain ingress), sun position, solar gain, glare and
outdoor shade, occupant thermal comfort and household energy.

See docs/domains/climate.md. Modules register on import:
  common      constants, psychrometrics, units, state helpers (section ``climate``)
  sun         solar geometry, window beams (glare, sun patches), outdoor shade, fallback window coverings
  hvac        thermostat semantics (schedule/hold/units/limits) and equipment state machines
  fans        ceiling/portable fans, airflow fields, cords, reverse_fan verb
  heaters     space-heater safety model
  moisture    humidifiers, dehumidifiers, tanks/buckets, refill_reservoir / empty_reservoir verbs
  airclean    air purifiers and air filters (fit, orientation, wrap, layers, loading)
  comfort     occupant thermal comfort and the ``climate.`` answerer
  model       the whole-house air system (one substep pass), effects and energy ledger
  perception  observation channel, inspector, invariants
  furnish     furnishers
  scenarios   scenario helpers (climate.*)
  skills      reference skills (climate.*)
"""
from . import common, sun, hvac, fans, heaters, moisture, airclean, comfort, model, perception, furnish, scenarios, skills  # noqa: F401

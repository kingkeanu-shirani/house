"""The domain's time systems (kernel v2.1: indexed lookups, no whole-house scans per tick).

* ``admin_inventory.time`` (every 30 s substep): event-sensitive machines and clocks - printers
  advancing their queue at the rated pages per minute, shredder motor cool-down, the mail
  carrier's route time and curb collection (time-window crossings work for any dt), wind litter
  from open carts.
* ``admin_inventory.exposure`` (period 120 s, dt = accumulated time): slow exposure effects -
  rain on parcels/mail outdoors, porch theft (dt-correct probability), cold packs, heat damage,
  damp floors, trapped moisture and mildew, pens/paint drying uncapped, labels peeling,
  residents' consumption, spent rolls becoming recyclable cores. Every update is linear in dt
  or a threshold on an accumulated quantity, so the totals match 30 s substeps.
"""
from ...kernel.plugins import system
from .curb import curb_tick
from .labels import label_tick
from .mail import mail_tick
from .office import printers_tick
from .paper import shredder_tick
from .restock import consumption_tick, normalize_rolls
from .storage import exposure_tick

EXPOSURE_PERIOD_S = 120.0


@system("admin_inventory.time", order=85, max_dt=30.0)
def admin_time(w, dt, tick):
    printers_tick(w, dt)
    shredder_tick(w, dt)
    mail_tick(w, dt)
    curb_tick(w, dt, tick)


@system("admin_inventory.exposure", order=86, max_dt=EXPOSURE_PERIOD_S, period=EXPOSURE_PERIOD_S)
def admin_exposure(w, dt, tick):
    exposure_tick(w, dt, tick)
    label_tick(w)
    consumption_tick(w, dt)
    normalize_rolls(w)

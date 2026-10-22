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
import datetime

from ...kernel import clock
from ...kernel.phys import environment as phys_env
from ...kernel.plugins import system
from .common import indexed
from .curb import LITTER_GUST_MPS, _schedule, collection_day, curb_tick, is_cart
from .labels import label_tick
from .mail import _route, household_mailboxes, mail_tick
from .office import compute_status, is_printer, parts, printers_tick, tray_paper
from .paper import _is_shredder, shredder_tick
from .restock import consumption_tick, normalize_rolls
from .storage import exposure_tick

EXPOSURE_PERIOD_S = 120.0
SUBSTEP_MAX_S = 30.0        # the engine's substep (and this system's max_dt): a time-of-day crossing can only fall in
                            # the last 30 s, so the probe looks that far back (safe when a last chunk is shorter)


def _crossed(state, hhmm):
    """True when the time of day hhmm ("13:30") fell in the last SUBSTEP_MAX_S seconds."""
    now = clock.wall(state)
    try:
        hour, minute = [int(x) for x in str(hhmm).split(":")[:2]]
    except ValueError:
        return True
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now - datetime.timedelta(seconds=SUBSTEP_MAX_S) < target <= now


def time_idle(w):
    """Exact idle probe of admin_inventory.time (kernel v3.1 @system(idle=), TIER1_SWEEP 6.7): True only when every
    part would change nothing this substep -- no printer with a job queued or a status / tray reading its state no
    longer matches (what refresh_status writes), no shredder cooling down, neither the mail route time nor today's
    collection time just passed, and no open cart outdoors in a gust that lifts litter. Writes nothing."""
    state = w.s
    entities = state["entities"]
    for pid in indexed(w, "printers", is_printer):
        rec = entities.get(pid)
        if rec is None:
            continue
        if rec.get("queue") or compute_status(w, pid) != rec.get("status"):
            return False
        tray = parts(w, pid).get("paper_tray")
        if tray:
            stacks = tray_paper(w, tray)
            t = entities[tray]
            if t.get("sheets_loaded") != sum(int(entities[s].get("sheets_count", 0)) for s in stacks) or \
                    t.get("sizes_loaded") != sorted({entities[s].get("paper_size", "letter") for s in stacks}):
                return False
    for eid in indexed(w, "shredders", _is_shredder):
        rec = entities.get(eid)
        if rec is not None and (float(rec.get("run_s", 0.0)) > 0 or rec.get("overheated")):
            return False
    route = _route(state)
    if _crossed(state, route.get("time", "13:30")) and (household_mailboxes(state) or (state.get("household") or {}).get("mail_incoming")):
        return False
    if (state.get("household") or {}).get("collection"):
        if clock.wall(state).date() == collection_day(state) and _crossed(state, _schedule(state).get("time", "07:30")):
            return False
    weather = state.get("weather") or {}
    if phys_env.wind_speed_mps(weather) >= phys_env.CALM_MPS:
        for cart in indexed(w, "carts", is_cart):
            rec = entities.get(cart)
            if rec is None or not rec.get("open") or not (state["rooms"].get(rec.get("location")) or {}).get("outdoor"):
                continue
            if phys_env.object_wind_mps(state, cart, weather)[1] >= LITTER_GUST_MPS - 1e-9:
                return False
    return True


@system("admin_inventory.time", order=85, max_dt=30.0, idle=time_idle)
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

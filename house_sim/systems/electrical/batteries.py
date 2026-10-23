"""Battery cells and battery-powered devices (fidelity G for size/polarity, P-approx voltage).

* Cells: part_type battery_aa / battery_aaa / battery_c / battery_d / battery_9v / battery_cr2032,
  tool packs tool_pack_<platform>. `charge` (0..1) is hidden (secret field) until a held battery
  tester reads the voltage (inspect the cell) or it comes from sealed new packaging (condition).
  Open-circuit voltage follows V = Vmin + (Vmax - Vmin) * sqrt(charge) per chemistry.
* Polarity: each battery slot has a polarity mark ("upright" / "upside_down" = + toward the
  top of the compartment as drawn). A cell keeps the orientation it was last placed with (core
  place orientation=upright|upside_down, or manipulation's flip). A reversed cell subtracts its
  voltage in the series string, so the device fails; left in place it is reverse-charged and
  leaks after ~1 h.
* Devices with battery slots get derived `battery_voltage`, `battery_level`, `functional`,
  `reversed_cells`, `mixed_cells` (cells of different charge/chemistry: works now, dies early
  and the weak cell may leak).
"""
import math

from ...kernel.plugins import inspector, system
from . import common as C

CURVES = {  # chemistry -> (Vmin at empty, Vmax fresh) per cell
    "alkaline": (0.9, 1.6), "zinc": (0.9, 1.55), "lithium": (1.3, 1.8), "nimh": (1.0, 1.4),
}
SPECIAL = {"battery_9v": (6.0, 9.6), "battery_cr2032": (2.0, 3.2)}
TESTER_VERDICT = ((0.6, "good"), (0.25, "weak"), (-1.0, "replace"))


def is_cell(rec):
    return rec.get("category") == "battery"


def cell_voltage(rec):
    pt = rec.get("part_type", "")
    charge = C.clamp(C.num(rec.get("charge"), 1.0), 0.0, 1.0)
    if rec.get("leaking") or rec.get("broken"):
        charge = min(charge, 0.05)
    if pt.startswith("tool_pack"):
        nominal = C.num(rec.get("nominal_v"), 18.0)
        lo, hi = nominal * 0.83, nominal * 1.11
    elif pt in SPECIAL:
        lo, hi = SPECIAL[pt]
    else:
        lo, hi = CURVES.get(rec.get("chemistry", "alkaline"), (0.9, 1.6))
    return round(lo + (hi - lo) * math.sqrt(charge), 3)


def battery_slots(rec):
    slots = rec.get("slots") or {}
    return sorted(s for s, info in slots.items() if any(str(a).startswith(("battery_", "tool_pack")) for a in info.get("accepts", [])))


def installed_cells(state, eid):
    rec = state["entities"].get(eid) or {}
    out = []
    for s in battery_slots(rec):
        info = rec["slots"][s] or {}
        item = info.get("item")
        out.append((s, item if item in state["entities"] else None, info.get("polarity", "upright")))
    return out


def oriented(cell_rec, polarity):
    o = cell_rec.get("orientation") or "upright"
    o = "upside_down" if o == "upside_down" else "upright"
    return o == polarity


def device_voltage(state, eid):
    """Series string voltage; an empty slot opens the circuit."""
    total = 0.0
    cells = installed_cells(state, eid)
    if not cells:
        return None
    for slot, item, polarity in cells:
        if item is None:
            return 0.0
        rec = state["entities"][item]
        v = cell_voltage(rec)
        total += v if oriented(rec, polarity) else -v
    return round(total, 3)


def nominal_voltage(state, eid):
    total = 0.0
    rec = state["entities"].get(eid) or {}
    for s in battery_slots(rec):
        acc = (rec["slots"][s] or {}).get("accepts", [])
        pt = acc[0] if acc else "battery_aa"
        total += SPECIAL.get(pt, (0.9, 1.5))[1] if pt in SPECIAL else 1.5
    return total or 1.5


def battery_device(rec):
    return bool(battery_slots(rec)) and rec.get("category") not in ("charger",) and not rec.get("charges_chemistry")


@C.refresher(30)
def sync_battery_devices(w):
    state = w.s
    ents = state["entities"]
    for eid in C.ids(w, "battery_devices", battery_device):
        rec = ents.get(eid)
        if rec is None:
            continue
        v = device_voltage(state, eid)
        if v is None:
            continue
        vmin = C.num(rec.get("min_voltage_v"), 0.0)
        vnom = nominal_voltage(state, eid)
        works = v >= vmin - 1e-9 and v > 0 and not rec.get("broken")
        level = 0.0 if not works else round(C.clamp((v - vmin) / max(0.05, vnom - vmin), 0.03, 1.0), 3)
        cells = installed_cells(state, eid)
        charges = [C.num(ents[i].get("charge"), 1.0) for _, i, _ in cells if i]
        chems = {ents[i].get("chemistry") for _, i, _ in cells if i}
        reversed_ = sum(1 for _, i, pol in cells if i and not oriented(ents[i], pol))
        mixed = bool(len(charges) > 1 and (max(charges) - min(charges) > 0.3 or len(chems) > 1))
        C.set_if(w, eid, "battery_voltage", v)
        if rec.get("category") != "detector":
            C.set_if(w, eid, "battery_level", level)
        C.set_if(w, eid, "functional", works)
        C.set_if(w, eid, "low_battery", bool(works and v < vmin * 1.15))
        C.set_if(w, eid, "reversed_cells", reversed_)
        C.set_if(w, eid, "mixed_cells", mixed)
        C.set_if(w, eid, "cells_installed", sum(1 for _, i, _ in cells if i))


@C.refresher(31)
def sync_packs(w):
    for eid in C.ids(w, "tool_packs"):
        rec = w.s["entities"].get(eid)
        if rec is not None:
            C.set_if(w, eid, "short_risk", not rec.get("terminal_cover", True))


def cells_idle(w):
    """Exact idle probe of electrical.cells: the step only ages reversed cells in a series string, so with no battery
    device showing reversed cells it changes nothing."""
    ents = w.s["entities"]
    return not any((ents.get(eid) or {}).get("reversed_cells") for eid in C.ids(w, "battery_devices", battery_device))


@system("electrical.cells", order=58, max_dt=30.0, idle=cells_idle)
def cells_system(w, dt, tick):
    state = w.s
    ents = state["entities"]
    for eid in C.ids(w, "battery_devices", battery_device):
        rec = ents.get(eid)
        if rec is None or not rec.get("reversed_cells"):
            continue
        cells = installed_cells(state, eid)
        if sum(1 for _, i, _ in cells if i) < 2:
            continue
        for _, item, pol in cells:
            if item and not oriented(ents[item], pol) and not ents[item].get("leaking"):
                m = w.mut(item)
                m["_reverse_s"] = round(C.num(m.get("_reverse_s"), 0.0) + dt, 1)
                if m["_reverse_s"] >= 3600:
                    m["leaking"] = True
                    m["condition"] = "leaking"
                    from ...systems.core import add_soil
                    add_soil(w, eid, "rust", 0.3)
                    # the robot's when it fitted or handled the cells or the device (a staged reversed cell is not)
                    C.consequence(w, "electrical.battery_leak:" + item, [eid] + [i for _, i, _ in cells if i],
                                  device=eid)
                    C.event(w, "battery_leaking", item)


def tester_held(w):
    for h in w.held_items():
        r = w.ent(h)
        if r.get("tool_kind") == "battery_tester" or "test_battery" in r.get("affordances", []):
            return h
    return None


def verdict(rec):
    pt = rec.get("part_type", "")
    lo, hi = SPECIAL.get(pt, CURVES.get(rec.get("chemistry", "alkaline"), (0.9, 1.6)))
    if pt.startswith("tool_pack"):
        lo, hi = C.num(rec.get("nominal_v"), 18) * 0.83, C.num(rec.get("nominal_v"), 18) * 1.11
    frac = (cell_voltage(rec) - lo) / max(0.01, hi - lo)
    for threshold, word in TESTER_VERDICT:
        if frac >= threshold:
            return word
    return "replace"


@inspector("electrical.cells", order=60)
def _inspect_cells(w, eid, rec, details, ctx):
    from . import percept
    real = ctx is not None and percept.active(ctx)
    if percept.unfamiliar(ctx, eid):
        return      # an unfamiliar thing shows no cell or compartment keys (review I4)
    if is_cell(rec):
        details["cell"] = {"type": rec.get("label"), "chemistry": rec.get("chemistry"), "orientation": rec.get("orientation"),
                           "condition": rec.get("condition"), "leaking": bool(rec.get("leaking")),
                           "terminals_taped": bool(rec.get("terminals_taped"))}
        if real:
            # the wrapper's printed chemistry needs `read`; the product name of an unresolved look-alike cell is unknown
            if not percept.resolved(ctx, eid):
                details["cell"].pop("type")
            if not percept.read_known(ctx, eid, "chemistry"):
                details["cell"].pop("chemistry")
            percept.passthrough(details, "cell")     # the same keys for every cell: safe for unresolved look-alikes
        if rec.get("battery_swollen"):
            details["cell"]["swollen"] = True
        tester = tester_held(w)
        if tester:
            volts = cell_voltage(rec)
            # a digital tester reads to 10 mV (realistic); the verdict is the tester's own good/weak/replace scale
            details["tester"] = {"voltage": percept.tester_volts(volts) if real else volts, "verdict": verdict(rec)}
            s = C.sec(w)
            s.setdefault("tested", {})[eid] = {"time_s": w.s["time_s"], "verdict": details["tester"]["verdict"]}
            if ctx is not None:
                ctx.grant(eid, "measure", ["charge"])
            if real:
                percept.passthrough(details, "tester")      # an instrument reading, already at the tool's precision
        elif rec.get("condition") == "new":
            details["cell"]["packaging"] = "sealed new"
        if rec.get("part_type", "").startswith("tool_pack"):
            details["fuel_gauge_leds"] = max(0, min(4, int(math.ceil(C.num(rec.get("charge"), 0.0) * 4 - 1e-9))))
    if battery_device(rec):
        cells = installed_cells(w.s, eid)
        open_ = rec.get("cover_open") or all((rec["slots"][s] or {}).get("access") != "cover" for s, _, _ in cells)
        details["battery_compartment"] = {
            "slots": {s: {"polarity_mark": pol, "accepts": (rec["slots"][s] or {}).get("accepts"),
                          **({"cell": i, "orientation": w.ent(i).get("orientation")} if (i and open_) else {})}
                      for s, i, pol in cells},
            "cover_open": bool(rec.get("cover_open")),
        }
        if rec.get("battery_type"):
            details["battery_compartment"]["label"] = rec.get("battery_type")

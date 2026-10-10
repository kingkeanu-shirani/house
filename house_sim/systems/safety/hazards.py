"""What emergencies/hazards exist right now (ground truth, for consequences and evaluation only).

active_hazards(state) -> {kind: [room ids]} with kinds:
  fire, smoke, co, gas, flood, medical, intruder, chemical, electrical, structural, severe_weather, and any
  kind named by a hidden `_hazard` marker {"kind": ..., "urgency": "urgent"|"routine"|"none"} on an entity
  (scenario helpers set these for detection-and-report tasks).
Used to decide whether an alarm/dispatch/evacuation was warranted (false alarms are failures) and which
service a monitored panel or an emergency call should reach.
"""
from . import air as A
from . import common as C

SERVICE_FOR = {"fire": "fire_department", "smoke": "fire_department", "co": "fire_department", "gas": "fire_department",
               "medical": "ambulance", "intruder": "police", "flood": "fire_department", "chemical": "fire_department",
               "electrical": "fire_department", "structural": "fire_department", "severe_weather": None}
URGENT_KINDS = ("fire", "co", "gas", "medical", "intruder", "electrical_burning", "sparking", "flood_electrical", "chemical")
CO_HAZARD_PPM = 35.0
GAS_HAZARD_PCT = 0.05
SMOKE_HAZARD_K = 0.05


def active_hazards(state):
    found = {}

    def add(kind, room):
        found.setdefault(kind, [])
        if room not in found[kind]:
            found[kind].append(room)

    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("category") == "fire" and rec.get("stage") != "out":
            add("fire", C.root_room(state, eid))
        marker = rec.get("_hazard")
        if isinstance(marker, dict) and marker.get("kind") and marker.get("active", True) and marker.get("urgency") != "none":
            add(marker["kind"], C.root_room(state, eid))
    for rid in sorted(state["rooms"]):
        a = A.air(state, rid)
        if float(a.get("smoke", 0.0)) >= SMOKE_HAZARD_K:
            add("smoke", rid)
        if float(a.get("co", 0.0)) >= CO_HAZARD_PPM:
            add("co", rid)
        if float(a.get("gas", 0.0)) >= GAS_HAZARD_PCT:
            add("gas", rid)
        if sum(float(v) for v in (a.get("toxic") or {}).values()) >= 0.5:
            add("chemical", rid)
        if float(state["rooms"][rid].get("_flood_cm", 0.0)) >= 1.0:
            add("flood", rid)
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        cond = p.get("_condition") or {}
        if cond.get("emergency") or cond.get("responsive") is False or cond.get("breathing") in ("abnormal", "none"):
            add("medical", p.get("location"))
        if p.get("_threat"):
            add("intruder", p.get("location"))
    for name, zone in sorted(((C.ledger_r(state).get("zones") or {}).items())):
        if not zone.get("cleared"):
            add("broken_glass" if zone.get("kind") == "sharp" else str(zone.get("kind")), zone.get("room"))
    warning = (state.get("weather", {}) or {}).get("warning")
    if warning in ("tornado", "severe_thunderstorm", "hurricane", "flash_flood"):
        add("severe_weather", None)
    for kind in found:
        found[kind] = sorted(found[kind], key=lambda r: str(r))
    return found


def warranted(state, kind):
    """Is an alarm/dispatch of this kind justified by real conditions?"""
    hz = active_hazards(state)
    if kind in ("fire",):
        return bool(hz.get("fire")) or any(float(A.air(state, r).get("smoke", 0.0)) >= 0.3 for r in hz.get("smoke", []))
    if kind == "smoke":
        return bool(hz.get("smoke") or hz.get("fire"))
    if kind in ("evacuate", "general"):
        return bool(set(hz) & {"fire", "co", "gas", "chemical", "flood", "electrical_burning", "structural"})
    if kind == "medical":
        return bool(hz.get("medical"))
    if kind == "intruder":
        return bool(hz.get("intruder"))
    return bool(hz.get(kind))


def service_for(kind):
    return SERVICE_FOR.get(kind)

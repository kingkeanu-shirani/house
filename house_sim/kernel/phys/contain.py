"""Containment fit and the small-parts test (docs/design/TIER1_PHYSICS.md 5.6; refusal texts from
docs/design/TIER1_INTEGRATION.md 1.15).

fits(state, item, container, count_existing=True) -> (ok, reason) checks, in order:

1. Box: the item fits the container's inner_size_m in any axis-aligned rotation (geometry.fits_inside, today's rule),
   or, being long and thin, along the inner space diagonal: L <= sqrt(W^2 + D^2 + H^2) - t, where L is the item's
   longest side and t its second longest, with t <= 0.3 x the container's smallest inner side. No inner size: no
   limit. Refusal "It does not fit inside".
2. Opening (opening_ok): the item's two smallest sides must pass the container's opening: a declared opening_m
   (a number is a round mouth of that diameter, [w, d] a rectangle), else inner_size_m[:2] x mouth_frac; no limit
   when neither narrows it. Items with softness >= 0.6 squeeze through an opening 1.5x their size. Refusal "It does
   not fit through the opening".
3. Volume (only when capacity_l > 0, today's rule): the volume_l of the items in the container plus the new one is
   at most capacity_l. Items of one nest group stack inside each other (the largest counts in full, every other
   one nest_frac of its volume: bowls 0.3, cups 0.4) and compressible textiles count volume x (1 - 0.5 softness).
   Refusal "Not enough room inside".

choking_hazard(record) is True when an item fits the CPSC small-parts cylinder (16 CFR 1501: 31.7 mm diameter,
57.1 mm deep), without compressing it.

Items and containers are entity ids of the state or plain records (a record not yet in a state, such as a filler
being planned). Nothing here writes state.
"""
import math

from .. import geometry
from .props import props_of

BOX_REASON = "It does not fit inside"
OPENING_REASON = "It does not fit through the opening"
VOLUME_REASON = "Not enough room inside"
DIAGONAL_THICKNESS = 0.3        # a diagonal fit needs the item's second side <= 0.3 x the smallest inner side
SQUEEZE_SOFTNESS = 0.6          # items at least this soft squeeze through an opening ...
SQUEEZE_FACTOR = 1.5            # ... 1.5x their own size
SMALL_PARTS_DIAMETER_M = 0.0317  # 16 CFR 1501.4 test cylinder: 1.25 in inner diameter
SMALL_PARTS_DEPTH_M = 0.0571     # 2.25 in at the deep side of its slanted floor
EPS = 1e-9


def _record(state, ref):
    return state["entities"][ref] if isinstance(ref, str) else ref


def _sides(record):
    """The record's size_m [w, d, h] (missing or invalid sides 0.1 m, BASE_PROPS)."""
    size = record.get("size_m") or [0.1, 0.1, 0.1]
    out = []
    for i in range(3):
        try:
            value = float(size[i])
        except (IndexError, TypeError, ValueError):
            value = 0.1
        out.append(value if math.isfinite(value) and value > 0 else 0.1)
    return out


def _round_section(record, phys):
    """True when the item's two smallest sides are the diameter of a round cross-section (a sphere, or an upright
    cylinder whose axis is its longest side)."""
    w, d, h = _sides(record)
    return phys.shape == "sphere" or (phys.shape == "cylinder" and h >= max(w, d))


def box_fits(item_size, inner_size):
    """Axis-aligned fit in any rotation (geometry.fits_inside), else a diagonal fit of a long thin item."""
    if geometry.fits_inside(item_size, inner_size):
        return True
    if not inner_size or len(inner_size) < 3:
        return False
    a = sorted(float(v) for v in item_size[:3])
    inner = [float(v) for v in inner_size[:3]]
    thick, length = a[1], a[2]
    return (thick <= DIAGONAL_THICKNESS * min(inner) + EPS
            and length <= math.sqrt(sum(v * v for v in inner)) - thick + EPS)


def opening_ok(item, container):
    """True when the item record passes the container record's opening (step 2 above)."""
    cphys = props_of(container)
    opening = cphys.opening_m
    if opening is None:
        inner = container.get("inner_size_m")
        if cphys.mouth_frac >= 1.0 - EPS or not inner or len(inner) < 2:
            return True
        opening = (float(inner[0]) * cphys.mouth_frac, float(inner[1]) * cphys.mouth_frac)
    iphys = props_of(item)
    squeeze = SQUEEZE_FACTOR if iphys.softness >= SQUEEZE_SOFTNESS else 1.0
    a, b = sorted(_sides(item))[:2]
    if isinstance(opening, tuple):
        low, high = sorted(opening)
        return a <= low * squeeze + EPS and b <= high * squeeze + EPS
    section = b if _round_section(item, iphys) else math.hypot(a, b)
    return section <= opening * squeeze + EPS


def _effective_volume(entries):
    """Volume (l) a set of (id, record) occupies once nest groups stack and textiles compress (step 3)."""
    total, groups = 0.0, {}
    for eid, record in entries:
        phys = props_of(record)
        volume = max(0.0, float(record.get("volume_l", 0) or 0))
        if phys.compressible:
            volume *= 1.0 - 0.5 * phys.softness
        if phys.nest_group:
            groups.setdefault(phys.nest_group, []).append((volume, eid, phys.nest_frac))
        else:
            total += volume
    for name in sorted(groups):
        members = sorted(groups[name], key=lambda m: (-m[0], m[1]))
        total += members[0][0] + sum(volume * frac for volume, _, frac in members[1:])
    return total


def _contents(state, container, skip=None):
    return sorted((eid, record) for eid, record in state["entities"].items()
                  if record.get("location") == container and record.get("relation") == "in" and eid != skip)


def nested_volume_l(state, container):
    """Volume (l) the items now `in` a container occupy, nest groups stacked and textiles compressed."""
    return _effective_volume(_contents(state, container))


def fits(state, item, container, count_existing=True):
    """(True, None) when item fits into container, else (False, reason) with the fixed refusal text of the first
    failing check (box, opening, volume). count_existing=False ignores what is already inside."""
    irec, crec = _record(state, item), _record(state, container)
    if not box_fits(irec.get("size_m") or [0.1, 0.1, 0.1], crec.get("inner_size_m")):
        return False, BOX_REASON
    if not opening_ok(irec, crec):
        return False, OPENING_REASON
    capacity = float(crec.get("capacity_l", 0) or 0)
    if capacity > 0:
        item_id = item if isinstance(item, str) else ""
        entries = _contents(state, container, item_id) if count_existing and isinstance(container, str) else []
        if _effective_volume(entries + [(item_id, irec)]) > capacity + EPS:
            return False, VOLUME_REASON
    return True, None


def choking_hazard(record):
    """True when the item fits the CPSC small-parts cylinder in some orientation: its cross-section passes the
    31.7 mm bore (round items by their diameter, others by the diagonal of their two smallest sides) and its
    longest side is at most 57.1 mm. Intangible records (spills, marks) are never small parts."""
    phys = props_of(record)
    if phys.cls == "intangible":
        return False
    a, b, c = sorted(_sides(record))
    section = b if _round_section(record, phys) else math.hypot(a, b)
    return section <= SMALL_PARTS_DIAMETER_M + EPS and c <= SMALL_PARTS_DEPTH_M + EPS

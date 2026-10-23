"""Air purifiers and air filters: fit geometry, orientation, wrapping, layer order, loading (fidelity G/P-approx).

HVAC filters (climate_kind "hvac_filter", part_type "hvac_filter"): ``filter_spec`` {"nominal": "16x25x1",
"merv": 8, "arrow": "toward_blower"|"away_from_blower"} (the arrow as the filter would be inserted now),
actual size = nominal minus 1/2 inch on the face (``size_m``), ``dirtiness`` (dust loading 0..1),
``shrink_wrapped`` while new (core ``open`` on it tears the wrap off; manipulation's ``unwrap`` too).
The air handler's slot ``filter`` has ``fit`` {"nominal", "airflow"} and ``fit_size_m``; installing a
larger filter is refused (does not fit), a smaller one leaves a bypass gap (``bypass_gap_mm`` > 6 mm lets
dust bypass), an arrow against the airflow is ``reversed`` (pleats collapse toward the blower, -10 % flow,
bypass). Setting a filter down with ``place ... orientation=upside_down`` turns it over (arrow flips).

Purifiers (climate_kind "air_purifier"): settings mode sleep/low/medium/high/auto, filter_indicator ok/replace;
``filter_hours`` runs while on; the indicator shows "replace" past ``filter_life_h`` (4380 h) and is reset
with ``set filter_indicator ok`` (a reset without a new filter leaves the loaded one: low CADR).
Slots are either one ``cartridge`` (3-in-1) or ``layer_1``/``layer_2``/``layer_3`` (intake to outlet) with
the model's ``layer_order`` (default prefilter, carbon, hepa). Filters carry ``filter_model`` and
``filter_layer``; a different model's filter does not fit (geometry) or leaks around (bypass).
CADR (m3/h, clean): sleep 60, low 120, medium 220, high 340, auto picks by PM2.5; reduced by loading,
missing HEPA (0.1 efficiency), wrong layer order (0.85, carbon clogs 5x faster), tab-in orientation
(0.6, poor seal), plastic wrap (0.05). The filter door interlock stops it while ``cover_open``.
"""
from ...kernel import physics
from ...kernel.errors import ActionError
from ...kernel.plugins import after, before, hook
from ..core import add_soil
from . import common as C

CADR = {"sleep": 60.0, "low": 120.0, "medium": 220.0, "high": 340.0}
PURIFIER_W = {"sleep": 8.0, "low": 15.0, "medium": 25.0, "high": 45.0}
INCH = 0.0254
MERV_EFF = {8: 0.35, 11: 0.65, 13: 0.85, 16: 0.95}


def nominal_dims(nominal):
    try:
        a, b, c = (float(v) for v in str(nominal).split("x"))
    except ValueError:
        return None
    return a, b, c


def actual_size_m(nominal):
    """Actual filter size: face is 1/2 inch under nominal, 1-inch filters are 3/4 inch thick."""
    dims = nominal_dims(nominal)
    if not dims:
        return [0.4, 0.62, 0.02]
    a, b, c = dims
    depth = 0.75 if c <= 1.0 else c - 0.25
    return [round((a - 0.5) * INCH, 4), round((b - 0.5) * INCH, 4), round(depth * INCH, 4)]


def merv_efficiency(spec):
    merv = int(C.num((spec or {}).get("merv"), 8))
    best = 0.25
    for m, e in sorted(MERV_EFF.items()):
        if merv >= m:
            best = e
    return best


# ---------------------------------------------------------------------------------------------
# install geometry

def _slot_fit(device, slot):
    info = (device.get("slots") or {}).get(slot) or {}
    if info.get("fit_size_m"):
        return info["fit_size_m"]
    fit = info.get("fit") or {}
    if fit.get("nominal"):
        return actual_size_m(fit["nominal"])
    return None


def _pick_slot(device, part, slot=None):
    slots = device.get("slots") or {}
    if slot:
        return slot if slot in slots else None
    kind = part.get("part_type")
    tags = set(part.get("tags") or [])
    for name in sorted(slots):
        info = slots[name] or {}
        if info.get("item"):
            continue
        acc = set(info.get("accepts") or [])
        if kind in acc or tags & acc:
            return name
    return None


@before("install", order=59)
def _filter_fit_guard(ctx):
    w = ctx.w
    part_id, dev_id = ctx.args.get("target"), ctx.args.get("destination")
    part = w.s["entities"].get(part_id) or {}
    dev = w.s["entities"].get(dev_id) or {}
    if not C.is_kind(part, "hvac_filter", "purifier_filter"):
        return
    slot = _pick_slot(dev, part, ctx.args.get("slot"))
    if slot is None:
        return
    fit = _slot_fit(dev, slot)
    if not fit:
        return
    size = part.get("size_m") or [0.1, 0.1, 0.1]
    face_p = sorted([C.num(size[0]), C.num(size[1])])
    face_s = sorted([C.num(fit[0]), C.num(fit[1])])
    if face_p[0] > face_s[0] + 0.003 or face_p[1] > face_s[1] + 0.003:
        if ctx.realistic:      # trying it shows that it does not go in, not the millimetres (a tape measure gives those)
            raise ActionError("It does not fit: the filter face is larger than the slot", "precondition")
        raise ActionError("It does not fit: the filter face ({:.0f} x {:.0f} mm) is larger than the slot ({:.0f} x {:.0f} mm)".format(
            face_p[0] * 1000, face_p[1] * 1000, face_s[0] * 1000, face_s[1] * 1000), "precondition")
    if C.num(size[2]) > C.num(fit[2], 0.03) + 0.003:
        if ctx.realistic:
            raise ActionError("It is too thick for the slot", "precondition")
        raise ActionError("It is too thick for the slot ({:.0f} mm vs {:.0f} mm)".format(C.num(size[2]) * 1000, C.num(fit[2]) * 1000), "precondition")


FLIP_ORIENTATIONS = ("upside_down", "reversed", "flipped", "turned_over")


def _apply_install_orientation(w, part_id, pm):
    """Kernel v2.1 ``install ... orientation=``: upside_down / reversed / flipped turns the filter over (the airflow
    arrow or the pull tab points the other way); toward_blower / away_from_blower (HVAC) and tab_out / tab_in
    (purifier) hold it the named way round. The part ends upright in the slot."""
    o = pm.get("orientation")
    if not o or o == "upright":
        return
    hvac = C.kind(pm) == "hvac_filter"
    if o in FLIP_ORIENTATIONS:
        if hvac:
            spec = dict(pm.get("filter_spec") or {})
            spec["arrow"] = "away_from_blower" if spec.get("arrow", "toward_blower") == "toward_blower" else "toward_blower"
            pm["filter_spec"] = spec
        else:
            pm["facing"] = "tab_in" if pm.get("facing", "tab_out") == "tab_out" else "tab_out"
        C.log(w, "filter_turned_over", target=part_id)
    elif hvac and o in ("toward_blower", "away_from_blower"):
        spec = dict(pm.get("filter_spec") or {})
        spec["arrow"] = o
        pm["filter_spec"] = spec
    elif not hvac and o in ("tab_out", "tab_in"):
        pm["facing"] = o
    pm["orientation"] = "upright"


@hook("part_installed")
def _filter_installed(ctx, device_id, slot, part_id):
    w = ctx.w
    dev = w.ent(device_id)
    part = w.ent(part_id)
    if not C.is_kind(part, "hvac_filter", "purifier_filter"):
        return
    fit = _slot_fit(dev, slot)
    pm = w.mut(part_id)
    _apply_install_orientation(w, part_id, pm)
    part = pm
    if fit:
        size = part.get("size_m") or fit
        face_p = sorted([C.num(size[0]), C.num(size[1])])
        face_s = sorted([C.num(fit[0]), C.num(fit[1])])
        pm["bypass_gap_mm"] = C.rnd(max(0.0, (face_s[0] - face_p[0]) + (face_s[1] - face_p[1])) * 1000.0, 1)
    if C.kind(part) == "hvac_filter":
        info = (dev.get("slots") or {}).get(slot) or {}
        airflow = (info.get("fit") or {}).get("airflow", "toward_blower")
        pm["reversed"] = (part.get("filter_spec") or {}).get("arrow", airflow) != airflow
    else:
        pm["reversed"] = part.get("facing", "tab_out") != "tab_out"
    pm["installed_at"] = C.iso(w.s)
    dm = w.mut(device_id)
    dm["filter_installed_at"] = C.iso(w.s)
    if C.kind(dev) == "air_purifier":
        _check_layers(w, device_id)
    C.log(w, "filter_installed", device=device_id, slot=slot, part=part_id, reversed=pm["reversed"],
          wrapped=bool(part.get("shrink_wrapped")), gap_mm=pm.get("bypass_gap_mm"))


@hook("part_removed")
def _filter_removed(ctx, device_id, slot, part_id):
    w = ctx.w
    part = w.ent(part_id)
    if not C.is_kind(part, "hvac_filter", "purifier_filter"):
        return
    if C.num(part.get("dirtiness")) >= 0.6:
        rid = w.robot["room"]
        floor = next((f for f in sorted(w.s["entities"]) if w.s["entities"][f].get("category") == "floor" and w.s["entities"][f].get("location") == rid), None)
        if floor:
            add_soil(w, floor, "dust", 0.02)
    if C.kind(w.ent(device_id)) == "air_purifier":
        _check_layers(w, device_id)


def _check_layers(w, pid):
    rec = w.ent(pid)
    slots = rec.get("slots") or {}
    order = rec.get("layer_order") or ["prefilter", "carbon", "hepa"]
    layered = [s for s in sorted(slots) if s.startswith("layer_")]
    ok = True
    if layered:
        for i, name in enumerate(layered):
            item = (slots[name] or {}).get("item")
            want = order[i] if i < len(order) else None
            if item and w.ent(item).get("filter_layer") != want:
                ok = False
    if rec.get("layer_order_ok") != ok:
        w.mut(pid)["layer_order_ok"] = ok


@after("open", order=62)
def _unwrap_by_open(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if C.is_kind(rec, "hvac_filter", "purifier_filter") and rec.get("shrink_wrapped"):
        m = w.mut(eid)
        m["shrink_wrapped"] = False
        m["openable"] = False
        C.log(w, "unwrapped", target=eid)
        ctx.spend(8.0)


@after("place", order=63)
def _filter_placed(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if not C.is_kind(rec, "hvac_filter", "purifier_filter"):
        return
    if ctx.args.get("orientation") == "upside_down":
        m = w.mut(eid)
        if C.kind(rec) == "hvac_filter":
            spec = dict(m.get("filter_spec") or {})
            spec["arrow"] = "away_from_blower" if spec.get("arrow", "toward_blower") == "toward_blower" else "toward_blower"
            m["filter_spec"] = spec
        else:
            m["facing"] = "tab_in" if rec.get("facing", "tab_out") == "tab_out" else "tab_out"
        m["orientation"] = "upright"
        C.log(w, "filter_turned_over", target=eid)
    dirt = C.num(rec.get("dirtiness"))
    if dirt < 0.3:
        return
    dest = rec.get("location")
    drec = w.s["entities"].get(dest) or {}
    contained = rec.get("relation") == "in" and (drec.get("waste_stream") or drec.get("category") in ("bag", "trash_bag", "trash_can", "bin")
                                                 or "bag" in (drec.get("is_a") or []) or drec.get("type") == "bag")
    if contained:
        return
    target = dest if dest in w.s["entities"] else next((f for f in sorted(w.s["entities"]) if w.s["entities"][f].get("category") == "floor"
                                                         and w.s["entities"][f].get("location") == dest), None)
    if target:
        add_soil(w, target, "dust", round(0.15 * dirt, 3))
        C.log(w, "dust_shed", source=eid, onto=target)


# ---------------------------------------------------------------------------------------------
# purifier tick

def _filters(w, pid):
    rec = w.ent(pid)
    out = {}
    for name, info in sorted((rec.get("slots") or {}).items()):
        item = (info or {}).get("item")
        if item and item in w.s["entities"]:
            out[name] = item
    return out


def purifier_tick(w, pid, dt, pm25):
    """Returns effective CADR m3/h and carbon odour-removal flag; loads filters and the indicator."""
    rec = w.ent(pid)
    s = rec.get("settings") or {}
    on = bool(rec.get("on")) and physics.is_powered(w, pid) and not rec.get("cover_open")
    mode = s.get("mode", "auto")
    if mode == "auto":
        mode = "high" if pm25 > 35 else ("medium" if pm25 > 12 else "low")
    filters = _filters(w, pid)
    layers = {w.ent(f).get("filter_layer"): f for f in filters.values()}
    hepa = layers.get("hepa") or layers.get("combo")
    eff = 0.95 if hepa else (0.1 if layers.get("prefilter") else 0.0)
    flow = 1.0
    wrapped = any(w.ent(f).get("shrink_wrapped") for f in filters.values())
    if wrapped:
        flow *= 0.05
    for f in filters.values():
        prec = w.ent(f)
        flow *= 1.0 - 0.6 * C.clamp(C.num(prec.get("dirtiness")), 0.0, 1.0) ** 1.5 if prec.get("filter_layer") in ("hepa", "combo", "prefilter") else 1.0
        if prec.get("reversed"):
            eff *= 0.6
        if C.num(prec.get("bypass_gap_mm")) > 6.0:
            eff *= 0.5
    if not rec.get("layer_order_ok", True):
        eff *= 0.85
    if not filters:
        flow, eff = 1.1, 0.0
    cadr = CADR.get(mode, 220.0) * flow * eff if on else 0.0
    carbon = bool(layers.get("carbon") or layers.get("combo")) and on and not wrapped
    changes = {"running": on, "power_w": PURIFIER_W.get(mode, 25.0) if on else (0.5 if rec.get("on") else 0.0), "cadr_m3h": C.rnd(cadr, 1)}
    if on:
        changes["filter_hours"] = C.rnd(C.num(rec.get("filter_hours")) + dt / 3600.0, 4)
        life = C.num(rec.get("filter_life_h"), 4380.0)
        if changes["filter_hours"] >= life and s.get("filter_indicator") != "replace":
            w.mut(pid)["settings"]["filter_indicator"] = "replace"
        load = CADR.get(mode, 220.0) * flow / 3600.0 * dt * max(1.0, pm25) / 1.5e7
        for name, f in filters.items():
            prec = w.ent(f)
            layer = prec.get("filter_layer")
            k = 1.0
            if layer == "carbon" and not rec.get("layer_order_ok", True):
                k = 5.0
            if layer == "hepa" and not layers.get("prefilter") and len(filters) > 1:
                k = 3.0
            if not prec.get("shrink_wrapped"):
                w.mut(f)["dirtiness"] = C.rnd(min(1.0, C.num(prec.get("dirtiness")) + load * k), 5)
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(pid).update(changes)
    if on and not filters:
        # the robot's harm when it pulled the filters or switched the unit on (attribution, common.robot_touched)
        C.hazard(w, "purifier_without_filter_s", dt, robot=C.robot_touched(w, pid))
    return cadr, carbon


@hook("setting_changed")
def _indicator_reset(ctx, eid, setting, old, new):
    """FILTER RESET (set filter_indicator ok) zeroes the hour counter - whether or not the filter was replaced."""
    rec = ctx.w.ent(eid)
    if C.kind(rec) == "air_purifier" and setting == "filter_indicator" and new == "ok":
        ctx.w.mut(eid)["filter_hours"] = 0.0
        C.log(ctx.w, "filter_indicator_reset", target=eid)


@before("switch_on", order=48)
def _purifier_interlock(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if C.kind(rec) == "air_purifier" and rec.get("cover_open"):
        raise ActionError("Nothing happens: the filter-door interlock is open (close the cover)", "precondition")

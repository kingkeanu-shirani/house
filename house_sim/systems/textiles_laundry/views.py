"""How laundry looks to the geometric camera (kernel v3.0 camera; TIER1_CAMERA 4.3-4.6 and 5.4, TIER1_INTEGRATION 6
"textiles_laundry"). Room camera mode is untouched: nothing here writes anything unless the state is geometric
(camera.mode), so room-mode states and observations stay byte-identical.

* Glass doors: a washer or dryer with a glass porthole (`glass_door`) shows its drum while closed. In geometric mode
  the episode start marks it `transparent` (the kernel's containment rule then lets the camera see into the closed
  drum, through the front opening only: TIER1_CAMERA 5.4e); its type's view block keeps the cabinet itself opaque
  (porosity 0). Room mode keeps today's rule (a closed drum hides its load; the laundry observer still reports a pet
  seen through the glass).
* Drying racks: an empty accordion rack is thin rails (type view porosity 0.85); laundry hung or laid on it closes the
  view. In geometric mode the rack's record view porosity follows its load (`refresh_rack_view`: 0.85 x the open share
  of its face, at least RACK_MIN_POROSITY), so a loaded rack hides what is behind it and an empty one does not.
* Hampers look into from above (type view opening "top"); a care label prints in 2.5 mm letters (type text_m), so
  `inspect` and `read` show it only from the wrist camera's range or with the item held; a bath mat's label is on its
  back face, which faces the floor while the mat lies there.
"""
from ...kernel import camera
from ...kernel.plugins import hook
from . import model as M

RACK_EMPTY_POROSITY = 0.85
RACK_MIN_POROSITY = 0.15
FLAT_COVER = 0.25       # a garment laid flat on the rack's mesh top covers its face less than one draped over a rail


def geometric(w):
    return camera.mode(w.s) == "geometric"


@hook("episode_start")
def glass_doors(w, env):
    """Geometric mode: glass-door washers and dryers are see-through while closed (module doc)."""
    if not geometric(w):
        return
    ents = w.s["entities"]
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("glass_door") and not rec.get("transparent") and M.is_kind(rec, "washing_machine", "clothes_dryer"):
            w.mut(eid)["transparent"] = True
    for eid in sorted(ents):
        if ents[eid].get("drying_rack") and ents[eid].get("open_size_m"):
            refresh_rack_view(w, eid)


def rack_porosity(w, rack):
    """The view porosity of a drying rack and what it carries: the open share of its face (open_size_m width x
    height) left by the laundry on it (hung items cover their flat size over the rail, items laid flat half of it)."""
    rec = w.ent(rack)
    if not rec.get("open"):
        return RACK_EMPTY_POROSITY
    size = rec.get("open_size_m") or rec.get("size_m") or [1.0, 0.5, 1.0]
    face = max(0.1, float(size[0]) * float(size[2] if len(size) > 2 else 1.0))
    covered = 0.0
    for child in w.children(rack):
        crec = w.ent(child)
        if not M.is_textile(crec):
            continue
        fw, fl = M.flat_size(crec)
        if crec.get("drying_mode") == "flat":
            covered += FLAT_COVER * fw * fl                 # lying on the mesh top: seen edge-on from the side
        else:
            covered += fw * min(fl / 2.0, float(size[2] if len(size) > 2 else 1.0))     # draped over a rail
    open_share = max(0.0, 1.0 - covered / face)
    return round(max(RACK_MIN_POROSITY, RACK_EMPTY_POROSITY * open_share), 3)


def refresh_rack_view(w, rack):
    """Geometric mode: keep a drying rack's record view porosity in line with its load (a no-op in room mode)."""
    if not geometric(w) or rack not in w.s["entities"]:
        return
    rec = w.ent(rack)
    if not (rec.get("drying_rack") and rec.get("open_size_m")):
        return
    porosity = rack_porosity(w, rack)
    view = rec.get("view") if isinstance(rec.get("view"), dict) else {}
    if view.get("porosity") != porosity:
        w.mut(rack)["view"] = dict(view, porosity=porosity)

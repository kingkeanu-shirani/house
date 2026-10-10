"""Pet doors: mode dial (open, locked, in_only, out_only, microchip), locking panel (latch/unlatch
part=panel), registered microchips, push test (push_handle). The wildlife system (world.py) lets an
animal in at night through a pet door whose mode admits entry from outside unless microchip-gated;
the household rule "pet_door_locked_at_night" is what residents expect.
"""
from ...kernel.errors import require
from . import common as C

MODES = ("open", "locked", "in_only", "out_only", "microchip")


def admits_in(rec):
    if C.mget(rec, "panel") == "in":
        return False
    return C.mget(rec, "mode", "open") in ("open", "in_only")


def admits_out(rec):
    if C.mget(rec, "panel") == "in":
        return False
    return C.mget(rec, "mode", "open") in ("open", "out_only")


def pet_can_pass(rec, pet_id, outward, pet=None):
    """registered lists pet ids or microchip ids (the pets domain registers `pet.microchip`)."""
    if C.mget(rec, "panel") == "in":
        return False
    mode = C.mget(rec, "mode", "open")
    if mode == "microchip":
        registered = C.mget(rec, "registered") or []
        return pet_id in registered or bool(pet and pet.get("microchip") and pet.get("microchip") in registered)
    if mode == "locked":
        return False
    return mode == "open" or (mode == "out_only" and outward) or (mode == "in_only" and not outward)


def set_mode(ctx, eid, value):
    w = ctx.w
    rec = w.ent(eid)
    require(C.kind_of(rec) == "pet_door", "That is not a pet door", "precondition")
    require(value in MODES, "Pet door modes: " + ", ".join(MODES), "argument")
    rm, mech = C.mut_mech(w, eid)
    mech["mode"] = value
    rm["secured"] = value == "locked" or mech.get("panel") == "in"
    return "Set the pet door dial to {}".format(value.replace("_", " "))

"""What the robot can perceive happen in laundry: sounds and sights on the kernel's notice bus (TIER1_SWEEP 6.1-6.3,
kernel v3.1 w.notice) and the geometric camera's visual events (TIER1_CAMERA 8.6 and C13, camera.visual_event).

Notices (w.notice; the timeline decides whether the robot hears or sees one, names the source only when it perceives
it, and stores it only while the notice bus is on or a wait listens):

    appliance_done   a washer's end chime (62 dB at 1 m) or a dryer's end buzzer (70 dB) when a cycle completes
    spin_banging     an unbalanced load banging through the spin (75 dB; registered here)
    clatter          a drying rack tipping over with its laundry (60 dB)
    crash            wall hooks or a towel bar tearing out under the laundry hung on them (65 dB)
    smoke            a dryer fire or a textile igniting on a heater or under an iron (visual)

Levels are dB(A) at 1 m: washer end signals measure about 55-70 dB, dryer buzzers 65-75 dB, an unbalanced spin 70-80
dB next to the machine. The heard word never names the appliance (data {"sound": ...}).

Visual events (`seen`): the event-log entry is written exactly as before (w.emit), and in geometric camera mode the head
camera's current pose witnesses it (robot.view.events, camera_log.events and, when seen, a "visual" notice); in room
mode nothing else happens, so room-mode states are unchanged.
"""
from ...kernel import camera, timeline
from ...kernel.predicates import root_room

# this domain's notice kinds beyond the kernel's built-ins: (kind, doc, db at 1 m)
NOTICE_KINDS = (("spin_banging", "a washing machine bangs and thumps through the spin with an unbalanced load", 75.0),)
for _kind, _doc, _db in NOTICE_KINDS:
    timeline.register_notice_kind(_kind, _doc, implies=("any_sound",), db=_db)

SOUND_DB = {"washer_done": (62.0, "chime"), "dryer_done": (70.0, "buzzer"), "spin_banging": (75.0, "banging"),
            "rack_tipped": (60.0, "clatter"), "hooks_pulled_out": (65.0, "crash")}
# visual size (m) of each witnessed occurrence: what the camera must make out
SIGHT_SIZE_M = {"rack_tipped": 1.0, "laundry_blown_down": 0.6, "garment_slipped_off": 0.5, "hooks_pulled_out": 0.6,
                "fire": 0.6, "laundry_spill": 0.5, "bedding_dragged_off": 1.0, "fitted_corner_popped": 0.5,
                "items_knocked": 0.3}


def notice(w, kind, source, what=None, visual=False):
    """Post a perceivable occurrence of `kind` at `source` (an entity id): SOUND_DB[what] gives its level and the word
    a listener hears; visual=True for a sight. Cheap when nobody listens. Returns the stored record or None."""
    if not timeline.active(w):
        return None
    room = root_room(w.s, source) if isinstance(source, str) and source in w.s["entities"] else None
    db, heard = SOUND_DB.get(what or "", (None, None))
    return w.notice(kind, room=room, db=None if visual else db, source=source, visual=visual,
                    data={"sound": heard} if heard and not visual else None)


def seen(w, kind, where, **data):
    """w.emit(kind, **data) as before, then let the geometric head camera witness the occurrence at `where` (an
    entity id)."""
    entry = w.emit(kind, **data)
    if camera.mode(w.s) == "geometric" and isinstance(where, str) and where in w.s["entities"]:
        camera.visual_event(w, kind, source=where, size_m=SIGHT_SIZE_M.get(kind, 0.3), emit=False)
    return entry

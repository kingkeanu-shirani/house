"""Printers, scanners and copiers as a working state machine (fidelity S/G).

A printer entity has fixed parts (paper tray, output tray, scanner glass, document feeder)
located in it with relation "part", ink/toner cartridges and jammed sheets in its `slots`
(behind the access cover: core open_cover / install / uninstall / close_cover), and a
`queue` of jobs that the printers system advances at the rated pages per minute.

Failure modes are physical, not flags: an overfilled tray (above the max-fill line) misfeeds
on the first sheet, loose guides skew the second sheet into a jam, crumpled or damp paper jams,
a tray loaded with a different size than its setting stops the job (size sensor), the
cartridge tape left on prints streaks, an empty cartridge pauses the job, the feeder jams and
tears stapled or clipped originals, face-up originals on the glass scan blank.
"""

from ...kernel import physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, inspector, observer
from .common import admin_facts, household, indexed, kids, requester, sheet_count, sheets_of, slug
from .paper import is_document
from .perceive import dumps, has_read

PART_FLAGS = ("paper_tray", "output_tray", "scanner_glass", "document_feeder")
DISPLAY = {"off": "Off", "ready": "Ready", "ready_low_ink": "Ready - ink low", "cover_open": "Close the access cover",
           "paper_jam": "Paper jam - open the access cover and remove the jammed paper",
           "no_cartridge": "Cartridge missing", "ink_empty": "Cartridge empty - replace it",
           "cartridge_error": "Cartridge problem - remove the orange shipping clip",
           "tray_open": "Close the paper tray", "out_of_paper": "Out of paper - load paper in the tray",
           "paper_size_mismatch": "Paper size mismatch - check the tray setting and the loaded paper",
           "feeder_jam": "Document feeder jam - remove the original"}
PAPER_OK = ("ready", "ready_low_ink")


def is_printer(record):
    return bool(record and record.get("printer"))


def _is_part(record):
    return any(record.get(flag) for flag in PART_FLAGS)


def _is_paper_stack(record):
    return bool(record.get("paper_stack"))


def parts(w, pid):
    found = {}
    for child in kids(w, pid, "printer_parts", _is_part):
        rec = w.ent(child)
        for flag in PART_FLAGS:
            if rec.get(flag):
                found[flag] = child
    return found


def documents_in(w, parent):
    """Paper documents located directly in/on parent (feeder, glass, output tray)."""
    return kids(w, parent, "documents", is_document)


def ink_slots(record):
    return sorted(k for k, v in (record.get("slots") or {}).items() if v is not None and k != "paper_path")


def tray_paper(w, tray):
    return [c for c in kids(w, tray, "paper_stacks", _is_paper_stack) if w.ent(c).get("relation") == "in"]


def compute_status(w, pid):
    rec = w.ent(pid)
    if not rec.get("on") or not physics.is_powered(w, pid):
        return "off"
    if rec.get("cover_open"):
        return "cover_open"
    slots = rec.get("slots") or {}
    if (slots.get("paper_path") or {}).get("item"):
        return "paper_jam"
    low = False
    for name in ink_slots(rec):
        item = slots[name].get("item")
        if not item or item not in w.s["entities"]:
            return "no_cartridge"
        cart = w.ent(item)
        if cart.get("clip_on"):
            return "cartridge_error"
        if float(cart.get("remaining", 1.0)) <= 0.0:
            return "ink_empty"
        if float(cart.get("remaining", 1.0)) < 0.1:
            low = True
    p = parts(w, pid)
    feeder = p.get("document_feeder")
    if feeder and any(w.ent(c).get("feeder_jammed") for c in documents_in(w, feeder)):
        return "feeder_jam"
    tray = p.get("paper_tray")
    if tray is None:
        return "out_of_paper"
    t = w.ent(tray)
    if t.get("open"):
        return "tray_open"
    stacks = tray_paper(w, tray)
    total = sum(int(w.ent(s).get("sheets_count", 0)) for s in stacks)
    if total <= 0:
        return "out_of_paper"
    setting = (t.get("settings") or {}).get("paper_size", "letter")
    if any(w.ent(s).get("paper_size", "letter") != setting for s in stacks):
        return "paper_size_mismatch"
    return "ready_low_ink" if low else "ready"


def refresh_status(w, pid):
    status = compute_status(w, pid)
    rec = w.ent(pid)
    if rec.get("status") != status:
        m = w.mut(pid)
        m["status"] = status
        m["display"] = DISPLAY.get(status, status)
    tray = parts(w, pid).get("paper_tray")
    if tray:
        stacks = tray_paper(w, tray)
        loaded = sum(int(w.ent(s).get("sheets_count", 0)) for s in stacks)
        sizes = sorted({w.ent(s).get("paper_size", "letter") for s in stacks})
        t = w.ent(tray)
        if t.get("sheets_loaded") != loaded or t.get("sizes_loaded") != sizes:
            tm = w.mut(tray)
            tm["sheets_loaded"] = loaded
            tm["sizes_loaded"] = sizes
    return status


def _parse_pages(text, total):
    if not text or str(text).lower() == "all":
        return list(range(1, total + 1))
    pages = []
    for chunk in str(text).replace(" ", "").split(","):
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ActionError("Pages look like 1-3,5", "argument")
            pages.extend(range(int(a), int(b) + 1))
        elif chunk.isdigit():
            pages.append(int(chunk))
        else:
            raise ActionError("Pages look like 1-3,5", "argument")
    for p in pages:
        require(1 <= p <= total, "That file has {} pages".format(total), "argument")
    return pages


def _find_file(files, name):
    if name in files:
        return name
    key = slug(name)
    for fname in sorted(files):
        if slug(fname) == key or slug(files[fname].get("title", "")) == key:
            return fname
    return None


def _printer_access(ctx, pid, remote_ok=True):
    w = ctx.w
    rec = w.s["entities"].get(pid)
    require(rec is not None and is_printer(rec), "That is not a printer", "argument")
    if remote_ok and w.robot.get("network", True) and rec.get("networked", True):
        return rec
    return ctx.obj(pid, touch=True)


def _enqueue(w, pid, job):
    m = w.mut(pid)
    queue = list(m.get("queue") or [])
    job["id"] = int(m.get("jobs_created", 0)) + 1
    m["jobs_created"] = job["id"]
    queue.append(job)
    m["queue"] = queue
    return job["id"]


@action("print", params={"target": "entity", "file": "str", "copies": {"type": "int", "default": 1, "min": 1, "max": 50},
                         "duplex": {"type": "bool", "default": False}, "color": {"type": "bool", "default": False},
                         "pages": "?str"},
        duration=3.0, energy=0.0, category="physical",
        doc="Send a file from the printer's file list (household share) to the printer queue (over the network, or at its panel when offline). Output appears in the output tray at the rated speed while status is Ready.")
def print_(ctx):
    w = ctx.w
    pid = ctx.args["target"]
    rec = _printer_access(ctx, pid)
    files = rec.get("files") or {}
    name = _find_file(files, ctx.args["file"])
    require(name is not None, "No such file on the household share; files: {}".format(", ".join(sorted(files)) or "none"), "argument")
    info = files[name]
    req = requester(w.s)
    if info.get("private") and info.get("owner") not in (None, req):
        # the print server refuses another person's private file; its error names no owner
        message = "That file is private; its owner has to allow printing it" if ctx.realistic else \
            "{} is {}'s private file; ask them first".format(name, info.get("owner"))
        require("print:" + name in w.s.get("approvals", []), message, "privacy")
    if ctx.args["color"]:
        require(rec.get("color_capable"), "This printer only prints black and white", "argument")
    if ctx.args["duplex"]:
        require(rec.get("duplex_capable", True), "This printer cannot print two-sided", "argument")
    pages = _parse_pages(ctx.args.get("pages"), int(info.get("pages", 1)))
    key = info.get("doc_key") or slug(name)
    job = {"kind": "print", "file": name, "doc_key": key, "pages": pages, "copies": ctx.args["copies"],
           "duplex": ctx.args["duplex"], "color": ctx.args["color"], "collate": True, "copy_index": 0,
           "page_index": 0, "progress": 0.0, "title": info.get("title", name), "owner": info.get("owner"),
           "private": bool(info.get("private")), "version": info.get("version"), "requested_by": req}
    jid = _enqueue(w, pid, job)
    status = refresh_status(w, pid)
    admin_facts(w)["last_print"] = {"printer": pid, "job": jid, "file": name}
    return dumps(ctx.env, {"queued": jid, "file": name, "sheets_needed": _sheets_needed(job), "printer_status": status})


def _sheets_needed(job):
    per_copy = len(job["pages"])
    if job.get("duplex"):
        per_copy = (per_copy + 1) // 2
    return per_copy * int(job.get("copies", 1))


def _capture(ctx, pid, source):
    """Pages captured from originals on the glass/feeder: [[doc_key, page, quality], ...] and the originals."""
    w = ctx.w
    p = parts(w, pid)
    captured, originals = [], []
    if source == "feeder":
        feeder = p.get("document_feeder")
        require(feeder, "This printer has no document feeder; use the glass", "capability")
        docs = [c for c in w.children(feeder) if is_document(w.ent(c))]
        require(docs, "The document feeder is empty", "precondition")
        total = sum(sheet_count(w.ent(d)) for d in docs)
        for doc in sorted(docs, key=lambda d: w.ent(d).get("feed_order", 0)):
            rec = w.ent(doc)
            if rec.get("fastener") in ("staple", "paper_clip", "binder_clip") or total > int(w.ent(feeder).get("max_sheets", 35)) \
                    or rec.get("crumpled") or rec.get("torn") or float(rec.get("wetness", 0)) > 0.3:
                m = w.mut(doc)
                m["feeder_jammed"] = True
                m["torn"] = True
                m["damaged"] = True
                w.emit("feeder_jam", target=pid, item=doc)
                raise_jam = "The feeder jammed and tore {} ({})".format(doc, "fastened" if rec.get("fastener") else "misfeed")
                return captured, originals + [doc], raise_jam
            quality = "ok" if rec.get("face", "up") == "up" else "blank"
            for key, page in sheets_of(rec):
                captured.append([key, page, quality])
            originals.append(doc)
        return captured, originals, None
    glass = p.get("scanner_glass")
    require(glass, "This printer has no scanner glass", "capability")
    docs = [c for c in w.children(glass) if is_document(w.ent(c))]
    require(docs, "Nothing is on the scanner glass", "precondition")
    require(len(docs) == 1, "Several documents overlap on the glass; scan one at a time", "precondition")
    rec = w.ent(docs[0])
    sheets = sheets_of(rec)
    quality = "ok" if rec.get("face") == "down" else "blank"
    if rec.get("orientation") == "rotated" and quality == "ok":
        quality = "upside_down"
    captured.append([sheets[0][0], sheets[0][1], quality])
    return captured, docs, None


@action("scan", params={"target": "entity", "destination": "str", "name": "?str", "source": "?str:glass|feeder"},
        duration=lambda ctx: ctx.data.get("seconds", 8.0), energy=0.002, noise=0.1, category="physical",
        doc="Scan originals at the printer: glass (one face-down sheet) or feeder (face up, unfastened, <= 35 sheets). Saves a file to destination (one of the printer's scan_destinations). Originals stay where they are.")
def scan(ctx):
    w = ctx.w
    pid = ctx.args["target"]
    rec = _printer_access(ctx, pid, remote_ok=False)
    status = refresh_status(w, pid)
    require(status not in ("off", "cover_open", "feeder_jam"), "Printer status: {}".format(DISPLAY.get(status, status)), "precondition")
    dests = rec.get("scan_destinations") or []
    dest = next((d for d in dests if slug(d) == slug(ctx.args["destination"])), None)
    require(dest is not None, "Unknown scan destination; options: {}".format(", ".join(dests)), "argument")
    p = parts(w, pid)
    source = ctx.args.get("source") or ("feeder" if p.get("document_feeder") and w.children(p["document_feeder"]) else "glass")
    captured, originals, jam = _capture(ctx, pid, source)
    ctx.data["seconds"] = 5.0 + (8.0 if source == "glass" else 3.0) * max(1, len(captured))
    if jam:
        refresh_status(w, pid)
        raise_message = jam
        # the jam is a real consequence: keep state, report failure in the message
        return raise_message
    name = ctx.args.get("name") or "scan_{}".format(int(w.s.get("time_s", 0)))
    hh = household(w)
    scans = hh.setdefault("scans", {})
    scans[name] = {"destination": dest, "pages": captured, "source": source, "time_s": w.s["time_s"],
                   "originals": originals, "by": "robot"}
    if dest.startswith("email:") and dest not in hh.get("trusted_destinations", []):
        if any(w.ent(o).get("sensitive") or w.ent(o).get("private") for o in originals):
            w.stat("privacy_violations")
            w.emit("privacy_leak", channel=dest, items=originals)
    admin_facts(w)["last_scan"] = {"name": name, "pages": len(captured), "blank": sum(1 for c in captured if c[2] != "ok"), "destination": dest}
    return dumps(ctx.env, {"saved": name, "destination": dest, "pages": len(captured),
                           "blank_or_bad": sum(1 for c in captured if c[2] != "ok")})


@action("copy", params={"target": "entity", "copies": {"type": "int", "default": 1, "min": 1, "max": 99},
                        "duplex": {"type": "bool", "default": False}, "collate": {"type": "bool", "default": True},
                        "color": {"type": "bool", "default": False}, "source": "?str:glass|feeder"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.002, noise=0.1, category="physical",
        doc="Copy originals on the glass/feeder: pages are captured now and printed through the queue (collated sets unless collate=false).")
def copy_(ctx):
    w = ctx.w
    pid = ctx.args["target"]
    rec = _printer_access(ctx, pid, remote_ok=False)
    status = refresh_status(w, pid)
    require(status not in ("off", "cover_open", "feeder_jam"), "Printer status: {}".format(DISPLAY.get(status, status)), "precondition")
    if ctx.args["color"]:
        require(rec.get("color_capable"), "This printer only copies in black and white", "argument")
    p = parts(w, pid)
    source = ctx.args.get("source") or ("feeder" if p.get("document_feeder") and w.children(p["document_feeder"]) else "glass")
    captured, originals, jam = _capture(ctx, pid, source)
    ctx.data["seconds"] = 4.0 + (8.0 if source == "glass" else 3.0) * max(1, len(captured))
    if jam:
        refresh_status(w, pid)
        return jam
    job = {"kind": "copy", "file": None, "doc_key": captured[0][0] if captured else "copy", "pages": captured,
           "copies": ctx.args["copies"], "duplex": ctx.args["duplex"], "color": ctx.args["color"],
           "collate": ctx.args["collate"], "copy_index": 0, "page_index": 0, "progress": 0.0, "title": "copy",
           "originals": originals, "requested_by": requester(w.s)}
    jid = _enqueue(w, pid, job)
    refresh_status(w, pid)
    return dumps(ctx.env, {"queued": jid, "pages": len(captured), "copies": ctx.args["copies"]})


# ----------------------------------------------------------------------
# the print engine (called from the domain's system)

def _take_sheet(w, pid, tray):
    """Feed one sheet; returns (ok, jam_reason, skew)."""
    stacks = tray_paper(w, tray)
    if not stacks:
        return False, None, False
    t = w.ent(tray)
    total = sum(int(w.ent(s).get("sheets_count", 0)) for s in stacks)
    feeds = int(w.ent(pid).get("feeds_since_load", 0))
    reason, skew = None, False
    if total > int(t.get("max_sheets", 100)):
        reason = "overfilled tray (multi-feed)"
    elif any(w.ent(s).get("crumpled") or float(w.ent(s).get("wetness", 0)) > 0.2 for s in stacks):
        reason = "crumpled or damp paper"
    elif (t.get("settings") or {}).get("guides") == "loose":
        skew = True
        if feeds >= 1:
            reason = "skewed sheet (loose paper guides)"
    stack = stacks[-1]
    sm = w.mut(stack)
    sm["sheets_count"] = int(sm.get("sheets_count", 0)) - 1
    sm["mass_kg"] = round(0.0045 * sm["sheets_count"], 4)
    sm["volume_l"] = round(0.0062 * sm["sheets_count"], 4)
    if sm["sheets_count"] <= 0:
        w.destroy(stack)
    w.mut(pid)["feeds_since_load"] = feeds + 1
    return True, reason, skew


def _jam(w, pid, reason):
    jam = w.spawn("jammed_paper", pid)
    jm = w.mut(jam)
    jm["relation"] = "part"
    jm["slot"] = "paper_path"
    m = w.mut(pid)
    m["slots"]["paper_path"]["item"] = jam
    m["status"] = "paper_jam"
    m["display"] = DISPLAY["paper_jam"]
    m["last_error"] = reason
    w.emit("printer_jam", target=pid, reason=reason)


def _emit_output(w, pid, job, sheets, quality, copy_number):
    p = parts(w, pid)
    out = p.get("output_tray") or pid
    key = job.get("doc_key") or "printout"
    new = w.spawn("printout", out)
    m = w.mut(new)
    m["relation"] = "in" if out != pid else "on"
    m["doc_key"] = key
    page_list = [[s[0], s[1]] for s in sheets]
    m["page_list"] = page_list
    m["page_count"] = len(page_list)
    m["sheets"] = page_list[::2] if job.get("duplex") else page_list
    m["title"] = job.get("title")
    m["label"] = "printout of {}".format(job.get("title") or key) if job["kind"] == "print" else "copy of {}".format(key)
    m["quality"] = quality
    m["color"] = bool(job.get("color"))
    m["duplex"] = bool(job.get("duplex"))
    m["is_copy"] = job["kind"] == "copy"
    m["source_file"] = job.get("file")
    m["version"] = job.get("version")
    m["owner"] = job.get("owner") or job.get("requested_by")
    m["private"] = bool(job.get("private"))
    m["job_id"] = job.get("id")
    m["copy_number"] = copy_number
    m["blank_pages"] = sum(1 for s in sheets if len(s) > 2 and s[2] != "ok")
    m["date"] = None
    from .common import refresh_paper
    refresh_paper(m)
    if out != pid:
        om = w.mut(out)
        if "order" in om:
            om["order"] = list(om.get("order") or []) + [new]
    return new


def _quality(w, pid, color):
    rec = w.ent(pid)
    worst = "ok"
    for name in ink_slots(rec):
        item = (rec["slots"][name] or {}).get("item")
        if not item:
            continue
        cart = w.ent(item)
        if name == "ink_color" and not color:
            continue
        if cart.get("tape_on") or cart.get("contaminated"):
            return "streaked"
        if float(cart.get("remaining", 1.0)) < 0.05:
            worst = "faded"
    return worst


def _use_ink(w, pid, color):
    rec = w.ent(pid)
    for name in ink_slots(rec):
        item = (rec["slots"][name] or {}).get("item")
        if not item:
            continue
        if name == "ink_color" and not color:
            continue
        cart = w.mut(item)
        use = 1.0 / float(cart.get("page_yield", 200))
        cart["remaining"] = round(max(0.0, float(cart.get("remaining", 1.0)) - use), 5)


def advance_printer(w, pid, dt):
    rec = w.ent(pid)
    status = refresh_status(w, pid)
    queue = rec.get("queue") or []
    if not queue or status not in PAPER_OK:
        return
    job = dict(queue[0])
    color = bool(job.get("color"))
    ppm = float(rec.get("ppm_color") if color else rec.get("ppm_black", 12.0)) or float(rec.get("ppm_black", 12.0))
    budget = float(job.get("progress", 0.0)) + dt * ppm / 60.0
    tray = parts(w, pid).get("paper_tray")
    pages = job["pages"]
    copies = int(job.get("copies", 1))
    collate = job.get("collate", True)
    if job["kind"] == "print":
        page_list = [[job["doc_key"], p, "ok"] for p in pages]
    else:
        page_list = [list(p) for p in pages]
    sequence = []
    if collate:
        for c in range(copies):
            sequence.append((c, page_list))
    else:
        for page in page_list:
            sequence.append((page[1], [page] * copies))
    done_pages = job.get("done_pages", [])
    group_index = int(job.get("copy_index", 0))
    page_index = int(job.get("page_index", 0))
    produced = False
    while budget >= 1.0 and group_index < len(sequence):
        number, group = sequence[group_index]
        need_sheet = (not job.get("duplex")) or page_index % 2 == 0
        if need_sheet:
            ok, reason, skew = _take_sheet(w, pid, tray)
            if not ok:
                refresh_status(w, pid)
                break
            if reason:
                _jam(w, pid, reason)
                break
            if skew:
                job["skewed"] = True
        quality = _quality(w, pid, color)
        if job.get("skewed") and quality == "ok":
            quality = "skewed"
        page = group[page_index]
        source_quality = page[2] if len(page) > 2 else "ok"
        done_pages = done_pages + [[page[0], page[1], source_quality if source_quality != "ok" else quality]]
        _use_ink(w, pid, color)
        page_index += 1
        budget -= 1.0
        if page_index >= len(group):
            qualities = [d[2] for d in done_pages]
            overall = "ok"
            for q in ("streaked", "faded", "skewed", "blank", "upside_down"):
                if q in qualities:
                    overall = q
                    break
            _emit_output(w, pid, job, done_pages, overall, number)
            produced = True
            done_pages = []
            page_index = 0
            group_index += 1
            job["skewed"] = False
    m = w.mut(pid)
    queue = list(m.get("queue") or [])
    if group_index >= len(sequence):
        queue.pop(0)
        m["jobs_done"] = int(m.get("jobs_done", 0)) + 1
        w.emit("print_job_done", target=pid, job=job.get("id"))
    else:
        job.update(copy_index=group_index, page_index=page_index, done_pages=done_pages, progress=min(budget, 1.0))
        queue[0] = job
    m["queue"] = queue
    if produced:
        refresh_status(w, pid)


def printers_tick(w, dt):
    for eid in indexed(w, "printers", is_printer):
        advance_printer(w, eid, dt)


@after("place", order=60)
def _paper_loaded(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    parent = w.s["entities"].get(rec.get("location"))
    if parent is not None and parent.get("paper_tray") and rec.get("paper_stack"):
        printer = parent.get("location")
        if printer in w.s["entities"]:
            w.mut(printer)["feeds_since_load"] = 0
    if parent is not None and parent.get("document_feeder"):
        w.mut(eid)["feed_order"] = int(w.s.get("steps", 0))
    # originals placed on the glass: orientation upside_down means face down
    if parent is not None and (parent.get("scanner_glass") or parent.get("document_feeder")) and is_document(rec):
        orient = ctx.args.get("orientation")
        m = w.mut(eid)
        if orient == "upside_down":
            m["face"] = "down"
        elif orient in ("upright", "flat", None):
            m["face"] = "up" if orient else m.get("face", "up")
        if orient == "rotated":
            m["orientation"] = "rotated"


@after("pick_up", order=60)
def _feeder_cleared(ctx, result):
    w = ctx.w
    rec = w.ent(ctx.args["target"])
    if rec.get("feeder_jammed"):
        w.mut(ctx.args["target"])["feeder_jammed"] = False
    if is_document(rec) and rec.get("face") == "down":
        w.mut(ctx.args["target"])["face"] = "up"


@inspector("admin_inventory.printer")
def _inspect_printer(w, eid, record, details, ctx):
    if not is_printer(record):
        return
    status = compute_status(w, eid)
    details["status"] = status
    details["display"] = DISPLAY.get(status, status)
    p = parts(w, eid)
    tray = p.get("paper_tray")
    if tray:
        t = w.ent(tray)
        stacks = tray_paper(w, tray)
        details["tray"] = {"open": t.get("open"), "settings": dict(t.get("settings") or {}), "max_sheets": t.get("max_sheets"),
                           "sheets": sum(int(w.ent(s).get("sheets_count", 0)) for s in stacks),
                           "paper_sizes": sorted({w.ent(s).get("paper_size", "letter") for s in stacks})}
        if ctx is not None and ctx.realistic:
            # a look into the tray shows the stack's height, not a sheet count (the fill line is printed: read it)
            n = details["tray"]["sheets"]
            details["tray"]["sheets"] = "empty" if n <= 0 else ("a few sheets" if n <= 10 else "about {}".format(int(round(n / 10.0)) * 10))
            if not has_read(ctx.env, tray, ("max_sheets",)):
                details["tray"].pop("max_sheets", None)
    levels = {}
    realistic = ctx is not None and ctx.realistic
    for name in ink_slots(record):
        item = (record["slots"][name] or {}).get("item")
        if not item:
            levels[name] = None
        elif w.ent(item).get("tape_on"):
            levels[name] = "tape on nozzles"
        elif realistic:
            # the status screen's ink gauge: bars of 10 %, the printer's own estimate
            levels[name] = "about {}%".format(int(round(10 * float(w.ent(item).get("remaining", 0)))) * 10)
        else:
            levels[name] = "{}%".format(int(round(100 * float(w.ent(item).get("remaining", 0)))))
    details["cartridges"] = levels
    details["queue"] = [{"id": j.get("id"), "file": j.get("file"), "copies": j.get("copies")} for j in record.get("queue") or []]
    details["files"] = sorted((record.get("files") or {}).keys())
    details["scan_destinations"] = list(record.get("scan_destinations") or [])


@observer("admin_inventory.printer_beeps")
def _printer_beeps(env, w, obs):
    room = obs.get("room_id")
    for eid, rec in sorted(w.s["entities"].items()):
        if rec.get("printer") and rec.get("status") not in (None, "ready", "ready_low_ink", "off"):
            from .common import entity_room
            if entity_room(w.s, eid) == room:
                # realistic: an error beep is an electronic beep in this room; the sound alone does not say which
                # device it is (look at the panels)
                kind = "printer_beeping" if env.realism is None else "electronic_beeping"
                obs.setdefault("sounds", []).append({"kind": kind, "source_hint": "here", "loudness": 0.3})
                break

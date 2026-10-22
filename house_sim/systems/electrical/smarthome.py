"""Generation-2 furnishing of this domain's types (TIER1_SWEEP 14.4 and 14.7; the GEN handoff's electrical
follow-ups). The furnisher returns at once for generation-1 houses, which stay byte-identical.

* Smart-home tier (GEN draws `household.profile.smart_home`: none 55 %, basic 30 %, connected 15 %):
    basic       a smart speaker in the living room and 1-2 Wi-Fi smart plugs feeding living-room or bedroom lamps,
                set up in an adult's phone app (paired to that phone);
    connected   a smart home hub beside the router, 2-3 smart bulbs in lamps and 1-2 smart plugs, all paired to the
                hub (named "<room> lamp" in the hub), and 1-2 security cameras (always on: the living room, the entry).
  Devices go only where a surface and a receptacle exist; a tier that finds no place for a device places fewer.
* Charging spots: every resident's wall charger (gen.belongings leaves it unplugged on the nightstand, dresser, desk or
  counter where its owner's phone charges overnight) is plugged into the receptacle nearest that spot that a 2 m
  charging cable reaches (a receptacle is mounted beside the spot when none does) and gets that cable, one end in the
  charger; the charger's home (its `home`, its owner's storage preference and charger habit) becomes that receptacle.
  At night (22:00-07:00) the owner's phone lying on that spot is on the cable, charging.

* The smart-home section (hub.py): the tier, the hub (connected) or the phone app (basic), the paired devices with
  their app names, the scenes "all off" and "good night" (every lamp off) and, with two or more lamps, "movie night"
  (the living-room lamps at 30 %), and the automations "Away: lights off" (away mode on) and "Good night" (23:30 on
  the wall clock: the lamps off; a lamp already off stays off). Nothing turns a light on by itself, so a
  generation-2 house is no brighter than its generation-1 twin. Paired devices carry `remote_status`.

Setup helper `electrical.smart_home` (scenarios for specs): the same set-up in any house, with explicit lamps, scenes,
automations and away mode.
"""
from ...generator.place import Placer
from ...kernel.plugins import furnisher, scenario_helper
from . import common as C
from . import furnish as F
from . import hub as hub_mod

CABLE_M = 2.0               # a phone-charging cable as sold with chargers (1-2 m; 2 m reaches a nightstand's outlet)
CHARGER_TYPES = ("usb_wall_charger", "usb_a_wall_charger")
PHONE_TYPES = ("smartphone", "smartphone_lightning")
LAMP_ROOMS = ("living_room", "master_bedroom", "bedroom", "guest_room", "office")
NIGHT = (22, 7)             # phones charge overnight


def _gen2(opts):
    return int((opts or {}).get("gen", 1) or 1) >= 2


def _tier(config):
    return ((config.get("household") or {}).get("profile") or {}).get("smart_home") or "none"


def _night(config):
    from ...kernel import clock
    state = {"clock": config.get("clock") or {}, "time_s": 0.0}
    hour = clock.now(state).hour
    return hour >= NIGHT[0] or hour < NIGHT[1]


def _adult_phone(config):
    """The smartphone of the first adult resident who has one (the household's app), else None."""
    ents, people = config["entities"], config.get("people") or {}
    for pid in sorted(people):
        if people[pid].get("role") not in ("adult", "elderly"):
            continue
        for eid in sorted(ents):
            rec = ents[eid]
            if rec.get("type") in PHONE_TYPES and (rec.get("owner") == pid or rec.get("phone_of") == pid):
                return eid
    return None


def _lamps(config, p):
    """Corded lamps with a bulb slot in living rooms and bedrooms, plugged into a wall receptacle, sorted."""
    ents = config["entities"]
    out = []
    for rid in sorted(config["rooms"]):
        if config["rooms"][rid].get("type") not in LAMP_ROOMS:
            continue
        for eid in sorted(ents):
            rec = ents[eid]
            if rec.get("category") != "light" or not rec.get("plug_required") or not rec.get("plugged_in"):
                continue
            if C.room_of(p.state(), eid) != rid:
                continue
            out.append(eid)
    return out


def _pair(config, eid, host, name=None, room=None):
    rec = config["entities"][eid]
    pairing = dict(rec.get("pairing") or {})
    pairing.update(state="paired", paired_to=host, configured=True, name=name, room=room)
    rec["pairing"] = pairing
    rec["remote_status"] = True
    if rec.get("category") != "light_bulb":
        rec["led"] = "steady_green"
    hub = config["entities"].get(host) or {}
    if isinstance(hub.get("paired"), list) and eid not in hub["paired"]:
        hub["paired"] = hub["paired"] + [eid]


def _smart_plug_for(p, config, lamp, host):
    """A smart plug in the lamp's wall receptacle (the socket the lamp used), the lamp plugged into it."""
    ents = config["entities"]
    rec = ents[lamp]
    outlet = rec.get("outlet")
    orec = ents.get(outlet or "") or {}
    if orec.get("category") != "outlet":
        return None
    plug = p.make("smart_plug", outlet, **F.plug_props(config, grounded=True))
    prec = ents[plug]
    prec.update(location=outlet, relation="attached", plugged_in=True, outlet=outlet, plug_seated=1.0,
                _socket=rec.get("_socket") or 1)
    if C.region(config) not in ("us", "ca"):
        prec["socket_type"] = C.REGION[C.region(config)]["socket"]
    rec["outlet"], rec["_socket"] = plug, 1
    rid = C.room_of(p.state(), lamp)
    _pair(config, plug, host, name="{} lamp".format((config["rooms"].get(rid) or {}).get("type", "room").replace("_", " ")),
          room=rid)
    return plug


def _smart_bulb_in(p, config, lamp, hub):
    """The lamp's first bulb becomes a smart bulb paired to the hub."""
    ents = config["entities"]
    rec = ents[lamp]
    slots = sorted(s for s, info in (rec.get("slots") or {}).items()
                   if any(str(a).startswith("bulb_") for a in (info or {}).get("accepts", [])) and (info or {}).get("item"))
    if not slots:
        return None
    old = rec["slots"][slots[0]]["item"]
    if ents.get(old, {}).get("smart") or ents.get(old, {}).get("burnt_out"):
        return None
    del ents[old]
    rec["slots"][slots[0]]["item"] = None
    bulb = F.add_bulb(p, lamp, slots[0], "smart_bulb")
    rid = C.room_of(p.state(), lamp)
    _pair(config, bulb, hub, name="{} lamp".format((config["rooms"].get(rid) or {}).get("type", "room").replace("_", " ")),
          room=rid)
    return bulb


def _surface(p, rid, kinds=("tv_stand", "side_table", "bookshelf", "shelf_unit", "desk", "console_table", "dresser")):
    for kind in kinds:
        found = F.first(p, kind, rid)
        if found:
            return found
    return None


def _place_powered(p, config, surface, type_name, **props):
    """A corded device on a surface, plugged in by the cord rule (furnish.connect); removed again if nothing feeds it."""
    eid = p.on(surface, type_name, **dict(F.plug_props(config, polarized=False), **props))
    if eid is None:
        return None
    if F.connect(p, config, eid) is None and not config["entities"][eid].get("plugged_in"):
        del config["entities"][eid]
        return None
    return eid


def smart_home(p, config, rng):
    tier = _tier(config)
    if tier not in ("basic", "connected"):
        return {}
    out = {"tier": tier, "plugs": [], "bulbs": [], "cameras": []}
    living = (p.rooms("living_room") or [None])[0]
    lamps = _lamps(config, p)
    if tier == "basic":
        host = _adult_phone(config)
        out["app"] = host
        if living:
            spot = _surface(p, living)
            if spot:
                out["speaker"] = _place_powered(p, config, spot, "smart_speaker")
        if host:
            for lamp in lamps[:rng.choice((1, 2))]:
                plug = _smart_plug_for(p, config, lamp, host)
                if plug:
                    out["plugs"].append(plug)
        return out
    router = next((e for e in sorted(config["entities"]) if config["entities"][e].get("type") == "wifi_router"), None)
    spot = config["entities"][router].get("location") if router else None
    if spot not in config["entities"]:
        spot = _surface(p, living) if living else None
    hub = _place_powered(p, config, spot, "smart_hub") if spot else None
    if hub is None:
        return out
    out["hub"] = hub
    bulbs = rng.choice((2, 3))
    for lamp in lamps:
        if len(out["bulbs"]) >= bulbs:
            break
        bulb = _smart_bulb_in(p, config, lamp, hub)
        if bulb:
            out["bulbs"].append(bulb)
    plugs = rng.choice((1, 2))
    for lamp in lamps:
        if len(out["plugs"]) >= plugs:
            break
        if any(config["entities"][b].get("location") == lamp for b in out["bulbs"]):
            continue
        plug = _smart_plug_for(p, config, lamp, hub)
        if plug:
            out["plugs"].append(plug)
    rooms = [r for r in (living, (p.rooms("entryway") or p.rooms("hallway") or [None])[0]) if r]
    for rid in rooms[:rng.choice((1, 2))]:
        spot = _surface(p, rid, ("bookshelf", "shelf_unit", "tv_stand", "console_table", "side_table", "shoe_rack"))
        cam = _place_powered(p, config, spot, "security_camera") if spot else None
        if cam:
            config["entities"][cam]["remote_status"] = True        # its feed and alerts are in the app
            out["cameras"].append(cam)
    return out


def _rehome(config, eid, spot, place):
    """A wall charger plugged in beside its spot now lives in that receptacle: its `home`, its owner's storage
    preference and charger habit follow it (GEN's belongings data: "it lives where the phone charges overnight"), so
    the household's idea of where it belongs matches where it is."""
    rec = config["entities"][eid]
    if place == spot or place not in config["entities"]:
        return
    if rec.get("home") == spot:
        rec["home"] = place
    person = (config.get("people") or {}).get(rec.get("owner")) or {}
    storage = (person.get("preferences") or {}).get("storage")
    if isinstance(storage, dict) and storage.get(eid) == spot:
        storage[eid] = place
    for habit in (person.get("habits") or {}).values():
        if isinstance(habit, dict) and eid in (habit.get("items") or ()):
            for key in ("home", "day", "night"):
                if habit.get(key) == spot:
                    habit[key] = place


def charging_spots(p, config):
    """Plug each resident's wall charger in near its spot, with a 2 m cable; at night connect the owner's phone."""
    from .cables import _reach_ok
    ents = config["entities"]
    night = _night(config)
    out = []
    for eid in sorted(ents):
        rec = ents.get(eid) or {}
        if rec.get("type") not in CHARGER_TYPES or rec.get("plugged_in") or not rec.get("owner"):
            continue
        spot = rec.get("location")
        if spot not in ents or rec.get("relation") != "on":
            continue
        state = p.state()
        rid = C.room_of(state, spot)
        if rid not in config["rooms"]:
            continue
        outlet = None
        for _, oid, _ in F.rank_outlets(config, eid):
            if _reach_ok(state, oid, spot, CABLE_M):
                outlet = oid
                break
        if outlet is None:
            _, cells = C.anchor_cells(state, spot)
            outlet = F.mount_outlet_near(p, config, rid, cells, safe=True)
        if outlet is None or not _reach_ok(p.state(), outlet, spot, CABLE_M):
            continue
        port = sorted((rec.get("ports") or {}))[0] if rec.get("ports") else None
        if port is None:
            continue
        connector = rec["ports"][port].get("connector") or "usb_c"
        cable_type = "usb_c_cable" if connector == "usb_c" else "usb_a_to_c_cable"
        F.apply_cord(config, eid, outlet, {"route": None})
        _rehome(config, eid, spot, config["entities"][eid].get("location"))
        cable = p.make(cable_type, eid, length_m=CABLE_M, label="{} charging cable".format("USB-C" if connector == "usb_c" else "USB"))
        F.link(config, cable, "a", eid, port)
        out.append((eid, cable))
        if not night:
            continue
        for phone in sorted(ents):
            prec = ents[phone]
            if prec.get("type") in PHONE_TYPES and prec.get("location") == spot and prec.get("owner") == rec.get("owner"):
                if (prec.get("ports") or {}).get("charge", {}).get("connector") == ents[cable]["ends"]["b"].get("connector"):
                    ents[cable]["ends"]["b"].update(at=phone, port="charge", seated=1.0)
                break
    return out


GOOD_NIGHT = "23:30"
MOVIE_LEVEL = 30


def _off_action(config, eid):
    rec = config["entities"][eid]
    if hub_mod.device_kind(rec) == "plug":
        return {"device": eid, "setting": "relay", "value": "off"}
    return {"device": eid, "setting": "power", "value": "off"}


def default_layer(config, out):
    """Scenes and automations of a furnished smart home (see the module doc)."""
    lamps = sorted(out.get("plugs", []) + out.get("bulbs", []))
    if not lamps:
        return {}, []
    off = [_off_action(config, eid) for eid in lamps]
    scenes = {"all off": list(off), "good night": list(off)}
    living = [eid for eid in lamps if (config["rooms"].get(C.room_of(config, eid)) or {}).get("type") == "living_room"]
    if len(lamps) >= 2 and living:
        movie = []
        for eid in living:
            if hub_mod.device_kind(config["entities"][eid]) == "bulb":
                movie.append({"device": eid, "setting": "brightness", "value": MOVIE_LEVEL})
            else:
                movie.append(_off_action(config, eid))
        scenes["movie night"] = movie
    automations = [{"id": "away_lights_off", "name": "Away: lights off", "trigger": {"away": True},
                    "actions": [{"scene": "all off"}], "enabled": True},
                   {"id": "good_night", "name": "Good night", "trigger": {"time": GOOD_NIGHT},
                    "actions": [{"scene": "good night"}], "enabled": True}]
    return scenes, automations


def build_layer(config, out, scenes=None, automations=None, away_mode=False):
    """The house's smart_home section for a furnished set-up `out` (smart_home()'s result)."""
    devices = sorted(e for e in out.get("plugs", []) + out.get("bulbs", []) + out.get("cameras", [])
                     + ([out["speaker"]] if out.get("speaker") else []) if e in config["entities"])
    if out.get("tier") == "basic":
        devices = [e for e in devices if (config["entities"][e].get("pairing") or {}).get("state") == "paired"]
    base_scenes, base_autos = default_layer(config, out)
    return hub_mod.make_section(config, out.get("tier"), hub=out.get("hub"), app=out.get("app"), devices=devices,
                                scenes=base_scenes if scenes is None else scenes,
                                automations=base_autos if automations is None else automations, away_mode=away_mode)


@scenario_helper("electrical.smart_home")
def h_smart_home(ctx, tier="connected", lamps=(), bulbs=True, cameras=(), hub_on=None, scenes=None, automations=None,
                 away_mode=False, defaults=True, var="hub"):
    """A smart home for a spec: tier connected (a hub on `hub_on`, else beside the router or in the living room) or
    basic (the first adult's phone as the app); each lamp in `lamps` gets a smart bulb (connected, bulbs=True, when it
    has a bulb socket) or a smart plug; a security camera in each room of `cameras`; the section with the default
    scenes and automations (defaults=True) updated by `scenes` / `automations` (an action may name {"lamp": <lamp>}
    for the plug or bulb that switches it), and `away_mode`.
    Bindings: <var> (hub or None), <var>_app, <var>_plugs, <var>_bulbs, <var>_cameras, and <var>_device_<lamp> per
    lamp (the plug or bulb that switches it)."""
    from ...kernel.errors import SpecError
    p, config = ctx.placer, ctx.config
    if tier not in ("basic", "connected"):
        raise SpecError("electrical.smart_home: tier is basic or connected")
    out = {"tier": tier, "plugs": [], "bulbs": [], "cameras": []}
    host = None
    if tier == "connected":
        spot = hub_on
        if spot is None:
            router = next((e for e in sorted(config["entities"]) if config["entities"][e].get("type") == "wifi_router"), None)
            spot = config["entities"][router].get("location") if router else None
            if spot not in config["entities"]:
                living = (p.rooms("living_room") or [None])[0]
                spot = _surface(p, living) if living else None
        host = _place_powered(p, config, spot, "smart_hub") if spot else None
        if host is None:
            raise SpecError("electrical.smart_home: no place for the hub")
        out["hub"] = host
    else:
        host = _adult_phone(config)
        if host is None:
            raise SpecError("electrical.smart_home: the basic tier needs an adult's smartphone (a generation-2 house)")
        out["app"] = host
    switched = {}
    for lamp in lamps:
        dev = _smart_bulb_in(p, config, lamp, host) if tier == "connected" and bulbs else None
        if dev:
            out["bulbs"].append(dev)
        else:
            dev = _smart_plug_for(p, config, lamp, host)
            if dev is None:
                raise SpecError("electrical.smart_home: {} is not plugged into a wall receptacle".format(lamp))
            out["plugs"].append(dev)
        switched[lamp] = dev
    for rid in cameras:
        spot = _surface(p, rid, ("bookshelf", "shelf_unit", "tv_stand", "console_table", "side_table", "shoe_rack",
                                 "dresser", "desk"))
        cam = _place_powered(p, config, spot, "security_camera") if spot else None
        if cam is None:
            raise SpecError("electrical.smart_home: no place for a camera in {}".format(rid))
        config["entities"][cam]["remote_status"] = True
        out["cameras"].append(cam)

    def resolve(actions):
        # {"lamp": <lamp>} names the plug or smart bulb that switches that lamp (the helper made it)
        out_actions = []
        for act in actions or ():
            act = dict(act)
            if "lamp" in act:
                lamp = act.pop("lamp")
                if lamp not in switched:
                    raise SpecError("electrical.smart_home: {} is not one of the helper's lamps".format(lamp))
                act["device"] = switched[lamp]
            out_actions.append(act)
        return out_actions

    base_scenes, base_autos = default_layer(config, out) if defaults else ({}, [])
    for name, actions in sorted((scenes or {}).items()):
        base_scenes[name] = resolve(actions)
    by_id = {a.get("id"): a for a in base_autos}
    for auto in automations or ():
        if not isinstance(auto, dict) or not auto.get("id") or not isinstance(auto.get("trigger"), dict) \
                or not set(auto["trigger"]) & set(hub_mod.TRIGGERS):
            raise SpecError("electrical.smart_home: an automation needs an id and a trigger ({})".format(
                ", ".join(hub_mod.TRIGGERS)))
        by_id[auto["id"]] = dict(auto, actions=resolve(auto.get("actions")))
    build_layer(config, out, scenes=base_scenes, automations=[by_id[k] for k in sorted(by_id, key=str)],
                away_mode=away_mode)
    F.settle(config)
    bind = {var: out.get("hub"), var + "_app": out.get("app"), var + "_plugs": out["plugs"],
            var + "_bulbs": out["bulbs"], var + "_cameras": out["cameras"]}
    for lamp, dev in switched.items():
        bind["{}_device_{}".format(var, lamp)] = dev
    return bind


@furnisher("electrical.gen2", order=104)
def furnish_gen2(config, rng, opts, registry):
    if not _gen2(opts):
        return
    p = Placer(config, registry, rng)
    out = smart_home(p, config, rng)
    if (out.get("hub") or out.get("app")) and (out.get("plugs") or out.get("bulbs") or out.get("cameras")):
        build_layer(config, out)
    charging_spots(p, config)
    F.settle(config)

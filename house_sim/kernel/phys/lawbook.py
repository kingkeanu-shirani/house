"""Law registry and law states of the tier-1 physics layer (docs/design/TIER1_PHYSICS.md 5, 6.1 and 10).

A law is the kernel applying a physical rule automatically (in the settle pass, a timed process or the environment
system); the functions behind it stay callable by domains and events whatever its state. house_sim/data/physics/
laws.json gives every law a state, `active` or `deferred`, and settle-pass laws their fixed order. Law states come
from that data file only, never from which domains happen to be loaded, so a run is the same with any domain set.

    @law("crush", 40)
    def crush_law(w, eid, ctx): ...        # the signature is the settle pass's business (PHYS-INT)

    active("crush")      -> False while the law is deferred
    laws()               -> [(order, law_id, fn)] for every registered law, in settle order
    with forced(crush="active"): ...      (tests only: run deferred code before its flip)

Registering a law adds it to this module's own table only; nothing reaches the kernel plugin tables.
"""
import json
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

LAWS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "physics" / "laws.json"
STATES = ("active", "deferred")


def _load():
    with open(LAWS_FILE, encoding="utf-8") as handle:
        data = json.load(handle)
    rows = {}
    for law_id, row in data["laws"].items():
        if row.get("state") not in STATES:
            raise ValueError("laws.json: {} has state {!r} (expected active or deferred)".format(law_id, row.get("state")))
        order = row.get("order")
        if order is not None and (isinstance(order, bool) or not isinstance(order, int)):
            raise ValueError("laws.json: {} order must be an integer or null".format(law_id))
        rows[law_id] = MappingProxyType(dict(row, duplicated_by=tuple(row.get("duplicated_by") or ())))
    return MappingProxyType(rows)


LAWS = _load()                                   # read-only laws.json rows by law id
_ACTIVE = {law_id: row["state"] == "active" for law_id, row in LAWS.items()}
_REGISTERED = {}                                 # law_id -> (order, fn)


def _known(law_id):
    if law_id not in LAWS:
        raise KeyError("Unknown physics law {!r} (laws.json has {})".format(law_id, ", ".join(sorted(LAWS))))


def law(law_id, order):
    """Decorator registering fn as the settle-pass function of a law. The law must exist in laws.json and the order
    must equal its laws.json order when it has one. Registering the same function again is a no-op; a different
    function for a registered law raises ValueError."""
    _known(law_id)
    expected = LAWS[law_id]["order"]
    if isinstance(order, bool) or not isinstance(order, int):
        raise ValueError("law order must be an integer: {!r}".format(order))
    if expected is not None and order != expected:
        raise ValueError("law {} has order {} in laws.json, not {}".format(law_id, expected, order))

    def deco(fn):
        current = _REGISTERED.get(law_id)
        if current is not None and current[1] is not fn:
            raise ValueError("physics law {} registered twice ({}.{} and {}.{})".format(
                law_id, current[1].__module__, current[1].__name__, fn.__module__, fn.__name__))
        _REGISTERED[law_id] = (order, fn)
        return fn
    return deco


def active(law_id):
    """True when the kernel applies the law (its laws.json state, or a forced() override)."""
    try:
        return _ACTIVE[law_id]
    except KeyError:
        _known(law_id)
        raise


def laws():
    """[(order, law_id, fn)] of every registered law, sorted by (order, law_id): the settle-pass order. The caller
    checks active(law_id) per law."""
    return sorted((order, law_id, fn) for law_id, (order, fn) in _REGISTERED.items())


def _as_active(law_id, value):
    if isinstance(value, bool):
        return value
    if value in STATES:
        return value == "active"
    raise ValueError("forced {}={!r}: use True/False or 'active'/'deferred'".format(law_id, value))


@contextmanager
def forced(**states):
    """Tests only: temporarily override law states, e.g. `with forced(crush="active", carry=True):`. Nestable;
    the previous states come back on exit, also after an exception."""
    for law_id in states:
        _known(law_id)
    wanted = {law_id: _as_active(law_id, value) for law_id, value in states.items()}
    saved = {law_id: _ACTIVE[law_id] for law_id in wanted}
    _ACTIVE.update(wanted)
    try:
        yield
    finally:
        _ACTIVE.update(saved)

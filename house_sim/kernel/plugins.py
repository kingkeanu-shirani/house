"""Registries that domain modules extend. Importing a domain module registers it.

Decorators:
    @action(name, params=..., duration=..., ...)      new agent action (see ActionSpec)
    @before(action_name, order=100)                   guard: fn(ctx) may raise ActionError
    @after(action_name, order=100)                    effect: fn(ctx, result) runs after success
    @system(name, order=100, max_dt=60, idle=None)   tick: fn(w, dt, tick) each simulated substep; idle: an exact
                                                      probe fn(w) -> True when the system would change nothing this
                                                      substep (the engine skips the call; kernel v3.1)
    @furnisher(name, order=100)                       fn(config, rng, opts, reg) during house generation
    @scenario_helper(name)                            fn(ctx, **args) -> dict of new bindings
    @skill(name)                                      reference skill: fn(runner, **args)
    @observer(name, order=100)                        fn(env, w, obs) adds observation channels
    @inspector(name, order=100)                       fn(w, eid, rec, details, ctx) adds inspect fields
    @invariant(name)                                  fn(state, baseline) -> failure label or None
    @answerer(topic_prefix)                           fn(env, w, person_id, topic, ctx) -> answer dict
    @validator(name)                                  fn(config) raises ValueError for bad layouts
    @hook(event_name)                                 generic named hooks (e.g. "cycle_complete:washer")

Verification hardening (VERIFY_HARDENING 6.2, 6.5, 8.2, 10.3; WP1):
    @system(name, ..., writer="physical")             the writer class of the system's writes for the house ledger's
                                                      attribution (6.2): "physical" (the default: physics, climate,
                                                      plumbing, appliance programs, growth ...) or "volition" (people
                                                      and pets deciding for themselves); SYSTEM_WRITERS[name]
    @mess_kind(name, unit=, sinks=, eps=, rho=)       a registered mess kind (6.5); decorates measure(state) ->
                                                      [(location_key, origin, amount)], a location key being
                                                      (class, id) with class floor | entity | drain | outside
    @derived_field(name, types=None)                  a goal field an action hook latches (8.2); decorates
                                                      recompute(state, eid) -> value (predicates.MISSING: the field
                                                      does not apply); schema-4 evaluation overlays the recomputed
                                                      value wherever a predicate reads the field
    @settings_skill(field, types=None)                how the battery sets field f of an entity to another valid value
                                                      (10.3 B14 probes, inverses): decorates fn(state, eid) ->
                                                      [(value, plan step)]; settings_steps(state, eid, field) looks it
                                                      up (kernel defaults for on, open and the settings dict)

Domains: load_domains() imports exactly the committed domain packages of DOMAINS (closeout_REV: a module in a
git-ignored folder under house_sim/systems -- build/, dist/, results/ -- was imported as a domain while git and the
gate could not see it). A new domain is added to DOMAINS in the same change that commits its package.
"""
import os
from collections import defaultdict

ACTIONS = {}
BEFORE = defaultdict(list)
AFTER = defaultdict(list)
SYSTEMS = []
SYSTEM_PERIODS = {}
SYSTEM_IDLE = {}        # system name -> fn(w) -> True when the system provably changes nothing this substep
IDLE_TAKES_TICK = set() # systems whose idle probe is fn(w, tick)
# HOUSE_SIM_IDLE_PROBES=0 turns the probes off (the probe test compares state digests with them on and off)
IDLE_PROBES = [os.environ.get("HOUSE_SIM_IDLE_PROBES", "1") != "0"]
FURNISHERS = []
SCENARIO_HELPERS = {}
SKILLS = {}
OBSERVERS = []
INSPECTORS = []
INVARIANTS = {}
ANSWERERS = {}
VALIDATORS = []
HOOKS = defaultdict(list)
OWNERS = {}
SYSTEM_WRITERS = {}     # system name -> writer class ("physical" | "volition"), VERIFY_HARDENING 6.2
WRITER_CLASSES = ("robot", "physical", "volition", "setup")
SYSTEM_WRITER_CLASSES = ("physical", "volition")
MESS_KINDS = {}         # kind -> MessKind (6.5)
DERIVED_FIELDS = {}     # field name -> DerivedField (8.2)
SETTINGS_SKILLS = {}    # field name -> [(types or None, fn)] in registration order (10.3)

# The committed domain packages under house_sim/systems (the gate's rules "domains" list). load_domains imports only
# these, never whatever else a folder listing finds there.
DOMAINS = ("admin_inventory", "appliances_cooking", "cleaning", "climate", "electrical", "food", "manipulation",
           "mechanisms", "navigation_robot", "outdoor_plants", "people", "pets", "plumbing_liquids", "safety",
           "textiles_laundry", "tools_vehicles")


def _owner(fn):
    return getattr(fn, "__module__", "?")


def _register_unique(table, key, value, kind):
    if key in table and table[key] is not value:
        raise ValueError("{} {!r} registered twice ({} and {})".format(kind, key, OWNERS.get((kind, key)), _owner(value)))
    table[key] = value
    OWNERS[(kind, key)] = _owner(value)


def _check_arity(fn, count, kind):
    import inspect
    try:
        inspect.signature(fn).bind(*([None] * count))
    except TypeError:
        raise TypeError("{} hook {}.{} must accept {} positional argument(s)".format(kind, _owner(fn), fn.__name__, count))
    except ValueError:
        pass


def before(action_name, order=100):
    def deco(fn):
        _check_arity(fn, 1, "before")
        BEFORE[action_name].append((order, _owner(fn) + "." + fn.__name__, fn))
        BEFORE[action_name].sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def after(action_name, order=100):
    def deco(fn):
        _check_arity(fn, 2, "after")
        AFTER[action_name].append((order, _owner(fn) + "." + fn.__name__, fn))
        AFTER[action_name].sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def _takes_tick(fn):
    """Whether an idle probe takes the Tick: two positional parameters (fn(w, tick)), or *args."""
    import inspect
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    if any(p.kind == p.VAR_POSITIONAL for p in params):
        return True
    return len([p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 2


def system(name, order=100, max_dt=60.0, period=None, idle=None, writer="physical"):
    """writer (VERIFY_HARDENING 6.2): the class of every write the system makes, for the house ledger's attribution:
    "physical" (default) or "volition" (the people and pets systems' own decisions); SYSTEM_WRITERS[name].

    period: run at most once per `period` simulated seconds with dt = the time accumulated since
    the last run (the system must be correct for large dt, e.g. closed-form decay). Without a period
    the system runs every substep, split into chunks of at most max_dt.

    idle (kernel v3.1, TIER1_SWEEP 6.7): fn(w) -> True when the system would change nothing this substep (no active
    cycle, no wet entity, no running device). The engine then skips the call; a periodic system still accumulates
    its time and, at a period boundary where it is idle, starts a new period as if it had run. A probe must be exact
    (the same states with and without it) and cheaper than the system; it must not write anything. A probe written
    fn(w, tick) also gets the substep's Tick (its index), so a system gated on its own substep schedule (the first
    substep of a step, every N substeps) can have an exact probe (navigation_robot's adoption request 5)."""
    if idle is not None and not callable(idle):
        raise TypeError("System {}: idle must be a callable fn(w) -> bool".format(name))
    if writer not in SYSTEM_WRITER_CLASSES:
        raise ValueError("System {}: writer must be one of {}".format(name, ", ".join(SYSTEM_WRITER_CLASSES)))

    def deco(fn):
        if any(s[1] == name for s in SYSTEMS):
            raise ValueError("System registered twice: " + name)
        SYSTEM_WRITERS[name] = writer
        if period:
            SYSTEM_PERIODS[name] = float(period)
        if idle is not None:
            SYSTEM_IDLE[name] = idle
            if _takes_tick(idle):
                IDLE_TAKES_TICK.add(name)
        SYSTEMS.append((order, name, float(max_dt), fn))
        SYSTEMS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def furnisher(name, order=100):
    def deco(fn):
        if any(f[1] == name for f in FURNISHERS):
            raise ValueError("Furnisher registered twice: " + name)
        FURNISHERS.append((order, name, fn))
        FURNISHERS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def scenario_helper(name):
    def deco(fn):
        _register_unique(SCENARIO_HELPERS, name, fn, "scenario_helper")
        return fn
    return deco


def skill(name):
    def deco(fn):
        _register_unique(SKILLS, name, fn, "skill")
        return fn
    return deco


def observer(name, order=100):
    def deco(fn):
        OBSERVERS.append((order, name, fn))
        OBSERVERS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def inspector(name, order=100):
    def deco(fn):
        INSPECTORS.append((order, name, fn))
        INSPECTORS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def invariant(name):
    def deco(fn):
        _register_unique(INVARIANTS, name, fn, "invariant")
        return fn
    return deco


def answerer(topic_prefix):
    def deco(fn):
        _register_unique(ANSWERERS, topic_prefix, fn, "answerer")
        return fn
    return deco


def validator(name):
    def deco(fn):
        VALIDATORS.append((name, fn))
        return fn
    return deco


def hook(event_name, order=100):
    def deco(fn):
        HOOKS[event_name].append((order, _owner(fn) + "." + fn.__name__, fn))
        HOOKS[event_name].sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


def run_hooks(event_name, *args, **kwargs):
    results = []
    for _, _, fn in HOOKS.get(event_name, []):
        results.append(fn(*args, **kwargs))
    return results


# ----------------------------------------------------------------------
# verification hardening registries (VERIFY_HARDENING 6.5, 8.2, 10.3; WP1)

class MessKind(object):
    """A registered mess kind (6.5). measure(state) -> [(location_key, origin, amount)]: the parcels of the kind in a
    state, a location key being a pair (class, id) with class "floor" (a room id), "entity" (the carrier),
    "drain" (the sink or drain the amount left through) or "outside" (a yard or street zone); origin is the parcel's
    origin id (None: the kind's own). unit, sinks, eps and rho left None take the kernel defaults of
    house_sim/data/verify/mess_classes.json (ledger.mess_kind_spec)."""
    __slots__ = ("name", "unit", "measure", "sinks", "eps", "rho", "owner")

    def __init__(self, name, measure, unit=None, sinks=None, eps=None, rho=None, owner="?"):
        self.name, self.measure, self.unit, self.sinks = name, measure, unit, sinks
        self.eps, self.rho, self.owner = eps, rho, owner

    def __repr__(self):
        return "MessKind({!r}, unit={!r}, owner={!r})".format(self.name, self.unit, self.owner)


def mess_kind(name, unit=None, measure=None, sinks=None, eps=None, rho=None):
    """Register a mess kind (VERIFY_HARDENING 6.5). Either @mess_kind(name, unit=..., sinks=[...], eps=..., rho=...)
    on measure(state), or mess_kind(name, unit, measure, sinks, eps, rho) with the function given. sinks: location
    class names of house_sim/data/verify/location_classes.json that accept the kind (None: the kernel default of the
    kind), or a callable fn(state, location_key) -> bool. eps is an absolute tolerance in the kind's unit; rho the
    relative slack of the unaccounted balance."""
    if not isinstance(name, str) or not name:
        raise ValueError("mess_kind needs a name")
    for label, value in (("eps", eps), ("rho", rho)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise ValueError("mess_kind {}: {} must be a nonnegative number".format(name, label))
    if sinks is not None and not callable(sinks) and not (isinstance(sinks, (list, tuple)) and
                                                          all(isinstance(s, str) for s in sinks)):
        raise ValueError("mess_kind {}: sinks is a list of location classes or a callable".format(name))

    def deco(fn):
        if not callable(fn):
            raise TypeError("mess_kind {}: measure must be callable".format(name))
        _check_arity(fn, 1, "mess_kind")
        kind = MessKind(name, fn, unit, list(sinks) if isinstance(sinks, (list, tuple)) else sinks, eps, rho,
                        _owner(fn))
        if name in MESS_KINDS and MESS_KINDS[name].measure is not fn:
            raise ValueError("mess_kind {!r} registered twice ({} and {})".format(name, MESS_KINDS[name].owner,
                                                                                  _owner(fn)))
        MESS_KINDS[name] = kind
        OWNERS[("mess_kind", name)] = _owner(fn)
        return fn
    return deco(measure) if measure is not None else deco


class DerivedField(object):
    """A registered derived field (8.2): recompute(state, eid) -> the field's value derived from the current state
    (predicates.MISSING when it does not apply to eid). types: entity types (type or is_a) it applies to, or None
    (every entity that carries the field)."""
    __slots__ = ("name", "recompute", "types", "owner")

    def __init__(self, name, recompute, types=None, owner="?"):
        self.name, self.recompute, self.types, self.owner = name, recompute, types, owner

    def applies(self, record):
        if self.types is None:
            return True
        return record.get("type") in self.types or any(t in self.types for t in record.get("is_a") or ())


def derived_field(name, recompute=None, types=None):
    """Register a derived (formerly latched) goal field (VERIFY_HARDENING 8.2): @derived_field(name) on
    recompute(state, eid), or derived_field(name, recompute). Schema-4 evaluation (evaluation.evaluation_view with a
    hardened task) overlays recompute's value on every entity that carries the field, and on every entity a predicate
    names explicitly with that field, wherever a success, failure or temporal predicate reads it."""
    if not isinstance(name, str) or not name or "." in name:
        raise ValueError("derived_field needs a plain field name")
    kinds = tuple(types) if isinstance(types, (list, tuple, set, frozenset)) else ((types,) if types else None)

    def deco(fn):
        if not callable(fn):
            raise TypeError("derived_field {}: recompute must be callable".format(name))
        _check_arity(fn, 2, "derived_field")
        if name in DERIVED_FIELDS and DERIVED_FIELDS[name].recompute is not fn:
            raise ValueError("derived_field {!r} registered twice ({} and {})".format(
                name, DERIVED_FIELDS[name].owner, _owner(fn)))
        DERIVED_FIELDS[name] = DerivedField(name, fn, kinds, _owner(fn))
        OWNERS[("derived_field", name)] = _owner(fn)
        return fn
    return deco(recompute) if recompute is not None else deco


def settings_skill(field, fn=None, types=None):
    """Register how a battery policy sets field f of an entity to another valid value (VERIFY_HARDENING 10.3: B14's
    changed:<e>.<f> probe and the inverses of setting changes): @settings_skill(field, types=[...]) on
    fn(state, eid) -> [(value, plan step)], the plan step a reference-skill step ({"skill": "set", "target": eid,
    "setting": ..., "value": ...}) that sets the field to value. A type-specific registration wins over a generic one
    (types None) for the same field, both over the kernel defaults of settings_steps."""
    if not isinstance(field, str) or not field:
        raise ValueError("settings_skill needs a field name")
    kinds = tuple(types) if isinstance(types, (list, tuple, set, frozenset)) else ((types,) if types else None)

    def deco(f):
        if not callable(f):
            raise TypeError("settings_skill {}: fn must be callable".format(field))
        _check_arity(f, 2, "settings_skill")
        entries = SETTINGS_SKILLS.setdefault(field, [])
        if any(existing is f for _, existing in entries):
            return f
        entries.append((kinds, f))
        entries.sort(key=lambda entry: entry[0] is None)        # type-specific first (stable)
        return f
    return deco(fn) if fn is not None else deco


def _is_kind(record, kinds):
    return record.get("type") in kinds or any(t in kinds for t in record.get("is_a") or ())


def settings_steps(state, eid, field):
    """[(value, plan step)] that set field of eid to another valid value: the first registered settings skill whose
    types match, else the kernel defaults -- on (switchable things: the switch skill), open (openable things: the
    open / close skills), settings.<name> (a value of the entity's setting_options other than the current one: the set
    skill). [] when nothing is known."""
    record = (state.get("entities") or {}).get(eid)
    if record is None:
        return []
    for kinds, fn in SETTINGS_SKILLS.get(field, ()):
        if kinds is None or _is_kind(record, kinds):
            return list(fn(state, eid) or [])
    if field == "on" and record.get("switchable"):
        value = not bool(record.get("on"))
        return [(value, {"skill": "switch", "target": eid, "on": value})]
    if field == "open" and record.get("openable"):
        value = not bool(record.get("open"))
        return [(value, {"skill": "open" if value else "close", "target": eid})]
    if field.startswith("settings."):
        name = field.split(".", 1)[1]
        options = (record.get("setting_options") or {}).get(name)
        current = (record.get("settings") or {}).get(name)
        if isinstance(options, list):
            return [(value, {"skill": "set", "target": eid, "setting": name, "value": value})
                    for value in options if value != current]
    return []


_LOADED = False


def enabled_domains():
    """HOUSE_SIM_DOMAINS="core,cleaning" restricts loading during parallel development."""
    import os
    raw = os.environ.get("HOUSE_SIM_DOMAINS", "").strip()
    if not raw:
        return None
    return {"core"} | {name.strip() for name in raw.split(",") if name.strip()}


def domain_modules():
    """The modules load_domains imports, in order: core first (other domains extend its actions), then the committed
    domains of DOMAINS (restricted by HOUSE_SIM_DOMAINS when set). Never a folder listing: a package that is not in
    DOMAINS -- a git-ignored build/, dist/ or results/ folder, a stray checkout -- is never imported."""
    only = enabled_domains()
    names = [name for name in DOMAINS if only is None or name in only]
    return ["core"] + names


def load_domains():
    """Import core and every committed domain package of house_sim.systems exactly once (registration side
    effects)."""
    global _LOADED
    if _LOADED:
        return
    import importlib
    from .. import systems as package
    for name in domain_modules():
        importlib.import_module(package.__name__ + "." + name)
    _LOADED = True

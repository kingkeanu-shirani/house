"""Registries that domain modules extend. Importing a domain module registers it.

Decorators:
    @action(name, params=..., duration=..., ...)      new agent action (see ActionSpec)
    @before(action_name, order=100)                   guard: fn(ctx) may raise ActionError
    @after(action_name, order=100)                    effect: fn(ctx, result) runs after success
    @system(name, order=100, max_dt=60)              tick: fn(w, dt, tick) each simulated substep
    @furnisher(name, order=100)                       fn(config, rng, opts, reg) during house generation
    @scenario_helper(name)                            fn(ctx, **args) -> dict of new bindings
    @skill(name)                                      reference skill: fn(runner, **args)
    @observer(name, order=100)                        fn(env, w, obs) adds observation channels
    @inspector(name, order=100)                       fn(w, eid, rec, details, ctx) adds inspect fields
    @invariant(name)                                  fn(state, baseline) -> failure label or None
    @answerer(topic_prefix)                           fn(env, w, person_id, topic, ctx) -> answer dict
    @validator(name)                                  fn(config) raises ValueError for bad layouts
    @hook(event_name)                                 generic named hooks (e.g. "cycle_complete:washer")
"""
from collections import defaultdict

ACTIONS = {}
BEFORE = defaultdict(list)
AFTER = defaultdict(list)
SYSTEMS = []
SYSTEM_PERIODS = {}
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


def system(name, order=100, max_dt=60.0, period=None):
    """period: run at most once per `period` simulated seconds with dt = the time accumulated since
    the last run (the system must be correct for large dt, e.g. closed-form decay). Without a period
    the system runs every substep, split into chunks of at most max_dt."""
    def deco(fn):
        if any(s[1] == name for s in SYSTEMS):
            raise ValueError("System registered twice: " + name)
        if period:
            SYSTEM_PERIODS[name] = float(period)
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


_LOADED = False


def enabled_domains():
    """HOUSE_SIM_DOMAINS="core,cleaning" restricts loading during parallel development."""
    import os
    raw = os.environ.get("HOUSE_SIM_DOMAINS", "").strip()
    if not raw:
        return None
    return {"core"} | {name.strip() for name in raw.split(",") if name.strip()}


def load_domains():
    """Import every module in house_sim.systems exactly once (registration side effects)."""
    global _LOADED
    if _LOADED:
        return
    import importlib
    import os
    import pkgutil
    from .. import systems as package
    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
    only = enabled_domains()
    if only is not None:
        names = [n for n in names if n in only]
    # core first: other domains extend its actions.
    for name in ["core"] + [n for n in names if n != "core"]:
        importlib.import_module(package.__name__ + "." + name)
    _LOADED = True

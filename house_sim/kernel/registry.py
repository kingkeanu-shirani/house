"""Object-type registry loaded from house_sim/data/types/*.json.

File format:
    {"domain": "kitchen", "types": {
        "dish":  {"is_a": "portable_object", "props": {...}, "doc": "..."},
        "plate": {"is_a": "dish", "props": {"label": "plate", "+affordances": ["stack"]}}}}

Child props deep-merge into the parent's resolved props. A key written as "+name"
appends to the parent's list instead of replacing it. Every entity record created
from a type carries "type" and "is_a" (the ancestor chain, nearest first).

A file may also declare look-alike groups for realistic perception ("looks_like": {group: {"descriptor", "resolve",
"members", "kind", "candidates", "label_fields", "portion_descriptor", "color", "size_m", "size_class"}}) and
domain-wide "field_tiers". lookalike_groups() / lookalike_types() give the merged groups and the types each covers;
loading warns (LookalikeWarning, self.lookalike_warnings) about groups whose members a camera could tell apart.
"""
import copy
import json
import warnings
from pathlib import Path

TYPE_DIR = Path(__file__).resolve().parent.parent / "data" / "types"
# keys a file-level look-alike group may set (besides "members"); see kernel/realism.py and docs/PERCEPTION.md
GROUP_KEYS = ("descriptor", "resolve", "kind", "candidates", "label_fields", "portion_descriptor", "color", "size_m",
              "size_class")
CAMERA_RESOLUTION_M = 0.05      # sizes closer than this (or 10 %) look the same to the robot's camera


class LookalikeWarning(UserWarning):
    """Members of one look-alike group declare glance values (color, size) a camera would tell apart."""

# Fields every entity has. Domains add their own through type props.
BASE_PROPS = {
    "label": "object", "category": "object", "material": "plastic", "color": None,
    "mass_kg": 0.4, "volume_l": 0.5, "capacity_l": 0.0, "size_m": [0.1, 0.1, 0.1],
    "fragile": False, "temperature_c": 21.0, "wetness": 0.0, "soil": {},
    "owner": None, "home": None, "value": 1.0, "sentimental": False, "freshness": 1.0,
    "hazards": [], "affordances": [], "tags": [], "private": False, "off_limits": False,
    "broken": False, "risk": 0, "water_safe": True, "requires_approval": False,
    "portable": False, "openable": False, "accepts_items": False, "locked": False,
    "open": False, "relation": "on",
}


def _merge(base, extra):
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if key.startswith("+"):
            name = key[1:]
            current = list(result.get(name, []))
            current.extend(v for v in value if v not in current)
            result[name] = current
        elif isinstance(value, dict) and isinstance(result.get(key), dict) and value and key not in ("soil",):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class TypeRegistry:
    def __init__(self, folder=TYPE_DIR, extra_files=()):
        self.types, self.sources, self._cache = {}, {}, {}
        self.perception, self.looks_like, self.field_tiers = {}, {}, []
        self._groups = self._group_types = None
        self.lookalike_warnings = []
        files = sorted(Path(folder).glob("*.json")) if Path(folder).is_dir() else []
        from .plugins import enabled_domains
        only = enabled_domains()
        if only is not None:
            files = [f for f in files if f.stem in only]
        for path in list(files) + [Path(p) for p in extra_files]:
            self.load_file(path)
        self.check()

    def load_file(self, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        types = data.get("types", {})
        if not isinstance(types, dict):
            raise ValueError("types must be an object in " + str(path))
        for name, group in (data.get("looks_like") or {}).items():
            # file-level look-alike groups: {"group": {"descriptor": "...", "resolve": "inspect|read|never", "members": [types]}}
            base = self.looks_like.setdefault(name, {"members": []})
            for key in GROUP_KEYS:
                if key in group:
                    base[key] = group[key]
            for member in group.get("members", []):
                if member not in base["members"]:
                    base["members"].append(member)
        if isinstance(data.get("field_tiers"), dict):
            # domain-wide perception tiers by field name, for fields this domain writes onto any entity
            self.field_tiers.append(data["field_tiers"])
        for name, spec in types.items():
            core = {k: v for k, v in spec.items() if k not in ("perception",)} if isinstance(spec, dict) else spec
            if name in self.types and self.types[name] != core:
                raise ValueError("Type {} defined twice ({} and {})".format(name, self.sources[name], path))
            if not isinstance(spec, dict) or set(spec) - {"is_a", "props", "doc", "perception"}:
                raise ValueError("Type {} must have only is_a/props/doc/perception".format(name))
            self.types[name] = core
            if "perception" in spec:
                # realistic perception: {"glance"|"inspect"|"read"|"hidden": [fields], "measure": {field: tool},
                # "looks_like": "group"}; see kernel/realism.py
                self.perception[name] = spec["perception"]
            self.sources[name] = str(Path(path).name)
        self._cache.clear()
        self._groups = self._group_types = None

    def check(self):
        for name in self.types:
            self.ancestors(name)  # raises on unknown parent or cycle
        try:
            self.lookalike_warnings = self.lookalike_conflicts()
        except (KeyError, TypeError, ValueError):      # a lint must never stop a registry from loading
            self.lookalike_warnings = []
        if self.lookalike_warnings:
            text = "look-alike group members differ at a glance (realistic perception shows the group's common " \
                   "color/size; set the group's \"color\"/\"size_m\" or align the types): " + "; ".join(self.lookalike_warnings[:8]) \
                   + ("; and {} more".format(len(self.lookalike_warnings) - 8) if len(self.lookalike_warnings) > 8 else "")
            warnings.warn(text, LookalikeWarning, stacklevel=3)     # shown once per process by the default filter

    # ----- look-alike groups (realistic perception) ----------------------------------------------------------
    def lookalike_groups(self):
        """Look-alike groups: kernel defaults (realism.DEFAULT_LOOKS_LIKE) merged with the file-level groups (by
        name), in the order realistic perception tries them. Built once per registry; do not mutate."""
        if self._groups is None:
            from .realism import DEFAULT_LOOKS_LIKE
            groups = copy.deepcopy(DEFAULT_LOOKS_LIKE)
            for name, spec in sorted(self.looks_like.items()):
                base = groups.setdefault(name, {"descriptor": spec.get("descriptor", name.replace("_", " ")),
                                                "resolve": spec.get("resolve", "inspect"), "members": []})
                for key in GROUP_KEYS:
                    if key in spec:
                        base[key] = copy.deepcopy(spec[key])
                for member in spec.get("members", []):
                    if member not in base["members"]:
                        base["members"].append(member)
            self._groups = groups
        return self._groups

    def _nearest_looks_like(self, chain):
        for name in chain:
            found = (self.perception.get(name) or {}).get("looks_like")
            if found:
                return found
        return None

    def lookalike_types(self):
        """{group: [types]} -- the registry types realistic perception puts in each look-alike group (same rule as
        Realism.group_of: a looks_like prop, else the nearest perception "looks_like", else the first group whose
        members name the type, an ancestor or its category)."""
        if self._group_types is None:
            groups = self.lookalike_groups()
            order = {name: i for i, name in enumerate(groups)}
            by_member = {}
            for name, spec in groups.items():
                for member in spec.get("members", []):
                    by_member.setdefault(member, []).append(name)
            out = {name: [] for name in groups}
            for type_name in sorted(self.types):
                chain = [type_name] + self.ancestors(type_name)
                props = self.resolve(type_name)
                found = props.get("looks_like") if props.get("looks_like") in groups else None
                if found is None:
                    found = self._nearest_looks_like(chain)
                    found = found if found in groups else None
                if found is None:
                    keys = chain + [props.get("category")]
                    hits = [g for k in keys if isinstance(k, str) for g in by_member.get(k, ())]
                    found = min(hits, key=order.get) if hits else None
                if found is not None:
                    out[found].append(type_name)
            self._group_types = out
        return self._group_types

    def lookalike_conflicts(self):
        """Groups whose member types declare different glance colors, or sizes further apart than the camera
        resolution, without a group "color" / "size_m" to show instead: ["group: color None/'blue'", ...]."""
        out = []
        groups = self.lookalike_groups()
        for name, types in sorted(self.lookalike_types().items()):
            if len(types) < 2:
                continue
            spec = groups.get(name) or {}
            if "color" not in spec:
                colors = sorted({json.dumps(self.resolve(t).get("color")) for t in types})
                if len(colors) > 1:
                    out.append("{}: color {}".format(name, "/".join(colors)))
            if "size_m" not in spec:
                sizes = []
                for t in types:
                    try:
                        sizes.append([float(v) for v in (self.resolve(t).get("size_m") or [])[:3]])
                    except (TypeError, ValueError):
                        continue
                sizes = [s for s in sizes if len(s) == 3]
                for dim in range(3):
                    column = sorted(s[dim] for s in sizes)
                    if column and column[-1] - column[0] > max(CAMERA_RESOLUTION_M, 0.1 * column[(len(column) - 1) // 2]):
                        out.append("{}: size {:.2f}-{:.2f} m".format(name, column[0], column[-1]))
                        break
        return out

    def known(self, name):
        return name in self.types

    def ancestors(self, name):
        chain, seen, current = [], {name}, self.types.get(name, {}).get("is_a")
        if name not in self.types:
            raise KeyError("Unknown object type: " + str(name))
        while current:
            if current in seen:
                raise ValueError("Type cycle at " + current)
            if current not in self.types:
                raise ValueError("Type {} has unknown parent {}".format(name, current))
            chain.append(current)
            seen.add(current)
            current = self.types[current].get("is_a")
        return chain

    def is_a(self, name, ancestor):
        return name == ancestor or (name in self.types and ancestor in self.ancestors(name))

    def resolve(self, name):
        if name not in self._cache:
            parent = self.types[name].get("is_a")
            base = self.resolve(parent) if parent else copy.deepcopy(BASE_PROPS)
            props = _merge(base, self.types[name].get("props", {}))
            props.pop("type", None)
            props.pop("is_a", None)
            self._cache[name] = props
        return self._cache[name]

    def make(self, name, location, **overrides):
        record = copy.deepcopy(self.resolve(name))
        record = _merge(record, overrides) if overrides else record
        record["type"] = name
        record["is_a"] = self.ancestors(name)
        record["location"] = location
        if "label" not in overrides and record.get("label") in (None, "object"):
            record["label"] = name.replace("_", " ")
        return record

    def names(self, ancestor=None):
        return sorted(n for n in self.types if ancestor is None or self.is_a(n, ancestor))


_DEFAULT = None


def default_registry():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = TypeRegistry()
    return _DEFAULT


def reset_default_registry():
    global _DEFAULT
    _DEFAULT = None

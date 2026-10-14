"""Tier-1 physics layer (K-PHYS, kernel v3.0; docs/design/TIER1_PHYSICS.md).

One module per concern, all plain functions over states and Worlds:

    props     Phys record: material classes, liquids, floors, support ratings, per-instance variation
    lawbook   law registry and the active / deferred states of data/physics/laws.json
    forces    robot capacities, lift plans, pushing, friction, floor scratching
    contain   containment fit (box, diagonal, opening, nesting, compression) and the small-parts cylinder

Later packages add support, statics, impact (support graph and consequences), liquids, thermal, environment,
processes (fluids, heat, weather and timed processes) and settle, events, observe (integration). Importing this
package registers nothing with the kernel plugin tables; kernel/physics.py wires the layer into the engine.
"""

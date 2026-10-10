"""Food domain: ingredients and groceries, processing (cut/peel/parts/eggs/mixing/dough), measuring and
weighing, transfers and portions, plating and serving, storage zones, cold chain and time-out,
spoilage and dates, contamination and allergens, eaters' constraints, leftovers.

See docs/domains/food.md. Modules register on import:
    data        reference tables (real magnitudes)
    model       food access, split/merge, contamination, zones, dates/status, eaters, dishes
    mixing      mixture state machine (homogeneity, aeration, gluten, emulsion)
    systems     time systems (food thermal with latent heat + core lag, safety clocks, spoilage)
    actions_*   the food verbs
    hooks       reactions to core and other domains' actions
    perception  inspector, smell observer, resident answers, global invariants
    furnish     kitchen stocking
    scenarios   scenario helpers food.*
    skills      reference skills food.*
"""
from . import data, model, mixing, systems, common  # noqa: F401
from . import actions_knife, actions_mix, actions_measure  # noqa: F401
from . import hooks, perception, furnish, scenarios, skills  # noqa: F401

consume = hooks.consume

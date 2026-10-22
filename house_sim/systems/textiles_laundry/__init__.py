"""Textiles & laundry domain (house_sim/systems/textiles_laundry).

Owns garments, shoes, towels, bedding, table/bath linens, washer/dryer, laundry products, hangers,
drying racks/lines, iron/steamer, lint and pilling tools, sewing kit and hampers, and the verbs
fold, unfold, hang_on_hanger, take_off_hanger, pair, iron, steam, sew_button, repair_seam,
thread_needle, trim_thread, lint_roll, depill, polish_shoes, lace_shoes, make_bed, strip_bed,
dress_bed, tuck, fluff, turn_inside_out, roll_sleeves, unroll_sleeves, treat_stain, soak,
hang_dry, roll_garment.

Modules: model (fabrics, care labels, stains, damage), wash (washer cycles and outcomes),
dry (dryer, air drying and the per-tick fabric system), fold (folding/pairing/hangers and
placement physics), bedding (beds), care (iron, steam, stains, soak, lint, pilling), sewing,
shoes, senses (observers, inspectors, answerers, invariants), furnish (house generation),
helpers (scenario setup helpers) and skills (reference skills). See docs/domains/textiles_laundry.md.
"""
from . import model, physical, signals, views, wash, dry, fold, bedding, care, sewing, shoes, senses, routes, events, furnish, helpers, skills  # noqa: F401

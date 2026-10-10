"""Cleaning physics tables and pure functions (no world mutation here).

Fidelity: P-approx lumped models. Soil is a level 0..1 per kind on a face / floor cell
(1.0 = heavily soiled; < 0.05 counts as clean). Each pass of a cleaning action removes a
fraction of each soil kind that depends on
  * mechanical action delivered (tool, force) vs. the soil's adhesion,
  * moisture (sticky/greasy soils are smeared, not lifted, by a dry tool),
  * chemistry present (product classes on the face, on the tool, in the wash water)
    scaled by how long it has dwelt, vs. the classes that dissolve that soil.
Real magnitudes are cited where they matter (label dwell times, dilutions, film thickness,
evaporation rates, clearances, dB levels).
"""
import math

CLEAN = 0.05          # a kind below this level counts as clean (CONVENTIONS.md)

# ----------------------------------------------------------------------
# soil kinds
#   cls   loose | film | adhered | stain | mineral | bio
#   mech  mechanical agitation needed (0..1) for a full-strength pass
#   wet   needs moisture: a dry tool smears/leaves it
#   chem  dissolving chemistry classes -> power (0..1)
#   base  fraction of chemistry effect available with water/mechanics alone (0..1)
#   dwell seconds for the chemistry to reach full effect
#   g_m2  grams per m2 at level 1.0 (loose kinds; used for debris mass and bins)
#   smear kind produced when smeared by a dry or dirty tool
SOILS = {
    "dust": {"cls": "loose", "mech": 0.1, "g_m2": 4.0},
    "crumbs": {"cls": "loose", "mech": 0.15, "g_m2": 60.0},
    "hair": {"cls": "loose", "mech": 0.2, "g_m2": 3.0},
    "pet_hair": {"cls": "loose", "mech": 0.35, "g_m2": 6.0},
    "lint": {"cls": "loose", "mech": 0.15, "g_m2": 2.0},
    "sand": {"cls": "loose", "mech": 0.2, "g_m2": 180.0},
    "dirt": {"cls": "loose", "mech": 0.25, "g_m2": 90.0, "chem": {"surfactant": 0.7}, "base": 0.7, "dwell": 30},
    "leaves": {"cls": "loose", "mech": 0.2, "g_m2": 120.0},
    "litter": {"cls": "loose", "mech": 0.15, "g_m2": 40.0},
    "ash": {"cls": "loose", "mech": 0.2, "g_m2": 50.0, "smear": "streaks"},
    "pollen": {"cls": "loose", "mech": 0.2, "g_m2": 2.0, "smear": "streaks"},
    "cobweb": {"cls": "loose", "mech": 0.1, "g_m2": 0.5, "smear": "streaks"},
    "sawdust": {"cls": "loose", "mech": 0.15, "g_m2": 60.0},
    "drywall_dust": {"cls": "loose", "mech": 0.2, "g_m2": 30.0, "fine": True, "smear": "streaks"},
    "glitter": {"cls": "loose", "mech": 0.4, "g_m2": 5.0},
    "cat_litter": {"cls": "loose", "mech": 0.15, "g_m2": 250.0},
    "salt": {"cls": "loose", "mech": 0.2, "g_m2": 40.0, "chem": {"acid": 1.0, "surfactant": 0.3}, "base": 0.6, "dwell": 30, "wet": True},
    # films and adhered soils
    "fingerprints": {"cls": "film", "mech": 0.2, "chem": {"solvent_light": 1.0, "surfactant": 0.9, "ammonia": 0.9}, "base": 0.35, "dwell": 0, "smear": "streaks"},
    "streaks": {"cls": "film", "mech": 0.15, "chem": {"solvent_light": 1.0, "surfactant": 0.5, "ammonia": 0.8}, "base": 0.55, "dwell": 0},
    "grease": {"cls": "film", "mech": 0.3, "wet": True, "chem": {"surfactant": 0.9, "alkali": 1.0, "alkali_strong": 1.0, "solvent": 0.8, "solvent_light": 0.5}, "base": 0.2, "dwell": 60, "smear": "streaks"},
    "food": {"cls": "adhered", "mech": 0.45, "wet": True, "chem": {"surfactant": 0.8, "enzyme": 0.6, "alkali": 0.6}, "base": 0.5, "dwell": 120, "cold_sets": False},
    "protein": {"cls": "adhered", "mech": 0.45, "wet": True, "chem": {"enzyme": 1.0, "surfactant": 0.7, "alkali": 0.6}, "base": 0.5, "dwell": 300, "heat_sets": True},
    "burnt": {"cls": "adhered", "mech": 0.85, "wet": True, "chem": {"alkali_strong": 1.0, "alkali": 0.55, "abrasive_mild": 0.45, "surfactant": 0.2}, "base": 0.12, "dwell": 1800},
    "sticky": {"cls": "adhered", "mech": 0.3, "wet": True, "chem": {"surfactant": 0.9, "solvent_light": 0.7, "acid": 0.5}, "base": 0.55, "dwell": 30, "smear": "streaks"},
    "mud": {"cls": "adhered", "mech": 0.35, "wet": True, "chem": {"surfactant": 0.6}, "base": 0.65, "dwell": 60, "smear": "streaks"},
    "soap_scum": {"cls": "mineral", "mech": 0.5, "wet": True, "chem": {"acid": 1.0, "alkali": 0.5, "surfactant": 0.3}, "base": 0.08, "dwell": 300},
    "limescale": {"cls": "mineral", "mech": 0.6, "wet": True, "chem": {"acid": 1.0}, "base": 0.04, "dwell": 900},
    "water_spots": {"cls": "mineral", "mech": 0.2, "chem": {"acid": 1.0, "solvent_light": 0.4}, "base": 0.3, "dwell": 60},
    "rust": {"cls": "mineral", "mech": 0.7, "wet": True, "chem": {"acid": 0.9}, "base": 0.1, "dwell": 600},
    "mildew": {"cls": "bio", "mech": 0.5, "wet": True, "chem": {"chlorine": 1.0, "oxidizer": 0.9, "quat": 0.5}, "base": 0.12, "dwell": 600},
    "mold": {"cls": "bio", "mech": 0.55, "wet": True, "chem": {"chlorine": 1.0, "oxidizer": 0.85, "quat": 0.4}, "base": 0.08, "dwell": 900},
    "algae": {"cls": "bio", "mech": 0.5, "wet": True, "chem": {"oxidizer": 1.0, "chlorine": 1.0}, "base": 0.15, "dwell": 600},
    "urine": {"cls": "bio", "mech": 0.3, "wet": True, "chem": {"enzyme": 1.0, "oxidizer": 0.6, "acid": 0.5, "quat": 0.4}, "base": 0.3, "dwell": 600},
    "biohazard": {"cls": "bio", "mech": 0.35, "wet": True, "chem": {"enzyme": 1.0, "surfactant": 0.6}, "base": 0.35, "dwell": 300},
    "garbage_juice": {"cls": "bio", "mech": 0.35, "wet": True, "chem": {"surfactant": 0.8, "enzyme": 0.7}, "base": 0.4, "dwell": 60},
    "toothpaste": {"cls": "adhered", "mech": 0.25, "wet": True, "chem": {"surfactant": 0.6}, "base": 0.85, "dwell": 30},
    "hairspray": {"cls": "film", "mech": 0.3, "chem": {"solvent_light": 1.0, "ammonia": 0.7}, "base": 0.3, "dwell": 30, "smear": "streaks"},
    "sunscreen": {"cls": "film", "mech": 0.3, "wet": True, "chem": {"surfactant": 0.9, "solvent_light": 0.8}, "base": 0.25, "dwell": 60, "smear": "streaks"},
    "bird_droppings": {"cls": "adhered", "mech": 0.35, "wet": True, "chem": {"surfactant": 0.7, "enzyme": 0.8}, "base": 0.5, "dwell": 120, "smear": "streaks"},
    "sap": {"cls": "adhered", "mech": 0.5, "chem": {"solvent_light": 1.0, "solvent": 1.0}, "base": 0.05, "dwell": 60},
    "wax": {"cls": "adhered", "mech": 0.6, "chem": {"solvent": 0.9}, "base": 0.2, "dwell": 60},
    "glue": {"cls": "adhered", "mech": 0.6, "wet": True, "chem": {"solvent_light": 0.6, "surfactant": 0.4}, "base": 0.35, "dwell": 120},
    "paint_acrylic": {"cls": "adhered", "mech": 0.3, "wet": True, "chem": {"surfactant": 0.8, "solvent_light": 0.6}, "base": 0.85, "dwell": 0},
    "paint_oil": {"cls": "adhered", "mech": 0.3, "chem": {"solvent": 1.0}, "base": 0.03, "dwell": 0, "smear": "streaks"},
    "paint_dried": {"cls": "adhered", "mech": 0.85, "chem": {"solvent_light": 0.5, "solvent": 0.7}, "base": 0.05, "dwell": 300},
    "scuff": {"cls": "film", "mech": 0.45, "chem": {"abrasive_mild": 0.9, "solvent_light": 0.5}, "base": 0.25, "dwell": 0},
    "crayon": {"cls": "film", "mech": 0.45, "chem": {"abrasive_mild": 0.8, "solvent_light": 0.8}, "base": 0.15, "dwell": 0},
    "gum": {"cls": "adhered", "mech": 0.8, "chem": {"solvent": 0.6}, "base": 0.3, "dwell": 60},
    "stain_wine": {"cls": "stain", "mech": 0.25, "wet": True, "chem": {"oxidizer": 0.9, "surfactant": 0.45, "enzyme": 0.3}, "base": 0.25, "dwell": 300},
    "stain_coffee": {"cls": "stain", "mech": 0.25, "wet": True, "chem": {"oxidizer": 0.9, "surfactant": 0.5, "abrasive_mild": 0.6}, "base": 0.25, "dwell": 300},
    "stain_blood": {"cls": "stain", "mech": 0.25, "wet": True, "chem": {"enzyme": 1.0, "oxidizer": 0.8, "surfactant": 0.4}, "base": 0.3, "dwell": 300, "heat_sets": True},
    "stain_oil": {"cls": "stain", "mech": 0.3, "chem": {"solvent": 1.0, "alkali": 0.7, "surfactant": 0.6, "absorbent": 0.8}, "base": 0.1, "dwell": 300},
    "stain_ink": {"cls": "stain", "mech": 0.3, "chem": {"solvent_light": 1.0}, "base": 0.05, "dwell": 60},
    "stain_grass": {"cls": "stain", "mech": 0.3, "wet": True, "chem": {"enzyme": 0.8, "solvent_light": 0.6}, "base": 0.1, "dwell": 300},
    "stain_food": {"cls": "stain", "mech": 0.3, "wet": True, "chem": {"enzyme": 0.8, "surfactant": 0.7, "oxidizer": 0.6}, "base": 0.3, "dwell": 300},
    "stain_juice": {"cls": "stain", "mech": 0.25, "wet": True, "chem": {"oxidizer": 0.8, "surfactant": 0.6}, "base": 0.35, "dwell": 300},
}
LOOSE = sorted(k for k, v in SOILS.items() if v["cls"] == "loose" and k not in ("salt",))
FLOOR_LOOSE = sorted(set(LOOSE) | {"salt"})
STAINS = sorted(k for k, v in SOILS.items() if v["cls"] == "stain")
ORGANIC_DEBRIS = ("leaves", "dirt", "sand", "pollen", "sawdust")   # may be swept into landscaping outdoors


def soil_info(kind):
    return SOILS.get(kind) or {"cls": "film", "mech": 0.3, "chem": {"surfactant": 0.6}, "base": 0.4, "dwell": 60}


# ----------------------------------------------------------------------
# products (chemistry). Classes: surfactant, alkali, alkali_strong, acid, chlorine, oxidizer,
# ammonia, solvent, solvent_light (alcohol), enzyme, quat, abrasive_mild, polish, conditioner,
# absorbent. Dwell/contact times follow typical US label claims; dilutions per label.
#   kill_s     label wet-contact time for disinfection (None = not a registered disinfectant)
#   wet_s      how long a sprayed film stays wet indoors (evaporation) before it dries
#   rinse      leaves residue that must be rinsed off food-contact / pet surfaces
#   fumes      0..1 irritant fumes per 100 ml used in a closed room
PRODUCTS = {
    "all_purpose": {"label": "All-Purpose Cleaner. Spray, wipe. Safe on sealed surfaces. Not for natural stone, wood floors or screens.",
                    "classes": {"surfactant": 0.8, "alkali": 0.4}, "wet_s": 300, "rinse": False, "fumes": 0.02},
    "dish_soap": {"label": "Dish Soap. Add 1-2 ml per litre of warm water or a drop to a sponge. Rinse dishes.",
                  "classes": {"surfactant": 1.0}, "wet_s": 900, "rinse": True, "fumes": 0.0, "concentrate": True, "dose_ml_per_l": 1.5},
    "degreaser": {"label": "Kitchen Degreaser. Spray, let stand 1-2 minutes, wipe, rinse food-contact surfaces. Not for aluminum.",
                  "classes": {"surfactant": 1.0, "alkali": 1.0}, "wet_s": 300, "rinse": True, "fumes": 0.05},
    "glass_cleaner": {"label": "Glass Cleaner with Ammonia-D. Spray on glass and mirrors, wipe dry. Do not use on screens or wood finishes.",
                      "classes": {"solvent_light": 0.8, "ammonia": 0.7, "surfactant": 0.4}, "wet_s": 60, "rinse": False, "fumes": 0.08, "streaky": True},
    "glass_cleaner_ammonia_free": {"label": "Streak-Free Glass Cleaner (ammonia-free). Safe on tinted glass. Spray on cloth for screens? No - use screen cleaner.",
                                   "classes": {"solvent_light": 0.8, "surfactant": 0.4}, "wet_s": 60, "rinse": False, "fumes": 0.02, "streaky": True},
    "screen_cleaner": {"label": "Screen Cleaner (water-based, alcohol- and ammonia-free). Spray onto microfiber cloth, never directly on screen. Safe for anti-glare coatings.",
                       "classes": {"surfactant": 0.7}, "wet_s": 40, "rinse": False, "fumes": 0.0, "screen_safe": True},
    "isopropyl_alcohol": {"label": "70% Isopropyl Alcohol. Disinfects hard non-porous surfaces in 30 s. Flammable. Not for coated screens.",
                          "classes": {"solvent_light": 1.0}, "kill_s": 30, "wet_s": 45, "rinse": False, "fumes": 0.05, "flammable": True},
    "disinfectant": {"label": "Disinfectant Spray (quaternary ammonium). Pre-clean heavily soiled surfaces. Keep surface visibly wet 10 minutes to disinfect. Rinse food-contact surfaces with potable water.",
                     "classes": {"quat": 1.0, "surfactant": 0.5}, "kill_s": 600, "wet_s": 720, "rinse": True, "fumes": 0.03},
    "disinfecting_wipes": {"label": "Disinfecting Wipes. Use enough wipes for surface to remain visibly wet for 4 minutes. Rinse food-contact surfaces.",
                           "classes": {"quat": 1.0, "surfactant": 0.4}, "kill_s": 240, "wet_s": 300, "rinse": True, "fumes": 0.0},
    "bleach": {"label": "Regular Bleach 6% sodium hypochlorite. Disinfect: 20 ml per litre of water, leave wet 5 minutes, rinse food-contact surfaces. NEVER mix with ammonia, vinegar or acids. Not for wood, metal, wool, coloured fabrics.",
               "classes": {"chlorine": 1.0, "oxidizer": 1.0, "alkali": 0.3}, "kill_s": 300, "wet_s": 600, "rinse": True, "fumes": 0.25, "concentrate": True, "dose_ml_per_l": 20},
    "hydrogen_peroxide": {"label": "3% Hydrogen Peroxide. Disinfects in 10 minutes. Safe around children once dry. Lifts fresh blood with cold water.",
                          "classes": {"oxidizer": 0.6}, "kill_s": 600, "wet_s": 700, "rinse": False, "fumes": 0.0},
    "oxygen_bleach": {"label": "Oxygen Bleach (sodium percarbonate). Dissolve 30 g per litre warm water. Grout, decks, outdoor mildew, colourfast stains. No chlorine.",
                      "classes": {"oxidizer": 0.8, "alkali": 0.5}, "wet_s": 900, "rinse": True, "fumes": 0.0, "concentrate": True, "dose_ml_per_l": 30},
    "vinegar": {"label": "Cleaning Vinegar 6% acetic acid. Dilute 1:1 for glass and limescale. Do not use on marble, granite, travertine, grout or wood floors. Not a disinfectant.",
                "classes": {"acid": 0.6}, "wet_s": 900, "rinse": True, "fumes": 0.02},
    "citric_descaler": {"label": "Citric Acid Descaler. Dissolve 25 g per litre, fill to cover scale (never above MAX), heat or soak 15-30 min, then rinse 3 times. Not for natural stone.",
                        "classes": {"acid": 0.9}, "wet_s": 1800, "rinse": True, "fumes": 0.0, "concentrate": True, "dose_ml_per_l": 25},
    "toilet_bowl_cleaner": {"label": "Toilet Bowl Cleaner (hydrochloric acid 9.5%). Apply under rim, wait 10 minutes, brush, flush. DANGER: never mix with bleach.",
                            "classes": {"acid": 1.0}, "wet_s": 1800, "rinse": True, "fumes": 0.15},
    "cream_cleanser": {"label": "Cream Cleanser (mild abrasive). Porcelain, enamel, stainless sinks. Not for acrylic, glass cooktops or screens.",
                       "classes": {"surfactant": 0.5, "abrasive_mild": 0.6, "alkali": 0.3}, "wet_s": 900, "rinse": True, "fumes": 0.0, "abrasion": 0.35},
    "baking_soda": {"label": "Baking Soda. Paste with water; let sit up to overnight on burnt-on soil; deodorizes. Gentle abrasive.",
                    "classes": {"alkali": 0.35, "abrasive_mild": 0.35}, "wet_s": 28800, "rinse": True, "fumes": 0.0, "abrasion": 0.2},
    "oven_cleaner": {"label": "Oven Cleaner (sodium hydroxide). Cold oven only. Wear gloves, ventilate. Let stand 20 min - overnight. Rinse thoroughly. Keep off heating elements, gaskets, aluminum, self-cleaning enamel.",
                     "classes": {"alkali_strong": 1.0, "alkali": 1.0}, "wet_s": 43200, "rinse": True, "fumes": 0.3, "gloves": True},
    "enzymatic_cleaner": {"label": "Enzyme Stain & Odor Remover. Saturate urine, vomit or organic stains, blot, let dwell 10 min, blot dry. Pet safe.",
                          "classes": {"enzyme": 1.0}, "wet_s": 1200, "rinse": False, "fumes": 0.0, "pet_safe": True},
    "wood_polish": {"label": "Furniture Polish. Finished wood only. Spray onto cloth, buff with the grain. Not for floors, handrails, laminate, fabric or leather.",
                    "classes": {"polish": 1.0, "solvent_light": 0.3}, "wet_s": 120, "rinse": False, "fumes": 0.02, "oily": True},
    "leather_conditioner": {"label": "Leather Conditioner. Finished/protected leather only; test on hidden area; thin coat, buff. Not for suede, nubuck or faux leather.",
                            "classes": {"conditioner": 1.0}, "wet_s": 600, "rinse": False, "fumes": 0.0, "oily": True},
    "cooking_oil": {"label": "Vegetable oil. Thin coat on dry cast iron/carbon steel; mineral oil for boards.",
                    "classes": {"conditioner": 0.8}, "wet_s": 3600, "rinse": False, "fumes": 0.0, "oily": True},
    "stone_cleaner": {"label": "pH-Neutral Stone Cleaner. Safe on marble, granite, travertine, sealed stone. Spray, wipe, buff dry.",
                      "classes": {"surfactant": 0.75}, "wet_s": 300, "rinse": False, "fumes": 0.0, "neutral": True},
    "wood_floor_cleaner": {"label": "Hardwood & Laminate Floor Cleaner. Use with barely damp microfiber mop. Never wet-mop or steam.",
                           "classes": {"surfactant": 0.7}, "wet_s": 300, "rinse": False, "fumes": 0.0, "neutral": True},
    "floor_cleaner": {"label": "Multi-Surface Floor Cleaner (tile, vinyl, sealed concrete). 30 ml per 4 litres warm water.",
                      "classes": {"surfactant": 0.85, "alkali": 0.3}, "wet_s": 600, "rinse": False, "fumes": 0.0, "concentrate": True, "dose_ml_per_l": 7.5},
    "upholstery_cleaner": {"label": "Upholstery Cleaner (water-based). For fabric code W or W/S only. Test hidden area, blot - do not rub or soak.",
                           "classes": {"surfactant": 0.8, "enzyme": 0.3}, "wet_s": 1200, "rinse": False, "fumes": 0.0, "water_based": True},
    "upholstery_solvent": {"label": "Dry-Cleaning Solvent Spot Remover. For fabric code S or W/S. Ventilate. Blot from outside in.",
                           "classes": {"solvent": 0.8, "solvent_light": 0.5}, "wet_s": 120, "rinse": False, "fumes": 0.15, "water_based": False},
    "mineral_spirits": {"label": "Odorless Mineral Spirits. Oil paint and brush cleaning. Flammable; ventilate; never pour down drains - let settle, reuse, dispose as hazardous waste.",
                        "classes": {"solvent": 1.0}, "wet_s": 900, "rinse": False, "fumes": 0.2, "flammable": True, "hazardous_waste": True},
    "stainless_polish": {"label": "Stainless Steel Cleaner & Polish. Spray onto cloth, wipe with the grain.",
                         "classes": {"polish": 0.6, "solvent_light": 0.6}, "wet_s": 120, "rinse": False, "fumes": 0.02, "oily": True},
    "metal_polish": {"label": "Metal Polish (brass, chrome). Mild abrasive. Not for lacquered, plated, matte or oil-rubbed finishes.",
                     "classes": {"polish": 0.7, "abrasive_mild": 0.5, "ammonia": 0.3}, "wet_s": 300, "rinse": False, "fumes": 0.05, "abrasion": 0.3},
    "mildew_remover": {"label": "Mold & Mildew Remover (contains bleach). Ventilate, wear gloves, leave 10 min, rinse. Never mix with ammonia or acids.",
                       "classes": {"chlorine": 1.0, "oxidizer": 1.0, "alkali": 0.3}, "kill_s": 600, "wet_s": 900, "rinse": True, "fumes": 0.3, "gloves": True},
    "grout_cleaner": {"label": "Grout Cleaner (alkaline, oxygen). Apply to grout lines, wait 5 min, scrub with grout brush, rinse. Acid-free.",
                      "classes": {"oxidizer": 0.7, "alkali": 0.6, "surfactant": 0.5}, "wet_s": 900, "rinse": True, "fumes": 0.02},
    "deck_cleaner": {"label": "Deck & Siding Cleaner (oxygen bleach). Wood and composite. Wet plants before and after. No chlorine.",
                     "classes": {"oxidizer": 0.8, "alkali": 0.5, "surfactant": 0.4}, "wet_s": 900, "rinse": True, "fumes": 0.0},
    "patio_cleaner": {"label": "Patio & Paver Cleaner (alkaline, acid-free). Safe on limestone and concrete. Rinse toward lawn, not drains.",
                      "classes": {"surfactant": 0.7, "alkali": 0.6, "oxidizer": 0.4}, "wet_s": 900, "rinse": True, "fumes": 0.0},
    "grill_cleaner": {"label": "Grill Cleaner. Cool grill only. Spray, wait 5 min, scrub, rinse.",
                      "classes": {"alkali": 0.9, "surfactant": 0.7}, "wet_s": 600, "rinse": True, "fumes": 0.05},
    "ammonia": {"label": "Household Ammonia. Dilute 120 ml per 4 litres. Ventilate. NEVER mix with bleach.",
                "classes": {"ammonia": 1.0, "alkali": 0.6}, "wet_s": 120, "rinse": True, "fumes": 0.35, "concentrate": True, "dose_ml_per_l": 30},
    "bird_safe_cleaner": {"label": "Bird-Safe Cage Cleaner (enzyme, fragrance-free). Spray on cloth; no fumes.",
                          "classes": {"enzyme": 0.9, "surfactant": 0.4}, "wet_s": 600, "rinse": False, "fumes": 0.0, "pet_safe": True},
    "oil_absorbent": {"label": "Oil Absorbent Granules. Spread over oil/antifreeze, wait 15 min, sweep up, bag, dispose as hazardous waste.",
                      "classes": {"absorbent": 1.0}, "wet_s": 0, "rinse": False, "fumes": 0.0, "granular": True},
    "water": {"label": "Water.", "classes": {}, "wet_s": 600, "rinse": False, "fumes": 0.0},
    "distilled_water": {"label": "Distilled water.", "classes": {}, "wet_s": 300, "rinse": False, "fumes": 0.0, "screen_safe": True},
}
DISINFECTANTS = sorted(k for k, v in PRODUCTS.items() if v.get("kill_s"))

# Chemicals that must never meet (toxic gas or corrosive vapour). Order-free pairs of classes.
INCOMPATIBLE = [
    ("chlorine", "acid", "chlorine gas"),
    ("chlorine", "ammonia", "chloramine vapour"),
    ("chlorine", "solvent_light", "chloroform vapour"),
    ("oxidizer_peroxide", "acid", "peracetic acid vapour"),
]


def product_classes(product):
    info = PRODUCTS.get(product, {})
    classes = dict(info.get("classes", {}))
    if product == "hydrogen_peroxide":
        classes["oxidizer_peroxide"] = 1.0
    return classes


def incompatibility(classes_a, classes_b, min_amount=0.02):
    """Return the hazard name if two chemical class mixtures react, else None."""
    for a, b, name in INCOMPATIBLE:
        if (classes_a.get(a, 0) > min_amount and classes_b.get(b, 0) > min_amount) or \
           (classes_a.get(b, 0) > min_amount and classes_b.get(a, 0) > min_amount):
            return name
    return None


def self_incompatibility(classes, min_amount=0.02):
    for a, b, name in INCOMPATIBLE:
        if classes.get(a, 0) > min_amount and classes.get(b, 0) > min_amount:
            return name
    return None


# ----------------------------------------------------------------------
# surface materials / finishes. Keys are matched against record["finish"], then
# record["surface_material"], then record["material"].
#   abrasion  max tool abrasiveness (0..1) before visible scratches/burnishing
#   water     max free-water film (mm) tolerated for ~10 min before marks/swelling
#   bad       chemistry classes that damage it -> (damage kind, severity per application)
#   porous    liquids soak in / stains set; blot rather than rub
#   polish    accepts wood/furniture polish
#   steam     tolerates a steam mop
#   heat_c    surface temperature limit before heat marks
MATERIALS = {
    "marble": {"abrasion": 0.3, "water": 1.0, "bad": {"acid": ("etched", 0.6)}, "porous": True, "slippery_wet": True},
    "travertine": {"abrasion": 0.3, "water": 1.0, "bad": {"acid": ("etched", 0.6)}, "porous": True, "slippery_wet": True},
    "limestone": {"abrasion": 0.35, "water": 1.0, "bad": {"acid": ("etched", 0.6)}, "porous": True, "slippery_wet": True},
    "natural_stone": {"abrasion": 0.35, "water": 1.0, "bad": {"acid": ("etched", 0.5)}, "porous": True, "slippery_wet": True},
    "stone": {"abrasion": 0.6, "water": 1.0, "bad": {"acid": ("sealer_dulled", 0.25)}, "porous": True, "slippery_wet": True},
    "granite": {"abrasion": 0.65, "water": 1.0, "bad": {"acid": ("sealer_dulled", 0.25)}, "porous": False},
    "slate": {"abrasion": 0.5, "water": 1.0, "bad": {"acid": ("sealer_dulled", 0.2)}, "porous": True, "slippery_wet": True},
    "quartz": {"abrasion": 0.55, "water": 1.0, "bad": {"alkali_strong": ("discolored", 0.4)}, "porous": False},
    "laminate": {"abrasion": 0.25, "water": 0.02, "bad": {"solvent": ("finish_damage", 0.3)}, "polish_film": True, "slippery_wet": True},
    "laminate_counter": {"abrasion": 0.25, "water": 0.5, "bad": {"solvent": ("finish_damage", 0.3)}},
    "butcher_block": {"abrasion": 0.35, "water": 0.05, "bad": {"chlorine": ("discolored", 0.4), "ammonia": ("finish_damage", 0.3)}, "porous": True, "polish": False},
    "sealed_wood": {"abrasion": 0.2, "water": 0.04, "bad": {"ammonia": ("finish_damage", 0.4), "chlorine": ("discolored", 0.5), "acid": ("finish_damage", 0.2), "alkali_strong": ("finish_damage", 0.6), "solvent": ("finish_damage", 0.3)}, "polish": True, "steam": False, "slippery_wet": True},
    "wood": {"abrasion": 0.2, "water": 0.04, "bad": {"ammonia": ("finish_damage", 0.4), "chlorine": ("discolored", 0.5), "alkali_strong": ("finish_damage", 0.6), "solvent": ("finish_damage", 0.3)}, "polish": True, "steam": False, "slippery_wet": True},
    "hardwood": {"abrasion": 0.2, "water": 0.04, "bad": {"ammonia": ("finish_damage", 0.4), "chlorine": ("discolored", 0.5), "acid": ("finish_damage", 0.25), "alkali_strong": ("finish_damage", 0.6)}, "polish": False, "steam": False, "slippery_wet": True},
    "unsealed_wood": {"abrasion": 0.15, "water": 0.01, "bad": {"ammonia": ("finish_damage", 0.4), "chlorine": ("discolored", 0.6), "acid": ("finish_damage", 0.3), "alkali": ("discolored", 0.3)}, "porous": True, "polish": False, "steam": False},
    "oiled_wood": {"abrasion": 0.15, "water": 0.015, "bad": {"ammonia": ("finish_damage", 0.4), "chlorine": ("discolored", 0.6)}, "porous": True, "polish": False, "steam": False},
    "antique_wood": {"abrasion": 0.1, "water": 0.01, "bad": {"ammonia": ("finish_damage", 0.5), "chlorine": ("discolored", 0.6), "solvent": ("finish_damage", 0.5)}, "polish": False, "steam": False},
    "cork": {"abrasion": 0.2, "water": 0.02, "bad": {"chlorine": ("discolored", 0.4)}, "porous": True, "steam": False},
    "teak": {"abrasion": 0.4, "water": 1.0, "bad": {"chlorine": ("graying", 0.5)}, "porous": True},
    "cedar": {"abrasion": 0.3, "water": 1.0, "bad": {"chlorine": ("graying", 0.5)}, "porous": True},
    "wood_deck": {"abrasion": 0.45, "water": 1.0, "bad": {"chlorine": ("graying", 0.5)}, "porous": True, "slippery_wet": True},
    "painted_wood": {"abrasion": 0.25, "water": 0.5, "bad": {"chlorine": ("discolored", 0.4), "solvent": ("paint_stripped", 0.6)}, "slippery_wet": True},
    "composite_deck": {"abrasion": 0.3, "water": 1.0, "bad": {"solvent": ("finish_damage", 0.3)}, "slippery_wet": True},
    "painted_flat": {"abrasion": 0.12, "water": 0.3, "bad": {"solvent": ("paint_stripped", 0.6), "ammonia": ("burnished", 0.2)}, "burnish": True},
    "painted_satin": {"abrasion": 0.3, "water": 0.6, "bad": {"solvent": ("paint_stripped", 0.5)}},
    "painted_gloss": {"abrasion": 0.2, "water": 1.0, "bad": {"solvent": ("paint_stripped", 0.5)}, "burnish": True},
    "painted": {"abrasion": 0.25, "water": 0.6, "bad": {"solvent": ("paint_stripped", 0.5)}},
    "drywall": {"abrasion": 0.12, "water": 0.3, "bad": {"solvent": ("paint_stripped", 0.6)}, "burnish": True},
    "wallpaper": {"abrasion": 0.1, "water": 0.05, "bad": {"solvent": ("finish_damage", 0.5), "chlorine": ("discolored", 0.5)}},
    "mdf": {"abrasion": 0.2, "water": 0.05, "bad": {"solvent": ("paint_stripped", 0.5)}, "swells": True},
    "stainless": {"abrasion": 0.4, "water": 1.0, "bad": {"chlorine": ("pitted", 0.35)}, "grain": True},
    "stainless_steel": {"abrasion": 0.4, "water": 1.0, "bad": {"chlorine": ("pitted", 0.35)}, "grain": True},
    "chrome": {"abrasion": 0.25, "water": 1.0, "bad": {}, "acid_limit_s": 7200},
    "nickel": {"abrasion": 0.2, "water": 1.0, "bad": {}, "acid_limit_s": 1800},
    "matte_black": {"abrasion": 0.08, "water": 1.0, "bad": {"polish": ("blotched", 0.4), "abrasive_mild": ("scratched", 0.4)}, "acid_limit_s": 600},
    "brushed_nickel": {"abrasion": 0.15, "water": 1.0, "bad": {"polish": ("blotched", 0.3)}, "acid_limit_s": 900},
    "oil_rubbed_bronze": {"abrasion": 0.08, "water": 1.0, "bad": {"polish": ("finish_stripped", 0.5), "abrasive_mild": ("finish_stripped", 0.4)}, "acid_limit_s": 300},
    "brass": {"abrasion": 0.2, "water": 1.0, "bad": {}, "acid_limit_s": 900},
    "lacquered_brass": {"abrasion": 0.1, "water": 1.0, "bad": {"polish": ("lacquer_stripped", 0.6), "abrasive_mild": ("lacquer_stripped", 0.5), "ammonia": ("lacquer_stripped", 0.3)}, "acid_limit_s": 600},
    "gold_pvd": {"abrasion": 0.08, "water": 1.0, "bad": {"abrasive_mild": ("scratched", 0.4)}, "acid_limit_s": 600},
    "copper": {"abrasion": 0.25, "water": 1.0, "bad": {"chlorine": ("discolored", 0.4)}},
    "aluminum": {"abrasion": 0.45, "water": 1.0, "bad": {"alkali_strong": ("pitted", 0.6), "chlorine": ("pitted", 0.3)}},
    "burnished_aluminum": {"abrasion": 0.4, "water": 1.0, "bad": {"alkali_strong": ("pitted", 0.6), "alkali": ("oxidized", 0.35)}},
    "cast_iron": {"abrasion": 0.95, "water": 1.0, "bad": {"alkali_strong": ("seasoning_stripped", 0.4)}, "rusts": True, "thermal_shock_c": 150},
    "carbon_steel": {"abrasion": 0.9, "water": 1.0, "bad": {}, "rusts": True},
    "enameled_cast_iron": {"abrasion": 0.3, "water": 1.0, "bad": {"acid": ("dulled", 0.15)}},
    "enamel": {"abrasion": 0.3, "water": 1.0, "bad": {"acid": ("dulled", 0.15)}},
    "self_clean_enamel": {"abrasion": 0.25, "water": 1.0, "bad": {"alkali_strong": ("enamel_damaged", 0.7)}},
    "porcelain": {"abrasion": 0.6, "water": 1.0, "bad": {}, "slippery_wet": True},
    "porcelain_coated": {"abrasion": 0.35, "water": 1.0, "bad": {}},
    "ceramic": {"abrasion": 0.55, "water": 1.0, "bad": {}},
    "ceramic_tile": {"abrasion": 0.7, "water": 1.0, "bad": {}, "grout": True, "slippery_wet": True},
    "tile": {"abrasion": 0.7, "water": 1.0, "bad": {"acid": ("grout_eroded", 0.15)}, "grout": True, "slippery_wet": True},
    "grout": {"abrasion": 0.75, "water": 1.0, "bad": {"acid": ("grout_eroded", 0.4), "chlorine": ("discolored", 0.25)}, "porous": True},
    "glass_tile": {"abrasion": 0.25, "water": 1.0, "bad": {}, "slippery_wet": True},
    "acrylic": {"abrasion": 0.12, "water": 1.0, "bad": {"solvent": ("crazed", 0.6)}},
    "fiberglass": {"abrasion": 0.15, "water": 1.0, "bad": {"solvent": ("crazed", 0.5)}},
    "cultured_marble": {"abrasion": 0.15, "water": 1.0, "bad": {"acid": ("dulled", 0.3), "solvent": ("crazed", 0.4)}},
    "composite_sink": {"abrasion": 0.3, "water": 1.0, "bad": {"alkali_strong": ("discolored", 0.4)}},
    "glass": {"abrasion": 0.55, "water": 1.0, "bad": {}, "glossy": True, "lint_shows": True, "thermal_shock_c": 45},
    "tempered_glass": {"abrasion": 0.55, "water": 1.0, "bad": {}, "glossy": True, "lint_shows": True, "thermal_shock_c": 120},
    "coated_glass": {"abrasion": 0.15, "water": 1.0, "bad": {"abrasive_mild": ("coating_scratched", 0.5)}, "glossy": True, "lint_shows": True},
    "mirror": {"abrasion": 0.5, "water": 0.3, "bad": {"ammonia": ("desilvering", 0.05)}, "glossy": True, "lint_shows": True, "edge_sensitive": True},
    "glass_cooktop": {"abrasion": 0.25, "water": 1.0, "bad": {}, "glossy": True},
    "tinted_glass": {"abrasion": 0.3, "water": 1.0, "bad": {"ammonia": ("tint_damaged", 0.5)}, "glossy": True, "lint_shows": True},
    "heating_element": {"abrasion": 0.3, "water": 0.0, "bad": {"alkali_strong": ("element_corroded", 0.6)}},
    "door_gasket": {"abrasion": 0.2, "water": 1.0, "bad": {"alkali_strong": ("gasket_degraded", 0.6), "chlorine": ("gasket_degraded", 0.4)}},
    "screen_matte": {"abrasion": 0.03, "water": 0.02, "bad": {"ammonia": ("coating_stripped", 0.7), "solvent_light": ("coating_stripped", 0.5), "solvent": ("coating_stripped", 0.8), "alkali": ("coating_stripped", 0.4), "chlorine": ("coating_stripped", 0.7)}, "glossy": True, "screen": True, "pressure": True},
    "screen_oled": {"abrasion": 0.03, "water": 0.02, "bad": {"ammonia": ("coating_stripped", 0.7), "solvent_light": ("coating_stripped", 0.4), "solvent": ("coating_stripped", 0.8), "chlorine": ("coating_stripped", 0.7)}, "glossy": True, "screen": True, "pressure": True},
    "screen_glass": {"abrasion": 0.2, "water": 0.02, "bad": {"ammonia": ("oleophobic_worn", 0.2), "solvent": ("coating_stripped", 0.5)}, "glossy": True, "screen": True},
    "screen_eink": {"abrasion": 0.03, "water": 0.01, "bad": {"solvent_light": ("coating_stripped", 0.5), "ammonia": ("coating_stripped", 0.6)}, "screen": True, "pressure": True},
    "plastic": {"abrasion": 0.2, "water": 1.0, "bad": {"solvent": ("crazed", 0.4)}},
    "glossy_plastic": {"abrasion": 0.06, "water": 0.5, "bad": {"solvent": ("crazed", 0.5)}, "glossy": True},
    "vinyl": {"abrasion": 0.25, "water": 1.0, "bad": {"solvent": ("crazed", 0.4), "chlorine": ("cracked", 0.3)}, "slippery_wet": True},
    "rubber": {"abrasion": 0.6, "water": 1.0, "bad": {"chlorine": ("rubber_degraded", 0.4), "solvent": ("rubber_degraded", 0.5)}},
    "silicone": {"abrasion": 0.5, "water": 1.0, "bad": {}},
    "nonstick": {"abrasion": 0.18, "water": 1.0, "bad": {"alkali_strong": ("coating_damaged", 0.7)}},
    "concrete": {"abrasion": 1.0, "water": 1.0, "bad": {"acid": ("etched", 0.2)}, "porous": True},
    "asphalt": {"abrasion": 0.9, "water": 1.0, "bad": {"solvent": ("binder_dissolved", 0.6)}, "porous": True},
    "brick": {"abrasion": 0.9, "water": 1.0, "bad": {}, "porous": True},
    "pavers": {"abrasion": 0.8, "water": 1.0, "bad": {}, "porous": True, "joint_sand": True},
    "carpet": {"abrasion": 0.5, "water": 0.0, "bad": {"chlorine": ("bleached", 0.7)}, "porous": True, "textile": True},
    "wool_carpet": {"abrasion": 0.4, "water": 0.0, "bad": {"chlorine": ("bleached", 0.7), "oxidizer": ("fiber_damage", 0.5), "alkali": ("fiber_damage", 0.3)}, "porous": True, "textile": True},
    "wool": {"abrasion": 0.3, "water": 0.0, "bad": {"chlorine": ("bleached", 0.7), "oxidizer": ("fiber_damage", 0.4)}, "porous": True, "textile": True},
    "fabric": {"abrasion": 0.12, "water": 0.0, "bad": {"chlorine": ("bleached", 0.6)}, "porous": True, "textile": True},
    "velvet": {"abrasion": 0.05, "water": 0.0, "bad": {"chlorine": ("bleached", 0.6)}, "porous": True, "textile": True, "delicate": True},
    "silk": {"abrasion": 0.03, "water": 0.0, "bad": {"chlorine": ("bleached", 0.8), "oxidizer": ("fiber_damage", 0.5)}, "porous": True, "textile": True, "delicate": True},
    "microfiber_fabric": {"abrasion": 0.12, "water": 0.0, "bad": {"chlorine": ("bleached", 0.6)}, "porous": True, "textile": True},
    "leather": {"abrasion": 0.06, "water": 0.05, "bad": {"chlorine": ("leather_damaged", 0.6), "ammonia": ("leather_damaged", 0.5), "solvent_light": ("leather_dried", 0.3), "solvent": ("leather_damaged", 0.5), "polish": ("stained", 0.3)}, "porous": True},
    "suede": {"abrasion": 0.08, "water": 0.0, "bad": {"conditioner": ("dark_stain", 0.6), "chlorine": ("leather_damaged", 0.6), "polish": ("stained", 0.5)}, "porous": True, "textile": True},
    "faux_leather": {"abrasion": 0.08, "water": 0.3, "bad": {"solvent": ("cracked", 0.4), "solvent_light": ("cracked", 0.2), "chlorine": ("cracked", 0.3)}},
    "wicker": {"abrasion": 0.2, "water": 0.1, "bad": {"chlorine": ("discolored", 0.4)}, "porous": True},
    "powder_coated_metal": {"abrasion": 0.25, "water": 1.0, "bad": {"solvent": ("finish_damage", 0.4)}},
    "metal": {"abrasion": 0.4, "water": 1.0, "bad": {}},
    "steel": {"abrasion": 0.5, "water": 1.0, "bad": {}, "rusts": True},
    "resin": {"abrasion": 0.25, "water": 1.0, "bad": {"solvent": ("crazed", 0.4)}},
    "paper": {"abrasion": 0.1, "water": 0.0, "bad": {}, "porous": True},
    "cardboard": {"abrasion": 0.2, "water": 0.0, "bad": {}, "porous": True},
    "gilded": {"abrasion": 0.01, "water": 0.0, "bad": {"ammonia": ("gilding_damaged", 0.6), "solvent_light": ("gilding_damaged", 0.4)}},
    "silver": {"abrasion": 0.1, "water": 1.0, "bad": {"chlorine": ("tarnished", 0.6)}},
    "silver_plate": {"abrasion": 0.08, "water": 1.0, "bad": {"chlorine": ("tarnished", 0.6), "abrasive_mild": ("plating_worn", 0.4)}},
    "crystal": {"abrasion": 0.3, "water": 1.0, "bad": {}, "glossy": True, "thermal_shock_c": 35},
    "china": {"abrasion": 0.35, "water": 1.0, "bad": {}, "thermal_shock_c": 60},
    "gold_rim_china": {"abrasion": 0.08, "water": 1.0, "bad": {"abrasive_mild": ("gilding_damaged", 0.5)}, "thermal_shock_c": 60},
    "melamine": {"abrasion": 0.2, "water": 1.0, "bad": {}},
    "eps_foam": {"abrasion": 0.2, "water": 1.0, "bad": {"solvent": ("foam_degraded", 0.8), "chlorine": ("foam_degraded", 0.4)}},
    "coir": {"abrasion": 0.8, "water": 0.0, "bad": {"chlorine": ("fiber_damage", 0.5)}, "porous": True, "textile": True},
    "polypropylene": {"abrasion": 0.5, "water": 1.0, "bad": {}},
    "terracotta": {"abrasion": 0.6, "water": 1.0, "bad": {}, "porous": True},
    "electronics": {"abrasion": 0.1, "water": 0.0, "bad": {"solvent": ("crazed", 0.4)}},
    "default": {"abrasion": 0.3, "water": 0.5, "bad": {}},
}
# grout-line / acid exposure of tiled surfaces is handled through "grout": True

FLOOR_FINISH = {"hardwood": "sealed_wood", "wood": "sealed_wood", "plywood": "unsealed_wood", "carpet": "carpet",
                "tile": "tile", "laminate": "laminate", "stone": "natural_stone", "concrete": "concrete",
                "rubber": "rubber", "grass": "grass", "soil": "soil", "vinyl": "vinyl", "cork": "cork"}
HARD_FLOORS = {"tile", "laminate", "sealed_wood", "hardwood", "unsealed_wood", "oiled_wood", "natural_stone", "marble",
               "travertine", "limestone", "slate", "concrete", "rubber", "vinyl", "cork", "wood", "stone", "pavers",
               "brick", "asphalt", "wood_deck", "composite_deck", "painted_wood", "granite", "ceramic_tile", "porcelain"}
TERRAIN = {"grass", "soil", "gravel", "mulch"}


# a floor finish belongs to one material family: a scene that turns a hardwood floor into carpet (material set,
# finish left over from the furnisher) has a carpet, not a sealed-wood carpet
FINISH_FAMILY = {"sealed_wood": "wood", "oiled_wood": "wood", "unsealed_wood": "wood", "painted_wood": "wood", "wood_deck": "wood",
                 "marble": "stone", "travertine": "stone", "limestone": "stone", "slate": "stone", "natural_stone": "stone",
                 "granite": "stone", "carpet": "carpet", "wool_carpet": "carpet", "tile": "tile", "ceramic_tile": "tile",
                 "porcelain": "tile", "laminate": "laminate", "vinyl": "vinyl", "cork": "cork", "concrete": "concrete"}
MATERIAL_FAMILY = {"hardwood": "wood", "wood": "wood", "plywood": "wood", "bamboo": "wood", "stone": "stone", "marble": "stone",
                   "granite": "stone", "slate": "stone", "carpet": "carpet", "wool": "carpet", "tile": "tile", "ceramic": "tile",
                   "porcelain": "tile", "laminate": "laminate", "vinyl": "vinyl", "cork": "cork", "concrete": "concrete"}


def material_key(record):
    finish = record.get("finish")
    if finish and record.get("category") == "floor":
        fam, mat = FINISH_FAMILY.get(finish), MATERIAL_FAMILY.get(record.get("material"))
        if fam and mat and fam != mat:
            finish = None
    for value in (finish, record.get("surface_material"), record.get("material")):
        if value and value in MATERIALS:
            return value
    category = record.get("category")
    if category == "floor":
        value = FLOOR_FINISH.get(record.get("material"))
        if value in MATERIALS:
            return value
    material = record.get("material")
    if material in FLOOR_FINISH and FLOOR_FINISH[material] in MATERIALS:
        return FLOOR_FINISH[material]
    return "default"


def material(record):
    return MATERIALS.get(material_key(record), MATERIALS["default"])


# ----------------------------------------------------------------------
# tools
#   mech      mechanical agitation delivered (0..1)
#   abrasion  how abrasive the contact face is (0..1) vs MATERIALS[*]["abrasion"]
#   capture   fraction of loose dust/crumbs captured rather than flicked/redistributed
#   absorb_l  liquid it can hold before dripping
#   lint      lint shed onto glossy surfaces (0..1)
#   head_m    head thickness for reaching under furniture; reach_m how far under it reaches
TOOLS = {
    "microfiber_cloth": {"mech": 0.38, "abrasion": 0.02, "capture": 0.92, "absorb_l": 0.15, "lint": 0.0, "kind": "cloth"},
    "cotton_rag": {"mech": 0.38, "abrasion": 0.05, "capture": 0.7, "absorb_l": 0.25, "lint": 0.35, "kind": "cloth"},
    "dish_towel": {"mech": 0.3, "abrasion": 0.04, "capture": 0.6, "absorb_l": 0.3, "lint": 0.3, "kind": "towel"},
    "towel": {"mech": 0.3, "abrasion": 0.04, "capture": 0.6, "absorb_l": 0.6, "lint": 0.4, "kind": "towel"},
    "paper_towel": {"mech": 0.3, "abrasion": 0.15, "capture": 0.6, "absorb_l": 0.05, "lint": 0.5, "kind": "paper", "disposable": True},
    "disinfecting_wipe": {"mech": 0.33, "abrasion": 0.03, "capture": 0.6, "absorb_l": 0.02, "lint": 0.05, "kind": "wipe", "disposable": True},
    "chamois": {"mech": 0.3, "abrasion": 0.01, "capture": 0.7, "absorb_l": 0.3, "lint": 0.0, "kind": "cloth"},
    "sponge": {"mech": 0.45, "abrasion": 0.05, "capture": 0.5, "absorb_l": 0.15, "lint": 0.0, "kind": "sponge", "scrub_side": {"mech": 0.72, "abrasion": 0.55}},
    "non_scratch_sponge": {"mech": 0.62, "abrasion": 0.12, "capture": 0.5, "absorb_l": 0.12, "lint": 0.0, "kind": "sponge"},
    "scouring_pad": {"mech": 0.8, "abrasion": 0.6, "capture": 0.3, "absorb_l": 0.03, "lint": 0.0, "kind": "pad"},
    "steel_wool": {"mech": 0.95, "abrasion": 0.9, "capture": 0.2, "absorb_l": 0.02, "lint": 0.0, "kind": "pad", "metal": True},
    "melamine_sponge": {"mech": 0.78, "abrasion": 0.45, "capture": 0.4, "absorb_l": 0.08, "lint": 0.0, "kind": "sponge"},
    "pumice_stone": {"mech": 0.9, "abrasion": 0.7, "abrasion_wet": 0.35, "capture": 0.1, "absorb_l": 0.0, "lint": 0.0, "kind": "pad"},
    "scrub_brush": {"mech": 0.72, "abrasion": 0.3, "capture": 0.2, "absorb_l": 0.02, "lint": 0.0, "kind": "brush"},
    "grout_brush": {"mech": 0.8, "abrasion": 0.35, "capture": 0.1, "absorb_l": 0.01, "lint": 0.0, "kind": "brush", "narrow": True},
    "toilet_brush": {"mech": 0.72, "abrasion": 0.3, "capture": 0.1, "absorb_l": 0.05, "lint": 0.0, "kind": "brush", "zone": "toilet"},
    "bottle_brush": {"mech": 0.66, "abrasion": 0.15, "capture": 0.1, "absorb_l": 0.02, "lint": 0.0, "kind": "brush", "interior": True},
    "dish_brush": {"mech": 0.66, "abrasion": 0.18, "capture": 0.2, "absorb_l": 0.02, "lint": 0.0, "kind": "brush"},
    "detail_brush": {"mech": 0.56, "abrasion": 0.12, "capture": 0.3, "absorb_l": 0.01, "lint": 0.0, "kind": "brush", "narrow": True},
    "wire_brush": {"mech": 0.95, "abrasion": 0.85, "capture": 0.1, "absorb_l": 0.0, "lint": 0.0, "kind": "brush", "metal": True},
    "nylon_grill_brush": {"mech": 0.8, "abrasion": 0.3, "capture": 0.1, "absorb_l": 0.0, "lint": 0.0, "kind": "brush"},
    "soft_brush": {"mech": 0.3, "abrasion": 0.02, "capture": 0.75, "absorb_l": 0.0, "lint": 0.0, "kind": "brush"},
    "deck_brush": {"mech": 0.78, "abrasion": 0.35, "capture": 0.1, "absorb_l": 0.02, "lint": 0.0, "kind": "brush"},
    "feather_duster": {"mech": 0.08, "abrasion": 0.01, "capture": 0.35, "absorb_l": 0.0, "lint": 0.0, "kind": "duster"},
    "microfiber_duster": {"mech": 0.15, "abrasion": 0.02, "capture": 0.85, "absorb_l": 0.0, "lint": 0.0, "kind": "duster"},
    "extendable_duster": {"mech": 0.15, "abrasion": 0.02, "capture": 0.8, "absorb_l": 0.0, "lint": 0.0, "kind": "duster", "reach_extension_m": 1.6},
    "flexible_duster": {"mech": 0.15, "abrasion": 0.02, "capture": 0.8, "absorb_l": 0.0, "lint": 0.0, "kind": "duster", "head_m": 0.02, "reach_m": 0.7},
    "lambswool_duster": {"mech": 0.12, "abrasion": 0.02, "capture": 0.75, "absorb_l": 0.0, "lint": 0.0, "kind": "duster"},
    "compressed_air": {"mech": 0.2, "abrasion": 0.0, "capture": 0.0, "absorb_l": 0.0, "lint": 0.0, "kind": "air"},
    "pillowcase": {"mech": 0.2, "abrasion": 0.02, "capture": 0.98, "absorb_l": 0.3, "lint": 0.2, "kind": "cloth"},
    "lint_roller": {"mech": 0.2, "abrasion": 0.0, "capture": 0.95, "absorb_l": 0.0, "lint": 0.0, "kind": "roller"},
    "plastic_scraper": {"mech": 0.85, "abrasion": 0.12, "capture": 0.0, "absorb_l": 0.0, "lint": 0.0, "kind": "scraper"},
    "razor_scraper": {"mech": 0.95, "abrasion": 0.1, "abrasion_steep": 0.9, "capture": 0.0, "absorb_l": 0.0, "lint": 0.0, "kind": "scraper", "metal": True},
    "squeegee": {"mech": 0.2, "abrasion": 0.05, "capture": 0.0, "absorb_l": 0.0, "lint": 0.0, "kind": "squeegee"},
    "rug_beater": {"mech": 0.6, "abrasion": 0.1, "capture": 0.0, "absorb_l": 0.0, "lint": 0.0, "kind": "beater"},
    "broom": {"mech": 0.3, "abrasion": 0.1, "capture": 0.0, "head_m": 0.06, "reach_m": 0.5, "transport": 0.9, "edge": 0.65, "kind": "broom"},
    "angled_broom": {"mech": 0.3, "abrasion": 0.1, "capture": 0.0, "head_m": 0.06, "reach_m": 0.55, "transport": 0.9, "edge": 0.95, "kind": "broom"},
    "push_broom": {"mech": 0.35, "abrasion": 0.15, "capture": 0.0, "head_m": 0.09, "reach_m": 0.4, "transport": 0.88, "edge": 0.55, "kind": "broom", "outdoor": True},
    "hand_brush": {"mech": 0.3, "abrasion": 0.08, "capture": 0.0, "head_m": 0.05, "reach_m": 0.3, "transport": 0.85, "edge": 0.9, "kind": "broom"},
    "dust_mop": {"mech": 0.2, "abrasion": 0.02, "capture": 0.88, "head_m": 0.04, "reach_m": 0.9, "kind": "mop", "dry": True},
    "flat_mop": {"mech": 0.5, "abrasion": 0.03, "capture": 0.6, "head_m": 0.05, "reach_m": 0.9, "absorb_l": 0.12, "kind": "mop"},
    "string_mop": {"mech": 0.45, "abrasion": 0.05, "capture": 0.4, "head_m": 0.1, "reach_m": 0.5, "absorb_l": 0.6, "kind": "mop"},
    "steam_mop": {"mech": 0.55, "abrasion": 0.03, "capture": 0.5, "head_m": 0.09, "reach_m": 0.4, "absorb_l": 0.1, "kind": "mop", "steam": True},
    "litter_picker": {"mech": 0.0, "abrasion": 0.0, "capture": 0.0, "kind": "grabber"},
}

# Vacuum attachment heads: suction coupling to soils and geometry.
VACUUM_HEADS = {
    "floor_head": {"head_m": 0.12, "reach_m": 0.35, "edge": 0.5, "crevice": 0.1, "fabric": 0.4, "hard": 0.9, "carpet": 1.0},
    "low_profile_head": {"head_m": 0.05, "reach_m": 0.9, "edge": 0.5, "crevice": 0.1, "fabric": 0.3, "hard": 0.9, "carpet": 0.75},
    "crevice_tool": {"head_m": 0.03, "reach_m": 0.8, "edge": 1.0, "crevice": 1.0, "fabric": 0.6, "hard": 0.6, "carpet": 0.3},
    "dust_brush": {"head_m": 0.05, "reach_m": 0.5, "edge": 0.85, "crevice": 0.5, "fabric": 0.7, "hard": 0.7, "carpet": 0.3, "gentle": True},
    "upholstery_tool": {"head_m": 0.05, "reach_m": 0.4, "edge": 0.6, "crevice": 0.4, "fabric": 1.0, "hard": 0.6, "carpet": 0.5, "gentle": True},
    "pet_tool": {"head_m": 0.06, "reach_m": 0.4, "edge": 0.6, "crevice": 0.3, "fabric": 1.0, "hard": 0.7, "carpet": 0.8, "pet_hair": True},
    "handheld_nozzle": {"head_m": 0.06, "reach_m": 0.3, "edge": 0.7, "crevice": 0.6, "fabric": 0.8, "hard": 0.8, "carpet": 0.5},
}

FORCE = {"light": 0.7, "normal": 1.0, "hard": 1.3}
WETNESS_LEVEL = {"dry": 0.0, "barely_damp": 0.2, "damp": 0.4, "wet": 0.85}
# mop water left on the floor (mm film) by wetness setting; damp mopping ~30-40 g/m2.
MOP_FILM_MM = {"dry": 0.0, "barely_damp": 0.012, "damp": 0.035, "wet": 0.18}
# evaporation of a thin indoor water film ~0.1-0.3 kg/m2/h  ->  mm per second
EVAP_MM_S = 0.2 / 3600.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def rnd(x, n=4):
    return round(float(x), n)


def chem_power(kind, sources, softened=0.0):
    """sources: iterable of (class, power, elapsed_s) -> chemistry factor 0..1 for a soil kind.

    softened (0..1, from soaking or steaming) raises the water-only baseline.
    """
    info = soil_info(kind)
    needs = info.get("chem")
    if not needs:
        return 1.0
    base = clamp(info.get("base", 0.3) + (1.0 - info.get("base", 0.3)) * 0.6 * softened)
    dwell = float(info.get("dwell", 60))
    best = 0.0
    for cls, power, elapsed in sources:
        if cls in needs:
            frac = 1.0 if dwell <= 0 else clamp(float(elapsed) / dwell)
            best = max(best, needs[cls] * min(1.0, power) * frac)
    return clamp(base + (1.0 - base) * best)


def removal_fraction(kind, mech, wetness, chem_factor, action="wipe", softened=0.0):
    """Fraction (0..0.97) of a soil kind removed by one pass."""
    info = soil_info(kind)
    need = max(0.05, float(info.get("mech", 0.3)) * (1.0 - 0.6 * clamp(softened)))
    m = clamp(mech / need)
    w = 1.0
    if info.get("wet") and wetness < 0.12:
        w = 0.3
    frac = 0.92 * m * w * chem_factor
    if action == "rinse":
        frac *= 0.5
    elif action == "wash":
        frac *= 1.15          # running water carries loosened soil away
    return clamp(frac, 0.0, 0.97)


def weekday_index(name):
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return days.index(name) if name in days else None


def evaporation_mm_s(room_temp_c=21.0, humidity=0.45, airflow=False, outdoor=False, wind_mps=0.0, sun=False):
    """Approximate free-water evaporation rate (mm/s) for floors and surfaces."""
    rate = EVAP_MM_S * (1.0 + 0.04 * (room_temp_c - 21.0)) * clamp(1.6 - 1.4 * humidity, 0.2, 1.6)
    if airflow:
        rate *= 2.5
    if outdoor:
        rate *= 1.5 + 0.3 * min(wind_mps, 8.0)
        if sun:
            rate *= 3.0
    return max(rate, 1e-6)


def grams(kind, level, area_m2):
    return float(soil_info(kind).get("g_m2", 20.0)) * float(level) * float(area_m2)


def level_from_grams(kind, grams_, area_m2):
    per = float(soil_info(kind).get("g_m2", 20.0)) * max(area_m2, 1e-6)
    return clamp(grams_ / per)


def log_kill(contamination_level, log_reduction):
    return contamination_level * math.pow(10.0, -log_reduction)

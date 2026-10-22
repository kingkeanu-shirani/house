"""Food-domain reference tables (real-world magnitudes; sources: USDA FSIS / FDA Food Code,
USDA FoodKeeper storage times, King Arthur ingredient weight chart, ASHRAE food
thermal properties, AAP choking guidance, IDDSI texture levels).

Per-kind static properties live on the object types (house_sim/data/types/food.json,
field ``food``). These tables hold category-level physics and rules that mechanics
need for any kind, including kinds an author spawns ad hoc.
"""

# ----------------------------------------------------------------------
# physical properties by food category
# cp: J/(kg K) above freezing, water: mass fraction, tf: initial freezing point (C),
# q10: spoilage-rate multiplier per +10 C, life: days to spoil at t_ref (C) when
# stored properly (unopened/whole), mold/odor/slime: visible/olfactory spoilage cues.

CATS = {
    "vegetable":    {"cp": 3900, "water": 0.90, "tf": -0.8, "q10": 2.5, "life": 14, "t_ref": 4, "mold": True, "wilt": False},
    "leafy_green":  {"cp": 4000, "water": 0.95, "tf": -0.3, "q10": 2.8, "life": 7, "t_ref": 4, "mold": False, "wilt": True},
    "herb":         {"cp": 3900, "water": 0.88, "tf": -0.5, "q10": 2.8, "life": 7, "t_ref": 4, "mold": False, "wilt": True},
    "root_veg":     {"cp": 3600, "water": 0.79, "tf": -0.8, "q10": 2.0, "life": 45, "t_ref": 10, "mold": True},
    "fruit":        {"cp": 3700, "water": 0.85, "tf": -1.5, "q10": 2.5, "life": 21, "t_ref": 4, "mold": True},
    "berry":        {"cp": 3800, "water": 0.90, "tf": -0.9, "q10": 3.0, "life": 5, "t_ref": 4, "mold": True},
    "poultry":      {"cp": 3500, "water": 0.70, "tf": -2.2, "q10": 3.0, "life": 2, "t_ref": 4, "odor": True, "slime": True, "raw_animal": True, "safe_c": 74},
    "meat":         {"cp": 3400, "water": 0.65, "tf": -1.7, "q10": 3.0, "life": 4, "t_ref": 4, "odor": True, "slime": True, "raw_animal": True, "safe_c": 63},
    "ground_meat":  {"cp": 3400, "water": 0.62, "tf": -1.7, "q10": 3.0, "life": 2, "t_ref": 4, "odor": True, "slime": True, "raw_animal": True, "safe_c": 71},
    "fish":         {"cp": 3600, "water": 0.75, "tf": -2.2, "q10": 3.2, "life": 1.5, "t_ref": 4, "odor": True, "slime": True, "raw_animal": True, "safe_c": 63},
    "shellfish":    {"cp": 3600, "water": 0.80, "tf": -2.2, "q10": 3.2, "life": 2, "t_ref": 4, "odor": True, "raw_animal": True, "safe_c": 63},
    "deli":         {"cp": 3400, "water": 0.65, "tf": -1.7, "q10": 2.8, "life": 5, "t_ref": 4, "odor": True, "slime": True},
    "egg":          {"cp": 3200, "water": 0.74, "tf": -0.5, "q10": 2.2, "life": 35, "t_ref": 4, "odor": True, "safe_c": 71},
    "dairy":        {"cp": 3900, "water": 0.88, "tf": -0.5, "q10": 3.0, "life": 10, "t_ref": 4, "odor": True},
    "cheese_hard":  {"cp": 2600, "water": 0.35, "tf": -10.0, "q10": 2.0, "life": 120, "t_ref": 4, "mold": True},
    "cheese_soft":  {"cp": 3200, "water": 0.55, "tf": -1.5, "q10": 2.5, "life": 14, "t_ref": 4, "mold": True, "odor": True},
    "butter":       {"cp": 2100, "water": 0.16, "tf": -1.0, "q10": 2.0, "life": 60, "t_ref": 4},
    "bread":        {"cp": 2800, "water": 0.36, "tf": -5.0, "q10": 2.0, "life": 5, "t_ref": 21, "mold": True},
    "grain":        {"cp": 1800, "water": 0.12, "tf": -20.0, "q10": 1.6, "life": 540, "t_ref": 21},
    "flour":        {"cp": 1800, "water": 0.12, "tf": -20.0, "q10": 1.6, "life": 270, "t_ref": 21},
    "sugar":        {"cp": 1250, "water": 0.0, "tf": -40.0, "q10": 1.0, "life": 3650, "t_ref": 21},
    "salt":         {"cp": 880, "water": 0.0, "tf": -40.0, "q10": 1.0, "life": 36500, "t_ref": 21},
    "leavening":    {"cp": 1100, "water": 0.0, "tf": -40.0, "q10": 1.3, "life": 540, "t_ref": 21},
    "spice":        {"cp": 1500, "water": 0.08, "tf": -40.0, "q10": 1.3, "life": 1095, "t_ref": 21},
    "oil":          {"cp": 2000, "water": 0.0, "tf": -10.0, "q10": 1.8, "life": 540, "t_ref": 21},
    "condiment":    {"cp": 3000, "water": 0.6, "tf": -3.0, "q10": 2.0, "life": 365, "t_ref": 21, "mold": True},
    "sauce":        {"cp": 3600, "water": 0.8, "tf": -1.5, "q10": 2.5, "life": 365, "t_ref": 21, "mold": True},
    "canned":       {"cp": 3700, "water": 0.8, "tf": -1.5, "q10": 1.4, "life": 900, "t_ref": 21},
    "snack":        {"cp": 1700, "water": 0.03, "tf": -40.0, "q10": 1.6, "life": 180, "t_ref": 21},
    "cereal":       {"cp": 1700, "water": 0.03, "tf": -40.0, "q10": 1.6, "life": 270, "t_ref": 21},
    "nut":          {"cp": 1900, "water": 0.04, "tf": -40.0, "q10": 2.0, "life": 180, "t_ref": 21},
    "beverage":     {"cp": 4000, "water": 0.9, "tf": -1.0, "q10": 1.8, "life": 270, "t_ref": 21},
    "alcohol":      {"cp": 3600, "water": 0.85, "tf": -7.0, "q10": 1.2, "life": 1800, "t_ref": 15},
    "frozen_dessert": {"cp": 3100, "water": 0.62, "tf": -2.5, "q10": 2.5, "life": 10, "t_ref": 4, "frozen_food": True},
    "frozen_veg":   {"cp": 3900, "water": 0.9, "tf": -0.8, "q10": 2.5, "life": 7, "t_ref": 4, "frozen_food": True},
    "frozen_meal":  {"cp": 3500, "water": 0.7, "tf": -2.0, "q10": 2.8, "life": 3, "t_ref": 4, "frozen_food": True},
    "prepared":     {"cp": 3600, "water": 0.75, "tf": -1.5, "q10": 3.0, "life": 4, "t_ref": 4, "mold": True, "odor": True},
    "mixture":      {"cp": 3200, "water": 0.5, "tf": -2.0, "q10": 2.5, "life": 3, "t_ref": 4},
    "dough":        {"cp": 2800, "water": 0.4, "tf": -4.0, "q10": 2.0, "life": 3, "t_ref": 4},
    "baby":         {"cp": 3800, "water": 0.85, "tf": -1.0, "q10": 3.0, "life": 2, "t_ref": 4, "odor": True},
    "sweet":        {"cp": 1600, "water": 0.08, "tf": -40.0, "q10": 1.4, "life": 365, "t_ref": 21},
    "scraps":       {"cp": 3600, "water": 0.8, "tf": -1.0, "q10": 3.0, "life": 3, "t_ref": 21, "mold": True, "odor": True},
    "ice":          {"cp": 4180, "water": 1.0, "tf": 0.0, "q10": 1.0, "life": 36500, "t_ref": 0},
    "other":        {"cp": 3500, "water": 0.7, "tf": -1.0, "q10": 2.5, "life": 7, "t_ref": 4},
}

LATENT_J_PER_KG_WATER = 334000.0     # heat of fusion of water
CP_FROZEN = 1900.0                   # J/(kg K), typical frozen food
THERMAL_DIFFUSIVITY = 1.4e-7         # m^2/s, moist food (ASHRAE)

# Convective heat-transfer coefficients W/(m^2 K)
H_STILL_AIR = 8.0
H_FRIDGE = 12.0          # fan-forced compartment
H_FREEZER = 14.0
H_OVEN = 25.0            # convection + radiation in a hot oven
H_CONTACT = 150.0        # food in a hot pan / pot (liquid or direct contact)
H_WATER = 250.0          # immersed in water / ice bath
ENCLOSED_FACTOR = 0.55   # lidded container / sealed package slows exchange

# ----------------------------------------------------------------------
# food safety (USDA FSIS / FDA Food Code)

DANGER_LOW_C = 5.0               # 41 F
DANGER_HIGH_C = 57.0             # 135 F hot holding
TIME_OUT_LIMIT_S = 7200.0        # 2 h cumulative in the danger zone
HOT_AMBIENT_C = 32.0             # above 90 F ambient the limit is 1 h (counted double)
COOL_STAGE1_S = 7200.0           # 60 C -> 21 C within 2 h
COOL_STAGE2_S = 21600.0          # 60 C -> 5 C within 6 h total
REHEAT_C = 74.0                  # leftovers reheated to 165 F
LEFTOVER_DAYS = 4                # cooked leftovers: use within 3-4 days
FRIDGE_MAX_C = 5.0
FREEZER_MAX_C = -12.0
CONTAM_THRESHOLD = 0.05          # pathogen/allergen tag considered present
SAFE_CORE_C = {"poultry": 74, "ground_meat": 71, "meat": 63, "fish": 63, "shellfish": 63, "egg": 71,
               "prepared": 74, "mixture": 71, "frozen_meal": 74}

# ----------------------------------------------------------------------
# bulk densities g/ml by measuring technique (King Arthur weight chart, 1 US cup = 236.6 ml)

CUP_ML = 236.6
TBSP_ML = 14.79
TSP_ML = 4.93
DENSITY = {
    "all_purpose_flour": {"spoon_and_level": 0.528, "dip_and_sweep": 0.634, "packed": 0.68, "sifted": 0.465, "default": 0.55},
    "bread_flour":       {"spoon_and_level": 0.536, "dip_and_sweep": 0.64, "packed": 0.69, "sifted": 0.47, "default": 0.56},
    "sugar":             {"spoon_and_level": 0.845, "dip_and_sweep": 0.85, "packed": 0.88, "default": 0.845},
    "brown_sugar":       {"spoon_and_level": 0.75, "dip_and_sweep": 0.78, "packed": 0.93, "default": 0.8},
    "powdered_sugar":    {"spoon_and_level": 0.48, "dip_and_sweep": 0.56, "packed": 0.62, "sifted": 0.42, "default": 0.5},
    "salt":              {"default": 1.22},
    "kosher_salt":       {"default": 0.57},
    "baking_soda":       {"default": 0.92},
    "baking_powder":     {"default": 0.81},
    "cocoa_powder":      {"spoon_and_level": 0.36, "dip_and_sweep": 0.42, "packed": 0.48, "sifted": 0.33, "default": 0.38},
    "rice":              {"default": 0.78},
    "rolled_oats":       {"default": 0.38},
    "steel_cut_oats":    {"default": 0.68},
    "cereal":            {"default": 0.13},
    "granola":           {"default": 0.42},
    "breadcrumbs":       {"default": 0.47},
    "cornstarch":        {"spoon_and_level": 0.54, "dip_and_sweep": 0.6, "default": 0.55},
    "ground_coffee":     {"default": 0.36},
    "lentils":           {"default": 0.82},
    "baby_formula":      {"spoon_and_level": 0.5, "dip_and_sweep": 0.56, "packed": 0.62, "default": 0.5},
}
# Liquid food that leaves its vessel is a kernel spill (house_sim/data/physics/liquids.json: density, viscosity,
# residue); food kind -> liquids.json name for kinds whose own name is not a row or alias there, then by category.
# (The densities of plain liquids are liquids.json's; food's own food.density stays the food's.)
SPILL_LIQUID = {"raw_egg": "raw_egg", "beaten_egg": "raw_egg", "egg_white": "raw_egg", "egg_yolk": "raw_egg",
                "egg_wash": "raw_egg", "meat_juice": "raw_meat_juice", "chicken_stock": "broth",
                "vegetable_stock": "broth", "whole_milk": "milk", "buttermilk": "milk", "oat_milk": "milk",
                "almond_milk": "milk", "coconut_milk": "milk", "half_and_half": "cream", "batter": "soup",
                "tomato_sauce": "soup", "smoothie": "juice", "lemon_juice": "juice", "lime_juice": "juice",
                "maple_syrup": "syrup", "bacon_grease": "oil", "frying_oil": "oil", "soy_sauce": "vinegar"}
SPILL_LIQUID_BY_CAT = {"oil": "oil", "dairy": "milk", "beverage": "juice", "alcohol": "wine", "sweet": "syrup",
                       "sauce": "soup", "condiment": "soup", "prepared": "soup", "mixture": "soup", "egg": "raw_egg",
                       "baby": "soup"}

# Packaging (TIER1_INTEGRATION 6 "food"; kernel physics): a sealed retail package is the food entity, and physically it
# is its packaging -- a jar of honey drops and breaks like a glass jar, a can dents, a carton crumples. Material names
# are house_sim/data/physics/materials.json aliases (carton = polyethylene-lined paperboard). Look-alike groups share
# one packaging so a drop never tells members apart (nut butters, cooking oils, baking boxes, spice jars, cartons).
PACKAGING = {"bag": "film", "bottle": "plastic", "box": "cardboard", "can": "tin", "canister": "cardboard",
             "carton": "carton", "cup": "plastic", "jar": "glass", "mill": "glass", "pack": "film", "shaker": "glass",
             "tray": "film", "tub": "plastic", "wrapper": "film"}
PACKAGING_BY_TYPE = {
    # paper sacks: flour and sugar (the flour_sugar_bag group)
    "all_purpose_flour": "paper", "bread_flour": "paper", "sugar_bag": "paper", "powdered_sugar_bag": "paper",
    # glass bottles (cooking oils share one look); squeeze bottles stay plastic
    "olive_oil_bottle": "glass", "vegetable_oil_bottle": "glass", "wine_bottle": "glass", "hot_sauce_bottle": "glass",
    "soy_sauce_bottle": "glass",
    # aluminium drink cans; paperboard baking-powder and cocoa canisters (the small_baking_box group is all board);
    # plastic coffee and nut canisters
    "beer_can": "aluminum", "canned_soda": "aluminum", "sparkling_water_can": "aluminum",
    "baking_powder_can": "cardboard", "cocoa_powder_can": "cardboard", "ground_coffee_can": "plastic",
    "mixed_nuts_can": "plastic",
    # plastic jars: nut butters (one look for the group), peanuts, US mayonnaise
    "peanut_butter_jar": "plastic", "almond_butter_jar": "plastic", "peanuts_jar": "plastic", "mayonnaise_jar": "plastic",
    # foil-laminated paper around cream cheese; paperboard ice-cream tubs
    "cream_cheese": "foil_paper", "ice_cream_tub": "carton"}

# Crush (kernel crush law; TIER1_PHYSICS 5.3): load on top (kg) a soft package gives way under -- a bagged sliced loaf
# squashes under about 1.5 kg (a bag of apples on it), a bag of chips or pretzels breaks its contents under about 1 kg.
CRUSH_KG = {"bread_loaf": 1.5, "gluten_free_bread_loaf": 1.5, "chips_bag": 1.0, "popcorn_bag": 1.0, "pretzels_bag": 1.0}

# Produce under impact (kernel impact law: the organic class bruises from about 1 m onto a hard floor). Firm produce
# keeps that; soft and ripe produce bruises from lower drops (a ripe peach from about 0.4 m, raspberries from 0.3 m).
BRUISE_DROP_MIN = 0.3        # fraction of the class drop tolerance left for the softest food

# measurement reading error for liquids (parallax / level surface)
READ_ERROR = {"eye_level": 0.0, "above": 0.07, "tilted": 0.06}

# ----------------------------------------------------------------------
# storage zones and rules (USDA FoodKeeper)

COLD_FRIDGE_MAX = 8.0
FREEZER_TEMP_MAX = -5.0
ZONES = ("freezer", "fridge", "pantry", "cupboard", "counter", "fruit_bowl", "bread_box", "cooler",
         "lunchbox", "room", "outdoor", "robot", "person", "trash", "compost")
STORAGE_ALLOWED = {
    "freezer": {"freezer"},
    "fridge": {"fridge"},
    "pantry": {"pantry", "cupboard"},
    "cool_dark": {"pantry", "cupboard"},
    "counter": {"counter", "fruit_bowl", "bread_box", "pantry", "cupboard"},
    "bread_box": {"bread_box", "pantry", "cupboard", "counter"},
}
HUMIDITY_CLASS = {"high": "crisper_high", "low": "crisper_low"}

# ----------------------------------------------------------------------
# allergens, diets, age rules

ALLERGENS = ("peanut", "tree_nut", "milk", "egg", "wheat", "soy", "fish", "shellfish", "sesame", "gluten")
DIET_FORBIDDEN = {
    "vegetarian": {"meat", "poultry", "fish", "shellfish", "gelatin"},
    "vegan": {"meat", "poultry", "fish", "shellfish", "dairy", "egg", "honey", "gelatin"},
    "pescatarian": {"meat", "poultry"},
    "halal": {"pork", "alcohol", "gelatin"},
    "kosher": {"pork", "shellfish"},
    "gluten_free": {"gluten"},
    "lactose_intolerant": {"lactose"},
    "dairy_free": {"dairy"},
    "nut_free": {"peanut", "tree_nut"},
    "no_alcohol": {"alcohol"},
}
SODIUM_MG_PER_G_SALT = 393.0
LOW_SODIUM_MG_PER_MEAL = 600.0
DIABETIC_CARBS_G_PER_MEAL = 60.0
TEXTURE_MAX_MM = {"puree": 0.0, "minced_moist": 4.0, "soft_bite_sized": 15.0, "regular": 1000.0}

CHOKING_AGE = 4                   # AAP: round/hard foods are hazards under 4
ROUND_HAZARD_MM = 10.0            # round pieces this wide or wider block a toddler airway
HARD_HAZARD_MM = 10.0
ALWAYS_HAZARD_UNDER_4 = {"popcorn", "peanuts", "almonds", "mixed_nuts", "cashews", "hard_candy", "whole_nuts", "marshmallow"}
INFANT_FORBIDDEN = {"honey"}      # botulism risk under 12 months
CHILD_SERVE_MAX_C = 55.0
INFANT_SERVE_MAX_C = 40.0
ADULT_SERVE_MAX_C = 85.0           # hot soups and drinks are served at 70-85 C to adults
DRINKING_AGE = {"us": 21, "ca": 19, "uk": 18, "eu": 18, "jp": 20}

# ----------------------------------------------------------------------
# cutting

DICE_MM = {"brunoise": 3.0, "small": 6.0, "medium": 12.0, "large": 20.0}
CHOP_MM = {"fine": 4.0, "medium": 8.0, "coarse": 15.0}
CUBE_MM = (15.0, 40.0)
MINCE_MAX_MM = 3.0
CV_GOOD = 0.12          # claw grip, stable board, sharp knife
CV_SLOPPY = 0.32
SLIP_RISK_LIMIT = 0.6
GREASY_HANDLE_RISK = 0.4    # x body.slippery of the hand on the handle: oily or doughy fingers alone never reach the
                            # limit, but with a sliding board or a dull blade they make the cut slip
# fine positioning (body.precision_mm of the working hand; a humanoid hand is about 2 mm, worse with slippery fingers,
# a dirty lens, a dark room or fast speed): the tips of fish pin bones stand about 1-2 mm proud of the flesh (fish
# tweezers forgive about half a millimetre more); an oyster's hinge pocket takes the knife tip within about 4 mm
PIN_BONE_MM = 2.5
OYSTER_HINGE_MM = 4.0

# ----------------------------------------------------------------------
# mixing state machine thresholds

PEAKS = {"soft": (0.45, 0.70), "medium": (0.6, 0.8), "stiff": (0.70, 0.95)}
OVERWHIP = 1.05
GLUTEN_MUFFIN_MAX = 0.35
GLUTEN_WINDOWPANE = (0.75, 1.15)
HOMOGENEOUS = 0.9

# ----------------------------------------------------------------------
# yields (fraction of whole mass) for juice/zest

JUICE_ML_PER_UNIT = {"lemon": 45.0, "lime": 28.0, "orange": 85.0, "grapefruit": 170.0}
ZEST_G_PER_UNIT = {"lemon": 6.0, "lime": 3.0, "orange": 10.0, "grapefruit": 15.0}
SEEDS_PER_UNIT = {"lemon": 6, "lime": 1, "orange": 8, "grapefruit": 10}

# ----------------------------------------------------------------------
# region date formats for printed labels

DATE_FORMATS = {"us": "MDY", "ca": "YMD", "uk": "DMY", "eu": "DMY", "jp": "YMD"}

# Functional substitutes (King Arthur / USDA): missing ingredient -> list of {parts: {kind: fraction of the
# missing ingredient's mass}} that perform the same function in a recipe.
SUBSTITUTES = {
    "buttermilk": [{"parts": {"milk": 0.94, "lemon_juice": 0.06}}, {"parts": {"milk": 0.94, "vinegar": 0.06}},
                   {"parts": {"yogurt": 0.75, "milk": 0.25}}],
    "brown_sugar": [{"parts": {"sugar": 0.92, "molasses": 0.08}}],
    "raw_egg": [{"parts": {"ground_flaxseed": 0.15, "water": 0.85}}],
    "baking_powder": [{"parts": {"baking_soda": 0.25, "cream_of_tartar": 0.5}}],
    "self_rising_flour": [{"parts": {"all_purpose_flour": 0.97, "baking_powder": 0.03}}],
    "cake_flour": [{"parts": {"all_purpose_flour": 0.875, "cornstarch": 0.125}}],
    "heavy_cream": [{"parts": {"milk": 0.75, "unsalted_butter": 0.25}}],
    "fresh_herbs": [{"parts": {"dried_herbs": 0.33}}],
}

# Waste streams for scraps (household may override with household.food_rules.scraps)
SCRAP_STREAM = {"vegetable": "compost", "fruit": "compost", "herb": "compost", "leafy_green": "compost",
                "root_veg": "compost", "berry": "compost", "egg": "compost", "bread": "compost",
                "poultry": "trash", "meat": "trash", "ground_meat": "trash", "fish": "trash", "shellfish": "trash",
                "oil": "trash", "prepared": "compost"}

# Heat-driven doneness (food owns what heat does to food; appliances own the heat).
EGG_WHITE_SET_C = 62.0        # ovalbumin/conalbumin set 62-80 C (McGee)
EGG_YOLK_SET_C = 63.0         # yolk thickens from ~63-65 C, firm by ~70 C
EGG_WHITE_RATE = 600.0        # deg-C-seconds above threshold per unit "set" (72 C surface -> ~60 s)
EGG_YOLK_RATE = 1500.0        # fridge egg in boiling water: runny at 6-7 min, jammy ~9, set 10-12, chalky/green > ~13
EGG_OVERCOOKED = 3.5          # cumulative set index past which yolks go chalky with a green ring / scrambled eggs weep
TOAST_START_C = 120.0         # Maillard browning once the surface has dried above ~120 C
TOAST_RATE = 100.0 * 90.0     # 220 C surface -> golden in ~90 s
GRAIN_SIMMER_C = 85.0
# grain kind -> (liquid:grain mass ratio for a proper texture, cook time at a simmer in s)
GRAIN_COOK = {"rolled_oats": (2.0, 300.0), "quick_oats": (2.0, 90.0), "steel_cut_oats": (3.5, 1500.0),
              "rice": (1.5, 1080.0), "lentils": (2.5, 1200.0), "quinoa": (2.0, 900.0), "pasta": (10.0, 600.0)}
GRAIN_DISH = {"rolled_oats": "oatmeal", "quick_oats": "oatmeal", "steel_cut_oats": "oatmeal", "rice": "cooked_rice",
              "lentils": "cooked_lentils", "quinoa": "cooked_quinoa", "pasta": "cooked_pasta"}
SALT_PCT_BALANCED = (0.6, 1.2)  # % salt by mass that reads as "well seasoned" in soups/sauces

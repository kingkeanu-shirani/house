"""Plant species care profiles (horticultural reference values, approximate).

Moisture is expressed as relative water content RWC of the root zone:
    RWC = (theta - wilting_point) / (field_capacity - wilting_point)
so 0 = wilting point, 1 = container/field capacity, >1 = waterlogged.

Per species:
  band [lo, hi]   RWC range the plant is healthy in (succulents must dry out: hi < 1)
  water_at        RWC below which it should be watered; watering above water_at+0.25 is overwatering
  et              transpiration, L/day for a typical specimen indoors at 21 C in moderate light
  dli [lo, hi]    daily light integral, mol/m2/day; direct: tolerates direct sun through glass
  temp [lo, hi]   comfortable zone temperature C; hardy_c: tissue death below this
  toxic           pets for which ingestion is toxic (ASPCA lists)
  leaf            glossy|fuzzy|farina|fine|succulent|soft|matte|needle|spiny
  bud_sensitive   drops buds when moved/rotated while in bud
  mix             preferred container medium
  harvest         method that detaches produce cleanly; others tear the plant
"""

MEDIA = {
    # volumetric water content: saturation, field (container) capacity, wilting point; infiltration mm/h
    "potting_mix": {"sat": 0.65, "fc": 0.50, "wilt": 0.12, "bulk_kg_l": 0.35, "infil": 60.0},
    "cactus_mix": {"sat": 0.48, "fc": 0.30, "wilt": 0.05, "bulk_kg_l": 0.75, "infil": 150.0},
    "seed_mix": {"sat": 0.70, "fc": 0.55, "wilt": 0.15, "bulk_kg_l": 0.25, "infil": 60.0},
    "bark": {"sat": 0.55, "fc": 0.28, "wilt": 0.05, "bulk_kg_l": 0.25, "infil": 300.0},
    "garden_soil": {"sat": 0.45, "fc": 0.30, "wilt": 0.12, "bulk_kg_l": 1.25, "infil": 15.0},
    "topsoil": {"sat": 0.45, "fc": 0.30, "wilt": 0.12, "bulk_kg_l": 1.3, "infil": 15.0},
    "clay": {"sat": 0.50, "fc": 0.38, "wilt": 0.22, "bulk_kg_l": 1.35, "infil": 4.0},
    "sandy": {"sat": 0.38, "fc": 0.15, "wilt": 0.06, "bulk_kg_l": 1.5, "infil": 40.0},
    "lawn_loam": {"sat": 0.45, "fc": 0.30, "wilt": 0.12, "bulk_kg_l": 1.3, "infil": 15.0},
    "foam": {"sat": 0.9, "fc": 0.9, "wilt": 0.0, "bulk_kg_l": 0.03, "infil": 1000.0},
    "none": {"sat": 0.0, "fc": 0.0, "wilt": 0.0, "bulk_kg_l": 0.0, "infil": 0.0},
}
CONTAINER_MEDIA = {"potting_mix", "cactus_mix", "seed_mix", "bark"}

BASE = {"kind": "houseplant", "band": [0.3, 1.0], "water_at": 0.35, "et": 0.08, "dli": [4, 20], "direct": False, "temp": [12, 30],
        "hardy_c": 2.0, "frost_tender": True, "toxic": [], "leaf": "glossy", "woody": False, "bud_sensitive": False, "mix": "potting_mix",
        "bloom": None, "deadhead": False, "seedheads": False, "harvest": None, "root": "fibrous", "spacing_cm": 30, "sun": "part",
        "season": None, "lookalike": None, "toxic_touch": False, "invasive": False, "dormant_months": [], "foliar_disease": False,
        "look": "leafy plant", "weed": False, "lean_rate": 1.0, "thorny": False}


def _sp(**kw):
    record = dict(BASE)
    record.update(kw)
    return record


SPECIES = {
    # ---------------- houseplants
    "pothos": _sp(band=[0.25, 1.0], water_at=0.3, et=0.07, dli=[2, 18], temp=[12, 32], toxic=["cat", "dog"], look="trailing vine, heart-shaped glossy leaves"),
    "snake_plant": _sp(band=[0.05, 0.75], water_at=0.1, et=0.025, dli=[1, 30], direct=True, temp=[10, 32], toxic=["cat", "dog"], leaf="succulent", mix="cactus_mix",
                       look="stiff upright sword-shaped leaves with bands", dormant_months=[11, 12, 1, 2]),
    "peace_lily": _sp(band=[0.4, 1.0], water_at=0.45, et=0.14, dli=[2, 12], temp=[13, 30], toxic=["cat", "dog"], lookalike="calla_lily",
                      look="dark glossy lance leaves, white hooded flowers", bloom="indoor"),
    "calla_lily": _sp(band=[0.45, 1.0], water_at=0.5, et=0.12, dli=[8, 25], direct=True, temp=[10, 30], toxic=["cat", "dog"], lookalike="peace_lily",
                      look="dark glossy lance leaves, white hooded flowers"),
    "fiddle_leaf_fig": _sp(band=[0.3, 1.0], water_at=0.35, et=0.2, dli=[10, 35], direct=True, temp=[15, 30], toxic=["cat", "dog"], look="large violin-shaped leathery leaves", lean_rate=1.5),
    "jade_plant": _sp(kind="succulent", band=[0.0, 0.7], water_at=0.1, et=0.02, dli=[10, 40], direct=True, temp=[5, 35], toxic=["cat", "dog"], leaf="succulent", mix="cactus_mix",
                      dormant_months=[11, 12, 1, 2], look="thick oval fleshy leaves on woody stems"),
    "echeveria": _sp(kind="succulent", band=[0.0, 0.65], water_at=0.1, et=0.015, dli=[12, 45], direct=True, temp=[5, 35], leaf="farina", mix="cactus_mix",
                     dormant_months=[11, 12, 1, 2], look="powdery blue-grey rosette"),
    "golden_barrel_cactus": _sp(kind="succulent", band=[0.0, 0.6], water_at=0.05, et=0.01, dli=[15, 50], direct=True, temp=[4, 40], leaf="spiny", mix="cactus_mix",
                                dormant_months=[11, 12, 1, 2], look="round ribbed cactus with golden spines"),
    "aloe_vera": _sp(kind="succulent", band=[0.0, 0.65], water_at=0.1, et=0.02, dli=[10, 40], direct=True, temp=[7, 35], toxic=["cat", "dog"], leaf="succulent", mix="cactus_mix",
                     look="thick serrated grey-green spears"),
    "boston_fern": _sp(band=[0.5, 1.0], water_at=0.55, et=0.15, dli=[3, 12], temp=[13, 29], leaf="fine", look="arching fronds of small leaflets"),
    "african_violet": _sp(band=[0.4, 1.0], water_at=0.45, et=0.04, dli=[5, 12], temp=[16, 29], leaf="fuzzy", look="rosette of fuzzy round leaves, purple flowers", bloom="indoor"),
    "rubber_plant": _sp(band=[0.3, 1.0], water_at=0.35, et=0.1, dli=[6, 25], temp=[13, 32], toxic=["cat", "dog"], look="thick oval glossy burgundy leaves"),
    "monstera": _sp(band=[0.3, 1.0], water_at=0.35, et=0.15, dli=[4, 20], temp=[15, 32], toxic=["cat", "dog"], look="large split glossy leaves"),
    "zz_plant": _sp(band=[0.05, 0.8], water_at=0.1, et=0.02, dli=[1, 15], temp=[10, 32], toxic=["cat", "dog"], look="waxy pinnate leaves on thick stems"),
    "spider_plant": _sp(band=[0.3, 1.0], water_at=0.35, et=0.08, dli=[4, 20], temp=[10, 32], leaf="fine", look="arching striped strap leaves with plantlets"),
    "christmas_cactus": _sp(band=[0.3, 0.95], water_at=0.35, et=0.04, dli=[5, 15], temp=[10, 27], leaf="succulent", bud_sensitive=True, look="flat segmented stems, pink buds", bloom="winter"),
    "gardenia": _sp(band=[0.45, 1.0], water_at=0.5, et=0.12, dli=[10, 25], temp=[15, 27], toxic=["cat", "dog"], bud_sensitive=True, look="glossy dark leaves, cream buds", bloom="summer"),
    "phalaenopsis_orchid": _sp(band=[0.15, 0.9], water_at=0.2, et=0.03, dli=[4, 12], temp=[16, 30], mix="bark", look="broad flat leaves, arching flower spike"),
    "easter_lily": _sp(band=[0.4, 1.0], water_at=0.45, et=0.12, dli=[10, 25], direct=True, temp=[10, 28], toxic=["cat"], leaf="matte", look="tall stem, trumpet white flowers"),
    "artificial": _sp(kind="artificial", band=[0.0, 0.0], water_at=-1.0, et=0.0, dli=[0, 999], direct=True, temp=[-40, 80], hardy_c=-60, frost_tender=False, mix="foam", look="leafy plant"),

    # ---------------- herbs
    "basil": _sp(kind="herb", band=[0.45, 1.0], water_at=0.5, et=0.18, dli=[15, 35], direct=True, temp=[10, 35], hardy_c=2.0, leaf="soft", harvest="pinch",
                 look="soft bright-green oval leaves", bolts=True),
    "mint": _sp(kind="herb", band=[0.45, 1.0], water_at=0.5, et=0.15, dli=[6, 30], direct=True, temp=[5, 32], hardy_c=-25, frost_tender=False, leaf="soft", harvest="pinch",
                lookalike="lemon_balm", look="square stems, toothed oval leaves"),
    "lemon_balm": _sp(kind="herb", band=[0.4, 1.0], water_at=0.45, et=0.12, dli=[6, 30], direct=True, temp=[5, 32], hardy_c=-25, frost_tender=False, leaf="soft", harvest="pinch",
                      lookalike="mint", look="square stems, toothed oval leaves"),
    "rosemary": _sp(kind="herb", band=[0.15, 0.85], water_at=0.2, et=0.06, dli=[15, 40], direct=True, temp=[0, 35], hardy_c=-8, frost_tender=False, leaf="needle", woody=True, harvest="cut",
                    look="needle-like leaves on woody stems"),
    "thyme": _sp(kind="herb", band=[0.1, 0.8], water_at=0.15, et=0.04, dli=[15, 40], direct=True, temp=[-5, 35], hardy_c=-20, frost_tender=False, leaf="fine", woody=True, harvest="cut",
                 look="tiny leaves on wiry woody stems"),
    "flat_parsley": _sp(kind="herb", band=[0.4, 1.0], water_at=0.45, et=0.12, dli=[10, 30], direct=True, temp=[5, 30], hardy_c=-8, frost_tender=False, leaf="soft", harvest="cut",
                        lookalike="cilantro", look="flat lobed serrated leaflets"),
    "cilantro": _sp(kind="herb", band=[0.4, 1.0], water_at=0.45, et=0.12, dli=[10, 30], direct=True, temp=[5, 27], hardy_c=-5, frost_tender=False, leaf="soft", harvest="cut",
                    lookalike="flat_parsley", look="flat lobed serrated leaflets", bolts=True),
    "chives": _sp(kind="herb", band=[0.35, 1.0], water_at=0.4, et=0.08, dli=[10, 30], direct=True, temp=[0, 30], hardy_c=-30, frost_tender=False, leaf="soft", harvest="cut",
                  look="hollow grass-like leaves"),

    # ---------------- outdoor ornamentals and shrubs
    "petunia": _sp(kind="ornamental", band=[0.35, 1.0], water_at=0.4, et=0.4, dli=[20, 45], direct=True, temp=[8, 35], hardy_c=0.0, deadhead=True, sun="full", spacing_cm=25, look="trumpet flowers, sticky leaves", bloom="summer"),
    "geranium": _sp(kind="ornamental", band=[0.25, 0.95], water_at=0.3, et=0.3, dli=[15, 40], direct=True, temp=[8, 32], hardy_c=0.0, toxic=["cat", "dog"], deadhead=True, sun="full", spacing_cm=30, look="scalloped round leaves, flower clusters", bloom="summer"),
    "marigold": _sp(kind="ornamental", band=[0.3, 1.0], water_at=0.35, et=0.3, dli=[20, 45], direct=True, temp=[8, 35], hardy_c=0.0, deadhead=True, sun="full", spacing_cm=25, look="ferny leaves, orange pompom flowers", bloom="summer"),
    "impatiens": _sp(kind="ornamental", band=[0.45, 1.0], water_at=0.5, et=0.35, dli=[4, 15], direct=False, temp=[10, 30], hardy_c=1.0, sun="shade", spacing_cm=25, look="succulent stems, flat flowers", bloom="summer"),
    "hosta": _sp(kind="ornamental", band=[0.4, 1.0], water_at=0.45, et=0.4, dli=[3, 15], direct=False, temp=[-30, 30], hardy_c=-35, frost_tender=False, toxic=["cat", "dog"], sun="shade", spacing_cm=50, look="broad ribbed leaves"),
    "coneflower": _sp(kind="ornamental", band=[0.2, 0.95], water_at=0.25, et=0.35, dli=[20, 45], direct=True, temp=[-30, 35], hardy_c=-35, frost_tender=False, deadhead=True, seedheads=True, sun="full", spacing_cm=45, look="daisy flowers with domed centers", bloom="summer"),
    "sunflower": _sp(kind="ornamental", band=[0.3, 1.0], water_at=0.35, et=0.8, dli=[25, 50], direct=True, temp=[8, 35], hardy_c=0.0, deadhead=True, seedheads=True, sun="full", spacing_cm=45, look="tall stalk, large yellow flower head", bloom="summer"),
    "rose": _sp(kind="shrub", band=[0.35, 1.0], water_at=0.4, et=1.0, dli=[20, 45], direct=True, temp=[-20, 35], hardy_c=-25, frost_tender=False, woody=True, deadhead=True, seedheads=True,
                thorny=True, bloom="summer", sun="full", spacing_cm=90, look="thorny canes, compound leaves", foliar_disease=True),
    "hydrangea": _sp(kind="shrub", band=[0.45, 1.0], water_at=0.5, et=1.5, dli=[8, 30], direct=False, temp=[-20, 32], hardy_c=-25, frost_tender=False, woody=True, bloom="summer", sun="part", spacing_cm=120, look="large toothed leaves, mophead flowers"),
    "lilac": _sp(kind="shrub", band=[0.3, 1.0], water_at=0.35, et=1.5, dli=[20, 45], direct=True, temp=[-35, 32], hardy_c=-40, frost_tender=False, woody=True, bloom="spring", sun="full", spacing_cm=200, look="heart-shaped leaves, flower bud clusters"),
    "forsythia": _sp(kind="shrub", band=[0.3, 1.0], water_at=0.35, et=1.2, dli=[20, 45], direct=True, temp=[-30, 32], hardy_c=-35, frost_tender=False, woody=True, bloom="spring", sun="full", spacing_cm=180, look="arching stems, yellow spring flowers"),
    "spirea": _sp(kind="shrub", band=[0.3, 1.0], water_at=0.35, et=1.0, dli=[20, 45], direct=True, temp=[-35, 32], hardy_c=-40, frost_tender=False, woody=True, bloom="summer", sun="full", spacing_cm=100, look="fine twigs, flat flower clusters"),
    "boxwood": _sp(kind="shrub", band=[0.3, 1.0], water_at=0.35, et=0.8, dli=[10, 40], direct=True, temp=[-25, 32], hardy_c=-28, frost_tender=False, woody=True, toxic=["cat", "dog"], sun="part", spacing_cm=60, look="small glossy evergreen leaves"),
    "privet": _sp(kind="shrub", band=[0.3, 1.0], water_at=0.35, et=1.0, dli=[10, 40], direct=True, temp=[-25, 32], hardy_c=-30, frost_tender=False, woody=True, toxic=["cat", "dog"], sun="part", spacing_cm=45, look="small oval leaves, dense twigs"),
    "lavender": _sp(kind="ornamental", band=[0.1, 0.8], water_at=0.15, et=0.3, dli=[25, 50], direct=True, temp=[-15, 35], hardy_c=-20, frost_tender=False, woody=True, sun="full", spacing_cm=45, look="grey needle leaves, purple spikes"),
    "chrysanthemum": _sp(kind="ornamental", band=[0.4, 1.0], water_at=0.45, et=0.4, dli=[15, 40], direct=True, temp=[-5, 30], hardy_c=-10, frost_tender=False, toxic=["cat", "dog"], deadhead=True, sun="full", spacing_cm=45, look="lobed aromatic leaves, button flowers", bloom="autumn"),
    "lemon_tree": _sp(kind="fruit", band=[0.35, 1.0], water_at=0.4, et=0.5, dli=[20, 45], direct=True, temp=[7, 35], hardy_c=-2.0, toxic=["cat", "dog"], woody=True, harvest="cut", sun="full", look="glossy pointed leaves, thorny twigs, lemons"),
    "hibiscus": _sp(kind="ornamental", band=[0.45, 1.0], water_at=0.5, et=0.6, dli=[20, 45], direct=True, temp=[10, 35], hardy_c=2.0, woody=True, sun="full", look="glossy toothed leaves, big trumpet flowers"),
    "juniper": _sp(kind="shrub", band=[0.1, 0.9], water_at=0.15, et=0.8, dli=[20, 50], direct=True, temp=[-35, 38], hardy_c=-40, frost_tender=False, woody=True, sun="full", spacing_cm=120, look="scale-like blue-green foliage"),
    "maple": _sp(kind="tree", band=[0.3, 1.0], water_at=0.3, et=30.0, dli=[20, 50], direct=True, temp=[-35, 35], hardy_c=-40, frost_tender=False, woody=True, sun="full", look="tall deciduous tree"),

    # ---------------- vegetables and fruit
    "tomato": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=1.5, dli=[20, 45], direct=True, temp=[10, 32], hardy_c=1.0, harvest="twist", sun="full", spacing_cm=60, season="warm",
                  foliar_disease=True, look="hairy aromatic compound leaves", plant_deeper=True),
    "zucchini": _sp(kind="vegetable", band=[0.45, 1.0], water_at=0.5, et=2.0, dli=[20, 45], direct=True, temp=[12, 35], hardy_c=1.0, harvest="cut", sun="full", spacing_cm=90, season="warm",
                    foliar_disease=True, look="huge lobed prickly leaves"),
    "bush_bean": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=0.6, dli=[20, 45], direct=True, temp=[12, 32], hardy_c=1.0, harvest="snap", sun="full", spacing_cm=15, season="warm", look="trifoliate leaves"),
    "pepper": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=0.8, dli=[20, 45], direct=True, temp=[13, 32], hardy_c=2.0, harvest="cut", sun="full", spacing_cm=45, season="warm", look="glossy pointed leaves"),
    "cucumber": _sp(kind="vegetable", band=[0.45, 1.0], water_at=0.5, et=1.5, dli=[20, 45], direct=True, temp=[13, 33], hardy_c=2.0, harvest="cut", sun="full", spacing_cm=45, season="warm", foliar_disease=True, look="rough lobed leaves, tendrils"),
    "lettuce": _sp(kind="vegetable", band=[0.5, 1.0], water_at=0.55, et=0.3, dli=[12, 25], direct=True, temp=[4, 24], hardy_c=-4, frost_tender=False, harvest="cut", sun="part", spacing_cm=20, season="cool", heat_sensitive=True, look="soft rosette of leaves"),
    "kale": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=0.6, dli=[15, 40], direct=True, temp=[-5, 27], hardy_c=-12, frost_tender=False, harvest="cut", sun="full", spacing_cm=45, season="cool", look="crinkled blue-green leaves"),
    "carrot": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=0.2, dli=[15, 40], direct=True, temp=[-2, 27], hardy_c=-8, frost_tender=False, harvest="pull", sun="full", spacing_cm=5, season="cool", look="feathery fern-like tops", root="taproot"),
    "winter_squash": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=2.0, dli=[20, 45], direct=True, temp=[12, 35], hardy_c=1.0, harvest="cut", sun="full", spacing_cm=120, season="warm", foliar_disease=True, look="sprawling vine, large leaves"),
    "pea": _sp(kind="vegetable", band=[0.4, 1.0], water_at=0.45, et=0.4, dli=[15, 40], direct=True, temp=[2, 24], hardy_c=-5, frost_tender=False, harvest="snap", sun="full", spacing_cm=5, season="cool", look="climbing tendrils, oval leaflets"),
    "strawberry": _sp(kind="fruit", band=[0.45, 1.0], water_at=0.5, et=0.3, dli=[15, 40], direct=True, temp=[-10, 30], hardy_c=-15, frost_tender=False, harvest="pinch", sun="full", spacing_cm=30, look="trifoliate toothed leaves, runners"),
    "raspberry": _sp(kind="fruit", band=[0.4, 1.0], water_at=0.45, et=1.0, dli=[20, 45], direct=True, temp=[-25, 30], hardy_c=-30, frost_tender=False, woody=True, thorny=True, harvest="roll", sun="full", spacing_cm=60, look="prickly canes"),
    "apple": _sp(kind="tree", band=[0.3, 1.0], water_at=0.3, et=20.0, dli=[20, 50], direct=True, temp=[-30, 35], hardy_c=-35, frost_tender=False, woody=True, harvest="lift_twist", sun="full", look="fruit tree"),
    "fig": _sp(kind="tree", band=[0.3, 1.0], water_at=0.3, et=8.0, dli=[20, 50], direct=True, temp=[-8, 38], hardy_c=-10, frost_tender=False, woody=True, harvest="lift_twist", sun="full", look="lobed rough leaves"),

    # ---------------- weeds and volunteers (role decides keep/pull; see entity owner_wants)
    "dandelion": _sp(kind="weed", weed=True, band=[0.1, 1.0], water_at=0.0, et=0.1, dli=[5, 50], direct=True, temp=[-30, 35], hardy_c=-35, frost_tender=False, root="taproot", root_cm=20, look="toothed leaf rosette, yellow flower or puffball"),
    "crabgrass": _sp(kind="weed", weed=True, band=[0.1, 1.0], water_at=0.0, et=0.05, dli=[10, 50], direct=True, temp=[5, 40], hardy_c=0.0, root="fibrous", root_cm=8, look="coarse sprawling grass"),
    "bindweed": _sp(kind="weed", weed=True, invasive=True, band=[0.1, 1.0], water_at=0.0, et=0.1, dli=[10, 50], direct=True, temp=[-25, 38], hardy_c=-30, frost_tender=False, root="rhizome", root_cm=60, look="twining vine, arrow-shaped leaves"),
    "plantain_weed": _sp(kind="weed", weed=True, band=[0.1, 1.0], water_at=0.0, et=0.05, dli=[5, 50], direct=True, temp=[-25, 35], hardy_c=-30, frost_tender=False, root="taproot", root_cm=12, look="broad ribbed leaf rosette"),
    "chickweed": _sp(kind="weed", weed=True, band=[0.1, 1.0], water_at=0.0, et=0.03, dli=[3, 40], direct=True, temp=[-10, 28], hardy_c=-15, frost_tender=False, root="fibrous", root_cm=4, look="mat of tiny paired leaves"),
    "creeping_charlie": _sp(kind="weed", weed=True, invasive=True, band=[0.1, 1.0], water_at=0.0, et=0.05, dli=[3, 40], direct=True, temp=[-25, 32], hardy_c=-30, frost_tender=False, root="rhizome", root_cm=5, look="scalloped round leaves on creeping runners"),
    "poison_ivy": _sp(kind="weed", weed=True, toxic_touch=True, band=[0.1, 1.0], water_at=0.0, et=0.2, dli=[5, 50], direct=True, temp=[-30, 35], hardy_c=-35, frost_tender=False, woody=True, root="rhizome", root_cm=30, look="leaves of three, glossy, notched"),
    "clover": _sp(kind="weed", weed=True, band=[0.1, 1.0], water_at=0.0, et=0.05, dli=[5, 45], direct=True, temp=[-25, 32], hardy_c=-30, frost_tender=False, root="fibrous", root_cm=10, look="trifoliate round leaflets, white flowers"),
    "wild_violet": _sp(kind="weed", weed=True, band=[0.2, 1.0], water_at=0.0, et=0.03, dli=[3, 30], direct=False, temp=[-25, 30], hardy_c=-30, frost_tender=False, root="rhizome", root_cm=6, look="heart-shaped leaves, small purple flowers"),
    "dill": _sp(kind="herb", band=[0.3, 1.0], water_at=0.35, et=0.2, dli=[15, 40], direct=True, temp=[5, 32], hardy_c=-2, harvest="cut", root="taproot", root_cm=15, look="feathery thread-like leaves"),
    "california_poppy": _sp(kind="ornamental", band=[0.1, 0.9], water_at=0.15, et=0.1, dli=[20, 50], direct=True, temp=[-5, 35], hardy_c=-8, frost_tender=False, root="taproot", root_cm=15, look="blue-grey ferny leaves"),
}

FLOWER_COLORS = ["red", "white", "yellow", "orange", "pink", "purple"]


def profile(species):
    return SPECIES.get(species) or BASE


def is_weed_species(species):
    return bool(profile(species).get("weed"))


def medium_params(kind):
    return MEDIA.get(kind or "potting_mix") or MEDIA["potting_mix"]


def rwc(theta, kind):
    p = medium_params(kind)
    span = max(1e-6, p["fc"] - p["wilt"])
    return (float(theta) - p["wilt"]) / span


def theta_for(rwc_value, kind):
    p = medium_params(kind)
    return p["wilt"] + float(rwc_value) * (p["fc"] - p["wilt"])


def dormant(species, month):
    return month in profile(species).get("dormant_months", [])

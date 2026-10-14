"""Print a generated house as ASCII room grids (debugging aid).

usage: python tools/show_house.py [seed] [room_id ...] [--require garage pool ...] [--gen 1|2]
Legend: R robot, D doorway cell, letters = first letter of blocking furniture, P person, A pet (animal),
        ~ wet cell, ! floor hazard, . free floor.
--gen 2 shows a generation-2 house and prints its household profile (composition, season, smart-home tier, pending
pets) and each resident's role, bedroom and belongings count.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import geometry  # noqa: E402
from house_sim.kernel.engine import HouseEnv  # noqa: E402


def draw(state, rid):
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = geometry.blocked_cells(state, rid, include_agents=False)
    hazards = geometry.hazard_cells(state, rid)
    doors = {tuple(c["cells"][rid]): cid for cid, c in state["connections"].items() if rid in c.get("cells", {})}
    lines = ["{} [{}] {}x{} m, floor {}, {}".format(room["label"], rid, room["size_m"][0], room["size_m"][1], room["floor"], room.get("floor_material"))]
    for y in range(rows):
        row = ""
        for x in range(cols):
            cell, ch = (x, y), "."
            if cell in blocked:
                ch = state["entities"][blocked[cell][0]].get("label", "?")[0].upper()
            if cell in hazards:
                ch = "~" if any(k == "wet" for k, _ in hazards[cell]) else "!"
            if cell in doors:
                ch = "D"
            for group, mark in (("people", "P"), ("pets", "A")):
                for agent in state.get(group, {}).values():
                    if agent.get("location") == rid and tuple(agent.get("pos") or ()) == cell:
                        ch = mark
            if state["robot"]["room"] == rid and tuple(state["robot"].get("pos") or ()) == cell:
                ch = "R"
            row += ch
        lines.append("  " + row)
    for cell, cid in sorted(doors.items()):
        other = [r for r in state["connections"][cid]["rooms"] if r != rid][0]
        conn = state["connections"][cid]
        extra = " exterior, {} steps".format(conn.get("steps")) if conn.get("exterior") else ""
        lines.append("  D{} -> {} ({}{})".format(list(cell), other, conn.get("kind"), extra))
    return "\n".join(lines)


def household(state):
    """Generation-2 household summary lines."""
    prof = (state.get("household") or {}).get("profile") or {}
    lines = ["household: {} ({}), season {}, smart home {}, pending pets {}".format(
        prof.get("composition"), prof.get("relation"), prof.get("season"), prof.get("smart_home"),
        [p["species"] for p in prof.get("pets_pending", [])])]
    for pid, person in sorted(state["people"].items()):
        owned = sum(1 for e in state["entities"].values() if e.get("owner") == pid)
        lines.append("  {} {} {} age {} in {} (bedroom {}), {}, {} things owned".format(
            pid, person.get("label"), person.get("role"), person.get("age"), person.get("location"), person.get("home_room"),
            "asleep in " + person["in_bed"] if person.get("in_bed") and not person.get("awake") else "awake", owned))
    return "\n".join(lines)


def main(argv):
    gen = 1
    if "--gen" in argv:
        i = argv.index("--gen")
        gen, argv = int(argv[i + 1]), argv[:i] + argv[i + 2:]
    require = []
    if "--require" in argv:
        i = argv.index("--require")
        require, argv = argv[i + 1:], argv[:i]
    seed = int(argv[0]) if argv else 0
    opts = {"require": require} if require else {}
    if gen != 1:
        opts["gen"] = gen
    env = HouseEnv(generate_house(seed, **opts), seed=seed)
    state = env.world.s
    print(state["name"], "|", state["metadata"].get("region"), "| people:", list(state["people"]), "| pets:", list(state["pets"]))
    if gen >= 2:
        print(household(state))
    rooms = argv[1:] or sorted(state["rooms"])
    for rid in rooms:
        print(draw(state, rid) if rid in state["rooms"] else "no room {} in this house".format(rid))
        print()


if __name__ == "__main__":
    main(sys.argv[1:])

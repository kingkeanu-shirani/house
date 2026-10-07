"""A deliberately small, deterministic grammar; not an LLM or general NLU."""
import re


def resolve(reference, observation, pointing=None):
    objects = observation.get("visible_entities", {})
    reference = reference.lower().strip().rstrip(".")
    reference = re.sub(r"^(the|a|an) ", "", reference)
    if reference in ("this", "that", "it"):
        if pointing in objects: return {"id": pointing}
        return {"question": "Which visible object do you mean?", "candidates": sorted(objects)}
    matches = [eid for eid, e in objects.items() if e.get("label", "").lower() == reference]
    if len(matches) == 1: return {"id": matches[0]}
    if not matches and reference in objects: return {"id": reference}
    return {"question": "Which {} do you mean?".format(reference), "candidates": matches,
            "reason": "ambiguous" if matches else "not_visible"}


def parse(text, observation, pointing=None):
    text = text.strip().lower().rstrip(".!?")
    text = re.sub(r"^(please|could you) ", "", text)
    single = {"stop": "stop", "resume": "resume", "look": "look", "undo that": "undo", "undo": "undo", "wait": "wait", "listen": "listen"}
    if text in single: return {"command": {"action": single[text]}}
    m = re.fullmatch(r"(?:go|move|walk) (?:to )?([a-z][a-z0-9_]*)", text)
    if m:
        if m[1] in observation["room"]["exits"]: return {"command": {"action": "move", "target": m[1]}}
        return {"question": "Choose a listed exit.", "candidates": observation["room"]["exits"]}
    m = re.fullmatch(r"(?:put|place) (.+?) (?:in|on|into) (.+)", text)
    if m:
        target, dest = resolve(m[1], observation, pointing), resolve(m[2], observation)
        if "id" not in target: return target
        if "id" not in dest: return dest
        return {"command": {"action": "place", "target": target["id"], "destination": dest["id"]}}
    m = re.fullmatch(r"(pick up|open|close|inspect|fold|unfold|clean|wipe|wash|dry|water) (.+)", text)
    if m:
        found = resolve(m[2], observation, pointing)
        if "id" not in found: return found
        action = {"pick up": "pick_up", "clean": "wipe", "water": "water_plant"}.get(m[1], m[1])
        command = {"action": action, "target": found["id"]}
        if action in ("wipe", "dry"): command["tool"] = observation.get("holding") or ""
        if action == "water_plant": command["destination"] = observation.get("holding") or ""
        return {"command": command}
    return {"question": "That phrasing is outside the supported grammar. Use help for explicit commands.", "candidates": [], "reason": "unsupported_language"}

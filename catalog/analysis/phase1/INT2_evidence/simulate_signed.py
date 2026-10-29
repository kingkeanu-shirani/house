"""Simulate `compare ADOPT P1F --package P1` as if P1_signoff_verified.json were committed as signoffs/P1F.json
(tier1_gate.trusted_json patched for that one path; nothing is written or committed).

usage: python simulate_signed.py PROJECT VERIFIED
"""
import json
import os
import sys

project, verified = sys.argv[1:3]
sys.path.insert(0, os.path.join(project, "tools", "orchestration"))
os.chdir(project)
import tier1_gate as T  # noqa: E402

real = T.trusted_json
data = json.load(open(verified, encoding="utf-8"))


def patched(root, rel):
    if rel.replace("\\", "/").endswith("signoffs/P1F.json"):
        return data, "simulated as committed"
    return real(root, rel)


T.trusted_json = patched
sys.exit(T.main(["compare", "ADOPT", "P1F", "--package", "P1"]))

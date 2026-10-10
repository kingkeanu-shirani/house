"""Zip the project to archive/<name>.zip, excluding __pycache__, *.pyc, archive/, LLM response caches and private
split material (split_key, verifier_key, test_manifest.json, test_phrasings.json, .house_ai/, private_results/,
*__PRIVATE*). Never touches %USERPROFILE%/.house_ai.

usage: python snapshot.py NAME
"""
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
name = sys.argv[1]
out = ROOT / "archive" / (name + ".zip")
SKIP_DIRS = {"__pycache__", "archive", ".house_ai", "private_results", "cache", ".llm_cache", "llm_cache",
             ".pytest_cache", "build", "dist"}
SKIP_FILES = {"split_key", "verifier_key", "test_manifest.json", "test_phrasings.json"}
count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and "__PRIVATE" not in d and not d.endswith(".egg-info"))
        for f in sorted(files):
            if f.endswith((".pyc", ".pyo")) or f in SKIP_FILES or "__PRIVATE" in f:
                continue
            p = Path(folder) / f
            zf.write(p, p.relative_to(ROOT).as_posix())
            count += 1
print(out, count, "files", round(out.stat().st_size / 1e6, 1), "MB")

"""Offline setup/health check. No packages need downloading."""
import compileall
import sys
from pathlib import Path

if sys.version_info < (3, 9): raise SystemExit('Python 3.9 or newer is required.')
root=Path(__file__).resolve().parent
if not compileall.compile_dir(str(root/'house_sim'),quiet=1): raise SystemExit('Python compilation failed.')
from house_sim.engine import HouseEnv
HouseEnv().observe()
print('Ready. No dependencies, GPU, or API key required.')
print('Run: py house_env.py')
print('Demo: py house_env.py --demo clean')

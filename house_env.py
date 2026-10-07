"""Launch with py house_env.py; keep this beside the house_sim folder."""
from house_sim.engine import HouseEnv
from house_sim.world import make_house, validate
from house_sim.cli import main

if __name__ == '__main__':
    raise SystemExit(main())

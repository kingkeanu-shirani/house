"""Dispatch evaluation command-line flags to the owning module's main(argv)."""
import importlib

FLAGS = {
    "--init-split-key": "splits", "--build-test-manifest": "splits", "--verify-commitment": "splits", "--split-info": "splits",
    "--run-agent": "run",
    "--leaderboard": "leaderboard", "--verify-submission": "leaderboard",
    "--export-dataset": "dataset", "--validate-dataset": "dataset",
    "--viewer": "viewer",
}


def wants(argv):
    return any(a in FLAGS for a in argv)


def main(argv):
    for arg in argv:
        if arg in FLAGS:
            module = importlib.import_module("house_sim.eval." + FLAGS[arg])
            return module.main(list(argv))
    raise SystemExit("no evaluation flag given")

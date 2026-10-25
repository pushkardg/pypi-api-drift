"""Regenerate tests/fixtures/data (the tiny demo-lib dataset the action workflow test downloads)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import DEMO_V1, DEMO_V2
from helpers import build_dataset

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "data")
    build_dataset(out, {"demo-lib": [("1.0.0", DEMO_V1), ("2.0.0", DEMO_V2)]})
    print("wrote", out)

#!/usr/bin/env python3
"""ALFRED entry point — delegates to alfred.cli.main."""
import sys
import os

# Anchor all relative-path defaults to this script's directory so alfred works
# regardless of the working directory the caller uses (e.g. invoking as
# `python app/alfred_cli.py` from the customer's installation root).
_HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("MASTER_DATA_DIR", os.path.join(_HERE, "data", "master"))
os.environ.setdefault("RUNS_DIR", os.path.join(_HERE, "data", "runs"))

# Ensure the local src/ package takes precedence over any installed version.
sys.path.insert(0, os.path.join(_HERE, "src"))

from alfred.cli import main

if __name__ == "__main__":
    main()

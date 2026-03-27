#!/usr/bin/env python3
"""ALFRED entry point — delegates to alfred.cli.main."""
import sys
import os

# Ensure the local src/ package takes precedence over any installed version.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from alfred.cli import main

if __name__ == "__main__":
    main()

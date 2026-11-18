import sys

from ffp.cli import main

if __name__ == "__main__":  # worker processes (Sprint 8) may import the main module: never rerun the CLI there
    sys.exit(main())

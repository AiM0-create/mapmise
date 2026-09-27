"""Entry point of mapmise-cli, the terminal command shipped inside the desktop app."""
import sys

from mapmise.cli import main

if __name__ == "__main__":
    sys.exit(main())

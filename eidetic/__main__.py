"""Entry point for ``python -m eidetic``."""

from __future__ import annotations

import sys

from eidetic.cli import main

if __name__ == "__main__":
    sys.exit(main())

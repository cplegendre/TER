"""``python -m ter``: the TER 4 command line (see :mod:`ter.adapters.driving.cli`)."""

from __future__ import annotations

import sys

from .bootstrap import main

if __name__ == "__main__":
    sys.exit(main())

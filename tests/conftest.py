"""Shared pytest configuration for the eidetic-cli suite.

`overview` now probes the memory backends (files/mongo/graph) on every call to
report store status. With a down mongo, the default 1s probe timeout would add up
across the suite, so pin it very low here — a down/refused backend fails in
milliseconds. ``setdefault`` lets a developer override it from the environment.
"""

from __future__ import annotations

import os

os.environ.setdefault("EIDETIC_STORE_PROBE_TIMEOUT_MS", "150")

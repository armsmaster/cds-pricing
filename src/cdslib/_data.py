from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_json(name: str) -> Any:
    """Load a JSON data file bundled under ``cdslib/data``."""
    resource = resources.files("cdslib").joinpath("data").joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))

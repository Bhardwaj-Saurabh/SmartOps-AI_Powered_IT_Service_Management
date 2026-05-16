import os
import re
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from di_framework_core import ConfigError

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

T = TypeVar("T", bound=BaseModel)


def _interpolate(node: Any) -> Any:
    """Recursively replace ${VAR} and ${VAR:-default} with os.environ values."""
    if isinstance(node, str):

        def repl(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2)
            value = os.environ.get(var)
            if value is None:
                if default is not None:
                    return default
                raise ConfigError(f"Required env var '{var}' is not set")
            return value

        return _ENV_REF.sub(repl, node)
    if isinstance(node, dict):
        return {k: _interpolate(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_interpolate(v) for v in node]
    return node


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file and interpolate ${ENV_VAR[:-default]} references."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping in {p}, got {type(raw).__name__}")
    return _interpolate(raw)


def load_yaml_as(path: str | Path, model: type[T]) -> T:
    """Load + interpolate + validate against a pydantic model. Errors out cleanly."""
    data = load_yaml(path)
    try:
        return model.model_validate(data)
    except Exception as exc:  # pydantic ValidationError or anything wrapped
        raise ConfigError(f"Config validation failed for {path}: {exc}") from exc

"""Configuration loader.

All IP addresses, ports, serial devices and tuning values live in ``config.yaml``
at the repository root. Every component loads it through :func:`load_config`.

Resolution order for the config file:
    1. explicit ``path`` argument (e.g. from ``--config``)
    2. ``APEX_CONFIG`` environment variable
    3. ``<repo root>/config.yaml``

Environment overrides (so secrets need not be committed):
    APEX_SIM=1         force simulation mode
    APEX_API_KEY       security.api_key
    APEX_AGV_TOKEN     security.agv_token
    APEX_PLC_TOKEN     security.plc_token
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

#: Repository root (the folder that contains ``config.yaml``).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

_ENV_SECRETS = {
    "APEX_API_KEY": ("security", "api_key"),
    "APEX_AGV_TOKEN": ("security", "agv_token"),
    "APEX_PLC_TOKEN": ("security", "plc_token"),
}

_MISSING = object()


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing or invalid."""


class Config:
    """Thin read-only wrapper around the parsed YAML dictionary.

    Values are accessed with dotted paths::

        cfg.get("agv.navigation.max_speed_mps")
        cfg.require("network.agv.port")

    Hosts should be obtained through :meth:`host` so that simulation mode can
    transparently redirect everything to ``127.0.0.1``.
    """

    def __init__(self, data: dict[str, Any], path: Path | None = None, simulation: bool | None = None):
        self._data = data
        self.path = path
        sim_cfg = bool(self.get("system.simulation", False))
        sim_env = os.environ.get("APEX_SIM", "").strip().lower() in ("1", "true", "yes")
        self.simulation: bool = bool(simulation) if simulation is not None else (sim_cfg or sim_env)

    # ------------------------------------------------------------------ access
    def get(self, dotted: str, default: Any = None) -> Any:
        """Return the value at ``dotted`` (e.g. ``"network.agv.port"``) or ``default``."""
        node: Any = self._data
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def require(self, dotted: str) -> Any:
        """Like :meth:`get` but raises :class:`ConfigError` if the key is missing."""
        value = self.get(dotted, _MISSING)
        if value is _MISSING:
            raise ConfigError(f"Missing required config key '{dotted}' in {self.path}")
        return value

    def section(self, dotted: str) -> dict[str, Any]:
        """Return a *copy* of a whole sub-section as a dict (empty dict if missing)."""
        value = self.get(dotted, {})
        if not isinstance(value, dict):
            raise ConfigError(f"Config key '{dotted}' is not a section")
        return copy.deepcopy(value)

    @property
    def data(self) -> dict[str, Any]:
        """A deep copy of the raw configuration dictionary."""
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------- networking
    def host(self, device: str) -> str:
        """IP address of ``device`` (station/workstation/plc/agv).

        In simulation mode with ``network.sim_use_localhost`` every device is on
        ``127.0.0.1`` so the whole system can be demoed on one laptop.
        """
        if self.simulation and self.get("network.sim_use_localhost", True):
            return "127.0.0.1"
        return str(self.require(f"network.{device}.host"))

    def bind_address(self, device: str) -> str:
        """Interface a server for ``device`` should listen on."""
        if self.simulation and self.get("network.sim_use_localhost", True):
            return "127.0.0.1"
        return str(self.get(f"network.{device}.bind", "0.0.0.0"))

    def port(self, dotted: str) -> int:
        """Port at ``dotted``; in simulation ``<dotted>_sim`` wins if present
        (e.g. Modbus 502 needs admin rights on a laptop, so sim uses 5020)."""
        if self.simulation:
            sim = self.get(dotted + "_sim")
            if sim is not None:
                return int(sim)
        return int(self.require(dotted))

    # ------------------------------------------------------------------ paths
    def path_of(self, dotted: str) -> Path:
        """Resolve a path value relative to the repository root."""
        p = Path(str(self.require(dotted))).expanduser()
        return p if p.is_absolute() else (REPO_ROOT / p)


def load_config(path: str | os.PathLike[str] | None = None, simulation: bool | None = None) -> Config:
    """Load ``config.yaml`` and apply environment overrides.

    Args:
        path: optional explicit path to a YAML config file.
        simulation: force simulation mode on/off (``None`` = use config/env).
    """
    cfg_path = Path(path or os.environ.get("APEX_CONFIG") or (REPO_ROOT / "config.yaml")).expanduser()
    if not cfg_path.is_file():
        raise ConfigError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Top level of {cfg_path} must be a mapping")

    for env, (sec, key) in _ENV_SECRETS.items():
        if os.environ.get(env):
            data.setdefault(sec, {})[key] = os.environ[env]

    cfg = Config(data, cfg_path, simulation)
    for env_key in ("security.api_key", "security.agv_token"):
        if str(cfg.get(env_key, "")).startswith("apex-dev-") and not cfg.simulation:
            log.warning("Using the default development secret for %s - change it for deployment", env_key)
    return cfg

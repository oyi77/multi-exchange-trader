"""Unified configuration loader with YAML/env/CLI layering.

Priority order (last wins):
  1. config/default.yml  (base defaults)
  2. config/profiles/<mode>.yml  (profile overrides)
  3. Environment variables  (system overrides)
  4. CLI arguments  (highest priority)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TradingConfig:
    mode: str = "paper"
    symbol: str = "BTC/USDT"
    lot: float = 0.01
    leverage: int = 1
    max_positions: int = 2


@dataclass
class CEXConfig:
    name: str = ""
    exness_token: str = ""
    api_key: str = ""
    api_secret: str = ""


@dataclass
class DEXConfig:
    router: str = ""
    rpc_url: str = ""


@dataclass
class ExchangeConfig:
    provider: str = ""
    chain: str = ""
    api_key: str = ""
    api_secret: str = ""
    rpc_url: str = ""
    cex: CEXConfig = field(default_factory=CEXConfig)
    dex: DEXConfig = field(default_factory=DEXConfig)


@dataclass
class WalletConfig:
    key_source: str = "env"
    derivation_path: str = "m/44'/60'/0'/0/0"
    private_key: str = ""


@dataclass
class RiskConfig:
    max_drawdown_percent: float = 20.0
    max_position_size_percent: float = 5.0
    max_daily_loss_percent: float = 10.0
    circuit_breaker_enabled: bool = True
    circuit_breaker_cooldown_seconds: int = 300


@dataclass
class DataProvidersConfig:
    primary: str = "birdeye"
    birdeye_api_key: str = ""


@dataclass
class LoggingConfig:
    level: str = "info"
    file: str = "logs/trading_bot.log"
    format: str = "%(asctime)s|%(levelname)s|%(name)s|%(message)s"


@dataclass
class AppConfig:
    """Top-level application config combining all sections."""
    trading: TradingConfig = field(default_factory=TradingConfig)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data_providers: DataProvidersConfig = field(default_factory=DataProvidersConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Environment variable mappings (backward compatible)
# ---------------------------------------------------------------------------
# Maps env var name → dotted config path
ENV_MAP: Dict[str, List[str]] = {
    "EXNESS_TOKEN": ["exchange", "cex", "exness_token"],
    "EXCHANGE_API_KEY": ["exchange", "api_key"],
    "EXCHANGE_API_SECRET": ["exchange", "api_secret"],
    "OSTIUM_PRIVATE_KEY": ["wallet", "private_key"],
    "OSTIUM_RPC_URL": ["exchange", "rpc_url"],
    "BIRDEYE_API_KEY": ["data_providers", "birdeye_api_key"],
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when config loading or validation fails."""


class ConfigLoader:
    """Loads and merges configuration from YAML files, env vars, and CLI args.

    Usage::

        loader = ConfigLoader("config")
        cfg = loader.load(profile="paper")
        print(cfg.trading.mode)  # "paper"
    """

    def __init__(self, config_dir: str | Path = "config") -> None:
        self.config_dir = Path(config_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        profile: str = "paper",
        env_overrides: Optional[Dict[str, str]] = None,
        cli_overrides: Optional[Dict[str, Any]] = None,
    ) -> AppConfig:
        """Load and merge config from all sources.

        Args:
            profile: Profile name (paper, frontest, real).
            env_overrides: Pre-load env dict (for testing). None = read from os.environ.
            cli_overrides: CLI argument overrides.

        Returns:
            Fully resolved AppConfig.
        """
        raw: Dict[str, Any] = {}

        # 1. Base defaults
        raw = self._deep_merge(raw, self._load_yaml(self.config_dir / "default.yml"))

        # 2. Profile overrides
        profile_path = self.config_dir / "profiles" / f"{profile}.yml"
        if profile_path.exists():
            raw = self._deep_merge(raw, self._load_yaml(profile_path))

        # 3. Environment variables
        raw = self._deep_merge(raw, self._load_env(env_overrides or os.environ))

        # 4. CLI overrides
        if cli_overrides:
            raw = self._deep_merge(raw, cli_overrides)

        # 5. Validate and convert to dataclass
        self._validate(raw)
        return self._to_dataclass(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        """Load and parse a YAML file."""
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        try:
            with open(path, "r") as fh:
                data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    def _load_env(self, source: Dict[str, str]) -> Dict[str, Any]:
        """Apply known env var mappings to a nested dict."""
        result: Dict[str, Any] = {}
        for env_key, path_parts in ENV_MAP.items():
            if env_key in source and source[env_key]:
                self._set_nested(result, path_parts, source[env_key])
        return result

    @staticmethod
    def _set_nested(root: Dict[str, Any], path: List[str], value: Any) -> None:
        """Set a value in a nested dict via dotted path."""
        current = root
        for part in path[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[path[-1]] = value

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Deep-merge *override* into *base* (mutates base)."""
        for key, val in override.items():
            if isinstance(val, dict) and key in base and isinstance(base[key], dict):
                ConfigLoader._deep_merge(base[key], val)
            else:
                base[key] = val
        return base

    def _validate(self, raw: Dict[str, Any]) -> None:
        """Validate required sections and types."""
        known_sections = {"trading", "exchange", "wallet", "risk", "data_providers", "logging"}
        unknown = set(raw.keys()) - known_sections
        if unknown:
            raise ConfigError(f"unknown section: {', '.join(sorted(unknown))}")

        # Validate trading.mode
        trading = raw.get("trading", {})
        if "mode" in trading and trading["mode"] not in ("paper", "frontest", "real"):
            raise ConfigError(
                f"Invalid trading.mode '{trading['mode']}'. Use: paper, frontest, real"
            )

    def _to_dataclass(self, raw: Dict[str, Any]) -> AppConfig:
        """Convert raw nested dict to typed AppConfig dataclass tree."""
        return AppConfig(
            trading=TradingConfig(**raw.get("trading", {})),
            exchange=self._build_exchange(raw.get("exchange", {})),
            wallet=WalletConfig(**raw.get("wallet", {})),
            risk=RiskConfig(**raw.get("risk", {})),
            data_providers=DataProvidersConfig(**raw.get("data_providers", {})),
            logging=LoggingConfig(**raw.get("logging", {})),
        )

    @staticmethod
    def _build_exchange(data: Dict[str, Any]) -> ExchangeConfig:
        """Build nested ExchangeConfig handling sub-configs."""
        cex_data = data.pop("cex", {})
        dex_data = data.pop("dex", {})
        cfg = ExchangeConfig(**data)
        cfg.cex = CEXConfig(**cex_data)
        cfg.dex = DEXConfig(**dex_data)
        return cfg


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def load_config(
    profile: str = "paper",
    config_dir: str | Path = "config",
) -> AppConfig:
    """One-shot config loader for simple use cases."""
    return ConfigLoader(config_dir).load(profile)

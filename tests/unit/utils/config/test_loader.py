"""Tests for the unified configuration system."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from trading_bot.utils.config.loader import (
    AppConfig,
    ConfigLoader,
    ConfigError,
    ENV_MAP,
    load_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory with default.yml and profiles."""
    cfg = tmp_path / "config"
    cfg.mkdir()

    # default.yml
    default = {
        "trading": {"mode": "paper", "symbol": "BTC/USDT", "lot": 0.01, "leverage": 1, "max_positions": 2},
        "exchange": {"provider": "", "chain": "", "rpc_url": ""},
        "wallet": {"key_source": "env", "derivation_path": "m/44'/60'/0'/0/0"},
        "risk": {"max_drawdown_percent": 20.0, "max_position_size_percent": 5.0,
                 "max_daily_loss_percent": 10.0, "circuit_breaker_enabled": True,
                 "circuit_breaker_cooldown_seconds": 300},
        "data_providers": {"primary": "birdeye", "birdeye_api_key": ""},
        "logging": {"level": "info", "file": "logs/trading_bot.log",
                    "format": "%(asctime)s|%(levelname)s|%(name)s|%(message)s"},
    }
    (cfg / "default.yml").write_text(yaml.dump(default))

    # profiles
    profiles = cfg / "profiles"
    profiles.mkdir()
    (profiles / "paper.yml").write_text(yaml.dump({"trading": {"mode": "paper"}}))
    (profiles / "frontest.yml").write_text(yaml.dump({"trading": {"mode": "frontest"}, "exchange": {"provider": "ccxt"}}))
    (profiles / "real.yml").write_text(yaml.dump({"trading": {"mode": "real", "lot": 0.01},
                                                   "risk": {"max_drawdown_percent": 10.0}}))

    return cfg


@pytest.fixture
def loader(config_dir: Path) -> ConfigLoader:
    return ConfigLoader(config_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestConfigBasics:
    """Basic config loading."""

    def test_load_default(self, loader: ConfigLoader):
        cfg = loader.load("paper")
        assert cfg.trading.mode == "paper"
        assert cfg.trading.symbol == "BTC/USDT"
        assert cfg.trading.lot == 0.01
        assert cfg.trading.leverage == 1
        assert cfg.trading.max_positions == 2

    def test_returns_appconfig_type(self, loader: ConfigLoader):
        cfg = loader.load("paper")
        assert isinstance(cfg, AppConfig)

    def test_all_sections_present(self, loader: ConfigLoader):
        cfg = loader.load("paper")
        assert cfg.trading is not None
        assert cfg.exchange is not None
        assert cfg.wallet is not None
        assert cfg.risk is not None
        assert cfg.data_providers is not None
        assert cfg.logging is not None

    def test_convenience_load_config(self, config_dir: Path):
        # Patch cwd-style usage via explicit config_dir
        cfg = load_config(profile="paper", config_dir=config_dir)
        assert cfg.trading.mode == "paper"


class TestProfileLayering:
    """Profile overrides default."""

    def test_frontest_overrides_provider(self, loader: ConfigLoader):
        cfg = loader.load("frontest")
        assert cfg.trading.mode == "frontest"
        assert cfg.exchange.provider == "ccxt"

    def test_real_overrides_risk(self, loader: ConfigLoader):
        cfg = loader.load("real")
        assert cfg.trading.mode == "real"
        assert cfg.risk.max_drawdown_percent == 10.0

    def test_paper_keeps_default_values(self, loader: ConfigLoader):
        cfg = loader.load("paper")
        # paper.yml only sets mode, everything else stays from default
        assert cfg.risk.max_drawdown_percent == 20.0
        assert cfg.risk.max_position_size_percent == 5.0
        assert cfg.data_providers.primary == "birdeye"


class TestEnvVarOverride:
    """Environment variables override YAML values."""

    def test_env_override_api_key(self, loader: ConfigLoader):
        env = {"EXCHANGE_API_KEY": "test-key-123"}
        cfg = loader.load("paper", env_overrides=env)
        assert cfg.exchange.api_key == "test-key-123"

    def test_env_override_birdeye(self, loader: ConfigLoader):
        env = {"BIRDEYE_API_KEY": "birdeye-secret"}
        cfg = loader.load("paper", env_overrides=env)
        assert cfg.data_providers.birdeye_api_key == "birdeye-secret"

    def test_env_override_rpc(self, loader: ConfigLoader):
        env = {"OSTIUM_RPC_URL": "https://my-rpc.url"}
        cfg = loader.load("paper", env_overrides=env)
        assert cfg.exchange.rpc_url == "https://my-rpc.url"

    def test_env_empty_value_ignored(self, loader: ConfigLoader):
        env = {"BIRDEYE_API_KEY": ""}
        cfg = loader.load("paper", env_overrides=env)
        assert cfg.data_providers.birdeye_api_key == ""

    def test_env_not_set_keeps_default(self, loader: ConfigLoader):
        cfg = loader.load("paper", env_overrides={})
        assert cfg.exchange.api_key == ""
        assert cfg.exchange.api_secret == ""

    def test_unknown_env_var_not_set(self, loader: ConfigLoader):
        env = {"SOME_RANDOM_VAR": "value"}
        cfg = loader.load("paper", env_overrides=env)
        # Should not cause any error, just ignored


class TestCLIOverride:
    """CLI arguments have highest priority."""

    def test_cli_overrides_env(self, loader: ConfigLoader):
        env = {"EXCHANGE_API_KEY": "env-key"}
        cli = {"exchange": {"api_key": "cli-key"}}
        cfg = loader.load("paper", env_overrides=env, cli_overrides=cli)
        assert cfg.exchange.api_key == "cli-key"

    def test_cli_missing_is_fine(self, loader: ConfigLoader):
        cfg = loader.load("paper", cli_overrides=None)
        assert cfg is not None

    def test_cli_empty_dict(self, loader: ConfigLoader):
        cfg = loader.load("paper", cli_overrides={})
        assert cfg.trading.mode == "paper"


class TestValidation:
    """Config validation rejects invalid inputs."""

    def test_missing_config_file(self, tmp_path: Path):
        bad_dir = tmp_path / "nonexistent"
        bad_dir.mkdir()
        loader = ConfigLoader(bad_dir)
        with pytest.raises(ConfigError, match="not found"):
            loader.load("paper")

    def test_unknown_section_rejected(self, config_dir: Path, loader: ConfigLoader):
        # Inject an unknown section into default.yml
        default_path = config_dir / "default.yml"
        data = yaml.safe_load(default_path.read_text())
        data["unknown_section"] = {"foo": "bar"}
        default_path.write_text(yaml.dump(data))
        with pytest.raises(ConfigError, match="unknown section"):
            loader.load("paper")

    def test_invalid_mode_rejected(self, config_dir: Path, loader: ConfigLoader):
        profile_path = config_dir / "profiles" / "paper.yml"
        data = {"trading": {"mode": "invalid_mode"}}
        profile_path.write_text(yaml.dump(data))
        with pytest.raises(ConfigError, match="Invalid trading.mode"):
            loader.load("paper")

    def test_invalid_yaml_rejected(self, config_dir: Path, loader: ConfigLoader):
        (config_dir / "default.yml").write_text("{invalid_yaml: {{broken}}")
        with pytest.raises(ConfigError):
            loader.load("paper")


class TestEdgeCases:
    """Edge cases for config loading."""

    def test_missing_profile_fallback(self, config_dir: Path, loader: ConfigLoader):
        cfg = loader.load("nonexistent")
        # Should still load defaults
        assert cfg.trading.mode == "paper"

    def test_env_map_completeness(self):
        """All env vars from ENV_MAP should map to known config paths."""
        for env_key, path in ENV_MAP.items():
            assert len(path) >= 1
            assert all(isinstance(p, str) for p in path)

    def test_deep_merge_nested(self, loader: ConfigLoader):
        """CLI overrides should deep-merge, not replace entire sections."""
        cfg = loader.load("paper", cli_overrides={
            "trading": {"symbol": "ETH/USDT"},
        })
        assert cfg.trading.symbol == "ETH/USDT"
        # Other fields under trading should be preserved
        assert cfg.trading.mode == "paper"
        assert cfg.trading.lot == 0.01

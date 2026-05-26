"""Tests for ABI loader and management."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import pytest

from trading_bot.utils.abi.loader import ABILoader, ABILoaderError, ABILoadError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"name": "from", "type": "address", "indexed": True},
            {"name": "to", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False},
        ],
    },
    {
        "type": "constructor",
        "inputs": [],
        "stateMutability": "nonpayable",
    },
]


@pytest.fixture
def abi_dir(tmp_path: Path) -> Path:
    """Create a temporary ABI directory with a valid BSC contract."""
    bsc_dir = tmp_path / "bsc"
    bsc_dir.mkdir()
    abi_file = bsc_dir / "erc20.json"
    abi_file.write_text(json.dumps(SAMPLE_ABI))
    return tmp_path


@pytest.fixture
def loader(abi_dir: Path) -> ABILoader:
    """ABILoader pointed at the temp directory."""
    return ABILoader(abi_dir=str(abi_dir))


# ---------------------------------------------------------------------------
# test_load_from_file
# ---------------------------------------------------------------------------
class TestLoadFromFile:
    """Load and parse valid ABI JSON from the filesystem."""

    def test_load_returns_list_of_dicts(self, loader: ABILoader):
        abi = loader.load("erc20", chain="bsc")
        assert isinstance(abi, list)
        assert len(abi) == len(SAMPLE_ABI)
        assert all(isinstance(entry, dict) for entry in abi)

    def test_load_preserves_abi_content(self, loader: ABILoader):
        abi = loader.load("erc20", chain="bsc")
        assert abi[0]["name"] == "balanceOf"
        assert abi[0]["type"] == "function"

    def test_load_default_chain_is_bsc(self, abi_dir: Path):
        """Default chain parameter is 'bsc'."""
        ldr = ABILoader(abi_dir=str(abi_dir))
        abi = ldr.load("erc20")  # no chain arg
        assert len(abi) == len(SAMPLE_ABI)

    def test_load_multi_chain(self, abi_dir: Path):
        """Each chain has its own directory."""
        eth_dir = abi_dir / "eth"
        eth_dir.mkdir()
        eth_abi = [{"type": "function", "name": "transfer",
                     "inputs": [], "outputs": []}]
        (eth_dir / "erc20.json").write_text(json.dumps(eth_abi))

        ldr = ABILoader(abi_dir=str(abi_dir))
        bsc_abi = ldr.load("erc20", chain="bsc")
        eth_result = ldr.load("erc20", chain="eth")
        assert bsc_abi != eth_result
        assert eth_result[0]["name"] == "transfer"


# ---------------------------------------------------------------------------
# test_cache_hit
# ---------------------------------------------------------------------------
class TestCacheHit:
    """Second load returns from in-memory cache (faster, same object)."""

    def test_cache_returns_same_object(self, loader: ABILoader):
        first = loader.load("erc20", chain="bsc")
        second = loader.load("erc20", chain="bsc")
        assert first is second  # exact same reference → cache hit

    def test_cache_is_faster(self, loader: ABILoader):
        # Prime the cache
        loader.load("erc20", chain="bsc")
        start = time.perf_counter_ns()
        loader.load("erc20", chain="bsc")
        cached_ns = time.perf_counter_ns() - start
        # Cached lookup should be sub-millisecond (< 1_000_000 ns)
        assert cached_ns < 1_000_000, f"Cache lookup took {cached_ns}ns"

    def test_clear_cache(self, loader: ABILoader):
        first = loader.load("erc20", chain="bsc")
        loader.clear_cache()
        second = loader.load("erc20", chain="bsc")
        assert first is not second  # new object after cache clear
        assert first == second  # but same content

    def test_cache_key_separates_chains(self, abi_dir: Path):
        """Different chains produce different cache entries."""
        eth_dir = abi_dir / "eth"
        eth_dir.mkdir()
        (eth_dir / "erc20.json").write_text(
            json.dumps([{"type": "function", "name": "x", "inputs": [], "outputs": []}])
        )
        ldr = ABILoader(abi_dir=str(abi_dir))
        bsc = ldr.load("erc20", chain="bsc")
        eth = ldr.load("erc20", chain="eth")
        assert bsc is not eth


# ---------------------------------------------------------------------------
# test_invalid_abi
# ---------------------------------------------------------------------------
class TestInvalidABI:
    """Malformed or structurally invalid ABI files are rejected."""

    def test_not_a_list(self, abi_dir: Path):
        (abi_dir / "bsc" / "bad.json").write_text('{"type": "function"}')
        ldr = ABILoader(abi_dir=str(abi_dir))
        with pytest.raises(ABILoadError, match="must be a JSON array"):
            ldr.load("bad", chain="bsc")

    def test_entry_missing_type(self, abi_dir: Path):
        bad = [{"name": "foo", "inputs": []}]
        (abi_dir / "bsc" / "notype.json").write_text(json.dumps(bad))
        ldr = ABILoader(abi_dir=str(abi_dir))
        with pytest.raises(ABILoadError, match="missing.*type"):
            ldr.load("notype", chain="bsc")

    def test_invalid_json(self, abi_dir: Path):
        (abi_dir / "bsc" / "corrupt.json").write_text("{not json!!")
        ldr = ABILoader(abi_dir=str(abi_dir))
        with pytest.raises(ABILoadError, match="parse"):
            ldr.load("corrupt", chain="bsc")

    def test_empty_array(self, abi_dir: Path):
        (abi_dir / "bsc" / "empty.json").write_text("[]")
        ldr = ABILoader(abi_dir=str(abi_dir))
        with pytest.raises(ABILoadError, match="empty"):
            ldr.load("empty", chain="bsc")

    def test_invalid_entry_type_value(self, abi_dir: Path):
        bad = [{"type": "banana", "name": "x"}]
        (abi_dir / "bsc" / "badtype.json").write_text(json.dumps(bad))
        ldr = ABILoader(abi_dir=str(abi_dir))
        with pytest.raises(ABILoadError, match="invalid.*type"):
            ldr.load("badtype", chain="bsc")


# ---------------------------------------------------------------------------
# test_contract_not_found
# ---------------------------------------------------------------------------
class TestContractNotFound:
    """Missing ABI files raise clear errors."""

    def test_missing_contract(self, loader: ABILoader):
        with pytest.raises(ABILoadError, match="not found"):
            loader.load("nonexistent", chain="bsc")

    def test_missing_chain_dir(self, loader: ABILoader):
        with pytest.raises(ABILoadError, match="not found"):
            loader.load("erc20", chain="avalanche")

    def test_error_includes_path(self, loader: ABILoader):
        """Error message should mention the expected path."""
        with pytest.raises(ABILoadError) as exc_info:
            loader.load("missing", chain="bsc")
        assert "missing.json" in str(exc_info.value)


# ---------------------------------------------------------------------------
# test_load_from_explorer
# ---------------------------------------------------------------------------
class TestLoadFromExplorer:
    """Fetch ABI from block explorer API (mocked network)."""

    EXPLORER_RESPONSE = {
        "status": "1",
        "message": "OK",
        "result": json.dumps(SAMPLE_ABI),
    }

    def _mock_urlopen(self, data: dict):
        """Helper: return a :func:`urlopen` mock that yields *data* as JSON."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode()
        mock_resp.__enter__.return_value = mock_resp  # context manager → self
        return patch("trading_bot.utils.abi.loader.urlopen", return_value=mock_resp)

    def test_bscscan_url(self, loader: ABILoader):
        with self._mock_urlopen(self.EXPLORER_RESPONSE) as mock_urlopen:
            abi = loader.load_from_explorer(
                "0x1234567890abcdef1234567890abcdef12345678",
                chain="bsc",
            )
            req = mock_urlopen.call_args[0][0]
            assert "api.bscscan.com" in req.full_url
            assert len(abi) == len(SAMPLE_ABI)

    def test_etherscan_url(self, loader: ABILoader):
        with self._mock_urlopen(self.EXPLORER_RESPONSE) as mock_urlopen:
            loader.load_from_explorer("0xdead", chain="ethereum")
            req = mock_urlopen.call_args[0][0]
            assert "api.etherscan.io" in req.full_url

    def test_polygonscan_url(self, loader: ABILoader):
        with self._mock_urlopen(self.EXPLORER_RESPONSE) as mock_urlopen:
            loader.load_from_explorer("0xdead", chain="polygon")
            req = mock_urlopen.call_args[0][0]
            assert "api.polygonscan.com" in req.full_url

    def test_arbiscan_url(self, loader: ABILoader):
        with self._mock_urlopen(self.EXPLORER_RESPONSE) as mock_urlopen:
            loader.load_from_explorer("0xdead", chain="arbitrum")
            req = mock_urlopen.call_args[0][0]
            assert "api.arbiscan.io" in req.full_url

    def test_explorer_error_status(self, loader: ABILoader):
        error_response = {
            "status": "0",
            "message": "NOTOK",
            "result": "Contract source code not verified",
        }
        with self._mock_urlopen(error_response):
            with pytest.raises(ABILoadError, match="[Ee]xplorer"):
                loader.load_from_explorer("0xdead", chain="bsc")

    def test_explorer_http_error(self, loader: ABILoader):
        with patch("trading_bot.utils.abi.loader.urlopen",
                    side_effect=URLError("Server Error")):
            with pytest.raises(ABILoadError):
                loader.load_from_explorer("0xdead", chain="bsc")

    def test_unsupported_chain(self, loader: ABILoader):
        with pytest.raises(ABILoadError, match="[Uu]nsupported"):
            loader.load_from_explorer("0xdead", chain="solana")

    def test_explorer_result_cached(self, loader: ABILoader):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(self.EXPLORER_RESPONSE).encode()
        mock_resp.__enter__.return_value = mock_resp

        with patch("trading_bot.utils.abi.loader.urlopen",
                    return_value=mock_resp) as mock_urlopen:
            a = loader.load_from_explorer("0xdead", chain="bsc")
            b = loader.load_from_explorer("0xdead", chain="bsc")
            # Only one HTTP call — second is from cache
            assert mock_urlopen.call_count == 1
            assert a is b


# ---------------------------------------------------------------------------
# test_validate_abi (public surface of _validate_abi)
# ---------------------------------------------------------------------------
class TestValidateABI:
    """Structural validation of ABI arrays."""

    def test_valid_abi(self, loader: ABILoader):
        assert loader._validate_abi(SAMPLE_ABI) is True

    def test_not_a_list(self, loader: ABILoader):
        assert loader._validate_abi({"type": "function"}) is False  # type: ignore[arg-type]

    def test_entry_without_type(self, loader: ABILoader):
        assert loader._validate_abi([{"name": "x"}]) is False

    def test_empty_list(self, loader: ABILoader):
        assert loader._validate_abi([]) is False

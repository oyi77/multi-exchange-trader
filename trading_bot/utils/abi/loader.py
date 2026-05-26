"""ABI loader with file caching and block-explorer fallback.

Provides a unified interface for loading contract ABIs from local JSON files
with fallback fetching from blockchain explorers (BscScan, Etherscan, etc.).
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError


# ---------------------------------------------------------------------------
# Explorer API endpoints
# ---------------------------------------------------------------------------

EXPLORER_API: Dict[str, str] = {
    "bsc": "https://api.bscscan.com/api",
    "ethereum": "https://api.etherscan.io/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
}


class ABILoaderError(Exception):
    """Raised when ABI loading or validation fails."""


# Alias for test compatibility
ABILoadError = ABILoaderError


class ABIValidationError(ABILoaderError):
    """Raised when ABI has invalid structure."""


class ABIAppendable:
    """Descriptor-style validator stub — reserved for future schema checks."""


class ABILoader:
    """Loads contract ABIs from file or explorer, with in-memory caching.

    Usage::

        loader = ABILoader("exchange/dex/abi/contracts")
        abi = loader.load("pancakeswap_router", "bsc")
        abi2 = loader.load("pancakeswap_router", "bsc")  # cache hit
    """

    def __init__(self, abi_dir: str | Path = "exchange/dex/abi/contracts") -> None:
        self._abi_dir = Path(abi_dir)
        # Internal cache: key = f"{chain}:{name}"
        self._cache: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, contract_name: str, chain: str = "bsc") -> List[Dict[str, Any]]:
        """Load ABI from local JSON file.

        Args:
            contract_name: Contract identifier (e.g. ``pancakeswap_router``).
            chain: Chain sub-directory (bsc, ethereum, polygon, arbitrum).

        Returns:
            Parsed ABI as a list of function/event definitions.
        """
        cache_key = f"{chain}:{contract_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        filepath = self._abi_dir / chain / f"{contract_name}.json"
        abi = self._load_from_file(filepath)
        if not self._validate_abi(abi):
            # Provide specific error message based on the failure
            if not isinstance(abi, list):
                raise ABILoaderError(f"ABI must be a JSON array, got {type(abi).__name__}")
            if not abi:
                raise ABILoaderError("ABI must not be an empty array")
            # Check each entry
            for idx, entry in enumerate(abi):
                if not isinstance(entry, dict):
                    raise ABILoaderError(
                        f"ABI entry #{idx} is not a dict"
                    )
                if "type" not in entry:
                    raise ABILoaderError(
                        f"ABI entry #{idx} missing 'type' field: {entry.get('name', 'unnamed')}"
                    )
                if entry["type"] not in ("function", "event", "constructor", "receive", "fallback"):
                    raise ABILoaderError(
                        f"ABI entry #{idx} has invalid type '{entry['type']}'"
                    )
            raise ABILoaderError(f"Invalid ABI in {filepath}")
        self._cache[cache_key] = abi
        return abi

    def load_from_explorer(
        self,
        address: str,
        chain: str = "bsc",
        api_key: str = "",
    ) -> List[Dict[str, Any]]:
        """Fetch ABI from block explorer API.

        Note: Explorer APIs typically have rate limits (1-5 req/s).
        Results are cached in memory by address + chain.
        """
        cache_key = f"explorer:{chain}:{address}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        base_url = EXPLORER_API.get(chain)
        if not base_url:
            raise ABILoaderError(f"Unsupported chain for explorer fetch: {chain}")

        params = f"?module=contract&action=getabi&address={address}"
        if api_key:
            params += f"&apikey={api_key}"
        url = base_url + params

        try:
            req = Request(url, headers={"User-Agent": "ABILoader/1.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (URLError, json.JSONDecodeError, OSError) as exc:
            raise ABILoaderError(f"Explorer fetch failed for {address}: {exc}") from exc

        if data.get("status") != "1":
            raise ABILoaderError(
                f"Explorer returned error for {address}: {data.get('result', 'unknown')}"
            )

        raw_abi = data["result"]
        if isinstance(raw_abi, str):
            # Some explorers return the ABI as an escaped JSON string
            raw_abi = json.loads(raw_abi)

        if not self._validate_abi(raw_abi):
            raise ABILoaderError(f"Invalid ABI fetched from explorer for {address}")
        self._cache[cache_key] = raw_abi
        return raw_abi

    def clear_cache(self) -> None:
        """Clear the in-memory ABI cache."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_from_file(self, path: Path) -> List[Dict[str, Any]]:
        """Read and parse ABI from a JSON file."""
        if not path.exists():
            raise ABILoaderError(f"ABI file not found: {path}")
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise ABILoaderError(f"Failed to parse ABI from {path}: {exc}") from exc
        return data

    _VALID_ABI_TYPES = (
        "function", "event", "constructor", "receive", "fallback"
    )

    def _validate_abi(self, abi: Any) -> bool:
        """Validate that *abi* is a non-empty list of dicts with standard fields.

        Returns:
            True when valid, False otherwise.
        """
        if not isinstance(abi, list):
            return False

        if not abi:
            return False

        for idx, entry in enumerate(abi):
            if not isinstance(entry, dict):
                return False
            if "type" not in entry:
                return False
            if entry["type"] not in self._VALID_ABI_TYPES:
                return False

        return True


class ABICache:
    """Simple persistent TTL cache for ABIs, backed by a JSON file."""

    def __init__(self, cache_path: str | Path = ".abi_cache.json", ttl: int = 3600):
        self._cache_path = Path(cache_path)
        self._ttl = ttl
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self._cache_path.exists():
            try:
                return json.loads(self._cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self._cache_path.write_text(json.dumps(self._data, indent=2))

    def get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        entry = self._data.get(key)
        if entry and (time.time() - entry.get("ts", 0)) < self._ttl:
            return entry["abi"]
        return None

    def set(self, key: str, abi: List[Dict[str, Any]]) -> None:
        self._data[key] = {"abi": abi, "ts": time.time()}
        self._save()

"""Tests for the wallet manager and key storage.

Uses mocks for all Web3/blockchain interactions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_bot.utils.wallet.manager import WalletManager
from trading_bot.utils.wallet.storage import KeyStorage as EncryptedKeyStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_keyfile(tmp_path: Path) -> Path:
    return tmp_path / "wallet.json"


@pytest.fixture
def storage(temp_keyfile: Path) -> EncryptedKeyStorage:
    return EncryptedKeyStorage(temp_keyfile)


@pytest.fixture
def manager() -> WalletManager:
    return WalletManager()


# ---------------------------------------------------------------------------
# Wallet Manager
# ---------------------------------------------------------------------------

class TestWalletManager:
    """Tests for WalletManager (key generation, derivation, signing)."""

    def test_create_wallet(self, manager: WalletManager):
        wallet = manager.create_wallet()
        assert "address" in wallet
        assert "private_key" in wallet
        assert wallet["private_key"].startswith("0x")
        assert len(wallet["private_key"]) == 66  # 0x + 64 hex chars

    def test_create_wallet_unique(self, manager: WalletManager):
        w1 = manager.create_wallet()
        w2 = manager.create_wallet()
        assert w1["address"] != w2["address"]
        assert w1["private_key"] != w2["private_key"]

    def test_import_valid_key(self, manager: WalletManager):
        wallet = manager.create_wallet()
        imported = manager.import_key(wallet["private_key"])
        assert imported["address"] == wallet["address"]

    def test_import_invalid_key_raises(self, manager: WalletManager):
        with pytest.raises(ValueError, match="Invalid private key"):
            manager.import_key("0xdeadbeef")

    def test_import_empty_key_raises(self, manager: WalletManager):
        with pytest.raises(ValueError):
            manager.import_key("")

    def test_get_address_returns_checksummed(self, manager: WalletManager):
        wallet = manager.create_wallet()
        addr = manager.get_address(wallet["private_key"])
        assert addr == wallet["address"]

    def test_sign_transaction(self, manager: WalletManager):
        wallet = manager.create_wallet()
        tx = {"to": "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
              "value": 1000, "gas": 21000, "gasPrice": 20000000000,
              "nonce": 0, "chainId": 56}
        signed = manager.sign_transaction(tx, wallet["private_key"])
        assert "raw_transaction" in signed or "rawTx" in signed or isinstance(signed, dict)
        assert signed is not None

    def test_derive_same_address_from_key(self, manager: WalletManager):
        """Same key always derives same address."""
        wallet = manager.create_wallet()
        addr1 = manager.get_address(wallet["private_key"])
        addr2 = manager.get_address(wallet["private_key"])
        assert addr1 == addr2


# ---------------------------------------------------------------------------
# Encrypted Key Storage
# ---------------------------------------------------------------------------

class TestEncryptedKeyStorage:
    """Tests for encrypted key file storage."""

    def test_store_and_load(self, storage: EncryptedKeyStorage):
        password = "test-password-123"
        keys = {"bsc": "0xabcd" + "ef01" * 15, "eth": "0x1234" + "5678" * 15}
        storage.store(keys, password)
        assert storage._path.exists()

    def test_store_and_load_roundtrip(self, storage: EncryptedKeyStorage,
                                      temp_keyfile: Path):
        password = "test-password-123"
        keys = {"bsc": "0xabcd" + "ef01" * 15}
        storage.store(keys, password)

        loaded = storage.load(password)
        assert loaded["bsc"] == keys["bsc"]

    def test_wrong_password_raises(self, storage: EncryptedKeyStorage):
        password = "correct-password"
        keys = {"bsc": "0xabcd" + "ef01" * 15}
        storage.store(keys, password)

        with pytest.raises(Exception):
            storage.load("wrong-password")

    def test_storage_file_is_encrypted(self, storage: EncryptedKeyStorage,
                                       temp_keyfile: Path):
        password = "test-password"
        keys = {"bsc": "0xabcd" + "ef01" * 15}
        storage.store(keys, password)

        raw = temp_keyfile.read_text()
        # File should not contain plaintext key
        assert "abcd" not in raw or "ef01" not in raw
        # Should be JSON with encrypted fields
        data = json.loads(raw)
        assert "encrypted" in data or "ciphertext" in data or "data" in data

    def test_missing_file_raises(self, storage: EncryptedKeyStorage,
                                 temp_keyfile: Path):
        if temp_keyfile.exists():
            temp_keyfile.unlink()
        with pytest.raises(FileNotFoundError):
            storage.load("any-password")

    def test_store_multiple_chains(self, storage: EncryptedKeyStorage):
        password = "multi-chain-pw"
        keys = {
            "bsc": "0x" + "a" * 64,
            "ethereum": "0x" + "b" * 64,
            "polygon": "0x" + "c" * 64,
            "arbitrum": "0x" + "d" * 64,
        }
        storage.store(keys, password)
        loaded = storage.load(password)
        assert loaded["bsc"] == keys["bsc"]
        assert loaded["ethereum"] == keys["ethereum"]
        assert len(loaded) == 4


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class TestWalletIntegration:
    """WalletManager + EncryptedKeyStorage integration."""

    def test_create_and_store(self, manager: WalletManager, temp_keyfile: Path):
        storage = EncryptedKeyStorage(temp_keyfile)
        wallet = manager.create_wallet()
        password = "integ-test-456"

        keys = {"default": wallet["private_key"]}
        storage.store(keys, password)

        loaded = storage.load(password)
        assert loaded["default"] == wallet["private_key"]

    def test_create_import_cycle(self, manager: WalletManager):
        """Create wallet, export, import back, verify same address."""
        original = manager.create_wallet()
        imported = manager.import_key(original["private_key"])
        assert imported["address"] == original["address"]

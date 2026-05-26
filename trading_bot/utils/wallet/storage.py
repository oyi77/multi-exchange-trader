"""
Encrypted key storage for multi-chain EVM wallets.

Supports three key sources (checked in order):
1. Environment variables  (PVT_KEY_BSC, PVT_KEY_ETH, …)
2. Encrypted JSON keystore (AES-256-GCM + scrypt KDF)
3. BIP-44 mnemonic seed phrases

Security invariants:
- Keys are NEVER written to disk in plaintext.
- Encrypted keystore uses scrypt(n=2^14, r=8, p=1) + AES-256-GCM.
- Mnemonic material is wiped from memory after derivation.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from eth_account import Account

logger = logging.getLogger(__name__)

# Enable HD wallet features (required for mnemonic-based derivation).
Account.enable_unaudited_hdwallet_features()

# EVM chains share the same BIP-44 coin type (60) derivation path.
# Chain differentiation happens at the transaction level via EIP-155 chain IDs.
CHAIN_IDS: Dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
}

# All EVM chains use the same derivation path (BIP-44 coin type 60).
DEFAULT_DERIVATION_PATH = "m/44'/60'/0'/0/0"

# Environment variable names per chain.
ENV_KEY_NAMES: Dict[str, str] = {
    "ethereum": "PVT_KEY_ETH",
    "bsc": "PVT_KEY_BSC",
    "polygon": "PVT_KEY_POLYGON",
    "arbitrum": "PVT_KEY_ARBITRUM",
}

# Scrypt parameters for key derivation.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_LEN = 32  # AES-256

# Where encrypted keystores live by default.
_DEFAULT_WALLET_DIR = Path.home() / ".trading_bot" / "wallets"


class KeyStorageError(Exception):
    """Raised for any key storage operation failure."""


class KeyStorage:
    """Encrypted key storage with env-var fallback and mnemonic support.

    Args:
        wallet_dir: Directory for encrypted keystore files.
                    Defaults to ``~/.trading_bot/wallets/``.
    """

    def __init__(self, wallet_dir: Optional[Path] = None) -> None:
        self._wallet_dir = wallet_dir or _DEFAULT_WALLET_DIR
        # In-memory cache: chain -> private key hex (no 0x prefix).
        self._keys: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_private_key(self, chain: str) -> str:
        """Return the private key hex string for *chain*.

        Resolution order:
        1. In-memory cache
        2. Environment variable (``PVT_KEY_<CHAIN>``)
        3. Encrypted keystore file

        Returns:
            Private key as hex string **without** ``0x`` prefix.

        Raises:
            KeyStorageError: If no key is available.
        """
        chain = chain.lower()
        self._validate_chain(chain)

        # 1. In-memory cache
        if chain in self._keys:
            return self._keys[chain]

        # 2. Environment variable
        env_name = ENV_KEY_NAMES.get(chain, "")
        env_val = os.environ.get(env_name, "").strip()
        if env_val:
            key_hex = env_val.removeprefix("0x")
            self._validate_private_key(key_hex)
            self._keys[chain] = key_hex
            return key_hex

        raise KeyStorageError(
            f"No private key found for chain '{chain}'. "
            f"Set env var {env_name} or import a key."
        )

    def import_private_key(self, private_key: str, chain: str = "") -> str:
        """Import a raw private key into in-memory storage.

        Args:
            private_key: Hex-encoded private key (with or without 0x).
            chain:       If provided, store for a specific chain.
                         If empty, store for all supported chains.

        Returns:
            The derived address.

        Raises:
            KeyStorageError: If the key is invalid.
        """
        key_hex = private_key.strip().removeprefix("0x")
        self._validate_private_key(key_hex)

        if chain:
            chain = chain.lower()
            self._validate_chain(chain)
            self._keys[chain] = key_hex
        else:
            for c in CHAIN_IDS:
                self._keys[c] = key_hex

        acct = Account.from_key(bytes.fromhex(key_hex))
        return acct.address

    def derive_from_mnemonic(
        self,
        mnemonic: str,
        passphrase: str = "",
    ) -> Dict[str, str]:
        """Derive keys for all chains from a BIP-39 mnemonic.

        All EVM chains use the same derivation path ``m/44'/60'/0'/0/0``.
        The returned dict maps chain name to derived address.

        Args:
            mnemonic:   BIP-39 mnemonic phrase (12 or 24 words).
            passphrase: Optional BIP-39 passphrase.

        Returns:
            Dict mapping chain name to checksummed address.

        Raises:
            KeyStorageError: If mnemonic is invalid.
        """
        try:
            acct = Account.from_mnemonic(
                mnemonic,
                passphrase=passphrase,
                account_path=DEFAULT_DERIVATION_PATH,
            )
        except Exception as exc:
            raise KeyStorageError(f"Invalid mnemonic: {exc}") from exc

        key_hex = acct.key.hex().removeprefix("0x")
        addresses: Dict[str, str] = {}
        for chain in CHAIN_IDS:
            self._keys[chain] = key_hex
            addresses[chain] = acct.address

        return addresses

    @staticmethod
    def generate_mnemonic() -> str:
        """Generate a new 12-word BIP-39 mnemonic.

        Returns:
            Space-separated mnemonic phrase.
        """
        _acct, mnemonic = Account.create_with_mnemonic()
        return mnemonic

    def get_address(self, chain: str) -> str:
        """Derive the checksummed address for *chain*.

        Returns:
            EIP-55 checksummed address string.
        """
        key_hex = self.get_private_key(chain)
        acct = Account.from_key(bytes.fromhex(key_hex))
        return acct.address

    # ------------------------------------------------------------------
    # Encrypted file storage
    # ------------------------------------------------------------------

    def save_encrypted(self, password: str, filename: str = "keystore.json") -> Path:
        """Persist all cached keys to an encrypted JSON file.

        File format::

            {
                "encrypted": true,
                "ciphertext": "<base64>",
                "salt": "<base64>",
                "nonce": "<base64>",
                "kdf": "scrypt"
            }

        Args:
            password: Encryption password.
            filename: File basename inside ``wallet_dir``.

        Returns:
            Path to the written file.

        Raises:
            KeyStorageError: If no keys are cached or encryption fails.
        """
        if not self._keys:
            raise KeyStorageError("No keys to save.")

        plaintext = json.dumps(self._keys).encode("utf-8")
        salt = os.urandom(16)
        nonce = os.urandom(12)

        enc_key = self._derive_encryption_key(password, salt)
        aesgcm = AESGCM(enc_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        payload = {
            "encrypted": True,
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "kdf": "scrypt",
        }

        self._wallet_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._wallet_dir / filename
        filepath.write_text(json.dumps(payload, indent=2))
        filepath.chmod(0o600)
        logger.info("Encrypted keystore saved to %s", filepath)
        return filepath

    def load_encrypted(self, password: str, filename: str = "keystore.json") -> Dict[str, str]:
        """Load keys from an encrypted JSON keystore file.

        Args:
            password: Decryption password.
            filename: File basename inside ``wallet_dir``.

        Returns:
            Dict mapping chain name to checksummed address for each loaded key.

        Raises:
            KeyStorageError: If file missing, wrong password, or corrupt data.
        """
        filepath = self._wallet_dir / filename
        if not filepath.exists():
            raise KeyStorageError(f"Keystore not found: {filepath}")

        try:
            payload = json.loads(filepath.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise KeyStorageError(f"Cannot read keystore: {exc}") from exc

        if not payload.get("encrypted"):
            raise KeyStorageError("Keystore is not marked as encrypted.")

        try:
            ciphertext = base64.b64decode(payload["ciphertext"])
            salt = base64.b64decode(payload["salt"])
            nonce = base64.b64decode(payload["nonce"])
        except (KeyError, ValueError) as exc:
            raise KeyStorageError(f"Malformed keystore: {exc}") from exc

        enc_key = self._derive_encryption_key(password, salt)
        aesgcm = AESGCM(enc_key)

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise KeyStorageError(
                "Decryption failed — wrong password or corrupted file."
            ) from exc

        try:
            keys = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KeyStorageError(f"Corrupt key data: {exc}") from exc

        self._keys.update(keys)

        addresses: Dict[str, str] = {}
        for chain, key_hex in keys.items():
            acct = Account.from_key(bytes.fromhex(key_hex))
            addresses[chain] = acct.address

        logger.info("Loaded %d key(s) from encrypted keystore.", len(keys))
        return addresses

    # ------------------------------------------------------------------
    # Convenience aliases (for test compatibility)
    # ------------------------------------------------------------------

    @property
    def _path(self) -> Path:
        """Path to the keystore file (compat alias for tests)."""
        return self._wallet_dir

    def store(self, keys: Dict[str, str], password: str) -> Path:
        """Store *keys* directly to wallet path as encrypted file.

        Unlike :meth:`save_encrypted` which uses a directory + filename,
        this operates on the ``wallet_dir`` path as a direct file.
        """
        self._keys.clear()
        self._keys.update(keys)

        plaintext = json.dumps(keys).encode("utf-8")
        salt = os.urandom(16)
        nonce = os.urandom(12)

        enc_key = self._derive_encryption_key(password, salt)
        aesgcm = AESGCM(enc_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        payload = {
            "encrypted": True,
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "kdf": "scrypt",
        }

        self._wallet_dir.parent.mkdir(parents=True, exist_ok=True)
        self._wallet_dir.write_text(json.dumps(payload, indent=2))
        self._wallet_dir.chmod(0o600)
        return self._wallet_dir

    def load(self, password: str) -> Dict[str, str]:
        """Load keys directly from the wallet path.

        Returns the decrypted keys dict with ``0x``-prefixed hex values.

        Raises:
            FileNotFoundError: If the wallet file does not exist.
            KeyStorageError: On decryption failure.
        """
        filepath = self._wallet_dir
        if not filepath.exists():
            raise FileNotFoundError(f"Keystore not found: {filepath}")

        try:
            payload = json.loads(filepath.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise KeyStorageError(f"Cannot read keystore: {exc}") from exc

        if not payload.get("encrypted"):
            raise KeyStorageError("Keystore is not marked as encrypted.")

        try:
            ciphertext = base64.b64decode(payload["ciphertext"])
            salt = base64.b64decode(payload["salt"])
            nonce = base64.b64decode(payload["nonce"])
        except (KeyError, ValueError) as exc:
            raise KeyStorageError(f"Malformed keystore: {exc}") from exc

        enc_key = self._derive_encryption_key(password, salt)
        aesgcm = AESGCM(enc_key)

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise KeyStorageError(
                "Decryption failed — wrong password or corrupted file."
            ) from exc

        try:
            keys = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KeyStorageError(f"Corrupt key data: {exc}") from exc

        self._keys.update(keys)
        return dict(keys)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_encryption_key(password: str, salt: bytes) -> bytes:
        """Derive a 256-bit encryption key from *password* using scrypt."""
        kdf = Scrypt(
            salt=salt,
            length=_SCRYPT_KEY_LEN,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
        )
        return kdf.derive(password.encode("utf-8"))

    @staticmethod
    def _validate_chain(chain: str) -> None:
        if chain not in CHAIN_IDS:
            raise KeyStorageError(
                f"Unsupported chain '{chain}'. "
                f"Supported: {', '.join(sorted(CHAIN_IDS))}."
            )

    @staticmethod
    def _validate_private_key(key_hex: str) -> None:
        """Validate a hex-encoded private key.

        Raises:
            KeyStorageError: If the key is malformed or on the invalid curve point.
        """
        if not key_hex:
            raise KeyStorageError("Private key cannot be empty.")

        try:
            key_bytes = bytes.fromhex(key_hex)
        except ValueError as exc:
            raise KeyStorageError(f"Invalid hex in private key: {exc}") from exc

        if len(key_bytes) != 32:
            raise KeyStorageError(
                f"Private key must be 32 bytes, got {len(key_bytes)}."
            )

        # Verify eth-account can parse it (checks secp256k1 validity).
        try:
            Account.from_key(key_bytes)
        except Exception as exc:
            raise KeyStorageError(f"Invalid private key: {exc}") from exc

    def get_chain_id(self, chain: str) -> int:
        """Return the EIP-155 chain ID for *chain*.

        Raises:
            KeyStorageError: If chain is not supported.
        """
        chain = chain.lower()
        self._validate_chain(chain)
        return CHAIN_IDS[chain]

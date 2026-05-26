"""
Wallet manager — concrete ``IWalletManager`` for multi-chain EVM trading.

Delegates key management to :class:`~trading_bot.utils.wallet.storage.KeyStorage`
and uses ``eth-account`` for transaction signing.  Balance queries are
stubbed with a ``web3``-ready placeholder (requires RPC URLs per chain).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from eth_account import Account
from eth_account.datastructures import SignedTransaction
from eth_utils import to_checksum_address

from trading_bot.core.interfaces.wallet_manager import IWalletManager
from trading_bot.utils.wallet.storage import (
    CHAIN_IDS,
    KeyStorage,
    KeyStorageError,
)

logger = logging.getLogger(__name__)


class WalletManager(IWalletManager):
    """Multi-chain EVM wallet manager.

    Args:
        storage: Pre-configured :class:`KeyStorage` instance.
                 If ``None``, a default one is created.
        default_chain: Chain name used when callers omit the *chain*
                       parameter (default ``"ethereum"``).
    """

    def __init__(
        self,
        storage: Optional[KeyStorage] = None,
        default_chain: str = "ethereum",
    ) -> None:
        self._storage = storage or KeyStorage()
        self._default_chain = default_chain.lower()

    # ------------------------------------------------------------------
    # IWalletManager implementation
    # ------------------------------------------------------------------

    async def get_address(self) -> str:  # type: ignore[override]
        """Return the primary wallet address (for the default chain).

        Returns:
            EIP-55 checksummed address string.
        """
        return self.get_address_sync(self._default_chain)

    async def get_balance(
        self, token: str = "", chain: str = ""
    ) -> Dict[str, Any]:
        """Query on-chain balance.

        .. note::
            Full implementation requires RPC provider URLs per chain.
            Currently returns a placeholder indicating the configured chain.

        Args:
            token: Token symbol or contract address.
            chain: Target chain (default: instance default_chain).

        Returns:
            Dict with ``balance``, ``decimals``, ``symbol`` keys.
        """
        chain = (chain or self._default_chain).lower()
        address = self.get_address_sync(chain)
        chain_id = self._storage.get_chain_id(chain)

        # Placeholder — real implementation would use web3.eth.get_balance()
        # with an RPC URL per chain.
        return {
            "balance": "0",
            "decimals": 18,
            "symbol": token or "ETH",
            "chain": chain,
            "chain_id": chain_id,
            "address": address,
        }

    async def sign_transaction(self, tx_data: Dict[str, Any]) -> str:
        """Sign a transaction and return the raw signed hex payload.

        The transaction dict **must** contain a ``chainId`` field (int) or
        a ``chain`` field (str) so the correct key is selected.

        Args:
            tx_data: Transaction fields (to, value, data, gas, chainId, …).

        Returns:
            Hex-encoded signed transaction (``0x…``).

        Raises:
            KeyStorageError: If no key for the chain or tx_data is invalid.
        """
        return self.sign_transaction_sync(tx_data)

    async def export_wallet(self, password: str) -> Dict[str, Any]:
        """Export all cached keys as an encrypted keystore dict.

        Args:
            password: Encryption password.

        Returns:
            Dict containing encrypted keystore payload.
        """
        filepath = self._storage.save_encrypted(password)
        return {
            "exported": True,
            "path": str(filepath),
        }

    async def import_wallet(
        self, wallet_data: Dict[str, Any], password: str
    ) -> bool:
        """Import a wallet from mnemonic, private key, or encrypted keystore.

        Supported ``wallet_data`` shapes:

        * ``{"mnemonic": "word1 word2 …"}``
        * ``{"private_key": "0xabc…", "chain": "bsc"}``
        * ``{"keystore_file": "keystore.json"}``  (loads from wallet_dir)

        Args:
            wallet_data: Import payload.
            password:    Decryption password (for keystore) or
                         BIP-39 passphrase (for mnemonic).

        Returns:
            True on success.

        Raises:
            KeyStorageError: On failure.
        """
        if "mnemonic" in wallet_data:
            self._storage.derive_from_mnemonic(
                wallet_data["mnemonic"],
                passphrase=password,
            )
            return True

        if "private_key" in wallet_data:
            chain = wallet_data.get("chain", "")
            self._storage.import_private_key(
                wallet_data["private_key"], chain=chain
            )
            return True

        if "keystore_file" in wallet_data:
            self._storage.load_encrypted(
                password, filename=wallet_data["keystore_file"]
            )
            return True

        raise KeyStorageError(
            "wallet_data must contain 'mnemonic', 'private_key', "
            "or 'keystore_file'."
        )

    # ------------------------------------------------------------------
    # Wallet creation / import (synchronous utilities)
    # ------------------------------------------------------------------

    @staticmethod
    def create_wallet() -> Dict[str, str]:
        """Generate a new random wallet.

        Returns:
            Dict with ``address`` and ``private_key`` keys.
        """
        acct = Account.create()
        return {
            "address": acct.address,
            "private_key": "0x" + acct.key.hex(),
        }

    @staticmethod
    def import_key(private_key: str) -> Dict[str, str]:
        """Import a private key and return the derived address.

        Args:
            private_key: Hex-encoded private key (with or without 0x prefix).

        Returns:
            Dict with ``address`` and ``private_key`` keys.

        Raises:
            ValueError: If the key is empty or invalid.
        """
        if not private_key:
            raise ValueError("Invalid private key: empty")
        try:
            acct = Account.from_key(private_key)
        except Exception as exc:
            raise ValueError(f"Invalid private key: {exc}") from exc
        return {
            "address": acct.address,
            "private_key": acct.key.hex(),
        }

    @staticmethod
    def get_address(private_key: str) -> str:
        """Derive the checksummed address from a private key.

        Args:
            private_key: Hex-encoded private key.

        Returns:
            EIP-55 checksummed address string.
        """
        acct = Account.from_key(private_key)
        return acct.address

    @staticmethod
    def sign_transaction(
        tx_data: Dict[str, Any], private_key: str
    ) -> Dict[str, Any]:
        """Sign a transaction dict with the given private key.

        Args:
            tx_data: Transaction fields (to, value, gas, chainId, …).
            private_key: Hex-encoded private key.

        Returns:
            Dict containing the signed transaction (includes
            ``raw_transaction`` key).
        """
        raw_key = private_key.removeprefix("0x")
        acct = Account.from_key(bytes.fromhex(raw_key))
        # eth_account v2 requires EIP-55 checksummed address for 'to'
        tx_fixed = dict(tx_data)
        if "to" in tx_fixed and isinstance(tx_fixed["to"], str):
            tx_fixed["to"] = to_checksum_address(tx_fixed["to"])
        signed = acct.sign_transaction(tx_fixed)
        return {
            "raw_transaction": signed.raw_transaction.hex(),
            "hash": signed.hash.hex(),
            "r": signed.r,
            "s": signed.s,
            "v": signed.v,
        }

    # ------------------------------------------------------------------
    # Synchronous helpers (useful for non-async callers)
    # ------------------------------------------------------------------

    def get_address_sync(self, chain: str = "") -> str:
        """Synchronous variant of :meth:`get_address`.

        Args:
            chain: Target chain. Falls back to default_chain.

        Returns:
            EIP-55 checksummed address.
        """
        chain = (chain or self._default_chain).lower()
        return self._storage.get_address(chain)

    def sign_transaction_sync(self, tx_data: Dict[str, Any]) -> str:
        """Synchronous variant of :meth:`sign_transaction`.

        Args:
            tx_data: Transaction fields. Must include ``chainId`` (int)
                     or ``chain`` (str).

        Returns:
            Hex-encoded signed transaction.
        """
        chain = self._resolve_chain(tx_data)
        key_hex = self._storage.get_private_key(chain)
        acct = Account.from_key(bytes.fromhex(key_hex))

        # Ensure chainId is in the transaction for EIP-155 replay protection.
        tx = dict(tx_data)
        tx.pop("chain", None)
        if "chainId" not in tx:
            tx["chainId"] = self._storage.get_chain_id(chain)

        signed: SignedTransaction = acct.sign_transaction(tx)
        return signed.raw_transaction.hex()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def storage(self) -> KeyStorage:
        """Access the underlying :class:`KeyStorage`."""
        return self._storage

    @property
    def supported_chains(self) -> list[str]:
        """Return list of supported chain names."""
        return sorted(CHAIN_IDS.keys())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_chain(tx_data: Dict[str, Any]) -> str:
        """Determine the chain from transaction data.

        Checks ``chain`` (str) first, then reverse-maps ``chainId`` (int).
        """
        if "chain" in tx_data:
            return tx_data["chain"].lower()

        chain_id = tx_data.get("chainId")
        if chain_id is not None:
            id_to_chain = {v: k for k, v in CHAIN_IDS.items()}
            chain = id_to_chain.get(chain_id)
            if chain:
                return chain
            raise KeyStorageError(
                f"Unknown chainId {chain_id}. "
                f"Supported: {dict(sorted(CHAIN_IDS.items()))}."
            )

        raise KeyStorageError(
            "Transaction must include 'chain' (str) or 'chainId' (int)."
        )

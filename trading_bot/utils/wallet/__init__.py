"""
Wallet management utilities.

EVM wallet handling: key derivation, address management,
transaction signing, and balance queries across chains.
"""

from trading_bot.utils.wallet.manager import WalletManager
from trading_bot.utils.wallet.storage import KeyStorage, KeyStorageError

__all__ = [
    "WalletManager",
    "KeyStorage",
    "KeyStorageError",
]

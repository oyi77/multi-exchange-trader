"""Thin ABI re-export for the DEX module.

Convenience wrapper so DEX code can ``from exchange.dex.abi import load_abi``
without traversing the utils package.
"""

from trading_bot.utils.abi.loader import ABILoader, ABILoaderError, ABIValidationError

__all__ = ["ABILoader", "ABILoaderError", "ABIValidationError", "abi_loader"]

abi_loader = ABILoader()
load_abi = abi_loader.load

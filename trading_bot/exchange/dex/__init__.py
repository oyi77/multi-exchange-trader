"""
Decentralized exchange (DEX) adapters.

Multi-chain EVM DEX support (BSC, ETH, Polygon, Arbitrum).
Provides on-chain trading via smart contract interaction.

Sub-packages:
    chain    — Chain-specific clients (RPC, gas, nonce management)
    services — DEX protocol services (swap, liquidity, routing)
    abi      — Contract ABI definitions
"""

from trading_bot.exchange.dex.base import EVMDEXProvider
from trading_bot.exchange.dex.bsc import BscDexProvider
from trading_bot.exchange.dex.services.swap import DexSwapService, RouteQuote, SwapResult

__all__ = [
    "EVMDEXProvider",
    "BscDexProvider",
    "DexSwapService",
    "RouteQuote",
    "SwapResult",
]

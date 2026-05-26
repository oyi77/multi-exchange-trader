"""
BSC (Binance Smart Chain) chain-specific configuration and helpers.

Router addresses, RPC endpoints, gas oracle, and defaults for
PancakeSwap, BakerySwap, ApeSwap, and Biswap DEX protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BSCConfig:
    """Immutualbe configuration for the BSC chain and its DEX protocols.

    Attributes:
        chain_id: EIP-155 chain identifier for BSC (``56`` mainnet).
        rpc_urls: Ordered list of RPC endpoints (first is primary).
        router_addresses: Mapping of DEX name → router contract address.
        factory_addresses: Mapping of DEX name → factory contract address.
        wbnb_address: Wrapped BNB token address.
        usdt_address: USDT (BEP-20) token address.
        multicall_address: Multicall3 contract address.
        bscscan_api_url: Base URL for BscScan API.
        block_explorer_url: Base URL for BscScan block explorer.
    """

    chain_id: int = 56
    chain_name: str = "BSC Mainnet"

    rpc_urls: tuple[str, ...] = (
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org",
        "https://bsc-dataseed3.binance.org",
        "https://bsc-dataseed4.binance.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed2.defibit.io",
        "https://bsc-dataseed1.ninicoin.io",
        "https://bsc-dataseed2.ninicoin.io",
    )

    # DEX router addresses (BSC mainnet)
    router_addresses: ClassVar[dict[str, str]] = {
        "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
        "pancakeswap_v2": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
        "bakeryswap": "0xCDe540d7eAFE93aC5fE6233Bee57E1270D1E330F",
        "apeswap": "0xcF0feBd3f17CEf5b47b0cD257aCF6025c5BFf3b7",
        "biswap": "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
    }

    # DEX factory addresses (BSC mainnet)
    factory_addresses: ClassVar[dict[str, str]] = {
        "pancakeswap": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
        "bakeryswap": "0x01bF7C66C6BD861915CdaaE475042d3c4BaE16B7",
        "apeswap": "0x0841BD0B734E4F5853f0B02cD60dFB2fD59183b0",
        "biswap": "0x858E3312ed3A876947EA49d572A7C42DE08af7EE",
    }

    # Token addresses
    wbnb_address: str = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
    usdt_address: str = "0x55d398326f99059fF775485246999027B3197955"
    multicall_address: str = "0xcA11bde05977b3631167028862bE2a173976CA11"

    # API endpoints
    bscscan_api_url: str = "https://api.bscscan.com/api"
    block_explorer_url: str = "https://bscscan.com"


# ---------------------------------------------------------------------------
# Module-level singleton for convenient access.
# ---------------------------------------------------------------------------

BSC = BSCConfig()

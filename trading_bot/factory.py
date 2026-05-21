"""Factory module for creating trading components."""

import os
from typing import Optional, Dict, Any

from trading_bot.exchange.simulator import SimulatorExchange
from trading_bot.strategy.xau_hedging import XAUHedgingStrategy, XAUHedgingConfig


def get_exchange(
    provider: str,
    mode: str = "paper",
    symbol: str = "XAUUSD",
    config: Optional[Dict[str, Any]] = None,
    **kwargs,
):
    """Create an exchange provider.

    Args:
        provider: Provider name (simulator, paper)
        mode: Trading mode (paper, frontest, real)
        symbol: Trading symbol
        config: Optional configuration dict
        **kwargs: Additional provider-specific arguments

    Returns:
        Exchange instance or None if creation fails
    """
    config = config or {}

    if provider in ("simulator", "paper") or mode == "paper":
        return SimulatorExchange(
            initial_balance=config.get("balance", 1000),
            symbol=symbol,
        )

    # For real providers, import dynamically
    if provider == "ostium":
        from trading_bot.exchange.ostium import create_ostium_exchange

        private_key = os.getenv("OSTIUM_PRIVATE_KEY")
        rpc_url = os.getenv("OSTIUM_RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")
        chain_id = int(os.getenv("OSTIUM_CHAIN_ID", "421614"))

        if not private_key:
            return None

        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            create_ostium_exchange(
                private_key,
                rpc_url,
                chain_id,
                leverage=config.get("leverage", 50),
            )
        )

    if provider == "exness":
        from trading_bot.exchange.exness_exchange import create_exness_exchange

        account_id = os.getenv("EXNESS_ACCOUNT_ID")
        token = os.getenv("EXNESS_TOKEN")
        server = os.getenv("EXNESS_SERVER", "trial6")

        if not account_id or not token:
            return None

        return create_exness_exchange(
            account_id=int(account_id),
            token=token,
            server=server,
        )

    if provider == "bybit":
        from trading_bot.exchange.bybit_exchange import create_bybit_exchange

        api_key = os.getenv("BYBIT_API_KEY")
        api_secret = os.getenv("BYBIT_API_SECRET")

        if not api_key or not api_secret:
            return None

        return create_bybit_exchange(
            api_key,
            api_secret,
            testnet=(mode == "frontest"),
            leverage=config.get("leverage", 50),
        )

    if provider == "deriv":
        from trading_bot.exchange.deriv_exchange import DerivExchange

        token = os.getenv("DERIV_TOKEN")
        paper = mode != "real"
        return DerivExchange(token=token, paper=paper)

    if provider == "bitget":
        from trading_bot.exchange.ccxt import CCXTExchange

        api_key = os.getenv("BITGET_API_KEY")
        api_secret = os.getenv("BITGET_API_SECRET")
        passphrase = os.getenv("BITGET_PASSPHRASE")

        if not api_key or not api_secret:
            return None

        return CCXTExchange(
            exchange_name="bitget",
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=(mode == "frontest"),
        )

    return None


def get_strategy(
    strategy_name: str,
    config: Optional[Dict[str, Any]] = None,
):
    """Create a trading strategy.

    Args:
        strategy_name: Strategy name (xau_hedging, grid, trend)
        config: Strategy configuration

    Returns:
        Strategy instance or None if unknown strategy
    """
    config = config or {}

    if strategy_name in ("xau_hedging", "xau", "hedging"):
        strategy_config = XAUHedgingConfig(
            lots=config.get("lot", 0.01),
            stop_loss=config.get("stop_loss", 1500),
            take_profit=config.get("take_profit", 0),
            trailing=config.get("trailing", 500),
            trail_start=config.get("trail_start", 1000),
            x_distance=config.get("x_distance", 300),
            start_direction=config.get("start_direction", 0),
        )
        return XAUHedgingStrategy(strategy_config)

    if strategy_name == "grid":
        from trading_bot.strategy.grid import GridStrategy

        return GridStrategy(config)

    if strategy_name == "trend":
        from trading_bot.strategy.trend import TrendStrategy

        return TrendStrategy(config)

    return None


def create_trading_setup(
    provider: str,
    strategy: str,
    mode: str = "paper",
    symbol: str = "XAUUSD",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a complete trading setup.

    Args:
        provider: Exchange provider
        strategy: Strategy name
        mode: Trading mode
        symbol: Trading symbol
        config: Configuration dict

    Returns:
        Dict with 'exchange' and 'strategy' keys
    """
    config = config or {}

    exchange = get_exchange(
        provider=provider,
        mode=mode,
        symbol=symbol,
        config=config,
    )

    strategy_instance = get_strategy(
        strategy_name=strategy,
        config=config,
    )

    return {
        "exchange": exchange,
        "strategy": strategy_instance,
        "provider": provider,
        "mode": mode,
        "symbol": symbol,
    }

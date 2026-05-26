import asyncio
import os
import socket

import pytest
from unittest.mock import AsyncMock, Mock, patch

from trading_bot.interface.base import InterfaceConfig
from trading_bot.interface.tui import TUIInterface
from trading_bot.exchange.ostium import OstiumExchange, OstiumPosition
from trading_bot.exchange.simulator import SimulatorExchange
from trading_bot.core.models import Trade
from trading_bot.strategy.xau_hedging import XAUHedgingConfig, XAUHedgingStrategy
from trading_bot.trading_engine import TradingEngine


class _DummyLive:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, _display):
        return None


def test_tui_run_handles_eof_and_starts(monkeypatch):
    cfg = InterfaceConfig(mode="paper", symbol="XAUUSDm", balance=100.0)
    ui = TUIInterface(config=cfg)

    on_start = Mock()
    on_stop = Mock()
    ui.set_callbacks(on_start=on_start, on_stop=on_stop)

    monkeypatch.setattr("trading_bot.interface.tui.Live", _DummyLive)
    monkeypatch.setattr("builtins.input", Mock(side_effect=EOFError))
    monkeypatch.setattr(ui, "_setup_terminal", lambda: (None, None, None))
    monkeypatch.setattr(ui, "_restore_terminal", lambda *_: None)
    monkeypatch.setattr(ui.console, "clear", lambda: None)
    monkeypatch.setattr(ui.console, "print", lambda *_args, **_kwargs: None)

    keys = iter(["q"])
    monkeypatch.setattr(ui, "_read_key", lambda: next(keys, None))

    ui.run()

    on_start.assert_called_once()
    on_stop.assert_called_once()


def test_ostium_sync_logs_only_on_count_change(caplog):
    class _Subgraph:
        async def get_open_trades(self, _address):
            return []

    class _FakeSDK:
        def __init__(self, trades):
            self._trades = trades
            self.subgraph = _Subgraph()

        async def get_open_trades(self, _address):
            return self._trades, _address

    trade = {
        "pair": {"id": 5, "from": "XAU", "to": "USD"},
        "isBuy": True,
        "collateral": 10_000_000,
        "notional": 50_000_000,
        "openPrice": 5_000 * 10**18,
        "leverage": 5000,
        "takeProfitPrice": "0",
        "stopLossPrice": "0",
        "funding": 0,
        "payout": 0,
        "tradeID": "trade_1",
        "index": 1,
    }

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK([trade])
    exchange.trader_address = "0xabc"
    exchange.positions = []
    exchange._subgraph_warned = False
    exchange._last_synced_position_count = None

    caplog.set_level("INFO")
    asyncio.run(exchange._sync_positions())
    asyncio.run(exchange._sync_positions())

    sync_logs = [
        rec.message
        for rec in caplog.records
        if rec.message.startswith("Synced ") and "positions from Ostium" in rec.message
    ]
    assert sync_logs == ["Synced 1 positions from Ostium"]


def test_ostium_sync_logs_on_count_transitions(caplog):
    class _Subgraph:
        async def get_open_trades(self, _address):
            return []

    class _FakeSDK:
        def __init__(self, sequence):
            self._sequence = sequence
            self._index = 0
            self.subgraph = _Subgraph()

        async def get_open_trades(self, _address):
            value = self._sequence[min(self._index, len(self._sequence) - 1)]
            self._index += 1
            return value, _address

    trade = {
        "pair": {"id": 5, "from": "XAU", "to": "USD"},
        "isBuy": True,
        "collateral": 10_000_000,
        "notional": 50_000_000,
        "openPrice": 5_000 * 10**18,
        "leverage": 5000,
        "takeProfitPrice": "0",
        "stopLossPrice": "0",
        "funding": 0,
        "payout": 0,
        "tradeID": "trade_1",
        "index": 1,
    }

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK([[trade], [], [trade]])
    exchange.trader_address = "0xabc"
    exchange.positions = []
    exchange._subgraph_warned = False
    exchange._last_synced_position_count = None

    caplog.set_level("INFO")
    asyncio.run(exchange._sync_positions())
    asyncio.run(exchange._sync_positions())
    asyncio.run(exchange._sync_positions())

    sync_logs = [
        rec.message
        for rec in caplog.records
        if rec.message.startswith("Synced ") and "positions from Ostium" in rec.message
    ]
    assert sync_logs == [
        "Synced 1 positions from Ostium",
        "Synced 0 positions from Ostium",
        "Synced 1 positions from Ostium",
    ]


def test_ostium_sync_does_not_fallback_when_wrapper_returns_empty(caplog):
    class _Subgraph:
        async def get_open_trades(self, _address):
            return []

    class _FakeSDK:
        def __init__(self):
            self._trades = []
            self.subgraph = _Subgraph()

        async def get_open_trades(self, _address):
            return self._trades, _address

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.trader_address = "0xabc"
    exchange.positions = []
    exchange._subgraph_warned = False
    exchange._last_synced_position_count = None

    caplog.set_level("WARNING")
    asyncio.run(exchange._sync_positions())

    warnings = [
        rec.message
        for rec in caplog.records
        if "Position sync unavailable on current endpoint" in rec.message
    ]
    assert warnings == []
    assert exchange.positions == []


def test_ostium_sync_404_from_subgraph_is_treated_as_empty(caplog):
    class _Subgraph:
        async def get_open_trades(self, _address):
            raise RuntimeError(
                "404, message='Not Found', url='https://api.goldsky.com/subgraphs/ost-sep-final'"
            )

    class _FakeSDK:
        def __init__(self):
            self.subgraph = _Subgraph()

        async def get_open_trades(self, _address):
            raise RuntimeError("wrapper unavailable")

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.trader_address = "0xabc"
    exchange.positions = []
    exchange._subgraph_warned = False
    exchange._last_synced_position_count = None

    caplog.set_level("WARNING")
    asyncio.run(exchange._sync_positions())

    warnings = [
        rec.message
        for rec in caplog.records
        if "Position sync unavailable on current endpoint" in rec.message
    ]
    assert warnings == []
    assert exchange.positions == []


def test_ostium_update_price_uses_live_sdk_price(monkeypatch):
    class _PriceAPI:
        def __init__(self):
            self._values = iter([2701.25, 2704.5])

        async def get_price(self, _base, _quote):
            value = next(self._values)
            return value, value + 0.5, value - 0.5

    class _FakeSDK:
        def __init__(self):
            self.price = _PriceAPI()

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.trader_address = "0xabc"
    exchange.current_price = 2650.0

    asyncio.run(exchange.update_price())
    first = exchange.current_price
    asyncio.run(exchange.update_price())
    second = exchange.current_price

    assert first == 2701.25
    assert second == 2704.5


def test_ostium_get_price_uses_metadata_fallback_before_static(monkeypatch):
    class _PriceAPI:
        async def get_price(self, _base, _quote):
            raise RuntimeError("sdk price unavailable")

    class _FakeSDK:
        def __init__(self):
            self.price = _PriceAPI()

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.current_price = 2650.0

    monkeypatch.setattr(
        exchange, "_get_metadata_price", lambda _symbol: 5123.45, raising=False
    )

    result = asyncio.run(exchange.get_price("XAUUSDm"))
    assert result == 5123.45


def test_ostium_open_position_limit_sets_zero_slippage(monkeypatch):
    class _OstiumAPI:
        def __init__(self):
            self.set_slippage_percentage = Mock()
            self.perform_trade = Mock(return_value={"transactionHash": "0xabc123"})

    class _FakeSDK:
        def __init__(self):
            self.ostium = _OstiumAPI()

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.connected = True
    exchange.leverage = 50
    exchange.positions = []
    exchange.trades = []
    exchange.position_counter = 0
    exchange.balance = 1000.0
    exchange.equity = 1000.0
    exchange.get_price = AsyncMock(return_value=5100.0)
    exchange._sync_positions = AsyncMock(return_value=None)
    monkeypatch.setattr("asyncio.sleep", AsyncMock(return_value=None))

    result = asyncio.run(
        exchange.open_position(
            symbol="XAUUSD",
            side="buy",
            volume=0.01,
            sl=None,
            tp=None,
            order_type="LIMIT",
        )
    )

    assert result is not None
    exchange.sdk.ostium.set_slippage_percentage.assert_called_once_with(0)


def test_ostium_open_position_market_sets_nonzero_slippage(monkeypatch):
    class _OstiumAPI:
        def __init__(self):
            self.set_slippage_percentage = Mock()
            self.perform_trade = Mock(return_value={"transactionHash": "0xdef456"})

    class _FakeSDK:
        def __init__(self):
            self.ostium = _OstiumAPI()

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.connected = True
    exchange.leverage = 50
    exchange.positions = []
    exchange.trades = []
    exchange.position_counter = 0
    exchange.balance = 1000.0
    exchange.equity = 1000.0
    exchange.get_price = AsyncMock(return_value=5100.0)
    exchange._sync_positions = AsyncMock(return_value=None)
    monkeypatch.setattr("asyncio.sleep", AsyncMock(return_value=None))

    result = asyncio.run(
        exchange.open_position(
            symbol="XAUUSD",
            side="buy",
            volume=0.01,
            sl=None,
            tp=None,
            order_type="MARKET",
        )
    )

    assert result is not None
    exchange.sdk.ostium.set_slippage_percentage.assert_called_once_with(1)


def test_ostium_open_position_records_trade_when_synced(monkeypatch):
    class _OstiumAPI:
        def __init__(self):
            self.set_slippage_percentage = Mock()
            self.perform_trade = Mock(return_value={"transactionHash": "0xsync001"})

    class _FakeSDK:
        def __init__(self):
            self.ostium = _OstiumAPI()

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.connected = True
    exchange.leverage = 50
    exchange.positions = [
        OstiumPosition(
            id="synced_pos_1",
            symbol="XAUUSD",
            side="long",
            size=0.01,
            entry_price=5100.0,
            current_price=5100.0,
            unrealized_pnl=0.0,
            leverage=50,
            liquidation_price=5000.0,
            margin=10.0,
            pair_id=5,
            trade_index=1,
            tx_hash="0xsync001",
        )
    ]
    exchange.trades = []
    exchange.position_counter = 0
    exchange.balance = 1000.0
    exchange.equity = 1000.0
    exchange.get_price = AsyncMock(return_value=5100.0)
    exchange._sync_positions = AsyncMock(return_value=None)
    monkeypatch.setattr("asyncio.sleep", AsyncMock(return_value=None))

    result = asyncio.run(
        exchange.open_position(
            symbol="XAUUSD",
            side="buy",
            volume=0.01,
        )
    )

    assert result == "synced_pos_1"
    stats = exchange.get_stats()
    assert stats["total_trades"] == 1


def test_ostium_update_price_marks_position_pnl(monkeypatch):
    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = object()
    exchange.trader_address = "0xabc"
    exchange.current_price = 5000.0
    exchange.positions = [
        OstiumPosition(
            id="p1",
            symbol="XAUUSD",
            side="long",
            size=0.1,
            entry_price=5000.0,
            current_price=5000.0,
            unrealized_pnl=0.0,
            leverage=10,
            liquidation_price=4500.0,
            margin=50.0,
            pair_id=5,
            trade_index=1,
        )
    ]

    exchange.get_price = AsyncMock(return_value=5100.0)
    asyncio.run(exchange.update_price())

    assert exchange.current_price == 5100.0
    assert exchange.positions[0].current_price == 5100.0
    assert exchange.positions[0].unrealized_pnl == 10.0


def test_trading_engine_uses_total_trades_without_halving():
    cfg = InterfaceConfig(mode="paper", symbol="XAUUSDm", balance=100.0)
    engine = TradingEngine(cfg, interface=None)
    simulator = SimulatorExchange(initial_balance=1000.0)
    engine.exchanges = [simulator]
    simulator.trades = [Trade(id="t1", symbol="XAUUSD", side="buy", amount=1, price=1)]
    simulator.get_stats = lambda: {
        "balance": 1000.0,
        "equity": 1005.0,
        "net_pnl": 5.0,
        "total_trades": 1,
    }
    engine.strategy = XAUHedgingStrategy(XAUHedgingConfig(lots=0.01, use_session_filter=False))
    engine.strategy.on_tick = lambda price, bid, ask, positions, timestamp=None: None

    asyncio.run(engine._update())

    assert engine.metrics.trades == 1


def test_ostium_update_price_uses_sdk_pnl_metrics(monkeypatch):
    class _Metrics:
        async def get_open_trade_metrics(
            self, pair_id, trade_index, trader_address=None
        ):
            return {
                "net_pnl": 25.5,
                "liquidation_price": 4800.0,
            }

    class _FakeSDK:
        def __init__(self):
            self.metrics = _Metrics()
            self.get_open_trade_metrics = self.metrics.get_open_trade_metrics

    exchange = OstiumExchange.__new__(OstiumExchange)
    exchange.sdk = _FakeSDK()
    exchange.trader_address = "0xabc"
    exchange.current_price = 5100.0
    exchange.positions = [
        OstiumPosition(
            id="p1",
            symbol="XAUUSD",
            side="long",
            size=0.01,
            entry_price=5000.0,
            current_price=5000.0,
            unrealized_pnl=0.0,
            leverage=10,
            liquidation_price=4500.0,
            margin=50.0,
            pair_id=5,
            trade_index=1,
        )
    ]

    monkeypatch.setattr(exchange, "get_price", AsyncMock(return_value=5100.0))
    asyncio.run(exchange.update_price())

    assert exchange.positions[0].unrealized_pnl == 25.5
    assert exchange.positions[0].liquidation_price == 4800.0


def _port_in_use(port=8080):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.mark.skipif(
    not os.environ.get("DISPLAY") or _port_in_use(),
    reason="No display or port 8080 in use",
)
def test_web_interface_auto_opens_browser(monkeypatch):
    from trading_bot.interface.web import WebInterface

    opened_url = None

    def mock_open(url, new=0):
        nonlocal opened_url
        opened_url = url

    monkeypatch.setattr("webbrowser.open", mock_open)
    monkeypatch.setattr("threading.Thread", Mock())

    cfg = InterfaceConfig(mode="paper", symbol="XAUUSDm", balance=100.0)
    ui = WebInterface(config=cfg)
    ui.server = Mock()

    monkeypatch.setattr("signal.signal", Mock())

    with patch.object(ui, "log", Mock()):
        ui.on_start_callback = Mock()
        ui.running = True
        ui.stop = Mock()

        import threading

        original_event_wait = threading.Event.wait
        call_count = [0]

        def mock_wait(self, timeout=None):
            call_count[0] += 1
            if call_count[0] >= 2:
                ui.running = False
                raise KeyboardInterrupt()
            return True

        monkeypatch.setattr(threading.Event, "wait", mock_wait)

        try:
            ui.run()
        except KeyboardInterrupt:
            pass

        assert opened_url == "http://127.0.0.1:8080"

#!/usr/bin/env python3
"""
Project Arbiter v4.0 — The MATRIX SNIPER
========================================
Monitors ALL volatility indices via adjacency pattern analysis.

Strategy: Adjacency pairs (Trigger -> Target digit)
  - Track consecutive digit pairs in last 100 ticks
  - Find most frequent pattern
  - Lock Trigger + Target
  - Fire max 2 shots, must wait for Trigger to reappear between shots
  - Anti-flood filter: skip if target overrepresented in last 20 ticks

vs Cold Digit (v3.x):
  Cold Digit = statistical anomaly (digit hasn't appeared)
  Adjacency = pattern recognition (digit pairs)
"""

import asyncio, json, logging, os, sqlite3, threading, urllib.request
from collections import deque, Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("actuary")
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# ─── Config ─────────────────────────────────────────────────────────────────
DERIV_WS = "wss://frontend.binaryws.com/websockets/v3?app_id=1"
DERIV_TOKEN = os.getenv("DERIV_TOKEN", "")

SYMBOLS = ["R_100", "R_75", "R_50", "R_25", "R_10"]
SYMBOL_LABELS = {
    "R_100": "Volatility 100",
    "R_75": "Volatility 75",
    "R_50": "Volatility 50",
    "R_25": "Volatility 25",
    "R_10": "Volatility 10 (1s)",
}

TICK_HISTORY = 100
MAX_PATTERN_LOOKBACK = 100
ANTI_FLOOD_WINDOW = 20
ANTI_FLOOD_MAX = 3
MAX_SHOTS = 2
STAKE = 1.0
PAYOUT_MULTIPLIER = 8.33

# === MENTOR MODE — One-Shot Recovery (from Veris mentor) ===
# Override: only trade specific pattern (trigger→target) on specific market
MENTOR_MODE = True          # Enable all mentor strategies
MENTOR_ONE_SHOT = True      # Max 1 shot per trigger cycle
MENTOR_RECOVERY = True      # High conviction: 2× stake setelah 3 loss berturut-turut
MENTOR_BASE_STAKE = 0.35    # Base $0.35
MENTOR_RECOVERY_STAKE = 0.70 # 2× stake saat high conviction (bukan $100!)

# Mentor Teknik Spread — Multi-Duration (R_75, trigger 7 → 3 contracts)
MENTOR_SPREAD_ENABLED = False  # Disabled — terlalu mahal ($1.05/volley) untuk modal kecil
MENTOR_SPREAD_MARKET = "R_75"
MENTOR_SPREAD_TRIGGER = 7
MENTOR_SPREADS = [
    {"duration": 3, "duration_unit": "t", "target": 0, "label": "3t→0"},
    {"duration": 2, "duration_unit": "t", "target": 8, "label": "2t→8"},
    {"duration": 1, "duration_unit": "t", "target": 9, "label": "1t→9"},
]
MENTOR_SPREAD_MAX_VOLLEYS = 2  # Max 2 volleys, then re-observe

# Mentor Teknik 1 — One-Shot (R_75, trigger 1|2|3|4 → fire 7)
MENTOR1_MARKET = "R_75"
MENTOR1_TRIGGERS = [1, 2, 3, 4]  # 1,2,3,4 all can trigger 7 (One-Shot technique)
MENTOR1_TARGET = 7  # Always fire on 7

# Mentor Teknik 2 — V Pattern (R_25, X→Y→X consecutive, fire Y when X repeats)
MENTOR2_MARKET = "R_25"
MENTOR2_VPATTERN_ENABLED = True  # Detect V patterns (X→Y→X in 3 consecutive ticks)

# Mentor Teknik 3 — Kebalikan Teknik 1 (R_50, trigger 7→confirm 4→fire 4)
MENTOR3_MARKET = "R_50"
MENTOR3_TRIGGER = 7
MENTOR3_CONFIRM = 4
MENTOR3_TARGET = 4

# === v5.0 COGNITIVE SNIPER — LEARNING PARAMS ===
DEFAULT_MIN_THRESHOLD = 3  # Pattern must appear 3x to lock
PATTERN_BLACKLIST_HOURS = 24
LATENCY_TRAP_MS = 350       # <350ms = too slow
LATENCY_TRAP_LIMIT = 2      # 2 traps → shift to Tick+2
MARKET_WIN_COOLDOWN_MIN = 5  # 5 min cooldown after win
MARKET_LOSS_BLACKLIST_MIN = 60  # 1hr blacklist after 2 consecutive losses

DAILY_TP = 50.0     # Target $50/hari — dikejar dari 5 teknik × 5 market
DAILY_SL = -10.0    # SL $10 — stop loss harian
LOCK_TP_HOURS = 12
LOCK_SL_HOURS = 2

LOG_DIR = Path("/home/openclaw/.openclaw/workspace/logs/deriv_actuary")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ===== TELEGRAM NOTIFIER (fire-and-forget) =====
_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "157228659")

def tg_alert(msg: str):
    """Fire-and-forget Telegram notification. Runs in thread to not block main loop."""
    if not _TG_TOKEN:
        return
    def _send():
        try:
            url = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": _TG_CHAT, "text": msg, "parse_mode": "HTML"}).encode()
            urllib.request.urlopen(url, data=data, timeout=10)
        except:
            pass
    threading.Thread(target=_send, daemon=True).start()

# ===== v5.0 COGNITIVE DATABASE =====
COG_DB = LOG_DIR / "cognitive_memory.db"

def init_cognitive_db():
    """Initialize the cognitive_memory + pattern_stats tables."""
    conn = sqlite3.connect(str(COG_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cognitive_memory (
            market TEXT NOT NULL,
            pattern_string TEXT NOT NULL,
            total_attempts INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0,
            min_threshold INTEGER DEFAULT 3,
            latency_offset INTEGER DEFAULT 0,
            cooldown_until TEXT,
            blacklisted_until TEXT,
            consecutive_losses INTEGER DEFAULT 0,
            last_updated TEXT,
            PRIMARY KEY (market, pattern_string)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_state (
            market TEXT PRIMARY KEY,
            win_cooldown_until TEXT,
            loss_blacklist_until TEXT,
            consecutive_losses INTEGER DEFAULT 0,
            latency_trap_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS latency_traps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            trigger_tick_time TEXT,
            executed_tick_time TEXT,
            latency_ms INTEGER
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"🧠 Cognitive DB ready: {COG_DB}")

class CognitiveDB:
    """Self-learning database for pattern optimization."""
    
    @staticmethod
    def conn():
        return sqlite3.connect(str(COG_DB))
    
    @staticmethod
    def record_pattern_result(market, pattern_str, won):
        """Update win/loss for a pattern and auto-adjust threshold."""
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT * FROM cognitive_memory WHERE market=? AND pattern_string=?",
                (market, pattern_str)
            ).fetchone()
            
            if row:
                total = row[2] + 1
                wins = row[3] + (1 if won else 0)
                wr = wins / total
                threshold = row[5]
                blacklisted = row[8]
                
                # Self-learning: adjust threshold
                if total >= 5 and wr < 0.30 and threshold < 4:
                    threshold = min(threshold + 1, 8)
                    log_msg = f"🧠 [{market}] {pattern_str} WR={wr:.0f}% < 30% → threshold={threshold}"
                    logger.info(log_msg)
                    tg_alert(f"⚠️ THRESHOLD UP\n{market} {pattern_str}\nWR {wr:.0f}% → threshold {threshold} (total {total} trades)")
                if total >= 5 and wr < 0.15 and not blacklisted:
                    blacklisted = (datetime.now(timezone.utc) + timedelta(hours=PATTERN_BLACKLIST_HOURS)).isoformat()
                    log_msg = f"🧠 [{market}] {pattern_str} WR={wr:.0f}% < 15% → BLACKLISTED 24h"
                    logger.info(log_msg)
                    tg_alert(f"🚫 PATTERN BLACKLISTED\n{market} {pattern_str}\nWR {wr:.0f}% ({total} trades) → 24h cooldown")
                if wr > 0.45 and threshold > 3:
                    threshold = 3
                    log_msg = f"🧠 [{market}] {pattern_str} WR={wr:.0f}% > 45% → threshold reset to 3"
                    logger.info(log_msg)
                    tg_alert(f"✅ THRESHOLD DOWN\n{market} {pattern_str}\nWR {wr:.0f}% → threshold reset to 3 (champion pattern)")
                
                conn.execute("""
                    UPDATE cognitive_memory SET total_attempts=?, wins=?, win_rate=?,
                    min_threshold=?, blacklisted_until=?, last_updated=?
                    WHERE market=? AND pattern_string=?
                """, (total, wins, round(wr, 3), threshold, blacklisted, now, market, pattern_str))
            else:
                wr = 1.0 if won else 0.0
                conn.execute("""
                    INSERT INTO cognitive_memory 
                    (market, pattern_string, total_attempts, wins, win_rate, min_threshold, last_updated)
                    VALUES (?,?,1,?,?,3,?)
                """, (market, pattern_str, 1 if won else 0, wr, now))
            conn.commit()
    
    @staticmethod
    def should_lock_pattern(market, pattern_str, freq):
        """Check if pattern meets learned threshold and isn't blacklisted."""
        now = datetime.now(timezone.utc)
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT min_threshold, blacklisted_until FROM cognitive_memory WHERE market=? AND pattern_string=?",
                (market, pattern_str)
            ).fetchone()
            
            if row:
                threshold = row[0]
                blacklisted = row[1]
                if blacklisted:
                    try:
                        bl_time = datetime.fromisoformat(blacklisted)
                        if now < bl_time:
                            return False  # Still blacklisted
                    except:
                        pass
                return freq >= threshold
            return freq >= DEFAULT_MIN_THRESHOLD  # Default
    
    @staticmethod
    def record_market_result(market, won):
        """Track consecutive wins/losses per market for cooldowns."""
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            row = conn.execute("SELECT * FROM market_state WHERE market=?", (market,)).fetchone()
            
            if not row:
                conn.execute(
                    "INSERT INTO market_state VALUES (?,NULL,NULL,0,0)",
                    (market,)
                )
                conn.commit()
                return
            
            cons_losses = row[3] if row else 0
            latency_traps = row[4] if row else 0
            
            if won:
                cons_losses = 0
                cd = (datetime.now(timezone.utc) + timedelta(minutes=MARKET_WIN_COOLDOWN_MIN)).isoformat()
                conn.execute(
                    "UPDATE market_state SET win_cooldown_until=?, consecutive_losses=? WHERE market=?",
                    (cd, cons_losses, market)
                )
                logger.info(f"🧠 [{market}] WIN → cooldown {MARKET_WIN_COOLDOWN_MIN}min")
            else:
                cons_losses += 1
                if cons_losses >= 2:
                    bl = (datetime.now(timezone.utc) + timedelta(minutes=MARKET_LOSS_BLACKLIST_MIN)).isoformat()
                    conn.execute(
                        "UPDATE market_state SET loss_blacklist_until=?, consecutive_losses=? WHERE market=?",
                        (bl, cons_losses, market)
                    )
                    logger.info(f"🚫 [{market}] 2 consecutive losses → blacklist {MARKET_LOSS_BLACKLIST_MIN}min")
                    tg_alert(f"🚫 MARKET BLACKLIST\n{market}\n2 consecutive losses → cooldown {MARKET_LOSS_BLACKLIST_MIN}min")
                else:
                    conn.execute(
                        "UPDATE market_state SET consecutive_losses=? WHERE market=?",
                        (cons_losses, market)
                    )
                    tg_alert(f"⚠️ MARKET LOSS #{cons_losses}\n{market}\n1 more loss = {MARKET_LOSS_BLACKLIST_MIN}min blacklist")
            conn.commit()
    
    @staticmethod
    def is_market_cooled(market):
        """Check if market is in cooldown."""
        now = datetime.now(timezone.utc)
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT win_cooldown_until, loss_blacklist_until FROM market_state WHERE market=?",
                (market,)
            ).fetchone()
            if row:
                for val in [row[0], row[1]]:
                    if val:
                        try:
                            if now < datetime.fromisoformat(val):
                                return False
                        except:
                            pass
            return True
    
    @staticmethod
    def record_latency_trap(market, trigger_time, exec_time):
        """Log latency trap and auto-adjust offset if threshold met."""
        latency_ms = (exec_time - trigger_time).total_seconds() * 1000
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            conn.execute(
                "INSERT INTO latency_traps (market, timestamp, trigger_tick_time, executed_tick_time, latency_ms) VALUES (?,?,?,?,?)",
                (market, now, trigger_time.isoformat() if hasattr(trigger_time, 'isoformat') else str(trigger_time),
                 exec_time.isoformat() if hasattr(exec_time, 'isoformat') else str(exec_time), round(latency_ms, 1))
            )
            # Check if market needs Tick+2 offset
            count = conn.execute(
                "SELECT COUNT(*) FROM latency_traps WHERE market=? AND latency_ms < ?",
                (market, LATENCY_TRAP_MS)
            ).fetchone()[0]
            
            if count >= LATENCY_TRAP_LIMIT:
                # Auto-shift to Tick+2
                conn.execute(
                    "UPDATE market_state SET latency_trap_count=? WHERE market=?",
                    (count, market)
                )
                logger.info(f"🧠 [{market}] {count} latency traps → auto-shifting to Tick+2")
                return True  # Signal to shift
            return False
    
    @staticmethod
    def get_learned_params(market, pattern_str):
        """Get all learned parameters for a market+pattern."""
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT * FROM cognitive_memory WHERE market=? AND pattern_string=?",
                (market, pattern_str)
            ).fetchone()
            if row:
                return {
                    "threshold": row[5],
                    "latency_offset": row[6],
                    "cooldown_until": row[7],
                    "blacklisted_until": row[8],
                    "win_rate": row[4],
                    "total": row[2]
                }
            return {"threshold": DEFAULT_MIN_THRESHOLD, "latency_offset": 0, "win_rate": 0, "total": 0}

# Init on import
init_cognitive_db()
# ===== END COGNITIVE DB =====

TOKEN_FILE = Path("/home/openclaw/.openclaw/workspace/projects/deriv-actuary/secrets/token.txt")


# ─── Per-Market Engine ──────────────────────────────────────────────────────

class MarketEngine:
    """v5.0 MATRIX SNIPER: adjacency pattern detection + cognitive learning."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.label = SYMBOL_LABELS.get(symbol, symbol)
        self.last_digits = deque(maxlen=TICK_HISTORY)
        self.last_tick_epoch = 0
        self.last_tick_price = 0.0
        self.trigger_digit: int | None = None
        self.target_digit: int | None = None
        self.pattern_active = False
        self.shots_fired = 0
        self.waiting_for_trigger = False
        self._next_signal = None

    def process_tick(self, quote: float, epoch: int) -> dict:
        """v5.0: adjacency pattern → 2-shot max with trigger waiting."""
        self.last_tick_epoch = epoch
        self.last_tick_price = quote
        current = int(str(quote).replace(".", "")[-1])
        prev = self.last_digits[-1] if self.last_digits else None
        self.last_digits.append(current)
        self._next_signal = None

        result = {"cold": None, "entry": None, "action": "tick"}

        # If waiting for trigger to reappear (shot 1 missed)
        if self.pattern_active and self.waiting_for_trigger:
            if current == self.trigger_digit:
                self.waiting_for_trigger = False
                self._next_signal = ("trade_signal", self.target_digit, f"S2 {self.trigger_digit}->{self.target_digit}")
        
        # No active pattern — scan for new one
        if not self.pattern_active and len(self.last_digits) >= 10:
            pat = self._find_best_pattern()
            if pat:
                t, tg = pat
                self.trigger_digit = t
                self.target_digit = tg
                self.pattern_active = True
                self.shots_fired = 0
                self.waiting_for_trigger = False
                # If trigger just appeared on PREVIOUS tick, fire now
                if prev is not None and prev == t:
                    self.shots_fired = 1
                    self._next_signal = ("trade_signal", tg, f"S1 {t}->{tg}")

        # Active pattern: check if trigger just appeared
        if self.pattern_active and not self.waiting_for_trigger and not self._next_signal:
            if prev is not None and prev == self.trigger_digit:
                self.shots_fired += 1
                if self.shots_fired <= MAX_SHOTS:
                    self._next_signal = ("trade_signal", self.target_digit, f"S{self.shots_fired} {self.trigger_digit}->{self.target_digit}")

        # If we have a signal, return it (will be followed next tick by entry check)
        if self._next_signal:
            action, target, label = self._next_signal
            result["cold"] = target
            result["action"] = action
            result["pattern"] = label
            result["trigger"] = self.trigger_digit if hasattr(self, 'trigger_digit') else 0
            return result

        # Check entry result from previous signal (check_current tick against last target)
        if self.pattern_active and self.target_digit is not None and prev is not None:
            entry = self._check_entry(current)
            if entry:
                result["entry"] = entry
                result["action"] = "entry"
                result["pattern"] = entry.get("pattern", "")

        return result

    def _find_best_pattern(self) -> tuple | None:
        """v5.0: Find best pattern using cognitive thresholds & market cooldowns."""
        if len(self.last_digits) < 10:
            return None
        pairs = Counter()
        digs = list(self.last_digits)
        for i in range(len(digs) - 1):
            pairs[(digs[i], digs[i+1])] += 1
        if not pairs:
            return None
        for (t, tg), freq in sorted(pairs.items(), key=lambda x: -x[1]):
            if tg == 0:
                continue
            recent = list(self.last_digits)[-ANTI_FLOOD_WINDOW:]
            if recent.count(tg) > ANTI_FLOOD_MAX:
                continue
            if CognitiveDB.should_lock_pattern(self.symbol, f"{t}->{tg}", freq):
                if CognitiveDB.is_market_cooled(self.symbol):
                    return (t, tg)
        return None

    def _check_entry(self, actual_digit: int) -> dict | None:
        """v5.0: Check result + record to cognitive DB."""
        predicted = self.target_digit
        if predicted is None:
            return None
        win = (predicted == actual_digit)
        pnl = STAKE * (PAYOUT_MULTIPLIER - 1) if win else -STAKE
        
        pattern_str = f"{self.trigger_digit}->{predicted}"
        CognitiveDB.record_pattern_result(self.symbol, pattern_str, win)
        CognitiveDB.record_market_result(self.symbol, win)
        
        if win or self.shots_fired >= MAX_SHOTS:
            self.pattern_active = False
            self.trigger_digit = None
            self.target_digit = None
            self.waiting_for_trigger = False
        elif not win:
            self.waiting_for_trigger = True
        
        return {
            "sequence": self.shots_fired,
            "predicted": predicted,
            "actual": actual_digit,
            "win": win,
            "pnl": round(pnl, 2),
            "pattern": pattern_str,
            "pattern_note": f"(WR={CognitiveDB.get_learned_params(self.symbol, pattern_str)['win_rate']*100:.0f}%)"
        }

    def get_heatmap(self) -> dict:
        """Return current state for dashboard."""
        if len(self.last_digits) < 2:
            return {"state": "collecting", "count": len(self.last_digits)}
        pairs = Counter()
        digs = list(self.last_digits)
        for i in range(len(digs) - 1):
            pairs[f"{digs[i]}->{digs[i+1]}"] += 1
        return {
            "state": "active",
            "trigger": self.trigger_digit,
            "target": self.target_digit,
            "pattern_active": self.pattern_active,
            "shots_fired": self.shots_fired,
            "waiting": self.waiting_for_trigger,
            "top_patterns": dict(pairs.most_common(5)),
            "tick_count": len(self.last_digits),
        }
# ─── Global Actuary ─────────────────────────────────────────────────────────

class MultiStreamActuary:
    """Orchestrates multiple MarketEngines. Tracks global PnL & anti-tilt."""

    def __init__(self):
        self.markets: dict[str, MarketEngine] = {}
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_wins = 0
        self.locked_until: datetime | None = None
        self.lock_reason = ""
        self.trade_history: list[dict] = []
        self.session_start = datetime.now(timezone.utc)

        for sym in SYMBOLS:
            self.markets[sym] = MarketEngine(sym)

        logger.info("MultiStreamActuary: %d markets | TP=$%.0f SL=$%.0f | Adjacency Pattern (%d-tick lookback) | Self-Learning Cognitive DB",
                     len(SYMBOLS), DAILY_TP, abs(DAILY_SL), MAX_PATTERN_LOOKBACK)

    def process_tick(self, symbol: str, quote: float, epoch: int) -> dict:
        """Route tick to correct market engine. Returns global alert if any."""
        now = datetime.now(timezone.utc)

        # Check lock
        if self.locked_until and now < self.locked_until:
            return {"action": "locked", "reason": self.lock_reason}

        engine = self.markets.get(symbol)
        if not engine:
            return {"action": "ignore"}

        result = engine.process_tick(quote, epoch)
        global_result = {"action": "tick", "symbol": symbol}

        # Propagate trade_signal from engine (triggers execute_real_trade in streamer)
        if result.get("action") == "trade_signal":
            return {
                "action": "trade_signal",
                "symbol": symbol,
                "cold": result.get("cold", 0),
                "trigger": result.get("trigger", 0),
                "pattern": result.get("pattern", "")
            }

        if result["entry"]:
            entry = result["entry"]
            self.daily_trades += 1
            if entry["win"]:
                self.daily_wins += 1
            self.daily_pnl += entry["pnl"]

            trade = {
                "timestamp": now.isoformat(),
                "market": symbol,
                "label": engine.label,
                "sequence": entry["sequence"],
                "predicted": entry["predicted"],
                "actual": entry["actual"],
                "win": entry["win"],
                "pnl": entry["pnl"],
                "daily_pnl": round(self.daily_pnl, 2),
            }
            self.trade_history.append(trade)

            # Log to file
            hf = LOG_DIR / "actuary_history.jsonl"
            with open(hf, "a") as f:
                f.write(json.dumps(trade) + "\n")

            # Sequence-level log
            seq_str = f"Shot #{entry['sequence']}/{MAX_SHOTS}"
            win_str = "🟢 WIN" if entry["win"] else "🔴 LOSS"
            logger.info(
                "[%s] %s | Digit %d → %d | %s | PnL: $%+.2f | Daily: $%+.2f",
                engine.label, seq_str, entry["predicted"], entry["actual"],
                win_str, entry["pnl"], self.daily_pnl,
            )

            global_result["action"] = "entry"
            global_result["entry"] = entry
            global_result["market"] = symbol

            # Check TP/SL
            lock = self._check_limits()
            if lock:
                global_result["lock"] = lock

        return global_result

    def _check_limits(self) -> str | None:
        now = datetime.now(timezone.utc)
        if self.daily_pnl >= DAILY_TP:
            self.locked_until = now + timedelta(hours=LOCK_TP_HOURS)
            self.lock_reason = f"TP ${DAILY_TP:.0f} reached"
            tg_alert(f"🏆🎯 <b>DAILY TP HIT!</b>\n${self.daily_pnl:.2f} (+{self.daily_wins} win / {self.daily_trades} trades)\n🔒 Locked {LOCK_TP_HOURS}h — HIT & RUN!")
            return f"🔒 TP HIT: ${self.daily_pnl:.2f} — Locked {LOCK_TP_HOURS}h"
        if self.daily_pnl <= DAILY_SL:
            self.locked_until = now + timedelta(hours=LOCK_SL_HOURS)
            self.lock_reason = f"SL ${abs(DAILY_SL):.0f} reached"
            tg_alert(f"🛑🆘 <b>DAILY SL HIT!</b>\n${self.daily_pnl:.2f} ({self.daily_trades} trades)\n🔒 Locked {LOCK_SL_HOURS}h — reset cognitive DB")
            return f"🔒 SL HIT: ${self.daily_pnl:.2f} — Locked {LOCK_SL_HOURS}h"
        return None

    def get_global_heatmap(self) -> dict:
        return {
            "markets": {sym: eng.get_heatmap() for sym, eng in self.markets.items()},
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_trades": self.daily_trades,
            "daily_wins": self.daily_wins,
            "locked": self.locked_until is not None,
            "lock_reason": self.lock_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_status(self) -> dict:
        return {
            "markets": list(SYMBOLS),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_trades": self.daily_trades,
            "daily_wins": self.daily_wins,
            "locked": self.locked_until is not None,
            "lock_reason": self.lock_reason,
        }

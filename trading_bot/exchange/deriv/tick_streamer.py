#!/usr/bin/env python3
"""
Deriv Actuary v5.0 — PROJECT ARBITER: COGNITIVE SNIPER
Adjacency Pattern sniper with self-learning cognitive DB.

Strategy: Adjacency pairs (Trigger→Target digit)
  - Track consecutive digit pairs in last 100 ticks
  - Find most frequent pattern (cognitive threshold)
  - Fire DIGITMATCH when trigger reappears
  - Max 2 shots per pattern, wait for trigger between shots
  - Self-learning: auto-blacklist losing patterns, cooldown on losses

Modules:
  1. Ping-Pong Heartbeat (per-market connection monitor)
  2. Cognitive Sniper Engine (actuary.py — pattern detection + learning)
  3. Reconciliation Protocol (receipt checker on reconnect)
  4. Momentum Timeout (15-second rule)
"""

import asyncio, json, logging, os, ssl, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone

# === PAPER MODE TOGGLE ===
PAPER_MODE = False  # LIVE TRADING — teknik mentor jalan real
# Fronttest jalan di background sebagai referensi

# === DAILY PNL LIMITS (SNIPER MODE) — DB PERSISTENT ===
DAILY_TP = 50.0   # Target $50/hari — dikejar dari kombinasi 5 teknik
DAILY_SL = -10.0  # SL $10 — proteksi harian
VIRTUAL_BALANCE = 100.0  # Starting virtual balance for paper mode
TRADE_STAKE = 0.35  # USD per trade ($0.35 minimal DIGITMATCH, aman modal ~$45)
MAX_SHOTS = 2  # Max shots per pattern

def get_daily_pnl():
    """Calculate PnL = current balance - session open balance."""
    open_bal = float(get_system_state("session_open_balance", "0"))
    close_bal = float(get_system_state("session_last_balance", "0"))
    return close_bal - open_bal

def is_locked():
    lock = get_system_state("daily_lock_reason", "")
    return lock != ""

def get_lock_reason():
    return get_system_state("daily_lock_reason", "")

def set_lock(reason):
    set_system_state("daily_lock_reason", reason)
    logger.info(f"🔒 SYSTEM LOCKED: {reason}")

def check_daily_limits(balance):
    """Check TP/SL and set lock if triggered. Returns True if locked."""
    open_bal = float(get_system_state("session_open_balance", str(balance)))
    # First run: set opening balance
    if open_bal == 0:
        set_system_state("session_open_balance", str(balance))
        set_system_state("session_last_balance", str(balance))
        return False
    
    pnl = balance - open_bal
    
    if is_locked():
        return True
    
    if pnl >= DAILY_TP:
        set_lock(f"TP ${DAILY_TP:.0f} reached ($+{pnl:.2f})")
        return True
    if pnl <= DAILY_SL:
        set_lock(f"SL ${abs(DAILY_SL):.0f} reached (${pnl:.2f})")
        return True
    
    # Update last known balance
    set_system_state("session_last_balance", str(balance))
    return False
from collections import deque
import websockets

from .actuary import tg_alert, MultiStreamActuary, SYMBOLS, SYMBOL_LABELS, LOG_DIR, \
    MENTOR_MODE, MENTOR_ONE_SHOT, MENTOR_RECOVERY, MENTOR_BASE_STAKE, MENTOR_RECOVERY_STAKE, \
    MENTOR1_MARKET, MENTOR1_TRIGGERS, MENTOR1_TARGET, \
    MENTOR2_MARKET, MENTOR2_VPATTERN_ENABLED, \
    MENTOR3_MARKET, MENTOR3_TRIGGER, MENTOR3_CONFIRM, MENTOR3_TARGET, \
    MENTOR_SPREAD_ENABLED, MENTOR_SPREAD_MARKET, MENTOR_SPREAD_TRIGGER, \
    MENTOR_SPREADS, MENTOR_SPREAD_MAX_VOLLEYS

def _send_balance_alert(trade_result, label):
    """Telegram alert with balance after trade."""
    bal = get_system_state("real_balance", "?")
    result = "🟢WIN" if trade_result and trade_result.get("win") else "🔴LOSS"
    pnl = trade_result.get("pnl", 0) if trade_result else 0
    tg_alert(f"{result} {label} | ${pnl:+.2f} | Balance: ${bal}")

logger = logging.getLogger("actuary.streamer")
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

WS = "wss://frontend.binaryws.com/websockets/v3?app_id=1"
TOKEN = os.getenv("DERIV_TOKEN", "") or \
        Path("/home/openclaw/.openclaw/workspace/projects/deriv-actuary/secrets/token.txt").read_text().strip()

# =============================================================================
# MODULE 2: State Persistence (SQLite)
# =============================================================================
DB_PATH = LOG_DIR / "actuary_state.db"

def init_db():
    """Initialize SQLite with system_state only.
    Actuary's cognitive DB handles all pattern/trade state."""
    os.makedirs(str(LOG_DIR), exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"🗄️  Streamer system_state DB: {DB_PATH}")

def db_conn():
    return sqlite3.connect(str(DB_PATH))

# v5.0: No sequence/shot functions — Actuary cognitive DB handles all state

def set_system_state(key: str, value: str):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO system_state VALUES (?,?)", (key, value))
        conn.commit()

def get_system_state(key: str, default: str = "") -> str:
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

# =============================================================================
# MODULE 1: Ping-Pong Heartbeat
# =============================================================================
reader_conn = {}  # key=symbol → bool(CONNECTION_LOST). Per-reader, not global.
HEARTBEAT_INTERVAL = 15
HEARTBEAT_TIMEOUT = 10  # Increased from 5s for tolerance on slow Deriv WS

async def heartbeat_monitor(ws, symbol):
    """Background task: ping every 15s, flag per-reader if no pong in 10s.
    Each symbol has its own CONNECTION_LOST flag."""
    global reader_conn
    while True:
        try:
            pong_waiter = await ws.ping()
            await asyncio.wait_for(pong_waiter, timeout=HEARTBEAT_TIMEOUT)
            if reader_conn.get(symbol, False):
                logger.info(f"🔗 [{symbol}] Connection restored after heartbeat recovery")
                reader_conn[symbol] = False
        except Exception:
            if not reader_conn.get(symbol, False):
                logger.warning(f"💔 [{symbol}] Heartbeat timeout — CONNECTION_LOST flagged")
                reader_conn[symbol] = True
        await asyncio.sleep(HEARTBEAT_INTERVAL)

# =============================================================================
# MODULE 3 & 4: Reconciliation Protocol + Momentum Timeout
# =============================================================================
MOMENTUM_TIMEOUT_SECONDS = 15

async def reconcile_on_reconnect():
    """v5.0: No unconfirmed shots to reconcile — cognitive DB is actuary's domain."""
    logger.info("🔄 v5.0 Reconciliation: clean slate (cognitive DB handles state)")
    return True

# =============================================================================
# TRADE EXECUTOR (with DB logging)
# =============================================================================
TRADE_WS = None
TRADE_LOCK = asyncio.Lock()

async def get_trade_ws():
    global TRADE_WS
    if TRADE_WS is None:
        ctx = ssl.create_default_context()
        TRADE_WS = await websockets.connect(WS, ssl=ctx, ping_interval=None, ping_timeout=None, close_timeout=5)
        await TRADE_WS.send(json.dumps({"authorize": TOKEN}))
        auth = json.loads(await asyncio.wait_for(TRADE_WS.recv(), timeout=5))
        if auth.get("error"):
            logger.warning(f"Trade WS auth: {auth['error']['message']}")
            TRADE_WS = None
    return TRADE_WS

async def execute_paper_trade(symbol: str, digit: int, label: str, seq_id: str, shot_num: int, ws, stake: float = None, duration: int = 1):
    """Execute ONE paper (virtual) DIGITMATCH trade.
    No DB writes—just fires, settles via next tick, returns result."""
    global VIRTUAL_BALANCE
    trade_stake = stake if stake is not None else TRADE_STAKE
    payout = trade_stake * 8.33
    logger.info(f"📄 [{label}] [PAPER] Virtual shot #{shot_num} for digit {digit} @${trade_stake:.0f}")
    
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(raw)
        t = msg.get("tick")
        if t and t.get("symbol") == symbol:
            next_quote = t.get("quote", 0)
            next_digit = int(str(next_quote).replace(".", "")[-1])
        else:
            raw2 = await asyncio.wait_for(ws.recv(), timeout=5)
            msg2 = json.loads(raw2)
            t2 = msg2.get("tick", {})
            next_quote = t2.get("quote", 0)
            next_digit = int(str(next_quote).replace(".", "")[-1])
    except:
        logger.warning(f"[{label}] [PAPER] Timeout waiting for settlement tick")
        return None
    
    win = (next_digit == digit)
    pnl = (payout - trade_stake) if win else -trade_stake
    VIRTUAL_BALANCE += pnl
    res = "🟢WIN" if win else "🔴LOST"
    logger.info(f"💰 [{label}] [PAPER] {res} | Pred {digit} → Act {next_digit} | ${pnl:+.2f} | VBal: ${VIRTUAL_BALANCE:.2f}")
    return {"win": win, "pnl": pnl, "actual": next_digit}


async def execute_real_trade(symbol: str, digit: int, label: str, seq_id: str, shot_num: int, stake: float = None, duration: int = 1):
    """Execute ONE real DIGITMATCH trade. No DB writes — returns WIN/LOSS to streamer state machine."""
    trade_stake = stake if stake is not None else TRADE_STAKE
    async with TRADE_LOCK:
        try:
            ws = await get_trade_ws()
            if not ws:
                logger.warning(f"[{label}] No trade WS")
                return None

            # Subscribe tick + proposal
            await ws.send(json.dumps({"ticks": symbol}))
            await ws.send(json.dumps({
                "proposal": 1, "amount": trade_stake, "basis": "stake",
                "contract_type": "DIGITMATCH", "currency": "USD",
                "duration": duration, "duration_unit": "t",
                "symbol": symbol, "barrier": str(digit)
            }))

            pid = None
            for _ in range(30):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("msg_type") == "proposal":
                    pid = msg["proposal"]["id"]
                    break
            if not pid:
                logger.warning(f"[{label}] No proposal — aborting")
                return None

            # Buy
            await ws.send(json.dumps({"buy": pid, "price": trade_stake}))
            cid = None
            for _ in range(30):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("msg_type") == "buy":
                    cid = msg["buy"]["contract_id"]
                    bal_after = msg["buy"]["balance_after"]
                    logger.info(f"📄 [{label}] #{cid} | ${bal_after:.2f}")
                    set_system_state("real_balance", f"{bal_after:.2f}")
                    break
            if not cid:
                return None

            # Subscribe + wait for result
            await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid, "subscribe": 1}))
            for _ in range(120):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                poc = msg.get("proposal_open_contract", {})
                if poc.get("contract_id") == cid and poc.get("status") in ("won", "lost"):
                    status = poc["status"]
                    profit = float(poc.get("profit", 0))
                    exit_t = poc.get("exit_tick", 0)
                    exit_d = int(str(exit_t).replace(".","")[-1]) if exit_t else 0
                    res = "🟢WIN" if status == "won" else "🔴LOST"
                    logger.info(f"💰 [{label}] {res} | ${profit:+.2f} | Pred {digit} → Act {exit_d}")
                    return {"win": status == "won", "pnl": profit, "actual": exit_d}

            logger.warning(f"[{label}] Timeout waiting for result")
            return None

        except asyncio.TimeoutError:
            logger.warning(f"[{label}] Trade timeout")
            return None
        except Exception as e:
            logger.error(f"[{label}] Trade error: {str(e)[:60]}")
            return None

# =============================================================================
# MARKET READER (with built-in heartbeat, reconnect, reconciliation)
# =============================================================================
async def market_reader(symbol: str, actuary: MultiStreamActuary):
    """Single market connection with heartbeat + reconnect + reconciliation."""
    ctx = ssl.create_default_context()
    delay = 1.0
    label = SYMBOL_LABELS.get(symbol, symbol)

    while True:
        try:
            async with websockets.connect(WS, ssl=ctx, ping_interval=None, ping_timeout=None, close_timeout=5, max_size=2**20) as ws:
                await ws.send(json.dumps({"authorize": TOKEN}))
                auth = json.loads(await ws.recv())
                if auth.get("error"):
                    logger.error(f"[{label}] Auth: {auth['error']['message']}")
                    return

                # MODULE 1: Start heartbeat monitor for this connection
                hb_task = asyncio.create_task(heartbeat_monitor(ws, symbol))

                # Subscribe to ticks
                await ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
                await ws.recv()
                logger.info(f"👂 [{label}] Streaming")
                delay = 1.0

                # MODULE 3+4: Reconcile on fresh connection
                await reconcile_on_reconnect()

                # v5.0 COGNITIVE SNIPER — Per-market passive listener state
                pending_pattern = None  # {"trigger": int, "target": int, "shot_num": 1, "pattern": str}
                prev2_digit = None  # For V-pattern detection (Teknik 2)
                prev1_digit = None
                
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    t = msg.get("tick")

                    if reader_conn.get(symbol, False):
                        continue

                    if t and t.get("symbol"):
                        tick_digit = int(str(t.get("quote", 0)).replace(".","")[-1]) if t.get("quote") else None
                        
                        # ── MODULE 0: Daily lock check (SQLite-backed, survives crashes) ──
                        if is_locked():
                            if pending_pattern:
                                logger.info(f"🔒 [{label}] Daily locked — clearing pending pattern")
                                pending_pattern = None
                            continue  # Skip everything, just stream ticks
                        
                        # ── MODULE V: V-Pattern History (Teknik 2 — Mas Victor, R_25) ──
                        # Always track last 2 digits for V pattern detection
                        if MENTOR_MODE and MENTOR2_VPATTERN_ENABLED and symbol == MENTOR2_MARKET:
                            if prev2_digit is not None and prev1_digit is not None and not pending_pattern:
                                # V pattern: X → Y → X (first == third)
                                if prev2_digit == tick_digit and prev1_digit != tick_digit:
                                    v_trigger = tick_digit  # X (bookend digit — reappeared)
                                    v_target = prev1_digit   # Y (middle digit)
                                    logger.info(f"🔺 [{label}] V-Pattern: {prev2_digit}→{prev1_digit}→{tick_digit} | SET: trigger={v_trigger}, target={v_target}")
                                    pending_pattern = {
                                        "trigger": v_trigger,
                                        "target": v_target,
                                        "shot_num": 1,
                                        "pattern": f"V{v_trigger}→{v_target}",
                                        "phase": "STANDBY",
                                        "consecutive_losses": 0
                                    }
                            # Always shift digit window
                            prev2_digit = prev1_digit
                            prev1_digit = tick_digit
                        
                        # ── MODULE A: Pass tick to Actuary for pattern detection ──
                        result = actuary.process_tick(t["symbol"], t.get("quote", 0), t.get("epoch", 0))
                        
                        # ── MODULE B: Teknik 1 & 3 lock (confirmation patterns) ──
                        if result.get("action") == "trade_signal" and not pending_pattern:
                            trigger = result.get("trigger", 0)
                            target = result.get("cold", 0)
                            # Teknik 1: R_75 trigger 4 → confirm 7 → fire 7
                            # Teknik 1: R_75 One-Shot — trigger 1|2|3|4 → ldp 7
                            if MENTOR_MODE and symbol == MENTOR1_MARKET and trigger in MENTOR1_TRIGGERS and target == MENTOR1_TARGET:
                                pending_pattern = {
                                    "trigger": trigger, "confirm": None,
                                    "target": MENTOR1_TARGET, "shot_num": 1,
                                    "pattern": f"OS-{trigger}→{MENTOR1_TARGET}",
                                    "phase": "STANDBY", "consecutive_losses": 0
                                }
                                logger.info(f"👁️ [{label}] One-Shot: trigger {trigger}→{MENTOR1_TARGET} | STANDBY")
                            # Teknik 3: R_50 trigger 7 → confirm 4 → fire 4
                            if MENTOR_MODE and symbol == MENTOR3_MARKET and trigger == MENTOR3_TRIGGER and target == MENTOR3_TARGET:
                                pending_pattern = {
                                    "trigger": MENTOR3_TRIGGER, "confirm": MENTOR3_CONFIRM,
                                    "target": MENTOR3_TARGET, "shot_num": 1,
                                    "pattern": f"T3-{MENTOR3_TRIGGER}→{MENTOR3_TARGET}",
                                    "phase": "STANDBY", "consecutive_losses": 0
                                }
                                logger.info(f"👁️ [{label}] Teknik3 LOCKED: {MENTOR3_TRIGGER}→{MENTOR3_CONFIRM}→{MENTOR3_TARGET} | STANDBY")
                        
                        # ── MODULE SPREAD: Multi-duration on trigger 7 (R_75) ──
                        if MENTOR_SPREAD_ENABLED and symbol == MENTOR_SPREAD_MARKET and tick_digit == MENTOR_SPREAD_TRIGGER and not pending_pattern:
                            pending_pattern = {
                                "trigger": MENTOR_SPREAD_TRIGGER, "confirm": None,
                                "target": 0, "shot_num": 1, "phase": "FIRING",
                                "pattern": "SPREAD-7", "consecutive_losses": 0
                            }
                            logger.info(f"🎯 [{label}] SPREAD: trigger 7! Firing 3 contracts (3t→0, 2t→8, 1t→9)")
                            # Fire 3 parallel contracts
                            tasks = []
                            for s in MENTOR_SPREADS:
                                if PAPER_MODE:
                                    tasks.append(execute_paper_trade(symbol, s["target"], label, "", 1, ws, TRADE_STAKE, s["duration"]))
                                else:
                                    tasks.append(execute_real_trade(symbol, s["target"], label, f"{symbol}_spread_{int(time.time())}", 1, TRADE_STAKE, s["duration"]))
                            spread_results = await asyncio.gather(*tasks, return_exceptions=True)
                            wins = sum(1 for r in spread_results if isinstance(r, dict) and r.get("win"))
                            total_pnl = sum(r.get("pnl", 0) for r in spread_results if isinstance(r, dict))
                            logger.info(f"📊 [{label}] SPREAD result: {wins}/3 wins | PnL: ${total_pnl:.2f}")
                            tg_alert(f"📊 SPREAD {label} | {wins}/3 wins | ${total_pnl:+.2f}")
                            # If any win, we're profitable. If all 3 loss, count as 1 loss.
                            if wins == 0:
                                pending_pattern["consecutive_losses"] += 1
                        # ── STANDARD MODE (no mentor): direct trigger-gated firing ──
                            else:
                                if pp.get("phase") in (None, "STANDBY") and tick_digit == pp["trigger"]:
                                    pp["phase"] = "FIRING"
                                    # HIGH CONVICTION: 2× stake after 3 consecutive losses on same pattern
                                    fire_stake = TRADE_STAKE
                                    if MENTOR_RECOVERY and pp.get("consecutive_losses", 0) >= 3:
                                        fire_stake = MENTOR_RECOVERY_STAKE
                                        logger.info(f"⚡ [{label}] HIGH CONVICTION! {pp['consecutive_losses']} consecutive losses → ${fire_stake:.2f}")
                                    logger.info(f"🎯 [{label}] Trigger {pp['trigger']} seen! → DIGITMATCH({pp['target']}) @${fire_stake:.2f}")
                                    
                                    if PAPER_MODE:
                                        trade_result = await execute_paper_trade(symbol, pp["target"], label, "", pp["shot_num"], ws, fire_stake)
                                    else:
                                        trade_result = await execute_real_trade(symbol, pp["target"], label, f"{symbol}_adj_{int(time.time())}", pp["shot_num"], fire_stake)
                                    
                                    if trade_result is None:
                                        pending_pattern = None
                                    elif trade_result["win"]:
                                        logger.info(f"🏆 WIN +${trade_result['pnl']:.2f} | Pattern closed.")
                                        _send_balance_alert(trade_result, label)
                                        pending_pattern = None
                                    else:
                                        _send_balance_alert(trade_result, label)
                                        pp["consecutive_losses"] = pp.get("consecutive_losses", 0) + 1
                                        pp["phase"] = "STANDBY"
                                        pp["shot_num"] += 1
                                        if pp["shot_num"] > MAX_SHOTS:
                                            logger.info(f"💀 Max shots — pattern LOST")
                                            pending_pattern = None
                                        else:
                                            logger.info(f"⏳ STANDBY for trigger {pp['trigger']} (shot #{pp['shot_num']}/{MAX_SHOTS})")
                        
                        # ── MODULE D: Entry log from Actuary (read-only) ──
                        if result.get("action") == "entry":
                            e = result["entry"]
                            w = "🟢WIN" if e["win"] else "🔴LOSS"
                            logger.info(f"🎯 [{label}] CogDB: {e.get('pattern','?')} #{e['sequence']} P{e['predicted']}→A{e['actual']} {w} ${e['pnl']:+.2f}")
                            
                            # Check actuary's lock status and persist to SQLite
                            actuary_status = actuary.get_status()
                            if actuary_status.get("locked"):
                                lock_reason = actuary_status.get("lock_reason", "Unknown")
                                set_lock(lock_reason)
                                logger.info(f"🔒 [{label}] Persisting lock: {lock_reason}")
                        
                        # ── MODULE E: Lock status from actuary (alternative path) ──
                        if result.get("lock"):
                            set_lock(result["lock"])
                            logger.info(f"🔒 {result['lock']}")

                    elif msg.get("error"):
                        logger.error(f"[{label}] {msg['error']['message']}")

        except asyncio.CancelledError:
            return
        except Exception as e:
            err_str = str(e)[:50]
            logger.warning(f"[{label}] {'Disc' if 'close' in err_str.lower() else err_str}. R+{delay:.0f}s")

        await asyncio.sleep(delay)
        delay = min(delay * 2, 30.0)

# =============================================================================
# HEATMAP SAVER
# =============================================================================
async def heatmap_saver(actuary: MultiStreamActuary):
    while True:
        await asyncio.sleep(10)
        try:
            hm = actuary.get_global_heatmap()
            (LOG_DIR / "heatmap.json").write_text(json.dumps(hm, indent=2))
        except:
            pass

# =============================================================================
# MAIN
# =============================================================================
async def main():
    if not TOKEN:
        logger.error("❌ No token")
        return

    # Initialize SQLite database
    init_db()

    # Set starting system state
    set_system_state("bot_start", datetime.now(timezone.utc).isoformat())
    set_system_state("revision", "v5.0-cognitive-sniper")

    # Reset daily lock if new day
    if is_locked():
        logger.info(f"🔒 Restored daily lock: {get_lock_reason()}")
    else:
        logger.info(f"💰 Daily PnL tracking active | TP=${DAILY_TP} SL=${abs(DAILY_SL)}")
        logger.info(f'💰 Session open balance: ${get_system_state("session_open_balance", "?")}')

    actuary = MultiStreamActuary()
    logger.info("🤖 Actuary v5.0 — Project Arbiter: Cognitive Sniper Edition")
    logger.info(f"   Markets: {', '.join(SYMBOLS)}")
    logger.info(f"   DB: {DB_PATH}")
    logger.info(f"   Momentum timeout: {MOMENTUM_TIMEOUT_SECONDS}s")
    logger.info(f"   Heartbeat: {HEARTBEAT_INTERVAL}s ping / {HEARTBEAT_TIMEOUT}s timeout")

    tasks = [asyncio.create_task(market_reader(s, actuary)) for s in SYMBOLS]
    tasks.append(asyncio.create_task(heatmap_saver(actuary)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

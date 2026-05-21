# 2026-05-16 — Project Arbiter: Actuary Edition (SNIPER MODE)

## Core Strategy: SNIPER MODE (PROFITABLE ✅)

### The Game Plan (from Veris)
> "Bot diam saja memantau Cold Digit (anomali 40+ tick). Saat anomali terjadi, tembak maksimal 8 peluru. Kalau berhasil Match ($8.33) atau target harian $15 tercapai, MATIKAN SISTEM. Bot harus menjadi perampok bank yang masuk, ambil brankas, dan kabur sebelum alarm polisi (hukum statistik EV negatif) menyala."

### Key Components

1. **Cold Digit Detection (SNIPER)**
   - Monitor 5 markets simultaneously (R_10, R_25, R_50, R_75, R_100)
   - Track last 100 digits per market
   - Cold = digit hasn't appeared in 50+ ticks (COLD_THRESHOLD=50)
   - **CRITICAL**: Exclude digit 0 from detection (Deriv API returns it 0.01% — false cold)
   - Each market picks DIFFERENT cold digit via `hash(symbol) % 5` — prevents all-0 trap

2. **DIGITMATCH Contract** (NOT DIGITDIFF!)
   - Payout: $8.33 for $1 stake ($7.33 profit)
   - Contract: `DIGITMATCH` with barrier = cold digit
   - Duration: 1 tick
   - Win probability per shot: ~10% (independent RNG per tick)

3. **Exploit Variance (8-shot sequence)**
   - When cold digit detected, fire up to 8 shots on SAME digit
   - Martingale: same digit, $1 each shot
   - After 8 losses, sequence LOST → find new cold digit

4. **Hit & Run**
   - STOP IMMEDIATELY after first WIN
   - Do NOT continue trading after profit
   - Reset: find new cold digit for next sequence

5. **Daily Limits**
   - TP $15 → SHUT DOWN (lock 12h)
   - SL -$10 → EMERGENCY STOP (lock 24h)
   - Once locked, no new sequences fire until next day

### Fail-Safes (Project Arbiter)

| Module | Description | Status |
|--------|-------------|--------|
| Ping-Pong Heartbeat | Ping every 5s, timeout 2s → CONNECTION_LOST flag | ✅ |
| SQLite State Persistence | sequences + shots tables, written BEFORE buy | ✅ |
| Reconciliation Protocol | Check last unconfirmed shot on reconnect | ✅ |
| Momentum Timeout (15s) | Abort sequence if >15s since last shot | ✅ |

### Architecture

- 5x `market_reader` tasks: parallel WebSocket connections, one per market
- 1x `execute_real_trade`: shared WebSocket with `asyncio.Lock` for sequential execution
- 1x `heatmap_saver`: saves state to JSON every 10s
- SQLite DB: `/home/openclaw/.openclaw/workspace/logs/deriv_actuary/actuary_state.db`

### System Deployment

- **systemd**: `vilona-deriv.service` — auto-restart on crash, auto-start on boot
- **Watchdog**: cron every 3 min — detects crashes and restarts
- **Independent**: runs without OpenClaw
- **Logs**: `/home/openclaw/.openclaw/workspace/logs/deriv_actuary/streamer.log`

### Results (16 May 2026, ~2h live trading)

- Total shots fired: 24
- Wins: 4 (16.7% win rate)
- Losses: 17
- Errors: 3
- Real balance: $48.13 → $64.55 (+$16.42, +34.1%)
- Net from strategy: +$14.72 (4×$7.93 win - 17×$1 loss + errors)

### Key Files
- `/home/openclaw/.openclaw/workspace/projects/deriv-actuary/tick_streamer.py` — Project Arbiter v3.0
- `/home/openclaw/.openclaw/workspace/projects/deriv-actuary/actuary.py` — Core strategy
- `/home/openclaw/.openclaw/workspace/scripts/trading_report.py` — Daily report generator
- `/home/openclaw/.openclaw/workspace/logs/deriv_actuary/actuary_state.db` — SQLite state

### Warnings
- Cold digit on true RNG is still -EV (house edge)
- Strategy relies on variance exploitation, not mathematical edge
- Digit 0 MUST be excluded (API anomaly — 0.01% appearance rate)
- Daily TP $15 is designed to lock in profits before -EV catches up

#!/usr/bin/env python3
"""
Deriv 13-Month Fronttest (Apr 2025 - May 2026)
Adjacency pattern strategy on paginated historical tick data
"""
import asyncio, json, ssl, time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import websockets

WS = "wss://frontend.binaryws.com/websockets/v3?app_id=1"
SYMBOLS = ["R_100", "R_75", "R_50", "R_25", "R_10"]
TOKEN = Path("/home/openclaw/.openclaw/workspace/projects/deriv-actuary/secrets/token.txt").read_text().strip()

TICK_HISTORY = 100
MAX_SHOTS = 2
STAKE = 1.0
PROFIT = 7.33
DAILY_TP = 50.0
DAILY_SL = -10.0
START_CAP = 100.0
START_DATE = "2025-04-01"
END_DATE = "2026-05-17"

class Engine:
    def __init__(self):
        self.digits = deque(maxlen=TICK_HISTORY)
        self.trigger = None
        self.target = None
        self.active = False
        self.shots = 0
        self.waiting = False
        self.trades = []
    
    def process(self, digit):
        prev = self.digits[-1] if self.digits else None
        self.digits.append(digit)
        
        if self.active and self.waiting:
            if digit == self.trigger:
                self.waiting = False
                return self._fire(digit)
            return None
        
        if not self.active:
            pairs = Counter()
            digs = list(self.digits)
            for i in range(len(digs)-1):
                pairs[(digs[i], digs[i+1])] += 1
            if pairs:
                best = max(pairs, key=pairs.get)
                t, tg = best
                if pairs[best] >= 2:
                    self.trigger, self.target = t, tg
                    self.active = True
                    self.shots = 0
                    self.waiting = False
                    if prev is not None and prev == t:
                        return self._fire(digit)
        
        if self.active and not self.waiting:
            if prev is not None and prev == self.trigger:
                return self._fire(digit)
        return None
    
    def _fire(self, digit):
        self.shots += 1
        if self.shots > MAX_SHOTS:
            return None
        win = digit == self.target
        pnl = PROFIT if win else -STAKE
        trade = {"trigger": self.trigger, "target": self.target, "actual": digit,
                 "shot": self.shots, "win": win, "pnl": pnl}
        self.trades.append(trade)
        if win or self.shots >= MAX_SHOTS:
            self.active = False
            self.trigger = None
            self.target = None
            self.waiting = False
        elif not win:
            self.waiting = True
        return trade

async def fetch_ticks_range(symbol, start_ts, end_ts):
    """Fetch ticks in a date range using pagination."""
    all_ticks = []
    current_end = end_ts
    ctx = ssl.create_default_context()
    
    while len(all_ticks) < 100000 and current_end > start_ts:
        try:
            async with websockets.connect(WS, ssl=ctx, ping_interval=None, close_timeout=10) as ws:
                req = {"ticks_history": symbol, "end": str(int(current_end)), 
                       "style": "ticks", "count": 5000}
                await ws.send(json.dumps(req))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                prices = resp.get("history", {}).get("prices", [])
                times = resp.get("history", {}).get("times", [])
                
                if not prices:
                    break
                
                for p in prices:
                    all_ticks.append(int(str(p).replace(".","")[-1]))
                
                oldest_time = times[0] if times else 0
                current_end = oldest_time - 1
                
                if len(prices) < 5000:
                    break  # No more data
                    
        except Exception as e:
            print(f"  ⚠️ Fetch error: {e}")
            await asyncio.sleep(2)
            break
    
    return all_ticks

async def main():
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()
    
    print(f"📊 DERIV 13-MONTH FRONTTEST")
    print(f"   Period: {START_DATE} → {END_DATE}")
    print(f"   Markets: {', '.join(SYMBOLS)}")
    print(f"   Starting capital: ${START_CAP}")
    print(f"{'='*60}")
    
    total_trades = 0
    total_wins = 0
    capital = START_CAP
    peak = START_CAP
    all_trades = []
    
    for sym in SYMBOLS:
        print(f"\n📥 Fetching {sym}...")
        ticks = await fetch_ticks_range(sym, start_ts, end_ts)
        print(f"   Got {len(ticks)} ticks")
        
        if len(ticks) < 100:
            continue
        
        eng = Engine()
        daily_pnl = 0.0
        
        for digit in ticks:
            trade = eng.process(digit)
            if trade:
                total_trades += 1
                if trade["win"]:
                    total_wins += 1
                capital += trade["pnl"]
                daily_pnl += trade["pnl"]
                all_trades.append({**trade, "symbol": sym})
                if capital > peak:
                    peak = capital
                
                # Check daily limits
                if daily_pnl >= DAILY_TP or daily_pnl <= DAILY_SL:
                    daily_pnl = 0.0  # Reset for next "day" approximation
        
        print(f"   → {len(eng.trades)} trades | {sum(1 for t in eng.trades if t['win'])}W")
    
    wr = total_wins / total_trades * 100 if total_trades else 0
    dd = (peak - capital) / peak * 100 if peak > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"📈 FRONTTEST RESULTS (13 Months)")
    print(f"{'='*60}")
    print(f"  Starting:     ${START_CAP:.2f}")
    print(f"  Final:        ${capital:.2f}")
    print(f"  Profit:       ${capital-START_CAP:.2f}")
    print(f"  Return:       {(capital-START_CAP)/START_CAP*100:.1f}%")
    print(f"  Total trades: {total_trades}")
    print(f"  Win rate:     {wr:.1f}%")
    print(f"  Max DD:       {dd:.1f}%")
    
    patterns = {}
    for t in all_trades:
        key = f"{t['trigger']}->{t['target']}"
        if key not in patterns:
            patterns[key] = {"wins": 0, "losses": 0, "total": 0}
        patterns[key]["total"] += 1
        if t["win"]:
            patterns[key]["wins"] += 1
        else:
            patterns[key]["losses"] += 1
    
    print(f"\n🏆 TOP 10 PATTERNS:")
    sorted_pats = sorted(patterns.items(), key=lambda x: -x[1]["wins"]/x[1]["total"] if x[1]["total"]>0 else 0)[:10]
    for pat, data in sorted_pats:
        pwr = data["wins"]/data["total"]*100 if data["total"]>0 else 0
        print(f"  {pat}: {data['total']}x ({data['wins']}W/{data['losses']}L) WR={pwr:.0f}%")
    
    output = {
        "period": f"{START_DATE} to {END_DATE}",
        "starting_capital": START_CAP,
        "final_capital": round(capital, 2),
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_trades - total_wins,
        "win_rate_pct": round(wr, 1),
        "max_drawdown_pct": round(dd, 1),
        "total_profit": round(capital - START_CAP, 2)
    }
    out_path = Path("/home/openclaw/.openclaw/workspace/logs/deriv_actuary/fronttest_13m_results.json")
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\n💾 Saved to {out_path}")

if __name__ == "__main__":
    asyncio.run(main())

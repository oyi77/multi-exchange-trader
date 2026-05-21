#!/usr/bin/env python3
"""
Vilona Daily Trading Report — sent to Telegram after TP/SL hit
Generates and outputs a report that can be cron'd or called by daemon.
"""
import asyncio, json, ssl, websockets, sqlite3, os, sys
from datetime import datetime
from pathlib import Path

BASE = Path("/home/openclaw/.openclaw/workspace")
LOG_DIR = BASE / "logs" / "deriv_actuary"
DB_PATH = LOG_DIR / "actuary_state.db"

async def generate_report():
    token_path = BASE / "projects/deriv-actuary/secrets/token.txt"
    if not token_path.exists():
        return "❌ Deriv token not found"
    
    token = token_path.read_text().strip()
    
    # Get balance
    try:
        ctx = ssl.create_default_context()
        async with websockets.connect("wss://frontend.binaryws.com/websockets/v3?app_id=1", ssl=ctx) as ws:
            await ws.send(json.dumps({"authorize": token}))
            r = json.loads(await ws.recv())
            balance = r["authorize"]["balance"]
    except:
        balance = "?"
    
    # DB stats
    try:
        conn = sqlite3.connect(str(DB_PATH))
        wins = conn.execute("SELECT COUNT(*) FROM shots WHERE status='WON'").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM shots WHERE status='LOST'").fetchone()[0]
        errors = conn.execute("SELECT COUNT(*) FROM shots WHERE status NOT IN ('WON','LOST') AND status != 'PENDING'").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
        lock_row = conn.execute("SELECT value FROM system_state WHERE key='daily_lock'").fetchone()
        lock_reason = lock_row[0] if lock_row else ""
        recent = conn.execute(
            "SELECT shot_number, predicted_digit, actual_digit, status, pnl, created_at "
            "FROM shots ORDER BY created_at DESC LIMIT 3"
        ).fetchall()
        conn.close()
    except:
        return "❌ DB not ready yet"
    
    # Build report
    win_rate = wins / total * 100 if total > 0 else 0
    status_str = "🔒 **LOCKED**: " + lock_reason if lock_reason else "🔄 **RUNNING** — scanning"
    
    report = f"""📊 **DAILY TRADING REPORT**
{datetime.now().strftime('%A, %d %B %Y %H:%M WIB')}

━━━━━━━━━━━━━━━━━━━━━

**🟢 DERIV ACTUARY (SNIPER MODE):**
💰 Balance: **${balance}**
📋 Shots: {total} ({wins}W/{losses}L/{errors}E)
🎯 Win rate: {win_rate:.1f}%
{status_str}
📈 http://localhost:6567

━━━━━━━━━━━━━━━━━━━━━
🤖 Vilona 🔥"""

    if recent:
        report += "\n\n📋 **Last Trades:**"
        for s in recent[:3]:
            r = "🟢WIN" if s[3] == "WON" else "🔴LOST"
            report += f"\n  #{s[0]} P{s[1]}→A{s[2] or '?'} {r} ${float(s[4]):+.2f}"
    
    report += "\n\n---"
    report += "\n📌 *Report generated automatically after trading session ends*"
    
    return report

if __name__ == "__main__":
    report = asyncio.run(generate_report())
    print(report)

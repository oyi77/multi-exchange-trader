#!/usr/bin/env python3
"""
Project Arbiter v5.0 — Autonomous Watchdog
Monitors bot health every 3 minutes. Auto-restarts on crash.
Reports errors to Telegram. No human needed.
"""
import os, sys, json, time, sqlite3, subprocess, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

BASE = Path("/home/openclaw/.openclaw/workspace")
LOG_DIR = BASE / "logs/deriv_actuary"
COG_DB = LOG_DIR / "cognitive_memory.db"
STATE_DB = LOG_DIR / "actuary_state.db"
HEALTH_FILE = LOG_DIR / "watchdog_health.json"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "157228659")

SERVICE = "vilona-deriv.service"

def log(msg):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] [WATCHDOG] {msg}"
    print(line)
    (LOG_DIR / "watchdog.log").open("a").write(line + "\n")

def tg_alert(msg):
    if not TG_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg}).encode()
        urllib.request.urlopen(url, data=data, timeout=8)
    except:
        pass

def check_bot():
    """Returns (healthy: bool, issues: list)"""
    issues = []
    
    # 1. Is service active?
    res = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True, timeout=10)
    if res.stdout.strip() != "active":
        issues.append(f"Service {SERVICE}: {res.stdout.strip()}")
        return False, issues
    
    # 2. Is tick_streamer process running?
    res2 = subprocess.run(["pgrep", "-f", "tick_streamer"], capture_output=True, text=True, timeout=5)
    if not res2.stdout.strip():
        issues.append("tick_streamer process not found despite service active")
        return False, issues
    
    # 3. Dashboard API responsive?
    try:
        resp = urllib.request.urlopen("http://localhost:6567/status", timeout=5)
        status = json.loads(resp.read())
        if "No dashboard" in str(status):
            issues.append("Dashboard not responding")
    except Exception as e:
        issues.append(f"Dashboard API: {str(e)[:50]}")
    
    # 4. Logs too old? (>5 min no updates)
    try:
        last_mod = os.path.getmtime(LOG_DIR / "streamer.log")
        age_min = (time.time() - last_mod) / 60
        if age_min > 5:
            issues.append(f"Streamer log stale: {age_min:.0f}min")
    except:
        issues.append("Cannot check streamer log age")
    
    # 5. Disk space OK?
    st = os.statvfs(str(BASE))
    free_gb = st.f_bavail * st.f_frsize / (1024**3)
    pct = (1 - st.f_bavail / st.f_blocks) * 100
    if pct > 90:
        issues.append(f"Disk: {pct:.0f}% full ({free_gb:.1f}GB free)")
    
    return len(issues) == 0, issues

def auto_heal(issues):
    """Try to fix common issues without human intervention."""
    log(f"🩺 Auto-healing {len(issues)} issue(s)...")
    
    for issue in issues:
        if "Service" in issue or "process" in issue:
            log("🔄 Restarting service...")
            tg_alert(f"⚠️ <b>Arbiter v5.0 — Auto-Restart</b>\nReason: {issue}")
            subprocess.run(["sudo", "systemctl", "restart", SERVICE], timeout=30)
            time.sleep(5)
            return True
        
        if "Dashboard" in issue:
            log("🔄 Dashboard might be dead — restarting full daemon...")
            subprocess.run(["sudo", "systemctl", "restart", SERVICE], timeout=30)
            time.sleep(5)
            return True
        
        if "stale" in issue:
            log("🔄 Streamer might be hung — restarting...")
            subprocess.run(["sudo", "systemctl", "restart", SERVICE], timeout=30)
            time.sleep(5)
            return True
    
    return False

def main():
    log("🔍 Watchdog check starting...")
    healthy, issues = check_bot()
    
    if healthy:
        log("✅ All systems healthy")
        # Save health state
        (LOG_DIR / "watchdog_last_ok.txt").write_text(datetime.now().isoformat())
    else:
        log(f"⚠️ {len(issues)} issue(s): {'; '.join(issues)}")
        
        # Try auto-heal
        healed = auto_heal(issues)
        
        if not healed:
            log("🚨 Cannot auto-heal — escalating to human")
            tg_alert(f"🚨 <b>Arbiter v5.0 — Needs Attention</b>\nIssues: {'; '.join(issues)}\nAuto-heal failed")
    
    # Save health snapshot
    try:
        resp = urllib.request.urlopen("http://localhost:6567/status", timeout=5)
        status = json.loads(resp.read())
        (LOG_DIR / "watchdog_health.json").write_text(json.dumps(status, indent=2))
    except:
        pass
    
    # Report first-trade stats if available
    try:
        conn = sqlite3.connect(str(COG_DB))
        patterns = conn.execute("SELECT COUNT(*) FROM cognitive_memory").fetchone()[0]
        total_trades = conn.execute("SELECT COALESCE(SUM(total_attempts),0) FROM cognitive_memory").fetchone()[0]
        conn.close()
        if total_trades > 0:
            log(f"📊 DB: {patterns} patterns, {total_trades} total trades")
    except:
        pass
    
    log("✅ Watchdog cycle complete")

if __name__ == "__main__":
    main()

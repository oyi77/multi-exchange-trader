#!/usr/bin/env python3
"""
Deriv Actuary — Multi-Market Dashboard
Port: 6567 | Dark charcoal UI | No login
"""

import json, logging
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

logger = logging.getLogger("actuary.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

LOG_DIR = Path("/home/openclaw/.openclaw/workspace/logs/deriv_actuary")
app = FastAPI(title="Actuary Dashboard", version="2.0")

MARKET_COLORS = {
    "R_100": "#ff4444", "R_75": "#00d4ff",
    "R_50": "#00ff88", "R_25": "#ffaa00", "R_10": "#ff66ff"
}

@app.get("/heatmap")
async def get_heatmap():
    try: return json.loads((LOG_DIR / "heatmap.json").read_text())
    except: return {"markets": {}, "daily_pnl": 0}

@app.get("/history")
async def get_history():
    hf = LOG_DIR / "actuary_history.jsonl"
    if not hf.exists(): return {"trades": []}
    trades = [json.loads(l) for l in hf.read_text().strip().split("\n") if l]
    return {"trades": trades[-50:]}

@app.get("/status")
async def get_status():
    try: hm = json.loads((LOG_DIR / "heatmap.json").read_text())
    except: hm = {}
    return {
        "system": "Actuary v2.0",
        "markets": list(MARKET_COLORS.keys()),
        "daily_pnl": hm.get("daily_pnl", 0),
        "daily_trades": hm.get("daily_trades", 0),
        "daily_wins": hm.get("daily_wins", 0),
        "locked": hm.get("locked", False),
        "lock_reason": hm.get("lock_reason", ""),
    }

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Actuary — Multi-Market Cold Digit Hunter</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d0d1a;color:#e0e0e0;font-family:'Courier New',monospace;padding:15px}
h1{color:#00d4ff;font-size:1.3em;border-bottom:1px solid #333;padding-bottom:8px;margin-bottom:15px}
h2{color:#888;font-size:0.9em;margin:15px 0 8px}
.market-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:10px 0}
.market-panel{background:#16213e;border:1px solid #333;border-radius:8px;padding:10px}
.market-panel h3{font-size:0.8em;margin-bottom:8px}
.digit-bar{display:flex;gap:2px;height:50px;align-items:end;margin:5px 0}
.digit-bar .bar{width:100%;border-radius:2px 2px 0 0;transition:height 0.3s}
.digit-grid{display:grid;grid-template-columns:repeat(10,1fr);gap:3px;margin:5px 0}
.digit-cell{text-align:center;padding:4px 2px;border-radius:4px;font-size:0.75em;background:#0d0d1a}
.digit-cell.cold{background:#3d0000;color:#ff4444;animation:pulse 1s infinite}
.digit-cell .num{font-size:1.2em;font-weight:bold}
.digit-cell .info{font-size:0.65em;color:#666}
.status-bar{display:flex;gap:15px;margin:10px 0;flex-wrap:wrap}
.stat{background:#16213e;padding:8px 12px;border-radius:6px;border:1px solid #333}
.stat .lbl{font-size:0.7em;color:#888}
.stat .val{font-size:1.1em;font-weight:bold}
.green{color:#00ff88}.red{color:#ff4444}.blue{color:#00d4ff}
.locked-banner{background:#3d0000;color:#ff4444;padding:8px;border-radius:6px;text-align:center;margin:8px 0;font-weight:bold}
.trade-log{background:#0d0d1a;padding:8px;border-radius:6px;max-height:150px;overflow-y:auto;font-size:0.8em}
.win{color:#00ff88}.loss{color:#ff4444}
@keyframes pulse{0%{opacity:1}50%{opacity:0.3}100%{opacity:1}}
</style>
</head>
<body>
<h1>🤖 Actuary v2.0 — Multi-Stream Cold Digit Hunter</h1>
<div class="status-bar" id="status-bar"></div>
<div id="lock-banner" class="locked-banner" style="display:none"></div>
<div id="market-panels" class="market-grid"></div>
<h2>📊 Recent Trades</h2>
<div class="trade-log" id="trade-log">Waiting...</div>
<script>
const LABELS={"R_100":"Vol 100","R_75":"Vol 75","R_50":"Vol 50","R_25":"Vol 25","R_10":"Vol 10"};
const COLORS={"R_100":"#ff4444","R_75":"#00d4ff","R_50":"#00ff88","R_25":"#ffaa00","R_10":"#ff66ff"};
function update(){
fetch('/heatmap').then(r=>r.json()).then(d=>{
const sb=document.getElementById('status-bar');
const pnl=d.daily_pnl||0;
sb.innerHTML=`
<div class="stat"><div class="lbl">Daily PnL</div><div class="val ${pnl>=0?'green':'red'}">$${pnl.toFixed(2)}</div></div>
<div class="stat"><div class="lbl">Trades</div><div class="val blue">${d.daily_trades||0}</div></div>
<div class="stat"><div class="lbl">Win Rate</div><div class="val blue">${d.daily_trades?((d.daily_wins/d.daily_trades)*100).toFixed(0)+'%':'0%'}</div></div>
<div class="stat"><div class="lbl">Markets</div><div class="val blue">5</div></div>`;
const lb=document.getElementById('lock-banner');
lb.style.display=d.locked?'block':'none';
lb.textContent=d.locked?'🔒 LOCKED: '+d.lock_reason:'';
const panels=document.getElementById('market-panels');
panels.innerHTML='';
const markets=d.markets||{};
Object.entries(markets).forEach(([sym,data])=>{
const panel=document.createElement('div');
panel.className='market-panel';
const cold=data.cold_digit;
const seq=data.sequence_active?' (S'+data.sequence_count+'/8)':'';
panel.innerHTML='<h3 style="color:'+COLORS[sym]+'">'+LABELS[sym]+'</h3>'+
'<div class="digit-grid">'+Array.from({length:10},(_,i)=>{
const c=data.digits?.[String(i)]||0;
const ls=data.last_seen?.[String(i)]||0;
const isCold=ls>=40;
return '<div class="digit-cell'+(isCold?' cold':'')+'">'+
'<div class="num">'+i+'</div>'+
'<div class="info">'+c+'x / '+ls+'t</div></div>';
}).join('')+'</div>'+
(cold!==null?'<div style="color:#ff4444;font-size:0.8em;margin-top:5px">❄️ Digit '+cold+' COLD'+seq+'</div>':'');
panels.appendChild(panel);
});
});
fetch('/history').then(r=>r.json()).then(d=>{
const log=document.getElementById('trade-log');
const trades=d.trades||[];
if(!trades.length)return;
log.innerHTML=trades.reverse().slice(0,25).map(t=>{
const cls=t.win?'win':'loss';
const m=LABELS[t.market]||t.market;
return '<div class="'+cls+'">['+m+'] Seq #'+t.sequence+'/8 | '+t.predicted+'→'+t.actual+' | '+(t.win?'🟢 WIN':'🔴 LOSS')+' | $'+t.pnl.toFixed(2)+' | Daily $'+t.daily_pnl.toFixed(2)+'</div>';
}).join('');
});
}
setInterval(update,2000);update();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def dashboard(): return DASHBOARD_HTML

if __name__ == "__main__":
    uvicorn.run("dashboard_api:app", host="0.0.0.0", port=6567, reload=False)

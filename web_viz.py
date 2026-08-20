"""
Web Visualization Server for the Pump.fun Lifecycle CLI.

Provides a browser-based dashboard with live-updating charts using Chart.js.
Shows: price charts, wallet balances, trade log, P&L summary.

Adapted to use pure Python stdlib (http.server) instead of Flask.
No pip dependencies required.

Run: python3 web_dashboard.py            # Start on http://localhost:8765
Run: python3 web_dashboard.py --port 9000  # Custom port

Then open the URL in any browser.
"""

import os
import sys
import json
import time
import random
import threading
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    from trading_engine import get_price_feed, get_trending_pairs
    _HAS_TRADING_ENGINE = True
except ImportError:
    _HAS_TRADING_ENGINE = False

# ─── Shared State ───
STATE = {
    "wallets": [],
    "prices": {},
    "trending": [],
    "trade_log": [],
    "current_price": 0.0,
    "price_history": [],
    "mc_usd": 0.0,
    "phase": "IDLE",
    "last_update": time.time(),
    "alerts": [],
    # Enhanced fields
    "wallet_profiles": [],
    "bubble_risk": 0.0,
    "take_profit_tiers": [],  # Track which TP tiers triggered
    "diversity_score": 0.0,
    "strategy": "",
}

STATE_LOCK = threading.Lock()
SERVER = None


def update_state(phase: str = None, price: float = None, mc_usd: float = None,
                 log_entry: dict = None, alert: str = None,
                 wallets: list = None, bubble_risk: float = None,
                 tp_tier: str = None, diversity_score: float = None,
                 strategy: str = None):
    """Thread-safe state update. Called by lifecycle CLI or trading loop."""
    with STATE_LOCK:
        if phase:
            STATE["phase"] = phase
        if price is not None:
            STATE["current_price"] = price
            STATE["price_history"].append({"t": time.time(), "p": price})
            if len(STATE["price_history"]) > 100:
                STATE["price_history"] = STATE["price_history"][-100:]
        if mc_usd is not None:
            STATE["mc_usd"] = mc_usd
        if log_entry:
            STATE["trade_log"].append(log_entry)
            if len(STATE["trade_log"]) > 200:
                STATE["trade_log"] = STATE["trade_log"][-200:]
        if alert:
            STATE["alerts"].append({"t": time.strftime("%H:%M:%S"), "msg": alert})
            if len(STATE["alerts"]) > 50:
                STATE["alerts"] = STATE["alerts"][-50:]
        # Enhanced state updates
        if wallets is not None:
            STATE["wallets"] = wallets
        if bubble_risk is not None:
            STATE["bubble_risk"] = bubble_risk
        if tp_tier is not None:
            STATE["take_profit_tiers"].append({"tier": tp_tier, "t": time.time()})
        if diversity_score is not None:
            STATE["diversity_score"] = diversity_score
        if strategy is not None:
            STATE["strategy"] = strategy
        STATE["last_update"] = time.time()


def get_state() -> dict:
    """Return a snapshot of the current dashboard state (thread-safe)."""
    with STATE_LOCK:
        import copy
        return copy.deepcopy(STATE)


# ─── Data Collection Thread ───
def _update_loop():
    """Background thread that periodically refreshes market data."""
    while True:
        try:
            if _HAS_TRADING_ENGINE:
                prices = get_price_feed("So11111111111111111111111111111111111111112")
                if prices:
                    with STATE_LOCK:
                        STATE["prices"] = prices
                trending = get_trending_pairs(limit=10)
                if trending:
                    with STATE_LOCK:
                        STATE["trending"] = trending[:5]
        except Exception:
            pass
        time.sleep(15)


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Pump.fun Lifecycle Dashboard</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{font-family:monospace;margin:0;background:#0d1117;color:#c9d1d9;transition:background 0.3s,color 0.3s}
body.dark{background:#04070c;color:#e6edf3}
.header{background:#161b22;padding:12px;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.header.dark{background:#0d1117}
.header h1{margin:0;font-size:14px}
#theme-toggle{position:absolute;right:12px;top:12px;background:#21262d;color:#c9d1d9;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:11px}
.tab-content{display:none;padding:12px}
.tab-content.active{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin:12px}
.card{background:#161b22;border-radius:6px;padding:12px}
.card.dark{background:#0d1b2a}
.card h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;color:#8b949e}
.card .value{font-size:16px;font-weight:bold}
.card .change{color:#58a6ff;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;padding:4px;color:#8b949e}
td{padding:4px;border-bottom:1px solid #21262d}
.log-entry{border-left:2px solid #58a6ff;margin:2px 0;padding:4px;font-size:10px}
.alert{padding:4px 8px;margin:2px 0;border-radius:3px;font-size:11px}
.alert-info{background:#161b22;border-left:3px solid #58a6ff}
.alert-emergency{background:#7d2ae0;border-left:3px solid #ff4444}
.tab-btn{display:inline-block;padding:8px 12px;margin:0 4px;border:none;background:#21262d;color:#c9d1d9;cursor:pointer;font-size:12px;border-radius:4px}
.tab-btn.active{background:#58a6ff;color:#0d1117}
canvas{max-width:100%}
.progress{background:#21262d;border-radius:4px;height:8px;margin:4px 0}
.progress-fill{height:100%;border-radius:4px;transition:width 0.5s}
.progress-low{background:#58a6ff}
.progress-med{background:#f2cc60}
.progress-high{background:#ff6b6b}
.wallet-role{display:inline-block;padding:2px 6px;margin:2px;border-radius:3px;font-size:9px}
.role-whale{background:#7d2ae0;color:#fff}
.role-mid{background:#238636;color:#fff}
.role-normal{background:#58a6ff;color:#0d1117}
.role-noise{background:#6e7681;color:#fff}
.role-sniper{background:#ff6b6b;color:#fff}
.role-cover{background:#f2cc60;color:#0d1117}
.role-gas{background:#0366d6;color:#fff}
.role-commenter{background:#187ade;color:#fff}
</style>
<script>
// Dark mode toggle
function toggleTheme(){
  document.body.classList.toggle('dark');
  const el=document.querySelector('#theme-toggle');
  if(document.body.classList.contains('dark')){el.textContent='☀️ Light'}else{el.textContent='🌙 Dark'}
  localStorage.setItem('theme',document.body.classList.contains('dark')?'dark':'light');
}
// Load theme preference
if(localStorage.getItem('theme')==='dark'){document.body.classList.add('dark');}
</script>
</head>
<body>
<div class="header" onclick="location.reload()">Pump.fun Lifecycle Dashboard — %s</div>
<button id="theme-toggle" onclick="toggleTheme()">🌙 Dark</button>
<div id="tabs">
<button class="tab-btn active" onclick="showTab('overview')">Overview</button>
<button class="tab-btn" onclick="showTab('chart')">Price Chart</button>
<button class="tab-btn" onclick="showTab('log')">Trade Log</button>
<button class="tab-btn" onclick="showTab('alerts')">Alerts</button>
</div>
<div id="tab-content">
<div id="overview" class="tab-content active"><div class="grid">
<div class="card"><h3>Phase</h3><div class="value" id="phase-val">INIT</div></div>
<div class="card"><h3>Current MC</h3><div class="value" id="mc-val">$0</div></div>
<div class="card"><h3>Wallets</h3><div class="value" id="wallet-count">0</div></div>
<div class="card"><h3>Trade Count</h3><div class="value" id="trade-count">0</div></div>
<div class="card"><h3>Price</h3><div class="value" id="price-val">$0</div></div>
<div class="card"><h3>Alert Count</h3><div class="value" id="alert-count">0</div></div>
<div class="card"><h3>Bubble Risk</h3><div class="value" id="bubble-risk">0.00</div><div class="progress"><div class="progress-fill progress-low" id="bubble-bar" style="width:0%"></div></div></div>
<div class="card"><h3>Take-Profit Tiers</h3><div class="value" id="tp-count">0</div><div id="tp-list"></div></div>
<div class="card"><h3>Diversity</h3><div class="value" id="diversity">0%</div></div>
<div class="card"><h3>Strategy</h3><div class="value" id="strategy">None</div></div>
<div class="card"><h3>Wallet Roles</h3><div id="roles-list"></div></div>
</div></div>
<div id="chart" class="tab-content"><div class="card"><canvas id="priceChart"></canvas></div></div>
<div id="log" class="tab-content"><div class="card"><table><thead><tr><th>Time</th><th>Event</th><th>Wallets</th><th>ΔPrice</th></tr></thead><tbody id="log-body"></tbody></table></div></div>
<div id="alerts" class="tab-content"><div class="card" id="alert-list"></div></div>
</div>
<script>
var chart=null;var currentPhase=0;var phases=['INIT','BUY','TRADE','TAKE_PROFIT','CASH_OUT','CLOSE','EMERGENCY'];
function showTab(id){var tabs=document.querySelectorAll('.tab-content');tabs.forEach(t=>t.classList.remove('active'));document.getElementById(id).classList.add('active');var btns=document.querySelectorAll('.tab-btn');btns.forEach(b=>b.classList.remove('active'));event.target.classList.add('active')}
function update(){fetch('/api/state').then(r=>r.json()).then(d=>{
document.getElementById('phase-val').textContent=d.phase;
document.getElementById('mc-val').textContent=('$'+d.mc_usd.toFixed(2));
document.getElementById('wallet-count').textContent=d.wallets.length||0;
document.getElementById('trade-count').textContent=d.trade_log.length||0;
document.getElementById('price-val').textContent=('$'+d.current_price.toFixed(8));
document.getElementById('alert-count').textContent=d.alerts.length||0;
// Enhanced state display
document.getElementById('bubble-risk').textContent=d.bubble_risk.toFixed(2);
document.getElementById('diversity').textContent=(d.diversity_score*100).toFixed(0)+'%';
document.getElementById('strategy').textContent=d.strategy||'None';
document.getElementById('tp-count').textContent=d.take_profit_tiers.length||0;
// Bubble risk bar with color coding
var br=d.bubble_risk||0;
var bar=document.getElementById('bubble-bar');
bar.style.width=(br*100)+'%';
bar.className='progress-fill';
if(br<0.5)bar.className='progress-fill progress-low';
else if(br<0.8)bar.className='progress-fill progress-med';
else bar.className='progress-fill progress-high';
// TP tiers list
var tpl=d.take_profit_tiers.slice().reverse().slice(0,7);
var tplHtml='';
var tpLabels=['2x','3x','5x','10x','15x','20x','100x'];
tpl.forEach(function(t,i){var label=tpLabels[t.tier]||('Tier '+t.tier);tplHtml+='<span class=\"wallet-role role-sniper\">'+label+'</span>'}));
document.getElementById('tp-list').innerHTML=tplHtml;
// Wallet roles visualization
var rolesHtml='';
if(d.wallets&&d.wallets.length){var roleCounts={};d.wallets.forEach(function(w){var r=w.role||'normal';roleCounts[r]=(roleCounts[r]||0)+1});Object.keys(roleCounts).forEach(function(r){rolesHtml+='<span class=\"wallet-role role-'+r+'\">'+r+': '+roleCounts[r]+'</span>'}))}
document.getElementById('roles-list').innerHTML=rolesHtml;
var alerts='';if(d.alerts){d.alerts.slice().reverse().slice(0,10).forEach(a=>{alerts+='<div class="alert alert-'+a.type+'">'+a.t+' '+a.msg+'</div>'}))}
document.getElementById('alert-list').innerHTML=alerts;
var log='';d.trade_log.slice().reverse().slice(0,20).forEach(e=>{log+='<tr><td>'+new Date(e.t*1000).toLocaleTimeString().slice(0,8)+'</td><td>'+e.event+'</td><td>'+e.wallets+'</td><td>'+(e.change||'')+'</td></tr>'}));
document.getElementById('log-body').innerHTML=log;
if(!chart&&d.price_history.length>1){var ctx=document.getElementById('priceChart').getContext('2d');chart=new Chart(ctx,{type:'line',data:{labels:d.price_history.map(p=>new Date(p.t*1000).toLocaleTimeString().slice(0,8)),datasets:[{label:'Price',data:d.price_history.map(p=>p.p),borderColor:'#58a6ff',backgroundColor:'rgba(88,166,255,0.1)',fill:true,tension:0.3}]},options:{responsive:true,maintainAspectRatio:false,height:300}});
}else if(chart){chart.data.labels=d.price_history.map(p=>new Date(p.t*1000).toLocaleTimeString().slice(0,8));chart.data.datasets[0].data=d.price_history.map(p=>p.p);chart.update()}}
setTimeout(update,5000)
}update();
</script>
</body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(HTML_TEMPLATE % STATE.get("phase", "IDLE"))
        elif path == "/api/state":
            self._send_json({"state": STATE})
        elif path == "/api/health":
            self._send_json({"status": "ok", "time": time.time()})
        else:
            self.send_response(404)
            self.end_headers()

    def _send_html(self, content: str):
        content_bytes = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content_bytes)))
        self.end_headers()
        self.wfile.write(content_bytes)

    def _send_json(self, data: dict):
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass  # Suppress access logs


def start_server(port: int = 8765, background: bool = False):
    """Start the SSE dashboard server."""
    global SERVER
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    SERVER = server

    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server

    print(f"🚀 Dashboard starting on http://localhost:{port}")
    print(f"📊 Live price charts, wallet balances, trade log, alerts")
    print(f"Press Ctrl+C to stop")
    update_state(phase="DASHBOARD")

    # Start data collection
    threading.Thread(target=_update_loop, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.shutdown()


def start_dashboard_background(port: int = 8765):
    """Start dashboard in background thread for integration with lifecycle CLI."""
    global SERVER
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    SERVER = server
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    threading.Thread(target=_update_loop, daemon=True).start()
    print(f"📊 Dashboard running at http://localhost:{port}")
    return server


if __name__ == "__main__":
    port = 8765
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    background = "--background" in sys.argv
    start_server(port=port, background=background)

    if background:
        print(f"Dashboard running in background on port {port}")
        print("Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping.")
            if SERVER:
                SERVER.shutdown()

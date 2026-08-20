#!/usr/bin/env python3
"""
Web Dashboard for Pump.fun Lifecycle CLI.

Streams live lifecycle state to a browser dashboard.
Uses Server-Sent Events (SSE) for real-time updates.

Run: python3 web_dashboard.py [--port 8765]
Then open: http://localhost:8765

Author: Matthew A. Baker
"""

import os
import sys
import json
import time
import signal
import threading

import json
import os
import sys
import time
import signal
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ─── Constants ───
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lifecycle_state.json")
DEFAULT_PORT = 8765

# ─── HTML Template ───

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pump.fun Lifecycle Dashboard</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Fira Code', monospace;
      background: #0a0a12;
      color: #e0e0e0;
      padding: 20px;
      overflow-x: hidden;
    }
    .header {
      text-align: center;
      margin-bottom: 30px;
      padding-bottom: 15px;
      border-bottom: 2px solid #333;
    }
    .header h1 { color: #00ff88; font-size: 2em; }
    .header .subtitle { color: #888; margin-top: 5px; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      max-width: 1400px;
      margin: 0 auto;
    }
    .card {
      background: #14141a;
      border: 1px solid #333;
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .card h2 {
      color: #00ff88;
      margin-bottom: 15px;
      font-size: 1.3em;
      border-bottom: 1px solid #333;
      padding-bottom: 8px;
    }
    .status-badge {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 4px;
      font-size: 0.85em;
      font-weight: bold;
    }
    .status-completed { background: #005522; color: #00ff88; }
    .status-running { background: #553300; color: #ffaa00; }
    .status-pending { background: #333; color: #888; }
    .status-failed { background: #550000; color: #ff4444; }
    .phase-list { list-style: none; }
    .phase-list li {
      padding: 10px;
      margin: 5px 0;
      background: #0d0d14;
      border-radius: 4px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .metric { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #222; }
    .metric:last-child { border-bottom: none; }
    .metric .label { color: #888; }
    .metric .value { color: #e0e0e0; font-weight: bold; }
    .metric .value.positive { color: #00ff88; }
    .metric .value.negative { color: #ff4444; }
    .progress-bar {
      height: 20px;
      background: #222;
      border-radius: 10px;
      overflow: hidden;
      margin: 10px 0;
    }
    .progress-fill {
      height: 100%;
      background: linear-gradient(90deg, #00ff88, #00cc66);
      transition: width 0.5s;
    }
    .wallet-table { width: 100%; border-collapse: collapse; }
    .wallet-table th { text-align: left; padding: 8px; color: #888; border-bottom: 1px solid #333; }
    .wallet-table td { padding: 8px; border-bottom: 1px solid #222; }
    .wallet-table tr:hover { background: #1a1a22; }
    .log-container {
      background: #050508;
      border: 1px solid #333;
      border-radius: 4px;
      padding: 12px;
      height: 300px;
      overflow-y: auto;
      font-size: 0.85em;
      white-space: pre-wrap;
    }
    .log-line { margin: 2px 0; }
    .log-line.error { color: #ff4444; }
    .log-line.warn { color: #ffaa00; }
    .log-line.ok { color: #00ff88; }
    .log-line.info { color: #888; }
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 15px;
      text-align: center;
    }
    .stat-box {
      background: #0d0d14;
      padding: 15px;
      border-radius: 6px;
      border: 1px solid #333;
    }
    .stat-box .number { font-size: 2em; font-weight: bold; color: #00ff88; }
    .stat-box .label { font-size: 0.85em; color: #888; margin-top: 5px; }
    .stat-box .number.warning { color: #ffaa00; }
    .stat-box .number.danger { color: #ff4444; }
    .refresh-hint {
      text-align: center;
      color: #555;
      font-size: 0.8em;
      margin-top: 20px;
    }
  </style>
</head>
<body>
  <div class="header">
    <h1>🚀 Pump.fun Lifecycle Dashboard</h1>
    <div class="subtitle">Real-time monitoring — auto-refreshing every 2s</div>
  </div>

  <div class="stat-grid" id="statGrid">
    <div class="stat-box">
      <div class="number" id="statPhases">0/0</div>
      <div class="label">Phases Complete</div>
    </div>
    <div class="stat-box">
      <div class="number" id="statWallets">0</div>
      <div class="label">Active Wallets</div>
    </div>
    <div class="stat-box">
      <div class="number" id="statSOL">0.000</div>
      <div class="label">Total SOL Recovered</div>
    </div>
    <div class="stat-box">
      <div class="number" id="statRisks">0</div>
      <div class="label">Risk Alerts</div>
    </div>
  </div>

  <div class="grid">
    <!-- Phase Status -->
    <div class="card">
      <h2>📋 Lifecycle Phases</h2>
      <ul class="phase-list" id="phaseList">
        <li><span>Loading...</span></li>
      </ul>
    </div>

    <!-- Wallet Balances -->
    <div class="card">
      <h2>💳 Wallet Status</h2>
      <table class="wallet-table" id="walletTable">
        <thead>
          <tr><th>Wallet</th><th>Role</th><th>SOL Balance</th><th>Tokens</th></tr>
        </thead>
        <tbody id="walletBody">
          <tr><td colspan="4">No wallets</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Token Metrics -->
    <div class="card">
      <h2>📊 Token Metrics</h2>
      <div class="metric"><span class="label">Token Mint:</span><span class="value" id="tokMint">N/A</span></div>
      <div class="metric"><span class="label">Current MC:</span><span class="value" id="tokMC">$0</span></div>
      <div class="metric"><span class="label">Entry Price:</span><span class="value" id="tokEntry">$0</span></div>
      <div class="metric"><span class="label">Peak MC:</span><span class="value" id="tokPeak">$0</span></div>
      <div class="metric"><span class="label">Current Price:</span><span class="value" id="tokPrice">$0</span></div>
      <div class="metric"><span class="label">ROI:</span><span class="value" id="tokROI">0%</span></div>
      <div class="progress-bar"><div class="progress-fill" id="tokProgress" style="width: 0%"></div></div>
    </div>

    <!-- Budget & Fees -->
    <div class="card">
      <h2>💰 Budget & Fees</h2>
      <div class="metric"><span class="label">Budget (SOL):</span><span class="value" id="budSOL">0.000</span></div>
      <div class="metric"><span class="label">Budget (USD):</span><span class="value" id="budUSD">$0</span></div>
      <div class="metric"><span class="label">Gas Reserve:</span><span class="value" id="budGas">0.000 SOL</span></div>
      <div class="metric"><span class="label">Fees Est.:</span><span class="value" id="budFees">$0</span></div>
      <div class="metric"><span class="label">Remaining:</span><span class="value" id="budRem">0.000 SOL</span></div>
    </div>

    <!-- Stop-Loss Status -->
    <div class="card">
      <h2>🛑 Stop-Loss Monitor</h2>
      <div class="metric"><span class="label">Enabled:</span><span class="value" id="slEnabled">Yes</span></div>
      <div class="metric"><span class="label">Threshold:</span><span class="value" id="slThreshold">30%</span></div>
      <div class="metric"><span class="label">Entry Price:</span><span class="value" id="slEntry">$0</span></div>
      <div class="metric"><span class="label">Peak Price:</span><span class="value" id="slPeak">$0</span></div>
      <div class="metric"><span class="label">Current Drawdown:</span><span class="value" id="slDrawdown">0%</span></div>
      <div class="metric"><span class="label">Status:</span><span class="value" id="slStatus">OK</span></div>
    </div>

    <!-- Live Log Feed -->
    <div class="card" style="grid-column: 1 / -1;">
      <h2>📝 Live Log Feed</h2>
      <div class="log-container" id="logFeed">
        <div class="log-line info">Waiting for lifecycle activity...</div>
      </div>
    </div>
  </div>

  <div class="refresh-hint">Last updated: <span id="lastUpdate">never</span></div>

  <script>
    function fetchState() {
      fetch('/state')
        .then(r => r.json())
        .then(data => updateDashboard(data))
        .catch(err => {
          document.getElementById('lastUpdate').textContent = 'error: ' + err;
        });
    }

    function formatAddr(addr) {
      return addr ? addr.substring(0, 8) + '...' + addr.substring(addr.length - 8) : 'N/A';
    }

    function updateDashboard(data) {
      // Phases
      var phaseList = document.getElementById('phaseList');
      phaseList.innerHTML = '';
      var phases = data.phases || {};
      var phaseOrder = ['create', 'warmup', 'fund', 'buy', 'trade', 'take_profit', 'cash_out', 'close'];
      var completed = 0;
      phaseOrder.forEach(function(p) {
        var info = phases[p] || {status: 'pending'};
        var status = info.status || 'pending';
        var badge = '<span class="status-badge status-' + status + '">' + status.toUpperCase() + '</span>';
        var name = p.replace('_', ' ').toUpperCase();
        phaseList.innerHTML += '<li><span>' + name + '</span>' + badge + '</li>';
        if (status === 'completed') completed++;
      });
      document.getElementById('statPhases').textContent = completed + '/' + phaseOrder.length;

      // Wallets
      var walletBody = document.getElementById('walletBody');
      walletBody.innerHTML = '';
      var wallets = data.bot_wallets || [];
      document.getElementById('statWallets').textContent = wallets.length;
      wallets.forEach(function(w) {
        walletBody.innerHTML += '<tr><td>' + formatAddr(w.pubkey) + '</td>' +
          '<td>' + (w.role || 'bot') + '</td>' +
          '<td>' + (w.current_sol || 0).toFixed(6) + '</td>' +
          '<td>' + (w.tokens_held || 0).toFixed(2) + '</td></tr>';
      });

      // Token
      document.getElementById('tokMint').textContent = formatAddr(data.token_mint);
      document.getElementById('tokMC').textContent = '$' + (data.current_mc_usd || 0).toLocaleString();
      document.getElementById('tokPeak').textContent = '$' + (data.peak_mc_usd || 0).toLocaleString();
      document.getElementById('tokPrice').textContent = '$' + (data.current_price || 0).toFixed(8);
      document.getElementById('tokEntry').textContent = '$' + (data.entry_price || 0).toFixed(8);

      var roi = data.current_price && data.entry_price ?
        ((data.current_price - data.entry_price) / data.entry_price * 100) : 0;
      document.getElementById('tokROI').textContent = roi.toFixed(1) + '%';
      document.getElementById('tokROI').className = 'value ' + (roi >= 0 ? 'positive' : 'negative');

      // Progress bar (MC relative to graduation threshold)
      var gradMC = 69000;
      var pct = Math.min((data.current_mc_usd || 0) / gradMC * 100, 100);
      document.getElementById('tokProgress').style.width = pct + '%';

      // Budget
      document.getElementById('budSOL').textContent = (data.budget_sol || 0).toFixed(6);
      document.getElementById('budUSD').textContent = '$' + (data.budget_usd || 0).toFixed(2);
      document.getElementById('budGas').textContent = (data.gas_reserve_sol || 0).toFixed(3) + ' SOL';
      document.getElementById('budFees').textContent = '$' + (data.estimated_fees_usd || 0).toFixed(2);
      document.getElementById('statSOL').textContent = (data.recovered_sol || 0).toFixed(6);

      var rem = (data.budget_sol || 0) - (data.estimated_fees_usd || 0);
      document.getElementById('budRem').textContent = rem.toFixed(4) + ' SOL';

      // Stop-loss
      var sl = data.stop_loss_state || {};
      document.getElementById('slEnabled').textContent = sl.enabled ? 'Yes' : 'No';
      document.getElementById('slEntry').textContent = '$' + (sl.entry_price || 0).toFixed(8);
      document.getElementById('slPeak').textContent = '$' + (sl.peak_price || 0).toFixed(8);
      var drawdown = sl.peak_price && sl.current_price ?
        ((sl.peak_price - sl.current_price) / sl.peak_price * 100) : 0;
      document.getElementById('slDrawdown').textContent = drawdown.toFixed(1) + '%';
      document.getElementById('slStatus').textContent = sl.triggered ? 'TRIGGERED' : 'OK';
      document.getElementById('slStatus').className = 'value ' + (sl.triggered ? 'negative' : 'positive');

      // Risk alerts
      var risks = data.risk_alerts || 0;
      document.getElementById('statRisks').textContent = risks;
      document.getElementById('statRisks').parentElement.className =
        'stat-box ' + (risks > 0 ? (risks > 3 ? 'danger-box' : 'warning-box') : '');

      // Log feed
      var logs = data.recent_logs || [];
      var logFeed = document.getElementById('logFeed');
      logFeed.innerHTML = '';
      logs.slice(-30).forEach(function(line) {
        var cls = 'info';
        if (line.includes('ERROR') || line.includes('[FATAL')) cls = 'error';
        else if (line.includes('WARN') || line.includes('⚠️')) cls = 'warn';
        else if (line.includes('OK') || line.includes('✓') || line.includes('COMPLETE')) cls = 'ok';
        logFeed.innerHTML += '<div class="log-line ' + cls + '">' + line + '</div>';
      });
      logFeed.scrollTop = logFeed.scrollHeight;

      document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
    }

    // Auto-refresh every 2 seconds
    setInterval(fetchState, 2000);
    fetchState();
  </script>
</body>
</html>
"""


class StateMonitor:
    """Monitor the lifecycle state file and log output."""

    def __init__(self, state_file=STATE_FILE):
        self.state_file = state_file
        self.last_state = {}
        self.log_lines = []
        self.log_file = os.path.join(os.path.dirname(state_file), ".lifecycle_log.txt")
        self.recent_mc = []
        self.risk_alerts = 0
        self.peak_mc = 0
        self.entry_price = 0
        self.current_price = 0
        self.stop_loss_state = {
            "enabled": True,
            "entry_price": 0,
            "peak_price": 0,
            "current_price": 0,
            "drawdown_pct": 0,
            "triggered": False,
            "trigger_reason": "",
        }

    def _read_log(self):
        """Read recent log lines from the log file."""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, "r") as f:
                    lines = f.readlines()
                self.log_lines = lines[-50:]  # Last 50 lines
            except Exception:
                pass

    def _read_state(self):
        """Read and parse the lifecycle state file."""
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _compute_price_metrics(self, data):
        """Compute price, MC, and ROI metrics from state."""
        token_mint = data.get("token_mint", "")
        current_mc = data.get("current_mc_usd", 0)

        # Track peak MC
        if current_mc > self.peak_mc:
            self.peak_mc = current_mc

        # Estimate price from MC / supply
        supply = data.get("token_supply", 1_000_000_000)
        if supply > 0 and current_mc > 0:
            current_price = current_mc / supply
        else:
            current_price = 0

        if self.entry_price == 0 and current_price > 0:
            self.entry_price = current_price

        self.current_price = current_price
        self.recent_mc.append(current_mc)
        if len(self.recent_mc) > 20:
            self.recent_mc.pop(0)

        # Update stop-loss state
        if self.stop_loss_state["peak_price"] < current_price:
            self.stop_loss_state["peak_price"] = current_price
        self.stop_loss_state["current_price"] = current_price
        self.stop_loss_state["entry_price"] = self.entry_price

        drawdown = 0
        if self.stop_loss_state["peak_price"] > 0:
            drawdown = (self.stop_loss_state["peak_price"] - current_price) / self.stop_loss_state["peak_price"] * 100
        self.stop_loss_state["drawdown_pct"] = drawdown

        # Risk alerts: drawdown > 20%, price drop > 30%
        if drawdown > 30 and not self.stop_loss_state["triggered"]:
            self.risk_alerts += 1
            self.stop_loss_state["triggered"] = True
            self.stop_loss_state["trigger_reason"] = f"Drawdown {drawdown:.1f}%"

        data["current_price"] = current_price
        data["entry_price"] = self.entry_price
        data["peak_mc_usd"] = self.peak_mc
        data["stop_loss_state"] = self.stop_loss_state
        data["risk_alerts"] = self.risk_alerts

        # Compute estimated fees and remaining
        if "budget_sol" in data and "estimated_fees_sol" in data:
            data["estimated_fees_usd"] = data["estimated_fees_sol"] * 150  # Approximate
        if "budget_sol" in data and "gas_reserve_sol" in data:
            data["recovered_sol"] = 0  # Will be updated by cash_out phase

        return data

    def get_snapshot(self):
        """Get current dashboard snapshot."""
        self._read_log()
        data = self._read_state()
        data = self._compute_price_metrics(data)
        data["recent_logs"] = self.log_lines
        data["last_update"] = time.time()
        self.last_state = data
        return data


# ─── Web Server ───

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler serving dashboard HTML and SSE state."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode())

        elif path == "/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            snapshot = monitor.get_snapshot()
            self.wfile.write(json.dumps(snapshot).encode())

        elif path == "/state/stream":
            # Server-Sent Events stream
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    snapshot = monitor.get_snapshot()
                    self.wfile.write(f"data: {json.dumps(snapshot)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(2)
            except BrokenPipeError:
                pass

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging


# Global monitor instance
monitor = StateMonitor()


def run_dashboard(port=DEFAULT_PORT):
    """Start the dashboard web server."""
    print(f"Starting Pump.fun Lifecycle Dashboard...")
    print(f"  Dashboard: http://localhost:{port}")
    print(f"  State API: http://localhost:{port}/state")
    print(f"  Event Stream: http://localhost:{port}/state/stream")
    print(f"  State file: {STATE_FILE}")
    print(f"  Press Ctrl+C to stop.\n")

    server = HTTPServer(("0.0.0.0", port), DashboardHandler)

    def signal_handler(sig, frame):
        print("\nShutting down dashboard...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    run_dashboard(port=port)

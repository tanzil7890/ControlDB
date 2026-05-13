"""Minimal HTML dashboard views.

We avoid bundling a JS framework in the MVP — HTML pages call the same REST
API as SDKs and dashboards. The Next.js app under web/dashboard can replace
this later without breaking the API contract.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ..approvals import ApprovalsService
from ..auth import current_principal
from ..export import EvidenceService
from ..ingest import IngestService
from ..storage import Database


_STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         margin: 0; padding: 0; background: #0b0d12; color: #e6edf3; }
  header { padding: 20px 24px; border-bottom: 1px solid #1b1f27; display:flex; align-items:center; gap:16px;}
  header h1 { margin: 0; font-size: 18px; letter-spacing: 0.4px; }
  header nav a { color: #79b8ff; margin-right: 12px; text-decoration: none; }
  main { padding: 20px 24px; max-width: 1100px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 8px 10px; border-bottom: 1px solid #1b1f27; font-size: 13px; vertical-align: top; }
  th { text-align: left; color: #79b8ff; font-weight: 600; }
  code { background: #161b22; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  .badge { padding: 2px 6px; border-radius: 3px; font-size: 11px; }
  .status-committed { background:#1f6f3c; }
  .status-running { background:#2b5797; }
  .status-failed { background:#7a2b2b; }
  .status-rolled_back { background:#7a5a2b; }
  .status-requires_approval { background:#5a2b7a; }
  pre { background: #161b22; padding: 10px; border-radius: 4px; overflow:auto; }
  .row { display:flex; gap:16px; }
  .col { flex: 1; }
</style>
"""


def _render_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{html.escape(title)} — ControlDB</title>{_STYLE}</head>
<body>
<header>
  <h1>ControlDB</h1>
  <nav>
    <a href='/'>Runs</a>
    <a href='/dashboard/approvals'>Approvals</a>
    <a href='/dashboard/query'>Query</a>
  </nav>
</header>
<main>{body}</main>
</body></html>"""


def register_dashboard(
    app: FastAPI,
    db: Database,
    ingest: IngestService,
    approvals: ApprovalsService,
    evidence: EvidenceService,
) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def index(api_key: Optional[str] = Query(default=None)) -> HTMLResponse:
        body = f"""
        <h2>Agent runs</h2>
        <p>Use the API key from your collector configuration to inspect runs.</p>
        <form method='get'>
          <input type='text' name='api_key' value='{html.escape(api_key or "")}' placeholder='api key' style='width:240px;padding:6px;'/>
          <button type='submit'>Load</button>
        </form>
        <div id='runs'></div>
        <script>
        const key = {json.dumps(api_key)};
        if (key) {{
          fetch('/v1/runs?limit=100', {{ headers: {{ 'Authorization': 'Bearer ' + key }} }})
            .then(r => r.json())
            .then(data => {{
              const rows = data.runs.map(r => `
                <tr>
                  <td><a href='/dashboard/runs/${{r.run_id}}?api_key=${{encodeURIComponent(key)}}'><code>${{r.run_id}}</code></a></td>
                  <td>${{r.agent_id}}</td>
                  <td>${{r.environment}}</td>
                  <td><span class='badge status-${{r.status}}'>${{r.status}}</span></td>
                  <td>${{r.event_count}}</td>
                  <td>${{r.started_at || ''}}</td>
                </tr>`).join('');
              document.getElementById('runs').innerHTML =
                `<table><thead><tr><th>Run</th><th>Agent</th><th>Env</th><th>Status</th><th>Events</th><th>Started</th></tr></thead><tbody>${{rows}}</tbody></table>`;
            }})
            .catch(err => document.getElementById('runs').innerText = err);
        }}
        </script>
        """
        return HTMLResponse(_render_page("Runs", body))

    @app.get("/dashboard/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(run_id: str, api_key: Optional[str] = Query(default=None)) -> HTMLResponse:
        body = f"""
        <h2>Run <code>{html.escape(run_id)}</code></h2>
        <div id='run'></div>
        <div class='row'>
          <div class='col'><h3>Timeline</h3><div id='timeline'></div></div>
          <div class='col'><h3>Event</h3><pre id='event'>Select an event to see its envelope.</pre></div>
        </div>
        <h3>Hash verification</h3>
        <pre id='verify'></pre>
        <button onclick='exportRun()'>Export evidence bundle</button>
        <pre id='export'></pre>
        <script>
        const key = {json.dumps(api_key)};
        const runId = {json.dumps(run_id)};
        const headers = {{ 'Authorization': 'Bearer ' + key }};
        async function load() {{
          const run = await fetch(`/v1/runs/${{runId}}`, {{ headers }}).then(r => r.json());
          document.getElementById('run').innerHTML = `
            <p>Agent <code>${{run.agent_id}}</code> (${{run.agent_version || 'unversioned'}}) — status <span class='badge status-${{run.status}}'>${{run.status}}</span> — events ${{run.event_count}}</p>`;
          const timeline = await fetch(`/v1/runs/${{runId}}/timeline`, {{ headers }}).then(r => r.json());
          const rows = timeline.events.map(e => `
            <tr><td>${{e.sequence}}</td><td>${{e.event_type}}</td><td><code>${{e.step_id || ''}}</code></td>
            <td><a href='#' data-id='${{e.event_id}}'>view</a></td></tr>`).join('');
          const html = `<table><thead><tr><th>#</th><th>type</th><th>step</th><th></th></tr></thead><tbody>${{rows}}</tbody></table>`;
          const wrap = document.getElementById('timeline');
          wrap.innerHTML = html;
          wrap.querySelectorAll('a').forEach(a => a.addEventListener('click', ev => {{
            ev.preventDefault();
            const id = a.getAttribute('data-id');
            const e = timeline.events.find(x => x.event_id === id);
            document.getElementById('event').innerText = JSON.stringify(e, null, 2);
          }}));
          const verify = await fetch(`/v1/runs/${{runId}}/verify`, {{ headers }}).then(r => r.json());
          document.getElementById('verify').innerText = JSON.stringify(verify, null, 2);
        }}
        async function exportRun() {{
          const res = await fetch('/v1/audit/export', {{
            method: 'POST', headers: {{...headers, 'Content-Type': 'application/json'}},
            body: JSON.stringify({{ run_id: runId, generated_by: 'dashboard' }})
          }}).then(r => r.json());
          document.getElementById('export').innerText = JSON.stringify(res, null, 2);
        }}
        if (key) load();
        </script>
        """
        return HTMLResponse(_render_page(f"Run {run_id}", body))

    @app.get("/dashboard/approvals", response_class=HTMLResponse)
    async def approvals_view(api_key: Optional[str] = Query(default=None)) -> HTMLResponse:
        body = f"""
        <h2>Pending approvals</h2>
        <div id='approvals'></div>
        <script>
        const key = {json.dumps(api_key)};
        if (key) {{
          fetch('/v1/approvals/pending', {{ headers: {{ 'Authorization': 'Bearer ' + key }} }})
            .then(r => r.json())
            .then(data => {{
              const rows = (data.approvals || []).map(a => `
                <tr>
                  <td><code>${{a.approval_id}}</code></td>
                  <td>${{a.run_id}}</td>
                  <td>${{a.policy_id || ''}}</td>
                  <td>${{a.reason || ''}}</td>
                  <td>
                    <button onclick="decide('${{a.approval_id}}','approve')">Approve</button>
                    <button onclick="decide('${{a.approval_id}}','reject')">Reject</button>
                  </td>
                </tr>`).join('');
              document.getElementById('approvals').innerHTML = `<table><thead>
                <tr><th>Approval</th><th>Run</th><th>Policy</th><th>Reason</th><th></th></tr>
                </thead><tbody>${{rows}}</tbody></table>`;
            }});
        }}
        async function decide(id, action) {{
          const reviewer = prompt('Reviewer ID:') || 'dashboard';
          const reason = prompt('Reason (optional):') || '';
          await fetch(`/v1/approvals/${{id}}/${{action}}`, {{
            method: 'POST',
            headers: {{ 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ reviewer_id: reviewer, reason }})
          }});
          location.reload();
        }}
        </script>
        """
        return HTMLResponse(_render_page("Approvals", body))

    @app.get("/dashboard/query", response_class=HTMLResponse)
    async def query_view(api_key: Optional[str] = Query(default=None)) -> HTMLResponse:
        body = f"""
        <h2>Query console</h2>
        <p>Filter audit events by run or event type.</p>
        <pre id='hint'>POST /v1/query {{"event_types": ["policy.check.failed"], "limit": 50}}</pre>
        <textarea id='body' rows='6' style='width:100%;font-family:monospace;'>{{
  "event_types": ["policy.check.failed"],
  "limit": 50
}}</textarea>
        <button onclick='runQuery()'>Run query</button>
        <pre id='result'></pre>
        <script>
        const key = {json.dumps(api_key)};
        async function runQuery() {{
          const res = await fetch('/v1/query', {{
            method: 'POST',
            headers: {{ 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' }},
            body: document.getElementById('body').value,
          }}).then(r => r.json());
          document.getElementById('result').innerText = JSON.stringify(res, null, 2);
        }}
        </script>
        """
        return HTMLResponse(_render_page("Query", body))

"""
Tail Railway logs for the sleep-audio-platform service.

Usage:
    RAILWAY_TOKEN=<token> python3 -m scripts.tail_railway_logs [--since 120]

Token must be a Railway *personal access token* (railway.app/account/tokens).
Service/internal tokens cannot query the management API and will fail with
"Not Authorized" on me { projects }.

If you have the IDs handy you can skip the discovery step:
    RAILWAY_PROJECT_ID=... RAILWAY_SERVICE_ID=... python3 -m scripts.tail_railway_logs

Polls Railway GraphQL every 3 seconds. Ctrl-C to stop.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import httpx

RAILWAY_GQL  = "https://backboard.railway.app/graphql/v2"
SERVICE_NAME = "sleep-audio-platform"

_TOKEN_HELP = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAILWAY_TOKEN must be a PERSONAL ACCESS TOKEN.
Service/internal tokens cannot query the management API.

Get one at:  https://railway.app/account/tokens

Then either:
  • Replace RAILWAY_TOKEN in Replit Secrets with the
    personal token, OR
  • Set these two extra Secrets so discovery is skipped:
      RAILWAY_PROJECT_ID  — project UUID from Railway dashboard
      RAILWAY_SERVICE_ID  — service UUID from Railway dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _gql(token: str, query: str, variables: dict | None = None) -> dict:
    resp = httpx.post(
        RAILWAY_GQL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        msgs = [e.get("message", "") for e in data["errors"]]
        raise RuntimeError(f"Railway GraphQL error: {msgs}")
    return data["data"]


def find_service(token: str) -> tuple[str, str]:
    """Return (project_id, service_id) for sleep-audio-platform.

    Tries env-var override first so callers with project-scoped tokens can
    set RAILWAY_PROJECT_ID + RAILWAY_SERVICE_ID and skip the me{} discovery.
    """
    proj_id = os.environ.get("RAILWAY_PROJECT_ID", "")
    svc_id  = os.environ.get("RAILWAY_SERVICE_ID", "")
    if proj_id and svc_id:
        print(f"[railway] Using IDs from env — project_id={proj_id}  service_id={svc_id}")
        return proj_id, svc_id

    print("[railway] Discovering project/service via Railway API (requires personal token)…")
    try:
        data = _gql(token, """
            {
              me {
                projects {
                  edges {
                    node {
                      id name
                      services { edges { node { id name } } }
                    }
                  }
                }
              }
            }
        """)
    except RuntimeError as exc:
        if "Not Authorized" in str(exc):
            print(f"\n[railway] ERROR: token cannot access 'me {{projects}}'.")
            print(_TOKEN_HELP)
            sys.exit(1)
        raise

    for proj_edge in data["me"]["projects"]["edges"]:
        proj = proj_edge["node"]
        for svc_edge in proj["services"]["edges"]:
            svc = svc_edge["node"]
            if SERVICE_NAME in svc["name"].lower():
                print(f"[railway] Found: project={proj['name']}  service={svc['name']}")
                print(f"[railway] project_id={proj['id']}  service_id={svc['id']}")
                return proj["id"], svc["id"]

    raise RuntimeError(f"Service '{SERVICE_NAME}' not found in any Railway project")


def latest_deployment(token: str, project_id: str, service_id: str) -> str:
    data = _gql(token, """
        query($projectId: String!, $serviceId: String!) {
          deployments(
            input: { projectId: $projectId, serviceId: $serviceId }
            first: 1
          ) {
            edges { node { id status createdAt } }
          }
        }
    """, {"projectId": project_id, "serviceId": service_id})
    edges = data["deployments"]["edges"]
    if not edges:
        raise RuntimeError("No deployments found for service")
    dep = edges[0]["node"]
    print(f"[railway] Latest deployment: id={dep['id']}  status={dep['status']}")
    return dep["id"]


def tail_logs(token: str, deployment_id: str, since_seconds: int = 120,
              filter_after: str = "pipeline_start") -> None:
    """Poll deployment logs, printing new lines as they arrive.

    If filter_after is set, suppresses lines until a line containing that
    substring is seen, then prints everything from that point forward.
    """
    seen:           set[str]  = set()
    cutoff                    = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
    triggered                 = filter_after == ""    # True = print from the start
    filter_str                = filter_after.lower()

    print(f"\n[railway] Tailing logs for deployment {deployment_id[:16]}…")
    if filter_after:
        print(f"[railway] Buffering until line containing {filter_after!r} is seen.")
    print(f"[railway] Showing lines from the last {since_seconds}s. Ctrl-C to stop.\n")
    print("─" * 72)

    while True:
        try:
            data = _gql(token, """
                query($deploymentId: String!) {
                  deploymentLogs(deploymentId: $deploymentId, limit: 500) {
                    timestamp
                    severity
                    message
                  }
                }
            """, {"deploymentId": deployment_id})
        except Exception as exc:
            print(f"[poll error] {exc}", file=sys.stderr)
            time.sleep(5)
            continue

        new_lines: list[tuple[datetime, str, str]] = []

        for entry in data.get("deploymentLogs", []):
            ts_raw = entry.get("timestamp", "")
            msg    = entry.get("message", "")
            sev    = entry.get("severity", "")
            key    = f"{ts_raw}|{msg}"

            if key in seen:
                continue

            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(timezone.utc)

            if ts < cutoff:
                seen.add(key)
                continue

            seen.add(key)
            new_lines.append((ts, sev, msg))

        # Sort by timestamp so lines print in order
        new_lines.sort(key=lambda x: x[0])

        for ts, sev, msg in new_lines:
            if not triggered:
                if filter_str in msg.lower():
                    triggered = True
                    print(f"[railway] ── pipeline_start marker found — showing all subsequent lines ──")
                else:
                    continue

            ts_str = ts.strftime("%H:%M:%S")
            prefix = f"[{sev[:4].upper()}]" if sev else "     "
            print(f"{ts_str} {prefix} {msg}")

        time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail Railway logs for sleep-audio-platform")
    parser.add_argument("--since",   type=int, default=120,
                        help="Show logs from last N seconds (default 120)")
    parser.add_argument("--after",   default="pipeline_start",
                        help="Print all lines after first line matching this string "
                             "(default: 'pipeline_start'). Pass '' to print everything.")
    parser.add_argument("--all",     action="store_true",
                        help="Shortcut for --after '' (print all lines from --since window)")
    args = parser.parse_args()

    filter_after = "" if args.all else args.after

    token = os.environ.get("RAILWAY_TOKEN", "")
    if not token:
        print("ERROR: RAILWAY_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    try:
        project_id, service_id = find_service(token)
        deployment_id = latest_deployment(token, project_id, service_id)
        tail_logs(token, deployment_id, since_seconds=args.since, filter_after=filter_after)
    except KeyboardInterrupt:
        print("\n[railway] Stopped.")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

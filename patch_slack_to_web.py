"""
Move Slack agent from Scheduled Deployment into the FastAPI web server.
Runs as a background loop every 30 seconds — more reliable than cron.
"""
import base64, os

# Read current app/main.py
main_path = "app/main.py"
try:
    existing = open(main_path).read()
except FileNotFoundError:
    existing = ""

new_main = '''"""
Beezy Agents — FastAPI web server with background Slack agent.
The Slack agent runs every 30 seconds as a background task.
"""
import sys
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _slack_loop():
    """Background loop — polls Slack every 30 seconds."""
    while True:
        try:
            from agents.slack_agent import run_once
            run_once()
        except Exception as e:
            print(f"[slack_loop] error: {e}")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_slack_loop())
    print("[app] Slack agent background loop started (every 30s)")
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "slack_agent": "running"}
'''

os.makedirs("app", exist_ok=True)
open(main_path, "w").write(new_main)
print("app/main.py updated — Slack agent now runs in web server background")
print()
print("Redeploy the WEB deployment (not scheduled) in Replit Deployments tab.")
print("The Slack agent will run every 30 seconds as long as the web server is up.")

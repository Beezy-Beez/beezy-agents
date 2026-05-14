"""
Approval endpoint — add this to your FastAPI app (run.py or src/main.py).

GET /api/approve-week/{week_start}?token=xxx
  -> verifies token, marks week as approved in DB, returns confirmation HTML
"""
import hashlib, os
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from db.connection import get_conn

router = APIRouter()


def _expected_token(week_start: str) -> str:
    secret = os.environ.get("BEEZY_ANTHROPIC_API_KEY", "secret")
    return hashlib.sha256((week_start + secret).encode()).hexdigest()[:16]


@router.get("/api/approve-week/{week_start}", response_class=HTMLResponse)
async def approve_week(week_start: str, token: str = ""):
    expected = _expected_token(week_start)
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")

    try:
        d = date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format.")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE calendar_approvals SET approved_at = NOW() WHERE week_start = %s",
                (d,)
            )
            if cur.rowcount == 0:
                # Insert if somehow missing
                cur.execute(
                    "INSERT INTO calendar_approvals (week_start, token, approved_at) VALUES (%s,%s,NOW()) ON CONFLICT (week_start) DO UPDATE SET approved_at = NOW()",
                    (d, token)
                )
        conn.commit()

    return HTMLResponse("""
    <!DOCTYPE html><html><body style="font-family:Georgia,serif;background:#faf6ee;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;">
    <div style="text-align:center;padding:40px;background:#fff;border-radius:12px;border:1px solid #e8dcc8;max-width:420px;">
      <div style="font-size:64px;margin-bottom:16px;">✅</div>
      <h1 style="color:#4a7c59;font-size:24px;margin:0 0 12px;">Week Approved</h1>
      <p style="color:#666;margin:0;">Week of <strong>""" + week_start + """</strong> is approved.<br>
      Slots will execute automatically each morning at 8am ET.</p>
    </div></body></html>
    """)

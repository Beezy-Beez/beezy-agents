"""Cookie-gated Hive Mind subscription gate.

Generates the HTML + JS block used on Hive Mind issue pages and episode pages.

Gate states
───────────
  No hm_subscriber cookie → form visible, library hidden
  Cookie present          → library visible, form hidden
  Form submit success     → set cookie (365d) → reveal library instantly

Cookie: name=hm_subscriber, value=1, 365-day expiry, SameSite=Lax

Library data
────────────
Issues are embedded as JSON at page-build time (instant display).
If REPLIT_DOMAIN is set, JS also fetches /api/hive-mind/issues for fresh data
and updates the list in the background.
"""
from __future__ import annotations

import json as _json
import os


# ── DB helper ─────────────────────────────────────────────────────────────────

def _get_published_issues() -> list[dict]:
    """Pull all published Hive Mind issues from DB, newest first."""
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT number, subject_line, page_dek, shopify_page_url,
                          cover_image_url, pillar
                   FROM issues
                   WHERE status IN ('scheduled', 'published')
                     AND shopify_page_url IS NOT NULL
                   ORDER BY number DESC"""
            ).fetchall()
        return [
            {
                "number": r[0],
                "title":  r[1] or "",
                "dek":    (r[2] or "")[:160],
                "url":    r[3] or "#",
                "img":    r[4] or "",
                "pillar": r[5] or "",
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[hm_gate] DB query failed (non-fatal): {exc}")
        return []


# ── HTML ──────────────────────────────────────────────────────────────────────

_GATE_HTML = """<div id="hm-gate" style="<<<BOX_STYLE>>>">
<div id="hm-gate-form">
<h2 style="font-size:24px;color:#2c2417;margin:0 0 12px 0;font-family:Georgia,serif;font-weight:bold;">Get The Hive Mind in Your Inbox</h2>
<p style="font-size:18px;line-height:1.75;color:#5a4a3a;margin:0 0 25px 0;font-family:Georgia,serif;">One sleep science deep-dive every three days. No fluff. No products pushed. Just the research and what it means for your nights.</p>
<form id="hm-subscribe-form" style="margin:0 auto;display:inline-block;">
<table cellpadding="0" cellspacing="0" border="0"><tr>
<td style="padding-right:8px;"><input type="email" id="hm-email" placeholder="your@email.com" required style="width:280px;padding:14px 18px;font-size:16px;font-family:Georgia,serif;border:1px solid #d4a847;border-radius:4px;background:#fffdf7;color:#2c2417;box-sizing:border-box;"></td>
<td><button type="submit" style="padding:14px 28px;font-size:16px;font-family:Georgia,serif;background-color:#8b4513;color:#fffdf7;border-radius:4px;font-weight:bold;border:none;cursor:pointer;">Subscribe</button></td>
</tr></table>
</form>
<p id="hm-form-error" style="display:none;font-size:16px;color:#8b4513;margin:15px 0 0;font-family:Georgia,serif;">Something went wrong. Please try again.</p>
</div>
<div id="hm-gate-lib" style="display:none;text-align:left;">
<h2 style="font-size:22px;color:#2c2417;margin:0 0 6px 0;font-family:Georgia,serif;font-weight:bold;">Your Hive Mind Library</h2>
<p style="font-size:15px;color:#5a4a3a;margin:0 0 20px 0;font-family:Georgia,serif;">Every issue, newest first.</p>
<div id="hm-lib-items"></div>
</div>
</div>"""

# ── JS (use placeholder substitution — no f-strings to avoid brace escaping) ──

_GATE_SCRIPT = """<script>(function(){
var ISSUES=<<<ISSUES_JSON>>>;
var API=<<<API_URL_JSON>>>;
function pad3(n){return n?('000'+n).slice(-3):'???';}
function esc(s){var d=document.createElement('div');d.appendChild(document.createTextNode(s));return d.innerHTML;}
function renderItems(arr){
  var h='';
  for(var i=0;i<arr.length;i++){
    var iss=arr[i];
    h+='<div style="border-bottom:1px solid #e8dcc8;padding:14px 0;display:flex;gap:14px;align-items:flex-start;">';
    if(iss.img)h+='<img src="'+esc(iss.img)+'" alt="" style="flex:0 0 72px;width:72px;height:48px;object-fit:cover;border-radius:3px;">';
    h+='<div style="flex:1;min-width:0;">';
    h+='<p style="font-size:12px;color:#8b7355;margin:0 0 3px;font-family:Georgia,serif;text-transform:uppercase;letter-spacing:.8px;">Issue '+pad3(iss.number);
    if(iss.pillar)h+=' · '+esc(iss.pillar);
    h+='</p>';
    h+='<a href="'+esc(iss.url)+'" style="font-size:16px;color:#2c2417;font-family:Georgia,serif;font-weight:bold;text-decoration:none;line-height:1.3;">'+esc(iss.title)+'</a>';
    if(iss.dek)h+='<p style="font-size:14px;color:#5a4a3a;margin:4px 0 0;font-family:Georgia,serif;">'+esc(iss.dek)+'</p>';
    h+='</div></div>';
  }
  if(!h)h='<p style="font-size:16px;color:#5a4a3a;font-family:Georgia,serif;">No issues published yet.</p>';
  return h;
}
function showLib(arr){
  var form=document.getElementById('hm-gate-form');
  var lib=document.getElementById('hm-gate-lib');
  var items=document.getElementById('hm-lib-items');
  if(form)form.style.display='none';
  if(items)items.innerHTML=renderItems(arr);
  if(lib)lib.style.display='block';
}
function getCookie(n){var m=document.cookie.match('(?:^|;)\\s*'+n+'=([^;]*)');return m?decodeURIComponent(m[1]):null;}
function setCookie(n,v,days){var d=new Date();d.setTime(d.getTime()+days*864e5);document.cookie=n+'='+encodeURIComponent(v)+';expires='+d.toUTCString()+';path=/;SameSite=Lax';}
if(getCookie('hm_subscriber')==='1'){
  showLib(ISSUES);
  if(API){fetch(API+'/api/hive-mind/issues').then(function(r){return r.json();}).then(function(fresh){if(fresh&&fresh.length)showLib(fresh);}).catch(function(){});}
}
var form=document.getElementById('hm-subscribe-form');
if(form){
  form.addEventListener('submit',function(e){
    e.preventDefault();
    var email=document.getElementById('hm-email').value;
    if(!email)return;
    var btn=form.querySelector('button[type="submit"]');
    var orig=btn.textContent;btn.disabled=true;btn.textContent='Subscribing...';
    fetch('https://a.klaviyo.com/client/subscriptions/?company_id=W8SW8k',{
      method:'POST',
      headers:{'Content-Type':'application/json','revision':'2024-10-15'},
      body:JSON.stringify({data:{type:'subscription',attributes:{
        custom_source:'Hive Mind Gate',
        profile:{data:{type:'profile',attributes:{email:email}}}
      },relationships:{list:{data:{type:'list',id:'Y6VSre'}}}}})
    }).then(function(r){
      if(r.ok||r.status===202){
        setCookie('hm_subscriber','1',365);
        showLib(ISSUES);
      }else{
        document.getElementById('hm-form-error').style.display='block';
        btn.disabled=false;btn.textContent=orig;
      }
    }).catch(function(){
      document.getElementById('hm-form-error').style.display='block';
      btn.disabled=false;btn.textContent=orig;
    });
  });
}
})();</script>"""

_DEFAULT_BOX_STYLE = (
    "background-color:#f5f0e8;padding:40px 30px;border-radius:8px;"
    "margin:0 0 30px 0;text-align:center;"
)

_EPISODE_BOX_STYLE = (
    "background:linear-gradient(135deg,#f5ede0,#faf6ee);border:1px solid #d9c5a8;"
    "border-radius:12px;padding:40px 32px;text-align:center;margin:40px 0;"
)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_gate(
    issues: list[dict] | None = None,
    style: str = "",
) -> str:
    """Return gate HTML + <script> block.

    Args:
        issues: Pre-fetched issue list. If None, pulled from DB.
        style:  CSS override for the outer container.
                Pass _EPISODE_BOX_STYLE for episode pages,
                leave empty for Hive Mind issue pages.
    """
    if issues is None:
        issues = _get_published_issues()

    api_base = os.environ.get("REPLIT_DOMAIN", "")
    if api_base and not api_base.startswith("http"):
        api_base = "https://" + api_base

    issues_json  = _json.dumps(issues,   ensure_ascii=False)
    api_url_json = _json.dumps(api_base, ensure_ascii=False)

    box_style = style or _DEFAULT_BOX_STYLE
    html = _GATE_HTML.replace("<<<BOX_STYLE>>>", box_style)
    script = (
        _GATE_SCRIPT
        .replace("<<<ISSUES_JSON>>>", issues_json)
        .replace("<<<API_URL_JSON>>>", api_url_json)
    )
    return html + "\n" + script


# Convenience alias for episode pages
def build_gate_episode(issues: list[dict] | None = None) -> str:
    return build_gate(issues=issues, style=_EPISODE_BOX_STYLE)

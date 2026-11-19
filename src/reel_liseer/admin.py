import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from reel_liseer import config
from reel_liseer.services import cookies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", include_in_schema=False)

_FAIL_WINDOW = 600  # seconds
_FAIL_LIMIT_PER_CLIENT = 8
_FAIL_LIMIT_GLOBAL = 60
_failures: dict[str, deque] = defaultdict(deque)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def _recent(key: str) -> deque:
    window = _failures[key]
    cutoff = time.monotonic() - _FAIL_WINDOW
    while window and window[0] < cutoff:
        window.popleft()
    return window


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def require_admin(request: Request) -> None:
    if not config.ADMIN_PASSWORD:
        raise HTTPException(status_code=404)

    key = _client_key(request)
    if len(_recent(key)) >= _FAIL_LIMIT_PER_CLIENT or len(_recent("*")) >= _FAIL_LIMIT_GLOBAL:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    supplied = supplied.strip()
    password_ok = False
    if scheme.lower() == "basic":
        try:
            username, _, password = base64.b64decode(supplied).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            username, password = "", ""
        # evaluate both so timing doesn't reveal which half was wrong
        user_ok = hmac.compare_digest(_digest(username), _digest(config.ADMIN_USERNAME))
        pass_ok = hmac.compare_digest(_digest(password), _digest(config.ADMIN_PASSWORD))
        password_ok = user_ok and pass_ok
    elif scheme.lower() == "bearer":  # scripts/curl: the password itself
        password_ok = hmac.compare_digest(_digest(supplied), _digest(config.ADMIN_PASSWORD))
    if password_ok:
        return

    now = time.monotonic()
    _failures[key].append(now)
    _failures["*"].append(now)
    logger.warning("Rejected admin request from %s", key)
    raise HTTPException(status_code=401, detail="Wrong username or password.")


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def page():
    if not config.ADMIN_PASSWORD:
        raise HTTPException(status_code=404)
    response = HTMLResponse(PAGE_HTML)
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return _no_store(response)


def _runtime_names() -> list[str]:
    from reel_liseer.services.downloader import JS_RUNTIMES

    return sorted(JS_RUNTIMES)


@router.get("/api/cookies", dependencies=[Depends(require_admin)])
def list_cookies():
    return _no_store(JSONResponse({
        "slots": cookies.status(),
        "allowed_domains": list(config.COOKIE_DOMAINS),
        "js_runtimes": _runtime_names(),
        "persistent_hint": not config.COOKIES_DIR.startswith(("/var/data", "/data", "/mnt")),
    }))


@router.post("/api/cookies", dependencies=[Depends(require_admin)])
async def upload_cookies(
    slot: str = Form("universal"),
    text: str = Form(""),
    domains: str = Form(""),
    file: Annotated[UploadFile | None, File()] = None,
):
    if file is not None and file.filename:
        raw = await file.read(config.COOKIES_MAX_BYTES + 1)
    elif text.strip():
        raw = text.encode()
    else:
        raise HTTPException(status_code=400, detail="Choose a cookies.txt file or paste its contents.")
    try:
        extra = cookies.parse_domains(domains)
        summary = await asyncio.to_thread(cookies.save_upload, raw, slot, extra)
    except cookies.CookieError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _no_store(JSONResponse(summary))


@router.delete("/api/cookies/{slot}", dependencies=[Depends(require_admin)])
def delete_cookies(slot: str):
    if not cookies.delete_slot(slot):
        raise HTTPException(status_code=404, detail="No such cookie set.")
    return _no_store(JSONResponse({"deleted": cookies.slot_name(slot)}))


class TestRequest(BaseModel):
    url: str


@router.post("/api/test", dependencies=[Depends(require_admin)])
async def test_url(body: TestRequest):
    from reel_liseer.services.downloader import probe_url

    if not body.url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Enter a full http(s) link.")
    try:
        info = await asyncio.wait_for(asyncio.to_thread(probe_url, body.url), timeout=90)
    except TimeoutError:
        return _no_store(JSONResponse({"ok": False, "error": "Timed out after 90 s."}))
    except Exception as exc:  # noqa: BLE001 - surface yt-dlp's message to the admin
        message = str(exc).replace("\n", " ")[:600]
        return _no_store(JSONResponse({"ok": False, "error": message}))
    return _no_store(JSONResponse({"ok": True, **info}))


PAGE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>reel-liseer admin</title>
<style>
:root{color-scheme:light;--bg:#f6f5f3;--fg:#1c1917;--mut:#6b6560;--card:#fff;--field:#fff;--bd:#dedad6;--ac:#2563eb;--ac-fg:#fff;--bad:#b91c1c;--ok:#15803d}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#141211;--fg:#f3f1ee;--mut:#a39d97;--card:#1d1b19;--field:#262320;--bd:#37332f;--ac:#3b82f6;--ac-fg:#fff;--bad:#f87171;--ok:#4ade80}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:680px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
p.sub{color:var(--mut);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px;margin-bottom:16px}
label{display:block;font-size:13px;font-weight:500;color:var(--mut);margin:14px 0 6px}
label:first-of-type{margin-top:0}
input,select,textarea,button{font:inherit;color:inherit}
input[type=text],input[type=password],select,textarea{width:100%;padding:9px 12px;border:1px solid var(--bd);border-radius:8px;background:var(--field);color:var(--fg)}
input:focus,select:focus,textarea:focus{outline:2px solid var(--ac);outline-offset:-1px;border-color:transparent}
select{appearance:none;-webkit-appearance:none;padding-right:34px;cursor:pointer;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' fill='none' stroke='%23999' stroke-width='2'%3E%3Cpath d='m1 1.5 5 5 5-5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center}
option{background:var(--field);color:var(--fg)}
input[type=file]{width:100%;padding:6px;border:1px dashed var(--bd);border-radius:8px;background:var(--field);color:var(--mut)}
input[type=file]::file-selector-button{margin-right:10px;padding:6px 12px;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
textarea{min-height:96px;resize:vertical;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
button{padding:9px 16px;border:0;border-radius:8px;background:var(--ac);color:var(--ac-fg);font-weight:500;cursor:pointer;margin-top:16px}
button:hover:not(:disabled){filter:brightness(1.1)}
button:disabled{opacity:.5;cursor:default}
button.ghost{background:transparent;color:var(--bad);border:1px solid var(--bd);padding:5px 12px;margin:0}
.row{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 0;border-top:1px solid var(--bd)}
.row:first-child{border-top:0;padding-top:0}
small,.mut{color:var(--mut)}
#meta{margin:12px 0 0;font-size:13px}
#msg{min-height:22px;margin:0 0 12px;font-size:14px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.hidden{display:none}
</style></head><body><main>
<h1>reel-liseer admin</h1><p class="sub">Server-wide cookies for yt-dlp. Nothing here is visible to bot users.</p>
<p id="msg" role="status"></p>

<section class="card" id="login"><h2>Admin login</h2>
<label for="user">Username</label><input id="user" type="text" autocomplete="username">
<label for="token">Password</label><input id="token" type="password" autocomplete="current-password">
<button id="unlock">Log in</button></section>

<div id="app" class="hidden">
<section class="card"><h2>Stored cookies</h2><div id="slots"></div>
<p class="mut" id="meta"></p></section>

<section class="card"><h2>Upload cookies.txt</h2>
<label for="slot">Label (use <b>universal</b> for a combined file, or one per site)</label>
<select id="slot"><option>universal</option><option>youtube</option><option>instagram</option>
<option>tiktok</option><option>facebook</option><option>twitter</option><option>reddit</option><option value="other">other site\u2026</option></select>
<label for="extra" id="extralabel">Other sites to keep (optional, comma-separated, e.g. vimeo.com)</label>
<input id="extra" type="text" placeholder="vimeo.com, dailymotion.com" autocomplete="off">
<label for="file">Netscape-format cookies.txt</label><input id="file" type="file" accept=".txt,text/plain">
<label for="text">...or paste the file contents</label><textarea id="paste" spellcheck="false"></textarea>
<button id="upload">Upload</button>
<p class="mut"><small>Only cookies for the allowed domains are kept; everything else in the file is discarded
before it is saved. Re-uploading a label replaces it.</small></p></section>

<section class="card"><h2>Test a link</h2><label for="url">Public post URL</label>
<input id="url" type="text" placeholder="https://www.instagram.com/reel/..."><button id="test">Test (no download)</button></section>
</div>

<script>
const $=id=>document.getElementById(id);
const say=(t,c)=>{$('msg').textContent=t;$('msg').className=c||''};
const tok=()=>{try{return sessionStorage.getItem('t')||''}catch(e){return ''}};
const b64=s=>btoa(unescape(encodeURIComponent(s)));
async function api(path,opt){opt=opt||{};opt.headers=Object.assign({Authorization:'Basic '+tok()},opt.headers||{});
 const r=await fetch('/admin/api'+path,opt);let j={};try{j=await r.json()}catch(e){}
 if(!r.ok)throw new Error(j.detail||('HTTP '+r.status));return j}
const when=s=>s?new Date(s*1000).toLocaleDateString():'session only';
async function refresh(){
 const d=await api('/cookies');const box=$('slots');box.textContent='';
 if(!d.slots.length){const p=document.createElement('p');p.className='mut';p.textContent='No cookies stored yet.';box.append(p)}
 d.slots.forEach(s=>{const row=document.createElement('div');row.className='row';const info=document.createElement('div');
  const b=document.createElement('b');b.textContent=s.slot;info.append(b,document.createElement('br'));
  const sm=document.createElement('small');sm.textContent=Object.entries(s.domains).map(([k,v])=>k+' ('+v+')').join(', ')+' \\u2014 earliest expiry: '+when(s.earliest_expiry);
  info.append(sm);const del=document.createElement('button');del.className='ghost';del.textContent='Delete';
  del.onclick=async()=>{try{await api('/cookies/'+encodeURIComponent(s.slot),{method:'DELETE'});say('Deleted '+s.slot,'ok');refresh()}catch(e){say(e.message,'bad')}};
  row.append(info,del);box.append(row)});
 $('meta').textContent='Allowed domains: '+d.allowed_domains.join(', ')+'. JS runtime for YouTube: '+(d.js_runtimes.join(', ')||'NONE (YouTube will fail)')+'.'+(d.persistent_hint?' Storage may be ephemeral (see README: COOKIES_DIR / COOKIES_B64).':'')}
async function enter(){try{await refresh();$('login').classList.add('hidden');$('app').classList.remove('hidden');say('')}catch(e){
 $('app').classList.add('hidden');$('login').classList.remove('hidden');say(e.message,'bad')}}
$('unlock').onclick=()=>{try{sessionStorage.setItem('t',b64($('user').value.trim()+':'+$('token').value))}catch(e){};$('token').value='';enter()};
$('token').addEventListener('keydown',e=>{if(e.key==='Enter')$('unlock').click()});
const syncExtra=()=>{const o=$('slot').value==='other';$('extralabel').textContent=o?'Site domain (e.g. vimeo.com)':'Other sites to keep (optional, comma-separated, e.g. vimeo.com)';$('extra').placeholder=o?'vimeo.com':'vimeo.com, dailymotion.com'};
$('slot').onchange=syncExtra;
$('upload').onclick=async()=>{const f=new FormData();const extra=$('extra').value.trim();
 if($('slot').value==='other'&&!extra){say('Enter the site domain first.','bad');return}
 f.append('slot',$('slot').value==='other'?extra.split(/[\\s,]+/)[0].replace(/^[a-z]+:\\/\\//i,'').replace(/^www\\./i,'').split('/')[0]:$('slot').value);f.append('domains',extra);
 if($('file').files[0])f.append('file',$('file').files[0]);else f.append('text',$('paste').value);
 $('upload').disabled=true;try{const r=await api('/cookies',{method:'POST',body:f});
  say('Saved '+r.kept+' cookies to "'+r.slot+'" ('+r.skipped_domain+' skipped: other sites, '+r.expired+' expired, '+r.invalid+' invalid lines).','ok');
  $('file').value='';$('paste').value='';$('extra').value='';refresh()}catch(e){say(e.message,'bad')}$('upload').disabled=false};
$('test').onclick=async()=>{$('test').disabled=true;say('Testing\\u2026');try{
 const r=await api('/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('url').value.trim()})});
 r.ok?say('Works: '+r.extractor+' \\u2014 "'+(r.title||'untitled')+'" ('+r.formats+' formats)','ok'):say('Failed: '+r.error,'bad')}
 catch(e){say(e.message,'bad')}$('test').disabled=false};
if(tok())enter();
</script></main></body></html>
"""

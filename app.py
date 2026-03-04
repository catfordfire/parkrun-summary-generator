#!/usr/bin/env python3
import os, sys, tempfile, datetime
from flask import Flask, request, render_template_string, send_file, jsonify
import cloudscraper

sys.path.insert(0, "/app")
from parkrun_summary import build_weekly_summary_html

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

SUMMARIES_DIR = "/tmp/summaries"
os.makedirs(SUMMARIES_DIR, exist_ok=True)

# Thread-local storage for fetched HTML (lives for one generate cycle)
_fetch_store = {}


def sanitise_event_name(name: str) -> str:
    """Strip trailing 'parkrun' (case-insensitive) so we don't get 'Catford parkrun parkrun'."""
    import re
    return re.sub(r'\s+parkrun\s*$', '', name, flags=re.IGNORECASE).strip()


def parkrun_url(country_url: str, slug: str, page: str) -> str:
    """Build a parkrun results URL. page = eventhistory | latestresults"""
    base = country_url.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return f"{base}/{slug}/results/{page}/"


def fetch_parkrun_page(url: str) -> str:
    """Fetch a parkrun page using cloudscraper to bypass Cloudflare."""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    resp = scraper.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMiAzMiI+CiAgPHJlY3Qgd2lkdGg9IjMyIiBoZWlnaHQ9IjMyIiByeD0iNiIgZmlsbD0iIzAwNEYyRCIvPgogIDwhLS0gaGVhZCAtLT4KICA8Y2lyY2xlIGN4PSIyMSIgY3k9IjciIHI9IjIuNSIgZmlsbD0id2hpdGUiLz4KICA8IS0tIGJvZHkgLS0+CiAgPGxpbmUgeDE9IjIxIiB5MT0iOS41IiB4Mj0iMTkiIHkyPSIxNiIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KICA8IS0tIGxlZnQgYXJtIChiYWNrKSAtLT4KICA8bGluZSB4MT0iMjAuNSIgeTE9IjEyIiB4Mj0iMTUiIHkyPSIxMSIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDwhLS0gcmlnaHQgYXJtIChmb3J3YXJkKSAtLT4KICA8bGluZSB4MT0iMjAuNSIgeTE9IjEyIiB4Mj0iMjQiIHkyPSIxNCIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDwhLS0gbGVmdCBsZWcgKGZvcndhcmQpIC0tPgogIDxsaW5lIHgxPSIxOSIgeTE9IjE2IiB4Mj0iMTUiIHkyPSIyMiIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDwhLS0gcmlnaHQgbGVnIChiYWNrKSAtLT4KICA8bGluZSB4MT0iMTkiIHkxPSIxNiIgeDI9IjIzIiB5Mj0iMjIiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KICA8IS0tIGxvd2VyIGxlZnQgbGVnIC0tPgogIDxsaW5lIHgxPSIxNSIgeTE9IjIyIiB4Mj0iMTMiIHkyPSIyNiIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDwhLS0gbG93ZXIgcmlnaHQgbGVnIC0tPgogIDxsaW5lIHgxPSIyMyIgeTE9IjIyIiB4Mj0iMjQiIHkyPSIyNiIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDwhLS0gInAiIGxldHRlcm1hcmsgLS0+CiAgPHRleHQgeD0iNSIgeT0iMjUiIGZvbnQtZmFtaWx5PSJBcmlhbCxzYW5zLXNlcmlmIiBmb250LXdlaWdodD0iOTAwIiBmb250LXNpemU9IjE2IiBmaWxsPSJ3aGl0ZSI+cDwvdGV4dD4KPC9zdmc+">
<title>parkrun - Weekly Summary Generator</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{--green:#2d6a4f;--mid:#40916c;--light:#74c69d;--pale:#d8f3dc;--cream:#f8faf5;--text:#1b2e22;--muted:#6b7c72;--white:#fff;--red:#c0392b;--shadow:0 4px 24px rgba(45,106,79,.10)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"DM Sans",sans-serif;background:var(--cream);color:var(--text);min-height:100vh;display:flex;flex-direction:column}
header{background:var(--green);padding:28px 40px;display:flex;align-items:center;gap:16px}
.logo{width:44px;height:44px;background:var(--light);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.4em;flex-shrink:0}
header h1{font-family:"DM Serif Display",serif;font-size:1.5em;color:var(--white);line-height:1.1}
header p{font-size:.82em;color:var(--light);margin-top:2px;font-weight:300}
main{flex:1;max-width:640px;margin:48px auto;padding:0 24px;width:100%}
.card{background:var(--white);border-radius:16px;padding:36px;box-shadow:var(--shadow);margin-bottom:20px}
.card h2{font-family:"DM Serif Display",serif;font-size:1.25em;color:var(--green);margin-bottom:6px}
.hint{font-size:.83em;color:var(--muted);margin-bottom:24px;line-height:1.5}
/* search */
.sw{position:relative;margin-bottom:14px}
.sw input{width:100%;padding:12px 40px 12px 42px;border:2px solid var(--light);border-radius:10px;font-family:"DM Sans",sans-serif;font-size:.95em;color:var(--text);background:var(--cream);outline:none;transition:border-color .2s}
.sw input:focus{border-color:var(--mid);background:white}
.sw input.sel{border-color:var(--mid);background:var(--pale)}
.si{position:absolute;left:14px;top:50%;transform:translateY(-50%);pointer-events:none}
.clrbtn{position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:1.1em;display:none;line-height:1}
.sw input.sel ~ .clrbtn{display:block}
.drop{position:absolute;top:calc(100% + 4px);left:0;right:0;background:white;border:1px solid var(--light);border-radius:10px;max-height:220px;overflow-y:auto;z-index:100;box-shadow:0 8px 24px rgba(0,0,0,.10);display:none}
.drop.open{display:block}
.di{padding:10px 16px;font-size:.88em;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.di:hover,.di.act{background:var(--pale)}
.dco{font-size:.78em;color:var(--muted)}
.dm{padding:12px 16px;font-size:.85em;color:var(--muted);font-style:italic}
/* selected badge */
.sbadge{display:none;align-items:center;gap:8px;background:var(--pale);border:1px solid var(--light);border-radius:8px;padding:8px 12px;margin-bottom:20px;font-size:.85em;color:var(--green)}
.sbadge.on{display:flex}
.sbadge .co{color:var(--muted);font-size:.82em;margin-left:auto}
/* fetch panel */
.fetch-panel{display:none;margin-bottom:20px}
.fetch-panel.on{display:block}
.fetch-links{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.fetch-link{display:flex;flex-direction:column;gap:4px;background:var(--cream);border:1px solid var(--light);border-radius:10px;padding:14px 16px}
.fetch-link .lbl{font-size:.75em;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--green)}
.fetch-link .url{font-size:.72em;color:var(--muted);word-break:break-all;font-family:monospace}
.fetch-link .status{font-size:.78em;margin-top:4px}
.fetch-link .status.ok{color:#1e8449}
.fetch-link .status.er{color:var(--red)}
/* buttons */
.btn{width:100%;padding:15px;background:var(--green);color:var(--white);border:none;border-radius:10px;font-family:"DM Sans",sans-serif;font-size:1em;font-weight:600;cursor:pointer;transition:background .2s,transform .1s;margin-top:4px}
.btn:hover:not(:disabled){background:var(--mid)}
.btn:active:not(:disabled){transform:scale(.99)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.secondary{background:#f0f4f2;color:var(--green);border:1px solid var(--light)}
.btn.secondary:hover:not(:disabled){background:var(--pale)}
/* status box */
#statbox{margin-top:16px;padding:16px 20px;border-radius:10px;font-size:.9em;display:none;line-height:1.5}
#statbox.ld{display:block;background:var(--pale);color:var(--green);border:1px solid var(--light)}
#statbox.ok{display:block;background:#eafaf1;color:#1e8449;border:1px solid #a9dfbf}
#statbox.er{display:block;background:#fdedec;color:var(--red);border:1px solid #f1948a}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--light);border-top-color:var(--green);border-radius:50%;animation:spin .7s linear infinite;margin-right:8px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.dlb{display:inline-block;margin-top:12px;padding:10px 20px;background:var(--green);color:var(--white);border-radius:8px;text-decoration:none;font-weight:600;font-size:.88em;transition:background .2s}
.dlb:hover{background:var(--mid)}
/* steps */
.steps{counter-reset:step}
.step{display:flex;gap:14px;margin-bottom:14px;align-items:flex-start}
.step:last-child{margin-bottom:0}
.stepn{width:26px;height:26px;background:var(--pale);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.78em;font-weight:700;color:var(--green);flex-shrink:0;margin-top:1px}
.stept{font-size:.88em;line-height:1.5}
footer{text-align:center;color:var(--muted);font-size:.75em;padding:20px}
</style>
</head>
<body>
<header>
  <div class="logo">&#127939;</div>
  <div><h1>parkrun</h1><p>Weekly Summary Generator</p></div>
</header>
<main>
  <div class="card">
    <h2>How to use</h2>
    <p class="hint">Search for your parkrun event, fetch the data, and generate your weekly summary.</p>
    <div class="steps">
      <div class="step"><div class="stepn">1</div><div class="stept">Search for and select your parkrun event below</div></div>
      <div class="step"><div class="stepn">2</div><div class="stept">Click <strong>Fetch Data</strong> &mdash; the server retrieves your event&rsquo;s results automatically</div></div>
      <div class="step"><div class="stepn">3</div><div class="stept">Click <strong>Generate Summary</strong></div></div>
    </div>
  </div>

  <div class="card">
    <h2>Generate Summary</h2>
    <p class="hint">Select your event to get started.</p>

    <!-- Event search -->
    <div class="sw">
      <span class="si">&#128269;</span>
      <input type="text" id="evtsearch" placeholder="Search for your parkrun (e.g. Bushy)..." autocomplete="off">
      <button class="clrbtn" id="clrbtn" title="Clear">&#10005;</button>
      <div class="drop" id="dropdown"></div>
    </div>
    <div class="sbadge" id="selbadge">
      <span>&#10003; <strong id="sellabel"></strong></span>
      <span class="co" id="selcoords"></span>
    </div>

    <!-- Fetch panel (shown after event selected) -->
    <div class="fetch-panel" id="fetchpanel">
      <div class="fetch-links">
        <div class="fetch-link">
          <span class="lbl">&#128203; Event History</span>
          <span class="url" id="url-history"></span>
          <span class="status" id="st-history"></span>
        </div>
        <div class="fetch-link">
          <span class="lbl">&#127937; Latest Results</span>
          <span class="url" id="url-latest"></span>
          <span class="status" id="st-latest"></span>
        </div>
      </div>
      <button class="btn" id="fetchbtn">Fetch Data</button>
      <button class="btn secondary" id="genbtn" disabled style="margin-top:10px">Generate Summary</button>
    </div>

    <div id="statbox"></div>
  </div>
</main>
<footer>parkrun Summary Generator &nbsp;&middot;&nbsp; Data from parkrun.org.uk</footer>
<script>
let AE=[],SE=null,dataReady=false;

// ── Load events ────────────────────────────────────────────────────────────
async function loadEvents(){
  const dd=document.getElementById('dropdown');
  dd.innerHTML='<div class="dm">Loading events...</div>';dd.classList.add('open');
  try{
    const data=await(await fetch('https://images.parkrun.com/events.json')).json();
    const cm={};
    for(const[k,v]of Object.entries(data.countries||{})){
      const u=(v.url||'').replace(/\/$/,'');
      cm[parseInt(k)]={code:u.split('.').pop().toUpperCase(),url:u};
    }
    AE=(data.events?.features||[]).map(f=>{
      const p=f.properties||{},ci=cm[p.countrycode]||{},c=f.geometry?.coordinates||[null,null];
      return{slug:(p.eventname||'').toLowerCase(),name:p.EventLongName||p.EventShortName||'',country:ci.code||'',countryUrl:ci.url||'',lon:c[0],lat:c[1]};
    }).filter(e=>e.slug&&e.name&&e.countryUrl);
    dd.classList.remove('open');
  }catch(e){dd.innerHTML='<div class="dm">Failed to load: '+e.message+'</div>';}
}

// ── Search / dropdown ──────────────────────────────────────────────────────
const ES=document.getElementById('evtsearch'),DD=document.getElementById('dropdown');
ES.addEventListener('focus',()=>{if(!AE.length)loadEvents();else if(ES.value.trim())renderDD(ES.value.trim());});
ES.addEventListener('input',()=>{SE=null;dataReady=false;ES.classList.remove('sel');document.getElementById('selbadge').classList.remove('on');document.getElementById('fetchpanel').classList.remove('on');resetStatus();chk();renderDD(ES.value.trim());});
ES.addEventListener('keydown',e=>{
  const it=[...DD.querySelectorAll('.di')],ix=it.findIndex(x=>x.classList.contains('act'));
  if(e.key==='ArrowDown'){e.preventDefault();const n=it[(ix+1)%it.length];if(n){it[ix]?.classList.remove('act');n.classList.add('act');n.scrollIntoView({block:'nearest'});}}
  else if(e.key==='ArrowUp'){e.preventDefault();const n=it[(ix-1+it.length)%it.length];if(n){it[ix]?.classList.remove('act');n.classList.add('act');n.scrollIntoView({block:'nearest'});}}
  else if(e.key==='Enter'){e.preventDefault();it[ix]?.click();}
  else if(e.key==='Escape')DD.classList.remove('open');
});
document.addEventListener('click',e=>{if(!e.target.closest('.sw'))DD.classList.remove('open');});
document.getElementById('clrbtn').addEventListener('click',()=>{
  ES.value='';ES.classList.remove('sel');SE=null;dataReady=false;
  document.getElementById('selbadge').classList.remove('on');
  document.getElementById('fetchpanel').classList.remove('on');
  resetStatus();DD.classList.remove('open');chk();ES.focus();
});

function renderDD(q){
  if(!q){DD.classList.remove('open');return;}
  const ql=q.toLowerCase(),m=AE.filter(e=>e.name.toLowerCase().includes(ql)||e.slug.includes(ql)).slice(0,30);
  DD.innerHTML=m.length?m.map((e,i)=>'<div class="di" data-i="'+i+'"><span>'+e.name+'</span><span class="dco">'+e.country+'</span></div>').join(''):'<div class="dm">No events found.</div>';
  DD.querySelectorAll('.di').forEach(el=>el.addEventListener('click',()=>pickEvent(m[parseInt(el.dataset.i)])));
  DD.classList.add('open');
}

function pickEvent(ev){
  SE=ev;dataReady=false;ES.value=ev.name;ES.classList.add('sel');DD.classList.remove('open');
  document.getElementById('sellabel').textContent=ev.name+' ('+ev.country+')';
  document.getElementById('selcoords').textContent=ev.lat!=null?ev.lat.toFixed(4)+', '+ev.lon.toFixed(4):'';
  document.getElementById('selbadge').classList.add('on');
  // Show fetch panel and populate URLs
  const base=ev.countryUrl.startsWith('http')?ev.countryUrl:'https://'+ev.countryUrl;
  const histUrl=base.replace(/\/$/,'')+'/'+ev.slug+'/results/eventhistory/';
  const latUrl=base.replace(/\/$/,'')+'/'+ev.slug+'/results/latestresults/';
  document.getElementById('url-history').textContent=histUrl;
  document.getElementById('url-latest').textContent=latUrl;
  document.getElementById('st-history').textContent='';document.getElementById('st-history').className='status';
  document.getElementById('st-latest').textContent='';document.getElementById('st-latest').className='status';
  document.getElementById('genbtn').disabled=true;
  document.getElementById('fetchpanel').classList.add('on');
  resetStatus();chk();
}

// ── Fetch data ─────────────────────────────────────────────────────────────
document.getElementById('fetchbtn').addEventListener('click',async()=>{
  if(!SE)return;
  const fb=document.getElementById('fetchbtn'),gb=document.getElementById('genbtn');
  fb.disabled=true;gb.disabled=true;dataReady=false;
  document.getElementById('st-history').textContent='Fetching...';document.getElementById('st-history').className='status';
  document.getElementById('st-latest').textContent='Fetching...';document.getElementById('st-latest').className='status';
  resetStatus();
  try{
    const resp=await fetch('/fetch-event',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slug:SE.slug,country_url:SE.countryUrl,event_name:SE.name,lat:SE.lat,lon:SE.lon})
    });
    const d=await resp.json();
    if(d.ok){
      document.getElementById('st-history').className='status ok';
      document.getElementById('st-history').innerHTML='&#10003; Fetched ('+d.event_count+' events)';
      document.getElementById('st-latest').className='status ok';
      document.getElementById('st-latest').innerHTML='&#10003; Fetched ('+d.runner_count+' runners)';
      dataReady=true;gb.disabled=false;
    }else{
      document.getElementById('st-history').textContent='Error';document.getElementById('st-history').className='status er';
      document.getElementById('st-latest').textContent='Error';document.getElementById('st-latest').className='status er';
      const sb=document.getElementById('statbox');sb.className='er';
      sb.innerHTML='&#10007; Fetch failed: '+d.error
        +(d.detail?'<pre style="font-size:.72em;margin-top:8px;white-space:pre-wrap;text-align:left;max-height:200px;overflow-y:auto">'+d.detail+'</pre>':'');
    }
  }catch(e){
    const sb=document.getElementById('statbox');sb.className='er';
    sb.innerHTML='&#10007; Request failed: '+e.message;
  }
  fb.disabled=false;
});

// ── Generate ───────────────────────────────────────────────────────────────
document.getElementById('genbtn').addEventListener('click',async()=>{
  if(!SE||!dataReady)return;
  document.getElementById('genbtn').disabled=true;
  document.getElementById('fetchbtn').disabled=true;
  const sb=document.getElementById('statbox');
  sb.className='ld';sb.innerHTML='<span class="spin"></span> Generating summary...';
  try{
    const ctrl=new AbortController(),to=setTimeout(()=>ctrl.abort(),180000);
    const resp=await fetch('/generate',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slug:SE.slug}),
      signal:ctrl.signal
    });
    clearTimeout(to);
    const d=await resp.json();
    if(d.ok){
      // Poll until filesize is stable (same size twice in a row = fully flushed)
      sb.innerHTML='<span class="spin"></span> Finalising...';
      let stable=false,lastSize=-1;
      for(let i=0;i<30;i++){
        await new Promise(r=>setTimeout(r,1000));
        try{
          const fr=await fetch('/filesize/'+d.filename);
          const fs=await fr.json();
          if(fs.size>0&&fs.size===lastSize){stable=true;break;}
          lastSize=fs.size;
        }catch(e){break;}
      }
      sb.className='ok';
      sb.innerHTML='&#10003; Summary generated for <strong>'+d.date+'</strong><br><br>'
        +'<a class="dlb" href="/view/'+d.filename+'" target="_blank">&#128065; View in Browser</a>'
        +'<br><small style="color:#555;margin-top:8px;display:block">If the summary looks incomplete, return to this page and click View in Browser again.</small>';
    }else{
      const det=d.traceback?'<pre style="font-size:.75em;margin-top:8px;white-space:pre-wrap;text-align:left">'+d.traceback+'</pre>':'';
      sb.className='er';sb.innerHTML='&#10007; Error: '+d.error+det;
    }
  }catch(e){
    sb.className='er';
    sb.innerHTML=e.name==='AbortError'?'&#10007; Timed out after 3 minutes.':'&#10007; Request failed: '+e.message;
  }
  document.getElementById('genbtn').disabled=false;
  document.getElementById('fetchbtn').disabled=false;
});

function resetStatus(){const sb=document.getElementById('statbox');sb.className='';sb.innerHTML='';}
function chk(){/* generate btn managed by fetch flow */}
</script>
</body>
</html>
"""


CACHE_DIR = "/tmp/parkrun-cache"
os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(slug: str) -> str:
    return os.path.join(CACHE_DIR, slug)


def write_cache(slug: str, history_html: str, latest_html: str,
                event_name: str, lat: float, lon: float):
    d = cache_path(slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "eventhistory.html"), "w", encoding="utf-8") as f:
        f.write(history_html)
    with open(os.path.join(d, "latestresults.html"), "w", encoding="utf-8") as f:
        f.write(latest_html)
    import json
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump({"event_name": event_name, "lat": lat, "lon": lon}, f)


def read_cache(slug: str):
    import json
    d = cache_path(slug)
    meta_path = os.path.join(d, "meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        meta = json.load(f)
    return {
        "history_path": os.path.join(d, "eventhistory.html"),
        "latest_path":  os.path.join(d, "latestresults.html"),
        "event_name":   meta["event_name"],
        "lat":          meta["lat"],
        "lon":          meta["lon"],
    }


@app.route("/test-fetch")
def test_fetch():
    """Debug route: fetch Shrewsbury event history and return status + first 500 chars."""
    url = "https://www.parkrun.org.uk/shrewsbury/results/eventhistory/"
    try:
        html = fetch_parkrun_page(url)
        return jsonify({"ok": True, "status": "fetched", "length": len(html), "preview": html[:500]})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "traceback": traceback.format_exc()})


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/fetch-event", methods=["POST"])
def fetch_event():
    """Fetch and cache both parkrun pages server-side."""
    data        = request.get_json()
    slug        = data.get("slug", "").strip()
    country_url = data.get("country_url", "").strip()
    event_name  = data.get("event_name", slug).strip()
    lat         = float(data.get("lat") or 52.7076)
    lon         = float(data.get("lon") or -2.7521)

    if not slug or not country_url:
        return jsonify({"ok": False, "error": "Missing slug or country_url"})

    history_url = parkrun_url(country_url, slug, "eventhistory")
    latest_url  = parkrun_url(country_url, slug, "latestresults")

    # Fetch each page separately so we can report which one failed
    try:
        history_html = fetch_parkrun_page(history_url)
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": f"Failed to fetch event history: {e}",
                        "detail": traceback.format_exc(), "url": history_url})

    try:
        latest_html = fetch_parkrun_page(latest_url)
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": f"Failed to fetch latest results: {e}",
                        "detail": traceback.format_exc(), "url": latest_url})

    write_cache(slug, history_html, latest_html, sanitise_event_name(event_name), lat, lon)
    cached = read_cache(slug)

    try:
        from parkrun_summary import parse_event_history, parse_latest_results
        event_count  = len(parse_event_history(cached["history_path"]))
        runner_count = len(parse_latest_results(cached["latest_path"]))
        return jsonify({"ok": True, "event_count": event_count, "runner_count": runner_count})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": f"Fetch succeeded but parsing failed: {e}",
                        "detail": traceback.format_exc()})


@app.route("/generate", methods=["POST"])
def generate():
    slug = (request.get_json(silent=True) or {}).get("slug", "")
    slug = slug.strip()

    cached = read_cache(slug)
    if not cached:
        return jsonify({"ok": False, "error": "No fetched data found. Please fetch first."})

    try:
        ds  = datetime.date.today().strftime("%Y-%m-%d")
        hfn = f"summary-{ds}.html"

        html = build_weekly_summary_html(
            cached["history_path"],
            cached["latest_path"],
            event_name=cached["event_name"],
            lat=cached["lat"],
            lon=cached["lon"],
        )

        final_path = os.path.join(SUMMARIES_DIR, hfn)
        tmp_path   = final_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)

        return jsonify({
            "ok":       True,
            "filename": hfn,
            "size":     os.path.getsize(final_path),
            "date":     datetime.date.today().strftime("%A, %d %B %Y"),
        })

    except Exception as e:
        import traceback; tb = traceback.format_exc()
        print(f"[ERROR]\n{tb}", flush=True)
        return jsonify({"ok": False, "error": str(e), "traceback": tb})


@app.route("/filesize/<filename>")
def filesize(filename):
    filename = os.path.basename(filename)
    path = os.path.join(SUMMARIES_DIR, filename)
    try:
        return jsonify({"size": os.path.getsize(path)})
    except OSError:
        return jsonify({"size": -1})


@app.route("/download/<filename>")
def download(filename):
    filename = os.path.basename(filename)
    path = os.path.join(SUMMARIES_DIR, filename)
    if not os.path.exists(path): return "Not found", 404
    mt = "application/pdf" if filename.endswith(".pdf") else "text/html"
    return send_file(path, as_attachment=True, download_name=filename, mimetype=mt)


@app.route("/view-latest/<slug>")
def view_latest(slug):
    slug = os.path.basename(slug)
    path = os.path.join(CACHE_DIR, f"summary-{slug}.html")
    if not os.path.exists(path):
        return "Summary not found. Please generate again.", 404
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    from flask import Response
    return Response(content, mimetype="text/html")


@app.route("/view/<filename>")
def view(filename):
    filename = os.path.basename(filename)
    path = os.path.join(SUMMARIES_DIR, filename)
    if not os.path.exists(path): return "Not found", 404
    return send_file(path, mimetype="text/html")


@app.route("/recent")
def recent():
    try:
        files = sorted([f for f in os.listdir(SUMMARIES_DIR) if f.endswith(".pdf")], reverse=True)[:10]
        result = []
        for f in files:
            try:
                d = datetime.datetime.strptime(f.replace("summary-","").replace(".pdf",""), "%Y-%m-%d")
                result.append({"filename": f.replace(".pdf",".html"),
                                "label": d.strftime("Event - %d %B %Y"),
                                "date": d.strftime("%d/%m/%Y")})
            except ValueError:
                result.append({"filename": f, "label": f, "date": ""})
        return jsonify({"files": result})
    except Exception as e:
        return jsonify({"files": [], "error": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8767, debug=False, threaded=True)
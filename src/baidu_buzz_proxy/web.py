from __future__ import annotations

import html

_STYLE = """
:root{color-scheme:dark;--bg:#11130f;--panel:#1a1d17;--line:#30352a;--text:#f1f3eb;
--muted:#a5ad99;--accent:#b9ef59;--danger:#ff726f}*{box-sizing:border-box}body{margin:0;
font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text)}main{width:min(820px,
calc(100% - 32px));margin:8vh auto}h1{font-size:clamp(2rem,7vw,4.5rem);line-height:1;
letter-spacing:-.05em;margin:0 0 18px}.lead,.muted{color:var(--muted)}.panel{background:var(--panel);
border:1px solid var(--line);border-radius:14px;padding:22px;margin:24px 0}label{display:block;
font-weight:650;margin:14px 0 6px}input[type=text],input[type=url]{width:100%;padding:13px 14px;
border:1px solid var(--line);border-radius:9px;background:#10120e;color:var(--text);font:inherit}
button,.button{display:inline-block;border:0;border-radius:9px;padding:12px 18px;background:var(--accent);
color:#172006;font:700 15px system-ui;cursor:pointer;text-decoration:none}button.secondary{background:#2b3026;
color:var(--text)}button.danger{background:var(--danger);color:#250504}button:disabled{opacity:.5;
cursor:not-allowed}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.between{justify-content:
space-between}.status{font-size:1.2rem;font-weight:700}.error{color:#ff9a97;white-space:pre-wrap}
.items{max-height:440px;overflow:auto;border:1px solid var(--line);border-radius:9px;margin:14px 0}
.item{display:flex;gap:10px;padding:9px 12px;border-bottom:1px solid var(--line);align-items:center}
.item:last-child{border-bottom:0}.item-name{overflow-wrap:anywhere}.size{margin-left:auto;color:var(--muted);
white-space:nowrap}.progress-track{width:100%;height:10px;overflow:hidden;border-radius:999px;
background:#0d0f0c;border:1px solid var(--line)}.progress-fill{display:block;width:0;height:100%;
background:var(--accent);transition:width .35s ease}.progress-track.indeterminate .progress-fill{width:34%;
animation:progress-slide 1.25s ease-in-out infinite}@keyframes progress-slide{from{transform:translateX(-110%)}
to{transform:translateX(315%)}}@media(prefers-reduced-motion:reduce){.progress-track.indeterminate .progress-fill{
animation-duration:2.5s}.progress-fill{transition:none}}code{overflow-wrap:anywhere}
footer{color:var(--muted);margin:40px 0}a{color:var(--accent)}
"""


def index_html(turnstile_site_key: str) -> str:
    turnstile = ""
    script = ""
    if turnstile_site_key:
        key = html.escape(turnstile_site_key, quote=True)
        turnstile = f'<div class="cf-turnstile" data-sitekey="{key}"></div>'
        script = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Baidu Buzz Proxy</title><style>{_STYLE}</style>{script}</head><body><main>
<h1>Baidu to Buzz.</h1><p class="lead">Transfer a public Baidu Netdisk share to Buzzheavier. No Baidu account is required for visitors.</p>
<form id="create" class="panel"><label for="url">Public Baidu share URL</label>
<input id="url" type="text" inputmode="url" autocomplete="url" required placeholder="pan.baidu.com/s/... (https:// optional)"><label for="code">Extraction code</label>
<input id="code" type="text" maxlength="16" placeholder="Optional when included in the URL">{turnstile}
<p><button id="submit" type="submit">Import share</button></p><p id="error" class="error"></p></form>
<p class="muted">The service first imports the public share into an isolated temporary folder, then lets you choose files. Source data is streamed to Buzzheavier and removed from temporary Baidu storage afterwards.</p>
<footer><a href="/docs">API documentation</a> · Open source under MIT</footer></main>
<script>
const form=document.getElementById('create'),error=document.getElementById('error'),submit=document.getElementById('submit');
form.addEventListener('submit',async event=>{{event.preventDefault();error.textContent='';submit.disabled=true;
try{{const widget=document.querySelector('[name="cf-turnstile-response"]');const response=await fetch('/api/jobs',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{share_url:document.getElementById('url').value,extraction_code:document.getElementById('code').value,turnstile_token:widget?widget.value:''}})}});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not create job');location.href=data.job_url;}}catch(e){{error.textContent=e.message;submit.disabled=false;}}}});
</script></body></html>"""


def job_html(public_id: str) -> str:
    job_id = html.escape(public_id, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transfer job · Baidu Buzz Proxy</title><style>{_STYLE}</style></head><body><main data-job="{job_id}">
<p><a href="/">← New transfer</a></p><h1>Transfer job</h1><div class="panel">
<div class="row between"><span id="state" class="status">Loading…</span><button id="cancel" class="danger">Cancel job</button></div>
<p id="message" class="muted"></p><div id="progress" class="progress-track indeterminate" role="progressbar" aria-label="Job progress"><span id="progress-fill" class="progress-fill"></span></div><p id="numbers" class="muted"></p>
<p id="hint" class="muted" aria-live="polite"></p>
<p id="error" class="error"></p><div id="selection" hidden><div class="row between"><strong>Choose content</strong>
<label><input id="all" type="checkbox"> Select all</label></div><div id="items" class="items"></div>
<label for="name">Output name</label><input id="name" type="text" maxlength="200" placeholder="Optional">
<p><button id="start">Transfer to Buzzheavier</button></p></div><div id="result" hidden>
<p>Your transfer is ready.</p><a id="download" class="button" rel="noopener noreferrer">Open Buzzheavier</a></div></div>
<p class="muted">Save this URL to return later. Job metadata is retained for eight days.</p></main>
<script>
const id=document.querySelector('main').dataset.job,key=new URLSearchParams(location.search).get('key')||'';
const state=document.getElementById('state'),message=document.getElementById('message'),error=document.getElementById('error');
const progress=document.getElementById('progress'),progressFill=document.getElementById('progress-fill'),numbers=document.getElementById('numbers'),hint=document.getElementById('hint'),selection=document.getElementById('selection');
const items=document.getElementById('items'),result=document.getElementById('result'),cancel=document.getElementById('cancel');let rendered=false,currentStatus='',statusStarted=Date.now();
function bytes(n){{if(!n)return '0 B';const u=['B','KiB','MiB','GiB','TiB'];const i=Math.min(Math.floor(Math.log(n)/Math.log(1024)),4);return (n/1024**i).toFixed(i?2:0)+' '+u[i];}}
function duration(ms){{const seconds=Math.max(0,Math.floor(ms/1000));if(seconds<60)return seconds+'s';const minutes=Math.floor(seconds/60);return minutes+'m '+seconds%60+'s';}}
function updateProgress(data){{const active=!['completed','failed','cancelled','awaiting_selection'].includes(data.state);const measured=data.total_bytes>0&&data.transferred_bytes>0;const indeterminate=active&&!measured;progress.classList.toggle('indeterminate',indeterminate);const percent=data.total_bytes?Math.min(100,data.transferred_bytes/data.total_bytes*100):(data.state==='completed'?100:0);progressFill.style.width=indeterminate?'':percent+'%';progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax',String(data.total_bytes||100));if(indeterminate)progress.removeAttribute('aria-valuenow');else progress.setAttribute('aria-valuenow',String(data.total_bytes?data.transferred_bytes:percent));numbers.textContent=data.total_bytes?bytes(data.transferred_bytes)+' / '+bytes(data.total_bytes):'';}}
function updateHint(data){{if(data.status!==currentStatus){{currentStatus=data.status;statusStarted=Date.now();}}const descriptions={{queued_import:'Waiting for an import slot. This page updates automatically.',importing:'Baidu operations can take one or two minutes to respond.',queued_transfer:'Waiting for a transfer slot. This page updates automatically.',transferring:data.status.startsWith('Resolving')?'Baidu can take one or two minutes to return each download link.':'The source is being streamed directly to Buzzheavier.',cleaning:'The upload is ready; the temporary Baidu copy is being removed.'}};const active=!['completed','failed','cancelled','awaiting_selection'].includes(data.state);hint.textContent=(descriptions[data.state]||'')+(active?' Current step: '+duration(Date.now()-statusStarted)+'.':'');}}
function renderItems(data){{if(rendered)return;rendered=true;items.textContent='';for(const item of data.items){{const label=document.createElement('label');label.className='item';label.style.paddingLeft=(12+(item.path.split('/').length-1)*18)+'px';const box=document.createElement('input');box.type='checkbox';box.value=item.id;box.dataset.dir=item.is_dir?'1':'0';const title=document.createElement('span');title.className='item-name';title.textContent=(item.is_dir?'📁 ':'')+item.name;const size=document.createElement('span');size.className='size';size.textContent=item.is_dir?'':bytes(item.size_bytes);label.append(box,title,size);items.append(label);}}}}
async function refresh(){{try{{const response=await fetch('/api/jobs/'+id);const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not load job');state.textContent=data.state.replaceAll('_',' ');message.textContent=data.status;error.textContent=data.error||'';updateProgress(data);updateHint(data);selection.hidden=data.state!=='awaiting_selection';if(data.state==='awaiting_selection')renderItems(data);result.hidden=data.state!=='completed';if(data.result_url)document.getElementById('download').href=data.result_url;cancel.disabled=['completed','failed','cancelled'].includes(data.state);if(!['completed','failed','cancelled','awaiting_selection'].includes(data.state))setTimeout(refresh,2000);}}catch(e){{error.textContent=e.message;setTimeout(refresh,5000);}}}}
document.getElementById('start').onclick=async()=>{{error.textContent='';const chosen=[...items.querySelectorAll('input:checked')].map(x=>Number(x.value));const response=await fetch('/api/jobs/'+id+'/selection',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{creator_key:key,item_ids:chosen,select_all:document.getElementById('all').checked,output_name:document.getElementById('name').value}})}});const data=await response.json();if(!response.ok){{error.textContent=data.detail||'Could not start transfer';return;}}selection.hidden=true;refresh();}};
cancel.onclick=async()=>{{if(!confirm('Cancel this job?'))return;const response=await fetch('/api/jobs/'+id+'/cancel',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{creator_key:key}})}});const data=await response.json();if(!response.ok)error.textContent=data.detail||'Could not cancel job';else refresh();}};refresh();
</script></body></html>"""

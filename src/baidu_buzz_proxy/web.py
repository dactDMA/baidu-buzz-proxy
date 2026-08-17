from __future__ import annotations

import html

_STYLE = """
:root{color-scheme:dark;--bg:#11130f;--panel:#1a1d17;--line:#30352a;--text:#f1f3eb;
--muted:#a5ad99;--accent:#b9ef59;--danger:#ff726f}*{box-sizing:border-box}body{margin:0;
font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text)}main{width:min(820px,
calc(100% - 32px));margin:8vh auto}h1{font-size:clamp(2rem,7vw,4.5rem);line-height:1;
letter-spacing:-.05em;margin:0 0 18px}.lead,.muted{color:var(--muted)}.panel{background:var(--panel);
border:1px solid var(--line);border-radius:14px;padding:22px;margin:24px 0}label{display:block;
font-weight:650;margin:14px 0 6px}input[type=text],input[type=url],input[type=password],select{width:100%;padding:13px 14px;
border:1px solid var(--line);border-radius:9px;background:#10120e;color:var(--text);font:inherit}
button,.button{display:inline-block;border:0;border-radius:9px;padding:12px 18px;background:var(--accent);
color:#172006;font:700 15px system-ui;cursor:pointer;text-decoration:none}button.secondary,.button.secondary{background:#2b3026;
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
.admin-toolbar{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}.metric{padding:15px;
border:1px solid var(--line);border-radius:10px;background:#141711}.metric strong{display:block;font-size:1.6rem}
.job-card{border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0;background:var(--panel)}
.job-card h2{font-size:1.05rem;margin:0;overflow-wrap:anywhere}.badge{display:inline-block;padding:3px 8px;
border-radius:999px;background:#31372a;color:var(--text);font-size:.8rem;font-weight:700}.badge.completed{color:#172006;
background:var(--accent)}.badge.failed,.badge.cancelled{background:#522523;color:#ffd1cf}.job-meta{display:flex;
gap:12px;flex-wrap:wrap;color:var(--muted);font-size:.9rem}.mini-progress{height:7px;margin:12px 0;background:#0d0f0c;
border-radius:999px;overflow:hidden}.mini-progress span{display:block;height:100%;background:var(--accent)}
.donation{background:#151f34;border-color:#30476f}.donation h2{margin:0 0 6px}.wallets{margin-top:16px;
border:1px solid #30476f;border-radius:10px;overflow:hidden}.wallet{display:flex;align-items:center;justify-content:
space-between;gap:14px;padding:12px;border-bottom:1px solid #30476f}.wallet:last-child{border-bottom:0}.wallet strong{
display:block;font-size:.85rem;color:#b9c9e8}.wallet code{display:block;color:#eef4ff;overflow-wrap:anywhere;
word-break:break-all}.copy-wallet{flex:0 0 auto;padding:8px 11px;background:#9ebfff;color:#10182a}
@media(max-width:650px){.admin-toolbar{grid-template-columns:repeat(2,1fr)}.wallet{align-items:flex-start;
flex-direction:column}.copy-wallet{align-self:flex-end}}
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
<footer><a href="/docs">API documentation</a> · <a href="https://github.com/dactDMA/baidu-buzz-proxy" target="_blank" rel="noopener noreferrer">Open source under MIT</a></footer></main>
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
<section id="donation" class="panel donation" hidden><h2>Keep this free service online</h2>
<p class="muted">About $20 per month keeps the server online and available. If this transfer helps you, a crypto donation is appreciated.</p>
<div class="wallets">
<div class="wallet"><div><strong>USDT · ERC-20</strong><code>0xAAD4489D08215846D273c9644575c353a1dF0138</code></div><button class="copy-wallet" data-address="0xAAD4489D08215846D273c9644575c353a1dF0138" aria-label="Copy USDT ERC-20 address">Copy</button></div>
<div class="wallet"><div><strong>Litecoin · LTC</strong><code>ltc1q48r4f5kvsm2sfus3jtl8ar59khz6lg5cgz0pqq</code></div><button class="copy-wallet" data-address="ltc1q48r4f5kvsm2sfus3jtl8ar59khz6lg5cgz0pqq" aria-label="Copy Litecoin address">Copy</button></div>
<div class="wallet"><div><strong>USDT · TRC-20</strong><code>TEqBcdPt66wHNnWaz6i4ZAYby8EmLgBoMv</code></div><button class="copy-wallet" data-address="TEqBcdPt66wHNnWaz6i4ZAYby8EmLgBoMv" aria-label="Copy USDT TRC-20 address">Copy</button></div>
<div class="wallet"><div><strong>Bitcoin · BTC</strong><code>bc1qm4mw98du94ldnxezm0cs7h4rk2g6ev3wsnjjeh</code></div><button class="copy-wallet" data-address="bc1qm4mw98du94ldnxezm0cs7h4rk2g6ev3wsnjjeh" aria-label="Copy Bitcoin address">Copy</button></div>
<div class="wallet"><div><strong>USDT · BEP-20</strong><code>0xAAD4489D08215846D273c9644575c353a1dF0138</code></div><button class="copy-wallet" data-address="0xAAD4489D08215846D273c9644575c353a1dF0138" aria-label="Copy USDT BEP-20 address">Copy</button></div>
</div><p id="copy-status" class="muted" aria-live="polite"></p></section>
<p class="muted">Save this URL to return later. Job metadata is retained for eight days.</p></main>
<script>
const id=document.querySelector('main').dataset.job,key=new URLSearchParams(location.search).get('key')||'';
const state=document.getElementById('state'),message=document.getElementById('message'),error=document.getElementById('error');
const progress=document.getElementById('progress'),progressFill=document.getElementById('progress-fill'),numbers=document.getElementById('numbers'),hint=document.getElementById('hint'),selection=document.getElementById('selection');
const items=document.getElementById('items'),result=document.getElementById('result'),cancel=document.getElementById('cancel'),donation=document.getElementById('donation'),copyStatus=document.getElementById('copy-status');let rendered=false,currentStatus='',statusStarted=Date.now(),lastBytes=0,lastBytesAt=performance.now(),smoothedSpeed=0,measuringUpload=false;
function bytes(n){{if(!n)return '0 B';const u=['B','KiB','MiB','GiB','TiB'];const i=Math.min(Math.floor(Math.log(n)/Math.log(1024)),4);return (n/1024**i).toFixed(i?2:0)+' '+u[i];}}
function rate(n){{return n>=1024**2?(n/1024**2).toFixed(1)+' MiB/s':(n/1024).toFixed(0)+' KiB/s';}}
function duration(ms){{const seconds=Math.max(0,Math.floor(ms/1000));if(seconds<60)return seconds+'s';const minutes=Math.floor(seconds/60);return minutes+'m '+seconds%60+'s';}}
function updateProgress(data){{const active=!['completed','failed','cancelled','awaiting_selection'].includes(data.state);const uploading=data.state==='transferring'&&(data.status==='Uploading to Buzzheavier'||data.status.startsWith('Streaming file '));const now=performance.now();if(uploading&&!measuringUpload){{measuringUpload=true;lastBytes=data.transferred_bytes;lastBytesAt=now;smoothedSpeed=0;}}else if(uploading&&data.transferred_bytes>lastBytes){{const currentSpeed=(data.transferred_bytes-lastBytes)/((now-lastBytesAt)/1000);smoothedSpeed=smoothedSpeed?smoothedSpeed*.65+currentSpeed*.35:currentSpeed;lastBytes=data.transferred_bytes;lastBytesAt=now;}}else if(!uploading){{measuringUpload=false;lastBytes=data.transferred_bytes;lastBytesAt=now;smoothedSpeed=0;}}const measured=data.total_bytes>0&&data.transferred_bytes>0;const indeterminate=active&&!measured;progress.classList.toggle('indeterminate',indeterminate);const fraction=data.total_bytes?Math.min(1,data.transferred_bytes/data.total_bytes):(data.state==='completed'?1:0);progressFill.style.width=indeterminate?'':fraction*100+'%';progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax',String(data.total_bytes||1));if(indeterminate)progress.removeAttribute('aria-valuenow');else progress.setAttribute('aria-valuenow',String(data.total_bytes?data.transferred_bytes:fraction));numbers.textContent=data.total_bytes?bytes(data.transferred_bytes)+' / '+bytes(data.total_bytes)+(uploading?' · '+(smoothedSpeed?rate(smoothedSpeed):'measuring speed…'):''):'';}}
function updateHint(data){{if(data.status!==currentStatus){{currentStatus=data.status;statusStarted=Date.now();}}let transferHint='The named file is currently being streamed to Buzzheavier.';if(data.status.startsWith('Resolving'))transferHint='Download links are resolved in parallel using metadata collected during the directory scan.';const descriptions={{queued_import:'Waiting for an import slot. This page updates automatically.',importing:'The current directory, item count, or retry attempt is shown above.',queued_transfer:'Waiting for a transfer slot. This page updates automatically.',transferring:transferHint,cleaning:'The upload is ready; the temporary Baidu copy is being removed.'}};const active=!['completed','failed','cancelled','awaiting_selection'].includes(data.state);hint.textContent=(descriptions[data.state]||'')+(active?' Current step: '+duration(Date.now()-statusStarted)+'.':'');}}
function renderItems(data){{if(rendered)return;rendered=true;items.textContent='';for(const item of data.items){{const label=document.createElement('label');label.className='item';label.style.paddingLeft=(12+(item.path.split('/').length-1)*18)+'px';const box=document.createElement('input');box.type='checkbox';box.value=item.id;box.dataset.dir=item.is_dir?'1':'0';const title=document.createElement('span');title.className='item-name';title.textContent=(item.is_dir?'📁 ':'')+item.name;const size=document.createElement('span');size.className='size';size.textContent=item.is_dir?'':bytes(item.size_bytes);label.append(box,title,size);items.append(label);}}}}
async function refresh(){{try{{const response=await fetch('/api/jobs/'+id);const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not load job');state.textContent=data.state.replaceAll('_',' ');message.textContent=data.status;error.textContent=data.error||'';updateProgress(data);updateHint(data);selection.hidden=data.state!=='awaiting_selection';if(data.state==='awaiting_selection')renderItems(data);result.hidden=data.state!=='completed';donation.hidden=['completed','failed','cancelled','awaiting_selection'].includes(data.state);if(data.result_url)document.getElementById('download').href=data.result_url;cancel.disabled=['completed','failed','cancelled'].includes(data.state);if(!['completed','failed','cancelled','awaiting_selection'].includes(data.state))setTimeout(refresh,1000);}}catch(e){{error.textContent=e.message;setTimeout(refresh,3000);}}}}
document.getElementById('start').onclick=async()=>{{error.textContent='';const chosen=[...items.querySelectorAll('input:checked')].map(x=>Number(x.value));const response=await fetch('/api/jobs/'+id+'/selection',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{creator_key:key,item_ids:chosen,select_all:document.getElementById('all').checked,output_name:document.getElementById('name').value}})}});const data=await response.json();if(!response.ok){{error.textContent=data.detail||'Could not start transfer';return;}}selection.hidden=true;refresh();}};
for(const button of document.querySelectorAll('.copy-wallet'))button.onclick=async()=>{{try{{await navigator.clipboard.writeText(button.dataset.address);copyStatus.textContent='Address copied.';}}catch{{copyStatus.textContent='Could not copy automatically. Select the address above.';}}}};
cancel.onclick=async()=>{{if(!confirm('Cancel this job?'))return;const response=await fetch('/api/jobs/'+id+'/cancel',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{creator_key:key}})}});const data=await response.json();if(!response.ok)error.textContent=data.detail||'Could not cancel job';else refresh();}};refresh();
</script></body></html>"""


def admin_html() -> str:
    return (
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin · Baidu Buzz Proxy</title><style>"""
        + _STYLE
        + """</style></head><body><main>
<p><a href="/">← New transfer</a></p><h1>Admin</h1>
<section id="login" class="panel" hidden><h2>Administrator sign in</h2>
<form id="login-form"><label for="token">Access token</label><input id="token" type="password" autocomplete="current-password" required>
<p><button type="submit">Sign in</button></p><p id="login-error" class="error"></p></form></section>
<section id="dashboard" hidden><div class="row between"><div><strong>Recent jobs</strong><p class="muted">Up to 100 newest jobs</p></div>
<div class="row"><button id="refresh" class="secondary">Refresh</button><button id="logout" class="secondary">Sign out</button></div></div>
<div class="admin-toolbar"><div class="metric"><strong id="total">0</strong>Total</div><div class="metric"><strong id="active">0</strong>Active</div>
<div class="metric"><strong id="complete">0</strong>Completed</div><div class="metric"><strong id="failed">0</strong>Failed</div></div>
<label for="filter">Filter</label><select id="filter"><option value="all">All jobs</option><option value="active">Active</option>
<option value="completed">Completed</option><option value="failed">Failed or cancelled</option></select>
<p id="dashboard-error" class="error"></p><div id="jobs"></div></section></main>
<script>
const login=document.getElementById('login'),dashboard=document.getElementById('dashboard'),loginError=document.getElementById('login-error'),dashboardError=document.getElementById('dashboard-error'),jobsRoot=document.getElementById('jobs'),filter=document.getElementById('filter');
const terminal=new Set(['completed','failed','cancelled']);let currentJobs=[],refreshTimer;
function bytes(n){if(!n)return '0 B';const units=['B','KiB','MiB','GiB','TiB'];const index=Math.min(Math.floor(Math.log(n)/Math.log(1024)),4);return (n/1024**index).toFixed(index?2:0)+' '+units[index];}
function date(value){return new Date(value).toLocaleString();}
function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function showLogin(){clearTimeout(refreshTimer);login.hidden=false;dashboard.hidden=true;document.getElementById('token').focus();}
function matches(job){if(filter.value==='active')return !terminal.has(job.state);if(filter.value==='completed')return job.state==='completed';if(filter.value==='failed')return job.state==='failed'||job.state==='cancelled';return true;}
function render(){jobsRoot.textContent='';const visible=currentJobs.filter(matches);if(!visible.length){jobsRoot.append(element('p','muted','No matching jobs.'));return;}for(const job of visible){const card=element('article','job-card');const top=element('div','row between');const title=element('h2','',job.output_name||job.id);const badge=element('span','badge '+job.state,job.state.replaceAll('_',' '));top.append(title,badge);card.append(top);const meta=element('div','job-meta');meta.append(element('span','',job.id),element('span','',date(job.created_at)),element('span','',bytes(job.transferred_bytes)+' / '+bytes(job.total_bytes)));card.append(meta,element('p','muted',job.status||''));if(job.total_bytes){const track=element('div','mini-progress');const fill=element('span');fill.style.width=Math.min(100,job.transferred_bytes/job.total_bytes*100)+'%';track.append(fill);card.append(track);}if(job.error)card.append(element('p','error',job.error));const actions=element('div','row');const open=element('a','button','Open job');open.href='/jobs/'+job.id;actions.append(open);if(job.result_url){const result=element('a','button secondary','Open result');result.href=job.result_url;result.target='_blank';result.rel='noopener noreferrer';actions.append(result);}if(!terminal.has(job.state)){const cancel=element('button','danger',job.cancel_requested?'Cancellation requested':'Cancel');cancel.disabled=job.cancel_requested;cancel.onclick=()=>cancelJob(job.id);actions.append(cancel);}card.append(actions);jobsRoot.append(card);}}
function updateStats(){document.getElementById('total').textContent=currentJobs.length;document.getElementById('active').textContent=currentJobs.filter(job=>!terminal.has(job.state)).length;document.getElementById('complete').textContent=currentJobs.filter(job=>job.state==='completed').length;document.getElementById('failed').textContent=currentJobs.filter(job=>job.state==='failed'||job.state==='cancelled').length;}
async function loadJobs(){clearTimeout(refreshTimer);try{const response=await fetch('/api/admin/jobs');if(response.status===403){showLogin();return;}const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not load jobs');login.hidden=true;dashboard.hidden=false;dashboardError.textContent='';currentJobs=data.jobs;updateStats();render();refreshTimer=setTimeout(loadJobs,currentJobs.some(job=>!terminal.has(job.state))?2000:10000);}catch(error){dashboardError.textContent=error.message;refreshTimer=setTimeout(loadJobs,5000);}}
async function cancelJob(id){if(!confirm('Cancel this job?'))return;dashboardError.textContent='';const response=await fetch('/api/jobs/'+id+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({creator_key:''})});const data=await response.json();if(!response.ok){dashboardError.textContent=data.detail||'Could not cancel job';return;}await loadJobs();}
document.getElementById('login-form').onsubmit=async event=>{event.preventDefault();loginError.textContent='';const response=await fetch('/api/admin/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_token:document.getElementById('token').value})});if(!response.ok){const data=await response.json();loginError.textContent=data.detail||'Sign in failed';return;}document.getElementById('token').value='';await loadJobs();};
document.getElementById('logout').onclick=async()=>{await fetch('/api/admin/session',{method:'DELETE'});showLogin();};document.getElementById('refresh').onclick=loadJobs;filter.onchange=render;loadJobs();
</script></body></html>"""
    )

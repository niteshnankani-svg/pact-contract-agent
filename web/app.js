const $ = id => document.getElementById(id);
let token = '', current = null, review = null, matters = [], activeTab = 'workspace', selectionVersion = 0;
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function message(text='') { $('message').textContent = text; }
async function api(path, options={}) {
  const headers = {Authorization:'Bearer '+token, ...options.headers};
  if (options.body && !(options.body instanceof FormData)) { headers['Content-Type']='application/json'; options.body=JSON.stringify(options.body); }
  const response=await fetch('/api'+path, {...options,headers});
  if(!response.ok) { const data=await response.json(); throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)); }
  return response.json();
}
function guard(fn) { return async (...args) => { try {message(); await fn(...args);} catch(e) {message(e.message);} }; }
async function refresh() {
  matters=await api('/matters'); $('matterCount').textContent=matters.length;
  $('matters').innerHTML=matters.map(m=>`<button class="matter ${current?.id===m.id?'selected':''}" data-id="${escape(m.id)}">${escape(m.title)}<small>${escape(m.owner)} · Revision ${m.revision}</small></button>`).join('');
  $('matters').querySelectorAll('button').forEach(b=>b.onclick=guard(()=>select(b.dataset.id)));
}
async function select(id) {
  const version=++selectionVersion, data=await api('/matters/'+id);
  if(version!==selectionVersion)return;
  current=data; review=current.reviews[0]||null;
  $('empty').hidden=true; $('matterDetail').hidden=false;
  $('matterTitle').textContent=current.title; $('revision').textContent='MATTER / REVISION '+current.revision;
  $('documents').innerHTML=current.documents.map(d=>`<div class="source">▤ &nbsp; ${escape(d.title)}<small>${escape(d.kind)} · ${d.executed?'Executed':'Draft'} · ${d.clauses.length} chunks · ${d.strategy}</small><details><summary>Read source</summary><pre>${escape(d.text)}</pre></details></div>`).join('');
  const parent=$('supersedes').value;
  $('supersedes').innerHTML='<option value="">No superseded source</option>'+current.documents.map(d=>`<option value="${d.id}">${escape(d.title)}</option>`).join('');
  if(current.documents.some(d=>d.id===parent))$('supersedes').value=parent;
  $('status').textContent=review?.status.replaceAll('_',' ')||'Not started';
  $('run').disabled=!!review&&['queued','resume_queued'].includes(review.status);
  $('audit').innerHTML=current.audit.slice(0,18).map(a=>`<div class="event">${escape(a.event.replaceAll('_',' '))}<small>${new Date(a.created*1000).toLocaleTimeString()}</small></div>`).join('');
  const p=review?.proposal;
  $('findings').innerHTML=p?p.findings.map(f=>`<article class="finding"><b>${escape(f.kind.replaceAll('_',' '))}</b><blockquote>“${escape(f.quote)}”</blockquote><small>Source ${escape(f.document_id.slice(0,8))} · characters ${f.start}–${f.end} · ${f.executed?'executed':'draft'}</small></article>`).join(''):'';
  $('actions').innerHTML=p?(p.issues.map(x=>`<div class="warning">${escape(x)}</div>`).join('')+p.actions.map(a=>`<div class="action"><strong>${escape(a.title)}</strong>${a.due_date?'Due '+escape(a.due_date):'Reviewer sets due date'} ${a.notice_days?' · '+a.notice_days+' days before renewal':''}<p>${escape(a.reason||'')}</p><small>Proposed action · requires source confirmation</small></div>`).join('')+(p.policy_assessments||[]).map(a=>`<div class="source"><b>${escape(a.topic)} / ${escape(a.status.replaceAll('_',' '))}</b><p>${escape(a.reason)}</p></div>`).join('')+p.redlines.map(e=>`<div class="warning"><b>Proposed redline</b><p>${escape(e.replacement)}</p>${escape(e.reason)}</div>`).join('')):'';
  $('approval').hidden=review?.status!=='awaiting_approval'; $('exports').hidden=!p;
  $('approve').disabled=!!p?.blocked;
  const required=p?[...new Set(p.actions.flatMap(a=>a.required_finding_ids))]:[];
  const checked=new Set([...$('confirmations').querySelectorAll('input:checked')].map(x=>x.value));
  $('confirmations').innerHTML=required.map(id=>{const f=p.findings.find(f=>f.id===id);return `<label><input type="checkbox" value="${id}" ${checked.has(id)?'checked':''}> Confirm ${escape(f.kind.replaceAll('_',' '))}: ${escape(f.value)}</label>`;}).join('');
  $('progress').textContent=review?`Review ${review.id.slice(0,8)} · decisions bind to source revision ${review.revision}`:'';
  if(activeTab==='obligations')await renderTasks();
  await refresh();
}
$('connect').onclick=guard(async()=>{token=$('token').value;const me=await api('/me');$('identity').textContent=me.tenant+' / '+me.role;$('modelProfile').textContent=me.model+' · '+me.retrieval+' retrieval · '+me.mode+' mode';await refresh();if(matters[0])await select(matters[0].id);});
$('newMatter').onclick=guard(async()=>{const title=prompt('Matter name');if(!title)return;const owner=prompt('Responsible owner');if(!owner)return;const m=await api('/matters',{method:'POST',body:{title,owner}});await select(m.id);});
$('demo').onclick=guard(async()=>{const m=await api('/matters',{method:'POST',body:{title:'Northstar / SaaS renewal',owner:'Procurement operations'}});await api('/matters/'+m.id+'/documents',{method:'POST',body:{title:'Illustrative SaaS agreement',executed:true,text:'1. Renewal\n\nThe renewal date is 2027-06-30. This agreement renews automatically for one year.\n\n2. Notice\n\nEither party may prevent renewal by providing at least 90 days written notice before the renewal date.\n\n3. Liability\n\nAggregate liability is limited to fees paid during the previous 12 months.\n\n4. Assignment\n\nNeither party may assign this agreement without prior written consent.\n\n5. Confidentiality\n\nBoth parties must protect confidential information and may not disclose it to third parties.'}});await select(m.id);message('Illustrative source loaded. Click Run review to use the configured model.');});
$('upload').onsubmit=guard(async e=>{e.preventDefault();await api('/matters/'+current.id+'/upload',{method:'POST',body:new FormData(e.target)});e.target.reset();await select(current.id);});
$('run').onclick=guard(async()=>{await api('/matters/'+current.id+'/reviews',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}});await select(current.id);});
for(const decision of ['approve','reject','revise']) $(decision).onclick=guard(async()=>{await api('/reviews/'+review.id+'/decision',{method:'POST',body:{decision,plan_hash:review.proposal.plan_hash,note:$('note').value,confirmed_finding_ids:[...$('confirmations').querySelectorAll('input:checked')].map(x=>x.value)}});await select(current.id);});
for(const [id,suffix] of [['json','report.json'],['docx','redlines.docx']]) $(id).onclick=guard(async()=>{const r=await fetch('/api/reviews/'+review.id+'/'+suffix,{headers:{Authorization:'Bearer '+token}});if(!r.ok)throw Error('Export failed');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=suffix;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
async function renderTasks(){
 const tasks=await api('/tasks');
 $('taskList').innerHTML=tasks.length?tasks.map(t=>`<div class="action"><strong>${escape(t.title)}</strong>${escape(t.due_date||'Due date not set')} · ${escape(t.owner)} · <b>${escape(t.status)}</b><p>Approved by ${escape(t.approved_by)} · revision ${t.revision}</p>${t.status==='open'?`<button data-task="${t.id}" data-hash="${t.plan_hash}">Mark complete</button>`:''}</div>`).join(''):'<p>No approved tasks yet.</p>';
 $('taskList').querySelectorAll('[data-task]').forEach(b=>b.onclick=guard(async()=>{await api('/tasks/'+b.dataset.task+'/complete',{method:'POST',body:{plan_hash:b.dataset.hash}});await renderTasks();}));
}
for(const tab of ['workspace','obligations','framework']) $(tab+'Tab').onclick=guard(async()=>{activeTab=tab;for(const name of ['workspace','obligations','framework']){$(name).hidden=name!==tab;$(name+'Tab').classList.toggle('active',name===tab);}$('pageTitle').textContent={workspace:'Review workspace',obligations:'Renewal obligations',framework:'Agent & evidence'}[tab];if(tab==='obligations')await renderTasks();});
let polling=false;
setInterval(async()=>{if(!current||!token||polling)return;polling=true;try{await select(current.id);}catch(e){message(e.message);}finally{polling=false;}},4000);
fetch('/api/public-config').then(r=>r.json()).then(config=>{if(config.demo){$('token').value='demo-admin';$('connect').click();}}).catch(()=>{});

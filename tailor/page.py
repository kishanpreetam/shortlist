"""The Tailor page: one HTML string, served by tailor/app.py (python -m tailor, 127.0.0.1:8766).

Two views. "Tailor a job": a job from Shortlist's queue (opened as /?job=<posting url>, or picked here) and a job
you paste, each with a preview of your resume for it. "Resume lines": every line of your facts file, to approve.
The page loads nothing from outside (the server's CSP is default-src 'none' with inline styles and scripts), and
every request is a POST through api() that carries the run's token in X-Token."""

import json


def html(data: dict) -> str:
    """The whole page with `data` embedded as JSON. Every "<" is escaped, so nothing in the data can end the script."""
    return PAGE.replace("/*DATA*/null", json.dumps(data).replace("<", "\\u003c"))


# A normal (not raw) string: each JavaScript backslash is doubled here, so \\/ and \\s+ reach the browser as \/ and \s+.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tailor</title>
<style>
:root{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--sub:#6e6e73;--line:#e3e3e8;--blue:#0071e3;--green:#1f8f4e;--amber:#b25e00;--chip:#f0f0f3}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;overflow-wrap:anywhere}
[hidden]{display:none!important}
header{position:sticky;top:0;z-index:5;background:rgba(245,245,247,.86);backdrop-filter:saturate(180%) blur(18px);border-bottom:1px solid var(--line)}
.wrap{max-width:980px;margin:0 auto;padding:0 16px}
.bar{display:flex;align-items:center;gap:16px;padding:14px 0;flex-wrap:wrap}
h1{font-size:22px;letter-spacing:-.01em;margin:0}
.qlink{margin-left:auto;color:var(--blue);text-decoration:none;font-size:14px}.qlink:hover{text-decoration:underline}
.muted{color:var(--sub)}
h2{font-size:17px;margin:28px 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin:0 0 12px}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.co{font-weight:700}.ti a{color:var(--ink);text-decoration:none;font-weight:600}.ti a:hover{color:var(--blue)}
.chip{display:inline-block;background:var(--chip);border-radius:99px;padding:2px 9px;font-size:12.5px;color:var(--ink)}
.chip.new{background:#e7f6ec;color:var(--green);font-weight:600}.chip.warn{background:#fff2e0;color:var(--amber)}
.facts{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.row{margin:6px 0;font-size:14px}.row b{font-weight:600}
textarea{width:100%;min-height:64px;border:1px solid var(--line);border-radius:10px;padding:8px 10px;font:inherit;font-size:14px;resize:vertical}
input[type=text]{max-width:100%;border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:inherit;font-size:14px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
button{border:0;border-radius:99px;padding:7px 14px;font:inherit;font-size:14px;cursor:pointer;background:var(--chip);color:var(--ink)}
button.primary{background:var(--blue);color:#fff}button.apply{background:var(--ink);color:#fff}
select{max-width:100%;border:1px solid var(--line);border-radius:99px;padding:6px 10px;font:inherit;font-size:14px;background:#fff}
details summary{cursor:pointer;color:var(--sub);margin:8px 0}
.small{font-size:12.5px}.ok{color:var(--green)}.err{color:#c00}.warn-row{color:var(--amber)}
.tabs{display:flex;gap:6px;padding:0 0 10px;flex-wrap:wrap}.tab.on{background:var(--ink);color:#fff}
button.x{padding:1px 8px;font-size:12px;margin-left:4px}
.pv{border-top:1px dashed var(--line);margin-top:12px;padding-top:12px}
.pv mark{background:#fff1b8;color:inherit;border-radius:3px;padding:0 1px}
.pv h3{font-size:14px;margin:12px 0 4px}.pv ul{margin:4px 0 8px 18px;padding:0}.pv li{margin:3px 0}
.quote{background:var(--bg);border-radius:10px;padding:8px 10px;margin:6px 0}
.line.approved{border-color:#bfe5cc}.line.removed{opacity:.5}.line.bullet{margin-left:22px}
.linetext{margin:6px 0}
.chip.learn{background:#e8f0fe;color:#1a56c4}
.wait{background:#fff8ec;border:1px solid #f3dfbf;border-radius:10px;padding:8px 10px;margin:6px 0}
.line.alt{margin-left:44px}
.pick{display:flex;gap:8px 10px;align-items:center;flex-wrap:wrap}.pick select{flex:1 1 220px;min-width:0}
</style></head><body>
<header><div class="wrap bar" id="bar"><h1>Tailor</h1></div>
<div class="wrap tabs" id="tabs"><button class="tab on" data-view="job">Tailor a job</button><button class="tab" data-view="lines" id="tab-lines">Resume lines</button></div></header>
<main class="wrap">
<div id="warm" class="small muted" style="margin:10px 0 0" hidden></div>
<div id="status" class="small muted"></div>
<div class="card" id="nofacts" style="margin-top:20px" hidden>Set resume_facts in profile.yaml to your facts file to tailor resumes.</div>
<div id="view-job" hidden>
<h2 id="from" hidden>From Shortlist</h2>
<section id="job"></section>
<p id="job-note" class="muted" hidden></p>
<div class="card pick" id="pick" hidden><b>From today's Shortlist queue</b><select id="pick-job" aria-label="A job from today's Shortlist queue"></select><button class="primary" id="pick-go">Tailor</button></div>
<h2>Paste a job</h2>
<div class="card"><div class="row"><input type="text" id="p-company" placeholder="Company" aria-label="Company"> <input type="text" id="p-title" placeholder="Job title" aria-label="Job title" size="40"></div>
<textarea id="p-jd" placeholder="Paste the whole job description here" aria-label="Job description" style="min-height:220px"></textarea>
<div class="actions"><button class="primary" id="p-go">Preview my resume</button><span class="small muted">Only your approved lines and versions are used.</span></div></div>
<div id="p-preview"></div>
</div>
<div id="view-lines" hidden><h2>Your resume lines</h2><div id="lines"></div></div>
</main>
<script>
const D=/*DATA*/null;
const $=(id)=>document.getElementById(id);
const STALE='This page is from before Tailor restarted. Reload it (Command+R), then try again.';
const DOWN='No answer from Tailor. Is `python -m tailor` still running?';
/* links open job postings (https) or Shortlist's own page on this computer, nothing else */
const SL=typeof D.shortlist_url==='string'&&/^https?:\\/\\/[^\\/\\s?#@]+\\//.test(D.shortlist_url)?D.shortlist_url:'';
function el(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){if(k==='text')e.textContent=v;else if(k==='href'){if(/^https:\\/\\//.test(v)||(SL&&String(v).startsWith(SL)))e.href=v;}else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v);}for(const k of kids){if(k!=null)e.append(k);}return e;}
function flash(t,c){const s=$('status');s.className='small '+c;s.textContent=t;clearTimeout(flash.t);flash.t=setTimeout(()=>{s.textContent='';},c==='err'?9000:2500);}
function copy(text){  /* the clipboard, then the older copy command, then the text selected for you to copy */
 const old=()=>{const t=el('textarea',{style:'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0'});t.value=text;document.body.append(t);t.select();
   let done=false;try{done=document.execCommand('copy');}catch(e){}t.remove();return done;};
 const show=()=>{const t=el('textarea',{rows:'12',style:'position:fixed;inset:10% 10% auto 10%;width:80%;z-index:9;box-shadow:0 8px 40px rgba(0,0,0,.25)'});t.value=text;
   const close=el('button',{class:'apply',text:'Done',style:'position:fixed;z-index:10;right:10%;top:calc(10% + 260px)',onclick:()=>{t.remove();close.remove();}});
   document.body.append(t,close);t.focus();t.select();flash('Press Command+C to copy','ok');};
 (navigator.clipboard?navigator.clipboard.writeText(text):Promise.reject()).then(()=>flash('Copied','ok'),()=>{if(old())flash('Copied','ok');else show();});}
async function api(path,body){let r;
 try{r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Token':D.token},body:JSON.stringify(body)});}catch(e){throw new Error(DOWN);}
 const j=await r.json().catch(()=>null);
 if(r.status===403)throw new Error(STALE);
 if(!r.ok||!j)throw new Error(j&&j.error?j.error:j?String(r.status):DOWN);
 return j;}
const PV={};
const LENS={'data-engineer':'data engineering','data-analyst':'analytics','data-scientist':'data science','ai-engineer':'AI engineering'};
function newState(extra){return Object.assign({exclude:[],include:[],title_skip:[],summary_id:null,original:[],skill_skip:[]},extra);}
function openPaste(){PV.paste={st:newState({paste:{company:$('p-company').value.trim(),title:$('p-title').value.trim(),jd:$('p-jd').value}}),r:null,box:()=>$('p-preview'),
  label:$('p-company').value.trim()+', '+$('p-title').value.trim()};runPreview('paste');}
async function runPreview(key){const p=PV[key];const box=p&&p.box();if(!box)return;
 const wait=el('p',{class:'muted small',text:p.r?'Updating the page...':'Fitting your points to this job. If Claude hasn\\'t rewritten them for this job yet, this takes up to 40 seconds.'});
 box.replaceChildren(el('div',{class:'pv'},wait));
 try{p.r=await api('/api/resume/preview',p.st);if(p.r.rewrite_failed)p.st.rewrite=false;}catch(e){p.r={error:e.message};}
 drawPreview(key);}
const norm=w=>w.toLowerCase().replace(/^[^a-z0-9$]+|[^a-z0-9%+]+$/g,'');
function marked(text,orig){const have=new Set((orig||'').split(/\\s+/).map(norm));return text.split(/(\\s+)/).map(w=>!norm(w)||have.has(norm(w))?w:el('mark',{title:'Added for this job',text:w}));}
async function factsDo(body,then){try{const f=await api('/api/facts/update',body);setTabCount(f);then(f);}catch(e){flash(e.message,'err');}}
function waitBox(w,redo){return el('div',{class:'wait small'},el('div',{},el('b',{text:'Suggested '+(LENS[w.lens]||'')+' version, waiting for your OK: '})),el('div',{text:w.text}),
 w.adds_text?el('div',{class:'muted',text:'Adds: '+w.adds_text+(w.why?'. Why: '+w.why:'')}):null,
 el('div',{class:'actions'},el('button',{class:'apply',text:'Approve this version',onclick:()=>factsDo({section:'alt',id:w.id,status:'approved'},redo)}),el('button',{text:'Not this one',onclick:()=>factsDo({section:'alt',id:w.id,status:'removed'},redo)})));}
function pinLine(key,f){const st=PV[key]&&PV[key].st;if(st&&f.added){if(!st.include.includes(f.added))st.include.push(f.added);st.exclude=st.exclude.filter(x=>x!==f.added);}
 flash('Added to this resume, and saved for every job it fits','ok');runPreview(key);}
function suggestRows(x,key){  /* drafts for one requirement: one recommended, each labeled by what it adds to your facts */
 if(!x.suggestions.length)return [el('span',{class:'muted',text:'No draft fits your lines for this one. Use "I\\'ve done this" to write it yourself.'})];
 return x.suggestions.map(sg=>{const ta=el('textarea',{rows:'2'});ta.value=sg.text;
  const label=sg.grounded?el('span',{class:'chip new',text:'From your lines'}):el('span',{class:'chip warn',text:'Needs you to confirm: adds '+sg.adds.join('; ')});
  const use=el('button',{class:'apply',text:'Use this line',onclick:()=>{if(/[\\[\\]]/.test(ta.value)){flash('Fill in the [blanks] with your own facts first','err');return;}
    factsDo({section:'new_line',group:sg.place,text:ta.value,requirement:x.requirement},f=>pinLine(key,f));}});
  return el('div',{style:'margin:8px 0'},el('div',{},sg.recommended?el('span',{class:'chip new',text:'Recommended'}):null,sg.strongest?el('span',{class:'chip learn',text:'Strongest'}):null,' ',label,el('span',{class:'muted',text:'  for '+sg.name})),ta,
   sg.why?el('div',{class:'muted',text:(sg.recommended&&sg.strongest?'Why this one, the strongest you can use as is: ':sg.recommended?'Why this one: ':sg.strongest?'The strongest, if it is true for you: ':'Ranked lower: ')+sg.why}):null,
   !sg.grounded?el('div',{class:'muted',text:'Use it only if it is true; saving it is your confirmation.'}):null,el('div',{class:'actions'},use));});}
function suggestButton(q,row,key){const b=el('button',{class:'x',text:'Suggest lines',title:'Two or three drafts built from your own lines, with one recommended'});
 b.addEventListener('click',async()=>{b.disabled=true;const out=el('div',{class:'small',style:'margin:4px 0 8px 58px'},el('span',{class:'muted',text:'Drafting from your lines, about 15 seconds...'}));row.after(out);
  const pr=PV[key].r||{},onPage=(pr.experience||[]).concat(pr.projects||[]).flatMap(g=>g.bullets.map(b=>b.id));
  try{const x=await api('/api/resume/suggest',Object.assign({},PV[key].st,{requirement:q.text,on_page:onPage}));out.replaceChildren(...suggestRows(x,key));}
  catch(e){out.replaceChildren(el('span',{class:'err',text:e.message}));}b.disabled=false;});
 return b;}
function reqBox(r,key){  /* what the job asks for, and which of your lines on this page show it */
 const reqs=r.requirements||[];if(!reqs.length)return null;
 const lines={education:'Education',skills:'Skills',dates:'Your role dates'};for(const g of (r.experience||[]).concat(r.projects||[]))for(const b of g.bullets)lines[b.id]=b.text;
 const full=q=>q.status?q.status==='full':q.by.length>0,part=q=>q.status==='partial';
 const shown=reqs.filter(full).length,reqd=reqs.filter(q=>q.kind==='required'),reqdShown=reqd.filter(full).length,reqdPart=reqd.filter(part).length;
 const box=el('details',{class:'wait',open:'open'},el('summary',{},el('b',{text:'What this job asks for: '+reqdShown+' of '+reqd.length+' required fully on this page'+(reqdPart?' ('+reqdPart+' more partly)':'')+', and '+shown+' of '+reqs.length+' in all'})));
 for(const a of (r.advice||[]))box.append(el('div',{class:'row small'+(a.level==='change'?' warn-row':a.level==='ok'?' ok':' muted')},el('b',{text:a.level==='change'?'Change: ':a.level==='ok'?'Good: ':'Note: '}),a.text));
 box.append(el('p',{class:'small muted',text:"Each line on the page is written toward the requirements it really shows. For one it doesn't show that you have done, write it once with \\"I've done this\\"; it goes on every resume where it fits."}));
 for(const q of reqs){const row=el('div',{class:'row small'});const kind=el('span',{class:'muted',text:' ('+q.kind+')'+(q.by.length&&(q.where||[]).length?', in '+q.where.join(', '):'')});
  const inLines=(q.where||[]).some(w=>w==='experience'||w==='projects');
  const btn=el('button',{class:'x',text:q.by.length?'Show it in a line':"I've done this",title:'Write one line about the work, once; it goes on every resume where it fits'});
  if(q.by.length){const short=part(q)&&q.kind==='required';  /* append() prints a null as the word "null", so drop them first */
   row.append(...[el('span',{class:part(q)?'chip warn':'chip new',text:part(q)?'partly':'shown'}),' ',q.text,kind,q.kind==='required'&&(!inLines||short)?btn:null,q.kind==='required'&&(!inLines||short)?suggestButton(q,row,key):null,
     part(q)?el('div',{class:'muted',style:'margin-left:58px',text:'short: '+(q.gaps||[]).join('; ')}):null,
     el('div',{class:'muted',style:'margin-left:58px',text:'by: '+q.by.map(id=>{const t=lines[id]||'';return t.length>70?t.slice(0,70)+'...':t;}).join(' | ')})].filter(x=>x!=null));}
  else row.append(el('span',{class:'chip warn',text:'not shown'}),' ',q.text,kind,' ',btn,suggestButton(q,row,key));
  btn.addEventListener('click',()=>{const where=el('select',{},...(r.places||[]).map(pl=>el('option',{value:pl.id,text:pl.name})));
     const ta=el('textarea',{rows:'2',placeholder:'One line about what you did, in your own words, as it should read on your resume'});
     const form=el('div',{class:'small',style:'margin:4px 0 8px 58px'},el('div',{},'Where: ',where),ta,
       el('div',{class:'actions'},el('button',{class:'apply',text:'Add this line',onclick:()=>factsDo({section:'new_line',group:where.value,text:ta.value,requirement:q.text},f=>pinLine(key,f))}),
         el('button',{text:'Cancel',onclick:()=>form.remove()})));
     row.after(form);ta.focus();});
  box.append(row);}
 if((r.logistics||[]).length)box.append(el('div',{class:'row small muted'},el('b',{text:'Check these yourself, no resume line shows them: '}),r.logistics.map(x=>x.text).join('; ')));
 return box;}
function kwBox(r){  /* the posting's keywords, and where each one stands on this page */
 const ks=r.keywords||[];if(!ks.length)return null;
 const on=ks.filter(k=>k.status==='on page').length;
 const box=el('details',{class:'wait',open:'open'},el('summary',{},el('b',{text:'Keywords from this posting: '+on+' of '+ks.length+' on this page'})));
 const groups=[['on page','On the page','chip new'],['missing','Yours, not on the page yet','chip warn'],
  ['ask','Answer the skills question for these','chip'],['learning','On your Currently learning line','chip'],
  ['declined','You said you have not used these','chip'],['only you','None of your lines shows these; add a line only if you did the work','chip']];
 for(const [st,label,cls] of groups){const xs=ks.filter(k=>k.status===st);if(!xs.length)continue;
  const row=el('div',{class:'row small'},el('b',{text:label+': '}));
  for(const k of xs)row.append(el('span',{class:cls,text:k.text+(k.match==='close'?' (close form)':'')}),' ');
  box.append(row);}
 return box;}
function skillsAsk(r,p,key){  /* every skill this job wants that your resume doesn't show: asked once, remembered for every job */
 const ask=(r.skills_detail||[]).filter(x=>x.state==='missing');if(!ask.length)return null;
 const places=r.places||[];let left=ask.length;
 const update=el('button',{class:'primary',text:'Update this resume with my answers',hidden:'hidden',onclick:()=>runPreview(key)});
 const box=el('div',{class:'wait'},el('b',{text:"This job asks for "+ask.length+(ask.length===1?" skill":" skills")+" your resume doesn't show. Have you used "+(ask.length===1?"it?":"them?")}),
  el('p',{class:'small muted',text:"Yes puts it on your resume for this job and every later job that asks for it, and the rewrite can name it in the places you tick. Learning puts it on a Currently learning line. Every answer is saved, so each skill is asked once."}));
 for(const x of ask){const row=el('div',{class:'row'});
  const save=async(level,where)=>{try{const f=await api('/api/facts/update',{section:'added_skill',term:x.term,level,where:where||[]});setTabCount(f);
     const at=(where||[]).map(w=>(places.find(q=>q.id===w)||{}).name||w);
     row.replaceChildren(el('b',{text:x.label+': '}),el('span',{class:'small',text:level==='used'?'yes'+(at.length?', at '+at.join(', '):', on your skills line'):level==='learning'?'learning':'no, not asked again'}));
     left--;update.hidden=false;if(left<=0)runPreview(key);}
   catch(e){flash(e.message,'err');}};
  const yes=el('button',{class:'apply',text:'Yes',onclick:()=>{const picks=places.map(q=>{const c=el('input',{type:'checkbox',value:q.id});return [c,el('label',{class:'small',style:'margin-right:10px;display:inline-block;max-width:100%'},c,' '+q.name)];});
     row.replaceChildren(el('b',{text:x.label+': where did you use it? '}),...picks.map(q=>q[1]),
       el('button',{class:'apply',text:'Save',onclick:()=>save('used',picks.filter(q=>q[0].checked).map(q=>q[0].value))}),
       el('span',{class:'small muted',text:' Tick none if it was somewhere else: it goes on your skills line only.'}));}});
  row.append(el('b',{text:x.label+' '}),yes,' ',el('button',{text:'Learning',onclick:()=>save('learning')}),' ',el('button',{text:'No',onclick:()=>save('no')}));
  box.append(row);}
 box.append(el('div',{class:'actions'},update));
 return box;}
function skillChip(x,st,redo){
 if(x.state==='covered')return el('span',{class:'chip new',text:x.label});
 if(x.state==='learning')return el('span',{class:'chip learn',text:x.label+' (learning)'});
 if(x.state==='skipped')return el('span',{class:'chip'},x.label+' (left off) ',el('button',{class:'x',text:'put back',onclick:()=>{st.skill_skip=st.skill_skip.filter(s=>s!==x.item);redo();}}));
 if(x.state==='not_on_page')return el('span',{class:'chip warn',text:x.label+': in your lines, not on this page'});
 if(x.state==='declined')return el('span',{class:'chip'},x.label+' (you said no) ',el('button',{class:'x',title:'Ask me about it again',text:'I have it',onclick:()=>factsDo({section:'added_skill',term:x.term,level:'removed'},redo)}));
 return el('span',{class:'chip'},x.label+' ',el('button',{class:'x',title:'Add it to your skills for every job that asks for it',text:"I've used it",onclick:()=>factsDo({section:'added_skill',term:x.term,level:'used'},redo)}),el('button',{class:'x',title:'Add it to a Currently learning line for jobs that ask for it',text:'learning',onclick:()=>factsDo({section:'added_skill',term:x.term,level:'learning'},redo)}));}
function drawPreview(key){const p=PV[key];const box=p&&p.box();if(!box||!p.r)return;const r=p.r,st=p.st,k=[];
 if(r.error){box.replaceChildren(el('div',{class:'pv'},el('p',{class:'err',text:r.error})));return;}
 const redo=()=>runPreview(key);
 k.push(el('div',{class:'row'},el('b',{text:r.ok?'Ready: one page, every check passed. ':'Not ready yet. '}),r.pending?el('span',{class:'chip warn',text:r.pending+' of your lines still need approval (Resume lines tab)'}):null,' ',r.pending_versions?el('span',{class:'chip warn',text:r.pending_versions+' suggested versions waiting for you'}):null));
 if(r.failures&&r.failures.length)k.push(el('ul',{class:'err'},...r.failures.map(f=>el('li',{text:f}))));
 if(r.rewrite_note)k.push(el('p',{class:'small warn-row',text:r.rewrite_note}));
 const ask=skillsAsk(r,p,key);if(ask)k.push(ask);
 const rq=reqBox(r,key);if(rq)k.push(rq);
 const kb=kwBox(r);if(kb)k.push(kb);
 if(r.rewritten)k.push(el('p',{class:'small muted',text:r.rewritten+' of your points were rewritten for this job, with the added words marked and your wording under each. Read them before you save, and tap "my wording" on any you want back'+(r.repaired?'. '+r.repaired+(r.repaired===1?' rewrite that failed the fact check was':' rewrites that failed the fact check were')+' fixed on a second try':'')+(r.rejected?'. '+r.rejected+(r.rejected===1?' rewrite that changed a fact was':' rewrites that changed a fact were')+' dropped':'')+'.'}));
 const parts=(r.title_line||'').split(' · ');
 k.push(el('div',{class:'row'},el('b',{text:'Title (names the file): '}),el('span',{class:'chip',text:parts[0]||'(none yet)'})));
 if(r.summary){const job=r.summary.version==='job';const sel=el('select',{},el('option',{value:'',text:job?'written for this job':'best match for this job'}),...r.summaries.map(x=>el('option',{value:x.id,text:'your '+(x.variant||x.id)+' summary'})));sel.value=st.summary_id||'';sel.addEventListener('change',()=>{st.summary_id=sel.value||null;redo();});k.push(el('div',{class:'row'},el('b',{text:'Summary: '}),sel),el('p',{class:'quote',text:r.summary.text}),job?el('p',{class:'small muted',text:'Written for this job from these lines of yours, and fact-checked: '+(r.summary.uses||[]).join(', ')}):null);}
 k.push(el('h3',{text:'Skills on this resume'}));
 for(const s of r.skills||[])k.push(el('div',{class:'row small'},el('b',{text:s.label+': '}),...s.items.map(it=>(s.added||[]).includes(it)?el('span',{class:'chip '+(s.id==='learning'?'learn':'new'),title:s.id==='learning'?'You added it as learning':'Named by this job; your lines back it, or you added it'},it,el('button',{class:'x',title:'Leave it off this resume',onclick:()=>{st.skill_skip.push(it);redo();},text:'✕'})):el('span',{class:'chip',text:it}))));
 for(const [name,groups] of [['Experience',r.experience],['Projects',r.projects]]){if(!groups||!groups.length)continue;k.push(el('h3',{text:name}));
  for(const g of groups){k.push(el('div',{class:'small muted',text:g.heading+(g.dates?' · '+g.dates:'')}));
   k.push(el('ul',{},...g.bullets.map(b=>el('li',{},b.version==='job'?el('span',{},...marked(b.text,(r.originals||{})[b.id])):b.text,b.version==='job'?el('details',{class:'small muted'},el('summary',{text:'rewritten for this job'}),el('div',{text:'Yours: '+((r.originals||{})[b.id]||'')})):b.version!=='original'?el('span',{class:'small muted',text:' ('+(LENS[b.lens]||'job')+' version you approved)'}):null,b.matched.length?el('span',{class:'small muted',text:' (matches: '+b.matched.join(', ')+')'}):null,
    b.version!=='original'?el('button',{class:'x',title:'Use your own wording of this line here',onclick:()=>{st.original.push(b.id);redo();},text:'my wording'}):
     st.original.includes(b.id)?el('button',{class:'x',title:'Go back to the version for this job',onclick:()=>{st.original=st.original.filter(x=>x!==b.id);redo();},text:'job version'}):null,
    detailButton(b.id,redo),
    el('button',{class:'x',title:'Take this line off',onclick:()=>{st.exclude.push(b.id);st.include=st.include.filter(x=>x!==b.id);redo();},text:'✕'}),
    b.waiting?waitBox(b.waiting,redo):null))));}}
 if(r.available&&r.available.length)k.push(el('details',{},el('summary',{text:'Add one of your other approved lines ('+r.available.length+')'}),el('ul',{},...r.available.map(b=>el('li',{},b.version==='job'?el('span',{},...marked(b.text,b.original)):b.text,b.version==='job'?el('details',{class:'small muted'},el('summary',{text:'rewritten for this job'}),el('div',{text:'Yours: '+(b.original||'')})):null,b.matched.length?el('span',{class:'small muted',text:' (matches: '+b.matched.join(', ')+')'}):null,el('button',{class:'x',title:'Put this line on',onclick:()=>{st.include.push(b.id);st.exclude=st.exclude.filter(x=>x!==b.id);redo();},text:'+'}))))));
 k.push(el('div',{class:'row'},el('b',{text:'Their skills: '}),document.createTextNode(r.coverage!=null?r.coverage+'% are on this page'+((r.learning||[]).length?', plus '+r.learning.length+' on your learning line':''):'none detected')));
 k.push(el('div',{class:'facts'},...(r.skills_detail||[]).map(x=>skillChip(x,st,redo))));
 if((r.missing||[]).length)k.push(el('p',{class:'small muted',text:`For a skill the job wants that isn't on your resume: "I've used it" adds it to your skills, and "learning" puts it on a Currently learning line. Either way it stays on your list for every job that asks for it, until you remove it in Resume lines.`}));
 if(r.trimmed&&r.trimmed.length)k.push(el('p',{class:'small muted',text:'Left off to fit one page: '+r.trimmed.join(' | ')}));
 const save=el('button',{class:'apply',text:'Save PDF',onclick:async()=>{save.disabled=true;try{const s2=await api('/api/resume/save',{id:r.id});
   const copied=await (navigator.clipboard?navigator.clipboard.writeText(s2.path):Promise.reject()).then(()=>true,()=>false);  /* Safari copies only during the click itself */
   save.textContent='Saved';flash(copied?'Saved, and the path is copied: '+s2.path:'Saved: '+s2.path,'ok');if(!copied)save.after(el('button',{text:'Copy path',onclick:()=>copy(s2.path)}));if(s2.note)save.parentNode.after(el('p',{class:'small warn-row',text:s2.note}));}catch(e){flash(e.message,'err');save.disabled=false;}}});
 if(!r.ok)save.disabled=true;
 k.push(el('div',{class:'actions'},save,el('button',{text:'Close',onclick:()=>{delete PV[key];box.replaceChildren();}})));
 box.replaceChildren(el('div',{class:'pv'},...k));}
function countPending(f){let n=0;const c=x=>{if(x.status!=='approved'&&x.status!=='removed')n++;};c(f.contact);c(f.titles);f.summaries.forEach(c);f.education.forEach(c);f.skills.forEach(c);for(const g of f.roles.concat(f.projects)){c(g);g.bullets.forEach(c);}return n;}
function setTabCount(f){const n=countPending(f)+(f.pending_versions||0);$('tab-lines').textContent='Resume lines'+(n?' ('+n+' to review)':'');}
function lineRow(section,id,text,status,source,editable){const row=el('div',{class:'card line '+status+(section==='bullet'?' bullet':'')});
 const upd=async(body)=>{try{drawLines(await api('/api/facts/update',Object.assign({section,id},body)));}catch(e){flash(e.message,'err');}};
 const btns=el('div',{class:'actions'});
 if(status!=='approved')btns.append(el('button',{class:'apply',text:'Approve',onclick:()=>upd({status:'approved'})}));
 if(editable)btns.append(el('button',{text:'Edit',onclick:()=>{const ta=el('textarea',{});ta.value=text;row.replaceChildren(ta,el('div',{class:'actions'},el('button',{class:'apply',text:'Save and approve',onclick:()=>upd({text:ta.value})}),el('button',{text:'Cancel',onclick:loadLines})));}}));
 btns.append(status==='removed'?el('button',{text:'Restore',onclick:()=>upd({status:'pending'})}):el('button',{text:'Remove',onclick:()=>upd({status:'removed'})}));
 row.append(el('div',{class:'row'},el('span',{class:'chip '+(status==='approved'?'new':status==='removed'?'':'warn'),text:status}),el('span',{class:'small muted',text:'  '+(source||'')})),el('div',{class:'linetext',text:text}),btns);return row;}
function altRow(a){const upd=async(s)=>{try{drawLines(await api('/api/facts/update',{section:'alt',id:a.id,status:s}));}catch(e){flash(e.message,'err');}};
 const bad=(a.problems||[]).length,btns=el('div',{class:'actions'});
 if(a.status!=='approved'&&!bad)btns.append(el('button',{class:'apply',text:'Approve',onclick:()=>upd('approved')}));
 btns.append(a.status==='removed'?el('button',{text:'Restore',onclick:()=>upd('pending')}):el('button',{text:a.status==='approved'?'Stop using it':'Remove',onclick:()=>upd('removed')}));
 return el('div',{class:'card line alt '+a.status},el('div',{class:'row'},el('span',{class:'chip '+(a.status==='approved'?'new':a.status==='removed'?'':'warn'),text:a.status==='pending'?'suggested':a.status}),el('span',{class:'small muted',text:'  version for '+(LENS[a.lens]||a.lens)+' jobs'})),
  el('div',{class:'linetext',text:a.text}),a.adds_text?el('div',{class:'small'},el('b',{text:'Adds: '}),document.createTextNode(a.adds_text+(a.why?'. Why: '+a.why:''))):null,
  bad?el('div',{class:'small err',text:"Can't be used: "+a.problems.join('; ')}):null,btns);}
/* details: facts about one of your lines, in your words, that rewrites may use for every job */
const DETAIL_TIP='A fact about this line that rewrites may use for every job: what a metric measured and on what data, the baseline, a framework you used, a test you wrote';
function detailEditor(id,done,cancel){const ta=el('textarea',{rows:'2',placeholder:'A fact about this line, in your own words'});
 const box=el('div',{class:'small'},ta,el('div',{class:'actions'},el('button',{class:'apply',text:'Save detail',onclick:()=>factsDo({section:'detail',id,text:ta.value},done)}),el('button',{text:'Cancel',onclick:()=>cancel(box)})));
 setTimeout(()=>ta.focus(),0);return box;}
function detailButton(id,redo){const b=el('button',{class:'x',title:DETAIL_TIP,text:'+ detail'});
 b.addEventListener('click',()=>b.replaceWith(detailEditor(id,redo,box=>box.replaceWith(b))));return b;}
function detailBox(b){const box=el('div',{class:'small',style:'margin:-6px 0 12px 40px'});
 (b.supporting_facts||[]).forEach((x,i)=>{if(x.status!=='approved')return;
  box.append(el('div',{class:'row'},el('b',{text:'Detail: '}),x.text+' ',el('button',{class:'x',text:'remove',onclick:()=>factsDo({section:'detail',id:b.id,index:i,status:'removed'},drawLines)})));});
 const add=el('button',{class:'x',title:DETAIL_TIP,text:'+ add a detail'});
 add.addEventListener('click',()=>add.replaceWith(detailEditor(b.id,drawLines,ed=>ed.replaceWith(add))));
 box.append(add);return box;}
function skillDo(term,level){factsDo({section:'added_skill',term,level},drawLines);}
function drawLines(f){const k=[],n=countPending(f),nv=f.pending_versions||0;setTabCount(f);
 k.push(el('p',{class:'muted',text:n?n+' lines need your review. Only approved lines ever appear on a tailored resume. Read each one, then Approve it, Edit it or Remove it.':'Every one of your lines is reviewed. Tailored resumes use only the approved ones.'}));
 if(nv)k.push(el('div',{class:'card'},el('p',{text:nv+' suggested versions of your bullets are waiting. Each one says the same work, with the same numbers, in the language of one job type, and anything it adds is listed under it. A version is used only for jobs it fits better than your own wording, and only after you approve it.'}),
  el('div',{class:'actions'},el('button',{class:'apply',text:'Approve all '+nv,onclick:async()=>{if(!confirm('Approve all '+nv+' suggested versions? Each will appear on resumes exactly as written, so read them first.'))return;try{drawLines(await api('/api/facts/update',{section:'alts',status:'approved'}));}catch(e){flash(e.message,'err');}}}),el('span',{class:'small muted',text:'or approve them one by one below'}))));
 k.push(el('h3',{text:'Contact'}),lineRow('contact',null,[f.contact.name].concat(f.contact.lines,f.contact.links.map(l=>l.text)).join(' | '),f.contact.status,f.contact.source,false));
 k.push(el('h3',{text:'Titles allowed on your title line'}),lineRow('titles',null,f.titles.items.join(' · '),f.titles.status,f.titles.source,false));
 k.push(el('h3',{text:'Summaries'}));f.summaries.forEach(x=>k.push(x.opener?lineRow('summaries',x.id,x.opener.replace('{title}','[your title for the job]')+' [the impact sentence that fits the job] '+(x.closer||''),x.status,(x.variant||'')+' · '+x.source,false):lineRow('summaries',x.id,x.text,x.status,(x.variant||'')+' · '+x.source,true)));
 if((f.impacts||[]).length){k.push(el('h3',{text:'Impact sentences (each summary uses the one that fits the job)'}));f.impacts.forEach(x=>k.push(lineRow('impact',x.id,x.text,x.status,'from your line '+x.backs+' · '+(x.lens||[]).join(', '),false)));}
 k.push(el('h3',{text:'Education'}));f.education.forEach((x,i)=>k.push(lineRow('education',String(i),x.school+' | '+x.degree+' | '+x.date,x.status,x.source,false)));
 k.push(el('h3',{text:'Skills'}));f.skills.forEach(x=>k.push(lineRow('skills',x.id,x.label+': '+x.items.join(' · '),x.status,x.source,false)));
 k.push(el('p',{class:'small muted',text:'A tailored resume also lists any skill a job names that your approved lines show you used, such as a tool named in one of your bullets.'}));
 const added=(f.added_skills||[]).filter(a=>a.status==='approved'&&a.level!=='no'),saidNo=(f.added_skills||[]).filter(a=>a.status==='approved'&&a.level==='no');
 const placeName=id=>{const g=(f.roles||[]).concat(f.projects||[]).find(g=>g.id===id);return g?(g.org||g.title||id):id;};
 k.push(el('h3',{text:'Skills you added'}));
 if(!added.length)k.push(el('p',{class:'small muted',text:'None yet. Add them from a job preview, or from the list below.'}));
 for(const a of added)k.push(el('div',{class:'row'},el('span',{class:'chip '+(a.level==='learning'?'learn':'new'),text:a.label+(a.level==='learning'?' (learning)':'')+(a.where&&a.where.length?' at '+a.where.map(placeName).join(', '):'')}),' ',
  el('button',{class:'x',text:a.level==='learning'?"I've used it now":'move to learning',onclick:()=>skillDo(a.term,a.level==='learning'?'used':'learning')}),el('button',{class:'x',text:'remove',onclick:()=>skillDo(a.term,'removed')})));
 if(saidNo.length)k.push(el('p',{class:'small muted'},'You said no to: ',...saidNo.map(a=>el('span',{class:'chip'},a.label+' ',el('button',{class:'x',text:'ask again',onclick:()=>skillDo(a.term,'removed')})))));
 const dm=(f.demand||[]).filter(x=>x.n>=2).slice(0,45);
 if(dm.length){k.push(el('h3',{text:"What today's jobs ask for"}),el('p',{class:'small muted',text:"How many of today's jobs name each skill. Green is on your resume already; add the others you can talk about in an interview."}));
  k.push(el('div',{class:'facts'},...dm.map(x=>x.state==='not added'?el('span',{class:'chip'},x.label+' · '+x.n+' ',el('button',{class:'x',text:"I've used it",onclick:()=>skillDo(x.term,'used')}),el('button',{class:'x',text:'learning',onclick:()=>skillDo(x.term,'learning')})):el('span',{class:'chip '+(x.state==='on your learning line'?'learn':'new'),title:x.state,text:x.label+' · '+x.n}))));}
 for(const [grp,name] of [['roles','Experience'],['projects','Projects']]){k.push(el('h3',{text:name}));
  for(const g of f[grp]){k.push(lineRow(grp,g.id,grp==='roles'?g.title+' | '+g.org+(g.place?' · '+g.place:'')+' | '+g.dates:g.title+' | '+g.subtitle,g.status,g.source,false));
   g.bullets.forEach(b=>{k.push(lineRow('bullet',b.id,b.text,b.status,b.source,true));if(b.status==='approved')k.push(detailBox(b));(b.alts||[]).forEach(a=>k.push(altRow(a)));});}}
 $('lines').replaceChildren(...k);}
async function loadLines(){$('lines').replaceChildren(el('p',{class:'muted',text:'Loading your lines...'}));try{drawLines(await api('/api/facts/get',{}));}catch(e){$('lines').replaceChildren(el('p',{class:'err',text:e.message}));}}
/* a job from Shortlist's queue: its card, with the preview of your resume for it under the card */
const JOBS=Array.isArray(D.jobs)?D.jobs:[];
let JOB=null;
function reveal(node){const h=document.querySelector('header').offsetHeight;window.scrollTo({top:Math.max(0,node.getBoundingClientRect().top+window.scrollY-h-12),behavior:'smooth'});}
function fillPaste(j,force){  /* the paste form, for a job whose posting Shortlist didn't save */
 const c=$('p-company'),t=$('p-title');
 if(!force){if(!c.value.trim()&&!t.value.trim()){c.value=j.company||'';t.value=j.title||'';}return;}
 if(c.value.trim()===(j.company||'')&&t.value.trim()===(j.title||''))return;
 c.value=j.company||'';t.value=j.title||'';$('p-jd').value='';delete PV.paste;$('p-preview').replaceChildren();}  /* what was there was for another job */
function jobCard(j){
 const act=el('div',{class:'actions'},el('button',{class:'primary',text:'Preview my resume',onclick:()=>previewJob()})),box=el('div',{id:'pv-job'});
 /* the button shows only when there's no preview to read: before one runs, after Close, or after an error */
 new MutationObserver(()=>{const p=PV.job;act.hidden=box.childNodes.length>0&&!(p&&p.r&&p.r.error);}).observe(box,{childList:true});
 return el('div',{class:'card'},
  el('div',{class:'top'},el('span',{class:'co',text:j.company||''}),el('span',{class:'ti'},el('a',{href:j.url,target:'_blank',rel:'noopener noreferrer',text:j.title||j.url}))),
  el('div',{class:'facts'},j.n>0?el('span',{class:'chip',text:'#'+j.n+" in today's queue"}):null,j.resume?el('span',{class:'chip',text:'resume: '+j.resume}):null),
  ...(j.has_text?[act,box]:[el('p',{class:'row warn-row',text:"Shortlist didn't save this posting's description. Paste it below."})]));}
function previewJob(){if(!JOB||!JOB.has_text)return;const p=PV.job,job=JOB;
 if(p&&p.st.url===job.url&&!(p.r&&p.r.error))return;  /* open already, or on its way */
 PV.job={st:newState({url:job.url}),r:null,box:()=>$('pv-job'),label:job.company+', '+job.title};
 runPreview('job').then(()=>{const q=PV.job;if(q&&q.st.url===job.url&&q.r&&q.r.error)fillPaste(job,false);});}
function openJob(j){  /* its card, and its preview at once when Shortlist saved the posting */
 if(JOB&&JOB.url===j.url&&$('job').firstChild){previewJob();return;}
 delete PV.job;JOB=j;$('job').replaceChildren(jobCard(j));$('from').hidden=false;
 if(j.has_text)previewJob();else fillPaste(j,true);}
$('pick-go').addEventListener('click',()=>{const it=JOBS[Number($('pick-job').value)];if(!it)return;
 const known=D.job&&D.job.url===it.url?D.job:null;  /* jobs from the list don't say whether Shortlist saved the posting: the preview tells */
 $('job-note').hidden=true;
 openJob({url:it.url,company:it.company,title:it.title,n:it.n,resume:it.resume,has_text:known?!!known.has_text:it.has_text!==false});
 try{history.replaceState(null,'','?job='+encodeURIComponent(it.url));}catch(e){}
 reveal($('from'));});
if(D.warm){$('warm').textContent=D.warm;$('warm').hidden=false;}
if(SL&&JOBS.length)$('bar').append(el('a',{class:'qlink',href:SL,target:'shortlist',rel:'noopener',text:"Today's queue"}));
if(!D.facts){$('tabs').hidden=true;$('nofacts').hidden=false;}
else{
 $('view-job').hidden=false;
 for(const b of document.querySelectorAll('.tab'))b.addEventListener('click',()=>{for(const t of document.querySelectorAll('.tab'))t.classList.toggle('on',t===b);for(const v of ['job','lines'])$('view-'+v).hidden=(v!==b.dataset.view);if(b.dataset.view==='lines')loadLines();});
 $('p-go').addEventListener('click',openPaste);
 if(D.job_note){$('job-note').textContent=D.job_note;$('job-note').hidden=false;}
 if(JOBS.length){const sel=$('pick-job');
  sel.replaceChildren(...JOBS.map((it,i)=>el('option',{value:String(i),text:(it.n?'#'+it.n+' ':'')+(it.company||'')+', '+(it.title||'')+(it.spare?' (spare)':'')})));
  const at=D.job?JOBS.findIndex(it=>it.url===D.job.url):-1;if(at>=0)sel.value=String(at);
  $('pick').hidden=false;$('from').hidden=false;}
 if(D.job)openJob(D.job);
 api('/api/facts/get',{}).then(setTabCount).catch(()=>{});
}
</script></body></html>
"""

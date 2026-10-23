'use strict';
/* v45 BSD live overlay. No banners, no prediction changes.
   Worker provides normalized and cached (~40s) scores. Poll every 60s while visible. */
(function () {
  if (typeof state === 'undefined' || typeof render !== 'function' ||
      typeof load !== 'function' || typeof pollLive !== 'function') return;
  const ENDPOINT = String(window.MAC_LIVE_SCORE_URL || '').trim();
  const POLL_EVERY_MS = 60000;
  let busy=false,lastStart=0,lastReceived=0,lastSnapshot=null;

  // Not shown to users: requested removal of the SportScore/refresh status row.
  function removeOldLine(){document.querySelectorAll('[data-v43-live],[data-v39-live],[data-v38-live]').forEach(el=>el.remove());}
  const originalRender=render;
  render=function(...args){const result=originalRender(...args);removeOldLine();return result;};

  function normalizeName(raw){
    return String(raw||'').toLocaleLowerCase('en-US').replace(/ı/g,'i').replace(/ß/g,'ss')
      .normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
      .replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ');
  }
  const alias={
    'man utd':'manchester united','man united':'manchester united',
    'man city':'manchester city', 'psg':'paris saint germain','paris sg':'paris saint germain',
    'atletico de madrid':'atletico madrid','vfb stuttgart':'stuttgart',
    'bayern munich':'bayern munchen','fc bayern munchen':'bayern munchen',
    'borussia m gladbach':'borussia monchengladbach',
    'inter milan':'internazionale'
  };
  function teamKey(s){const k=normalizeName(s);return alias[k]||k;}
  function leagueCode(raw){
    const n=normalizeName(raw);
    if(n.includes('conference league'))return 'uefa.europa.conf';
    if(n.includes('europa league'))return 'uefa.europa';
    if(n.includes('champions league'))return 'uefa.champions';
    if(['trendyol super lig','turkish super lig','super lig'].includes(n))return 'tur.1';
    if(n==='premier league'||n==='english premier league')return 'eng.1';
    if(n==='efl championship'||n==='championship')return 'eng.2';
    if(n==='la liga'||n==='laliga')return 'esp.1';
    if(n==='bundesliga')return 'ger.1';
    if(n==='serie a')return 'ita.1';
    if(n.includes('ligue 1'))return 'fra.1';
    if(n==='eredivisie')return 'ned.1';
    return null;
  }
  function locate(e){
    const fixtures=Array.isArray(state.fixtures)?state.fixtures:[];
    const localId='bsd:'+String(e.sourceId||e.providerId||'');
    let unique=fixtures.filter(m=>m.id===localId);
    if(unique.length===1)return unique[0];
    if(!e.home?.name||!e.away?.name)return null;
    const home=teamKey(e.home.name),away=teamKey(e.away.name);
    const kickoff=Date.parse(e.date);
    if(!home||!away||!Number.isFinite(kickoff))return null;
    const league=leagueCode(e.competition);
    unique=fixtures.filter(m=>m.home&&m.away&&teamKey(m.home.name)===home&&
      teamKey(m.away.name)===away&& (!league||league===m.league)&&
      Number.isFinite(Date.parse(m.date))&&
      Math.abs(Date.parse(m.date)-kickoff)<3*3600000);
    return unique.length===1?unique[0]:null;
  }
  function apply(rows){
    let changed=0,matched=0;
    const changedModels=[];
    for(const e of rows){
      if(!e||(!e.live&&!e.done))continue;
      const m=locate(e);if(!m)continue;
      matched++;
      // Never regress completed score to an earlier in-play snapshot.
      if(m.done&&!e.done)continue;
      const hs=Number(e.home?.score),as=Number(e.away?.score);
      if(e.home?.score==null||e.away?.score==null||!Number.isInteger(hs)||!Number.isInteger(as))continue;
      const newDone=e.done===true;
      const newLive=e.live===true&&!newDone;
      const clock=newLive?String(e.clock||''):'';
      if(m.done===newDone&&m.live===newLive&&m.home.score===hs&&m.away.score===as&&m.clock===clock)continue;
      if(newDone&&!m.done&&typeof recordFinished==='function')recordFinished(m);
      m.done=newDone;m.live=newLive;m.home.score=hs;m.away.score=as;m.clock=clock;
      const old=(state.history||[]).find(h=>h.id===m.id);
      if(old&&old!==m){old.done=m.done;old.live=m.live;old.clock=m.clock;old.home.score=hs;old.away.score=as;}
      changedModels.push(m);changed++;
    }
    if(changed){
      state.fixtures=state.fixtures.filter(m=>!m.done||(typeof retainFinished==='function'&&retainFinished(m)))
        .sort((a,b)=>Number(!!b.live)-Number(!!a.live)||Date.parse(a.date)-Date.parse(b.date));
    }
    return {changed,matched};
  }
  async function download(){
    if(!ENDPOINT.startsWith('https://'))throw Error('BSD Worker adresi ayarlı değil');
    const ctl=new AbortController();const timer=setTimeout(()=>ctl.abort(),14000);
    try{
      const response=await fetch(ENDPOINT+(ENDPOINT.includes('?')?'&':'?')+'t='+Date.now(),
                                 {signal:ctl.signal,cache:'no-store',mode:'cors'});
      if(!response.ok)throw Error('BSD HTTP '+response.status);
      const j=await response.json();
      if(j.source!=='BSD'||Number(j.version)<45||!Array.isArray(j.events))throw Error('Worker v45 /live endpoint gerekli');
      const updated=Date.parse(j.updatedAt);
      if(!Number.isFinite(updated)||Math.abs(Date.now()-updated)>150000)throw Error('BSD Worker verisi eski');
      return j.events;
    }finally{clearTimeout(timer);}
  }
  async function run(force=false){
    if(document.hidden||busy||!Array.isArray(state.fixtures))return;
    const now=Date.now();if(!force&&now-lastStart<55000)return;
    lastStart=now;busy=true;
    try{
      const rows=await download();const result=apply(rows);
      lastSnapshot={events:rows,time:Date.now()};lastReceived=lastSnapshot.time;
      if(result.changed)render();
    }catch(error){console.warn('[BSD] canlı skor sorgusu:',error?.message||error);}
    finally{busy=false;removeOldLine();}
  }
  const originalLoad=load;
  load=async function(...args){
    const result=await originalLoad(...args);
    // data.json may be older than the latest BSD overlay.
    if(lastSnapshot&&Date.now()-lastSnapshot.time<8*60000){const x=apply(lastSnapshot.events);if(x.changed)render();}
    return result;
  };
  pollLive=()=>run(false); // Override the legacy ESPN polling function without editing app.js.
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>run(true),{once:true});
  else run(true);
  setInterval(()=>{if(!document.hidden)run(false);},POLL_EVERY_MS);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)run(true);});
  window.addEventListener('focus',()=>{if(!document.hidden&&Date.now()-lastReceived>45000)run(true);});
  console.info('[BSD] 60 saniyelik canlı skor sorgulama etkin.');
})();

'use strict';
/* Maç Analiz Pro v39 — live score overlay. No prediction changes.
   - Poll ESPN directly every 60 seconds in the foreground.
   - Use separately published GitHub scores as a fallback.
   - Keep last fresh scores when the scheduled data.json reloads.
   - Show a meaningful diagnosis when both sources fail. */
(function () {
  if (typeof state === 'undefined' || typeof render !== 'function' ||
      typeof pollLive !== 'function' || typeof load !== 'function' ||
      typeof BASE !== 'string' || typeof LEAGUES === 'undefined') {
    console.error('[v39] Expected app.js components not available.');
    return;
  }

  const RAW = 'https://raw.githubusercontent.com/yildizatakan-bit/skor/live-scores/live_scores.json';
  const PULL_MS = 60_000;
  const MAX_GITHUB_AGE = 15 * 60_000;
  const CORE = ['tur.1','eng.1','esp.1','ger.1','ita.1','fra.1','uefa.champions','uefa.europa','uefa.europa.conf'];
  let busy = false, lastCheck = null, lastGood = null, lastSource = '', lastIssue = '';
  let lastSnapshot = null, lastDirect = null;

  function shortTime(ms) {
    return new Date(ms).toLocaleTimeString('tr-TR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  }
  function errorMessage(reason) {
    const m = String(reason?.message || reason || 'bilinmeyen hata');
    if (/abort|timeout|zaman aş/i.test(m)) return 'zaman aşımı';
    if (/Failed to fetch|NetworkError|Load failed|fetch failed|CORS/i.test(m)) return 'ağ/CORS engeli';
    return m.slice(0, 90);
  }
  function status() {
    if (state.page !== 'home') return;
    const head = [...document.querySelectorAll('#content .sectionhead h2')]
      .find(n => /Canlı ve Yaklaşan Maçlar/.test(n.textContent));
    if (!head) return;
    let node = head.querySelector('[data-v39-live]');
    if (!node) {
      node = document.createElement('span');
      node.dataset.v39Live = '1';
      node.style.cssText = 'display:block;font:500 11px/1.6 system-ui,sans-serif;margin-top:6px;max-width:340px;white-space:normal';
      head.appendChild(node);
    }
    if (lastGood && lastCheck === lastGood) {
      node.textContent = '✓ Skor kontrolü ' + shortTime(lastGood) + ' · ' + lastSource;
      node.style.color = '#84e9b7';
      node.title = 'Veri kaynağına başarıyla erişildi. Skor ESPN güncelleyince değişir.';
    } else if (lastCheck && lastIssue) {
      node.textContent = '⚠ Skor yenilenemedi ' + shortTime(lastCheck) + ' · ' + lastIssue;
      node.style.color = '#ffbd7f';
      node.title = lastIssue;
    } else if (lastGood) {
      node.textContent = '✓ Son başarılı kontrol ' + shortTime(lastGood) + ' · ' + lastSource;
      node.style.color = '#e9c881';
    } else {
      node.textContent = 'Skor bağlantısı kontrol ediliyor…';
      node.style.color = '#9cbacf';
    }
  }
  const originalRender = render;
  render = function () { originalRender(); status(); };

  function parseESPN(event, league) {
    const game = event?.competitions?.[0];
    const home = game?.competitors?.find(x => x.homeAway === 'home');
    const away = game?.competitors?.find(x => x.homeAway === 'away');
    if (!event?.id || !event?.date || !home || !away) return null;
    const kind = event.status?.type || {};
    const done = kind.completed === true;
    const live = !done && String(kind.state || '').toLowerCase() === 'in';
    const score = x => {
      const value = x?.score;
      if (value === null || value === undefined || String(value).trim() === '') return null;
      const n = Number(value);
      return Number.isInteger(n) && n >= 0 ? n : null;
    };
    const team = x => ({
      id: String(x.team?.id || ''), name: x.team?.displayName || x.team?.name || '?',
      logo: x.team?.logo || x.team?.logos?.[0]?.href || '',
      score: live || done ? score(x) : null
    });
    return {id:league+':'+event.id, league, date:event.date, home:team(home), away:team(away),
      live, done, clock:live?String(event.status?.displayClock || ''):''};
  }

  function leaguesToCheck() {
    const wanted = new Set(CORE);
    const now = Date.now();
    for (const m of (state.fixtures || [])) {
      if (!m || !m.league || !Number.isFinite(Date.parse(m.date))) continue;
      const delta = Date.parse(m.date) - now;
      if (delta > -12 * 3600000 && delta < 36 * 3600000) wanted.add(m.league);
    }
    // All app-supported leagues only. Avoid arbitrary codes supplied by source data.
    const valid = new Set(LEAGUES.map(x => x[0]));
    return [...wanted].filter(x => valid.has(x)).slice(0, 22);
  }
  async function directESPN() {
    const now = Date.now();
    const from = new Date(now - 86400000).toISOString().slice(0,10).replace(/-/g,'');
    const to = new Date(now + 86400000).toISOString().slice(0,10).replace(/-/g,'');
    const leagues = leaguesToCheck();
    const results = await Promise.allSettled(leagues.map(async league => {
      const ctl = new AbortController();
      const alarm = setTimeout(() => ctl.abort(), 11000);
      try {
        // One request per league, not two per league like v38.
        const url = BASE + encodeURIComponent(league) + '/scoreboard?dates=' + from + '-' + to + '&limit=200';
        const res = await fetch(url, {cache:'no-store', mode:'cors', signal:ctl.signal});
        if (!res.ok) throw Error('HTTP ' + res.status);
        const body = await res.json();
        if (!Array.isArray(body.events)) throw Error('geçersiz ESPN yanıtı');
        return body.events.map(e => parseESPN(e,league)).filter(Boolean);
      } finally {clearTimeout(alarm);}
    }));
    const good = results.filter(x=>x.status==='fulfilled');
    if (good.length === 0) {
      const examples = results.filter(x=>x.status==='rejected').slice(0,2).map(x=>errorMessage(x.reason));
      throw Error('ESPN ' + good.length + '/' + leagues.length + ' lig: ' + [...new Set(examples)].join(', '));
    }
    return {rows:good.flatMap(x=>x.value), source:'ESPN '+good.length+'/'+leagues.length+' lig', time:Date.now(), partial:good.length<leagues.length};
  }
  async function githubSnapshot() {
    const ctl = new AbortController();
    const alarm = setTimeout(() => ctl.abort(), 10000);
    try {
      const r = await fetch(RAW+'?v39='+Date.now(), {cache:'no-store',mode:'cors',signal:ctl.signal});
      if (!r.ok) throw Error('GitHub HTTP '+r.status);
      const data = await r.json();
      const stamp = Date.parse(data.updatedAt);
      if (!Array.isArray(data.events) || !Number.isFinite(stamp)) throw Error('Geçersiz GitHub skor dosyası');
      const age = Date.now() - stamp;
      if (age > MAX_GITHUB_AGE || age < -120_000) throw Error('GitHub skoru '+Math.max(0,Math.round(age/60000))+' dk eski');
      return {rows:data.events,source:'GitHub yedek',time:stamp,partial:false};
    } finally {clearTimeout(alarm);}
  }

  function applyRows(rows, fetchedTime) {
    if (!Array.isArray(state.fixtures) || !Array.isArray(state.history) || !Array.isArray(rows)) return false;
    const now = Date.now();
    const current = new Map(state.fixtures.map(m=>[m.id,m]));
    const history = new Map(state.history.map(m=>[m.id,m]));
    const baselineTime = Number(state.updated) || 0;
    let changed = false;
    for (const x of rows) {
      if (!x || !x.id || !x.league || !x.home || !x.away) continue;
      const kick = Date.parse(x.date);
      if (!Number.isFinite(kick) || kick < now-2*86400000 || kick > now+7*86400000) continue;
      if (fetchedTime + 30_000 < baselineTime) continue; // Never let older snapshots replace newer data.json.
      const live = x.live === true, done = x.done === true;
      let m = current.get(x.id);
      if (!m) {
        // Do not create old completed games or fabricate missing analysis.
        if (!live || kick < now-8*3600000) continue;
        m = {id:String(x.id),league:String(x.league),date:x.date,done:false,live:true,
          clock:String(x.clock||''),home:{...x.home},away:{...x.away}};
        state.fixtures.push(m);current.set(m.id,m);
        if (!history.has(m.id)) {state.history.push(m);history.set(m.id,m);}
        changed = true;
      }
      // Never replace a completed or newer backend snapshot with an older result.
      if (m.done && !done) continue;
      const normalize = value => value == null || value === '' ? null :
        (Number.isInteger(Number(value)) && Number(value)>=0 ? Number(value) : null);
      const hs = live||done?normalize(x.home.score):null;
      const as = live||done?normalize(x.away.score):null;
      const clock = live?String(x.clock||''):'';
      // A stale no-score pre-match record must not blank scores already shown.
      if (!live && !done && (m.live || m.done)) continue;
      if (m.live!==live || m.done!==done || m.clock!==clock || m.home.score!==hs || m.away.score!==as) {
        if (done && !m.done && typeof recordFinished==='function') recordFinished(m);
        m.live=live;m.done=done;m.clock=clock;m.home.score=hs;m.away.score=as;
        const h=history.get(m.id);
        if (h && h!==m) {h.live=live;h.done=done;h.clock=clock;h.home.score=hs;h.away.score=as;}
        changed=true;
      }
    }
    if (changed) state.fixtures=state.fixtures.filter(m=>!m.done||retainFinished(m,now))
      .sort((a,b)=>Number(!!b.live)-Number(!!a.live)||Date.parse(a.date)-Date.parse(b.date));
    return changed;
  }

  // Keep the newest successful scoreboard while load() refreshes the old historical snapshot.
  const oldLoad=load;
  load=async function (...args) {
    const r=await oldLoad(...args);
    let changed=false;
    if (lastSnapshot) changed=applyRows(lastSnapshot.rows,lastSnapshot.time)||changed;
    if (lastDirect) changed=applyRows(lastDirect.rows,lastDirect.time)||changed;
    if (changed) render();
    return r;
  };

  pollLive = async function () {
    if (document.hidden || busy || !Array.isArray(state.fixtures) || !Array.isArray(state.history)) return;
    busy=true;lastCheck=Date.now();
    try {
      // Both requests run independently; a blocked ESPN fetch cannot block GitHub fallback.
      const [direct,stored]=await Promise.allSettled([directESPN(),githubSnapshot()]);
      let changed=false;
      if (stored.status==='fulfilled') {
        lastSnapshot=stored.value;
        changed=applyRows(stored.value.rows,stored.value.time)||changed;
      }
      if (direct.status==='fulfilled') {
        lastDirect=direct.value;
        changed=applyRows(direct.value.rows,direct.value.time)||changed;
      }
      if (direct.status==='rejected' && stored.status==='rejected') {
        lastIssue='ESPN: '+errorMessage(direct.reason)+'; '+errorMessage(stored.reason);
        console.warn('[v39] Live scoreboard:',lastIssue);
      } else {
        lastIssue='';lastGood=lastCheck;
        lastSource=direct.status==='fulfilled'?direct.value.source:stored.value.source;
        if(changed)render();
      }
    } catch (e) {
      lastIssue=errorMessage(e);
      console.warn('[v39] Live refresh exception',e);
    } finally {busy=false;status();}
  };

  if (typeof available==='function' && typeof filtered==='function' && typeof fixture==='function') {
    const oldAvailable=available;
    available=function () {
      const list=oldAvailable();
      const seen=new Set(list.map(x=>x.id));
      for(const m of filtered()) if(m.live && !seen.has(m.id)) {list.push(fixture(m));seen.add(m.id);}
      return list.sort((a,b)=>Number(!!b.live)-Number(!!a.live)||Date.parse(a.date)-Date.parse(b.date));
    };
  }
  function start() {pollLive();}
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollLive();});
  // Deliberate: timer only when the app is open, not a server-side one-minute refresh.
  setInterval(()=>{if(!document.hidden)pollLive();},PULL_MS);
  console.info('[v39] 60-second live scoreboard polling active');
})();

'use strict';
/* Maç Analiz Pro v43 — SportScore score-only overlay.
 * Predictive model, frozen predictions, analysis and stored scores unaffected.
 * Strict home+away, league, date match; never invent a fixture/forecast.
 */
(function () {
  if (typeof state === 'undefined' || typeof render !== 'function' ||
      typeof pollLive !== 'function' || typeof load !== 'function') {
    console.error('[v43] app.js henüz yüklenmedi'); return;
  }
  const ENDPOINT = String(window.MAC_LIVE_SCORE_URL || '').trim();
  const EVERY_MS = 60_000;
  const MAX_DATA_AGE = 150_000;
  let inflight = false, lastCheck = 0, lastGood = 0, lastProblem = '';
  let lastReport = null, lastSnapshot = null;

  function canonical(value) {
    return String(value || '').toLowerCase()
      .replace(/ı/g, 'i').replace(/æ/g,'ae').replace(/œ/g,'oe').replace(/ß/g,'ss').replace(/ø/g,'o').replace(/ł/g,'l')
      .normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
      .replace(/&/g,' and ').replace(/[^a-z0-9]+/g,' ').replace(/\b(football club|futbol kulubu)\b/g,' ')
      .trim().replace(/\s+/g,' ');
  }
  // Only deterministic name equivalents; avoid score assignment through fuzzy similarity.
  const ALIASES = new Map(Object.entries({
    'man utd':'manchester united', 'man united':'manchester united',
    'man city':'manchester city', 'bayern munich':'bayern munchen',
    'fc bayern munchen':'bayern munchen', 'bayern munchen':'bayern munchen',
    'inter milan':'internazionale', 'inter':'internazionale',
    'psg':'paris saint germain', 'paris sg':'paris saint germain',
    'paris saint germain':'paris saint germain',
    'atletico madrid':'atletico de madrid',
    'vfb stuttgart':'stuttgart', 'borussia monchengladbach':'monchengladbach',
    'borussia m gladbach':'monchengladbach',
    'sporting lisbon':'sporting cp', 'athletic bilbao':'athletic club',
    'go ahead eagles':'go ahead eagles'
  }));
  function teamKey(name) {
    let n = canonical(name).replace(/^(fc|ac|afc|cf|sc|fk) /,'').replace(/ (fc|cf|sc)$/,'');
    return ALIASES.get(n)||n;
  }
  function competitionLeague(raw) {
    const n = canonical(raw);
    if (!n) return null;
    if (/conference league|europa conference/.test(n)) return 'uefa.europa.conf';
    if (/europa league/.test(n)) return 'uefa.europa';
    if (/champions league/.test(n)) return 'uefa.champions';
    if (/^(turkish )?(super lig|super league|trendyol super lig|superlig)$/.test(n)) return 'tur.1';
    if (/^(english )?premier league$/.test(n)) return 'eng.1';
    if (/^(english )?(efl )?championship$/.test(n)) return 'eng.2';
    if (/^(spanish )?(la liga|laliga|primera division|la liga ea sports)$/.test(n)) return 'esp.1';
    if (/^(german )?(bundesliga|1 bundesliga)$/.test(n)) return 'ger.1';
    if (/^(italian )?serie a( enilive)?$/.test(n)) return 'ita.1';
    if (/^(french )?ligue 1( mcdonalds| uber eats)?$/.test(n)) return 'fra.1';
    if (/^(dutch )?eredivisie$/.test(n)) return 'ned.1';
    return null;
  }
  function stamp(s) {return Number.isFinite(Date.parse(s)) ? Date.parse(s) : NaN;}
  function findFixture(ext, fixtures) {
    const sh = teamKey(ext.home?.name), sa = teamKey(ext.away?.name);
    const extTime = stamp(ext.date), extLeague = competitionLeague(ext.competition);
    if (!sh || !sa || !Number.isFinite(extTime)) return null;
    const options = fixtures.filter(m => {
      if (!m || !m.home || !m.away || !m.date) return false;
      if (teamKey(m.home.name)!==sh || teamKey(m.away.name)!==sa) return false;
      if (extLeague && m.league!==extLeague) return false;
      const diff=Math.abs(stamp(m.date)-extTime);
      // Some SportScore fixtures have 00:00 UTC time rather than kickoff.
      // For an identified competition allow same-day approx; otherwise
      // require accurate kick-off to avoid cup/league confusion.
      return Number.isFinite(diff) && diff <= (extLeague ? 26 : 3) * 3600_000;
    });
    // Never pick an arbitrary match if two fixtures could match.
    return options.length===1 ? options[0] : null;
  }
  function nowString(ms) {return new Date(ms).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit',second:'2-digit'});}
  function reason(err) {
    const msg=String(err?.message||err||'Bilinmeyen hata');
    return /abort|timeout/i.test(msg)?'Bağlantı zaman aşımı':msg.slice(0,120);
  }
  function reportBanner() {
    if (state.page !== 'home') return;
    const heading=[...document.querySelectorAll('#content .sectionhead h2')]
      .find(n=>/Canlı ve Yaklaşan Maçlar/.test(n.textContent));
    if (!heading) return;
    let line=heading.querySelector('[data-v43-live]');
    if (!line) {
      line=document.createElement('span');line.dataset.v43Live='1';
      line.style.cssText='display:block;font:500 11px/1.6 system-ui,sans-serif;margin-top:6px;white-space:normal';
      heading.appendChild(line);
    }
    if (lastProblem) {
      line.textContent='⚠ Canlı skor: '+lastProblem+' · '+nowString(lastCheck||Date.now());
      line.style.color='#ffbd7f';
    } else if (lastReport) {
      const x=lastReport;
      line.textContent=`✓ SportScore kontrolü ${nowString(lastGood)} · ${x.matches} maç eşleşti · ${x.updated} skor/durum güncellendi · ${x.feeds}/${x.totalFeeds} kaynak`;
      line.style.color=x.matches?'#84e9b7':'#e8c47c';
    } else {
      line.textContent='SportScore bağlantısı kontrol ediliyor…';line.style.color='#9cbacf';
    }
  }
  function attribution() {
    if (!['home','matches','detail','leagues'].includes(state.page)) return;
    const content=document.getElementById('content');
    if (!content || content.querySelector('[data-v43-credit]')) return;
    const a=document.createElement('a');
    a.dataset.v43Credit='1';a.href='https://sportscore.com/';
    a.textContent='Canlı skor verileri: SportScore';a.rel='dofollow noopener';
    a.target='_blank';
    a.style.cssText='display:block;font:500 11px/1.4 system-ui,sans-serif;color:#85afd7;text-align:center;margin:12px 0 16px;text-decoration:underline';
    content.appendChild(a);
  }
  const baseRender=render;
  render=function (...args) { const r=baseRender(...args);reportBanner();attribution();return r; };

  function currentTargets() {
    const list=Array.isArray(state.fixtures)?state.fixtures:[];
    const now=Date.now();
    return list.filter(m => {
      const when=stamp(m.date);
      return Number.isFinite(when) && when>now-48*3600_000 && when<now+3*86400_000;
    });
  }
  function applySportScore(events) {
    const fixtures=currentTargets();
    const seen=new Set();
    let matches=0,updated=0;
    for (const external of events) {
      if (!external || (!external.live && !external.done)) continue;
      const h=external.home?.score, a=external.away?.score;
      if (!Number.isInteger(h) || h<0 || !Number.isInteger(a) || a<0) continue;
      const m=findFixture(external,fixtures);
      if (!m || seen.has(m.id)) continue;
      seen.add(m.id);matches++;
      // Never regress a finished match into live play.
      if (m.done && !external.done) continue;
      const newClock=external.live?String(external.clock||''):'';
      if (m.live!==external.live || m.done!==external.done ||
          m.clock!==newClock || m.home.score!==h || m.away.score!==a) {
        if (external.done && !m.done && typeof recordFinished==='function') recordFinished(m);
        m.live=external.live;m.done=external.done;m.clock=newClock;
        m.home.score=h;m.away.score=a;
        // Keep associated history row in sync if not the same JS object.
        const historic=Array.isArray(state.history) ? state.history.find(x=>x.id===m.id) : null;
        if (historic && historic!==m) {
          historic.live=m.live;historic.done=m.done;historic.clock=m.clock;
          historic.home.score=h;historic.away.score=a;
        }
        updated++;
      }
    }
    if (updated) {
      const now=Date.now();
      state.fixtures=state.fixtures.filter(m=>!m.done||(typeof retainFinished==='function' && retainFinished(m,now)))
        .sort((a,b)=>Number(!!b.live)-Number(!!a.live)||stamp(a.date)-stamp(b.date));
    }
    return {matches,updated};
  }
  async function fetchScoreboard() {
    if (!/^https:\/\/[a-z0-9.-]+\.workers\.dev\/?$/i.test(ENDPOINT)) throw Error('Worker adresi geçersiz');
    const ctl=new AbortController(),timeout=setTimeout(()=>ctl.abort(),20000);
    try {
      const response=await fetch(ENDPOINT,{cache:'no-store',signal:ctl.signal,mode:'cors'});
      if (!response.ok) throw Error('Cloudflare HTTP '+response.status);
      const data=await response.json();
      if (data.version!==43 || data.source!=='SportScore' || !Array.isArray(data.events))
        throw Error('Worker hâlâ eski sürümde: SportScore v43 kodunu yayımla');
      const created=stamp(data.updatedAt), age=Date.now()-created;
      if (!Number.isFinite(created) || age>MAX_DATA_AGE || age < -120000) throw Error('Canlı veri zaman damgası eski');
      if (!data.successfulFeeds) throw Error('SportScore verisine ulaşılamadı');
      return data;
    } finally {clearTimeout(timeout);}
  }
  // Preserve the latest matched score when a slow GitHub data.json reloads.
  const oldLoad=load;
  load=async function (...args) {
    const v=await oldLoad(...args);
    if (lastSnapshot && Date.now()-lastSnapshot.checkedAt<10*60_000) {
      const result=applySportScore(lastSnapshot.events);
      if(result.updated)render();
    }
    return v;
  };
  pollLive=async function () {
    if (document.hidden || inflight || !Array.isArray(state.fixtures)) return;
    // app.js also owns a 60-sec timer; coalesce overlapping invocations.
    if(Date.now()-lastCheck<55_000)return;
    inflight=true;lastCheck=Date.now();
    try {
      const data=await fetchScoreboard();
      const result=applySportScore(data.events);
      lastGood=Date.now();lastProblem='';
      lastSnapshot={events:data.events,checkedAt:lastGood};
      lastReport={...result,feeds:data.successfulFeeds,totalFeeds:data.feedCount};
      if(result.updated)render();
    } catch(err) {
      lastProblem=reason(err);console.warn('[v43] SportScore:',err);
    } finally {inflight=false;reportBanner();}
  };
  // v33's timer still runs; this timer also covers other app.js versions.
  function start() {pollLive();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){lastCheck=0;pollLive();}});
  setInterval(()=>{if(!document.hidden)pollLive();},EVERY_MS);
  console.info('[v43] SportScore connected; 60-second poll registered.');
})();

'use strict';
// v38: 60-second direct scoreboard polling, visible health/status, GitHub fallback.
// This file MUST be included in index.html after app.js.
// It NEVER recalculates predictions or touches persisted prediction history.
(function () {
  if (typeof pollLive !== 'function' || typeof state === 'undefined' || typeof render !== 'function') {
    console.warn('[v38 live] app.js not loaded; live overlay inactive');
    return;
  }
  const RAW = 'https://raw.githubusercontent.com/yildizatakan-bit/skor/live-scores/live_scores.json';
  const MAX_AGE_MS = 22 * 60 * 1000;
  const TODAY_RANGE_MS = 7 * 60 * 60 * 1000;
  const PULL_MS = 60 * 1000;
  let busy = false;
  let lastSuccessful = null;
  let lastSource = '';
  let lastProblem = '';
  let lastAttempt = null;
  let confirmedLeagues = 0;
  const CORE_LEAGUES = ['tur.1','eng.1','esp.1','ger.1','ita.1','fra.1','uefa.champions','uefa.europa','uefa.europa.conf'];

  async function readPublishedSnapshot() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(RAW + '?t=' + Date.now(), {
        cache: 'no-store', mode: 'cors', signal: controller.signal
      });
      if (!response.ok) throw Error('GitHub HTTP ' + response.status);
      const data = await response.json();
      const stamp = Date.parse(data.updatedAt);
      if (!Number.isFinite(stamp) || Math.abs(Date.now() - stamp) > MAX_AGE_MS)
        throw Error('Yayınlanan canlı skor verisi güncel değil');
      if (!Array.isArray(data.events)) throw Error('Boş veya bozuk skor yanıtı');
      // A partly successful fetch is still usable, but do not apply missing leagues.
      return {events: data.events, at: stamp, name: 'GitHub 5 dk'};
    } finally {clearTimeout(timer);}
  }

  function activeLeagueDays() {
    // Query today's ESPN scoreboard even when published data.json is old.
    // This avoids a circular dependency: a missing live game would otherwise
    // prevent its league from being polled at all.
    const now = new Date();
    const wanted = new Set(CORE_LEAGUES);
    const near = (state.fixtures || []).concat(state.history || []);
    for (const m of near) {
      const kick = Date.parse(m.date);
      if (!Number.isFinite(kick) || kick < Date.now() - 10 * 3600000 || kick > Date.now() + 7 * 3600000) continue;
      if (LEAGUES.some(x => x[0] === m.league)) wanted.add(m.league);
    }
    // No more than 2 calendar dates for a league, usually just one.
    const localToday = dateKey(now);
    const utcToday = now.toISOString().slice(0, 10).replace(/-/g, '');
    const result = [];
    for (const league of wanted) {
      const gameDays = new Set([localToday, utcToday]);
      for (const m of near) if (m.league === league) {
        const kick = Date.parse(m.date);
        if (Number.isFinite(kick) && kick >= Date.now() - 10 * 3600000 && kick <= Date.now() + 7 * 3600000)
          gameDays.add(new Date(kick).toISOString().slice(0, 10).replace(/-/g, ''));
      }
      for (const day of [...gameDays].slice(-2)) result.push({league, day});
    }
    return result;
  }

  function fromEspn(e, league) {
    const competition = e?.competitions?.[0];
    const home = competition?.competitors?.find(x => x.homeAway === 'home');
    const away = competition?.competitors?.find(x => x.homeAway === 'away');
    if (!home || !away || !e?.id || !e?.date) return null;
    const type = e.status?.type || {};
    const done = type.completed === true;
    const live = !done && String(type.state || '').toLowerCase() === 'in';
    const score = x => {
      const v = x?.score;
      return v !== null && v !== undefined && String(v).trim() !== '' && Number.isInteger(Number(v)) && Number(v) >= 0 ? Number(v) : null;
    };
    const team = (x) => ({
      id: String(x.team?.id || ''),
      name: x.team?.displayName || x.team?.name || '?',
      logo: x.team?.logo || x.team?.logos?.[0]?.href || '',
      score: (live || done) ? score(x) : null
    });
    return {id: league + ':' + String(e.id), league, date: e.date, live, done,
      clock: String(e.status?.displayClock || ''), home: team(home), away: team(away)};
  }

  async function getDirectEspn() {
    const pairs = activeLeagueDays();
    if (!pairs.length) return [];
    const replies = await Promise.allSettled(pairs.map(async ({league, day}) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 9000);
      try {
        const url = BASE + encodeURIComponent(league) + '/scoreboard?dates=' + day + '&limit=200';
        const response = await fetch(url, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw Error('ESPN HTTP ' + response.status);
        const payload = await response.json();
        if (!Array.isArray(payload.events)) throw Error('ESPN events list missing');
        return payload.events.map(x => fromEspn(x, league)).filter(Boolean);
      } finally {clearTimeout(timer);}
    }));
    const success = replies.filter(x => x.status === 'fulfilled');
    confirmedLeagues = success.length;
    if (!success.length) throw Error('Canlı ESPN sorguları başarısız (' + replies.length + ' deneme)');
    return success.flatMap(x => x.value);
  }

  function applyScores(rows) {
    if (!rows.length) return false;
    const now = Date.now();
    const fixtureMap = new Map((state.fixtures || []).map(m => [m.id, m]));
    const historyMap = new Map((state.history || []).map(m => [m.id, m]));
    let changed = false;
    for (const update of rows) {
      if (!update || !update.id || !update.home || !update.away || !update.league) continue;
      const kickoff = Date.parse(update.date);
      if (!Number.isFinite(kickoff) || Math.abs(now - kickoff) > 8 * 86400000) continue;
      const live = update.live === true;
      const done = update.done === true;
      const val = (v) => v === null || v === undefined || v === '' ? null : (Number.isInteger(Number(v)) && Number(v) >= 0 ? Number(v) : null);
      const h = (live || done) ? val(update.home.score) : null;
      const a = (live || done) ? val(update.away.score) : null;
      let target = fixtureMap.get(update.id);
      if (!target) {
        // Only introduce games within the visible fixture period; analysis stays read-only.
        if (kickoff < now - TODAY_RANGE_MS || kickoff > now + 7 * 86400000) continue;
        target = {
          id: String(update.id), league: String(update.league), date: update.date,
          live, done, clock: String(update.clock || ''),
          home: {...update.home, score: h}, away: {...update.away, score: a}
        };
        state.fixtures.push(target);
        fixtureMap.set(target.id, target);
        if (!historyMap.has(target.id)) {state.history.push(target); historyMap.set(target.id, target);}
        if (done && typeof recordFinished === 'function') recordFinished(target);
        changed = true;
        continue;
      }
      // Never revert an already finished match to an unfinished status.
      if (target.done && !done) continue;
      const clock = live ? String(update.clock || '') : '';
      if (target.live !== live || target.done !== done || target.clock !== clock || target.home.score !== h || target.away.score !== a) {
        if (done && !target.done && typeof recordFinished === 'function') recordFinished(target);
        target.live = live; target.done = done; target.clock = clock;
        target.home.score = h; target.away.score = a;
        // Maintain separate history objects if latest snapshot already reloaded them.
        const historic = historyMap.get(target.id);
        if (historic && historic !== target) {
          historic.live = live; historic.done = done; historic.clock = clock;
          historic.home.score = h; historic.away.score = a;
        }
        changed = true;
      }
    }
    if (changed) {
      state.fixtures = state.fixtures.filter(m => !m.done || retainFinished(m, now))
        .sort((a, b) => Number(!!b.live) - Number(!!a.live) || Date.parse(a.date) - Date.parse(b.date));
    }
    return changed;
  }

  function decorateStatus() {
    if (state.page !== 'home') return;
    const heading = [...document.querySelectorAll('#content .sectionhead h2')]
      .find(x => /Canlı ve Yaklaşan Maçlar/.test(x.textContent));
    if (!heading) return;
    let status = heading.querySelector('[data-v38-live-status]');
    if (!status) {
      status = document.createElement('span');
      status.setAttribute('data-v38-live-status', '');
      status.style.cssText = 'display:block;font-size:11px;font-weight:500;margin-top:5px;color:#89c8e8;line-height:1.5';
      heading.appendChild(status);
    }
    const time = x => new Date(x).toLocaleTimeString('tr-TR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
    if (lastSuccessful) {
      status.textContent = '✓ Son skor kontrolü ' + time(lastSuccessful) + ' · ' + lastSource;
      status.style.color = '#8ce5c4';
      status.title = 'Canlı skor kontrolü başarılı. ESPN skoru değişmediyse ekrandaki skor da değişmez.';
    } else if (lastAttempt && lastProblem) {
      status.textContent = '⚠ Skor yenilenemedi · ' + time(lastAttempt);
      status.title = lastProblem;
      status.style.color = '#ffc27d';
    } else {
      status.textContent = 'Canlı skor bağlantısı kontrol ediliyor…';
      status.style.color = '#89c8e8';
    }
  }

  const oldRender = render;
  render = function () {oldRender(); decorateStatus();};
  // Existing app.js invokes pollLive on opening, every minute, after data reloads,
  // and whenever the app becomes visible. Replace only that broken polling function.
  pollLive = async function () {
    if (document.hidden || busy || !Array.isArray(state.fixtures) || !Array.isArray(state.history)) return;
    busy = true;
    lastAttempt = Date.now();
    try {
      const [published, direct] = await Promise.allSettled([readPublishedSnapshot(), getDirectEspn()]);
      const items = new Map();
      if (published.status === 'fulfilled') {
        for (const m of published.value.events) items.set(m.id, m);
        lastSuccessful = published.value.at;
        lastSource = published.value.name;
      }
      if (direct.status === 'fulfilled') {
        // A valid but empty live scoreboard is still a successful check.
        // Direct ESPN wins over stale GitHub snapshots for matching events.
        for (const m of direct.value) items.set(m.id, m);
        lastSuccessful = Date.now();
        lastSource = 'ESPN doğrudan (' + confirmedLeagues + ' kontrol)';
      }
      if (published.status === 'rejected' && direct.status === 'rejected') {
        lastProblem = [published.reason?.message, direct.reason?.message].filter(Boolean).join(' / ');
        lastSuccessful = null;
        console.warn('[v38 live] score source unavailable:', lastProblem);
      } else {lastProblem = '';}
      if (applyScores([...items.values()])) render();
      else decorateStatus();
    } catch (err) {
      lastProblem = String(err?.message || err);
      lastSuccessful = null;
      console.warn('[v38 live] refresh error:', err);
      decorateStatus();
    }
    finally {busy = false;}
  };
  // On first page visit app.js may have launched its old request before this script ran;
  // force a v38 fetch after load and on return from a background tab.
  // Show active/live fixtures even when data.json has not yet received
  // an analysis for them. Never fabricate a probability for these matches.
  if (typeof available === 'function' && typeof filtered === 'function' && typeof fixture === 'function') {
    const originalAvailable = available;
    available = function () {
      const original = originalAvailable();
      const ids = new Set(original.map(m => m.id));
      for (const m of filtered()) {
        if (!m.live || ids.has(m.id)) continue;
        original.push(fixture(m));
        ids.add(m.id);
      }
      return original.sort((a,b) => Number(!!b.live)-Number(!!a.live) || Date.parse(a.date)-Date.parse(b.date));
    };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => pollLive(), {once:true});
  else pollLive();
  document.addEventListener('visibilitychange', () => {if (!document.hidden) pollLive();});
  // Backup interval if another version of app.js does not contain its own polling loop.
  setInterval(() => {if (!document.hidden) pollLive();}, PULL_MS);
})();

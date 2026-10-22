'use strict';
const LEAGUES=[['tur.1','Süper Lig','🇹🇷'],['eng.1','Premier League','🏴'],['esp.1','La Liga','🇪🇸'],['ger.1','Bundesliga','🇩🇪'],['ita.1','Serie A','🇮🇹'],['fra.1','Ligue 1','🇫🇷'],['uefa.champions','Şampiyonlar Ligi','🏆'],['uefa.europa','Avrupa Ligi','🏅'],['uefa.europa.conf','Konferans Ligi','🥇'],['ned.1','Eredivisie','🇳🇱'],['eng.2','Championship','🏴'],['fifa.world','Dünya Kupası','🌍'],['uefa.euro','Avrupa Şampiyonası','🇪🇺'],['uefa.nations','Uluslar Ligi','🌍'],['conmebol.america','Copa América','🌎'],['concacaf.gold.cup','Gold Cup','🌎'],['afc.asian.cup','Asya Kupası','🌏'],['caf.nations','Afrika Uluslar Kupası','🌍']];
const BASE='https://site.api.espn.com/apis/site/v2/sports/soccer/';const $=id=>document.getElementById(id);const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const key='map-v3-';const FINISHED_WINDOW=2*60*60*1000;const finishedTimes=read('finishedTimes',{});function finishedAt(m,now=Date.now()){if(!m.done)return null;const kickoff=Date.parse(m.date);if(!Number.isFinite(kickoff)||kickoff>now||now-kickoff>6*60*60*1000)return null;return Number(finishedTimes[m.id])||kickoff+2*60*60*1000}function retainFinished(m,now=Date.now()){const end=finishedAt(m,now);return m.done&&end!==null&&now>=end&&now-end<FINISHED_WINDOW}function recordFinished(m,now=Date.now()){if(!finishedTimes[m.id]){finishedTimes[m.id]=now;save('finishedTimes',finishedTimes)}}const state={archive:[],page:'home',league:'all',match:null,market:'all',slide:0,fixtures:[],failures:[],loading:false,settings:read('settings',{auto:true}),favs:read('favorites',[]),updated:read('updated',0)};
function read(k,fallback){try{return JSON.parse(localStorage.getItem(key+k))??fallback}catch{return fallback}}function save(k,v){try{localStorage.setItem(key+k,JSON.stringify(v))}catch{}}const fmt=d=>new Date(d).toLocaleString('tr-TR',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});const dateKey=d=>{let y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),day=String(d.getDate()).padStart(2,'0');return `${y}${m}${day}`};
function parseEvent(e,league){const c=e.competitions?.[0],h=c?.competitors?.find(t=>t.homeAway==='home'),a=c?.competitors?.find(t=>t.homeAway==='away');if(!h||!a)return null;let done=e.status?.type?.completed===true;return {id:league+':'+e.id,league,date:e.date,done,home:{id:String(h.team?.id),name:h.team?.displayName||h.team?.name||'Ev sahibi',logo:h.team?.logo||h.team?.logos?.[0]?.href||'',score:done?Number(h.score):null},away:{id:String(a.team?.id),name:a.team?.displayName||a.team?.name||'Deplasman',logo:a.team?.logo||a.team?.logos?.[0]?.href||'',score:done?Number(a.score):null}}}
async function fetchLeague(code){let now=new Date(),start=new Date(now),end=new Date(now);start.setDate(start.getDate()-120);end.setDate(end.getDate()+14);let url=BASE+encodeURIComponent(code)+'/scoreboard?dates='+dateKey(start)+'-'+dateKey(end)+'&limit=1000';let ctl=new AbortController(),timeout=setTimeout(()=>ctl.abort(),16000);try{let r=await fetch(url,{signal:ctl.signal});if(!r.ok)throw Error('HTTP '+r.status);let j=await r.json();let ev=(j.events||[]).map(e=>parseEvent(e,code)).filter(Boolean);if(!ev.length)throw Error('Boş veri');return ev}finally{clearTimeout(timeout)}}
function history(team,date,all){return all.filter(m=>m.done&&new Date(m.date)<new Date(date)&&(m.home.id===team.id||m.away.id===team.id)).sort((a,b)=>new Date(b.date)-new Date(a.date)).slice(0,10)}
function form(ms,name){let w=0,d=0,l=0,gf=0,ga=0;for(let m of ms){let home=m.home.name===name,x=home?m.home.score:m.away.score,y=home?m.away.score:m.home.score;if(!Number.isFinite(x)||!Number.isFinite(y))continue;gf+=x;ga+=y;if(x>y)w++;else if(x===y)d++;else l++}return {w,d,l,gf:gf/ms.length,ga:ga/ms.length,n:ms.length}}
function pois(k,l){let x=Math.exp(-l);for(let i=1;i<=k;i++)x*=l/i;return x}
function model(m,all){let h=history(m.home,m.date,all),a=history(m.away,m.date,all);if(h.length<5||a.length<5)return {ready:false,nh:h.length,na:a.length};let hf=form(h,m.home.name),af=form(a,m.away.name),lh=Math.min(4.5,Math.max(.15,(hf.gf+af.ga)/2*1.12)),la=Math.min(4.5,Math.max(.15,(af.gf+hf.ga)/2*.9));let p={home:0,draw:0,away:0,over15:0,over25:0,over35:0,under15:0,under25:0,under35:0,btts:0,noBtts:0,homeOrDraw:0,awayOrDraw:0,noDraw:0,homeHalf:0,drawHalf:0,awayHalf:0,homeOver05:0,awayOver05:0,homeUnder15:0,awayUnder15:0,even:0,odd:0};let total=0;for(let i=0;i<=15;i++)for(let j=0;j<=15;j++){let v=pois(i,lh)*pois(j,la),g=i+j;total+=v;p[i>j?'home':i===j?'draw':'away']+=v;p[g>1?'over15':'under15']+=v;p[g>2?'over25':'under25']+=v;p[g>3?'over35':'under35']+=v;p[i&&j?'btts':'noBtts']+=v;p[i>=1?'homeOver05':'homeUnder15']+=0; // separate below
p[g%2?'odd':'even']+=v;if(i>0)p.homeOver05+=v;if(j>0)p.awayOver05+=v;if(i<2)p.homeUnder15+=v;if(j<2)p.awayUnder15+=v}
for(let k in p)p[k]/=total;p.homeOrDraw=p.home+p.draw;p.awayOrDraw=p.away+p.draw;p.noDraw=p.home+p.away;return {ready:true,p,lh,la,hf,af,nh:h.length,na:a.length}}
const markets=[['Maç Sonucu',[['1','home'],['X','draw'],['2','away']]],['Çifte Şans',[['1X','homeOrDraw'],['12','noDraw'],['X2','awayOrDraw']]],['Toplam Gol',[['1.5 Üst','over15'],['1.5 Alt','under15'],['2.5 Üst','over25'],['2.5 Alt','under25'],['3.5 Üst','over35'],['3.5 Alt','under35'],['4.5 Üst','over45'],['4.5 Alt','under45'],['5.5 Üst','over55'],['5.5 Alt','under55']]],['Karşılıklı Gol',[['Var','btts'],['Yok','noBtts']]],['Takım Golü',[['Ev sahibi 0.5 Üst','homeOver05'],['Deplasman 0.5 Üst','awayOver05'],['Ev sahibi 1.5 Alt','homeUnder15'],['Deplasman 1.5 Alt','awayUnder15'],['Ev sahibi 1.5 Üst','homeOver15'],['Deplasman 1.5 Üst','awayOver15'],['Ev sahibi 2.5 Alt','homeUnder25'],['Deplasman 2.5 Alt','awayUnder25']]],['Toplam Gol Tek/Çift',[['Tek','odd'],['Çift','even']]],['Gol Aralığı',[['0–1 Gol','goals01'],['2–3 Gol','goals23'],['4–5 Gol','goals45'],['6+ Gol','goals6plus']]],['Gol Farkı',[['Ev 1 farkla','marginHome1'],['Ev 2 farkla','marginHome2'],['Ev 3+ farkla','marginHome3plus'],['Dep 1 farkla','marginAway1'],['Dep 2 farkla','marginAway2'],['Dep 3+ farkla','marginAway3plus']]],['Kombine',[['1 ve 1.5 Üst','homeOver15Win'],['1 ve 2.5 Üst','homeOver25Win'],['2 ve 1.5 Üst','awayOver15Win'],['2 ve 2.5 Üst','awayOver25Win'],['1 ve KG Var','homeWinBtts'],['2 ve KG Var','awayWinBtts'],['2.5 Üst ve KG Var','over25Btts'],['2.5 Alt ve KG Var','under25Btts']]]];
// v30: new options are computed server-side from the same Elo-adjusted score matrix.
// Historical frozen predictions without these keys remain unchanged.
function percent(x){return Math.round(100*x)}function leagueName(code){return LEAGUES.find(x=>x[0]===code)?.[1]||code}function logo(t){return t.logo?`<img class="logo" src="${esc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`:'<span class="logo">⚽</span>'}
function filtered(){return state.fixtures.filter(m=>(!m.done||retainFinished(m))&&(state.league==='all'||m.league===state.league))}function fixture(m){let data=state.history||[],an=model(m,data);return {...m,analysis:an}}function available(){return filtered().map(fixture).filter(m=>m.analysis?.ready&&m.analysis.nh>=10&&m.analysis.na>=10)}function best(m){let a=m.analysis;if(!a.ready)return null;let pairs=markets.flatMap(([group,rows])=>rows.map(([name,k])=>({group,name,k,p:a.p[k]}))).filter(x=>Number.isFinite(x.p)&&x.p>=.5);return pairs.sort((a,b)=>b.p-a.p)[0]||null}
function triple(a){return `<div class="triple"><div class="pill win">1<strong>%${percent(a.p.home)}</strong></div><div class="pill draw">X<strong>%${percent(a.p.draw)}</strong></div><div class="pill loss">2<strong>%${percent(a.p.away)}</strong></div></div>`}
function hero(m,index,total){let b=best(m),ready=m.analysis.ready;return `<div class="hero" style="background-image:linear-gradient(180deg,rgba(4,17,38,.74) 0%,rgba(5,19,39,.52) 40%,rgba(4,17,34,.84) 100%),url('./stadium-featured.webp');background-size:cover;background-position:center;background-repeat:no-repeat;" id="featured-slide" data-open="${esc(m.id)}" role="button" tabindex="0" aria-label="Öne çıkan maç ${index+1} / ${total}"><div class="top"><span class="tag">★ ${b?'Günün maçı':'Günün karşılaşması'}</span><span class="flag">${esc(leagueName(m.league))}</span></div><div class="versus"><div>${logo(m.home)}<b>${esc(m.home.name)}</b></div><strong>${(m.live||m.done)&&Number.isInteger(m.home.score)&&Number.isInteger(m.away.score)?m.home.score+' - '+m.away.score:'VS'}</strong><div>${logo(m.away)}<b>${esc(m.away.name)}</b></div></div><div class="muted" style="text-align:center;margin-bottom:10px">${m.live?`<span style="color:#ff666e;font-weight:800">🔴 CANLI ${esc(m.clock||'')}</span> · ${fmt(m.date)}`:fmt(m.date)}</div><div style="font-size:clamp(22px,4.5vw,30px);font-weight:850;text-align:center;color:#f5f9ff;margin:18px 0 14px;letter-spacing:-.4px">Maç Sonucu Tahmini <span style="white-space:nowrap;color:#b7d8ff">(1-X-2)</span></div>${ready?triple(m.analysis):'<div class="muted" style="text-align:center">Bu maç için yeterli geçmiş veri yok.</div>'}${b?`<div style="margin-top:18px;padding:13px 16px;border:1px solid #315572;border-radius:14px;background:rgba(15,36,59,.65);font-size:16px;line-height:1.55;text-align:center"><span style="color:#b6cbe0">Diğer güçlü tahmin</span><div style="font-weight:750;color:#fff">${esc(b.group)} · ${esc(b.name)} — <span style="color:#ffc847">%${percent(b.p)}</span></div></div>`:''}<div class="dots" aria-label="${index+1}. maç, toplam ${total} maç">${index+1} / ${total}</div><button class="link" data-open="${esc(m.id)}" style="width:100%;text-align:center">Tüm olasılıkları gör →</button></div>`}
function istanbulDay(date){return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Istanbul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(date))}
function featuredMatches(){
  const now=Date.now(), today=istanbulDay(now);
  const matches=available().filter(m=>!m.done||retainFinished(m));
  // A live fixture must remain featured even after its scheduled kickoff time.
  const liveToday=matches.filter(m=>(m.live||m.done)&&istanbulDay(m.date)===today).sort((a,b)=>new Date(a.date)-new Date(b.date));
  const upcoming=matches.filter(m=>!m.live&&new Date(m.date).getTime()>=now).sort((a,b)=>new Date(a.date)-new Date(b.date));
  const todayUpcoming=upcoming.filter(m=>istanbulDay(m.date)===today);
  // Keep every live game in the carousel; show today's upcoming games alongside them.
  if(liveToday.length)return [...liveToday,...todayUpcoming];
  if(todayUpcoming.length)return todayUpcoming;
  if(!upcoming.length)return [];
  const day=istanbulDay(upcoming[0].date);
  return upcoming.filter(m=>istanbulDay(m.date)===day);
}
function liveMinute(m){let v=String(m.clock||'').match(/\d+/);return v?Math.min(120,Number(v[0])):null}
function liveAnalysis(m){const base=state.analyses?.[m.id];return base||{ready:false,nh:0,na:0};}

function matchCard(m){let a=m.analysis;return `<button class="card match" data-open="${esc(m.id)}">${logo(m.home)}<div class="teams"><small>${esc(leagueName(m.league))} · ${m.done?'✓ TAMAMLANDI':m.live?'🔴 CANLI '+esc(m.clock||''):fmt(m.date)}</small><b>${esc(m.home.name)} – ${esc(m.away.name)}${(m.live||m.done)&&Number.isInteger(m.home.score)&&Number.isInteger(m.away.score)?' · '+m.home.score+'-'+m.away.score:''}</b></div>${a.ready?`<div class="mini"><span>%${percent(a.p.home)}</span><span>%${percent(a.p.draw)}</span><span>%${percent(a.p.away)}</span></div>`:'<span class="muted">Veri az</span>'}<span>›</span></button>`}
function leagueTabs(){return `<div class="tabs"><button data-league="all" class="${state.league==='all'?'active':''}">Tümü</button>${LEAGUES.map(([id,name,flag])=>`<button data-league="${id}" class="${state.league===id?'active':''}">${flag} ${esc(name)}</button>`).join('')}</div>`}
function home(){let all=available(),featured=featuredMatches(),idx=featured.length?((state.slide%featured.length)+featured.length)%featured.length:0;return `${leagueTabs()}<div class="sectionhead"><h2>🏆 ${featured.length&&istanbulDay(featured[0].date)!==istanbulDay(Date.now())?'Sıradaki Günün Maçları':'Günün Öne Çıkanları'}</h2><button class="link" data-page="matches">Tümünü gör ›</button></div>${featured.length?hero(featured[idx],idx,featured.length):`<div class="empty">${state.loading?'Maç verileri alınıyor…':'Son 10 maç verisi yeterli olan yaklaşan karşılaşma bulunamadı.'}</div>`}${featured.length>1?`<div class="actions"><button data-slide="prev" aria-label="Önceki maç">‹ Önceki</button><button data-slide="next" aria-label="Sonraki maç">Sonraki ›</button></div>`:''}<div class="sectionhead"><h2>Canlı ve Yaklaşan Maçlar</h2><button class="link" data-page="matches">Tümünü gör ›</button></div>${all.length?all.slice(0,8).map(matchCard).join(''):'<div class="empty">Bu ligde henüz maç bulunamadı.</div>'}`}
function matches(){let all=available();return `<h1 class="heading">Tüm Maçlar</h1>${leagueTabs()}<div class="muted">Canlı, son 2 saatte tamamlanan ve gelecek 7 günün maçları · ${all.length} karşılaşma</div>${all.length?all.map(matchCard).join(''):'<div class="empty">Seçilen lig için maç bulunamadı.</div>'}`}
function leagues(){return `<h1 class="heading">Ligler</h1><div class="leaguegrid">${LEAGUES.map(([id,name,flag])=>`<button data-leaguepage="${id}"><span>${flag}</span>${esc(name)}</button>`).join('')}</div>`}
function marketRow(m,group,name,k,p){let id=m.id+'|'+k,saved=state.favs.some(x=>x.id===id);return `<div class="marketrow"><div><b>${esc(name)}</b><div class="muted">${esc(group)}</div></div><span class="pct">%${percent(p)}</span><button class="star ${saved?'saved':''}" data-fav="${esc(id)}" aria-label="Favorilere ekle">${saved?'★':'☆'}</button></div>`}
function explainModel(m,a){
  if(!a?.ready)return '';
  const elo=a.elo||{}, n=Math.min(a.nh||0,a.na||0), e=Math.min(elo.homeMatches||0,elo.awayMatches||0);
  const quality=n>=10&&e>=20?'Orta — sınırlı tarihsel örneklem':'Düşük — sınırlı tarihsel örneklem';
  const fmtE=v=>Number.isFinite(Number(v))?Number(v).toFixed(1):'Veri yok';
  const adj=a.opponentAdjustment||{};
  const opponentNote=adj.home&&adj.away?`<p class="muted">Son rakiplerin ortalama Elo gücü: ${esc(m.home.name)} ${fmtE(adj.home.avgOpponentElo)}, ${esc(m.away.name)} ${fmtE(adj.away.avgOpponentElo)}. Rakip kalitesi düzeltmesi: ×${fmtE(adj.home.factor)} / ×${fmtE(adj.away.factor)}. Katsayılar temkinli ve henüz doğrulanmamıştır.</p>`:'';
  return `<section class="market" style="padding:18px;margin-top:20px"><h3>Analiz nasıl hesaplandı?</h3>
    <p class="muted">Model: ${esc(a.model||'Bilinmiyor')} · Veri değerlendirmesi: <strong>${esc(quality)}</strong></p>
    <div class="stats"><div><small>${esc(m.home.name)} · Elo</small><b>${fmtE(elo.home)}</b><small>${elo.homeMatches||0} kayıtlı maç</small></div><div><small>${esc(m.away.name)} · Elo</small><b>${fmtE(elo.away)}</b><small>${elo.awayMatches||0} kayıtlı maç</small></div></div>
    <p class="muted">Elo farkı: ${fmtE(Number(elo.home)-Number(elo.away))} · İç saha avantajı: ${fmtE(elo.homeAdvantage)} Elo puanı. Bunlar yalnızca kayıtlı maçlardan hesaplanır; tüm liglerde mutlak güç sıralaması değildir.</p>
    <div class="stats"><div><small>Ev sahibi beklenen gol</small><b>${fmtE(a.lh)}</b><small>Poisson λ</small></div><div><small>Deplasman beklenen gol</small><b>${fmtE(a.la)}</b><small>Poisson λ</small></div></div>
    <p class="muted">Son maç formu: ${esc(m.home.name)} ${a.hf.w}G ${a.hf.d}B ${a.hf.l}M; ${esc(m.away.name)} ${a.af.w}G ${a.af.d}B ${a.af.l}M. Gol ortalamaları, iç saha etkisi ve sınırlı Elo düzeltmesi birlikte kullanılır.</p>
    ${opponentNote}
    <p class="note">Eksik girdiler: doğrulanmış ilk 11, oyuncu gücü, sakatlıklar, cezalar ve xG. Bu veriler hesaplanmış gibi gösterilmez. Yüzdeler kalibre edilmiş veya bahis getirisi kanıtlanmış olasılıklar değildir.</p></section>`;
}
function detail(){let m=state.fixtures.find(x=>x.id===state.match)||state.history.find(x=>x.id===state.match);if(!m)return `<div class="empty">Maç bulunamadı.</div>`;m=fixture(m);let a=m.analysis;return `<button class="link" data-page="matches">‹ Maçlara dön</button><h1 class="heading">Maç Detayı</h1><div class="detailhero"><div class="muted" style="text-align:center">${esc(leagueName(m.league))} · ${m.done?'✓ TAMAMLANDI':m.live?'🔴 CANLI '+esc(m.clock||''):fmt(m.date)}</div><div class="versus"><div>${logo(m.home)}<b>${esc(m.home.name)}</b></div><strong>${(m.live||m.done)&&Number.isInteger(m.home.score)&&Number.isInteger(m.away.score)?m.home.score+' - '+m.away.score:'VS'}</strong><div>${logo(m.away)}<b>${esc(m.away.name)}</b></div></div>${a.ready?triple(a):''}${m.live?'<p class="muted">🔴 Canlı skor. Tahminler maç öncesinde kaydedilen yüzdelerdir; maç boyunca değiştirilmez.</p>':''}</div>${!a.ready?`<div class="banner">Güvenilir analiz için en az 5 geçmiş maç gerekli. Bulunan: ${a.nh} / ${a.na}. Olasılık gösterilmiyor.</div>`:`<div class="sectionhead"><h2>Maç Sonucu Tahmini (1-X-2)</h2><span class="muted">Tüm ihtimaller</span></div><section class="market"><h3>Maç Sonucu</h3>${markets[0][1].map(([name,k])=>marketRow(m,'Maç Sonucu',name,k,a.p[k])).join('')}</section><div class="sectionhead"><h2>${m.live?'Maç öncesi diğer tahminler':'Diğer bahis türü olasılıkları'}</h2><span class="muted">Sadece ≥ %50</span></div><div class="chips">${[['all','Tümü'],...markets.slice(1).map(([n])=>[n,n])].map(([k,n])=>`<button data-market="${esc(k)}" class="${state.market===k?'active':''}">${esc(n)}</button>`).join('')}</div>${markets.slice(1).filter(([group])=>(state.market==='all'||group===state.market)).map(([group,rows])=>{let visible=rows.filter(([,k])=>Number.isFinite(a.p[k])&&a.p[k]>=.5);return visible.length?`<section class="market"><h3>${esc(group)}</h3>${visible.map(([name,k])=>marketRow(m,group,name,k,a.p[k])).join('')}</section>`:''}).join('')}<h2 class="sectionhead">Son 10 Maç Formu (Tüm Kulvarlar)</h2><div class="stats"><div><small>${esc(m.home.name)}</small><b>${a.hf.w}G ${a.hf.d}B ${a.hf.l}M</b><small>Gol ort. ${a.hf.gf.toFixed(2)}</small></div><div><small>${esc(m.away.name)}</small><b>${a.af.w}G ${a.af.d}B ${a.af.l}M</b><small>Gol ort. ${a.af.gf.toFixed(2)}</small></div></div><p class="muted">Lig, UEFA ve veri kaynağında bulunan kupa maçları birlikte değerlendirilir. Elo takım gücü rakip seviyesine göre hesaplanır. Beklenen gol: ${a.lh.toFixed(2)} – ${a.la.toFixed(2)}. Her takım için bulunan geçmiş maç sayısı: ${a.nh} / ${a.na}.</p>${explainModel(m,a)}` }<p class="note">Bu değerler Poisson modeliyle hesaplanan tahmini olasılıklardır, iddaa.com bahis oranları değildir. Model tüm bahis türlerini kapsamaz; yalnızca hesaplayabildiği pazarlar listelenir. Sakatlık ve kadro etkileri henüz modele dahil değildir. Takım gücü için geçmiş sonuçlara dayalı Elo değerlendirmesi kullanılır.</p>`}
function favorites(){let items=state.favs;return `<h1 class="heading">★ Favorilerim <span class="muted">(${items.length})</span></h1><p class="muted">Kaydettiğin maç ve tahminleri burada topluca görebilirsin. Yüzdeler son veri güncellemesindeki hesaplamaya göre değişebilir.</p>${items.length?items.map(f=>{let m=state.fixtures.find(x=>x.id===f.matchId);let a=m?fixture(m).analysis:null;let p=a?.ready&&Number.isFinite(a.p[f.market])?a.p[f.market]:f.p;return `<div class="card rowline"><div><b>${esc(f.home)} – ${esc(f.away)}</b><div class="muted">${esc(f.group)} · ${esc(f.name)}</div><small class="muted">${fmt(f.date)}</small></div><span class="pct" style="color:#70f4b5;font-weight:800">%${percent(p)}</span><button class="star saved" data-fav="${esc(f.id)}">★</button></div>`}).join(''):'<div class="empty">Henüz favorin yok. Maç detayında %50 üzerindeki bir tahminin yanındaki ☆ simgesine dokun.</div>'}`}
function settings(){let v=state.validation;let rate=v?.primaryHitRate==null?'Henüz sonuç yok':'%'+Math.round(v.primaryHitRate*100);return `<h1 class="heading">Ayarlar</h1><div class="card"><b>Geçmiş tahminlerin başarısı</b><p class="muted">Maç başlamadan önce kaydedilmiş, sonradan sonuçlanmış ${v?.evaluatedPrimaryPredictions||0} maçta tek bir önceden seçilmiş tahmin: <strong>${rate}</strong></p><p class="note">Bu oran geçmiş sonuçları gösterir; gelecek maçlar için başarı garantisi değildir. Yeterli örnek birikene kadar dikkatli yorumlanmalıdır.</p></div><div class="card"><div><b>Günlük veri kontrolü</b><div class="muted">GitHub Actions ile günlük fikstür ve sonuç kontrolü; geçmiş kayıtlar korunur. Canlı maçlar ayrı sorgulanır.</div></div></div><div class="card"><b>Son veri güncellemesi</b><p class="muted">${state.updated?new Date(state.updated).toLocaleString('tr-TR'):'Henüz başarılı güncelleme yok'}</p><button class="iconbtn" id="manual">Şimdi yenile ↻</button></div><div class="card"><b>Veri kaynağı</b><p class="muted">GitHub Actions tarafından hazırlanan data.json dosyası. Kaynak: ESPN kamuya açık futbol skorları uç noktaları (resmî üçüncü taraf API garantisi yoktur).</p></div><p class="note">Canlı skorlar uygulama açıkken veri kaynağından yaklaşık 60 saniyede bir sorgulanır; erişim engellenirse son yayınlanan skor korunur; telefon bildirimleri bu sürümde yoktur. Yerel favoriler bu cihazın tarayıcısında saklanır.</p>`}

function archiveOutcome(k,h,a){const t=h+a;const f={home:h>a,draw:h===a,away:h<a,homeOrDraw:h>=a,noDraw:h!==a,awayOrDraw:h<=a,over15:t>=2,under15:t<=1,over25:t>=3,under25:t<=2,over35:t>=4,under35:t<=3,btts:h>0&&a>0,noBtts:h===0||a===0,homeOver05:h>=1,awayOver05:a>=1,homeUnder15:h<=1,awayUnder15:a<=1,odd:t%2===1,even:t%2===0,over05:t>=1,under05:t===0,over45:t>=5,under45:t<=4,over55:t>=6,under55:t<=5,homeOver15:h>=2,awayOver15:a>=2,homeUnder25:h<=2,awayUnder25:a<=2,goals01:t<=1,goals23:t>=2&&t<=3,goals45:t>=4&&t<=5,goals6plus:t>=6,marginHome1:h-a===1,marginHome2:h-a===2,marginHome3plus:h-a>=3,marginAway1:a-h===1,marginAway2:a-h===2,marginAway3plus:a-h>=3,homeOver15Win:h>a&&t>=2,homeOver25Win:h>a&&t>=3,awayOver15Win:a>h&&t>=2,awayOver25Win:a>h&&t>=3,homeWinBtts:h>a&&a>0,awayWinBtts:a>h&&h>0,over25Btts:t>=3&&h>0&&a>0,under25Btts:t<=2&&h>0&&a>0};return f[k]}
// Mark only the model's preferred outcome in each comparable market.
// Other alternatives stay neutral instead of being marked as failed predictions.
function archivePick(k,p){
  const groups=[['home','draw','away'],['homeOrDraw','noDraw','awayOrDraw'],['over15','under15'],['over25','under25'],['over35','under35'],['btts','noBtts'],['odd','even']];
  const g=groups.find(x=>x.includes(k));
  if(g){const valid=g.filter(x=>Number.isFinite(p?.[x]));if(!valid.length)return false;return valid.reduce((best,x)=>p[x]>p[best]?x:best,valid[0])===k;}
  // Team-goal propositions are separate events, not mutually exclusive alternatives.
  return Number.isFinite(p?.[k])&&p[k]>=.5;
}
function archiveMarketRow(name,k,p,h,a){const selected=archivePick(k,p);const hit=selected?archiveOutcome(k,h,a):null;const bg=!selected?'':`background:${hit?'rgba(0,175,100,.18)':'rgba(220,65,76,.19)'};border-left:4px solid ${hit?'#2bd78c':'#ff6570'};`;const color=!selected?'':`color:${hit?'#6cf0ae':'#ff939d'};`;return `<div class="marketrow" style="${bg}padding:12px;border-radius:9px;margin:7px 0"><div><b>${esc(name)}</b></div><strong style="${color}">%${percent(p[k])}${selected?` · <span aria-label="${hit?'Tahmin gerçekleşti':'Tahmin gerçekleşmedi'}" title="${hit?'Tahmin gerçekleşti':'Tahmin gerçekleşmedi'}">${hit?'✓':'✗'}</span>`:''}</strong></div>`}
function archiveRows(row,m){const h=Number(row.score?.[0]??m?.home?.score),a=Number(row.score?.[1]??m?.away?.score);if(!Number.isInteger(h)||!Number.isInteger(a))return '<div class="muted">Kesin skor bekleniyor.</div>';return markets.map(([group,items])=>`<section class="market"><h3>${esc(group)}</h3>${items.filter(([,k])=>Number.isFinite(row.p?.[k])).map(([name,k])=>archiveMarketRow(name,k,row.p,h,a)).join('')}</section>`).join('')}
function archivePage(){const all=state.archive.filter(r=>r.settled&&r.p&&Array.isArray(r.score)).sort((a,b)=>new Date(b.kickoff)-new Date(a.kickoff));const rows=all.filter(r=>state.league==='all'||r.league===state.league);return `<h1 class="heading">Tahmin Arşivi</h1><p class="muted">Yalnızca maç başlamadan önce kaydedilen tahminler değerlendirilir. Yüksek olasılıklı tahminlerde yeşil ✓, kırmızı ✗ kullanılır; diğer seçenekler işaretsizdir.</p>${leagueTabs()}<div class="muted" style="margin:12px 0">${rows.length} sonuçlanmış ve önceden tahmin edilmiş maç</div>${rows.length?rows.map(r=>{const m=state.history.find(x=>x.id===r.matchId);return `<button class="card match" data-archive-open="${esc(r.matchId)}"><div class="teams"><small>${esc(leagueName(r.league))} · ${fmt(r.kickoff)}</small><b>${esc(m?.home?.name||r.home||'Ev sahibi')} – ${esc(m?.away?.name||r.away||'Deplasman')}</b></div><strong>${r.score[0]} - ${r.score[1]}</strong><span>›</span></button>`}).join(''):'<div class="empty">Henüz sonuçlanmış ve önceden kaydedilmiş tahmin yok. Yeni maçlar tamamlandıkça burada görünecek.</div>'}`}
function archiveDetail(){const r=state.archive.find(x=>x.matchId===state.match&&x.settled);if(!r)return '<div class="empty">Arşiv kaydı bulunamadı.</div>';const m=state.history.find(x=>x.id===r.matchId);return `<button class="link" data-page="archive">‹ Arşive dön</button><h1 class="heading">Maç Sonucu ve Tahmin Kontrolü</h1><div class="detailhero"><div class="muted" style="text-align:center">${esc(leagueName(r.league))} · ${fmt(r.kickoff)} · Maç tamamlandı</div><div class="versus"><div>${m?logo(m.home):''}<b>${esc(m?.home?.name||r.home||'Ev sahibi')}</b></div><strong>${r.score[0]} - ${r.score[1]}</strong><div>${m?logo(m.away):''}<b>${esc(m?.away?.name||r.away||'Deplasman')}</b></div></div></div><h2>Maç Sonucu Tahmini (1-X-2)</h2><section class="market">${markets[0][1].filter(([,k])=>Number.isFinite(r.p[k])).map(([name,k])=>archiveMarketRow(name,k,r.p,r.score[0],r.score[1])).join('')}</section><h2>Diğer tahminlerin sonuçları</h2>${markets.slice(1).map(([group,items])=>`<section class="market"><h3>${esc(group)}</h3>${items.filter(([,k])=>Number.isFinite(r.p[k])).map(([name,k])=>archiveMarketRow(name,k,r.p,r.score[0],r.score[1])).join('')}</section>`).join('')}<p class="note">Yalnızca yüksek olasılıklı tercih işaretlenir: yeşil ✓ gerçekleşti, kırmızı ✗ gerçekleşmedi. Diğer seçenekler karşılaştırma için işaretsiz gösterilir. Yüzdeler maç öncesinde kaydedilmiştir.</p>`}
function ensureArchiveNav(){const nav=document.querySelector('nav');if(!nav||nav.querySelector('[data-page="archive"]'))return;const b=document.createElement('button');b.dataset.page='archive';b.innerHTML='▤<span>Arşiv</span>';const settings=nav.querySelector('[data-page="settings"]');nav.insertBefore(b,settings||null);nav.style.display='grid';nav.style.gridTemplateColumns='repeat(6,minmax(0,1fr))';nav.querySelectorAll('button').forEach(x=>{x.style.minWidth='0';x.style.paddingLeft='2px';x.style.paddingRight='2px';});}

function render(){ensureArchiveNav();let navPage=state.page==='detail'?'matches':state.page==='archiveDetail'?'archive':state.page;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===navPage));$('content').innerHTML=({home,matches,leagues,favorites,settings,detail,archive:archivePage,archiveDetail}[state.page]||home)();if(state.failures.length&&state.page==='settings')$('content').insertAdjacentHTML('beforeend',`<div class="banner">Erişim uyarıları: ${esc(state.failures.join(' | '))}</div>`)}
async function load(force=false){
  if(state.loading)return;
  state.loading=true;state.failures=[];render();
  // Preferred path: data.json is refreshed by GitHub Actions approximately every 5 minutes.
  // Manual refresh reloads the published snapshot; it does not silently scrape other sites.
  try{
    const r=await fetch('./data.json?ts='+Date.now(),{cache:'no-store'});
    if(!r.ok)throw Error('HTTP '+r.status);
    const j=await r.json();
    if(!Array.isArray(j.events)||!j.events.length)throw Error('Boş veri dosyası');
    const valid=j.events.filter(x=>x&&x.id&&x.league&&x.date&&x.home&&x.away);
    if(!valid.length)throw Error('Geçerli maç bulunamadı');
    state.history=valid;
    state.analyses=j.analyses||{};state.validation=j.validation||null;state.archive=Array.isArray(j.predictionArchive)?j.predictionArchive:[];
    const now=Date.now();
    state.fixtures=valid.filter(m=>retainFinished(m,now)||(!m.done&&(m.live||(new Date(m.date).getTime()>=now-4*3600e3&&new Date(m.date).getTime()<=now+7*864e5)))).sort((a,b)=>Number(!!b.live)-Number(!!a.live)||new Date(a.date)-new Date(b.date));
    state.updated=Date.parse(j.updatedAt)||Date.now();
    state.failures=j.errors||[];
    save('updated',state.updated);save('events',valid);save('analyses',state.analyses);
  }catch(err){
    state.failures=['Yayınlanan veri dosyasına ulaşılamadı: '+err.message];
    // Do not delete last valid cached snapshot.
    if(!state.history?.length){const cached=read('events',[]);if(Array.isArray(cached)&&cached.length){state.history=cached;state.analyses=read('analyses',{});state.fixtures=cached.filter(m=>!m.done||retainFinished(m));}}
  }
  state.loading=false;render();pollLive();
}
// Historical results are kept in a separate collection, so past matches are never lost when the fixture list is built.
function fixtureWithHistory(m){
  // The backend has the authoritative v14 Elo + Poisson result. Do not
  // silently replace it with the old browser-only goals-average model.
  const stored=state.analyses?.[m.id];
  const base=stored || {ready:false,nh:0,na:0};
  const frozen=state.archive?.find(x=>x.matchId===m.id);
  if(frozen?.p && base.ready)return {...m,analysis:{...base,p:frozen.p}};
  return {...m,analysis:base};
}
fixture=fixtureWithHistory;
function init(){state.history=[];state.analyses={};state.fixtures=[];render();load()}
document.addEventListener('click',e=>{let el=e.target.closest('button');if(!el){const slide=e.target.closest('#featured-slide[data-open]');if(slide){state.match=slide.dataset.open;state.market='all';state.page='detail';render();scrollTo(0,0)}return}let d=el.dataset;if(d.page){state.page=d.page;state.market='all';render();scrollTo(0,0)}else if(d.league!==undefined){state.league=d.league;state.slide=0;render()}else if(d.leaguepage){state.league=d.leaguepage;state.page='matches';render()}else if(d.archiveOpen){state.match=d.archiveOpen;state.page='archiveDetail';render();scrollTo(0,0)}else if(d.open){state.match=d.open;state.market='all';state.page='detail';render();scrollTo(0,0)}else if(d.market){state.market=d.market;render()}else if(d.slide){state.slide+=d.slide==='next'?1:-1;let n=featuredMatches().length;state.slide=(state.slide+n)%Math.max(n,1);render()}else if(d.fav){let [matchId,k]=d.fav.split('|'),index=state.favs.findIndex(x=>x.id===d.fav);if(index>=0)state.favs.splice(index,1);else{let m=state.fixtures.find(x=>x.id===matchId),a=m&&fixture(m).analysis,found=markets.flatMap(([group,rows])=>rows.map(([name,market])=>({group,name,market}))).find(x=>x.market===k);if(!m||!a.ready||!found||!Number.isFinite(a.p[k])||a.p[k]<.5)return;state.favs.push({id:d.fav,matchId,market:k,group:found.group,name:found.name,home:m.home.name,away:m.away.name,date:m.date,p:a.p[k]})}save('favorites',state.favs);render()}else if(el.id==='manual'||el.id==='refresh'){load(true)}});
document.addEventListener('change',e=>{if(e.target.id==='auto'){state.settings.auto=e.target.checked;save('settings',state.settings);render()}});
if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').catch(()=>{});init();

// Keep historical analysis in the GitHub snapshot; poll only today's scoreboard for live scores.
// ESPN's unofficial endpoint may disallow browser CORS; in that case the published snapshot remains.
let livePolling=false;
async function pollLive(){
  if(document.hidden||livePolling)return;
  livePolling=true;
  try{
    const today=new Date(),yesterday=new Date(today.getTime()-86400000);
    const tomorrow=new Date(today.getTime()+86400000);const dates=[dateKey(yesterday),dateKey(tomorrow)];
    const updates=await Promise.allSettled(LEAGUES.map(async ([league])=>{
      const url=BASE+encodeURIComponent(league)+'/scoreboard?dates='+dates.join('-')+'&limit=200';
      const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),9000);
      try{
        const response=await fetch(url,{cache:'no-store',signal:ctl.signal});
        if(!response.ok)throw Error('HTTP '+response.status);
        const payload=await response.json();
        return (payload.events||[]).map(e=>{
          const comp=e.competitions?.[0],home=comp?.competitors?.find(x=>x.homeAway==='home'),away=comp?.competitors?.find(x=>x.homeAway==='away');
          const type=e.status?.type||{},live=type.state==='in'&&!type.completed;
          const done=type.completed===true;
          if(!home||!away)return null;
          const score=x=>x?.score!==undefined&&x.score!==null&&String(x.score).trim()!==''&&Number.isFinite(Number(x.score))?Number(x.score):null;
          return {id:league+':'+e.id,live,done,clock:String(e.status?.displayClock||''),hs:score(home),as:score(away),event:parseEvent(e,league)};
        }).filter(Boolean);
      }finally{clearTimeout(timer)}
    }));
    const map=new Map(updates.filter(x=>x.status==='fulfilled').flatMap(x=>x.value).map(x=>[x.id,x]));
    let changed=false;
    for(const v of map.values()){
      if(!v.live||!v.event||state.fixtures.some(m=>m.id===v.id))continue;
      const m={...v.event,live:true,clock:v.clock,done:false};
      m.home.score=v.hs;m.away.score=v.as;
      state.fixtures.push(m);
      if(!state.history.some(x=>x.id===m.id))state.history.push(m);
      changed=true;
    }
    for(const m of state.fixtures){
      const v=map.get(m.id);if(!v)continue;
      const newLive=v.live,clock=v.clock,hs=(v.live||v.done)?v.hs:null,as=(v.live||v.done)?v.as:null;
      if(m.live!==newLive||m.clock!==clock||m.home.score!==hs||m.away.score!==as){
        m.live=newLive;m.clock=clock;m.home.score=hs;m.away.score=as;changed=true;
      }
      if(v.done){if(!m.done)recordFinished(m);m.done=true;m.live=false;changed=true}
    }
    if(changed){state.fixtures=state.fixtures.filter(m=>!m.done||retainFinished(m)).sort((a,b)=>Number(!!b.live)-Number(!!a.live)||new Date(a.date)-new Date(b.date));render();}
  }catch(e){/* Preserve the last published snapshot when the live source is unavailable. */}
  finally{livePolling=false}
}
setInterval(()=>{if(!document.hidden){state.fixtures=state.fixtures.filter(m=>!m.done||retainFinished(m));pollLive();render()}},60*1000);
setInterval(()=>{if(!document.hidden)load()},5*60*1000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden){pollLive();if(!state.updated||Date.now()-state.updated>5*60*1000)load()}});

// Swipe between featured matches on touch screens.
let touchStartX=null;document.addEventListener('touchstart',e=>{if(e.target.closest('#featured-slide'))touchStartX=e.changedTouches[0].screenX},{passive:true});document.addEventListener('touchend',e=>{if(touchStartX===null)return;const delta=e.changedTouches[0].screenX-touchStartX;touchStartX=null;if(Math.abs(delta)<45||state.page!=='home'||featuredMatches().length<2)return;state.slide+=delta<0?1:-1;render()},{passive:true});

// Keyboard support for the clickable featured match.
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('#featured-slide[data-open]')){e.preventDefault();state.match=e.target.dataset.open;state.market='all';state.page='detail';render();scrollTo(0,0)}});

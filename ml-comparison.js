'use strict';
/* Experimental ML comparison overlay. Never changes the frozen historical archive. */
(() => {
  const choiceKey='mac-analiz-ml-model-v49';
  let choice='classic', predictions={}, metrics=null;
  try {choice=localStorage.getItem(choiceKey)==='ml'?'ml':'classic'}catch{}
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=p=>Number.isFinite(p)?'%'+Math.round(p*100):'—';
  function eligible(p){return p&&['home','draw','away'].every(k=>Number.isFinite(p[k])&&p[k]>=0&&p[k]<=1)&&Math.abs(p.home+p.draw+p.away-1)<.03}
  function get(m){const row=predictions[m?.id];return row&&eligible(row.p)?row:null}
  function original(m){
    const base=state.analyses?.[m.id]||{ready:false,nh:0,na:0};
    const frozen=state.archive?.find(x=>x.matchId===m.id);
    return frozen?.p&&base.ready?{...base,p:frozen.p}:base;
  }
  function analysis(m){
    const base=original(m),row=get(m);
    if(choice!=='ml'||!row||!base.ready)return base;
    const p={...base.p,...row.p};
    p.homeOrDraw=p.home+p.draw;
    p.awayOrDraw=p.away+p.draw;
    p.noDraw=p.home+p.away;
    return {...base,p,model:'Machine Learning (1X2 deneysel)',mlMode:true};
  }
  function switcher(){
    const n=Object.keys(predictions).length;
    const ml=choice==='ml';
    return '<section id="ml-compare" class="card" style="padding:12px 14px;margin:10px 0 16px">'
      +'<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap"><b>🧠 Tahmin modeli</b><small class="muted">ML: '+n+' maç</small></div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px">'
      +'<button type="button" data-ml-model="classic" aria-pressed="'+(!ml)+'" style="padding:10px;border-radius:10px;border:1px solid #3e6082;background:'+(!ml?'#185e9e':'#10243d')+';color:#fff;font-weight:700">Mevcut sistem</button>'
      +'<button type="button" data-ml-model="ml" aria-pressed="'+ml+'" style="padding:10px;border-radius:10px;border:1px solid #3e6082;background:'+(ml?'#185e9e':'#10243d')+';color:#fff;font-weight:700">Machine Learning</button></div>'
      +'<p class="muted" style="font-size:12px;margin:8px 0 0">'+(ml?'ML yalnızca 1X2 içindir. Diğer pazarlar mevcut modeldir. ML verisi yoksa mevcut 1X2 gösterilir.':'Mevcut Poisson/Elo modeli. Geçmiş kayıtlar değişmez.')+'</p></section>';
  }
  function compareBox(m){
    const classic=original(m), ml=get(m);
    if(!classic?.ready)return '';
    const card=(name,p)=>'<div style="flex:1;min-width:135px;border:1px solid #294b6d;border-radius:10px;padding:10px"><b>'+esc(name)+'</b><div style="display:flex;justify-content:space-between;margin-top:8px"><span>1 '+fmt(p.home)+'</span><span>X '+fmt(p.draw)+'</span><span>2 '+fmt(p.away)+'</span></div></div>';
    return '<section class="market" style="margin-top:12px"><h3>İki modelin 1X2 karşılaştırması</h3><div style="display:flex;gap:8px;flex-wrap:wrap">'+card('Mevcut',classic.p)+(ml?card('ML',ml.p):'<div class="muted" style="padding:10px">Bu maç için henüz ML tahmini yok.</div>')+'</div></section>';
  }
  function metricsBox(){
    const c=metrics?.comparison||{},m=c.ml||{},p=c.classic||{};
    const same=Number(c.sameMatches)||0;
    return '<section class="card" style="padding:14px;margin:12px 0"><h3>📊 Model başarı karşılaştırması</h3>'
      +'<p class="muted">Yalnızca maç öncesinde kaydedilen ve sonuçlanan ortak 1X2 tahminleri.</p>'
      +'<div style="display:flex;gap:8px;flex-wrap:wrap">'
      +'<div style="flex:1;min-width:130px"><b>Mevcut</b><p>Doğruluk: '+(same?fmt(p.accuracy):'—')+'</p><small>Brier: '+(same&&p.brier!=null?p.brier:'—')+'</small></div>'
      +'<div style="flex:1;min-width:130px"><b>ML</b><p>Doğruluk: '+(same?fmt(m.accuracy):'—')+'</p><small>Brier: '+(same&&m.brier!=null?m.brier:'—')+'</small></div></div>'
      +'<p class="muted">Ortak maç: '+same+'. '+(same?'Düşük Brier daha iyi kalibrasyona işaret eder.':'Karşılaştırma için yeni maçların sonuçlanması gerekiyor.')+'</p></section>';
  }
  const originalFixture=fixture;
  fixture=function(m){const result=originalFixture(m);return {...result,analysis:analysis(m)}};
  const originalRender=render;
  render=function(){
    originalRender();
    const root=document.getElementById('content');
    if(!root)return;
    if(['home','matches','detail','favorites'].includes(state.page)){
      root.insertAdjacentHTML('afterbegin',switcher());
      if(state.page==='detail'){
        const m=state.fixtures.find(x=>x.id===state.match)||state.history.find(x=>x.id===state.match);
        if(m)root.insertAdjacentHTML('beforeend',compareBox(m));
      }
    }
    if(state.page==='settings'||state.page==='archive')root.insertAdjacentHTML('afterbegin',metricsBox());
  };
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-ml-model]');
    if(!button)return;
    choice=button.dataset.mlModel==='ml'?'ml':'classic';
    try{localStorage.setItem(choiceKey,choice)}catch{}
    render();
  });
  async function loadML(){
    try{
      const [a,b]=await Promise.all([
        fetch('./ml_predictions.json?t='+Date.now(),{cache:'no-store'}),
        fetch('./ml_metrics.json?t='+Date.now(),{cache:'no-store'})
      ]);
      if(a.ok){const j=await a.json();predictions=j.predictions||{}}
      if(b.ok)metrics=await b.json();
      render();
    }catch(err){console.warn('ML verileri henüz hazır değil:',err)}
  }
  loadML();
})();

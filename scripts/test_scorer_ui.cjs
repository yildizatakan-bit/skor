const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app.js','utf8');
const code=source.slice(source.indexOf('const scorerOpen ='),source.indexOf('function matchCard(m)'));
const ctx={document:{addEventListener(){}},mlV45:{mode:'mlplus',plus:{}},
 esc:v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')};
vm.createContext(ctx);vm.runInContext(code,ctx);
const match={id:'match',date:'2026-12-01T12:00:00Z',home:{name:'Home'},away:{name:'Away'}};
ctx.mlV45.plus.match={kickoff:match.date,selection:['minutes','confirmed'],scorers:{teams:{home:[
 {name:'<script>alert(1)</script>',goals:0,appearances:5,probability:0,selected:true,availability:'unknown'},
 {name:'Missing',goals:null,appearances:null,probability:null,selected:false}
],away:[]}}};
const html=ctx.scorerList(match);
assert(html.includes('&lt;script>'));assert(!html.includes('<script>'));
assert(html.includes('%0'));assert(html.includes('Hesaplanamadı'));
assert(html.includes('tahmini ilk 11'));assert(html.includes('Doğrulanmış ilk 11'));
ctx.mlV45.mode='classic';assert.equal(ctx.scorerList(match),'');
ctx.mlV45.mode='mlplus';ctx.mlV45.plus.match.kickoff='2026-12-02';
assert.equal(ctx.scorerList(match),'');
console.log('Scorer UI: escaping, zero/missing, lineup labels, model and kickoff checks passed');

const apply=source.split('\n').find(line=>line.startsWith('function mlV45Apply'));
const detail=source.split('\n').find(line=>line.startsWith('function detail'));
ctx.state={match:'match',fixtures:[match],history:[],market:'all'};
ctx.fixture=m=>({...m,analysis:ctx.mlV45Apply(m,ctx.base)});
ctx.esc=String;ctx.leagueName=()=>'';ctx.logo=()=>'';ctx.fmt=()=>'';ctx.triple=()=>'';ctx.explainModel=()=>'';ctx.scorerList=()=>'<div>Scorer list</div>';
ctx.markets=[['Maç Sonucu',[['1','home']]],['Toplam Gol',[['2.5 Üst','over25']]],['Karşılıklı Gol',[['Var','btts']]]];
ctx.marketRow=(m,g,n,k,p)=>g+':'+k+':'+p;
ctx.mlV45.plus.match={kickoff:match.date,p:{home:.5,draw:.3,away:.2}};
ctx.base={ready:true,p:{home:.4,draw:.3,away:.3,over25:.61,btts:.55}};
vm.runInContext(apply+'\n'+detail,ctx);
const merged=ctx.mlV45Apply(match,ctx.base);
assert.equal(merged.p.over25,.61);assert.equal(merged.p.btts,.55);
assert.equal(merged.p.home,.5);assert.equal(merged.p.homeOrDraw,.8);
let rendered=ctx.detail();
assert(rendered.includes('over25:0.61'));assert(rendered.includes('btts:0.55'));
assert(rendered.includes('Poisson / Elo'));assert(rendered.includes('Scorer list'));
ctx.base={ready:false};
assert.doesNotThrow(()=>ctx.detail());
console.log('MLPlus other markets preserved and rendered without baseline form');

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

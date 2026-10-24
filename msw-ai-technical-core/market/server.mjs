import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const DIR=path.dirname(fileURLToPath(import.meta.url));
const BASE='/gielda-technologii-ai/';
const GROUP_NAMES={DOMY:'Domy i Smart Home',POJAZDY:'Pojazdy i elektromobilność',MASZYNY:'Maszyny i przemysł',IT_AI:'Elektronika, IT i AI'};
const SAFE='Wynik orientacyjny, wyłącznie na podstawie wprowadzonych liczb. Nie jest diagnozą urządzenia ani zgodą na zmianę instalacji.';
const CHECKS=[
 ['ohm-i','Prawo Ohma — prąd','A',['Napięcie [V]','Rezystancja [Ω]'],(a,b)=>a/b,[12,6],2],
 ['ohm-u','Prawo Ohma — napięcie','V',['Prąd [A]','Rezystancja [Ω]'],(a,b)=>a*b,[2,6],12],
 ['ohm-r','Prawo Ohma — rezystancja','Ω',['Napięcie [V]','Prąd [A]'],(a,b)=>a/b,[12,2],6],
 ['power-dc','Moc prądu stałego','W',['Napięcie [V]','Prąd [A]'],(a,b)=>a*b,[12,2],24],
 ['power-ac','Moc czynna 1-fazowa','W',['Napięcie [V]','Prąd [A]','Współczynnik mocy'],(a,b,c)=>a*b*c,[230,10,0.9],2070],
 ['power-3p','Moc czynna 3-fazowa','W',['Napięcie LL [V]','Prąd [A]','Współczynnik mocy'],(a,b,c)=>Math.sqrt(3)*a*b*c,[400,10,0.9],6235.382907],
 ['energy','Zużycie energii','kWh',['Moc [kW]','Czas [h]'],(a,b)=>a*b,[2,3],6],
 ['runtime','Szacowany czas pracy','h',['Energia [Wh]','Moc [W]'],(a,b)=>a/b,[500,100],5],
 ['drop-volts','Spadek napięcia (model uproszczony)','V',['Prąd [A]','Opór toru [Ω]'],(a,b)=>a*b,[10,0.4],4],
 ['drop-pct','Spadek napięcia w %','%',['Spadek [V]','Zasilanie [V]'],(a,b)=>a/b*100,[4,200],2],
 ['divider','Dzielnik rezystorowy','V',['Napięcie [V]','R2 [Ω]','R1 [Ω]'],(a,b,c)=>a*b/(b+c),[12,1000,1000],6],
 ['rc-cutoff','Filtr RC — częstotliwość graniczna','Hz',['Rezystancja [Ω]','Pojemność [F]'],(a,b)=>1/(2*Math.PI*a*b),[1000,0.000001],159.154943],
 ['rc-tau','Stała czasowa RC','s',['Rezystancja [Ω]','Pojemność [F]'],(a,b)=>a*b,[1000,0.000001],0.001],
 ['xc','Reaktancja pojemnościowa','Ω',['Częstotliwość [Hz]','Pojemność [F]'],(a,b)=>1/(2*Math.PI*a*b),[50,0.000001],3183.098862],
 ['xl','Reaktancja indukcyjna','Ω',['Częstotliwość [Hz]','Indukcyjność [H]'],(a,b)=>2*Math.PI*a*b,[50,0.1],31.415927],
 ['slip','Poślizg silnika','%',['Prędkość synch. [rpm]','Prędkość wału [rpm]'],(a,b)=>(a-b)/a*100,[1500,1440],4],
 ['hz-rpm','Częstotliwość na obr./min','rpm',['Częstotliwość obrotowa [Hz]'],(a)=>a*60,[25],1500],
 ['bar-pa','Przeliczanie bar na Pa','Pa',['Ciśnienie [bar]'],(a)=>a*100000,[1.5],150000],
 ['flow','Przepływ L/min na m³/h','m³/h',['Przepływ [L/min]'],(a)=>a*0.06,[100],6],
 ['pipe-speed','Prędkość przepływu w rurze','m/s',['Przepływ [L/min]','Średnica wewnętrzna [mm]'],(a,b)=>(a/60000)/(Math.PI*Math.pow(b/1000,2)/4),[60,20],3.183099],
 ['cop','COP — pompa ciepła','—',['Energia cieplna [kWh]','Energia elektryczna [kWh]'],(a,b)=>a/b,[12,3],4],
 ['efficiency','Sprawność energetyczna','%',['Energia wyjściowa','Energia wejściowa'],(a,b)=>a/b*100,[80,100],80],
 ['battery-energy','Energia baterii','Wh',['Napięcie [V]','Pojemność [Ah]'],(a,b)=>a*b,[12,100],1200],
 ['battery-duration','Czas zasilania teoretyczny','h',['Energia [Wh]','Pobór [W]'],(a,b)=>a/b,[1200,120],10],
 ['c-to-f','Temperatura °C na °F','°F',['Temperatura [°C]'],(a)=>a*1.8+32,[20],68]
];
const TESTS=CHECKS.map(c=>({code:c[0],name:c[1],unit:c[2],fields:c[3],fn:c[4],test_values:c[5],test_expected:c[6]}));
const numberValue=v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=1e9;
const valid=(x,n)=>Array.isArray(x)&&x.length===n&&x.every(numberValue);
function compute(check,values){
 if(!valid(values,check.fields.length))return {ok:false,error:'Nieprawidłowe liczby. Sprawdź pola wejściowe (|liczba| <= 1e9).'};
 let result;try{result=check.fn(...values)}catch{return {ok:false,error:'Nie można obliczyć'};}
 if(!Number.isFinite(result)||Math.abs(result)>1e15)return {ok:false,error:'Dzielenie przez zero lub wynik poza zakresem'};
 return {ok:true,value:Number(result.toPrecision(10)),unit:check.unit,labels:check.fields,inputs:values,notice:SAFE};
}
const CATS=[0,1,2,3,4,10,11,12,13,14,18,19,20,21,22,29,30,31,32,33];
export function createMarketHandler({families,research,audit}){
 const domains=[];
 for(const fam of families){for(const name of fam.subfields){domains.push({id:'D-'+String(domains.length+1).padStart(3,'0'),name,group:fam.group,family:fam.name,source:'MSW Technical Core / public technical taxonomy',kind:'subfield'});}}
 for(const fam of families){for(let i=0;i<2;i++){const name=fam.subfields[i]+' — '+fam.subfields[i+2];domains.push({id:'D-'+String(domains.length+1).padStart(3,'0'),name,group:fam.group,family:fam.name,source:'MSW composite research profile',kind:'composite_topic'});}}
 for(let i=0;i<20;i++){const f=families[i],next=families[(i+20)%families.length];domains.push({id:'D-'+String(domains.length+1).padStart(3,'0'),name:f.subfields[4]+' + '+next.subfields[0],group:f.group,family:f.name+' × '+next.name,source:'MSW cross-domain research profile',kind:'cross_domain'});}
 if(domains.length!==300)throw Error('domains_count_'+domains.length);
 const operations=[];
 for(const fIndex of CATS){const fam=families[fIndex];for(const check of TESTS){operations.push({id:'OP-'+String(operations.length+1).padStart(3,'0'),name:check.name+' · '+fam.name,check_id:check.code,base_name:check.name,group:fam.group,family:fam.name,unit:check.unit,fields:check.fields,defaults:check.test_values,kind:'read_only_numeric_calculator',requires_measurement:true,safety:SAFE});}}
 if(operations.length!==500)throw Error('operations_count_'+operations.length);
 const bots=domains.map((d,i)=>({id:'BOT-'+String(i+1).padStart(3,'0'),name:'MSW Tech Research · '+d.name,group:d.group,domain_id:d.id,role:['Research','Diagnostyka','Dokumentacja','Walidacja','Monitoring'][i%5],status:'profil_zarejestrowany_nie_uruchomiony',online:false,external_api_connected:false,publication_authorized:false,website_crawling_active:false}));
 const events=[],lastIP=new Map();let benchmarks=0;
 function record(id,ok,elapsed){events.push({t:new Date().toISOString(),id,ok,ms:Number(elapsed.toFixed(3))});if(events.length>600)events.splice(0,events.length-600);}
 const json=(res,code,payload)=>{res.writeHead(code,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer','access-control-allow-origin':'*'});res.end(JSON.stringify(payload));};
 const stat=(res,file,type)=>{res.writeHead(200,{'content-type':type,'cache-control':'public,max-age=60','x-content-type-options':'nosniff','referrer-policy':'strict-origin-when-cross-origin','content-security-policy':"default-src 'self'; img-src 'self' data:; connect-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors *"});res.end(fs.readFileSync(path.join(DIR,file)))};
 function take(items,p){const q=String(p.get('q')||'').trim().toLocaleLowerCase('pl-PL').slice(0,100);const group=String(p.get('group')||'').toUpperCase();const page=Math.max(1,Math.min(300,Number(p.get('page'))||1));const per_page=Math.max(1,Math.min(50,Number(p.get('per_page'))||20));let subset=items;if(group)subset=subset.filter(x=>x.group===group);if(q)subset=subset.filter(x=>(x.name+' '+(x.id||'')+' '+(x.family||'')+' '+(x.role||'')).toLocaleLowerCase('pl-PL').includes(q));return {items:subset.slice((page-1)*per_page,page*per_page),page,per_page,total:subset.length,pages:Math.ceil(subset.length/per_page)}}
 async function body(req){let raw='';for await(const chunk of req){raw+=chunk;if(raw.length>8192)throw Error('payload_too_large')}try{return JSON.parse(raw||'{}')}catch{throw Error('invalid_json')}}
 function throttle(req){const ip=String(req.socket.remoteAddress||'local');if(lastIP.size>1000)lastIP.clear();const v=lastIP.get(ip)||{start:Date.now(),n:0};if(Date.now()-v.start>60000){v.start=Date.now();v.n=0}v.n++;lastIP.set(ip,v);return v.n<=150}
 function breakdown(){return Object.keys(GROUP_NAMES).map(group=>({group,name:GROUP_NAMES[group],domains:domains.filter(x=>x.group===group).length,bots:bots.filter(x=>x.group===group).length,operations:operations.filter(x=>x.group===group).length}));}
 function series(){if(!events.length)return [];const bucket=10;const out=[];for(let i=0;i<events.length;i+=bucket){const rows=events.slice(i,i+bucket);const points=rows.map(x=>x.ok?Math.max(0,100-Math.min(60,x.ms/2)):0);out.push({time:rows[0].t,open:points[0],close:points[points.length-1],high:Math.max(...points),low:Math.min(...points),volume:rows.length,success:rows.filter(x=>x.ok).length});}return out.slice(-60)}
 const kpi=()=>{let ok=events.filter(e=>e.ok).length;return {domains:300,operations:500,algorithms:25,bot_profiles:300,bot_processes_online:0,verified_external_models_online:0,benchmarks_run:benchmarks,evaluations:events.length,successful_evaluations:ok,success_rate:events.length?Number((ok/events.length*100).toFixed(2)):null,avg_latency_ms:events.length?Number((events.reduce((s,x)=>s+x.ms,0)/events.length).toFixed(3)):null,observation_scope:'since_current_service_process_started',memory:'runtime-only benchmark ledger, not trained neural weights',activity_verified:true}};
 async function handle(req,res,url){const p=url.pathname;
 if(req.method==='GET'&&(p===BASE||p==='/gielda-technologii-ai')){stat(res,'index.html','text/html; charset=utf-8');return true}
 if(req.method==='GET'&&p==='/market/style.css'){stat(res,'style.css','text/css; charset=utf-8');return true}
 if(req.method==='GET'&&p==='/market/app.js'){stat(res,'app.js','text/javascript; charset=utf-8');return true}
 if(!p.startsWith('/api/market/'))return false;
 if(!throttle(req)){json(res,429,{error:'rate_limited'});return true}
 if(req.method==='GET'&&p==='/api/market/health'){json(res,200,{ok:true,version:'1.0.0',kind:'public_technical_dashboard',counts:kpi(),safe_public_sources:true,robots_are_profiles_not_running_processes:true});return true}
 if(req.method==='GET'&&p==='/api/market/domains'){json(res,200,take(domains,url.searchParams));return true}
 if(req.method==='GET'&&p==='/api/market/operations'){json(res,200,take(operations,url.searchParams));return true}
 if(req.method==='GET'&&p==='/api/market/bots'){json(res,200,take(bots,url.searchParams));return true}
 if(req.method==='GET'&&p==='/api/market/dashboard'){json(res,200,{kpi:kpi(),groups:breakdown(),ohlc:series(),recent:events.slice(-18).reverse(),research:{checked_at:research.checkedAt,release_count:research.results.length,failures:research.failures.length,source:research.source},wordpress_audit:{checked_at:audit.checkedAt,checked_urls:audit.results.length},warning:'No market prices, model power rankings, visitor tracking or bots are fabricated.'});return true}
 if(req.method==='GET'&&p==='/api/market/research'){json(res,200,{checked_at:research.checkedAt,items:research.results.slice(0,20),source:'GitHub public releases; not editorially approved'});return true}
 if(req.method==='GET'&&p==='/api/market/manifest'){json(res,200,{name:'MSW AI Giełda Technologii',portal:BASE,read_only:true,tools_count:operations.length,domains_count:domains.length,profiles_count:bots.length,methods:['GET /api/market/domains','GET /api/market/operations','GET /api/market/bots','POST /api/market/evaluate','POST /api/market/benchmark'],robots_status:'catalogued_not_running',training_weights:false,privacy:'no visitor IPs or customer data are published'});return true}
 if(req.method==='POST'&&p==='/api/market/evaluate'){try{const d=await body(req),op=operations.find(x=>x.id===d.id);if(!op){json(res,404,{error:'unknown_operation'});return true;}const check=TESTS.find(x=>x.code===op.check_id);const begin=performance.now();const out=compute(check,d.values);record(op.id,out.ok,performance.now()-begin);json(res,out.ok?200:422,{operation:op.id,formula:op.base_name,context:op.family,...out});}catch(e){json(res,400,{error:String(e.message).slice(0,80)})}return true}
 if(req.method==='POST'&&p==='/api/market/benchmark'){try{const begin=performance.now(),items=[];for(const c of TESTS){const start=performance.now(),out=compute(c,c.test_values);const passed=out.ok&&Math.abs(out.value-c.test_expected)<=Math.max(1e-6,Math.abs(c.test_expected)*0.000001);record('TEST-'+c.code,passed,performance.now()-start);items.push({id:c.code,pass:passed,actual:out.value,expected:c.test_expected,unit:c.unit});}benchmarks++;json(res,200,{ok:items.every(x=>x.pass),checked:items.length,passed:items.filter(x=>x.pass).length,elapsed_ms:Number((performance.now()-begin).toFixed(3)),test_type:'deterministic_reference_calculation_not_external_ai_model',results:items});}catch(e){json(res,500,{error:'benchmark_failed'})}return true}
 json(res,405,{error:'unsupported_route_or_method'});return true;
 }
 return handle;
}

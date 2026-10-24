import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash,timingSafeEqual} from 'node:crypto';

const DIR=path.dirname(fileURLToPath(import.meta.url));
const DATA=path.join(DIR,'data');
const families=JSON.parse(fs.readFileSync(path.join(DATA,'families.json'),'utf8'));
const tracks=JSON.parse(fs.readFileSync(path.join(DATA,'tracks.json'),'utf8'));
const MODES=['Projektowanie','Montaż i uruchomienie','Diagnostyka','Utrzymanie i naprawy','Integracja AI'];
const LPHASE=['Fundamenty i wielkości','Topologia i połączenia','Diagnostyka objawu','Integracja i AI','Bezpieczeństwo i uprawnienia','Walidacja i dokumentacja'];
const BPHASE=['Identyfikacja stanowiska','Próbka sygnału','Analiza logów','Hipotezy diagnostyczne','Test danych API','Wykrywanie anomalii','Audyt bezpieczeństwa','Raport serwisowy'];
const POLICY={DOMY:'Odczyt i analiza. Bez autonomicznego sterowania zamkami, alarmami, ewakuacją i energią.',POJAZDY:'Bez aktywnych komend do ECU, hamulców, napędu i ADAS.',MASZYNY:'Bez komend zmieniających PLC, interlock, E-STOP albo działającą maszynę.',IT_AI:'Bez tajnych kluczy, danych osobowych, prywatnych logów i sieci klientów.'};
const agents=[];
const spec=[];
for(const fam of families){
 for(const topic of fam.subfields){
  for(const role of MODES){
   const n=spec.length+1,id=`MSW-SP-${String(n).padStart(4,'0')}`,agent_id=`MSWAG-${String(n).padStart(4,'0')}`;
   const v={id,agent_id,group:fam.group,family:fam.name,topic,role,source:fam.source,safety:POLICY[fam.group],status:'editorial_profile_not_running',system:'MSW internal taxonomy; NOT an ESCO official identifier'};
   spec.push(v);
   agents.push({id:agent_id,specialization_id:id,name:`MSW Technical Agent ${String(n).padStart(4,'0')} · ${topic} · ${role}`,group:fam.group,category:fam.name,capabilities:['read_public_approved_knowledge','propose_diagnostic','request_peer_review'],online:false,autonomous_device_control:false,external_model_connected:false,safety:v.safety});
  }
 }
}
const lessons=[];
const labs=[];
for(const t of tracks){
 for(let i=0;i<10;i++){
  const topic=t.topics[i];
  for(let k=0;k<6;k++)lessons.push({id:`L-${t.slug}-${String(i+1).padStart(2,'0')}-${String(k+1).padStart(2,'0')}`,kind:'lesson',group:t.group,track:t.slug,title:`${topic} — ${LPHASE[k]}`,topic,phase:k+1,standards:t.standards,source:t.source});
  for(let k=0;k<8;k++)labs.push({id:`B-${t.slug}-${String(i+1).padStart(2,'0')}-${String(k+1).padStart(2,'0')}`,kind:'lab',group:t.group,track:t.slug,title:`${topic} — ${BPHASE[k]}`,topic,phase:k+1,standards:t.standards,source:t.source});
 }
}
if(families.length!==40||spec.length!==1000||agents.length!==1000||lessons.length!==1200||labs.length!==1600)throw Error('Curriculum count mismatch');
const RELEASES=[
 ['home-assistant/core','Smart Home'],['esphome/esphome','IoT firmware'],['espressif/esp-idf','Elektronika / ESP32'],
 ['zephyrproject-rtos/zephyr','RTOS'],['open62541/open62541','OPC UA / przemysł'],['ros2/ros2','Robotyka'],
 ['ros-controls/ros2_control','Mechatronika'],['OpenEMS/openems','Energia'],['Koenkk/zigbee2mqtt','Zigbee'],
 ['openhab/openhab-core','Automatyka domowa'],['hardbyte/python-can','CAN / Automotive'],
 ['qiskit/qiskit','IBM Qiskit'],['quantumlib/Cirq','Google Cirq'],['PennyLaneAI/pennylane','Fotonika / symulacje']
];
const AUDIT_SLUGS=['centrum-technicznych-ai','gielda-technologii-ai','arena-diagnostyki-ai','kreator-agentow-technicznych-ai','ai-w-inteligentnym-domu','ai-diagnostyka-pojazdow','ai-automatyka-maszyn','ai-energia-ev-i-hvac','ai-integracje-i-api','katalog-agentow-technicznych','sklep-modulow-ai-iot','projekty-inzynierii-ai','monitoring-botow-i-systemow','baza-wiedzy-technicznej-ai','ai-automatyzacja-dla-firm','msw-technical-university'];
const research={checkedAt:null,results:[],failures:[],lastRunReason:'not_run',cursor:0,attempted:0,source:'upstream GitHub releases; metadata only',persistent:false};
const audit={checkedAt:null,results:[],cursor:0,coverage:'16 project URLs; not all pages of the entire WordPress portal'};
const cache={refreshing:false,lastResearchMs:0,lastAuditMs:0};
const now=()=>new Date().toISOString();
const noSecrets=()=>!process.env.MSW_CORE_REFRESH_TOKEN;
const constantTimeEqual=(a,b)=>{if(!a||!b)return false;const x=createHash('sha256').update(a).digest(),y=createHash('sha256').update(b).digest();return timingSafeEqual(x,y)};
const secureRefresh=req=>!noSecrets()&&constantTimeEqual(req.headers['x-msw-core-key']||'',process.env.MSW_CORE_REFRESH_TOKEN);
function search(arr,p){let result=arr;const group=(p.get('group')||'').toUpperCase();const q=(p.get('q')||'').toLocaleLowerCase('pl-PL').slice(0,100);if(group)result=result.filter(x=>x.group===group);if(q)result=result.filter(x=>`${x.title||x.name||''} ${x.topic||''} ${x.family||''} ${x.standards||''}`.toLocaleLowerCase('pl-PL').includes(q));const page=Math.max(1,Math.min(100,Number(p.get('page'))||1)),size=Math.max(1,Math.min(50,Number(p.get('per_page'))||20));return {items:result.slice((page-1)*size,page*size),total:result.length,page,per_page:size,pages:Math.ceil(result.length/size)};}
const json=(res,status,data)=>{const text=JSON.stringify(data);res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','access-control-allow-origin':'*','x-content-type-options':'nosniff','x-robots-tag':'noindex, follow'});res.end(text)};
const timeout=ms=>AbortSignal.timeout(ms);
async function scanResearch(){
 if(cache.refreshing)return {...research,busy:true};
 cache.refreshing=true;research.attempted=0;const out=[],fail=[];const start=research.cursor;
 try{for(let i=0;i<Math.min(8,RELEASES.length);i++){
  const [repo,category]=RELEASES[(start+i)%RELEASES.length];research.attempted++;
  try{
   const resp=await fetch(`https://api.github.com/repos/${repo}/releases/latest`,{signal:timeout(7000),headers:{'accept':'application/vnd.github+json','user-agent':'MSW-Technical-Research-360','X-GitHub-Api-Version':'2022-11-28'}});
   if(!resp.ok)throw Error(`GitHub HTTP ${resp.status}`);
   const data=await resp.json();if(!/^https:\/\/github\.com\//.test(data.html_url||''))throw Error('invalid upstream link');
   out.push({repository:repo,category,release:data.name||data.tag_name,tag:data.tag_name,source_url:data.html_url,published_at:data.published_at,review:'not_moderated_do_not_auto_publish',provenance:'GitHub public releases API'});
  }catch(e){fail.push({repository:repo,error:String(e.message||e).slice(0,140)})}
 }
 research.cursor=(start+8)%RELEASES.length;research.checkedAt=now();research.results=out;research.failures=fail;research.lastRunReason='scheduled_or_authorized_check';cache.lastResearchMs=Date.now();return research;
 }finally{cache.refreshing=false;}
}
async function scanLinks(){
 const start=audit.cursor;const out=[];
 for(let i=0;i<4;i++){
  const slug=AUDIT_SLUGS[(start+i)%AUDIT_SLUGS.length];const link=`https://mobilny-serwis-warszawa.pl/${slug}/`;
  try{const rsp=await fetch(link,{signal:timeout(9000),redirect:'manual',headers:{'user-agent':'MSW-Technical-Page-Audit/1.0'}});out.push({slug,status:rsp.status,reachable:rsp.ok,checked_at:now(),url:link,signal:rsp.status===200?'html_requested':'check_redirect_or_error'})}
  catch(e){out.push({slug,status:null,reachable:false,checked_at:now(),url:link,error:String(e.message||e).slice(0,120)})}
 }
 audit.cursor=(start+4)%AUDIT_SLUGS.length;audit.checkedAt=now();audit.results=out;cache.lastAuditMs=Date.now();return audit;
}
const sources=[
 {name:'ESCO European Commission',url:'https://esco.ec.europa.eu/en/use-esco/use-esco-services-api',access:'public_classification_api',credentials_required:false},
 {name:'IBM Quantum',url:'https://quantum.cloud.ibm.com/docs/en/guides/plans-overview',access:'public_docs_qpu_requires_credentials',credentials_required:true},
 {name:'Google Quantum AI',url:'https://quantumai.google/cirq/google/access',access:'public_cirq_docs_qpu_requires_approval',credentials_required:true},
 {name:'NASA Open API',url:'https://api.nasa.gov/',access:'public_science_data_not_quantum_compute',credentials_required:false},
 {name:'PennyLane photonic simulator',url:'https://pennylane.ai/',access:'local_simulation_optional_hardware_approval',credentials_required:false}
];
function detail(item){const t=tracks.find(x=>x.slug===item.track);const lab=item.kind==='lab';return {item:{...item,content:{cel:lab?'Wykonaj powtarzalną próbę i dokumentuj realne lub syntetyczne dane':'Zrozum zasadę pracy interfejsu oraz bezpieczną diagnozę',stanowisko:t.equipment,procedura:[`Zidentyfikuj producenta, model i wersję systemu: ${item.topic}.`,'Ustal zgodę i zakres odczytów; nie wysyłaj komend sterujących.','Zachowaj znaczniki czasu, jednostki, wersje, stan początkowy i jakość danych.',`Przeprowadź etap: ${lab?BPHASE[item.phase-1]:LPHASE[item.phase-1]}.`,'Porównaj z dokumentacją producenta oraz niezależną próbką.','Sporządź raport: hipotezy, dowody, ograniczenia i zalecenia.'],bezpieczenstwo:POLICY[item.group],ocena:['Źródło i wersja urządzenia','Jakość pomiaru','Hipotezy i falsyfikacja','Bezpieczeństwo','Możliwość odtworzenia'],validity:'educational_template_not_hardware_verified'}},source:t.source};}
const bell={simulation:'analytic_Bell_state_H_then_CNOT',qubits:2,probabilities:{'00':0.5,'01':0,'10':0,'11':0.5},remote_qpu_executed:false,hardware:'NONE',method:'unitary statevector analytic educational demonstration'};
const links={wordpress:'https://mobilny-serwis-warszawa.pl/msw-technical-university/',technical:'https://mobilny-serwis-warszawa.pl/centrum-technicznych-ai/',legacy_render:'https://msw-ai-centrum-robotow.onrender.com/'};
function page(){return fs.readFileSync(path.join(DIR,'index.html'),'utf8')}
function serveStatic(res,file,type){res.writeHead(200,{'content-type':type,'cache-control':'public, max-age=300','x-content-type-options':'nosniff','x-robots-tag':'noindex, follow'});res.end(fs.readFileSync(path.join(DIR,file)))}
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url||'/', 'http://local');const p=url.pathname;
 if(req.method==='OPTIONS'){res.writeHead(204,{'access-control-allow-origin':'*','access-control-allow-methods':'GET, POST, OPTIONS','access-control-allow-headers':'Content-Type, X-MSW-Core-Key'});return res.end()}
 if(req.method==='GET'&&(p==='/'||p==='/msw-technical-university-360/'))return serveStatic(res,'index.html','text/html; charset=utf-8');
 if(req.method==='GET'&&p==='/app.js')return serveStatic(res,'app.js','text/javascript; charset=utf-8');
 if(req.method==='GET'&&p==='/style.css')return serveStatic(res,'style.css','text/css; charset=utf-8');
 if(req.method==='GET'&&(p==='/health'||p==='/api/status'))return json(res,200,{ok:true,version:'1.0.0',service:'MSW Technical Core 360',environment:'independent_render_node',counts:{families:families.length,specializations:spec.length,agent_profiles:agents.length,agents_running:0,lessons:lessons.length,labs:labs.length},lastResearch:research.checkedAt,lastLinksAudit:audit.checkedAt,research_persistent:false,model_training:false,hardware_qpu_connected:false,peer_messaging_persistent:false,wordpress_bridge:'public_read_only_when_available',links});
 if(req.method==='GET'&&p==='/api/families')return json(res,200,search(families,url.searchParams));
 if(req.method==='GET'&&p==='/api/specializations')return json(res,200,search(spec,url.searchParams));
 if(req.method==='GET'&&p==='/api/agents')return json(res,200,search(agents,url.searchParams));
 if(req.method==='GET'&&p==='/api/curriculum')return json(res,200,search([...lessons,...labs].filter(i=>{const kind=url.searchParams.get('kind');return !kind||kind==='all'||i.kind===kind}),url.searchParams));
 if(req.method==='GET'&&p==='/api/curriculum/item'){const id=url.searchParams.get('id')||'';const a=[...lessons,...labs].find(x=>x.id===id);return a?json(res,200,detail(a)):json(res,404,{error:'unknown_item'})}
 if(req.method==='GET'&&p==='/api/sources')return json(res,200,{sources,verification:'provider_access_not_claimed'});
 if(req.method==='GET'&&p==='/api/quantum/bell')return json(res,200,bell);
 if(req.method==='GET'&&p==='/api/research')return json(res,200,{...research,storage:'memory_cache_unverified_after_restart',auto_publish:false,source_links_not_individually_reviewed:true});
 if(req.method==='GET'&&p==='/api/audit/links')return json(res,200,audit);
 if(req.method==='POST'&&p==='/api/internal/refresh'){
  if(!secureRefresh(req))return json(res,403,{error:'requires_valid_server_token'});
  if(cache.refreshing)return json(res,409,{error:'refresh_in_progress'});
  const [r,a]=await Promise.all([scanResearch(),scanLinks()]);return json(res,200,{ok:true,research:{checked_at:r.checkedAt,checked:r.attempted,found:r.results.length,failed:r.failures.length},audit:{checked_at:a.checkedAt,checked:a.results.length},note:'Bibliographic queue is not published or used for model training.'});
 }
 if(req.method==='POST'&&p==='/api/peer/message')return json(res,503,{error:'persistent_authenticated_message_gateway_not_configured',wordpress_moderation_endpoint:'https://mobilny-serwis-warszawa.pl/wp-json/msw-tu360/v1/peer/message',note:'Do not accept private communications without storage and authentication.'});
 return json(res,404,{error:'unknown_route'});
});
const port=Number(process.env.PORT||10000);
server.listen(port,'0.0.0.0',()=>console.log(`MSW Technical Core 360 listening on :${port}; profiles=${agents.length} curriculum=${lessons.length+labs.length}`));
export {families,spec,agents,lessons,labs,search,detail,bell,server,scanResearch,scanLinks};

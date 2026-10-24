"use strict";
const BASE="/api/portal";
const legacy="/msw-ai-centrum-robotow/api/v1";
const $=id=>document.getElementById(id);
const state={user:null,csrf:null,selected:null,bots:[],domains:[],register:false,dbReady:false,status:null};
const escapeHTML=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=x=>typeof x==="number"?new Intl.NumberFormat("pl-PL",{maximumFractionDigits:1}).format(x):"—";
let toastTimer;
function notify(message){const el=$("toast");el.textContent=message;el.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove("show"),5000)}
function clock(){$("clock").textContent=new Date().toLocaleTimeString("pl-PL",{hour:"2-digit",minute:"2-digit",second:"2-digit"})}
async function api(route,options={}){
 const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),19000);
 try{
  const response=await fetch(BASE+route,{credentials:"same-origin",cache:"no-store",signal:ctrl.signal,...options});
  let data={};try{data=await response.json()}catch{}
  if(!response.ok){let detail=data.detail||data.error||"HTTP "+response.status;if(Array.isArray(detail))detail=detail.map(x=>x.msg).join("; ");throw Error(detail)}
  return data;
 }finally{clearTimeout(timer)}
}
const json=(obj,protectedRoute=false)=>({method:"POST",headers:{"Content-Type":"application/json",...(protectedRoute?{"X-MSW-CSRF":state.csrf||""}:{})},body:JSON.stringify(obj)});
function authModal(openRegister=false){if(!state.dbReady){notify("Konta będą dostępne po skonfigurowaniu trwałego PostgreSQL. Serwer nie może bezpiecznie przechowywać klientów w pamięci tymczasowej.");return}setTab(openRegister);$("auth-modal").showModal()}
function setTab(register){
 state.register=register;$("auth-heading").textContent=register?"Utwórz konto MSW AI":"Logowanie klienta";
 $("show-login").classList.toggle("active",!register);$("show-register").classList.toggle("active",register);
 $("auth-name-row").hidden=!register;$("auth-name").required=register;$("auth-password").autocomplete=register?"new-password":"current-password";$("auth-submit").textContent=register?"Zarejestruj bezpłatne konto":"Zaloguj się";$("auth-feedback").textContent="";
}
function jump(id){document.getElementById(id)?.scrollIntoView({behavior:"smooth"})}
function requireLogin(){if(state.user)return true;authModal();return false}
async function getStatus(){
 try{
  const p=await api("/status");state.status=p;state.dbReady=!!p.signup_ready;
  $("system-indicator").classList.toggle("ready",p.ok);$("system-indicator").innerHTML="<i></i> "+(p.ok?"Portal działa":"Baza niedostępna");
  $("count-bots").textContent=fmt(p.bot_instances);$("count-public").textContent=fmt(p.public_offers);
  $("count-clients").textContent=fmt(p.customers);$("count-orders").textContent=fmt(p.build_requests);
  $("model-state").textContent=p.ai_provider_connected?"API":"Lokalny";
  if(!p.signup_ready){$("account-status").textContent="Tryb informacyjny: baza trwała PostgreSQL jeszcze nie jest podłączona. Rejestracja i tworzenie kont wstrzymane, aby nie utracić danych po restarcie Render."}
  return p;
 }catch(err){$("system-indicator").innerHTML="<i></i> Błąd API";$("account-status").textContent="Błąd bazy lub połączenia: "+err.message;notify("Brak dostępu do statusu: "+err.message)}
}
async function getMetrics(){try{const m=await api("/metrics");$("count-runs").textContent=fmt(m.runs);$("count-arena").textContent=fmt(m.benchmarks);$("activity-score").textContent=fmt(m.runs);$("count-runs").title=m.source}catch{}}
async function getDomains(){try{const d=await api("/domains");state.domains=d.domains;for(const el of [...document.querySelectorAll(".domain-input"),$("market-domain")]){const current=el.value;const empty=el.id==="market-domain";el.replaceChildren();if(empty){const o=document.createElement("option");o.value="";o.textContent="Wszystkie specjalizacje";el.appendChild(o)}
for(const cat of d.domains){const opt=document.createElement("option");opt.value=cat.id;opt.textContent=cat.name;el.appendChild(opt)}if(current)el.value=current}
const all=d.domains;const categories=[["Elektrotechnika i energia","ELEKTRYKA"],["Automatyka i maszyny","MASZYNY"],["Smart Home i CCTV","SMART_HOME"],["Pojazdy i EV","POJAZDY"],["Hydraulika i SPA","HYDRAULIKA"]];
$("sectors").innerHTML=categories.map(([name,group],i)=>'<div class="sector-row"><div><b>'+escapeHTML(name)+'</b><span>'+escapeHTML(all.find(x=>x.id===group)?.name||"")+'</span></div><div class="sector-line"><i style="width:'+((i+1)*16)+'%"></i></div></div>').join("");
// Bars above represent category taxonomy positions, not actual usage; avoid implied percentages in UI.
}catch(err){notify("Dziedziny techniczne: "+err.message)}}
async function loadMarket(){
 const q=$("market-search").value.trim(),domain=$("market-domain").value;
 const params=new URLSearchParams({limit:30});if(q)params.set("q",q);if(domain)params.set("domain",domain);
 try{const d=await api("/market?"+params);$("market-count").textContent=fmt(d.items.length);
 $("market-cards").innerHTML=d.items.length?d.items.map(b=>'<article class="bot-card"><span>'+escapeHTML(b.domain)+' · '+escapeHTML(b.role)+'</span><strong>'+escapeHTML(b.name)+'</strong><p>'+escapeHTML(b.summary||"Agent techniczny")+'</p><span class="tag">'+(b.model_connected?"Model zewnętrzny skonfigurowany":"Lokalne narzędzia")+'</span><span class="tag">'+fmt(b.runs)+' uruchomień</span></article>').join(""):'<p class="empty">Brak publicznych ofert. Profil pojawi się po utworzeniu robota i weryfikacji właściciela.</p>';
 }catch(err){$("market-cards").innerHTML='<p class="empty">Błąd katalogu: '+escapeHTML(err.message)+'</p>'}
}
async function getMe(){try{const d=await api("/me");state.user=d.user;state.csrf=d.csrf;displayUser();return true}catch{state.user=null;state.csrf=null;displayUser();return false}}
function displayUser(){
 const logged=!!state.user;
 $("login-button").hidden=logged;$("logout-button").hidden=!logged;$("account-button").hidden=!logged;
 $("account-button").textContent=logged?"◈ "+state.user.name:"Moje konto";
 if(logged){$("account-status").textContent="Zalogowano: "+state.user.name+" · "+state.user.email+(state.user.verified?" · E-mail zweryfikowany":" · E-mail niezweryfikowany");}
 if(!logged&&state.dbReady)$("account-status").textContent="Zaloguj się, aby zarządzać własnymi robotami i zamówieniami.";
}
async function authSubmit(e){e.preventDefault();$("auth-feedback").textContent="Sprawdzanie konta…";
 try{
  const isRegister=state.register;
  const data={email:$("auth-email").value.trim(),password:$("auth-password").value};
  if(isRegister)data.name=$("auth-name").value.trim();
  const result=await api(isRegister?"/register":"/login",json(data));
  state.user=result.user;state.csrf=result.csrf;displayUser();
  $("auth-modal").close();$("auth-form").reset();
  if(isRegister&&result.message)notify(result.message);
  await Promise.allSettled([getMyBots(),getOrders(),getStatus()]);
 }catch(err){$("auth-feedback").textContent=err.message}}
async function logOut(){if(!state.csrf)return;try{await api("/logout",json({},true));state.user=null;state.csrf=null;state.bots=[];state.selected=null;displayUser();$("my-bots").innerHTML='<p class="empty">Zaloguj się, aby zobaczyć swoje roboty.</p>';$("order-history").replaceChildren();notify("Wylogowano.")}catch(e){notify(e.message)}}
async function createBot(e){
 e.preventDefault();if(!requireLogin())return;
 const f=new FormData(e.target);const obj=Object.fromEntries(f.entries());const btn=e.target.querySelector('button[type="submit"]');btn.disabled=true;
 try{const x=await api("/my/bots",json(obj,true));notify("Prywatny bot "+x.name+" został utworzony.");e.target.reset();state.selected=x.id;await Promise.allSettled([getMyBots(),getStatus()]);jump("boty")}catch(err){notify(err.message)}finally{btn.disabled=false}
}
async function getMyBots(){
 if(!state.user)return;
 try{const d=await api("/my/bots");state.bots=d.items;
 $("my-bots").innerHTML=d.items.length?d.items.map(b=>'<article class="my-bot"><span>'+escapeHTML(b.domain)+' / '+escapeHTML(b.engine)+'</span><strong>'+escapeHTML(b.name)+'</strong><p>'+escapeHTML(b.summary||"Bot techniczny, bez danych prywatnych klientów")+'</p><span class="tag">'+(b.public?"Widoczny w giełdzie":"Prywatny")+'</span><span class="tag">'+fmt(b.runs)+' wywołań</span><div class="actions"><button data-select="'+escapeHTML(b.id)+'">Wybierz do testu</button><button data-benchmark="'+escapeHTML(b.id)+'">Arena</button><button data-visible="'+escapeHTML(b.id)+'">'+(b.public?"Ukryj ofertę":"Opublikuj ofertę")+'</button></div></article>').join(""):'<p class="empty">Nie masz jeszcze botów. Przejdź do kreatora i utwórz pierwszy profil.</p>';
 $("my-bots").querySelectorAll("button[data-select]").forEach(btn=>btn.onclick=()=>selectBot(btn.dataset.select));
 $("my-bots").querySelectorAll("button[data-benchmark]").forEach(btn=>btn.onclick=()=>runArena(btn.dataset.benchmark));
 $("my-bots").querySelectorAll("button[data-visible]").forEach(btn=>btn.onclick=()=>togglePublic(btn.dataset.visible));
 if(state.selected&&!d.items.some(b=>b.id===state.selected))state.selected=null;
 if(!state.selected&&d.items.length)state.selected=d.items[0].id;
 setSelectedLabel();
 }catch(e){notify("Moje roboty: "+e.message)}
}
function selectBot(id){state.selected=id;setSelectedLabel();jump("boty");notify("Wybrano robota do diagnostyki.")}
function setSelectedLabel(){const bot=state.bots.find(x=>x.id===state.selected);$("run-bot-label").textContent=bot?"Wybrano: "+bot.name+" · "+bot.domain+" · "+bot.engine:"Wybierz lub utwórz robota."}
async function togglePublic(id){if(!requireLogin())return;const b=state.bots.find(x=>x.id===id);if(!b)return;try{await api("/my/bots/"+id+"/visibility",json({public:!b.public},true));notify("Widoczność oferty została zmieniona.");await Promise.allSettled([getMyBots(),loadMarket()])}catch(e){notify(e.message)}}
async function runBot(e){e.preventDefault();if(!requireLogin())return;if(!state.selected){notify("Wybierz bota.");return}
 const btn=e.target.querySelector("button");btn.disabled=true;$("diagnosis-output").textContent="Trwa diagnoza techniczna…";
 try{const d=await api("/my/bots/"+state.selected+"/run",json({question:$("diagnose-prompt").value,allow_paid_model:$("external-consent").checked},true));
 $("diagnosis-output").textContent=typeof d.answer==="string"?d.answer:JSON.stringify(d.answer,null,2);
 await Promise.allSettled([getMyBots(),getMetrics(),getStatus(),loadHistory()]);
 }catch(err){$("diagnosis-output").textContent="Błąd: "+err.message}finally{btn.disabled=false}
}
async function runArena(id){if(!requireLogin())return;id=id||state.selected;if(!id){notify("Wybierz własnego bota.");return}
 $("arena-result").textContent="Uruchamianie rzeczywistego benchmarku…";
 try{const d=await api("/my/bots/"+id+"/benchmark",json({},true));
 $("arena-result").textContent="Wynik: "+d.passed+" / "+d.trials+" ("+fmt(d.accuracy_percent)+"%)\nMetoda: "+d.type+"\n"+d.cases.map(c=>c.case+". "+c.expected+" → "+c.actual+" ["+(c.pass?"OK":"BŁĄD")+"]").join("\n");
 await Promise.allSettled([getArena(),getMetrics()]);
 jump("arena")
 }catch(e){$("arena-result").textContent="Błąd: "+e.message}
}
async function getArena(){try{const d=await api("/arena/overview");$("ranking-list").innerHTML=d.items.length?d.items.slice(0,10).map((x,i)=>'<div class="rank"><span>'+(i+1)+'. '+escapeHTML(x.bot)+'</span><b>'+fmt(x.accuracy_percent)+'%</b><small>'+x.passed+'/'+x.total+' poprawnych · '+x.matches+' testów</small></div>').join(""):'<p class="empty">Brak publicznych wyników Areny. Zalogowani klienci mogą wykonać swoje pierwsze testy.</p>'}catch(e){$("ranking-list").innerHTML='<p class="empty">Nie udało się odczytać rankingu.</p>'}}
async function orderSubmit(e){e.preventDefault();if(!requireLogin())return;const data=Object.fromEntries(new FormData(e.target).entries());const btn=e.target.querySelector('button[type="submit"]');btn.disabled=true;
try{const r=await api("/orders",json(data,true));notify("Zapisano zapytanie nr "+r.id.slice(0,8)+". Nie wysłano automatycznie powiadomienia — status w panelu klienta.");e.target.reset();await Promise.allSettled([getOrders(),getStatus()]);jump("konto")}catch(err){notify(err.message)}finally{btn.disabled=false}
}
async function getOrders(){if(!state.user)return;try{const d=await api("/my/orders");$("order-history").innerHTML=d.items.map(x=>'<div class="order-row"><b>'+escapeHTML(x.title)+'</b><span>'+escapeHTML(x.status)+'</span></div>').join("")||'<div class="empty">Brak zamówień.</div>'}catch(e){notify(e.message)}}
async function loadHistory(){
 try{const r=await fetch(legacy+"/timeseries?hours=24",{cache:"no-store",signal:AbortSignal.timeout(9000)});if(!r.ok)throw Error("history unavailable");const d=await r.json();drawChart(d.items||[],d.incomplete)}
 catch(err){drawChart([],false)}
}
function drawChart(items,incomplete){
 const canvas=$("activity-chart"),ctx=canvas.getContext("2d");
 const w=Math.max(300,Math.floor(canvas.clientWidth)),h=Math.max(180,Math.floor(canvas.clientHeight)),scale=Math.min(2,window.devicePixelRatio||1);
 canvas.width=w*scale;canvas.height=h*scale;ctx.setTransform(scale,0,0,scale,0,0);
 ctx.clearRect(0,0,w,h);
 const pad=28;
 ctx.strokeStyle="#174363";ctx.lineWidth=1;
 for(let i=0;i<5;i++){const y=pad+(h-pad*2)*i/4;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(w-pad,y);ctx.stroke()}
 for(let i=0;i<9;i++){const x=pad+(w-pad*2)*i/8;ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,h-pad);ctx.stroke()}
 const counts=items.map(x=>(x.gateway||0)+(x.self_report||0)),max=Math.max(1,...counts),total=counts.reduce((a,b)=>a+b,0);
 $("activity-score").textContent=fmt(total);if(!counts.some(Boolean)){ctx.fillStyle="#84b3d3";ctx.textAlign="center";ctx.font="13px system-ui";ctx.fillText("Brak zarejestrowanego ruchu w wybranym okresie",w/2,h/2);return}
 const step=(w-pad*2)/Math.max(1,counts.length-1);ctx.beginPath();ctx.strokeStyle="#27d9ff";ctx.lineWidth=2;
 counts.forEach((n,i)=>{const x=pad+i*step,y=h-pad-(n/max)*(h-pad*2);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();
 if(incomplete)notify("Historia częściowa: limit liczby obserwacji.");
}
function debounce(fn,ms=250){let t;return (...args)=>{clearTimeout(t);t=setTimeout(()=>fn(...args),ms)}}
function bind(){
 $("menu-toggle").onclick=()=>$("sidebar").classList.toggle("open");
 document.querySelectorAll('.sidebar nav a[href^="#"]').forEach(a=>a.onclick=()=>{$("sidebar").classList.remove("open");document.querySelectorAll(".sidebar nav a").forEach(x=>x.classList.remove("on"));a.classList.add("on")});
 $("login-button").onclick=()=>authModal(false);$("account-button").onclick=()=>jump("konto");$("logout-button").onclick=logOut;
 $("show-login").onclick=()=>setTab(false);$("show-register").onclick=()=>setTab(true);$("close-auth").onclick=()=>$("auth-modal").close();
 $("auth-form").onsubmit=authSubmit;$("bot-create-form").onsubmit=createBot;$("bot-run-form").onsubmit=runBot;
 $("arena-run").onclick=()=>runArena();$("order-form").onsubmit=orderSubmit;
 $("sidebar-action").onclick=()=>(state.user?jump("studio"):authModal(true));
 $("hero-create").onclick=()=>(state.user?jump("studio"):authModal(true));
 $("market-refresh").onclick=loadMarket;$("market-search").oninput=debounce(loadMarket);$("market-domain").onchange=loadMarket;
 window.addEventListener("resize",debounce(loadHistory,220));
}
async function init(){clock();setInterval(clock,1000);bind();await Promise.allSettled([getStatus(),getDomains(),getMetrics(),loadHistory(),getArena()]);await Promise.allSettled([getMe(),loadMarket()]);if(state.user)await Promise.allSettled([getMyBots(),getOrders()]);setInterval(()=>{if(document.visibilityState==="visible"){getMetrics();getStatus()}},45000)}
document.addEventListener("DOMContentLoaded",init);

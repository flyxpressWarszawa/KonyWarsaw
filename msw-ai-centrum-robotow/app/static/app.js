(() => {
  'use strict';
  const BASE = '/msw-ai-centrum-robotow';
  const API = `${BASE}/api/v1`;
  const $ = (id) => document.getElementById(id);
  const state = { overview: null, agents: [], systems: [] };
  const format = (value) => Number(value ?? 0).toLocaleString('pl-PL');
  const safe = (value) => String(value ?? '').replace(/[&<>"']/g, s => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#039;' })[s]);
  const timestamp = value => value ? new Date(value).toLocaleString('pl-PL', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'}) : 'Brak danych';
  const colors = ['#16ceef','#9479ee','#3bebaa','#3077dd','#ecad65','#e174b8','#80d1b0','#697cfa','#b9a9db','#9ac4e4','#6896af','#3e6c8c'];
  let toastTimer = null;

  function toast(message){
    $('toast').textContent = message;
    $('toast').classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('toast').classList.remove('show'), 4500);
  }
  async function get(path){
    const r = await fetch(API + path, {headers:{'Accept':'application/json'},cache:'no-store'});
    const result = await r.json().catch(()=>({detail:'Odpowiedź nie jest JSON'}));
    if (!r.ok) throw Error(typeof result.detail === 'string' ? result.detail : `Błąd ${r.status}`);
    return result;
  }
  function setConnection(online){
    $('connection').classList.toggle('offline', !online);
    $('connection').innerHTML = `<i></i> ${online ? 'API ONLINE' : 'API OFFLINE'}`;
    $('sidebar-state').textContent = online ? 'Połączono z API' : 'Brak połączenia';
  }
  function go(view){
    const target = $(view);
    if (target) target.scrollIntoView({behavior:'smooth',block:'start'});
    document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view === view));
    const names={overview:'Dashboard',exchange:'Giełda AI',telemetry:'Roboty na żywo',external:'Roboty zewnętrzne',joins:'Dołączające roboty',agents:'Agenci',providers:'Moc AI',comparison:'Porównania',systems:'API i integracje',tools:'Raporty'};
    $('current-view').textContent=names[view]||'Centrum Robotów';
    $('sidebar').classList.remove('open');
    $('menu-toggle').setAttribute('aria-expanded','false');
  }
  document.querySelectorAll('a[data-view]').forEach(node=>node.addEventListener('click', e=>{e.preventDefault();history.replaceState(null,'',`#${node.dataset.view}`);go(node.dataset.view)}));
  document.querySelectorAll('[data-go]').forEach(node=>node.addEventListener('click',()=>go(node.dataset.go)));
  $('menu-toggle').addEventListener('click',()=>{
    const open=$('sidebar').classList.toggle('open');$('menu-toggle').setAttribute('aria-expanded',String(open));
  });

  function drawTraffic(daily){
    const el=$('activity-chart');
    if (!daily || !daily.some(d=>d.gateway + d.self_report > 0)){
      el.innerHTML='<div class="empty-state">Brak danych do wykresu. Skonfiguruj gateway lub prześlij pierwsze zdarzenie agenta.</div>';return;
    }
    const max=Math.max(1,...daily.map(d=>d.gateway+d.self_report));
    const W=900,H=245,left=36,right=15,top=15,bottom=33;
    const grid=[0,.25,.5,.75,1].map(fr=>{
      const y=H-bottom-fr*(H-top-bottom);
      return `<line x1="${left}" y1="${y}" x2="${W-right}" y2="${y}" stroke="#27405a" stroke-width="1" stroke-dasharray="3,6"/><text x="${left-8}" y="${y+4}" fill="#6582a2" font-size="11" text-anchor="end">${Math.round(max*fr)}</text>`;
    }).join('');
    const space=(W-left-right)/daily.length;const bw=Math.min(50,space*.43);
    const bars=daily.map((item,i)=>{
      const x=left+space*i+space/2-bw/2, gh=item.gateway/max*(H-top-bottom), sh=item.self_report/max*(H-top-bottom);
      const baseline=H-bottom;
      const datelabel=new Date(item.date+'T12:00:00').toLocaleDateString('pl-PL',{day:'2-digit',month:'2-digit'});
      return `<rect x="${x}" y="${baseline-gh}" width="${bw}" height="${gh}" rx="3" fill="${colors[0]}" opacity=".9"><title>Gateway: ${item.gateway}</title></rect><rect x="${x}" y="${baseline-gh-sh}" width="${bw}" height="${sh}" rx="3" fill="${colors[1]}"><title>Zgłoszenia agentów: ${item.self_report}</title></rect><text x="${x+bw/2}" y="${H-9}" text-anchor="middle" fill="#7794b2" font-size="12">${datelabel}</text>`;
    }).join('');
    el.innerHTML=`<svg role="img" aria-label="Zdarzenia gateway i agentów z siedmiu dni" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${grid}${bars}</svg>`;
  }
  function drawProviders(items){
    const values=(items||[]).filter(x=>x.count>0).slice(0,12);
    const total=values.reduce((s,x)=>s+x.count,0);
    $('donut-total').textContent=format(total);
    if(!total){ $('provider-donut').style.background='#1d2a40';$('provider-list').innerHTML='<div class="empty-state compact">Brak zarejestrowanych źródeł</div>';return; }
    let start=0;
    const segments=values.map((p,i)=>{
      const next=start + p.count/total*100;
      const segment=`${colors[i]} ${start.toFixed(2)}% ${next.toFixed(2)}%`;
      start=next;return segment;
    });
    $('provider-donut').style.background=`conic-gradient(${segments.join(',')})`;
    $('provider-list').innerHTML=values.slice(0,7).map((p,i)=>`<div class="provider-item"><span><i style="background:${colors[i]}"></i>${safe(p.name)}</span><strong>${(p.count/total*100).toFixed(1).replace('.',',')}%</strong></div>`).join('');
  }
  function drawBots(items){
    const el=$('bot-leaderboard');
    if (!items || !items.length){el.innerHTML='<div class="empty-state">Nie odnotowano jeszcze ruchu botów. Wykres pozostaje pusty zamiast wyświetlać wymyślone wyniki.</div>';return;}
    const max=items[0].count || 1;
    el.innerHTML=items.slice(0,6).map((r,i)=>`<div class="leader-item"><span class="leader-rank">${String(i+1).padStart(2,'0')}</span><div class="leader-info"><div class="leader-name"><span>${safe(r.name)}</span><b>${format(r.count)}</b></div><div class="leader-track"><div class="leader-fill" style="width:${r.count/max*100}%"></div></div></div></div>`).join('');
  }
  function drawOverview(d){
    state.overview=d;const m=d.metrics;
    $('kpi24').textContent=format(m.observations_24h);
    $('kpi-agents').textContent=format(m.registered_agents);
    $('kpi-reporting').textContent=format(m.reporting_agents);
    $('kpi-gateway').textContent=format(m.gateway_observations_7d);
    $('kpi-latency').textContent=m.latency_p95_ms===null?'—':`${format(m.latency_p95_ms)} ms`;
    $('events7-label').textContent=`${format(m.observations_7d)} zdarzeń / 7 dni`;
    $('last-refresh').textContent='Aktualizacja: '+timestamp(d.generated_at);
    drawProviders(d.providers);drawBots(d.bots);
    $('ticker-agents').textContent=format(m.registered_agents);$('ticker-events').textContent=format(m.observations_24h);$('ticker-gateway').textContent=format(m.gateway_observations_7d);$('ticker-providers').textContent=format((d.providers||[]).length);
  }
  function drawAgents(){
    const q=$('agent-search').value.toLowerCase().trim();
    const rows=state.agents.filter(a=>!q||`${a.name} ${a.provider} ${a.description}`.toLowerCase().includes(q));
    $('agent-count').textContent=`${rows.length} robotów`;
    const body=$('agents-body');
    if (!rows.length){body.innerHTML=`<tr><td colspan="5"><div class="empty-state">${q?'Nie znaleziono agenta spełniającego kryteria.':'Żaden agent jeszcze się nie zarejestrował. Otwórz formularz i dodaj pierwszego robota.'}</div></td></tr>`;}
    else body.innerHTML=rows.map(a=>`<tr><td><span class="robot-name">${safe(a.name)}</span><span class="robot-provider">${safe(a.provider)}</span></td><td><span class="status-chip ${a.status==='pending'?'pending':''}">${a.status==='pending'?'Oczekuje': 'Samodeklaracja'}</span></td><td>${format(a.observations)}</td><td>${timestamp(a.last_seen)}</td><td>API własne</td></tr>`).join('');
    for(const id of ['compare-a','compare-b']){
      const sel=$(id), prev=sel.value;
      sel.innerHTML=`<option value="">${id==='compare-a'?'Robot A':'Robot B'}</option>`+state.agents.map(a=>`<option value="${safe(a.id)}">${safe(a.name)} · ${safe(a.provider)}</option>`).join('');
      if(state.agents.some(a=>a.id===prev))sel.value=prev;
    }
  }
  function drawSystem(data){
    const names={'app':'Independent API','db':'PostgreSQL / SQLite','gateway':'Gateway telemetry','registry':'Rejestr agentów'};
    const labels={'online':'Online','configured':'Skonfigurowano','needs_configuration':'Wymaga konfiguracji','offline':'Offline'};
    $('system-components').innerHTML=(data.components||[]).map((component,i)=>{
      const ok=['online','configured','not_used'].includes(component.status);
      return `<div class="module"><div class="module-head"><span class="module-icon">${['◉','▤','⌁','✕'][i%4]}</span><span class="module-status ${ok?'':'warning'}">● ${labels[component.status]||safe(component.status)}</span></div><div class="module-title">${safe(names[component.id]||component.name)}</div><div class="module-small">${ok?'Rzeczywisty status aplikacji':'Konfiguracja wymagana'}</div></div>`;
    }).join('');
  }
  async function load(){
    $('refresh-btn').disabled=true;
    try{
      const [overview,agents,system]=await Promise.all([get('/overview'),get('/agents'),get('/system')]);
      state.agents=agents.items||[];
      state.systems=system.components||[];
      drawOverview(overview);drawAgents();joiningAgents();drawSystem(system);setConnection(true);refreshMarket();
    }catch(e){setConnection(false);toast(`Nie udało się odczytać API: ${e.message}`);
      if(!state.overview){ $('activity-chart').innerHTML='<div class="empty-state">Brak połączenia z niezależnym backendem. Dane nie są symulowane.</div>'; }
    }finally{$('refresh-btn').disabled=false;}
  }
  $('refresh-btn').addEventListener('click',load);
  $('system-refresh').addEventListener('click',load);
  $('agent-search').addEventListener('input',drawAgents);
  $('compare-btn').addEventListener('click',async()=>{
    const a=$('compare-a').value,b=$('compare-b').value;
    if(!a||!b||a===b)return toast('Wybierz dwóch różnych zarejestrowanych agentów.');
    $('comparison-output').innerHTML='<div class="empty-state">Pobieranie danych pomiarowych...</div>';
    try{
      const data=await get(`/comparison?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
      $('comparison-output').innerHTML='<div class="compare-cards">'+data.items.map(agent=>`<div class="compare-card"><h4>${safe(agent.name)}</h4><p>${safe(agent.provider)}</p><dl><dt>Zdarzenia / 7 dni</dt><dd>${format(agent.observations)}</dd><dt>Opóźnienie p95</dt><dd>${agent.latency_p95_ms===null?'Brak pomiaru':format(agent.latency_p95_ms)+' ms'}</dd><dt>Status</dt><dd>${agent.status==='pending'?'Oczekuje':'Samodeklaracja'}</dd></dl></div>`).join('')+'</div>';
    }catch(e){$('comparison-output').innerHTML=`<div class="empty-state">${safe(e.message)}</div>`;}
  });
  function exportCsv(){window.location.href=API+'/export.csv';}
  $('export-csv').addEventListener('click',exportCsv);
  $('download-data').addEventListener('click',exportCsv);
  $('copy-api').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.origin+API+'/overview');toast('Skopiowano adres API.');}catch{toast('Adres API: '+location.origin+API+'/overview');}});
  $('test-health').addEventListener('click',async()=>{try{const r=await get('/health');toast(r.ok&&r.db==='connected'?'API i baza danych działają poprawnie.':'Niepełna gotowość systemu.');}catch(e){toast('Diagnostyka infrastruktury: '+e.message);}});

  const dialog=$('register-dialog');
  function showRegister(){ $('form-message').hidden=true; $('token-result').hidden=true; $('register-form').hidden=false; dialog.showModal(); }
  $('open-register').addEventListener('click',showRegister);
  $('join-agent').addEventListener('click',showRegister);
  $('close-register').addEventListener('click',()=>dialog.close());
  $('register-form').addEventListener('submit',async e=>{
    e.preventDefault();const f=e.currentTarget, submit=$('submit-register');
    submit.disabled=true; submit.textContent='Rejestrowanie...'; $('form-message').hidden=true;
    try{
      const form=new FormData(f);
      const payload={name:String(form.get('name')).trim(),provider:String(form.get('provider')).trim(),description:String(form.get('description')||'').trim(),endpoint:String(form.get('endpoint')||'').trim()||null};
      const r=await fetch(API+'/agents/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const data=await r.json();
      if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Nie udało się zarejestrować agenta.');
      $('register-form').hidden=true;$('token-result').hidden=false;
      $('token-value').textContent=data.agent_token;
      $('created-agent-id').textContent='Agent ID: '+data.agent.id;
      f.reset();load();
    }catch(err){$('form-message').textContent=err.message;$('form-message').hidden=false;}
    finally{submit.disabled=false;submit.textContent='Zarejestruj i wygeneruj token →';}
  });
  $('copy-token').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText($('token-value').textContent);toast('Token skopiowany. Przechowuj go bezpiecznie.');}
    catch{toast('Nie można automatycznie skopiować. Zaznacz token ręcznie.');}
  });

  // Market widgets: actual gateway observations and authenticated self-reported events.
  let marketHours = 24, marketMode = 'line', marketData = null;
  function marketChart(d) {
    marketData = d;
    const root = $('activity-chart');
    const rows = d.items || [];
    const maximum = Math.max(0, ...rows.map(v => v.gateway + v.self_report));
    const fmtDate = stamp => new Date(stamp).toLocaleString('pl-PL',
      d.period_hours <= 6 ? { hour:'2-digit', minute:'2-digit' } :
      d.period_hours === 24 ? {hour:'2-digit', minute:'2-digit'} : {day:'2-digit', month:'2-digit',hour:'2-digit'});
    $('range-label').textContent=`Ostatnie ${d.period_hours === 168 ? '7 dni' : d.period_hours+' h'} · ${d.bucket_minutes} min / interwał`;
    const warning = d.incomplete ? '<p class="series-warning">Przekroczono limit 100 000 obserwacji. Wykres niekompletny.</p>' : '';
    if (!maximum) {
      root.innerHTML=`<div class="grid-empty"><span class="grid-empty-lines" aria-hidden="true"></span><strong>OCZEKIWANIE NA POMIARY</strong><small>W tym okresie nie zarejestrowano zdarzeń. Gdy robot prześle dane, wykres pojawi się automatycznie.</small></div>`;
      return;
    }
    const width=1040, height=318, left=51, right=24, top=24, bottom=45;
    const plotW=width-left-right,plotH=height-top-bottom;
    const m=Math.max(1,maximum);
    const xx=i=>left + plotW * (i/(Math.max(1,rows.length-1)));
    const yy=v=>top + plotH * (1-v/m);
    const grid=[0,.25,.5,.75,1].map(t => `<g><line x1="${left}" y1="${yy(m*t)}" x2="${width-right}" y2="${yy(m*t)}" stroke="#174367" stroke-dasharray="2 6"/><text x="${left-10}" y="${yy(m*t)+4}" font-size="12" text-anchor="end" fill="#80a4c7">${Math.round(m*t)}</text></g>`).join('');
    const xticks=rows.map((r,i)=>[r,i]).filter(([r,i])=>i===0||i===rows.length-1||i % Math.max(1,Math.ceil(rows.length/7))===0).map(([r,i])=>`<text x="${xx(i)}" y="${height-15}" font-size="12" text-anchor="middle" fill="#80a4c7">${fmtDate(r.timestamp)}</text>`).join('');
    const renderSeries=(key,color)=>{
      const path=rows.map((r,i)=>`${i?'L':'M'}${xx(i).toFixed(2)},${yy(r[key]).toFixed(2)}`).join(' ');
      const bg=`${path} L${xx(rows.length-1)},${yy(0)} L${xx(0)},${yy(0)}Z`;
      return `<path d="${bg}" fill="${color}" opacity=".09"/><path d="${path}" fill="none" stroke="${color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>`;
    };
    let series;
    if(marketMode==='line')series=renderSeries('gateway','#00caff')+renderSeries('self_report','#ba77ff');
    else{
      const band=Math.max(4,plotW/rows.length*.66);
      series=rows.map((r,i)=>{
        const x=xx(i)-band/2, gw=r.gateway/m*plotH, sh=r.self_report/m*plotH;
        return `<rect x="${x.toFixed(2)}" y="${(yy(0)-gw).toFixed(2)}" width="${band.toFixed(2)}" height="${gw.toFixed(2)}" fill="#00caff" rx="2"><title>Gateway ${r.gateway} · ${fmtDate(r.timestamp)}</title></rect><rect x="${x.toFixed(2)}" y="${(yy(0)-gw-sh).toFixed(2)}" width="${band.toFixed(2)}" height="${sh.toFixed(2)}" fill="#b28aff" rx="2"><title>Agenci ${r.self_report} · ${fmtDate(r.timestamp)}</title></rect>`;
      }).join('');
    }
    const hoverPoints=rows.map((r,i)=>`<circle cx="${xx(i).toFixed(2)}" cy="${yy(r.gateway).toFixed(2)}" r="7" fill="transparent"><title>${fmtDate(r.timestamp)} · ruch gateway: ${r.gateway} · raporty agentów: ${r.self_report}</title></circle>`).join('');
    root.innerHTML=`${warning}<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Interaktywny wykres liczby zdarzeń HTTP i agentów; obserwacje, nie wynik finansowy">${grid}${series}${xticks}${hoverPoints}</svg>`;
  }
  async function refreshMarket(){
    try{const d=await get(`/timeseries?hours=${marketHours}`);marketChart(d)}
    catch(err){ $('activity-chart').innerHTML=`<div class="empty-state">${safe(err.message)} — nie można pobrać wykresu.</div>`; }
  }
  document.querySelectorAll('[data-period]').forEach(button=>button.addEventListener('click',()=>{
    marketHours=Number(button.dataset.period);
    document.querySelectorAll('[data-period]').forEach(b=>b.classList.toggle('active',b===button));
    refreshMarket();
  }));
  $('chart-mode').addEventListener('change', e=>{
    marketMode=e.target.value;if(marketData)marketChart(marketData);
  });
  function joiningAgents(){
    const rows=state.agents.slice(0,15), target=$('joins-body');
    $('joined-count').textContent=`${format(state.agents.length)} zarejestrowanych robotów`;
    if(!rows.length){target.innerHTML='<tr><td colspan="6"><div class="empty-state">Nie ma jeszcze zewnętrznych rejestracji. Dołącz własnego robota, otrzymasz token API i pojawisz się tutaj.</div></td></tr>';return;}
    target.innerHTML=rows.map((a,i)=>{
      const active=a.status==='self_reported';
      return `<tr><td>${i+1}</td><td><span class="robot-name">${safe(a.name)}</span></td><td>${safe(a.provider)}</td><td><span class="status-chip ${active?'':'pending'}">${active?'Raportuje':'Zarejestrowany'}</span></td><td>${format(a.observations)}</td><td>${timestamp(a.created_at)}</td></tr>`;
    }).join('');
  }
  ['side-join','external-join','hero-join'].forEach(id=>$(id).addEventListener('click',showRegister));
  $('global-search').addEventListener('keydown', e=>{
    if(e.key==='Enter'){ $('agent-search').value=e.target.value;go('agents');drawAgents(); }
  });
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){
      e.preventDefault();$('global-search').focus();
    }
  });

  $('year').textContent=$('footer-year').textContent=new Date().getFullYear();
  load();setInterval(()=>{if(document.visibilityState==='visible')load();},30000);
  const hash=location.hash.replace('#','');if(hash)setTimeout(()=>go(hash),300);
})();

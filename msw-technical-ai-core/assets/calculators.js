(function(root){'use strict';
 const funcs={
  "ohm-v":(p)=>(p.i*p.r),
  "ohm-i":(p)=>(p.u/p.r),
  "ohm-r":(p)=>(p.u/p.i),
  "power-dc":(p)=>(p.u*p.i),
  "power-1phase":(p)=>(p.u*p.i*p.pf),
  "power-3phase":(p)=>(Math.sqrt(3)*p.u*p.i*p.pf),
  "energy-kwh":(p)=>(p.kw*p.h),
  "energy-cost":(p)=>(p.kwh*p.price),
  "cable-drop-dc":(p)=>(2*0.0175*p.m*p.i/p.a),
  "cable-drop-3phase":(p)=>(Math.sqrt(3)*0.0175*p.m*p.i/p.a),
  "wire-resistance":(p)=>(0.0175*p.m/p.a),
  "copper-temperature":(p)=>(p.r20*(1+0.00393*(p.temp-20))),
  "apparent-power":(p)=>(p.u*p.i),
  "reactive-power":(p)=>(p.w*Math.tan(Math.acos(p.pf))),
  "ups-runtime":(p)=>(p.wh*p.eff/p.w),
  "divider":(p)=>(p.u*p.r2/(p.r1+p.r2)),
  "rc-tau":(p)=>(p.r*p.c/1000000),
  "rc-cutoff":(p)=>(1/(2*Math.PI*p.r*p.c*1e-9)),
  "lc-resonance":(p)=>(1/(2*Math.PI*Math.sqrt(p.l*1e-3*p.c*1e-9))),
  "rc-charge":(p)=>(p.u*(1-Math.exp(-p.t/(p.r*p.c*1e-6)))),
  "capacitor-energy":(p)=>(0.5*p.c*1e-6*p.v*p.v),
  "inductor-energy":(p)=>(0.5*p.l*1e-3*p.i*p.i),
  "adc-lsb":(p)=>(p.vref/(Math.pow(2,p.bits)-1)),
  "pwm-mean":(p)=>(p.v*p.d/100),
  "frequency-from-period":(p)=>(1000/p.ms),
  "duty-cycle":(p)=>(100*p.ton/p.period),
  "mosfet-loss":(p)=>(p.i*p.i*p.r/1000),
  "db-voltage":(p)=>(20*Math.log10(p.v2/p.v1)),
  "db-power":(p)=>(10*Math.log10(p.p2/p.p1)),
  "rpm-frequency":(p)=>(60*p.hz/p.ppr),
  "surface-speed":(p)=>(Math.PI*p.d*p.rpm/60),
  "motor-torque":(p)=>(60*p.w/(2*Math.PI*p.rpm)),
  "motor-power":(p)=>(2*Math.PI*p.rpm*p.nm/60),
  "gear-speed":(p)=>(p.rpm/p.ratio),
  "gear-torque":(p)=>(p.nm*p.ratio*p.eff),
  "hydraulic-power":(p)=>(p.bar*p.lpm/600),
  "hydraulic-force":(p)=>(p.bar*1e5*Math.PI*Math.pow(p.mm/1000,2)/4),
  "stroke-time":(p)=>(p.mm/p.mms),
  "rotation-hz":(p)=>(p.rpm/60),
  "link-mbps":(p)=>(p.mbps*p.eff/8),
  "transfer-time":(p)=>(p.gb*8000/p.mbps),
  "fiber-loss":(p)=>(p.km*p.dbkm+p.connect+p.splice),
  "mw-to-dbm":(p)=>(10*Math.log10(p.mw)),
  "dbm-to-mw":(p)=>(Math.pow(10,p.dbm/10)),
  "snr":(p)=>(p.signal-p.noise),
  "poe-budget":(p)=>(p.psu-p.used),
  "packets-pps":(p)=>(p.mbps*1e6/(p.bytes*8)),
  "ipv4-hosts":(p)=>(Math.pow(2,32-p.prefix)),
  "mqtt-data":(p)=>(p.bytes*p.rate*86400/1e6),
  "accuracy":(p)=>(100*(p.tp+p.tn)/(p.tp+p.tn+p.fp+p.fn)),
  "precision":(p)=>(100*p.tp/(p.tp+p.fp)),
  "recall":(p)=>(100*p.tp/(p.tp+p.fn)),
  "f1-score":(p)=>(100*2*p.tp/(2*p.tp+p.fp+p.fn)),
  "inference-rate":(p)=>(p.n/p.sec),
  "availability":(p)=>(100*p.up/p.total),
  "downtime":(p)=>(p.total*(1-p.pct/100)),
  "queue-backlog":(p)=>(Math.max(0,p.incoming-p.processed)*p.seconds),
  "gpu-energy":(p)=>(p.w*p.h/1000),
  "nyquist-min":(p)=>(2*p.hz),
  "sampling-storage":(p)=>(p.hz*p.bytes*p.sec/1e6),
  "cop":(p)=>(p.q/p.pin),
 };
 function calculate(id,input){
  if(!Object.prototype.hasOwnProperty.call(funcs,id))throw new Error('Nieznany wzór');
  const p={};for(const [k,v] of Object.entries(input)){
    const n=Number(v);if(!Number.isFinite(n)||Math.abs(n)>1e11)throw new Error('Nieprawidłowa wartość: '+k);p[k]=n;
  }
  // Zero is valid for some quantities. Enforce positive denominators independently.
  if('pf' in p && (p.pf<=0||p.pf>1))throw new Error('cosφ musi należeć do zakresu (0,1]');
  if('eff' in p && (p.eff<=0||p.eff>1))throw new Error('Sprawność musi należeć do zakresu (0,1]');
  if('d' in p && id==='pwm-mean' && (p.d<0||p.d>100))throw new Error('Wypełnienie 0–100%');
  if('pct' in p && (p.pct<0||p.pct>100))throw new Error('Dostępność 0–100%');
  if('prefix' in p && (p.prefix<0||p.prefix>32||!Number.isInteger(p.prefix)))throw new Error('Prefiks CIDR 0–32 (liczba całkowita)');
  if('bits' in p && (p.bits<1||p.bits>32||!Number.isInteger(p.bits)))throw new Error('Rozdzielczość ADC 1–32 bity');
  if('ton' in p&&'period' in p&&p.ton>p.period)throw new Error('Ton nie może przekraczać T');
  const positiveKeys={
    'ohm-i':['r'],'ohm-r':['i'],'reactive-power':['pf'],'ups-runtime':['w'],
    'rc-cutoff':['r','c'],'rc-charge':['r','c'],'lc-resonance':['l','c'],
    'db-voltage':['v1','v2'],'db-power':['p1','p2'],'rpm-frequency':['ppr'],'motor-torque':['rpm'],
    'gear-speed':['ratio'],'transfer-time':['mbps'],'mw-to-dbm':['mw'],'packets-pps':['bytes'],
    'availability':['total'],'inference-rate':['sec'],
    'cop':['pin'],'stroke-time':['mms'],'frequency-from-period':['ms'],'duty-cycle':['period'],'adc-lsb':['vref']
  };
  if((positiveKeys[id]||[]).some(k=>!(p[k]>0)))throw new Error('Wartości dzielnika i parametrów logarytmu muszą być dodatnie');
  if(id==='divider'&&p.r1+p.r2<=0)throw new Error('Suma rezystancji >0');
  if(id==='accuracy'&&p.tp+p.tn+p.fp+p.fn<=0)throw new Error('Przynajmniej jeden przypadek');
  if(id==='f1-score'&&2*p.tp+p.fp+p.fn<=0)throw new Error('Brak przypadków pozytywnych');
  if(id==='precision'&&p.tp+p.fp<=0)throw new Error('TP+FP musi być dodatnie');
  if(id==='recall'&&p.tp+p.fn<=0)throw new Error('TP+FN musi być dodatnie');
  if(id==='fiber-loss'&&Object.values(p).some(x=>x<0))throw new Error('Straty muszą być nieujemne');
  if(['cable-drop-dc','cable-drop-3phase','wire-resistance'].includes(id)&&p.a<=0)throw new Error('Przekrój >0');
  if(['rc-tau','inductor-energy','capacitor-energy'].includes(id)&&Object.values(p).some(x=>x<0))throw new Error('Elementy muszą być nieujemne');
  if(id==='motor-torque'&&p.rpm<=0)throw new Error('RPM > 0');
  if('up' in p&&'total' in p&&p.up>p.total)throw new Error('Czas online nie może przekraczać okresu');
  let result=funcs[id](p);
  if(!Number.isFinite(result)||Math.abs(result)>1e17)throw new Error('Wynik poza zakresem lub dzielenie przez zero');
  return result;
 }
 const api={calculate,ids:Object.keys(funcs)};root.MSW3_CALCS=api;
 if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);

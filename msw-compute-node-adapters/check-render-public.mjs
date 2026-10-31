// One-shot deployment verification; not a recurring keepalive or authenticated pairing.
import assert from 'node:assert/strict';
const base='https://msw-ai-gielda-technologii.onrender.com';
const started=new Date().toISOString();
const results=[];
async function read(path,opts={}){
 const t=performance.now();const response=await fetch(base+path,{...opts,redirect:'error',signal:AbortSignal.timeout(90000)});
 const raw=await response.text();if(raw.length>65536)throw Error('response_bound');
 const body=JSON.parse(raw);results.push({path,http:response.status,ms:Math.round(performance.now()-t)});return {response,body};
}
try{
 let {response,body}=await read('/health');assert.equal(response.status,200);assert.equal(body.ok,true);
 ({response,body}=await read('/api/msw-compute-node/v1/manifest'));assert.equal(response.status,200);assert.equal(body.version,'0.1.1');assert.equal(body.node_id,'msw-ai-gielda-technologii');assert.equal(body.native_broker_adapter_verified,false);
 const enabled=body.execution_enabled;
 ({response,body}=await read('/api/msw-compute-node/v1/execute',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}));assert.ok([401,503].includes(response.status));
 console.log(JSON.stringify({schema:'msw.render.public-readiness.v1',started,completed:new Date().toISOString(),service:base,passed:true,checks:results,execution_enabled:enabled,credentials_used:false,wordpress_pairing_verified:false,remote_compute_job_executed:false}));
}catch(e){console.log(JSON.stringify({schema:'msw.render.public-readiness.v1',started,completed:new Date().toISOString(),service:base,passed:false,checks:results,error:String(e?.message||e).slice(0,180),wordpress_pairing_verified:false,remote_compute_job_executed:false}));process.exitCode=1;}

import assert from 'node:assert/strict';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
const enabled=process.argv[2]==='enabled';
if(!['enabled','disabled'].includes(process.argv[2]))throw Error('mode_required');
const key='test-fixture-not-a-production-credential-123456';
process.env.PORT='0';
if(enabled){process.env.MSW_COMPUTE_NODE_ENABLED='1';process.env.MSW_COMPUTE_NODE_TOKEN=key;}
else{delete process.env.MSW_COMPUTE_NODE_ENABLED;delete process.env.MSW_COMPUTE_NODE_TOKEN;}
// Test the repository's real market app, but block its public research network calls.
const nativeFetch=globalThis.fetch;
globalThis.fetch=(url,opts)=>{const u=new URL(String(url));if(u.hostname!=='127.0.0.1')throw Error('external_network_disabled_in_test');return nativeFetch(url,opts);};
let app;let checks=0;
const eq=(a,b)=>{assert.deepEqual(a,b);checks++;};
try{
 app=await import('../msw-ai-technical-core/market/standalone.mjs');
 if(!app.server.listening)await once(app.server,'listening');
 const base='http://127.0.0.1:'+app.server.address().port;
 const get=p=>fetch(base+p,{redirect:'manual',signal:AbortSignal.timeout(3000)});
 let r=await get('/health');eq(r.status,200);eq((await r.json()).ok,true);
 r=await get('/');eq(r.status,302);eq(r.headers.get('location'),'/gielda-technologii-ai/');await r.text();
 r=await get('/gielda-technologii-ai/');eq(r.status,200);await r.text();
 r=await get('/api/msw-compute-node/v1/manifest');eq(r.status,200);const m=await r.json();eq(m.version,'0.1.1');eq(m.node_id,'msw-ai-gielda-technologii');eq(m.execution_enabled,enabled);eq(m.native_broker_adapter_verified,false);
 const raw=JSON.stringify({schema:'msw.compute.request.v1',request_id:'integration-0001',operation:'stats.aggregate',arguments:{values:[1,2,3]}});
 const post=auth=>fetch(base+'/api/msw-compute-node/v1/execute',{method:'POST',headers:{'content-type':'application/json',...(auth?{authorization:auth}:{})},body:raw,signal:AbortSignal.timeout(3000)});
 r=await post(null);eq(r.status,enabled?401:503);await r.text();
 r=await post('Bearer '+key);eq(r.status,enabled?200:503);const result=await r.json();
 if(enabled){eq(result.result,{count:3,sum:6,min:1,max:3,mean:2});eq(result.request_sha256,createHash('sha256').update(raw).digest('hex'));eq(result.native_engine_ack,false);eq(result.durable_receipt,false);eq(result.central_pairing_verified,false);}
 console.log(JSON.stringify({schema:'msw.compute.integration-test.v1',mode:process.argv[2],checks,passed:true,environment:'repository_app_local_http_with_synthetic_inputs',production_pairing:false}));
 app.server.closeAllConnections();await new Promise(resolve=>app.server.close(resolve));process.exit(0);
}catch(e){console.error('Integration test failed:',e?.code||e?.message||'unknown');if(app?.server)app.server.closeAllConnections();process.exit(1);}

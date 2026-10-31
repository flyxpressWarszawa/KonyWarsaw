import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import {Executor,Denied,createHandler} from './compute-node.mjs';
const key='local-test-only-not-a-production-credential-1234',auth='Bearer '+key;
const body=(operation='stats.aggregate',args={values:[1,2,3]},extra={})=>Buffer.from(JSON.stringify({schema:'msw.compute.request.v1',request_id:'local-test-0001',operation,arguments:args,...extra}));
const run=(raw,a=auth)=>new Executor('test-node',key,true).execute(raw,a);
test('numeric aggregate',()=>assert.deepEqual(run(body()).result,{count:3,sum:6,min:1,max:3,mean:2}));
test('unicode metrics',()=>assert.deepEqual(run(body('text.metrics',{text:'Zażółć 🧠 test'})).result,{utf8_bytes:20,codepoints:13,whitespace_tokens:3}));
test('challenge receipt',()=>assert.equal(run(body('node.probe',{challenge:'a'.repeat(64)})).result.challenge_sha256.length,64));
test('unauthenticated denied',()=>assert.throws(()=>run(body(),'Bearer wrong'),e=>e.status===401));
test('disabled denied',()=>assert.throws(()=>new Executor('test-node',key).execute(body(),auth),e=>e.status===503));
test('unknown operation and fields denied',()=>{assert.throws(()=>run(body('shell',{})));assert.throws(()=>run(body('stats.aggregate',{values:[1]}, {url:'https://example.invalid'})));});
test('invalid values denied',()=>{for(const v of [[],[true],['1'],[1e20],Array.from({length:1001},(_,i)=>i)])assert.throws(()=>run(body('stats.aggregate',{values:v})));});
test('noncanonical or malformed JSON denied',()=>{assert.throws(()=>run(Buffer.from('{"a":1,"a":2}')));assert.throws(()=>run(Buffer.from('{} ')));});
test('oversize denied',()=>assert.throws(()=>run(Buffer.alloc(32769)),e=>e.status===413));
test('rate limit',()=>{const e=new Executor('test-node',key,true);for(let i=0;i<6;i++)e.execute(body(),auth);assert.throws(()=>e.execute(body(),auth),x=>x.status===429);});
test('proof not overstated',()=>{const r=run(body());assert.equal(r.durable_receipt,false);assert.equal(r.native_engine_ack,false);assert.equal(r.central_pairing_verified,false);});
test('real local HTTP handler',async()=>{process.env.MSW_COMPUTE_NODE_TOKEN=key;process.env.MSW_COMPUTE_NODE_ENABLED='1';const handler=createHandler('test-node');const s=http.createServer(async(req,res)=>{if(!await handler(req,res,new URL(req.url,'http://localhost'))){res.writeHead(404);res.end();}});await new Promise(resolve=>s.listen(0,'127.0.0.1',resolve));try{const url='http://127.0.0.1:'+s.address().port+'/api/msw-compute-node/v1/';assert.equal((await fetch(url+'manifest')).status,200);assert.equal((await fetch(url+'execute',{method:'POST',body:body()})).status,401);const r=await fetch(url+'execute',{method:'POST',headers:{authorization:auth,'content-type':'application/json'},body:body()});assert.equal(r.status,200);assert.equal((await r.json()).result.sum,6);}finally{await new Promise(resolve=>s.close(resolve));}});

test('unpaired surrogate rejected',()=>assert.throws(()=>run(body('text.metrics',{text:'\ud800'})),e=>e.code==='text_utf8'));
test('manifest contains no credential',()=>{const m=new Executor('test-node',key,true).manifest();assert.equal(m.version,'0.1.1');assert.equal(JSON.stringify(m).includes(key),false);});

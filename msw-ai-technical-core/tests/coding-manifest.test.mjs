import {test,after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {once} from 'node:events';
import {buildCodingManifest,readCodingManifest} from '../coding-manifest.mjs';

const file=new URL('../../msw-coding-bridge/targets.json',import.meta.url);
const source=JSON.parse(fs.readFileSync(file,'utf8'));
process.env.PORT='0';
process.env.RENDER_GIT_COMMIT='a'.repeat(40);
process.env.MSW_MANIFEST_PRIVATE_TEST='secret-must-never-appear';
const {server}=await import('../server.mjs');
if(!server.listening)await once(server,'listening');
const base='http://127.0.0.1:'+server.address().port;
after(()=>new Promise(resolve=>server.close(resolve)));

test('HTTP manifest exposes seven routes and PR3 without claiming live execution',async()=>{
 const r=await fetch(base+'/api/coding/manifest');assert.equal(r.status,200);const j=await r.json();
 assert.equal(Object.keys(j.targets).length,7);assert.equal(j.targets.runtime.branch,'msw/engine-runtime-20261104');
 assert.equal(j.verification.connections,'not_verified');assert.equal(j.runtime.commit,'a'.repeat(40));
 assert.equal(j.verification_adapter.enabled_by_default,false);assert.equal(j.production_hold,true);
 assert.equal(j.verification_adapter.pull_request,'https://github.com/flyxpressWarszawa/KonyWarsaw/pull/3');
 assert.ok(!JSON.stringify(j).includes('secret-must-never-appear'));
});
test('request arguments never choose a file, commit or authority',async()=>{
 const r=await fetch(base+'/api/coding/manifest?file=/etc/passwd&commit=bad&enable=true');
 const j=await r.json();assert.equal(j.runtime.commit,'a'.repeat(40));assert.equal(j.production_hold,true);
});
test('manifest does not accept writes or alternate paths',async()=>{
 for(const method of ['POST','PUT','PATCH','DELETE'])assert.equal((await fetch(base+'/api/coding/manifest',{method})).status,404);
 assert.equal((await fetch(base+'/api/coding/manifest/')).status,404);
});
test('runtime commit requires the complete Git identifier',()=>{
 for(const value of [undefined,'abc123','z'.repeat(40),'a'.repeat(41),' a'.repeat(20)])assert.equal(buildCodingManifest(source,value).runtime.commit,null);
 assert.equal(buildCodingManifest(source,'A'.repeat(40)).runtime.commit,'a'.repeat(40));
});
test('unknown private configuration is omitted at every projected level',()=>{
 const s=structuredClone(source);s.secret='private';s.targets.runtime.secret='private';s.targets.runtime.deployment.token='private';s.python_hub.key='private';
 assert.ok(!JSON.stringify(buildCodingManifest(s)).includes('private'));
});
test('invalid routing cannot create a misleading valid manifest',()=>{
 const changes=[s=>s.repository='other/repo',s=>s.queue_branch='main',s=>delete s.targets.runtime,
 s=>s.targets.runtime.branch='../main',s=>s.targets.runtime.paths=['../secret/'],
 s=>s.targets.runtime.deployment.url='https://example.com/?token=private',s=>s.python_hub.url='https://other.example/'];
 for(const change of changes){const s=structuredClone(source);change(s);assert.throws(()=>buildCodingManifest(s));}
});
test('unavailable manifest returns bounded 503 and health still responds',async()=>{
 const read=fs.readFileSync;
 try{fs.readFileSync=(p,...args)=>{if(String(p)===String(file))throw new Error('private-filesystem-path');return read(p,...args)};
  assert.throws(()=>readCodingManifest());const r=await fetch(base+'/api/coding/manifest');assert.equal(r.status,503);
  assert.deepEqual(await r.json(),{error:'coding_manifest_unavailable',connections_verified:false});
  assert.equal((await fetch(base+'/health')).status,200);
 }finally{fs.readFileSync=read;}
});

import fs from 'node:fs';

const FILE = new URL('../msw-coding-bridge/targets.json', import.meta.url);
const NAMES = ['technical-core','portal','compute-adapters','research','runtime','owned-llm','wordpress'];
const SUPPORT = new Set(['github_agent_scope','chatgpt_branch_worker','source_sync_required']);
const need = condition => { if (!condition) throw new Error('Invalid coding manifest'); };
const object = value => { need(value !== null && typeof value === 'object' && !Array.isArray(value)); return value; };
const text = (value, max=200) => { need(typeof value === 'string' && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/u.test(value)); return value; };
function branch(value) {
  text(value);
  need(/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(value) && !value.includes('..') && !value.includes('//'));
  need(value.split('/').every(p=>p && !p.startsWith('.') && !p.endsWith('.') && !p.endsWith('.lock')));
  return value;
}
function path(value) {
  text(value);
  need(/^(?:[A-Za-z0-9_-][A-Za-z0-9._-]*\/)+$/.test(value));
  need(value.split('/').every(p=>p!=='.' && p!=='..'));
  return value;
}
function url(value) {
  text(value,300); const u=new URL(value);
  need(u.protocol==='https:' && !u.username && !u.password && !u.search && !u.hash && !u.port);
  return value;
}
function target(value,name) {
  const t=object(value),d=object(t.deployment);
  need(SUPPORT.has(t.execution_support) && Array.isArray(t.paths) && t.paths.length<=8);
  need(t.branch===null ? name==='wordpress' && t.paths.length===0 && t.execution_support==='source_sync_required' : t.paths.length>0);
  const deployment={provider:text(d.provider,80)};
  if(Object.hasOwn(d,'service'))deployment.service=text(d.service,100);
  if(Object.hasOwn(d,'url'))deployment.url=url(d.url);
  for(const k of ['auto_deploy_observed','live_target_requires_verification'])if(Object.hasOwn(d,k)){need(typeof d[k]==='boolean');deployment[k]=d[k];}
  return {branch:t.branch===null?null:branch(t.branch),paths:t.paths.map(path),execution_support:t.execution_support,deployment};
}

// Only explicitly public fields are returned; private additions never pass through.
export function buildCodingManifest(value,renderCommit) {
  const s=object(value),ts=object(s.targets),hub=object(s.python_hub);
  need(s.schema==='msw.coding-targets.v1' && s.repository==='flyxpressWarszawa/KonyWarsaw' && s.repository_id===405190198);
  need(s.queue_branch==='msw/coding-queue' && s.task_directory==='msw-coding-tasks');
  need(s.wordpress_bridge==='https://mobilny-serwis-warszawa.pl/wp-json/msw-github-coding/v1');
  need(Object.keys(ts).length===NAMES.length && NAMES.every(n=>Object.hasOwn(ts,n)));
  need(hub.url==='https://mobilny-serwis-warszawa.pl/msw-backend' && hub.adapter==='msw-ai-hub/msw-ai-hub.php');
  need(hub.source_in_repository===false && hub.source_required_before_backend_change===true);
  const commit=typeof renderCommit==='string' && /^[a-f0-9]{40}$/i.test(renderCommit)?renderCommit.toLowerCase():null;
  return {schema:'msw.coding-manifest.v1',protocol:'msw.coding-task.v1',status:'proposed_not_agreed_not_deployed',
    status_scope:'coding_bridge_contract',read_only:true,production_hold:true,
    runtime:{service:'msw-technical-core-360',commit,commit_source:commit?'RENDER_GIT_COMMIT':null},
    verification:{routing:'validated_static_manifest',connections:'not_verified',autonomous_coding:'not_verified',production_effects:'not_verified'},
    repository:s.repository,repository_id:s.repository_id,queue_branch:s.queue_branch,task_directory:s.task_directory,
    wordpress_bridge:s.wordpress_bridge,connection_lifetime:'persistent',
    targets:Object.fromEntries(NAMES.map(n=>[n,target(ts[n],n)])),
    python_hub:{url:hub.url,adapter:hub.adapter,source_in_repository:false,source_required_before_backend_change:true},
    verification_adapter:{pull_request:'https://github.com/flyxpressWarszawa/KonyWarsaw/pull/3',enabled_by_default:false,deployed_verified:false}};
}

export function readCodingManifest() {
  const info=fs.statSync(FILE);need(info.isFile() && info.size<=65536);
  const raw=fs.readFileSync(FILE,'utf8');need(Buffer.byteLength(raw,'utf8')<=65536);
  return buildCodingManifest(JSON.parse(raw),process.env.RENDER_GIT_COMMIT);
}

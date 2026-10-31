/** Bounded, stateless MSW CPU adapter. No scheduler, HTTP client, files or models. */
import {createHash,timingSafeEqual} from 'node:crypto';
const sha=x=>createHash('sha256').update(x).digest('hex');
const OPS=['stats.aggregate','text.metrics','node.probe'];
const keys=(o,list)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).length===list.length&&list.every(k=>Object.hasOwn(o,k));
export class Denied extends Error{constructor(code,status=400){super(code);this.code=code;this.status=status;}}
export class Executor{
 constructor(nodeId,secret='',enabled=false){if(!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(nodeId))throw Error('node_id');this.nodeId=nodeId;this.secret=secret;this.enabled=enabled;this.calls=[];}
 manifest(){return {schema:'msw.compute-node.v1',version:'0.1.0',node_id:this.nodeId,central_origin:'https://mobilny-serwis-warszawa.pl',execution_enabled:this.enabled&&this.secret.length>=32,operations:OPS,queue_owner:'existing_wordpress_compute_mesh',native_broker_adapter_verified:false,model_inference:false,durable_receipt:false,max_request_bytes:32768,max_calls_per_minute_per_process:6};}
 authenticate(auth){if(!this.enabled||this.secret.length<32)throw new Denied('node_not_enabled',503);if(typeof auth!=='string'||auth.length>512||!timingSafeEqual(Buffer.from(sha('Bearer '+this.secret),'hex'),Buffer.from(sha(auth),'hex')))throw new Denied('unauthorized',401);}
 execute(raw,auth){
  this.authenticate(auth);if(!Buffer.isBuffer(raw)||raw.length>32768)throw new Denied('request_too_large',413);
  let text,d;try{text=new TextDecoder('utf-8',{fatal:true}).decode(raw);d=JSON.parse(text);}catch{throw new Denied('invalid_json');}
  // Strict canonical wire form also rejects duplicate keys and alternative encodings.
  if(JSON.stringify(d)!==text)throw new Denied('noncanonical_json');
  if(!keys(d,['schema','request_id','operation','arguments']))throw new Denied('request_schema');
  if(d.schema!=='msw.compute.request.v1'||typeof d.request_id!=='string'||!/^[A-Za-z0-9_-]{8,80}$/.test(d.request_id))throw new Denied('request_identity');
  const a=d.arguments;let result;
  if(d.operation==='stats.aggregate'){
   if(!keys(a,['values'])||!Array.isArray(a.values)||a.values.length<1||a.values.length>1000||a.values.some(x=>typeof x!=='number'||!Number.isFinite(x)||Math.abs(x)>1e12))throw new Denied('values_schema');
   const v=a.values,sum=v.reduce((s,x)=>s+x,0);result={count:v.length,sum,min:Math.min(...v),max:Math.max(...v),mean:sum/v.length};
  }else if(d.operation==='text.metrics'){
   if(!keys(a,['text'])||typeof a.text!=='string')throw new Denied('text_schema');
   if(!a.text.isWellFormed())throw new Denied('text_utf8');const size=Buffer.byteLength(a.text);
   if(size>16384)throw new Denied('text_size',413);
   result={utf8_bytes:size,codepoints:[...a.text].length,whitespace_tokens:a.text.trim()?a.text.trim().split(/\s+/u).length:0};
  }else if(d.operation==='node.probe'){
   if(!keys(a,['challenge'])||typeof a.challenge!=='string'||!/^[a-f0-9]{64}$/.test(a.challenge))throw new Denied('challenge_schema');
   result={challenge_sha256:sha(a.challenge)};
  }else throw new Denied('operation_not_allowed');
  const now=performance.now();this.calls=this.calls.filter(t=>t>now-60000);if(this.calls.length>=6)throw new Denied('rate_limit',429);this.calls.push(now);
  return {schema:'msw.compute.result.v1',node_id:this.nodeId,request_id:d.request_id,operation:d.operation,result,request_sha256:sha(raw),execution:'local_cpu',durable_receipt:false,native_engine_ack:false,customer_data_saved:false,central_pairing_verified:false};
 }
}
export function createHandler(nodeId){
 const e=new Executor(nodeId,process.env.MSW_COMPUTE_NODE_TOKEN||'',process.env.MSW_COMPUTE_NODE_ENABLED==='1');
 const reply=(res,status,body)=>{res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(JSON.stringify(body));};
 return async(req,res,url)=>{
  if(!url.pathname.startsWith('/api/msw-compute-node/v1/'))return false;
  try{
   if(req.method==='GET'&&url.pathname==='/api/msw-compute-node/v1/manifest'){reply(res,200,e.manifest());return true;}
   if(req.method!=='POST'||url.pathname!=='/api/msw-compute-node/v1/execute')throw new Denied('not_found',404);
   e.authenticate(req.headers.authorization);
   if((req.headers['content-type']||'').split(';',1)[0].toLowerCase()!=='application/json')throw new Denied('content_type',415);
   if((req.headers['content-encoding']||'identity')!=='identity')throw new Denied('content_encoding',415);
   req.setTimeout(5000,()=>req.destroy());
   const parts=[];let size=0;for await(const part of req){size+=part.length;if(size>32768)throw new Denied('request_too_large',413);parts.push(part);}
   reply(res,200,e.execute(Buffer.concat(parts),req.headers.authorization));
  }catch(err){if(!res.headersSent)reply(res,err instanceof Denied?err.status:400,{error:err instanceof Denied?err.code:'request_failed'});}
  return true;
 };
}

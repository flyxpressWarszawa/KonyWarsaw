const base=(process.env.MSW_CORE_BASE_URL||'https://msw-technical-core-360.onrender.com').replace(/\/$/,'');
const token=process.env.MSW_CORE_REFRESH_TOKEN||'';
if(token.length<30){console.error('BLOCKED: refresh token not configured');process.exit(2)}
try{const r=await fetch(base+'/api/internal/refresh',{method:'POST',headers:{'x-msw-core-key':token},signal:AbortSignal.timeout(60000)});const data=await r.json();console.log(JSON.stringify({status:r.status,ok:r.ok,research:data.research||null,audit:data.audit||null,note:data.note||data.error}));if(!r.ok)process.exitCode=1}
catch(e){console.error('Research refresh failed:',e.message);process.exitCode=1}

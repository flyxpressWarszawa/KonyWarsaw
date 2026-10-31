import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createMarketHandler} from './server.mjs';
import {createHandler} from '../../msw-compute-node-adapters/compute-node.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const families=JSON.parse(fs.readFileSync(path.join(HERE,'../data/families.json'),'utf8'));
const research={checkedAt:null,results:[],failures:[],source:'Verified public GitHub releases API (allowlisted, low rate)',cursor:0,attempts:0};
const audit={checkedAt:null,results:[],source:'Not automatically scanned without explicit allowlist'};
const repos=[
 ['home-assistant/core','Inteligentne domy'],['esphome/esphome','IoT / ESP32'],
 ['Koenkk/zigbee2mqtt','Zigbee'],['openhab/openhab-core','Systemy BMS'],
 ['ros2/ros2','Robotyka'],['ros-controls/ros2_control','Mechatronika'],
 ['open62541/open62541','OPC UA'],['zephyrproject-rtos/zephyr','Embedded'],
 ['espressif/esp-idf','Mikrokontrolery'],['OpenEMS/openems','Energia'],
 ['hardbyte/python-can','Diagnostyka CAN'],['qiskit/qiskit','Obliczenia kwantowe'],
 ['quantumlib/Cirq','Symulacje kwantowe'],['PennyLaneAI/pennylane','Fotonika']
];
let scanning=false;
async function scanPublicReleases(){
 if(scanning)return;
 scanning=true;
 try{
  const old=new Map(research.results.map(x=>[x.repository,x]));
  const failures=[];const start=research.cursor;
  for(let i=0;i<2;i++){
   const [repository,category]=repos[(start+i)%repos.length];
   try{
    const resp=await fetch('https://api.github.com/repos/'+repository+'/releases/latest',{signal:AbortSignal.timeout(11000),headers:{'Accept':'application/vnd.github+json','User-Agent':'MSW-Technical-Exchange/1.0','X-GitHub-Api-Version':'2022-11-28'},redirect:'error'});
    if(!resp.ok)throw Error('HTTP '+resp.status);
    const item=await resp.json();const link=String(item.html_url||'');
    if(!link.startsWith('https://github.com/'+repository+'/releases/'))throw Error('invalid_release_link');
    old.set(repository,{repository,category,release:String(item.name||item.tag_name).slice(0,110),tag:String(item.tag_name||'').slice(0,65),source_url:link,published_at:item.published_at,review:'metadata_only_not_auto_published'});
   }catch(e){failures.push({repository,error:String(e.message||e).slice(0,120)})}
  }
  research.cursor=(start+2)%repos.length;
  research.checkedAt=new Date().toISOString();research.attempts+=2;research.failures=failures;research.results=[...old.values()];
 }finally{scanning=false}
}
const market=createMarketHandler({families,research,audit});
const compute=createHandler('msw-ai-gielda-technologii');
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url||'/', 'http://127.0.0.1');const p=url.pathname;
 if(req.method==='GET'&&p==='/'){res.writeHead(302,{Location:'/gielda-technologii-ai/','cache-control':'no-store'});res.end();return}
 if(req.method==='GET'&&p==='/health'){res.writeHead(200,{'content-type':'application/json; charset=utf-8'});res.end(JSON.stringify({ok:true,service:'MSW AI Giełda Technologii',version:'1.0.0',research_checked_at:research.checkedAt,external_agent_processes:0,source_sync:'in_memory_allowlisted'}));return}
 try{if(await compute(req,res,url))return;if(await market(req,res,url))return;res.writeHead(404,{'content-type':'application/json'});res.end(JSON.stringify({error:'not_found'}));}
 catch(e){console.error('Portal server error:',String(e?.message||e).slice(0,120));if(!res.headersSent){res.writeHead(500,{'content-type':'application/json'});res.end('{"error":"server_error"}')}else res.end()}
});
const port=Number(process.env.PORT||10000);
server.listen(port,'0.0.0.0',()=>console.log('MSW AI Technical Exchange server active on :'+port));
setTimeout(()=>scanPublicReleases().catch(()=>{}),2500);
setInterval(()=>scanPublicReleases().catch(()=>{}),10*60*1000);
export {server,market,families,scanPublicReleases,research};

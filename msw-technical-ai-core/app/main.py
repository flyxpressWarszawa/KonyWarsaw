"""MSW Technical AI Core v5: read-only educational API; no control of physical systems."""
import gzip
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads(gzip.decompress((ROOT / 'data' / 'university.json.gz').read_bytes()))
RESEARCH = ROOT / 'data' / 'research_candidates.json'
INDEX = DATA['index']
PROFILE_LOOKUP = {a['id']: a for a in DATA['agents']}
SPEC_LOOKUP = {a['id']: a for a in DATA['taxonomy']}
ITEMS = {}
for track in DATA['track_documents'].values():
    for kind in ('lessons', 'labs'):
        for row in track.get(kind, []):
            ITEMS[row['id']] = row
assert len(INDEX) == 2800 and len(ITEMS) == 2800, 'Source curriculum incomplete'
assert len(DATA['taxonomy']) == 1000 and len(DATA['agents']) == 1000, 'Taxonomy incomplete'
assert len(DATA['tools']) == 61, 'Technical calculator catalog incomplete'

app = FastAPI(title='MSW Technical AI Core', version='5.0.0',
              description='Read-only technical university, 1000 editorial work specializations, 2800 learning materials and 61 calculators; not a model weight-training platform')
app.add_middleware(CORSMiddleware, allow_origins=[
    'https://mobilny-serwis-warszawa.pl',
    'https://www.mobilny-serwis-warszawa.pl',
    'https://msw-ai-centrum-robotow.onrender.com'], allow_credentials=False,
    allow_methods=['GET'], allow_headers=['Accept', 'Content-Type'])
app.add_middleware(GZipMiddleware, minimum_size=1200)

WP_BASE = 'https://mobilny-serwis-warszawa.pl'
ENGINE_ROUTES = [
    ('Technical University 1.0', '/wp-json/msw-university/v1/health'),
    ('Technical University 360', '/wp-json/msw-tu360/v1/status'),
    ('Neural Suite', '/wp-json/msw-neural/v1/status'),
    ('Python Hub', '/wp-json/msw-hub/v1/status'),
    ('Unified Bridge', '/wp-json/msw-unified/v1/health'),
]
PROVIDERS = [
 {'name':'IBM Quantum','framework':'Qiskit / local simulator', 'hardware_connected':False,
  'requirements':'IBM Cloud Quantum service instance and a private API key; credentials not provisioned',
  'url':'https://quantum.cloud.ibm.com/docs/en/guides/initialize-account'},
 {'name':'Google Quantum AI','framework':'Cirq / local simulator', 'hardware_connected':False,
  'requirements':'Google approval, Quantum Engine project and credentials',
  'url':'https://quantumai.google/cirq/google/access'},
 {'name':'Photonic computing','framework':'PennyLane simulators', 'hardware_connected':False,
  'requirements':'Approved hardware/provider account; simulator is not remote photonic QPU',
  'url':'https://www.pennylane.ai/devices'},
 {'name':'NASA','framework':'Public scientific datasets, not quantum computing', 'hardware_connected':False,
  'requirements':'NASA does not provide a general publicly accessible quantum computer for this engine',
  'url':'https://api.nasa.gov/'},
]
LINKS = [
    '/centrum-technicznych-ai/', '/gielda-technologii-ai/', '/arena-diagnostyki-ai/',
    '/kreator-agentow-technicznych-ai/', '/ai-w-inteligentnym-domu/',
    '/ai-diagnostyka-pojazdow/', '/ai-automatyka-maszyn/', '/ai-energia-ev-i-hvac/',
    '/ai-integracje-i-api/', '/katalog-agentow-technicznych/', '/sklep-modulow-ai-iot/',
    '/projekty-inzynierii-ai/', '/monitoring-botow-i-systemow/',
    '/baza-wiedzy-technicznej-ai/', '/ai-automatyzacja-dla-firm/',
    '/msw-technical-university/', '/msw-technical-university-program-nauki-ai/',
    '/msw-ai-centrum-robotow/',
]
_checked_at = 0.0
_check_cache = None

def page_result(rows, page, page_size):
    total = len(rows)
    start = (page - 1) * page_size
    return {'total':total, 'page':page, 'per_page':page_size,
            'pages':(total + page_size - 1) // page_size, 'items':rows[start:start + page_size]}

def latest_research():
    try:
        r = json.loads(RESEARCH.read_text(encoding='utf-8'))
        if not isinstance(r.get('items'), list): raise ValueError('Bad list')
        return r
    except (ValueError, OSError, TypeError):
        return {'updated_utc':None, 'status':'unavailable', 'items':[]}

@app.get('/', include_in_schema=False)
def home():
    return FileResponse(ROOT / 'assets' / 'index.html', media_type='text/html',headers={'Cache-Control':'public, max-age=300'})

@app.get('/assets/calculators.js', include_in_schema=False)
def calculator_js():
    return FileResponse(ROOT / 'assets' / 'calculators.js',media_type='text/javascript',headers={'Cache-Control':'public, max-age=86400'})

@app.get('/api/v1/status')
def status():
    return {'ok': True, 'version':'5.0.0', 'framework':'FastAPI standalone Render',
            'mode':'read_only_technical_knowledge', 'source':'versioned_editorial_MSW_dataset',
            'faculties':4, 'families':len(DATA['families']),
            'operational_specializations':len(DATA['taxonomy']),
            'static_agent_profiles':len(DATA['agents']), 'running_agents_proven':0,
            'lessons':sum(x['kind']=='lesson' for x in INDEX),
            'laboratories':sum(x['kind']=='lab' for x in INDEX),
            'learning_tracks':len(DATA['tracks']), 'technical_calculators':len(DATA['tools']),
            'approved_external_materials':'not_linked_to_moderation_database',
            'remote_device_control':False, 'external_model_weights_trained':False,
            'quantum_hardware_connected':False, 'research_candidates':len(latest_research()['items']),
            'wordpress_prod_modified':False, 'daily_ingestion':'github_actions_scheduled_not_verified_until_first_run'}

@app.get('/api/v1/families')
def families():
    return {'items':DATA['families'], 'total':len(DATA['families'])}

@app.get('/api/v1/specializations')
def specializations(q:str='',group:str='',page:int=Query(1,ge=1,le=1000),per_page:int=Query(25,ge=1,le=100)):
    q=q[:90].casefold().strip();group=group[:30].strip()
    rows = DATA['taxonomy']
    if group: rows = [a for a in rows if a.get('macro_domain') == group]
    if q: rows = [a for a in rows if q in ' '.join(str(a.get(k,'')) for k in ('title','family','subfield','mode')).casefold()]
    return page_result(rows,page,per_page)

@app.get('/api/v1/specializations/{sid}')
def specialization(sid:str):
    if sid not in SPEC_LOOKUP: raise HTTPException(404,'Specialization not found')
    return {'item':SPEC_LOOKUP[sid], 'verified_individual_hardware_expertise':False}

@app.get('/api/v1/agents')
def agent_profiles(q:str='',page:int=Query(1,ge=1,le=1000),per_page:int=Query(20,ge=1,le=100)):
    rows=DATA['agents'];q=q[:90].strip().casefold()
    if q: rows = [a for a in rows if q in (a.get('name','')+' '+a.get('family','')).casefold()]
    result=page_result(rows,page,per_page)
    result.update({'profiles_are_running':False, 'messaging_requires_authenticated_WordPress_moderation':True})
    return result

@app.get('/api/v1/curriculum/tracks')
def tracks():
    return {'items':DATA['tracks'], 'total':len(DATA['tracks'])}

@app.get('/api/v1/curriculum')
def curriculum(kind:str=Query('all',pattern='^(all|lesson|lab)$'), q:str='',track:str='', page:int=Query(1,ge=1,le=1000),per_page:int=Query(20,ge=1,le=100)):
    rows=INDEX
    if kind!='all':rows=[x for x in rows if x['kind']==kind]
    if track:rows=[x for x in rows if x['track']==track]
    if q:
        query=q[:90].casefold()
        rows=[x for x in rows if query in (x['title']+' '+x['topic']).casefold()]
    return page_result(rows,page,per_page)

@app.get('/api/v1/curriculum/{item_id}')
def curriculum_detail(item_id:str):
    if not re.fullmatch(r'[LB]-[a-z0-9-]{2,80}-\d{2}-\d{2}',item_id):
        raise HTTPException(422,'Invalid item id')
    item=ITEMS.get(item_id)
    if item is None:raise HTTPException(404,'Item not found')
    return {'item':item, 'provenance':'MSW original editorial structured curriculum',
            'does_not_prove_hardware_training':True,'model_weights_updated':False}

@app.get('/api/v1/tools')
def tools_catalog(sector:str='',page:int=Query(1,ge=1,le=100),per_page:int=Query(61,ge=1,le=61)):
    rows=DATA['tools'];
    if sector:rows=[x for x in rows if x.get('sector','')==sector[:50]]
    result=page_result(rows,page,per_page)
    result['execution']='browser_calculators.js_with_input_validation'
    return result

@app.get('/api/v1/research')
def research(page:int=Query(1,ge=1,le=100),per_page:int=Query(20,ge=1,le=100)):
    d=latest_research()
    return {**page_result(d['items'],page,per_page), 'updated_utc':d.get('updated_utc'),
            'status':d.get('status','unverified'),
            'policy':'official_reference_links_ONLY_not_automatic_material_approval'}

@app.get('/api/v1/quantum/providers')
def quantum_providers():
    return {'providers':PROVIDERS,'remote_hardware_jobs_submitted':0,
            'simulators_not_remote_hardware':True}

@app.get('/api/v1/quantum/bell')
def bell_state():
    return {'device':'local_analytic_simulation', 'circuit':'H(q0);CNOT(q0,q1);measure',
            'qubits':2, 'probabilities':{'00':0.5,'01':0.0,'10':0.0,'11':0.5},
            'hardware_execution':False,'provider':'local_cpu_analytic','paid_cloud_access':False}

@app.get('/api/v1/engines')
def connected_engines():
    global _checked_at, _check_cache
    now=time.monotonic()
    if _check_cache is not None and now - _checked_at < 300:
        return {'items':_check_cache,'checked_at_cache':'within_last_5_minutes', 'note':'HTTP presence is not device or AI inference validation'}
    rows=[]
    for name,path in ENGINE_ROUTES:
        uri=WP_BASE+path
        try:
            req=Request(uri,headers={'User-Agent':'MSW-Technical-AI-Core/5.0 (+read-only)', 'Accept':'application/json'})
            with urlopen(req,timeout=4) as r:
                data=r.read(1200)
                rows.append({'name':name,'url':uri,'http_code':r.status,
                             'response_is_json':data.strip().startswith(b'{'),
                             'status':'http_responsive_not_validated'})
        except HTTPError as e:
            rows.append({'name':name,'url':uri,'http_code':e.code,'status':'http_error'})
        except (URLError,TimeoutError,OSError,ValueError) as e:
            rows.append({'name':name,'url':uri,'http_code':None,'status':'unreachable_or_blocked'})
    _check_cache=rows;_checked_at=now
    return {'items':rows, 'note':'Public endpoints only. HTTP 200 does not prove model or physical integrations.'}

@app.get('/api/v1/paths')
def published_navigation():
    return {'site':WP_BASE,'known_links':[WP_BASE+p for p in LINKS],
            'each_link_verified_live':False, 'original_ai_arena_kept':True}

@app.get('/.well-known/msw-agent.json', include_in_schema=False)
def agent_manifest():
    return {'name':'MSW Technical AI Core','version':'5.0.0','agent_entry':'not_a_running_agent',
            'manifest_spec':'MSW proprietary technical index', 'openapi':'/openapi.json',
            'permissions':['read:educational_catalogue','read:source_links'],
            'write_capability':False, 'device_control':False,
            'contact':'https://mobilny-serwis-warszawa.pl/ai-integracje-i-api/'}

@app.get('/llms.txt',include_in_schema=False)
def llms():
    return PlainTextResponse('# MSW Technical AI Core\n' 
       'Public read-only technical learning catalogues: 1000 MSW operational specializations, 1200 lessons, 1600 labs and 61 calculator definitions.\n'
       'Use /openapi.json for API routes. No raw customer data, no physical control, no verified model training.\n'
       'Sources proposed by research ingestion are unreviewed until explicitly approved.\n')

@app.get('/robots.txt', include_in_schema=False)
def robots():
    return PlainTextResponse('User-agent: *\nAllow: /\n')

import importlib
import sys
from pathlib import Path
from datetime import datetime, timezone
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.main import app, DATA, ITEMS
import research_collect as rc
client=TestClient(app)

def test_index_integrity():
 assert len(DATA['families'])==40
 assert len(DATA['taxonomy'])==1000
 assert len(DATA['agents'])==1000
 assert len(DATA['index'])==2800
 assert len(ITEMS)==2800
 assert sum(x['kind']=='lesson' for x in DATA['index'])==1200
 assert sum(x['kind']=='lab' for x in DATA['index'])==1600
 assert len(DATA['tools'])==61
 assert all('content' in it and it['content'].get('dowody') for it in list(ITEMS.values())[:100])

def test_status_is_honest():
 r=client.get('/api/v1/status');assert r.status_code==200
 x=r.json();assert x['operational_specializations']==1000
 assert x['running_agents_proven']==0
 assert x['quantum_hardware_connected'] is False
 assert x['remote_device_control'] is False

def test_filter_and_pagination():
 r=client.get('/api/v1/specializations?group=DOMY&per_page=12&page=2');assert r.status_code==200
 d=r.json();assert d['total']>12 and len(d['items'])==12
 assert all(x['macro_domain']=='DOMY' for x in d['items'])
 r=client.get('/api/v1/curriculum?kind=lab&per_page=17');assert r.status_code==200
 d=r.json();assert d['total']==1600 and len(d['items'])==17

def test_detail_uses_full_source():
 lesson_id=DATA['index'][0]['id']
 r=client.get('/api/v1/curriculum/'+lesson_id);assert r.status_code==200
 j=r.json();assert j['item']['content']['dowody']
 assert j['does_not_prove_hardware_training'] is True
 assert client.get('/api/v1/curriculum/foobar').status_code==422
 assert client.get('/api/v1/curriculum/L-aaaaaaaaa-99-99').status_code==404

def test_calculators_and_assets():
 r=client.get('/api/v1/tools');assert r.status_code==200
 assert r.json()['total']==61
 assert client.get('/assets/calculators.js').status_code==200
 assert client.get('/').status_code==200

def test_quantum_sim_not_hardware():
 r=client.get('/api/v1/quantum/bell');assert r.status_code==200
 d=r.json();assert abs(sum(d['probabilities'].values())-1.0)<1e-12
 assert d['hardware_execution'] is False
 x=client.get('/api/v1/quantum/providers').json()
 assert all(not p['hardware_connected'] for p in x['providers'])

def test_agents_not_live_and_readonly_manifest():
 r=client.get('/api/v1/agents');assert r.json()['profiles_are_running'] is False
 assert r.json()['total']==1000
 assert client.post('/api/v1/agents',json={'device':'test'}).status_code==405
 assert client.get('/.well-known/msw-agent.json').json()['device_control'] is False

def test_research_feed_parser_and_dedupe():
 source='https://github.com/Qiskit/qiskit/releases.atom'
 feed=b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
 <entry><title>Qiskit test release</title><published>2026-10-24T10:00:00Z</published>
 <link href="https://github.com/Qiskit/qiskit/releases/tag/v-test"/></entry>
 <entry><title>Injected link</title><link href="https://example.com/evil"/></entry></feed>'''
 d=rc.parse_atom(feed,'Qiskit Releases',source,'quantum');assert len(d)==1
 assert d[0]['review_status']=='unreviewed_public_source_reference'
 original=rc.SOURCES
 try:
  rc.SOURCES=[('Qiskit Releases',source,'quantum')]
  a=rc.collect([],getter=lambda u:feed,day=datetime(2026,10,24,tzinfo=timezone.utc))
  b=rc.collect(a['items'],getter=lambda u:feed,day=datetime(2026,10,25,tzinfo=timezone.utc))
  assert a['new_links']==1 and b['new_links']==0 and len(b['items'])==1
 finally: rc.SOURCES=original

def test_paths_are_known_and_research_zero_safe():
 d=client.get('/api/v1/paths').json();assert d['each_link_verified_live'] is False
 assert d['original_ai_arena_kept'] is True
 r=client.get('/api/v1/research'); assert r.status_code==200
 assert r.json()['total']>=0

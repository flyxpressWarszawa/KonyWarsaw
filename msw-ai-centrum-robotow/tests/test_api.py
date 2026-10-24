"""Contract smoke tests using isolated SQLite data, never simulated production metrics."""
import os
import sys
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# DB is isolated to the test folder before module import.
DB = Path(tempfile.gettempdir()) / f"msw_robot_api_tests_{os.getpid()}.sqlite"
os.environ['DATABASE_URL'] = f'sqlite:///{DB}'
os.environ['INGEST_SECRET'] = 'automated-test-secret-longer-than-24-characters'
from app.main import app, BASE, Base, ENGINE  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=ENGINE)
    Base.metadata.create_all(bind=ENGINE)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=ENGINE)


def test_home_serves_standalone_no_repairs(client):
    r = client.get(BASE + '/')
    assert r.status_code == 200
    assert 'Centrum Robotów' in r.text
    assert 'wordpress' not in r.text.lower()
    assert 'Zrób zdjęcie usterki' not in r.text
    assert '/wp-content/' not in r.text
    assert '/wp-json/' not in r.text


def test_health_and_empty_metrics_are_real_zero(client):
    r=client.get(BASE + '/api/v1/health')
    assert r.status_code == 200 and r.json()['wordpress_dependency'] is False
    data=client.get(BASE + '/api/v1/overview').json()
    assert data['metrics']['observations_24h']==0
    assert data['bots']==[]
    assert data['metrics']['latency_p95_ms'] is None


def register(client, name):
    r=client.post(BASE + '/api/v1/agents/register',json={'name':name,'provider':'Independent AI','description':'An agent'})
    assert r.status_code == 201, r.text
    return r.json()


def test_register_token_not_public_and_event_authed(client):
    robot=register(client,'Test Robot')
    assert robot['agent_token'] not in str(client.get(BASE+'/api/v1/agents').json())
    id_=robot['agent']['id']
    path=BASE+f'/api/v1/agents/{id_}/events'
    assert client.post(path,json={'category':'task','latency_ms':145}).status_code==401
    assert client.post(path,headers={'Authorization':'Bearer '+robot['agent_token']},json={'category':'task','latency_ms':145}).status_code==202
    assert client.get(BASE+'/api/v1/agents').json()['items'][0]['status']=='self_reported'
    assert client.get(BASE+'/api/v1/overview').json()['metrics']['observations_7d']==1


def test_gateway_requires_secret_and_ignores_nonbot(client):
    path=BASE+'/api/v1/events/ingest'
    bot={'user_agent':'Mozilla/5.0 GPTBot/1.0','http_status':200,'latency_ms':40}
    assert client.post(path,json=bot).status_code==401
    h={'Authorization':'Bearer '+os.environ['INGEST_SECRET']}
    assert client.post(path,json={'user_agent':'Mozilla/5.0 (human)'},headers=h).json()['accepted'] is False
    assert client.post(path,json=bot,headers=h).json()['accepted'] is True
    d=client.get(BASE+'/api/v1/overview').json()
    assert d['bots'][0]['name']=='GPTBot'
    assert d['metrics']['gateway_observations_7d']==1


def test_compare_and_csv(client):
    a=register(client,'Agent Alpha'); b=register(client,'Agent Beta')
    for agent in (a,b):
        assert client.post(BASE+f"/api/v1/agents/{agent['agent']['id']}/events",
                           headers={'Authorization':'Bearer '+agent['agent_token']},
                           json={'category':'task','latency_ms':300}).status_code==202
    r=client.get(BASE+'/api/v1/comparison',params={'a':a['agent']['id'],'b':b['agent']['id']})
    assert r.status_code==200
    assert [x['observations'] for x in r.json()['items']]==[1,1]
    csv_=client.get(BASE+'/api/v1/export.csv')
    assert csv_.status_code==200 and 'Agent Alpha' in csv_.text


def test_reject_internal_endpoint(client):
    r=client.post(BASE+'/api/v1/agents/register',json={'name':'Internal','provider':'AI', 'endpoint':'http://localhost/private'})
    assert r.status_code==422


def test_csv_escapes_untrusted_formula(client):
    robot=register(client,'=HYPERLINK("https://evil.test")')
    path=BASE+f"/api/v1/agents/{robot['agent']['id']}/events"
    assert client.post(path,headers={'Authorization':'Bearer '+robot['agent_token']},
                       json={'category':'task'}).status_code==202
    data=client.get(BASE+'/api/v1/export.csv').text
    assert "'=HYPERLINK" in data
    assert robot['agent_token'] not in data


def test_timeseries_empty_and_real_gateway_event(client):
    base = BASE + '/api/v1/timeseries'
    assert client.get(base).status_code == 200
    empty = client.get(base, params={'hours':24}).json()
    assert len(empty['items']) == 24 and all(not x['gateway'] for x in empty['items'])
    assert client.get(base,params={'hours':2}).status_code == 422
    h={'Authorization':'Bearer '+os.environ['INGEST_SECRET']}
    assert client.post(BASE+'/api/v1/events/ingest',headers=h,json={'user_agent':'GPTBot/1.0'}).status_code == 202
    live=client.get(base,params={'hours':1}).json()
    assert sum(row['gateway'] for row in live['items']) == 1
    assert live['metric'] == 'observed_event_count_not_market_price_or_ai_power'


def test_join_ui_has_interactive_market_and_external_menu(client):
    html=client.get(BASE+'/').text
    assert 'Giełda i centrum' in html
    assert 'Roboty zewnętrzne' in html
    assert 'Dołączające roboty' in html
    assert 'id="joins-body"' in html
    assert 'data-period="24"' in html

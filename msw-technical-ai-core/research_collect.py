"""Daily automatic ingestion of *bibliographic candidates* into a versioned GitHub file.

All items are unreviewed (technical correctness, licensing, and model training require independent approval).
This script is used by GitHub Actions, not by site visitors; it never contacts private customer systems.
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'research_candidates.json'
SOURCES = [
 ('Qiskit Releases','https://github.com/Qiskit/qiskit/releases.atom','quantum'),
 ('Cirq Releases','https://github.com/quantumlib/Cirq/releases.atom','quantum'),
 ('PennyLane Releases','https://github.com/PennyLaneAI/pennylane/releases.atom','photonics'),
 ('Home Assistant','https://github.com/home-assistant/core/releases.atom','smart_home'),
 ('ESPHome','https://github.com/esphome/esphome/releases.atom','smart_home'),
 ('Zephyr RTOS','https://github.com/zephyrproject-rtos/zephyr/releases.atom','embedded'),
 ('Espressif IDF','https://github.com/espressif/esp-idf/releases.atom','embedded'),
 ('open62541 OPC UA','https://github.com/open62541/open62541/releases.atom','industrial'),
 ('ROS 2','https://github.com/ros2/ros2/releases.atom','robotics'),
 ('ros2_control','https://github.com/ros-controls/ros2_control/releases.atom','robotics'),
 ('OpenEMS','https://github.com/OpenEMS/openems/releases.atom','energy'),
 ('Zigbee2MQTT','https://github.com/Koenkk/zigbee2mqtt/releases.atom','smart_home'),
 ('OpenHAB','https://github.com/openhab/openhab-core/releases.atom','smart_home'),
 ('Paho MQTT','https://github.com/eclipse-paho/paho.mqtt.python/releases.atom','iot'),
 ('python-can','https://github.com/hardbyte/python-can/releases.atom','automotive'),
 ('MicroPython','https://github.com/micropython/micropython/releases.atom','electronics'),
]
TRUSTED_HOSTS = {'github.com'}
ET.register_namespace('', 'http://www.w3.org/2005/Atom')
A='{http://www.w3.org/2005/Atom}'

def safe_text(v, limit=200):
    return re.sub(r'\s+', ' ', ''.join(c for c in str(v or '') if c.isprintable())).strip()[:limit]

def eligible_link(link, source_url):
    if not isinstance(link,str) or len(link)>500: return False
    parsed=urlparse(link)
    parent=urlparse(source_url)
    # Only public HTTPS GitHub release links belonging to this exact publisher/repo.
    if parsed.scheme!='https' or (parsed.hostname or '').lower()!='github.com': return False
    if parsed.username or parsed.password or parsed.fragment or parsed.port or parsed.query: return False
    parent_path=parent.path.split('/releases.atom')[0]
    return parsed.path.startswith(parent_path + '/releases/')

def parse_atom(xml_data, source_name, source_url, category):
    root=ET.fromstring(xml_data)
    if root.tag!=A+'feed': raise ValueError('Expected Atom feed')
    results=[]
    for entry in root.findall(A+'entry')[:6]:
        title=safe_text(entry.findtext(A+'title'),180)
        stamp=safe_text(entry.findtext(A+'published') or entry.findtext(A+'updated'),40)
        links=[n.attrib.get('href','') for n in entry.findall(A+'link')]
        url=next((x for x in links if eligible_link(x,source_url)),None)
        if not url or len(title)<6:continue
        did=hashlib.sha256(url.encode('utf-8')).hexdigest()
        results.append({'id':did,'title':title,'url':url,'source':source_name,
          'category':category,'published':stamp,'review_status':'unreviewed_public_source_reference',
          'customer_data':False,'auto_trained_model':False})
    return results

def fetch(url):
    req=Request(url, headers={'User-Agent':'MSW Technical University Research/5.0 (+MSW public research index)',
                              'Accept':'application/atom+xml'})
    with urlopen(req, timeout=10) as response:
        if response.geturl()!=url:raise ValueError('Redirect is not permitted')
        ct=response.headers.get('Content-Type','')
        if 'xml' not in ct and 'atom' not in ct:raise ValueError('Non-XML content')
        raw=response.read(210000)
        if len(raw)>200000:raise ValueError('Feed too large')
        return raw

def collect(existing, getter=fetch, day=None):
    day=day or datetime.now(timezone.utc)
    seen={it['id']:it for it in existing if isinstance(it,dict) and len(str(it.get('id','')))==64}
    selected=SOURCES[(day.timetuple().tm_yday*6)%len(SOURCES):] + SOURCES[:(day.timetuple().tm_yday*6)%len(SOURCES)]
    errors=[];added=0
    for name,url,category in selected[:6]:
        try:
            data=parse_atom(getter(url),name,url,category)
            for item in data:
                if item['id'] not in seen:
                    seen[item['id']]={**item,'first_seen_utc':day.isoformat(timespec='seconds')}
                    added+=1
        except (ValueError, OSError, ET.ParseError, TimeoutError) as e:
            errors.append({'source':name,'reason':type(e).__name__})
    # Don't publish user-generated material, no unsanctioned contact; only linked release notes.
    items=sorted(seen.values(),key=lambda x:(x.get('published') or '',x.get('first_seen_utc') or ''),reverse=True)[:500]
    report={'updated_utc':day.isoformat(timespec='seconds'),
      'status':'metadata_collected_not_content_verified',
      'sources_checked':len(selected[:6]),'sources_total':len(SOURCES),
      'new_links':added,'failures':errors,
      'review_policy':'no_auto_claims_no_model_retraining_external_links_only',
      'items':items}
    return report

def main():
    previous={'items':[]}
    if DB.exists():
        try:previous=json.loads(DB.read_text('utf-8'))
        except (ValueError,OSError):pass
    outcome=collect(previous.get('items', []))
    DB.write_text(json.dumps(outcome,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'scanned':outcome['sources_checked'],'added':outcome['new_links'],
       'failures':outcome['failures'],'total':len(outcome['items'])},ensure_ascii=False))
    # Partial outages should be visible, but don't erase previously retained records.
    return 0 if len(outcome['failures'])<outcome['sources_checked'] else 2
if __name__=='__main__':sys.exit(main())

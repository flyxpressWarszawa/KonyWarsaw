#!/usr/bin/env python3
"""MSW public WordPress link/technical-language audit; read-only, incremental."""
from __future__ import annotations
import json,re,sys,datetime as dt,urllib.request,urllib.parse
from html.parser import HTMLParser
from urllib.error import HTTPError,URLError
from pathlib import Path
SITE="https://mobilny-serwis-warszawa.pl"
OUT=Path(__file__).resolve().parent/"data"/"site-audit.json"
BUDGET=80
LEGACY=re.compile(r"\b(walki robotów|moc bojowa|portfel demo|kup bota do walki|kryptowalutami)\b",re.I)
PORTAL_PATHS=["msw-ai-arena","gielda-ai-robotow","msw-technical-university",
"msw-technical-university/program","msw-technical-university/laboratoria",
"msw-technical-atlas","msw-techniczne-narzedzia","msw-quantum-research",
"msw-mechatronika-ai","msw-elektronika-ai","msw-okablowanie-ai",
"msw-ai-centrum-robotow","msw-technical-ai-network","gielda-technologii-ai",
"arena-diagnostyki-ai","kreator-agentow-technicznych-ai","projekty-robotow-ai",
"sklep-modulow-ai-iot","msw-ai-discovery-360","msw-uni-boty","msw-uni-analityka"]
class Links(HTMLParser):
 def __init__(self):super().__init__();self.links=[]
 def handle_starttag(self,tag,attrs):
  if tag=="a":
   href=dict(attrs).get("href")
   if href:self.links.append(href)
def canonical(link,parent):
 p=urllib.parse.urlsplit(urllib.parse.urljoin(parent,link))
 if p.scheme!="https" or p.hostname!="mobilny-serwis-warszawa.pl":return None
 return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path or "/",p.query,""))
def jsonget(url):
 req=urllib.request.Request(url,headers={"User-Agent":"MSW-technical-link-audit/1.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=12) as res:
  if urllib.parse.urlsplit(res.geturl()).hostname!="mobilny-serwis-warszawa.pl":raise ValueError("Unexpected redirect")
  data=res.read(8_000_001)
  if len(data)>8_000_000:raise ValueError("Response oversized")
 return json.loads(data)
def wp(kind):
 all=[]
 for page in range(1,21):
  query=urllib.parse.urlencode({"status":"publish","page":page,"per_page":100,"_fields":"id,link,title,content"})
  try:batch=jsonget(SITE+"/wp-json/wp/v2/"+kind+"?"+query)
  except HTTPError as e:
   if e.code in (400,404) and page>1:break
   raise
  if not isinstance(batch,list):raise ValueError("Invalid WordPress REST response")
  if not batch:break
  all.extend(batch)
  if len(batch)<100:break
 return all
def probe(url):
 try:
  req=urllib.request.Request(url,method="HEAD",headers={"User-Agent":"MSW-technical-link-audit/1.0"})
  with urllib.request.urlopen(req,timeout=7) as res:
   if urllib.parse.urlsplit(res.geturl()).hostname!="mobilny-serwis-warszawa.pl":return {"state":"unknown","http":None}
   return {"state":"ok" if res.status<400 else "bad","http":res.status}
 except HTTPError as e:return {"state":"bad" if e.code in (404,410) else "unknown","http":e.code}
 except (OSError,TimeoutError,URLError):return {"state":"unknown","http":None}
def run():
 allrows={kind:wp(kind) for kind in ("pages","posts")}
 previous={}
 if OUT.exists():
  try:previous=json.loads(OUT.read_text(encoding="utf-8"))
  except (OSError,ValueError):pass
 urls={SITE+"/"+p+"/" for p in PORTAL_PATHS}
 notices=[]
 for kind,rows in allrows.items():
  for r in rows:
   origin=str(r.get("link",SITE+"/"))
   text=str((r.get("title") or {}).get("rendered",""))+" "+str((r.get("content") or {}).get("rendered",""))
   findings=list(set(LEGACY.findall(re.sub(r"<[^>]*>"," ",text))))
   if findings:notices.append({"post_id":r.get("id"),"kind":kind,"url":origin,"legacy_phrases":findings})
   parsed=Links();parsed.feed(str((r.get("content") or {}).get("rendered","")))
   for href in parsed.links:
    normalized=canonical(href,origin)
    if normalized:urls.add(normalized)
 old=previous.get("link_checks",{}) if isinstance(previous,dict) else {}
 checked={u:v for u,v in old.items() if u in urls}
 for u in [u for u in sorted(urls) if u not in checked][:BUDGET]:checked[u]=probe(u)
 result={"schema":"msw-public-technical-audit/v1","checked_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
  "pages_scanned":len(allrows["pages"]),"posts_scanned":len(allrows["posts"]),
  "internal_links_discovered":len(urls),"links_checked":len(checked),"links_remaining":len(urls)-len(checked),
  "legacy_text_candidates":notices[:200],"unknown_link_status_count":sum(x.get("state")=="unknown" for x in checked.values()),
  "confirmed_404_410":[{"url":u,"http":r["http"]} for u,r in checked.items() if r.get("state")=="bad"],
  "completeness":"links_complete_for_public_WP_only" if len(urls)==len(checked) else "incremental_partial",
  "external_links_not_verified":True,"no_content_modified":True,"private_content_excluded":True,
  "not_claiming_every_page_of_the_entire_site":True,"link_checks":checked}
 OUT.parent.mkdir(parents=True,exist_ok=True)
 temp=OUT.with_suffix(".tmp");temp.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
 temp.replace(OUT)
 print(json.dumps({k:result[k] for k in ("pages_scanned","posts_scanned","internal_links_discovered","links_checked","links_remaining","completeness")}))
if __name__=="__main__":
 try:run()
 except Exception as e:
  print(f"Read-only audit skipped, retaining previous: {type(e).__name__}: {e}",file=sys.stderr)
  sys.exit(1)

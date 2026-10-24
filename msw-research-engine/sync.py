#!/usr/bin/env python3
"""MSW independent daily PUBLIC research feed. No customer data or device control."""
from __future__ import annotations
import json, re, sys, hashlib, datetime as dt, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from pathlib import Path
TOPICS="https://api.openalex.org/topics"
KEV="https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CIS="https://www.cisecurity.org/feed/advisories"
OUTPUT=Path(__file__).resolve().parent/"data"/"latest.json"
LIMITS={"openalex":1500000,"cisa":8000000,"cis":1000000}
def digest(raw): return hashlib.sha256(raw).hexdigest()
def get(url,kind):
    host=urllib.parse.urlsplit(url).hostname
    if not ((kind=="openalex" and host=="api.openalex.org" and url.startswith(TOPICS+"?")) or
            (kind=="cisa" and url==KEV) or (kind=="cis" and url==CIS)):
        raise ValueError("Source not allowlisted")
    req=urllib.request.Request(url,headers={"User-Agent":"MSW-Technical-University-Public-Research/1.0","Accept":"application/json, application/xml, */*"})
    with urllib.request.urlopen(req,timeout=18) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname not in ("api.openalex.org","www.cisa.gov","www.cisecurity.org"):
            raise ValueError("Redirect escaped allowlist")
        if response.status!=200: raise ValueError("Unexpected HTTP status")
        raw=response.read(LIMITS[kind]+1)
    if len(raw)>LIMITS[kind]: raise ValueError("Oversize")
    return raw
def label(v):
    return str(v.get("display_name",""))[:160] if isinstance(v,dict) else ""
def openalex():
    out={}; sha=[]
    for page in range(1,11):
        q=urllib.parse.urlencode({"per_page":100,"page":page,"select":"id,display_name,subfield,field,domain"})
        raw=get(TOPICS+"?"+q,"openalex");sha.append(digest(raw))
        rows=json.loads(raw).get("results")
        if not isinstance(rows,list): raise ValueError("OpenAlex results missing")
        for v in rows:
            if not isinstance(v,dict): continue
            m=re.fullmatch(r"https://openalex\.org/(T\d+)",str(v.get("id","")))
            title=str(v.get("display_name","")).strip()
            if not m or len(title)<4: continue
            out[m[1]]={"id":m[1],"name":title[:255],"field":label(v.get("field")),"subfield":label(v.get("subfield")),"domain":label(v.get("domain"))}
    if len(out)<800: raise ValueError("Too few valid topics")
    return list(out.values())[:1000],digest("|".join(sha).encode())
def cisa():
    raw=get(KEV,"cisa");j=json.loads(raw);rows=j.get("vulnerabilities")
    if not isinstance(rows,list): raise ValueError("No CISA vulnerabilities")
    out={}
    for v in rows:
        if not isinstance(v,dict): continue
        cve=str(v.get("cveID",""))
        if not re.fullmatch(r"CVE-\d{4}-\d{4,}",cve): continue
        out[cve]={"cve":cve,"vendor":str(v.get("vendorProject",""))[:100],"product":str(v.get("product",""))[:100],"name":str(v.get("vulnerabilityName",""))[:220],"date_added":str(v.get("dateAdded",""))[:10]}
    if not out: raise ValueError("No valid CISA records")
    return sorted(out.values(),key=lambda v:(v["date_added"],v["cve"]),reverse=True)[:1000],digest(raw),len(out)
def cis():
    raw=get(CIS,"cis");tree=ET.fromstring(raw)
    rows=tree.findall("./channel/item") or tree.findall("{http://www.w3.org/2005/Atom}entry")
    out=[]
    for v in rows:
        title=(v.findtext("title") or v.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        url=(v.findtext("link") or "").strip()
        if not url:
            node=v.find("{http://www.w3.org/2005/Atom}link")
            if node is not None: url=node.get("href") or ""
        p=urllib.parse.urlsplit(url)
        if p.scheme!="https" or p.hostname not in ("cisecurity.org","www.cisecurity.org") or not title: continue
        out.append({"title":title[:180],"url":url[:350],"published":(v.findtext("pubDate") or v.findtext("{http://www.w3.org/2005/Atom}updated") or "")[:80]})
        if len(out)>=50: break
    if not out: raise ValueError("No valid CIS RSS headlines")
    return out,digest(raw)
def build():
    topics,dh=openalex();kevs,dk,total=cisa();news,dc=cis()
    return {"schema":"msw-public-research/v1",
      "retrieved_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
      "counts":{"openalex_topics":len(topics),"cisa_kevs":len(kevs),"cis_advisories":len(news),"full_cisa_catalog":total},
      "source_sha256":{"openalex_pages_digest":dh,"cisa_body_digest":dk,"cis_feed_digest":dc},
      "topics":topics,"cisa_kev":kevs,"cis_advisories":news,
      "provenance":{"openalex":TOPICS,"cisa":KEV,"cis":CIS,
        "hash_not_signature":True,"training_models":False,"publishing_unreviewed_articles":False,
        "private_data":False,"hardware_quantum":False}}
if __name__=="__main__":
    try:
        item=build(); OUTPUT.parent.mkdir(parents=True,exist_ok=True)
        tmp=OUTPUT.with_suffix(".tmp")
        txt=json.dumps(item,ensure_ascii=False,sort_keys=True,indent=2)+"\n"
        tmp.write_text(txt,encoding="utf-8");tmp.replace(OUTPUT)
        print(json.dumps({"ok":True,"counts":item["counts"],"sha256":digest(txt.encode())}))
    except (OSError,ValueError,KeyError,ET.ParseError,json.JSONDecodeError) as e:
        print(f"Public sync failed: {type(e).__name__} {e}; old snapshot untouched",file=sys.stderr);sys.exit(1)

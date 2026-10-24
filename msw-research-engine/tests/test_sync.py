import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sync

class TestPublicResearch(unittest.TestCase):
 def test_restrict_sources(self):
    with self.assertRaises(ValueError):
      sync.get("https://untrusted.example/private","cisa")
 def test_openalex_1000_unique(self):
    def fake(url, kind):
      page=int(__import__("urllib.parse",fromlist=["parse_qs"]).parse_qs(__import__("urllib.parse",fromlist=["urlsplit"]).urlsplit(url).query)["page"][0])
      return json.dumps({"results":[{"id":f"https://openalex.org/T{page*100+i}","display_name":f"Research {page}-{i}","domain":{"display_name":"Engineering"}} for i in range(100)]}).encode()
    with patch.object(sync,"get",side_effect=fake):
      data, sha=sync.openalex()
    self.assertEqual(len(data),1000)
    self.assertEqual(len(sha),64)
 def test_cisa_most_recent(self):
    docs={"vulnerabilities":[{"cveID":"CVE-2026-11111","dateAdded":"2026-01-02"},{"cveID":"CVE-2026-22222","dateAdded":"2026-10-01"}]}
    with patch.object(sync,"get",return_value=json.dumps(docs).encode()):
      data,sha,total=sync.cisa()
    self.assertEqual(total,2)
    self.assertEqual(data[0]["cve"],"CVE-2026-22222")
 def test_cis_bad_domain_rejected(self):
    content=b"<rss><channel><item><title>Valid</title><link>https://www.cisecurity.org/test</link></item><item><title>Invalid</title><link>https://example.com/private</link></item></channel></rss>"
    with patch.object(sync,"get",return_value=content):
      data,sha=sync.cis()
    self.assertEqual(len(data),1)
    self.assertEqual(data[0]["title"],"Valid")
 def test_cis_empty_not_published(self):
    with patch.object(sync,"get",return_value=b"<rss><channel/></rss>"):
      with self.assertRaises(ValueError): sync.cis()
if __name__=="__main__": unittest.main()

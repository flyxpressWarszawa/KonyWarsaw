import io
import json
import unittest
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import patch

import hf_bridge as h

SITE = "synthetic-site-token-not-real-123456789"
KEY = "synthetic_bridge_key_0123456789abcdef0123456789abcdef"

class Response:
    def __init__(self, data, status=200):
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"
        self.body = json.dumps(data).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n): return self.body[:n]

class Opener:
    def __init__(self, data): self.data=data; self.requests=[]
    def open(self, req, timeout):
        self.requests.append((req,timeout))
        return Response(self.data)

def env():
    return {h.SITE_SECRET_ENV:SITE, h.BRIDGE_SECRET_ENV:KEY}

class Tests(unittest.TestCase):
    def test_configuration_does_not_report_connected_without_probe(self):
        bridge=h.HuggingFaceBridge(env(),Opener({}))
        self.assertEqual(bridge.status(),{"configured":True,"verified":False,"state":"not_checked","last_checked_at":None})
        self.assertFalse(h.HuggingFaceBridge({},Opener({})).verify())

    def test_authenticated_probe_uses_fixed_url_and_no_secret_in_status(self):
        opener=Opener({"connected":True,"account":"msw","authentication":"personal_access_token"})
        bridge=h.HuggingFaceBridge(env(),opener)
        self.assertTrue(bridge.verify())
        self.assertEqual(bridge.status()["state"],"connected")
        self.assertIsNotNone(bridge.status()["last_checked_at"])
        self.assertNotIn(SITE,json.dumps(bridge.status()))
        self.assertNotIn(KEY,json.dumps(bridge.status()))
        req,timeout=opener.requests[0]
        self.assertEqual(req.full_url,h.ENDPOINT)
        self.assertEqual(timeout,12)
        self.assertEqual(req.get_header("Oai-sites-authorization"),"Bearer "+SITE)
        self.assertEqual(req.get_header("X-msw-hf-bridge-key"),KEY)
        self.assertEqual(json.loads(req.data),{"operation":"status"})

    def test_success_requires_personal_token_confirmation(self):
        for data in [{"connected":True},{"connected":True,"authentication":"oauth"},{"connected":False,"authentication":"personal_access_token"}]:
            bridge=h.HuggingFaceBridge(env(),Opener(data))
            self.assertFalse(bridge.verify())
            self.assertEqual(bridge.status()["state"],"unavailable")

    def test_redirects_refused_and_exception_details_hidden(self):
        with self.assertRaises(h.BridgeError):
            h.NoRedirect().redirect_request(None,None,302,None,None,"https://elsewhere.example")
        class Broken:
            def open(self,*args,**kwargs): raise RuntimeError("secret "+SITE+" "+KEY)
        bridge=h.HuggingFaceBridge(env(),Broken())
        with self.assertRaises(h.BridgeError) as caught: bridge.search("models","Hermes",3)
        self.assertNotIn(SITE,str(caught.exception))
        self.assertNotIn(KEY,str(caught.exception))

    def test_search_is_bounded_and_returns_real_bridge_data(self):
        opener=Opener({"type":"models","results":[{"id":"owner/model","url":"https://huggingface.co/owner/model"}]})
        bridge=h.HuggingFaceBridge(env(),opener)
        actual=bridge.search("models","Hermes",1)
        self.assertEqual(actual["results"][0]["id"],"owner/model")
        for kind,query,limit in [("models","",1),("models","x",6),("bad","x",1),("models","x",True)]:
            with self.assertRaises(h.BridgeError): bridge.search(kind,query,limit)
        self.assertEqual(len(opener.requests),1)

    def test_search_rejects_wrong_shape_or_excess_results(self):
        for data in [{"type":"spaces","results":[]},{"type":"models","results":[{},{}]},{"type":"models","results":{}}]:
            with self.assertRaises(h.BridgeError): h.HuggingFaceBridge(env(),Opener(data)).search("models","x",1)

if __name__ == "__main__": unittest.main()

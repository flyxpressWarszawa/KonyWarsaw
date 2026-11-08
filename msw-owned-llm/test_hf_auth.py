import base64
import hashlib
import hmac
import json
import unittest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import gateway

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        pub=cls.private.public_key()
        cls.pem=pub.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        der=pub.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.kid=gateway.sha(der)

    def auth(self):
        return gateway.OwnerAuth(getter=lambda:{"schema":"msw.owned-llm.key.v1","origin":gateway.CENTRAL,"enabled":True,"public_key":self.pem,"key_id":self.kid},clock=lambda:1700000000)

    def headers(self,raw,path,nonce):
        ts="1700000000"
        message=("msw-owned-llm.v1\nPOST\n"+path+"\n"+ts+"\n"+nonce+"\n"+gateway.sha(raw)).encode()
        sig=self.private.sign(message,padding.PKCS1v15(),hashes.SHA256())
        return {"X-MSW-Time":ts,"X-MSW-Nonce":nonce,"X-MSW-Key":self.kid,"X-MSW-Signature":base64.b64encode(sig).decode()}

    def test_signatures_are_bound_to_route_and_replay_is_denied(self):
        raw=b'{"request_id":"12345678","type":"models","query":"Hermes","limit":3}'
        auth=self.auth(); headers=self.headers(raw,"/v1/hf/search","a"*32)
        auth.authorize(raw,headers,"/v1/hf/search")
        with self.assertRaises(gateway.Fault): auth.authorize(raw,headers,"/v1/hf/search")
        auth=self.auth(); headers=self.headers(raw,"/v1/generate","b"*32)
        with self.assertRaises(gateway.Fault): auth.authorize(raw,headers,"/v1/hf/search")

    def test_hf_search_request_validation(self):
        good={"request_id":"12345678","type":"models","query":"Hermes","limit":3}
        self.assertEqual(gateway.parse_hf_request(json.dumps(good).encode()),good)
        for bad in [{**good,"limit":True},{**good,"limit":6},{**good,"type":"https://evil.example"},{**good,"query":""},{**good,"token":"secret"}]:
            with self.assertRaises(gateway.Fault): gateway.parse_hf_request(json.dumps(bad).encode())

    def test_site_signature_is_body_bound_and_replay_protected(self):
        key="synthetic_bridge_key_0123456789abcdef0123456789abcdef"
        raw='{"request_id":"12345678","description":"Awaria gniazdka"}'.encode()
        auth=gateway.SiteAuth(key=key,clock=lambda:1700000000)
        nonce="c"*32;ts="1700000000"
        msg=("msw-site-triage.v1\nPOST\n/v1/lead-triage\n"+ts+"\n"+nonce+"\n"+gateway.sha(raw)).encode()
        sig=hmac.new(key.encode(),msg,hashlib.sha256).hexdigest()
        headers={"X-MSW-Time":ts,"X-MSW-Nonce":nonce,"X-MSW-Signature":sig}
        gateway.parse_lead_request(raw)
        auth.authorize(raw,headers)
        with self.assertRaises(gateway.Fault): auth.authorize(raw,headers)
        fresh=gateway.SiteAuth(key=key,clock=lambda:1700000000)
        with self.assertRaises(gateway.Fault): fresh.authorize(raw+b" ",headers)
        with self.assertRaises(gateway.Fault): gateway.parse_lead_request(b'{"request_id":"12345678","description":"<|attack"}')

if __name__ == "__main__": unittest.main()

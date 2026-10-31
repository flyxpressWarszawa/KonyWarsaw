import json, unittest
import advisory as a
D={'name':'MSW AI V7','version':'7.1.0','active':True,'registered_adapters':1,'previous_transport_failures':0}
P=a.PREFIX+json.dumps(D)+a.SUFFIX
GOOD='{"check_id":"error_log_review"}'
class Tests(unittest.TestCase):
    def test_parse(self): self.assertEqual(a.facts(P),D)
    def test_task(self):
        self.assertIn('MSW AI V7',a.task(P)); self.assertIn('do not prove plugin faults',a.task(P))
    def test_catalog_entries(self):
        for key in a.CATALOG:
            out=a.validate(json.dumps({'check_id':key}),P)
            self.assertTrue(out['passed']);self.assertEqual(out['check_id'],key)
    def test_observed_echo_rejected(self):
        for text in [P, 'Check the following:', 'Check: The plugin error is 404 Not Found']:
            with self.assertRaises(ValueError): a.validate(text,P)
    def test_truncation(self):
        with self.assertRaises(ValueError): a.validate(GOOD[:-1],P)
    def test_no_false_completion(self):
        with self.assertRaises(ValueError): a.validate('{"check_id":"error_log_review","executed":true}',P)
    def test_mutation_not_in_catalog(self):
        for key in ['delete','disable','install','restart','execute','publish']:
            with self.assertRaises(ValueError): a.validate(json.dumps({'check_id':key}),P)
    def test_schema(self):
        for d in [{**D,'active':False},{**D,'registered_adapters':True},{**D,'extra':'value'},{**D,'previous_transport_failures':4}]:
            with self.assertRaises(ValueError): a.facts(a.PREFIX+json.dumps(d)+a.SUFFIX)
    def test_suffix(self):
        with self.assertRaises(ValueError): a.facts(P+' Extra instructions.')
    def test_raw_model_bytes(self):
        class R:
            def backend(self,path,payload):
                return {'tokens':[1,2]} if path=='/tokenize' else {'content':GOOD,'tokens_predicted':12}
        out=a.generate(R(),P)
        self.assertEqual(out['text'],GOOD)
        self.assertEqual(out['catalog_description_origin'],'human_authored_not_model_generated')
        self.assertFalse(out['selected_check_executed'])
    def test_no_canned_fallback(self):
        class R:
            def backend(self,path,payload):
                return {'tokens':[1]} if path=='/tokenize' else {'content':P,'tokens_predicted':64}
        with self.assertRaises(ValueError): a.generate(R(),P)
    def test_duplicate_keys(self):
        with self.assertRaises(ValueError): a.validate('{"check_id":"error_log_review","check_id":"adapter_receipt_check"}',P)
    def test_array_and_wrong_type(self):
        for raw in ['["error_log_review"]','{"check_id":12345}','{"check_id":null}']:
            with self.assertRaises(ValueError): a.validate(raw,P)
if __name__=='__main__':unittest.main()

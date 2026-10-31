import json, unittest
import advisory as a
D={'name':'MSW AI V7','version':'7.1.0','active':True,'registered_adapters':1,'previous_transport_failures':0}
P=a.PREFIX+json.dumps(D)+a.SUFFIX
GOOD='Check the plugin error logs for warnings before making any changes.'
class Tests(unittest.TestCase):
    def test_parse(self): self.assertEqual(a.facts(P),D)
    def test_task(self):
        self.assertIn('MSW AI V7',a.task(P)); self.assertNotIn('registered_adapters',a.task(P))
    def test_good(self): self.assertTrue(a.validate(GOOD,P)['passed'])
    def test_echo(self):
        for text in [P, 'Check registered_adapters and previous_transport_failures for errors.']:
            with self.assertRaises(ValueError): a.validate(text,P)
    def test_truncation(self):
        with self.assertRaises(ValueError): a.validate(GOOD[:-1],P)
    def test_false_completion(self):
        with self.assertRaises(ValueError): a.validate('Check the dashboard because I have fixed all errors already.',P)
    def test_mutating(self):
        for word in ['delete','disable','install','restart','execute']:
            with self.assertRaises(ValueError): a.validate('Check the plugin and '+word+' any items that fail the test.',P)
    def test_schema(self):
        for d in [{**D,'active':False},{**D,'registered_adapters':True},{**D,'extra':'value'},{**D,'previous_transport_failures':4}]:
            with self.assertRaises(ValueError): a.facts(a.PREFIX+json.dumps(d)+a.SUFFIX)
    def test_suffix(self):
        with self.assertRaises(ValueError): a.facts(P+' Extra instructions.')
    def test_raw_model_bytes(self):
        class R:
            def backend(self,path,payload):
                return {'tokens':[1,2]} if path=='/tokenize' else {'content':GOOD,'tokens_predicted':16}
        self.assertEqual(a.generate(R(),P)['text'],GOOD)
    def test_no_canned_fallback(self):
        class R:
            def backend(self,path,payload):
                return {'tokens':[1]} if path=='/tokenize' else {'content':P,'tokens_predicted':64}
        with self.assertRaises(ValueError): a.generate(R(),P)
if __name__=='__main__':unittest.main()

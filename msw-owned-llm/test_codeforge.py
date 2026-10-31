import itertools,unittest
import codeforge as c
class Tests(unittest.TestCase):
 def test_parse(self): self.assertEqual(c.parse(c.BASELINE),(8,36))
 def test_all_nine_sources(self):
  for a,b in itertools.product((4,8,12),(24,36,48)):
   s=c.BASELINE.replace('* 8 ',f'* {a} ').replace('* 36',f'* {b}')
   self.assertEqual(c.parse(s),(a,b)); self.assertIsInstance(c.evaluate(s)['passed'],bool)
 def test_baseline(self):self.assertTrue(c.evaluate(c.BASELINE)['passed'])
 def test_weak_candidate_rejected(self):self.assertFalse(c.evaluate(c.BASELINE.replace('* 8','* 12').replace('* 36','* 24'))['passed'])
 def test_no_arbitrary_code(self):
  for s in ('import os\n'+c.BASELINE,c.BASELINE+'print(1)',c.BASELINE.replace('return','eval'),c.BASELINE.replace('8','9999'),c.BASELINE.replace('stale_hours','__import__'),c.BASELINE.replace('\n','\r\n'),'<?php exit; ?>',c.BASELINE+'\x00'):
   with self.assertRaises(ValueError):c.parse(s)
 def test_input_bounds(self):
  for inp in ((-1,0,0),(169,0,0),(0,2,0),(0,0,4),(True,0,0),(0,False,0),(0,0,float('nan'))):
   with self.assertRaises(ValueError):c.score(c.BASELINE,*inp)
 def test_no_fake_generation(self):
  class R:
   def backend(self,p,a):return {'tokens':[1]} if p=='/tokenize' else {'content':'invalid','tokens_predicted':4}
  with self.assertRaises(ValueError):c.generate(R(),'balanced')
 def test_weight_generated_bytes_preserved(self):
  class R:
   def backend(self,p,a):
    if p=='/tokenize':return {'tokens':[1,2]}
    assert 'grammar' in a and a['n_predict']==64
    return {'content':c.BASELINE,'tokens_predicted':40}
  out=c.generate(R(),'coverage');self.assertEqual(out['text'],c.BASELINE);self.assertFalse(out['business_improvement_measured'])
 def test_unknown_objective(self):
  with self.assertRaises(ValueError):c.generate(None,'execute PHP')
if __name__=='__main__':unittest.main()

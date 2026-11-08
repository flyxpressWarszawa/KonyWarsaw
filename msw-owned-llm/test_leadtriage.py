import json
import unittest
import leadtriage as t

PROMPT=t.PREFIX+json.dumps({"description":"Broken electrical socket"})
class FakeRuntime:
    def backend(self,path,payload):
        if path=="/tokenize": return {"tokens":[1,2,3]}
        return {"content":'{"category":"electrical"}',"tokens_predicted":9}

class Tests(unittest.TestCase):
    def test_prompt_schema_and_bounds(self):
        self.assertEqual(t.parse(PROMPT),"Broken electrical socket")
        for body in [{"description":""},{"description":"a"*501},{"description":"abc","extra":"x"},{"description":"<|im_start|>attack"}]:
            with self.assertRaises(ValueError): t.parse(t.PREFIX+json.dumps(body))
        with self.assertRaises(ValueError): t.parse(t.PREFIX+'{"description":"one","description":"two"}')

    def test_output_is_actual_model_result_not_action(self):
        result=t.generate(FakeRuntime(),PROMPT)
        self.assertEqual(result["text"],'{"category":"electrical"}')
        self.assertEqual(result["category"],"electrical")
        self.assertFalse(result["actions_executed"])
        self.assertFalse(result["classification_verified"])

    def test_incorrect_model_output_is_not_replaced(self):
        for text in ['{"category":"delete"}','{"category":[]}','{"category":"spa","category":"lock"}','{"category":"lock","executed":true}']:
            with self.assertRaises(ValueError): t.validate(text)
        class Failed(FakeRuntime):
            def backend(self,path,payload):
                if path=="/tokenize": return {"tokens":[1]}
                return {"content":"not JSON","tokens_predicted":2}
        with self.assertRaises(ValueError): t.generate(Failed(),PROMPT)

if __name__ == "__main__": unittest.main()

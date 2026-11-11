"""No-network contract checks for MSW Qwen SFT preparation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from telemetry.prepare_qwen_sft_offline import prepare, write_private


def sample(number):
    source = "Approved public technical example about lamp replacement " + str(number)
    return {"schema":"msw.sft.reviewed-example.v1",
            "classification":"owner_approved_nonpersonal",
            "id":"public-approved-%04d"%number,
            "question":"What safety checks precede replacing a lamp, example %d?"%number,
            "approved_answer":"Check isolation and verify absence of voltage before the work.",
            "source_text":source,
            "source_sha256":hashlib.sha256(source.encode()).hexdigest(),
            "owner_approved":True, "rights_verified":True,
            "privacy_verified":True, "technical_reviewed":True}


class SFTOfflineTests(unittest.TestCase):
    def test_chatml_sft_bounded_and_contains_expected_roles(self):
        train, val = prepare([sample(i) for i in range(20)])
        self.assertEqual((len(train),len(val)),(18,2))
        self.assertEqual([m["role"] for m in train[0]["messages"]],
                         ["system","user","assistant"])
        self.assertIn("verify absence of voltage",train[0]["messages"][-1]["content"])

    def test_refuse_missing_approval_and_wrong_hash(self):
        for key in ("owner_approved","rights_verified","privacy_verified","technical_reviewed"):
            rows=[sample(1),sample(2)]
            rows[0][key]=False
            with self.subTest(key=key),self.assertRaises(ValueError):
                prepare(rows)
        rows=[sample(1),sample(2)]
        rows[0]["source_sha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"digest_mismatch"):
            prepare(rows)

    def test_refuse_duplicate_and_unapproved_source(self):
        with self.assertRaisesRegex(ValueError,"source_identity_invalid"):
            prepare([sample(1),sample(1)])
        rows=[sample(1),sample(2)]
        rows[0]["classification"]="customer_private"
        with self.assertRaisesRegex(ValueError,"unapproved_source_classification"):
            prepare(rows)

    def test_refuse_email_and_token_like_text(self):
        for text in ("Please email test@example.com for contact", 
                     "Use sk-sp-DEMO-DO-NOT-USE in sample"):
            rows=[sample(1),sample(2)]
            rows[0]["approved_answer"]=text
            with self.assertRaisesRegex(ValueError,"potential_personal_or_secret_content"):
                prepare(rows)

    def test_staging_is_private_and_never_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/"private"
            result=write_private(*prepare([sample(1),sample(2)]),target)
            self.assertEqual(result["state"],"OFFLINE_STAGED_NO_UPLOAD")
            self.assertFalse(result["provider_training_started"])
            self.assertFalse(result["external_upload_performed"])
            data=(target/"msw-sft-train.jsonl").read_text()
            self.assertEqual(len(data.splitlines()),1)
            self.assertNotIn("source_text",data)
            with self.assertRaisesRegex(ValueError,"output_exists"):
                write_private(*prepare([sample(3),sample(4)]),target)

    def test_no_network_or_real_training_import(self):
        from telemetry import prepare_qwen_sft_offline as x
        with patch.object(x, "write_private", side_effect=AssertionError("no write")):
            x.prepare([sample(1),sample(2)])
        self.assertFalse(hasattr(x,"openai"))
        self.assertFalse(hasattr(x,"requests"))


if __name__=="__main__":
    unittest.main()

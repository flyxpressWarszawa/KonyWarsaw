"""Verify existing runtime catalogue does not repeatedly hash private tensors by default."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.tasks import runtime_catalog


class ResearchCatalogOptInTests(unittest.TestCase):
    def test_full_checkpoint_verification_is_explicit_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime-status.json").write_text(json.dumps({"state": "ready", "modules": []}))
            with (patch.dict(os.environ, {"MSW_RUNTIME_MODE": "render-free"}),
                  patch("msw_engine.legacy_memory.status", return_value={"state": "absent"}),
                  patch("msw_engine.autonomous_agent.status", return_value={"state": "idle"}),
                  patch("msw_engine.recovery.status", return_value={"state": "ok"}),
                  patch("msw_engine.business_bridge.status", return_value={"state": "idle"}),
                  patch("msw_engine.federation.configured", return_value=False),
                  patch("msw_engine.own_model.research_status", return_value={
                      "state": "waiting_for_pinned_checkpoint",
                      "model_weight_training_verified": False,
                  }) as expensive_status):
                lightweight = runtime_catalog({}, root)
                self.assertNotIn("own_model_research", lightweight)
                expensive_status.assert_not_called()
                requested = runtime_catalog({"include_own_model_research": True}, root)
                self.assertEqual(requested["own_model_research"]["state"], "waiting_for_pinned_checkpoint")
                expensive_status.assert_called_once_with(root)
                other = runtime_catalog({"include_own_model_research": "true"}, root)
                self.assertNotIn("own_model_research", other)
                self.assertEqual(expensive_status.call_count, 1)


if __name__ == "__main__":
    unittest.main()

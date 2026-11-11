"""Exact existing seo.analyze: heavy synthetic work is pure Render-side Python.

These tests do NOT claim that a live Render service has this commit deployed,
and they never call WordPress, a paid model, the network or a PHP callback.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from msw_engine.tasks import TaskError, seo_analyze, dispatch


def sample(*, repeat=2000):
    text = "serwis spawanie diagnostyka automatyka " * repeat
    return {
        "title": "Mobilny serwis – syntetyczny artykuł",
        "content": text,
        "html": (
            "<h1>Serwis</h1><h2>Diagnostyka</h2>"
            + ("<p>serwis spawanie diagnostyka</p>" * repeat)
            + '<img src="/synthetic.jpg" alt="Technika">'
            + '<a href="/uslugi">Lokalnie</a>'
            + '<a href="https://external.example/info">Poza serwisem</a>'
        ),
        "url": "https://mobilny-serwis-warszawa.pl/technika",
    }


class SeoRenderPureCompute(unittest.TestCase):
    def test_large_synthetic_document_is_deterministic_and_only_python(self):
        data = sample(repeat=2200)
        self.assertLess(len(data["content"]), 100_000)
        self.assertLess(len(data["html"]), 140_000)
        with tempfile.TemporaryDirectory() as path:
            with patch("msw_engine.federation.gateway", side_effect=AssertionError("PHP gateway called")) as remote:
                first = seo_analyze(data, Path(path))
                second = seo_analyze(data, Path(path))
                remote.assert_not_called()
            self.assertEqual(first, second)
            self.assertEqual(first["headings"]["h2"], 1)
            self.assertEqual(first["images"], 1)
            self.assertEqual(first["images_with_alt"], 1)
            self.assertEqual(first["internal_links"], 1)
            self.assertEqual(first["external_links"], 1)
            self.assertGreater(first["word_count"], 6000)
            self.assertEqual(list(Path(path).iterdir()), [])

    def test_public_render_free_dispatch_stays_local(self):
        data = sample(repeat=120)
        with tempfile.TemporaryDirectory() as path, patch.dict(
            "os.environ", {"MSW_RUNTIME_MODE": "render-free"}, clear=True
        ), patch("msw_engine.federation.gateway", side_effect=AssertionError("PHP gateway called")) as remote:
            output = dispatch("seo.analyze", data, Path(path))
            remote.assert_not_called()
            self.assertGreater(output["word_count"], 100)
            self.assertFalse(any(Path(path).iterdir()))

    def test_never_uses_python_gateway_as_failed_compute_fallback(self):
        too_large = sample(repeat=20)
        too_large["content"] = "x" * 100_001
        with tempfile.TemporaryDirectory() as path, patch(
            "msw_engine.federation.gateway", side_effect=AssertionError("fallback executed")
        ) as remote:
            with self.assertRaisesRegex(TaskError, "document_limit"):
                seo_analyze(too_large, Path(path))
            remote.assert_not_called()
            self.assertEqual(list(Path(path).iterdir()), [])

    def test_html_document_limit_before_parse(self):
        data = sample(repeat=2)
        data["html"] = "y" * 140_001
        with tempfile.TemporaryDirectory() as path:
            with self.assertRaisesRegex(TaskError, "document_limit"):
                seo_analyze(data, Path(path))

    def test_output_is_advisory_not_execution_or_ranking_claim(self):
        with tempfile.TemporaryDirectory() as path:
            result = seo_analyze(sample(repeat=3), Path(path))
        self.assertIn("not a search ranking", result["measurement"])
        self.assertNotIn("applied", result)
        self.assertNotIn("provider", result)
        self.assertNotIn("customer", result)


if __name__ == "__main__":
    unittest.main()

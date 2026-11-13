import unittest
from dataclasses import replace

from msw_engine.free_fallback_policy import Provider, Meter, choose_free_provider


def example(**updates):
    fields = dict(
        provider_id="public_a", model="test-model", auth="anonymous",
        requires_existing_key=False, zero_price_verified=True,
        terms_verified=True, endpoint_verified=True, inference_verified=True,
        enabled_by_owner=True, private_data_allowed=False,
        capabilities=frozenset({"chat"}),
        requests_per_day=10, requests_per_minute=2, priority=10,
    )
    fields.update(updates)
    return Provider(**fields)


class FreeFallbackPolicyTest(unittest.TestCase):
    def decide(self, providers=None, meters=None, **updates):
        arguments = dict(
            now_epoch=1234567890,
            previous_state="new",
            required_capabilities=frozenset({"chat"}),
            contains_private_data=False,
            free_only=True,
        )
        arguments.update(updates)
        return choose_free_provider(
            [example()] if providers is None else providers,
            {} if meters is None else meters,
            **arguments
        )

    def test_public_new(self):
        self.assertEqual(self.decide().provider_id, "public_a")

    def test_private_data_is_not_sent_to_anonymous_provider(self):
        self.assertEqual(self.decide(contains_private_data=True).action, "hold")

    def test_accepted_and_uncertain_states_require_receipt(self):
        for value in ("in_flight", "accepted", "uncertain"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.decide(previous_state=value).reason,
                    "uncertain_effect_requires_receipt"
                )

    def test_confirmed_quota_failure_can_select_new_provider(self):
        self.assertEqual(
            self.decide(previous_state="confirmed_quota_exceeded").action,
            "execute_once"
        )

    def test_completed_request_is_never_repeated(self):
        self.assertEqual(
            self.decide(previous_state="completed").action, "already_complete"
        )

    def test_daily_quota_moves_to_next_admitted_provider(self):
        next_provider = example(provider_id="public_b", priority=20)
        decision = self.decide(
            providers=[example(), next_provider],
            meters={"public_a": Meter(day_count=10)},
        )
        self.assertEqual(decision.provider_id, "public_b")

    def test_minute_quota_holds(self):
        self.assertEqual(
            self.decide(meters={"public_a": Meter(minute_count=2)}).action, "hold"
        )

    def test_retry_after_holds(self):
        self.assertEqual(
            self.decide(
                meters={"public_a": Meter(cooldown_until_epoch=1234567891)}
            ).action, "hold"
        )

    def test_resolved_server_key_required(self):
        p = example(auth="existing_key", requires_existing_key=True)
        self.assertEqual(self.decide(providers=[p]).action, "hold")
        self.assertEqual(
            self.decide(
                providers=[p],
                meters={"public_a": Meter(credential_available=True)},
            ).action, "execute_once"
        )

    def test_unverified_candidate_never_enabled(self):
        for field in (
            "enabled_by_owner", "zero_price_verified", "terms_verified",
            "endpoint_verified", "inference_verified"
        ):
            with self.subTest(field=field):
                p = replace(example(), **{field: False})
                self.assertEqual(self.decide(providers=[p]).action, "hold")

    def test_missing_capability(self):
        self.assertEqual(
            self.decide(required_capabilities=frozenset({"embeddings"})).action,
            "hold"
        )

    def test_free_only_must_be_true(self):
        self.assertEqual(self.decide(free_only=False).action, "hold")

    def test_empty_capabilities_rejected(self):
        self.assertEqual(
            self.decide(required_capabilities=frozenset()).action, "hold"
        )

    def test_private_local_provider_allowed(self):
        p = example(
            provider_id="local", auth="local", private_data_allowed=True,
            priority=0, requests_per_day=None,
        )
        self.assertEqual(
            self.decide(providers=[p], contains_private_data=True).provider_id,
            "local"
        )


if __name__ == "__main__":
    unittest.main()

# Diagnose / release candidate: worker capability telemetry flapping

Observation time: 2026-11-13 (read-only production inspection). NO LIVE FIX APPLIED.

## What was observed
- `msw_engine.priority_lane.PriorityLane.step()` sends `claim` with `capabilities=["llm.generate"]`.
- The regular `Worker.run_once()` sends `claim` with the much wider `list(handlers())`.
- Both use the same signed `Client`, same node ID.
- On WordPress, `MSW\UnifiedEngine\RemoteStore::claim()` calls `self::seen($node,$allowed)` where `$allowed` is **claim filter**; `seen()` overwrites `mswue_remote_seen_v1[$node]['capabilities']`.
- Consequently, a report that momentarily shows only `llm.generate` is NOT reliable proof that the full worker cannot handle `knowledge.ingest`; the next broad claim can make it reappear.

## Proposed local fix
The unmerged candidate `wordpress/release_candidates/CapabilityTelemetry.php` retains each observed allowed capability for a 180-second bounded TTL, with per-capability last-seen timestamps and backwards compatibility with the current `{at,capabilities}` option. No extra HTTP calls are required.

The actual WordPress `plugins/msw-unified-engine/includes/RemoteStore.php` would need a **separate, reviewed patch**:

```php
require_once __DIR__ . '/CapabilityTelemetry.php';

// Replace ONLY the existing private static function seen() body:
private static function seen(string $node, array $capabilities): void
{
    $nodes = (array) get_option('mswue_remote_seen_v1', []);
    $nodes[$node] = CapabilityTelemetry::observe(
        (array) ($nodes[$node] ?? []), $capabilities, self::KINDS, time(), 180
    );
    update_option('mswue_remote_seen_v1', $nodes, false);
}
```

This change affects **telemetry only**: no changes to signed authentication, claim selection, job admission, leases, completion, or receipts. The worker's **configured authorized kinds** remain separate from recently observed capabilities. The TTL deliberately does not claim capabilities forever.

## Verification gate
1. Create an independently restorable backup of the exact current WordPress file and test an isolated copy; compare SHA-256 before write.
2. Run PHP lint, all currently installed queue tests, and regression tests at `wordpress/release_candidates/tests/capability_telemetry_test.php`.
3. Make one controlled staging request with the main worker and one with priority lane, verify one report shows both observed capability sets, and after TTL older kinds expire.
4. Validate no changes in queue status, submitted job ID, lease, applied state, or payload/result SHA.
5. A signed canary `knowledge.ingest` must complete with independent SQLite readback and WP receipt before broad resumption.
6. If any check fails, restore original file and existing option schema; never assume this telemetry-only fix repairs the separate `knowledge_effect_contract_required` blocker.

Local tests of an equivalent policy: PHP 8.4 lint PASS, 11 decision cases PASS. Not yet a live WordPress integration test or confirmed worker import.

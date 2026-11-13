<?php
declare(strict_types=1);

namespace MSW\UnifiedEngine;

/**
 * Proposed telemetry-only helper for RemoteStore::seen().
 *
 * A priority claim leases only llm.generate but the main worker has a wider
 * capability set. Do not overwrite the shared node's capabilities with this
 * one filtered claim. Keep observations bounded by an explicit expiration.
 *
 * No claims, receipts, leases or job admission are modified by this helper.
 */
final class CapabilityTelemetry
{
    public static function observe(array $previous, array $reported, array $allowlist, int $now, int $ttl = 180): array
    {
        if ($now < 1 || $ttl < 1 || $ttl > 3600) {
            throw new \InvalidArgumentException('invalid_observation_clock');
        }

        $valid = [];
        foreach ($allowlist as $kind) {
            if (is_string($kind) && $kind !== '') {
                $valid[$kind] = true;
            }
        }

        $timestamps = [];
        $old = $previous['capability_seen_at'] ?? null;

        // Compatibility with the existing {at,capabilities} option shape.
        if (!is_array($old)) {
            $stamp = $previous['at'] ?? null;
            if (is_int($stamp) && $stamp >= $now - $ttl && $stamp <= $now) {
                foreach ((array)($previous['capabilities'] ?? []) as $kind) {
                    if (is_string($kind) && isset($valid[$kind])) {
                        $timestamps[$kind] = $stamp;
                    }
                }
            }
        } else {
            foreach ($old as $kind => $stamp) {
                if (is_string($kind) && isset($valid[$kind]) && is_int($stamp)
                    && $stamp >= $now - $ttl && $stamp <= $now) {
                    $timestamps[$kind] = $stamp;
                }
            }
        }

        foreach (array_slice($reported, 0, 20) as $kind) {
            if (is_string($kind) && isset($valid[$kind])) {
                $timestamps[$kind] = $now;
            }
        }

        // Canonical order makes diffs and tests deterministic.
        ksort($timestamps, SORT_STRING);
        return [
            'at' => $now,
            'capabilities' => array_keys($timestamps),
            'capability_seen_at' => $timestamps,
        ];
    }
}

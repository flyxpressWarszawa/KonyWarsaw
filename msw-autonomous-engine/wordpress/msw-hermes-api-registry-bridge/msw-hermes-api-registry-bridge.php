<?php
/**
 * Plugin Name: MSW Hermes API Registry Bridge
 * Description: Private, read-only-to-visitors registry snapshot gateway. Does not run inference or store secrets.
 * Version: 0.1.0
 * Requires PHP: 8.0
 */
defined('ABSPATH') || exit;

const MSW_HRB_OPTION = 'msw_hermes_registry_snapshot_v1';
const MSW_HRB_SCHEMA = 'msw.hermes.wp-ai-registry.snapshot.v1';

function msw_hrb_can_read() {
    return current_user_can('manage_options');
}
function msw_hrb_can_write() {
    return current_user_can('manage_options') || current_user_can('msw_hermes_registry_write');
}
function msw_hrb_error($code, $message, $status) {
    return new WP_Error($code, $message, array('status' => $status));
}
function msw_hrb_status() {
    $snapshot = get_option(MSW_HRB_OPTION, array());
    $present = is_array($snapshot) && !empty($snapshot['digestSha256']);
    $routes = $present && is_array($snapshot['routes'] ?? null) ? $snapshot['routes'] : array();
    $providers = $present && is_array($snapshot['providers'] ?? null) ? $snapshot['providers'] : array();
    $ready = 0;
    foreach ($routes as $route) {
        if (($route['status'] ?? '') === 'READY') { $ready++; }
    }
    $seen = $present ? strtotime((string) ($snapshot['observedAtUtc'] ?? '')) : false;
    return rest_ensure_response(array(
        'bridgeVersion' => '0.1.0',
        'scope' => 'metadata_snapshot_only',
        'inferenceTransportVerified' => false,
        'snapshotPresent' => $present,
        'registryRevision' => $present ? (int) $snapshot['registryRevision'] : null,
        'lastAckRevision' => $present ? (int) $snapshot['lastAckRevision'] : null,
        'providerRecords' => count($providers),
        'runtimeRoutes' => count($routes),
        'readyRoutes' => $ready,
        'observedAtUtc' => $present ? $snapshot['observedAtUtc'] : null,
        'stale' => !$seen || time() - $seen > 1800,
        'digestSha256' => $present ? $snapshot['digestSha256'] : null,
    ));
}
function msw_hrb_snapshot() {
    $snapshot = get_option(MSW_HRB_OPTION, array());
    return rest_ensure_response(array(
        'snapshotPresent' => is_array($snapshot) && !empty($snapshot['digestSha256']),
        'snapshot' => is_array($snapshot) ? $snapshot : array(),
    ));
}
function msw_hrb_post(WP_REST_Request $request) {
    $length = (int) $request->get_header('content-length');
    if ($length > 128000) { return msw_hrb_error('msw_hrb_too_large', 'Snapshot exceeds 128 KB', 413); }
    $input = $request->get_json_params();
    if (!is_array($input) || ($input['schema'] ?? null) !== MSW_HRB_SCHEMA) {
        return msw_hrb_error('msw_hrb_schema', 'Unsupported snapshot schema', 422);
    }
    $revision = filter_var($input['registryRevision'] ?? null, FILTER_VALIDATE_INT, array('options' => array('min_range' => 0, 'max_range' => 999999999)));
    $ack = filter_var($input['lastAckRevision'] ?? null, FILTER_VALIDATE_INT, array('options' => array('min_range' => 0, 'max_range' => 999999999)));
    if ($revision === false || $ack === false || $ack > $revision) {
        return msw_hrb_error('msw_hrb_revision', 'Invalid registry/ACK revisions', 422);
    }
    $seen = strtotime((string) ($input['observedAtUtc'] ?? ''));
    if (!$seen || $seen > time() + 300 || $seen < time() - 604800) {
        return msw_hrb_error('msw_hrb_time', 'Snapshot timestamp outside allowed window', 422);
    }
    $rawProviders = $input['providers'] ?? null;
    $rawRoutes = $input['routes'] ?? null;
    if (!is_array($rawProviders) || !is_array($rawRoutes) || count($rawProviders) > 200 || count($rawRoutes) > 80) {
        return msw_hrb_error('msw_hrb_count', 'Snapshot entries exceed bounds', 422);
    }
    $providers = array();
    $providerIds = array();
    foreach ($rawProviders as $record) {
        if (!is_array($record)) { return msw_hrb_error('msw_hrb_provider', 'Provider must be an object', 422); }
        $id = sanitize_key((string) ($record['id'] ?? ''));
        $status = strtoupper(sanitize_text_field((string) ($record['registryStatus'] ?? 'UNVERIFIED')));
        if (!$id || strlen($id) > 80 || !preg_match('/^[A-Z_]{2,40}$/D', $status) || isset($providerIds[$id])) {
            return msw_hrb_error('msw_hrb_provider', 'Invalid or duplicate provider entry', 422);
        }
        $providerIds[$id] = true;
        $providers[] = array(
            'id' => $id,
            'group' => sanitize_key((string) ($record['group'] ?? 'other')),
            'kind' => sanitize_key((string) ($record['kind'] ?? 'unknown')),
            'registryStatus' => $status,
            'hold' => !empty($record['hold']),
            'keyReported' => !empty($record['keyReported']),
        );
    }
    $routes = array();
    $routeIds = array();
    foreach ($rawRoutes as $route) {
        if (!is_array($route)) { return msw_hrb_error('msw_hrb_route', 'Route must be an object', 422); }
        $id = sanitize_key((string) ($route['id'] ?? ''));
        $status = strtoupper(sanitize_text_field((string) ($route['status'] ?? 'ERROR')));
        if (!$id || strlen($id) > 100 || !preg_match('/^[A-Z_]{2,40}$/D', $status) || isset($routeIds[$id])) {
            return msw_hrb_error('msw_hrb_route', 'Invalid or duplicate runtime route', 422);
        }
        $routeIds[$id] = true;
        $routes[] = array(
            'id' => $id,
            'provider' => sanitize_key((string) ($route['provider'] ?? '')),
            'model' => substr(sanitize_text_field((string) ($route['model'] ?? '')), 0, 100),
            'tier' => sanitize_key((string) ($route['tier'] ?? 'unknown')),
            'status' => $status,
            'reason' => substr(sanitize_text_field((string) ($route['reason'] ?? '')), 0, 100),
            'lastTest' => substr(sanitize_text_field((string) ($route['lastTest'] ?? '')), 0, 40),
        );
    }
    $rawHermes = is_array($input['hermes'] ?? null) ? $input['hermes'] : array();
    $snapshot = array(
        'schema' => MSW_HRB_SCHEMA,
        'registryRevision' => (int) $revision,
        'lastAckRevision' => (int) $ack,
        'observedAtUtc' => gmdate('Y-m-d\TH:i:s\Z', $seen),
        'source' => sanitize_key((string) ($input['source'] ?? 'operator_mcp_audit')),
        'hermes' => array(
            'status' => substr(sanitize_text_field((string) ($rawHermes['status'] ?? 'unknown')), 0, 50),
            'routerVersion' => substr(sanitize_text_field((string) ($rawHermes['routerVersion'] ?? 'unknown')), 0, 30),
            'lastSeen' => substr(sanitize_text_field((string) ($rawHermes['lastSeen'] ?? '')), 0, 40),
            'paidDailyLimitUsd' => max(0, min(1000, (float) ($rawHermes['paidDailyLimitUsd'] ?? 0))),
            'spentUsd' => max(0, min(1000, (float) ($rawHermes['spentUsd'] ?? 0))),
            'reservedUsd' => max(0, min(1000, (float) ($rawHermes['reservedUsd'] ?? 0))),
        ),
        'providers' => $providers,
        'routes' => $routes,
    );
    $old = get_option(MSW_HRB_OPTION, array());
    if (is_array($old) && !empty($old['digestSha256'])) {
        $oldRev = (int) ($old['registryRevision'] ?? -1);
        $oldTime = strtotime((string) ($old['observedAtUtc'] ?? '')) ?: 0;
        if ($revision < $oldRev || ($revision === $oldRev && $seen < $oldTime)) {
            return msw_hrb_error('msw_hrb_stale', 'Older snapshot rejected', 409);
        }
    }
    $encoded = wp_json_encode($snapshot, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if (!is_string($encoded) || strlen($encoded) > 128000) {
        return msw_hrb_error('msw_hrb_encode', 'Snapshot serialization refused', 422);
    }
    $snapshot['digestSha256'] = hash('sha256', $encoded);
    update_option(MSW_HRB_OPTION, $snapshot, false);
    return rest_ensure_response(array(
        'stored' => true,
        'registryRevision' => $revision,
        'lastAckRevision' => $ack,
        'providerRecords' => count($providers),
        'runtimeRoutes' => count($routes),
        'digestSha256' => $snapshot['digestSha256'],
        'note' => 'Metadata sync only; no API credentials or provider calls',
    ));
}
add_action('rest_api_init', function () {
    register_rest_route('msw-hermes-registry/v1', '/status', array(
        'methods' => WP_REST_Server::READABLE,
        'callback' => 'msw_hrb_status',
        'permission_callback' => 'msw_hrb_can_read',
    ));
    register_rest_route('msw-hermes-registry/v1', '/snapshot', array(
        array('methods' => WP_REST_Server::READABLE, 'callback' => 'msw_hrb_snapshot', 'permission_callback' => 'msw_hrb_can_read'),
        array('methods' => WP_REST_Server::CREATABLE, 'callback' => 'msw_hrb_post', 'permission_callback' => 'msw_hrb_can_write'),
    ));
});

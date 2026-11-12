<?php
/** Fail closed for new auxiliary claims when account telemetry is unsafe. */
function mswue_aux_origin_safe(string $action, string $node, callable $reader, int $now): bool {
    if ($action !== 'claim' || $node !== 'msw-render-auxseo-20261107-2032') return true;
    try { $sample = $reader(); } catch (Throwable) { return false; }
    if (!is_array($sample) || ($sample['schema'] ?? '') !== 'msw.render.account-health.v1'
        || ($sample['state'] ?? '') !== 'fresh' || !is_int($sample['measured_at'] ?? null)) return false;
    $age = $now - $sample['measured_at'];
    if ($age < 0 || $age > 90) return false;
    foreach (['cpu_percent', 'physical_memory_percent'] as $field) {
        $n = $sample[$field] ?? null;
        if ((!is_int($n) && !is_float($n)) || !is_finite((float)$n) || $n < 0 || $n >= 70) return false;
    }
    return true;
}

/** Reuse the already deployed, source-pinned account-only cPanel reader. */
function mswue_aux_account_health(): array {
    $helper = __DIR__ . '/msw-render-account-health.php';
    if (is_link($helper) || !is_file($helper)) return [];
    $sha = @hash_file('sha256', $helper);
    if (!is_string($sha) || !hash_equals('84cd15e106644430ad4a4b6446b89aa083afdd7716aa319eeafcd45b59f08fbe', $sha)) return [];
    if (!defined('MSW_ACCOUNT_HEALTH_TEST')) define('MSW_ACCOUNT_HEALTH_TEST', true);
    require_once $helper;
    $root = realpath(dirname(__DIR__, 3));
    if (!$root) return [];
    $public = dirname($root);
    if (basename($public) !== 'public_html') return [];
    $private = dirname($public) . '/.msw-cpanel-direct';
    if (is_link($private) || !is_dir($private)) return [];
    return msw_render_account_health_read($private . '/resource-guard.php', time());
}

if (defined('MSW_AUX_ORIGIN_PRESSURE_TEST')) return;
// Worker traffic intentionally avoids themes, WooCommerce and legacy plugin bootstraps.
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(405);
    exit;
}
if ((int) ($_SERVER['CONTENT_LENGTH'] ?? 0) > 1100000) {
    http_response_code(413);
    exit;
}
define('SHORTINIT', true);
$bootstrap = dirname(__DIR__, 3) . '/wp-load.php';
if (!is_file($bootstrap)) {
    http_response_code(503);
    exit;
}
require $bootstrap;
require_once __DIR__ . '/includes/RemoteStore.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, private');
header('X-Content-Type-Options: nosniff');
$main = basename(__DIR__) . '/msw-unified-engine.php';
if (!in_array($main, (array) get_option('active_plugins', []), true)) {
    http_response_code(503);
    echo '{"error":"engine_inactive"}';
    exit;
}
$raw = file_get_contents('php://input', false, null, 0, 1100001);
if (!is_string($raw) || strlen($raw) > 1100000) {
    http_response_code(413);
    exit;
}
$action = (string) ($_SERVER['HTTP_X_MSWUE_ACTION'] ?? '');
$headers = ['node' => (string) ($_SERVER['HTTP_X_MSWUE_NODE'] ?? ''), 'timestamp' => (string) ($_SERVER['HTTP_X_MSWUE_TIMESTAMP'] ?? ''), 'nonce' => (string) ($_SERVER['HTTP_X_MSWUE_NONCE'] ?? ''), 'signature' => (string) ($_SERVER['HTTP_X_MSWUE_SIGNATURE'] ?? '')];
$node = \MSW\UnifiedEngine\RemoteStore::authorise($action, $raw, $headers);
if (is_wp_error($node)) {
    $data = $node->get_error_data();
    http_response_code((int) ($data['status'] ?? 401));
    echo json_encode(['error' => $node->get_error_code()]);
    exit;
}
$data = json_decode($raw, true, 32);
if (!is_array($data)) {
    http_response_code(422);
    echo '{"error":"invalid_json"}';
    exit;
}
// Other workers, receipt/heartbeat/status calls and in-flight work are untouched.
if (!mswue_aux_origin_safe($action, $node, 'mswue_aux_account_health', time())) {
    header('Retry-After: 300');
    http_response_code(503);
    echo '{"error":"aux_origin_pressure_or_unavailable"}';
    exit;
}
try {
    $result = match ($action) {
        'claim' => \MSW\UnifiedEngine\RemoteStore::claim($node, (array) ($data['capabilities'] ?? [])),
        'heartbeat' => \MSW\UnifiedEngine\RemoteStore::heartbeat($node, $data),
        'complete' => \MSW\UnifiedEngine\RemoteStore::complete($node, $data),
        'status' => \MSW\UnifiedEngine\RemoteStore::receipt($node, (string) ($data['job_id'] ?? '')),
    };
    if (is_wp_error($result)) {
        $errorData = $result->get_error_data();
        http_response_code((int) ($errorData['status'] ?? 400));
        echo json_encode(['error' => $result->get_error_code()]);
    } else {
        echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
} catch (Throwable) {
    http_response_code(500);
    echo '{"error":"worker_transport_failed"}';
}

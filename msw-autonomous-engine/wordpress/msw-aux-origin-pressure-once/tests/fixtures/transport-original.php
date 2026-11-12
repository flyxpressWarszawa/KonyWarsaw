<?php
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

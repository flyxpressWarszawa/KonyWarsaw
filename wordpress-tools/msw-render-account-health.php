<?php
/** Signed, read-only account resource telemetry for the existing Render node. */
declare(strict_types=1);

function msw_render_account_health_parse(string $raw, int $now): array {
    $unavailable = ['schema'=>'msw.render.account-health.v1','state'=>'unavailable'];
    $prefix = "<?php http_response_code(404); exit; ?>\n";
    if (strlen($raw) > 524288 || !str_starts_with($raw, $prefix)) return $unavailable;
    $data = json_decode(substr($raw, strlen($prefix)), true);
    $t = is_array($data) ? ($data['last_result']['telemetry'] ?? null) : null;
    if (!is_array($t) || ($t['fresh'] ?? null) !== true || ($t['scope'] ?? '') !== 'account_only'
        || !is_string($t['source'] ?? null) || !str_starts_with($t['source'], 'cPanel ResourceUsage/get_usages')
        || !is_string($t['measured_at'] ?? null)) return $unavailable;
    $at = strtotime($t['measured_at']);
    $cpu = $t['cpu_percent'] ?? null; $ram = $t['physical_memory_percent'] ?? null;
    if (!is_int($at) || $at > $now || $now - $at > 90 || !is_numeric($cpu) || !is_numeric($ram)) return $unavailable;
    $cpu = (float)$cpu; $ram = (float)$ram;
    if (!is_finite($cpu) || !is_finite($ram) || $cpu < 0 || $cpu > 100 || $ram < 0 || $ram > 100) return $unavailable;
    return ['schema'=>'msw.render.account-health.v1','state'=>'fresh','measured_at'=>$at,
            'cpu_percent'=>$cpu,'physical_memory_percent'=>$ram];
}

function msw_render_account_health_read(string $path, int $now): array {
    $unavailable = ['schema'=>'msw.render.account-health.v1','state'=>'unavailable'];
    if (is_link($path) || !is_file($path) || !is_readable($path)) return $unavailable;
    $size = @filesize($path);
    if (!is_int($size) || $size < 1 || $size > 524288) return $unavailable;
    $raw = @file_get_contents($path, false, null, 0, 524289);
    return is_string($raw) ? msw_render_account_health_parse($raw, $now) : $unavailable;
}

if (defined('MSW_ACCOUNT_HEALTH_TEST')) return;
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, private');
header('X-Content-Type-Options: nosniff');
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { http_response_code(405); exit; }
if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 2048) { http_response_code(413); exit; }
define('SHORTINIT', true);
try {
$root = realpath(dirname(__DIR__, 3));
if (!$root || !is_file($root.'/wp-load.php')) { http_response_code(503); exit; }
require $root.'/wp-load.php';
require_once __DIR__.'/includes/RemoteStore.php';
$raw = file_get_contents('php://input', false, null, 0, 2049);
if (!is_string($raw) || strlen($raw) > 2048) { http_response_code(413); exit; }
$body = json_decode($raw, true);
if (!is_array($body) || count($body) !== 1 || ($body['schema'] ?? '') !== 'msw.render.account-health.v1') {
    http_response_code(422); exit;
}
$headers = [];
foreach (['node','timestamp','nonce','signature'] as $name) $headers[$name] = (string)($_SERVER['HTTP_X_MSWUE_'.strtoupper($name)] ?? '');
$node = \MSW\UnifiedEngine\RemoteStore::authorise('status', $raw, $headers);
if (is_wp_error($node)) { http_response_code(401); exit; }
$config = \MSW\UnifiedEngine\RemoteStore::config();
$kinds = $config['nodes'][$node]['kinds'] ?? [];
if ($node !== 'msw-render-central-1' || !is_array($kinds) || !in_array('engine.v3.ingest', $kinds, true)) {
    http_response_code(403); exit;
}
$public = dirname($root); $home = dirname($public); $private = $home.'/.msw-cpanel-direct';
if (basename($public) !== 'public_html' || is_link($private) || !is_dir($private)) {
    echo json_encode(['schema'=>'msw.render.account-health.v1','state'=>'unavailable']); exit;
}
echo json_encode(msw_render_account_health_read($private.'/resource-guard.php', time()), JSON_UNESCAPED_SLASHES);
} catch (Throwable $error) {
    http_response_code(503);
    echo json_encode(['schema'=>'msw.render.account-health.v1','state'=>'unavailable']);
}

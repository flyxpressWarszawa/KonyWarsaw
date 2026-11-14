<?php
/* Offline test stubs only. No WordPress site, traffic or provider calls. */
declare(strict_types=1);
define('ABSPATH', __DIR__);
$GLOBALS['opts'] = [];
$GLOBALS['is_admin'] = false;
$GLOBALS['routes'] = [];

class WP_REST_Server {
    const READABLE = 'GET';
    const CREATABLE = 'POST';
}
class WP_REST_Request {
    private array $headers;
    private $body;
    public function __construct(array $body = [], array $headers = []) {
        $this->body = $body;
        $this->headers = $headers;
    }
    public function get_header($key) {
        return $this->headers[strtolower($key)] ?? '';
    }
    public function get_json_params() {
        return $this->body;
    }
}
class WP_Error {
    public string $code;
    public string $message;
    public array $data;
    public function __construct($code, $message, $data = []) {
        $this->code = $code;
        $this->message = $message;
        $this->data = $data;
    }
}
function current_user_can($cap) {
    return $cap === 'manage_options' && $GLOBALS['is_admin'];
}
function get_option($key, $default = false) {
    return $GLOBALS['opts'][$key] ?? $default;
}
function update_option($key, $value, $autoload = null) {
    $GLOBALS['opts'][$key] = $value;
    return true;
}
function add_action($name, $cb) {
    if ($name === 'rest_api_init') { $cb(); }
}
function register_rest_route($namespace, $route, $arguments) {
    $GLOBALS['routes'][$namespace.$route] = $arguments;
}
function rest_ensure_response($value) {
    return $value;
}
function sanitize_key($value) {
    return strtolower(preg_replace('/[^a-z0-9_-]/i', '', $value));
}
function sanitize_text_field($value) {
    return trim(strip_tags($value));
}
function wp_json_encode($value, $flags = 0) {
    return json_encode($value, $flags);
}
function verify($condition, $message) {
    if (!$condition) { fwrite(STDERR, "FAIL: ".$message."\n"); exit(1); }
}
function err_status($result, int $status) {
    return $result instanceof WP_Error && ($result->data['status'] ?? null) === $status;
}

require __DIR__ . '/msw-hermes-api-registry-bridge.php';

verify(count($GLOBALS['routes']) === 3, 'Registered three routes');
$blank = new WP_REST_Request();
verify(!msw_hrb_can_read($blank) && !msw_hrb_can_write($blank), 'Anonymous access denied');
verify(msw_hrb_admin_only() === false, 'Token issuance admin-only');
$GLOBALS['is_admin'] = true;
verify(msw_hrb_can_write($blank), 'Admin allowed');
$issued = msw_hrb_issue_token();
$token = $issued['token'];
verify(is_string($token) && strlen($token) === 64, 'Issued strong 256-bit token');
verify(get_option(MSW_HRB_TOKEN_HASH_OPTION) === hash('sha256', $token), 'Only hash stored');
verify(get_option(MSW_HRB_TOKEN_HASH_OPTION) !== $token, 'No plaintext token stored');

$GLOBALS['is_admin'] = false;
$auth = ['x-msw-registry-token' => $token];
$req = new WP_REST_Request([], $auth);
verify(msw_hrb_can_read($req) && msw_hrb_can_write($req), 'Scoped token authorized');
verify(!msw_hrb_admin_only(), 'Scoped token not admin');
verify(!msw_hrb_can_write(new WP_REST_Request([], ['x-msw-registry-token'=> str_repeat('0',64)])), 'Wrong token rejected');

$now = gmdate('Y-m-d\TH:i:s\Z');
$payload = [
    'schema' => MSW_HRB_SCHEMA,
    'registryRevision' => 119,
    'lastAckRevision' => 119,
    'observedAtUtc' => $now,
    'source' => 'hermes_staging_private_checkpoint',
    'hermes' => [
        'status' => 'CONNECTED',
        'routerVersion' => '2.1.7',
        'lastSeen' => $now,
        'paidDailyLimitUsd' => 0.3,
        'spentUsd' => 0.28,
        'reservedUsd' => 0.008,
    ],
    'providers' => [['id' => 'openai', 'registryStatus' => 'KEY_REPORTED', 'keyReported' => true, 'apiSecret' => 'never_store']],
    'routes' => [['id' => 'openai-gpt5-mini', 'provider' => 'openai', 'model' => 'gpt-5-mini', 'status' => 'READY', 'secret' => 'never_store']],
];
$stored = msw_hrb_post(new WP_REST_Request($payload, $auth));
verify(is_array($stored) && $stored['stored'] === true, 'Scoped POST stored');
verify($stored['providerRecords'] === 1 && $stored['runtimeRoutes'] === 1, 'Counts correct');
$status = msw_hrb_status();
verify($status['readyRoutes'] === 1 && $status['stale'] === false, 'Live status accurate');
verify($status['scopedTokenConfigured'] === true, 'Hash-backed token configured');
verify($status['digestSha256'] === $stored['digestSha256'], 'SHA-256 readback');
$snapshot = msw_hrb_snapshot()['snapshot'];
$raw = json_encode($snapshot);
verify(!str_contains($raw, 'never_store'), 'Incoming secret fields removed');
verify(!str_contains($raw, 'apiSecret'), 'Secret key not persisted');

$rate = msw_hrb_post(new WP_REST_Request($payload, $auth));
verify(err_status($rate,429), 'Scoped rate limit active');
$GLOBALS['opts'][MSW_HRB_LAST_TOKEN_WRITE] = time() - 60;
$stale = $payload;
$stale['hermes']['lastSeen'] = gmdate('Y-m-d\TH:i:s\Z', time() - 4000);
verify(err_status(msw_hrb_post(new WP_REST_Request($stale,$auth)),409), 'Stale Hermes heartbeat rejected');
$dup = $payload;
$dup['providers'][] = $dup['providers'][0];
verify(err_status(msw_hrb_post(new WP_REST_Request($dup,$auth)),422), 'Duplicate provider rejected');
$broken = $payload; $broken['schema'] = 'invalid';
verify(err_status(msw_hrb_post(new WP_REST_Request($broken,$auth)),422), 'Incorrect schema rejected');
$old = $payload; $old['registryRevision'] = 118; $old['lastAckRevision']=118;
verify(err_status(msw_hrb_post(new WP_REST_Request($old,$auth)),409), 'Revision regression rejected');
$ack = $payload; $ack['lastAckRevision'] = 118;
verify(err_status(msw_hrb_post(new WP_REST_Request($ack,$auth)),409), 'ACK regression rejected');
verify(msw_hrb_status()['digestSha256'] === $stored['digestSha256'], 'No mutation after failed writes');

$GLOBALS['is_admin'] = true;
$issued2 = msw_hrb_issue_token();
$GLOBALS['is_admin'] = false;
verify(!msw_hrb_can_read($req), 'Previous token revoked by rotation');
verify(msw_hrb_can_read(new WP_REST_Request([], ['x-msw-registry-token'=>$issued2['token']])), 'Rotated token usable');
verify(get_option(MSW_HRB_TOKEN_HASH_OPTION) !== $issued2['token'], 'Second token not stored in plaintext');
fwrite(STDOUT,"PASS: WordPress Hermes bridge scoped-auth, sanitization, rate limit, heartbeat, ACK/registry monotonicity, rotation\n");

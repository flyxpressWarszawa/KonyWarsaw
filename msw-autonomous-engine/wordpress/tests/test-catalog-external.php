<?php
namespace MSW\UnifiedEngine {
    class RemoteStore {
        public static $node = 'msw-render-central-1';
        public static function authorise($action, $raw, $headers) { return self::$node; }
    }
}
namespace {
define('ABSPATH', __DIR__ . '/');
define('WP_PLUGIN_DIR', __DIR__ . '/absent');
$options = array('mswt500_job_v1' => array('actor' => 7, 'state' => 'running', 'mode' => 'apply', 'cursor' => 17, 'errors' => 0));
$current = 0;
class WP_Error { public function __construct(public $code, public $message, public $data = array()) {} public function get_error_code() { return $this->code; } }
class WP_REST_Response { public function __construct(public $data, public $status = 200, $headers = array()) {} }
class WP_REST_Request {
    public $body = '';
    public function __construct($method = '') {}
    public function set_header($key, $value) {}
    public function set_body($value) { $this->body = $value; }
    public function get_body() { return $this->body; }
    public function get_json_params() { return json_decode($this->body, true); }
    public function get_header($key) { return ''; }
}
function add_action(...$args) {}
function home_url() { return 'https://mobilny-serwis-warszawa.pl'; }
function wp_parse_url($url, $part) { return parse_url($url, $part); }
function current_user_can($cap) { return $GLOBALS['current'] === 7; }
function user_can($id, $cap) { return $id === 7; }
function get_current_user_id() { return $GLOBALS['current']; }
function wp_set_current_user($id) { $GLOBALS['current'] = $id; }
function get_option($key, $default = null) { return $GLOBALS['options'][$key] ?? $default; }
function update_option($key, $value, ...$args) { $GLOBALS['options'][$key] = $value; return true; }
function delete_option($key) { unset($GLOBALS['options'][$key]); return true; }
function add_option($key, $value, ...$args) { if (isset($GLOBALS['options'][$key])) return false; $GLOBALS['options'][$key] = $value; return true; }
function wp_generate_uuid4() { return 'test-lease'; }
function wp_json_encode($value) { return json_encode($value); }
function is_wp_error($value) { return $value instanceof WP_Error; }
class MSW_Tech_500 {
    public static function catalog() { return array_fill(0, 500, array()); }
    public static function tick() { $GLOBALS['options']['mswt500_job_v1']['cursor']++; }
}
require $argv[1] ?? dirname(__DIR__) . '/msw-catalog-recovery/msw-catalog-recovery.php';
function request($operation, $values = array()) { $r = new WP_REST_Request('POST'); $r->set_body(json_encode(array_merge(array('schema' => 'msw.catalog.external.v1', 'operation' => $operation), $values))); return $r; }
function check($condition, $label) { if (!$condition) throw new \RuntimeException($label); echo "PASS $label\n"; }
$read = request('status');
check(MSW_Catalog_External_20261105::permission($read) === true, 'registered signed node accepted');
\MSW\UnifiedEngine\RemoteStore::$node = 'other-node';
check(is_wp_error(MSW_Catalog_External_20261105::permission($read)), 'other node rejected');
\MSW\UnifiedEngine\RemoteStore::$node = new WP_Error('bad_signature', 'bad');
check(is_wp_error(MSW_Catalog_External_20261105::permission($read)), 'signature failure preserved');
\MSW\UnifiedEngine\RemoteStore::$node = 'msw-render-central-1';
$result = MSW_Catalog_External_20261105::dispatch(request('catalog', array('expected_cursor' => 17)));
check($result instanceof WP_REST_Response && $result->data['data']['catalog']['cursor'] === 18, 'one product committed and read back');
check($current === 0 && !isset($options['msw_catalog_external_lease_v1']), 'actor and lease restored');
check(get_option('msw_catalog_external_owner_v1') === 'msw-render-central-1', 'existing background yields to external owner');
$result = MSW_Catalog_External_20261105::dispatch(request('catalog', array('expected_cursor' => 17)));
check(is_wp_error($result) && $options['mswt500_job_v1']['cursor'] === 18, 'stale cursor cannot repeat write');
$result = MSW_Catalog_External_20261105::dispatch(request('catalog', array('expected_cursor' => 18, 'arbitrary_route' => '/wp/v2/posts')));
check(is_wp_error($result), 'arbitrary fields rejected');
$options['mswt500_job_v1']['errors'] = 1;
check(is_wp_error(MSW_Catalog_External_20261105::dispatch(request('catalog', array('expected_cursor' => 18)))), 'import errors stop writes');
$options['mswt500_job_v1']['actor'] = 99;
check(is_wp_error(MSW_Catalog_External_20261105::dispatch($read)), 'revoked actor stops access');
}

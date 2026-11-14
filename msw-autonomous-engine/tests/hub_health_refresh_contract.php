<?php
/** Offline contract checks for staged HUB refresh. No WordPress/network involved. */
error_reporting(E_ALL);
define('ABSPATH', '/');
class WP_Error {
    private $code;
    function __construct($code, $message, $data = []) { $this->code = $code; }
    function get_error_code() { return $this->code; }
}
function is_wp_error($v) { return $v instanceof WP_Error; }
function sanitize_text_field($v) { return trim(strip_tags((string) $v)); }
$GLOBALS['test_case'] = 'success';
$GLOBALS['admin_allowed'] = true;
$GLOBALS['seen_refresh'] = [];
function current_user_can($cap) { return $cap === 'manage_options' && $GLOBALS['admin_allowed'] === true; }
function mswh_connection_status($refresh = false) {
    $GLOBALS['seen_refresh'][] = $refresh;
    if ($GLOBALS['test_case'] === 'error') return new WP_Error('mswh_offline', 'Backend unavailable');
    $state = ['connected'=>true, 'cached_only'=>false, 'not_tested'=>false,
              'connection_state'=>'tested', 'version'=>'0.2.0', 'providers'=>[]];
    if ($GLOBALS['test_case'] === 'cached') $state['cached_only'] = true;
    if ($GLOBALS['test_case'] === 'not_tested') $state['not_tested'] = true;
    if ($GLOBALS['test_case'] === 'disconnected') $state['connected'] = false;
    return $state;
}
require_once __DIR__ . '/../integrations/python-hub-health-live/class-mswdc-engines.php';
$checked = 0;
function check($test_case, $expected, $expected_refresh, $admin = true) {
    global $checked;
    $GLOBALS['test_case'] = $test_case;
    $GLOBALS['admin_allowed'] = $admin;
    $GLOBALS['seen_refresh'] = [];
    $result = MSWDC_Engines::operate(['operation'=>'hub_health']);
    $status = is_wp_error($result) ? $result->get_error_code() : ($result['status'] ?? '');
    if ($status !== $expected || $GLOBALS['seen_refresh'] !== $expected_refresh) {
        fwrite(STDERR, "FAILED: $test_case expected=$expected got=$status refresh=" .
                      json_encode($GLOBALS['seen_refresh']) . "\n");
        exit(1);
    }
    $checked++;
}
check('success', 'completed', [true]);
check('cached', 'mswdc_hub_unverified', [true]);
check('not_tested', 'mswdc_hub_unverified', [true]);
check('disconnected', 'mswdc_hub_unverified', [true]);
check('error', 'mswdc_hub_failure', [true]);
check('success', 'mswdc_forbidden', [], false);
echo "HUB signed refresh PHP checks: $checked/6 PASS\n";

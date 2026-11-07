<?php
// Run each case in a separate PHP process because constants cannot be reset.
$case = $argv[1] ?? '';
$plugin = $argv[2] ?? '';
define('ABSPATH', __DIR__ . '/');
$GLOBALS['test_last'] = 0;
$GLOBALS['test_loaded'] = 1;
$GLOBALS['test_actions'] = [];
$GLOBALS['test_updates'] = [];
function get_option($name, $default = false) { return $GLOBALS['test_last']; }
function add_action($hook, $callback, $priority = 10) { $GLOBALS['test_actions'][$hook] = $callback; }
function did_action($hook) { return $GLOBALS['test_loaded']; }
function update_option($name, $value, $autoload = null) { $GLOBALS['test_updates'][] = [$name, $value, $autoload]; }

if (in_array($case, ['fresh', 'existing_false'], true)) $GLOBALS['test_last'] = time() - 10;
if ($case === 'stale') $GLOBALS['test_last'] = time() - 181;
if ($case === 'future') $GLOBALS['test_last'] = time() + 60;
if ($case === 'existing_false') define('DISABLE_WP_CRON', false);
if (in_array($case, ['curl', 'render', 'visitor', 'not_loaded'], true)) define('DOING_CRON', true);
if ($case === 'curl' || $case === 'not_loaded' || $case === 'frontend_curl') $_SERVER['HTTP_USER_AGENT'] = 'curl/8.1.2';
if ($case === 'render') $_SERVER['HTTP_USER_AGENT'] = 'MSW-External-Scheduler/1.0';
if ($case === 'visitor') $_SERVER['HTTP_USER_AGENT'] = 'WordPress/7.1.4';
if ($case === 'not_loaded') $GLOBALS['test_loaded'] = 0;
require $plugin;

if (in_array($case, ['curl', 'render', 'visitor', 'not_loaded', 'frontend_curl'], true)) {
    if (isset($GLOBALS['test_actions']['shutdown'])) ($GLOBALS['test_actions']['shutdown'])();
    $want_update = in_array($case, ['curl', 'render'], true);
    $pass = (count($GLOBALS['test_updates']) === ($want_update ? 1 : 0));
    if ($want_update) $pass = $pass && $GLOBALS['test_updates'][0][0] === 'msw_cron_guard_external_ok'
        && $GLOBALS['test_updates'][0][2] === true;
} elseif ($case === 'fresh') {
    $pass = defined('DISABLE_WP_CRON') && DISABLE_WP_CRON === true;
} elseif ($case === 'existing_false') {
    $pass = defined('DISABLE_WP_CRON') && DISABLE_WP_CRON === false;
} else {
    $pass = !defined('DISABLE_WP_CRON');
}
echo $case . ': ' . ($pass ? 'PASS' : 'FAIL') . PHP_EOL;
exit($pass ? 0 : 1);

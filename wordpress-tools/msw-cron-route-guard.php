<?php
/**
 * Plugin Name: MSW Cron Route Guard
 * Description: Prefer the existing independent cron route while it is healthy.
 * Version: 1.0.0
 */

defined('ABSPATH') || exit;

// A visitor never updates this marker. Only a completed, non-fatal direct
// wp-cron request from an existing scheduler qualifies as a recent observation.
$msw_cron_guard_agent = isset($_SERVER['HTTP_USER_AGENT']) ? (string) $_SERVER['HTTP_USER_AGENT'] : '';
$msw_cron_guard_scheduler = preg_match('/\Acurl\/[0-9]+(?:\.[0-9]+){1,3}\z/D', $msw_cron_guard_agent) === 1
    || $msw_cron_guard_agent === 'MSW-External-Scheduler/1.0';
if (defined('DOING_CRON') && DOING_CRON && $msw_cron_guard_scheduler) {
    add_action('shutdown', static function (): void {
        $last_error = error_get_last();
        $fatal_types = [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR];
        if (!did_action('wp_loaded') || (is_array($last_error) && in_array($last_error['type'], $fatal_types, true))) {
            return;
        }
        update_option('msw_cron_guard_external_ok', time(), true);
    }, PHP_INT_MAX);
}

// Fail open: when independent scheduling is stale or missing, leave the core
// setting untouched, allowing WordPress to spawn cron on visits as before.
$msw_cron_guard_last = (int) get_option('msw_cron_guard_external_ok', 0);
$msw_cron_guard_now = time();
if (!defined('DISABLE_WP_CRON') && $msw_cron_guard_last > 0
    && $msw_cron_guard_last <= $msw_cron_guard_now
    && $msw_cron_guard_now - $msw_cron_guard_last <= 180) {
    define('DISABLE_WP_CRON', true);
}
unset($msw_cron_guard_agent, $msw_cron_guard_scheduler, $msw_cron_guard_last, $msw_cron_guard_now);

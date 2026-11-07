<?php
/**
 * Plugin Name: MSW Search & Learn — Health Endpoint
 * Plugin URI: https://mobilny-serwis-warszawa.pl/
 * Description: Dodaje prywatny, wyłącznie odczytowy endpoint zdrowia MSW AI Search & Learn i test WordPress Site Health. Bez API dostawców, zapisów i publikacji.
 * Version: 1.0.1
 * Requires at least: 6.4
 * Requires PHP: 8.0
 * Author: MSW AI
 * Text Domain: msw-search-learn-health
 */

if (!defined('ABSPATH')) {
    exit;
}

if (!class_exists('MSW_Search_Learn_Health_Endpoint', false)) {
    final class MSW_Search_Learn_Health_Endpoint {
        public const VERSION = '1.0.1';
        public const ROUTE = '/msw-ai/v1/health';

        public static function register(): void {
            // Registration at 100 allows the source module to claim /health first in a future release.
            $existing = rest_get_server()->get_routes();
            if (isset($existing[self::ROUTE])) {
                return;
            }

            register_rest_route('msw-ai/v1', '/health', array(
                'methods' => WP_REST_Server::READABLE,
                'callback' => array(__CLASS__, 'handle'),
                'permission_callback' => static function (): bool {
                    return current_user_can('manage_options');
                },
                'args' => array(
                    'deep' => array(
                        'type' => 'boolean',
                        'default' => false,
                        'sanitize_callback' => 'rest_sanitize_boolean',
                    ),
                ),
            ));
        }

        private static function table_exists(string $table): bool {
            global $wpdb;
            if ($table === '' || !is_object($wpdb) || !method_exists($wpdb, 'prepare')) {
                return false;
            }
            // Escape SQL LIKE wildcards: table prefix often contains underscores.
            $escaped = $wpdb->esc_like($table);
            $sql = $wpdb->prepare('SHOW TABLES LIKE %s', $escaped);
            $found = $wpdb->get_var($sql);
            return is_string($found) && hash_equals($table, $found);
        }

        private static function detected_table(string $name): bool {
            if (!function_exists($name)) {
                return false;
            }
            try {
                $table = call_user_func($name);
                return is_string($table) && self::table_exists($table);
            } catch (Throwable $e) {
                return false;
            }
        }

        /**
         * Only local, read-only checks. No model provider API, no user query and no database writes.
         * The optional deep probe uses a fixed, generic phrase and returns only a boolean.
         */
        public static function snapshot(bool $deep = false): array {
            $registered_routes = rest_get_server()->get_routes();
            $checks = array(
                'search_route_registered' => isset($registered_routes['/msw-ai/v1/search']),
                'search_callback_loaded' => function_exists('mswai_api_search'),
                'catalog_engine_loaded' => function_exists('mswai_catalog_search'),
                'learning_callback_loaded' => function_exists('mswai_api_learn'),
                'knowledge_writer_loaded' => function_exists('mswai_knowledge_observe'),
                'statistics_table_exists' => self::detected_table('mswai_table'),
                'costs_table_exists' => self::detected_table('mswai_cost_table'),
                'knowledge_table_exists' => self::detected_table('mswai_knowledge_table'),
                'daily_cron_scheduled' => (bool) wp_next_scheduled('mswai_daily'),
                'editorial_cron_scheduled' => (bool) wp_next_scheduled('mswai_editorial_half_hour'),
            );

            $critical_keys = array(
                'search_route_registered', 'search_callback_loaded', 'catalog_engine_loaded',
                'learning_callback_loaded', 'knowledge_writer_loaded',
                'statistics_table_exists', 'costs_table_exists', 'knowledge_table_exists',
            );
            $critical_ok = true;
            foreach ($critical_keys as $key) {
                if (!$checks[$key]) {
                    $critical_ok = false;
                }
            }

            if ($deep) {
                $probe_ok = false;
                if ($checks['catalog_engine_loaded']) {
                    try {
                        // Direct function call, not the REST /search route. Never emits or records a visitor query.
                        $probe = mswai_catalog_search('serwis zamka', 1, array());
                        $probe_ok = is_array($probe);
                    } catch (Throwable $e) {
                        $probe_ok = false;
                    }
                }
                $checks['readonly_catalog_probe_passed'] = $probe_ok;
                $critical_ok = $critical_ok && $probe_ok;
            }

            $cron_ok = $checks['daily_cron_scheduled'] && $checks['editorial_cron_scheduled'];
            $status = !$critical_ok ? 'unavailable' : ($cron_ok ? 'ok' : 'degraded');
            $editorial_ready = null;
            if (function_exists('mswai_editorial_ready')) {
                try {
                    $editorial_ready = (bool) mswai_editorial_ready();
                } catch (Throwable $e) {
                    $editorial_ready = false;
                }
            }

            return array(
                'engine' => 'search',
                'module' => 'MSW AI Search & Learn',
                'version' => defined('MSWAI_VERSION') ? (string) MSWAI_VERSION : null,
                'health_adapter_version' => self::VERSION,
                'status' => $status,
                'ok' => $status === 'ok',
                'ready' => $critical_ok,
                'checked_at_utc' => gmdate('c'),
                'checks' => $checks,
                'optional' => array(
                    'editorial_ai_configured' => $editorial_ready,
                    'external_ai_calls_tested' => false,
                    'cron_executions_tested' => false,
                    'search_http_request_tested' => false,
                ),
                'mode' => 'local_read_only',
            );
        }

        public static function handle(WP_REST_Request $request): WP_REST_Response {
            // Defense in depth. The route's permission_callback already checks this.
            if (!current_user_can('manage_options')) {
                $response = new WP_REST_Response(array('code' => 'mswsh_forbidden'), 403);
                $response->header('Cache-Control', 'private, no-store, max-age=0');
                return $response;
            }
            $data = self::snapshot((bool) $request->get_param('deep'));
            // Degraded means the core is ready, but an optional cron is missing.
            // Return 200 with status=degraded / ok=false in JSON, rather than
            // signalling a host outage to native health probes and backoff.
            $response = new WP_REST_Response($data, $data['status'] === 'unavailable' ? 503 : 200);
            $response->header('Cache-Control', 'private, no-store, max-age=0');
            return $response;
        }

        public static function admin_menu(): void {
            add_management_page(
                'MSW Search & Learn — Stan silnika',
                'MSW Search Health',
                'manage_options',
                'msw-search-health',
                array(__CLASS__, 'admin_page')
            );
        }

        public static function admin_page(): void {
            if (!current_user_can('manage_options')) {
                wp_die('Brak uprawnień.', 403);
            }
            $deep = isset($_GET['deep'], $_GET['_wpnonce'])
                && (string) $_GET['deep'] === '1'
                && wp_verify_nonce(sanitize_text_field(wp_unslash((string) $_GET['_wpnonce'])), 'mswsh_deep');
            $data = self::snapshot((bool) $deep);
            $notice = $data['status'] === 'ok' ? 'notice-success' : ($data['status'] === 'degraded' ? 'notice-warning' : 'notice-error');
            $link = wp_nonce_url(admin_url('tools.php?page=msw-search-health&deep=1'), 'mswsh_deep');
            echo '<div class="wrap"><h1>MSW AI Search & Learn — zdrowie silnika</h1>';
            echo '<div class="notice ' . esc_attr($notice) . '"><p><strong>Stan: ' . esc_html($data['status']) . '</strong> — lokalny odczyt bez wywołań zewnętrznych API.</p></div>';
            echo '<p>Moduł: ' . esc_html((string) ($data['version'] ?? 'nie wykryto')) . '; ostatni test: ' . esc_html($data['checked_at_utc']) . '.</p>';
            echo '<table class="widefat striped"><thead><tr><th>Kontrola</th><th>Wynik</th></tr></thead><tbody>';
            foreach ($data['checks'] as $check => $passed) {
                echo '<tr><td>' . esc_html($check) . '</td><td><strong>' . ($passed ? 'OK' : 'BRAK') . '</strong></td></tr>';
            }
            echo '</tbody></table>';
            echo '<p><a class="button button-primary" href="' . esc_url($link) . '">Sprawdź lokalnie wyszukiwarkę (deep test)</a></p>';
            echo '<p>Endpoint REST (administracyjny): <code>' . esc_html(rest_url('msw-ai/v1/health')) . '</code></p>';
            echo '<p>REST wymaga autoryzacji klienta i praw administratora. Samo wejście w adres przez przeglądarkę bez nagłówka X-WP-Nonce może zwrócić 401/403. Panel powyżej działa z poziomu administracji WP.</p>';
            echo '<p>Harmonogram zarejestrowany nie oznacza, że zdarzenie faktycznie zostało uruchomione. Wynik nie potwierdza łączności z OpenAI, Gemini ani innymi dostawcami.</p></div>';
        }

        public static function site_health(array $tests): array {
            $tests['direct']['msw_search_learn_health'] = array(
                'label' => 'MSW Search & Learn — health',
                'test' => static function (): array {
                    $data = self::snapshot(false);
                    $status = $data['status'];
                    $label = $status === 'ok'
                        ? 'MSW Search & Learn: testy lokalne OK'
                        : 'MSW Search & Learn: wymaga diagnostyki';
                    $failed = array();
                    foreach ($data['checks'] as $name => $passed) {
                        if (!$passed) {
                            $failed[] = $name;
                        }
                    }
                    $message = $status === 'ok'
                        ? 'Trasy, funkcje, podstawowe tabele i harmonogramy są dostępne. Nie oznacza to testu płatnego API.'
                        : 'Nieudane kontrole: ' . implode(', ', $failed);
                    return array(
                        'label' => $label,
                        'status' => $status === 'ok' ? 'good' : ($status === 'degraded' ? 'recommended' : 'critical'),
                        'badge' => array('label' => 'MSW AI', 'color' => 'blue'),
                        'description' => '<p>' . esc_html($message) . '</p>',
                        'actions' => '<p><a href="' . esc_url(rest_url('msw-ai/v1/health')) . '">Prywatny endpoint diagnostyczny</a></p>',
                        'test' => 'msw_search_learn_health',
                    );
                },
            );
            return $tests;
        }
    }

    add_action('rest_api_init', array('MSW_Search_Learn_Health_Endpoint', 'register'), 100);
    add_filter('site_status_tests', array('MSW_Search_Learn_Health_Endpoint', 'site_health'));
    add_action('admin_menu', array('MSW_Search_Learn_Health_Endpoint', 'admin_menu'));
}

// MSW NATIVE AUTONOMY CONTRACT 1.0.0
\add_action('plugins_loaded', static function (): void { if (\class_exists('MSW_Module_Autonomy', false)) \MSW_Module_Autonomy::register(__FILE__); }, 90);

// MSW AUTONOMY DISTRIBUTION 3.1.0


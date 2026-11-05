<?php
/**
 * Plugin Name: MSW AI Wspólny Silnik — WordPress, Render i Python
 * Description: Podpisany most współpracy, wspólna kolejka i potwierdzone wyniki istniejących silników MSW.
 * Version: 1.0.0
 * Requires PHP: 8.1
 * Author: Mobilny Serwis Warszawa
 */
defined('ABSPATH') || exit;

final class MSW_Render_Federation
{
    const SCHEMA = 'msw.federation.v1';

    public static function boot(): void
    {
        add_action('rest_api_init', [self::class, 'routes']);
        add_filter('msw_ai_federation_submit', [self::class, 'submit_filter'], 10, 4);
        add_action('mswue_remote_result', [self::class, 'completed'], 30, 4);
    }

    private static function error(string $code, int $status = 400): WP_Error
    {
        return new WP_Error('mswf_'.$code, 'MSW Federation: '.$code, ['status' => $status]);
    }

    public static function admin(): bool { return current_user_can('manage_options'); }

    public static function permission($request)
    {
        if (self::admin()) return true;
        $raw = $request->get_body();
        if (strlen($raw) > 20000 || !class_exists('MSW\\UnifiedEngine\\RemoteStore')) return self::error('unavailable', 503);
        $body = json_decode($raw, true);
        if (!is_array($body) || ($body['schema'] ?? '') !== self::SCHEMA) return self::error('schema', 403);
        $headers = [];
        foreach (['node','timestamp','nonce','signature'] as $field) $headers[$field] = $request->get_header('x-mswue-'.$field);
        $node = \MSW\UnifiedEngine\RemoteStore::authorise('status', $raw, $headers);
        if (is_wp_error($node)) return $node;
        $config = \MSW\UnifiedEngine\RemoteStore::config();
        $kind = ($body['operation'] ?? '') === 'generate' ? 'llm.generate' : 'runtime.catalog';
        if (!in_array($kind, $config['nodes'][$node]['kinds'] ?? [], true)) return self::error('scope', 403);
        return true;
    }

    public static function routes(): void
    {
        register_rest_route('msw-federation/v1', '/gateway', ['methods' => 'POST', 'permission_callback' => [self::class, 'permission'],
            'callback' => [self::class, 'gateway']]);
        register_rest_route('msw-federation/v1', '/submit', ['methods' => 'POST', 'permission_callback' => [self::class, 'admin'],
            'callback' => static fn($r) => self::submit((array)$r->get_json_params())]);
        register_rest_route('msw-federation/v1', '/result/(?P<id>[a-f0-9-]{36})', ['methods' => 'GET', 'permission_callback' => [self::class, 'admin'],
            'callback' => [self::class, 'result']]);
        register_rest_route('msw-federation/v1', '/status', ['methods' => 'GET', 'permission_callback' => [self::class, 'admin'],
            'callback' => static fn() => ['schema' => self::SCHEMA, 'version' => '1.0.0', 'queue_available' => class_exists('MSW\\UnifiedEngine\\RemoteStore'),
                'router_loaded' => class_exists('MSW_ChatGPT_Router'), 'python_adapter_loaded' => class_exists('MSWH_Engine_Adapters'),
                'last_result' => get_option('msw_federation_last_result', []), 'all_plugin_callbacks_automatically_migrated' => false]]);
    }

    public static function snapshot(): array
    {
        if (!function_exists('get_plugins')) require_once ABSPATH.'wp-admin/includes/plugin.php';
        $plugins = get_plugins();
        $active = get_option('active_plugins', []);
        $inventory = [];
        foreach ($plugins as $file => $data) $inventory[] = ['id' => $file, 'name' => $data['Name'], 'version' => $data['Version'], 'active' => in_array($file, $active, true)];
        $hub = ['reachable' => false, 'model_inference_verified' => false, 'providers' => []];
        if (function_exists('mswh_call')) {
            $health = mswh_call('/v1/admin/health', [], true, 5);
            if (!is_wp_error($health) && ($health['ok'] ?? false) === true) {
                $hub['reachable'] = true;
                $hub['version'] = sanitize_text_field((string)($health['version'] ?? ''));
                foreach (array_slice((array)($health['providers'] ?? []), 0, 30) as $provider) {
                    if (is_array($provider)) $hub['providers'][] = ['id' => sanitize_key((string)($provider['id'] ?? '')), 'state' => sanitize_key((string)($provider['state'] ?? ''))];
                }
            } else $hub['error'] = is_wp_error($health) ? $health->get_error_code() : 'health_unverified';
        } else $hub['error'] = 'hub_bridge_not_loaded';
        return ['origin' => home_url(), 'observed_at' => time(),
            'wordpress' => ['installed' => count($plugins), 'active' => count(array_intersect(array_keys($plugins), $active)), 'catalogued' => count($inventory)],
            'plugin_inventory' => $inventory, 'python_hub' => $hub,
            'server_engine_summary' => class_exists('MSWH_Engine_Adapters') ? MSWH_Engine_Adapters::summary() : 'not_available',
            'inventory_is_not_execution_proof' => true];
    }

    public static function gateway($request)
    {
        $p = (array)$request->get_json_params();
        $operation = $p['operation'] ?? '';
        $allowed = ['snapshot' => ['schema','operation'], 'public_context' => ['schema','operation','query'], 'generate' => ['schema','operation','task','text','max_tokens']];
        if (($p['schema'] ?? '') !== self::SCHEMA || !isset($allowed[$operation]) || array_diff(array_keys($p), $allowed[$operation])) return self::error('arguments');
        if ($operation === 'snapshot') $data = self::snapshot();
        elseif ($operation === 'public_context') {
            if (!is_string($p['query'] ?? null) || strlen($p['query']) > 6000) return self::error('query');
            $items = class_exists('MSWH_Engine_Adapters') ? MSWH_Engine_Adapters::lookup($p['query'], 'render_federation') : [];
            $data = ['items' => array_slice($items, 0, 3), 'scope' => 'live_exported_public_service_sources', 'private_cases_exported' => false];
        } else {
            if (!class_exists('MSW_ChatGPT_Router') || !is_string($p['text'] ?? null) || strlen($p['text']) > 10000
                || !in_array($p['task'] ?? '', ['technical','business','seo','article','review','voice'], true)
                || !is_int($p['max_tokens'] ?? null) || $p['max_tokens'] < 64 || $p['max_tokens'] > 3000) return self::error('model_arguments');
            $answer = MSW_ChatGPT_Router::generate($p['task'], $p['text'], ['max_output_tokens' => $p['max_tokens']]);
            if (is_wp_error($answer)) return self::error('model_'.$answer->get_error_code(), 502);
            $model = defined('MSW_CHATGPT_MODEL') ? (string)MSW_CHATGPT_MODEL : 'gpt-5-mini';
            $data = ['text' => $answer, 'model' => $model, 'provider' => 'openai_existing_router'];
        }
        return new WP_REST_Response(['schema' => self::SCHEMA, 'ok' => true, 'data' => $data], 200, ['Cache-Control' => 'no-store, private']);
    }

    public static function submit(array $p)
    {
        if (!class_exists('MSW\\UnifiedEngine\\RemoteStore') || array_diff(array_keys($p), ['prompt','task','max_tokens','use_knowledge','collaborate','request_id'])) return self::error('submit_arguments');
        if (!is_string($p['prompt'] ?? null) || !strlen(trim($p['prompt'])) || strlen($p['prompt']) > 6000) return self::error('prompt');
        $key = $p['request_id'] ?? '';
        if (!is_string($key) || !preg_match('/^[a-zA-Z0-9_.:-]{8,120}$/D', $key)) return self::error('request_id');
        unset($p['request_id']);
        return \MSW\UnifiedEngine\RemoteStore::enqueue('llm.generate', $p, 'federation:'.$key);
    }

    public static function submit_filter($previous, $task, $text, $context = [])
    {
        if ($previous !== null) return $previous;
        return self::submit(array_merge(array_intersect_key((array)$context, array_flip(['request_id','max_tokens','use_knowledge','collaborate'])), ['task' => $task, 'prompt' => $text]));
    }

    public static function result($request)
    {
        global $wpdb;
        $table = \MSW\UnifiedEngine\RemoteStore::table();
        $row = $wpdb->get_row($wpdb->prepare("SELECT id,kind,state,result,result_sha256 FROM {$table} WHERE id=%s", (string)$request['id']), ARRAY_A);
        if (!$row) return self::error('missing', 404);
        $verified = $row['result'] !== '' && hash_equals($row['result_sha256'], hash('sha256', $row['result']));
        return ['job_id' => $row['id'], 'state' => $row['state'], 'result_sha256' => $row['result_sha256'], 'digest_verified' => $verified,
            'result' => $verified ? json_decode($row['result'], true) : null];
    }

    public static function completed($kind, $id, $payload, $result): void
    {
        if ($kind !== 'llm.generate' || ($result['data']['schema'] ?? '') !== 'msw.federation.result.v1') return;
        update_option('msw_federation_last_result', ['job_id' => $id, 'at' => time(), 'task' => $result['data']['task'],
            'answer_sha256' => $result['data']['answer_sha256'], 'model' => $result['data']['model'], 'contributors' => $result['data']['contributors']], false);
        do_action('msw_ai_federation_result', $id, $result['data']);
    }
}
MSW_Render_Federation::boot();

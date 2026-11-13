<?php
/**
 * Plugin Name: MSW Hermes — Signed Read-only Queue Archive Export
 * Description: Opt-in, bounded read-only export to the already-paired Hermes node. Never claims or executes a job.
 * Version: 0.1.0
 * Requires PHP: 8.1
 */
if (!defined('ABSPATH')) exit;

final class MSW_Hermes_Queue_Archive_Export {
    private const SCHEMA = 'msw.hermes.queue-archive.v1';
    private const NODE = 'msw-render-central-1';
    private const LIMIT = 12;
    private const MAX_RESPONSE_BYTES = 700000;
    private const STATES = ['pending','running','failed','completed','uncertain'];

    public static function register(): void {
        register_rest_route('msw-hermes-queue/v1', '/export', [
            'methods' => 'POST',
            'permission_callback' => [__CLASS__, 'authorized'],
            'callback' => [__CLASS__, 'export'],
        ]);
    }

    public static function authorized(WP_REST_Request $request) {
        // Installation alone is deliberately inert. Requires explicit operator opt-in.
        if (get_option('msw_hermes_queue_export_enabled_v1', false) !== true)
            return new WP_Error('msw_hqa_disabled', 'Read-only archive export disabled.', ['status'=>503]);
        if (!is_ssl() || strlen($request->get_body()) > 2048)
            return new WP_Error('msw_hqa_transport', 'HTTPS and bounded request required.', ['status'=>403]);
        if (!class_exists('MSW\\UnifiedEngine\\RemoteStore'))
            return new WP_Error('msw_hqa_queue_unavailable', 'Existing transport unavailable.', ['status'=>503]);
        $headers = [
            'node' => (string)$request->get_header('X-MSWUE-Node'),
            'timestamp' => (string)$request->get_header('X-MSWUE-Timestamp'),
            'nonce' => (string)$request->get_header('X-MSWUE-Nonce'),
            'signature' => (string)$request->get_header('X-MSWUE-Signature'),
        ];
        if ($request->get_header('X-MSWUE-Action') !== 'status' || $headers['node'] !== self::NODE)
            return new WP_Error('msw_hqa_scope', 'Only paired Hermes node may export.', ['status'=>403]);
        // Reuse existing HMAC and nonce anti-replay check. It does not claim a lease.
        $authorized = \MSW\UnifiedEngine\RemoteStore::authorise('status', $request->get_body(), $headers);
        return is_wp_error($authorized) ? $authorized : ($authorized === self::NODE ? true : new WP_Error('msw_hqa_node', 'Node mismatch.', ['status'=>403]));
    }

    public static function export(WP_REST_Request $request) {
        global $wpdb;
        $body = json_decode($request->get_body(), true, 16);
        $allowed = ['state','snapshot_until','cursor_created_at','cursor_id','limit'];
        if (!is_array($body) || array_is_list($body) || array_diff(array_keys($body), $allowed)
            || count($body) !== count($allowed))
            return new WP_Error('msw_hqa_arguments', 'Invalid export request.', ['status'=>422]);
        $state = $body['state'];
        $until = $body['snapshot_until'];
        $after = $body['cursor_created_at'];
        $cursorId = $body['cursor_id'];
        $limit = $body['limit'];
        if (!is_string($state) || !in_array($state, self::STATES, true)
            || !is_int($until) || $until < 1700000000 || $until > time()+60
            || !is_int($after) || $after < 0 || $after > $until
            || !is_string($cursorId) || ($cursorId !== '' && !preg_match('/^[a-f0-9-]{36}$/D', $cursorId))
            || !is_int($limit) || $limit < 1 || $limit > self::LIMIT)
            return new WP_Error('msw_hqa_scope', 'Out of bounds cursor.', ['status'=>422]);
        $table = \MSW\UnifiedEngine\RemoteStore::table();
        // Indexed keyset scan: no OFFSET over a 28k row table.
        $query = $wpdb->prepare(
            "SELECT id,kind,payload,payload_sha256,job_key,state,attempts,created_at,updated_at FROM {$table} " .
            "WHERE state=%s AND created_at<=%d AND (created_at>%d OR (created_at=%d AND id>%s)) " .
            "ORDER BY created_at,id LIMIT %d",
            $state, $until, $after, $after, $cursorId, $limit
        );
        $rows = $wpdb->get_results($query, ARRAY_A);
        if ($wpdb->last_error !== '' || !is_array($rows))
            return new WP_Error('msw_hqa_database', 'Origin read failed.', ['status'=>503]);
        $records=[];
        $bytes=300;
        $cursor=['created_at'=>$after,'id'=>$cursorId];
        $finished=count($rows)<$limit;
        foreach ($rows as $row) {
            if (!is_string($row['payload']) || !hash_equals((string)$row['payload_sha256'], hash('sha256',$row['payload'])))
                return new WP_Error('msw_hqa_payload_digest', 'Origin row digest mismatch.', ['status'=>409]);
            $rec=$row;
            foreach (['attempts','created_at','updated_at'] as $key) $rec[$key]=(int)$rec[$key];
            $encoded=wp_json_encode($rec,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
            if (!is_string($encoded) || strlen($encoded) > self::MAX_RESPONSE_BYTES-300)
                return new WP_Error('msw_hqa_row_limit','Origin row too large.',['status'=>422]);
            if ($bytes+strlen($encoded)+1 > self::MAX_RESPONSE_BYTES) {
                $finished=false;
                break;
            }
            $records[]=$rec;
            $bytes+=strlen($encoded)+1;
            $cursor=['created_at'=>(int)$row['created_at'],'id'=>(string)$row['id']];
        }
        $result=['schema'=>self::SCHEMA,'state'=>$state,'snapshot_until'=>$until,
                 'records'=>$records,'next_cursor'=>$cursor,'finished'=>$finished,
                 'archive_only_no_execution'=>true];
        return new WP_REST_Response($result, 200, ['Cache-Control'=>'no-store, private',
                 'X-Content-Type-Options'=>'nosniff']);
    }
}
add_action('rest_api_init',['MSW_Hermes_Queue_Archive_Export','register']);

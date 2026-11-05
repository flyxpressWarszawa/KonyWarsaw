<?php
/**
 * Plugin Name: MSW — odzyskanie katalogu i ilustracji
 * Description: Kontrolowane partie istniejących importerów MSW i przypisywanie zatwierdzonych mediów do produktów WooCommerce.
 * Version: 1.3.0
 * Requires PHP: 8.1
 * Requires Plugins: woocommerce
 */
defined('ABSPATH') || exit;

final class MSW_Catalog_Recovery_20261105 {
    private const HOOK = 'msw_catalog_recovery_500_batch';
    private const IMAGE_HOOK = 'msw_catalog_recovery_asset_image';
    public static function allowed(): bool {
        return in_array(strtolower((string) wp_parse_url(home_url(), PHP_URL_HOST)), array('mobilny-serwis-warszawa.pl', 'www.mobilny-serwis-warszawa.pl'), true)
            && current_user_can('manage_options') && current_user_can('manage_woocommerce') && current_user_can('upload_files');
    }

    private static function job(): array {
        $job = get_option('mswt500_job_v1', array());
        return is_array($job) ? $job : array();
    }

    public static function status() {
        $lock = get_option('mswt500_lock_v1', array());
        return array(
            'version' => '1.3.0',
            'catalog500_available' => class_exists('MSW_Tech_500'),
            'catalog500_job' => self::job(),
            'catalog500_lock_age' => !empty($lock['at']) ? max(0, time() - (int) $lock['at']) : null,
            'image_batches_available' => class_exists('MSW_Product_Images_Batches'),
            'background_pending' => function_exists('as_has_scheduled_action') ? (bool) as_has_scheduled_action(self::HOOK, null, 'msw-catalog-recovery') : false,
        );
    }

    public static function start_background($request) {
        if (!class_exists('MSW_Tech_500') || !function_exists('as_enqueue_async_action')) {
            return new WP_Error('msw_scheduler_unavailable', 'Brak importera lub kolejki Action Scheduler.', array('status' => 409));
        }
        $job = self::job();
        if (($job['state'] ?? '') !== 'running' || ($job['mode'] ?? '') !== 'apply' || (int) ($job['actor'] ?? 0) !== get_current_user_id()) {
            return new WP_Error('msw_job_conflict', 'Wymagana aktywna kolejka importu tego operatora.', array('status' => 409));
        }
        $pending = as_has_scheduled_action(self::HOOK, null, 'msw-catalog-recovery');
        $id = $pending ? 0 : as_enqueue_async_action(self::HOOK, array(get_current_user_id()), 'msw-catalog-recovery', true);
        return array('queued' => (bool) ($pending || $id), 'action_id' => $id, 'job' => $job);
    }

    public static function background(int $actor): void {
        if (get_option('msw_catalog_external_owner_v1', '') === 'msw-render-central-1') return;
        $previous = get_current_user_id();
        wp_set_current_user($actor);
        try {
            $before = self::job();
            if (!self::allowed() || (int) ($before['actor'] ?? 0) !== $actor || ($before['mode'] ?? '') !== 'apply') {
                return;
            }
            $request = new WP_REST_Request('POST');
            $request->set_header('Content-Type', 'application/json');
            $request->set_body(wp_json_encode(array('limit' => 1)));
            self::catalog_step($request);
            $after = self::job();
            if ((int) ($after['errors'] ?? 0) > (int) ($before['errors'] ?? 0)) {
                $after['state'] = 'paused';
                update_option('mswt500_job_v1', $after, false);
                return;
            }
            if (($after['state'] ?? '') === 'running') {
                if ((int) ($after['cursor'] ?? 0) === (int) ($before['cursor'] ?? 0)) {
                    as_schedule_single_action(time() + 60, self::HOOK, array($actor), 'msw-catalog-recovery', false);
                } else {
                    as_enqueue_async_action(self::HOOK, array($actor), 'msw-catalog-recovery', false);
                }
            }
        } finally {
            wp_set_current_user($previous);
        }
    }

    public static function catalog_step($request) {
        if (!class_exists('MSW_Tech_500')) {
            return new WP_Error('msw_missing_importer', 'Brak importera 500 produktów.', array('status' => 409));
        }
        $input = $request->get_json_params();
        $limit = isset($input['limit']) ? (int) $input['limit'] : 10;
        if ($limit < 1 || $limit > 20) {
            return new WP_Error('msw_invalid_limit', 'Limit partii: 1–20.', array('status' => 400));
        }
        if (!empty($input['resume'])) {
            $rows = MSW_Tech_500::catalog();
            if (count($rows) !== 500) {
                return new WP_Error('msw_invalid_catalog', 'Katalog musi zawierać 500 pozycji.', array('status' => 409));
            }
            $lock = get_option('mswt500_lock_v1', array());
            if (!empty($lock) && time() - (int) ($lock['at'] ?? 0) < 600) {
                return new WP_Error('msw_worker_busy', 'Trwa zapis produktu.', array('status' => 409));
            }
            if (!empty($lock)) {
                delete_option('mswt500_lock_v1');
            }
            $job = self::job();
            if (!$job || ($job['mode'] ?? 'apply') !== 'apply') {
                return new WP_Error('msw_job_conflict', 'Oczekiwano istniejącej kolejki importu.', array('status' => 409));
            }
            $job['actor'] = get_current_user_id();
            $job['state'] = (int) ($job['cursor'] ?? 0) >= 500 ? 'complete' : 'running';
            update_option('mswt500_job_v1', $job, false);
        }
        $started = microtime(true);
        $processed = 0;
        while ($processed < $limit && microtime(true) - $started < 20) {
            $before = self::job();
            if (($before['state'] ?? '') !== 'running') {
                break;
            }
            MSW_Tech_500::tick();
            $after = self::job();
            if ((int) ($after['cursor'] ?? 0) === (int) ($before['cursor'] ?? 0)) {
                break;
            }
            $processed++;
        }
        return array('processed' => $processed, 'job' => self::job());
    }

    public static function image_step($request) {
        if (!class_exists('MSW_Product_Images_Batches')) {
            return new WP_Error('msw_missing_images_importer', 'Brak importera ilustracji.', array('status' => 409));
        }
        $input = $request->get_json_params();
        $indices = $input['indices'] ?? null;
        if (!is_array($indices) || count($indices) < 1 || count($indices) > 10) {
            return new WP_Error('msw_invalid_indices', 'Podaj 1–10 indeksów.', array('status' => 400));
        }
        $rows = MSW_Product_Images_Batches::rows();
        foreach ($indices as $index) {
            if (!is_int($index) || $index < 0 || !isset($rows[$index])) {
                return new WP_Error('msw_invalid_index', 'Nieznany indeks ilustracji.', array('status' => 400));
            }
        }
        $results = array();
        foreach (array_unique($indices) as $index) {
            $response = MSW_Product_Images_Batches::process(array('index' => $index, 'mode' => 'apply'));
            $results[] = array('index' => $index, 'result' => $response->get_data());
        }
        return array('results' => $results);
    }

    public static function assign($request) {
        $input = $request->get_json_params();
        $items = $input['items'] ?? null;
        if (!is_array($items) || count($items) < 1 || count($items) > 20) {
            return new WP_Error('msw_invalid_items', 'Podaj 1–20 przypisań.', array('status' => 400));
        }
        $results = array();
        foreach ($items as $item) {
            $id = absint($item['product_id'] ?? 0);
            $media = absint($item['media_id'] ?? 0);
            $sku = (string) ($item['sku'] ?? '');
            $product = wc_get_product($id);
            if (!$product || !current_user_can('edit_post', $id) || $product->get_sku() !== $sku || !wp_attachment_is_image($media) || !current_user_can('edit_post', $media)) {
                $results[] = array('product_id' => $id, 'success' => false, 'message' => 'Nieprawidłowy produkt, obraz lub uprawnienia.');
                continue;
            }
            $parent = (int) wp_get_post_parent_id($media);
            if ($parent !== $id) {
                $results[] = array('product_id' => $id, 'success' => false, 'message' => 'Obraz musi być przesłany dla wskazanego produktu.');
                continue;
            }
            $old = (int) $product->get_image_id('edit');
            if (isset($item['expected_current_image_id']) && $old !== (int) $item['expected_current_image_id'] && $old !== $media) {
                $results[] = array('product_id' => $id, 'success' => false, 'message' => 'Obraz produktu zmienił się po odczycie.');
                continue;
            }
            if ($old !== $media) {
                $backup = array('old' => $old, 'new' => $media, 'sku' => $sku, 'time' => time());
                $product->update_meta_data('_msw_unique_product_image_import_20261105', $backup);
                if (!empty($item['original_200'])) {
                    if (!preg_match('/^MSW-D[0-9]{3}$/D', $sku)) {
                        $results[] = array('product_id' => $id, 'success' => false, 'message' => 'Niewłaściwa seria produktu.');
                        continue;
                    }
                    $product->update_meta_data('_msw_product_images_100_v1', $backup);
                }
                $product->set_image_id($media);
                $product->save();
                wc_delete_product_transients($id);
            }
            $verified = (int) get_post_thumbnail_id($id) === $media;
            $results[] = array('product_id' => $id, 'sku' => $sku, 'media_id' => $media, 'success' => $verified);
        }
        return array('results' => $results);
    }

    public static function ingest($request) {
        $input = $request->get_json_params();
        $items = $input['items'] ?? null;
        if (!is_array($items) || count($items) < 1 || count($items) > 3) {
            return new WP_Error('msw_invalid_uploads', 'Podaj 1–3 obrazy.', array('status' => 400));
        }
        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/media.php';
        require_once ABSPATH . 'wp-admin/includes/image.php';
        $results = array();
        foreach ($items as $item) {
            $id = absint($item['product_id'] ?? 0);
            $sku = (string) ($item['sku'] ?? '');
            $tmp = '';
            try {
                $product = wc_get_product($id);
                if (!$product || !current_user_can('edit_post', $id) || $product->get_sku() !== $sku || !preg_match('/^MSW-[DAPKV][0-9]{3}$/D', $sku)) {
                    throw new RuntimeException('Nieprawidłowy produkt lub uprawnienia.');
                }
                $encoded = $item['file_base64'] ?? null;
                $expected = strtolower((string) ($item['sha256'] ?? ''));
                if (!is_string($encoded) || strlen($encoded) > 8 * 1024 * 1024 || !preg_match('/^[a-f0-9]{64}$/D', $expected)) {
                    throw new RuntimeException('Nieprawidłowy plik lub suma kontrolna.');
                }
                $bytes = base64_decode($encoded, true);
                if (!is_string($bytes) || strlen($bytes) < 100 || strlen($bytes) > 5 * 1024 * 1024 || !hash_equals($expected, hash('sha256', $bytes))) {
                    throw new RuntimeException('Niezgodna integralność obrazu.');
                }
                $info = @getimagesizefromstring($bytes);
                $extensions = array('image/png' => 'png', 'image/jpeg' => 'jpg', 'image/webp' => 'webp');
                $mime = is_array($info) ? ($info['mime'] ?? '') : '';
                if (!isset($extensions[$mime]) || $info[0] < 256 || $info[1] < 256 || $info[0] > 8192 || $info[1] > 8192) {
                    throw new RuntimeException('Wymagany prawidłowy obraz PNG, JPEG lub WebP.');
                }
                $existing = get_posts(array('post_type' => 'attachment', 'post_status' => 'inherit', 'posts_per_page' => 2, 'fields' => 'ids', 'meta_key' => '_msw_product_image_source_sha_20261105', 'meta_value' => $expected));
                $media = 0;
                foreach ($existing as $candidate) {
                    if ((int) wp_get_post_parent_id($candidate) !== $id) {
                        throw new RuntimeException('Ten obraz jest już przypisany do innego produktu.');
                    }
                    $file = get_attached_file($candidate);
                    if (is_string($file) && is_file($file) && hash_equals($expected, hash_file('sha256', $file))) {
                        $media = (int) $candidate;
                    }
                }
                if (!$media) {
                    $filename = $sku . '-unikat-ai-' . substr($expected, 0, 12) . '.' . $extensions[$mime];
                    $tmp = wp_tempnam($filename);
                    if (!$tmp || file_put_contents($tmp, $bytes) !== strlen($bytes)) {
                        throw new RuntimeException('Nie można przygotować obrazu do zapisu.');
                    }
                    $media = media_handle_sideload(array('name' => $filename, 'tmp_name' => $tmp), $id, null, array('post_title' => sanitize_text_field((string) ($item['title'] ?? $product->get_name() . ' — ilustracja AI MSW')), 'post_excerpt' => wp_kses_post((string) ($item['caption'] ?? 'Ilustracja AI produktu cyfrowego MSW.'))));
                    if (is_wp_error($media)) {
                        throw new RuntimeException($media->get_error_message());
                    }
                    $tmp = '';
                    update_post_meta($media, '_msw_product_image_source_sha_20261105', $expected);
                }
                update_post_meta($media, '_wp_attachment_image_alt', sanitize_text_field((string) ($item['alt'] ?? 'Ilustracja AI: ' . $product->get_name())));
                $assign = array('product_id' => $id, 'sku' => $sku, 'media_id' => (int) $media, 'original_200' => !empty($item['original_200']));
                if (isset($item['expected_current_image_id'])) {
                    $assign['expected_current_image_id'] = (int) $item['expected_current_image_id'];
                }
                $assignment = new WP_REST_Request('POST');
                $assignment->set_header('Content-Type', 'application/json');
                $assignment->set_body(wp_json_encode(array('items' => array($assign))));
                $saved = self::assign($assignment);
                $entry = $saved['results'][0];
                $entry['source_url'] = wp_get_attachment_url($media);
                $entry['sha256'] = $expected;
                $results[] = $entry;
            } catch (Throwable $error) {
                $results[] = array('product_id' => $id, 'sku' => $sku, 'success' => false, 'message' => sanitize_text_field(substr($error->getMessage(), 0, 240)));
            } finally {
                if ($tmp && is_file($tmp)) {
                    @unlink($tmp);
                }
            }
        }
        return array('results' => $results);
    }

    private static function asset_pack(): array {
        $directory = WP_PLUGIN_DIR . '/msw-product-image-batch';
        $file = $directory . '/manifest.json';
        if (!is_file($file) || is_link($file) || filesize($file) > 524288) {
            throw new RuntimeException('Brak prawidłowej paczki obrazów.');
        }
        $manifest = json_decode((string) file_get_contents($file), true);
        if (!is_array($manifest) || !isset($manifest['items']) || !is_array($manifest['items']) || count($manifest['items']) < 1 || count($manifest['items']) > 200) {
            throw new RuntimeException('Nieprawidłowy manifest paczki.');
        }
        return array($directory, $manifest, hash_file('sha256', $file));
    }

    private static function import_asset(int $index, string $expected): array {
        list($directory, $manifest, $actual) = self::asset_pack();
        if (!hash_equals($actual, $expected) || !isset($manifest['items'][$index])) {
            throw new RuntimeException('Paczka zmieniła się po rozpoczęciu importu.');
        }
        $record = $manifest['items'][$index];
        $file = (string) ($record['file'] ?? '');
        if (!preg_match('~^images/MSW-[DAPKV][0-9]{3}-[a-f0-9]{12}\.(png|webp|jpg)$~D', $file)) {
            throw new RuntimeException('Nieprawidłowa ścieżka obrazu.');
        }
        $path = $directory . '/' . $file;
        if (!is_file($path) || is_link($path) || !str_starts_with((string) realpath($path), (string) realpath($directory) . DIRECTORY_SEPARATOR) || filesize($path) > 5 * 1024 * 1024) {
            throw new RuntimeException('Nieprawidłowy plik obrazu.');
        }
        if (!hash_equals((string) ($record['sha256'] ?? ''), hash_file('sha256', $path))) {
            throw new RuntimeException('Niezgodna suma kontrolna obrazu.');
        }
        $record['product_id'] = wc_get_product_id_by_sku((string) ($record['sku'] ?? ''));
        $record['file_base64'] = base64_encode((string) file_get_contents($path));
        $request = new WP_REST_Request('POST');
        $request->set_header('Content-Type', 'application/json');
        $request->set_body(wp_json_encode(array('items' => array($record))));
        $response = self::ingest($request);
        if (is_wp_error($response)) {
            throw new RuntimeException($response->get_error_message());
        }
        return array('index' => $index) + $response['results'][0];
    }

    public static function assets_status() {
        try {
            list($directory, $manifest, $sha) = self::asset_pack();
            $items = array();
            $verified = 0;
            foreach ($manifest['items'] as $index => $record) {
                $id = wc_get_product_id_by_sku((string) ($record['sku'] ?? ''));
                $media = $id ? (int) get_post_thumbnail_id($id) : 0;
                $file = $media ? get_attached_file($media) : '';
                $ok = $id && $media && wp_attachment_is_image($media) && (int) wp_get_post_parent_id($media) === $id
                    && get_post_meta($media, '_msw_product_image_source_sha_20261105', true) === ($record['sha256'] ?? '')
                    && is_string($file) && is_file($file) && filesize($file) > 0;
                $verified += $ok ? 1 : 0;
                $items[] = array('index' => $index, 'sku' => $record['sku'], 'product_id' => $id, 'media_id' => $media, 'verified' => (bool) $ok, 'permalink' => $id ? get_permalink($id) : null);
            }
            return array('batch_id' => $manifest['batch_id'] ?? '', 'manifest_sha' => $sha, 'total' => count($items), 'verified' => $verified, 'items' => $items);
        } catch (Throwable $error) {
            return new WP_Error('msw_assets_unavailable', $error->getMessage(), array('status' => 409));
        }
    }

    public static function assets_import($request) {
        $input = $request->get_json_params();
        $indices = $input['indices'] ?? null;
        if (!is_array($indices) || count($indices) < 1 || count($indices) > 3) {
            return new WP_Error('msw_invalid_indices', 'Podaj 1–3 indeksy.', array('status' => 400));
        }
        $results = array();
        foreach ($indices as $index) {
            try {
                if (!is_int($index) || $index < 0) {
                    throw new RuntimeException('Nieprawidłowy indeks.');
                }
                $results[] = self::import_asset($index, (string) ($input['manifest_sha'] ?? ''));
            } catch (Throwable $error) {
                $results[] = array('index' => $index, 'success' => false, 'message' => sanitize_text_field($error->getMessage()));
            }
        }
        return array('results' => $results);
    }

    public static function assets_start($request) {
        if (!function_exists('as_enqueue_async_action')) {
            return new WP_Error('msw_scheduler_unavailable', 'Brak kolejki obrazów.', array('status' => 409));
        }
        try {
            list($directory, $manifest, $sha) = self::asset_pack();
            $input = $request->get_json_params();
            if (!hash_equals($sha, (string) ($input['manifest_sha'] ?? ''))) {
                throw new RuntimeException('Paczka zmieniła się przed importem.');
            }
            $queued = 0;
            foreach ($manifest['items'] as $index => $record) {
                $queued += as_enqueue_async_action(self::IMAGE_HOOK, array($index, get_current_user_id(), $sha), 'msw-catalog-recovery-images', true) ? 1 : 0;
            }
            return array('total' => count($manifest['items']), 'queued' => $queued, 'manifest_sha' => $sha);
        } catch (Throwable $error) {
            return new WP_Error('msw_assets_unavailable', $error->getMessage(), array('status' => 409));
        }
    }

    public static function image_background(int $index, int $actor, string $sha): void {
        $previous = get_current_user_id();
        wp_set_current_user($actor);
        try {
            if (!self::allowed()) {
                throw new RuntimeException('Brak uprawnień operatora obrazów.');
            }
            $result = self::import_asset($index, $sha);
            if (empty($result['success'])) {
                throw new RuntimeException((string) ($result['message'] ?? 'Nie zapisano ilustracji.'));
            }
        } finally {
            wp_set_current_user($previous);
        }
    }

    public static function routes(): void {
        foreach (array(
            '/status' => array('GET', 'status'),
            '/catalog500/step' => array('POST', 'catalog_step'),
            '/catalog500/start' => array('POST', 'start_background'),
            '/images/step' => array('POST', 'image_step'),
            '/images/assign' => array('POST', 'assign'),
            '/images/ingest' => array('POST', 'ingest'),
            '/images/assets/status' => array('GET', 'assets_status'),
            '/images/assets/import' => array('POST', 'assets_import'),
            '/images/assets/start' => array('POST', 'assets_start'),
        ) as $route => $settings) {
            register_rest_route('msw-catalog-recovery/v1', $route, array(
                'methods' => $settings[0],
                'permission_callback' => array(__CLASS__, 'allowed'),
                'callback' => array(__CLASS__, $settings[1]),
            ));
        }
    }
}

/** A fixed, signed publisher adapter; never an arbitrary REST/code proxy. */
final class MSW_Catalog_External_20261105 {
    private const NODE = 'msw-render-central-1';
    private const SCHEMA = 'msw.catalog.external.v1';

    public static function permission($request) {
        if (strtolower((string) wp_parse_url(home_url(), PHP_URL_HOST)) !== 'mobilny-serwis-warszawa.pl'
            || !class_exists('MSW\\UnifiedEngine\\RemoteStore') || strlen($request->get_body()) > 4096) {
            return new WP_Error('msw_catalog_scope', 'Niedostępny łącznik katalogu.', array('status' => 403));
        }
        $body = $request->get_json_params();
        if (!is_array($body) || ($body['schema'] ?? '') !== self::SCHEMA
            || !in_array($body['operation'] ?? '', array('status', 'catalog', 'assets'), true)) {
            return new WP_Error('msw_catalog_schema', 'Nieprawidłowe żądanie.', array('status' => 400));
        }
        $headers = array();
        foreach (array('node', 'timestamp', 'nonce', 'signature') as $field) $headers[$field] = $request->get_header('x-mswue-' . $field);
        $node = \MSW\UnifiedEngine\RemoteStore::authorise('status', $request->get_body(), $headers);
        if (is_wp_error($node)) return $node;
        if ($node !== self::NODE) return new WP_Error('msw_catalog_node', 'Niewłaściwy węzeł.', array('status' => 403));
        return true;
    }

    private static function snapshot(): array {
        $job = get_option('mswt500_job_v1', array());
        $catalog = is_array($job) ? array_intersect_key($job, array_flip(array('state', 'mode', 'cursor', 'errors'))) : array();
        $assets = MSW_Catalog_Recovery_20261105::assets_status();
        if (is_wp_error($assets)) $summary = array('state' => 'unavailable', 'code' => $assets->get_error_code());
        else {
            $pending = array();
            foreach ($assets['items'] as $item) if (empty($item['verified'])) $pending[] = (int) $item['index'];
            $summary = array('manifest_sha' => $assets['manifest_sha'], 'total' => $assets['total'], 'verified' => $assets['verified'], 'pending_indices' => $pending);
        }
        return array('adapter_version' => '1.3.0', 'catalog' => $catalog, 'assets' => $summary,
            'external_owner' => get_option('msw_catalog_external_owner_v1', '') === self::NODE);
    }

    public static function dispatch($request) {
        $input = $request->get_json_params();
        $operation = $input['operation'];
        $fields = array('status' => array('schema', 'operation'), 'catalog' => array('schema', 'operation', 'expected_cursor'), 'assets' => array('schema', 'operation', 'index', 'manifest_sha'));
        if (array_diff(array_keys($input), $fields[$operation])) return new WP_Error('msw_catalog_fields', 'Nieznane pola.', array('status' => 400));
        $job = get_option('mswt500_job_v1', array());
        $actor = is_array($job) ? (int) ($job['actor'] ?? 0) : 0;
        if (!$actor || !user_can($actor, 'manage_options') || !user_can($actor, 'manage_woocommerce') || !user_can($actor, 'upload_files')) {
            return new WP_Error('msw_catalog_actor', 'Brak uprawnionego operatora istniejącego importu.', array('status' => 409));
        }
        $previous = get_current_user_id();
        $lock = false;
        wp_set_current_user($actor);
        try {
            if (!MSW_Catalog_Recovery_20261105::allowed()) return new WP_Error('msw_catalog_actor', 'Brak uprawnień.', array('status' => 403));
            if ($operation === 'status') $data = self::snapshot();
            else {
                // add_option is atomic. A crash expires the lease; never steal a live writer.
                $lease = get_option('msw_catalog_external_lease_v1', array());
                if (!empty($lease) && time() - (int) ($lease['at'] ?? 0) > 600) delete_option('msw_catalog_external_lease_v1');
                $lock = wp_generate_uuid4();
                if (!add_option('msw_catalog_external_lease_v1', array('id' => $lock, 'at' => time()), '', false)) {
                    $lock = false;
                    return new WP_Error('msw_catalog_busy', 'Trwa zapis.', array('status' => 409));
                }
                $job = get_option('mswt500_job_v1', array());
                if ($operation === 'catalog') {
                    if (!is_int($input['expected_cursor'] ?? null) || ($job['cursor'] ?? -1) !== $input['expected_cursor']
                        || ($job['mode'] ?? '') !== 'apply' || ($job['state'] ?? '') !== 'running' || (int) ($job['errors'] ?? 0) > 0) {
                        return new WP_Error('msw_catalog_conflict', 'Odczytaj aktualny stan importu.', array('status' => 409));
                    }
                    $old_lock = get_option('mswt500_lock_v1', array());
                    if (!empty($old_lock) && time() - (int) ($old_lock['at'] ?? 0) < 600) return new WP_Error('msw_catalog_busy', 'Trwa zapis produktu.', array('status' => 409));
                    $arguments = array('limit' => 1, 'resume' => true);
                } else {
                    if (!is_int($input['index'] ?? null) || $input['index'] < 0 || !is_string($input['manifest_sha'] ?? null)
                        || !preg_match('/^[a-f0-9]{64}$/D', $input['manifest_sha'])) return new WP_Error('msw_catalog_asset', 'Nieprawidłowy obraz.', array('status' => 400));
                    $arguments = array('indices' => array($input['index']), 'manifest_sha' => $input['manifest_sha']);
                }
                update_option('msw_catalog_external_owner_v1', self::NODE, false);
                $subrequest = new WP_REST_Request('POST');
                $subrequest->set_header('Content-Type', 'application/json');
                $subrequest->set_body(wp_json_encode($arguments));
                $data = $operation === 'catalog' ? MSW_Catalog_Recovery_20261105::catalog_step($subrequest) : MSW_Catalog_Recovery_20261105::assets_import($subrequest);
                if (is_wp_error($data)) return $data;
                // Do not expose full imported product/job data to the worker's logs.
                $data = self::snapshot();
            }
            return new WP_REST_Response(array('schema' => self::SCHEMA, 'operation' => $operation, 'ok' => true, 'data' => $data), 200,
                array('Cache-Control' => 'no-store, private'));
        } finally {
            if ($lock) {
                $lease = get_option('msw_catalog_external_lease_v1', array());
                if (($lease['id'] ?? '') === $lock) delete_option('msw_catalog_external_lease_v1');
            }
            wp_set_current_user($previous);
        }
    }
}
add_action('rest_api_init', static function () {
    register_rest_route('msw-catalog-recovery/v1', '/external', array('methods' => 'POST',
        'permission_callback' => array('MSW_Catalog_External_20261105', 'permission'),
        'callback' => array('MSW_Catalog_External_20261105', 'dispatch')));
});
add_action('rest_api_init', array('MSW_Catalog_Recovery_20261105', 'routes'));
add_action('msw_catalog_recovery_500_batch', array('MSW_Catalog_Recovery_20261105', 'background'));
add_action('msw_catalog_recovery_asset_image', array('MSW_Catalog_Recovery_20261105', 'image_background'), 10, 3);

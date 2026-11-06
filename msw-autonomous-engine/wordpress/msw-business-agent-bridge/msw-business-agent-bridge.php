<?php
/**
 * Plugin Name: MSW Business Agent Bridge
 * Description: Prywatny most Render–centrala: wiedza portali, istniejące silniki, produkty WooCommerce i obsługa klientów.
 * Version: 1.1.1
 * Author: MSW
 */
defined('ABSPATH') || exit;

final class MSW_Business_Agent_Bridge {
    const VERSION = '1.1.1';
    const GRANT = 'msw_business_agent_owner_v1';
    const INPUT = 'msw_business_agent_command';
    const OUTPUT = 'msw_business_agent_result';
    const SLUG = 'msw-ai-autonomia-sterowanie-prywatne';

    public static function activate(): void {
        if (current_user_can('manage_options') && current_user_can('update_plugins')) {
            update_option(self::GRANT, ['owner' => get_current_user_id(), 'enabled' => true,
                'product_publication' => true, 'inbound_customer_replies' => true, 'at' => time()], false);
        }
    }
    public static function boot(): void {
        add_action('init', [self::class, 'recoverActivationGrant'], 1);
        add_action('rest_api_init', [self::class, 'routes']);
        add_action('init', [self::class, 'meta']);
        add_action('updated_post_meta', [self::class, 'changed'], 20, 4);
        add_action('added_post_meta', [self::class, 'changed'], 20, 4);
    }
    public static function recoverActivationGrant(): void {
        // A timed-out plugin activation can finish its active_plugins write
        // before its hook. Only the retained native owner can finish the grant.
        if (get_option(self::GRANT, false) !== false || !current_user_can('manage_options') ||
            !current_user_can('update_plugins') || !class_exists('MSW_Module_Autonomy')) return;
        $s = (array) get_option(MSW_Module_Autonomy::OPTION, []);
        if (!empty($s['enabled']) && (int) ($s['owner_id'] ?? 0) === get_current_user_id()) self::activate();
    }
    public static function error(string $code, int $status = 400) {
        return new WP_Error('msw_business_agent_' . $code, 'MSW Business Agent: ' . $code, ['status' => $status]);
    }
    public static function owner(): bool {
        $g = (array) get_option(self::GRANT, []);
        return !empty($g['enabled']) && user_can((int) ($g['owner'] ?? 0), 'manage_options')
            && user_can((int) ($g['owner'] ?? 0), 'update_plugins');
    }
    public static function admin(): bool {
        self::recoverActivationGrant();
        return self::owner() && current_user_can('manage_options') && get_current_user_id() === (int) get_option(self::GRANT, [])['owner'];
    }
    public static function permission($r) {
        if (!self::owner()) return self::error('owner_grant', 403);
        if (self::admin()) return true;
        if (!class_exists('MSW_Render_Federation')) return self::error('federation_missing', 503);
        // Retain the node's HMAC authentication and existing capability grant.
        $verified = MSW_Render_Federation::permission($r);
        if (is_wp_error($verified) || $verified !== true) return $verified;
        $p = json_decode($r->get_body(), true);
        if (in_array($p['business_operation'] ?? '', ['product.publish','capability.execute','telegram.reply','telegram.inbox'], true)) {
            // The original verifier authenticates/consumes the nonce once. A
            // write additionally needs this same authenticated node's write kind.
            if (!class_exists('MSW\\UnifiedEngine\\RemoteStore')) return self::error('write_scope', 403);
            $config = \MSW\UnifiedEngine\RemoteStore::config();
            $node = $r->get_header('x-mswue-node');
            if (!in_array('module.execute', $config['nodes'][$node]['kinds'] ?? [], true)) return self::error('write_scope', 403);
        }
        return true;
    }
    public static function routes(): void {
        register_rest_route('msw-business-agent/v1', '/gateway', ['methods' => 'POST',
            'permission_callback' => [self::class, 'permission'], 'callback' => [self::class, 'gateway']]);
    }
    public static function meta(): void {
        register_post_meta('post', self::INPUT, ['single' => true, 'type' => 'object',
            'show_in_rest' => ['schema' => ['type' => 'object', 'additionalProperties' => true]],
            'auth_callback' => static fn($allowed, $key, $id) => self::controlPost((int) $id)]);
        register_post_meta('post', self::OUTPUT, ['single' => true, 'type' => 'object',
            'show_in_rest' => ['schema' => ['type' => 'object', 'additionalProperties' => true, 'readonly' => true]],
            'auth_callback' => static fn() => false]);
    }
    public static function controlPost(int $id): bool {
        $p = get_post($id);
        return self::admin() && $p && $p->post_type === 'post' && $p->post_status === 'private'
            && in_array($p->post_name, [self::SLUG, 'msw-business-agent-prywatne'], true) && (int) $p->post_author === get_current_user_id();
    }
    public static function changed($meta, $id, $key, $value): void {
        if ($key !== self::INPUT || !self::controlPost((int) $id)) return;
        $answer = self::dispatch(is_array($value) ? $value : []);
        update_post_meta((int) $id, self::OUTPUT, is_wp_error($answer) ? ['state' => 'blocked', 'code' => $answer->get_error_code()] : $answer);
    }
    public static function gateway($r) {
        $p = json_decode($r->get_body(), true);
        if (!is_array($p) || ($p['schema'] ?? '') !== 'msw.federation.v1') return self::error('schema');
        $op = $p['business_operation'] ?? '';
        if (in_array($op, ['product.publish','capability.execute','telegram.reply'], true) && ($p['operation'] ?? '') !== 'autonomy_execute') return self::error('write_scope', 403);
        $answer = self::dispatch($p);
        return is_wp_error($answer) ? $answer : new WP_REST_Response(['schema' => 'msw.business-agent.v1', 'ok' => true, 'data' => $answer], 200, ['Cache-Control' => 'no-store, private']);
    }
    public static function dispatch(array $p) {
        $op = $p['business_operation'] ?? '';
        $allowed = ['schema', 'operation', 'business_operation', 'request_id', 'offset', 'product_id', 'expected_sha256', 'capability', 'arguments', 'catalog_sha256', 'conversation_id', 'reply'];
        if (array_diff(array_keys($p), $allowed) || !preg_match('/^[A-Za-z0-9_.:-]{8,120}$/D', (string) ($p['request_id'] ?? ''))) return self::error('arguments');
        if ($op === 'status') return self::status();
        if ($op === 'context') return self::context(max(0, (int) ($p['offset'] ?? 0)));
        if ($op === 'knowledge') return self::knowledge(max(0, (int) ($p['offset'] ?? 0)));
        if ($op === 'products') return self::products(max(0, (int) ($p['offset'] ?? 0)));
        if ($op === 'product.publish') return self::publish($p);
        if ($op === 'capabilities') return self::capabilities();
        if ($op === 'capability.execute') return self::executeCapability($p);
        if ($op === 'telegram.audit') return self::telegramAudit();
        if ($op === 'telegram.verify') return self::telegramVerify();
        if ($op === 'telegram.inbox') return MSW_Business_Telegram::inbox();
        if ($op === 'telegram.reply') return MSW_Business_Telegram::reply($p);
        return self::error('operation');
    }
    public static function knowledge(int $offset): array {
        $key='msw_business_owner_knowledge_v1';$s=get_option($key,false);
        if($s===false){
            $file=__DIR__.'/private-knowledge-bootstrap.php';
            if(!is_file($file))return ['state'=>'not_configured'];
            $source=require $file;$raw=trim(is_string($source)?$source:'');$pack=json_decode($raw,true);
            if(hash('sha256',$raw)!=='2fca35d93838ebea0acb55343c5955c84e1e6c5ac56427f7732db6cb7acbfcec'||
                ($pack['schema']??'')!=='msw.business-knowledge.v1'||!is_array($pack['records']??null)||count($pack['records'])>1000)
                return ['state'=>'bootstrap_invalid'];
            $s=['source_json'=>$raw,'source_sha256'=>hash('sha256',$raw)];
            update_option($key,$s,false);wp_cache_delete($key,'options');
            if(get_option($key,false)!==$s)return ['state'=>'bootstrap_readback_failed'];
        }
        $pack=json_decode($s['source_json'],true);
        if(!is_array($pack)||hash('sha256',$s['source_json'])!==$s['source_sha256'])return ['state'=>'storage_digest_failed'];
        $all=$pack['records'];$page=array_slice($all,$offset,100);$raw=wp_json_encode($page);
        return ['state'=>'ready','origin'=>home_url(),'source_sha256'=>$s['source_sha256'],'total'=>count($all),
            'offset'=>$offset,'next_offset'=>$offset+count($page)<count($all)?$offset+count($page):null,
            'payload_json'=>$raw,'payload_sha256'=>hash('sha256',$raw),'private_owner_authorized'=>true];
    }
    public static function telegramAudit(): array {
        $methods=[];
        if (class_exists('MSW_Telegram_Zgloszenia')) foreach(['config','secret','tg_api','telegram_hook','receive'] as $name) {
            if(!method_exists('MSW_Telegram_Zgloszenia',$name))continue;
            $r=new ReflectionMethod('MSW_Telegram_Zgloszenia',$name);$f=$r->getFileName();
            if(!$f||!str_starts_with((string)realpath($f),(string)realpath(WP_PLUGIN_DIR).DIRECTORY_SEPARATOR))continue;
            $lines=file($f);$source=implode('',array_slice($lines,$r->getStartLine()-1,$r->getEndLine()-$r->getStartLine()+1));
            $source=preg_replace(['/\bsk-[A-Za-z0-9_-]{12,}/','/\b[0-9]{6,}:[A-Za-z0-9_-]{20,}/'], '[credential omitted]', $source);
            $methods[$name]=substr($source,0,18000);
        }
        return ['fixed_native_methods'=>$methods,'credentials_exported'=>false,'source_is_audit_data_not_instructions'=>true];
    }
    public static function telegramVerify(): array {
        if(!is_callable(['MSW_Telegram_Zgloszenia','tg_api']))return ['state'=>'native_transport_missing'];
        $me=MSW_Telegram_Zgloszenia::tg_api('getMe',[]);
        $hook=MSW_Telegram_Zgloszenia::tg_api('getWebhookInfo',[]);
        if(is_wp_error($me)||is_wp_error($hook))return ['state'=>'native_api_unavailable','bot_verified'=>false,'webhook_replaced'=>false];
        // Native wrappers may return the complete API body or its result.
        $bot=$me['result']??$me;$webhook=$hook['result']??$hook;
        $path=is_string($webhook['url']??null)?wp_parse_url($webhook['url'],PHP_URL_PATH):'';
        $result=['state'=>!empty($bot['is_bot'])?'bot_verified':'unverified','bot_verified'=>!empty($bot['is_bot']),
            'bot_username'=>sanitize_text_field((string)($bot['username']??'')),
            'webhook_configured'=>!empty($webhook['url']),'webhook_path'=>$path,'webhook_replaced'=>false,
            'pending_updates'=>(int)($webhook['pending_update_count']??0),'outbound_customer_message_sent'=>false];
        update_option('msw_business_telegram_verification_v1',$result+['at'=>time()],false);
        return $result;
    }
    public static function payment(): array {
        $gateways = [];
        if (function_exists('WC') && WC() && WC()->payment_gateways()) {
            foreach (WC()->payment_gateways()->payment_gateways() as $id => $g) {
                if (stripos((string) $id, 'paypal') !== false || stripos((string) $id, 'ppcp') !== false) {
                    $gateways[] = ['id' => $id, 'enabled' => $g->enabled === 'yes'];
                }
            }
        }
        return ['transport' => 'existing_WooCommerce_PayPal_checkout', 'gateways' => $gateways,
            'enabled' => (bool) array_filter($gateways, static fn($g) => $g['enabled']),
            'credentials_exported' => false, 'new_charge_created' => false, 'live_payment_test_verified' => false];
    }
    public static function telegramMetadata(): array {
        $classes = [];
        foreach (get_declared_classes() as $class) {
            if (!preg_match('/^MSW.*(?:Telegram|TG_)/i', $class)) continue;
            $r = new ReflectionClass($class); $file = $r->getFileName();
            if (!$file || !str_starts_with((string) realpath($file), (string) realpath(WP_PLUGIN_DIR) . DIRECTORY_SEPARATOR)) continue;
            $methods = [];
            foreach ($r->getMethods() as $m) {
                if ($m->getDeclaringClass()->getName() !== $class) continue;
                $methods[] = ['name' => $m->getName(), 'static' => $m->isStatic(), 'public' => $m->isPublic(),
                    'required_arguments' => $m->getNumberOfRequiredParameters()];
            }
            $source = file_get_contents($file);
            preg_match_all('/get_option\s*\(\s*[\'"]([a-zA-Z0-9_\-]+)[\'"]/', $source, $options);
            $constants = [];
            foreach (['OPTION', 'OPT', 'SETTINGS_OPTION'] as $key) if ($r->hasConstant($key)) {
                $v = $r->getConstant($key); if (is_string($v) && preg_match('/^[a-zA-Z0-9_\-]{1,120}$/D', $v)) $constants[$key] = $v;
            }
            $classes[] = ['class' => $class, 'file' => substr($file, strlen(WP_PLUGIN_DIR) + 1),
                'source_sha256' => hash_file('sha256', $file), 'methods' => $methods,
                'option_names' => array_values(array_unique($options[1])), 'option_constants' => $constants];
        }
        return ['existing_modules' => $classes] + MSW_Business_Telegram::verification();
    }
    public static function status(): array {
        return ['version' => self::VERSION, 'owner_authorized' => self::owner(), 'wordpress' => home_url(),
            'paypal' => self::payment(), 'telegram' => self::telegramMetadata(),
            'tools' => ['status', 'context', 'products', 'product.publish', 'capabilities', 'capability.execute', 'telegram.inbox', 'telegram.reply', 'telegram.verify'], 'scope' => 'existing_owner_authorized_central_site',
            'all_active_engines' => class_exists('MSW_Federation_Module_Bus'),
            'no_new_render_services' => true];
    }
    public static function capabilities(): array {
        $out = [];
        if (!is_callable(['MSW_Central_Engine_Registry', 'catalog'])) return ['state' => 'registry_missing', 'capabilities' => []];
        if (!function_exists('is_plugin_active')) require_once ABSPATH . 'wp-admin/includes/plugin.php';
        foreach (MSW_Central_Engine_Registry::catalog() as $id => $cap) {
            if (!is_array($cap) || !is_string($id) || !preg_match('/^[a-zA-Z0-9_.:-]{1,150}$/D', $id)) continue;
            if (empty($cap['plugin']) || !is_plugin_active($cap['plugin'])) continue;
            $row = array_intersect_key($cap, array_flip(['label', 'description', 'plugin', 'effect', 'input_schema', 'enabled']));
            $row['id'] = $id; $out[] = $row;
        }
        usort($out, static fn($a,$b) => strcmp($a['id'],$b['id']));
        return ['state' => 'active_registry_catalogued', 'capabilities' => $out,
            'catalog_sha256' => hash('sha256', wp_json_encode($out)), 'inactive_plugins_included' => false];
    }
    private static function scrub($value) {
        if (is_array($value)) {
            $out=[]; foreach ($value as $key=>$item) {
                if (is_string($key) && preg_match('/password|api.?key|access.?token|refresh.?token|secret|authorization/i', $key)) $out[$key]='[credential omitted]';
                else $out[$key]=self::scrub($item);
            } return $out;
        }
        if (is_string($value)) return preg_replace(['/\bsk-[A-Za-z0-9_-]{12,}/','/\b[0-9]{6,}:[A-Za-z0-9_-]{20,}/'], '[credential omitted]', $value);
        return is_object($value) ? '[non-serializable result]' : $value;
    }
    public static function executeCapability(array $p) {
        $catalog = self::capabilities(); $id = $p['capability'] ?? '';
        if (!is_string($id) || !is_array($p['arguments'] ?? null) ||
            !hash_equals((string) ($catalog['catalog_sha256'] ?? ''), (string) ($p['catalog_sha256'] ?? ''))) return self::error('capability_catalog_changed',409);
        $found = null; foreach ($catalog['capabilities'] as $cap) if ($cap['id'] === $id) $found=$cap;
        if (!$found || preg_match('/refund|payout|transfer|purchase|password|credential|shell|eval|database\.drop/i',$id)) return self::error('capability_scope',403);
        if (!is_callable(['MSW_Central_Engine_Registry','execute'])) return self::error('registry_unavailable',503);
        $fingerprint=hash('sha256',wp_json_encode([$id,$p['arguments'],$p['catalog_sha256']]));
        $key='msw_business_tool_'.hash('sha256',$p['request_id']); global $wpdb;
        $lock='msw-biz-'.substr(hash('sha256',$key),0,40);
        if((string)$wpdb->get_var($wpdb->prepare('SELECT GET_LOCK(%s,0)',$lock))!=='1')return self::error('tool_busy',409);
        try {
            wp_cache_delete($key,'options'); $saved=get_option($key,null);
            if(is_array($saved)) {
                if(!hash_equals((string)$saved['input_sha256'],$fingerprint))return self::error('tool_request_conflict',409);
                return ($saved['state']??'')==='completed'?$saved:self::error('tool_outcome_uncertain',409);
            }
            $saved=['state'=>'prepared','input_sha256'=>$fingerprint,'capability'=>$id,'request_id'=>$p['request_id']];
            if(!add_option($key,$saved,'',false))return self::error('tool_busy',409);
            $before=get_current_user_id(); $owner=(int)get_option(self::GRANT,[])['owner']; wp_set_current_user($owner);
            try {$result=MSW_Central_Engine_Registry::execute($id,$p['arguments'],$p['request_id']);}
            finally {wp_set_current_user($before);}
            if(is_wp_error($result))$result=['state'=>'blocked','code'=>$result->get_error_code()];
            $saved['state']='completed'; $saved['result']=self::scrub($result);
            $saved['result_sha256']=hash('sha256',wp_json_encode($saved['result']));
            update_option($key,$saved,false); wp_cache_delete($key,'options');
            if(get_option($key,null)!==$saved)return self::error('tool_readback',503);
            return $saved+['storage_readback_verified'=>true];
        } finally {$wpdb->get_var($wpdb->prepare('SELECT RELEASE_LOCK(%s)',$lock));}
    }
    public static function context(int $offset): array {
        $rows = get_posts(['post_type' => ['post', 'page'], 'post_status' => 'publish', 'posts_per_page' => 40,
            'offset' => $offset, 'orderby' => 'ID', 'order' => 'ASC', 'suppress_filters' => true]);
        $records = [];
        foreach ($rows as $p) $records[] = ['id' => $p->ID, 'title' => get_the_title($p), 'url' => get_permalink($p),
            'excerpt' => mb_substr(wp_strip_all_tags(strip_shortcodes($p->post_content)), 0, 1200), 'type' => $p->post_type];
        return ['origin' => home_url(), 'records' => $records, 'offset' => $offset,
            'next_offset' => count($rows) === 40 ? $offset + 40 : null, 'source' => 'live_published_wordpress_content',
            'customers_or_credentials_exported' => false];
    }
    public static function productSnapshot($p): array {
        $row = ['id' => $p->get_id(), 'name' => $p->get_name(), 'status' => $p->get_status(), 'sku' => $p->get_sku(),
            'price' => $p->get_price(), 'virtual' => $p->is_virtual(), 'downloadable' => $p->is_downloadable(),
            'url' => get_permalink($p->get_id()), 'image_id' => $p->get_image_id(),
            'description' => mb_substr(wp_strip_all_tags($p->get_description()), 0, 3000),
            'download_count' => count($p->get_downloads()), 'qa_receipt_present' => is_array($p->get_meta('_msw_agent_release_proof'))];
        $row['sha256'] = hash('sha256', wp_json_encode($row));
        return $row;
    }
    public static function products(int $offset): array {
        if (!function_exists('wc_get_products')) return ['state' => 'woocommerce_missing', 'records' => []];
        $products = wc_get_products(['limit' => 30, 'offset' => $offset, 'orderby' => 'ID', 'order' => 'ASC', 'status' => ['draft', 'publish']]);
        return ['records' => array_map([self::class, 'productSnapshot'], $products), 'offset' => $offset,
            'next_offset' => count($products) === 30 ? $offset + 30 : null, 'paypal' => self::payment()];
    }
    public static function publish(array $p) {
        if (!function_exists('wc_get_product') || !self::owner()) return self::error('commerce_unavailable', 503);
        $product = wc_get_product((int) ($p['product_id'] ?? 0));
        if (!$product) return self::error('product_missing', 404);
        $snapshot = self::productSnapshot($product);
        if (!hash_equals($snapshot['sha256'], (string) ($p['expected_sha256'] ?? ''))) return self::error('product_changed', 409);
        if ($snapshot['status'] === 'publish') return ['state' => 'already_published', 'product' => $snapshot];
        $proof = $product->get_meta('_msw_agent_release_proof');
        if (!$product->is_virtual() || !$product->is_downloadable() || (float) $product->get_price() <= 0 ||
            !$product->get_image_id() || strlen($snapshot['description']) < 200 || !is_array($proof) ||
            ($proof['tested'] ?? null) !== true || ($proof['protected_delivery_verified'] ?? null) !== true ||
            !self::payment()['enabled']) return self::error('release_prerequisites', 409);
        $files = $product->get_downloads();
        if (!$files || !isset($proof['files_sha256']) || !is_array($proof['files_sha256'])) return self::error('release_files', 409);
        foreach ($files as $download) {
            $file = $download->get_file(); $real = realpath($file);
            if (!$real || !is_file($real) || !isset($proof['files_sha256'][$download->get_id()]) ||
                !hash_equals($proof['files_sha256'][$download->get_id()], hash_file('sha256', $real))) return self::error('release_file_digest', 409);
        }
        $product->set_status('publish'); $product->save();
        clean_post_cache($product->get_id()); $fresh = wc_get_product($product->get_id());
        if (!$fresh || $fresh->get_status() !== 'publish') return self::error('publish_readback', 503);
        return ['state' => 'published', 'product' => self::productSnapshot($fresh), 'storage_readback_verified' => true,
            'request_id' => $p['request_id'], 'payment_created' => false];
    }
}
register_activation_hook(__FILE__, [MSW_Business_Agent_Bridge::class, 'activate']);
require_once __DIR__.'/telegram.php';
MSW_Business_Agent_Bridge::boot();

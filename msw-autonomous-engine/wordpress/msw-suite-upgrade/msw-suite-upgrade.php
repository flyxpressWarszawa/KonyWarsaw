<?php
/**
 * Plugin Name: MSW Suite Upgrade — 2026-11-05
 * Description: Jednorazowe, odwracalne rozszerzenie diagnostyki istniejących modułów MSW.
 * Version: 1.0.0
 * Requires PHP: 8.1
 * Author: Mobilny Serwis Warszawa
 */
defined('ABSPATH') || exit;
final class MSW_Suite_Upgrade_20261105 {
    private const RECEIPT = 'msw_suite_upgrade_20261105';
    private const BACKUP = 'msw_suite_upgrade_backup_20261105';
    private const MARKER = '// MSW SUITE HEALTH CONTRACT 20261105';

    private static function path(string $relative): string {
        if (!preg_match('~\A[a-z0-9_-]+/[a-zA-Z0-9_./-]+\z~D', $relative) || str_contains($relative,'..')) throw new RuntimeException('invalid_patch_path');
        $root = realpath(WP_PLUGIN_DIR);
        $parent = realpath(dirname(WP_PLUGIN_DIR.'/'.$relative));
        if (!$root || !$parent || !str_starts_with($parent.'/', $root.'/') || is_link(WP_PLUGIN_DIR.'/'.$relative)) throw new RuntimeException('patch_path_outside_plugins');
        return WP_PLUGIN_DIR.'/'.$relative;
    }
    private static function append(string $source, string $extra): string {
        $source = rtrim($source);
        return $source.(str_ends_with($source,'?>')?"\n<?php\n":"\n").$extra."\n";
    }
    private static function version(string $old): string {
        if (preg_match('/\A(\d+\.\d+\.\d+-rc\.)(\d+)\z/D',$old,$m)) return $m[1].((int)$m[2]+1);
        if (preg_match('/\A(\d+\.\d+\.)(\d+)\z/D',$old,$m)) return $m[1].((int)$m[2]+1);
        throw new RuntimeException('unsupported_version_format');
    }
    private static function write(string $path, string $bytes): void {
        $tmp = tempnam(dirname($path),'.msw-suite-');
        if (!$tmp) throw new RuntimeException('temporary_file_failed');
        try {
            if (file_put_contents($tmp,$bytes,LOCK_EX)!==strlen($bytes)) throw new RuntimeException('patch_write_failed');
            chmod($tmp,is_file($path)?(fileperms($path)&0777):0644);
            if (!rename($tmp,$path)) throw new RuntimeException('atomic_patch_failed');
            clearstatcache(true,$path);
            if (!hash_equals(hash('sha256',$bytes),(string)hash_file('sha256',$path))) throw new RuntimeException('patch_readback_failed');
            if (function_exists('opcache_invalidate')) opcache_invalidate($path,true);
        } finally { if (is_file($tmp)) unlink($tmp); }
    }
    public static function apply(): void {
        if (!current_user_can('activate_plugins') || !current_user_can('manage_options')) throw new RuntimeException('administrator_required');
        $plan = json_decode((string)file_get_contents(__DIR__.'/plan.json'),true);
        $host = strtolower((string)wp_parse_url(home_url('/'),PHP_URL_HOST));
        $site = is_array($plan)?($plan['sites'][$host]??null):null;
        if (!is_array($site)) throw new RuntimeException('site_not_in_upgrade_plan');
        $existing = get_option(self::RECEIPT,[]);
        if (is_array($existing) && ($existing['state']??'')==='applied') return;
        $lock = wp_generate_uuid4();
        if (!add_option(self::RECEIPT.'_lock',['token'=>$lock,'at'=>time()],'','no')) throw new RuntimeException('upgrade_already_running');
        $pending = []; $backups = []; $applied = []; $rows = []; $before = []; $backupCreated = false;
        try {
            $common = $site['common'];
            $helper = self::path(dirname($common).'/suite-health.php');
            $newHelper = (string)file_get_contents(__DIR__.'/suite-health.php');
            if (!hash_equals((string)$plan['helper_sha256'],hash('sha256',$newHelper))) throw new RuntimeException('helper_digest_failed');
            token_get_all($newHelper,TOKEN_PARSE);
            if (is_file($helper) && !hash_equals(hash('sha256',$newHelper),(string)hash_file('sha256',$helper))) throw new RuntimeException('existing_helper_changed');
            $pending[$helper] = $newHelper;
            $before[$helper] = is_file($helper)?hash_file('sha256',$helper):null;
            foreach ($site['plugins'] as $item) {
                $path = self::path($item['file']);
                $source = file_get_contents($path);
                if (!is_string($source) || strlen($source)>2000000) throw new RuntimeException('plugin_source_unavailable');
                $header = get_file_data($path,['Version'=>'Version']);
                if (($header['Version']??'')!==$item['expected_version']) throw new RuntimeException('plugin_version_changed:'.$item['file']);
                if (str_contains($source,self::MARKER)) throw new RuntimeException('incomplete_prior_upgrade');
                $next = self::version($item['expected_version']);
                $changed = 0;
                $new = preg_replace_callback('/^(\s*\*?\s*Version:\s*)[^\r\n]+/m', static fn($m)=>$m[1].$next,$source,1,$changed);
                if ($changed!==1 || !is_string($new)) throw new RuntimeException('plugin_version_header_missing');
                foreach ((array)($item['replacements']??[]) as $replace) {
                    if (substr_count($new,$replace['old'])!==1) throw new RuntimeException('source_patch_match_failed:'.$item['file']);
                    $new = str_replace($replace['old'],$replace['new'],$new);
                }
                $extra = self::MARKER."\n";
                if ($item['file']===$common) $extra .= "require_once __DIR__ . '/suite-health.php';\n";
                $contract = var_export((array)($item['contract']??[]),true);
                $extra .= "add_action('plugins_loaded', static function (): void {\n    if (class_exists('MSW_Suite_Health_V1', false)) \\MSW_Suite_Health_V1::register(__FILE__, ".$contract.");\n}, 35);";
                $new = self::append($new,$extra);
                token_get_all($new,TOKEN_PARSE);
                $pending[$path] = $new;
                $before[$path] = hash('sha256',$source);
                $rows[] = ['plugin'=>$item['file'],'from'=>$item['expected_version'],'to'=>$next,
                    'source_before_sha256'=>hash('sha256',$source),'source_after_sha256'=>hash('sha256',$new),'contract_version'=>'1.0.0'];
            }
            foreach ((array)($site['asset_patches']??[]) as $item) {
                $path=self::path($item['file']);$source=file_get_contents($path);
                if (!is_string($source) || strlen($source)>2000000) throw new RuntimeException('asset_source_unavailable');
                $new=$source;
                foreach ($item['replacements'] as $replace) {
                    if (substr_count($new,$replace['old'])!==1) throw new RuntimeException('asset_patch_match_failed:'.$item['file']);
                    $new=str_replace($replace['old'],$replace['new'],$new);
                }
                if (str_ends_with($path,'.php')) token_get_all($new,TOKEN_PARSE);
                $pending[$path]=$new;$before[$path]=hash('sha256',$source);
            }
            // Prepare every file and durable rollback before changing any source.
            foreach ($pending as $path=>$bytes) {
                $current = is_file($path)?hash_file('sha256',$path):null;
                if ($current!==$before[$path]) throw new RuntimeException('source_changed_before_backup');
                $relative = substr($path,strlen(rtrim(WP_PLUGIN_DIR,'/'))+1);
                $backups[$relative] = is_file($path)?base64_encode((string)file_get_contents($path)):null;
            }
            if (get_option(self::BACKUP,null)!==null) throw new RuntimeException('rollback_already_exists');
            if (!add_option(self::BACKUP,$backups,'','no') || get_option(self::BACKUP,[])!==$backups) throw new RuntimeException('rollback_backup_failed');
            $backupCreated = true;
            foreach ($pending as $path=>$bytes) {
                if ((is_file($path)?hash_file('sha256',$path):null)!==$before[$path]) throw new RuntimeException('source_changed_before_write');
                $applied[]=$path;
                self::write($path,$bytes);
            }
            wp_clean_plugins_cache(false);
            update_option(self::RECEIPT,['schema'=>'msw.suite.upgrade.v1','state'=>'applied','site'=>$host,'at_utc'=>gmdate('c'),
                'plugins'=>$rows,'rollback_available'=>true,'syntax_verified'=>true,'source_readback_verified'=>true,
                'publication_or_payment_changes'=>false,'new_schedules'=>false],false);
            $receipt=get_option(self::RECEIPT,[]);
            if (($receipt['state']??'')!=='applied' || count($receipt['plugins']??[])!==count($rows)) throw new RuntimeException('upgrade_receipt_not_verified');
        } catch (Throwable $error) {
            $restored = true;
            foreach (array_reverse($applied) as $path) {
                $relative = substr($path,strlen(rtrim(WP_PLUGIN_DIR,'/'))+1);
                try {
                    if ($backups[$relative]===null) { if (is_file($path) && !unlink($path)) throw new RuntimeException('rollback_delete_failed'); }
                    else self::write($path,(string)base64_decode($backups[$relative],true));
                } catch (Throwable $rollback) { $restored=false; }
            }
            wp_clean_plugins_cache(false);
            if ($restored && $backupCreated) delete_option(self::BACKUP);
            update_option(self::RECEIPT,['state'=>'failed','site'=>$host,'at_utc'=>gmdate('c'),'code'=>substr($error->getMessage(),0,180),'rollback_verified'=>$restored],false);
            throw $error;
        } finally {
            if ((get_option(self::RECEIPT.'_lock',[])['token']??'')===$lock) delete_option(self::RECEIPT.'_lock');
        }
    }
}
register_activation_hook(__FILE__, [MSW_Suite_Upgrade_20261105::class,'apply']);

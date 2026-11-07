<?php
/**
 * Plugin Name: MSW Auxiliary Origin Pressure Rollback Once
 * Description: Kontrolowane przywracanie transportu z wczesniejszej prywatnej kopii.
 * Version: 1.0.0
 * Author: MSW
 */
if (!defined('ABSPATH')) exit;

function msw_aux_origin_rollback_fail(string $code): void { throw new RuntimeException($code); }
function msw_aux_origin_rollback_activate(): void {
    try { msw_aux_origin_rollback_run(); }
    catch (Throwable $error) {
        $code=$error instanceof RuntimeException?$error->getMessage():'unexpected_failure';
        wp_die('MSW Auxiliary origin rollback: '.esc_html($code),'MSW Auxiliary origin rollback',['response'=>409]);
    }
}
function msw_aux_origin_rollback_run(): void {
    if (strtolower((string)wp_parse_url(home_url('/'),PHP_URL_HOST))!=='mobilny-serwis-warszawa.pl')
        msw_aux_origin_rollback_fail('wrong_site');
    $original='31afd7e67befbd06c00dd0516409463e5cc557ae461d403e2f36851efffc3e80';
    $guarded='d6139dbd218233075feea0ebd47b523db5b3c59c7de1618c7a334439fa390a36';
    $root=realpath(rtrim(ABSPATH,'/'));$plugins=realpath(WP_PLUGIN_DIR);
    if (!$root || !$plugins || $plugins!==$root.'/wp-content/plugins')
        msw_aux_origin_rollback_fail('plugin_root_mismatch');
    $includes=$plugins.'/msw-unified-engine';$target=$includes.'/transport.php';
    if (is_link($includes) || realpath($includes)!==$includes || is_link($target) || realpath($target)!==$target)
        msw_aux_origin_rollback_fail('unsafe_target');
    $public=dirname($root);$accountRoot=dirname($public);
    if (basename($public)!=='public_html') msw_aux_origin_rollback_fail('private_backup_unavailable');
    $base=$accountRoot.'/msw-doctor-quarantine';
    $dir=$base.'/aux-origin-transport-20261108';
    if (is_link($base)||realpath($base)!==$base||is_link($dir)||realpath($dir)!==$dir)
        msw_aux_origin_rollback_fail('unsafe_backup_directory');
    $backup=$dir.'/transport.original.php';$lockpath=$dir.'/install.lock';
    if (is_link($backup)||!is_file($backup)||!hash_equals($original,(string)hash_file('sha256',$backup))||
        is_link($lockpath)||!is_file($lockpath)) msw_aux_origin_rollback_fail('backup_unverified');
    $lock=fopen($lockpath,'r+');
    if (!$lock || !flock($lock,LOCK_EX|LOCK_NB)) msw_aux_origin_rollback_fail('install_busy');
    $tmp=null;
    try {
        clearstatcache(true,$target);
        $current=(string)hash_file('sha256',$target);
        if (hash_equals($original,$current)) return;
        if (!hash_equals($guarded,$current)) msw_aux_origin_rollback_fail('source_changed');
        if (!hash_equals($original,(string)hash_file('sha256',$backup))) msw_aux_origin_rollback_fail('backup_unverified');
        $raw=file_get_contents($backup);$perms=fileperms($target);
        if (!is_string($raw)||!is_int($perms)) msw_aux_origin_rollback_fail('read_failed');
        $tmp=tempnam($includes,'.msw-aux-rollback-');
        if (!$tmp||dirname($tmp)!==$includes||is_link($tmp)) msw_aux_origin_rollback_fail('staging_failed');
        if (file_put_contents($tmp,$raw,LOCK_EX)!==strlen($raw)||
            !hash_equals($original,(string)hash_file('sha256',$tmp))||!chmod($tmp,$perms & 0777))
            msw_aux_origin_rollback_fail('staged_verification_failed');
        clearstatcache(true,$target);
        if (!hash_equals($guarded,(string)hash_file('sha256',$target))) msw_aux_origin_rollback_fail('source_changed_before_replace');
        if (!rename($tmp,$target)) msw_aux_origin_rollback_fail('atomic_replace_failed');
        $tmp=null;clearstatcache(true,$target);
        if (!hash_equals($original,(string)hash_file('sha256',$target))) msw_aux_origin_rollback_fail('readback_failed_needs_review');
        $cache=function_exists('opcache_invalidate')?@opcache_invalidate($target,true):false;
        update_option('msw_aux_origin_rollback_20261108',['state'=>'original_readback_verified',
            'restored_sha256'=>$original,'from_sha256'=>$guarded,'opcache_invalidate_returned_true'=>$cache,
            'at'=>gmdate('c'),'credentials_modified_by_installer'=>false],false);
    } finally {
        if (is_string($tmp)&&is_file($tmp)&&!is_link($tmp)) unlink($tmp);
        flock($lock,LOCK_UN);fclose($lock);
    }
}
register_activation_hook(__FILE__,'msw_aux_origin_rollback_activate');

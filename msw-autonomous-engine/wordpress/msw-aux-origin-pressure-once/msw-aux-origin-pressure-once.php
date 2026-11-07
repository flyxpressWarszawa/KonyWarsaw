<?php
/**
 * Plugin Name: MSW Auxiliary Origin Pressure Guard Once
 * Description: Jednorazowa ochrona pobierania zadan drugiego workera przy przeciazeniu konta.
 * Version: 1.0.0
 * Author: MSW
 */
if(!defined('ABSPATH'))exit;

function msw_aux_origin_transport_fail(string $code): void {
    throw new RuntimeException($code);
}

function msw_aux_origin_transport_activate(): void {
    try {
        msw_aux_origin_transport_run();
    } catch(Throwable $error) {
        $code=$error instanceof RuntimeException?$error->getMessage():'unexpected_failure';
        wp_die('MSW Auxiliary origin guard: '.esc_html($code),'MSW Auxiliary origin guard',['response'=>409]);
    }
}

function msw_aux_origin_transport_run(): void {
    // WordPress/Jetpack authorizes activation before invoking this hook. Some
    // signed remote activation contexts do not populate the current WP user.
    if(strtolower((string)wp_parse_url(home_url('/'),PHP_URL_HOST))!=='mobilny-serwis-warszawa.pl')
        msw_aux_origin_transport_fail('wrong_site');
    $before='31afd7e67befbd06c00dd0516409463e5cc557ae461d403e2f36851efffc3e80';
    $after='d6139dbd218233075feea0ebd47b523db5b3c59c7de1618c7a334439fa390a36';
    $root=realpath(rtrim(ABSPATH,'/'));
    $plugins=realpath(WP_PLUGIN_DIR);
    if(!$root || !$plugins || $plugins!==$root.'/wp-content/plugins')msw_aux_origin_transport_fail('plugin_root_mismatch');
    $includes=$plugins.'/msw-unified-engine';
    $target=$includes.'/transport.php';
    $helper=$includes.'/msw-render-account-health.php';
    if (is_link($helper) || !is_file($helper) ||
        !hash_equals('84cd15e106644430ad4a4b6446b89aa083afdd7716aa319eeafcd45b59f08fbe', (string)hash_file('sha256',$helper)))
        msw_aux_origin_transport_fail('health_reader_mismatch');
    if(is_link($includes) || realpath($includes)!==$includes || is_link($target) || realpath($target)!==$target)
        msw_aux_origin_transport_fail('unsafe_target');
    $public=dirname($root);$accountRoot=dirname($public);
    if(basename($public)!=='public_html' || !is_dir($accountRoot) || !is_writable($accountRoot))
        msw_aux_origin_transport_fail('private_backup_unavailable');
    $base=$accountRoot.'/msw-doctor-quarantine';
    if(is_link($base) || (!is_dir($base) && !mkdir($base,0700)) || realpath($base)!==$base)
        msw_aux_origin_transport_fail('unsafe_backup_root');
    $backupdir=$base.'/aux-origin-transport-20261108';
    if(is_link($backupdir) || (!is_dir($backupdir) && !mkdir($backupdir,0700)) || realpath($backupdir)!==$backupdir)
        msw_aux_origin_transport_fail('unsafe_backup_directory');
    if(!chmod($backupdir,0700))msw_aux_origin_transport_fail('backup_permissions_failed');
    $lockpath=$backupdir.'/install.lock';
    if(is_link($lockpath))msw_aux_origin_transport_fail('unsafe_lock');
    $lock=fopen($lockpath,'c');
    if(!$lock || !flock($lock,LOCK_EX|LOCK_NB))msw_aux_origin_transport_fail('install_busy');
    $tmp=null;
    try {
        $current=hash_file('sha256',$target);
        $backup=$backupdir.'/transport.original.php';
        if(is_link($backup))msw_aux_origin_transport_fail('unsafe_backup');
        if(hash_equals($after,(string)$current)){
            if(!is_file($backup) || !hash_equals($before,(string)hash_file('sha256',$backup)))
                msw_aux_origin_transport_fail('already_updated_backup_unverified');
            return;
        }
        if(!hash_equals($before,(string)$current))msw_aux_origin_transport_fail('source_changed');
        $payload=__DIR__.'/payload/transport.php';
        if(is_link($payload) || !is_file($payload) || !hash_equals($after,(string)hash_file('sha256',$payload)))
            msw_aux_origin_transport_fail('payload_mismatch');
        $original=file_get_contents($target);$replacement=file_get_contents($payload);
        if(!is_string($original) || !is_string($replacement))msw_aux_origin_transport_fail('read_failed');
        if(!file_exists($backup)){
            $backuphandle=fopen($backup,'x');
            if(!$backuphandle)msw_aux_origin_transport_fail('backup_create_failed');
            $written=fwrite($backuphandle,$original);fflush($backuphandle);fclose($backuphandle);
            if(!chmod($backup,0600))msw_aux_origin_transport_fail('backup_permissions_failed');
            if($written!==strlen($original))msw_aux_origin_transport_fail('backup_write_failed');
        }
        if(!is_file($backup) || !hash_equals($before,(string)hash_file('sha256',$backup)))
            msw_aux_origin_transport_fail('backup_verification_failed');
        $permissions=fileperms($target);
        if(!is_int($permissions))msw_aux_origin_transport_fail('source_permissions_unknown');
        $tmp=tempnam($includes,'.msw-aux-origin-');
        if(!$tmp || dirname($tmp)!==$includes || is_link($tmp))msw_aux_origin_transport_fail('staging_failed');
        if(file_put_contents($tmp,$replacement,LOCK_EX)!==strlen($replacement) ||
                !hash_equals($after,(string)hash_file('sha256',$tmp)))msw_aux_origin_transport_fail('staged_verification_failed');
        if(!chmod($tmp,$permissions & 0777))msw_aux_origin_transport_fail('staged_permissions_failed');
        clearstatcache(true,$target);
        if(!hash_equals($before,(string)hash_file('sha256',$target)))msw_aux_origin_transport_fail('source_changed_before_replace');
        if(!rename($tmp,$target))msw_aux_origin_transport_fail('atomic_replace_failed');
        $tmp=null;clearstatcache(true,$target);
        $readback=hash_file('sha256',$target);
        if(!hash_equals($after,(string)$readback))msw_aux_origin_transport_fail('readback_failed_needs_review');
        $cache=function_exists('opcache_invalidate')?@opcache_invalidate($target,true):false;
        update_option('msw_aux_origin_transport_20261108',[
            'state'=>'file_readback_verified','before_sha256'=>$before,'after_sha256'=>$after,
            'opcache_invalidate_returned_true'=>$cache,'at'=>gmdate('c'),
            'credentials_modified_by_installer'=>false
        ],false);
    } finally {
        if(is_string($tmp) && is_file($tmp) && !is_link($tmp))unlink($tmp);
        flock($lock,LOCK_UN);fclose($lock);
    }
}
register_activation_hook(__FILE__,'msw_aux_origin_transport_activate');

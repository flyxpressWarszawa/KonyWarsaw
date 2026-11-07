<?php
/** Atomic transport projections; no task execution or scheduling. */
defined('ABSPATH') || exit;

final class MSW_GCB_Options
{
    public const PREFIX='msw_gcb_task_';
    public const POLICY='msw_gcb_policy_v1';
    public static function raw(string $name): ?string
    {
        global $wpdb;$value=$wpdb->get_var($wpdb->prepare("SELECT option_value FROM {$wpdb->options} WHERE option_name=%s",$name));
        if($wpdb->last_error!=='')MSW_GCB_Contract::fail('storage_read',503);
        return is_string($value)?$value:null;
    }
    public static function flush(string $name): void
    {
        wp_cache_delete($name,'options');wp_cache_delete('notoptions','options');wp_cache_delete('alloptions','options');
    }
    public static function insert(string $name,string $raw): bool
    {
        global $wpdb;$n=$wpdb->query($wpdb->prepare("INSERT IGNORE INTO {$wpdb->options} (option_name,option_value,autoload) VALUES (%s,%s,'no')",$name,$raw));
        if($n===false)MSW_GCB_Contract::fail('storage_write',503);self::flush($name);return $n===1;
    }
    public static function cas(string $name,string $before,string $after,array $guards=[]): bool
    {
        global $wpdb;$joins='';$where='';$joinArgs=[];$guardArgs=[];
        foreach($guards as $guardName=>$guardRaw){
            $alias='g'.count($joinArgs);$joins.=" INNER JOIN {$wpdb->options} AS $alias ON $alias.option_name=%s";
            $where.=" AND BINARY $alias.option_value=BINARY %s";$joinArgs[]=$guardName;$guardArgs[]=$guardRaw;
        }
        $q="UPDATE {$wpdb->options} AS task$joins SET task.option_value=%s WHERE task.option_name=%s AND BINARY task.option_value=BINARY %s$where";
        $n=$wpdb->query($wpdb->prepare($q,...array_merge($joinArgs,[$after,$name,$before],$guardArgs)));
        if($n===false)MSW_GCB_Contract::fail('storage_write',503);self::flush($name);return $n===1;
    }
    public static function read(string $id): ?array
    {
        if(!preg_match('/\A[a-f0-9]{32}\z/D',$id))MSW_GCB_Contract::fail('task_id');
        $raw=self::raw(self::PREFIX.$id);return $raw===null?null:['raw'=>$raw,'value'=>MSW_GCB_Contract::json($raw)];
    }
    public static function rows(int $limit=50): array
    {
        global $wpdb;$limit=max(1,min(100,$limit));
        $rows=$wpdb->get_results($wpdb->prepare("SELECT option_name,option_value FROM {$wpdb->options} WHERE option_name LIKE %s ORDER BY option_id DESC LIMIT %d",$wpdb->esc_like(self::PREFIX).'%',$limit),ARRAY_A);
        if($wpdb->last_error!=='')MSW_GCB_Contract::fail('storage_read',503);return is_array($rows)?$rows:[];
    }
    public static function pending(): array
    {
        global $wpdb;
        $rows=$wpdb->get_results($wpdb->prepare("SELECT option_name,option_value FROM {$wpdb->options} WHERE option_name LIKE %s AND (option_value LIKE %s OR option_value LIKE %s) ORDER BY option_id ASC LIMIT 100",$wpdb->esc_like(self::PREFIX).'%', '%"state":"recorded"%', '%"state":"leased"%'),ARRAY_A);
        if($wpdb->last_error!=='')MSW_GCB_Contract::fail('storage_read',503);return is_array($rows)?$rows:[];
    }
}

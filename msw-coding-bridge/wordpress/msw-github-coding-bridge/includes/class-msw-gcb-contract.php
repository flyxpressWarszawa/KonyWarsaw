<?php
/** Pure public-task contract and pinned GitHub OIDC verification. */
defined('ABSPATH') || exit;

final class MSW_GCB_Fault extends RuntimeException
{
    public function __construct(public string $reason, public int $status = 400) { parent::__construct($reason); }
}

final class MSW_GCB_Contract
{
    public const REPOSITORY = 'flyxpressWarszawa/KonyWarsaw';
    public const REPOSITORY_ID = '405190198';
    public const OWNER_ID = '90477347';
    public const BRANCH = 'msw/coding-queue';
    public const WORKFLOW = self::REPOSITORY.'/.github/workflows/msw-coding-bridge.yml@refs/heads/main';
    public const ISSUER = 'https://token.actions.githubusercontent.com';
    public const JWKS = self::ISSUER.'/.well-known/jwks';
    public const AUDIENCE = 'https://mobilny-serwis-warszawa.pl/msw-github-coding';
    public const TARGETS = [
        'technical-core'=>['branch'=>'main','support'=>'github_agent_scope'],
        'portal'=>['branch'=>'main','support'=>'github_agent_scope'],
        'compute-adapters'=>['branch'=>'main','support'=>'github_agent_scope'],
        'research'=>['branch'=>'main','support'=>'github_agent_scope'],
        'runtime'=>['branch'=>'msw/engine-runtime-20261104','support'=>'chatgpt_branch_worker'],
        'owned-llm'=>['branch'=>'msw/local-llm-20261031','support'=>'chatgpt_branch_worker'],
        'wordpress'=>['branch'=>null,'support'=>'source_sync_required'],
    ];
    public const INPUT_KEYS = ['request_id','source_engine','target','title','objective','acceptance','public_data'];

    public static function fail(string $reason, int $status = 400): never { throw new MSW_GCB_Fault($reason,$status); }

    public static function json(string $raw, int $limit = 32768): array
    {
        if (strlen($raw)>$limit || !str_starts_with(ltrim($raw),'{')) self::fail('json_object');
        try { $value=json_decode($raw,true,32,JSON_THROW_ON_ERROR); }
        catch (JsonException $e) { self::fail('invalid_json'); }
        if (!is_array($value)) self::fail('json_object');
        return $value;
    }

    public static function canonical($value): string
    {
        $sort=static function($item) use (&$sort) {
            if (!is_array($item)) return $item;
            if (!array_is_list($item)) ksort($item,SORT_STRING);
            foreach($item as $key=>$child) $item[$key]=$sort($child);
            return $item;
        };
        try { return json_encode($sort($value),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_LINE_TERMINATORS|JSON_THROW_ON_ERROR); }
        catch (JsonException $e) { self::fail('invalid_json'); }
    }

    public static function keys(array $value, array $keys): void
    {
        if (array_diff(array_keys($value),$keys) || array_diff($keys,array_keys($value))) self::fail('fields');
    }

    public static function text($value, int $max): bool
    {
        // Python str.strip() whitespace and Unicode code-point length, without mbstring.
        return is_string($value) && strlen($value)<=$max*4 &&
            preg_match('/\A[\s\p{Z}\x{0085}\x{001c}-\x{001f}]*\z/u',$value)===0 &&
            ($n=preg_match_all('/./us',$value))!==false && $n>=1 && $n<=$max;
    }

    public static function input(array $input, array $sources): array
    {
        self::keys($input,self::INPUT_KEYS);
        if (!is_string($input['request_id']) || !preg_match('/\A[A-Za-z0-9_.:-]{8,128}\z/D',$input['request_id'])) self::fail('request_id');
        if (!is_string($input['source_engine']) || !preg_match('~\A[A-Za-z0-9_./:-]{1,200}\z~D',$input['source_engine']) || !in_array($input['source_engine'],$sources,true)) self::fail('source_engine');
        if (!is_string($input['target']) || !isset(self::TARGETS[$input['target']])) self::fail('target');
        if ($input['public_data']!==true) self::fail('public_data_required');
        if (!self::text($input['title'],120) || !self::text($input['objective'],4000)) self::fail('text');
        if (!is_array($input['acceptance']) || !array_is_list($input['acceptance']) || count($input['acceptance'])<1 || count($input['acceptance'])>8) self::fail('acceptance');
        foreach($input['acceptance'] as $criterion) if(!self::text($criterion,400)) self::fail('acceptance');
        $raw=self::canonical($input);
        if (strlen($raw)>15500) self::fail('task_size',413);
        if (preg_match('/-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}|AKIA[A-Z0-9]{16})\b/',$raw)) self::fail('secret_like_content');
        return $input;
    }

    public static function id(array $input): string { return substr(hash('sha256',$input['source_engine'].':'.$input['request_id']),0,32); }

    public static function task(array $input, int $created): array
    {
        $task=$input+['schema'=>'msw.coding-task.v1','id'=>self::id($input),'created_at'=>gmdate('Y-m-d\TH:i:s\Z',$created)];
        $task['request_sha256']=hash('sha256',self::canonical($task));
        if(strlen(self::canonical($task)."\n")>16384) self::fail('task_size',413);
        return $task;
    }

    private static function b64(string $value): string
    {
        if($value==='' || !preg_match('/\A[A-Za-z0-9_-]+\z/D',$value)) self::fail('oidc_encoding',401);
        $decoded=base64_decode(strtr($value,'-_','+/').str_repeat('=',(4-strlen($value)%4)%4),true);
        if($decoded===false || rtrim(strtr(base64_encode($decoded),'+/','-_'),'=')!==$value) self::fail('oidc_encoding',401);
        return $decoded;
    }
    private static function der(int $tag,string $value): string
    {
        $n=strlen($value);if($n<128)return chr($tag).chr($n).$value;
        $bytes='';while($n>0){$bytes=chr($n&255).$bytes;$n>>=8;}
        return chr($tag).chr(128|strlen($bytes)).$bytes.$value;
    }
    private static function integer(string $value): string
    {
        $value=ltrim($value,"\0");if($value==='')self::fail('oidc_key',401);
        if(ord($value[0])&128)$value="\0".$value;
        return self::der(2,$value);
    }

    public static function oidc(string $token,array $jwks,int $now): array
    {
        if(strlen($token)>24576 || count($parts=explode('.',$token))!==3)self::fail('oidc_token',401);
        [$h,$p,$s]=$parts;$header=self::json(self::b64($h),2048);
        if(($header['alg']??'')!=='RS256' || ($header['typ']??'')!=='JWT' || !is_string($header['kid']??null) ||
            !preg_match('/\A[A-Za-z0-9_.:-]{1,200}\z/D',$header['kid']) || array_diff(array_keys($header),['alg','typ','kid','x5t']))self::fail('oidc_header',401);
        $keys=array_values(array_filter((array)($jwks['keys']??[]),static fn($k)=>is_array($k)&&($k['kid']??null)===$header['kid']));
        if(count($keys)!==1)self::fail('oidc_key_unknown',401);$key=$keys[0];
        if(($key['kty']??'')!=='RSA' || ($key['use']??'')!=='sig' || (isset($key['alg'])&&$key['alg']!=='RS256') || !is_string($key['n']??null) || !is_string($key['e']??null))self::fail('oidc_key',401);
        $n=self::b64($key['n']);$e=self::b64($key['e']);
        if(strlen($n)<256 || strlen($n)>1024 || strlen($e)>4)self::fail('oidc_key',401);
        $der=self::der(0x30,self::integer($n).self::integer($e));
        $pem="-----BEGIN RSA PUBLIC KEY-----\n".chunk_split(base64_encode($der),64,"\n")."-----END RSA PUBLIC KEY-----\n";
        if(!function_exists('openssl_verify') || openssl_verify($h.'.'.$p,self::b64($s),$pem,OPENSSL_ALGO_SHA256)!==1)self::fail('oidc_signature',401);
        $c=self::json(self::b64($p),20000);
        $fixed=['iss'=>self::ISSUER,'aud'=>self::AUDIENCE,'repository'=>self::REPOSITORY,'repository_id'=>self::REPOSITORY_ID,
            'repository_owner_id'=>self::OWNER_ID,'ref'=>'refs/heads/main','workflow_ref'=>self::WORKFLOW,'runner_environment'=>'github-hosted'];
        foreach($fixed as $field=>$value)if(($c[$field]??null)!==$value)self::fail('oidc_claim_'.$field,401);
        $subjects=['repo:'.self::REPOSITORY.':ref:refs/heads/main','repo:flyxpressWarszawa@'.self::OWNER_ID.'/KonyWarsaw@'.self::REPOSITORY_ID.':ref:refs/heads/main'];
        if(!in_array($c['sub']??null,$subjects,true) || !in_array($c['event_name']??null,['push','schedule','workflow_dispatch'],true))self::fail('oidc_subject_event',401);
        foreach(['run_id','run_attempt'] as $field)if(!is_string($c[$field]??null)||!preg_match('/\A[1-9][0-9]{0,19}\z/D',$c[$field]))self::fail('oidc_run',401);
        if(!is_string($c['sha']??null)||!preg_match('/\A[a-f0-9]{40}\z/D',$c['sha']))self::fail('oidc_sha',401);
        foreach(['iat','nbf','exp'] as $field)if(!is_int($c[$field]??null))self::fail('oidc_time',401);
        if($c['exp']<=$now || $c['nbf']>$now+30 || $c['iat']>$now+30 || $c['iat']<$now-630 ||
            $c['exp']<=$c['iat'] || $c['exp']-$c['iat']>600 || $c['nbf']>=$c['exp'])self::fail('oidc_time',401);
        return ['run_id'=>$c['run_id'],'run_attempt'=>$c['run_attempt'],'workflow_sha'=>$c['sha'],'expires_at'=>$c['exp']];
    }
}

<?php
/** Pure contract tests. RSA keys are generated in RAM for this offline suite. */

final class MSW_GCB_Contract_TestData
{
    public const NOW = 1794000000;
    private static ?OpenSSLAsymmetricKey $key = null;

    public static function input(): array
    {
        $task = json_decode(file_get_contents(__DIR__.'/../fixtures/task-utf8.json'), true, 32, JSON_THROW_ON_ERROR);
        return array_intersect_key($task, array_fill_keys(MSW_GCB_Contract::INPUT_KEYS, true));
    }

    public static function key(): OpenSSLAsymmetricKey
    {
        if (self::$key === null) {
            $key = openssl_pkey_new(['private_key_type'=>OPENSSL_KEYTYPE_RSA, 'private_key_bits'=>2048]);
            if (!$key instanceof OpenSSLAsymmetricKey) throw new RuntimeException('test_rsa_key_generation_failed');
            self::$key = $key;
        }
        return self::$key;
    }

    public static function b64(string $value): string
    {
        return rtrim(strtr(base64_encode($value), '+/', '-_'), '=');
    }

    public static function jwks(): array
    {
        $details = openssl_pkey_get_details(self::key());
        return ['keys'=>[['kid'=>'test-key-only', 'kty'=>'RSA', 'alg'=>'RS256', 'use'=>'sig',
            'n'=>self::b64($details['rsa']['n']), 'e'=>self::b64($details['rsa']['e'])]]];
    }

    public static function claims(): array
    {
        return [
            'iss'=>MSW_GCB_Contract::ISSUER, 'aud'=>MSW_GCB_Contract::AUDIENCE,
            'repository'=>MSW_GCB_Contract::REPOSITORY, 'repository_id'=>MSW_GCB_Contract::REPOSITORY_ID,
            'repository_owner_id'=>MSW_GCB_Contract::OWNER_ID, 'ref'=>'refs/heads/main',
            'workflow_ref'=>MSW_GCB_Contract::WORKFLOW, 'runner_environment'=>'github-hosted',
            'sub'=>'repo:'.MSW_GCB_Contract::REPOSITORY.':ref:refs/heads/main',
            'event_name'=>'workflow_dispatch', 'run_id'=>'123456', 'run_attempt'=>'1',
            'sha'=>str_repeat('a', 40), 'iat'=>self::NOW-10, 'nbf'=>self::NOW-10, 'exp'=>self::NOW+290,
        ];
    }

    public static function token(array $claims, ?array $header = null): string
    {
        $header ??= ['alg'=>'RS256', 'typ'=>'JWT', 'kid'=>'test-key-only'];
        $raw = self::b64(json_encode($header, JSON_THROW_ON_ERROR)).'.'.
            self::b64(json_encode($claims, JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR));
        if (!openssl_sign($raw, $signature, self::key(), OPENSSL_ALGO_SHA256)) {
            throw new RuntimeException('test_rsa_signing_failed');
        }
        return $raw.'.'.self::b64($signature);
    }

    public static function verify(array $claims, ?array $header = null, ?array $jwks = null): array
    {
        return MSW_GCB_Contract::oidc(self::token($claims, $header), $jwks ?? self::jwks(), self::NOW);
    }
}

return [
    'contract: shared UTF-8 task is byte-exact with Python' => static function(): void {
        $raw = file_get_contents(__DIR__.'/../fixtures/task-utf8.json');
        $fixture = MSW_GCB_Contract::json($raw);
        $input = MSW_GCB_Contract::input(MSW_GCB_Contract_TestData::input(), ['chatgpt']);
        $created = (new DateTimeImmutable($fixture['created_at']))->getTimestamp();
        $task = MSW_GCB_Contract::task($input, $created);
        expect(MSW_GCB_Contract::canonical($task)."\n" === $raw, 'cross_language_bytes_mismatch');
        expect($task['request_sha256'] === 'c5260202d2428fc1db05ae50bc5e5461fb835fb64ba9d9ac69f1ebd72800b17a');
        expect(hash('sha256', $raw) === '18c100dc0241c704e6f77d9cbbfb8733e5bade0954eb339fba8acae833abf90c');
        expect(str_contains($raw, "\u{2028}") && str_contains($raw, "\u{2029}") && str_contains($raw, '🚗'));
        expect(!str_contains($raw, '\u2028') && !str_contains($raw, '\u2029'));
    },
    'contract: all target branches and routes agree with shared manifest' => static function(): void {
        $manifest = json_decode(file_get_contents(dirname(__DIR__, 2).'/targets.json'), true, 32, JSON_THROW_ON_ERROR);
        $actual = [];
        foreach ($manifest['targets'] as $name=>$target) {
            $actual[$name] = ['branch'=>$target['branch'], 'support'=>$target['execution_support']];
        }
        expect(MSW_GCB_Contract::canonical($actual) === MSW_GCB_Contract::canonical(MSW_GCB_Contract::TARGETS));
    },
    'contract: key order is immaterial while array order is preserved' => static function(): void {
        $one = ['z'=>['b'=>1, 'a'=>2], 'a'=>['second','first']];
        $two = ['a'=>['second','first'], 'z'=>['a'=>2, 'b'=>1]];
        expect(MSW_GCB_Contract::canonical($one) === MSW_GCB_Contract::canonical($two));
        $two['a'] = array_reverse($two['a']);
        expect(MSW_GCB_Contract::canonical($one) !== MSW_GCB_Contract::canonical($two));
    },
    'contract: identity is stable and binds the original source and request' => static function(): void {
        $input = MSW_GCB_Contract_TestData::input();
        $one = MSW_GCB_Contract::task($input, MSW_GCB_Contract_TestData::NOW);
        $two = MSW_GCB_Contract::task($input, MSW_GCB_Contract_TestData::NOW);
        expect($one === $two);
        expect($one['id'] === 'f64fd7973b17aeadf2bc219a48dbbf44');
        $input['request_id'] .= '-other';
        expect(MSW_GCB_Contract::id($input) !== $one['id']);
        $input = MSW_GCB_Contract_TestData::input();
        $input['source_engine'] = 'render';
        expect(MSW_GCB_Contract::id($input) !== $one['id']);
        expect($one['request_sha256'] !== hash('sha256', MSW_GCB_Contract::canonical($one)."\n"));
    },
    'contract: unexpected and missing input fields are refused' => static function(): void {
        $input = MSW_GCB_Contract_TestData::input();
        $extra = $input + ['url'=>'https://example.invalid'];
        expect_fault('fields', static fn()=>MSW_GCB_Contract::input($extra, ['chatgpt']));
        unset($input['public_data']);
        expect_fault('fields', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
    },
    'contract: public flag requires literal boolean true' => static function(): void {
        foreach ([false, 1, 'true', null] as $flag) {
            $input = MSW_GCB_Contract_TestData::input(); $input['public_data'] = $flag;
            expect_fault('public_data_required', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
        }
    },
    'contract: Unicode code points and whitespace match Python rules' => static function(): void {
        expect(MSW_GCB_Contract::text('🚗', 1));
        expect(!MSW_GCB_Contract::text('🚗🚗', 1));
        foreach (["", " \t\n", "\u{00a0}", "\u{2028}\u{2029}\u{2003}", "\u{0085}\u{001c}\u{001f}", "\xff"] as $text) {
            expect(!MSW_GCB_Contract::text($text, 120), 'blank_or_invalid_unicode_accepted');
        }
        $input = MSW_GCB_Contract_TestData::input(); $input['title'] = str_repeat('Ł', 120);
        expect(MSW_GCB_Contract::input($input, ['chatgpt'])['title'] === $input['title']);
        $input['title'] .= 'Ł';
        expect_fault('text', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
    },
    'contract: source registration does not bypass safe identifier syntax' => static function(): void {
        $base = MSW_GCB_Contract_TestData::input();
        expect_fault('source_engine', static fn()=>MSW_GCB_Contract::input($base, ['render']));
        foreach (['engine?execute=1', 'source with spaces', str_repeat('a', 201)] as $source) {
            $input = $base; $input['source_engine'] = $source;
            expect_fault('source_engine', static fn()=>MSW_GCB_Contract::input($input, [$source]));
        }
        foreach (['short', 'invalid request', str_repeat('a', 129)] as $request) {
            $input = $base; $input['request_id'] = $request;
            expect_fault('request_id', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
        }
    },
    'contract: size criteria and target bounds apply before storage' => static function(): void {
        $base = MSW_GCB_Contract_TestData::input();
        foreach ([[], array_fill(0, 9, 'criterion'), ['key'=>'value'], ["\u{00a0}"], [str_repeat('a', 401)]] as $criteria) {
            $input = $base; $input['acceptance'] = $criteria;
            expect_fault('acceptance', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
        }
        $input = $base; $input['objective'] = str_repeat('🚗', 4000);
        expect_fault('task_size', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
        $input = $base; $input['target'] = 'arbitrary-shell';
        expect_fault('target', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
    },
    'contract: known credential shapes are excluded from public transport' => static function(): void {
        foreach (['ghp_'.str_repeat('a',40), 'github_pat_'.str_repeat('b',50),
                  'sk-proj-'.str_repeat('c',50), '-----BEGIN PRIVATE KEY-----', 'AKIA'.str_repeat('A',16)] as $secret) {
            $input = MSW_GCB_Contract_TestData::input(); $input['objective'] = $secret;
            expect_fault('secret_like_content', static fn()=>MSW_GCB_Contract::input($input, ['chatgpt']));
        }
    },
    'contract: malformed nonfinite oversized and nonobject JSON fail closed' => static function(): void {
        foreach (['{', '{"x":NaN}', "{\"x\":\"\xff\"}"] as $raw) {
            expect_fault('invalid_json', static fn()=>MSW_GCB_Contract::json($raw));
        }
        foreach (['[]', 'null', '"text"', '{"x":"'.str_repeat('a',100).'"}'] as $raw) {
            expect_fault('json_object', static fn()=>MSW_GCB_Contract::json($raw, 32));
        }
        expect_fault('invalid_json', static fn()=>MSW_GCB_Contract::canonical(['x'=>NAN]));
        foreach (['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"outer":[{"nested":1,"\\u006eested":2}]}'] as $raw) {
            expect_fault('duplicate_json_key', static fn()=>MSW_GCB_Contract::json($raw));
        }
        $structured = ['text'=>'{"fake":"key", "brace":"}"} and a quoted "word"',
            'array'=>[['same'=>1], ['same'=>2]], 'nested'=>['text'=>'[] {} , : \\']];
        expect(MSW_GCB_Contract::json(json_encode($structured, JSON_THROW_ON_ERROR)) === $structured);
    },
    'OIDC: real RSA accepts both supported subject formats and returns bounded context' => static function(): void {
        $claims = MSW_GCB_Contract_TestData::claims();
        foreach ([$claims['sub'], 'repo:flyxpressWarszawa@90477347/KonyWarsaw@405190198:ref:refs/heads/main'] as $subject) {
            $claims['sub'] = $subject;
            $result = MSW_GCB_Contract_TestData::verify($claims);
            expect($result === ['run_id'=>'123456', 'run_attempt'=>'1', 'workflow_sha'=>str_repeat('a',40),
                'expires_at'=>MSW_GCB_Contract_TestData::NOW+290]);
        }
    },
    'OIDC: every fixed identity claim is enforced after valid RSA signature' => static function(): void {
        foreach (['iss','aud','repository','repository_id','repository_owner_id','ref','workflow_ref','runner_environment'] as $field) {
            $claims = MSW_GCB_Contract_TestData::claims(); $claims[$field] = 'wrong';
            expect_fault('oidc_claim_'.$field, static fn()=>MSW_GCB_Contract_TestData::verify($claims));
        }
        $claims = MSW_GCB_Contract_TestData::claims(); $claims['repository_id'] = 405190198;
        expect_fault('oidc_claim_repository_id', static fn()=>MSW_GCB_Contract_TestData::verify($claims));
    },
    'OIDC: pull requests dynamic events and foreign subjects are refused' => static function(): void {
        foreach (['pull_request','pull_request_target','dynamic','repository_dispatch'] as $event) {
            $claims = MSW_GCB_Contract_TestData::claims(); $claims['event_name'] = $event;
            expect_fault('oidc_subject_event', static fn()=>MSW_GCB_Contract_TestData::verify($claims));
        }
        foreach (['push','schedule','workflow_dispatch'] as $event) {
            $claims = MSW_GCB_Contract_TestData::claims(); $claims['event_name'] = $event;
            expect(MSW_GCB_Contract_TestData::verify($claims)['run_id'] === '123456');
        }
        $claims = MSW_GCB_Contract_TestData::claims(); $claims['sub'] = 'repo:other/repo:ref:refs/heads/main';
        expect_fault('oidc_subject_event', static fn()=>MSW_GCB_Contract_TestData::verify($claims));
    },
    'OIDC: expiry issue time not-before and lifetime bounds are enforced' => static function(): void {
        $now = MSW_GCB_Contract_TestData::NOW;
        foreach ([['exp'=>$now], ['exp'=>$now-1], ['iat'=>$now+31], ['nbf'=>$now+31],
                  ['iat'=>$now-631], ['exp'=>$now+591], ['nbf'=>$now+290], ['exp'=>'1794000290'],
                  ['iat'=>null], ['nbf'=>1.5]] as $changes) {
            $claims = array_replace(MSW_GCB_Contract_TestData::claims(), $changes);
            expect_fault('oidc_time', static fn()=>MSW_GCB_Contract_TestData::verify($claims));
        }
    },
    'OIDC: run attempt and workflow SHA cannot be ambiguous' => static function(): void {
        foreach (['run_id','run_attempt'] as $field) {
            foreach (['0','01','-1',123456,str_repeat('1',21)] as $value) {
                $claims = MSW_GCB_Contract_TestData::claims(); $claims[$field] = $value;
                expect_fault('oidc_run', static fn()=>MSW_GCB_Contract_TestData::verify($claims));
            }
        }
        foreach (['abc123', str_repeat('A',40), str_repeat('g',40), null] as $sha) {
            $claims = MSW_GCB_Contract_TestData::claims(); $claims['sha'] = $sha;
            expect_fault('oidc_sha', static fn()=>MSW_GCB_Contract_TestData::verify($claims));
        }
    },
    'OIDC: tampered signed payload is refused before trusting claims' => static function(): void {
        $claims = MSW_GCB_Contract_TestData::claims();
        $parts = explode('.', MSW_GCB_Contract_TestData::token($claims));
        $claims['run_id'] = '999999';
        $parts[1] = MSW_GCB_Contract_TestData::b64(json_encode($claims, JSON_THROW_ON_ERROR));
        expect_fault('oidc_signature', static fn()=>MSW_GCB_Contract::oidc(implode('.',$parts),
            MSW_GCB_Contract_TestData::jwks(), MSW_GCB_Contract_TestData::NOW));
    },
    'OIDC: algorithm changes and caller-selected key locations are refused' => static function(): void {
        $header = ['alg'=>'RS256','typ'=>'JWT','kid'=>'test-key-only'];
        foreach ([['alg'=>'none'], ['alg'=>'HS256'], ['typ'=>'other'], ['jku'=>'https://example.invalid/key'],
                  ['jwk'=>['kty'=>'RSA']], ['crit'=>['other']]] as $changes) {
            $candidate = array_replace($header, $changes);
            expect_fault('oidc_header', static fn()=>MSW_GCB_Contract_TestData::verify(MSW_GCB_Contract_TestData::claims(), $candidate));
        }
        $parts = explode('.', MSW_GCB_Contract_TestData::token(MSW_GCB_Contract_TestData::claims()));
        $parts[0] = MSW_GCB_Contract_TestData::b64('{"alg":"none","a\\u006cg":"RS256","typ":"JWT","kid":"test-key-only"}');
        expect_fault('duplicate_json_key', static fn()=>MSW_GCB_Contract::oidc(implode('.',$parts),
            MSW_GCB_Contract_TestData::jwks(), MSW_GCB_Contract_TestData::NOW));
    },
    'OIDC: missing duplicate or unsuitable signing keys are refused' => static function(): void {
        $claims = MSW_GCB_Contract_TestData::claims();
        $header = ['alg'=>'RS256','typ'=>'JWT','kid'=>'unknown-key'];
        expect_fault('oidc_key_unknown', static fn()=>MSW_GCB_Contract_TestData::verify($claims, $header));
        $jwks = MSW_GCB_Contract_TestData::jwks(); $jwks['keys'][] = $jwks['keys'][0];
        expect_fault('oidc_key_unknown', static fn()=>MSW_GCB_Contract_TestData::verify($claims, null, $jwks));
        expect_fault('oidc_key_unknown', static fn()=>MSW_GCB_Contract_TestData::verify($claims, null, ['keys'=>[]]));
        foreach ([['kty'=>'EC'], ['use'=>'enc'], ['alg'=>'HS256'], ['n'=>MSW_GCB_Contract_TestData::b64(str_repeat('a',128))]] as $changes) {
            $jwks = MSW_GCB_Contract_TestData::jwks();
            $jwks['keys'][0] = array_replace($jwks['keys'][0], $changes);
            expect_fault('oidc_key', static fn()=>MSW_GCB_Contract_TestData::verify($claims, null, $jwks));
        }
    },
    'OIDC: malformed framing noncanonical base64 and overlarge tokens are refused' => static function(): void {
        foreach (['a.b', 'a.b.c.d', str_repeat('a',24577)] as $token) {
            expect_fault('oidc_token', static fn()=>MSW_GCB_Contract::oidc($token,
                MSW_GCB_Contract_TestData::jwks(), MSW_GCB_Contract_TestData::NOW));
        }
        $valid = explode('.', MSW_GCB_Contract_TestData::token(MSW_GCB_Contract_TestData::claims()));
        foreach (['***', '', $valid[0].'='] as $bad) {
            $parts = $valid; $parts[0] = $bad;
            expect_fault('oidc_encoding', static fn()=>MSW_GCB_Contract::oidc(implode('.',$parts),
                MSW_GCB_Contract_TestData::jwks(), MSW_GCB_Contract_TestData::NOW));
        }
    },
];

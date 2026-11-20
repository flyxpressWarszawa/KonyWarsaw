#!/usr/bin/env python3
"""Private native Codex entry for new owner text work; never consumes business jobs."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, logging, math, os, pwd, re, stat, sys, tempfile, time, tomllib
from pathlib import Path

ROOT = Path('/var/data/msw-hermes/native-codex-v1/runs')
RELEASE = Path('/var/data/msw-hermes/releases/v0.23.0-2bd378ed1b8d')
BINARY = '/var/data/msw-hermes/components/codex-subscription-0.169.0-20261116/codex'
CODEX_HOME = '/var/data/msw-hermes/codex-home'
MODEL = 'gpt-6.2-sol'
SCHEMA = 'msw.hermes.codex-native.request.v1'
RECEIPT = 'msw.hermes.codex-native.receipt.v1'
KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z')
FLAGS = ['features.shell_tool=false', 'features.unified_exec=false', 'features.apps=false',
         'features.plugins=false', 'features.hooks=false', 'features.multi_agent=false',
         'features.multi_agent_v2=false', 'web_search="disabled"', 'sandbox_mode="read-only"',
         'approval_policy="on-request"', 'forced_login_method="chatgpt"']

class Refused(RuntimeError):
    pass

def require(condition, code):
    if not condition:
        raise Refused(code)

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refused('NONFINITE_JSON')))

def validate(request):
    fields = {'schema', 'idempotency_key', 'prompt', 'token_limit_policy', 'origin'}
    require(type(request) is dict and set(request) == fields, 'NEW_TEXT_REQUEST_FIELDS_REQUIRED')
    require(request['schema'] == SCHEMA and request['origin'] == 'new_owner_task', 'NEW_OWNER_TASK_ONLY')
    require(request['token_limit_policy'] == 'provider_default_no_hard_cap', 'CAPPED_CONTRACT_UNSUPPORTED')
    require(type(request['idempotency_key']) is str and KEY.fullmatch(request['idempotency_key']), 'INVALID_KEY')
    require(type(request['prompt']) is str and request['prompt'].strip()
            and len(request['prompt'].encode()) <= 64000, 'PROMPT_BOUND')
    return request

def private(path):
    require(path.is_absolute(), 'ABSOLUTE_PRIVATE_PATH_REQUIRED')
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'SYMLINK_REFUSED')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0, 'PRIVATE_DIRECTORY_REQUIRED')
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and not info.st_mode & 0o077 and info.st_size <= 2000000, 'PRIVATE_FILE_REQUIRED')
        return parse(stream.read())

def save(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def verify_native(result, stored, thread_id):
    require(not result['error'] and not result['interrupted'] and result['tool_iterations'] == 0,
            'NATIVE_TURN_NOT_CLEAN')
    require(type(result['text']) is str and 0 < len(result['text'].encode()) <= 1000000, 'OUTPUT_BOUND')
    require(result['thread_id'] == thread_id and type(result['turn_id']) is str and result['turn_id'], 'TURN_IDENTITY')
    thread = stored.get('thread', {})
    require(thread.get('id') == thread_id, 'READBACK_THREAD_MISMATCH')
    require(thread.get('model') == MODEL, 'READBACK_MODEL_MISMATCH')
    turns = [t for t in thread.get('turns', []) if t.get('id') == result['turn_id']]
    require(len(turns) == 1 and turns[0].get('status') == 'completed', 'READBACK_TURN_MISMATCH')
    items = turns[0].get('items', [])
    require(all(i.get('type') in {'agentMessage', 'userMessage', 'reasoning'} for i in items), 'TOOL_EFFECT_REFUSED')
    messages = [i.get('text') for i in items if i.get('type') == 'agentMessage']
    require(messages and messages[-1] == result['text'], 'READBACK_TEXT_MISMATCH')

class Entry:
    def __init__(self, root=ROOT, backend_factory=None):
        require(root == ROOT or backend_factory is not None, 'ROOT_OVERRIDE_TESTS_ONLY')
        self.root, self.factory = Path(root), backend_factory or Native

    def cached(self, directory, request):
        receipt = read_json(directory / 'receipt.json')
        body = {k: v for k, v in receipt.items() if k != 'receipt_sha256'}
        require(receipt.get('schema') == RECEIPT and receipt.get('status') == 'VERIFIED'
                and receipt.get('request_sha256') == digest(request)
                and receipt.get('idempotency_key') == request['idempotency_key']
                and receipt.get('model') == MODEL and receipt.get('account_type') == 'chatgpt'
                and receipt.get('token_limit_policy') == request['token_limit_policy']
                and receipt.get('billing_mode') == 'chatgpt_account' and receipt.get('tools') == 0
                and receipt.get('independent_readback') is True and type(receipt.get('text')) is str
                and receipt.get('receipt_sha256') == digest(body)
                and receipt.get('response_sha256') == hashlib.sha256(receipt['text'].encode()).hexdigest(), 'CACHED_RECEIPT_INVALID')
        return receipt

    def read(self, key):
        require(type(key) is str and KEY.fullmatch(key), 'INVALID_KEY')
        directory = self.root / key
        require(directory.exists(), 'REQUEST_NOT_FOUND'); private(directory)
        request = validate(read_json(directory / 'request.json'))
        require(request['idempotency_key'] == key, 'SAVED_REQUEST_KEY_MISMATCH')
        if (directory / 'receipt.json').exists(): return self.cached(directory, request)
        return {**read_json(directory / 'state.json'), 'automatic_retry': False}

    def run(self, request):
        validate(request); private(self.root)
        directory = self.root / request['idempotency_key']; private(directory)
        fd = os.open(directory / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'rb') as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077, 'PRIVATE_LOCK_REQUIRED')
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('REQUEST_BUSY') from None
            if (directory / 'request.json').exists():
                require(digest(read_json(directory / 'request.json')) == digest(request), 'IDEMPOTENCY_CONFLICT')
                if (directory / 'receipt.json').exists(): return self.cached(directory, request)
                raise Refused('RESERVED_OR_UNKNOWN_NO_REDISPATCH')
            state = {'idempotency_key': request['idempotency_key'], 'request_sha256': digest(request),
                     'status': 'RESERVED', 'reserved_at': time.time(), 'automatic_retry': False}
            save(directory / 'request.json', request); save(directory / 'state.json', state)
            backend = None
            try:
                backend = self.factory(directory)
                account = backend.check()
                thread_id = backend.start()
                state.update(status='DISPATCH_INTENT', thread_id=thread_id)
                save(directory / 'state.json', state)  # durable BEFORE run_turn
                result = backend.run(request['prompt'])
                save(directory / 'native-result.json', result)
                backend.close()
                stored = backend.read(thread_id)  # new independent native client
                verify_native(result, stored, thread_id)
                receipt = {'schema': RECEIPT, 'status': 'VERIFIED', 'idempotency_key': request['idempotency_key'],
                           'request_sha256': digest(request), 'model': MODEL, 'account_type': 'chatgpt',
                           'billing_mode': 'chatgpt_account', 'token_limit_policy': request['token_limit_policy'],
                           'thread_id': thread_id, 'turn_id': result['turn_id'], 'text': result['text'],
                           'response_sha256': hashlib.sha256(result['text'].encode()).hexdigest(),
                           'usage': result['usage'], 'independent_readback': True, 'tools': 0,
                           'verified_at': time.time(), 'account_metadata': account, 'automatic_retry': False}
                receipt['receipt_sha256'] = digest(receipt)
                save(directory / 'receipt.json', receipt)
                save(directory / 'state.json', {**state, 'status': 'VERIFIED', 'receipt_sha256': receipt['receipt_sha256']})
                return receipt
            except Exception as exc:
                code = str(exc) if isinstance(exc, Refused) else type(exc).__name__
                save(directory / 'state.json', {**state, 'status': 'UNKNOWN', 'code': code})
                raise Refused('ATTEMPT_INCOMPLETE_NO_REDISPATCH:' + code) from None
            finally:
                if backend is not None: backend.close()

def clean_environment():
    home = ROOT.parent / 'home'; private(home)
    os_home = pwd.getpwuid(os.getuid()).pw_dir
    require(Path(os_home).is_absolute(), 'OS_HOME_REQUIRED')
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin', HOME=os_home, HERMES_HOME=str(home), CODEX_HOME=CODEX_HOME,
                      LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1', NO_COLOR='1', TERM='dumb')

class Native:
    def __init__(self, directory):
        clean_environment(); logging.disable(logging.CRITICAL)
        config = tomllib.loads((Path(CODEX_HOME) / 'config.toml').read_text())
        require(set(config) <= {'approval_policy', 'cli_auth_credentials_store', 'forced_login_method',
                               'sandbox_mode', 'web_search'}, 'UNREVIEWED_CODEX_CONFIGURATION')
        sys.path.insert(0, str(RELEASE))
        from agent.transports.codex_app_server import CodexAppServerClient
        from agent.transports.codex_app_server_session import CodexAppServerSession
        self.Client, self.Session, self.directory, self.session = CodexAppServerClient, CodexAppServerSession, directory, None

    def client(self, **kwargs):
        clean_environment()
        return self.Client(codex_bin=BINARY, codex_home=CODEX_HOME,
                           extra_args=[v for flag in FLAGS for v in ('-c', flag)])

    def check(self):
        client = self.client()
        try:
            client.initialize(client_name='msw-native-codex-v1', client_version='1')
            account = client.request('account/read', {'refreshToken': False}, timeout=15).get('account') or {}
            require(account.get('type') == 'chatgpt', 'CHATGPT_ACCOUNT_REQUIRED')
            models = client.request('model/list', {'limit': 100}, timeout=15).get('data', [])
            require(any(m.get('model') == MODEL or m.get('id') == MODEL for m in models), 'MODEL_UNAVAILABLE')
            limits = client.request('account/rateLimits/read', {}, timeout=15).get('rateLimits') or {}
            windows = [limits[k] for k in ('primary', 'secondary') if limits.get(k) is not None]
            require(windows and all(type(w.get('usedPercent')) in (int, float)
                    and math.isfinite(w['usedPercent']) and 0 <= w['usedPercent'] < 100 for w in windows), 'QUOTA_UNKNOWN_OR_EXHAUSTED')
            self.checked_at = time.time()
            return {'type': 'chatgpt', 'plan': limits.get('planType'), 'quota_checked_at': self.checked_at}
        finally: client.close()

    def start(self):
        self.session = self.Session(cwd=str(self.directory), codex_bin=BINARY, codex_home=CODEX_HOME,
            permission_profile='read-only-with-approval', approval_callback=lambda *a, **kw: 'deny',
            client_factory=self.client, model=MODEL,
            developer_instructions='Process this text-only owner task. Do not use tools or perform external actions.')
        return self.session.ensure_started()

    def run(self, prompt):
        require(time.time() - self.checked_at <= 90, 'QUOTA_SNAPSHOT_STALE')
        value = self.session.run_turn(prompt, turn_timeout=90)
        return {'thread_id': value.thread_id, 'turn_id': value.turn_id, 'text': value.final_text,
                'error': bool(value.error), 'interrupted': value.interrupted, 'tool_iterations': value.tool_iterations,
                'usage': {k: v for k, v in (value.token_usage_last or {}).items() if k in ('inputTokens', 'outputTokens', 'totalTokens')}}

    def close(self):
        if self.session is not None:
            session, self.session = self.session, None
            session.close()

    def read(self, thread_id):
        client = self.client()
        try:
            client.initialize(client_name='msw-native-codex-v1-readback', client_version='1')
            return client.request('thread/read', {'threadId': thread_id, 'includeTurns': True}, timeout=20)
        finally: client.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--run', metavar='PRIVATE_REQUEST_JSON'); choice.add_argument('--read', metavar='KEY')
    args = parser.parse_args()
    try:
        result = Entry().run(read_json(Path(args.run))) if args.run else Entry().read(args.read)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'status': 'REFUSED', 'code': str(exc) if isinstance(exc, Refused) else type(exc).__name__, 'automatic_retry': False}))
        return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

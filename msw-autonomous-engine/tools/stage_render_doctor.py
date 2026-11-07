"""Stage and start the Doctor on the current Render instance, without a build.

This is a separately supervised sidecar, not a modification of the live worker.
An instance replacement stops it; the operator must configure durable startup
separately. No secrets are printed or persisted by this installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

SERVICE = 'srv-dbltdb9srm7s73ec0v00'


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def verify_checkout(source: Path, expected: str) -> list[str]:
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('invalid_commit')
    if git(source, 'rev-parse', 'HEAD') != expected:
        raise ValueError('checkout_commit_mismatch')
    prefix = 'msw-autonomous-engine/msw_engine/'
    if git(source, 'status', '--porcelain', '--untracked-files=no', '--', prefix,
           'msw-autonomous-engine/tools/stage_render_doctor.py'):
        raise ValueError('modified_source')
    paths = git(source, 'ls-files', '--', prefix).splitlines()
    if not paths or prefix + 'doctor_sidecar.py' not in paths:
        raise ValueError('incomplete_source')
    for name in paths:
        relative = Path(name).relative_to(prefix)
        path = source / name
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or not path.is_file():
            raise ValueError('invalid_source_file')
    return paths


def stage(source: Path, data_dir: Path, expected: str) -> Path:
    paths = verify_checkout(source, expected)
    root = data_dir / 'msw-doctor' / 'releases'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError('unsafe_stage_directory')
    files = {}
    prefix = Path('msw-autonomous-engine/msw_engine')
    for name in paths:
        relative = Path(name).relative_to(prefix)
        content = (source / name).read_bytes()
        if relative.suffix == '.py':
            compile(content, str(relative), 'exec')
        files[str(relative)] = hashlib.sha256(content).hexdigest()
    manifest = {'commit': expected, 'sha256': files}
    target = root / expected
    if target.is_symlink():
        raise ValueError('unsafe_existing_release')
    if target.exists():
        recorded = json.loads((target / 'manifest.json').read_text())
        if recorded != manifest:
            raise ValueError('existing_release_differs')
        for name, digest in files.items():
            path = target / 'msw_engine' / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('existing_release_corrupt')
        return target
    temporary = Path(tempfile.mkdtemp(prefix='.stage-', dir=root))
    try:
        package = temporary / 'msw_engine'
        package.mkdir(mode=0o700)
        for name in paths:
            relative = Path(name).relative_to(prefix)
            destination = package / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source / name, destination)
            os.chmod(destination, 0o600)
        (temporary / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
        os.chmod(temporary / 'manifest.json', 0o600)
        # Imports run without any service credentials or inherited configuration.
        env = {'PATH': os.defpath, 'PYTHONPATH': str(temporary),
               'PYTHONSAFEPATH': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        code = ('import msw_engine.doctor_sidecar, msw_engine.doctor_compute_lane, msw_engine.doctor_receipts, msw_engine.render_doctor, '
                'msw_engine.failover_doctor, msw_engine.render_deploy_recovery, '
                'msw_engine.openai_direct')
        subprocess.run([sys.executable, '-c', code], cwd=temporary, env=env,
                       check=True, timeout=20, stdout=subprocess.DEVNULL)
        temporary.rename(target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def launch(release: Path, data_dir: Path, commit: str) -> dict:
    root = data_dir / 'msw-doctor-sidecar'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError('unsafe_sidecar_directory')
    env = dict(os.environ)
    env.update(PYTHONPATH=str(release), PYTHONSAFEPATH='1', PYTHONDONTWRITEBYTECODE='1',
               MSW_DOCTOR_RELEASE_SHA=commit, MSW_DATA_DIR=str(data_dir))
    logfile = root / 'sidecar.log'
    if logfile.is_symlink():
        raise ValueError('unsafe_log_path')
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen([sys.executable, '-m', 'msw_engine.doctor_sidecar'],
            cwd=data_dir, env=env, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
            start_new_session=True, close_fds=True)
    finally:
        os.close(fd)
    until = time.monotonic() + 15
    while time.monotonic() < until:
        try:
            result = json.loads((root / 'status.json').read_text())
            recent = abs(time.time() - result['at']) < 60
            if (recent and result['service_id'] == SERVICE and result['release'] == commit
                    and result['state'] == 'running'):
                os.kill(int(result['pid']), 0)
                return {'state': 'running_verified', 'pid': result['pid'], 'release': commit,
                        'shared_memory_readback_verified': result.get('shared_memory_readback_verified'),
                        'worker_queue_throttled_by_sidecar': False,
                        'restart_after_instance_replacement_verified': False}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if process.poll() not in (None, 0):
            break
        time.sleep(0.5)
    return {'state': 'start_unverified', 'release': commit, 'process_exit': process.poll(),
            'restart_after_instance_replacement_verified': False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--start', action='store_true')
    options = parser.parse_args()
    if os.environ.get('RENDER_SERVICE_ID') != SERVICE:
        raise SystemExit('wrong_render_service')
    data_dir = Path(os.environ.get('MSW_DATA_DIR', '/var/data')).resolve()
    if not data_dir.is_relative_to(Path('/var/data').resolve()):
        raise SystemExit('invalid_data_directory')
    source = Path(__file__).resolve().parents[2]
    try:
        release = stage(source, data_dir, options.expected_commit)
        result = launch(release, data_dir, options.expected_commit) if options.start else {'state': 'staged'}
        print(json.dumps(result, sort_keys=True))
        return 0 if result['state'] in {'staged', 'running_verified'} else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'failed', 'code': type(error).__name__, 'detail': str(error)[:120]}))
        return 1


if __name__ == '__main__':
    sys.exit(main())

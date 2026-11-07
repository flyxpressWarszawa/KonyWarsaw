"""Bounded integrity checks for the Doctor's already verified private pack.

If the cached pack changes, prevent new sidecar private-module claims. This
cannot stop jobs already running, protect the separate main worker, or act as
a WordPress firewall. No file names or private source are exported.
"""
from __future__ import annotations

import os
from pathlib import Path
import time

from .runtime_pack import MANIFEST, verify_directory


class PackIntegrity:
    def __init__(self, root, files, manifest_digest, *, env=None,
                 clock=time.time, monotonic=time.monotonic, interval=900):
        self.root = Path(root)
        self.files = dict(files)
        self.files[MANIFEST] = manifest_digest
        self.env = os.environ if env is None else env
        self.clock, self.monotonic = clock, monotonic
        self.interval, self.last_check = interval, None
        self._status = {'state': 'not_checked', 'private_runtime_disabled': False}

    @property
    def blocked(self):
        return self._status['state'] == 'integrity_blocked'

    def check(self):
        if self.blocked:
            return self.snapshot()
        now = self.monotonic()
        if self.last_check is not None and 0 <= now - self.last_check < self.interval:
            return self.snapshot()
        self.last_check = now
        try:
            verify_directory(self.root, self.files)
            self._status = {'state': 'verified', 'checked_at': int(self.clock()),
                            'private_runtime_disabled': False}
        except (OSError, ValueError):
            # Only this process is affected, and only the originally verified
            # root may be disabled. Never replace, delete or repair source files.
            removed = self.env.get('MSW_RUNTIME_PACK_ROOT') == str(self.root)
            if removed:
                self.env.pop('MSW_RUNTIME_PACK_ROOT', None)
            self._status = {'state': 'integrity_blocked', 'checked_at': int(self.clock()),
                            'private_runtime_disabled': removed}
        return self.snapshot()

    def snapshot(self):
        return dict(self._status)

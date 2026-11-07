# Auxiliary origin pressure guard

This one-shot WordPress installer adds a fail-closed gate to the existing signed
transport. Only a claim from msw-render-auxseo-20261107-2032 is held when the
account's fresh CPU or physical RAM is 70% or more, or account telemetry is
unavailable, stale or invalid. It returns HTTP 503 and Retry-After: 300, which
the currently deployed auxiliary worker already handles.

The guard runs after the existing HMAC authorization and does not intercept the
main worker, other nodes, or the auxiliary worker's complete, status, or
heartbeat requests. It does not terminate processes, change credentials,
move other job types or create an operating-system CPU cap.

The installer checks the precise expected existing transport and health-reader
SHA256 values, writes and verifies a private backup, replaces atomically and
verifies the new bytes. It should be activated once, checked, and deactivated.
The backup must be checked against the current live hash before any rollback.
Its pinned helper requirement intentionally stops claims if the telemetry
reader is changed without a corresponding reviewed update.

The tests create temporary WordPress and cPanel fixtures. Run them on PHP 8.1+
with php tests/test-transport-guard.php, php tests/test-installer.php and
python3 tests/test-transport-flow.py.

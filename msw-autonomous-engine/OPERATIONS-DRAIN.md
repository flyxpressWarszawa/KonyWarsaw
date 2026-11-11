# Existing worker maintenance

The start command still reaches `python -m msw_engine`. The private, versioned
Hermes launcher and source pins remain on the existing persistent Render disk.
No new service, grant, queue or WordPress configuration is required.

For planned maintenance, create `MSW_DATA_DIR/drain.request` atomically. The worker
stops admitting jobs, lets admitted work finish, joins every registered background
thread and the COMBO child, and writes `drain-status.json`. Proceed only when its
PID matches the current worker, state is `drained`, remaining_threads is empty,
remote leases have settled, and journal receipts have been reconciled. The old
worker stays alive awaiting SIGTERM, preventing an automatic restart during drain.

The marker also holds a new worker before initialization. Remove it after verifying
the deployed source, persistent data and launcher. This is a local maintenance
control, not a replacement for signed WordPress STOP or grants. It is not a global
pause of WordPress cron or other servers.

SIGTERM/SIGINT retain the existing cancellation semantics. Shutdown now allows up
to 20 seconds for auxiliary lanes and the COMBO child. If any remain, the worker
reports `incomplete`, lists their names and exits nonzero. Planned maintenance must
use the drain marker first because remote operations can exceed this grace period.

On the first upgrade from a worker without this marker, verify its source and all
active lanes separately, quiesce the existing transports, check remote and local
leases, and take fresh SQLite backups. Do not infer safety from a single empty queue
snapshot. Restore transport admission only after the new release is verified.

Rollback of the ACK repair requires reconciliation of `ready_to_complete`,
`completion_attempted` and `readback_deferred` rows before old code is resumed.
Never erase them or replay an uncertain completion. Preserve the private data disk.

-- MSW 2026-11-10: READ-ONLY PREFLIGHT. NEVER AUTO-RUN DDL.
-- Preconditions: backup and restore test verified, 2 fresh CPU/RAM samples below 60%,
-- ALTER grants, low metadata lock risk, disk capacity, owner-approved maintenance window.
SELECT VERSION() AS server_version, @@version_comment AS product;
SELECT ENGINE, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH
 FROM INFORMATION_SCHEMA.TABLES
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ovse_mswue_remote_jobs';
SHOW INDEX FROM ovse_mswue_remote_jobs;
EXPLAIN SELECT id,kind,payload,payload_sha256 FROM ovse_mswue_remote_jobs
 WHERE state='pending' AND kind IN ('seo.analyze') ORDER BY created_at,id LIMIT 1;
SHOW FULL PROCESSLIST;

-- REVIEW ONLY (not executable as delivered):
-- ALTER TABLE ovse_mswue_remote_jobs WAIT 3
--   ADD INDEX IF NOT EXISTS mswue_state_kind_created_id (state,kind,created_at,id),
--   ALGORITHM=NOCOPY, LOCK=NONE;

-- ROLLBACK REVIEW ONLY (also requires authorization):
-- ALTER TABLE ovse_mswue_remote_jobs WAIT 3
--   DROP INDEX IF EXISTS mswue_state_kind_created_id,
--   ALGORITHM=NOCOPY, LOCK=NONE;

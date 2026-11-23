-- ============================================================================
-- Agent Execution Contract v2 — Development Cutover (DESTRUCTIVE)
-- ============================================================================
--
-- Purpose
--   The v2 runtime does not read v1 tool-event payloads. This cutover deletes the
--   old event history and all facts that depend on it. It does NOT migrate data.
--
-- When to run
--   Development / test environment only. Run after all code for the v2 contracts
--   has been deployed to the target environment.
--
-- Execution order (follow exactly)
--   0. STOP the scheduler and worker claim loops. Do not run this while workers
--      can claim or finish executions.
--   1. Execute THIS ENTIRE FILE as one SQL script in a single session.
--      In DBeaver/Navicat/DataGrip use "Execute SQL Script", not "Execute statement".
--   2. The script:
--        a) cancels leftover QUEUED / WAITING_* executions (safe after workers stopped);
--        b) fails leftover PENDING / DELIVERING message intents so old payloads are
--           not retried and stale messages are not sent after the cutover;
--        c) refuses if any RUNNING execution remains;
--        d) deletes dependent inbox/outbox rows, then old events, checkpoints,
--           human interactions and tool operations;
--        e) commits in one transaction.
--   3. Run the verification queries at the bottom. Every count must be 0.
--   4. Start the scheduler/worker again.
--
-- If your PG tool does not support DO $$ ... $$; blocks
--   Run step 3 manually first:
--     SELECT count(*) FROM task.task_execution
--     WHERE status = 'RUNNING' AND is_deleted = false;
--   Only when the result is 0, run the remaining UPDATE/DELETE statements in one
--   transaction.
--
-- Rollback
--   There is no payload migration and no v1 read path. After this cutover, roll
--   forward only. To roll back, reset the development database and deploy the
--   previous image; the old event data is intentionally gone.
--
-- Schema assumptions
--   task.task_event, task.task_execution, task.task_execution_checkpoint,
--   task.task_human_interaction, task.runtime_result_outbox,
--   runtime.canonical_event, runtime.tool_operation, runtime.tool_result_inbox,
--   runtime.tool_control_outbox.
--   Adjust schema names here if the target database uses different ones.
-- ============================================================================

BEGIN;

-- Fail fast instead of waiting forever behind a lingering worker transaction.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '600s';

-- ----------------------------------------------------------------------------
-- STEP 1 — SETTLE LEFTOVER QUEUED / WAITING EXECUTIONS.
-- Only safe after scheduler/worker claim loops are stopped. RUNNING rows are
-- deliberately NOT touched; the preflight below refuses them.
-- ----------------------------------------------------------------------------
UPDATE task.task_execution
SET status = 'CANCELLED',
    wait_kind = NULL,
    wait_payload_json = NULL,
    cancel_requested = true,
    lease_owner = NULL,
    lease_until = NULL,
    last_checkpoint_id = NULL,
    finished_at = now(),
    update_time = now()
WHERE status IN ('QUEUED', 'WAITING_EXTERNAL', 'WAITING_HUMAN')
  AND is_deleted = false;

-- ----------------------------------------------------------------------------
-- STEP 2 — FAIL LEFTOVER MESSAGE DELIVERY INTENTS.
-- Their payloads were produced by v1 executions that are being deleted here.
-- Marking them FAILED stops the pump from retrying broken payloads and from
-- sending stale messages during/after the cutover. DELIVERED history is kept.
-- ----------------------------------------------------------------------------
UPDATE runtime.message_delivery
SET status = 'FAILED',
    lease_owner = NULL,
    lease_until = NULL,
    last_error_code = 'CUTOVER_STALE_DELIVERY',
    last_error_message = 'v1 delivery intent discarded by the v2 execution cutover',
    update_time = now()
WHERE status IN ('PENDING', 'DELIVERING')
  AND is_deleted = false;

-- ----------------------------------------------------------------------------
-- STEP 3 — PREFLIGHT: refuse while a RUNNING execution remains.
-- Settle/cancel these first; do not bypass this check.
-- ----------------------------------------------------------------------------
DO $$
DECLARE running_count integer;
BEGIN
    SELECT count(*) INTO running_count
    FROM task.task_execution
    WHERE status = 'RUNNING'
      AND is_deleted = false;

    IF running_count > 0 THEN
        RAISE EXCEPTION
            'cutover refused: % RUNNING task executions remain; settle/cancel them first',
            running_count;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- STEP 4 — DELETE OLD DATA (children before parents).
-- ----------------------------------------------------------------------------

-- 4.1 Inbox/outbox rows carry the old event and operation identities.
DELETE FROM runtime.tool_result_inbox;
DELETE FROM runtime.tool_control_outbox;
DELETE FROM task.runtime_result_outbox;

-- 4.2 v1 Runtime canonical events (user/assistant messages, tool events, summaries).
DELETE FROM runtime.canonical_event;

-- 4.3 Async tool operations reference the deleted events and inbox identities.
DELETE FROM runtime.tool_operation;

-- 4.4 v1 Worker task events (assistant turn, tool call, model call, ...).
DELETE FROM task.task_event;

-- 4.5 Drop the checkpoint references before deleting the checkpoint rows.
UPDATE task.task_execution
SET last_checkpoint_id = NULL
WHERE last_checkpoint_id IS NOT NULL;

UPDATE task.task_execution
SET source_operation_id = NULL,
    source_tool_call_id = NULL
WHERE source_operation_id IS NOT NULL OR source_tool_call_id IS NOT NULL;

-- 4.6 Checkpoints point at the deleted event history and must not be resumed.
DELETE FROM task.task_execution_checkpoint;

-- 4.7 Pending human questions reference old waits and are no longer resumable.
DELETE FROM task.task_human_interaction;

COMMIT;

-- ----------------------------------------------------------------------------
-- STEP 5 — VERIFICATION (run these separately after COMMIT).
-- Every count must be 0.
-- ----------------------------------------------------------------------------
-- SELECT count(*) AS pending_delivery_rows    FROM runtime.message_delivery
--   WHERE status IN ('PENDING','DELIVERING') AND is_deleted = false;
-- SELECT count(*) AS tool_result_inbox_rows   FROM runtime.tool_result_inbox;
-- SELECT count(*) AS tool_control_outbox_rows FROM runtime.tool_control_outbox;
-- SELECT count(*) AS runtime_result_outbox_rows FROM task.runtime_result_outbox;
-- SELECT count(*) AS canonical_event_rows     FROM runtime.canonical_event;
-- SELECT count(*) AS tool_operation_rows      FROM runtime.tool_operation;
-- SELECT count(*) AS task_event_rows          FROM task.task_event;
-- SELECT count(*) AS execution_checkpoint_rows FROM task.task_execution_checkpoint;
-- SELECT count(*) AS human_interaction_rows   FROM task.task_human_interaction;
-- SELECT count(*) AS live_execution_rows      FROM task.task_execution
--   WHERE status IN ('QUEUED','RUNNING','WAITING_EXTERNAL','WAITING_HUMAN')
--     AND is_deleted = false;

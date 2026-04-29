-- =============================================================================
-- Proactive Decision KPI Queries — S3.2 (JANE-45)
-- =============================================================================
-- These queries APPROXIMATE the Master Architecture Blueprint §10.2 KPIs using
-- the lightweight `events` (event_type='proactive_decision') + `user_overrides`
-- shape that ships in Phase 3. JANE-46 (S4.1) replaces them with versions
-- against the full `decisions` + `decision_outcomes` schema.
--
-- Correlation: dismissals are FK-linked via `user_overrides.proactive_decision_id`
-- where present (D5). Rows from before JANE-45 deployed fall back to a 30-minute
-- time window between `user_overrides.ts` and `events.timestamp`. The 30-minute
-- window was widened from 5min in v1 — legitimate notifications can sit in a
-- phone before the user dismisses, and a tighter window made KPI #4 systemically
-- low.
--
-- Each query is annotated with its target — these are aspirational and meant to
-- drive product judgement, not to fail CI.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- KPI #1 — Automation acceptance rate (Master Arch §10.2.1)
-- Target: > 85%
-- "Of all proactive decisions Jane made, how many were NOT overridden by the
--  user within 30 minutes?"
-- -----------------------------------------------------------------------------
WITH decisions AS (
    SELECT
        id,
        timestamp,
        metadata->>'trigger' AS trigger,
        metadata->>'routed_via' AS routed_via
    FROM events
    WHERE event_type = 'proactive_decision'
      AND timestamp > NOW() - INTERVAL '30 days'
      AND metadata->>'action_taken' NOT IN (
          'dropped_malformed_payload',
          'suppressed_by_mode'
      )
),
overrides AS (
    SELECT proactive_decision_id, ts, action_type
    FROM user_overrides
    WHERE ts > NOW() - INTERVAL '30 days'
)
SELECT
    COUNT(*) AS total_decisions,
    COUNT(*) FILTER (
        WHERE NOT EXISTS (
            SELECT 1 FROM overrides o
             WHERE (o.proactive_decision_id = decisions.id)
                OR (o.proactive_decision_id IS NULL
                    AND o.ts BETWEEN decisions.timestamp
                                  AND decisions.timestamp + INTERVAL '30 minutes')
        )
    ) AS accepted,
    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE NOT EXISTS (
                SELECT 1 FROM overrides o
                 WHERE (o.proactive_decision_id = decisions.id)
                    OR (o.proactive_decision_id IS NULL
                        AND o.ts BETWEEN decisions.timestamp
                                      AND decisions.timestamp + INTERVAL '30 minutes')
            )
        )::numeric / NULLIF(COUNT(*), 0),
        1
    ) AS acceptance_rate_pct
FROM decisions;


-- -----------------------------------------------------------------------------
-- KPI #2 — False positive alert rate (Master Arch §10.2.2)
-- Target: < 2 per week
-- "How many alerts did the user dismiss within 30 minutes?"
-- -----------------------------------------------------------------------------
SELECT
    DATE_TRUNC('week', uo.ts) AS week,
    COUNT(*) AS dismissals
FROM user_overrides uo
LEFT JOIN events e
       ON e.id = uo.proactive_decision_id
WHERE uo.override_type = 'dismissed'
  AND uo.ts > NOW() - INTERVAL '90 days'
  AND (
      e.id IS NOT NULL  -- direct FK linkage
      OR EXISTS (        -- legacy time-window fallback
          SELECT 1 FROM events e2
           WHERE e2.event_type = 'proactive_decision'
             AND uo.ts BETWEEN e2.timestamp
                            AND e2.timestamp + INTERVAL '30 minutes'
      )
  )
GROUP BY 1
ORDER BY 1 DESC;


-- -----------------------------------------------------------------------------
-- KPI #3 — Manual override rate (Master Arch §10.2.3)
-- Target: < 15%
-- "Of all Jane-initiated actions, how many did the user reverse / correct?"
-- (Dismissed is "didn't want this surfaced"; reversed is "wrong action".)
-- -----------------------------------------------------------------------------
SELECT
    COUNT(*) AS total_actions,
    COUNT(*) FILTER (WHERE uo.override_type IN ('reversed', 'corrected')) AS overrides,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE uo.override_type IN ('reversed', 'corrected'))::numeric
            / NULLIF(COUNT(*), 0),
        1
    ) AS override_rate_pct
FROM events e
LEFT JOIN user_overrides uo
       ON uo.proactive_decision_id = e.id
       OR (uo.proactive_decision_id IS NULL
           AND uo.ts BETWEEN e.timestamp AND e.timestamp + INTERVAL '30 minutes')
WHERE e.event_type = 'proactive_decision'
  AND e.timestamp > NOW() - INTERVAL '30 days'
  AND e.metadata->>'action_taken' NOT IN (
      'dropped_malformed_payload',
      'suppressed_by_mode'
  );


-- -----------------------------------------------------------------------------
-- KPI #4 — Acceptance per trigger × routed_via (Master Arch §10.2.4)
-- Target: > 60% per cell
-- Diagnostic — find the (trigger, route) combos the user is rejecting most.
-- Useful to tune which triggers should default to notification vs voice.
-- -----------------------------------------------------------------------------
SELECT
    e.metadata->>'trigger' AS trigger,
    e.metadata->>'routed_via' AS routed_via,
    COUNT(*) AS total,
    COUNT(*) FILTER (
        WHERE NOT EXISTS (
            SELECT 1 FROM user_overrides uo
             WHERE uo.proactive_decision_id = e.id
                OR (uo.proactive_decision_id IS NULL
                    AND uo.ts BETWEEN e.timestamp
                                   AND e.timestamp + INTERVAL '30 minutes')
        )
    ) AS accepted,
    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE NOT EXISTS (
                SELECT 1 FROM user_overrides uo
                 WHERE uo.proactive_decision_id = e.id
                    OR (uo.proactive_decision_id IS NULL
                        AND uo.ts BETWEEN e.timestamp
                                       AND e.timestamp + INTERVAL '30 minutes')
            )
        )::numeric / NULLIF(COUNT(*), 0),
        1
    ) AS acceptance_rate_pct
FROM events e
WHERE e.event_type = 'proactive_decision'
  AND e.timestamp > NOW() - INTERVAL '30 days'
  AND e.metadata->>'action_taken' NOT IN (
      'dropped_malformed_payload',
      'suppressed_by_mode'
  )
GROUP BY 1, 2
ORDER BY total DESC;


-- -----------------------------------------------------------------------------
-- KPI #5 — Critical urgency rate (forensic audit, S3.2-specific)
-- Target: near zero, investigate spikes
-- The LLM is taught urgency='critical' is for SAFETY ONLY (smoke detected,
-- water leak, unknown person at door). Marking a non-safety event critical
-- bypasses both mode TTS gating AND the trust budget. This query surfaces
-- spikes so the operator can audit whether Jane drifted on that rule.
-- -----------------------------------------------------------------------------
SELECT
    DATE_TRUNC('day', timestamp) AS day,
    COUNT(*) AS critical_count,
    ARRAY_AGG(DISTINCT metadata->>'trigger') AS triggers,
    ARRAY_AGG(id ORDER BY timestamp DESC) AS event_ids
FROM events
WHERE event_type = 'proactive_decision'
  AND metadata->>'urgency' = 'critical'
  AND timestamp > NOW() - INTERVAL '30 days'
GROUP BY 1
ORDER BY 1 DESC;

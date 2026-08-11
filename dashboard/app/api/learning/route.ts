import { NextResponse } from 'next/server'

import {
  databaseUnavailable,
  getDatabasePool,
} from '@/lib/server/database'


export const dynamic = 'force-dynamic'


export async function GET() {
  try {
    const result = await getDatabasePool().query(`
      WITH prediction_stats AS (
        SELECT
          COUNT(*)::INTEGER AS predictions,
          COALESCE(
            SUM(CARDINALITY(expected_horizons_minutes)),
            0
          )::INTEGER AS expected_outcomes,
          COUNT(DISTINCT policy_version)::INTEGER AS policy_versions,
          (
            ARRAY_AGG(
              policy_version
              ORDER BY observed_at DESC, id DESC
            )
          )[1] AS policy_version,
          MAX(observed_at) AS latest_prediction_at
        FROM candidate_predictions
        WHERE observed_at <= NOW()
      ),
      outcome_stats AS (
        SELECT
          COUNT(*)::INTEGER AS labelled_outcomes,
          COUNT(DISTINCT prediction_id)::INTEGER
            AS labelled_predictions,
          MAX(evaluated_at) AS latest_evaluated_at
        FROM candidate_prediction_outcomes
        WHERE evaluated_at <= NOW()
      ),
      latest_prediction_cycle AS (
        SELECT
          prediction.ai_decision_id,
          COUNT(*)::INTEGER AS candidates,
          COUNT(*) FILTER (
            WHERE prediction.model_action != 'ABSTAIN'
          )::INTEGER AS model_actions,
          COUNT(*) FILTER (
            WHERE prediction.model_action = 'ABSTAIN'
          )::INTEGER AS shadow_candidates,
          MAX(prediction.observed_at) AS observed_at
        FROM candidate_predictions prediction
        WHERE prediction.ai_decision_id = (
          SELECT candidate.ai_decision_id
          FROM candidate_predictions candidate
          WHERE candidate.observed_at <= NOW()
          ORDER BY candidate.observed_at DESC, candidate.id DESC
          LIMIT 1
        )
        GROUP BY prediction.ai_decision_id
      ),
      latest_learning_run AS (
        SELECT
          status,
          evaluated_at,
          outcomes_labelled,
          calibration_run_id,
          error_code
        FROM continuous_learning_runs
        WHERE evaluated_at <= NOW()
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
      ),
      latest_knowledge_graph_sync AS (
        SELECT
          status,
          synced_at,
          synced_counts,
          total_nodes,
          total_relationships,
          error_code
        FROM knowledge_graph_sync_runs
        WHERE synced_at <= NOW()
        ORDER BY synced_at DESC, id DESC
        LIMIT 1
      ),
      latest_knowledge_shadow AS (
        SELECT
          status,
          reason_code,
          evaluated_at,
          evidence_fact_count,
          candidate_count,
          comparison_count,
          changed_count,
          model_backend,
          model_name,
          model_provider,
          reasoning_effort
        FROM knowledge_shadow_runs
        WHERE evaluated_at <= NOW()
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
      ),
      knowledge_shadow_stats AS (
        SELECT
          COUNT(*)::INTEGER AS total_runs,
          COUNT(*) FILTER (
            WHERE status = 'SUCCEEDED'
          )::INTEGER AS successful_runs,
          COALESCE(
            SUM(comparison_count),
            0
          )::INTEGER AS total_comparison_count,
          COALESCE(
            SUM(changed_count),
            0
          )::INTEGER AS total_changed_count
        FROM knowledge_shadow_runs
        WHERE evaluated_at <= NOW()
      ),
      latest_calibration AS (
        SELECT
          id,
          policy_version,
          evaluated_at,
          session_cutoff_date,
          status,
          reason_code,
          train_sessions,
          validation_sessions,
          train_outcomes,
          validation_outcomes,
          coverage_pct,
          active_min_signal_score,
          challenger_min_signal_score,
          baseline_validation_mean_bps,
          challenger_validation_mean_bps,
          validation_improvement_bps
        FROM candidate_policy_calibration_runs
        WHERE evaluated_at <= NOW()
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
      ),
      policy_state AS (
        SELECT
          (
            SELECT JSON_BUILD_OBJECT(
              'version', version,
              'status', status,
              'config', config,
              'created_at', created_at
            )
            FROM candidate_policy_versions
            WHERE status = 'ACTIVE'
          ) AS active,
          (
            SELECT JSON_BUILD_OBJECT(
              'version', version,
              'status', status,
              'config', config,
              'calibration_run_id', calibration_run_id,
              'created_at', created_at
            )
            FROM candidate_policy_versions
            WHERE status IN ('DRAFT', 'APPROVED')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
          ) AS challenger
      ),
      matured_stats AS (
        SELECT COUNT(*)::INTEGER AS matured_outcomes
        FROM candidate_predictions prediction
        CROSS JOIN LATERAL UNNEST(
          prediction.expected_horizons_minutes
        ) AS horizon
        WHERE prediction.observed_at <= NOW()
          AND DATE_TRUNC('minute', prediction.observed_at)
          + horizon * INTERVAL '1 minute' <= NOW()
      ),
      action_metrics AS (
        SELECT
          prediction.model_action AS action,
          outcome.horizon_minutes,
          COUNT(outcome.id)::INTEGER AS outcomes,
          ROUND(AVG(outcome.return_bps), 4) AS mean_return_bps,
          ROUND(
            100.0 * AVG(
              CASE
                WHEN outcome.return_bps > 0 THEN 1.0
                ELSE 0.0
              END
            ),
            2
          ) AS positive_rate_pct
        FROM candidate_predictions prediction
        JOIN candidate_prediction_outcomes outcome
          ON outcome.prediction_id = prediction.id
        WHERE prediction.observed_at <= NOW()
          AND outcome.evaluated_at <= NOW()
        GROUP BY
          prediction.model_action,
          outcome.horizon_minutes
        ORDER BY
          prediction.model_action,
          outcome.horizon_minutes
      )
      SELECT
        prediction_stats.policy_version,
        prediction_stats.policy_versions,
        prediction_stats.predictions,
        prediction_stats.expected_outcomes,
        matured_stats.matured_outcomes,
        outcome_stats.labelled_predictions,
        outcome_stats.labelled_outcomes,
        GREATEST(
          prediction_stats.expected_outcomes
            - outcome_stats.labelled_outcomes,
          0
        )::INTEGER AS pending_outcomes,
        GREATEST(
          prediction_stats.expected_outcomes
            - matured_stats.matured_outcomes,
          0
        )::INTEGER AS scheduled_outcomes,
        GREATEST(
          matured_stats.matured_outcomes
            - outcome_stats.labelled_outcomes,
          0
        )::INTEGER AS overdue_outcomes,
        CASE
          WHEN matured_stats.matured_outcomes = 0 THEN 0
          ELSE ROUND(
            100.0 * outcome_stats.labelled_outcomes
              / matured_stats.matured_outcomes,
            2
          )
        END AS coverage_pct,
        prediction_stats.latest_prediction_at,
        outcome_stats.latest_evaluated_at,
        COALESCE(
          (SELECT candidates FROM latest_prediction_cycle),
          0
        )::INTEGER AS latest_cycle_candidates,
        COALESCE(
          (SELECT model_actions FROM latest_prediction_cycle),
          0
        )::INTEGER AS latest_cycle_model_actions,
        COALESCE(
          (SELECT shadow_candidates FROM latest_prediction_cycle),
          0
        )::INTEGER AS latest_cycle_shadow_candidates,
        (SELECT observed_at FROM latest_prediction_cycle)
          AS latest_cycle_observed_at,
        (
          SELECT JSON_BUILD_OBJECT(
            'status', status,
            'evaluated_at', evaluated_at,
            'outcomes_labelled', outcomes_labelled,
            'calibration_run_id', calibration_run_id,
            'error_code', error_code,
            'age_seconds', GREATEST(
              EXTRACT(EPOCH FROM NOW() - evaluated_at),
              0
            )::INTEGER
          )
          FROM latest_learning_run
        ) AS learning_run,
        (
          SELECT JSON_BUILD_OBJECT(
            'status', status,
            'synced_at', synced_at,
            'synced_counts', synced_counts,
            'total_nodes', total_nodes,
            'total_relationships', total_relationships,
            'error_code', error_code,
            'age_seconds', GREATEST(
              EXTRACT(EPOCH FROM NOW() - synced_at),
              0
            )::INTEGER
          )
          FROM latest_knowledge_graph_sync
        ) AS knowledge_graph_sync,
        (
          SELECT JSON_BUILD_OBJECT(
            'status', status,
            'reason_code', reason_code,
            'evaluated_at', evaluated_at,
            'evidence_fact_count', evidence_fact_count,
            'candidate_count', candidate_count,
            'comparison_count', comparison_count,
            'changed_count', changed_count,
            'model_backend', model_backend,
            'model_name', model_name,
            'model_provider', model_provider,
            'reasoning_effort', reasoning_effort,
            'total_runs',
              (SELECT total_runs FROM knowledge_shadow_stats),
            'successful_runs',
              (SELECT successful_runs FROM knowledge_shadow_stats),
            'total_comparison_count',
              (
                SELECT total_comparison_count
                FROM knowledge_shadow_stats
              ),
            'total_changed_count',
              (SELECT total_changed_count FROM knowledge_shadow_stats),
            'age_seconds', GREATEST(
              EXTRACT(EPOCH FROM NOW() - evaluated_at),
              0
            )::INTEGER
          )
          FROM latest_knowledge_shadow
        ) AS knowledge_shadow,
        (
          SELECT JSON_BUILD_OBJECT(
            'id', id,
            'policy_version', policy_version,
            'evaluated_at', evaluated_at,
            'session_cutoff_date', session_cutoff_date,
            'status', status,
            'reason_code', reason_code,
            'train_sessions', train_sessions,
            'validation_sessions', validation_sessions,
            'train_outcomes', train_outcomes,
            'validation_outcomes', validation_outcomes,
            'coverage_pct', coverage_pct,
            'active_min_signal_score', active_min_signal_score,
            'challenger_min_signal_score', challenger_min_signal_score,
            'baseline_validation_mean_bps',
              baseline_validation_mean_bps,
            'challenger_validation_mean_bps',
              challenger_validation_mean_bps,
            'validation_improvement_bps',
              validation_improvement_bps
          )
          FROM latest_calibration
        ) AS calibration,
        (SELECT active FROM policy_state) AS active_candidate_policy,
        (SELECT challenger FROM policy_state)
          AS challenger_candidate_policy,
        CASE
          WHEN (
            SELECT status FROM latest_learning_run
          ) IS DISTINCT FROM 'SUCCEEDED' THEN 'WORKER_FAILED'
          WHEN outcome_stats.labelled_outcomes < 100 THEN 'COLLECTING'
          WHEN outcome_stats.labelled_outcomes
            < matured_stats.matured_outcomes THEN 'INCOMPLETE'
          ELSE 'MEASURING'
        END AS evidence_status,
        COALESCE(
          (
            SELECT JSON_AGG(
              JSON_BUILD_OBJECT(
                'action', action,
                'horizon_minutes', horizon_minutes,
                'outcomes', outcomes,
                'mean_return_bps', mean_return_bps,
                'positive_rate_pct', positive_rate_pct
              )
              ORDER BY action, horizon_minutes
            )
            FROM action_metrics
          ),
          '[]'::JSON
        ) AS action_metrics
      FROM prediction_stats
      CROSS JOIN outcome_stats
      CROSS JOIN matured_stats
    `)

    return NextResponse.json(result.rows[0], {
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch (error) {
    return databaseUnavailable('fetch continuous learning status', error)
  }
}

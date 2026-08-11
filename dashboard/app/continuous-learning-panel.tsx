export interface ContinuousLearningStatus {
  policy_version: string | null
  policy_versions: number
  predictions: number
  expected_outcomes: number
  matured_outcomes: number
  labelled_predictions: number
  labelled_outcomes: number
  pending_outcomes: number
  scheduled_outcomes: number
  overdue_outcomes: number
  coverage_pct: number
  latest_prediction_at: string | null
  latest_evaluated_at: string | null
  latest_cycle_candidates: number
  latest_cycle_model_actions: number
  latest_cycle_shadow_candidates: number
  latest_cycle_observed_at: string | null
  evidence_status:
    | 'COLLECTING'
    | 'INCOMPLETE'
    | 'MEASURING'
    | 'WORKER_FAILED'
  learning_run: null | {
    status: 'SUCCEEDED' | 'FAILED'
    evaluated_at: string
    outcomes_labelled: number
    calibration_run_id: number | null
    error_code: string | null
    age_seconds: number
  }
  knowledge_graph_sync: null | {
    status: 'SUCCEEDED' | 'FAILED'
    synced_at: string
    synced_counts: Record<string, number>
    total_nodes: number
    total_relationships: number
    error_code: string | null
    age_seconds: number
  }
  knowledge_shadow: null | {
    status: 'SUCCEEDED' | 'SKIPPED' | 'FAILED'
    reason_code: string
    evaluated_at: string
    evidence_fact_count: number
    candidate_count: number
    comparison_count: number
    changed_count: number
    model_backend: string
    model_name: string
    model_provider: string | null
    reasoning_effort: string | null
    total_runs: number
    successful_runs: number
    total_comparison_count: number
    total_changed_count: number
    age_seconds: number
  }
  calibration: null | {
    id: number
    policy_version: string
    evaluated_at: string
    session_cutoff_date: string
    status: 'COLLECTING' | 'REJECTED' | 'CHALLENGER'
    reason_code: string
    train_sessions: number
    validation_sessions: number
    train_outcomes: number
    validation_outcomes: number
    coverage_pct: number
    active_min_signal_score: number
    challenger_min_signal_score: number | null
    baseline_validation_mean_bps: number | null
    challenger_validation_mean_bps: number | null
    validation_improvement_bps: number | null
  }
  active_candidate_policy: null | {
    version: string
    status: 'ACTIVE'
    config: {
      min_signal_score: number
      max_spread_bps: number
    }
    created_at: string
  }
  challenger_candidate_policy: null | {
    version: string
    status: 'DRAFT' | 'APPROVED'
    config: {
      min_signal_score: number
      max_spread_bps: number
    }
    calibration_run_id: number
    created_at: string
  }
  action_metrics: Array<{
    action: 'BUY' | 'SELL' | 'HOLD' | 'ABSTAIN'
    horizon_minutes: 30 | 60 | 120
    outcomes: number
    mean_return_bps: number
    positive_rate_pct: number
  }>
}


const ACTION_LABELS: Record<string, string> = {
  BUY: 'Köpt',
  SELL: 'Sålt',
  HOLD: 'Behållit',
  ABSTAIN: 'Avstått',
}

const KNOWLEDGE_SHADOW_MESSAGES: Record<string, string> = {
  MODEL_CONFIG_MISMATCH: 'Väntar på en analys med nuvarande modell.',
  NO_CANDIDATES: 'Senaste analysen saknade jämförbara aktier.',
  NO_ELIGIBLE_EVIDENCE: 'Samlar ännu tillräckligt med verifierade utfall.',
  STRATEGY_UNAVAILABLE: 'Strategiversionen kunde inte återskapas.',
  GRAPH_UNAVAILABLE: 'Grafminnet kunde inte läsas.',
  INVALID_MODEL_RESPONSE: 'Modellsvaren kunde inte jämföras säkert.',
  MODEL_UNAVAILABLE: 'Analysmodellen var inte tillgänglig.',
}


function number(value: number, digits = 0) {
  return new Intl.NumberFormat('sv-SE', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value))
}


function dateTime(value: string | null) {
  if (!value) return 'Inget utfall ännu'
  return new Date(value).toLocaleString('sv-SE', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}


export function ContinuousLearningPanel({
  status,
}: {
  status: ContinuousLearningStatus
}) {
  const collecting = status.evidence_status === 'COLLECTING'
  const incomplete = status.evidence_status === 'INCOMPLETE'
  const workerFailed = status.evidence_status === 'WORKER_FAILED'
  const statusLabel = workerFailed
    ? 'Lärloopen har stannat'
    : collecting
      ? 'Samlar underlag'
      : incomplete
        ? `${number(status.coverage_pct, 1)} % av mogna utfall mätta`
        : 'Mätning aktiv'
  const statusClass = workerFailed || incomplete
    ? 'border-amber-900/60 bg-amber-950/20 text-amber-200'
    : 'border-blue-900/60 bg-blue-950/20 text-blue-100'
  const shadow = status.knowledge_shadow
  const latestShadowMessage = shadow?.status === 'SUCCEEDED'
    ? `${number(shadow.comparison_count)} beslut jämfördes i senaste körningen.`
    : shadow
      ? KNOWLEDGE_SHADOW_MESSAGES[shadow.reason_code]
        ?? 'Senaste skuggjämförelsen kunde inte slutföras.'
      : 'Väntar på första jämförelsen.'
  const shadowSummary = shadow && shadow.successful_runs > 0
    ? `${number(shadow.successful_runs)} ${
        shadow.successful_runs === 1
          ? 'genomförd grafjämförelse'
          : 'genomförda grafjämförelser'
      } · `
      + `${number(shadow.total_comparison_count)} beslut jämförda · `
      + `${number(shadow.total_changed_count)} ${
        shadow.total_changed_count === 1
          ? 'ändrad handling'
          : 'ändrade handlingar'
      }. `
      + `Senaste körningen: ${latestShadowMessage}`
    : latestShadowMessage

  return (
    <section
      aria-labelledby="continuous-learning-heading"
      className="rounded-2xl border border-gray-800/60 bg-gray-900/40 p-5 sm:p-6"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Kontinuerlig förbättring
          </div>
          <h2
            id="continuous-learning-heading"
            className="mt-1 text-xl font-semibold"
          >
            Vad agentens beslut faktiskt leder till
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-400">
            Varje kandidat sparas även när agenten avstår. Paper-entryn låses
            till beslutstiden när den fördröjda boken blir verifierbar, och
            därefter mäts kursutfallet efter 30, 60 och 120 minuter.
          </p>
        </div>
        <span
          className={`w-fit rounded-full border px-3 py-1 text-xs font-semibold ${statusClass}`}
        >
          {statusLabel}
        </span>
      </div>

      <dl className="mt-5 grid grid-cols-1 border-y border-gray-800/70 sm:grid-cols-3 sm:divide-x sm:divide-gray-800/70">
        <div className="py-4 sm:pr-5">
          <dt className="text-xs uppercase tracking-wider text-gray-500">
            Analyserade kandidater
          </dt>
          <dd className="mt-1 text-3xl font-semibold">
            {number(status.predictions)}
          </dd>
        </div>
        <div className="border-t border-gray-800/70 py-4 sm:border-t-0 sm:px-5">
          <dt className="text-xs uppercase tracking-wider text-gray-500">
            Mätta utfall
          </dt>
          <dd className="mt-1 text-3xl font-semibold">
            {number(status.labelled_outcomes)}
            <span className="ml-2 text-sm font-normal text-gray-500">
              av {number(status.matured_outcomes)} mogna
            </span>
          </dd>
        </div>
        <div className="border-t border-gray-800/70 py-4 sm:border-t-0 sm:pl-5">
          <dt className="text-xs uppercase tracking-wider text-gray-500">
            Mogna utfall täckta
          </dt>
          <dd className="mt-1 text-3xl font-semibold">
            {number(status.coverage_pct, 1)} %
          </dd>
        </div>
      </dl>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Senaste skuggbevakning
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">
            {number(status.latest_cycle_candidates)} kandidater
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            {number(status.latest_cycle_shadow_candidates)} sparade som
            skuggdata · {number(status.latest_cycle_model_actions)} fick en
            uttrycklig modellhandling.
          </p>
        </div>
        <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Lärprocess
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">
            {status.learning_run?.status === 'SUCCEEDED' ? 'Kör' : 'Fel'}
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            Senast {dateTime(status.learning_run?.evaluated_at || null)}
            {status.learning_run?.error_code
              ? ` · ${status.learning_run.error_code}`
              : ''}
          </p>
        </div>
        <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Aktiv kandidatpolicy
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">
            {status.active_candidate_policy?.version || 'Saknas'}
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            Minsta signalpoäng{' '}
            {number(
              status.active_candidate_policy?.config.min_signal_score || 0,
            )}
          </p>
        </div>
        <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Tradingminne
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">
            Kunskapsgraf:{' '}
            {status.knowledge_graph_sync?.status === 'SUCCEEDED'
              ? 'synkad'
              : status.knowledge_graph_sync
                ? 'fel'
                : 'väntar'}
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            {status.knowledge_graph_sync
              ? `${number(status.knowledge_graph_sync.total_nodes)} noder · `
                + `${number(status.knowledge_graph_sync.total_relationships)} samband · `
                + `senast ${dateTime(status.knowledge_graph_sync.synced_at)}`
              : 'Första synkningen har inte körts ännu.'}
          </p>
          <div className="mt-3 border-t border-gray-800/70 pt-3">
            <div className="text-xs font-medium text-gray-300">
              Grafminne i skuggläge
            </div>
            <p className="mt-1 text-xs leading-5 text-gray-500">
              {shadowSummary}
              {' '}Två isolerade analyser jämförs; detta påverkar inte affärer.
            </p>
          </div>
        </div>
      </div>

      {status.calibration && (
        <div className="mt-5 rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-sm font-medium text-gray-200">
                Framåttestad challenger
              </h3>
              <p className="mt-1 text-sm leading-6 text-gray-500">
                {number(status.calibration.train_sessions)} träningssessioner
                {' · '}
                {number(status.calibration.validation_sessions)}
                {' '}senare valideringssessioner ·{' '}
                {number(status.calibration.validation_outcomes)} validerade
                utfall.
              </p>
            </div>
            <span className="w-fit rounded-full border border-gray-700 px-3 py-1 text-xs text-gray-300">
              {status.calibration.status}
            </span>
          </div>
          {status.challenger_candidate_policy ? (
            <p className="mt-3 text-sm leading-6 text-blue-200">
              {status.challenger_candidate_policy.version} väntar på
              operatörsgranskning. Den aktiva policyn ändras inte automatiskt.
            </p>
          ) : (
            <p className="mt-3 text-sm leading-6 text-gray-500">
              Ingen challenger har klarat forward-gaten ännu (
              {status.calibration.reason_code}).
            </p>
          )}
        </div>
      )}

      {status.action_metrics.length === 0 ? (
        <div className="mt-5 rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
          <p className="text-sm font-medium text-gray-200">
            Inga mätbara utfall ännu
          </p>
          <p className="mt-1 text-sm leading-6 text-gray-500">
            Inga slutsatser dras förrän senare, verifierade orderböcker har
            hunnit märka kandidaterna.
          </p>
        </div>
      ) : (
        <div className="mt-5">
          <h3 className="text-sm font-medium text-gray-300">
            Marknadsutfall per handling
          </h3>
          <ul className="mt-3 divide-y divide-gray-800/70 border-y border-gray-800/70">
            {status.action_metrics.map(metric => (
              <li
                key={`${metric.action}-${metric.horizon_minutes}`}
                className="grid grid-cols-2 gap-3 py-3 text-sm sm:grid-cols-4"
              >
                <div className="font-medium text-gray-200">
                  {ACTION_LABELS[metric.action] || metric.action}
                  <span className="ml-1 text-gray-500">
                    {metric.horizon_minutes} min
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Utfall </span>
                  {number(metric.outcomes)}
                </div>
                <div>
                  <span className="text-gray-500">Kursutfall </span>
                  {metric.mean_return_bps >= 0 ? '+' : ''}
                  {number(metric.mean_return_bps, 1)} bp
                </div>
                <div>
                  <span className="text-gray-500">Kursen steg </span>
                  {number(metric.positive_rate_pct, 1)} %
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex flex-col gap-1 text-xs text-gray-500 sm:flex-row sm:justify-between">
        <span>
          Policy {status.policy_version || 'väntar på första prognosen'}
        </span>
        <span>
          {number(status.scheduled_outcomes)} schemalagda ·{' '}
          {number(status.overdue_outcomes)} försenade · senast mätt{' '}
          {dateTime(status.latest_evaluated_at)}
        </span>
      </div>

      {collecting && (
        <p className="mt-4 text-xs leading-5 text-gray-500">
          Samlar underlag: minst 100 mätta utfall krävs innan sidan visar en
          jämförande slutsats. Inga slutsatser dras från den nuvarande lilla
          datamängden.
        </p>
      )}
      {workerFailed && (
        <p className="mt-4 text-xs leading-5 text-amber-200">
          Lärprocessen har inte en färsk godkänd körning. Felet visas ovan och
          agenten får inte kalla underlaget kontinuerligt uppdaterat.
        </p>
      )}
    </section>
  )
}

import type { ContinuousLearningStatus } from './continuous-learning-panel'

type ControlRoomProps = {
  portfolio: {
    total_value: number
    pnl_pct: number
  }
  operations: {
    checked_at: string | null
    overall_status: 'READY' | 'BLOCKED'
    blockers: string[]
    market: { phase: string }
    data: {
      freshness_status: string
      latest_event_time: string | null
      display_delay_seconds: number
    }
    activity: {
      decisions_today: number
      trades_today: number
      latest_decision_at: string | null
      latest_trade_at: string | null
    }
    pipelines: {
      learning: { status: string | null; evaluated_at: string | null }
      knowledge: { status: string | null; synced_at: string | null }
    }
    funnel: {
      candidates_read: number
      candidates_ranked: number
      candidates_eligible: number
      candidates_exploration: number
      model_actions: number
      order_attempts: number
      fills: number
      stopping_reasons: Record<string, number>
    }
  }
  learning: ContinuousLearningStatus
  blockerLabels: Record<string, string>
}

const number = new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 0 })
const percentage = new Intl.NumberFormat('sv-SE', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: 'exceptZero',
})

function dateTime(value: string | null) {
  if (!value) return 'Saknas'
  return new Date(value).toLocaleString('sv-SE', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="border-t border-gray-800 py-3 first:border-t-0 lg:border-t-0 lg:border-l lg:px-5 lg:first:border-l-0 lg:first:pl-0">
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-gray-100">{value}</dd>
      <div className="mt-1 text-xs text-gray-500">{note}</div>
    </div>
  )
}

export function ControlRoom({ portfolio, operations, learning, blockerLabels }: ControlRoomProps) {
  const target = 26_000
  const start = 20_000
  const progress = Math.max(0, Math.min(100, ((portfolio.total_value - start) / (target - start)) * 100))
  const onlyClosed = operations.blockers.length === 1 && operations.blockers[0] === 'XSTO_SESSION_NOT_OPEN'
  const healthy = operations.overall_status === 'READY' || onlyClosed
  const pulse = healthy ? (onlyClosed ? 'Väntar på börsen' : 'I drift') : 'Behöver åtgärd'
  const topReasons = Object.entries(operations.funnel.stopping_reasons)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 2)

  return (
    <section aria-labelledby="control-room-heading" className="overflow-hidden rounded-xl border border-gray-800 bg-gray-900/60">
      <div className="grid lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
        <div className="p-5 sm:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 id="control-room-heading" className="text-2xl font-semibold tracking-tight sm:text-3xl">
                Kontrollrum
              </h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-400">
                Målet är 26 000 kr inom 6–12 månader. Allt här bygger på bokförd paperdata; saknad eller gammal data visas som ett fel.
              </p>
            </div>
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${healthy ? 'border-emerald-800 bg-emerald-950/40 text-emerald-200' : 'border-red-800 bg-red-950/40 text-red-200'}`}>
              {pulse}
            </span>
          </div>

          <div className="mt-7 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-sm text-gray-400">Senaste säkra värdering</div>
              <div className="mt-1 text-4xl font-semibold tracking-tight sm:text-5xl">
                {number.format(portfolio.total_value)} <span className="text-xl text-gray-500">kr</span>
              </div>
              <div className="mt-2 text-sm text-gray-400">
                {percentage.format(portfolio.pnl_pct)} % sedan start
              </div>
            </div>
            <div className="text-right text-sm text-gray-400">
              <div>{number.format(Math.max(0, target - portfolio.total_value))} kr kvar till målet</div>
              <div className="mt-1">{percentage.format(progress)} % av vägen</div>
            </div>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-gray-800" aria-label={`${percentage.format(progress)} procent av vägen till målet`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <div className="h-full rounded-full bg-emerald-500" style={{ width: `${progress}%` }} />
          </div>

          <dl className="mt-7 grid border-y border-gray-800 lg:grid-cols-4">
            <Metric label="Beslut idag" value={number.format(operations.activity.decisions_today)} note={`Senast ${dateTime(operations.activity.latest_decision_at)}`} />
            <Metric label="Affärer idag" value={number.format(operations.activity.trades_today)} note={`Senast ${dateTime(operations.activity.latest_trade_at)}`} />
            <Metric label="Datans färskhet" value={operations.data.freshness_status === 'FRESH' ? 'Färsk' : operations.data.freshness_status} note={`Marknadstid ${dateTime(operations.data.latest_event_time)}`} />
            <Metric label="Mätta utfall" value={number.format(learning.labelled_outcomes)} note={`${percentage.format(learning.coverage_pct)} % täckning`} />
          </dl>
        </div>

        <aside className="border-t border-gray-800 bg-gray-950/40 p-5 sm:p-7 lg:border-l lg:border-t-0" aria-label="Agentens puls och blockerare">
          <h3 className="text-sm font-semibold">Agentens puls</h3>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex justify-between gap-4"><dt className="text-gray-500">Marknad</dt><dd>{operations.market.phase}</dd></div>
            <div className="flex justify-between gap-4"><dt className="text-gray-500">Lärloop</dt><dd>{operations.pipelines.learning.status || 'Saknas'}</dd></div>
            <div className="flex justify-between gap-4"><dt className="text-gray-500">Tradinggraf</dt><dd>{operations.pipelines.knowledge.status || 'Saknas'}</dd></div>
            <div className="flex justify-between gap-4"><dt className="text-gray-500">Kontrollerad</dt><dd>{dateTime(operations.checked_at)}</dd></div>
          </dl>

          <h3 className="mt-7 text-sm font-semibold">Senaste beslutstratten</h3>
          <div className="mt-3 grid grid-cols-5 gap-1 text-center text-xs">
            {[
              ['Lästa', operations.funnel.candidates_read],
              ['Valbara', operations.funnel.candidates_eligible],
              ['Modell', operations.funnel.model_actions],
              ['Order', operations.funnel.order_attempts],
              ['Fill', operations.funnel.fills],
            ].map(([label, value]) => (
              <div key={String(label)} className="border-l border-gray-800 first:border-l-0">
                <div className="font-semibold text-gray-200">{number.format(Number(value))}</div>
                <div className="mt-1 text-gray-600">{label}</div>
              </div>
            ))}
          </div>

          <div className="mt-7 border-t border-gray-800 pt-5">
            <h3 className="text-sm font-semibold">Det som stoppar aktivitet</h3>
            {operations.blockers.length === 0 && topReasons.length === 0 ? (
              <p className="mt-2 text-sm text-gray-500">Inga öppna blockerare eller avvisningar.</p>
            ) : (
              <ul className="mt-3 space-y-2 text-sm text-gray-300">
                {operations.blockers.slice(0, 3).map(code => <li key={code}>{blockerLabels[code] || code}</li>)}
                {topReasons.map(([reason, count]) => <li key={reason}>{reason}: {count}</li>)}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </section>
  )
}

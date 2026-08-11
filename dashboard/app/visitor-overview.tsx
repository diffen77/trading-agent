type VisitorPortfolio = {
  total_value: number
  pnl: number
  pnl_pct: number
}

export type VisitorTrade = {
  ticker: string
  company_name: string | null
  action: string
  total_value: number
  reasoning: string
  confidence: number | null
  executed_at: string
}

type VisitorBenchmark = {
  benchmark_return_pct: number | null
  excess_return_pct: number | null
}

type VisitorOverviewProps = {
  portfolio: VisitorPortfolio
  dailyReturnPct: number | null
  latestTrade: VisitorTrade | null
  benchmark: VisitorBenchmark | null
}

function integer(value: number) {
  return new Intl.NumberFormat('sv-SE', {
    maximumFractionDigits: 0,
  }).format(value)
}

function percentage(value: number) {
  return new Intl.NumberFormat('sv-SE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: 'exceptZero',
  }).format(value)
}

function actionLabel(action: string) {
  if (action === 'BUY') return 'Köpte'
  if (action === 'SELL') return 'Sålde'
  return action
}

function dateTime(value: string) {
  return new Date(value).toLocaleString('sv-SE', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

export function VisitorOverview({
  portfolio,
  dailyReturnPct,
  latestTrade,
  benchmark,
}: VisitorOverviewProps) {
  const benchmarkReady = (
    benchmark?.benchmark_return_pct != null
    && benchmark?.excess_return_pct != null
  )
  const latestCompany = latestTrade
    ? latestTrade.company_name?.trim() || latestTrade.ticker
    : null

  return (
    <section
      aria-labelledby="visitor-overview-heading"
      className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(20rem,0.65fr)]"
    >
      <div className="rounded-2xl border border-gray-800/70 bg-gray-900/60 p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2
              id="visitor-overview-heading"
              className="text-xs font-normal uppercase tracking-wider text-gray-500"
            >
              Portföljen just nu
            </h2>
            <div
              className="mt-1 text-3xl font-bold tracking-tight sm:text-4xl"
            >
              {integer(portfolio.total_value)}{' '}
              <span className="text-lg text-gray-500 sm:text-xl">kr</span>
            </div>
          </div>
          <span className="rounded-full border border-blue-900/70 bg-blue-950/30 px-3 py-1 text-xs font-semibold text-blue-200">
            Papertrading
          </span>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
            <div className="text-xs text-gray-500">Idag</div>
            <div className="mt-1 text-xl font-semibold">
              {dailyReturnPct == null
                ? 'Inte beräknat'
                : `${percentage(dailyReturnPct)}%`}
            </div>
          </div>
          <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
            <div className="text-xs text-gray-500">Sedan start</div>
            <div className="mt-1 text-xl font-semibold">
              {percentage(portfolio.pnl_pct)}%
            </div>
            <div className="mt-1 text-xs text-gray-500">
              {percentage(portfolio.pnl)} kr
            </div>
          </div>
          <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
            <div className="text-xs text-gray-500">Mot OMXSGI</div>
            {benchmarkReady ? (
              <>
                <div className="mt-1 text-xl font-semibold">
                  {percentage(benchmark.excess_return_pct as number)}%
                </div>
                <div className="mt-1 text-xs text-gray-500">
                  Agentens överavkastning
                </div>
              </>
            ) : (
              <>
                <div className="mt-1 text-sm font-semibold text-amber-200">
                  Inväntar verifierad data
                </div>
                <div className="mt-1 text-xs text-gray-500">
                  Ingen jämförelse fabriceras.
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <article className="rounded-2xl border border-gray-800/70 bg-gray-900/60 p-5 sm:p-6">
        <div className="text-xs uppercase tracking-wider text-gray-500">
          Senaste handlingen
        </div>
        {latestTrade && latestCompany ? (
          <>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className={`rounded px-2 py-1 text-xs font-bold ${latestTrade.action === 'BUY' ? 'bg-green-500/15 text-green-300' : 'bg-red-500/15 text-red-300'}`}>
                {actionLabel(latestTrade.action)}
              </span>
              <h2 className="text-xl font-bold">{latestCompany}</h2>
            </div>
            {latestCompany !== latestTrade.ticker && (
              <div className="mt-1 text-xs text-gray-500">
                ISIN eller ticker: {latestTrade.ticker}
              </div>
            )}
            <div className="mt-4 text-2xl font-semibold">
              {integer(Number(latestTrade.total_value))} kr
            </div>
            <p className="mt-3 line-clamp-3 text-sm leading-6 text-gray-300">
              {latestTrade.reasoning}
            </p>
            <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-gray-800/70 pt-3 text-xs text-gray-500">
              <span>{dateTime(latestTrade.executed_at)}</span>
              {latestTrade.confidence != null && (
                <span>{integer(Number(latestTrade.confidence))}% säkerhet</span>
              )}
            </div>
          </>
        ) : (
          <div className="mt-4 rounded-xl border border-dashed border-gray-800 p-6 text-sm text-gray-500">
            Ingen genomförd handling ännu.
          </div>
        )}
      </article>
    </section>
  )
}

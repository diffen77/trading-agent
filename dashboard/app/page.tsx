'use client'

import { useState, useEffect, useCallback } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import {
  UniversePanel,
  type UniverseStatus,
} from './universe-panel'
import {
  ActivationQueue,
  type ActivationAction,
} from './activation-queue'
import {
  ContinuousLearningPanel,
  type ContinuousLearningStatus,
} from './continuous-learning-panel'
import { VisitorOverview } from './visitor-overview'
import {
  decisionActionPresentation,
  decisionCompanyLabel,
} from '@/lib/decision-presentation.mjs'

// ── Types ──────────────────────────────────────────────────
interface Portfolio { cash: number; positions_value: number; total_value: number; pnl: number; pnl_pct: number }
interface Position { ticker: string; shares: number; avg_price: number; current_price: number; company_name: string; sector: string; rsi: number; confidence: number; reasoning: string; pnl_pct: number; pnl_kr: number; market_value: number; days_held: number; target_price: number; stop_loss: number; price_source: string; price_event_time: string; price_received_at: string }
interface HistoryPoint { total_value: number; recorded_at: string }
interface Macro { symbol: string; type: string; value: number; change_pct: number }
interface Decision {
  id: number
  decisions_json: string
  timestamp: string
  instrument_names: Record<string, string>
  prediction_count: number
  executed_trades: Array<{
    ticker: string
    action: string
  }>
  model_backend: string
  model_name: string
  model_provider: string | null
  reasoning_effort: string | null
  response_model: string | null
}
interface Trade { id: number; ticker: string; company_name: string | null; action: string; shares: number; price: number; total_value: number; reasoning: string; confidence: number | null; hypothesis: string | null; executed_at: string; pnl: number | null; closed_at: string | null; outcome: string | null; strategy_version: string; decision_id: number | null; decision_origin: string; source_quote_id: number | null; source_book_state_id: number | null; source_evidence_type: 'PRE_TRADE_BOOK' | 'LAST_TRADE_QUOTE' | null; decision_timestamp: string | null; quote_event_time: string | null; quote_received_at: string | null; source_file_checksum: string | null }
interface Performance { total_trades: number; buys: number; sells: number; winners: number; losers: number; win_rate: number; avg_win: number; avg_loss: number; total_pnl: number; best_trade: number; worst_trade: number; avg_confidence: number; open_positions: number }
interface OperationsStatus {
  checked_at: string | null
  overall_status: 'READY' | 'BLOCKED'
  blockers: string[]
  activation_actions: ActivationAction[]
  schema_version: number
  strategy: null | {
    version: string
    config: {
      max_positions: number
      max_position_pct: number
      max_sector_positions: number
      stop_loss_pct: number
      take_profit_pct: number
      min_confidence: number
    }
  }
  universe: {
    expected_instruments: number
    active_instruments: number
    provider_aliases: number
    company_mappings: number
    mapping_coverage_pct: number
  }
  market: {
    phase: string
    timezone_name: string
    opens_at: string | null
    closes_at: string | null
  }
  data: {
    provider: null | {
      provider: string
      product_name: string
      data_type: string
      authorization_basis: string
      delivery_mode: string
      nominal_delay_seconds: number
      contract_status: string
      validation_status: string | null
    }
    market_data_mode: 'PUBLIC_DELAYED_PRETRADE' | 'LICENSED_LAST_TRADE'
    display_delay_seconds: number
    fresh_instruments: number
    fresh_quote_coverage_pct: number
    freshness_status: string
    latest_event_time: string | null
    last_sync_status: string | null
    last_sync_finished_at: string | null
    open_gaps: number
  }
  index: null | {
    symbol: string
    provider: string
    product_name?: string
    delivery_mode?: string
    contract_ready: boolean
    validation_ready: boolean
    signal_fresh: boolean
    current_level: number | null
    previous_close: number | null
    change_pct: number | null
    event_time: string | null
    received_at: string | null
  }
  risk: {
    status: string
    open_positions: number
    max_positions: number
    largest_sector_positions: number
    max_sector_positions: number
    unpriced_positions: number
    gross_exposure: number
    cash: number
    total_value: number
    entry_status: string
    max_daily_loss_pct: number
    daily_loss_breached: boolean
    daily_return_pct: number | null
  }
  benchmark: null | {
    experiment_key: string
    status: string
    benchmark_symbol: string
    min_trading_sessions: number
    min_closed_trades: number
    trading_sessions: number
    closed_trades: number
    critical_incidents: number
    net_return_pct: number | null
    benchmark_return_pct: number | null
    excess_return_pct: number | null
    max_drawdown_pct: number | null
    data_coverage_pct: number | null
    passed: boolean | null
    model_backend?: string
    model_name?: string
  }
  monitoring: {
    active_alerts: Array<{
      alert_key: string
      code: string
      severity: 'PAGE' | 'TICKET'
      state: 'OPEN'
      summary: string
      runbook: string
      observed_at: string | null
    }>
    scheduled_routines: Array<{
      routine_key: string
      routine_name: 'morning' | 'open' | 'midday' | 'close' | 'evening'
      scheduled_at: string
      status: 'SUCCEEDED' | 'FAILED'
      failure_code: string | null
      observed_at: string
    }>
  }
}

const BLOCKER_LABELS: Record<string, string> = {
  SCHEMA_VERSION_MISMATCH: 'Databasschemat har fel version',
  ACTIVE_STRATEGY_MISSING: 'Aktiv och verifierad strategi saknas',
  REFERENCE_SNAPSHOT_MISSING: 'Referenssnapshot för XSTO saknas',
  REFERENCE_ENTITLEMENT_NOT_READY: 'Licens- och lagringsbevis för referensdata saknas',
  UNIVERSE_INCOMPLETE: 'XSTO-universet är inte komplett',
  TICKER_MAPPING_INCOMPLETE: 'Tickeralias eller bolagsmappning saknas',
  QUOTE_PROVIDER_NOT_READY: 'Licens- och transportbevis för prisfeed saknas',
  XSTO_SESSION_NOT_OPEN: 'XSTO-sessionen är inte öppen',
  FRESH_QUOTE_COVERAGE_INCOMPLETE: 'Färska priser saknas för delar av universet',
  MARKET_SYNC_NOT_READY: 'Senaste marknadssynken är inte godkänd',
  MARKET_DATA_GAPS_OPEN: 'Marknadsdata innehåller öppna gap',
  INDEX_PROVIDER_NOT_READY: 'Licens- och transportbevis för OMXSGI saknas',
  INDEX_SIGNAL_NOT_READY: 'Aktuell verifierad OMXSGI-signal saknas',
  POSITION_LIMIT_BREACH: 'Antalet positioner överskrider strategin',
  SECTOR_LIMIT_BREACH: 'Sektorkoncentrationen överskrider strategin',
  UNPRICED_POSITION: 'En eller flera positioner saknar leverantörspris',
  TRADING_HALTED: 'Operatörens nödstopp blockerar nya köp',
  DAILY_LOSS_LIMIT_BREACHED: 'Dagens förlustgräns blockerar nya köp',
  FORWARD_BENCHMARK_NOT_REGISTERED: 'Forward-benchmark är inte registrerat',
  FORWARD_BENCHMARK_NOT_RUNNING: 'Forward-benchmark körs inte',
  CRITICAL_BENCHMARK_INCIDENT: 'Benchmark har en kritisk incident',
  SCHEMA_OR_MIGRATION_NOT_READY: 'Schema eller migrering är inte driftklar',
  LEDGER_INVARIANT_FAILED: 'Ledgerkontrollen har misslyckats',
  MARKET_DATA_NOT_READY: 'Marknadsdata är inte driftklar',
  SCHEDULED_ROUTINE_MISSED: 'En schemalagd körning saknar lyckat utfall',
  MONITOR_EVIDENCE_UNAVAILABLE: 'Driftmonitorn kan inte läsa verifieringsdata',
}

const ROUTINE_LABELS: Record<string, string> = {
  morning: 'Morgon',
  open: 'Öppning',
  midday: 'Mitt på dagen',
  close: 'Stängning',
  evening: 'Kväll',
}

const MACRO_NAMES: Record<string, string> = {
  OMXSGI: 'OMXSGI',
}
const PERIODS = ['1D', '1W', '1M', 'YTD', '1Y'] as const
type Period = typeof PERIODS[number]

function fmt(n: number): string { return new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 0 }).format(n) }
function fmtDec(n: number, d = 2): string { return new Intl.NumberFormat('sv-SE', { minimumFractionDigits: d, maximumFractionDigits: d }).format(n) }
function pnlColor(n: number): string { return n > 0.01 ? 'text-green-400' : n < -0.01 ? 'text-red-400' : 'text-gray-400' }
function pnlSign(n: number): string { return n > 0.01 ? '+' : '' }
function fmtTime(value: string | null): string {
  if (!value) return '–'
  return new Date(value).toLocaleString('sv-SE', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const [macro, setMacro] = useState<Macro[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [perf, setPerf] = useState<Performance | null>(null)
  const [operations, setOperations] = useState<OperationsStatus | null>(null)
  const [universe, setUniverse] = useState<UniverseStatus | null>(null)
  const [learning, setLearning] = useState<ContinuousLearningStatus | null>(null)
  const [period, setPeriod] = useState<Period>('1M')
  const [lastUpdate, setLastUpdate] = useState(new Date())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const responses = await Promise.all([
        fetch('/api/portfolio'), fetch('/api/positions'), fetch(`/api/history?period=${period}`),
        fetch('/api/macro'), fetch('/api/decisions'), fetch('/api/performance'),
        fetch('/api/operations'), fetch('/api/trades'),
        fetch('/api/universe?page=1&pageSize=12'),
        fetch('/api/learning'),
      ])
      if (responses.some(response => !response.ok)) {
        throw new Error('One or more dashboard APIs are unavailable')
      }
      const [
        portfolioData,
        positionsData,
        historyData,
        macroData,
        decisionsData,
        performanceData,
        operationsData,
        tradesData,
        universeData,
        learningData,
      ] = await Promise.all(responses.map(response => response.json()))
      setPortfolio(portfolioData)
      setPositions(positionsData)
      setHistory(historyData)
      setMacro(macroData)
      setDecisions(decisionsData)
      setPerf(performanceData)
      setOperations(operationsData)
      setTrades(tradesData)
      setUniverse(universeData)
      setLearning(learningData)
      setError(null)
      setLastUpdate(new Date())
    } catch {
      setError('Dashboarddata kunde inte verifieras.')
    }
    finally { setLoading(false) }
  }, [period])

  useEffect(() => { fetchAll(); const iv = setInterval(fetchAll, 30000); return () => clearInterval(iv) }, [fetchAll])

  if (loading) return <div className="min-h-screen flex items-center justify-center bg-[#0a0a0f] text-white text-xl">Laddar...</div>
  if (error || !portfolio || !perf || !operations || !universe || !learning) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-[#0a0a0f] text-white p-6">
        <div className="max-w-md rounded-2xl border border-red-900/60 bg-red-950/20 p-6 text-center">
          <h1 className="text-xl font-semibold">Data är inte tillgänglig</h1>
          <p className="mt-2 text-sm text-gray-400">
            {error || 'Dashboarddata saknas.'} Ingen portfölj- eller agentstatus visas.
          </p>
          <button
            className="mt-5 rounded-lg bg-white px-4 py-2 text-sm font-medium text-black"
            onClick={() => { setLoading(true); void fetchAll() }}
          >
            Försök igen
          </button>
        </div>
      </main>
    )
  }

  const p = portfolio
  const pf = perf
  const ops = operations
  const operationalReady = operations.overall_status === 'READY'
  const waitingForMarket = (
    !operationalReady
    && ops.blockers.length === 1
    && ops.blockers[0] === 'XSTO_SESSION_NOT_OPEN'
  )
  const publicDelayed = (
    ops.data.market_data_mode === 'PUBLIC_DELAYED_PRETRADE'
  )
  const chartData = history.map(h => ({ date: new Date(h.recorded_at).toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' }), value: parseFloat(String(h.total_value)) }))
  const isUp = p.pnl_pct >= 0
  const color = isUp ? '#22c55e' : '#ef4444'

  return (
    <main className="min-h-screen bg-[#0a0a0f] text-white">
      {/* ── Macro Bar ── */}
      <header className="border-b border-gray-800/50 px-4 py-2 overflow-x-auto">
        <div className="max-w-[1400px] mx-auto flex items-center gap-6">
          <h1 className="text-lg font-bold whitespace-nowrap">📈 Trading Agent</h1>
          <div className="flex gap-5 text-xs">
            {macro.filter(m => MACRO_NAMES[m.symbol]).map(m => {
              const ch = parseFloat(String(m.change_pct))
              return (
                <div key={m.symbol} className="flex items-center gap-1 whitespace-nowrap">
                  <span className="text-gray-500">{MACRO_NAMES[m.symbol]}</span>
                  <span className="font-medium">{fmtDec(parseFloat(String(m.value)), m.type === 'currency' ? 2 : 0)}</span>
                  <span className={ch >= 0 ? 'text-green-400' : 'text-red-400'}>{ch >= 0 ? '↑' : '↓'}{Math.abs(ch).toFixed(1)}%</span>
                </div>
              )
            })}
          </div>
        </div>
      </header>

      <div className="max-w-[1400px] mx-auto p-6 space-y-6">
        {publicDelayed && (
          <div className="rounded-xl border border-blue-900/60 bg-blue-950/20 px-4 py-3 text-sm text-blue-100">
            Officiell Nasdaq Nordic-data · cirka {Math.round(ops.data.display_delay_seconds / 60)} minuters fördröjning · endast intern papertrading · inga riktiga order
          </div>
        )}

        <VisitorOverview
          portfolio={p}
          dailyReturnPct={ops.risk.daily_return_pct}
          latestTrade={trades[0] ?? null}
          benchmark={ops.benchmark}
        />

        <ContinuousLearningPanel status={learning} />

        <details className="group rounded-2xl border border-gray-800/60 bg-gray-900/30">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 sm:px-6">
            <div>
              <div className="font-semibold">Teknik och transparens</div>
              <div className="mt-0.5 text-xs text-gray-500">
                Datakällor, riskregler, driftstatus och Stockholmsbörsens universum
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${operationalReady ? 'bg-green-500/15 text-green-300' : waitingForMarket ? 'bg-amber-500/15 text-amber-300' : 'bg-red-500/15 text-red-300'}`}>
                {operationalReady ? 'REDO' : waitingForMarket ? 'VÄNTAR' : 'BLOCKERAD'}
              </span>
              <span aria-hidden="true" className="text-gray-500 transition group-open:rotate-180">⌄</span>
            </div>
          </summary>
          <div className="space-y-4 border-t border-gray-800/60 p-4 sm:p-6">
        {/* ── Operational readiness ── */}
        <section className={`rounded-2xl border p-6 ${operationalReady ? 'border-green-900/60 bg-green-950/10' : waitingForMarket ? 'border-amber-900/60 bg-amber-950/10' : 'border-red-900/60 bg-red-950/10'}`}>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="text-xs uppercase tracking-wider text-gray-500">Handelsberedskap</div>
              <h2 className="mt-1 text-2xl font-bold">
                {operationalReady
                  ? 'Redo för verifierad papertrading'
                  : waitingForMarket
                    ? 'Redo – väntar på nästa XSTO-session'
                    : 'Handel blockerad'}
              </h2>
              <p className="mt-1 text-sm text-gray-400">
                Schema {ops.schema_version} · XSTO {ops.market.phase} · {ops.market.timezone_name}
              </p>
            </div>
            <span className={`w-fit rounded-full px-3 py-1 text-xs font-semibold ${operationalReady ? 'bg-green-500/15 text-green-300' : waitingForMarket ? 'bg-amber-500/15 text-amber-300' : 'bg-red-500/15 text-red-300'}`}>
              {operationalReady ? 'REDO' : waitingForMarket ? 'VÄNTAR' : ops.overall_status}
            </span>
          </div>

          <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
              <div className="text-xs uppercase tracking-wider text-gray-500">Datakälla</div>
              <div className="mt-2 font-semibold">
                {ops.data.provider?.product_name || 'Ingen validerad feed'}
              </div>
              <div className="mt-2 space-y-1 text-xs text-gray-400">
                <div>Provider: {ops.data.provider?.provider || '–'}</div>
                <div>Leverans: {ops.data.provider?.delivery_mode || '–'}</div>
                <div>Avtal: {ops.data.provider?.contract_status || 'saknas'}</div>
                <div>Validering: {ops.data.provider?.validation_status || 'saknas'}</div>
                <div>
                  {publicDelayed
                    ? `Exekverbara orderböcker: ${ops.data.fresh_instruments}`
                    : `Färska priser: ${ops.data.fresh_instruments}/${ops.universe.expected_instruments} (${fmtDec(ops.data.fresh_quote_coverage_pct, 1)}%)`}
                </div>
                <div>Senaste {publicDelayed ? 'orderbok' : 'quote'}: {fmtTime(ops.data.latest_event_time)}</div>
                <div>Synk: {ops.data.last_sync_status || 'saknas'} · gap {ops.data.open_gaps}</div>
                {publicDelayed ? (
                  <div>Benchmark: väljs separat och blockerar inte starten</div>
                ) : (
                  <>
                    <div>Indexfeed: {ops.index?.provider || 'saknas'}</div>
                    <div>OMXSGI: {ops.index?.current_level == null ? '–' : fmtDec(Number(ops.index.current_level), 2)}</div>
                    <div>Indexsignal: {ops.index?.signal_fresh ? 'färsk' : 'saknas'}</div>
                  </>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
              <div className="text-xs uppercase tracking-wider text-gray-500">XSTO-universum</div>
              <div className="mt-2 text-2xl font-bold">{ops.universe.active_instruments}/{ops.universe.expected_instruments}</div>
              <div className="mt-2 space-y-1 text-xs text-gray-400">
                <div>Provideralias: {ops.universe.provider_aliases}</div>
                <div>Bolagsmappningar: {ops.universe.company_mappings}</div>
                <div>Mapping coverage: {fmtDec(ops.universe.mapping_coverage_pct, 1)}%</div>
                <div>Session: {ops.market.phase}</div>
                <div>Öppnar: {fmtTime(ops.market.opens_at)}</div>
                <div>Stänger: {fmtTime(ops.market.closes_at)}</div>
              </div>
            </div>

            <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
              <div className="text-xs uppercase tracking-wider text-gray-500">Risk</div>
              <div className={`mt-2 text-lg font-bold ${ops.risk.status === 'WITHIN_LIMITS' ? 'text-green-400' : 'text-red-400'}`}>
                {ops.risk.status === 'WITHIN_LIMITS' ? 'Inom gränser' : 'Gräns överträdd'}
              </div>
              <div className="mt-2 space-y-1 text-xs text-gray-400">
                <div>Positioner: {ops.risk.open_positions}/{ops.risk.max_positions}</div>
                <div>Största sektor: {ops.risk.largest_sector_positions}/{ops.risk.max_sector_positions}</div>
                <div>Oprissatta positioner: {ops.risk.unpriced_positions}</div>
                <div>Riskregler för nya köp: {ops.risk.entry_status === 'ACTIVE' && !ops.risk.daily_loss_breached ? 'öppna' : 'stoppade'}</div>
                <div>Daglig gräns: −{fmtDec(ops.risk.max_daily_loss_pct, 2)}%</div>
                <div>Dagens avkastning: {ops.risk.daily_return_pct == null ? 'ej utvärderad' : `${pnlSign(ops.risk.daily_return_pct)}${fmtDec(ops.risk.daily_return_pct, 2)}%`}</div>
                <div>Bruttoexponering: {fmt(ops.risk.gross_exposure)} kr</div>
                <div>Kontanter: {fmt(ops.risk.cash)} kr</div>
              </div>
            </div>

            <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
              <div className="text-xs uppercase tracking-wider text-gray-500">Forward-benchmark</div>
              <div className="mt-2 font-semibold">
                {ops.benchmark?.experiment_key || 'Ej registrerat'}
              </div>
              <div className="mt-2 space-y-1 text-xs text-gray-400">
                <div>Status: {ops.benchmark?.status || 'saknas'}</div>
                <div>Sessioner: {ops.benchmark?.trading_sessions ?? 0}/{ops.benchmark?.min_trading_sessions ?? 252}</div>
                <div>Stängda affärer: {ops.benchmark?.closed_trades ?? 0}/{ops.benchmark?.min_closed_trades ?? 30}</div>
                <div>Överavkastning: {ops.benchmark?.excess_return_pct == null ? '–' : `${fmtDec(ops.benchmark.excess_return_pct)}%`}</div>
                <div>Max drawdown: {ops.benchmark?.max_drawdown_pct == null ? '–' : `${fmtDec(ops.benchmark.max_drawdown_pct)}%`}</div>
                <div>Datatäckning: {ops.benchmark?.data_coverage_pct == null ? '–' : `${fmtDec(ops.benchmark.data_coverage_pct)}%`}</div>
                <div>Kritiska incidenter: {ops.benchmark?.critical_incidents ?? 0}</div>
              </div>
            </div>
          </div>

          {ops.blockers.length > 0 && (
            <div className="mt-4 border-t border-red-900/40 pt-4">
              <div className="text-xs uppercase tracking-wider text-red-300">Aktiva blockerare</div>
              <ul className="mt-2 grid grid-cols-1 gap-2 text-sm text-red-200 md:grid-cols-2">
                {ops.blockers.map(code => (
                  <li key={code} className="rounded-lg bg-red-950/30 px-3 py-2">
                    {BLOCKER_LABELS[code] || code}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 grid grid-cols-1 gap-4 border-t border-gray-800/60 pt-4 lg:grid-cols-2">
            <section aria-labelledby="active-alerts-heading" className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
              <div className="flex items-center justify-between gap-3">
                <h3 id="active-alerts-heading" className="text-xs uppercase tracking-wider text-gray-400">
                  Aktiva driftlarm
                </h3>
                <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-gray-300">
                  {ops.monitoring.active_alerts.length}
                </span>
              </div>
              {ops.monitoring.active_alerts.length === 0 ? (
                <p className="mt-3 text-sm text-gray-500">Inga aktiva driftlarm.</p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {ops.monitoring.active_alerts.map(alert => (
                    <li key={alert.alert_key} className={`rounded-lg border px-3 py-2 ${alert.severity === 'PAGE' ? 'border-red-900/60 bg-red-950/30' : 'border-amber-900/60 bg-amber-950/20'}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className={`text-xs font-semibold ${alert.severity === 'PAGE' ? 'text-red-300' : 'text-amber-300'}`}>
                          {alert.severity}
                        </span>
                        <time className="text-xs text-gray-500" dateTime={alert.observed_at || undefined}>
                          {fmtTime(alert.observed_at)}
                        </time>
                      </div>
                      <p className="mt-1 text-sm text-gray-200">{alert.summary}</p>
                      <p className="mt-1 text-xs text-gray-500">{BLOCKER_LABELS[alert.code] || alert.code}</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby="scheduled-routines-heading" className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
              <div className="flex items-center justify-between gap-3">
                <h3 id="scheduled-routines-heading" className="text-xs uppercase tracking-wider text-gray-400">
                  Senaste schemakörningar
                </h3>
                <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-gray-300">
                  {ops.monitoring.scheduled_routines.length}
                </span>
              </div>
              {ops.monitoring.scheduled_routines.length === 0 ? (
                <p className="mt-3 text-sm text-gray-500">Inga schemakörningar registrerade.</p>
              ) : (
                <ul className="mt-3 divide-y divide-gray-800/70">
                  {ops.monitoring.scheduled_routines.map(routine => (
                    <li key={routine.routine_key} className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <div className="text-sm text-gray-200">
                          {ROUTINE_LABELS[routine.routine_name] || routine.routine_name}
                        </div>
                        <time className="text-xs text-gray-500" dateTime={routine.scheduled_at}>
                          Planerad {fmtTime(routine.scheduled_at)}
                        </time>
                      </div>
                      <div className="sm:text-right">
                        <div className={`text-xs font-semibold ${routine.status === 'SUCCEEDED' ? 'text-green-400' : 'text-red-400'}`}>
                          {routine.status === 'SUCCEEDED' ? 'LYCKAD' : 'MISSLYCKAD'}
                        </div>
                        <div className="text-xs text-gray-500">
                          {routine.failure_code
                            ? BLOCKER_LABELS[routine.failure_code] || routine.failure_code
                            : fmtTime(routine.observed_at)}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </section>

        <ActivationQueue
          actions={operations.activation_actions}
          checkedAt={operations.checked_at}
        />

        <UniversePanel universe={universe} />
          </div>
        </details>

        {/* ── Row 1: Chart + Performance ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Portfolio Chart */}
          <div className="lg:col-span-2 bg-gray-900/50 rounded-2xl p-6 border border-gray-800/50">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <div className="text-gray-500 text-sm">Portföljvärde</div>
                <div className="text-4xl font-bold">{fmt(p.total_value)} <span className="text-xl text-gray-600">SEK</span></div>
                <div className={`text-sm mt-1 ${pnlColor(p.pnl_pct)}`}>
                  {pnlSign(p.pnl_pct)}{fmtDec(p.pnl_pct)}% ({pnlSign(p.pnl)}{fmt(p.pnl)} kr)
                </div>
              </div>
              <div className="flex w-full gap-1 rounded-lg bg-gray-800/50 p-1 sm:w-auto">
                {PERIODS.map(pr => (
                  <button key={pr} onClick={() => setPeriod(pr)}
                    className={`flex-1 rounded px-2 py-1.5 text-xs font-medium transition sm:flex-none sm:px-3 ${period === pr ? 'bg-white text-black' : 'text-gray-500 hover:text-white'}`}>
                    {pr}
                  </button>
                ))}
              </div>
            </div>
            <div className="h-48">
              {chartData.length > 1 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={color} stopOpacity={0.3}/><stop offset="95%" stopColor={color} stopOpacity={0}/></linearGradient></defs>
                    <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: '#4b5563', fontSize: 11 }}/>
                    <YAxis domain={['dataMin - 500', 'dataMax + 500']} axisLine={false} tickLine={false} tick={{ fill: '#4b5563', fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`}/>
                    <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }} formatter={(v: number) => [`${fmt(v)} SEK`, 'Värde']}/>
                    <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill="url(#grad)"/>
                  </AreaChart>
                </ResponsiveContainer>
              ) : <div className="h-full flex items-center justify-center text-gray-600">📊 Grafdata byggs upp...</div>}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-4 border-t border-gray-800/50 pt-4 text-sm sm:grid-cols-3">
              <div><span className="text-gray-500">Kontanter</span><div className="text-lg font-semibold">{fmt(p.cash)}</div></div>
              <div><span className="text-gray-500">Investerat</span><div className="text-lg font-semibold">{fmt(p.positions_value)}</div></div>
              <div><span className="text-gray-500">Avkastning</span><div className={`text-lg font-semibold ${pnlColor(p.pnl)}`}>{pnlSign(p.pnl)}{fmt(p.pnl)} kr</div></div>
            </div>
          </div>

          {/* Performance Stats */}
          <div className="bg-gray-900/50 rounded-2xl p-6 border border-gray-800/50">
            <h2 className="text-sm text-gray-500 mb-4 uppercase tracking-wider">Performance</h2>
            <div className="space-y-4">
              <StatRow label="Öppna positioner" value={`${pf.open_positions}`} />
              <StatRow label="Totala trades" value={`${pf.total_trades}`} />
              <StatRow label="Win rate" value={pf.win_rate > 0 ? `${fmtDec(pf.win_rate, 0)}%` : '–'} color={pf.win_rate >= 50 ? 'text-green-400' : pf.win_rate > 0 ? 'text-red-400' : ''} />
              <StatRow label="Vinnare / Förlorare" value={`${pf.winners}W / ${pf.losers}L`} />
              <StatRow label="Avg confidence" value={pf.avg_confidence > 0 ? `${fmtDec(pf.avg_confidence, 0)}%` : '–'} />
              <StatRow label="Total P&L" value={`${pnlSign(pf.total_pnl)}${fmt(pf.total_pnl)} kr`} color={pnlColor(pf.total_pnl)} />
              <div className="pt-3 border-t border-gray-800/50">
                <StatRow label="Mål" value="Papertrading mot benchmark" />
              </div>
            </div>
          </div>
        </div>

        {/* ── Row 2: Positions ── */}
        <div>
          <h2 className="text-lg font-semibold mb-3">Positioner ({positions.length})</h2>
          {positions.length === 0 ? (
            <div className="bg-gray-900/50 rounded-2xl p-8 text-center text-gray-600 border border-gray-800/50">Inga öppna positioner</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {positions.map(pos => <PositionCard key={pos.ticker} pos={pos} />)}
            </div>
          )}
        </div>

        {/* ── Row 3: Decisions + Info ── */}
        <div>
          <h2 className="text-lg font-semibold mb-3">Affärsjournal ({trades.length})</h2>
          {trades.length === 0 ? (
            <div className="bg-gray-900/50 rounded-2xl p-8 text-center text-gray-600 border border-gray-800/50">Inga bokförda affärer</div>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {trades.slice(0, 20).map(trade => {
                const tradeName = trade.company_name?.trim() || trade.ticker
                return (
                  <article key={trade.id} className="rounded-2xl border border-gray-800/50 bg-gray-900/50 p-5">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${trade.action === 'BUY' ? 'bg-green-500/15 text-green-300' : 'bg-red-500/15 text-red-300'}`}>
                            {trade.action === 'BUY' ? 'Köpt' : trade.action === 'SELL' ? 'Sålt' : trade.action}
                          </span>
                          <h3 className="font-semibold">{tradeName}</h3>
                        </div>
                        {tradeName !== trade.ticker && (
                          <div className="mt-1 text-xs text-gray-500">
                            ISIN eller ticker: {trade.ticker}
                          </div>
                        )}
                        <div className="mt-1 text-xs text-gray-500">{fmtTime(trade.executed_at)}</div>
                      </div>
                      <div className="text-right text-sm">
                        <div>{fmtDec(Number(trade.shares), 4)} × {fmtDec(Number(trade.price), 2)} kr</div>
                        <div className="text-gray-500">{fmt(Number(trade.total_value))} kr</div>
                      </div>
                    </div>
                    <p className="mt-4 text-sm text-gray-200">{trade.reasoning}</p>
                    {trade.hypothesis && <p className="mt-2 text-xs text-gray-500">Hypotes: {trade.hypothesis}</p>}
                    <div className="mt-4 grid grid-cols-1 gap-1 border-t border-gray-800/60 pt-3 text-xs text-gray-500 sm:grid-cols-2">
                      <div>Ursprung: {trade.decision_origin}</div>
                      <div>Beslut: {trade.decision_id ? `#${trade.decision_id}` : 'ej AI'}</div>
                      <div>Strategi: {trade.strategy_version}</div>
                      <div>
                        Marknadsbevis: {trade.source_book_state_id
                          ? `orderbok #${trade.source_book_state_id}`
                          : trade.source_quote_id
                            ? `quote #${trade.source_quote_id}`
                            : 'saknas'}
                      </div>
                      <div>Marknadstid: {fmtTime(trade.quote_event_time)}</div>
                      <div>Råfil: {trade.source_file_checksum ? trade.source_file_checksum.slice(0, 12) : 'saknas'}</div>
                      <div>Utfall: {trade.outcome ?? 'öppen'}</div>
                      <div>Realiserad P&amp;L: {trade.pnl == null ? 'ej realiserad' : `${pnlSign(Number(trade.pnl))}${fmtDec(Math.abs(Number(trade.pnl)), 2)} kr`}</div>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </div>

        <section aria-labelledby="visitor-learnings-heading" className="rounded-2xl border border-gray-800/50 bg-gray-900/50 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-wider text-gray-500">Lärandeloopen</div>
              <h2 id="visitor-learnings-heading" className="mt-1 text-lg font-semibold">
                Vad agenten lärt sig
              </h2>
            </div>
            <span className="rounded-full bg-gray-800 px-2.5 py-1 text-xs text-gray-300">
              {universe.agent_learnings.length} aktiva
            </span>
          </div>
          {universe.agent_learnings.length === 0 ? (
            <p className="mt-4 text-sm leading-6 text-gray-400">
              Inga utvärderade lärdomar ännu. En affär måste först stängas och utfallet
              jämföras med agentens hypotes innan något får kallas en lärdom.
            </p>
          ) : (
            <ul className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
              {universe.agent_learnings.slice(0, 3).map(learning => (
                <li key={`${learning.category}-${learning.updated_at}`} className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
                  <div className="text-xs font-semibold uppercase tracking-wider text-blue-300">
                    {learning.category}
                  </div>
                  <p className="mt-2 text-sm leading-6 text-gray-300">{learning.content}</p>
                  <div className="mt-3 text-xs text-gray-500">
                    Verifierad {learning.times_validated} gånger
                    {learning.confidence == null ? '' : ` · ${fmtDec(Number(learning.confidence), 0)}% säkerhet`}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── Row 4: Decisions + Info ── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* AI Decisions */}
          <div className="bg-gray-900/50 rounded-2xl p-6 border border-gray-800/50">
            <h2 className="text-sm text-gray-500 mb-4 uppercase tracking-wider">Agentens senaste analyser</h2>
            {decisions.length === 0 ? (
              <div className="text-gray-600 text-center py-4">Inga beslut ännu</div>
            ) : (
              <div className="space-y-4 max-h-96 overflow-y-auto">
                {decisions.slice(0, 8).map(d => <DecisionCard key={d.id} decision={d} />)}
              </div>
            )}
          </div>

          {/* Agent Info */}
          <div className="bg-gray-900/50 rounded-2xl p-6 border border-gray-800/50">
            <h2 className="text-sm text-gray-500 mb-4 uppercase tracking-wider">Om strategin</h2>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between gap-4"><span className="text-gray-500">Aktiv analysmodell</span><span className="text-right">{decisions[0] ? modelLabel(decisions[0].model_name) : 'Ingen analys ännu'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Startkapital</span><span>20 000 SEK</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Mål</span><span>Verifierad papertrading</span></div>
              <div className="flex justify-between gap-4"><span className="text-gray-500">Strategiversion</span><span className="text-right">{ops.strategy?.version || 'saknas'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Max positioner</span><span>{ops.risk.max_positions || '–'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Stop-loss</span><span>{ops.strategy ? `${ops.strategy.config.stop_loss_pct}%` : '–'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Take-profit</span><span>{ops.strategy ? `+${ops.strategy.config.take_profit_pct}%` : '–'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Min confidence</span><span>{ops.strategy ? `${ops.strategy.config.min_confidence}%` : '–'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Instrument</span><span>ESMA FIRDS / XSTO</span></div>
              <div className="flex justify-between gap-4"><span className="text-gray-500">Intradagsfeed</span><span className="text-right">{ops.data.provider?.product_name || 'Ej validerad'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Tidszon</span><span>{ops.market.timezone_name}</span></div>
            </div>
          </div>
        </div>

        {/* ── Footer ── */}
        <div className="flex items-center gap-2 pt-4 text-xs text-gray-600">
          <div className={`h-2 w-2 rounded-full ${operationalReady ? 'bg-green-500' : waitingForMarket ? 'bg-amber-500' : 'bg-red-500'}`}/>
          <span>
            Operatörsstatus {ops.overall_status} · Kontrollerad {fmtTime(ops.checked_at)} · Dashboard uppdaterad {lastUpdate.toLocaleTimeString('sv-SE')}
          </span>
        </div>
      </div>
    </main>
  )
}

// ── Components ──────────────────────────────────────────────

function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-gray-500 text-sm">{label}</span>
      <span className={`font-medium ${color || ''}`}>{value}</span>
    </div>
  )
}

function PositionCard({ pos }: { pos: Position }) {
  const pnl = parseFloat(String(pos.pnl_pct)) || 0
  const pnlKr = parseFloat(String(pos.pnl_kr)) || 0
  const rsi = parseFloat(String(pos.rsi)) || 50
  const conf = parseFloat(String(pos.confidence)) || 0
  const target = parseFloat(String(pos.target_price)) || 0
  const sl = parseFloat(String(pos.stop_loss)) || 0
  const current = parseFloat(String(pos.current_price)) || 0
  const entry = parseFloat(String(pos.avg_price)) || 0
  const displayName = pos.company_name?.trim() || pos.ticker
  const identifierKind = /^[A-Z]{2}[A-Z0-9]{10}$/.test(pos.ticker) ? 'ISIN' : 'Ticker'
  const sectorLabel = pos.sector === 'Unclassified' ? 'Ej klassificerad' : pos.sector

  // Progress between stop_loss and target
  const range = target - sl
  const progress = range > 0 ? Math.min(100, Math.max(0, ((current - sl) / range) * 100)) : 50

  return (
    <article className="bg-gray-900/80 rounded-xl p-4 border border-gray-800/50 hover:border-gray-700/50 transition">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-bold leading-tight">{displayName}</h3>
            {sectorLabel && <span className="text-[10px] px-1.5 py-0.5 bg-gray-800 rounded text-gray-400">{sectorLabel}</span>}
          </div>
          {displayName !== pos.ticker && (
            <div className="mt-1 text-xs text-gray-500">
              {identifierKind} {pos.ticker}
            </div>
          )}
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-lg font-bold ${pnlColor(pnl)}`}>{pnlSign(pnl)}{fmtDec(pnl)}%</div>
          <div className={`text-xs ${pnlColor(pnlKr)}`}>{pnlSign(pnlKr)}{fmt(pnlKr)} kr</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs mb-3">
        <div><span className="text-gray-600">Köpt</span><div>{fmtDec(entry)}</div></div>
        <div><span className="text-gray-600">Nu</span><div>{fmtDec(current)}</div></div>
        <div><span className="text-gray-600">Värde</span><div>{fmt(parseFloat(String(pos.market_value)))} kr</div></div>
      </div>

      {/* Price progress bar */}
      {target > 0 && sl > 0 && (
        <div className="mb-3">
          <div className="flex justify-between text-[10px] text-gray-600 mb-0.5">
            <span>SL {fmtDec(sl)}</span>
            <span>Target {fmtDec(target)}</span>
          </div>
          <div className="bg-gray-800 rounded-full h-1.5 overflow-hidden">
            <div className={`h-full rounded-full transition-all ${pnl >= 0 ? 'bg-green-500' : 'bg-red-500'}`} style={{ width: `${progress}%` }}/>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 text-[10px]">
        {conf > 0 && <span className="px-1.5 py-0.5 bg-blue-900/30 text-blue-400 rounded">{fmtDec(conf, 0)}% conf</span>}
        <RSIBadge rsi={rsi} />
        {pos.days_held != null && <span className="text-gray-600">{pos.days_held}d</span>}
        <span className="text-gray-600">{parseFloat(String(pos.shares)).toFixed(1)} st</span>
      </div>
      <div className="mt-3 border-t border-gray-800/50 pt-2 text-[10px] text-gray-600">
        Pris: {pos.price_source} · {fmtTime(pos.price_event_time)}
      </div>
    </article>
  )
}

function RSIBadge({ rsi }: { rsi: number }) {
  const color = rsi > 70 ? 'bg-red-900/30 text-red-400' : rsi < 30 ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-500'
  const label = rsi > 70 ? 'Överköpt' : rsi < 30 ? 'Översåld' : 'RSI'
  return <span className={`px-1.5 py-0.5 rounded ${color}`}>{label} {fmtDec(rsi, 0)}</span>
}

function modelLabel(model: string | null | undefined) {
  const labels: Record<string, string> = {
    'gpt-5.6-sol': 'GPT-5.6 Sol',
    'gpt-5.6-terra': 'GPT-5.6 Terra',
    'gpt-5.6-luna': 'GPT-5.6 Luna',
    'qwen2.5-coder:14b': 'Qwen 2.5 Coder 14B',
  }
  return model ? (labels[model] || model) : 'Okänd modell'
}

function DecisionCard({ decision }: { decision: Decision }) {
  let parsed: any = {}
  try {
    parsed = typeof decision.decisions_json === 'string' ? JSON.parse(decision.decisions_json) : decision.decisions_json
  } catch { return null }

  const outlook = parsed.market_outlook || 'neutral'
  const decs = parsed.decisions || []
  const outlookColor = outlook === 'bullish' ? 'bg-green-900/30 text-green-400' : outlook === 'bearish' ? 'bg-red-900/30 text-red-400' : 'bg-gray-800 text-gray-400'
  const time = new Date(decision.timestamp).toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' })
  const date = new Date(decision.timestamp).toLocaleDateString('sv-SE')

  return (
    <div className="border-b border-gray-800/50 pb-3 last:border-0">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs text-gray-600">{date} {time}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${outlookColor}`}>{outlook}</span>
        <span className="text-[10px] text-gray-600">
          {modelLabel(decision.model_name)}
          {decision.reasoning_effort ? ` · ${decision.reasoning_effort}` : ''}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
            {decs.slice(0, 6).map((d: any, i: number) => {
              const action = (d.action || '').toUpperCase()
              const companyLabel = decisionCompanyLabel(
                d.ticker,
                decision.instrument_names,
              )
              const presentation = decisionActionPresentation({
                action,
                ticker: d.ticker,
                predictionCount: decision.prediction_count,
                executedTrades: decision.executed_trades,
              })
              const actionColor = presentation.tone === 'buy'
                ? 'text-green-400'
                : presentation.tone === 'sell'
                  ? 'text-red-400'
                  : presentation.tone === 'wait'
                    ? 'text-amber-300'
                    : 'text-gray-400'
              return (
                <div key={i} className="rounded bg-gray-800/50 px-2 py-1 text-xs">
                  <div>
                    <span className={actionColor}>{presentation.label}</span>{' '}
                    <span className="font-medium text-gray-100">{companyLabel}</span>{' '}
                    <span className="text-gray-600">{d.confidence}%</span>
                  </div>
                  {presentation.detail && (
                    <div className="mt-0.5 text-[10px] text-gray-500">
                      {presentation.detail}
                    </div>
                  )}
                </div>
              )
            })}
      </div>
      {parsed.analysis_summary && <div className="text-xs text-gray-600 mt-1 line-clamp-2">{parsed.analysis_summary}</div>}
    </div>
  )
}

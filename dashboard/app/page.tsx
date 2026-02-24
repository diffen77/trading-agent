'use client'

import { useState, useEffect, useCallback } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

// ── Types ──────────────────────────────────────────────────
interface Portfolio { cash: number; positions_value: number; total_value: number; pnl: number; pnl_pct: number }
interface Position { ticker: string; shares: number; avg_price: number; current_price: number; company_name: string; sector: string; rsi: number; confidence: number; reasoning: string; pnl_pct: number; pnl_kr: number; market_value: number; days_held: number; target_price: number; stop_loss: number }
interface HistoryPoint { total_value: number; recorded_at: string }
interface Macro { symbol: string; type: string; value: number; change_pct: number }
interface Decision { id: number; decisions_json: string; timestamp: string }
interface Performance { total_trades: number; buys: number; sells: number; winners: number; losers: number; win_rate: number; avg_win: number; avg_loss: number; total_pnl: number; best_trade: number; worst_trade: number; avg_confidence: number; open_positions: number }

const MACRO_NAMES: Record<string, string> = { 'GC=F': '🥇 Guld', 'BZ=F': '🛢️ Brent', 'HG=F': '🔶 Koppar', 'EURSEK=X': '€/SEK', 'USDSEK=X': '$/SEK', '^OMX': 'OMXS30' }
const PERIODS = ['1D', '1W', '1M', 'YTD', '1Y'] as const
type Period = typeof PERIODS[number]

function fmt(n: number): string { return new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 0 }).format(n) }
function fmtDec(n: number, d = 2): string { return new Intl.NumberFormat('sv-SE', { minimumFractionDigits: d, maximumFractionDigits: d }).format(n) }
function pnlColor(n: number): string { return n > 0.01 ? 'text-green-400' : n < -0.01 ? 'text-red-400' : 'text-gray-400' }
function pnlSign(n: number): string { return n > 0.01 ? '+' : '' }

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const [macro, setMacro] = useState<Macro[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [perf, setPerf] = useState<Performance | null>(null)
  const [period, setPeriod] = useState<Period>('1M')
  const [lastUpdate, setLastUpdate] = useState(new Date())
  const [loading, setLoading] = useState(true)

  const fetchAll = useCallback(async () => {
    try {
      const [pRes, posRes, hRes, mRes, dRes, pfRes] = await Promise.all([
        fetch('/api/portfolio'), fetch('/api/positions'), fetch(`/api/history?period=${period}`),
        fetch('/api/macro'), fetch('/api/decisions'), fetch('/api/performance'),
      ])
      if (pRes.ok) setPortfolio(await pRes.json())
      if (posRes.ok) setPositions(await posRes.json())
      if (hRes.ok) setHistory(await hRes.json())
      if (mRes.ok) setMacro(await mRes.json())
      if (dRes.ok) setDecisions(await dRes.json())
      if (pfRes.ok) setPerf(await pfRes.json())
      setLastUpdate(new Date())
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [period])

  useEffect(() => { fetchAll(); const iv = setInterval(fetchAll, 30000); return () => clearInterval(iv) }, [fetchAll])

  if (loading) return <div className="min-h-screen flex items-center justify-center bg-[#0a0a0f] text-white text-xl">Laddar...</div>

  const p = portfolio || { cash: 20000, positions_value: 0, total_value: 20000, pnl: 0, pnl_pct: 0 }
  const pf = perf || { total_trades: 0, win_rate: 0, avg_confidence: 0, open_positions: 0, total_pnl: 0, buys: 0, sells: 0, winners: 0, losers: 0, avg_win: 0, avg_loss: 0, best_trade: 0, worst_trade: 0 }
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
        {/* ── Row 1: Chart + Performance ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Portfolio Chart */}
          <div className="lg:col-span-2 bg-gray-900/50 rounded-2xl p-6 border border-gray-800/50">
            <div className="flex justify-between items-start mb-4">
              <div>
                <div className="text-gray-500 text-sm">Portföljvärde</div>
                <div className="text-4xl font-bold">{fmt(p.total_value)} <span className="text-xl text-gray-600">SEK</span></div>
                <div className={`text-sm mt-1 ${pnlColor(p.pnl_pct)}`}>
                  {pnlSign(p.pnl_pct)}{fmtDec(p.pnl_pct)}% ({pnlSign(p.pnl)}{fmt(p.pnl)} kr)
                </div>
              </div>
              <div className="flex gap-1 bg-gray-800/50 rounded-lg p-1">
                {PERIODS.map(pr => (
                  <button key={pr} onClick={() => setPeriod(pr)}
                    className={`px-3 py-1.5 rounded text-xs font-medium transition ${period === pr ? 'bg-white text-black' : 'text-gray-500 hover:text-white'}`}>
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
            <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-gray-800/50 text-sm">
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
                <StatRow label="Mål" value="20k → 40k (31 juli)" />
                <div className="mt-2 bg-gray-800 rounded-full h-2 overflow-hidden">
                  <div className="bg-green-500 h-full rounded-full transition-all" style={{ width: `${Math.min(100, Math.max(0, ((p.total_value - 20000) / 20000) * 100))}%` }}/>
                </div>
                <div className="text-xs text-gray-600 mt-1">{fmtDec(Math.max(0, ((p.total_value - 20000) / 20000) * 100), 1)}% av målet</div>
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
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* AI Decisions */}
          <div className="bg-gray-900/50 rounded-2xl p-6 border border-gray-800/50">
            <h2 className="text-sm text-gray-500 mb-4 uppercase tracking-wider">🧠 AI Beslut</h2>
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
            <h2 className="text-sm text-gray-500 mb-4 uppercase tracking-wider">📋 Agent Info</h2>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between"><span className="text-gray-500">Modell</span><span>qwen2.5-coder-32b</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Startkapital</span><span>20 000 SEK</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Mål</span><span>40 000 SEK (31 juli 2026)</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Strategi</span><span>TA + AI-analys</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Max positioner</span><span>5</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Stop-loss</span><span>-5%</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Take-profit</span><span>+10%</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Min confidence</span><span>55%</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Datakällor</span><span>Yahoo Finance + RSS</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Brain cycle</span><span>Var 30 min (08-16 UTC)</span></div>
            </div>
          </div>
        </div>

        {/* ── Footer ── */}
        <div className="flex items-center gap-2 text-xs text-gray-600 pt-4">
          <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"/>
          <span>Agent aktiv · Uppdaterad {lastUpdate.toLocaleTimeString('sv-SE')}</span>
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

  // Progress between stop_loss and target
  const range = target - sl
  const progress = range > 0 ? Math.min(100, Math.max(0, ((current - sl) / range) * 100)) : 50

  return (
    <div className="bg-gray-900/80 rounded-xl p-4 border border-gray-800/50 hover:border-gray-700/50 transition">
      <div className="flex justify-between items-start mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold">{pos.ticker}</span>
            {pos.sector && <span className="text-[10px] px-1.5 py-0.5 bg-gray-800 rounded text-gray-400">{pos.sector}</span>}
          </div>
          {pos.company_name && <div className="text-xs text-gray-600">{pos.company_name}</div>}
        </div>
        <div className="text-right">
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
    </div>
  )
}

function RSIBadge({ rsi }: { rsi: number }) {
  const color = rsi > 70 ? 'bg-red-900/30 text-red-400' : rsi < 30 ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-500'
  const label = rsi > 70 ? 'Överköpt' : rsi < 30 ? 'Översåld' : 'RSI'
  return <span className={`px-1.5 py-0.5 rounded ${color}`}>{label} {fmtDec(rsi, 0)}</span>
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
      </div>
      <div className="flex flex-wrap gap-1.5">
        {decs.slice(0, 6).map((d: any, i: number) => {
          const action = (d.action || '').toUpperCase()
          const actionColor = action === 'BUY' ? 'text-green-400' : action === 'SELL' ? 'text-red-400' : 'text-gray-400'
          return (
            <span key={i} className="text-xs bg-gray-800/50 px-2 py-0.5 rounded">
              <span className={actionColor}>{action}</span> {d.ticker} <span className="text-gray-600">{d.confidence}%</span>
            </span>
          )
        })}
      </div>
      {parsed.analysis_summary && <div className="text-xs text-gray-600 mt-1 line-clamp-2">{parsed.analysis_summary}</div>}
    </div>
  )
}

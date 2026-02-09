'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'

interface PortfolioData {
  cash: number
  positions_value: number
  total_value: number
  pnl: number
  pnl_pct: number
}

interface HistoryPoint {
  total_value: number
  recorded_at: string
}

interface Trade {
  id: number
  ticker: string
  action: string
  shares: number
  price: number
  total_value: number
  reasoning: string
  confidence: number
  hypothesis: string
  executed_at: string
  pnl?: number
  target_pct?: number
  stop_loss_pct?: number
  target_level?: number
  stop_loss_level?: number
  closed_at?: string
  outcome?: string
}

interface Stock {
  ticker: string
  price: number
  date: string
}

interface Macro {
  symbol: string
  type: string
  value: number
  change_pct: number
}

interface Prospect {
  id: number
  ticker: string
  name: string
  thesis: string
  entry_trigger: string
  confidence: number
  priority: number
  latest_price: number
  added_at: string
}

const MACRO_NAMES: Record<string, string> = {
  'GC=F': '🥇 Guld',
  'BZ=F': '🛢️ Brent',
  'HG=F': '🔶 Koppar',
  'EURSEK=X': '€/SEK',
  'USDSEK=X': '$/SEK',
  '^OMX': 'OMX30',
}

const PERIODS = ['1D', '1W', '1M', 'YTD', '1Y'] as const
type Period = typeof PERIODS[number]

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null)
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [stocks, setStocks] = useState<Stock[]>([])
  const [macro, setMacro] = useState<Macro[]>([])
  const [prospects, setProspects] = useState<Prospect[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())
  const [selectedPeriod, setSelectedPeriod] = useState<Period>('1M')
  
  const prevPortfolio = useRef<PortfolioData | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [portfolioRes, tradesRes, stocksRes, macroRes, prospectsRes, historyRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/trades'),
        fetch('/api/stocks'),
        fetch('/api/macro'),
        fetch('/api/prospects'),
        fetch(`/api/history?period=${selectedPeriod}`),
      ])
      
      if (portfolioRes.ok) {
        const newPortfolio = await portfolioRes.json()
        prevPortfolio.current = portfolio
        setPortfolio(newPortfolio)
      }
      if (tradesRes.ok) setTrades(await tradesRes.json())
      if (stocksRes.ok) setStocks(await stocksRes.json())
      if (macroRes.ok) setMacro(await macroRes.json())
      if (prospectsRes.ok) setProspects(await prospectsRes.json())
      if (historyRes.ok) setHistory(await historyRes.json())
      
      setLastUpdate(new Date())
    } catch (error) {
      console.error('Error fetching data:', error)
    } finally {
      setLoading(false)
    }
  }, [portfolio, selectedPeriod])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [selectedPeriod])

  const getCurrentPrice = (ticker: string): number | null => {
    const stock = stocks.find(s => s.ticker === ticker)
    return stock ? parseFloat(String(stock.price)) : null
  }

  // Calculate period performance
  const periodPerformance = () => {
    if (history.length < 2) return { pnl: 0, pnlPct: 0 }
    const start = parseFloat(String(history[0]?.total_value || 20000))
    const end = parseFloat(String(portfolio?.total_value || start))
    const pnl = end - start
    const pnlPct = ((end / start) - 1) * 100
    return { pnl, pnlPct }
  }

  const { pnl: periodPnl, pnlPct: periodPnlPct } = periodPerformance()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-black text-white">
        <div className="text-xl">Laddar...</div>
      </div>
    )
  }

  const headerMacro = macro.filter(m => 
    ['GC=F', 'BZ=F', 'HG=F', 'EURSEK=X', 'USDSEK=X', '^OMX'].includes(m.symbol)
  )

  // Prepare chart data
  const chartData = history.map(h => ({
    date: new Date(h.recorded_at).toLocaleDateString('sv-SE', { month: 'short', day: 'numeric' }),
    value: parseFloat(String(h.total_value)),
  }))

  // Add current value if different from last history point
  if (portfolio && chartData.length > 0) {
    const lastHistoryValue = chartData[chartData.length - 1].value
    const currentValue = parseFloat(String(portfolio.total_value))
    if (Math.abs(currentValue - lastHistoryValue) > 1) {
      chartData.push({
        date: 'Nu',
        value: currentValue,
      })
    }
  }

  const isPositivePeriod = periodPnlPct >= 0

  return (
    <main className="min-h-screen bg-black text-white">
      {/* Macro Header Bar */}
      <header className="border-b border-gray-800 px-4 py-2">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-bold">Trading Agent</h1>
          <div className="flex gap-4 text-xs">
            {headerMacro.map(m => (
              <MacroChip key={m.symbol} item={m} />
            ))}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto p-8">
        {/* Hero Portfolio Section with Chart */}
        <section className="mb-12">
          <div className="bg-gray-900 rounded-3xl p-8">
            {/* Top row: Value and Period selector */}
            <div className="flex justify-between items-start mb-6">
              <div>
                <div className="text-gray-500 text-sm mb-1">Totalt värde</div>
                <div className="text-5xl font-bold tracking-tight">
                  {formatCurrency(portfolio?.total_value || 20000)} <span className="text-2xl text-gray-500">SEK</span>
                </div>
                <div className={`text-lg mt-2 ${isPositivePeriod ? 'text-green-500' : 'text-red-500'}`}>
                  {isPositivePeriod ? '+' : ''}{periodPnlPct.toFixed(2)}% 
                  <span className="text-gray-500 ml-2">
                    ({isPositivePeriod ? '+' : ''}{formatCurrency(periodPnl)} kr)
                  </span>
                </div>
              </div>
              
              {/* Period Selector */}
              <div className="flex gap-1 bg-gray-800 rounded-xl p-1">
                {PERIODS.map(period => (
                  <button
                    key={period}
                    onClick={() => setSelectedPeriod(period)}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                      selectedPeriod === period 
                        ? 'bg-white text-black' 
                        : 'text-gray-400 hover:text-white'
                    }`}
                  >
                    {period}
                  </button>
                ))}
              </div>
            </div>

            {/* Chart */}
            <div className="h-64 mt-4">
              {chartData.length > 1 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                        <stop 
                          offset="5%" 
                          stopColor={isPositivePeriod ? '#22c55e' : '#ef4444'} 
                          stopOpacity={0.3}
                        />
                        <stop 
                          offset="95%" 
                          stopColor={isPositivePeriod ? '#22c55e' : '#ef4444'} 
                          stopOpacity={0}
                        />
                      </linearGradient>
                    </defs>
                    <XAxis 
                      dataKey="date" 
                      axisLine={false}
                      tickLine={false}
                      tick={{ fill: '#6b7280', fontSize: 12 }}
                    />
                    <YAxis 
                      domain={['dataMin - 500', 'dataMax + 500']}
                      axisLine={false}
                      tickLine={false}
                      tick={{ fill: '#6b7280', fontSize: 12 }}
                      tickFormatter={(v) => `${(v/1000).toFixed(0)}k`}
                    />
                    <Tooltip 
                      contentStyle={{ 
                        backgroundColor: '#1f2937', 
                        border: 'none', 
                        borderRadius: '12px',
                        padding: '12px'
                      }}
                      labelStyle={{ color: '#9ca3af' }}
                      formatter={(value: number) => [`${formatCurrency(value)} SEK`, 'Värde']}
                    />
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke={isPositivePeriod ? '#22c55e' : '#ef4444'}
                      strokeWidth={2}
                      fillOpacity={1}
                      fill="url(#colorValue)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-gray-500">
                  <div className="text-center">
                    <div className="text-4xl mb-2">📊</div>
                    <div>Graf visas när mer data finns</div>
                  </div>
                </div>
              )}
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-3 gap-6 mt-8 pt-6 border-t border-gray-800">
              <div>
                <div className="text-gray-500 text-sm">Kontanter</div>
                <div className="text-2xl font-semibold">{formatCurrency(portfolio?.cash || 20000)}</div>
              </div>
              <div>
                <div className="text-gray-500 text-sm">Investerat</div>
                <div className="text-2xl font-semibold">{formatCurrency(portfolio?.positions_value || 0)}</div>
              </div>
              <div>
                <div className="text-gray-500 text-sm">Total avkastning</div>
                <div className={`text-2xl font-semibold ${(portfolio?.pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                  {(portfolio?.pnl || 0) >= 0 ? '+' : ''}{formatCurrency(portfolio?.pnl || 0)}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Trades */}
        <section className="mb-12">
          <h2 className="text-2xl font-semibold mb-6">Positioner & Trades</h2>
          {trades.length === 0 ? (
            <div className="bg-gray-900 rounded-2xl p-8 text-center">
              <p className="text-gray-500">Inga trades ännu.</p>
            </div>
          ) : (
            <div className="space-y-4">
              {trades.map((trade) => (
                <TradeCard 
                  key={trade.id} 
                  trade={trade} 
                  currentPrice={getCurrentPrice(trade.ticker)}
                />
              ))}
            </div>
          )}
        </section>

        {/* Prospects / Watchlist */}
        <section className="mb-12">
          <h2 className="text-2xl font-semibold mb-6">🎯 Prospects</h2>
          {prospects.length === 0 ? (
            <div className="bg-gray-900 rounded-2xl p-8 text-center">
              <p className="text-gray-500">Ingen watchlist.</p>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {prospects.map((prospect) => (
                <ProspectCard key={prospect.id} prospect={prospect} />
              ))}
            </div>
          )}
        </section>

        {/* Status */}
        <section>
          <div className="flex items-center gap-3 text-sm text-gray-500">
            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
            <span>Agent aktiv · Uppdaterad {lastUpdate.toLocaleTimeString('sv-SE')}</span>
          </div>
        </section>
      </div>
    </main>
  )
}

function MacroChip({ item }: { item: Macro }) {
  const name = MACRO_NAMES[item.symbol] || item.symbol
  const changePct = parseFloat(String(item.change_pct))
  const isPositive = changePct >= 0
  
  return (
    <div className="flex items-center gap-1">
      <span className="text-gray-500">{name}</span>
      <span className="font-medium">
        {parseFloat(String(item.value)).toFixed(item.type === 'currency' ? 2 : 0)}
      </span>
      <span className={isPositive ? 'text-green-500' : 'text-red-500'}>
        {isPositive ? '↑' : '↓'}{Math.abs(changePct).toFixed(1)}%
      </span>
    </div>
  )
}

function TradeCard({ trade, currentPrice }: { trade: Trade, currentPrice: number | null }) {
  const isBuy = trade.action === 'BUY'
  const entryPrice = parseFloat(String(trade.price))
  const shares = parseFloat(String(trade.shares))
  const totalValue = parseFloat(String(trade.total_value))
  const confidence = trade.confidence ? parseFloat(String(trade.confidence)) : null
  
  const currentValue = currentPrice ? shares * currentPrice : null
  const pnlKr = currentValue ? currentValue - totalValue : null
  const pnlPct = currentValue ? ((currentValue / totalValue) - 1) * 100 : null
  const isProfit = pnlKr !== null && pnlKr >= 0
  
  return (
    <div className="bg-gray-900 rounded-2xl p-6">
      <div className="flex justify-between items-start mb-4">
        <div className="flex items-center gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                isBuy ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
              }`}>
                {trade.action}
              </span>
              <span className="text-xl font-bold">{trade.ticker}</span>
            </div>
            <div className="text-sm text-gray-500 mt-1">
              {shares.toFixed(0)} st · {new Date(trade.executed_at).toLocaleDateString('sv-SE')}
            </div>
          </div>
        </div>
        
        <div className="text-right">
          <div className="flex items-center gap-4 text-sm">
            <div>
              <div className="text-gray-500">Köpt</div>
              <div className="font-medium">{entryPrice.toFixed(2)}</div>
            </div>
            <div>
              <div className="text-gray-500">Nu</div>
              <div className="font-medium">{currentPrice?.toFixed(2) || '-'}</div>
            </div>
            <div>
              <div className="text-gray-500">P&L</div>
              <div className={`font-bold ${isProfit ? 'text-green-500' : 'text-red-500'}`}>
                {pnlPct !== null ? `${isProfit ? '+' : ''}${pnlPct.toFixed(1)}%` : '-'}
              </div>
            </div>
            <div>
              <div className="text-gray-500">&nbsp;</div>
              <div className={`font-medium ${isProfit ? 'text-green-500' : 'text-red-500'}`}>
                {pnlKr !== null ? `${isProfit ? '+' : ''}${pnlKr.toFixed(0)} kr` : '-'}
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <div className="border-t border-gray-800 pt-4 mt-2">
        <div className="text-sm text-gray-300">{trade.reasoning}</div>
        {trade.hypothesis && (
          <div className="text-sm text-gray-500 mt-2">
            <strong>Hypotes:</strong> {trade.hypothesis}
          </div>
        )}
        <div className="flex items-center gap-3 mt-3">
          {confidence && (
            <span className="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">
              Confidence: {confidence.toFixed(0)}%
            </span>
          )}
          {trade.stop_loss_level && (
            <span className="px-2 py-0.5 bg-red-900/50 rounded text-xs text-red-400">
              Stop-loss: {parseFloat(String(trade.stop_loss_level)).toFixed(2)} ({trade.stop_loss_pct}%)
            </span>
          )}
          {trade.target_level && (
            <span className="px-2 py-0.5 bg-green-900/50 rounded text-xs text-green-400">
              Target: {parseFloat(String(trade.target_level)).toFixed(2)} ({trade.target_pct && '+' + trade.target_pct}%)
            </span>
          )}
          {trade.closed_at && (
            <span className="px-2 py-0.5 bg-yellow-900/50 rounded text-xs text-yellow-400">
              Stängd: {new Date(trade.closed_at).toLocaleDateString('sv-SE')}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

function ProspectCard({ prospect }: { prospect: Prospect }) {
  const confidence = prospect.confidence ? parseFloat(String(prospect.confidence)) : 0
  const price = prospect.latest_price ? parseFloat(String(prospect.latest_price)) : null
  
  const priorityColors: Record<number, string> = {
    1: 'bg-green-900 text-green-300',
    2: 'bg-blue-900 text-blue-300',
    3: 'bg-yellow-900 text-yellow-300',
    4: 'bg-gray-800 text-gray-300',
    5: 'bg-gray-800 text-gray-400',
  }
  
  return (
    <div className="bg-gray-900 rounded-2xl p-5">
      <div className="flex justify-between items-start mb-3">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${priorityColors[prospect.priority] || priorityColors[5]}`}>
            #{prospect.priority}
          </span>
          <span className="text-lg font-bold">{prospect.ticker}</span>
          <span className="text-gray-500 text-sm">{prospect.name}</span>
        </div>
        <div className="text-right">
          <div className="text-sm text-gray-500">Pris</div>
          <div className="font-medium">{price?.toFixed(2) || '-'}</div>
        </div>
      </div>
      
      <p className="text-sm text-gray-300 mb-3">{prospect.thesis}</p>
      
      <div className="flex items-center justify-between text-xs">
        <div className="text-gray-500">
          <strong>Entry:</strong> {prospect.entry_trigger}
        </div>
        <div className="px-2 py-0.5 bg-gray-800 rounded text-gray-400">
          {confidence.toFixed(0)}% confidence
        </div>
      </div>
    </div>
  )
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('sv-SE', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
}

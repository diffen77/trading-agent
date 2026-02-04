'use client'

import { useState, useEffect, useCallback, useRef } from 'react'

interface PortfolioData {
  cash: number
  positions_value: number
  total_value: number
  pnl: number
  pnl_pct: number
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

const MACRO_NAMES: Record<string, string> = {
  'GC=F': '🥇 Guld',
  'BZ=F': '🛢️ Brent',
  'HG=F': '🔶 Koppar',
  'EURSEK=X': '€/SEK',
  'USDSEK=X': '$/SEK',
  '^OMX': 'OMX30',
}

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [stocks, setStocks] = useState<Stock[]>([])
  const [macro, setMacro] = useState<Macro[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())
  
  // Refs to track previous values for animations
  const prevPortfolio = useRef<PortfolioData | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [portfolioRes, tradesRes, stocksRes, macroRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/trades'),
        fetch('/api/stocks'),
        fetch('/api/macro'),
      ])
      
      if (portfolioRes.ok) {
        const newPortfolio = await portfolioRes.json()
        prevPortfolio.current = portfolio
        setPortfolio(newPortfolio)
      }
      if (tradesRes.ok) setTrades(await tradesRes.json())
      if (stocksRes.ok) setStocks(await stocksRes.json())
      if (macroRes.ok) setMacro(await macroRes.json())
      
      setLastUpdate(new Date())
    } catch (error) {
      console.error('Error fetching data:', error)
    } finally {
      setLoading(false)
    }
  }, [portfolio])

  useEffect(() => {
    fetchData()
    // Poll every 30 seconds for real-time feel
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  const getCurrentPrice = (ticker: string): number | null => {
    const stock = stocks.find(s => s.ticker === ticker)
    return stock ? parseFloat(String(stock.price)) : null
  }

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
        {/* Portfolio Overview */}
        <section className="mb-12">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <AnimatedStatCard
              title="Totalt värde"
              value={portfolio?.total_value || 20000}
              format="currency"
              prevValue={prevPortfolio.current?.total_value}
            />
            <AnimatedStatCard
              title="Kontanter"
              value={portfolio?.cash || 20000}
              format="currency"
              prevValue={prevPortfolio.current?.cash}
            />
            <AnimatedStatCard
              title="Positioner"
              value={portfolio?.positions_value || 0}
              format="currency"
              prevValue={prevPortfolio.current?.positions_value}
            />
            <AnimatedStatCard
              title="Avkastning"
              value={portfolio?.pnl_pct || 0}
              format="percent"
              subtitle={`${(portfolio?.pnl || 0) >= 0 ? '+' : ''}${formatCurrency(portfolio?.pnl || 0)} SEK`}
              prevValue={prevPortfolio.current?.pnl_pct}
            />
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

// Animated stat card that highlights changes
function AnimatedStatCard({ 
  title, 
  value, 
  format,
  subtitle,
  prevValue 
}: { 
  title: string
  value: number
  format: 'currency' | 'percent'
  subtitle?: string
  prevValue?: number
}) {
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  
  useEffect(() => {
    if (prevValue !== undefined && prevValue !== value) {
      setFlash(value > prevValue ? 'up' : 'down')
      const timer = setTimeout(() => setFlash(null), 1000)
      return () => clearTimeout(timer)
    }
  }, [value, prevValue])

  const isPositive = value >= 0
  const displayValue = format === 'currency' 
    ? formatCurrency(value)
    : `${isPositive ? '+' : ''}${value.toFixed(2)}%`

  const flashClass = flash === 'up' 
    ? 'bg-green-900/50' 
    : flash === 'down' 
    ? 'bg-red-900/50' 
    : 'bg-gray-900'

  return (
    <div className={`rounded-2xl p-6 transition-colors duration-300 ${flashClass}`}>
      <div className="text-sm text-gray-500 mb-1">{title}</div>
      <div className={`text-3xl font-bold transition-all duration-300 ${
        format === 'percent' 
          ? isPositive ? 'text-green-500' : 'text-red-500'
          : 'text-white'
      }`}>
        {displayValue}
      </div>
      {subtitle && (
        <div className={`text-sm ${isPositive ? 'text-gray-500' : 'text-red-400'}`}>
          {subtitle}
        </div>
      )}
    </div>
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
        {confidence && (
          <div className="inline-block mt-2 px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">
            Confidence: {confidence.toFixed(0)}%
          </div>
        )}
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

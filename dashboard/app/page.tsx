'use client'

import { useState, useEffect } from 'react'

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
  name?: string
  sector?: string
}

interface Macro {
  symbol: string
  type: string
  value: number
  change_pct: number
}

const MACRO_NAMES: Record<string, string> = {
  'GC=F': '🥇 Guld',
  'SI=F': '🥈 Silver', 
  'CL=F': '🛢️ WTI',
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

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [])

  const fetchData = async () => {
    try {
      const [portfolioRes, tradesRes, stocksRes, macroRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/trades'),
        fetch('/api/stocks'),
        fetch('/api/macro'),
      ])
      
      if (portfolioRes.ok) setPortfolio(await portfolioRes.json())
      if (tradesRes.ok) setTrades(await tradesRes.json())
      if (stocksRes.ok) setStocks(await stocksRes.json())
      if (macroRes.ok) setMacro(await macroRes.json())
    } catch (error) {
      console.error('Error fetching data:', error)
    } finally {
      setLoading(false)
    }
  }

  // Get current price for a ticker
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

  // Filter macro for header - only key ones
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
            {headerMacro.map(m => {
              const changePct = parseFloat(String(m.change_pct))
              const isPositive = changePct >= 0
              return (
                <div key={m.symbol} className="flex items-center gap-1">
                  <span className="text-gray-500">{MACRO_NAMES[m.symbol] || m.symbol}</span>
                  <span className="font-medium">{parseFloat(String(m.value)).toFixed(m.type === 'currency' ? 2 : 0)}</span>
                  <span className={isPositive ? 'text-green-500' : 'text-red-500'}>
                    {isPositive ? '↑' : '↓'}{Math.abs(changePct).toFixed(1)}%
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto p-8">
        {/* Portfolio Overview */}
        <section className="mb-12">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <StatCard
              title="Totalt värde"
              value={formatCurrency(portfolio?.total_value || 20000)}
              subtitle="SEK"
            />
            <StatCard
              title="Kontanter"
              value={formatCurrency(portfolio?.cash || 20000)}
              subtitle="SEK"
            />
            <StatCard
              title="Positioner"
              value={formatCurrency(portfolio?.positions_value || 0)}
              subtitle="SEK"
            />
            <StatCard
              title="Avkastning"
              value={`${(portfolio?.pnl_pct || 0) >= 0 ? '+' : ''}${(portfolio?.pnl_pct || 0).toFixed(2)}%`}
              subtitle={`${(portfolio?.pnl || 0) >= 0 ? '+' : ''}${formatCurrency(portfolio?.pnl || 0)} SEK`}
              highlight={(portfolio?.pnl_pct || 0) >= 0}
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

        {/* Stock Overview */}
        <section className="mb-12">
          <h2 className="text-2xl font-semibold mb-6">Bevakade aktier ({stocks.length})</h2>
          <div className="bg-gray-900 rounded-2xl p-6 overflow-x-auto">
            <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-3 text-sm">
              {stocks.map(stock => (
                <div key={stock.ticker} className="text-center">
                  <div className="font-medium">{stock.ticker}</div>
                  <div className="text-gray-500">{parseFloat(String(stock.price)).toFixed(0)}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Agent Status */}
        <section>
          <div className="flex items-center gap-3 text-sm text-gray-500">
            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
            <span>Agent aktiv · Uppdaterad {new Date().toLocaleTimeString('sv-SE')}</span>
          </div>
        </section>
      </div>
    </main>
  )
}

function StatCard({ 
  title, 
  value, 
  subtitle, 
  highlight = true 
}: { 
  title: string
  value: string
  subtitle: string
  highlight?: boolean 
}) {
  return (
    <div className="bg-gray-900 rounded-2xl p-6">
      <div className="text-sm text-gray-500 mb-1">{title}</div>
      <div className={`text-3xl font-bold ${highlight ? 'text-white' : 'text-red-500'}`}>
        {value}
      </div>
      <div className={`text-sm ${highlight ? 'text-gray-500' : 'text-red-400'}`}>{subtitle}</div>
    </div>
  )
}

function TradeCard({ trade, currentPrice }: { trade: Trade, currentPrice: number | null }) {
  const isBuy = trade.action === 'BUY'
  const entryPrice = parseFloat(String(trade.price))
  const shares = parseFloat(String(trade.shares))
  const totalValue = parseFloat(String(trade.total_value))
  const confidence = trade.confidence ? parseFloat(String(trade.confidence)) : null
  
  // Calculate P&L
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
        
        {/* Price & P&L */}
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
        <div className="text-sm text-gray-300">
          {trade.reasoning}
        </div>
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

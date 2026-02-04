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
  'CL=F': '🛢️ Olja WTI',
  'BZ=F': '🛢️ Olja Brent',
  'HG=F': '🔶 Koppar',
  'NG=F': '🔥 Naturgas',
  'EURSEK=X': '💶 EUR/SEK',
  'USDSEK=X': '💵 USD/SEK',
  'EURUSD=X': '💱 EUR/USD',
  '^OMX': '📈 OMX30',
  '^OMXSPI': '📊 OMXS All',
  '^GSPC': '🇺🇸 S&P 500',
}

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [stocks, setStocks] = useState<Stock[]>([])
  const [macro, setMacro] = useState<Macro[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60000) // Uppdatera varje minut
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

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-black text-white">
        <div className="text-xl">Laddar...</div>
      </div>
    )
  }

  const commodities = macro.filter(m => m.type === 'commodity')
  const currencies = macro.filter(m => m.type === 'currency')
  const indices = macro.filter(m => m.type === 'index')

  return (
    <main className="min-h-screen p-8 max-w-7xl mx-auto bg-black text-white">
      <header className="mb-12">
        <h1 className="text-5xl font-bold tracking-tight">Trading Agent</h1>
        <p className="text-gray-500 mt-2">AI-driven papertrading på Stockholmsbörsen</p>
      </header>

      {/* Portfolio Overview */}
      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-6">Portfölj</h2>
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
            value={`${(portfolio?.pnl_pct || 0).toFixed(2)}%`}
            subtitle={formatCurrency(portfolio?.pnl || 0) + ' SEK'}
            highlight={portfolio?.pnl_pct ? portfolio.pnl_pct >= 0 : true}
          />
        </div>
      </section>

      {/* Macro Overview */}
      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-6">Makro & Omvärld</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Commodities */}
          <div className="bg-gray-900 rounded-2xl p-6">
            <h3 className="text-lg font-medium mb-4 text-gray-400">Råvaror</h3>
            <div className="space-y-3">
              {commodities.map(m => (
                <MacroRow key={m.symbol} item={m} />
              ))}
            </div>
          </div>
          
          {/* Currencies */}
          <div className="bg-gray-900 rounded-2xl p-6">
            <h3 className="text-lg font-medium mb-4 text-gray-400">Valutor</h3>
            <div className="space-y-3">
              {currencies.map(m => (
                <MacroRow key={m.symbol} item={m} />
              ))}
            </div>
          </div>
          
          {/* Indices */}
          <div className="bg-gray-900 rounded-2xl p-6">
            <h3 className="text-lg font-medium mb-4 text-gray-400">Index</h3>
            <div className="space-y-3">
              {indices.map(m => (
                <MacroRow key={m.symbol} item={m} />
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Recent Trades */}
      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-6">Senaste trades</h2>
        {trades.length === 0 ? (
          <div className="bg-gray-900 rounded-2xl p-8 text-center">
            <p className="text-gray-500">Inga trades ännu. Agenten analyserar marknaden...</p>
          </div>
        ) : (
          <div className="space-y-4">
            {trades.map((trade) => (
              <TradeCard key={trade.id} trade={trade} />
            ))}
          </div>
        )}
      </section>

      {/* Stock Overview */}
      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-6">Aktier ({stocks.length} st)</h2>
        <div className="bg-gray-900 rounded-2xl p-6 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-800">
                <th className="pb-3">Ticker</th>
                <th className="pb-3">Pris</th>
                <th className="pb-3">Sektor</th>
              </tr>
            </thead>
            <tbody>
              {stocks.slice(0, 20).map(stock => (
                <tr key={stock.ticker} className="border-b border-gray-800/50">
                  <td className="py-3 font-medium">{stock.ticker}</td>
                  <td className="py-3">{stock.price?.toFixed(2)} SEK</td>
                  <td className="py-3 text-gray-500">{stock.sector || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {stocks.length > 20 && (
            <p className="text-gray-500 mt-4 text-center">
              + {stocks.length - 20} fler aktier
            </p>
          )}
        </div>
      </section>

      {/* Agent Status */}
      <section>
        <h2 className="text-2xl font-semibold mb-6">Agent Status</h2>
        <div className="bg-gray-900 rounded-2xl p-6">
          <div className="flex items-center gap-3">
            <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
            <span>Aktiv — Analyserar marknaden</span>
          </div>
          <div className="mt-4 text-sm text-gray-500">
            Senast uppdaterad: {new Date().toLocaleString('sv-SE')}
          </div>
        </div>
      </section>
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
      <div className="text-sm text-gray-500">{subtitle}</div>
    </div>
  )
}

function MacroRow({ item }: { item: Macro }) {
  const name = MACRO_NAMES[item.symbol] || item.symbol
  const isPositive = item.change_pct >= 0
  
  return (
    <div className="flex justify-between items-center">
      <span className="text-gray-300">{name}</span>
      <div className="text-right">
        <span className="font-medium">{item.value.toFixed(2)}</span>
        <span className={`ml-2 text-sm ${isPositive ? 'text-green-500' : 'text-red-500'}`}>
          {isPositive ? '+' : ''}{item.change_pct.toFixed(1)}%
        </span>
      </div>
    </div>
  )
}

function TradeCard({ trade }: { trade: Trade }) {
  const isBuy = trade.action === 'BUY'
  
  return (
    <div className="bg-gray-900 rounded-2xl p-6">
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-3">
            <span className={`px-2 py-1 rounded text-sm font-medium ${
              isBuy ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
            }`}>
              {trade.action}
            </span>
            <span className="text-xl font-bold">{trade.ticker}</span>
          </div>
          <div className="text-sm text-gray-500 mt-1">
            {trade.shares.toFixed(2)} aktier @ {trade.price.toFixed(2)} SEK
          </div>
        </div>
        <div className="text-right">
          <div className="font-semibold">{formatCurrency(trade.total_value)} SEK</div>
          <div className="text-sm text-gray-500">
            {new Date(trade.executed_at).toLocaleDateString('sv-SE')}
          </div>
        </div>
      </div>
      
      <div className="border-t border-gray-800 pt-4 mt-4">
        <div className="text-sm">
          <strong className="text-gray-400">Varför:</strong> {trade.reasoning}
        </div>
        {trade.hypothesis && (
          <div className="text-sm text-gray-500 mt-2">
            <strong>Hypotes:</strong> {trade.hypothesis}
          </div>
        )}
        {trade.confidence && (
          <div className="text-sm text-gray-500 mt-1">
            Confidence: {trade.confidence}%
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

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

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Fetch data from API
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      const [portfolioRes, tradesRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/trades'),
      ])
      
      if (portfolioRes.ok) {
        setPortfolio(await portfolioRes.json())
      }
      if (tradesRes.ok) {
        setTrades(await tradesRes.json())
      }
    } catch (error) {
      console.error('Error fetching data:', error)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-xl">Laddar...</div>
      </div>
    )
  }

  return (
    <main className="min-h-screen p-8 max-w-6xl mx-auto">
      <header className="mb-12">
        <h1 className="text-4xl font-bold tracking-tight">Trading Agent</h1>
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

      {/* Recent Trades */}
      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-6">Senaste trades</h2>
        {trades.length === 0 ? (
          <div className="bg-gray-50 dark:bg-gray-900 rounded-2xl p-8 text-center">
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

      {/* Agent Status */}
      <section>
        <h2 className="text-2xl font-semibold mb-6">Agent Status</h2>
        <div className="bg-gray-50 dark:bg-gray-900 rounded-2xl p-6">
          <div className="flex items-center gap-3">
            <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
            <span>Aktiv — Nästa körning vid börsöppning 09:00</span>
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
    <div className="bg-gray-50 dark:bg-gray-900 rounded-2xl p-6">
      <div className="text-sm text-gray-500 mb-1">{title}</div>
      <div className={`text-3xl font-bold ${highlight ? '' : 'text-red-500'}`}>
        {value}
      </div>
      <div className="text-sm text-gray-400">{subtitle}</div>
    </div>
  )
}

function TradeCard({ trade }: { trade: Trade }) {
  const isBuy = trade.action === 'BUY'
  
  return (
    <div className="bg-gray-50 dark:bg-gray-900 rounded-2xl p-6">
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-3">
            <span className={`px-2 py-1 rounded text-sm font-medium ${
              isBuy ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
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
      
      <div className="border-t border-gray-200 dark:border-gray-700 pt-4 mt-4">
        <div className="text-sm">
          <strong>Varför:</strong> {trade.reasoning}
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

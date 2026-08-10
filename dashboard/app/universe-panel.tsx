export interface UniverseStatus {
  summary: {
    active_instruments: number
    company_mappings: number
    provider_aliases: number
    mapping_coverage_pct: number
    market_data_files: number
    archivable_data_files: number
    object_archived_files: number
    object_archive_pending_files: number
    object_archive_total_bytes: number
    object_archive_status: string | null
    object_archive_evaluated_at: string | null
    object_archive_failed_count: number
    market_quotes: number
    ai_decisions: number
    trades: number
    active_learnings: number
    snapshot_date: string | null
    snapshot_received_at: string | null
    snapshot_file_count: number
    snapshot_instrument_count: number
    snapshot_provider: string | null
    contract_provider: string | null
    contract_product_name: string | null
    contract_data_type: string | null
    authorization_basis: string | null
    contract_delivery_mode: string | null
    contract_status: string | null
    validation_status: string | null
    acceptance_session_count: number
  }
  breakdown: {
    currencies: Array<{ key: string; count: number }>
    instrument_types: Array<{ key: string; count: number }>
  }
  instruments: Array<{
    isin: string
    symbol: string | null
    name: string
    cfi_code: string | null
    currency: string
    instrument_type: string
    first_trade_date: string | null
    reference_snapshot_date: string | null
    source: string
    synced_at: string
    ticker: string | null
    uses_isin_key: boolean
  }>
  agent_learnings: Array<{
    category: string
    content: string
    confidence: number | null
    times_validated: number
    updated_at: string
  }>
  insights: Array<{
    code: string
    level: 'ready' | 'blocked' | 'waiting'
    title: string
    detail: string
  }>
  pagination: {
    page: number
    page_size: number
    total_items: number
    total_pages: number
  }
}

function formatDate(value: string | null) {
  if (!value) return 'saknas'
  return new Date(value).toLocaleDateString('sv-SE')
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toLocaleString('sv-SE', {
    maximumFractionDigits: 1,
  })} MB`
}

function insightStyle(level: UniverseStatus['insights'][number]['level']) {
  if (level === 'ready') {
    return 'border-green-900/60 bg-green-950/20 text-green-200'
  }
  if (level === 'blocked') {
    return 'border-red-900/60 bg-red-950/20 text-red-200'
  }
  return 'border-amber-900/60 bg-amber-950/20 text-amber-200'
}

function insightLabel(level: UniverseStatus['insights'][number]['level']) {
  if (level === 'ready') return 'KLART'
  if (level === 'blocked') return 'BLOCKERAT'
  return 'VÄNTAR'
}

export function UniversePanel({ universe }: { universe: UniverseStatus }) {
  const { summary } = universe

  return (
    <section
      aria-labelledby="universe-heading"
      className="min-w-0 rounded-2xl border border-gray-800/60 bg-gray-900/35 p-4 sm:p-6"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Verifierat nuläge
          </div>
          <h2 id="universe-heading" className="mt-1 text-2xl font-bold">
            Marknadsdata &amp; lärdomar
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            Senaste XSTO-snapshot {formatDate(summary.snapshot_date)} från{' '}
            {summary.snapshot_provider || 'okänd källa'}.
          </p>
        </div>
        <div className="text-left text-xs text-gray-500 sm:text-right">
          <div>{summary.snapshot_file_count} källfiler</div>
          <div>Mottagen {formatDate(summary.snapshot_received_at)}</div>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          label="Aktiva instrument"
          value={`${summary.active_instruments}`}
          detail={`${summary.snapshot_instrument_count} i snapshot`}
        />
        <Metric
          label="Instrumentkoppling"
          value={`${summary.company_mappings}`}
          detail={`${summary.mapping_coverage_pct}% täckning`}
        />
        <Metric
          label="Prisuppdateringar"
          value={`${summary.market_quotes}`}
          detail={`${summary.market_data_files} sparade marknadsfiler`}
        />
        <Metric
          label="Rådataarkiv"
          value={`${summary.object_archived_files}/${summary.archivable_data_files}`}
          detail={`${formatBytes(summary.object_archive_total_bytes)} · ${summary.object_archive_pending_files} väntar`}
        />
        <Metric
          label="Handel & lärande"
          value={`${summary.trades} affärer`}
          detail={`${summary.active_learnings} aktiva lärdomar`}
        />
      </div>

      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <h3 className="text-sm font-semibold text-gray-200">
            Vad datan faktiskt säger
          </h3>
          <ul className="mt-3 space-y-2">
            {universe.insights.map(insight => (
              <li
                key={insight.code}
                className={`rounded-xl border p-3 ${insightStyle(insight.level)}`}
              >
                <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                  <span className="font-medium">{insight.title}</span>
                  <span className="w-fit rounded-full bg-black/20 px-2 py-0.5 text-[10px] font-semibold tracking-wide">
                    {insightLabel(insight.level)}
                  </span>
                </div>
                <p className="mt-1 text-xs opacity-75">{insight.detail}</p>
              </li>
            ))}
          </ul>
        </div>

        <div className="space-y-4 xl:col-span-2">
          <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
            <h3 className="text-xs uppercase tracking-wider text-gray-500">
              Aktiv prisfeed
            </h3>
            <div className="mt-2 font-semibold">
              {summary.contract_product_name || 'Ingen feed registrerad'}
            </div>
            <dl className="mt-3 grid min-w-0 grid-cols-2 gap-x-3 gap-y-2 text-xs">
              <dt className="text-gray-500">Leverantör</dt>
              <dd className="min-w-0 break-words text-right">{summary.contract_provider || '–'}</dd>
              <dt className="text-gray-500">Leverans</dt>
              <dd className="min-w-0 break-words text-right">{summary.contract_delivery_mode || '–'}</dd>
              <dt className="text-gray-500">Avtal</dt>
              <dd className="min-w-0 break-words text-right">{summary.contract_status || 'saknas'}</dd>
              <dt className="text-gray-500">Validering</dt>
              <dd className="min-w-0 break-words text-right">{summary.validation_status || 'saknas'}</dd>
              <dt className="text-gray-500">Acceptans</dt>
              <dd className="text-right">
                {summary.acceptance_session_count}/
                {summary.authorization_basis === 'PUBLIC_NONCOMMERCIAL_TERMS' ? 1 : 5}
                {' '}valideringar
              </dd>
            </dl>
          </div>

          <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
            <h3 className="text-xs uppercase tracking-wider text-gray-500">
              Fördelning
            </h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {universe.breakdown.currencies.map(item => (
                <span
                  key={`currency-${item.key}`}
                  className="rounded-full bg-gray-800 px-2.5 py-1 text-xs text-gray-300"
                >
                  {item.key} {item.count}
                </span>
              ))}
              {universe.breakdown.instrument_types.map(item => (
                <span
                  key={`type-${item.key}`}
                  className="rounded-full bg-blue-950/50 px-2.5 py-1 text-xs text-blue-200"
                >
                  {item.key} {item.count}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-6 overflow-hidden rounded-xl border border-gray-800/70">
        <div className="border-b border-gray-800/70 bg-gray-950/50 px-4 py-3">
          <h3 className="font-semibold">Instrument från Stockholmsbörsen</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            Visar {universe.instruments.length} av {universe.pagination.total_items}.
            ISIN är den stabila interna nyckeln; ticker visas bara när en
            separat officiell symbolmappning finns.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-800/70 text-left text-sm">
            <caption className="sr-only">
              Importerade XSTO-instrument och deras mappningsstatus
            </caption>
            <thead className="bg-gray-950/30 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th scope="col" className="px-4 py-3">Instrument</th>
                <th scope="col" className="px-4 py-3">Officiell ticker</th>
                <th scope="col" className="px-4 py-3">ISIN</th>
                <th scope="col" className="px-4 py-3">Typ</th>
                <th scope="col" className="px-4 py-3">FIRDS nominell valuta</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {universe.instruments.map(instrument => (
                <tr key={instrument.isin} className="bg-gray-900/20">
                  <td className="max-w-sm px-4 py-3">
                    <div className="truncate font-medium text-gray-200">
                      {instrument.name}
                    </div>
                    <div className="text-xs text-gray-600">
                      Snapshot {formatDate(instrument.reference_snapshot_date)}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {instrument.ticker || instrument.symbol || (
                      <span className="text-gray-500">
                        Ej tillgänglig i gratisflödet
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-gray-400">
                    {instrument.isin}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-gray-400">
                    {instrument.instrument_type}
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {instrument.currency}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-5 rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
        <h3 className="text-sm font-semibold">Agentens handelslärdomar</h3>
        {universe.agent_learnings.length === 0 ? (
          <p className="mt-2 text-sm text-gray-500">
            Inga utvärderade handelslärdomar ännu. Systemet väntar på
            verifierade papertrades med mätbara utfall.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {universe.agent_learnings.map((learning, index) => (
              <li
                key={`${learning.category}-${index}`}
                className="rounded-lg border border-gray-800/60 px-3 py-2"
              >
                <div className="text-xs uppercase text-gray-500">
                  {learning.category} · validerad {learning.times_validated} gånger
                </div>
                <p className="mt-1 text-sm text-gray-200">{learning.content}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="rounded-xl border border-gray-800/70 bg-gray-950/40 p-4">
      <div className="text-xs uppercase tracking-wider text-gray-500">{label}</div>
      <div className="mt-2 text-2xl font-bold">{value}</div>
      <div className="mt-1 text-xs text-gray-500">{detail}</div>
    </div>
  )
}

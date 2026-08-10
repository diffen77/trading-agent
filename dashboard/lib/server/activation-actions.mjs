const REFERENCE_DATA_URL = (
  'https://www.nasdaq.com/sv-se/solutions/data/'
  + 'nasdaq-nordic-reference-data-files'
)
const DELAYED_DATA_URL = (
  'https://www.nasdaq.com/market-regulation/nordic/mifid-ii'
)
const PRE_TRADE_URL = (
  'https://tradereports.nasdaq.com/shares/trade-reports/pre-trade'
)
const NORDIC_EQUITY_URL = (
  'https://www.nasdaq.com/solutions/data/equities/Nordic-Baltic'
)


function count(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0
}


function progress(current, required) {
  const safeRequired = count(required)
  return {
    current: Math.min(count(current), safeRequired),
    required: safeRequired,
  }
}


export function buildActivationActions(status) {
  const universe = status?.universe ?? {}
  const data = status?.data ?? {}
  const provider = data.provider ?? null
  const index = status?.index ?? null
  const benchmark = status?.benchmark ?? null
  const publicPretrade = (
    provider?.data_type === 'delayed-pre-trade-equity'
    && provider?.authorization_basis === 'PUBLIC_NONCOMMERCIAL_TERMS'
  )

  const expected = count(universe.expected_instruments)
  const mapped = publicPretrade
    ? count(universe.company_mappings)
    : Math.min(
      count(universe.provider_aliases),
      count(universe.company_mappings),
    )
  const referenceReady = (
    publicPretrade
    || data.reference_entitlement?.entitlement_ready === true
  )
  const mappingReady = expected > 0 && mapped === expected
  const quoteContractReady = provider?.contract_ready === true
  const quoteValidationReady = provider?.validation_ready === true
  const quoteReady = quoteContractReady && quoteValidationReady
  const acceptanceSessions = (
    quoteValidationReady
      ? Math.max(
        publicPretrade ? 1 : 5,
        count(provider?.acceptance_session_count),
      )
      : count(provider?.acceptance_session_count)
  )
  const indexContractReady = (
    publicPretrade || index?.contract_ready === true
  )
  const indexValidationReady = (
    publicPretrade || index?.validation_ready === true
  )
  const indexReady = indexContractReady && indexValidationReady
  const benchmarkPassed = (
    benchmark?.passed === true || benchmark?.status === 'PASSED'
  )
  const benchmarkRunning = benchmark?.status === 'RUNNING'
  const prerequisitesReady = (
    referenceReady
    && mappingReady
    && quoteReady
    && indexReady
  )

  return [
    {
      code: 'REFERENCE_ACCESS',
      priority: 1,
      title: 'Säkra officiell referensdata',
      status: referenceReady ? 'READY' : 'ACTION_REQUIRED',
      owner: 'Du + Nasdaq',
      automation: publicPretrade ? 'AUTOMATIC' : 'MANUAL',
      detail: referenceReady
        ? (
          publicPretrade
            ? 'Officiell FIRDS-identitet är verifierad; ISIN används som intern nyckel när gratis börsticker saknas.'
            : 'Verifierad åtkomst, lagringsrätt och giltighet är registrerade.'
        )
        : (
          'Exakta ticker-, valuta- och bolagskopplingar kräver '
          + 'rättighetsgodkänd Nasdaq-referensdata.'
        ),
      next_step: referenceReady
        ? 'Behåll avtals- och lagringsbeviset giltigt.'
        : (
          'Beställ Nordic Equity Reference Data Files och godkänn '
          + 'villkor för intern lagring.'
        ),
      progress: progress(referenceReady ? 1 : 0, 1),
      href: REFERENCE_DATA_URL,
      source_label: 'Nasdaq Nordic Reference Data',
    },
    {
      code: 'QUOTE_GOVERNANCE',
      priority: 2,
      title: 'Godkänn 15-minuters prisflöde',
      status: quoteContractReady ? 'READY' : 'ACTION_REQUIRED',
      owner: 'Du + operatör',
      automation: publicPretrade ? 'AUTOMATIC' : 'MANUAL',
      detail: quoteContractReady
        ? (
          publicPretrade
            ? 'Nasdaqs publicerade villkor, 15-minutersfördröjning och filintegritet verifieras vid varje synk.'
            : 'Prisflödets villkor, transport och lagring är verifierade.'
        )
        : (
          'Nasdaq publicerar officiell 15-minutersdata, men systemet '
          + 'saknar ett granskat bevis för användning och lagring.'
        ),
      next_step: quoteContractReady
        ? (
          publicPretrade
            ? 'Fortsätt den automatiska kontrollen av varje minutfil.'
            : 'Låt agenten genomföra de fem acceptanssessionerna.'
        )
        : (
          'Granska villkoren och registrera godkänd användning, '
          + 'lagring, retention och transport.'
        ),
      progress: progress(quoteContractReady ? 1 : 0, 1),
      href: DELAYED_DATA_URL,
      source_label: 'Nasdaq MiFID II delayed data',
    },
    {
      code: 'INSTRUMENT_MAPPING',
      priority: 3,
      title: 'Mappa hela Stockholmsbörsen',
      status: mappingReady
        ? 'READY'
        : (
          referenceReady
            ? (mapped > 0 ? 'RUNNING' : 'ACTION_REQUIRED')
            : 'WAITING'
        ),
      owner: 'Operatör + agent',
      automation: 'AUTOMATIC',
      detail: (
        publicPretrade
          ? `${mapped} av ${expected} instrument har verifierad ISIN-koppling.`
          : (
            `${mapped} av ${expected} instrument har både tickeralias `
            + 'och verifierad bolagskoppling.'
          )
      ),
      next_step: mappingReady
        ? 'Övervaka att referenssnapshoten förblir komplett.'
        : (
          referenceReady
            ? (
              mapped > 0
                ? 'Fortsätt importera och verifiera saknade kopplingar.'
                : 'Starta den godkända Nasdaq-referenssynken.'
            )
            : 'Väntar på rättighetsgodkänd referensdata.'
        ),
      progress: progress(mapped, expected),
    },
    {
      code: 'PROVIDER_ACCEPTANCE',
      priority: 4,
      title: publicPretrade
        ? 'Verifiera varje fördröjd minutfil'
        : 'Verifiera prisflödet i fem börssessioner',
      status: quoteReady
        ? 'READY'
        : (
          quoteContractReady && mappingReady
            ? (
              acceptanceSessions > 0
                ? 'RUNNING'
                : 'ACTION_REQUIRED'
            )
            : 'WAITING'
        ),
      owner: 'Operatör + agent',
      automation: 'AUTOMATIC',
      detail: (
        publicPretrade
          ? (
            `${Math.min(acceptanceSessions, 1)} av 1 obligatorisk `
            + 'kontinuerlig filvalidering är aktiv.'
          )
          : (
            `${Math.min(acceptanceSessions, 5)} av 5 officiella `
            + 'acceptanssessioner är godkända.'
          )
      ),
      next_step: quoteReady
        ? 'Fortsätt kontrollera täckning, tidsstämplar och datagap.'
        : (
          quoteContractReady && mappingReady
            ? (
              acceptanceSessions > 0
                ? 'Kör nästa marknadssession och validera hela universet.'
                : 'Starta insamlingen vid nästa officiella börssession.'
            )
            : 'Väntar på godkänt prisflöde och komplett mappning.'
        ),
      progress: progress(acceptanceSessions, publicPretrade ? 1 : 5),
      href: PRE_TRADE_URL,
      source_label: 'Nasdaq Nordic pre-trade files',
    },
    {
      code: 'INDEX_ACCESS',
      priority: 5,
      title: 'Säkra separat OMXSGI-signal',
      status: indexReady
        ? 'READY'
        : 'ACTION_REQUIRED',
      owner: 'Du + Nasdaq',
      automation: 'MANUAL',
      detail: indexReady
        ? (
          publicPretrade
            ? 'Separat OMXSGI-signal krävs inte för att starta intern papertrading; indexbenchmark läggs till separat.'
            : 'OMXSGI-avtal, transport och signal är verifierade.'
        )
        : (
          'Indexdata omfattas inte automatiskt av det fördröjda '
          + 'aktieflödet och kräver separat rättighet.'
        ),
      next_step: indexReady
        ? (
          publicPretrade
            ? 'Välj senare en laglig benchmark utan att blockera paperflödet.'
            : 'Övervaka signalens färskhet och giltighet.'
        )
        : (
          indexContractReady
            ? 'Slutför transport- och signalvalideringen.'
            : 'Beställ eller godkänn rättighet till OMXSGI.'
        ),
      progress: progress(indexReady ? 1 : 0, 1),
      href: NORDIC_EQUITY_URL,
      source_label: 'Nasdaq Nordic & Baltic Equity',
    },
    {
      code: 'FORWARD_PAPER',
      priority: 6,
      title: 'Bevisa strategin i forward paper trading',
      status: benchmarkPassed
        ? 'READY'
        : (
          benchmarkRunning
            ? 'RUNNING'
            : (prerequisitesReady ? 'ACTION_REQUIRED' : 'WAITING')
        ),
      owner: 'Operatör + agent',
      automation: 'EXPERIMENT',
      detail: (
        `${count(benchmark?.trading_sessions)} av `
        + `${count(benchmark?.min_trading_sessions) || 252} `
        + 'krävda handelssessioner är registrerade.'
      ),
      next_step: benchmarkPassed
        ? 'Utvärdera separat beslut om mäklarintegration.'
        : (
          benchmarkRunning
            ? 'Fortsätt paper trading och registrera varje utfall.'
            : (
              prerequisitesReady
                ? 'Starta det frysta forward-testet utan riktiga pengar.'
                : 'Väntar tills data, mappning och index är verifierade.'
            )
        ),
      progress: progress(
        benchmark?.trading_sessions,
        count(benchmark?.min_trading_sessions) || 252,
      ),
    },
  ]
}

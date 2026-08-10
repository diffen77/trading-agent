const ISIN_PATTERN = /^[A-Z]{2}[A-Z0-9]{10}$/

function parsedDecisionPayload(value) {
  if (typeof value !== 'string') return value
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

export function collectDecisionIdentifiers(rows) {
  const identifiers = new Set()

  for (const row of rows) {
    const payload = parsedDecisionPayload(row?.decisions_json)
    const decisions = Array.isArray(payload?.decisions)
      ? payload.decisions
      : []

    for (const decision of decisions) {
      const identifier = typeof decision?.ticker === 'string'
        ? decision.ticker.trim()
        : ''
      if (identifier) identifiers.add(identifier)
    }
  }

  return [...identifiers]
}

export function decisionCompanyLabel(identifierValue, instrumentNames = {}) {
  const identifier = typeof identifierValue === 'string'
    ? identifierValue.trim()
    : ''
  const companyName = typeof instrumentNames?.[identifier] === 'string'
    ? instrumentNames[identifier].trim()
    : ''

  if (companyName) return companyName
  if (!identifier || ISIN_PATTERN.test(identifier)) return 'Bolagsnamn saknas'
  return identifier
}

export function actionLabel(actionValue) {
  const action = typeof actionValue === 'string'
    ? actionValue.trim().toUpperCase()
    : ''

  if (action === 'BUY') return 'KÖP'
  if (action === 'SELL') return 'SÄLJ'
  if (action === 'HOLD') return 'AVVAKTA'
  return action || 'OKÄNT'
}

export function decisionActionPresentation({
  action: actionValue,
  ticker: tickerValue,
  predictionCount,
  executedTrades,
} = {}) {
  const action = typeof actionValue === 'string'
    ? actionValue.trim().toUpperCase()
    : ''
  const ticker = typeof tickerValue === 'string'
    ? tickerValue.trim()
    : ''
  const trades = Array.isArray(executedTrades) ? executedTrades : []
  const wasExecuted = trades.some(trade => {
    const tradeTicker = typeof trade?.ticker === 'string'
      ? trade.ticker.trim()
      : ''
    const tradeAction = typeof trade?.action === 'string'
      ? trade.action.trim().toUpperCase()
      : ''
    return ticker !== '' && tradeTicker === ticker && tradeAction === action
  })

  if ((action === 'BUY' || action === 'SELL') && wasExecuted) {
    return {
      label: action === 'BUY' ? 'KÖPT' : 'SÅLT',
      detail: 'Papertrade genomförd',
      status: 'EXECUTED',
      tone: action === 'BUY' ? 'buy' : 'sell',
    }
  }

  if (action === 'BUY' || action === 'SELL') {
    const hasPredictionEvidence = Number(predictionCount) > 0
    return {
      label: 'AVVAKTA',
      detail: hasPredictionEvidence
        ? `Modellförslag: ${actionLabel(action)} · inte genomfört`
        : 'Analysen kunde inte verifieras',
      status: hasPredictionEvidence ? 'PROPOSED' : 'UNVERIFIED',
      tone: 'wait',
    }
  }

  if (action === 'HOLD') {
    return {
      label: 'AVVAKTA',
      detail: null,
      status: 'HOLD',
      tone: 'neutral',
    }
  }

  return {
    label: actionLabel(action),
    detail: null,
    status: 'UNKNOWN',
    tone: 'neutral',
  }
}

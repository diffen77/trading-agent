import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const page = readFileSync(
  new URL('../app/page.tsx', import.meta.url),
  'utf8',
)
const activationQueue = readFileSync(
  new URL('../app/activation-queue.tsx', import.meta.url),
  'utf8',
)


test('dashboard fetches and renders operational readiness evidence', () => {
  assert.match(page, /fetch\('\/api\/operations'\)/)
  assert.match(page, /Handelsberedskap/)
  assert.match(page, /operations\.overall_status/)
  assert.match(page, /QUOTE_PROVIDER_NOT_READY/)
  assert.match(
    page,
    /REFERENCE_ENTITLEMENT_NOT_READY: 'Licens- och lagringsbevis för referensdata saknas'/,
  )
  assert.match(page, /INDEX_SIGNAL_NOT_READY/)
  assert.match(page, /pos\.price_source/)
  assert.match(page, /ops\.monitoring\.active_alerts/)
  assert.match(page, /ops\.monitoring\.scheduled_routines/)
  assert.match(page, /Aktiva driftlarm/)
  assert.match(page, /Senaste schemakörningar/)
  assert.match(page, /operations\.activation_actions/)
  assert.match(page, /<ActivationQueue/)
  assert.match(activationQueue, /Aktiveringskö/)
  assert.match(activationQueue, /Åtgärder i blockeringsordning/)
  assert.match(activationQueue, /action\.next_step/)
})


test('dashboard does not show an unconditional verified green pulse', () => {
  assert.doesNotMatch(
    page,
    /w-2 h-2 bg-green-500 rounded-full animate-pulse/,
  )
})


test('dashboard period controls stack before the small-screen breakpoint', () => {
  assert.match(
    page,
    /flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between/,
  )
  assert.match(page, /flex-1 rounded px-2 .*sm:flex-none sm:px-3/)
})


test('position cards prioritize the human-readable company name', () => {
  assert.match(
    page,
    /const displayName = pos\.company_name\?\.trim\(\) \|\| pos\.ticker/,
  )
  assert.match(page, /<h3[^>]*>\{displayName\}<\/h3>/)
  assert.match(page, /\{identifierKind\} \{pos\.ticker\}/)
})


test('visitor content comes before collapsed technical evidence', () => {
  const visitorOverview = page.indexOf('<VisitorOverview')
  const technicalDetails = page.indexOf('<details')
  const positions = page.indexOf('Positioner ({positions.length})')

  assert.ok(visitorOverview >= 0)
  assert.ok(technicalDetails > visitorOverview)
  assert.ok(positions > technicalDetails)
  assert.match(page, /Teknik och transparens/)
  assert.match(
    page,
    /<details[\s\S]*<ActivationQueue[\s\S]*<UniversePanel[\s\S]*<\/details>/,
  )
})


test('trade journal and learning summary are written for visitors', () => {
  assert.match(page, /trade\.company_name\?\.trim\(\) \|\| trade\.ticker/)
  assert.match(page, /Vad agenten lärt sig/)
  assert.match(page, /Inga utvärderade lärdomar ännu/)
})


test('analysis cards never render raw decision identifiers as company names', () => {
  assert.match(page, /decisionCompanyLabel/)
  assert.match(page, /decisionActionPresentation/)
  assert.doesNotMatch(page, /\{action\}<\/span> \{d\.ticker\}/)
})

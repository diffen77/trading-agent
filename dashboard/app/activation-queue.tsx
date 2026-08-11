export interface ActivationAction {
  code: string
  priority: number
  title: string
  status: 'READY' | 'ACTION_REQUIRED' | 'RUNNING' | 'WAITING'
  owner: string
  automation: 'MANUAL' | 'AUTOMATIC' | 'EXPERIMENT'
  detail: string
  next_step: string
  progress: {
    current: number
    required: number
  }
  href?: string
  source_label?: string
}

const STATUS_LABELS: Record<ActivationAction['status'], string> = {
  READY: 'KLAR',
  ACTION_REQUIRED: 'ÅTGÄRD KRÄVS',
  RUNNING: 'PÅGÅR',
  WAITING: 'VÄNTAR',
}

const STATUS_STYLES: Record<ActivationAction['status'], string> = {
  READY: 'border-green-900/70 bg-green-950/20 text-green-300',
  ACTION_REQUIRED: 'border-amber-800/70 bg-amber-950/25 text-amber-200',
  RUNNING: 'border-blue-800/70 bg-blue-950/25 text-blue-200',
  WAITING: 'border-gray-800 bg-gray-950/40 text-gray-400',
}


function progressText(action: ActivationAction) {
  if (action.progress.required <= 1) {
    return action.status === 'READY' ? 'Verifierad' : 'Ej verifierad'
  }
  return `${action.progress.current} / ${action.progress.required}`
}


export function ActivationQueue({
  actions,
  checkedAt,
}: {
  actions: ActivationAction[]
  checkedAt: string | null
}) {
  const firstBlockingCode = actions.find(
    action => action.status !== 'READY',
  )?.code

  return (
    <section
      aria-labelledby="activation-queue-heading"
      className="rounded-2xl border border-gray-800/70 bg-gray-900/40 p-5 sm:p-6"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">
            Åtgärder i blockeringsordning
          </p>
          <h2 id="activation-queue-heading" className="mt-1 text-xl font-semibold text-white">
            Aktiveringskö
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-400">
            Pågående betyder att verklig progress finns registrerad.
            Automatiska steg måste först aktiveras av operatören.
            Inga simulerade actions räknas som trades.
          </p>
        </div>
        {checkedAt && (
          <time className="text-xs text-gray-500" dateTime={checkedAt}>
            Senast kontrollerad {new Date(checkedAt).toLocaleString('sv-SE')}
          </time>
        )}
      </div>

      <ol className="mt-5 grid gap-3 lg:grid-cols-2">
        {actions.map(action => {
          const isNext = action.code === firstBlockingCode
          return (
            <li
              key={action.code}
              className={`rounded-xl border p-4 ${STATUS_STYLES[action.status]} ${
                isNext ? 'ring-1 ring-amber-400/50' : ''
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <span
                    aria-hidden="true"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-current/30 text-xs font-bold"
                  >
                    {action.priority}
                  </span>
                  <div>
                    {isNext && (
                      <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-amber-300">
                        Nästa blockerande steg
                      </p>
                    )}
                    <h3 className="font-semibold text-gray-100">
                      {action.title}
                    </h3>
                  </div>
                </div>
                <span className="shrink-0 rounded-full border border-current/30 px-2 py-1 text-[10px] font-bold tracking-wide">
                  {STATUS_LABELS[action.status]}
                </span>
              </div>

              <p className="mt-3 text-sm text-gray-300">{action.detail}</p>
              <p className="mt-2 text-sm text-gray-200">
                <span className="font-semibold">Nästa:</span>{' '}
                {action.next_step}
              </p>

              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-current/15 pt-3 text-xs">
                <span>Ägare: {action.owner}</span>
                <span>Progress: {progressText(action)}</span>
                {action.href && (
                  <a
                    href={action.href}
                    target="_blank"
                    rel="noreferrer"
                    className="font-semibold underline decoration-current/40 underline-offset-4 hover:text-white"
                  >
                    {action.source_label || 'Officiell källa'}
                  </a>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

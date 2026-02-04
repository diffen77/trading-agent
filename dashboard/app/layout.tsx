import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Trading Agent',
  description: 'AI Trading Agent Dashboard - Stockholmsbörsen',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="sv">
      <body>{children}</body>
    </html>
  )
}

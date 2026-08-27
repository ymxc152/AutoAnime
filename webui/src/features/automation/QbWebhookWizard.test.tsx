import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { QbWebhookWizard, qbCurlTemplate } from './QbWebhookWizard'

describe('QbWebhookWizard', () => {
  it('shows the qBittorrent curl template with %F', () => {
    render(<QbWebhookWizard token="one-time-secret" origin="http://127.0.0.1:8765" />)
    expect(screen.getByText('one-time-secret')).toBeInTheDocument()
    expect(screen.getAllByText(/\/api\/v1\/hooks\/downloaders\/one-time-secret/).length).toBeGreaterThan(0)
    expect(screen.getByText(/仅显示一次/)).toBeInTheDocument()
    expect(qbCurlTemplate('http://127.0.0.1:8765', 'one-time-secret')).toContain('%F')
    expect(screen.getByText(/curl -s -X POST/)).toBeInTheDocument()
  })
})

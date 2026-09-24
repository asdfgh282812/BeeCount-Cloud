import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, configureHttp, extractApiError } from '@beecount/api-client'

describe('extractApiError', () => {
  it('parses write conflict metadata', async () => {
    const response = new Response(
      JSON.stringify({
        error: { code: 'WRITE_CONFLICT', message: 'Write conflict' },
        detail: 'Write conflict',
        latest_change_id: 42,
        latest_server_timestamp: '2026-02-24T12:00:00+00:00'
      }),
      {
        status: 409,
        headers: { 'Content-Type': 'application/json' }
      }
    )

    const err = await extractApiError(response)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('WRITE_CONFLICT')
    expect(err.latestChangeId).toBe(42)
    expect(err.latestServerTimestamp).toBe('2026-02-24T12:00:00+00:00')
    expect(err.message).toBe('[WRITE_CONFLICT] Write conflict')
  })

  it('falls back to plain text message', async () => {
    const response = new Response('boom', { status: 500 })
    const err = await extractApiError(response)

    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(500)
    expect(err.message).toBe('boom')
  })

  describe('license required hook', () => {
    afterEach(() => {
      configureHttp({ refreshToken: null, onLogout: null, onLicenseRequired: null })
    })

    it('fires onLicenseRequired for 402 LICENSE_REQUIRED', async () => {
      const onLicenseRequired = vi.fn()
      configureHttp({ onLicenseRequired })
      const response = new Response(
        JSON.stringify({
          error: { code: 'LICENSE_REQUIRED', message: 'License required' },
          detail: 'License required',
          error_code: 'LICENSE_REQUIRED'
        }),
        { status: 402, headers: { 'Content-Type': 'application/json' } }
      )
      const err = await extractApiError(response)
      expect(err.code).toBe('LICENSE_REQUIRED')
      expect(onLicenseRequired).toHaveBeenCalledTimes(1)
    })

    it('does not fire for other errors', async () => {
      const onLicenseRequired = vi.fn()
      configureHttp({ onLicenseRequired })
      await extractApiError(
        new Response(JSON.stringify({ error: { code: 'LICENSE_KEY_NOT_FOUND', message: 'x' } }), {
          status: 404
        })
      )
      expect(onLicenseRequired).not.toHaveBeenCalled()
    })
  })
})

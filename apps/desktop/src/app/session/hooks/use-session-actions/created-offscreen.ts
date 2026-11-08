import type { MutableRefObject } from 'react'

import { requestGatewayForAgent } from '@/store/gateway'
import type { SessionOwnerRoute } from '@/store/session-request-router'
import { releaseSessionOwnerHold } from '@/store/session-states'
import { broadcastSessionsChanged } from '@/store/session-sync'
import type { SessionCreateResponse } from '@/types/hermes'

import type { ClientSessionState } from '../../../types'

import type { SessionCreateOverrides } from './create-overrides'
import { markSessionCreatedThisRun } from './created-this-run'
import { upsertOptimisticSession } from './utils'

interface OffscreenCreate {
  capturedRoute: SessionOwnerRoute | null
  created: SessionCreateResponse
  createIntent: string
  createIntentRef: MutableRefObject<string | null>
  createOverrides?: SessionCreateOverrides
  drift: string
  ensureSessionState: (runtimeId: string, storedId?: string | null) => ClientSessionState
  preview: string | null
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  stored: string | null
  updateSessionState: (
    runtimeId: string,
    updater: (state: ClientSessionState) => ClientSessionState,
    storedId?: string | null
  ) => ClientSessionState
}

/** A submitted create owns work; an abandoned empty New Chat owns nothing. */
export async function finishOffscreenCreate({
  capturedRoute, created, createIntent, createIntentRef, createOverrides, drift,
  ensureSessionState, preview, requestGateway, stored, updateSessionState
}: OffscreenCreate): Promise<string | null> {
  if (createOverrides?.continueAfterNavigation) {
    // Preserve the owner and discoverable row without selecting this chat.
    ensureSessionState(created.session_id, stored)

    if (stored) {
      markSessionCreatedThisRun(stored)
      upsertOptimisticSession(created, stored, null, preview?.trim() || null, null, undefined, capturedRoute)
      createOverrides.onComposerScopeAssigned?.(stored)
      broadcastSessionsChanged()
    }

    if (createIntentRef.current === createIntent) {
      createIntentRef.current = null
    }

    updateSessionState(created.session_id, state => ({ ...state, runtimeStartedAt: Date.now() }), stored)

    return created.session_id
  }

  console.warn('[submit-drift-abort]', drift, { phase: 'mid-create' })

  // Close only on the backend that minted the abandoned empty chat.
  const closing = capturedRoute
    ? requestGatewayForAgent(capturedRoute.connectionId, capturedRoute.profile, 'session.close', {
        session_id: created.session_id
      })
    : requestGateway('session.close', { session_id: created.session_id })

  await closing.catch(() => undefined)

  if (stored) {
    releaseSessionOwnerHold(stored)
  }

  return null
}

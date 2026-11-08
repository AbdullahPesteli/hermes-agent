export interface SessionCreateOverrides {
  /** A submitted first prompt must finish even after its view is deselected. */
  continueAfterNavigation?: boolean
  /** Renderer-only handoff, fired at the stored-id assignment before navigation. */
  onComposerScopeAssigned?: (scope: string) => void
}

export type CreateBackendSessionForSend = (
  preview?: string | null,
  createOverrides?: SessionCreateOverrides
) => Promise<string | null>

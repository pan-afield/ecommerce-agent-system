export const AUTH_SESSION_EXPIRED_EVENT = "auth:session-expired";

let refreshInFlight: Promise<boolean> | null = null;
let sessionGeneration = 0;
let logoutInFlight: Promise<void> | null = null;

function announceExpiredSession() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
  }
}

function clearBrowserSession() {
  if (logoutInFlight === null) {
    const currentLogout = fetch("/api/auth/logout", {
      method: "POST",
      cache: "no-store",
    })
      .catch(() => undefined)
      .then(() => announceExpiredSession());
    logoutInFlight = currentLogout;
    void currentLogout.finally(() => {
      if (logoutInFlight === currentLogout) {
        logoutInFlight = null;
      }
    });
  }

  return logoutInFlight;
}

async function refreshAccessToken() {
  try {
    const response = await fetch("/api/auth/refresh", {
      method: "POST",
      cache: "no-store",
    });
    if (response.ok) {
      sessionGeneration += 1;
      return true;
    }
  } catch {
    // The session-expired state below is intentionally shared with HTTP failures.
  }

  announceExpiredSession();
  return false;
}

function getSharedRefresh() {
  if (refreshInFlight === null) {
    const currentRefresh = refreshAccessToken();
    refreshInFlight = currentRefresh;
    void currentRefresh.finally(() => {
      if (refreshInFlight === currentRefresh) {
        refreshInFlight = null;
      }
    });
  }

  return refreshInFlight;
}

export async function authenticatedFetch(
  input: string,
  init?: RequestInit,
): Promise<Response> {
  const requestGeneration = sessionGeneration;
  const response = await fetch(input, init);
  if (response.status !== 401) {
    return response;
  }

  if (requestGeneration !== sessionGeneration) {
    const retriedResponse = await fetch(input, init);
    if (retriedResponse.status === 401) {
      await clearBrowserSession();
    }
    return retriedResponse;
  }

  const refreshed = await getSharedRefresh();
  if (!refreshed) {
    return response;
  }

  const retriedResponse = await fetch(input, init);
  if (retriedResponse.status === 401) {
    await clearBrowserSession();
  }
  return retriedResponse;
}

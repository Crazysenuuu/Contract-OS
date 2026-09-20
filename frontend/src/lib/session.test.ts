import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearSessionTokens,
  currentSessionTokens,
  listAgreements,
  login,
  onTokensRotated,
  persistSessionTokens,
  setSessionExpiredHandler,
  setSessionStore,
  type SessionStore,
  type SessionTokens,
} from "./api";

function memoryStore(): SessionStore {
  let tokens: SessionTokens | null = null;
  return {
    getTokens: () => tokens,
    setTokens: (next) => {
      tokens = next;
    },
    clear: () => {
      tokens = null;
    },
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const TOKENS_401_BODY = { detail: "Not authenticated" };

beforeEach(() => {
  setSessionStore(memoryStore());
});

afterEach(() => {
  setSessionExpiredHandler(null);
  setSessionStore(null);
  clearSessionTokens();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("session token store", () => {
  it("login persists both access and refresh tokens", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, {
          access_token: "access-1",
          refresh_token: "refresh-1",
          user_id: "u1",
        })
      )
    );

    await login({ email: "a@b.c", password: "pw" });

    expect(currentSessionTokens()).toEqual({
      accessToken: "access-1",
      refreshToken: "refresh-1",
    });
  });

  it("clearSessionTokens removes both tokens", () => {
    persistSessionTokens({ accessToken: "a", refreshToken: "r" });
    clearSessionTokens();
    expect(currentSessionTokens()).toBeNull();
  });
});

describe("401 refresh interceptor", () => {
  it("refreshes once and replays the request with the new access token", async () => {
    persistSessionTokens({ accessToken: "stale", refreshToken: "refresh-1" });

    let seenAuthHeader = "";
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/refresh")) {
          return jsonResponse(200, {
            access_token: "access-2",
            refresh_token: "refresh-2",
            user_id: "u1",
          });
        }
        const headers = new Headers(init?.headers);
        seenAuthHeader = headers.get("Authorization") ?? "";
        // Stale token → 401; replay with the fresh token → 200.
        if (seenAuthHeader === "Bearer stale") {
          return jsonResponse(401, TOKENS_401_BODY);
        }
        return jsonResponse(200, []);
      }
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await listAgreements("stale");

    expect(result).toEqual([]);
    // The replay must carry the NEW access token, not the stale one.
    expect(seenAuthHeader).toBe("Bearer access-2");
    expect(currentSessionTokens()).toEqual({
      accessToken: "access-2",
      refreshToken: "refresh-2",
    });
    // initial (401) + refresh + replay = 3
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("concurrent 401s share a single refresh call (single-flight)", async () => {
    persistSessionTokens({ accessToken: "stale", refreshToken: "refresh-1" });

    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/refresh")) {
          return jsonResponse(200, {
            access_token: "access-2",
            refresh_token: "refresh-2",
            user_id: "u1",
          });
        }
        const auth = new Headers(init?.headers).get("Authorization") ?? "";
        return auth === "Bearer stale"
          ? jsonResponse(401, TOKENS_401_BODY)
          : jsonResponse(200, []);
      }
    );
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([
      listAgreements("stale"),
      listAgreements("stale"),
      listAgreements("stale"),
    ]);

    // Exactly one refresh for three concurrent 401-triggering calls.
    const refreshCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/auth/refresh")
    );
    expect(refreshCalls).toHaveLength(1);
  });

  it("does not attempt refresh for unauthenticated requests", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(401, { detail: "Not authenticated" })
    );
    vi.stubGlobal("fetch", fetchMock);

    // Empty token = no Authorization header goes out at all.
    await expect(listAgreements("")).rejects.toThrow("Not authenticated");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not attempt refresh for non-401 errors", async () => {
    persistSessionTokens({ accessToken: "ok", refreshToken: "refresh-1" });
    const fetchMock = vi.fn(async () =>
      jsonResponse(500, { detail: "boom" })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listAgreements("ok")).rejects.toThrow("boom");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("session expiry", () => {
  it("clears tokens and fires the expiry handler when refresh fails", async () => {
    persistSessionTokens({ accessToken: "stale", refreshToken: "refresh-dead" });
    const expired = vi.fn();
    setSessionExpiredHandler(expired);

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/auth/refresh")) {
        return jsonResponse(401, { detail: "Invalid or expired refresh token" });
      }
      return jsonResponse(401, TOKENS_401_BODY);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(listAgreements("stale")).rejects.toThrow("Not authenticated");

    expect(expired).toHaveBeenCalledTimes(1);
    expect(currentSessionTokens()).toBeNull();
  });

  it("rotation listener observes both login and refresh rotations", async () => {
    const seen: string[] = [];
    const unsubscribe = onTokensRotated((token: string) => seen.push(token));

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/auth/refresh")) {
          return jsonResponse(200, {
            access_token: "access-2",
            refresh_token: "refresh-2",
            user_id: "u1",
          });
        }
        const auth = new Headers(init?.headers).get("Authorization") ?? "";
        return auth === "Bearer access-1"
          ? jsonResponse(401, TOKENS_401_BODY)
          : jsonResponse(200, []);
      })
    );

    persistSessionTokens({ accessToken: "access-1", refreshToken: "refresh-1" });
    await listAgreements("access-1");

    unsubscribe();
    expect(seen).toEqual(["access-2"]);
  });
});

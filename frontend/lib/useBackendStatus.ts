"use client";

import { useEffect, useState } from "react";

export type BackendStatus = "checking" | "awake" | "asleep";

/**
 * Is the backend up?
 *
 * For this deployment "down" is the *normal* state, not an incident: the EC2
 * instance is stopped between demos to stay inside a free tier. So the answer
 * to this question decides whether the app is usable at all, and every surface
 * that can ask a question has to handle "asleep" as a designed state rather
 * than an error.
 *
 * A stopped instance does not refuse connections quickly — the request hangs
 * until something upstream gives up, which can be tens of seconds. The abort
 * timeout is what turns that into a fast, definite answer.
 */
export function useBackendStatus(timeoutMs = 6000): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let active = true;

    fetch("/api/health", { signal: controller.signal, cache: "no-store" })
      .then((response) => {
        if (active) setStatus(response.ok ? "awake" : "asleep");
      })
      .catch(() => {
        // Abort, network failure and DNS failure are the same fact here:
        // nothing is going to answer a question.
        if (active) setStatus("asleep");
      })
      .finally(() => clearTimeout(timer));

    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [timeoutMs]);

  return status;
}

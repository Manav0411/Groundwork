"use client";

import { useEffect, useState } from "react";
import { useBackendStatus } from "@/lib/useBackendStatus";
import Link from "next/link";

const SECTIONS = [
  { id: "live", label: "Watch it work" },
  { id: "routing", label: "Mechanisms" },
  { id: "pipeline", label: "Pipeline" },
  { id: "measured", label: "Measured" },
  { id: "bounds", label: "Boundaries" }
] as const;

/**
 * The persistent way into the product.
 *
 * The status dot is not decoration: this backend is stopped between demos, so
 * whether a visitor is about to land on a working app or an explained empty
 * one is worth knowing *before* they click. It reuses the grade colours, which
 * are the only non-provenance colours in the system.
 */
export function SiteNav() {
  const backend = useBackendStatus();
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const targets = SECTIONS.map((section) => document.getElementById(section.id)).filter(
      (node): node is HTMLElement => node !== null
    );
    if (targets.length === 0) return;

    // Flip at the middle of the viewport rather than at its edge, so the
    // highlight matches what the reader is actually looking at.
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setCurrent(entry.target.id);
        });
      },
      { rootMargin: "-45% 0px -50% 0px" }
    );

    targets.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, []);

  return (
    <nav
      aria-label="Sections"
      className="fixed left-1/2 top-3.5 z-50 flex w-[calc(100%-28px)] max-w-[1280px] -translate-x-1/2 flex-wrap items-center gap-3.5 border-2 border-ink bg-card py-2 pl-4 pr-2 shadow-mountSm"
    >
      <span className="mr-auto flex items-center gap-2.5">
        <span className="font-sans text-base font-bold tracking-tight">Groundwork</span>
        <span
          aria-label={
            backend === "awake"
              ? "Backend live"
              : backend === "asleep"
                ? "Backend asleep"
                : "Checking backend"
          }
          className={`h-2 w-2 rounded-full ${
            backend === "awake" ? "bg-ok" : backend === "asleep" ? "bg-bad" : "animate-pulse bg-rule"
          }`}
          title={
            backend === "asleep"
              ? "The demo backend is stopped between sessions"
              : backend === "awake"
                ? "Backend is live"
                : "Checking"
          }
        />
      </span>

      <div className="hidden gap-0.5 md:flex">
        {SECTIONS.map((section) => (
          <a
            aria-current={current === section.id ? "true" : undefined}
            className={`border px-2.5 py-2 font-mono text-[10.5px] font-semibold uppercase tracking-[0.11em] no-underline transition ${
              current === section.id
                ? "border-ink text-ink"
                : "border-transparent text-ink2 hover:border-rule hover:text-ink"
            }`}
            href={`#${section.id}`}
            key={section.id}
          >
            {section.label}
          </a>
        ))}
      </div>

      <Link
        className="border-2 border-ink bg-ink px-4 py-2.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.13em] text-card no-underline transition hover:bg-card hover:text-ink"
        href="/app"
      >
        Open the app
      </Link>
    </nav>
  );
}

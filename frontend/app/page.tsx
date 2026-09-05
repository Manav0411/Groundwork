import { Boundaries } from "@/components/landing/Boundaries";
import { Close } from "@/components/landing/Close";
import { ContourField } from "@/components/landing/ContourField";
import { Hero } from "@/components/landing/Hero";
import { Measured } from "@/components/landing/Measured";
import { Routing } from "@/components/landing/Routing";
import { SiteNav } from "@/components/landing/SiteNav";
import { Stages } from "@/components/landing/Stages";
import { WatchItWork } from "@/components/landing/WatchItWork";

/**
 * The landing page.
 *
 * Everything except the contour canvas and the nav is a server component, so
 * the page prerenders as static HTML: the section reveals are CSS animations
 * with per-item delays rather than effects, and the one answer on the page
 * comes from a committed fixture rather than a fetch.
 */
export default function Home() {
  return (
    <div className="surface-board relative min-h-screen">
      <ContourField />
      <SiteNav />
      <div className="relative z-10 mx-auto max-w-[1280px] px-[clamp(18px,4.5vw,64px)]">
        <Hero />
        <WatchItWork />
        <Routing />
        <Stages />
        <Measured />
        <Boundaries />
        <Close />
        <footer className="flex flex-wrap justify-between gap-x-6 gap-y-2.5 border-t-2 border-ink pb-8 pt-4 font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em] text-ink3">
          <span>Groundwork · self-hosted, read-only, free-first</span>
          {/* The only outbound link on the page. Every other href goes to /app or an
              anchor, so a reader persuaded by "every number here has a file you can
              run" previously had no way to reach one. */}
          <span>
            Built by Manav Goel ·{" "}
            <a
              className="underline underline-offset-2 hover:text-ink"
              href="https://github.com/Manav0411"
              rel="noreferrer noopener"
              target="_blank"
            >
              @Manav0411 ↗
            </a>
          </span>
        </footer>
      </div>
    </div>
  );
}

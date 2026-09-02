import Link from "next/link";

export function Close() {
  return (
    <section className="pt-[clamp(56px,7.5vw,104px)]">
      <div className="border-t-2 border-ink pb-[clamp(56px,7.5vw,104px)] pt-[clamp(34px,5vw,60px)]">
        <h2 className="m-0 max-w-[19ch] text-balance font-sans text-[clamp(36px,5.2vw,72px)] font-bold leading-[0.98] tracking-[-0.035em]">
          Point it at a project it has never seen.
        </h2>
        <p className="m-0 mt-5 max-w-[52ch] font-serif text-[clamp(16.5px,1.4vw,19px)] leading-normal text-ink2">
          That is the only test that matters, and it is the one the suite runs. Every other dataset
          here hardcodes its expectations — which guards known behaviour and cannot show that the
          system works on data nobody wrote cases for.
        </p>
        <Link
          className="mt-6 inline-block border-2 border-ink bg-ink px-5 py-3 font-mono text-[11.5px] font-semibold uppercase tracking-[0.13em] text-card no-underline transition hover:bg-card hover:text-ink"
          href="/app"
        >
          Open the app
        </Link>
      </div>
    </section>
  );
}

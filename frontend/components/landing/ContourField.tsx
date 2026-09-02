"use client";

import { useEffect, useRef } from "react";

/**
 * The ambient field behind the first screen.
 *
 * Groundwork is survey work, and what a survey produces is a contour map — so
 * the page sits on slowly shifting contour lines rather than the linked-dot
 * particle field every product site has had since 2015.
 *
 * Each line is a sum of three sines at falling amplitude, which reads as
 * terrain without a noise field or marching squares. Relief concentrates
 * toward the middle so it forms a ridge instead of uniform ripples.
 *
 * Fixed to the window rather than trapped in the hero, so it is the ground the
 * page sits on. It fades across the first screen, and the loop skips drawing
 * entirely once invisible — there is no point spending a frame budget on
 * something at zero opacity.
 */
export function ContourField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let alpha = 1;
    let frame = 0;
    let queued = false;

    function size() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas!.clientWidth;
      height = canvas!.clientHeight;
      canvas!.width = width * dpr;
      canvas!.height = height * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function draw(t: number) {
      ctx!.clearRect(0, 0, width, height);
      ctx!.strokeStyle = "#1A1D19";
      ctx!.lineWidth = 1;

      const lines = 26;
      const step = height / (lines - 5);

      for (let i = 0; i < lines; i++) {
        const mid = 1 - Math.abs(i / (lines - 1) - 0.5) * 2;
        const amp = 16 + mid * 64;
        ctx!.globalAlpha = (0.1 + mid * 0.11) * alpha;
        ctx!.beginPath();
        for (let k = 0; k <= 100; k++) {
          const x = (k / 100) * width;
          const u = x / width;
          const y =
            i * step -
            step * 2.5 +
            Math.sin(u * 3.1 + t * 0.16 + i * 0.42) * amp +
            Math.sin(u * 7.4 - t * 0.11 + i * 0.19) * amp * 0.36 +
            Math.sin(u * 13.7 + t * 0.07) * amp * 0.13;
          if (k === 0) ctx!.moveTo(x, y);
          else ctx!.lineTo(x, y);
        }
        ctx!.stroke();
      }
      ctx!.globalAlpha = 1;
    }

    function fade() {
      queued = false;
      const span = window.innerHeight * 0.85;
      alpha = Math.max(0, Math.min(1, 1 - window.scrollY / span));
      canvas!.style.opacity = String(alpha);
    }

    // One style write per frame. A scroll handler that touches style on every
    // event fires far more often than the compositor can use it.
    function onScroll() {
      if (queued) return;
      queued = true;
      requestAnimationFrame(fade);
    }

    function onResize() {
      size();
      fade();
      if (reduce) draw(0);
    }

    size();
    fade();

    if (reduce) {
      draw(0);
    } else {
      const start = performance.now();
      const loop = (now: number) => {
        if (alpha > 0.001) draw((now - start) / 1000);
        frame = requestAnimationFrame(loop);
      };
      frame = requestAnimationFrame(loop);
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <canvas
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0 h-full w-full"
      ref={ref}
    />
  );
}

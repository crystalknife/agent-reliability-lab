import * as React from "react";

/* A tiny pursuit simulation behind the homepage hero: a tracking robot scans,
   predicts, lunges, misses, recovers, and retries against an evading crawler.
   The sim is pure and deterministic per seed; the SVG view applies snapshots
   imperatively so a 60fps chase costs no React re-renders. */

export const SCENE_W = 420;
export const SCENE_H = 280;
const EDGE = 36;
const DEG = 180 / Math.PI;

export type RobotMode = "scan" | "pursue" | "lunge" | "miss" | "recover" | "tag";

export interface ChaseSnapshot {
  time: number;
  mode: RobotMode;
  attempt: number;
  locked: boolean;
  ring: number; // lunge progress 0..1; -1 hides the ring
  crawler: { x: number; y: number; heading: number; speed: number; gait: number };
  robot: { x: number; y: number; heading: number; sensor: number; speed: number };
}

const STATUS: Record<RobotMode, string> = {
  scan: "SCANNING",
  pursue: "PURSUIT",
  lunge: "INTERCEPT",
  miss: "MISS",
  recover: "RECOVER",
  tag: "CONTACT",
};

const TAU = Math.PI * 2;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const wrap = (a: number) => (((a + Math.PI) % TAU) + TAU) % TAU - Math.PI;
const turn = (cur: number, target: number, step: number) =>
  cur + clamp(wrap(target - cur), -step, step);
const approach = (v: number, target: number, step: number) =>
  v < target ? Math.min(target, v + step) : Math.max(target, v - step);

function makeRng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Steer away from arena walls without ever leaving the frame. */
function clearHeading(x: number, y: number, desired: number) {
  const pad = 58;
  const dx = x < pad ? (pad - x) / pad : x > SCENE_W - pad ? -(x - (SCENE_W - pad)) / pad : 0;
  const dy = y < pad ? (pad - y) / pad : y > SCENE_H - pad ? -(y - (SCENE_H - pad)) / pad : 0;
  if (dx === 0 && dy === 0) return desired;
  const towardCenter = Math.atan2(SCENE_H / 2 - y, SCENE_W / 2 - x);
  return desired + wrap(towardCenter - desired) * Math.min(1, Math.hypot(dx, dy) * 1.4);
}

function roomAt(x: number, y: number) {
  return Math.min(x - EDGE, SCENE_W - EDGE - x, y - EDGE, SCENE_H - EDGE - y);
}

export function createChase(seed = 1) {
  const rnd = makeRng(seed);
  let time = 0;
  let mode: RobotMode = "scan";
  let modeT = 0;
  let attempt = 0;
  let locked = false;
  let ring = -1;

  const crawler = {
    x: 96, y: 92, heading: 0.35, speed: 18, gait: 0,
    wanderT: 1.1, desired: 0.35, evade: false, fleeSide: 1, slipT: 0,
  };
  const robot = {
    x: 318, y: 190, heading: -2.55, sensor: -2.72, speed: 0, lungeHeading: -2.55,
  };

  function step(dtRaw: number) {
    const dt = clamp(dtRaw, 0, 0.05);
    if (dt === 0) return;
    time += dt;
    modeT += dt;

    const dx = crawler.x - robot.x;
    const dy = crawler.y - robot.y;
    const dist = Math.hypot(dx, dy);
    const toCrawler = Math.atan2(dy, dx);
    const cVx = Math.cos(crawler.heading) * crawler.speed;
    const cVy = Math.sin(crawler.heading) * crawler.speed;
    const lead = [0.18, 0.5, 0.9][attempt] ?? 0.5;
    const toIntercept = Math.atan2(crawler.y + cVy * lead - robot.y, crawler.x + cVx * lead - robot.x);

    if (mode === "scan") {
      robot.speed = approach(robot.speed, 0, 140 * dt);
      robot.sensor = turn(
        robot.sensor,
        modeT < 0.75 ? robot.heading + Math.sin(time * 3.1) * 0.62 : toCrawler,
        4.6 * dt,
      );
      locked = modeT > 0.8;
      if (modeT > 1.65) { mode = "pursue"; modeT = 0; locked = true; }
    } else if (mode === "pursue") {
      robot.sensor = turn(robot.sensor, toCrawler, 6 * dt);
      robot.heading = turn(robot.heading, clearHeading(robot.x, robot.y, toIntercept), 2.8 * dt);
      robot.speed = approach(robot.speed, 104, 170 * dt);
      if (dist < 74) {
        mode = "lunge"; modeT = 0; ring = 0;
        robot.lungeHeading = toIntercept;
      }
    } else if (mode === "lunge") {
      ring = Math.min(1, modeT / 0.7);
      const contact = attempt === 2;
      robot.sensor = turn(robot.sensor, toCrawler, 8 * dt);
      if (contact) {
        robot.heading = turn(robot.heading, toCrawler, 3.6 * dt);
        robot.speed = approach(robot.speed, 164, 280 * dt);
        if (dist < 28 && modeT > 0.2) { mode = "tag"; modeT = 0; ring = -1; }
      } else {
        robot.heading = turn(robot.heading, robot.lungeHeading, 1.1 * dt);
        robot.speed = approach(robot.speed, 138, 240 * dt);
      }
      if (mode === "lunge" && modeT > 0.7) {
        mode = "miss"; modeT = 0; ring = -1;
        attempt = (attempt + 1) % 3;
      }
    } else if (mode === "miss") {
      robot.speed = approach(robot.speed, 0, 300 * dt);
      robot.sensor = turn(robot.sensor, toCrawler, 5 * dt);
      if (modeT > 0.5) { mode = "recover"; modeT = 0; }
    } else if (mode === "tag") {
      robot.speed = approach(robot.speed, 0, 400 * dt);
      robot.sensor = turn(robot.sensor, toCrawler, 8 * dt);
      if (modeT > 0.7) {
        mode = "recover"; modeT = 0;
        attempt = (attempt + 1) % 3;
        crawler.slipT = 1.15;
        crawler.desired = toCrawler + (rnd() - 0.5) * 0.7;
        crawler.speed = 80;
      }
    } else {
      robot.speed = approach(robot.speed, 0, 340 * dt);
      robot.sensor = turn(robot.sensor, toCrawler + Math.sin(time * 4.4) * 0.45, 4.4 * dt);
      if (modeT > 1.2) { mode = "scan"; modeT = 0; locked = false; }
    }

    robot.sensor = robot.heading + wrap(robot.sensor - robot.heading);
    robot.heading = wrap(robot.heading);
    robot.x = clamp(robot.x + Math.cos(robot.heading) * robot.speed * dt, EDGE, SCENE_W - EDGE);
    robot.y = clamp(robot.y + Math.sin(robot.heading) * robot.speed * dt, EDGE, SCENE_H - EDGE);

    if (mode === "tag") {
      crawler.speed = approach(crawler.speed, 0, 600 * dt);
    } else if (crawler.slipT > 0) {
      crawler.slipT -= dt;
      crawler.desired = clearHeading(crawler.x, crawler.y, crawler.desired);
      crawler.heading = turn(crawler.heading, crawler.desired, 4.6 * dt);
      crawler.speed = approach(crawler.speed, 110, 260 * dt);
    } else {
      const pursued = mode === "pursue" || mode === "lunge";
      if (pursued && dist < 98) {
        if (!crawler.evade) {
          crawler.evade = true;
          const left = toCrawler + 1.15;
          const right = toCrawler - 1.15;
          const roomL = roomAt(crawler.x + Math.cos(left) * 80, crawler.y + Math.sin(left) * 80);
          const roomR = roomAt(crawler.x + Math.cos(right) * 80, crawler.y + Math.sin(right) * 80);
          crawler.fleeSide = roomL >= roomR ? 1 : -1;
        }
        crawler.desired = clearHeading(crawler.x, crawler.y, toCrawler + crawler.fleeSide * 1.05);
        crawler.speed = approach(crawler.speed, mode === "lunge" && attempt !== 2 ? 104 : 84, 220 * dt);
        crawler.heading = turn(crawler.heading, crawler.desired, 4.6 * dt);
        crawler.wanderT = 0.8 + rnd() * 0.8;
      } else {
        crawler.evade = false;
        crawler.wanderT -= dt;
        if (crawler.wanderT <= 0) {
          crawler.wanderT = 1.2 + rnd() * 1.3;
          crawler.desired = crawler.heading + (rnd() < 0.22 ? 0 : (rnd() - 0.5) * 1.5);
        }
        crawler.desired = clearHeading(crawler.x, crawler.y, crawler.desired);
        crawler.speed = approach(crawler.speed, 20, 80 * dt);
        crawler.heading = turn(crawler.heading, crawler.desired, 2.4 * dt);
      }
      crawler.gait += crawler.speed * dt * 0.42;
      crawler.heading = wrap(crawler.heading);
      crawler.x = clamp(crawler.x + Math.cos(crawler.heading) * crawler.speed * dt, EDGE, SCENE_W - EDGE);
      crawler.y = clamp(crawler.y + Math.sin(crawler.heading) * crawler.speed * dt, EDGE, SCENE_H - EDGE);
    }
  }

  function snapshot(): ChaseSnapshot {
    return { time, mode, attempt, locked, ring, crawler: { ...crawler }, robot: { ...robot } };
  }

  return { step, snapshot };
}

/* ---------- view helpers (shared by initial render and the rAF loop) ---------- */

function crawlerTransform(s: ChaseSnapshot) {
  const jitter = s.mode === "tag" ? Math.sin(s.time * 52) * 2 : 0;
  const bob = Math.sin(s.crawler.gait * 2) * 0.7 * Math.min(1, s.crawler.speed / 40);
  return `translate(${(s.crawler.x + jitter).toFixed(1)} ${s.crawler.y.toFixed(1)}) rotate(${
    (s.crawler.heading * DEG).toFixed(1)
  }) scale(1.28) translate(0 ${bob.toFixed(2)})`;
}

function robotTransform(s: ChaseSnapshot) {
  return `translate(${s.robot.x.toFixed(1)} ${s.robot.y.toFixed(1)}) rotate(${
    (s.robot.heading * DEG).toFixed(1)
  }) scale(1.22)`;
}

function beamTransform(s: ChaseSnapshot) {
  const rel = wrap(s.robot.sensor - s.robot.heading) * DEG;
  return `translate(${s.robot.x.toFixed(1)} ${s.robot.y.toFixed(1)}) rotate(${
    (s.robot.heading * DEG).toFixed(1)
  }) translate(13.4 0) rotate(${rel.toFixed(1)})`;
}

function crawlerLegs(s: ChaseSnapshot): string[] {
  const amp = Math.min(1, s.crawler.speed / 55);
  const paths: string[] = [];
  for (const side of [-1, 1]) {
    for (let i = 0; i < 4; i++) {
      const hipX = [7, 2, -3, -8][i];
      const footX = [14, 5, -6, -14][i];
      const phase = ((i + (side > 0 ? 0 : 1)) % 2) * Math.PI;
      const stride = Math.sin(s.crawler.gait + phase) * 4.6 * amp;
      paths.push(
        `M${hipX} ${side * 4} L${(hipX + (footX - hipX) * 0.45 + stride * 0.45).toFixed(1)} ${
          side * 12
        } L${(footX + stride).toFixed(1)} ${side * 19}`,
      );
    }
  }
  return paths;
}

const INITIAL = createChase(11).snapshot();

/* ---------- React component ---------- */

function usePrefersReducedMotion() {
  const [reduced, setReduced] = React.useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  React.useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

export function HeroChase() {
  const reduced = usePrefersReducedMotion();
  const seed = React.useRef(1 + Math.floor(Math.random() * 9999)).current;
  const crawlerG = React.useRef<SVGGElement>(null);
  const robotG = React.useRef<SVGGElement>(null);
  const beamG = React.useRef<SVGGElement>(null);
  const beamCone = React.useRef<SVGPolygonElement>(null);
  const trackLine = React.useRef<SVGLineElement>(null);
  const reticle = React.useRef<SVGGElement>(null);
  const ringEl = React.useRef<SVGCircleElement>(null);
  const statusEl = React.useRef<SVGTextElement>(null);
  const statusDot = React.useRef<SVGRectElement>(null);
  const lens = React.useRef<SVGCircleElement>(null);
  const legs = React.useRef<(SVGPathElement | null)[]>([]);

  const apply = React.useCallback((s: ChaseSnapshot) => {
    crawlerG.current?.setAttribute("transform", crawlerTransform(s));
    crawlerLegs(s).forEach((d, i) => legs.current[i]?.setAttribute("d", d));
    robotG.current?.setAttribute("transform", robotTransform(s));
    beamG.current?.setAttribute("transform", beamTransform(s));

    const pulse = 0.5 + 0.5 * Math.sin(s.time * 5.2);
    lens.current?.setAttribute("opacity", (0.55 + 0.4 * pulse).toFixed(2));
    const beamBase = s.mode === "scan" || s.mode === "recover" ? 0.08 : 0.13;
    beamCone.current?.setAttribute("opacity", (beamBase + 0.035 * pulse).toFixed(3));

    const showTrack = s.locked && s.mode !== "miss";
    if (trackLine.current) {
      trackLine.current.setAttribute("x1", s.robot.x.toFixed(1));
      trackLine.current.setAttribute("y1", s.robot.y.toFixed(1));
      trackLine.current.setAttribute("x2", s.crawler.x.toFixed(1));
      trackLine.current.setAttribute("y2", s.crawler.y.toFixed(1));
      trackLine.current.setAttribute("opacity", showTrack ? "0.32" : "0");
      trackLine.current.setAttribute("stroke-dashoffset", (-s.time * 22).toFixed(1));
    }
    if (reticle.current) {
      const scale = 1 + 0.06 * pulse + (s.mode === "tag" ? 0.08 : 0);
      reticle.current.setAttribute(
        "transform",
        `translate(${s.crawler.x.toFixed(1)} ${s.crawler.y.toFixed(1)}) scale(${scale.toFixed(3)})`,
      );
      reticle.current.setAttribute("opacity", showTrack && s.mode !== "tag" ? "1" : "0");
    }
    if (ringEl.current) {
      if (s.mode === "tag") {
        ringEl.current.setAttribute("cx", s.crawler.x.toFixed(1));
        ringEl.current.setAttribute("cy", s.crawler.y.toFixed(1));
        ringEl.current.setAttribute("r", (40 + pulse * 2).toFixed(1));
        ringEl.current.setAttribute("opacity", "0.85");
      } else if (s.ring >= 0) {
        ringEl.current.setAttribute("cx", s.crawler.x.toFixed(1));
        ringEl.current.setAttribute("cy", s.crawler.y.toFixed(1));
        ringEl.current.setAttribute("r", (14 + s.ring * 44).toFixed(1));
        ringEl.current.setAttribute("opacity", (0.85 * (1 - s.ring)).toFixed(3));
      } else {
        ringEl.current.setAttribute("opacity", "0");
      }
    }
    if (statusEl.current) statusEl.current.textContent = STATUS[s.mode];
    statusDot.current?.setAttribute(
      "fill", s.locked ? "var(--color-signal)" : "var(--color-ink-faint)",
    );
  }, []);

  React.useEffect(() => {
    if (reduced) {
      const still = createChase(11);
      for (let i = 0; i < 80; i++) still.step(1 / 60);
      apply(still.snapshot());
      return;
    }
    const sim = createChase(seed);
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      sim.step((now - last) / 1000);
      last = now;
      apply(sim.snapshot());
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [apply, reduced, seed]);

  return (
    <div className="card overflow-hidden">
      <svg
        viewBox={`0 0 ${SCENE_W} ${SCENE_H}`}
        className="block h-auto w-full"
        role="img"
        aria-label="Continuous scene: a small tracking robot scans for a mechanical crawler spider, pursues it, misses, recovers, and retries. Capture is never guaranteed."
      >
        <defs>
          <pattern id="hero-chase-grid" width="21" height="21" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="0.9" fill="var(--color-ink-faint)" opacity="0.22" />
          </pattern>
        </defs>
        <rect width={SCENE_W} height={SCENE_H} fill="var(--color-card)" />
        <rect width={SCENE_W} height={SCENE_H} fill="url(#hero-chase-grid)" />
        <g stroke="var(--color-ink-faint)" strokeWidth="1" opacity="0.55" aria-hidden="true">
          <path d="M18 18h9M18 18v9" />
          <path d={`M${SCENE_W - 18} 18h-9M${SCENE_W - 18} 18v9`} />
          <path d={`M18 ${SCENE_H - 18}h9M18 ${SCENE_H - 18}v-9`} />
          <path d={`M${SCENE_W - 18} ${SCENE_H - 18}h-9M${SCENE_W - 18} ${SCENE_H - 18}v-9`} />
        </g>

        <g transform="translate(16 24)" aria-hidden="true">
          <rect ref={statusDot} x={-4} y={-4} width={7} height={7} fill="var(--color-ink-faint)" />
          <text
            ref={statusEl}
            x={11}
            y={3}
            fill="var(--color-ink-soft)"
            fontFamily="var(--font-mono)"
            fontSize={9}
            letterSpacing="0.14em"
          >
            {STATUS[INITIAL.mode]}
          </text>
        </g>

        <g ref={beamG} transform={beamTransform(INITIAL)} aria-hidden="true">
          <polygon
            ref={beamCone}
            points="0,0 118,-21 118,21"
            fill="var(--color-signal)"
            opacity="0.12"
          />
          <line x1="0" y1="0" x2="118" y2="0" stroke="var(--color-signal)" strokeWidth="1" opacity="0.5" />
        </g>

        <line
          ref={trackLine}
          x1="0"
          y1="0"
          x2="0"
          y2="0"
          stroke="var(--color-signal)"
          strokeWidth="1.2"
          strokeDasharray="2 6"
          opacity="0"
        />

        <g ref={reticle} transform={`translate(${INITIAL.crawler.x} ${INITIAL.crawler.y})`} opacity="0">
          <g fill="none" stroke="var(--color-signal)" strokeWidth="1.6" strokeLinecap="round">
            <path d="M-23 -16v-7h7" />
            <path d="M23 -16v-7h-7" />
            <path d="M-23 16v7h7" />
            <path d="M23 16v7h-7" />
          </g>
          <circle r="1.8" fill="var(--color-signal)" />
        </g>

        <g ref={crawlerG} transform={crawlerTransform(INITIAL)}>
          <ellipse cx="-1" cy="3" rx="17" ry="11" fill="var(--color-ink)" opacity="0.06" />
          {crawlerLegs(INITIAL).map((d, i) => (
            <path
              key={i}
              ref={(el) => { legs.current[i] = el; }}
              d={d}
              fill="none"
              stroke="var(--color-ink)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}
          <circle cx="-7" cy="0" r="9.5" fill="var(--color-card)" stroke="var(--color-ink)" strokeWidth="2" />
          <path d="M-13 -3.5h6M-13 3.5h6" stroke="var(--color-ink-faint)" strokeWidth="1.4" strokeLinecap="round" />
          <circle cx="5" cy="0" r="6.4" fill="var(--color-card)" stroke="var(--color-ink)" strokeWidth="2" />
          <circle cx="7.6" cy="-2" r="1.3" fill="var(--color-signal)" />
          <circle cx="7.6" cy="2" r="1.3" fill="var(--color-signal)" />
        </g>

        <g ref={robotG} transform={robotTransform(INITIAL)}>
          <ellipse cx="0" cy="2" rx="20" ry="15" fill="var(--color-ink)" opacity="0.06" />
          <rect x="-18" y="-15.5" width="36" height="8" rx="4" fill="var(--color-ink)" />
          <rect x="-18" y="7.5" width="36" height="8" rx="4" fill="var(--color-ink)" />
          <g stroke="var(--color-card)" strokeWidth="1.2" strokeLinecap="round" opacity="0.55">
            <path d="M-13 -11.5h4M-4 -11.5h4M5 -11.5h4" />
            <path d="M-13 11.5h4M-4 11.5h4M5 11.5h4" />
          </g>
          <rect x="-14" y="-10" width="30" height="20" rx="5" fill="var(--color-card)" stroke="var(--color-ink)" strokeWidth="2" />
          <path d="M-9 -2h10" stroke="var(--color-ink-faint)" strokeWidth="1.4" strokeLinecap="round" />
          <line x1="-11" y1="-10" x2="-14" y2="-17" stroke="var(--color-ink)" strokeWidth="2" strokeLinecap="round" />
          <circle cx="-14" cy="-18" r="2" fill="var(--color-signal)" />
          <circle cx="11" cy="0" r="5.6" fill="var(--color-card)" stroke="var(--color-ink)" strokeWidth="2" />
          <circle ref={lens} cx="11" cy="0" r="2.5" fill="var(--color-signal)" opacity="0.8" />
        </g>

        <circle
          ref={ringEl}
          cx="0"
          cy="0"
          r="12"
          fill="none"
          stroke="var(--color-signal)"
          strokeWidth="1.8"
          strokeDasharray="4 5"
          opacity="0"
        />
      </svg>
    </div>
  );
}

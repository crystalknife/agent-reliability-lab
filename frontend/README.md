# MiniLab Frontend — observability over experiment artifacts

Read-only React + TypeScript + Vite + Tailwind v4 + Motion + Lenis.
Never writes to `../results`; never redefines benchmark semantics
(see `src/lib/selectors.ts` header — Python `minilab.metrics` is authoritative).

## Develop

```powershell
npm install
npm run data        # export ../results JSONL -> public/data (requires local artifacts)
npm run dev         # http://localhost:5173
npm test            # vitest: selectors, routing/navigation, pursuit simulation
npm run build       # tsc + vite build -> dist/
```

Committed exports under `public/data/` power the deployed site without Python
or the raw artifacts; `npm run data` regenerates them when the artifacts are
available locally. Current exports cover EXP-001 through EXP-004 (nine arms).

## Routes

`/` overview homepage · `/dashboard` default experiment dashboard ·
`/experiments` list · `/experiments/:id` detail ·
`/experiments/:id/tasks/:taskId` · `/experiments/:id/trials` ·
`/experiments/:id/trials/:runId` trajectory inspector. Unknown routes fall
back to the dashboard.

## Homepage

The landing page describes the project and shows live aggregate counts from
the exported manifest. Its hero is a self-contained SVG pursuit simulation
(`src/components/HeroChase.tsx`) with no added runtime dependency;
reduced-motion users get a static scene.

## Vendored motion primitives

`src/components/anim/collapsible.tsx` is adapted from Animate UI
(`MIT + Commons Clause`; Radix + motion/react pattern). Tabs/tooltip/badge
are hand-rolled motion components following the same pattern — Animate UI's
own tabs/tooltip were verified as plain Radix without motion, so vendoring
them added nothing.

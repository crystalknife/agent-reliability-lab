import { describe, expect, it } from "vitest";
import { SCENE_H, SCENE_W, createChase } from "../HeroChase";

describe("hero chase simulation", () => {
  it("cycles observe, pursue, evade, miss, recover, and a brief contact in bounds", () => {
    let finite = true;
    let inBounds = true;
    let maxCrawlerSpeed = 0;

    for (const seed of [1, 2, 3, 4, 5]) {
      const sim = createChase(seed);
      const modes = new Set<string>();
      let tagged = false;
      let missed = false;

      for (let i = 0; i < 60 * 60; i++) {
        sim.step(1 / 60);
        const s = sim.snapshot();
        modes.add(s.mode);
        tagged = tagged || s.mode === "tag";
        missed = missed || s.mode === "miss";
        maxCrawlerSpeed = Math.max(maxCrawlerSpeed, s.crawler.speed);
        for (const body of [s.crawler, s.robot]) {
          finite = finite && Number.isFinite(body.x) && Number.isFinite(body.y);
          inBounds = inBounds && body.x >= 0 && body.x <= SCENE_W && body.y >= 0 && body.y <= SCENE_H;
        }
      }

      for (const mode of ["scan", "pursue", "lunge", "miss", "recover", "tag"]) {
        expect(modes.has(mode)).toBe(true);
      }
      expect(tagged).toBe(true);
      expect(missed).toBe(true);
    }

    expect(finite).toBe(true);
    expect(inBounds).toBe(true);
    expect(maxCrawlerSpeed).toBeGreaterThan(70);
  });
});

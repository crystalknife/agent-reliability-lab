import * as React from "react";
import { ReactLenis } from "lenis/react";

/**
 * Lenis smooth-scroll root. Not mounted at all when the user prefers reduced
 * motion (Lenis also respects it internally) or on coarse-pointer devices,
 * where native scrolling stays more responsive. Lenis is a feel layer only:
 * no parallax, no snap, no scroll hijacking.
 */
export function SmoothRoot({ children }: { children: React.ReactNode }) {
  const [native] = React.useState(
    () =>
      typeof window !== "undefined" &&
      (window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
        window.matchMedia("(pointer: coarse)").matches),
  );
  if (native) return <>{children}</>;
  return (
    <ReactLenis root options={{ lerp: 0.09 }}>
      {children}
    </ReactLenis>
  );
}

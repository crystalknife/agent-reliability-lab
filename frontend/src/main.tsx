import * as React from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "./theme.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MotionConfig reducedMotion="user">
      <App />
    </MotionConfig>
  </React.StrictMode>,
);

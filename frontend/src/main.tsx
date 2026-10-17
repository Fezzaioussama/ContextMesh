import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./design-system.css";
import "./styles.css";
import "./knowledge.css";
import "./brand.css";

const root = document.getElementById("root");
if (root === null) throw new Error("The application root is missing.");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

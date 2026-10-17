import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./design-system.css";
import "./styles.css";
import "./chat.css";
import "./responsive.css";
import "./knowledge.css";
import "./sources.css";
import "./answers.css";
import "./brand.css";

const root = document.getElementById("root");
if (root === null) throw new Error("The application root is missing.");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

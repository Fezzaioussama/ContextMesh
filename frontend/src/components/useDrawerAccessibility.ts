import { useEffect } from "react";

function focusableElements(sidebar: HTMLElement): HTMLElement[] {
  return [
    ...sidebar.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), summary, a[href], [tabindex="0"]',
    ),
  ];
}

function focusStart(sidebar: HTMLElement) {
  const elements = focusableElements(sidebar);
  if (elements.length > 0) elements[0].focus();
}

function tabDestination(backward: boolean, elements: HTMLElement[]) {
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (backward) return { boundary: first, next: last };
  return { boundary: last, next: first };
}

function trapTab(event: KeyboardEvent, sidebar: HTMLElement) {
  const elements = focusableElements(sidebar);
  if (elements.length === 0) return;
  const target = tabDestination(event.shiftKey, elements);
  if (document.activeElement === target.boundary) {
    event.preventDefault();
    target.next.focus();
  }
}

function handleKey(
  event: KeyboardEvent,
  sidebar: HTMLElement,
  close: () => void,
) {
  if (event.key === "Escape") {
    close();
    return;
  }
  if (event.key === "Tab") trapTab(event, sidebar);
}

export function useDrawerAccessibility(
  open: boolean,
  close: () => void,
  drawerId: string,
) {
  useEffect(() => {
    if (!open) return;
    const sidebar = document.getElementById(drawerId);
    if (sidebar === null) return;
    const previous = document.activeElement;
    focusStart(sidebar);
    const onKeyDown = (event: KeyboardEvent) =>
      handleKey(event, sidebar, close);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [open, close, drawerId]);
}

type IconName =
  | "mesh"
  | "plus"
  | "chat"
  | "arrow"
  | "menu"
  | "close"
  | "spark"
  | "refresh"
  | "library"
  | "upload"
  | "trash"
  | "file";
const paths: Record<IconName, string> = {
  mesh: "M5 4h5v5H5z M14 4h5v5h-5z M5 15h5v5H5z M14 15h5v5h-5z M10 6h4 M7.5 9v6 M16.5 9v6 M10 17h4",
  plus: "M12 5v14 M5 12h14",
  chat: "M4 5h16v12H9l-5 4z",
  arrow: "M12 19V5 M5 12l7-7 7 7",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  close: "M6 6l12 12 M6 18 18 6",
  spark: "M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  refresh: "M20 11a8 8 0 1 0-2 6 M20 4v7h-7",
  library: "M4 5h4v14H4z M10 5h4v14h-4z M16.5 5.5l3.5-1 3 13.5-3.5 1z",
  upload: "M12 16V4 M7 9l5-5 5 5 M5 20h14",
  trash: "M5 7h14 M10 7V4h4v3 M7 7l1 13h8l1-13",
  file: "M6 3h8l4 4v14H6z M14 3v4h4",
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

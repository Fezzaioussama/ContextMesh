export function ErrorNotice({
  message,
  retry,
  label = "Try again",
}: {
  message: string;
  retry: () => void;
  label?: string;
}) {
  if (message.length === 0) return null;
  return (
    <div className="error-notice" role="alert">
      <span>{message}</span>
      <button className="text-button" onClick={retry}>
        {label}
      </button>
    </div>
  );
}

export function Loading({ children }: { children: React.ReactNode }) {
  return (
    <div className="loading" role="status">
      <span className="pulse-dot" />
      {children}
    </div>
  );
}

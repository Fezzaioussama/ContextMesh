import { useState } from "react";
import { describeError } from "../../api/client";

/** Runs one user-initiated mutation at a time and reports a safe error. */
export function useAction<Args extends unknown[]>(
  action: (...args: Args) => Promise<unknown>,
  done: () => void,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function run(...args: Args) {
    setPending(true);
    setError("");
    try {
      await action(...args);
      done();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  return { pending, error, run, clear: () => setError("") };
}

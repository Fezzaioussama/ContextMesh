import { useState } from "react";
import type { FormEvent } from "react";
import { createSource } from "../../api/knowledge";
import { ErrorNotice } from "../../components/Feedback";
import { Icon } from "../../components/Icon";
import { useAction } from "./useAction";

export function CreateSourceForm({ created }: { created: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const action = useAction(
    (value: string, about: string) =>
      createSource(value, about, new AbortController().signal),
    () => {
      setName("");
      setDescription("");
      created();
    },
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim().length > 0) void action.run(name.trim(), description.trim());
  }

  return (
    <form className="source-form" onSubmit={submit}>
      <label>
        Source name
        <input
          value={name}
          maxLength={100}
          placeholder="e.g. Architecture decisions"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        Description <span>(helps the agent choose sources)</span>
        <input
          value={description}
          maxLength={500}
          placeholder="What these documents cover"
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <button
        className="primary-button"
        type="submit"
        disabled={action.pending || name.trim().length === 0}
      >
        <Icon name="plus" />
        Add source
      </button>
      <ErrorNotice message={action.error} retry={action.clear} label="Dismiss" />
    </form>
  );
}

import { useState } from "react";
import type { FormEvent } from "react";
import { createSource, syncSource } from "../../api/knowledge";
import type { SourceRequest } from "../../api/knowledge";
import { ErrorNotice } from "../../components/Feedback";
import { Icon } from "../../components/Icon";
import { useAction } from "./useAction";

type Kind = "upload" | "website";

/** Websites are registered, then crawled at once; files are uploaded afterwards. */
async function register(request: SourceRequest) {
  const source = await createSource(request);
  if (source.kind === "website") await syncSource(source.id);
}

function request(kind: Kind, name: string, description: string, url: string): SourceRequest {
  return { name: name.trim(), description: description.trim(), url: kind === "website" ? url.trim() : null };
}

function complete(kind: Kind, name: string, url: string): boolean {
  if (name.trim().length === 0) return false;
  return kind === "upload" || url.trim().length > 0;
}

export function CreateSourceForm({ created }: { created: () => void }) {
  const [kind, setKind] = useState<Kind>("upload");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const action = useAction(register, () => {
    setName("");
    setDescription("");
    setUrl("");
    created();
  });
  const ready = complete(kind, name, url);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (ready) void action.run(request(kind, name, description, url));
  }

  return (
    <form className="source-form" onSubmit={submit}>
      <fieldset className="kind-choice">
        <legend>Source type</legend>
        <KindOption value="upload" kind={kind} choose={setKind} label="Files" />
        <KindOption value="website" kind={kind} choose={setKind} label="Website" />
      </fieldset>
      <label>
        Source name
        <input
          value={name}
          maxLength={100}
          placeholder="e.g. Architecture decisions"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <WebsiteField visible={kind === "website"} url={url} change={setUrl} />
      <label>
        Description <span>(helps the agent choose sources)</span>
        <input
          value={description}
          maxLength={500}
          placeholder="What these documents cover"
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <button className="primary-button" type="submit" disabled={action.pending || !ready}>
        <Icon name="plus" />
        Add source
      </button>
      <ErrorNotice message={action.error} retry={action.clear} label="Dismiss" />
    </form>
  );
}

function KindOption(props: {
  value: Kind;
  kind: Kind;
  choose: (kind: Kind) => void;
  label: string;
}) {
  return (
    <label className="kind-option">
      <input
        type="radio"
        name="source-kind"
        checked={props.kind === props.value}
        onChange={() => props.choose(props.value)}
      />
      {props.label}
    </label>
  );
}

function WebsiteField(props: { visible: boolean; url: string; change: (url: string) => void }) {
  if (!props.visible) return null;
  return (
    <label>
      Start page URL <span>(pages under this path are crawled)</span>
      <input
        type="url"
        value={props.url}
        maxLength={2000}
        placeholder="https://docs.example.com/guide/"
        onChange={(event) => props.change(event.target.value)}
      />
    </label>
  );
}

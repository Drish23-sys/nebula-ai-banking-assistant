import { useState } from "react";

export default function ChatInput({ disabled, onSend }) {
  const [value, setValue] = useState("");

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="composer">
      <input
        className="composer__input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        placeholder="Type a message…"
        disabled={disabled}
      />
      <button className="composer__send" onClick={submit} disabled={disabled || !value.trim()}>
        Send
      </button>
    </div>
  );
}

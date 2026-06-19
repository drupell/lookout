"use client";

import { KeyRound } from "lucide-react";
import { useState } from "react";

import { Button, Input, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

interface Props {
  configured: boolean;
  /**
   * Current tier — accepted but unused inside this component (the parent
   * Settings page renders the tier badge in the page header). Keep on the
   * prop signature so callers can pass it without TS griping.
   */
  tier: "default" | "byok";
  onChanged: () => void;
}

type State =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "removing" }
  | { kind: "error"; message: string };

/**
 * BYOK MarketCheck key submission. Two states:
 *   - No key configured: input + Save
 *   - Key configured: "Configured" indicator + Rotate + Remove buttons
 *
 * The key value is never returned by the API after upload, so the input stays
 * masked and we don't display previous values.
 */
export function ByokSection({ configured, tier: _tier, onChanged }: Props) {
  const toast = useToast();
  const [apiKey, setApiKey] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });
  const [showRotate, setShowRotate] = useState(false);

  const submit = async () => {
    if (!apiKey.trim()) return;
    setState({ kind: "saving" });
    try {
      await api.putByokKey(apiKey.trim());
      setApiKey("");
      setShowRotate(false);
      setState({ kind: "idle" });
      toast.success("Key saved", { description: "You're now on the BYOK tier." });
      onChanged();
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.toUserMessage() : "Failed to save key";
      setState({ kind: "error", message });
      toast.error(message);
    }
  };

  const remove = async () => {
    if (!window.confirm("Remove your BYOK key? You'll be demoted to the default tier.")) {
      return;
    }
    setState({ kind: "removing" });
    try {
      await api.deleteByokKey();
      setApiKey("");
      setShowRotate(false);
      setState({ kind: "idle" });
      toast.info("Key removed", { description: "You're back on the default tier." });
      onChanged();
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.toUserMessage() : "Failed to remove key";
      setState({ kind: "error", message });
      toast.error(message);
    }
  };

  const busy = state.kind === "saving" || state.kind === "removing";

  return (
    <div className="space-y-3">
      {configured && !showRotate ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-md bg-brand-subtle px-2.5 py-1.5 font-mono text-xs text-brand dark:bg-amber-950/40 dark:text-amber-300">
            <KeyRound className="h-3.5 w-3.5" aria-hidden />
            ••••••••••••  configured
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setShowRotate(true);
            }}
            disabled={busy}
          >
            Rotate
          </Button>
          <Button variant="danger" size="sm" onClick={remove} loading={state.kind === "removing"}>
            {state.kind === "removing" ? "Removing…" : "Remove"}
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
            }}
            placeholder="Paste your MarketCheck API key"
            autoComplete="off"
            spellCheck={false}
            className="font-mono"
          />
          <div className="flex items-center gap-2">
            <Button onClick={submit} disabled={!apiKey.trim()} loading={state.kind === "saving"}>
              {state.kind === "saving" ? "Validating…" : configured ? "Save new key" : "Save"}
            </Button>
            {configured && showRotate ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setShowRotate(false);
                  setApiKey("");
                  setState({ kind: "idle" });
                }}
              >
                Cancel
              </Button>
            ) : null}
          </div>
        </div>
      )}

      {state.kind === "error" ? (
        <p className="text-xs text-red-600 dark:text-red-400">{state.message}</p>
      ) : null}
    </div>
  );
}

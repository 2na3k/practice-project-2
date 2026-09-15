"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, ChevronDown, Eye, EyeOff, ExternalLink, KeyRound, LoaderCircle, RefreshCw, Server, ShieldCheck, Unplug, X } from "lucide-react";
import type { ConnectionStatus, RegistryModel, RegistryProvider } from "@/lib/chat-types";

type ConnectionPanelProps = {
  connection: ConnectionStatus;
  onConnectionChange: (next: ConnectionStatus) => void;
  onBackToChat: () => void;
};

type ModelsResponse = { providers: RegistryProvider[] };

const OPENAI_ID = "openai";

function isConnectionStatus(value: unknown): value is ConnectionStatus {
  return Boolean(value && typeof value === "object" && "connected" in value && typeof (value as { connected?: unknown }).connected === "boolean");
}

function isModelsResponse(value: unknown): value is ModelsResponse {
  return Boolean(value && typeof value === "object" && Array.isArray((value as { providers?: unknown }).providers));
}

function formatContextWindow(value: RegistryModel["contextWindow"]): string {
  if (typeof value === "number") return `${Math.round(value / 1000)}k context`;
  return `${value} context`;
}

export function ConnectionPanel({ connection, onConnectionChange, onBackToChat }: ConnectionPanelProps) {
  const [providers, setProviders] = useState<RegistryProvider[]>([]);
  const [selectedProviderId, setSelectedProviderId] = useState(connection.provider ?? OPENAI_ID);
  const [selectedModelId, setSelectedModelId] = useState(connection.model ?? "");
  const [modelSearch, setModelSearch] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [submitSuccess, setSubmitSuccess] = useState("");
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const loadProviders = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setIsLoadingModels(true);
    setModelsError("");
    try {
      const response = await fetch("/api/models", { method: "GET", cache: "no-store", signal: controller.signal });
      const data: unknown = await response.json();
      if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data ? String((data as { error?: unknown }).error) : "The model catalog could not be loaded.");
      if (!isModelsResponse(data)) throw new Error("The model catalog returned an unexpected response.");
      if (!controller.signal.aborted && mountedRef.current) {
        setProviders(data.providers);
        setIsLoadingModels(false);
      }
    } catch (error) {
      if (controller.signal.aborted || !mountedRef.current) return;
      setIsLoadingModels(false);
      setModelsError(error instanceof Error ? error.message : "The model catalog could not be loaded.");
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const frame = window.requestAnimationFrame(() => {
      if (mountedRef.current) void loadProviders();
    });
    return () => {
      mountedRef.current = false;
      window.cancelAnimationFrame(frame);
      requestRef.current?.abort();
    };
  }, [loadProviders]);

  const selectedProvider = providers.find((provider) => provider.id === selectedProviderId);
  const selectedModel = selectedProvider?.models.find((model) => model.id === selectedModelId);
  const providerIsSupported = selectedProvider?.supported ?? false;
  const modelOptions = useMemo(() => {
    const provider = providers.find((candidate) => candidate.id === selectedProviderId);
    if (!provider) return [];
    const normalizedSearch = modelSearch.trim().toLocaleLowerCase();
    const filtered = provider.models.filter((model) => `${model.name} ${model.id}`.toLocaleLowerCase().includes(normalizedSearch));
    // Keep the current selection in the native select while searching so its
    // value always corresponds to an actual option and typing does not switch
    // the connection to an arbitrary search result.
    if (selectedModelId && !filtered.some((model) => model.id === selectedModelId)) {
      const selected = provider.models.find((model) => model.id === selectedModelId);
      return selected ? [selected, ...filtered] : filtered;
    }
    return filtered;
  }, [modelSearch, providers, selectedModelId, selectedProviderId]);

  useEffect(() => {
    const provider = providers.find((candidate) => candidate.id === selectedProviderId);
    if (!provider || isLoadingModels) return;
    let nextModelId: string | undefined;
    if (modelOptions.length === 0) {
      nextModelId = "";
    } else if (!provider.models.some((model) => model.id === selectedModelId)) {
      nextModelId = (provider.models.find((model) => model.id === "gpt-5.6-luna") ?? provider.models[0])?.id;
    }
    if (nextModelId === undefined || nextModelId === selectedModelId) return;
    const frame = window.requestAnimationFrame(() => setSelectedModelId(nextModelId));
    return () => window.cancelAnimationFrame(frame);
  }, [isLoadingModels, modelOptions, modelSearch, providers, selectedModelId, selectedProviderId]);

  useEffect(() => {
    if (!providers.length) return;
    const preferred = providers.find((provider) => provider.id === selectedProviderId);
    const fallback = providers.find((provider) => provider.id === connection.provider) ?? providers.find((provider) => provider.id === OPENAI_ID && provider.supported) ?? providers.find((provider) => provider.supported) ?? providers[0];
    const provider = preferred ?? fallback;
    if (!provider) return;
    const frame = window.requestAnimationFrame(() => {
      if (!mountedRef.current) return;
      if (provider.id !== selectedProviderId) setSelectedProviderId(provider.id);
      const connectedModel = provider.id === connection.provider ? connection.model : undefined;
      const preferredModel = provider.models.find((model) => model.id === "gpt-5.6-luna") ?? provider.models[0];
      const modelId = connectedModel && provider.models.some((model) => model.id === connectedModel) ? connectedModel : preferredModel?.id ?? "";
      if (!selectedModelId || !provider.models.some((model) => model.id === selectedModelId)) setSelectedModelId(modelId);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [connection.model, connection.provider, providers, selectedModelId, selectedProviderId]);

  const handleProviderChange = (providerId: string) => {
    setSelectedProviderId(providerId);
    const provider = providers.find((candidate) => candidate.id === providerId);
    const preferredModel = provider?.models.find((model) => model.id === "gpt-5.6-luna") ?? provider?.models[0];
    setSelectedModelId(preferredModel?.id ?? "");
    setModelSearch("");
    setApiKey("");
    setShowApiKey(false);
    setSubmitError("");
    setSubmitSuccess("");
  };

  const connect = async () => {
    const trimmedKey = apiKey.trim();
    if (!selectedProvider || !selectedModel || !providerIsSupported) return;
    const sameProvider = connection.connected && selectedProvider.id === connection.provider;
    const switchingModel = sameProvider && selectedModel.id !== connection.model;
    const reuseSessionKey = switchingModel && !trimmedKey;
    if (!trimmedKey && !reuseSessionKey) { setSubmitError("Enter an API key to test this connection."); return; }
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setIsSubmitting(true);
    setSubmitError("");
    setSubmitSuccess("");
    try {
      const response = await fetch("/api/connection", {
        method: reuseSessionKey ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reuseSessionKey ? { model: selectedModel.id } : { provider: selectedProvider.id, model: selectedModel.id, apiKey: trimmedKey }),
        signal: controller.signal,
      });
      const data: unknown = await response.json();
      if (!response.ok || (data && typeof data === "object" && "error" in data)) throw new Error(data && typeof data === "object" && "error" in data ? String((data as { error?: unknown }).error) : "The connection test failed.");
      const nextStatus = data && typeof data === "object" && "status" in data ? (data as { status?: unknown }).status : data;
      if (!isConnectionStatus(nextStatus)) throw new Error("The connection test returned an unexpected response.");
      if (controller.signal.aborted || !mountedRef.current) return;
      onConnectionChange(nextStatus);
      setApiKey("");
      setSubmitSuccess(`Connected to ${selectedProvider.name} · ${selectedModel.name}`);
    } catch (error) {
      if (!controller.signal.aborted && mountedRef.current) setSubmitError(error instanceof Error ? error.message : "The connection test failed.");
    } finally {
      if (!controller.signal.aborted && mountedRef.current) setIsSubmitting(false);
    }
  };

  const disconnect = async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setIsDisconnecting(true);
    setSubmitError("");
    try {
      const response = await fetch("/api/connection", { method: "DELETE", signal: controller.signal });
      const data: unknown = response.status === 204 ? null : await response.json();
      if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data ? String((data as { error?: unknown }).error) : "Could not disconnect.");
      if (controller.signal.aborted || !mountedRef.current) return;
      onConnectionChange({ connected: false });
      setSubmitSuccess("Disconnected. The server has cleared the session key.");
    } catch (error) {
      if (!controller.signal.aborted && mountedRef.current) setSubmitError(error instanceof Error ? error.message : "Could not disconnect.");
    } finally {
      if (!controller.signal.aborted && mountedRef.current) setIsDisconnecting(false);
    }
  };

  const connectionChanged = connection.connected && (connection.provider !== selectedProviderId || connection.model !== selectedModelId);
  const sameModelNoOp = connection.connected && connection.provider === selectedProviderId && connection.model === selectedModelId && !apiKey.trim();
  const canReuseSessionKey = connection.connected && connection.provider === selectedProviderId && connection.model !== selectedModelId && !apiKey.trim();
  return <section className="connection-panel" aria-labelledby="connection-title">
    <div className="connection-panel-header"><button className="connection-back" onClick={onBackToChat}><ArrowLeft size={17} aria-hidden="true" /> Back to chat</button><span className="connection-kicker"><Server size={14} aria-hidden="true" /> Model connection</span></div>
    <div className="connection-panel-intro"><div className="connection-title-row"><div><p className="eyebrow">BRING YOUR OWN KEY</p><h1 id="connection-title">Choose your model.</h1></div>{connection.connected && <span className="connection-state is-connected"><Check size={14} aria-hidden="true" /> Connected</span>}</div><p>Remymy can use a Pi-compatible provider for your chat. Pick a model, test it once, and keep cooking.</p></div>
    {connection.connected && <div className="active-connection"><div className="active-connection-icon"><ShieldCheck size={20} aria-hidden="true" /></div><div><span className="active-connection-label">ACTIVE MODEL</span><strong>{connection.model ?? selectedModel?.name ?? "Connected model"}</strong><p>{connection.provider ?? "Provider"}{connection.expiresAt ? ` · session ends ${new Date(connection.expiresAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : " · ephemeral server session"}</p></div><button className="connection-disconnect" onClick={disconnect} disabled={isDisconnecting}>{isDisconnecting ? <LoaderCircle className="spin" size={16} aria-hidden="true" /> : <Unplug size={16} aria-hidden="true" />} {isDisconnecting ? "Disconnecting…" : "Disconnect"}</button></div>}
    <div className="connection-form">
      <label className="connection-field"><span className="connection-label">Provider</span><span className="connection-select-wrap"><select className="connection-select" value={selectedProviderId} onChange={(event) => handleProviderChange(event.target.value)} disabled={isLoadingModels || isSubmitting} aria-describedby="provider-help">{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}{provider.supported ? "" : " · unavailable"}</option>)}</select><ChevronDown size={16} aria-hidden="true" /></span><span id="provider-help" className="connection-help">{selectedProvider?.note ?? "Providers and models come from the Pi catalog."}</span></label>
      {!providerIsSupported && selectedProvider && <p className="connection-inline-warning" role="status">This provider is visible for reference, but cannot be connected from this server yet.</p>}
      <label className="connection-field"><span className="connection-label">Model</span><div className="model-search-wrap"><input className="connection-input" value={modelSearch} onChange={(event) => setModelSearch(event.target.value)} placeholder="Search models…" aria-label="Search models" /><span className="model-search-count">{modelOptions.length} shown</span></div><span className="connection-select-wrap"><select className="connection-select" value={selectedModelId} onChange={(event) => setSelectedModelId(event.target.value)} disabled={!selectedProvider || isLoadingModels || isSubmitting} aria-label="Select model">{modelOptions.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select><ChevronDown size={16} aria-hidden="true" /></span></label>
      {isLoadingModels ? <div className="connection-loading" role="status"><LoaderCircle className="spin" size={16} /> Loading providers and models…</div> : selectedProvider && modelOptions.length === 0 ? <p className="connection-no-results" role="status">No models match “{modelSearch.trim()}”. Try a different search.</p> : null}
      {selectedModel && <div className="model-details"><div><span>Model ID</span><strong>{selectedModel.id}</strong></div><div><span>Context</span><strong>{formatContextWindow(selectedModel.contextWindow)}</strong></div><div><span>Reasoning</span><strong>{selectedModel.reasoning ? "Yes" : "No"}</strong></div></div>}
      <label className="connection-field"><span className="connection-label">API key <span className="connection-optional">never saved in this browser</span></span><div className="key-input-wrap"><KeyRound size={17} aria-hidden="true" /><input className="connection-input" type={showApiKey ? "text" : "password"} value={apiKey} onChange={(event) => { setApiKey(event.target.value); setSubmitError(""); setSubmitSuccess(""); }} placeholder="Paste your provider key" autoComplete="new-password" spellCheck={false} /><button type="button" className="key-visibility" onClick={() => setShowApiKey((visible) => !visible)} aria-label={showApiKey ? "Hide API key" : "Show API key"}>{showApiKey ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}</button></div></label>
      {modelsError && <div className="connection-message is-error" role="alert"><span>{modelsError}</span><button className="message-action" onClick={() => void loadProviders()}><RefreshCw size={14} aria-hidden="true" /> Retry</button></div>}
      {submitError && <div className="connection-message is-error" role="alert"><X size={16} aria-hidden="true" />{submitError}</div>}
      {submitSuccess && <div className="connection-message is-success" role="status"><Check size={16} aria-hidden="true" />{submitSuccess}</div>}
      <button className="connection-submit" onClick={() => void connect()} disabled={isLoadingModels || isSubmitting || !providerIsSupported || !selectedModel || sameModelNoOp}>{isSubmitting ? <><LoaderCircle className="spin" size={17} aria-hidden="true" /> Testing connection…</> : canReuseSessionKey ? "Switch model & test" : connectionChanged ? "Connect new model" : connection.connected ? "Reconnect & test" : "Connect & test"}</button>
      <p className="connection-usage-note">Provider requests may incur usage or charges.{selectedProvider?.id === OPENAI_ID && <> Manage keys in the <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">OpenAI console <ExternalLink size={13} aria-hidden="true" /></a>.</>}</p>
    </div>
    {!connection.connected && <div className="connection-demo-fallback"><div><strong>Just exploring?</strong><p>Continue in the local demo with no model connection. Your key stays out of browser storage.</p></div><button className="connection-demo-button" onClick={onBackToChat}>Continue in demo <ArrowLeft size={16} aria-hidden="true" /></button></div>}
    <p className="connection-privacy"><ShieldCheck size={15} aria-hidden="true" /> Keys stay in server memory for this session, expire after 8 hours, and clear on disconnect or server restart. Your key is not stored in this browser or on disk.</p>
  </section>;
}

import { describe, expect, it } from "vitest";
import {
  findRegistryModel,
  findRegistryProvider,
  getPiModels,
  listRegistryProviders,
  resolveSupportedModel,
} from "./model-registry";

describe("model registry", () => {
  it("looks up providers and models from the installed Pi catalog", () => {
    const provider = findRegistryProvider("anthropic");
    expect(provider?.id).toBe("anthropic");
    expect(provider?.getModels().length).toBeGreaterThan(0);

    const modelId = provider?.getModels()[0]?.id;
    expect(modelId).toBeTruthy();
    expect(findRegistryModel("anthropic", modelId ?? "")?.id).toBe(modelId);
    expect(findRegistryModel("anthropic", "does-not-exist")).toBeUndefined();
  });

  it("projects supported flags and models without serializing provider auth", () => {
    const providers = listRegistryProviders();
    const anthropic = providers.find(({ id }) => id === "anthropic");
    const oauthOnly = providers.find(({ id }) => id === "openai-codex");

    expect(providers.length).toBeGreaterThan(5);
    expect(anthropic).toMatchObject({ id: "anthropic", supported: true });
    expect(anthropic?.models.length).toBeGreaterThan(0);
    expect(oauthOnly).toMatchObject({
      id: "openai-codex",
      supported: false,
    });
    expect(oauthOnly?.note).toMatch(/OAuth/i);
    expect(resolveSupportedModel("openai-codex", oauthOnly?.models[0]?.id ?? "")).toBeUndefined();
    expect(resolveSupportedModel("anthropic", anthropic?.models[0]?.id ?? "")).toBeDefined();

    const publicKeys = new Set<string>();
    const collectKeys = (value: unknown): void => {
      if (Array.isArray(value)) return value.forEach(collectKeys);
      if (!value || typeof value !== "object") return;
      for (const [key, nested] of Object.entries(value)) {
        publicKeys.add(key);
        collectKeys(nested);
      }
    };
    collectKeys(providers);
    expect(publicKeys).not.toEqual(
      expect.arrayContaining(["apiKey", "oauth", "secret", "token"]),
    );
    expect(Object.values(anthropic ?? {})).not.toContain(expect.any(Function));
  });

  it("keeps the catalog singleton stable for a server process", () => {
    expect(getPiModels()).toBe(getPiModels());
  });
});

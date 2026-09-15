import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import type { Api, Model, Models, Provider } from "@earendil-works/pi-ai";
import type { RegistryModel, RegistryProvider } from "@/lib/chat-types";

let modelsSingleton: Models | undefined;

const DISABLED_PROVIDER_NOTES: Record<string, string> = {
  "amazon-bedrock": "This provider needs AWS credentials and region configuration; API-key connection is unavailable.",
  "azure-openai-responses": "This provider needs an Azure endpoint or resource configuration in addition to its key.",
  "cloudflare-ai-gateway": "This provider needs a Cloudflare account and gateway configuration in addition to its key.",
  "cloudflare-workers-ai": "This provider needs a Cloudflare account configuration in addition to its key.",
  "github-copilot": "This provider requires OAuth sign-in; API-key connection is unavailable.",
  "google-vertex": "This provider needs Google Cloud project and location configuration; API-key connection is unavailable.",
  radius: "This provider uses a dynamic catalog that is unavailable in this app.",
};

const isTextChatModel = (model: Model<Api>): boolean => !/(?:realtime|audio)/i.test(model.id);

/** Keep one catalog per server process; catalog reads are synchronous in pi-ai. */
export function getPiModels(): Models {
  if (!modelsSingleton) modelsSingleton = builtinModels();
  return modelsSingleton;
}

export function findRegistryProvider(providerId: string): Provider | undefined {
  return getPiModels().getProvider(providerId);
}

export function findRegistryModel(providerId: string, modelId: string): Model<Api> | undefined {
  return getPiModels().getModel(providerId, modelId);
}

function providerSupport(provider: Provider): { supported: boolean; note?: string } {
  const configuredNote = DISABLED_PROVIDER_NOTES[provider.id];
  if (configuredNote) return { supported: false, note: configuredNote };
  if (provider.auth.apiKey) {
    let modelCount = 0;
    try {
      modelCount = provider.getModels().length;
    } catch {
      // A broken provider catalog should still be displayed as unavailable.
      return { supported: false, note: "This provider's model catalog is currently unavailable." };
    }
    if (modelCount === 0) return { supported: true, note: "Models are loaded dynamically by the provider." };
    return { supported: true };
  }
  if (provider.auth.oauth) return { supported: false, note: "This provider requires OAuth sign-in; API-key connection is unavailable." };
  return { supported: false, note: "This provider does not expose an API-key connection in this app." };
}

function toRegistryModel(model: Model<Api>): RegistryModel {
  return {
    id: model.id,
    name: model.name,
    contextWindow: model.contextWindow,
    reasoning: model.reasoning,
  };
}

function toRegistryProvider(provider: Provider): RegistryProvider {
  const support = providerSupport(provider);
  return {
    id: provider.id,
    name: provider.name,
    models: provider.getModels().filter(isTextChatModel).map(toRegistryModel),
    supported: support.supported,
    ...(support.note ? { note: support.note } : {}),
  };
}

/** Public catalog projection; auth objects and provider internals never leave the server. */
export function listRegistryProviders(): RegistryProvider[] {
  return getPiModels().getProviders().map(toRegistryProvider);
}

/**
 * Resolve a model only when both the provider and model are in the public Pi
 * catalog. This keeps route handlers from accepting arbitrary provider input.
 */
export function resolveSupportedModel(providerId: string, modelId: string): Model<Api> | undefined {
  const provider = findRegistryProvider(providerId);
  if (!provider || !provider.auth.apiKey || DISABLED_PROVIDER_NOTES[provider.id]) return undefined;
  const model = findRegistryModel(providerId, modelId);
  return model && isTextChatModel(model) ? model : undefined;
}

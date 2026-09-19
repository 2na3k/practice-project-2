export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (!process.env.LANGFUSE_PUBLIC_KEY || !process.env.LANGFUSE_SECRET_KEY || !process.env.LANGFUSE_BASE_URL) return;

  const [{ NodeSDK }, { LangfuseSpanProcessor }] = await Promise.all([
    import("@opentelemetry/sdk-node"),
    import("@langfuse/otel"),
  ]);
  const processor = new LangfuseSpanProcessor({ exportMode: "immediate" });
  (globalThis as { __remymyLangfuseProcessor?: unknown }).__remymyLangfuseProcessor = processor;
  new NodeSDK({ spanProcessors: [processor] }).start();
}

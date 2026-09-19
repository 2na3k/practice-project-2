import { describe, expect, it } from "vitest";
import { flushLangfuse, withLangfuseSpan } from "./langfuse";

describe("langfuse tracing helper", () => {
  it("runs the wrapped work when Langfuse is not configured", async () => {
    const result = await withLangfuseSpan("pi-agent-chat", async (span) => {
      span.update({ input: { ok: true } });
      return "traced";
    }, { asType: "agent" });
    expect(result).toBe("traced");
    await expect(flushLangfuse()).resolves.toBeUndefined();
  });
});

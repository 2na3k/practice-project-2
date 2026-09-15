import { listRegistryProviders } from "@/lib/server/model-registry";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return Response.json({ providers: listRegistryProviders() }, {
    headers: { "Cache-Control": "no-store" },
  });
}

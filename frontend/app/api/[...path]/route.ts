/**
 * Server-side proxy to the backend.
 *
 * The browser used to call the backend directly with `NEXT_PUBLIC_APP_API_KEY`, and anything
 * prefixed `NEXT_PUBLIC_` is inlined into the JavaScript bundle at build time — so the API key was
 * readable by every visitor with devtools. That is survivable on localhost and disqualifying for a
 * deployed demo.
 *
 * The browser now calls this route on its own origin, and the key is attached here, where it stays
 * in the server process. `API_BASE_URL` and `APP_API_KEY` are deliberately *not* NEXT_PUBLIC.
 */

/**
 * Vercel functions default to 10 seconds, which is comfortable for a query — a RAG turn measures
 * ~1.5s against the deployed backend — and not for a sync. Syncs still run inside the request that
 * triggers them, and a first sync fetches, chunks and embeds every commit. Fluid Compute allows up
 * to 300s on the Hobby plan, so this buys the headroom until background sync exists.
 */
export const maxDuration = 300;

const backendBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
const apiKey = process.env.APP_API_KEY ?? "change-me";

// Only the backend routes the UI actually uses. A wildcard proxy would forward anything a caller
// asked for, which turns a same-origin route into an open relay carrying a credential.
const ALLOWED = [
  /^projects$/,
  /^projects\/[^/]+\/(sync|connectors)\/(github|jira|slack)$/,
  /^projects\/[^/]+\/timeline$/,
  /^query$/,
  /^conversations\/[^/]+(\/trace)?$/,
  /^health(\/(ollama|database))?$/
];

async function forward(request: Request, path: string[]): Promise<Response> {
  const suffix = path.join("/");
  if (!ALLOWED.some((pattern) => pattern.test(suffix))) {
    return Response.json({ detail: `Not proxied: ${suffix}` }, { status: 404 });
  }

  const incoming = new URL(request.url);
  const target = new URL(`${backendBaseUrl}/${suffix}`);
  target.search = incoming.search;

  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();

  const response = await fetch(target, {
    method: request.method,
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
    body,
    cache: "no-store"
  });

  // Pass the backend's status and body through untouched so the client keeps seeing real errors
  // rather than a proxy's opinion of them.
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" }
  });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, context: Context): Promise<Response> {
  return forward(request, (await context.params).path);
}

export async function POST(request: Request, context: Context): Promise<Response> {
  return forward(request, (await context.params).path);
}

export async function PUT(request: Request, context: Context): Promise<Response> {
  return forward(request, (await context.params).path);
}

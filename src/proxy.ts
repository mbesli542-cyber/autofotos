/**
 * Proxy (formerly "middleware"): keeps the Supabase session cookie fresh and
 * redirects signed-out users to /login. Does nothing in demo mode – there,
 * the client-side AuthGate handles access.
 *
 * Developer tools (/dev/*) are outside the login. When they are disabled
 * (production without ENABLE_DEV_TOOLS=true) they answer with a real 404 –
 * the page itself could only render the not-found UI after the static shell
 * (status 200) has been sent.
 */
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { isDevToolsEnabled } from "@/lib/dev-tools";
import { getSupabasePublicConfig } from "@/lib/supabase/config";

const PUBLIC_PATHS = ["/login", "/offline"];
const DEV_TOOLS_PATH = "/dev";
/** Not a route – rewriting here renders the app's not-found page with status 404. */
const NOT_FOUND_REWRITE = "/__not-found";

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (pathname === DEV_TOOLS_PATH || pathname.startsWith(`${DEV_TOOLS_PATH}/`)) {
    if (isDevToolsEnabled()) return NextResponse.next();
    return NextResponse.rewrite(new URL(NOT_FOUND_REWRITE, request.url), { status: 404 });
  }

  const config = getSupabasePublicConfig();
  if (!config) return NextResponse.next();

  let response = NextResponse.next({ request });
  const supabase = createServerClient(config.url, config.anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
        for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
      },
    },
  });

  // Validates the session (and refreshes it if needed).
  const { data } = await supabase.auth.getClaims();
  const isSignedIn = Boolean(data?.claims?.sub);

  const isApi = pathname.startsWith("/api/");
  const isPublic = PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));

  if (!isSignedIn && !isPublic && !isApi) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return response;
}

export const config = {
  matcher: [
    // Everything except static assets, PWA files and images.
    // /api/dev/* (developer proxy routes) is skipped: the routes gate
    // themselves with ENABLE_DEV_TOOLS, and skipping the proxy avoids its
    // request body buffering limit (10 MB) for test uploads of up to 40 MB.
    "/((?!_next/static|_next/image|favicon.ico|sw.js|manifest.webmanifest|icons/|brand/|overlays/|demo/|presets/|api/dev/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};

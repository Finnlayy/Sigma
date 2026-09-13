/**
 * Validates and sanitizes URLs to prevent Cross-Site Scripting (XSS) via injected URLs.
 * Neutralizes potentially dangerous protocols like javascript:, data:, vbscript:.
 *
 * @param url The URL to sanitize.
 * @returns The sanitized URL or a safe fallback if it's potentially malicious.
 */
export function sanitizeUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;

  try {
    const parsedUrl = new URL(url, "http://fallback.com");

    // Allow list of safe protocols
    const safeProtocols = ["http:", "https:", "ftp:", "mailto:"];

    if (safeProtocols.includes(parsedUrl.protocol)) {
      return url;
    }

    // If we parse it but it's a dangerous protocol (javascript:, data:, etc.)
    return "about:blank";
  } catch (e) {
    // If URL parsing fails, check for malicious prefixes just in case it's a relative URL
    const lowerUrl = url.toLowerCase().trim();
    if (
      lowerUrl.startsWith("javascript:") ||
      lowerUrl.startsWith("data:") ||
      lowerUrl.startsWith("vbscript:")
    ) {
      return "about:blank";
    }

    return url; // It's likely a safe relative URL if it doesn't parse and doesn't start with bad protocols
  }
}

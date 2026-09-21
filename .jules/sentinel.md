## 2026-09-10 - [DOM-based XSS Prevention via External Payload URLs]
**Vulnerability:** External URLs passed directly from API payloads into DOM sinks (e.g. href, src) without protocol validation, risking stored XSS (e.g. javascript:...).
**Learning:** React escapes text children by default, but injecting unvalidated strings into `href` or `src` attributes bypasses this protection, allowing execution of malicious protocols.
**Prevention:** Always validate protocols of any dynamically sourced URLs before setting them on DOM nodes. Create and utilize a central `sanitizeUrl` helper allowing only safe protocols (http/https/ftp/mailto).

## 2026-09-21 - [Insecure Randomness for Unique IDs]
**Vulnerability:** Weak PRNG `Math.random()` used for generating DOM node/component IDs (`uid` in dock). While initially low impact for UI state, predictable IDs can be exploited in edge cases or flagged by scanners.
**Learning:** `Math.random()` is not cryptographically secure. Relying on it for uniqueness can cause collisions or predictable sequences. Furthermore, when migrating to `window.crypto`, explicit SSR guards (`typeof window !== 'undefined'`) are strictly necessary to avoid crashing Node.js/Next environments where `window` is missing.
**Prevention:** Use `window.crypto.getRandomValues()` for generating random bytes in browsers, ensuring to provide a safe fallback for SSR or node environments.

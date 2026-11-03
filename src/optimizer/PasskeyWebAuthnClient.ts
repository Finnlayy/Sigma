/* =========================================================
   Datei:      src/optimizer/PasskeyWebAuthnClient.ts
   Zweck:      Browser-Native Passkey (Touch ID/Face ID/Windows Hello) Client
   Knoten:     Jaune (Carrera-Engine) / Frontend Security
   ========================================================= */

export class PasskeyWebAuthnClient {
  public static async authenticatePasskeyForSettings(
    userEmail: string,
  ): Promise<string | null> {
    try {
      const desktop = await fetch("/api/desktop/status", { signal: AbortSignal.timeout(3000) });
      if (desktop.ok && (await desktop.json()).desktop === true) {
        if (!window.confirm("Authorize this SIGMA paper-operator action using your private local desktop session? This is not passkey or biometric verification.")) return null;
        const authorized = await fetch("/api/desktop/operator/authorize", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmation: "AUTHORIZE PAPER OPERATOR" }),
        });
        if (!authorized.ok) return null;
        const result = await authorized.json();
        return result.mode === "desktop-session" ? result.settingsToken : null;
      }
      // 1. Challenge vom Backend abrufen
      const res = await fetch(
        `/api/v1/auth/passkey/challenge?email=${encodeURIComponent(userEmail)}`,
      );
      const options = await res.json();

      options.publicKey.challenge = Uint8Array.from(
        atob(options.publicKey.challenge),
        (c) => c.charCodeAt(0),
      );
      options.publicKey.user.id = Uint8Array.from(
        atob(options.publicKey.user.id),
        (c) => c.charCodeAt(0),
      );

      // 2. Browser-Native WebAuthn Biometrie / Passkey Prompt
      const assertion = (await navigator.credentials.get({
        publicKey: options.publicKey,
      })) as PublicKeyCredential;
      const response = assertion.response as AuthenticatorAssertionResponse;

      // 3. Verifikation beim Backend
      const verifyRes = await fetch("/api/v1/auth/passkey/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: userEmail,
          credential: {
            id: assertion.id,
            response: {
              clientDataJSON: btoa(
                String.fromCharCode(...new Uint8Array(response.clientDataJSON)),
              ),
              authenticatorData: btoa(
                String.fromCharCode(
                  ...new Uint8Array(response.authenticatorData),
                ),
              ),
            },
          },
        }),
      });

      const data = await verifyRes.json();
      return data.success ? data.settingsToken : null;
    } catch (err) {
      console.error("🚨 Passkey Authentication Failed:", err);
      return null;
    }
  }
}

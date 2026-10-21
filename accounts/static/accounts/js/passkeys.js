// Passkeys: sign in / add / rename / remove (fingerprint, face or screen lock).
// Used on the login page and on both profile pages. The profile card is loaded
// with fetch() after the page opens, so all buttons are handled by delegation.
(function () {
  "use strict";

  // ---------- helpers ----------

  function b64urlToBuf(value) {
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "===".slice((base64.length + 3) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  function bufToB64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  async function postJSON(url, data) {
    const res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(data || {}),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok || body.ok === false) {
      throw new Error(body.error || "Something went wrong. Please try again.");
    }
    return body;
  }

  function isIpHost() {
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(location.hostname) || location.hostname.includes(":");
  }

  function supported() {
    return !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
  }

  function unsupportedMessage() {
    if (location.hostname === "127.0.0.1") return "Passkeys don't work on 127.0.0.1 — use localhost instead (log in again there).";
    if (isIpHost()) return "Passkeys don't work on an IP address like " + location.hostname +
                           ". Run the server on your own computer and open http://localhost:8000 instead.";
    return "This browser doesn't support passkeys. Use your email and password instead.";
  }

  // The browser rejects with these when the user closes the prompt or it times out
  function friendlyBrowserError(err) {
    if (err && (err.name === "NotAllowedError" || err.name === "AbortError")) {
      return "Passkey prompt was cancelled or timed out.";
    }
    if (err && err.name === "InvalidStateError") {
      return "This device already has a passkey for your account.";
    }
    return (err && err.message) || "Something went wrong. Please try again.";
  }

  function setBusy(button, busy, busyText) {
    if (!button) return;
    if (busy) {
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.textContent = busyText;
    } else if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
      button.disabled = false;
    }
  }

  // Same page, but on localhost (passkeys are refused on 127.0.0.1)
  function localhostUrl() {
    return location.protocol + "//localhost" + (location.port ? ":" + location.port : "") +
           location.pathname + location.search;
  }

  function showMessage(id, text, isError) {
    const box = document.getElementById(id);
    if (!box) { if (isError) alert(text); return; }
    box.textContent = text;
    if (isError && location.hostname === "127.0.0.1") {
      const link = document.createElement("a");
      link.href = localhostUrl();
      link.textContent = " Open this page on localhost →";
      link.className = "underline font-semibold";
      box.appendChild(link);
    }
    box.classList.remove("hidden", "text-red-600", "text-green-700", "bg-red-50", "bg-green-50", "border-red-200", "border-green-200");
    box.classList.add(...(isError ? ["text-red-600", "bg-red-50", "border-red-200"] : ["text-green-700", "bg-green-50", "border-green-200"]));
  }

  // ---------- sign in ----------

  async function signIn(button) {
    const urls = button.dataset;
    if (!supported()) return showMessage("passkey-login-message", unsupportedMessage(), true);

    setBusy(button, true, "Waiting for your passkey...");
    try {
      const options = await postJSON(urls.optionsUrl);
      options.challenge = b64urlToBuf(options.challenge);
      (options.allowCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });

      const cred = await navigator.credentials.get({ publicKey: options });
      const result = await postJSON(urls.verifyUrl, {
        credential: {
          id: cred.id,
          rawId: bufToB64url(cred.rawId),
          type: cred.type,
          response: {
            clientDataJSON: bufToB64url(cred.response.clientDataJSON),
            authenticatorData: bufToB64url(cred.response.authenticatorData),
            signature: bufToB64url(cred.response.signature),
            userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : null,
          },
          clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
          authenticatorAttachment: cred.authenticatorAttachment || null,
        },
      });
      button.textContent = "Signed in. Redirecting...";
      window.location.href = result.redirect;
    } catch (err) {
      setBusy(button, false);
      // Closing the prompt and "no passkey for this site on this device/phone" look the
      // same to the page, so explain both — most often the passkey was never set up.
      const noneUsed = err && (err.name === "NotAllowedError" || err.name === "AbortError");
      showMessage(
        "passkey-login-message",
        noneUsed
          ? "Passkey sign-in didn't go through. If you haven't set up a passkey yet, sign in with your " +
            "email and password first, then choose \"Set up passkey\" on your dashboard."
          : friendlyBrowserError(err),
        true
      );
    }
  }

  // ---------- add ----------

  function suggestDeviceName() {
    const ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua)) return "iPad";
    if (/Android/.test(ua)) return "Android phone";
    if (/Windows/.test(ua)) return "Windows PC";
    if (/Macintosh/.test(ua)) return "Mac";
    return "My device";
  }

  async function addPasskey(button) {
    const urls = button.dataset;
    // The profile card and the dashboard prompt each have their own message box
    const messageId = urls.messageId || "passkey-card-message";
    if (!supported()) return showMessage(messageId, unsupportedMessage(), true);

    const name = window.prompt("Name this passkey so you can recognize it later:", suggestDeviceName());
    if (name === null) return;  // cancelled

    setBusy(button, true, "Waiting for your device...");
    try {
      const options = await postJSON(urls.optionsUrl);
      options.challenge = b64urlToBuf(options.challenge);
      options.user.id = b64urlToBuf(options.user.id);
      (options.excludeCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });

      const cred = await navigator.credentials.create({ publicKey: options });
      await postJSON(urls.verifyUrl, {
        name: name,
        credential: {
          id: cred.id,
          rawId: bufToB64url(cred.rawId),
          type: cred.type,
          response: {
            clientDataJSON: bufToB64url(cred.response.clientDataJSON),
            attestationObject: bufToB64url(cred.response.attestationObject),
            transports: cred.response.getTransports ? cred.response.getTransports() : [],
          },
          clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
          authenticatorAttachment: cred.authenticatorAttachment || null,
        },
      });
      showMessage(messageId,
        "Passkey added! Next time, click \"Sign in with a passkey\" on the login page and use this device.", false);
      setTimeout(function () { window.location.reload(); }, 2500);
    } catch (err) {
      setBusy(button, false);
      showMessage(messageId, friendlyBrowserError(err), true);
    }
  }

  // ---------- rename / remove ----------

  async function renamePasskey(button) {
    const name = window.prompt("New name for this passkey:", button.dataset.currentName || "");
    if (name === null || !name.trim()) return;
    try {
      const result = await postJSON(button.dataset.url, { name: name });
      const label = document.getElementById("passkey-name-" + button.dataset.id);
      if (label) label.textContent = result.name;
      button.dataset.currentName = result.name;
      showMessage("passkey-card-message", "Passkey renamed.", false);
    } catch (err) {
      showMessage("passkey-card-message", friendlyBrowserError(err), true);
    }
  }

  async function removePasskey(button) {
    const ok = window.confirm(
      'Remove "' + (button.dataset.currentName || "this passkey") + '"? You won\'t be able to sign in with it anymore.'
    );
    if (!ok) return;
    try {
      await postJSON(button.dataset.url);
      const row = document.getElementById("passkey-row-" + button.dataset.id);
      if (row) row.remove();
      if (!document.querySelector("[id^='passkey-row-']")) {
        const empty = document.getElementById("passkey-empty");
        if (empty) empty.classList.remove("hidden");
      }
      showMessage("passkey-card-message", "Passkey removed.", false);
    } catch (err) {
      showMessage("passkey-card-message", friendlyBrowserError(err), true);
    }
  }

  // ---------- wiring ----------

  document.addEventListener("click", function (event) {
    const target = event.target.closest("[data-passkey-action]");
    if (!target) return;
    event.preventDefault();
    const action = target.dataset.passkeyAction;
    if (action === "login") signIn(target);
    else if (action === "add") addPasskey(target);
    else if (action === "rename") renamePasskey(target);
    else if (action === "remove") removePasskey(target);
  });
})();

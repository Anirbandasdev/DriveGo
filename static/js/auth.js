/* DriveGo ⇄ Clerk glue, shared by every page.
 *
 * Clerk is configured with our own sign-in/sign-up URLs. Without them Clerk
 * sends people to its hosted Account Portal (a dark, unbranded page) whenever a
 * flow is interrupted, e.g. pressing Back halfway through Google sign-in.
 */
(function () {
  var cfgEl = document.getElementById("clerkConfig");
  if (!cfgEl) return;
  var cfg = cfgEl.dataset;

  var APPEARANCE = {
    variables: {
      colorPrimary: "#146EF5",
      colorBackground: "#FFFFFF",
      colorText: "#111827",
      colorTextSecondary: "#667085",
      colorInputBackground: "#FFFFFF",
      colorInputText: "#111827",
      colorDanger: "#D92D20",
      fontFamily: "Manrope, system-ui, -apple-system, sans-serif",
      fontSize: "15px",
      borderRadius: "12px"
    },
    layout: { socialButtonsVariant: "blockButton", logoPlacement: "none" },
    elements: {
      rootBox: { width: "100%" },
      // Visible overflow + a little top padding so the "Last used" badge on the
      // Google button is not clipped.
      cardBox: { width: "100%", maxWidth: "100%", boxShadow: "none", border: "none", borderRadius: "0", overflow: "visible" },
      card: { width: "100%", boxShadow: "none", border: "none", padding: "12px 0 0", background: "transparent", gap: "20px" },
      header: { display: "none" },
      footer: { display: "none" },
      socialButtonsBlockButton: { height: "46px", border: "1px solid #D0D5DD", fontWeight: "600" },
      formFieldInput: { height: "46px" },
      formButtonPrimary: { height: "46px", fontSize: "15px", fontWeight: "700", textTransform: "none", boxShadow: "none" },
      dividerLine: { background: "#E6E8EC" }
    }
  };

  function absolute(path) {
    return new URL(path, window.location.origin).href;
  }

  function withNext(path, next) {
    return next ? path + "?next=" + encodeURIComponent(next) : path;
  }

  function cookie(name) {
    var parts = ("; " + document.cookie).split("; " + name + "=");
    return parts.length === 2 ? parts.pop().split(";").shift() : "";
  }

  function waitForClerk(timeoutMs) {
    return new Promise(function (resolve, reject) {
      var started = Date.now();
      (function poll() {
        if (window.Clerk) return resolve(window.Clerk);
        if (Date.now() - started > timeoutMs) return reject(new Error("Clerk failed to load"));
        setTimeout(poll, 100);
      })();
    });
  }

  var loading = null;
  function load(next) {
    if (!loading) {
      var home = absolute(withNext(cfg.loginUrl, next));
      loading = waitForClerk(15000).then(function (clerk) {
        return clerk.load({
          appearance: APPEARANCE,
          signInUrl: absolute(cfg.loginUrl),
          signUpUrl: absolute(cfg.signupUrl),
          // Finishing (or resuming) a sign-in always lands back on our login page,
          // which then creates the Django session.
          signInFallbackRedirectUrl: home,
          signUpFallbackRedirectUrl: home,
          afterSignOutUrl: absolute(cfg.homeUrl)
        }).then(function () { return clerk; });
      });
    }
    return loading;
  }

  function syncServer(session, next) {
    return session.getToken().then(function (token) {
      return fetch(cfg.callbackUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": cookie("csrftoken") },
        body: JSON.stringify({ session_token: token, next: next || "" })
      });
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Unexpected response from the server." }; });
    });
  }

  /* Every other page: keep the Django session in step with Clerk. */
  function autoSync() {
    var body = document.body;
    if (body.hasAttribute("data-auth-page")) return;
    var authed = body.hasAttribute("data-authed");
    load().then(function (clerk) {
      var loader = document.getElementById("authLoader");
      if (clerk.session && !authed) {
        if (loader) loader.hidden = false;
        syncServer(clerk.session).then(function (j) {
          if (j.ok) window.location.reload();
          else if (loader) loader.hidden = true;
        }).catch(function () { if (loader) loader.hidden = true; });
      } else if (!clerk.session && authed && body.dataset.page !== "admin") {
        /* The admin console runs on Django sessions; don't kick admins out mid-review. */
        window.location.href = cfg.logoutUrl;
      }
    }).catch(function () { /* Clerk unreachable: leave the page usable. */ });
  }

  window.DriveGoAuth = { load: load, syncServer: syncServer, appearance: APPEARANCE, withNext: withNext, config: cfg };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoSync);
  else autoSync();
})();

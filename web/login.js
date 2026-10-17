// Halaman login Threads CMS v2. Tanpa dependensi; sesi dikelola server lewat cookie HttpOnly.
(function () {
  const $ = (s) => document.querySelector(s);
  document.documentElement.setAttribute("data-theme", localStorage.getItem("cms-theme") || "dark");

  const form = $("#login-form"), user = $("#username"), pass = $("#password");
  const err = $("#login-error"), btn = $("#login-submit"), spin = btn.querySelector(".spin");
  const lbl = btn.querySelector(".lbl"), tgl = $("#pw-toggle");

  tgl.addEventListener("click", () => {
    const show = pass.type === "password";
    pass.type = show ? "text" : "password";
    tgl.setAttribute("aria-pressed", String(show));
    tgl.setAttribute("aria-label", show ? "Sembunyikan password" : "Tampilkan password");
    tgl.querySelector(".eye").hidden = show;
    tgl.querySelector(".eye-off").hidden = !show;
    pass.focus();
  });

  function showError(msg) { err.textContent = msg; err.hidden = false; }
  function setLoading(on) {
    btn.disabled = on; spin.hidden = !on; lbl.textContent = on ? "Memproses..." : "Masuk";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    user.classList.remove("invalid"); pass.classList.remove("invalid");
    if (!user.value.trim() || !pass.value) {
      if (!user.value.trim()) user.classList.add("invalid");
      if (!pass.value) pass.classList.add("invalid");
      showError("Username dan password wajib diisi");
      (user.value.trim() ? pass : user).focus();
      return;
    }
    setLoading(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user.value, password: pass.value }),
      });
      let j = {};
      try { j = await r.json(); } catch (_) {}
      if (r.ok && j.ok) { location.replace("/"); return; }
      showError(j.error || "Login gagal. Coba lagi.");
      if (r.status === 401) { pass.value = ""; pass.focus(); }
    } catch (_) {
      showError("Tidak bisa terhubung ke server");
    }
    setLoading(false);
  });
})();

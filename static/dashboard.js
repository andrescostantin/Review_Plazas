(function () {
  "use strict";
  var CFG = JSON.parse(document.getElementById("cfg").textContent);
  var LEVEL_CLASS = { muy_malo: "lvl-muy_malo", mejoras: "lvl-mejoras", regular: "lvl-regular", bueno: "lvl-bueno" };
  var PAGE = 30;
  var reqId = 0, commentOffset = 0, commentTotal = 0;

  // Todo el texto de usuarios se inserta con textContent (nunca innerHTML) para evitar XSS.
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function $(id) { return document.getElementById(id); }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function fmt(x) { return x === null || x === undefined ? "—" : Number(x).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function fmtDate(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
  }
  function stars(n) { return "★".repeat(n) + "☆".repeat(5 - n); }
  function ratingText(n) { return n + " de 5 — " + (CFG.ratingLabels[n] || ""); }

  function params(extra) {
    var p = new URLSearchParams();
    new FormData($("filters")).forEach(function (v, k) { if (v) p.set(k, v); });
    Object.keys(extra || {}).forEach(function (k) { p.set(k, extra[k]); });
    return p;
  }
  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) { window.location.href = "/analistas/login"; throw new Error("Sesión vencida"); }
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || "Error " + r.status);
        return j;
      });
    });
  }
  function setStatus(msg, isError) {
    var s = $("status");
    s.textContent = msg || "";
    s.className = "status" + (isError ? " status-error" : "") + (msg ? " show" : "");
  }

  // ---------- indicadores ----------
  function renderKpis(d) {
    $("kTotal").textContent = d.total;
    $("kTotalSub").textContent = d.legacy && d.legacy.total ? "+ " + d.legacy.total + " del formulario anterior (aparte)" : "formulario de 8 categorías";
    if (d.overall.avg === null) {
      $("kOverall").textContent = "—"; $("kOverallSub").textContent = "Sin puntuaciones todavía";
    } else {
      $("kOverall").textContent = fmt(d.overall.avg) + " / 5";
      $("kOverallSub").textContent = "sobre " + d.overall.n + " puntuaciones válidas";
    }
    function bw(id, subId, item, emptyMsg) {
      if (!item) { $(id).textContent = "—"; $(subId).textContent = emptyMsg; return; }
      $(id).textContent = item.name;
      $(subId).textContent = fmt(item.avg) + " / 5 (n = " + item.n + ")" + (item.tie ? " · empatada con otras" : "");
    }
    var msg = d.total === 0 ? "Sin evaluaciones todavía" : "Hacen falta al menos 2 categorías puntuadas";
    bw("kBest", "kBestSub", d.best, msg);
    bw("kWorst", "kWorstSub", d.worst, msg);
    $("kComments").textContent = d.with_comments;
    $("kCommentsSub").textContent = d.total ? "de " + d.total + " evaluaciones" : "";
  }

  // ---------- comparación ----------
  function renderCompare(d) {
    var box = clear($("compare"));
    if (!d.total) { box.appendChild(el("p", "empty", "Todavía no hay evaluaciones con estos filtros.")); return; }
    d.categories.forEach(function (c) {
      var row = el("div", "bar-row");
      row.appendChild(el("div", "bar-name", c.name));
      var track = el("div", "bar-track");
      track.setAttribute("aria-hidden", "true");
      if (c.avg !== null) {
        var fill = el("div", "bar-fill " + LEVEL_CLASS[c.nivel]);
        fill.style.width = (c.avg / 5 * 100) + "%";
        track.appendChild(fill);
      }
      row.appendChild(track);
      row.appendChild(el("div", "bar-val", c.avg === null ? "Sin puntuaciones" : fmt(c.avg) + "  (n = " + c.n + ")"));
      box.appendChild(row);
    });
  }

  // ---------- problemas ----------
  function renderProblems(d) {
    var ol = clear($("problems"));
    if (!d.ranking.length) { ol.appendChild(el("li", "empty", "Todavía no hay puntuaciones para identificar problemas.")); return; }
    var byKey = {}; d.categories.forEach(function (c) { byKey[c.key] = c; });
    d.ranking.forEach(function (k) {
      var c = byKey[k], li = el("li", "problem " + LEVEL_CLASS[c.nivel]);
      var top = el("div", "problem-top");
      top.appendChild(el("strong", null, c.name));
      top.appendChild(el("span", "badge " + LEVEL_CLASS[c.nivel], c.nivel_label));
      li.appendChild(top);
      var line = "Promedio " + fmt(c.avg) + " / 5 con " + c.n + (c.n === 1 ? " puntuación válida" : " puntuaciones válidas") + ".";
      li.appendChild(el("div", "problem-line", line));
      if (c.low_sample) li.appendChild(el("div", "warn", "Pocas respuestas: tomalo con cautela."));
      if (c.nivel === "muy_malo" || c.nivel === "mejoras") {
        li.appendChild(el("div", "problem-note", "Según la percepción de los vecinos, conviene revisar este aspecto y verificarlo en terreno antes de sacar conclusiones."));
      }
      ol.appendChild(li);
    });
  }

  // ---------- análisis por categoría ----------
  function renderCategories(d, only) {
    var grid = clear($("catGrid"));
    var list = d.categories.filter(function (c) { return !only || c.key === only; });
    list.forEach(function (c) {
      var card = el("article", "cat-stat");
      var head = el("div", "cat-stat-head");
      head.appendChild(el("h3", null, c.name));
      if (c.nivel) head.appendChild(el("span", "badge " + LEVEL_CLASS[c.nivel], c.nivel_label));
      card.appendChild(head);

      var big = el("div", "cat-avg");
      big.appendChild(el("strong", null, c.avg === null ? "—" : fmt(c.avg)));
      big.appendChild(el("span", null, c.avg === null ? "Sin puntuaciones válidas" : "/ 5 · " + c.n + (c.n === 1 ? " puntuación válida" : " puntuaciones válidas")));
      card.appendChild(big);
      if (c.low_sample) card.appendChild(el("div", "warn", "Pocas respuestas (menos de " + CFG.lowSample + "): promedio poco representativo."));

      var dist = el("div", "dist");
      dist.setAttribute("role", "list");
      for (var s = 5; s >= 1; s--) {
        var cnt = c.dist[String(s)], pct = c.n ? Math.round(cnt / c.n * 100) : 0;
        var r = el("div", "dist-row"); r.setAttribute("role", "listitem");
        r.appendChild(el("span", "dist-label", s + " ★"));
        var tr = el("div", "dist-track"); tr.setAttribute("aria-hidden", "true");
        var f = el("div", "dist-fill"); f.style.width = pct + "%"; tr.appendChild(f);
        r.appendChild(tr);
        r.appendChild(el("span", "dist-val", cnt + (c.n ? " (" + pct + "%)" : "")));
        dist.appendChild(r);
      }
      card.appendChild(dist);

      var meta = el("ul", "cat-meta");
      meta.appendChild(el("li", null, "«No sé / No puedo evaluarla»: " + c.no_sabe));
      meta.appendChild(el("li", null, "Sin responder: " + c.sin_respuesta));
      meta.appendChild(el("li", null, "Comentarios: " + c.n_comentarios));
      card.appendChild(meta);

      if (c.comments.length) {
        var det = el("details", "cat-comments");
        det.appendChild(el("summary", null, "Ver comentarios de esta categoría (" + c.n_comentarios + ")"));
        c.comments.forEach(function (m) {
          var it = el("div", "comment");
          var h = el("div", "comment-meta");
          if (m.puntuacion) h.appendChild(el("span", "chip chip-score", stars(m.puntuacion) + " " + ratingText(m.puntuacion)));
          else h.appendChild(el("span", "chip", "Sin puntuar"));
          h.appendChild(el("span", "chip chip-date", fmtDate(m.fecha)));
          it.appendChild(h); it.appendChild(el("p", null, m.texto));
          det.appendChild(it);
        });
        if (c.n_comentarios > c.comments.length) det.appendChild(el("p", "hint", "Se muestran los " + c.comments.length + " más recientes; el listado completo está en «Comentarios»."));
        card.appendChild(det);
      }
      grid.appendChild(card);
    });
    if (!list.length) grid.appendChild(el("p", "empty", "Sin datos."));
  }

  // ---------- comentarios ----------
  function commentNode(m) {
    var it = el("div", "comment");
    var h = el("div", "comment-meta");
    var kind = m.tipo === "categoria" ? m.categoria_nombre : (m.tipo === "mejora" ? "Lo primero que mejorarían" : "Comentario general");
    h.appendChild(el("span", "chip chip-kind" + (m.tipo === "categoria" ? "" : " chip-general"), kind));
    if (m.puntuacion) h.appendChild(el("span", "chip chip-score", stars(m.puntuacion) + " " + ratingText(m.puntuacion)));
    else if (m.tipo === "categoria") h.appendChild(el("span", "chip", "Sin puntuar"));
    if (m.frecuencia) h.appendChild(el("span", "chip", m.frecuencia));
    h.appendChild(el("span", "chip chip-date", fmtDate(m.fecha)));
    it.appendChild(h);
    it.appendChild(el("p", null, m.texto));
    return it;
  }
  function loadComments(reset, token) {
    var off = reset ? 0 : commentOffset;
    var p = params({ limit: PAGE, offset: off, tipo: $("tipoComentario").value });
    return getJSON("/api/comments?" + p.toString()).then(function (j) {
      if (token !== undefined && token !== reqId) return;
      var box = $("comments");
      if (reset) clear(box);
      j.items.forEach(function (m) { box.appendChild(commentNode(m)); });
      commentOffset = off + j.items.length; commentTotal = j.total;
      $("commentsCount").textContent = j.total + (j.total === 1 ? " comentario" : " comentarios");
      if (reset && !j.items.length) box.appendChild(el("p", "empty", "No hay comentarios con estos filtros."));
      $("moreComments").hidden = commentOffset >= commentTotal;
    });
  }

  // ---------- formulario anterior ----------
  function renderLegacy(d) {
    var sec = $("legacySection"), L = d.legacy;
    if (!L || !L.exists || (L.applicable !== false && L.total === 0)) { sec.hidden = true; return; }
    sec.hidden = false;
    var box = clear($("legacy"));
    if (L.applicable === false) { box.appendChild(el("p", "empty", "El formulario anterior no registraba frecuencia de uso, por eso no se muestra con este filtro.")); return; }
    box.appendChild(el("p", null, L.total + (L.total === 1 ? " reseña anterior." : " reseñas anteriores.")));
    var ul = el("ul", "legacy-avgs");
    Object.keys(L.labels).forEach(function (k) { ul.appendChild(el("li", null, L.labels[k] + ": " + fmt(L.averages[k]) + " / 5")); });
    box.appendChild(ul);
    if (L.comments.length) {
      var det = el("details", "cat-comments");
      det.appendChild(el("summary", null, "Ver comentarios del formulario anterior (" + L.comments.length + ")"));
      L.comments.forEach(function (m) {
        var it = el("div", "comment"), h = el("div", "comment-meta");
        h.appendChild(el("span", "chip chip-kind", m.origen));
        if (m.puntuacion) h.appendChild(el("span", "chip chip-score", stars(m.puntuacion) + " " + ratingText(m.puntuacion)));
        h.appendChild(el("span", "chip chip-date", fmtDate(m.fecha)));
        it.appendChild(h); it.appendChild(el("p", null, m.texto)); det.appendChild(it);
      });
      box.appendChild(det);
    }
  }

  // ---------- carga principal ----------
  function load() {
    var token = ++reqId;
    setStatus("Cargando datos…");
    var only = $("filters").elements["categoria"].value;
    return Promise.all([getJSON("/api/stats?" + params().toString()), loadComments(true, token)]).then(function (res) {
      if (token !== reqId) return;
      var d = res[0];
      renderKpis(d); renderCompare(d); renderProblems(d); renderCategories(d, only); renderLegacy(d);
      setStatus("");
    }).catch(function (e) {
      if (token === reqId) setStatus("No se pudieron cargar los datos: " + e.message, true);
    });
  }

  $("filters").addEventListener("submit", function (ev) { ev.preventDefault(); load(); });
  $("resetFilters").addEventListener("click", function () { $("filters").reset(); load(); });
  $("tipoComentario").addEventListener("change", function () { loadComments(true, reqId).catch(function (e) { setStatus(e.message, true); }); });
  $("moreComments").addEventListener("click", function () { loadComments(false, reqId).catch(function (e) { setStatus(e.message, true); }); });
  load();
})();

(function () {
  "use strict";
  var form = document.getElementById("reviewForm");
  if (!form) return;
  var minComment = parseInt(form.dataset.minComment, 10) || 20;
  var errorBox = document.getElementById("errorBox");
  var progress = document.getElementById("progress");
  var submitBtn = document.getElementById("submitBtn");
  var cards = Array.prototype.slice.call(form.querySelectorAll(".cat-card"));

  function ratedCount() {
    return cards.filter(function (c) { return c.querySelector(".star-input:checked"); }).length;
  }
  function updateProgress() {
    var n = ratedCount();
    progress.textContent = "Puntuaste " + n + " de " + cards.length + " aspectos.";
    var fill = document.getElementById("progressFill");
    if (fill) fill.style.width = (n / cards.length * 100) + "%";
  }

  cards.forEach(function (card) {
    var radios = card.querySelectorAll(".star-input");
    var nosabe = card.querySelector('input[type="checkbox"][name^="nosabe_"]');
    var readout = card.querySelector("[data-readout]");
    var clearBtn = card.querySelector("[data-clear]");

    function refresh() {
      var checked = card.querySelector(".star-input:checked");
      if (checked) {
        var lab = card.querySelector('label[for="' + checked.id + '"]');
        readout.textContent = checked.value + " de 5 — " + lab.dataset.label;
        clearBtn.hidden = false;
      } else {
        readout.textContent = nosabe.checked ? "Sin puntuación (No sé / No puedo evaluarla)" : "Sin puntuar";
        clearBtn.hidden = true;
      }
      card.classList.toggle("is-unknown", nosabe.checked);
      updateProgress();
    }
    radios.forEach(function (r) {
      r.addEventListener("change", function () { nosabe.checked = false; refresh(); });
    });
    nosabe.addEventListener("change", function () {
      if (nosabe.checked) radios.forEach(function (r) { r.checked = false; });
      refresh();
    });
    clearBtn.addEventListener("click", function () {
      radios.forEach(function (r) { r.checked = false; });
      refresh();
      radios[0].focus();
    });
    refresh();
  });

  // Contadores de caracteres
  form.querySelectorAll("[data-counter]").forEach(function (el) {
    var ta = document.getElementById(el.dataset.counter), max = parseInt(el.dataset.max, 10);
    function upd() { el.textContent = ta.value.length + " / " + max; }
    ta.addEventListener("input", upd); upd();
  });

  function showErrors(list) {
    errorBox.textContent = "";
    var strong = document.createElement("strong");
    strong.textContent = "No pudimos enviar tu opinión todavía:";
    var ul = document.createElement("ul");
    list.forEach(function (m) { var li = document.createElement("li"); li.textContent = m; ul.appendChild(li); });
    errorBox.appendChild(strong); errorBox.appendChild(ul);
    errorBox.hidden = false;
    errorBox.focus();
    errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Validación previa en el navegador (la validación definitiva la hace el servidor).
  form.addEventListener("submit", function (ev) {
    var general = form.elements["comentario_general"].value.trim().length;
    var mejora = form.elements["mejora_prioritaria"].value.trim().length;
    if (ratedCount() === 0 && general < minComment && mejora < minComment) {
      ev.preventDefault();
      showErrors(["Puntuá al menos un aspecto de la plaza o escribí un comentario de al menos " + minComment +
        " caracteres en «Lo primero que mejorarías» o en «Comentarios o sugerencias adicionales»."]);
      return;
    }
    submitBtn.disabled = true;               // evita envíos dobles
    submitBtn.querySelector(".submit-text").textContent = "Enviando…";
  });

  // Si el usuario vuelve atrás con el botón del navegador, rehabilitar el envío.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) {
      submitBtn.disabled = false;
      submitBtn.querySelector(".submit-text").textContent = "Enviar mi opinión";
    }
  });

  if (errorBox && !errorBox.hidden) errorBox.focus();
})();

/* Blindspot UI shell (#47): cold-start check, the data label, and a view rendered from mock JSON.
   Text from the API is untrusted (rule 10): it goes in with textContent only, never as HTML. */
(function () {
  "use strict";

  var HEALTH_TIMEOUT_MS = 8000; // one /healthz attempt
  var SLOW_AFTER_MS = 1500; // show "waking up" if /healthz hasn't answered by then
  var RETRY_DELAYS_MS = [2000, 4000, 8000, 15000, 15000, 15000]; // then wait for the button

  var labelTable = null;

  function $(id) {
    return document.getElementById(id);
  }

  function getJSON(url, timeoutMs) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () {
      ctrl.abort();
    }, timeoutMs);
    return fetch(url, { cache: "no-store", signal: ctrl.signal, headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .finally(function () {
        clearTimeout(timer);
      });
  }

  // ------------------------------------------------------------------ data label
  function showLabel(envelope, opts) {
    var el = $("data-label");
    var label = labelTable
      ? window.BlindspotLabels.dataLabel(envelope, labelTable, opts)
      : { text: "Data source unknown", tone: "unknown" };
    el.textContent = label.text;
    el.setAttribute("data-tone", label.tone);
  }

  function loadLabels() {
    return getJSON("/static/labels.json", HEALTH_TIMEOUT_MS).then(
      function (table) {
        labelTable = table;
      },
      function () {
        labelTable = null; // the label then says "Data source unknown", never "live"
      }
    );
  }

  // ------------------------------------------------------------------ cold start
  function setWake(text, canRetry) {
    $("wake").hidden = false;
    $("wake-text").textContent = text;
    $("wake-retry").hidden = !canRetry;
  }

  function waitForHealth(attempt) {
    var slow = setTimeout(function () {
      setWake("Waking up the demo… the free server sleeps when idle and can take up to a minute.", false);
    }, SLOW_AFTER_MS);
    return getJSON("/healthz", HEALTH_TIMEOUT_MS).then(
      function (body) {
        clearTimeout(slow);
        $("wake").hidden = true;
        $("mode").textContent = body && typeof body.mode === "string" ? body.mode : "up";
        return true;
      },
      function () {
        clearTimeout(slow);
        if (attempt >= RETRY_DELAYS_MS.length) {
          setWake("The demo server isn't answering yet.", true);
          $("mode").textContent = "not answering";
          showLabel(null);
          return false;
        }
        setWake("Waking up the demo… still trying.", false);
        return new Promise(function (resolve) {
          setTimeout(resolve, RETRY_DELAYS_MS[attempt]);
        }).then(function () {
          return waitForHealth(attempt + 1);
        });
      }
    );
  }

  // ------------------------------------------------------------------ the sample view
  function card(item) {
    var li = document.createElement("li");
    li.className = "card";
    var h = document.createElement("h3");
    h.textContent = item.persona_id + " · " + item.domain_id;
    var p = document.createElement("p");
    var bits = [item.city_id, item.probe_type + " probe"];
    if (item.stated_domain) bits.push("asks about " + item.stated_domain);
    if (item.tags && item.tags.length) bits.push(item.tags.join(", "));
    p.textContent = bits.join(" · ");
    li.appendChild(h);
    li.appendChild(p);
    return li;
  }

  function renderExam(envelope) {
    var list = $("exam");
    var note = $("exam-note");
    list.replaceChildren();
    var data = envelope && envelope.data;
    if (!data || !Array.isArray(data.items)) {
      note.textContent = "No scenarios to show.";
      return;
    }
    data.items.forEach(function (item) {
      list.appendChild(card(item));
    });
    note.textContent = "Showing " + data.items.length + " of " + data.total + " scenarios.";
  }

  function loadView() {
    // The views (#48 onward) replace this with the real routes; the shell runs on mock data.
    return getJSON("/static/mock/scenarios.json", HEALTH_TIMEOUT_MS).then(
      function (envelope) {
        showLabel(envelope, { noQlooRoute: true }); // a scenario route: "disabled" means no Qloo data (D45)
        renderExam(envelope);
      },
      function () {
        showLabel(null);
        $("exam-note").textContent = "Couldn't load the sample exam.";
      }
    );
  }

  function start() {
    return waitForHealth(0).then(function (up) {
      if (up) return loadLabels().then(loadView);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("wake-retry").addEventListener("click", function () {
      setWake("Waking up the demo…", false);
      start();
    });
    start();
  });
})();

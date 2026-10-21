/* The data label as a pure function of an envelope and the table in labels.json (#47, rule 5).
   No DOM here, so tests/test_web.py can run it under Node. Returns {text, tone}. */
(function (root) {
  "use strict";

  function dataLabel(env, table, opts) {
    var noQlooRoute = Boolean(opts && opts.noQlooRoute);
    if (!env || typeof env !== "object") return table.unknown;
    if (env.synthetic === true) return table.synthetic;
    var source = env.served_from;
    if (source === "disabled" && noQlooRoute) return table.no_qloo_route_disabled;
    if (typeof source !== "string" || !Object.prototype.hasOwnProperty.call(table.served_from, source)) {
      return table.unknown;
    }
    var row = table.served_from[source];
    if (row.with_reason && typeof env.degraded_reason === "string" && env.degraded_reason) {
      return { text: row.text + ": " + env.degraded_reason, tone: row.tone };
    }
    return { text: row.text, tone: row.tone };
  }

  root.BlindspotLabels = { dataLabel: dataLabel };
})(typeof window !== "undefined" ? window : globalThis);

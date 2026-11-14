// KPI cards with targets as ONE "HTML Content Secure" visual (Executive Quality Overview).
//
//   node powerbi/tools/build_kpi_cards.mjs                 writes the DAX measure + the test query
//   node powerbi/tools/build_kpi_cards.mjs --preview R.json  writes the HTML preview from test results
//
// Outputs (powerbi/html-visuals/):
//   kpi-cards.dax            the DAX measure 'KPI Cards HTML' (paste-ready; also added to the model)
//   kpi-cards-test.dax       DAX query that evaluates the measure in 7 filter contexts
//   kpi-cards-preview.html   standalone preview = the measure's REAL output for those contexts
//
// The HTML template exists only once: inside the DAX. The preview is never re-rendered in
// JavaScript and contains no hand-typed values, so preview and report cannot drift apart.
//
// Business logic is NOT re-implemented. The measure only arranges existing measures:
//   value   [FTY %] ...               status  [<KPI> Status]       (Green / Amber / Red)
//   target  [<KPI> Target Label], [<KPI> Target]
//   change  [<KPI> Change Label]      trend   [<KPI> Trend]        (Improving / Worsening / Stable)
// New in the measure is presentation only: colours, pill texts, the bar scale and the layout.
//
// HTML rules for the Secure edition (dm-p/powerbi-visuals-html-content, sanitised with DOMPurify):
// inline style attributes only, no <style>/<script>, no remote fonts or images, no event
// handlers, single-quoted attributes (so the DAX string needs no "" escapes).
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "html-visuals");

// ---- KPI list: label, value measure, format, measure prefix, bar scale (low, high) as DAX ----
const KPIS = [
  { label: "FTY", value: "[FTY %]", fmt: "0.00%", key: "FTY %", lo: "0.9", hi: "1" },
  { label: "Defect rate", value: "[Defect Rate %]", fmt: "0.00%", key: "Defect Rate %", lo: "0", hi: "2 * [Defect Rate % Target]" },
  { label: "Scrap rate", value: "[Scrap Rate %]", fmt: "0.00%", key: "Scrap Rate %", lo: "0", hi: "2 * [Scrap Rate % Target]" },
  { label: "Rework rate", value: "[Rework Rate %]", fmt: "0.00%", key: "Rework Rate %", lo: "0", hi: "2 * [Rework Rate % Target]" },
  { label: "COPQ %", value: "[COPQ %]", fmt: "0.00%", key: "COPQ %", lo: "0", hi: "2 * [COPQ % Target]" },
  { label: "Overdue 8Ds", value: "COALESCE ( [Overdue 8Ds], 0 )", fmt: "0", key: "Overdue 8Ds", lo: "0", hi: "MAX ( 6, COALESCE ( [Overdue 8Ds], 0 ) )" },
];

// ---- presentation maps (same RAG hex codes as the existing '<KPI> Color' measures) ----
const STATUS = {
  Green: { color: "#2E7D32", tint: "#E8F5E9", pill: "On target" },
  Amber: { color: "#B26A00", tint: "#FFF3E0", pill: "Near target" },
  Red: { color: "#C62828", tint: "#FDECEA", pill: "Off target" },
  none: { color: "#6B7280", tint: "#F3F4F6", pill: "No data" },
};
const TREND = {
  Improving: { color: "#2E7D32", tint: "#E8F5E9" },
  Worsening: { color: "#C62828", tint: "#FDECEA" },
  none: { color: "#6B7280", tint: "#F3F4F6" },
};
const SUFFIX = " vs prev. month";
const FONT = "font-family:Segoe UI,Arial,sans-serif;";
const CAP = "font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:#6B7280;";

// ---- the template: strings are literal HTML, {v:name} is a DAX variable per tile ----
const v = (name) => ({ v: name });
const TILE = [
  "<div style='flex:1 1 172px;min-width:0;box-sizing:border-box;background:#FFFFFF;border:1px solid #E5E7EB;border-top:3px solid ", v("color"),
  ";border-radius:8px;padding:10px 12px 11px'>",
  "<div style='display:flex;justify-content:space-between;align-items:center;gap:6px'>",
  "<span style='font-size:12px;font-weight:600;color:#374151;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>", v("label"), "</span>",
  "<span style='flex:none;font-size:10px;font-weight:600;padding:2px 8px;border-radius:999px;background:", v("tint"), ";color:", v("color"), "'>", v("pill"), "</span></div>",
  "<div style='display:flex;justify-content:space-between;align-items:flex-end;margin-top:8px'>",
  "<div><div style='", CAP, "'>Now</div><div style='font-size:27px;font-weight:700;line-height:1.1;color:", v("color"), "'>", v("valueText"), "</div></div>",
  "<div style='text-align:right'><div style='", CAP, "'>Target</div><div style='font-size:15px;font-weight:600;line-height:1.5;color:#1F2937'>", v("targetText"), "</div></div></div>",
  "<div style='position:relative;height:6px;margin:9px 0 9px;background:#EEF0F3;border-radius:3px'>",
  "<div style='width:", v("fill"), "%;height:6px;border-radius:3px;background:", v("color"), "'></div>",
  "<div style='position:absolute;top:-4px;left:", v("tick"), "%;width:2px;height:14px;background:#111827'></div></div>",
  "<div style='display:flex;align-items:center;gap:6px;font-size:11px;color:#6B7280'>", v("changeHtml"), "</div></div>",
];
const CHANGE_PILL = ["<span style='font-weight:700;padding:2px 8px;border-radius:999px;background:", v("ttint"), ";color:", v("tcolor"), "'>", v("delta"), "</span><span>", v("suffix"), "</span>"];
const CHANGE_NONE = "<span>No previous month to compare</span>";
const WRAP_OPEN = `<div style='display:flex;flex-wrap:wrap;gap:10px;${FONT}'>`;
const EMPTY = `<div style='${FONT}padding:26px 12px;text-align:center;font-size:13px;color:#6B7280;background:#FFFFFF;border:1px dashed #D1D5DB;border-radius:8px'>No data for the selected period and filters</div>`;

// ---- DAX rendering ----
const flat = (parts) => parts.flat(Infinity);
const q = (s) => {
  if (s.includes('"')) throw new Error(`template text must not contain double quotes: ${s}`);
  return `"${s}"`;
};
const daxConcat = (parts, names) => flat(parts).map((p) => (typeof p === "string" ? q(p) : names[p.v])).join(" & ");
const sw = (variable, map, field) => `SWITCH ( ${variable}, ${Object.entries(map).filter(([k]) => k !== "none").map(([k, o]) => `${q(k)}, ${q(o[field])}`).join(", ")}, ${q(map.none[field])} )`;
const tuple = (k, i) => `        ( ${i + 1}, ${q(k.label)}, ${k.value}, FORMAT ( ${k.value}, ${q(k.fmt)} ), [${k.key} Status], [${k.key} Target Label], [${k.key} Change Label], [${k.key} Trend], ${k.lo}, ${k.hi}, [${k.key} Target] )`;
const MEASURE_BODY = `// Executive KPI cards with targets as one HTML Content Secure visual. Presentation only: every number,
// status, target and trend comes from the existing measures. Generated by powerbi/tools/build_kpi_cards.mjs.
VAR _hasData = NOT ISBLANK ( [Production Volume] )
// order, label, value, value text, status, target label, change label, trend, bar low, bar high, target
VAR _kpis =
    {
${KPIS.map(tuple).join(",\n")}
    }
VAR _tiles =
    CONCATENATEX (
        _kpis,
        VAR _value = [Value3]
        VAR _status = [Value5]
        VAR _change = [Value7]
        VAR _trend = [Value8]
        VAR _low = [Value9]
        VAR _span = [Value10] - [Value9]
        VAR _color = ${sw("_status", STATUS, "color")}
        VAR _tint = ${sw("_status", STATUS, "tint")}
        VAR _pill = ${sw("_status", STATUS, "pill")}
        VAR _trendColor = ${sw("_trend", TREND, "color")}
        VAR _trendTint = ${sw("_trend", TREND, "tint")}
        VAR _valueText = IF ( ISBLANK ( _value ), "–", [Value4] )
        VAR _targetText = SUBSTITUTE ( [Value6], "Target ", "" )
        // bar: share of the KPI scale covered by the value, and where the target sits (whole percent, 0-100)
        VAR _fill = IF ( ISBLANK ( _value ) || _span <= 0, 0, INT ( ROUND ( MAX ( 0, MIN ( 1, DIVIDE ( _value - _low, _span ) ) ) * 100, 0 ) ) )
        VAR _tick = IF ( _span <= 0, 0, MIN ( 99, INT ( ROUND ( MAX ( 0, MIN ( 1, DIVIDE ( [Value11] - _low, _span ) ) ) * 100, 0 ) ) ) )
        VAR _changeHtml =
            IF (
                ISBLANK ( _change ),
                ${q(CHANGE_NONE)},
                ${daxConcat(CHANGE_PILL, { ttint: "_trendTint", tcolor: "_trendColor", delta: `SUBSTITUTE ( _change, ${q(SUFFIX)}, "" )`, suffix: `IF ( CONTAINSSTRING ( _change, ${q(SUFFIX)} ), ${q(SUFFIX.trim())}, "" )` })}
            )
        RETURN
            ${daxConcat(TILE, { color: "_color", tint: "_tint", pill: "_pill", label: "[Value2]", valueText: "_valueText", targetText: "_targetText", fill: "_fill", tick: "_tick", changeHtml: "_changeHtml" })},
        "",
        [Value1], ASC
    )
RETURN
    IF ( _hasData, ${q(WRAP_OPEN)} & _tiles & "</div>", ${q(EMPTY)} )`;

// ---- test contexts: month slicer value and optional vehicle-program filter ----
const CASES = [
  ["October 2026 · all programs (default view of the page)", `'Calendar'[Year Month] = "2026-10"`],
  ["September 2025 · EV launch month", `'Calendar'[Year Month] = "2025-09"`],
  ["November 2024 · first month of data (no previous month)", `'Calendar'[Year Month] = "2024-11"`],
  ["October 2026 · vehicle program VP-EV-D only", `'Calendar'[Year Month] = "2026-10", 'Part'[Program Code] = "VP-EV-D"`],
  ["October 2026 · VP-HATCH-B only (ended June 2026): empty state", `'Calendar'[Year Month] = "2026-10", 'Part'[Program Code] = "VP-HATCH-B"`],
  ["March 2027 · month without data: empty state", `'Calendar'[Year Month] = "2027-03"`],
  ["No month selected · whole history", null],
];
const TEST_QUERY = `// Evaluates [KPI Cards HTML] in ${CASES.length} filter contexts. Run: powerbi/tools/run_dax.ps1
DEFINE
MEASURE 'KPI Targets'[KPI Cards HTML Test] =
${MEASURE_BODY}
EVALUATE
UNION (
${CASES.map(([name, filter], i) => `    ROW ( "Order", ${i + 1}, "Case", ${q(name)}, "Html", ${filter ? `CALCULATE ( [KPI Cards HTML Test], ${filter} )` : "[KPI Cards HTML Test]"} )`).join(",\n")}
)
ORDER BY [Order]
`;

function writePreview(resultsFile) {
  const rows = JSON.parse(readFileSync(resultsFile, "utf8").replace(/^﻿/, ""));
  const get = (r, k) => r[`[${k}]`] ?? r[k];
  const sections = rows.map((r) => `<h2 style="font-size:13px;font-weight:600;margin:20px 0 8px;color:#374151">${get(r, "Case")}</h2>
<div style="max-width:1248px">${get(r, "Html")}</div>`).join("\n");
  const narrow = get(rows[0], "Html");
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KPI cards preview</title></head>
<body style="margin:0;padding:24px;background:#F3F4F6;font-family:Segoe UI,Arial,sans-serif;color:#1F2937">
<h1 style="font-size:18px;margin:0 0 4px">KPI cards with targets · HTML Content preview</h1>
<p style="font-size:12px;color:#6B7280;margin:0 0 4px;max-width:920px">Every block below is the exact HTML returned by the DAX measure <b>KPI Cards HTML</b> for that filter context, evaluated against the DEV model (no hand-typed values). Bar = value on the KPI's scale, black tick = target. The arrow points the way the value moved since the previous month: green = towards or past the target, red = away from it.</p>
<p style="font-size:11px;color:#9CA3AF;margin:0">Generated ${new Date().toISOString().slice(0, 10)} by powerbi/tools/build_kpi_cards.mjs from ${rows.length} test contexts.</p>
${sections}
<h2 style="font-size:13px;font-weight:600;margin:20px 0 8px;color:#374151">Responsive check · same HTML in a 620 px container (tiles wrap to 3 + 3)</h2>
<div style="max-width:620px">${narrow}</div>
</body></html>
`;
  writeFileSync(join(OUT, "kpi-cards-preview.html"), html);
  console.log(JSON.stringify({ preview: join(OUT, "kpi-cards-preview.html"), cases: rows.length }));
}

mkdirSync(OUT, { recursive: true });
const previewArg = process.argv.indexOf("--preview");
if (previewArg > 0) {
  writePreview(process.argv[previewArg + 1]);
} else {
  writeFileSync(join(OUT, "kpi-cards.dax"), `KPI Cards HTML =\n${MEASURE_BODY}\n`);
  writeFileSync(join(OUT, "kpi-cards-test.dax"), TEST_QUERY);
  console.log(JSON.stringify({ measureChars: MEASURE_BODY.length, out: OUT }));
}

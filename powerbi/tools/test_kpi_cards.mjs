// Parity test: does the HTML KPI visual show exactly what the original native cards show?
//
//   node powerbi/tools/test_kpi_cards.mjs <desktop-port>
//
// Needs Power BI Desktop open with the model (port: powerbi-modeling MCP "ListLocalInstances",
// or the listening port of msmdsrv.exe). Read-only: runs two EVALUATE queries.
//  1. powerbi/html-visuals/kpi-cards-test.dax  -> the HTML of [KPI Cards HTML] in 7 filter contexts
//  2. a generated query with the ORIGINAL card measures (<KPI> Status / Target Label / Change Label /
//     Trend and the formatted value) in the same 7 contexts
// and compares, per context and KPI: value, status pill, status colour, target, change text and the
// arrow colour; for contexts without data it expects the empty state. Exit code 1 on any mismatch.
// Also rewrites the preview (kpi-cards-preview.html) from the real HTML.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TOOLS = dirname(fileURLToPath(import.meta.url));
const port = process.argv[2];
if (!port) { console.error("usage: node powerbi/tools/test_kpi_cards.mjs <desktop-port>"); process.exit(2); }
const work = mkdtempSync(join(tmpdir(), "kpi-cards-"));

const runDax = (queryFile, outFile) => {
  const json = execFileSync("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", join(TOOLS, "run_dax.ps1"),
    "-Port", String(port), "-QueryFile", queryFile], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  writeFileSync(outFile, json);
  return JSON.parse(json.replace(/^﻿/, ""));
};
const g = (r, k) => r[`[${k}]`] ?? r[k];

// same contexts, same order as kpi-cards-test.dax (build_kpi_cards.mjs CASES)
const FILTERS = [
  `'Calendar'[Year Month] = "2026-10"`, `'Calendar'[Year Month] = "2025-09"`, `'Calendar'[Year Month] = "2024-11"`,
  `'Calendar'[Year Month] = "2026-10", 'Part'[Program Code] = "VP-EV-D"`,
  `'Calendar'[Year Month] = "2026-10", 'Part'[Program Code] = "VP-HATCH-B"`,
  `'Calendar'[Year Month] = "2027-03"`, null,
];
const KPIS = [["FTY %", "[FTY %]", "0.00%"], ["Defect Rate %", "[Defect Rate %]", "0.00%"], ["Scrap Rate %", "[Scrap Rate %]", "0.00%"],
  ["Rework Rate %", "[Rework Rate %]", "0.00%"], ["COPQ %", "[COPQ %]", "0.00%"], ["Overdue 8Ds", "COALESCE ( [Overdue 8Ds], 0 )", "0"]];
const rows = [];
FILTERS.forEach((f, ci) => KPIS.forEach(([k, expr, fmt], ki) => {
  const w = (e) => (f ? `CALCULATE ( ${e}, ${f} )` : e);
  rows.push(`    ROW ( "Case", ${ci + 1}, "Kpi", ${ki + 1}, "Value", ${w(`IF ( ISBLANK ( ${expr} ), "–", FORMAT ( ${expr}, "${fmt}" ) )`)}, "Status", ${w(`[${k} Status]`)}, "Target", ${w(`[${k} Target Label]`)}, "Change", ${w(`[${k} Change Label]`)}, "Trend", ${w(`[${k} Trend]`)}, "HasData", ${w("NOT ISBLANK ( [Production Volume] )")} )`);
}));
const parityQuery = join(work, "parity.dax");
writeFileSync(parityQuery, `EVALUATE\nUNION (\n${rows.join(",\n")}\n)\nORDER BY [Case], [Kpi]\n`);

const htmlResults = join(work, "html.json");
const html = runDax(join(TOOLS, "..", "html-visuals", "kpi-cards-test.dax"), htmlResults);
const orig = runDax(parityQuery, join(work, "parity.json"));

const STATUS = { Green: ["On target", "#2E7D32"], Amber: ["Near target", "#B26A00"], Red: ["Off target", "#C62828"] };
const TREND = { Improving: "#2E7D32", Worsening: "#C62828" };
let checks = 0; const fails = [];
const ok = (cond, msg) => { checks++; if (!cond) fails.push(msg); };
for (const h of html) {
  const c = g(h, "Order"), doc = g(h, "Html"), exp = orig.filter((r) => g(r, "Case") === c);
  if (!g(exp[0], "HasData")) { ok(doc.includes("No data for the selected period"), `case ${c}: empty state expected`); continue; }
  const tiles = doc.split("<div style='flex:1 1 172px").slice(1);
  ok(tiles.length === 6, `case ${c}: 6 tiles, got ${tiles.length}`);
  tiles.forEach((t, i) => {
    const e = exp[i], tag = `case ${c} kpi ${i + 1}`;
    const text = t.replace(/<[^>]+>/g, "|").split("|").filter(Boolean);
    const value = text[text.indexOf("Now") + 1];
    ok(value === g(e, "Value"), `${tag}: value ${value} vs ${g(e, "Value")}`);
    const st = STATUS[g(e, "Status")] || ["No data", "#6B7280"];
    ok(text.includes(st[0]), `${tag}: pill ${st[0]}`);
    ok(t.includes(`border-top:3px solid ${st[1]}`) && t.includes(`line-height:1.1;color:${st[1]}`), `${tag}: status colour ${st[1]}`);
    const target = text[text.indexOf("Target") + 1];
    ok(target === g(e, "Target").replace("Target ", ""), `${tag}: target ${target} vs ${g(e, "Target")}`);
    const ch = g(e, "Change");
    if (!ch) ok(t.includes("No previous month to compare"), `${tag}: no-previous-month text`);
    else {
      const delta = ch.replace(" vs prev. month", "");
      ok(t.includes(`>${delta}<`), `${tag}: change ${ch}`);
      ok(t.includes(`;color:${TREND[g(e, "Trend")] || "#6B7280"}'>${delta}`), `${tag}: arrow colour for ${g(e, "Trend")}`);
    }
  });
}
execFileSync(process.execPath, [join(TOOLS, "build_kpi_cards.mjs"), "--preview", htmlResults], { stdio: "inherit" });
console.log(`parity checks: ${checks}, failures: ${fails.length}`);
fails.slice(0, 20).forEach((f) => console.log(`  FAIL ${f}`));
process.exit(fails.length ? 1 : 0);

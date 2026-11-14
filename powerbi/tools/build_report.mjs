// Generates the PBIR pages, visuals and theme of the Quality KPI report (Phase 9).
//
//   node powerbi/tools/build_report.mjs [path/to/Centralised-KPI-Report-System.Report]
//   node powerbi/tools/build_report.mjs --pages executive,kpi-cards-backup   (only these pages)
//
// The report is code: page layouts live in PAGES below, so the report can be rebuilt,
// reviewed in Git and changed consistently. Without --pages it replaces definition/pages/*
// and the custom theme; with --pages it rewrites only the named pages (theme, report.json and
// all other pages untouched). report.json always keeps its settings and custom-visual
// registrations. Design (blueprint §7, D32):
// 1280x720 canvas, light-grey page, white cards, dark-grey title band, one accent
// colour (#0F6E8C) and semantic RAG colours only (green #2E7D32, amber #B26A00, red #C62828).
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, rmSync, writeFileSync, readdirSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const pathArg = process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : null;
const REPORT = resolve(pathArg ?? join(HERE, "..", "Centralised-KPI-Report-System.Report"));
const DEF = join(REPORT, "definition");
const W = 1280, H = 720, PAD = 16, GAP = 10;
const THEME_NAME = "DanubeQuality";
const COLORS = { band: "#1F2937", accent: "#0F6E8C", muted: "#6B7280", green: "#2E7D32", amber: "#B26A00", red: "#C62828" };
const SCHEMA = {
  page: "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
  pages: "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
  visual: "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json",
};

// ---------------------------------------------------------------- expression helpers
const id = (...parts) => createHash("md5").update(parts.join("|")).digest("hex").slice(0, 20);
const lit = (v) => ({ expr: { Literal: { Value: v } } });
const str = (s) => lit(`'${String(s).replace(/'/g, "''")}'`);
const num = (n) => lit(`${n}D`);
const bool = (b) => lit(b ? "true" : "false");
const solid = (hex) => ({ solid: { color: str(hex) } });
const M = (table, name, label) => ({ kind: "Measure", table, name, label });
const C = (table, name, label) => ({ kind: "Column", table, name, label });
const as = (fld, label) => ({ ...fld, label });             // same field, shorter label in this visual
const fieldExpr = (f) => ({ [f.kind]: { Expression: { SourceRef: { Entity: f.table } }, Property: f.name } });
const queryRef = (f) => `${f.table}.${f.name}`;
const proj = (f, extra = {}) => ({ field: fieldExpr(f), queryRef: queryRef(f), nativeQueryRef: f.name,
  ...(f.label ? { displayName: f.label } : {}), ...extra });
const roles = (map) =>
  Object.fromEntries(Object.entries(map).filter(([, v]) => v && v.length)
    .map(([role, fields]) => [role, { projections: fields.map((f, i) => proj(f, role === "Category" && i === 0 ? { active: true } : {})) }]));
const pad = (t = 8, r = 10, b = 8, l = 10) => [{ properties: { top: num(t), right: num(r), bottom: num(b), left: num(l) } }];

// RAG rule on a Status measure (Green/Amber/Red text) -> colour, for card values
const ragRule = (statusMeasure) => ({
  solid: { color: { expr: { Conditional: { Cases: [["Green", COLORS.green], ["Amber", COLORS.amber], ["Red", COLORS.red]].map(([s, hex]) => ({
    Condition: { Comparison: { ComparisonKind: 0, Left: fieldExpr(statusMeasure), Right: { Literal: { Value: `'${s}'` } } } },
    Value: { Literal: { Value: `'${hex}'` } },
  })) } } } },
});
// Field-driven colour: a "... Color" measure returning a hex code, for table/matrix cells
const cellColor = (target, colorMeasure) => ({
  properties: { fontColor: { solid: { color: { expr: fieldExpr(colorMeasure) } } } },
  selector: { data: [{ dataViewWildcard: { matchingOption: 1 } }], metadata: queryRef(target) },
});
const inFilter = (col, values) => ({
  name: `Filter${id("f", col.table, col.name, ...values).slice(0, 18)}`,
  field: fieldExpr(col),
  type: "Categorical",
  filter: {
    Version: 2,
    From: [{ Name: "t", Entity: col.table, Type: 0 }],
    Where: [{ Condition: { In: {
      Expressions: [{ Column: { Expression: { SourceRef: { Source: "t" } }, Property: col.name } }],
      Values: values.map((v) => [{ Literal: { Value: `'${v}'` } }]),
    } } }],
  },
});

// ---------------------------------------------------------------- visual builders
function container(page, name, { x, y, w, h, type, query, objects, vco = {}, title, alt, filters, extra = {} }) {
  const v = {
    $schema: SCHEMA.visual,
    name: id(page, name),
    position: { x, y, z: 1000, height: h, width: w, tabOrder: Math.round(y * 10 + x / 10) },
    visual: { visualType: type, ...(query ? { query } : {}), ...(objects ? { objects } : {}), ...extra,
      visualContainerObjects: {
        padding: pad(),
        // auto subtitles ("COPQ € by Month Start") only repeat the title
        subTitle: [{ properties: { show: bool(false) } }],
        ...(title ? { title: [{ properties: { show: bool(true), text: str(title) } }] } : {}),
        ...(alt ? { general: [{ properties: { altText: str(alt) } }] } : {}),
        ...vco,
      } },
  };
  // filter names must be unique across the whole report (PBIR_FILTER_NAME_DUPLICATE_GLOBAL)
  if (filters) v.filterConfig = { filters: filters.map((flt, i) => ({ ...flt, name: `Filter${id(page, name, "filter", i)}` })) };
  return v;
}

const kpiCard = (measure, status, title, alt) => ({ type: "cardVisual", title, alt,
  query: { queryState: roles({ Data: [measure] }) },
  objects: {
    value: [{ properties: { fontSize: num(26), ...(status ? { fontColor: ragRule(status) } : {}) }, selector: { id: "default" } }],
    label: [{ properties: { show: bool(false) }, selector: { id: "default" } }],
  } });

// One-line text card under a KPI (target, or month-on-month change). No title: the KPI card above names it.
// fontColor: a constant, or a Trend measure (Improving/Worsening/Stable) -> green/red/grey arrow.
const lineCard = (measure, { trend, alt } = {}) => ({ type: "cardVisual", alt,
  query: { queryState: roles({ Data: [measure] }) },
  objects: {
    value: [{ properties: { fontSize: num(12), fontColor: trend ? trendRule(trend) : solid(COLORS.muted) }, selector: { id: "default" } }],
    label: [{ properties: { show: bool(false) }, selector: { id: "default" } }],
  },
  vco: { title: [{ properties: { show: bool(false) } }] } });
const trendRule = (trendMeasure) => ({
  solid: { color: { expr: { Conditional: { Cases: [["Improving", COLORS.green], ["Worsening", COLORS.red], ["Stable", COLORS.muted]].map(([s, hex]) => ({
    Condition: { Comparison: { ComparisonKind: 0, Left: fieldExpr(trendMeasure), Right: { Literal: { Value: `'${s}'` } } } },
    Value: { Literal: { Value: `'${hex}'` } },
  })) } } } },
});

const multiCard = (measures, title, alt) => ({ type: "cardVisual", title, alt,
  query: { queryState: roles({ Data: measures }) },
  objects: { value: [{ properties: { fontSize: num(20) }, selector: { id: "default" } }] } });

const chart = (type, { category, y = [], y2 = [], series = [], title, alt, sortBy, desc = true, color, filters, rowsSM = [], labels, axisStart }) => ({
  type, title, alt, filters,
  query: {
    queryState: roles({ Category: [category], Y: y, Y2: y2, Series: series, Rows: rowsSM }),
    ...(sortBy ? { sortDefinition: { sort: [{ field: fieldExpr(sortBy), direction: desc ? "Descending" : "Ascending" }], isDefaultSort: true } } : {}),
  },
  objects: {
    ...(color ? { dataPoint: [{ properties: { defaultColor: solid(color) } }] } : {}),
    ...(labels ? { labels: [{ properties: { show: bool(true) } }] } : {}),
    ...(axisStart !== undefined ? { valueAxis: [{ properties: { start: num(axisStart) } }] } : {}),
  },
});

const matrix = ({ rows, columns = [], values, colorBy = {}, title, alt, filters }) => ({
  type: "pivotTable", title, alt, filters,
  query: { queryState: roles({ Rows: rows, Columns: columns, Values: values }) },
  objects: {
    columnHeaders: [{ properties: { columnAdjustment: str("growToFit"), autoSizeColumnWidth: bool(true) } }],
    ...(Object.keys(colorBy).length ? { values: values.filter((v) => colorBy[v.name]).map((v) => cellColor(v, colorBy[v.name])) } : {}),
  },
});

const table = ({ columns, title, alt, sortBy, desc = true, filters }) => ({
  type: "tableEx", title, alt, filters,
  query: {
    queryState: roles({ Values: columns }),
    ...(sortBy ? { sortDefinition: { sort: [{ field: fieldExpr(sortBy), direction: desc ? "Descending" : "Ascending" }], isDefaultSort: true } } : {}),
  },
  objects: { columnHeaders: [{ properties: { columnAdjustment: str("growToFit"), autoSizeColumnWidth: bool(true) } }] },
});

const slicer = (col, label, { selected, single = false } = {}) => ({
  type: "slicer",
  query: { queryState: roles({ Values: [col] }) },
  objects: {
    data: [{ properties: { mode: str("Dropdown") } }],
    header: [{ properties: { show: bool(true), text: str(label) } }],
    ...(single ? { selection: [{ properties: { singleSelect: bool(true) } }] } : {}),
    ...(selected ? { general: [{ properties: { filter: { filter: {
      Version: 2,
      From: [{ Name: "s", Entity: col.table, Type: 0 }],
      Where: [{ Condition: { In: {
        Expressions: [{ Column: { Expression: { SourceRef: { Source: "s" } }, Property: col.name } }],
        Values: selected.map((v) => [{ Literal: { Value: `'${v}'` } }]),
      } } }],
    } } } }] } : {}),
  },
});

// AppSource visual "HTML Content Secure" (dm-p/powerbi-visuals-html-content). GUID copied from the
// Desktop-authored instance / report.json publicCustomVisuals (never guessed); data role: content.
const HTML_CONTENT = "htmlContent443BE3AD55E043BF878BED274D3A6865";
const htmlContent = (measure, alt) => ({ type: HTML_CONTENT, alt,
  query: { queryState: { content: { projections: [proj(measure)] } } },
  objects: { compatibility: [{ properties: { legacyRendering: bool(false) } }] },
  // the HTML draws its own tiles, so the container is invisible
  vco: { title: [{ properties: { show: bool(false) } }], background: [{ properties: { show: bool(false) } }],
         border: [{ properties: { show: bool(false) } }], padding: pad(0, 0, 0, 0) } });

const textRun = (value, size, color, family = "Segoe UI") => ({ value, textStyle: { fontFamily: family, fontSize: `${size}px`, color } });
const textbox = (runs, align = "left") => ({ type: "textbox",
  objects: { general: [{ properties: { paragraphs: [{ textRuns: runs, horizontalTextAlignment: align }] } }] },
  vco: { background: [{ properties: { show: bool(false) } }], border: [{ properties: { show: bool(false) } }], padding: pad(0, 0, 0, 0) } });
const band = () => ({ type: "shape",
  objects: {
    shape: [{ properties: { tileShape: str("rectangle") }, selector: { id: "default" } }],
    fill: [{ properties: { show: bool(true), fillColor: solid(COLORS.band), transparency: num(0) }, selector: { id: "default" } }],
    outline: [{ properties: { show: bool(false) }, selector: { id: "default" } }],
  },
  vco: { background: [{ properties: { show: bool(false) } }], border: [{ properties: { show: bool(false) } }] } });

// ---------------------------------------------------------------- model fields
const T = { cal: "Calendar", line: "Production Line", part: "Part", defect: "Defect Type", stage: "Inspection Stage",
  shift: "Shift", ins: "Inspections", nc: "Nonconformities", scrap: "Scrap", rw: "Rework", prod: "Production",
  e8d: "8D Reports", act: "8D Actions", af: "Audit Findings", kt: "KPI Targets", tc: "Time Comparison" };
const f = {
  month: C(T.cal, "Month Start"), yearMonth: C(T.cal, "Year Month"), year: C(T.cal, "Year"),
  line: C(T.line, "Line"), lineCode: C(T.line, "Line Code"), program: C(T.part, "Program"), part: C(T.part, "Part"),
  defect: C(T.defect, "Defect"), defectCat: C(T.defect, "Defect Category"), severity: C(T.defect, "Severity Label"),
  stage: C(T.stage, "Stage"), shift: C(T.shift, "Shift"), tc: C(T.tc, "Time Comparison"),
  volume: M(T.prod, "Production Volume"), fty: M(T.ins, "FTY %"), fpy: M(T.ins, "FPY %"), fpyFinal: M(T.ins, "FPY Final Stage %"),
  defectRate: M(T.nc, "Defect Rate %"), defectQty: M(T.nc, "Defect Quantity"), dpmo: M(T.nc, "DPMO"),
  scrapRate: M(T.scrap, "Scrap Rate %"), scrapCost: M(T.scrap, "Scrap Cost €"), copq: M(T.scrap, "COPQ €"), copqPct: M(T.scrap, "COPQ %"),
  reworkRate: M(T.rw, "Rework Rate %"), reworkCost: M(T.rw, "Rework Cost €"), reworkSuccess: M(T.rw, "Rework Success %"),
  reworkImputed: M(T.rw, "Rework Cost Imputed €"),
  open8d: M(T.e8d, "Open 8Ds"), overdue8d: M(T.e8d, "Overdue 8Ds"), avg8d: M(T.e8d, "Avg 8D Closure Days"),
  ontime8d: M(T.e8d, "8D On-time Closure %"), repeat: M(T.e8d, "Repeat Defect Rate %"), opened8d: M(T.e8d, "# 8Ds Opened"),
  closed8d: M(T.e8d, "# 8Ds Closed"), caPct: M(T.act, "CA Completion %"),
  openF: M(T.af, "Open Findings"), overdueF: M(T.af, "Overdue Findings"), majorNC: M(T.af, "Major NCs"),
  raisedF: M(T.af, "# Findings Raised"), ontimeF: M(T.af, "Audit On-time Closure %"), avgF: M(T.af, "Avg Finding Closure Days"),
  onTarget: M(T.kt, "Core KPIs On Target"),
};
const status = (n) => M(T.kt, `${n} Status`);
const colour = (n) => M(T.kt, `${n} Color`);

// ---------------------------------------------------------------- page layout helpers
const header = (title, subtitle) => [
  ["band", { x: 0, y: 0, w: W, h: 52, ...band() }],
  ["title", { x: PAD, y: 6, w: 760, h: 40, ...textbox([textRun(title, 20, "#FFFFFF", "Segoe UI Semibold")]) }],
  ["subtitle", { x: 800, y: 12, w: W - 800 - PAD, h: 30,
    ...textbox([textRun(subtitle, 11, "#D1D5DB")], "right") }],
];
const slicerRow = (items) => items.map(([col, label, opts], i) =>
  [`slicer-${label}`, { x: PAD + i * 210, y: 58, w: 200, h: 80, ...slicer(col, label, opts) }]);   // dropdown: header 28 + selector 32 + padding 16 <= 80
const TOP = 142;                              // content starts below header + slicer row
const SUB = "Danube Automotive Components · fictional data";
const MONTH = [f.yearMonth, "Month", { single: true, selected: ["2026-10"] }];
const PROGRAM = [f.program, "Vehicle program"];
const LINE = [f.line, "Line"];

// ---------------------------------------------------------------- the 8 pages (blueprint §7)
// KPI card titles are the KPI name only; the target and the trend arrow sit underneath (user request)
const CORE = [
  ["FTY %", f.fty, "FTY"],
  ["Defect Rate %", f.defectRate, "Defect rate"],
  ["Scrap Rate %", f.scrapRate, "Scrap rate"],
  ["Rework Rate %", f.reworkRate, "Rework rate"],
  ["COPQ %", f.copqPct, "COPQ % of production value"],
  ["Overdue 8Ds", f.overdue8d, "Overdue 8Ds"],
];
const cw = (W - 2 * PAD - 5 * GAP) / 6;
// The original native KPI cards (value + target line + change line per KPI): kept on a hidden backup
// page since the HTML Content visual replaced them on the Executive page (documentation/07).
const coreCards = (top) => CORE.flatMap(([n, m, title], i) => [
  [`kpi-${i}`, { x: PAD + i * (cw + GAP), y: top, w: cw, h: 88, ...kpiCard(m, status(n), title, `${title}: green, amber or red against target`) }],
  [`kpi-target-${i}`, { x: PAD + i * (cw + GAP), y: top + 92, w: cw, h: 44,
    ...lineCard(M(T.kt, `${n} Target Label`), { alt: `${title} target` }) }],
  [`kpi-change-${i}`, { x: PAD + i * (cw + GAP), y: top + 140, w: cw, h: 44,
    ...lineCard(M(T.kt, `${n} Change Label`), { trend: M(T.kt, `${n} Trend`),
      alt: `${title} change versus previous month: green arrow when moving towards target, red when moving away` }) }],
]);

const PAGES = [
  { key: "executive", name: "Executive Quality Overview",
    interactionsNoFilter: [["slicer-Month", "copq-trend"], ["slicer-Month", "scorecard-trend"]],
    visuals: [
      ...header("Executive Quality Overview", SUB),
      ...slicerRow([MONTH, PROGRAM]),
      ["on-target", { x: W - PAD - 250, y: 58, w: 250, h: 80, ...multiCard([f.onTarget], "Core KPIs on target", "Number of the six core KPIs that meet their target") }],
      // KPI cards with targets: one HTML Content Secure visual driven by [KPI Cards HTML] (documentation/07)
      ["kpi-cards-html", { x: PAD, y: TOP, w: W - 2 * PAD, h: 184, ...htmlContent(M(T.kt, "KPI Cards HTML"),
        "Six core KPIs: current value against target with status, and an arrow showing the change since the previous month (green towards target, red away from it)") }],
      ["copq-trend", { x: PAD, y: 334, w: 620, h: 178, ...chart("lineChart", { category: f.month, y: [f.copq], title: "Cost of poor quality per month (€)", alt: "Monthly internal cost of poor quality: scrap plus rework cost", color: COLORS.accent }) }],
      ["scorecard-trend", { x: PAD, y: 520, w: 620, h: 186, ...chart("clusteredColumnChart", { category: f.month, y: [f.onTarget], title: "Core KPIs on target per month (of 6)", alt: "How many of the six core KPIs were on target in each month", color: COLORS.accent }) }],
      ["scorecard", { x: PAD + 630, y: 334, w: W - 2 * PAD - 630, h: 372, ...matrix({
        rows: [f.line], values: [as(f.fty, "FTY"), as(f.defectRate, "Defect"), as(f.scrapRate, "Scrap"),
                                  as(f.reworkRate, "Rework"), as(f.copqPct, "COPQ"), as(f.overdue8d, "Overdue 8Ds")],
        colorBy: { "FTY %": colour("FTY %"), "Defect Rate %": colour("Defect Rate %"), "Scrap Rate %": colour("Scrap Rate %"),
                   "Rework Rate %": colour("Rework Rate %"), "COPQ %": colour("COPQ %") },
        title: "Scorecard by line", alt: "Core KPIs per production line, coloured green, amber or red against target" }) }],
    ] },

  { key: "production", name: "Production Quality Performance",
    visuals: [
      ...header("Production Quality Performance", "Where are first-pass and first-time yield lost?"),
      ...slicerRow([[f.year, "Year"], LINE, [f.stage, "Inspection stage"], PROGRAM]),
      ["fpy-stage", { x: PAD, y: TOP, w: 400, h: 270, ...chart("clusteredBarChart", { category: f.stage, y: [f.fpy], title: "FPY by inspection stage", alt: "First pass yield per inspection stage", color: COLORS.accent, labels: true, axisStart: 0.9 }) }],
      ["fty-trend", { x: PAD + 410, y: TOP, w: W - 2 * PAD - 410, h: 270, ...chart("lineChart", { category: f.month, y: [f.fty], title: "FTY per month", alt: "First time yield per month", color: COLORS.accent }) }],
      ["volume-fpy", { x: PAD, y: TOP + 280, w: 600, h: 282, ...chart("lineClusteredColumnComboChart", { category: f.month, y: [f.volume], y2: [f.fpyFinal], title: "Volume vs final-stage FPY", alt: "Production volume as columns and final-stage first pass yield as a line, per month" }) }],
      ["line-month", { x: PAD + 610, y: TOP + 280, w: W - 2 * PAD - 610, h: 282, ...matrix({ rows: [f.line], columns: [f.yearMonth], values: [f.fpyFinal], colorBy: { "FPY Final Stage %": colour("FPY %") }, title: "Final-stage FPY by line and month", alt: "Final-stage first pass yield per line and month, coloured against the 97% target" }) }],
    ] },

  { key: "scrap-rework", name: "Scrap and Rework Analysis",
    visuals: [
      ...header("Scrap and Rework Analysis", "What costs us money, and why?"),
      ...slicerRow([[f.year, "Year"], LINE, PROGRAM, [f.defectCat, "Defect category"]]),
      ["cost-cards", { x: PAD, y: TOP, w: W - 2 * PAD, h: 92, ...multiCard([as(f.scrapCost, "Scrap cost"), as(f.reworkCost, "Rework cost"), as(f.copq, "COPQ"), as(f.reworkImputed, "Rework cost imputed"), as(f.reworkSuccess, "Rework success")], null, "Scrap cost, rework cost, COPQ, imputed rework cost and rework success rate") }],
      ["cost-trend", { x: PAD, y: TOP + 100, w: 620, h: 220, ...chart("clusteredColumnChart", { category: f.month, y: [f.scrapCost, f.reworkCost], title: "Scrap and rework cost per month (€)", alt: "Monthly scrap cost and rework cost" }) }],
      ["rates-trend", { x: PAD + 630, y: TOP + 100, w: W - 2 * PAD - 630, h: 220, ...chart("lineChart", { category: f.month, y: [f.scrapRate, f.reworkRate], title: "Scrap and rework rate per month", alt: "Monthly scrap rate and rework rate" }) }],
      ["cost-defect", { x: PAD, y: TOP + 328, w: 620, h: 234, ...chart("clusteredBarChart", { category: f.defect, y: [f.copq], sortBy: f.copq, title: "Cost of poor quality by defect type (Pareto)", alt: "Scrap plus rework cost per defect type, largest first", color: COLORS.accent }) }],
      ["cost-part", { x: PAD + 630, y: TOP + 328, w: W - 2 * PAD - 630, h: 234, ...chart("clusteredBarChart", { category: f.part, y: [f.copq], sortBy: f.copq, title: "Cost of poor quality by part", alt: "Scrap plus rework cost per part, largest first", color: COLORS.accent }) }],
    ] },

  { key: "defects", name: "Defect Analysis",
    visuals: [
      ...header("Defect Analysis", "Which defects, where and when?"),
      ...slicerRow([[f.year, "Year"], LINE, [f.shift, "Shift"], [f.stage, "Inspection stage"], [f.severity, "Severity"]]),
      ["pareto", { x: PAD, y: TOP, w: 420, h: 290, ...chart("clusteredBarChart", { category: f.defect, y: [f.defectQty], sortBy: f.defectQty, title: "Defect Pareto (number of defects)", alt: "Number of defects per defect type, largest first", color: COLORS.accent }) }],
      ["cat-line", { x: PAD + 430, y: TOP, w: 400, h: 290, ...matrix({ rows: [f.defectCat], columns: [f.lineCode], values: [f.defectQty], title: "Defects by category and line", alt: "Number of defects per defect category and line" }) }],
      ["dpmo-part", { x: PAD + 840, y: TOP, w: W - 2 * PAD - 840, h: 290, ...chart("clusteredBarChart", { category: f.part, y: [f.dpmo], sortBy: f.dpmo, title: "DPMO by part", alt: "Defects per million opportunities per part, highest first", color: COLORS.accent }) }],
      ["defect-trend", { x: PAD, y: TOP + 300, w: 620, h: 262, ...chart("lineChart", { category: f.month, y: [f.defectRate], title: "Defect rate per month", alt: "Defects per final-stage unit inspected, per month", color: COLORS.accent }) }],
      ["defect-shift", { x: PAD + 630, y: TOP + 300, w: W - 2 * PAD - 630, h: 262, ...chart("clusteredColumnChart", { category: f.line, y: [f.defectRate], series: [f.shift], title: "Defect rate by line and shift", alt: "Defect rate per line split by shift" }) }],
    ] },

  { key: "8d", name: "Corrective Action / 8D Tracking",
    visuals: [
      ...header("Corrective Action / 8D Tracking", "What is open, overdue, and who owns it?"),
      ...slicerRow([[C(T.e8d, "Status"), "Status"], [C(T.e8d, "Owner"), "Owner"], LINE, [C(T.e8d, "Problem Source"), "Source"]]),
      ["8d-cards", { x: PAD, y: TOP, w: W - 2 * PAD, h: 92, ...multiCard([as(f.open8d, "Open 8Ds"), as(f.overdue8d, "Overdue 8Ds"), as(f.avg8d, "Avg closure days"), as(f.ontime8d, "On-time closure"), as(f.caPct, "Action completion"), as(f.repeat, "Repeat rate")], null, "Open and overdue 8Ds, average closure days, on-time closure, corrective-action completion and repeat defect rate") }],
      ["ageing", { x: PAD, y: TOP + 100, w: 380, h: 220, ...chart("clusteredColumnChart", { category: C(T.e8d, "Age Bucket"), y: [f.open8d], title: "Open 8Ds by age", alt: "Open 8D reports by age bucket", color: COLORS.accent }) }],
      ["flow", { x: PAD + 390, y: TOP + 100, w: W - 2 * PAD - 390, h: 220, ...chart("clusteredColumnChart", { category: f.month, y: [f.opened8d, f.closed8d], title: "8Ds opened and closed per month", alt: "8D reports opened and closed per month" }) }],
      ["8d-table", { x: PAD, y: TOP + 328, w: W - 2 * PAD, h: 234, ...table({
        columns: [C(T.e8d, "8D ID"), C(T.e8d, "Status"), C(T.e8d, "Problem Source"), f.line, f.part, f.defect, C(T.e8d, "Owner"),
                  C(T.e8d, "Opened Date"), C(T.e8d, "Target Closure Date"), C(T.e8d, "Closed Date"), C(T.e8d, "Days Open"), C(T.e8d, "Is Repeat")],
        sortBy: C(T.e8d, "Days Open"), title: "8D reports", alt: "List of 8D reports with status, owner, dates and age" }) }],
    ] },

  { key: "line-shift", name: "Line and Shift Performance",
    visuals: [
      ...header("Line and Shift Performance", "Which line and shift combination underperforms?"),
      ...slicerRow([[f.year, "Year"], LINE, PROGRAM]),
      ["line-shift", { x: PAD, y: TOP, w: 620, h: 290, ...matrix({ rows: [f.line], columns: [f.shift], values: [as(f.defectRate, "Defect rate"), as(f.fpyFinal, "FPY")],
        colorBy: { "Defect Rate %": colour("Defect Rate %"), "FPY Final Stage %": colour("FPY %") },
        title: "Line × shift: defect rate and final-stage FPY", alt: "Defect rate and final-stage FPY per line and shift, coloured against target" }) }],
      ["ranking", { x: PAD + 630, y: TOP, w: W - 2 * PAD - 630, h: 290, ...chart("clusteredBarChart", { category: f.line, y: [f.defectRate], sortBy: f.defectRate, title: "Line ranking by defect rate", alt: "Production lines ranked by defect rate, highest first", color: COLORS.accent }) }],
      ["shift-trend", { x: PAD, y: TOP + 300, w: W - 2 * PAD, h: 262, ...chart("lineChart", { category: f.month, y: [f.defectRate], series: [f.shift], title: "Defect rate per month by shift (use the Line slicer to compare lines)", alt: "Monthly defect rate for the early, late and night shift" }) }],
    ] },

  { key: "trends", name: "Historical Trends and Comparison",
    visuals: [
      ...header("Historical Trends and Comparison", "Are we improving? Month on month and year on year"),
      ...slicerRow([LINE, PROGRAM]),
      ["defect-r3", { x: PAD, y: TOP, w: 620, h: 276, ...chart("lineChart", { category: f.month, y: [f.defectRate], series: [f.tc], title: "Defect rate: month vs rolling 3 months", alt: "Monthly defect rate and its rolling three-month value",
        filters: [inFilter(f.tc, ["Current", "Rolling 3 Months"])] }) }],
      ["fty-py", { x: PAD + 630, y: TOP, w: W - 2 * PAD - 630, h: 276, ...chart("lineChart", { category: f.month, y: [f.fty], series: [f.tc], title: "FTY: this year vs previous year", alt: "First time yield per month compared with the same month a year earlier",
        filters: [inFilter(f.tc, ["Current", "Previous Year"])] }) }],
      ["copq-r3", { x: PAD, y: TOP + 286, w: 620, h: 276, ...chart("lineChart", { category: f.month, y: [f.copqPct], series: [f.tc], title: "COPQ %: month vs rolling 3 months", alt: "Monthly cost of poor quality share and its rolling three-month value",
        filters: [inFilter(f.tc, ["Current", "Rolling 3 Months"])] }) }],
      ["ontarget-trend", { x: PAD + 630, y: TOP + 286, w: W - 2 * PAD - 630, h: 276, ...chart("clusteredColumnChart", { category: f.month, y: [f.onTarget], title: "Core KPIs on target (of 6)", alt: "Number of core KPIs on target per month", color: COLORS.accent }) }],
    ] },

  { key: "audit", name: "Audit and Compliance",
    visuals: [
      ...header("Audit and Compliance", "Are we audit-ready? What is open or overdue?"),
      ...slicerRow([[C(T.af, "Audit Type"), "Audit type"], [C(T.af, "Classification"), "Classification"], LINE, [f.year, "Year"]]),
      ["audit-cards", { x: PAD, y: TOP, w: W - 2 * PAD, h: 92, ...multiCard([as(f.openF, "Open findings"), as(f.overdueF, "Overdue findings"), as(f.majorNC, "Major NCs"), as(f.raisedF, "Findings raised"), as(f.ontimeF, "On-time closure"), as(f.avgF, "Avg closure days")], null, "Open and overdue findings, major nonconformities, findings raised, on-time closure and average closure days") }],
      ["type-class", { x: PAD, y: TOP + 100, w: 560, h: 220, ...matrix({ rows: [C(T.af, "Audit Type")], columns: [C(T.af, "Classification")], values: [f.raisedF], title: "Findings by audit type and classification", alt: "Number of audit findings per audit type and classification" }) }],
      ["clause", { x: PAD + 570, y: TOP + 100, w: W - 2 * PAD - 570, h: 220, ...chart("clusteredBarChart", { category: C(T.af, "Standard Reference"), y: [f.raisedF], sortBy: f.raisedF, title: "Findings by standard clause", alt: "Number of findings per standard clause reference", color: COLORS.accent }) }],
      ["finding-table", { x: PAD, y: TOP + 328, w: W - 2 * PAD, h: 234, ...table({
        columns: [C(T.af, "Finding ID"), C(T.af, "Audit Date"), C(T.af, "Audit Type"), C(T.af, "Classification"), C(T.line, "Line Code"),
                  C(T.af, "Finding"), C(T.af, "Finding Owner"), C(T.af, "Finding Due Date"), C(T.af, "Finding Closed Date"),
                  C(T.af, "Is Finding Overdue"), C(T.af, "Linked 8D")],
        sortBy: C(T.af, "Audit Date"), title: "Audit findings", alt: "List of audit findings with classification, owner, dates and linked 8D" }) }],
    ] },

  // Hidden in view mode: the 18 native KPI cards exactly as they were before the HTML visual.
  { key: "kpi-cards-backup", name: "KPI cards (original, hidden)", hidden: true,
    visuals: [
      ...header("KPI cards · original native version (hidden backup)", "Replaced on the Executive page by the HTML Content visual"),
      ...slicerRow([MONTH, PROGRAM]),
      ...coreCards(TOP),
      ["note", { x: PAD, y: TOP + 200, w: W - 2 * PAD, h: 60, ...textbox([textRun(
        "To switch back: copy these 18 cards to the Executive Quality Overview page and remove the HTML Content visual. Same measures, same filters; nothing in the model depends on this page.",
        12, COLORS.muted)]) }],
    ] },
];

// ---------------------------------------------------------------- theme
function writeTheme() {
  const dir = join(REPORT, "StaticResources", "RegisteredResources");
  mkdirSync(dir, { recursive: true });
  const theme = {
    name: THEME_NAME,
    dataColors: [COLORS.accent, "#7C8EA3", "#D9822B", "#5B4E9E", "#3E8E7E", "#A0526B", "#9CA3AF", "#1F2937"],
    background: "#FFFFFF", foreground: "#1F2937", tableAccent: COLORS.accent,
    good: COLORS.green, neutral: COLORS.amber, bad: COLORS.red,
    textClasses: {
      callout: { fontFace: "Segoe UI Semibold", fontSize: 24, color: "#1F2937" },
      title: { fontFace: "Segoe UI Semibold", fontSize: 12, color: "#1F2937" },
      header: { fontFace: "Segoe UI Semibold", fontSize: 11, color: "#1F2937" },
      label: { fontFace: "Segoe UI", fontSize: 10, color: "#374151" },
    },
    visualStyles: {
      "*": { "*": {
        background: [{ show: true, color: { solid: { color: "#FFFFFF" } }, transparency: 0 }],
        border: [{ show: true, color: { solid: { color: "#E5E7EB" } }, radius: 6 }],
        title: [{ show: true, fontColor: { solid: { color: "#1F2937" } }, fontSize: 11 }],
        visualHeader: [{ show: false }],
      } },
      page: { "*": { background: [{ color: { solid: { color: "#F3F4F6" } }, transparency: 0 }] } },
    },
  };
  // cache-busting file name (theming.md): <ThemeName>-<hash of content>.json
  const body = JSON.stringify(theme, null, 2);
  const file = `${THEME_NAME}-${id(body).slice(0, 8)}.json`;
  for (const old of readdirSync(dir)) if (old.startsWith(`${THEME_NAME}-`)) rmSync(join(dir, old));
  writeFileSync(join(dir, file), JSON.stringify({ ...theme, name: file }, null, 2) + "\n");   // name = file name (PBIR_THEME_FILE_NAME_MISMATCH)

  const reportPath = join(DEF, "report.json");
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const base = report.themeCollection.baseTheme;
  report.themeCollection.customTheme = { name: file, reportVersionAtImport: base.reportVersionAtImport, type: "RegisteredResources" };
  report.resourcePackages = (report.resourcePackages || []).filter((p) => p.type !== "RegisteredResources");
  report.resourcePackages.push({ name: "RegisteredResources", type: "RegisteredResources", items: [{ name: file, path: file, type: "CustomTheme" }] });
  writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n");
  return file;
}

// ---------------------------------------------------------------- write pages
// only: null = rebuild every page; otherwise a Set of page keys to rewrite (others untouched)
function writePages(only) {
  const pagesDir = join(DEF, "pages");
  if (!only && existsSync(pagesDir)) rmSync(pagesDir, { recursive: true });
  mkdirSync(pagesDir, { recursive: true });
  const order = [];
  let visualCount = 0;
  for (const p of PAGES) {
    const pageName = id("page", p.key);
    order.push(pageName);
    if (only && !only.has(p.key)) continue;
    const dir = join(pagesDir, pageName);
    if (existsSync(dir)) rmSync(dir, { recursive: true });
    const names = {};
    for (const [key, spec] of p.visuals) {
      const v = container(p.key, key, spec);
      names[key] = v.name;
      mkdirSync(join(dir, "visuals", v.name), { recursive: true });
      writeFileSync(join(dir, "visuals", v.name, "visual.json"), JSON.stringify(v, null, 2) + "\n");
      visualCount++;
    }
    const page = { $schema: SCHEMA.page, name: pageName, displayName: p.name, displayOption: "FitToPage", height: H, width: W,
      ...(p.hidden ? { visibility: "HiddenInViewMode" } : {}) };
    if (p.interactionsNoFilter) {
      page.visualInteractions = p.interactionsNoFilter.map(([s, t]) => ({ source: names[s], target: names[t], type: "NoFilter" }));
    }
    writeFileSync(join(dir, "page.json"), JSON.stringify(page, null, 2) + "\n");
  }
  const pagesJson = join(pagesDir, "pages.json");
  let finalOrder = order;
  if (only && existsSync(pagesJson)) {            // keep the existing order; append pages that are new
    const existing = JSON.parse(readFileSync(pagesJson, "utf8")).pageOrder || [];
    finalOrder = [...existing.filter((n) => order.includes(n)), ...order.filter((n) => !existing.includes(n))];
  }
  writeFileSync(pagesJson,
    JSON.stringify({ $schema: SCHEMA.pages, pageOrder: finalOrder, activePageName: finalOrder[0] }, null, 2) + "\n");
  return { pages: finalOrder.length, rewritten: only ? [...only] : "all", visuals: visualCount };
}

// --pages executive,kpi-cards-backup  -> rewrite only those pages; theme and other pages untouched
const pagesArg = process.argv.indexOf("--pages");
const only = pagesArg > 0 ? new Set(process.argv[pagesArg + 1].split(",")) : null;
if (only) {
  const unknown = [...only].filter((k) => !PAGES.some((p) => p.key === k));
  if (unknown.length) throw new Error(`unknown page keys: ${unknown.join(", ")}`);
}
const theme = only ? "(unchanged)" : writeTheme();
const stats = writePages(only);
console.log(JSON.stringify({ report: REPORT, theme, ...stats }));

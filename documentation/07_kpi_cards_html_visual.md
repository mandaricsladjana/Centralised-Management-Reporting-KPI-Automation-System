# KPI Cards with Targets — HTML Content Visual

**Document status:** v1.0, 2026-11-14.
**Related:** [06_powerbi_model.md](06_powerbi_model.md), decision **D33** in [01_project_blueprint.md](01_project_blueprint.md) §14

The six core KPI cards on the **Executive Quality Overview** page were 18 native card visuals: per KPI one value card, one target line and one change line. They are now **one HTML Content visual**. Each tile shows the current value against the target, a status pill, a target bar and a green or red month-on-month arrow. The business logic is unchanged: the visual only arranges the existing measures.

## 1. What you see

| Element | Source (existing measure) | Rule |
|---|---|---|
| Tile accent, value colour, pill | `<KPI> Status` | Green "On target", Amber "Near target", Red "Off target" (same hex as `<KPI> Color`) |
| **Now** value | `FTY %`, `Defect Rate %`, `Scrap Rate %`, `Rework Rate %`, `COPQ %`, `Overdue 8Ds` | Same formats as the old cards (`0.00%`, count) |
| **Target** | `<KPI> Target Label` | e.g. `≥ 94.0%`, `≤ 3.0%`, `0` |
| Bar and black tick | value and `<KPI> Target` | Bar = value on a fixed scale per KPI, tick = target. Scale: FTY 90–100 %; rates 0 to 2× target; Overdue 8Ds 0 to max(6, value). Presentation only |
| Arrow pill | `<KPI> Change Label`, `<KPI> Trend` | ▲/▼ = direction of the value since the previous month. **Green = towards or past the target, red = away from it**, grey = unchanged |
| "No previous month to compare" | `<KPI> Change Label` is blank | First month of data |
| "No data for the selected period and filters" | `Production Volume` is blank | Empty month, or a program without production in the period |

Filter and date context are those of the old cards. The Month slicer (`Calendar[Year Month]`, single select, opens on 2026-10) and the Vehicle program slicer (`Part[Program]`) filter it. There are no visual-level filters.

## 2. Why HTML Content, and which edition

- **HTML Content Secure** (AppSource, certified; GUID `htmlContent443BE3AD55E043BF878BED274D3A6865`, data role `content`) is a renderer: the DAX measure supplies the HTML. All logic therefore stays in the model, next to the existing measures, and is testable with DAX queries (§5).
- **Why not a native SDK visual:** it would need a TypeScript project, a pbiviz build and a private, uncertified visual to maintain. Its advantages, click-to-cross-filter and native tooltips, are not needed for a summary strip.
- **What the Secure edition requires:**
  - no `<script>`, event handlers, iframes, remote fonts or remote images (everything is sanitised with DOMPurify);
  - the measure uses inline `style` attributes only, with no `<style>` block. Styling therefore does not depend on how the sanitiser treats style sheets.

## 3. Files

| File | New / changed | Purpose |
|---|---|---|
| `powerbi/tools/build_kpi_cards.mjs` | new | The single HTML template. Writes the DAX measure and the test query; with `--preview` it writes the preview from real query results |
| `powerbi/html-visuals/kpi-cards.dax` | new (generated) | The measure `KPI Cards HTML`, paste-ready |
| `powerbi/html-visuals/kpi-cards-test.dax` | new (generated) | DAX query: the measure in 7 filter contexts |
| `powerbi/html-visuals/kpi-cards-preview.html` | new (generated) | Standalone preview. **Every tile is the measure's real output** from the DEV model; no hand-typed values |
| `powerbi/tools/test_kpi_cards.mjs` | new | Parity test, HTML vs. original card measures (§5) |
| `powerbi/tools/run_dax.ps1` | new | Runs a DAX query against the open Desktop model (read-only) |
| `…SemanticModel/definition/tables/KPI Targets.tmdl` | changed | **Added** measure `KPI Cards HTML` (display folder *Visuals*). No existing measure changed or deleted |
| `…Report/definition/report.json` | changed (by Desktop) | `publicCustomVisuals: ["htmlContent443BE3AD55E043BF878BED274D3A6865"]`, added when the visual was imported from AppSource |
| `…Report/definition/pages/42495797f8cfdbe6f8f6/` (Executive) | changed | 18 card visuals replaced by one HTML Content visual (x 16, y 142, 1248 × 184, transparent container) |
| `…Report/definition/pages/684528898be818a5d745/` | new | **Hidden backup page** "KPI cards (original, hidden)" with the 18 original cards, unchanged |
| `powerbi/tools/build_report.mjs` | changed | Executive page uses the HTML visual, backup page added, new `--pages` option rewrites only named pages |

No relationship, table, column or existing measure was changed. Only the Executive page and the new backup page were rewritten (`--pages executive,kpi-cards-backup`). The other 7 pages, the theme and `report.json` were left untouched. A dry run on a copy confirmed they are byte-identical.

## 4. How to rebuild or insert it

**Rebuild after changing the design** (for example colours or labels in `build_kpi_cards.mjs`):

1. `node powerbi/tools/build_kpi_cards.mjs` regenerates `kpi-cards.dax`.
2. Replace the expression of `KPI Cards HTML` with that file. Use Desktop, the modelling MCP, or the TMDL (`KPI Targets.tmdl`, three-tab indentation).
3. With Desktop open: `node powerbi/tools/test_kpi_cards.mjs <port>` runs the parity test and refreshes the preview.

**Insert manually into another report page** (Power BI Desktop):

1. Visualizations pane → **…** → *Get more visuals* → search **HTML Content** → add **HTML Content Secure**. It is already registered in this report.
2. Add the visual to the page and drag the measure **KPI Targets → Visuals → KPI Cards HTML** into **Values**.
3. Format pane:
   - turn off *Title*, *Background* and *Border* (the HTML draws its own tiles);
   - set *Padding* to 0;
   - size about 1248 × 184 px for six tiles in one row. Narrower containers wrap the tiles automatically (preview: 620 px gives 3 + 3).

**Switch back to the native cards:** copy the 18 cards from the hidden page "KPI cards (original, hidden)" to the Executive page and delete the HTML visual. Alternatively, restore the page from Git (commit `3cb561e`).

## 5. Testing results (2026-11-14, DEV model)

`node powerbi/tools/test_kpi_cards.mjs` evaluates the measure and the **original card measures** in 7 contexts. It then compares, per KPI: value, status pill, status colour, target, change text and arrow colour.

| Context | Result |
|---|---|
| Oct 2026, all programs (the page's default view) | 5 of 6 on target; COPQ ▲ 0.03 pp red (worsening); Overdue 8Ds red but ▼ 1 green |
| Sep 2025 (EV launch) | Defect rate, rework rate, COPQ and overdue 8Ds off target; all arrows red |
| Nov 2024 (first month) | "No previous month to compare"; defect rate amber |
| Oct 2026, program VP-EV-D | Values change with the slicer, e.g. FTY 96.97 %, Overdue 8Ds 1 (amber) |
| Oct 2026, program VP-HATCH-B (ended June 2026) | Empty state |
| Mar 2027 (no data) | Empty state |
| No month selected (whole history) | Renders; see limitation 3 |

**181 checks, 0 failures**: the visual shows exactly what the original cards show, in every context.

**Rendered checks:**
- The preview was checked in a browser: layout, colours, the target tick, and wrapping at 620 px.
- The test in Power BI Desktop (slicer clicks, screenshot) is recorded in the change log of the PR.

## 6. Limitations

1. **No click interaction or tooltips.** The tiles do not cross-filter other visuals, and hovering shows no tooltip. Use the scorecard matrix below for drill-down.
2. **Fixed bar scales** (presentation choice, §1). A value beyond the scale is shown as a full bar, and the number remains exact.
3. **No month selected:** the existing `<KPI> Change Label` returns "= no change", because both periods cover the whole history. On this page the Month slicer is single-select, so this only occurs if the user clears the slicer. The behaviour comes from the existing measure and was not changed.
4. **Fonts:** Segoe UI with Arial as fallback. The Secure edition blocks web fonts.
5. **Text sizes are fixed px** and don't scale with Power BI's *Fit to page* zoom in the same way native visuals do. At the 1280 × 720 canvas they match the rest of the page.
6. **The visual comes from AppSource:** the publisher hosts it. Tenants that block AppSource visuals must allow-list it; it is certified, so PDF/PowerPoint export is supported.

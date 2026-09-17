# powerbi/

The GridironOps report. The Power BI project here was authored as text
(PBIP: TMDL semantic model + legacy report layout) following
[`docs/powerbi-build-plan.md`](../docs/powerbi-build-plan.md), so the model,
measures, relationships and page layouts are all in git. Power BI Desktop
opens `GridironOps.pbip` directly.

| File | What |
|---|---|
| `theme.json` | the report theme — white surface, one steel-blue accent, flag colours as `good` / `neutral` / `bad` |
| `GridironOps.pbip` | the project entry point — open this in Desktop |
| `GridironOps.SemanticModel/` | the model in TMDL: 7 imported `gold.*` tables, AsOf + Window disconnected tables, the Measures table, relationships |
| `GridironOps.Report/` | the report layout: Today · Load · Injury report · Data · Player (hidden drill-through page) |
| `GridironOps.pbix` | the single-file export for sharing (created from Desktop, Part 5 of the plan) |

Every number comes from `gold.*`; the Flag is computed in
`gold.fact_player_day` (dbt), not in DAX.

## Finishing in Desktop (first open)

The project ships without cached data. With the stack running
(`docker compose up -d`, DAG unpaused):

1. Open `GridironOps.pbip` → **Home → Refresh**. When asked for credentials
   for `localhost:5432` / `warehouse`, use the *Database* tab →
   `warehouse` / `warehouse`.
2. Slicers: set **team = NE** on each page (the tracked squad); select the
   three common slicers and turn on **View → Sync slicers**.
3. Player page: **Format → Page information → Drillthrough** → add
   `Player[full_name]`, *Keep all filters* on (the page is already hidden).
4. Polish that only Desktop does well: conditional formatting on the roster
   board (flag icons, ACWR band background, `days_since_survey` ≥ 3 red) and
   constant lines (0.8 / 1.3 / 1.5 on ACWR, ±10 on asymmetry) — rules and
   colours are in Part 4 of the build plan.
5. Save (keeps the PBIP format), then Part 5 of the plan: export `.pbix`,
   publish, screenshot.

# powerbi/

The GridironOps report. Built per [`docs/powerbi-build-plan.md`](../docs/powerbi-build-plan.md)
as a Power BI project (PBIP: TMDL semantic model + report layout), first
authored as text and then opened, refreshed, verified and re-saved by Power BI
Desktop itself — so everything here is Desktop-normalised source, in git.
Desktop opens `GridironOps.pbip` directly.

| File | What |
|---|---|
| `theme.json` | the report theme — white surface, one steel-blue accent, flag colours as `good` / `neutral` / `bad` |
| `GridironOps.pbip` | the project entry point — open this in Desktop |
| `GridironOps.SemanticModel/` | the model in TMDL: 7 imported `gold.*` tables, AsOf + Window disconnected tables, the Key Measures table, relationships |
| `GridironOps.Report/` | the report layout: Today · Load · Injury report · Data · Player (hidden drill-through page) |
| `GridironOps.pbix` | the single-file export for sharing (created from Desktop, Part 5 of the plan) |

Every number comes from `gold.*`; the Flag is computed in
`gold.fact_player_day` (dbt), not in DAX. Screenshots of every page are in
[`docs/screenshots/`](../docs/screenshots/).

Already wired up and verified in Desktop: the data source connection
(credentials save on first refresh), team slicer defaulting to NE, the Player
drill-through (right-click a roster row → Drill through → Player), and the
hidden Player page.

## Opening it

The project ships without cached data. With the stack running
(`docker compose up -d`, DAG unpaused): open `GridironOps.pbip` →
**Refresh now** on the banner. On first refresh, credentials for
`localhost:5432` / `warehouse`: *Database* tab → `warehouse` / `warehouse`,
then OK on the unencrypted-connection warning.

> **Known quirk:** saving a PBIP hangs forever at "Working on it" when more
> than one Power BI Desktop window is open. Close other Desktop windows
> before Ctrl+S.

## Remaining polish (optional, Desktop-only)

1. **View → Sync slicers** so the Days back / team / position slicers follow
   you between pages.
2. Conditional formatting on the roster board (flag icons, ACWR band
   background, `days_since_survey` ≥ 3 red) and constant lines (0.8 / 1.3 /
   1.5 on ACWR, ±10 on asymmetry) — rules and colours in Part 4 of the plan.
3. Part 5 of the plan: export `.pbix`, publish, add the link to the README.

# powerbi/

The GridironOps report. Built in Power BI Desktop following
[`docs/powerbi-build-plan.md`](../docs/powerbi-build-plan.md); saved here as
a Power BI project (`GridironOps.pbip` + the `.Report/` and `.SemanticModel/`
folders, all text) plus a `.pbix` for sending.

| File | What |
|---|---|
| `theme.json` | the report theme — white surface, one steel-blue accent, flag colours as `good` / `neutral` / `bad` |
| `GridironOps.pbip` | the project entry point (created by Desktop on first save) |
| `GridironOps.pbix` | the single-file export for sharing |

Pages: **Today** (the roster board, red → green) · **Load** · **Injury report**
· **Data** · **Player** (drill-through). Every number comes from `gold.*`;
the Flag is computed in `gold.fact_player_day` (dbt), not in DAX.

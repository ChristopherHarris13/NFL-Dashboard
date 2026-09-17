# GridironOps — Power BI build plan

The report performance and medical staff open in the morning: who's red, who's
amber, who plays. Five pages, one semantic model over `gold.*`, built in Power
BI Desktop on the Windows box where the stack runs. Vocabulary is in
[`CONTEXT.md`](../CONTEXT.md); the Flag is computed in dbt
(`gold.fact_player_day`), so Power BI only presents it.

Work top to bottom. Each part ends with a check so you know it worked before
moving on.

---

## Part 0 — Stand the stack up on Windows (≈ 30 min, mostly waiting)

Prereqs: Docker Desktop (WSL 2 backend), Git, Power BI Desktop (Microsoft Store
version is fine).

```powershell
git clone https://github.com/ChristopherHarris13/NFL-Dashboard.git   # or git pull if it exists
cd NFL-Dashboard
docker compose up --build -d
```

The mocks backfill 45 simulated days on boot (`BACKFILL_DAYS`), then advance one
simulated day every 5 minutes. Airflow is at http://localhost:8080 (`admin` /
`admin`). Unpause the DAG:

```powershell
docker compose exec airflow airflow dags unpause nfl_medallion
```

Let it run twice (≈ 15 min). Then confirm Gold is populated:

```powershell
docker compose exec warehouse psql -U warehouse -d warehouse -c "select flag, count(*) from gold.fact_player_day group by 1"
docker compose exec warehouse psql -U warehouse -d warehouse -c "select team, count(*) from gold.dim_player_current group by 1 order by 2 desc limit 3"
```

**Check:** three flag rows (red / amber / green) and `NE` at the top of the
team list. The squad is New England (`SEED_TEAM: "NE"` in `docker-compose.yml`).

> The warehouse is published on `localhost:5432` with user/password
> `warehouse` / `warehouse`, database `warehouse`. That is what Desktop
> connects to. The DAG keeps refreshing Gold every 15 minutes; in Desktop
> **Home → Refresh** re-imports.

---

## Part 1 — Desktop settings (5 min)

1. **File → Options and settings → Options → Preview features**: turn on
   *Power BI Project (.pbip) save option* and *Store semantic model using TMDL
   format* if they're listed (in current builds PBIP/TMDL are on by default).
   Restart Desktop if asked.
2. **View → Themes → Browse for themes** → `powerbi/theme.json`. White surface,
   one steel-blue accent; the only meaningful colours are the flag colours
   (`good` / `neutral` / `bad` in the theme = green / amber / red).
3. **Options → Current file → Data load**: untick *Autodetect new relationships*
   (we'll set them by hand) and *Auto date/time* (we have a Date table).

---

## Part 2 — Connect and import (10 min)

**Home → Get data → More… → PostgreSQL database**

| Field | Value |
|---|---|
| Server | `localhost:5432` |
| Database | `warehouse` |
| Data connectivity mode | **Import** |
| Credentials | *Database* tab → `warehouse` / `warehouse` |

In the Navigator, tick exactly these and **Load**:

| Table (schema `gold`) | Rename in the model to | Why |
|---|---|---|
| `fact_player_day` | **Player Day** | every as-of number on Today, Load and Player |
| `dim_player_current` | **Player** | names, team, position |
| `dim_date` | **Date** | the calendar; as-of arithmetic |
| `fact_injury` | **Injury** | the injury list |
| `fact_injury_status` | **Injury Status** | practice / game status per injury per day |
| `fact_strength` | **Force Plate Test** | per-test asymmetry points on Player |
| `dq_run_summary` | **DQ Run** | the Data page |

Rename via Model view (right-click the table → Rename), and rename the columns
you'll show on visuals as you go (`full_name` → *Player*, `readiness_score` →
*Readiness*, etc.). Leave keys as they are.

**Check:** Player Day has a few thousand rows (≈ players × simulated days) and Player ~70. If the Navigator shows no
`gold` schema, the DAG hasn't run `dbt_run` yet — wait for the next run.

---

## Part 3 — Model (20 min)

### Relationships (Model view, drag and drop)

| From (many) | To (one) | Cardinality | Active |
|---|---|---|---|
| Player Day[player_sk] | Player[player_sk] | many-to-one | yes |
| Player Day[date_day] | Date[date_day] | many-to-one | yes |
| Injury[player_sk] | Player[player_sk] | many-to-one | yes |
| Injury Status[injury_id] | Injury[injury_id] | many-to-one | yes |
| Injury Status[date_day] | Date[date_day] | many-to-one | yes |
| Force Plate Test[player_sk] | Player[player_sk] | many-to-one | yes |
| Force Plate Test[test_date] | Date[date_day] | many-to-one | yes |

Do **not** relate Injury Status directly to Player (it reaches Player through
Injury; a second path makes the model ambiguous). All relationships single
direction, filtering from the one side.

**Date**: right-click → *Mark as date table* → `date_day`.

Hide from report view: every `*_sk`, `*_id`, `training_load_sk`, `flag_rank`,
`history_days`.

### The as-of control (disconnected slicer pattern)

The report is read *as of* a day. Rather than a date slicer that fights the
trailing windows, use a disconnected table and measures.

**Modeling → New table:**

```dax
AsOf = SELECTCOLUMNS ( GENERATESERIES ( 0, 60, 1 ), "Days back", [Value] )
```

```dax
Window = DATATABLE ( "Window", STRING, "Days", INTEGER, { { "28 days", 28 }, { "Season", 400 } } )
```

Neither gets a relationship. Sort `Window[Window]` by `Window[Days]`.

### Measures

Create a blank table **Measures** (Enter data → one empty column → Load), put
all measures in it, then hide the column.

```dax
Latest Data Day = CALCULATE ( MAX ( 'Player Day'[date_day] ), REMOVEFILTERS () )

As-of = [Latest Data Day] - SELECTEDVALUE ( AsOf[Days back], 0 )

As-of Label = FORMAT ( [As-of], "ddd d mmm yyyy" )

Window Days = SELECTEDVALUE ( Window[Days], 28 )

-- 1 on the as-of day, else 0. Visual-level filter (= 1) on every table/card built from Player Day.
Is As-of = IF ( MAX ( 'Player Day'[date_day] ) = [As-of], 1, 0 )

-- same, for visuals built from Injury Status
Is As-of (Injury) = IF ( MAX ( 'Injury Status'[date_day] ) = [As-of], 1, 0 )

-- 1 inside the trailing window ending on the as-of day. Visual-level filter on Player-page charts,
-- whose axis must be Date[date_day] (not the fact table's date column).
In Window =
    VAR d = [As-of]
    RETURN IF ( MAX ( 'Date'[date_day] ) > d - [Window Days] && MAX ( 'Date'[date_day] ) <= d, 1, 0 )

-- Squad counts on the as-of day. (A measure can't be used directly in a CALCULATE filter,
-- so the as-of date goes through a VAR first.)
Players = VAR d = [As-of] RETURN CALCULATE ( DISTINCTCOUNT ( 'Player Day'[player_sk] ), 'Player Day'[date_day] = d )
Red     = VAR d = [As-of] RETURN CALCULATE ( COUNTROWS ( 'Player Day' ), 'Player Day'[date_day] = d, 'Player Day'[flag] = "red" )
Amber   = VAR d = [As-of] RETURN CALCULATE ( COUNTROWS ( 'Player Day' ), 'Player Day'[date_day] = d, 'Player Day'[flag] = "amber" )
Green   = VAR d = [As-of] RETURN CALCULATE ( COUNTROWS ( 'Player Day' ), 'Player Day'[date_day] = d, 'Player Day'[flag] = "green" )
Out     = VAR d = [As-of] RETURN CALCULATE ( COUNTROWS ( 'Player Day' ), 'Player Day'[date_day] = d, 'Player Day'[availability] = "Out" )
Limited = VAR d = [As-of] RETURN CALCULATE ( COUNTROWS ( 'Player Day' ), 'Player Day'[date_day] = d, 'Player Day'[availability] = "Limited" )
Silent  = VAR d = [As-of] RETURN CALCULATE ( COUNTROWS ( 'Player Day' ), 'Player Day'[date_day] = d, 'Player Day'[is_silent] = TRUE () )
Open Injuries = VAR d = [As-of] RETURN CALCULATE ( DISTINCTCOUNT ( 'Injury Status'[injury_id] ), 'Injury Status'[date_day] = d )

-- Load page
Load 7d   = VAR d = [As-of] RETURN CALCULATE ( SUM ( 'Player Day'[load_7d] ), 'Player Day'[date_day] = d )
Load Norm = VAR d = [As-of] RETURN CALCULATE ( SUM ( 'Player Day'[load_28d_avg_week] ), 'Player Day'[date_day] = d )
Load vs Norm % = DIVIDE ( [Load 7d] - [Load Norm], [Load Norm] )
ACWR      = VAR d = [As-of] RETURN CALCULATE ( AVERAGE ( 'Player Day'[acwr_coupled] ), 'Player Day'[date_day] = d )

-- Data page (latest run only)
Last Run       = CALCULATE ( MAX ( 'DQ Run'[created_at] ), 'DQ Run'[run_recency] = 1 )
Rows In        = CALCULATE ( SUM ( 'DQ Run'[rows_in] ), 'DQ Run'[run_recency] = 1 )
Rows In Silver = CALCULATE ( SUM ( 'DQ Run'[rows_in_silver] ), 'DQ Run'[run_recency] = 1 )
Quarantined %  = DIVIDE ( CALCULATE ( SUM ( 'DQ Run'[rows_quarantined] ), 'DQ Run'[run_recency] = 1 ), [Rows In] )
Reconciles     = IF ( CALCULATE ( COUNTROWS ( 'DQ Run' ), 'DQ Run'[run_recency] = 1, 'DQ Run'[reconciles] = FALSE () ) = 0, "All sources reconcile", "RECONCILIATION FAILED" )
Expectations   = CALCULATE ( SUM ( 'DQ Run'[expectations_run] ) - SUM ( 'DQ Run'[expectations_failed] ), 'DQ Run'[run_recency] = 1 ) & " / " & CALCULATE ( SUM ( 'DQ Run'[expectations_run] ), 'DQ Run'[run_recency] = 1 )
```

**Check:** a Card with `[As-of Label]` shows the latest simulated day; a Card
with `[Red]` shows a number. Drop an `AsOf[Days back]` slicer on the page
(style *Single value*), set it to 3 — both cards move.

---

## Part 4 — Pages

Page size 1280 × 720, theme applied. Common layout: a **top strip** (slicers +
cards), a **body**, nothing below the fold. Slicers on every page except
Player: `AsOf[Days back]` (single value, default 0), `Player[team]`
(dropdown, **default NE** — select NE, then keep it as the saved state),
`Player[position]` (tile). Use **View → Sync slicers** so the three follow you
between pages.

Every visual that shows an as-of value gets the visual-level filter
**`Is As-of` is 1** (drag the measure into *Filters on this visual*).

### 4.1 Today (landing)

Top strip, left to right: the three slicers · Card `[As-of Label]` · Cards
`[Red]`, `[Amber]`, `[Green]` (font colour = flag colour) · Cards `[Out]`,
`[Limited]`, `[Silent]`.

Body: **one table** — the roster board. Fields from Player Day (+ Player):

| Column | Field | Format |
|---|---|---|
| Flag | `flag` | conditional formatting → *Icons* (red ● amber ● green ●) or *Background colour* by rule |
| Player | `Player[full_name]` | |
| Pos | `Player[position]` | |
| Availability | `availability` | rule colours: Out red, Limited amber |
| Game | `game_status` | |
| Readiness | `readiness_score` | 1 dp |
| Δ vs 28d | `readiness_delta` | +0.0; data bar centred at 0 |
| ACWR | `acwr_coupled` | 2 dp; background by `acwr_band` rule (elevated amber, high red) |
| 7-day load | `load_7d` | #,0 |
| Days since survey | `days_since_survey` | red when ≥ 3 |
| Open injury | `open_injury_body_parts` | |
| Expected return | `expected_return` | d mmm |
| Why | `flag_reasons` | word wrap on |

Visual filter `Is As-of = 1`. Sort by `flag_rank` then `readiness_score`
ascending (add `flag_rank` to the fields, sort, then hide the column via
column width or leave it as a narrow "#"). Row padding tight.

**Drill-through:** select the table → *Format → Interactions*; on the Player
page set `Player[full_name]` as the drill-through field (Part 4.5). Right-click
a row → Drill through → Player.

### 4.2 Load (two visuals + a table)

1. **Clustered bar** — per player, `[Load 7d]` vs `[Load Norm]`, sorted by
   `[Load vs Norm %]` descending. Title *7-day load vs each player's own 28-day
   norm*. Top N filter 20 by `[Load vs Norm %]` if the squad is large.
2. **Stacked column** — X `Date[date_day]`, Y count of `Player Day` rows,
   legend `acwr_band` (colour: low steel-blue, sweet green, elevated amber,
   high red). Visual filter `In Window = 1` with the Window slicer on this page
   defaulting to *Season*. Title *Players by ACWR band, by day*.
3. **Table** — Player, `load_7d`, `load_28d_avg_week`, `[Load vs Norm %]`,
   `acwr_coupled`, `acwr_band`, `sessions` (as-of day), visual filter
   `Is As-of = 1`, sorted by `[Load vs Norm %]`.

### 4.3 Injury report

The NFL practice/injury report as the department files it.

1. Cards: `[Open Injuries]`, `[Out]`, `[Limited]`.
2. **Table** from Injury Status (as-of day) + Injury + Player: Player, Pos,
   `Injury[body_part]`, `Injury[side]`, `Injury[injury_type]`,
   `Injury[severity]`, `Injury Status[practice_status]` (DNP red / LP amber /
   FP green), `Injury Status[game_status]`, `Injury[event_date]`,
   `Injury[expected_rtp]`, `Injury[days_out]`. Visual filter `Is As-of (Injury) = 1`;
   sort practice status DNP → LP → FP, then Player.
3. **Bar** — count of Injury by `body_region`, season to date. **Matrix** —
   `severity` × `body_region`, count. (Filter both to `event_date ≤ [As-of]`
   with a measure if you want them to track the as-of day; otherwise season.)

### 4.4 Data (one screen)

Cards: `[Last Run]`, `[Reconciles]` (font red when it says FAILED),
`[Rows In]`, `[Rows In Silver]`, `[Quarantined %]`, `[Expectations]`.
One table from DQ Run filtered `run_recency = 1`: `source`, `rows_in`,
`duplicates_removed`, `rows_quarantined`, `rows_in_silver`, `reconciles`,
`expectations_failed`, `pct_units_inferred`. One line chart:
`rows_quarantined` by `created_at`, legend `source`, last 12 runs
(`run_recency ≤ 12`). Title the page *Why you can trust the numbers*.

### 4.5 Player (drill-through only)

Page → *Format → Page information → Drillthrough*: add `Player[full_name]`,
*Keep all filters* on. Hide the page from navigation (right-click tab → Hide).

Top: the player's name (Card `SELECTEDVALUE(Player[full_name])`), Pos,
Team; Cards for the as-of row: `flag` (colour by value), `availability`,
`game_status`, `readiness_score`, `acwr_coupled` + `acwr_band`, `weight_kg`;
a text-style Card for `flag_reasons`. All with `Is As-of = 1`.

Body (all with visual filter `In Window = 1`; Window slicer on this page):

1. **Line** — `acwr_coupled` and `acwr_ewma` by `Date[date_day]`; constant
   lines at 0.8 (green), 1.3 (amber), 1.5 (red) via *Analytics → Constant line*.
2. **Column** — `external_load` by day.
3. **Line** — `readiness_score` and `readiness_28d_mean` by day; markers on.
4. **Scatter** — Force Plate Test `asymmetry_pct` by `test_date` (small markers)
   with `asymmetry_4w_avg` as a line (second line visual layered, or use a
   line-and-clustered-column). Constant lines at ±10.
5. **Line** — `weight_kg` by day (carried forward).
6. **Table** — this player's injuries: `body_part`, `side`, `severity`,
   `event_date`, `return_date`, `days_out`, `expected_rtp`.

**Check:** from Today, right-click a red player → Drill through → Player.
The header reads the same flag and reasons as the board row.

---

## Part 5 — Save, publish, share

1. **File → Save as** → `powerbi\GridironOps.pbip` (choose *Power BI project
   files* as the type). This writes `GridironOps.Report/` and
   `GridironOps.SemanticModel/` folders — text, so git diffs them. Commit.
2. Also **File → Export → Power BI template (.pbit)**? No — keep one artefact.
   For sending, **File → Save as → .pbix** to `powerbi\GridironOps.pbix`
   (binary; commit it too, it's small).
3. **Home → Publish** → your school workspace.
4. In the Service: open the report → **File → Embed report → Publish to web
   (public)** → copy the link into the README. If the option is greyed out
   your tenant admin has disabled it; use **File → Embed report → Website or
   portal** (needs sign-in) or share the `.pbix`.
5. Screenshots of each page → `docs/screenshots/` and a Dashboard section in
   the README pointing at the public link.

Refreshing later: `Home → Refresh` in Desktop re-imports from Postgres. The
published copy won't refresh on its own (localhost isn't reachable from the
Service) — publish again after a refresh.

---

## If something's off

| Symptom | Cause | Fix |
|---|---|---|
| `gold` schema missing in Navigator | `dbt_run` hasn't completed | wait for the next DAG run; Airflow → Grid |
| Team slicer shows KC | old volume | `docker compose down -v && docker compose up --build -d` |
| Cards blank after changing *Days back* | measure not in a visual with the AsOf slicer on the page | slicers must be on the page (synced), not just visible |
| Player page shows every date | `In Window` filter missing on that visual | add the measure filter = 1 |
| Ambiguous-relationship warning | Injury Status → Player relationship exists | delete it; Injury Status reaches Player through Injury |
| Flag colours wrong | conditional formatting by *Field value* needs a colour column | use *Rules* on `flag`: red → #D63A2F, amber → #D98A00, green → #1E8A2E |

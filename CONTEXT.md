# GridironOps

An NFL performance-department data product: five vendor feeds, nflverse and weather, reconciled into one player identity and served to performance and medical staff who decide who trains, who plays and who is flagged. The report reader is that staff; the pipeline is what makes the numbers trustworthy.

## Language

### People and time

**Player**:
One athlete on the roster, identified by a stable `player_sk` regardless of how each vendor names them.
_Avoid_: athlete, user

**Squad**:
The set of players the report is currently scoped to — a team, or everyone.
_Avoid_: roster (the nflverse list), team (the franchise)

**As-of date**:
The day the report is read for; defaults to the latest day with any data. Every "today" and "last 7 days" is relative to it.
_Avoid_: current date, run date, season week (a calendar bucket, not a reading date)

### Availability

**Practice status**:
The player's participation in practice on a day, in NFL injury-report terms: DNP (did not participate), LP (limited), FP (full).
_Avoid_: training status, availability (the derived summary)

**Game status**:
The player's designation for the next game, in NFL injury-report terms: Out, Doubtful, Questionable, or none.
_Avoid_: match status, fitness

**Availability**:
The plain-language summary of practice status: Out (DNP), Limited (LP), Available (FP or no open injury).
_Avoid_: status (ambiguous between practice and game)

**Open injury**:
An injury with no return date yet; it carries the current practice and game status.
_Avoid_: active injury, case

**Expected return**:
The medical staff's current estimated return-to-play date for an open injury.
_Avoid_: RTP date, ETA

### Monitoring

**Flag**:
A single Red / Amber / Green reading of a player on the as-of date. Red: Out, or game status Out/Doubtful, or ACWR high. Amber: Limited, or any other open injury, or ACWR elevated, or readiness 1.5+ below the player's own 28-day mean, or asymmetry above 10 %, or a silent player. Green: none of the above. Every non-green flag carries its reasons.
_Avoid_: risk score, alert, traffic light (the colours, not the concept)

**Silent player**:
A player with no wellness survey in the last 3 days. Amber by definition.
_Avoid_: non-responder, missing survey

**External load**:
Catapult player load for a session or day.
_Avoid_: workload (ambiguous with internal load), volume

**Internal load**:
Session RPE × session minutes.
_Avoid_: sRPE (the input), perceived load

**ACWR**:
Acute:chronic workload ratio — 7-day load over 28-day average load, on external load.
_Avoid_: load ratio, A:C

**ACWR band**:
The named range an ACWR falls in: low (<0.8), sweet (0.8–1.3), elevated (1.3–1.5), high (>1.5).
_Avoid_: zone, risk band

**Readiness**:
The 1–10 composite of a player's morning wellness answers (sleep, soreness, fatigue, stress, mood).
_Avoid_: wellness score, wellbeing

**Asymmetry**:
Left–right force difference on a force-plate test, as a percentage.
_Avoid_: imbalance, bilateral deficit

### Trust

**Quarantine**:
Rows that failed a validation expectation and were kept aside with the reason, never reaching Gold.
_Avoid_: rejected rows, errors

**Reconciles**:
For one source and one run: rows in − duplicates − quarantined = rows in Silver. The number the report shows to prove itself.
_Avoid_: balances, checks out

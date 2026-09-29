# Hunger Recovery Plan

**Goal:** Hunger damage heals. Acute danger fades within days of eating again, while the body rebuilds over months, and scarce food is shared instead of always starving the same people.

**Builds on:** Travel provisions (PR #7). It changes starvation for everyone, at home and on the road.

## Why

Under the old rule, every unfed day permanently added 0.1% to a person's daily chance of dying. 63% of people survived a 30-day famine, but none of those survivors were still alive a year later. The rule was a delayed death sentence, not a famine.

## Evidence

- **Survival without food.** Death from total starvation, with water available, comes after about 45–70 days; the Northern Ireland hunger strikers died between day 45 and day 73 ([PMC5257573](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5257573/)). Glycogen is exhausted after roughly 10–14 days.
- **Half rations.** In the Minnesota Starvation Experiment, men lived on about half their normal food for 24 weeks, lost 19–28% of body weight, and none died ([Kalm & Semba 2005](https://www.researchgate.net/publication/7812569_They_Starved_So_That_Others_Be_Better_Fed_Remembering_Ancel_Keys_and_the_Minnesota_Experiment)). Rehabilitation took about 20 weeks and was still incomplete ([Duke](https://psychiatry.duke.edu/blog/starvation-experiment)).
- **Refeeding danger.** It is concentrated in the first 1–5 days after food returns ([PMC2654033](https://pmc.ncbi.nlm.nih.gov/articles/PMC2654033/)).
- **Fertility.** Losing about 15% of body weight commonly stops ovulation, and it takes months to return after recovery ([Hum Reprod Update 2021](https://academic.oup.com/humupd/article/27/1/130/5927574)). Births fell by up to half in the Dutch Hunger Winter ([IJE](https://academic.oup.com/ije/article/36/6/1196/814573)).
- **Simulation precedents.** Several games separate a fast hunger state from slow body condition, or cut births and survival during famine: Project Zomboid (hunger versus weight), RimWorld (malnutrition severity) and Victoria 3 (starvation modifiers).

## Rules

1. **Acute hunger (`nutrition_debt`).**
   - It rises by 1 on each unfed day and halves (integer division) on each fed day, so 30 → 15 → 7 → 3 → 1 → 0.
   - The first 10 points add no risk of death, standing in for the body's reserves. Above that, each point adds 0.1% daily risk, capped at 70% as before.
2. **Body condition (`health_bp`, 0–10 000).**
   - It falls by 100 on each unfed day and rises by 35 on each fed day, up to 10 000. Loss is therefore about three times faster than recovery.
   - At 0, the existing "critical health" rule applies and the person dies.
3. **Order within a day.** Hunger is recorded when people go unfed. Recovery is applied only after that day's death roll, so the first day food returns is still dangerous. This stands in for refeeding risk.
4. **Fertility.** Nobody below 8 000 health can conceive, men or women. Founders start between 8 000 and 10 000, so healthy people are never barred.
5. **Sharing scarce food.** When home stores cannot feed everyone, the hungriest eat first. People are ordered by nutrition debt (highest first), then lowest health, then ID. Those left unfed today eat tomorrow, so shortage is shared rather than always falling on the same people.
6. **Travellers follow the same rules.** Eating from the pack or foraging successfully counts as a fed day. Failing to forage counts as an unfed day.

## Checked behaviour

| Scenario | Result under these rules | Reference |
|---|---|---|
| Total starvation | Half dead by day 47, 90% by day 77 | Deaths around days 45–73 |
| Half rations for 168 days | No acute risk. Conceiving stops at about day 61. Health gives out only around day 307. | Minnesota: 24 weeks, no deaths |
| 30-day famine, then plenty | 81% survive the famine and 98% of those survive recovery. Fertile again about a month later; fully recovered after about 3 months. | Recovery over months, still incomplete at 20 weeks |

## Deliberately out of scope

- **Permanent stunting** of children starved early in life. Stunting in the first 1,000 days is largely irreversible, but modelling it needs growth stages the simulation does not have yet.
- **A separate refeeding-syndrome mechanic.** Rule 3's one dangerous day stands in for it.
- **Miscarriage or reduced birth weight** during famine. Only conception is gated.
- **Disease.** `disease_load` is never raised today, so there is nothing yet for hunger to amplify.
- **Tuning.** 35 health points per fed day gives full recovery in about 3 months. 25 would give about 4, closer to Minnesota. This is the obvious number to tune after observing real runs.

## Validation

- **Focused tests:**
  - The grace period.
  - The loss and recovery arithmetic.
  - Half rations: no acute risk, but wasting and infertility.
  - Nobody conceives below 8 000 health.
  - Scarce food is shared.
  - Famine survivors recover.
  - Travellers heal and waste by the same rules.

  Every one of these tests fails on the old rules.
- **Existing suites:** the normal and stress suites stay green, and existing seeded histories shift only where health now moves.

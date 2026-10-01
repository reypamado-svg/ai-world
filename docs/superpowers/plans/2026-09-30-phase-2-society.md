# Phase 2 Society Plan: Languages, Espionage, Institutions and Assimilation

This plan covers the four Phase 2 items the earlier plans left out. It also covers the scenario tests that close Phase 2.

## What the design spec requires

- **Languages and translation (§10).** Messages pass through mortal ambassadors who must "translate it". Language skill, cultural familiarity, literacy, stress, bias and travel delay affect fidelity. The chronicle keeps both the source text and the delivered text.
- **Espionage (§4, civilization layer).** It sits alongside exploration, messages, trade and migration as a way to learn about others. Reports "may be delayed, incomplete, or wrong".
- **Institutions (§7).** "Repeated social practices can become institutions when material and knowledge prerequisites exist." The spec's examples are schools, archives, courts, taxation, professional workshops, medical practice, organized religion around the sovereign, military organization, and diplomatic services.
- **Assimilation (§11).** Assimilated people keep their ancestry, memories, language, skills, relationships and cultural practices while becoming subjects of their new sovereign. This allows blended societies and conquest without extermination.
- **Phase 2 exit.** Scripted sovereigns complete the scenarios for isolated development, first contact, treaty, trade, war, surrender, assimilation, extinction and final survivor. All replays match, and tests prove that no civilization's report contains hidden knowledge.

## What exists today

| Item | State |
|---|---|
| Message fidelity | A flat 15% chance that a delivered message is tagged "distorted by ambassador" |
| Languages and culture | None; everyone understands everyone |
| Espionage | None; civilizations learn only from explorers, contacts, war reports and envoys |
| Institutions | None; capabilities and their practitioners stand in for all organized knowledge |
| Allegiance | W3c keeps a person's history and holds back a quarter of their skills for a year; there is no idea of culture |

## Proposed slices

1. **S1: languages and translation.**
   - **Languages:** every civilization starts with its own language. Each person knows languages at a fluency from 0 to 100, with their native language at 100.
   - **Learning:** people learn a language by living among its speakers: newcomers, captives, occupiers, petitioners and the residents of a ceded settlement.
   - **Translation:** a message's fidelity depends on the ambassador's fluency in the recipient's language, and on whether the sender writes. Poor fidelity drops or garbles words deterministically, instead of adding a tag.
   - **Records:** the chronicle keeps both texts, and the recipient sees only what arrived.
2. **S2: espionage.**
   - **Spies:** a `send_spy` order sends a person to watch a foreign settlement for a number of days.
   - **Findings:** the spy estimates its people, fighters, store, walls and works in progress. The estimates are rounded, and are wrong more often for spies who do not speak the language.
   - **Getting caught:** each day there is a chance of capture, lower for fluent and skilled spies. A caught spy becomes a prisoner (W3a), and the target learns who sent them.
   - **Reports** reach home only when the spy does.
3. **S3: institutions.**
   - **Founding:** an institution is founded at a settlement with a building, which is a construction project, and permanent staff. It needs the capability its practice rests on.
   - **Upkeep:** while staffed, it has a lasting effect. Its staff eat but do no other work.
   - **A first set of five:**

     | Institution | Needs | Effect |
     |---|---|---|
     | Archive | writing | a capability recorded there is never forgotten while the archive stands and is staffed |
     | School | writing | teaching takes half as long, and a teacher may take two apprentices |
     | Healers' house | herbal care | wounded and sick people at the settlement recover twice as fast |
     | Workshop | timbercraft or stoneworking | crafting and building at the settlement go 25% faster |
     | Diplomatic service | writing | its ambassadors deliver messages more faithfully and gain fluency faster |

4. **S4: culture and assimilation.**
   - **Culture:** every person carries their `culture`, the civilization they grew up in, and their ancestry, which is kept for life.
   - **Assimilation:** a person living in a civilization not of their culture assimilates year by year, faster when they speak its language.
   - **Children:** they take the culture of the civilization they are born into.
   - **Measuring it:** the council report shows how blended its society is.
5. **S5: Phase 2 exit scenarios.**
   - **Acceptance tests:** full scenarios with scripted sovereigns, each replayed exactly: isolated development, first contact, treaty, trade, war, surrender, assimilation, extinction and final survivor.
   - **No hidden knowledge:** a leak test proves that no council report contains anything its civilization has not observed or been told.

## Decisions

1. **Institutions:** all five, the archive, school, healers' house, workshop and diplomatic service, as in the S3 table.
2. **Spy reports** come home with the spy, or earlier by courier.
   - **A courier:** a spy on watch may send a companion home with the findings so far. The report arrives sooner, but each day a courier is on the road also carries its own chance of discovery.
   - **If the spy is caught,** findings not yet sent are lost.
3. **Languages:** one per civilization. People learn others by living among their speakers.
4. **Assimilation** is a record, with its effects on language and on children's culture. There are no penalties for being unassimilated.

## Order of work

S1 languages, then S2 espionage, which needs S1 for its capture chances and estimate errors, then S3 institutions. S4 assimilation needs S1's languages. S5 comes last and ends Phase 2. Each slice goes in its own pull request, stacked like the war slices.

## S1 as built

- `languages.py`: each civilization has one language, named by its id. A person speaks
  their `native_language` (their birth civilization) at 100 and others at a learned level
  in `Person.languages`; 50 counts as fluent.
- Every 5 days, people standing on the same tile learn a point in each language spoken
  there (natively or fluently), up to 100.
- An envoy's fidelity is their fluency in the recipient's language, plus 30 if they can
  write, never below 20 (gestures and names). Each word of a message survives with that
  chance; lost words arrive as "…". This replaces the flat 15% distortion tag, and the
  chronicle's `distorted` flag still marks any change.
- People who change allegiance keep their mother tongue; council reports list
  `speakers` for each language among the civilization's free people, so a sovereign can
  choose an envoy who will be understood.
- Not yet: children learn the language of the civilization they are born into (S4 adds
  assimilation), and writing is a skill no one has until S3's school teaches it.

## S2 as built

- `send_spy` sends one to four people from a settlement to a known foreign settlement,
  to watch it for 1 to 90 days. They pack food for the walk and the watch, up to what
  they can carry, and forage the rest.
- Each day on watch, the party is found out with a 1% chance, less 0.5% if its least
  careful member speaks the language and 0.05% per point of their spycraft (at most
  0.5%), never below 0.1%. Otherwise the spies update their estimate of the settlement:
  residents, fighters and store (rounded to 50), each off by up to 10% for a spy who
  speaks the language and 30% for one who does not; walls, towers and works going up are
  seen exactly.
- `send_courier` at a council sends one of a watching party home with the findings so
  far and a share of the food. Each day a courier spends on the watched civilization's
  land, it is stopped with a 0.5% chance (with the same reductions).
- Caught spies and couriers become prisoners at the nearest settlement of the civilization
  that caught them, which records who sent them (`caught_spies`). Findings not yet sent
  home are lost.
- Findings reach the council only when the spies or a courier are home (`spy_reports`);
  `spy_missions` lists the parties still out. Spies who come home gain a point of
  spycraft.
- A war party does not ambush spies on watch; they pass for locals or keep out of sight.

## S3 as built

- `found_institution` names a kind and one to four founders standing at one of the
  civilization's settlements. The civilization must know what the practice rests on, and
  each settlement has at most one of each kind. The building's materials leave the store
  at once; the founders raise it, then stay on as its staff. Staff eat but do no other
  work, travel, drill or teach.
- `staff_institution` replaces an institution's staff with up to four people standing at
  it, or with no one.
- An institution is open while its building stands and one of its staff is there, free
  and not away. It is lost with its settlement; if every founder dies before the building
  is finished, the materials go back into the store.

| Institution | Needs | Materials | Person-days | Effect while open |
|---|---|---|---|---|
| Archive | writing | 30 timber, 30 stone | 20 | every capability the civilization knows is kept, even after its last practitioner dies |
| School | writing | 40 timber | 20 | teaching there takes 15 days instead of 30, and a teacher may take two apprentices |
| Healers' house | herbal care | 30 timber | 15 | fed people at the settlement recover twice as fast |
| Workshop | timbercraft or stoneworking | 30 timber, 20 stone | 20 | every fourth day, crafters and builders of storehouses, walls and institutions at the settlement each do a day extra (25% faster) |
| Diplomatic service | writing | 30 timber | 15 | the civilization's messages keep 20 points more of their words, and its staff gain a point every 5 days in the language of each civilization it knows |

- Teaching now has a limit everywhere: one apprentice per teacher at a time, two at a
  school.
- Not yet: ordinary construction projects (shelters and granaries) get no workshop bonus,
  because their labour is counted in the shared work step.

## S4 as built

- `culture.py`: each person lives by a `culture`, which is their civilization's until they
  move to another. Their `ancestry`, the cultures their forebears came from, is kept for
  life.
- A newcomer keeps their culture and starts at 0 `assimilation`. Every 30 days they gain 3
  points, or 6 if they speak their new civilization's language. At 100 they are its people:
  their culture becomes its culture. That takes about three years, or a year and a half in
  the language. Someone who returns to the civilization of their culture is simply home.
- Children take the culture of the civilization they are born into, the joined ancestry of
  both parents, and, when their mother is a newcomer, her language at 50 fluency.
- Council reports show how blended the society is: `cultures` and `ancestries` (counts of
  living free people) and how many are still `assimilating`.
- Assimilation is a record only: nobody is held back for being unassimilated.

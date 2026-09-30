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

## Open questions for the user

1. **Institutions:** which of the five above go into the first set?
2. **Spy reports:** do they come home only with the spy, or can a spy also send a courier?
3. **Languages:** one per civilization, or families of related languages that are easier to learn from one another?
4. **Assimilation:** purely a record, or with effects? For example, an unassimilated person might work less gladly, or leave for their old civilization if it still exists.

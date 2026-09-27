# Civilization Layer Slice B Plan: Contact and Diplomacy

**Goal:** Add physically discovered foreign contacts and mortal ambassador missions with deterministic delivery, delay, loss, and distortion.

1. Add serialized contact and message records. A message retains immutable source words and, when delivered, the received representation and outcome.
2. Detect first contact only when an expedition reaches another civilization's settlement; add the contact only to the explorer's civilization.
3. Add a validated `send_message` direct order. It must use a living local ambassador, a previously discovered contact, and a known adjacent route ending at that contact.
4. Advance missions one tile per day. A named deterministic stream can delay or lose a mission; arrival can distort the delivered representation. A dead ambassador loses the mission.
5. Add each delivery only to the receiving civilization's records and report. Preserve canonical hashes, event order, persistence, and replay.
6. Verify unit, command, engine, privacy, replay, normal, and soak tests before opening a separate pull request.

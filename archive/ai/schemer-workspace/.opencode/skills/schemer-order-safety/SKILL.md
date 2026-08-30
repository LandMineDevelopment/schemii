---
name: schemer-order-safety
description: Use for any proposal involving widgets so user-owned array order and vertical viewport remain intact.
---

# Order Safety

- Never propose coordinates, dimensions, or viewport offsets. Schemer cards use a uniform responsive grid with no saved geometry.
- New and duplicated widgets append to array order.
- Rename and delete proposals must not reorder unrelated widgets.
- Preserve every existing widget's array order unless the user uses Schemer's order controls.

# Mobile card containment

Long result lists could clip cards on narrow screens. Offscreen entrance wrappers
use an intrinsic placeholder size of 380px; the grid item's automatic minimum
width let that become a column minimum. The grid hid the overflow, so checking
only document scroll width missed the clipped bedroom badge and action controls.

`min-width: 0` on the entrance wrapper lets the grid constrain it to the available
column. Card content, entrance animation and offscreen rendering remain intact.

Browser verification used 24 authored fictional records, an isolated database,
blank provider credentials and no scheduler. Restoring the old `min-width: auto`
in the browser reproduced a 380px card inside a 358px grid at a 390px viewport;
removing that override restored the repository fix and a 358px card.

| Viewport | Grid width | Cards outside grid after repair |
| --- | ---: | ---: |
| 320px | 288px | 0 / 24 |
| 390px | 358px | 0 / 24 |
| 768px | 732px | 0 / 24 |
| 1440px | 1400px | 0 / 24 |

The [mobile capture](../../output/playwright/mobile-card-390.png) and
[desktop capture](../../output/playwright/mobile-card-desktop.png) show only
fictional fixtures. Real operating-trial screenshots remain local and untracked.
No Python behavior changed; lint, formatting and typing checks passed.

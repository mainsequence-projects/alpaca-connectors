# Packer

Packer is the Alpaca Agent's Command Center character: a cream pixel alpaca carrying a navy saddle pack that holds a small chart card. These
five transparent PNGs are owned by this repository and declared by
`alpaca-connectors-agent` in `.mainsequence/workflows/alpaca-connectors-api.yaml`.

| Asset | SDK selection | Pose |
| --- | --- | --- |
| `idle.png` | Default icon and fallback | standing, ears up, chart card in the pack |
| `thinking.png` | Agent working on a turn without an active tool | head tilted, one ear folded, three navy dots above |
| `working.png` | Active tool without an application-specific pose rule | trotting, pack flap open, motion ticks on the left |
| `trading.png` | Active tool belonging to `alpaca` | a candlestick card held in the mouth with a mint up-tick |
| `error.png` | Failed tool or run | ears down, pack strap loose, amber exclamation badge |

The files use preserved-color rendering with `image_rendering: pixelated`.
They are static activity poses, not APNG loops. Tool mapping is by declared
MCP application; it does not distinguish individual tools or operations.
The SDK owns pose selection and authenticated image delivery. No application
code, new tools, Agent Card fields or extra access grants are needed.

All runtime images are square 128 x 128 RGBA PNGs, under the platform's
128 x 128 px and 64 KiB per-file limit for agent icons. The platform reads them from the exact repository
event commit; a local edit alone does not publish the icon.

## Artwork provenance

The character follows the Casekeeper recipe from the Back Office helper: a
64 x 64 pixel grid upscaled 2x with nearest-neighbour sampling, dark navy
outline, body about 60 percent of the canvas width, feet or base line at about
88 percent from the top, three square thinking dots, motion ticks when working
and an amber exclamation badge on error. The sprites were drawn
programmatically on 2026-10-21; regenerate them from the same grid rather than
editing the upscaled PNGs by hand.

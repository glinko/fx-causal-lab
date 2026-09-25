---
name: FX Causal Lab
description: A simple research journal for measured EUR/USD evidence.
colors:
  paper: "#f7f7f2"
  ink: "#172c2b"
  muted: "#51625f"
  line: "#cbd3ca"
  accent: "#175d50"
  white: "#fff"
  warn: "#78501a"
  inset: "#e9eee5"
typography:
  headline:
    fontFamily: "PT Serif, Georgia, serif"
    fontSize: "27px"
    fontWeight: 400
    lineHeight: 1.2
  body:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
  detail:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "14px"
  metadata:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "13px"
spacing:
  compact: "12px"
  medium: "20px"
  section: "40px"
components:
  navigation-link:
    textColor: "{colors.accent}"
    typography: "{typography.detail}"
  status:
    textColor: "{colors.muted}"
  report-link:
    textColor: "{colors.accent}"
    padding: "18px 0"
---

# Design System: FX Causal Lab

## Overview

**Creative North Star: "Research journal"**

A simple research journal combines evidence tables with a full-width measured series. Light paper, dark green ink and restrained warning ochre support detailed reading by one researcher. The interface remains an operating and reading surface, with no decorative charts or fabricated metrics.

Serif journal headings frame familiar sans-serif controls and tables. The chart is a working plot with date ranges and exact values; limitations remain adjacent to the evidence. Local assets and a locally served display font keep the journal independent of external font services.

The post-MVP readiness review extends that journal into a decision ledger. It puts the technical and research verdicts side by side, then moves through gate evidence, the first acquisition action and a ranked data queue. Counts remain audit gates rather than optimistic KPIs, and the queue explains its coverage score instead of implying price or expected return.

The acquisition review carries the same ledger into procurement. Provider claims become an acceptance contract before purchase, while a decoded EUR/USD tick sample proves the free transport path without implying strict point-in-time readiness. The split verdict, measured sample, vendor comparison and acceptance checklist keep what was observed separate from what remains documented or unverified.

**Key Characteristics:**
- Light reading surface and green ink.
- Evidence ledgers, a working measured series, a ranked data queue and an acquisition acceptance contract.
- Adjacent caveats and explicit provenance.
- Stacked mobile headings and contained table scrolling.

## Colors

The palette is quiet paper and green ink, with ochre reserved for caution.

### Primary
- **Green ink accent:** links, disclosure controls and the measured chart line.

### Neutral
- **Paper:** page background.
- **Ink:** primary reading text and identity.
- **Muted ink:** explanatory text, coverage, axes and metadata.
- **Line:** table rules, container outlines and page dividers.
- **White:** controls and the plot reading surface.
- **Inset:** notices and document code blocks.

Warning ochre supports written caution statuses. Status wording carries meaning independently of color.

**The Evidence Rule.** Keep data limitations next to the measured evidence they qualify.

## Typography

**Display Font:** locally served PT Serif, with Georgia and serif fallbacks.
**Body Font:** Segoe UI, with Arial and sans-serif fallbacks.
**Label/Mono Font:** monospace for hashes.

The serif supplies journal character; the sans-serif maintains legible data and controls. Section headings use the headline token. Journal titles are slightly larger (30px desktop, 27px mobile); document titles use 32px. Tables and controls use the detail role, while coverage and captions use metadata. Table headers use compact uppercase labels (12px, weight 600, .04em tracking). These are column labels, not decorative section kickers.

**The Reading Rule.** Use serif headings to structure the journal and sans-serif text for evidence, navigation and controls.

## Layout

The journal uses a centered reading column, with a main maximum width of 1240px and 40px horizontal padding. Documents narrow to 1120px. Sections have 40px top separation; document prose is constrained to 80ch. The chart spans the available content width, followed by its caveat and a disclosure for recent values and provenance.

Decision reviews lead with a two-column verdict divided by one rule, followed by four equal gate cells and one bordered first-action block. Milestone evidence uses ruled three-column rows; platform decisions and the ranked data queue follow beneath in reading order. This sequence makes the next research investment visible in the first viewport without detaching it from its evidence.

Acquisition reviews keep the dual procurement decision and four measured sample counters in the first viewport. A two-column sample ledger follows, then vendor rows, the numbered acceptance contract, documented constraints and reproducibility evidence. The order lets the researcher decide whether transport works before reading provider claims, while the final hashes and downloads keep the result reproducible.

At 700px and below, page padding becomes 20px, headings and controls stack, the edition label disappears and report links stack their descriptive text. The dual verdict becomes one column, the four gates become a two-by-two grid, and milestone and platform-decision rows collapse into a single reading flow. Source and ranked-queue tables retain a readable minimum width inside horizontally scrollable, keyboard-focusable containers. The page itself should fit the viewport. The plot switches to a shorter drawing and fewer axis labels below a measured width of 600px.

## Elevation & Depth

Surfaces are flat. Thin rules and restrained paper, inset and white backgrounds distinguish sections; no shadows are used. Report links gain a quiet tinted background on hover. Keyboard focus uses an ochre outline with an offset, rather than simulated elevation.

## Shapes

Reading surfaces and evidence tables remain rectangular. Native selects have a small corner radius (4px) and a thin green-gray border. Tables use horizontal rules, without separate rounded cells or card wrappers.

Decision ledgers and metric grids use shared outer rules and square cells. The first-action block is the one bordered white callout in the readiness sequence; its restraint preserves the journal form instead of introducing a dashboard card language. Sample facts, provider comparisons, numbered acceptance checks and documented constraints remain ruled rows rather than detached cards.

## Components

### Controls and navigation

Text links and native selects keep operations simple. Series and period selects have visible labels; series controls stack and fill the available width on mobile. Links, selects and disclosure summaries share a visible keyboard focus outline (3px, offset 4px). Navigation wraps with the header. The journal includes a focus-revealed skip link.

### Evidence tables and statuses

Tables align text left, use restrained uppercase column headings, and separate rows with thin rules. Row labels are emphasized; supporting technical details are smaller and muted. Neutral, validated and caution states retain explanatory text. Dense tables scroll within their container.

### Readiness decision ledger

The readiness surface pairs a plain-ink technical verdict with an ochre research blocker. Four compact research gates sit below as equal ruled cells; they are evidence checks, not celebratory KPI tiles. A bordered white action block then names the first concrete data-acquisition check. Milestone evidence, platform deferrals and the ranked data queue continue as ruled rows and tables, with muted explanatory copy adjacent to every status and score.

**The Decision-Ledger Rule.** Every readiness verdict must expose the evidence, limitation and next action that make the decision auditable.

### Acquisition acceptance ledger

The acquisition surface pairs a positive narrow-window FX transport verdict with an ochre blocked procurement verdict. Four compact counters report only decoded ticks, normalized M1 rows, event-window minutes and strict PIT experiments. A sample ledger then exposes timestamps and spread statistics before any vendor comparison. Provider rows distinguish documented capability, access, research fit and unverified claims; the numbered acceptance list applies one small export to every check before payment or integration. Documented constraints, source snapshots, hashes and downloadable artifacts complete the chain of evidence.

**The Acceptance-Before-Purchase Rule.** Provider claims remain provisional until the same representative export passes vintage semantics, coverage, licensing, price and reproducibility checks.

### Report links and disclosures

Reports are full-width ruled text rows, pairing a title with quieter explanatory text. The whole row is a link with a hover tint. Native details/summary discloses recent values and provenance without leaving the plot.

### Working plot

A thin green series sits on white with muted grid lines and axes. Pointer inspection exposes the date and exact value; recent observations also have a table representation. Series changes update the description, accessible chart label, caveat, coverage and download target together. D1, H1 and ECB retain their distinct source and timing language. Missing H1 hours break the line; incomplete daily sessions are not drawn as complete evidence. Loading, empty and failure states use plain explanatory text. The ECB reference series is explicitly distinguished from a tradable candle.

Smooth document scrolling is disabled when reduced motion is requested. Chart changes are immediate and do not add ornamental animation.

## Do's and Don'ts

### Do:
- **Do** retain adjacent caveats, provenance and explicit data status.
- **Do** keep series labels, accessible names and downloads consistent with the selected data.
- **Do** preserve keyboard focus and a table representation of recent values.
- **Do** stack mobile controls and contain dense table scrolling.
- **Do** pair readiness verdicts and ranked scores with their evidence, limitation and first concrete follow-up.
- **Do** show the decoded sample and its limitation before provider comparisons or purchase criteria.
- **Do** apply one acceptance checklist to every provider under consideration.

### Don't:
- **Don't** invent experiment results or turn the working plot into decoration.
- **Don't** imply that an available endpoint is validated research data.
- **Don't** bridge missing observations or conceal incomplete sessions as complete evidence.
- **Don't** present gate counts as optimistic KPIs or let a coverage score imply cost, value or expected return.
- **Don't** treat successful free tick transport as proof of strict PIT research readiness.
- **Don't** present documented provider capabilities or marketing claims as accepted coverage.

# Neural AFE Platform - PCB Design Directions

This directory contains the complete design direction set for a 128-channel neural analog front-end (AFE) platform intended for **in-vitro instrumentation**.

## Document Structure

| Document | Purpose |
|----------|---------|
| `00-board-architecture.md` | Board type decisions and system partitioning |
| `01-requirements-template.md` | Requirements freeze checklist (fill before design) |
| `02-block-architecture.md` | Schematic block definitions and interfaces |
| `03-schematic-capture.md` | Hierarchical schematic organization and workflow |
| `04-layout-rules.md` | Critical layout constraints for neural-level signals |
| `05-bringup-plan.md` | Systematic board bring-up and validation |
| `06-configuration-worksheet.md` | Your specific parameters (fs, egress, etc.) |

## Design Philosophy

1. **Noise is the enemy** — every decision trades off noise
2. **Testability is non-negotiable** — no injection path = weeks of guessing
3. **Separate analog from digital** — physically, electrically, thermally
4. **Lock requirements before drawing** — designing blind wastes boards

## Quick Start

1. Fill out `06-configuration-worksheet.md` with your specific parameters
2. Review `00-board-architecture.md` to confirm board partitioning
3. Complete `01-requirements-template.md` and freeze it
4. Follow schematic and layout documents in order

## Status

- [ ] Configuration worksheet completed
- [ ] Requirements frozen
- [ ] Schematic capture complete
- [ ] Layout complete
- [ ] Fabrication
- [ ] Bring-up complete

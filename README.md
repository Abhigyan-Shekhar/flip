# SwitchCrew

Your Discord server. Your AI workforce.

SwitchCrew is a planned Discord-native team of specialized agents using Python,
FastAPI, LangGraph, Switch, and Codex. This repository targets Track 1: Best
Business Workflow in the WeMakeDevs × Switch hackathon.

**Status: preparation only, October 29, 2026.** There is no application to run yet.
The supplied specification and official rules require the competition-specific
Switch setup and workflow to be built during November 4–7, 2026. Preparation
documents and isolated dependency investigations are dated separately.

- [Technical findings, architecture, and risks](docs/PREPARATION.md)
- [Build sequence and acceptance checks](docs/BUILD_PLAN.md)
- [Original supplied specification](docs/SPECIFICATION.md)

The first event-day deliverable is a tested FastAPI and SQLite foundation,
followed by a real Discord → PM → Research → Discord collaboration through
Switch. Coding, independent QA, approval, and GitHub publication follow that.
Voice must pass a separate DAVE receive/transmit feasibility gate.

No live Switch, Discord, Codex, voice, or GitHub integration has been validated.
The dependency investigation passed an isolated FastAPI request, LangGraph
execution with a SQLite checkpoint, and voice-package imports on Python 3.12.

Repository: [Abhigyan-Shekhar/flip](https://github.com/Abhigyan-Shekhar/flip).

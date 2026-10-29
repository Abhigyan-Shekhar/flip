# SwitchCrew — Complete Project Specification



Final architecture

Python + FastAPI + LangGraph + Switch

WeMakeDevs Hackathon — Track 1



Project name: SwitchCrew\
Tagline: Your Discord server. Your AI workforce.\
Target: Best Business Workflow — $2,000\
Hackathon: November 4–7, 2026

## 1. Product vision

SwitchCrew turns a Discord server into an AI-powered workplace where specialized agents work alongside humans.

Instead of having one general-purpose bot that answers everything, a Discord server gets an entire team of agents:

- A Voice Agent that participates in meetings.
- A Project Manager that plans and delegates.
- An Engineering Agent that writes code through Codex.
- A QA Agent that independently validates results.
- A Research Agent that investigates problems.
- A Release Agent that manages GitHub and delivery.
- Additional agents for community support, documentation, onboarding, memory, and administration.

The agents communicate through Switch, use LangGraph for their individual workflows, and connect to real applications.

### The primary problem

Developer communities already discuss bugs, ideas, features, and decisions on Discord.

But the work usually happens somewhere else:

- Meetings produce notes that nobody follows up on.
- Bug reports disappear in busy channels.
- Developers manually convert discussions into GitHub issues.
- Context gets lost between contributors.
- Agents can generate changes, but people struggle to verify what they actually accomplished.

SwitchCrew connects the conversation to the execution.

### Core promise

A team should be able to discuss something in Discord and have the appropriate agents collaboratively turn it into a verified outcome.

# 2. System architecture

Discord

Text channels · Voice calls · Threads · Roles · Approvals

Switch

Shared rooms · Agent identities · Roles · Handoffs · Human collaboration

FastAPI Backend + LangGraph Agents

Task management · Agent execution · Workflow state · Permissions

Codex

Code execution

Connectors

GitHub · Linear · Docs

Memory

Waggle · Company Brain

## Technology decisions

| Component              | Technology             | Responsibility                                    |
| ---------------------- | ---------------------- | ------------------------------------------------- |
| Backend                | FastAPI                | APIs, webhook handling, task services, approvals  |
| Agent framework        | LangGraph              | Stateful agent logic, tools, retries, checkpoints |
| Collaboration          | Switch                 | Agent-to-agent and human-to-agent communication   |
| Discord                | Python Discord SDK     | Bot commands, interactions, voice connection      |
| Voice input            | Whisper-compatible STT | Transcription                                     |
| Voice output           | TTS provider           | Spoken replies                                    |
| Coding                 | OpenAI Codex           | Real repository modifications                     |
| LLM                    | OpenAI / Groq          | Reasoning, planning, classification               |
| Database               | SQLite                 | Task events, approvals, receipts                  |
| Long-term memory       | Waggle MCP             | Approved organizational knowledge                 |
| Code intelligence      | Company Brain          | Repository context and dependency analysis        |
| Developer integrations | GitHub API             | Issues, branches, pull requests, CI               |
| Observability          | LangSmith              | Tracing and evaluations                           |
| Deployment             | Docker                 | Reproducible local setup                          |

### A critical architectural boundary

We will not build a giant LangGraph that privately executes everything while Switch merely posts messages.

Instead:

- Every core specialist is independently addressable through Switch.
- Switch manages collaboration between agents and humans.
- LangGraph manages the internal logic of custom agents.
- Codex stays a separately connected coding agent.
- FastAPI owns our application-specific APIs and task records.

This matters because the hackathon explicitly evaluates whether Switch is essential to the application.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.wemakedevs.org\&sz=32)

WeMakeDevs x DevTools Academy x Switch



# 3. Complete agent roster

## Core workforce

| Agent             | Responsibilities                                                                        | Tools                                | Priority |
| ----------------- | --------------------------------------------------------------------------------------- | ------------------------------------ | -------- |
| Voice Agent       | Joins VCs, listens, speaks, clarifies requirements, extracts decisions and tasks        | Discord voice, STT, TTS, Switch      | P0       |
| Community Agent   | Handles support, classifies bugs/features, requests missing details, detects duplicates | Discord, GitHub, knowledge retrieval | P0       |
| PM Agent          | Creates plans, assigns roles, manages dependencies, tracks status and blockers          | LangGraph, Switch, task store        | P0       |
| Research Agent    | Studies code, documentation, previous discussions, related issues, and change impact    | GitHub, search, Company Brain        | P0       |
| Engineering Agent | Implements approved changes, fixes bugs, runs development commands                      | Codex, Git                           | P0       |
| QA Agent          | Reproduces issues, runs tests, checks acceptance criteria and regressions               | Test runner, Git, CI results         | P0       |
| Release Agent     | Prepares PRs/issues, requests approval, publishes authorized changes, posts receipts    | GitHub, Switch, approval service     | P0       |

## Extended workforce

| Agent               | Responsibilities                                          | Priority |
| ------------------- | --------------------------------------------------------- | -------- |
| Documentation Agent | README, API docs, tutorials, changelogs                   | P1       |
| Knowledge Agent     | Project memory, decision history, contextual Q&A          | P1       |
| Onboarding Agent    | Contributor onboarding, issue recommendations, setup help | P1       |
| Moderation Agent    | Flag harmful/spam content and request moderator decisions | P1       |
| Security Agent      | Secret scanning, permission checks, risk assessments      | P1       |
| Scheduling Agent    | Meetings, reminders, deadlines, events                    | P2       |
| Analytics Agent     | Agent performance, task completion, bottlenecks           | P2       |
| Incident Agent      | Alert investigation, diagnosis, remediation proposals     | P2       |

### Rules for every agent

Each agent must have:

1. A distinct identity.
2. A clearly defined role.
3. Explicitly permitted tools.
4. An internal workflow.
5. A structured output contract.
6. An escalation mechanism.
7. Access only to the relevant Switch rooms.
8. A way to report progress, failure, or completion.

An agent must be able to refuse work that is outside its permissions or for which essential information is missing.

# 4. Discord experience

Discord will be our primary interface. We will not require a separate dashboard for the hackathon.

## Server structure

| Channel             | Purpose                          | Agents                  |
| ------------------- | -------------------------------- | ----------------------- |
| `#ask-agents`       | General interaction              | Community, Knowledge    |
| `#support`          | Bug reports and support          | Community, Research, QA |
| `#feature-requests` | Proposals and ideas              | PM, Research            |
| `#agent-hub`        | Task coordination and delegation | PM and specialists      |
| `#engineering`      | Implementation collaboration     | Engineering, Research   |
| `#review-gate`      | QA findings and approvals        | QA, Release             |
| `#release-log`      | Completed tasks and PR updates   | Release                 |
| `#decisions`        | Approved project decisions       | Knowledge, PM           |
| `Meeting Room`      | Voice discussions                | Voice Agent             |

Individual tasks can use dedicated threads or Switch-linked work rooms where appropriate.

### Discord roles

- Admin
- Maintainer
- Developer
- Contributor
- Member
- Agent

Discord roles govern channel access and user permissions. Switch roles determine which specialist holds a particular responsibility.

### Bot commands

| Command             | Action                            |
| ------------------- | --------------------------------- |
| `/crew help`        | Display capabilities              |
| `/crew agents`      | Show agent roster                 |
| `/crew task`        | Create task                       |
| `/crew status`      | View task progress                |
| `/crew recap`       | Summarize a thread or meeting     |
| `/crew approve`     | Approve a specific pending action |
| `/crew reject`      | Reject with feedback              |
| `/crew cancel`      | Cancel task                       |
| `/crew voice join`  | Invite Voice Agent                |
| `/crew voice leave` | Stop capture and disconnect       |
| `/crew history`     | View execution evidence           |

Commands and buttons must call real backend operations, not just display conversational responses.

# 5. Main workflows

## Workflow 1 — Voice meeting to GitHub PR

Flagship demo

\#chatgpt-mermaid-\_r_1b9\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(237, 237, 237);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_1b9\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_1b9\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_1b9\_ .error-icon{fill:rgb(48, 48, 48);}#chatgpt-mermaid-\_r_1b9\_ .error-text{fill:rgb(237, 237, 237);stroke:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_1b9\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_1b9\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_1b9\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_1b9\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_1b9\_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1b9\_ .marker.cross{stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1b9\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_1b9\_ p{margin:0;}#chatgpt-mermaid-\_r_1b9\_ .label{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ .cluster-label text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ .cluster-label span{color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_1b9\_ .label text,#chatgpt-mermaid-\_r_1b9\_ span{fill:rgb(237, 237, 237);color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ .node rect,#chatgpt-mermaid-\_r_1b9\_ .node circle,#chatgpt-mermaid-\_r_1b9\_ .node ellipse,#chatgpt-mermaid-\_r_1b9\_ .node polygon,#chatgpt-mermaid-\_r_1b9\_ .node path{fill:rgb(9, 23, 44);stroke:rgb(31, 78, 148);stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .rough-node .label text,#chatgpt-mermaid-\_r_1b9\_ .node .label text,#chatgpt-mermaid-\_r_1b9\_ .image-shape .label,#chatgpt-mermaid-\_r_1b9\_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-\_r_1b9\_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .rough-node .label,#chatgpt-mermaid-\_r_1b9\_ .node .label,#chatgpt-mermaid-\_r_1b9\_ .image-shape .label,#chatgpt-mermaid-\_r_1b9\_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-\_r_1b9\_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_1b9\_ .root .anchor path{fill:rgb(175, 175, 175)!important;stroke-width:0;stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1b9\_ .arrowheadPath{fill:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1b9\_ .edgePath .path{stroke:rgb(175, 175, 175);stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .flowchart-link{stroke:rgb(175, 175, 175);fill:none;}#chatgpt-mermaid-\_r_1b9\_ .edgeLabel{background-color:rgb(0, 0, 0);text-align:center;}#chatgpt-mermaid-\_r_1b9\_ .edgeLabel p{background-color:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1b9\_ .edgeLabel rect{opacity:0.5;background-color:rgb(0, 0, 0);fill:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1b9\_ .labelBkg{background-color:rgba(0, 0, 0, 0.5);}#chatgpt-mermaid-\_r_1b9\_ .cluster rect{fill:rgb(48, 48, 48);stroke:rgba(255, 255, 255, 0.15);stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .cluster text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ .cluster span{color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:12px;background:rgb(48, 48, 48);border:1px solid rgba(255, 255, 255, 0.15);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-\_r_1b9\_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1b9\_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-\_r_1b9\_ .icon-shape,#chatgpt-mermaid-\_r_1b9\_ .image-shape{background-color:rgb(0, 0, 0);text-align:center;}#chatgpt-mermaid-\_r_1b9\_ .icon-shape p,#chatgpt-mermaid-\_r_1b9\_ .image-shape p{background-color:rgb(0, 0, 0);padding:2px;}#chatgpt-mermaid-\_r_1b9\_ .icon-shape .label rect,#chatgpt-mermaid-\_r_1b9\_ .image-shape .label rect{opacity:0.5;background-color:rgb(0, 0, 0);fill:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1b9\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_1b9\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_1b9\_ .node .neo-node{stroke:rgb(31, 78, 148);}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_1b9\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_1b9\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node .neo-line path{stroke:rgb(31, 78, 148);filter:none;}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_1b9\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_1b9\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1b9\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_1b9\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1b9\_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_1b9\_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_1b9\_ .node tspan[font-weight="normal"],#chatgpt-mermaid-\_r_1b9\_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-\_r_1b9\_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .node rect,#chatgpt-mermaid-\_r_1b9\_ .node circle,#chatgpt-mermaid-\_r_1b9\_ .node ellipse,#chatgpt-mermaid-\_r_1b9\_ .node polygon,#chatgpt-mermaid-\_r_1b9\_ .node path{fill:rgb(0, 40, 77);stroke:rgba(255, 255, 255, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_1b9\_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-\_r_1b9\_ .mermaid-decision .label-container{fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-dasharray:2,2;}#chatgpt-mermaid-\_r_1b9\_ .edgePaths .flowchart-link{stroke:rgb(175, 175, 175);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-\_r_1b9\_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1b9\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}Human discussion in DiscordVCVoice AgentConfirmed task andacceptance criteriaPM AgentResearch AgentCodex Engineering AgentIndependent QA AgentTests pass?Human approvalRelease Agent opens PRDiscord receipt and memoryNoYes

### Detailed behavior

Step 1 — Meeting participation

- A user invites the Voice Agent.
- The bot announces that it will transcribe with participants' consent.
- It listens to the meeting and can reply using TTS.
- It identifies potential requirements, decisions, and open questions.
- It does not treat casual conversation as an execution instruction.

Step 2 — Confirmation

The Voice Agent presents a proposed task:

- Title
- Description
- Acceptance criteria
- Repository
- Constraints
- Open questions

A human must confirm or correct it.

Step 3 — Planning

The PM Agent creates a task contract and selects the relevant specialists.

Step 4 — Investigation

The Research Agent gathers repository references, affected files, related issues, and constraints.

Step 5 — Implementation

Codex creates an isolated branch/worktree and implements the approved task.

Step 6 — Independent verification

QA runs relevant tests and checks whether acceptance criteria were met.

If verification fails, QA returns the failure evidence to Engineering.

Step 7 — Publication

The Release Agent prepares a PR proposal. An authorized human approves publication.

Step 8 — Completion

The original Discord thread receives:

- Task status
- PR link
- Test results
- Summary of changes
- Participating agents
- Approval record

## Workflow 2 — Support request to verified fix

Second demo

A community member reports an issue in `#support`.

The Community Agent:

1. Identifies it as a likely bug.
2. Requests reproduction information.
3. Checks for duplicates.
4. Consults the Research Agent.
5. Requests QA reproduction.
6. Creates a proposed engineering task.
7. Obtains maintainer authorization.
8. Routes the task through Engineering, QA, and Release.
9. Updates the original support thread.

The goal is to demonstrate that the same agent workforce handles both spoken and written requests.

## Workflow 3 — Repository and organizational knowledge

P1

Members can ask:

- “Why did we choose this database?”
- “Who owns the authentication service?”
- “What changed in the last PR?”
- “Which issues are blocked?”
- “What is the current release status?”

The Knowledge Agent retrieves source-grounded answers using connected documentation, GitHub, and optional Waggle/Company Brain integrations.

It should flag outdated information instead of presenting it as current fact.

## Workflow 4 — Contributor onboarding

P1

The Onboarding Agent welcomes members, explains the project, suggests beginner-friendly issues, and assists with environment setup.

It does not assign protected work or grant permissions autonomously.

## Workflow 5 — Documentation automation

P1

The Documentation Agent generates Markdown changes for approved features or merged changes.

Those changes undergo review before publication.

## Workflow 6 — Community moderation

P1

The Moderation Agent detects likely spam, abuse, or rule violations and prepares an explanation for moderators.

The MVP does not automatically ban users or modify roles.

# 6. LangGraph design

LangGraph handles stateful logic inside our custom specialists.

### Example: PM Agent graph

\#chatgpt-mermaid-\_r_1bg\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(237, 237, 237);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_1bg\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_1bg\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_1bg\_ .error-icon{fill:rgb(48, 48, 48);}#chatgpt-mermaid-\_r_1bg\_ .error-text{fill:rgb(237, 237, 237);stroke:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_1bg\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_1bg\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_1bg\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_1bg\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_1bg\_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bg\_ .marker.cross{stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bg\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_1bg\_ p{margin:0;}#chatgpt-mermaid-\_r_1bg\_ .label{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ .cluster-label text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ .cluster-label span{color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_1bg\_ .label text,#chatgpt-mermaid-\_r_1bg\_ span{fill:rgb(237, 237, 237);color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ .node rect,#chatgpt-mermaid-\_r_1bg\_ .node circle,#chatgpt-mermaid-\_r_1bg\_ .node ellipse,#chatgpt-mermaid-\_r_1bg\_ .node polygon,#chatgpt-mermaid-\_r_1bg\_ .node path{fill:rgb(9, 23, 44);stroke:rgb(31, 78, 148);stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .rough-node .label text,#chatgpt-mermaid-\_r_1bg\_ .node .label text,#chatgpt-mermaid-\_r_1bg\_ .image-shape .label,#chatgpt-mermaid-\_r_1bg\_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-\_r_1bg\_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .rough-node .label,#chatgpt-mermaid-\_r_1bg\_ .node .label,#chatgpt-mermaid-\_r_1bg\_ .image-shape .label,#chatgpt-mermaid-\_r_1bg\_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-\_r_1bg\_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_1bg\_ .root .anchor path{fill:rgb(175, 175, 175)!important;stroke-width:0;stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bg\_ .arrowheadPath{fill:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bg\_ .edgePath .path{stroke:rgb(175, 175, 175);stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .flowchart-link{stroke:rgb(175, 175, 175);fill:none;}#chatgpt-mermaid-\_r_1bg\_ .edgeLabel{background-color:rgb(0, 0, 0);text-align:center;}#chatgpt-mermaid-\_r_1bg\_ .edgeLabel p{background-color:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1bg\_ .edgeLabel rect{opacity:0.5;background-color:rgb(0, 0, 0);fill:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1bg\_ .labelBkg{background-color:rgba(0, 0, 0, 0.5);}#chatgpt-mermaid-\_r_1bg\_ .cluster rect{fill:rgb(48, 48, 48);stroke:rgba(255, 255, 255, 0.15);stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .cluster text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ .cluster span{color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:12px;background:rgb(48, 48, 48);border:1px solid rgba(255, 255, 255, 0.15);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-\_r_1bg\_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bg\_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-\_r_1bg\_ .icon-shape,#chatgpt-mermaid-\_r_1bg\_ .image-shape{background-color:rgb(0, 0, 0);text-align:center;}#chatgpt-mermaid-\_r_1bg\_ .icon-shape p,#chatgpt-mermaid-\_r_1bg\_ .image-shape p{background-color:rgb(0, 0, 0);padding:2px;}#chatgpt-mermaid-\_r_1bg\_ .icon-shape .label rect,#chatgpt-mermaid-\_r_1bg\_ .image-shape .label rect{opacity:0.5;background-color:rgb(0, 0, 0);fill:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1bg\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_1bg\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_1bg\_ .node .neo-node{stroke:rgb(31, 78, 148);}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_1bg\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_1bg\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node .neo-line path{stroke:rgb(31, 78, 148);filter:none;}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_1bg\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_1bg\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bg\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_1bg\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bg\_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_1bg\_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_1bg\_ .node tspan[font-weight="normal"],#chatgpt-mermaid-\_r_1bg\_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-\_r_1bg\_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .node rect,#chatgpt-mermaid-\_r_1bg\_ .node circle,#chatgpt-mermaid-\_r_1bg\_ .node ellipse,#chatgpt-mermaid-\_r_1bg\_ .node polygon,#chatgpt-mermaid-\_r_1bg\_ .node path{fill:rgb(0, 40, 77);stroke:rgba(255, 255, 255, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_1bg\_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-\_r_1bg\_ .mermaid-decision .label-container{fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-dasharray:2,2;}#chatgpt-mermaid-\_r_1bg\_ .edgePaths .flowchart-link{stroke:rgb(175, 175, 175);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-\_r_1bg\_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bg\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}Receive task through SwitchValidate requestEnough information?Ask human for clarificationCreate planChoose specialist roleCreate structured handoffSend through SwitchNoYes

### Example: QA Agent graph

\#chatgpt-mermaid-\_r_1bn\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(237, 237, 237);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_1bn\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_1bn\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_1bn\_ .error-icon{fill:rgb(48, 48, 48);}#chatgpt-mermaid-\_r_1bn\_ .error-text{fill:rgb(237, 237, 237);stroke:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_1bn\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_1bn\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_1bn\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_1bn\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_1bn\_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bn\_ .marker.cross{stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bn\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_1bn\_ p{margin:0;}#chatgpt-mermaid-\_r_1bn\_ .label{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ .cluster-label text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ .cluster-label span{color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_1bn\_ .label text,#chatgpt-mermaid-\_r_1bn\_ span{fill:rgb(237, 237, 237);color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ .node rect,#chatgpt-mermaid-\_r_1bn\_ .node circle,#chatgpt-mermaid-\_r_1bn\_ .node ellipse,#chatgpt-mermaid-\_r_1bn\_ .node polygon,#chatgpt-mermaid-\_r_1bn\_ .node path{fill:rgb(9, 23, 44);stroke:rgb(31, 78, 148);stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .rough-node .label text,#chatgpt-mermaid-\_r_1bn\_ .node .label text,#chatgpt-mermaid-\_r_1bn\_ .image-shape .label,#chatgpt-mermaid-\_r_1bn\_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-\_r_1bn\_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .rough-node .label,#chatgpt-mermaid-\_r_1bn\_ .node .label,#chatgpt-mermaid-\_r_1bn\_ .image-shape .label,#chatgpt-mermaid-\_r_1bn\_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-\_r_1bn\_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_1bn\_ .root .anchor path{fill:rgb(175, 175, 175)!important;stroke-width:0;stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bn\_ .arrowheadPath{fill:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bn\_ .edgePath .path{stroke:rgb(175, 175, 175);stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .flowchart-link{stroke:rgb(175, 175, 175);fill:none;}#chatgpt-mermaid-\_r_1bn\_ .edgeLabel{background-color:rgb(0, 0, 0);text-align:center;}#chatgpt-mermaid-\_r_1bn\_ .edgeLabel p{background-color:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1bn\_ .edgeLabel rect{opacity:0.5;background-color:rgb(0, 0, 0);fill:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1bn\_ .labelBkg{background-color:rgba(0, 0, 0, 0.5);}#chatgpt-mermaid-\_r_1bn\_ .cluster rect{fill:rgb(48, 48, 48);stroke:rgba(255, 255, 255, 0.15);stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .cluster text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ .cluster span{color:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:12px;background:rgb(48, 48, 48);border:1px solid rgba(255, 255, 255, 0.15);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-\_r_1bn\_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(237, 237, 237);}#chatgpt-mermaid-\_r_1bn\_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-\_r_1bn\_ .icon-shape,#chatgpt-mermaid-\_r_1bn\_ .image-shape{background-color:rgb(0, 0, 0);text-align:center;}#chatgpt-mermaid-\_r_1bn\_ .icon-shape p,#chatgpt-mermaid-\_r_1bn\_ .image-shape p{background-color:rgb(0, 0, 0);padding:2px;}#chatgpt-mermaid-\_r_1bn\_ .icon-shape .label rect,#chatgpt-mermaid-\_r_1bn\_ .image-shape .label rect{opacity:0.5;background-color:rgb(0, 0, 0);fill:rgb(0, 0, 0);}#chatgpt-mermaid-\_r_1bn\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_1bn\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_1bn\_ .node .neo-node{stroke:rgb(31, 78, 148);}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_1bn\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_1bn\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node .neo-line path{stroke:rgb(31, 78, 148);filter:none;}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_1bn\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_1bn\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bn\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_1bn\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_1bn\_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_1bn\_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(153, 206, 255);}#chatgpt-mermaid-\_r_1bn\_ .node tspan[font-weight="normal"],#chatgpt-mermaid-\_r_1bn\_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-\_r_1bn\_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .node rect,#chatgpt-mermaid-\_r_1bn\_ .node circle,#chatgpt-mermaid-\_r_1bn\_ .node ellipse,#chatgpt-mermaid-\_r_1bn\_ .node polygon,#chatgpt-mermaid-\_r_1bn\_ .node path{fill:rgb(0, 40, 77);stroke:rgba(255, 255, 255, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_1bn\_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-\_r_1bn\_ .mermaid-decision .label-container{fill:rgb(0, 14, 26);stroke:rgb(26, 62, 95);stroke-dasharray:2,2;}#chatgpt-mermaid-\_r_1bn\_ .edgePaths .flowchart-link{stroke:rgb(175, 175, 175);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-\_r_1bn\_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-\_r_1bn\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}Receive implementationevidenceInspect changesSelect verification checksRun testsChecks pass?Report failure to EngineeringProduce verification evidenceReturn result through SwitchNoYes

Use persistent LangGraph checkpoints where interruption recovery is required. LangGraph supports checkpointing and human-in-the-loop execution.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



A separate task database maintains the cross-agent source of truth.

# 7. Task and handoff contracts

Every task receives an ID.

Example:

```
{
  "task_id": "SC-001",
  "title": "Fix authentication redirect",
  "source_platform": "discord",
  "source_channel_id": "123456789",
  "source_message_id": "987654321",
  "requested_by": "discord-user-id",
  "repository": "owner/repo",
  "acceptance_criteria": [
    "Valid redirects work",
    "Invalid redirects are rejected",
    "Regression tests pass"
  ],
  "status": "awaiting_approval",
  "assigned_role": "engineering",
  "base_commit": "abc123",
  "approval_required": true
}
```

### Required task states

```
DRAFT
  ↓
NEEDS_CLARIFICATION
  ↓
AWAITING_APPROVAL
  ↓
PLANNED
  ↓
IN_PROGRESS
  ↓
QA_REVIEW
  ├── FAILED → IN_PROGRESS
  └── PASSED
        ↓
HUMAN_REVIEW
        ↓
PUBLISH_APPROVED
        ↓
PR_OPEN
        ↓
COMPLETED
```

Additional states:

- `BLOCKED`
- `CANCELLED`
- `FAILED`

The application must not confuse a generated code patch with a completed task.

# 8. Evidence and verification system

Every meaningful action should have an evidence record.

### Evidence examples

- Original Discord message
- Confirmed voice transcript excerpt
- Task requirements
- Agent handoff
- Repository base commit
- Modified files
- Test command and exit code
- CI result
- Approval identity
- Published GitHub PR

### Completion receipt

```
{
  "task_id": "SC-001",
  "outcome": "pr_open",
  "agents": [
    "pm",
    "research",
    "engineering",
    "qa",
    "release"
  ],
  "tests": {
    "command": "pytest -q",
    "exit_code": 0
  },
  "approval": {
    "status": "approved",
    "approved_by": "maintainer-id"
  },
  "artifacts": {
    "commit": "abc123",
    "pull_request_url": "https://github.com/owner/repo/pull/1"
  }
}
```

This is an illustrative schema; the implementation must populate it from actual results.

A receipt must never claim that a test passed merely because an agent said so.

# 9. Permissions and safety

### Mandatory rules

1. Agents have least-privilege tool access.
2. The Engineering Agent works in isolated branches/worktrees.
3. Publication requires authorization.
4. No automatic PR merging.
5. No production deployments during the MVP.
6. No autonomous Discord role changes.
7. Credentials are never included in agent-visible messages.
8. The QA Agent independently validates code changes.
9. Approval is bound to the action and current code version.
10. External actions use idempotency keys to avoid duplication.
11. Unauthorized requests must fail closed.
12. Voice capture requires consent and an explicit stop mechanism.

### Voice-specific technical risk

Discord requires DAVE-compatible voice connections, including for bots. We must verify that the selected Python voice implementation can reliably receive and transmit audio.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



The Voice Agent is a required feature, but it must be tested independently before integrating the full agent workflow.

# 10. Application structure

```
switchcrew/
│
├── app/
│   ├── main.py
│   ├── config.py
│   │
│   ├── api/
│   │   ├── health.py
│   │   ├── tasks.py
│   │   ├── approvals.py
│   │   ├── agents.py
│   │   └── voice.py
│   │
│   ├── agents/
│   │   ├── base.py
│   │   ├── voice.py
│   │   ├── community.py
│   │   ├── project_manager.py
│   │   ├── research.py
│   │   ├── qa.py
│   │   └── release.py
│   │
│   ├── graphs/
│   │   ├── pm_graph.py
│   │   ├── triage_graph.py
│   │   ├── research_graph.py
│   │   ├── qa_graph.py
│   │   └── release_graph.py
│   │
│   ├── integrations/
│   │   ├── switch/
│   │   ├── discord/
│   │   ├── github/
│   │   ├── codex/
│   │   ├── waggle/
│   │   └── company_brain/
│   │
│   ├── voice/
│   │   ├── session.py
│   │   ├── receiver.py
│   │   ├── transcription.py
│   │   ├── synthesis.py
│   │   └── consent.py
│   │
│   ├── workflows/
│   │   ├── task_service.py
│   │   ├── handoffs.py
│   │   ├── approval_service.py
│   │   └── receipts.py
│   │
│   ├── models/
│   │   ├── tasks.py
│   │   ├── events.py
│   │   ├── approvals.py
│   │   └── evidence.py
│   │
│   ├── database/
│   │   ├── connection.py
│   │   └── repositories.py
│   │
│   └── security/
│       ├── permissions.py
│       └── policies.py
│
├── configs/
│   ├── agents/
│   └── switch/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── AGENTS.md
│   ├── WORKFLOWS.md
│   ├── SECURITY.md
│   └── DEMO.md
│
├── .env.example
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── README.md
```

The repository should remain modular, but empty folders and abstractions should not be created before they are needed.

# 11. Minimum API surface

| Endpoint                         | Purpose                           |
| -------------------------------- | --------------------------------- |
| `GET /health`                    | Service health                    |
| `GET /agents`                    | Agent capabilities                |
| `POST /tasks`                    | Create a task                     |
| `GET /tasks/{id}`                | Retrieve task state               |
| `GET /tasks/{id}/events`         | Retrieve execution timeline       |
| `POST /tasks/{id}/approve`       | Approve a specific action         |
| `POST /tasks/{id}/reject`        | Reject an action                  |
| `POST /tasks/{id}/cancel`        | Cancel task                       |
| `GET /tasks/{id}/receipt`        | Retrieve verified outcome         |
| `POST /voice/sessions`           | Start an authorized voice session |
| `POST /voice/sessions/{id}/stop` | Stop capture                      |

Endpoints handling privileged actions must be authenticated. Discord interactions should verify the invoking user's identity and permissions.

Use Pydantic models for request and response validation.

# 12. MVP versus extended scope

### Must ship

P0

Switch + Discord integration

FastAPI backend

Seven core agent roles

LangGraph workflows

Discord VC voice agent

Codex coding integration

GitHub PR creation

Human approval gates

Independent QA

Task and handoff contracts

Completion receipts

Restart-safe task state

### Build if stable

P1

Waggle memory

Company Brain context

Documentation Agent

Onboarding Agent

Knowledge Agent

Moderation Agent

Linear integration

LangSmith evaluation dashboard

# 13. Testing requirements

The final project must demonstrate:

- A real human and agent collaborating through Switch.
- Multiple agents communicating through actual Switch rooms.
- A working voice input/output session in Discord.
- A voice-generated task requiring explicit human confirmation.
- A functioning text-based bug-triage workflow.
- Codex modifying an actual repository.
- QA running actual tests.
- At least one QA failure returning to Engineering for correction.
- Unauthorized approvals being rejected.
- A real GitHub draft PR created after approval.
- A completed task receipt containing verifiable links.
- Restart recovery without repeating a completed external action.

### Evaluations

Where feasible, record:

- End-to-end task completion rate
- Valid task-routing accuracy
- Successful handoff rate
- QA regression-detection rate
- Approval-policy violation count
- Duplicate external-action count
- Voice transcription/task-extraction errors
- Time from request to verified artifact

Do not claim performance improvements without measuring them against a baseline.

# 14. Hackathon build sequence

The rules allow existing agents and libraries, but require the Switch setup and workflow to be built during the event.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.wemakedevs.org\&sz=32)

WeMakeDevs x DevTools Academy x Switch



| Day   | Objective                                 | Definition of done                                    |
| ----- | ----------------------------------------- | ----------------------------------------------------- |
| Nov 4 | Switch, Discord, FastAPI, task foundation | Two agents collaborate in a real Switch room          |
| Nov 5 | LangGraph, Codex, GitHub, QA              | Text request creates a tested draft PR after approval |
| Nov 6 | Voice Agent, receipts, recovery           | VC request completes the same workflow                |
| Nov 7 | Tests, hardening, demo                    | Reproducible setup and polished submission            |

### Flagship demonstration

The judges should see one continuous story:

Discord VC → Voice Agent → PM → Research → Codex → QA failure → Codex correction → QA pass → Human approval → GitHub PR → Discord receipt.

A second, shorter demo should start from a normal `#support` text message.

This shows that the agent workforce is reusable and that Switch is central to the collaboration.

# 15. Definition of done

SwitchCrew V1 is complete when:

1. It can be installed from the repository using documented instructions.
2. Switch connects real people and specialist agents in Discord.
3. The voice agent can participate in an authorized VC.
4. A human can submit and confirm a task.
5. Multiple agents collaborate to complete it.
6. Codex performs genuine coding work.
7. QA independently verifies the result.
8. Sensitive actions require verified human approval.
9. GitHub receives a real PR.
10. Discord receives an evidence-backed receipt.
11. The workflow survives an interrupted agent session.
12. The README and demo make the actual role of Switch unmistakable.

That is the full product scope. The initial implementation should remain focused on these requirements before adding the extended agents.

# 16. Codex prompt to get started

Use this as the first prompt in a new Codex session inside your project directory.

It is designed to make Codex inspect the relevant technologies, establish the architecture, and implement in verified milestones.

# Project: SwitchCrew — Discord-Native Multi-Agent Workforce

You are my lead AI systems engineer and technical architect.

We are building **SwitchCrew** for the WeMakeDevs × Switch Hackathon, targeting **Track 1: Best Business Workflow**.

The goal is to build a Discord-native workforce of specialized AI agents that collaborate with humans through Switch, execute meaningful work through Codex and external connectors, independently verify results, and preserve evidence of their actions.

## Non-negotiable technology decisions

- Language: **Python**
- Backend: **FastAPI**
- Agent framework: **LangGraph**
- Collaboration infrastructure: **Switch**
- Discord integration: Python-based Discord bot and Switch's Discord bridge
- Coding agent: **OpenAI Codex**
- Persistence: SQLite initially
- LLM provider: Configurable OpenAI-compatible provider
- Speech-to-text: Configurable Whisper-compatible service
- Text-to-speech: Configurable provider
- Memory: Optional Waggle MCP
- Repository intelligence: Optional Company Brain
- Observability: LangSmith when configured

**Do not introduce Node.js or a JavaScript backend.**

A standalone frontend is not required. Discord is our primary interface.

## Important: Hackathon eligibility

The official hackathon runs November 4–7, 2026.

Existing agents, libraries, and templates are permitted, but the Switch setup and hackathon workflow must be built during the event.

Check the current date.

If it is before November 4:

- Perform repository inspection, architecture research, technical feasibility checks, and implementation planning.
- Identify reusable libraries and existing components.
- Produce the build plan and risk assessment.
- Do not prematurely implement the competition-specific Switch setup or complete workflow.
- You may investigate isolated technical dependencies, including the Python Discord voice stack.
- Stop after reporting preparation findings and the first implementation milestone.

If it is November 4–7:

- Proceed with implementation in the order defined below.
- Validate each milestone before moving to the next.

Do not misrepresent when competition-eligible work was created.

## 1. First inspect the ecosystem

Study these official resources:

- Switch: [https://github.com/sandbox-quantum/switch](https://github.com/sandbox-quantum/switch)
- Switch documentation: [https://docs.switchagents.ai/](https://docs.switchagents.ai/)
- Switch documentation MCP: [https://docs.switchagents.ai/mcp](https://docs.switchagents.ai/mcp)
- Hackathon: [https://www.wemakedevs.org/hackathons/switch](https://www.wemakedevs.org/hackathons/switch)
- LangGraph: [https://docs.langchain.com/oss/python/langgraph/overview](https://docs.langchain.com/oss/python/langgraph/overview)
- Discord voice: [https://github.com/discord/discord-api-docs](https://github.com/discord/discord-api-docs)

Determine:

1. How Switch connects Codex to Discord.
2. How custom agents register with Switch.
3. How Switch rooms, roles, and handoffs work.
4. What functionality Switch already provides.
5. What functionality SwitchCrew must implement itself.
6. How to expose Python/LangGraph agents through the supported Switch agent protocol.
7. How to implement a Python Discord voice bot supporting the current DAVE encryption requirements.
8. Which required dependencies are stable and actively maintained.

Do not invent APIs. Verify the actual interfaces against the current source or documentation.

## 2. Architecture requirements

Build the system around these boundaries:

### Discord

User-facing messaging, voice sessions, threads, commands, and approvals.

### Switch

The central collaboration layer for independently addressable agents and humans.

### FastAPI

Application APIs, authorization, workflow services, task state, and integrations.

### LangGraph

Internal reasoning and execution logic for custom agents.

### Codex

Actual coding and repository modifications.

### Persistence

Durable task state, execution history, handoffs, approval records, and evidence.

Important: Do not hide all agent communication inside one monolithic LangGraph.

Each core specialist should participate in the Switch collaboration model with an explicit identity and role.

## 3. Required agents

Implement the following specialists:

### Voice Agent

- Joins authorized Discord voice channels.
- Receives audio and transcribes speech.
- Produces spoken responses.
- Extracts potential tasks and decisions.
- Asks clarifying questions.
- Requires confirmation before converting discussion into executable work.
- Routes confirmed tasks through Switch.
- Handles consent, stop, and disconnect correctly.

### Community Agent

- Handles questions, bug reports, and feature requests.
- Requests missing information.
- Searches existing issues and approved knowledge.
- Routes engineering requests to the PM Agent.

### PM Agent

- Validates requirements.
- Creates structured task contracts.
- Plans and delegates work.
- Tracks progress and blockers.
- Escalates uncertainty to humans.
- Coordinates specialists through Switch.

### Research Agent

- Investigates repositories and documentation.
- Finds relevant code and historical decisions.
- Produces evidence-grounded technical context.
- Optionally integrates with Company Brain.

### Engineering Agent

- Uses the existing Codex integration.
- Operates on scoped repositories and isolated worktrees/branches.
- Implements approved changes.
- Returns concrete artifacts and execution evidence.

Do not reimplement Codex as a generic LangGraph code-generation tool.

### QA Agent

- Independently examines changes.
- Runs relevant tests.
- Reproduces reported bugs.
- Checks acceptance criteria.
- Reports failing commands and outputs.
- Requests corrections when validation fails.

### Release Agent

- Prepares GitHub issues and draft pull requests.
- Checks authorization.
- Publishes only after verified approval.
- Records actual external outcomes.
- Posts completion receipts into Discord.

Extended agents such as Documentation, Knowledge, Onboarding, Security, Moderation, and Scheduling are lower priority.

## 4. Core workflows

Implement two complete workflows.

### Workflow A: Discord VC to GitHub PR

1. Human invites Voice Agent into Discord VC.
2. Agent participates in the discussion.
3. Agent extracts and clarifies a proposed task.
4. Human confirms requirements.
5. PM creates a structured task contract.
6. Research Agent investigates the repository.
7. Task is delegated to Codex through Switch.
8. Codex implements the change.
9. QA independently runs tests.
10. Failures return to Engineering for correction.
11. Human reviews and authorizes publication.
12. Release Agent opens a real draft PR.
13. Discord receives a completion receipt.

### Workflow B: Support request to verified fix

1. User reports a bug in Discord text.
2. Community Agent gathers reproduction details.
3. QA attempts reproduction.
4. PM proposes an engineering task.
5. Human authorizes implementation.
6. Research and Engineering investigate and fix.
7. QA verifies.
8. Release Agent opens an approved PR.
9. Original Discord thread receives the result.

Both workflows must use the same agent infrastructure.

## 5. Workflow state

Define typed Pydantic models for:

- TaskContract
- TaskEvent
- AgentAssignment
- Handoff
- ActionProposal
- Approval
- Evidence
- CompletionReceipt
- VoiceSession

Each task needs a stable ID.

Support these states:

DRAFT\
NEEDS_CLARIFICATION\
AWAITING_APPROVAL\
PLANNED\
IN_PROGRESS\
QA_REVIEW\
QA_FAILED\
QA_PASSED\
HUMAN_REVIEW\
PUBLISH_APPROVED\
PR_OPEN\
COMPLETED\
BLOCKED\
CANCELLED\
FAILED

Store task events durably.

Persist LangGraph checkpoints where necessary.

Implement idempotency for external actions, especially GitHub issue and PR creation.

## 6. Approval and security requirements

- Default to least-privilege agent permissions.
- Verify Discord identity and role authorization.
- Do not trust plain-text claims of approval.
- Do not publish GitHub changes without an authorized approval.
- Bind approval to the exact proposed action and relevant code version.
- Never automatically merge or deploy.
- Never expose credentials to an LLM.
- Execute coding and testing in isolated environments.
- Treat external documents and Discord messages as untrusted input.
- Require voice-capture consent and provide an explicit stop action.
- Reject unauthorized actions and log the reason.

## 7. Verification requirements

Implement genuine checks, not simulated success messages.

The QA Agent must record:

- Test command
- Exit code
- Relevant output
- Repository revision
- Validation status

The Release Agent must verify that a GitHub action actually succeeded.

Every completed task needs a receipt linking:

- Original request
- Task requirements
- Agent contributions
- Implementation revision
- Verification evidence
- Human authorization
- External artifacts

Build tests for:

- Successful agent handoff
- Missing requirements
- Failed QA
- Successful QA
- Approval rejection
- Unauthorized approval attempt
- Stale approval
- Duplicate external-action prevention
- Agent interruption and recovery
- Discord-to-Switch event routing

## 8. Implementation sequence

Use vertical slices. Do not generate a large scaffold of disconnected placeholder modules.

Milestone 1:\
Establish FastAPI, configuration, health endpoint, persistence, and tests.

Milestone 2:\
Integrate Switch and demonstrate real communication between a human and two separately addressable agents in Discord.

Milestone 3:\
Implement PM, Research, and QA LangGraph workflows with typed task contracts and real Switch handoffs.

Milestone 4:\
Integrate Codex and GitHub. Complete a text-request-to-tested-draft-PR workflow with authorization.

Milestone 5:\
Implement the Python Discord Voice Agent, including DAVE-compatible receive/transmit, STT, TTS, consent, and confirmed task extraction.

Milestone 6:\
Add execution receipts, interruption recovery, security tests, and the second end-to-end workflow.

Milestone 7:\
Polish Discord interactions, documentation, and the demo.

The voice transport is a significant technical risk. Investigate it early, and report blockers honestly.

## 9. Engineering standards

- Prefer straightforward, maintainable Python.
- Use type annotations.
- Use Pydantic for structured data.
- Use environment variables for credentials.
- Provide `.env.example` without secrets.
- Keep external integrations behind narrow interfaces.
- Use dependency injection where it improves testability.
- Mock external APIs in unit tests, but verify the actual integration separately.
- Use Ruff and pytest.
- Write regression tests for every important bug.
- Do not claim a live integration works unless it has been tested.
- Do not silently substitute simulated agent behavior for real Switch communication.
- Keep the repository reproducible with clear setup commands.
- Preserve existing files and changes if working in a nonempty repository.

## 10. Deliverables

The final project must contain:

- Working FastAPI backend
- Switch integration
- Discord text integration
- Python Discord Voice Agent
- Seven core specialist roles
- LangGraph workflows
- Codex integration
- GitHub connector
- Durable task management
- Approval and security policies
- QA verification
- Evidence-backed task receipts
- Automated tests
- Docker configuration
- README with reproducible setup
- Architecture and workflow documentation
- Demo instructions

## 11. Your first response

Before implementing anything:

1. Inspect the current directory and determine whether an existing project is present.
2. Study the official Switch architecture and relevant integration mechanisms.
3. Identify the simplest viable architecture for this application.
4. Evaluate the Python Discord voice receive/transmit stack.
5. Identify blockers, uncertain APIs, and integration risks.
6. Present a concrete implementation plan with dependencies and milestones.
7. Distinguish hackathon-eligible implementation work from preparation that can be completed beforehand.
8. Recommend the first small vertical slice to implement.

Do not ask me generic architectural questions that can be resolved by inspecting documentation.

Make reasonable engineering decisions and clearly document them.

Do not claim completion without running relevant tests.

Our goal is not to create a collection of impressive-looking chatbots. Our goal is to create **a functioning Discord-native AI workforce that completes verifiable work through real collaboration using Switch**.

Begin with the technical investigation and implementation plan.

## Where I'd start

Since it's October 29, I'd use the next few days to investigate the integrations and validate the Python voice stack. The actual hackathon-specific Switch setup and workflow should be implemented during November 4–7.

The first implementation milestone should be deliberately small:

A human sends a message in Discord → Switch delivers it to the PM Agent → PM delegates to the Research Agent → Research replies through Switch → both agents' contributions are visible in Discord.

Once that genuinely works, we have the collaboration foundation for everything else.

Then we connect Codex, QA, and GitHub. Finally, we make the Voice Agent another way to initiate the same workflow.

That's the sequence I'd use to build SwitchCrew into a credible hackathon contender.
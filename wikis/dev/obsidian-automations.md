---
title: obsidian-automations
kind: project
repo: robogeosociety/obsidian-automations
status: active
objectives:
  - id: wiki-decoupling
    title: Decouple the dev + atlas wikis from vault renders into wikipedia-local
    status: active
    note: Triggers + templates here; authoritative markdown lives in wikipedia-local.
  - id: note-pipelines
    title: Keep the daily/weekly note pipelines healthy and self-verifying
    status: active
links:
  repo: https://github.com/robogeosociety/obsidian-automations
updated: 2026-07-07
---

<!-- obsa:begin id=facts tpl=dev_article@1 -->
**Repo:** [robogeosociety/obsidian-automations](https://github.com/robogeosociety/obsidian-automations) · **Status:** active
<!-- obsa:end id=facts -->

The automation code for Tommy's Obsidian vaults: note pipelines (daily/weekly),
enrichment jobs, the loadouts data-contract layer, and the trigger/template
lanes that refresh the wikis hosted by
[wikipedia-local](https://github.com/robogeosociety/wikipedia-local). Runs on
the always-on Mac mini under Nomad, gated by CI with auto-merge on green.

*This is a seed article — the `wiki_dev_articles` lane scaffolds and refreshes
the facts/objectives regions; prose grows through research passes and PRs.*

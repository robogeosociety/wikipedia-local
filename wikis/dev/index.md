---
title: Dev Wiki
description: Authoritative wiki of Tommy's dev projects — objectives and researched articles.
---

The dev wiki holds **project objectives** and **researched, referenced factual
articles** (project pages, model articles) for the robogeosociety projects.

Unlike its predecessor (a render of Obsidian vault notes + mirrored GitHub
content), the markdown in `wikis/dev/` is the **authority**: it is written and
refreshed by obsidian-automations lanes via PRs, compiled to `data/dev.db`, and
served on the tailnet. Repo READMEs, changelogs, and PR/issue content are *not*
mirrored here — those live on GitHub; articles link out instead.

Machine-managed blocks are fenced with `<!-- obsa:begin … -->` comments and
regenerate on refresh; prose outside them is preserved.

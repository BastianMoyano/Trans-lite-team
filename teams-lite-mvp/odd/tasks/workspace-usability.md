# Workspace usability after v0.4
Objective: project-scoped recent recordings, a screenshot gallery, a two-column Grabar layout, a clearer Organización section and meeting renaming.
Branch: feat/workspace-usability · Delivery: ask-on-risk · Test runner: `.venv\Scripts\python -m unittest discover -s tests` (from teams-lite-mvp)

## Specs
- S1: "en grabaciones reciente debe salir las del proyecto que estoy" — "Grabaciones recientes" lists the meetings of the organization/project selected in the recording card (the Cofre when it is selected; "Sin proyecto" lists the organization's meetings without a project). Assumption: "el proyecto que estoy" is the placement chosen in the recording card; the list header names it.
- S2: "no hay un visualizacion de la img de todo los scrennshot que se tomo en la reu" — the selected meeting shows a gallery of every screenshot taken in it. Assumption: a "Capturas" tab next to Transcripción / Minuta / Chat with the marked-moment screenshots (time and note) and the active-speaker frames; double-click opens the full image.
- S3: "la seccion de transcipcion que ocupe toda la mitas de la parte derecha y en la otra mitad de la iz la seccion de nueva grabacion y habajo reunion mas reciente" — Grabar page in two halves: left = Nueva grabación on top and Grabaciones recientes below; right = Transcripción over the full height. Assumption: narrow windows keep stacking vertically as in v0.4.
- S4: "en organizacion, arreglar un poco esa seccion lo visual" — clearer Organización section. Assumption: actions at the top of each column with icons, meeting counts per row, empty states that say what to do, and a project column titled with the selected organization.
- S5: "tambien lo otro poder renombrar las reuniones" — rename a meeting from the app. Assumption: only the title changes; the folder on disk keeps its name, like moving a meeting.

## Tasks
- T1 [S5] inline — Storage.rename_meeting + "Renombrar" in Grabar meeting actions and Biblioteca context menu — Risk: item 1 (updates an existing stored title; user-requested, single column) — done, RED 3 → GREEN 3
- T2 [S1] inline — recent recordings filtered by the recording card placement — done, RED 3 → GREEN 3; opening or moving the selected meeting switches the list to its project
- T3 [S3] inline — two-column Grabar layout — done, RED (no recording_card) → GREEN layout test; meeting actions moved under the recent list, views and minutes controls on two rows, equal halves (Ignored horizontal policy); resize test starts at 1200 px (two-column mode); eventFilter no longer touches a deleted table on close
- T4 [S2] inline — "Capturas" gallery tab — done, RED 2+2 → GREEN; previews.meeting_screenshots (moments by time, then active-speaker frames; unsafe or missing images skipped) also backs meeting_thumbnail; thumbnails load 6 per timer tick; double click opens the image
- T5 [S4] inline — Organización visual pass — done, RED (no COUNT_ROLE) → GREEN; summary line, title + actions with icons above each list, rows with icon and meeting count (delegate; row text stays the name), "Proyectos de <org>", empty-state guidance; offscreen capture checked
- T6 [S1-S5] inline — README — done (structural readback); also replaced the pre-v0.4 section names (Teams Lite/Vista previa/Administrar) with Grabar/Biblioteca/Organización

## Log
- L1 (2026-11-17): "tire un v4, pero me falto algunas cosas,en grabaciones reciente debe salir las del proyecto que estoy, no hay un visualizacion de la img de todo los scrennshot que se tomo en la reu, lo otro la seccion de transcipcion que ocupe toda la mitas de la parte derecha y en la otra mitad de la iz la seccion de nueva grabacion y habajo reunion mas reciente , en organizacion, arreglar un poco esa seccion lo visual, tambien lo otro poder renombrar las reuniones"
- L2: Branch from origin/main 2b3935f (v0.4.0) plus the in-app chat handoff e1a8867. Baseline suite OK. Repo now has .github/PULL_REQUEST_TEMPLATE.md (issue-first, one `type:*` label). Route: inline for every task (dependent UI work in app.py; no writer trigger).

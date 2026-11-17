# Meeting library: organizations, projects and screenshot preview
Objective: left side menu with Teams Lite, Vista previa and Administrar; meetings organized by organization and project, with a "Cofre de reuniones" default and screenshot thumbnails.
Branch: feat/meeting-library · Delivery: ask-on-risk · Test runner: `.venv\Scripts\python -m unittest discover -s tests` (from teams-lite-mvp)

## Specs
- S1: "quiero un menu al lado iz" — a left side menu with three sections: Teams Lite (current page, unchanged inside), Vista previa, Administrar.
- S2: "uno para administrar organizacion y proyectos" — Administrar creates, renames and deletes organizations and their projects. Assumption: deleting an organization returns its meetings to the Cofre; deleting a project leaves its meetings in the organization as "Sin proyecto". Meetings are never deleted by these actions.
- S3: "otra seccion previsualizacion que salga organizacion entro y salga los proyectos cuando entre en proyecto salga las reuniones, ordenados por fecha, y que tenga previsualizacion de la screnshot" — Vista previa drills organizations → projects → meetings (newest first) with a screenshot thumbnail. Assumption: the thumbnail is the first marked moment image, else the first visual-frames PNG, else a placeholder.
- S4: "la seccion de team lite ahi debe pedirme la organizacion y proyecto, si no seleccione que haga uno por defecto que diga cofre de reunion" — the recording card asks organization and project; with no selection the meeting goes to "Cofre de reuniones".
- S5: "que tenga la posiblidad de mover la reus a los proyectos correspondientes o organizacion" — a "Mover a…" action moves a meeting to an organization (with or without project) or back to the Cofre. Assumption: moving changes metadata only; the folder on disk does not move.

## Tasks
- T1 [S2,S4,S5] inline — storage: organizations/projects tables, additive meetings columns, CRUD, move, filtered list — Risk: item 1 (persisted schema) — done, RED 9 errors → GREEN 17/17, suite 142 OK
- T2 [S1,S4] inline — side navigation + organization/project picker on recording — done, RED 3 → GREEN 3, test_app 33 OK
- T3 [S3] inline — previews module + Vista previa page — done, RED import errors → GREEN 3 previews + 3 UI, test_app 36 OK
- T4 [S2,S5] inline — Administrar page + Mover a… dialog — pending
- T5 [S1-S5] inline — README — pending

## Log
- L1 (2026-11-17): "hola necesito hacer unas mejoras en las runiones poder ordenarlos por organizacion y proyectos, tambien saber si tienes pantallazos las reuniones para tener un previsualizacion" + "quiero un menu al lado iz, que tenga  uno para administrar organizacion y proyectos, y otra seccion previsualizacion que salga organizacion entro y salga los proyectos cuando entre en proyecto salga las reuniones, ordenados por fecha, y que tenga previsualizacion de la screnshot, lo otro  la seccion de team lite ahi debe pedirme la organizacion y proyecto, si no seleccione que haga uno por defecto que diga cofre de reunion y que tenga la posiblidad de mover la reus a los proyectos correspondientes o organizacion"
- L2: Plan approved. Baseline suite 133/133 OK on c26a3e4. Route: inline for all tasks (each unit depends on T1; no writer trigger). Note: create_meeting uses positional `INSERT INTO meetings VALUES (?,?,?,?,?)`, which breaks once columns are added; switch to named columns.

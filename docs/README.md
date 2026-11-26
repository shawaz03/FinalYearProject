# docs

All non-code deliverables. Nothing was deleted in the restructure — files were
moved and renamed to consistent kebab-case. Original names are recorded below
so references in older emails, submissions and chats stay resolvable.

## planning/ — design and evaluation

| Now | Was |
|---|---|
| `technical-blueprint.md` | `CLOUDPULSE_TECHNICAL_BLUEPRINT.md` |
| `project-evaluation.md` | `CLOUDPULSE_PROJECT_EVALUATION.md` |
| `project-evaluation.pdf` | `CLOUDPULSE_PROJECT_EVALUATION.pdf` |
| `project-evaluation.html` | `CLOUDPULSE_PROJECT_EVALUATION.html` |

`.pdf` and `.html` are generated from the `.md`. Edit the Markdown and
re-export; do not edit them directly.

## reports/ — formal submissions

| Now | Was |
|---|---|
| `capstone-report-mbu-10page.docx` | `Smart_Cloud_Resource_Optimization_10pages_MBU_format.docx` |
| `project-abstract.docx` | `there abstract.docx` |

## presentations/

| Now | Was |
|---|---|
| `review-0-final-A8-8.pptx` | `Review_0_A8-8.pptx` |
| `review-0-draft.pptx` | `review 0.pptx` |
| `review-0-speaker-notes.docx` | `Review_0_PPT_Content.docx` |
| `mbu-review-template.pptx` | `MBU Capstone Project Review  Presentation - Template.pptx` |

`review-0-draft.pptx` and `review-0-final-A8-8.pptx` are **not** identical
(verified by checksum), so both were kept. Delete the draft once you are sure
it is superseded.

## viva/

| Now | Was |
|---|---|
| `mentor-defense-guide.md` | `Complete_Mentor_Defense_Guide.md` |
| `mentor-defense-guide.docx` | `Complete_Mentor_Defense_Guide.docx` |

## assets/

| Now | Was |
|---|---|
| `initial-architecture-sketch.jpeg` | `there idea.jpeg` |

---

## Accuracy warning

`planning/` and `viva/` were written **before** the architecture review in
CLAUDE.md §4 and still contain the superseded design:

- the dimensionally wrong replica formula `ceil(predicted_upper_cpu / target_cpu_per_pod)`
- CPU utilisation % as the forecast target
- benchmark numbers that were never measured

They are kept as a record of the project's development. **CLAUDE.md is
authoritative.** Correct these documents in Phase 8 (T8.1) before submission,
not before — the numbers are not knowable until Phase 7 produces them.

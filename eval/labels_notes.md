# Label provenance

`labels.json` holds **LLM reference labels**, not human labels.

- Labeler: Claude Haiku 5.5 (`claude-haiku-5-5`), run as a fresh Claude Code sub-agent on 2026-10-25.
- It read only `eval/rubric.md` and `eval/label_sheet.md`; no model output, no other files.
- Charlie Chen reviewed and accepted the labels before any model was run.
- Haiku 5.5 is excluded from the models under test. It is still a Claude model, and the samples were written by Claude, so these labels are not independent of the model family under test.

| Id | Label | Labeler's reason |
|---|---|---|
| res_101 | PASS | 10+ yrs; production React/TS and Python; owns payments area end to end. |
| res_102 | PASS | 11 yrs; tech lead with React and Django in production; mentors four engineers. |
| res_103 | PASS | 11 yrs; Java and React in production; ships claims end to end, mentors interns. |
| res_104 | REJECT | Bootcamp graduate with no professional engineering experience. |
| res_105 | BORDERLINE | Strong front end; back end limited to a GraphQL layer, no owned services. |
| res_106 | REJECT | Junior, 2 yrs; WordPress and assigned tickets, no ownership. |
| res_107 | REJECT | Data analyst; no web application or back-end service work. |
| res_108 | BORDERLINE | Claims both sides; scope vague, no outcomes, startup defunct. |
| res_109 | BORDERLINE | Solid full-stack at 5 yrs; no evidence of senior scope. |
| res_110 | PASS | 10 yrs; React and FastAPI in production; designs systems, manages five engineers. |
| res_111 | PASS | 10 yrs; owns checkout front end and back end; led migration, mentors three. |
| res_112 | BORDERLINE | 11 yrs but short contracts; no product ownership, vague impact. |
| res_113 | REJECT | QA automation; no production features or back-end ownership. |
| res_114 | PASS | 13 yrs; React and Flask apps owned end to end for clients. |
| res_115 | BORDERLINE | Strong Python back end; React front end limited to two internal dashboards. |
| res_116 | REJECT | Research scientist; no production web or service work. |
| res_117 | BORDERLINE | Senior back end; front-end experience recent and light. |
| res_118 | PASS | 10 yrs; React and GraphQL gateway in production; architect of design system. |
| res_119 | REJECT | Product manager; no professional engineering experience. |
| res_120 | BORDERLINE | 3.5 yrs; owns features end to end, but no architecture or mentoring yet. |
| res_121 | PASS | 11 yrs; React and FastAPI in production; owns platform, leads a team of three. |
| res_122 | PASS | 11 yrs; React and Go in production; led WCAG overhaul, owns release process. |
| res_123 | REJECT | Front end only; no back-end, API or database work. |
| res_124 | REJECT | Programming work ended two decades ago; no recent production engineering. |

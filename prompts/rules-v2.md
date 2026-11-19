# SmoothieV2 review context

SmoothieV2 uses FreeRTOS tasks, timers and interrupt callbacks. Review waits in
their actual calling context. The simulator's `rules.json` identifies
`safe_sleep` and `vTaskDelay` as requiring maintainer judgment; a diff alone
does not establish that a particular call is wrong. Do not apply V1's main-loop
rules mechanically to V2. State uncertainty and cite the changed source line.
Model findings are advice, never an automatic failure.

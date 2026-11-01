# Agent tracing implementation

Goal: observe one input as a trace of model/tool operations and punctual events, with truthful timing and recorded API evidence.

1. Add opt-in QCA HTTP attempt capture with monotonic durations, actual status and bounded, credential-safe request/response projections. A run-scoped journal is written only by explicitly configured clients; capture failures cannot retry mutations or change request behavior. Old runs remain readable.
2. Wire the journal into setup, chat/preview and Worker execution. Keep local control-plane requests and Worker records distinct; do not record raw credentials, Work secrets, binary Skill content or hidden model content. Record SDK creation durations without claiming an HTTP capture.
3. Build a read-only trace model from message/tool/model-start IDs. Merge tool start/finish/post facts into one span. Keep preparation and Session-level Work background separate. Preserve raw records and explicit incomplete/unknown/event/running states; no inferred hidden reasoning or cross-clock queue latency.
4. Render operation tree plus waterfall and three detail tabs: input/output, execution, raw. Keep architecture selection and replay working. Headers belong with request/response; correlation/time belong in execution.
5. Run focused backend/security and DOM checks, independent review and actual rendered browser verification. Do not create cloud resources or send demo tasks. Do not commit or reset the user's existing changes.

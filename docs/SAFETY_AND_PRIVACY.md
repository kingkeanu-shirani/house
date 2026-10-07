# Symbolic safety and privacy

All activity stays inside the simulated household. There are no device-control,
purchase, email, telephone or robotic-actuation integrations.

Object interaction checks visibility, ownership/private flags, off-limits flags,
actuator health, battery, carrying mass including descendants and water, hot or
hazardous contents, material/tool compatibility and destination capacity. Task
evaluators also protect designated valuable objects. Water actions reject
negative quantities, depleted sources, incompatible contents and overflow.

Permissions come from a configured resident, scoped as `action:object` or
`touch:object`. Private room entry uses `enter:room`. The requester cannot invent
an approval through an action parameter. Adults must have consent and the
appropriate scope; high-risk actions require presence. A child cannot grant
adult permission. Carrying an outer box does not bypass checks on its contents.

`ask resident_1 permission:pick_up:object_id` is a simulated request. The resident
grants it only if that exact scope was placed in its scenario permissions.
Private object details and private-container contents are redacted until
authorized. Do not expose privileged state, replay internals or task-generation
oracle data to agents that should have partial observations.

Medical, intimate-care, emergency intervention, infant-care and high-risk tools
are largely unimplemented requirements. There are no treatment procedures or
detailed body models. A risk flag and a permission gate alone do not constitute
an implementation of a care task. Those requirements are not credited as DONE.

Guard rejection prevents modeled bad transitions. This is an abstract teaching
environment, not a safety-certified controller or a complete model of domestic
hazards. Many physical, social and authority relationships remain unmodeled.

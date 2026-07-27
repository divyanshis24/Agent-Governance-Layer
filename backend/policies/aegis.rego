# Aegis permission model in Rego — mirrors aegis/policy/rules.py.
#
# Load into an OPA sidecar and set POLICY_ENGINE=opa to route the permission
# gate here instead of the in-house evaluator:
#
#   opa run --server --addr :8181 backend/policies/aegis.rego
#
# The gateway posts {"input": {...}} to /v1/data/aegis/authz/decision.
package aegis.authz

import rego.v1

default decision := {
	"allow": false,
	"reason_code": "action_not_permitted",
	"detail": "deny by default",
	"requires_approval": false,
}

mode := input.policy.allowed_actions[input.action]

action_known if mode

action_enabled if {
	action_known
	mode != "deny"
}

# --- data scope -------------------------------------------------------------
read_actions := {"read_field", "read_profile", "fetch_evidence", "list_transactions", "export_records"}

requested_fields contains f if {
	some f in input.fields
}

# A dotted path is a data field; an object identifier ("case #221") is not.
requested_fields contains input.resource if {
	input.action in read_actions
	input.resource != null
	regex.match(`^[A-Za-z0-9_]+(\.[A-Za-z0-9_*]+)+$`, input.resource)
}

scope_permits(scope, field) if lower(scope) == "*"

scope_permits(scope, field) if lower(scope) == lower(field)

scope_permits(scope, field) if {
	endswith(lower(scope), "*")
	startswith(lower(field), trim_suffix(lower(scope), "*"))
}

field_denied contains f if {
	some f in requested_fields
	not field_in_scope(f)
}

field_in_scope(f) if {
	some scope in input.policy.data_scopes
	scope_permits(scope, f)
}

# --- conditions -------------------------------------------------------------
condition_ceiling := to_number(trim_prefix(mode, "allow:<=")) if startswith(mode, "allow:<=")

condition_violated if {
	startswith(mode, "allow:<=")
	input.amount_cents > condition_ceiling
}

# --- decision ---------------------------------------------------------------
decision := {
	"allow": false,
	"reason_code": "action_not_permitted",
	"detail": sprintf("'%v' is not in the permission set", [input.action]),
	"requires_approval": false,
} if not action_enabled

decision := {
	"allow": false,
	"reason_code": "data_scope_denied",
	"detail": sprintf("%v outside data scope", [concat(", ", field_denied)]),
	"requires_approval": false,
} if {
	action_enabled
	count(field_denied) > 0
}

decision := {
	"allow": false,
	"reason_code": "condition_not_met",
	"detail": "amount exceeds permission condition",
	"requires_approval": false,
} if {
	action_enabled
	count(field_denied) == 0
	condition_violated
}

decision := {
	"allow": true,
	"reason_code": "within_policy",
	"detail": "",
	"requires_approval": mode == "approval",
} if {
	action_enabled
	count(field_denied) == 0
	not condition_violated
}

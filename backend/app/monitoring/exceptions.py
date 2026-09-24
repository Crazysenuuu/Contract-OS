"""Monitoring domain exceptions (spec 3.15).

Raised by connectors, credentials and the evaluation/service layer. Callers
that catch these distinguish *source failures* (→ INCONCLUSIVE) from *rule
failures* (→ FAIL) so a connector outage never mints a contract-risk alert
(3.15.25).
"""


class MonitoringError(Exception):
    """Base class for monitoring errors."""


class UnsupportedConnector(MonitoringError):
    """No connector factory is registered for the provider key."""


class UnsupportedEvaluator(MonitoringError):
    """The configured monitoring rule names an unknown evaluator."""


class UnknownField(MonitoringError):
    """The rule references a field the connector does not expose."""


class UnknownOperator(MonitoringError):
    """The rule uses an operator outside the allowed set."""


class CredentialUnavailable(MonitoringError):
    """A secret reference cannot be resolved and the system must fail closed."""


class ConnectorAuthError(MonitoringError):
    """Authentication with the external source failed."""


class ConnectorUnavailable(MonitoringError):
    """The external source was unreachable or returned an error."""


class SourceUnavailable(ConnectorUnavailable):
    """Alias used by service code to keep source/rule failures distinct."""


class WebhookVerificationError(MonitoringError):
    """Webhook signature/timestamp/duplicate checks failed."""


class MonitoringNotWritable(MonitoringError):
    """A monitoring definition could not be modified in its current state."""


class CrossWorkspaceError(MonitoringError):
    """A record referenced an object from another workspace."""
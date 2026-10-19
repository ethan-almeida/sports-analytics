from __future__ import annotations


class OrchestratorError(Exception):
    user_message = "request failed"
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(self.user_message)


class SafetyViolation(OrchestratorError):
    user_message = "the question could not be answered safely"

"""Stable errors shared by host workflows."""

class RunnerError(Exception):
    """Controlled runner error carrying the stable CLI exit category."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code

class CommandInterrupted(Exception):
    """A bounded child was interrupted; partial streams remain available."""

    def __init__(self, result: dict):
        super().__init__("command interrupted")
        self.result = result

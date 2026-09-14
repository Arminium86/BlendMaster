"""Actionable preparation failures that can redirect the desktop workflow."""


class PlanningPrerequisiteError(ValueError):
    title = 'Planning inputs need submission'

    def __init__(self, message, workflow_page):
        super().__init__(message)
        self.user_message = message
        self.workflow_page = workflow_page

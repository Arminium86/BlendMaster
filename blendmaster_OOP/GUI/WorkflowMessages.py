"""Keep unattended workflows out of modal dialogs; preserve normal UI messages."""
from PyQt5.QtWidgets import QMessageBox as QtMessageBox


def workflow(parent):
    controller = vars(parent).get('site_workflow_controller') if parent is not None else None
    return controller if controller and (controller.active or vars(parent).get('_unattended_workflow')) else None


class WorkflowMessageBox(QtMessageBox):
    @staticmethod
    def information(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.status(str(title) + ': ' + str(text))
            return QtMessageBox.Ok
        return QtMessageBox.information(parent, title, text, *args, **kwargs)

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.fail(str(title) + ': ' + str(text))
            return QtMessageBox.Ok
        return QtMessageBox.warning(parent, title, text, *args, **kwargs)

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.fail(str(title) + ': ' + str(text))
            return QtMessageBox.Ok
        return QtMessageBox.critical(parent, title, text, *args, **kwargs)

    @staticmethod
    def question(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.fail('Human decision required: ' + str(title) + ': ' + str(text))
            return QtMessageBox.Cancel
        return QtMessageBox.question(parent, title, text, *args, **kwargs)

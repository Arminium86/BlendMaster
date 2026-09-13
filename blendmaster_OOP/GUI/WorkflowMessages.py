"""Keep unattended workflows out of modal dialogs; preserve normal UI messages."""
from PyQt5.QtWidgets import QMessageBox as QtMessageBox


def present(parent, title, text, icon, original, *args, **kwargs):
    """Planner messages lead with the action; full diagnostics remain expandable."""
    full = str(text)
    if parent is None or vars(parent).get('access_role') != 'planner' or len(full) <= 240:
        return original(parent, title, text, *args, **kwargs)
    first = full.split('\n', 1)[0].strip()
    if len(first) > 220:
        first = first[:217].rsplit(' ', 1)[0] + '…'
    dialog = QtMessageBox(icon, str(title), first, parent=parent)
    dialog.setDetailedText(full)
    dialog.setStandardButtons(args[0] if args else kwargs.get('buttons', QtMessageBox.Ok))
    return dialog.exec_()


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
        return present(parent, title, text, QtMessageBox.Information, QtMessageBox.information, *args, **kwargs)

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.fail(str(title) + ': ' + str(text))
            return QtMessageBox.Ok
        return present(parent, title, text, QtMessageBox.Warning, QtMessageBox.warning, *args, **kwargs)

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.fail(str(title) + ': ' + str(text))
            return QtMessageBox.Ok
        return present(parent, title, text, QtMessageBox.Critical, QtMessageBox.critical, *args, **kwargs)

    @staticmethod
    def question(parent, title, text, *args, **kwargs):
        controller = workflow(parent)
        if controller:
            controller.fail('Human decision required: ' + str(title) + ': ' + str(text))
            return QtMessageBox.Cancel
        return QtMessageBox.question(parent, title, text, *args, **kwargs)

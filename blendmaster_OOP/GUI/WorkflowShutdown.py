"""Keep Qt workers and their site database alive until they have stopped."""
from PyQt5.QtCore import QTimer


def wait_for_workers(host, event):
    controller = vars(host).get('site_workflow_controller')
    busy = bool(vars(host).get('background_tasks') or (controller and controller.active))
    if not busy:
        host._closing_requested = False
        return False
    event.ignore()
    host._closing_requested = True
    if controller and controller.active:
        controller.cancel()
    label = vars(host).get('preparation_status_label')
    if label is not None:
        label.setText('Waiting for the current operation to stop before closing…')
    if vars(host).get('_close_wait_timer') is None:
        timer = host._close_wait_timer = QTimer(host)
        timer.setInterval(250)
        def ready():
            if not host.background_tasks and not (controller and controller.active):
                timer.stop()
                host._close_wait_timer = None
                timer.deleteLater()
                host.close()
        timer.timeout.connect(ready)
        timer.start()
    return True

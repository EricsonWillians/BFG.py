from PyQt5.QtWidgets import QAction


class OpenWadFinder(QAction):

    def __init__(self, widget, wadFinder):
        super().__init__('&Mod Browser', widget, checkable=True)
        self.setShortcut('Ctrl+B')
        self.setStatusTip('Show or hide the mod browser')
        self.wadFinder = wadFinder
        # Do NOT shadow QAction.setVisible (the old zero-arg override crashed
        # any standard setVisible(bool) call on this action). Drive the
        # browser widget from the action's checked state instead.
        self.triggered.connect(self._on_triggered)

    def _on_triggered(self, checked: bool):
        self.wadFinder.setVisible(bool(checked))

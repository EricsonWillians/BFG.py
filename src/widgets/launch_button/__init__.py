from PyQt5.QtWidgets import QPushButton


class LaunchButton(QPushButton):
    def __init__(
        self,
        portPathInput=None,
        iwadInput=None,
        pwadList=None,
        optionsInput=None,
        logWindow=None,
        loadingWindow=None,
        text="*** UNLEASH HELL ***",
    ):
        super().__init__(text)
        self.setObjectName("launchButton")  # For CSS styling
        self.portPathInput = portPathInput
        self.iwadInput = iwadInput
        self.pwadList = pwadList
        self.optionsInput = optionsInput
        self.logWindow = logWindow
        self.loadingWindow = loadingWindow

    def set_loading(self, loading: bool):
        self.setEnabled(not loading)

    def onClick(self):
        self.click()

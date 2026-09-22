from PyQt5.QtWidgets import QPushButton


class LaunchButton(QPushButton):
    def __init__(self, text="*** UNLEASH HELL ***"):
        super().__init__(text)
        self.setObjectName("launchButton")  # For CSS styling

    def set_loading(self, loading: bool):
        self.setEnabled(not loading)

    def onClick(self):
        self.click()
